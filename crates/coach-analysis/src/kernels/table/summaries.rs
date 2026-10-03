//! Whole-column summaries and per-sample series: `_caution_flag_series`, `_dt_series`,
//! `_channel_range`, `_conditions_summary`, `_driver_adjustments_summary`.

use std::collections::HashMap;
use std::rc::Rc;

use crate::kernels::error::KernelError;
use crate::kernels::value::{Dict, PyValue};

use super::units::{Op, apply_optional_steps, apply_steps, normalized_unit, unit_steps};
use super::views::{ColumnKey, Table, View, is_py_space};

/// `analysis_engine.CAUTION_FLAGS`.
pub const CAUTION_FLAGS: i64 = 0x0008 | 0x0100 | 0x0200 | 0x4000 | 0x8000;
/// `analysis_engine.ONE_LAP_TO_GREEN_FLAG`.
pub const ONE_LAP_TO_GREEN_FLAG: i64 = 0x0200;

/// `_round(value, digits)` of a float: `round(value, digits)` when finite, else `None`.
pub fn round_value(value: Option<f64>, digits: i64) -> Result<PyValue, KernelError> {
    match value {
        Some(number) if number.is_finite() => Ok(PyValue::Float(coach_numerics::round_ndigits(
            number, digits,
        )?)),
        _ => Ok(PyValue::None),
    }
}

/// `str(value)` of a scalar the oracle formats (`str`, `int`, `float`, `bool`).
pub fn py_str(value: &PyValue) -> Result<String, KernelError> {
    Ok(match value {
        PyValue::Str(text) => text.clone(),
        PyValue::Int(i) => i.to_string(),
        PyValue::BigInt(text) => text.clone(),
        PyValue::Float(x) => coach_numerics::float_repr(*x),
        PyValue::Bool(b) => if *b { "True" } else { "False" }.to_owned(),
        PyValue::None => "None".to_owned(),
        other => {
            return Err(KernelError::internal(format!(
                "str() of a {} is not ported",
                other.type_name()
            )));
        }
    })
}

/// `str(value or "").strip().lower()`.
pub fn lowered_text(value: Option<&PyValue>) -> Result<String, KernelError> {
    let text = match value {
        Some(value) if value.truthy()? => py_str(value)?,
        _ => String::new(),
    };
    Ok(text.trim_matches(is_py_space).to_lowercase())
}

/// `analysis_engine._numeric_text(value)`.
pub fn numeric_text(value: Option<&PyValue>) -> Result<Option<f64>, KernelError> {
    match value {
        Some(
            number @ (PyValue::Int(_) | PyValue::BigInt(_) | PyValue::Float(_) | PyValue::Bool(_)),
        ) => number.finite(),
        Some(PyValue::Str(text)) => {
            let token = text
                .split(is_py_space)
                .find(|piece| !piece.is_empty())
                .unwrap_or("");
            match coach_numerics::parse_float(token) {
                Ok(number) => Ok(Some(number)),
                Err(error) if error.kind == coach_numerics::PyErrorKind::ValueError => Ok(None),
                Err(error) => Err(error.into()),
            }
        }
        _ => Ok(None),
    }
}

/// `analysis_engine._path_get(value, *keys)`.
fn path_get<'v>(value: &'v PyValue, keys: &[&str]) -> Option<&'v PyValue> {
    let mut current = value;
    for key in keys {
        current = current.as_dict()?.get(key)?;
    }
    Some(current)
}

/// `_caution_flag_series(table)`: `SessionFlags` bits (`int(_finite(v) or 0)`), with the
/// one-lap-to-green bit cleared on the samples of a non-Race session that carry no other
/// caution bit; the shared flag view itself when nothing is cleared. A session-info value
/// the port cannot read as the oracle does records a table failure ([`Table::check`]) and
/// leaves the flags unchanged.
pub fn caution_flag_series(table: &Table) -> Rc<Vec<i64>> {
    let flags = table.flag_bits(&["SessionFlags"], PyValue::Int(0));
    match clear_one_lap_to_green(table, &flags) {
        Ok(Some(cleared)) => Rc::new(cleared),
        Ok(None) => flags,
        Err(error) => {
            table.fail(error);
            flags
        }
    }
}

/// The flags with the non-Race one-lap-to-green bits cleared, or `None` when none is.
fn clear_one_lap_to_green(table: &Table, flags: &[i64]) -> Result<Option<Vec<i64>>, KernelError> {
    let recorded_type = lowered_text(table.metadata("sim_session_type"))?;
    if recorded_type.is_empty() || recorded_type == "race" {
        return Ok(None);
    }
    let real_caution = CAUTION_FLAGS & !ONE_LAP_TO_GREEN_FLAG;
    let candidate = |flag: i64| flag & ONE_LAP_TO_GREEN_FLAG != 0 && flag & real_caution == 0;
    if !flags.iter().any(|&flag| candidate(flag)) {
        return Ok(None);
    }
    // Keyed by the integral float itself: int(a) == int(b) exactly when a == b, for an int of
    // any size (signed zeros made one by `+ 0.0`).
    let int_key = |number: f64| (number + 0.0).to_bits();
    let mut session_types: HashMap<u64, String> = HashMap::new();
    let sessions = path_get(&table.meta().session_info, &["SessionInfo", "Sessions"]);
    let items: &[PyValue] = match sessions {
        Some(PyValue::List(items) | PyValue::Tuple(items)) => items,
        _ => &[],
    };
    for item in items {
        let Some(dict) = item.as_dict() else { continue };
        let Some(number) = numeric_text(dict.get("SessionNum"))? else {
            continue;
        };
        if number.is_finite() && number.fract() == 0.0 {
            session_types.insert(int_key(number), lowered_text(dict.get("SessionType"))?);
        }
    }
    let session_numbers = table
        .has(&["SessionNum"])
        .then(|| table.normalized(&["SessionNum"], PyValue::None))
        .filter(|view| !view.is_empty());
    let mut result = flags.to_vec();
    for (index, &flag) in flags.iter().enumerate() {
        if !candidate(flag) {
            continue;
        }
        let mut session_type = recorded_type.as_str();
        if let Some(numbers) = &session_numbers
            && let Some(number) = numbers.get(index)
            && number.fract() == 0.0
        {
            session_type = session_types
                .get(&int_key(number))
                .map(String::as_str)
                .filter(|kind| !kind.is_empty())
                .unwrap_or(&recorded_type);
        }
        if session_type != "race" {
            result[index] = flag & !ONE_LAP_TO_GREEN_FLAG;
        }
    }
    Ok(Some(result))
}

/// `_dt_series(times, fallback_hz)` over the normalised times: the fallback interval
/// `1.0 / max(fallback_hz, 1.0)` first, then each positive step under 2 s, the fallback for any
/// other step or gap; empty for no samples.
pub fn dt_series(times: &View, fallback_hz: f64) -> Vec<f64> {
    let fallback = 1.0 / if 1.0 > fallback_hz { 1.0 } else { fallback_hz };
    if times.is_empty() {
        return Vec::new();
    }
    let mut result = Vec::with_capacity(times.len());
    result.push(fallback);
    for index in 1..times.len() {
        let step = match (times.get(index - 1), times.get(index)) {
            (Some(previous), Some(current)) => {
                let delta = current - previous;
                if 0.0 < delta && delta < 2.0 {
                    delta
                } else {
                    fallback
                }
            }
            _ => fallback,
        };
        result.push(step);
    }
    result
}

/// `min(values)` / `max(values)` of floats: the first item, replaced only by a strictly
/// smaller (larger) one, as the builtins compare.
fn first_extreme(values: &[f64], better: fn(f64, f64) -> bool) -> f64 {
    let mut best = values[0];
    for &value in &values[1..] {
        if better(value, best) {
            best = value;
        }
    }
    best
}

/// A `_channel_range` column: the resolved channel to its converted view.
type Converter<'a> = Option<&'a dyn Fn(&str) -> View>;

/// `_channel_range(table, aliases, column=..., digits=...)`: `None` when no alias is present or
/// no converted sample is; `column` maps the resolved channel to its converted view.
pub fn channel_range(
    table: &Table,
    aliases: &[&str],
    column: Converter<'_>,
    digits: i64,
) -> Result<Option<Dict>, KernelError> {
    let (channel, _raw) = table.resolve(aliases, PyValue::None);
    let Some(channel) = channel else {
        return Ok(None);
    };
    let values = match column {
        Some(convert) => convert(channel).present_values(),
        None => table
            .finite_for(&ColumnKey::Channel(channel.to_owned()))
            .present_values(),
    };
    if values.is_empty() {
        return Ok(None);
    }
    let minimum = first_extreme(&values, |a, b| a < b);
    let maximum = first_extreme(&values, |a, b| a > b);
    // _median(values): statistics.median of the finite ones.
    let finite: Vec<f64> = values.iter().copied().filter(|v| v.is_finite()).collect();
    let median = if finite.is_empty() {
        None
    } else {
        Some(coach_numerics::median_floats(&finite)?)
    };
    // 10 ** (-digits): an int for digits <= 0, else float pow (C library).
    let threshold = if digits <= 0 {
        10f64
            .powi(i32::try_from(-digits).map_err(|_| KernelError::internal("digits out of range"))?)
    } else {
        coach_numerics::float_pow(10.0, -(digits as f64))?
    };
    let mut summary = Dict::new();
    summary.insert("channel", channel);
    summary.insert("source_unit", table.unit(Some(channel)).map(str::to_owned));
    summary.insert("start", round_value(Some(values[0]), digits)?);
    summary.insert("end", round_value(values.last().copied(), digits)?);
    summary.insert("minimum", round_value(Some(minimum), digits)?);
    summary.insert("median", round_value(median, digits)?);
    summary.insert("maximum", round_value(Some(maximum), digits)?);
    summary.insert("changed", (maximum - minimum) > threshold);
    Ok(Some(summary))
}

/// `_conditions_summary(table)`.
pub fn conditions_summary(table: &Table) -> Result<Dict, KernelError> {
    let scaled = |steps: Option<&'static [(Op, f64)]>| {
        move |channel: &str| {
            apply_optional_steps(
                &table.finite_for(&ColumnKey::Channel(channel.to_owned())),
                steps,
            )
        }
    };
    let percent = |channel: &str| {
        apply_steps(
            &table.fractions_for(&ColumnKey::Channel(channel.to_owned())),
            &[(Op::Mul, 100.0)],
        )
    };
    let temperature = scaled(unit_steps("temperature", &normalized_unit(Some("C"))));
    let wind = scaled(Some(&[(Op::Mul, 2.236936)]));
    let fields: [(&str, &[&str], Converter<'_>, i64); 8] = [
        (
            "track_temperature_f",
            &["TrackTempCrew", "TrackTemp"],
            Some(&temperature),
            1,
        ),
        ("air_temperature_f", &["AirTemp"], Some(&temperature), 1),
        ("track_wetness_state", &["TrackWetness"], None, 0),
        (
            "relative_humidity_percent",
            &["RelativeHumidity"],
            Some(&percent),
            1,
        ),
        ("wind_speed_mph", &["WindVel"], Some(&wind), 1),
        ("air_density_kg_m3", &["AirDensity"], None, 4),
        ("air_pressure_pa", &["AirPressure"], None, 0),
        (
            "precipitation_percent",
            &["Precipitation"],
            Some(&percent),
            2,
        ),
    ];
    let mut result = Dict::new();
    for (name, aliases, column, digits) in fields {
        if let Some(mut summary) = channel_range(table, aliases, column, digits)? {
            if name == "track_wetness_state" {
                summary.insert("semantics", "iRacing TrackWetness categorical state");
            }
            result.insert(name, summary);
        }
    }
    if table.has(&["WeatherDeclaredWet"]) {
        let declared = table.booleans(&["WeatherDeclaredWet"], PyValue::Bool(false));
        result.insert("weather_declared_wet", declared.iter().any(|&wet| wet));
    }
    if table.has(&["PlayerTireCompound"]) {
        let compounds = table.normalized(&["PlayerTireCompound"], PyValue::None);
        if let Some(last) = compounds.iter().rev().flatten().next() {
            // int(last): a Python int of any size.
            let compound = match coach_numerics::int_from_float(last) {
                Ok(value) => PyValue::Int(value),
                Err(error) if error.kind == coach_numerics::PyErrorKind::IntBeyondI64 => {
                    PyValue::BigInt(format!("{:.0}", last.trunc()))
                }
                Err(error) => return Err(error.into()),
            };
            result.insert("player_tire_compound", compound);
        }
    }
    table.check()?;
    Ok(result)
}

/// `_driver_adjustments_summary(table)`.
pub fn driver_adjustments_summary(table: &Table) -> Result<Dict, KernelError> {
    let mut result = Dict::new();
    for channel in [
        "dcBrakeBias",
        "dpQTape",
        "dpWeightJackerLeft",
        "dpWeightJackerRight",
        "dpFuelAddKg",
    ] {
        if let Some(mut summary) = channel_range(table, &[channel], None, 4)? {
            summary.insert(
                "semantics",
                if channel.starts_with("dp") {
                    "requested pit adjustment; not proof that service was completed"
                } else {
                    "in-car driver control setting"
                },
            );
            result.insert(channel, summary);
        }
    }
    table.check()?;
    Ok(result)
}

//! `analysis_engine._lap_conditions(table, indices)` with `_session_track_usage(table)`.

use std::rc::Rc;

use coach_numerics as num;

use super::super::error::KernelError;
use super::super::table::{Table, View};
use super::super::value::{Dict, PyValue};
use super::{median, path_get, py_str, py_strip, round};

const SKY_LABELS: [&str; 4] = ["Clear", "Partly cloudy", "Mostly cloudy", "Overcast"];

/// The columns `_lap_conditions` reads, resolved once (each accessor records what the per-lap
/// Python call records), and its per-table values.
pub(super) struct LapConditions {
    skies: Rc<View>,
    track_temp: Rc<View>,
    air_temp: Rc<View>,
    wind_speed: Rc<View>,
    wind_direction: Rc<View>,
    humidity: Rc<View>,
    fog: Rc<View>,
    precipitation: Rc<View>,
    air_pressure: Rc<View>,
    air_density: Rc<View>,
    usage: Rc<View>,
    wetness: Rc<View>,
    declared_wet: Option<Rc<Vec<bool>>>,
    /// `str(_deep_find(table.session_info, "TrackSkies") or "").strip()` when it names a sky;
    /// the oracle reads it only for a lap without a sky sample, so an error waits for one.
    recorded_sky: Result<Option<String>, KernelError>,
    track_usage: PyValue,
}

impl LapConditions {
    pub(super) fn new(table: &Table) -> Result<LapConditions, KernelError> {
        let view = |aliases: &[&str]| table.normalized(aliases, PyValue::None);
        let skies = view(&["Skies"]);
        let track_temp = view(&["TrackTempCrew", "TrackTemp"]);
        let air_temp = view(&["AirTemp"]);
        let wind_speed = view(&["WindVel"]);
        let wind_direction = view(&["WindDir"]);
        let humidity = view(&["RelativeHumidity"]);
        let fog = view(&["FogLevel"]);
        let precipitation = view(&["Precipitation"]);
        let air_pressure = view(&["AirPressure"]);
        let air_density = view(&["AirDensity"]);
        let usage = view(&["TrackUsage"]);
        let wetness = view(&["TrackWetness"]);
        let declared = table.booleans(&["WeatherDeclaredWet"], PyValue::None);
        let declared_wet = table.has(&["WeatherDeclaredWet"]).then_some(declared);
        let session_info = &table.meta().session_info;
        let recorded_sky = recorded_sky(session_info);
        let track_usage = session_track_usage(session_info)?;
        Ok(LapConditions {
            skies,
            track_temp,
            air_temp,
            wind_speed,
            wind_direction,
            humidity,
            fog,
            precipitation,
            air_pressure,
            air_density,
            usage,
            wetness,
            declared_wet,
            recorded_sky,
            track_usage,
        })
    }

    /// `_lap_conditions(table, indices)` for one lap.
    pub(super) fn summary(&self, indices: &[usize]) -> Result<Dict, KernelError> {
        let median_of = |view: &View| -> Result<Option<f64>, KernelError> {
            let values: Vec<f64> = indices.iter().filter_map(|&i| view.get(i)).collect();
            median(&values)
        };
        let sky_value = median_of(&self.skies)?;
        // sky_labels.get(int(round(sky_value))): round() is half to even and integral, so
        // 0.0 to 3.0 (and -0.0) are the four keys.
        let mut sky_label: PyValue = match sky_value.map(num::round_half_even) {
            Some(x) if (0.0..=3.0).contains(&x) => SKY_LABELS[x as usize].into(),
            _ => PyValue::None,
        };
        if sky_label.is_none()
            && let Some(text) = self.recorded_sky.clone()?
        {
            sky_label = PyValue::Str(text);
        }
        let track_temp_c = median_of(&self.track_temp)?;
        let air_temp_c = median_of(&self.air_temp)?;
        let wind_speed_mps = median_of(&self.wind_speed)?;
        let wind_direction_rad = median_of(&self.wind_direction)?;
        let humidity = median_of(&self.humidity)?.map(fraction);
        let fog = median_of(&self.fog)?.map(fraction);
        let precipitation = median_of(&self.precipitation)?.map(fraction);
        let air_pressure_pa = median_of(&self.air_pressure)?;
        let air_density_kg_m3 = median_of(&self.air_density)?;
        let usage = median_of(&self.usage)?;
        let usage_percent = usage.map(|value| fraction(value) * 100.0);
        let declared_wet: PyValue = match &self.declared_wet {
            Some(values) => indices.iter().any(|&i| values[i]).into(),
            None => PyValue::None,
        };
        let fahrenheit = |celsius: Option<f64>| celsius.map(|c| c * 9.0 / 5.0 + 32.0);
        let wind_direction = match wind_direction_rad {
            Some(radians) => Some(num::float_mod(num::degrees(radians), 360.0)?),
            None => None,
        };
        Ok(Dict::new()
            .with("sky", sky_label)
            .with("track_temperature_f", round(fahrenheit(track_temp_c), 1)?)
            .with("air_temperature_f", round(fahrenheit(air_temp_c), 1)?)
            .with(
                "wind_speed_mph",
                round(wind_speed_mps.map(|v| v * 2.236936), 1)?,
            )
            .with("wind_direction_degrees", round(wind_direction, 1)?)
            .with(
                "relative_humidity_percent",
                round(humidity.map(|v| v * 100.0), 1)?,
            )
            .with("fog_percent", round(fog.map(|v| v * 100.0), 1)?)
            .with(
                "air_pressure_inhg",
                round(air_pressure_pa.map(|v| v * 0.00029529983071445), 2)?,
            )
            .with(
                "air_density_lb_ft3",
                round(air_density_kg_m3.map(|v| v * 0.0624279606), 3)?,
            )
            .with(
                "precipitation_percent",
                round(precipitation.map(|v| v * 100.0), 1)?,
            )
            .with("track_wetness_state", round(median_of(&self.wetness)?, 0)?)
            .with("track_usage_percent", round(usage_percent, 1)?)
            .with("track_usage", self.track_usage.clone())
            .with("weather_declared_wet", declared_wet))
    }
}

/// The session's recorded sky when it is one (`_lap_conditions`' fallback label).
fn recorded_sky(session_info: &PyValue) -> Result<Option<String>, KernelError> {
    match deep_find(session_info, "TrackSkies") {
        Some(found) if found.truthy()? => {
            let text = py_strip(&py_str(found)?).to_owned();
            Ok((!text.is_empty() && text.to_lowercase() != "dynamic").then_some(text))
        }
        _ => Ok(None),
    }
}

/// `_fraction(number)` of a finite number: `max(0.0, min(1.0, n))` after scaling a percentage.
fn fraction(number: f64) -> f64 {
    let scaled = if number.abs() > 1.5 {
        number / 100.0
    } else {
        number
    };
    let capped = if scaled < 1.0 { scaled } else { 1.0 };
    if capped > 0.0 { capped } else { 0.0 }
}

/// `_deep_find(value, key)`: a mapping's own `key` first (even when its value is `None`), then
/// the first child holding a non-`None` match; lists and tuples by element.
fn deep_find<'a>(value: &'a PyValue, key: &str) -> Option<&'a PyValue> {
    let children: Box<dyn Iterator<Item = &'a PyValue>> = match value {
        PyValue::Dict(dict) => {
            if let Some(found) = dict.get(key) {
                return Some(found);
            }
            Box::new(dict.values())
        }
        PyValue::List(items) | PyValue::Tuple(items) => Box::new(items.iter()),
        _ => return None,
    };
    for child in children {
        if let Some(found) = deep_find(child, key)
            && !found.is_none()
        {
            return Some(found);
        }
    }
    None
}

/// `_session_track_usage(table)`: the last non-"carry over" rubber state at or before the last
/// Race session (or the last session).
fn session_track_usage(session_info: &PyValue) -> Result<PyValue, KernelError> {
    let sessions = match path_get(session_info, &["SessionInfo", "Sessions"]) {
        Some(value) if value.truthy()? => value,
        _ => return Ok(PyValue::None),
    };
    // isinstance(sessions, Sequence): a list or tuple (a str holds no mapping either way).
    let items = match sessions {
        PyValue::List(items) | PyValue::Tuple(items) => items,
        _ => return Ok(PyValue::None),
    };
    let mut race_index = items.len().checked_sub(1);
    for (index, session) in items.iter().enumerate().rev() {
        if let PyValue::Dict(dict) = session {
            let kind = match dict.get("SessionType") {
                Some(value) => py_str(value)?,
                None => String::new(),
            };
            if kind.to_lowercase() == "race" {
                race_index = Some(index);
                break;
            }
        }
    }
    let Some(race_index) = race_index else {
        return Ok(PyValue::None);
    };
    for session in items[..=race_index].iter().rev() {
        let PyValue::Dict(dict) = session else {
            continue;
        };
        let state = match dict.get("SessionTrackRubberState") {
            Some(value) if value.truthy()? => py_strip(&py_str(value)?).to_owned(),
            _ => String::new(),
        };
        if !state.is_empty() && state.to_lowercase() != "carry over" {
            return Ok(PyValue::Str(state));
        }
    }
    Ok(PyValue::None)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fraction_clamps_like_max_min() {
        assert!(fraction(-0.0).is_sign_positive());
        assert_eq!(fraction(55.0), 0.55);
        assert_eq!(fraction(1.2), 1.0);
        assert_eq!(fraction(-3.0), 0.0);
    }

    #[test]
    fn deep_find_prefers_the_mapping_own_key() {
        let inner = PyValue::Dict(Dict::new().with("TrackSkies", "Overcast"));
        let outer = PyValue::Dict(
            Dict::new()
                .with("a", PyValue::List(vec![PyValue::None, inner]))
                .with("TrackSkies", PyValue::None),
        );
        assert!(deep_find(&outer, "TrackSkies").is_some_and(PyValue::is_none));
        let nested = PyValue::Dict(Dict::new().with("x", outer));
        assert!(deep_find(&nested, "TrackSkies").is_none());
    }
}

//! Setup channel series and their summary: `_setup_channel_series` (with
//! `_resolved_setup_series`, `_derived_mean_series`) and `_setup_telemetry` (with
//! `_setup_sample_indices`, `_series_values`, `_series_metadata`, `_range_summary`,
//! `_shock_summary`).

use std::collections::BTreeSet;
use std::rc::Rc;

use crate::kernels::error::KernelError;
use crate::kernels::value::{Dict, PyValue};

use super::summaries::{CAUTION_FLAGS, caution_flag_series, round_value};
use super::units::{apply_optional_steps, normalize_view, normalized_unit, row_means, unit_steps};
use super::views::{ColumnKey, Table, View};

/// `analysis_engine.TIRES`.
pub const TIRES: [&str; 4] = ["LF", "RF", "LR", "RR"];

const DERIVED_MEAN: &str = "per-sample arithmetic mean of available source channels";
const SAMPLE_SCOPE: &str = "non-pit, non-caution samples with valid lap distance when available";
const LIMITS: [&str; 4] = [
    "Dynamic platform, shock, pressure, and temperature traces are observational correlations; telemetry cannot uniquely identify which setup parameter caused a change.",
    "Attribute setup effects only after controlled A/B runs with one change at a time and matched fuel, tires, weather, track state, line, and driver inputs.",
    "Observed ride-height minima are not universal setup targets; legal and effective clearances depend on the car, track, ruleset, and complete setup.",
    "Pressure and temperature traces do not establish tire wear; wear remains a discrete pit-service observation under the tire-wear rule.",
];

/// `PLATFORM_CHANNEL_ALIASES`, in its order.
pub fn platform_channel_aliases() -> Vec<(String, Vec<String>)> {
    let mut result = vec![(
        "center_front_splitter".to_owned(),
        [
            "CFSRrideHeight",
            "CFSRRideHeight",
            "CenterFrontSplitterRideHeight",
            "CenterFrontRideHeight",
            "SplitterRideHeight",
        ]
        .map(str::to_owned)
        .to_vec(),
    )];
    for tire in TIRES {
        result.push((
            tire.to_owned(),
            vec![format!("{tire}rideHeight"), format!("{tire}RideHeight")],
        ));
    }
    result
}

/// `_shock_channel_aliases(tire, measurement)`.
pub fn shock_channel_aliases(tire: &str, measurement: &str) -> Vec<String> {
    let (suffixes, dampers) = if measurement == "deflection" {
        (
            [
                "shockDefl",
                "ShockDefl",
                "shockDeflection",
                "ShockDeflection",
            ],
            [
                "damperDefl",
                "DamperDefl",
                "damperDeflection",
                "DamperDeflection",
            ],
        )
    } else {
        (
            ["shockVel", "ShockVel", "shockVelocity", "ShockVelocity"],
            ["damperVel", "DamperVel", "damperVelocity", "DamperVelocity"],
        )
    };
    let mut result: Vec<String> = Vec::new();
    let names = suffixes
        .iter()
        .map(|s| format!("{tire}SH{s}"))
        .chain(suffixes.iter().map(|s| format!("{tire}{s}")))
        .chain(dampers.iter().map(|s| format!("{tire}{s}")));
    for name in names {
        if !result.contains(&name) {
            result.push(name);
        }
    }
    result
}

/// `_tire_pressure_aliases(tire, cold=cold)`.
pub fn tire_pressure_aliases(tire: &str, cold: bool) -> Vec<String> {
    let suffixes: &[&str] = if cold {
        &[
            "coldPressure",
            "ColdPressure",
            "tireColdPressure",
            "TireColdPressure",
        ]
    } else {
        &[
            "pressure",
            "Pressure",
            "tirePressure",
            "TirePressure",
            "hotPressure",
            "HotPressure",
        ]
    };
    suffixes.iter().map(|s| format!("{tire}{s}")).collect()
}

/// `_tire_temperature_aliases(tire, position)`.
pub fn tire_temperature_aliases(tire: &str, position: &str) -> Vec<String> {
    ["temp", "Temp", "tireTemp", "TireTemp"]
        .iter()
        .map(|s| format!("{tire}{s}{position}"))
        .collect()
}

fn refs(names: &[String]) -> Vec<&str> {
    names.iter().map(String::as_str).collect()
}

/// One series of the setup structure: a resolved channel converted to the summary unit
/// (`{"channel", "source_unit", "values"}`) or a per-sample mean of such series (`{"channels",
/// "source_unit", "values", "derived"}`).
#[derive(Clone, Debug)]
pub struct SeriesItem {
    pub origin: Origin,
    pub source_unit: Option<String>,
    pub values: Rc<View>,
}

#[derive(Clone, Debug)]
pub enum Origin {
    Channel(String),
    Derived(Vec<String>, String),
}

impl SeriesItem {
    pub fn to_value(&self) -> PyValue {
        let mut dict = Dict::new();
        match &self.origin {
            Origin::Channel(channel) => {
                dict.insert("channel", channel.as_str());
                dict.insert("source_unit", self.source_unit.clone());
                dict.insert("values", self.values.to_value());
            }
            Origin::Derived(channels, derived) => {
                dict.insert(
                    "channels",
                    PyValue::List(channels.iter().map(|c| PyValue::from(c.as_str())).collect()),
                );
                dict.insert("source_unit", self.source_unit.clone());
                dict.insert("values", self.values.to_value());
                dict.insert("derived", derived.as_str());
            }
        }
        PyValue::Dict(dict)
    }

    /// Reads a series dict as the oracle built it; any other shape is `Internal`.
    pub fn from_value(value: &PyValue) -> Result<SeriesItem, KernelError> {
        let bad = |what: &str| KernelError::internal(format!("setup series item: {what}"));
        let dict = value.as_dict().ok_or_else(|| bad("not a dict"))?;
        let text = |key: &str| -> Result<Option<String>, KernelError> {
            match dict.get(key) {
                None | Some(PyValue::None) => Ok(None),
                Some(PyValue::Str(s)) => Ok(Some(s.clone())),
                Some(_) => Err(bad(&format!("{key} is not a str"))),
            }
        };
        let values = match dict.get("values") {
            Some(PyValue::List(items)) => {
                let mut options = Vec::with_capacity(items.len());
                for item in items {
                    options.push(match item {
                        PyValue::Float(x) => Some(*x),
                        PyValue::None => None,
                        other => return Err(bad(&format!("a {} sample", other.type_name()))),
                    });
                }
                View::from_options(options)
            }
            _ => return Err(bad("values is not a list")),
        };
        let origin = match (dict.get("channel"), dict.get("channels")) {
            (Some(PyValue::Str(channel)), None) => Origin::Channel(channel.clone()),
            (None, Some(PyValue::List(channels))) => Origin::Derived(
                channels
                    .iter()
                    .map(|c| c.as_str().map(str::to_owned))
                    .collect::<Option<Vec<String>>>()
                    .ok_or_else(|| bad("channels are not str"))?,
                text("derived")?.ok_or_else(|| bad("derived missing"))?,
            ),
            _ => return Err(bad("neither one channel nor channels")),
        };
        let expected = match origin {
            Origin::Channel(_) => 3,
            Origin::Derived(..) => 4,
        };
        if dict.len() != expected {
            return Err(bad("unexpected keys"));
        }
        Ok(SeriesItem {
            origin,
            source_unit: text("source_unit")?,
            values: Rc::new(values),
        })
    }

    /// `_series_metadata(series)`: the truthy ones of channel, channels, source_unit, derived.
    fn metadata(&self, dict: &mut Dict) {
        match &self.origin {
            Origin::Channel(channel) if !channel.is_empty() => {
                dict.insert("channel", channel.as_str());
            }
            Origin::Derived(channels, _) if !channels.is_empty() => {
                dict.insert(
                    "channels",
                    PyValue::List(channels.iter().map(|c| PyValue::from(c.as_str())).collect()),
                );
            }
            _ => {}
        }
        if let Some(unit) = self.source_unit.as_deref().filter(|u| !u.is_empty()) {
            dict.insert("source_unit", unit);
        }
        if let Origin::Derived(_, derived) = &self.origin
            && !derived.is_empty()
        {
            dict.insert("derived", derived.as_str());
        }
    }

    /// The channels `_setup_telemetry`'s `register` adds.
    fn register(&self, available: &mut BTreeSet<String>) {
        match &self.origin {
            Origin::Channel(channel) if !channel.is_empty() => {
                available.insert(channel.clone());
            }
            Origin::Channel(_) => {}
            Origin::Derived(channels, _) => available.extend(channels.iter().cloned()),
        }
    }
}

/// A tire's series (`tires[tire]`), keys in the oracle's order.
#[derive(Clone, Debug, Default)]
pub struct TireSeries {
    pub live_pressure: Option<SeriesItem>,
    pub cold_pressure: Option<SeriesItem>,
    pub temperatures: Vec<(String, SeriesItem)>,
    pub carcass_average: Option<SeriesItem>,
}

/// `_setup_channel_series(table)`: `{"platform": ..., "shocks": ..., "tires": ...}` with the
/// converted columns. In production the kernel process keeps it behind a handle for
/// `_setup_telemetry` and `_track_profile`.
#[derive(Clone, Debug, Default)]
pub struct SetupSeries {
    pub platform: Vec<(String, SeriesItem)>,
    /// Tire -> `[("deflection", ...), ("velocity", ...)]` (present ones, in that order).
    pub shocks: Vec<(String, Vec<(String, SeriesItem)>)>,
    pub tires: Vec<(String, TireSeries)>,
}

fn named_dict(items: &[(String, SeriesItem)]) -> PyValue {
    PyValue::Dict(
        items
            .iter()
            .map(|(name, item)| (PyValue::from(name.as_str()), item.to_value()))
            .collect(),
    )
}

fn named_items(value: &PyValue, what: &str) -> Result<Vec<(String, SeriesItem)>, KernelError> {
    let dict = value
        .as_dict()
        .ok_or_else(|| KernelError::internal(format!("setup series: {what} is not a dict")))?;
    let mut items = Vec::with_capacity(dict.len());
    for (key, item) in dict.iter() {
        let name = key
            .as_str()
            .ok_or_else(|| KernelError::internal(format!("setup series: {what} key not a str")))?;
        items.push((name.to_owned(), SeriesItem::from_value(item)?));
    }
    Ok(items)
}

impl SetupSeries {
    pub fn get_platform(&self, name: &str) -> Option<&SeriesItem> {
        self.platform
            .iter()
            .find(|(n, _)| n == name)
            .map(|(_, item)| item)
    }

    pub fn to_value(&self) -> PyValue {
        let shocks: Dict = self
            .shocks
            .iter()
            .map(|(tire, items)| (PyValue::from(tire.as_str()), named_dict(items)))
            .collect();
        let tires: Dict = self
            .tires
            .iter()
            .map(|(tire, series)| {
                let mut dict = Dict::new();
                if let Some(item) = &series.live_pressure {
                    dict.insert("live_pressure", item.to_value());
                }
                if let Some(item) = &series.cold_pressure {
                    dict.insert("cold_pressure", item.to_value());
                }
                if !series.temperatures.is_empty() {
                    dict.insert("temperatures", named_dict(&series.temperatures));
                    dict.insert(
                        "carcass_average",
                        series
                            .carcass_average
                            .as_ref()
                            .map_or(PyValue::None, SeriesItem::to_value),
                    );
                }
                (PyValue::from(tire.as_str()), PyValue::Dict(dict))
            })
            .collect();
        PyValue::Dict(
            Dict::new()
                .with("platform", named_dict(&self.platform))
                .with("shocks", shocks)
                .with("tires", tires),
        )
    }

    /// Reads the dict `_setup_channel_series` returned (the vectors pass it to
    /// `_setup_telemetry` by value); any other shape is `Internal`.
    pub fn from_value(value: &PyValue) -> Result<SetupSeries, KernelError> {
        let bad = |what: &str| KernelError::internal(format!("setup series: {what}"));
        let dict = value.as_dict().ok_or_else(|| bad("not a dict"))?;
        if dict.len() != 3 {
            return Err(bad("expected platform, shocks and tires"));
        }
        let part = |key: &str| dict.get(key).ok_or_else(|| bad(&format!("{key} missing")));
        let platform = named_items(part("platform")?, "platform")?;
        let mut shocks = Vec::new();
        for (tire, shock) in part("shocks")?
            .as_dict()
            .ok_or_else(|| bad("shocks"))?
            .iter()
        {
            let tire = tire.as_str().ok_or_else(|| bad("shock key"))?;
            shocks.push((tire.to_owned(), named_items(shock, "shock")?));
        }
        let mut tires = Vec::new();
        for (tire, series) in part("tires")?.as_dict().ok_or_else(|| bad("tires"))?.iter() {
            let tire = tire.as_str().ok_or_else(|| bad("tire key"))?;
            let series = series.as_dict().ok_or_else(|| bad("tire series"))?;
            let mut parsed = TireSeries::default();
            for (key, item) in series.iter() {
                match key.as_str() {
                    Some("live_pressure") => {
                        parsed.live_pressure = Some(SeriesItem::from_value(item)?)
                    }
                    Some("cold_pressure") => {
                        parsed.cold_pressure = Some(SeriesItem::from_value(item)?)
                    }
                    Some("temperatures") => {
                        parsed.temperatures = named_items(item, "temperatures")?
                    }
                    Some("carcass_average") => {
                        parsed.carcass_average = match item {
                            PyValue::None => None,
                            item => Some(SeriesItem::from_value(item)?),
                        }
                    }
                    _ => return Err(bad("unknown tire series key")),
                }
            }
            tires.push((tire.to_owned(), parsed));
        }
        Ok(SetupSeries {
            platform,
            shocks,
            tires,
        })
    }
}

/// `_resolved_setup_series(table, aliases, quantity)`: records the resolved alias.
fn resolved_setup_series(table: &Table, aliases: &[String], quantity: &str) -> Option<SeriesItem> {
    let (channel, _raw) = table.resolve(&refs(aliases), PyValue::None);
    let channel = channel?.to_owned();
    let default = match quantity {
        "distance" => Some("m"),
        "velocity" => Some("m/s"),
        "pressure" => Some("kPa"),
        "temperature" => Some("C"),
        _ => None,
    };
    let source_unit = table
        .unit(Some(&channel))
        .filter(|unit| !unit.is_empty())
        .or(default)
        .map(str::to_owned);
    let values = table.finite_for(&ColumnKey::Channel(channel.clone()));
    let steps = unit_steps(quantity, &normalized_unit(source_unit.as_deref()));
    let values = match steps {
        None => values,
        Some(_) => Rc::new(apply_optional_steps(&values, steps)),
    };
    Some(SeriesItem {
        origin: Origin::Channel(channel),
        source_unit,
        values,
    })
}

/// `_derived_mean_series(series, unit)`: `None` without a source.
fn derived_mean_series(
    series: &[&SeriesItem],
    unit: &str,
) -> Result<Option<SeriesItem>, KernelError> {
    if series.is_empty() {
        return Ok(None);
    }
    let normalized: Vec<View> = series
        .iter()
        .map(|item| normalize_view(&item.values))
        .collect();
    let columns: Vec<&View> = normalized.iter().collect();
    let values = row_means(&columns)?;
    let channels = series
        .iter()
        .filter_map(|item| match &item.origin {
            Origin::Channel(channel) if !channel.is_empty() => Some(channel.clone()),
            _ => None,
        })
        .collect();
    Ok(Some(SeriesItem {
        origin: Origin::Derived(channels, DERIVED_MEAN.to_owned()),
        source_unit: Some(unit.to_owned()),
        values: Rc::new(values),
    }))
}

/// `_setup_channel_series(table)`.
pub fn setup_channel_series(table: &Table) -> Result<SetupSeries, KernelError> {
    let mut series = SetupSeries::default();
    for (name, aliases) in platform_channel_aliases() {
        if let Some(item) = resolved_setup_series(table, &aliases, "distance") {
            series.platform.push((name, item));
        }
    }
    let rear: Vec<&SeriesItem> = ["LR", "RR"]
        .iter()
        .filter_map(|name| series.get_platform(name))
        .collect();
    if let Some(dynamic_rear) = derived_mean_series(&rear, "in")? {
        series
            .platform
            .push(("dynamic_rear".to_owned(), dynamic_rear));
    }
    for tire in TIRES {
        let mut shock = Vec::new();
        let deflection = resolved_setup_series(
            table,
            &shock_channel_aliases(tire, "deflection"),
            "distance",
        );
        let velocity =
            resolved_setup_series(table, &shock_channel_aliases(tire, "velocity"), "velocity");
        if let Some(item) = deflection {
            shock.push(("deflection".to_owned(), item));
        }
        if let Some(item) = velocity {
            shock.push(("velocity".to_owned(), item));
        }
        if !shock.is_empty() {
            series.shocks.push((tire.to_owned(), shock));
        }

        let mut tire_series = TireSeries {
            live_pressure: resolved_setup_series(
                table,
                &tire_pressure_aliases(tire, false),
                "pressure",
            ),
            cold_pressure: resolved_setup_series(
                table,
                &tire_pressure_aliases(tire, true),
                "pressure",
            ),
            ..TireSeries::default()
        };
        for position in ["CL", "CM", "CR"] {
            if let Some(item) = resolved_setup_series(
                table,
                &tire_temperature_aliases(tire, position),
                "temperature",
            ) {
                tire_series.temperatures.push((position.to_owned(), item));
            }
        }
        if !tire_series.temperatures.is_empty() {
            let sources: Vec<&SeriesItem> = tire_series
                .temperatures
                .iter()
                .map(|(_, item)| item)
                .collect();
            tire_series.carcass_average = derived_mean_series(&sources, "F")?;
        }
        if tire_series.live_pressure.is_some()
            || tire_series.cold_pressure.is_some()
            || !tire_series.temperatures.is_empty()
        {
            series.tires.push((tire.to_owned(), tire_series));
        }
    }
    table.check()?;
    Ok(series)
}

/// `_setup_sample_indices(table)`: samples off pit road, without a caution bit, and with a
/// valid lap distance when the table records one.
fn setup_sample_indices(table: &Table) -> Result<Vec<usize>, KernelError> {
    let pit = table.booleans(
        &["OnPitRoad", "PlayerCarInPitStall", "PitstopActive"],
        PyValue::Bool(false),
    );
    let flags = caution_flag_series(table);
    let has_distance = table.has(&["LapDistPct"]);
    let distance = table.normalized(&["LapDistPct"], PyValue::None);
    let length = pit.len().min(flags.len()).min(distance.len());
    Ok((0..length)
        .filter(|&index| {
            !pit[index]
                && flags[index] & CAUTION_FLAGS == 0
                && !(has_distance && distance.get(index).is_none_or(|position| position < 0.0))
        })
        .collect())
}

/// `_series_values(series, indices)`: the finite values at `indices` within the series.
fn series_values(item: &SeriesItem, indices: &[usize]) -> Vec<f64> {
    indices
        .iter()
        .filter(|&&index| index < item.values.len())
        .filter_map(|&index| item.values.get(index))
        .filter(|value| value.is_finite())
        .collect()
}

/// `_percentile_of_sorted(numbers, percentile)` of a non-empty sorted list.
fn percentile_of_sorted(numbers: &[f64], percentile: f64) -> Result<f64, KernelError> {
    if numbers.len() == 1 {
        return Ok(numbers[0]);
    }
    // max(0.0, min(1.0, percentile)), as the builtins compare.
    let clamped = if percentile < 1.0 { percentile } else { 1.0 };
    let clamped = if clamped > 0.0 { clamped } else { 0.0 };
    let position = clamped * (numbers.len() - 1) as f64;
    let lower = coach_numerics::floor(position)?;
    let upper = coach_numerics::ceil(position)?;
    let at = |index: i64| {
        usize::try_from(index)
            .ok()
            .and_then(|i| numbers.get(i).copied())
            .ok_or_else(|| KernelError::internal("percentile index out of range"))
    };
    if lower == upper {
        return at(lower);
    }
    let fraction = position - lower as f64;
    Ok(at(lower)? * (1.0 - fraction) + at(upper)? * fraction)
}

/// `_median_of_sorted(numbers)` of a non-empty sorted list.
fn median_of_sorted(numbers: &[f64]) -> f64 {
    let count = numbers.len();
    if count % 2 == 1 {
        numbers[count / 2]
    } else {
        (numbers[count / 2 - 1] + numbers[count / 2]) / 2.0
    }
}

fn first_min(values: &[f64]) -> f64 {
    values[1..].iter().fold(
        values[0],
        |best, &value| if value < best { value } else { best },
    )
}

fn first_max(values: &[f64]) -> f64 {
    values[1..].iter().fold(
        values[0],
        |best, &value| if value > best { value } else { best },
    )
}

/// `_range_summary(series, indices, unit_suffix, digits)`.
fn range_summary(
    item: &SeriesItem,
    indices: &[usize],
    suffix: &str,
    digits: i64,
) -> Result<Option<Dict>, KernelError> {
    let values = series_values(item, indices);
    if values.is_empty() {
        return Ok(None);
    }
    let ordered = coach_numerics::sorted_floats(&values, false);
    let mut summary = Dict::new();
    item.metadata(&mut summary);
    summary.insert("sample_count", values.len() as i64);
    summary.insert(
        format!("min_{suffix}"),
        round_value(Some(first_min(&values)), digits)?,
    );
    summary.insert(
        format!("p05_{suffix}"),
        round_value(Some(percentile_of_sorted(&ordered, 0.05)?), digits)?,
    );
    summary.insert(
        format!("median_{suffix}"),
        round_value(Some(median_of_sorted(&ordered)), digits)?,
    );
    summary.insert(
        format!("max_{suffix}"),
        round_value(Some(first_max(&values)), digits)?,
    );
    Ok(Some(summary))
}

/// `x or 0.0` of a rounded value (`None`, `0.0` and `-0.0` are `0.0`).
fn or_zero(value: Option<&PyValue>) -> f64 {
    match value {
        Some(PyValue::Float(x)) if *x != 0.0 => *x,
        _ => 0.0,
    }
}

/// `_shock_summary(shock, indices)`.
fn shock_summary(
    shock: &[(String, SeriesItem)],
    indices: &[usize],
) -> Result<Option<Dict>, KernelError> {
    let find = |name: &str| shock.iter().find(|(n, _)| n == name).map(|(_, item)| item);
    let mut result = Dict::new();
    if let Some(deflection) = find("deflection")
        && let Some(mut summary) = range_summary(deflection, indices, "in", 4)?
    {
        let range = or_zero(summary.get("max_in")) - or_zero(summary.get("min_in"));
        let range = round_value(Some(range), 4)?;
        summary.insert("range_in", range.clone());
        result.insert("deflection", summary);
        result.insert("deflection_range_in", range);
    }
    if let Some(velocity) = find("velocity") {
        let values: Vec<f64> = series_values(velocity, indices)
            .into_iter()
            .map(f64::abs)
            .collect();
        if !values.is_empty() {
            let ordered = coach_numerics::sorted_floats(&values, false);
            let p90 = round_value(Some(percentile_of_sorted(&ordered, 0.90)?), 3)?;
            let p99 = round_value(Some(percentile_of_sorted(&ordered, 0.99)?), 3)?;
            let mut summary = Dict::new();
            velocity.metadata(&mut summary);
            summary.insert("sample_count", values.len() as i64);
            summary.insert("abs_velocity_p90_in_s", p90.clone());
            summary.insert("abs_velocity_p99_in_s", p99.clone());
            result.insert("velocity", summary);
            result.insert("abs_velocity_p90_in_s", p90);
            result.insert("abs_velocity_p99_in_s", p99);
        }
    }
    Ok((!result.is_empty()).then_some(result))
}

/// `_setup_telemetry(table, series)`.
pub fn setup_telemetry(table: &Table, series: &SetupSeries) -> Result<Dict, KernelError> {
    let indices = setup_sample_indices(table)?;
    let mut available = BTreeSet::new();

    let mut platform = Dict::new();
    if let Some(center) = series.get_platform("center_front_splitter") {
        center.register(&mut available);
        if let Some(summary) = range_summary(center, &indices, "in", 4)? {
            let flat: Vec<(String, PyValue)> = ["min", "p05", "median", "max"]
                .iter()
                .map(|s| {
                    let key = format!("{s}_in");
                    (
                        format!("center_front_splitter_{key}"),
                        summary.get(&key).cloned().unwrap_or(PyValue::None),
                    )
                })
                .collect();
            platform.insert("center_front_splitter", summary);
            for (key, value) in flat {
                platform.insert(key, value);
            }
        }
    }
    let mut corners = Dict::new();
    for tire in TIRES {
        let Some(item) = series.get_platform(tire) else {
            continue;
        };
        item.register(&mut available);
        if let Some(summary) = range_summary(item, &indices, "in", 4)? {
            corners.insert(tire, summary);
        }
    }
    if !corners.is_empty() {
        platform.insert("corners", corners);
    }
    if let Some(dynamic_rear) = series.get_platform("dynamic_rear") {
        dynamic_rear.register(&mut available);
        if let Some(summary) = range_summary(dynamic_rear, &indices, "in", 4)? {
            let flat: Vec<(String, PyValue)> = ["min", "p05", "median", "max"]
                .iter()
                .map(|s| {
                    let key = format!("{s}_in");
                    (
                        format!("dynamic_rear_{key}"),
                        summary.get(&key).cloned().unwrap_or(PyValue::None),
                    )
                })
                .collect();
            platform.insert("dynamic_rear", summary);
            for (key, value) in flat {
                platform.insert(key, value);
            }
        }
    }

    let mut shocks = Dict::new();
    for (tire, shock) in &series.shocks {
        for (_, item) in shock {
            item.register(&mut available);
        }
        if let Some(summary) = shock_summary(shock, &indices)? {
            shocks.insert(tire.as_str(), summary);
        }
    }

    let mut tires = Dict::new();
    for (tire, tire_series) in &series.tires {
        let mut summary = Dict::new();
        for (name, item) in [
            ("live_pressure", &tire_series.live_pressure),
            ("cold_pressure", &tire_series.cold_pressure),
        ] {
            let Some(item) = item else { continue };
            item.register(&mut available);
            if let Some(pressure) = range_summary(item, &indices, "psi", 2)? {
                summary.insert(name, pressure);
            }
        }
        let mut temperatures = Dict::new();
        for (position, item) in &tire_series.temperatures {
            item.register(&mut available);
            if let Some(temperature) = range_summary(item, &indices, "f", 1)? {
                temperatures.insert(position.as_str(), temperature);
            }
        }
        if !temperatures.is_empty() {
            summary.insert("temperatures", temperatures);
        }
        if let Some(average) = &tire_series.carcass_average
            && let Some(average) = range_summary(average, &indices, "f", 1)?
        {
            summary.insert("carcass_average", average);
        }
        if !summary.is_empty() {
            tires.insert(tire.as_str(), summary);
        }
    }
    table.check()?;
    Ok(Dict::new()
        .with(
            "available_channels",
            PyValue::List(available.into_iter().map(PyValue::Str).collect()),
        )
        .with("sample_count", indices.len() as i64)
        .with("sample_scope", SAMPLE_SCOPE)
        .with("platform", platform)
        .with("shocks", shocks)
        .with("tires", tires)
        .with(
            "limits",
            PyValue::List(LIMITS.iter().map(|text| PyValue::from(*text)).collect()),
        ))
}

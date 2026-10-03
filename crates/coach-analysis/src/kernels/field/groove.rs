//! `groove_analysis.analyze_groove_evolution` and its helpers (`_finite`, `_median`,
//! `_percentile`, `_round`, `_series`, `_at`, `_lap_fraction`, `_unavailable`, `_unit_ok`,
//! `_project`, `_zone_contains`, `_wrapped_angle_delta`, `_zone_bin_indices`,
//! `_authoritative_oval`, `_zone_curvature_calibration`, `_ascii_track_type`, `_path_label`,
//! `_window_summary`, `_wear_context`, `_previous_run_changed_tires`,
//! `_valid_green_lap_numbers`).
//!
//! Ordering hazards, in the oracle's order:
//! - Zones: the first 12 load zones, each kept when both ends pass `_lap_fraction` (negative
//!   is rejected, above 1.5 is a percentage, then [0, 1.05] and `% 1.0`) and differ;
//!   `segment` is `int(_finite(segment) or len(zones) + 1)`.
//! - Valid green laps follow `_valid_green_lap_numbers`: runs in order, a run skipped when its
//!   damage context says `automatic_coaching_reference_eligible is False`, laps by `int(lap)`
//!   (a later lap dict of the same number wins), `previous_flag` updated only for laps found.
//! - Rows: every `stride`-th sample in index order through the position, lap, pit, caution
//!   (`int(_finite(flag) or 0) & CAUTION_FLAGS`), speed and odometer filters; the tyre distance
//!   is `statistics.median` of the non-negative odometer readings in channel order.
//! - The origin is `statistics.median` of the rows' latitudes and longitudes; `_project` is
//!   `R * radians(lon - lon0) * cos(radians(lat0))` and `R * (radians(lat) - radians(lat0))`,
//!   left to right (`cos` is the C library's: platform family).
//! - Reference bins: medians of each bin's points when it has at least 3 points from 2 laps;
//!   gaps are filled in bin order from the nearest present bins within four, where the
//!   previous neighbour may already be a filled bin (the list is mutated during the scan).
//! - The track length is builtin `sum` (Neumaier) of `hypot` over consecutive bins.
//! - Calibration: zone bins sorted by `((i + 0.5) / bins - start) % 1.0` (stable), headings
//!   from `atan2` of two-bin central chords at least 0.1 m long, changes `atan2(sin(d),
//!   cos(d))`; the weights are builtin `sum`s that stay `int` 0 when nothing is summed.
//! - Aggregates: per (zone, run, lap) in row order, at most 2,000 offsets each; summaries in
//!   lap order; early and late are the first and last two; the first sustained lap is the
//!   first candidate after the early window whose own and next offsets clear the threshold,
//!   else the first late lap.

use std::cmp::Ordering;
use std::collections::{BTreeMap, BTreeSet, HashMap};

use coach_numerics as num;
use num::Num;

use super::{
    Channels, Series, as_mapping, casefold, finite, format, int_of, int_value, iterate, median,
    round, sequence, strip,
};
use crate::kernels::error::KernelError;
use crate::kernels::value::{Dict, PyValue};

const SCHEMA_VERSION: i64 = 2;
const EARTH_RADIUS_M: f64 = 6_371_008.8;
const DEFAULT_BINS: i64 = 120;
const MAX_INPUT_SAMPLES: i64 = 200_000;
const MAX_ZONES: usize = 12;
const MAX_RUNS: usize = 20;
const MAX_SAMPLES_PER_LAP_ZONE: usize = 2_000;
const MIN_GEOMETRY_BIN_COUNT: i64 = 5;
const MIN_ZONE_REFERENCE_COVERAGE: f64 = 0.85;
const MIN_ZONE_HEADING_CHANGE_DEG: f64 = 12.0;
const MIN_CURVATURE_SIGN_AGREEMENT: f64 = 0.85;
const CAUTION_FLAGS: i128 = 0x0008 | 0x0100 | 0x0200 | 0x4000 | 0x8000;
const TIRE_ODOMETER_CHANNELS: [&str; 4] = ["LFodometer", "RFodometer", "LRodometer", "RRodometer"];

/// The keyword arguments of `analyze_groove_evolution` (`bins` `None` when defaulted).
pub struct GrooveArgs<'a> {
    pub runs: &'a PyValue,
    pub laps: &'a PyValue,
    pub load_zones: &'a PyValue,
    pub channel_units: &'a PyValue,
    pub track_type: &'a PyValue,
    pub bins: Option<&'a PyValue>,
}

fn s(text: &str) -> PyValue {
    PyValue::Str(text.to_owned())
}

/// `_round(value, digits)`: `None` unless finite.
fn round_opt(value: Option<f64>, digits: i64) -> Result<PyValue, KernelError> {
    match value {
        Some(x) if x.is_finite() => Ok(PyValue::Float(round(x, digits)?)),
        _ => Ok(PyValue::None),
    }
}

/// `_median(values)`: `statistics.median`, `None` when empty.
fn median_opt(values: &[f64]) -> Result<Option<f64>, KernelError> {
    if values.is_empty() {
        Ok(None)
    } else {
        Ok(Some(median(values)?))
    }
}

/// `x or 0.0` of an optional float.
fn or_zero(value: Option<f64>) -> f64 {
    match value {
        Some(x) if x != 0.0 => x,
        _ => 0.0,
    }
}

/// `_percentile(values, fraction)`.
fn percentile(values: &[f64], fraction: f64) -> Result<Option<f64>, KernelError> {
    if values.is_empty() {
        return Ok(None);
    }
    let ordered = num::sorted_floats(values, false);
    if ordered.len() == 1 {
        return Ok(Some(ordered[0]));
    }
    let clamped = {
        let low = if 1.0 < fraction { 1.0 } else { fraction };
        if low > 0.0 { low } else { 0.0 }
    };
    let position = clamped * (ordered.len() - 1) as f64;
    let (lower, upper) = (num::floor(position)?, num::ceil(position)?);
    if lower == upper {
        return Ok(Some(ordered[lower as usize]));
    }
    let weight = position - lower as f64;
    Ok(Some(
        ordered[lower as usize] * (1.0 - weight) + ordered[upper as usize] * weight,
    ))
}

/// `_lap_fraction(value)`.
fn lap_fraction(value: Option<f64>) -> Result<Option<f64>, KernelError> {
    let Some(mut number) = value else {
        return Ok(None);
    };
    if number < 0.0 {
        return Ok(None);
    }
    if number > 1.5 {
        number /= 100.0;
    }
    if !(0.0..=1.05).contains(&number) {
        return Ok(None);
    }
    Ok(Some(num::float_mod(number, 1.0)?))
}

/// `_required_channels()`.
fn required_channels() -> PyValue {
    let mut tire_age = Dict::with_capacity(3);
    tire_age.push_new("minimum", PyValue::Int(2));
    tire_age.push_new(
        "any_of",
        PyValue::List(TIRE_ODOMETER_CHANNELS.iter().map(|n| s(n)).collect()),
    );
    tire_age.push_new("unit", "m");
    let mut dict = Dict::with_capacity(4);
    dict.push_new(
        "position",
        PyValue::List(vec![s("Lat (decimal degrees)"), s("Lon (decimal degrees)")]),
    );
    dict.push_new(
        "track_progress",
        PyValue::List(vec![s("LapDistPct"), s("Lap")]),
    );
    dict.push_new("tire_age", tire_age);
    dict.push_new(
        "recommended_filters",
        PyValue::List(vec![s("SessionFlags"), s("OnPitRoad"), s("Speed")]),
    );
    PyValue::Dict(dict)
}

/// `_limitations()`.
fn limitations() -> PyValue {
    PyValue::List(
        [
            "Lateral offset is relative to the driver's session-median path, not an official centerline or measured track edge.",
            "Positive offset means left of travel relative to that reference; it maps toward inside/outside only where signed-curvature quality gates pass.",
            "Inside/outside and oval low/high fields describe observed movement direction, not absolute lane position or a recommended groove.",
            "LapDistPct aligns samples around the lap but cannot establish a groove without recorded Lat/Lon position.",
            "Tire odometers measure distance on the current tire set, not continuous wear; wear remains known only at a discrete service reading.",
            "A detected migration is a repeatable path change, not proof that tire wear or setup caused it; traffic, cautions, line choice, and driver intent may contribute.",
        ]
        .iter()
        .map(|text| s(text))
        .collect(),
    )
}

/// `_unavailable(reason, available_channels=...)`.
fn unavailable(reason: String, available: Vec<&str>) -> PyValue {
    let mut performance = Dict::with_capacity(3);
    performance.push_new("status", "unavailable");
    performance.push_new("best_groove_claimed", false);
    performance.push_new(
        "reason",
        "No path-performance claim is possible without valid path evidence and controlled pace comparisons.",
    );
    let mut calibration = Dict::with_capacity(3);
    calibration.push_new("status", "unavailable");
    calibration.push_new("calibrated_zone_count", PyValue::Int(0));
    calibration.push_new(
        "reason",
        "No valid zone-specific signed-curvature calibration is available.",
    );
    let mut dict = Dict::with_capacity(9);
    dict.push_new("schema_version", PyValue::Int(SCHEMA_VERSION));
    dict.push_new("status", "unavailable");
    dict.push_new("reason", reason);
    dict.push_new("required_channels", required_channels());
    dict.push_new(
        "available_required_channels",
        PyValue::List(available.into_iter().map(s).collect()),
    );
    dict.push_new("zones", PyValue::List(Vec::new()));
    dict.push_new("performance_claim", performance);
    dict.push_new("inside_outside_calibration", calibration);
    dict.push_new("limitations", limitations());
    PyValue::Dict(dict)
}

/// `str(value or "")`.
fn text_or_empty(value: &PyValue) -> Result<String, KernelError> {
    if value.truthy()? {
        super::py_str(value)
    } else {
        Ok(String::new())
    }
}

/// `_unit_ok(unit, accepted)`.
fn unit_ok(unit: &PyValue, accepted: &[&str]) -> Result<bool, KernelError> {
    let normalized = casefold(strip(&text_or_empty(unit)?)).replace(' ', "");
    Ok(normalized.is_empty() || accepted.contains(&normalized.as_str()))
}

/// `_zone_contains(position, start, end)`.
fn zone_contains(position: f64, start: f64, end: f64) -> bool {
    if start <= end {
        start <= position && position < end
    } else {
        position >= start || position < end
    }
}

/// `_wrapped_angle_delta(after, before)`.
fn wrapped_angle_delta(after: f64, before: f64) -> Result<f64, KernelError> {
    let delta = after - before;
    Ok(num::atan2(num::sin(delta)?, num::cos(delta)?))
}

/// `_authoritative_oval(track_type)`.
fn authoritative_oval(track_type: &PyValue) -> Result<bool, KernelError> {
    let normalized = casefold(strip(&text_or_empty(track_type)?));
    Ok(!normalized.is_empty() && normalized.contains("oval"))
}

/// `_ascii_track_type(value)`.
fn ascii_track_type(value: &PyValue) -> Result<PyValue, KernelError> {
    let text = text_or_empty(value)?;
    let text = strip(&text);
    if text.is_empty() {
        return Ok(PyValue::None);
    }
    let ascii: String = text.chars().filter(char::is_ascii).take(80).collect();
    Ok(if ascii.is_empty() {
        PyValue::None
    } else {
        PyValue::Str(ascii)
    })
}

/// `_path_label(offset_m)`.
fn path_label(offset: Option<f64>) -> PyValue {
    match offset {
        None => PyValue::None,
        Some(x) if x > 0.35 => s("left-of-session-reference"),
        Some(x) if x < -0.35 => s("right-of-session-reference"),
        Some(_) => s("near-session-reference"),
    }
}

/// A zone as `analyze_groove_evolution` builds it.
#[derive(Clone)]
struct Zone {
    segment: PyValue,
    start: f64,
    end: f64,
}

/// A row of `raw_rows` (`index` is not read after it is built).
struct Row {
    lat: f64,
    lon: f64,
    pct: f64,
    lap: i64,
    tire_distance: f64,
    session_time: Option<f64>,
    x: f64,
    y: f64,
}

/// A per-lap summary of one (zone, run).
#[derive(Clone)]
struct Summary {
    lap: i64,
    offset: f64,
    tire_distance: f64,
    session_time: Option<f64>,
}

/// `_window_summary(items, track_length_m)` and the median age in lap equivalents it rounds.
fn window_summary(items: &[Summary], track_length: f64) -> Result<(PyValue, f64), KernelError> {
    let offsets: Vec<f64> = items.iter().map(|item| item.offset).collect();
    let ages: Vec<f64> = items.iter().map(|item| item.tire_distance).collect();
    let median_offset = median_opt(&offsets)?;
    let median_age = median_opt(&ages)?;
    let age_laps = round(or_zero(median_age) / track_length, 2)?;
    let iqr = or_zero(percentile(&offsets, 0.75)?) - or_zero(percentile(&offsets, 0.25)?);
    let mut dict = Dict::with_capacity(6);
    dict.push_new(
        "laps",
        PyValue::List(items.iter().map(|item| PyValue::Int(item.lap)).collect()),
    );
    dict.push_new("median_lateral_offset_m", round_opt(median_offset, 3)?);
    dict.push_new("path", path_label(median_offset));
    dict.push_new("median_tire_distance_m", round_opt(median_age, 1)?);
    dict.push_new("median_tire_age_lap_equivalents", age_laps);
    dict.push_new("lap_to_lap_offset_iqr_m", round_opt(Some(iqr), 3)?);
    Ok((PyValue::Dict(dict), age_laps))
}

/// `_wear_context(run)`.
fn wear_context(run: &Dict) -> Result<PyValue, KernelError> {
    let Some(observation) = run.get("tire_observation").and_then(as_mapping) else {
        let mut dict = Dict::with_capacity(2);
        dict.push_new("status", "unmeasured");
        dict.push_new(
            "note",
            "No discrete post-run tire reading is available for this run.",
        );
        return Ok(PyValue::Dict(dict));
    };
    let mut lowest = observation.get("lowest_remaining_percent").and_then(finite);
    if lowest.is_none() {
        let tires = match observation.get("tires") {
            Some(value) if value.truthy()? => value,
            _ => &PyValue::None,
        };
        let mut values: Vec<f64> = Vec::new();
        match tires {
            PyValue::None => {}
            PyValue::Dict(dict) => {
                for tire in dict.values() {
                    if let Some(tire) = as_mapping(tire)
                        && let Some(value) = tire.get("minimum_remaining_percent").and_then(finite)
                    {
                        values.push(value);
                    }
                }
            }
            other => {
                return Err(KernelError::raises(
                    "AttributeError",
                    format!("'{}' object has no attribute 'values'", other.type_name()),
                ));
            }
        }
        // min() keeps the first of equal values.
        lowest = values
            .iter()
            .copied()
            .fold(None, |low: Option<f64>, value| match low {
                Some(current) if value.partial_cmp(&current) != Some(Ordering::Less) => {
                    Some(current)
                }
                _ => Some(value),
            });
    }
    let mut dict = Dict::with_capacity(4);
    dict.push_new(
        "status",
        if lowest.is_some() {
            "measured-at-service"
        } else {
            "unmeasured"
        },
    );
    dict.push_new("lowest_remaining_percent", round_opt(lowest, 2)?);
    dict.push_new(
        "lowest_remaining_tire",
        observation
            .get("lowest_remaining_tire")
            .cloned()
            .unwrap_or(PyValue::None),
    );
    dict.push_new(
        "measurement_scope",
        "discrete reading assigned to the run end; it does not timestamp when wear occurred",
    );
    Ok(PyValue::Dict(dict))
}

/// Python `str(item).upper()` of a value in `tires_changed_observed`.
fn upper_text(item: &PyValue) -> Result<String, KernelError> {
    Ok(super::py_str(item)?.to_uppercase())
}

/// `_previous_run_changed_tires(runs, run_index)`.
fn previous_run_changed_tires(runs: &[Dict], run_index: usize) -> Result<bool, KernelError> {
    if run_index == 0 {
        return Ok(false);
    }
    let Some(service) = runs[run_index - 1].get("pit_service").and_then(as_mapping) else {
        return Ok(false);
    };
    let changed = iterate(service.get("tires_changed_observed"))?;
    let mut names: BTreeSet<String> = BTreeSet::new();
    for item in &changed {
        names.insert(upper_text(item)?);
    }
    Ok(names.len() >= 2)
}

/// `int(number)` of a finite float used as a lap number.
fn lap_int(number: f64) -> Result<i64, KernelError> {
    int_of(number)
}

/// `_valid_green_lap_numbers(runs, laps)`.
fn valid_green_lap_numbers(
    runs: &[PyValue],
    laps: &[PyValue],
) -> Result<BTreeSet<i64>, KernelError> {
    let mut lap_by_number: HashMap<i64, &Dict> = HashMap::new();
    for item in laps {
        if let Some(lap) = as_mapping(item)
            && let Some(number) = lap.get("lap").and_then(finite)
        {
            lap_by_number.insert(lap_int(number)?, lap);
        }
    }
    let mut valid = BTreeSet::new();
    for run in runs {
        let Some(run) = as_mapping(run) else {
            continue;
        };
        if let Some(context) = run.get("damage_repair_context").and_then(as_mapping)
            && matches!(
                context.get("automatic_coaching_reference_eligible"),
                Some(PyValue::Bool(false))
            )
        {
            continue;
        }
        let mut screened: BTreeSet<i64> = BTreeSet::new();
        for value in iterate(run.get("coaching_reference_lap_numbers"))? {
            if let Some(number) = finite(&value) {
                screened.insert(lap_int(number)?);
            }
        }
        let mut previous_flag: Option<String> = None;
        for raw in iterate(run.get("lap_numbers"))? {
            let Some(number) = finite(&raw) else {
                continue;
            };
            let number = lap_int(number)?;
            let Some(lap) = lap_by_number.get(&number) else {
                continue;
            };
            let flag_state = text_or_empty(lap.get("flag_state").unwrap_or(&PyValue::None))?;
            let racing_fraction = lap.get("racing_state_fraction").and_then(finite);
            let is_restart = previous_flag.as_deref() == Some("caution") && flag_state == "green";
            let pit_time = or_zero(lap.get("pit_time_s").and_then(finite));
            if flag_state == "green"
                && matches!(lap.get("complete"), Some(PyValue::Bool(true)))
                && pit_time < 1.0
                && racing_fraction.is_none_or(|fraction| fraction >= 0.98)
                && !is_restart
                && (screened.is_empty() || screened.contains(&number))
            {
                valid.insert(number);
            }
            previous_flag = Some(flag_state);
        }
    }
    Ok(valid)
}

/// `int(_finite(_at(flags, index, 0)) or 0) & CAUTION_FLAGS`, exact for any float.
fn caution_bits(flag: Option<f64>) -> i128 {
    let value = or_zero(flag);
    // Floats of 2**127 and beyond are multiples of 2**17: their low bits are zero.
    if value.abs() >= 1.7e38 {
        return 0;
    }
    (value.trunc() as i128) & CAUTION_FLAGS
}

/// `_zone_bin_indices(zone, bins)` for a zone's (start, end).
fn zone_bin_indices(start: f64, end: f64, bins: usize) -> Result<Vec<usize>, KernelError> {
    if bins < 1 {
        return Ok(Vec::new());
    }
    let start = num::float_mod(start, 1.0)?;
    let end = num::float_mod(end, 1.0)?;
    let center = |index: usize| (index as f64 + 0.5) / bins as f64;
    let mut selected: Vec<(usize, f64)> = Vec::new();
    for index in 0..bins {
        if zone_contains(center(index), start, end) {
            selected.push((index, num::float_mod(center(index) - start, 1.0)?));
        }
    }
    num::sort_by(&mut selected, |a, b| a.1 < b.1, false);
    Ok(selected.into_iter().map(|(index, _)| index).collect())
}

/// `sum(...)` of floats from the int start (an `int` 0 when nothing is summed).
fn builtin_sum(values: &[f64]) -> Num {
    num::sum_floats_from_int_zero(values)
}

/// `_zone_curvature_calibration(points, zone, observed_reference_bins, track_type)`.
fn zone_curvature_calibration(
    points: &[(f64, f64)],
    zone: &Zone,
    observed: &[bool],
    track_type: &PyValue,
) -> Result<PyValue, KernelError> {
    let bins = points.len();
    let indices = zone_bin_indices(zone.start, zone.end, bins)?;
    let observed_count = indices.iter().filter(|&&index| observed[index]).count();
    let coverage = if indices.is_empty() {
        0.0
    } else {
        num::int_true_div(observed_count as i64, indices.len() as i64)?
    };
    let mut headings: Vec<f64> = Vec::new();
    let tangent_radius = 2usize;
    for &index in &indices {
        let before = points[(index + bins - tangent_radius % bins) % bins];
        let after = points[(index + tangent_radius) % bins];
        let (dx, dy) = (after.0 - before.0, after.1 - before.1);
        if num::hypot(dx, dy) < 0.1 {
            continue;
        }
        headings.push(num::atan2(dy, dx));
    }
    let changes: Vec<f64> = headings
        .windows(2)
        .map(|pair| wrapped_angle_delta(pair[1], pair[0]))
        .collect::<Result<_, _>>()?;
    let positive: Vec<f64> = changes.iter().copied().filter(|c| *c > 0.0).collect();
    let negative: Vec<f64> = changes.iter().filter(|c| **c < 0.0).map(|c| -c).collect();
    let positive_weight = builtin_sum(&positive);
    let negative_weight = builtin_sum(&negative);
    let absolute_weight = num::add(positive_weight, negative_weight)?;
    let signed_change = builtin_sum(&changes);
    let agreement = if num::py_gt(absolute_weight, Num::Float(1e-12)) {
        // max(positive, negative) keeps the first unless the second is greater.
        let larger = if num::py_gt(negative_weight, positive_weight) {
            negative_weight
        } else {
            positive_weight
        };
        num::truediv(larger, absolute_weight)?
    } else {
        0.0
    };
    let signed_change_deg = num::degrees(signed_change.to_f64());
    let absolute_change_deg = num::degrees(absolute_weight.to_f64());

    let mut reasons: Vec<String> = Vec::new();
    if (indices.len() as i64) < MIN_GEOMETRY_BIN_COUNT {
        reasons.push(format!(
            "fewer than {MIN_GEOMETRY_BIN_COUNT} reference bins in the zone"
        ));
    }
    if coverage < MIN_ZONE_REFERENCE_COVERAGE {
        reasons.push(format!(
            "reference coverage {} is below {}",
            format(coverage, ".0%")?,
            format(MIN_ZONE_REFERENCE_COVERAGE, ".0%")?
        ));
    }
    if (changes.len() as i64) < MIN_GEOMETRY_BIN_COUNT - 1 {
        reasons.push("too few stable tangent changes".to_owned());
    }
    if signed_change_deg.abs() < MIN_ZONE_HEADING_CHANGE_DEG {
        reasons.push(format!(
            "net heading change {} deg is below {} deg",
            format(signed_change_deg.abs(), ".1f")?,
            format(MIN_ZONE_HEADING_CHANGE_DEG, ".0f")?
        ));
    }
    if agreement < MIN_CURVATURE_SIGN_AGREEMENT {
        reasons.push(format!(
            "curvature-sign agreement {} is below {}",
            format(agreement, ".0%")?,
            format(MIN_CURVATURE_SIGN_AGREEMENT, ".0%")?
        ));
    }
    let calibrated = reasons.is_empty();
    let turn_direction = if calibrated && num::py_gt(signed_change, Num::Float(0.0)) {
        Some("left")
    } else if calibrated && num::py_lt(signed_change, Num::Float(0.0)) {
        Some("right")
    } else {
        None
    };
    let inside_sign: Option<i64> = match turn_direction {
        Some("left") => Some(1),
        Some("right") => Some(-1),
        _ => None,
    };
    let oval = authoritative_oval(track_type)?;
    let confidence =
        if calibrated && coverage >= 0.95 && agreement >= 0.95 && signed_change_deg.abs() >= 25.0 {
            "high"
        } else if calibrated {
            "medium"
        } else {
            "unavailable"
        };
    let mut thresholds = Dict::with_capacity(4);
    thresholds.push_new(
        "minimum_reference_bins",
        PyValue::Int(MIN_GEOMETRY_BIN_COUNT),
    );
    thresholds.push_new(
        "minimum_reference_coverage_fraction",
        MIN_ZONE_REFERENCE_COVERAGE,
    );
    thresholds.push_new(
        "minimum_net_heading_change_deg",
        MIN_ZONE_HEADING_CHANGE_DEG,
    );
    thresholds.push_new(
        "minimum_curvature_sign_agreement_fraction",
        MIN_CURVATURE_SIGN_AGREEMENT,
    );
    let mut dict = Dict::with_capacity(20);
    dict.push_new(
        "status",
        if calibrated {
            "calibrated"
        } else {
            "unavailable"
        },
    );
    dict.push_new("confidence", confidence);
    dict.push_new(
        "method",
        "signed heading change of two-bin central tangents on the projected session-reference path",
    );
    dict.push_new("reference_bin_count", PyValue::Int(indices.len() as i64));
    dict.push_new(
        "observed_reference_bin_count",
        PyValue::Int(observed_count as i64),
    );
    dict.push_new(
        "reference_bin_coverage_fraction",
        round_opt(Some(coverage), 4)?,
    );
    dict.push_new(
        "net_heading_change_deg",
        round_opt(Some(signed_change_deg), 2)?,
    );
    dict.push_new(
        "absolute_heading_change_deg",
        round_opt(Some(absolute_change_deg), 2)?,
    );
    dict.push_new(
        "curvature_sign_agreement_fraction",
        round_opt(Some(agreement), 4)?,
    );
    dict.push_new("travel_turn_direction", PyValue::from(turn_direction));
    dict.push_new(
        "inside_lateral_offset_sign",
        PyValue::from(match inside_sign {
            Some(1) => Some("positive-left-of-travel"),
            Some(-1) => Some("negative-right-of-travel"),
            _ => None,
        }),
    );
    dict.push_new(
        "inside_lateral_offset_sign_value",
        PyValue::from(inside_sign),
    );
    dict.push_new("track_edges_used", false);
    dict.push_new("absolute_lane_position_available", false);
    dict.push_new("track_type", ascii_track_type(track_type)?);
    dict.push_new("oval_track_type_confirmed", oval);
    dict.push_new(
        "oval_direction_mapping",
        if oval && calibrated {
            let mut mapping = Dict::with_capacity(2);
            mapping.push_new("toward_inside", "toward-low-side");
            mapping.push_new("toward_outside", "toward-high-side");
            PyValue::Dict(mapping)
        } else {
            PyValue::None
        },
    );
    dict.push_new("thresholds", thresholds);
    dict.push_new(
        "unavailable_reason",
        if reasons.is_empty() {
            PyValue::None
        } else {
            PyValue::Str(reasons.join("; "))
        },
    );
    dict.push_new(
        "claim_scope",
        "directional movement relative to the session reference path only; not absolute lane position or groove effectiveness",
    );
    Ok(PyValue::Dict(dict))
}

/// `_at(values, index, default)` then `_finite`.
fn finite_at(series: Option<Series<'_>>, index: usize) -> Option<f64> {
    series.and_then(|s| {
        if index < s.len() {
            s.finite(index)
        } else {
            None
        }
    })
}

/// `analyze_groove_evolution(channels, *, runs, laps, load_zones, channel_units, track_type,
/// bins)`.
pub fn analyze_groove_evolution(
    channels: &Channels<'_>,
    args: &GrooveArgs<'_>,
) -> Result<PyValue, KernelError> {
    let bins = match args.bins {
        None => DEFAULT_BINS,
        Some(PyValue::Int(n)) if (60..=240).contains(n) => *n,
        Some(_) => {
            return Err(KernelError::raises(
                "ValueError",
                "bins must be an integer between 60 and 240",
            ));
        }
    };
    let bins_usize = bins as usize;
    let required_names = ["Lat", "Lon", "LapDistPct", "Lap"];
    let mut present: Vec<&str> = Vec::new();
    let mut missing: Vec<&str> = Vec::new();
    for name in required_names {
        if channels.series(name)?.is_some() {
            present.push(name);
        } else {
            missing.push(name);
        }
    }
    if !missing.is_empty() {
        return Ok(unavailable(
            format!(
                "Missing measured position/progress channels: {}",
                missing.join(", ")
            ),
            present,
        ));
    }
    let units: Option<&Dict> = match args.channel_units {
        value if !value.truthy()? => None,
        PyValue::Dict(dict) => Some(dict),
        other => {
            return Err(KernelError::internal(format!(
                "channel_units is a {}, not a dict",
                other.type_name()
            )));
        }
    };
    let unit = |name: &str| -> &PyValue {
        units
            .and_then(|dict| dict.get(name))
            .unwrap_or(&PyValue::None)
    };
    let degrees = ["deg", "degree", "degrees"];
    if !unit_ok(unit("Lat"), &degrees)? || !unit_ok(unit("Lon"), &degrees)? {
        return Ok(unavailable(
            "Lat/Lon units are not decimal degrees; geographic projection is unsafe.".to_owned(),
            Vec::new(),
        ));
    }
    let mut odometer_names: Vec<&str> = Vec::new();
    for name in TIRE_ODOMETER_CHANNELS {
        if channels.series(name)?.is_some() && unit_ok(unit(name), &["m", "meter", "meters"])? {
            odometer_names.push(name);
        }
    }
    if odometer_names.len() < 2 {
        let mut available: Vec<&str> = required_names.to_vec();
        available.extend(odometer_names.iter().copied());
        return Ok(unavailable(
            "At least two meter-valued tire odometer channels are required to compare path against tire distance.".to_owned(),
            available,
        ));
    }

    let mut zones: Vec<Zone> = Vec::new();
    let load_zones = sequence(args.load_zones, "load_zones")?;
    for raw in load_zones.iter().take(MAX_ZONES) {
        let Some(raw) = as_mapping(raw) else {
            continue;
        };
        let start = lap_fraction(raw.get("start_pct").and_then(finite))?;
        let end = lap_fraction(raw.get("end_pct").and_then(finite))?;
        let (Some(start), Some(end)) = (start, end) else {
            continue;
        };
        if start == end {
            continue;
        }
        let segment = match raw.get("segment").and_then(finite) {
            Some(value) if value != 0.0 => int_value(value)?,
            _ => PyValue::Int(zones.len() as i64 + 1),
        };
        zones.push(Zone {
            segment,
            start,
            end,
        });
    }
    if zones.is_empty() {
        return Ok(unavailable(
            "No telemetry-derived corner/load zones were available.".to_owned(),
            Vec::new(),
        ));
    }

    let runs = sequence(args.runs, "runs")?;
    let laps = sequence(args.laps, "laps")?;
    let valid_green_laps = valid_green_lap_numbers(runs, laps)?;
    if valid_green_laps.is_empty() {
        return Ok(unavailable(
            "No complete racing-state green laps remained after pit, partial-lap, and first-restart-lap exclusions.".to_owned(),
            Vec::new(),
        ));
    }

    let required = |name: &str| -> Result<Series<'_>, KernelError> {
        channels
            .series(name)?
            .ok_or_else(|| KernelError::internal(format!("{name} vanished")))
    };
    let lat_values = required("Lat")?;
    let lon_values = required("Lon")?;
    let pct_values = required("LapDistPct")?;
    let lap_values = required("Lap")?;
    let flags = channels.series("SessionFlags")?;
    let pit = channels.series("OnPitRoad")?;
    let speed = channels.series("Speed")?;
    let session_time = channels.series("SessionTime")?;
    let tire_series: Vec<Series<'_>> = odometer_names
        .iter()
        .map(|name| required(name))
        .collect::<Result<_, _>>()?;
    let length = lat_values.len().max(lon_values.len()).max(pct_values.len());
    let stride = num::ceil(num::int_true_div(length as i64, MAX_INPUT_SAMPLES)?)?.max(1) as usize;

    let mut rows: Vec<Row> = Vec::new();
    let mut ages: Vec<f64> = Vec::with_capacity(4);
    let mut index = 0usize;
    while index < length {
        let current = index;
        index += stride;
        let lat = finite_at(Some(lat_values), current);
        let lon = finite_at(Some(lon_values), current);
        let position = lap_fraction(finite_at(Some(pct_values), current))?;
        let lap_number = finite_at(Some(lap_values), current);
        let (Some(lat), Some(lon), Some(position), Some(lap_number)) =
            (lat, lon, position, lap_number)
        else {
            continue;
        };
        if !(-90.0..=90.0).contains(&lat) || !(-180.0..=180.0).contains(&lon) || lap_number < 1.0 {
            continue;
        }
        let lap = lap_int(lap_number)?;
        if !valid_green_laps.contains(&lap) {
            continue;
        }
        if let Some(pit) = pit
            && current < pit.len()
            && pit.truthy(current)?
        {
            continue;
        }
        let flag = match flags {
            Some(flags) if current < flags.len() => flags.finite(current),
            // _at(flags, index, 0): the default 0 is finite.
            _ => Some(0.0),
        };
        if caution_bits(flag) != 0 {
            continue;
        }
        if let Some(speed_value) = finite_at(speed, current)
            && speed_value < 5.0
        {
            continue;
        }
        ages.clear();
        for series in &tire_series {
            if let Some(value) = finite_at(Some(*series), current)
                && value >= 0.0
            {
                ages.push(value);
            }
        }
        if ages.len() < 2 {
            continue;
        }
        rows.push(Row {
            lat,
            lon,
            pct: position,
            lap,
            tire_distance: median(&ages)?,
            session_time: finite_at(session_time, current),
            x: 0.0,
            y: 0.0,
        });
    }
    if (rows.len() as i64) < bins * 2 {
        return Ok(unavailable(
            "Too few valid green, non-pit geographic samples to reconstruct a path.".to_owned(),
            Vec::new(),
        ));
    }
    let reference_laps: BTreeSet<i64> = rows.iter().map(|row| row.lap).collect();
    if reference_laps.len() < 3 {
        return Ok(unavailable(
            "At least three valid green laps are required for a session reference path.".to_owned(),
            Vec::new(),
        ));
    }
    let lat0 = median(&rows.iter().map(|row| row.lat).collect::<Vec<_>>())?;
    let lon0 = median(&rows.iter().map(|row| row.lon).collect::<Vec<_>>())?;
    let cos_lat0 = num::cos(num::radians(lat0))?;
    let lat0_rad = num::radians(lat0);
    for row in &mut rows {
        row.x = EARTH_RADIUS_M * num::radians(row.lon - lon0) * cos_lat0;
        row.y = EARTH_RADIUS_M * (num::radians(row.lat) - lat0_rad);
    }
    let x_bounds = min_max(rows.iter().map(|row| row.x));
    let y_bounds = min_max(rows.iter().map(|row| row.y));
    let span_m = num::hypot(x_bounds.1 - x_bounds.0, y_bounds.1 - y_bounds.0);
    if !(80.0..=30_000.0).contains(&span_m) {
        return Ok(unavailable(
            format!(
                "Projected Lat/Lon span ({} m) is not plausible for one circuit layout.",
                format(span_m, ".1f")?
            ),
            Vec::new(),
        ));
    }

    let mut by_bin: Vec<Vec<(f64, f64)>> = vec![Vec::new(); bins_usize];
    let mut bin_laps: Vec<BTreeSet<i64>> = vec![BTreeSet::new(); bins_usize];
    for row in &rows {
        let bin = (int_of(row.pct * bins as f64)?.max(0) as usize).min(bins_usize - 1);
        by_bin[bin].push((row.x, row.y));
        bin_laps[bin].insert(row.lap);
    }
    let mut reference: Vec<Option<(f64, f64)>> = Vec::with_capacity(bins_usize);
    for bin in 0..bins_usize {
        let points = &by_bin[bin];
        if points.len() >= 3 && bin_laps[bin].len() >= 2 {
            let xs: Vec<f64> = points.iter().map(|p| p.0).collect();
            let ys: Vec<f64> = points.iter().map(|p| p.1).collect();
            reference.push(Some((median(&xs)?, median(&ys)?)));
        } else {
            reference.push(None);
        }
    }
    drop(by_bin);
    drop(bin_laps);
    let observed: Vec<bool> = reference.iter().map(Option::is_some).collect();
    let present_count = observed.iter().filter(|&&b| b).count();
    let coverage = num::int_true_div(present_count as i64, bins)?;
    if coverage < 0.90 {
        return Ok(unavailable(
            format!(
                "Only {} of lap-distance bins had a stable multi-lap geographic reference.",
                format(coverage, ".1%")?
            ),
            Vec::new(),
        ));
    }
    for index in 0..bins_usize {
        if reference[index].is_some() {
            continue;
        }
        let previous = (1..5)
            .map(|distance| (index + bins_usize - distance % bins_usize) % bins_usize)
            .find(|&candidate| reference[candidate].is_some());
        let following = (1..5)
            .map(|distance| (index + distance) % bins_usize)
            .find(|&candidate| reference[candidate].is_some());
        let (Some(previous), Some(following)) = (previous, following) else {
            return Ok(unavailable(
                "A geographic reference-path gap exceeded four lap-distance bins.".to_owned(),
                Vec::new(),
            ));
        };
        let previous_distance = (index + bins_usize - previous) % bins_usize;
        let following_distance = (following + bins_usize - index) % bins_usize;
        let fraction = num::int_true_div(
            previous_distance as i64,
            (previous_distance + following_distance) as i64,
        )?;
        let (left, right) = (
            reference[previous].unwrap_or_default(),
            reference[following].unwrap_or_default(),
        );
        reference[index] = Some((
            left.0 + (right.0 - left.0) * fraction,
            left.1 + (right.1 - left.1) * fraction,
        ));
    }
    let points: Vec<(f64, f64)> = reference.into_iter().flatten().collect();
    if points.len() != bins_usize {
        return Ok(unavailable(
            "Unable to complete a bounded circular reference path.".to_owned(),
            Vec::new(),
        ));
    }
    let segment_lengths: Vec<f64> = (0..bins_usize)
        .map(|index| {
            let next = points[(index + 1) % bins_usize];
            num::hypot(next.0 - points[index].0, next.1 - points[index].1)
        })
        .collect();
    let track_length_m = builtin_sum(&segment_lengths).to_f64();
    if !(100.0..=30_000.0).contains(&track_length_m) {
        return Ok(unavailable(
            format!(
                "Reconstructed path length ({} m) is not plausible for one circuit layout.",
                format(track_length_m, ".1f")?
            ),
            Vec::new(),
        ));
    }
    let zone_calibrations: Vec<PyValue> = zones
        .iter()
        .map(|zone| zone_curvature_calibration(&points, zone, &observed, args.track_type))
        .collect::<Result<_, _>>()?;

    // Runs: the first 20 mappings, copied (dict(run)); lap -> (run index, run number).
    let bounded_runs: Vec<Dict> = runs
        .iter()
        .take(MAX_RUNS)
        .filter_map(|run| as_mapping(run).cloned())
        .collect();
    let mut run_by_lap: HashMap<i64, usize> = HashMap::new();
    for (run_index, run) in bounded_runs.iter().enumerate() {
        for value in iterate(run.get("lap_numbers"))? {
            if let Some(number) = finite(&value) {
                run_by_lap.insert(lap_int(number)?, run_index);
            }
        }
    }

    #[derive(Default)]
    struct Bucket {
        offsets: Vec<f64>,
        ages: Vec<f64>,
        times: Vec<f64>,
    }
    // (zone, run, lap) -> bucket; visited later per (zone, run) in lap order.
    let mut aggregates: BTreeMap<(usize, usize, i64), Bucket> = BTreeMap::new();
    let mut rejected_offset_samples: i64 = 0;
    for row in &rows {
        if !valid_green_laps.contains(&row.lap) {
            continue;
        }
        let Some(&run_index) = run_by_lap.get(&row.lap) else {
            continue;
        };
        let q = row.pct * bins as f64 - 0.5;
        let floor_q = num::floor(q)?;
        let left_index = floor_q.rem_euclid(bins) as usize;
        let fraction = q - floor_q as f64;
        let right_index = (left_index + 1) % bins_usize;
        let previous_index = (left_index + bins_usize - 1) % bins_usize;
        let next_index = (right_index + 1) % bins_usize;
        let (left_point, right_point) = (points[left_index], points[right_index]);
        let ref_x = left_point.0 + (right_point.0 - left_point.0) * fraction;
        let ref_y = left_point.1 + (right_point.1 - left_point.1) * fraction;
        let tangent_x = points[next_index].0 - points[previous_index].0;
        let tangent_y = points[next_index].1 - points[previous_index].1;
        let magnitude = num::hypot(tangent_x, tangent_y);
        if magnitude < 0.1 {
            continue;
        }
        let (normal_x, normal_y) = (-tangent_y / magnitude, tangent_x / magnitude);
        let lateral = (row.x - ref_x) * normal_x + (row.y - ref_y) * normal_y;
        if lateral.abs() > 30.0 {
            rejected_offset_samples += 1;
            continue;
        }
        for (zone_index, zone) in zones.iter().enumerate() {
            if !zone_contains(row.pct, zone.start, zone.end) {
                continue;
            }
            let bucket = aggregates
                .entry((zone_index, run_index, row.lap))
                .or_default();
            if bucket.offsets.len() >= MAX_SAMPLES_PER_LAP_ZONE {
                continue;
            }
            bucket.offsets.push(lateral);
            bucket.ages.push(row.tire_distance);
            if let Some(time) = row.session_time {
                bucket.times.push(time);
            }
        }
    }

    let mut output_zones: Vec<PyValue> = Vec::with_capacity(zones.len());
    let mut comparison_count: i64 = 0;
    let mut migration_count: i64 = 0;
    for (zone_index, zone) in zones.iter().enumerate() {
        let calibration = &zone_calibrations[zone_index];
        let mut zone_runs: Vec<PyValue> = Vec::new();
        for (run_index, run) in bounded_runs.iter().enumerate() {
            let mut summaries: Vec<Summary> = Vec::new();
            for (&(_, _, lap), bucket) in aggregates
                .range((zone_index, run_index, i64::MIN)..=(zone_index, run_index, i64::MAX))
            {
                if bucket.offsets.len() < 5 || bucket.ages.len() < 5 {
                    continue;
                }
                summaries.push(Summary {
                    lap,
                    offset: median(&bucket.offsets)?,
                    tire_distance: median(&bucket.ages)?,
                    session_time: median_opt(&bucket.times)?,
                });
            }
            if summaries.len() < 4 {
                continue;
            }
            let early = &summaries[..2];
            let late = &summaries[summaries.len() - 2..];
            let (early_window, early_age) = window_summary(early, track_length_m)?;
            let (late_window, late_age) = window_summary(late, track_length_m)?;
            // min() keeps the first of equal values.
            let minimum_distance =
                summaries
                    .iter()
                    .map(|item| item.tire_distance)
                    .fold(f64::NAN, |low, value| {
                        if low.is_nan() || value < low {
                            value
                        } else {
                            low
                        }
                    });
            let minimum_age_laps = minimum_distance / track_length_m;
            let fresh_available = minimum_age_laps <= 1.5;
            let fresh_status =
                if fresh_available && previous_run_changed_tires(&bounded_runs, run_index)? {
                    "confirmed-after-observed-tire-change"
                } else if fresh_available && run_index == 0 {
                    "low-recorded-distance-initial-history-unknown"
                } else if fresh_available {
                    "low-recorded-distance-without-prior-change-confirmation"
                } else {
                    "not-observed"
                };
            let age_separation = late_age - early_age;
            let offsets_early: Vec<f64> = early.iter().map(|item| item.offset).collect();
            let offsets_late: Vec<f64> = late.iter().map(|item| item.offset).collect();
            let early_median = or_zero(median_opt(&offsets_early)?);
            let late_median = or_zero(median_opt(&offsets_late)?);
            let delta = late_median - early_median;
            let deviations: Vec<f64> = offsets_early
                .iter()
                .map(|value| (value - early_median).abs())
                .chain(offsets_late.iter().map(|value| (value - late_median).abs()))
                .collect();
            let robust_noise = 1.4826 * or_zero(median_opt(&deviations)?);
            let threshold = {
                let scaled = 2.5 * robust_noise;
                if scaled > 0.35 { scaled } else { 0.35 }
            };
            let enough_age_change = age_separation >= 2.0;
            let detected = enough_age_change && delta.abs() >= threshold;
            let mut first_migration: Option<&Summary> = None;
            if detected {
                let direction_sign = if delta > 0.0 { 1.0 } else { -1.0 };
                let last_early = early[early.len() - 1].lap;
                let candidates: Vec<&Summary> = summaries
                    .iter()
                    .filter(|item| item.lap > last_early)
                    .collect();
                for (index, item) in candidates.iter().enumerate() {
                    let shifted = (item.offset - early_median) * direction_sign >= threshold;
                    let next_shifted = index + 1 < candidates.len()
                        && (candidates[index + 1].offset - early_median) * direction_sign
                            >= threshold;
                    if shifted && next_shifted {
                        first_migration = Some(item);
                        break;
                    }
                }
                if first_migration.is_none() {
                    first_migration = Some(&late[0]);
                }
                migration_count += 1;
            }
            let mut inside_outside_direction = PyValue::None;
            let mut oval_low_high_direction = PyValue::None;
            let mut display_direction = PyValue::None;
            let inside_sign = calibration
                .get("inside_lateral_offset_sign_value")
                .and_then(finite);
            if detected
                && matches!(calibration.get("status"), Some(PyValue::Str(status)) if status == "calibrated")
                && matches!(inside_sign, Some(sign) if sign == -1.0 || sign == 1.0)
            {
                let sign = inside_sign.unwrap_or_default();
                let moved_toward_inside = delta * sign > 0.0;
                let inside_outside = if moved_toward_inside {
                    "toward-inside"
                } else {
                    "toward-outside"
                };
                inside_outside_direction = s(inside_outside);
                if calibration
                    .get("oval_track_type_confirmed")
                    .map(PyValue::truthy)
                    .transpose()?
                    .unwrap_or(false)
                {
                    let low_high = if moved_toward_inside {
                        "toward-low-side"
                    } else {
                        "toward-high-side"
                    };
                    oval_low_high_direction = s(low_high);
                    display_direction = PyValue::Str(format!(
                        "{low_high} ({})",
                        if moved_toward_inside {
                            "inside"
                        } else {
                            "outside"
                        }
                    ));
                } else {
                    display_direction = s(inside_outside);
                }
            }
            comparison_count += 1;
            let wear = wear_context(run)?;
            let run_number = match run.get("run_number").and_then(finite) {
                Some(value) if value != 0.0 => int_value(value)?,
                _ => PyValue::Int(run_index as i64 + 1),
            };
            let mut migration = Dict::with_capacity(16);
            migration.push_new("status", if detected { "detected" } else { "not-detected" });
            migration.push_new("claim_scope", "observed relative-line movement only");
            migration.push_new("traffic_and_clean_air_screened", false);
            migration.push_new(
                "direction",
                if detected && delta > 0.0 {
                    s("left-relative-to-session-reference")
                } else if detected {
                    s("right-relative-to-session-reference")
                } else {
                    PyValue::None
                },
            );
            migration.push_new(
                "inside_outside_calibration_status",
                calibration.get("status").cloned().unwrap_or(PyValue::None),
            );
            migration.push_new("inside_outside_direction", inside_outside_direction);
            migration.push_new("oval_low_high_direction", oval_low_high_direction);
            migration.push_new("display_direction", display_direction);
            migration.push_new("effectiveness_status", "unavailable");
            migration.push_new("recommendation_emitted", false);
            migration.push_new("threshold_m", round_opt(Some(threshold), 3)?);
            migration.push_new(
                "first_sustained_lap",
                PyValue::from(first_migration.map(|item| item.lap)),
            );
            migration.push_new(
                "first_sustained_session_time_s",
                round_opt(first_migration.and_then(|item| item.session_time), 3)?,
            );
            migration.push_new(
                "first_sustained_tire_age_lap_equivalents",
                round_opt(
                    first_migration.map(|item| item.tire_distance / track_length_m),
                    2,
                )?,
            );
            migration.push_new(
                "method",
                "two-lap sustained displacement from the early-window median; fallback to late-window boundary when persistence is unavailable",
            );
            let mut samples: Vec<PyValue> = Vec::with_capacity(summaries.len().min(60));
            for item in summaries.iter().take(60) {
                let mut sample = Dict::with_capacity(5);
                sample.push_new("lap", PyValue::Int(item.lap));
                sample.push_new("lateral_offset_m", round_opt(Some(item.offset), 3)?);
                sample.push_new("path", path_label(Some(item.offset)));
                sample.push_new("tire_distance_m", round_opt(Some(item.tire_distance), 1)?);
                sample.push_new(
                    "tire_age_lap_equivalents",
                    round_opt(Some(item.tire_distance / track_length_m), 2)?,
                );
                samples.push(PyValue::Dict(sample));
            }
            let mut entry = Dict::with_capacity(14);
            entry.push_new("run_number", run_number);
            entry.push_new("green_laps_analyzed", PyValue::Int(summaries.len() as i64));
            entry.push_new("early_tire_window", early_window.clone());
            entry.push_new(
                "fresh_tire_window",
                if fresh_available {
                    early_window
                } else {
                    PyValue::None
                },
            );
            entry.push_new("fresh_tire_status", fresh_status);
            entry.push_new("late_tire_window", late_window);
            entry.push_new("late_tire_wear_context", wear);
            entry.push_new(
                "fresh_vs_late_available",
                fresh_available && enough_age_change,
            );
            entry.push_new(
                "comparison_basis",
                if fresh_available {
                    "fresh-recorded-distance-vs-late-on-set"
                } else {
                    "early-vs-late-on-used-set; no fresh window observed"
                },
            );
            entry.push_new(
                "lateral_delta_late_minus_early_m",
                round_opt(Some(delta), 3)?,
            );
            entry.push_new(
                "tire_age_separation_lap_equivalents",
                round_opt(Some(age_separation), 2)?,
            );
            entry.push_new("migration", migration);
            entry.push_new("lap_path_samples", PyValue::List(samples));
            zone_runs.push(PyValue::Dict(entry));
        }
        let mut output = Dict::with_capacity(6);
        output.push_new("segment", zone.segment.clone());
        output.push_new("start_pct", zone.start);
        output.push_new("end_pct", zone.end);
        output.push_new("wraps_start_finish", zone.start > zone.end);
        output.push_new("inside_outside_calibration", calibration.clone());
        output.push_new("runs", PyValue::List(zone_runs));
        output_zones.push(PyValue::Dict(output));
    }

    let calibrated_zone_count = zone_calibrations
        .iter()
        .filter(|item| matches!(item.get("status"), Some(PyValue::Str(status)) if status == "calibrated"))
        .count() as i64;
    let calibration_status = if !zone_calibrations.is_empty()
        && calibrated_zone_count == zone_calibrations.len() as i64
    {
        "calibrated"
    } else if calibrated_zone_count > 0 {
        "partially-calibrated"
    } else {
        "unavailable"
    };

    let mut channels_used: Vec<PyValue> = ["Lat", "Lon", "LapDistPct", "Lap"]
        .iter()
        .map(|name| s(name))
        .collect();
    channels_used.extend(odometer_names.iter().map(|name| s(name)));
    let mut coordinate = Dict::with_capacity(11);
    coordinate.push_new("channels", PyValue::List(channels_used));
    coordinate.push_new("lat_lon_units", "decimal degrees");
    coordinate.push_new(
        "projection",
        "local equirectangular projection about session median Lat/Lon",
    );
    coordinate.push_new("origin_lat_deg", round(lat0, 7)?);
    coordinate.push_new("origin_lon_deg", round(lon0, 7)?);
    coordinate.push_new(
        "reference",
        "driver session-median x/y path by lap-distance bin",
    );
    coordinate.push_new(
        "lateral_sign",
        "positive is left of travel relative to the reference path",
    );
    coordinate.push_new("inside_outside_sign_calibrated", calibrated_zone_count > 0);
    coordinate.push_new(
        "inside_outside_calibration_scope",
        "zone-specific signed reference-path curvature",
    );
    coordinate.push_new("absolute_groove_claimed", false);
    coordinate.push_new("absolute_low_middle_high_lane_claimed", false);

    let mut calibration = Dict::with_capacity(7);
    calibration.push_new("status", calibration_status);
    calibration.push_new("calibrated_zone_count", PyValue::Int(calibrated_zone_count));
    calibration.push_new("zone_count", PyValue::Int(zone_calibrations.len() as i64));
    calibration.push_new("track_type", ascii_track_type(args.track_type)?);
    calibration.push_new(
        "oval_track_type_confirmed",
        authoritative_oval(args.track_type)?,
    );
    calibration.push_new(
        "method",
        "zone-specific signed reference-path curvature with strict heading, sign-agreement, and coverage gates",
    );
    calibration.push_new("absolute_lane_position_available", false);

    let mut quality = Dict::with_capacity(13);
    quality.push_new("bins", PyValue::Int(bins));
    quality.push_new("bin_coverage_fraction", round(coverage, 4)?);
    quality.push_new(
        "reference_lap_count",
        PyValue::Int(reference_laps.len() as i64),
    );
    quality.push_new(
        "valid_green_lap_count",
        PyValue::Int(valid_green_laps.len() as i64),
    );
    quality.push_new(
        "lap_exclusion_rule",
        "complete green laps only; pit-time >=1 s, racing-state fraction <0.98, partial laps, and the first green lap after a caution are excluded",
    );
    quality.push_new("projected_span_m", round(span_m, 1)?);
    quality.push_new("reconstructed_path_length_m", round(track_length_m, 1)?);
    quality.push_new("input_sample_count", PyValue::Int(length as i64));
    quality.push_new("processed_sample_count", PyValue::Int(rows.len() as i64));
    quality.push_new("sample_stride", PyValue::Int(stride as i64));
    quality.push_new(
        "rejected_offset_samples",
        PyValue::Int(rejected_offset_samples),
    );
    quality.push_new("bounded", true);

    let mut summary = Dict::with_capacity(4);
    summary.push_new("zone_count", PyValue::Int(output_zones.len() as i64));
    summary.push_new("run_zone_comparison_count", PyValue::Int(comparison_count));
    summary.push_new("detected_migration_count", PyValue::Int(migration_count));
    summary.push_new(
        "inside_outside_calibrated_zone_count",
        PyValue::Int(calibrated_zone_count),
    );

    let mut performance = Dict::with_capacity(3);
    performance.push_new("status", "unavailable");
    performance.push_new("best_groove_claimed", false);
    performance.push_new(
        "reason",
        "This pass measures the driver's path evolution only. It does not establish which groove works best because traffic, clean-air state, fuel, tire age, and driver intent are not controlled here.",
    );

    let mut result = Dict::with_capacity(11);
    result.push_new("schema_version", PyValue::Int(SCHEMA_VERSION));
    result.push_new(
        "status",
        if comparison_count > 0 {
            "available"
        } else {
            "insufficient-comparable-laps"
        },
    );
    result.push_new("coordinate_evidence", coordinate);
    result.push_new("inside_outside_calibration", calibration);
    result.push_new("reference_quality", quality);
    result.push_new("summary", summary);
    result.push_new("performance_claim", performance);
    result.push_new("required_channels", required_channels());
    result.push_new("zones", PyValue::List(output_zones));
    result.push_new("limitations", limitations());
    Ok(PyValue::Dict(result))
}

/// `(min(values), max(values))` of a non-empty sequence; Python keeps the first of equal values.
fn min_max(values: impl Iterator<Item = f64>) -> (f64, f64) {
    let mut bounds: Option<(f64, f64)> = None;
    for value in values {
        bounds = Some(match bounds {
            None => (value, value),
            Some((low, high)) => (
                if value < low { value } else { low },
                if value > high { value } else { high },
            ),
        });
    }
    bounds.unwrap_or((0.0, 0.0))
}

#[cfg(test)]
mod tests {
    use super::{caution_bits, lap_fraction, percentile, zone_bin_indices};

    #[test]
    fn lap_fraction_rules() {
        assert_eq!(lap_fraction(Some(-0.0)).unwrap(), Some(0.0));
        assert_eq!(lap_fraction(Some(50.0)).unwrap(), Some(0.5));
        assert_eq!(
            lap_fraction(Some(1.04)).unwrap().map(f64::to_bits),
            Some((1.04f64 % 1.0).to_bits())
        );
        assert_eq!(lap_fraction(Some(1.2)).unwrap(), None);
        assert_eq!(lap_fraction(Some(-0.1)).unwrap(), None);
    }

    #[test]
    fn caution_bits_of_large_floats() {
        assert_eq!(caution_bits(Some(16384.0)), 0x4000);
        assert_eq!(
            caution_bits(Some(-1.0)),
            0x0008 | 0x0100 | 0x0200 | 0x4000 | 0x8000
        );
        assert_eq!(caution_bits(Some(1e300)), 0);
        assert_eq!(caution_bits(None), 0);
    }

    #[test]
    fn percentile_interpolates() {
        assert_eq!(percentile(&[1.0, 3.0], 0.75).unwrap(), Some(2.5));
        assert_eq!(percentile(&[], 0.5).unwrap(), None);
    }

    #[test]
    fn zone_bins_wrap_and_sort_from_start() {
        assert_eq!(zone_bin_indices(0.9, 0.1, 10).unwrap(), vec![9, 0]);
        assert_eq!(zone_bin_indices(0.95, 0.05, 10).unwrap(), vec![9]);
    }
}

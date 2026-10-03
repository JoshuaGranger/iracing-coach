//! The lap screen and the run-phase comparison of `_corner_tire_age_summary`:
//! `_corner_lap_exclusion_reasons`, `_run_tire_age_context`, `_phase_lap_groups`,
//! `_phase_green_age_bounds`, `_phase_summary`, `_phase_comparison`, `_corner_phase_coaching`.

use super::{
    KResult, ae_round, finite_value, float_max, float_min, get, get_or_empty, int_of, lap_sort_key,
    py_str, sorted_laps,
};
use crate::kernels::value::{Dict, PyValue};

pub const TIRES: [&str; 4] = ["LF", "RF", "LR", "RR"];

/// `str(lap.get("flag_state") or "")`.
pub fn flag_state(lap: &PyValue) -> KResult<String> {
    let value = get(lap, "flag_state")?;
    if value.truthy()? {
        py_str(value)
    } else {
        Ok(String::new())
    }
}

/// `(_finite(value) or 0.0)`.
fn finite_or_zero(value: &PyValue) -> KResult<f64> {
    Ok(match finite_value(value)? {
        Some(x) if x != 0.0 => x,
        _ => 0.0,
    })
}

/// `_corner_lap_exclusion_reasons(lap, previous_flag)`.
pub fn exclusion_reasons(lap: &PyValue, previous_flag: Option<&str>) -> KResult<Vec<String>> {
    let mut reasons: Vec<String> = Vec::new();
    let flag = flag_state(lap)?;
    if !get(lap, "complete")?.truthy()? {
        reasons.push("partial".to_owned());
    }
    if flag != "green" {
        reasons.push("caution_or_mixed".to_owned());
    }
    if finite_or_zero(get(lap, "pit_time_s")?)? >= 1.0 {
        reasons.push("pit".to_owned());
    }
    if let Some(racing) = finite_value(get(lap, "racing_state_fraction")?)?
        && racing < 0.98
    {
        reasons.push("not_racing_state".to_owned());
    }
    let clean_context = get(lap, "clean_context")?;
    if let Some(on_track) = finite_value(get_or_empty(clean_context, "on_track_fraction")?)?
        && on_track < 0.98
    {
        reasons.push("off_track".to_owned());
    }
    if let Some(traffic) = finite_value(get_or_empty(clean_context, "traffic_proximity_fraction")?)?
        && traffic >= 0.10
    {
        reasons.push("close_traffic".to_owned());
    }
    if previous_flag == Some("caution") && flag == "green" {
        reasons.push("restart".to_owned());
    }
    let damage = get(lap, "damage_repair_context")?;
    if let PyValue::Dict(damage) = damage {
        let raw = damage
            .get("exclusion_reason_codes")
            .unwrap_or(&PyValue::None);
        let raw_codes: Vec<&PyValue> = match raw {
            _ if !raw.truthy()? => Vec::new(),
            PyValue::Str(_) => vec![raw],
            PyValue::List(items) | PyValue::Tuple(items) => items.iter().collect(),
            PyValue::Dict(_) | PyValue::Set(_) => {
                return Err(crate::kernels::error::KernelError::internal(
                    "exclusion_reason_codes is a dict or set",
                ));
            }
            other => vec![other],
        };
        let mut codes: Vec<String> = Vec::new();
        for item in raw_codes {
            let text = py_str(item)?;
            if !text
                .trim_matches(|c: char| c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c))
                .is_empty()
            {
                codes.push(text);
            }
        }
        let ineligible = matches!(
            damage.get("automatic_coaching_reference_eligible"),
            Some(PyValue::Bool(false))
        );
        if ineligible || !codes.is_empty() {
            if codes.is_empty() {
                reasons.push("damage_repair_context".to_owned());
            } else {
                reasons.extend(codes);
            }
        }
    }
    Ok(reasons)
}

/// `_run_tire_age_context(run, initial_zero_evidence)`.
pub fn run_tire_age_context(run: &PyValue, initial_zero_evidence: &Dict) -> KResult<Dict> {
    let lifecycle = get(run, "tire_set_lifecycle")?;
    let corners = get_or_empty(lifecycle, "corners")?;
    let run_green = match finite_value(get(run, "green_laps")?)? {
        Some(x) if x != 0.0 => x,
        _ => 0.0,
    };
    let initial_zero_confirmed = match initial_zero_evidence.get("confirmed") {
        Some(value) => value.truthy()?,
        None => false,
    };
    let mut by_tire = Dict::new();
    let mut confirmed_all = true;
    for tire in TIRES {
        let corner = match corners {
            PyValue::Dict(dict) => dict
                .get(tire)
                .filter(|value| matches!(value, PyValue::Dict(_))),
            // A corners value that is not a mapping reads as no corner.
            _ => None,
        };
        let (set_number, end_age) = match corner {
            Some(corner) => (
                finite_value(get(corner, "session_set_number")?)?,
                finite_value(get(corner, "green_laps_on_set")?)?,
            ),
            None => (None, None),
        };
        let (Some(set_number), Some(end_age)) = (set_number, end_age) else {
            confirmed_all = false;
            continue;
        };
        let confirmed_zero = set_number >= 2.0 || (set_number == 1.0 && initial_zero_confirmed);
        confirmed_all = confirmed_all && confirmed_zero;
        let start = end_age - run_green;
        by_tire.insert(
            tire,
            Dict::new()
                .with("session_set_number", int_of(set_number)?)
                .with(
                    "start_green_laps_on_set",
                    ae_round(Some(if start > 0.0 { start } else { 0.0 }), 2)?,
                )
                .with("end_green_laps_on_set", ae_round(Some(end_age), 2)?)
                .with("zero_age_confirmed_by_prior_change", confirmed_zero),
        );
    }
    let confirmed_all = confirmed_all && by_tire.len() == TIRES.len();
    Ok(Dict::new()
        .with(
            "basis",
            if confirmed_all {
                "confirmed_tire_odometer_and_change_lifecycle"
            } else {
                "session_relative_age_with_unconfirmed_initial_or_mixed_tire_sets"
            },
        )
        .with("confirmed_zero_age_for_all_corners", confirmed_all)
        .with("by_tire", by_tire)
        .with("initial_zero_evidence", initial_zero_evidence.clone())
        .with(
            "limitation",
            if confirmed_all {
                "Green-lap age starts at a telemetry-confirmed all-corner tire change."
            } else {
                "At least one tire lacks a confirmed zero-age boundary; early/middle/late are \
                 observational run phases, not proof of fresh or worn tires."
            },
        ))
}

/// One lap's green-lap age bounds per tyre: `{"session_set_number", "start", "end"}`.
pub type AgeBounds = Dict;

/// `age_by_lap`: lap number to bounds, in insertion order (a repeated number keeps its first
/// position and takes the later bounds, as a dict assignment does).
#[derive(Default)]
pub struct AgeByLap(Vec<(i64, AgeBounds)>);

impl AgeByLap {
    fn insert(&mut self, lap: i64, bounds: AgeBounds) {
        match self.0.iter_mut().find(|(key, _)| *key == lap) {
            Some((_, slot)) => *slot = bounds,
            None => self.0.push((lap, bounds)),
        }
    }

    pub fn get(&self, lap: i64) -> Option<&AgeBounds> {
        self.0
            .iter()
            .find(|(key, _)| *key == lap)
            .map(|(_, value)| value)
    }

    /// `min(age_by_lap)`.
    pub fn first_lap(&self) -> Option<i64> {
        self.0.iter().map(|(key, _)| *key).min()
    }
}

/// `_phase_lap_groups(eligible_laps, run_laps, age_context)`: the phase model, the laps of
/// each third (early, middle, late) and the age bounds of every eligible lap.
pub fn phase_lap_groups<'a>(
    eligible_laps: &[&'a PyValue],
    run_laps: &[&'a PyValue],
    age_context: &Dict,
) -> KResult<(&'static str, [Vec<&'a PyValue>; 3], AgeByLap)> {
    let mut eligible_numbers: Vec<i64> = Vec::new();
    for lap in eligible_laps {
        if let Some(number) = finite_value(get(lap, "lap")?)? {
            eligible_numbers.push(int_of(number)?);
        }
    }
    let by_tire = age_context.get("by_tire").unwrap_or(&PyValue::None);
    let mut current: Vec<(String, f64, PyValue)> = Vec::new();
    if let PyValue::Dict(by_tire) = by_tire {
        for (tire, details) in by_tire.iter() {
            if let (PyValue::Str(tire), PyValue::Dict(details)) = (tire, details) {
                let start = match finite_value(
                    details
                        .get("start_green_laps_on_set")
                        .unwrap_or(&PyValue::None),
                )? {
                    Some(x) if x != 0.0 => x,
                    _ => 0.0,
                };
                let set_number = details
                    .get("session_set_number")
                    .cloned()
                    .unwrap_or(PyValue::None);
                current.push((tire.clone(), start, set_number));
            }
        }
    }
    let mut age_by_lap = AgeByLap::default();
    let ordered_run = sorted_refs(run_laps)?;
    for lap in ordered_run {
        let Some(number) = finite_value(get(lap, "lap")?)? else {
            continue;
        };
        let lap_number = int_of(number)?;
        let increment = match finite_value(get(lap, "green_fraction")?)? {
            Some(x) if x != 0.0 => x,
            _ => 0.0,
        };
        let increment = if increment > 0.0 { increment } else { 0.0 };
        let mut bounds = Dict::new();
        for (tire, start_age, set_number) in &mut current {
            let end_age = *start_age + increment;
            bounds.insert(
                tire.as_str(),
                Dict::new()
                    .with("session_set_number", set_number.clone())
                    .with("start", ae_round(Some(*start_age), 2)?)
                    .with("end", ae_round(Some(end_age), 2)?),
            );
            *start_age = end_age;
        }
        if eligible_numbers.contains(&lap_number) {
            age_by_lap.insert(lap_number, bounds);
        }
    }
    let ordered = sorted_refs(eligible_laps)?;
    let mut groups: [Vec<&PyValue>; 3] = [Vec::new(), Vec::new(), Vec::new()];
    let count = ordered.len().max(1);
    for (index, lap) in ordered.into_iter().enumerate() {
        groups[((index * 3) / count).min(2)].push(lap);
    }
    let confirmed = match age_context.get("confirmed_zero_age_for_all_corners") {
        Some(value) => value.truthy()?,
        None => false,
    };
    let model = if confirmed {
        "confirmed_age_run_thirds_proxy"
    } else {
        "run_thirds_proxy"
    };
    Ok((model, groups, age_by_lap))
}

/// `sorted(laps, key=...)` of lap references.
fn sorted_refs<'a>(laps: &[&'a PyValue]) -> KResult<Vec<&'a PyValue>> {
    let keys = laps
        .iter()
        .map(|lap| lap_sort_key(lap))
        .collect::<KResult<Vec<f64>>>()?;
    let order = coach_numerics::sort_permutation(laps.len(), &mut |i, j| keys[i] < keys[j], false);
    Ok(order.into_iter().map(|index| laps[index]).collect())
}

/// Every lap of `laps` sorted by the lap key (re-exported for the entries).
pub fn sorted_all(laps: &[PyValue]) -> KResult<Vec<&PyValue>> {
    sorted_laps(laps)
}

/// `_phase_green_age_bounds(lap_numbers, age_by_lap)`.
fn phase_green_age_bounds(lap_numbers: &[i64], age_by_lap: &AgeByLap) -> KResult<Dict> {
    let mut by_tire = Dict::new();
    for tire in TIRES {
        let mut starts: Vec<f64> = Vec::new();
        let mut ends: Vec<f64> = Vec::new();
        let mut set_numbers: Vec<i64> = Vec::new();
        for &lap in lap_numbers {
            let details = age_by_lap
                .get(lap)
                .and_then(|bounds| bounds.get(tire))
                .unwrap_or(&PyValue::None);
            let field = |name: &str| -> KResult<Option<f64>> {
                match details {
                    PyValue::Dict(dict) => finite_value(dict.get(name).unwrap_or(&PyValue::None)),
                    _ => Ok(None),
                }
            };
            if let Some(value) = field("start")? {
                starts.push(value);
            }
            if let Some(value) = field("end")? {
                ends.push(value);
            }
            if let Some(value) = field("session_set_number")? {
                let number = int_of(value)?;
                if !set_numbers.contains(&number) {
                    set_numbers.push(number);
                }
            }
        }
        if !starts.is_empty() || !ends.is_empty() {
            set_numbers.sort_unstable();
            by_tire.insert(
                tire,
                Dict::new()
                    .with(
                        "session_set_numbers",
                        set_numbers
                            .into_iter()
                            .map(PyValue::Int)
                            .collect::<Vec<_>>(),
                    )
                    .with("start", ae_round(float_min(&starts), 2)?)
                    .with("end", ae_round(float_max(&ends), 2)?),
            );
        }
    }
    Ok(Dict::new()
        .with(
            "unit",
            "green-lap equivalents on the session-local tire set",
        )
        .with("by_tire", by_tire))
}

pub const CORNER_PHASE_METRICS: [&str; 18] = [
    "entry_speed_mph",
    "minimum_speed_mph",
    "exit_speed_mph",
    "brake_average_fraction",
    "brake_peak_fraction",
    "brake_energy_proxy",
    "brake_onset_lap_pct",
    "brake_onset_offset_lap_pct",
    "brake_release_lap_pct",
    "brake_release_offset_lap_pct",
    "steering_average_abs_rad",
    "steering_work_proxy",
    "steering_corrections",
    "turn_in_lap_pct",
    "turn_in_offset_lap_pct",
    "throttle_pickup_lap_pct",
    "throttle_pickup_offset_lap_pct",
    "exit_throttle_fraction",
];

/// `item.get(name)` of a metric dict.
fn metric<'a>(item: &'a Dict, name: &str) -> &'a PyValue {
    item.get(name).unwrap_or(&PyValue::None)
}

/// `_phase_summary(phase, assigned_laps, lap_metrics, age_by_lap, unavailable_reason=...)`.
pub fn phase_summary(
    phase: &str,
    assigned_laps: &[&PyValue],
    lap_metrics: &[&Dict],
    age_by_lap: &AgeByLap,
    unavailable_reason: Option<String>,
) -> KResult<Dict> {
    let mut assigned_numbers = Vec::new();
    for lap in assigned_laps {
        if let Some(number) = finite_value(get(lap, "lap")?)? {
            assigned_numbers.push(int_of(number)?);
        }
    }
    let mut included_numbers = Vec::new();
    for item in lap_metrics {
        if let Some(number) = finite_value(metric(item, "lap"))? {
            included_numbers.push(int_of(number)?);
        }
    }
    let mut metrics = Dict::new();
    let mut counts = Dict::new();
    for name in CORNER_PHASE_METRICS {
        let mut values = Vec::new();
        for item in lap_metrics {
            if let Some(value) = finite_value(metric(item, name))? {
                values.push(value);
            }
        }
        let median = if values.is_empty() {
            None
        } else {
            Some(coach_numerics::median_floats(&values)?)
        };
        metrics.insert(name, ae_round(median, 5)?);
        counts.insert(name, values.len() as i64);
    }
    let count = |test: &dyn Fn(&Dict) -> KResult<bool>| -> KResult<i64> {
        let mut total = 0;
        for item in lap_metrics {
            if test(item)? {
                total += 1;
            }
        }
        Ok(total)
    };
    let present = |name: &'static str| move |item: &Dict| Ok(!metric(item, name).is_none());
    let flagged = |name: &'static str| move |item: &Dict| metric(item, name).truthy();
    let events = Dict::new()
        .with("brake_onset_laps", count(&present("brake_onset_lap_pct"))?)
        .with(
            "brake_onset_boundary_censored_laps",
            count(&flagged("brake_onset_boundary_censored"))?,
        )
        .with(
            "brake_release_laps",
            count(&present("brake_release_lap_pct"))?,
        )
        .with(
            "throttle_pickup_laps",
            count(&present("throttle_pickup_lap_pct"))?,
        )
        .with(
            "throttle_pickup_boundary_censored_laps",
            count(&flagged("throttle_pickup_boundary_censored"))?,
        )
        .with("turn_in_laps", count(&present("turn_in_lap_pct"))?)
        .with(
            "turn_in_boundary_censored_laps",
            count(&flagged("turn_in_boundary_censored"))?,
        );
    let (status, reason) = match unavailable_reason {
        Some(reason) => ("unavailable", Some(reason)),
        None if lap_metrics.len() >= 2 => ("usable", None),
        None if !lap_metrics.is_empty() => ("limited", None),
        None => (
            "unavailable",
            Some("No eligible lap supplied enough samples in this load zone.".to_owned()),
        ),
    };
    let mut sample_count = 0i64;
    for item in lap_metrics {
        let value = metric(item, "sample_count");
        sample_count += if value.truthy()? {
            super::py_int(value)?
        } else {
            0
        };
    }
    let bounds_laps = if included_numbers.is_empty() {
        &assigned_numbers
    } else {
        &included_numbers
    };
    Ok(Dict::new()
        .with("phase", phase)
        .with("status", status)
        .with("reason", reason)
        .with("assigned_lap_count", assigned_numbers.len() as i64)
        .with("lap_count", included_numbers.len() as i64)
        .with("sample_count", sample_count)
        .with(
            "lap_numbers",
            included_numbers
                .iter()
                .map(|&n| PyValue::Int(n))
                .collect::<Vec<_>>(),
        )
        .with(
            "green_lap_on_set_bounds",
            phase_green_age_bounds(bounds_laps, age_by_lap)?,
        )
        .with("event_availability", events)
        .with("metric_observation_counts", counts)
        .with("metrics", metrics))
}

/// `d.get(key) or {}` read as a dict.
fn dict_field<'a>(dict: &'a Dict, key: &str) -> &'a PyValue {
    dict.get(key).unwrap_or(&PyValue::None)
}

/// `int(_finite(counts.get(name)) or 0)`.
fn count_of(counts: &PyValue, name: &str) -> KResult<i64> {
    let value = get_or_empty(counts, name)?;
    match finite_value(value)? {
        Some(x) if x != 0.0 => int_of(x),
        _ => Ok(0),
    }
}

fn metric_status(before: i64, after: i64) -> &'static str {
    if before >= 2 && after >= 2 {
        "usable"
    } else if before >= 1 && after >= 1 {
        "limited"
    } else {
        "unavailable"
    }
}

/// `_phase_comparison(baseline, comparison)`.
pub fn phase_comparison(baseline: &Dict, comparison: &Dict) -> KResult<Dict> {
    let baseline_metrics = dict_field(baseline, "metrics");
    let comparison_metrics = dict_field(comparison, "metrics");
    let baseline_counts = dict_field(baseline, "metric_observation_counts");
    let comparison_counts = dict_field(comparison, "metric_observation_counts");
    let status_of = |dict: &Dict| {
        dict.get("status")
            .and_then(PyValue::as_str)
            .map(str::to_owned)
    };
    let (b, c) = (status_of(baseline), status_of(comparison));
    let in_set = |status: &Option<String>| matches!(status.as_deref(), Some("usable" | "limited"));
    let status = if b.as_deref() == Some("usable") && c.as_deref() == Some("usable") {
        "usable"
    } else if in_set(&b) && in_set(&c) {
        "limited"
    } else {
        "unavailable"
    };
    const ADDITIVE: [&str; 12] = [
        "entry_speed_mph",
        "minimum_speed_mph",
        "exit_speed_mph",
        "brake_average_fraction",
        "brake_peak_fraction",
        "brake_onset_offset_lap_pct",
        "brake_release_offset_lap_pct",
        "steering_average_abs_rad",
        "steering_corrections",
        "turn_in_offset_lap_pct",
        "throttle_pickup_offset_lap_pct",
        "exit_throttle_fraction",
    ];
    let mut deltas = Dict::new();
    let mut statuses = Dict::new();
    for name in ADDITIVE {
        let before = finite_value(get_or_empty(baseline_metrics, name)?)?;
        let after = finite_value(get_or_empty(comparison_metrics, name)?)?;
        let delta = match (before, after) {
            (Some(before), Some(after)) => ae_round(Some(after - before), 5)?,
            _ => PyValue::None,
        };
        deltas.insert(name, delta);
        statuses.insert(
            name,
            metric_status(
                count_of(baseline_counts, name)?,
                count_of(comparison_counts, name)?,
            ),
        );
    }
    for name in ["brake_energy_proxy", "steering_work_proxy"] {
        let before = finite_value(get_or_empty(baseline_metrics, name)?)?;
        let after = finite_value(get_or_empty(comparison_metrics, name)?)?;
        let delta = match (before, after) {
            (Some(before), Some(after)) if before != 0.0 => {
                ae_round(Some((after / before - 1.0) * 100.0), 1)?
            }
            _ => PyValue::None,
        };
        let key = format!("{name}_percent");
        deltas.insert(key.as_str(), delta);
        statuses.insert(
            key,
            metric_status(
                count_of(baseline_counts, name)?,
                count_of(comparison_counts, name)?,
            ),
        );
    }
    Ok(Dict::new()
        .with("baseline_phase", dict_field(baseline, "phase").clone())
        .with("comparison_phase", dict_field(comparison, "phase").clone())
        .with("status", status)
        .with("minimum_laps_per_phase_for_usable", 2i64)
        .with("delta_definition", "comparison phase minus baseline phase")
        .with("metric_status", statuses)
        .with("deltas", deltas))
}

/// `str(value or default)`.
fn text_or(value: Option<&PyValue>, default: &str) -> KResult<String> {
    match value {
        Some(value) if value.truthy()? => py_str(value),
        _ => Ok(default.to_owned()),
    }
}

/// `format(value, spec)`.
fn fmt(value: f64, spec: &str) -> KResult<String> {
    Ok(coach_numerics::format_float(value, spec)?)
}

/// `str.capitalize()` of ASCII text.
fn capitalize(text: &str) -> KResult<String> {
    if !text.is_ascii() {
        return Err(crate::kernels::error::KernelError::internal(
            "capitalize() of non-ASCII coaching text",
        ));
    }
    let mut out = text.to_ascii_lowercase();
    if let Some(first) = out.get_mut(0..1) {
        first.make_ascii_uppercase();
    }
    Ok(out)
}

/// `_corner_phase_coaching(comparison)`.
pub fn corner_phase_coaching(comparison: &Dict) -> KResult<Dict> {
    let status = text_or(comparison.get("status"), "unavailable")?;
    let baseline = text_or(comparison.get("baseline_phase"), "baseline")?;
    let compared = text_or(comparison.get("comparison_phase"), "comparison")?;
    if status != "usable" {
        return Ok(Dict::new()
            .with("evidence_class", "inferred")
            .with("status", "insufficient_comparison")
            .with(
                "finding",
                "Fewer than two usable laps were available in one or both phases.",
            )
            .with(
                "action",
                "Collect two clean laps in each phase before changing technique.",
            )
            .with("exact_target_emitted", false));
    }
    let deltas = dict_field(comparison, "deltas");
    let statuses = dict_field(comparison, "metric_status");
    let usable_delta = |name: &str| -> KResult<Option<f64>> {
        let status = get_or_empty(statuses, name)?;
        if status.as_str() == Some("usable") {
            finite_value(get_or_empty(deltas, name)?)
        } else {
            Ok(None)
        }
    };
    let entry = usable_delta("entry_speed_mph")?;
    let minimum = usable_delta("minimum_speed_mph")?;
    let exit_speed = usable_delta("exit_speed_mph")?;
    let brake_energy = usable_delta("brake_energy_proxy_percent")?;
    let steer_work = usable_delta("steering_work_proxy_percent")?;
    let corrections = usable_delta("steering_corrections")?;
    let turn_in = usable_delta("turn_in_offset_lap_pct")?;
    let throttle = usable_delta("throttle_pickup_offset_lap_pct")?;
    let release = usable_delta("brake_release_offset_lap_pct")?;

    let mut evidence: Vec<String> = Vec::new();
    let mut actions: Vec<&str> = Vec::new();
    if let (Some(entry), Some(steer)) = (entry, steer_work)
        && entry > 1.0
        && steer > 10.0
    {
        evidence.push(format!(
            "entry speed rose {} mph and steering work rose {}%",
            fmt(entry, ".1f")?,
            fmt(steer, ".0f")?
        ));
        actions.push("reduce combined entry speed and steering load late in the run");
    }
    if let (Some(brake), Some(minimum)) = (brake_energy, minimum)
        && brake > 10.0
        && minimum < -1.0
    {
        evidence.push(format!(
            "brake-energy proxy rose {}% while minimum speed fell {} mph",
            fmt(brake, ".0f")?,
            fmt(minimum.abs(), ".1f")?
        ));
        actions.push("brake smoothly and finish releasing before adding steering");
    }
    if let Some(release) = release
        && release > 0.005
    {
        evidence.push(format!(
            "brake release moved {}% of a lap later",
            fmt(release * 100.0, ".1f")?
        ));
        actions.push("release the brake earlier in the loaded phase");
    }
    if let (Some(turn_in), Some(steer)) = (turn_in, steer_work)
        && turn_in < -0.005
        && steer > 10.0
    {
        evidence.push(format!(
            "turn-in moved {}% of a lap earlier while steering work rose {}%",
            fmt(turn_in.abs() * 100.0, ".1f")?,
            fmt(steer, ".0f")?
        ));
        actions.push("turn in later, then build steering angle once");
    }
    if let Some(corrections) = corrections
        && corrections >= 1.0
    {
        evidence.push(format!(
            "median steering corrections increased by {}",
            fmt(corrections, ".0f")?
        ));
        actions.push("use one clean steering arc and remove the extra correction");
    }
    if let (Some(throttle), Some(exit_speed)) = (throttle, exit_speed)
        && throttle > 0.005
        && exit_speed < -1.0
    {
        evidence.push(format!(
            "75% throttle pickup moved {}% of a lap later and exit speed fell {} mph",
            fmt(throttle * 100.0, ".1f")?,
            fmt(exit_speed.abs(), ".1f")?
        ));
        actions.push("prioritize rotation and clean throttle pickup before more exit commitment");
    }
    let (finding, action) = if evidence.is_empty() {
        (
            format!(
                "No strong {baseline}-to-{compared} control shift crossed the deterministic \
                 coaching thresholds."
            ),
            "Repeat the same line; use an aligned lap before exact targets.".to_owned(),
        )
    } else {
        let mut unique: Vec<&str> = Vec::new();
        for action in actions {
            if !unique.contains(&action) {
                unique.push(action);
            }
        }
        (
            capitalize(&evidence.join("; "))? + ".",
            capitalize(&unique.join("; "))? + ".",
        )
    };
    Ok(Dict::new()
        .with("evidence_class", "inferred_from_derived_phase_comparison")
        .with("status", "usable")
        .with("finding", finding)
        .with("action", action)
        .with("exact_target_emitted", false)
        .with(
            "target_limitation",
            "Exact corner targets require a valid aligned representative-lap comparison.",
        ))
}

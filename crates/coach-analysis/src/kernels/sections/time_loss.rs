//! `analysis_engine._pace_attribution` with `time_loss.analyze_time_loss` and
//! `TimeLossReport.to_payload` (the report never leaves the entry as a dataclass).

use super::lap_reference::{
    LapTrace, STATUS_UNAVAILABLE, STATUS_USABLE, boundary_index, build_traces, lap_groups,
    segment_elapsed, segment_extreme_speed, uniform_segments,
};
use super::phases::{exclusion_reasons, flag_state};
use super::tire_energy::{self, EnergyInputs, segment_energy};
use super::{
    KResult, Seq, ae_round, finite_value, float_max, float_min, get, get_or_empty, int_of, items,
    lr_finite_value, py_str, round_float, round_opt, sorted_laps,
};
use crate::kernels::dispatch::Call;
use crate::kernels::error::KernelError;
use crate::kernels::session::Session;
use crate::kernels::table::Table;
use crate::kernels::value::{Dict, PyValue};

pub const TIME_LOSS_VERSION: i64 = 1;
pub const MINIMUM_LAPS_FOR_RANKING: usize = 3;
pub const NEAR_BEST_MARGIN_S: f64 = 0.05;
pub const STATUS_LIMITED: &str = "limited";
pub const WRAP_REASON: &str = "segment_wraps_start_finish";

/// `time_loss.SegmentLoss`.
#[derive(Clone, Debug)]
pub struct SegmentLoss {
    pub name: String,
    pub start_pct: f64,
    pub end_pct: f64,
    pub status: &'static str,
    pub reason: Option<&'static str>,
    pub lap_count: i64,
    pub best_s: Option<f64>,
    pub median_s: Option<f64>,
    pub worst_s: Option<f64>,
    pub recoverable_s: Option<f64>,
    pub best_lap: PyValue,
    pub near_best_lap_count: i64,
    pub minimum_speed_on_best_mps: Option<f64>,
    pub median_minimum_speed_mps: Option<f64>,
}

impl SegmentLoss {
    fn sort_key(&self) -> f64 {
        self.recoverable_s.unwrap_or(-1.0)
    }

    fn to_payload(&self) -> KResult<PyValue> {
        Ok(PyValue::Dict(
            Dict::new()
                .with("name", self.name.as_str())
                .with("start_pct", round_float(self.start_pct, 6)?)
                .with("end_pct", round_float(self.end_pct, 6)?)
                .with("status", self.status)
                .with("reason", self.reason)
                .with("lap_count", self.lap_count)
                .with("best_s", round_opt(self.best_s, 4)?)
                .with("median_s", round_opt(self.median_s, 4)?)
                .with("worst_s", round_opt(self.worst_s, 4)?)
                .with("recoverable_s", round_opt(self.recoverable_s, 4)?)
                .with("best_lap", self.best_lap.clone())
                .with("near_best_lap_count", self.near_best_lap_count)
                .with(
                    "minimum_speed_on_best_mps",
                    round_opt(self.minimum_speed_on_best_mps, 3)?,
                )
                .with(
                    "median_minimum_speed_mps",
                    round_opt(self.median_minimum_speed_mps, 3)?,
                ),
        ))
    }
}

/// `time_loss.TimeLossReport`.
#[derive(Clone, Debug)]
pub struct TimeLossReport {
    pub segments: Vec<SegmentLoss>,
    pub status: &'static str,
    pub total_recoverable_s: Option<f64>,
    pub ranked_names: Vec<String>,
    pub usable_lap_count: i64,
    pub excluded_segment_count: i64,
}

impl TimeLossReport {
    pub fn to_payload(&self) -> KResult<PyValue> {
        let segments = self
            .segments
            .iter()
            .map(SegmentLoss::to_payload)
            .collect::<KResult<Vec<_>>>()?;
        Ok(PyValue::Dict(
            Dict::new()
                .with("contract_version", TIME_LOSS_VERSION)
                .with("status", self.status)
                .with("usable_lap_count", self.usable_lap_count)
                .with("excluded_segment_count", self.excluded_segment_count)
                .with(
                    "total_recoverable_s",
                    round_opt(self.total_recoverable_s, 4)?,
                )
                .with(
                    "ranked_names",
                    self.ranked_names
                        .iter()
                        .map(|name| PyValue::from(name.as_str()))
                        .collect::<Vec<_>>(),
                )
                .with("segments", segments)
                .with(
                    "classification",
                    "recoverable time is this driver's own median minus his own best in each \
                     segment; the total is a ceiling composed from different laps, not an \
                     achievable single-lap time",
                )
                .with("minimum_laps_for_ranking", MINIMUM_LAPS_FOR_RANKING as i64),
        ))
    }
}

/// `_segment_name(segment, ordinal)`.
pub fn segment_name(segment: &PyValue, ordinal: usize) -> KResult<String> {
    for key in ["name", "label"] {
        if let Some(value) = segment.get(key)
            && value.truthy()?
        {
            return py_str(value);
        }
    }
    match segment.get("segment") {
        Some(number) if !number.is_none() => Ok(format!("Corner {}", py_str(number)?)),
        _ => Ok(format!("Segment {ordinal}")),
    }
}

fn unavailable(
    name: String,
    start_pct: f64,
    end_pct: f64,
    reason: &'static str,
    lap_count: i64,
) -> SegmentLoss {
    SegmentLoss {
        name,
        start_pct,
        end_pct,
        status: STATUS_UNAVAILABLE,
        reason: Some(reason),
        lap_count,
        best_s: None,
        median_s: None,
        worst_s: None,
        recoverable_s: None,
        best_lap: PyValue::None,
        near_best_lap_count: 0,
        minimum_speed_on_best_mps: None,
        median_minimum_speed_mps: None,
    }
}

/// `x or 0.0` for an optional float: `None`, `0.0` and `-0.0` become `0.0` (NaN is truthy).
pub fn or_zero(value: Option<f64>) -> f64 {
    match value {
        Some(x) if x != 0.0 => x,
        _ => 0.0,
    }
}

/// `_collect(traces, start_pct, end_pct)`: (elapsed, lap number, minimum speed) per covering lap.
fn collect<'t>(
    traces: &[&'t LapTrace],
    start_pct: f64,
    end_pct: f64,
) -> KResult<Vec<(f64, &'t PyValue, Option<f64>)>> {
    let mut collected = Vec::new();
    for trace in traces {
        if trace.status == STATUS_UNAVAILABLE {
            continue;
        }
        let start = boundary_index(trace.bins, start_pct)?;
        let end = boundary_index(trace.bins, end_pct)?;
        let Some(elapsed) = segment_elapsed(&trace.session_time_s, start, end)? else {
            continue;
        };
        if elapsed <= 0.0 {
            continue;
        }
        collected.push((
            elapsed,
            &trace.lap_number,
            segment_extreme_speed(&trace.speed_mps, start, end),
        ));
    }
    Ok(collected)
}

/// `analyze_time_loss(traces, segments)` with the default `minimum_laps`.
pub fn analyze_time_loss(traces: &[LapTrace], segments: &[PyValue]) -> KResult<TimeLossReport> {
    let usable_traces: Vec<&LapTrace> = traces
        .iter()
        .filter(|trace| trace.status != STATUS_UNAVAILABLE)
        .collect();
    let mut results: Vec<SegmentLoss> = Vec::with_capacity(segments.len());
    let mut excluded = 0;
    for (index, segment) in segments.iter().enumerate() {
        let name = segment_name(segment, index + 1)?;
        let start_pct = lr_finite_value(segment.get("start_pct").unwrap_or(&PyValue::None))?;
        let end_pct = lr_finite_value(segment.get("end_pct").unwrap_or(&PyValue::None))?;
        let (Some(start_pct), Some(end_pct)) = (start_pct, end_pct) else {
            excluded += 1;
            results.push(unavailable(
                name,
                or_zero(start_pct),
                or_zero(end_pct),
                "segment_bounds_missing",
                0,
            ));
            continue;
        };
        let wraps = match segment.get("wraps_start_finish") {
            Some(value) => value.truthy()?,
            None => false,
        };
        if wraps || end_pct <= start_pct {
            excluded += 1;
            results.push(unavailable(name, start_pct, end_pct, WRAP_REASON, 0));
            continue;
        }
        let collected = collect(&usable_traces, start_pct, end_pct)?;
        if collected.len() < MINIMUM_LAPS_FOR_RANKING {
            excluded += 1;
            results.push(unavailable(
                name,
                start_pct,
                end_pct,
                "insufficient_covering_laps",
                collected.len() as i64,
            ));
            continue;
        }
        let times: Vec<f64> = collected.iter().map(|item| item.0).collect();
        let best_value = float_min(&times).unwrap_or(f64::NAN);
        let best_index =
            coach_numerics::min_index_by(collected.len(), |i, j| collected[i].0 < collected[j].0)
                .unwrap_or(0);
        let best_entry = &collected[best_index];
        let median_value = coach_numerics::median_floats(&times)?;
        let speeds: Vec<f64> = collected.iter().filter_map(|item| item.2).collect();
        let difference = median_value - best_value;
        results.push(SegmentLoss {
            name,
            start_pct,
            end_pct,
            status: STATUS_USABLE,
            reason: None,
            lap_count: collected.len() as i64,
            best_s: Some(best_value),
            median_s: Some(median_value),
            worst_s: float_max(&times),
            recoverable_s: Some(if difference > 0.0 { difference } else { 0.0 }),
            best_lap: best_entry.1.clone(),
            near_best_lap_count: times
                .iter()
                .filter(|&&value| value - best_value <= NEAR_BEST_MARGIN_S)
                .count() as i64,
            minimum_speed_on_best_mps: best_entry.2,
            median_minimum_speed_mps: if speeds.is_empty() {
                None
            } else {
                Some(coach_numerics::median_floats(&speeds)?)
            },
        });
    }

    // sorted(key=(status != usable, -sort_key, name)): tuple order, first unequal element.
    let keys: Vec<(bool, f64, &str)> = results
        .iter()
        .map(|item| {
            (
                item.status != STATUS_USABLE,
                -item.sort_key(),
                item.name.as_str(),
            )
        })
        .collect();
    let order = coach_numerics::sort_permutation(
        results.len(),
        &mut |i, j| {
            let (a, b) = (&keys[i], &keys[j]);
            if a.0 != b.0 {
                return !a.0 && b.0;
            }
            if a.1 != b.1 {
                return a.1 < b.1;
            }
            a.2 < b.2
        },
        false,
    );
    let ranked: Vec<SegmentLoss> = order.iter().map(|&index| results[index].clone()).collect();
    let usable: Vec<&SegmentLoss> = ranked
        .iter()
        .filter(|item| item.status == STATUS_USABLE)
        .collect();
    let total = if usable.is_empty() {
        None
    } else {
        let items: Vec<f64> = usable
            .iter()
            .map(|item| or_zero(item.recoverable_s))
            .collect();
        Some(coach_numerics::sum_floats_from_int_zero(&items).to_f64())
    };
    let status = if usable.is_empty() {
        STATUS_UNAVAILABLE
    } else if excluded > 0 {
        STATUS_LIMITED
    } else {
        STATUS_USABLE
    };
    let ranked_names = usable.iter().map(|item| item.name.clone()).collect();
    Ok(TimeLossReport {
        status,
        total_recoverable_s: total,
        ranked_names,
        usable_lap_count: usable_traces.len() as i64,
        excluded_segment_count: excluded,
        segments: ranked,
    })
}

pub const PACE_ATTRIBUTION_SCHEMA_VERSION: i64 = 1;
pub const FALLBACK_SEGMENT_COUNT: i64 = 12;

fn unavailable_attribution(reason: &str) -> PyValue {
    PyValue::Dict(
        Dict::new()
            .with("schema_version", PACE_ATTRIBUTION_SCHEMA_VERSION)
            .with("status", "unavailable")
            .with("priorities", Vec::<PyValue>::new())
            .with("eligible_lap_numbers", Vec::<PyValue>::new())
            .with("reason", reason),
    )
}

/// `_pace_attribution(table, laps, track_profile)`.
pub fn pace_attribution(
    table: &Table,
    laps: &[PyValue],
    track_profile: &PyValue,
) -> KResult<PyValue> {
    if !table.has(&["LapDistPct"]) || !table.has(&["SessionTime"]) || !table.has(&["Lap"]) {
        return Ok(unavailable_attribution(
            "distance_time_or_lap_channel_missing",
        ));
    }
    let mut eligible: Vec<i64> = Vec::new();
    let mut previous: Option<String> = None;
    for lap in sorted_laps(laps)? {
        let number = finite_value(get(lap, "lap")?)?;
        let reasons = exclusion_reasons(lap, previous.as_deref())?;
        previous = Some(flag_state(lap)?);
        if let Some(number) = number
            && reasons.is_empty()
            && number >= 0.0
        {
            let number = int_of(number)?;
            if !eligible.contains(&number) {
                eligible.push(number);
            }
        }
    }
    if eligible.is_empty() {
        return Ok(unavailable_attribution("no_clean_green_racing_laps"));
    }
    let lap_channel = table.get(&["Lap"], PyValue::None);
    let groups = lap_groups(&lap_channel, &eligible)?;
    let distance = table.get(&["LapDistPct"], PyValue::None);
    let times = table.get(&["SessionTime", "SessionTimeOfDay"], PyValue::None);
    let speed = table.get(&["Speed"], PyValue::None);
    let traces = build_traces(&groups, &distance, &times, &speed)?;

    let listed = get_or_empty(track_profile, "detected_corner_segments")?;
    let detected: Vec<PyValue> = if listed.truthy()? {
        items(listed, "detected_corner_segments")?
            .iter()
            .filter(|segment| matches!(segment, PyValue::Dict(_)))
            .cloned()
            .collect()
    } else {
        Vec::new()
    };
    let (segments, source) = if detected.is_empty() {
        (uniform_segments(FALLBACK_SEGMENT_COUNT), "uniform_fallback")
    } else {
        (detected, "detected_corner_segments")
    };
    let loss = analyze_time_loss(&traces, &segments)?;

    let mut missing = Vec::new();
    for name in ["Speed", "LatAccel", "LongAccel"] {
        if !table.has(&[name]) {
            missing.push(name.to_owned());
        }
    }
    let mut energy_indices: Vec<usize> = groups
        .iter()
        .flat_map(|(_, indices)| indices.iter().copied())
        .collect();
    energy_indices.sort_unstable();
    let lat = table.get(&["LatAccel"], PyValue::None);
    let long = table.get(&["LongAccel"], PyValue::None);
    let velocity_x = table
        .has(&["VelocityX"])
        .then(|| table.get(&["VelocityX"], PyValue::None));
    let velocity_y = table
        .has(&["VelocityY"])
        .then(|| table.get(&["VelocityY"], PyValue::None));
    let sample_rate = match finite_value(table.metadata("sample_rate").unwrap_or(&PyValue::None))? {
        Some(rate) if rate != 0.0 => rate,
        _ => 60.0,
    };
    let energy = segment_energy(&EnergyInputs {
        lap_dist_pct: &distance,
        session_time_s: &times,
        speed_m_s: &speed,
        lat_accel_m_s2: &lat,
        long_accel_m_s2: &long,
        segments: &segments,
        indices: &energy_indices,
        velocity_x_m_s: velocity_x.as_ref().map(|raw| raw as &dyn Seq),
        velocity_y_m_s: velocity_y.as_ref().map(|raw| raw as &dyn Seq),
        sample_rate_hz: sample_rate,
        missing_channels: missing,
    })?;

    // {segment.name: segment for segment in energy.segments}: the last of equal names wins.
    let matched_energy = |name: &str| energy.segments.iter().rev().find(|item| item.name == name);
    let mut priorities = Vec::new();
    for segment in &loss.segments {
        if segment.status != STATUS_USABLE {
            continue;
        }
        let matched = matched_energy(&segment.name);
        let usable_energy = matched.filter(|item| item.status == tire_energy::STATUS_USABLE);
        priorities.push(PyValue::Dict(
            Dict::new()
                .with("name", segment.name.as_str())
                .with("start_pct", round_float(segment.start_pct, 6)?)
                .with("end_pct", round_float(segment.end_pct, 6)?)
                .with("recoverable_s", ae_round(segment.recoverable_s, 3)?)
                .with("best_s", ae_round(segment.best_s, 3)?)
                .with("median_s", ae_round(segment.median_s, 3)?)
                .with("best_lap", segment.best_lap.clone())
                .with("lap_count", segment.lap_count)
                .with("near_best_lap_count", segment.near_best_lap_count)
                .with(
                    "tire_energy_share",
                    match usable_energy {
                        Some(item) => ae_round(item.share_of_lap, 4)?,
                        None => PyValue::None,
                    },
                )
                .with("tire_energy_grade", usable_energy.map(|item| item.grade))
                .with(
                    "peak_lateral_g",
                    match matched {
                        Some(item) => ae_round(item.peak_lateral_g, 3)?,
                        None => PyValue::None,
                    },
                ),
        ));
    }

    let mut limitations: Vec<PyValue> = vec![
        PyValue::from(
            "Recoverable time is this driver's own median minus his own best in each segment. \
             It is proven achievable in isolation; the total is a ceiling composed from \
             different laps, not a lap time.",
        ),
        PyValue::from(
            "Tire energy is a specific-energy proxy from vehicle acceleration and speed, not a \
             wear measurement.",
        ),
    ];
    if source == "uniform_fallback" {
        limitations.push(PyValue::from(
            "No load zones were detected, so segments are even distance slices and do not \
             correspond to named corners.",
        ));
    }
    if loss.excluded_segment_count != 0 {
        limitations.push(PyValue::from(format!(
            "{} segment(s) were excluded from ranking; each carries its own reason.",
            loss.excluded_segment_count
        )));
    }
    eligible.sort_unstable();
    Ok(PyValue::Dict(
        Dict::new()
            .with("schema_version", PACE_ATTRIBUTION_SCHEMA_VERSION)
            .with("status", loss.status)
            .with("reason", PyValue::None)
            .with("segment_source", source)
            .with(
                "eligible_lap_numbers",
                eligible.into_iter().map(PyValue::Int).collect::<Vec<_>>(),
            )
            .with("eligible_lap_count", traces.len() as i64)
            .with("priorities", priorities)
            .with(
                "total_recoverable_s",
                ae_round(loss.total_recoverable_s, 3)?,
            )
            .with("time_loss", loss.to_payload()?)
            .with("tire_energy", energy.to_payload()?)
            .with("limitations", limitations),
    ))
}

/// The native `analysis_engine._pace_attribution(table, laps, track_profile)`.
pub fn pace_attribution_entry(session: &Session, call: &mut Call) -> Result<PyValue, KernelError> {
    const ENTRY: &str = "analysis_engine._pace_attribution";
    let bound = call.bind(ENTRY, &["table", "laps", "track_profile"], &[])?;
    bound.require_table(call, "table")?;
    let laps = items(bound.require(call, "laps")?, "laps")?;
    let track_profile = bound.require(call, "track_profile")?;
    let table = Table::of(session)?;
    let result = pace_attribution(&table, laps, track_profile)?;
    table.check()?;
    Ok(result)
}

//! `lap_reference.build_lap_trace` and the helpers `time_loss` and `pit_loss` share
//! (`_boundary_index`, `_segment_elapsed`, `_segment_extreme_speed`, `uniform_segments`).
//!
//! A trace's grid times are interpolated exactly as the oracle writes them:
//! `left_time + (right_time - left_time) * weight`, `weight = (target - left_pct) / span`,
//! `target = grid_index / bins` (an exact int division, both operands below 2**53).

use super::phases::{exclusion_reasons, flag_state};
use super::{KResult, Seq, finite, finite_value, get, items, lr_finite, py_int, sorted_laps};
use crate::kernels::dispatch::Call;
use crate::kernels::error::KernelError;
use crate::kernels::session::Session;
use crate::kernels::table::Table;
use crate::kernels::value::{Dataclass, Dict, PyValue};

pub const DEFAULT_BINS: i64 = 1000;
pub const DEFAULT_MAX_GAP_PCT: f64 = 0.02;
pub const USABLE_COVERAGE: f64 = 0.95;
pub const MINIMUM_COVERAGE: f64 = 0.50;
pub const STATUS_USABLE: &str = "usable";
pub const STATUS_LIMITED: &str = "limited";
pub const STATUS_UNAVAILABLE: &str = "unavailable";
pub const CLASS: &str = "lap_reference.LapTrace";

fn classify(coverage: f64) -> &'static str {
    if coverage >= USABLE_COVERAGE {
        STATUS_USABLE
    } else if coverage >= MINIMUM_COVERAGE {
        STATUS_LIMITED
    } else {
        STATUS_UNAVAILABLE
    }
}

/// `lap_reference.LapTrace`, fields in declared order.
#[derive(Clone, Debug)]
pub struct LapTrace {
    pub lap_number: PyValue,
    pub bins: i64,
    pub session_time_s: Vec<Option<f64>>,
    pub speed_mps: Vec<Option<f64>>,
    pub coverage: f64,
    pub status: &'static str,
    pub covered_time_s: Option<f64>,
    pub sample_count: i64,
    pub regressed_sample_count: i64,
    pub gap_count: i64,
}

fn optional_tuple(values: &[Option<f64>]) -> PyValue {
    PyValue::Tuple(values.iter().map(|&value| PyValue::from(value)).collect())
}

impl LapTrace {
    pub fn to_value(&self) -> PyValue {
        PyValue::Dataclass(Box::new(Dataclass {
            class: CLASS.to_owned(),
            fields: vec![
                ("lap_number".to_owned(), self.lap_number.clone()),
                ("bins".to_owned(), PyValue::Int(self.bins)),
                (
                    "session_time_s".to_owned(),
                    optional_tuple(&self.session_time_s),
                ),
                ("speed_mps".to_owned(), optional_tuple(&self.speed_mps)),
                ("coverage".to_owned(), PyValue::Float(self.coverage)),
                ("status".to_owned(), PyValue::from(self.status)),
                (
                    "covered_time_s".to_owned(),
                    PyValue::from(self.covered_time_s),
                ),
                ("sample_count".to_owned(), PyValue::Int(self.sample_count)),
                (
                    "regressed_sample_count".to_owned(),
                    PyValue::Int(self.regressed_sample_count),
                ),
                ("gap_count".to_owned(), PyValue::Int(self.gap_count)),
            ],
        }))
    }

    /// `trace.covers(index)`.
    pub fn covers(&self, index: i64) -> bool {
        covers(&self.session_time_s, index)
    }
}

/// `0 <= index < len(session_time_s) and session_time_s[index] is not None`.
pub fn covers(times: &[Option<f64>], index: i64) -> bool {
    usize::try_from(index)
        .ok()
        .and_then(|index| times.get(index))
        .is_some_and(Option::is_some)
}

/// `_boundary_index(bins, pct)`: `max(0, min(bins, int(round(pct * bins))))`.
pub fn boundary_index(bins: i64, pct: f64) -> KResult<i64> {
    let scaled = coach_numerics::round_to_int(pct * bins as f64)?;
    let capped = if scaled < bins { scaled } else { bins };
    Ok(if 0 > capped { 0 } else { capped })
}

/// `tuple[index]` for an index the oracle computed (`IndexError` past either end).
fn tuple_at(values: &[Option<f64>], index: i64) -> KResult<Option<f64>> {
    let length = values.len() as i64;
    let position = if index < 0 { index + length } else { index };
    if position < 0 || position >= length {
        return Err(KernelError::raises(
            "IndexError",
            "tuple index out of range",
        ));
    }
    Ok(values[position as usize])
}

/// `_segment_elapsed(trace, start, end)`.
pub fn segment_elapsed(times: &[Option<f64>], start: i64, end: i64) -> KResult<Option<f64>> {
    let first = tuple_at(times, start)?;
    let last = tuple_at(times, end)?;
    Ok(match (first, last) {
        (Some(first), Some(last)) => Some(last - first),
        _ => None,
    })
}

/// `_segment_extreme_speed(trace, start, end)`: `min` of the present speeds in
/// `speed_mps[start : end + 1]` (the first of equal values).
pub fn segment_extreme_speed(speeds: &[Option<f64>], start: i64, end: i64) -> Option<f64> {
    let (lower, upper) = python_slice(speeds.len(), start, end + 1);
    let mut best: Option<f64> = None;
    for value in speeds[lower..upper].iter().flatten() {
        if best.is_none_or(|current| *value < current) {
            best = Some(*value);
        }
    }
    best
}

/// The bounds of `seq[start:stop]` (step 1) on a sequence of `length` items.
fn python_slice(length: usize, start: i64, stop: i64) -> (usize, usize) {
    let length = length as i64;
    let clamp = |index: i64| {
        if index < 0 {
            (index + length).max(0)
        } else {
            index.min(length)
        }
    };
    let (lower, upper) = (clamp(start), clamp(stop));
    (lower as usize, upper.max(lower) as usize)
}

/// `lap_reference.uniform_segments(count)` (prefix `S`).
pub fn uniform_segments(count: i64) -> Vec<PyValue> {
    (0..count)
        .map(|index| {
            PyValue::Dict(
                Dict::new()
                    .with("name", format!("S{}", index + 1))
                    .with("start_pct", index as f64 / count as f64)
                    .with("end_pct", (index + 1) as f64 / count as f64),
            )
        })
        .collect()
}

/// `(pct, time, speed)` of the samples that advance around the lap.
type Pairs = Vec<(f64, f64, Option<f64>)>;

/// `_forward_progress_pairs`: samples that advance around the lap, and the count dropped.
fn forward_progress_pairs(
    distance: &dyn Seq,
    times: &dyn Seq,
    speed: Option<&dyn Seq>,
    indices: &[usize],
) -> KResult<(Pairs, i64)> {
    let mut pairs = Vec::with_capacity(indices.len());
    let mut regressed = 0;
    let mut last_pct: Option<f64> = None;
    let mut last_time: Option<f64> = None;
    for &index in indices {
        let pct = if index < distance.len() {
            lr_finite(distance.cell(index))?
        } else {
            None
        };
        let time = if index < times.len() {
            lr_finite(times.cell(index))?
        } else {
            None
        };
        let (Some(pct), Some(time)) = (pct, time) else {
            continue;
        };
        if !(0.0..=1.0).contains(&pct) {
            continue;
        }
        if last_pct.is_some_and(|last| pct <= last) || last_time.is_some_and(|last| time <= last) {
            regressed += 1;
            continue;
        }
        let speed = match speed {
            Some(column) if index < column.len() => lr_finite(column.cell(index))?,
            _ => None,
        };
        pairs.push((pct, time, speed));
        last_pct = Some(pct);
        last_time = Some(time);
    }
    Ok((pairs, regressed))
}

/// `build_lap_trace(distance_pct, session_time_s, indices, speed_mps=..., lap_number=...)` with
/// the default `bins` and `max_gap_pct`.
pub fn build_lap_trace(
    distance: &dyn Seq,
    times: &dyn Seq,
    indices: &[usize],
    speed: Option<&dyn Seq>,
    lap_number: PyValue,
) -> KResult<LapTrace> {
    let bins = DEFAULT_BINS;
    let max_gap = DEFAULT_MAX_GAP_PCT;
    let (pairs, regressed) = forward_progress_pairs(distance, times, speed, indices)?;
    let points = (bins + 1) as usize;
    let mut grid_times: Vec<Option<f64>> = vec![None; points];
    let mut grid_speeds: Vec<Option<f64>> = vec![None; points];
    let mut gap_count = 0;
    if pairs.len() >= 2 {
        let mut cursor = 0;
        for grid_index in 0..points {
            let target = grid_index as f64 / bins as f64;
            while cursor + 1 < pairs.len() && pairs[cursor + 1].0 < target {
                cursor += 1;
            }
            if cursor + 1 >= pairs.len() {
                break;
            }
            let (left_pct, left_time, left_speed) = pairs[cursor];
            let (right_pct, right_time, right_speed) = pairs[cursor + 1];
            if target < left_pct || target > right_pct {
                continue;
            }
            let span = right_pct - left_pct;
            if span > max_gap {
                continue;
            }
            let weight = if span <= 0.0 {
                0.0
            } else {
                (target - left_pct) / span
            };
            grid_times[grid_index] = Some(left_time + (right_time - left_time) * weight);
            match (left_speed, right_speed) {
                (Some(left), Some(right)) => {
                    grid_speeds[grid_index] = Some(left + (right - left) * weight);
                }
                (Some(left), None) if weight <= 0.0 => grid_speeds[grid_index] = Some(left),
                _ => {}
            }
        }
        gap_count = pairs
            .windows(2)
            .filter(|pair| pair[1].0 - pair[0].0 > max_gap)
            .count() as i64;
    }
    let covered: Vec<f64> = grid_times.iter().flatten().copied().collect();
    let coverage = covered.len() as f64 / points as f64;
    let covered_time = if covered.len() >= 2 {
        let mut low = covered[0];
        let mut high = covered[0];
        for &value in &covered[1..] {
            if value > high {
                high = value;
            }
            if value < low {
                low = value;
            }
        }
        Some(high - low)
    } else {
        None
    };
    Ok(LapTrace {
        lap_number,
        bins,
        session_time_s: grid_times,
        speed_mps: grid_speeds,
        coverage,
        status: classify(coverage),
        covered_time_s: covered_time,
        sample_count: pairs.len() as i64,
        regressed_sample_count: regressed,
        gap_count,
    })
}

/// A `LapTrace` argument as `pit_loss` reads it: `lap_number`, `bins` and the grid times.
pub struct ReferenceTrace {
    pub lap_number: PyValue,
    pub bins: i64,
    pub session_time_s: Vec<Option<f64>>,
}

impl ReferenceTrace {
    pub fn from_value(value: &PyValue) -> KResult<ReferenceTrace> {
        let reject = |what: &str| KernelError::internal(format!("reference LapTrace: {what}"));
        let dataclass = value
            .as_dataclass()
            .filter(|dataclass| dataclass.class == CLASS)
            .ok_or_else(|| reject("not a lap_reference.LapTrace"))?;
        let bins = dataclass
            .field("bins")
            .and_then(PyValue::as_int)
            .ok_or_else(|| reject("bins is not an int"))?;
        let Some(PyValue::Tuple(items)) = dataclass.field("session_time_s") else {
            return Err(reject("session_time_s is not a tuple"));
        };
        let session_time_s = items
            .iter()
            .map(|item| match item {
                PyValue::None => Ok(None),
                PyValue::Float(x) => Ok(Some(*x)),
                _ => Err(reject(
                    "session_time_s holds a value that is not a float or None",
                )),
            })
            .collect::<KResult<Vec<_>>>()?;
        let lap_number = dataclass
            .field("lap_number")
            .cloned()
            .ok_or_else(|| reject("no lap_number"))?;
        Ok(ReferenceTrace {
            lap_number,
            bins,
            session_time_s,
        })
    }
}

/// `groups[int(value)].append(index)` over the `Lap` column for the eligible lap numbers:
/// `(lap, sample indices)` in first-occurrence order (`defaultdict(list)`).
pub fn lap_groups(lap_channel: &dyn Seq, eligible: &[i64]) -> KResult<Vec<(i64, Vec<usize>)>> {
    let mut groups: Vec<(i64, Vec<usize>)> = Vec::new();
    for index in 0..lap_channel.len() {
        let Some(value) = finite(lap_channel.cell(index))? else {
            continue;
        };
        // int(value) beyond i64 is a Python int no eligible number equals.
        let Ok(number) = coach_numerics::int_from_float(value) else {
            continue;
        };
        if !eligible.contains(&number) {
            continue;
        }
        match groups.iter_mut().find(|(lap, _)| *lap == number) {
            Some((_, indices)) => indices.push(index),
            None => groups.push((number, vec![index])),
        }
    }
    Ok(groups)
}

/// `[build_lap_trace(distance, times, indices, speed_mps=speed, lap_number=number) for number,
/// indices in sorted(groups.items())]`.
pub fn build_traces(
    groups: &[(i64, Vec<usize>)],
    distance: &dyn Seq,
    times: &dyn Seq,
    speed: &dyn Seq,
) -> KResult<Vec<LapTrace>> {
    let mut ordered: Vec<&(i64, Vec<usize>)> = groups.iter().collect();
    ordered.sort_by_key(|(lap, _)| *lap);
    ordered
        .into_iter()
        .map(|(number, indices)| {
            build_lap_trace(distance, times, indices, Some(speed), PyValue::Int(*number))
        })
        .collect()
}

/// `_screened_clean_laps(laps)`.
pub fn screened_clean_laps(laps: &[PyValue]) -> KResult<Vec<&PyValue>> {
    let mut result = Vec::new();
    let mut previous: Option<String> = None;
    for lap in sorted_laps(laps)? {
        let number = finite_value(get(lap, "lap")?)?;
        let reasons = exclusion_reasons(lap, previous.as_deref())?;
        previous = Some(flag_state(lap)?);
        if reasons.is_empty() && number.is_some_and(|number| number >= 0.0) {
            result.push(lap);
        }
    }
    Ok(result)
}

/// `_clean_lap_traces(table, laps)`: the traces of every screened clean lap and the sorted
/// eligible lap numbers, as the tuple `(list[LapTrace], list[int])`.
pub fn clean_lap_traces(table: &Table, laps: &[PyValue]) -> KResult<PyValue> {
    let empty = || PyValue::Tuple(vec![PyValue::List(Vec::new()), PyValue::List(Vec::new())]);
    if !table.has(&["LapDistPct"]) || !table.has(&["SessionTime"]) || !table.has(&["Lap"]) {
        return Ok(empty());
    }
    let mut eligible: Vec<i64> = Vec::new();
    for lap in screened_clean_laps(laps)? {
        let PyValue::Dict(dict) = lap else {
            return Err(KernelError::internal("a lap is not a dict"));
        };
        let value = dict
            .get("lap")
            .ok_or_else(|| KernelError::raises("KeyError", "'lap'"))?;
        let number = py_int(value)?;
        if !eligible.contains(&number) {
            eligible.push(number);
        }
    }
    if eligible.is_empty() {
        return Ok(empty());
    }
    let lap_channel = table.get(&["Lap"], PyValue::None);
    let groups = lap_groups(&lap_channel, &eligible)?;
    let distance = table.get(&["LapDistPct"], PyValue::None);
    let times = table.get(&["SessionTime", "SessionTimeOfDay"], PyValue::None);
    let speed = table.get(&["Speed"], PyValue::None);
    let traces = build_traces(&groups, &distance, &times, &speed)?;
    eligible.sort_unstable();
    Ok(PyValue::Tuple(vec![
        PyValue::List(traces.iter().map(LapTrace::to_value).collect()),
        PyValue::List(eligible.into_iter().map(PyValue::Int).collect()),
    ]))
}

/// The native `analysis_engine._clean_lap_traces(table, laps)`.
pub fn clean_lap_traces_entry(session: &Session, call: &mut Call) -> Result<PyValue, KernelError> {
    const ENTRY: &str = "analysis_engine._clean_lap_traces";
    let bound = call.bind(ENTRY, &["table", "laps"], &[])?;
    bound.require_table(call, "table")?;
    let laps = items(bound.require(call, "laps")?, "laps")?;
    let table = Table::of(session)?;
    let result = clean_lap_traces(&table, laps)?;
    table.check()?;
    Ok(result)
}

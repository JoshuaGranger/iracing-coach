//! `pit_loss.measure_pit_loss`: every pit-road visit and what it cost against the reference
//! green lap, returned as the `pit_loss.PitLossReport` dataclass `_strategy_planning` reads.

use super::lap_reference::{ReferenceTrace, boundary_index, covers, segment_elapsed};
use super::{Cell, KResult, Seq, lr_finite, resolve_seq};
use crate::kernels::dispatch::Call;
use crate::kernels::error::KernelError;
use crate::kernels::session::Session;
use crate::kernels::table::Table;
use crate::kernels::value::{Dataclass, PyValue};

pub const STATIONARY_SPEED_M_S: f64 = 0.5;
pub const MINIMUM_EPISODE_S: f64 = 3.0;
pub const STATUS_USABLE: &str = "usable";
pub const STATUS_LIMITED: &str = "limited";
pub const STATUS_UNAVAILABLE: &str = "unavailable";
pub const BOUNDARY_TOLERANCE_PCT: f64 = super::lap_reference::DEFAULT_MAX_GAP_PCT;

/// `pit_loss._truthy(value)`.
fn pit_truthy(cell: Cell<'_>) -> KResult<bool> {
    match cell {
        Cell::None => Ok(false),
        Cell::Bool(b) => Ok(b),
        other => match lr_finite(other)? {
            Some(number) => Ok(number != 0.0),
            None => super::truthy(other),
        },
    }
}

/// `pit_loss.PitEpisode`.
struct PitEpisode {
    entry_lap: Option<i64>,
    entry_pct: Option<f64>,
    exit_pct: Option<f64>,
    duration_s: f64,
    stationary_s: f64,
    green_equivalent_s: Option<f64>,
    covered_span_pct: Option<f64>,
    loss_s: Option<f64>,
    travel_loss_s: Option<f64>,
    wrapped_start_finish: bool,
    status: &'static str,
    reason: Option<&'static str>,
}

impl PitEpisode {
    fn to_value(&self) -> PyValue {
        dataclass(
            "pit_loss.PitEpisode",
            vec![
                ("entry_lap", PyValue::from(self.entry_lap)),
                ("entry_pct", PyValue::from(self.entry_pct)),
                ("exit_pct", PyValue::from(self.exit_pct)),
                ("duration_s", PyValue::Float(self.duration_s)),
                ("stationary_s", PyValue::Float(self.stationary_s)),
                ("green_equivalent_s", PyValue::from(self.green_equivalent_s)),
                ("covered_span_pct", PyValue::from(self.covered_span_pct)),
                ("loss_s", PyValue::from(self.loss_s)),
                ("travel_loss_s", PyValue::from(self.travel_loss_s)),
                (
                    "wrapped_start_finish",
                    PyValue::Bool(self.wrapped_start_finish),
                ),
                ("status", PyValue::from(self.status)),
                ("reason", PyValue::from(self.reason)),
            ],
        )
    }
}

fn dataclass(class: &str, fields: Vec<(&str, PyValue)>) -> PyValue {
    PyValue::Dataclass(Box::new(Dataclass {
        class: class.to_owned(),
        fields: fields
            .into_iter()
            .map(|(name, value)| (name.to_owned(), value))
            .collect(),
    }))
}

/// `_green_span_seconds(reference, start_pct, end_pct)`.
fn green_span_seconds(
    reference: &ReferenceTrace,
    start_pct: f64,
    end_pct: f64,
) -> KResult<(Option<f64>, bool, Option<f64>)> {
    let bins = reference.bins;
    let wrapped = end_pct < start_pct;
    let limit = coach_numerics::ceil(BOUNDARY_TOLERANCE_PCT * bins as f64)?;
    let nearest_covered = |target: f64, upward: bool| -> KResult<Option<i64>> {
        let requested = boundary_index(bins, target)?;
        for offset in 0..=limit.max(-1) {
            let index = if upward {
                requested + offset
            } else {
                requested - offset
            };
            if 0 <= index && index <= bins && covers(&reference.session_time_s, index) {
                return Ok(Some(index));
            }
        }
        Ok(None)
    };
    let span = |low: f64, high: f64| -> KResult<(Option<f64>, f64)> {
        let low_index = nearest_covered(low, true)?;
        let high_index = nearest_covered(high, false)?;
        match (low_index, high_index) {
            (Some(low_index), Some(high_index)) if high_index > low_index => {
                let elapsed = segment_elapsed(&reference.session_time_s, low_index, high_index)?;
                let covered = coach_numerics::int_true_div(high_index - low_index, bins)?;
                Ok((elapsed, covered))
            }
            _ => Ok((None, 0.0)),
        }
    };
    if !wrapped {
        let (seconds, covered) = span(start_pct, end_pct)?;
        return Ok((seconds, false, seconds.map(|_| covered)));
    }
    let (first, first_covered) = span(start_pct, 1.0)?;
    let (second, second_covered) = span(0.0, end_pct)?;
    match (first, second) {
        (Some(first), Some(second)) => Ok((
            Some(first + second),
            true,
            Some(first_covered + second_covered),
        )),
        _ => Ok((None, true, None)),
    }
}

/// The arguments of one `measure_pit_loss` call.
pub struct PitLossInputs<'a> {
    pub session_time_s: &'a dyn Seq,
    pub on_pit_road: &'a dyn Seq,
    pub lap_dist_pct: &'a dyn Seq,
    pub speed_m_s: Option<&'a dyn Seq>,
    pub lap_numbers: Option<&'a dyn Seq>,
    pub reference: Option<ReferenceTrace>,
    pub minimum_episode_s: f64,
}

/// `measure_pit_loss(...)` as the `pit_loss.PitLossReport` dataclass.
pub fn measure_pit_loss(inputs: &PitLossInputs<'_>) -> KResult<PyValue> {
    let times = inputs.session_time_s;
    let length = times
        .len()
        .min(inputs.on_pit_road.len())
        .min(inputs.lap_dist_pct.len());
    let mut episodes: Vec<PitEpisode> = Vec::new();
    let mut index = 0;
    while index < length {
        if !pit_truthy(inputs.on_pit_road.cell(index))? {
            index += 1;
            continue;
        }
        let start = index;
        while index < length && pit_truthy(inputs.on_pit_road.cell(index))? {
            index += 1;
        }
        let end = index - 1;

        let entry_time = lr_finite(times.cell(start))?;
        let exit_time = lr_finite(times.cell(end))?;
        let (Some(entry_time), Some(exit_time)) = (entry_time, exit_time) else {
            continue;
        };
        if exit_time <= entry_time {
            continue;
        }
        let duration = exit_time - entry_time;
        if duration < inputs.minimum_episode_s {
            continue;
        }
        let entry_pct = lr_finite(inputs.lap_dist_pct.cell(start))?;
        let exit_pct = lr_finite(inputs.lap_dist_pct.cell(end))?;
        let entry_lap = match inputs.lap_numbers {
            Some(laps) if start < laps.len() => match lr_finite(laps.cell(start))? {
                Some(value) => Some(super::int_of(value)?),
                None => None,
            },
            _ => None,
        };

        let mut stationary = 0.0;
        if let Some(speed) = inputs.speed_m_s {
            for position in start..(end + 1).min(speed.len()) {
                match lr_finite(speed.cell(position))? {
                    Some(value) if value <= STATIONARY_SPEED_M_S => {}
                    _ => continue,
                }
                let previous = if position > start {
                    lr_finite(times.cell(position - 1))?
                } else {
                    None
                };
                let current = lr_finite(times.cell(position))?;
                if let (Some(previous), Some(current)) = (previous, current)
                    && current > previous
                {
                    stationary += current - previous;
                }
            }
        }

        let mut green_equivalent = None;
        let mut covered_span = None;
        let mut wrapped = false;
        let mut reason = None;
        match (&inputs.reference, entry_pct, exit_pct) {
            (None, _, _) => reason = Some("no_green_reference_lap"),
            (Some(_), None, _) | (Some(_), _, None) => {
                reason = Some("pit_road_distance_unavailable");
            }
            (Some(reference), Some(entry), Some(exit)) => {
                (green_equivalent, wrapped, covered_span) =
                    green_span_seconds(reference, entry, exit)?;
                if green_equivalent.is_none() {
                    reason = Some("reference_lap_does_not_cover_pit_span");
                }
            }
        }
        let loss = green_equivalent.map(|green| duration - green);
        episodes.push(PitEpisode {
            entry_lap,
            entry_pct,
            exit_pct,
            duration_s: duration,
            stationary_s: stationary,
            green_equivalent_s: green_equivalent,
            covered_span_pct: covered_span,
            loss_s: loss,
            travel_loss_s: loss.map(|loss| loss - stationary),
            wrapped_start_finish: wrapped,
            status: if loss.is_some() {
                STATUS_USABLE
            } else {
                STATUS_UNAVAILABLE
            },
            reason,
        });
    }

    let measured: Vec<&PitEpisode> = episodes.iter().filter(|e| e.loss_s.is_some()).collect();
    let status = if episodes.is_empty() || measured.is_empty() {
        STATUS_UNAVAILABLE
    } else if measured.len() < episodes.len() {
        STATUS_LIMITED
    } else {
        STATUS_USABLE
    };
    let median = |values: Vec<f64>| -> KResult<PyValue> {
        Ok(PyValue::Float(coach_numerics::median_floats(&values)?))
    };
    let (median_loss, median_stationary, median_travel) = if measured.is_empty() {
        (PyValue::None, PyValue::None, PyValue::None)
    } else {
        (
            median(measured.iter().filter_map(|e| e.loss_s).collect())?,
            median(measured.iter().map(|e| e.stationary_s).collect())?,
            median(measured.iter().filter_map(|e| e.travel_loss_s).collect())?,
        )
    };
    Ok(dataclass(
        "pit_loss.PitLossReport",
        vec![
            (
                "episodes",
                PyValue::Tuple(episodes.iter().map(PitEpisode::to_value).collect()),
            ),
            ("status", PyValue::from(status)),
            ("median_loss_s", median_loss),
            ("median_stationary_s", median_stationary),
            ("median_travel_loss_s", median_travel),
            ("measured_stop_count", PyValue::Int(measured.len() as i64)),
            (
                "reference_lap",
                inputs
                    .reference
                    .as_ref()
                    .map_or(PyValue::None, |reference| reference.lap_number.clone()),
            ),
        ],
    ))
}

/// The native `pit_loss.measure_pit_loss(*, session_time_s, on_pit_road, lap_dist_pct,
/// speed_m_s=None, lap_numbers=None, reference=None, minimum_episode_s=3.0)`.
pub fn measure_pit_loss_entry(session: &Session, call: &mut Call) -> Result<PyValue, KernelError> {
    const ENTRY: &str = "pit_loss.measure_pit_loss";
    let bound = call.bind(
        ENTRY,
        &[],
        &[
            "session_time_s",
            "on_pit_road",
            "lap_dist_pct",
            "speed_m_s",
            "lap_numbers",
            "reference",
            "minimum_episode_s",
        ],
    )?;
    let table = Table::of(session)?;
    let optional = |name: &str| -> KResult<Option<&PyValue>> {
        Ok(bound.get(call, name).filter(|value| !value.is_none()))
    };
    let session_time_s = resolve_seq(&table, bound.require(call, "session_time_s")?)?;
    let on_pit_road = resolve_seq(&table, bound.require(call, "on_pit_road")?)?;
    let lap_dist_pct = resolve_seq(&table, bound.require(call, "lap_dist_pct")?)?;
    let speed = optional("speed_m_s")?
        .map(|value| resolve_seq(&table, value))
        .transpose()?;
    let laps = optional("lap_numbers")?
        .map(|value| resolve_seq(&table, value))
        .transpose()?;
    let reference = optional("reference")?
        .map(ReferenceTrace::from_value)
        .transpose()?;
    let minimum_episode_s = match bound.get(call, "minimum_episode_s") {
        None => MINIMUM_EPISODE_S,
        Some(PyValue::Float(x)) => *x,
        Some(PyValue::Int(i)) => *i as f64,
        Some(other) => {
            return Err(KernelError::internal(format!(
                "{ENTRY}: minimum_episode_s is a {}",
                other.type_name()
            )));
        }
    };
    let report = measure_pit_loss(&PitLossInputs {
        session_time_s: session_time_s.as_seq(),
        on_pit_road: on_pit_road.as_seq(),
        lap_dist_pct: lap_dist_pct.as_seq(),
        speed_m_s: speed.as_ref().map(|seq| seq.as_seq()),
        lap_numbers: laps.as_ref().map(|seq| seq.as_seq()),
        reference,
        minimum_episode_s,
    })?;
    table.check()?;
    Ok(report)
}

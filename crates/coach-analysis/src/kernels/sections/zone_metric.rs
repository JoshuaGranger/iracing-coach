//! `_zone_lap_metric`: one clean lap through one load zone. Not an entry point (the oracle
//! would pass whole columns on every call); `_corner_tire_age_summary` calls it per lap and
//! zone over the series it builds once.
//!
//! Samples are read raw with `_finite`, `_bool` and `_fraction`, as the oracle indexes the
//! `table.get` columns; the records are sorted by their zone-relative position (CPython's
//! stable sort, ties in index order) before the onset, release, pickup and turn-in scans.

use super::{
    Cell, KResult, Seq, ae_round, at, finite, finite_value, float_max, fraction, get, py_bool,
    py_int,
};
use crate::kernels::value::{Dict, PyValue};

/// `SessionFlags` bits read as a caution (`analysis_engine.CAUTION_FLAGS`).
pub const CAUTION_FLAGS: i64 = 0x0008 | 0x0100 | 0x0200 | 0x4000 | 0x8000;

/// What `_zone_lap_metric` needs of the recording's steering basis.
#[derive(Clone, Copy, Debug)]
pub struct Basis {
    pub road_wheel: bool,
}

impl Basis {
    /// `SteeringBasis.threshold(wheel_value)`.
    pub fn threshold(self, wheel_value: f64) -> f64 {
        if self.road_wheel {
            wheel_value / 12.0
        } else {
            wheel_value
        }
    }

    /// `_steering_digits(basis, wheel_digits)`.
    pub fn digits(self, wheel_digits: i64) -> i64 {
        if self.road_wheel {
            wheel_digits + 1
        } else {
            wheel_digits
        }
    }
}

/// The `series` mapping `_corner_tire_age_summary` builds; an absent optional channel (`()` in
/// the oracle) is `None`.
pub struct Series<'a> {
    pub pct: &'a dyn Seq,
    pub speed: &'a dyn Seq,
    pub brake: &'a dyn Seq,
    pub throttle: &'a dyn Seq,
    pub steering: &'a dyn Seq,
    pub flags: &'a dyn Seq,
    pub pit: &'a dyn Seq,
    pub track_location: Option<&'a dyn Seq>,
    pub car_distance_ahead: Option<&'a dyn Seq>,
    pub car_distance_behind: Option<&'a dyn Seq>,
    pub session_state: Option<&'a dyn Seq>,
}

/// `seq[index] if index < len(seq) else None` for a Python int index.
fn read(seq: &dyn Seq, index: i64) -> KResult<Option<Cell<'_>>> {
    if index < seq.len() as i64 {
        Ok(Some(at(seq, index)?))
    } else {
        Ok(None)
    }
}

fn read_finite(seq: &dyn Seq, index: i64) -> KResult<Option<f64>> {
    match read(seq, index)? {
        Some(cell) => finite(cell),
        None => Ok(None),
    }
}

#[derive(Clone, Copy)]
struct Record {
    relative: f64,
    lap_pct: f64,
    speed_mps: Option<f64>,
    speed_mph: Option<f64>,
    brake: Option<f64>,
    throttle: Option<f64>,
    steering_rad: Option<f64>,
    steering_abs_rad: Option<f64>,
    dt: f64,
    traffic_near: bool,
}

/// `range(int(lap.get("start_index") or 0), int(lap.get("end_index") or -1) + 1)`.
fn lap_range(lap: &PyValue) -> KResult<std::ops::Range<i64>> {
    let start = get(lap, "start_index")?;
    let end = get(lap, "end_index")?;
    let start = if start.truthy()? { py_int(start)? } else { 0 };
    let end = if end.truthy()? { py_int(end)? } else { -1 };
    Ok(start..end.saturating_add(1))
}

/// `_count_corrections(values, threshold)`.
pub fn count_corrections(values: &[f64], threshold: f64) -> i64 {
    let mut signs: Vec<i8> = Vec::new();
    let mut previous: Option<f64> = None;
    for &current in values {
        if let Some(previous) = previous {
            let delta = current - previous;
            if delta.abs() >= threshold {
                signs.push(if delta > 0.0 { 1 } else { -1 });
            }
        }
        previous = Some(current);
    }
    signs.windows(2).filter(|pair| pair[0] != pair[1]).count() as i64
}

/// `statistics.fmean` of floats (`_mean` of values that are all finite floats).
fn mean(values: &[f64]) -> KResult<Option<f64>> {
    if values.is_empty() {
        Ok(None)
    } else {
        Ok(Some(coach_numerics::fmean(values)?))
    }
}

/// `_round` of a builtin `sum` result (an int when nothing was summed).
fn round_sum(items: &[f64], digits: i64) -> KResult<PyValue> {
    ae_round(
        Some(coach_numerics::sum_floats_from_int_zero(items).to_f64()),
        digits,
    )
}

/// `_zone_lap_metric(anchor_lap=..., next_lap=..., zone=..., series=..., dts=...,
/// require_next_lap=..., steering=...)`.
#[allow(clippy::too_many_arguments)]
pub fn zone_lap_metric(
    anchor_lap: &PyValue,
    next_lap: Option<&PyValue>,
    zone: &PyValue,
    series: &Series<'_>,
    dts: &dyn Seq,
    require_next_lap: bool,
    basis: Basis,
) -> KResult<Option<Dict>> {
    let material_steering = basis.threshold(0.08);
    let correction_step = basis.threshold(0.025);
    let (Some(start), Some(end)) = (
        finite_value(get(zone, "start_pct")?)?,
        finite_value(get(zone, "end_pct")?)?,
    ) else {
        return Ok(None);
    };
    let wraps = get(zone, "wraps_start_finish")?.truthy()? || start > end;
    let span = if wraps {
        coach_numerics::float_mod(end - start, 1.0)?
    } else {
        end - start
    };
    if span <= 0.0 || span >= 0.75 {
        return Ok(None);
    }
    let scaled = span * 0.40;
    let floor = if scaled > 0.025 { scaled } else { 0.025 };
    let entry_margin = if floor < 0.08 { floor } else { 0.08 };
    let exit_margin = entry_margin;

    let mut indexed: Vec<(i64, bool)> =
        lap_range(anchor_lap)?.map(|index| (index, false)).collect();
    if wraps {
        if require_next_lap && next_lap.is_none() {
            return Ok(None);
        }
        if let Some(next_lap) = next_lap {
            indexed.extend(lap_range(next_lap)?.map(|index| (index, true)));
        }
    }

    let low_bound = {
        let value = start - entry_margin;
        if value > 0.0 { value } else { 0.0 }
    };
    let high_bound = {
        let value = end + exit_margin;
        if value < 1.0 { value } else { 1.0 }
    };
    let mut records: Vec<Record> = Vec::new();
    for (index, from_next_lap) in indexed {
        let Some(position) = read_finite(series.pct, index)? else {
            continue;
        };
        if position < 0.0 {
            continue;
        }
        let position = coach_numerics::float_mod(position, 1.0)?;
        if wraps {
            if !from_next_lap && position < low_bound {
                continue;
            }
            if from_next_lap && position > high_bound {
                continue;
            }
        }
        let mut relative = coach_numerics::float_mod(position - start, 1.0)?;
        if relative >= 1.0 - entry_margin {
            relative -= 1.0;
        }
        if relative < -entry_margin || relative > span + exit_margin {
            continue;
        }
        // int(_finite(flags[index]) or 0) & CAUTION_FLAGS
        if let Some(number) = read_finite(series.flags, index)?
            && number != 0.0
            && super::int_of(number)? & CAUTION_FLAGS != 0
        {
            continue;
        }
        if let Some(cell) = read(series.pit, index)?
            && py_bool(cell)?
        {
            continue;
        }
        if let Some(column) = series.track_location
            && let Some(location) = read_finite(column, index)?
            && super::int_of(location)? != 3
        {
            continue;
        }
        if let Some(column) = series.session_state
            && let Some(state) = read_finite(column, index)?
            && super::int_of(state)? != 4
        {
            continue;
        }
        let speed_mps = read_finite(series.speed, index)?;
        let steering_value = read_finite(series.steering, index)?;
        let ahead = match series.car_distance_ahead {
            Some(column) => read_finite(column, index)?,
            None => None,
        };
        let behind = match series.car_distance_behind {
            Some(column) => read_finite(column, index)?,
            None => None,
        };
        let speed_or_zero = match speed_mps {
            Some(x) if x != 0.0 => x,
            _ => 0.0,
        };
        let ahead_limit = {
            let value = speed_or_zero * 0.75;
            if value > 25.0 { value } else { 25.0 }
        };
        let behind_limit = {
            let value = speed_or_zero * 0.40;
            if value > 15.0 { value } else { 15.0 }
        };
        let traffic_near = ahead.is_some_and(|ahead| ahead > 0.0 && ahead <= ahead_limit)
            || behind.is_some_and(|behind| behind > 0.0 && behind <= behind_limit);
        let brake = match read(series.brake, index)? {
            Some(cell) => fraction(cell)?,
            None => None,
        };
        let throttle = match read(series.throttle, index)? {
            Some(cell) => fraction(cell)?,
            None => None,
        };
        let dt = match read(dts, index)? {
            Some(Cell::Float(dt)) => dt,
            _ => 0.0,
        };
        records.push(Record {
            relative,
            lap_pct: position,
            speed_mps,
            speed_mph: speed_mps.map(|speed| speed * 2.236936),
            brake,
            throttle,
            steering_rad: steering_value,
            steering_abs_rad: steering_value.map(f64::abs),
            dt,
            traffic_near,
        });
    }
    let order = coach_numerics::sort_permutation(
        records.len(),
        &mut |i, j| records[i].relative < records[j].relative,
        false,
    );
    let records: Vec<Record> = order.into_iter().map(|index| records[index]).collect();
    let core: Vec<&Record> = records
        .iter()
        .filter(|item| 0.0 <= item.relative && item.relative <= span)
        .collect();
    let speed_core: Vec<&Record> = core
        .iter()
        .copied()
        .filter(|item| item.speed_mph.is_some())
        .collect();
    if core.len() < 3 || speed_core.is_empty() {
        return Ok(None);
    }
    let near = core.iter().filter(|item| item.traffic_near).count();
    if coach_numerics::int_true_div(near as i64, core.len() as i64)? >= 0.10 {
        return Ok(None);
    }

    let entry_record = speed_core[0];
    let exit_record = speed_core[speed_core.len() - 1];
    let minimum_index = coach_numerics::min_index_by(speed_core.len(), |i, j| {
        speed_core[i].speed_mph < speed_core[j].speed_mph
    })
    .unwrap_or(0);
    let minimum_record = speed_core[minimum_index];
    let brakes: Vec<f64> = core.iter().filter_map(|item| item.brake).collect();
    let steering: Vec<f64> = core
        .iter()
        .filter_map(|item| item.steering_abs_rad)
        .collect();
    let steering_signed: Vec<f64> = core.iter().filter_map(|item| item.steering_rad).collect();
    let brake_energy: Vec<f64> = core
        .iter()
        .filter_map(|item| Some(item.brake? * item.speed_mps? * item.dt))
        .collect();
    let steering_work: Vec<f64> = core
        .iter()
        .filter_map(|item| Some(item.steering_abs_rad? * item.speed_mps? * item.dt))
        .collect();

    let brake_records: Vec<(&Record, f64)> = records
        .iter()
        .filter_map(|item| item.brake.map(|brake| (item, brake)))
        .collect();
    let brake_onset_censored = brake_records
        .first()
        .is_some_and(|(_, brake)| *brake >= 0.05);
    let mut brake_onset: Option<&Record> = None;
    if !brake_onset_censored {
        for pair in brake_records.windows(2) {
            let ((_, previous), (current, brake)) = (pair[0], pair[1]);
            if previous < 0.05 && 0.05 <= brake && current.relative <= span {
                brake_onset = Some(current);
                break;
            }
        }
    }
    let mut brake_release: Option<&Record> = None;
    let brake_values: Vec<f64> = brake_records.iter().map(|(_, brake)| *brake).collect();
    if float_max(&brake_values).is_some_and(|peak| peak >= 0.05) {
        let peak_index = coach_numerics::max_index_by(brake_values.len(), |i, j| {
            brake_values[i] > brake_values[j]
        })
        .unwrap_or(0);
        brake_release = brake_records[peak_index + 1..]
            .iter()
            .find(|(_, brake)| *brake < 0.05)
            .map(|(record, _)| *record);
    }

    let throttle_records: Vec<(&Record, f64)> = records
        .iter()
        .filter(|item| item.relative >= minimum_record.relative)
        .filter_map(|item| item.throttle.map(|throttle| (item, throttle)))
        .collect();
    let throttle_censored = throttle_records
        .first()
        .is_some_and(|(_, throttle)| *throttle >= 0.75);
    let mut throttle_pickup: Option<&Record> = None;
    if !throttle_censored {
        for pair in throttle_records.windows(2) {
            let ((_, previous), (current, throttle)) = (pair[0], pair[1]);
            if previous < 0.75 && 0.75 <= throttle {
                throttle_pickup = Some(current);
                break;
            }
        }
    }

    let steering_records: Vec<&Record> = records
        .iter()
        .filter(|item| item.steering_abs_rad.is_some())
        .collect();
    let baseline_limit = {
        let value = entry_margin * 0.35;
        -(if value > 0.01 { value } else { 0.01 })
    };
    let baseline_values: Vec<f64> = steering_records
        .iter()
        .filter(|item| item.relative <= baseline_limit)
        .filter_map(|item| item.steering_rad)
        .collect();
    let steering_baseline = if baseline_values.is_empty() {
        None
    } else {
        Some(coach_numerics::median_floats(&baseline_values)?)
    };
    let turn_threshold = basis.threshold(0.05);
    let demands: Vec<f64> = steering_records
        .iter()
        .map(|item| match steering_baseline {
            Some(baseline) => {
                let steering = match item.steering_rad {
                    Some(x) if x != 0.0 => x,
                    _ => 0.0,
                };
                (steering - baseline).abs()
            }
            None => item.steering_abs_rad.unwrap_or(0.0),
        })
        .collect();
    let mut turn_in_censored = demands
        .first()
        .is_some_and(|demand| *demand >= turn_threshold);
    let mut turn_in: Option<&Record> = None;
    if !turn_in_censored {
        for (offset, pair) in demands.windows(2).enumerate() {
            let current = steering_records[offset + 1];
            if pair[0] < turn_threshold && turn_threshold <= pair[1] && current.relative <= span {
                turn_in = Some(current);
                break;
            }
        }
        let first_abs = steering_records
            .first()
            .and_then(|item| item.steering_abs_rad)
            .map_or(0.0, |x| if x != 0.0 { x } else { 0.0 });
        if turn_in.is_none()
            && !steering_records.is_empty()
            && first_abs >= material_steering
            && steering_baseline.map_or(0.0, f64::abs) >= material_steering
        {
            turn_in_censored = true;
        }
    }

    let lap = match finite_value(get(anchor_lap, "lap")?)? {
        Some(x) if x != 0.0 => super::int_of(x)?,
        _ => 0,
    };
    let event = |record: Option<&Record>, pick: fn(&Record) -> f64| -> KResult<PyValue> {
        match record {
            Some(record) => ae_round(Some(pick(record)), 5),
            None => Ok(PyValue::None),
        }
    };
    let pct = |record: &Record| record.lap_pct;
    let offset = |record: &Record| record.relative;
    Ok(Some(
        Dict::new()
            .with("lap", lap)
            .with("sample_count", core.len() as i64)
            .with("entry_speed_mph", ae_round(entry_record.speed_mph, 2)?)
            .with("minimum_speed_mph", ae_round(minimum_record.speed_mph, 2)?)
            .with("exit_speed_mph", ae_round(exit_record.speed_mph, 2)?)
            .with("brake_average_fraction", ae_round(mean(&brakes)?, 4)?)
            .with("brake_peak_fraction", ae_round(float_max(&brakes), 4)?)
            .with(
                "brake_energy_proxy",
                if brakes.is_empty() {
                    PyValue::None
                } else {
                    round_sum(&brake_energy, 3)?
                },
            )
            .with("brake_onset_lap_pct", event(brake_onset, pct)?)
            .with("brake_onset_offset_lap_pct", event(brake_onset, offset)?)
            .with("brake_onset_boundary_censored", brake_onset_censored)
            .with("brake_release_lap_pct", event(brake_release, pct)?)
            .with(
                "brake_release_offset_lap_pct",
                event(brake_release, offset)?,
            )
            .with(
                "steering_average_abs_rad",
                ae_round(mean(&steering)?, basis.digits(4))?,
            )
            .with(
                "steering_work_proxy",
                if steering.is_empty() {
                    PyValue::None
                } else {
                    round_sum(&steering_work, 3)?
                },
            )
            .with(
                "steering_corrections",
                count_corrections(&steering_signed, correction_step),
            )
            .with("turn_in_lap_pct", event(turn_in, pct)?)
            .with("turn_in_offset_lap_pct", event(turn_in, offset)?)
            .with("turn_in_boundary_censored", turn_in_censored)
            .with(
                "turn_in_steering_baseline_rad",
                ae_round(steering_baseline, basis.digits(5))?,
            )
            .with(
                "turn_in_demand_threshold_rad",
                ae_round(Some(turn_threshold), 7)?,
            )
            .with("throttle_pickup_lap_pct", event(throttle_pickup, pct)?)
            .with(
                "throttle_pickup_offset_lap_pct",
                event(throttle_pickup, offset)?,
            )
            .with("throttle_pickup_boundary_censored", throttle_censored)
            .with("exit_throttle_fraction", ae_round(exit_record.throttle, 4)?),
    ))
}

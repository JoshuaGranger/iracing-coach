//! `tire_energy.segment_energy` and `EnergyReport.to_payload`, as `_pace_attribution` calls
//! them. Per-sample energy accumulates as the oracle's `+=` loops (plain left folds); the lap
//! total is builtin `sum` (int start). `atan2` and `sin` are C library calls (platform family,
//! `numerics.md` section 4).

use super::time_loss::or_zero;
use super::{KResult, Seq, lr_finite, lr_finite_value, py_str, round_float, round_opt};
use crate::kernels::value::{Dict, PyValue};
use coach_numerics::Num;

pub const TIRE_ENERGY_VERSION: i64 = 1;
pub const MINIMUM_SLIP_SPEED_M_S: f64 = 5.0;
pub const SLIP_COVERAGE_FOR_GRADE: f64 = 0.50;
pub const GRADE_SLIP_WEIGHTED: &str = "slip_weighted";
pub const GRADE_LOAD_ONLY: &str = "load_only";
pub const STATUS_USABLE: &str = "usable";
pub const STATUS_LIMITED: &str = "limited";
pub const STATUS_UNAVAILABLE: &str = "unavailable";
pub const REQUIRED_CHANNELS: [&str; 5] = [
    "Speed",
    "LatAccel",
    "LongAccel",
    "LapDistPct",
    "SessionTime",
];

/// `tire_energy.SegmentEnergy`.
#[derive(Clone, Debug)]
pub struct SegmentEnergy {
    pub name: String,
    pub start_pct: f64,
    pub end_pct: f64,
    pub status: &'static str,
    pub grade: &'static str,
    pub lateral_load_j_per_kg: f64,
    pub longitudinal_load_j_per_kg: f64,
    pub lateral_slip_j_per_kg: Option<f64>,
    pub total_load_j_per_kg: f64,
    pub share_of_lap: Option<f64>,
    pub slip_coverage: f64,
    pub sample_count: i64,
    pub peak_lateral_g: Option<f64>,
}

impl SegmentEnergy {
    fn to_payload(&self) -> KResult<PyValue> {
        Ok(PyValue::Dict(
            Dict::new()
                .with("name", self.name.as_str())
                .with("start_pct", round_float(self.start_pct, 6)?)
                .with("end_pct", round_float(self.end_pct, 6)?)
                .with("status", self.status)
                .with("grade", self.grade)
                .with(
                    "lateral_load_j_per_kg",
                    round_float(self.lateral_load_j_per_kg, 3)?,
                )
                .with(
                    "longitudinal_load_j_per_kg",
                    round_float(self.longitudinal_load_j_per_kg, 3)?,
                )
                .with(
                    "lateral_slip_j_per_kg",
                    round_opt(self.lateral_slip_j_per_kg, 3)?,
                )
                .with(
                    "total_load_j_per_kg",
                    round_float(self.total_load_j_per_kg, 3)?,
                )
                .with("share_of_lap", round_opt(self.share_of_lap, 4)?)
                .with("slip_coverage", round_float(self.slip_coverage, 4)?)
                .with("sample_count", self.sample_count)
                .with("peak_lateral_g", round_opt(self.peak_lateral_g, 3)?),
        ))
    }
}

/// `tire_energy.EnergyReport`.
#[derive(Clone, Debug)]
pub struct EnergyReport {
    pub segments: Vec<SegmentEnergy>,
    pub status: &'static str,
    pub total_load_j_per_kg: Option<Num>,
    pub grade: &'static str,
    pub missing_channels: Vec<String>,
}

impl EnergyReport {
    pub fn to_payload(&self) -> KResult<PyValue> {
        let total = match self.total_load_j_per_kg {
            None => PyValue::None,
            Some(Num::Float(x)) => PyValue::Float(round_float(x, 3)?),
            Some(Num::Int(i)) => PyValue::Int(i),
        };
        let segments = self
            .segments
            .iter()
            .map(SegmentEnergy::to_payload)
            .collect::<KResult<Vec<_>>>()?;
        Ok(PyValue::Dict(
            Dict::new()
                .with("contract_version", TIRE_ENERGY_VERSION)
                .with("status", self.status)
                .with("grade", self.grade)
                .with("total_load_j_per_kg", total)
                .with(
                    "required_channels",
                    REQUIRED_CHANNELS
                        .iter()
                        .map(|&name| PyValue::from(name))
                        .collect::<Vec<_>>(),
                )
                .with(
                    "missing_channels",
                    self.missing_channels
                        .iter()
                        .map(|name| PyValue::from(name.as_str()))
                        .collect::<Vec<_>>(),
                )
                .with("segments", segments)
                .with(
                    "classification",
                    "specific energy proxy from vehicle acceleration and speed; vehicle mass \
                     cancels. Not a wear measurement and not a wear model.",
                )
                .with("units", "J/kg"),
        ))
    }
}

/// `_sideslip_radians(forward, lateral)`.
fn sideslip_radians(forward: Option<f64>, lateral: Option<f64>) -> Option<f64> {
    let (longitudinal, sideways) = (forward?, lateral?);
    if coach_numerics::hypot(longitudinal, sideways) < MINIMUM_SLIP_SPEED_M_S {
        return None;
    }
    if longitudinal <= 0.5 {
        return None;
    }
    let angle = coach_numerics::atan2(sideways, longitudinal);
    angle.is_finite().then_some(angle)
}

struct Bucket {
    name: String,
    start_pct: Option<f64>,
    end_pct: Option<f64>,
    wraps: bool,
    lateral: f64,
    longitudinal: f64,
    slip: f64,
    slip_samples: i64,
    samples: i64,
    peak_lat_g: Option<f64>,
}

/// The inputs of one `segment_energy` call; the table columns as `_pace_attribution` passes
/// them.
pub struct EnergyInputs<'a> {
    pub lap_dist_pct: &'a dyn Seq,
    pub session_time_s: &'a dyn Seq,
    pub speed_m_s: &'a dyn Seq,
    pub lat_accel_m_s2: &'a dyn Seq,
    pub long_accel_m_s2: &'a dyn Seq,
    pub segments: &'a [PyValue],
    pub indices: &'a [usize],
    pub velocity_x_m_s: Option<&'a dyn Seq>,
    pub velocity_y_m_s: Option<&'a dyn Seq>,
    pub sample_rate_hz: f64,
    pub missing_channels: Vec<String>,
}

/// `tire_energy._dt_series(session_time_s, fallback_hz)[index]`, computed for one index.
fn dt_at(times: &dyn Seq, index: usize, fallback: f64) -> KResult<f64> {
    let current = lr_finite(times.cell(index))?;
    let previous = if index > 0 {
        lr_finite(times.cell(index - 1))?
    } else {
        None
    };
    let (Some(current), Some(previous)) = (current, previous) else {
        return Ok(fallback);
    };
    if current <= previous {
        return Ok(fallback);
    }
    let step = current - previous;
    Ok(if step < fallback * 20.0 {
        step
    } else {
        fallback
    })
}

/// `segment_energy(...)`.
pub fn segment_energy(inputs: &EnergyInputs<'_>) -> KResult<EnergyReport> {
    if !inputs.missing_channels.is_empty() {
        return Ok(EnergyReport {
            segments: Vec::new(),
            status: STATUS_UNAVAILABLE,
            total_load_j_per_kg: None,
            grade: GRADE_LOAD_ONLY,
            missing_channels: inputs.missing_channels.clone(),
        });
    }
    let fallback = if inputs.sample_rate_hz > 0.0 {
        1.0 / inputs.sample_rate_hz
    } else {
        0.05
    };
    let mut buckets = Vec::with_capacity(inputs.segments.len());
    for (index, segment) in inputs.segments.iter().enumerate() {
        let start_pct = lr_finite_value(segment.get("start_pct").unwrap_or(&PyValue::None))?;
        let end_pct = lr_finite_value(segment.get("end_pct").unwrap_or(&PyValue::None))?;
        let named = segment.get("name").unwrap_or(&PyValue::None);
        let name = if named.truthy()? {
            py_str(named)?
        } else {
            match segment.get("segment") {
                Some(number) if number.truthy()? => format!("Corner {}", py_str(number)?),
                _ => format!("Segment {}", index + 1),
            }
        };
        let wraps = match segment.get("wraps_start_finish") {
            Some(value) => value.truthy()?,
            None => false,
        };
        buckets.push(Bucket {
            name,
            start_pct,
            end_pct,
            wraps,
            lateral: 0.0,
            longitudinal: 0.0,
            slip: 0.0,
            slip_samples: 0,
            samples: 0,
            peak_lat_g: None,
        });
    }

    for &index in inputs.indices {
        if index >= inputs.lap_dist_pct.len() {
            continue;
        }
        let read = |column: &dyn Seq| -> KResult<Option<f64>> {
            if index < column.len() {
                lr_finite(column.cell(index))
            } else {
                Ok(None)
            }
        };
        let position = lr_finite(inputs.lap_dist_pct.cell(index))?;
        let speed = read(inputs.speed_m_s)?;
        let lat = read(inputs.lat_accel_m_s2)?;
        let lon = read(inputs.long_accel_m_s2)?;
        let (Some(position), Some(speed)) = (position, speed) else {
            continue;
        };
        if speed <= 0.0 || (lat.is_none() && lon.is_none()) {
            continue;
        }
        let dt = if index < inputs.session_time_s.len() {
            dt_at(inputs.session_time_s, index, fallback)?
        } else {
            1.0 / inputs.sample_rate_hz
        };
        let beta = match (inputs.velocity_x_m_s, inputs.velocity_y_m_s) {
            (Some(vx), Some(vy)) if index < vx.len() && index < vy.len() => {
                sideslip_radians(lr_finite(vx.cell(index))?, lr_finite(vy.cell(index))?)
            }
            _ => None,
        };
        for bucket in &mut buckets {
            let (Some(start), Some(end)) = (bucket.start_pct, bucket.end_pct) else {
                continue;
            };
            if bucket.wraps || !(start <= position && position < end) {
                continue;
            }
            let lateral_power = lat.map_or(0.0, |lat| lat.abs() * speed);
            let longitudinal_power = lon.map_or(0.0, |lon| lon.abs() * speed);
            bucket.lateral += lateral_power * dt;
            bucket.longitudinal += longitudinal_power * dt;
            bucket.samples += 1;
            if let Some(lat) = lat {
                let lateral_g = lat.abs() / 9.80665;
                if bucket.peak_lat_g.is_none_or(|peak| lateral_g > peak) {
                    bucket.peak_lat_g = Some(lateral_g);
                }
            }
            if let Some(beta) = beta {
                bucket.slip += lateral_power * coach_numerics::sin(beta)?.abs() * dt;
                bucket.slip_samples += 1;
            }
            break;
        }
    }

    let totals: Vec<f64> = buckets
        .iter()
        .map(|bucket| bucket.lateral + bucket.longitudinal)
        .collect();
    let lap_total = coach_numerics::sum_floats_from_int_zero(&totals);
    let lap_total_positive = match lap_total {
        Num::Int(i) => i > 0,
        Num::Float(x) => x > 0.0,
    };
    let mut results = Vec::with_capacity(buckets.len());
    let mut slip_covered = 0i64;
    let mut all_samples = 0i64;
    for (bucket, &total) in buckets.iter().zip(&totals) {
        let samples = bucket.samples;
        all_samples += samples;
        slip_covered += bucket.slip_samples;
        let coverage = if samples != 0 {
            bucket.slip_samples as f64 / samples as f64
        } else {
            0.0
        };
        let usable = samples > 0 && !bucket.wraps;
        results.push(SegmentEnergy {
            name: bucket.name.clone(),
            start_pct: or_zero(bucket.start_pct),
            end_pct: or_zero(bucket.end_pct),
            status: if usable {
                STATUS_USABLE
            } else {
                STATUS_UNAVAILABLE
            },
            grade: if coverage >= SLIP_COVERAGE_FOR_GRADE {
                GRADE_SLIP_WEIGHTED
            } else {
                GRADE_LOAD_ONLY
            },
            lateral_load_j_per_kg: bucket.lateral,
            longitudinal_load_j_per_kg: bucket.longitudinal,
            lateral_slip_j_per_kg: (bucket.slip_samples != 0).then_some(bucket.slip),
            total_load_j_per_kg: total,
            share_of_lap: lap_total_positive.then(|| total / lap_total.to_f64()),
            slip_coverage: coverage,
            sample_count: samples,
            peak_lateral_g: bucket.peak_lat_g,
        });
    }

    // sorted(key=(status != usable, -(share_of_lap or 0.0))).
    let keys: Vec<(bool, f64)> = results
        .iter()
        .map(|item| (item.status != STATUS_USABLE, -or_zero(item.share_of_lap)))
        .collect();
    let order = coach_numerics::sort_permutation(
        results.len(),
        &mut |i, j| {
            let (a, b) = (keys[i], keys[j]);
            if a.0 != b.0 {
                return !a.0 && b.0;
            }
            a.1 != b.1 && a.1 < b.1
        },
        false,
    );
    let ranked: Vec<SegmentEnergy> = order.iter().map(|&index| results[index].clone()).collect();
    let usable_count = ranked
        .iter()
        .filter(|item| item.status == STATUS_USABLE)
        .count();
    let overall_coverage = if all_samples != 0 {
        slip_covered as f64 / all_samples as f64
    } else {
        0.0
    };
    let status = if usable_count == 0 {
        STATUS_UNAVAILABLE
    } else if usable_count < results.len() {
        STATUS_LIMITED
    } else {
        STATUS_USABLE
    };
    Ok(EnergyReport {
        segments: ranked,
        status,
        total_load_j_per_kg: (usable_count > 0).then_some(lap_total),
        grade: if overall_coverage >= SLIP_COVERAGE_FOR_GRADE {
            GRADE_SLIP_WEIGHTED
        } else {
            GRADE_LOAD_ONLY
        },
        missing_channels: Vec::new(),
    })
}

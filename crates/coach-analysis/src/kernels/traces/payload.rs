//! `_lap_trace_payload`: bounded distance-domain traces, one point per lap-distance bin.

use super::super::error::KernelError;
use super::super::table::{SteeringBasis, Table};
use super::super::value::{Dict, PyValue};
use super::signals::{Reducer, additional_trace_sources, sideslip_column};
use super::{Col, dict, first_max, first_min, median_in_place, or_zero, round_opt, signed_peak};

const DOWNSAMPLING: &str = "lap-distance bins retain representative values plus speed range, \
                            minimum throttle, peak brake, and peak absolute steering";
const TIRE_STRESS_DEFINITION: &str = "bounded blend of brake fraction at speed, steering \
                                      magnitude at speed, and lateral acceleration; it is not \
                                      per-lap tread wear";

/// `SteeringBasis.describe()`.
pub(super) fn describe(basis: &SteeringBasis) -> Result<PyValue, KernelError> {
    let road = basis.road_wheel();
    let label = if road {
        "Steering (road wheel)".to_owned()
    } else {
        let suffix = match basis.status.as_str() {
            "unreadable" | "ambiguous" => "ratio not readable",
            "changed_during_recording" => "ratio changed during recording",
            _ => "ratio not recorded",
        };
        format!("Steering wheel angle \u{2014} {suffix}")
    };
    let definition = if road {
        // self.ratio_text or _ratio_text(self.ratio or 0.0)
        let ratio_text = match &basis.ratio_text {
            Some(text) if !text.is_empty() => text.clone(),
            _ => {
                let ratio = basis.ratio.filter(|ratio| *ratio != 0.0).unwrap_or(0.0);
                format!("{}:1", coach_numerics::format_float(ratio, "g")?)
            }
        };
        let mut text = format!(
            "Recorded SteeringWheelAngle divided by the recording's {ratio_text} steering \
             ratio: the steer applied at the front wheels, comparable across steering ratios."
        );
        if let Some(offset) = basis
            .offset_text
            .as_deref()
            .filter(|offset| !offset.is_empty())
        {
            text.push_str(&format!(
                " The setup's steering offset ({offset}) is not applied."
            ));
        }
        text
    } else {
        "Recorded SteeringWheelAngle. Without the car's steering ratio it cannot be converted \
         to road-wheel angle, so it is never compared with road-wheel steering."
            .to_owned()
    };
    let text = |value: &Option<String>| PyValue::from(value.clone());
    Ok(dict([
        ("schema_version", PyValue::Int(1)),
        ("basis", basis.kind.as_str().into()),
        ("status", basis.status.as_str().into()),
        ("label", label.into()),
        (
            "short_label",
            if road {
                "Steering (road wheel)"
            } else {
                "Steering wheel angle"
            }
            .into(),
        ),
        ("unit", "deg".into()),
        ("value_unit", "rad".into()),
        ("degree_digits", PyValue::Int(if road { 2 } else { 1 })),
        ("steering_ratio", basis.ratio.into()),
        ("steering_ratio_text", text(&basis.ratio_text)),
        ("ratio_source", text(&basis.ratio_source)),
        ("setup_steering_offset", text(&basis.offset_text)),
        ("steering_offset_applied", PyValue::Bool(false)),
        ("raw_channel", "SteeringWheelAngle".into()),
        (
            "steering_wheel_angle_recorded",
            PyValue::Bool(basis.wheel_angle_recorded),
        ),
        (
            "evidence_class",
            if road { "derived" } else { "measured" }.into(),
        ),
        ("definition", definition.into()),
        ("reason", text(&basis.reason)),
        ("threshold_reference_ratio", PyValue::Float(12.0)),
    ]))
}

/// `sorted({finite SectorStartPct in [0, 1)})`, as `_round(value, 6)`.
fn sector_start_pcts(session_info: &PyValue) -> Result<PyValue, KernelError> {
    // _path_get(session_info, "SplitTimeInfo", "Sectors") or []
    let sectors = session_info
        .get("SplitTimeInfo")
        .and_then(|value| value.get("Sectors"))
        .unwrap_or(&PyValue::None);
    let items: &[PyValue] = match sectors {
        value if !value.truthy()? => &[],
        PyValue::List(items) | PyValue::Tuple(items) => items,
        // Iterating a dict or a str yields keys or characters: never a mapping.
        PyValue::Dict(_) | PyValue::Str(_) => &[],
        other => {
            return Err(KernelError::raises(
                "TypeError",
                format!("'{}' object is not iterable", other.type_name()),
            ));
        }
    };
    // The set keeps the first of equal values (0.0 before -0.0 or the reverse).
    let mut unique: Vec<f64> = Vec::new();
    for item in items {
        let Some(map) = item.as_dict() else { continue };
        let Some(value) = map
            .get("SectorStartPct")
            .map_or(Ok(None), PyValue::finite)?
        else {
            continue;
        };
        if (0.0..1.0).contains(&value) && !unique.contains(&value) {
            unique.push(value);
        }
    }
    unique.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    unique
        .into_iter()
        .map(|value| round_opt(Some(value), 6))
        .collect::<Result<Vec<_>, _>>()
        .map(PyValue::List)
}

/// `int(_finite(lap.get(key)) or default)` as a float of an integral value (Python ints are
/// unbounded; only comparisons and the table length bound the range below).
fn lap_index(lap: &Dict, key: &str, default: f64) -> Result<f64, KernelError> {
    let value = lap.get(key).map_or(Ok(None), PyValue::finite)?;
    Ok(match value {
        Some(number) if number != 0.0 => number.trunc(),
        _ => default,
    })
}

/// `list(value or [])` for a lap's `flag_states`.
fn list_of(value: Option<&PyValue>) -> Result<PyValue, KernelError> {
    let Some(value) = value else {
        return Ok(PyValue::List(Vec::new()));
    };
    if !value.truthy()? {
        return Ok(PyValue::List(Vec::new()));
    }
    Ok(PyValue::List(match value {
        PyValue::List(items) | PyValue::Tuple(items) => items.clone(),
        PyValue::Str(text) => text.chars().map(|c| PyValue::Str(c.to_string())).collect(),
        PyValue::Dict(map) => map.keys().cloned().collect(),
        PyValue::Int(_) | PyValue::BigInt(_) | PyValue::Float(_) | PyValue::Bool(_) => {
            return Err(KernelError::raises(
                "TypeError",
                format!("'{}' object is not iterable", value.type_name()),
            ));
        }
        other => {
            return Err(KernelError::internal(format!(
                "list() of a {} is not ported",
                other.type_name()
            )));
        }
    }))
}

/// `dict(value or {})` for a lap's `conditions`.
fn dict_of(value: Option<&PyValue>) -> Result<PyValue, KernelError> {
    match value {
        None => Ok(PyValue::Dict(Dict::new())),
        Some(value) if !value.truthy()? => Ok(PyValue::Dict(Dict::new())),
        Some(PyValue::Dict(map)) => Ok(PyValue::Dict(map.clone())),
        Some(other) => Err(KernelError::internal(format!(
            "dict() of a {} is not ported",
            other.type_name()
        ))),
    }
}

/// `_path_get(lap, "fuel", "used_gal")`.
fn fuel_used(lap: &Dict) -> PyValue {
    lap.get("fuel")
        .and_then(|fuel| fuel.get("used_gal"))
        .cloned()
        .unwrap_or(PyValue::None)
}

/// The per-sample columns every bin reads.
struct Columns {
    lap_distance: Col,
    speed: Col,
    throttle: Col,
    brake: Col,
    steering: Col,
    gear: Col,
    rpm: Col,
    yaw_degrees: Col,
    sideslip: Col,
    lateral_g: Col,
    longitudinal_g: Col,
    latitude: Col,
    longitude: Col,
    session_time: Col,
}

/// Reused per-bin buffers.
#[derive(Default)]
struct Scratch {
    speeds: Vec<f64>,
    throttles: Vec<f64>,
    brakes: Vec<f64>,
    steerings: Vec<f64>,
    work: Vec<f64>,
}

/// `index` as a list position: a negative index counts from the end (the caller checked it is
/// not below `-length`).
fn position(index: i64, length: usize) -> usize {
    if index < 0 {
        (index + length as i64) as usize
    } else {
        index as usize
    }
}

/// The positions the oracle reads for one bin's `indices` (as appended, in sample order):
/// `column[first:last + 1]` when they span exactly their count (`_present_values`' run, a
/// Python slice), else each index.
fn bin_positions(indices: &[i64], length: usize, out: &mut Vec<u32>) -> Result<(), KernelError> {
    out.clear();
    let (Some(&first), Some(&last)) = (indices.first(), indices.last()) else {
        return Ok(());
    };
    let to_u32 =
        |p: usize| u32::try_from(p).map_err(|_| KernelError::internal("sample index beyond u32"));
    if last + 1 - first == indices.len() as i64 {
        let bound = |i: i64| -> usize {
            let i = if i < 0 { i + length as i64 } else { i };
            i.clamp(0, length as i64) as usize
        };
        for p in bound(first)..bound(last + 1) {
            out.push(to_u32(p)?);
        }
    } else {
        for &index in indices {
            out.push(to_u32(position(index, length))?);
        }
    }
    Ok(())
}

fn median_of(col: &Col, indices: &[u32], work: &mut Vec<f64>) -> Option<f64> {
    col.gather(indices, work);
    median_in_place(work)
}

fn peak_of(col: &Col, indices: &[u32], work: &mut Vec<f64>) -> Option<f64> {
    col.gather(indices, work);
    signed_peak(work)
}

/// `_lap_trace_payload(table, laps, maximum_bins=..., steering_basis=basis)`.
pub fn lap_trace_payload(
    table: &Table,
    laps: &[PyValue],
    maximum_bins: i64,
    basis: &SteeringBasis,
) -> Result<PyValue, KernelError> {
    let length = table.len();
    let view = |aliases: &[&str]| Col::View(table.normalized(aliases, PyValue::None));
    let fraction = |aliases: &[&str]| Col::View(table.fractions(aliases, PyValue::None));
    let lap_distance = view(&["LapDistPct"]);
    let speed = view(&["Speed"]);
    let throttle = fraction(&["Throttle", "ThrottleRaw"]);
    let brake = fraction(&["Brake", "BrakeRaw"]);
    let steering_values = Col::View(table.steering_column(basis));
    let road_wheel = basis.road_wheel();
    let steering_stress_scale = basis.threshold(0.35);
    let steering_digits = if road_wheel { 6 } else { 5 };
    let gear = view(&["Gear"]);
    let rpm = view(&["RPM"]);
    let yaw_rate = view(&["YawRate"]);
    let velocity_x = view(&["VelocityX"]);
    let velocity_y = view(&["VelocityY"]);
    let lateral_accel = view(&["LatAccel"]);
    let longitudinal_accel = view(&["LongAccel"]);
    let latitude = view(&["Lat"]);
    let longitude = view(&["Lon"]);
    let session_time = view(&["SessionTime", "SessionTimeOfDay"]);
    let sectors = sector_start_pcts(&table.meta().session_info)?;

    let sources = additional_trace_sources(table, basis)?;
    let columns = Columns {
        lap_distance,
        speed,
        throttle,
        brake,
        steering: steering_values,
        gear,
        rpm,
        // _apply_steps(yaw, ((mul, 180.0), (truediv, math.pi))): no finite check.
        yaw_degrees: yaw_rate.map(|n| n * 180.0 / std::f64::consts::PI),
        sideslip: sideslip_column(&velocity_x, &velocity_y),
        lateral_g: lateral_accel.map(|n| n / 9.80665),
        longitudinal_g: longitudinal_accel.map(|n| n / 9.80665),
        latitude,
        longitude,
        session_time,
    };

    let mut traces: Vec<PyValue> = Vec::new();
    let mut scratch = Scratch::default();
    let mut assigned: Vec<(i64, i64)> = Vec::new();
    for lap in laps {
        let Some(lap) = lap.as_dict() else {
            return Err(KernelError::raises(
                "AttributeError",
                format!("'{}' object has no attribute 'get'", lap.type_name()),
            ));
        };
        let start = lap_index(lap, "start_index", 0.0)?;
        let end = lap_index(lap, "end_index", -1.0)?;
        if end < start {
            continue;
        }
        // range(start, min(end + 1, table.length)); a negative index counts from the end.
        let stop = (end + 1.0).min(length as f64);
        assigned.clear();
        if start < stop {
            if start < -(length as f64) {
                return Err(KernelError::raises("IndexError", "list index out of range"));
            }
            for index in (start as i64)..(stop as i64) {
                let Some(pct) = columns.lap_distance.at(position(index, length)) else {
                    continue;
                };
                if !(-0.01..=1.01).contains(&pct) {
                    continue;
                }
                let pct = coach_numerics::float_mod(pct, 1.0)?;
                let scaled = coach_numerics::int_from_float(pct * maximum_bins as f64)?;
                // min(maximum_bins - 1, max(0, scaled)): every sample's bin is
                // maximum_bins - 1 when maximum_bins < 1.
                let bucket = scaled.max(0).min(maximum_bins - 1);
                assigned.push((bucket, index));
            }
        }
        // defaultdict(list) visited by sorted(buckets): each bin's indices in sample order.
        assigned.sort_by_key(|&(bucket, _)| bucket);
        let mut points: Vec<PyValue> = Vec::new();
        let mut cursor = 0;
        let mut indices: Vec<i64> = Vec::new();
        let mut positions: Vec<u32> = Vec::new();
        while cursor < assigned.len() {
            let bucket = assigned[cursor].0;
            indices.clear();
            while cursor < assigned.len() && assigned[cursor].0 == bucket {
                indices.push(assigned[cursor].1);
                cursor += 1;
            }
            bin_positions(&indices, length, &mut positions)?;
            points.push(point(
                &columns,
                &sources.signals,
                &positions,
                indices.len(),
                bucket,
                maximum_bins,
                steering_stress_scale,
                steering_digits,
                &mut scratch,
            )?);
        }
        if points.is_empty() {
            continue;
        }
        traces.push(dict([
            ("lap", lap.get("lap").cloned().unwrap_or(PyValue::None)),
            (
                "lap_time_s",
                lap.get("lap_time_s").cloned().unwrap_or(PyValue::None),
            ),
            ("complete", PyValue::Bool(truthy(lap.get("complete"))?)),
            (
                "flag_state",
                lap.get("flag_state").cloned().unwrap_or(PyValue::None),
            ),
            ("flag_states", list_of(lap.get("flag_states"))?),
            (
                "green_fraction",
                lap.get("green_fraction").cloned().unwrap_or(PyValue::None),
            ),
            (
                "caution_fraction",
                lap.get("caution_fraction")
                    .cloned()
                    .unwrap_or(PyValue::None),
            ),
            (
                "pit_time_s",
                lap.get("pit_time_s").cloned().unwrap_or(PyValue::None),
            ),
            ("pit_entry", PyValue::Bool(truthy(lap.get("pit_entry"))?)),
            ("pit_exit", PyValue::Bool(truthy(lap.get("pit_exit"))?)),
            ("fuel_used_gal", fuel_used(lap)),
            ("conditions", dict_of(lap.get("conditions"))?),
            ("points", PyValue::List(points)),
        ]));
    }
    let trace_count = traces.len() as i64;
    Ok(dict([
        ("schema_version", PyValue::Int(2)),
        ("additional_signal_catalog", PyValue::List(sources.catalog)),
        ("distance_bins_per_lap", PyValue::Int(maximum_bins)),
        ("sector_start_pcts", sectors),
        ("trace_count", PyValue::Int(trace_count)),
        ("downsampling", DOWNSAMPLING.into()),
        ("steering", describe(basis)?),
        (
            "tire_stress",
            dict([
                ("evidence_class", "proxy".into()),
                ("version", "controls-load-proxy-v1".into()),
                ("definition", TIRE_STRESS_DEFINITION.into()),
            ]),
        ),
        ("traces", PyValue::List(traces)),
    ]))
}

fn truthy(value: Option<&PyValue>) -> Result<bool, KernelError> {
    value.map_or(Ok(false), PyValue::truthy)
}

/// One bin's point: the representatives in the order the oracle's dict literal writes them,
/// then `additional_signals` in catalog order.
#[allow(clippy::too_many_arguments)]
fn point(
    columns: &Columns,
    signals: &[super::signals::Signal],
    indices: &[u32],
    samples: usize,
    bucket: i64,
    maximum_bins: i64,
    steering_stress_scale: f64,
    steering_digits: i64,
    scratch: &mut Scratch,
) -> Result<PyValue, KernelError> {
    let Scratch {
        speeds,
        throttles,
        brakes,
        steerings,
        work,
    } = scratch;
    columns.speed.gather(indices, speeds);
    columns.throttle.gather(indices, throttles);
    columns.brake.gather(indices, brakes);
    columns.steering.gather(indices, steerings);

    let speed_min = first_min(speeds);
    let speed_max = first_max(speeds);
    let throttle_minimum = first_min(throttles);
    let brake_peak = first_max(brakes);
    let brake_mean = if brakes.is_empty() {
        None
    } else {
        Some(coach_numerics::fmean(brakes)?)
    };
    let representative_steering = signed_peak(steerings);
    // max(map(abs, steerings), default=None)
    let steering_abs_peak = steerings.iter().map(|value| value.abs()).fold(
        None,
        |best: Option<f64>, value| match best {
            Some(best) if value > best => Some(value),
            Some(best) => Some(best),
            None => Some(value),
        },
    );
    let representative_speed = median_in_place(speeds);
    let throttle_median = median_in_place(throttles);
    let session_time = median_of(&columns.session_time, indices, work);

    columns.gear.gather(indices, work);
    let gear = match median_in_place(work) {
        Some(median) => PyValue::Int(coach_numerics::round_to_int(median)?),
        None => PyValue::None,
    };
    let rpm = median_of(&columns.rpm, indices, work);
    let slip_angle = peak_of(&columns.sideslip, indices, work);
    let yaw = peak_of(&columns.yaw_degrees, indices, work);
    let representative_lateral_g = peak_of(&columns.lateral_g, indices, work);
    let longitudinal_g = peak_of(&columns.longitudinal_g, indices, work);
    let latitude = median_of(&columns.latitude, indices, work);
    let longitude = median_of(&columns.longitude, indices, work);

    // speed_scale = min(1.5, max(0.0, (representative_speed or 0.0) / 80.0))
    let scaled = or_zero(representative_speed) / 80.0;
    let scaled = if scaled > 0.0 { scaled } else { 0.0 };
    let speed_scale = if scaled < 1.5 { scaled } else { 1.5 };
    let cap = |value: f64| if value < 1.0 { value } else { 1.0 };
    let stress = [
        cap(or_zero(brake_peak) * speed_scale),
        cap(or_zero(representative_steering).abs() / steering_stress_scale * speed_scale),
        cap(or_zero(representative_lateral_g).abs() / 2.0),
    ];
    let tire_stress = 0.35 * stress[0] + 0.35 * stress[1] + 0.30 * stress[2];

    let mut additional = Dict::with_capacity(signals.len());
    for signal in signals {
        signal.series.gather(indices, work);
        if work.is_empty() {
            continue;
        }
        let representative = match signal.reducer {
            Reducer::Max => first_max(work),
            Reducer::SignedPeak => signed_peak(work),
            Reducer::Last => work.last().copied(),
            Reducer::Median => median_in_place(work),
        };
        if let Some(value) = representative.filter(|value| value.is_finite()) {
            additional.insert(
                signal.id.as_str(),
                PyValue::Float(coach_numerics::round_ndigits(value, signal.precision)?),
            );
        }
    }

    // (bucket + 0.5) / maximum_bins
    if maximum_bins == 0 {
        return Err(KernelError::raises(
            "ZeroDivisionError",
            "float division by zero",
        ));
    }
    let lap_pct = (bucket as f64 + 0.5) / maximum_bins as f64;
    let mph = |value: Option<f64>| value.map(|value| value * 2.236936);
    Ok(dict([
        (
            "lap_pct",
            PyValue::Float(coach_numerics::round_ndigits(lap_pct, 6)?),
        ),
        ("session_time_s", round_opt(session_time, 6)?),
        (
            "speed_mph",
            round_opt(Some(or_zero(representative_speed) * 2.236936), 3)?,
        ),
        ("speed_min_mph", round_opt(mph(speed_min), 3)?),
        ("speed_max_mph", round_opt(mph(speed_max), 3)?),
        ("throttle", round_opt(throttle_median, 4)?),
        ("throttle_min", round_opt(throttle_minimum, 4)?),
        ("brake", round_opt(brake_peak, 4)?),
        ("brake_mean", round_opt(brake_mean, 4)?),
        (
            "steering_rad",
            round_opt(representative_steering, steering_digits)?,
        ),
        (
            "steering_abs_peak_rad",
            round_opt(steering_abs_peak, steering_digits)?,
        ),
        ("gear", gear),
        ("rpm", round_opt(rpm, 0)?),
        ("slip_angle_deg", round_opt(slip_angle, 3)?),
        ("yaw_rate_deg_s", round_opt(yaw, 3)?),
        ("lateral_g", round_opt(representative_lateral_g, 4)?),
        ("longitudinal_g", round_opt(longitudinal_g, 4)?),
        ("latitude", round_opt(latitude, 8)?),
        ("longitude", round_opt(longitude, 8)?),
        ("tire_stress_proxy", round_opt(Some(tire_stress), 4)?),
        ("additional_signals", PyValue::Dict(additional)),
        ("samples", PyValue::Int(samples as i64)),
    ]))
}

#[cfg(test)]
mod tests {
    use super::bin_positions;

    fn positions(indices: &[i64], length: usize) -> Vec<u32> {
        let mut out = Vec::new();
        bin_positions(indices, length, &mut out).expect("positions");
        out
    }

    // CPython 3.12, `col = list(range(10))`: `col[3:6]`, `[col[i] for i in (5, 9)]`,
    // `col[-3:-1]` and `col[-2:1]` (a run across index 0 is an empty slice).
    #[test]
    fn bins_read_the_oracles_slice_or_indices() {
        assert_eq!(positions(&[3, 4, 5], 10), [3, 4, 5]);
        assert_eq!(positions(&[5, 9], 10), [5, 9]);
        assert_eq!(positions(&[-3, -2], 10), [7, 8]);
        assert_eq!(positions(&[-2, -1, 0], 10), [] as [u32; 0]);
    }
}

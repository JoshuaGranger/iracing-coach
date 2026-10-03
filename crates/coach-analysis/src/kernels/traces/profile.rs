//! `_track_profile`: per-bin medians of clean green samples over the lap distance, the
//! telemetry-derived load zones (`detected_corner_segments`, read by units U4 and U5) and the
//! setup traces.

use std::collections::BTreeSet;
use std::rc::Rc;

use super::super::error::KernelError;
use super::super::table::{
    SeriesItem, SetupSeries, SteeringBasis, Table, caution_flag_series, normalize_view,
};
use super::super::value::{Dict, PyValue};
use super::{
    Col, dict, finite_only, median_in_place, median_of_sorted, or_zero, percentile_of_sorted,
    round_opt, sort_floats,
};

const CAUTION_FLAGS: i64 = 0x0008 | 0x0100 | 0x0200 | 0x4000 | 0x8000;
const TIRES: [&str; 4] = ["LF", "RF", "LR", "RR"];
const SEGMENT_NOTE: &str = "Segments are telemetry-derived load zones; cached track references \
                            may supply official corner names.";

#[derive(Clone, Copy, PartialEq, Eq)]
enum Kind {
    Height,
    ShockDeflection,
    ShockVelocity,
    Pressure,
    Temperature,
}

/// One setup trace in `add_setup_trace` order: its kind, key prefix and normalised values.
pub struct SetupTrace {
    kind: Kind,
    prefix: String,
    values: Col,
}

/// The setup traces of a `_setup_channel_series` result in the order `add_setup_trace`
/// registers them: center-front splitter, the four corner ride heights, the dynamic rear, then
/// per shock its deflection and velocity, then per tire its live pressure and temperatures.
/// Each trace's values are `_normalize_column(values)[0]`.
pub fn setup_traces(series: &SetupSeries) -> Vec<SetupTrace> {
    let mut traces = Vec::new();
    let mut add = |kind: Kind, prefix: String, item: Option<&SeriesItem>| {
        if let Some(item) = item {
            traces.push(SetupTrace {
                kind,
                prefix,
                values: Col::View(Rc::new(normalize_view(&item.values))),
            });
        }
    };
    fn find<'a>(items: &'a [(String, SeriesItem)], name: &str) -> Option<&'a SeriesItem> {
        items
            .iter()
            .find(|(key, _)| key == name)
            .map(|(_, item)| item)
    }
    add(
        Kind::Height,
        "center_front_splitter".to_owned(),
        series.get_platform("center_front_splitter"),
    );
    for tire in TIRES {
        add(
            Kind::Height,
            format!("{}_ride_height", tire.to_lowercase()),
            series.get_platform(tire),
        );
    }
    add(
        Kind::Height,
        "dynamic_rear".to_owned(),
        series.get_platform("dynamic_rear"),
    );
    for (tire, shock) in &series.shocks {
        let prefix = tire.to_lowercase();
        add(
            Kind::ShockDeflection,
            format!("{prefix}_shock"),
            find(shock, "deflection"),
        );
        add(
            Kind::ShockVelocity,
            format!("{prefix}_shock"),
            find(shock, "velocity"),
        );
    }
    for (tire, tire_series) in &series.tires {
        let prefix = tire.to_lowercase();
        add(
            Kind::Pressure,
            format!("{prefix}_tire_pressure"),
            tire_series.live_pressure.as_ref(),
        );
        for (position, item) in &tire_series.temperatures {
            add(
                Kind::Temperature,
                format!("{prefix}_tire_temp_{}", position.to_lowercase()),
                Some(item),
            );
        }
    }
    traces
}

/// `_round(_median(values), digits)` of finite-or-not floats: `_finite_values` drops the
/// non-finite ones.
fn median_rounded(values: &mut Vec<f64>, digits: i64) -> Result<PyValue, KernelError> {
    finite_only(values);
    round_opt(median_in_place(values), digits)
}

/// `_track_profile(table, bins, setup_series)`.
pub fn track_profile(
    table: &Table,
    bins: i64,
    basis: &dyn Fn() -> Result<SteeringBasis, KernelError>,
    setup: &dyn Fn() -> Result<Vec<SetupTrace>, KernelError>,
) -> Result<PyValue, KernelError> {
    if !table.has(&["LapDistPct"]) || table.is_empty() {
        return Ok(PyValue::None);
    }
    if bins < 1 {
        return Err(KernelError::internal(format!(
            "bins {bins} is not ported (the oracle's default is 200)"
        )));
    }
    let bin_count =
        usize::try_from(bins).map_err(|_| KernelError::internal("bins beyond usize"))?;
    let length = table.len();
    let pct = Col::View(table.normalized(&["LapDistPct"], PyValue::None));
    let speed = Col::View(table.normalized(&["Speed"], PyValue::None));
    let brake = Col::View(table.fractions(&["Brake", "BrakeRaw"], PyValue::None));
    let throttle = Col::View(table.fractions(&["Throttle", "ThrottleRaw"], PyValue::None));
    let steering = basis()?;
    let steering = &steering;
    let steering_values = Col::View(table.steering_column(steering));
    let steering_digits: i64 = if steering.road_wheel() { 4 } else { 3 };
    let lat_accel = Col::View(table.normalized(&["LatAccel"], PyValue::None));
    let flags = caution_flag_series(table);
    let pit = table.booleans(&["OnPitRoad"], PyValue::Bool(false));
    let latitude = Col::View(table.normalized(&["Lat"], PyValue::None));
    let longitude = Col::View(table.normalized(&["Lon"], PyValue::None));
    let setup = setup()?;

    // The samples of each bin, in sample order: non-pit, non-caution samples with a valid lap
    // distance.
    let mut bin_samples: Vec<Vec<u32>> = vec![Vec::new(); bin_count];
    for index in 0..length {
        let Some(position) = pct.at(index) else {
            continue;
        };
        if position < 0.0 || pit[index] {
            continue;
        }
        if flags[index] & CAUTION_FLAGS != 0 {
            continue;
        }
        let scaled = coach_numerics::int_from_float(
            coach_numerics::float_mod(position, 1.0)? * bins as f64,
        )?;
        let bin = scaled.clamp(0, bins - 1) as usize;
        bin_samples[bin].push(index as u32);
    }

    let mut profile: Vec<Dict> = Vec::new();
    let mut setup_trace_fields: BTreeSet<String> = BTreeSet::new();
    let mut work: Vec<f64> = Vec::new();
    for (index, indices) in bin_samples.iter().enumerate() {
        // speed, steering and lateral acceleration have a value for every sample, so a bin
        // enters the profile exactly when it has samples.
        if indices.is_empty() {
            continue;
        }
        let gather_all = |f: &dyn Fn(Option<f64>) -> f64, col: &Col, out: &mut Vec<f64>| {
            out.clear();
            out.extend(indices.iter().map(|&i| f(col.at(i as usize))));
        };
        gather_all(&|v| or_zero(v) * 2.236936, &speed, &mut work);
        let samples = work.len() as i64;
        let speed_mph = median_rounded(&mut work, 3)?;
        brake.gather(indices, &mut work);
        let brake_median = median_rounded(&mut work, 3)?;
        throttle.gather(indices, &mut work);
        let throttle_median = median_rounded(&mut work, 3)?;
        gather_all(&|v| or_zero(v).abs(), &steering_values, &mut work);
        let steering_median = median_rounded(&mut work, steering_digits)?;
        gather_all(&|v| or_zero(v).abs() / 9.80665, &lat_accel, &mut work);
        let lateral = median_rounded(&mut work, 3)?;
        latitude.gather(indices, &mut work);
        let lat = median_rounded(&mut work, 6)?;
        longitude.gather(indices, &mut work);
        let lon = median_rounded(&mut work, 6)?;
        let lap_pct = (index as f64 + 0.5) / bins as f64;
        let mut record = Dict::with_capacity(10);
        record.insert("bin", PyValue::Int(index as i64));
        record.insert(
            "lap_pct",
            PyValue::Float(coach_numerics::round_ndigits(lap_pct, 5)?),
        );
        record.insert("samples", PyValue::Int(samples));
        record.insert("speed_mph", speed_mph);
        record.insert("brake", brake_median);
        record.insert("throttle", throttle_median);
        record.insert("steering_abs_rad", steering_median);
        record.insert("lateral_g", lateral);
        record.insert("lat", lat);
        record.insert("lon", lon);
        for trace in &setup {
            // A setup trace shorter than the table contributes its samples that exist.
            work.clear();
            work.extend(
                indices
                    .iter()
                    .filter(|&&i| (i as usize) < trace.values.len())
                    .filter_map(|&i| trace.values.at(i as usize)),
            );
            if work.is_empty() {
                continue;
            }
            let additions = setup_additions(trace, &mut work)?;
            for (key, value) in additions {
                setup_trace_fields.insert(key.clone());
                record.insert(key, value);
            }
        }
        profile.push(record);
    }
    if profile.is_empty() {
        return Ok(PyValue::None);
    }

    let field = |record: &Dict, key: &str| record.get(key).and_then(PyValue::as_float);
    let percentile = |key: &str, p: f64| -> Result<Option<f64>, KernelError> {
        let mut values: Vec<f64> = profile
            .iter()
            .filter_map(|record| field(record, key))
            .collect();
        finite_only(&mut values);
        sort_floats(&mut values);
        percentile_of_sorted(&values, p)
    };
    let steer_threshold = or_zero(percentile("steering_abs_rad", 0.55)?);
    let lat_threshold = or_zero(percentile("lateral_g", 0.55)?);
    let brake_percentile = match percentile("brake", 0.65)? {
        Some(value) if value != 0.0 => value,
        _ => 0.04,
    };
    let brake_threshold = if brake_percentile > 0.04 {
        brake_percentile
    } else {
        0.04
    };
    let mut active: Vec<i64> = Vec::new();
    for record in &profile {
        let steer = or_zero(field(record, "steering_abs_rad"));
        let lateral = or_zero(field(record, "lateral_g"));
        let brake = or_zero(field(record, "brake"));
        if steer >= steer_threshold && (lateral >= lat_threshold || brake >= brake_threshold) {
            active.push(
                record
                    .get("bin")
                    .and_then(PyValue::as_int)
                    .unwrap_or_default(),
            );
        }
    }
    // Profile records are in bin order, so the active bins already are sorted(active_bins).
    let mut segments: Vec<Vec<i64>> = Vec::new();
    for bin in active {
        match segments.last_mut() {
            Some(segment) if segment.last().is_some_and(|&last| bin == last + 1) => {
                segment.push(bin)
            }
            _ => segments.push(vec![bin]),
        }
    }
    if segments.len() > 1
        && segments[0][0] == 0
        && segments.last().and_then(|segment| segment.last()) == Some(&(bins - 1))
    {
        let mut last = segments.pop().unwrap_or_default();
        last.extend_from_slice(&segments[0]);
        segments[0] = last;
    }
    let by_bin = |bin: i64| {
        profile
            .iter()
            .find(|record| record.get("bin").and_then(PyValue::as_int) == Some(bin))
    };
    let minimum_length = 2.max(bins / 100) as usize;
    let mut summaries: Vec<PyValue> = Vec::new();
    for raw in &segments {
        if raw.len() < minimum_length {
            continue;
        }
        let records: Vec<&Dict> = raw.iter().filter_map(|&bin| by_bin(bin)).collect();
        if records.is_empty() {
            continue;
        }
        // min(records, key=speed_mph if not None else inf): the first of the slowest.
        let key = |record: &Dict| field(record, "speed_mph").unwrap_or(f64::INFINITY);
        let minimum = records.iter().skip(1).fold(records[0], |best, &record| {
            if key(record) < key(best) {
                record
            } else {
                best
            }
        });
        let median_of_key = |name: &str, digits: i64| -> Result<PyValue, KernelError> {
            let mut values: Vec<f64> = records
                .iter()
                .filter_map(|record| field(record, name))
                .collect();
            finite_only(&mut values);
            round_opt(median_in_place(&mut values), digits)
        };
        let first = raw[0];
        let last = *raw.last().unwrap_or(&first);
        let start = coach_numerics::int_true_div(first.rem_euclid(bins), bins)?;
        let end = coach_numerics::int_true_div((last + 1).rem_euclid(bins), bins)?;
        summaries.push(dict([
            ("segment", PyValue::Int(summaries.len() as i64 + 1)),
            ("start_pct", round_opt(Some(start), 4)?),
            ("end_pct", round_opt(Some(end), 4)?),
            ("wraps_start_finish", PyValue::Bool(first > last)),
            (
                "minimum_speed_mph",
                minimum.get("speed_mph").cloned().unwrap_or(PyValue::None),
            ),
            (
                "minimum_speed_pct",
                minimum.get("lap_pct").cloned().unwrap_or(PyValue::None),
            ),
            ("median_brake", median_of_key("brake", 3)?),
            (
                "median_steering_rad",
                median_of_key("steering_abs_rad", steering_digits)?,
            ),
            ("median_lateral_g", median_of_key("lateral_g", 3)?),
        ]));
    }
    let shape: Vec<PyValue> = profile
        .iter()
        .filter(|record| field(record, "lat").is_some() && field(record, "lon").is_some())
        .map(|record| {
            dict([
                (
                    "lap_pct",
                    record.get("lap_pct").cloned().unwrap_or(PyValue::None),
                ),
                ("x", record.get("lon").cloned().unwrap_or(PyValue::None)),
                ("y", record.get("lat").cloned().unwrap_or(PyValue::None)),
            ])
        })
        .collect();
    Ok(dict([
        ("bins", PyValue::Int(bins)),
        (
            "profile",
            PyValue::List(profile.into_iter().map(PyValue::Dict).collect()),
        ),
        ("detected_corner_segments", PyValue::List(summaries)),
        (
            "shape",
            if shape.is_empty() {
                PyValue::None
            } else {
                PyValue::List(shape)
            },
        ),
        (
            "setup_trace_fields",
            PyValue::List(setup_trace_fields.into_iter().map(PyValue::Str).collect()),
        ),
        ("segment_note", SEGMENT_NOTE.into()),
    ]))
}

/// The per-bin keys one setup trace adds, in the oracle's order. `values` holds the bin's
/// finite values in sample order.
fn setup_additions(
    trace: &SetupTrace,
    values: &mut [f64],
) -> Result<Vec<(String, PyValue)>, KernelError> {
    let prefix = &trace.prefix;
    let minimum = super::first_min(values);
    let maximum = super::first_max(values);
    Ok(match trace.kind {
        Kind::Height | Kind::Pressure | Kind::Temperature => {
            let (unit, digits) = match trace.kind {
                Kind::Height => ("in", 4),
                Kind::Pressure => ("psi", 2),
                _ => ("f", 1),
            };
            sort_floats(values);
            vec![
                (format!("{prefix}_min_{unit}"), round_opt(minimum, digits)?),
                (
                    format!("{prefix}_p05_{unit}"),
                    round_opt(percentile_of_sorted(values, 0.05)?, digits)?,
                ),
                (
                    format!("{prefix}_median_{unit}"),
                    round_opt(Some(median_of_sorted(values)), digits)?,
                ),
                (format!("{prefix}_max_{unit}"), round_opt(maximum, digits)?),
            ]
        }
        Kind::ShockDeflection => vec![
            (
                format!("{prefix}_deflection_min_in"),
                round_opt(minimum, 4)?,
            ),
            (
                format!("{prefix}_deflection_max_in"),
                round_opt(maximum, 4)?,
            ),
            (
                format!("{prefix}_deflection_range_in"),
                round_opt(maximum.zip(minimum).map(|(high, low)| high - low), 4)?,
            ),
        ],
        Kind::ShockVelocity => {
            for value in values.iter_mut() {
                *value = value.abs();
            }
            sort_floats(values);
            vec![
                (
                    format!("{prefix}_abs_velocity_p90_in_s"),
                    round_opt(percentile_of_sorted(values, 0.90)?, 3)?,
                ),
                (
                    format!("{prefix}_abs_velocity_p99_in_s"),
                    round_opt(percentile_of_sorted(values, 0.99)?, 3)?,
                ),
            ]
        }
    })
}

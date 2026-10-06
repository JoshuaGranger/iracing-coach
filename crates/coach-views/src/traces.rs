//! Lap traces: the trace catalog and the CCOL columnar container (`traces.ccol`), plus
//! `select_traces`, which serves a subset of it. A channel copies one backend `lap_traces` point key
//! (`TraceSignal.source_key`) in the backend's unit, laps x bins, lap-major.

use std::collections::HashSet;
use std::fs::File;
use std::io::{self, Read, Seek, SeekFrom, Write};
use std::path::Path;

use serde_json::{Map, Value, json};

use crate::laps::LapRecord;
use crate::parse::{CORE_COUNT, LapTraces, RawTrace};
use crate::val::{arr, int, int_of, nonblank, num_of, text};
use crate::{Envelope, ProjectError, SelectError};

/// `TraceCatalog.signals` `maxItems` in `api.openapi.yaml`; signals past it are listed in
/// `omitted_signals` and not served.
pub(crate) const MAX_SIGNALS: usize = 128;
const DEFAULT_BINS: usize = 160;
const MAX_BINS: usize = 4096;
const PREAMBLE_LEN: usize = 12;
/// Index of `session-time` in `CORE`.
const SESSION_TIME: usize = 1;

pub(crate) struct CoreSignal {
    pub id: &'static str,
    pub source_key: &'static str,
    pub label: &'static str,
    pub short_label: &'static str,
    pub unit: &'static str,
    pub category: &'static str,
    pub default: bool,
    pub range: Option<(f64, f64)>,
}

/// Fixed channels, in `parse::CORE_COUNT` order (the parser's point-key table).
pub(crate) const CORE: [CoreSignal; CORE_COUNT] = [
    core(
        "speed",
        "speed_mph",
        "Speed",
        "Speed",
        "mph",
        "Pace",
        true,
        None,
    ),
    core(
        "session-time",
        "session_time_s",
        "Session time",
        "Time",
        "s",
        "Pace",
        true,
        None,
    ),
    core(
        "throttle",
        "throttle",
        "Throttle",
        "T",
        "ratio",
        "Controls",
        true,
        Some((0.0, 1.0)),
    ),
    core(
        "brake",
        "brake",
        "Brake",
        "B",
        "ratio",
        "Controls",
        true,
        Some((0.0, 1.0)),
    ),
    core(
        "steering",
        "steering_rad",
        "Steering",
        "Steer",
        "rad",
        "Controls",
        true,
        None,
    ),
    core(
        "steering-peak",
        "steering_abs_peak_rad",
        "Steering peak",
        "Peak",
        "rad",
        "Controls",
        false,
        None,
    ),
    core(
        "slip",
        "slip_angle_deg",
        "Slip angle",
        "Slip",
        "deg",
        "Vehicle",
        true,
        None,
    ),
    core(
        "yaw",
        "yaw_rate_deg_s",
        "Yaw rate",
        "Yaw",
        "deg/s",
        "Vehicle",
        false,
        None,
    ),
    core(
        "lateral-g",
        "lateral_g",
        "Lateral G",
        "Lat",
        "g",
        "Vehicle",
        false,
        None,
    ),
    core(
        "longitudinal-g",
        "longitudinal_g",
        "Longitudinal G",
        "Long",
        "g",
        "Vehicle",
        false,
        None,
    ),
    core(
        "gear", "gear", "Gear", "Gear", "gear", "Vehicle", false, None,
    ),
    core("rpm", "rpm", "RPM", "RPM", "rpm", "Vehicle", false, None),
    core(
        "latitude", "latitude", "Latitude", "Lat", "deg", "Position", true, None,
    ),
    core(
        "longitude",
        "longitude",
        "Longitude",
        "Lon",
        "deg",
        "Position",
        true,
        None,
    ),
    core(
        "tire-stress",
        "tire_stress_proxy",
        "Tire load proxy",
        "Load",
        "ratio",
        "Tires",
        false,
        None,
    ),
];

#[allow(clippy::too_many_arguments)]
const fn core(
    id: &'static str,
    source_key: &'static str,
    label: &'static str,
    short_label: &'static str,
    unit: &'static str,
    category: &'static str,
    default: bool,
    range: Option<(f64, f64)>,
) -> CoreSignal {
    CoreSignal {
        id,
        source_key,
        label,
        short_label,
        unit,
        category,
        default,
        range,
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum Dtype {
    F64,
    I16,
    U8,
}

impl Dtype {
    fn name(self) -> &'static str {
        match self {
            Dtype::F64 => "f64",
            Dtype::I16 => "i16",
            Dtype::U8 => "u8",
        }
    }

    fn parse(name: &str) -> Option<Dtype> {
        match name {
            "f64" => Some(Dtype::F64),
            "i16" => Some(Dtype::I16),
            "u8" => Some(Dtype::U8),
            _ => None,
        }
    }

    fn width(self) -> usize {
        match self {
            Dtype::F64 => 8,
            Dtype::I16 => 2,
            Dtype::U8 => 1,
        }
    }
}

#[derive(Debug, Clone, Copy)]
pub(crate) enum Source {
    Core(usize),
    Extra(usize),
}

pub(crate) struct Signal {
    pub id: String,
    pub source: Source,
    pub source_key: String,
    pub label: String,
    pub short_label: Option<String>,
    pub unit: String,
    pub digits: Option<u8>,
    pub category: Option<String>,
    pub range: Option<(f64, f64)>,
    pub default: bool,
    pub dtype: Dtype,
    /// Sentinel of an integer channel; floats use NaN.
    pub missing: Option<i64>,
}

pub(crate) struct Prepared {
    pub bins: usize,
    pub laps: Vec<i64>,
    pub bin_index: Vec<Vec<u16>>,
    pub signals: Vec<Signal>,
    pub omitted: Vec<String>,
}

#[derive(Clone, Copy)]
struct Stats {
    present: bool,
    all_integer: bool,
    min: f64,
    max: f64,
}

fn stats(columns: impl Iterator<Item = f64>) -> Stats {
    let mut s = Stats {
        present: false,
        all_integer: true,
        min: f64::INFINITY,
        max: f64::NEG_INFINITY,
    };
    for v in columns {
        if v.is_nan() {
            continue;
        }
        s.present = true;
        s.min = s.min.min(v);
        s.max = s.max.max(v);
        if v.fract() != 0.0 || (v == 0.0 && v.is_sign_negative()) || v.abs() > 1.0e9 {
            s.all_integer = false;
        }
    }
    s
}

/// The narrowest exact encoding: integers in range with a sentinel outside it, else f64.
fn dtype_for(s: Stats) -> (Dtype, Option<i64>) {
    if s.all_integer {
        if s.min >= 0.0 && s.max <= 254.0 {
            return (Dtype::U8, Some(255));
        }
        if s.min >= -32767.0 && s.max <= 32767.0 {
            return (Dtype::I16, Some(-32768));
        }
    }
    (Dtype::F64, None)
}

fn column(trace: &RawTrace, source: Source) -> &[f64] {
    match source {
        Source::Core(i) => &trace.core[i],
        Source::Extra(i) => &trace.extras[i],
    }
}

fn short_label(id: &str) -> Option<String> {
    if let Some((prefix, rest)) = id.split_once('-')
        && matches!(prefix, "lf" | "rf" | "lr" | "rr")
    {
        let suffix = match rest {
            "wheel-speed" => "",
            "wheel-slip" => " slip",
            "pressure" => " psi",
            "carcass-temp" => " temp",
            "surface-temp" => " surf",
            "ride-height" => " RH",
            "shock-deflection" => " shock",
            "shock-velocity" => " vel",
            _ => return None,
        };
        return Some(format!("{}{suffix}", prefix.to_uppercase()));
    }
    let label = match id {
        "tire-wear" => "Wear",
        "steering-wheel-angle" => "Wheel",
        "vertical-g" => "Vert",
        "clutch" => "C",
        "abs-active" => "ABS",
        "abs-cut" => "ABS cut",
        "brake-bias" => "Bias",
        "steering-torque" => "Torque",
        "pitch" | "pitch-rate" => "Pitch",
        "roll" | "roll-rate" => "Roll",
        "fuel-level" => "Fuel",
        "fuel-use-rate" => "Fuel rate",
        "center-front-ride-height" => "Front RH",
        "track-temperature" => "Track",
        "air-temperature" => "Air",
        "wind-speed" => "Wind",
        "humidity" => "Humidity",
        "fog" => "Fog",
        "precipitation" => "Rain",
        "air-pressure" => "Pressure",
        "air-density" => "Density",
        "track-wetness" => "Wetness",
        "track-usage" => "Usage",
        "weather-wet" => "Wet",
        "overall-position" => "Pos",
        "class-position" => "Class",
        "distance-ahead" => "Ahead",
        "distance-behind" => "Behind",
        "on-pit-road" => "Pit",
        "track-surface" => "Surface",
        _ => return None,
    };
    Some(label.to_string())
}

fn json_error(path: String, message: &str) -> ProjectError {
    ProjectError::Json {
        path,
        message: message.to_string(),
    }
}

/// Seals the parsed traces and decides the channels. `road_wheel` names the steering signal.
pub(crate) fn prepare(
    lt: &mut LapTraces,
    road_wheel: bool,
    steering_digits: u8,
) -> Result<Prepared, ProjectError> {
    let bins = match &lt.bins {
        None | Some(Value::Null) => DEFAULT_BINS,
        Some(value) => int_of(value)
            .filter(|b| (1..=MAX_BINS as i64).contains(b))
            .map(|b| b as usize)
            .ok_or_else(|| {
                json_error(
                    "lap_traces.distance_bins_per_lap".into(),
                    "expected an integer from 1 to 4096",
                )
            })?,
    };
    let extra_count = lt.extra_ids.len();
    let mut laps = Vec::with_capacity(lt.traces.len());
    let mut seen = HashSet::new();
    let mut bin_index = Vec::with_capacity(lt.traces.len());
    let mut taken = vec![false; bins];
    for (index, trace) in lt.traces.iter_mut().enumerate() {
        trace.seal(extra_count);
        let lap = trace.meta.get("lap").and_then(int_of).ok_or_else(|| {
            json_error(
                format!("lap_traces.traces[{index}].lap"),
                "expected an integer",
            )
        })?;
        if !seen.insert(lap) {
            return Err(json_error(
                format!("lap_traces.traces[{index}].lap"),
                &format!("lap {lap} has more than one trace"),
            ));
        }
        taken.fill(false);
        let mut indices = Vec::with_capacity(trace.points);
        for (point, pct) in trace.lap_pct.iter().enumerate() {
            let path = || format!("lap_traces.traces[{index}].points[{point}].lap_pct");
            if !pct.is_finite() || *pct < 0.0 {
                return Err(json_error(path(), "expected a lap fraction from 0 to 1"));
            }
            let bin = ((pct * bins as f64).floor() as usize).min(bins - 1);
            if std::mem::replace(&mut taken[bin], true) {
                return Err(json_error(
                    path(),
                    &format!("two points fall in distance bin {bin}"),
                ));
            }
            indices.push(bin as u16);
        }
        laps.push(lap);
        bin_index.push(indices);
    }

    let mut signals: Vec<Signal> = Vec::new();
    for (i, spec) in CORE.iter().enumerate() {
        let s = stats(lt.traces.iter().flat_map(|t| t.core[i].iter().copied()));
        if !s.present {
            continue;
        }
        let (dtype, missing) = dtype_for(s);
        let steering = matches!(spec.id, "steering" | "steering-peak");
        let label = match (spec.id, road_wheel) {
            ("steering", true) => "Steering (road wheel)",
            ("steering", false) => "Steering wheel angle",
            _ => spec.label,
        };
        signals.push(Signal {
            id: spec.id.into(),
            source: Source::Core(i),
            source_key: spec.source_key.into(),
            label: label.into(),
            short_label: Some(spec.short_label.into()),
            unit: spec.unit.into(),
            digits: steering.then_some(steering_digits),
            category: Some(spec.category.into()),
            range: spec.range,
            default: spec.default,
            dtype,
            missing,
        });
    }

    // Additional signals in the backend catalog's order, then any only the points name.
    let core_ids: HashSet<&str> = CORE.iter().map(|c| c.id).collect();
    let mut order: Vec<usize> = Vec::new();
    let mut meta: Vec<Option<&Map<String, Value>>> = vec![None; extra_count];
    if let Some(Value::Array(entries)) = &lt.signal_catalog {
        for entry in entries.iter().filter_map(Value::as_object) {
            let Some(id) = nonblank(entry, "id") else {
                continue;
            };
            if let Some(index) = lt.extra_ids.iter().position(|e| *e == id)
                && !order.contains(&index)
            {
                order.push(index);
                meta[index] = Some(entry);
            }
        }
    }
    let rest: Vec<usize> = (0..extra_count).filter(|i| !order.contains(i)).collect();
    order.extend(rest);
    let mut omitted = Vec::new();
    for index in order {
        let id = &lt.extra_ids[index];
        if core_ids.contains(id.as_str()) {
            continue;
        }
        let s = stats(
            lt.traces
                .iter()
                .flat_map(|t| t.extras[index].iter().copied()),
        );
        if !s.present {
            continue;
        }
        if signals.len() >= MAX_SIGNALS {
            omitted.push(id.clone());
            continue;
        }
        let (dtype, missing) = dtype_for(s);
        let entry = meta[index];
        signals.push(Signal {
            id: id.clone(),
            source: Source::Extra(index),
            source_key: format!("additional_signals.{id}"),
            label: entry
                .and_then(|e| nonblank(e, "name"))
                .or_else(|| crate::tuning::humanize(id))
                .unwrap_or_else(|| id.clone()),
            short_label: short_label(id),
            unit: entry.and_then(|e| text(e, "unit")).unwrap_or_default(),
            digits: None,
            category: entry.and_then(|e| nonblank(e, "category")),
            range: None,
            default: false,
            dtype,
            missing,
        });
    }
    Ok(Prepared {
        bins,
        laps,
        bin_index,
        signals,
        omitted,
    })
}

/// [`prepare`] for `lap_trace_columns`, its error paths named after it.
pub(crate) fn prepare_columns(
    lt: &mut LapTraces,
    road_wheel: bool,
    steering_digits: u8,
) -> Result<Prepared, ProjectError> {
    prepare(lt, road_wheel, steering_digits).map_err(|error| match error {
        ProjectError::Json { path, message } => ProjectError::Json {
            path: path.replacen("lap_traces", "lap_trace_columns", 1),
            message,
        },
        other => other,
    })
}

fn signal_json(signal: &Signal) -> Value {
    json!({
        "id": signal.id,
        "source_key": signal.source_key,
        "label": signal.label,
        "short_label": signal.short_label,
        "unit": signal.unit,
        "digits": signal.digits,
        "category": signal.category,
        "minimum": signal.range.map(|r| r.0),
        "maximum": signal.range.map(|r| r.1),
        "dtype": signal.dtype.name(),
        "default": signal.default,
    })
}

/// Session time at `target` (a lap fraction), linearly interpolated between the two distance bins
/// that straddle it (`TelemetryWorkspace.TimeAtPercent`). `None` when the lap's bins stop more than
/// two bins short of the target, so a partial lap has no time at a boundary it never reached.
fn time_at(points: &[(f64, f64)], target: f64, bins: usize) -> Option<f64> {
    let slack = 2.0 / bins as f64;
    let (first, last) = (points.first()?, points.last()?);
    if target < first.0 - slack || target > last.0 + slack {
        return None;
    }
    if target <= first.0 {
        return Some(first.1);
    }
    if target >= last.0 {
        return Some(last.1);
    }
    let after = points.iter().position(|p| p.0 >= target)?;
    let (before, after) = (points[after - 1], points[after]);
    let span = after.0 - before.0;
    if span <= 1e-6 {
        return Some(after.1);
    }
    Some(before.1 + (after.1 - before.1) * ((target - before.0) / span))
}

/// Seconds spent in each sector (`sector_times_s`): the session-time difference between the sector's
/// boundaries, from the lap's `session-time` bins. `None` for a sector the lap's bins do not span,
/// whose session time is missing, or whose end reads before its start. The last sector ends at lap
/// fraction 1.
fn sector_times(trace: &RawTrace, starts: &[f64], bins: usize) -> Vec<Option<f64>> {
    let mut points: Vec<(f64, f64)> = trace
        .lap_pct
        .iter()
        .zip(&trace.core[SESSION_TIME])
        .filter(|(_, time)| time.is_finite())
        .map(|(pct, time)| (*pct, *time))
        .collect();
    points.sort_by(|a, b| a.0.total_cmp(&b.0));
    (0..starts.len())
        .map(|i| {
            let end = starts.get(i + 1).copied().unwrap_or(1.0);
            let begin = time_at(&points, starts[i], bins)?;
            let finish = time_at(&points, end, bins)?;
            (finish >= begin).then(|| ((finish - begin) * 1000.0).round() / 1000.0)
        })
        .collect()
}

pub(crate) struct CatalogInputs<'a> {
    pub envelope: &'a Envelope,
    /// `lap_traces`: the laps and their sector times.
    pub prepared: &'a Prepared,
    /// What `traces.ccol` holds: its bins and channels (the dense traces when the analysis has them).
    pub served: &'a Prepared,
    pub traces: &'a [RawTrace],
    pub records: &'a [LapRecord],
    pub sector_start_pcts: Option<&'a Value>,
    pub steering_basis: Value,
}

pub(crate) fn catalog_json(input: &CatalogInputs) -> Value {
    let prepared = input.prepared;
    let mut sectors: Vec<f64> = match input.sector_start_pcts {
        Some(Value::Array(items)) => items.iter().filter_map(num_of).collect(),
        _ => Vec::new(),
    };
    sectors.sort_by(f64::total_cmp);
    let laps: Vec<Value> = input
        .traces
        .iter()
        .zip(&prepared.laps)
        .map(|(trace, lap)| {
            let mut row = trace.meta.clone();
            row.insert("lap".into(), json!(lap));
            if sectors.len() > 1 {
                row.insert(
                    "sector_times_s".into(),
                    json!(sector_times(trace, &sectors, prepared.bins)),
                );
            }
            match input.records.iter().find(|r| r.lap == *lap) {
                Some(record) => {
                    row.insert("comparison_eligible".into(), json!(record.eligible));
                    row.insert("exclusion_reasons".into(), json!(record.reasons));
                    if let Some(number) = record.run_number {
                        row.entry("run_number").or_insert(json!(number));
                    }
                    if let Some(age) = record.run_age {
                        row.entry("run_age_laps").or_insert(json!(age));
                    }
                }
                None => {
                    row.insert("comparison_eligible".into(), json!(false));
                    row.insert(
                        "exclusion_reasons".into(),
                        json!(["eligibility_not_recorded"]),
                    );
                }
            }
            Value::Object(row)
        })
        .collect();
    let mut view = Map::new();
    input.envelope.apply(&mut view);
    view.insert("bins_per_lap".into(), json!(input.served.bins));
    view.insert("laps".into(), Value::Array(laps));
    view.insert(
        "signals".into(),
        Value::Array(input.served.signals.iter().map(signal_json).collect()),
    );
    view.insert("sector_start_pcts".into(), json!(sectors));
    view.insert("steering_basis".into(), input.steering_basis.clone());
    view.insert(
        "budgets".into(),
        json!({
            "vertex_budget_per_signal": 48000,
            "min_points_per_trace": 48,
            "max_color_groups": 20,
            "cursor_detail_traces": 24,
            "max_trace_rows": 10
        }),
    );
    if !input.served.omitted.is_empty() {
        view.insert("omitted_signals".into(), json!(input.served.omitted));
    }
    Value::Object(view)
}

// ---- CCOL ----

fn pad8(n: u64) -> u64 {
    n.div_ceil(8) * 8
}

pub(crate) struct ChannelLayout {
    pub id: String,
    pub dtype: Dtype,
    pub missing: Option<i64>,
    pub offset: u64,
    pub byte_length: u64,
}

/// Offsets (from the start of the data section, 8-byte aligned) for `channels` of `laps` x `bins`.
fn layout(
    channels: &[(String, Dtype, Option<i64>)],
    laps: usize,
    bins: usize,
) -> (Vec<ChannelLayout>, u64) {
    let mut offset = 0u64;
    let mut out = Vec::with_capacity(channels.len());
    for (id, dtype, missing) in channels {
        let byte_length = (laps * bins * dtype.width()) as u64;
        out.push(ChannelLayout {
            id: id.clone(),
            dtype: *dtype,
            missing: *missing,
            offset,
            byte_length,
        });
        offset += pad8(byte_length);
    }
    (out, offset)
}

fn header_bytes(
    analysis_id: &str,
    projection_version: u32,
    bins: usize,
    laps: &[i64],
    channels: &[ChannelLayout],
) -> Vec<u8> {
    let header = json!({
        "format": "CCOL",
        "version": 1,
        "analysis_id": analysis_id,
        "projection_version": projection_version,
        "bins": bins,
        "laps": laps,
        "channels": channels.iter().map(|c| json!({
            "id": c.id,
            "dtype": c.dtype.name(),
            "offset": c.offset,
            "byte_length": c.byte_length,
            "missing": c.missing,
        })).collect::<Vec<_>>(),
    });
    let json = serde_json::to_vec(&header).unwrap_or_default();
    let mut out = Vec::with_capacity(PREAMBLE_LEN + json.len() + 8);
    out.extend_from_slice(b"CCOL");
    out.extend_from_slice(&1u16.to_le_bytes());
    out.extend_from_slice(&0u16.to_le_bytes());
    out.extend_from_slice(&(json.len() as u32).to_le_bytes());
    out.extend_from_slice(&json);
    out.resize(pad8(out.len() as u64) as usize, 0);
    out
}

fn encode(row: &[f64], dtype: Dtype, missing: Option<i64>, out: &mut Vec<u8>) {
    match dtype {
        Dtype::F64 => row
            .iter()
            .for_each(|v| out.extend_from_slice(&v.to_le_bytes())),
        Dtype::I16 => {
            let sentinel = missing.unwrap_or(-32768) as i16;
            row.iter().for_each(|v| {
                let value = if v.is_nan() { sentinel } else { *v as i16 };
                out.extend_from_slice(&value.to_le_bytes());
            });
        }
        Dtype::U8 => {
            let sentinel = missing.unwrap_or(255) as u8;
            row.iter()
                .for_each(|v| out.push(if v.is_nan() { sentinel } else { *v as u8 }));
        }
    }
}

/// Writes `traces.ccol`: every projected lap and channel.
pub(crate) fn write_ccol(
    w: &mut dyn Write,
    analysis_id: &str,
    projection_version: u32,
    prepared: &Prepared,
    traces: &[RawTrace],
) -> io::Result<()> {
    let channels: Vec<_> = prepared
        .signals
        .iter()
        .map(|s| (s.id.clone(), s.dtype, s.missing))
        .collect();
    let (layouts, _) = layout(&channels, prepared.laps.len(), prepared.bins);
    w.write_all(&header_bytes(
        analysis_id,
        projection_version,
        prepared.bins,
        &prepared.laps,
        &layouts,
    ))?;
    let mut row = vec![f64::NAN; prepared.bins];
    for (signal, placed) in prepared.signals.iter().zip(&layouts) {
        let mut block = Vec::with_capacity(placed.byte_length as usize + 8);
        for (trace, indices) in traces.iter().zip(&prepared.bin_index) {
            row.fill(f64::NAN);
            for (value, bin) in column(trace, signal.source).iter().zip(indices) {
                row[*bin as usize] = *value;
            }
            encode(&row, signal.dtype, signal.missing, &mut block);
        }
        block.resize(pad8(block.len() as u64) as usize, 0);
        w.write_all(&block)?;
    }
    Ok(())
}

// ---- selection ----

struct CcolHeader {
    analysis_id: String,
    projection_version: u64,
    bins: usize,
    laps: Vec<i64>,
    channels: Vec<ChannelLayout>,
    data_start: u64,
    file_len: u64,
}

fn corrupt(message: impl Into<String>) -> SelectError {
    SelectError::Corrupt(message.into())
}

fn read_header(file: &mut File) -> Result<CcolHeader, SelectError> {
    let file_len = file.metadata()?.len();
    let mut preamble = [0u8; PREAMBLE_LEN];
    file.read_exact(&mut preamble)
        .map_err(|_| corrupt("shorter than its preamble"))?;
    if &preamble[..4] != b"CCOL" {
        return Err(corrupt("bad magic"));
    }
    if u16::from_le_bytes([preamble[4], preamble[5]]) != 1 || preamble[6..8] != [0, 0] {
        return Err(corrupt("unsupported container version"));
    }
    let header_len = u64::from(u32::from_le_bytes([
        preamble[8],
        preamble[9],
        preamble[10],
        preamble[11],
    ]));
    if PREAMBLE_LEN as u64 + header_len > file_len {
        return Err(corrupt("header runs past the end of the file"));
    }
    let mut json = vec![0u8; header_len as usize];
    file.read_exact(&mut json)
        .map_err(|_| corrupt("truncated header"))?;
    let header: Value =
        serde_json::from_slice(&json).map_err(|e| corrupt(format!("header: {e}")))?;
    let map = header
        .as_object()
        .ok_or_else(|| corrupt("header is not an object"))?;
    if text(map, "format").as_deref() != Some("CCOL") || int(map, "version") != Some(1) {
        return Err(corrupt("header format or version"));
    }
    let bins = int(map, "bins")
        .filter(|b| *b >= 1)
        .ok_or_else(|| corrupt("bins"))? as usize;
    let laps: Vec<i64> = arr(map, "laps")
        .iter()
        .map(|v| int_of(v).ok_or_else(|| corrupt("lap number")))
        .collect::<Result<_, _>>()?;
    let data_start = pad8(PREAMBLE_LEN as u64 + header_len);
    let mut channels = Vec::new();
    for entry in arr(map, "channels") {
        let entry = entry.as_object().ok_or_else(|| corrupt("channel entry"))?;
        let id = text(entry, "id").ok_or_else(|| corrupt("channel id"))?;
        let dtype = text(entry, "dtype")
            .and_then(|d| Dtype::parse(&d))
            .ok_or_else(|| corrupt(format!("channel {id} dtype")))?;
        let offset = int(entry, "offset")
            .filter(|o| *o >= 0)
            .ok_or_else(|| corrupt("channel offset"))? as u64;
        let byte_length = int(entry, "byte_length")
            .filter(|o| *o >= 0)
            .ok_or_else(|| corrupt("byte_length"))? as u64;
        if byte_length != (laps.len() * bins * dtype.width()) as u64
            || !offset.is_multiple_of(8)
            || data_start + offset + byte_length > file_len
        {
            return Err(corrupt(format!("channel {id} layout")));
        }
        channels.push(ChannelLayout {
            id,
            dtype,
            missing: int(entry, "missing"),
            offset,
            byte_length,
        });
    }
    Ok(CcolHeader {
        analysis_id: text(map, "analysis_id").unwrap_or_default(),
        projection_version: int(map, "projection_version").unwrap_or(0).max(0) as u64,
        bins,
        laps,
        channels,
        data_start,
        file_len,
    })
}

/// `laps` query: comma list of lap numbers or `a-b` ranges.
fn parse_laps(query: &str) -> Result<Vec<(i64, i64)>, SelectError> {
    let bad = |message: &str| SelectError::BadLaps(format!("{message}: {query:?}"));
    let number = |s: &str| -> Result<i64, SelectError> {
        let s = s.trim();
        if s.is_empty() || !s.bytes().all(|b| b.is_ascii_digit()) {
            return Err(bad("expected lap numbers"));
        }
        s.parse::<i64>().map_err(|_| bad("lap number too large"))
    };
    let mut ranges = Vec::new();
    for token in query.split(',') {
        let range = match token.split_once('-') {
            Some((from, to)) => (number(from)?, number(to)?),
            None => {
                let n = number(token)?;
                (n, n)
            }
        };
        if range.0 > range.1 {
            return Err(bad("range runs backwards"));
        }
        ranges.push(range);
    }
    Ok(ranges)
}

/// See `lib.rs`.
pub(crate) fn select(
    view_dir: &Path,
    laps: Option<&str>,
    channels: Option<&str>,
    max_bytes: u64,
) -> Result<Vec<u8>, SelectError> {
    let ranges = laps.map(parse_laps).transpose()?;
    let mut file = File::open(view_dir.join("traces.ccol"))?;
    let header = read_header(&mut file)?;

    let wanted: Vec<String> = match channels {
        Some(list) => {
            let mut ids: Vec<String> = Vec::new();
            for token in list.split(',') {
                let id = token.trim();
                if !header.channels.iter().any(|c| c.id == id) {
                    return Err(SelectError::UnknownChannel(id.to_string()));
                }
                if !ids.iter().any(|i| i == id) {
                    ids.push(id.to_string());
                }
            }
            ids
        }
        None => {
            let catalog: Value =
                serde_json::from_slice(&std::fs::read(view_dir.join("traces-catalog.json"))?)
                    .map_err(|e| corrupt(format!("catalog: {e}")))?;
            let signals = catalog
                .get("signals")
                .and_then(Value::as_array)
                .ok_or_else(|| corrupt("catalog signals"))?;
            signals
                .iter()
                .filter(|s| s.get("default").and_then(Value::as_bool) == Some(true))
                .filter_map(|s| s.get("id").and_then(Value::as_str).map(str::to_string))
                .collect()
        }
    };
    let chosen: Vec<&ChannelLayout> = header
        .channels
        .iter()
        .filter(|c| wanted.contains(&c.id))
        .collect();
    let lap_rows: Vec<usize> = (0..header.laps.len())
        .filter(|i| {
            ranges.as_ref().is_none_or(|r| {
                r.iter()
                    .any(|(lo, hi)| (*lo..=*hi).contains(&header.laps[*i]))
            })
        })
        .collect();
    let selected_laps: Vec<i64> = lap_rows.iter().map(|i| header.laps[*i]).collect();

    let plan: Vec<(String, Dtype, Option<i64>)> = chosen
        .iter()
        .map(|c| (c.id.clone(), c.dtype, c.missing))
        .collect();
    let (layouts, data_len) = layout(&plan, lap_rows.len(), header.bins);
    let head = header_bytes(
        &header.analysis_id,
        header.projection_version as u32,
        header.bins,
        &selected_laps,
        &layouts,
    );
    let total = head.len() as u64 + data_len;
    if total > max_bytes {
        return Err(SelectError::TooLarge {
            bytes: total,
            limit: max_bytes,
        });
    }
    let mut out = Vec::with_capacity(total as usize);
    out.extend_from_slice(&head);
    let row_bytes = |dtype: Dtype| (header.bins * dtype.width()) as u64;
    for (source, placed) in chosen.iter().zip(&layouts) {
        let base = header.data_start + source.offset;
        if lap_rows.len() == header.laps.len() {
            let mut block = vec![0u8; source.byte_length as usize];
            file.seek(SeekFrom::Start(base))?;
            file.read_exact(&mut block)
                .map_err(|_| corrupt("truncated data"))?;
            out.extend_from_slice(&block);
        } else {
            let mut row = vec![0u8; row_bytes(source.dtype) as usize];
            for lap in &lap_rows {
                file.seek(SeekFrom::Start(
                    base + *lap as u64 * row_bytes(source.dtype),
                ))?;
                file.read_exact(&mut row)
                    .map_err(|_| corrupt("truncated data"))?;
                out.extend_from_slice(&row);
            }
        }
        out.resize(
            (head.len() as u64 + placed.offset + pad8(placed.byte_length)) as usize,
            0,
        );
    }
    debug_assert!(header.file_len >= header.data_start);
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn core_table_matches_the_point_key_order() {
        let keys: Vec<&str> = CORE.iter().map(|c| c.source_key).collect();
        assert_eq!(
            keys,
            [
                "speed_mph",
                "session_time_s",
                "throttle",
                "brake",
                "steering_rad",
                "steering_abs_peak_rad",
                "slip_angle_deg",
                "yaw_rate_deg_s",
                "lateral_g",
                "longitudinal_g",
                "gear",
                "rpm",
                "latitude",
                "longitude",
                "tire_stress_proxy"
            ]
        );
    }

    #[test]
    fn dtype_is_the_narrowest_exact_encoding() {
        let s = |values: &[f64]| dtype_for(stats(values.iter().copied()));
        assert_eq!(s(&[0.0, 5.0, f64::NAN]), (Dtype::U8, Some(255)));
        assert_eq!(s(&[0.0, 254.0]), (Dtype::U8, Some(255)));
        assert_eq!(s(&[0.0, 255.0]), (Dtype::I16, Some(-32768)));
        assert_eq!(s(&[-1.0, 9000.0]), (Dtype::I16, Some(-32768)));
        assert_eq!(s(&[0.5, 1.0]), (Dtype::F64, None));
        assert_eq!(s(&[-0.0, 1.0]), (Dtype::F64, None));
        assert_eq!(s(&[40000.0]), (Dtype::F64, None));
    }

    #[test]
    fn lap_queries_parse_lists_and_ranges() {
        assert_eq!(parse_laps("1-40,52").unwrap(), [(1, 40), (52, 52)]);
        assert_eq!(parse_laps(" 3 , 5-6 ").unwrap(), [(3, 3), (5, 6)]);
        for bad in [
            "",
            "a",
            "1,,2",
            "5-3",
            "-3",
            "1-",
            "1.5",
            "99999999999999999999",
        ] {
            assert!(
                matches!(parse_laps(bad), Err(SelectError::BadLaps(_))),
                "{bad:?}"
            );
        }
    }

    #[test]
    fn short_labels_cover_corner_families() {
        assert_eq!(short_label("lf-pressure").as_deref(), Some("LF psi"));
        assert_eq!(short_label("rr-wheel-speed").as_deref(), Some("RR"));
        assert_eq!(short_label("track-surface").as_deref(), Some("Surface"));
        assert_eq!(short_label("unknown"), None);
    }
}

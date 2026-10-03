//! `race_foundations.build_track_geometry` and its helpers (`_finite_column`, `_median`,
//! `_resample_path`, `_average_paths`, `_normalize_geometry`, `_geometry_extents`,
//! `_path_is_plausible_relative_to_main`, `_sanitize_geometry_layers`, `_main_loop_quality`,
//! `_cross_line`, `track_configuration_key`, `_safe_slug`).
//!
//! Ordering hazards, in the oracle's order:
//! - Candidates are the samples in index order whose lap distance, latitude and longitude are
//!   finite, the distance in [-0.01, 1.01], |lat| <= 90, |lon| <= 180 and not the (0, 0)
//!   placeholder; a rejected sample with both coordinates finite counts as a rejected GPS sample.
//! - The cluster centre is the median longitude and latitude of the candidates (`_median`,
//!   `statistics.median` of the finite list), the radius `max(0.25, median distance +
//!   max(0.0005, MAD) * 12)`; `x or 0.0` turns a `-0.0` median into `0.0` before it is used.
//! - Main bins: `min(main_bins - 1, max(0, int((pct % 1.0) * main_bins)))`, filled in sample
//!   order by off-pit points, emitted in bin order with `round(float(_median(...) or 0.0), 8)`.
//! - Pit episodes: `pit[index]` truthiness, entry window `valid[index - window : index +
//!   max(2, window // 4)]` with `window = max(3, int(sample_rate * 4.0))` and Python slice
//!   clamping, exit window mirrored; an episode is appended when the next sample leaves pit
//!   road or at the end.
//! - `_average_paths`: `_resample_path` picks `round(i * (n - 1) / max(1, count - 1))`
//!   (int true division, half to even), medians per index over the paths, `lap_pct` kept
//!   unrounded-`None`-aware (`round(value, 6)` only when a median exists).
//! - `_normalize_geometry`: the main path is the anchor (else every point of every layer, in
//!   layer order), `min`/`max` keep the first of equal values, `scale = max(dx, dy)`; a point's
//!   dict keeps its key order with `x` and `y` replaced.
//! - `_sanitize_geometry_layers` checks pit lane, entry and exit only (the line fields are
//!   absent from the paths at that point), appending rejected names in that order; the reason
//!   text sorts them.
//! - `result.update(paths)` keeps the keys' positions; `transform` is appended after `quality`;
//!   the hash is taken over the final dict.

use std::cmp::Ordering;

use coach_numerics as num;

use super::geometry_hash::track_geometry_sha256;
use super::{
    Channels, as_mapping, casefold, finite, format, int_of, median, py_str, round, sequence,
};
use crate::kernels::error::KernelError;
use crate::kernels::value::{Dict, PyValue};

const MIN_MAIN_LOOP_LAP_PERCENT_COVERAGE: f64 = 0.95;
const MAX_MAIN_LOOP_LAP_PERCENT_GAP: f64 = 0.05;
const MAX_MAIN_LOOP_CLOSURE_DISTANCE: f64 = 0.15;
const MIN_MAIN_LOOP_NORMALIZED_SPAN: f64 = 0.20;
const MAX_MAIN_LOOP_RELATIVE_SEGMENT_DISTANCE: f64 = 0.35;
const MAX_AUXILIARY_BOUNDS_MARGIN: f64 = 0.45;
const MAX_AUXILIARY_RELATIVE_SEGMENT_DISTANCE: f64 = 0.55;
const GPS_PLACEHOLDER_EPSILON: f64 = 1e-9;
const GPS_CLUSTER_RADIUS_DEGREES: f64 = 0.25;

/// A recorded point (`{"x", "y", "lap_pct", "sample_index"}`).
#[derive(Clone, Copy)]
struct Sample {
    x: f64,
    y: f64,
    lap_pct: f64,
    index: usize,
}

/// A path point as the oracle's dict. `lap_pct` may be `None` only in `_average_paths`.
#[derive(Clone, Debug)]
struct Point {
    kind: PointKind,
    sequence: usize,
    x: f64,
    y: f64,
    lap_pct: Option<f64>,
    observations: usize,
}

/// Key order of the point dict: the main path's (`sequence, lap_pct, x, y, observations`) or
/// `_average_paths`' (`sequence, x, y, lap_pct, observations`).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum PointKind {
    Main,
    Averaged,
}

impl Point {
    fn to_value(&self) -> PyValue {
        let lap_pct = PyValue::from(self.lap_pct);
        let mut dict = Dict::with_capacity(5);
        dict.push_new("sequence", PyValue::Int(self.sequence as i64));
        if self.kind == PointKind::Main {
            dict.push_new("lap_pct", lap_pct);
            dict.push_new("x", self.x);
            dict.push_new("y", self.y);
        } else {
            dict.push_new("x", self.x);
            dict.push_new("y", self.y);
            dict.push_new("lap_pct", lap_pct);
        }
        dict.push_new("observations", PyValue::Int(self.observations as i64));
        PyValue::Dict(dict)
    }
}

/// A line (`{"a": {"x", "y"}, "b": {"x", "y"}}`).
#[derive(Clone, Copy, Debug)]
struct Line {
    a: (f64, f64),
    b: (f64, f64),
}

impl Line {
    fn to_value(self) -> PyValue {
        let end = |(x, y): (f64, f64)| {
            let mut dict = Dict::with_capacity(2);
            dict.push_new("x", x);
            dict.push_new("y", y);
            PyValue::Dict(dict)
        };
        let mut dict = Dict::with_capacity(2);
        dict.push_new("a", end(self.a));
        dict.push_new("b", end(self.b));
        PyValue::Dict(dict)
    }
}

/// `_median(values)` of finite floats, `None` when empty; then `x or 0.0`.
fn median_or_zero(values: &[f64]) -> Result<f64, KernelError> {
    if values.is_empty() {
        return Ok(0.0);
    }
    let value = median(values)?;
    Ok(if value == 0.0 { 0.0 } else { value })
}

/// Python `min(values)` / `max(values)` of floats: the first of equal values is kept.
fn min_max(values: impl Iterator<Item = f64>) -> Option<(f64, f64)> {
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
    bounds
}

/// Python `max(a, b)`: `b` only when it is greater.
fn max2(a: f64, b: f64) -> f64 {
    if b > a { b } else { a }
}

/// Python `min(a, b)`: `b` only when it is smaller.
fn min2(a: f64, b: f64) -> f64 {
    if b < a { b } else { a }
}

/// `_resample_path(points, count)` (as indices into `points`).
fn resample(len: usize, count: usize) -> Result<Vec<usize>, KernelError> {
    if len <= count {
        return Ok((0..len).collect());
    }
    let mut indices = Vec::with_capacity(count);
    let denominator = count.saturating_sub(1).max(1) as i64;
    for output_index in 0..count {
        let numerator = (output_index as i64)
            .checked_mul(len as i64 - 1)
            .ok_or_else(|| KernelError::internal("resample index overflow"))?;
        let position = num::int_true_div(numerator, denominator)?;
        let source = num::round_to_int(position)?;
        let source = usize::try_from(source)
            .ok()
            .filter(|&s| s < len)
            .ok_or_else(|| KernelError::internal("resample index out of range"))?;
        indices.push(source);
    }
    Ok(indices)
}

/// `_average_paths(paths, count)`.
fn average_paths(paths: &[Vec<Sample>], count: usize) -> Result<Vec<Point>, KernelError> {
    let usable: Vec<&Vec<Sample>> = paths.iter().filter(|path| path.len() >= 2).collect();
    if usable.is_empty() {
        return Ok(Vec::new());
    }
    let normalized: Vec<Vec<Sample>> = usable
        .iter()
        .map(|path| {
            Ok(resample(path.len(), count)?
                .into_iter()
                .map(|i| path[i])
                .collect())
        })
        .collect::<Result<_, KernelError>>()?;
    let length = normalized.iter().map(Vec::len).min().unwrap_or(0);
    let mut out = Vec::with_capacity(length);
    let mut xs = Vec::with_capacity(normalized.len());
    let mut ys = Vec::with_capacity(normalized.len());
    let mut pcts = Vec::with_capacity(normalized.len());
    for index in 0..length {
        xs.clear();
        ys.clear();
        pcts.clear();
        for path in &normalized {
            xs.push(path[index].x);
            ys.push(path[index].y);
            pcts.push(path[index].lap_pct);
        }
        let lap_pct = if pcts.is_empty() {
            None
        } else {
            Some(round(median(&pcts)?, 6)?)
        };
        out.push(Point {
            kind: PointKind::Averaged,
            sequence: index,
            x: round(median_or_zero(&xs)?, 8)?,
            y: round(median_or_zero(&ys)?, 8)?,
            lap_pct,
            observations: normalized.len(),
        });
    }
    Ok(out)
}

/// `(minimum_x, maximum_x, minimum_y, maximum_y, scale)` of `_normalize_geometry`, `None` when
/// it returns empty layers and `{}`.
struct Transform {
    min_x: f64,
    max_x: f64,
    min_y: f64,
    max_y: f64,
    scale: f64,
}

/// `_normalize_geometry(paths)`: the layers in place and the transform.
fn normalize(layers: &mut [Vec<Point>; 4]) -> Result<Option<Transform>, KernelError> {
    let anchor: Vec<&Point> = if layers[0].is_empty() {
        layers.iter().flatten().collect()
    } else {
        layers[0].iter().collect()
    };
    let x_bounds = min_max(anchor.iter().map(|p| p.x));
    let y_bounds = min_max(anchor.iter().map(|p| p.y));
    let (Some((min_x, max_x)), Some((min_y, max_y))) = (x_bounds, y_bounds) else {
        layers.iter_mut().for_each(Vec::clear);
        return Ok(None);
    };
    let scale = max2(max_x - min_x, max_y - min_y);
    if scale <= 1e-12 {
        layers.iter_mut().for_each(Vec::clear);
        return Ok(None);
    }
    for layer in layers.iter_mut() {
        for point in layer.iter_mut() {
            point.x = round((point.x - min_x) / scale, 8)?;
            point.y = round((max_y - point.y) / scale, 8)?;
        }
    }
    Ok(Some(Transform {
        min_x,
        max_x,
        min_y,
        max_y,
        scale,
    }))
}

/// `_geometry_extents(path)`.
fn extents(path: &[Point]) -> Option<(f64, f64, f64, f64)> {
    let (min_x, max_x) = min_max(path.iter().map(|p| p.x))?;
    let (min_y, max_y) = min_max(path.iter().map(|p| p.y))?;
    Some((min_x, max_x, min_y, max_y))
}

/// `max(hypot(...) for consecutive points, default=0.0)`.
fn maximum_segment(path: &[Point]) -> f64 {
    let mut maximum: Option<f64> = None;
    for pair in path.windows(2) {
        let distance = num::hypot(pair[1].x - pair[0].x, pair[1].y - pair[0].y);
        // max() replaces the running value only when an item is greater.
        maximum = Some(match maximum {
            Some(current) if distance.partial_cmp(&current) != Some(Ordering::Greater) => current,
            _ => distance,
        });
    }
    maximum.unwrap_or(0.0)
}

/// `_path_is_plausible_relative_to_main(path, main_path)` (every point is finite here).
fn plausible(path: &[Point], main: &[Point]) -> bool {
    if path.len() < 2 {
        return false;
    }
    let (Some(main_bounds), Some(path_bounds)) = (extents(main), extents(path)) else {
        return false;
    };
    let main_span = max2(main_bounds.1 - main_bounds.0, main_bounds.3 - main_bounds.2);
    if !main_span.is_finite() || main_span <= 1e-9 {
        return false;
    }
    let margin = main_span * MAX_AUXILIARY_BOUNDS_MARGIN;
    if path_bounds.0 < main_bounds.0 - margin
        || path_bounds.1 > main_bounds.1 + margin
        || path_bounds.2 < main_bounds.2 - margin
        || path_bounds.3 > main_bounds.3 + margin
    {
        return false;
    }
    maximum_segment(path) <= main_span * MAX_AUXILIARY_RELATIVE_SEGMENT_DISTANCE
}

/// `_main_loop_quality(path)`, as the dict and `main_loop_complete`.
fn main_loop_quality(path: &[Point]) -> Result<(Dict, bool, f64, f64, f64), KernelError> {
    let mut percentages: Vec<f64> = Vec::with_capacity(path.len());
    for point in path {
        if let Some(value) = point.lap_pct {
            percentages.push(num::float_mod(value, 1.0)?);
        }
    }
    // sorted(set(...)): equal floats collapse to one (the values are finite and never -0.0).
    percentages.sort_by(f64::total_cmp);
    percentages.dedup_by(|a, b| a == b);
    let (maximum_gap, coverage) = if percentages.len() >= 2 {
        let mut maximum_gap: Option<f64> = None;
        let mut consider = |gap: f64| {
            maximum_gap = Some(match maximum_gap {
                Some(current) if gap.partial_cmp(&current) != Some(Ordering::Greater) => current,
                _ => gap,
            });
        };
        for pair in percentages.windows(2) {
            consider(pair[1] - pair[0]);
        }
        consider(1.0 - percentages[percentages.len() - 1] + percentages[0]);
        let maximum_gap = maximum_gap.unwrap_or(0.0);
        (maximum_gap, max2(0.0, min2(1.0, 1.0 - maximum_gap)))
    } else {
        (1.0, 0.0)
    };
    let mut closure_distance = 1.0;
    let mut span = 0.0;
    let mut maximum_segment_distance = f64::INFINITY;
    if path.len() >= 2 {
        let (first, last) = (&path[0], &path[path.len() - 1]);
        closure_distance = num::hypot(last.x - first.x, last.y - first.y);
        if let Some(bounds) = extents(path) {
            span = max2(bounds.1 - bounds.0, bounds.3 - bounds.2);
        }
        maximum_segment_distance = maximum_segment(path);
    }
    let relative = if span > 1e-12 && maximum_segment_distance.is_finite() {
        maximum_segment_distance / span
    } else {
        f64::INFINITY
    };
    let plausible = span >= MIN_MAIN_LOOP_NORMALIZED_SPAN
        && relative <= MAX_MAIN_LOOP_RELATIVE_SEGMENT_DISTANCE;
    let complete = path.len() >= 3
        && coverage >= MIN_MAIN_LOOP_LAP_PERCENT_COVERAGE
        && maximum_gap <= MAX_MAIN_LOOP_LAP_PERCENT_GAP
        && closure_distance <= MAX_MAIN_LOOP_CLOSURE_DISTANCE
        && plausible;
    let finite_round = |x: f64| -> Result<PyValue, KernelError> {
        Ok(if x.is_finite() {
            PyValue::Float(round(x, 8)?)
        } else {
            PyValue::None
        })
    };
    let coverage_rounded = round(coverage, 6)?;
    let gap_rounded = round(maximum_gap, 6)?;
    let closure_rounded = round(closure_distance, 8)?;
    let mut dict = Dict::with_capacity(8);
    dict.push_new("main_loop_complete", complete);
    dict.push_new("lap_percent_coverage", coverage_rounded);
    dict.push_new("maximum_lap_percent_gap", gap_rounded);
    dict.push_new("closure_distance", closure_rounded);
    dict.push_new("geometry_plausible", plausible);
    dict.push_new("main_path_span", round(span, 8)?);
    dict.push_new(
        "maximum_segment_distance",
        finite_round(maximum_segment_distance)?,
    );
    dict.push_new("maximum_relative_segment_distance", finite_round(relative)?);
    Ok((
        dict,
        complete,
        coverage_rounded,
        gap_rounded,
        closure_rounded,
    ))
}

/// `_cross_line(path, index)` with the default width.
fn cross_line(path: &[Point], index: usize) -> Result<Option<Line>, KernelError> {
    const WIDTH: f64 = 0.018;
    if path.is_empty() {
        return Ok(None);
    }
    let center_index = index.min(path.len() - 1);
    let before = &path[center_index.saturating_sub(1)];
    let after = &path[(center_index + 1).min(path.len() - 1)];
    let center = &path[center_index];
    let dx = after.x - before.x;
    let dy = after.y - before.y;
    let magnitude = num::hypot(dx, dy);
    if magnitude <= 1e-12 {
        return Ok(None);
    }
    let (px, py) = (-dy / magnitude, dx / magnitude);
    Ok(Some(Line {
        a: (
            round(center.x - px * WIDTH, 8)?,
            round(center.y - py * WIDTH, 8)?,
        ),
        b: (
            round(center.x + px * WIDTH, 8)?,
            round(center.y + py * WIDTH, 8)?,
        ),
    }))
}

/// `_safe_slug(value)`: `re.sub(r"[^a-z0-9]+", "-", str(value or "").casefold()).strip("-")`,
/// at most 100 characters, else `"unknown"`.
fn safe_slug(text: &str) -> String {
    let folded = casefold(text);
    let mut slug = String::with_capacity(folded.len());
    let mut in_run = false;
    for c in folded.chars() {
        if c.is_ascii_lowercase() || c.is_ascii_digit() {
            slug.push(c);
            in_run = false;
        } else if !in_run {
            slug.push('-');
            in_run = true;
        }
    }
    let trimmed: String = slug.trim_matches('-').chars().take(100).collect();
    if trimmed.is_empty() {
        "unknown".to_owned()
    } else {
        trimmed
    }
}

/// `identity.get(key)` of a mapping (`None` when missing).
fn get<'v>(mapping: &'v Dict, key: &str) -> &'v PyValue {
    static NONE: PyValue = PyValue::None;
    mapping.get(key).unwrap_or(&NONE)
}

/// `track_configuration_key(identity)`.
fn track_configuration_key(identity: &Dict) -> Result<String, KernelError> {
    let track_id = get(identity, "track_id");
    let config = match get(identity, "track_config") {
        value if value.truthy()? => value,
        _ => get(identity, "track_path"),
    };
    let prefix = match track_id {
        PyValue::None => "track".to_owned(),
        PyValue::Str(text) if text.is_empty() => "track".to_owned(),
        other => py_str(other)?,
    };
    let tail = if config.truthy()? {
        py_str(config)?
    } else {
        py_str(get(identity, "track_name"))?
    };
    // _safe_slug(value) takes str(value or ""): the f-string is never empty.
    Ok(safe_slug(&format!("{prefix}-{tail}")))
}

/// `build_track_geometry(channels, metadata, identity, source_fingerprints, *, main_bins)`.
pub fn build_track_geometry(
    channels: &Channels<'_>,
    metadata: &PyValue,
    identity: &PyValue,
    source_fingerprints: &PyValue,
    main_bins: Option<&PyValue>,
) -> Result<PyValue, KernelError> {
    let main_bins = match main_bins {
        None => 500usize,
        Some(PyValue::Int(n)) if *n >= 1 => {
            usize::try_from(*n).map_err(|_| KernelError::internal("main_bins out of range"))?
        }
        Some(other) => {
            return Err(KernelError::internal(format!(
                "main_bins {other:?} is not a positive int"
            )));
        }
    };
    let identity =
        as_mapping(identity).ok_or_else(|| KernelError::internal("identity is not a mapping"))?;
    let metadata =
        as_mapping(metadata).ok_or_else(|| KernelError::internal("metadata is not a mapping"))?;

    let required = [
        ("LapDistPct", "LapDistPct was not recorded."),
        ("Lat", "Latitude was not recorded."),
        ("Lon", "Longitude was not recorded."),
    ];
    let mut reasons: Vec<PyValue> = required
        .iter()
        .filter(|(channel, _)| !channels.contains(channel))
        .map(|(_, reason)| PyValue::from(*reason))
        .collect();
    let missing = !reasons.is_empty();

    let mut sources: Vec<String> = Vec::new();
    for item in sequence(source_fingerprints, "source_fingerprints")? {
        let item = as_mapping(item)
            .ok_or_else(|| KernelError::internal("a source fingerprint is not a mapping"))?;
        let sha = get(item, "sha256");
        let text = if sha.truthy()? {
            py_str(sha)?
        } else {
            String::new()
        };
        if text.chars().count() == 64 {
            sources.push(py_str(sha)?.to_lowercase());
        }
    }
    sources.sort();
    sources.dedup();
    let source_list = PyValue::List(sources.into_iter().map(PyValue::Str).collect());

    let mut result = Dict::with_capacity(24);
    result.push_new("schema_version", PyValue::Int(1));
    result.push_new(
        "track_configuration_key",
        track_configuration_key(identity)?,
    );
    result.push_new("track_id", get(identity, "track_id").clone());
    result.push_new("track_name", get(identity, "track_name").clone());
    result.push_new("track_config", get(identity, "track_config").clone());
    result.push_new("source_sha256", source_list.clone());
    result.push_new("contributing_source_sha256", source_list.clone());
    result.push_new("observed_source_sha256", source_list);
    result.push_new("coordinate_system", "normalized_local_vector");
    result.push_new("geometry_hash", PyValue::None);
    result.push_new("status", if missing { "unavailable" } else { "usable" });
    result.push_new("unavailable_reasons", PyValue::None);
    for layer in ["main_path", "pit_lane", "pit_entry_path", "pit_exit_path"] {
        result.push_new(layer, PyValue::List(Vec::new()));
    }
    for line in ["start_finish_line", "pit_commitment_line", "pit_merge_line"] {
        result.push_new(line, PyValue::None);
    }
    let mut quality = Dict::with_capacity(16);
    quality.push_new("main_loop_complete", false);
    quality.push_new("lap_percent_coverage", 0.0);
    quality.push_new("maximum_lap_percent_gap", 1.0);
    quality.push_new("closure_distance", 1.0);
    quality.push_new("geometry_plausible", false);
    quality.push_new("main_path_span", 0.0);
    quality.push_new("maximum_segment_distance", PyValue::None);
    quality.push_new("maximum_relative_segment_distance", PyValue::None);
    for key in [
        "main_path_points",
        "observed_main_path_points",
        "pit_lane_points",
        "pit_entry_observations",
        "pit_exit_observations",
        "main_source_samples",
        "pit_source_samples",
    ] {
        quality.push_new(key, PyValue::Int(0));
    }
    result.push_new("quality", quality);
    if missing {
        result.insert("unavailable_reasons", PyValue::List(reasons));
        return Ok(PyValue::Dict(result));
    }

    let pct = channels.required("LapDistPct")?;
    let lat = channels.required("Lat")?;
    let lon = channels.required("Lon")?;
    let pit_recorded = channels.contains("OnPitRoad");
    let pit_series = if channels.truthy("OnPitRoad")? {
        Some(channels.required("OnPitRoad")?)
    } else {
        None
    };
    let base = pct.len().min(lat.len()).min(lon.len());
    let count = match pit_series {
        Some(series) => base.min(series.len()),
        None => base,
    };
    let pit = |index: usize| -> Result<bool, KernelError> {
        match pit_series {
            Some(series) => series.truthy(index),
            None => Ok(false),
        }
    };

    let mut candidates: Vec<Sample> = Vec::new();
    let mut rejected_gps_samples: i64 = 0;
    for index in 0..count {
        let (lap_pct, y, x) = (pct.finite(index), lat.finite(index), lon.finite(index));
        let accepted = match (lap_pct, x, y) {
            (Some(lap_pct), Some(x), Some(y)) => {
                !(lap_pct < -0.01
                    || lap_pct > 1.01
                    || y.abs() > 90.0
                    || x.abs() > 180.0
                    || (x.abs() <= GPS_PLACEHOLDER_EPSILON && y.abs() <= GPS_PLACEHOLDER_EPSILON))
            }
            _ => false,
        };
        if !accepted {
            if x.is_some() && y.is_some() {
                rejected_gps_samples += 1;
            }
            continue;
        }
        candidates.push(Sample {
            x: x.unwrap_or_default(),
            y: y.unwrap_or_default(),
            lap_pct: lap_pct.unwrap_or_default(),
            index,
        });
    }

    let mut bins: Vec<Vec<Sample>> = vec![Vec::new(); main_bins];
    let mut valid: Vec<Option<Sample>> = vec![None; count];
    if !candidates.is_empty() {
        let xs: Vec<f64> = candidates.iter().map(|c| c.x).collect();
        let ys: Vec<f64> = candidates.iter().map(|c| c.y).collect();
        let center_x = median(&xs)?;
        let center_y = median(&ys)?;
        drop((xs, ys));
        let distances: Vec<f64> = candidates
            .iter()
            .map(|c| num::hypot(c.x - center_x, c.y - center_y))
            .collect();
        let median_distance = median_or_zero(&distances)?;
        let deviations: Vec<f64> = distances
            .iter()
            .map(|d| (d - median_distance).abs())
            .collect();
        let distance_mad = median_or_zero(&deviations)?;
        let cluster_radius = max2(
            GPS_CLUSTER_RADIUS_DEGREES,
            median_distance + max2(0.0005, distance_mad) * 12.0,
        );
        for candidate in &candidates {
            if num::hypot(candidate.x - center_x, candidate.y - center_y) > cluster_radius {
                rejected_gps_samples += 1;
                continue;
            }
            let wrapped = num::float_mod(candidate.lap_pct, 1.0)?;
            let point = Sample {
                lap_pct: wrapped,
                ..*candidate
            };
            valid[candidate.index] = Some(point);
            if !pit(candidate.index)? {
                let bin = int_of(wrapped * main_bins as f64)?.max(0);
                let bin = (bin as usize).min(main_bins - 1);
                bins[bin].push(point);
            }
        }
    }

    let mut main_raw: Vec<Point> = Vec::new();
    let mut scratch_x = Vec::new();
    let mut scratch_y = Vec::new();
    for (bin_index, points) in bins.iter().enumerate() {
        if points.is_empty() {
            continue;
        }
        scratch_x.clear();
        scratch_y.clear();
        scratch_x.extend(points.iter().map(|p| p.x));
        scratch_y.extend(points.iter().map(|p| p.y));
        main_raw.push(Point {
            kind: PointKind::Main,
            sequence: main_raw.len(),
            x: round(median_or_zero(&scratch_x)?, 8)?,
            y: round(median_or_zero(&scratch_y)?, 8)?,
            lap_pct: Some(round((bin_index as f64 + 0.5) / main_bins as f64, 6)?),
            observations: points.len(),
        });
    }

    let rate_value = match get(metadata, "sample_rate") {
        value if value.truthy()? => value,
        _ => get(metadata, "tick_rate"),
    };
    let sample_rate = match finite(rate_value) {
        Some(rate) if rate != 0.0 => rate,
        _ => 20.0,
    };
    let window_float = sample_rate * 4.0;
    if !window_float.is_finite() {
        return Err(KernelError::raises(
            "OverflowError",
            "cannot convert float infinity to integer",
        ));
    }
    let window = int_of(window_float)?.max(3);
    let quarter = (window.div_euclid(4)).max(2);
    // Python slice bounds: clamp to [0, count]; an empty slice when start >= stop.
    let slice = |start: i64, stop: i64| -> Vec<Sample> {
        let clamp = |v: i64| v.clamp(0, count as i64) as usize;
        let (start, stop) = (clamp(start), clamp(stop));
        if start >= stop {
            return Vec::new();
        }
        valid[start..stop].iter().flatten().copied().collect()
    };

    let mut pit_episodes: Vec<Vec<Sample>> = Vec::new();
    let mut entry_paths: Vec<Vec<Sample>> = Vec::new();
    let mut exit_paths: Vec<Vec<Sample>> = Vec::new();
    let mut entry_points = 0usize;
    let mut exit_points = 0usize;
    let mut current: Vec<Sample> = Vec::new();
    let mut prior_pit = false;
    for (index, &point) in valid.iter().enumerate() {
        let is_pit = pit(index)?;
        if is_pit && let Some(point) = point {
            current.push(point);
        }
        let i = index as i64;
        if is_pit && !prior_pit {
            let segment = slice((i - window).max(0), (count as i64).min(i + quarter));
            if segment.len() >= 2 {
                entry_paths.push(segment);
            }
            if point.is_some() {
                entry_points += 1;
            }
        }
        if !is_pit && prior_pit {
            if current.len() >= 2 {
                pit_episodes.push(std::mem::take(&mut current));
            }
            current.clear();
            let segment = slice((i - quarter).max(0), (count as i64).min(i + window));
            if segment.len() >= 2 {
                exit_paths.push(segment);
            }
            if point.is_some() {
                exit_points += 1;
            }
        }
        prior_pit = is_pit;
    }
    if current.len() >= 2 {
        pit_episodes.push(current);
    }

    let main_source_samples: usize = bins.iter().map(Vec::len).sum();
    let pit_source_samples: usize = pit_episodes.iter().map(Vec::len).sum();
    drop(bins);
    drop(valid);
    let mut layers: [Vec<Point>; 4] = [
        main_raw,
        average_paths(&pit_episodes, 180)?,
        average_paths(&entry_paths, 70)?,
        average_paths(&exit_paths, 70)?,
    ];
    let transform = normalize(&mut layers)?;
    let mut rejected_layers: Vec<&'static str> = Vec::new();
    for (slot, name) in [(1, "pit_lane"), (2, "pit_entry_path"), (3, "pit_exit_path")] {
        if !layers[slot].is_empty() && !plausible(&layers[slot], &layers[0]) {
            layers[slot].clear();
            rejected_layers.push(name);
        }
    }
    let [main_path, pit_lane, entry_path, exit_path] = &layers;
    for (name, layer) in [
        ("main_path", main_path),
        ("pit_lane", pit_lane),
        ("pit_entry_path", entry_path),
        ("pit_exit_path", exit_path),
    ] {
        result.insert(
            name,
            PyValue::List(layer.iter().map(Point::to_value).collect()),
        );
    }
    let transform_value = match &transform {
        None => PyValue::Dict(Dict::new()),
        Some(t) => {
            let mut bounds = Dict::with_capacity(4);
            bounds.push_new("minimum_x", t.min_x);
            bounds.push_new("maximum_x", t.max_x);
            bounds.push_new("minimum_y", t.min_y);
            bounds.push_new("maximum_y", t.max_y);
            let mut dict = Dict::with_capacity(2);
            dict.push_new("source_bounds", bounds);
            dict.push_new("normalization_scale", t.scale);
            PyValue::Dict(dict)
        }
    };
    result.push_new("transform", transform_value);

    let (loop_quality, main_loop_complete, coverage, gap, closure) = main_loop_quality(main_path)?;
    if main_loop_complete {
        result.insert(
            "start_finish_line",
            PyValue::from(cross_line(main_path, 0)?.map(Line::to_value)),
        );
    }
    if entry_points > 0 && !pit_lane.is_empty() {
        result.insert(
            "pit_commitment_line",
            PyValue::from(cross_line(pit_lane, 0)?.map(Line::to_value)),
        );
    }
    if exit_points > 0 && !pit_lane.is_empty() {
        result.insert(
            "pit_merge_line",
            PyValue::from(cross_line(pit_lane, pit_lane.len() - 1)?.map(Line::to_value)),
        );
    }
    let mut quality = loop_quality;
    let count_value = |n: usize| PyValue::Int(n as i64);
    quality.push_new(
        "main_path_points",
        count_value(if main_loop_complete {
            main_path.len()
        } else {
            0
        }),
    );
    quality.push_new("observed_main_path_points", count_value(main_path.len()));
    quality.push_new("pit_lane_points", count_value(pit_lane.len()));
    quality.push_new("pit_entry_observations", count_value(entry_paths.len()));
    quality.push_new("pit_exit_observations", count_value(exit_paths.len()));
    quality.push_new("main_source_samples", count_value(main_source_samples));
    quality.push_new("pit_source_samples", count_value(pit_source_samples));
    quality.push_new("rejected_gps_samples", PyValue::Int(rejected_gps_samples));
    quality.push_new(
        "rejected_geometry_layers",
        PyValue::List(
            rejected_layers
                .iter()
                .map(|name| PyValue::from(*name))
                .collect(),
        ),
    );
    result.insert("quality", quality);

    let mut status = "usable";
    if !main_loop_complete {
        status = "unavailable";
        result.insert("main_path", PyValue::List(Vec::new()));
        result.insert("start_finish_line", PyValue::None);
        reasons.push(PyValue::from(format!(
            "A complete closed main circuit loop could not be verified ({} lap coverage, {} largest gap, {} normalized closure distance).",
            format(coverage, ".1%")?,
            format(gap, ".1%")?,
            format(closure, ".4f")?,
        )));
    }
    let partial = if main_loop_complete {
        "partial"
    } else {
        "unavailable"
    };
    if !pit_recorded {
        status = partial;
        reasons.push(PyValue::from(
            "Pit-road state was not recorded, so pit lane, entry, exit, commitment, and merge layers are unavailable.",
        ));
    } else if pit_lane.is_empty() {
        status = partial;
        reasons.push(PyValue::from(
            "No complete recorded pit-road traversal was available.",
        ));
    }
    if !rejected_layers.is_empty() {
        status = partial;
        let mut sorted = rejected_layers.clone();
        sorted.sort_unstable();
        reasons.push(PyValue::from(format!(
            "Implausible recorded track overlays were omitted: {}.",
            sorted.join(", ")
        )));
    }
    result.insert("status", status);
    result.insert("unavailable_reasons", PyValue::List(reasons));
    if main_loop_complete {
        let hash = track_geometry_sha256(&result)?;
        result.insert("geometry_hash", hash);
    }
    Ok(PyValue::Dict(result))
}

#[cfg(test)]
mod tests {
    use super::{resample, safe_slug};

    #[test]
    fn slug_folds_and_joins() {
        assert_eq!(safe_slug("9001-Oval"), "9001-oval");
        assert_eq!(safe_slug("track-Stra\u{DF}e 2"), "track-strasse-2");
        assert_eq!(safe_slug("--"), "unknown");
        assert_eq!(safe_slug("a\u{17F}b"), "asb");
    }

    #[test]
    fn resample_rounds_half_to_even() {
        // round(1 * 4 / 2) == 2, round(1 * 3 / 2) == 2 (1.5 to even)
        assert_eq!(resample(5, 3).unwrap(), vec![0, 2, 4]);
        assert_eq!(resample(4, 3).unwrap(), vec![0, 2, 3]);
        assert_eq!(resample(2, 3).unwrap(), vec![0, 1]);
    }
}

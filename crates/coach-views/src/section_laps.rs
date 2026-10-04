//! `InvestigationView.section_laps`: per detected section, the clean laps' times through it, their
//! median and the three fastest and three slowest of them (web round 3, backlog #48 and #49).
//!
//! A lap's section time is the backend's own rule, `race_inspection.section_elapsed` (the time a
//! pass pair's `section_s` is measured with), ported line for line and checked against vectors the
//! Python function produced (`tests/data/section-elapsed-vectors.json`). "Clean" is the backend's
//! lap screen, `race_investigation.laps[].eligible`; nothing is screened again here.

use serde_json::{Value, json};

use crate::parse::RawTrace;
use crate::val::{Obj, arr, flag, int, num, obj, text};

/// `race_inspection.MAX_POINT_STEP`: trace bins are about 0.6 % lap; a larger step is missing coverage.
const MAX_POINT_STEP: f64 = 0.03;
/// `race_inspection.MAX_POINT_SECONDS`.
const MAX_POINT_SECONDS: f64 = 2.0;
/// Laps shown at each end of a section's ranking.
const SHOWN: usize = 3;
/// `traces::SESSION_TIME`, the core column of `session_time_s`.
const SESSION_TIME: usize = 1;

/// One recorded point: lap fraction and session time (`None` when the sample has none).
type Point = (f64, Option<f64>);

/// `race_inspection._points`: the trace's points in recorded order, or none when the lap fraction
/// ever fails to increase (corrupt or reversed telemetry is never sorted into a plausible drive).
fn ordered(pct: &[f64], time: &[f64]) -> Vec<Point> {
    let points: Vec<Point> = pct
        .iter()
        .zip(time)
        .map(|(x, t)| (*x, t.is_finite().then_some(*t)))
        .collect();
    if points.windows(2).any(|w| w[0].0 >= w[1].0) {
        return Vec::new();
    }
    points
}

/// `race_investigation._crossing`: session time at `position`, interpolated inside the first sample
/// pair that straddles it and is a plausible step (0 < dx <= 0.03 lap, 0 < dt <= 2 s).
fn crossing(points: &[Point], position: f64) -> Option<f64> {
    for w in points.windows(2) {
        let ((x0, t0), (x1, t1)) = (w[0], w[1]);
        let (Some(t0), Some(t1)) = (t0, t1) else {
            continue;
        };
        if !(0.0 < x1 - x0 && x1 - x0 <= 0.03) || !(0.0 < t1 - t0 && t1 - t0 <= 2.0) {
            continue;
        }
        if x0 <= position && position <= x1 {
            return Some(t0 + (position - x0) / (x1 - x0) * (t1 - t0));
        }
    }
    None
}

/// `race_inspection._covered`: both boundaries cross, and every sample pair touching the section is
/// timed and plausible.
fn covered(points: &[Point], start: f64, end: f64) -> bool {
    if crossing(points, start).is_none() || crossing(points, end).is_none() {
        return false;
    }
    for w in points.windows(2) {
        let ((x0, t0), (x1, t1)) = (w[0], w[1]);
        if x1 <= start || x0 >= end {
            continue;
        }
        let (Some(t0), Some(t1)) = (t0, t1) else {
            return false;
        };
        if !(0.0 < t1 - t0 && t1 - t0 <= MAX_POINT_SECONDS) || x1 - x0 > MAX_POINT_STEP {
            return false;
        }
    }
    true
}

/// `race_inspection.section_elapsed`: recorded time across a section, withheld when coverage has a gap.
/// `time` holds NaN where a sample has no session time.
pub(crate) fn section_elapsed(pct: &[f64], time: &[f64], start: f64, end: f64) -> Option<f64> {
    let points = ordered(pct, time);
    if !covered(&points, start, end) {
        return None;
    }
    let entered = crossing(&points, start)?;
    let exited = crossing(&points, end)?;
    (exited > entered).then_some(exited - entered)
}

/// Python's `round(x, 3)`: the exact binary value rounded to 3 decimals, ties to even (Rust's
/// fixed-precision formatting rounds the same way).
pub(crate) fn round3(value: f64) -> f64 {
    format!("{value:.3}").parse().unwrap_or(value)
}

/// `statistics.median` of a sorted, non-empty slice.
fn median(sorted: &[f64]) -> f64 {
    let middle = sorted.len() / 2;
    if sorted.len() % 2 == 1 {
        sorted[middle]
    } else {
        (sorted[middle - 1] + sorted[middle]) / 2.0
    }
}

fn entry(lap: i64, seconds: f64, middle: f64) -> Value {
    json!({ "lap": lap, "section_s": seconds, "delta_s": round3(seconds - middle) })
}

/// The `section_laps` object for one investigation: one entry per listed section with valid bounds
/// (0 <= start < end <= 1), in the backend's order.
pub(crate) fn build(investigation: &Obj, traces: &[RawTrace]) -> Value {
    let clean: Vec<i64> = arr(investigation, "laps")
        .iter()
        .filter_map(Value::as_object)
        .filter(|lap| flag(lap, "eligible") == Some(true))
        .filter_map(|lap| int(lap, "lap"))
        .collect();
    let laps: Vec<(i64, &RawTrace)> = traces
        .iter()
        .filter_map(|trace| {
            let lap = trace.meta.get("lap").and_then(crate::val::int_of)?;
            clean.contains(&lap).then_some((lap, trace))
        })
        .collect();
    let sections = obj(investigation, "sections")
        .map(|s| arr(s, "sections"))
        .unwrap_or(&[]);
    let mut out = Vec::new();
    for section in sections.iter().filter_map(Value::as_object) {
        let (Some(id), Some(start), Some(end)) = (
            text(section, "id"),
            num(section, "start_pct"),
            num(section, "end_pct"),
        ) else {
            continue;
        };
        if !(0.0 <= start && start < end && end <= 1.0) {
            continue;
        }
        let mut timed: Vec<(f64, i64)> = laps
            .iter()
            .filter_map(|(lap, trace)| {
                section_elapsed(&trace.lap_pct, &trace.core[SESSION_TIME], start, end)
                    .map(|s| (round3(s), *lap))
            })
            .collect();
        timed.sort_by(|a, b| a.0.total_cmp(&b.0).then(a.1.cmp(&b.1)));
        let times: Vec<f64> = timed.iter().map(|(s, _)| *s).collect();
        let middle = (!times.is_empty()).then(|| round3(median(&times)));
        let shown = SHOWN.min(timed.len() / 2);
        let (fastest, slowest): (Vec<Value>, Vec<Value>) = match middle {
            Some(m) => (
                timed[..shown]
                    .iter()
                    .map(|(s, lap)| entry(*lap, *s, m))
                    .collect(),
                timed[timed.len() - shown..]
                    .iter()
                    .rev()
                    .map(|(s, lap)| entry(*lap, *s, m))
                    .collect(),
            ),
            None => (Vec::new(), Vec::new()),
        };
        out.push(json!({
            "section_id": id,
            "clean_lap_count": timed.len(),
            "median_s": middle,
            "fastest": fastest,
            "slowest": slowest,
        }));
    }
    json!({ "sections": out })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn vectors() -> Value {
        serde_json::from_str(include_str!("../tests/data/section-elapsed-vectors.json")).unwrap()
    }

    #[test]
    fn section_elapsed_matches_the_backend_on_every_vector() {
        let doc = vectors();
        let mut checked = 0;
        let mut withheld = 0;
        for case in doc["cases"].as_array().unwrap() {
            let points = case["points"].as_array().unwrap();
            let pct: Vec<f64> = points.iter().map(|p| p[0].as_f64().unwrap()).collect();
            let time: Vec<f64> = points
                .iter()
                .map(|p| p[1].as_f64().unwrap_or(f64::NAN))
                .collect();
            for section in case["sections"].as_array().unwrap() {
                let (start, end) = (section[0].as_f64().unwrap(), section[1].as_f64().unwrap());
                let got = section_elapsed(&pct, &time, start, end).map(round3);
                assert_eq!(got, section[2].as_f64(), "{} {start} {end}", case["kind"]);
                checked += 1;
                withheld += usize::from(got.is_none());
            }
        }
        assert_eq!(checked, 180);
        assert!(withheld > 20 && withheld < 160, "{withheld}");
    }

    #[test]
    fn round3_rounds_like_python() {
        assert_eq!(round3(0.0625), 0.062);
        assert_eq!(round3(0.0635), 0.064);
        assert_eq!(round3(2.675), 2.675);
        assert_eq!(round3(1.0005), 1.0);
        assert_eq!(round3(-0.1235), -0.123);
        assert_eq!(round3(6.4004999), 6.4);
    }

    #[test]
    fn median_follows_statistics_median() {
        assert_eq!(median(&[1.0]), 1.0);
        assert_eq!(median(&[1.0, 2.0]), 1.5);
        assert_eq!(median(&[1.0, 2.0, 9.0]), 2.0);
    }
}

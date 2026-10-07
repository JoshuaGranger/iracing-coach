//! A run's observed pace trend (web round 6, backlog #90 #91): the straight least-squares line of
//! the backend's `lap_time_s` against the lap number over the run's green, complete laps with close
//! traffic included. An observed trend over the backend's own lap rows, distinct from the backend's
//! screened pace fit (`race_investigation.runs[].fit`), which leaves traffic laps out.
//!
//! A lap counts when its projected race row (`laps::build`) is in the run, complete, green, under a
//! second on pit road, neither a pit-in nor a pit-out lap, timed, and carries no exclusion reason but
//! `close_traffic`: so not partial, caution, pit, out of the racing state, off track, tow or repair,
//! and not the restart lap after a caution (the slow first lap that made #91's fall-off negative).
//! A lap with an incident (`damage_repair.incident_points.events[].candidate_lap`) does not count.

use std::collections::BTreeSet;

use serde_json::{Map, Value, json};

use crate::val::{arr, flag, int, num, obj, text};

/// Fewest laps a trend is fitted over.
pub const MIN_TREND_LAPS: usize = 3;

/// The one exclusion reason a trend lap may carry.
const TRAFFIC: &str = "close_traffic";

/// Laps with an incident: `damage_repair.incident_points.events[].candidate_lap`.
pub fn incident_laps(damage_repair: &Value) -> BTreeSet<i64> {
    damage_repair
        .as_object()
        .and_then(|d| obj(d, "incident_points"))
        .map(|points| {
            arr(points, "events")
                .iter()
                .filter_map(Value::as_object)
                .filter_map(|event| int(event, "candidate_lap"))
                .collect()
        })
        .unwrap_or_default()
}

/// Whether a projected race lap row counts for its run's observed trend (module doc).
pub fn trend_lap(row: &Map<String, Value>, incidents: &BTreeSet<i64>) -> bool {
    let Some(lap) = int(row, "lap") else {
        return false;
    };
    lap > 0
        && !incidents.contains(&lap)
        && flag(row, "complete") == Some(true)
        && text(row, "flag_state").is_some_and(|f| f.trim().eq_ignore_ascii_case("green"))
        && num(row, "pit_time_s").unwrap_or(0.0) < 1.0
        && flag(row, "pit_in") != Some(true)
        && flag(row, "pit_out") != Some(true)
        && num(row, "lap_time_s").is_some_and(|t| t > 0.0)
        && arr(row, "exclusion_reasons")
            .iter()
            .all(|reason| reason.as_str() == Some(TRAFFIC))
}

fn round(value: f64, digits: i32) -> f64 {
    let scale = 10f64.powi(digits);
    (value * scale).round() / scale
}

/// The least-squares line through `(lap, lap_time_s)` points: `{slope_s_per_lap, intercept_s,
/// first_lap, last_lap, laps_used}`, the time at lap `x` being `intercept_s + slope_s_per_lap * x`.
/// Slope and intercept to 4 decimals. None under `MIN_TREND_LAPS` points or on a single lap.
pub fn fit(points: &[(i64, f64)]) -> Option<Value> {
    if points.len() < MIN_TREND_LAPS {
        return None;
    }
    let count = points.len() as f64;
    let mean_x = points.iter().map(|(x, _)| *x as f64).sum::<f64>() / count;
    let mean_y = points.iter().map(|(_, y)| *y).sum::<f64>() / count;
    let sxx: f64 = points
        .iter()
        .map(|(x, _)| (*x as f64 - mean_x).powi(2))
        .sum();
    if sxx <= 0.0 {
        return None;
    }
    let sxy: f64 = points
        .iter()
        .map(|(x, y)| (*x as f64 - mean_x) * (y - mean_y))
        .sum();
    let slope = sxy / sxx;
    let intercept = mean_y - slope * mean_x;
    let first = points.iter().map(|(x, _)| *x).min()?;
    let last = points.iter().map(|(x, _)| *x).max()?;
    Some(json!({
        "slope_s_per_lap": round(slope, 4),
        "intercept_s": round(intercept, 4),
        "first_lap": first,
        "last_lap": last,
        "laps_used": points.len(),
    }))
}

/// The observed trend of run `run` over the projected race lap rows; None when fewer than
/// `MIN_TREND_LAPS` laps count.
pub fn observed_trend(rows: &[Value], incidents: &BTreeSet<i64>, run: i64) -> Option<Value> {
    let points: Vec<(i64, f64)> = rows
        .iter()
        .filter_map(Value::as_object)
        .filter(|row| int(row, "run_number") == Some(run))
        .filter(|row| trend_lap(row, incidents))
        .filter_map(|row| Some((int(row, "lap")?, num(row, "lap_time_s")?)))
        .collect();
    fit(&points)
}

/// Sets `observed_trend` (an object, or null) on every backend run that has a `run_number`.
pub(crate) fn annotate(runs: &mut Value, rows: &[Value], damage_repair: &Value) {
    let Some(items) = runs.as_array_mut() else {
        return;
    };
    let incidents = incident_laps(damage_repair);
    for run in items.iter_mut().filter_map(Value::as_object_mut) {
        let Some(number) = int(run, "run_number") else {
            continue;
        };
        run.insert(
            "observed_trend".into(),
            observed_trend(rows, &incidents, number).unwrap_or(Value::Null),
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn row(lap: i64, run: i64, t: f64, reasons: &[&str]) -> Value {
        json!({"lap": lap, "run_number": run, "lap_time_s": t, "complete": true, "flag_state": "green",
               "pit_time_s": 0.0, "pit_in": false, "pit_out": false, "exclusion_reasons": reasons})
    }

    #[test]
    fn hand_computed_least_squares() {
        // laps 2..5, times 30.0 30.2 30.1 30.5: mean x 3.5, mean y 30.2,
        // sxx = 2.25+0.25+0.25+2.25 = 5, sxy = (-1.5)(-0.2)+(-0.5)(0)+(0.5)(-0.1)+(1.5)(0.3) = 0.7
        // slope 0.14, intercept 30.2 - 0.14*3.5 = 29.71
        let t = fit(&[(2, 30.0), (3, 30.2), (4, 30.1), (5, 30.5)]).unwrap();
        assert_eq!(
            t,
            json!({"slope_s_per_lap": 0.14, "intercept_s": 29.71, "first_lap": 2, "last_lap": 5, "laps_used": 4})
        );
        assert_eq!(fit(&[(2, 30.0), (3, 30.2)]), None, "under three laps");
        assert_eq!(fit(&[(2, 30.0), (2, 30.2), (2, 30.4)]), None, "one lap");
    }

    #[test]
    fn traffic_counts_and_everything_else_is_left_out() {
        let mut rows = vec![
            row(1, 1, 31.0, &["close_traffic"]),
            row(2, 1, 31.2, &[]),
            row(3, 1, 31.4, &["close_traffic"]),
        ];
        let mut out_lap = row(4, 1, 40.0, &[]);
        out_lap["pit_out"] = json!(true);
        let mut in_lap = row(5, 1, 40.0, &[]);
        in_lap["pit_in"] = json!(true);
        let mut caution = row(6, 1, 50.0, &["caution_or_mixed"]);
        caution["flag_state"] = json!("caution");
        let off = row(7, 1, 29.0, &["off_track", "close_traffic"]);
        let tow = row(8, 1, 29.0, &["pit_tow_or_repair_episode_overlap"]);
        let restart = row(9, 1, 34.0, &["close_traffic", "restart"]);
        let mut partial = row(10, 1, 5.0, &["partial"]);
        partial["complete"] = json!(false);
        let incident = row(11, 1, 33.0, &["close_traffic"]);
        let other_run = row(12, 2, 20.0, &[]);
        rows.extend([
            out_lap, in_lap, caution, off, tow, restart, partial, incident, other_run,
        ]);
        let incidents = incident_laps(
            &json!({"incident_points": {"events": [{"candidate_lap": 11, "points_added": 2}]}}),
        );
        let t = observed_trend(&rows, &incidents, 1).unwrap();
        assert_eq!(t["laps_used"], 3);
        assert_eq!(t["first_lap"], 1);
        assert_eq!(t["last_lap"], 3);
        assert_eq!(t["slope_s_per_lap"], 0.2);
        assert_eq!(t["intercept_s"], 30.8);
        assert_eq!(observed_trend(&rows, &incidents, 2), None, "one lap");
    }

    /// Backlog #91: the 10/02 Vegas win, run 1 laps 10-27 (times to the millisecond as recorded). The
    /// restart lap after the opening caution (33.686 s) is the slowest; laps then slow from about 30.9
    /// to 32.0. The old fall-off took last minus first, (32.068 - 33.686) / 17 = -0.095 s/lap, and read
    /// "-0.10 s/lap"; the trend leaves the restart lap out and is positive.
    #[test]
    fn the_restart_lap_does_not_turn_a_slowing_run_negative() {
        let times = [
            (10, 33.686, true),
            (11, 30.906, false),
            (12, 30.867, false),
            (13, 30.961, false),
            (14, 31.056, false),
            (15, 31.091, false),
            (16, 30.932, false),
            (17, 31.322, false),
            (18, 31.218, false),
            (19, 31.383, false),
            (20, 31.463, false),
            (21, 31.682, false),
            (22, 31.691, false),
            (23, 31.638, false),
            (24, 31.977, false),
            (25, 31.803, false),
            (26, 31.768, false),
            (27, 32.068, false),
        ];
        let rows: Vec<Value> = times
            .iter()
            .map(|&(lap, t, restart)| {
                row(
                    lap,
                    1,
                    t,
                    if restart {
                        &["close_traffic", "restart"]
                    } else {
                        &["close_traffic"]
                    },
                )
            })
            .collect();
        // Lap 14 had a 2x incident in that race.
        let incidents = incident_laps(
            &json!({"incident_points": {"events": [{"candidate_lap": 14, "points_added": 2.0}]}}),
        );
        let t = observed_trend(&rows, &incidents, 1).unwrap();
        assert_eq!(t["first_lap"], 11);
        assert_eq!(t["last_lap"], 27);
        assert_eq!(t["laps_used"], 16);
        // Hand-computed over laps 11-13 and 15-27 (16 laps): mean x 19.3125, mean y 31.423125,
        // sxx 381.4375, sxy 28.854375 -> slope 0.075646, intercept 29.962204.
        assert_eq!(t["slope_s_per_lap"], 0.0756);
        assert_eq!(t["intercept_s"], 29.9622);
    }
}

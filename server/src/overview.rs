//! `SessionSummary.overview`: the Sessions row figures of one session, read from its current
//! analysis's views (`race.json`, `investigation.json`). Every figure is the backend's; the
//! derived values (the green pace, the longest run's first-to-last clean-lap difference, the first
//! two timed laps) are read from the backend's own lap times over the laps its own runs and screen
//! name, and no lap is screened again here.

use std::path::Path;

use serde_json::{Map, Value, json};

use crate::app::AppState;
use crate::error::Result;

/// `race_summary` key of the analysis to the overview key.
const SUMMARY_KEYS: [(&str, &str); 12] = [
    ("starting_position", "starting_position"),
    ("final_recorded_position", "final_position"),
    ("recorded_laps", "recorded_laps"),
    ("green_laps_estimated", "green_laps_estimated"),
    ("caution_laps_estimated", "caution_laps_estimated"),
    ("pit_stops_detected", "pit_stops_detected"),
    ("runs_detected", "runs_detected"),
    ("longest_green_run", "longest_green_run"),
    ("pace_slope_s_per_lap", "pace_slope_s_per_lap"),
    ("pace_consistency_percent", "pace_consistency_percent"),
    ("best_clean_lap_s", "best_clean_lap_s"),
    ("fuel_used_gal", "fuel_used_gal"),
];

/// Keys every overview built by this rule carries, null when not measured. A stored overview
/// without them was built by an older rule (`refresh_on_start`).
pub const ROUND2_KEYS: [&str; 2] = ["green_pace_median_s", "pace_slope_supported"];

/// The round-3 Sessions figures (practice long run, qualifying laps), always sent like
/// `ROUND2_KEYS`.
pub const ROUND3_KEYS: [&str; 4] = [
    "longest_run_laps",
    "longest_run_pace_loss_s",
    "timed_lap_1_s",
    "timed_lap_2_s",
];

/// Every key the server always sends on `SessionSummary.overview`, null when not measured.
pub fn always_sent() -> impl Iterator<Item = &'static str> {
    ROUND2_KEYS.into_iter().chain(ROUND3_KEYS)
}

/// Fewest clean laps a run needs before its first-to-last difference is reported.
const PACE_LOSS_MIN_LAPS: usize = 3;

/// The overview of one analysis: `race` is `race.json`, `investigation` is `investigation.json`
/// (absent when the backend produced no investigation).
pub fn session_overview(race: &Value, investigation: Option<&Value>) -> Value {
    let mut out = Map::new();
    let summary = &race["race_summary"];
    for (from, to) in SUMMARY_KEYS {
        if let Some(v) = summary.get(from)
            && !v.is_null()
        {
            out.insert(to.to_string(), v.clone());
        }
    }
    let inv = investigation.map(|v| &v["race_investigation"]);
    out.insert(
        "green_pace_median_s".into(),
        json!(inv.and_then(green_pace_median)),
    );
    let slope = summary
        .get("pace_slope_s_per_lap")
        .is_some_and(|v| v.as_f64().is_some());
    out.insert(
        "pace_slope_supported".into(),
        json!(if slope {
            inv.and_then(fit_supported)
        } else {
            None
        }),
    );
    let longest = longest_run(race);
    out.insert("longest_run_laps".into(), json!(longest.map(|(_, n)| n)));
    out.insert(
        "longest_run_pace_loss_s".into(),
        json!(
            longest
                .zip(inv)
                .and_then(|((run, _), inv)| run_pace_loss(inv, run))
        ),
    );
    let timed = timed_laps(race);
    out.insert("timed_lap_1_s".into(), json!(timed.first()));
    out.insert("timed_lap_2_s".into(), json!(timed.get(1)));
    Value::Object(out)
}

/// The backend run with the most laps (`race.json` `laps[].run_number`, joined from the backend's
/// `runs[].lap_numbers`: the complete laps between two pit or stall visits, so a reset to the
/// pits or the garage starts a new run) and its lap count; the earlier run on a tie. None when no
/// lap names a run.
fn longest_run(race: &Value) -> Option<(i64, i64)> {
    let mut counts: Vec<(i64, i64)> = Vec::new();
    for run in race["laps"]
        .as_array()?
        .iter()
        .filter_map(|lap| lap["run_number"].as_i64())
    {
        match counts.iter_mut().find(|(n, _)| *n == run) {
            Some((_, count)) => *count += 1,
            None => counts.push((run, 1)),
        }
    }
    counts.sort_by_key(|(run, _)| *run);
    counts
        .into_iter()
        .fold(None, |best: Option<(i64, i64)>, (run, n)| match best {
            Some((_, m)) if m >= n => best,
            _ => Some((run, n)),
        })
}

/// Last minus first clean-lap time of one run, seconds (3 decimals): the green laps of that run
/// the backend's screen kept (`race_investigation.laps[]`, `eligible` and green), in lap order.
/// An observed difference, not a fit. None under `PACE_LOSS_MIN_LAPS` clean laps.
fn run_pace_loss(investigation: &Value, run: i64) -> Option<f64> {
    let mut laps: Vec<(i64, f64)> = investigation["laps"]
        .as_array()?
        .iter()
        .filter(|lap| lap["run_number"].as_i64() == Some(run))
        .filter(|lap| lap["eligible"] == true)
        .filter(|lap| {
            lap["flag_state"]
                .as_str()
                .is_some_and(|f| f.eq_ignore_ascii_case("green"))
        })
        .filter_map(|lap| Some((lap["lap"].as_i64()?, lap["lap_time_s"].as_f64()?)))
        .filter(|(_, t)| t.is_finite() && *t > 0.0)
        .collect();
    if laps.len() < PACE_LOSS_MIN_LAPS {
        return None;
    }
    laps.sort_by_key(|(lap, _)| *lap);
    let (first, last) = (laps.first()?.1, laps.last()?.1);
    Some(((last - first) * 1000.0).round() / 1000.0)
}

/// The first two timed laps' `lap_time_s` in lap order (`race.json` `laps[]`): complete, with a
/// time, under a second on pit road and neither a pit-in nor a pit-out lap, so the out lap is not
/// one of them. Fewer when the phase has fewer.
fn timed_laps(race: &Value) -> Vec<f64> {
    let Some(laps) = race["laps"].as_array() else {
        return Vec::new();
    };
    let mut timed: Vec<(i64, f64)> = laps
        .iter()
        .filter(|lap| lap["complete"] == true)
        .filter(|lap| lap["pit_time_s"].as_f64().unwrap_or(0.0) < 1.0)
        .filter(|lap| lap["pit_in"] != true && lap["pit_out"] != true)
        .filter_map(|lap| Some((lap["lap"].as_i64()?, lap["lap_time_s"].as_f64()?)))
        .filter(|(lap, t)| *lap > 0 && t.is_finite() && *t > 0.0)
        .collect();
    timed.sort_by_key(|(lap, _)| *lap);
    timed.into_iter().take(2).map(|(_, t)| t).collect()
}

/// Median `lap_time_s` of the green laps the backend's screen kept (`race_investigation.laps[]`
/// with `eligible: true`); None when it kept none.
fn green_pace_median(investigation: &Value) -> Option<f64> {
    let mut times: Vec<f64> = investigation["laps"]
        .as_array()?
        .iter()
        .filter(|lap| lap["eligible"] == true)
        .filter(|lap| {
            lap["flag_state"]
                .as_str()
                .is_some_and(|f| f.eq_ignore_ascii_case("green"))
        })
        .filter_map(|lap| lap["lap_time_s"].as_f64())
        .filter(|t| t.is_finite() && *t > 0.0)
        .collect();
    if times.is_empty() {
        return None;
    }
    times.sort_by(f64::total_cmp);
    let mid = times.len() / 2;
    let median = if times.len() % 2 == 1 {
        times[mid]
    } else {
        (times[mid - 1] + times[mid]) / 2.0
    };
    // the backend's lap times carry 3 decimals; the mean of two needs at most 4
    Some((median * 10_000.0).round() / 10_000.0)
}

/// Whether the backend's screened fit had enough eligible laps by its own rule: a run's `fit` is
/// null with `fit_unavailable_reason` when it had fewer than three screened laps with known
/// exposure (`race_inspection.run_views`). True when at least one run has a fit, false when none
/// has, None when the investigation reports no runs.
fn fit_supported(investigation: &Value) -> Option<bool> {
    let runs = investigation["runs"].as_array().filter(|r| !r.is_empty())?;
    Some(runs.iter().any(|run| run["fit"].is_object()))
}

/// The overview from a projected views directory; None when `race.json` cannot be read.
pub fn from_views(dir: &Path) -> Option<Value> {
    let read = |name: &str| {
        std::fs::read(dir.join(name))
            .ok()
            .and_then(|b| serde_json::from_slice::<Value>(&b).ok())
    };
    let race = read("race.json")?;
    let investigation = read("investigation.json");
    Some(session_overview(&race, investigation.as_ref()))
}

/// At start, in the background: sessions whose current analysis is projected at this version but
/// whose stored overview predates the round-2 or round-3 figures get them from their views. Idempotent; a
/// session whose views cannot be read keeps its overview.
pub async fn refresh_on_start(app: &AppState) -> Result<usize> {
    let version = app.projector.version();
    let rows: Vec<(i64, String)> = app
        .store
        .read(move |conn| {
            Ok(conn
                .prepare(
                    "SELECT s.session_id, s.current_analysis_id FROM sessions s
                     JOIN analyses a ON a.analysis_id = s.current_analysis_id
                     WHERE a.projection_state = 'ready' AND a.projection_version = ?1
                       AND (NOT json_valid(s.overview_json)
                            OR json_type(s.overview_json, '$.pace_slope_supported') IS NULL
                            OR json_type(s.overview_json, '$.longest_run_laps') IS NULL)",
                )?
                .query_map([version], |r| Ok((r.get(0)?, r.get(1)?)))?
                .collect::<rusqlite::Result<_>>()?)
        })
        .await?;
    let mut updates = Vec::new();
    for (session_id, analysis_id) in rows {
        if let Some(overview) = from_views(&app.cfg.views_dir(&analysis_id, version)) {
            updates.push((session_id, overview.to_string()));
        }
    }
    let count = updates.len();
    if count > 0 {
        app.store
            .write(move |conn| {
                let tx = conn.transaction()?;
                for (session_id, overview) in &updates {
                    tx.execute(
                        "UPDATE sessions SET overview_json = ?2 WHERE session_id = ?1",
                        rusqlite::params![session_id, overview],
                    )?;
                }
                tx.commit()?;
                Ok(())
            })
            .await?;
    }
    Ok(count)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn race(slope: Value) -> Value {
        json!({"race_summary": {"recorded_laps": 12, "final_recorded_position": 4, "pace_slope_s_per_lap": slope,
                                "best_clean_lap_s": 30.02, "fuel_used_gal": null}})
    }

    fn lap(n: i64, t: Value, eligible: bool, flag: &str) -> Value {
        json!({"lap": n, "lap_time_s": t, "eligible": eligible, "flag_state": flag})
    }

    #[test]
    fn copies_the_summary_and_adds_the_round_2_figures() {
        let inv = json!({"race_investigation": {
            "laps": [lap(1, json!(30.3), true, "green"), lap(2, json!(30.1), true, "green"),
                     lap(3, json!(30.2), true, "green"), lap(4, json!(40.0), false, "green"),
                     lap(5, json!(55.0), true, "caution"), lap(6, Value::Null, true, "green")],
            "runs": [{"run_number": 1, "fit": {"eligible_count": 3, "slope_s_per_green_lap": 0.01}}]}});
        let o = session_overview(&race(json!(0.0123)), Some(&inv));
        assert_eq!(o["recorded_laps"], 12);
        assert_eq!(o["final_position"], 4);
        assert!(
            o.get("fuel_used_gal").is_none(),
            "a null summary figure stays absent"
        );
        assert_eq!(o["green_pace_median_s"], 30.2, "screened green laps only");
        assert_eq!(o["pace_slope_supported"], true);
    }

    #[test]
    fn an_even_count_takes_the_mean_of_the_middle_pair() {
        let inv = json!({"race_investigation": {"laps": [lap(1, json!(30.08), true, "green"), lap(2, json!(30.1), true, "green")],
                                                "runs": []}});
        let o = session_overview(&race(Value::Null), Some(&inv));
        assert_eq!(o["green_pace_median_s"], 30.09);
        assert_eq!(
            o["pace_slope_supported"],
            Value::Null,
            "no slope: nothing to qualify"
        );
    }

    #[test]
    fn no_fit_in_any_run_is_unsupported() {
        let inv = json!({"race_investigation": {"laps": [lap(1, json!(30.0), true, "green")],
            "runs": [{"run_number": 1, "fit": null, "fit_unavailable_reason": "Fewer than three screened laps with known exposure."},
                     {"run_number": 2, "fit": null}]}});
        let o = session_overview(&race(json!(0.5)), Some(&inv));
        assert_eq!(o["pace_slope_supported"], false);
        assert_eq!(o["green_pace_median_s"], 30.0);
    }

    #[test]
    fn missing_evidence_is_null_never_a_guess() {
        // no investigation at all
        let o = session_overview(&race(json!(0.02)), None);
        assert_eq!(o["green_pace_median_s"], Value::Null);
        assert_eq!(o["pace_slope_supported"], Value::Null);
        // an investigation that kept no lap and reports no run
        let inv = json!({"race_investigation": {"schema_version": 2,
            "laps": [lap(1, json!(31.0), false, "green")]}});
        let o = session_overview(&race(json!(0.02)), Some(&inv));
        assert_eq!(o["green_pace_median_s"], Value::Null);
        assert_eq!(o["pace_slope_supported"], Value::Null);
        for key in ROUND2_KEYS {
            assert!(o.get(key).is_some(), "{key} is always sent");
        }
    }

    /// A `race.json` lap row as the projector writes it.
    fn row(n: i64, t: Value, run: Option<i64>, complete: bool, pit_s: f64) -> Value {
        json!({"lap": n, "lap_time_s": t, "run_number": run, "complete": complete, "pit_time_s": pit_s,
               "pit_in": false, "pit_out": false})
    }

    fn inv_lap(n: i64, t: f64, run: i64, eligible: bool) -> Value {
        json!({"lap": n, "lap_time_s": t, "eligible": eligible, "flag_state": "green", "run_number": run})
    }

    #[test]
    fn the_longest_run_and_its_first_to_last_clean_lap_difference() {
        // run 1: laps 1-3; run 2 (after a reset to the pits): laps 4-9, the longest
        let mut laps: Vec<Value> = (1..=3)
            .map(|n| row(n, json!(30.0), Some(1), true, 0.0))
            .collect();
        laps.extend((4..=9).map(|n| row(n, json!(30.0), Some(2), true, 0.0)));
        laps.push(row(10, json!(45.0), None, false, 0.0));
        let race = json!({"race_summary": {}, "laps": laps});
        let inv = json!({"race_investigation": {"laps": [
            inv_lap(1, 29.5, 1, true), inv_lap(2, 29.6, 1, true), inv_lap(3, 29.7, 1, true),
            inv_lap(4, 31.0, 2, false), inv_lap(5, 30.10, 2, true), inv_lap(6, 30.15, 2, true),
            inv_lap(7, 30.40, 2, false), inv_lap(8, 30.31, 2, true), inv_lap(9, 33.0, 2, false)],
            "runs": []}});
        let o = session_overview(&race, Some(&inv));
        assert_eq!(o["longest_run_laps"], 6);
        // laps 5, 6 and 8 kept: 30.31 - 30.10, the screened-out laps 4, 7 and 9 left out
        assert_eq!(o["longest_run_pace_loss_s"], 0.21);
        // a faster end is a negative difference
        let inv = json!({"race_investigation": {"laps": [
            inv_lap(4, 30.5, 2, true), inv_lap(5, 30.2, 2, true), inv_lap(9, 30.1, 2, true)]}});
        assert_eq!(
            session_overview(&race, Some(&inv))["longest_run_pace_loss_s"],
            -0.4
        );
    }

    #[test]
    fn a_run_with_fewer_than_three_clean_laps_has_no_pace_loss() {
        let race = json!({"race_summary": {}, "laps": [row(1, json!(30.0), Some(1), true, 0.0),
                          row(2, json!(30.0), Some(1), true, 0.0), row(3, json!(30.0), Some(1), true, 0.0)]});
        let inv = json!({"race_investigation": {"laps": [
            inv_lap(1, 30.0, 1, true), inv_lap(2, 30.2, 1, true), inv_lap(3, 30.4, 1, false)]}});
        let o = session_overview(&race, Some(&inv));
        assert_eq!(o["longest_run_laps"], 3);
        assert_eq!(o["longest_run_pace_loss_s"], Value::Null);
        // no investigation: the run is known, its pace loss is not
        let o = session_overview(&race, None);
        assert_eq!(o["longest_run_laps"], 3);
        assert_eq!(o["longest_run_pace_loss_s"], Value::Null);
        // equal runs: the earlier one
        let race = json!({"race_summary": {}, "laps": [row(1, json!(30.0), Some(1), true, 0.0),
                          row(2, json!(30.0), Some(2), true, 0.0)]});
        let inv = json!({"race_investigation": {"laps": [
            inv_lap(1, 30.0, 1, true), inv_lap(2, 30.0, 2, true)]}});
        assert_eq!(session_overview(&race, Some(&inv))["longest_run_laps"], 1);
    }

    #[test]
    fn qualifying_takes_the_first_two_timed_laps_not_the_out_lap() {
        let mut out_lap = row(1, json!(41.2), Some(1), true, 12.0);
        out_lap["pit_out"] = json!(true);
        let race = json!({"race_summary": {}, "laps": [
            row(3, json!(29.811), Some(1), true, 0.0),
            out_lap,
            row(2, json!(29.902), Some(1), true, 0.0),
            row(4, json!(35.0), Some(1), false, 0.0)]});
        let o = session_overview(&race, None);
        assert_eq!(o["timed_lap_1_s"], 29.902);
        assert_eq!(o["timed_lap_2_s"], 29.811);
        // one timed lap: the second is null
        let race = json!({"race_summary": {}, "laps": [row(2, json!(29.9), None, true, 0.0),
                          row(3, Value::Null, None, true, 0.0)]});
        let o = session_overview(&race, None);
        assert_eq!(o["timed_lap_1_s"], 29.9);
        assert_eq!(o["timed_lap_2_s"], Value::Null);
        // no laps at all: every round-3 key present and null
        let o = session_overview(&json!({"race_summary": {}}), None);
        for key in ROUND3_KEYS {
            assert_eq!(o[key], Value::Null, "{key} is always sent");
        }
    }

    #[test]
    fn the_sanitized_investigation_fixture() {
        let path =
            Path::new(env!("CARGO_MANIFEST_DIR")).join("../test-data/race-investigation.json");
        let inv: Value = serde_json::from_slice(&std::fs::read(path).unwrap()).unwrap();
        let o = session_overview(
            &race(json!(0.02)),
            Some(&json!({"race_investigation": inv})),
        );
        // laps 1-9 kept (30.02 to 30.18 s), the caution and excluded laps left out; run 1 has a 9-lap fit
        assert_eq!(o["green_pace_median_s"], 30.1);
        assert_eq!(o["pace_slope_supported"], true);
        // run 1's twelve laps: laps 1-9 kept, 30.02 s to 30.18 s
        let mut with_laps = race(json!(0.02));
        with_laps["laps"] = json!(
            (1..=12)
                .map(|n| row(n, json!(30.0), Some(1), true, 0.0))
                .collect::<Vec<_>>()
        );
        let o = session_overview(&with_laps, Some(&json!({"race_investigation": inv})));
        assert_eq!(o["longest_run_laps"], 12);
        assert_eq!(o["longest_run_pace_loss_s"], 0.16);
    }
}

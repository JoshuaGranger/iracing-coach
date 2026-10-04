//! `SessionSummary.overview`: the Sessions row figures of one session, read from its current
//! analysis's views (`race.json`, `investigation.json`). Every figure is the backend's; the one
//! derived value, the green pace, is an order statistic of the backend's own lap times over the
//! laps its own screen kept, and no lap is screened again here.

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
    Value::Object(out)
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
/// whose stored overview predates the round-2 figures get them from their views. Idempotent; a
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
                            OR json_type(s.overview_json, '$.pace_slope_supported') IS NULL)",
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
    }
}

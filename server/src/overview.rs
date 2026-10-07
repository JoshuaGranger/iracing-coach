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

/// The round-4 Sessions figures (field size and official position, the grid position a race
/// finish is measured against, burn per lap), always sent like `ROUND2_KEYS`.
pub const ROUND4_KEYS: [&str; 4] = [
    "field_size",
    "results_position",
    "start_position",
    "fuel_per_lap_gal",
];

/// The round-6 race figures (backlog #85): the fastest and the median green lap with traffic
/// included, so a race whose every lap had close traffic still shows a best and a pace. Always
/// sent like `ROUND2_KEYS`.
pub const ROUND6_KEYS: [&str; 2] = ["best_green_lap_s", "race_pace_median_s"];

/// Every key the server always sends on `SessionSummary.overview`, null when not measured.
pub fn always_sent() -> impl Iterator<Item = &'static str> {
    ROUND2_KEYS
        .into_iter()
        .chain(ROUND3_KEYS)
        .chain(ROUND4_KEYS)
        .chain(ROUND6_KEYS)
}

/// The backend's lap exclusion reasons (`race.json` `laps[].exclusion_reasons`) that describe the
/// race around the car, not the lap itself: a green lap with only these still counts as racing
/// pace (`green_laps`).
const RACING_CONTEXT_REASONS: [&str; 5] = [
    "close_traffic",
    "restart",
    // Driving on with damage: a real race lap, never faster than the car allows (backlog #95).
    "damage_repair_context",
    "recorded_repair_evidence",
    "repair_correlated_candidate",
];

/// The damage codes that mark the tow or repair episode itself; those laps never count.
const REPAIR_EPISODE_CODES: [&str; 1] = ["pit_tow_or_repair_episode_overlap"];

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
    let green = green_laps(race);
    out.insert(
        "best_green_lap_s".into(),
        json!(green.iter().copied().min_by(f64::total_cmp)),
    );
    out.insert("race_pace_median_s".into(), json!(median(green)));
    let timed = timed_laps(race);
    out.insert("timed_lap_1_s".into(), json!(timed.first()));
    out.insert("timed_lap_2_s".into(), json!(timed.get(1)));
    // the backend's own burn figure: the median green-lap burn over its runs
    // (`strategy.measured_green_fuel_gal_per_lap`), never recomputed here
    out.insert(
        "fuel_per_lap_gal".into(),
        json!(
            race["strategy"]["measured_green_fuel_gal_per_lap"]
                .as_f64()
                .filter(|v| v.is_finite() && *v > 0.0)
        ),
    );
    // the session-info figures: `merge_results` fills them from the event's recordings
    for key in ["field_size", "results_position", "start_position"] {
        out.insert(key.into(), Value::Null);
    }
    Value::Object(out)
}

// ------------------------------------------------------------ official results

/// The last entry of `key` in a YAML map (Python dict semantics).
fn yaml_field<'a>(node: &'a coach_ibt::YamlNode, key: &str) -> Option<&'a coach_ibt::YamlNode> {
    match node {
        coach_ibt::YamlNode::Map(entries) => {
            entries.iter().rev().find(|(k, _)| k == key).map(|(_, v)| v)
        }
        _ => None,
    }
}

fn yaml_int(node: Option<&coach_ibt::YamlNode>) -> Option<i64> {
    use coach_ibt::{YamlNode, YamlScalar};
    let scalar = match node? {
        YamlNode::Scalar(s) => s.typed(),
        YamlNode::Literal(s) => s.clone(),
        _ => return None,
    };
    match scalar {
        YamlScalar::Int(i) => Some(i),
        YamlScalar::Float(f) if f.is_finite() && f.fract() == 0.0 => Some(f as i64),
        YamlScalar::Str(text) => text.trim().parse().ok(),
        _ => None,
    }
}

/// One results list (`ResultsPositions` or `QualifyResultsInfo.Results`): the number of cars it
/// lists and the player's place in it, counted by its overall `Position` (never `ClassPosition`),
/// so a list that numbers from 0 and one that numbers from 1 give the same place. None when the
/// list names no car.
fn standing(list: Option<&coach_ibt::YamlNode>, car_idx: Option<i64>) -> Option<Value> {
    let coach_ibt::YamlNode::List(items) = list? else {
        return None;
    };
    let mut cars: Vec<(i64, i64)> = Vec::new();
    for item in items {
        let (Some(car), Some(position)) = (
            yaml_int(yaml_field(item, "CarIdx")),
            yaml_int(yaml_field(item, "Position")),
        ) else {
            continue;
        };
        if car >= 0 && !cars.iter().any(|(c, _)| *c == car) {
            cars.push((car, position));
        }
    }
    if cars.is_empty() {
        return None;
    }
    let position = car_idx
        .and_then(|me| cars.iter().find(|(c, _)| *c == me))
        .map(|(_, mine)| 1 + cars.iter().filter(|(_, p)| p < mine).count() as i64);
    Some(json!({"field_size": cars.len(), "position": position}))
}

/// What a recording's session info says about results, stored as `recordings.header_json.results`:
/// the player's `CarIdx`; per session that lists results, the number of cars and the player's
/// overall place; `grid`, the same from `QualifyResultsInfo.Results` (null when absent). The
/// session info is the one the sim wrote when the recording started, so a session's results are
/// final only in a recording of a later session (`merge_results`).
pub fn results_summary(info: &coach_ibt::SessionInfo) -> Value {
    let car_idx = yaml_int(info.get("DriverInfo.DriverCarIdx"));
    let mut sessions = Vec::new();
    if let Some(coach_ibt::YamlNode::List(items)) = info.get("SessionInfo.Sessions") {
        for item in items {
            let Some(num) = yaml_int(yaml_field(item, "SessionNum")) else {
                continue;
            };
            if let Some(mut s) = standing(yaml_field(item, "ResultsPositions"), car_idx) {
                s["session_num"] = json!(num);
                sessions.push(s);
            }
        }
    }
    json!({
        "car_idx": car_idx,
        "sessions": sessions,
        "grid": standing(info.get("QualifyResultsInfo.Results"), car_idx),
    })
}

fn is_race(session_type: Option<&str>) -> bool {
    session_type.is_some_and(|t| t.to_ascii_lowercase().contains("race"))
}

/// Fills the session-info figures of one session's overview from the stored results of its
/// event's recordings (`header_json.results`, newest recording first):
/// - `field_size` and `results_position`: that session's results as listed by a recording of a
///   later session of the same event (only there are they final), sent together, null otherwise;
/// - `start_position`, races only: the player's place on the grid (`QualifyResultsInfo`) in a
///   recording of this race or later, else the backend's position at the green flag
///   (`starting_position`); null for other sessions.
pub fn merge_results(
    conn: &rusqlite::Connection,
    session_id: i64,
    overview: &mut Value,
) -> rusqlite::Result<()> {
    use rusqlite::OptionalExtension;
    if !overview.is_object() {
        return Ok(());
    }
    let row: Option<(i64, Option<i64>, Option<i64>, Option<String>)> = conn
        .query_row(
            "SELECT customer_id, subsession_id, sim_session_num, session_type FROM sessions WHERE session_id = ?1",
            [session_id],
            |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?)),
        )
        .optional()?;
    let Some((customer, subsession, num, session_type)) = row else {
        return Ok(());
    };
    let mut phase: Option<(Value, Value)> = None;
    let mut grid: Option<i64> = None;
    if let (Some(subsession), Some(num)) = (subsession.filter(|s| *s != 0), num) {
        let recordings: Vec<(String, Option<i64>)> = conn
            .prepare(
                "SELECT r.header_json, s.sim_session_num FROM recordings r
                 JOIN session_recordings sr ON sr.recording_sha256 = r.sha256
                 JOIN sessions s ON s.session_id = sr.session_id
                 WHERE s.customer_id = ?1 AND s.subsession_id = ?2
                 ORDER BY r.start_time_utc DESC, r.sha256",
            )?
            .query_map(rusqlite::params![customer, subsession], |r| {
                Ok((r.get(0)?, r.get(1)?))
            })?
            .collect::<rusqlite::Result<_>>()?;
        for (header, recorded) in recordings {
            let Some(recorded) = recorded else { continue };
            let header: Value = serde_json::from_str(&header).unwrap_or(Value::Null);
            let results = &header["results"];
            if phase.is_none() && recorded > num {
                phase = results["sessions"].as_array().and_then(|list| {
                    list.iter()
                        .find(|s| s["session_num"].as_i64() == Some(num))
                        .filter(|s| s["position"].as_i64().is_some())
                        .map(|s| (s["field_size"].clone(), s["position"].clone()))
                });
            }
            if grid.is_none() && recorded >= num {
                grid = results["grid"]["position"].as_i64();
            }
        }
    }
    let (field_size, position) = phase.unwrap_or((Value::Null, Value::Null));
    overview["field_size"] = field_size;
    overview["results_position"] = position;
    overview["start_position"] = if is_race(session_type.as_deref()) {
        json!(grid.or_else(|| overview["starting_position"].as_i64()))
    } else {
        Value::Null
    };
    Ok(())
}

/// Re-reads the session-info figures of the other sessions of `session_id`'s event (a later
/// recording makes an earlier session's results final). Returns how many overviews changed.
pub fn refresh_event(conn: &rusqlite::Connection, session_id: i64) -> rusqlite::Result<usize> {
    let siblings: Vec<(i64, String)> = conn
        .prepare(
            "SELECT o.session_id, o.overview_json FROM sessions s
             JOIN sessions o ON o.customer_id = s.customer_id AND o.subsession_id = s.subsession_id
             WHERE s.session_id = ?1 AND s.subsession_id IS NOT NULL AND s.subsession_id != 0
               AND o.session_id != s.session_id AND o.current_analysis_id IS NOT NULL",
        )?
        .query_map([session_id], |r| Ok((r.get(0)?, r.get(1)?)))?
        .collect::<rusqlite::Result<_>>()?;
    let mut changed = 0;
    for (id, stored) in siblings {
        let Ok(before) = serde_json::from_str::<Value>(&stored) else {
            continue;
        };
        if !before.is_object() {
            continue;
        }
        let mut after = before.clone();
        merge_results(conn, id, &mut after)?;
        if after != before {
            conn.execute(
                "UPDATE sessions SET overview_json = ?2 WHERE session_id = ?1",
                rusqlite::params![id, after.to_string()],
            )?;
            changed += 1;
        }
    }
    Ok(changed)
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

/// The `lap_time_s` of every complete green lap (`race.json` `laps[]`), traffic included: green
/// for the whole lap (`green_fraction` 1, or `flag_state` green), under a second on pit road and
/// neither a pit-in nor a pit-out lap, on track (`clean_context.on_track_fraction` at least the
/// backend's 0.98), not inside a tow or repair episode (`REPAIR_EPISODE_CODES`; laps driven on with damage count),
/// and no backend exclusion reason but racing context (`RACING_CONTEXT_REASONS`). The backend's own
/// rows and figures; nothing is measured again here.
fn green_laps(race: &Value) -> Vec<f64> {
    let Some(laps) = race["laps"].as_array() else {
        return Vec::new();
    };
    laps.iter()
        .filter(|lap| lap["complete"] == true)
        .filter(|lap| {
            lap["green_fraction"].as_f64().is_some_and(|f| f >= 1.0)
                || lap["flag_state"]
                    .as_str()
                    .is_some_and(|f| f.eq_ignore_ascii_case("green"))
        })
        .filter(|lap| lap["pit_time_s"].as_f64().unwrap_or(0.0) < 1.0)
        .filter(|lap| lap["pit_in"] != true && lap["pit_out"] != true)
        .filter(|lap| {
            lap["clean_context"]["on_track_fraction"]
                .as_f64()
                .is_none_or(|f| f >= 0.98)
        })
        .filter(|lap| {
            lap["damage_repair_context"]["exclusion_reason_codes"]
                .as_array()
                .is_none_or(|codes| {
                    !codes.iter().any(|c| {
                        c.as_str()
                            .is_some_and(|c| REPAIR_EPISODE_CODES.contains(&c))
                    })
                })
        })
        .filter(|lap| {
            lap["exclusion_reasons"].as_array().is_none_or(|reasons| {
                reasons.iter().all(|r| {
                    r.as_str()
                        .is_some_and(|r| RACING_CONTEXT_REASONS.contains(&r))
                })
            })
        })
        .filter(|lap| lap["lap"].as_i64().is_some_and(|n| n > 0))
        .filter_map(|lap| lap["lap_time_s"].as_f64())
        .filter(|t| t.is_finite() && *t > 0.0)
        .collect()
}

/// Median `lap_time_s` of the green laps the backend's screen kept (`race_investigation.laps[]`
/// with `eligible: true`); None when it kept none.
fn green_pace_median(investigation: &Value) -> Option<f64> {
    let times: Vec<f64> = investigation["laps"]
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
    median(times)
}

/// The median of lap times, rounded to 4 decimals; None when there are none.
fn median(mut times: Vec<f64>) -> Option<f64> {
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

/// Adds `results` (`results_summary`) to the stored header of recordings in a session that were
/// parsed before it, reading each recording's session info from the spool or, archived, from the
/// NAS (header and session info only). Returns how many rows gained it; a recording that cannot
/// be read is left as it is (the next start tries again).
pub async fn backfill_results(app: &AppState) -> Result<usize> {
    let rows: Vec<(String, String)> = app
        .store
        .read(|conn| {
            Ok(conn
                .prepare(
                    "SELECT DISTINCT r.sha256, r.header_json FROM recordings r
                     JOIN session_recordings sr ON sr.recording_sha256 = r.sha256
                     WHERE NOT json_valid(r.header_json) OR json_type(r.header_json, '$.results') IS NULL",
                )?
                .query_map([], |r| Ok((r.get(0)?, r.get(1)?)))?
                .collect::<rusqlite::Result<_>>()?)
        })
        .await?;
    let mut done = 0;
    for (sha, stored) in rows {
        let spool = crate::ingest::ready_path(app, &sha);
        let nas = app
            .archiver
            .nas_root()
            .join("raw")
            .join(&sha[..2.min(sha.len())])
            .join(format!("{sha}.ibt"));
        let parser = app.parser.clone();
        let results = tokio::task::spawn_blocking(move || {
            [spool, nas]
                .into_iter()
                .filter(|p| p.is_file())
                .find_map(|p| parser.results(&p).ok())
        })
        .await
        .ok()
        .flatten();
        let Some(results) = results else { continue };
        let mut header: Value = serde_json::from_str(&stored).unwrap_or_else(|_| json!({}));
        if !header.is_object() {
            header = json!({});
        }
        header["results"] = results;
        let (sha, text) = (sha.clone(), header.to_string());
        app.store
            .write(move |conn| {
                conn.execute(
                    "UPDATE recordings SET header_json = ?2 WHERE sha256 = ?1",
                    rusqlite::params![sha, text],
                )?;
                Ok(())
            })
            .await?;
        done += 1;
    }
    Ok(done)
}

/// At start, in the background: recordings stored before the results summary gain it
/// (`backfill_results`); sessions whose current analysis is projected at this version but whose
/// stored overview predates the round-2, round-3, round-4 or round-6 figures get them from their views;
/// every such overview then takes its session-info figures (`merge_results`) from the stored
/// results. Returns how many overviews changed. Idempotent; a session whose views cannot be read
/// keeps its view figures.
pub async fn refresh_on_start(app: &AppState) -> Result<usize> {
    if let Err(e) = backfill_results(app).await {
        tracing::warn!(error = %e, "recording results not backfilled");
    }
    let version = app.projector.version();
    let rows: Vec<(i64, String, String)> = app
        .store
        .read(move |conn| {
            Ok(conn
                .prepare(
                    "SELECT s.session_id, s.current_analysis_id, s.overview_json FROM sessions s
                     JOIN analyses a ON a.analysis_id = s.current_analysis_id
                     WHERE a.projection_state = 'ready' AND a.projection_version = ?1",
                )?
                .query_map([version], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))?
                .collect::<rusqlite::Result<_>>()?)
        })
        .await?;
    let mut candidates = Vec::new();
    for (session_id, analysis_id, stored) in rows {
        let before: Value = serde_json::from_str(&stored).unwrap_or(Value::Null);
        let stale = !before.is_object()
            || [
                "pace_slope_supported",
                "longest_run_laps",
                "fuel_per_lap_gal",
                "best_green_lap_s",
            ]
            .iter()
            .any(|key| before.get(key).is_none())
            // a null best is recomputed at every start: the green-lap rule may have widened (#95)
            || before.get("best_green_lap_s").is_some_and(Value::is_null);
        let after = if stale {
            from_views(&app.cfg.views_dir(&analysis_id, version))
        } else {
            Some(before.clone())
        };
        if let Some(after) = after {
            candidates.push((session_id, before, after));
        }
    }
    if candidates.is_empty() {
        return Ok(0);
    }
    app.store
        .write(move |conn| {
            let tx = conn.transaction()?;
            let mut count = 0;
            for (session_id, before, mut after) in candidates {
                merge_results(&tx, session_id, &mut after)?;
                if after != before {
                    tx.execute(
                        "UPDATE sessions SET overview_json = ?2 WHERE session_id = ?1",
                        rusqlite::params![session_id, after.to_string()],
                    )?;
                    count += 1;
                }
            }
            tx.commit()?;
            Ok(count)
        })
        .await
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
    fn burn_per_lap_is_the_backends_strategy_figure() {
        let mut r = race(Value::Null);
        r["strategy"] = json!({"measured_green_fuel_gal_per_lap": 0.424, "measured_green_fuel_l_per_lap": 1.605});
        let o = session_overview(&r, None);
        assert_eq!(o["fuel_per_lap_gal"], 0.424);
        // no strategy, or no measured green burn: null, never fuel used over the race
        let o = session_overview(&race(Value::Null), None);
        assert_eq!(o["fuel_per_lap_gal"], Value::Null);
        r["strategy"]["measured_green_fuel_gal_per_lap"] = Value::Null;
        assert_eq!(session_overview(&r, None)["fuel_per_lap_gal"], Value::Null);
        for key in ROUND4_KEYS {
            assert!(o.get(key).is_some(), "{key} is always sent");
        }
    }

    /// The session info of a race recording at Talladega as the sim writes it when the race
    /// starts: practice and qualifying final, the race list still the grid order, the grid in
    /// `QualifyResultsInfo` numbered from 0.
    const SESSION_INFO: &str = "\
DriverInfo:
 DriverCarIdx: 7
 Drivers:
 - CarIdx: 7
   UserName: Joshua Granger
SessionInfo:
 Sessions:
 - SessionNum: 0
   SessionType: Practice
   ResultsPositions:
   - Position: 1
     ClassPosition: 0
     CarIdx: 3
   - Position: 2
     ClassPosition: 1
     CarIdx: 7
   - Position: 3
     ClassPosition: 2
     CarIdx: 9
 - SessionNum: 1
   SessionType: Lone Qualify
   ResultsPositions:
   - Position: 1
     ClassPosition: 0
     CarIdx: 3
   - Position: 2
     ClassPosition: 1
     CarIdx: 9
   - Position: 3
     ClassPosition: 2
     CarIdx: 7
   - Position: 4
     ClassPosition: 3
     CarIdx: 11
 - SessionNum: 2
   SessionType: Race
   ResultsPositions:
QualifyResultsInfo:
 Results:
 - Position: 0
   ClassPosition: 0
   CarIdx: 3
 - Position: 1
   ClassPosition: 1
   CarIdx: 9
 - Position: 2
   ClassPosition: 2
   CarIdx: 7
 - Position: 3
   ClassPosition: 3
   CarIdx: 11
";

    #[test]
    fn the_results_summary_counts_places_by_overall_position() {
        let info = coach_ibt::SessionInfo::parse(SESSION_INFO).unwrap();
        let s = results_summary(&info);
        assert_eq!(s["car_idx"], 7);
        assert_eq!(
            s["sessions"],
            json!([{"session_num": 0, "field_size": 3, "position": 2},
                   {"session_num": 1, "field_size": 4, "position": 3}]),
            "a session with an empty list is left out"
        );
        // numbered from 0 there: the same place
        assert_eq!(s["grid"], json!({"field_size": 4, "position": 3}));
        // a player missing from a list: the field is known, the place is not
        let info = coach_ibt::SessionInfo::parse(
            &SESSION_INFO.replace("DriverCarIdx: 7", "DriverCarIdx: 42"),
        )
        .unwrap();
        let s = results_summary(&info);
        assert_eq!(s["grid"], json!({"field_size": 4, "position": null}));
        // nothing at all
        let s = results_summary(
            &coach_ibt::SessionInfo::parse("WeekendInfo:\n TrackName: x\n").unwrap(),
        );
        assert_eq!(s, json!({"car_idx": null, "sessions": [], "grid": null}));
    }

    fn db() -> rusqlite::Connection {
        let conn = rusqlite::Connection::open_in_memory().unwrap();
        conn.execute_batch(
            "CREATE TABLE sessions (session_id INTEGER PRIMARY KEY, customer_id INTEGER, subsession_id INTEGER,
               sim_session_num INTEGER, session_type TEXT, overview_json TEXT NOT NULL DEFAULT '{}', current_analysis_id TEXT);
             CREATE TABLE recordings (sha256 TEXT PRIMARY KEY, header_json TEXT NOT NULL DEFAULT '{}', start_time_utc TEXT);
             CREATE TABLE session_recordings (session_id INTEGER, recording_sha256 TEXT);",
        )
        .unwrap();
        conn
    }

    fn add(
        conn: &rusqlite::Connection,
        id: i64,
        num: i64,
        kind: &str,
        start: &str,
        results: Value,
    ) {
        conn.execute(
            "INSERT INTO sessions VALUES (?1, 1, 89012751, ?2, ?3, '{}', 'a')",
            rusqlite::params![id, num, kind],
        )
        .unwrap();
        let sha = format!("sha-{id}");
        conn.execute(
            "INSERT INTO recordings VALUES (?1, ?2, ?3)",
            rusqlite::params![sha, json!({"results": results}).to_string(), start],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO session_recordings VALUES (?1, ?2)",
            rusqlite::params![id, sha],
        )
        .unwrap();
    }

    #[test]
    fn an_events_results_come_from_its_later_recordings() {
        let conn = db();
        // each recording's own session lists only what was known when it started
        let early = |num: i64| json!({"car_idx": 7, "sessions": [{"session_num": num, "field_size": 1, "position": 1}], "grid": null});
        add(&conn, 25, 0, "Practice", "2026-09-30T02:32:36Z", early(0));
        add(
            &conn,
            24,
            1,
            "Lone Qualify",
            "2026-09-30T02:34:29Z",
            early(1),
        );
        let info = coach_ibt::SessionInfo::parse(SESSION_INFO).unwrap();
        add(
            &conn,
            23,
            2,
            "Race",
            "2026-09-30T02:39:30Z",
            results_summary(&info),
        );
        let overview = |id: i64, start: i64| {
            let mut o = json!({"starting_position": start, "final_position": 7});
            merge_results(&conn, id, &mut o).unwrap();
            o
        };
        // practice and qualifying: final in the race recording
        let o = overview(25, 3);
        assert_eq!(
            (o["results_position"].clone(), o["field_size"].clone()),
            (json!(2), json!(3))
        );
        assert_eq!(
            o["start_position"],
            Value::Null,
            "only a race has a grid position"
        );
        let o = overview(24, 3);
        assert_eq!(
            (o["results_position"].clone(), o["field_size"].clone()),
            (json!(3), json!(4))
        );
        // the race: its grid, not the first recorded position; its own results are not final
        let o = overview(23, 12);
        assert_eq!(o["start_position"], 3);
        assert_eq!(o["field_size"], Value::Null);
        assert_eq!(o["results_position"], Value::Null);
        // without a grid: the backend's position at the green flag
        conn.execute(
            "UPDATE recordings SET header_json = '{\"results\": null}' WHERE sha256 = 'sha-23'",
            [],
        )
        .unwrap();
        assert_eq!(overview(23, 12)["start_position"], 12);
        // and the qualifying session then has no later recording that lists it
        assert_eq!(overview(24, 3)["field_size"], Value::Null);
        assert_eq!(overview(24, 3)["results_position"], Value::Null);
    }

    #[test]
    fn a_later_recording_refreshes_the_events_other_overviews() {
        let conn = db();
        add(
            &conn,
            24,
            1,
            "Lone Qualify",
            "2026-09-30T02:34:29Z",
            Value::Null,
        );
        conn.execute(
            "UPDATE sessions SET overview_json = '{\"final_position\": 5}' WHERE session_id = 24",
            [],
        )
        .unwrap();
        let info = coach_ibt::SessionInfo::parse(SESSION_INFO).unwrap();
        add(
            &conn,
            23,
            2,
            "Race",
            "2026-09-30T02:39:30Z",
            results_summary(&info),
        );
        assert_eq!(refresh_event(&conn, 23).unwrap(), 1);
        let stored: String = conn
            .query_row(
                "SELECT overview_json FROM sessions WHERE session_id = 24",
                [],
                |r| r.get(0),
            )
            .unwrap();
        let o: Value = serde_json::from_str(&stored).unwrap();
        assert_eq!(o["final_position"], 5, "the recorded figure stays");
        assert_eq!(o["results_position"], 3);
        assert_eq!(o["field_size"], 4);
        assert_eq!(refresh_event(&conn, 23).unwrap(), 0, "idempotent");
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

    /// A `race.json` race lap row: complete, green, on track, with close traffic.
    fn race_row(n: i64, t: f64) -> Value {
        json!({"lap": n, "lap_time_s": t, "complete": true, "green_fraction": 1.0, "flag_state": "green",
               "pit_time_s": 0.0, "pit_in": false, "pit_out": false,
               "clean_context": {"on_track_fraction": 1.0, "traffic_proximity_fraction": 0.6},
               "damage_repair_context": {"automatic_coaching_reference_eligible": true, "exclusion_reason_codes": []},
               "exclusion_reasons": ["close_traffic"]})
    }

    #[test]
    fn a_race_in_traffic_has_a_best_and_a_pace_over_its_green_laps() {
        let mut r = race(Value::Null);
        r["race_summary"]["best_clean_lap_s"] = Value::Null;
        let mut laps = vec![
            race_row(1, 29.5),
            race_row(2, 28.5),
            race_row(3, 28.4),
            race_row(4, 28.6),
        ];
        // left out: partial, caution, pit in, pit out, off track, tow, pit-road time, lap 0
        let mut partial = race_row(5, 3.9);
        partial["complete"] = json!(false);
        let mut caution = race_row(6, 40.0);
        caution["green_fraction"] = json!(0.0);
        caution["flag_state"] = json!("caution");
        let mut pit_in = race_row(7, 35.0);
        pit_in["pit_in"] = json!(true);
        let mut pit_out = race_row(8, 45.0);
        pit_out["pit_out"] = json!(true);
        let mut off = race_row(9, 27.0);
        off["clean_context"]["on_track_fraction"] = json!(0.9);
        off["exclusion_reasons"] = json!(["off_track", "close_traffic"]);
        let mut tow = race_row(10, 26.0);
        tow["damage_repair_context"]["exclusion_reason_codes"] =
            json!(["pit_tow_or_repair_episode_overlap"]);
        let mut pit_road = race_row(11, 33.0);
        pit_road["pit_time_s"] = json!(4.0);
        let lap_zero = race_row(0, 20.0);
        // a restart lap is racing context like traffic
        let mut restart = race_row(12, 28.7);
        restart["exclusion_reasons"] = json!(["restart", "close_traffic"]);
        // a lap driven on with damage is a real race lap (backlog #95)
        let mut damaged = race_row(13, 28.8);
        damaged["exclusion_reasons"] = json!(["damage_repair_context", "close_traffic"]);
        damaged["damage_repair_context"]["exclusion_reason_codes"] = json!(["post_damage_lap"]);
        laps.extend([
            partial, caution, pit_in, pit_out, off, tow, pit_road, lap_zero, restart, damaged,
        ]);
        r["laps"] = json!(laps);
        let o = session_overview(&r, None);
        assert!(
            o.get("best_clean_lap_s").is_none(),
            "the screened best stays the backend's"
        );
        assert_eq!(o["best_green_lap_s"], 28.4);
        assert_eq!(
            o["race_pace_median_s"], 28.65,
            "29.5 28.5 28.4 28.6 28.7 28.8"
        );
        assert_eq!(
            o["green_pace_median_s"],
            Value::Null,
            "the screened pace is unchanged"
        );
    }

    #[test]
    fn no_green_lap_is_null() {
        let o = session_overview(&race(Value::Null), None);
        for key in ROUND6_KEYS {
            assert_eq!(o[key], Value::Null, "{key} is always sent");
        }
        let mut r = race(Value::Null);
        let mut out_lap = race_row(1, 40.0);
        out_lap["pit_out"] = json!(true);
        r["laps"] = json!([out_lap]);
        let o = session_overview(&r, None);
        assert_eq!(o["best_green_lap_s"], Value::Null);
        assert_eq!(o["race_pace_median_s"], Value::Null);
    }

    #[test]
    fn the_sanitized_traffic_heavy_race() {
        // every lap of this 82-lap race had close traffic, so the screen kept none
        let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("../test-data/g2/race.json");
        let race: Value = serde_json::from_slice(&std::fs::read(path).unwrap()).unwrap();
        assert_eq!(race["race_summary"]["best_clean_lap_s"], Value::Null);
        let o = session_overview(&race, None);
        // 80 of 83 laps: the partial last lap and the two pit-stop laps are left out
        assert_eq!(o["best_green_lap_s"], 28.408);
        assert_eq!(o["race_pace_median_s"], 28.5105);
    }
}

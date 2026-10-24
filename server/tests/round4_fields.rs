//! Web round 4 server fields (task S1, backlog #55-#57): the field size and official place of a
//! practice or qualifying session from a later recording of its event, the race's grid position,
//! and the backend's burn per lap, on `SessionSummary.overview`, always sent, and filled at start
//! for recordings and overviews stored before them.

mod support;

use axum::http::StatusCode;
use serde_json::{Value, json};
use support::*;

async fn ingest(env: &Env, meta: Value) {
    let reply = env
        .upload(&fake_ibt(meta, 4_000), "ibt", "rec.ibt", None, 4096)
        .await;
    assert_eq!(
        reply.status,
        StatusCode::OK,
        "{}",
        String::from_utf8_lossy(&reply.body)
    );
    coach_server::ingest::ingest_pending(&env.app)
        .await
        .unwrap();
}

#[tokio::test]
async fn practice_gets_its_field_and_the_race_its_grid() {
    let env = Env::new().await;
    let Some(_) = env.python.clone() else {
        eprintln!("skipped: no Python 3 for the stub worker");
        return;
    };
    let contract = Contract::load();
    // the practice recording knew only itself when it started
    let mut practice = race_meta(9961, 0, "practice");
    practice["session_type"] = json!("Practice");
    practice["results"] = json!({"car_idx": 7, "sessions": [{"session_num": 0, "field_size": 1, "position": 1}], "grid": null});
    ingest(&env, practice).await;
    // the race recording lists practice final and the grid (P12 of 24)
    let mut race = race_meta(9961, 2, "race");
    race["start"] = json!("2026-08-01T12:30:00Z");
    race["results"] = json!({"car_idx": 7,
        "sessions": [{"session_num": 0, "field_size": 24, "position": 3}],
        "grid": {"field_size": 24, "position": 12}});
    ingest(&env, race).await;
    env.settle().await;
    let archived: Vec<(String, String)> = env.query(|c| {
        c.prepare("SELECT sha256,relative_path FROM nas_library_objects WHERE kind='ibt' AND state='canonical_verified'")?
            .query_map([], |r| Ok((r.get(0)?, r.get(1)?)))?
            .collect()
    });
    assert_eq!(archived.len(), 2);
    for (sha, path) in &archived {
        assert!(env.nas().join(path).is_file());
        assert!(!coach_server::ingest::ready_path(&env.app, sha).exists());
        assert!(
            !env.nas()
                .join(format!("raw/{}/{sha}.ibt", &sha[..2]))
                .exists()
        );
    }

    let overviews = || async {
        let r = env
            .get(&format!("/api/v1/drivers/{CUSTOMER}/sessions"))
            .await;
        contract.check(
            "GET",
            "/api/v1/drivers/{customer_id}/sessions",
            r.status.as_u16(),
            "application/json",
            &r.json(),
        );
        let mut out = std::collections::BTreeMap::new();
        for s in r.json()["sessions"].as_array().unwrap() {
            out.insert(
                s["session_type"].as_str().unwrap().to_string(),
                s["overview"].clone(),
            );
        }
        out
    };
    let o = overviews().await;
    for key in coach_server::overview::ROUND4_KEYS {
        assert!(o["Practice"].get(key).is_some(), "{key} is always sent");
        assert!(o["Race"].get(key).is_some(), "{key} is always sent");
    }
    assert_eq!(o["Practice"]["results_position"], 3);
    assert_eq!(o["Practice"]["field_size"], 24);
    assert_eq!(o["Practice"]["start_position"], Value::Null);
    // the grid, not the backend's first recorded position (5 in the fake views)
    assert_eq!(o["Race"]["start_position"], 12);
    assert_eq!(o["Race"]["starting_position"], 5);
    assert_eq!(o["Race"]["field_size"], Value::Null, "no later recording");
    assert_eq!(
        o["Race"]["fuel_per_lap_gal"],
        Value::Null,
        "no strategy figure"
    );
    let practice_before_refresh = o["Practice"].clone();

    // stored before round 4: the race recording's header has no results, its overview no
    // round-4 keys; its views gain the backend's strategy figure
    let analysis: String = env.query(|c| {
        c.query_row(
            "SELECT current_analysis_id FROM sessions WHERE session_type = 'Race'",
            [],
            |r| r.get(0),
        )
    });
    let views = env.app.cfg.views_dir(&analysis, PROJECTION);
    let mut view: Value =
        serde_json::from_slice(&std::fs::read(views.join("race.json")).unwrap()).unwrap();
    view["strategy"] = json!({"measured_green_fuel_gal_per_lap": 0.471, "confidence": "medium"});
    std::fs::write(views.join("race.json"), view.to_string()).unwrap();
    env.app
        .store
        .write_blocking(|c| {
            c.execute(
                "UPDATE recordings SET header_json = json_remove(header_json, '$.results')
                 WHERE sha256 IN (SELECT sr.recording_sha256 FROM session_recordings sr
                                  JOIN sessions s ON s.session_id = sr.session_id WHERE s.session_type = 'Race')",
                [],
            )?;
            c.execute(
                "UPDATE sessions SET overview_json = '{\"recorded_laps\": 2, \"starting_position\": 5,
                   \"green_pace_median_s\": null, \"pace_slope_supported\": null, \"longest_run_laps\": null}'
                 WHERE session_type = 'Race'",
                [],
            )?;
            Ok(())
        })
        .unwrap();
    assert_eq!(
        coach_server::overview::refresh_on_start(&env.app)
            .await
            .unwrap(),
        1,
        "the old race overview is refreshed; practice is unchanged once the results are read back"
    );
    let o = overviews().await;
    assert_eq!(o["Practice"], practice_before_refresh);
    assert_eq!(o["Race"]["fuel_per_lap_gal"], 0.471);
    assert_eq!(o["Race"]["start_position"], 12);
    assert_eq!(o["Practice"]["field_size"], 24);
    let backfilled: Value = serde_json::from_str(&env.query(|c| {
        c.query_row(
            "SELECT r.header_json FROM recordings r JOIN session_recordings sr ON sr.recording_sha256 = r.sha256
             JOIN sessions s ON s.session_id = sr.session_id WHERE s.session_type = 'Race'",
            [],
            |r| r.get::<_, String>(0),
        )
    }))
    .unwrap();
    assert_eq!(backfilled["results"]["grid"]["position"], 12);
    assert_eq!(
        coach_server::overview::refresh_on_start(&env.app)
            .await
            .unwrap(),
        0,
        "idempotent"
    );
}

#[tokio::test]
async fn a_deleted_recording_cache_cannot_backfill_results() {
    let env = Env::new().await;
    let Some(_) = env.python.clone() else { return };
    ingest(&env, race_meta(9962, 0, "deleted backfill")).await;
    env.settle().await;
    let (sha, path): (String, String) = env.query(|c| {
        c.query_row(
            "SELECT sha256,relative_path FROM nas_library_objects WHERE kind='ibt'",
            [],
            |r| Ok((r.get(0)?, r.get(1)?)),
        )
    });
    let canonical = env.nas().join(path);
    let bytes = std::fs::read(&canonical).unwrap();
    std::fs::remove_file(&canonical).unwrap();
    coach_server::nas_library::reconcile(&env.app)
        .await
        .unwrap();
    assert!(
        coach_server::nas_library::is_deleted(&env.app, &sha)
            .await
            .unwrap()
    );
    // A stale rebuildable cache must not undo the deliberate library deletion.
    let spool = coach_server::ingest::ready_path(&env.app, &sha);
    std::fs::write(&spool, &bytes).unwrap();
    env.app
        .store
        .write(|c| {
            c.execute(
                "UPDATE recordings SET header_json=json_remove(header_json,'$.results')",
                [],
            )?;
            Ok(())
        })
        .await
        .unwrap();
    assert_eq!(
        coach_server::overview::backfill_results(&env.app)
            .await
            .unwrap(),
        0
    );
    let has_results: bool = env.query(|c| {
        c.query_row(
            "SELECT json_type(header_json,'$.results') IS NOT NULL FROM recordings",
            [],
            |r| r.get(0),
        )
    });
    assert!(!has_results);
    assert_eq!(std::fs::read(spool).unwrap(), bytes);
    assert!(!canonical.exists());
}

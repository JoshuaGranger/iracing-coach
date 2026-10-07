//! Web round 6 server fields (backlog #85): a race's fastest and median green lap with traffic
//! included (`best_green_lap_s`, `race_pace_median_s`) on `SessionSummary.overview`, always sent,
//! and filled at start for overviews stored before them.

mod support;

use axum::http::StatusCode;
use serde_json::{Value, json};
use support::*;

/// A race lap row as the backend writes it: complete, green, on track, in close traffic.
fn lap(n: i64, t: f64) -> Value {
    json!({"lap": n, "lap_time_s": t, "complete": true, "green_fraction": 1.0, "flag_state": "green",
           "pit_time_s": 0.0, "pit_in": false, "pit_out": false, "run_number": 1,
           "clean_context": {"on_track_fraction": 1.0, "traffic_proximity_fraction": 0.55},
           "damage_repair_context": {"automatic_coaching_reference_eligible": true, "exclusion_reason_codes": []},
           "exclusion_reasons": ["close_traffic"]})
}

#[tokio::test]
async fn a_race_in_traffic_gets_a_best_and_a_pace_and_old_overviews_are_refreshed() {
    let env = Env::new().await;
    let Some(_) = env.python.clone() else {
        eprintln!("skipped: no Python 3 for the stub worker");
        return;
    };
    let contract = Contract::load();
    let reply = env
        .upload(
            &fake_ibt(race_meta(9971, 2, "race"), 4_000),
            "ibt",
            "rec.ibt",
            None,
            4096,
        )
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
    env.settle().await;

    let race_overview = || async {
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
        r.json()["sessions"]
            .as_array()
            .unwrap()
            .iter()
            .find(|s| s["session_type"] == "Race")
            .unwrap()["overview"]
            .clone()
    };
    let o = race_overview().await;
    for key in coach_server::overview::ROUND6_KEYS {
        assert!(o.get(key).is_some(), "{key} is always sent");
    }

    // stored before round 6: the views gain the backend's lap rows (every lap in close traffic,
    // so the screen kept none), the overview has no round-6 keys
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
    let mut out_lap = lap(4, 41.0);
    out_lap["pit_out"] = json!(true);
    view["laps"] = json!([lap(1, 29.5), lap(2, 28.5), lap(3, 28.4), out_lap]);
    view["race_summary"]["best_clean_lap_s"] = Value::Null;
    std::fs::write(views.join("race.json"), view.to_string()).unwrap();
    env.app
        .store
        .write_blocking(|c| {
            c.execute(
                "UPDATE sessions SET overview_json = json_remove(overview_json, '$.best_green_lap_s', '$.race_pace_median_s')
                 WHERE session_type = 'Race'",
                [],
            )?;
            Ok(())
        })
        .unwrap();
    assert!(
        race_overview().await.get("best_green_lap_s").is_some(),
        "the API always sends it"
    );
    assert_eq!(
        coach_server::overview::refresh_on_start(&env.app)
            .await
            .unwrap(),
        1,
        "the old race overview is rebuilt from its views"
    );
    let o = race_overview().await;
    assert_eq!(o["best_green_lap_s"], 28.4);
    assert_eq!(o["race_pace_median_s"], 28.5);
    assert_eq!(
        coach_server::overview::refresh_on_start(&env.app)
            .await
            .unwrap(),
        0,
        "idempotent"
    );
}

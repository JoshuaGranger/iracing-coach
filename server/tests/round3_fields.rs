//! Web round 3 server fields (task R1, backlog #33): the Sessions row's practice long run with its
//! first-to-last clean-lap difference and the qualifying phase's first two timed laps, on
//! `SessionSummary.overview`, always sent, and filled at start for overviews stored before them.

mod support;

use axum::http::StatusCode;
use serde_json::{Value, json};
use support::*;

#[tokio::test]
async fn the_session_row_carries_the_long_run_and_the_first_two_timed_laps() {
    let env = Env::new().await;
    let Some(_) = env.python.clone() else {
        eprintln!("skipped: no Python 3 for the stub worker");
        return;
    };
    let contract = Contract::load();
    let mut practice = race_meta(9951, 0, "long-run");
    practice["session_type"] = json!("Practice");
    let reply = env
        .upload(&fake_ibt(practice, 4_000), "ibt", "rec.ibt", None, 4096)
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
    let session = || async {
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
        r.json()["sessions"][0]["overview"].clone()
    };
    // the stub's analysis: two laps with no run and no kept lap; the keys are sent, null where unmeasured
    let overview = session().await;
    for key in coach_server::overview::ROUND3_KEYS {
        assert!(overview.get(key).is_some(), "{key} is always sent");
    }
    assert_eq!(overview["longest_run_pace_loss_s"], Value::Null);

    // a session projected before this rule: its views gain an out lap, two runs (a reset to the
    // pits between them) and the backend's screen; the stored overview has only the round-2 keys
    let analysis: String =
        env.query(|c| c.query_row("SELECT current_analysis_id FROM sessions", [], |r| r.get(0)));
    let views = env.app.cfg.views_dir(&analysis, PROJECTION);
    let mut race: Value =
        serde_json::from_slice(&std::fs::read(views.join("race.json")).unwrap()).unwrap();
    let lap = |n: i64, t: f64, run: i64, pit_s: f64, pit_out: bool| {
        json!({"lap": n, "lap_time_s": t, "complete": true, "pit_time_s": pit_s, "pit_in": false,
               "pit_out": pit_out, "run_number": run, "comparison_eligible": true, "exclusion_reasons": []})
    };
    let mut laps = vec![
        lap(1, 44.1, 1, 14.0, true),
        lap(2, 30.412, 1, 0.0, false),
        lap(3, 30.388, 1, 0.0, false),
    ];
    laps.extend((4..=11).map(|n| lap(n, 30.0 + n as f64 * 0.01, 2, 0.0, false)));
    race["laps"] = json!(laps);
    std::fs::write(views.join("race.json"), race.to_string()).unwrap();
    let kept = |n: i64, t: f64, eligible: bool| json!({"lap": n, "lap_time_s": t, "eligible": eligible, "flag_state": "green", "run_number": 2});
    let mut view: Value =
        serde_json::from_slice(&std::fs::read(views.join("investigation.json")).unwrap()).unwrap();
    view["race_investigation"]["laps"] = json!([
        kept(4, 30.04, true),
        kept(5, 30.05, true),
        kept(6, 30.9, false),
        kept(11, 30.25, true)
    ]);
    std::fs::write(views.join("investigation.json"), view.to_string()).unwrap();
    env.app
        .store
        .write_blocking(|c| {
            c.execute(
                "UPDATE sessions SET overview_json =
                 '{\"recorded_laps\": 2, \"green_pace_median_s\": null, \"pace_slope_supported\": null}'",
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
        "an overview without the round-3 keys is refreshed"
    );
    let overview = session().await;
    assert_eq!(overview["longest_run_laps"], 8);
    assert_eq!(overview["longest_run_pace_loss_s"], 0.21);
    assert_eq!(overview["timed_lap_1_s"], 30.412);
    assert_eq!(overview["timed_lap_2_s"], 30.388);
    assert_eq!(
        coach_server::overview::refresh_on_start(&env.app)
            .await
            .unwrap(),
        0,
        "idempotent"
    );
}

//! Backlog #97: a race left before the flag. The backend's `race_summary.finish` reaches
//! `SessionSummary.overview` as `finished`, `retired_lap` and `retired_position`, always sent,
//! and overviews stored before the field are rebuilt from their views at start.

mod support;

use axum::http::StatusCode;
use serde_json::{Value, json};
use support::*;

#[tokio::test]
async fn a_race_left_mid_race_is_not_a_finish_and_old_overviews_are_refreshed() {
    let env = Env::new().await;
    let Some(_) = env.python.clone() else {
        eprintln!("skipped: no Python 3 for the stub worker");
        return;
    };
    let contract = Contract::load();
    let reply = env
        .upload(
            &fake_ibt(race_meta(9972, 2, "race"), 4_000),
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
    for key in coach_server::overview::FINISH_KEYS {
        assert!(o.get(key).is_some(), "{key} is always sent");
    }

    // stored before #97: the views gain the backend's finish (the 21:09 Charlotte race left in
    // the stall on lap 14, running P6), the overview has none of the finish keys
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
    view["race_summary"]["finish"] = json!({
        "status": "recording_ended", "finished": false, "last_lap": 14, "position_at_end": 6,
        "last_session_state": "racing", "laps_remaining_at_end": 77,
        "evidence_class": "measured_session_state", "rule": "test"});
    std::fs::write(views.join("race.json"), view.to_string()).unwrap();
    env.app
        .store
        .write_blocking(|c| {
            c.execute(
                "UPDATE sessions SET overview_json = json_remove(overview_json, '$.finished', '$.retired_lap', '$.retired_position')
                 WHERE session_type = 'Race'",
                [],
            )?;
            Ok(())
        })
        .unwrap();
    assert_eq!(
        race_overview().await["finished"],
        Value::Null,
        "the API always sends it, null until known"
    );
    assert_eq!(
        coach_server::overview::refresh_on_start(&env.app)
            .await
            .unwrap(),
        1,
        "the old race overview is rebuilt from its views"
    );
    let o = race_overview().await;
    assert_eq!(o["finished"], false);
    assert_eq!(o["retired_lap"], 14);
    assert_eq!(o["retired_position"], 6);
    assert_eq!(
        coach_server::overview::refresh_on_start(&env.app)
            .await
            .unwrap(),
        0,
        "idempotent"
    );
}

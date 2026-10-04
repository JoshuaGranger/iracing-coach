//! Web round 2 server fields (task C2): the Sessions row's green pace and supported-trend flag,
//! the setup library's race count, a setup's versions with the backend's changed-setting count,
//! the structured unit on compare rows, the Setups page's several cars, and the library parameter
//! on the setup detail.

mod support;

use axum::http::StatusCode;
use serde_json::{Value, json};
use support::*;

async fn ingest(env: &Env, meta: Value) {
    let bytes = fake_ibt(meta, 4_000);
    let reply = env.upload(&bytes, "ibt", "rec.ibt", None, 4096).await;
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

fn check(contract: &Contract, method: &str, template: &str, reply: &Reply) {
    contract.check(
        method,
        template,
        reply.status.as_u16(),
        "application/json",
        &reply.json(),
    );
}

fn with_setup(mut meta: Value, start: &str, arb: &str) -> Value {
    meta["start"] = json!(start);
    meta["setup"]["values"]["Chassis.Front.ArbSetting"] = json!(arb);
    meta["setup"]["values"]["Chassis.LeftFront.CornerWeight"] = json!(format!("17{arb}0 N"));
    meta
}

fn compare_jobs(env: &Env) -> i64 {
    env.query(|c| {
        c.query_row(
            "SELECT COUNT(*) FROM jobs WHERE kind = 'setup_compare'",
            [],
            |r| r.get(0),
        )
    })
}

#[tokio::test]
async fn setups_count_races_and_list_versions_with_the_backends_changed_count() {
    let env = Env::new().await;
    let Some(_) = env.python.clone() else {
        eprintln!("skipped: no Python 3 for the stub worker");
        return;
    };
    let contract = Contract::load();
    // two races and a practice on the first values, one race on the second
    ingest(
        &env,
        with_setup(race_meta(9901, 0, "r1"), "2026-08-01T12:00:00Z", "4"),
    )
    .await;
    ingest(
        &env,
        with_setup(race_meta(9902, 0, "r2"), "2026-08-02T12:00:00Z", "4"),
    )
    .await;
    let mut practice = with_setup(race_meta(9903, 0, "p"), "2026-08-03T12:00:00Z", "4");
    practice["session_type"] = json!("Practice");
    ingest(&env, practice).await;
    ingest(
        &env,
        with_setup(race_meta(9904, 0, "r3"), "2026-08-05T12:00:00Z", "5"),
    )
    .await;
    env.settle().await;

    let r = env.get(&format!("/api/v1/drivers/{CUSTOMER}/setups")).await;
    check(&contract, "GET", "/api/v1/drivers/{customer_id}/setups", &r);
    let mut counts: Vec<(i64, Value)> = r.json()["setups"]
        .as_array()
        .unwrap()
        .iter()
        .map(|s| (s["setup_id"].as_i64().unwrap(), s["raced_count"].clone()))
        .collect();
    counts.sort_by_key(|(id, _)| *id);
    assert_eq!(counts.len(), 2, "{counts:?}");
    let (first, second) = (counts[0].0, counts[1].0);
    assert_eq!(
        counts.iter().map(|(_, n)| n.clone()).collect::<Vec<_>>(),
        [json!(2), json!(1)],
        "distinct race sessions; the practice is not a race"
    );
    // the bot never drove them: no lab use, and Lab is asked explicitly
    let lab = env
        .get(&format!("/api/v1/drivers/{CUSTOMER}/setups?library=lab"))
        .await;
    assert!(lab.json()["setups"].as_array().unwrap().is_empty());

    // the detail: the newer version knows the older one; no count until the backend compares them
    let r = env.get(&format!("/api/v1/setups/{second}")).await;
    check(&contract, "GET", "/api/v1/setups/{setup_id}", &r);
    let detail = r.json();
    assert_eq!(detail["setup"]["raced_count"], 1);
    assert_eq!(
        detail["versions"],
        json!([
            {"setup_id": first, "first_seen_at": "2026-08-01T12:00:00Z", "last_seen_at": "2026-08-03T12:00:00Z",
             "previous_setup_id": null, "changed_count": null},
            {"setup_id": second, "first_seen_at": "2026-08-05T12:00:00Z", "last_seen_at": "2026-08-05T12:00:00Z",
             "previous_setup_id": first, "changed_count": null},
        ])
    );

    // the backend compares them (older left, as the Versions section asks)
    let pair = json!({"left_setup_id": first, "right_setup_id": second});
    let (r, _) = tokio::join!(env.post("/api/v1/setups/compare", pair.clone()), async {
        tokio::time::sleep(std::time::Duration::from_millis(300)).await;
        coach_server::jobs::drain(&env.app).await.unwrap();
    });
    assert_eq!(
        r.status,
        StatusCode::OK,
        "{}",
        String::from_utf8_lossy(&r.body)
    );
    check(&contract, "POST", "/api/v1/setups/compare", &r);
    let rows = r.json()["rows"].as_array().unwrap().clone();
    let row = |key: &str| rows.iter().find(|row| row["key"] == key).unwrap().clone();
    assert_eq!(row("Chassis.LeftFront.CornerWeight")["unit"], "N");
    assert_eq!(row("Chassis.LeftFront.CornerWeight")["left"], "1740 N");
    assert_eq!(row("Chassis.Front.ArbSetting")["unit"], Value::Null);
    assert_eq!(compare_jobs(&env), 1);

    // asked again, the finished comparison answers without another job
    let again = env.post("/api/v1/setups/compare", pair).await;
    assert_eq!(again.status, StatusCode::OK);
    assert_eq!(again.json()["rows"], json!(rows));
    assert_eq!(compare_jobs(&env), 1);

    // and the version carries the backend's count: the bar changed; the corner weight is a readout
    let r = env.get(&format!("/api/v1/setups/{second}")).await;
    check(&contract, "GET", "/api/v1/setups/{setup_id}", &r);
    assert_eq!(r.json()["versions"][1]["changed_count"], 1);
    assert_eq!(r.json()["versions"][0]["changed_count"], Value::Null);
    // the older one lists the same versions
    let r = env.get(&format!("/api/v1/setups/{first}")).await;
    assert_eq!(r.json()["setup"]["raced_count"], 2);
    assert_eq!(r.json()["versions"][1]["changed_count"], 1);

    // the detail's usage is of one library, main unless asked
    let r = env
        .get(&format!("/api/v1/setups/{first}?library=lab"))
        .await;
    check(&contract, "GET", "/api/v1/setups/{setup_id}", &r);
    let lab = r.json();
    assert_eq!(lab["setup"]["observation_count"], 0);
    assert_eq!(lab["setup"]["raced"], false);
    assert_eq!(lab["setup"]["raced_count"], 0);
    assert_eq!(
        lab["versions"].as_array().unwrap().len(),
        1,
        "no other version has lab use"
    );
    assert_eq!(
        lab["observations"].as_array().unwrap().len(),
        3,
        "observations are facts of every library"
    );
    let r = env
        .get(&format!("/api/v1/setups/{first}?library=joshua"))
        .await;
    assert_eq!(r.status, StatusCode::BAD_REQUEST);
    check(&contract, "GET", "/api/v1/setups/{setup_id}", &r);
}

#[tokio::test]
async fn a_recording_in_no_session_leaves_the_race_count_unknown() {
    let env = Env::new().await;
    let Some(_) = env.python.clone() else {
        eprintln!("skipped: no Python 3 for the stub worker");
        return;
    };
    ingest(&env, race_meta(9911, 0, "raced")).await;
    env.settle().await;
    let count = || async {
        env.get(&format!("/api/v1/drivers/{CUSTOMER}/setups"))
            .await
            .json()["setups"][0]["raced_count"]
            .clone()
    };
    assert_eq!(count().await, 1);
    env.app
        .store
        .write_blocking(|c| {
            c.execute("DELETE FROM session_recordings", [])?;
            Ok(())
        })
        .unwrap();
    assert_eq!(count().await, Value::Null, "unknown is not 0");
}

#[tokio::test]
async fn the_session_row_carries_green_pace_and_whether_the_trend_is_supported() {
    let env = Env::new().await;
    let Some(_) = env.python.clone() else {
        eprintln!("skipped: no Python 3 for the stub worker");
        return;
    };
    let contract = Contract::load();
    ingest(&env, race_meta(9921, 0, "pace")).await;
    env.settle().await;
    let session = || async {
        let r = env
            .get(&format!("/api/v1/drivers/{CUSTOMER}/sessions"))
            .await;
        check(
            &contract,
            "GET",
            "/api/v1/drivers/{customer_id}/sessions",
            &r,
        );
        r.json()["sessions"][0]["overview"].clone()
    };
    // the stub's analysis has no slope and its investigation keeps no lap: honest nulls, always sent
    let overview = session().await;
    assert_eq!(overview["recorded_laps"], 2);
    assert_eq!(overview["green_pace_median_s"], Value::Null);
    assert_eq!(overview["pace_slope_supported"], Value::Null);

    // a session projected before this rule: its views gain a slope and the sanitized
    // investigation, its stored overview loses the round-2 keys; the start-up refresh fills them
    let analysis: String =
        env.query(|c| c.query_row("SELECT current_analysis_id FROM sessions", [], |r| r.get(0)));
    let views = env.app.cfg.views_dir(&analysis, PROJECTION);
    let mut race: Value =
        serde_json::from_slice(&std::fs::read(views.join("race.json")).unwrap()).unwrap();
    race["race_summary"]["pace_slope_s_per_lap"] = json!(0.0213);
    std::fs::write(views.join("race.json"), race.to_string()).unwrap();
    let fixture = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../test-data/race-investigation.json");
    let investigation: Value = serde_json::from_slice(&std::fs::read(fixture).unwrap()).unwrap();
    let mut view: Value =
        serde_json::from_slice(&std::fs::read(views.join("investigation.json")).unwrap()).unwrap();
    view["race_investigation"] = investigation;
    std::fs::write(views.join("investigation.json"), view.to_string()).unwrap();
    env.app
        .store
        .write_blocking(|c| {
            c.execute(
                "UPDATE sessions SET overview_json = '{\"recorded_laps\": 2}'",
                [],
            )?;
            Ok(())
        })
        .unwrap();
    assert_eq!(
        coach_server::overview::refresh_on_start(&env.app)
            .await
            .unwrap(),
        1
    );
    let overview = session().await;
    assert_eq!(overview["pace_slope_s_per_lap"], 0.0213);
    assert_eq!(overview["green_pace_median_s"], 30.1);
    assert_eq!(overview["pace_slope_supported"], true);
    assert_eq!(
        coach_server::overview::refresh_on_start(&env.app)
            .await
            .unwrap(),
        0,
        "idempotent"
    );
}

#[tokio::test]
async fn setup_filters_keep_several_cars_and_unknown_fields() {
    let env = Env::new().await;
    ingest(&env, race_meta(9931, 0, "driver")).await;
    let contract = Contract::load();
    let filters = json!({"car_path": null, "car_paths": ["stockcars chevyss2020", "other car"], "raced": true,
                         "text": null, "future_filter": {"kept": true}});
    let r = env
        .put(
            &format!("/api/v1/drivers/{CUSTOMER}/prefs"),
            json!({"setup_filters": filters}),
            Some(0),
        )
        .await;
    assert_eq!(
        r.status,
        StatusCode::OK,
        "{}",
        String::from_utf8_lossy(&r.body)
    );
    check(&contract, "PUT", "/api/v1/drivers/{customer_id}/prefs", &r);
    let got = env.get(&format!("/api/v1/drivers/{CUSTOMER}/prefs")).await;
    check(
        &contract,
        "GET",
        "/api/v1/drivers/{customer_id}/prefs",
        &got,
    );
    assert_eq!(got.json()["setup_filters"], filters);
    let version = got.json()["version"].as_i64().unwrap();
    // the page writes null for no car
    let none = json!({"car_path": null, "car_paths": null, "raced": null, "text": null});
    let r = env
        .put(
            &format!("/api/v1/drivers/{CUSTOMER}/prefs"),
            json!({"setup_filters": none}),
            Some(version),
        )
        .await;
    assert_eq!(r.status, StatusCode::OK);
    assert_eq!(r.json()["setup_filters"], none);
    let version = r.json()["version"].as_i64().unwrap();
    for bad in [
        json!({"setup_filters": {"car_paths": "stockcars chevyss2020"}}),
        json!({"setup_filters": {"car_paths": [7]}}),
    ] {
        let r = env
            .put(
                &format!("/api/v1/drivers/{CUSTOMER}/prefs"),
                bad.clone(),
                Some(version),
            )
            .await;
        assert!(
            r.status == StatusCode::BAD_REQUEST || r.status == StatusCode::UNPROCESSABLE_ENTITY,
            "{bad}: {}",
            r.status
        );
    }
}

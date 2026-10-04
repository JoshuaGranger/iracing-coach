//! The Sessions index as the web's filters use it (series, fixed or open setup, repeated car, track,
//! series and type values), the setup library's raced filter, the prefs the pages persist and the
//! built-in trace layout.

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

/// Four sessions: a fixed-setup race, an open-setup race on another car and track, a qualifying
/// session of the first race's event whose recording does not say, and a test session.
async fn library() -> Option<Env> {
    let env = Env::new().await;
    env.python.as_ref()?;
    let mut a = race_meta(9601, 0, "a");
    a["series"] = json!("Series A");
    a["fixed_setup"] = json!(true);
    a["start"] = json!("2026-08-01T12:00:00Z");
    ingest(&env, a).await;
    let mut b = race_meta(9602, 0, "b");
    b["series"] = json!("Series B");
    b["fixed_setup"] = json!(false);
    b["car_path"] = json!("other car");
    b["car_name"] = json!("Other Car");
    b["track"] = json!("Other Track");
    b["start"] = json!("2026-08-02T12:00:00Z");
    ingest(&env, b).await;
    let mut c = race_meta(9601, 1, "c");
    c["series"] = json!("Series A");
    c["session_type"] = json!("Qualify");
    c["start"] = json!("2026-08-03T12:00:00Z");
    ingest(&env, c).await;
    let mut d = race_meta(0, 0, "d");
    d["session_type"] = json!("Offline Testing");
    d["start"] = json!("2026-08-04T12:00:00Z");
    ingest(&env, d).await;
    env.settle().await;
    Some(env)
}

/// Series, fixed flag and session type of the sessions a query returns, newest first.
async fn rows(env: &Env, query: &str) -> Vec<(String, Option<bool>, String)> {
    let r = env
        .get(&format!("/api/v1/drivers/1001/sessions?{query}"))
        .await;
    assert_eq!(
        r.status,
        StatusCode::OK,
        "{query}: {}",
        String::from_utf8_lossy(&r.body)
    );
    r.json()["sessions"]
        .as_array()
        .unwrap()
        .iter()
        .map(|s| {
            (
                s["series_name"].as_str().unwrap_or("").to_string(),
                s["is_fixed_setup"].as_bool(),
                s["session_type"].as_str().unwrap_or("").to_string(),
            )
        })
        .collect()
}

#[tokio::test]
async fn the_index_carries_series_and_whether_the_setup_is_fixed() {
    let Some(env) = library().await else {
        eprintln!("skipped: no Python 3 for the stub worker");
        return;
    };
    let contract = Contract::load();
    let r = env.get("/api/v1/drivers/1001/sessions").await;
    check(
        &contract,
        "GET",
        "/api/v1/drivers/{customer_id}/sessions",
        &r,
    );
    assert_eq!(
        rows(&env, "").await,
        [
            ("Stub Series".into(), None, "Offline Testing".into()),
            ("Series A".into(), None, "Qualify".into()),
            ("Series B".into(), Some(false), "Race".into()),
            ("Series A".into(), Some(true), "Race".into()),
        ],
        "fixed true, open false, and null where the recording does not say"
    );
    // the detail carries the same fields, and the event's other phases carry their analysis state
    let race: i64 = r.json()["sessions"]
        .as_array()
        .unwrap()
        .iter()
        .find(|s| s["series_name"] == "Series A" && s["session_type"] == "Race")
        .unwrap()["session_id"]
        .as_i64()
        .unwrap();
    let r = env.get(&format!("/api/v1/sessions/{race}")).await;
    check(&contract, "GET", "/api/v1/sessions/{session_id}", &r);
    let detail = r.json();
    assert_eq!(detail["session"]["series_name"], "Series A");
    assert_eq!(detail["session"]["is_fixed_setup"], true);
    let phases = detail["event_sessions"].as_array().unwrap();
    assert_eq!(phases.len(), 1);
    assert_eq!(phases[0]["session_type"], "Qualify");
    assert_eq!(phases[0]["analysis_state"], "ready");
    assert_eq!(phases[0]["analysis_reason"], Value::Null);

    // the series filter's options come with the cars and tracks
    let r = env.get("/api/v1/drivers/1001/cars-and-tracks").await;
    check(
        &contract,
        "GET",
        "/api/v1/drivers/{customer_id}/cars-and-tracks",
        &r,
    );
    assert_eq!(
        r.json()["series"],
        json!([
            {"series_name": "Series A"},
            {"series_name": "Series B"},
            {"series_name": "Stub Series"}
        ])
    );
}

#[tokio::test]
async fn repeated_filters_are_alternatives_and_different_filters_all_hold() {
    let Some(env) = library().await else {
        eprintln!("skipped: no Python 3 for the stub worker");
        return;
    };
    let series = |query: &str| {
        let env = &env;
        let query = query.to_string();
        async move {
            rows(env, &query)
                .await
                .into_iter()
                .map(|(series, fixed, kind)| format!("{series}/{fixed:?}/{kind}"))
                .collect::<Vec<_>>()
        }
    };
    // fixed or open
    assert_eq!(series("setup=fixed").await, ["Series A/Some(true)/Race"]);
    assert_eq!(series("setup=open").await, ["Series B/Some(false)/Race"]);
    assert_eq!(
        series("setup=").await.len(),
        4,
        "an empty value is no filter"
    );
    // series, case-insensitive; repeated values are alternatives
    assert_eq!(series("series=Series%20A").await.len(), 2);
    assert_eq!(series("series=series%20a&series=SERIES%20B").await.len(), 3);
    assert!(series("series=Nothing").await.is_empty());
    // cars and tracks, by their option values
    assert_eq!(
        series("car=other%20car").await,
        ["Series B/Some(false)/Race"]
    );
    assert_eq!(
        series("car=other%20car&car=stockcars%20chevyss2020")
            .await
            .len(),
        4
    );
    assert_eq!(series("track=Other%20Track").await.len(), 1);
    assert_eq!(
        series("track=other%20track&track=Stub%20Speedway")
            .await
            .len(),
        4
    );
    // session type: several, with `all` meaning none
    assert_eq!(series("type=qualify").await, ["Series A/None/Qualify"]);
    assert_eq!(series("type=race&type=qualify").await.len(), 3);
    assert_eq!(
        series("type=test").await,
        ["Stub Series/None/Offline Testing"]
    );
    assert_eq!(series("type=all&type=race").await.len(), 4);
    // every filter must hold
    assert_eq!(
        series("type=race&series=Series%20A&setup=fixed&car=stockcars%20chevyss2020").await,
        ["Series A/Some(true)/Race"]
    );
    assert!(series("type=qualify&setup=fixed").await.is_empty());
    // paging keeps the filter
    let first = env
        .get("/api/v1/drivers/1001/sessions?series=Series%20A&limit=1")
        .await
        .json();
    assert_eq!(first["sessions"].as_array().unwrap().len(), 1);
    let cursor = first["next_cursor"].as_str().expect("a second page");
    let second = env
        .get(&format!(
            "/api/v1/drivers/1001/sessions?series=Series%20A&limit=1&cursor={}",
            cursor.replace('|', "%7C")
        ))
        .await
        .json();
    assert_eq!(second["sessions"].as_array().unwrap().len(), 1);
    assert_eq!(second["next_cursor"], Value::Null);
    assert_ne!(
        first["sessions"][0]["session_id"],
        second["sessions"][0]["session_id"]
    );
    // bad values are refused, not ignored
    for bad in ["type=race&type=bogus", "setup=both"] {
        assert_eq!(
            env.get(&format!("/api/v1/drivers/1001/sessions?{bad}"))
                .await
                .status,
            StatusCode::BAD_REQUEST,
            "{bad}"
        );
    }
}

#[tokio::test]
async fn the_setup_library_filters_raced_and_never_raced() {
    let env = Env::new().await;
    let Some(_) = env.python.clone() else {
        eprintln!("skipped: no Python 3 for the stub worker");
        return;
    };
    ingest(&env, race_meta(9701, 0, "raced")).await;
    env.upload(
        b"unread sto",
        "sto",
        "qualy.sto",
        Some("stockcars chevyss2020/qualy.sto"),
        4096,
    )
    .await;
    env.settle().await;
    let list = |query: &str| {
        let env = &env;
        let path = format!("/api/v1/drivers/1001/setups{query}");
        async move { env.get(&path).await }
    };
    let all = list("").await.json();
    assert_eq!(all["setups"].as_array().unwrap().len(), 1);
    assert_eq!(all["unlinked_files"].as_array().unwrap().len(), 1);
    let raced = list("?raced=true").await.json();
    assert_eq!(raced["setups"].as_array().unwrap().len(), 1);
    assert!(
        raced["unlinked_files"].as_array().unwrap().is_empty(),
        "a file with unknown values has never been raced"
    );
    let never = list("?raced=false").await.json();
    assert!(never["setups"].as_array().unwrap().is_empty());
    assert_eq!(never["unlinked_files"].as_array().unwrap().len(), 1);
    // the older flag still works, and `raced` overrides it
    assert_eq!(
        list("?raced_only=true").await.json()["setups"]
            .as_array()
            .unwrap()
            .len(),
        1
    );
    assert!(
        list("?raced_only=true&raced=false").await.json()["setups"]
            .as_array()
            .unwrap()
            .is_empty()
    );
    assert_eq!(list("?raced=maybe").await.status, StatusCode::BAD_REQUEST);
}

#[tokio::test]
async fn prefs_keep_the_text_size_and_the_page_filters() {
    let env = Env::new().await;
    ingest(&env, race_meta(9801, 0, "driver")).await;
    let contract = Contract::load();
    let filters = json!({"cars": ["stockcars chevyss2020"], "tracks": [], "series": ["Series A"],
                         "types": ["race", "qualify"], "setup": "fixed", "text": null,
                         "future_filter": {"kept": true}});
    let r = env
        .put(
            "/api/v1/drivers/1001/prefs",
            json!({"text_size": "larger", "session_filters": filters,
                   "setup_filters": {"car_path": "other car", "raced": false},
                   "future_pref": {"deep": [1, 2]}}),
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
    let got = env.get("/api/v1/drivers/1001/prefs").await;
    check(
        &contract,
        "GET",
        "/api/v1/drivers/{customer_id}/prefs",
        &got,
    );
    let prefs = got.json();
    assert_eq!(prefs["text_size"], "larger");
    assert_eq!(prefs["session_filters"], filters);
    assert_eq!(
        prefs["setup_filters"],
        json!({"car_path": "other car", "raced": false})
    );
    assert_eq!(
        prefs["future_pref"],
        json!({"deep": [1, 2]}),
        "unknown fields round-trip"
    );
    let version = prefs["version"].as_i64().unwrap();

    // a later write changes only what it names; null clears
    let r = env
        .put(
            "/api/v1/drivers/1001/prefs",
            json!({"text_size": "largest", "session_filters": null}),
            Some(version),
        )
        .await;
    assert_eq!(r.status, StatusCode::OK);
    let prefs = r.json();
    assert_eq!(prefs["text_size"], "largest");
    assert_eq!(prefs["session_filters"], Value::Null);
    assert_eq!(prefs["setup_filters"]["car_path"], "other car");
    assert_eq!(prefs["future_pref"], json!({"deep": [1, 2]}));

    // values outside the contract are refused and nothing is stored
    let version = prefs["version"].as_i64().unwrap();
    for bad in [
        json!({"text_size": "huge"}),
        json!({"session_filters": {"types": ["bogus"]}}),
        json!({"session_filters": {"setup": "both"}}),
        json!({"setup_filters": {"raced": "yes"}}),
    ] {
        let r = env
            .put("/api/v1/drivers/1001/prefs", bad.clone(), Some(version))
            .await;
        assert!(
            r.status == StatusCode::BAD_REQUEST || r.status == StatusCode::UNPROCESSABLE_ENTITY,
            "{bad}: {}",
            r.status
        );
    }
    let after = env.get("/api/v1/drivers/1001/prefs").await.json();
    assert_eq!(after["version"], version);
    assert_eq!(after["text_size"], "largest");
}

#[tokio::test]
async fn the_built_in_trace_layout_has_one_row_per_critical_channel() {
    let env = Env::new().await;
    ingest(&env, race_meta(9802, 0, "driver")).await;
    let contract = Contract::load();
    let r = env.get("/api/v1/drivers/1001/trace-layouts").await;
    check(
        &contract,
        "GET",
        "/api/v1/drivers/{customer_id}/trace-layouts",
        &r,
    );
    let layouts = r.json();
    let default = &layouts["layouts"][0];
    assert_eq!(default["layout_id"], "default");
    assert_eq!(default["builtin"], true);
    let rows: Vec<(String, Vec<String>)> = default["body"]["rows"]
        .as_array()
        .unwrap()
        .iter()
        .map(|row| {
            (
                row["row_id"].as_str().unwrap().to_string(),
                row["signals"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|s| s.as_str().unwrap().to_string())
                    .collect(),
            )
        })
        .collect();
    let want = [
        ("critical-speed", "speed"),
        ("critical-delta", "delta"),
        ("critical-throttle", "throttle"),
        ("critical-brake", "brake"),
        ("critical-steering", "steering"),
        ("critical-slip", "slip"),
    ];
    assert_eq!(rows.len(), want.len());
    for ((id, signals), (want_id, want_signal)) in rows.iter().zip(want) {
        assert_eq!(
            (id.as_str(), signals.as_slice()),
            (want_id, &[want_signal.to_string()][..])
        );
    }
}

#[tokio::test]
async fn an_export_only_setup_is_not_compared_with_a_recorded_one() {
    let env = Env::new().await;
    ingest(&env, race_meta(9803, 0, "recorded")).await;
    let recording: String =
        env.query(|c| c.query_row("SELECT sha256 FROM recordings", [], |r| r.get(0)));
    let (recorded, exported) = env
        .app
        .store
        .write_blocking(move |c| {
            let recorded: i64 = c.query_row("SELECT setup_id FROM setups", [], |r| r.get(0))?;
            let values = std::collections::BTreeMap::from([(
                "chassis.left_front.camber".to_string(),
                "-3.2".to_string(),
            )]);
            let exported = coach_server::setups::upsert_setup(
                c,
                1001,
                "stockcars chevyss2020",
                "export-fingerprint",
                &values,
                "2026-08-02T00:00:00Z",
            )?;
            c.execute(
                "INSERT INTO setup_observations(setup_id, customer_id, source, blob_sha256, observed_at)
                 VALUES (?1, 1001, 'htm_export', ?2, '2026-08-02T00:00:00Z')",
                rusqlite::params![exported, recording],
            )?;
            Ok((recorded, exported))
        })
        .unwrap();
    let r = env
        .post(
            "/api/v1/setups/compare",
            json!({"left_setup_id": recorded, "right_setup_id": exported}),
        )
        .await;
    assert_eq!(r.status, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(r.json()["error"], "different_schemes");
    Contract::load().check(
        "POST",
        "/api/v1/setups/compare",
        422,
        "application/json",
        &r.json(),
    );
    let r = env
        .post(
            "/api/v1/setups/compare",
            json!({"left_setup_id": exported, "right_setup_id": recorded}),
        )
        .await;
    assert_eq!(r.json()["error"], "different_schemes", "in either order");
}

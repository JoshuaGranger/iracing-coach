//! HTTP API: every endpoint of `api.openapi.yaml` is called on fixture data and each response is
//! validated against the schema the contract gives for it; plus replay, versions, views and
//! static hosting.

mod support;

use axum::http::{Method, StatusCode};
use coach_server::util::sha256_hex;
use http_body_util::BodyExt;
use serde_json::{Value, json};
use support::*;

async fn ingest(env: &Env, meta: Value) -> String {
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
    sha256_hex(&bytes)
}

fn check(contract: &Contract, method: &str, template: &str, reply: &Reply) {
    let ct = reply.header("content-type").unwrap_or_default();
    let ct = ct.split(';').next().unwrap_or("").to_string();
    if reply.body.is_empty() {
        contract.check(
            method,
            template,
            reply.status.as_u16(),
            "application/json",
            &Value::Null,
        );
    } else if ct == "application/json" {
        contract.check(method, template, reply.status.as_u16(), &ct, &reply.json());
    } else {
        contract.check(
            method,
            template,
            reply.status.as_u16(),
            &ct,
            &Value::String(String::new()),
        );
    }
}

fn scalar<T: Send + 'static + rusqlite::types::FromSql>(env: &Env, sql: &str) -> T {
    let sql = sql.to_string();
    env.query(move |c| c.query_row(&sql, [], |r| r.get(0)))
}

#[tokio::test]
async fn every_endpoint_answers_as_the_contract_says() {
    let env = Env::with(Opts {
        tweak: Box::new(|c| c.inline_wait = std::time::Duration::from_secs(2)),
        ..Default::default()
    })
    .await;
    let Some(_) = env.python.clone() else {
        eprintln!("skipped: no Python 3 for the stub worker");
        return;
    };
    let contract = Contract::load();
    let c = &contract;

    // ---- fixture data: two cars (so a setup comparison can refuse), a .sto, a notes subject
    ingest(&env, race_meta(9500, 0, "main")).await;
    let mut other = race_meta(9501, 0, "other");
    other["car_path"] = json!("other car");
    other["setup"]["values"]["Chassis.Front.ArbSetting"] = json!("9");
    ingest(&env, other).await;
    let mut third = race_meta(9502, 0, "third");
    third["setup"]["values"]["Chassis.Front.ArbSetting"] = json!("6");
    ingest(&env, third).await;
    env.upload(
        b"sto bytes",
        "sto",
        "baseline.sto",
        Some("stockcars chevyss2020/baseline.sto"),
        4096,
    )
    .await;
    env.upload(
        b"unread sto",
        "sto",
        "qualy.sto",
        Some("stockcars chevyss2020/qualy.sto"),
        4096,
    )
    .await;
    env.settle().await;

    // ---- system
    let r = env.get("/health").await;
    assert_eq!(r.status, StatusCode::OK);
    check(c, "GET", "/health", &r);
    let r = env.get("/api/v1/status").await;
    check(c, "GET", "/api/v1/status", &r);
    assert_eq!(r.json()["nas"]["state"], "ok");

    // server-sent events: the first frame after a library change is a valid ServerEvent
    let response = env.call_stream("/api/v1/events?customer_id=1001").await;
    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(
        response.headers()[axum::http::header::CONTENT_TYPE],
        "text/event-stream"
    );
    env.app.events.library_changed(1001, "sessions");
    let mut body = response.into_body();
    let frame = tokio::time::timeout(std::time::Duration::from_secs(5), body.frame())
        .await
        .expect("an event")
        .unwrap()
        .unwrap();
    let text = String::from_utf8(frame.into_data().unwrap().to_vec()).unwrap();
    assert!(text.contains("event: library_changed"), "{text}");
    let data: Value =
        serde_json::from_str(text.lines().find_map(|l| l.strip_prefix("data: ")).unwrap()).unwrap();
    contract.check("GET", "/api/v1/events", 200, "text/event-stream", &data);

    let r = env.get("/api/v1/live/channels").await;
    check(c, "GET", "/api/v1/live/channels", &r);
    assert_eq!(
        r.json()["table_version"],
        coach_wire::live_channels::TABLE_VERSION
    );

    // ---- library
    let r = env.get("/api/v1/drivers").await;
    check(c, "GET", "/api/v1/drivers", &r);
    assert_eq!(r.json()["drivers"][0]["name"], "Joshua Granger");
    assert_eq!(r.json()["drivers"][0]["session_count"], 3);
    let r = env.get("/api/v1/drivers/1001").await;
    check(c, "GET", "/api/v1/drivers/{customer_id}", &r);
    let r = env.get("/api/v1/drivers/55").await;
    assert_eq!(r.status, StatusCode::NOT_FOUND);
    check(c, "GET", "/api/v1/drivers/{customer_id}", &r);

    let r = env.get("/api/v1/drivers/1001/sessions?limit=2").await;
    check(c, "GET", "/api/v1/drivers/{customer_id}/sessions", &r);
    let page = r.json();
    assert_eq!(page["sessions"].as_array().unwrap().len(), 2);
    let cursor = page["next_cursor"]
        .as_str()
        .expect("a next page")
        .to_string();
    let r = env
        .get(&format!(
            "/api/v1/drivers/1001/sessions?limit=2&cursor={}",
            cursor.replace('|', "%7C")
        ))
        .await;
    check(c, "GET", "/api/v1/drivers/{customer_id}/sessions", &r);
    assert_eq!(r.json()["sessions"].as_array().unwrap().len(), 1);
    assert_eq!(r.json()["next_cursor"], Value::Null);
    let filtered = env
        .get("/api/v1/drivers/1001/sessions?type=race&q=other")
        .await
        .json();
    assert_eq!(filtered["sessions"].as_array().unwrap().len(), 1);
    assert_eq!(filtered["sessions"][0]["car_path"], "other car");
    assert_eq!(
        env.get("/api/v1/drivers/1001/sessions?type=practice")
            .await
            .json()["sessions"]
            .as_array()
            .unwrap()
            .len(),
        0
    );
    assert_eq!(
        env.get("/api/v1/drivers/1001/sessions?type=bogus")
            .await
            .status,
        StatusCode::BAD_REQUEST
    );

    let r = env.get("/api/v1/drivers/1001/cars-and-tracks").await;
    check(
        c,
        "GET",
        "/api/v1/drivers/{customer_id}/cars-and-tracks",
        &r,
    );
    assert_eq!(r.json()["cars"].as_array().unwrap().len(), 2);

    let session_id: i64 = scalar(
        &env,
        "SELECT session_id FROM sessions WHERE subsession_id = 9500",
    );
    let r = env.get(&format!("/api/v1/sessions/{session_id}")).await;
    check(c, "GET", "/api/v1/sessions/{session_id}", &r);
    let detail = r.json();
    assert_eq!(detail["session"]["analysis_state"], "ready");
    assert_eq!(detail["recordings"].as_array().unwrap().len(), 1);
    assert_eq!(detail["analyses"][0]["current"], true);
    assert_eq!(detail["setup_ids"].as_array().unwrap().len(), 1);
    let analysis_id = detail["session"]["analysis_id"]
        .as_str()
        .unwrap()
        .to_string();
    let r = env.get("/api/v1/sessions/999999").await;
    check(c, "GET", "/api/v1/sessions/{session_id}", &r);

    let r = env
        .post(
            &format!("/api/v1/sessions/{session_id}/analysis"),
            json!({}),
        )
        .await;
    assert_eq!(r.status, StatusCode::ACCEPTED);
    check(c, "POST", "/api/v1/sessions/{session_id}/analysis", &r);
    assert_eq!(
        r.json()["state"],
        "succeeded",
        "the equivalent finished job is returned"
    );
    let r = env
        .post("/api/v1/sessions/999999/analysis", json!({}))
        .await;
    check(c, "POST", "/api/v1/sessions/{session_id}/analysis", &r);

    // ---- views
    for (suffix, template) in [
        ("race", "/api/v1/analyses/{analysis_id}/race"),
        ("runs", "/api/v1/analyses/{analysis_id}/runs"),
        (
            "investigation",
            "/api/v1/analyses/{analysis_id}/investigation",
        ),
        (
            "traces/catalog",
            "/api/v1/analyses/{analysis_id}/traces/catalog",
        ),
        ("track", "/api/v1/analyses/{analysis_id}/track"),
        ("tuning", "/api/v1/analyses/{analysis_id}/tuning"),
    ] {
        let r = env
            .get(&format!("/api/v1/analyses/{analysis_id}/{suffix}"))
            .await;
        assert_eq!(
            r.status,
            StatusCode::OK,
            "{suffix}: {}",
            String::from_utf8_lossy(&r.body)
        );
        check(c, "GET", template, &r);
        if suffix != "tuning" {
            // the tuning context also carries request-time bindings, so it has no stored-file ETag
            let etag = r.header("etag").unwrap_or_default();
            assert_eq!(etag, format!("\"{analysis_id}-p{PROJECTION}\""), "{suffix}");
            assert_eq!(r.header("cache-control").as_deref(), Some("no-cache"));
            let not_modified = env
                .call(
                    Method::GET,
                    &format!("/api/v1/analyses/{analysis_id}/{suffix}"),
                    None,
                    &[("if-none-match", etag.as_str())],
                )
                .await;
            assert_eq!(not_modified.status, StatusCode::NOT_MODIFIED, "{suffix}");
        }
        let missing = env.get(&format!("/api/v1/analyses/nope/{suffix}")).await;
        assert_eq!(missing.status, StatusCode::NOT_FOUND, "{suffix}");
        check(c, "GET", template, &missing);
    }
    let r = env
        .get(&format!(
            "/api/v1/analyses/{analysis_id}/traces?laps=1-2&channels=speed"
        ))
        .await;
    assert_eq!(r.status, StatusCode::OK);
    assert_eq!(
        r.header("content-type").as_deref(),
        Some("application/vnd.coach.columns")
    );
    assert_eq!(r.header("x-coach-trace-laps").as_deref(), Some("3"));
    assert_eq!(&r.body[..4], b"CCOL");
    let header_len = u32::from_le_bytes(r.body[8..12].try_into().unwrap()) as usize;
    let header: Value = serde_json::from_slice(&r.body[12..12 + header_len]).unwrap();
    assert!(
        coach_server::schema::Schemas::global()
            .check("TraceBlobHeader", &header)
            .unwrap()
            .is_ok()
    );
    contract.check(
        "GET",
        "/api/v1/analyses/{analysis_id}/traces",
        200,
        "application/vnd.coach.columns",
        &Value::String(String::new()),
    );
    let bad = env
        .get(&format!("/api/v1/analyses/{analysis_id}/traces?laps=bad"))
        .await;
    assert_eq!(bad.status, StatusCode::BAD_REQUEST);
    let big = env
        .get(&format!(
            "/api/v1/analyses/{analysis_id}/traces?laps=toolarge"
        ))
        .await;
    assert_eq!(big.status, StatusCode::PAYLOAD_TOO_LARGE);
    check(c, "GET", "/api/v1/analyses/{analysis_id}/traces", &big);
    check(
        c,
        "GET",
        "/api/v1/analyses/{analysis_id}/traces",
        &env.get("/api/v1/analyses/nope/traces").await,
    );
    // projection pending: the view directory is rebuilt, the browser retries on analysis_ready
    env.app
        .store
        .write_blocking(|c| {
            c.execute(
                "UPDATE analyses SET projection_state = 'pending', projection_version = NULL",
                [],
            )?;
            Ok(())
        })
        .unwrap();
    let r = env
        .get(&format!("/api/v1/analyses/{analysis_id}/race"))
        .await;
    assert_eq!(r.status, StatusCode::CONFLICT);
    assert_eq!(r.json()["error"], "projection_pending");
    check(c, "GET", "/api/v1/analyses/{analysis_id}/race", &r);
    check(
        c,
        "GET",
        "/api/v1/analyses/{analysis_id}/traces",
        &env.get(&format!("/api/v1/analyses/{analysis_id}/traces"))
            .await
            .into_conflict(),
    );
    check(
        c,
        "GET",
        "/api/v1/analyses/{analysis_id}/runs",
        &env.get(&format!("/api/v1/analyses/{analysis_id}/runs"))
            .await,
    );
    check(
        c,
        "GET",
        "/api/v1/analyses/{analysis_id}/investigation",
        &env.get(&format!("/api/v1/analyses/{analysis_id}/investigation"))
            .await,
    );
    check(
        c,
        "GET",
        "/api/v1/analyses/{analysis_id}/traces/catalog",
        &env.get(&format!("/api/v1/analyses/{analysis_id}/traces/catalog"))
            .await,
    );
    check(
        c,
        "GET",
        "/api/v1/analyses/{analysis_id}/track",
        &env.get(&format!("/api/v1/analyses/{analysis_id}/track"))
            .await,
    );
    check(
        c,
        "GET",
        "/api/v1/analyses/{analysis_id}/tuning",
        &env.get(&format!("/api/v1/analyses/{analysis_id}/tuning"))
            .await,
    );
    env.settle().await; // the queued projection runs
    assert_eq!(
        env.get(&format!("/api/v1/analyses/{analysis_id}/race"))
            .await
            .status,
        StatusCode::OK
    );

    // ---- setups
    let r = env.get("/api/v1/drivers/1001/setups").await;
    check(c, "GET", "/api/v1/drivers/{customer_id}/setups", &r);
    let library = r.json();
    assert_eq!(library["setups"].as_array().unwrap().len(), 3);
    assert!(
        library["setups"]
            .as_array()
            .unwrap()
            .iter()
            .all(|s| s["raced"] == true),
        "every raced setup is listed"
    );
    assert_eq!(
        library["unlinked_files"][0]["relative_path"], "qualy.sto",
        "a .sto without values is listed"
    );
    assert!(
        library["setups"]
            .as_array()
            .unwrap()
            .iter()
            .any(|s| s["names"]
                .as_array()
                .unwrap()
                .iter()
                .any(|n| n == "baseline.sto")),
        "an unmodified recording links its .sto by name"
    );
    assert_eq!(library["unlinked_files"][0]["linked_setup_id"], Value::Null);
    let by_car: Vec<i64> = library["setups"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|s| s["car_path"] == "stockcars chevyss2020")
        .map(|s| s["setup_id"].as_i64().unwrap())
        .collect();
    let other_id = library["setups"]
        .as_array()
        .unwrap()
        .iter()
        .find(|s| s["car_path"] == "other car")
        .unwrap()["setup_id"]
        .as_i64()
        .unwrap();
    let r = env
        .get("/api/v1/drivers/1001/setups?car_path=other%20car&raced_only=true")
        .await;
    check(c, "GET", "/api/v1/drivers/{customer_id}/setups", &r);
    assert_eq!(r.json()["setups"].as_array().unwrap().len(), 1);
    let r = env.get(&format!("/api/v1/setups/{}", by_car[0])).await;
    check(c, "GET", "/api/v1/setups/{setup_id}", &r);
    // a recording observation names the session's current analysis (Tune this setup, one request)
    let detail = r.json();
    let recorded = detail["observations"]
        .as_array()
        .unwrap()
        .iter()
        .find(|o| o["source"] == "recording")
        .expect("a recording observation");
    let observed_session = recorded["session_id"].as_i64().unwrap();
    let current: String = scalar(
        &env,
        &format!("SELECT current_analysis_id FROM sessions WHERE session_id = {observed_session}"),
    );
    assert_eq!(recorded["analysis_id"], current.as_str());
    assert!(
        r.json()["identity_excluded_keys"]
            .as_array()
            .unwrap()
            .iter()
            .any(|k| k == "UpdateCount")
    );
    check(
        c,
        "GET",
        "/api/v1/setups/{setup_id}",
        &env.get("/api/v1/setups/999999").await,
    );

    // nothing runs jobs yet: the comparison does not finish inside the wait and answers 202
    let r = env
        .post(
            "/api/v1/setups/compare",
            json!({"left_setup_id": by_car[0], "right_setup_id": by_car[1]}),
        )
        .await;
    assert_eq!(r.status, StatusCode::ACCEPTED);
    check(c, "POST", "/api/v1/setups/compare", &r);
    // with the worker running it answers inline
    let (r, _) = tokio::join!(
        env.post(
            "/api/v1/setups/compare",
            json!({"left_setup_id": by_car[0], "right_setup_id": by_car[1]})
        ),
        async {
            tokio::time::sleep(std::time::Duration::from_millis(300)).await;
            coach_server::jobs::drain(&env.app).await.unwrap();
        }
    );
    assert_eq!(
        r.status,
        StatusCode::OK,
        "{}",
        String::from_utf8_lossy(&r.body)
    );
    check(c, "POST", "/api/v1/setups/compare", &r);
    assert!(
        r.json()["rows"]
            .as_array()
            .unwrap()
            .iter()
            .any(|row| row["key"] == "Chassis.Front.ArbSetting" && row["status"] == "changed")
    );
    let r = env
        .post(
            "/api/v1/setups/compare",
            json!({"left_setup_id": by_car[0], "right_setup_id": other_id}),
        )
        .await;
    assert_eq!(r.status, StatusCode::UNPROCESSABLE_ENTITY);
    check(c, "POST", "/api/v1/setups/compare", &r);

    let r = env.post("/api/v1/drivers/1001/baseline", json!({"season": "2026S3", "car": "stock", "track": "stub", "analysis_id": analysis_id})).await;
    assert_eq!(r.status, StatusCode::ACCEPTED);
    check(c, "POST", "/api/v1/drivers/{customer_id}/baseline", &r);

    // ---- tuning
    let context = env
        .get(&format!("/api/v1/analyses/{analysis_id}/tuning"))
        .await
        .json();
    let key = context["draft_identity_key"].as_str().unwrap().to_string();
    assert_eq!(context["binding"]["session_id"], session_id);
    assert_eq!(context["binding"]["event_key"], "subsession:9500");
    assert_eq!(
        context["open_targets"].as_array().unwrap().len(),
        0,
        "an Open setup needs no target"
    );
    let recommend = json!({
        "feedback": [{"corner_id": "t1", "run_phase": "early", "corner_phases": ["entry"], "symptom_id": "push"}],
        "map_identity": {"track_configuration_key": "stub-speedway:oval", "source_type": "user-confirmed", "verified": true,
                         "corners": [{"corner_id": "t1", "label": "Turn 1", "start_pct": 0.1, "apex_pct": 0.15, "end_pct": 0.2,
                                      "is_official": false, "user_verified": true}]},
    });
    let r = env
        .post(
            &format!("/api/v1/analyses/{analysis_id}/tuning/recommendations"),
            recommend.clone(),
        )
        .await;
    assert_eq!(
        r.status,
        StatusCode::ACCEPTED,
        "{}",
        String::from_utf8_lossy(&r.body)
    );
    check(
        c,
        "POST",
        "/api/v1/analyses/{analysis_id}/tuning/recommendations",
        &r,
    );
    let job = env
        .get(&format!("/api/v1/jobs/{}", r.json()["job_id"]))
        .await
        .json();
    let args = &job["subject"]["arguments"];
    assert_eq!(
        args["map_identity"]["source_type"], "verified-manual",
        "aliases are canonicalized"
    );
    assert_eq!(
        args["map_identity"]["geometry_hash"]
            .as_str()
            .unwrap()
            .len(),
        64
    );
    assert_eq!(
        args["map_identity"]["annotation_hash"]
            .as_str()
            .unwrap()
            .len(),
        64
    );
    assert_eq!(args["maximum_changes"], 1);
    let mut bad = recommend.clone();
    bad["feedback"] = json!([]);
    assert_eq!(
        env.post(
            &format!("/api/v1/analyses/{analysis_id}/tuning/recommendations"),
            bad
        )
        .await
        .status,
        StatusCode::BAD_REQUEST
    );

    let r = env
        .post(
            "/api/v1/drivers/1001/tuning/experiments/exp-1/outcome",
            json!({"outcome": "improved", "notes": "better"}),
        )
        .await;
    assert_eq!(r.status, StatusCode::ACCEPTED);
    check(
        c,
        "POST",
        "/api/v1/drivers/{customer_id}/tuning/experiments/{experiment_id}/outcome",
        &r,
    );
    let r = env
        .get(&format!(
            "/api/v1/drivers/1001/tuning/history?analysis_id={analysis_id}"
        ))
        .await;
    assert_eq!(r.status, StatusCode::ACCEPTED);
    check(c, "GET", "/api/v1/drivers/{customer_id}/tuning/history", &r);

    // ---- drafts and annotations (If-Match versions)
    let path = format!("/api/v1/drivers/1001/tuning-drafts/{key}");
    check(
        c,
        "GET",
        "/api/v1/drivers/{customer_id}/tuning-drafts/{identity_key}",
        &env.get(&path).await,
    );
    let created = env
        .put(&path, json!({"body": {"goal": "tire-life"}}), None)
        .await;
    assert_eq!(created.status, StatusCode::OK);
    check(
        c,
        "PUT",
        "/api/v1/drivers/{customer_id}/tuning-drafts/{identity_key}",
        &created,
    );
    assert_eq!(created.json()["version"], 1);
    let r = env.get(&path).await;
    check(
        c,
        "GET",
        "/api/v1/drivers/{customer_id}/tuning-drafts/{identity_key}",
        &r,
    );
    let stale = env
        .put(&path, json!({"body": {"goal": "stability"}}), Some(7))
        .await;
    assert_eq!(stale.status, StatusCode::PRECONDITION_FAILED);
    check(
        c,
        "PUT",
        "/api/v1/drivers/{customer_id}/tuning-drafts/{identity_key}",
        &stale,
    );
    assert_eq!(stale.json()["current"]["body"]["goal"], "tire-life");
    let updated = env
        .put(&path, json!({"body": {"goal": "stability"}}), Some(1))
        .await;
    assert_eq!(updated.json()["version"], 2);

    let path = "/api/v1/drivers/1001/turn-annotations/stub-speedway:oval/map-1";
    check(
        c,
        "GET",
        "/api/v1/drivers/{customer_id}/turn-annotations/{track_config_key}/{map_identity}",
        &env.get(path).await,
    );
    let r = env
        .put(path, json!({"body": {"names": {"t1": "Turn One"}}}), None)
        .await;
    check(
        c,
        "PUT",
        "/api/v1/drivers/{customer_id}/turn-annotations/{track_config_key}/{map_identity}",
        &r,
    );
    check(
        c,
        "GET",
        "/api/v1/drivers/{customer_id}/turn-annotations/{track_config_key}/{map_identity}",
        &env.get(path).await,
    );
    let r = env.put(path, json!({"body": {}}), Some(9)).await;
    assert_eq!(r.status, StatusCode::PRECONDITION_FAILED);
    check(
        c,
        "PUT",
        "/api/v1/drivers/{customer_id}/turn-annotations/{track_config_key}/{map_identity}",
        &r,
    );

    // ---- notes
    let note = json!({"subject_kind": "session", "subject_id": session_id.to_string(), "body": "good start"});
    let r = env.post("/api/v1/drivers/1001/notes", note.clone()).await;
    assert_eq!(r.status, StatusCode::CREATED);
    check(c, "POST", "/api/v1/drivers/{customer_id}/notes", &r);
    let note_id = r.json()["note_id"].as_i64().unwrap();
    let r = env
        .get(&format!(
            "/api/v1/drivers/1001/notes?subject_kind=session&subject_id={session_id}"
        ))
        .await;
    check(c, "GET", "/api/v1/drivers/{customer_id}/notes", &r);
    assert_eq!(r.json()["notes"].as_array().unwrap().len(), 1);
    assert_eq!(
        env.get("/api/v1/drivers/1001/notes").await.status,
        StatusCode::BAD_REQUEST
    );
    let r = env
        .put(
            &format!("/api/v1/notes/{note_id}"),
            json!({"body": "better start"}),
            Some(1),
        )
        .await;
    check(c, "PUT", "/api/v1/notes/{note_id}", &r);
    assert_eq!(r.json()["version"], 2);
    let r = env
        .put(
            &format!("/api/v1/notes/{note_id}"),
            json!({"body": "lost"}),
            Some(1),
        )
        .await;
    assert_eq!(r.status, StatusCode::PRECONDITION_FAILED);
    check(c, "PUT", "/api/v1/notes/{note_id}", &r);
    let r = env
        .delete(&format!("/api/v1/notes/{note_id}"), Some(1))
        .await;
    assert_eq!(r.status, StatusCode::PRECONDITION_FAILED);
    check(c, "DELETE", "/api/v1/notes/{note_id}", &r);
    let r = env
        .delete(&format!("/api/v1/notes/{note_id}"), Some(2))
        .await;
    assert_eq!(r.status, StatusCode::NO_CONTENT);
    check(c, "DELETE", "/api/v1/notes/{note_id}", &r);

    // ---- layouts and prefs
    let r = env.get("/api/v1/drivers/1001/trace-layouts").await;
    check(c, "GET", "/api/v1/drivers/{customer_id}/trace-layouts", &r);
    assert_eq!(r.json()["layouts"][0]["builtin"], true);
    let layout =
        json!({"name": "Mine", "body": {"rows": [{"row_id": "r1", "signals": ["speed"]}]}});
    let r = env
        .put(
            "/api/v1/drivers/1001/trace-layouts/mine",
            layout.clone(),
            None,
        )
        .await;
    check(
        c,
        "PUT",
        "/api/v1/drivers/{customer_id}/trace-layouts/{layout_id}",
        &r,
    );
    let r = env
        .put(
            "/api/v1/drivers/1001/trace-layouts/mine",
            layout.clone(),
            Some(5),
        )
        .await;
    assert_eq!(r.status, StatusCode::PRECONDITION_FAILED);
    check(
        c,
        "PUT",
        "/api/v1/drivers/{customer_id}/trace-layouts/{layout_id}",
        &r,
    );
    assert_eq!(
        env.put(
            "/api/v1/drivers/1001/trace-layouts/default",
            layout.clone(),
            None
        )
        .await
        .status,
        StatusCode::UNPROCESSABLE_ENTITY
    );
    let r = env
        .delete("/api/v1/drivers/1001/trace-layouts/mine", Some(9))
        .await;
    check(
        c,
        "DELETE",
        "/api/v1/drivers/{customer_id}/trace-layouts/{layout_id}",
        &r,
    );
    let r = env
        .delete("/api/v1/drivers/1001/trace-layouts/mine", Some(1))
        .await;
    assert_eq!(r.status, StatusCode::NO_CONTENT);
    check(
        c,
        "DELETE",
        "/api/v1/drivers/{customer_id}/trace-layouts/{layout_id}",
        &r,
    );

    let r = env.get("/api/v1/drivers/1001/live-layouts").await;
    check(c, "GET", "/api/v1/drivers/{customer_id}/live-layouts", &r);
    assert_eq!(r.json()["active_layout_id"], "factory-default");
    let live = json!({"name": "Race", "body": {"rows": 2, "columns": 3, "tiles": [
        {"tile_id": "speed", "metric": "speed", "row": 0, "column": 0, "row_span": 1, "column_span": 1, "style": "number"}]}});
    let r = env
        .put("/api/v1/drivers/1001/live-layouts/race", live.clone(), None)
        .await;
    check(
        c,
        "PUT",
        "/api/v1/drivers/{customer_id}/live-layouts/{layout_id}",
        &r,
    );
    let r = env
        .put(
            "/api/v1/drivers/1001/live-layouts/race",
            live.clone(),
            Some(3),
        )
        .await;
    check(
        c,
        "PUT",
        "/api/v1/drivers/{customer_id}/live-layouts/{layout_id}",
        &r,
    );
    let r = env
        .delete("/api/v1/drivers/1001/live-layouts/race", Some(3))
        .await;
    check(
        c,
        "DELETE",
        "/api/v1/drivers/{customer_id}/live-layouts/{layout_id}",
        &r,
    );
    let r = env
        .delete("/api/v1/drivers/1001/live-layouts/race", Some(1))
        .await;
    check(
        c,
        "DELETE",
        "/api/v1/drivers/{customer_id}/live-layouts/{layout_id}",
        &r,
    );
    assert_eq!(r.status, StatusCode::NO_CONTENT);

    let r = env.get("/api/v1/drivers/1001/prefs").await;
    check(c, "GET", "/api/v1/drivers/{customer_id}/prefs", &r);
    assert_eq!(r.json()["version"], 0);
    let r = env.put("/api/v1/drivers/1001/prefs", json!({"safe_glance": false, "last_investigation": {"session_id": session_id, "analysis_id": analysis_id, "view": "traces"}}), Some(0)).await;
    assert_eq!(
        r.status,
        StatusCode::OK,
        "{}",
        String::from_utf8_lossy(&r.body)
    );
    check(c, "PUT", "/api/v1/drivers/{customer_id}/prefs", &r);
    let r = env
        .put(
            "/api/v1/drivers/1001/prefs",
            json!({"theme_color": "mint"}),
            Some(0),
        )
        .await;
    assert_eq!(r.status, StatusCode::PRECONDITION_FAILED);
    check(c, "PUT", "/api/v1/drivers/{customer_id}/prefs", &r);
    let r = env.get("/api/v1/drivers/1001/prefs").await;
    assert_eq!(r.json()["safe_glance"], false);
    assert_eq!(r.json()["last_investigation"]["view"], "traces");

    // ---- jobs
    let r = env.get("/api/v1/jobs?limit=5").await;
    check(c, "GET", "/api/v1/jobs", &r);
    assert!(r.json()["jobs"].as_array().unwrap().len() <= 5);
    let r = env.get("/api/v1/jobs?state=queued").await;
    check(c, "GET", "/api/v1/jobs", &r);
    let queued_id = r.json()["jobs"][0]["job_id"]
        .as_i64()
        .expect("a queued job (the baseline)");
    let r = env.get(&format!("/api/v1/jobs/{queued_id}")).await;
    check(c, "GET", "/api/v1/jobs/{job_id}", &r);
    check(
        c,
        "GET",
        "/api/v1/jobs/{job_id}",
        &env.get("/api/v1/jobs/999999").await,
    );
    let r = env
        .post(&format!("/api/v1/jobs/{queued_id}/cancel"), json!({}))
        .await;
    assert_eq!(r.status, StatusCode::ACCEPTED);
    check(c, "POST", "/api/v1/jobs/{job_id}/cancel", &r);
    let finished: i64 = scalar(
        &env,
        "SELECT job_id FROM jobs WHERE state = 'succeeded' LIMIT 1",
    );
    let r = env
        .post(&format!("/api/v1/jobs/{finished}/cancel"), json!({}))
        .await;
    assert_eq!(r.status, StatusCode::CONFLICT);
    check(c, "POST", "/api/v1/jobs/{job_id}/cancel", &r);

    // ---- uploads
    let bytes = fake_ibt(race_meta(9600, 0, "upload"), 9_000);
    let sha = sha256_hex(&bytes);
    let r = env
        .create_upload(&sha, bytes.len(), "ibt", "u.ibt", None, None)
        .await;
    assert_eq!(r.status, StatusCode::CREATED);
    check(c, "POST", "/api/v1/uploads", &r);
    let r = env.put_chunk(&sha, bytes.len(), 0, &bytes[..4096]).await;
    check(c, "PUT", "/api/v1/uploads/{sha256}", &r);
    let r = env
        .put_chunk(&sha, bytes.len(), 6000, &bytes[6000..7000])
        .await;
    assert_eq!(r.status, StatusCode::CONFLICT);
    check(c, "PUT", "/api/v1/uploads/{sha256}", &r);
    let head = env
        .call(Method::HEAD, &format!("/api/v1/uploads/{sha}"), None, &[])
        .await;
    check(c, "HEAD", "/api/v1/uploads/{sha256}", &head);
    let unknown = env
        .call(
            Method::HEAD,
            &format!("/api/v1/uploads/{}", sha256_hex(b"?")),
            None,
            &[],
        )
        .await;
    check(c, "HEAD", "/api/v1/uploads/{sha256}", &unknown);
    let mut tampered = bytes.clone();
    tampered[8000] ^= 1;
    let r = env
        .put_chunk(&sha, bytes.len(), 4096, &tampered[4096..])
        .await;
    assert_eq!(r.status, StatusCode::UNPROCESSABLE_ENTITY);
    check(c, "PUT", "/api/v1/uploads/{sha256}", &r);
    let r = env.upload(&bytes, "ibt", "u.ibt", None, 4096).await;
    assert_eq!(r.status, StatusCode::OK);
    let r = env
        .create_upload(&sha, bytes.len(), "ibt", "u.ibt", None, None)
        .await;
    assert_eq!(r.status, StatusCode::OK);
    check(c, "POST", "/api/v1/uploads", &r);

    // ---- admin
    let r = env.post("/api/v1/admin/reanalyze", json!({})).await;
    assert_eq!(r.status, StatusCode::ACCEPTED);
    check(c, "POST", "/api/v1/admin/reanalyze", &r);
    let r = env.post("/api/v1/admin/snapshot", json!({})).await;
    assert_eq!(r.status, StatusCode::ACCEPTED);
    check(c, "POST", "/api/v1/admin/snapshot", &r);
    let bundle = json!({"bundle_version": 1, "customer_id": 1001, "source": {"machine": "DESKTOP-SIM", "root": "C:/legacy"},
        "notes": [{"legacy_id": "n1", "subject_kind": "session", "subject_id": "ignored", "body": "legacy note",
                   "subject_ref": {"subsession_id": 9500, "sim_session_num": 0, "car_path": "stockcars chevyss2020"}},
                  {"legacy_id": "n2", "subject_kind": "session", "subject_id": "x", "body": "unknown event",
                   "subject_ref": {"subsession_id": 123456}}],
        "trace_layouts": [{"name": "Legacy rows", "body": {"rows": [{"row_id": "r", "signals": ["speed"]}]}}],
        "legacy_analyses": [{"legacy_analysis_id": "old-a", "subsession_id": 9500, "sim_session_num": 0},
                            {"legacy_analysis_id": "old-b", "subsession_id": 123456}],
        "legacy_archive": {"files": [legacy_file("history.sqlite3", b"not read by the stub"),
                                     legacy_file("tuning/experiments/s/c/t/old-1.json",
                                                 json!({"experiment_id": "old-1", "analysis_id": "old-a"}).to_string().as_bytes())]}});
    let r = env.post("/api/v1/admin/import", bundle.clone()).await;
    assert_eq!(r.status, StatusCode::ACCEPTED);
    check(c, "POST", "/api/v1/admin/import", &r);
    env.settle().await;
    let r = env.post("/api/v1/admin/import", bundle.clone()).await;
    assert_eq!(r.status, StatusCode::OK, "the same bundle imports once");
    check(c, "POST", "/api/v1/admin/import", &r);
    assert_eq!(
        scalar::<i64>(
            &env,
            "SELECT COUNT(*) FROM notes WHERE legacy_source = 'import:n1'"
        ),
        1
    );
    assert_eq!(
        scalar::<i64>(
            &env,
            "SELECT COUNT(*) FROM legacy_items WHERE kind = 'note'"
        ),
        1,
        "the unresolved note is kept"
    );
    assert_eq!(
        scalar::<i64>(
            &env,
            "SELECT COUNT(*) FROM legacy_items WHERE kind = 'tuning_experiment'"
        ),
        0,
        "tuning experiments go through the worker, not into legacy_items"
    );
    let summary: Value =
        serde_json::from_str(&scalar::<String>(&env, "SELECT summary_json FROM imports")).unwrap();
    assert_eq!(summary["tuning_experiments_imported"], 1);
    assert_eq!(summary["tuning_experiments_resolved"], 1);
    assert_eq!(summary["legacy_analyses_resolved"], 1);
    assert_eq!(summary["legacy_analyses_unresolved"], 1);
    assert_eq!(scalar::<i64>(&env, "SELECT COUNT(*) FROM trace_layouts"), 1);
    assert_eq!(scalar::<i64>(&env, "SELECT COUNT(*) FROM imports"), 1);

    // ---- coverage: every operation of the contract (WebSockets aside) was exercised
    let seen = contract.seen.lock().unwrap().clone();
    let missing: Vec<String> = contract
        .operations()
        .into_iter()
        .filter(|op| !seen.contains(op))
        .collect();
    assert!(missing.is_empty(), "operations not exercised: {missing:?}");
}

trait IntoConflict {
    fn into_conflict(self) -> Reply;
}

impl IntoConflict for Reply {
    fn into_conflict(self) -> Reply {
        assert_eq!(
            self.status,
            StatusCode::CONFLICT,
            "{}",
            String::from_utf8_lossy(&self.body)
        );
        self
    }
}

#[tokio::test]
async fn idempotency_key_replays_the_stored_response() {
    let env = Env::new().await;
    ingest(&env, race_meta(9700, 0, "idem")).await;
    let body = json!({"subject_kind": "session", "subject_id": "1", "body": "once"}).to_string();
    let send = |key: &'static str, path: &'static str| {
        let body = body.clone();
        let env = &env;
        async move {
            env.call(
                Method::POST,
                path,
                Some(axum::body::Bytes::from(body)),
                &[
                    ("content-type", "application/json"),
                    ("idempotency-key", key),
                ],
            )
            .await
        }
    };
    let first = send("key-1", "/api/v1/drivers/1001/notes").await;
    assert_eq!(first.status, StatusCode::CREATED);
    let second = send("key-1", "/api/v1/drivers/1001/notes").await;
    assert_eq!(second.status, StatusCode::CREATED);
    assert_eq!(
        second.header("idempotency-replayed").as_deref(),
        Some("true")
    );
    assert_eq!(
        first.json(),
        second.json(),
        "the stored response is returned"
    );
    assert_eq!(
        scalar::<i64>(&env, "SELECT COUNT(*) FROM notes"),
        1,
        "one note for two sends"
    );
    // a different key creates another note
    assert_eq!(
        send("key-2", "/api/v1/drivers/1001/notes").await.status,
        StatusCode::CREATED
    );
    assert_eq!(scalar::<i64>(&env, "SELECT COUNT(*) FROM notes"), 2);
    // the same key on another path is refused
    let reused = send("key-1", "/api/v1/drivers/1002/notes").await;
    assert_eq!(reused.status, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(reused.json()["error"], "idempotency_key_reused");
    // concurrent sends of one key create one note
    let (a, b) = tokio::join!(
        send("key-3", "/api/v1/drivers/1001/notes"),
        send("key-3", "/api/v1/drivers/1001/notes")
    );
    assert_eq!(a.json(), b.json());
    assert_eq!(scalar::<i64>(&env, "SELECT COUNT(*) FROM notes"), 3);
    // a replayed delete does nothing the second time
    let note_id = first.json()["note_id"].as_i64().unwrap();
    let del = |key: &'static str| {
        let env = &env;
        async move {
            env.call(
                Method::DELETE,
                &format!("/api/v1/notes/{note_id}"),
                None,
                &[("if-match", "1"), ("idempotency-key", key)],
            )
            .await
        }
    };
    assert_eq!(del("del-1").await.status, StatusCode::NO_CONTENT);
    assert_eq!(del("del-1").await.status, StatusCode::NO_CONTENT);
    assert_eq!(scalar::<i64>(&env, "SELECT COUNT(*) FROM notes"), 2);
}

#[tokio::test]
async fn static_files_and_spa_fallback() {
    let web = tempfile::tempdir().unwrap();
    std::fs::create_dir_all(web.path().join("assets")).unwrap();
    std::fs::write(
        web.path().join("index.html"),
        "<!doctype html><title>coach</title>",
    )
    .unwrap();
    std::fs::write(web.path().join("assets/app.js"), "console.log(1)").unwrap();
    let path = web.path().to_path_buf();
    let env = Env::with(Opts {
        tweak: Box::new(move |c| c.web_dir = Some(path.clone())),
        ..Default::default()
    })
    .await;
    let index = env.get("/").await;
    assert_eq!(index.status, StatusCode::OK);
    assert!(String::from_utf8_lossy(&index.body).contains("<title>coach</title>"));
    assert_eq!(index.header("cache-control").as_deref(), Some("no-cache"));
    assert_eq!(env.get("/assets/app.js").await.status, StatusCode::OK);
    // client-side routes fall back to the SPA shell
    let route = env.get("/drivers/1001/sessions").await;
    assert!(String::from_utf8_lossy(&route.body).contains("<title>coach</title>"));
    // the API never falls through to the shell
    let missing = env.get("/api/v1/nonsense").await;
    assert_eq!(missing.status, StatusCode::NOT_FOUND);
    assert_eq!(missing.json()["error"], "not_found");
}

#[tokio::test]
async fn status_reports_queue_spool_and_versions() {
    let env = Env::new().await;
    let bytes = fake_ibt(race_meta(9800, 0, "status"), 5_000);
    env.upload(&bytes, "ibt", "s.ibt", None, 5000).await;
    let status = env.get("/api/v1/status").await.json();
    assert_eq!(status["api_version"], 1);
    assert_eq!(status["collector_protocol"], 1);
    assert_eq!(
        status["live_table_version"],
        coach_wire::live_channels::TABLE_VERSION
    );
    assert_eq!(status["projection_version"], PROJECTION);
    assert_eq!(status["spool"]["files"], 1);
    assert_eq!(status["spool"]["bytes"], 5_000);
    assert_eq!(status["nas"]["pending_archive"], 1);
    assert!(status["disk_free_bytes"].as_i64().unwrap() > 0);
}

#[tokio::test]
async fn a_fixed_setup_analysis_lists_compatible_open_targets_and_requires_one() {
    let env = Env::new().await;
    if env.python.is_none() {
        eprintln!("skipped: no Python 3 for the stub worker");
        return;
    }
    ingest(&env, race_meta(9701, 0, "fixed")).await;
    ingest(&env, race_meta(9702, 0, "open")).await;
    ingest(&env, race_meta(9703, 0, "other-track")).await;
    env.settle().await;
    let analysis_of = |subsession: i64| -> (String, i64) {
        env.query(move |c| {
            c.query_row(
                "SELECT current_analysis_id, session_id FROM sessions WHERE subsession_id = ?1",
                [subsession],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
        })
    };
    let (fixed, fixed_session) = analysis_of(9701);
    let (open, open_session) = analysis_of(9702);
    let (elsewhere, _) = analysis_of(9703);
    let rewrite = |analysis: &str, setup_type: &str, track_key: &str| {
        let path = env
            .data()
            .join("views")
            .join(analysis)
            .join(format!("p{PROJECTION}"))
            .join("tuning.json");
        let mut value = tuning_json(analysis, setup_type, "fp-1");
        value["identity"]["track_configuration_key"] = json!(track_key);
        std::fs::write(path, value.to_string()).unwrap();
    };
    rewrite(&fixed, "Fixed", "stub-speedway:oval");
    rewrite(&open, "Open", "stub-speedway:oval");
    rewrite(&elsewhere, "Open", "another-track:road");

    let context = env
        .get(&format!("/api/v1/analyses/{fixed}/tuning"))
        .await
        .json();
    let targets = context["open_targets"].as_array().unwrap();
    assert_eq!(
        targets.len(),
        1,
        "only the Open analysis of the same track configuration: {targets:?}"
    );
    assert_eq!(targets[0]["analysis_id"], open.as_str());
    assert_eq!(targets[0]["binding"]["session_id"], open_session);
    assert_eq!(targets[0]["binding"]["event_key"], "subsession:9702");

    // the draft key is the SHA-256 of the contract's joined identity values
    let identity = &context["identity"];
    let binding = &context["binding"];
    assert_eq!(binding["session_id"], fixed_session);
    let joined = [
        fixed.to_lowercase(),
        "3".to_string(),
        "8001".to_string(),
        "race".to_string(),
        "stub-speedway:oval".to_string(),
        "9002".to_string(),
        "stockcars chevyss2020".to_string(),
        "fp-1".to_string(),
        "a".repeat(64),
        fixed_session.to_string(),
        "subsession:9701".to_string(),
        "subsession:9701:0".to_string(),
    ]
    .join("|");
    assert_eq!(identity["setup_type"], "Fixed");
    assert_eq!(context["draft_identity_key"], sha256_hex(joined.as_bytes()));

    // the Open analysis needs no target; a Fixed one must name one
    assert_eq!(
        env.get(format!("/api/v1/analyses/{open}/tuning").as_str())
            .await
            .json()["open_targets"]
            .as_array()
            .unwrap()
            .len(),
        0
    );
    let request = |target: Option<&str>| {
        json!({
            "open_target_analysis_id": target,
            "feedback": [{"corner_id": "t1", "run_phase": "late", "corner_phases": ["exit"], "symptom_id": "loose"}],
            "map_identity": {"track_configuration_key": "stub-speedway:oval", "source_type": "telemetry-derived", "verified": false,
                             "corners": [{"corner_id": "t1", "label": "T1", "start_pct": 0.1, "apex_pct": 0.15, "end_pct": 0.2,
                                          "is_official": false, "user_verified": false}]},
        })
    };
    let missing = env
        .post(
            &format!("/api/v1/analyses/{fixed}/tuning/recommendations"),
            request(None),
        )
        .await;
    assert_eq!(missing.status, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(missing.json()["error"], "open_target_required");
    let accepted = env
        .post(
            &format!("/api/v1/analyses/{fixed}/tuning/recommendations"),
            request(Some(&open)),
        )
        .await;
    assert_eq!(accepted.status, StatusCode::ACCEPTED);
    let job = env
        .get(&format!("/api/v1/jobs/{}", accepted.json()["job_id"]))
        .await
        .json();
    let open_path: String = env.query({
        let open = open.clone();
        move |c| {
            c.query_row(
                "SELECT analysis_path FROM analyses WHERE analysis_id = ?1",
                [open],
                |r| r.get(0),
            )
        }
    });
    assert_eq!(
        job["subject"]["arguments"]["open_target_analysis_path"],
        open_path.as_str()
    );
    // an unknown target is refused
    let unknown = env
        .post(
            &format!("/api/v1/analyses/{fixed}/tuning/recommendations"),
            request(Some("nope")),
        )
        .await;
    assert_eq!(unknown.status, StatusCode::NOT_FOUND);
}

#[tokio::test]
async fn a_recording_names_its_setup_when_the_server_holds_no_such_file() {
    let env = Env::new().await;
    // an unmodified load names the file whose values it has, held or not
    ingest(&env, race_meta(9810, 0, "named")).await;
    // a modified load names a file its values no longer match
    let mut edited = race_meta(9811, 0, "edited");
    edited["car_path"] = json!("other car");
    edited["setup"]["name"] = json!("Edited.sto");
    edited["setup"]["is_modified"] = json!(true);
    ingest(&env, edited).await;
    // a load with no modified flag proves nothing
    let mut unknown = race_meta(9812, 0, "unknown");
    unknown["car_path"] = json!("third car");
    unknown["setup"]["name"] = json!("Unknown.sto");
    unknown["setup"]["is_modified"] = Value::Null;
    ingest(&env, unknown).await;

    let library = env.get("/api/v1/drivers/1001/setups").await.json();
    let names_of = |car: &str| -> Value {
        library["setups"]
            .as_array()
            .unwrap()
            .iter()
            .find(|s| s["car_path"] == car)
            .unwrap_or_else(|| panic!("a setup for {car}"))["names"]
            .clone()
    };
    assert_eq!(names_of("stockcars chevyss2020"), json!(["baseline.sto"]));
    assert_eq!(names_of("other car"), json!([]));
    assert_eq!(names_of("third car"), json!([]));
    let named = library["setups"]
        .as_array()
        .unwrap()
        .iter()
        .find(|s| s["car_path"] == "stockcars chevyss2020")
        .unwrap();
    assert_eq!(named["sto_files"], json!([]), "no file is claimed");
    let found = env
        .get("/api/v1/drivers/1001/setups?q=BASELINE")
        .await
        .json();
    assert_eq!(
        found["setups"].as_array().unwrap().len(),
        1,
        "search finds the name"
    );

    // the held file of the same name (any letter case) adds no second entry
    env.upload(
        b"sto bytes",
        "sto",
        "Baseline.sto",
        Some("stockcars chevyss2020/Baseline.sto"),
        4096,
    )
    .await;
    let library = env.get("/api/v1/drivers/1001/setups").await.json();
    let named = library["setups"]
        .as_array()
        .unwrap()
        .iter()
        .find(|s| s["car_path"] == "stockcars chevyss2020")
        .unwrap();
    assert_eq!(named["names"].as_array().unwrap().len(), 1, "{named}");
    assert_eq!(named["sto_files"].as_array().unwrap().len(), 1);
}

#[tokio::test]
async fn queueing_the_analysis_of_a_session_without_recordings_is_refused() {
    let env = Env::new().await;
    ingest(&env, race_meta(9820, 0, "driver row")).await;
    env.app
        .store
        .write_blocking(|c| {
            c.execute(
                "INSERT INTO sessions(customer_id, subsession_id, sim_session_num, group_key, session_type, track,
                   car_path, start_time_utc, discovered_at)
                 VALUES (1001, 9821, 0, 'subsession:9821:0', 'Race', 'Stub Speedway', 'stockcars chevyss2020',
                         '2026-08-01T12:00:00Z', '2026-08-01T13:00:00Z')",
                [],
            )?;
            Ok(())
        })
        .unwrap();
    let session_id: i64 = scalar(
        &env,
        "SELECT session_id FROM sessions WHERE subsession_id = 9821",
    );
    let r = env
        .post(
            &format!("/api/v1/sessions/{session_id}/analysis"),
            json!({}),
        )
        .await;
    assert_eq!(r.status, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(r.json()["error"], "no_recordings");
    assert_eq!(
        scalar::<i64>(&env, "SELECT COUNT(*) FROM jobs WHERE kind = 'analyze'"),
        0,
        "nothing was queued"
    );
}

//! Durable field segments: upgrade preservation, late association and NAS recovery.
mod support;

use axum::http::StatusCode;
use coach_server::{db, field, jobs, rebuild};
use rusqlite::Connection;
use serde_json::{Value, json};
use support::*;

fn segment(subsession: i64, phase: i64) -> Vec<u8> {
    let info = format!(
        "WeekendInfo:\n SubSessionID: {subsession}\nDriverInfo:\n DriverCarIdx: 0\n Drivers:\n - CarIdx: 0\n   UserID: 1001\n   UserName: Fixture Driver\n"
    );
    let row = |time: f64, tick: u64, info: Option<&str>| {
        let mut row = json!({"schema_version":1,"captured_unix_ms":1_791_234_567_000u64,"tick":tick,"session_info_update":1,
            "channels":{"SessionNum":phase,"SessionTime":time,"SessionState":4,"CarIdxLap":[1,1],"CarIdxLapDistPct":[0.1,null],"CarIdxOnPitRoad":[false,true]},"future_optional":{"kept":true}});
        if let Some(info) = info {
            row["session_info"] = json!(info);
        }
        row.to_string() + "\n"
    };
    (row(12., 1, Some(&info)) + &row(13., 2, None)).into_bytes()
}

#[test]
fn field_upgrade_preserves_existing_blobs_references_and_open_uploads() {
    // Exercise both the original pre-field upgrade and the deployed STO-version schema
    // immediately before the NAS migration rebuilds these referenced tables again.
    for previous in [3, 5] {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("coach.sqlite3");
        let mut conn = Connection::open(&path).unwrap();
        conn.pragma_update(None, "foreign_keys", true).unwrap();
        db::migrate_with(&mut conn, &path, "old", &db::MIGRATIONS[..previous]).unwrap();
        db::upsert_driver(&conn, 1001, Some("Fixture"), None).unwrap();
        let sha = "a".repeat(64);
        conn.execute("INSERT INTO blobs(sha256,size,kind,first_name,received_at) VALUES(?1,100,'ibt','old.ibt','2026-10-01T00:00:00Z')", [&sha]).unwrap();
        conn.execute(
            "INSERT INTO recordings(sha256,customer_id,ingest_state) VALUES(?1,1001,'parsed')",
            [&sha],
        )
        .unwrap();
        conn.execute("INSERT INTO sessions(session_id,customer_id,group_key,discovered_at) VALUES(1,1001,'old','2026-10-01T00:00:00Z')", []).unwrap();
        conn.execute("INSERT INTO session_recordings VALUES(1,?1)", [&sha])
            .unwrap();
        conn.execute("INSERT INTO blob_occurrences(sha256,kind,file_name,customer_id,seen_at) VALUES(?1,'ibt','old.ibt',1001,'2026-10-01T00:00:00Z')", [&sha]).unwrap();
        conn.execute("INSERT INTO uploads(sha256,size,kind,file_name,durable_offset,state,created_at,updated_at) VALUES(?1,100,'ibt','open.ibt',42,'open','a','b')", ["b".repeat(64)]).unwrap();
        db::migrate(&mut conn, &path, "new").unwrap();
        assert!(
            dir.path()
                .join(format!("pre-migrate-v{previous}.sqlite3"))
                .exists()
        );
        let saved: (i64, String) = conn.query_row("SELECT b.size,r.ingest_state FROM blobs b JOIN recordings r ON r.sha256=b.sha256 JOIN session_recordings sr ON sr.recording_sha256=b.sha256 WHERE sr.session_id=1", [], |r| Ok((r.get(0)?,r.get(1)?))).unwrap();
        assert_eq!(saved, (100, "parsed".into()));
        assert_eq!(
            conn.query_row("SELECT durable_offset FROM uploads", [], |r| r
                .get::<_, i64>(0))
                .unwrap(),
            42
        );
        assert_eq!(
            conn.query_row("SELECT COUNT(*) FROM blob_occurrences", [], |r| r
                .get::<_, i64>(0))
                .unwrap(),
            1
        );
        assert_eq!(
            conn.query_row("SELECT COUNT(*) FROM pragma_foreign_key_check", [], |r| r
                .get::<_, i64>(
                0
            ))
            .unwrap(),
            0
        );
        assert_eq!(
            conn.query_row("PRAGMA foreign_keys", [], |r| r.get::<_, i64>(0))
                .unwrap(),
            1
        );
        conn.execute("INSERT INTO blobs(sha256,size,kind,first_name,received_at) VALUES(?1,20,'field','fresh.field','now')", ["c".repeat(64)]).unwrap();
        conn.execute("INSERT INTO blobs(sha256,size,kind,first_name,received_at) VALUES(?1,20,'rpy','fresh.rpy','now')", ["d".repeat(64)]).unwrap();
    }
}

#[test]
fn field_inspection_requires_complete_rows_and_stable_session_identity() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("sample.field");
    let bytes = segment(9801, 0);
    std::fs::write(&path, &bytes).unwrap();
    let m = field::inspect(&path).unwrap();
    assert_eq!(
        (m.customer, m.subsession, m.phase, m.count, m.first, m.last),
        (1001, Some(9801), 0, 2, 12., 13.)
    );
    std::fs::write(&path, &bytes[..bytes.len() - 1]).unwrap();
    assert!(
        field::inspect(&path).is_err(),
        "a truncated final row is not durable evidence"
    );
    let mixed = [bytes, segment(9802, 0)].concat();
    std::fs::write(&path, mixed).unwrap();
    assert!(
        field::inspect(&path).is_err(),
        "cross-session evidence must not attach"
    );
}

#[tokio::test]
async fn late_field_upload_changes_membership_and_restores_from_nas_manifest() {
    let env = Env::new().await;
    assert!(
        env.python.is_some(),
        "set COACH_TEST_PYTHON to the explicit interpreter"
    );
    let bytes = fake_ibt(race_meta(9801, 0, "player"), 4000);
    assert_eq!(
        env.upload(&bytes, "ibt", "player.ibt", None, 4096)
            .await
            .status,
        StatusCode::OK
    );
    env.settle().await;
    let session: i64 =
        env.query(|c| c.query_row("SELECT session_id FROM sessions", [], |r| r.get(0)));
    let old = env.query(move |c| jobs::membership_digest(c, session).map(|m| m.0));
    let other_phase = segment(9801, 1);
    assert_eq!(
        env.upload(&other_phase, "field", "other-phase.field", None, 4096)
            .await
            .status,
        StatusCode::OK
    );
    while jobs::run_next(&env.app, "io").await.unwrap() {}
    assert!(
        env.query(move |c| field::members(c, session)).is_empty(),
        "a qualifying phase must not attach to the race"
    );
    assert_eq!(
        env.query(move |c| jobs::membership_digest(c, session).map(|m| m.0)),
        old
    );
    let field_bytes = segment(9801, 0);
    let sha = coach_server::util::sha256_hex(&field_bytes);
    assert_eq!(
        env.upload(&field_bytes, "field", "fixture.field", None, 4096)
            .await
            .status,
        StatusCode::OK
    );
    while jobs::run_next(&env.app, "io").await.unwrap() {}
    let members = env.query(move |c| field::members(c, session));
    assert_eq!(members, vec![(sha.clone(), field_bytes.len() as u64)]);
    let digest = env.query(move |c| jobs::membership_digest(c, session).map(|m| m.0));
    assert_ne!(old, digest);
    let pending: Value = env.query(move |c| {
        let raw: String = c.query_row(
            "SELECT subject_json FROM jobs WHERE kind='analyze' ORDER BY job_id DESC LIMIT 1",
            [],
            |r| r.get(0),
        )?;
        Ok(serde_json::from_str(&raw).unwrap())
    });
    assert_eq!(pending["membership_sha256"], digest);
    assert_eq!(pending["field_recordings"][0]["sha256"], sha);
    let nas_file = env
        .nas()
        .join("field")
        .join(&sha[..2])
        .join(format!("{sha}.field"));
    assert_eq!(std::fs::read(&nas_file).unwrap(), field_bytes);
    let manifest = env
        .nas()
        .join("field")
        .join(&sha[..2])
        .join(format!("{sha}.manifest.json"));
    let manifest: Value = serde_json::from_slice(&std::fs::read(manifest).unwrap()).unwrap();
    assert_eq!(manifest["kind"], "field_recording");
    assert_eq!(manifest["field_recording"]["sample_count"], 2);
    let mut cfg = env.app.cfg.clone();
    cfg.data_dir = env.dir.path().join("rebuilt-data");
    let summary = rebuild::rebuild_index(&cfg).unwrap();
    assert_eq!(summary.skipped, 0);
    let store = db::Store::open(&cfg.db_path(), &cfg.app_version).unwrap();
    let stored=store.read_blocking(|c| Ok(c.query_row("SELECT COUNT(*) FROM field_recordings WHERE customer_id=1001 AND subsession_id=9801 AND session_num=0",[],|r|r.get::<_,i64>(0))?)).unwrap();
    assert_eq!(stored, 1);
    // A job stages the archived original byte-for-byte even after its NVMe spool copy is gone.
    std::fs::remove_file(coach_server::ingest::ready_path(&env.app, &sha)).unwrap();
    let stage = env.dir.path().join("field-stage");
    field::stage(&env.app, &pending, &stage).await.unwrap();
    assert_eq!(
        std::fs::read(stage.join("field").join(format!("{sha}.field.jsonl"))).unwrap(),
        field_bytes
    );
    let before = env.query(move |c| jobs::membership_digest(c, session).map(|m| m.0));
    field::index(&env.app, &sha, &nas_file).await.unwrap();
    let after = env.query(move |c| jobs::membership_digest(c, session).map(|m| m.0));
    assert_eq!(before, after, "re-indexing is idempotent");
}
#[tokio::test]
async fn real_worker_projects_collector_field_laps_and_pit_visit_after_http_upload() {
    let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("..");
    let scripts = root.join("iracing-coach/skills/analyze-iracing-race/scripts");
    let interpreter = python().expect("set COACH_TEST_PYTHON to the real worker interpreter");
    let mut env = Env::new().await;
    let mut cfg = env.app.cfg.clone();
    cfg.worker.command = vec![
        interpreter,
        "-X".into(),
        "utf8".into(),
        "-u".into(),
        scripts.join("mcp_server.py").to_string_lossy().into_owned(),
    ];
    cfg.worker.scripts_dir = Some(scripts);
    // Exercise production parsing and projection, rather than the harness's fake adapters.
    let app = coach_server::App::start(cfg, coach_server::Parts::default())
        .await
        .unwrap();
    env.router = coach_server::api::router(app.clone());
    env.app = app;
    let ibt = std::fs::read(root.join("test-data/ibt/synthetic-race.ibt")).unwrap();
    let reply = env
        .upload(&ibt, "ibt", "synthetic-race.ibt", None, 1 << 20)
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
    while jobs::run_next(&env.app, "light").await.unwrap() {}
    let session: i64 = env.query(|c| c.query_row("SELECT session_id FROM sessions WHERE customer_id=1 AND subsession_id=8001 AND sim_session_num=1", [], |r| r.get(0)));
    for index in 0..3 {
        let name = format!("synthetic-opponents-{index:03}.field.jsonl");
        let bytes = std::fs::read(root.join("test-data/field").join(&name)).unwrap();
        let reply = env.upload(&bytes, "field", &name, None, 4096).await;
        assert_eq!(
            reply.status,
            StatusCode::OK,
            "{}",
            String::from_utf8_lossy(&reply.body)
        );
    }
    while jobs::run_next(&env.app, "io").await.unwrap() {}
    assert_eq!(env.query(move |c| field::members(c, session)).len(), 3);
    while jobs::run_next(&env.app, "analysis").await.unwrap() {}
    while jobs::run_next(&env.app, "server").await.unwrap() {}
    let (state, analysis): (String, Option<String>) = env.query(move |c| {
        c.query_row(
            "SELECT analysis_state,current_analysis_id FROM sessions WHERE session_id=?1",
            [session],
            |r| Ok((r.get(0)?, r.get(1)?)),
        )
    });
    let failures: Vec<(String,String,Option<String>)> = env.query(|c| c.prepare("SELECT kind,state,error_message FROM jobs WHERE state NOT IN ('succeeded','cancelled')")?.query_map([], |r| Ok((r.get(0)?,r.get(1)?,r.get(2)?)))?.collect());
    assert_eq!(state, "ready", "pipeline jobs: {failures:?}");
    let analysis = analysis.expect("published real-worker analysis");
    let race = env.get(&format!("/api/v1/analyses/{analysis}/race")).await;
    assert_eq!(
        race.status,
        StatusCode::OK,
        "{}",
        String::from_utf8_lossy(&race.body)
    );
    let race = race.json();
    let results = &race["field_pace"]["results"];
    let rival = results["cars"]
        .as_array()
        .expect("projected field results")
        .iter()
        .find(|c| c["is_player"] == false)
        .expect("synthetic competitor");
    assert_eq!(rival["pit_visits"], 1);
    let laps: Vec<f64> = rival["laps"]
        .as_array()
        .unwrap()
        .iter()
        .map(|l| l["lap_time_s"].as_f64().unwrap())
        .collect();
    assert_eq!(laps, vec![50., 60.]);
    assert!(
        results["cars"]
            .as_array()
            .unwrap()
            .iter()
            .find(|c| c["is_player"] == true)
            .unwrap()["laps"]
            .as_array()
            .is_some_and(|laps| !laps.is_empty())
    );
    let current = env.query(move |c| jobs::membership_digest(c, session).map(|m| m.0));
    let analyzed: String = env.query(move |c| {
        c.query_row(
            "SELECT membership_sha256 FROM analyses WHERE analysis_id=?1",
            [&analysis],
            |r| r.get(0),
        )
    });
    assert_eq!(
        current, analyzed,
        "published analysis covers all three uploaded field segments"
    );
}

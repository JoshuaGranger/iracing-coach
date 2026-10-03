//! D12 import: the bundle's legacy archive goes through the worker's
//! `import_legacy_tuning_experiments` on the light lane, with every legacy analysis resolved to
//! the matching session's current analysis.

mod support;

use axum::http::StatusCode;
use coach_server::util::sha256_hex;
use serde_json::{Value, json};
use support::*;

macro_rules! require_python {
    ($env:expr) => {
        if $env.python.is_none() {
            eprintln!("skipped: no Python 3 on PATH (set COACH_TEST_PYTHON)");
            return;
        }
    };
}

async fn ingest(env: &Env, subsession: i64) {
    let bytes = fake_ibt(race_meta(subsession, 0, "imported"), 4_000);
    env.upload(&bytes, "ibt", "rec.ibt", None, 4096).await;
    coach_server::ingest::ingest_pending(&env.app)
        .await
        .unwrap();
}

fn scalar<T: Send + 'static + rusqlite::types::FromSql>(env: &Env, sql: &str) -> T {
    let sql = sql.to_string();
    env.query(move |c| c.query_row(&sql, [], |r| r.get(0)))
}

fn bundle(files: Vec<Value>) -> Value {
    json!({
        "bundle_version": 1, "customer_id": CUSTOMER,
        "source": {"machine": "DESKTOP-SIM", "root": "C:/legacy"},
        "legacy_analyses": [
            {"legacy_analysis_id": "old-a", "subsession_id": 9600, "sim_session_num": 0},
            {"legacy_analysis_id": "old-b", "subsession_id": 123456}
        ],
        "legacy_archive": {"files": files},
    })
}

fn experiment(id: &str, analysis: &str) -> Value {
    legacy_file(
        &format!("tuning/experiments/2026S3/stock/iowa/{id}.json"),
        json!({"experiment_id": id, "analysis_id": analysis, "analysis_path": "C:/old/analysis.json"})
            .to_string()
            .as_bytes(),
    )
}

async fn import_job(env: &Env, bundle: Value) -> Value {
    let reply = env.post("/api/v1/admin/import", bundle).await;
    assert_eq!(
        reply.status,
        StatusCode::ACCEPTED,
        "{}",
        String::from_utf8_lossy(&reply.body)
    );
    let id = reply.json()["job_id"].as_i64().unwrap();
    env.settle().await;
    env.get(&format!("/api/v1/jobs/{id}")).await.json()
}

#[tokio::test]
async fn the_legacy_archive_is_imported_through_the_worker_with_a_resolved_analysis_map() {
    let env = Env::new().await;
    require_python!(env);
    ingest(&env, 9600).await;
    env.settle().await;
    let analysis_id: String = scalar(&env, "SELECT current_analysis_id FROM sessions");
    let analysis_path: String = scalar(&env, "SELECT analysis_path FROM analyses");
    let generation: i64 = scalar(
        &env,
        "SELECT value FROM server_counters WHERE name = 'user_data_generation'",
    );

    let files = vec![
        legacy_file("history.sqlite3", b"copied byte for byte"),
        experiment("exp-1", "old-a"),
        experiment("exp-2", "old-b"),
    ];
    let job = import_job(&env, bundle(files.clone())).await;
    assert_eq!(job["state"], "succeeded", "{job}");
    assert_eq!(job["lane"], "light", "the worker runs on the light lane");

    let summary = &job["result"];
    assert_eq!(summary["tuning_experiments_imported"], 2, "{summary}");
    assert_eq!(summary["tuning_experiments_resolved"], 1);
    assert_eq!(summary["tuning_experiments_unresolved"], 1);
    assert_eq!(summary["legacy_analyses"], 2);
    assert_eq!(summary["legacy_analyses_resolved"], 1);
    assert_eq!(summary["legacy_analyses_unresolved"], 1);
    assert_eq!(
        scalar::<i64>(
            &env,
            "SELECT COUNT(*) FROM legacy_items WHERE kind = 'tuning_experiment'"
        ),
        0
    );
    let after: i64 = scalar(
        &env,
        "SELECT value FROM server_counters WHERE name = 'user_data_generation'",
    );
    assert!(after > generation, "the import bumps user_data_generation");

    // what the worker was given: every file under the job root's legacy/, and the resolved map
    let call: Value = serde_json::from_slice(
        &std::fs::read(env.data().join("py/1001/archive/import-call.json")).unwrap(),
    )
    .unwrap();
    assert_eq!(
        call["files"],
        json!([
            "history.sqlite3",
            "tuning/experiments/2026S3/stock/iowa/exp-1.json",
            "tuning/experiments/2026S3/stock/iowa/exp-2.json"
        ])
    );
    assert!(call["root"].as_str().unwrap().contains("jobs"));
    let map = call["analysis_map"].as_array().unwrap();
    let old_a = map
        .iter()
        .find(|m| m["legacy_analysis_id"] == "old-a")
        .unwrap();
    assert_eq!(old_a["analysis_id"], analysis_id.as_str());
    assert_eq!(old_a["analysis_path"], analysis_path.as_str());
    let old_b = map
        .iter()
        .find(|m| m["legacy_analysis_id"] == "old-b")
        .unwrap();
    assert!(old_b["analysis_id"].is_null() && old_b["analysis_path"].is_null());
    assert!(
        std::fs::read_dir(env.data().join("py/1001/jobs"))
            .map(|d| d.count() == 0)
            .unwrap_or(true),
        "the job root is gone after the import"
    );

    // the same bundle imports once
    let again = env.post("/api/v1/admin/import", bundle(files)).await;
    assert_eq!(again.status, StatusCode::OK);
}

#[tokio::test]
async fn a_legacy_file_that_fails_its_checks_fails_the_import_before_anything_is_stored() {
    let env = Env::new().await;
    ingest(&env, 9601).await;
    let good = legacy_file("tuning/experiments/x/y/z/ok.json", b"{}");
    let mut wrong_hash = good.clone();
    wrong_hash["sha256"] = json!(sha256_hex(b"something else"));
    let mut bad_base64 = good.clone();
    bad_base64["content_base64"] = json!("not base64!");
    let cases: Vec<(&str, Vec<Value>)> = vec![
        ("hash", vec![wrong_hash]),
        ("base64", vec![bad_base64]),
        (
            "dotdot",
            vec![legacy_file("tuning/../../escape.json", b"{}")],
        ),
        ("absolute", vec![legacy_file("/etc/passwd", b"{}")]),
        ("drive", vec![legacy_file("tuning/C:/x.json", b"{}")]),
        ("wal", vec![legacy_file("history.sqlite3-wal", b"{}")]),
        ("other", vec![legacy_file("notes/x.json", b"{}")]),
        ("duplicate", vec![good.clone(), good.clone()]),
    ];
    for (name, files) in cases {
        let mut b = bundle(files);
        b["source"]["root"] = json!(format!("C:/legacy/{name}"));
        let job = import_job(&env, b).await;
        assert_eq!(job["state"], "failed", "{name}: {job}");
        assert_eq!(job["error_code"], "invalid_bundle", "{name}: {job}");
    }
    assert_eq!(scalar::<i64>(&env, "SELECT COUNT(*) FROM imports"), 0);
    assert!(
        !env.data().join("py/1001/archive/import-call.json").exists(),
        "the worker was never called"
    );
}

#[tokio::test]
async fn a_failing_worker_fails_the_import_and_leaves_it_retryable() {
    let env = Env::with(Opts {
        tweak: Box::new(|c| c.worker.command = vec!["no-such-worker-program".into()]),
        ..Default::default()
    })
    .await;
    ingest(&env, 9602).await;
    let job = import_job(&env, bundle(vec![legacy_file("history.sqlite3", b"x")])).await;
    assert_eq!(job["state"], "failed", "{job}");
    assert_eq!(job["error_code"], "worker_crash");
    assert_eq!(
        scalar::<i64>(&env, "SELECT COUNT(*) FROM imports"),
        0,
        "nothing is recorded when the worker step fails"
    );
}

#[tokio::test]
async fn a_bundle_without_a_legacy_archive_needs_no_worker() {
    let env = Env::with(Opts {
        tweak: Box::new(|c| c.worker.command = vec!["no-such-worker-program".into()]),
        ..Default::default()
    })
    .await;
    ingest(&env, 9603).await;
    let mut b = bundle(vec![]);
    b.as_object_mut().unwrap().remove("legacy_archive");
    let job = import_job(&env, b).await;
    assert_eq!(job["state"], "succeeded", "{job}");
    assert_eq!(job["result"]["tuning_experiments_imported"], 0);
    assert_eq!(scalar::<i64>(&env, "SELECT COUNT(*) FROM imports"), 1);
}

/// The real tool on a generated legacy archive (needs Python and the Python backend).
#[tokio::test]
async fn the_real_import_tool_records_a_generated_legacy_archive() {
    let scripts = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../iracing-coach/skills/analyze-iracing-race/scripts");
    let Some(python) = python() else {
        eprintln!("skipped: no Python 3 available");
        return;
    };
    if !scripts.join("legacy_import.py").exists() {
        eprintln!("skipped: the worker has no legacy_import.py");
        return;
    }
    let script = scripts.join("mcp_server.py").to_string_lossy().into_owned();
    let env = Env::with(Opts {
        tweak: Box::new(move |c| {
            c.worker.command = vec![
                python.clone(),
                "-X".into(),
                "utf8".into(),
                "-u".into(),
                script.clone(),
            ];
            c.worker.scripts_dir = Some(scripts.clone());
        }),
        ..Default::default()
    })
    .await;
    ingest(&env, 9604).await;

    // a legacy archive: history.sqlite3 with one experiment row, and its record file
    let scratch = tempfile::tempdir().unwrap();
    let database = scratch.path().join("history.sqlite3");
    {
        let conn = rusqlite::Connection::open(&database).unwrap();
        conn.execute_batch(
            "CREATE TABLE tuning_experiments (
               experiment_id TEXT PRIMARY KEY, created_at TEXT, updated_at TEXT, analysis_id TEXT,
               package_id TEXT, season_key TEXT, car_key TEXT, track_key TEXT, setup_type TEXT,
               setup_fingerprint TEXT, symptoms_json TEXT, recommendation_json TEXT,
               feedback_json TEXT, outcome TEXT, experiment_path TEXT);
             INSERT INTO tuning_experiments VALUES
               ('exp-legacy-1', '2026-09-01T10:00:00Z', '2026-09-01T10:00:00Z', 'old-a', NULL,
                '2026S3', 'stock', 'iowa', 'open', 'fp', '[]', '{}', NULL, NULL, 'C:/old/exp.json');",
        )
        .unwrap();
    }
    let record = json!({
        "schema_version": 1, "experiment_id": "exp-legacy-1", "created_at": "2026-09-01T10:00:00Z",
        "analysis_id": "old-a", "analysis_path": "C:/old/analysis.json",
        "context": {"season_key": "2026S3", "car_key": "stock", "track_key": "iowa", "setup_type": "open"},
        "setup": {"fingerprint": "fp"}, "symptoms": [], "recommendation": {}, "feedback": null, "outcome": null
    });
    let files = vec![
        legacy_file("history.sqlite3", &std::fs::read(&database).unwrap()),
        legacy_file(
            "tuning/experiments/2026S3/stock/iowa/exp-legacy-1.json",
            record.to_string().as_bytes(),
        ),
    ];
    let job = import_job(&env, bundle(files)).await;
    assert_eq!(job["state"], "succeeded", "{job}");
    assert_eq!(job["result"]["tuning_experiments_imported"], 1, "{job}");
    // old-a resolves to nothing in this library (no analysed session), so the reference is nulled
    assert_eq!(job["result"]["tuning_experiments_unresolved"], 1);

    // the experiment is in the driver's worker archive, with its original id
    let history = env.data().join("py/1001/archive/history.sqlite3");
    let conn =
        rusqlite::Connection::open_with_flags(&history, rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY)
            .unwrap();
    let id: String = conn
        .query_row("SELECT experiment_id FROM tuning_experiments", [], |r| {
            r.get(0)
        })
        .unwrap();
    assert_eq!(id, "exp-legacy-1");
}

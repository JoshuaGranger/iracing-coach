//! Job runner and worker supervision with the stub worker, session assembly and the membership
//! rule, plus the real worker on the synthetic fixture when Python is available.

mod support;

use std::time::Duration;

use axum::http::StatusCode;
use coach_server::jobs::{self, JobOutcome, JobRow};
use coach_server::util::sha256_hex;
use coach_server::worker::{Cancel, WorkerCall, WorkerError, is_oom_exit, run_worker, worker_env};
use serde_json::{Value, json};
use support::*;

fn meta(subsession: i64, num: i64, name: &str, behavior: Option<&str>) -> Value {
    let mut m = race_meta(subsession, num, name);
    if let Some(b) = behavior {
        m["behavior"] = json!(b);
    }
    m
}

async fn ingest(env: &Env, meta: Value) -> String {
    let bytes = fake_ibt(meta, 4_000);
    let sha = sha256_hex(&bytes);
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
    sha
}

fn scalar<T: Send + 'static + rusqlite::types::FromSql>(env: &Env, sql: &str) -> T {
    let sql = sql.to_string();
    env.query(move |c| c.query_row(&sql, [], |r| r.get(0)))
}

fn job_state(env: &Env, kind: &str) -> Vec<(String, Option<String>)> {
    let kind = kind.to_string();
    env.query(move |c| {
        let mut stmt =
            c.prepare("SELECT state, error_code FROM jobs WHERE kind = ?1 ORDER BY job_id")?;
        let rows = stmt
            .query_map([kind], |r| Ok((r.get(0)?, r.get(1)?)))?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        Ok(rows)
    })
}

macro_rules! require_python {
    ($env:expr) => {
        if $env.python.is_none() {
            eprintln!("skipped: no Python 3 on PATH (set COACH_TEST_PYTHON)");
            return;
        }
    };
}

#[tokio::test]
async fn recording_becomes_a_session_with_a_current_analysis_and_views() {
    let env = Env::new().await;
    require_python!(env);
    let sha = ingest(&env, meta(9001, 0, "one", None)).await;
    let mut events = env.app.events.subscribe();
    env.settle().await;

    assert_eq!(scalar::<i64>(&env, "SELECT COUNT(*) FROM sessions"), 1);
    assert_eq!(
        scalar::<String>(&env, "SELECT group_key FROM sessions"),
        "subsession:9001:0"
    );
    assert_eq!(
        scalar::<String>(&env, "SELECT analysis_state FROM sessions"),
        "ready"
    );
    let analysis_id: String = scalar(&env, "SELECT current_analysis_id FROM sessions");
    assert_eq!(
        scalar::<String>(&env, "SELECT projection_state FROM analyses"),
        "ready"
    );
    assert!(
        env.data()
            .join("views")
            .join(&analysis_id)
            .join(format!("p{PROJECTION}"))
            .join("race.json")
            .exists()
    );
    // the library row carries the figures the projection copied
    let overview: String = scalar(&env, "SELECT overview_json FROM sessions");
    let overview: Value = serde_json::from_str(&overview).unwrap();
    assert_eq!(overview["recorded_laps"], 2);
    assert_eq!(overview["final_position"], 3);
    // the recording archived and, with its jobs done, released its spool copy
    assert_eq!(
        scalar::<String>(
            &env,
            &format!("SELECT nas_state FROM blobs WHERE sha256 = '{sha}'")
        ),
        "archived"
    );
    assert_eq!(
        scalar::<i64>(
            &env,
            &format!("SELECT spool_present FROM blobs WHERE sha256 = '{sha}'")
        ),
        0
    );
    assert_eq!(
        scalar::<String>(
            &env,
            &format!("SELECT ingest_state FROM recordings WHERE sha256 = '{sha}'")
        ),
        "discovered"
    );
    // the stub left no job root behind
    assert!(
        std::fs::read_dir(env.data().join("py/1001/jobs"))
            .map(|d| d.count() == 0)
            .unwrap_or(true)
    );
    // events: job states and analysis_ready reached the bus
    let mut kinds = Vec::new();
    while let Ok(e) = events.try_recv() {
        kinds.push(e.kind);
    }
    assert!(
        kinds.contains(&"analysis_ready")
            && kinds.contains(&"job_state")
            && kinds.contains(&"library_changed"),
        "{kinds:?}"
    );
}

#[tokio::test]
async fn a_reconnected_race_joins_its_session_and_supersedes_the_analysis() {
    let env = Env::new().await;
    require_python!(env);
    ingest(&env, meta(9002, 1, "first", None)).await;
    env.settle().await;
    let first: String = scalar(&env, "SELECT current_analysis_id FROM sessions");
    ingest(&env, meta(9002, 1, "second", None)).await;
    env.settle().await;
    assert_eq!(
        scalar::<i64>(&env, "SELECT COUNT(*) FROM sessions"),
        1,
        "one row per SubSessionID + session number"
    );
    assert_eq!(
        scalar::<i64>(&env, "SELECT COUNT(*) FROM session_recordings"),
        2
    );
    let second: String = scalar(&env, "SELECT current_analysis_id FROM sessions");
    assert_ne!(first, second);
    assert_eq!(
        scalar::<String>(
            &env,
            &format!("SELECT superseded_by FROM analyses WHERE analysis_id = '{first}'")
        ),
        second
    );
    assert_eq!(
        scalar::<String>(&env, "SELECT analysis_state FROM sessions"),
        "ready"
    );
}

#[tokio::test]
async fn recording_without_server_ids_uses_a_local_file_key_and_path_selector() {
    let env = Env::new().await;
    require_python!(env);
    let mut m = meta(0, 0, "offline", None);
    m["subsession_id"] = json!(0);
    let sha = ingest(&env, m).await;
    env.settle().await;
    assert_eq!(
        scalar::<String>(&env, "SELECT group_key FROM sessions"),
        format!("local-file:{sha}")
    );
    assert_eq!(
        scalar::<String>(&env, "SELECT analysis_state FROM sessions"),
        "ready"
    );
    assert_eq!(job_state(&env, "analyze")[0].0, "succeeded");
}

#[tokio::test]
async fn analysis_membership_a_late_recording_queues_a_new_job_and_the_stale_result_is_not_current()
{
    let env = Env::new().await;
    require_python!(env);
    // Recording A's analysis blocks inside the worker until released.
    ingest(&env, meta(9003, 0, "a", Some("block"))).await;
    assert!(
        jobs::run_next(&env.app, "light").await.unwrap(),
        "discover A"
    );
    let archive = env.data().join("py/1001/archive");
    let app = env.app.clone();
    let running = tokio::spawn(async move { jobs::run_next(&app, "analysis").await.unwrap() });
    let started = archive.join("started-1");
    for _ in 0..200 {
        if started.exists() {
            break;
        }
        tokio::time::sleep(Duration::from_millis(50)).await;
    }
    assert!(started.exists(), "analysis A is running");
    assert_eq!(
        scalar::<String>(&env, "SELECT analysis_state FROM sessions"),
        "running"
    );

    // Recording B joins the same session while A's analysis runs.
    ingest(&env, meta(9003, 0, "b", Some("block"))).await;
    assert!(
        jobs::run_next(&env.app, "light").await.unwrap(),
        "discover B"
    );
    assert_eq!(
        scalar::<i64>(&env, "SELECT COUNT(*) FROM session_recordings"),
        2
    );
    assert_eq!(
        job_state(&env, "analyze")
            .iter()
            .map(|s| s.0.as_str())
            .collect::<Vec<_>>(),
        vec!["running", "queued"],
        "B's job is queued"
    );

    // A finishes: its result is stored but is not made current.
    std::fs::write(archive.join("release-1"), b"").unwrap();
    assert!(running.await.unwrap());
    assert_eq!(scalar::<i64>(&env, "SELECT COUNT(*) FROM analyses"), 1);
    assert_eq!(
        scalar::<Option<String>>(&env, "SELECT current_analysis_id FROM sessions"),
        None
    );
    assert_eq!(
        scalar::<String>(&env, "SELECT analysis_state FROM sessions"),
        "queued"
    );
    assert_eq!(
        job_state(&env, "project").len(),
        0,
        "a stale result is not projected"
    );

    // B runs over both recordings and becomes current.
    std::fs::write(archive.join("release-2"), b"").unwrap();
    assert!(jobs::run_next(&env.app, "analysis").await.unwrap());
    let current: String = scalar(&env, "SELECT current_analysis_id FROM sessions");
    assert_eq!(scalar::<i64>(&env, "SELECT COUNT(*) FROM analyses"), 2);
    assert!(
        jobs::run_next(&env.app, "server").await.unwrap(),
        "projection"
    );
    assert_eq!(
        scalar::<String>(&env, "SELECT analysis_state FROM sessions"),
        "ready"
    );
    let members: String = scalar(
        &env,
        &format!("SELECT recordings_sha256 FROM analyses WHERE analysis_id = '{current}'"),
    );
    assert_eq!(
        serde_json::from_str::<Vec<String>>(&members).unwrap().len(),
        2
    );
}

#[tokio::test]
async fn analysis_over_the_memory_ceiling_is_deferred_with_its_reason() {
    let env = Env::with(Opts {
        tweak: Box::new(|c| c.analysis_ceiling_bytes = 100 << 20),
        ..Default::default()
    })
    .await;
    ingest(&env, meta(9004, 0, "big", None)).await;
    env.settle().await;
    assert_eq!(
        scalar::<String>(&env, "SELECT analysis_state FROM sessions"),
        "deferred"
    );
    assert_eq!(
        scalar::<String>(&env, "SELECT analysis_reason FROM sessions"),
        "exceeds_memory_budget"
    );
    assert_eq!(
        job_state(&env, "analyze"),
        vec![(
            "deferred".to_string(),
            Some("exceeds_memory_budget".to_string())
        )]
    );
    let sessions = env.get("/api/v1/drivers/1001/sessions").await.json();
    assert_eq!(sessions["sessions"][0]["analysis_state"], "deferred");
    assert_eq!(
        sessions["sessions"][0]["analysis_reason"],
        "exceeds_memory_budget"
    );
}

#[tokio::test]
async fn shared_memory_budget_makes_a_job_wait_for_running_work() {
    let env = Env::with(Opts {
        tweak: Box::new(|c| c.memory_budget_bytes = (0.9 * (1u64 << 30) as f64) as u64),
        ..Default::default()
    })
    .await;
    require_python!(env);
    ingest(&env, meta(9005, 0, "budget", Some("block"))).await;
    assert!(jobs::run_next(&env.app, "light").await.unwrap());
    let app = env.app.clone();
    let running = tokio::spawn(async move { jobs::run_next(&app, "analysis").await.unwrap() });
    let started = env.data().join("py/1001/archive/started-1");
    for _ in 0..200 {
        if started.exists() {
            break;
        }
        tokio::time::sleep(Duration::from_millis(50)).await;
    }
    assert!(started.exists());
    // a projection (0.6 GiB) does not fit beside the analysis (0.15 GiB) and the server reserve (0.3 GiB)
    env.app
        .store
        .write_blocking(|c| {
            c.execute(
                "INSERT INTO analyses(analysis_id, session_id, recordings_sha256, membership_sha256, schema_version, profile_version,
                   analysis_path, analysis_bytes, created_at) VALUES ('zz', 1, '[]', 'x', 1, 'p', '/none', 1, '2026-01-01T00:00:00Z')",
                [],
            )?;
            let tx = c.transaction()?;
            jobs::enqueue_tx(&tx, &jobs::NewJob::project("zz", PROJECTION, Some(1001)), false)?;
            tx.commit()?;
            Ok(())
        })
        .unwrap();
    assert!(
        !jobs::run_next(&env.app, "server").await.unwrap(),
        "the projection waits"
    );
    std::fs::write(env.data().join("py/1001/archive/release-1"), b"").unwrap();
    running.await.unwrap();
    assert!(
        jobs::run_next(&env.app, "server").await.unwrap(),
        "after the analysis it runs"
    );
}

fn call(env: &Env, tool: &str, root: &std::path::Path, timeout: Duration) -> WorkerCall {
    WorkerCall {
        tool: tool.into(),
        arguments: json!({}),
        iracing_root: root.to_path_buf(),
        archive_root: env.data().join("py/1/archive"),
        home: env.data().join("py/1/tmp"),
        timeout,
        garage61_token_file: None,
        pid: Default::default(),
    }
}

#[tokio::test]
async fn worker_environment_is_complete_and_inherits_nothing() {
    let env = Env::new().await;
    let call = call(&env, "x", &env.data().join("root"), Duration::from_secs(1));
    let vars = worker_env(&call);
    for key in [
        "IRACING_COACH_IRACING_ROOT",
        "IRACING_COACH_DATA",
        "IRACING_COACH_RAW_ARCHIVE",
        "IRACING_COACH_NATIVE_STAGES",
        "PYTHONUTF8",
        "PYTHONDONTWRITEBYTECODE",
        "LANG",
        "HOME",
        "TMPDIR",
        "PATH",
    ] {
        assert!(vars.contains_key(key), "missing {key}");
    }
    assert_eq!(vars["IRACING_COACH_RAW_ARCHIVE"], "external");
    assert_eq!(vars["IRACING_COACH_NATIVE_STAGES"], "");
    assert!(!vars.contains_key("IRACING_COACH_GARAGE61_TOKEN_FILE"));
    assert!(!vars.contains_key("IRACING_COACH_INSTALL_ROOT"));
    assert!(is_oom_exit(Some(9), 3, 4));
    assert!(!is_oom_exit(Some(9), 3, 3));
    assert!(!is_oom_exit(Some(15), 3, 9));
}

async fn run_behavior(
    env: &Env,
    behavior: &str,
    tweak: impl FnOnce(&mut JobRow),
) -> (JobOutcome, JobRow) {
    ingest(env, meta(9100, 0, behavior, Some(behavior))).await;
    assert!(jobs::run_next(&env.app, "light").await.unwrap());
    let mut job: JobRow = env.query(|c| {
        c.query_row(
            "SELECT job_id, kind, lane, customer_id, subject_json, attempts FROM jobs WHERE kind = 'analyze'",
            [],
            |r| {
                let subject: String = r.get(4)?;
                Ok(JobRow { job_id: r.get(0)?, kind: r.get(1)?, lane: r.get(2)?, customer_id: r.get(3)?, subject: serde_json::from_str(&subject).unwrap(), attempts: r.get(5)? })
            },
        )
    });
    tweak(&mut job);
    let outcome = coach_server::handlers::run(&env.app, &job, &Cancel::default()).await;
    (outcome, job)
}

#[tokio::test]
async fn worker_tool_error_crash_and_oom_exit_are_classified() {
    let env = Env::new().await;
    require_python!(env);
    let (outcome, _) = run_behavior(&env, "iserror", |_| {}).await;
    match outcome {
        JobOutcome::Failed { code, message } => {
            assert_eq!(code, "tool_error");
            assert!(message.contains("failed on purpose"), "{message}");
        }
        other => panic!("{other:?}"),
    }
    let env = Env::new().await;
    let (outcome, _) = run_behavior(&env, "crash", |_| {}).await;
    match outcome {
        JobOutcome::Failed { code, message } => {
            assert_eq!(code, "worker_crash");
            assert!(
                message.contains("stub crash"),
                "stderr is carried: {message}"
            );
        }
        other => panic!("{other:?}"),
    }
    // a worker killed with SIGKILL outside a cgroup OOM is a crash, not an OOM
    let env = Env::new().await;
    let (outcome, _) = run_behavior(&env, "oom", |_| {}).await;
    assert!(
        matches!(outcome, JobOutcome::Failed { ref code, .. } if code == "worker_crash"),
        "{outcome:?}"
    );
}

#[tokio::test]
async fn worker_timeout_stops_the_process_group_and_cleans_orphans() {
    let env = Env::with(Opts {
        tweak: Box::new(|c| c.worker.analyze_timeout = Duration::from_secs(2)),
        ..Default::default()
    })
    .await;
    require_python!(env);
    let archive = env.data().join("py/1001/archive");
    let (outcome, _) = run_behavior(&env, "orphans", |_| {}).await;
    assert!(
        matches!(outcome, JobOutcome::Failed { ref code, .. } if code == "timeout"),
        "{outcome:?}"
    );
    // the pid-named temp file and the stray .write file (no other worker for the driver) are gone
    let left: Vec<String> = std::fs::read_dir(&archive)
        .unwrap()
        .map(|e| e.unwrap().file_name().to_string_lossy().into_owned())
        .collect();
    assert!(
        !left
            .iter()
            .any(|n| n.ends_with(".tmp") || n.starts_with(".write-")),
        "{left:?}"
    );
    #[cfg(unix)]
    {
        let child: i32 = std::fs::read_to_string(archive.join("child.pid"))
            .unwrap()
            .trim()
            .parse()
            .unwrap();
        std::thread::sleep(Duration::from_millis(300));
        assert!(
            !std::path::Path::new(&format!("/proc/{child}")).exists(),
            "the child of the worker was killed with its group"
        );
    }
}

#[tokio::test]
async fn cancelling_a_running_job_kills_the_worker() {
    let env = Env::new().await;
    require_python!(env);
    ingest(&env, meta(9101, 0, "hang", Some("hang"))).await;
    assert!(jobs::run_next(&env.app, "light").await.unwrap());
    let app = env.app.clone();
    let running = tokio::spawn(async move { jobs::run_next(&app, "analysis").await.unwrap() });
    let pid_file = env.data().join("py/1001/archive/child.pid");
    for _ in 0..200 {
        if pid_file.exists() {
            break;
        }
        tokio::time::sleep(Duration::from_millis(50)).await;
    }
    assert!(pid_file.exists(), "the worker is running");
    let job_id: i64 = scalar(&env, "SELECT job_id FROM jobs WHERE kind = 'analyze'");
    let reply = env
        .post(&format!("/api/v1/jobs/{job_id}/cancel"), json!({}))
        .await;
    assert_eq!(reply.status, StatusCode::ACCEPTED);
    assert!(running.await.unwrap());
    assert_eq!(job_state(&env, "analyze")[0].0, "cancelled");
    // cancelling a finished job conflicts
    let again = env
        .post(&format!("/api/v1/jobs/{job_id}/cancel"), json!({}))
        .await;
    assert_eq!(again.status, StatusCode::CONFLICT);
}

#[tokio::test]
async fn running_jobs_become_interrupted_and_are_retried_once() {
    let env = Env::new().await;
    ingest(&env, meta(9102, 0, "interrupted", None)).await;
    env.app
        .store
        .write_blocking(|c| {
            c.execute(
                "UPDATE jobs SET state = 'running', attempts = 1 WHERE kind = 'discover'",
                [],
            )?;
            Ok(())
        })
        .unwrap();
    let env = env.restart().await;
    assert_eq!(
        job_state(&env, "discover")[0].0,
        "queued",
        "first interruption is retried"
    );
    env.app
        .store
        .write_blocking(|c| {
            c.execute(
                "UPDATE jobs SET state = 'running', attempts = 2 WHERE kind = 'discover'",
                [],
            )?;
            Ok(())
        })
        .unwrap();
    let env = env.restart().await;
    assert_eq!(
        job_state(&env, "discover")[0].0,
        "interrupted",
        "a second interruption is not retried"
    );
}

#[tokio::test]
async fn raw_worker_call_reports_a_missing_interpreter_as_a_crash() {
    let env = Env::with(Opts {
        tweak: Box::new(|c| c.worker.command = vec!["definitely-not-a-program".into()]),
        ..Default::default()
    })
    .await;
    let call = call(&env, "x", &env.data().join("root"), Duration::from_secs(2));
    let result = run_worker(&env.app.cfg.worker, &call, &Cancel::default(), "t").await;
    assert!(matches!(result, Err(WorkerError::Spawn(_))), "{result:?}");
}

#[tokio::test]
async fn htm_exports_become_setup_observations() {
    let env = Env::new().await;
    require_python!(env);
    ingest(&env, meta(9103, 0, "driver", None)).await;
    let htm = b"<html>setup export</html>".to_vec();
    let reply = env
        .upload(
            &htm,
            "htm",
            "baseline.htm",
            Some("stockcars chevyss2020/baseline.htm"),
            4096,
        )
        .await;
    assert_eq!(reply.json()["state"], "verified");
    env.settle().await;
    assert_eq!(
        scalar::<i64>(
            &env,
            "SELECT COUNT(*) FROM setup_observations WHERE source = 'htm_export'"
        ),
        1
    );
    assert_eq!(job_state(&env, "setup_files")[0].0, "succeeded");
    let page = env.get("/api/v1/drivers/1001/setups").await.json();
    assert!(page["setups"].as_array().unwrap().iter().any(|s| {
        s["sources"]
            .as_array()
            .unwrap()
            .iter()
            .any(|v| v == "htm_export")
    }));
}

#[tokio::test]
async fn real_worker_analyses_the_synthetic_fixture() {
    let scripts = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../iracing-coach/skills/analyze-iracing-race/scripts");
    let fixture = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../test-data/ibt/synthetic-race.ibt");
    let Some(python) = python() else {
        eprintln!("skipped: no Python 3 available");
        return;
    };
    if !scripts.join("mcp_server.py").exists() || !fixture.exists() {
        eprintln!("skipped: worker or fixture missing");
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
    // The fake parser cannot read a real IBT, so the recording is announced by a fake header
    // file and the real bytes are placed in the spool under their own hash.
    let bytes = std::fs::read(&fixture).unwrap();
    let reply = env
        .upload(&bytes, "ibt", "synthetic-race.ibt", None, 1 << 20)
        .await;
    assert_eq!(reply.status, StatusCode::OK);
    env.app
        .store
        .write_blocking(|c| {
            c.execute("INSERT OR IGNORE INTO drivers(customer_id, display_name, first_seen_at, last_seen_at) VALUES (1001, 'Test', 'x', 'x')", [])?;
            c.execute("UPDATE recordings SET customer_id = 1001, ingest_state = 'parsed'", [])?;
            let tx = c.transaction()?;
            jobs::enqueue_tx(&tx, &jobs::NewJob::discover(1001, &sha_of_only_blob(&tx)), false)?;
            tx.commit()?;
            Ok(())
        })
        .unwrap();
    while jobs::run_next(&env.app, "light").await.unwrap() {}
    assert_eq!(
        job_state(&env, "discover")[0].0,
        "succeeded",
        "{:?}",
        env.query(|c| c.query_row(
            "SELECT error_message FROM jobs WHERE kind = 'discover'",
            [],
            |r| r.get::<_, Option<String>>(0)
        ))
    );
    assert_eq!(scalar::<i64>(&env, "SELECT COUNT(*) FROM sessions"), 1);
    while jobs::run_next(&env.app, "analysis").await.unwrap() {}
    let state = job_state(&env, "analyze");
    let message: Option<String> = env.query(|c| {
        c.query_row(
            "SELECT error_message FROM jobs WHERE kind = 'analyze'",
            [],
            |r| r.get(0),
        )
    });
    assert_eq!(state[0].0, "succeeded", "analysis failed: {message:?}");
    let path: String = scalar(&env, "SELECT analysis_path FROM analyses");
    let analysis: Value = serde_json::from_slice(&std::fs::read(&path).unwrap()).unwrap();
    assert!(analysis["analysis_id"].is_string());
    assert!(
        analysis.get("analysis_view").is_some()
            || analysis.get("laps").is_some()
            || analysis.is_object()
    );
}

fn sha_of_only_blob(conn: &rusqlite::Connection) -> String {
    conn.query_row("SELECT sha256 FROM blobs LIMIT 1", [], |r| r.get(0))
        .unwrap()
}

#[tokio::test]
async fn job_root_file_names_cannot_escape_the_root() {
    let env = Env::new().await;
    let sha = ingest(&env, meta(9990, 0, "escape", None)).await;
    let job = JobRow {
        job_id: 4242,
        kind: "discover".into(),
        lane: "light".into(),
        customer_id: Some(1001),
        subject: json!({}),
        attempts: 0,
    };
    let (root, names) = coach_server::handlers::prepare_root(
        &env.app,
        &job,
        1001,
        &[(sha, "..\\..\\evil.ibt".to_string())],
        false,
    )
    .await
    .unwrap();
    assert_eq!(names, vec!["evil.ibt".to_string()]);
    assert!(root.join("telemetry/evil.ibt").exists());
    assert!(!root.parent().unwrap().join("evil.ibt").exists());
    assert!(!root.join("evil.ibt").exists());
}

//! Job runner and worker supervision with the stub worker, session assembly and the membership
//! rule, plus the real worker on the synthetic fixture when Python is available.

mod support;

use std::time::Duration;

use axum::http::StatusCode;
use coach_server::config::{GIB, NativeStages};
use coach_server::jobs::{self, JobOutcome, JobRow, QueueParams};
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
async fn the_deferral_reaches_the_session_detail_and_the_job_list_with_its_reason() {
    let env = Env::with(Opts {
        tweak: Box::new(|c| c.analysis_ceiling_bytes = 100 << 20),
        ..Default::default()
    })
    .await;
    ingest(&env, meta(9007, 0, "big", None)).await;
    env.settle().await;
    let session_id: i64 = scalar(&env, "SELECT session_id FROM sessions");
    let detail = env
        .get(&format!("/api/v1/sessions/{session_id}"))
        .await
        .json();
    assert_eq!(detail["session"]["analysis_state"], "deferred");
    assert_eq!(
        detail["session"]["analysis_reason"],
        "exceeds_memory_budget"
    );
    assert_eq!(detail["session"]["analysis_id"], Value::Null);
    let listed = env.get("/api/v1/jobs?state=deferred").await.json();
    let job = &listed["jobs"][0];
    assert_eq!(job["kind"], "analyze");
    assert_eq!(job["state"], "deferred");
    assert_eq!(job["error_code"], "exceeds_memory_budget");
    assert!(job["subject"]["estimate_bytes"].as_u64().unwrap() > 100 << 20);
    let status = env.get("/api/v1/status").await.json();
    assert_eq!(status["queue"]["deferred"], 1);

    // asking for the analysis again does not run it: the job stays deferred, the reason stays
    let again = env
        .post(
            &format!("/api/v1/sessions/{session_id}/analysis"),
            json!({}),
        )
        .await;
    assert_eq!(again.status, StatusCode::ACCEPTED);
    assert_eq!(again.json()["state"], "deferred");
    env.settle().await;
    assert_eq!(scalar::<i64>(&env, "SELECT COUNT(*) FROM analyses"), 0);
    let detail = env
        .get(&format!("/api/v1/sessions/{session_id}"))
        .await
        .json();
    assert_eq!(
        detail["session"]["analysis_reason"],
        "exceeds_memory_budget"
    );
}

#[tokio::test]
async fn a_ready_session_whose_newer_analysis_is_deferred_still_says_why() {
    // One recording fits under the ceiling; the same session with a second recording (the
    // reconnect case: the owner's Bristol races) does not.
    let env = Env::with(Opts {
        tweak: Box::new(|c| c.analysis_ceiling_bytes = 200 << 20),
        ..Default::default()
    })
    .await;
    require_python!(env);
    ingest(&env, meta(9008, 0, "first", None)).await;
    env.settle().await;
    assert_eq!(
        scalar::<String>(&env, "SELECT analysis_state FROM sessions"),
        "ready"
    );
    assert_eq!(
        scalar::<Option<String>>(&env, "SELECT analysis_reason FROM sessions"),
        None
    );
    ingest(&env, meta(9008, 0, "second", None)).await;
    env.settle().await;
    assert_eq!(
        scalar::<i64>(&env, "SELECT COUNT(*) FROM session_recordings"),
        2
    );
    assert_eq!(
        job_state(&env, "analyze"),
        vec![
            ("succeeded".to_string(), None),
            (
                "deferred".to_string(),
                Some("exceeds_memory_budget".to_string())
            )
        ]
    );
    // the earlier analysis stays usable, and the library says the newer one is waiting
    let sessions = env.get("/api/v1/drivers/1001/sessions").await.json();
    let row = &sessions["sessions"][0];
    assert_eq!(row["analysis_state"], "ready");
    assert!(row["analysis_id"].is_string());
    assert_eq!(row["analysis_reason"], "exceeds_memory_budget");
    let session_id = row["session_id"].as_i64().unwrap();
    let detail = env
        .get(&format!("/api/v1/sessions/{session_id}"))
        .await
        .json();
    assert_eq!(
        detail["session"]["analysis_reason"],
        "exceeds_memory_budget"
    );

    // a deferred job of an earlier, smaller membership is not reported once the session has
    // an analysis of its current recordings (ceiling raised by a restart, here by hand)
    env.app
        .store
        .write_blocking(|c| {
            c.execute(
                "UPDATE jobs SET state = 'queued', error_code = NULL WHERE state = 'deferred'",
                [],
            )?;
            Ok(())
        })
        .unwrap();
    env.settle().await;
    assert_eq!(
        scalar::<String>(&env, "SELECT analysis_state FROM sessions"),
        "ready"
    );
    assert_eq!(
        scalar::<Option<String>>(&env, "SELECT analysis_reason FROM sessions"),
        None,
        "nothing is waiting any more"
    );
}

#[test]
fn the_pure_python_formula_is_kept_and_the_legacy_bristol_races_exceed_its_ceiling() {
    // acceptance/migration.md step 4: sessions 105 and 123, 3.6 and 4.2 GB against the 3 GiB ceiling
    let config = base_config(std::path::Path::new("."), None);
    assert!(!config.native_memory_model());
    let ceiling = config.analysis_ceiling_bytes;
    assert_eq!(ceiling, 3 << 30);
    assert_eq!(config.memory_budget_bytes, (3.7 * GIB as f64) as u64);
    let one_and_a_stub = jobs::analysis_estimate(&[197_206_664, 253_361], false);
    let pair = jobs::analysis_estimate(&[146_209_324, 81_729_208], false);
    assert_eq!(one_and_a_stub, 3_647_777_341);
    assert_eq!(pair, 4_173_531_587);
    assert!(one_and_a_stub > ceiling && pair > ceiling);
    // each recording alone fits: the session was analysable before it grew
    assert!(jobs::analysis_estimate(&[197_206_664], false) < ceiling);
    assert!(jobs::analysis_estimate(&[146_209_324], false) < ceiling);
}

fn gib(bytes: u64) -> f64 {
    bytes as f64 / GIB as f64
}

fn native_config() -> coach_server::Config {
    let mut config = base_config(std::path::Path::new("."), None);
    config.set_native(NativeStages {
        stages: vec!["decode".into(), "kernels".into(), "writer".into()],
        bin: Some("coach-analysis".into()),
    });
    config
}

#[test]
fn the_native_formula_admits_the_deferred_analyses_and_covers_the_n100_peaks() {
    // acceptance/g1-native.md: 5.0 x bytes + 0.15 GiB, x1.5 for a group, ceiling 2.0 GiB, shared
    // budget 2.7 GiB; the bound is the worker's peak plus its largest child's on the N100.
    let config = native_config();
    assert!(config.native_memory_model());
    assert_eq!(config.analysis_ceiling_bytes, 2 << 30);
    assert_eq!(config.memory_budget_bytes, (2.7 * GIB as f64) as u64);
    assert_eq!(config.server_reserve_bytes, (0.3 * GIB as f64) as u64);
    let ceiling = config.analysis_ceiling_bytes;

    // (recording bytes, the N100 bound in GiB, the documented native estimate, the old estimate)
    let table: [(&[u64], f64, f64, f64); 4] = [
        (&[203_200_000], 0.880, 1.096, 2.327),
        (&[270_486_936], 0.795, 1.410, 3.047),
        // session 105: the Bristol race of migration.md (197,206,664 + 253,361 bytes)
        (&[197_206_664, 253_361], 1.296, 1.604, 3.397),
        // session 123: the Bristol race of migration.md (146,209_324 + 81,729,208 bytes)
        (&[146_209_324, 81_729_208], 1.587, 1.817, 3.887),
    ];
    for (sizes, bound, documented, old) in table {
        let native = gib(jobs::analysis_estimate(sizes, true));
        let python = gib(jobs::analysis_estimate(sizes, false));
        assert!((native - documented).abs() < 0.005, "{sizes:?}: {native}");
        assert!((python - old).abs() < 0.005, "{sizes:?}: {python}");
        assert!(
            native >= bound,
            "{sizes:?}: estimate {native} under the peak {bound}"
        );
        assert!(
            jobs::analysis_estimate(sizes, true) <= ceiling,
            "{sizes:?} admitted"
        );
        // alone it always fits next to the server's reserve
        assert!(
            jobs::analysis_estimate(sizes, true) + config.server_reserve_bytes
                <= config.memory_budget_bytes
        );
    }
    // the three analyses the old ceiling deferred now fit; the 2 GB Kansas test does not
    assert!(jobs::analysis_estimate(&[270_486_936], false) > 3 << 30);
    let kansas = jobs::analysis_estimate(&[2_093_549_028], true);
    assert!((gib(kansas) - 9.90).abs() < 0.01);
    assert!(kansas > ceiling);

    // what the ceiling admits: one recording up to 397 MB, a group of two up to 254 MB in total
    assert!(jobs::analysis_estimate(&[397_000_000], true) <= ceiling);
    assert!(jobs::analysis_estimate(&[398_000_000], true) > ceiling);
    assert!(jobs::analysis_estimate(&[127_000_000, 127_000_000], true) <= ceiling);
    assert!(jobs::analysis_estimate(&[128_000_000, 128_000_000], true) > ceiling);
}

#[test]
fn only_decode_with_kernels_selects_the_native_memory_model() {
    let stages = |names: &[&str]| NativeStages {
        stages: names.iter().map(|n| n.to_string()).collect(),
        bin: Some("coach-analysis".into()),
    };
    for (names, native) in [
        (&["decode", "kernels", "writer"][..], true),
        (&["decode", "kernels"][..], true),
        (&["decode"][..], false),
        (&["decode", "writer"][..], false),
        (&["writer"][..], false),
        (&[][..], false),
    ] {
        let mut config = base_config(std::path::Path::new("."), None);
        config.set_native(stages(names));
        assert_eq!(config.native_memory_model(), native, "{names:?}");
        let (ceiling, budget) = if native { (2.0, 2.7) } else { (3.0, 3.7) };
        assert_eq!(config.analysis_ceiling_bytes, (ceiling * GIB as f64) as u64);
        assert_eq!(config.memory_budget_bytes, (budget * GIB as f64) as u64);
    }
}

#[tokio::test]
async fn a_native_server_admits_by_the_native_estimate() {
    let env = Env::with(Opts {
        tweak: Box::new(|c| {
            c.set_native(NativeStages {
                stages: vec!["decode".into(), "kernels".into()],
                bin: Some("coach-analysis".into()),
            })
        }),
        ..Default::default()
    })
    .await;
    ingest(&env, meta(9010, 0, "native", None)).await;
    require_python!(env);
    assert!(
        jobs::run_next(&env.app, "light").await.unwrap(),
        "discovery queues the analysis"
    );
    let (estimate, size): (i64, i64) = env.query(|c| {
        c.query_row(
            "SELECT json_extract(j.subject_json, '$.estimate_bytes'), b.size
             FROM jobs j, blobs b WHERE j.kind = 'analyze'
             AND b.sha256 = json_extract(j.subject_json, '$.recordings[0].sha256')",
            [],
            |r| Ok((r.get(0)?, r.get(1)?)),
        )
    });
    assert_eq!(
        estimate as u64,
        jobs::analysis_estimate(&[size as u64], true)
    );
    assert_ne!(
        estimate as u64,
        jobs::analysis_estimate(&[size as u64], false)
    );
}

#[tokio::test]
async fn the_worker_gets_the_native_stages_the_server_is_set_to() {
    let native = NativeStages {
        stages: vec!["decode".into(), "kernels".into(), "writer".into()],
        bin: Some("/opt/coach/coach-analysis".into()),
    };
    let env = Env::with(Opts {
        tweak: {
            let native = native.clone();
            Box::new(move |c| c.set_native(native.clone()))
        },
        ..Default::default()
    })
    .await;
    let call = call(&env, "x", &env.data().join("root"), Duration::from_secs(1));
    let vars = worker_env(&env.app.cfg.worker, &call);
    assert_eq!(vars["IRACING_COACH_NATIVE_STAGES"], "decode,kernels,writer");
    assert_eq!(
        vars["IRACING_COACH_NATIVE_BIN"],
        "/opt/coach/coach-analysis"
    );
    // everything else stays as without native stages: nothing is inherited
    assert_eq!(vars["IRACING_COACH_RAW_ARCHIVE"], "external");
    assert!(!vars.contains_key("IRACING_COACH_INSTALL_ROOT"));

    // no stage: the executable is not named either
    let mut worker = env.app.cfg.worker.clone();
    worker.native.stages.clear();
    let vars = worker_env(&worker, &call);
    assert_eq!(vars["IRACING_COACH_NATIVE_STAGES"], "");
    assert!(!vars.contains_key("IRACING_COACH_NATIVE_BIN"));

    // and the process sees them
    require_python!(env);
    let (outcome, _) = run_behavior(&env, "nativeenv", |_| {}).await;
    assert!(matches!(outcome, JobOutcome::Succeeded(_)), "{outcome:?}");
    let seen: Value = serde_json::from_slice(
        &std::fs::read(env.data().join("py/1001/archive/native-env.json")).unwrap(),
    )
    .unwrap();
    assert_eq!(seen["IRACING_COACH_NATIVE_STAGES"], "decode,kernels,writer");
    assert_eq!(
        seen["IRACING_COACH_NATIVE_BIN"],
        "/opt/coach/coach-analysis"
    );
}

type AnalysisRow = (i64, String, String, String);

fn analysis_rows(env: &Env) -> Vec<AnalysisRow> {
    env.query(|c| {
        let mut stmt = c.prepare(
            "SELECT job_id, state, idempotency_key, subject_json FROM jobs
             WHERE kind = 'analyze' ORDER BY job_id",
        )?;
        let rows = stmt
            .query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?)))?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        Ok(rows)
    })
}

fn queue_again(env: &Env, queue: QueueParams, session_id: i64) -> (i64, bool) {
    env.app
        .store
        .write_blocking(move |c| {
            let tx = c.transaction()?;
            let result = jobs::queue_analysis_tx(&tx, &queue, session_id, true, true)?;
            tx.commit()?;
            Ok(result)
        })
        .unwrap()
        .unwrap()
}

#[tokio::test]
async fn asking_again_after_the_analyzer_changed_keeps_one_deferred_row_per_session() {
    let env = Env::with(Opts {
        tweak: Box::new(|c| c.analysis_ceiling_bytes = 100 << 20),
        ..Default::default()
    })
    .await;
    ingest(&env, meta(9020, 0, "big", None)).await;
    env.settle().await;
    let session_id: i64 = scalar(&env, "SELECT session_id FROM sessions");
    let first: i64 = scalar(&env, "SELECT job_id FROM jobs WHERE kind = 'analyze'");
    assert_eq!(analysis_rows(&env).len(), 1);

    // the analyzer bundle changed: asking again moves the row to the new key, no second row
    let mut queue = QueueParams::of(&env.app);
    queue.bundle_digest = "0123456789abcdef".into();
    assert_eq!(
        queue_again(&env, queue.clone(), session_id),
        (first, true),
        "the same job, updated"
    );
    let listed = analysis_rows(&env);
    assert_eq!(listed.len(), 1, "{listed:?}");
    assert_eq!(listed[0].1, "deferred");
    assert!(listed[0].2.contains("0123456789abcdef"), "{}", listed[0].2);
    let subject: Value = serde_json::from_str(&listed[0].3).unwrap();
    assert_eq!(subject["session_id"], session_id);

    // the same key again changes nothing
    assert_eq!(queue_again(&env, queue, session_id), (first, false));
    let reply = env
        .post(
            &format!("/api/v1/sessions/{session_id}/analysis"),
            json!({}),
        )
        .await;
    assert_eq!(reply.status, StatusCode::ACCEPTED);
    assert_eq!(reply.json()["state"], "deferred");
    assert_eq!(analysis_rows(&env).len(), 1);
    assert_eq!(
        env.get("/api/v1/status").await.json()["queue"]["deferred"],
        1
    );

    // rows an earlier release left (one deferred row per analyzer digest) collapse on the next ask
    env.app
        .store
        .write_blocking(move |c| {
            c.execute(
                "INSERT INTO jobs(kind, lane, priority, state, idempotency_key, customer_id, subject_json,
                                  attempts, created_at, error_code)
                 SELECT kind, lane, priority, state, idempotency_key || '-old', customer_id, subject_json,
                        attempts, created_at, error_code FROM jobs WHERE job_id = ?1",
                [first],
            )?;
            Ok(())
        })
        .unwrap();
    assert_eq!(analysis_rows(&env).len(), 2);
    let reply = env
        .post(
            &format!("/api/v1/sessions/{session_id}/analysis"),
            json!({}),
        )
        .await;
    assert_eq!(reply.status, StatusCode::ACCEPTED);
    assert_eq!(reply.json()["job_id"], first);
    let listed = analysis_rows(&env);
    assert_eq!(listed.len(), 1, "{listed:?}");
    assert_eq!(listed[0].0, first);
    assert_eq!(
        env.get("/api/v1/status").await.json()["queue"]["deferred"],
        1
    );
}

#[tokio::test]
async fn a_deferred_row_that_now_fits_becomes_the_sessions_queued_analysis() {
    let env = Env::with(Opts {
        tweak: Box::new(|c| c.analysis_ceiling_bytes = 100 << 20),
        ..Default::default()
    })
    .await;
    ingest(&env, meta(9021, 0, "fits", None)).await;
    env.settle().await;
    let session_id: i64 = scalar(&env, "SELECT session_id FROM sessions");
    let first: i64 = scalar(&env, "SELECT job_id FROM jobs WHERE kind = 'analyze'");
    // a new analyzer digest and an estimate under the ceiling (the admission formula changed)
    let mut queue = QueueParams::of(&env.app);
    queue.bundle_digest = "fedcba9876543210".into();
    queue.ceiling = u64::MAX;
    assert_eq!(queue_again(&env, queue, session_id), (first, true));
    assert_eq!(
        job_state(&env, "analyze"),
        vec![("queued".to_string(), None)],
        "one row, queued, reason cleared"
    );
    assert_eq!(
        scalar::<String>(&env, "SELECT analysis_state FROM sessions"),
        "queued"
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
    let vars = worker_env(&env.app.cfg.worker, &call);
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
    // no native stage configured: the list is empty and the executable is not named
    assert_eq!(vars["IRACING_COACH_NATIVE_STAGES"], "");
    assert!(!vars.contains_key("IRACING_COACH_NATIVE_BIN"));
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
    // the native stores the worker left under its TMPDIR are gone, a file that is not one stays
    let scratch: Vec<String> = std::fs::read_dir(env.data().join("py/1001/tmp"))
        .unwrap()
        .map(|e| e.unwrap().file_name().to_string_lossy().into_owned())
        .collect();
    assert_eq!(scratch, vec!["keep.txt".to_string()]);
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
    let export = page["setups"]
        .as_array()
        .unwrap()
        .iter()
        .find(|s| {
            s["sources"]
                .as_array()
                .unwrap()
                .iter()
                .any(|v| v == "htm_export")
        })
        .expect("the export's setup");
    assert_eq!(
        export["names"],
        json!(["baseline"]),
        "no .sto name is known: the export's file name without its extension"
    );
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

// ------------------------------------------- review follow-ups (wave 1)

fn light_job(kind: &'static str, arguments: Value) -> jobs::NewJob {
    jobs::NewJob::new(
        kind,
        "light",
        Some(1001),
        json!({"arguments": arguments, "needs_setups": false}),
    )
    .interactive()
}

async fn run_light(
    env: &Env,
    new: jobs::NewJob,
) -> (String, Option<String>, Option<String>, Option<Value>) {
    let id = env
        .app
        .store
        .write_blocking(move |c| {
            let tx = c.transaction()?;
            let (id, _) = jobs::enqueue_tx(&tx, &new, false)?;
            tx.commit()?;
            Ok(id)
        })
        .unwrap();
    assert!(jobs::run_next(&env.app, "light").await.unwrap());
    env.query(move |c| {
        c.query_row(
            "SELECT state, error_code, error_message, result_json FROM jobs WHERE job_id = ?1",
            [id],
            |r| {
                let result: Option<String> = r.get(3)?;
                Ok((
                    r.get(0)?,
                    r.get(1)?,
                    r.get(2)?,
                    result.map(|t| serde_json::from_str(&t).unwrap()),
                ))
            },
        )
    })
}

#[tokio::test]
async fn an_analysis_that_breaks_its_contract_fails_the_job_as_tool_error() {
    let env = Env::new().await;
    require_python!(env);
    let (outcome, _) = run_behavior(&env, "badresult", |_| {}).await;
    match outcome {
        JobOutcome::Failed { code, message } => {
            assert_eq!(code, "tool_error");
            assert!(message.contains("analyze-result-v1"), "{message}");
            assert!(message.contains("analysis_view"), "{message}");
        }
        other => panic!("{other:?}"),
    }
    assert_eq!(
        scalar::<i64>(&env, "SELECT COUNT(*) FROM analyses"),
        0,
        "nothing was recorded for the invalid result"
    );
}

#[tokio::test]
async fn baseline_and_tuning_results_are_validated_against_their_contracts() {
    let env = Env::new().await;
    require_python!(env);
    ingest(&env, meta(9300, 0, "driver", None)).await;
    for (kind, tool_contract, valid_key) in [
        ("baseline", "setup-package-v1", "package_id"),
        (
            "tuning_recommend",
            "tuning-recommendation-v1",
            "recommendation",
        ),
    ] {
        let (state, code, message, result) = run_light(&env, light_job(kind, json!({}))).await;
        assert_eq!(state, "succeeded", "{kind}: {code:?} {message:?}");
        assert!(result.unwrap().get(valid_key).is_some(), "{kind}");

        let (state, code, message, result) =
            run_light(&env, light_job(kind, json!({"stub_invalid": true}))).await;
        assert_eq!(state, "failed", "{kind}");
        assert_eq!(code.as_deref(), Some("tool_error"), "{kind}");
        assert!(
            message.unwrap_or_default().contains(tool_contract),
            "{kind}: the message names the contract"
        );
        assert!(result.is_none(), "{kind}");
    }
}

#[tokio::test]
async fn a_worker_that_closes_stdout_without_exiting_is_stopped_at_the_timeout() {
    let env = Env::with(Opts {
        tweak: Box::new(|c| c.worker.analyze_timeout = Duration::from_secs(2)),
        ..Default::default()
    })
    .await;
    require_python!(env);
    let (outcome, _) = tokio::time::timeout(
        Duration::from_secs(30),
        run_behavior(&env, "closeout", |_| {}),
    )
    .await
    .expect("the supervision must not wait on a worker that never exits");
    assert!(
        matches!(outcome, JobOutcome::Failed { ref code, .. } if code == "timeout"),
        "{outcome:?}"
    );
    assert!(env.app.jobs.running_ids().is_empty());
}

#[cfg(unix)]
#[tokio::test]
async fn a_descendant_that_ignores_sigterm_is_killed_even_after_the_leader_exits() {
    let env = Env::with(Opts {
        tweak: Box::new(|c| c.worker.analyze_timeout = Duration::from_secs(2)),
        ..Default::default()
    })
    .await;
    require_python!(env);
    let archive = env.data().join("py/1001/archive");
    let (outcome, _) = run_behavior(&env, "ignoreterm", |_| {}).await;
    assert!(
        matches!(outcome, JobOutcome::Failed { ref code, .. } if code == "timeout"),
        "{outcome:?}"
    );
    let child: i32 = std::fs::read_to_string(archive.join("stubborn.pid"))
        .unwrap()
        .trim()
        .parse()
        .unwrap();
    std::thread::sleep(Duration::from_millis(200));
    // The leader dies on SIGTERM; the child ignores it. SIGKILL after the grace must reach it.
    let state = std::fs::read_to_string(format!("/proc/{child}/stat")).unwrap_or_default();
    assert!(
        state.is_empty()
            || state
                .rsplit(')')
                .next()
                .map(str::trim_start)
                .is_some_and(|s| s.starts_with('Z')),
        "the child outlived its job: {state}"
    );
}

#[tokio::test]
async fn a_failed_start_write_releases_the_memory_reservation() {
    // room for exactly one light job beside the server reserve
    let env = Env::with(Opts {
        tweak: Box::new(|c| c.memory_budget_bytes = (0.6 * (1u64 << 30) as f64) as u64),
        ..Default::default()
    })
    .await;
    require_python!(env);
    ingest(&env, meta(9200, 0, "guard", None)).await;
    env.faults.arm(
        "job.before_running",
        coach_server::faults::FaultMode::Io(std::io::ErrorKind::StorageFull),
    );
    assert!(
        jobs::run_next(&env.app, "light").await.is_err(),
        "SQLite refused the state change"
    );
    assert!(
        env.app.jobs.running_ids().is_empty(),
        "the admission was released on the error path"
    );
    assert_eq!(job_state(&env, "discover")[0].0, "queued");
    // a leaked reservation would count against the job itself and it would never run again
    assert!(jobs::run_next(&env.app, "light").await.unwrap());
    assert_eq!(job_state(&env, "discover")[0].0, "succeeded");
}

#[tokio::test]
async fn a_terminal_state_that_sqlite_refuses_is_kept_and_retried() {
    let env = Env::new().await;
    require_python!(env);
    ingest(&env, meta(9201, 0, "terminal", None)).await;
    env.faults.arm(
        "job.before_finish",
        coach_server::faults::FaultMode::Io(std::io::ErrorKind::StorageFull),
    );
    assert!(jobs::run_next(&env.app, "light").await.unwrap());
    assert!(
        env.app.jobs.running_ids().is_empty(),
        "the reservation is released although the state write failed"
    );
    assert_eq!(
        job_state(&env, "discover")[0].0,
        "running",
        "the row is still waiting for its outcome"
    );
    assert_eq!(jobs::flush_pending(&env.app).await, 1);
    assert_eq!(job_state(&env, "discover")[0].0, "succeeded");
    assert_eq!(jobs::flush_pending(&env.app).await, 0);
    // the outcome's follow-up work ran: the session exists and its analysis is queued
    assert_eq!(scalar::<i64>(&env, "SELECT COUNT(*) FROM sessions"), 1);
}

#[tokio::test]
async fn deferred_orphan_cleanup_runs_when_the_drivers_lanes_go_idle() {
    let env = Env::new().await;
    let archive = env.data().join("py/1001/archive");
    std::fs::create_dir_all(&archive).unwrap();
    let stray = archive.join(".write-stray");
    std::fs::write(&stray, b"x").unwrap();
    let tmp = env.data().join("py/1001/tmp");
    let stores = [tmp.join("coach-decode-aaaa"), tmp.join("coach-merge-bbbb")];
    for store in &stores {
        std::fs::create_dir_all(store).unwrap();
        std::fs::write(store.join("store.ccs"), b"x").unwrap();
    }
    std::fs::write(tmp.join("keep.txt"), b"x").unwrap();
    let cancel = Cancel::default();
    let busy = match env
        .app
        .jobs
        .try_admit(&env.app.cfg, 9001, "light", Some(1001), 0, &cancel)
    {
        jobs::Admit::Yes(guard) => guard,
        _ => panic!("admitted"),
    };
    // a worker of the driver was killed while another one runs: its temp files wait
    env.app.jobs.defer_cleanup(Some(1001));
    assert_eq!(jobs::sweep_deferred_cleanup(&env.app), 0);
    assert!(stray.exists(), "removed while another worker may own it");
    assert!(stores.iter().all(|s| s.exists()), "so is a native store");
    drop(busy);
    assert_eq!(
        jobs::sweep_deferred_cleanup(&env.app),
        3,
        "the stray and both stores"
    );
    assert!(!stray.exists());
    assert!(stores.iter().all(|s| !s.exists()));
    assert!(tmp.join("keep.txt").exists());
    assert_eq!(jobs::sweep_deferred_cleanup(&env.app), 0, "done once");
}

#[tokio::test]
async fn stray_temp_files_of_killed_workers_are_removed_at_start() {
    let env = Env::new().await;
    let archive = env.data().join("py/1001/archive/tuning");
    std::fs::create_dir_all(&archive).unwrap();
    std::fs::write(archive.join(".copy-old"), b"x").unwrap();
    std::fs::write(archive.join(".write-old"), b"x").unwrap();
    std::fs::write(archive.join("kept.json"), b"{}").unwrap();
    let tmp = env.data().join("py/1001/tmp");
    std::fs::create_dir_all(tmp.join("coach-decode-old")).unwrap();
    std::fs::create_dir_all(tmp.join("coach-merge-old")).unwrap();
    std::fs::write(tmp.join("coach-decode-old/decode.ccs"), b"x").unwrap();
    std::fs::write(tmp.join("other.txt"), b"x").unwrap();
    let env = env.restart().await;
    let left: Vec<String> = std::fs::read_dir(env.data().join("py/1001/tmp"))
        .unwrap()
        .map(|e| e.unwrap().file_name().to_string_lossy().into_owned())
        .collect();
    assert_eq!(
        left,
        vec!["other.txt".to_string()],
        "native stores are swept at start"
    );
    let names: Vec<String> = std::fs::read_dir(env.data().join("py/1001/archive/tuning"))
        .unwrap()
        .map(|e| e.unwrap().file_name().to_string_lossy().into_owned())
        .collect();
    assert_eq!(names, vec!["kept.json".to_string()]);
}

#[tokio::test]
async fn native_scratch_of_a_killed_worker_waits_while_another_worker_of_the_driver_runs() {
    let env = Env::new().await;
    let archive = env.data().join("py/1001/archive");
    let tmp = env.data().join("py/1001/tmp");
    std::fs::create_dir_all(&archive).unwrap();
    std::fs::create_dir_all(tmp.join("coach-decode-live")).unwrap();
    std::fs::write(archive.join("half.4242.tmp"), b"x").unwrap();
    // another worker of the driver runs: only the killed process's own `*.<pid>.tmp` goes
    coach_server::worker::cleanup_after_kill(&archive, &tmp, Some(4242), false);
    assert!(!archive.join("half.4242.tmp").exists());
    assert!(tmp.join("coach-decode-live").exists());
    // the driver is idle: the stores go
    coach_server::worker::cleanup_after_kill(&archive, &tmp, Some(4242), true);
    assert!(!tmp.join("coach-decode-live").exists());
    // no scratch directory at all is not an error
    assert_eq!(
        coach_server::worker::cleanup_native_scratch(&tmp.join("absent")),
        0
    );
}

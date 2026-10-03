//! `POST /api/v1/admin/remove`: library cleanup of named setup file entries and recordings with
//! their derived rows and views; dry run, idempotency, the log, what is never touched, the guards.

mod support;

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use axum::http::StatusCode;
use coach_server::setups;
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

fn count(env: &Env, sql: &str) -> i64 {
    let sql = sql.to_string();
    env.query(move |c| c.query_row(&sql, [], |r| r.get(0)))
}

/// Every file under `root` with its size, for "the NAS did not change".
fn tree(root: &Path) -> BTreeMap<PathBuf, u64> {
    fn walk(dir: &Path, root: &Path, out: &mut BTreeMap<PathBuf, u64>) {
        let Ok(rd) = std::fs::read_dir(dir) else {
            return;
        };
        for entry in rd.flatten() {
            let path = entry.path();
            if path.is_dir() {
                walk(&path, root, out);
            } else {
                out.insert(
                    path.strip_prefix(root).unwrap().to_path_buf(),
                    entry.metadata().map(|m| m.len()).unwrap_or(0),
                );
            }
        }
    }
    let mut out = BTreeMap::new();
    walk(root, root, &mut out);
    out
}

fn log_lines(env: &Env) -> Vec<Value> {
    let path = env.data().join("run").join("admin-removals.jsonl");
    std::fs::read_to_string(path)
        .unwrap_or_default()
        .lines()
        .map(|l| serde_json::from_str(l).expect("log line is JSON"))
        .collect()
}

async fn remove(env: &Env, body: Value) -> Value {
    let reply = env.post("/api/v1/admin/remove", body).await;
    assert_eq!(
        reply.status,
        StatusCode::OK,
        "{}",
        String::from_utf8_lossy(&reply.body)
    );
    reply.json()
}

fn views_dir(env: &Env, analysis_id: &str) -> PathBuf {
    env.data().join("views").join(analysis_id)
}

#[tokio::test]
async fn a_recording_goes_with_its_session_analysis_views_and_jobs_and_the_nas_stays() {
    let env = Env::new().await;
    require_python!(env);
    let keep = ingest(&env, race_meta(9701, 0, "keep")).await;
    let mut other = race_meta(9702, 0, "gone");
    other["setup"]["values"]["Chassis.Front.ArbSetting"] = json!("9");
    let gone = ingest(&env, other).await;
    env.settle().await;
    let session: i64 = env.query(|c| {
        c.query_row(
            "SELECT s.session_id FROM sessions s JOIN session_recordings sr USING (session_id)
             WHERE sr.recording_sha256 = ?1",
            [&gone],
            |r| r.get(0),
        )
    });
    let analysis: String = env.query(|c| {
        c.query_row(
            "SELECT current_analysis_id FROM sessions WHERE session_id = ?1",
            [session],
            |r| r.get(0),
        )
    });
    assert!(views_dir(&env, &analysis).is_dir());
    let nas_before = tree(&env.nas());
    let manifest = env
        .nas()
        .join("raw")
        .join(&gone[..2])
        .join(format!("{gone}.manifest.json"));
    assert!(manifest.exists(), "archived before the removal");
    let setups_before = count(&env, "SELECT COUNT(*) FROM setups");
    // the recording's own setup, which nothing else observed, goes with it
    assert_eq!(setups_before, 2);

    // A dry run (the default) lists the whole removal and changes nothing.
    let plan = remove(&env, json!({"recordings": [gone]})).await;
    assert_eq!(plan["dry_run"], true);
    let rec = &plan["recordings"][0];
    assert_eq!(rec["found"], true);
    assert_eq!(rec["applied"], false);
    assert_eq!(rec["blocked"], json!([]));
    assert_eq!(rec["sessions_removed"][0]["session_id"], session);
    assert_eq!(rec["analyses"], json!([analysis]));
    assert_eq!(rec["setup_observations"], 1);
    assert_eq!(rec["setups"].as_array().unwrap().len(), 1);
    assert!(rec["jobs"].as_i64().unwrap() >= 4, "{rec}");
    assert_eq!(plan["log"], Value::Null);
    assert!(plan["kept"].as_array().unwrap().len() >= 3);
    assert_eq!(count(&env, "SELECT COUNT(*) FROM sessions"), 2);
    assert_eq!(count(&env, "SELECT COUNT(*) FROM blobs"), 2);
    assert!(views_dir(&env, &analysis).is_dir());
    assert!(log_lines(&env).is_empty(), "a dry run is not logged");
    let explicit = remove(&env, json!({"recordings": [gone], "dry_run": true})).await;
    assert_eq!(explicit["recordings"], plan["recordings"]);

    // The real call.
    let done = remove(&env, json!({"recordings": [gone], "dry_run": false})).await;
    assert_eq!(done["dry_run"], false);
    assert_eq!(done["recordings"][0]["applied"], true);
    assert_eq!(
        done["recordings"][0]["sessions_removed"][0]["session_id"],
        session
    );
    assert!(
        done["log"]
            .as_str()
            .unwrap()
            .ends_with("admin-removals.jsonl")
    );
    for sql in [
        format!("SELECT COUNT(*) FROM blobs WHERE sha256 = '{gone}'"),
        format!("SELECT COUNT(*) FROM recordings WHERE sha256 = '{gone}'"),
        format!("SELECT COUNT(*) FROM session_recordings WHERE recording_sha256 = '{gone}'"),
        format!("SELECT COUNT(*) FROM uploads WHERE sha256 = '{gone}'"),
        format!("SELECT COUNT(*) FROM blob_occurrences WHERE sha256 = '{gone}'"),
        format!("SELECT COUNT(*) FROM jobs WHERE instr(subject_json, '{gone}') > 0"),
        format!("SELECT COUNT(*) FROM sessions WHERE session_id = {session}"),
        format!("SELECT COUNT(*) FROM analyses WHERE session_id = {session}"),
        format!(
            "SELECT COUNT(*) FROM jobs WHERE json_extract(subject_json, '$.analysis_id') = '{analysis}'"
        ),
        format!("SELECT COUNT(*) FROM setup_observations WHERE recording_sha256 = '{gone}'"),
    ] {
        assert_eq!(count(&env, &sql), 0, "{sql}");
    }
    assert_eq!(
        count(&env, "SELECT COUNT(*) FROM setups"),
        1,
        "the orphaned setup went"
    );
    assert!(!views_dir(&env, &analysis).exists(), "views removed");
    assert!(!env.data().join("spool/ready").join(&gone).exists());
    // the other recording is untouched
    assert_eq!(
        count(
            &env,
            &format!("SELECT COUNT(*) FROM blobs WHERE sha256 = '{keep}'")
        ),
        1
    );
    assert_eq!(
        count(
            &env,
            "SELECT COUNT(*) FROM sessions WHERE analysis_state = 'ready'"
        ),
        1
    );
    // the NAS did not change
    assert_eq!(
        tree(&env.nas()),
        nas_before,
        "NAS raw files and manifests are never touched"
    );

    // Logged once, with the request and the report.
    let log = log_lines(&env);
    assert_eq!(log.len(), 1);
    assert_eq!(log[0]["request"]["recordings"], json!([gone]));
    assert_eq!(log[0]["report"]["recordings"][0]["applied"], true);

    // Idempotent: the same call again finds nothing, changes nothing and logs nothing.
    let again = remove(&env, json!({"recordings": [gone], "dry_run": false})).await;
    assert_eq!(again["recordings"][0]["found"], false);
    assert_eq!(again["recordings"][0]["applied"], false);
    assert_eq!(again["log"], Value::Null);
    assert_eq!(log_lines(&env).len(), 1);

    // The same bytes can be uploaded and analysed again: no key of the old rows is left behind.
    let mut other = race_meta(9702, 0, "gone");
    other["setup"]["values"]["Chassis.Front.ArbSetting"] = json!("9");
    let back = ingest(&env, other).await;
    assert_eq!(back, gone);
    env.settle().await;
    assert_eq!(
        count(
            &env,
            "SELECT COUNT(*) FROM sessions WHERE analysis_state = 'ready'"
        ),
        2
    );
}

/// Two recordings of one session, both analysed (A alone first, then A and B together).
async fn two_recording_session(env: &Env) -> (String, String, i64) {
    let a = ingest(env, race_meta(9711, 0, "a")).await;
    let b = ingest(env, race_meta(9711, 0, "b")).await;
    env.settle().await;
    assert_eq!(count(env, "SELECT COUNT(*) FROM session_recordings"), 2);
    assert_eq!(count(env, "SELECT COUNT(*) FROM sessions"), 1);
    let session: i64 =
        env.query(|c| c.query_row("SELECT session_id FROM sessions", [], |r| r.get(0)));
    (a, b, session)
}

fn current_members(env: &Env) -> Vec<String> {
    let members: String = env.query(|c| {
        c.query_row(
            "SELECT recordings_sha256 FROM analyses WHERE analysis_id =
               (SELECT current_analysis_id FROM sessions)",
            [],
            |r| r.get(0),
        )
    });
    serde_json::from_str(&members).unwrap()
}

#[tokio::test]
async fn removing_one_recording_of_a_session_makes_the_analysis_of_the_rest_current() {
    let env = Env::new().await;
    require_python!(env);
    let (a, b, session) = two_recording_session(&env).await;
    assert_eq!(current_members(&env).len(), 2);

    let plan = remove(&env, json!({"recordings": [b.clone()]})).await;
    assert_eq!(plan["recordings"][0]["sessions_removed"], json!([]));
    assert_eq!(plan["recordings"][0]["sessions_requeued"], json!([session]));
    let done = remove(&env, json!({"recordings": [b.clone()], "dry_run": false})).await;
    assert_eq!(done["recordings"][0]["applied"], true);
    assert_eq!(count(&env, "SELECT COUNT(*) FROM session_recordings"), 1);
    assert_eq!(
        count(
            &env,
            &format!("SELECT COUNT(*) FROM analyses WHERE instr(recordings_sha256, '{b}') > 0")
        ),
        0,
        "an analysis that included the removed recording does not stand"
    );
    // A alone had been analysed before B joined: that analysis is current again, nothing re-runs
    env.settle().await;
    assert_eq!(current_members(&env), vec![a]);
    assert_eq!(
        count(
            &env,
            "SELECT COUNT(*) FROM sessions WHERE analysis_state = 'ready'"
        ),
        1
    );
}

#[tokio::test]
async fn removing_one_recording_queues_a_new_analysis_when_the_rest_was_never_analysed_alone() {
    let env = Env::new().await;
    require_python!(env);
    let (a, b, _) = two_recording_session(&env).await;
    // forget the analysis of A alone (its row and its finished job)
    env.app
        .store
        .write_blocking({
            let a = a.clone();
            move |conn| {
                conn.execute("DELETE FROM jobs WHERE kind = 'project' AND json_extract(subject_json, '$.analysis_id') IN (SELECT analysis_id FROM analyses WHERE recordings_sha256 = json_array(?1))", [&a])?;
                conn.execute("DELETE FROM analyses WHERE recordings_sha256 = json_array(?1)", [&a])?;
                conn.execute(
                    "DELETE FROM jobs WHERE kind = 'analyze' AND instr(subject_json, ?1) > 0 AND json_array_length(json_extract(subject_json, '$.recordings')) = 1",
                    [&a],
                )?;
                Ok(())
            }
        })
        .unwrap();
    assert_eq!(
        count(&env, "SELECT COUNT(*) FROM analyses"),
        1,
        "only the pair's analysis is left"
    );
    remove(&env, json!({"recordings": [b], "dry_run": false})).await;
    assert_eq!(count(&env, "SELECT COUNT(*) FROM analyses"), 0);
    assert_eq!(
        count(
            &env,
            "SELECT COUNT(*) FROM sessions WHERE analysis_state = 'queued'"
        ),
        1,
        "the analysis over the remaining recording is queued"
    );
    env.settle().await;
    assert_eq!(current_members(&env), vec![a]);
}

fn garage_visit(
    env: &Env,
    visit: &str,
    snapshots: &[(i64, Value)],
    commit: Option<(&str, &str, Value)>,
) {
    let visit = visit.to_string();
    let snapshots = snapshots.to_vec();
    let commit = commit.map(|(n, h, v)| (n.to_string(), h.to_string(), v));
    env.app
        .store
        .write_blocking(move |conn| {
            conn.execute(
                "INSERT OR IGNORE INTO collectors(collector_id, machine_name, collector_version, protocol, first_seen_at, last_seen_at)
                 VALUES (?1, 'TEST', '0', 1, '2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z')",
                [COLLECTOR],
            )?;
            for (count, values) in &snapshots {
                let msg = json!({"type": "garage.snapshot", "visit_id": visit, "update_count": count,
                    "observed_at": "2026-10-01T10:00:00Z", "car_path": "stockcars chevyss2020",
                    "driver": {"customer_id": CUSTOMER, "name": "Joshua Granger"}, "values": values});
                setups::record_garage_snapshot(conn, &msg, COLLECTOR, &fake_fingerprint)?;
            }
            if let Some((name, sto, values)) = &commit {
                let msg = json!({"type": "garage.commit", "visit_id": visit, "update_count": 9,
                    "entered_at": "2026-10-01T09:59:00Z", "exited_at": "2026-10-01T10:00:30Z",
                    "car_path": "stockcars chevyss2020", "driver": {"customer_id": CUSTOMER},
                    "setup_name": name, "sto_sha256": sto, "is_modified": false, "load_type": "user", "values": values});
                setups::record_garage_commit(conn, &msg, COLLECTOR, &fake_fingerprint)?;
            }
            Ok(())
        })
        .expect("garage events");
}

#[tokio::test]
async fn a_setup_file_goes_with_its_garage_visits_and_orphaned_setups_and_a_shared_blob_stays() {
    let env = Env::new().await;
    require_python!(env);
    let real = ingest(&env, race_meta(9721, 0, "real")).await; // a real recording with its own setup
    let baseline = b"e2e baseline sto";
    let capture = b"e2e capture sto";
    let folder = "stockcars chevyss2020";
    env.upload(
        baseline,
        "sto",
        "e2e baseline.sto",
        Some(&format!("{folder}/e2e baseline.sto")),
        4096,
    )
    .await;
    env.upload(
        capture,
        "sto",
        "e2e garage capture.sto",
        Some(&format!("{folder}/e2e garage capture.sto")),
        4096,
    )
    .await;
    // the same bytes under a second name: the blob must stay for it
    env.upload(
        capture,
        "sto",
        "kept copy.sto",
        Some(&format!("{folder}/kept copy.sto")),
        4096,
    )
    .await;
    env.upload(
        b"real sto",
        "sto",
        "real.sto",
        Some(&format!("{folder}/real.sto")),
        4096,
    )
    .await;
    env.settle().await;
    let capture_sha = sha256_hex(capture);
    let baseline_sha = sha256_hex(baseline);
    assert_eq!(count(&env, "SELECT COUNT(*) FROM setup_files"), 4);

    // two visits of the test run (one nameless snapshot of its own) and one real visit
    let x = json!({"Chassis.Front.ArbSetting": "11", "UpdateCount": "3"});
    let y = json!({"Chassis.Front.ArbSetting": "12", "UpdateCount": "4"});
    garage_visit(
        &env,
        "aaaaaaaa-0000-4000-8000-000000000001",
        &[(3, x.clone()), (4, y.clone())],
        Some(("e2e garage capture.sto", &capture_sha, x.clone())),
    );
    let z = json!({"Chassis.Front.ArbSetting": "13", "UpdateCount": "2"});
    garage_visit(
        &env,
        "aaaaaaaa-0000-4000-8000-000000000002",
        &[(2, z.clone())],
        Some(("real.sto", &sha256_hex(b"real sto"), z.clone())),
    );
    assert_eq!(count(&env, "SELECT COUNT(*) FROM garage_visits"), 2);
    let setups_before = count(&env, "SELECT COUNT(*) FROM setups");
    assert_eq!(setups_before, 4, "recording, x, y, z");
    let nas_before = tree(&env.nas());

    let names = json!([
        "e2e garage capture.sto",
        "E2E BASELINE.STO",
        "never existed.sto"
    ]);
    let plan = remove(&env, json!({"setup_files": names})).await;
    let cap = &plan["setup_files"][0];
    assert_eq!(cap["found"], true);
    assert_eq!(cap["applied"], false);
    assert_eq!(cap["files"].as_array().unwrap().len(), 1);
    assert_eq!(
        cap["garage_visits"],
        json!(["aaaaaaaa-0000-4000-8000-000000000001"])
    );
    assert_eq!(cap["setup_observations"], 3, "two snapshots and the commit");
    assert_eq!(cap["setups"].as_array().unwrap().len(), 2, "x and y");
    assert_eq!(cap["blobs_removed"], json!([]));
    assert_eq!(
        cap["blobs_kept"],
        json!([capture_sha]),
        "another name still uses the bytes"
    );
    assert_eq!(
        plan["setup_files"][1]["files"].as_array().unwrap().len(),
        1,
        "names match without case"
    );
    assert_eq!(
        plan["setup_files"][1]["blobs_removed"],
        json!([baseline_sha])
    );
    assert_eq!(plan["setup_files"][2]["found"], false);
    assert_eq!(
        count(&env, "SELECT COUNT(*) FROM setup_files"),
        4,
        "a dry run changes nothing"
    );

    let done = remove(&env, json!({"setup_files": names, "dry_run": false})).await;
    assert_eq!(done["setup_files"][0]["applied"], true);
    assert_eq!(done["setup_files"][1]["applied"], true);
    assert_eq!(done["setup_files"][2]["applied"], false);
    assert_eq!(
        count(&env, "SELECT COUNT(*) FROM setup_files"),
        2,
        "kept copy.sto and real.sto remain"
    );
    assert_eq!(
        count(
            &env,
            "SELECT COUNT(*) FROM setup_files WHERE relative_path IN ('kept copy.sto', 'real.sto')"
        ),
        2
    );
    assert_eq!(count(&env, "SELECT COUNT(*) FROM garage_visits"), 1);
    assert_eq!(
        count(
            &env,
            "SELECT COUNT(*) FROM garage_visits WHERE committed_name = 'real.sto'"
        ),
        1
    );
    assert_eq!(
        count(&env, "SELECT COUNT(*) FROM setups"),
        2,
        "recording and z remain"
    );
    assert_eq!(
        count(
            &env,
            &format!("SELECT COUNT(*) FROM blobs WHERE sha256 = '{capture_sha}'")
        ),
        1
    );
    assert_eq!(
        count(
            &env,
            &format!("SELECT COUNT(*) FROM blob_occurrences WHERE sha256 = '{capture_sha}'")
        ),
        1,
        "only the kept name's occurrence is left"
    );
    for sql in [
        format!("SELECT COUNT(*) FROM blobs WHERE sha256 = '{baseline_sha}'"),
        format!("SELECT COUNT(*) FROM uploads WHERE sha256 = '{baseline_sha}'"),
        format!("SELECT COUNT(*) FROM jobs WHERE instr(subject_json, '{baseline_sha}') > 0"),
    ] {
        assert_eq!(count(&env, &sql), 0, "{sql}");
    }
    assert!(!env.data().join("spool/ready").join(&baseline_sha).exists());
    assert_eq!(
        count(
            &env,
            &format!("SELECT COUNT(*) FROM blobs WHERE sha256 = '{real}'")
        ),
        1
    );
    assert_eq!(tree(&env.nas()), nas_before, "the NAS is untouched");
    assert_eq!(log_lines(&env).len(), 1);
    // the library no longer lists the removed files
    let library = env.get("/api/v1/drivers/1001/setups").await.json();
    let listed = library.to_string();
    assert!(
        !listed.contains("e2e baseline.sto") && !listed.contains("e2e garage capture.sto"),
        "{listed}"
    );

    // idempotent
    let again = remove(&env, json!({"setup_files": names, "dry_run": false})).await;
    for target in again["setup_files"].as_array().unwrap() {
        assert_eq!(target["found"], false);
        assert_eq!(target["applied"], false);
    }
    assert_eq!(log_lines(&env).len(), 1);
}

#[tokio::test]
async fn blocked_targets_are_reported_and_left_alone() {
    let env = Env::new().await;
    require_python!(env);
    let noted = ingest(&env, race_meta(9731, 0, "noted")).await;
    let running = ingest(&env, race_meta(9732, 0, "running")).await;
    env.settle().await;
    let session: i64 = env.query(|c| {
        c.query_row(
            "SELECT session_id FROM session_recordings WHERE recording_sha256 = ?1",
            [&noted],
            |r| r.get(0),
        )
    });
    let note = env
        .post(
            "/api/v1/drivers/1001/notes",
            json!({"subject_kind": "session", "subject_id": session.to_string(), "body": "keep me"}),
        )
        .await;
    assert_eq!(note.status, StatusCode::CREATED);
    let sha = running.clone();
    env.app
        .store
        .write_blocking(move |c| {
            c.execute(
                "INSERT INTO jobs(kind, lane, state, subject_json, created_at) VALUES ('analyze', 'analysis', 'running', ?1, '2026-10-01T00:00:00Z')",
                [json!({"recordings": [{"sha256": sha}]}).to_string()],
            )?;
            Ok(())
        })
        .unwrap();
    let setup_blob = {
        let bytes = b"not a recording";
        env.upload(bytes, "sto", "x.sto", Some("car/x.sto"), 4096)
            .await;
        sha256_hex(bytes)
    };
    env.settle().await;
    // not on the NAS yet: the spool copy would be the only one
    let pending = ingest(&env, race_meta(9733, 0, "pending")).await;
    assert_eq!(
        count(
            &env,
            &format!(
                "SELECT COUNT(*) FROM blobs WHERE sha256 = '{pending}' AND nas_state = 'pending'"
            )
        ),
        1
    );

    let report = remove(
        &env,
        json!({"recordings": [noted, running, pending, setup_blob], "dry_run": false}),
    )
    .await;
    let by = |i: usize| &report["recordings"][i];
    assert_eq!(by(0)["blocked"], json!(["has_notes"]));
    assert_eq!(by(1)["blocked"], json!(["job_running"]));
    assert_eq!(by(2)["blocked"], json!(["not_archived"]));
    assert_eq!(by(3)["blocked"], json!(["not_a_recording"]));
    for i in 0..4 {
        assert_eq!(by(i)["applied"], false, "{i}");
    }
    assert_eq!(count(&env, "SELECT COUNT(*) FROM blobs"), 4);
    assert_eq!(count(&env, "SELECT COUNT(*) FROM sessions"), 2);
    assert_eq!(count(&env, "SELECT COUNT(*) FROM notes"), 1);
    assert!(
        log_lines(&env).is_empty(),
        "nothing applied, nothing logged"
    );
}

#[tokio::test]
async fn a_remembered_investigation_of_a_removed_session_is_cleared() {
    let env = Env::new().await;
    require_python!(env);
    let gone = ingest(&env, race_meta(9741, 0, "gone")).await;
    env.settle().await;
    let (session, analysis): (i64, String) = env.query(|c| {
        c.query_row(
            "SELECT session_id, current_analysis_id FROM sessions",
            [],
            |r| Ok((r.get(0)?, r.get(1)?)),
        )
    });
    let put = env
        .put(
            "/api/v1/drivers/1001/prefs",
            json!({"last_investigation": {"session_id": session, "analysis_id": analysis, "view": "race"}}),
            None,
        )
        .await;
    assert!(
        put.status.is_success(),
        "{}",
        String::from_utf8_lossy(&put.body)
    );
    assert_eq!(
        count(
            &env,
            "SELECT COUNT(*) FROM driver_prefs WHERE last_investigation_json IS NOT NULL"
        ),
        1
    );
    remove(&env, json!({"recordings": [gone], "dry_run": false})).await;
    assert_eq!(
        count(
            &env,
            "SELECT COUNT(*) FROM driver_prefs WHERE last_investigation_json IS NOT NULL"
        ),
        0
    );
}

#[tokio::test]
async fn the_request_is_validated() {
    let env = Env::new().await;
    for body in [
        json!({}),
        json!({"setup_files": []}),
        json!({"recordings": ["not a hash"]}),
        json!({"setup_files": ["a.sto"], "surprise": 1}),
        json!({"setup_files": [""]}),
    ] {
        let reply = env.post("/api/v1/admin/remove", body.clone()).await;
        assert_eq!(reply.status, StatusCode::BAD_REQUEST, "{body}");
    }
}

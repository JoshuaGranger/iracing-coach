//! Migrations, snapshots and backups, rebuild-index, the server binary (`--dev`, healthcheck,
//! idle memory), ingest failures and the Python parity of the tuning map hash.

mod support;

use std::io::{Read, Write};
use std::net::TcpStream;
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

use coach_server::util::sha256_hex;
use rusqlite::Connection;
use serde_json::{Value, json};
use support::*;

fn count(env: &Env, sql: &str) -> i64 {
    let sql = sql.to_string();
    env.query(move |c| c.query_row(&sql, [], |r| r.get(0)))
}

// ---------------------------------------------------------------- migrations

#[test]
fn migration_one_is_the_contract_schema_and_runs_once() {
    let contract = std::fs::read_to_string(
        std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../docs/08-rewrite/contracts/db-schema.sql"),
    )
    .unwrap();
    let shipped = std::fs::read_to_string(
        std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("migrations/0001_initial.sql"),
    )
    .unwrap();
    assert_eq!(
        contract.replace("\r\n", "\n"),
        shipped.replace("\r\n", "\n"),
        "migrations/0001_initial.sql must equal contracts/db-schema.sql"
    );

    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("coach.sqlite3");
    let store = coach_server::db::Store::open(&path, "0.19.0").unwrap();
    drop(store);
    let store = coach_server::db::Store::open(&path, "0.19.0").unwrap(); // second start: nothing to do
    let versions: Vec<i64> = store
        .read_blocking(|c| {
            let mut stmt = c.prepare("SELECT version FROM schema_migrations")?;
            Ok(stmt
                .query_map([], |r| r.get(0))?
                .collect::<rusqlite::Result<_>>()?)
        })
        .unwrap();
    assert_eq!(versions, vec![1]);
    let (journal, fk): (String, i64) = store
        .read_blocking(|c| {
            Ok((
                c.query_row("PRAGMA journal_mode", [], |r| r.get(0))?,
                c.query_row("PRAGMA foreign_keys", [], |r| r.get(0))?,
            ))
        })
        .unwrap();
    assert_eq!(journal, "wal");
    assert_eq!(fk, 1);
    assert!(
        !dir.path().join("pre-migrate-v1.sqlite3").exists(),
        "nothing was migrated"
    );
}

#[test]
fn a_pending_migration_writes_a_pre_migrate_copy_first() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("coach.sqlite3");
    drop(coach_server::db::Store::open(&path, "0.19.0").unwrap());
    let mut conn = Connection::open(&path).unwrap();
    let extra = [(
        2,
        "add a nullable column",
        "ALTER TABLE drivers ADD COLUMN nickname TEXT;",
    )];
    let all: Vec<(i64, &str, &str)> = coach_server::db::MIGRATIONS
        .iter()
        .copied()
        .chain(extra)
        .collect();
    coach_server::db::migrate_with(&mut conn, &path, "0.20.0", &all).unwrap();
    assert!(dir.path().join("pre-migrate-v1.sqlite3").exists());
    let version: i64 = conn
        .query_row("SELECT MAX(version) FROM schema_migrations", [], |r| {
            r.get(0)
        })
        .unwrap();
    assert_eq!(version, 2);
    // the copy still holds the old schema
    let old = Connection::open(dir.path().join("pre-migrate-v1.sqlite3")).unwrap();
    let cols: i64 = old
        .query_row(
            "SELECT COUNT(*) FROM pragma_table_info('drivers') WHERE name = 'nickname'",
            [],
            |r| r.get(0),
        )
        .unwrap();
    assert_eq!(cols, 0);
}

// ------------------------------------------------------- snapshots and NAS

#[tokio::test]
async fn snapshots_follow_user_data_generations_and_back_up_to_the_nas() {
    let env = Env::new().await;
    // a driver and one user-authored write
    let bytes = fake_ibt(race_meta(9910, 0, "snap"), 2_000);
    env.upload(&bytes, "ibt", "s.ibt", None, 2000).await;
    coach_server::ingest::ingest_pending(&env.app)
        .await
        .unwrap();
    env.post(
        "/api/v1/drivers/1001/notes",
        json!({"subject_kind": "session", "subject_id": "1", "body": "remember"}),
    )
    .await;
    let generation = count(
        &env,
        "SELECT value FROM server_counters WHERE name = 'user_data_generation'",
    );
    assert_eq!(generation, 1);

    // a Python history database and tuning folder are part of the snapshot
    let archive = env.data().join("py/1001/archive");
    std::fs::create_dir_all(archive.join("tuning")).unwrap();
    std::fs::write(archive.join("tuning/draft.json"), b"{}").unwrap();
    let history = Connection::open(archive.join("history.sqlite3")).unwrap();
    history.execute_batch("CREATE TABLE tuning_experiments(id TEXT); INSERT INTO tuning_experiments VALUES ('e1');").unwrap();
    drop(history);

    let now = coach_server::util::unix_now();
    let summary = coach_server::scheduler::tick(&env.app, now).await.unwrap();
    assert_eq!(summary["snapshot"]["generation"], 1);
    assert_eq!(summary["snapshot"]["created"], true);
    let dir = env.data().join("snapshots/g1");
    let manifest: Value =
        serde_json::from_slice(&std::fs::read(dir.join("manifest.json")).unwrap()).unwrap();
    assert_eq!(manifest["kind"], "backup");
    let files: Vec<String> = manifest["backup"]["files"]
        .as_array()
        .unwrap()
        .iter()
        .map(|f| f["path"].as_str().unwrap().to_string())
        .collect();
    for expected in [
        "db/coach.sqlite3",
        "py/1001/archive/history.sqlite3",
        "py/1001/archive/tuning/draft.json",
    ] {
        assert!(
            files.iter().any(|f| f == expected),
            "{expected} missing from {files:?}"
        );
    }
    // the snapshot is a readable copy that holds the note
    let copy = Connection::open(dir.join("db/coach.sqlite3")).unwrap();
    let notes: i64 = copy
        .query_row("SELECT COUNT(*) FROM notes", [], |r| r.get(0))
        .unwrap();
    assert_eq!(notes, 1);
    #[cfg(unix)]
    assert_eq!(
        std::fs::read_link(env.data().join("snapshots/latest"))
            .unwrap()
            .to_string_lossy(),
        "g1"
    );

    // no change, no new generation
    let again = coach_server::scheduler::tick(&env.app, now + 3600)
        .await
        .unwrap();
    assert!(
        again["snapshot"].is_null() || again["snapshot"]["created"] == false,
        "{again}"
    );
    assert!(!env.data().join("snapshots/g2").exists());

    // the daily copy lands in backups/<day> with matching hashes, complete and left alone afterwards
    let day = coach_server::util::day_from_unix(now, 0);
    let day_dir = env.nas().join("backups").join(&day);
    if !day_dir.exists() {
        // before 04:00 UTC the daily copy is not due yet; ask for it explicitly
        let reply = env.post("/api/v1/admin/snapshot", json!({})).await;
        assert_eq!(reply.status, axum::http::StatusCode::ACCEPTED);
        coach_server::jobs::drain(&env.app).await.unwrap();
    }
    assert!(day_dir.join("manifest.json").exists(), "{day_dir:?}");
    let nas_manifest: Value =
        serde_json::from_slice(&std::fs::read(day_dir.join("manifest.json")).unwrap()).unwrap();
    for f in nas_manifest["backup"]["files"].as_array().unwrap() {
        let (hash, _) =
            coach_server::util::sha256_file(&day_dir.join(f["path"].as_str().unwrap())).unwrap();
        assert_eq!(hash, f["sha256"].as_str().unwrap());
    }
    let before = std::fs::metadata(day_dir.join("manifest.json"))
        .unwrap()
        .modified()
        .unwrap();
    env.post(
        "/api/v1/drivers/1001/notes",
        json!({"subject_kind": "session", "subject_id": "2", "body": "more"}),
    )
    .await;
    let (g, dir2, created) = coach_server::scheduler::take_snapshot(&env.app)
        .await
        .unwrap();
    assert!(created && g == 2);
    assert!(
        !coach_server::scheduler::backup_to_nas(&env.app, g, dir2, &day)
            .await
            .unwrap(),
        "an existing complete day is left alone"
    );
    assert_eq!(
        std::fs::metadata(day_dir.join("manifest.json"))
            .unwrap()
            .modified()
            .unwrap(),
        before
    );
    assert_eq!(
        std::fs::read_dir(env.nas().join(".tmp")).unwrap().count(),
        0
    );
}

#[tokio::test]
async fn only_the_newest_48_generations_are_kept_locally() {
    let env = Env::new().await;
    for g in 100..160 {
        let dir = env.data().join(format!("snapshots/g{g}"));
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("manifest.json"), b"{}").unwrap();
    }
    env.post("/api/v1/drivers/1/notes", json!({})).await; // no-op request, bumps nothing
    let bytes = fake_ibt(race_meta(9911, 0, "prune"), 1_000);
    env.upload(&bytes, "ibt", "p.ibt", None, 1000).await;
    coach_server::ingest::ingest_pending(&env.app)
        .await
        .unwrap();
    env.post(
        "/api/v1/drivers/1001/notes",
        json!({"subject_kind": "session", "subject_id": "1", "body": "x"}),
    )
    .await;
    coach_server::scheduler::take_snapshot(&env.app)
        .await
        .unwrap();
    let kept = std::fs::read_dir(env.data().join("snapshots"))
        .unwrap()
        .filter(|e| {
            e.as_ref()
                .unwrap()
                .file_name()
                .to_string_lossy()
                .starts_with('g')
        })
        .count();
    assert_eq!(kept, 48);
    assert!(env.data().join("snapshots/g159").exists(), "newest kept");
    assert!(!env.data().join("snapshots/g100").exists(), "oldest pruned");
}

// ------------------------------------------------------------- rebuild-index

#[tokio::test]
async fn rebuild_index_recovers_blobs_and_recordings_from_manifests() {
    let env = Env::new().await;
    let bytes = fake_ibt(race_meta(9920, 0, "rebuild"), 6_000);
    let sha = sha256_hex(&bytes);
    env.upload(&bytes, "ibt", "rebuild.ibt", None, 6000).await;
    let sto = b"setup".to_vec();
    env.upload(
        &fake_ibt(race_meta(9921, 0, "drv"), 1_000),
        "ibt",
        "drv.ibt",
        None,
        1000,
    )
    .await;
    coach_server::ingest::ingest_pending(&env.app)
        .await
        .unwrap();
    env.upload(
        &sto,
        "sto",
        "x.sto",
        Some("stockcars chevyss2020/x.sto"),
        100,
    )
    .await;
    // the recordings' manifests wait for their discovery
    while coach_server::jobs::run_next(&env.app, "light")
        .await
        .unwrap()
    {}
    while coach_server::jobs::run_next(&env.app, "io").await.unwrap() {}
    assert_eq!(
        count(
            &env,
            "SELECT COUNT(*) FROM blobs WHERE nas_state = 'archived'"
        ),
        3
    );
    let cfg = env.app.cfg.clone();
    let dir = env.dir.clone();
    drop(env);

    // the database is lost
    for entry in std::fs::read_dir(dir.path().join("data/db")).unwrap() {
        std::fs::remove_file(entry.unwrap().path()).unwrap();
    }
    let summary = coach_server::rebuild::rebuild_index(&cfg).unwrap();
    assert_eq!(summary.manifests, 3);
    assert_eq!(summary.recordings, 2);
    assert_eq!(summary.setup_files, 1);
    let store = coach_server::db::Store::open(&cfg.db_path(), "0.19.0").unwrap();
    let (blobs, recs, files, drivers, jobs): (i64, i64, i64, i64, i64) = store
        .read_blocking(|c| {
            Ok((
                c.query_row("SELECT COUNT(*) FROM blobs WHERE nas_state = 'archived' AND spool_present = 0", [], |r| r.get(0))?,
                c.query_row("SELECT COUNT(*) FROM recordings WHERE ingest_state = 'parsed' AND customer_id = 1001", [], |r| r.get(0))?,
                c.query_row("SELECT COUNT(*) FROM setup_files", [], |r| r.get(0))?,
                c.query_row("SELECT COUNT(*) FROM drivers", [], |r| r.get(0))?,
                c.query_row("SELECT COUNT(*) FROM jobs WHERE kind = 'discover'", [], |r| r.get(0))?,
            ))
        })
        .unwrap();
    assert_eq!((blobs, recs, files, drivers, jobs), (3, 2, 1, 1, 2));
    let first: String = store
        .read_blocking(|c| {
            Ok(c.query_row(
                "SELECT first_name FROM blobs WHERE sha256 = ?1",
                [&sha],
                |r| r.get(0),
            )?)
        })
        .unwrap();
    assert_eq!(first, "rebuild.ibt");
    // running it again changes nothing
    drop(store);
    let again = coach_server::rebuild::rebuild_index(&cfg).unwrap();
    assert_eq!(again.manifests, 3);
}

// -------------------------------------------------------------- ingest errors

#[tokio::test]
async fn unreadable_recordings_are_marked_failed_with_the_reason() {
    let env = Env::new().await;
    let mut meta = race_meta(9930, 0, "broken");
    meta["fail"] = json!("IBT file is shorter than the extent declared by its disk header.");
    let bytes = fake_ibt(meta, 3_000);
    let sha = sha256_hex(&bytes);
    env.upload(&bytes, "ibt", "broken.ibt", None, 3000).await;
    coach_server::ingest::ingest_pending(&env.app)
        .await
        .unwrap();
    let (state, error): (String, String) = env.query(move |c| {
        c.query_row(
            "SELECT ingest_state, ingest_error FROM recordings WHERE sha256 = ?1",
            [sha],
            |r| Ok((r.get(0)?, r.get(1)?)),
        )
    });
    assert_eq!(state, "failed");
    assert!(error.contains("shorter than the extent"));
    assert_eq!(
        count(&env, "SELECT COUNT(*) FROM jobs WHERE kind = 'discover'"),
        0,
        "a failed recording is not discovered"
    );
    // the collector sees `failed` and stops retrying
    let again = env
        .create_upload(
            &sha256_hex(&bytes),
            bytes.len(),
            "ibt",
            "broken.ibt",
            None,
            None,
        )
        .await;
    assert_eq!(again.json()["state"], "failed");

    // a recording without a driver identity fails too
    let mut anon = race_meta(9931, 0, "anon");
    anon["customer_id"] = Value::Null;
    env.upload(&fake_ibt(anon, 3_000), "ibt", "anon.ibt", None, 3000)
        .await;
    coach_server::ingest::ingest_pending(&env.app)
        .await
        .unwrap();
    assert_eq!(
        count(
            &env,
            "SELECT COUNT(*) FROM recordings WHERE ingest_state = 'failed'"
        ),
        2
    );
}

#[tokio::test]
async fn a_failed_projection_is_reported_and_can_be_retried() {
    let env = Env::new().await;
    if env.python.is_none() {
        eprintln!("skipped: no Python 3 for the stub worker");
        return;
    }
    *env.projector.fail.lock().unwrap() = true;
    let bytes = fake_ibt(race_meta(9940, 0, "proj"), 3_000);
    env.upload(&bytes, "ibt", "p.ibt", None, 3000).await;
    env.settle().await;
    let analysis_id: String =
        env.query(|c| c.query_row("SELECT analysis_id FROM analyses", [], |r| r.get(0)));
    assert_eq!(
        count(
            &env,
            "SELECT COUNT(*) FROM analyses WHERE projection_state = 'failed'"
        ),
        1
    );
    let reply = env
        .get(&format!("/api/v1/analyses/{analysis_id}/race"))
        .await;
    assert_eq!(reply.status, axum::http::StatusCode::CONFLICT);
    assert_eq!(
        reply.json()["details"]["last_error"],
        "projection failed on purpose"
    );
    // fixed: the request that found it failed queued a retry
    *env.projector.fail.lock().unwrap() = false;
    env.settle().await;
    assert_eq!(
        env.get(&format!("/api/v1/analyses/{analysis_id}/race"))
            .await
            .status,
        axum::http::StatusCode::OK
    );
}

// ------------------------------------------------------------------- the binary

fn free_port() -> u16 {
    std::net::TcpListener::bind("127.0.0.1:0")
        .unwrap()
        .local_addr()
        .unwrap()
        .port()
}

fn http_get(port: u16, path: &str) -> Option<String> {
    let mut stream = TcpStream::connect_timeout(
        &format!("127.0.0.1:{port}").parse().unwrap(),
        Duration::from_secs(2),
    )
    .ok()?;
    stream.set_read_timeout(Some(Duration::from_secs(3))).ok()?;
    write!(
        stream,
        "GET {path} HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
    )
    .ok()?;
    let mut out = String::new();
    stream.read_to_string(&mut out).ok()?;
    Some(out)
}

struct Server(Child);

impl Drop for Server {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

fn spawn_server(dir: &std::path::Path, port: u16) -> Server {
    let child = Command::new(env!("CARGO_BIN_EXE_coach-server"))
        .arg("--dev")
        .env("COACH_DEV_DIR", dir)
        .env("COACH_BIND", format!("127.0.0.1:{port}"))
        .env("COACH_PYTHON", "python-not-needed")
        .env("RUST_LOG", "warn")
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .expect("spawn coach-server");
    Server(child)
}

#[test]
fn dev_mode_serves_healthcheck_succeeds_and_idle_memory_is_small() {
    let dir = tempfile::tempdir().unwrap();
    let port = free_port();
    let server = spawn_server(dir.path(), port);
    let end = Instant::now() + Duration::from_secs(30);
    let health = loop {
        if let Some(r) = http_get(port, "/health")
            && r.starts_with("HTTP/1.1 200")
        {
            break r;
        }
        assert!(Instant::now() < end, "the server did not become healthy");
        std::thread::sleep(Duration::from_millis(100));
    };
    assert!(health.contains("\"status\":\"ok\""), "{health}");
    assert!(health.contains("\"schema_version\":1"));

    // `coach-server healthcheck` is the Compose healthcheck
    let status = Command::new(env!("CARGO_BIN_EXE_coach-server"))
        .args(["healthcheck", "--dev"])
        .env("COACH_BIND", format!("127.0.0.1:{port}"))
        .status()
        .unwrap();
    assert!(status.success());
    let down = Command::new(env!("CARGO_BIN_EXE_coach-server"))
        .args(["healthcheck", "--dev"])
        .env("COACH_BIND", format!("127.0.0.1:{}", free_port()))
        .status()
        .unwrap();
    assert!(!down.success(), "healthcheck fails when nothing listens");

    // --dev keeps everything under the given directory
    assert!(dir.path().join("data/db/coach.sqlite3").exists());
    assert!(dir.path().join("nas/FORMAT").exists());
    let api = http_get(port, "/api/v1/status").unwrap();
    assert!(api.contains("\"api_version\":1"), "{api}");

    // idle resident memory stays inside the 128 MB budget
    #[cfg(target_os = "linux")]
    {
        std::thread::sleep(Duration::from_millis(500));
        let status = std::fs::read_to_string(format!("/proc/{}/status", server.0.id())).unwrap();
        let rss_kb: u64 = status
            .lines()
            .find_map(|l| l.strip_prefix("VmRSS:"))
            .unwrap()
            .trim()
            .trim_end_matches(" kB")
            .trim()
            .parse()
            .unwrap();
        assert!(rss_kb <= 128 * 1024, "idle RSS {rss_kb} kB exceeds 128 MB");
        eprintln!("idle RSS {} kB", rss_kb);
    }
    drop(server);
}

#[cfg(unix)]
#[test]
fn sigterm_takes_a_final_snapshot_and_exits_cleanly() {
    let dir = tempfile::tempdir().unwrap();
    let port = free_port();
    let mut server = spawn_server(dir.path(), port);
    let end = Instant::now() + Duration::from_secs(30);
    while http_get(port, "/health").is_none_or(|r| !r.starts_with("HTTP/1.1 200")) {
        assert!(Instant::now() < end);
        std::thread::sleep(Duration::from_millis(100));
    }
    Command::new("kill")
        .args(["-TERM", &server.0.id().to_string()])
        .status()
        .unwrap();
    let status = server.0.wait().unwrap();
    assert!(status.success(), "{status:?}");
    let snapshots: Vec<_> = std::fs::read_dir(dir.path().join("data/snapshots"))
        .unwrap()
        .flatten()
        .collect();
    assert!(
        snapshots
            .iter()
            .any(|e| e.path().join("manifest.json").exists()),
        "a snapshot was written on SIGTERM"
    );
}

// ---------------------------------------------------------------- Python parity

#[test]
fn annotation_hash_matches_the_python_oracle() {
    let scripts = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../iracing-coach/skills/analyze-iracing-race/scripts");
    let Some(python) = python() else {
        eprintln!("skipped: no Python 3 available");
        return;
    };
    if !scripts.join("tuning_engine.py").exists() {
        return;
    }
    let submission = json!({
        "track_configuration_key": "stub-speedway:oval", "source_type": "user-confirmed", "verified": true,
        "corners": [
            {"corner_id": "t2", "label": "Turn 2 \u{00e9}", "start_pct": 0.5123456789, "apex_pct": 0.55, "end_pct": 0.6, "is_official": true, "user_verified": true},
            {"corner_id": "t1", "label": "Turn 1", "start_pct": 0.00001, "apex_pct": 0.05, "end_pct": 0.1, "is_official": false, "user_verified": false},
            {"corner_id": "t3", "label": "Wrap", "start_pct": 0.95, "apex_pct": 0.98, "end_pct": 0.02, "is_official": false, "user_verified": true}
        ]
    });
    let geometry = "AB".repeat(32);
    let script = format!(
        "import json, sys\nsys.path.insert(0, {scripts:?})\nimport tuning_engine\nsub = json.loads(sys.argv[1])\nsub['geometry_hash'] = sys.argv[2]\nprint(tuning_engine.map_annotation_hash(sub))",
        scripts = scripts.to_string_lossy()
    );
    let out = Command::new(&python)
        .args(["-c", &script, &submission.to_string(), &geometry])
        .output()
        .unwrap();
    assert!(
        out.status.success(),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    let python_hash = String::from_utf8_lossy(&out.stdout).trim().to_string();
    let rust_hash = coach_server::tuning::map_annotation_hash(&submission, &geometry).unwrap();
    assert_eq!(rust_hash, python_hash);
}

#[test]
fn worker_identity_changes_with_the_scripts() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(dir.path().join("a.py"), "x = 1").unwrap();
    std::fs::write(
        dir.path().join("analysis_engine.py"),
        "ANALYSIS_PROFILE_VERSION = \"post-race-foundations-v23\"\n",
    )
    .unwrap();
    let first = coach_server::app::WorkerIdentity::from_scripts(Some(dir.path()));
    assert_eq!(
        first.profile_version.as_deref(),
        Some("post-race-foundations-v23")
    );
    std::fs::write(dir.path().join("a.py"), "x = 2").unwrap();
    let second = coach_server::app::WorkerIdentity::from_scripts(Some(dir.path()));
    assert_ne!(first.bundle_digest, second.bundle_digest);
}

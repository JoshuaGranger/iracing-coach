//! Upload and ingest: protocol behaviour, ENOSPC, concurrency, crash cuts at every step of the
//! upload and archive protocols, and every row of the restart reconciliation table.

mod support;

use std::time::{Duration, SystemTime};

use axum::http::StatusCode;
use coach_server::faults::{ARCHIVE_POINTS, FaultMode, UPLOAD_POINTS};
use coach_server::jobs::JobRow;
use coach_server::util::sha256_hex;
use rusqlite::params;
use serde_json::{Value, json};
use support::*;

fn recording(subsession: i64, name: &str, len: usize) -> Vec<u8> {
    fake_ibt(race_meta(subsession, 0, name), len)
}

fn count(env: &Env, sql: &str) -> i64 {
    let sql = sql.to_string();
    env.query(move |c| c.query_row(&sql, [], |r| r.get(0)))
}

fn durable_offset(env: &Env, sha: &str) -> i64 {
    let sha = sha.to_string();
    env.query(move |c| {
        c.query_row(
            "SELECT durable_offset FROM uploads WHERE sha256 = ?1",
            [sha],
            |r| r.get(0),
        )
    })
}

fn ready_files(env: &Env) -> Vec<String> {
    let mut v: Vec<String> = std::fs::read_dir(env.data().join("spool/ready"))
        .unwrap()
        .map(|e| e.unwrap().file_name().to_string_lossy().into_owned())
        .collect();
    v.sort();
    v
}

fn incoming_files(env: &Env) -> Vec<String> {
    std::fs::read_dir(env.data().join("spool/incoming"))
        .unwrap()
        .map(|e| e.unwrap().file_name().to_string_lossy().into_owned())
        .collect()
}

#[tokio::test]
async fn upload_verifies_commits_and_ingests() {
    let env = Env::new().await;
    let bytes = recording(8001, "a", 10_000);
    let sha = sha256_hex(&bytes);
    let reply = env.upload(&bytes, "ibt", "a.ibt", None, 4096).await;
    assert_eq!(
        reply.status,
        StatusCode::OK,
        "{}",
        String::from_utf8_lossy(&reply.body)
    );
    assert_eq!(reply.json()["state"], "verified");
    assert_eq!(reply.json()["offset"], 10_000);

    let ready = env.data().join("spool/ready").join(&sha);
    assert_eq!(std::fs::read(&ready).unwrap(), bytes);
    // mtime is the source's, clamped to now - 10 s: the source (2026-08-01) is older
    let mtime = std::fs::metadata(&ready).unwrap().modified().unwrap();
    let want = SystemTime::UNIX_EPOCH
        + Duration::from_secs(
            coach_server::util::parse_iso("2026-08-01T12:30:00Z").unwrap() as u64,
        );
    assert_eq!(mtime, want);
    assert!(incoming_files(&env).is_empty());
    assert_eq!(count(&env, "SELECT COUNT(*) FROM blobs"), 1);
    assert_eq!(count(&env, "SELECT COUNT(*) FROM blob_occurrences"), 1);
    assert_eq!(
        count(
            &env,
            "SELECT COUNT(*) FROM uploads WHERE state = 'verified'"
        ),
        1
    );

    // ingest: header, driver, setup observation, discovery and archive queued
    coach_server::ingest::ingest_pending(&env.app)
        .await
        .unwrap();
    assert_eq!(
        count(
            &env,
            "SELECT COUNT(*) FROM recordings WHERE ingest_state = 'parsed' AND customer_id = 1001"
        ),
        1
    );
    assert_eq!(
        count(
            &env,
            "SELECT COUNT(*) FROM drivers WHERE customer_id = 1001 AND display_name = 'Joshua Granger'"
        ),
        1
    );
    assert_eq!(count(&env, "SELECT COUNT(*) FROM setups"), 1);
    assert_eq!(
        count(
            &env,
            "SELECT COUNT(*) FROM setup_observations WHERE source = 'recording' AND is_modified = 0"
        ),
        1
    );
    assert_eq!(
        count(
            &env,
            "SELECT COUNT(*) FROM jobs WHERE kind = 'discover' AND state = 'queued'"
        ),
        1
    );
    assert_eq!(
        count(
            &env,
            "SELECT COUNT(*) FROM jobs WHERE kind = 'archive' AND state = 'queued'"
        ),
        1
    );
    // a second pass changes nothing
    coach_server::ingest::ingest_pending(&env.app)
        .await
        .unwrap();
    assert_eq!(count(&env, "SELECT COUNT(*) FROM setup_observations"), 1);
}

#[tokio::test]
async fn future_source_mtime_is_clamped_to_ten_seconds_ago() {
    let env = Env::new().await;
    let bytes = recording(8002, "future", 2_000);
    let sha = sha256_hex(&bytes);
    let created = env
        .post(
            "/api/v1/uploads",
            json!({"sha256": sha, "size": bytes.len(), "kind": "ibt", "file_name": "f.ibt",
                   "source_mtime": "2099-01-01T00:00:00Z", "collector_id": COLLECTOR}),
        )
        .await;
    assert_eq!(created.status, StatusCode::CREATED);
    let done = env.put_chunk(&sha, bytes.len(), 0, &bytes).await;
    assert_eq!(done.json()["state"], "verified");
    let mtime = std::fs::metadata(env.data().join("spool/ready").join(&sha))
        .unwrap()
        .modified()
        .unwrap();
    let age = SystemTime::now().duration_since(mtime).unwrap();
    assert!(
        age >= Duration::from_secs(9) && age < Duration::from_secs(60),
        "age {age:?}"
    );
}

#[tokio::test]
async fn resume_head_and_idempotent_create() {
    let env = Env::new().await;
    let bytes = recording(8003, "resume", 9_000);
    let sha = sha256_hex(&bytes);
    assert_eq!(
        env.create_upload(&sha, bytes.len(), "ibt", "r.ibt", None, None)
            .await
            .status,
        StatusCode::CREATED
    );
    let first = env.put_chunk(&sha, bytes.len(), 0, &bytes[..4096]).await;
    assert_eq!(first.json()["offset"], 4096);
    // HEAD reports the durable offset
    let head = env
        .call(
            axum::http::Method::HEAD,
            &format!("/api/v1/uploads/{sha}"),
            None,
            &[],
        )
        .await;
    assert_eq!(head.status, StatusCode::OK);
    assert_eq!(head.header("upload-offset").as_deref(), Some("4096"));
    assert_eq!(head.header("upload-state").as_deref(), Some("open"));
    // create again resumes
    let again = env
        .create_upload(&sha, bytes.len(), "ibt", "r.ibt", None, None)
        .await;
    assert_eq!(again.status, StatusCode::OK);
    assert_eq!(again.json()["offset"], 4096);
    let rest = env.put_chunk(&sha, bytes.len(), 4096, &bytes[4096..]).await;
    assert_eq!(rest.json()["state"], "verified");
    // create after completion: the server already has it
    let done = env
        .create_upload(&sha, bytes.len(), "ibt", "r.ibt", None, None)
        .await;
    assert_eq!(done.status, StatusCode::OK);
    assert_eq!(done.json()["state"], "verified");
    assert_eq!(count(&env, "SELECT COUNT(*) FROM blobs"), 1);
    let unknown = env
        .call(
            axum::http::Method::HEAD,
            &format!("/api/v1/uploads/{}", sha256_hex(b"none")),
            None,
            &[],
        )
        .await;
    assert_eq!(unknown.status, StatusCode::NOT_FOUND);
}

#[tokio::test]
async fn gaps_overlaps_and_identical_resends() {
    let env = Env::new().await;
    let bytes = recording(8004, "gap", 9_000);
    let sha = sha256_hex(&bytes);
    env.create_upload(&sha, bytes.len(), "ibt", "g.ibt", None, None)
        .await;
    env.put_chunk(&sha, bytes.len(), 0, &bytes[..4000]).await;
    // a gap returns 409 with the offset to continue from
    let gap = env
        .put_chunk(&sha, bytes.len(), 5000, &bytes[5000..6000])
        .await;
    assert_eq!(gap.status, StatusCode::CONFLICT);
    assert_eq!(gap.json()["offset"], 4000);
    // an identical resend of a durable range is a no-op
    let same = env
        .put_chunk(&sha, bytes.len(), 1000, &bytes[1000..2000])
        .await;
    assert_eq!(same.status, StatusCode::OK);
    assert_eq!(same.json()["offset"], 4000);
    // different bytes at a durable range conflict
    let other = vec![7u8; 1000];
    let wrong = env.put_chunk(&sha, bytes.len(), 1000, &other).await;
    assert_eq!(wrong.status, StatusCode::CONFLICT);
    assert_eq!(wrong.json()["offset"], 4000);
    assert_eq!(durable_offset(&env, &sha), 4000);
}

#[tokio::test]
async fn whole_file_hash_mismatch_discards_and_restarts() {
    let env = Env::new().await;
    let good = recording(8005, "good", 6_000);
    let sha = sha256_hex(&good);
    let mut bad = good.clone();
    bad[100] ^= 0xFF;
    env.create_upload(&sha, bad.len(), "ibt", "m.ibt", None, None)
        .await;
    env.put_chunk(&sha, bad.len(), 0, &bad[..3000]).await;
    let last = env.put_chunk(&sha, bad.len(), 3000, &bad[3000..]).await;
    assert_eq!(last.status, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(last.json()["error"], "hash_mismatch");
    assert!(incoming_files(&env).is_empty());
    assert_eq!(durable_offset(&env, &sha), 0);
    assert_eq!(count(&env, "SELECT COUNT(*) FROM blobs"), 0);
    // restarting from 0 with the right bytes succeeds
    let again = env.upload(&good, "ibt", "m.ibt", None, 3000).await;
    assert_eq!(again.json()["state"], "verified");
}

#[tokio::test]
async fn oversize_upload_is_413() {
    let env = Env::new().await;
    let reply = env
        .post(
            "/api/v1/uploads",
            json!({"sha256": sha256_hex(b"big"), "size": 4_294_967_297u64, "kind": "ibt", "file_name": "big.ibt", "collector_id": COLLECTOR}),
        )
        .await;
    assert_eq!(reply.status, StatusCode::PAYLOAD_TOO_LARGE);
}

#[tokio::test]
async fn full_spool_and_low_disk_answer_503_with_retry_after() {
    let env = Env::with(Opts {
        tweak: Box::new(|c| c.spool_limit_bytes = 5_000),
        ..Default::default()
    })
    .await;
    let first = recording(8006, "one", 4_000);
    assert_eq!(
        env.upload(&first, "ibt", "one.ibt", None, 4000)
            .await
            .json()["state"],
        "verified"
    );
    let second = recording(8007, "two", 4_000);
    let reply = env
        .create_upload(
            &sha256_hex(&second),
            second.len(),
            "ibt",
            "two.ibt",
            None,
            None,
        )
        .await;
    assert_eq!(reply.status, StatusCode::SERVICE_UNAVAILABLE);
    assert!(reply.header("retry-after").is_some());

    let env = Env::with(Opts {
        tweak: Box::new(|c| c.min_free_bytes = u64::MAX),
        ..Default::default()
    })
    .await;
    let reply = env
        .create_upload(
            &sha256_hex(&second),
            second.len(),
            "ibt",
            "two.ibt",
            None,
            None,
        )
        .await;
    assert_eq!(reply.status, StatusCode::SERVICE_UNAVAILABLE);
}

#[tokio::test]
async fn enospc_on_a_chunk_returns_503_and_keeps_the_offset() {
    let env = Env::new().await;
    let bytes = recording(8008, "full", 9_000);
    let sha = sha256_hex(&bytes);
    env.create_upload(&sha, bytes.len(), "ibt", "full.ibt", None, None)
        .await;
    env.put_chunk(&sha, bytes.len(), 0, &bytes[..4000]).await;
    env.faults.arm(
        "upload.after_write",
        FaultMode::Io(std::io::ErrorKind::StorageFull),
    );
    let failed = env
        .put_chunk(&sha, bytes.len(), 4000, &bytes[4000..8000])
        .await;
    assert_eq!(failed.status, StatusCode::SERVICE_UNAVAILABLE);
    assert!(failed.header("retry-after").is_some());
    assert_eq!(durable_offset(&env, &sha), 4000);
    // the part was truncated back: nothing unacknowledged is held
    assert_eq!(
        std::fs::metadata(
            env.data()
                .join("spool/incoming")
                .join(format!("{sha}.part"))
        )
        .unwrap()
        .len(),
        4000
    );
    let retry = env
        .put_chunk(&sha, bytes.len(), 4000, &bytes[4000..8000])
        .await;
    assert_eq!(retry.json()["offset"], 8000);
    let done = env.put_chunk(&sha, bytes.len(), 8000, &bytes[8000..]).await;
    assert_eq!(done.json()["state"], "verified");
}

#[tokio::test]
async fn enospc_during_the_commit_returns_503_without_advancing_the_offset() {
    let env = Env::new().await;
    let bytes = recording(8009, "commit", 6_000);
    let sha = sha256_hex(&bytes);
    env.create_upload(&sha, bytes.len(), "ibt", "c.ibt", None, None)
        .await;
    env.put_chunk(&sha, bytes.len(), 0, &bytes[..3000]).await;
    env.faults.arm(
        "commit.before_tx",
        FaultMode::Io(std::io::ErrorKind::StorageFull),
    );
    let failed = env.put_chunk(&sha, bytes.len(), 3000, &bytes[3000..]).await;
    assert_eq!(failed.status, StatusCode::SERVICE_UNAVAILABLE);
    assert!(failed.header("retry-after").is_some());
    assert_eq!(durable_offset(&env, &sha), 3000, "offset must not advance");
    assert_eq!(count(&env, "SELECT COUNT(*) FROM blobs"), 0);
    // the retry settles the renamed file: one blob, verified
    let retry = env
        .create_upload(&sha, bytes.len(), "ibt", "c.ibt", None, None)
        .await;
    assert_eq!(retry.status, StatusCode::OK);
    assert_eq!(retry.json()["state"], "verified");
    assert_eq!(count(&env, "SELECT COUNT(*) FROM blobs"), 1);
    assert_eq!(count(&env, "SELECT COUNT(*) FROM recordings"), 1);
    assert_eq!(ready_files(&env), vec![sha.clone()]);
}

#[tokio::test]
async fn two_concurrent_chunks_for_one_hash_second_gets_409() {
    let env = Env::new().await;
    let bytes = recording(8010, "race", 6_000);
    let sha = sha256_hex(&bytes);
    env.create_upload(&sha, bytes.len(), "ibt", "x.ibt", None, None)
        .await;
    let guard = env.app.upload_locks.try_lock(&sha).expect("lock free");
    let blocked = env.put_chunk(&sha, bytes.len(), 0, &bytes[..3000]).await;
    assert_eq!(blocked.status, StatusCode::CONFLICT);
    assert_eq!(blocked.json()["offset"], 0);
    drop(guard);
    let ok = env.put_chunk(&sha, bytes.len(), 0, &bytes[..3000]).await;
    assert_eq!(ok.status, StatusCode::OK);

    // two requests really in flight at once: exactly one wins the chunk, the other is told the offset
    let (a, b) = tokio::join!(
        env.put_chunk(&sha, bytes.len(), 3000, &bytes[3000..]),
        env.put_chunk(&sha, bytes.len(), 3000, &bytes[3000..])
    );
    let states: Vec<_> = [&a, &b]
        .iter()
        .map(|r| (r.status, r.json()["state"].clone()))
        .collect();
    assert!(
        states
            .iter()
            .any(|(s, v)| *s == StatusCode::OK && v == "verified"),
        "{states:?}"
    );
    assert_eq!(count(&env, "SELECT COUNT(*) FROM blobs"), 1);
}

#[tokio::test]
async fn duplicate_sto_under_a_new_name_records_an_occurrence() {
    let env = Env::new().await;
    // the driver must exist for setup files to attach
    let recording_bytes = recording(8011, "driver", 3_000);
    env.upload(&recording_bytes, "ibt", "d.ibt", None, 3000)
        .await;
    coach_server::ingest::ingest_pending(&env.app)
        .await
        .unwrap();

    let sto = b"encrypted setup bytes".to_vec();
    let sha = sha256_hex(&sto);
    let one = env
        .upload(
            &sto,
            "sto",
            "baseline.sto",
            Some("stockcars chevyss2020/baseline.sto"),
            4096,
        )
        .await;
    assert_eq!(one.json()["state"], "verified");
    let two = env
        .create_upload(
            &sha,
            sto.len(),
            "sto",
            "copy of baseline.sto",
            Some("stockcars chevyss2020/sub/copy of baseline.sto"),
            None,
        )
        .await;
    assert_eq!(two.status, StatusCode::OK);
    assert_eq!(
        count(&env, "SELECT COUNT(*) FROM blobs WHERE kind = 'sto'"),
        1
    );
    assert_eq!(
        count(
            &env,
            &format!("SELECT COUNT(*) FROM blob_occurrences WHERE sha256 = '{sha}'")
        ),
        2
    );
    assert_eq!(count(&env, "SELECT COUNT(*) FROM setup_files"), 2);
    // creating the same occurrence again adds nothing
    env.create_upload(
        &sha,
        sto.len(),
        "sto",
        "copy of baseline.sto",
        Some("stockcars chevyss2020/sub/copy of baseline.sto"),
        None,
    )
    .await;
    assert_eq!(
        count(
            &env,
            &format!("SELECT COUNT(*) FROM blob_occurrences WHERE sha256 = '{sha}'")
        ),
        2
    );
    assert_eq!(count(&env, "SELECT COUNT(*) FROM setup_files"), 2);
}

// ------------------------------------------------------------ crash cuts

async fn crash_cycle(point: &str) {
    let env = Env::new().await;
    // the driver exists so the .sto-free path stays simple
    let bytes = recording(8100, point, 10_000);
    let sha = sha256_hex(&bytes);
    env.faults.arm(point, FaultMode::Crash);
    let reply = env.upload(&bytes, "ibt", "crash.ibt", None, 4096).await;
    assert_eq!(
        reply.status,
        StatusCode::INTERNAL_SERVER_ERROR,
        "{point}: the crash must interrupt the upload"
    );
    assert_eq!(reply.json()["error"], "crash", "{point}");
    assert!(env.faults.hits(point) >= 1);

    // the process dies here; restart reconciles before serving
    let env = env.restart().await;
    let done = env.upload(&bytes, "ibt", "crash.ibt", None, 4096).await;
    assert_eq!(
        done.status,
        StatusCode::OK,
        "{point}: {}",
        String::from_utf8_lossy(&done.body)
    );
    assert_ne!(done.json()["state"], "open", "{point}");
    env.settle_ingest_only().await;

    assert_eq!(
        count(&env, "SELECT COUNT(*) FROM blobs"),
        1,
        "{point}: no loss, no duplicates"
    );
    assert_eq!(count(&env, "SELECT COUNT(*) FROM recordings"), 1, "{point}");
    assert_eq!(
        count(
            &env,
            "SELECT COUNT(*) FROM setup_observations WHERE source = 'recording'"
        ),
        1,
        "{point}"
    );
    assert_eq!(
        count(
            &env,
            "SELECT COUNT(*) FROM uploads WHERE state = 'verified'"
        ),
        1,
        "{point}"
    );
    assert_eq!(
        count(&env, "SELECT COUNT(*) FROM blob_occurrences"),
        1,
        "{point}"
    );
    assert_eq!(ready_files(&env), vec![sha.clone()], "{point}");
    assert_eq!(
        std::fs::read(env.data().join("spool/ready").join(&sha)).unwrap(),
        bytes,
        "{point}"
    );
    assert!(incoming_files(&env).is_empty(), "{point}: no .part left");
    assert_eq!(
        count(&env, "SELECT COUNT(*) FROM jobs WHERE kind = 'archive'"),
        1,
        "{point}"
    );
}

impl Env {
    async fn settle_ingest_only(&self) {
        coach_server::ingest::ingest_pending(&self.app)
            .await
            .expect("ingest");
    }
}

#[tokio::test]
async fn crash_at_every_step_of_the_upload_protocol_loses_nothing() {
    for point in UPLOAD_POINTS {
        crash_cycle(point).await;
    }
}

fn job_row(env: &Env, key: &str) -> JobRow {
    let key = key.to_string();
    env.query(move |c| {
        c.query_row(
            "SELECT job_id, kind, lane, customer_id, subject_json, attempts FROM jobs WHERE idempotency_key = ?1",
            [key],
            |r| {
                let subject: String = r.get(4)?;
                Ok(JobRow {
                    job_id: r.get(0)?,
                    kind: r.get(1)?,
                    lane: r.get(2)?,
                    customer_id: r.get(3)?,
                    subject: serde_json::from_str(&subject).unwrap(),
                    attempts: r.get(5)?,
                })
            },
        )
    })
}

async fn run_io_lane(env: &Env) {
    while coach_server::jobs::run_next(&env.app, "io").await.unwrap() {}
}

fn manifest_valid(manifest: &Value) {
    let schema: Value = serde_json::from_str(
        &std::fs::read_to_string(
            std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("../docs/08-rewrite/contracts/manifest.schema.json"),
        )
        .unwrap(),
    )
    .unwrap();
    let validator = jsonschema::validator_for(&schema).unwrap();
    let errors: Vec<String> = validator
        .iter_errors(manifest)
        .map(|e| e.to_string())
        .collect();
    assert!(
        errors.is_empty(),
        "manifest invalid: {errors:?}\n{manifest}"
    );
}

fn nas_object(env: &Env, sha: &str) -> (std::path::PathBuf, std::path::PathBuf) {
    let dir = env.nas().join("raw").join(&sha[..2]);
    (
        dir.join(format!("{sha}.ibt")),
        dir.join(format!("{sha}.manifest.json")),
    )
}

async fn archive_cycle(point: &str) {
    let env = Env::new().await;
    let bytes = recording(8200, point, 70_000);
    let sha = sha256_hex(&bytes);
    env.upload(&bytes, "ibt", "arch.ibt", None, 32 * 1024).await;
    coach_server::ingest::ingest_pending(&env.app)
        .await
        .unwrap();
    let job = job_row(&env, &format!("archive:{sha}"));
    env.faults.arm(point, FaultMode::Crash);
    let outcome =
        coach_server::handlers::run(&env.app, &job, &coach_server::worker::Cancel::default()).await;
    match outcome {
        coach_server::jobs::JobOutcome::Failed { code, .. } => assert_eq!(code, "crash", "{point}"),
        other => panic!("{point}: expected the crash to stop the archive, got {other:?}"),
    }
    assert!(env.faults.hits(point) >= 1);

    let env = env.restart().await;
    run_io_lane(&env).await;
    let (data, manifest) = nas_object(&env, &sha);
    assert_eq!(std::fs::read(&data).unwrap(), bytes, "{point}: NAS copy");
    manifest_valid(&serde_json::from_slice(&std::fs::read(&manifest).unwrap()).unwrap());
    assert_eq!(
        count(
            &env,
            &format!(
                "SELECT COUNT(*) FROM blobs WHERE sha256 = '{sha}' AND nas_state = 'archived'"
            )
        ),
        1,
        "{point}"
    );
    let leftovers: Vec<_> = std::fs::read_dir(env.nas().join(".tmp")).unwrap().collect();
    assert!(
        leftovers.is_empty(),
        "{point}: temp files left {leftovers:?}"
    );
    let raw_count = std::fs::read_dir(env.nas().join("raw").join(&sha[..2]))
        .unwrap()
        .count();
    assert_eq!(
        raw_count, 2,
        "{point}: exactly the data file and its manifest"
    );
    assert_eq!(
        std::fs::read_dir(env.nas().join("quarantine"))
            .unwrap()
            .count(),
        0,
        "{point}"
    );
}

#[tokio::test]
async fn crash_at_every_step_of_the_archive_protocol_loses_nothing() {
    for point in ARCHIVE_POINTS {
        archive_cycle(point).await;
    }
}

#[tokio::test]
async fn archived_recording_releases_its_spool_copy_once_no_job_needs_it() {
    let env = Env::new().await;
    let bytes = recording(8201, "release", 5_000);
    let sha = sha256_hex(&bytes);
    env.upload(&bytes, "ibt", "rel.ibt", None, 5000).await;
    coach_server::ingest::ingest_pending(&env.app)
        .await
        .unwrap();
    // the queued discovery still needs the spool copy
    run_io_lane(&env).await;
    assert!(env.data().join("spool/ready").join(&sha).exists());
    assert_eq!(count(&env, "SELECT spool_present FROM blobs"), 1);
    // once discovery is no longer pending, releasing frees the spool but keeps the NAS copy
    env.app
        .store
        .write_blocking(|c| {
            c.execute(
                "UPDATE jobs SET state = 'cancelled' WHERE kind = 'discover'",
                [],
            )?;
            Ok(())
        })
        .unwrap();
    assert!(
        coach_server::handlers::release_spool(&env.app, &sha)
            .await
            .unwrap()
    );
    assert!(!env.data().join("spool/ready").join(&sha).exists());
    assert_eq!(count(&env, "SELECT spool_present FROM blobs"), 0);
    assert!(nas_object(&env, &sha).0.exists());
}

#[tokio::test]
async fn archiver_quarantines_a_wrong_final_file_and_rewrites() {
    let env = Env::new().await;
    let bytes = recording(8202, "quarantine", 4_000);
    let sha = sha256_hex(&bytes);
    env.upload(&bytes, "ibt", "q.ibt", None, 4000).await;
    coach_server::ingest::ingest_pending(&env.app)
        .await
        .unwrap();
    let (data, manifest) = nas_object(&env, &sha);
    std::fs::create_dir_all(data.parent().unwrap()).unwrap();
    std::fs::write(&data, b"corrupt final file").unwrap();
    run_io_lane(&env).await;
    assert_eq!(std::fs::read(&data).unwrap(), bytes);
    assert!(manifest.exists());
    assert_eq!(
        std::fs::read_dir(env.nas().join("quarantine"))
            .unwrap()
            .count(),
        1
    );
    assert_eq!(
        std::fs::read_to_string(env.nas().join("FORMAT")).unwrap(),
        "iracing-coach-nas 1\n"
    );
}

// -------------------------------------------------- reconciliation table

fn put_upload_row(env: &Env, sha: &str, size: usize, offset: i64, state: &str) {
    let (sha, state) = (sha.to_string(), state.to_string());
    env.app
        .store
        .write_blocking(move |c| {
            c.execute(
                "INSERT OR IGNORE INTO collectors(collector_id, machine_name, collector_version, protocol, first_seen_at, last_seen_at)
                 VALUES (?1, 'x', '0', 1, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
                [COLLECTOR],
            )?;
            c.execute(
                "INSERT INTO uploads(sha256, size, kind, file_name, source_mtime, collector_id, durable_offset, state, created_at, updated_at)
                 VALUES (?1, ?2, 'ibt', 'x.ibt', '2026-08-01T12:30:00Z', ?3, ?4, ?5, ?6, ?6)",
                params![sha, size as i64, COLLECTOR, offset, state, coach_server::util::iso_now()],
            )?;
            Ok(())
        })
        .unwrap();
}

#[tokio::test]
async fn reconciliation_table_every_row() {
    let env = Env::new().await;
    let data = env.data();

    // row 1: ready + blob row -> nothing
    let a = recording(8301, "row1", 3_000);
    let sha_a = sha256_hex(&a);
    env.upload(&a, "ibt", "a.ibt", None, 3000).await;

    // row 2a: ready, no blob row, equal hash -> blob inserted, upload verified, ingest queued
    let b = recording(8302, "row2a", 3_000);
    let sha_b = sha256_hex(&b);
    std::fs::write(data.join("spool/ready").join(&sha_b), &b).unwrap();
    put_upload_row(&env, &sha_b, b.len(), 3000, "open");

    // row 2b: ready, no blob row, different bytes -> removed, upload back to open at 0
    let c = recording(8303, "row2b", 3_000);
    let sha_c = sha256_hex(&c);
    std::fs::write(
        data.join("spool/ready").join(&sha_c),
        b"not the announced bytes",
    )
    .unwrap();
    put_upload_row(&env, &sha_c, c.len(), 3000, "open");

    // row 3: part + open upload -> offset = part length
    let d = recording(8304, "row3", 8_000);
    let sha_d = sha256_hex(&d);
    std::fs::write(
        data.join("spool/incoming").join(format!("{sha_d}.part")),
        &d[..5000],
    )
    .unwrap();
    put_upload_row(&env, &sha_d, d.len(), 3000, "open");

    // row 4: part of exactly size + open upload -> committed
    let e = recording(8305, "row4", 3_000);
    let sha_e = sha256_hex(&e);
    std::fs::write(
        data.join("spool/incoming").join(format!("{sha_e}.part")),
        &e,
    )
    .unwrap();
    put_upload_row(&env, &sha_e, e.len(), 2000, "open");

    // row 5: part, no upload row -> deleted
    let f = recording(8306, "row5", 3_000);
    let sha_f = sha256_hex(&f);
    std::fs::write(
        data.join("spool/incoming").join(format!("{sha_f}.part")),
        &f[..1000],
    )
    .unwrap();

    // row 6: no files, upload open with offset > 0 -> reset to 0
    let g = recording(8307, "row6", 3_000);
    let sha_g = sha256_hex(&g);
    put_upload_row(&env, &sha_g, g.len(), 1500, "open");

    // row 7: blob row with spool_present = 1 but no file -> spool_present = 0
    let h = recording(8308, "row7", 3_000);
    let sha_h = sha256_hex(&h);
    env.upload(&h, "ibt", "h.ibt", None, 3000).await;
    std::fs::remove_file(data.join("spool/ready").join(&sha_h)).unwrap();

    let env = env.restart().await;
    let blob = |sha: &str| {
        count(
            &env,
            &format!("SELECT COUNT(*) FROM blobs WHERE sha256 = '{sha}'"),
        )
    };
    let offset = |sha: &str| durable_offset(&env, sha);
    let state = |sha: &str| {
        let sha = sha.to_string();
        env.query(move |c| {
            c.query_row("SELECT state FROM uploads WHERE sha256 = ?1", [sha], |r| {
                r.get::<_, String>(0)
            })
        })
    };

    assert_eq!(blob(&sha_a), 1, "row 1");
    assert_eq!(blob(&sha_b), 1, "row 2a");
    assert_eq!(state(&sha_b), "verified", "row 2a");
    assert_eq!(
        count(
            &env,
            &format!(
                "SELECT COUNT(*) FROM recordings WHERE sha256 = '{sha_b}' AND ingest_state = 'verified'"
            )
        ),
        1,
        "row 2a ingest queued"
    );
    assert_eq!(blob(&sha_c), 0, "row 2b");
    assert!(
        !data.join("spool/ready").join(&sha_c).exists(),
        "row 2b: bad file deleted"
    );
    assert_eq!(offset(&sha_c), 0, "row 2b");
    assert_eq!(state(&sha_c), "open", "row 2b");
    assert_eq!(offset(&sha_d), 5000, "row 3");
    assert!(
        data.join("spool/incoming")
            .join(format!("{sha_d}.part"))
            .exists(),
        "row 3 keeps the part"
    );
    assert_eq!(blob(&sha_e), 1, "row 4");
    assert_eq!(state(&sha_e), "verified", "row 4");
    assert!(
        !data
            .join("spool/incoming")
            .join(format!("{sha_f}.part"))
            .exists(),
        "row 5"
    );
    assert_eq!(offset(&sha_g), 0, "row 6");
    assert_eq!(
        count(
            &env,
            &format!("SELECT spool_present FROM blobs WHERE sha256 = '{sha_h}'")
        ),
        0,
        "row 7"
    );

    // a torn tail is caught by the whole-file hash and restarts from 0
    let torn = recording(8309, "torn", 6_000);
    let sha_t = sha256_hex(&torn);
    let mut tail_bad = torn.clone();
    tail_bad[5990] ^= 0x55;
    env.create_upload(&sha_t, torn.len(), "ibt", "t.ibt", None, None)
        .await;
    std::fs::write(
        data.join("spool/incoming").join(format!("{sha_t}.part")),
        &tail_bad,
    )
    .unwrap();
    let env = env.restart().await;
    assert_eq!(
        count(
            &env,
            &format!("SELECT COUNT(*) FROM blobs WHERE sha256 = '{sha_t}'")
        ),
        0
    );
    assert_eq!(durable_offset(&env, &sha_t), 0);
    assert!(
        !data
            .join("spool/incoming")
            .join(format!("{sha_t}.part"))
            .exists()
    );
}

#[tokio::test]
async fn nas_state_is_reported_and_archive_retries_use_backoff() {
    let env = Env::new().await;
    let status = env.get("/api/v1/status").await.json();
    assert_eq!(status["nas"]["state"], "ok");
    // an archive that fails (NAS root is a file) leaves the blob pending and retryable
    let bytes = recording(8400, "nasdown", 3_000);
    let sha = sha256_hex(&bytes);
    env.upload(&bytes, "ibt", "n.ibt", None, 3000).await;
    coach_server::ingest::ingest_pending(&env.app)
        .await
        .unwrap();
    env.faults.arm(
        "archive.after_tmp_create",
        FaultMode::Io(std::io::ErrorKind::TimedOut),
    );
    run_io_lane(&env).await;
    assert_eq!(
        count(
            &env,
            &format!(
                "SELECT COUNT(*) FROM blobs WHERE sha256 = '{sha}' AND nas_state = 'failed' AND nas_attempts = 1"
            )
        ),
        1
    );
    assert_eq!(
        env.get("/api/v1/status").await.json()["nas"]["state"],
        "unreachable"
    );
    let now = coach_server::util::unix_now();
    // not due yet: one minute of backoff has not passed
    assert_eq!(
        coach_server::scheduler::retry_archives(&env.app, now)
            .await
            .unwrap(),
        0
    );
    assert_eq!(
        coach_server::scheduler::retry_archives(&env.app, now + 120)
            .await
            .unwrap(),
        1
    );
    run_io_lane(&env).await;
    assert_eq!(
        count(
            &env,
            &format!(
                "SELECT COUNT(*) FROM blobs WHERE sha256 = '{sha}' AND nas_state = 'archived'"
            )
        ),
        1
    );
    assert_eq!(env.get("/api/v1/status").await.json()["nas"]["state"], "ok");
}

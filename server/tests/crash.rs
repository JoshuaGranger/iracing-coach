//! Crash tests that end a real process. The server runs in a child process (this test binary,
//! started again on `crash_child`) over directories the parent owns; an armed fault point exits
//! it on the spot with `KILLED_EXIT`, as `kill -9` would: no destructor runs and nothing is
//! flushed that the protocol did not flush itself. The parent then starts the server over the
//! same directories and, before any retransmission, checks that everything the child had
//! acknowledged is still there (`collector-protocol.md` section 4, `nas-layout.md` section 3).

mod support;

use std::path::Path;
use std::process::Command;

use axum::http::{Method, StatusCode};
use coach_server::faults::{ARCHIVE_POINTS, FaultMode, KILLED_EXIT, UPLOAD_POINTS};
use coach_server::jobs::JobRow;
use coach_server::util::sha256_hex;
use support::*;

const CHUNK: usize = 16 * 1024;

fn recording() -> Vec<u8> {
    fake_ibt(race_meta(8100, 0, "crash"), 70_000)
}

// ------------------------------------------------------------------ the child

/// Runs only when the parent started this binary for it (`COACH_CRASH_CHILD=<mode>|<dir>|<point>`).
#[tokio::test]
async fn crash_child() {
    let Ok(spec) = std::env::var("COACH_CRASH_CHILD") else {
        return;
    };
    let parts: Vec<&str> = spec.splitn(3, '|').collect();
    let (mode, dir, point) = (parts[0], Path::new(parts[1]), parts[2]);
    let bytes = recording();
    let sha = sha256_hex(&bytes);
    match mode {
        "upload" => {
            let env = Env::over(dir, Some((point, FaultMode::Kill))).await;
            let created = env
                .create_upload(&sha, bytes.len(), "ibt", "crash.ibt", None, None)
                .await;
            println!("CREATE {}", created.status.as_u16());
            let mut offset = created.json()["offset"].as_u64().unwrap_or(0) as usize;
            while offset < bytes.len() {
                let end = (offset + CHUNK).min(bytes.len());
                let reply = env
                    .put_chunk(&sha, bytes.len(), offset, &bytes[offset..end])
                    .await;
                assert_eq!(reply.status, StatusCode::OK);
                if reply.json()["state"] == "verified" {
                    println!("VERIFIED");
                } else {
                    println!("ACK {end}");
                }
                offset = end;
            }
        }
        "archive" => {
            let env = Env::over(dir, None).await;
            env.upload(&bytes, "ibt", "crash.ibt", None, CHUNK).await;
            coach_server::ingest::ingest_pending(&env.app)
                .await
                .unwrap();
            let job = archive_job(&env, &sha);
            env.faults.arm(point, FaultMode::Kill);
            let outcome = coach_server::handlers::run(
                &env.app,
                &job,
                &coach_server::worker::Cancel::default(),
            )
            .await;
            println!("ARCHIVE_RETURNED {outcome:?}");
        }
        other => panic!("unknown crash mode {other}"),
    }
    println!("CHILD_DONE");
}

fn archive_job(env: &Env, sha: &str) -> JobRow {
    let key = format!("archive:{sha}");
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

// ----------------------------------------------------------------- the parent

struct ChildRun {
    killed: bool,
    stdout: String,
}

fn run_child(mode: &str, dir: &Path, point: &str) -> ChildRun {
    let exe = std::env::current_exe().expect("test binary");
    let out = Command::new(exe)
        .args(["crash_child", "--exact", "--nocapture", "--test-threads=1"])
        .env(
            "COACH_CRASH_CHILD",
            format!("{mode}|{}|{point}", dir.display()),
        )
        .output()
        .expect("child starts");
    ChildRun {
        killed: out.status.code() == Some(KILLED_EXIT),
        stdout: String::from_utf8_lossy(&out.stdout).into_owned(),
    }
}

fn acknowledged(stdout: &str) -> usize {
    stdout
        .lines()
        .filter_map(|l| l.strip_prefix("ACK "))
        .filter_map(|n| n.trim().parse::<usize>().ok())
        .max()
        .unwrap_or(0)
}

#[tokio::test]
async fn a_killed_server_keeps_every_acknowledged_byte_at_each_upload_cut() {
    for point in UPLOAD_POINTS {
        let temp = tempfile::tempdir().unwrap();
        let run = run_child("upload", temp.path(), point);
        assert!(
            run.killed,
            "{point}: the child must die at the cut\n{}",
            run.stdout
        );
        let acked = acknowledged(&run.stdout);
        let verified = run.stdout.contains("VERIFIED");
        let bytes = recording();
        let sha = sha256_hex(&bytes);

        // the restart reconciles before it serves; nothing is retransmitted yet
        let env = Env::over(temp.path(), None).await;
        let ready = env.data().join("spool/ready").join(&sha);
        let part = env
            .data()
            .join("spool/incoming")
            .join(format!("{sha}.part"));
        let head = env
            .call(Method::HEAD, &format!("/api/v1/uploads/{sha}"), None, &[])
            .await;
        if verified {
            assert_eq!(head.status, StatusCode::OK, "{point}");
            assert_ne!(
                head.header("upload-state").as_deref(),
                Some("open"),
                "{point}"
            );
            assert_eq!(
                std::fs::read(&ready).unwrap(),
                bytes,
                "{point}: an acknowledged `verified` recording is whole in ready/"
            );
        } else if head.status == StatusCode::OK {
            let offset: usize = head.header("upload-offset").unwrap().parse().unwrap();
            if head.header("upload-state").as_deref() == Some("open") {
                assert!(
                    offset >= acked,
                    "{point}: the server reports {offset} durable bytes but acknowledged {acked}"
                );
                let held = std::fs::read(&part).unwrap_or_default();
                assert_eq!(held.len(), offset, "{point}: the offset is the part length");
                assert_eq!(
                    held[..],
                    bytes[..offset],
                    "{point}: the held bytes are the sent ones"
                );
            } else {
                assert_eq!(std::fs::read(&ready).unwrap(), bytes, "{point}");
            }
        } else {
            assert_eq!(head.status, StatusCode::NOT_FOUND, "{point}");
            assert_eq!(
                acked, 0,
                "{point}: bytes were acknowledged but the upload is gone"
            );
        }

        // only now does the collector retransmit, from the reported offset
        let done = env.upload(&bytes, "ibt", "crash.ibt", None, CHUNK).await;
        assert_eq!(done.status, StatusCode::OK, "{point}");
        assert_ne!(done.json()["state"], "open", "{point}");
        assert_eq!(std::fs::read(&ready).unwrap(), bytes, "{point}");
        assert!(!part.exists(), "{point}: no .part left");
        let blobs: i64 = env.query(|c| c.query_row("SELECT COUNT(*) FROM blobs", [], |r| r.get(0)));
        assert_eq!(blobs, 1, "{point}: no loss, no duplicates");
    }
}

#[tokio::test]
async fn a_killed_server_never_loses_the_spool_copy_or_half_archives_at_each_archive_cut() {
    for point in ARCHIVE_POINTS {
        let temp = tempfile::tempdir().unwrap();
        let run = run_child("archive", temp.path(), point);
        assert!(
            run.killed,
            "{point}: the child must die at the cut\n{}",
            run.stdout
        );
        let bytes = recording();
        let sha = sha256_hex(&bytes);

        let env = Env::over(temp.path(), None).await;
        // the spool copy is the only durable copy until the NAS commit: it is still whole
        let ready = env.data().join("spool/ready").join(&sha);
        assert_eq!(std::fs::read(&ready).unwrap(), bytes, "{point}");
        let data = env.nas_payload(&sha);
        let manifest = env.nas_manifest(&sha);
        let state: String = env.query({
            let sha = sha.clone();
            move |c| {
                c.query_row(
                    "SELECT nas_state FROM blobs WHERE sha256 = ?1",
                    [sha],
                    |r| r.get(0),
                )
            }
        });
        if state == "archived" {
            assert!(
                data.exists() && manifest.exists(),
                "{point}: archived without its data and manifest"
            );
        }

        // the manifest waits for discovery (jobs.rs ARCHIVE_WAITS_FOR_DISCOVERY)
        while coach_server::jobs::run_next(&env.app, "light")
            .await
            .unwrap()
        {}
        while coach_server::jobs::run_next(&env.app, "io").await.unwrap() {}
        assert_eq!(std::fs::read(&data).unwrap(), bytes, "{point}: NAS copy");
        assert!(manifest.exists(), "{point}");
        let archived: i64 = env.query({
            let sha = sha.clone();
            move |c| {
                c.query_row(
                    "SELECT COUNT(*) FROM blobs WHERE sha256 = ?1 AND nas_state = 'archived'",
                    [sha],
                    |r| r.get(0),
                )
            }
        });
        assert_eq!(archived, 1, "{point}");
        assert_eq!(
            std::fs::read_dir(env.nas().join(".tmp")).unwrap().count(),
            0,
            "{point}: temp files cleaned"
        );
    }
}

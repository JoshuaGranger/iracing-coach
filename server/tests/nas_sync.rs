//! Real filesystem + SQLite integration; all payloads are synthetic and task-owned.
mod support;
use axum::http::{Method, StatusCode};
use coach_server::faults::FaultMode;
use coach_server::{nas_library, util::sha256_hex};
use rusqlite::params;
use serde_json::json;
use std::time::{Duration, SystemTime};
use support::*;

async fn passes(env: &Env) {
    for _ in 0..3 {
        nas_library::reconcile(&env.app).await.unwrap();
    }
}
fn canonical(env: &Env, sha: &str) -> std::path::PathBuf {
    let sha = sha.to_string();
    let path: String = env.query(move |c| {
        c.query_row(
            "SELECT relative_path FROM nas_library_objects WHERE sha256=?1",
            [sha],
            |r| r.get(0),
        )
    });
    env.nas().join(path)
}
fn object_state(env: &Env, sha: &str) -> String {
    let sha = sha.to_string();
    env.query(move |c| {
        c.query_row(
            "SELECT state FROM nas_library_objects WHERE sha256=?1",
            [sha],
            |r| r.get(0),
        )
    })
}
async fn owner(env: &Env) {
    env.app.store.write(|c|{coach_server::db::upsert_driver(c,CUSTOMER,Some("Synthetic driver"),None)?;c.execute("INSERT OR IGNORE INTO collectors(collector_id,machine_name,collector_version,protocol,first_seen_at,last_seen_at,last_customer_id) VALUES(?1,'fixture','fixture',1,'2026-01-01','2026-01-01',?2)",params![COLLECTOR,CUSTOMER])?;Ok(())}).await.unwrap();
}
async fn replay(env: &Env, bytes: &[u8]) -> String {
    owner(env).await;
    let sha = sha256_hex(bytes);
    assert_eq!(
        env.upload(bytes, "rpy", "test replay.rpy", None, 37)
            .await
            .status,
        StatusCode::OK
    );
    env.settle().await;
    sha
}

#[tokio::test]
async fn replay_is_archive_only_single_canonical_copy_and_releases_spool() {
    let env = Env::new().await;
    let bytes = b"synthetic replay payload";
    let sha = replay(&env, bytes).await;
    let path = canonical(&env, &sha);
    assert!(path.to_string_lossy().contains("library"));
    assert_eq!(std::fs::read(path).unwrap(), bytes);
    assert!(
        !env.nas()
            .join(format!("replays/{}/{sha}.rpy", &sha[..2]))
            .exists()
    );
    assert!(!env.data().join("spool/ready").join(&sha).exists());
    assert_eq!(
        env.query(|c| c.query_row("SELECT COUNT(*) FROM recordings", [], |r| r
            .get::<_, i64>(0))),
        0
    );
    assert_eq!(
        env.query(
            |c| c.query_row("SELECT COUNT(*) FROM setup_files", [], |r| r
                .get::<_, i64>(0))
        ),
        0
    );
    assert_eq!(
        env.query(|c| c.query_row(
            "SELECT COUNT(*) FROM jobs WHERE kind IN ('analyze','discover')",
            [],
            |r| r.get::<_, i64>(0)
        )),
        0
    );
    assert_eq!(
        env.upload(bytes, "rpy", "another name.rpy", None, 19)
            .await
            .json()["state"],
        "archived"
    );
    assert_eq!(
        env.query(
            |c| c.query_row("SELECT COUNT(*) FROM nas_library_objects", [], |r| r
                .get::<_, i64>(0))
        ),
        1
    );
}

#[tokio::test]
async fn deliberate_library_delete_is_durable_and_suppresses_create_head_and_put() {
    let env = Env::new().await;
    let bytes = b"delete only this synthetic replay";
    let sha = replay(&env, bytes).await;
    let path = canonical(&env, &sha);
    std::fs::remove_file(&path).unwrap();
    passes(&env).await;
    assert_eq!(object_state(&env, &sha), "deleted");
    assert_eq!(
        env.upload(bytes, "rpy", "test replay.rpy", None, 7)
            .await
            .json()["state"],
        "deleted"
    );
    let head = env
        .call(Method::HEAD, &format!("/api/v1/uploads/{sha}"), None, &[])
        .await;
    assert_eq!(head.headers["upload-state"], "deleted");
    assert_eq!(head.headers["upload-offset"], "0");
    assert_eq!(
        env.put_chunk(&sha, bytes.len(), 0, bytes).await.json()["state"],
        "deleted"
    );
    assert!(!path.exists());
    let env = env.restart().await;
    passes(&env).await;
    assert_eq!(object_state(&env, &sha), "deleted");
    assert!(!path.exists());
    // An explicit mismatching share restore does not lift the tombstone.
    std::fs::write(&path, b"wrong bytes").unwrap();
    for _ in 0..3 {
        let _ = nas_library::reconcile(&env.app).await;
    }
    assert_eq!(object_state(&env, &sha), "deleted");
    std::fs::write(&path, bytes).unwrap();
    passes(&env).await;
    assert_eq!(object_state(&env, &sha), "canonical_verified");
    assert_eq!(
        env.upload(bytes, "rpy", "test replay.rpy", None, 7)
            .await
            .json()["state"],
        "archived"
    );
}

#[tokio::test]
async fn missing_library_marker_pauses_without_recreation_or_false_deletion() {
    let env = Env::new().await;
    let sha = replay(&env, b"marker fixture").await;
    let marker = env.nas().join("library/.identity");
    let marker_bytes = std::fs::read(&marker).unwrap();
    std::fs::remove_file(&marker).unwrap();
    assert!(nas_library::reconcile(&env.app).await.is_err());
    assert!(!marker.exists());
    assert_eq!(object_state(&env, &sha), "canonical_verified");
    let env = env.restart().await;
    assert!(!marker.exists());
    assert!(nas_library::reconcile(&env.app).await.is_err());
    assert_eq!(object_state(&env, &sha), "canonical_verified");
    std::fs::write(marker, marker_bytes).unwrap();
    passes(&env).await;
}

#[tokio::test]
async fn missing_parent_and_mismatched_bytes_are_not_deletions() {
    let env = Env::new().await;
    let sha = replay(&env, b"parent fixture").await;
    let path = canonical(&env, &sha);
    let parent = path.parent().unwrap();
    let moved = parent.with_extension("offline");
    std::fs::rename(parent, &moved).unwrap();
    for _ in 0..3 {
        let _ = nas_library::reconcile(&env.app).await;
    }
    assert_eq!(object_state(&env, &sha), "canonical_verified");
    std::fs::rename(&moved, parent).unwrap();
    std::fs::write(&path, b"mismatching bytes").unwrap();
    for _ in 0..3 {
        let _ = nas_library::reconcile(&env.app).await;
    }
    assert_eq!(object_state(&env, &sha), "conflict");
}

#[tokio::test]
async fn quiet_inbox_is_adopted_locked_idempotent_and_delivered_only_to_persisted_driver() {
    let env = Env::new().await;
    owner(&env).await;
    // Initializes marker only; the source is then user-supplied staging.
    passes(&env).await;
    let inbox = env.nas().join(format!(
        "library/setups/{CUSTOMER}/stockcars2 supra2019/inbox/shop"
    ));
    std::fs::create_dir_all(&inbox).unwrap();
    let input = inbox.join("test.sto");
    let bytes = b"opaque synthetic STO";
    std::fs::write(&input, bytes).unwrap();
    assert_eq!(
        nas_library::reconcile(&env.app).await.unwrap()["imported"],
        0
    ); // still being written / not quiet
    std::fs::File::options()
        .write(true)
        .open(&input)
        .unwrap()
        .set_modified(SystemTime::now() - Duration::from_secs(30))
        .unwrap();
    passes(&env).await;
    let sha = sha256_hex(bytes);
    assert!(!input.exists());
    assert_eq!(std::fs::read(canonical(&env, &sha)).unwrap(), bytes);
    assert_eq!(
        env.query(
            |c| c.query_row("SELECT COUNT(*) FROM nas_inbox_receipts", [], |r| r
                .get::<_, i64>(0))
        ),
        1
    );
    let list = env
        .get(&format!("/api/v1/drivers/{CUSTOMER}/sto-files"))
        .await
        .json();
    assert_eq!(list["files"][0]["locked"], true);
    assert_eq!(
        env.query(|c| c.query_row("SELECT spool_present FROM blobs", [], |r| r
            .get::<_, i64>(0))),
        0
    );
    let job = coach_server::jobs::JobRow {
        job_id: 9999,
        kind: "setup_files".into(),
        lane: "light".into(),
        customer_id: Some(CUSTOMER),
        subject: json!({}),
        attempts: 0,
    };
    let (root, _) = coach_server::handlers::prepare_root(&env.app, &job, CUSTOMER, &[], true)
        .await
        .unwrap();
    assert_eq!(
        std::fs::read(root.join("setups/stockcars2 supra2019/NAS/shop/test.sto")).unwrap(),
        bytes,
        "NAS-only sources must reach Python job roots"
    );
    let reply = env
        .get(&format!("/api/v1/collectors/{COLLECTOR}/sync"))
        .await;
    assert_eq!(reply.status, StatusCode::OK);
    let manifest = reply.json();
    let f = &manifest["files"][0];
    assert_eq!(f["sha256"], sha);
    assert!(
        f["relative_path"]
            .as_str()
            .unwrap()
            .contains("/Coach Synced/NAS/")
    );
    assert_eq!(
        env.get(f["download_url"].as_str().unwrap())
            .await
            .body
            .as_ref(),
        bytes
    );
    assert_eq!(
        env.get("/api/v1/collectors/not-known/sync").await.json()["customer_id"],
        serde_json::Value::Null
    );
    passes(&env).await;
    assert_eq!(
        env.query(
            |c| c.query_row("SELECT COUNT(*) FROM blob_occurrences", [], |r| r
                .get::<_, i64>(0))
        ),
        1
    );
    // A duplicate user input stays in place; the app creates no second NAS payload.
    std::fs::write(&input, bytes).unwrap();
    std::fs::File::options()
        .write(true)
        .open(&input)
        .unwrap()
        .set_modified(SystemTime::now() - Duration::from_secs(30))
        .unwrap();
    passes(&env).await;
    assert!(input.exists());
    assert_eq!(
        env.query(
            |c| c.query_row("SELECT COUNT(*) FROM nas_library_objects", [], |r| r
                .get::<_, i64>(0))
        ),
        1
    );
}

#[tokio::test]
async fn pending_inbox_adoption_recovers_after_link_crash_without_duplicate_payload() {
    for cut in [
        "library.inbox_after_intent",
        "library.after_link",
        "library.after_unlink",
    ] {
        let env = Env::new().await;
        owner(&env).await;
        passes(&env).await;
        let dir = env
            .nas()
            .join(format!("library/setups/{CUSTOMER}/car/inbox"));
        std::fs::create_dir_all(&dir).unwrap();
        let input = dir.join("test.sto");
        let bytes = b"inbox crash fixture";
        std::fs::write(&input, bytes).unwrap();
        std::fs::File::options()
            .write(true)
            .open(&input)
            .unwrap()
            .set_modified(SystemTime::now() - Duration::from_secs(30))
            .unwrap();
        env.faults.arm(cut, FaultMode::Crash);
        assert!(nas_library::reconcile(&env.app).await.is_err());
        let sha = sha256_hex(bytes);
        let dest = canonical(&env, &sha);
        assert_eq!(
            env.query(
                |c| c.query_row("SELECT COUNT(*) FROM nas_inbox_pending", [], |r| r
                    .get::<_, i64>(0))
            ),
            1
        );
        if cut == "library.inbox_after_intent" {
            assert!(input.exists());
            assert!(!dest.exists());
        } else if cut == "library.after_link" {
            assert!(input.exists());
            assert!(dest.exists());
            assert!(coach_server::sys::same_file(&input, &dest).unwrap());
        } else {
            assert!(!input.exists());
            assert!(dest.exists());
        }
        let env = env.restart().await;
        passes(&env).await;
        assert!(!input.exists());
        assert_eq!(std::fs::read(dest).unwrap(), bytes);
        assert_eq!(
            env.query(
                |c| c.query_row("SELECT COUNT(*) FROM nas_inbox_receipts", [], |r| r
                    .get::<_, i64>(0))
            ),
            1
        );
    }
}

#[tokio::test]
async fn upload_kind_mismatch_and_replay_size_limit_are_rejected_before_payload() {
    let env = Env::new().await;
    let bytes = b"kind conflict fixture";
    let sha = replay(&env, bytes).await;
    assert_eq!(
        env.create_upload(
            &sha,
            bytes.len(),
            "sto",
            "x.sto",
            Some("car/x.sto"),
            Some(CUSTOMER)
        )
        .await
        .status,
        StatusCode::CONFLICT
    );
    let request = json!({"sha256":"a".repeat(64),"size":(16u64<<30)+1,"kind":"rpy","file_name":"huge.rpy","collector_id":COLLECTOR});
    assert_eq!(
        env.post("/api/v1/uploads", request).await.status,
        StatusCode::PAYLOAD_TOO_LARGE
    );
    assert_eq!(
        env.query(
            |c| c.query_row("SELECT COUNT(*) FROM setup_files", [], |r| r
                .get::<_, i64>(0))
        ),
        0
    );
}

#[test]
fn path_budgets_and_windows_reserved_names_are_not_silently_redirected() {
    for name in [
        "..", "CON.sto", "LPT0", "com¹", "conout$", "x.", "x ", "a/b",
    ] {
        assert!(!nas_library::fs::component(name, 64), "{name}");
    }
    let name = "😀".repeat(50);
    let stem = nas_library::fs::stem(&format!("{name}.sto"), 32);
    assert_eq!(stem.encode_utf16().count(), 32);
    assert!(stem.chars().all(|c| c == '😀'));
}

#[tokio::test]
async fn legacy_relocation_has_no_independent_copy_and_recovers_both_crash_cuts() {
    for cut in ["library.after_link", "library.after_unlink"] {
        let env = Env::new().await;
        let bytes = format!("legacy relocation {cut}").into_bytes();
        let sha = replay(&env, &bytes).await;
        let dest = canonical(&env, &sha);
        let legacy = env.nas().join(format!("replays/{}/{sha}.rpy", &sha[..2]));
        std::fs::create_dir_all(legacy.parent().unwrap()).unwrap();
        std::fs::rename(&dest, &legacy).unwrap();
        let identity = coach_server::sys::file_identity(&legacy).unwrap();
        let key = sha.clone();
        env.app
            .store
            .write(move |c| {
                c.execute("DELETE FROM nas_library_objects WHERE sha256=?1", [key])?;
                Ok(())
            })
            .await
            .unwrap();
        env.faults.arm(cut, FaultMode::Crash);
        assert!(nas_library::reconcile(&env.app).await.is_err());
        let dest = canonical(&env, &sha);
        assert!(dest.exists());
        assert_eq!(coach_server::sys::file_identity(&dest).unwrap(), identity);
        if cut.ends_with("link") && !cut.ends_with("unlink") {
            assert!(legacy.exists());
            assert!(coach_server::sys::same_file(&legacy, &dest).unwrap());
        }
        let env = env.restart().await;
        passes(&env).await;
        assert!(!legacy.exists());
        assert_eq!(std::fs::read(&dest).unwrap(), bytes);
        assert_eq!(object_state(&env, &sha), "canonical_verified");
    }
}

#[tokio::test]
async fn open_replay_reservations_count_whole_sizes_and_resume_reserves_no_duplicate() {
    let env = Env::new().await;
    let size = 8usize << 30;
    for hash in ["a".repeat(64), "b".repeat(64)] {
        let r = env
            .create_upload(&hash, size, "rpy", "large.rpy", None, None)
            .await;
        assert_eq!(r.status, StatusCode::CREATED, "{}", r.json());
    }
    assert_eq!(
        env.create_upload(&"a".repeat(64), size, "rpy", "large.rpy", None, None)
            .await
            .status,
        StatusCode::OK
    );
    assert_eq!(
        env.create_upload(&"c".repeat(64), size, "rpy", "large.rpy", None, None)
            .await
            .status,
        StatusCode::SERVICE_UNAVAILABLE
    );
    assert_eq!(
        env.create_upload(
            &"d".repeat(64),
            (4usize << 30) + 1,
            "ibt",
            "large.ibt",
            None,
            None
        )
        .await
        .status,
        StatusCode::PAYLOAD_TOO_LARGE
    );
    assert_eq!(
        env.query(
            |c| c.query_row("SELECT COUNT(*) FROM uploads WHERE state='open'", [], |r| r
                .get::<_, i64>(0))
        ),
        2
    );
}

#[tokio::test]
async fn setup_manifest_pages_current_versions_and_rejects_driver_changes() {
    let env = Env::new().await;
    owner(&env).await;
    passes(&env).await;
    let base = env
        .nas()
        .join(format!("library/setups/{CUSTOMER}/car/coach"));
    std::fs::create_dir_all(&base).unwrap();
    let mut rows = Vec::new();
    for n in 1..=130 {
        let bytes = format!("setup {n}").into_bytes();
        let sha = sha256_hex(&bytes);
        let relative = format!("library/setups/{CUSTOMER}/car/coach/{n}/{sha}.sto");
        let p = env.nas().join(&relative);
        std::fs::create_dir_all(p.parent().unwrap()).unwrap();
        std::fs::write(p, &bytes).unwrap();
        rows.push((n, bytes, sha, relative));
    }
    env.app.store.write(move|c|{let tx=c.transaction()?;for(n,bytes,sha,path)in rows{
        tx.execute("INSERT INTO sto_working_files(file_id,customer_id,name,name_key,car_folder,parent_file_id,parent_version,current_version,created_at,updated_at) VALUES(?1,?2,?3,?3,'car','fixture',1,1,'2026-01-01','2026-01-01')",params![n,CUSTOMER,format!("setup-{n}.sto")])?;
        tx.execute("INSERT INTO sto_revisions(file_id,version,sha256,content,document_json,created_at,message) VALUES(?1,1,?2,?3,'{}','2026-01-01','fixture')",params![n,sha,bytes])?;
        tx.execute("INSERT INTO nas_library_objects(kind,sha256,size,relative_path,state) VALUES('sto',?1,?2,?3,'canonical_verified')",params![sha,bytes.len() as i64,path])?;
    }tx.commit()?;Ok(())}).await.unwrap();
    let url = format!("/api/v1/collectors/{COLLECTOR}/sync");
    let page = env.get(&url).await.json();
    assert_eq!(page["files"].as_array().unwrap().len(), 128);
    let cursor = page["next_cursor"].as_str().unwrap();
    let next = env.get(&format!("{url}?cursor={cursor}")).await.json();
    assert_eq!(next["files"].as_array().unwrap().len(), 2);
    assert!(next["next_cursor"].is_null());
    let one = page["files"][0].clone();
    let old_name = one["relative_path"].as_str().unwrap();
    assert!(old_name.starts_with("car/Coach Synced/"));
    assert!(!old_name.contains(':'));
    env.app
        .store
        .write(|c| {
            coach_server::db::upsert_driver(c, 2002, Some("Other synthetic driver"), None)?;
            c.execute(
                "UPDATE collectors SET last_customer_id=2002 WHERE collector_id=?1",
                [COLLECTOR],
            )?;
            Ok(())
        })
        .await
        .unwrap();
    let changed = env.get(&format!("{url}?cursor={cursor}")).await;
    assert_eq!(changed.status, StatusCode::CONFLICT);
    assert_eq!(changed.json()["error"], "sync_driver_changed");
    assert!(
        env.get(&url).await.json()["files"]
            .as_array()
            .unwrap()
            .is_empty()
    );
}

#[tokio::test]
async fn canonical_metadata_rebuild_preserves_replays_paths_and_tombstones() {
    let env = Env::new().await;
    let bytes = b"rebuild synthetic replay";
    let sha = replay(&env, bytes).await;
    let path = canonical(&env, &sha);
    let metadata = env.nas_manifest(&sha);
    assert!(metadata.is_file());
    std::fs::remove_file(&path).unwrap();
    env.faults.arm(
        "archive.after_manifest_tmp",
        FaultMode::Io(std::io::ErrorKind::StorageFull),
    );
    assert!(nas_library::reconcile(&env.app).await.is_err());
    assert_eq!(object_state(&env, &sha), "deleted");
    let env = env.restart().await;
    passes(&env).await;
    let metadata_value: serde_json::Value =
        serde_json::from_slice(&std::fs::read(metadata).unwrap()).unwrap();
    assert_eq!(metadata_value["library"]["state"], "deleted");
    let temporary = tempfile::tempdir().unwrap();
    let mut cfg = env.app.cfg.clone();
    cfg.data_dir = temporary.path().join("fresh-data");
    coach_server::rebuild::rebuild_index(&cfg).unwrap();
    let store = coach_server::db::Store::open(&cfg.db_path(), "fixture").unwrap();
    let k = sha.clone();
    assert!(
        store
            .read_blocking(move |c| Ok(nas_library::deleted(c, &k)?))
            .unwrap()
    );
    let count = store
        .read_blocking(|c| {
            Ok(c.query_row("SELECT COUNT(*) FROM recordings", [], |r| {
                r.get::<_, i64>(0)
            })?)
        })
        .unwrap();
    assert_eq!(count, 0);
}

#[test]
fn pinned_nas_parents_prevent_a_late_directory_swap_from_redirecting_writes() {
    let root = tempfile::tempdir().unwrap();
    let outside = tempfile::tempdir().unwrap();
    std::fs::create_dir(root.path().join("car")).unwrap();
    let pinned = coach_server::sys::anchor_nas(root.path(), "car/payload.sto", false).unwrap();
    #[cfg(unix)]
    {
        let moved = root.path().join("old-car");
        std::fs::rename(root.path().join("car"), &moved).unwrap();
        std::os::unix::fs::symlink(outside.path(), root.path().join("car")).unwrap();
        std::fs::write(&pinned, b"owned payload").unwrap();
        assert_eq!(
            std::fs::read(moved.join("payload.sto")).unwrap(),
            b"owned payload"
        );
        assert!(!outside.path().join("payload.sto").exists());
        assert!(coach_server::sys::anchor_nas(root.path(), "car/payload.sto", false).is_err());
    }
    #[cfg(windows)]
    {
        assert!(std::fs::rename(root.path().join("car"), root.path().join("old-car")).is_err());
        std::fs::write(&pinned, b"owned payload").unwrap();
        assert!(!outside.path().join("payload.sto").exists());
    }
}

#[test]
fn server_nas_gate_requires_explicit_fresh_collector_bulk_evidence() {
    use coach_server::hub::{CollectorConn, Hub};
    let hub = Hub::default();
    assert!(hub.allows_library_work());
    let (tx, _rx) = tokio::sync::mpsc::unbounded_channel();
    let collector = |status: Option<serde_json::Value>, seen: i64| CollectorConn {
        conn_id: 1,
        tx: tx.clone(),
        machine: "fixture".into(),
        version: "fixture".into(),
        protocol: 1,
        compatible: true,
        online: true,
        last_seen: seen,
        status,
        driver: Some(CUSTOMER),
        streaming: false,
        newer_than_server: false,
        bad_frame_logged: false,
        frames_relayed: 0,
        frames_invalid: 0,
    };
    hub.register(
        COLLECTOR,
        collector(
            Some(json!({"sim":"not_running"})),
            coach_server::util::unix_now(),
        ),
    );
    assert!(!hub.allows_library_work());
    hub.register(
        COLLECTOR,
        collector(
            Some(json!({"bulk_io_allowed":true})),
            coach_server::util::unix_now(),
        ),
    );
    assert!(hub.allows_library_work());
    hub.register(
        COLLECTOR,
        collector(
            Some(json!({"bulk_io_allowed":true})),
            coach_server::util::unix_now() - 11,
        ),
    );
    hub.touch(COLLECTOR, 1); // Fresh pongs must not refresh stale status evidence.
    assert!(!hub.allows_library_work());
    hub.update_status(
        COLLECTOR,
        1,
        json!({"bulk_io_allowed":true,"driver":{"customer_id":CUSTOMER}}),
    );
    assert!(hub.allows_library_work());
    hub.register(
        COLLECTOR,
        collector(
            Some(json!({"bulk_io_allowed":false,"session":{"session_type":"Race"}})),
            coach_server::util::unix_now(),
        ),
    );
    assert!(!hub.allows_library_work());
}

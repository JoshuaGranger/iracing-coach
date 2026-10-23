//! HTTP and durability tests for STO lineages, independent of the production native codec.
//! Fixtures are synthetic JSON; the test-only codec exercises the subprocess boundary too.
mod support;

use axum::body::Bytes;
use axum::http::{Method, StatusCode};
use coach_server::util::sha256_hex;
use rusqlite::params;
use serde_json::{Value, json};
use support::*;

const ROOT: &str = "/api/v1/drivers/1001/sto-files";
const CAR: &str = "synthetic car";
const GARAGE_PROFILE: &str = "synthetic-garage-v1";

async fn environment() -> Option<Env> {
    let env = Env::with(Opts {
        tweak: Box::new(|cfg| {
            cfg.worker.scripts_dir =
                Some(std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/support"));
        }),
        ..Opts::default()
    })
    .await;
    if env.python.is_none() {
        eprintln!("skipped: no Python 3 for the storage-only STO codec");
        return None;
    }
    Some(env)
}

fn source_bytes() -> Vec<u8> {
    json!({"notes":"Original notes", "settings":{"fuelFillToKg":"10.000000","fBrakeBias":"55.0","unmappedGeometry":"-0.000100"},"unknown":{"opaque":"kept"}}).to_string().into_bytes()
}

async fn seed(
    env: &Env,
    customer: i64,
    car: &str,
    path: &str,
    bytes: &[u8],
    present: bool,
) -> String {
    let sha = sha256_hex(bytes);
    let id = format!(
        "source:{}",
        sha256_hex(json!([customer, car, path, sha]).to_string().as_bytes())
    );
    std::fs::write(env.app.cfg.spool_ready().join(&sha), bytes).unwrap();
    let (car, path, size) = (car.to_string(), path.to_string(), bytes.len() as i64);
    env.app.store.write(move |conn| {
        let tx = conn.transaction()?;
        tx.execute("INSERT OR IGNORE INTO drivers(customer_id,display_name,first_seen_at,last_seen_at) VALUES(?1,'Synthetic driver','2026-10-23T00:00:00Z','2026-10-23T00:00:00Z')", [customer])?;
        tx.execute("INSERT OR IGNORE INTO blobs(sha256,size,kind,first_name,received_at,spool_present) VALUES(?1,?2,'sto',?3,'2026-10-23T00:00:00Z',1)",params![sha,size,path])?;
        tx.execute("INSERT INTO setup_files(customer_id,blob_sha256,car_folder,relative_path,format,first_seen_at,last_seen_at,present) VALUES(?1,?2,?3,?4,'sto','2026-10-23T00:00:00Z','2026-10-23T00:00:00Z',?5)",params![customer,sha,car,path,i64::from(present)])?;
        tx.commit()?;
        Ok(())
    }).await.unwrap();
    id
}

fn expect(reply: &Reply, status: StatusCode) -> Value {
    assert_eq!(
        reply.status,
        status,
        "{}",
        String::from_utf8_lossy(&reply.body)
    );
    reply.json()
}

async fn write(env: &Env, path: &str, body: Value, key: &str, version: Option<i64>) -> Reply {
    let match_header = version.map(|n| n.to_string());
    let mut headers = vec![
        ("content-type", "application/json"),
        ("idempotency-key", key),
    ];
    if let Some(value) = &match_header {
        headers.push(("if-match", value));
    }
    env.call(
        Method::POST,
        path,
        Some(Bytes::from(body.to_string())),
        &headers,
    )
    .await
}

async fn fork(env: &Env, source: &str, name: &str) -> Value {
    expect(
        &write(
            env,
            &format!("{ROOT}/{source}/save-as"),
            json!({"name":name,"base_version":1,"changes":{}}),
            &format!("fork-{}", sha256_hex(name.as_bytes())),
            None,
        )
        .await,
        StatusCode::CREATED,
    )
}

fn generation(env: &Env) -> i64 {
    env.query(|c| {
        c.query_row(
            "SELECT value FROM server_counters WHERE name='user_data_generation'",
            [],
            |r| r.get(0),
        )
    })
}

fn value(document: &Value, name: &str) -> String {
    document["settings"]
        .as_array()
        .unwrap()
        .iter()
        .find(|field| field["name"] == name)
        .unwrap()["raw_value"]
        .as_str()
        .unwrap()
        .to_string()
}

#[tokio::test]
async fn every_imported_occurrence_is_locked_scoped_and_stable_across_restart_and_vacuum() {
    let Some(env) = environment().await else {
        return;
    };
    let bytes = source_bytes();
    let a = seed(&env, CUSTOMER, CAR, "Purchased/baseline.sto", &bytes, true).await;
    let b = seed(&env, CUSTOMER, CAR, "Archive/baseline.sto", &bytes, false).await;
    let c = seed(
        &env,
        CUSTOMER,
        "other synthetic car",
        "baseline.sto",
        &bytes,
        true,
    )
    .await;
    let other = seed(&env, 2002, CAR, "private.sto", &bytes, true).await;
    let list = expect(&env.get(ROOT).await, StatusCode::OK);
    let files = list["files"].as_array().unwrap();
    assert_eq!(files.len(), 3);
    assert!(
        files
            .iter()
            .all(|f| f["locked"] == true && f["version_count"] == 1)
    );
    assert!(files.iter().any(|f| f["id"] == b && f["present"] == false));
    assert!(files.iter().any(|f| f["name"] == "Purchased/baseline.sto"));
    assert_ne!(a, b);
    assert_ne!(a, c);
    assert_eq!(
        env.get(&format!("{ROOT}/{other}")).await.status,
        StatusCode::NOT_FOUND
    );
    assert_eq!(
        env.get(&format!("{ROOT}/{other}/download")).await.status,
        StatusCode::NOT_FOUND
    );
    env.app
        .store
        .write(|conn| {
            conn.execute_batch("VACUUM")?;
            Ok(())
        })
        .await
        .unwrap();
    let env = env.restart().await;
    assert_eq!(expect(&env.get(ROOT).await, StatusCode::OK), list);
    let detail = expect(&env.get(&format!("{ROOT}/{a}")).await, StatusCode::OK);
    assert_eq!(detail["document"]["future_optional"]["opaque"], "kept");
    assert_eq!(detail["version"], 1);
    assert_eq!(detail["unavailable_reason"], Value::Null);
    assert_eq!(
        env.get(&format!("{ROOT}/{a}?version=2")).await.status,
        StatusCode::NOT_FOUND
    );
    assert_eq!(
        env.get(&format!("{ROOT}/{a}?version=0")).await.status,
        StatusCode::BAD_REQUEST
    );
}

#[tokio::test]
async fn save_as_preserves_a_locked_original_even_when_the_first_revision_is_byte_identical() {
    let Some(env) = environment().await else {
        return;
    };
    let bytes = source_bytes();
    let source = seed(&env, CUSTOMER, CAR, "Purchased.sto", &bytes, true).await;
    let locked = write(
        &env,
        &format!("{ROOT}/{source}"),
        json!({"base_version":1,"changes":{"fuelFillToKg":"11"}}),
        "locked-save",
        Some(1),
    )
    .await;
    assert_eq!(
        expect(&locked, StatusCode::CONFLICT)["error"],
        "locked_source"
    );
    assert_eq!(generation(&env), 0);
    let own = fork(&env, &source, "My baseline.STO").await;
    assert_eq!(own["file"]["locked"], false);
    assert_eq!(own["file"]["name"], "My baseline.sto");
    assert_eq!(own["file"]["parent_file_id"], source);
    assert_eq!(own["file"]["parent_version"], 1);
    assert_eq!(own["sha256"], sha256_hex(&bytes));
    assert_eq!(generation(&env), 1);
    let files = expect(&env.get(ROOT).await, StatusCode::OK);
    assert!(
        files["files"]
            .as_array()
            .unwrap()
            .iter()
            .any(|f| f["id"] == source && f["locked"] == true),
        "An unchanged Save As must not hide its purchased original"
    );
    let original = env.get(&format!("{ROOT}/{source}/download")).await;
    assert_eq!(original.status, StatusCode::OK);
    assert_eq!(original.body.as_ref(), bytes);
    assert_eq!(
        std::fs::read(env.app.cfg.spool_ready().join(sha256_hex(&bytes))).unwrap(),
        bytes
    );
}

#[tokio::test]
async fn saves_and_restore_append_history_preserve_unknowns_and_keep_old_downloads() {
    let Some(env) = environment().await else {
        return;
    };
    let bytes = source_bytes();
    let source = seed(&env, CUSTOMER, CAR, "Purchased.sto", &bytes, true).await;
    let own = fork(&env, &source, "Race.sto").await;
    let id = own["file"]["id"].as_str().unwrap();
    let path = format!("{ROOT}/{id}");
    let edited=expect(&write(&env,&path,json!({"base_version":1,"changes":{"fuelFillToKg":"12.500"},"notes":"One deliberate change","message":"More fuel"}),"save-2",Some(1)).await,StatusCode::OK);
    assert_eq!(edited["version"], 2);
    assert_eq!(value(&edited["document"], "fuelFillToKg"), "12.500");
    assert_eq!(value(&edited["document"], "unmappedGeometry"), "-0.000100");
    assert_eq!(edited["document"]["future_optional"]["opaque"], "kept");
    assert_eq!(edited["document"]["notes"], "One deliberate change");
    assert_eq!(edited["file"]["car_folder"], CAR);
    let old = expect(&env.get(&format!("{path}?version=1")).await, StatusCode::OK);
    assert_eq!(old["file"]["current_version"], 2);
    assert_eq!(old["sha256"], own["sha256"]);
    assert_eq!(old["document"], own["document"]);
    let restored = expect(
        &write(
            &env,
            &path,
            json!({"base_version":1,"changes":{},"message":"Restore revision 1"}),
            "restore-1",
            Some(2),
        )
        .await,
        StatusCode::OK,
    );
    assert_eq!(restored["version"], 3);
    assert_eq!(restored["sha256"], own["sha256"]);
    assert_eq!(restored["history"].as_array().unwrap().len(), 3);
    assert_eq!(restored["history"][0]["based_on_version"], 1);
    assert_eq!(generation(&env), 3);
    let latest = expect(&env.get(&path).await, StatusCode::OK);
    assert_eq!(latest["document"], own["document"]);
    let download = env.get(edited["download_url"].as_str().unwrap()).await;
    assert_eq!(download.status, StatusCode::OK);
    assert_eq!(sha256_hex(&download.body), edited["sha256"]);
    assert_eq!(
        download.header("content-type").as_deref(),
        Some("application/octet-stream")
    );
    assert_eq!(
        download.header("cache-control").as_deref(),
        Some("no-store")
    );
    let disposition = download.header("content-disposition").unwrap();
    assert!(
        disposition.contains("-r2.sto") && disposition.contains("filename*=UTF-8''"),
        "{disposition}"
    );
    let original = env.get(&format!("{ROOT}/{source}/download")).await;
    assert_eq!(original.body.as_ref(), bytes);
    assert_eq!(
        env.get(&format!("/api/v1/drivers/2002/sto-files/{id}"))
            .await
            .status,
        StatusCode::NOT_FOUND
    );
}

#[tokio::test]
async fn concurrent_saves_have_one_winner_and_conflict_returns_the_complete_current_detail() {
    let Some(env) = environment().await else {
        return;
    };
    let source = seed(&env, CUSTOMER, CAR, "source.sto", &source_bytes(), true).await;
    let own = fork(&env, &source, "Concurrent.sto").await;
    let path = format!("{ROOT}/{}", own["file"]["id"].as_str().unwrap());
    let (a, b) = tokio::join!(
        write(
            &env,
            &path,
            json!({"base_version":1,"changes":{"fuelFillToKg":"11"}}),
            "save-a",
            Some(1)
        ),
        write(
            &env,
            &path,
            json!({"base_version":1,"changes":{"fuelFillToKg":"12"}}),
            "save-b",
            Some(1)
        )
    );
    let (winner, loser) = if a.status == StatusCode::OK {
        (a, b)
    } else {
        (b, a)
    };
    let saved = expect(&winner, StatusCode::OK);
    let conflict = expect(&loser, StatusCode::PRECONDITION_FAILED);
    assert_eq!(conflict["error"], "version_conflict");
    assert_eq!(conflict["current"], saved);
    assert_eq!(generation(&env), 2);
    let count: i64 =
        env.query(|c| c.query_row("SELECT COUNT(*) FROM sto_revisions", [], |r| r.get(0)));
    assert_eq!(count, 2);
    assert_eq!(
        expect(
            &write(
                &env,
                &path,
                json!({"base_version":2,"changes":{}}),
                "no-ifmatch",
                None
            )
            .await,
            StatusCode::PRECONDITION_FAILED
        )["current"],
        saved
    );
}

#[tokio::test]
async fn committed_receipts_survive_restart_and_reject_changed_body_header_or_scope() {
    let Some(env) = environment().await else {
        return;
    };
    let source = seed(&env, CUSTOMER, CAR, "source.sto", &source_bytes(), true).await;
    let fork_path = format!("{ROOT}/{source}/save-as");
    let body = json!({"name":"Retry.sto","base_version":1,"changes":{}});
    let created = expect(
        &write(&env, &fork_path, body.clone(), "durable-create", None).await,
        StatusCode::CREATED,
    );
    let env = env.restart().await;
    assert_eq!(
        expect(
            &write(&env, &fork_path, body.clone(), "durable-create", None).await,
            StatusCode::CREATED
        ),
        created
    );
    let path = format!("{ROOT}/{}", created["file"]["id"].as_str().unwrap());
    let edit = json!({"base_version":1,"changes":{"fBrakeBias":"55.500"}});
    let saved = expect(
        &write(&env, &path, edit.clone(), "durable-save", Some(1)).await,
        StatusCode::OK,
    );
    let env = env.restart().await;
    assert_eq!(
        expect(
            &write(&env, &path, edit.clone(), "durable-save", Some(1)).await,
            StatusCode::OK
        ),
        saved
    );
    assert_eq!(
        expect(
            &write(
                &env,
                &path,
                json!({"base_version":1,"changes":{"fBrakeBias":"56"}}),
                "durable-save",
                Some(1)
            )
            .await,
            StatusCode::UNPROCESSABLE_ENTITY
        )["error"],
        "idempotency_key_reused"
    );
    assert_eq!(
        expect(
            &write(&env, &path, edit.clone(), "durable-save", Some(2)).await,
            StatusCode::UNPROCESSABLE_ENTITY
        )["error"],
        "idempotency_key_reused"
    );
    assert_eq!(
        write(&env, &fork_path, body, "durable-save", None)
            .await
            .status,
        StatusCode::UNPROCESSABLE_ENTITY
    );
    assert_eq!(
        write(
            &env,
            &format!("/api/v1/drivers/2002/sto-files/{source}/save-as"),
            json!({"name":"Retry.sto","base_version":1,"changes":{}}),
            "durable-create",
            None
        )
        .await
        .status,
        StatusCode::UNPROCESSABLE_ENTITY
    );
    assert_eq!(generation(&env), 2);
    let counts: (i64, i64) = env.query(|c| {
        Ok((
            c.query_row("SELECT COUNT(*) FROM sto_revisions", [], |r| r.get(0))?,
            c.query_row("SELECT COUNT(*) FROM sto_write_receipts", [], |r| r.get(0))?,
        ))
    });
    assert_eq!(counts, (2, 2));
}

#[tokio::test]
async fn filenames_and_python_rejections_cannot_create_or_replace_a_setup() {
    let Some(env) = environment().await else {
        return;
    };
    let source = seed(&env, CUSTOMER, CAR, "source.sto", &source_bytes(), true).await;
    let path = format!("{ROOT}/{source}/save-as");
    for (n, name) in [
        "../escape.sto",
        "nested/file.sto",
        "nested\\file.sto",
        "CON.sto",
        "aux.config.sto",
        "LPT9.sto",
        "bad.sto ",
        "bad..sto",
        "bad.sto.sto",
        "missing-extension",
        "bad?.sto",
        ".sto",
    ]
    .iter()
    .enumerate()
    {
        let reply = write(
            &env,
            &path,
            json!({"name":name,"base_version":1,"changes":{}}),
            &format!("bad-name-{n}"),
            None,
        )
        .await;
        expect(&reply, StatusCode::BAD_REQUEST);
    }
    let first = fork(&env, &source, "Öwned.sto").await;
    assert_eq!(
        expect(
            &write(
                &env,
                &path,
                json!({"name":"öwned.STO","base_version":1,"changes":{}}),
                "duplicate-name",
                None
            )
            .await,
            StatusCode::CONFLICT
        )["error"],
        "duplicate_name"
    );
    let rejected = write(
        &env,
        &path,
        json!({"name":"Rejected.sto","base_version":1,"changes":{"nonexistent":"1"}}),
        "bad-field",
        None,
    )
    .await;
    assert_eq!(
        expect(&rejected, StatusCode::UNPROCESSABLE_ENTITY)["error"],
        "invalid_edit"
    );
    assert_eq!(generation(&env), 1);
    let raw = env
        .call(
            Method::POST,
            &path,
            Some(Bytes::from(
                json!({"name":"No key.sto","base_version":1,"changes":{}}).to_string(),
            )),
            &[("content-type", "application/json")],
        )
        .await;
    expect(&raw, StatusCode::BAD_REQUEST);
    let selected = env.get(first["download_url"].as_str().unwrap()).await;
    assert_eq!(selected.status, StatusCode::OK);
    assert!(
        selected
            .header("content-disposition")
            .unwrap()
            .contains("%C3%96wned")
    );
}

#[tokio::test]
async fn source_decode_failure_explains_unavailability_without_disabling_original_download() {
    let Some(env) = environment().await else {
        return;
    };
    let bytes = b"unsupported synthetic source";
    let source = seed(&env, CUSTOMER, CAR, "unknown.sto", bytes, true).await;
    let detail = expect(&env.get(&format!("{ROOT}/{source}")).await, StatusCode::OK);
    assert_eq!(detail["document"], Value::Null);
    assert!(
        detail["unavailable_reason"]
            .as_str()
            .unwrap()
            .contains("could not read")
    );
    let download = env.get(detail["download_url"].as_str().unwrap()).await;
    assert_eq!(download.status, StatusCode::OK);
    assert_eq!(download.body.as_ref(), bytes);
    assert_eq!(generation(&env), 0);
}

#[tokio::test]
async fn archived_only_sto_sources_restage_from_the_setup_archive_and_verify_the_hash() {
    use coach_server::archiver::{ArchiveKind, ArchiveSpec};
    let Some(env) = environment().await else {
        return;
    };
    let bytes = source_bytes();
    let sha = sha256_hex(&bytes);
    let source = seed(&env, CUSTOMER, CAR, "Archive.sto", &bytes, true).await;
    let spool = env.app.cfg.spool_ready().join(&sha);
    env.app
        .archiver
        .archive(ArchiveSpec {
            sha256: sha.clone(),
            size: bytes.len() as u64,
            kind: ArchiveKind::SetupFile,
            extension: "sto".into(),
            spool_path: spool.clone(),
            manifest: json!({"original_name":"Archive.sto"}),
        })
        .await
        .unwrap();
    assert!(
        env.nas()
            .join("setups")
            .join(&sha[..2])
            .join(format!("{sha}.sto"))
            .exists()
    );
    std::fs::remove_file(&spool).unwrap();
    let hash = sha.clone();
    env.app
        .store
        .write(move |c| {
            c.execute(
                "UPDATE blobs SET spool_present=0,nas_state='archived' WHERE sha256=?1",
                [hash],
            )?;
            Ok(())
        })
        .await
        .unwrap();
    let opened = expect(&env.get(&format!("{ROOT}/{source}")).await, StatusCode::OK);
    assert_eq!(opened["document"]["sha256"], sha);
    assert_eq!(opened["unavailable_reason"], Value::Null);
    assert_eq!(std::fs::read(spool).unwrap(), bytes);
    let hash = sha.clone();
    assert_eq!(
        env.query(move |c| c.query_row(
            "SELECT spool_present FROM blobs WHERE sha256=?1",
            [hash],
            |r| r.get::<_, i64>(0)
        )),
        1
    );
}

#[tokio::test]
async fn user_data_snapshots_include_every_revision_and_committed_retry_receipt() {
    let Some(env) = environment().await else {
        return;
    };
    let source = seed(&env, CUSTOMER, CAR, "Source.sto", &source_bytes(), true).await;
    let own = fork(&env, &source, "Snapshot.sto").await;
    let (first_generation, first_dir, first_created) =
        coach_server::scheduler::take_snapshot(&env.app)
            .await
            .unwrap();
    assert_eq!(first_generation, 1);
    assert!(first_created);
    let path = format!("{ROOT}/{}", own["file"]["id"].as_str().unwrap());
    let saved = expect(
        &write(
            &env,
            &path,
            json!({"base_version":1,"changes":{"fBrakeBias":"56.000"}}),
            "snapshot-save",
            Some(1),
        )
        .await,
        StatusCode::OK,
    );
    let (next_generation, next_dir, next_created) =
        coach_server::scheduler::take_snapshot(&env.app)
            .await
            .unwrap();
    assert_eq!(next_generation, 2);
    assert!(next_created);
    assert_ne!(first_dir, next_dir);
    let backup = rusqlite::Connection::open(next_dir.join("db/coach.sqlite3")).unwrap();
    let rows: Vec<(i64, String, Vec<u8>)> = backup
        .prepare("SELECT version,sha256,content FROM sto_revisions ORDER BY version")
        .unwrap()
        .query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))
        .unwrap()
        .collect::<rusqlite::Result<_>>()
        .unwrap();
    assert_eq!(rows.len(), 2);
    assert_eq!(rows[0].1, own["sha256"].as_str().unwrap());
    assert_eq!(rows[1].1, saved["sha256"].as_str().unwrap());
    assert!(
        rows.iter()
            .all(|(_, hash, content)| sha256_hex(content) == *hash)
    );
    let receipts: i64 = backup
        .query_row("SELECT COUNT(*) FROM sto_write_receipts", [], |r| r.get(0))
        .unwrap();
    assert_eq!(receipts, 2);
    let (_, _, created_again) = coach_server::scheduler::take_snapshot(&env.app)
        .await
        .unwrap();
    assert!(!created_again);
}

#[tokio::test]
async fn unreadable_old_revision_keeps_its_selected_identity_and_specific_failure_reason() {
    let Some(env) = environment().await else {
        return;
    };
    let source = seed(&env, CUSTOMER, CAR, "Source.sto", &source_bytes(), true).await;
    let own = fork(&env, &source, "History.sto").await;
    let path = format!("{ROOT}/{}", own["file"]["id"].as_str().unwrap());
    let second = expect(
        &write(
            &env,
            &path,
            json!({"base_version":1,"changes":{"fuelFillToKg":"12"}}),
            "history-2",
            Some(1),
        )
        .await,
        StatusCode::OK,
    );
    let third = expect(
        &write(
            &env,
            &path,
            json!({"base_version":2,"changes":{"fuelFillToKg":"14"}}),
            "history-3",
            Some(2),
        )
        .await,
        StatusCode::OK,
    );
    let id = own["file"]["id"]
        .as_str()
        .unwrap()
        .strip_prefix("coach:")
        .unwrap()
        .parse::<i64>()
        .unwrap();
    env.app.store.write(move |c| {
        c.execute("UPDATE sto_revisions SET document_json='invalid json' WHERE file_id=?1 AND version=2", [id])?;
        Ok(())
    }).await.unwrap();
    let broken_document = expect(&env.get(&format!("{path}?version=2")).await, StatusCode::OK);
    assert_eq!(broken_document["version"], 2);
    assert_eq!(broken_document["file"]["current_version"], 3);
    assert_eq!(broken_document["sha256"], second["sha256"]);
    assert_eq!(broken_document["document"], Value::Null);
    assert_eq!(broken_document["download_url"], second["download_url"]);
    assert!(
        broken_document["unavailable_reason"]
            .as_str()
            .unwrap()
            .contains("metadata could not be verified")
    );
    assert_eq!(broken_document["history"][1]["version"], 2);
    assert_eq!(broken_document["history"][1]["sha256"], second["sha256"]);
    let document = second["document"].to_string();
    env.app.store.write(move |c| {
        c.execute("UPDATE sto_revisions SET document_json=?2,content=?3 WHERE file_id=?1 AND version=2", params![id, document, b"corrupt historical bytes".as_slice()])?;
        Ok(())
    }).await.unwrap();
    let broken_bytes = expect(&env.get(&format!("{path}?version=2")).await, StatusCode::OK);
    assert_eq!(broken_bytes["version"], 2);
    assert_eq!(broken_bytes["sha256"], second["sha256"]);
    assert_eq!(broken_bytes["download_url"], second["download_url"]);
    assert!(
        broken_bytes["unavailable_reason"]
            .as_str()
            .unwrap()
            .contains("do not match the archived hash")
    );
    assert_eq!(
        expect(
            &env.get(broken_bytes["download_url"].as_str().unwrap())
                .await,
            StatusCode::UNPROCESSABLE_ENTITY
        )["error"],
        "invalid_sto"
    );
    let current = expect(&env.get(&path).await, StatusCode::OK);
    assert_eq!(current["document"], third["document"]);
    assert_eq!(current["unavailable_reason"], Value::Null);
    assert_eq!(generation(&env), 3);
}

#[tokio::test]
async fn oversized_archive_metadata_is_rejected_before_any_nas_restage() {
    let Some(env) = environment().await else {
        return;
    };
    let bytes = source_bytes();
    let sha = sha256_hex(&bytes);
    let source = seed(&env, CUSTOMER, CAR, "Too large.sto", &bytes, true).await;
    let spool = env.app.cfg.spool_ready().join(&sha);
    std::fs::remove_file(&spool).unwrap();
    let hash = sha.clone();
    env.app.store.write(move |c| {
        c.execute("UPDATE blobs SET size=16777217,spool_present=0,nas_state='archived' WHERE sha256=?1", [hash])?;
        Ok(())
    }).await.unwrap();
    let incoming = env.app.cfg.spool_incoming();
    let initial_entries = std::fs::read_dir(&incoming).unwrap().count();
    assert!(
        !env.nas()
            .join("setups")
            .join(&sha[..2])
            .join(format!("{sha}.sto"))
            .exists()
    );
    let detail = expect(&env.get(&format!("{ROOT}/{source}")).await, StatusCode::OK);
    assert_eq!(detail["document"], Value::Null);
    assert!(
        detail["unavailable_reason"]
            .as_str()
            .unwrap()
            .contains("16 MiB limit")
    );
    let download = expect(
        &env.get(detail["download_url"].as_str().unwrap()).await,
        StatusCode::UNPROCESSABLE_ENTITY,
    );
    assert_eq!(download["error"], "invalid_sto");
    assert!(
        download["message"]
            .as_str()
            .unwrap()
            .contains("16 MiB limit")
    );
    assert!(!spool.exists());
    assert_eq!(
        std::fs::read_dir(incoming).unwrap().count(),
        initial_entries
    );
    let present: i64 = env.query(move |c| {
        c.query_row(
            "SELECT spool_present FROM blobs WHERE sha256=?1",
            [sha],
            |r| r.get(0),
        )
    });
    assert_eq!(present, 0);
    assert_eq!(generation(&env), 0);
}

#[tokio::test]
async fn overflowing_codec_output_is_stopped_without_a_revision_and_releases_admission() {
    let Some(env) = environment().await else {
        return;
    };
    let mut overflowing: Value = serde_json::from_slice(&source_bytes()).unwrap();
    overflowing["test_behavior"] = json!("oversized_stdout");
    let source = seed(
        &env,
        CUSTOMER,
        CAR,
        "Overflow.sto",
        overflowing.to_string().as_bytes(),
        true,
    )
    .await;
    let reply = tokio::time::timeout(
        std::time::Duration::from_secs(15),
        write(
            &env,
            &format!("{ROOT}/{source}/save-as"),
            json!({"name":"Overflow copy.sto","base_version":1,"changes":{}}),
            "overflowing-codec",
            None,
        ),
    )
    .await
    .expect("bounded codec output must fail promptly");
    let error = expect(&reply, StatusCode::INTERNAL_SERVER_ERROR);
    assert!(
        error["message"]
            .as_str()
            .unwrap()
            .contains("exceeded its bound")
    );
    let counts: (i64, i64, i64) = env.query(|c| {
        Ok((
            c.query_row("SELECT COUNT(*) FROM sto_working_files", [], |r| r.get(0))?,
            c.query_row("SELECT COUNT(*) FROM sto_revisions", [], |r| r.get(0))?,
            c.query_row("SELECT COUNT(*) FROM sto_write_receipts", [], |r| r.get(0))?,
        ))
    });
    assert_eq!(counts, (0, 0, 0));
    assert_eq!(generation(&env), 0);
    let pid = std::fs::read_to_string(env.data().join("oversized-codec.pid"))
        .unwrap()
        .parse::<u32>()
        .unwrap();
    #[cfg(target_os = "linux")]
    assert!(
        !std::path::Path::new("/proc").join(pid.to_string()).exists(),
        "overflowing codec process must be reaped"
    );
    #[cfg(windows)]
    {
        let processes = std::process::Command::new("tasklist.exe")
            .args(["/FI", &format!("PID eq {pid}"), "/FO", "CSV", "/NH"])
            .output()
            .unwrap();
        assert!(processes.status.success());
        assert!(
            !String::from_utf8_lossy(&processes.stdout).contains(&format!("\",\"{pid}\",")),
            "overflowing codec process must have exited"
        );
    }
    let normal = seed(&env, CUSTOMER, CAR, "Normal.sto", &source_bytes(), true).await;
    let reopened = tokio::time::timeout(
        std::time::Duration::from_secs(5),
        env.get(&format!("{ROOT}/{normal}")),
    )
    .await
    .expect("codec admission must be released after overflow");
    assert_eq!(
        expect(&reopened, StatusCode::OK)["unavailable_reason"],
        Value::Null
    );
    let saved = fork(&env, &normal, "After overflow.sto").await;
    assert_eq!(saved["version"], 1);
    assert_eq!(generation(&env), 1);
}

#[tokio::test]
async fn garage_routes_save_both_representations_and_replay_exactly_after_restart() {
    let Some(env) = environment().await else {
        return;
    };
    let bytes = source_bytes();
    let source = seed(&env, CUSTOMER, CAR, "Purchased.sto", &bytes, true).await;
    let fork_path = format!("{ROOT}/{source}/garage/save-as");
    let body = json!({"name":"Garage.sto","base_version":1,"garage_profile_id":GARAGE_PROFILE,"garage_changes":{"fBrakeBias":"55.5"},"native_changes":{"unmappedGeometry":"-0.000200"},"notes":"Mixed garage and native changes"});
    let created = expect(
        &write(&env, &fork_path, body.clone(), "garage-create", None).await,
        StatusCode::CREATED,
    );
    Contract::load().check(
        "POST",
        "/api/v1/drivers/{customer_id}/sto-files/{file_id}/garage/save-as",
        201,
        "application/json",
        &created,
    );
    assert_eq!(created["file"]["locked"], false);
    assert_eq!(created["document"]["descriptor_version"], 2);
    assert_eq!(created["document"]["garage_profile_id"], GARAGE_PROFILE);
    assert_eq!(value(&created["document"], "fBrakeBias"), "55.5");
    assert_eq!(value(&created["document"], "unmappedGeometry"), "-0.000200");
    assert_eq!(
        created["document"]["notes"],
        "Mixed garage and native changes"
    );
    let geometry = created["document"]["settings"]
        .as_array()
        .unwrap()
        .iter()
        .find(|f| f["name"] == "unmappedGeometry")
        .unwrap();
    assert!(geometry.get("garage").is_none());
    let env = env.restart().await;
    assert_eq!(
        expect(
            &write(&env, &fork_path, body, "garage-create", None).await,
            StatusCode::CREATED
        ),
        created
    );
    let path = format!("{ROOT}/{}", created["file"]["id"].as_str().unwrap());
    let garage_path = format!("{path}/garage");
    let edit = json!({"base_version":1,"garage_profile_id":GARAGE_PROFILE,"garage_changes":{"fBrakeBias":"56.0"},"native_changes":{"fuelFillToKg":"12.5000"}});
    let saved = expect(
        &write(&env, &garage_path, edit.clone(), "garage-save", Some(1)).await,
        StatusCode::OK,
    );
    Contract::load().check(
        "POST",
        "/api/v1/drivers/{customer_id}/sto-files/{file_id}/garage",
        200,
        "application/json",
        &saved,
    );
    assert_eq!(saved["version"], 2);
    assert_eq!(value(&saved["document"], "fBrakeBias"), "56.0");
    assert_eq!(value(&saved["document"], "fuelFillToKg"), "12.5000");
    assert_eq!(value(&saved["document"], "unmappedGeometry"), "-0.000200");
    let env = env.restart().await;
    assert_eq!(
        expect(
            &write(&env, &garage_path, edit.clone(), "garage-save", Some(1)).await,
            StatusCode::OK
        ),
        saved
    );
    let mut changed_profile = edit.clone();
    changed_profile["garage_profile_id"] = json!("a-different-profile");
    assert_eq!(
        expect(
            &write(&env, &garage_path, changed_profile, "garage-save", Some(1)).await,
            StatusCode::UNPROCESSABLE_ENTITY
        )["error"],
        "idempotency_key_reused"
    );
    assert_eq!(
        expect(
            &write(&env, &garage_path, edit, "garage-stale", Some(1)).await,
            StatusCode::PRECONDITION_FAILED
        )["current"],
        saved
    );
    let history = expect(&env.get(&format!("{path}?version=1")).await, StatusCode::OK);
    assert_eq!(history["sha256"], created["sha256"]);
    assert_eq!(history["document"], created["document"]);
    let original = env.get(&format!("{ROOT}/{source}/download")).await;
    assert_eq!(original.body.as_ref(), bytes);
    assert_eq!(generation(&env), 2);
}

#[tokio::test]
async fn garage_validation_rejects_profile_mismatch_unmapped_fields_and_invalid_grids_with_field_details()
 {
    let Some(env) = environment().await else {
        return;
    };
    let source = seed(&env, CUSTOMER, CAR, "Source.sto", &source_bytes(), true).await;
    let path = format!("{ROOT}/{source}/garage/save-as");
    let common = json!({"name":"Invalid.sto","base_version":1,"garage_profile_id":GARAGE_PROFILE,"garage_changes":{}});
    let mut mismatch = common.clone();
    mismatch["garage_profile_id"] = json!("obsolete-profile");
    assert_eq!(
        expect(
            &write(&env, &path, mismatch, "garage-wrong-profile", None).await,
            StatusCode::UNPROCESSABLE_ENTITY
        )["error"],
        "garage_profile_mismatch"
    );
    let cases = [
        (
            json!({"unmappedGeometry":"1"}),
            json!({}),
            "garage_unmapped_field",
            "unmappedGeometry",
        ),
        (
            json!({"fBrakeBias":"100.5"}),
            json!({}),
            "garage_out_of_range",
            "fBrakeBias",
        ),
        (
            json!({"fBrakeBias":"55.25"}),
            json!({}),
            "garage_off_grid",
            "fBrakeBias",
        ),
        (
            json!({"fBrakeBias":"NaN"}),
            json!({}),
            "invalid_garage_value",
            "fBrakeBias",
        ),
        (
            json!({}),
            json!({"fBrakeBias":"55.25"}),
            "garage_off_grid",
            "fBrakeBias",
        ),
        (json!({}), json!({"missing":"1"}), "invalid_edit", "missing"),
        (
            json!({"fBrakeBias":"56"}),
            json!({"fBrakeBias":"56"}),
            "ambiguous_change",
            "fBrakeBias",
        ),
    ];
    for (index, (garage, native, code, name)) in cases.into_iter().enumerate() {
        let mut body = common.clone();
        body["garage_changes"] = garage;
        body["native_changes"] = native;
        let error = expect(
            &write(&env, &path, body, &format!("garage-invalid-{index}"), None).await,
            StatusCode::UNPROCESSABLE_ENTITY,
        );
        assert_eq!(error["error"], code);
        assert_eq!(error["details"]["name"], name);
    }
    let mut legacy = common;
    legacy["changes"] = json!({});
    expect(
        &write(&env, &path, legacy, "garage-reject-legacy-map", None).await,
        StatusCode::BAD_REQUEST,
    );
    let native_path = format!("{ROOT}/{source}/save-as");
    let error = expect(
        &write(
            &env,
            &native_path,
            json!({"name":"Raw bypass.sto","base_version":1,"changes":{"fBrakeBias":"55.25"}}),
            "garage-raw-bypass",
            None,
        )
        .await,
        StatusCode::UNPROCESSABLE_ENTITY,
    );
    assert_eq!(error["error"], "garage_off_grid");
    assert_eq!(error["details"]["name"], "fBrakeBias");
    assert_eq!(generation(&env), 0);
    assert_eq!(
        env.query(
            |c| c.query_row("SELECT COUNT(*) FROM sto_revisions", [], |r| r
                .get::<_, i64>(0))
        ),
        0
    );
}

#[tokio::test]
async fn native_pending_receipts_keep_the_original_hash_formula_when_garage_routes_are_added() {
    let Some(env) = environment().await else {
        return;
    };
    let source = seed(&env, CUSTOMER, CAR, "Source.sto", &source_bytes(), true).await;
    let created = fork(&env, &source, "Existing.sto").await;
    let body = json!({"name":"Existing.sto","base_version":1,"changes":{}});
    let legacy_hash = sha256_hex(
        json!([CUSTOMER, source, true, body, Value::Null])
            .to_string()
            .as_bytes(),
    );
    let response = created.to_string();
    env.app.store.write(move |c| {
        c.execute("INSERT INTO sto_write_receipts(operation_id,request_sha256,response_json) VALUES('old-native-pending',?1,?2)", params![legacy_hash,response])?;
        Ok(())
    }).await.unwrap();
    let env = env.restart().await;
    let replayed = expect(
        &write(
            &env,
            &format!("{ROOT}/{source}/save-as"),
            body,
            "old-native-pending",
            None,
        )
        .await,
        StatusCode::CREATED,
    );
    assert_eq!(replayed, created);
    let garage = json!({"name":"Existing.sto","base_version":1,"garage_profile_id":GARAGE_PROFILE,"garage_changes":{}});
    assert_eq!(
        expect(
            &write(
                &env,
                &format!("{ROOT}/{source}/garage/save-as"),
                garage,
                "old-native-pending",
                None
            )
            .await,
            StatusCode::UNPROCESSABLE_ENTITY
        )["error"],
        "idempotency_key_reused"
    );
    assert_eq!(generation(&env), 1);
    assert_eq!(
        env.query(
            |c| c.query_row("SELECT COUNT(*) FROM sto_revisions", [], |r| r
                .get::<_, i64>(0))
        ),
        1
    );
}

#[tokio::test]
async fn opening_owned_bytes_enriches_legacy_cache_and_strips_stale_garage_data_when_material_is_unavailable()
 {
    let Some(env) = environment().await else {
        return;
    };
    let source = seed(&env, CUSTOMER, CAR, "Source.sto", &source_bytes(), true).await;
    let created = fork(&env, &source, "Cache.sto").await;
    let id = created["file"]["id"].as_str().unwrap();
    let numeric_id = id.strip_prefix("coach:").unwrap().parse::<i64>().unwrap();
    let mut legacy = created["document"].clone();
    for key in [
        "garage_profile_id",
        "garage_profile_label",
        "descriptor_version",
    ] {
        legacy.as_object_mut().unwrap().remove(key);
    }
    for field in legacy["settings"].as_array_mut().unwrap() {
        field.as_object_mut().unwrap().remove("garage");
    }
    let legacy_text = legacy.to_string();
    let stored = legacy_text.clone();
    env.app
        .store
        .write(move |c| {
            c.execute(
                "UPDATE sto_revisions SET document_json=?2 WHERE file_id=?1 AND version=1",
                params![numeric_id, stored],
            )?;
            Ok(())
        })
        .await
        .unwrap();
    let path = format!("{ROOT}/{id}");
    let enriched = expect(&env.get(&path).await, StatusCode::OK);
    assert_eq!(enriched["document"]["descriptor_version"], 2);
    assert_eq!(enriched["document"]["garage_profile_id"], GARAGE_PROFILE);
    assert!(
        enriched["document"]["settings"]
            .as_array()
            .unwrap()
            .iter()
            .any(|field| field["name"] == "fBrakeBias"
                && field["garage"]["label"] == "Front brake bias")
    );
    assert_eq!(enriched["sha256"], created["sha256"]);
    assert_eq!(enriched["history"], created["history"]);
    assert_eq!(
        env.query(move |c| c.query_row(
            "SELECT document_json FROM sto_revisions WHERE file_id=?1 AND version=1",
            [numeric_id],
            |r| r.get::<_, String>(0)
        )),
        legacy_text
    );
    let mut stale = enriched["document"].clone();
    stale["garage_profile_id"] = json!("obsolete-garage-profile");
    for field in stale["settings"].as_array_mut().unwrap() {
        if field["name"] == "fBrakeBias" {
            field["garage"]["value"] = json!("99");
        }
    }
    let stale_text = stale.to_string();
    let stored = stale_text.clone();
    env.app
        .store
        .write(move |c| {
            c.execute(
                "UPDATE sto_revisions SET document_json=?2 WHERE file_id=?1 AND version=1",
                params![numeric_id, stored],
            )?;
            Ok(())
        })
        .await
        .unwrap();
    let marker = env.data().join("codec-unavailable");
    std::fs::write(&marker, b"test codec unavailable").unwrap();
    let fallback = expect(&env.get(&path).await, StatusCode::OK);
    assert!(fallback["document"]["garage_profile_id"].is_null());
    assert!(fallback["document"].get("garage_profile_label").is_none());
    assert!(
        fallback["document"]["settings"]
            .as_array()
            .unwrap()
            .iter()
            .all(|field| field.get("garage").is_none())
    );
    assert_eq!(value(&fallback["document"], "fBrakeBias"), "55.0");
    assert!(
        fallback["document"]["warnings"]
            .as_array()
            .unwrap()
            .iter()
            .any(|w| w
                .as_str()
                .unwrap_or_default()
                .to_lowercase()
                .contains("unavailable"))
    );
    assert_eq!(fallback["sha256"], created["sha256"]);
    assert_eq!(fallback["history"], created["history"]);
    assert_eq!(
        env.query(move |c| c.query_row(
            "SELECT document_json FROM sto_revisions WHERE file_id=?1 AND version=1",
            [numeric_id],
            |r| r.get::<_, String>(0)
        )),
        stale_text
    );
    std::fs::remove_file(marker).unwrap();
    let fresh = expect(&env.get(&path).await, StatusCode::OK);
    assert_eq!(fresh["document"]["garage_profile_id"], GARAGE_PROFILE);
    assert_eq!(generation(&env), 1);
}

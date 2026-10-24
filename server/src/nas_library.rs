//! Canonical NAS library and immutable setup delivery. NAS I/O runs only on the archiver thread.
pub mod fs;

use axum::extract::{Path, Query, State};
use axum::response::Response;
use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
use rusqlite::{Connection, OptionalExtension, params};
use serde::Deserialize;
use serde_json::{Value, json};
use std::sync::Arc;

use crate::app::AppState;
use crate::archiver::ArchiveSpec;
use crate::error::{Error, Result};
use crate::util::{iso_now, sha256_hex};
use fs::{Reply, Request};

#[derive(Debug, Clone)]
pub struct Object {
    pub kind: String,
    pub sha: String,
    pub size: u64,
    pub path: String,
    pub legacy: Option<String>,
    pub state: String,
    pub fingerprint: Option<String>,
}

pub fn deleted(conn: &Connection, sha: &str) -> rusqlite::Result<bool> {
    conn.query_row(
        "SELECT EXISTS(SELECT 1 FROM nas_library_objects WHERE sha256=?1 AND state='deleted')",
        [sha],
        |r| r.get(0),
    )
}

pub async fn guard(app: &AppState, sha: &str) -> Result<()> {
    if is_deleted(app, sha).await? {
        Err(Error::unprocessable(
            "library_file_deleted",
            "This file was deleted from the NAS library. Restore its exact bytes at the recorded library path to use it again.",
        ))
    } else {
        Ok(())
    }
}

pub async fn is_deleted(app: &AppState, sha: &str) -> Result<bool> {
    let sha = sha.to_string();
    app.store.read(move |c| Ok(deleted(c, &sha)?)).await
}

fn gate(app: &AppState) -> fs::Gate {
    let hub = app.hub.clone();
    Arc::new(move || hub.allows_library_work())
}

async fn identity(app: &AppState) -> Result<String> {
    let _lock = app.idem_locks.lock("nas-library-identity").await;
    let expected = app
        .store
        .read(|c| {
            Ok(c.query_row(
                "SELECT identity FROM nas_library_meta WHERE singleton=1",
                [],
                |r| r.get::<_, Option<String>>(0),
            )?)
        })
        .await?;
    let Reply::Identity(id) = app
        .archiver
        .library(Request::Identity(expected.clone()), gate(app))
        .await?
    else {
        return Err(Error::internal("invalid library identity reply"));
    };
    if expected.is_none() {
        let pin = id.clone();
        app.store.write(move|c|{let tx=c.transaction()?;tx.execute("UPDATE nas_library_meta SET identity=?1 WHERE singleton=1 AND identity IS NULL",[pin])?;crate::db::bump_generation(&tx)?;tx.commit()?;Ok(())}).await?;
    }
    Ok(id)
}

fn read_object(conn: &Connection, kind: &str, sha: &str) -> rusqlite::Result<Option<Object>> {
    conn.query_row("SELECT kind,sha256,size,relative_path,legacy_path,state,verified_fingerprint FROM nas_library_objects WHERE kind=?1 AND sha256=?2",params![kind,sha],|r|Ok(Object{kind:r.get(0)?,sha:r.get(1)?,size:r.get::<_,i64>(2)? as u64,path:r.get(3)?,legacy:r.get(4)?,state:r.get(5)?,fingerprint:r.get(6)?})).optional()
}

fn canonical(
    kind: &str,
    sha: &str,
    name: &str,
    customer: Option<i64>,
    car: &str,
    revision: Option<(i64, i64)>,
) -> Result<String> {
    if !crate::util::is_sha256_hex(sha) {
        return Err(Error::invalid("invalid payload hash"));
    }
    let owner = customer
        .map(|c| c.to_string())
        .unwrap_or_else(|| "unassigned".into());
    let stem = fs::stem(name, 40);
    let path = match kind {
        "ibt" => format!("library/telemetry/{owner}/{stem}--{sha}.ibt"),
        "rpy" => format!("library/replays/{owner}/{stem}--{sha}.rpy"),
        "sto" | "htm" => {
            if !fs::component(car, 64) {
                return Err(Error::invalid(
                    "explicit safe car folder (<=64 UTF-16 units) required for setup sync",
                ));
            }
            if let Some((id, version)) = revision {
                format!("library/setups/{owner}/{car}/coach/{id}/{stem}--v{version}--{sha}.{kind}")
            } else {
                format!("library/setups/{owner}/{car}/sources/{stem}--{sha}.{kind}")
            }
        }
        _ => return Err(Error::invalid("unsupported library payload kind")),
    };
    if path.encode_utf16().count() > 240 {
        return Err(Error::invalid(
            "canonical library path exceeds 240 UTF-16 units",
        ));
    }
    Ok(path)
}

async fn register(
    app: &AppState,
    kind: &str,
    sha: &str,
    size: u64,
    path: String,
    legacy: Option<String>,
) -> Result<Object> {
    let (kind, sha) = (kind.to_string(), sha.to_string());
    app.store.write(move|c|{
        let tx=c.transaction()?;
        let inserted=tx.execute("INSERT OR IGNORE INTO nas_library_objects(kind,sha256,size,relative_path,legacy_path,state) VALUES(?1,?2,?3,?4,?5,?6)",params![kind,sha,size as i64,path,legacy,if legacy.is_some(){"legacy"}else{"rename_pending"}])?;
        if inserted>0{crate::db::bump_generation(&tx)?;}
        let object=read_object(&tx,&kind,&sha)?.ok_or_else(||Error::conflict("library_path_conflict","canonical path already belongs to another identity"))?;
        if object.size!=size{return Err(Error::conflict("library_identity_conflict","same hash was announced with another size"));}tx.commit()?;Ok(object)
    }).await
}

async fn record_reply(
    app: &AppState,
    object: &Object,
    reply: Reply,
    allow_delete: bool,
) -> Result<()> {
    let object = object.clone();
    let cleanup_sha = object.sha.clone();
    let (state, error, fingerprint) = match reply {
        Reply::Present(fingerprint) => ("canonical_verified", None, Some(fingerprint)),
        Reply::Missing if allow_delete => (
            "deleted",
            Some("Explicitly missing canonical NAS library file".to_string()),
            None,
        ),
        Reply::Missing => {
            return Err(Error::unavailable(
                60,
                "library object unavailable during relocation; no deletion inferred",
            ));
        }
        Reply::Conflict(message) => ("conflict", Some(message), None),
        _ => return Err(Error::internal("invalid library filesystem reply")),
    };
    let error_copy = error.clone();
    let metadata_object = object.clone();
    app.store.write(move|c|{
        let tx=c.transaction()?;
        if object.state!=state{crate::db::bump_generation(&tx)?;}
        tx.execute("UPDATE nas_library_objects SET state=?3,error=?4,last_verified_at=CASE WHEN ?3='canonical_verified' THEN ?5 ELSE last_verified_at END,deleted_at=CASE WHEN ?3='deleted' THEN COALESCE(deleted_at,?5) ELSE NULL END,verified_fingerprint=?6 WHERE kind=?1 AND sha256=?2",params![object.kind,object.sha,state,error_copy,iso_now(),fingerprint])?;
        if state=="deleted" {
            tx.execute("UPDATE uploads SET state='deleted',durable_offset=0,error='library_file_deleted',updated_at=?2 WHERE sha256=?1",params![object.sha,iso_now()])?;
            tx.execute("UPDATE blobs SET nas_state='failed',nas_error='library_file_deleted' WHERE sha256=?1",[&object.sha])?;
            tx.execute("UPDATE jobs SET state='failed',error_code='library_file_deleted',error_message='Canonical NAS library file was deleted',finished_at=?2 WHERE state IN ('queued','deferred','interrupted') AND instr(subject_json,?1)>0",params![object.sha,iso_now()])?;
        } else if state=="conflict" {
            tx.execute("UPDATE blobs SET nas_state='failed',nas_error='library_conflict' WHERE sha256=?1",[&object.sha])?;
        }
        tx.commit()?;Ok(())
    }).await?;
    sidecar(app, &metadata_object, state).await?;
    if state == "canonical_verified" {
        let sha = metadata_object.sha.clone();
        app.store.write(move|c|{let tx=c.transaction()?;tx.execute("UPDATE blobs SET nas_state='archived',nas_archived_at=?2,nas_error=NULL WHERE sha256=?1",params![sha,iso_now()])?;tx.execute("UPDATE uploads SET state='verified',durable_offset=size,error=NULL WHERE sha256=?1 AND state='deleted'",[sha])?;tx.commit()?;Ok(())}).await?;
    }
    if state == "deleted" {
        cleanup_deleted(app, &cleanup_sha).await?;
    }
    if let Some(error) = error
        && state == "conflict"
    {
        return Err(Error::conflict("library_conflict", error));
    }
    Ok(())
}

async fn sidecar(app: &AppState, object: &Object, state: &str) -> Result<()> {
    let object2 = object.clone();
    let manifest=app.store.read(move|c|{
        let mut value=json!({});
        let row:Option<(String,Option<String>,Option<String>,String)>=c.query_row("SELECT first_name,source_mtime,collector_id,received_at FROM blobs WHERE sha256=?1",[&object2.sha],|r|Ok((r.get(0)?,r.get(1)?,r.get(2)?,r.get(3)?))).optional()?;
        if let Some((name,mtime,collector,received))=row{value["original_name"]=json!(name);value["source_mtime"]=json!(mtime);value["collector_id"]=json!(collector);value["received_at"]=json!(received);}
        if let Some(owner)=c.query_row("SELECT customer_id FROM blob_occurrences WHERE sha256=?1 AND customer_id IS NOT NULL ORDER BY occurrence_id LIMIT 1",[&object2.sha],|r|r.get::<_,i64>(0)).optional()?{value["driver"]=json!({"customer_id":owner});}
        if object2.kind=="ibt"{
            let rec:Option<(Option<i64>,Option<i64>,Option<i64>,Option<String>,Option<String>,Option<String>,Option<String>,Option<String>,String)>=c.query_row("SELECT customer_id,record_count,tick_rate,session_info_sha256,track,track_config,car_path,start_time_utc,header_json FROM recordings WHERE sha256=?1",[&object2.sha],|r|Ok((r.get(0)?,r.get(1)?,r.get(2)?,r.get(3)?,r.get(4)?,r.get(5)?,r.get(6)?,r.get(7)?,r.get(8)?))).optional()?;
            if let Some((owner,records,tick,info,track,config,car,start,header))=rec{let header:Value=serde_json::from_str(&header).unwrap_or(json!({}));if let Some(owner)=owner{value["driver"]=json!({"customer_id":owner});}value["recording"]=json!({"record_count":records,"tick_rate":tick,"session_info_sha256":info,"track":track,"track_config":config,"car_path":car,"start_time_utc":start,"buffer_length":header["buffer_length"],"variable_count":header["variable_count"]});
                let types=c.prepare("SELECT DISTINCT s.session_type FROM session_recordings sr JOIN sessions s ON s.session_id=sr.session_id WHERE sr.recording_sha256=?1 AND s.session_type IS NOT NULL ORDER BY s.session_type")?.query_map([&object2.sha],|r|r.get::<_,String>(0))?.collect::<rusqlite::Result<Vec<_>>>()?;value["recording"]["session_types"]=json!(types);
            }
        }else if matches!(object2.kind.as_str(),"sto"|"htm"){
            let setup:Option<(i64,String,String)>=c.query_row("SELECT customer_id,car_folder,relative_path FROM setup_files WHERE blob_sha256=?1 ORDER BY first_seen_at LIMIT 1",[&object2.sha],|r|Ok((r.get(0)?,r.get(1)?,r.get(2)?))).optional()?.or(c.query_row("SELECT f.customer_id,f.car_folder,f.name FROM sto_working_files f JOIN sto_revisions r ON r.file_id=f.file_id WHERE r.sha256=?1 ORDER BY f.file_id,r.version LIMIT 1",[&object2.sha],|r|Ok((r.get(0)?,r.get(1)?,r.get(2)?))).optional()?).or(c.query_row("SELECT customer_id,car_folder,'NAS/'||relative_path FROM nas_inbox_pending WHERE sha256=?1 LIMIT 1",[&object2.sha],|r|Ok((r.get(0)?,r.get(1)?,r.get(2)?))).optional()?);
            if let Some((customer,car,path))=setup{value["driver"]=json!({"customer_id":customer});value["setup_file"]=json!({"format":object2.kind,"car_folder":car,"relative_path":path});if value["original_name"].is_null(){value["original_name"]=json!(path);}}
        }
        Ok(value)
    }).await?;
    app.archiver
        .library(
            Request::Metadata {
                identity: identity(app).await?,
                kind: object.kind.clone(),
                sha: object.sha.clone(),
                size: object.size,
                path: object.path.clone(),
                state: state.into(),
                legacy: object.legacy.clone(),
                manifest: Some(manifest),
            },
            gate(app),
        )
        .await?;
    Ok(())
}

pub(crate) async fn cleanup_deleted(app: &AppState, sha: &str) -> Result<()> {
    let key = sha.to_string();
    let busy=app.store.read(move|c|Ok(c.query_row("SELECT EXISTS(SELECT 1 FROM jobs WHERE state='running' AND kind<>'archive' AND instr(subject_json,?1)>0)",[key],|r|r.get::<_,bool>(0))?)).await?;
    if !busy {
        for path in [
            crate::ingest::part_path(app, sha),
            crate::ingest::ready_path(app, sha),
        ] {
            match std::fs::remove_file(path) {
                Ok(()) => {}
                Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
                Err(e) => return Err(e.into()),
            }
        }
        let sha = sha.to_string();
        app.store
            .write(move |c| {
                c.execute("UPDATE blobs SET spool_present=0 WHERE sha256=?1", [sha])?;
                Ok(())
            })
            .await?;
    }
    Ok(())
}

/// Called by the existing archive job. Intent commits before any NAS rename.
pub async fn archive(app: &AppState, spec: ArchiveSpec) -> Result<()> {
    let _lock = app.upload_locks.lock(&spec.sha256).await;
    guard(app, &spec.sha256).await?;
    let pin = identity(app).await?;
    let mut owner = spec.manifest["driver"]["customer_id"].as_i64();
    if owner.is_none() {
        let sha = spec.sha256.clone();
        owner=app.store.read(move|c|Ok(c.query_row("SELECT customer_id FROM blob_occurrences WHERE sha256=?1 AND customer_id IS NOT NULL ORDER BY occurrence_id LIMIT 1",[sha],|r|r.get::<_,i64>(0)).optional()?)).await?;
    }
    let car = spec.manifest["setup_file"]["car_folder"]
        .as_str()
        .unwrap_or_default();
    let name = spec.manifest["original_name"]
        .as_str()
        .unwrap_or("recording");
    let path = canonical(&spec.extension, &spec.sha256, name, owner, car, None)?;
    let object = register(app, &spec.extension, &spec.sha256, spec.size, path, None).await?;
    if object.state == "deleted" {
        return guard(app, &spec.sha256).await;
    }
    // A verified path is inspected only; a missing library file must NEVER be recopied.
    let request = if object.state == "canonical_verified" || object.state == "conflict" {
        Request::Inspect {
            identity: pin,
            path: object.path.clone(),
            sha: object.sha.clone(),
            size: object.size,
            fingerprint: object.fingerprint.clone(),
        }
    } else {
        Request::Publish {
            identity: pin,
            path: object.path.clone(),
            legacy: object.legacy.clone(),
            sha: object.sha.clone(),
            size: object.size,
            source: Some(spec.spool_path),
        }
    };
    let result = app.archiver.library(request, gate(app)).await?;
    record_reply(app, &object, result, object.state == "canonical_verified").await?;
    guard(app, &spec.sha256).await
}

async fn seed(app: &AppState) -> Result<()> {
    // Metadata-only bounded discovery; old immutable payloads relocate in the later pass.
    let rows=app.store.read(|c|{
        let mut stmt=c.prepare("SELECT b.sha256,b.kind,b.size,b.first_name,COALESCE(r.customer_id,(SELECT customer_id FROM blob_occurrences o WHERE o.sha256=b.sha256 AND customer_id IS NOT NULL ORDER BY occurrence_id LIMIT 1)),COALESCE((SELECT car_folder FROM setup_files f WHERE f.blob_sha256=b.sha256 ORDER BY f.first_seen_at LIMIT 1),'') FROM blobs b LEFT JOIN recordings r ON r.sha256=b.sha256 WHERE b.kind IN ('ibt','sto','htm','rpy') AND b.nas_state='archived' AND NOT EXISTS(SELECT 1 FROM nas_library_objects n WHERE n.kind=b.kind AND n.sha256=b.sha256) ORDER BY b.sha256 LIMIT 128")?;
        Ok(stmt.query_map([],|r|Ok((r.get::<_,String>(0)?,r.get::<_,String>(1)?,r.get::<_,i64>(2)?,r.get::<_,String>(3)?,r.get::<_,Option<i64>>(4)?,r.get::<_,String>(5)?)))?.collect::<rusqlite::Result<Vec<_>>>()?)
    }).await?;
    for (sha, kind, size, name, owner, car) in rows {
        let dir = match kind.as_str() {
            "ibt" => "raw",
            "rpy" => "replays",
            _ => "setups",
        };
        let planned = if matches!(kind.as_str(), "sto" | "htm") && owner.is_none() {
            Err(Error::invalid("setup archive has no explicit driver"))
        } else {
            canonical(&kind, &sha, &name, owner, &car, None)
        };
        let (error, path) = match planned {
            Ok(path) => (None, path),
            Err(e) => (
                Some(e.to_string()),
                format!("library/unassigned/{kind}/{sha}.{kind}"),
            ),
        };
        register(
            app,
            &kind,
            &sha,
            size as u64,
            path,
            Some(format!("{dir}/{}/{sha}.{kind}", &sha[..2])),
        )
        .await?;
        if let Some(error) = error {
            app.store.write(move|c|{c.execute("UPDATE nas_library_objects SET state='conflict',error=?3 WHERE kind=?1 AND sha256=?2",params![kind,sha,error])?;Ok(())}).await?;
        }
    }
    // Every immutable Coach revision is eligible, not only the latest version.
    let revisions=app.store.read(|c|{
        let mut s=c.prepare("SELECT f.file_id,f.customer_id,f.name,f.car_folder,r.version,r.sha256,length(r.content) FROM sto_revisions r JOIN sto_working_files f ON f.file_id=r.file_id WHERE NOT EXISTS(SELECT 1 FROM nas_library_objects n WHERE n.kind='sto' AND n.sha256=r.sha256) ORDER BY f.file_id,r.version LIMIT 128")?;
        Ok(s.query_map([],|r|Ok((r.get::<_,i64>(0)?,r.get::<_,i64>(1)?,r.get::<_,String>(2)?,r.get::<_,String>(3)?,r.get::<_,i64>(4)?,r.get::<_,String>(5)?,r.get::<_,i64>(6)?)))?.collect::<rusqlite::Result<Vec<_>>>()?)
    }).await?;
    for (id, customer, name, car, version, sha, size) in revisions {
        register(
            app,
            "sto",
            &sha,
            size as u64,
            canonical(
                "sto",
                &sha,
                &name,
                Some(customer),
                &car,
                Some((id, version)),
            )?,
            None,
        )
        .await?;
    }
    Ok(())
}

async fn reconcile_object(app: &AppState, pin: &str, object: Object) -> Result<()> {
    let _lock = app.upload_locks.lock(&object.sha).await;
    let (kind, sha) = (object.kind.clone(), object.sha.clone());
    let object = app
        .store
        .read(move |c| Ok(read_object(c, &kind, &sha)?))
        .await?
        .ok_or_else(|| Error::not_found("library catalog entry disappeared"))?;
    if matches!(
        object.state.as_str(),
        "canonical_verified" | "deleted" | "conflict"
    ) {
        let reply = app
            .archiver
            .library(
                Request::Inspect {
                    identity: pin.into(),
                    path: object.path.clone(),
                    sha: object.sha.clone(),
                    size: object.size,
                    fingerprint: if object.state == "deleted" {
                        None
                    } else {
                        object.fingerprint.clone()
                    },
                },
                gate(app),
            )
            .await?;
        if object.state == "deleted" && matches!(reply, Reply::Missing) {
            // A previous metadata write may have failed after the durable tombstone committed.
            sidecar(app, &object, "deleted").await?;
            cleanup_deleted(app, &object.sha).await?;
            return Ok(());
        }
        if object.state == "deleted" && matches!(reply, Reply::Conflict(_)) {
            return Err(Error::conflict(
                "library_conflict",
                "Restored bytes do not match the deleted file; tombstone retained",
            ));
        }
        return record_reply(app, &object, reply, object.state == "canonical_verified").await;
    }
    let (kind, sha) = (object.kind.clone(), object.sha.clone());
    app.store.write(move|c|{c.execute("UPDATE nas_library_objects SET state='rename_pending' WHERE kind=?1 AND sha256=?2 AND state='legacy'",params![kind,sha])?;Ok(())}).await?;
    let spool = crate::ingest::ready_path(app, &object.sha);
    let mut owned = None;
    let source = if spool.is_file() {
        Some(spool)
    } else if object.kind == "sto" && object.legacy.is_none() {
        let sha = object.sha.clone();
        let bytes = app
            .store
            .read(move |c| {
                Ok(c.query_row(
                    "SELECT content FROM sto_revisions WHERE sha256=?1 LIMIT 1",
                    [sha],
                    |r| r.get::<_, Vec<u8>>(0),
                )
                .optional()?)
            })
            .await?;
        if let Some(bytes) = bytes {
            let tmp = app
                .cfg
                .spool_incoming()
                .join(format!("library-sto-{}.tmp", crate::util::new_uuid()));
            std::fs::write(&tmp, bytes)?;
            owned = Some(tmp.clone());
            Some(tmp)
        } else {
            None
        }
    } else {
        None
    };
    let result = app
        .archiver
        .library(
            Request::Publish {
                identity: pin.into(),
                path: object.path.clone(),
                legacy: object.legacy.clone(),
                sha: object.sha.clone(),
                size: object.size,
                source,
            },
            gate(app),
        )
        .await;
    if let Some(tmp) = owned {
        let _ = std::fs::remove_file(tmp);
    }
    record_reply(app, &object, result?, false).await?;
    crate::handlers::release_spool(app, &object.sha).await?;
    Ok(())
}

async fn import_inbox(app: &AppState, pin: &str) -> Result<usize> {
    let customers = app
        .store
        .read(|c| {
            Ok(
                c.prepare("SELECT customer_id FROM drivers ORDER BY customer_id LIMIT 128")?
                    .query_map([], |r| r.get::<_, i64>(0))?
                    .collect::<rusqlite::Result<Vec<_>>>()?,
            )
        })
        .await?;
    let Reply::Inbox(files) = app
        .archiver
        .library(
            Request::Inbox {
                identity: pin.into(),
                customers,
            },
            gate(app),
        )
        .await?
    else {
        return Err(Error::internal("invalid inbox list"));
    };
    let mut imported = 0;
    for file in files {
        let Reply::Bytes(bytes) = app
            .archiver
            .library(
                Request::ReadInbox {
                    identity: pin.into(),
                    file: file.clone(),
                },
                gate(app),
            )
            .await?
        else {
            return Err(Error::internal("invalid inbox read"));
        };
        let sha = sha256_hex(&bytes);
        let _lock = app.upload_locks.lock(&sha).await;
        guard(app, &sha).await?;
        let (f, s) = (file.clone(), sha.clone());
        let seen=app.store.read(move|c|Ok(c.query_row("SELECT EXISTS(SELECT 1 FROM nas_inbox_receipts WHERE customer_id=?1 AND car_folder=?2 AND relative_path=?3 AND sha256=?4)",params![f.customer,f.car,f.relative,s],|r|r.get::<_,bool>(0))?)).await?;
        if seen {
            continue;
        }
        let k = sha.clone();
        let existing_kind=app.store.read(move|c|Ok(c.query_row("SELECT kind FROM blobs WHERE sha256=?1 UNION ALL SELECT kind FROM nas_library_objects WHERE sha256=?1 LIMIT 1",[k],|r|r.get::<_,String>(0)).optional()?)).await?;
        if existing_kind.as_deref().is_some_and(|k| k != "sto") {
            return Err(Error::conflict(
                "upload_kind_conflict",
                "inbox bytes already belong to another file kind",
            ));
        }
        let canonical_path = canonical(
            "sto",
            &sha,
            &file.relative,
            Some(file.customer),
            &file.car,
            None,
        )?;
        let (f, s) = (file.clone(), sha.clone());
        let object=app.store.write(move|c|{let tx=c.transaction()?;
            tx.execute("INSERT OR IGNORE INTO nas_library_objects(kind,sha256,size,relative_path,legacy_path,state) VALUES('sto',?1,?2,?3,?4,'legacy')",params![s,f.size as i64,canonical_path,f.path])?;
            let object=read_object(&tx,"sto",&s)?.ok_or_else(||Error::conflict("library_conflict","inbox canonical path belongs to another identity"))?;
            if object.size!=f.size{return Err(Error::conflict("library_identity_conflict","inbox size differs from catalog"));}
            tx.execute("INSERT OR IGNORE INTO nas_inbox_pending(customer_id,car_folder,relative_path,sha256,size) VALUES(?1,?2,?3,?4,?5)",params![f.customer,f.car,f.relative,s,f.size as i64])?;
            crate::db::bump_generation(&tx)?;tx.commit()?;Ok(object)
        }).await?;
        app.faults.check("library.inbox_after_intent")?;
        if object.state != "canonical_verified" {
            let reply = app
                .archiver
                .library(
                    Request::Adopt {
                        identity: pin.into(),
                        file: file.clone(),
                        path: object.path.clone(),
                        sha: sha.clone(),
                    },
                    gate(app),
                )
                .await?;
            record_reply(app, &object, reply, false).await?;
        } else {
            let reply = app
                .archiver
                .library(
                    Request::Inspect {
                        identity: pin.into(),
                        path: object.path.clone(),
                        sha: sha.clone(),
                        size: object.size,
                        fingerprint: object.fingerprint.clone(),
                    },
                    gate(app),
                )
                .await?;
            record_reply(app, &object, reply, true).await?;
            guard(app, &sha).await?;
        }
        finalize_inbox(
            app,
            file.customer,
            file.car.clone(),
            file.relative.clone(),
            sha,
            file.size,
        )
        .await?;
        app.events.library_changed(file.customer, "setups");
        imported += 1;
    }
    Ok(imported)
}

async fn finalize_inbox(
    app: &AppState,
    customer: i64,
    car: String,
    relative: String,
    content_sha: String,
    size: u64,
) -> Result<()> {
    app.store.write(move|c|{
            let tx=c.transaction()?;let now=iso_now();
            if let Some(kind)=tx.query_row("SELECT kind FROM blobs WHERE sha256=?1",[&content_sha],|r|r.get::<_,String>(0)).optional()? && kind!="sto" {return Err(Error::conflict("upload_kind_conflict","same hash already belongs to another kind"));}
            let seen:bool=tx.query_row("SELECT EXISTS(SELECT 1 FROM nas_inbox_receipts WHERE customer_id=?1 AND car_folder=?2 AND relative_path=?3 AND sha256=?4)",params![customer,car,relative,content_sha],|r|r.get(0))?;
            if !seen {
            tx.execute("INSERT OR IGNORE INTO blobs(sha256,size,kind,first_name,received_at,spool_present,nas_state,nas_archived_at) VALUES(?1,?2,'sto',?3,?4,0,'archived',?4)",params![content_sha,size as i64,relative,now])?;
            let full=format!("{car}/NAS/{relative}");
            tx.execute("INSERT INTO blob_occurrences(sha256,kind,file_name,relative_path,customer_id,seen_at) VALUES(?1,'sto',?2,?3,?4,?5)",params![content_sha,relative,full,customer,now])?;
            crate::ingest::link_setup_file(&tx,customer,&content_sha,"sto",Some(&full),&relative)?;
            tx.execute("INSERT INTO nas_inbox_receipts(customer_id,car_folder,relative_path,sha256,imported_at) VALUES(?1,?2,?3,?4,?5)",params![customer,car,relative,content_sha,now])?;
            crate::db::bump_generation(&tx)?;
            }
            tx.execute("DELETE FROM nas_inbox_pending WHERE customer_id=?1 AND car_folder=?2 AND relative_path=?3 AND sha256=?4",params![customer,car,relative,content_sha])?;
            tx.commit()?;Ok(())
        }).await
}

/// Bounded at 128 objects per pass with a durable round-robin key. No startup migration.
pub async fn reconcile(app: &AppState) -> Result<Value> {
    let _lock = app.idem_locks.lock("nas-library-pass").await;
    let result=async {
        let pin=identity(app).await?;seed(app).await?;
        let objects=app.store.read(|c|{
            let cursor:String=c.query_row("SELECT object_cursor FROM nas_library_meta WHERE singleton=1",[],|r|r.get(0))?;
            let mut stmt=c.prepare("SELECT kind,sha256,size,relative_path,legacy_path,state,verified_fingerprint FROM nas_library_objects WHERE state NOT IN ('legacy','rename_pending') AND kind||':'||sha256>?1 ORDER BY kind,sha256 LIMIT 64")?;
            let mut out=stmt.query_map([cursor],|r|Ok((Object{kind:r.get(0)?,sha:r.get(1)?,size:r.get::<_,i64>(2)? as u64,path:r.get(3)?,legacy:r.get(4)?,state:r.get(5)?,fingerprint:r.get(6)?},true)))?.collect::<rusqlite::Result<Vec<_>>>()?;
            let mut pending=c.prepare("SELECT kind,sha256,size,relative_path,legacy_path,state,verified_fingerprint FROM nas_library_objects WHERE state IN ('legacy','rename_pending') ORDER BY CASE kind WHEN 'sto' THEN 0 WHEN 'htm' THEN 1 WHEN 'rpy' THEN 2 ELSE 3 END,sha256 LIMIT 128")?;
            out.extend(pending.query_map([],|r|Ok((Object{kind:r.get(0)?,sha:r.get(1)?,size:r.get::<_,i64>(2)? as u64,path:r.get(3)?,legacy:r.get(4)?,state:r.get(5)?,fingerprint:r.get(6)?},false)))?.collect::<rusqlite::Result<Vec<_>>>()?);Ok(out)
        }).await?;
        let mut errors=Vec::new();let mut verified=0;let mut cursor=String::new();
        let started=std::time::Instant::now();
        for (object,verification) in &objects {
            match reconcile_object(app,&pin,object.clone()).await {Ok(())=>verified+=1,Err(Error::Crash(p))=>return Err(Error::Crash(p)),Err(e)=>{errors.push(e.to_string());let(k,s,message)=(object.kind.clone(),object.sha.clone(),e.to_string());app.store.write(move|c|{c.execute("UPDATE nas_library_objects SET error=?3 WHERE kind=?1 AND sha256=?2",params![k,s,message])?;Ok(())}).await?;}}
            if *verification{cursor=format!("{}:{}",object.kind,object.sha);}
            if !app.hub.allows_library_work()||(!verification&&object.size>(64<<20))||started.elapsed().as_secs()>=15{break;}
        }
        app.store.write(move|c|{c.execute("UPDATE nas_library_meta SET object_cursor=?1 WHERE singleton=1",[cursor])?;Ok(())}).await?;
        let pending=app.store.read(|c|Ok(c.prepare("SELECT p.customer_id,p.car_folder,p.relative_path,p.sha256,p.size FROM nas_inbox_pending p JOIN nas_library_objects n ON n.kind='sto' AND n.sha256=p.sha256 WHERE n.state='canonical_verified' LIMIT 128")?.query_map([],|r|Ok((r.get::<_,i64>(0)?,r.get::<_,String>(1)?,r.get::<_,String>(2)?,r.get::<_,String>(3)?,r.get::<_,i64>(4)?)))?.collect::<rusqlite::Result<Vec<_>>>()?)).await?;
        for(customer,car,path,sha,size)in pending{finalize_inbox(app,customer,car,path,sha,size as u64).await?;}
        let imported=match import_inbox(app,&pin).await{Ok(n)=>n,Err(e)=>{errors.push(e.to_string());0}};
        if !errors.is_empty(){return Err(Error::unavailable(60,errors.join("; ")));}
        Ok(json!({"verified":verified,"imported":imported}))
    }.await;
    let mut error = result.as_ref().err().map(ToString::to_string);
    if error.is_none() {
        error=app.store.read(|c|Ok(c.query_row("SELECT error FROM nas_library_objects WHERE error IS NOT NULL AND state<>'deleted' ORDER BY kind,sha256 LIMIT 1",[],|r|r.get::<_,String>(0)).optional()?)).await?;
    }
    let success = error.is_none();
    app.store.write(move|c|{c.execute("UPDATE nas_library_meta SET last_error=?1,last_ok_at=CASE WHEN ?2 THEN ?3 ELSE last_ok_at END WHERE singleton=1",params![error,success,iso_now()])?;Ok(())}).await?;
    result
}

pub async fn status(app: &AppState) -> Result<Value> {
    app.store.read(|c|{
    let(pending,verified,deleted,conflicts):(i64,i64,i64,i64)=c.query_row("SELECT COALESCE(SUM(state IN ('legacy','rename_pending')),0),COALESCE(SUM(state='canonical_verified'),0),COALESCE(SUM(state='deleted'),0),COALESCE(SUM(state='conflict'),0) FROM nas_library_objects",[],|r|Ok((r.get(0)?,r.get(1)?,r.get(2)?,r.get(3)?)))?;
    let unseeded:i64=c.query_row("SELECT COUNT(*) FROM (SELECT kind,sha256 FROM blobs WHERE kind IN ('ibt','sto','htm','rpy') UNION SELECT 'sto' AS kind,sha256 FROM sto_revisions) b WHERE NOT EXISTS(SELECT 1 FROM nas_library_objects n WHERE n.kind=b.kind AND n.sha256=b.sha256)",[],|r|r.get(0))?;
    let(error,last):(Option<String>,Option<String>)=c.query_row("SELECT last_error,last_ok_at FROM nas_library_meta WHERE singleton=1",[],|r|Ok((r.get(0)?,r.get(1)?)))?;
    let pending=pending+unseeded;let state=if error.as_deref().is_some_and(|e|e.contains("paused")||e.contains("identity")||e.contains("racing")){"paused"}else if error.is_some()||conflicts>0{"error"}else if pending>0||last.is_none(){"pending"}else{"ready"};
    Ok(json!({"state":state,"pending":pending,"verified":verified,"deleted":deleted,"conflicts":conflicts,"last_error":error,"last_ok_at":last,"path":"library"}))
}).await
}

pub async fn header(app: &AppState, sha: &str) -> Result<Option<Value>> {
    let _lock = app.upload_locks.lock(sha).await;
    guard(app, sha).await?;
    let key = sha.to_string();
    let object = app
        .store
        .read(move |c| Ok(read_object(c, "ibt", &key)?))
        .await?;
    let path = if let Some(object) = object {
        if object.state == "conflict" {
            return Err(Error::unprocessable(
                "library_conflict",
                "recording archive requires review",
            ));
        }
        if object.state == "canonical_verified" {
            object.path
        } else {
            object.legacy.unwrap_or(object.path)
        }
    } else {
        format!("raw/{}/{sha}.ibt", &sha[..2])
    };
    let Reply::Header(value) = app
        .archiver
        .library(
            Request::Header {
                identity: identity(app).await?,
                path,
                parser: app.parser.clone(),
            },
            gate(app),
        )
        .await?
    else {
        return Err(Error::internal("invalid archived header reply"));
    };
    Ok(Some(value))
}

pub async fn restage(app: &AppState, sha: &str, kind: &str, size: u64, mtime: i64) -> Result<bool> {
    guard(app, sha).await?;
    let (k, s) = (kind.to_string(), sha.to_string());
    let object = app.store.read(move |c| Ok(read_object(c, &k, &s)?)).await?;
    let Some(object) = object else {
        return Ok(false);
    };
    if object.state == "conflict" {
        return Err(Error::unprocessable(
            "library_conflict",
            "library payload requires review",
        ));
    }
    reconcile_object(app, &identity(app).await?, object.clone()).await?;
    let _lock = app.upload_locks.lock(sha).await;
    guard(app, sha).await?;
    app.archiver
        .library(
            Request::Restage {
                identity: identity(app).await?,
                path: object.path,
                sha: sha.into(),
                size,
                dest: crate::ingest::ready_path(app, sha),
                tmp: app.cfg.spool_incoming(),
                mtime,
            },
            gate(app),
        )
        .await?;
    Ok(true)
}

#[derive(Default, Deserialize)]
pub struct SyncQuery {
    cursor: Option<String>,
}

fn managed(file: &Value) -> Result<String> {
    let car = file["car_folder"].as_str().unwrap_or_default();
    if !fs::component(car, 64) {
        return Err(Error::invalid("unsafe setup car folder"));
    }
    let id = file["id"]
        .as_str()
        .ok_or_else(|| Error::internal("setup identity missing"))?;
    let hash = file["sha256"]
        .as_str()
        .filter(|s| crate::util::is_sha256_hex(s))
        .ok_or_else(|| Error::internal("setup hash missing"))?;
    let stem = fs::stem(file["name"].as_str().unwrap_or("setup.sto"), 32);
    let short = sha256_hex(id.as_bytes());
    let path = if file["locked"] == true {
        format!(
            "{car}/Coach Synced/NAS/{stem}--{}--{}.sto",
            &short[..12],
            &hash[..16]
        )
    } else {
        format!(
            "{car}/Coach Synced/{stem}--{}--v{}--{}.sto",
            &short[..12],
            file["current_version"].as_i64().unwrap_or(1),
            &hash[..16]
        )
    };
    if path.encode_utf16().count() > 180 {
        return Err(Error::invalid(
            "managed setup path exceeds 180 UTF-16 units",
        ));
    }
    Ok(path)
}

pub async fn sync(
    State(app): State<AppState>,
    Path(collector): Path<String>,
    Query(query): Query<SyncQuery>,
) -> Result<Response> {
    let customer = app
        .store
        .read(move |c| {
            Ok(c.query_row(
                "SELECT last_customer_id FROM collectors WHERE collector_id=?1",
                [collector],
                |r| r.get::<_, Option<i64>>(0),
            )
            .optional()?
            .flatten())
        })
        .await?;
    let Some(customer) = customer else {
        return Ok(crate::api::json_ok(
            json!({"schema_version":1,"replay_upload":true,"customer_id":null,"files":[],"next_cursor":null}),
        ));
    };
    let after = if let Some(cursor) = query.cursor {
        let bytes = URL_SAFE_NO_PAD
            .decode(cursor)
            .map_err(|_| Error::invalid("invalid sync cursor"))?;
        let value: Value =
            serde_json::from_slice(&bytes).map_err(|_| Error::invalid("invalid sync cursor"))?;
        if value["customer_id"] != customer {
            return Err(Error::conflict(
                "sync_driver_changed",
                "Collector driver changed; restart setup sync",
            ));
        }
        value["key"]
            .as_str()
            .ok_or_else(|| Error::invalid("invalid sync cursor"))?
            .to_string()
    } else {
        String::new()
    };
    let files=app.store.read(move|c|{
        let mut files=crate::sto::sources(c,customer)?.into_iter().filter(|f|f["present"]==true&&f["name"].as_str().is_some_and(|s|s.starts_with("NAS/"))).collect::<Vec<_>>();
        let ids=c.prepare("SELECT file_id FROM sto_working_files WHERE customer_id=?1 ORDER BY file_id")?.query_map([customer],|r|r.get::<_,i64>(0))?.collect::<rusqlite::Result<Vec<_>>>()?;
        for id in ids {if let Some(file)=crate::sto::working(c,customer,id)?{files.push(file);}}
        let mut eligible=Vec::new();
        for file in files {let id=file["id"].as_str().unwrap_or_default();if id<=after.as_str(){continue;}let sha=file["sha256"].as_str().unwrap_or_default();
            let available:bool=c.query_row("SELECT EXISTS(SELECT 1 FROM nas_library_objects WHERE kind='sto' AND sha256=?1 AND state='canonical_verified')",[sha],|r|r.get(0))?;
            if !available{continue;}let size:i64=c.query_row("SELECT size FROM nas_library_objects WHERE kind='sto' AND sha256=?1",[sha],|r|r.get(0))?;
            if !(1..=fs::SETUP_LIMIT as i64).contains(&size){continue;}let relative=managed(&file)?;
            eligible.push(json!({"id":id,"version":file["current_version"],"sha256":sha,"size":size,"relative_path":relative,"download_url":format!("/api/v1/drivers/{customer}/sto-files/{id}/download?version={}",file["current_version"].as_i64().unwrap_or(1))}));
        }
        eligible.sort_by_key(|f|f["id"].as_str().unwrap_or_default().to_string());eligible.truncate(129);Ok(eligible)
    }).await?;
    let next = if files.len() > 128 {
        Some(
            URL_SAFE_NO_PAD
                .encode(json!({"customer_id":customer,"key":files[127]["id"]}).to_string()),
        )
    } else {
        None
    };
    Ok(crate::api::json_ok(
        json!({"schema_version":1,"replay_upload":true,"customer_id":customer,"files":files.into_iter().take(128).collect::<Vec<_>>(),"next_cursor":next}),
    ))
}

#[cfg(test)]
mod stale_catalog_tests {
    use super::*;
    #[tokio::test]
    async fn an_old_pending_snapshot_cannot_republish_a_later_deleted_object() {
        let tmp = tempfile::tempdir().unwrap();
        let mut cfg = crate::Config::base(tmp.path().join("data"), tmp.path().join("nas"), vec![]);
        cfg.background = false;
        let app = crate::App::start(cfg, crate::Parts::default())
            .await
            .unwrap();
        let pin = identity(&app).await.unwrap();
        let bytes = b"stale catalog proof";
        let sha = sha256_hex(bytes);
        let path = canonical("rpy", &sha, "proof.rpy", None, "", None).unwrap();
        let snapshot = register(&app, "rpy", &sha, bytes.len() as u64, path.clone(), None)
            .await
            .unwrap();
        let target = app.cfg.nas_root.join(&path);
        std::fs::create_dir_all(target.parent().unwrap()).unwrap();
        std::fs::write(&target, bytes).unwrap();
        std::fs::write(crate::ingest::ready_path(&app, &sha), bytes).unwrap();
        let s = sha.clone();
        app.store
            .write(move |c| {
                c.execute(
                    "UPDATE nas_library_objects SET state='canonical_verified' WHERE sha256=?1",
                    [s],
                )?;
                Ok(())
            })
            .await
            .unwrap();
        std::fs::remove_file(&target).unwrap();
        reconcile_object(&app, &pin, snapshot).await.unwrap();
        assert!(is_deleted(&app, &sha).await.unwrap());
        assert!(!target.exists());
    }
}

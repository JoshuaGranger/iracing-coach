//! Upload and ingest (`collector-protocol.md` section 4): create or resume, chunked append with
//! fsync before acknowledgement, whole-file verification and atomic commit into `ready/`, start-up
//! reconciliation, then header and session-info parsing (setup library fill, D9).

use std::fs::{self, OpenOptions};
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::PathBuf;

use axum::http::StatusCode;
use rusqlite::{Connection, OptionalExtension, params};
use serde_json::{Value, json};

use crate::app::AppState;
use crate::error::{ApiError, Error, Result};
use crate::jobs::{self, NewJob};
use crate::schema::validate_request;
use crate::util::{self, iso_now};

#[derive(Debug, Clone)]
pub struct UploadRow {
    pub sha256: String,
    pub size: i64,
    pub kind: String,
    pub file_name: String,
    pub relative_path: Option<String>,
    pub source_mtime: Option<String>,
    pub collector_id: Option<String>,
    pub durable_offset: i64,
    pub state: String,
}

fn read_upload(conn: &Connection, sha: &str) -> rusqlite::Result<Option<UploadRow>> {
    conn.query_row(
        "SELECT sha256, size, kind, file_name, relative_path, source_mtime, collector_id,
                durable_offset, state FROM uploads WHERE sha256 = ?1",
        [sha],
        |r| {
            Ok(UploadRow {
                sha256: r.get(0)?,
                size: r.get(1)?,
                kind: r.get(2)?,
                file_name: r.get(3)?,
                relative_path: r.get(4)?,
                source_mtime: r.get(5)?,
                collector_id: r.get(6)?,
                durable_offset: r.get(7)?,
                state: r.get(8)?,
            })
        },
    )
    .optional()
}

fn blob_exists(conn: &Connection, sha: &str) -> rusqlite::Result<bool> {
    conn.query_row(
        "SELECT COUNT(*) > 0 FROM blobs WHERE sha256 = ?1",
        [sha],
        |r| r.get(0),
    )
}

pub fn part_path(app: &AppState, sha: &str) -> PathBuf {
    app.cfg.spool_incoming().join(format!("{sha}.part"))
}

pub fn ready_path(app: &AppState, sha: &str) -> PathBuf {
    app.cfg.spool_ready().join(sha)
}

/// The collector-facing state name of a held blob.
pub fn derive_blob_state(conn: &Connection, sha: &str) -> rusqlite::Result<String> {
    if crate::nas_library::deleted(conn, sha)? {
        return Ok("deleted".into());
    }
    let (kind, nas): (String, String) = conn.query_row(
        "SELECT kind, nas_state FROM blobs WHERE sha256 = ?1",
        [sha],
        |r| Ok((r.get(0)?, r.get(1)?)),
    )?;
    let archived = nas == "archived";
    if kind != "ibt" {
        return Ok(if archived { "archived" } else { "verified" }.into());
    }
    let ingest: Option<String> = conn
        .query_row(
            "SELECT ingest_state FROM recordings WHERE sha256 = ?1",
            [sha],
            |r| r.get(0),
        )
        .optional()?;
    Ok(match ingest.as_deref() {
        None | Some("verified") => "verified".into(),
        Some("failed") => "failed".into(),
        Some("parsed") => if archived { "archived" } else { "parsed" }.into(),
        Some(_) => {
            let analysis: Option<String> = conn
                .query_row(
                    "SELECT s.analysis_state FROM session_recordings sr
                     JOIN sessions s ON s.session_id = sr.session_id
                     WHERE sr.recording_sha256 = ?1
                     ORDER BY CASE s.analysis_state WHEN 'ready' THEN 0 WHEN 'running' THEN 1
                       WHEN 'queued' THEN 2 WHEN 'deferred' THEN 3 ELSE 4 END LIMIT 1",
                    [sha],
                    |r| r.get(0),
                )
                .optional()?;
            match analysis.as_deref() {
                Some("ready") => "analyzed".into(),
                Some("queued") | Some("running") => "analysis_queued".into(),
                Some("deferred") => "deferred".into(),
                Some("failed") => "failed".into(),
                _ => if archived { "archived" } else { "discovered" }.into(),
            }
        }
    })
}

fn upload_state_json(sha: &str, state: &str, offset: i64, size: i64) -> Value {
    json!({"sha256": sha, "state": state, "offset": offset, "size": size})
}

/// Resolves the driver an occurrence belongs to: as reported, else the collector's last driver,
/// else the only driver known (D7: one driver opens directly).
fn resolve_customer(
    conn: &Connection,
    reported: Option<i64>,
    collector_id: Option<&str>,
) -> rusqlite::Result<Option<i64>> {
    if let Some(id) = reported {
        crate::db::upsert_driver(conn, id, None, None)?;
        return Ok(Some(id));
    }
    if let Some(cid) = collector_id {
        let last: Option<i64> = conn
            .query_row(
                "SELECT last_customer_id FROM collectors WHERE collector_id = ?1",
                [cid],
                |r| r.get(0),
            )
            .optional()?
            .flatten();
        if last.is_some() {
            return Ok(last);
        }
    }
    let ids: Vec<i64> = conn
        .prepare("SELECT customer_id FROM drivers LIMIT 2")?
        .query_map([], |r| r.get(0))?
        .collect::<rusqlite::Result<_>>()?;
    Ok(if ids.len() == 1 { Some(ids[0]) } else { None })
}

/// Records the file under the driver's setups folder (`setup_files`), superseding earlier
/// blobs at the same path.
pub fn link_setup_file(
    conn: &Connection,
    customer_id: i64,
    sha: &str,
    kind: &str,
    relative_path: Option<&str>,
    file_name: &str,
) -> rusqlite::Result<()> {
    let full = relative_path.unwrap_or(file_name).replace('\\', "/");
    let full = full.trim_start_matches('/');
    let (car_folder, rest) = match full.split_once('/') {
        Some((car, rest)) if !rest.is_empty() => (car.to_string(), rest.to_string()),
        _ => (String::new(), full.to_string()),
    };
    let now = iso_now();
    conn.execute(
        "UPDATE setup_files SET present = 0
         WHERE customer_id = ?1 AND car_folder = ?2 AND relative_path = ?3 AND blob_sha256 <> ?4",
        params![customer_id, car_folder, rest, sha],
    )?;
    conn.execute(
        "INSERT INTO setup_files(customer_id, blob_sha256, car_folder, relative_path, format,
                                 first_seen_at, last_seen_at, present)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?6, 1)
         ON CONFLICT(customer_id, car_folder, relative_path, blob_sha256)
         DO UPDATE SET last_seen_at = ?6, present = 1",
        params![customer_id, sha, car_folder, rest, kind, now],
    )?;
    Ok(())
}

enum Existing {
    Blob(String, i64),
    Upload(UploadRow),
    Nothing,
}

/// `POST /api/v1/uploads`.
pub async fn create_upload(app: &AppState, body: &[u8]) -> Result<(StatusCode, Value)> {
    let value: Value = serde_json::from_slice(body)
        .map_err(|e| Error::invalid(format!("body is not valid JSON: {e}")))?;
    let limit = if value["kind"] == "rpy" {
        (16u64 << 30).min(app.cfg.spool_limit_bytes)
    } else {
        app.cfg.max_upload_bytes.min(4u64 << 30)
    };
    if let Some(size) = value["size"].as_u64()
        && size > limit
    {
        return Err(Error::Api(ApiError::new(
            StatusCode::PAYLOAD_TOO_LARGE,
            "too_large",
            format!("uploads of this kind are limited to {limit} bytes"),
        )));
    }
    validate_request("UploadCreate", &value)?;
    let sha = value["sha256"].as_str().unwrap_or_default().to_string();
    let size = value["size"].as_i64().unwrap_or_default();
    let kind = value["kind"].as_str().unwrap_or_default().to_string();
    if kind == "field" && size > 64 * 1024 * 1024 {
        return Err(Error::invalid("field segments are limited to 64 MiB"));
    }
    let file_name = value["file_name"].as_str().unwrap_or_default().to_string();
    let relative_path = value["relative_path"].as_str().map(str::to_string);
    let source_mtime = value["source_mtime"].as_str().map(str::to_string);
    let collector_id = value["collector_id"]
        .as_str()
        .unwrap_or_default()
        .to_string();
    let reported = value["customer_id"].as_i64();

    // One hash lock covers every state read and the decision: a concurrent create or chunk for
    // the same bytes can no longer act on a stale view (review 6).
    let _guard = app.upload_locks.lock(&sha).await;
    let existing = {
        let (sha, kind, file_name, relative_path, source_mtime, collector_id) = (
            sha.clone(),
            kind.clone(),
            file_name.clone(),
            relative_path.clone(),
            source_mtime.clone(),
            collector_id.clone(),
        );
        app.store
            .write(move |conn| {
                let tx = conn.transaction()?;
                let now = iso_now();
                let existing_kind: Option<String> = tx.query_row("SELECT kind FROM blobs WHERE sha256=?1 UNION ALL SELECT kind FROM uploads WHERE sha256=?1 UNION ALL SELECT kind FROM nas_library_objects WHERE sha256=?1 LIMIT 1",[&sha],|r|r.get(0)).optional()?;
                if existing_kind.as_deref().is_some_and(|k|k!=kind) { return Err(Error::conflict("upload_kind_conflict","This hash already belongs to another upload kind")); }
                if crate::nas_library::deleted(&tx,&sha)? { return Ok(Existing::Blob("deleted".into(),size)); }
                tx.execute(
                    "INSERT OR IGNORE INTO collectors(collector_id, machine_name, collector_version,
                       protocol, first_seen_at, last_seen_at)
                     VALUES (?1, 'unknown', 'unknown', 0, ?2, ?2)",
                    params![collector_id, now],
                )?;
                let customer = resolve_customer(&tx, reported, Some(&collector_id))?;
                tx.execute(
                    "INSERT INTO blob_occurrences(sha256, kind, file_name, relative_path, source_mtime,
                                                  collector_id, customer_id, seen_at)
                     SELECT ?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8
                     WHERE NOT EXISTS (SELECT 1 FROM blob_occurrences
                        WHERE sha256 = ?1 AND collector_id IS ?6 AND relative_path IS ?4
                          AND file_name = ?3 AND source_mtime IS ?5)",
                    params![sha, kind, file_name, relative_path, source_mtime, collector_id, customer, now],
                )?;
                let existing = if blob_exists(&tx, &sha)? {
                    if matches!(kind.as_str(), "sto" | "htm")
                        && let Some(customer) = customer
                    {
                        link_setup_file(&tx, customer, &sha, &kind, relative_path.as_deref(), &file_name)?;
                    }
                    let size: i64 =
                        tx.query_row("SELECT size FROM blobs WHERE sha256 = ?1", [&sha], |r| r.get(0))?;
                    Existing::Blob(derive_blob_state(&tx, &sha)?, size)
                } else {
                    match read_upload(&tx, &sha)? {
                        Some(row) => Existing::Upload(row),
                        None => Existing::Nothing,
                    }
                };
                tx.commit()?;
                Ok(existing)
            })
            .await?
    };

    match existing {
        Existing::Blob(state, size) => Ok((
            StatusCode::OK,
            upload_state_json(
                &sha,
                &state,
                if state == "deleted" { 0 } else { size },
                size,
            ),
        )),
        Existing::Upload(row) if row.state == "open" => {
            if row.size != size {
                return Err(Error::invalid(
                    "size differs from the open upload of this hash",
                ));
            }
            match settle(app, &sha).await? {
                Settled::Verified => {
                    let state = blob_state(app, &sha).await?;
                    Ok((StatusCode::OK, upload_state_json(&sha, &state, size, size)))
                }
                Settled::Mismatch => {
                    admit(app, 0).await?;
                    Ok((StatusCode::OK, upload_state_json(&sha, "open", 0, size)))
                }
                Settled::Open(offset) => {
                    // The resume's remaining bytes are already reserved in `uploads`.
                    admit(app, 0).await?;
                    Ok((
                        StatusCode::OK,
                        upload_state_json(&sha, "open", offset, size),
                    ))
                }
            }
        }
        Existing::Upload(_) | Existing::Nothing => {
            // Check and registration are one step under the admission gate.
            let _gate = app.spool.gate.lock().await;
            check_admission(app, size as u64).await?;
            let part = part_path(app, &sha);
            let _ = fs::remove_file(&part);
            {
                let (sha, kind, file_name, relative_path, source_mtime, collector_id) = (
                    sha.clone(),
                    kind.clone(),
                    file_name.clone(),
                    relative_path.clone(),
                    source_mtime.clone(),
                    collector_id.clone(),
                );
                app.store
                    .write(move |conn| {
                        let now = iso_now();
                        conn.execute(
                            "INSERT INTO uploads(sha256, size, kind, file_name, relative_path, source_mtime,
                               collector_id, durable_offset, state, created_at, updated_at)
                             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, 0, 'open', ?8, ?8)
                             ON CONFLICT(sha256) DO UPDATE SET size = ?2, kind = ?3, file_name = ?4,
                               relative_path = ?5, source_mtime = ?6, collector_id = ?7,
                               durable_offset = 0, state = 'open', error = NULL, updated_at = ?8",
                            params![sha, size, kind, file_name, relative_path, source_mtime, collector_id, now],
                        )?;
                        Ok(())
                    })
                    .await?;
            }
            app.faults.check("upload.after_create")?;
            Ok((
                StatusCode::CREATED,
                upload_state_json(&sha, "open", 0, size),
            ))
        }
    }
}

async fn blob_state(app: &AppState, sha: &str) -> Result<String> {
    let sha = sha.to_string();
    app.store
        .read(move |conn| Ok(derive_blob_state(conn, &sha)?))
        .await
}

/// Takes the admission gate and checks `extra` more reserved bytes (a resume adds nothing).
async fn admit(app: &AppState, extra: u64) -> Result<()> {
    let _gate = app.spool.gate.lock().await;
    check_admission(app, extra).await
}

/// Refuses with 503 when the spool or `/data` cannot take `extra` more reserved bytes: the spool
/// counts incoming, `ready/` and restaged bytes plus every outstanding reservation against the
/// limit, and the filesystem must keep the minimum free after all of them are written. `extra`
/// is the increment only: a new upload's whole size, nothing for a resume, whose remaining bytes
/// are already reserved in `uploads` (a resume counted twice could never resume near the limit).
/// The caller holds the admission gate.
pub async fn check_admission(app: &AppState, extra: u64) -> Result<()> {
    cleanup_idle_uploads(app).await?;
    let (incoming, reserved, ready): (i64, i64, i64) = app
        .store
        .read(move |conn| {
            let (incoming, reserved): (i64, i64) = conn.query_row(
                "SELECT COALESCE(SUM(durable_offset), 0), COALESCE(SUM(size - durable_offset), 0)
                 FROM uploads WHERE state = 'open'",
                [],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )?;
            let ready: i64 = conn.query_row(
                "SELECT COALESCE(SUM(size), 0) FROM blobs WHERE spool_present = 1",
                [],
                |r| r.get(0),
            )?;
            Ok((incoming, reserved, ready))
        })
        .await?;
    let outstanding = (reserved.max(0) as u64).saturating_add(app.spool.restaging_bytes());
    let used = (incoming.max(0) as u64)
        .saturating_add(ready.max(0) as u64)
        .saturating_add(outstanding)
        .saturating_add(extra);
    let free = app.disk.free_bytes(&app.cfg.data_dir);
    let needed_free = outstanding
        .saturating_add(extra)
        .saturating_add(app.cfg.min_free_bytes);
    if used > app.cfg.spool_limit_bytes || free < needed_free {
        return Err(Error::unavailable(
            app.cfg.retry_after_secs,
            "spool is full or /data has too little free space; retry later",
        ));
    }
    Ok(())
}

/// Deletes `.part` files idle for `part_idle_secs` and releases their reservation. The state and
/// the cutoff are checked again under the hash lock: a chunk that arrived since the list was
/// read keeps its upload.
pub async fn cleanup_idle_uploads(app: &AppState) -> Result<usize> {
    let cutoff = util::iso_from_unix(util::unix_now() - app.cfg.part_idle_secs);
    let stale: Vec<String> = {
        let cutoff = cutoff.clone();
        app.store
            .read(move |conn| {
                let mut stmt = conn.prepare(
                    "SELECT sha256 FROM uploads WHERE state = 'open' AND updated_at < ?1",
                )?;
                Ok(stmt
                    .query_map([cutoff], |r| r.get::<_, String>(0))?
                    .collect::<rusqlite::Result<Vec<_>>>()?)
            })
            .await?
    };
    let mut removed = 0;
    for sha in &stale {
        if remove_idle_upload(app, sha, &cutoff).await? {
            removed += 1;
        }
    }
    Ok(removed)
}

/// Removes one upload that is still idle at `cutoff`. False when a request holds its hash lock,
/// or when the row is gone or was touched since the caller listed it: the check and the delete
/// are one statement under the lock, so a chunk acknowledged in between keeps its upload.
pub async fn remove_idle_upload(app: &AppState, sha: &str, cutoff: &str) -> Result<bool> {
    let Some(_guard) = app.upload_locks.try_lock(sha) else {
        return Ok(false);
    };
    let (sha_owned, cutoff) = (sha.to_string(), cutoff.to_string());
    let deleted = app
        .store
        .write(move |conn| {
            Ok(conn.execute(
                "DELETE FROM uploads WHERE sha256 = ?1 AND state = 'open' AND updated_at < ?2",
                params![sha_owned, cutoff],
            )?)
        })
        .await?;
    if deleted > 0 {
        let _ = fs::remove_file(part_path(app, sha));
    }
    Ok(deleted > 0)
}

#[derive(Debug, PartialEq)]
pub enum Settled {
    Verified,
    Open(i64),
    /// The whole-file hash did not match; the part was discarded and the upload is at 0.
    Mismatch,
}

/// Aligns the file system with the upload row (per-hash lock held by the caller): finishes a
/// commit that crashed after the rename, completes a full `.part`, or reports the durable offset.
pub async fn settle(app: &AppState, sha: &str) -> Result<Settled> {
    crate::nas_library::guard(app, sha).await?;
    let sha_owned = sha.to_string();
    let (row, has_blob) = app
        .store
        .read(move |conn| {
            Ok((
                read_upload(conn, &sha_owned)?,
                blob_exists(conn, &sha_owned)?,
            ))
        })
        .await?;
    if has_blob {
        return Ok(Settled::Verified);
    }
    let Some(row) = row else {
        return Err(Error::not_found("no such upload"));
    };
    let ready = ready_path(app, sha);
    let part = part_path(app, sha);
    if ready.exists() {
        // crash between the rename and the transaction
        if adopt_ready(app, &row).await? {
            return Ok(Settled::Verified);
        }
        set_offset(app, sha, 0).await?;
        return Ok(Settled::Open(0));
    }
    if part.exists() {
        let len = fs::metadata(&part)?.len() as i64;
        if len > row.size {
            let _ = fs::remove_file(&part);
            set_offset(app, sha, 0).await?;
            return Ok(Settled::Open(0));
        }
        if len == row.size {
            return match commit_part(app, &row).await? {
                true => Ok(Settled::Verified),
                false => Ok(Settled::Mismatch),
            };
        }
        if len != row.durable_offset {
            set_offset(app, sha, len).await?;
        }
        return Ok(Settled::Open(len));
    }
    if row.durable_offset != 0 {
        set_offset(app, sha, 0).await?;
    }
    Ok(Settled::Open(0))
}

async fn set_offset(app: &AppState, sha: &str, offset: i64) -> Result<()> {
    let sha = sha.to_string();
    app.store
        .write(move |conn| {
            conn.execute(
                "UPDATE uploads SET durable_offset = ?2, updated_at = ?3 WHERE sha256 = ?1",
                params![sha, offset, iso_now()],
            )?;
            Ok(())
        })
        .await
}

/// `HEAD /api/v1/uploads/{sha256}`: (offset, state) or None.
pub async fn head_upload(app: &AppState, sha: &str) -> Result<Option<(i64, String)>> {
    let sha = sha.to_string();
    app.store
        .read(move |conn| {
            if crate::nas_library::deleted(conn, &sha)? {
                return Ok(Some((0, "deleted".into())));
            }
            if blob_exists(conn, &sha)? {
                let size: i64 =
                    conn.query_row("SELECT size FROM blobs WHERE sha256 = ?1", [&sha], |r| {
                        r.get(0)
                    })?;
                return Ok(Some((size, derive_blob_state(conn, &sha)?)));
            }
            Ok(read_upload(conn, &sha)?.map(|u| (u.durable_offset, u.state)))
        })
        .await
}

/// `PUT /api/v1/uploads/{sha256}`: one chunk. Returns (status, body).
pub async fn put_chunk(
    app: &AppState,
    sha: &str,
    start: i64,
    end: i64,
    size: i64,
    body: &[u8],
) -> Result<(StatusCode, Value)> {
    if head_upload(app, sha)
        .await?
        .is_some_and(|(_, s)| s == "deleted")
    {
        return Ok((StatusCode::OK, upload_state_json(sha, "deleted", 0, size)));
    }
    let Some(_guard) = app.upload_locks.try_lock(sha) else {
        let (offset, state) = head_upload(app, sha).await?.unwrap_or((0, "open".into()));
        if state == "deleted" {
            return Ok((StatusCode::OK, upload_state_json(sha, "deleted", 0, size)));
        }
        return Ok((
            StatusCode::CONFLICT,
            upload_state_json(sha, "open", offset, size),
        ));
    };
    let sha_owned = sha.to_string();
    let (row, has_blob) = app
        .store
        .read(move |conn| {
            Ok((
                read_upload(conn, &sha_owned)?,
                blob_exists(conn, &sha_owned)?,
            ))
        })
        .await?;
    if has_blob {
        let state = blob_state(app, sha).await?;
        return Ok((StatusCode::OK, upload_state_json(sha, &state, size, size)));
    }
    if head_upload(app, sha)
        .await?
        .is_some_and(|(_, s)| s == "deleted")
    {
        return Ok((StatusCode::OK, upload_state_json(sha, "deleted", 0, size)));
    }
    let Some(row) = row else {
        return Err(Error::not_found("no such upload; create it first"));
    };
    if row.state != "open" {
        return Err(Error::conflict(
            "upload_closed",
            format!("upload is {}", row.state),
        ));
    }
    if row.size != size {
        return Err(Error::invalid(
            "Content-Range size differs from the upload's size",
        ));
    }
    if start < 0 || end < start || end >= size || body.len() as i64 != end - start + 1 {
        return Err(Error::invalid(
            "Content-Range does not match the body length",
        ));
    }
    // Align with the file system first (finishes a crashed commit, fixes a lagging offset).
    match settle(app, sha).await? {
        Settled::Verified => {
            let state = blob_state(app, sha).await?;
            return Ok((StatusCode::OK, upload_state_json(sha, &state, size, size)));
        }
        Settled::Mismatch => {
            return Ok((
                StatusCode::CONFLICT,
                upload_state_json(sha, "open", 0, size),
            ));
        }
        Settled::Open(_) => {}
    }
    let part = part_path(app, sha);
    let part_len = fs::metadata(&part).map(|m| m.len() as i64).unwrap_or(0);
    if start > part_len {
        return Ok((
            StatusCode::CONFLICT,
            upload_state_json(sha, "open", part_len, size),
        ));
    }
    if start < part_len {
        // A chunk at an already-durable range is a no-op only when the bytes are identical.
        let identical = end < part_len && {
            let part = part.clone();
            let want = body.to_vec();
            tokio::task::spawn_blocking(move || -> std::io::Result<bool> {
                let mut f = fs::File::open(part)?;
                f.seek(SeekFrom::Start(start as u64))?;
                let mut have = vec![0u8; want.len()];
                f.read_exact(&mut have)?;
                Ok(have == want)
            })
            .await
            .map_err(|e| Error::internal(e.to_string()))??
        };
        if identical {
            return Ok((
                StatusCode::OK,
                upload_state_json(sha, "open", part_len, size),
            ));
        }
        return Ok((
            StatusCode::CONFLICT,
            upload_state_json(sha, "open", part_len, size),
        ));
    }

    // start == part_len: append, fsync, then record the offset.
    app.faults.check("upload.before_write")?;
    let write_result = {
        let part = part.clone();
        let body = body.to_vec();
        let faults = app.faults.clone();
        let incoming = app.cfg.spool_incoming();
        tokio::task::spawn_blocking(move || -> Result<()> {
            let created = !part.exists();
            let mut file = OpenOptions::new().create(true).append(true).open(&part)?;
            file.write_all(&body)?;
            faults.check("upload.after_write")?;
            file.sync_data()?;
            if created {
                util::fsync_dir(&incoming)?;
            }
            faults.check("upload.after_fsync")?;
            Ok(())
        })
        .await
        .map_err(|e| Error::internal(e.to_string()))?
    };
    if let Err(e) = write_result {
        if !matches!(e, Error::Crash(_))
            && let Ok(f) = OpenOptions::new().write(true).open(&part)
        {
            let _ = f.set_len(part_len as u64);
        }
        return Err(e);
    }
    let new_len = end + 1;
    let offset_result = {
        let sha = sha.to_string();
        app.store
            .write(move |conn| {
                conn.execute(
                    "UPDATE uploads SET durable_offset = ?2, updated_at = ?3 WHERE sha256 = ?1",
                    params![sha, new_len, iso_now()],
                )?;
                Ok(())
            })
            .await
    };
    if let Err(e) = offset_result {
        if let Ok(f) = OpenOptions::new().write(true).open(&part) {
            let _ = f.set_len(part_len as u64);
        }
        return Err(e);
    }
    app.faults.check("upload.after_offset")?;
    if new_len == size {
        let row = UploadRow {
            durable_offset: new_len,
            ..row
        };
        return match commit_part(app, &row).await {
            Ok(true) => {
                let state = blob_state(app, sha).await?;
                Ok((StatusCode::OK, upload_state_json(sha, &state, size, size)))
            }
            Ok(false) => Err(Error::Api(ApiError::new(
                StatusCode::UNPROCESSABLE_ENTITY,
                "hash_mismatch",
                "the received bytes do not hash to the announced sha256; restart from 0",
            ))),
            Err(e) => {
                // A failed commit (for example ENOSPC in the transaction) does not advance the
                // offset; the renamed file is settled by the next create or chunk.
                if !matches!(e, Error::Crash(_)) {
                    let _ = set_offset(app, sha, part_len).await;
                }
                Err(e)
            }
        };
    }
    Ok((
        StatusCode::OK,
        upload_state_json(sha, "open", new_len, size),
    ))
}

/// Step 5: hash the `.part`, set mtime, rename to `ready/`, commit. Returns false on a hash
/// mismatch (the part is deleted and the upload is back at 0).
async fn commit_part(app: &AppState, row: &UploadRow) -> Result<bool> {
    let sha = row.sha256.as_str();
    let part = part_path(app, sha);
    let part2 = part.clone();
    let (hash, bytes) = tokio::task::spawn_blocking(move || util::sha256_file(&part2))
        .await
        .map_err(|e| Error::internal(e.to_string()))??;
    app.faults.check("commit.after_hash")?;
    if hash != sha || bytes as i64 != row.size {
        let _ = fs::remove_file(&part);
        set_offset(app, sha, 0).await?;
        return Ok(false);
    }
    // The worker's 2 s quiet rule reads mtime; clamp absorbs clock skew between the PCs.
    let source = row
        .source_mtime
        .as_deref()
        .and_then(util::parse_iso)
        .unwrap_or_else(util::unix_now);
    let mtime = source.min(util::unix_now() - 10);
    {
        let part = part.clone();
        tokio::task::spawn_blocking(move || -> std::io::Result<()> {
            let f = OpenOptions::new().write(true).open(part)?;
            f.set_modified(util::systime_from_unix(mtime))?;
            f.sync_all()
        })
        .await
        .map_err(|e| Error::internal(e.to_string()))??;
    }
    app.faults.check("commit.after_mtime")?;
    let ready = ready_path(app, sha);
    fs::rename(&part, &ready)?;
    app.faults.check("commit.after_rename")?;
    util::fsync_dir(&app.cfg.spool_ready())?;
    util::fsync_dir(&app.cfg.spool_incoming())?;
    app.faults.check("commit.after_dir_sync")?;
    finish_blob(app, row).await?;
    Ok(true)
}

/// The mtime a committed recording carries: the source's, clamped to `now - 10 s` (the worker's
/// 2 s quiet rule reads it; the clamp absorbs clock skew between the PCs).
fn commit_mtime(source: Option<i64>) -> i64 {
    source
        .unwrap_or_else(util::unix_now)
        .min(util::unix_now() - 10)
}

/// A file in `ready/` with no blob row (a crash between the rename and the transaction, or a
/// transaction that failed). It is adopted only when its bytes hash to its name; then, exactly as
/// a fresh commit, its mtime is set, the file is fsynced and so are both rename directories
/// before the blob is recorded as `verified` (review 1). False when the hash differs: the file
/// is deleted.
async fn adopt_ready(app: &AppState, row: &UploadRow) -> Result<bool> {
    let sha = row.sha256.as_str();
    let ready = ready_path(app, sha);
    let hashed = {
        let ready = ready.clone();
        tokio::task::spawn_blocking(move || util::sha256_file(&ready))
            .await
            .map_err(|e| Error::internal(e.to_string()))??
    };
    if hashed.0 != sha {
        let bad = app.cfg.spool_incoming().join(format!("{sha}.bad"));
        let _ = fs::rename(&ready, &bad);
        let _ = fs::remove_file(&bad);
        return Ok(false);
    }
    let source = row
        .source_mtime
        .as_deref()
        .and_then(util::parse_iso)
        .or_else(|| {
            fs::metadata(&ready)
                .and_then(|m| m.modified())
                .ok()
                .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
                .map(|d| d.as_secs() as i64)
        });
    let mtime = commit_mtime(source);
    {
        let (ready, ready_dir, incoming) = (
            ready.clone(),
            app.cfg.spool_ready(),
            app.cfg.spool_incoming(),
        );
        tokio::task::spawn_blocking(move || -> std::io::Result<()> {
            let f = OpenOptions::new().write(true).open(ready)?;
            f.set_modified(util::systime_from_unix(mtime))?;
            f.sync_all()?;
            util::fsync_dir(&ready_dir)?;
            util::fsync_dir(&incoming)
        })
        .await
        .map_err(|e| Error::internal(e.to_string()))??;
    }
    app.faults.check("recover.after_sync")?;
    finish_blob(app, row).await?;
    Ok(true)
}

/// The commit transaction: blob row, upload `verified`, recording row (ingest queued) or setup
/// file link. Idempotent.
async fn finish_blob(app: &AppState, row: &UploadRow) -> Result<()> {
    app.faults.check("commit.before_tx")?;
    let row = row.clone();
    let spool_ready = ready_path(app, &row.sha256);
    let mtime_iso = fs::metadata(&spool_ready)
        .ok()
        .and_then(|m| m.modified().ok())
        .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
        .map(|d| util::iso_from_unix(d.as_secs() as i64));
    let (customer, kind, sha) = {
        let row = row.clone();
        app.store
            .write(move |conn| {
                let tx = conn.transaction()?;
                let now = iso_now();
                tx.execute(
                    "INSERT OR IGNORE INTO blobs(sha256, size, kind, first_name, source_mtime, collector_id,
                       received_at, spool_present, nas_state)
                     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, 1, 'pending')",
                    params![
                        row.sha256,
                        row.size,
                        row.kind,
                        row.file_name,
                        row.source_mtime.clone().or(mtime_iso),
                        row.collector_id,
                        now
                    ],
                )?;
                tx.execute(
                    "UPDATE uploads SET state = 'verified', durable_offset = ?2, updated_at = ?3 WHERE sha256 = ?1",
                    params![row.sha256, row.size, now],
                )?;
                let customer: Option<i64> = tx
                    .query_row(
                        "SELECT customer_id FROM blob_occurrences WHERE sha256 = ?1 ORDER BY occurrence_id DESC LIMIT 1",
                        [&row.sha256],
                        |r| r.get(0),
                    )
                    .optional()?
                    .flatten();
                if row.kind == "ibt" {
                    tx.execute(
                        "INSERT OR IGNORE INTO recordings(sha256, ingest_state) VALUES (?1, 'verified')",
                        [&row.sha256],
                    )?;
                } else if matches!(row.kind.as_str(), "field" | "rpy") {
                    jobs::enqueue_tx(&tx, &NewJob::archive(&row.sha256), true)?;
                } else {
                    // Every occurrence the bytes were announced under, not only the path of the
                    // upload that completed (review 23): each is a file of its driver's folder.
                    let occurrences: Vec<(Option<i64>, Option<String>, String, Option<String>)> = tx
                        .prepare(
                            "SELECT customer_id, relative_path, file_name, collector_id FROM blob_occurrences
                             WHERE sha256 = ?1 AND kind <> 'ibt' ORDER BY occurrence_id",
                        )?
                        .query_map([&row.sha256], |r| {
                            Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?))
                        })?
                        .collect::<rusqlite::Result<_>>()?;
                    for (owner, relative_path, file_name, collector) in occurrences {
                        let owner = match owner {
                            Some(owner) => Some(owner),
                            None => resolve_customer(&tx, None, collector.as_deref())?,
                        };
                        if let Some(owner) = owner {
                            link_setup_file(
                                &tx,
                                owner,
                                &row.sha256,
                                &row.kind,
                                relative_path.as_deref(),
                                &file_name,
                            )?;
                        }
                    }
                    jobs::enqueue_tx(
                        &tx,
                        &NewJob::archive(&row.sha256),
                        true,
                    )?;
                    if row.kind == "htm" {
                        jobs::enqueue_tx(&tx, &NewJob::setup_files(customer, &row.sha256), true)?;
                    }
                }
                tx.commit()?;
                Ok((customer, row.kind.clone(), row.sha256.clone()))
            })
            .await?
    };
    app.faults.check("commit.after_tx")?;
    app.events.recording_state(&sha, customer, "verified", None);
    app.jobs.wake_all();
    if kind == "ibt" && app.cfg.background {
        spawn_pending(app);
    }
    Ok(())
}

/// Server start, before the listener opens: every file/DB combination has one outcome
/// (`collector-protocol.md` section 4 table).
pub async fn reconcile(app: &AppState) -> Result<()> {
    let ready_dir = app.cfg.spool_ready();
    let incoming = app.cfg.spool_incoming();
    let mut ready_files = Vec::new();
    for entry in fs::read_dir(&ready_dir)? {
        let name = entry?.file_name().to_string_lossy().into_owned();
        if util::is_sha256_hex(&name) {
            ready_files.push(name);
        }
    }
    let mut part_files = Vec::new();
    for entry in fs::read_dir(&incoming)? {
        let name = entry?.file_name().to_string_lossy().into_owned();
        if let Some(sha) = name.strip_suffix(".part")
            && util::is_sha256_hex(sha)
        {
            part_files.push(sha.to_string());
        } else if name.ends_with(".bad") || name.contains(".restage.") {
            let _ = fs::remove_file(incoming.join(&name));
        }
    }
    // ready/<sha>
    for sha in &ready_files {
        let _guard = app.upload_locks.lock(sha).await;
        if crate::nas_library::is_deleted(app, sha).await? {
            crate::nas_library::cleanup_deleted(app, sha).await?;
            continue;
        }
        let sha2 = sha.clone();
        let (has_blob, row) = app
            .store
            .read(move |conn| Ok((blob_exists(conn, &sha2)?, read_upload(conn, &sha2)?)))
            .await?;
        if has_blob {
            continue;
        }
        let ready = ready_path(app, sha);
        let bytes = fs::metadata(&ready)?.len();
        let row = row.unwrap_or_else(|| UploadRow {
            sha256: sha.clone(),
            size: bytes as i64,
            kind: "ibt".into(),
            file_name: sha.clone(),
            relative_path: None,
            source_mtime: None,
            collector_id: None,
            durable_offset: bytes as i64,
            state: "open".into(),
        });
        if row.collector_id.is_none() {
            // No upload row survived: the file name and kind are unknown; it is kept as a recording.
            tracing::warn!(sha256 = %sha, "ready file without an upload row; kept as ibt");
        }
        if !adopt_ready(app, &row).await? {
            set_offset_if_row(app, sha, 0).await?;
        }
    }
    // incoming/<sha>.part
    for sha in &part_files {
        let _guard = app.upload_locks.lock(sha).await;
        if crate::nas_library::is_deleted(app, sha).await? {
            crate::nas_library::cleanup_deleted(app, sha).await?;
            continue;
        }
        let sha2 = sha.clone();
        let row = app
            .store
            .read(move |conn| Ok(read_upload(conn, &sha2)?))
            .await?;
        match row {
            Some(row) if row.state == "open" => {
                settle(app, sha).await?;
            }
            _ => {
                let _ = fs::remove_file(part_path(app, sha));
            }
        }
    }
    // open uploads with neither file: reset to 0; blobs whose spool file is gone
    let missing: Vec<String> = app
        .store
        .read(|conn| {
            let mut stmt = conn.prepare(
                "SELECT sha256 FROM uploads WHERE state = 'open' AND durable_offset > 0",
            )?;
            Ok(stmt
                .query_map([], |r| r.get::<_, String>(0))?
                .collect::<rusqlite::Result<Vec<_>>>()?)
        })
        .await?;
    for sha in missing {
        if !part_path(app, &sha).exists() && !ready_path(app, &sha).exists() {
            set_offset(app, &sha, 0).await?;
        }
    }
    let spool_ready = app.cfg.spool_ready();
    app.store
        .write(move |conn| {
            let present: Vec<String> = conn
                .prepare("SELECT sha256 FROM blobs WHERE spool_present = 1")?
                .query_map([], |r| r.get(0))?
                .collect::<rusqlite::Result<_>>()?;
            for sha in present {
                if !spool_ready.join(&sha).exists() {
                    conn.execute("UPDATE blobs SET spool_present = 0 WHERE sha256 = ?1", [sha])?;
                }
            }
            // blobs not yet on the NAS go back in the archive queue (nas-layout.md section 6)
            let pending: Vec<String> = conn
                .prepare(
                    "SELECT sha256 FROM blobs WHERE nas_state IN ('pending', 'failed') AND spool_present = 1 AND NOT EXISTS(SELECT 1 FROM nas_library_objects n WHERE n.sha256=blobs.sha256 AND n.state='deleted')",
                )?
                .query_map([], |r| r.get(0))?
                .collect::<rusqlite::Result<_>>()?;
            for sha in pending {
                // recordings are archived after parsing; setup files right away
                let kind: String =
                    conn.query_row("SELECT kind FROM blobs WHERE sha256 = ?1", [&sha], |r| r.get(0))?;
                let parsed: bool = conn
                    .query_row(
                        "SELECT COALESCE((SELECT ingest_state <> 'verified' FROM recordings WHERE sha256 = ?1), 1)",
                        [&sha],
                        |r| r.get(0),
                    )?;
                if kind != "ibt" || parsed {
                    jobs::enqueue_tx(conn, &NewJob::archive(&sha), true)?;
                }
            }
            Ok(())
        })
        .await?;
    Ok(())
}

async fn set_offset_if_row(app: &AppState, sha: &str, offset: i64) -> Result<()> {
    let sha = sha.to_string();
    app.store
        .write(move |conn| {
            conn.execute(
                "UPDATE uploads SET durable_offset = ?2 WHERE sha256 = ?1 AND state = 'open'",
                params![sha, offset],
            )?;
            Ok(())
        })
        .await
}

/// Parses every recording still in `verified`, one at a time.
pub fn spawn_pending(app: &AppState) {
    let app = app.clone();
    tokio::spawn(async move {
        if let Err(e) = ingest_pending(&app).await {
            tracing::error!(error = %e, "ingest failed");
        }
    });
}

static INGEST_LOCK: tokio::sync::Mutex<()> = tokio::sync::Mutex::const_new(());

pub async fn ingest_pending(app: &AppState) -> Result<usize> {
    let _guard = INGEST_LOCK.lock().await;
    let mut done = 0;
    loop {
        let next: Option<String> = app
            .store
            .read(|conn| {
                Ok(conn
                    .query_row(
                        "SELECT r.sha256 FROM recordings r JOIN blobs b ON b.sha256 = r.sha256
                         WHERE r.ingest_state = 'verified' ORDER BY b.received_at DESC, r.sha256 LIMIT 1",
                        [],
                        |r| r.get(0),
                    )
                    .optional()?)
            })
            .await?;
        let Some(sha) = next else {
            return Ok(done);
        };
        ingest_recording(app, &sha).await?;
        done += 1;
    }
}

/// Parses one recording: driver, session metadata, `CarSetup` observation; queues discovery
/// and archiving in the same transaction.
pub async fn ingest_recording(app: &AppState, sha: &str) -> Result<()> {
    let path = ready_path(app, sha);
    if !path.exists() {
        // restage from the NAS when the spool copy is gone
        let archived: bool = {
            let sha = sha.to_string();
            app.store
                .read(move |conn| {
                    Ok(conn
                        .query_row(
                            "SELECT nas_state = 'archived' FROM blobs WHERE sha256 = ?1",
                            [sha],
                            |r| r.get(0),
                        )
                        .unwrap_or(false))
                })
                .await?
        };
        let restaged = archived && restage_recording(app, sha).await.is_ok();
        if !restaged {
            return fail_ingest(app, sha, "spool copy is missing and could not be restaged").await;
        }
    }
    let parser = app.parser.clone();
    let parse_path = path.clone();
    let parsed = tokio::task::spawn_blocking(move || {
        std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| parser.parse(&parse_path)))
            .unwrap_or_else(|_| Err("recording parser is not available".to_string()))
    })
    .await
    .map_err(|e| Error::internal(e.to_string()))?;
    let parsed = match parsed {
        Ok(p) => p,
        Err(message) => return fail_ingest(app, sha, &message).await,
    };
    let Some(customer) = parsed.customer_id else {
        return fail_ingest(app, sha, "the recording carries no driver identity").await;
    };
    app.faults.check("ingest.before_tx")?;
    let sha_owned = sha.to_string();
    app.store
        .write(move |conn| {
            let tx = conn.transaction()?;
            let now = iso_now();
            // An offline session's placeholder rating, or one older than a parsed race of the
            // driver, must not replace the latest real rating (`drivers.latest_irating`).
            let mut irating = crate::db::recorded_irating(parsed.irating, parsed.subsession_id);
            if irating.is_some()
                && crate::db::newer_rated_recording(
                    &tx,
                    customer,
                    &sha_owned,
                    parsed.start_time_utc.as_deref(),
                )?
            {
                irating = None;
            }
            crate::db::upsert_driver(&tx, customer, parsed.user_name.as_deref(), irating)?;
            tx.execute(
                "UPDATE recordings SET customer_id = ?2, ingest_state = 'parsed', ingest_error = NULL,
                   record_count = ?3, tick_rate = ?4, session_info_sha256 = ?5, subsession_id = ?6,
                   track = ?7, track_config = ?8, car_path = ?9, car_name = ?10, series_name = ?11,
                   season_name = ?12, start_time_utc = ?13, header_json = ?14, parsed_at = ?15
                 WHERE sha256 = ?1",
                params![
                    sha_owned,
                    customer,
                    parsed.record_count,
                    parsed.tick_rate,
                    parsed.session_info_sha256,
                    parsed.subsession_id,
                    parsed.track,
                    parsed.track_config,
                    parsed.car_path,
                    parsed.car_name,
                    parsed.series_name,
                    parsed.season_name,
                    parsed.start_time_utc,
                    parsed.header_json.to_string(),
                    now
                ],
            )?;
            // main, or lab inside a matching lab window (`library.rs`)
            crate::library::file_recording(&tx, &sha_owned)?;
            if let (Some(setup), Some(car_path)) = (&parsed.setup, &parsed.car_path) {
                let observed = parsed.start_time_utc.clone().unwrap_or_else(|| now.clone());
                let setup_id = crate::setups::upsert_setup(
                    &tx,
                    customer,
                    car_path,
                    &setup.fingerprint,
                    &setup.values,
                    &observed,
                )?;
                tx.execute(
                    "INSERT INTO setup_observations(setup_id, customer_id, source, recording_sha256, setup_name,
                       is_modified, load_type, update_count, observed_at)
                     SELECT ?1, ?2, 'recording', ?3, ?4, ?5, ?6, ?7, ?8
                     WHERE NOT EXISTS (SELECT 1 FROM setup_observations
                        WHERE source = 'recording' AND recording_sha256 = ?3)",
                    params![
                        setup_id,
                        customer,
                        sha_owned,
                        setup.setup_name,
                        setup.is_modified.map(|b| b as i64),
                        setup.load_type,
                        setup.update_count,
                        observed
                    ],
                )?;
            }
            jobs::enqueue_tx(&tx, &NewJob::discover(customer, &sha_owned), false)?;
            jobs::enqueue_tx(&tx, &NewJob::archive(&sha_owned), true)?;
            tx.commit()?;
            Ok(())
        })
        .await?;
    app.events
        .recording_state(sha, Some(customer), "parsed", None);
    app.events.library_changed(customer, "setups");
    app.jobs.wake_all();
    Ok(())
}

async fn fail_ingest(app: &AppState, sha: &str, message: &str) -> Result<()> {
    let (sha_owned, message_owned) = (sha.to_string(), message.to_string());
    app.store
        .write(move |conn| {
            conn.execute(
                "UPDATE recordings SET ingest_state = 'failed', ingest_error = ?2 WHERE sha256 = ?1",
                params![sha_owned, message_owned],
            )?;
            Ok(())
        })
        .await?;
    app.events
        .recording_state(sha, None, "failed", Some(message));
    Ok(())
}

/// Copies an archived recording from the NAS back into `ready/` for analysis. The bytes are
/// reserved through the upload admission first (`nas-layout.md` section 4: they count toward the
/// 20 GB and the 4 GiB free rule, and a refused restage waits up to `restage_wait`), and the
/// file gets the blob's source mtime with the commit clamp, fsynced before it is published.
pub async fn restage_recording(app: &AppState, sha: &str) -> Result<()> {
    crate::nas_library::guard(app, sha).await?;
    let sha_owned = sha.to_string();
    let (size, source_mtime, kind): (i64, Option<String>, String) = app
        .store
        .read(move |conn| {
            Ok(conn.query_row(
                "SELECT size, source_mtime, kind FROM blobs WHERE sha256 = ?1",
                [sha_owned],
                |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
            )?)
        })
        .await?;
    let deadline = tokio::time::Instant::now() + app.cfg.restage_wait;
    let reservation = loop {
        let attempt = {
            let _gate = app.spool.gate.lock().await;
            match check_admission(app, size as u64).await {
                Ok(()) => Ok(app.spool.hold_restage(size as u64)),
                Err(e) => Err(e),
            }
        };
        match attempt {
            Ok(reservation) => break reservation,
            Err(Error::Api(e))
                if e.status == StatusCode::SERVICE_UNAVAILABLE
                    && tokio::time::Instant::now() < deadline =>
            {
                tokio::time::sleep(std::time::Duration::from_millis(250)).await;
            }
            Err(e) => return Err(e),
        }
    };
    let mtime = commit_mtime(source_mtime.as_deref().and_then(util::parse_iso));
    let canonical = crate::nas_library::restage(app, sha, &kind, size as u64, mtime).await?;
    if !canonical {
        app.archiver
            .restage(
                sha,
                match kind.as_str() {
                    "sto" | "htm" => crate::archiver::ArchiveKind::SetupFile,
                    "field" => crate::archiver::ArchiveKind::FieldRecording,
                    _ => crate::archiver::ArchiveKind::Recording,
                },
                match kind.as_str() {
                    "sto" => "sto",
                    "htm" => "htm",
                    "field" => "field",
                    _ => "ibt",
                },
                ready_path(app, sha),
                app.cfg.spool_incoming(),
                Some(mtime),
            )
            .await?;
    }
    // Counted as spool bytes before the reservation is released.
    let sha_owned = sha.to_string();
    app.store
        .write(move |conn| {
            conn.execute(
                "UPDATE blobs SET spool_present = 1 WHERE sha256 = ?1",
                [sha_owned],
            )?;
            Ok(())
        })
        .await?;
    drop(reservation);
    Ok(())
}

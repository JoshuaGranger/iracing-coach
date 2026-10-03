//! Scheduler: user-data snapshots (hourly when changed, daily to the NAS, on demand and on
//! shutdown), archive retries with backoff, idle-upload and request-log cleanup, NAS probe.

use std::fs;
use std::path::{Path, PathBuf};
use std::sync::atomic::Ordering;
use std::time::Duration;

use rusqlite::{OptionalExtension, params};
use serde_json::{Value, json};

use crate::app::AppState;
use crate::error::Result;
use crate::jobs::{JobOutcome, JobRow, NewJob};
use crate::util::{self, day_from_unix, iso_now, sha256_file};

/// Local generations kept (nas-layout.md section 5).
pub const KEEP_GENERATIONS: usize = 48;

struct LightHold(AppState);

impl LightHold {
    async fn acquire(app: &AppState) -> LightHold {
        app.jobs.light_hold.store(true, Ordering::SeqCst);
        let deadline = tokio::time::Instant::now() + Duration::from_secs(200);
        while app.jobs.lane_busy("light") && tokio::time::Instant::now() < deadline {
            tokio::time::sleep(Duration::from_millis(50)).await;
        }
        LightHold(app.clone())
    }
}

impl Drop for LightHold {
    fn drop(&mut self) {
        self.0.jobs.light_hold.store(false, Ordering::SeqCst);
    }
}

fn vacuum_into(conn: &rusqlite::Connection, dest: &Path) -> Result<()> {
    if let Some(parent) = dest.parent() {
        fs::create_dir_all(parent)?;
    }
    let _ = fs::remove_file(dest);
    let text = dest.to_string_lossy().replace('\'', "''");
    conn.execute_batch(&format!("VACUUM INTO '{text}'"))?;
    Ok(())
}

fn copy_tree(src: &Path, dest: &Path) -> std::io::Result<()> {
    fs::create_dir_all(dest)?;
    for entry in fs::read_dir(src)? {
        let entry = entry?;
        let target = dest.join(entry.file_name());
        if entry.path().is_dir() {
            copy_tree(&entry.path(), &target)?;
        } else {
            fs::copy(entry.path(), target)?;
        }
    }
    Ok(())
}

fn list_files(base: &Path, dir: &Path, out: &mut Vec<(String, PathBuf)>) -> std::io::Result<()> {
    for entry in fs::read_dir(dir)? {
        let path = entry?.path();
        if path.is_dir() {
            list_files(base, &path, out)?;
        } else {
            let rel = path
                .strip_prefix(base)
                .expect("under base")
                .to_string_lossy()
                .replace('\\', "/");
            out.push((rel, path));
        }
    }
    Ok(())
}

/// Takes one snapshot generation. Returns `(generation, dir, created)`; `created` is false when
/// the newest complete generation already holds this `user_data_generation`.
pub async fn take_snapshot(app: &AppState) -> Result<(i64, PathBuf, bool)> {
    let _hold = LightHold::acquire(app).await;
    let snapshots = app.cfg.snapshots_dir();
    let data_dir = app.cfg.data_dir.clone();
    let db_path = app.store.path().to_path_buf();
    let version = app.cfg.app_version.clone();
    let (generation, dir, created) = app
        .store
        .write(move |conn| {
            let generation = crate::db::user_data_generation(conn)?;
            let dir = snapshots.join(format!("g{generation}"));
            if dir.join("manifest.json").exists() {
                return Ok((generation, dir, false));
            }
            let _ = fs::remove_dir_all(&dir);
            // user_data_generation was read on this connection, then the backup starts
            vacuum_into(conn, &dir.join("db").join("coach.sqlite3"))?;
            let py = data_dir.join("py");
            if let Ok(drivers) = fs::read_dir(&py) {
                for driver in drivers.flatten() {
                    let archive = driver.path().join("archive");
                    let cid = driver.file_name();
                    let dest_archive = dir.join("py").join(&cid).join("archive");
                    let history = archive.join("history.sqlite3");
                    if history.is_file() {
                        let other = rusqlite::Connection::open(&history)?;
                        vacuum_into(&other, &dest_archive.join("history.sqlite3"))?;
                    }
                    for sub in ["tuning", "trials"] {
                        if archive.join(sub).is_dir() {
                            copy_tree(&archive.join(sub), &dest_archive.join(sub))?;
                        }
                    }
                }
            }
            let mut files = Vec::new();
            list_files(&dir, &dir, &mut files)?;
            let mut entries = Vec::new();
            let mut total = 0u64;
            let mut chain = String::new();
            for (rel, path) in &files {
                let (sha, size) = sha256_file(path)?;
                total += size;
                chain.push_str(&sha);
                entries.push(json!({"path": rel, "sha256": sha, "size": size}));
            }
            let manifest = json!({
                "manifest_version": 1,
                "kind": "backup",
                "sha256": util::sha256_hex(chain.as_bytes()),
                "size": total,
                "archived_at": iso_now(),
                "archiver_version": version,
                "backup": {"schema_version": crate::db::SCHEMA_VERSION, "files": entries},
            });
            fs::write(dir.join("manifest.json"), serde_json::to_vec_pretty(&manifest).unwrap())?;
            #[cfg(unix)]
            {
                let latest = snapshots.join("latest");
                let tmp = snapshots.join("latest.tmp");
                let _ = fs::remove_file(&tmp);
                std::os::unix::fs::symlink(format!("g{generation}"), &tmp)?;
                fs::rename(&tmp, &latest)?;
            }
            conn.execute(
                "INSERT INTO snapshots(taken_at, data_version, local_path, bytes, state) VALUES (?1, ?2, ?3, ?4, 'local')",
                params![iso_now(), generation, dir.to_string_lossy(), total as i64],
            )?;
            let _ = &db_path;
            Ok((generation, dir, true))
        })
        .await?;
    if created {
        prune_generations(app).await?;
    }
    Ok((generation, dir, created))
}

async fn prune_generations(app: &AppState) -> Result<()> {
    let snapshots = app.cfg.snapshots_dir();
    let mut gens: Vec<(i64, PathBuf)> = fs::read_dir(&snapshots)?
        .flatten()
        .filter_map(|e| {
            let name = e.file_name().to_string_lossy().into_owned();
            let n = name.strip_prefix('g')?.parse::<i64>().ok()?;
            e.path().is_dir().then(|| (n, e.path()))
        })
        .collect();
    gens.sort_by_key(|g| std::cmp::Reverse(g.0));
    for (_, path) in gens.into_iter().skip(KEEP_GENERATIONS) {
        let _ = fs::remove_dir_all(&path);
    }
    let keep = KEEP_GENERATIONS as i64;
    app.store
        .write(move |conn| {
            conn.execute(
                "DELETE FROM snapshots WHERE snapshot_id NOT IN
                   (SELECT snapshot_id FROM snapshots ORDER BY data_version DESC, snapshot_id DESC LIMIT ?1)",
                [keep],
            )?;
            Ok(())
        })
        .await
}

/// Copies the newest generation to the NAS day folder and records it.
pub async fn backup_to_nas(
    app: &AppState,
    generation: i64,
    dir: PathBuf,
    day: &str,
) -> Result<bool> {
    let copied = app.archiver.backup(dir, day.to_string()).await?;
    let day = day.to_string();
    app.store
        .write(move |conn| {
            conn.execute(
                "UPDATE snapshots SET nas_day = ?2, state = 'archived' WHERE data_version = ?1",
                params![generation, day],
            )?;
            Ok(())
        })
        .await?;
    Ok(copied)
}

pub async fn snapshot_job(app: &AppState, job: &JobRow) -> Result<JobOutcome> {
    let (generation, dir, created) = take_snapshot(app).await?;
    let mut nas = Value::Null;
    if job.subject["nas"].as_bool().unwrap_or(true) {
        let day = day_from_unix(util::unix_now(), app.cfg.tz_offset_minutes);
        match backup_to_nas(app, generation, dir, &day).await {
            Ok(copied) => nas = json!({"day": day, "copied": copied}),
            Err(e) => nas = json!({"error": e.to_string()}),
        }
    }
    Ok(JobOutcome::Succeeded(
        json!({"generation": generation, "created": created, "nas": nas}),
    ))
}

/// Snapshot on SIGTERM: local only (the NAS may be unreachable at shutdown).
pub async fn snapshot_on_shutdown(app: &AppState) {
    if let Err(e) = take_snapshot(app).await {
        tracing::warn!(error = %e, "shutdown snapshot failed");
    }
}

/// Retries archive jobs for blobs still `pending` or `failed` (backoff 1 min to 1 h).
pub async fn retry_archives(app: &AppState, now: i64) -> Result<usize> {
    let candidates: Vec<(String, i64, Option<String>)> = app
        .store
        .read(|conn| {
            let mut stmt = conn.prepare(
                "SELECT b.sha256, b.nas_attempts,
                        (SELECT j.finished_at FROM jobs j WHERE j.idempotency_key = 'archive:' || b.sha256)
                 FROM blobs b WHERE b.nas_state IN ('pending', 'failed') AND b.spool_present = 1
                   AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.idempotency_key = 'archive:' || b.sha256
                                   AND j.state IN ('queued', 'running'))
                   AND (b.kind <> 'ibt' OR EXISTS (SELECT 1 FROM recordings r WHERE r.sha256 = b.sha256 AND r.ingest_state <> 'verified'))",
            )?;
            Ok(stmt
                .query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))?
                .collect::<rusqlite::Result<Vec<_>>>()?)
        })
        .await?;
    let mut queued = Vec::new();
    for (sha, attempts, finished) in candidates {
        let backoff = (60i64 << attempts.clamp(0, 6)).min(3600);
        let due = finished
            .as_deref()
            .and_then(util::parse_iso)
            .is_none_or(|t| now - t >= backoff);
        if due {
            queued.push(sha);
        }
    }
    let count = queued.len();
    if count > 0 {
        app.store
            .write(move |conn| {
                let tx = conn.transaction()?;
                for sha in &queued {
                    crate::jobs::enqueue_tx(&tx, &NewJob::archive(sha), true)?;
                }
                tx.commit()?;
                Ok(())
            })
            .await?;
        app.jobs.wake_all();
    }
    Ok(count)
}

/// One pass of every periodic duty; `now` is unix seconds. Returns what it did.
pub async fn tick(app: &AppState, now: i64) -> Result<Value> {
    let idle = crate::ingest::cleanup_idle_uploads(app).await?;
    let cutoff = util::iso_from_unix(now - 7 * 86_400);
    app.store
        .write(move |conn| {
            conn.execute("DELETE FROM request_log WHERE created_at < ?1", [cutoff])?;
            Ok(())
        })
        .await?;
    let retried = retry_archives(app, now).await?;
    app.archiver.probe().await;

    let mut snapshot = Value::Null;
    let last: Option<(i64, Option<String>)> = app
        .store
        .read(|conn| {
            Ok(conn
                .query_row(
                    "SELECT data_version, nas_day FROM snapshots ORDER BY data_version DESC LIMIT 1",
                    [],
                    |r| Ok((r.get(0)?, r.get(1)?)),
                )
                .optional()?)
        })
        .await?;
    let generation = app
        .store
        .read(|conn| Ok(crate::db::user_data_generation(conn)?))
        .await?;
    let changed = last.as_ref().is_none_or(|(g, _)| *g != generation);
    let day = day_from_unix(now, app.cfg.tz_offset_minutes);
    let local_minutes = (now + app.cfg.tz_offset_minutes * 60).rem_euclid(86_400) / 60;
    let nas_done_today: bool = app
        .store
        .read({
            let day = day.clone();
            move |conn| {
                Ok(conn.query_row(
                    "SELECT COUNT(*) > 0 FROM snapshots WHERE nas_day = ?1",
                    [day],
                    |r| r.get(0),
                )?)
            }
        })
        .await?;
    let daily_due = local_minutes >= 4 * 60 && !nas_done_today;
    if changed || daily_due {
        let (generation, dir, created) = take_snapshot(app).await?;
        let mut nas = Value::Null;
        if daily_due {
            nas = match backup_to_nas(app, generation, dir, &day).await {
                Ok(copied) => json!({"day": day, "copied": copied}),
                Err(e) => json!({"error": e.to_string()}),
            };
        }
        snapshot = json!({"generation": generation, "created": created, "nas": nas});
    }
    Ok(json!({"idle_uploads_removed": idle, "archives_requeued": retried, "snapshot": snapshot}))
}

pub fn spawn(app: &AppState) {
    let app = app.clone();
    tokio::spawn(async move {
        let mut interval = tokio::time::interval(Duration::from_secs(60));
        interval.tick().await;
        loop {
            interval.tick().await;
            if let Err(e) = tick(&app, util::unix_now()).await {
                tracing::warn!(error = %e, "scheduler tick failed");
            }
        }
    });
}

/// Used by `POST /admin/snapshot`.
pub async fn enqueue_snapshot(app: &AppState) -> Result<Value> {
    let (id, _) = app
        .store
        .write(|conn| {
            let tx = conn.transaction()?;
            let job = NewJob::new("snapshot", "server", None, json!({"nas": true}));
            let result = crate::jobs::enqueue_tx(&tx, &job, false)?;
            let value = crate::jobs::job_ref(&tx, result.0)?;
            tx.commit()?;
            Ok((result.0, value))
        })
        .await?;
    app.jobs.wake_all();
    let job = app
        .store
        .read(move |conn| Ok(crate::jobs::job_ref(conn, id)?))
        .await?;
    Ok(job)
}

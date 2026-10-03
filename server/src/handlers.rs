//! Job handlers: what each job kind does once the runner has admitted it.

use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::sync::atomic::{AtomicU32, Ordering};

use rusqlite::{OptionalExtension, params};
use serde_json::{Value, json};

use crate::app::AppState;
use crate::archiver::{ArchiveKind, ArchiveSpec};
use crate::error::{Error, Result};
use crate::ingest::ready_path;
use crate::jobs::{JobOutcome, JobRow, QueueParams, queue_analysis_tx};
use crate::util::{self, iso_now};
use crate::worker::{Cancel, WorkerCall, WorkerError, cleanup_after_kill, run_worker};

pub async fn run(app: &AppState, job: &JobRow, cancel: &Cancel) -> JobOutcome {
    let result = match job.kind.as_str() {
        "discover" => discover(app, job, cancel).await,
        "analyze" => analyze(app, job, cancel).await,
        "project" => project(app, job).await,
        "archive" => archive(app, job).await,
        "setup_files" => setup_files(app, job, cancel).await,
        "setup_compare" | "baseline" | "tuning_recommend" | "tuning_outcome" | "tuning_history" => {
            light_tool(app, job, cancel).await
        }
        "import" => crate::import::run(app, job, cancel).await,
        "snapshot" => crate::scheduler::snapshot_job(app, job).await,
        "reanalyze" => reanalyze(app, job).await,
        other => Ok(JobOutcome::failed(
            "unsupported",
            format!("no handler for job kind {other}"),
        )),
    };
    match result {
        Ok(outcome) => outcome,
        Err(Error::Crash(point)) => {
            JobOutcome::failed("crash", format!("simulated crash at {point}"))
        }
        Err(e) => {
            let code = if e.is_no_space() {
                "no_space"
            } else {
                "internal"
            };
            JobOutcome::failed(code, e.to_string())
        }
    }
}

pub(crate) fn worker_failure(error: WorkerError) -> JobOutcome {
    match error {
        WorkerError::Cancelled => JobOutcome::Cancelled,
        other => JobOutcome::failed(other.code(), other.message()),
    }
}

/// One tool call in a fresh worker process, with orphan cleanup after a kill.
pub(crate) async fn call_worker(
    app: &AppState,
    job: &JobRow,
    tool: &str,
    arguments: Value,
    iracing_root: &Path,
    timeout: std::time::Duration,
    cancel: &Cancel,
) -> std::result::Result<Value, WorkerError> {
    let customer = job.customer_id.unwrap_or(0);
    let archive = app.cfg.py_archive(customer);
    std::fs::create_dir_all(&archive).map_err(|e| WorkerError::Spawn(e.to_string()))?;
    let pid = Arc::new(AtomicU32::new(0));
    let call = WorkerCall {
        tool: tool.to_string(),
        arguments,
        iracing_root: iracing_root.to_path_buf(),
        archive_root: archive.clone(),
        home: app.cfg.py_root(customer).join("tmp"),
        timeout,
        garage61_token_file: None,
        pid: pid.clone(),
    };
    let result = run_worker(&app.cfg.worker, &call, cancel, &app.cfg.app_version).await;
    if let Err(e) = &result
        && e.killed()
    {
        let idle = app.jobs.driver_idle_except(job.customer_id, job.job_id);
        let pid = pid.load(Ordering::SeqCst);
        cleanup_after_kill(&archive, (pid != 0).then_some(pid), idle);
        if !idle {
            // another worker of the driver is running: its temp files go when the driver is idle
            app.jobs.defer_cleanup(job.customer_id);
        }
    }
    // A result that does not meet its Python contract fails the job (`contracts/README.md`).
    let result = result?;
    if let Some(contract) = crate::schema::worker_contract_for(tool)
        && let Err(message) = app.contracts.check(contract, &result)
    {
        return Err(WorkerError::ToolError {
            error: "contract_mismatch".into(),
            message,
        });
    }
    Ok(result)
}

fn link_or_copy(src: &Path, dest: &Path) -> std::io::Result<()> {
    if let Some(parent) = dest.parent() {
        std::fs::create_dir_all(parent)?;
    }
    if std::fs::hard_link(src, dest).is_err() {
        std::fs::copy(src, dest)?;
    }
    Ok(())
}

/// Builds the job root (`worker-interface.md` section 3): hard links to the job's recordings and,
/// when asked, to the driver's received setup files. Restages recordings whose spool copy is gone.
pub async fn prepare_root(
    app: &AppState,
    job: &JobRow,
    customer: i64,
    recordings: &[(String, String)],
    with_setups: bool,
) -> Result<(PathBuf, Vec<String>)> {
    let root = app.cfg.job_root(customer, job.job_id);
    let _ = std::fs::remove_dir_all(&root);
    std::fs::create_dir_all(root.join("telemetry"))?;
    let mut names = Vec::new();
    let mut used = std::collections::HashSet::new();
    for (sha, name) in recordings {
        let spool = ready_path(app, sha);
        if !spool.exists() {
            crate::ingest::restage_recording(app, sha).await?;
        }
        // the name came from a collector: keep only its last path component
        let base = name.rsplit(['/', '\\']).next().unwrap_or("");
        let base = if base.is_empty() || base == ".." || base == "." {
            sha[..8].to_string()
        } else {
            base.to_string()
        };
        let mut file_name = if base.to_lowercase().ends_with(".ibt") {
            base
        } else {
            format!("{base}.ibt")
        };
        if !used.insert(file_name.to_lowercase()) {
            file_name = format!("{}-{}.ibt", file_name.trim_end_matches(".ibt"), &sha[..8]);
            used.insert(file_name.to_lowercase());
        }
        link_or_copy(&spool, &root.join("telemetry").join(&file_name))?;
        names.push(file_name);
    }
    if with_setups {
        let files: Vec<(String, String, String)> = app
            .store
            .read(move |conn| {
                let mut stmt = conn.prepare(
                    "SELECT f.blob_sha256, f.car_folder, f.relative_path FROM setup_files f
                     JOIN blobs b ON b.sha256 = f.blob_sha256
                     WHERE f.customer_id = ?1 AND f.present = 1 AND b.spool_present = 1",
                )?;
                Ok(stmt
                    .query_map([customer], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))?
                    .collect::<rusqlite::Result<Vec<_>>>()?)
            })
            .await?;
        for (sha, car, rel) in files {
            // `rel` comes from a collector: refuse anything that would leave the root
            if rel.split('/').any(|s| s == ".." || s.is_empty()) || car == ".." || car.contains('/')
            {
                continue;
            }
            let mut dest = root.join("setups");
            if !car.is_empty() {
                dest = dest.join(&car);
            }
            link_or_copy(&ready_path(app, &sha), &dest.join(&rel))?;
        }
    }
    Ok((root, names))
}

pub(crate) fn cleanup_root(root: &Path) {
    let _ = std::fs::remove_dir_all(root);
}

// ---------------------------------------------------------------- discover

async fn discover(app: &AppState, job: &JobRow, cancel: &Cancel) -> Result<JobOutcome> {
    let sha = job.subject["sha256"]
        .as_str()
        .unwrap_or_default()
        .to_string();
    let Some(customer) = job.customer_id else {
        return Ok(JobOutcome::failed(
            "invalid_job",
            "discover job has no driver",
        ));
    };
    let name: String = {
        let sha = sha.clone();
        app.store
            .read(move |conn| {
                Ok(conn
                    .query_row(
                        "SELECT first_name FROM blobs WHERE sha256 = ?1",
                        [sha],
                        |r| r.get(0),
                    )
                    .optional()?
                    .unwrap_or_default())
            })
            .await?
    };
    let (root, names) = prepare_root(app, job, customer, &[(sha.clone(), name)], false).await?;
    let file_name = names.first().cloned().unwrap_or_default();
    let result = call_worker(
        app,
        job,
        "discover_iracing_sessions",
        json!({"root": root.to_string_lossy(), "races_only": false, "limit": 50}),
        &root,
        app.cfg.worker.discover_timeout,
        cancel,
    )
    .await;
    cleanup_root(&root);
    let result = match result {
        Ok(v) => v,
        Err(e) => return Ok(worker_failure(e)),
    };
    apply_discovery(app, customer, &sha, &file_name, &result).await
}

fn file_matches(path: &str, file_name: &str) -> bool {
    path.rsplit(['/', '\\'])
        .next()
        .is_some_and(|n| n == file_name)
}

async fn apply_discovery(
    app: &AppState,
    customer: i64,
    sha: &str,
    file_name: &str,
    result: &Value,
) -> Result<JobOutcome> {
    let group = result["sessions"].as_array().and_then(|groups| {
        groups.iter().find(|g| {
            g["files"].as_array().is_some_and(|files| {
                files
                    .iter()
                    .any(|f| f.as_str().is_some_and(|f| file_matches(f, file_name)))
            })
        })
    });
    let Some(group) = group.cloned() else {
        let reason = result["errors"][0]["error"]
            .as_str()
            .unwrap_or("discovery found no session in the recording")
            .to_string();
        let (sha_owned, reason2) = (sha.to_string(), reason.clone());
        app.store
            .write(move |conn| {
                conn.execute(
                    "UPDATE recordings SET ingest_state = 'failed', ingest_error = ?2 WHERE sha256 = ?1",
                    params![sha_owned, reason2],
                )?;
                Ok(())
            })
            .await?;
        app.events
            .recording_state(sha, Some(customer), "failed", Some(&reason));
        return Ok(JobOutcome::failed("no_session_found", reason));
    };
    let group_id = group["group_id"].as_str().unwrap_or_default().to_string();
    // A group keyed by recording time from the file mtime embeds the recording path (worker-interface.md section 3).
    let path_dependent = group["time_source"] == "mtime";
    let group_key = if path_dependent {
        format!("local-file:{sha}")
    } else {
        group_id
    };
    let text = |k: &str| group[k].as_str().map(str::to_string);
    let session_type = text("sim_session_type").or_else(|| text("event_type"));
    let track = text("track_display_name").or_else(|| text("track_name"));
    let car_path = text("car_path");
    let start = group["start_time_utc"]
        .as_str()
        .and_then(util::parse_iso)
        .map(util::iso_from_unix);
    let subsession = group["subsession_id"].as_i64().filter(|v| *v != 0);
    let sim_num = group["sim_session_num"].as_i64();
    let queue = QueueParams::of(app);
    let sha_owned = sha.to_string();
    let session_id = app
        .store
        .write(move |conn| {
            let tx = conn.transaction()?;
            tx.execute(
                "INSERT INTO sessions(customer_id, subsession_id, sim_session_num, group_key, session_type, track,
                   car_path, start_time_utc, discovered_at)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)
                 ON CONFLICT(customer_id, group_key) DO UPDATE SET
                   session_type = COALESCE(excluded.session_type, session_type),
                   track = COALESCE(excluded.track, track),
                   car_path = COALESCE(excluded.car_path, car_path),
                   start_time_utc = CASE WHEN start_time_utc IS NULL OR excluded.start_time_utc < start_time_utc
                                         THEN excluded.start_time_utc ELSE start_time_utc END",
                params![customer, subsession, sim_num, group_key, session_type, track, car_path, start, iso_now()],
            )?;
            let session_id: i64 = tx.query_row(
                "SELECT session_id FROM sessions WHERE customer_id = ?1 AND group_key = ?2",
                params![customer, group_key],
                |r| r.get(0),
            )?;
            tx.execute(
                "INSERT OR IGNORE INTO session_recordings(session_id, recording_sha256) VALUES (?1, ?2)",
                params![session_id, sha_owned],
            )?;
            tx.execute(
                "UPDATE recordings SET ingest_state = 'discovered' WHERE sha256 = ?1",
                [&sha_owned],
            )?;
            queue_analysis_tx(&tx, &queue, session_id, false, false)?;
            tx.commit()?;
            Ok(session_id)
        })
        .await?;
    app.events
        .recording_state(sha, Some(customer), "discovered", None);
    app.events.library_changed(customer, "sessions");
    Ok(JobOutcome::Succeeded(json!({"session_id": session_id})))
}

// ----------------------------------------------------------------- analyze

async fn analyze(app: &AppState, job: &JobRow, cancel: &Cancel) -> Result<JobOutcome> {
    let Some(customer) = job.customer_id else {
        return Ok(JobOutcome::failed(
            "invalid_job",
            "analyze job has no driver",
        ));
    };
    let session_id = job.subject["session_id"].as_i64().unwrap_or_default();
    let membership = job.subject["membership_sha256"]
        .as_str()
        .unwrap_or_default()
        .to_string();
    let recordings: Vec<(String, String)> = job.subject["recordings"]
        .as_array()
        .map(|a| {
            a.iter()
                .map(|r| {
                    (
                        r["sha256"].as_str().unwrap_or_default().to_string(),
                        r["name"].as_str().unwrap_or_default().to_string(),
                    )
                })
                .collect()
        })
        .unwrap_or_default();
    let (root, names) = prepare_root(app, job, customer, &recordings, true).await?;
    let selector = match job.subject["local_file"].as_str() {
        Some(local_sha) => {
            let idx = recordings
                .iter()
                .position(|r| r.0 == local_sha)
                .unwrap_or(0);
            root.join("telemetry")
                .join(names.get(idx).cloned().unwrap_or_default())
                .to_string_lossy()
                .into_owned()
        }
        None => job.subject["group_key"]
            .as_str()
            .unwrap_or_default()
            .to_string(),
    };
    let archive = app.cfg.py_archive(customer);
    let result = call_worker(
        app,
        job,
        "analyze_iracing_race",
        json!({
            "selector": selector,
            "iracing_root": root.to_string_lossy(),
            "archive_root": archive.to_string_lossy(),
            "target_hz": null,
        }),
        &root,
        app.cfg.worker.analyze_timeout,
        cancel,
    )
    .await;
    cleanup_root(&root);
    let result = match result {
        Ok(v) => v,
        Err(e) => return Ok(worker_failure(e)),
    };
    let Some(analysis_id) = result["analysis_id"].as_str().map(str::to_string) else {
        return Ok(JobOutcome::failed(
            "tool_error",
            "the analysis has no analysis_id",
        ));
    };
    let Some(analysis_path) = result["analysis_path"].as_str().map(str::to_string) else {
        return Ok(JobOutcome::failed(
            "tool_error",
            "the analysis has no analysis_path",
        ));
    };
    let path = PathBuf::from(&analysis_path);
    if !path.starts_with(&archive) {
        return Ok(JobOutcome::failed(
            "tool_error",
            "the analysis path is outside the driver's archive",
        ));
    }
    let bytes = std::fs::metadata(&path)
        .map(|m| m.len() as i64)
        .unwrap_or(0);
    let view = &result["analysis_view"];
    let schema_version = view["schema_version"].as_i64().unwrap_or(0);
    let profile = view["analysis_profile_version"]
        .as_str()
        .unwrap_or("")
        .to_string();
    let lap_count = view["laps"].as_array().map(|l| l.len() as i64);
    let recordings_json =
        json!(recordings.iter().map(|r| r.0.clone()).collect::<Vec<_>>()).to_string();
    let projection = app.projector.version();
    let id = analysis_id.clone();
    let current = app
        .store
        .write(move |conn| {
            let tx = conn.transaction()?;
            tx.execute(
                "INSERT OR IGNORE INTO analyses(analysis_id, session_id, recordings_sha256, membership_sha256,
                   schema_version, profile_version, analysis_path, analysis_bytes, lap_count, created_at,
                   projection_state)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, 'pending')",
                params![id, session_id, recordings_json, membership, schema_version, profile, analysis_path, bytes, lap_count, iso_now()],
            )?;
            let (now_digest, _) = crate::jobs::membership_digest(&tx, session_id)?;
            let is_current = now_digest == membership;
            if is_current {
                let old: Option<String> = tx
                    .query_row(
                        "SELECT current_analysis_id FROM sessions WHERE session_id = ?1",
                        [session_id],
                        |r| r.get(0),
                    )
                    .optional()?
                    .flatten();
                tx.execute(
                    "UPDATE sessions SET current_analysis_id = ?2 WHERE session_id = ?1",
                    params![session_id, id],
                )?;
                if let Some(old) = old
                    && old != id
                {
                    tx.execute(
                        "UPDATE analyses SET superseded_by = ?2 WHERE analysis_id = ?1",
                        params![old, id],
                    )?;
                }
                crate::jobs::requeue_project_tx(&tx, &id, projection, Some(customer))?;
            }
            tx.commit()?;
            Ok(is_current)
        })
        .await?;
    Ok(JobOutcome::Succeeded(
        json!({"analysis_id": analysis_id, "current": current}),
    ))
}

// ----------------------------------------------------------------- project

async fn project(app: &AppState, job: &JobRow) -> Result<JobOutcome> {
    let analysis_id = job.subject["analysis_id"]
        .as_str()
        .unwrap_or_default()
        .to_string();
    let version = app.projector.version();
    let id = analysis_id.clone();
    let path: Option<String> = app
        .store
        .read(move |conn| {
            Ok(conn
                .query_row(
                    "SELECT analysis_path FROM analyses WHERE analysis_id = ?1",
                    [id],
                    |r| r.get(0),
                )
                .optional()?)
        })
        .await?;
    let Some(path) = path else {
        return Ok(JobOutcome::failed("not_found", "analysis not found"));
    };
    let out_dir = app.cfg.views_dir(&analysis_id, version);
    if out_dir.join("race.json").exists() {
        mark_projected(app, &analysis_id, version, None).await?;
        return Ok(JobOutcome::Succeeded(
            json!({"analysis_id": analysis_id, "reused": true}),
        ));
    }
    if let Some(parent) = out_dir.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let projector = app.projector.clone();
    let (analysis_path, out) = (PathBuf::from(&path), out_dir.clone());
    let projected = tokio::task::spawn_blocking(move || {
        std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            projector.project(&analysis_path, &out)
        }))
        .unwrap_or_else(|_| Err("view projector is not available".to_string()))
    })
    .await
    .map_err(|e| Error::internal(e.to_string()))?;
    match projected {
        Ok(info) => {
            mark_projected(app, &analysis_id, version, Some(info)).await?;
            Ok(JobOutcome::Succeeded(json!({"analysis_id": analysis_id})))
        }
        Err(message) => {
            let (id, message2) = (analysis_id.clone(), message.clone());
            app.store
                .write(move |conn| {
                    conn.execute(
                        "UPDATE analyses SET projection_state = 'failed', projection_error = ?2 WHERE analysis_id = ?1",
                        params![id, message2],
                    )?;
                    Ok(())
                })
                .await?;
            Ok(JobOutcome::failed("projection_failed", message))
        }
    }
}

const OVERVIEW_KEYS: [(&str, &str); 12] = [
    ("starting_position", "starting_position"),
    ("final_recorded_position", "final_position"),
    ("recorded_laps", "recorded_laps"),
    ("green_laps_estimated", "green_laps_estimated"),
    ("caution_laps_estimated", "caution_laps_estimated"),
    ("pit_stops_detected", "pit_stops_detected"),
    ("runs_detected", "runs_detected"),
    ("longest_green_run", "longest_green_run"),
    ("pace_slope_s_per_lap", "pace_slope_s_per_lap"),
    ("pace_consistency_percent", "pace_consistency_percent"),
    ("best_clean_lap_s", "best_clean_lap_s"),
    ("fuel_used_gal", "fuel_used_gal"),
];

async fn mark_projected(
    app: &AppState,
    analysis_id: &str,
    version: u32,
    info: Option<crate::adapters::ProjectionInfo>,
) -> Result<()> {
    let view_dir = app.cfg.views_dir(analysis_id, version);
    let overview = std::fs::read(view_dir.join("race.json"))
        .ok()
        .and_then(|b| serde_json::from_slice::<Value>(&b).ok())
        .map(|race| {
            let mut out = serde_json::Map::new();
            for (from, to) in OVERVIEW_KEYS {
                if let Some(v) = race["race_summary"].get(from)
                    && !v.is_null()
                {
                    out.insert(to.to_string(), v.clone());
                }
            }
            Value::Object(out)
        });
    let id = analysis_id.to_string();
    let sessions: Vec<(i64, i64)> = app
        .store
        .write(move |conn| {
            let tx = conn.transaction()?;
            match &info {
                Some(i) => tx.execute(
                    "UPDATE analyses SET projection_version = ?2, projection_state = 'ready', projection_error = NULL,
                       schema_version = ?3, profile_version = ?4, lap_count = ?5 WHERE analysis_id = ?1",
                    params![id, version, i.schema_version, i.profile_version, i.lap_count],
                )?,
                None => tx.execute(
                    "UPDATE analyses SET projection_version = ?2, projection_state = 'ready', projection_error = NULL
                     WHERE analysis_id = ?1",
                    params![id, version],
                )?,
            };
            let sessions: Vec<(i64, i64)> = tx
                .prepare("SELECT session_id, customer_id FROM sessions WHERE current_analysis_id = ?1")?
                .query_map([&id], |r| Ok((r.get(0)?, r.get(1)?)))?
                .collect::<rusqlite::Result<_>>()?;
            for (session_id, _) in &sessions {
                if let Some(overview) = &overview {
                    tx.execute(
                        "UPDATE sessions SET overview_json = ?2 WHERE session_id = ?1",
                        params![session_id, overview.to_string()],
                    )?;
                }
                crate::jobs::refresh_session_state(&tx, *session_id)?;
            }
            tx.commit()?;
            Ok(sessions)
        })
        .await?;
    for (session_id, customer) in sessions {
        app.events
            .analysis_ready(customer, session_id, analysis_id, version);
        app.events.library_changed(customer, "sessions");
    }
    Ok(())
}

// ----------------------------------------------------------------- archive

async fn archive(app: &AppState, job: &JobRow) -> Result<JobOutcome> {
    let sha = job.subject["sha256"]
        .as_str()
        .unwrap_or_default()
        .to_string();
    let sha2 = sha.clone();
    #[allow(clippy::type_complexity)]
    let row: Option<(i64, String, String, Option<String>, Option<String>, String, String)> = app
        .store
        .read(move |conn| {
            Ok(conn
                .query_row(
                    "SELECT size, kind, first_name, source_mtime, collector_id, received_at, nas_state
                     FROM blobs WHERE sha256 = ?1",
                    [sha2],
                    |r| {
                        Ok((
                            r.get(0)?,
                            r.get(1)?,
                            r.get(2)?,
                            r.get(3)?,
                            r.get(4)?,
                            r.get(5)?,
                            r.get(6)?,
                        ))
                    },
                )
                .optional()?)
        })
        .await?;
    let Some((size, kind, name, source_mtime, collector, received, nas_state)) = row else {
        return Ok(JobOutcome::failed("not_found", "blob not found"));
    };
    if nas_state == "archived" {
        release_spool(app, &sha).await?;
        return Ok(JobOutcome::Succeeded(
            json!({"sha256": sha, "already": true}),
        ));
    }
    let spool = ready_path(app, &sha);
    if !spool.exists() {
        return Ok(JobOutcome::failed(
            "spool_missing",
            "the spool copy is gone and the blob is not archived",
        ));
    }
    // manifest fields from the recording row or the setup file row
    let sha3 = sha.clone();
    let kind2 = kind.clone();
    let manifest: Value = app
        .store
        .read(move |conn| {
            let mut manifest = json!({});
            if kind2 == "ibt" {
                let rec: Option<(Option<i64>, Option<i64>, Option<i64>, Option<String>, Option<String>, Option<String>, Option<String>, Option<String>, String)> = conn
                    .query_row(
                        "SELECT customer_id, record_count, tick_rate, session_info_sha256, track, track_config,
                                car_path, start_time_utc, header_json FROM recordings WHERE sha256 = ?1",
                        [&sha3],
                        |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?, r.get(5)?, r.get(6)?, r.get(7)?, r.get(8)?)),
                    )
                    .optional()?;
                if let Some((customer, records, tick, info_sha, track, config, car, start, header)) = rec {
                    let header: Value = serde_json::from_str(&header).unwrap_or(json!({}));
                    if let Some(customer) = customer {
                        let name: Option<String> = conn
                            .query_row("SELECT display_name FROM drivers WHERE customer_id = ?1", [customer], |r| r.get(0))
                            .optional()?;
                        manifest["driver"] = json!({"customer_id": customer, "name": name});
                    }
                    if let Some(info_sha) = info_sha {
                        let types: Vec<String> = conn
                            .prepare("SELECT DISTINCT s.session_type FROM session_recordings sr JOIN sessions s ON s.session_id = sr.session_id WHERE sr.recording_sha256 = ?1 AND s.session_type IS NOT NULL")?
                            .query_map([&sha3], |r| r.get(0))?
                            .collect::<rusqlite::Result<_>>()?;
                        manifest["recording"] = json!({
                            "record_count": records.unwrap_or(0),
                            "tick_rate": tick.unwrap_or(0),
                            "buffer_length": header["buffer_length"].as_i64().unwrap_or(0),
                            "variable_count": header["variable_count"],
                            "session_info_sha256": info_sha,
                            "session_types": types,
                            "track": track,
                            "track_config": config,
                            "car_path": car,
                            "start_time_utc": start,
                        });
                    }
                }
            } else {
                let file: Option<(i64, String, String, String)> = conn
                    .query_row(
                        "SELECT customer_id, car_folder, relative_path, format FROM setup_files
                         WHERE blob_sha256 = ?1 ORDER BY last_seen_at DESC LIMIT 1",
                        [&sha3],
                        |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?)),
                    )
                    .optional()?;
                if let Some((customer, car_folder, relative_path, format)) = file {
                    let name: Option<String> = conn
                        .query_row("SELECT display_name FROM drivers WHERE customer_id = ?1", [customer], |r| r.get(0))
                        .optional()?;
                    manifest["driver"] = json!({"customer_id": customer, "name": name});
                    manifest["setup_file"] =
                        json!({"format": format, "car_folder": car_folder, "relative_path": relative_path});
                }
            }
            Ok(manifest)
        })
        .await?;
    let mut manifest = manifest;
    manifest["original_name"] = json!(name);
    manifest["source_mtime"] = json!(source_mtime);
    manifest["received_at"] = json!(received);
    manifest["collector_id"] = json!(collector);
    let spec = ArchiveSpec {
        sha256: sha.clone(),
        size: size as u64,
        kind: if kind == "ibt" {
            ArchiveKind::Recording
        } else {
            ArchiveKind::SetupFile
        },
        extension: kind.clone(),
        spool_path: spool,
        manifest,
    };
    match app.archiver.archive(spec).await {
        Ok(()) => {}
        Err(Error::Crash(p)) => return Err(Error::Crash(p)),
        Err(e) => {
            let (sha_owned, message) = (sha.clone(), e.to_string());
            app.store
                .write(move |conn| {
                    conn.execute(
                        "UPDATE blobs SET nas_attempts = nas_attempts + 1, nas_error = ?2, nas_state = 'failed'
                         WHERE sha256 = ?1",
                        params![sha_owned, message],
                    )?;
                    Ok(())
                })
                .await?;
            return Ok(JobOutcome::failed("nas_unreachable", e.to_string()));
        }
    }
    app.faults.check("archive.before_db")?;
    let sha_owned = sha.clone();
    app.store
        .write(move |conn| {
            conn.execute(
                "UPDATE blobs SET nas_state = 'archived', nas_archived_at = ?2, nas_error = NULL WHERE sha256 = ?1",
                params![sha_owned, iso_now()],
            )?;
            Ok(())
        })
        .await?;
    release_spool(app, &sha).await?;
    let state = {
        let sha = sha.clone();
        app.store
            .read(move |conn| Ok(crate::ingest::derive_blob_state(conn, &sha)?))
            .await?
    };
    app.events
        .recording_state(&sha, job.customer_id, &state, None);
    Ok(JobOutcome::Succeeded(json!({"sha256": sha})))
}

/// Deletes the spool copy of an archived recording once no queued or running job needs it.
/// Setup files stay (a few KB; job roots link them).
pub async fn release_spool(app: &AppState, sha: &str) -> Result<bool> {
    let sha_owned = sha.to_string();
    let releasable: bool = app
        .store
        .read(move |conn| {
            let row: Option<(String, String, i64)> = conn
                .query_row(
                    "SELECT kind, nas_state, spool_present FROM blobs WHERE sha256 = ?1",
                    [&sha_owned],
                    |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
                )
                .optional()?;
            let Some((kind, nas, present)) = row else {
                return Ok(false);
            };
            if kind != "ibt" || nas != "archived" || present == 0 {
                return Ok(false);
            }
            let referenced: i64 = conn.query_row(
                "SELECT COUNT(*) FROM jobs WHERE state IN ('queued', 'running', 'interrupted')
                 AND kind <> 'archive' AND instr(subject_json, ?1) > 0",
                [&sha_owned],
                |r| r.get(0),
            )?;
            let unparsed: i64 = conn.query_row(
                "SELECT COUNT(*) FROM recordings WHERE sha256 = ?1 AND ingest_state = 'verified'",
                [&sha_owned],
                |r| r.get(0),
            )?;
            Ok(referenced == 0 && unparsed == 0)
        })
        .await?;
    if !releasable {
        return Ok(false);
    }
    let _ = std::fs::remove_file(ready_path(app, sha));
    let sha_owned = sha.to_string();
    app.store
        .write(move |conn| {
            conn.execute(
                "UPDATE blobs SET spool_present = 0 WHERE sha256 = ?1",
                [sha_owned],
            )?;
            Ok(())
        })
        .await?;
    Ok(true)
}

// ------------------------------------------------------ .htm setup exports

async fn setup_files(app: &AppState, job: &JobRow, cancel: &Cancel) -> Result<JobOutcome> {
    let Some(customer) = job.customer_id else {
        return Ok(JobOutcome::Succeeded(
            json!({"entries": 0, "observations": 0, "note": "no driver"}),
        ));
    };
    let (root, _) = prepare_root(app, job, customer, &[], true).await?;
    let archive = app.cfg.py_archive(customer);
    let result = call_worker(
        app,
        job,
        "catalog_iracing_setups",
        json!({"root": root.to_string_lossy(), "archive_root": archive.to_string_lossy(), "maximum_entries": 1000}),
        &root,
        app.cfg.worker.discover_timeout,
        cancel,
    )
    .await;
    cleanup_root(&root);
    let result = match result {
        Ok(v) => v,
        Err(e) => return Ok(worker_failure(e)),
    };
    let entries = result["entries"].as_array().cloned().unwrap_or_default();
    let parser = app.parser.clone();
    let count = entries.len();
    let observations = app
        .store
        .write(move |conn| {
            let tx = conn.transaction()?;
            let mut made = 0;
            for entry in &entries {
                let parsed = &entry["parsed_html"];
                let Some(fields) = parsed["fields"].as_object() else {
                    continue;
                };
                let Some(name) = entry["sources"]["html"][0]["name"].as_str() else {
                    continue;
                };
                let car_folder = entry["car_folder"].as_str().unwrap_or_default();
                let mut values = std::collections::BTreeMap::new();
                for (key, field) in fields {
                    let text = match &field["raw"] {
                        Value::String(s) => s.clone(),
                        Value::Null => continue,
                        other => other.to_string(),
                    };
                    values.insert(key.clone(), text);
                }
                if values.is_empty() {
                    continue;
                }
                let blob: Option<String> = tx
                    .query_row(
                        "SELECT blob_sha256 FROM setup_files WHERE customer_id = ?1 AND format = 'htm'
                           AND (relative_path = ?2 OR relative_path LIKE '%/' || ?2) AND lower(car_folder) = lower(?3)
                         ORDER BY last_seen_at DESC LIMIT 1",
                        params![customer, name, car_folder],
                        |r| r.get(0),
                    )
                    .optional()?;
                let Some(blob) = blob else { continue };
                let exists: bool = tx.query_row(
                    "SELECT COUNT(*) > 0 FROM setup_observations WHERE source = 'htm_export' AND blob_sha256 = ?1",
                    [&blob],
                    |r| r.get(0),
                )?;
                if exists {
                    continue;
                }
                let now = iso_now();
                let setup_id = crate::setups::upsert_setup(
                    &tx,
                    customer,
                    car_folder,
                    &parser.fingerprint(&values),
                    &values,
                    &now,
                )?;
                tx.execute(
                    "INSERT INTO setup_observations(setup_id, customer_id, source, blob_sha256, observed_at)
                     VALUES (?1, ?2, 'htm_export', ?3, ?4)",
                    params![setup_id, customer, blob, now],
                )?;
                made += 1;
            }
            tx.commit()?;
            Ok(made)
        })
        .await?;
    app.events.library_changed(customer, "setups");
    Ok(JobOutcome::Succeeded(
        json!({"entries": count, "observations": observations}),
    ))
}

// ----------------------------------------------------- light worker tools

fn tool_for(kind: &str) -> &'static str {
    match kind {
        "setup_compare" => "compare_iracing_setups",
        "baseline" => "build_open_setup_package",
        "tuning_recommend" => "recommend_structured_open_setup_tuning",
        "tuning_outcome" => "record_open_setup_feedback",
        _ => "iracing_setup_history",
    }
}

/// Maps the worker's comparison onto the `SetupCompareResult` wire shape.
pub fn compare_result(backend: &Value) -> Value {
    let rows: Vec<Value> = backend["rows"]
        .as_array()
        .map(|rows| {
            rows.iter()
                .map(|r| {
                    json!({
                        "key": r["key"],
                        "label": r["field"],
                        "left": scalar_text(&r["left"]),
                        "right": scalar_text(&r["right"]),
                        "status": r["status"],
                        "readout": r["readout"].as_bool().unwrap_or(false),
                    })
                })
                .collect()
        })
        .unwrap_or_default();
    json!({
        "rows": rows,
        "identical_count": backend["counts"]["identical"].as_i64().unwrap_or(0),
        "backend": backend,
    })
}

fn scalar_text(v: &Value) -> Value {
    match v {
        Value::Null => Value::Null,
        Value::String(_) => v.clone(),
        other => Value::String(other.to_string()),
    }
}

async fn light_tool(app: &AppState, job: &JobRow, cancel: &Cancel) -> Result<JobOutcome> {
    let customer = job.customer_id.unwrap_or(0);
    let tool = tool_for(&job.kind);
    let mut arguments = job.subject["arguments"].clone();
    if !arguments.is_object() {
        return Ok(JobOutcome::failed(
            "invalid_job",
            "the job has no arguments",
        ));
    }
    let needs_setups = job.subject["needs_setups"].as_bool().unwrap_or(false);
    let (root, _) = prepare_root(app, job, customer, &[], needs_setups).await?;
    if tool != "compare_iracing_setups" {
        arguments["archive_root"] = json!(app.cfg.py_archive(customer).to_string_lossy());
    }
    if tool == "build_open_setup_package" {
        arguments["iracing_root"] = json!(root.to_string_lossy());
    }
    let result = call_worker(
        app,
        job,
        tool,
        arguments,
        &root,
        app.cfg.worker.light_timeout,
        cancel,
    )
    .await;
    cleanup_root(&root);
    let result = match result {
        Ok(v) => v,
        Err(e) => return Ok(worker_failure(e)),
    };
    if job.subject["bumps_generation"].as_bool().unwrap_or(false) {
        app.store
            .write(|conn| {
                let tx = conn.transaction()?;
                crate::db::bump_generation(&tx)?;
                tx.commit()?;
                Ok(())
            })
            .await?;
    }
    let result = if job.kind == "setup_compare" {
        compare_result(&result)
    } else {
        result
    };
    Ok(JobOutcome::Succeeded(result))
}

// --------------------------------------------------------------- reanalyze

async fn reanalyze(app: &AppState, job: &JobRow) -> Result<JobOutcome> {
    let customer = job.subject["customer_id"].as_i64();
    let queue = QueueParams::of(app);
    let queued = app
        .store
        .write(move |conn| {
            let tx = conn.transaction()?;
            let ids: Vec<i64> = tx
                .prepare(
                    "SELECT s.session_id FROM sessions s
                     LEFT JOIN analyses a ON a.analysis_id = s.current_analysis_id
                     WHERE (?1 IS NULL OR s.customer_id = ?1)
                       AND (a.analysis_id IS NULL OR a.profile_version <> ?2 OR s.analysis_state IN ('deferred', 'failed', 'none'))
                       AND EXISTS (SELECT 1 FROM session_recordings sr WHERE sr.session_id = s.session_id)",
                )?
                .query_map(params![customer, queue.profile], |r| r.get(0))?
                .collect::<rusqlite::Result<_>>()?;
            let mut queued = 0;
            for id in ids {
                if let Some((_, changed)) = queue_analysis_tx(&tx, &queue, id, true, false)?
                    && changed
                {
                    queued += 1;
                }
            }
            tx.commit()?;
            Ok(queued)
        })
        .await?;
    app.jobs.wake_all();
    Ok(JobOutcome::Succeeded(json!({"queued": queued})))
}

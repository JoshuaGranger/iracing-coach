//! Job queue and runner: persistent rows in `jobs`, four lanes (analysis, light, io, server)
//! each running one job at a time, admission through one shared memory budget, restart recovery.

use std::collections::{HashMap, HashSet};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use rusqlite::{Connection, OptionalExtension, params};
use serde_json::{Value, json};
use tokio::sync::{broadcast, watch};

use crate::app::AppState;
use crate::config::{Config, GIB};
use crate::error::{Error, Result};
use crate::util::iso_now;
use crate::worker::Cancel;

pub const LANES: [&str; 4] = ["analysis", "light", "io", "server"];
pub const INTERACTIVE: i64 = 100;

#[derive(Debug, Clone)]
pub struct NewJob {
    pub kind: &'static str,
    pub lane: &'static str,
    pub priority: i64,
    pub key: Option<String>,
    pub customer_id: Option<i64>,
    pub subject: Value,
    /// `queued` normally; `deferred` for an analysis over the admission ceiling.
    pub state: &'static str,
    pub error_code: Option<String>,
}

impl NewJob {
    pub fn new(
        kind: &'static str,
        lane: &'static str,
        customer_id: Option<i64>,
        subject: Value,
    ) -> NewJob {
        NewJob {
            kind,
            lane,
            priority: 0,
            key: None,
            customer_id,
            subject,
            state: "queued",
            error_code: None,
        }
    }
    pub fn interactive(mut self) -> NewJob {
        self.priority = INTERACTIVE;
        self
    }
    pub fn keyed(mut self, key: String) -> NewJob {
        self.key = Some(key);
        self
    }
    pub fn archive(sha: &str) -> NewJob {
        NewJob::new("archive", "io", None, json!({"sha256": sha})).keyed(format!("archive:{sha}"))
    }
    pub fn discover(customer_id: i64, sha: &str) -> NewJob {
        NewJob::new(
            "discover",
            "light",
            Some(customer_id),
            json!({"sha256": sha, "customer_id": customer_id}),
        )
        .keyed(format!("discover:{sha}"))
    }
    pub fn setup_files(customer_id: Option<i64>, sha: &str) -> NewJob {
        NewJob::new("setup_files", "light", customer_id, json!({"sha256": sha}))
            .keyed(format!("setup_files:{sha}"))
    }
    pub fn project(analysis_id: &str, projection: u32, customer_id: Option<i64>) -> NewJob {
        NewJob::new(
            "project",
            "server",
            customer_id,
            json!({"analysis_id": analysis_id, "projection_version": projection}),
        )
        .keyed(format!("project:{analysis_id}:p{projection}"))
    }
}

/// Inserts a job, or returns the existing job with the same idempotency key. With
/// `requeue`, a finished-unsuccessfully or deferred job with that key goes back to `queued`.
/// Returns `(job_id, created_or_requeued)`.
pub fn enqueue_tx(conn: &Connection, job: &NewJob, requeue: bool) -> rusqlite::Result<(i64, bool)> {
    if let Some(key) = &job.key {
        let existing: Option<(i64, String)> = conn
            .query_row(
                "SELECT job_id, state FROM jobs WHERE idempotency_key = ?1",
                [key],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
            .optional()?;
        if let Some((id, state)) = existing {
            let again = matches!(
                state.as_str(),
                "failed" | "cancelled" | "interrupted" | "deferred"
            );
            if requeue && again && job.state == "queued" {
                conn.execute(
                    "UPDATE jobs SET state = 'queued', error_code = NULL, error_message = NULL,
                       started_at = NULL, finished_at = NULL, result_json = NULL, subject_json = ?2,
                       created_at = ?3 WHERE job_id = ?1",
                    params![id, job.subject.to_string(), iso_now()],
                )?;
                return Ok((id, true));
            }
            if again && job.state == "deferred" && state != "deferred" {
                conn.execute(
                    "UPDATE jobs SET state = 'deferred', error_code = ?2, subject_json = ?3 WHERE job_id = ?1",
                    params![id, job.error_code, job.subject.to_string()],
                )?;
                return Ok((id, true));
            }
            return Ok((id, false));
        }
    }
    conn.execute(
        "INSERT INTO jobs(kind, lane, priority, state, idempotency_key, customer_id, subject_json,
                          attempts, created_at, error_code)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, 0, ?8, ?9)",
        params![
            job.kind,
            job.lane,
            job.priority,
            job.state,
            job.key,
            job.customer_id,
            job.subject.to_string(),
            iso_now(),
            job.error_code
        ],
    )?;
    Ok((conn.last_insert_rowid(), true))
}

/// Queues the projection of an analysis unless one is already queued or running; a finished
/// projection job is queued again (its view files are missing or outdated).
pub fn requeue_project_tx(
    conn: &Connection,
    analysis_id: &str,
    projection: u32,
    customer_id: Option<i64>,
) -> rusqlite::Result<i64> {
    let job = NewJob::project(analysis_id, projection, customer_id);
    let key = job.key.clone().unwrap_or_default();
    let existing: Option<(i64, String)> = conn
        .query_row(
            "SELECT job_id, state FROM jobs WHERE idempotency_key = ?1",
            [&key],
            |r| Ok((r.get(0)?, r.get(1)?)),
        )
        .optional()?;
    match existing {
        None => Ok(enqueue_tx(conn, &job, false)?.0),
        Some((id, state)) if state == "queued" || state == "running" => Ok(id),
        Some((id, _)) => {
            conn.execute(
                "UPDATE jobs SET state = 'queued', error_code = NULL, error_message = NULL, started_at = NULL,
                   finished_at = NULL, result_json = NULL, created_at = ?2 WHERE job_id = ?1",
                params![id, iso_now()],
            )?;
            Ok(id)
        }
    }
}

pub fn job_value(conn: &Connection, job_id: i64) -> rusqlite::Result<Option<Value>> {
    conn.query_row(
        "SELECT job_id, kind, state, lane, priority, customer_id, subject_json, attempts, created_at,
                started_at, finished_at, error_code, error_message, result_json
         FROM jobs WHERE job_id = ?1",
        [job_id],
        |r| {
            let subject: String = r.get(6)?;
            let result: Option<String> = r.get(13)?;
            Ok(json!({
                "job_id": r.get::<_, i64>(0)?,
                "kind": r.get::<_, String>(1)?,
                "state": r.get::<_, String>(2)?,
                "lane": r.get::<_, String>(3)?,
                "priority": r.get::<_, i64>(4)?,
                "customer_id": r.get::<_, Option<i64>>(5)?,
                "subject": serde_json::from_str::<Value>(&subject).unwrap_or(json!({})),
                "attempts": r.get::<_, i64>(7)?,
                "created_at": r.get::<_, String>(8)?,
                "started_at": r.get::<_, Option<String>>(9)?,
                "finished_at": r.get::<_, Option<String>>(10)?,
                "error_code": r.get::<_, Option<String>>(11)?,
                "error_message": r.get::<_, Option<String>>(12)?,
                "result": result.and_then(|t| serde_json::from_str::<Value>(&t).ok()),
            }))
        },
    )
    .optional()
}

pub fn job_ref(conn: &Connection, job_id: i64) -> rusqlite::Result<Value> {
    conn.query_row(
        "SELECT job_id, kind, state FROM jobs WHERE job_id = ?1",
        [job_id],
        |r| {
            Ok(json!({
                "job_id": r.get::<_, i64>(0)?,
                "kind": r.get::<_, String>(1)?,
                "state": r.get::<_, String>(2)?,
            }))
        },
    )
}

struct Running {
    cancel: Cancel,
    lane: &'static str,
    customer_id: Option<i64>,
    estimate: u64,
}

/// A terminal state write that SQLite refused; kept and retried until it lands.
pub struct TerminalWrite {
    job: JobRow,
    state: &'static str,
    code: Option<String>,
    message: Option<String>,
    result: Option<String>,
}

pub struct JobRuntime {
    /// Set by the snapshot while it holds the light lane.
    pub light_hold: std::sync::atomic::AtomicBool,
    running: Mutex<HashMap<i64, Running>>,
    /// Terminal writes waiting for SQLite (`flush_pending`).
    pending: Mutex<Vec<TerminalWrite>>,
    /// Drivers whose `.write-*` and `.copy-*` temp files wait for their workers to go idle.
    deferred_cleanup: Mutex<HashSet<Option<i64>>>,
    wake: watch::Sender<u64>,
    pub finished: broadcast::Sender<i64>,
}

impl Default for JobRuntime {
    fn default() -> Self {
        JobRuntime {
            light_hold: std::sync::atomic::AtomicBool::new(false),
            running: Mutex::new(HashMap::new()),
            pending: Mutex::new(Vec::new()),
            deferred_cleanup: Mutex::new(HashSet::new()),
            wake: watch::channel(0).0,
            finished: broadcast::channel(256).0,
        }
    }
}

pub enum Admit {
    /// The job's declared memory is reserved until the guard is dropped, on every path.
    Yes(AdmissionGuard),
    Wait,
    Never,
}

/// Holds one job's slice of the shared memory budget; dropping it releases the slice.
pub struct AdmissionGuard {
    runtime: Arc<JobRuntime>,
    job_id: i64,
}

impl Drop for AdmissionGuard {
    fn drop(&mut self) {
        self.runtime.release(self.job_id);
    }
}

impl JobRuntime {
    pub fn wake_all(&self) {
        self.wake.send_modify(|v| *v += 1);
    }

    pub fn subscribe(&self) -> watch::Receiver<u64> {
        self.wake.subscribe()
    }

    /// Reserves `estimate` bytes of the shared budget for `job_id`.
    pub fn try_admit(
        self: &Arc<Self>,
        cfg: &Config,
        job_id: i64,
        lane: &'static str,
        customer_id: Option<i64>,
        estimate: u64,
        cancel: &Cancel,
    ) -> Admit {
        let mut running = self.running.lock().unwrap();
        let in_use: u64 = running.values().map(|r| r.estimate).sum();
        let limit = cfg.memory_budget_bytes;
        if estimate + cfg.server_reserve_bytes > limit {
            return Admit::Never;
        }
        if in_use + estimate + cfg.server_reserve_bytes > limit {
            return Admit::Wait;
        }
        running.insert(
            job_id,
            Running {
                cancel: cancel.clone(),
                lane,
                customer_id,
                estimate,
            },
        );
        Admit::Yes(AdmissionGuard {
            runtime: self.clone(),
            job_id,
        })
    }

    fn release(&self, job_id: i64) {
        self.running.lock().unwrap().remove(&job_id);
    }

    pub fn lane_busy(&self, lane: &str) -> bool {
        self.running
            .lock()
            .unwrap()
            .values()
            .any(|r| r.lane == lane)
    }

    /// True when no other running job belongs to this driver.
    pub fn driver_idle_except(&self, customer_id: Option<i64>, job_id: i64) -> bool {
        !self
            .running
            .lock()
            .unwrap()
            .iter()
            .any(|(id, r)| *id != job_id && r.customer_id == customer_id)
    }

    pub fn running_ids(&self) -> Vec<i64> {
        self.running.lock().unwrap().keys().copied().collect()
    }

    /// Cancels a running job; false when it is not running here.
    pub fn cancel(&self, job_id: i64) -> bool {
        match self.running.lock().unwrap().get(&job_id) {
            Some(r) => {
                r.cancel.cancel();
                true
            }
            None => false,
        }
    }

    /// Notes that a killed worker's `.write-*` and `.copy-*` files could not be removed because
    /// another worker of the driver was running.
    pub fn defer_cleanup(&self, customer_id: Option<i64>) {
        self.deferred_cleanup.lock().unwrap().insert(customer_id);
    }

    /// Takes the drivers with deferred cleanup that have no running job now.
    pub fn take_idle_deferred(&self) -> Vec<Option<i64>> {
        let running = self.running.lock().unwrap();
        let mut deferred = self.deferred_cleanup.lock().unwrap();
        let idle: Vec<Option<i64>> = deferred
            .iter()
            .copied()
            .filter(|c| !running.values().any(|r| r.customer_id == *c))
            .collect();
        for customer in &idle {
            deferred.remove(customer);
        }
        idle
    }
}

/// Runs the deferred orphan cleanup of every driver whose lanes have gone idle
/// (`nas-layout.md` section 7). Returns the files removed.
pub fn sweep_deferred_cleanup(app: &AppState) -> usize {
    app.jobs
        .take_idle_deferred()
        .into_iter()
        .map(|customer| crate::worker::cleanup_strays(&app.cfg.py_archive(customer.unwrap_or(0))))
        .sum()
}

#[derive(Debug, Clone)]
pub struct JobRow {
    pub job_id: i64,
    pub kind: String,
    pub lane: String,
    pub customer_id: Option<i64>,
    pub subject: Value,
    pub attempts: i64,
}

#[derive(Debug)]
pub enum JobOutcome {
    Succeeded(Value),
    Failed { code: String, message: String },
    Deferred { code: String, message: String },
    Cancelled,
}

impl JobOutcome {
    pub fn failed(code: &str, message: impl Into<String>) -> JobOutcome {
        JobOutcome::Failed {
            code: code.to_string(),
            message: message.into(),
        }
    }
}

/// Memory a job declares before it starts (`worker-interface.md` section 6).
pub fn estimate_bytes(job: &JobRow) -> u64 {
    if let Some(v) = job.subject["estimate_bytes"].as_u64() {
        return v;
    }
    match job.kind.as_str() {
        "discover" | "setup_files" | "setup_compare" | "baseline" | "tuning_outcome" | "import"
        | "tuning_history" => (GIB as f64 * 0.25) as u64,
        "tuning_recommend" => (GIB as f64 * 0.25) as u64,
        "project" => (GIB as f64 * 0.6) as u64,
        _ => 0,
    }
}

/// Estimated analysis peak: 11.5 x bytes + 0.15 GiB; x1.5 for groups of two or more.
pub fn analysis_estimate(sizes: &[u64]) -> u64 {
    let total: u64 = sizes.iter().sum();
    let base = 11.5 * total as f64 + 0.15 * GIB as f64;
    (if sizes.len() >= 2 { base * 1.5 } else { base }) as u64
}

/// Picks and runs the head of one lane. Returns false when nothing could start.
pub async fn run_next(app: &AppState, lane: &'static str) -> Result<bool> {
    if lane == "light"
        && app
            .jobs
            .light_hold
            .load(std::sync::atomic::Ordering::SeqCst)
    {
        return Ok(false);
    }
    let head: Option<JobRow> = app
        .store
        .read(move |conn| {
            Ok(conn
                .query_row(
                    "SELECT job_id, kind, lane, customer_id, subject_json, attempts FROM jobs
                     WHERE state = 'queued' AND lane = ?1 ORDER BY priority DESC, job_id ASC LIMIT 1",
                    [lane],
                    |r| {
                        let subject: String = r.get(4)?;
                        Ok(JobRow {
                            job_id: r.get(0)?,
                            kind: r.get(1)?,
                            lane: r.get(2)?,
                            customer_id: r.get(3)?,
                            subject: serde_json::from_str(&subject).unwrap_or(json!({})),
                            attempts: r.get(5)?,
                        })
                    },
                )
                .optional()?)
        })
        .await?;
    let Some(job) = head else {
        return Ok(false);
    };
    let cancel = Cancel::default();
    let estimate = estimate_bytes(&job);
    let guard = match app.jobs.try_admit(
        &app.cfg,
        job.job_id,
        lane,
        job.customer_id,
        estimate,
        &cancel,
    ) {
        Admit::Wait => return Ok(false),
        Admit::Never => {
            finish(
                app,
                &job,
                JobOutcome::failed(
                    "exceeds_memory_budget",
                    "the job's declared memory does not fit the worker budget",
                ),
            )
            .await?;
            return Ok(true);
        }
        Admit::Yes(guard) => guard,
    };
    // From here every exit drops the guard, so the budget is never leaked.
    let started = {
        let id = job.job_id;
        let faults = app.faults.clone();
        app.store
            .write(move |conn| {
                faults.check("job.before_running")?;
                let n = conn.execute(
                    "UPDATE jobs SET state = 'running', started_at = ?2, attempts = attempts + 1
                     WHERE job_id = ?1 AND state = 'queued'",
                    params![id, iso_now()],
                )?;
                Ok(n == 1)
            })
            .await?
    };
    if !started {
        return Ok(true);
    }
    publish_state(app, job.job_id, None).await;
    if let Err(e) = refresh_for_job(app, &job).await {
        // derived state only; the job is running and must run
        tracing::warn!(job_id = job.job_id, error = %e, "session state not refreshed");
    }

    let handler_app = app.clone();
    let handler_job = job.clone();
    let handler_cancel = cancel.clone();
    let outcome = tokio::spawn(async move {
        crate::handlers::run(&handler_app, &handler_job, &handler_cancel).await
    })
    .await
    .unwrap_or_else(|e| JobOutcome::failed("internal", format!("job task panicked: {e}")));
    let outcome = if cancel.is_cancelled() && !matches!(outcome, JobOutcome::Succeeded(_)) {
        JobOutcome::Cancelled
    } else {
        outcome
    };
    drop(guard);
    finish(app, &job, outcome).await?;
    Ok(true)
}

async fn publish_state(app: &AppState, job_id: i64, error_code: Option<String>) {
    let reader = app
        .store
        .read(move |conn| {
            let v = job_ref(conn, job_id)?;
            let customer: Option<i64> = conn.query_row(
                "SELECT customer_id FROM jobs WHERE job_id = ?1",
                [job_id],
                |r| r.get(0),
            )?;
            Ok((v, customer))
        })
        .await;
    if let Ok((value, customer)) = reader {
        app.events.job_state(value, customer, error_code.as_deref());
    }
}

async fn finish(app: &AppState, job: &JobRow, outcome: JobOutcome) -> Result<()> {
    let (state, code, message, result) = match outcome {
        JobOutcome::Succeeded(v) => ("succeeded", None, None, Some(v.to_string())),
        JobOutcome::Failed { code, message } => ("failed", Some(code), Some(message), None),
        JobOutcome::Deferred { code, message } => ("deferred", Some(code), Some(message), None),
        JobOutcome::Cancelled => ("cancelled", Some("cancelled".to_string()), None, None),
    };
    let terminal = TerminalWrite {
        job: job.clone(),
        state,
        code,
        message,
        result,
    };
    match write_terminal(app, &terminal).await {
        Ok(()) => after_finish(app, &terminal).await,
        Err(e) => {
            // SQLite refused the write (disk full, busy): the outcome is kept and retried by the
            // lane loop, never dropped, so the row does not stay `running` without an owner.
            tracing::error!(job_id = job.job_id, error = %e, "job outcome not written; will retry");
            app.jobs.pending.lock().unwrap().push(terminal);
        }
    }
    Ok(())
}

async fn write_terminal(app: &AppState, terminal: &TerminalWrite) -> Result<()> {
    let id = terminal.job.job_id;
    let (state, code, message, result) = (
        terminal.state,
        terminal.code.clone(),
        terminal.message.clone(),
        terminal.result.clone(),
    );
    let faults = app.faults.clone();
    app.store
        .write(move |conn| {
            faults.check("job.before_finish")?;
            conn.execute(
                "UPDATE jobs SET state = ?2, finished_at = ?3, error_code = ?4, error_message = ?5,
                   result_json = ?6 WHERE job_id = ?1",
                params![id, state, iso_now(), code, message, result],
            )?;
            Ok(())
        })
        .await
}

/// What follows a written terminal state; none of it may stop the lane.
async fn after_finish(app: &AppState, terminal: &TerminalWrite) {
    let job = &terminal.job;
    if let Err(e) = refresh_for_job(app, job).await {
        tracing::warn!(job_id = job.job_id, error = %e, "session state not refreshed");
    }
    // recordings no queued job needs any more leave the spool once they are on the NAS
    let mut shas: Vec<String> = Vec::new();
    if let Some(sha) = job.subject["sha256"].as_str() {
        shas.push(sha.to_string());
    }
    if let Some(list) = job.subject["recordings"].as_array() {
        shas.extend(
            list.iter()
                .filter_map(|r| r["sha256"].as_str().map(str::to_string)),
        );
    }
    for sha in shas {
        if let Err(e) = crate::handlers::release_spool(app, &sha).await {
            tracing::warn!(error = %e, "spool release failed");
        }
    }
    sweep_deferred_cleanup(app);
    publish_state(app, job.job_id, terminal.code.clone()).await;
    let _ = app.jobs.finished.send(job.job_id);
    app.jobs.wake_all();
}

/// Writes the terminal states SQLite refused earlier. Returns how many landed.
pub async fn flush_pending(app: &AppState) -> usize {
    let items = std::mem::take(&mut *app.jobs.pending.lock().unwrap());
    let mut landed = 0;
    for terminal in items {
        match write_terminal(app, &terminal).await {
            Ok(()) => {
                after_finish(app, &terminal).await;
                landed += 1;
            }
            Err(e) => {
                tracing::error!(job_id = terminal.job.job_id, error = %e, "job outcome still not written");
                app.jobs.pending.lock().unwrap().push(terminal);
            }
        }
    }
    landed
}

/// Recomputes `sessions.analysis_state` for the session an analysis or projection job concerns.
async fn refresh_for_job(app: &AppState, job: &JobRow) -> Result<()> {
    let mut sessions = Vec::new();
    if let Some(id) = job.subject["session_id"].as_i64() {
        sessions.push(id);
    }
    if job.kind == "project"
        && let Some(aid) = job.subject["analysis_id"].as_str()
    {
        let aid = aid.to_string();
        sessions.extend(
            app.store
                .read(move |conn| {
                    let mut stmt = conn.prepare(
                        "SELECT session_id FROM sessions WHERE current_analysis_id = ?1
                         UNION SELECT session_id FROM analyses WHERE analysis_id = ?1",
                    )?;
                    Ok(stmt
                        .query_map([aid], |r| r.get::<_, i64>(0))?
                        .collect::<rusqlite::Result<Vec<_>>>()?)
                })
                .await?,
        );
    }
    if sessions.is_empty() {
        return Ok(());
    }
    app.store
        .write(move |conn| {
            for id in sessions {
                refresh_session_state(conn, id)?;
            }
            Ok(())
        })
        .await
}

/// Derives `analysis_state` from the session's jobs and current analysis.
pub fn refresh_session_state(conn: &Connection, session_id: i64) -> rusqlite::Result<()> {
    let count = |states: &str| -> rusqlite::Result<i64> {
        conn.query_row(
            &format!(
                "SELECT COUNT(*) FROM jobs WHERE kind = 'analyze' AND state IN ({states})
                 AND json_extract(subject_json, '$.session_id') = ?1"
            ),
            [session_id],
            |r| r.get(0),
        )
    };
    let (current, projection): (Option<String>, Option<String>) = conn
        .query_row(
            "SELECT s.current_analysis_id, a.projection_state FROM sessions s
             LEFT JOIN analyses a ON a.analysis_id = s.current_analysis_id WHERE s.session_id = ?1",
            [session_id],
            |r| Ok((r.get(0)?, r.get(1)?)),
        )
        .optional()?
        .unwrap_or((None, None));
    let project_pending: i64 = conn.query_row(
        "SELECT COUNT(*) FROM jobs WHERE kind = 'project' AND state IN ('queued', 'running')
         AND json_extract(subject_json, '$.analysis_id') = ?1",
        [current.clone().unwrap_or_default()],
        |r| r.get(0),
    )?;
    let (state, reason): (&str, Option<String>) = if count("'running'")? > 0 {
        ("running", None)
    } else if count("'queued', 'interrupted'")? > 0 {
        ("queued", None)
    } else if project_pending > 0 {
        ("running", None)
    } else if current.is_some() && projection.as_deref() == Some("ready") {
        ("ready", None)
    } else if count("'deferred'")? > 0 {
        let reason: Option<String> = conn
            .query_row(
                "SELECT error_code FROM jobs WHERE kind = 'analyze' AND state = 'deferred'
                 AND json_extract(subject_json, '$.session_id') = ?1 ORDER BY job_id DESC LIMIT 1",
                [session_id],
                |r| r.get(0),
            )
            .optional()?
            .flatten();
        ("deferred", reason)
    } else if count("'failed', 'cancelled'")? > 0 {
        let reason: Option<String> = conn
            .query_row(
                "SELECT error_code FROM jobs WHERE kind = 'analyze' AND state IN ('failed', 'cancelled')
                 AND json_extract(subject_json, '$.session_id') = ?1 ORDER BY job_id DESC LIMIT 1",
                [session_id],
                |r| r.get(0),
            )
            .optional()?
            .flatten();
        ("failed", reason)
    } else {
        ("none", None)
    };
    conn.execute(
        "UPDATE sessions SET analysis_state = ?2, analysis_reason = ?3 WHERE session_id = ?1",
        params![session_id, state, reason],
    )?;
    Ok(())
}

/// Digest of the recordings in a session (`analyses.membership_sha256`).
pub fn membership_digest(
    conn: &Connection,
    session_id: i64,
) -> rusqlite::Result<(String, Vec<(String, u64, String)>)> {
    let mut stmt = conn.prepare(
        "SELECT sr.recording_sha256, b.size, b.first_name FROM session_recordings sr
         JOIN blobs b ON b.sha256 = sr.recording_sha256 WHERE sr.session_id = ?1
         ORDER BY sr.recording_sha256",
    )?;
    let members: Vec<(String, u64, String)> = stmt
        .query_map([session_id], |r| {
            Ok((
                r.get::<_, String>(0)?,
                r.get::<_, i64>(1)? as u64,
                r.get::<_, String>(2)?,
            ))
        })?
        .collect::<rusqlite::Result<_>>()?;
    let joined = members
        .iter()
        .map(|m| m.0.as_str())
        .collect::<Vec<_>>()
        .join("\n");
    Ok((crate::util::sha256_hex(joined.as_bytes()), members))
}

/// What `queue_analysis_tx` needs from the app (no `Arc<App>` inside a writer closure).
#[derive(Debug, Clone)]
pub struct QueueParams {
    pub bundle_digest: String,
    pub profile: String,
    pub ceiling: u64,
}

impl QueueParams {
    pub fn of(app: &AppState) -> QueueParams {
        QueueParams {
            bundle_digest: app.worker_identity.bundle_digest.clone(),
            profile: app
                .worker_identity
                .profile_version
                .clone()
                .unwrap_or_default(),
            ceiling: app.cfg.analysis_ceiling_bytes,
        }
    }
}

/// Queues (or re-queues) the analysis of a session's current membership. Over the admission
/// ceiling the job is recorded as `deferred` with `exceeds_memory_budget`.
pub fn queue_analysis_tx(
    conn: &Connection,
    params: &QueueParams,
    session_id: i64,
    requeue: bool,
    interactive: bool,
) -> rusqlite::Result<Option<(i64, bool)>> {
    let (digest, members) = membership_digest(conn, session_id)?;
    if members.is_empty() {
        return Ok(None);
    }
    let (customer, group_key): (i64, String) = conn.query_row(
        "SELECT customer_id, group_key FROM sessions WHERE session_id = ?1",
        [session_id],
        |r| Ok((r.get(0)?, r.get(1)?)),
    )?;
    let sizes: Vec<u64> = members.iter().map(|m| m.1).collect();
    let estimate = analysis_estimate(&sizes);
    let key = format!(
        "analyze:{session_id}:{digest}:{}:{}",
        params.bundle_digest, params.profile
    );
    let local_file = group_key.strip_prefix("local-file:").map(str::to_string);
    let subject = json!({
        "session_id": session_id,
        "group_key": group_key,
        "local_file": local_file,
        "membership_sha256": digest,
        "recordings": members.iter().map(|m| json!({"sha256": m.0, "size": m.1, "name": m.2})).collect::<Vec<_>>(),
        "estimate_bytes": estimate,
    });
    let mut job = NewJob::new("analyze", "analysis", Some(customer), subject).keyed(key);
    if interactive {
        job.priority = INTERACTIVE;
    }
    if estimate > params.ceiling {
        job.state = "deferred";
        job.error_code = Some("exceeds_memory_budget".into());
    }
    let result = enqueue_tx(conn, &job, requeue)?;
    refresh_session_state(conn, session_id)?;
    Ok(Some(result))
}

/// Lane loops (background mode). Each lane runs one job at a time.
pub fn spawn_lanes(app: &AppState) {
    for lane in LANES {
        let app = app.clone();
        tokio::spawn(async move {
            let mut rx = app.jobs.subscribe();
            loop {
                let _ = rx.borrow_and_update();
                flush_pending(&app).await;
                sweep_deferred_cleanup(&app);
                match run_next(&app, lane).await {
                    Ok(true) => continue,
                    Ok(false) => {}
                    Err(e) => {
                        tracing::error!(lane, error = %e, "job lane error");
                        tokio::time::sleep(Duration::from_secs(1)).await;
                    }
                }
                tokio::select! {
                    _ = rx.changed() => {}
                    _ = tokio::time::sleep(Duration::from_secs(5)) => {}
                }
            }
        });
    }
}

/// Runs every queued job that can start, lane by lane, until nothing is runnable (used by tests
/// and by `rebuild-index` style one-shot work).
pub async fn drain(app: &AppState) -> Result<usize> {
    let mut ran = 0;
    loop {
        flush_pending(app).await;
        sweep_deferred_cleanup(app);
        let mut progressed = false;
        for lane in LANES {
            while run_next(app, lane).await? {
                ran += 1;
                progressed = true;
            }
        }
        if !progressed {
            return Ok(ran);
        }
    }
}

/// Start-up: running jobs become `interrupted` and are requeued once; stale job roots go.
pub async fn recover(app: &AppState) -> Result<()> {
    app.store
        .write(|conn| {
            conn.execute(
                "UPDATE jobs SET state = 'interrupted', finished_at = ?1 WHERE state = 'running'",
                [iso_now()],
            )?;
            conn.execute(
                "UPDATE jobs SET state = 'queued', error_code = NULL, finished_at = NULL
                 WHERE state = 'interrupted' AND attempts <= 1",
                [],
            )?;
            let sessions: Vec<i64> = conn
                .prepare("SELECT session_id FROM sessions")?
                .query_map([], |r| r.get(0))?
                .collect::<rusqlite::Result<_>>()?;
            for id in sessions {
                refresh_session_state(conn, id)?;
            }
            Ok(())
        })
        .await?;
    let py = app.cfg.data_dir.join("py");
    if let Ok(drivers) = std::fs::read_dir(&py) {
        for driver in drivers.flatten() {
            let jobs = driver.path().join("jobs");
            if jobs.is_dir() {
                let _ = std::fs::remove_dir_all(&jobs);
            }
            // no worker runs at start: the temp files killed workers left are safe to remove
            crate::worker::cleanup_strays(&driver.path().join("archive"));
        }
    }
    Ok(())
}

/// Waits (bounded) for a job to leave `queued`/`running`; returns its final row.
pub async fn wait_finished(app: &AppState, job_id: i64, limit: Duration) -> Result<Option<Value>> {
    let mut rx = app.jobs.finished.subscribe();
    let check = |app: AppState| async move {
        app.store
            .read(move |conn| Ok(job_value(conn, job_id)?))
            .await
    };
    let deadline = tokio::time::Instant::now() + limit;
    loop {
        let value = check(app.clone()).await?;
        let Some(value) = value else {
            return Err(Error::not_found("no such job"));
        };
        if !matches!(value["state"].as_str(), Some("queued") | Some("running")) {
            return Ok(Some(value));
        }
        let remaining = deadline.saturating_duration_since(tokio::time::Instant::now());
        if remaining.is_zero() {
            return Ok(None);
        }
        let _ = tokio::time::timeout(remaining.min(Duration::from_millis(500)), rx.recv()).await;
    }
}

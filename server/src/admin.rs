//! Library cleanup (`POST /api/v1/admin/remove`): removes named setup file entries and recordings
//! (by hash) together with their derived rows and views, or lists what it would remove.
//!
//! Every target is planned from the database first (`plan_*`, read-only), then applied from the
//! same plan inside one write transaction (`apply_*`). The NAS, the collector's journal and the
//! worker's archive are never touched; user-authored rows are never deleted (a target with a note
//! is blocked). The planning functions are shared by the dry run and the real call, so a dry run
//! lists exactly what a real call does.

use std::collections::{BTreeMap, BTreeSet};
use std::io::Write;

use rusqlite::{Connection, OptionalExtension, params};
use serde_json::{Value, json};

use crate::app::AppState;
use crate::error::{Error, Result};
use crate::jobs::{self, QueueParams};
use crate::schema::parse_request;
use crate::util::{is_sha256_hex, iso_now};

/// What a removal never touches (reported with every answer).
const KEPT: [&str; 4] = [
    "NAS raw files and manifests",
    "the collector's upload journal",
    "the worker's per-driver archive",
    "user-authored rows (notes, layouts, preferences, drafts, annotations)",
];

const LOG_FILE: &str = "admin-removals.jsonl";

// ------------------------------------------------------------------ helpers

fn base_name(path: &str) -> &str {
    path.rsplit('/').next().unwrap_or(path)
}

fn placeholders(n: usize) -> String {
    vec!["?"; n].join(", ")
}

fn ints(values: &[i64]) -> Vec<&dyn rusqlite::ToSql> {
    values.iter().map(|v| v as &dyn rusqlite::ToSql).collect()
}

fn strs(values: &[String]) -> Vec<&dyn rusqlite::ToSql> {
    values.iter().map(|v| v as &dyn rusqlite::ToSql).collect()
}

/// Whether any of `ids` has a note of `kind` (`session`, `setup` or `analysis`).
fn has_notes(conn: &Connection, kind: &str, ids: &[String]) -> rusqlite::Result<bool> {
    if ids.is_empty() {
        return Ok(false);
    }
    let mut args: Vec<&dyn rusqlite::ToSql> = vec![&kind];
    args.extend(strs(ids));
    conn.query_row(
        &format!(
            "SELECT COUNT(*) > 0 FROM notes WHERE subject_kind = ?1 AND subject_id IN ({})",
            placeholders(ids.len())
        ),
        args.as_slice(),
        |r| r.get(0),
    )
}

/// Setups whose every observation is in `removing` (observation id to setup id).
fn emptied_setups(conn: &Connection, removing: &BTreeMap<i64, i64>) -> rusqlite::Result<Vec<i64>> {
    let mut gone = Vec::new();
    let affected: BTreeSet<i64> = removing.values().copied().collect();
    for setup in affected {
        let total: i64 = conn.query_row(
            "SELECT COUNT(*) FROM setup_observations WHERE setup_id = ?1",
            [setup],
            |r| r.get(0),
        )?;
        let leaving = removing.values().filter(|s| **s == setup).count() as i64;
        if total == leaving {
            gone.push(setup);
        }
    }
    Ok(gone)
}

fn running_jobs_for(conn: &Connection, sha: &str) -> rusqlite::Result<bool> {
    conn.query_row(
        "SELECT COUNT(*) > 0 FROM jobs WHERE state = 'running' AND instr(subject_json, ?1) > 0",
        [sha],
        |r| r.get(0),
    )
}

fn delete_where_in(
    conn: &Connection,
    sql_prefix: &str,
    ids: &[&dyn rusqlite::ToSql],
) -> rusqlite::Result<()> {
    if ids.is_empty() {
        return Ok(());
    }
    conn.execute(&format!("{sql_prefix} ({})", placeholders(ids.len())), ids)?;
    Ok(())
}

// --------------------------------------------------------------- setup file

struct FileHit {
    customer_id: i64,
    car_folder: String,
    relative_path: String,
    sha: String,
    present: bool,
}

struct SetupFilePlan {
    name: String,
    base_lower: String,
    files: Vec<FileHit>,
    visits: Vec<String>,
    observations: BTreeMap<i64, i64>,
    setups: Vec<i64>,
    blobs_removed: Vec<String>,
    blobs_kept: Vec<String>,
    blocked: Vec<&'static str>,
    customers: BTreeSet<i64>,
}

impl SetupFilePlan {
    fn found(&self) -> bool {
        !self.files.is_empty() || !self.visits.is_empty()
    }

    fn report(&self, applied: bool) -> Value {
        json!({
            "name": self.name,
            "found": self.found(),
            "applied": applied,
            "blocked": self.blocked,
            "files": self.files.iter().map(|f| json!({
                "customer_id": f.customer_id, "car_folder": f.car_folder,
                "relative_path": f.relative_path, "sha256": f.sha, "present": f.present,
            })).collect::<Vec<_>>(),
            "garage_visits": self.visits,
            "setup_observations": self.observations.len(),
            "setups": self.setups,
            "blobs_removed": self.blobs_removed,
            "blobs_kept": self.blobs_kept,
        })
    }
}

fn plan_setup_file(
    conn: &Connection,
    name: &str,
    customer: Option<i64>,
) -> rusqlite::Result<SetupFilePlan> {
    let wanted = name.replace('\\', "/");
    let wanted_lower = wanted.trim_matches('/').to_lowercase();
    let qualified = wanted_lower.contains('/');
    let base_lower = base_name(&wanted_lower).to_string();
    let mut plan = SetupFilePlan {
        name: name.to_string(),
        base_lower: base_lower.clone(),
        files: Vec::new(),
        visits: Vec::new(),
        observations: BTreeMap::new(),
        setups: Vec::new(),
        blobs_removed: Vec::new(),
        blobs_kept: Vec::new(),
        blocked: Vec::new(),
        customers: BTreeSet::new(),
    };
    let mut stmt = conn.prepare(
        "SELECT customer_id, car_folder, relative_path, blob_sha256, present FROM setup_files
         ORDER BY customer_id, car_folder, relative_path, blob_sha256",
    )?;
    let rows = stmt
        .query_map([], |r| {
            Ok(FileHit {
                customer_id: r.get(0)?,
                car_folder: r.get(1)?,
                relative_path: r.get(2)?,
                sha: r.get(3)?,
                present: r.get(4)?,
            })
        })?
        .collect::<rusqlite::Result<Vec<_>>>()?;
    for row in rows {
        if customer.is_some_and(|c| c != row.customer_id) {
            continue;
        }
        let rel = row.relative_path.to_lowercase();
        let hit = if qualified {
            let full = if row.car_folder.is_empty() {
                rel.clone()
            } else {
                format!("{}/{rel}", row.car_folder.to_lowercase())
            };
            full == wanted_lower
        } else {
            base_name(&rel) == base_lower
        };
        if hit {
            plan.customers.insert(row.customer_id);
            plan.files.push(row);
        }
    }

    let mut stmt = conn.prepare(
        "SELECT visit_id, committed_name, customer_id FROM garage_visits WHERE committed_name IS NOT NULL
         ORDER BY visit_id",
    )?;
    let visits = stmt
        .query_map([], |r| {
            Ok((
                r.get::<_, String>(0)?,
                r.get::<_, String>(1)?,
                r.get::<_, Option<i64>>(2)?,
            ))
        })?
        .collect::<rusqlite::Result<Vec<_>>>()?;
    for (visit, committed, owner) in visits {
        let owner_ok = match (customer, owner) {
            (Some(want), Some(have)) => want == have,
            _ => true,
        };
        if owner_ok && base_name(&committed.replace('\\', "/")).to_lowercase() == base_lower {
            if let Some(owner) = owner {
                plan.customers.insert(owner);
            }
            plan.visits.push(visit);
        }
    }

    // Which blobs go: archived, and referenced by no other file entry or name.
    let blob_shas: BTreeSet<String> = plan.files.iter().map(|f| f.sha.clone()).collect();
    for sha in &blob_shas {
        let archived: bool = conn
            .query_row(
                "SELECT nas_state = 'archived' FROM blobs WHERE sha256 = ?1",
                [sha],
                |r| r.get(0),
            )
            .optional()?
            .unwrap_or(false);
        let mut other_files = 0i64;
        let mut stmt = conn.prepare(
            "SELECT customer_id, car_folder, relative_path, blob_sha256 FROM setup_files WHERE blob_sha256 = ?1",
        )?;
        let all = stmt
            .query_map([sha], |r| {
                Ok((
                    r.get::<_, i64>(0)?,
                    r.get::<_, String>(1)?,
                    r.get::<_, String>(2)?,
                ))
            })?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        for (c, folder, rel) in all {
            if !plan.files.iter().any(|f| {
                f.sha == *sha
                    && f.customer_id == c
                    && f.car_folder == folder
                    && f.relative_path == rel
            }) {
                other_files += 1;
            }
        }
        let mut stmt = conn.prepare("SELECT file_name FROM blob_occurrences WHERE sha256 = ?1")?;
        let other_names = stmt
            .query_map([sha], |r| r.get::<_, String>(0))?
            .collect::<rusqlite::Result<Vec<_>>>()?
            .into_iter()
            .filter(|n| base_name(&n.replace('\\', "/")).to_lowercase() != plan.base_lower)
            .count();
        if archived && other_files == 0 && other_names == 0 {
            plan.blobs_removed.push(sha.clone());
        } else {
            plan.blobs_kept.push(sha.clone());
        }
        if running_jobs_for(conn, sha)? && !plan.blocked.contains(&"job_running") {
            plan.blocked.push("job_running");
        }
    }

    // Observations: those of the visits, and of the removed `.htm` blobs.
    let mut observations = BTreeMap::new();
    let mut collect = |sql: &str, key: &str| -> rusqlite::Result<()> {
        let mut stmt = conn.prepare(sql)?;
        let rows = stmt
            .query_map([key], |r| Ok((r.get::<_, i64>(0)?, r.get::<_, i64>(1)?)))?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        observations.extend(rows);
        Ok(())
    };
    for visit in &plan.visits {
        collect(
            "SELECT observation_id, setup_id FROM setup_observations WHERE visit_id = ?1",
            visit,
        )?;
    }
    for sha in &plan.blobs_removed {
        collect(
            "SELECT observation_id, setup_id FROM setup_observations WHERE blob_sha256 = ?1",
            sha,
        )?;
    }
    plan.observations = observations;
    plan.setups = emptied_setups(conn, &plan.observations)?;
    let setup_ids: Vec<String> = plan.setups.iter().map(|s| s.to_string()).collect();
    if has_notes(conn, "setup", &setup_ids)? {
        plan.blocked.push("has_notes");
    }
    for setup in &plan.setups {
        let owner: Option<i64> = conn
            .query_row(
                "SELECT customer_id FROM setups WHERE setup_id = ?1",
                [setup],
                |r| r.get(0),
            )
            .optional()?;
        plan.customers.extend(owner);
    }
    Ok(plan)
}

fn apply_setup_file(conn: &Connection, plan: &SetupFilePlan) -> rusqlite::Result<()> {
    let observation_ids: Vec<i64> = plan.observations.keys().copied().collect();
    delete_where_in(
        conn,
        "DELETE FROM setup_observations WHERE observation_id IN",
        &ints(&observation_ids),
    )?;
    delete_where_in(
        conn,
        "DELETE FROM setups WHERE setup_id IN",
        &ints(&plan.setups),
    )?;
    delete_where_in(
        conn,
        "DELETE FROM garage_visits WHERE visit_id IN",
        &strs(&plan.visits),
    )?;
    for f in &plan.files {
        conn.execute(
            "DELETE FROM setup_files WHERE customer_id = ?1 AND car_folder = ?2 AND relative_path = ?3 AND blob_sha256 = ?4",
            params![f.customer_id, f.car_folder, f.relative_path, f.sha],
        )?;
    }
    for sha in plan.blobs_removed.iter().chain(&plan.blobs_kept) {
        // this name's occurrences go; a kept blob keeps the occurrences of its other names
        let mut stmt = conn
            .prepare("SELECT occurrence_id, file_name FROM blob_occurrences WHERE sha256 = ?1")?;
        let rows = stmt
            .query_map([sha], |r| Ok((r.get::<_, i64>(0)?, r.get::<_, String>(1)?)))?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        for (id, file_name) in rows {
            if base_name(&file_name.replace('\\', "/")).to_lowercase() == plan.base_lower {
                conn.execute(
                    "DELETE FROM blob_occurrences WHERE occurrence_id = ?1",
                    [id],
                )?;
            }
        }
    }
    for sha in &plan.blobs_removed {
        delete_blob(conn, sha)?;
    }
    Ok(())
}

/// Removes every database row of a blob (the caller has checked it is unreferenced).
fn delete_blob(conn: &Connection, sha: &str) -> rusqlite::Result<()> {
    conn.execute("DELETE FROM jobs WHERE instr(subject_json, ?1) > 0", [sha])?;
    conn.execute("DELETE FROM uploads WHERE sha256 = ?1", [sha])?;
    conn.execute("DELETE FROM blob_occurrences WHERE sha256 = ?1", [sha])?;
    conn.execute("DELETE FROM blobs WHERE sha256 = ?1", [sha])?;
    Ok(())
}

// ---------------------------------------------------------------- recording

struct RecordingPlan {
    sha: String,
    found: bool,
    file_name: Option<String>,
    size: Option<i64>,
    nas_state: Option<String>,
    sessions_removed: Vec<(i64, Option<String>, Option<String>)>,
    sessions_requeued: Vec<i64>,
    analyses: Vec<String>,
    observations: BTreeMap<i64, i64>,
    setups: Vec<i64>,
    jobs: Vec<i64>,
    blocked: Vec<&'static str>,
    customers: BTreeSet<i64>,
}

impl RecordingPlan {
    fn report(&self, applied: bool) -> Value {
        json!({
            "sha256": self.sha,
            "found": self.found,
            "applied": applied,
            "blocked": self.blocked,
            "file_name": self.file_name,
            "size": self.size,
            "nas_state": self.nas_state,
            "sessions_removed": self.sessions_removed.iter().map(|(id, track, kind)| json!({
                "session_id": id, "track": track, "session_type": kind,
            })).collect::<Vec<_>>(),
            "sessions_requeued": self.sessions_requeued,
            "analyses": self.analyses,
            "setup_observations": self.observations.len(),
            "setups": self.setups,
            "jobs": self.jobs.len(),
        })
    }
}

fn plan_recording(conn: &Connection, sha: &str) -> rusqlite::Result<RecordingPlan> {
    let mut plan = RecordingPlan {
        sha: sha.to_string(),
        found: false,
        file_name: None,
        size: None,
        nas_state: None,
        sessions_removed: Vec::new(),
        sessions_requeued: Vec::new(),
        analyses: Vec::new(),
        observations: BTreeMap::new(),
        setups: Vec::new(),
        jobs: Vec::new(),
        blocked: Vec::new(),
        customers: BTreeSet::new(),
    };
    let blob: Option<(String, String, i64, String)> = conn
        .query_row(
            "SELECT kind, first_name, size, nas_state FROM blobs WHERE sha256 = ?1",
            [sha],
            |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?)),
        )
        .optional()?;
    let Some((kind, name, size, nas_state)) = blob else {
        return Ok(plan);
    };
    if kind != "ibt" {
        plan.blocked.push("not_a_recording");
        return Ok(plan);
    }
    plan.found = true;
    plan.file_name = Some(name);
    plan.size = Some(size);
    if nas_state != "archived" {
        plan.blocked.push("not_archived");
    }
    plan.nas_state = Some(nas_state);
    let owner: Option<i64> = conn
        .query_row(
            "SELECT customer_id FROM recordings WHERE sha256 = ?1",
            [sha],
            |r| r.get(0),
        )
        .optional()?
        .flatten();
    plan.customers.extend(owner);

    let sessions: Vec<(i64, Option<String>, Option<String>, i64)> = conn
        .prepare(
            "SELECT s.session_id, s.track, s.session_type, s.customer_id FROM sessions s
             WHERE s.session_id IN (SELECT session_id FROM session_recordings WHERE recording_sha256 = ?1)
             ORDER BY s.session_id",
        )?
        .query_map([sha], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?)))?
        .collect::<rusqlite::Result<_>>()?;
    for (id, track, kind, customer) in sessions {
        plan.customers.insert(customer);
        let others: i64 = conn.query_row(
            "SELECT COUNT(*) FROM session_recordings WHERE session_id = ?1 AND recording_sha256 <> ?2",
            params![id, sha],
            |r| r.get(0),
        )?;
        if others == 0 {
            plan.sessions_removed.push((id, track, kind));
        } else {
            plan.sessions_requeued.push(id);
        }
    }
    let removed_ids: Vec<i64> = plan.sessions_removed.iter().map(|s| s.0).collect();

    let mut analyses = BTreeSet::new();
    let mut stmt =
        conn.prepare("SELECT session_id, analysis_id, recordings_sha256 FROM analyses")?;
    let all = stmt
        .query_map([], |r| {
            Ok((
                r.get::<_, i64>(0)?,
                r.get::<_, String>(1)?,
                r.get::<_, String>(2)?,
            ))
        })?
        .collect::<rusqlite::Result<Vec<_>>>()?;
    for (session, id, members) in all {
        if removed_ids.contains(&session)
            || (plan.sessions_requeued.contains(&session) && members.contains(sha))
        {
            analyses.insert(id);
        }
    }
    plan.analyses = analyses.into_iter().collect();

    let mut stmt = conn.prepare(
        "SELECT observation_id, setup_id FROM setup_observations WHERE recording_sha256 = ?1",
    )?;
    plan.observations = stmt
        .query_map([sha], |r| Ok((r.get::<_, i64>(0)?, r.get::<_, i64>(1)?)))?
        .collect::<rusqlite::Result<_>>()?;
    plan.setups = emptied_setups(conn, &plan.observations)?;

    let mut stmt = conn.prepare(
        "SELECT job_id, state, kind, subject_json FROM jobs
         WHERE instr(subject_json, ?1) > 0
            OR (kind = 'analyze' AND json_extract(subject_json, '$.session_id') IN (SELECT value FROM json_each(?2)))
            OR (kind = 'project' AND json_extract(subject_json, '$.analysis_id') IN (SELECT value FROM json_each(?3)))
         ORDER BY job_id",
    )?;
    let jobs: Vec<(i64, String)> = stmt
        .query_map(
            params![
                sha,
                serde_json::to_string(&removed_ids).unwrap_or_else(|_| "[]".into()),
                serde_json::to_string(&plan.analyses).unwrap_or_else(|_| "[]".into())
            ],
            |r| Ok((r.get(0)?, r.get(1)?)),
        )?
        .collect::<rusqlite::Result<_>>()?;
    for (id, state) in jobs {
        if state == "running" && !plan.blocked.contains(&"job_running") {
            plan.blocked.push("job_running");
        }
        plan.jobs.push(id);
    }

    let session_ids: Vec<String> = removed_ids.iter().map(|s| s.to_string()).collect();
    let setup_ids: Vec<String> = plan.setups.iter().map(|s| s.to_string()).collect();
    if has_notes(conn, "session", &session_ids)?
        || has_notes(conn, "analysis", &plan.analyses)?
        || has_notes(conn, "setup", &setup_ids)?
    {
        plan.blocked.push("has_notes");
    }
    Ok(plan)
}

fn apply_recording(
    conn: &Connection,
    plan: &RecordingPlan,
    queue: &QueueParams,
    projection: u32,
) -> rusqlite::Result<()> {
    let sha = &plan.sha;
    let removed_ids: Vec<i64> = plan.sessions_removed.iter().map(|s| s.0).collect();
    delete_where_in(
        conn,
        "UPDATE analyses SET superseded_by = NULL WHERE superseded_by IN",
        &strs(&plan.analyses),
    )?;
    delete_where_in(
        conn,
        "UPDATE sessions SET current_analysis_id = NULL WHERE current_analysis_id IN",
        &strs(&plan.analyses),
    )?;
    delete_where_in(
        conn,
        "DELETE FROM analyses WHERE analysis_id IN",
        &strs(&plan.analyses),
    )?;
    let observation_ids: Vec<i64> = plan.observations.keys().copied().collect();
    delete_where_in(
        conn,
        "DELETE FROM setup_observations WHERE observation_id IN",
        &ints(&observation_ids),
    )?;
    delete_where_in(
        conn,
        "DELETE FROM setups WHERE setup_id IN",
        &ints(&plan.setups),
    )?;
    conn.execute(
        "DELETE FROM session_recordings WHERE recording_sha256 = ?1",
        [sha],
    )?;
    delete_where_in(
        conn,
        "DELETE FROM sessions WHERE session_id IN",
        &ints(&removed_ids),
    )?;
    delete_where_in(conn, "DELETE FROM jobs WHERE job_id IN", &ints(&plan.jobs))?;
    conn.execute("DELETE FROM recordings WHERE sha256 = ?1", [sha])?;
    delete_blob(conn, sha)?;
    clear_last_investigation(conn, &removed_ids, &plan.analyses)?;
    for session in &plan.sessions_requeued {
        // An analysis of exactly the remaining recordings may already exist (the session was
        // analysed before the removed recording joined); it becomes current again. Its job is
        // finished, so queueing would find the job by its key and do nothing.
        let (digest, _) = jobs::membership_digest(conn, *session)?;
        let existing: Option<String> = conn
            .query_row(
                "SELECT analysis_id FROM analyses WHERE session_id = ?1 AND membership_sha256 = ?2
                   AND profile_version = ?3 ORDER BY created_at DESC, analysis_id LIMIT 1",
                params![session, digest, queue.profile],
                |r| r.get(0),
            )
            .optional()?;
        match existing {
            Some(id) => {
                conn.execute(
                    "UPDATE sessions SET current_analysis_id = ?2 WHERE session_id = ?1",
                    params![session, id],
                )?;
                // a result that arrived while the session was growing was stored without views
                let views_ready: bool = conn.query_row(
                    "SELECT projection_state = 'ready' AND projection_version = ?2 FROM analyses WHERE analysis_id = ?1",
                    params![id, projection],
                    |r| r.get(0),
                )?;
                if !views_ready {
                    conn.execute(
                        "UPDATE analyses SET projection_state = 'pending' WHERE analysis_id = ?1 AND projection_state <> 'pending'",
                        [&id],
                    )?;
                    jobs::requeue_project_tx(
                        conn,
                        &id,
                        projection,
                        plan.customers.first().copied(),
                    )?;
                }
                jobs::refresh_session_state(conn, *session)?;
            }
            None => {
                jobs::queue_analysis_tx(conn, queue, *session, true, false)?;
            }
        }
    }
    Ok(())
}

/// A driver's remembered investigation that names a removed session or analysis is cleared (the
/// browser would otherwise open a page that no longer exists).
fn clear_last_investigation(
    conn: &Connection,
    sessions: &[i64],
    analyses: &[String],
) -> rusqlite::Result<()> {
    let rows: Vec<(i64, String)> = conn
        .prepare("SELECT customer_id, last_investigation_json FROM driver_prefs WHERE last_investigation_json IS NOT NULL")?
        .query_map([], |r| Ok((r.get(0)?, r.get(1)?)))?
        .collect::<rusqlite::Result<_>>()?;
    for (customer, text) in rows {
        let value: Value = serde_json::from_str(&text).unwrap_or(Value::Null);
        let names_session = value["session_id"]
            .as_i64()
            .is_some_and(|s| sessions.contains(&s));
        let names_analysis = value["analysis_id"]
            .as_str()
            .is_some_and(|a| analyses.iter().any(|x| x == a));
        if names_session || names_analysis {
            conn.execute(
                "UPDATE driver_prefs SET last_investigation_json = NULL, version = version + 1, updated_at = ?2
                 WHERE customer_id = ?1",
                params![customer, iso_now()],
            )?;
            conn.execute(
                "UPDATE server_counters SET value = value + 1 WHERE name = 'user_data_generation'",
                [],
            )?;
        }
    }
    Ok(())
}

// ----------------------------------------------------------------- the call

struct Request {
    customer: Option<i64>,
    names: Vec<String>,
    shas: Vec<String>,
    dry_run: bool,
}

fn parse(body: &[u8]) -> Result<Request> {
    let value = parse_request("AdminRemoveRequest", body)?;
    let mut names: Vec<String> = Vec::new();
    for name in value["setup_files"].as_array().into_iter().flatten() {
        let name = name.as_str().unwrap_or_default().to_string();
        if !names.contains(&name) {
            names.push(name);
        }
    }
    let mut shas: Vec<String> = Vec::new();
    for sha in value["recordings"].as_array().into_iter().flatten() {
        let sha = sha.as_str().unwrap_or_default().to_string();
        if !is_sha256_hex(&sha) {
            return Err(Error::invalid("recordings must be 64 lowercase hex hashes"));
        }
        if !shas.contains(&sha) {
            shas.push(sha);
        }
    }
    if names.is_empty() && shas.is_empty() {
        return Err(Error::invalid(
            "name at least one of setup_files and recordings",
        ));
    }
    Ok(Request {
        customer: value["customer_id"].as_i64(),
        names,
        shas,
        dry_run: value["dry_run"].as_bool().unwrap_or(true),
    })
}

struct Outcome {
    setup_files: Vec<(SetupFilePlan, bool)>,
    recordings: Vec<(RecordingPlan, bool)>,
}

fn plan_all(conn: &Connection, req: &Request) -> rusqlite::Result<Outcome> {
    let mut out = Outcome {
        setup_files: Vec::new(),
        recordings: Vec::new(),
    };
    for name in &req.names {
        out.setup_files
            .push((plan_setup_file(conn, name, req.customer)?, false));
    }
    for sha in &req.shas {
        out.recordings.push((plan_recording(conn, sha)?, false));
    }
    Ok(out)
}

fn report(out: &Outcome, dry_run: bool, log: Option<&str>) -> Value {
    json!({
        "dry_run": dry_run,
        "setup_files": out.setup_files.iter().map(|(p, applied)| p.report(*applied)).collect::<Vec<_>>(),
        "recordings": out.recordings.iter().map(|(p, applied)| p.report(*applied)).collect::<Vec<_>>(),
        "kept": KEPT,
        "log": log,
    })
}

fn append_log(app: &AppState, line: &Value) -> std::io::Result<std::path::PathBuf> {
    let path = app.cfg.run_dir().join(LOG_FILE);
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir)?;
    }
    let mut file = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&path)?;
    writeln!(file, "{line}")?;
    file.sync_all()?;
    Ok(path)
}

/// `POST /api/v1/admin/remove`.
pub async fn remove(app: &AppState, body: &[u8]) -> Result<Value> {
    let req = parse(body)?;
    let (names, shas, customer) = (req.names.clone(), req.shas.clone(), req.customer);
    let read_req = Request {
        customer,
        names: names.clone(),
        shas: shas.clone(),
        dry_run: true,
    };
    let planned = app
        .store
        .read(move |conn| Ok(plan_all(conn, &read_req)?))
        .await?;
    if req.dry_run {
        return Ok(report(&planned, true, None));
    }

    // Hold the hash locks of everything the plan touches, so an upload of the same bytes cannot
    // interleave with the removal.
    let mut locked: BTreeSet<String> = shas.iter().cloned().collect();
    for (plan, _) in &planned.setup_files {
        locked.extend(plan.blobs_removed.iter().chain(&plan.blobs_kept).cloned());
    }
    let mut guards = Vec::new();
    for sha in &locked {
        guards.push(app.upload_locks.lock(sha).await);
    }

    let queue = QueueParams::of(app);
    let projection = app.projector.version();
    let spool_ready = app.cfg.spool_ready();
    let outcome = app
        .store
        .write(move |conn| {
            let tx = conn.transaction()?;
            let mut out = plan_all(&tx, &req)?;
            for (plan, applied) in &mut out.setup_files {
                if plan.found() && plan.blocked.is_empty() {
                    apply_setup_file(&tx, plan)?;
                    *applied = true;
                }
            }
            for (plan, applied) in &mut out.recordings {
                if plan.found && plan.blocked.is_empty() {
                    apply_recording(&tx, plan, &queue, projection)?;
                    *applied = true;
                }
            }
            // The spool copies go before the commit: a spool file without a blob row would be
            // adopted again as a recording at the next start, while a row without its spool file
            // (a failed commit) is an archived blob, which needs none.
            for (plan, applied) in &out.setup_files {
                if *applied {
                    for sha in &plan.blobs_removed {
                        let _ = std::fs::remove_file(spool_ready.join(sha));
                    }
                }
            }
            for (plan, applied) in &out.recordings {
                if *applied {
                    let _ = std::fs::remove_file(spool_ready.join(&plan.sha));
                }
            }
            tx.commit()?;
            Ok(out)
        })
        .await?;

    // The views of a removed analysis follow the committed rows (an orphan directory is harmless).
    let mut customers: BTreeSet<i64> = BTreeSet::new();
    for (plan, applied) in &outcome.setup_files {
        if *applied {
            customers.extend(&plan.customers);
        }
    }
    for (plan, applied) in &outcome.recordings {
        if *applied {
            customers.extend(&plan.customers);
            for analysis in &plan.analyses {
                let _ = std::fs::remove_dir_all(app.cfg.data_dir.join("views").join(analysis));
            }
        }
    }
    drop(guards);

    let any =
        outcome.setup_files.iter().any(|(_, a)| *a) || outcome.recordings.iter().any(|(_, a)| *a);
    let mut log_path = None;
    if any {
        let summary = report(&outcome, false, None);
        tracing::warn!(report = %summary, "admin removal applied");
        let line = json!({
            "at": iso_now(),
            "request": {"customer_id": customer, "setup_files": names, "recordings": shas},
            "report": summary,
        });
        match append_log(app, &line) {
            Ok(path) => log_path = Some(path.to_string_lossy().into_owned()),
            Err(e) => tracing::error!(error = %e, "admin removal log could not be written"),
        }
        for customer in customers {
            app.events.library_changed(customer, "sessions");
            app.events.library_changed(customer, "setups");
        }
        app.jobs.wake_all();
    }
    Ok(report(&outcome, false, log_path.as_deref()))
}

//! D12: one-time conversion of legacy user-authored data. The bundle is stored by hash, the job
//! reads it back, resolves every reference against the re-ingested library and attaches notes,
//! drafts and annotations; what cannot be resolved is kept verbatim as `legacy_items`. The legacy
//! worker archive travels in the bundle and goes through W6's `import_legacy_tuning_experiments`
//! on the light lane.

use rusqlite::{Connection, OptionalExtension, params};
use serde_json::{Value, json};

use crate::app::AppState;
use crate::error::{Error, Result};
use crate::jobs::{JobOutcome, JobRow};
use crate::util::{self, iso_now};
use crate::worker::Cancel;

pub fn bundle_path(app: &AppState, sha: &str) -> std::path::PathBuf {
    app.cfg.data_dir.join("import").join(format!("{sha}.json"))
}

fn slug(name: &str) -> String {
    let mut out = String::new();
    for c in name.to_lowercase().chars() {
        if c.is_ascii_alphanumeric() {
            out.push(c);
        } else if !out.ends_with('-') {
            out.push('-');
        }
    }
    let out = out.trim_matches('-').chars().take(56).collect::<String>();
    if out.is_empty() { "layout".into() } else { out }
}

fn session_for_ref(conn: &Connection, customer: i64, r: &Value) -> rusqlite::Result<Option<i64>> {
    if let Some(hashes) = r["recording_sha256"].as_array() {
        for h in hashes.iter().filter_map(|h| h.as_str()) {
            let id: Option<i64> = conn
                .query_row(
                    "SELECT s.session_id FROM session_recordings sr JOIN sessions s ON s.session_id = sr.session_id
                     WHERE sr.recording_sha256 = ?1 AND s.customer_id = ?2 LIMIT 1",
                    params![h, customer],
                    |r| r.get(0),
                )
                .optional()?;
            if id.is_some() {
                return Ok(id);
            }
        }
    }
    if let Some(sub) = r["subsession_id"].as_i64().filter(|s| *s != 0) {
        let id: Option<i64> = conn
            .query_row(
                "SELECT session_id FROM sessions WHERE customer_id = ?1 AND subsession_id = ?2
                   AND (?3 IS NULL OR sim_session_num = ?3) AND (?4 IS NULL OR lower(car_path) = lower(?4))
                 ORDER BY session_id LIMIT 1",
                params![customer, sub, r["sim_session_num"].as_i64(), r["car_path"].as_str()],
                |r| r.get(0),
            )
            .optional()?;
        return Ok(id);
    }
    Ok(None)
}

fn setup_for_ref(conn: &Connection, customer: i64, r: &Value) -> rusqlite::Result<Option<i64>> {
    if let Some(sto) = r["sto_sha256"].as_str() {
        let id: Option<i64> = conn
            .query_row(
                "SELECT setup_id FROM setup_observations WHERE customer_id = ?1 AND sto_sha256 = ?2
                 ORDER BY observed_at DESC LIMIT 1",
                params![customer, sto],
                |r| r.get(0),
            )
            .optional()?;
        if id.is_some() {
            return Ok(id);
        }
    }
    if let Some(fp) = r["setup_values_fingerprint"].as_str() {
        return conn
            .query_row(
                "SELECT setup_id FROM setups WHERE customer_id = ?1 AND values_fingerprint = ?2 LIMIT 1",
                params![customer, fp],
                |r| r.get(0),
            )
            .optional();
    }
    Ok(None)
}

/// New subject id for a legacy note, or None when the reference does not resolve.
fn resolve(
    conn: &Connection,
    customer: i64,
    kind: &str,
    r: &Value,
) -> rusqlite::Result<Option<String>> {
    match kind {
        "session" => Ok(session_for_ref(conn, customer, r)?.map(|s| s.to_string())),
        "setup" => Ok(setup_for_ref(conn, customer, r)?.map(|s| s.to_string())),
        "analysis" => {
            if let Some(id) = r["legacy_analysis_id"].as_str() {
                let found: Option<String> = conn
                    .query_row(
                        "SELECT analysis_id FROM analyses WHERE analysis_id = ?1",
                        [id],
                        |r| r.get(0),
                    )
                    .optional()?;
                if found.is_some() {
                    return Ok(found);
                }
            }
            match session_for_ref(conn, customer, r)? {
                Some(session) => Ok(conn
                    .query_row(
                        "SELECT current_analysis_id FROM sessions WHERE session_id = ?1",
                        [session],
                        |r| r.get(0),
                    )
                    .optional()?
                    .flatten()),
                None => Ok(None),
            }
        }
        _ => Ok(None),
    }
}

fn unique_layout_id(
    conn: &Connection,
    table: &str,
    customer: i64,
    base: &str,
) -> rusqlite::Result<String> {
    let mut candidate = base.to_string();
    let mut n = 1;
    loop {
        let taken: bool = conn.query_row(
            &format!("SELECT COUNT(*) > 0 FROM {table} WHERE customer_id = ?1 AND layout_id = ?2"),
            params![customer, candidate],
            |r| r.get(0),
        )?;
        if !taken {
            return Ok(candidate);
        }
        n += 1;
        candidate = format!("{base}-{n}");
    }
}

/// A legacy archive file path the bundle may carry: `history.sqlite3` or a file under `tuning/`,
/// forward slashes, no empty, `.` or `..` segment, no drive or stream colon.
fn legacy_path_ok(path: &str) -> bool {
    let allowed = path == "history.sqlite3" || path.starts_with("tuning/");
    allowed
        && !path.contains(['\\', ':', '\0'])
        && path
            .split('/')
            .all(|segment| !segment.is_empty() && segment != "." && segment != "..")
}

/// The bundle's `legacy_archive` files, each decoded and checked against its SHA-256 and path.
fn legacy_files(bundle: &Value) -> std::result::Result<Vec<(String, Vec<u8>)>, String> {
    let mut files: Vec<(String, Vec<u8>)> = Vec::new();
    for file in bundle["legacy_archive"]["files"]
        .as_array()
        .map(Vec::as_slice)
        .unwrap_or_default()
    {
        let path = file["relative_path"].as_str().unwrap_or_default();
        if !legacy_path_ok(path) {
            return Err(format!("legacy_archive path {path:?} is not allowed"));
        }
        if files.iter().any(|(seen, _)| seen == path) {
            return Err(format!("legacy_archive names {path} twice"));
        }
        let bytes = util::base64_decode(file["content_base64"].as_str().unwrap_or_default())
            .ok_or_else(|| format!("legacy_archive file {path} is not valid base64"))?;
        let want = file["sha256"].as_str().unwrap_or_default();
        if util::sha256_hex(&bytes) != want {
            return Err(format!(
                "legacy_archive file {path} does not match its sha256"
            ));
        }
        files.push((path.to_string(), bytes));
    }
    Ok(files)
}

/// Every legacy analysis the bundle names, resolved to the matching session's current analysis
/// (null id and path when unresolved), as `import_legacy_tuning_experiments` expects.
fn analysis_map(conn: &Connection, customer: i64, bundle: &Value) -> rusqlite::Result<Vec<Value>> {
    let mut map = Vec::new();
    for entry in bundle["legacy_analyses"]
        .as_array()
        .cloned()
        .unwrap_or_default()
    {
        let legacy = entry["legacy_analysis_id"].as_str().unwrap_or_default();
        let current: Option<(String, String)> = match session_for_ref(conn, customer, &entry)? {
            Some(session) => conn
                .query_row(
                    "SELECT a.analysis_id, a.analysis_path FROM sessions s
                     JOIN analyses a ON a.analysis_id = s.current_analysis_id WHERE s.session_id = ?1",
                    [session],
                    |r| Ok((r.get(0)?, r.get(1)?)),
                )
                .optional()?,
            None => None,
        };
        map.push(json!({
            "legacy_analysis_id": legacy,
            "analysis_id": current.as_ref().map(|c| c.0.clone()),
            "analysis_path": current.map(|c| c.1),
        }));
    }
    Ok(map)
}

/// Writes the legacy files under the job root and runs the worker's import; the counts for the
/// summary, or the failed outcome.
async fn import_tuning_experiments(
    app: &AppState,
    job: &JobRow,
    cancel: &Cancel,
    customer: i64,
    bundle: &Value,
    map: &[Value],
) -> Result<std::result::Result<Value, JobOutcome>> {
    let files = match legacy_files(bundle) {
        Ok(files) => files,
        Err(message) => return Ok(Err(JobOutcome::failed("invalid_bundle", message))),
    };
    let mapped = map.iter().filter(|m| !m["analysis_id"].is_null()).count();
    let mut counts = json!({
        "legacy_analyses": map.len(),
        "legacy_analyses_resolved": mapped,
        "legacy_analyses_unresolved": map.len() - mapped,
        "tuning_experiments_imported": 0,
        "tuning_experiments_resolved": 0,
        "tuning_experiments_unresolved": 0,
    });
    if files.is_empty() {
        return Ok(Ok(counts));
    }
    let (root, _) = crate::handlers::prepare_root(app, job, customer, &[], false).await?;
    let legacy = root.join("legacy");
    let written = (|| -> std::io::Result<()> {
        for (path, bytes) in &files {
            let dest = legacy.join(path);
            if let Some(parent) = dest.parent() {
                std::fs::create_dir_all(parent)?;
            }
            std::fs::write(dest, bytes)?;
        }
        Ok(())
    })();
    if let Err(e) = written {
        crate::handlers::cleanup_root(&root);
        return Err(e.into());
    }
    let archive = app.cfg.py_archive(customer);
    let result = crate::handlers::call_worker(
        app,
        job,
        "import_legacy_tuning_experiments",
        json!({
            "legacy_archive_root": legacy.to_string_lossy(),
            "analysis_map": map,
            "archive_root": archive.to_string_lossy(),
        }),
        &root,
        app.cfg.worker.light_timeout,
        cancel,
    )
    .await;
    crate::handlers::cleanup_root(&root);
    match result {
        Ok(result) => {
            counts["tuning_experiments_imported"] = result["experiment_count"].clone();
            counts["tuning_experiments_resolved"] = result["resolved_count"].clone();
            counts["tuning_experiments_unresolved"] = result["unresolved_count"].clone();
            Ok(Ok(counts))
        }
        Err(e) => Ok(Err(crate::handlers::worker_failure(e))),
    }
}

/// One bundle draft: its new key and rewritten body when its legacy analysis resolves to a
/// session's current analysis with a tuning context, else None (the draft is parked).
struct DraftPlan {
    item: Value,
    new_key: Option<(String, Value)>,
}

/// Re-keys each draft as `GET .../tuning` would for the resolved analysis; the target of an open
/// setup is rewritten through the same map (null when unresolved).
async fn plan_drafts(app: &AppState, bundle: &Value, map: &[Value]) -> Result<Vec<DraftPlan>> {
    let resolve_id = |legacy: &str| -> Option<String> {
        map.iter()
            .find(|m| m["legacy_analysis_id"].as_str() == Some(legacy))
            .and_then(|m| m["analysis_id"].as_str())
            .map(str::to_owned)
    };
    let mut plans = Vec::new();
    for item in bundle["tuning_drafts"]
        .as_array()
        .cloned()
        .unwrap_or_default()
    {
        let mut new_key = None;
        if let Some(analysis) = item["legacy_analysis_id"].as_str().and_then(resolve_id)
            && let Some(key) = crate::tuning::draft_key_for_analysis(app, &analysis).await?
        {
            let mut body = item["body"].clone();
            if let Some(target) = body.get("open_target_analysis_id").and_then(Value::as_str) {
                body["open_target_analysis_id"] = json!(resolve_id(target));
            }
            new_key = Some((key, body));
        }
        plans.push(DraftPlan { item, new_key });
    }
    Ok(plans)
}

pub async fn run(app: &AppState, job: &JobRow, cancel: &Cancel) -> Result<JobOutcome> {
    let sha = job.subject["bundle_sha256"]
        .as_str()
        .unwrap_or_default()
        .to_string();
    let path = bundle_path(app, &sha);
    let bundle: Value = serde_json::from_slice(&std::fs::read(&path)?)
        .map_err(|e| Error::internal(format!("stored bundle is not JSON: {e}")))?;
    let customer = bundle["customer_id"].as_i64().unwrap_or_default();
    let already: bool = {
        let sha = sha.clone();
        app.store
            .read(move |conn| {
                Ok(conn.query_row(
                    "SELECT COUNT(*) > 0 FROM imports WHERE bundle_sha256 = ?1",
                    [sha],
                    |r| r.get(0),
                )?)
            })
            .await?
    };
    if already {
        return Ok(JobOutcome::Succeeded(json!({"already_imported": true})));
    }
    let map = {
        let bundle = bundle.clone();
        app.store
            .read(move |conn| Ok(analysis_map(conn, customer, &bundle)?))
            .await?
    };
    // The worker step is idempotent, so a retry after a failed transaction repeats it safely.
    let experiments =
        match import_tuning_experiments(app, job, cancel, customer, &bundle, &map).await? {
            Ok(counts) => counts,
            Err(outcome) => return Ok(outcome),
        };
    let draft_plans = plan_drafts(app, &bundle, &map).await?;
    let job_id = job.job_id;
    let summary = app
        .store
        .write(move |conn| {
            let tx = conn.transaction()?;
            let already: bool = tx.query_row(
                "SELECT COUNT(*) > 0 FROM imports WHERE bundle_sha256 = ?1",
                [&sha],
                |r| r.get(0),
            )?;
            if already {
                return Ok(json!({"already_imported": true}));
            }
            let customer = bundle["customer_id"].as_i64().unwrap_or_default();
            crate::db::upsert_driver(&tx, customer, None, None)?;
            let now = iso_now();
            tx.execute(
                "INSERT INTO imports(bundle_sha256, imported_at, summary_json) VALUES (?1, ?2, '{}')",
                params![sha, now],
            )?;
            let import_id = tx.last_insert_rowid();
            let (mut notes_attached, mut notes_unresolved) = (0, 0);
            let mut unresolved: Vec<Value> = Vec::new();
            for note in bundle["notes"].as_array().cloned().unwrap_or_default() {
                let legacy = note["legacy_id"].as_str().unwrap_or_default().to_string();
                let source = format!("import:{legacy}");
                let exists: bool = tx.query_row(
                    "SELECT COUNT(*) > 0 FROM notes WHERE legacy_source = ?1 AND customer_id = ?2",
                    params![source, customer],
                    |r| r.get(0),
                )?;
                if exists {
                    continue;
                }
                let kind = note["subject_kind"].as_str().unwrap_or("session");
                match resolve(&tx, customer, kind, &note["subject_ref"])? {
                    Some(subject_id) => {
                        tx.execute(
                            "INSERT INTO notes(customer_id, subject_kind, subject_id, body, version, created_at, updated_at, legacy_source)
                             VALUES (?1, ?2, ?3, ?4, 1, ?5, ?5, ?6)",
                            params![customer, kind, subject_id, note["body"].as_str().unwrap_or_default(), now, source],
                        )?;
                        notes_attached += 1;
                    }
                    None => {
                        tx.execute(
                            "INSERT INTO legacy_items(customer_id, kind, source_path, body_json, imported_at, import_id)
                             VALUES (?1, 'note', ?2, ?3, ?4, ?5)",
                            params![customer, format!("notes/{legacy}"), note.to_string(), now, import_id],
                        )?;
                        notes_unresolved += 1;
                        unresolved.push(json!({"legacy_id": legacy, "subject_kind": kind}));
                    }
                }
            }
            let mut layouts = 0;
            for (table, key) in [("trace_layouts", "trace_layouts"), ("live_layouts", "live_layouts")] {
                for layout in bundle[key].as_array().cloned().unwrap_or_default() {
                    let name = layout["name"].as_str().unwrap_or("Imported").to_string();
                    let id = unique_layout_id(&tx, table, customer, &slug(&name))?;
                    tx.execute(
                        &format!(
                            "INSERT INTO {table}(customer_id, layout_id, name, body_json, version, updated_at)
                             VALUES (?1, ?2, ?3, ?4, 1, ?5)"
                        ),
                        params![customer, id, name, layout["body"].to_string(), now],
                    )?;
                    layouts += 1;
                }
            }
            if bundle["prefs"].is_object() {
                let prefs = &bundle["prefs"];
                let mut rest = prefs.clone();
                if let Some(map) = rest.as_object_mut() {
                    map.remove("active_live_layout_id");
                    map.remove("last_investigation");
                }
                tx.execute(
                    "INSERT OR IGNORE INTO driver_prefs(customer_id, active_live_layout_id, last_investigation_json,
                       prefs_json, version, updated_at) VALUES (?1, ?2, ?3, ?4, 1, ?5)",
                    params![
                        customer,
                        prefs["active_live_layout_id"].as_str(),
                        prefs["last_investigation"].as_object().map(|_| prefs["last_investigation"].to_string()),
                        rest.to_string(),
                        now
                    ],
                )?;
            }
            let (mut drafts, mut drafts_unresolved) = (0, 0);
            for plan in &draft_plans {
                let stored = match &plan.new_key {
                    Some((key, body)) => tx.execute(
                        "INSERT OR IGNORE INTO tuning_drafts(customer_id, identity_key, body_json, version, updated_at)
                         VALUES (?1, ?2, ?3, 1, ?4)",
                        params![customer, key, body.to_string(), now],
                    )?,
                    None => 0,
                };
                if stored == 1 {
                    drafts += 1;
                    continue;
                }
                tx.execute(
                    "INSERT INTO legacy_items(customer_id, kind, source_path, body_json, imported_at, import_id)
                     VALUES (?1, 'tuning_draft', ?2, ?3, ?4, ?5)",
                    params![
                        customer,
                        format!(
                            "tuning-drafts/{}",
                            plan.item["identity_key"].as_str().unwrap_or_default()
                        ),
                        plan.item.to_string(),
                        now,
                        import_id
                    ],
                )?;
                drafts_unresolved += 1;
            }
            let mut annotations = 0;
            for a in bundle["turn_annotations"].as_array().cloned().unwrap_or_default() {
                annotations += tx.execute(
                    "INSERT OR IGNORE INTO turn_annotations(customer_id, track_config_key, map_identity, body_json, version, updated_at)
                     VALUES (?1, ?2, ?3, ?4, 1, ?5)",
                    params![
                        customer,
                        a["track_config_key"].as_str().unwrap_or_default(),
                        a["map_identity"].as_str().unwrap_or_default(),
                        a["body"].to_string(),
                        now
                    ],
                )?;
            }
            let mut parked = 0;
            for item in bundle["legacy_items"].as_array().cloned().unwrap_or_default() {
                tx.execute(
                    "INSERT INTO legacy_items(customer_id, kind, source_path, body_json, imported_at, import_id)
                     VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
                    params![
                        customer,
                        item["kind"].as_str().unwrap_or("other"),
                        item["source_path"].as_str().unwrap_or_default(),
                        item["body"].to_string(),
                        now,
                        import_id
                    ],
                )?;
                parked += 1;
            }
            let mut summary = json!({
                "job_id": job_id,
                "notes_attached": notes_attached,
                "notes_unresolved": notes_unresolved,
                "unresolved_notes": unresolved,
                "layouts": layouts,
                "tuning_drafts": drafts,
                "tuning_drafts_unresolved": drafts_unresolved,
                "turn_annotations": annotations,
                "legacy_items": parked,
            });
            if let (Some(out), Some(counts)) = (summary.as_object_mut(), experiments.as_object()) {
                out.extend(counts.iter().map(|(k, v)| (k.clone(), v.clone())));
            }
            tx.execute(
                "UPDATE imports SET summary_json = ?2 WHERE import_id = ?1",
                params![import_id, summary.to_string()],
            )?;
            crate::db::bump_generation(&tx)?;
            tx.commit()?;
            Ok(summary)
        })
        .await?;
    Ok(JobOutcome::Succeeded(summary))
}

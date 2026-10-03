//! D12: one-time conversion of legacy user-authored data. The bundle is stored by hash, the job
//! reads it back, resolves every reference against the re-ingested library and attaches notes,
//! drafts and annotations; what cannot be resolved is kept verbatim as `legacy_items`.

use rusqlite::{Connection, OptionalExtension, params};
use serde_json::{Value, json};

use crate::app::AppState;
use crate::error::{Error, Result};
use crate::jobs::{JobOutcome, JobRow};
use crate::util::iso_now;

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

pub async fn run(app: &AppState, job: &JobRow) -> Result<JobOutcome> {
    let sha = job.subject["bundle_sha256"]
        .as_str()
        .unwrap_or_default()
        .to_string();
    let path = bundle_path(app, &sha);
    let bundle: Value = serde_json::from_slice(&std::fs::read(&path)?)
        .map_err(|e| Error::internal(format!("stored bundle is not JSON: {e}")))?;
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
            let mut drafts = 0;
            for draft in bundle["tuning_drafts"].as_array().cloned().unwrap_or_default() {
                drafts += tx.execute(
                    "INSERT OR IGNORE INTO tuning_drafts(customer_id, identity_key, body_json, version, updated_at)
                     VALUES (?1, ?2, ?3, 1, ?4)",
                    params![customer, draft["identity_key"].as_str().unwrap_or_default(), draft["body"].to_string(), now],
                )?;
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
            for exp in bundle["tuning_experiments"].as_array().cloned().unwrap_or_default() {
                tx.execute(
                    "INSERT INTO legacy_items(customer_id, kind, source_path, body_json, imported_at, import_id)
                     VALUES (?1, 'tuning_experiment', 'history.sqlite3/tuning_experiments', ?2, ?3, ?4)",
                    params![customer, exp.to_string(), now, import_id],
                )?;
                parked += 1;
            }
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
            let summary = json!({
                "job_id": job_id,
                "notes_attached": notes_attached,
                "notes_unresolved": notes_unresolved,
                "unresolved_notes": unresolved,
                "layouts": layouts,
                "tuning_drafts": drafts,
                "turn_annotations": annotations,
                "legacy_items": parked,
                "tuning_experiments_awaiting_worker_import": bundle["tuning_experiments"].as_array().map(|a| a.len()).unwrap_or(0),
            });
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

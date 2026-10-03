//! User-authored resources: notes, trace and live layouts, preferences, tuning drafts and turn
//! annotations. Writes carry `If-Match` versions, bump `user_data_generation` in the same
//! transaction and announce `library_changed`.

use axum::body::Bytes;
use axum::http::{HeaderMap, StatusCode};
use rusqlite::{Connection, OptionalExtension, params};
use serde_json::{Value, json};

use crate::app::AppState;
use crate::db::bump_generation;
use crate::error::{ApiError, Error, Result};
use crate::hub::{default_live_layout, default_trace_layout};
use crate::schema::parse_request;
use crate::util::iso_now;

pub const DEFAULT_LIVE_LAYOUT_ID: &str = "factory-default";
pub const DEFAULT_TRACE_LAYOUT_ID: &str = "default";

/// Parsed `If-Match` header: the resource version the client last saw.
pub fn if_match(headers: &HeaderMap) -> Option<i64> {
    headers
        .get("if-match")
        .and_then(|v| v.to_str().ok())
        .map(|v| v.trim().trim_matches('"'))
        .and_then(|v| v.parse::<i64>().ok())
}

pub fn version_conflict(current: Value) -> Error {
    let mut api = ApiError::new(
        StatusCode::PRECONDITION_FAILED,
        "version_conflict",
        "the resource changed; reload and retry",
    );
    api.current = Some(current);
    Error::Api(api)
}

/// Checks `If-Match` against the stored version. `None` stored means the resource is new.
fn check_version(
    stored: Option<i64>,
    given: Option<i64>,
    current: impl FnOnce() -> Value,
) -> Result<()> {
    match (stored, given) {
        (None, None) | (None, Some(0)) => Ok(()),
        (Some(s), Some(g)) if s == g => Ok(()),
        (None, Some(_)) => Err(version_conflict(json!({}))),
        (Some(_), _) => Err(version_conflict(current())),
    }
}

fn require_driver(conn: &Connection, customer: i64) -> Result<()> {
    let exists: bool = conn.query_row(
        "SELECT COUNT(*) > 0 FROM drivers WHERE customer_id = ?1",
        [customer],
        |r| r.get(0),
    )?;
    if exists {
        Ok(())
    } else {
        Err(Error::not_found("no such driver"))
    }
}

// ------------------------------------------------------------------- notes

fn note_json(r: &rusqlite::Row<'_>) -> rusqlite::Result<Value> {
    Ok(json!({
        "note_id": r.get::<_, i64>(0)?,
        "customer_id": r.get::<_, i64>(1)?,
        "subject_kind": r.get::<_, String>(2)?,
        "subject_id": r.get::<_, String>(3)?,
        "body": r.get::<_, String>(4)?,
        "version": r.get::<_, i64>(5)?,
        "created_at": r.get::<_, String>(6)?,
        "updated_at": r.get::<_, String>(7)?,
    }))
}

const NOTE_COLUMNS: &str =
    "note_id, customer_id, subject_kind, subject_id, body, version, created_at, updated_at";

pub async fn list_notes(
    app: &AppState,
    customer: i64,
    kind: String,
    subject: String,
) -> Result<Value> {
    app.store
        .read(move |conn| {
            let mut stmt = conn.prepare(&format!(
                "SELECT {NOTE_COLUMNS} FROM notes WHERE customer_id = ?1 AND subject_kind = ?2 AND subject_id = ?3
                 ORDER BY created_at DESC, note_id DESC LIMIT 64"
            ))?;
            let notes = stmt
                .query_map(params![customer, kind, subject], note_json)?
                .collect::<rusqlite::Result<Vec<_>>>()?;
            Ok(json!({"notes": notes}))
        })
        .await
}

pub async fn create_note(app: &AppState, customer: i64, body: &Bytes) -> Result<Value> {
    let value = parse_request("NoteCreate", body)?;
    let (kind, subject, text) = (
        value["subject_kind"]
            .as_str()
            .unwrap_or_default()
            .to_string(),
        value["subject_id"].as_str().unwrap_or_default().to_string(),
        value["body"].as_str().unwrap_or_default().to_string(),
    );
    let note = app
        .store
        .write(move |conn| {
            let tx = conn.transaction()?;
            require_driver(&tx, customer)?;
            let now = iso_now();
            tx.execute(
                "INSERT INTO notes(customer_id, subject_kind, subject_id, body, version, created_at, updated_at)
                 VALUES (?1, ?2, ?3, ?4, 1, ?5, ?5)",
                params![customer, kind, subject, text, now],
            )?;
            let id = tx.last_insert_rowid();
            bump_generation(&tx)?;
            let note = tx.query_row(
                &format!("SELECT {NOTE_COLUMNS} FROM notes WHERE note_id = ?1"),
                [id],
                note_json,
            )?;
            tx.commit()?;
            Ok(note)
        })
        .await?;
    app.events.library_changed(customer, "notes");
    Ok(note)
}

pub async fn update_note(
    app: &AppState,
    note_id: i64,
    given: Option<i64>,
    body: &Bytes,
) -> Result<Value> {
    let value = parse_request("NoteUpdate", body)?;
    let text = value["body"].as_str().unwrap_or_default().to_string();
    let (customer, note) = app
        .store
        .write(move |conn| {
            let tx = conn.transaction()?;
            let current: Option<Value> = tx
                .query_row(
                    &format!("SELECT {NOTE_COLUMNS} FROM notes WHERE note_id = ?1"),
                    [note_id],
                    note_json,
                )
                .optional()?;
            let Some(current) = current else {
                return Err(Error::not_found("no such note"));
            };
            check_version(current["version"].as_i64(), given, || current.clone())?;
            tx.execute(
                "UPDATE notes SET body = ?2, version = version + 1, updated_at = ?3 WHERE note_id = ?1",
                params![note_id, text, iso_now()],
            )?;
            bump_generation(&tx)?;
            let note = tx.query_row(
                &format!("SELECT {NOTE_COLUMNS} FROM notes WHERE note_id = ?1"),
                [note_id],
                note_json,
            )?;
            tx.commit()?;
            Ok((current["customer_id"].as_i64().unwrap_or(0), note))
        })
        .await?;
    app.events.library_changed(customer, "notes");
    Ok(note)
}

pub async fn delete_note(app: &AppState, note_id: i64, given: Option<i64>) -> Result<()> {
    let customer = app
        .store
        .write(move |conn| {
            let tx = conn.transaction()?;
            let current: Option<Value> = tx
                .query_row(
                    &format!("SELECT {NOTE_COLUMNS} FROM notes WHERE note_id = ?1"),
                    [note_id],
                    note_json,
                )
                .optional()?;
            let Some(current) = current else {
                return Err(Error::not_found("no such note"));
            };
            check_version(current["version"].as_i64(), given, || current.clone())?;
            tx.execute("DELETE FROM notes WHERE note_id = ?1", [note_id])?;
            bump_generation(&tx)?;
            tx.commit()?;
            Ok(current["customer_id"].as_i64().unwrap_or(0))
        })
        .await?;
    app.events.library_changed(customer, "notes");
    Ok(())
}

// ----------------------------------------------------------------- layouts

#[derive(Clone, Copy)]
pub enum LayoutKind {
    Trace,
    Live,
}

impl LayoutKind {
    fn table(self) -> &'static str {
        match self {
            LayoutKind::Trace => "trace_layouts",
            LayoutKind::Live => "live_layouts",
        }
    }
    fn write_schema(self) -> &'static str {
        match self {
            LayoutKind::Trace => "TraceLayoutWrite",
            LayoutKind::Live => "LiveLayoutWrite",
        }
    }
    fn builtin_id(self) -> &'static str {
        match self {
            LayoutKind::Trace => DEFAULT_TRACE_LAYOUT_ID,
            LayoutKind::Live => DEFAULT_LIVE_LAYOUT_ID,
        }
    }
    fn builtin(self) -> Value {
        match self {
            LayoutKind::Trace => {
                json!({"layout_id": DEFAULT_TRACE_LAYOUT_ID, "name": "Default", "body": default_trace_layout(), "version": 1, "builtin": true})
            }
            LayoutKind::Live => {
                json!({"layout_id": DEFAULT_LIVE_LAYOUT_ID, "name": "Default", "body": default_live_layout(), "version": 1, "builtin": true})
            }
        }
    }
}

fn layout_row(r: &rusqlite::Row<'_>) -> rusqlite::Result<Value> {
    let body: String = r.get(2)?;
    Ok(json!({
        "layout_id": r.get::<_, String>(0)?,
        "name": r.get::<_, String>(1)?,
        "body": serde_json::from_str::<Value>(&body).unwrap_or(json!({})),
        "version": r.get::<_, i64>(3)?,
        "builtin": false,
    }))
}

fn load_layout(
    conn: &Connection,
    kind: LayoutKind,
    customer: i64,
    id: &str,
) -> rusqlite::Result<Option<Value>> {
    conn.query_row(
        &format!(
            "SELECT layout_id, name, body_json, version FROM {} WHERE customer_id = ?1 AND layout_id = ?2",
            kind.table()
        ),
        params![customer, id],
        layout_row,
    )
    .optional()
}

fn active_live_id(conn: &Connection, customer: i64) -> rusqlite::Result<String> {
    Ok(conn
        .query_row(
            "SELECT active_live_layout_id FROM driver_prefs WHERE customer_id = ?1",
            [customer],
            |r| r.get::<_, Option<String>>(0),
        )
        .optional()?
        .flatten()
        .unwrap_or_else(|| DEFAULT_LIVE_LAYOUT_ID.to_string()))
}

pub async fn list_layouts(app: &AppState, kind: LayoutKind, customer: i64) -> Result<Value> {
    app.store
        .read(move |conn| {
            require_driver(conn, customer)?;
            let mut stmt = conn.prepare(&format!(
                "SELECT layout_id, name, body_json, version FROM {} WHERE customer_id = ?1
                 ORDER BY name COLLATE NOCASE, layout_id LIMIT 63",
                kind.table()
            ))?;
            let mut layouts = vec![kind.builtin()];
            layouts.extend(
                stmt.query_map([customer], layout_row)?
                    .collect::<rusqlite::Result<Vec<_>>>()?,
            );
            Ok(match kind {
                LayoutKind::Trace => json!({"layouts": layouts}),
                LayoutKind::Live => {
                    let active = active_live_id(conn, customer)?;
                    let active = if layouts.iter().any(|l| l["layout_id"] == active.as_str()) {
                        active
                    } else {
                        DEFAULT_LIVE_LAYOUT_ID.to_string()
                    };
                    json!({"layouts": layouts, "active_layout_id": active})
                }
            })
        })
        .await
}

pub async fn put_layout(
    app: &AppState,
    kind: LayoutKind,
    customer: i64,
    id: String,
    given: Option<i64>,
    body: &Bytes,
) -> Result<Value> {
    if id == kind.builtin_id() {
        return Err(Error::unprocessable(
            "builtin_immutable",
            "the built-in layout cannot be changed",
        ));
    }
    let value = parse_request(kind.write_schema(), body)?;
    let name = value["name"].as_str().unwrap_or_default().to_string();
    let body_json = value["body"].to_string();
    if body_json.len() > 65_536 {
        return Err(Error::Api(ApiError::new(
            StatusCode::PAYLOAD_TOO_LARGE,
            "too_large",
            "layout body exceeds 64 KiB",
        )));
    }
    let id2 = id.clone();
    let (layout, active) = app
        .store
        .write(move |conn| {
            let tx = conn.transaction()?;
            require_driver(&tx, customer)?;
            let current = load_layout(&tx, kind, customer, &id2)?;
            check_version(current.as_ref().and_then(|c| c["version"].as_i64()), given, || {
                current.clone().unwrap_or(json!({}))
            })?;
            let now = iso_now();
            match &current {
                None => {
                    let count: i64 = tx.query_row(
                        &format!("SELECT COUNT(*) FROM {} WHERE customer_id = ?1", kind.table()),
                        [customer],
                        |r| r.get(0),
                    )?;
                    if count >= 63 {
                        return Err(Error::unprocessable("too_many_layouts", "at most 63 saved layouts"));
                    }
                    tx.execute(
                        &format!(
                            "INSERT INTO {}(customer_id, layout_id, name, body_json, version, updated_at)
                             VALUES (?1, ?2, ?3, ?4, 1, ?5)",
                            kind.table()
                        ),
                        params![customer, id2, name, body_json, now],
                    )?;
                }
                Some(_) => {
                    tx.execute(
                        &format!(
                            "UPDATE {} SET name = ?3, body_json = ?4, version = version + 1, updated_at = ?5
                             WHERE customer_id = ?1 AND layout_id = ?2",
                            kind.table()
                        ),
                        params![customer, id2, name, body_json, now],
                    )?;
                }
            }
            bump_generation(&tx)?;
            let layout = load_layout(&tx, kind, customer, &id2)?.expect("just written");
            let active = matches!(kind, LayoutKind::Live) && active_live_id(&tx, customer)? == id2;
            tx.commit()?;
            Ok((layout, active))
        })
        .await?;
    app.events.library_changed(customer, "layouts");
    if active {
        push_active_layout(app, customer).await;
    }
    Ok(layout)
}

pub async fn delete_layout(
    app: &AppState,
    kind: LayoutKind,
    customer: i64,
    id: String,
    given: Option<i64>,
) -> Result<()> {
    if id == kind.builtin_id() {
        return Err(Error::unprocessable(
            "builtin_immutable",
            "the built-in layout cannot be deleted",
        ));
    }
    let was_active = app
        .store
        .write(move |conn| {
            let tx = conn.transaction()?;
            let current = load_layout(&tx, kind, customer, &id)?;
            let Some(current) = current else {
                return Err(Error::not_found("no such layout"));
            };
            check_version(current["version"].as_i64(), given, || current.clone())?;
            tx.execute(
                &format!("DELETE FROM {} WHERE customer_id = ?1 AND layout_id = ?2", kind.table()),
                params![customer, id],
            )?;
            let mut was_active = false;
            if matches!(kind, LayoutKind::Live) && active_live_id(&tx, customer)? == id {
                tx.execute(
                    "UPDATE driver_prefs SET active_live_layout_id = NULL, version = version + 1, updated_at = ?2
                     WHERE customer_id = ?1",
                    params![customer, iso_now()],
                )?;
                was_active = true;
            }
            bump_generation(&tx)?;
            tx.commit()?;
            Ok(was_active)
        })
        .await?;
    app.events.library_changed(customer, "layouts");
    if was_active {
        push_active_layout(app, customer).await;
    }
    Ok(())
}

/// `layout.set` for the driver's active live layout.
pub async fn active_layout_message(app: &AppState, customer: i64) -> Result<Value> {
    app.store
        .read(move |conn| {
            let active = active_live_id(conn, customer)?;
            let (id, version, body) = match load_layout(conn, LayoutKind::Live, customer, &active)?
            {
                Some(l) => (
                    active,
                    l["version"].as_i64().unwrap_or(1),
                    l["body"].clone(),
                ),
                None => (DEFAULT_LIVE_LAYOUT_ID.to_string(), 1, default_live_layout()),
            };
            Ok(json!({"type": "layout.set", "layout_id": id, "version": version, "layout": body}))
        })
        .await
}

/// Pushes the active layout to every collector reporting this driver.
pub async fn push_active_layout(app: &AppState, customer: i64) {
    let Ok(message) = active_layout_message(app, customer).await else {
        return;
    };
    for collector in app.hub.collectors_for(customer) {
        app.hub.send(&collector, message.clone());
    }
}

// ------------------------------------------------------------------- prefs

fn prefs_value(conn: &Connection, customer: i64) -> rusqlite::Result<Value> {
    let row: Option<(Option<String>, Option<String>, String, i64)> = conn
        .query_row(
            "SELECT active_live_layout_id, last_investigation_json, prefs_json, version FROM driver_prefs WHERE customer_id = ?1",
            [customer],
            |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?)),
        )
        .optional()?;
    let Some((active, last, prefs, version)) = row else {
        return Ok(json!({"customer_id": customer, "version": 0}));
    };
    let mut out: Value = serde_json::from_str(&prefs).unwrap_or(json!({}));
    if !out.is_object() {
        out = json!({});
    }
    out["customer_id"] = json!(customer);
    out["version"] = json!(version);
    out["active_live_layout_id"] = json!(active);
    if let Some(last) = last.and_then(|t| serde_json::from_str::<Value>(&t).ok()) {
        out["last_investigation"] = last;
    }
    Ok(out)
}

pub async fn get_prefs(app: &AppState, customer: i64) -> Result<Value> {
    app.store
        .read(move |conn| {
            require_driver(conn, customer)?;
            Ok(prefs_value(conn, customer)?)
        })
        .await
}

pub async fn put_prefs(
    app: &AppState,
    customer: i64,
    given: Option<i64>,
    body: &Bytes,
) -> Result<Value> {
    let value = parse_request("DriverPrefsWrite", body)?;
    let (prefs, layout_changed) = app
        .store
        .write(move |conn| {
            let tx = conn.transaction()?;
            require_driver(&tx, customer)?;
            let current = prefs_value(&tx, customer)?;
            let stored = current["version"].as_i64().filter(|v| *v > 0);
            check_version(stored, given, || current.clone())?;
            let mut rest = current.clone();
            if let (Some(map), Some(incoming)) = (rest.as_object_mut(), value.as_object()) {
                for k in ["customer_id", "version", "active_live_layout_id", "last_investigation"] {
                    map.remove(k);
                }
                for (k, v) in incoming {
                    if !matches!(k.as_str(), "active_live_layout_id" | "last_investigation") {
                        map.insert(k.clone(), v.clone());
                    }
                }
            }
            let active = if value.get("active_live_layout_id").is_some() {
                value["active_live_layout_id"].as_str().map(str::to_string)
            } else {
                current["active_live_layout_id"].as_str().map(str::to_string)
            };
            let last = if value.get("last_investigation").is_some() {
                value["last_investigation"].as_object().map(|_| value["last_investigation"].to_string())
            } else {
                current.get("last_investigation").map(|v| v.to_string())
            };
            let layout_changed = value.get("active_live_layout_id").is_some()
                && current["active_live_layout_id"] != value["active_live_layout_id"];
            tx.execute(
                "INSERT INTO driver_prefs(customer_id, active_live_layout_id, last_investigation_json, prefs_json, version, updated_at)
                 VALUES (?1, ?2, ?3, ?4, 1, ?5)
                 ON CONFLICT(customer_id) DO UPDATE SET active_live_layout_id = ?2, last_investigation_json = ?3,
                   prefs_json = ?4, version = version + 1, updated_at = ?5",
                params![customer, active, last, rest.to_string(), iso_now()],
            )?;
            bump_generation(&tx)?;
            let out = prefs_value(&tx, customer)?;
            tx.commit()?;
            Ok((out, layout_changed))
        })
        .await?;
    app.events.library_changed(customer, "prefs");
    if layout_changed {
        push_active_layout(app, customer).await;
    }
    Ok(prefs)
}

// ------------------------------------------------- drafts and annotations

fn draft_value(r: &rusqlite::Row<'_>) -> rusqlite::Result<Value> {
    let body: String = r.get(1)?;
    Ok(json!({
        "identity_key": r.get::<_, String>(0)?,
        "body": serde_json::from_str::<Value>(&body).unwrap_or(json!({})),
        "version": r.get::<_, i64>(2)?,
        "updated_at": r.get::<_, String>(3)?,
    }))
}

pub async fn get_draft(app: &AppState, customer: i64, key: String) -> Result<Value> {
    app.store
        .read(move |conn| {
            conn.query_row(
                "SELECT identity_key, body_json, version, updated_at FROM tuning_drafts WHERE customer_id = ?1 AND identity_key = ?2",
                params![customer, key],
                draft_value,
            )
            .optional()?
            .ok_or_else(|| Error::not_found("no draft for this identity"))
        })
        .await
}

pub async fn put_draft(
    app: &AppState,
    customer: i64,
    key: String,
    given: Option<i64>,
    body: &Bytes,
) -> Result<Value> {
    let value = parse_request("TuningDraftWrite", body)?;
    let body_json = value["body"].to_string();
    if body_json.len() > 262_144 {
        return Err(Error::Api(ApiError::new(
            StatusCode::PAYLOAD_TOO_LARGE,
            "too_large",
            "draft exceeds 256 KiB",
        )));
    }
    let out = app
        .store
        .write(move |conn| {
            let tx = conn.transaction()?;
            require_driver(&tx, customer)?;
            let current: Option<Value> = tx
                .query_row(
                    "SELECT identity_key, body_json, version, updated_at FROM tuning_drafts WHERE customer_id = ?1 AND identity_key = ?2",
                    params![customer, key],
                    draft_value,
                )
                .optional()?;
            check_version(current.as_ref().and_then(|c| c["version"].as_i64()), given, || {
                current.clone().unwrap_or(json!({}))
            })?;
            tx.execute(
                "INSERT INTO tuning_drafts(customer_id, identity_key, body_json, version, updated_at) VALUES (?1, ?2, ?3, 1, ?4)
                 ON CONFLICT(customer_id, identity_key) DO UPDATE SET body_json = ?3, version = version + 1, updated_at = ?4",
                params![customer, key, body_json, iso_now()],
            )?;
            bump_generation(&tx)?;
            let out = tx.query_row(
                "SELECT identity_key, body_json, version, updated_at FROM tuning_drafts WHERE customer_id = ?1 AND identity_key = ?2",
                params![customer, key],
                draft_value,
            )?;
            tx.commit()?;
            Ok(out)
        })
        .await?;
    app.events.library_changed(customer, "notes");
    Ok(out)
}

fn annotation_value(r: &rusqlite::Row<'_>) -> rusqlite::Result<Value> {
    let body: String = r.get(2)?;
    Ok(json!({
        "track_config_key": r.get::<_, String>(0)?,
        "map_identity": r.get::<_, String>(1)?,
        "body": serde_json::from_str::<Value>(&body).unwrap_or(json!({})),
        "version": r.get::<_, i64>(3)?,
        "updated_at": r.get::<_, String>(4)?,
    }))
}

pub async fn get_annotations(
    app: &AppState,
    customer: i64,
    track: String,
    map: String,
) -> Result<Value> {
    app.store
        .read(move |conn| {
            conn.query_row(
                "SELECT track_config_key, map_identity, body_json, version, updated_at FROM turn_annotations
                 WHERE customer_id = ?1 AND track_config_key = ?2 AND map_identity = ?3",
                params![customer, track, map],
                annotation_value,
            )
            .optional()?
            .ok_or_else(|| Error::not_found("no annotations for this map"))
        })
        .await
}

pub async fn put_annotations(
    app: &AppState,
    customer: i64,
    track: String,
    map: String,
    given: Option<i64>,
    body: &Bytes,
) -> Result<Value> {
    let value = parse_request("TurnAnnotationsWrite", body)?;
    let body_json = value["body"].to_string();
    if body_json.len() > 65_536 {
        return Err(Error::Api(ApiError::new(
            StatusCode::PAYLOAD_TOO_LARGE,
            "too_large",
            "annotations exceed 64 KiB",
        )));
    }
    let out = app
        .store
        .write(move |conn| {
            let tx = conn.transaction()?;
            require_driver(&tx, customer)?;
            let select = "SELECT track_config_key, map_identity, body_json, version, updated_at FROM turn_annotations
                          WHERE customer_id = ?1 AND track_config_key = ?2 AND map_identity = ?3";
            let current: Option<Value> = tx
                .query_row(select, params![customer, track, map], annotation_value)
                .optional()?;
            check_version(current.as_ref().and_then(|c| c["version"].as_i64()), given, || {
                current.clone().unwrap_or(json!({}))
            })?;
            tx.execute(
                "INSERT INTO turn_annotations(customer_id, track_config_key, map_identity, body_json, version, updated_at)
                 VALUES (?1, ?2, ?3, ?4, 1, ?5)
                 ON CONFLICT(customer_id, track_config_key, map_identity)
                 DO UPDATE SET body_json = ?4, version = version + 1, updated_at = ?5",
                params![customer, track, map, body_json, iso_now()],
            )?;
            bump_generation(&tx)?;
            let out = tx.query_row(select, params![customer, track, map], annotation_value)?;
            tx.commit()?;
            Ok(out)
        })
        .await?;
    app.events.library_changed(customer, "notes");
    Ok(out)
}

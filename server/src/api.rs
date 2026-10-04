//! HTTP API (`api.openapi.yaml`): router, system, library, views, setups, jobs, uploads, admin,
//! idempotent replay and static SPA hosting.

use std::collections::HashMap;
use std::convert::Infallible;
use std::time::Duration;

use axum::body::{Body, Bytes};
use axum::extract::{DefaultBodyLimit, Path, Query, Request, State};
use axum::http::{HeaderMap, HeaderValue, Method, StatusCode, header};
use axum::middleware::{self, Next};
use axum::response::sse::{Event as SseEvent, KeepAlive, Sse};
use axum::response::{IntoResponse, Response};
use axum::routing::{any, get, post, put};
use axum::{Json, Router};
use rusqlite::{OptionalExtension, params};
use serde_json::{Value, json};
use tokio_stream::StreamExt;
use tokio_stream::wrappers::BroadcastStream;

use crate::adapters::SelectFailure;
use crate::app::AppState;
use crate::error::{ApiError, Error, Result};
use crate::jobs::{self, NewJob, QueueParams};
use crate::schema::parse_request;
use crate::userdata::{self, LayoutKind, if_match};
use crate::util::{self, iso_now};

type Resp = Result<Response>;

pub fn json_status(status: StatusCode, value: Value) -> Response {
    (status, Json(value)).into_response()
}

pub fn json_ok(value: Value) -> Response {
    json_status(StatusCode::OK, value)
}

fn json_response(status: StatusCode, value: Value) -> Response {
    (status, Json(value)).into_response()
}

fn ok(value: Value) -> Resp {
    Ok(json_response(StatusCode::OK, value))
}

fn accepted(job: Value) -> Resp {
    Ok(json_response(StatusCode::ACCEPTED, job))
}

pub fn router(app: AppState) -> Router {
    let upload_limit = DefaultBodyLimit::max(app.cfg.max_chunk_bytes as usize + 1024);
    let big_limit = DefaultBodyLimit::max(128 << 20);
    let api = Router::new()
        .route("/status", get(status))
        .route("/events", get(events))
        .route("/drivers", get(drivers))
        .route("/drivers/{customer_id}", get(driver))
        .route("/drivers/{customer_id}/sessions", get(sessions))
        .route(
            "/drivers/{customer_id}/cars-and-tracks",
            get(cars_and_tracks),
        )
        .route("/sessions/{session_id}", get(session_detail))
        .route("/sessions/{session_id}/analysis", post(queue_analysis))
        .route("/analyses/{analysis_id}/race", get(view_race))
        .route("/analyses/{analysis_id}/runs", get(view_runs))
        .route(
            "/analyses/{analysis_id}/investigation",
            get(view_investigation),
        )
        .route("/analyses/{analysis_id}/traces/catalog", get(view_catalog))
        .route("/analyses/{analysis_id}/traces", get(view_traces))
        .route("/analyses/{analysis_id}/track", get(view_track))
        .route("/drivers/{customer_id}/setups", get(setup_library))
        .route("/setups/compare", post(setup_compare))
        .route("/setups/{setup_id}", get(setup_detail))
        .route(
            "/drivers/{customer_id}/baseline",
            post(crate::tuning::baseline),
        )
        .route(
            "/analyses/{analysis_id}/tuning",
            get(crate::tuning::context),
        )
        .route(
            "/analyses/{analysis_id}/tuning/recommendations",
            post(crate::tuning::recommend),
        )
        .route(
            "/drivers/{customer_id}/tuning/experiments/{experiment_id}/outcome",
            post(crate::tuning::outcome),
        )
        .route(
            "/drivers/{customer_id}/tuning/history",
            get(crate::tuning::history),
        )
        .route(
            "/drivers/{customer_id}/tuning-drafts/{identity_key}",
            get(get_draft).put(put_draft),
        )
        .route(
            "/drivers/{customer_id}/turn-annotations/{track_config_key}/{map_identity}",
            get(get_annotations).put(put_annotations),
        )
        .route(
            "/drivers/{customer_id}/notes",
            get(list_notes).post(create_note),
        )
        .route("/notes/{note_id}", put(update_note).delete(delete_note))
        .route(
            "/drivers/{customer_id}/trace-layouts",
            get(list_trace_layouts),
        )
        .route(
            "/drivers/{customer_id}/trace-layouts/{layout_id}",
            put(put_trace_layout).delete(delete_trace_layout),
        )
        .route(
            "/drivers/{customer_id}/live-layouts",
            get(list_live_layouts),
        )
        .route(
            "/drivers/{customer_id}/live-layouts/{layout_id}",
            put(put_live_layout).delete(delete_live_layout),
        )
        .route(
            "/drivers/{customer_id}/prefs",
            get(get_prefs).put(put_prefs),
        )
        .route("/jobs", get(list_jobs))
        .route("/jobs/{job_id}", get(get_job))
        .route("/jobs/{job_id}/cancel", post(cancel_job))
        .route("/uploads", post(create_upload))
        .route(
            "/uploads/{sha256}",
            axum::routing::head(head_upload)
                .put(put_chunk)
                .layer(upload_limit),
        )
        .route("/live/channels", get(live_channels))
        .route("/admin/import", post(admin_import).layer(big_limit))
        .route("/admin/reanalyze", post(admin_reanalyze))
        .route("/admin/snapshot", post(admin_snapshot))
        .route("/admin/remove", post(admin_remove))
        .fallback(api_not_found)
        .layer(middleware::from_fn_with_state(app.clone(), idempotency));

    let mut router = Router::new()
        .route("/health", get(health))
        .route("/ws/collector", get(crate::ws::collector_ws))
        .route("/ws/live/{customer_id}", get(crate::ws::live_ws))
        .nest("/api/v1", api)
        .route("/api/{*rest}", any(api_not_found));
    router = match app.cfg.web_dir.as_ref().filter(|dir| dir.is_dir()) {
        Some(dir) => {
            use tower_http::services::{ServeDir, ServeFile};
            let index = dir.join("index.html");
            router.fallback_service(
                ServeDir::new(dir)
                    .append_index_html_on_directories(true)
                    .not_found_service(ServeFile::new(index)),
            )
        }
        None => router.fallback(api_not_found),
    };
    router
        .layer(middleware::map_response(static_cache))
        .layer(tower_http::compression::CompressionLayer::new().gzip(true))
        .with_state(app)
}

async fn api_not_found() -> Response {
    json_response(
        StatusCode::NOT_FOUND,
        json!({"error": "not_found", "message": "no such endpoint"}),
    )
}

async fn static_cache(mut response: Response) -> Response {
    if !response.headers().contains_key(header::CACHE_CONTROL) {
        let is_asset = response
            .headers()
            .get(header::CONTENT_TYPE)
            .and_then(|v| v.to_str().ok())
            .is_some_and(|t| t.starts_with("text/html"));
        if is_asset {
            response
                .headers_mut()
                .insert(header::CACHE_CONTROL, HeaderValue::from_static("no-cache"));
        }
    }
    response
}

// ------------------------------------------------------- idempotent replay

async fn idempotency(State(app): State<AppState>, request: Request, next: Next) -> Response {
    let method = request.method().clone();
    let path = request.uri().path().to_string();
    let key = request
        .headers()
        .get("idempotency-key")
        .and_then(|v| v.to_str().ok())
        .map(str::to_string);
    let relevant = matches!(method, Method::POST | Method::PUT | Method::DELETE)
        && !path.starts_with("/api/v1/uploads");
    let Some(key) = key.filter(|_| relevant) else {
        return next.run(request).await;
    };
    let _guard = app.idem_locks.lock(&key).await;
    let k = key.clone();
    let stored = app
        .store
        .read(move |conn| {
            Ok(conn
                .query_row(
                    "SELECT method, path, status, response_json FROM request_log WHERE idempotency_key = ?1",
                    [k],
                    |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?, r.get::<_, i64>(2)?, r.get::<_, Option<String>>(3)?)),
                )
                .optional()?)
        })
        .await;
    match stored {
        Ok(Some((m, p, status, body))) => {
            if m != method.as_str() || p != path {
                return Error::unprocessable(
                    "idempotency_key_reused",
                    "the key was used for a different request",
                )
                .into_response();
            }
            let status = StatusCode::from_u16(status as u16).unwrap_or(StatusCode::OK);
            let mut response = match body {
                Some(text) => Response::builder()
                    .status(status)
                    .header(header::CONTENT_TYPE, "application/json")
                    .body(Body::from(text))
                    .unwrap_or_default(),
                None => Response::builder()
                    .status(status)
                    .body(Body::empty())
                    .unwrap_or_default(),
            };
            response
                .headers_mut()
                .insert("idempotency-replayed", HeaderValue::from_static("true"));
            return response;
        }
        Ok(None) => {}
        Err(e) => return e.into_response(),
    }
    let response = next.run(request).await;
    if response.status().is_server_error() {
        return response;
    }
    let (parts, body) = response.into_parts();
    let Ok(bytes) = axum::body::to_bytes(body, 16 << 20).await else {
        return Error::internal("response too large to record").into_response();
    };
    let text = (!bytes.is_empty()).then(|| String::from_utf8_lossy(&bytes).into_owned());
    let status = parts.status.as_u16() as i64;
    let (k, m, p) = (key, method.to_string(), path);
    let _ = app
        .store
        .write(move |conn| {
            conn.execute(
                "INSERT OR IGNORE INTO request_log(idempotency_key, method, path, status, response_json, created_at)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
                params![k, m, p, status, text, iso_now()],
            )?;
            Ok(())
        })
        .await;
    Response::from_parts(parts, Body::from(bytes))
}

// ------------------------------------------------------------------ system

async fn health(State(app): State<AppState>) -> Response {
    match app
        .store
        .read(|conn| {
            Ok(conn.query_row(
                "SELECT COALESCE(MAX(version), 0) FROM schema_migrations",
                [],
                |r| r.get::<_, i64>(0),
            )?)
        })
        .await
    {
        Ok(version) => json_response(
            StatusCode::OK,
            json!({"status": "ok", "server_version": app.cfg.app_version, "schema_version": version}),
        ),
        Err(e) => json_response(
            StatusCode::SERVICE_UNAVAILABLE,
            json!({"error": "not_ready", "message": e.to_string()}),
        ),
    }
}

fn dir_stats(dir: &std::path::Path) -> (u64, u64) {
    let (mut bytes, mut files) = (0u64, 0u64);
    if let Ok(entries) = std::fs::read_dir(dir) {
        for e in entries.flatten() {
            if let Ok(m) = e.metadata()
                && m.is_file()
            {
                bytes += m.len();
                files += 1;
            }
        }
    }
    (bytes, files)
}

async fn status(State(app): State<AppState>) -> Resp {
    let (pending, analysis, light, deferred) = app
        .store
        .read(|conn| {
            let pending: i64 = conn.query_row(
                "SELECT COUNT(*) FROM blobs WHERE nas_state <> 'archived'",
                [],
                |r| r.get(0),
            )?;
            let q = |lane: &str| -> rusqlite::Result<i64> {
                conn.query_row(
                    "SELECT COUNT(*) FROM jobs WHERE state = 'queued' AND lane = ?1",
                    [lane],
                    |r| r.get(0),
                )
            };
            let deferred: i64 = conn.query_row(
                "SELECT COUNT(*) FROM jobs WHERE state = 'deferred'",
                [],
                |r| r.get(0),
            )?;
            Ok((pending, q("analysis")?, q("light")?, deferred))
        })
        .await?;
    let python_version = app
        .python_version
        .get_or_init(|| async {
            let command = app.cfg.worker.command.clone();
            tokio::task::spawn_blocking(move || crate::app::interpreter_version(&command))
                .await
                .ok()
                .flatten()
        })
        .await
        .clone();
    let (ready_bytes, ready_files) = dir_stats(&app.cfg.spool_ready());
    let (part_bytes, part_files) = dir_stats(&app.cfg.spool_incoming());
    let collectors = app.hub.presence_list();
    let newer = collectors
        .iter()
        .any(|c| c["compatible"] == false && c["protocol"] == 1);
    ok(json!({
        "server_version": app.cfg.app_version,
        "api_version": 1,
        "collector_protocol": 1,
        "live_table_version": coach_wire::live_channels::TABLE_VERSION,
        "projection_version": app.projector.version(),
        "worker_profile_version": app.worker_identity.profile_version,
        "python_version": python_version,
        "nas": {
            "state": app.archiver.state().as_str(),
            "last_ok_at": app.archiver.last_ok_at(),
            "pending_archive": pending,
        },
        "spool": {"bytes": ready_bytes + part_bytes, "files": ready_files + part_files, "limit_bytes": app.cfg.spool_limit_bytes},
        "queue": {"analysis": analysis, "light": light, "deferred": deferred},
        "collectors": collectors,
        "disk_free_bytes": app.disk.free_bytes(&app.cfg.data_dir),
        "collector_newer_than_server": newer,
    }))
}

async fn events(State(app): State<AppState>, Query(q): Query<HashMap<String, String>>) -> Response {
    let customer: Option<i64> = q.get("customer_id").and_then(|v| v.parse().ok());
    let rx = app.events.subscribe();
    let initial: Vec<SseEvent> = app
        .hub
        .presence_list()
        .into_iter()
        .filter(|p| {
            customer.is_none_or(|c| {
                p["status"]["driver"]["customer_id"]
                    .as_i64()
                    .is_none_or(|d| d == c)
            })
        })
        .map(|p| {
            SseEvent::default()
                .event("presence")
                .data(json!({"type": "presence", "collector": p}).to_string())
        })
        .collect();
    let live = BroadcastStream::new(rx).filter_map(move |event| {
        let event = event.ok()?;
        if let (Some(want), Some(have)) = (customer, event.customer_id)
            && want != have
        {
            return None;
        }
        Some(
            SseEvent::default()
                .event(event.kind)
                .data(event.payload.to_string()),
        )
    });
    let stream = tokio_stream::iter(initial)
        .chain(live)
        .map(Ok::<SseEvent, Infallible>);
    Sse::new(stream)
        .keep_alive(KeepAlive::new().interval(Duration::from_secs(15)))
        .into_response()
}

async fn live_channels() -> Resp {
    use coach_wire::live_channels::{CHANNELS, CUES, TILES};
    let channels: Vec<Value> = CHANNELS
        .iter()
        .map(|c| {
            json!({"index": c.index, "id": c.id, "type": format!("{:?}", c.ty).to_lowercase(), "unit": c.unit, "enum": c.enum_name})
        })
        .collect();
    let cues: Vec<Value> = CUES
        .iter()
        .map(|c| {
            json!({
                "id": c.id, "key": c.key, "priority": c.priority as u8, "evidence": c.evidence as u8,
                "template": c.template, "basis": c.basis,
                "args": c.args.iter().map(|a| format!("{a:?}").to_lowercase()).collect::<Vec<_>>(),
            })
        })
        .collect();
    let tiles: Vec<Value> = TILES
        .iter()
        .map(|t| {
            json!({
                "id": t.id, "label": t.label, "channels": t.channels, "units": t.units, "format": t.format,
                "style": t.style, "precision": t.precision, "min": t.min, "max": t.max, "step": t.step,
            })
        })
        .collect();
    ok(
        json!({"table_version": coach_wire::live_channels::TABLE_VERSION, "channels": channels, "cues": cues, "tiles": tiles}),
    )
}

// ----------------------------------------------------------------- library

async fn drivers(State(app): State<AppState>) -> Resp {
    let rows = app
        .store
        .read(|conn| {
            let mut stmt = conn.prepare(
                "SELECT d.customer_id, d.display_name, d.latest_irating,
                        (SELECT COUNT(*) FROM sessions s WHERE s.customer_id = d.customer_id),
                        (SELECT MAX(s.start_time_utc) FROM sessions s WHERE s.customer_id = d.customer_id)
                 FROM drivers d
                 WHERE EXISTS (SELECT 1 FROM sessions s WHERE s.customer_id = d.customer_id)
                    OR EXISTS (SELECT 1 FROM recordings r WHERE r.customer_id = d.customer_id)
                    OR EXISTS (SELECT 1 FROM setups s WHERE s.customer_id = d.customer_id)
                    OR EXISTS (SELECT 1 FROM setup_files f WHERE f.customer_id = d.customer_id)
                 ORDER BY d.display_name COLLATE NOCASE, d.customer_id LIMIT 64",
            )?;
            Ok(stmt
                .query_map([], |r| {
                    Ok((r.get::<_, i64>(0)?, r.get::<_, String>(1)?, r.get::<_, Option<i64>>(2)?, r.get::<_, i64>(3)?, r.get::<_, Option<String>>(4)?))
                })?
                .collect::<rusqlite::Result<Vec<_>>>()?)
        })
        .await?;
    let drivers: Vec<Value> = rows
        .into_iter()
        .map(|(id, name, irating, count, last)| {
            json!({"customer_id": id, "name": name, "irating": irating, "session_count": count,
                   "last_session_at": last, "live": app.hub.is_live(id)})
        })
        .collect();
    ok(json!({"drivers": drivers}))
}

async fn driver(State(app): State<AppState>, Path(customer): Path<i64>) -> Resp {
    let row = app
        .store
        .read(move |conn| {
            Ok(conn
                .query_row(
                    "SELECT d.display_name, d.latest_irating,
                            (SELECT COUNT(*) FROM sessions s WHERE s.customer_id = d.customer_id),
                            (SELECT MAX(s.start_time_utc) FROM sessions s WHERE s.customer_id = d.customer_id)
                     FROM drivers d WHERE d.customer_id = ?1",
                    [customer],
                    |r| Ok((r.get::<_, String>(0)?, r.get::<_, Option<i64>>(1)?, r.get::<_, i64>(2)?, r.get::<_, Option<String>>(3)?)),
                )
                .optional()?)
        })
        .await?;
    let Some((name, irating, count, last)) = row else {
        return Err(Error::not_found("no such driver"));
    };
    ok(
        json!({"customer_id": customer, "name": name, "irating": irating, "session_count": count,
              "last_session_at": last, "live": app.hub.is_live(customer)}),
    )
}

/// The session's value of one recording column: the first recording (by start) that has one.
macro_rules! first_recording_value {
    ($value:literal, $present:literal) => {
        concat!(
            "(SELECT ",
            $value,
            " FROM session_recordings sr JOIN recordings r ON r.sha256 = sr.recording_sha256
               WHERE sr.session_id = s.session_id AND ",
            $present,
            " ORDER BY r.start_time_utc LIMIT 1)"
        )
    };
}

/// `WeekendInfo.SeriesName` of the session's first recording that names one.
macro_rules! series_of {
    () => {
        first_recording_value!("r.series_name", "r.series_name IS NOT NULL")
    };
}

/// `WeekendOptions.IsFixedSetup` (1, 0 or NULL) from the header summary the ingest stored.
macro_rules! fixed_setup_of {
    () => {
        first_recording_value!(
            "json_extract(r.header_json, '$.summary.is_fixed_setup')",
            "json_valid(r.header_json) AND json_extract(r.header_json, '$.summary.is_fixed_setup') IS NOT NULL"
        )
    };
}

const SESSION_SELECT: &str = concat!(
    "SELECT s.session_id, s.customer_id, s.group_key, s.session_type, s.track, s.car_path,
    s.subsession_id, s.sim_session_num, s.start_time_utc, s.analysis_state, s.analysis_reason,
    s.current_analysis_id, s.overview_json,
    (SELECT COUNT(*) FROM session_recordings sr WHERE sr.session_id = s.session_id),
    (SELECT r.track_config FROM session_recordings sr JOIN recordings r ON r.sha256 = sr.recording_sha256
       WHERE sr.session_id = s.session_id ORDER BY r.start_time_utc LIMIT 1),
    (SELECT r.car_name FROM session_recordings sr JOIN recordings r ON r.sha256 = sr.recording_sha256
       WHERE sr.session_id = s.session_id AND r.car_name IS NOT NULL ORDER BY r.start_time_utc LIMIT 1),
    ",
    series_of!(),
    ",
    (SELECT r.season_name FROM session_recordings sr JOIN recordings r ON r.sha256 = sr.recording_sha256
       WHERE sr.session_id = s.session_id AND r.season_name IS NOT NULL ORDER BY r.start_time_utc LIMIT 1),
    (SELECT MIN(sr.recording_sha256) FROM session_recordings sr WHERE sr.session_id = s.session_id),
    ",
    fixed_setup_of!(),
    "
  FROM sessions s"
);

fn session_summary(r: &rusqlite::Row<'_>) -> rusqlite::Result<Value> {
    let subsession: Option<i64> = r.get(6)?;
    let first_sha: Option<String> = r.get(18)?;
    let event_key = match (subsession.filter(|s| *s != 0), first_sha) {
        (Some(id), _) => format!("subsession:{id}"),
        (None, Some(sha)) => format!("offline:{sha}"),
        (None, None) => format!("offline:session-{}", r.get::<_, i64>(0)?),
    };
    let overview: String = r.get(12)?;
    let mut overview: Value = serde_json::from_str(&overview).unwrap_or(json!({}));
    if !overview.is_object() {
        overview = json!({});
    }
    Ok(json!({
        "session_id": r.get::<_, i64>(0)?,
        "customer_id": r.get::<_, i64>(1)?,
        "group_key": r.get::<_, String>(2)?,
        "event_key": event_key,
        "session_type": r.get::<_, Option<String>>(3)?,
        "track": r.get::<_, Option<String>>(4)?,
        "track_config": r.get::<_, Option<String>>(14)?,
        "car_path": r.get::<_, Option<String>>(5)?,
        "car_name": r.get::<_, Option<String>>(15)?,
        "series_name": r.get::<_, Option<String>>(16)?,
        "season_name": r.get::<_, Option<String>>(17)?,
        "is_fixed_setup": r.get::<_, Option<i64>>(19)?.map(|v| v != 0),
        "subsession_id": subsession,
        "sim_session_num": r.get::<_, Option<i64>>(7)?,
        "start_time_utc": r.get::<_, Option<String>>(8)?,
        "recording_count": r.get::<_, i64>(13)?,
        "analysis_state": r.get::<_, String>(9)?,
        "analysis_reason": r.get::<_, Option<String>>(10)?,
        "analysis_id": r.get::<_, Option<String>>(11)?,
        "overview": overview,
    }))
}

/// Every value of a repeated query parameter, trimmed, blanks dropped.
fn query_values(pairs: &[(String, String)], name: &str) -> Vec<String> {
    pairs
        .iter()
        .filter(|(key, _)| key == name)
        .map(|(_, value)| value.trim().to_string())
        .filter(|value| !value.is_empty())
        .collect()
}

/// `AND lower(<column>) IN (?, ?)` over lowercased values, so the match ignores case; the arguments
/// are pushed. `column` yields text.
fn push_in(
    sql: &mut String,
    args: &mut Vec<rusqlite::types::Value>,
    column: &str,
    values: &[String],
) {
    let marks: Vec<String> = values
        .iter()
        .map(|value| {
            args.push(value.to_lowercase().into());
            format!("?{}", args.len())
        })
        .collect();
    sql.push_str(&format!(
        " AND lower(COALESCE({column}, '')) IN ({})",
        marks.join(", ")
    ));
}

async fn sessions(
    State(app): State<AppState>,
    Path(customer): Path<i64>,
    Query(pairs): Query<Vec<(String, String)>>,
) -> Resp {
    let one = |name: &str| {
        pairs
            .iter()
            .find(|(key, _)| key == name)
            .map(|(_, value)| value.clone())
    };
    let mut kinds = query_values(&pairs, "type");
    if let Some(bad) = kinds
        .iter()
        .find(|k| !["all", "race", "qualify", "practice", "test"].contains(&k.as_str()))
    {
        return Err(Error::invalid(format!(
            "type must be all, race, qualify, practice or test, not {bad}"
        )));
    }
    if kinds.iter().any(|k| k == "all") {
        kinds.clear();
    }
    let setup = match one("setup").as_deref().map(str::trim) {
        None | Some("") => None,
        Some("fixed") => Some(true),
        Some("open") => Some(false),
        Some(_) => return Err(Error::invalid("setup must be fixed or open")),
    };
    let (cars, tracks, series) = (
        query_values(&pairs, "car"),
        query_values(&pairs, "track"),
        query_values(&pairs, "series"),
    );
    let limit: usize = one("limit")
        .and_then(|v| v.parse().ok())
        .unwrap_or(50)
        .clamp(1, 200);
    let needle = one("q")
        .map(|v| v.trim().to_string())
        .filter(|v| !v.is_empty())
        .map(|v| util::like_pattern(&v));
    let cursor = one("cursor");
    let page = app
        .store
        .read(move |conn| {
            let mut sql = format!("{SESSION_SELECT} WHERE s.customer_id = ?1");
            let mut args: Vec<rusqlite::types::Value> = vec![customer.into()];
            if !kinds.is_empty() {
                let clauses: Vec<&str> = kinds
                    .iter()
                    .map(|kind| match kind.as_str() {
                        "race" => "lower(COALESCE(s.session_type, '')) LIKE 'race%'",
                        "qualify" => "lower(COALESCE(s.session_type, '')) LIKE '%qualify%'",
                        "practice" => "lower(COALESCE(s.session_type, '')) LIKE 'practice%'",
                        _ => "(lower(COALESCE(s.session_type, '')) LIKE 'offline%' OR lower(COALESCE(s.session_type, '')) LIKE '%test%')",
                    })
                    .collect();
                sql.push_str(&format!(" AND ({})", clauses.join(" OR ")));
            }
            if !cars.is_empty() {
                push_in(&mut sql, &mut args, "s.car_path", &cars);
            }
            if !tracks.is_empty() {
                // the discovery's track name, or the recordings' display name
                let marks: Vec<String> = tracks
                    .iter()
                    .map(|value| {
                        args.push(value.to_lowercase().into());
                        format!("?{}", args.len())
                    })
                    .collect();
                let marks = marks.join(", ");
                sql.push_str(&format!(
                    " AND (lower(COALESCE(s.track, '')) IN ({marks}) OR EXISTS (SELECT 1 FROM session_recordings sr
                        JOIN recordings r ON r.sha256 = sr.recording_sha256 WHERE sr.session_id = s.session_id
                        AND lower(COALESCE(r.track, '')) IN ({marks})))"
                ));
            }
            if !series.is_empty() {
                push_in(&mut sql, &mut args, series_of!(), &series);
            }
            if let Some(fixed) = setup {
                sql.push_str(&format!(" AND {} = {}", fixed_setup_of!(), i64::from(fixed)));
            }
            if let Some(n) = &needle {
                sql.push_str(&format!(
                    " AND (lower(COALESCE(s.track,'') || ' ' || COALESCE(s.car_path,'') || ' ' ||
                        COALESCE((SELECT r.track_config || ' ' || COALESCE(r.car_name,'') || ' ' || COALESCE(r.series_name,'')
                                  FROM session_recordings sr JOIN recordings r ON r.sha256 = sr.recording_sha256
                                  WHERE sr.session_id = s.session_id LIMIT 1), '')) LIKE ?{} ESCAPE '\\')",
                    args.len() + 1
                ));
                args.push(n.clone().into());
            }
            if let Some(c) = &cursor {
                let (start, id) = c
                    .split_once('|')
                    .and_then(|(s, i)| Some((s.to_string(), i.parse::<i64>().ok()?)))
                    .ok_or_else(|| Error::invalid("cursor is not valid"))?;
                sql.push_str(&format!(
                    " AND (COALESCE(s.start_time_utc, '') < ?{a} OR (COALESCE(s.start_time_utc, '') = ?{a} AND s.session_id < ?{b}))",
                    a = args.len() + 1,
                    b = args.len() + 2
                ));
                args.push(start.into());
                args.push(id.into());
            }
            sql.push_str(&format!(
                " ORDER BY COALESCE(s.start_time_utc, '') DESC, s.session_id DESC LIMIT {}",
                limit + 1
            ));
            let mut stmt = conn.prepare(&sql)?;
            let mut rows: Vec<Value> = stmt
                .query_map(rusqlite::params_from_iter(args), session_summary)?
                .collect::<rusqlite::Result<_>>()?;
            let next = if rows.len() > limit {
                rows.truncate(limit);
                rows.last().map(|r| {
                    format!("{}|{}", r["start_time_utc"].as_str().unwrap_or(""), r["session_id"])
                })
            } else {
                None
            };
            Ok(json!({"sessions": rows, "next_cursor": next}))
        })
        .await?;
    ok(page)
}

async fn cars_and_tracks(State(app): State<AppState>, Path(customer): Path<i64>) -> Resp {
    ok(app
        .store
        .read(move |conn| Ok(crate::setups::cars_and_tracks(conn, customer)?))
        .await?)
}

async fn session_detail(State(app): State<AppState>, Path(session_id): Path<i64>) -> Resp {
    let detail = app
        .store
        .read(move |conn| {
            let session = conn
                .query_row(&format!("{SESSION_SELECT} WHERE s.session_id = ?1"), [session_id], session_summary)
                .optional()?;
            let Some(session) = session else {
                return Err(Error::not_found("no such session"));
            };
            let mut stmt = conn.prepare(
                "SELECT b.sha256, b.size, b.first_name, r.start_time_utc, COALESCE(r.ingest_state, 'verified'), b.nas_state
                 FROM session_recordings sr JOIN blobs b ON b.sha256 = sr.recording_sha256
                 LEFT JOIN recordings r ON r.sha256 = b.sha256
                 WHERE sr.session_id = ?1 ORDER BY r.start_time_utc, b.sha256",
            )?;
            let recordings: Vec<Value> = stmt
                .query_map([session_id], |r| {
                    Ok(json!({
                        "sha256": r.get::<_, String>(0)?, "size": r.get::<_, i64>(1)?, "file_name": r.get::<_, String>(2)?,
                        "start_time_utc": r.get::<_, Option<String>>(3)?, "ingest_state": r.get::<_, String>(4)?,
                        "nas_state": r.get::<_, String>(5)?,
                    }))
                })?
                .collect::<rusqlite::Result<_>>()?;
            let mut stmt = conn.prepare(
                "SELECT a.analysis_id, a.created_at, a.profile_version, a.lap_count, a.projection_version,
                        a.projection_state, a.analysis_id = s.current_analysis_id
                 FROM analyses a JOIN sessions s ON s.session_id = a.session_id
                 WHERE a.session_id = ?1 ORDER BY a.created_at DESC, a.analysis_id LIMIT 50",
            )?;
            let analyses: Vec<Value> = stmt
                .query_map([session_id], |r| {
                    Ok(json!({
                        "analysis_id": r.get::<_, String>(0)?, "created_at": r.get::<_, String>(1)?,
                        "profile_version": r.get::<_, String>(2)?, "lap_count": r.get::<_, Option<i64>>(3)?,
                        "projection_version": r.get::<_, Option<i64>>(4)?, "projection_state": r.get::<_, String>(5)?,
                        "current": r.get::<_, Option<bool>>(6)?.unwrap_or(false),
                    }))
                })?
                .collect::<rusqlite::Result<_>>()?;
            let setup_ids: Vec<i64> = conn
                .prepare(
                    "SELECT DISTINCT o.setup_id FROM setup_observations o
                     JOIN session_recordings sr ON sr.recording_sha256 = o.recording_sha256
                     WHERE sr.session_id = ?1 ORDER BY o.setup_id",
                )?
                .query_map([session_id], |r| r.get(0))?
                .collect::<rusqlite::Result<_>>()?;
            let notes_count: i64 = conn.query_row(
                "SELECT COUNT(*) FROM notes WHERE subject_kind = 'session' AND subject_id = ?1",
                [session_id.to_string()],
                |r| r.get(0),
            )?;
            let (customer, subsession): (i64, Option<i64>) = conn.query_row(
                "SELECT customer_id, subsession_id FROM sessions WHERE session_id = ?1",
                [session_id],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )?;
            let event_sessions: Vec<Value> = match subsession.filter(|s| *s != 0) {
                Some(sub) => conn
                    .prepare(
                        "SELECT session_id, session_type, analysis_state, analysis_reason FROM sessions
                         WHERE customer_id = ?1 AND subsession_id = ?2
                         AND session_id <> ?3 ORDER BY sim_session_num, session_id",
                    )?
                    .query_map(params![customer, sub, session_id], |r| {
                        Ok(json!({
                            "session_id": r.get::<_, i64>(0)?,
                            "session_type": r.get::<_, Option<String>>(1)?,
                            "analysis_state": r.get::<_, String>(2)?,
                            "analysis_reason": r.get::<_, Option<String>>(3)?,
                        }))
                    })?
                    .collect::<rusqlite::Result<_>>()?,
                None => Vec::new(),
            };
            Ok(json!({
                "session": session, "recordings": recordings, "analyses": analyses, "setup_ids": setup_ids,
                "notes_count": notes_count, "event_sessions": event_sessions,
            }))
        })
        .await?;
    ok(detail)
}

async fn queue_analysis(State(app): State<AppState>, Path(session_id): Path<i64>) -> Resp {
    let queue = QueueParams::of(&app);
    let result = app
        .store
        .write(move |conn| {
            let tx = conn.transaction()?;
            let exists: bool = tx.query_row(
                "SELECT COUNT(*) > 0 FROM sessions WHERE session_id = ?1",
                [session_id],
                |r| r.get(0),
            )?;
            if !exists {
                return Err(Error::not_found("no such session"));
            }
            let Some((id, _)) = jobs::queue_analysis_tx(&tx, &queue, session_id, true, true)?
            else {
                return Err(Error::unprocessable(
                    "no_recordings",
                    "the session has no recordings",
                ));
            };
            let value = jobs::job_ref(&tx, id)?;
            tx.commit()?;
            Ok(value)
        })
        .await?;
    app.jobs.wake_all();
    accepted(result)
}

// ------------------------------------------------------------------- views

pub struct ViewDir {
    pub dir: std::path::PathBuf,
    pub etag: String,
}

pub async fn ready_view_dir(app: &AppState, analysis_id: &str) -> Result<std::path::PathBuf> {
    Ok(view_dir(app, analysis_id).await?.dir)
}

pub async fn require_driver(app: &AppState, customer: i64) -> Result<()> {
    let exists: bool = app
        .store
        .read(move |conn| {
            Ok(conn.query_row(
                "SELECT COUNT(*) > 0 FROM drivers WHERE customer_id = ?1",
                [customer],
                |r| r.get(0),
            )?)
        })
        .await?;
    if exists {
        Ok(())
    } else {
        Err(Error::not_found("no such driver"))
    }
}

async fn view_dir(app: &AppState, analysis_id: &str) -> Result<ViewDir> {
    let id = analysis_id.to_string();
    let row: Option<(String, Option<i64>, Option<String>, i64)> = app
        .store
        .read(move |conn| {
            Ok(conn
                .query_row(
                    "SELECT projection_state, projection_version, projection_error,
                            (SELECT customer_id FROM sessions s WHERE s.session_id = a.session_id)
                     FROM analyses a WHERE analysis_id = ?1",
                    [id],
                    |r| {
                        Ok((
                            r.get(0)?,
                            r.get(1)?,
                            r.get(2)?,
                            r.get::<_, Option<i64>>(3)?.unwrap_or(0),
                        ))
                    },
                )
                .optional()?)
        })
        .await?;
    let Some((state, version, error, customer)) = row else {
        return Err(Error::not_found("no such analysis"));
    };
    let want = app.projector.version();
    if state == "ready" && version == Some(want as i64) {
        return Ok(ViewDir {
            dir: app.cfg.views_dir(analysis_id, want),
            etag: format!("\"{analysis_id}-p{want}\""),
        });
    }
    // Queue (or re-queue) the projection and tell the browser to retry on analysis_ready.
    let (id, customer) = (analysis_id.to_string(), customer);
    app.store
        .write(move |conn| {
            let tx = conn.transaction()?;
            if state != "pending" || version.is_some_and(|v| v != want as i64) {
                tx.execute(
                    "UPDATE analyses SET projection_state = 'pending' WHERE analysis_id = ?1 AND projection_state <> 'pending'",
                    [&id],
                )?;
            }
            jobs::requeue_project_tx(&tx, &id, want, Some(customer))?;
            tx.commit()?;
            Ok(())
        })
        .await?;
    app.jobs.wake_all();
    let mut api = ApiError::new(
        StatusCode::CONFLICT,
        "projection_pending",
        "the analysis views are being built; retry on analysis_ready",
    );
    if let Some(error) = error {
        api = api.with_details(json!({"last_error": error}));
    }
    Err(Error::Api(api))
}

async fn serve_view(app: &AppState, analysis_id: &str, file: &str, headers: &HeaderMap) -> Resp {
    let view = view_dir(app, analysis_id).await?;
    let path = view.dir.join(file);
    let bytes = match tokio::fs::read(&path).await {
        Ok(b) => b,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
            return Err(Error::not_found("this analysis has no such view"));
        }
        Err(e) => return Err(e.into()),
    };
    if headers
        .get(header::IF_NONE_MATCH)
        .and_then(|v| v.to_str().ok())
        .is_some_and(|v| v.split(',').any(|t| t.trim() == view.etag))
    {
        return Ok(Response::builder()
            .status(StatusCode::NOT_MODIFIED)
            .header(header::ETAG, &view.etag)
            .body(Body::empty())
            .unwrap_or_default());
    }
    Ok(Response::builder()
        .status(StatusCode::OK)
        .header(header::CONTENT_TYPE, "application/json")
        .header(header::ETAG, &view.etag)
        .header(header::CACHE_CONTROL, "no-cache")
        .body(Body::from(bytes))
        .unwrap_or_default())
}

macro_rules! view_handler {
    ($name:ident, $file:expr) => {
        async fn $name(
            State(app): State<AppState>,
            Path(id): Path<String>,
            headers: HeaderMap,
        ) -> Resp {
            serve_view(&app, &id, $file, &headers).await
        }
    };
}

view_handler!(view_race, "race.json");
view_handler!(view_runs, "runs.json");
view_handler!(view_investigation, "investigation.json");
view_handler!(view_catalog, "traces-catalog.json");
view_handler!(view_track, "track.json");

async fn view_traces(
    State(app): State<AppState>,
    Path(id): Path<String>,
    Query(q): Query<HashMap<String, String>>,
) -> Resp {
    let view = view_dir(&app, &id).await?;
    if !view.dir.join("traces.ccol").exists() {
        return Err(Error::not_found("this analysis has no traces"));
    }
    let (laps, channels) = (q.get("laps").cloned(), q.get("channels").cloned());
    let projector = app.projector.clone();
    let dir = view.dir.clone();
    let result = tokio::task::spawn_blocking(move || {
        projector.select_traces(&dir, laps.as_deref(), channels.as_deref(), 16 << 20)
    })
    .await
    .map_err(|e| Error::internal(e.to_string()))?;
    let body = match result {
        Ok(b) => b,
        Err(SelectFailure::BadLaps(m)) => {
            return Err(Error::invalid(format!("invalid laps selection: {m}")));
        }
        Err(SelectFailure::UnknownChannel(c)) => {
            return Err(Error::invalid(format!("unknown channel {c}")));
        }
        Err(SelectFailure::TooLarge { bytes, limit }) => {
            return Err(Error::Api(ApiError::new(
                StatusCode::PAYLOAD_TOO_LARGE,
                "too_large",
                format!("selection is {bytes} bytes, bound {limit}; narrow laps or channels"),
            )));
        }
        Err(SelectFailure::Corrupt(m)) => {
            // re-project on the next request
            let _ = std::fs::remove_dir_all(&view.dir);
            let id2 = id.clone();
            let _ = app
                .store
                .write(move |conn| {
                    conn.execute(
                        "UPDATE analyses SET projection_state = 'pending' WHERE analysis_id = ?1",
                        [id2],
                    )?;
                    Ok(())
                })
                .await;
            return Err(Error::internal(format!(
                "trace view was corrupt and will be rebuilt: {m}"
            )));
        }
        Err(SelectFailure::Io(m)) => return Err(Error::internal(m)),
    };
    let laps = ccol_lap_count(&body);
    Ok(Response::builder()
        .status(StatusCode::OK)
        .header(header::CONTENT_TYPE, "application/vnd.coach.columns")
        .header(header::ETAG, &view.etag)
        .header(header::CACHE_CONTROL, "no-cache")
        .header("x-coach-trace-laps", laps.to_string())
        .body(Body::from(body))
        .unwrap_or_default())
}

/// Lap count from a CCOL header (`TraceBlobHeader.laps`); 0 when the container is unreadable.
fn ccol_lap_count(body: &[u8]) -> usize {
    if body.len() < 12 || &body[0..4] != b"CCOL" {
        return 0;
    }
    let len = u32::from_le_bytes([body[8], body[9], body[10], body[11]]) as usize;
    body.get(12..12 + len)
        .and_then(|h| serde_json::from_slice::<Value>(h).ok())
        .and_then(|h| h["laps"].as_array().map(|a| a.len()))
        .unwrap_or(0)
}

// ------------------------------------------------------------------ setups

async fn setup_library(
    State(app): State<AppState>,
    Path(customer): Path<i64>,
    Query(q): Query<HashMap<String, String>>,
) -> Resp {
    let limit: usize = q
        .get("limit")
        .and_then(|v| v.parse().ok())
        .unwrap_or(50)
        .clamp(1, 200);
    let raced = q.get("raced_only").is_some_and(|v| v == "true" || v == "1");
    let raced_filter = match q.get("raced").map(String::as_str) {
        None | Some("") => None,
        Some("true" | "1") => Some(true),
        Some("false" | "0") => Some(false),
        Some(_) => return Err(Error::invalid("raced must be true or false")),
    };
    let (car, text, cursor) = (
        q.get("car_path").cloned(),
        q.get("q").cloned(),
        q.get("cursor").cloned(),
    );
    let page = app
        .store
        .read(move |conn| {
            crate::setups::library_page(
                conn,
                &crate::setups::LibraryQuery {
                    customer_id: customer,
                    car_path: car.as_deref(),
                    q: text.as_deref(),
                    raced_only: raced,
                    raced: raced_filter,
                    cursor: cursor.as_deref(),
                    limit,
                },
            )
        })
        .await?;
    ok(page)
}

async fn setup_detail(State(app): State<AppState>, Path(setup_id): Path<i64>) -> Resp {
    match app
        .store
        .read(move |conn| crate::setups::detail(conn, setup_id))
        .await?
    {
        Some(detail) => ok(detail),
        None => Err(Error::not_found("no such setup")),
    }
}

async fn setup_compare(State(app): State<AppState>, body: Bytes) -> Resp {
    let request = parse_request("SetupCompareRequest", &body)?;
    let (left_id, right_id) = (
        request["left_setup_id"].as_i64().unwrap_or_default(),
        request["right_setup_id"].as_i64().unwrap_or_default(),
    );
    let (left, right) = app
        .store
        .read(move |conn| {
            Ok((
                crate::setups::values_of_setup(conn, left_id)?,
                crate::setups::values_of_setup(conn, right_id)?,
            ))
        })
        .await?;
    let (Some((left_car, left_values, left_export)), Some((right_car, right_values, right_export))) =
        (left, right)
    else {
        return Err(Error::not_found("no such setup"));
    };
    if !left_car.eq_ignore_ascii_case(&right_car) {
        return Err(Error::unprocessable(
            "different_cars",
            "setups of different cars cannot be compared",
        ));
    }
    if left_values.is_empty() || right_values.is_empty() {
        return Err(Error::unprocessable(
            "values_unknown",
            "a setup without values cannot be compared",
        ));
    }
    if left_export != right_export {
        // A garage export keys its values differently from a recording; compared, every row would
        // read unknown.
        return Err(Error::unprocessable(
            "different_schemes",
            "a setup known only from a garage export cannot be compared with a recorded one",
        ));
    }
    let scheme = if left_export { "export" } else { "recording" };
    let customer: Option<i64> = app
        .store
        .read(move |conn| {
            Ok(conn
                .query_row(
                    "SELECT customer_id FROM setups WHERE setup_id = ?1",
                    [left_id],
                    |r| r.get(0),
                )
                .optional()?)
        })
        .await?;
    let subject = json!({
        "needs_setups": false,
        "arguments": {"left": left_values, "right": right_values, "scheme": scheme},
    });
    let job = NewJob::new("setup_compare", "light", customer, subject).interactive();
    let id = app
        .store
        .write(move |conn| {
            let tx = conn.transaction()?;
            let (id, _) = jobs::enqueue_tx(&tx, &job, false)?;
            tx.commit()?;
            Ok(id)
        })
        .await?;
    app.jobs.wake_all();
    match jobs::wait_finished(&app, id, app.cfg.inline_wait).await? {
        Some(job) if job["state"] == "succeeded" => ok(job["result"].clone()),
        Some(job) if job["state"] == "failed" => Err(Error::Api(ApiError::new(
            StatusCode::BAD_GATEWAY,
            "compare_failed",
            job["error_message"]
                .as_str()
                .unwrap_or("the comparison failed")
                .to_string(),
        ))),
        _ => {
            let job = app
                .store
                .read(move |conn| Ok(jobs::job_ref(conn, id)?))
                .await?;
            accepted(job)
        }
    }
}

// ---------------------------------------------------------------- user data

async fn list_notes(
    State(app): State<AppState>,
    Path(customer): Path<i64>,
    Query(q): Query<HashMap<String, String>>,
) -> Resp {
    let (Some(kind), Some(subject)) = (q.get("subject_kind"), q.get("subject_id")) else {
        return Err(Error::invalid("subject_kind and subject_id are required"));
    };
    if !["session", "setup", "analysis"].contains(&kind.as_str()) {
        return Err(Error::invalid(
            "subject_kind must be session, setup or analysis",
        ));
    }
    ok(userdata::list_notes(&app, customer, kind.clone(), subject.clone()).await?)
}

async fn create_note(State(app): State<AppState>, Path(customer): Path<i64>, body: Bytes) -> Resp {
    Ok(json_response(
        StatusCode::CREATED,
        userdata::create_note(&app, customer, &body).await?,
    ))
}

async fn update_note(
    State(app): State<AppState>,
    Path(id): Path<i64>,
    headers: HeaderMap,
    body: Bytes,
) -> Resp {
    ok(userdata::update_note(&app, id, if_match(&headers), &body).await?)
}

async fn delete_note(State(app): State<AppState>, Path(id): Path<i64>, headers: HeaderMap) -> Resp {
    userdata::delete_note(&app, id, if_match(&headers)).await?;
    Ok(StatusCode::NO_CONTENT.into_response())
}

async fn list_trace_layouts(State(app): State<AppState>, Path(c): Path<i64>) -> Resp {
    ok(userdata::list_layouts(&app, LayoutKind::Trace, c).await?)
}

async fn put_trace_layout(
    State(app): State<AppState>,
    Path((c, id)): Path<(i64, String)>,
    headers: HeaderMap,
    body: Bytes,
) -> Resp {
    ok(userdata::put_layout(&app, LayoutKind::Trace, c, id, if_match(&headers), &body).await?)
}

async fn delete_trace_layout(
    State(app): State<AppState>,
    Path((c, id)): Path<(i64, String)>,
    headers: HeaderMap,
) -> Resp {
    userdata::delete_layout(&app, LayoutKind::Trace, c, id, if_match(&headers)).await?;
    Ok(StatusCode::NO_CONTENT.into_response())
}

async fn list_live_layouts(State(app): State<AppState>, Path(c): Path<i64>) -> Resp {
    ok(userdata::list_layouts(&app, LayoutKind::Live, c).await?)
}

async fn put_live_layout(
    State(app): State<AppState>,
    Path((c, id)): Path<(i64, String)>,
    headers: HeaderMap,
    body: Bytes,
) -> Resp {
    ok(userdata::put_layout(&app, LayoutKind::Live, c, id, if_match(&headers), &body).await?)
}

async fn delete_live_layout(
    State(app): State<AppState>,
    Path((c, id)): Path<(i64, String)>,
    headers: HeaderMap,
) -> Resp {
    userdata::delete_layout(&app, LayoutKind::Live, c, id, if_match(&headers)).await?;
    Ok(StatusCode::NO_CONTENT.into_response())
}

async fn get_prefs(State(app): State<AppState>, Path(c): Path<i64>) -> Resp {
    ok(userdata::get_prefs(&app, c).await?)
}

async fn put_prefs(
    State(app): State<AppState>,
    Path(c): Path<i64>,
    headers: HeaderMap,
    body: Bytes,
) -> Resp {
    ok(userdata::put_prefs(&app, c, if_match(&headers), &body).await?)
}

async fn get_draft(State(app): State<AppState>, Path((c, key)): Path<(i64, String)>) -> Resp {
    ok(userdata::get_draft(&app, c, key).await?)
}

async fn put_draft(
    State(app): State<AppState>,
    Path((c, key)): Path<(i64, String)>,
    headers: HeaderMap,
    body: Bytes,
) -> Resp {
    ok(userdata::put_draft(&app, c, key, if_match(&headers), &body).await?)
}

async fn get_annotations(
    State(app): State<AppState>,
    Path((c, track, map)): Path<(i64, String, String)>,
) -> Resp {
    ok(userdata::get_annotations(&app, c, track, map).await?)
}

async fn put_annotations(
    State(app): State<AppState>,
    Path((c, track, map)): Path<(i64, String, String)>,
    headers: HeaderMap,
    body: Bytes,
) -> Resp {
    ok(userdata::put_annotations(&app, c, track, map, if_match(&headers), &body).await?)
}

// -------------------------------------------------------------------- jobs

async fn list_jobs(State(app): State<AppState>, Query(q): Query<HashMap<String, String>>) -> Resp {
    let limit: i64 = q
        .get("limit")
        .and_then(|v| v.parse().ok())
        .unwrap_or(50)
        .clamp(1, 200);
    let state = q.get("state").cloned();
    if let Some(s) = &state
        && ![
            "queued",
            "running",
            "succeeded",
            "failed",
            "cancelled",
            "interrupted",
            "deferred",
        ]
        .contains(&s.as_str())
    {
        return Err(Error::invalid("unknown job state"));
    }
    let customer: Option<i64> = q.get("customer_id").and_then(|v| v.parse().ok());
    let list = app
        .store
        .read(move |conn| {
            let ids: Vec<i64> = conn
                .prepare(
                    "SELECT job_id FROM jobs WHERE (?1 IS NULL OR state = ?1) AND (?2 IS NULL OR customer_id = ?2)
                     ORDER BY job_id DESC LIMIT ?3",
                )?
                .query_map(params![state, customer, limit], |r| r.get(0))?
                .collect::<rusqlite::Result<_>>()?;
            let mut jobs = Vec::new();
            for id in ids {
                if let Some(mut v) = jobs::job_value(conn, id)? {
                    // the list omits results; GET /jobs/{id} carries them
                    v.as_object_mut().map(|m| m.remove("result"));
                    jobs.push(v);
                }
            }
            Ok(json!({"jobs": jobs}))
        })
        .await?;
    ok(list)
}

async fn get_job(State(app): State<AppState>, Path(id): Path<i64>) -> Resp {
    match app
        .store
        .read(move |conn| Ok(jobs::job_value(conn, id)?))
        .await?
    {
        Some(job) => ok(job),
        None => Err(Error::not_found("no such job")),
    }
}

async fn cancel_job(State(app): State<AppState>, Path(id): Path<i64>) -> Resp {
    let state: Option<String> = app
        .store
        .read(move |conn| {
            Ok(conn
                .query_row("SELECT state FROM jobs WHERE job_id = ?1", [id], |r| {
                    r.get(0)
                })
                .optional()?)
        })
        .await?;
    match state.as_deref() {
        None => Err(Error::not_found("no such job")),
        Some("running") => {
            if !app.jobs.cancel(id) {
                // running in the table but not in this process (stale after a crash)
                app.store
                    .write(move |conn| {
                        conn.execute("UPDATE jobs SET state = 'cancelled', finished_at = ?2 WHERE job_id = ?1", params![id, iso_now()])?;
                        Ok(())
                    })
                    .await?;
            }
            let job = app
                .store
                .read(move |conn| Ok(jobs::job_ref(conn, id)?))
                .await?;
            accepted(job)
        }
        Some("queued") | Some("deferred") | Some("interrupted") => {
            app.store
                .write(move |conn| {
                    conn.execute(
                        "UPDATE jobs SET state = 'cancelled', finished_at = ?2, error_code = 'cancelled'
                         WHERE job_id = ?1 AND state IN ('queued', 'deferred', 'interrupted')",
                        params![id, iso_now()],
                    )?;
                    let sessions: Vec<i64> = conn
                        .prepare("SELECT json_extract(subject_json, '$.session_id') FROM jobs WHERE job_id = ?1 AND kind = 'analyze'")?
                        .query_map([id], |r| r.get::<_, Option<i64>>(0))?
                        .filter_map(|r| r.ok().flatten())
                        .collect();
                    for s in sessions {
                        jobs::refresh_session_state(conn, s)?;
                    }
                    Ok(())
                })
                .await?;
            let job = app
                .store
                .read(move |conn| Ok(jobs::job_ref(conn, id)?))
                .await?;
            accepted(job)
        }
        Some(other) => Err(Error::conflict(
            "already_finished",
            format!("the job is already {other}"),
        )),
    }
}

// ----------------------------------------------------------------- uploads

async fn create_upload(State(app): State<AppState>, body: Bytes) -> Resp {
    let (status, value) = crate::ingest::create_upload(&app, &body).await?;
    Ok(json_response(status, value))
}

async fn head_upload(State(app): State<AppState>, Path(sha): Path<String>) -> Resp {
    if !util::is_sha256_hex(&sha) {
        return Err(Error::invalid("sha256 must be 64 lowercase hex characters"));
    }
    match crate::ingest::head_upload(&app, &sha).await? {
        Some((offset, state)) => Ok(Response::builder()
            .status(StatusCode::OK)
            .header("upload-offset", offset.to_string())
            .header("upload-state", state)
            .body(Body::empty())
            .unwrap_or_default()),
        None => Ok(StatusCode::NOT_FOUND.into_response()),
    }
}

fn parse_content_range(headers: &HeaderMap) -> Result<(i64, i64, i64)> {
    let text = headers
        .get(header::CONTENT_RANGE)
        .and_then(|v| v.to_str().ok())
        .ok_or_else(|| Error::invalid("Content-Range is required"))?;
    let rest = text
        .strip_prefix("bytes ")
        .ok_or_else(|| Error::invalid("Content-Range must be 'bytes start-end/size'"))?;
    let (range, size) = rest
        .split_once('/')
        .ok_or_else(|| Error::invalid("Content-Range lacks the total size"))?;
    let (start, end) = range
        .split_once('-')
        .ok_or_else(|| Error::invalid("Content-Range lacks the range"))?;
    let parse = |s: &str| {
        s.trim()
            .parse::<i64>()
            .map_err(|_| Error::invalid("Content-Range numbers are invalid"))
    };
    Ok((parse(start)?, parse(end)?, parse(size)?))
}

async fn put_chunk(
    State(app): State<AppState>,
    Path(sha): Path<String>,
    headers: HeaderMap,
    body: Bytes,
) -> Resp {
    if !util::is_sha256_hex(&sha) {
        return Err(Error::invalid("sha256 must be 64 lowercase hex characters"));
    }
    let (start, end, size) = parse_content_range(&headers)?;
    let (status, value) = crate::ingest::put_chunk(&app, &sha, start, end, size, &body).await?;
    Ok(json_response(status, value))
}

// ------------------------------------------------------------------- admin

async fn admin_import(State(app): State<AppState>, body: Bytes) -> Resp {
    let bundle = parse_request("ImportBundle", &body)?;
    let sha = util::sha256_hex(&body);
    let customer = bundle["customer_id"].as_i64();
    let path = crate::import::bundle_path(&app, &sha);
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir)?;
    }
    if !path.exists() {
        let tmp = path.with_extension("tmp");
        std::fs::write(&tmp, &body)?;
        std::fs::rename(&tmp, &path)?;
    }
    let key = format!("import:{sha}");
    let (job, imported) = app
        .store
        .write(move |conn| {
            let tx = conn.transaction()?;
            let imported: bool = tx.query_row(
                "SELECT COUNT(*) > 0 FROM imports WHERE bundle_sha256 = ?1",
                [&sha],
                |r| r.get(0),
            )?;
            let new =
                NewJob::new("import", "light", customer, json!({"bundle_sha256": sha})).keyed(key);
            let (id, _) = jobs::enqueue_tx(&tx, &new, !imported)?;
            let job = jobs::job_ref(&tx, id)?;
            tx.commit()?;
            Ok((job, imported))
        })
        .await?;
    app.jobs.wake_all();
    if imported && job["state"] == "succeeded" {
        return ok(job);
    }
    accepted(job)
}

async fn admin_reanalyze(State(app): State<AppState>, body: Bytes) -> Resp {
    let value: Value = if body.is_empty() {
        json!({})
    } else {
        serde_json::from_slice(&body)
            .map_err(|e| Error::invalid(format!("body is not valid JSON: {e}")))?
    };
    let customer = value["customer_id"].as_i64();
    let job = app
        .store
        .write(move |conn| {
            let tx = conn.transaction()?;
            let new = NewJob::new(
                "reanalyze",
                "server",
                customer,
                json!({"customer_id": customer}),
            );
            let (id, _) = jobs::enqueue_tx(&tx, &new, false)?;
            let job = jobs::job_ref(&tx, id)?;
            tx.commit()?;
            Ok(job)
        })
        .await?;
    app.jobs.wake_all();
    accepted(job)
}

async fn admin_snapshot(State(app): State<AppState>) -> Resp {
    accepted(crate::scheduler::enqueue_snapshot(&app).await?)
}

async fn admin_remove(State(app): State<AppState>, body: Bytes) -> Resp {
    ok(crate::admin::remove(&app, &body).await?)
}

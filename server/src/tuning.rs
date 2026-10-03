//! Progressive Tuning and baseline endpoints: the server-bound parts of `TuningContext`, request
//! assembly for the worker (map hashes included) and the light jobs behind them.

use std::collections::HashMap;

use axum::body::Bytes;
use axum::extract::{Path, Query, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::Response;
use rusqlite::{OptionalExtension, params};
use serde_json::{Value, json};

use crate::app::AppState;
use crate::error::{Error, Result};
use crate::jobs::{self, NewJob};
use crate::schema::parse_request;
use crate::util;

type Resp = Result<Response>;

fn blank(v: &Value) -> bool {
    v.as_str().is_none_or(|s| s.trim().is_empty())
}

/// `ProgressiveTuningCoordinator.CompatibleOpenTarget(evidence, target)`.
pub fn compatible_open_target(evidence: &Value, target: &Value) -> bool {
    if !target["setup_type"]
        .as_str()
        .is_some_and(|t| t.eq_ignore_ascii_case("open"))
    {
        return false;
    }
    if target["embedded_setup_available"] != true || blank(&target["setup_fingerprint"]) {
        return false;
    }
    let (ek, tk) = (
        &evidence["track_configuration_key"],
        &target["track_configuration_key"],
    );
    if blank(ek) || blank(tk) || ek != tk {
        return false;
    }
    match (evidence["car_id"].as_i64(), target["car_id"].as_i64()) {
        (Some(a), Some(b)) => a == b,
        _ => {
            let (a, b) = (&evidence["car_path"], &target["car_path"]);
            !blank(a)
                && !blank(b)
                && a.as_str()
                    .unwrap_or("")
                    .eq_ignore_ascii_case(b.as_str().unwrap_or(""))
        }
    }
}

fn key_part(v: &Value) -> String {
    match v {
        Value::Null => String::new(),
        Value::String(s) => s.trim().to_lowercase(),
        other => other.to_string().trim().to_lowercase(),
    }
}

/// Lowercase hex SHA-256 over the identity values joined by `|` (api.openapi.yaml `TuningContext`).
pub fn draft_identity_key(identity: &Value, binding: &Value) -> String {
    let sources = identity["source_sha256"]
        .as_array()
        .map(|a| {
            a.iter()
                .filter_map(|v| v.as_str())
                .collect::<Vec<_>>()
                .join(",")
        })
        .unwrap_or_default();
    let parts = [
        key_part(&identity["analysis_id"]),
        key_part(&identity["session_unique_id"]),
        key_part(&identity["subsession_id"]),
        key_part(&identity["session_type"]),
        key_part(&identity["track_configuration_key"]),
        key_part(&identity["car_id"]),
        key_part(&identity["car_path"]),
        key_part(&identity["setup_fingerprint"]),
        sources.trim().to_lowercase(),
        key_part(&binding["session_id"]),
        key_part(&binding["event_key"]),
        key_part(&binding["group_key"]),
    ];
    util::sha256_hex(parts.join("|").as_bytes())
}

struct AnalysisRow {
    session_id: i64,
    customer_id: i64,
    group_key: String,
    subsession_id: Option<i64>,
    first_sha: Option<String>,
    analysis_path: String,
    analysis_bytes: i64,
}

fn binding_of(row: &AnalysisRow) -> Value {
    let event_key = match (row.subsession_id.filter(|s| *s != 0), &row.first_sha) {
        (Some(id), _) => format!("subsession:{id}"),
        (None, Some(sha)) => format!("offline:{sha}"),
        (None, None) => format!("offline:session-{}", row.session_id),
    };
    json!({"session_id": row.session_id, "event_key": event_key, "group_key": row.group_key})
}

async fn analysis_row(app: &AppState, analysis_id: &str) -> Result<AnalysisRow> {
    let id = analysis_id.to_string();
    app.store
        .read(move |conn| {
            conn.query_row(
                "SELECT a.session_id, s.customer_id, s.group_key, s.subsession_id,
                        (SELECT MIN(sr.recording_sha256) FROM session_recordings sr WHERE sr.session_id = s.session_id),
                        a.analysis_path, a.analysis_bytes
                 FROM analyses a JOIN sessions s ON s.session_id = a.session_id WHERE a.analysis_id = ?1",
                [id],
                |r| {
                    Ok(AnalysisRow {
                        session_id: r.get(0)?,
                        customer_id: r.get(1)?,
                        group_key: r.get(2)?,
                        subsession_id: r.get(3)?,
                        first_sha: r.get(4)?,
                        analysis_path: r.get(5)?,
                        analysis_bytes: r.get(6)?,
                    })
                },
            )
            .optional()?
            .ok_or_else(|| Error::not_found("no such analysis"))
        })
        .await
}

async fn read_tuning_json(app: &AppState, analysis_id: &str) -> Result<Value> {
    let dir = crate::api::ready_view_dir(app, analysis_id).await?;
    let bytes = match tokio::fs::read(dir.join("tuning.json")).await {
        Ok(b) => b,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
            return Err(Error::not_found("this analysis has no tuning context"));
        }
        Err(e) => return Err(e.into()),
    };
    serde_json::from_slice(&bytes)
        .map_err(|e| Error::internal(format!("tuning.json is not JSON: {e}")))
}

/// `GET /analyses/{id}/tuning`.
pub async fn context(State(app): State<AppState>, Path(analysis_id): Path<String>) -> Resp {
    let projection = read_tuning_json(&app, &analysis_id).await?;
    let row = analysis_row(&app, &analysis_id).await?;
    let binding = binding_of(&row);
    let identity = projection["identity"].clone();
    let mut open_targets: Vec<Value> = Vec::new();
    let is_open = identity["setup_type"]
        .as_str()
        .is_some_and(|t| t.eq_ignore_ascii_case("open"));
    if !is_open {
        let (customer, session) = (row.customer_id, row.session_id);
        let candidates: Vec<(String, i64, String, Option<i64>, Option<String>, Option<String>)> = app
            .store
            .read(move |conn| {
                let mut stmt = conn.prepare(
                    "SELECT s.current_analysis_id, s.session_id, s.group_key, s.subsession_id, s.start_time_utc,
                            (SELECT MIN(sr.recording_sha256) FROM session_recordings sr WHERE sr.session_id = s.session_id)
                     FROM sessions s JOIN analyses a ON a.analysis_id = s.current_analysis_id
                     WHERE s.customer_id = ?1 AND s.session_id <> ?2 AND a.projection_state = 'ready'
                     ORDER BY COALESCE(s.start_time_utc, '') DESC, s.session_id DESC LIMIT 200",
                )?;
                Ok(stmt
                    .query_map(params![customer, session], |r| {
                        Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?, r.get(5)?))
                    })?
                    .collect::<rusqlite::Result<Vec<_>>>()?)
            })
            .await?;
        for (aid, session_id, group_key, subsession, start, first_sha) in candidates {
            if open_targets.len() >= 50 {
                break;
            }
            let Ok(target) = read_tuning_json(&app, &aid).await else {
                continue;
            };
            if target["eligible"] != true || !compatible_open_target(&identity, &target["identity"])
            {
                continue;
            }
            let fake = AnalysisRow {
                session_id,
                customer_id: customer,
                group_key,
                subsession_id: subsession,
                first_sha,
                analysis_path: String::new(),
                analysis_bytes: 0,
            };
            open_targets.push(json!({
                "analysis_id": aid,
                "binding": binding_of(&fake),
                "start_time_utc": start,
                "identity": target["identity"],
            }));
        }
    }
    let key = draft_identity_key(&identity, &binding);
    let mut out = projection;
    out["binding"] = binding;
    out["open_targets"] = json!(open_targets);
    out["draft_identity_key"] = json!(key);
    Ok(crate::api::json_ok(out))
}

// --------------------------------------------------- map identity hashing

const SOURCE_ALIASES: [(&str, &str); 11] = [
    ("iracing-official", "iracing-official"),
    ("official-iracing", "iracing-official"),
    ("iracing-game", "iracing-official"),
    ("iracing-hud-capture", "iracing-hud-capture"),
    ("nascar-official", "nascar-official"),
    ("official-nascar", "nascar-official"),
    ("venue-official", "venue-official"),
    ("official-track", "venue-official"),
    ("verified-manual", "verified-manual"),
    ("user-confirmed", "verified-manual"),
    ("telemetry-derived", "telemetry-derived"),
];

pub fn canonical_source_type(raw: &str) -> String {
    let lowered = raw.trim().to_lowercase();
    SOURCE_ALIASES
        .iter()
        .find(|(alias, _)| *alias == lowered)
        .map(|(_, canonical)| canonical.to_string())
        .unwrap_or(lowered)
}

/// Python `repr(float)` for a finite value.
pub fn py_float_repr(value: f64) -> String {
    if value == 0.0 {
        return if value.is_sign_negative() {
            "-0.0".into()
        } else {
            "0.0".into()
        };
    }
    let sci = format!("{:e}", value.abs());
    let (mantissa, exp) = sci.split_once('e').unwrap_or((&sci, "0"));
    let exp: i32 = exp.parse().unwrap_or(0);
    let digits: String = mantissa.chars().filter(|c| *c != '.').collect();
    let sign = if value < 0.0 { "-" } else { "" };
    if (-4..16).contains(&exp) {
        if exp >= 0 {
            let int_len = exp as usize + 1;
            let mut padded = digits.clone();
            while padded.len() < int_len {
                padded.push('0');
            }
            let (int_part, frac) = padded.split_at(int_len);
            let frac = if frac.is_empty() { "0" } else { frac };
            format!("{sign}{int_part}.{frac}")
        } else {
            format!("{sign}0.{}{digits}", "0".repeat((-exp - 1) as usize))
        }
    } else {
        let mut out = String::from(sign);
        out.push_str(&digits[..1]);
        if digits.len() > 1 {
            out.push('.');
            out.push_str(&digits[1..]);
        }
        out.push('e');
        out.push(if exp < 0 { '-' } else { '+' });
        out.push_str(&format!("{:02}", exp.abs()));
        out
    }
}

#[derive(Debug, Clone)]
struct Corner {
    corner_id: String,
    label: String,
    start: f64,
    apex: f64,
    end: f64,
    is_official: bool,
    user_verified: bool,
}

fn round6(x: f64) -> f64 {
    format!("{x:.6}").parse().unwrap_or(x)
}

/// `tuning_engine._normalized_corner`.
fn normalize_corner(raw: &Value) -> std::result::Result<Corner, String> {
    let corner_id = raw["corner_id"].as_str().unwrap_or("").trim().to_string();
    let label = raw["label"]
        .as_str()
        .or(raw["corner_label"].as_str())
        .unwrap_or("")
        .trim()
        .to_string();
    let (start, apex, end) = (
        raw["start_pct"].as_f64(),
        raw["apex_pct"].as_f64(),
        raw["end_pct"].as_f64(),
    );
    let (Some(start), Some(apex), Some(end)) = (start, apex, end) else {
        return Err(
            "Every verified turn requires corner_id, label, start_pct, apex_pct, and end_pct."
                .into(),
        );
    };
    if corner_id.is_empty() || label.is_empty() {
        return Err(
            "Every verified turn requires corner_id, label, start_pct, apex_pct, and end_pct."
                .into(),
        );
    }
    if ![start, apex, end].iter().all(|v| (0.0..1.0).contains(v)) {
        return Err("Turn lap fractions must be finite values in [0, 1).".into());
    }
    let eps = 0.000001;
    let forward = (end - start).rem_euclid(1.0);
    let apex_distance = (apex - start).rem_euclid(1.0);
    if forward <= eps || apex_distance <= eps || apex_distance >= forward - eps {
        return Err(
            "Turn bounds must form a non-degenerate forward start-to-apex-to-end arc.".into(),
        );
    }
    Ok(Corner {
        corner_id,
        label,
        start: round6(start),
        apex: round6(apex),
        end: round6(end),
        is_official: raw["is_official"].as_bool().unwrap_or(false),
        user_verified: raw["user_verified"]
            .as_bool()
            .or(raw["verified"].as_bool())
            .unwrap_or(false),
    })
}

fn json_str(s: &str) -> String {
    serde_json::to_string(s).unwrap_or_else(|_| "\"\"".into())
}

/// `tuning_engine.map_annotation_hash` (sha256 of Python's compact sorted JSON).
pub fn map_annotation_hash(
    submission: &Value,
    geometry_hash: &str,
) -> std::result::Result<String, String> {
    let mut corners: Vec<Corner> = submission["corners"]
        .as_array()
        .map(|a| {
            a.iter()
                .filter(|c| c.is_object())
                .map(normalize_corner)
                .collect::<std::result::Result<_, _>>()
        })
        .transpose()?
        .unwrap_or_default();
    corners.sort_by(|a, b| {
        a.start
            .partial_cmp(&b.start)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then(
                a.apex
                    .partial_cmp(&b.apex)
                    .unwrap_or(std::cmp::Ordering::Equal),
            )
            .then(
                a.end
                    .partial_cmp(&b.end)
                    .unwrap_or(std::cmp::Ordering::Equal),
            )
            .then(a.corner_id.cmp(&b.corner_id))
    });
    let corner_text: Vec<String> = corners
        .iter()
        .map(|c| {
            format!(
                "{{\"apex_pct\":{},\"corner_id\":{},\"end_pct\":{},\"is_official\":{},\"label\":{},\"start_pct\":{},\"user_verified\":{}}}",
                py_float_repr(c.apex),
                json_str(&c.corner_id),
                py_float_repr(c.end),
                c.is_official,
                json_str(&c.label),
                py_float_repr(c.start),
                c.user_verified
            )
        })
        .collect();
    let text = format!(
        "{{\"corners\":[{}],\"geometry_hash\":{},\"source_type\":{},\"track_configuration_key\":{}}}",
        corner_text.join(","),
        json_str(&geometry_hash.to_lowercase()),
        json_str(&canonical_source_type(
            submission["source_type"].as_str().unwrap_or("")
        )),
        json_str(submission["track_configuration_key"].as_str().unwrap_or(""))
    );
    Ok(util::sha256_hex(text.as_bytes()))
}

fn is_hex64(text: &str) -> bool {
    text.len() == 64 && text.bytes().all(|c| c.is_ascii_hexdigit())
}

async fn resolve_path(app: &AppState, customer: i64, analysis_id: &str) -> Result<(String, i64)> {
    let row = analysis_row(app, analysis_id).await?;
    if row.customer_id != customer {
        return Err(Error::not_found("no such analysis for this driver"));
    }
    Ok((row.analysis_path, row.analysis_bytes))
}

async fn enqueue(app: &AppState, job: NewJob) -> Result<Value> {
    let value = app
        .store
        .write(move |conn| {
            let tx = conn.transaction()?;
            let (id, _) = jobs::enqueue_tx(&tx, &job, false)?;
            let value = jobs::job_ref(&tx, id)?;
            tx.commit()?;
            Ok(value)
        })
        .await?;
    app.jobs.wake_all();
    Ok(value)
}

/// `POST /analyses/{id}/tuning/recommendations`.
pub async fn recommend(
    State(app): State<AppState>,
    Path(analysis_id): Path<String>,
    body: Bytes,
) -> Resp {
    let request = parse_request("TuningRecommendRequest", &body)?;
    let projection = read_tuning_json(&app, &analysis_id).await?;
    let row = analysis_row(&app, &analysis_id).await?;
    let is_open = projection["identity"]["setup_type"]
        .as_str()
        .is_some_and(|t| t.eq_ignore_ascii_case("open"));
    let mut bytes = row.analysis_bytes;
    let mut arguments = json!({"analysis_path": row.analysis_path});
    match request["open_target_analysis_id"].as_str() {
        Some(target_id) => {
            let (path, size) = resolve_path(&app, row.customer_id, target_id).await?;
            arguments["open_target_analysis_path"] = json!(path);
            bytes += size;
        }
        None if !is_open => {
            return Err(Error::unprocessable(
                "open_target_required",
                "a fixed-setup analysis needs an open target analysis to receive the recommendation",
            ));
        }
        None => {}
    }
    let geometry_hash = projection["map"]["geometry_hash"]
        .as_str()
        .unwrap_or("")
        .to_string();
    if !is_hex64(&geometry_hash) {
        return Err(Error::unprocessable(
            "geometry_hash_missing",
            "the analysis map has no geometry hash",
        ));
    }
    let submission = &request["map_identity"];
    let annotation = map_annotation_hash(submission, &geometry_hash)
        .map_err(|m| Error::unprocessable("invalid_map", m))?;
    let mut map_identity = submission.clone();
    map_identity["geometry_hash"] = json!(geometry_hash.to_lowercase());
    map_identity["annotation_hash"] = json!(annotation);
    map_identity["source_type"] = json!(canonical_source_type(
        submission["source_type"].as_str().unwrap_or("")
    ));
    arguments["map_identity"] = map_identity;
    arguments["feedback"] = request["feedback"].clone();
    arguments["representative_run_ids"] = request
        .get("representative_run_ids")
        .cloned()
        .unwrap_or(json!([]));
    for key in ["ruleset_id", "goal", "generic_note"] {
        if let Some(v) = request.get(key).filter(|v| !v.is_null()) {
            arguments[key] = v.clone();
        }
    }
    arguments["maximum_changes"] = json!(1);
    let estimate = (bytes.max(0) as u64) * 10;
    let subject = json!({
        "arguments": arguments, "needs_setups": false, "bumps_generation": true,
        "estimate_bytes": estimate.max(256 << 20),
    });
    let job =
        NewJob::new("tuning_recommend", "light", Some(row.customer_id), subject).interactive();
    Ok(crate::api::json_status(
        StatusCode::ACCEPTED,
        enqueue(&app, job).await?,
    ))
}

/// `POST /drivers/{id}/tuning/experiments/{experiment_id}/outcome`.
pub async fn outcome(
    State(app): State<AppState>,
    Path((customer, experiment_id)): Path<(i64, String)>,
    body: Bytes,
) -> Resp {
    let request = parse_request("TuningOutcomeRequest", &body)?;
    crate::api::require_driver(&app, customer).await?;
    let mut arguments = json!({
        "experiment_id": experiment_id,
        "outcome": request["outcome"],
        "notes": request["notes"],
    });
    if let Some(result) = request["result_analysis_id"].as_str() {
        let (path, _) = resolve_path(&app, customer, result).await?;
        arguments["result_analysis_path"] = json!(path);
    }
    let subject = json!({"arguments": arguments, "needs_setups": false, "bumps_generation": true});
    let job = NewJob::new("tuning_outcome", "light", Some(customer), subject).interactive();
    Ok(crate::api::json_status(
        StatusCode::ACCEPTED,
        enqueue(&app, job).await?,
    ))
}

/// `GET /drivers/{id}/tuning/history?analysis_id=`.
pub async fn history(
    State(app): State<AppState>,
    Path(customer): Path<i64>,
    Query(q): Query<HashMap<String, String>>,
) -> Resp {
    let Some(analysis_id) = q.get("analysis_id") else {
        return Err(Error::invalid("analysis_id is required"));
    };
    crate::api::require_driver(&app, customer).await?;
    let (path, bytes) = resolve_path(&app, customer, analysis_id).await?;
    let subject = json!({
        "arguments": {"analysis_path": path, "limit": 100},
        "needs_setups": false,
        "estimate_bytes": ((bytes.max(0) as u64) * 10).max(256 << 20),
    });
    let job = NewJob::new("tuning_history", "light", Some(customer), subject).interactive();
    Ok(crate::api::json_status(
        StatusCode::ACCEPTED,
        enqueue(&app, job).await?,
    ))
}

/// `POST /drivers/{id}/baseline` (Find a baseline).
pub async fn baseline(
    State(app): State<AppState>,
    Path(customer): Path<i64>,
    _headers: HeaderMap,
    body: Bytes,
) -> Resp {
    let request = parse_request("BaselineRequest", &body)?;
    crate::api::require_driver(&app, customer).await?;
    let mut arguments = json!({
        "season": request["season"], "car": request["car"], "track": request["track"],
    });
    let mut bytes = 0;
    if let Some(aid) = request["analysis_id"].as_str() {
        let (path, size) = resolve_path(&app, customer, aid).await?;
        arguments["analysis_path"] = json!(path);
        bytes = size;
    }
    if let Some(text) = request["track_characteristics"].as_str() {
        arguments["track_characteristics"] = json!(text);
    }
    let subject = json!({
        "arguments": arguments, "needs_setups": true,
        "estimate_bytes": ((bytes.max(0) as u64) * 10).max(256 << 20),
    });
    let job = NewJob::new("baseline", "light", Some(customer), subject).interactive();
    Ok(crate::api::json_status(
        StatusCode::ACCEPTED,
        enqueue(&app, job).await?,
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn python_float_repr() {
        assert_eq!(py_float_repr(0.5), "0.5");
        assert_eq!(py_float_repr(0.0), "0.0");
        assert_eq!(py_float_repr(1e-6), "1e-06");
        assert_eq!(py_float_repr(1.5e-5), "1.5e-05");
        assert_eq!(py_float_repr(0.0001), "0.0001");
        assert_eq!(py_float_repr(0.123456), "0.123456");
        assert_eq!(py_float_repr(12.0), "12.0");
        assert_eq!(py_float_repr(1e16), "1e+16");
        assert_eq!(py_float_repr(-0.25), "-0.25");
    }

    #[test]
    fn source_aliases() {
        assert_eq!(canonical_source_type(" User-Confirmed "), "verified-manual");
        assert_eq!(
            canonical_source_type("official-iracing"),
            "iracing-official"
        );
        assert_eq!(canonical_source_type("other"), "other");
    }

    #[test]
    fn corner_arc_rules() {
        assert!(normalize_corner(&json!({"corner_id":"t1","label":"Turn 1","start_pct":0.1,"apex_pct":0.15,"end_pct":0.2})).is_ok());
        // apex outside the arc
        assert!(normalize_corner(&json!({"corner_id":"t1","label":"Turn 1","start_pct":0.1,"apex_pct":0.3,"end_pct":0.2})).is_err());
        // wrap-around arc is allowed
        assert!(normalize_corner(&json!({"corner_id":"t1","label":"Turn 1","start_pct":0.95,"apex_pct":0.98,"end_pct":0.05})).is_ok());
    }

    #[test]
    fn open_target_rules() {
        let evidence = json!({"track_configuration_key": "a", "car_id": 3, "car_path": "x"});
        let good = json!({"setup_type": "open", "embedded_setup_available": true, "setup_fingerprint": "f",
                          "track_configuration_key": "a", "car_id": 3});
        assert!(compatible_open_target(&evidence, &good));
        let fixed = json!({"setup_type": "Fixed", "embedded_setup_available": true, "setup_fingerprint": "f",
                           "track_configuration_key": "a", "car_id": 3});
        assert!(!compatible_open_target(&evidence, &fixed));
        let other_car = json!({"setup_type": "Open", "embedded_setup_available": true, "setup_fingerprint": "f",
                               "track_configuration_key": "a", "car_id": 4});
        assert!(!compatible_open_target(&evidence, &other_car));
    }
}

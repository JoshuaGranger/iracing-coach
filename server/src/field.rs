//! Durable field sidecars. No telemetry math: validate, associate and stage raw evidence.
use crate::app::AppState;
use crate::error::{Error, Result};
use crate::jobs::{QueueParams, queue_analysis_tx};
use rusqlite::{Connection, params};
use serde_json::{Value, json};
use std::fs::File;
use std::io::{BufRead, BufReader, Read};
use std::path::Path;

#[derive(Debug, Clone, PartialEq)]
pub struct Metadata {
    pub customer: i64,
    pub subsession: Option<i64>,
    pub phase: i64,
    pub count: i64,
    pub first: f64,
    pub last: f64,
}

pub fn inspect(path: &Path) -> Result<Metadata> {
    if path.metadata()?.len() > 64 * 1024 * 1024 {
        return Err(Error::invalid("field segment exceeds 64 MiB"));
    }
    let mut reader = BufReader::new(File::open(path)?);
    let mut meta: Option<Metadata> = None;
    let mut identity = None;
    let mut last_update = None;
    let mut first_capture = None;
    let mut last_capture = 0;
    loop {
        let mut line = Vec::new();
        let len = reader
            .by_ref()
            .take(1024 * 1024 + 1)
            .read_until(b'\n', &mut line)?;
        if len == 0 {
            break;
        }
        if len > 1024 * 1024 || line.last() != Some(&b'\n') {
            return Err(Error::invalid("field row is oversized or incomplete"));
        }
        let row: Value = serde_json::from_slice(&line)
            .map_err(|e| Error::invalid(format!("invalid field JSON: {e}")))?;
        if row["schema_version"].as_i64() != Some(1)
            || row["captured_unix_ms"].as_u64().is_none()
            || row["tick"].as_u64().is_none()
            || row["session_info_update"].as_i64().is_none()
        {
            return Err(Error::invalid(
                "invalid field schema/version or required capture fields",
            ));
        }
        let capture = row["captured_unix_ms"].as_u64().unwrap();
        let first = *first_capture.get_or_insert(capture);
        if capture < last_capture || capture.saturating_sub(first) > 60_000 {
            return Err(Error::invalid(
                "field segment capture order/span is invalid",
            ));
        }
        last_capture = capture;
        let update = row["session_info_update"].as_i64();
        if update != last_update && !row.get("session_info").is_some_and(Value::is_string) {
            return Err(Error::invalid(
                "changed field session info requires a fresh snapshot",
            ));
        }
        last_update = update;
        if let Some(raw) = row.get("session_info") {
            let info = coach_ibt::SessionInfo::parse(
                raw.as_str()
                    .ok_or_else(|| Error::invalid("invalid field session info"))?,
            )
            .map_err(|e| Error::invalid(format!("invalid field session info: {e}")))?;
            let driver = info
                .driver()
                .ok_or_else(|| Error::invalid("field recording has no player"))?;
            identity = Some((
                driver
                    .user_id
                    .filter(|id| *id > 0)
                    .ok_or_else(|| Error::invalid("field player has no UserID"))?,
                info.weekend().subsession_id.filter(|id| *id > 0),
            ));
        }
        let (customer, subsession) =
            identity.ok_or_else(|| Error::invalid("field segment must start with session info"))?;
        let channels = row["channels"]
            .as_object()
            .ok_or_else(|| Error::invalid("field channels must be an object"))?;
        let phase = channels
            .get("SessionNum")
            .and_then(Value::as_i64)
            .filter(|n| *n >= 0)
            .ok_or_else(|| Error::invalid("field SessionNum is missing"))?;
        let time = channels
            .get("SessionTime")
            .and_then(Value::as_f64)
            .filter(|n| n.is_finite() && *n >= 0.)
            .ok_or_else(|| Error::invalid("field SessionTime is missing"))?;
        for (name, value) in channels {
            if !matches!(
                name.as_str(),
                "SessionTime"
                    | "SessionNum"
                    | "SessionUniqueID"
                    | "SessionState"
                    | "SessionFlags"
                    | "PlayerCarIdx"
                    | "CarIdxLap"
                    | "CarIdxLapCompleted"
                    | "CarIdxLapDistPct"
                    | "CarIdxPosition"
                    | "CarIdxClassPosition"
                    | "CarIdxOnPitRoad"
                    | "CarIdxTrackSurface"
                    | "CarIdxLastLapTime"
                    | "CarIdxBestLapTime"
            ) {
                continue;
            }
            if value.is_null() {
                continue;
            }
            if name.starts_with("CarIdx") {
                let array = value
                    .as_array()
                    .filter(|a| a.len() <= 64)
                    .ok_or_else(|| Error::invalid("invalid field car array"))?;
                for cell in array {
                    if cell.is_null() {
                        continue;
                    }
                    let valid = match name.as_str() {
                        "CarIdxOnPitRoad" => cell.is_boolean(),
                        "CarIdxLapDistPct" | "CarIdxLastLapTime" | "CarIdxBestLapTime" => {
                            cell.as_f64().is_some_and(f64::is_finite)
                        }
                        _ => cell.as_i64().is_some(),
                    };
                    if !valid {
                        return Err(Error::invalid("invalid field channel value"));
                    }
                }
            } else if name != "SessionTime" && value.as_i64().is_none() {
                return Err(Error::invalid("invalid field scalar"));
            }
        }
        match &mut meta {
            Some(m) => {
                if (m.customer, m.subsession, m.phase) != (customer, subsession, phase) {
                    return Err(Error::invalid("field segment crosses session identity"));
                }
                m.count += 1;
                m.last = time;
            }
            None => {
                meta = Some(Metadata {
                    customer,
                    subsession,
                    phase,
                    count: 1,
                    first: time,
                    last: time,
                })
            }
        }
    }
    meta.ok_or_else(|| Error::invalid("empty field segment"))
}

pub fn members(conn: &Connection, session: i64) -> rusqlite::Result<Vec<(String, u64)>> {
    conn.prepare("SELECT f.sha256, b.size FROM field_recordings f JOIN blobs b ON b.sha256=f.sha256
        JOIN sessions s ON s.customer_id=f.customer_id AND s.subsession_id=f.subsession_id AND s.sim_session_num=f.session_num
        WHERE s.session_id=?1 AND f.subsession_id > 0 ORDER BY f.sha256")?
        .query_map([session], |r| Ok((r.get(0)?, r.get::<_, i64>(1)? as u64)))?.collect()
}

pub async fn index(app: &AppState, sha: &str, path: &Path) -> Result<Value> {
    let path = path.to_owned();
    let m = tokio::task::spawn_blocking(move || inspect(&path))
        .await
        .map_err(|e| Error::internal(e.to_string()))??;
    let manifest = json!({"driver":{"customer_id":m.customer}, "field_recording":{
        "customer_id":m.customer,"subsession_id":m.subsession,"session_num":m.phase,"sample_count":m.count,
        "first_time":m.first,"last_time":m.last}});
    let sha = sha.to_owned();
    let queue = QueueParams::of(app);
    app.store.write(move |conn| {
        let tx = conn.transaction()?;
        crate::db::upsert_driver(&tx, m.customer, None, None)?;
        let added = tx.execute("INSERT OR IGNORE INTO field_recordings VALUES (?1,?2,?3,?4,?5,?6,?7)",
            params![sha,m.customer,m.subsession,m.phase,m.count,m.first,m.last])?;
        if added > 0 {
            let ids = tx.prepare("SELECT session_id FROM sessions WHERE customer_id=?1 AND subsession_id=?2 AND sim_session_num=?3 AND subsession_id>0")?
                .query_map(params![m.customer,m.subsession,m.phase], |r| r.get::<_,i64>(0))?.collect::<rusqlite::Result<Vec<_>>>()?;
            for id in ids { queue_analysis_tx(&tx, &queue, id, true, false)?; }
        }
        tx.commit()?; Ok(())
    }).await?;
    app.jobs.wake_all();
    Ok(manifest)
}

pub async fn stage(app: &AppState, job: &Value, root: &Path) -> Result<()> {
    let Some(items) = job["field_recordings"].as_array() else {
        return Ok(());
    };
    let dir = root.join("field");
    std::fs::create_dir_all(&dir)?;
    for item in items {
        let sha = item["sha256"]
            .as_str()
            .filter(|s| crate::util::is_sha256_hex(s))
            .ok_or_else(|| Error::invalid("invalid field member hash"))?;
        let path = crate::ingest::ready_path(app, sha);
        if !path.exists() {
            crate::ingest::restage_recording(app, sha).await?;
        }
        let dest = dir.join(format!("{sha}.field.jsonl"));
        if std::fs::hard_link(&path, &dest).is_err() {
            std::fs::copy(&path, &dest)?;
        }
    }
    Ok(())
}

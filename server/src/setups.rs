//! Setup library assembly (D9): setups from recordings, garage capture and `.htm` exports,
//! `.sto` files linked by name and hash, files whose values are unknown.

use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::sync::OnceLock;

use regex::Regex;
use rusqlite::{Connection, OptionalExtension, params};
use serde_json::{Value, json};

use crate::error::{Error, Result};
use crate::util::iso_now;

/// Inserts or refreshes a setup (distinct values fingerprint per driver and car).
pub fn upsert_setup(
    conn: &Connection,
    customer_id: i64,
    car_path: &str,
    fingerprint: &str,
    values: &BTreeMap<String, String>,
    observed_at: &str,
) -> rusqlite::Result<i64> {
    let values_json = serde_json::to_string(values).unwrap_or_else(|_| "{}".into());
    conn.execute(
        "INSERT INTO setups(customer_id, car_path, values_fingerprint, values_json, first_seen_at, last_seen_at)
         VALUES (?1, ?2, ?3, ?4, ?5, ?5)
         ON CONFLICT(customer_id, car_path, values_fingerprint) DO UPDATE SET
           first_seen_at = MIN(first_seen_at, excluded.first_seen_at),
           values_json = CASE WHEN excluded.last_seen_at >= last_seen_at THEN excluded.values_json ELSE values_json END,
           last_seen_at = MAX(last_seen_at, excluded.last_seen_at)",
        params![customer_id, car_path, fingerprint, values_json, observed_at],
    )?;
    conn.query_row(
        "SELECT setup_id FROM setups WHERE customer_id = ?1 AND car_path = ?2 AND values_fingerprint = ?3",
        params![customer_id, car_path, fingerprint],
        |r| r.get(0),
    )
}

/// Keys that do not count toward setup identity (`setup-keys.json`).
pub fn is_identity_excluded(key: &str) -> bool {
    static PATTERNS: OnceLock<Vec<Regex>> = OnceLock::new();
    let patterns = PATTERNS.get_or_init(|| {
        coach_wire::setup_keys::EXCLUDED_LEAF_PATTERNS
            .iter()
            .filter_map(|(pattern, _)| {
                regex::RegexBuilder::new(pattern)
                    .case_insensitive(true)
                    .build()
                    .ok()
            })
            .collect()
    });
    let leaf = key.rsplit('.').next().unwrap_or(key);
    patterns.iter().any(|p| p.is_match(leaf))
}

/// What the server did with one garage event.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum GarageOutcome {
    /// Its values are stored as an observation for this driver (a replay counts): acknowledge.
    Stored(i64),
    /// It cannot be attributed yet (driver, car or values missing): leave it unacknowledged so the
    /// collector resends it after a later `welcome` (`collector-protocol.md` section 8).
    Unattributed(&'static str),
}

/// The event's own driver, else the collector's last reported driver.
fn resolve_garage_customer(
    conn: &Connection,
    msg: &Value,
    collector_id: &str,
) -> rusqlite::Result<Option<i64>> {
    if let Some(id) = msg["driver"]["customer_id"].as_i64() {
        crate::db::upsert_driver(conn, id, msg["driver"]["name"].as_str(), None)?;
        return Ok(Some(id));
    }
    Ok(conn
        .query_row(
            "SELECT last_customer_id FROM collectors WHERE collector_id = ?1",
            [collector_id],
            |r| r.get(0),
        )
        .optional()?
        .flatten())
}

/// The event's own car, else the car its visit already carries.
fn resolve_garage_car(conn: &Connection, msg: &Value) -> rusqlite::Result<Option<String>> {
    if let Some(car) = msg["car_path"].as_str() {
        return Ok(Some(car.to_string()));
    }
    Ok(conn
        .query_row(
            "SELECT car_path FROM garage_visits WHERE visit_id = ?1",
            [msg["visit_id"].as_str().unwrap_or_default()],
            |r| r.get(0),
        )
        .optional()?
        .flatten())
}

fn values_of(msg: &Value) -> BTreeMap<String, String> {
    msg["values"]
        .as_object()
        .map(|m| {
            m.iter()
                .filter_map(|(k, v)| v.as_str().map(|s| (k.clone(), s.to_string())))
                .collect()
        })
        .unwrap_or_default()
}

fn ensure_visit(
    conn: &Connection,
    msg: &Value,
    collector_id: &str,
    customer: Option<i64>,
) -> rusqlite::Result<()> {
    let visit = msg["visit_id"].as_str().unwrap_or_default();
    conn.execute(
        "INSERT INTO garage_visits(visit_id, collector_id, customer_id, subsession_id, session_num, car_path, entered_at)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)
         ON CONFLICT(visit_id) DO UPDATE SET
           customer_id = COALESCE(customer_id, ?3), car_path = COALESCE(car_path, ?6)",
        params![
            visit,
            collector_id,
            customer,
            msg["session"]["subsession_id"].as_i64(),
            msg["session"]["session_num"].as_i64(),
            msg["car_path"].as_str(),
            msg["entered_at"].as_str().or(msg["observed_at"].as_str()),
        ],
    )?;
    Ok(())
}

/// `garage.snapshot`: one value snapshot per `(visit_id, update_count)`; a replay inserts nothing.
pub fn record_garage_snapshot(
    conn: &Connection,
    msg: &Value,
    collector_id: &str,
    fingerprint: &dyn Fn(&BTreeMap<String, String>) -> String,
) -> Result<GarageOutcome> {
    let visit = msg["visit_id"].as_str().unwrap_or_default().to_string();
    let update_count = msg["update_count"].as_i64();
    let customer = resolve_garage_customer(conn, msg, collector_id)?;
    ensure_visit(conn, msg, collector_id, customer)?;
    let Some(customer) = customer else {
        return Ok(GarageOutcome::Unattributed("driver"));
    };
    let Some(car_path) = resolve_garage_car(conn, msg)? else {
        return Ok(GarageOutcome::Unattributed("car"));
    };
    let values = values_of(msg);
    let observed = msg["observed_at"]
        .as_str()
        .map(str::to_string)
        .unwrap_or_else(iso_now);
    let setup_id = upsert_setup(
        conn,
        customer,
        &car_path,
        &fingerprint(&values),
        &values,
        &observed,
    )?;
    conn.execute(
        "INSERT OR IGNORE INTO setup_observations(setup_id, customer_id, source, visit_id, update_count, observed_at)
         VALUES (?1, ?2, 'garage_snapshot', ?3, ?4, ?5)",
        params![setup_id, customer, visit, update_count, observed],
    )?;
    Ok(GarageOutcome::Stored(customer))
}

/// `garage.commit`: the visit's last snapshot with the name and `.sto` hash iRacing published at
/// exit. A modified commit never defines the named file's values (links are derived only from
/// observations with `is_modified = 0`).
pub fn record_garage_commit(
    conn: &Connection,
    msg: &Value,
    collector_id: &str,
    fingerprint: &dyn Fn(&BTreeMap<String, String>) -> String,
) -> Result<GarageOutcome> {
    let visit = msg["visit_id"].as_str().unwrap_or_default().to_string();
    let customer = resolve_garage_customer(conn, msg, collector_id)?;
    ensure_visit(conn, msg, collector_id, customer)?;
    let Some(customer) = customer else {
        return Ok(GarageOutcome::Unattributed("driver"));
    };
    let Some(car_path) = resolve_garage_car(conn, msg)? else {
        return Ok(GarageOutcome::Unattributed("car"));
    };
    let mut values = values_of(msg);
    if values.is_empty()
        && let Some(count) = msg["update_count"].as_i64()
    {
        // a commit may reference its last snapshot instead of repeating the values
        let json: Option<String> = conn
            .query_row(
                "SELECT s.values_json FROM setup_observations o JOIN setups s ON s.setup_id = o.setup_id
                 WHERE o.visit_id = ?1 AND o.update_count = ?2 AND o.source = 'garage_snapshot'",
                params![visit, count],
                |r| r.get(0),
            )
            .optional()?;
        if let Some(json) = json {
            values = serde_json::from_str(&json).unwrap_or_default();
        }
    }
    if values.is_empty() {
        // The snapshot it names is not stored yet (it was not attributable); the resend of both
        // events, oldest first, stores them in order.
        return Ok(GarageOutcome::Unattributed("values"));
    }
    conn.execute(
        "UPDATE garage_visits SET exited_at = ?2, committed_name = ?3, committed_sto_sha256 = ?4,
           ambiguous_name = ?5, entered_at = COALESCE(entered_at, ?6) WHERE visit_id = ?1",
        params![
            visit,
            msg["exited_at"].as_str(),
            msg["setup_name"].as_str(),
            msg["sto_sha256"].as_str(),
            msg["ambiguous"].as_bool().unwrap_or(false) as i64,
            msg["entered_at"].as_str(),
        ],
    )?;
    let observed = msg["exited_at"]
        .as_str()
        .map(str::to_string)
        .unwrap_or_else(iso_now);
    let setup_id = upsert_setup(
        conn,
        customer,
        &car_path,
        &fingerprint(&values),
        &values,
        &observed,
    )?;
    conn.execute(
        "INSERT OR IGNORE INTO setup_observations(setup_id, customer_id, source, visit_id, setup_name, sto_sha256,
           is_modified, load_type, update_count, observed_at)
         VALUES (?1, ?2, 'garage_commit', ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
        params![
            setup_id,
            customer,
            visit,
            msg["setup_name"].as_str(),
            msg["sto_sha256"].as_str(),
            msg["is_modified"].as_bool().map(|b| b as i64),
            msg["load_type"].as_str(),
            msg["update_count"].as_i64(),
            observed
        ],
    )?;
    Ok(GarageOutcome::Stored(customer))
}

#[derive(Debug, Clone)]
struct FileRow {
    sha: String,
    car_folder: String,
    relative_path: String,
    format: String,
    present: bool,
}

#[derive(Debug, Clone)]
struct ObsRow {
    setup_id: i64,
    source: String,
    blob: Option<String>,
    visit: Option<String>,
    name: Option<String>,
    sto: Option<String>,
    modified: Option<i64>,
    load_type: Option<String>,
    observed_at: String,
    session_id: Option<i64>,
    session_type: Option<String>,
    /// The session's current analysis (recording observations).
    analysis_id: Option<String>,
    /// The visit's commit could not tell which same-named file was loaded.
    ambiguous: bool,
}

fn base_name(relative_path: &str) -> &str {
    relative_path.rsplit('/').next().unwrap_or(relative_path)
}

fn load_files(conn: &Connection, customer: i64) -> rusqlite::Result<Vec<FileRow>> {
    let mut stmt = conn.prepare(
        "SELECT blob_sha256, car_folder, relative_path, format, present FROM setup_files
         WHERE customer_id = ?1 ORDER BY car_folder, relative_path, last_seen_at DESC",
    )?;
    let rows = stmt
        .query_map([customer], |r| {
            Ok(FileRow {
                sha: r.get(0)?,
                car_folder: r.get(1)?,
                relative_path: r.get(2)?,
                format: r.get(3)?,
                present: r.get::<_, i64>(4)? != 0,
            })
        })?
        .collect::<rusqlite::Result<_>>()?;
    Ok(rows)
}

fn load_observations(conn: &Connection, customer: i64) -> rusqlite::Result<Vec<ObsRow>> {
    let mut stmt = conn.prepare(
        "SELECT o.setup_id, o.source, o.recording_sha256, o.blob_sha256, o.visit_id, o.setup_name,
                o.sto_sha256, o.is_modified, o.load_type, o.observed_at,
                (SELECT s.session_id FROM session_recordings sr JOIN sessions s ON s.session_id = sr.session_id
                   WHERE sr.recording_sha256 = o.recording_sha256 LIMIT 1),
                (SELECT s.session_type FROM session_recordings sr JOIN sessions s ON s.session_id = sr.session_id
                   WHERE sr.recording_sha256 = o.recording_sha256 LIMIT 1),
                (SELECT s.current_analysis_id FROM session_recordings sr JOIN sessions s ON s.session_id = sr.session_id
                   WHERE sr.recording_sha256 = o.recording_sha256 LIMIT 1),
                COALESCE((SELECT v.ambiguous_name FROM garage_visits v WHERE v.visit_id = o.visit_id), 0)
         FROM setup_observations o WHERE o.customer_id = ?1 ORDER BY o.observed_at DESC, o.observation_id DESC",
    )?;
    let rows = stmt
        .query_map([customer], |r| {
            Ok(ObsRow {
                setup_id: r.get(0)?,
                source: r.get(1)?,
                blob: r.get(3)?,
                visit: r.get(4)?,
                name: r.get(5)?,
                sto: r.get(6)?,
                modified: r.get(7)?,
                load_type: r.get(8)?,
                observed_at: r.get(9)?,
                session_id: r.get(10)?,
                session_type: r.get(11)?,
                analysis_id: r.get(12)?,
                ambiguous: r.get::<_, i64>(13)? != 0,
            })
        })?
        .collect::<rusqlite::Result<_>>()?;
    Ok(rows)
}

/// The setup a file's values belong to, derived from observations (never from a modified one):
/// an unmodified garage commit or recording naming the file, or an `.htm` export of the file.
fn linked_setup(file: &FileRow, car_path_of: &HashMap<i64, String>, obs: &[ObsRow]) -> Option<i64> {
    obs.iter()
        .filter(|o| match o.source.as_str() {
            "htm_export" => o.blob.as_deref() == Some(file.sha.as_str()),
            "garage_commit" | "recording" if o.modified == Some(0) => {
                if o.sto.as_deref() == Some(file.sha.as_str()) {
                    return true;
                }
                // An ambiguous visit withheld the hash because it could not tell which file was
                // loaded, so its name proves nothing about any one file.
                o.sto.is_none()
                    && !o.ambiguous
                    && file.format == "sto"
                    && o.name
                        .as_deref()
                        .is_some_and(|n| n.eq_ignore_ascii_case(base_name(&file.relative_path)))
                    && car_path_of
                        .get(&o.setup_id)
                        .is_some_and(|c| c.eq_ignore_ascii_case(&file.car_folder))
            }
            _ => false,
        })
        .map(|o| o.setup_id)
        .next()
}

fn file_entry(file: &FileRow, linked: Option<i64>) -> Value {
    json!({
        "sha256": file.sha,
        "car_folder": file.car_folder,
        "relative_path": file.relative_path,
        "format": file.format,
        "present": file.present,
        "linked_setup_id": linked,
    })
}

struct SetupRow {
    setup_id: i64,
    customer_id: i64,
    car_path: String,
    values_json: String,
    first_seen_at: String,
    last_seen_at: String,
}

fn summarize(
    setup: &SetupRow,
    car_name: Option<String>,
    files: &[FileRow],
    obs: &[ObsRow],
    car_path_of: &HashMap<i64, String>,
) -> Value {
    let mine: Vec<&ObsRow> = obs
        .iter()
        .filter(|o| o.setup_id == setup.setup_id)
        .collect();
    let mut sources: BTreeSet<&str> = BTreeSet::new();
    for o in &mine {
        sources.insert(match o.source.as_str() {
            "recording" => "recording",
            "garage_snapshot" => "garage_snapshot",
            "garage_commit" => "garage_commit",
            _ => "htm_export",
        });
    }
    let mut names: BTreeSet<String> = BTreeSet::new();
    let mut sto_files = Vec::new();
    for f in files {
        if linked_setup(f, car_path_of, obs) == Some(setup.setup_id) {
            if f.format == "sto" {
                names.insert(base_name(&f.relative_path).to_string());
            }
            sto_files.push(file_entry(f, Some(setup.setup_id)));
        }
    }
    let raced = mine.iter().any(|o| {
        o.source == "recording"
            && o.session_type
                .as_deref()
                .is_some_and(|t| t.to_lowercase().starts_with("race"))
    });
    json!({
        "setup_id": setup.setup_id,
        "customer_id": setup.customer_id,
        "car_path": setup.car_path,
        "car_name": car_name,
        "names": names.into_iter().collect::<Vec<_>>(),
        "sto_files": sto_files,
        "sources": sources.into_iter().collect::<Vec<_>>(),
        "observation_count": mine.len(),
        "raced": raced,
        "first_seen_at": setup.first_seen_at,
        "last_seen_at": setup.last_seen_at,
    })
}

fn load_setups(conn: &Connection, customer: i64) -> rusqlite::Result<Vec<SetupRow>> {
    let mut stmt = conn.prepare(
        "SELECT setup_id, customer_id, car_path, values_json, first_seen_at, last_seen_at FROM setups
         WHERE customer_id = ?1 ORDER BY last_seen_at DESC, setup_id DESC",
    )?;
    let rows = stmt
        .query_map([customer], |r| {
            Ok(SetupRow {
                setup_id: r.get(0)?,
                customer_id: r.get(1)?,
                car_path: r.get(2)?,
                values_json: r.get(3)?,
                first_seen_at: r.get(4)?,
                last_seen_at: r.get(5)?,
            })
        })?
        .collect::<rusqlite::Result<_>>()?;
    Ok(rows)
}

fn car_name(conn: &Connection, customer: i64, car_path: &str) -> rusqlite::Result<Option<String>> {
    conn.query_row(
        "SELECT car_name FROM recordings WHERE customer_id = ?1 AND car_path = ?2 AND car_name IS NOT NULL
         ORDER BY start_time_utc DESC LIMIT 1",
        params![customer, car_path],
        |r| r.get(0),
    )
    .optional()
}

pub struct LibraryQuery<'a> {
    pub customer_id: i64,
    pub car_path: Option<&'a str>,
    pub q: Option<&'a str>,
    pub raced_only: bool,
    pub cursor: Option<&'a str>,
    pub limit: usize,
}

/// `GET /drivers/{id}/setups`.
pub fn library_page(conn: &Connection, query: &LibraryQuery<'_>) -> Result<Value> {
    let setups = load_setups(conn, query.customer_id)?;
    let files = load_files(conn, query.customer_id)?;
    let obs = load_observations(conn, query.customer_id)?;
    let car_path_of: HashMap<i64, String> = setups
        .iter()
        .map(|s| (s.setup_id, s.car_path.clone()))
        .collect();
    let needle = query.q.map(|q| q.to_lowercase()).filter(|q| !q.is_empty());

    let mut summaries: Vec<(String, i64, Value)> = Vec::new();
    for setup in &setups {
        if query
            .car_path
            .is_some_and(|c| !setup.car_path.eq_ignore_ascii_case(c))
        {
            continue;
        }
        let name = car_name(conn, query.customer_id, &setup.car_path)?;
        let summary = summarize(setup, name, &files, &obs, &car_path_of);
        if query.raced_only && summary["raced"] != true {
            continue;
        }
        if let Some(needle) = &needle {
            let hay = format!(
                "{} {} {}",
                setup.car_path,
                summary["car_name"].as_str().unwrap_or(""),
                summary["names"]
                    .as_array()
                    .map(|a| a
                        .iter()
                        .filter_map(|v| v.as_str())
                        .collect::<Vec<_>>()
                        .join(" "))
                    .unwrap_or_default()
            )
            .to_lowercase();
            if !hay.contains(needle) {
                continue;
            }
        }
        summaries.push((setup.last_seen_at.clone(), setup.setup_id, summary));
    }
    // keyset cursor over (last_seen_at desc, setup_id desc)
    if let Some(cursor) = query.cursor {
        let (seen, id) = cursor
            .split_once('|')
            .and_then(|(s, i)| Some((s.to_string(), i.parse::<i64>().ok()?)))
            .ok_or_else(|| Error::invalid("cursor is not valid"))?;
        summaries.retain(|(s, i, _)| (s.as_str(), *i) < (seen.as_str(), id));
    }
    let more = summaries.len() > query.limit;
    summaries.truncate(query.limit);
    let next = if more {
        summaries.last().map(|(s, i, _)| format!("{s}|{i}"))
    } else {
        None
    };
    let unlinked: Vec<Value> = if query.cursor.is_some() {
        Vec::new()
    } else {
        files
            .iter()
            .filter(|f| f.present)
            .filter(|f| {
                query
                    .car_path
                    .is_none_or(|c| f.car_folder.eq_ignore_ascii_case(c))
            })
            .filter(|f| {
                needle.as_ref().is_none_or(|n| {
                    format!("{} {}", f.car_folder, f.relative_path)
                        .to_lowercase()
                        .contains(n)
                })
            })
            .filter(|f| linked_setup(f, &car_path_of, &obs).is_none())
            .take(2000)
            .map(|f| file_entry(f, None))
            .collect()
    };
    Ok(json!({
        "setups": summaries.into_iter().map(|(_, _, v)| v).collect::<Vec<_>>(),
        "unlinked_files": unlinked,
        "next_cursor": next,
    }))
}

/// `GET /setups/{id}`; None when it does not exist.
pub fn detail(conn: &Connection, setup_id: i64) -> Result<Option<Value>> {
    let row = conn
        .query_row(
            "SELECT setup_id, customer_id, car_path, values_json, first_seen_at, last_seen_at
             FROM setups WHERE setup_id = ?1",
            [setup_id],
            |r| {
                Ok(SetupRow {
                    setup_id: r.get(0)?,
                    customer_id: r.get(1)?,
                    car_path: r.get(2)?,
                    values_json: r.get(3)?,
                    first_seen_at: r.get(4)?,
                    last_seen_at: r.get(5)?,
                })
            },
        )
        .optional()?;
    let Some(setup) = row else {
        return Ok(None);
    };
    let setups = load_setups(conn, setup.customer_id)?;
    let car_path_of: HashMap<i64, String> = setups
        .iter()
        .map(|s| (s.setup_id, s.car_path.clone()))
        .collect();
    let files = load_files(conn, setup.customer_id)?;
    let obs = load_observations(conn, setup.customer_id)?;
    let name = car_name(conn, setup.customer_id, &setup.car_path)?;
    let summary = summarize(&setup, name, &files, &obs, &car_path_of);
    let values: BTreeMap<String, String> =
        serde_json::from_str(&setup.values_json).unwrap_or_default();
    let excluded: Vec<&String> = values.keys().filter(|k| is_identity_excluded(k)).collect();
    let observations: Vec<Value> = obs
        .iter()
        .filter(|o| o.setup_id == setup_id)
        .take(500)
        .map(|o| {
            json!({
                "source": o.source,
                "observed_at": o.observed_at,
                "setup_name": o.name,
                "sto_sha256": o.sto,
                "is_modified": o.modified.map(|m| m != 0),
                "load_type": o.load_type,
                "session_id": o.session_id,
                "analysis_id": o.analysis_id,
                "visit_id": o.visit,
            })
        })
        .collect();
    Ok(Some(json!({
        "setup": summary,
        "values": values,
        "identity_excluded_keys": excluded,
        "observations": observations,
    })))
}

/// Setup values map and car for compare; None when the setup does not exist.
pub fn values_of_setup(
    conn: &Connection,
    setup_id: i64,
) -> rusqlite::Result<Option<(String, BTreeMap<String, String>, bool)>> {
    let row: Option<(String, String)> = conn
        .query_row(
            "SELECT car_path, values_json FROM setups WHERE setup_id = ?1",
            [setup_id],
            |r| Ok((r.get(0)?, r.get(1)?)),
        )
        .optional()?;
    let Some((car, json)) = row else {
        return Ok(None);
    };
    let only_export: bool = conn.query_row(
        "SELECT COUNT(*) > 0 AND SUM(source <> 'htm_export') = 0 FROM setup_observations WHERE setup_id = ?1",
        [setup_id],
        |r| r.get(0),
    )?;
    Ok(Some((
        car,
        serde_json::from_str(&json).unwrap_or_default(),
        only_export,
    )))
}

/// Cars and tracks seen in the driver's recordings (`GET .../cars-and-tracks`).
pub fn cars_and_tracks(conn: &Connection, customer: i64) -> rusqlite::Result<Value> {
    let mut cars_stmt = conn.prepare(
        "SELECT car_path, MAX(car_name) FROM recordings WHERE customer_id = ?1 AND car_path IS NOT NULL
         GROUP BY car_path ORDER BY car_path",
    )?;
    let cars: Vec<Value> = cars_stmt
        .query_map([customer], |r| {
            Ok(json!({"car_path": r.get::<_, String>(0)?, "car_name": r.get::<_, Option<String>>(1)?}))
        })?
        .collect::<rusqlite::Result<_>>()?;
    let mut tracks_stmt = conn.prepare(
        "SELECT track, track_config FROM recordings WHERE customer_id = ?1 AND track IS NOT NULL
         GROUP BY track, track_config ORDER BY track, track_config",
    )?;
    let tracks: Vec<Value> = tracks_stmt
        .query_map([customer], |r| {
            Ok(json!({"track": r.get::<_, String>(0)?, "track_config": r.get::<_, Option<String>>(1)?}))
        })?
        .collect::<rusqlite::Result<_>>()?;
    Ok(json!({"cars": cars, "tracks": tracks}))
}

//! Setup library assembly (D9): setups from recordings, garage capture and `.htm` exports,
//! `.sto` files linked by name and hash, files whose values are unknown.

use std::collections::{BTreeMap, BTreeSet, HashMap};

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

/// Keys that do not count toward setup identity: the one implementation of `setup-keys.json`.
pub use coach_ibt::is_identity_excluded;

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

// ------------------------------------------------- setup identity of a garage visit

/// 0.1 psi in kPa: iRacing publishes cold pressures in the driver's units.
const PSI_KPA: f64 = 6.894_757;

/// How far the sim's own rounding moves a published value of `key` in `unit` between two readings
/// of one setup (`collector-protocol.md` section 8, "Setup identity of a visit"): camber within
/// 0.1 deg (the garage recomputes static camber at the computed ride height: the Supra's left-front
/// camber reads +5.9 deg while a file is loaded and +5.8 deg after Apply & Close), pressures within
/// 0.1 psi. The P5 derived values (corner weights, ride heights, preloads) need no tolerance here:
/// they are outside identity altogether (`setup-keys.json`).
fn rounding_tolerance(key: &str, unit: &str) -> Option<f64> {
    let leaf = key.rsplit('.').next().unwrap_or(key).to_ascii_lowercase();
    let unit = unit.to_ascii_lowercase();
    if leaf.contains("camber") && unit == "deg" {
        return Some(0.1);
    }
    if leaf.contains("pressure") {
        return match unit.as_str() {
            "psi" => Some(0.1),
            "kpa" => Some(0.1 * PSI_KPA),
            "bar" => Some(0.1 * PSI_KPA / 100.0),
            _ => None,
        };
    }
    None
}

/// A published value as a number and its unit text (`+5.9 deg` is `(5.9, "deg")`).
fn number_and_unit(value: &str) -> Option<(f64, &str)> {
    let value = value.trim();
    let (number, unit) = match value.split_once(char::is_whitespace) {
        Some((number, unit)) => (number, unit.trim()),
        None => (value, ""),
    };
    number
        .parse::<f64>()
        .ok()
        .filter(|n| n.is_finite())
        .map(|n| (n, unit))
}

/// True when `a` and `b` are one setup up to the sim's own rounding: the same identity keys, each
/// value equal as published or, for a key [`rounding_tolerance`] names, within its tolerance in the
/// same unit. Keys outside identity (`setup-keys.json`) are ignored.
pub fn same_setup_within_rounding(
    a: &BTreeMap<String, String>,
    b: &BTreeMap<String, String>,
) -> bool {
    let identity = |m: &BTreeMap<String, String>| -> BTreeSet<String> {
        m.keys()
            .filter(|k| !is_identity_excluded(k))
            .cloned()
            .collect()
    };
    let keys = identity(a);
    if keys != identity(b) {
        return false;
    }
    keys.iter().all(|key| {
        let (x, y) = (a[key].trim(), b[key].trim());
        if x == y {
            return true;
        }
        match (number_and_unit(x), number_and_unit(y)) {
            (Some((x, unit)), Some((y, other))) if unit == other => {
                rounding_tolerance(key, unit).is_some_and(|tol| (x - y).abs() <= tol + 1e-9)
            }
            _ => false,
        }
    })
}

/// The setup a garage snapshot is an observation of, if any.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct SnapshotTarget {
    pub setup_id: i64,
    /// Same values fingerprint (not only within rounding).
    pub exact: bool,
}

fn stored_values(
    conn: &Connection,
    setup_id: i64,
) -> rusqlite::Result<Option<BTreeMap<String, String>>> {
    let json: Option<String> = conn
        .query_row(
            "SELECT values_json FROM setups WHERE setup_id = ?1",
            [setup_id],
            |r| r.get(0),
        )
        .optional()?;
    Ok(json.and_then(|j| serde_json::from_str(&j).ok()))
}

/// Which setup a snapshot of a visit belongs to (`collector-protocol.md` section 8). A snapshot
/// never creates one. In order: the visit's commit when the fingerprints are equal; any other
/// setup of the driver and car with that fingerprint (a visit's entry snapshot is usually the setup
/// the car had); the commit when the values are within the sim's rounding; a setup that is not
/// known from snapshots alone within rounding (named first, then the latest seen); else none. `exclude`
/// leaves one setup out (the snapshot-only setup being folded).
#[allow(clippy::too_many_arguments)]
pub fn resolve_snapshot(
    conn: &Connection,
    customer: i64,
    car_path: &str,
    values: &BTreeMap<String, String>,
    commit_setup: Option<i64>,
    exclude: Option<i64>,
    fingerprint: &dyn Fn(&BTreeMap<String, String>) -> String,
) -> rusqlite::Result<Option<SnapshotTarget>> {
    let fp = fingerprint(values);
    let commit_values = match commit_setup {
        Some(id) => stored_values(conn, id)?,
        None => None,
    };
    if let (Some(id), Some(commit)) = (commit_setup, &commit_values)
        && fingerprint(commit) == fp
    {
        return Ok(Some(SnapshotTarget {
            setup_id: id,
            exact: true,
        }));
    }
    let same: Option<i64> = conn
        .query_row(
            "SELECT setup_id FROM setups WHERE customer_id = ?1 AND car_path = ?2 AND values_fingerprint = ?3
               AND setup_id IS NOT ?4",
            params![customer, car_path, fp, exclude],
            |r| r.get(0),
        )
        .optional()?;
    if let Some(id) = same {
        return Ok(Some(SnapshotTarget {
            setup_id: id,
            exact: true,
        }));
    }
    if let (Some(id), Some(commit)) = (commit_setup, &commit_values)
        && same_setup_within_rounding(values, commit)
    {
        return Ok(Some(SnapshotTarget {
            setup_id: id,
            exact: false,
        }));
    }
    let mut stmt = conn.prepare(
        "SELECT s.setup_id, s.values_json FROM setups s
         WHERE s.customer_id = ?1 AND s.car_path = ?2 AND s.setup_id IS NOT ?3
           AND EXISTS (SELECT 1 FROM setup_observations o WHERE o.setup_id = s.setup_id
                         AND o.source <> 'garage_snapshot')
         ORDER BY EXISTS (SELECT 1 FROM setup_observations o WHERE o.setup_id = s.setup_id
                            AND o.source IN ('recording', 'garage_commit') AND o.is_modified = 0
                            AND trim(COALESCE(o.setup_name, '')) <> '') DESC,
                  s.last_seen_at DESC, s.setup_id",
    )?;
    let candidates = stmt
        .query_map(params![customer, car_path, exclude], |r| {
            Ok((r.get::<_, i64>(0)?, r.get::<_, String>(1)?))
        })?
        .collect::<rusqlite::Result<Vec<_>>>()?;
    for (id, json) in candidates {
        let Ok(other) = serde_json::from_str::<BTreeMap<String, String>>(&json) else {
            continue;
        };
        if same_setup_within_rounding(values, &other) {
            return Ok(Some(SnapshotTarget {
                setup_id: id,
                exact: false,
            }));
        }
    }
    Ok(None)
}

/// Records a snapshot as an observation of `target`. A snapshot with the setup's own fingerprint
/// also refreshes its values when it is the latest observation (as [`upsert_setup`] does); one that
/// matches only within rounding leaves the setup's values as they are.
pub fn attach_snapshot(
    conn: &Connection,
    target: SnapshotTarget,
    customer: i64,
    visit: &str,
    update_count: i64,
    observed_at: &str,
    values: &BTreeMap<String, String>,
) -> rusqlite::Result<usize> {
    let values_json = serde_json::to_string(values).unwrap_or_else(|_| "{}".into());
    conn.execute(
        "UPDATE setups SET
           first_seen_at = MIN(first_seen_at, ?2),
           values_json = CASE WHEN ?4 AND ?2 >= last_seen_at THEN ?3 ELSE values_json END,
           last_seen_at = MAX(last_seen_at, ?2)
         WHERE setup_id = ?1",
        params![target.setup_id, observed_at, values_json, target.exact],
    )?;
    conn.execute(
        "INSERT OR IGNORE INTO setup_observations(setup_id, customer_id, source, visit_id, update_count, observed_at)
         VALUES (?1, ?2, 'garage_snapshot', ?3, ?4, ?5)",
        params![target.setup_id, customer, visit, update_count, observed_at],
    )
}

/// The setup the visit's commit stored, once it is stored.
pub fn commit_setup_of(conn: &Connection, visit: &str) -> rusqlite::Result<Option<i64>> {
    conn.query_row(
        "SELECT setup_id FROM setup_observations WHERE visit_id = ?1 AND source = 'garage_commit'",
        [visit],
        |r| r.get(0),
    )
    .optional()
}

/// Resolves every snapshot of `visit` still waiting for its commit (`commit_setup`).
fn resolve_visit_snapshots(
    conn: &Connection,
    visit: &str,
    commit_setup: i64,
    fingerprint: &dyn Fn(&BTreeMap<String, String>) -> String,
) -> rusqlite::Result<()> {
    let pending: Vec<(i64, i64, String, String, String)> = conn
        .prepare(
            "SELECT update_count, customer_id, car_path, values_json, observed_at FROM garage_snapshots
             WHERE visit_id = ?1 AND resolved_at IS NULL ORDER BY update_count",
        )?
        .query_map([visit], |r| {
            Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?))
        })?
        .collect::<rusqlite::Result<_>>()?;
    for (count, customer, car, json, observed) in pending {
        let values: BTreeMap<String, String> = serde_json::from_str(&json).unwrap_or_default();
        let target = resolve_snapshot(
            conn,
            customer,
            &car,
            &values,
            Some(commit_setup),
            None,
            fingerprint,
        )?;
        if let Some(target) = target {
            attach_snapshot(conn, target, customer, visit, count, &observed, &values)?;
        }
        conn.execute(
            "UPDATE garage_snapshots SET resolved_at = ?3 WHERE visit_id = ?1 AND update_count = ?2",
            params![visit, count, iso_now()],
        )?;
    }
    Ok(())
}

/// `garage.snapshot`: kept once per `(visit_id, update_count)` (a replay inserts nothing) until the
/// visit's commit decides which setup it observed; it never creates a setup itself. A snapshot
/// that arrives after its visit's commit is resolved at once.
pub fn record_garage_snapshot(
    conn: &Connection,
    msg: &Value,
    collector_id: &str,
    fingerprint: &dyn Fn(&BTreeMap<String, String>) -> String,
) -> Result<GarageOutcome> {
    let visit = msg["visit_id"].as_str().unwrap_or_default().to_string();
    let update_count = msg["update_count"].as_i64().unwrap_or(-1);
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
    conn.execute(
        "INSERT OR IGNORE INTO garage_snapshots(visit_id, update_count, customer_id, car_path, values_json,
           values_fingerprint, observed_at)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
        params![
            visit,
            update_count,
            customer,
            car_path,
            serde_json::to_string(&values).unwrap_or_else(|_| "{}".into()),
            fingerprint(&values),
            observed
        ],
    )?;
    if let Some(commit) = commit_setup_of(conn, &visit)? {
        resolve_visit_snapshots(conn, &visit, commit, fingerprint)?;
    }
    Ok(GarageOutcome::Stored(customer))
}

/// `garage.commit`: the visit's last snapshot with the name and `.sto` hash iRacing published at
/// exit; the only garage event that creates a setup (a modified exit creates the values-only one).
/// The visit's snapshots then become observations of the setup each equals (`resolve_snapshot`).
/// A modified commit never defines the named file's values (links are derived only from
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
        // A commit may reference its last snapshot instead of repeating the values; a snapshot a
        // server of schema version 1 stored is an observation of a setup of its own.
        let json: Option<String> = match conn
            .query_row(
                "SELECT values_json FROM garage_snapshots WHERE visit_id = ?1 AND update_count = ?2",
                params![visit, count],
                |r| r.get(0),
            )
            .optional()?
        {
            Some(json) => Some(json),
            None => conn
                .query_row(
                    "SELECT s.values_json FROM setup_observations o JOIN setups s ON s.setup_id = o.setup_id
                     WHERE o.visit_id = ?1 AND o.update_count = ?2 AND o.source = 'garage_snapshot'",
                    params![visit, count],
                    |r| r.get(0),
                )
                .optional()?,
        };
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
    // A replayed commit finds the stored one (the unique index ignored the insert).
    let commit_setup = commit_setup_of(conn, &visit)?.unwrap_or(setup_id);
    resolve_visit_snapshots(conn, &visit, commit_setup, fingerprint)?;
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
    /// The recording's library (`main` or `lab`; recording observations only).
    library: Option<String>,
}

fn base_name(relative_path: &str) -> &str {
    relative_path.rsplit('/').next().unwrap_or(relative_path)
}

/// An `.htm` (or `.html`) export's file name without its extension; None for any other file or an
/// empty stem.
fn export_stem(relative_path: &str) -> Option<&str> {
    let name = base_name(relative_path);
    let (stem, extension) = name.rsplit_once('.')?;
    let export = extension.eq_ignore_ascii_case("htm") || extension.eq_ignore_ascii_case("html");
    let stem = stem.trim();
    (export && !stem.is_empty()).then_some(stem)
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
                COALESCE((SELECT v.ambiguous_name FROM garage_visits v WHERE v.visit_id = o.visit_id), 0),
                (SELECT r.library FROM recordings r WHERE r.sha256 = o.recording_sha256)
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
                library: r.get(14)?,
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

/// A setup's usage over some observations (`usage`).
struct Usage {
    sources: Vec<&'static str>,
    count: usize,
    /// Seen in a race session's recording.
    raced: bool,
    /// Distinct race sessions with a recording of the setup; None when a recording of it belongs
    /// to no session yet, or to one whose type is unknown (the count could be short).
    raced_count: Option<usize>,
}

impl Usage {
    /// Writes the usage fields of a `SetupSummary`.
    fn apply(&self, summary: &mut Value) {
        summary["sources"] = json!(self.sources);
        summary["observation_count"] = json!(self.count);
        summary["raced"] = json!(self.raced);
        summary["raced_count"] = json!(self.raced_count);
    }
}

/// A setup's usage over `obs`: the sources, the observation count, whether one is of a race
/// recording (`raced`) and in how many race sessions (`raced_count`).
fn usage<'a>(setup_id: i64, obs: impl IntoIterator<Item = &'a ObsRow>) -> Usage {
    let (mut sources, mut count, mut raced) = (BTreeSet::new(), 0, false);
    let (mut races, mut unknown) = (BTreeSet::new(), false);
    for o in obs.into_iter().filter(|o| o.setup_id == setup_id) {
        count += 1;
        sources.insert(match o.source.as_str() {
            "recording" => "recording",
            "garage_snapshot" => "garage_snapshot",
            "garage_commit" => "garage_commit",
            _ => "htm_export",
        });
        if o.source != "recording" {
            continue;
        }
        let race = o
            .session_type
            .as_deref()
            .is_some_and(|t| t.to_lowercase().starts_with("race"));
        raced |= race;
        match (o.session_id, o.session_type.as_deref()) {
            (Some(session), Some(_)) if race => {
                races.insert(session);
            }
            (Some(_), Some(_)) => {}
            _ => unknown = true,
        }
    }
    Usage {
        sources: sources.into_iter().collect(),
        count,
        raced,
        raced_count: (!unknown).then_some(races.len()),
    }
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
    let mut names: BTreeSet<String> = BTreeSet::new();
    let mut export_names: BTreeSet<String> = BTreeSet::new();
    let mut sto_files = Vec::new();
    for f in files {
        if linked_setup(f, car_path_of, obs) == Some(setup.setup_id) {
            if f.format == "sto" {
                names.insert(base_name(&f.relative_path).to_string());
            } else if let Some(stem) = export_stem(&f.relative_path) {
                export_names.insert(stem.to_string());
            }
            sto_files.push(file_entry(f, Some(setup.setup_id)));
        }
    }
    // An unmodified load of a named file has that file's values, so its name is a known name
    // even when the server holds no such file (`collector-protocol.md` section 8).
    for o in &mine {
        if matches!(o.source.as_str(), "recording" | "garage_commit")
            && o.modified == Some(0)
            && let Some(name) = o.name.as_deref().map(str::trim).filter(|n| !n.is_empty())
            && !names.iter().any(|n| n.eq_ignore_ascii_case(name))
        {
            names.insert(name.to_string());
        }
    }
    // Values known only from a garage `.htm` export have no `.sto` name: the export's file name
    // without its extension names them (the migration's exports showed as "Unnamed").
    if names.is_empty() {
        names = export_names;
    }
    // usage over every observation; the callers replace it with one library's (`Usage::apply`)
    let mut summary = json!({
        "setup_id": setup.setup_id,
        "customer_id": setup.customer_id,
        "car_path": setup.car_path,
        "car_name": car_name,
        "names": names.into_iter().collect::<Vec<_>>(),
        "sto_files": sto_files,
        "first_seen_at": setup.first_seen_at,
        "last_seen_at": setup.last_seen_at,
    });
    usage(setup.setup_id, mine.iter().copied()).apply(&mut summary);
    summary
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

/// The screen name of each car folder in the setups folder, keys lower case. A folder is a car path in
/// nearly every case, so a recording of that car path names it (the newest name wins); a folder that
/// is not a car path takes the name of the car whose setup one of its files is linked to. A folder no
/// recording or linked setup names is absent: the client then falls back to the folder name.
fn folder_car_names(
    conn: &Connection,
    customer: i64,
    files: &[FileRow],
    car_path_of: &HashMap<i64, String>,
    obs: &[ObsRow],
) -> rusqlite::Result<HashMap<String, String>> {
    let mut stmt = conn.prepare(
        "SELECT car_path, car_name FROM recordings
         WHERE customer_id = ?1 AND car_path IS NOT NULL AND car_name IS NOT NULL AND car_name <> ''
         ORDER BY start_time_utc ASC, sha256 ASC",
    )?;
    let mut by_path: HashMap<String, String> = HashMap::new();
    for row in stmt.query_map([customer], |r| {
        Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?))
    })? {
        let (path, name) = row?;
        by_path.insert(path.to_lowercase(), name);
    }
    let mut out: HashMap<String, String> = HashMap::new();
    for f in files {
        let folder = f.car_folder.to_lowercase();
        if out.contains_key(&folder) {
            continue;
        }
        let named = by_path.get(&folder).cloned().or_else(|| {
            let setup = linked_setup(f, car_path_of, obs)?;
            by_path
                .get(&car_path_of.get(&setup)?.to_lowercase())
                .cloned()
        });
        if let Some(name) = named {
            out.insert(folder, name);
        }
    }
    Ok(out)
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
    /// `Some(true)` raced only (no unlinked files), `Some(false)` never raced; overrides `raced_only`.
    pub raced: Option<bool>,
    pub cursor: Option<&'a str>,
    pub limit: usize,
    /// Whose setup usage: the counts, sources and `raced` come from this library's recordings
    /// (garage visits and exports count in `main`); a setup without usage there is left out.
    /// Names and file links come from every observation (what a file holds does not depend on
    /// who drove it).
    pub library: crate::library::Library,
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
    let used: Vec<&ObsRow> = obs
        .iter()
        .filter(|o| {
            crate::library::observation_counts(&o.source, o.library.as_deref(), query.library)
        })
        .collect();

    let mut summaries: Vec<(String, i64, Value)> = Vec::new();
    for setup in &setups {
        if query
            .car_path
            .is_some_and(|c| !setup.car_path.eq_ignore_ascii_case(c))
        {
            continue;
        }
        let used_here = usage(setup.setup_id, used.iter().copied());
        let raced = used_here.raced;
        let seen = obs.iter().any(|o| o.setup_id == setup.setup_id);
        if used_here.count == 0 && (seen || query.library == crate::library::Library::Lab) {
            continue; // seen only in the other library
        }
        let name = car_name(conn, query.customer_id, &setup.car_path)?;
        let mut summary = summarize(setup, name, &files, &obs, &car_path_of);
        used_here.apply(&mut summary);
        if query
            .raced
            .map_or(query.raced_only && !raced, |want| want != raced)
        {
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
    // files with values unknown are Joshua's setups folder, not lab usage
    let names = folder_car_names(conn, query.customer_id, &files, &car_path_of, &obs)?;
    let unlinked: Vec<Value> = if query.cursor.is_some()
        || query.raced == Some(true)
        || query.library == crate::library::Library::Lab
    {
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
                    format!(
                        "{} {} {}",
                        f.car_folder,
                        names.get(&f.car_folder.to_lowercase()).map_or("", |n| n),
                        f.relative_path
                    )
                    .to_lowercase()
                    .contains(n)
                })
            })
            .filter(|f| linked_setup(f, &car_path_of, &obs).is_none())
            .take(2000)
            .map(|f| {
                let mut entry = file_entry(f, None);
                entry["car_name"] = json!(names.get(&f.car_folder.to_lowercase()));
                entry
            })
            .collect()
    };
    Ok(json!({
        "setups": summaries.into_iter().map(|(_, _, v)| v).collect::<Vec<_>>(),
        "unlinked_files": unlinked,
        "next_cursor": next,
    }))
}

/// The idempotency key of the backend's comparison of two value sets (`POST /setups/compare`):
/// the worker bundle, then a digest of exactly what the worker is given. A finished comparison
/// is found again by it, so it answers a repeated request and a version's `changed_count`.
pub fn compare_key(
    worker: &str,
    left: &BTreeMap<String, String>,
    right: &BTreeMap<String, String>,
    scheme: &str,
) -> String {
    use sha2::{Digest, Sha256};
    let text = json!({"left": left, "right": right, "scheme": scheme}).to_string();
    format!(
        "setup_compare:{worker}:{}",
        crate::util::hex(&Sha256::digest(text.as_bytes()))
    )
}

/// The unit a displayed setup value ends in (`-3.2 deg` is `deg`, `1 3/4 in` is `in`), read as
/// the backend's `setup_catalog._VALUE_RE` reads it; None for text, a bare number, a ratio or a
/// list.
pub fn displayed_unit(text: &str) -> Option<String> {
    let s = text.trim();
    let b = s.as_bytes();
    let digits = |from: usize| {
        let mut j = from;
        while j < b.len() && b[j].is_ascii_digit() {
            j += 1;
        }
        j
    };
    // `n/d` after `at`, if there is one: its end
    let fraction = |at: usize| {
        let n = digits(at);
        (n > at && n < b.len() && b[n] == b'/' && digits(n + 1) > n + 1).then(|| digits(n + 1))
    };
    let mut i = 0;
    if i < b.len() && (b[i] == b'+' || b[i] == b'-') {
        i += 1;
    }
    let whole = digits(i);
    let end = if whole > i {
        let mut gap = whole;
        while gap < b.len() && b[gap].is_ascii_whitespace() {
            gap += 1;
        }
        if let Some(end) = fraction(i) {
            end
        } else if let Some(end) = (gap > whole).then(|| fraction(gap)).flatten() {
            end
        } else if whole < b.len() && b[whole] == b'.' {
            digits(whole + 1)
        } else {
            whole
        }
    } else if i < b.len() && b[i] == b'.' && digits(i + 1) > i + 1 {
        digits(i + 1)
    } else {
        return None;
    };
    let unit = s[end..].trim();
    (!unit.is_empty()
        && !unit
            .chars()
            .any(|c| c.is_ascii_digit() || c == ',' || c == ':'))
    .then(|| unit.to_string())
}

/// The unit a compare row's two displayed values share: the present one's when one is missing,
/// None when either has none or they differ.
pub fn shared_unit(left: &Value, right: &Value) -> Option<String> {
    let units: Vec<Option<String>> = [left, right]
        .into_iter()
        .filter(|v| !v.is_null())
        .map(|v| v.as_str().and_then(displayed_unit))
        .collect();
    match units.as_slice() {
        [one] => one.clone(),
        [a, b] if a == b => a.clone(),
        _ => None,
    }
}

/// `counts.changed` of the backend's finished comparison of `older` with `newer` (left older),
/// None when the pair does not compare (a side without values, or one known only from an export
/// and the other from a recording) or the backend has not compared it under this worker.
fn changed_count(
    conn: &Connection,
    older: i64,
    newer: i64,
    worker: &str,
) -> rusqlite::Result<Option<u64>> {
    let (Some((_, left, left_export)), Some((_, right, right_export))) =
        (values_of_setup(conn, older)?, values_of_setup(conn, newer)?)
    else {
        return Ok(None);
    };
    if left.is_empty() || right.is_empty() || left_export != right_export {
        return Ok(None);
    }
    let scheme = if left_export { "export" } else { "recording" };
    let key = compare_key(worker, &left, &right, scheme);
    let result: Option<String> = conn
        .query_row(
            "SELECT result_json FROM jobs WHERE idempotency_key = ?1 AND state = 'succeeded'",
            [key],
            |r| r.get(0),
        )
        .optional()?
        .flatten();
    Ok(result
        .and_then(|text| serde_json::from_str::<Value>(&text).ok())
        .and_then(|v| v["backend"]["counts"]["changed"].as_u64()))
}

/// The versions of a setup: it and the other setups of its car that carry one of its names and
/// have use in the library, oldest first, each with the backend's changed-setting count against
/// the one before it.
#[allow(clippy::too_many_arguments)]
fn versions(
    conn: &Connection,
    current: &SetupRow,
    current_names: &[String],
    setups: &[SetupRow],
    files: &[FileRow],
    obs: &[ObsRow],
    used: &[&ObsRow],
    car_path_of: &HashMap<i64, String>,
    worker: &str,
) -> rusqlite::Result<Vec<Value>> {
    let wanted: BTreeSet<String> = current_names.iter().map(|n| n.to_lowercase()).collect();
    let mut family: Vec<&SetupRow> = setups
        .iter()
        .filter(|s| {
            s.setup_id == current.setup_id
                || (!wanted.is_empty()
                    && s.car_path.eq_ignore_ascii_case(&current.car_path)
                    && used.iter().any(|o| o.setup_id == s.setup_id)
                    && summarize(s, None, files, obs, car_path_of)["names"]
                        .as_array()
                        .is_some_and(|names| {
                            names
                                .iter()
                                .filter_map(Value::as_str)
                                .any(|n| wanted.contains(&n.to_lowercase()))
                        }))
        })
        .collect();
    family.sort_by(|a, b| {
        (a.first_seen_at.as_str(), a.setup_id).cmp(&(b.first_seen_at.as_str(), b.setup_id))
    });
    let mut out = Vec::with_capacity(family.len());
    let mut previous: Option<i64> = None;
    for s in family {
        let changed = match previous {
            Some(older) => changed_count(conn, older, s.setup_id, worker)?,
            None => None,
        };
        out.push(json!({
            "setup_id": s.setup_id,
            "first_seen_at": s.first_seen_at,
            "last_seen_at": s.last_seen_at,
            "previous_setup_id": previous,
            "changed_count": changed,
        }));
        previous = Some(s.setup_id);
    }
    Ok(out)
}

/// `GET /setups/{id}`; None when it does not exist. The usage figures count `library`'s
/// observations, as the list does; `worker` is the worker bundle that keys the backend's
/// comparisons (`compare_key`).
pub fn detail(
    conn: &Connection,
    setup_id: i64,
    library: crate::library::Library,
    worker: &str,
) -> Result<Option<Value>> {
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
    let used: Vec<&ObsRow> = obs
        .iter()
        .filter(|o| crate::library::observation_counts(&o.source, o.library.as_deref(), library))
        .collect();
    let mut summary = summarize(&setup, name, &files, &obs, &car_path_of);
    usage(setup.setup_id, used.iter().copied()).apply(&mut summary);
    let names: Vec<String> = summary["names"]
        .as_array()
        .map(|a| {
            a.iter()
                .filter_map(|v| v.as_str().map(String::from))
                .collect()
        })
        .unwrap_or_default();
    let versions = versions(
        conn,
        &setup,
        &names,
        &setups,
        &files,
        &obs,
        &used,
        &car_path_of,
        worker,
    )?;
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
        "versions": versions,
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

/// Cars and tracks seen in the driver's recordings of one library (`GET .../cars-and-tracks`).
pub fn cars_and_tracks(
    conn: &Connection,
    customer: i64,
    library: crate::library::Library,
) -> rusqlite::Result<Value> {
    let library = library.as_str();
    let mut cars_stmt = conn.prepare(
        "SELECT car_path, MAX(car_name) FROM recordings WHERE customer_id = ?1 AND library = ?2 AND car_path IS NOT NULL
         GROUP BY car_path ORDER BY car_path",
    )?;
    let cars: Vec<Value> = cars_stmt
        .query_map(params![customer, library], |r| {
            Ok(json!({"car_path": r.get::<_, String>(0)?, "car_name": r.get::<_, Option<String>>(1)?}))
        })?
        .collect::<rusqlite::Result<_>>()?;
    let mut tracks_stmt = conn.prepare(
        "SELECT track, track_config FROM recordings WHERE customer_id = ?1 AND library = ?2 AND track IS NOT NULL
         GROUP BY track, track_config ORDER BY track, track_config",
    )?;
    let tracks: Vec<Value> = tracks_stmt
        .query_map(params![customer, library], |r| {
            Ok(json!({"track": r.get::<_, String>(0)?, "track_config": r.get::<_, Option<String>>(1)?}))
        })?
        .collect::<rusqlite::Result<_>>()?;
    let mut series_stmt = conn.prepare(
        "SELECT DISTINCT series_name FROM recordings WHERE customer_id = ?1 AND library = ?2 AND series_name IS NOT NULL
           AND trim(series_name) <> '' ORDER BY series_name",
    )?;
    let series: Vec<Value> = series_stmt
        .query_map(params![customer, library], |r| {
            Ok(json!({"series_name": r.get::<_, String>(0)?}))
        })?
        .collect::<rusqlite::Result<_>>()?;
    Ok(json!({"cars": cars, "tracks": tracks, "series": series}))
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The literal leaves a contract pattern matches: anchors dropped, one group expanded (an
    /// optional group also to nothing).
    fn literals(pattern: &str) -> Vec<String> {
        let body = pattern.trim_start_matches('^').trim_end_matches('$');
        match (body.find('('), body.find(')')) {
            (Some(open), Some(close)) => {
                let (head, rest) = (&body[..open], &body[close + 1..]);
                let mut alternatives: Vec<&str> = body[open + 1..close].split('|').collect();
                let tail = match rest.strip_prefix('?') {
                    Some(tail) => {
                        alternatives.push("");
                        tail
                    }
                    None => rest,
                };
                alternatives
                    .iter()
                    .map(|alt| format!("{head}{alt}{tail}"))
                    .collect()
            }
            _ => vec![body.to_owned()],
        }
    }

    #[test]
    fn every_contract_pattern_literal_is_excluded() {
        for (pattern, _) in coach_wire::setup_keys::EXCLUDED_LEAF_PATTERNS {
            for literal in literals(pattern) {
                assert!(
                    is_identity_excluded(&format!("Chassis.Front.{literal}")),
                    "{literal}"
                );
                assert!(
                    is_identity_excluded(&literal.to_ascii_uppercase()),
                    "{literal}"
                );
            }
        }
    }

    #[test]
    fn kelvin_sign_is_not_ascii_k() {
        assert!(is_identity_excluded("Chassis.Rear.TruckArmPreload"));
        assert!(!is_identity_excluded("Chassis.Rear.Truc\u{212A}ArmPreload"));
    }

    #[test]
    fn the_displayed_unit_is_split_as_the_backend_reads_a_value() {
        for (text, unit) in [
            ("-3.2 deg", Some("deg")),
            ("152 kPa", Some("kPa")),
            ("+5.8 deg", Some("deg")),
            ("1 3/4 in", Some("in")),
            ("3/8\"", Some("\"")),
            (".5 in", Some("in")),
            ("50.0%", Some("%")),
            ("1250 lbs/in", Some("lbs/in")),
            ("10 clicks", Some("clicks")),
            ("  12.5   mm  ", Some("mm")),
            ("4", None),
            ("1.500", None),
            ("Soft", None),
            ("3.5:1", None),
            ("1.0, 2.0 mm", None),
            ("", None),
            ("—", None),
        ] {
            assert_eq!(displayed_unit(text).as_deref(), unit, "{text:?}");
        }
    }

    #[test]
    fn a_row_unit_is_the_one_both_values_share() {
        assert_eq!(
            shared_unit(&json!("-3.2 deg"), &json!("-3.0 deg")).as_deref(),
            Some("deg")
        );
        assert_eq!(
            shared_unit(&Value::Null, &json!("152 kPa")).as_deref(),
            Some("kPa")
        );
        assert_eq!(shared_unit(&json!("22.0 psi"), &json!("152 kPa")), None);
        assert_eq!(shared_unit(&json!("4"), &json!("5")), None);
        assert_eq!(shared_unit(&json!(4), &json!(5)), None);
        assert_eq!(shared_unit(&Value::Null, &Value::Null), None);
    }

    fn obs(setup_id: i64, source: &str, session: Option<(i64, &str)>) -> ObsRow {
        ObsRow {
            setup_id,
            source: source.into(),
            blob: None,
            visit: None,
            name: None,
            sto: None,
            modified: None,
            load_type: None,
            observed_at: "2026-10-01T00:00:00Z".into(),
            session_id: session.map(|s| s.0),
            session_type: session.map(|s| s.1.to_string()),
            analysis_id: None,
            ambiguous: false,
            library: None,
        }
    }

    #[test]
    fn raced_count_counts_race_sessions_once_and_unknown_is_not_zero() {
        let rows = [
            obs(1, "recording", Some((10, "Race"))),
            obs(1, "recording", Some((10, "Race"))), // a second recording of the same race
            obs(1, "recording", Some((11, "race"))),
            obs(1, "recording", Some((12, "Practice"))),
            obs(1, "garage_commit", None),
            obs(2, "recording", Some((13, "Race"))),
        ];
        let u = usage(1, &rows);
        assert_eq!((u.count, u.raced, u.raced_count), (5, true, Some(2)));
        let u = usage(3, &rows);
        assert_eq!((u.count, u.raced, u.raced_count), (0, false, Some(0)));
        // a recording not yet in a session: the count could be short
        let pending = [
            obs(1, "recording", Some((10, "Race"))),
            obs(1, "recording", None),
        ];
        let u = usage(1, &pending);
        assert_eq!((u.raced, u.raced_count), (true, None));
        let untyped = [ObsRow {
            session_type: None,
            ..obs(1, "recording", Some((14, "")))
        }];
        assert_eq!(usage(1, &untyped).raced_count, None);
    }

    #[test]
    fn the_compare_key_names_the_worker_and_the_exact_values() {
        let a: BTreeMap<String, String> = [("K".to_string(), "1 in".to_string())].into();
        let b: BTreeMap<String, String> = [("K".to_string(), "2 in".to_string())].into();
        let key = compare_key("w1", &a, &b, "recording");
        assert!(key.starts_with("setup_compare:w1:"));
        assert_eq!(key, compare_key("w1", &a, &b, "recording"));
        assert_ne!(
            key,
            compare_key("w1", &b, &a, "recording"),
            "the order matters"
        );
        assert_ne!(key, compare_key("w2", &a, &b, "recording"));
        assert_ne!(key, compare_key("w1", &a, &b, "export"));
    }

    #[test]
    fn an_export_is_named_after_its_file_without_the_extension() {
        assert_eq!(
            export_stem("NOAPS_MaconiSetupShop Sonoma 26S3 R.htm"),
            Some("NOAPS_MaconiSetupShop Sonoma 26S3 R")
        );
        assert_eq!(export_stem("exports/Base.HTML"), Some("Base"));
        assert_eq!(export_stem("a.b.htm"), Some("a.b"));
        for none in ["Base.sto", "Base", ".htm", "dir.htm/", "  .htm"] {
            assert_eq!(export_stem(none), None, "{none}");
        }
    }
}

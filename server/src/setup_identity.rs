//! Setup identity reconciliation (`setup-keys.json`). A setup is one values fingerprint per driver
//! and car. When the contract's exclusions change, the fingerprints stored under the old rule no
//! longer equal what new observations compute, and setups the new rule calls one stay apart: in
//! the 2026-10-04 harvest every garage visit left its snapshots (setups 69 and 70) beside the named
//! commit (71) because they differed only in the derived `Chassis.FrontArb.Preload`.
//!
//! At every start, before any listener opens, [`reconcile`] recomputes each setup's fingerprint
//! from its stored values, rewrites the ones that changed and merges setups whose fingerprints now
//! coincide; setups without any observation go. Once the stored fingerprints follow the rule it
//! changes nothing, so it runs at every start and acts once per rule change (and again if an older
//! server, run after a rollback, stored old-rule fingerprints meanwhile).
//!
//! Observations keep no values of their own; a setup holds the latest values of its observations.
//! That is exact for a rule that excludes more keys than before (two value maps equal outside the
//! old exclusions are equal outside the new ones), which every `setup-keys.json` change so far
//! has done. A rule that excluded fewer keys could not split stored setups: it would need the
//! recordings and exports ingested again.

use std::collections::BTreeMap;
use std::io::Write;
use std::path::PathBuf;

use rusqlite::{Connection, params};
use serde::Serialize;
use serde_json::{Value, json};

use crate::app::AppState;
use crate::util::iso_now;

const LOG_FILE: &str = "setup-identity.jsonl";

/// Setups that the rule now calls one.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Merge {
    pub customer_id: i64,
    pub car_path: String,
    /// Keeps its id: a named setup first (an unmodified recording or garage commit with a name),
    /// then the first seen, then the lowest id.
    pub survivor: i64,
    /// Removed after their observations and notes moved to the survivor.
    pub merged: Vec<i64>,
    pub observations_moved: usize,
    pub notes_moved: usize,
    /// The survivor's names after the merge (unmodified recordings and garage commits).
    pub names: Vec<String>,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize)]
pub struct Report {
    pub setups_checked: usize,
    /// Setups whose stored fingerprint was rewritten, survivors included.
    pub fingerprints_rewritten: usize,
    pub merges: Vec<Merge>,
    /// Setups that had no observation, removed.
    pub removed_without_observations: Vec<i64>,
    /// Setups that have no observation but a note: kept (a note is never removed; only a merge
    /// moves one).
    pub kept_for_notes: Vec<i64>,
    /// Setups whose stored values do not parse: left as they are.
    pub unreadable: Vec<i64>,
}

impl Report {
    /// True when a row changed.
    pub fn changed(&self) -> bool {
        self.fingerprints_rewritten > 0
            || !self.merges.is_empty()
            || !self.removed_without_observations.is_empty()
    }
}

struct Stored {
    id: i64,
    customer: i64,
    car: String,
    fingerprint: String,
    values_json: String,
    first_seen_at: String,
    last_seen_at: String,
}

/// A named setup: an unmodified recording or garage commit gave its values a name.
fn is_named(conn: &Connection, setup_id: i64) -> rusqlite::Result<bool> {
    conn.query_row(
        "SELECT EXISTS(SELECT 1 FROM setup_observations WHERE setup_id = ?1
           AND source IN ('recording', 'garage_commit') AND is_modified = 0
           AND trim(COALESCE(setup_name, '')) <> '')",
        [setup_id],
        |r| r.get(0),
    )
}

fn names_of(conn: &Connection, setup_id: i64) -> rusqlite::Result<Vec<String>> {
    conn.prepare(
        "SELECT DISTINCT trim(setup_name) FROM setup_observations WHERE setup_id = ?1
           AND source IN ('recording', 'garage_commit') AND is_modified = 0
           AND trim(COALESCE(setup_name, '')) <> '' ORDER BY 1",
    )?
    .query_map([setup_id], |r| r.get(0))?
    .collect()
}

fn has_note(conn: &Connection, setup_id: i64) -> rusqlite::Result<bool> {
    conn.query_row(
        "SELECT EXISTS(SELECT 1 FROM notes WHERE subject_kind = 'setup' AND subject_id = ?1)",
        [setup_id.to_string()],
        |r| r.get(0),
    )
}

fn load(conn: &Connection) -> rusqlite::Result<Vec<Stored>> {
    conn.prepare(
        "SELECT setup_id, customer_id, car_path, values_fingerprint, values_json, first_seen_at,
                last_seen_at FROM setups ORDER BY setup_id",
    )?
    .query_map([], |r| {
        Ok(Stored {
            id: r.get(0)?,
            customer: r.get(1)?,
            car: r.get(2)?,
            fingerprint: r.get(3)?,
            values_json: r.get(4)?,
            first_seen_at: r.get(5)?,
            last_seen_at: r.get(6)?,
        })
    })?
    .collect()
}

/// Merges `members` (two or more setups of one driver and car with one fingerprint) into the
/// first by rank and returns the merge; the caller rewrites the survivor's fingerprint.
fn merge(conn: &Connection, members: Vec<Stored>) -> rusqlite::Result<(Merge, Stored)> {
    let mut ranked = Vec::with_capacity(members.len());
    for member in members {
        ranked.push((is_named(conn, member.id)?, member));
    }
    ranked.sort_by(|(named_a, a), (named_b, b)| {
        named_b
            .cmp(named_a)
            .then_with(|| a.first_seen_at.cmp(&b.first_seen_at))
            .then(a.id.cmp(&b.id))
    });
    let first_seen = ranked
        .iter()
        .map(|(_, m)| m.first_seen_at.clone())
        .min()
        .unwrap_or_default();
    // The latest observation's values, as `upsert_setup` keeps them.
    let (last_seen, values_json) = ranked
        .iter()
        .map(|(_, m)| m)
        .max_by(|a, b| a.last_seen_at.cmp(&b.last_seen_at).then(a.id.cmp(&b.id)))
        .map(|m| (m.last_seen_at.clone(), m.values_json.clone()))
        .unwrap_or_default();
    let mut ranked = ranked.into_iter().map(|(_, m)| m);
    let survivor = ranked.next().expect("a group has members");
    let mut out = Merge {
        customer_id: survivor.customer,
        car_path: survivor.car.clone(),
        survivor: survivor.id,
        merged: Vec::new(),
        observations_moved: 0,
        notes_moved: 0,
        names: Vec::new(),
    };
    for gone in ranked {
        out.observations_moved += conn.execute(
            "UPDATE setup_observations SET setup_id = ?1 WHERE setup_id = ?2",
            params![survivor.id, gone.id],
        )?;
        out.notes_moved += conn.execute(
            "UPDATE notes SET subject_id = ?1 WHERE subject_kind = 'setup' AND subject_id = ?2",
            params![survivor.id.to_string(), gone.id.to_string()],
        )?;
        conn.execute("DELETE FROM setups WHERE setup_id = ?1", [gone.id])?;
        out.merged.push(gone.id);
    }
    conn.execute(
        "UPDATE setups SET first_seen_at = ?2, last_seen_at = ?3, values_json = ?4 WHERE setup_id = ?1",
        params![survivor.id, first_seen, last_seen, values_json],
    )?;
    out.names = names_of(conn, survivor.id)?;
    Ok((out, survivor))
}

/// One transaction over every driver's setups (see the module documentation). `fingerprint` is
/// the rule new observations use (`RecordingParser::fingerprint`).
pub fn reconcile(
    conn: &mut Connection,
    fingerprint: &dyn Fn(&BTreeMap<String, String>) -> String,
) -> rusqlite::Result<Report> {
    let tx = conn.transaction()?;
    let mut report = Report::default();

    let empty: Vec<i64> = tx
        .prepare(
            "SELECT setup_id FROM setups s WHERE NOT EXISTS
               (SELECT 1 FROM setup_observations o WHERE o.setup_id = s.setup_id) ORDER BY setup_id",
        )?
        .query_map([], |r| r.get(0))?
        .collect::<rusqlite::Result<_>>()?;
    for id in empty {
        if has_note(&tx, id)? {
            report.kept_for_notes.push(id);
        } else {
            tx.execute("DELETE FROM setups WHERE setup_id = ?1", [id])?;
            report.removed_without_observations.push(id);
        }
    }

    let stored = load(&tx)?;
    report.setups_checked = stored.len();
    let mut groups: BTreeMap<(i64, String, String), Vec<Stored>> = BTreeMap::new();
    for setup in stored {
        let Ok(values) = serde_json::from_str::<BTreeMap<String, String>>(&setup.values_json)
        else {
            report.unreadable.push(setup.id);
            continue;
        };
        let key = (setup.customer, setup.car.clone(), fingerprint(&values));
        groups.entry(key).or_default().push(setup);
    }

    let mut rewrite: Vec<(i64, String)> = Vec::new();
    for ((_, _, wanted), members) in groups {
        let kept = if members.len() > 1 {
            let (done, survivor) = merge(&tx, members)?;
            report.merges.push(done);
            survivor
        } else {
            members.into_iter().next().expect("a group has members")
        };
        if kept.fingerprint != wanted {
            rewrite.push((kept.id, wanted));
        }
    }
    // Two passes: no row holds another's new fingerprint midway (UNIQUE per driver and car).
    for (id, _) in &rewrite {
        tx.execute(
            "UPDATE setups SET values_fingerprint = 'reconciling:' || setup_id WHERE setup_id = ?1",
            [id],
        )?;
    }
    for (id, wanted) in &rewrite {
        tx.execute(
            "UPDATE setups SET values_fingerprint = ?2 WHERE setup_id = ?1",
            params![id, wanted],
        )?;
    }
    report.fingerprints_rewritten = rewrite.len();
    if report.merges.iter().any(|m| m.notes_moved > 0) {
        // a note's subject moved: user-authored data changed (`db-schema.sql`, server_counters)
        tx.execute(
            "UPDATE server_counters SET value = value + 1 WHERE name = 'user_data_generation'",
            [],
        )?;
    }
    tx.commit()?;
    Ok(report)
}

fn append_log(app: &AppState, line: &Value) -> std::io::Result<PathBuf> {
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

/// [`reconcile`] at start-up, on the writer thread. What changed goes to the server log and, as
/// one line, to `$COACH_DATA_DIR/run/setup-identity.jsonl`. A failure is logged and leaves every
/// row as it was (one transaction); the next start tries again.
pub async fn reconcile_on_start(app: &AppState) {
    let parser = app.parser.clone();
    let outcome = app
        .store
        .write(move |conn| Ok(reconcile(conn, &|values| parser.fingerprint(values))?))
        .await;
    let report = match outcome {
        Ok(report) => report,
        Err(e) => {
            tracing::error!(error = %e, "setup identity check failed; setups left as they were");
            return;
        }
    };
    let line = json!({
        "at": iso_now(),
        "setup_keys_version": coach_wire::setup_keys::VERSION,
        "report": report,
    });
    if report.changed() {
        tracing::warn!(report = %line, "setup identity reconciled");
        if let Err(e) = append_log(app, &line) {
            tracing::error!(error = %e, "setup identity log could not be written");
        }
    } else if !report.unreadable.is_empty() || !report.kept_for_notes.is_empty() {
        tracing::warn!(report = %line, "setup identity unchanged; some setups need a look");
    } else {
        tracing::info!(setups = report.setups_checked, "setup identity unchanged");
    }
}

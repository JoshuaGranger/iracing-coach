//! Offline stints of one iRacing session (Test Drive, offline practice) as one library session.
//!
//! iRacing starts a new `.ibt` file each time the driver gets back in the car. A hosted session
//! keeps its files together by `SubSessionID` and session number; an offline session has
//! `SubSessionID` 0 and `SessionID` 0, and the worker's discovery keys such a recording by the
//! file's own wall-clock start, so every stint became a session of its own (the bot run of
//! 2026-10-04: sessions 658 and 659 for the two files of one Test Drive).
//!
//! The session's own start identifies it: the disk header's `sessionStartDate` (wall clock at the
//! file's start) minus `sessionStartTime` (the sim's `SessionTime` then) is the wall-clock time at
//! which the sim session's clock started, the same for every file of the session to the second
//! (`sessionStartDate` has whole seconds); a new Test Drive restarts the clock. Two recordings are
//! stints of one session when they have the same driver, car, track and configuration, session
//! number, `SessionUniqueID` and session type, both have `SubSessionID` and `SessionID` 0 (or
//! none), their session starts agree within [`ZERO_TOLERANCE_S`] and they do not overlap in time.
//!
//! [`stint_of`] reads that from a recording's stored `header_json` (the ingest stores the two
//! disk-header times since schema version 2 of this module; [`backfill`] adds them to older rows).
//! New recordings join an existing session at discovery ([`joining_session`]); [`repair`] merges
//! sessions that an older server split, once and idempotently, at every start.
//!
//! **One setup per session.** A joined session is analysed with one setup, and a test day exists
//! to change the setup between stints, so a stint joins only stints of its sim session that were
//! driven on the same setup ([`joins`]): the values fingerprint of the recording's own `CarSetup`
//! (`setup-keys.json`, derived keys excluded; the `recording` observation's setup, which
//! `setup_identity` keeps under the current rule). A setup change starts another library session
//! of the same sim session; a stint that returns to an earlier setup of that sim session joins
//! that setup's session (two stints of the 2026-10-03 Las Vegas rig test are two sessions: the
//! garage test changed the left-front cold pressure from 155 to 152 kPa between them).
//!
//! **A stopped lap counter.** The sim stops counting laps at [`LAP_COUNTER_LIMIT`] in one sim
//! session: `Lap` stays 1024 (1025 for five seconds after each start/finish crossing) and
//! `LapLastLapTime` freezes, so the recording's laps cannot be told apart. Such a recording (its
//! last `Lap` sample at the limit or above, `header_json.lap_last`) is no stint: it joins nothing
//! and nothing joins it, and its analysis is deferred with `lap_counter_stopped`
//! (`handlers::analyze`). The bot's IRP Test Drive of 2026-09-25 ran one sim session for 15 h:
//! its three stints of 09-25 (laps 93-145, one setup) are one session; its eleven stints of
//! 09-26 (four setups) all record `Lap` 1024 and stay apart, where the rule without setups or
//! the lap counter made all fourteen one session.

use std::collections::{BTreeMap, BTreeSet};
use std::io::Write;

use rusqlite::{Connection, OptionalExtension, params};
use serde::Serialize;
use serde_json::{Value, json};

use crate::app::AppState;
use crate::jobs::{QueueParams, queue_analysis_tx};
use crate::util::iso_now;

const LOG_FILE: &str = "offline-sessions.jsonl";

/// Session starts of two files of one sim session differ by the whole-second rounding of
/// `sessionStartDate` (0.61 s apart on the 2026-10-03 rig stints); two Test Drives started within
/// this window of each other's clock would need the same car, track and stint order too.
pub const ZERO_TOLERANCE_S: f64 = 2.0;

/// Two stints may touch: one ends as the next begins.
const OVERLAP_TOLERANCE_S: f64 = 1.0;

/// The sim's `Lap` counter does not count past this lap of one sim session (see the module
/// comment).
pub const LAP_COUNTER_LIMIT: i64 = 1024;

/// The analysis deferral code of a recording whose lap counter stopped.
pub const LAP_COUNTER_STOPPED: &str = "lap_counter_stopped";

/// True when the recording's last `Lap` sample (`header_json.lap_last`, stored by the parser and
/// [`backfill`]) is at [`LAP_COUNTER_LIMIT`] or above. False when unknown.
pub fn lap_counter_stopped(header: &Value) -> bool {
    header["lap_last"]
        .as_i64()
        .is_some_and(|lap| lap >= LAP_COUNTER_LIMIT)
}

/// What identifies the sim session, besides its start.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize)]
pub struct StintKey {
    pub customer_id: i64,
    pub car_path: String,
    /// `TrackID`, else the track name.
    pub track: String,
    pub track_config: String,
    pub session_num: Option<i64>,
    pub session_unique_id: Option<i64>,
    pub session_type: String,
}

/// One recording of an offline session.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Stint {
    pub sha256: String,
    pub key: StintKey,
    /// Wall-clock time (Unix seconds) at which the sim session's clock started.
    pub session_zero: f64,
    pub start: f64,
    pub end: f64,
    /// Values fingerprint of the recording's own setup; None when it has no `CarSetup`.
    pub setup: Option<String>,
}

fn number(v: &Value) -> Option<f64> {
    v.as_f64().filter(|n| n.is_finite())
}

fn text(v: &Value) -> Option<String> {
    v.as_str()
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string)
}

fn offline_id(v: &Value) -> bool {
    v.is_null() || v.as_i64() == Some(0)
}

/// The stint a recording is, from its stored `header_json` (`adapters::IbtParser`) and the values
/// fingerprint of its own setup ([`recording_setup`]): None for a hosted recording, one timed by
/// file modification only, one stored before the ingest kept the disk header's
/// `session_start_time`, or one whose lap counter stopped ([`lap_counter_stopped`]).
pub fn stint_of(
    sha256: &str,
    customer_id: i64,
    header: &Value,
    setup: Option<&str>,
) -> Option<Stint> {
    let summary = &header["summary"];
    if !offline_id(&summary["subsession_id"]) || !offline_id(&summary["session_id"]) {
        return None;
    }
    if lap_counter_stopped(header) {
        return None;
    }
    if summary["time_source"] != "disk_header" {
        return None;
    }
    let date = number(&header["session_start_date"])?;
    let clock = number(&header["session_start_time"])?;
    let start = number(&summary["start_time_unix"])?;
    let end = number(&summary["end_time_unix"]).unwrap_or(start);
    let track = summary["track_id"]
        .as_i64()
        .map(|id| id.to_string())
        .or_else(|| text(&summary["track_name"]))?;
    Some(Stint {
        sha256: sha256.to_string(),
        key: StintKey {
            customer_id,
            car_path: text(&summary["car_path"])?,
            track,
            track_config: text(&summary["track_config_name"]).unwrap_or_default(),
            session_num: summary["sim_session_num"].as_i64(),
            session_unique_id: summary["session_unique_id"].as_i64(),
            session_type: text(&summary["sim_session_type"])
                .or_else(|| text(&summary["event_type"]))?,
        },
        session_zero: date - clock,
        start,
        end: end.max(start),
        setup: setup.map(str::to_string),
    })
}

/// True when `a` and `b` are two files of one sim session (its identity: driver, car, track,
/// session number, `SessionUniqueID`, type and start; no overlap). Says nothing about the setup.
pub fn same_session(a: &Stint, b: &Stint) -> bool {
    a.key == b.key
        && (a.session_zero - b.session_zero).abs() <= ZERO_TOLERANCE_S
        && (a.end <= b.start + OVERLAP_TOLERANCE_S || b.end <= a.start + OVERLAP_TOLERANCE_S)
}

/// True when `a` and `b` belong in one library session: two files of one sim session driven on
/// the same setup (equal values fingerprints, or neither recording has a `CarSetup`).
pub fn joins(a: &Stint, b: &Stint) -> bool {
    same_session(a, b) && a.setup == b.setup
}

/// Groups stints into library sessions ([`joins`]: one sim session and one setup), each in time
/// order; the groups come in order of their first stint.
pub fn group(mut stints: Vec<Stint>) -> Vec<Vec<Stint>> {
    stints.sort_by(|a, b| {
        a.key
            .cmp(&b.key)
            .then(a.setup.cmp(&b.setup))
            .then(a.start.total_cmp(&b.start))
            .then(a.sha256.cmp(&b.sha256))
    });
    let mut out: Vec<Vec<Stint>> = Vec::new();
    for stint in stints {
        match out.last_mut() {
            Some(current) if current.iter().all(|s| joins(s, &stint)) => current.push(stint),
            _ => out.push(vec![stint]),
        }
    }
    out.sort_by(|a, b| a[0].start.total_cmp(&b[0].start));
    out
}

/// The values fingerprint of the setup a recording was driven on, as SQL over `r.sha256`: the
/// setup its `recording` observation names (the ingest records one from the file's `CarSetup`;
/// `setup_identity` keeps the fingerprint under the current `setup-keys.json` rule). NULL when
/// the recording has no `CarSetup`.
const RECORDING_SETUP_SQL: &str = "(SELECT st.values_fingerprint FROM setup_observations o
     JOIN setups st ON st.setup_id = o.setup_id
     WHERE o.source = 'recording' AND o.recording_sha256 = r.sha256
     ORDER BY o.observation_id LIMIT 1)";

/// The values fingerprint of the setup recording `sha256` was driven on ([`RECORDING_SETUP_SQL`]).
pub fn recording_setup(conn: &Connection, sha256: &str) -> rusqlite::Result<Option<String>> {
    conn.query_row(
        &format!("SELECT {RECORDING_SETUP_SQL} FROM recordings r WHERE r.sha256 = ?1"),
        [sha256],
        |r| r.get(0),
    )
    .optional()
    .map(Option::flatten)
}

/// One recording that may be a stint (`candidate_rows`).
struct Candidate {
    session_id: i64,
    sha256: String,
    customer_id: i64,
    header: Value,
    setup: Option<String>,
}

impl Candidate {
    fn stint(&self) -> Option<Stint> {
        stint_of(
            &self.sha256,
            self.customer_id,
            &self.header,
            self.setup.as_deref(),
        )
    }
}

/// Recordings that may be stints: members of a session with no subsession and a group the worker
/// keys without a path.
fn candidate_rows(conn: &Connection, customer: Option<i64>) -> rusqlite::Result<Vec<Candidate>> {
    conn.prepare(&format!(
        "SELECT sr.session_id, r.sha256, s.customer_id, r.header_json, {RECORDING_SETUP_SQL}
         FROM session_recordings sr
         JOIN sessions s ON s.session_id = sr.session_id
         JOIN recordings r ON r.sha256 = sr.recording_sha256
         WHERE s.subsession_id IS NULL AND s.group_key NOT LIKE 'local-file:%'
           AND (?1 IS NULL OR s.customer_id = ?1)
         ORDER BY sr.session_id, r.sha256"
    ))?
    .query_map([customer], |r| {
        let header: String = r.get(3)?;
        Ok(Candidate {
            session_id: r.get(0)?,
            sha256: r.get(1)?,
            customer_id: r.get(2)?,
            header: serde_json::from_str(&header).unwrap_or(Value::Null),
            setup: r.get(4)?,
        })
    })?
    .collect()
}

/// The session a newly discovered offline recording belongs to, when another stint of its sim
/// session driven on the same setup already has one ([`joins`]; `handlers::apply_discovery`).
/// None for a hosted recording, a recording already in a session, one whose lap counter stopped,
/// or the first stint of its sim session on its setup.
pub fn joining_session(
    conn: &Connection,
    sha256: &str,
    customer: i64,
) -> rusqlite::Result<Option<i64>> {
    let already: bool = conn.query_row(
        "SELECT EXISTS(SELECT 1 FROM session_recordings WHERE recording_sha256 = ?1)",
        [sha256],
        |r| r.get(0),
    )?;
    if already {
        return Ok(None);
    }
    let header: Option<String> = conn
        .query_row(
            "SELECT header_json FROM recordings WHERE sha256 = ?1",
            [sha256],
            |r| r.get(0),
        )
        .optional()?;
    let setup = recording_setup(conn, sha256)?;
    let Some(new) = header
        .and_then(|h| serde_json::from_str::<Value>(&h).ok())
        .and_then(|h| stint_of(sha256, customer, &h, setup.as_deref()))
    else {
        return Ok(None);
    };
    let mut by_session: BTreeMap<i64, Vec<Stint>> = BTreeMap::new();
    for candidate in candidate_rows(conn, Some(customer))? {
        if let Some(stint) = candidate.stint() {
            by_session
                .entry(candidate.session_id)
                .or_default()
                .push(stint);
        }
    }
    // the session whose every stint is of the same sim session and setup, the earliest such first
    Ok(by_session
        .into_iter()
        .filter(|(_, stints)| stints.iter().all(|s| joins(s, &new)))
        .min_by(|a, b| {
            let first = |s: &[Stint]| s.iter().map(|x| x.start).fold(f64::INFINITY, f64::min);
            first(&a.1).total_cmp(&first(&b.1)).then(a.0.cmp(&b.0))
        })
        .map(|(id, _)| id))
}

/// Sessions of one sim session merged into the first.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Merge {
    pub customer_id: i64,
    pub survivor: i64,
    pub merged: Vec<i64>,
    pub recordings: usize,
    pub car_path: String,
    pub track: String,
    pub session_type: String,
    /// The sim session's start (UTC).
    pub session_start_utc: Option<String>,
    /// The setup the merged stints were driven on (values fingerprint).
    pub setup_fingerprint: Option<String>,
}

#[derive(Debug, Clone, Default, PartialEq, Serialize)]
pub struct Report {
    pub dry_run: bool,
    /// Recordings of offline sessions looked at.
    pub recordings_checked: usize,
    /// Of those, recordings whose stored header has no session start time (not judged).
    pub recordings_without_session_time: usize,
    /// Of those, recordings whose lap counter stopped ([`lap_counter_stopped`]): never joined.
    pub recordings_lap_counter_stopped: usize,
    pub merges: Vec<Merge>,
    /// Groups left alone because an analysis of one of their sessions is running.
    pub skipped_running: Vec<Vec<i64>>,
    /// Sessions whose stints the rule puts in more than one session (a setup change inside a
    /// session joined under an earlier rule): reported, left as they are, and no group that
    /// touches one is merged.
    pub sessions_with_mixed_stints: Vec<i64>,
}

impl Report {
    pub fn sessions_merged(&self) -> usize {
        self.merges.iter().map(|m| m.merged.len()).sum()
    }
}

fn analysis_running(conn: &Connection, sessions: &[i64]) -> rusqlite::Result<bool> {
    conn.query_row(
        "SELECT EXISTS(SELECT 1 FROM jobs WHERE kind = 'analyze' AND state = 'running'
           AND json_extract(subject_json, '$.session_id') IN (SELECT value FROM json_each(?1)))",
        [serde_json::to_string(sessions).unwrap_or_else(|_| "[]".into())],
        |r| r.get(0),
    )
}

fn bump_generation(conn: &Connection) -> rusqlite::Result<()> {
    conn.execute(
        "UPDATE server_counters SET value = value + 1 WHERE name = 'user_data_generation'",
        [],
    )?;
    Ok(())
}

/// Moves session `gone` into `survivor`: its recordings, analyses (kept, not current), notes, the
/// driver's remembered investigation and the start time; drops its unfinished analysis jobs and
/// the row. The caller queues the survivor's analysis.
fn merge_session(conn: &Connection, survivor: i64, gone: i64) -> rusqlite::Result<()> {
    conn.execute(
        "INSERT OR IGNORE INTO session_recordings(session_id, recording_sha256)
         SELECT ?1, recording_sha256 FROM session_recordings WHERE session_id = ?2",
        params![survivor, gone],
    )?;
    conn.execute(
        "DELETE FROM session_recordings WHERE session_id = ?1",
        [gone],
    )?;
    conn.execute(
        "UPDATE analyses SET session_id = ?1 WHERE session_id = ?2",
        params![survivor, gone],
    )?;
    let notes = conn.execute(
        "UPDATE notes SET subject_id = ?1 WHERE subject_kind = 'session' AND subject_id = ?2",
        params![survivor.to_string(), gone.to_string()],
    )?;
    let prefs = conn.execute(
        "UPDATE driver_prefs SET last_investigation_json = json_set(last_investigation_json, '$.session_id', ?1),
           version = version + 1, updated_at = ?3
         WHERE json_valid(last_investigation_json)
           AND json_extract(last_investigation_json, '$.session_id') = ?2",
        params![survivor, gone, iso_now()],
    )?;
    if notes + prefs > 0 {
        bump_generation(conn)?;
    }
    conn.execute(
        "DELETE FROM jobs WHERE kind = 'analyze' AND state IN ('queued', 'deferred', 'failed', 'cancelled', 'interrupted')
           AND json_extract(subject_json, '$.session_id') = ?1",
        [gone],
    )?;
    conn.execute(
        "UPDATE sessions SET start_time_utc = (SELECT MIN(start_time_utc) FROM sessions WHERE session_id IN (?1, ?2))
         WHERE session_id = ?1",
        params![survivor, gone],
    )?;
    conn.execute("DELETE FROM sessions WHERE session_id = ?1", [gone])?;
    Ok(())
}

/// Merges every offline session an older server split into one session per sim session and
/// setup ([`group`]; the session of the earliest stint keeps its id and queues an analysis of all
/// its recordings). With `dry_run` it only reports. Idempotent: once merged, every group has one
/// session. A session whose own stints fall in several groups is reported and left alone.
pub fn repair(
    conn: &mut Connection,
    queue: &QueueParams,
    dry_run: bool,
) -> rusqlite::Result<Report> {
    let tx = conn.transaction()?;
    let mut report = Report {
        dry_run,
        ..Report::default()
    };
    let mut session_of: BTreeMap<String, i64> = BTreeMap::new();
    let mut stints = Vec::new();
    for candidate in candidate_rows(&tx, None)? {
        report.recordings_checked += 1;
        match candidate.stint() {
            Some(stint) => {
                session_of.insert(candidate.sha256, candidate.session_id);
                stints.push(stint);
            }
            None => {
                if candidate.header["session_start_time"].is_null() {
                    report.recordings_without_session_time += 1;
                } else if lap_counter_stopped(&candidate.header) {
                    report.recordings_lap_counter_stopped += 1;
                }
            }
        }
    }
    let groups = group(stints);
    // a session whose stints the rule puts in two groups (joined under the rule before setups
    // counted) is not merged further: report it
    let mut group_of_session: BTreeMap<i64, BTreeSet<usize>> = BTreeMap::new();
    for (index, members) in groups.iter().enumerate() {
        for stint in members {
            group_of_session
                .entry(session_of[&stint.sha256])
                .or_default()
                .insert(index);
        }
    }
    let mixed: BTreeSet<i64> = group_of_session
        .iter()
        .filter(|(_, groups)| groups.len() > 1)
        .map(|(session, _)| *session)
        .collect();
    report.sessions_with_mixed_stints = mixed.iter().copied().collect();
    for members in groups {
        let mut sessions: Vec<i64> = Vec::new();
        for stint in &members {
            let id = session_of[&stint.sha256];
            if !sessions.contains(&id) {
                sessions.push(id);
            }
        }
        if sessions.len() < 2 || sessions.iter().any(|id| mixed.contains(id)) {
            continue;
        }
        if analysis_running(&tx, &sessions)? {
            report.skipped_running.push(sessions);
            continue;
        }
        let (survivor, merged) = (sessions[0], sessions[1..].to_vec());
        let first = &members[0];
        report.merges.push(Merge {
            customer_id: first.key.customer_id,
            survivor,
            merged: merged.clone(),
            recordings: members.len(),
            car_path: first.key.car_path.clone(),
            track: first.key.track.clone(),
            session_type: first.key.session_type.clone(),
            session_start_utc: first
                .session_zero
                .is_finite()
                .then(|| crate::util::iso_from_unix(first.session_zero.round() as i64)),
            setup_fingerprint: first.setup.clone(),
        });
        if dry_run {
            continue;
        }
        for gone in &merged {
            merge_session(&tx, survivor, *gone)?;
        }
        queue_analysis_tx(&tx, queue, survivor, false, false)?;
    }
    if dry_run {
        tx.rollback()?;
    } else {
        tx.commit()?;
    }
    Ok(report)
}

/// Adds the disk header's `session_start_time` and `session_end_time`, and the last `Lap` sample
/// (`lap_last`, [`lap_counter_stopped`]), to recordings of offline sessions stored without them,
/// reading each recording from the spool or, archived, from the NAS (headers and the edge
/// samples only). Returns how many rows gained them. A recording that cannot be read is left as
/// it is (the next start tries again).
pub async fn backfill(app: &AppState) -> crate::error::Result<usize> {
    let rows: Vec<(String, String)> = app
        .store
        .read(|conn| {
            Ok(conn
                .prepare(
                    "SELECT DISTINCT r.sha256, r.header_json FROM recordings r
                     JOIN session_recordings sr ON sr.recording_sha256 = r.sha256
                     JOIN sessions s ON s.session_id = sr.session_id
                     WHERE s.subsession_id IS NULL AND s.group_key NOT LIKE 'local-file:%'
                       AND (NOT json_valid(r.header_json)
                            OR json_extract(r.header_json, '$.session_start_time') IS NULL
                            OR json_type(r.header_json, '$.lap_last') IS NULL)",
                )?
                .query_map([], |r| Ok((r.get(0)?, r.get(1)?)))?
                .collect::<rusqlite::Result<_>>()?)
        })
        .await?;
    let mut done = 0;
    for (sha, stored) in rows {
        if crate::nas_library::guard(app, &sha).await.is_err() {
            continue;
        }
        let spool = crate::ingest::ready_path(app, &sha);
        let parser = app.parser.clone();
        let times = if spool.is_file() {
            tokio::task::spawn_blocking(move || parser.parse(&spool).ok().map(|p| p.header_json))
                .await
                .ok()
                .flatten()
        } else {
            crate::nas_library::header(app, &sha).await.ok().flatten()
        };
        let Some(times) = times else {
            continue;
        };
        // a parser that does not report the lap sample (`lap_last` absent, not null) would make
        // every start read the row again
        if times["session_start_time"].is_null() || times.get("lap_last").is_none() {
            continue;
        }
        let mut header: Value = serde_json::from_str(&stored).unwrap_or_else(|_| json!({}));
        if !header.is_object() {
            header = json!({});
        }
        for key in [
            "session_start_date",
            "session_start_time",
            "session_end_time",
            "lap_last",
        ] {
            header[key] = times[key].clone();
        }
        if header["summary"].is_null() {
            header["summary"] = times["summary"].clone();
        }
        let (sha_owned, text) = (sha.clone(), header.to_string());
        app.store
            .write(move |conn| {
                conn.execute(
                    "UPDATE recordings SET header_json = ?2 WHERE sha256 = ?1",
                    params![sha_owned, text],
                )?;
                Ok(())
            })
            .await?;
        done += 1;
    }
    Ok(done)
}

fn append_log(app: &AppState, line: &Value) -> std::io::Result<()> {
    let path = app.cfg.run_dir().join(LOG_FILE);
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir)?;
    }
    let mut file = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&path)?;
    writeln!(file, "{line}")?;
    file.sync_all()
}

/// [`backfill`], then [`repair`]; what changed goes to the server log and, as one line, to
/// `$COACH_DATA_DIR/run/offline-sessions.jsonl`. Runs in the background after start (it may read
/// the NAS); a failure leaves every row as it was and the next start tries again.
pub async fn repair_on_start(app: &AppState) {
    let backfilled = match backfill(app).await {
        Ok(n) => n,
        Err(e) => {
            tracing::error!(error = %e, "offline session times could not be read; repair skipped");
            return;
        }
    };
    let queue = QueueParams::of(app);
    let outcome = app
        .store
        .write(move |conn| Ok(repair(conn, &queue, false)?))
        .await;
    let report = match outcome {
        Ok(report) => report,
        Err(e) => {
            tracing::error!(error = %e, "offline session repair failed; sessions left as they were");
            return;
        }
    };
    if !report.sessions_with_mixed_stints.is_empty() {
        tracing::warn!(
            sessions = ?report.sessions_with_mixed_stints,
            "offline sessions hold stints of more than one setup or sim session; left as they are"
        );
    }
    if report.merges.is_empty() && backfilled == 0 {
        tracing::info!(
            recordings = report.recordings_checked,
            lap_counter_stopped = report.recordings_lap_counter_stopped,
            "offline sessions unchanged"
        );
        return;
    }
    let customers: BTreeSet<i64> = report.merges.iter().map(|m| m.customer_id).collect();
    for customer in customers {
        app.events.library_changed(customer, "sessions");
    }
    app.jobs.wake_all();
    let line = json!({"at": iso_now(), "backfilled": backfilled, "report": report});
    tracing::warn!(report = %line, "offline sessions repaired");
    if let Err(e) = append_log(app, &line) {
        tracing::error!(error = %e, "offline session log could not be written");
    }
}

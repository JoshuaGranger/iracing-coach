//! The Lab library (`web-round-2-plan.md`, "Lab library"; backlog #18).
//!
//! A recording belongs to the `main` library (Joshua's own driving, the default) or to `lab` (the
//! iRacing-utility's bot, its setup harvest and the other agent-driven rig runs). The driver stays
//! who drove it; raw files are never moved or edited. A recording is filed `lab` only on the
//! utility's word, corroborated by the recording itself:
//!
//! - a **lab window** (`lab_windows`: from, until, car, track, note, source) says that the utility
//!   drives `car` at `track` in that time; the utility posts one before it drives and closes it
//!   after (`POST /api/v1/admin/library/windows`), and the historical ones are applied by the
//!   release (`docs/08-rewrite/acceptance/lab-library.md`);
//! - the recording's start (`recordings.start_time_utc`, the time the session list shows) falls
//!   inside the window (both ends included);
//! - its car (`CarPath` or the car's screen name) and track (`TrackName`, `TrackDisplayName` or
//!   `TrackID`) equal the window's, ignoring case;
//! - it is a solo offline test (no `SubSessionID`, `EventType` Test): the utility drives only
//!   offline Test Drives with one driver (iRacing-utility `AGENTS.md`), so a hosted session or a
//!   race against AI is never lab, even inside a window.
//!
//! Anything else is `main`: a recording whose start falls in a window without the matching car,
//! track or session kind stays main and is reported as an overlap. The ingest files each new
//! recording ([`file_recording`]); [`repair`] applies the windows to the stored recordings with the
//! same rule (dry run first, idempotent, logged to `run/library.jsonl`).
//!
//! A session belongs to `lab` when any of its recordings does ([`session_library_sql!`]): stints of
//! one sim session are one drive, and Lab never enters the main counts. Every session,
//! setup-usage and statistics query takes `library` (default `main`).

use std::collections::BTreeSet;
use std::io::Write;

use rusqlite::{Connection, OptionalExtension, params};
use serde::Serialize;
use serde_json::{Value, json};

use crate::app::AppState;
use crate::error::{Error, Result};
use crate::schema::parse_request;
use crate::util::{iso_from_unix, iso_now, parse_iso};

pub const LOG_FILE: &str = "library.jsonl";

/// The longest window the API accepts: a window that hides a week of driving is a mistake.
pub const MAX_WINDOW_S: i64 = 72 * 3600;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Library {
    #[default]
    Main,
    Lab,
}

impl Library {
    pub fn as_str(self) -> &'static str {
        match self {
            Library::Main => "main",
            Library::Lab => "lab",
        }
    }

    /// The `library` query parameter: absent or blank is `main`.
    pub fn from_query(value: Option<&str>) -> Result<Library> {
        match value.map(str::trim) {
            None | Some("") | Some("main") => Ok(Library::Main),
            Some("lab") => Ok(Library::Lab),
            Some(other) => Err(Error::invalid(format!(
                "library must be main or lab, not {other}"
            ))),
        }
    }
}

/// SQL for the library of the session aliased `s`: `lab` when any of its recordings is lab.
#[macro_export]
macro_rules! session_library_sql {
    () => {
        "(CASE WHEN EXISTS (SELECT 1 FROM session_recordings lib_sr JOIN recordings lib_r
            ON lib_r.sha256 = lib_sr.recording_sha256 WHERE lib_sr.session_id = s.session_id
            AND lib_r.library = 'lab') THEN 'lab' ELSE 'main' END)"
    };
}

/// Whether a setup observation counts toward the setup usage of `library`: a recording's
/// observation in its recording's library; a garage visit's or an export's (setup facts, not
/// driving) in `main` only.
pub fn observation_counts(source: &str, recording_library: Option<&str>, library: Library) -> bool {
    if source == "recording" {
        recording_library.unwrap_or("main") == library.as_str()
    } else {
        library == Library::Main
    }
}

// ------------------------------------------------------------------ the rule

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Window {
    pub window_id: i64,
    pub from: i64,
    pub until: i64,
    pub car: String,
    pub track: String,
}

/// What the rule reads of a recording (its row and the header summary the ingest stored).
#[derive(Debug, Clone, Default, PartialEq, Serialize)]
pub struct RecordingFacts {
    pub sha256: String,
    pub customer_id: Option<i64>,
    pub start_time_utc: Option<String>,
    pub car_path: Option<String>,
    pub car_name: Option<String>,
    /// `TrackDisplayName` (else `TrackName`), as the ingest stores it.
    pub track: Option<String>,
    pub track_name: Option<String>,
    pub track_id: Option<i64>,
    pub subsession_id: Option<i64>,
    pub event_type: Option<String>,
    pub session_type: Option<String>,
}

fn text(v: &Value) -> Option<String> {
    v.as_str()
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string)
}

fn same(a: &str, b: &str) -> bool {
    a.trim().eq_ignore_ascii_case(b.trim())
}

impl RecordingFacts {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        sha256: String,
        customer_id: Option<i64>,
        start_time_utc: Option<String>,
        car_path: Option<String>,
        car_name: Option<String>,
        track: Option<String>,
        subsession_id: Option<i64>,
        header_json: &str,
    ) -> RecordingFacts {
        let header: Value = serde_json::from_str(header_json).unwrap_or(Value::Null);
        let summary = &header["summary"];
        RecordingFacts {
            sha256,
            customer_id,
            start_time_utc,
            car_path: car_path.or_else(|| text(&summary["car_path"])),
            car_name: car_name.or_else(|| text(&summary["car_screen_name"])),
            track: track.or_else(|| text(&summary["track_display_name"])),
            track_name: text(&summary["track_name"]),
            track_id: summary["track_id"].as_i64(),
            subsession_id: subsession_id.or_else(|| summary["subsession_id"].as_i64()),
            event_type: text(&summary["event_type"]),
            session_type: text(&summary["sim_session_type"]),
        }
    }

    pub fn start(&self) -> Option<i64> {
        self.start_time_utc.as_deref().and_then(parse_iso)
    }

    fn car_matches(&self, car: &str) -> bool {
        [&self.car_path, &self.car_name]
            .into_iter()
            .flatten()
            .any(|c| same(c, car))
    }

    fn track_matches(&self, track: &str) -> bool {
        [&self.track_name, &self.track]
            .into_iter()
            .flatten()
            .any(|t| same(t, track))
            || self.track_id.is_some_and(|id| same(&id.to_string(), track))
    }

    /// No hosted subsession, and an offline test (`EventType` Test; without it, a session type
    /// that names a test, as `Offline Testing`).
    pub fn solo_offline_test(&self) -> bool {
        if self.subsession_id.is_some_and(|id| id != 0) {
            return false;
        }
        match &self.event_type {
            Some(kind) => same(kind, "test"),
            None => self
                .session_type
                .as_deref()
                .is_some_and(|t| t.to_ascii_lowercase().contains("test")),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Verdict {
    /// Filed lab by the first matching window (lowest id).
    Lab { window_id: i64 },
    /// No window holds the recording's start.
    Main,
    /// A window holds its start, but no such window matches: stays main. `reasons` are those of
    /// the first such window: `car`, `track`, `not_solo_offline_test`.
    Overlap {
        window_id: i64,
        reasons: Vec<&'static str>,
    },
}

impl Verdict {
    pub fn library(&self) -> Library {
        match self {
            Verdict::Lab { .. } => Library::Lab,
            _ => Library::Main,
        }
    }

    pub fn window_id(&self) -> Option<i64> {
        match self {
            Verdict::Lab { window_id } => Some(*window_id),
            _ => None,
        }
    }
}

/// The library `rec` belongs to under `windows`.
pub fn classify(rec: &RecordingFacts, windows: &[Window]) -> Verdict {
    let Some(start) = rec.start() else {
        return Verdict::Main;
    };
    let mut overlap: Option<Verdict> = None;
    for w in windows
        .iter()
        .filter(|w| w.from <= start && start <= w.until)
    {
        let mut reasons = Vec::new();
        if !rec.car_matches(&w.car) {
            reasons.push("car");
        }
        if !rec.track_matches(&w.track) {
            reasons.push("track");
        }
        if !rec.solo_offline_test() {
            reasons.push("not_solo_offline_test");
        }
        if reasons.is_empty() {
            return Verdict::Lab {
                window_id: w.window_id,
            };
        }
        overlap.get_or_insert(Verdict::Overlap {
            window_id: w.window_id,
            reasons,
        });
    }
    overlap.unwrap_or(Verdict::Main)
}

// ------------------------------------------------------------------ storage

pub fn load_windows(conn: &Connection) -> rusqlite::Result<Vec<Window>> {
    let mut stmt = conn.prepare(
        "SELECT window_id, from_utc, until_utc, car, track FROM lab_windows ORDER BY window_id",
    )?;
    let rows = stmt
        .query_map([], |r| {
            Ok((
                r.get::<_, i64>(0)?,
                r.get::<_, String>(1)?,
                r.get::<_, String>(2)?,
                r.get::<_, String>(3)?,
                r.get::<_, String>(4)?,
            ))
        })?
        .collect::<rusqlite::Result<Vec<_>>>()?;
    Ok(rows
        .into_iter()
        .filter_map(|(id, from, until, car, track)| {
            Some(Window {
                window_id: id,
                from: parse_iso(&from)?,
                until: parse_iso(&until)?,
                car,
                track,
            })
        })
        .collect())
}

const RECORDING_SELECT: &str =
    "SELECT sha256, customer_id, start_time_utc, car_path, car_name, track,
    subsession_id, header_json, library, library_window_id FROM recordings";

fn recording_row(r: &rusqlite::Row<'_>) -> rusqlite::Result<(RecordingFacts, String, Option<i64>)> {
    let header: String = r.get(7)?;
    Ok((
        RecordingFacts::new(
            r.get(0)?,
            r.get(1)?,
            r.get(2)?,
            r.get(3)?,
            r.get(4)?,
            r.get(5)?,
            r.get(6)?,
            &header,
        ),
        r.get(8)?,
        r.get(9)?,
    ))
}

/// Files one recording by the stored windows (the ingest, in its transaction, after the row is
/// parsed). Returns the library.
pub fn file_recording(conn: &Connection, sha256: &str) -> rusqlite::Result<Library> {
    let row = conn
        .query_row(
            &format!("{RECORDING_SELECT} WHERE sha256 = ?1"),
            [sha256],
            recording_row,
        )
        .optional()?;
    let Some((facts, _, _)) = row else {
        return Ok(Library::Main);
    };
    let verdict = classify(&facts, &load_windows(conn)?);
    conn.execute(
        "UPDATE recordings SET library = ?2, library_window_id = ?3 WHERE sha256 = ?1",
        params![sha256, verdict.library().as_str(), verdict.window_id()],
    )?;
    Ok(verdict.library())
}

// ------------------------------------------------------------------ repair

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Change {
    pub sha256: String,
    pub customer_id: Option<i64>,
    pub start_time_utc: Option<String>,
    pub car_path: Option<String>,
    pub track: Option<String>,
    pub from: Library,
    pub to: Library,
    pub window_id: Option<i64>,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Overlap {
    pub sha256: String,
    pub customer_id: Option<i64>,
    pub start_time_utc: Option<String>,
    pub car_path: Option<String>,
    pub track: Option<String>,
    pub window_id: i64,
    pub reasons: Vec<&'static str>,
}

#[derive(Debug, Clone, Default, PartialEq, Serialize)]
pub struct Report {
    pub dry_run: bool,
    pub windows: usize,
    /// Parsed recordings looked at (one driver with `customer_id`).
    pub recordings_checked: usize,
    /// Lab recordings after the call (what a real call leaves).
    pub lab_recordings: usize,
    /// Recordings whose library the call changes (`from`, `to`).
    pub changes: Vec<Change>,
    /// Recordings whose start falls in a window without its car, track or session kind: main.
    pub overlaps_kept_main: Vec<Overlap>,
    /// The log file a real call wrote its line to.
    pub log: Option<String>,
}

/// Applies the stored windows to every parsed recording (one driver with `customer`): what
/// [`file_recording`] would decide now. With `dry_run` nothing changes. Idempotent: a second call
/// changes nothing.
pub fn repair(
    conn: &mut Connection,
    customer: Option<i64>,
    dry_run: bool,
) -> rusqlite::Result<Report> {
    let tx = conn.transaction()?;
    let windows = load_windows(&tx)?;
    let rows = tx
        .prepare(&format!(
            "{RECORDING_SELECT} WHERE ingest_state IN ('parsed', 'discovered')
               AND (?1 IS NULL OR customer_id = ?1) ORDER BY start_time_utc, sha256"
        ))?
        .query_map([customer], recording_row)?
        .collect::<rusqlite::Result<Vec<_>>>()?;
    let mut report = Report {
        dry_run,
        windows: windows.len(),
        recordings_checked: rows.len(),
        ..Report::default()
    };
    for (facts, stored, stored_window) in rows {
        let verdict = classify(&facts, &windows);
        let (library, window) = (verdict.library(), verdict.window_id());
        if library == Library::Lab {
            report.lab_recordings += 1;
        }
        if let Verdict::Overlap { window_id, reasons } = &verdict {
            report.overlaps_kept_main.push(Overlap {
                sha256: facts.sha256.clone(),
                customer_id: facts.customer_id,
                start_time_utc: facts.start_time_utc.clone(),
                car_path: facts.car_path.clone(),
                track: facts.track.clone(),
                window_id: *window_id,
                reasons: reasons.clone(),
            });
        }
        let was = if stored == "lab" {
            Library::Lab
        } else {
            Library::Main
        };
        if was != library {
            report.changes.push(Change {
                sha256: facts.sha256.clone(),
                customer_id: facts.customer_id,
                start_time_utc: facts.start_time_utc.clone(),
                car_path: facts.car_path.clone(),
                track: facts.track.clone(),
                from: was,
                to: library,
                window_id: window,
            });
        }
        if !dry_run && (was != library || stored_window != window) {
            tx.execute(
                "UPDATE recordings SET library = ?2, library_window_id = ?3 WHERE sha256 = ?1",
                params![facts.sha256, library.as_str(), window],
            )?;
        }
    }
    if dry_run {
        tx.rollback()?;
    } else {
        tx.commit()?;
    }
    Ok(report)
}

// ------------------------------------------------------------------ windows

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WindowWrite {
    pub from: i64,
    pub until: i64,
    pub car: String,
    pub track: String,
    pub note: String,
    pub source: String,
}

/// A `LabWindowWrite` body: times with an offset or `Z` (whole seconds kept), `until` not before
/// `from`, at most [`MAX_WINDOW_S`] long.
pub fn parse_window(body: &[u8]) -> Result<WindowWrite> {
    let value = parse_request("LabWindowWrite", body)?;
    let time = |key: &str| {
        value[key]
            .as_str()
            .and_then(parse_iso)
            .ok_or_else(|| Error::invalid(format!("{key} must be an ISO 8601 date-time")))
    };
    let (from, until) = (time("from")?, time("until")?);
    if until < from {
        return Err(Error::invalid("until must not be before from"));
    }
    if until - from > MAX_WINDOW_S {
        return Err(Error::invalid(format!(
            "a window may last at most {} hours",
            MAX_WINDOW_S / 3600
        )));
    }
    let field = |key: &str| text(&value[key]).unwrap_or_default();
    let (car, track) = (field("car"), field("track"));
    if car.is_empty() || track.is_empty() {
        return Err(Error::invalid("car and track must not be blank"));
    }
    let source = text(&value["source"]).unwrap_or_else(|| "api".to_string());
    Ok(WindowWrite {
        from,
        until,
        car,
        track,
        note: value["note"]
            .as_str()
            .unwrap_or_default()
            .trim()
            .to_string(),
        source,
    })
}

/// Stores a window. A window with the same start, car and track is the same window: its end,
/// note and source are replaced (the utility closes a window by posting it again with the time it
/// stopped). Returns the id and whether it was created.
pub fn upsert_window(conn: &Connection, w: &WindowWrite) -> rusqlite::Result<(i64, bool)> {
    let now = iso_now();
    let from = iso_from_unix(w.from);
    let existing: Option<i64> = conn
        .query_row(
            "SELECT window_id FROM lab_windows WHERE from_utc = ?1 AND lower(car) = lower(?2)
               AND lower(track) = lower(?3)",
            params![from, w.car, w.track],
            |r| r.get(0),
        )
        .optional()?;
    if let Some(id) = existing {
        conn.execute(
            "UPDATE lab_windows SET until_utc = ?2, note = ?3, source = ?4, updated_at = ?5
             WHERE window_id = ?1",
            params![id, iso_from_unix(w.until), w.note, w.source, now],
        )?;
        return Ok((id, false));
    }
    conn.execute(
        "INSERT INTO lab_windows(from_utc, until_utc, car, track, note, source, created_at, updated_at)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?7)",
        params![
            from,
            iso_from_unix(w.until),
            w.car,
            w.track,
            w.note,
            w.source,
            now
        ],
    )?;
    Ok((conn.last_insert_rowid(), true))
}

const WINDOW_SELECT: &str = "SELECT w.window_id, w.from_utc, w.until_utc, w.car, w.track, w.note,
    w.source, w.created_at, w.updated_at,
    (SELECT COUNT(*) FROM recordings r WHERE r.library_window_id = w.window_id AND r.library = 'lab')
    FROM lab_windows w";

fn window_value(r: &rusqlite::Row<'_>) -> rusqlite::Result<Value> {
    Ok(json!({
        "window_id": r.get::<_, i64>(0)?,
        "from": r.get::<_, String>(1)?,
        "until": r.get::<_, String>(2)?,
        "car": r.get::<_, String>(3)?,
        "track": r.get::<_, String>(4)?,
        "note": r.get::<_, String>(5)?,
        "source": r.get::<_, String>(6)?,
        "created_at": r.get::<_, String>(7)?,
        "updated_at": r.get::<_, String>(8)?,
        "recording_count": r.get::<_, i64>(9)?,
    }))
}

/// One window as `LabWindow`.
pub fn window(conn: &Connection, window_id: i64) -> rusqlite::Result<Option<Value>> {
    conn.query_row(
        &format!("{WINDOW_SELECT} WHERE w.window_id = ?1"),
        [window_id],
        window_value,
    )
    .optional()
}

/// Every window, newest start first (`LabWindowList`).
pub fn list_windows(conn: &Connection) -> rusqlite::Result<Value> {
    let windows = conn
        .prepare(&format!(
            "{WINDOW_SELECT} ORDER BY w.from_utc DESC, w.window_id DESC LIMIT 1000"
        ))?
        .query_map([], window_value)?
        .collect::<rusqlite::Result<Vec<_>>>()?;
    Ok(json!({"windows": windows}))
}

// ------------------------------------------------------------------ admin calls

fn append_log(app: &AppState, line: &Value) -> std::io::Result<std::path::PathBuf> {
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

/// `POST /api/v1/admin/library/windows`: (created, `LabWindow`). Logged to `run/library.jsonl`.
/// Recordings already stored are not refiled here: that is the classify repair's job.
pub async fn post_window(app: &AppState, body: &[u8]) -> Result<(bool, Value)> {
    let write = parse_window(body)?;
    let (created, value) = app
        .store
        .write(move |conn| {
            let tx = conn.transaction()?;
            let (id, created) = upsert_window(&tx, &write)?;
            let value = window(&tx, id)?.unwrap_or(Value::Null);
            tx.commit()?;
            Ok((created, value))
        })
        .await?;
    let line = json!({"at": iso_now(), "action": if created { "window_created" } else { "window_updated" },
                      "window": value});
    tracing::info!(window = %line, "lab window stored");
    if let Err(e) = append_log(app, &line) {
        tracing::error!(error = %e, "library log could not be written");
    }
    Ok((created, value))
}

/// `GET /api/v1/admin/library/windows`.
pub async fn get_windows(app: &AppState) -> Result<Value> {
    app.store.read(|conn| Ok(list_windows(conn)?)).await
}

/// `POST /api/v1/admin/library/classify`: the repair; a dry run unless `dry_run` is false. A real
/// call is logged (one line with the report) and announces the changed drivers' libraries.
pub async fn classify_stored(app: &AppState, body: &[u8]) -> Result<Value> {
    let value = if body.iter().all(u8::is_ascii_whitespace) {
        json!({})
    } else {
        parse_request("LibraryClassifyRequest", body)?
    };
    let customer = value["customer_id"].as_i64();
    let dry_run = value["dry_run"].as_bool().unwrap_or(true);
    // the dry run plans in a transaction it rolls back, on the writer like the real call
    let mut report = app
        .store
        .write(move |conn| Ok(repair(conn, customer, dry_run)?))
        .await?;
    if !dry_run {
        let line = json!({"at": iso_now(), "action": "classify",
                          "request": {"customer_id": customer}, "report": report});
        tracing::warn!(
            changes = report.changes.len(),
            lab = report.lab_recordings,
            "lab library classify applied"
        );
        match append_log(app, &line) {
            Ok(path) => report.log = Some(path.to_string_lossy().into_owned()),
            Err(e) => tracing::error!(error = %e, "library log could not be written"),
        }
        let customers: BTreeSet<i64> = report
            .changes
            .iter()
            .filter_map(|c| c.customer_id)
            .collect();
        for customer in customers {
            app.events.library_changed(customer, "sessions");
            app.events.library_changed(customer, "setups");
        }
    }
    Ok(serde_json::to_value(&report).unwrap_or(Value::Null))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn facts(start: &str, car: &str, track: &str, event: &str) -> RecordingFacts {
        RecordingFacts {
            sha256: "a".into(),
            customer_id: Some(1),
            start_time_utc: Some(start.into()),
            car_path: Some(car.into()),
            car_name: Some("Toyota Supra".into()),
            track: Some("Lucas Oil Indianapolis Raceway Park".into()),
            track_name: Some(track.into()),
            track_id: Some(232),
            subsession_id: Some(0),
            event_type: Some(event.into()),
            session_type: Some("Offline Testing".into()),
        }
    }

    fn window(id: i64, from: &str, until: &str) -> Window {
        Window {
            window_id: id,
            from: parse_iso(from).unwrap(),
            until: parse_iso(until).unwrap(),
            car: "stockcars2 supra2019".into(),
            track: "irp".into(),
        }
    }

    #[test]
    fn the_rule() {
        let w = [window(
            7,
            "2026-10-04T08:00:00-05:00",
            "2026-10-04T08:52:00-05:00",
        )];
        let bot = facts(
            "2026-10-04T13:11:20Z",
            "stockcars2 supra2019",
            "irp",
            "Test",
        );
        assert_eq!(classify(&bot, &w), Verdict::Lab { window_id: 7 });
        // the ends are inside
        let mut edge = bot.clone();
        edge.start_time_utc = Some("2026-10-04T13:52:00Z".into());
        assert_eq!(classify(&edge, &w).library(), Library::Lab);
        edge.start_time_utc = Some("2026-10-04T13:52:01Z".into());
        assert_eq!(classify(&edge, &w), Verdict::Main);
        // display name and track id match too, any case
        let mut by_name = bot.clone();
        by_name.track_name = None;
        let mut named = w.clone();
        named[0].track = "lucas oil indianapolis raceway park".into();
        assert_eq!(classify(&by_name, &named).library(), Library::Lab);
        named[0].track = "232".into();
        assert_eq!(classify(&by_name, &named).library(), Library::Lab);
        // another car or track in the window: main, reported
        let other = facts(
            "2026-10-04T13:20:00Z",
            "trucks toyotatundra2022",
            "irp",
            "Test",
        );
        assert_eq!(
            classify(&other, &w),
            Verdict::Overlap {
                window_id: 7,
                reasons: vec!["car"]
            }
        );
        // a race against AI in the window: main
        let race = facts(
            "2026-10-04T13:20:00Z",
            "stockcars2 supra2019",
            "irp",
            "Race",
        );
        assert_eq!(
            classify(&race, &w),
            Verdict::Overlap {
                window_id: 7,
                reasons: vec!["not_solo_offline_test"]
            }
        );
        // a hosted session in the window: main
        let mut hosted = bot.clone();
        hosted.subsession_id = Some(87_697_310);
        assert_eq!(classify(&hosted, &w).library(), Library::Main);
        // no start: main
        let mut timeless = bot.clone();
        timeless.start_time_utc = None;
        assert_eq!(classify(&timeless, &w), Verdict::Main);
    }

    #[test]
    fn without_an_event_type_the_session_type_decides() {
        let mut rec = facts(
            "2026-10-04T13:11:20Z",
            "stockcars2 supra2019",
            "irp",
            "Test",
        );
        rec.event_type = None;
        assert!(rec.solo_offline_test());
        rec.session_type = Some("Race".into());
        assert!(!rec.solo_offline_test());
        rec.session_type = None;
        assert!(!rec.solo_offline_test());
    }

    #[test]
    fn the_query_parameter() {
        assert_eq!(Library::from_query(None).unwrap(), Library::Main);
        assert_eq!(Library::from_query(Some("")).unwrap(), Library::Main);
        assert_eq!(Library::from_query(Some("lab")).unwrap(), Library::Lab);
        assert!(Library::from_query(Some("joshua")).is_err());
        assert!(observation_counts("recording", Some("lab"), Library::Lab));
        assert!(!observation_counts("recording", Some("lab"), Library::Main));
        assert!(observation_counts("recording", None, Library::Main));
        assert!(observation_counts("garage_commit", None, Library::Main));
        assert!(!observation_counts("garage_commit", None, Library::Lab));
    }
}

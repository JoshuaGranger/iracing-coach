//! The journal (`%LOCALAPPDATA%\iRacingCoach\journal.jsonl`): pending recordings and every garage
//! event. Append-only JSON lines, fsynced before anything is sent. A recording entry is dropped
//! when the server acknowledges `verified`; a garage entry on `garage.ack`.
//!
//! A garage commit refers to its last snapshot by `(visit_id, update_count)` instead of repeating
//! the values, so a snapshot is kept after its own ack while it is the newest of a visit whose
//! commit is not yet acknowledged.

use std::collections::BTreeMap;
use std::fs::{self, File, OpenOptions};
use std::io::{self, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

/// A recording or setup file waiting for upload.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RecordingEntry {
    pub path: String,
    pub size: u64,
    pub mtime_ms: u64,
    /// `ibt`, `sto` or `htm`.
    pub kind: String,
    /// For setup files: path under `Documents\iRacing\setups`, forward slashes.
    pub relative_path: Option<String>,
    /// Filled once the file has been hashed (a restart resumes without hashing again).
    pub sha256: Option<String>,
}

/// A file the server is known to hold, remembered so a restart does not hash or post it again.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SeenEntry {
    pub path: String,
    pub size: u64,
    pub mtime_ms: u64,
    pub sha256: String,
    /// Wall clock when the server confirmed it.
    pub at_ms: u64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SnapshotEntry {
    pub visit_id: String,
    pub update_count: i64,
    pub observed_at: String,
    pub car_path: Option<String>,
    pub driver: Option<Value>,
    pub session: Option<Value>,
    pub values: BTreeMap<String, String>,
}

/// A commit without values: they are the values of the snapshot `(visit_id, update_count)`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CommitEntry {
    pub visit_id: String,
    pub update_count: Option<i64>,
    pub entered_at: String,
    pub exited_at: String,
    pub car_path: Option<String>,
    pub driver: Option<Value>,
    pub session: Option<Value>,
    pub setup_name: Option<String>,
    pub sto_sha256: Option<String>,
    pub ambiguous: bool,
    pub is_modified: Option<bool>,
    pub load_type: Option<String>,
    /// True while the `.sto` hash is still to be computed; the commit is not sent until resolved.
    pub hash_pending: bool,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
enum Record {
    Recording(RecordingEntry),
    RecordingDone {
        path: String,
    },
    Seen(SeenEntry),
    Snapshot(SnapshotEntry),
    SnapshotAck {
        visit_id: String,
        update_count: i64,
    },
    Commit(CommitEntry),
    CommitHash {
        visit_id: String,
        sto_sha256: Option<String>,
        ambiguous: bool,
    },
    CommitAck {
        visit_id: String,
    },
}

/// A garage message ready to go on the wire, with its values resolved.
#[derive(Debug, Clone, PartialEq)]
pub enum GarageOut {
    Snapshot(SnapshotEntry),
    Commit {
        entry: CommitEntry,
        values: BTreeMap<String, String>,
    },
}

#[derive(Debug, Clone)]
struct SnapState {
    entry: SnapshotEntry,
    acked: bool,
    seq: u64,
    sent_conn: Option<u64>,
}

#[derive(Debug, Clone)]
struct CommitState {
    entry: CommitEntry,
    acked: bool,
    seq: u64,
    sent_conn: Option<u64>,
}

#[derive(Debug, Default)]
struct Visit {
    snapshots: BTreeMap<i64, SnapState>,
    commit: Option<CommitState>,
    last_seq: u64,
}

pub struct Journal {
    path: Option<PathBuf>,
    /// Opened for writing without append mode: every record is written at `len`, so a failed
    /// write can be cut off again (an append handle cannot be truncated on Windows).
    file: Option<File>,
    /// Bytes of complete records in the file.
    len: u64,
    /// A write failed: the file may carry a fragment beyond `len`, cut off before the next write.
    tainted: bool,
    /// The last attempt to open a journal that was unavailable at start.
    last_attach: Option<Instant>,
    recordings: BTreeMap<String, RecordingEntry>,
    seen: BTreeMap<String, SeenEntry>,
    visits: BTreeMap<String, Visit>,
    seq: u64,
    lines: usize,
    /// Tests: the next appends write half a line and fail.
    #[cfg(test)]
    fail_writes: u32,
}

/// How often a journal that could not be opened is opened again.
const REATTACH_EVERY: Duration = Duration::from_millis(if cfg!(test) { 0 } else { 1_000 });

/// Seen entries older than this are ignored, so a server that lost its data is refilled within a week.
pub const SEEN_TTL_MS: u64 = 7 * 24 * 3600 * 1000;
const KEEP_SEEN: usize = 4096;

/// Abandoned visits (no commit, everything acknowledged) kept at compaction.
const KEEP_ABANDONED_VISITS: usize = 8;

impl Journal {
    fn blank(path: Option<PathBuf>) -> Journal {
        Journal {
            path,
            file: None,
            len: 0,
            tainted: false,
            last_attach: None,
            recordings: BTreeMap::new(),
            seen: BTreeMap::new(),
            visits: BTreeMap::new(),
            seq: 0,
            lines: 0,
            #[cfg(test)]
            fail_writes: 0,
        }
    }

    /// An in-memory journal that writes nothing (`--dry-run` keeps its own log instead).
    pub fn in_memory() -> Journal {
        Journal::blank(None)
    }

    /// The journal at `path`, or, when it cannot be opened, an empty one that keeps entries in
    /// memory and opens the file again later (`retry_attach`). It sends no garage event until the
    /// file is open: an event the collector sent must survive a restart.
    pub fn open_or_unavailable(path: &Path) -> Journal {
        Journal::open(path).unwrap_or_else(|e| {
            crate::error!("journal unavailable, garage events wait for it: {e}");
            Journal::blank(Some(path.to_path_buf()))
        })
    }

    /// Opens or creates the journal, replays it, and rewrites it compactly when it has grown.
    /// Only complete lines are replayed. A torn tail (the last line has no newline, so the crash
    /// came before its append returned) is cut off and the repair fsynced, so the next append
    /// starts on a line of its own. Complete lines that do not parse are skipped.
    pub fn open(path: &Path) -> io::Result<Journal> {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        let mut journal = Journal::blank(Some(path.to_path_buf()));
        if path.exists() {
            let bytes = fs::read(path)?;
            let complete = bytes.iter().rposition(|b| *b == b'\n').map_or(0, |i| i + 1);
            for line in bytes[..complete].split(|b| *b == b'\n') {
                if line.iter().all(u8::is_ascii_whitespace) {
                    continue;
                }
                if let Ok(record) = serde_json::from_slice::<Record>(line) {
                    journal.apply(record);
                }
                journal.lines += 1;
            }
            if complete < bytes.len() {
                let file = OpenOptions::new().write(true).open(path)?;
                file.set_len(complete as u64)?;
                file.sync_all()?;
            }
        }
        journal.compact_if_needed()?;
        journal.attach_file(path)?;
        Ok(journal)
    }

    fn attach_file(&mut self, path: &Path) -> io::Result<()> {
        let file = OpenOptions::new()
            .create(true)
            .write(true)
            .truncate(false)
            .open(path)?;
        self.len = file.metadata()?.len();
        self.tainted = false;
        self.file = Some(file);
        Ok(())
    }

    /// False while a journal that could not be opened still has no file.
    pub fn sendable(&self) -> bool {
        self.path.is_none() || self.file.is_some()
    }

    /// Opens the file of a journal that was unavailable and writes out what accumulated in
    /// memory. True when the journal is backed by its file (tried at most once per second).
    pub fn retry_attach(&mut self) -> bool {
        let Some(path) = self.path.clone() else {
            return true;
        };
        if self.file.is_some() {
            return true;
        }
        if self
            .last_attach
            .is_some_and(|t| t.elapsed() < REATTACH_EVERY)
        {
            return false;
        }
        self.last_attach = Some(Instant::now());
        let Ok(mut fresh) = Journal::open(&path) else {
            return false;
        };
        for record in self.live_records() {
            if fresh.append(record).is_err() {
                return false;
            }
        }
        fresh.last_attach = self.last_attach;
        *self = fresh;
        crate::info!("journal available again");
        true
    }

    fn apply(&mut self, record: Record) {
        self.seq += 1;
        let seq = self.seq;
        match record {
            Record::Recording(entry) => {
                self.recordings.insert(entry.path.clone(), entry);
            }
            Record::RecordingDone { path } => {
                self.recordings.remove(&path);
            }
            Record::Seen(entry) => {
                self.seen.insert(entry.path.clone(), entry);
            }
            Record::Snapshot(entry) => {
                let visit = self.visits.entry(entry.visit_id.clone()).or_default();
                visit.last_seq = seq;
                visit.snapshots.insert(
                    entry.update_count,
                    SnapState {
                        entry,
                        acked: false,
                        seq,
                        sent_conn: None,
                    },
                );
            }
            Record::SnapshotAck {
                visit_id,
                update_count,
            } => {
                if let Some(s) = self
                    .visits
                    .get_mut(&visit_id)
                    .and_then(|v| v.snapshots.get_mut(&update_count))
                {
                    s.acked = true;
                }
            }
            Record::Commit(entry) => {
                let visit = self.visits.entry(entry.visit_id.clone()).or_default();
                visit.last_seq = seq;
                visit.commit = Some(CommitState {
                    entry,
                    acked: false,
                    seq,
                    sent_conn: None,
                });
            }
            Record::CommitHash {
                visit_id,
                sto_sha256,
                ambiguous,
            } => {
                if let Some(c) = self
                    .visits
                    .get_mut(&visit_id)
                    .and_then(|v| v.commit.as_mut())
                {
                    c.entry.sto_sha256 = sto_sha256;
                    c.entry.ambiguous = ambiguous;
                    c.entry.hash_pending = false;
                }
            }
            Record::CommitAck { visit_id } => {
                if let Some(c) = self
                    .visits
                    .get_mut(&visit_id)
                    .and_then(|v| v.commit.as_mut())
                {
                    c.acked = true;
                }
            }
        }
    }

    /// Writes the record, fsyncs, then applies it. A failed write applies nothing and the caller
    /// keeps the event to try again; the next write first cuts off the fragment the failure may
    /// have left. A journal that was unavailable at start applies in memory only (see
    /// `open_or_unavailable`).
    fn append(&mut self, record: Record) -> io::Result<()> {
        if self.path.is_some() && self.file.is_none() {
            self.retry_attach();
        }
        if let Some(file) = self.file.as_mut() {
            let mut line = serde_json::to_string(&record).map_err(io::Error::other)?;
            line.push('\n');
            if self.tainted {
                file.set_len(self.len)?;
                self.tainted = false;
            }
            #[cfg(test)]
            if self.fail_writes > 0 {
                self.fail_writes -= 1;
                self.tainted = true;
                file.seek(SeekFrom::Start(self.len))?;
                file.write_all(&line.as_bytes()[..line.len() / 2])?;
                return Err(io::Error::other("injected write failure"));
            }
            let written = file
                .seek(SeekFrom::Start(self.len))
                .and_then(|_| file.write_all(line.as_bytes()))
                .and_then(|()| file.sync_data());
            if let Err(e) = written {
                self.tainted = true;
                return Err(e);
            }
            self.len += line.len() as u64;
            self.lines += 1;
        }
        self.apply(record);
        Ok(())
    }

    /// Tests: make the next `n` writes fail after half a line.
    #[cfg(test)]
    pub(crate) fn fail_next_writes(&mut self, n: u32) {
        self.fail_writes = n;
    }

    // ---- recordings ----

    /// Adds or replaces the pending entry for `entry.path`.
    pub fn add_recording(&mut self, entry: RecordingEntry) -> io::Result<()> {
        if self.recordings.get(&entry.path) == Some(&entry) {
            return Ok(());
        }
        self.append(Record::Recording(entry))
    }

    /// Records the hash of a pending file; no-op when the entry changed or is gone.
    pub fn set_recording_hash(
        &mut self,
        path: &str,
        size: u64,
        mtime_ms: u64,
        sha: &str,
    ) -> io::Result<()> {
        let Some(current) = self.recordings.get(path) else {
            return Ok(());
        };
        if current.size != size || current.mtime_ms != mtime_ms {
            return Ok(());
        }
        let mut updated = current.clone();
        updated.sha256 = Some(sha.to_string());
        self.append(Record::Recording(updated))
    }

    /// The server has the file (`verified` or later): drop the entry.
    pub fn recording_done(&mut self, path: &str) -> io::Result<()> {
        if !self.recordings.contains_key(path) {
            return Ok(());
        }
        self.append(Record::RecordingDone {
            path: path.to_string(),
        })
    }

    /// Remembers that the server holds this file.
    pub fn mark_seen(&mut self, entry: SeenEntry) -> io::Result<()> {
        if self.seen.get(&entry.path) == Some(&entry) {
            return Ok(());
        }
        self.append(Record::Seen(entry))
    }

    /// The hash of a file the server is known to hold, if `(path, size, mtime)` still match and
    /// the confirmation is recent.
    pub fn seen_hash(&self, path: &str, size: u64, mtime_ms: u64, now_ms: u64) -> Option<String> {
        let e = self.seen.get(path)?;
        (e.size == size && e.mtime_ms == mtime_ms && now_ms.saturating_sub(e.at_ms) < SEEN_TTL_MS)
            .then(|| e.sha256.clone())
    }

    pub fn pending_recordings(&self) -> Vec<RecordingEntry> {
        self.recordings.values().cloned().collect()
    }

    pub fn recording(&self, path: &str) -> Option<&RecordingEntry> {
        self.recordings.get(path)
    }

    // ---- garage events ----

    pub fn add_snapshot(&mut self, entry: SnapshotEntry) -> io::Result<()> {
        if self
            .visits
            .get(&entry.visit_id)
            .is_some_and(|v| v.snapshots.contains_key(&entry.update_count))
        {
            return Ok(());
        }
        self.append(Record::Snapshot(entry))
    }

    pub fn add_commit(&mut self, entry: CommitEntry) -> io::Result<()> {
        if self
            .visits
            .get(&entry.visit_id)
            .is_some_and(|v| v.commit.is_some())
        {
            return Ok(());
        }
        self.append(Record::Commit(entry))
    }

    /// Resolves a pending `.sto` hash (`None` when the file was not found).
    pub fn set_commit_hash(
        &mut self,
        visit_id: &str,
        sto_sha256: Option<String>,
        ambiguous: bool,
    ) -> io::Result<()> {
        if !self
            .visits
            .get(visit_id)
            .and_then(|v| v.commit.as_ref())
            .is_some_and(|c| c.entry.hash_pending)
        {
            return Ok(());
        }
        self.append(Record::CommitHash {
            visit_id: visit_id.to_string(),
            sto_sha256,
            ambiguous,
        })
    }

    /// Commits whose `.sto` hash is still to be computed, oldest first.
    pub fn commits_needing_hash(&self) -> Vec<CommitEntry> {
        // Ordered by sequence number through a map: sorting a Vec of these large entries costs
        // far more code than the whole function (the executable is held to 3 MB).
        let out: BTreeMap<u64, CommitEntry> = self
            .visits
            .values()
            .filter_map(|v| v.commit.as_ref())
            .filter(|c| !c.acked && c.entry.hash_pending)
            .map(|c| (c.seq, c.entry.clone()))
            .collect();
        out.into_values().collect()
    }

    pub fn ack_snapshot(&mut self, visit_id: &str, update_count: i64) -> io::Result<()> {
        let live = self
            .visits
            .get(visit_id)
            .and_then(|v| v.snapshots.get(&update_count))
            .is_some_and(|s| !s.acked);
        if !live {
            return Ok(());
        }
        self.append(Record::SnapshotAck {
            visit_id: visit_id.to_string(),
            update_count,
        })
    }

    pub fn ack_commit(&mut self, visit_id: &str) -> io::Result<()> {
        let live = self
            .visits
            .get(visit_id)
            .and_then(|v| v.commit.as_ref())
            .is_some_and(|c| !c.acked);
        if !live {
            return Ok(());
        }
        self.append(Record::CommitAck {
            visit_id: visit_id.to_string(),
        })?;
        self.compact_if_needed()
    }

    /// Garage messages not yet sent on connection `conn`, oldest first, each marked as sent on it.
    /// A commit is held back until its `.sto` hash is resolved and its snapshot is known.
    pub fn take_unsent(&mut self, conn: u64) -> Vec<GarageOut> {
        if !self.sendable() {
            return Vec::new();
        }
        let mut out: BTreeMap<u64, GarageOut> = BTreeMap::new();
        for visit in self.visits.values_mut() {
            for snap in visit.snapshots.values_mut() {
                if !snap.acked && snap.sent_conn != Some(conn) {
                    snap.sent_conn = Some(conn);
                    out.insert(snap.seq, GarageOut::Snapshot(snap.entry.clone()));
                }
            }
            let values = visit
                .commit
                .as_ref()
                .and_then(|c| c.entry.update_count)
                .and_then(|n| visit.snapshots.get(&n))
                .map(|s| s.entry.values.clone());
            if let Some(commit) = visit.commit.as_mut()
                && !commit.acked
                && commit.sent_conn != Some(conn)
                && !commit.entry.hash_pending
            {
                let values = values.unwrap_or_default();
                commit.sent_conn = Some(conn);
                out.insert(
                    commit.seq,
                    GarageOut::Commit {
                        entry: commit.entry.clone(),
                        values,
                    },
                );
            }
        }
        out.into_values().collect()
    }

    /// The values a commit carries: those of its referenced snapshot.
    pub fn commit_values(&self, visit_id: &str) -> Option<BTreeMap<String, String>> {
        let visit = self.visits.get(visit_id)?;
        let n = visit.commit.as_ref()?.entry.update_count?;
        visit.snapshots.get(&n).map(|s| s.entry.values.clone())
    }

    /// Number of unacknowledged garage entries (for the status line and tests).
    pub fn garage_pending(&self) -> usize {
        self.visits
            .values()
            .map(|v| {
                v.snapshots.values().filter(|s| !s.acked).count()
                    + usize::from(v.commit.as_ref().is_some_and(|c| !c.acked))
            })
            .sum()
    }

    // ---- compaction ----

    fn live_records(&self) -> Vec<Record> {
        // Keyed by (sequence number, insertion order): a map keeps them in order without a sort.
        let mut records: BTreeMap<(u64, usize), Record> = BTreeMap::new();
        let mut add = |seq: u64, record: Record| {
            let n = records.len();
            records.insert((seq, n), record);
        };
        for entry in self.recordings.values() {
            add(0, Record::Recording(entry.clone()));
        }
        let mut seen: Vec<&SeenEntry> = self.seen.values().collect();
        seen.sort_by_key(|e| std::cmp::Reverse(e.at_ms));
        for entry in seen.into_iter().take(KEEP_SEEN) {
            add(0, Record::Seen(entry.clone()));
        }
        let mut abandoned: Vec<&String> = self
            .visits
            .iter()
            .filter(|(_, v)| v.commit.is_none() && v.snapshots.values().all(|s| s.acked))
            .map(|(id, _)| id)
            .collect();
        abandoned.sort_by_key(|id| self.visits[*id].last_seq);
        let drop_abandoned: Vec<String> = abandoned
            .iter()
            .take(abandoned.len().saturating_sub(KEEP_ABANDONED_VISITS))
            .map(|s| (*s).clone())
            .collect();
        for (id, visit) in &self.visits {
            if visit.commit.as_ref().is_some_and(|c| c.acked) || drop_abandoned.contains(id) {
                continue;
            }
            let newest = visit.snapshots.keys().next_back().copied();
            for (n, snap) in &visit.snapshots {
                if !snap.acked || Some(*n) == newest {
                    add(snap.seq, Record::Snapshot(snap.entry.clone()));
                    if snap.acked {
                        add(
                            snap.seq,
                            Record::SnapshotAck {
                                visit_id: id.clone(),
                                update_count: *n,
                            },
                        );
                    }
                }
            }
            if let Some(c) = &visit.commit {
                add(c.seq, Record::Commit(c.entry.clone()));
            }
        }
        records.into_values().collect()
    }

    fn compact_if_needed(&mut self) -> io::Result<()> {
        let Some(path) = self.path.clone() else {
            return Ok(());
        };
        let live = self.live_records();
        if self.lines <= 2 * live.len() + 16 {
            return Ok(());
        }
        let tmp = path.with_extension("jsonl.tmp");
        {
            let mut f = File::create(&tmp)?;
            for record in &live {
                let mut line = serde_json::to_string(record).map_err(io::Error::other)?;
                line.push('\n');
                f.write_all(line.as_bytes())?;
            }
            f.sync_all()?;
        }
        // The handle points at the old inode on some platforms: reopen after the rename.
        self.file = None;
        fs::rename(&tmp, &path)?;
        self.attach_file(&path)?;
        self.lines = live.len();
        // Replay the live records so sequence numbers and the in-memory state match the file,
        // keeping which entries were already sent on the current connection.
        let old_visits = std::mem::take(&mut self.visits);
        self.recordings.clear();
        self.seen.clear();
        self.seq = 0;
        for record in live {
            self.apply(record);
        }
        for (id, old) in old_visits {
            let Some(visit) = self.visits.get_mut(&id) else {
                continue;
            };
            for (n, old_snap) in old.snapshots {
                if let Some(s) = visit.snapshots.get_mut(&n) {
                    s.sent_conn = old_snap.sent_conn;
                }
            }
            if let (Some(old_commit), Some(c)) = (old.commit, visit.commit.as_mut()) {
                c.sent_conn = old_commit.sent_conn;
            }
        }
        Ok(())
    }

    /// A summary line for diagnostics.
    pub fn summary(&self) -> Value {
        json!({
            "recordings": self.recordings.len(),
            "seen": self.seen.len(),
            "garage_pending": self.garage_pending(),
            "visits": self.visits.len(),
            "lines": self.lines,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::util::uuid_v4;

    fn temp() -> PathBuf {
        std::env::temp_dir().join(format!("coach-journal-{}.jsonl", uuid_v4()))
    }

    fn snap(visit: &str, n: i64) -> SnapshotEntry {
        SnapshotEntry {
            visit_id: visit.into(),
            update_count: n,
            observed_at: "2026-10-02T00:00:00.000Z".into(),
            car_path: Some("stockcars2 supra2019".into()),
            driver: None,
            session: None,
            values: BTreeMap::from([("Chassis.Front.ArbSetting".to_string(), format!("{n}"))]),
        }
    }

    fn commit(visit: &str, n: Option<i64>, pending: bool) -> CommitEntry {
        CommitEntry {
            visit_id: visit.into(),
            update_count: n,
            entered_at: "2026-10-02T00:00:00.000Z".into(),
            exited_at: "2026-10-02T00:01:00.000Z".into(),
            car_path: None,
            driver: None,
            session: None,
            setup_name: Some("a.sto".into()),
            sto_sha256: None,
            ambiguous: false,
            is_modified: Some(false),
            load_type: Some("user".into()),
            hash_pending: pending,
        }
    }

    #[test]
    fn recordings_survive_a_restart_until_done() {
        let path = temp();
        let entry = RecordingEntry {
            path: "C:/x/a.ibt".into(),
            size: 10,
            mtime_ms: 5,
            kind: "ibt".into(),
            relative_path: None,
            sha256: None,
        };
        {
            let mut j = Journal::open(&path).unwrap();
            j.add_recording(entry.clone()).unwrap();
            j.set_recording_hash("C:/x/a.ibt", 10, 5, "ab").unwrap();
        }
        let mut j = Journal::open(&path).unwrap();
        let pending = j.pending_recordings();
        assert_eq!(pending.len(), 1);
        assert_eq!(pending[0].sha256.as_deref(), Some("ab"));
        j.recording_done("C:/x/a.ibt").unwrap();
        drop(j);
        assert!(
            Journal::open(&path)
                .unwrap()
                .pending_recordings()
                .is_empty()
        );
        let _ = fs::remove_file(path);
    }

    #[test]
    fn a_stale_hash_is_ignored_when_the_file_changed() {
        let mut j = Journal::in_memory();
        let mut entry = RecordingEntry {
            path: "p".into(),
            size: 1,
            mtime_ms: 1,
            kind: "ibt".into(),
            relative_path: None,
            sha256: None,
        };
        j.add_recording(entry.clone()).unwrap();
        entry.size = 2;
        j.add_recording(entry).unwrap();
        j.set_recording_hash("p", 1, 1, "old").unwrap();
        assert_eq!(j.recording("p").unwrap().sha256, None);
    }

    #[test]
    fn garage_events_are_sent_once_per_connection_and_resent_after_reconnect() {
        let mut j = Journal::in_memory();
        j.add_snapshot(snap("v1", 3)).unwrap();
        j.add_snapshot(snap("v1", 4)).unwrap();
        assert_eq!(j.take_unsent(1).len(), 2);
        assert!(j.take_unsent(1).is_empty());
        assert_eq!(
            j.take_unsent(2).len(),
            2,
            "unacknowledged entries are resent"
        );
        j.ack_snapshot("v1", 3).unwrap();
        assert_eq!(j.take_unsent(3).len(), 1);
    }

    #[test]
    fn a_commit_waits_for_its_hash_and_carries_its_snapshots_values() {
        let mut j = Journal::in_memory();
        j.add_snapshot(snap("v1", 4)).unwrap();
        j.add_commit(commit("v1", Some(4), true)).unwrap();
        assert_eq!(j.commits_needing_hash().len(), 1);
        let first = j.take_unsent(1);
        assert_eq!(first.len(), 1, "snapshot only");
        j.set_commit_hash("v1", Some("ff".into()), false).unwrap();
        let second = j.take_unsent(1);
        match &second[..] {
            [GarageOut::Commit { entry, values }] => {
                assert_eq!(entry.sto_sha256.as_deref(), Some("ff"));
                assert_eq!(values["Chassis.Front.ArbSetting"], "4");
            }
            other => panic!("unexpected {other:?}"),
        }
    }

    #[test]
    fn the_newest_snapshot_is_kept_after_its_ack_until_the_commit_is_acked() {
        let path = temp();
        let mut j = Journal::open(&path).unwrap();
        j.add_snapshot(snap("v1", 4)).unwrap();
        j.ack_snapshot("v1", 4).unwrap();
        j.add_commit(commit("v1", Some(4), false)).unwrap();
        let out = j.take_unsent(1);
        assert!(matches!(&out[..], [GarageOut::Commit { values, .. }] if values.len() == 1));
        drop(j);
        let mut j = Journal::open(&path).unwrap();
        assert_eq!(j.garage_pending(), 1);
        assert_eq!(
            j.take_unsent(2).len(),
            1,
            "commit resent after restart with values"
        );
        j.ack_commit("v1").unwrap();
        assert_eq!(j.garage_pending(), 0);
        assert!(j.take_unsent(3).is_empty());
        let _ = fs::remove_file(path);
    }

    #[test]
    fn a_torn_last_line_is_skipped_and_compaction_keeps_live_state() {
        let path = temp();
        {
            let mut j = Journal::open(&path).unwrap();
            for n in 0..40 {
                j.add_snapshot(snap("v1", n)).unwrap();
                j.ack_snapshot("v1", n).unwrap();
            }
            j.add_snapshot(snap("v2", 1)).unwrap();
        }
        let mut f = OpenOptions::new().append(true).open(&path).unwrap();
        f.write_all(b"{\"snapshot\":{\"visit_id\":\"v3\"").unwrap();
        drop(f);
        let mut j = Journal::open(&path).unwrap();
        assert_eq!(j.garage_pending(), 1);
        let lines = fs::read_to_string(&path).unwrap().lines().count();
        assert!(lines < 20, "compacted, got {lines} lines");
        assert_eq!(j.take_unsent(1).len(), 1);
        let _ = fs::remove_file(path);
    }

    #[test]
    fn a_torn_tail_is_cut_off_so_the_next_append_survives_a_restart() {
        let path = temp();
        {
            let mut j = Journal::open(&path).unwrap();
            j.add_snapshot(snap("v1", 1)).unwrap();
        }
        let append = |bytes: &[u8]| {
            let mut f = OpenOptions::new().append(true).open(&path).unwrap();
            f.write_all(bytes).unwrap();
            f.sync_all().unwrap();
        };
        // A fragment, and a record that is whole but lost its newline: neither was acknowledged
        // to the caller, so neither is replayed.
        append(b"{\"snapshot\":{\"visit_id\":\"v2\",\"update_cou");
        let mut j = Journal::open(&path).unwrap();
        assert!(
            fs::read(&path).unwrap().ends_with(b"\n"),
            "the repair is on disk"
        );
        j.add_snapshot(snap("v3", 7)).unwrap();
        drop(j);
        let whole = serde_json::to_string(&Record::Snapshot(snap("v4", 9))).unwrap();
        append(whole.as_bytes());
        let mut j = Journal::open(&path).unwrap();
        j.add_snapshot(snap("v5", 11)).unwrap();
        drop(j);
        let mut j = Journal::open(&path).unwrap();
        let ids: Vec<String> = j
            .take_unsent(1)
            .into_iter()
            .filter_map(|m| match m {
                GarageOut::Snapshot(s) => Some(s.visit_id),
                GarageOut::Commit { .. } => None,
            })
            .collect();
        assert_eq!(ids, ["v1", "v3", "v5"], "no appended event was lost");
        let _ = fs::remove_file(path);
    }

    #[test]
    fn a_failed_append_applies_nothing_and_its_retry_leaves_no_fragment() {
        let path = temp();
        let mut j = Journal::open(&path).unwrap();
        j.add_snapshot(snap("v1", 1)).unwrap();
        j.fail_next_writes(2);
        assert!(j.add_snapshot(snap("v1", 2)).is_err());
        assert!(j.add_snapshot(snap("v1", 2)).is_err());
        assert_eq!(j.garage_pending(), 1, "a failed append is not applied");
        j.add_snapshot(snap("v1", 2)).unwrap();
        j.ack_snapshot("v1", 1).unwrap();
        drop(j);
        for line in fs::read_to_string(&path).unwrap().lines() {
            serde_json::from_str::<Record>(line).expect("every line is a whole record");
        }
        let j = Journal::open(&path).unwrap();
        assert_eq!(j.garage_pending(), 1);
        assert_eq!(j.visits["v1"].snapshots.len(), 2);
        let _ = fs::remove_file(path);
    }

    #[test]
    fn an_unavailable_journal_holds_events_back_until_the_file_opens() {
        // A directory where the journal file belongs: it cannot be opened.
        let path = temp();
        fs::create_dir_all(&path).unwrap();
        let mut j = Journal::open_or_unavailable(&path);
        assert!(!j.sendable());
        j.add_snapshot(snap("v1", 1)).unwrap();
        assert_eq!(j.garage_pending(), 1, "kept in memory");
        assert!(
            j.take_unsent(1).is_empty(),
            "nothing is sent without a file"
        );
        assert!(!j.retry_attach());
        fs::remove_dir_all(&path).unwrap();
        assert!(j.retry_attach());
        assert_eq!(j.take_unsent(1).len(), 1);
        drop(j);
        assert_eq!(Journal::open(&path).unwrap().garage_pending(), 1);
        let _ = fs::remove_file(path);
    }
}

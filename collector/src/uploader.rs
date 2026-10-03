//! The uploader (`collector-protocol.md` section 4): finalization gate, hash, create, 4 MiB chunks,
//! resume, acknowledgement. One upload at a time, newest recording first, setup files after
//! recordings. Everything that touches a file (scan, finalization check, hash, chunk read) runs
//! only while the on-track gate allows it and re-checks before every chunk, every 32 KiB piece,
//! every directory entry of a scan and every pending journal entry, and while a full socket is
//! waited on, so an in-flight chunk or scan is abandoned within the 100 ms allowance when the
//! evidence lapses. Work interrupted that way is kept and resumed.

use std::fs::File;
use std::io::{self, Read, Seek, SeekFrom};
use std::path::Path;
use std::sync::Arc;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use coach_ibt::{Finalization, HashOutcome, NotReadyReason, finalization_check, sha256_file};
use serde_json::json;

use crate::config::{Paths, server_authority};
use crate::gate::TrackGate;
use crate::http::{Body, HttpClient, HttpError};
use crate::hub::Hub;
use crate::journal::{RecordingEntry, SeenEntry};
use crate::scan::{Candidate, Kind, scan_recordings, scan_setups};
use crate::util::{Clock, iso_utc};

pub const CHUNK_BYTES: usize = 4 * 1024 * 1024;

#[derive(Debug, Clone)]
pub struct UploaderSettings {
    pub collector_id: String,
    pub chunk_bytes: usize,
    /// How often the folders are scanned again without a directory event.
    pub scan_interval_ms: u64,
    /// Server-unreachable backoff steps (the last repeats).
    pub server_backoff_ms: Vec<u64>,
    /// Delay before a file that is not final yet (recording in progress) is checked again.
    pub not_ready_retry_ms: u64,
}

impl UploaderSettings {
    pub fn new(collector_id: &str) -> UploaderSettings {
        UploaderSettings {
            collector_id: collector_id.to_string(),
            chunk_bytes: CHUNK_BYTES,
            scan_interval_ms: 60_000,
            server_backoff_ms: vec![1_000, 2_000, 4_000, 8_000, 16_000, 30_000],
            not_ready_retry_ms: 5_000,
        }
    }
}

/// What one call to `step` did.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Step {
    /// A file was fully handled or a state changed; call again at once.
    Worked,
    /// Nothing to do now; look again after the given time (a wake-up call ends the sleep early).
    Idle(Duration),
}

#[derive(Debug, Clone)]
struct Item {
    cand: Candidate,
    next_try_ms: u64,
    failed: bool,
}

#[derive(Debug)]
enum Outcome {
    /// The server holds the file.
    Done,
    /// The gate closed (or shutdown): try again when it opens.
    GateClosed,
    /// The file is not final or changed: check again after the delay.
    Later(u64),
    /// The server could not be reached or refused for now.
    ServerTrouble { retry_after_ms: Option<u64> },
    /// Never retry this file as it is (it changes the item's size/mtime to be reconsidered).
    Failed(String),
    /// The server's hash differs: forget the remembered hash and start over.
    Rehash,
}

pub struct Uploader {
    hub: Arc<Hub>,
    /// Retry timing; the hub's clock unless `with_clock` says otherwise (a virtual-time replay
    /// freezes the hub's clock, but uploads still need real time).
    clock: Arc<dyn Clock>,
    http: HttpClient,
    paths: Paths,
    settings: UploaderSettings,
    items: Vec<Item>,
    scanned_at_ms: Option<u64>,
    rescan_requested: bool,
    /// The journal's pending entries are matched with files on the first gate-open step (the
    /// stat calls are file work and wait for off-track evidence like everything else).
    journal_loaded: bool,
    server_trouble: u32,
    server_retry_at_ms: u64,
    last_customer_id: Option<i64>,
}

impl Uploader {
    pub fn new(hub: Arc<Hub>, server: &str, paths: Paths, settings: UploaderSettings) -> Uploader {
        let uploader = Uploader {
            clock: hub.clock.clone(),
            hub,
            http: HttpClient::new(&server_authority(server)),
            paths,
            settings,
            items: Vec::new(),
            scanned_at_ms: None,
            rescan_requested: true,
            journal_loaded: false,
            server_trouble: 0,
            server_retry_at_ms: 0,
            last_customer_id: None,
        };
        // The queue figures come from the journal's memory: no file is touched yet.
        let pending = uploader.hub.journal().pending_recordings();
        let bytes: u64 = pending.iter().map(|e| e.size).sum();
        uploader
            .hub
            .uploads
            .uploads_pending
            .store(pending.len() as i64, std::sync::atomic::Ordering::Relaxed);
        uploader
            .hub
            .uploads
            .bytes_pending
            .store(bytes as i64, std::sync::atomic::Ordering::Relaxed);
        uploader
    }

    pub fn with_clock(mut self, clock: Arc<dyn Clock>) -> Uploader {
        self.clock = clock;
        self
    }

    pub fn request_rescan(&mut self) {
        self.rescan_requested = true;
    }

    pub fn pending(&self) -> usize {
        self.items.iter().filter(|i| !i.failed).count()
    }

    /// Thread body.
    pub fn run(&mut self) {
        while !self.hub.is_shutdown() {
            match self.step() {
                Step::Worked => {}
                Step::Idle(d) => {
                    if self.hub.uploader_sleep(d) {
                        self.rescan_requested = true;
                    }
                }
            }
        }
    }

    fn now(&self) -> u64 {
        self.clock.mono_ms()
    }

    /// True while file work may go on: the gate allows it and the collector is not shutting down.
    fn proceed(&self) -> impl Fn() -> bool + use<> {
        let gate = self.hub.gate.clone();
        let hub = self.hub.clone();
        move || gate.allows_file_work() && !hub.is_shutdown()
    }

    /// Items pending in the journal from an earlier run, whose files still exist. False when the
    /// gate closed part way: the entries not reached stay in the journal for the next pass.
    fn load_journal(&mut self, proceed: &dyn Fn() -> bool) -> bool {
        let pending = self.hub.journal().pending_recordings();
        for entry in pending {
            if !proceed() {
                self.publish_stats();
                return false;
            }
            let path = Path::new(&entry.path);
            let Some(kind) = Kind::parse(&entry.kind) else {
                continue;
            };
            if let Some(cand) = stat(path, kind, entry.relative_path.clone()) {
                self.upsert(cand);
            } else {
                crate::warn!("pending upload {} no longer exists", entry.path);
                let _ = self.hub.journal().recording_done(&entry.path);
            }
        }
        self.publish_stats();
        true
    }

    fn upsert(&mut self, cand: Candidate) {
        match self.items.iter_mut().find(|i| i.cand.path == cand.path) {
            Some(item) => {
                if item.cand.size != cand.size || item.cand.mtime_ms != cand.mtime_ms {
                    item.cand = cand;
                    item.next_try_ms = 0;
                    item.failed = false;
                }
            }
            None => self.items.push(Item {
                cand,
                next_try_ms: 0,
                failed: false,
            }),
        }
    }

    fn publish_stats(&self) {
        let live = self.items.iter().filter(|i| !i.failed);
        let count = live.clone().count() as i64;
        let bytes: u64 = live.map(|i| i.cand.size).sum();
        self.hub
            .uploads
            .uploads_pending
            .store(count, std::sync::atomic::Ordering::Relaxed);
        self.hub
            .uploads
            .bytes_pending
            .store(bytes as i64, std::sync::atomic::Ordering::Relaxed);
    }

    /// Lists both folders and reconciles the queue with them. False when the gate closed part way:
    /// the scan stays due, nothing is dropped from the queue or the journal on a partial listing.
    fn scan(&mut self, proceed: &dyn Fn() -> bool) -> bool {
        let now_unix = self.hub.clock.unix_ms();
        let found = scan_recordings(&self.paths.telemetry_dir(), proceed).and_then(|mut found| {
            found.extend(scan_setups(&self.paths.setups_dir(), proceed)?);
            Ok(found)
        });
        let Ok(found) = found else {
            return false;
        };
        for cand in found {
            // Skip what the server is already known to hold (same path, size, mtime).
            let mut journal = self.hub.journal();
            if journal
                .seen_hash(&cand.key(), cand.size, cand.mtime_ms, now_unix)
                .is_some()
            {
                // A pending entry for it (queued by a garage commit) is already satisfied.
                let _ = journal.recording_done(&cand.key());
            } else {
                drop(journal);
                self.upsert(cand);
            }
        }
        // A file that disappeared from disk can no longer be uploaded.
        let mut gone = Vec::new();
        for item in &self.items {
            if !proceed() {
                return false;
            }
            if !item.cand.path.is_file() {
                gone.push(item.cand.key());
            }
        }
        self.items.retain(|i| !gone.contains(&i.cand.key()));
        for key in gone {
            crate::warn!("{key} disappeared before it was uploaded");
            let _ = self.hub.journal().recording_done(&key);
        }
        self.scanned_at_ms = Some(self.now());
        self.rescan_requested = false;
        self.hub
            .uploads
            .scans_done
            .fetch_add(1, std::sync::atomic::Ordering::Relaxed);
        self.publish_stats();
        true
    }

    /// One unit of work: a scan if due, else one file driven as far as it goes.
    pub fn step(&mut self) -> Step {
        if let Some(driver) = self.hub.status().driver.as_ref()
            && let Some(id) = driver.get("customer_id").and_then(|v| v.as_i64())
        {
            self.last_customer_id = Some(id);
        }
        let now = self.now();
        let has_work = self.scanned_at_ms.is_none() || self.rescan_requested || self.pending() > 0;
        if !self.hub.gate.allows_file_work() {
            self.hub
                .uploads
                .waiting_for_off_track
                .store(has_work, std::sync::atomic::Ordering::Relaxed);
            return Step::Idle(Duration::from_millis(200));
        }
        self.hub
            .uploads
            .waiting_for_off_track
            .store(false, std::sync::atomic::Ordering::Relaxed);

        let proceed = self.proceed();
        if !self.journal_loaded {
            // Interrupted: the gate is closed now, and the next step waits for it.
            self.journal_loaded = self.load_journal(&proceed);
            if !self.journal_loaded {
                return Step::Worked;
            }
        }
        let scan_due = self.scanned_at_ms.is_none_or(|t| {
            self.rescan_requested || now.saturating_sub(t) >= self.settings.scan_interval_ms
        });
        if scan_due {
            self.scan(&proceed);
            return Step::Worked;
        }
        if now < self.server_retry_at_ms {
            return Step::Idle(Duration::from_millis(
                (self.server_retry_at_ms - now).min(1_000),
            ));
        }

        let next = self
            .items
            .iter()
            .enumerate()
            .filter(|(_, i)| !i.failed && i.next_try_ms <= now)
            .min_by_key(|(_, i)| (i.cand.kind != Kind::Ibt, std::cmp::Reverse(i.cand.mtime_ms)))
            .map(|(idx, _)| idx);
        let Some(idx) = next else {
            let wake = self
                .items
                .iter()
                .filter(|i| !i.failed)
                .map(|i| i.next_try_ms.saturating_sub(now))
                .min()
                .unwrap_or(1_000)
                .clamp(50, 1_000);
            return Step::Idle(Duration::from_millis(wake));
        };

        let cand = self.items[idx].cand.clone();
        let outcome = self.process(&cand);
        let now = self.now();
        match outcome {
            Outcome::Done => {
                self.server_trouble = 0;
                self.items.remove(idx);
            }
            Outcome::GateClosed => {}
            Outcome::Later(ms) => self.items[idx].next_try_ms = now + ms,
            Outcome::Rehash => {
                // Forget the remembered hash so the next attempt reads the file again.
                let key = cand.key();
                let mut journal = self.hub.journal();
                if let Some(mut entry) = journal.recording(&key).cloned() {
                    entry.sha256 = None;
                    let _ = journal.add_recording(entry);
                }
                self.items[idx].next_try_ms = now + 2_000;
            }
            Outcome::ServerTrouble { retry_after_ms } => {
                let steps = &self.settings.server_backoff_ms;
                let step = steps
                    .get((self.server_trouble as usize).min(steps.len().saturating_sub(1)))
                    .copied()
                    .unwrap_or(30_000);
                self.server_trouble = self.server_trouble.saturating_add(1);
                self.server_retry_at_ms = now + retry_after_ms.unwrap_or(step);
            }
            Outcome::Failed(why) => {
                crate::error!("giving up on {}: {why}", cand.path.display());
                self.items[idx].failed = true;
            }
        }
        self.publish_stats();
        Step::Worked
    }

    fn process(&mut self, cand: &Candidate) -> Outcome {
        let now_unix = self.hub.clock.unix_ms();
        // 1. Known to the server already (same path, size, mtime).
        if self
            .hub
            .journal()
            .seen_hash(&cand.key(), cand.size, cand.mtime_ms, now_unix)
            .is_some()
        {
            let _ = self.hub.journal().recording_done(&cand.key());
            return Outcome::Done;
        }
        // 2. Finalization gate for recordings.
        let mut cand = cand.clone();
        if cand.kind == Kind::Ibt {
            match finalization_check(&cand.path, SystemTime::now()) {
                Finalization::Ready { size, mtime, .. } => {
                    cand.size = size;
                    cand.mtime_ms = mtime
                        .duration_since(UNIX_EPOCH)
                        .map_or(cand.mtime_ms, |d| d.as_millis() as u64);
                }
                Finalization::NotReady(reason) => {
                    return Outcome::Later(not_ready_delay(&reason, &self.settings));
                }
            }
        }
        // 3. Pending in the journal.
        let entry = RecordingEntry {
            path: cand.key(),
            size: cand.size,
            mtime_ms: cand.mtime_ms,
            kind: cand.kind.as_str().to_string(),
            relative_path: cand.relative_path.clone(),
            sha256: None,
        };
        let remembered = {
            let mut journal = self.hub.journal();
            let known = journal
                .recording(&entry.path)
                .filter(|e| e.size == entry.size && e.mtime_ms == entry.mtime_ms)
                .and_then(|e| e.sha256.clone());
            if known.is_none()
                && let Err(e) = journal.add_recording(entry.clone())
            {
                crate::error!("journal append failed: {e}");
            }
            known
        };
        // 4. Hash.
        let sha = match remembered {
            Some(sha) => sha,
            None => match self.hash(&cand) {
                Ok(sha) => sha,
                Err(outcome) => return outcome,
            },
        };
        // 5. Create, then chunks.
        self.send(&cand, &sha)
    }

    fn hash(&mut self, cand: &Candidate) -> Result<String, Outcome> {
        let gate = self.hub.gate.clone();
        let hub = self.hub.clone();
        let mut proceed = move |_: u64| gate.allows_file_work() && !hub.is_shutdown();
        match sha256_file(&cand.path, &mut proceed) {
            Ok(HashOutcome::Complete { sha256, bytes }) => {
                // The file must not have changed while it was read.
                match stat(&cand.path, cand.kind, cand.relative_path.clone()) {
                    Some(now)
                        if now.size == cand.size
                            && now.mtime_ms == cand.mtime_ms
                            && bytes == cand.size =>
                    {
                        let _ = self.hub.journal().set_recording_hash(
                            &cand.key(),
                            cand.size,
                            cand.mtime_ms,
                            &sha256,
                        );
                        Ok(sha256)
                    }
                    _ => Err(Outcome::Later(1_000)),
                }
            }
            Ok(HashOutcome::Aborted { .. }) => Err(Outcome::GateClosed),
            Err(e) => {
                crate::warn!("hashing {} failed: {e}", cand.path.display());
                Err(Outcome::Later(self.settings.not_ready_retry_ms))
            }
        }
    }

    fn send(&mut self, cand: &Candidate, sha: &str) -> Outcome {
        let create = json!({
            "sha256": sha,
            "size": cand.size,
            "kind": cand.kind.as_str(),
            "file_name": cand.file_name(),
            "relative_path": cand.relative_path,
            "source_mtime": iso_utc(cand.mtime_ms),
            "collector_id": self.settings.collector_id,
            "customer_id": self.last_customer_id,
        })
        .to_string();
        let mut offset = match self.create(&create) {
            Ok(Created::Open(offset)) => offset,
            Ok(Created::Held) => return self.finish(cand, sha),
            Err(outcome) => return outcome,
        };

        let live = self.proceed();
        let mut file = match File::open(&cand.path) {
            Ok(f) => f,
            Err(e) => {
                crate::warn!("open {} failed: {e}", cand.path.display());
                return Outcome::Later(self.settings.not_ready_retry_ms);
            }
        };
        loop {
            if !self.hub.gate.allows_file_work() || self.hub.is_shutdown() {
                return Outcome::GateClosed;
            }
            if offset >= cand.size {
                // The server holds every byte but did not verify: ask again.
                return Outcome::Later(500);
            }
            let len = (self.settings.chunk_bytes as u64).min(cand.size - offset);
            if let Err(e) = file.seek(SeekFrom::Start(offset)) {
                crate::warn!("seek in {} failed: {e}", cand.path.display());
                return Outcome::Later(self.settings.not_ready_retry_ms);
            }
            let mut reader = GateReader {
                file: &mut file,
                remaining: len,
                gate: self.hub.gate.clone(),
                hub: self.hub.clone(),
            };
            let range = format!("bytes {}-{}/{}", offset, offset + len - 1, cand.size);
            let result = self.http.request(
                "PUT",
                &format!("/api/v1/uploads/{sha}"),
                &[
                    ("Content-Type", "application/octet-stream".to_string()),
                    ("Content-Range", range),
                ],
                Body::Stream {
                    len,
                    reader: &mut reader,
                    keep_going: &live,
                },
            );
            match result {
                Err(HttpError::Body(_)) => return Outcome::GateClosed,
                Err(e) => {
                    crate::debug!("chunk upload failed: {e}");
                    return Outcome::ServerTrouble {
                        retry_after_ms: None,
                    };
                }
                Ok(resp) => match resp.status {
                    200 => {
                        let body = resp.json().unwrap_or_default();
                        match body.get("state").and_then(|s| s.as_str()) {
                            Some("open") => {
                                offset = body
                                    .get("offset")
                                    .and_then(|o| o.as_u64())
                                    .unwrap_or(offset + len);
                            }
                            Some(_) => return self.finish(cand, sha),
                            None => {
                                return Outcome::ServerTrouble {
                                    retry_after_ms: None,
                                };
                            }
                        }
                    }
                    409 => {
                        let body = resp.json().unwrap_or_default();
                        match body.get("offset").and_then(|o| o.as_u64()) {
                            Some(o) => offset = o,
                            None => {
                                return Outcome::ServerTrouble {
                                    retry_after_ms: None,
                                };
                            }
                        }
                    }
                    422 => {
                        crate::warn!("server reports a hash mismatch for {}", cand.path.display());
                        return Outcome::Rehash;
                    }
                    503 => {
                        return Outcome::ServerTrouble {
                            retry_after_ms: retry_after(&resp),
                        };
                    }
                    other => {
                        crate::warn!("chunk answered {other}");
                        return Outcome::ServerTrouble {
                            retry_after_ms: None,
                        };
                    }
                },
            }
        }
    }

    fn create(&mut self, body: &str) -> Result<Created, Outcome> {
        let resp = self.http.request(
            "POST",
            "/api/v1/uploads",
            &[("Content-Type", "application/json".to_string())],
            Body::Bytes(body.as_bytes()),
        );
        let resp = match resp {
            Ok(r) => r,
            Err(e) => {
                crate::debug!("upload create failed: {e}");
                return Err(Outcome::ServerTrouble {
                    retry_after_ms: None,
                });
            }
        };
        match resp.status {
            200 | 201 => {
                let json = resp.json().unwrap_or_default();
                match json.get("state").and_then(|s| s.as_str()) {
                    Some("open") => Ok(Created::Open(
                        json.get("offset").and_then(|o| o.as_u64()).unwrap_or(0),
                    )),
                    Some(_) => Ok(Created::Held),
                    None => Err(Outcome::ServerTrouble {
                        retry_after_ms: None,
                    }),
                }
            }
            503 => Err(Outcome::ServerTrouble {
                retry_after_ms: retry_after(&resp),
            }),
            413 => Err(Outcome::Failed(
                "the server refuses files of this size".into(),
            )),
            400 | 422 => Err(Outcome::Failed(format!(
                "the server rejected the upload ({})",
                resp.status
            ))),
            other => {
                crate::warn!("upload create answered {other}");
                Err(Outcome::ServerTrouble {
                    retry_after_ms: None,
                })
            }
        }
    }

    /// The server has the bytes (verified or later): drop the journal entry, remember the file.
    fn finish(&mut self, cand: &Candidate, sha: &str) -> Outcome {
        let mut journal = self.hub.journal();
        let _ = journal.recording_done(&cand.key());
        let _ = journal.mark_seen(SeenEntry {
            path: cand.key(),
            size: cand.size,
            mtime_ms: cand.mtime_ms,
            sha256: sha.to_string(),
            at_ms: self.hub.clock.unix_ms(),
        });
        Outcome::Done
    }
}

enum Created {
    Open(u64),
    /// The server already has the file (`verified`, `parsed`, `archived`, ...).
    Held,
}

fn retry_after(resp: &crate::http::Response) -> Option<u64> {
    resp.header("retry-after")
        .and_then(|v| v.trim().parse::<u64>().ok())
        .map(|s| s.clamp(1, 600) * 1000)
}

fn not_ready_delay(reason: &NotReadyReason, settings: &UploaderSettings) -> u64 {
    match reason {
        NotReadyReason::TooRecent { .. } => 2_100,
        NotReadyReason::InUse => settings.not_ready_retry_ms,
        NotReadyReason::NoRecords | NotReadyReason::SizeMismatch { .. } => {
            settings.not_ready_retry_ms * 2
        }
        NotReadyReason::Unreadable(_) => settings.not_ready_retry_ms * 6,
    }
}

fn stat(path: &Path, kind: Kind, relative_path: Option<String>) -> Option<Candidate> {
    let meta = std::fs::metadata(path).ok()?;
    let mtime_ms = meta
        .modified()
        .ok()?
        .duration_since(UNIX_EPOCH)
        .ok()?
        .as_millis() as u64;
    meta.is_file().then(|| Candidate {
        path: path.to_path_buf(),
        size: meta.len(),
        mtime_ms,
        kind,
        relative_path,
    })
}

/// The chunk body: reads the file in small pieces and stops the moment the gate closes.
struct GateReader<'a> {
    file: &'a mut File,
    remaining: u64,
    gate: Arc<TrackGate>,
    hub: Arc<Hub>,
}

impl Read for GateReader<'_> {
    fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
        if self.remaining == 0 {
            return Ok(0);
        }
        if !self.gate.allows_file_work() || self.hub.is_shutdown() {
            return Err(io::Error::other("on-track gate closed"));
        }
        let want = buf.len().min(self.remaining as usize);
        let n = self.file.read(&mut buf[..want])?;
        self.remaining -= n as u64;
        Ok(n)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::journal::Journal;
    use crate::util::{ManualClock, uuid_v4};
    use std::cell::Cell;
    use std::fs;

    struct Env {
        root: std::path::PathBuf,
        up: Uploader,
        hub: Arc<Hub>,
    }

    impl Env {
        fn new() -> Env {
            let root = std::env::temp_dir().join(format!("coach-up-{}", uuid_v4()));
            let paths = Paths {
                state_dir: root.join("state"),
                iracing_root: root.join("iRacing"),
            };
            fs::create_dir_all(paths.telemetry_dir()).unwrap();
            fs::create_dir_all(paths.setups_dir().join("car/shop")).unwrap();
            let hub = Hub::new(
                Arc::new(ManualClock::new(1_790_000_000_000)),
                Journal::in_memory(),
                false,
            );
            let up = Uploader::new(
                hub.clone(),
                "http://127.0.0.1:1",
                paths,
                UploaderSettings::new("11111111-2222-4333-8444-555555555555"),
            );
            Env { root, up, hub }
        }
    }

    impl Drop for Env {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.root);
        }
    }

    fn stops_after(budget: usize, calls: &Cell<usize>) -> impl Fn() -> bool + '_ {
        calls.set(0);
        move || {
            calls.set(calls.get() + 1);
            calls.get() <= budget
        }
    }

    /// Two setup files on disk and a queued recording that is gone from disk (in the queue and in
    /// the journal): a complete scan finds the setups and drops the ghost, and only a complete one.
    fn scan_fixture() -> (Env, Candidate) {
        let mut env = Env::new();
        let setups = env.up.paths.setups_dir();
        fs::write(setups.join("car/shop/a.sto"), b"1").unwrap();
        fs::write(setups.join("car/b.sto"), b"22").unwrap();
        let ghost = Candidate {
            path: env.up.paths.telemetry_dir().join("ghost.ibt"),
            size: 5,
            mtime_ms: 1,
            kind: Kind::Ibt,
            relative_path: None,
        };
        env.up.items.push(Item {
            cand: ghost.clone(),
            next_try_ms: 0,
            failed: false,
        });
        env.hub
            .journal()
            .add_recording(RecordingEntry {
                path: ghost.key(),
                size: 5,
                mtime_ms: 1,
                kind: "ibt".into(),
                relative_path: None,
                sha256: None,
            })
            .unwrap();
        (env, ghost)
    }

    #[test]
    fn a_scan_closed_mid_walk_keeps_its_queue_and_stays_due() {
        let calls = Cell::new(0);
        let (mut env, ghost) = scan_fixture();
        // How many checks a complete scan makes.
        assert!(env.up.scan(&stops_after(usize::MAX, &calls)));
        let full = calls.get();
        assert!(full > 8, "{full}");
        assert!(env.up.items.iter().all(|i| i.cand.path != ghost.path));
        assert_eq!(env.hub.journal().pending_recordings().len(), 0);

        // The same state again, interrupted at every possible point.
        for budget in 0..full {
            let (mut env, ghost) = scan_fixture();
            assert!(
                !env.up.scan(&stops_after(budget, &calls)),
                "closed after {budget} checks"
            );
            assert!(env.up.scanned_at_ms.is_none(), "the scan is still due");
            assert!(env.up.rescan_requested);
            assert_eq!(
                env.hub
                    .uploads
                    .scans_done
                    .load(std::sync::atomic::Ordering::Relaxed),
                0
            );
            assert!(
                env.up.items.iter().any(|i| i.cand.path == ghost.path),
                "nothing is dropped on a partial listing (budget {budget})"
            );
            assert_eq!(
                env.hub.journal().pending_recordings().len(),
                1,
                "the journal entry stays (budget {budget})"
            );
            // Once the gate opens again the scan completes and finishes the reconciliation.
            assert!(env.up.scan(&|| true));
            assert!(env.up.scanned_at_ms.is_some());
            assert!(env.up.items.iter().all(|i| i.cand.path != ghost.path));
            assert_eq!(
                env.up
                    .items
                    .iter()
                    .filter(|i| i.cand.kind == Kind::Sto)
                    .count(),
                2
            );
        }
    }

    #[test]
    fn pending_journal_entries_are_matched_only_while_the_gate_stays_open() {
        let mut env = Env::new();
        let tel = env.up.paths.telemetry_dir();
        for n in 0..3 {
            let path = tel.join(format!("p{n}.ibt"));
            fs::write(&path, b"abc").unwrap();
            env.hub
                .journal()
                .add_recording(RecordingEntry {
                    path: path.to_string_lossy().into_owned(),
                    size: 3,
                    mtime_ms: 1,
                    kind: "ibt".into(),
                    relative_path: None,
                    sha256: None,
                })
                .unwrap();
        }
        let calls = Cell::new(0);
        assert!(!env.up.load_journal(&stops_after(1, &calls)));
        assert_eq!(env.up.items.len(), 1, "one entry reached, the rest wait");
        assert_eq!(env.hub.journal().pending_recordings().len(), 3);
        assert!(env.up.load_journal(&|| true));
        assert_eq!(env.up.items.len(), 3);
    }
}

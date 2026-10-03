//! Garage events to disk, and the `.sto` hash. The reader thread only detects events; this thread
//! journals them (fsync) and resolves each commit's setup file under the same off-track rule as
//! the uploader (`collector-protocol.md` sections 4 and 8): searching and hashing the file waits
//! for fresh off-track evidence and aborts when it lapses.

use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::sync::mpsc::{Receiver, RecvTimeoutError};
use std::time::Duration;

use coach_ibt::{HashOutcome, sha256_file};
use serde_json::json;

use crate::dry::DryLog;
use crate::garage::{GarageAction, StoResolution, resolve_sto};
use crate::hub::Hub;
use crate::journal::{CommitEntry, RecordingEntry};

/// How often unresolved commits are retried while the gate is closed.
const RETRY_MS: u64 = 250;

pub struct GarageIo {
    hub: Arc<Hub>,
    setups_dir: PathBuf,
    dry: Option<Arc<DryLog>>,
}

impl GarageIo {
    pub fn new(hub: Arc<Hub>, setups_dir: PathBuf, dry: Option<Arc<DryLog>>) -> GarageIo {
        GarageIo {
            hub,
            setups_dir,
            dry,
        }
    }

    /// Runs until the channel closes or shutdown is requested.
    pub fn run(&self, rx: Receiver<GarageAction>) {
        while !self.hub.is_shutdown() {
            match rx.recv_timeout(Duration::from_millis(RETRY_MS)) {
                Ok(action) => self.journal(action),
                Err(RecvTimeoutError::Timeout) => {}
                Err(RecvTimeoutError::Disconnected) => {
                    self.resolve_pending();
                    return;
                }
            }
            self.resolve_pending();
        }
    }

    /// Journals one action. A failed append is logged: the event stays in memory only.
    pub fn journal(&self, action: GarageAction) {
        let result = {
            let mut journal = self.hub.journal();
            match &action {
                GarageAction::Snapshot(s) => journal.add_snapshot(s.clone()),
                GarageAction::Commit(c) => journal.add_commit(c.clone()),
            }
        };
        if let Err(e) = result {
            crate::error!("journal append failed: {e}");
        }
        if let (Some(dry), GarageAction::Snapshot(s)) = (&self.dry, &action) {
            dry.write(&json!({"kind": "garage.snapshot", "snapshot": s}));
        }
    }

    /// Resolves every commit that still needs its `.sto` hash. Returns how many are left.
    pub fn resolve_pending(&self) -> usize {
        let pending = self.hub.journal().commits_needing_hash();
        let mut left = 0;
        for commit in pending {
            if !self.resolve_one(&commit) {
                left += 1;
            }
        }
        left
    }

    fn resolve_one(&self, commit: &CommitEntry) -> bool {
        // Searching the setups folder is file work too: nothing happens while the car is on track.
        if !self.hub.gate.allows_file_work() {
            return false;
        }
        let name = commit.setup_name.as_deref().unwrap_or_default();
        let resolution = resolve_sto(&self.setups_dir, commit.car_path.as_deref(), name);
        let (sha, ambiguous, path) = match resolution {
            StoResolution::NotFound => (None, false, None),
            StoResolution::Ambiguous => (None, true, None),
            StoResolution::Found(path) => {
                let gate = self.hub.gate.clone();
                let hub = self.hub.clone();
                let mut proceed = move |_: u64| gate.allows_file_work() && !hub.is_shutdown();
                match sha256_file(&path, &mut proceed) {
                    Ok(HashOutcome::Complete { sha256, .. }) => (Some(sha256), false, Some(path)),
                    Ok(HashOutcome::Aborted { .. }) => return false,
                    Err(e) => {
                        crate::warn!("hashing {} failed: {e}", path.display());
                        (None, false, None)
                    }
                }
            }
        };
        if let Err(e) = self
            .hub
            .journal()
            .set_commit_hash(&commit.visit_id, sha.clone(), ambiguous)
        {
            crate::error!("journal append failed: {e}");
        }
        if let (Some(sha), Some(path)) = (&sha, &path) {
            self.queue_sto_upload(path, sha);
        }
        if let Some(dry) = &self.dry {
            let values = self.hub.journal().commit_values(&commit.visit_id);
            dry.write(&json!({
                "kind": "garage.commit",
                "commit": CommitEntry { sto_sha256: sha, ambiguous, hash_pending: false, ..commit.clone() },
                "values": values,
            }));
        }
        true
    }

    /// The named `.sto` goes to the upload queue as well (the watcher would find it too).
    fn queue_sto_upload(&self, path: &Path, sha: &str) {
        let Ok(meta) = std::fs::metadata(path) else {
            return;
        };
        let mtime_ms = meta
            .modified()
            .ok()
            .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
            .map_or(0, |d| d.as_millis() as u64);
        let relative = path
            .strip_prefix(&self.setups_dir)
            .ok()
            .map(|p| p.to_string_lossy().replace('\\', "/"));
        let entry = RecordingEntry {
            path: path.to_string_lossy().into_owned(),
            size: meta.len(),
            mtime_ms,
            kind: if path
                .extension()
                .is_some_and(|e| e.eq_ignore_ascii_case("htm"))
            {
                "htm"
            } else {
                "sto"
            }
            .to_string(),
            relative_path: relative,
            sha256: Some(sha.to_string()),
        };
        if self.dry.is_none() {
            let mut journal = self.hub.journal();
            // Already uploaded this run (the folder scan got there first): nothing to queue.
            let held = journal
                .seen_hash(
                    &entry.path,
                    entry.size,
                    entry.mtime_ms,
                    self.hub.clock.unix_ms(),
                )
                .is_some();
            if !held && let Err(e) = journal.add_recording(entry) {
                crate::error!("journal append failed: {e}");
            }
            drop(journal);
            self.hub.wake_uploader();
        }
    }
}

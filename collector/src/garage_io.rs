//! Garage events to disk, and the `.sto` hash. The reader thread only detects events; this thread
//! journals them (fsync) and resolves each commit's setup file under the same off-track rule as
//! the uploader (`collector-protocol.md` sections 4 and 8): searching and hashing the file waits
//! for fresh off-track evidence and aborts when it lapses.
//!
//! An event whose journal append fails (disk full) stays in a queue here and is tried again, in
//! order, until it is on disk; nothing is sent before that. A `.sto` that changes while it is
//! hashed is resolved again from the start.

use std::collections::VecDeque;
use std::path::{Path, PathBuf};
use std::sync::mpsc::{Receiver, RecvTimeoutError};
use std::sync::{Arc, Mutex, MutexGuard};
use std::time::{Duration, SystemTime};

use coach_ibt::{HashOutcome, sha256_file};
use serde_json::json;

use crate::dry::DryLog;
use crate::garage::{GarageAction, StoResolution, resolve_sto};
use crate::hub::Hub;
use crate::journal::{CommitEntry, RecordingEntry};

/// How often unresolved commits and queued events are retried while the gate is closed.
const RETRY_MS: u64 = 250;

/// Size and modification time of a file: what must not change while it is hashed.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct Signature {
    size: u64,
    mtime: Option<SystemTime>,
}

fn signature(path: &Path) -> Option<Signature> {
    let meta = std::fs::metadata(path).ok()?;
    meta.is_file().then(|| Signature {
        size: meta.len(),
        mtime: meta.modified().ok(),
    })
}

enum Hashed {
    /// The hash, and the signature that held before and after reading.
    Stable(String, Signature),
    /// The gate closed: try again when it opens.
    Aborted,
    /// The file changed (or vanished) while it was read: resolve it again.
    Changed,
    /// The file cannot be read.
    Unreadable,
}

fn hash_stable(path: &Path, proceed: &mut dyn FnMut(u64) -> bool) -> Hashed {
    let Some(before) = signature(path) else {
        return Hashed::Unreadable;
    };
    match sha256_file(path, proceed) {
        Ok(HashOutcome::Complete { sha256, bytes }) => {
            if signature(path) == Some(before) && bytes == before.size {
                Hashed::Stable(sha256, before)
            } else {
                Hashed::Changed
            }
        }
        Ok(HashOutcome::Aborted { .. }) => Hashed::Aborted,
        Err(e) => {
            crate::warn!("hashing {} failed: {e}", path.display());
            Hashed::Unreadable
        }
    }
}

pub struct GarageIo {
    hub: Arc<Hub>,
    setups_dir: PathBuf,
    dry: Option<Arc<DryLog>>,
    /// Events not yet on disk, oldest first.
    backlog: Mutex<VecDeque<GarageAction>>,
}

impl GarageIo {
    pub fn new(hub: Arc<Hub>, setups_dir: PathBuf, dry: Option<Arc<DryLog>>) -> GarageIo {
        GarageIo {
            hub,
            setups_dir,
            dry,
            backlog: Mutex::new(VecDeque::new()),
        }
    }

    fn backlog(&self) -> MutexGuard<'_, VecDeque<GarageAction>> {
        self.backlog.lock().unwrap_or_else(|e| e.into_inner())
    }

    /// Runs until the channel closes or shutdown is requested.
    pub fn run(&self, rx: Receiver<GarageAction>) {
        while !self.hub.is_shutdown() {
            match rx.recv_timeout(Duration::from_millis(RETRY_MS)) {
                Ok(action) => self.journal(action),
                Err(RecvTimeoutError::Timeout) => {}
                Err(RecvTimeoutError::Disconnected) => {
                    self.flush_backlog();
                    self.resolve_pending();
                    return;
                }
            }
            self.flush_backlog();
            self.hub.journal().retry_attach();
            self.resolve_pending();
        }
        self.flush_backlog();
    }

    /// Journals one action. When the append fails the action is kept and tried again with the
    /// next call and on every pass of `run`; later actions wait behind it.
    pub fn journal(&self, action: GarageAction) {
        self.backlog().push_back(action);
        self.flush_backlog();
    }

    /// Appends queued actions in order; stops at the first failure. Returns how many are left.
    pub fn flush_backlog(&self) -> usize {
        let mut queue = self.backlog();
        while let Some(action) = queue.front() {
            let result = {
                let mut journal = self.hub.journal();
                match action {
                    GarageAction::Snapshot(s) => journal.add_snapshot(s.clone()),
                    GarageAction::Commit(c) => journal.add_commit(c.clone()),
                }
            };
            if let Err(e) = result {
                crate::error!(
                    "journal append failed, {} garage events wait: {e}",
                    queue.len()
                );
                break;
            }
            if let (Some(dry), Some(GarageAction::Snapshot(s))) = (&self.dry, queue.pop_front()) {
                dry.write(&json!({"kind": "garage.snapshot", "snapshot": s}));
            }
        }
        queue.len()
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
        let (sha, ambiguous, found) = match resolution {
            StoResolution::NotFound => (None, false, None),
            StoResolution::Ambiguous => (None, true, None),
            StoResolution::Found(path) => {
                let gate = self.hub.gate.clone();
                let hub = self.hub.clone();
                let mut proceed = move |_: u64| gate.allows_file_work() && !hub.is_shutdown();
                match hash_stable(&path, &mut proceed) {
                    Hashed::Stable(sha256, sig) => (Some(sha256), false, Some((path, sig))),
                    Hashed::Aborted | Hashed::Changed => return false,
                    Hashed::Unreadable => (None, false, None),
                }
            }
        };
        if let Err(e) = self
            .hub
            .journal()
            .set_commit_hash(&commit.visit_id, sha.clone(), ambiguous)
        {
            // The commit stays unresolved and is looked at again.
            crate::error!("journal append failed: {e}");
            return false;
        }
        if let (Some(sha), Some((path, sig))) = (&sha, &found) {
            self.queue_sto_upload(path, sha, *sig);
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

    /// The named `.sto` goes to the upload queue as well (the watcher would find it too), with the
    /// size and modification time it had while it was hashed.
    fn queue_sto_upload(&self, path: &Path, sha: &str, sig: Signature) {
        let mtime_ms = sig
            .mtime
            .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
            .map_or(0, |d| d.as_millis() as u64);
        let relative = path
            .strip_prefix(&self.setups_dir)
            .ok()
            .map(|p| p.to_string_lossy().replace('\\', "/"));
        let entry = RecordingEntry {
            path: path.to_string_lossy().into_owned(),
            size: sig.size,
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::garage::{GarageCapture, GarageTick, SetupView};
    use crate::gate::Evidence;
    use crate::journal::{GarageOut, Journal, SnapshotEntry};
    use crate::util::{ManualClock, uuid_v4};
    use std::collections::BTreeMap;
    use std::fs;
    use std::io::Write;

    fn temp_dir() -> PathBuf {
        let dir = std::env::temp_dir().join(format!("coach-gio-{}", uuid_v4()));
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn snapshot(visit: &str, n: i64) -> GarageAction {
        GarageAction::Snapshot(SnapshotEntry {
            visit_id: visit.into(),
            update_count: n,
            observed_at: "2026-10-02T00:00:00.000Z".into(),
            car_path: Some("car".into()),
            driver: None,
            session: None,
            values: BTreeMap::from([("A.B".to_string(), n.to_string())]),
        })
    }

    #[test]
    fn a_file_that_changes_while_it_is_hashed_is_not_hashed_at_all() {
        let dir = temp_dir();
        let sto = dir.join("a.sto");
        fs::write(&sto, b"first contents").unwrap();
        // Stable: the hash comes with the signature that held.
        let mut allow = |_: u64| true;
        match hash_stable(&sto, &mut allow) {
            Hashed::Stable(sha, sig) => {
                assert_eq!(sha.len(), 64);
                assert_eq!(sig.size, 14);
            }
            _ => panic!("an untouched file hashes"),
        }
        // Rewritten between the signature and the end of the read.
        let mut tamper = |_: u64| {
            let mut f = fs::OpenOptions::new().append(true).open(&sto).unwrap();
            f.write_all(b" and more").unwrap();
            true
        };
        assert!(matches!(hash_stable(&sto, &mut tamper), Hashed::Changed));
        // Resolution starts again and settles on the new contents.
        let mut allow = |_: u64| true;
        match hash_stable(&sto, &mut allow) {
            Hashed::Stable(_, sig) => assert_eq!(sig.size, 23),
            _ => panic!("the second pass is stable"),
        }
        assert!(matches!(
            hash_stable(&dir.join("missing.sto"), &mut |_| true),
            Hashed::Unreadable
        ));
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn a_commit_waits_for_the_gate_and_queues_its_sto_with_the_hashed_signature() {
        let dir = temp_dir();
        let setups = dir.join("setups");
        fs::create_dir_all(setups.join("car")).unwrap();
        let sto = setups.join("car").join("a.sto");
        fs::write(&sto, b"one").unwrap();
        let clock = Arc::new(ManualClock::new(0));
        let hub = Hub::new(clock, Journal::in_memory(), false);
        hub.gate.observe(Evidence::NoMap);
        let io = GarageIo::new(hub.clone(), setups.clone(), None);
        let mut g = GarageCapture::new();
        let view = |count: i64, name: &str| SetupView {
            update_count: Some(count),
            values: BTreeMap::from([("A.B".to_string(), count.to_string())]),
            name: Some(name.to_string()),
            is_modified: Some(false),
            load_type: None,
            car_path: Some("car".into()),
            driver: None,
            session: None,
        };
        let tick = |g: &mut GarageCapture, now_ms: u64, in_garage: bool, v: &SetupView| {
            g.observe(&GarageTick {
                now_ms,
                connected: true,
                in_garage: Some(in_garage),
                setup: v,
            })
        };
        let mut actions = tick(&mut g, 0, true, &view(1, "old.sto"));
        actions.extend(tick(&mut g, 100, true, &view(2, "old.sto")));
        actions.extend(tick(&mut g, 200, false, &view(2, "old.sto")));
        actions.extend(tick(&mut g, 300, false, &view(2, "a.sto")));
        for a in actions {
            io.journal(a);
        }
        assert_eq!(hub.journal().commits_needing_hash().len(), 1);
        // The gate closing during the hash leaves the commit pending; a clean pass resolves it.
        hub.gate.observe(Evidence::Live {
            on_track: Some(true),
        });
        assert_eq!(io.resolve_pending(), 1);
        hub.gate.observe(Evidence::NoMap);
        assert_eq!(io.resolve_pending(), 0);
        let sent = hub.journal().take_unsent(1);
        assert!(sent.iter().any(|m| matches!(m,
            GarageOut::Commit { entry, .. } if entry.sto_sha256.is_some() && !entry.hash_pending)));
        // The upload entry carries the size and mtime the hash was taken at.
        let pending = hub.journal().pending_recordings();
        assert_eq!(pending.len(), 1);
        assert_eq!(pending[0].size, 3);
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn events_that_could_not_be_journaled_wait_and_go_in_order() {
        let dir = temp_dir();
        let path = dir.join("journal.jsonl");
        let clock = Arc::new(ManualClock::new(0));
        let hub = Hub::new(clock, Journal::open(&path).unwrap(), false);
        let io = GarageIo::new(hub.clone(), dir.join("setups"), None);
        io.journal(snapshot("v1", 1));
        hub.journal().fail_next_writes(2);
        io.journal(snapshot("v1", 2));
        io.journal(snapshot("v1", 3));
        assert_eq!(
            hub.journal().garage_pending(),
            1,
            "only the first is on disk"
        );
        assert!(
            hub.journal()
                .take_unsent(1)
                .iter()
                .all(|m| matches!(m, GarageOut::Snapshot(s) if s.update_count == 1)),
            "nothing unjournaled is sent"
        );
        // The disk recovers: the held events follow in order, none lost.
        assert_eq!(io.flush_backlog(), 0);
        let counts: Vec<i64> = hub
            .journal()
            .take_unsent(2)
            .into_iter()
            .filter_map(|m| match m {
                GarageOut::Snapshot(s) => Some(s.update_count),
                GarageOut::Commit { .. } => None,
            })
            .collect();
        assert_eq!(counts, [1, 2, 3]);
        let reopened = Journal::open(&path).unwrap();
        assert_eq!(reopened.garage_pending(), 3, "all three are durable");
        let _ = fs::remove_dir_all(dir);
    }
}

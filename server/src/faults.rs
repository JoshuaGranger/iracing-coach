//! Fault injection for the ingest and archive crash tests. A named point either "crashes" (the
//! operation stops at once and runs no cleanup, as if the process was killed) or fails with an
//! I/O error such as `ENOSPC`. Points are disarmed after they fire once unless armed as sticky.

use std::collections::HashMap;
use std::io::ErrorKind;
use std::sync::Mutex;

use crate::error::{Error, Result};

#[derive(Debug, Clone, Copy)]
pub enum FaultMode {
    Crash,
    Io(ErrorKind),
}

#[derive(Default)]
pub struct Faults {
    armed: Mutex<HashMap<String, (FaultMode, bool)>>,
    hits: Mutex<HashMap<String, u32>>,
}

impl Faults {
    /// Fires once at `name`.
    pub fn arm(&self, name: &str, mode: FaultMode) {
        self.armed
            .lock()
            .unwrap()
            .insert(name.to_string(), (mode, false));
    }

    pub fn arm_sticky(&self, name: &str, mode: FaultMode) {
        self.armed
            .lock()
            .unwrap()
            .insert(name.to_string(), (mode, true));
    }

    pub fn disarm_all(&self) {
        self.armed.lock().unwrap().clear();
    }

    pub fn hits(&self, name: &str) -> u32 {
        self.hits.lock().unwrap().get(name).copied().unwrap_or(0)
    }

    /// Called at every point where a crash test may cut the protocol.
    pub fn check(&self, name: &str) -> Result<()> {
        {
            let mut hits = self.hits.lock().unwrap();
            *hits.entry(name.to_string()).or_default() += 1;
        }
        let mut armed = self.armed.lock().unwrap();
        let Some(&(mode, sticky)) = armed.get(name) else {
            return Ok(());
        };
        if !sticky {
            armed.remove(name);
        }
        Err(match mode {
            FaultMode::Crash => Error::Crash(name.to_string()),
            FaultMode::Io(kind) => {
                Error::Io(std::io::Error::new(kind, format!("injected at {name}")))
            }
        })
    }
}

/// Every point the upload and archive protocols expose, in protocol order.
pub const UPLOAD_POINTS: &[&str] = &[
    "upload.after_create",
    "upload.before_write",
    "upload.after_write",
    "upload.after_fsync",
    "upload.after_offset",
    "commit.after_hash",
    "commit.after_mtime",
    "commit.after_rename",
    "commit.after_dir_sync",
    "commit.before_tx",
    "commit.after_tx",
];

pub const ARCHIVE_POINTS: &[&str] = &[
    "archive.after_tmp_create",
    "archive.after_tmp_write",
    "archive.after_readback",
    "archive.after_data_rename",
    "archive.after_manifest_tmp",
    "archive.after_manifest_rename",
    "archive.before_db",
];

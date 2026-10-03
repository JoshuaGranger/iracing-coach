//! The `--dry-run` log: JSON lines with the captures (garage snapshots and commits) and a frame
//! summary every second, so a rig session can be compared against the next recording (gate G3)
//! without a server.

use std::fs::{File, OpenOptions};
use std::io::{self, Write};
use std::path::Path;
use std::sync::Mutex;

use serde_json::Value;

pub struct DryLog {
    file: Mutex<File>,
}

impl DryLog {
    pub fn create(path: &Path) -> io::Result<DryLog> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        Ok(DryLog {
            file: Mutex::new(OpenOptions::new().create(true).append(true).open(path)?),
        })
    }

    pub fn write(&self, record: &Value) {
        let mut line = record.to_string();
        line.push('\n');
        let mut file = self.file.lock().unwrap_or_else(|e| e.into_inner());
        if let Err(e) = file.write_all(line.as_bytes()).and_then(|()| file.flush()) {
            crate::warn!("dry-run log write failed: {e}");
        }
    }
}

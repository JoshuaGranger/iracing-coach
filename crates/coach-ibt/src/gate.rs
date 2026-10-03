//! Finalization gate (`workflow._assert_finalized_ibt`, workflow.py:1254-1295, plus the share
//! check), streaming SHA-256 and the setup values fingerprint (`setup-keys.json`).

use std::collections::BTreeMap;
use std::fs::File;
use std::io::Read;
use std::path::Path;
use std::sync::LazyLock;
use std::time::{Duration, SystemTime};

use regex::{Regex, RegexBuilder};
use sha2::{Digest, Sha256};

use crate::{HASH_BLOCK_BYTES, IbtError, IbtFile, QUIET_PERIOD};

pub(crate) fn hex(bytes: &[u8]) -> String {
    const DIGITS: &[u8; 16] = b"0123456789abcdef";
    let mut out = String::with_capacity(bytes.len() * 2);
    for &byte in bytes {
        out.push(char::from(DIGITS[usize::from(byte >> 4)]));
        out.push(char::from(DIGITS[usize::from(byte & 0x0F)]));
    }
    out
}

#[derive(Debug, Clone, PartialEq)]
pub enum Finalization {
    Ready {
        size: u64,
        record_count: u32,
        mtime: SystemTime,
    },
    NotReady(NotReadyReason),
}

#[derive(Debug, Clone, PartialEq)]
pub enum NotReadyReason {
    /// `record_count <= 0`.
    NoRecords,
    /// `expected = buffer_offset + record_count * buffer_length`.
    SizeMismatch { expected: u64, actual: u64 },
    /// `now - mtime < QUIET_PERIOD`; zero when the mtime is in the future.
    TooRecent { age: Duration },
    /// Windows: opening with share mode `FILE_SHARE_READ` fails (a writer holds it).
    InUse,
    /// `IbtError` display (header, bounds, I/O).
    Unreadable(String),
}

/// True when another handle holds the file with write access.
#[cfg(windows)]
fn held_by_writer(path: &Path) -> Result<bool, std::io::Error> {
    use std::os::windows::fs::OpenOptionsExt;
    const FILE_SHARE_READ: u32 = 0x1;
    const ERROR_SHARING_VIOLATION: i32 = 32;
    match std::fs::OpenOptions::new()
        .read(true)
        .share_mode(FILE_SHARE_READ)
        .open(path)
    {
        Ok(_) => Ok(false),
        Err(error) if error.raw_os_error() == Some(ERROR_SHARING_VIOLATION) => Ok(true),
        Err(error) => Err(error),
    }
}

#[cfg(not(windows))]
fn held_by_writer(_path: &Path) -> Result<bool, std::io::Error> {
    Ok(false)
}

/// `workflow.py:1254-1295` plus the share check, in that order: header and bounds, records,
/// size, quiet period, writer. `now` is passed for tests. The share check is skipped on
/// non-Windows targets.
pub fn finalization_check(path: &Path, now: SystemTime) -> Finalization {
    let not_ready = Finalization::NotReady;
    let file = match IbtFile::open(path) {
        Ok(file) => file,
        Err(error) => return not_ready(NotReadyReason::Unreadable(error.to_string())),
    };
    if file.record_count == 0 {
        return not_ready(NotReadyReason::NoRecords);
    }
    let expected =
        file.buffer_offset + u64::from(file.record_count) * u64::from(file.buffer_length);
    if file.file_size != expected {
        return not_ready(NotReadyReason::SizeMismatch {
            expected,
            actual: file.file_size,
        });
    }
    let age = now.duration_since(file.mtime).unwrap_or(Duration::ZERO);
    if age < QUIET_PERIOD {
        return not_ready(NotReadyReason::TooRecent { age });
    }
    match held_by_writer(path) {
        Ok(true) => not_ready(NotReadyReason::InUse),
        Ok(false) => Finalization::Ready {
            size: file.file_size,
            record_count: file.record_count,
            mtime: file.mtime,
        },
        Err(error) => not_ready(NotReadyReason::Unreadable(IbtError::Io(error).to_string())),
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum HashOutcome {
    /// `sha256` is lowercase hex.
    Complete {
        sha256: String,
        bytes: u64,
    },
    Aborted {
        bytes_read: u64,
    },
}

/// Reads until `buffer` is full or the file ends.
fn fill(file: &mut File, buffer: &mut [u8]) -> Result<usize, std::io::Error> {
    let mut filled = 0;
    while filled < buffer.len() {
        match file.read(&mut buffer[filled..]) {
            Ok(0) => break,
            Ok(read) => filled += read,
            Err(error) if error.kind() == std::io::ErrorKind::Interrupted => {}
            Err(error) => return Err(error),
        }
    }
    Ok(filled)
}

/// Streaming SHA-256 in `HASH_BLOCK_BYTES` blocks. `proceed(bytes_so_far)` is called before every
/// block; returning false stops at once with `Aborted` (the collector's on-track gate).
pub fn sha256_file(
    path: &Path,
    proceed: &mut dyn FnMut(u64) -> bool,
) -> Result<HashOutcome, IbtError> {
    let mut file = File::open(path)?;
    let mut hasher = Sha256::new();
    let mut buffer = vec![0u8; HASH_BLOCK_BYTES];
    let mut total = 0u64;
    loop {
        if !proceed(total) {
            return Ok(HashOutcome::Aborted { bytes_read: total });
        }
        let read = fill(&mut file, &mut buffer)?;
        hasher.update(&buffer[..read]);
        total += read as u64;
        if read < buffer.len() {
            break;
        }
    }
    Ok(HashOutcome::Complete {
        sha256: hex(&hasher.finalize()),
        bytes: total,
    })
}

static EXCLUDED_LEAVES: LazyLock<Vec<Regex>> = LazyLock::new(|| {
    coach_wire::setup_keys::EXCLUDED_LEAF_PATTERNS
        .iter()
        .map(|(pattern, _reason)| {
            RegexBuilder::new(pattern)
                .case_insensitive(true)
                .build()
                .expect("setup-keys.json patterns are valid regular expressions")
        })
        .collect()
});

/// True when the last path segment of a `CarSetup` key matches an excluded pattern
/// (case-insensitive).
pub fn is_identity_excluded(key: &str) -> bool {
    let leaf = key.rsplit('.').next().unwrap_or(key);
    EXCLUDED_LEAVES.iter().any(|pattern| pattern.is_match(leaf))
}

/// `setup-keys.json`: drop excluded keys; hash the rest in ascending UTF-8 byte order as
/// `key + 0x1F + value.trim() + 0x1E`; lowercase hex, first 32 characters.
pub fn setup_values_fingerprint(values: &BTreeMap<String, String>) -> String {
    let mut hasher = Sha256::new();
    for (key, value) in values {
        if is_identity_excluded(key) {
            continue;
        }
        hasher.update(key.as_bytes());
        hasher.update([0x1F]);
        hasher.update(value.trim().as_bytes());
        hasher.update([0x1E]);
    }
    let mut digest = hex(&hasher.finalize());
    digest.truncate(32);
    digest
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_contract_pattern_compiles() {
        assert_eq!(
            EXCLUDED_LEAVES.len(),
            coach_wire::setup_keys::EXCLUDED_LEAF_PATTERNS.len()
        );
    }

    #[test]
    fn exclusions_match_the_last_segment_case_insensitively() {
        assert!(is_identity_excluded("Chassis.LeftFront.CornerWeight"));
        assert!(is_identity_excluded("Chassis.LeftFront.cornerweight"));
        assert!(is_identity_excluded("Chassis.Front.ArbPreload"));
        assert!(is_identity_excluded("Tires.LeftFront.LastTempsOMI"));
        assert!(is_identity_excluded("Tires.LeftFront.TreadRemaining"));
        assert!(is_identity_excluded("UpdateCount"));
        assert!(!is_identity_excluded("Chassis.LeftFront.CornerWeightBias"));
        assert!(!is_identity_excluded("CornerWeight.Camber"));
        assert!(!is_identity_excluded("Tires.LeftFront.ColdPressure"));
    }

    #[test]
    fn fingerprint_hashes_the_kept_pairs_in_key_order() {
        let values: BTreeMap<String, String> = [
            ("Tires.LeftFront.ColdPressure", " 25.0 psi "),
            ("Chassis.LeftFront.CornerWeight", "2,431 N"),
            ("Chassis.LeftFront.Camber", "-2.8 deg"),
            ("UpdateCount", "3"),
        ]
        .into_iter()
        .map(|(k, v)| (k.to_owned(), v.to_owned()))
        .collect();
        // hashlib.sha256(b"Chassis.LeftFront.Camber\x1f-2.8 deg\x1e"
        //                b"Tires.LeftFront.ColdPressure\x1f25.0 psi\x1e").hexdigest()[:32]
        assert_eq!(
            setup_values_fingerprint(&values),
            "3e1c4e820482b9ce36769799d5db81e8"
        );
        assert_eq!(
            setup_values_fingerprint(&BTreeMap::new()),
            "e3b0c44298fc1c149afbf4c8996fb924"
        );
    }
}

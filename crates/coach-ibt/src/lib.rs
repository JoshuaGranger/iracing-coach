//! IBT files and the live SDK map share one header layout. This crate decodes headers,
//! variable tables, session-info YAML and the finalization gate; it decodes no telemetry
//! columns. Public API: `docs/08-rewrite/contracts/crate-api.md` section 3. Parsing follows
//! `iracing-coach/skills/analyze-iracing-race/scripts/ibt_reader.py` (the Python oracle) byte
//! for byte; `tests/parity.rs` compares both on every fixture.
#![forbid(unsafe_code)]

use std::time::Duration;

mod extract;
mod file;
mod gate;
mod header;
mod literal;
mod matchers;
mod py;
mod time;
mod yaml;

pub use extract::{CarSetup, DriverSetup, PlayerDriver, SessionEntry, SessionInfo, WeekendInfo};
pub use file::{DiskSubHeader, IbtFile, RecordingSummary, TimeSource, decode_session_info};
pub use gate::{
    Finalization, HashOutcome, NotReadyReason, finalization_check, is_identity_excluded,
    setup_values_fingerprint, sha256_file,
};
pub use header::{SdkHeader, SdkScalar, VarBuf, VarHeader, VarTable, VarType};
pub use yaml::{Scalar, YamlNode, YamlScalar};

// ---- 3.1 constants ----

pub const SDK_HEADER_LEN: usize = 112;
pub const DISK_SUBHEADER_LEN: usize = 32;
pub const VAR_HEADER_LEN: usize = 144;
pub const MAX_VARS: i32 = 16_384;
pub const MAX_IBT_SESSION_INFO_BYTES: i32 = 64 * 1024 * 1024;
pub const MAX_LIVE_SESSION_INFO_BYTES: i32 = 16 * 1024 * 1024;
pub const MAX_TICK_RATE: i32 = 10_000;
pub const QUIET_PERIOD: Duration = Duration::from_secs(2);
pub const HASH_BLOCK_BYTES: usize = 4 * 1024 * 1024;

// ---- 3.8 errors ----

#[derive(Debug, thiserror::Error)]
pub enum IbtError {
    #[error("I/O: {0}")]
    Io(#[from] std::io::Error),
    /// Python `IbtTruncatedError`.
    #[error("{region} needs bytes [{start}, {end}) but only {available} exist")]
    Truncated {
        region: &'static str,
        start: u64,
        end: u64,
        available: u64,
    },
    /// Python `IbtBoundsError`.
    #[error("{what}: {detail}")]
    Bounds { what: &'static str, detail: String },
    /// Python `IbtTypeError`.
    #[error("unsupported SDK type code {type_code} for variable #{index} {name:?}")]
    UnsupportedVarType {
        index: u32,
        name: String,
        type_code: i32,
    },
    /// Python `IbtYamlError`.
    #[error("session YAML line {line}: {message}")]
    Yaml { line: u32, message: String },
    /// Python `IbtFormatError` (version, names).
    #[error("{0}")]
    Format(String),
}

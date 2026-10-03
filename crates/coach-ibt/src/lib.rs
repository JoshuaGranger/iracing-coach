//! IBT files and the live SDK map share one header layout. This crate decodes headers,
//! variable tables, session-info YAML and the finalization gate; it decodes no telemetry
//! columns. Public API: `docs/08-rewrite/contracts/crate-api.md` section 3.
#![forbid(unsafe_code)]
// Scaffold stubs: remove these allows as the bodies are implemented.
#![allow(unused_variables, dead_code, clippy::ptr_arg)]

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::time::{Duration, SystemTime};

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

// ---- 3.2 header and variable table ----

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct VarBuf {
    pub tick_count: i32,
    pub buf_offset: i32,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct SdkHeader {
    pub ver: i32,
    /// Bit 0 = connected (live only).
    pub status: i32,
    pub tick_rate: i32,
    pub session_info_update: i32,
    pub session_info_len: i32,
    pub session_info_offset: i32,
    pub num_vars: i32,
    pub var_header_offset: i32,
    pub num_buf: i32,
    pub buf_len: i32,
    pub var_bufs: [VarBuf; 4],
}

impl SdkHeader {
    /// Little-endian decode of `bytes[0..112]`. Fails only on a short slice.
    pub fn parse(bytes: &[u8]) -> Result<SdkHeader, IbtError> {
        unimplemented!()
    }

    /// `status & 1 != 0`.
    pub fn connected(&self) -> bool {
        unimplemented!()
    }

    /// Live-map bounds (C# `IRacingSdkTelemetrySource.cs:42`).
    pub fn validate_live(&self, map_len: usize) -> Result<(), IbtError> {
        unimplemented!()
    }

    /// The buffer with the highest tick that lies inside the map (C# `:48-60`).
    pub fn newest_buffer(&self, map_len: usize) -> Option<VarBuf> {
        unimplemented!()
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(i32)]
pub enum VarType {
    Char = 0,
    Bool = 1,
    Int = 2,
    BitField = 3,
    Float = 4,
    Double = 5,
}

impl VarType {
    pub fn from_code(code: i32) -> Option<VarType> {
        unimplemented!()
    }

    /// Bytes per element: 1, 1, 4, 4, 4, 8.
    pub fn width(self) -> usize {
        unimplemented!()
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct VarHeader {
    pub var_type: VarType,
    /// Within a sample row.
    pub offset: i32,
    /// Elements (> 0).
    pub count: i32,
    pub count_as_time: bool,
    /// Text rule of the parser that built the table (`parse_ibt` or `parse_live`).
    pub name: String,
    pub desc: String,
    pub unit: String,
}

#[derive(Debug, Clone, PartialEq, Default)]
pub struct VarTable {
    headers: Vec<VarHeader>,
    index: BTreeMap<String, usize>,
}

#[allow(clippy::len_without_is_empty)]
impl VarTable {
    /// IBT rules (`ibt_reader.py:983-1023`). Text fields follow Python `_decode_c_string`: bytes
    /// before the first NUL, UTF-8, else cp1252 with `errors="replace"`.
    pub fn parse_ibt(var_bytes: &[u8], num_vars: i32, buf_len: i32) -> Result<VarTable, IbtError> {
        unimplemented!()
    }

    /// Live rules (C# `:207-230`): bad entries are skipped, never an error. Text follows C#
    /// `Encoding.ASCII` (bytes >= 0x80 -> '?') with trailing NULs trimmed; a later duplicate wins.
    pub fn parse_live(map: &[u8], header: &SdkHeader) -> Result<VarTable, IbtError> {
        unimplemented!()
    }

    pub fn len(&self) -> usize {
        unimplemented!()
    }

    pub fn iter(&self) -> impl Iterator<Item = &VarHeader> {
        self.headers.iter()
    }

    pub fn get(&self, name: &str) -> Option<&VarHeader> {
        unimplemented!()
    }

    // Readers over one sample row (`row.len() >= buf_len`); None when the variable is absent,
    // its type is not accepted, or the row is too short; never because of the value.

    /// Any type; char as unsigned byte, bool 0/1, int, bitfield as u32, float, double -> f64.
    /// A non-finite float or double is returned as is.
    pub fn f64(&self, name: &str, row: &[u8]) -> Option<f64> {
        unimplemented!()
    }

    /// Int and bitfield exactly; float/double truncated toward zero, None when non-finite or outside i32.
    pub fn i32(&self, name: &str, row: &[u8]) -> Option<i32> {
        unimplemented!()
    }

    /// Like `i32` but i64; float/double rounded half to even (C# ReadLong).
    pub fn i64(&self, name: &str, row: &[u8]) -> Option<i64> {
        unimplemented!()
    }

    /// Int or bitfield only (C# ReadUInt).
    pub fn u32(&self, name: &str, row: &[u8]) -> Option<u32> {
        unimplemented!()
    }

    /// Bool only (C# ReadBool).
    pub fn bool(&self, name: &str, row: &[u8]) -> Option<bool> {
        unimplemented!()
    }

    /// Int or bitfield arrays into `out` (cleared first); empty when not accepted.
    pub fn i32_array(&self, name: &str, row: &[u8], out: &mut Vec<i32>) {
        unimplemented!()
    }

    /// Float arrays only; every element as stored, non-finite included (indices stay car indices).
    pub fn f32_array(&self, name: &str, row: &[u8], out: &mut Vec<f32>) {
        unimplemented!()
    }
}

/// One scalar from a sample row with its SDK type preserved (edge reads, goldens).
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum SdkScalar {
    Char(u8),
    Bool(bool),
    Int(i32),
    BitField(u32),
    Float(f32),
    Double(f64),
}

// ---- 3.3 IBT file ----

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct DiskSubHeader {
    /// Unix seconds (wall clock of recording start).
    pub session_start_date: i64,
    /// SDK SessionTime at start, s.
    pub session_start_time: f64,
    /// SDK SessionTime at end, s.
    pub session_end_time: f64,
    pub session_lap_count: i32,
    /// Equals `record_count`.
    pub session_record_count: i32,
}

#[derive(Debug, Clone)]
pub struct IbtFile {
    pub path: PathBuf,
    pub file_size: u64,
    pub mtime: SystemTime,
    pub header: SdkHeader,
    pub disk: DiskSubHeader,
    pub vars: VarTable,
    /// `header.var_bufs[0].buf_offset`.
    pub buffer_offset: u64,
    /// `header.buf_len`.
    pub buffer_length: u32,
    /// `disk.session_record_count`.
    pub record_count: u32,
    /// Raw region `[session_info_offset, +len)`.
    pub session_info_bytes: Vec<u8>,
}

impl IbtFile {
    /// Port of `ibt_reader._read_layout` bounds and order, without YAML parsing. Reads no sample rows.
    pub fn open(path: &Path) -> Result<IbtFile, IbtError> {
        unimplemented!()
    }

    /// Bytes up to the first NUL; UTF-8 with an optional BOM stripped; cp1252 with
    /// `errors="replace"` on invalid UTF-8. Same function as [`decode_session_info`].
    pub fn session_info_text(&self) -> String {
        unimplemented!()
    }

    pub fn session_info(&self) -> Result<SessionInfo, IbtError> {
        unimplemented!()
    }

    /// SHA-256 (lowercase hex) of `session_info_bytes` as stored.
    pub fn session_info_sha256(&self) -> String {
        unimplemented!()
    }

    /// Port of `ibt_reader._read_edge_scalar`: reads the last and (record_count > 1) the first record
    /// and returns the last record's value as stored, non-finite included; never the first because
    /// the last is NaN or fails a later conversion.
    pub fn edge_scalar(&self, name: &str) -> Result<Option<SdkScalar>, IbtError> {
        unimplemented!()
    }

    /// Port of `ibt_reader._extract_summary`.
    pub fn summary(&self) -> Result<RecordingSummary, IbtError> {
        unimplemented!()
    }
}

pub fn decode_session_info(bytes: &[u8]) -> String {
    unimplemented!()
}

// ---- 3.4 recording summary ----

/// Field-for-field port of `ibt_reader._extract_summary`, with `driver_setup_modified` reading
/// `DriverSetupIsModified`, falling back to `DriverSetupModified`.
#[derive(Debug, Clone, PartialEq, serde::Serialize)]
pub struct RecordingSummary {
    pub series_id: Option<i64>,
    pub season_id: Option<i64>,
    pub session_id: Option<i64>,
    /// 0 for offline sessions, kept as 0.
    pub subsession_id: Option<i64>,
    pub race_week: Option<i64>,
    pub event_type: Option<String>,
    pub category: Option<String>,
    pub official: Option<bool>,
    pub track_id: Option<i64>,
    pub track_name: Option<String>,
    pub track_display_name: Option<String>,
    pub track_config_name: Option<String>,
    /// As published, e.g. "1.06 km".
    pub track_length: Option<String>,
    pub is_fixed_setup: Option<bool>,
    pub sim_session_num: Option<i64>,
    pub sim_session_type: Option<String>,
    pub session_unique_id: Option<i64>,
    pub is_race: bool,
    pub car_id: Option<i64>,
    pub car_path: Option<String>,
    pub car_screen_name: Option<String>,
    /// `Drivers[DriverCarIdx].UserID` = customer_id.
    pub driver_user_id: Option<i64>,
    pub driver_setup_name: Option<String>,
    pub driver_setup_modified: Option<bool>,
    pub start_time_unix: f64,
    pub end_time_unix: f64,
    /// Python `_safe_iso`: `datetime.isoformat()` (`YYYY-MM-DDTHH:MM:SS[.ffffff]+00:00`), None when
    /// the epoch is non-finite or outside Python `datetime`.
    pub start_time_utc: Option<String>,
    pub end_time_utc: Option<String>,
    pub time_source: TimeSource,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize)]
#[serde(rename_all = "snake_case")]
pub enum TimeSource {
    DiskHeader,
    SessionYaml,
    Mtime,
}

// ---- 3.5 session info ----

#[derive(Debug, Clone, PartialEq)]
pub enum YamlNode {
    /// Document order; a repeated key keeps both entries, lookups take the last.
    Map(Vec<(String, YamlNode)>),
    List(Vec<YamlNode>),
    Scalar(Scalar),
    /// Leaf of an evaluated quoted or flow literal (Python `ast.literal_eval` subset).
    Literal(YamlScalar),
    /// Key with no value and no block, or "- " with no block.
    Null,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Scalar {
    /// Text after "key:" or "- ", trimmed, quotes kept.
    pub raw: String,
}

#[derive(Debug, Clone, PartialEq, serde::Serialize)]
#[serde(untagged)]
pub enum YamlScalar {
    Null,
    Bool(bool),
    Int(i64),
    Float(f64),
    Str(String),
}

impl Scalar {
    /// Port of `_yaml_scalar`; quoted text that `literal_eval` rejects is the text between the quotes,
    /// flow text it rejects is `Str(raw)`.
    pub fn typed(&self) -> YamlScalar {
        unimplemented!()
    }

    /// `raw` without one pair of matching surrounding quotes.
    pub fn text(&self) -> &str {
        unimplemented!()
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct SessionInfo {
    pub root: YamlNode,
}

impl SessionInfo {
    /// The in-house YAML subset of `_parse_iracing_yaml_subset`; empty text -> empty map.
    pub fn parse(text: &str) -> Result<SessionInfo, IbtError> {
        unimplemented!()
    }

    /// Dotted lookup with list indices, e.g. `WeekendInfo.WeekendOptions.IsFixedSetup`.
    pub fn get(&self, path: &str) -> Option<&YamlNode> {
        unimplemented!()
    }

    pub fn weekend(&self) -> WeekendInfo {
        unimplemented!()
    }

    pub fn sessions(&self) -> Vec<SessionEntry> {
        unimplemented!()
    }

    /// The `DriverInfo.Drivers[]` entry whose `CarIdx == DriverInfo.DriverCarIdx`.
    pub fn driver(&self) -> Option<PlayerDriver> {
        unimplemented!()
    }

    pub fn driver_setup(&self) -> DriverSetup {
        unimplemented!()
    }

    /// None when there is no `CarSetup` map.
    pub fn car_setup(&self) -> Option<CarSetup> {
        unimplemented!()
    }
}

#[derive(Debug, Clone, PartialEq, serde::Serialize)]
pub struct WeekendInfo {
    pub track_name: Option<String>,
    pub track_display_name: Option<String>,
    pub track_config_name: Option<String>,
    pub track_id: Option<i64>,
    pub track_length: Option<String>,
    pub series_id: Option<i64>,
    pub season_id: Option<i64>,
    pub session_id: Option<i64>,
    pub subsession_id: Option<i64>,
    pub race_week: Option<i64>,
    pub event_type: Option<String>,
    pub category: Option<String>,
    pub official: Option<bool>,
    pub is_fixed_setup: Option<bool>,
}

#[derive(Debug, Clone, PartialEq, serde::Serialize)]
pub struct SessionEntry {
    /// `Sessions[].SessionNum`.
    pub session_num: Option<i64>,
    /// "Race", "Qualify", "Practice", "Offline Testing", ...
    pub session_type: Option<String>,
    pub session_name: Option<String>,
}

#[derive(Debug, Clone, PartialEq, serde::Serialize)]
pub struct PlayerDriver {
    pub car_idx: i64,
    /// customer_id.
    pub user_id: Option<i64>,
    pub user_name: Option<String>,
    /// `IRating`.
    pub irating: Option<i64>,
    pub car_id: Option<i64>,
    pub car_path: Option<String>,
    pub car_screen_name: Option<String>,
}

#[derive(Debug, Clone, PartialEq, serde::Serialize)]
pub struct DriverSetup {
    /// `DriverInfo.DriverSetupName`.
    pub name: Option<String>,
    /// `DriverSetupIsModified`, else `DriverSetupModified`.
    pub is_modified: Option<bool>,
    /// `DriverSetupLoadTypeName` (user, iracing, fixed).
    pub load_type_name: Option<String>,
    /// `DriverSetupLoadType`.
    pub load_type: Option<i64>,
}

#[derive(Debug, Clone, PartialEq, serde::Serialize)]
pub struct CarSetup {
    /// `CarSetup.UpdateCount`.
    pub update_count: Option<i64>,
    /// Every scalar leaf under `CarSetup`, key = dotted path (e.g. `Chassis.LeftFront.Camber`),
    /// value = `Scalar::text()` (e.g. "-2.8 deg"). Null leaves are omitted. Includes `UpdateCount`
    /// and derived keys; this is the wire `SetupValues` map.
    pub values: BTreeMap<String, String>,
}

// ---- 3.6 setup fingerprint ----

/// `setup-keys.json`: drop keys whose last segment matches an excluded pattern; hash the rest in
/// ascending UTF-8 byte order as `key + 0x1F + value.trim() + 0x1E`; lowercase hex, first 32 characters.
pub fn setup_values_fingerprint(values: &BTreeMap<String, String>) -> String {
    unimplemented!()
}

pub fn is_identity_excluded(key: &str) -> bool {
    unimplemented!()
}

// ---- 3.7 finalization gate and hashing ----

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
    /// `now - mtime < QUIET_PERIOD`.
    TooRecent { age: Duration },
    /// Windows: opening with share mode `FILE_SHARE_READ` fails (a writer holds it).
    InUse,
    /// `IbtError` display (header, bounds, I/O).
    Unreadable(String),
}

/// `workflow.py:1254-1295` plus the share check. `now` is passed for tests. The share check is
/// skipped on non-Windows targets.
pub fn finalization_check(path: &Path, now: SystemTime) -> Finalization {
    unimplemented!()
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

/// Streaming SHA-256 in `HASH_BLOCK_BYTES` blocks. `proceed(bytes_so_far)` is called before every
/// block; returning false stops at once with `Aborted` (the collector's on-track gate).
pub fn sha256_file(
    path: &Path,
    proceed: &mut dyn FnMut(u64) -> bool,
) -> Result<HashOutcome, IbtError> {
    unimplemented!()
}

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

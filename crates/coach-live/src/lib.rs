//! The live engine: one `Sample` per SDK tick in, one `LiveState` out; the live frame codec and
//! tile formatting. Public API: `docs/08-rewrite/contracts/crate-api.md` section 4.
#![forbid(unsafe_code)]
// Scaffold stubs: remove these allows as the bodies are implemented.
#![allow(unused_variables, dead_code, clippy::ptr_arg)]

use std::sync::Arc;

use coach_wire::live_channels::{Evidence, FlagState, GapTrend, Priority, RunPhase};

// ---- 4.1 session context ----

/// Built by the collector whenever `SdkHeader::session_info_update` changes, shared with every
/// following sample.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct SessionContext {
    pub session_info_update: i32,
    /// `WeekendInfo.SubSessionID`.
    pub subsession_id: Option<i64>,
    /// `Sessions[]`: (SessionNum, SessionType), entries with a type only.
    pub session_types: Vec<(i64, String)>,
    /// `LiveSteering` rules.
    pub steering_ratio: Option<f64>,
    /// `DriverInfo.DriverCarIdx`.
    pub driver_car_idx: Option<i64>,
    /// The player's `UserID` (the stream session ends when it changes).
    pub driver_user_id: Option<i64>,
    pub driver_user_name: Option<String>,
    pub car_path: Option<String>,
    /// `TrackDisplayName` else `TrackName`.
    pub track: Option<String>,
    pub track_config: Option<String>,
}

impl SessionContext {
    /// Steering ratio per `LiveSteering.cs:22-58`; every other field from the session info.
    pub fn from_session_info(
        info: &coach_ibt::SessionInfo,
        session_info_update: i32,
    ) -> SessionContext {
        unimplemented!()
    }

    pub fn session_type(&self, session_num: i64) -> Option<&str> {
        unimplemented!()
    }
}

// ---- 4.2 sample ----

/// One per new tick, reusing the allocation. Every SDK field is the value as read through the
/// `coach_ibt::VarTable` reader named in the contract's table 4.3; nothing is validated here.
/// Missing = `None`; arrays are empty when missing. A present non-finite value stays `Some(NaN)`
/// (array elements keep NaN at their index); fallbacks select by presence, as C# `??` does.
#[derive(Debug, Clone, Default)]
pub struct Sample {
    // Collector-provided.
    /// Collector wall clock right after the row copy; the engine's only clock.
    pub captured_unix_ms: u64,
    /// Status bit 0 set and the tick advanced within the last 2 s.
    pub connected: bool,
    /// Newest buffer's tick (SDK i32 reinterpreted).
    pub tick: u32,
    /// Header `tick_rate`, saturating.
    pub tick_rate: u16,
    pub session: Option<Arc<SessionContext>>,
    // SDK variables.
    pub player_car_idx: Option<i32>,
    pub player_car_position: Option<i32>,
    pub player_car_class_position: Option<i32>,
    pub car_idx_position: Vec<i32>,
    pub car_idx_class_position: Vec<i32>,
    pub car_idx_lap: Vec<i32>,
    pub car_idx_f2_time: Vec<f32>,
    pub car_idx_last_lap_time: Vec<f32>,
    pub session_flags: Option<u32>,
    pub player_car_tow_time: Option<f64>,
    pub lap: Option<i32>,
    pub session_laps_remain_ex: Option<f64>,
    pub session_laps_remain: Option<f64>,
    pub lap_last_lap_time: Option<f64>,
    pub fuel_level: Option<f64>,
    pub fuel_level_pct: Option<f64>,
    pub track_temp_crew: Option<f64>,
    pub track_temp: Option<f64>,
    pub air_temp: Option<f64>,
    pub dc_brake_bias: Option<f64>,
    pub on_pit_road: Option<bool>,
    pub pit_repair_left: Option<f64>,
    pub pit_opt_repair_left: Option<f64>,
    pub steering_wheel_angle: Option<f64>,
    pub throttle: Option<f64>,
    pub brake: Option<f64>,
    pub brake_abs_active: Option<bool>,
    pub gear: Option<i32>,
    pub rpm: Option<f64>,
    pub yaw_rate: Option<f64>,
    pub lat_accel: Option<f64>,
    pub long_accel: Option<f64>,
    pub speed: Option<f64>,
    pub lap_dist_pct: Option<f64>,
    pub lat: Option<f64>,
    pub lon: Option<f64>,
    pub session_time: Option<f64>,
    pub session_num: Option<i32>,
    pub session_unique_id: Option<i64>,
    pub player_car_my_incident_count: Option<i32>,
    pub player_incidents: Option<i32>,
    pub is_on_track: Option<bool>,
    pub is_in_garage: Option<bool>,
}

impl Sample {
    /// All `None`/empty, capacity kept.
    pub fn clear(&mut self) {
        unimplemented!()
    }

    /// Fills every SDK field from one row (the collector's copy of the newest buffer).
    pub fn read_row(&mut self, vars: &coach_ibt::VarTable, row: &[u8]) {
        unimplemented!()
    }
}

// ---- 4.4 engine ----

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct EngineConfig {
    /// `DriverPrefs.safe_glance`, default true.
    pub safe_glance: bool,
}

impl Default for EngineConfig {
    fn default() -> Self {
        EngineConfig { safe_glance: true }
    }
}

pub struct Engine {
    state: (),
}

impl Engine {
    pub fn new(config: EngineConfig) -> Engine {
        unimplemented!()
    }

    pub fn set_safe_glance(&mut self, enabled: bool) {
        unimplemented!()
    }

    /// Tray "Pause coaching cue"; not persisted.
    pub fn set_paused(&mut self, paused: bool) {
        unimplemented!()
    }

    /// One call per new SDK tick (and once per second while disconnected). Applies the epoch rule,
    /// the reader normalisations, the engine rules and cues, and the safe-glance hold. Never
    /// blocks, never allocates after warm-up.
    pub fn on_tick(&mut self, sample: &Sample) -> LiveState {
        unimplemented!()
    }
}

// ---- 4.5 live state ----

/// One per `on_tick`; plain `Copy` data. Channel fields mirror `live-channels.json` table
/// version 1 one to one (index in comments); `None` = absent bit; a non-finite float is `None`.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct LiveState {
    // Frame header.
    /// Flags bit 0.
    pub connected: bool,
    /// Bit 1, `is_on_track == Some(true)`.
    pub on_track: bool,
    /// Bit 2, `is_in_garage == Some(true)`.
    pub in_garage: bool,
    /// Bit 4 source, first state of a new `session_epoch`; the collector carries it to the next sent
    /// frame (contract 4.8). (Bit 3 is channel 39 == Some(true).)
    pub first_of_epoch: bool,
    /// Bit 5.
    pub coaching_paused: bool,
    pub session_epoch: u32,
    pub captured_unix_ms: u64,
    /// Header field; `None` encodes as NaN.
    pub session_time: Option<f64>,
    pub tick: u32,
    pub tick_rate: u16,
    // Channels.
    /// 0, m/s.
    pub speed: Option<f32>,
    /// 1, ratio.
    pub throttle: Option<f32>,
    /// 2, ratio.
    pub brake: Option<f32>,
    /// 3, rad.
    pub steering_wheel_angle: Option<f32>,
    /// 4, rad; `None` without a ratio.
    pub steering_road_wheel: Option<f32>,
    /// 5.
    pub steering_ratio: Option<f32>,
    /// 6.
    pub gear: Option<i8>,
    /// 7.
    pub rpm: Option<f32>,
    /// 8, rad/s.
    pub yaw_rate: Option<f32>,
    /// 9, m/s2.
    pub lateral_accel: Option<f32>,
    /// 10, m/s2.
    pub longitudinal_accel: Option<f32>,
    /// 11, [0, 1].
    pub lap_dist_pct: Option<f32>,
    /// 12, deg.
    pub latitude: Option<f64>,
    /// 13, deg.
    pub longitude: Option<f64>,
    /// 14, raw + 1, min 1.
    pub lap: Option<u16>,
    /// 15, sentinel rule.
    pub laps_remaining: Option<u16>,
    /// 16.
    pub position: Option<u8>,
    /// 17.
    pub class_position: Option<u8>,
    /// 18; `Some(Unknown)` when flags are missing but connected.
    pub flag_state: Option<FlagState>,
    /// 19.
    pub session_flags: Option<u32>,
    /// 20.
    pub abs_active: Option<bool>,
    /// 21, %.
    pub brake_bias: Option<f32>,
    /// 22, L.
    pub fuel: Option<f32>,
    /// 23, ratio.
    pub fuel_pct: Option<f32>,
    /// 24; needs >= 2 clean fuel samples.
    pub fuel_laps_remaining: Option<f32>,
    /// 25.
    pub fuel_hard_limit_laps: Option<u16>,
    /// 26, s.
    pub last_lap: Option<f32>,
    /// 27, s.
    pub leader_last_lap: Option<f32>,
    /// 28, s; the value is kept even when the trend is Stale.
    pub ahead_gap: Option<f32>,
    /// 29.
    pub ahead_gap_trend: Option<GapTrend>,
    /// 30.
    pub behind_gap: Option<f32>,
    /// 31.
    pub behind_gap_trend: Option<GapTrend>,
    /// 32; 0 when the player leads overall.
    pub leader_gap: Option<f32>,
    /// 33, s; needs >= 3 clean laps.
    pub pace_band_min: Option<f32>,
    /// 34.
    pub pace_band_max: Option<f32>,
    /// 35; Early/Middle/Late while connected.
    pub run_phase: Option<RunPhase>,
    /// 36.
    pub green_run_laps: Option<u16>,
    /// 37, s.
    pub mandatory_repair: Option<f32>,
    /// 38, s.
    pub optional_repair: Option<f32>,
    /// 39; also frame flag bit 3.
    pub on_pit_road: Option<bool>,
    /// 40.
    pub tow_active: Option<bool>,
    /// 41, C.
    pub track_temperature: Option<f32>,
    /// 42, C.
    pub air_temperature: Option<f32>,
    /// 43.
    pub incidents: Option<u16>,
    /// 44, s.
    pub pace_delta_to_leader: Option<f32>,
    /// Always `Some` while the engine runs: cue 14 when disconnected, 15 when paused.
    pub cue: Option<CueState>,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct CueState {
    /// `CUES[].id`.
    pub id: u8,
    pub priority: Priority,
    pub evidence: Evidence,
    /// 0..=4.
    pub arg_count: u8,
    /// Unused slots 0.0.
    pub args: [f32; 4],
}

// ---- 4.6 frame codec (`live-frame.md`) ----

pub const FRAME_MAGIC: [u8; 2] = *b"LF";
pub const FRAME_VERSION: u8 = 1;
pub const FRAME_FIXED_HEADER_LEN: usize = 38;

impl LiveState {
    /// Clears `out` and writes one complete frame: header, presence bits for all `CHANNELS`,
    /// present values in index order, cue block (id 0 when `cue` is None).
    pub fn encode_frame(&self, seq: u32, out: &mut Vec<u8>) {
        unimplemented!()
    }
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct FrameHeader {
    pub version: u8,
    pub flags: u8,
    pub seq: u32,
    pub session_epoch: u32,
    pub captured_unix_ms: u64,
    /// NaN when absent.
    pub session_time: f64,
    pub tick: u32,
    pub tick_rate: u16,
    pub table_version: u16,
    pub channel_count: u16,
}

/// Server check: magic, `version == 1`, `table_version <=` own `TABLE_VERSION`,
/// `channel_count <= CHANNELS.len()`, and `len >= 38 + ceil(channel_count / 8)`. Decodes no values.
pub fn parse_header(frame: &[u8]) -> Result<FrameHeader, FrameError> {
    unimplemented!()
}

/// Sets flags bit 6 in place (backlog frames).
pub fn set_backlog_flag(frame: &mut [u8]) -> Result<(), FrameError> {
    unimplemented!()
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum ChannelValue {
    F32(f32),
    F64(f64),
    U8(u8),
    I8(i8),
    U16(u16),
    U32(u32),
    Bool(bool),
    Enum(u8),
}

#[derive(Debug, Clone, PartialEq)]
pub struct DecodedFrame {
    pub header: FrameHeader,
    /// `len = channel_count` (<= own table); index >= `channel_count` is absent.
    pub values: Vec<Option<ChannelValue>>,
    /// `None` when the cue id is 0.
    pub cue: Option<CueState>,
}

/// Full decode for tests, golden vectors and the collector's own checks. Every length and
/// discriminant is checked before use.
pub fn decode_frame(frame: &[u8]) -> Result<DecodedFrame, FrameError> {
    unimplemented!()
}

#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum FrameError {
    #[error("frame shorter than {needed} bytes ({len})")]
    TooShort { needed: usize, len: usize },
    #[error("bad magic")]
    BadMagic,
    #[error("unsupported frame version {0}")]
    UnsupportedVersion(u8),
    #[error("table version {frame} newer than {own}")]
    TableNewer { frame: u16, own: u16 },
    /// A sender at a table version <= own cannot know more channels; their widths are unknown.
    #[error("channel count {frame} exceeds own table's {own}")]
    TooManyChannels { frame: u16, own: u16 },
    /// "truncated", "argument count above 4", "unknown cue id", "unknown priority" or
    /// "unknown evidence"; cue id 0 skips the id, priority and evidence checks.
    #[error("bad cue block: {0}")]
    BadCue(&'static str),
}

// ---- 4.7 tile formatting ----

#[derive(Debug, Clone, PartialEq)]
pub struct TileText {
    /// E.g. "P4", "1:32.418", "12.4 US gal", "Waiting", "Not measured".
    pub primary: String,
    /// Cue basis, "{x.x} estimated laps", "{ratio}:1 steering ratio", ...
    pub secondary: Option<String>,
    /// In the chosen display unit, for gauge, bar and trend styles.
    pub value: Option<f64>,
    /// Reading lower bound in the display unit (C# reading `Minimum`), before `display_range`.
    pub min: Option<f64>,
    /// Reading upper bound in the display unit (C# reading `Maximum`), e.g. fuel capacity.
    pub max: Option<f64>,
    pub state: TileState,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TileState {
    Value,
    Waiting,
    NotMeasured,
    InsufficientEvidence,
}

/// `LiveTelemetryCatalog.Read` rules, including reading bounds and the per-tile missing texts
/// (Waiting, Insufficient evidence, Ratio not recorded, Not measured). None for an unknown tile id.
pub fn format_tile(
    tile_id: &str,
    state: &LiveState,
    unit: Option<&str>,
    precision: Option<u8>,
) -> Option<TileText> {
    unimplemented!()
}

/// `LiveTelemetryCatalog.ApplyRange`: display range from `text`, the tile's min/max and the caller's
/// `trend` history in the same display unit. None when `text.value` is None or non-finite.
pub fn display_range(tile_id: &str, text: &TileText, trend: &[f64]) -> Option<(f64, f64)> {
    unimplemented!()
}

/// Cue text from `CUES[id].template` and the cue's args (ceil, int, fixed2, percent0).
pub fn cue_text(cue: &CueState) -> String {
    unimplemented!()
}

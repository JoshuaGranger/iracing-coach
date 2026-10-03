//! The engine's output: one `LiveState` per tick.

use coach_wire::live_channels::{Evidence, FlagState, GapTrend, Priority, RunPhase};

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

impl CueState {
    /// A cue of the table's id with the table's priority and evidence. `None` for an unknown id.
    pub(crate) fn from_table(id: u8, args: &[f32]) -> Option<CueState> {
        let spec = coach_wire::live_channels::CUES
            .iter()
            .find(|spec| spec.id == id)?;
        let mut slots = [0.0_f32; 4];
        let count = args.len().min(4);
        slots[..count].copy_from_slice(&args[..count]);
        Some(CueState {
            id,
            priority: spec.priority,
            evidence: spec.evidence,
            arg_count: count as u8,
            args: slots,
        })
    }
}

impl LiveState {
    /// Every channel absent.
    pub(crate) fn blank(captured_unix_ms: u64, tick: u32, tick_rate: u16) -> LiveState {
        LiveState {
            connected: false,
            on_track: false,
            in_garage: false,
            first_of_epoch: false,
            coaching_paused: false,
            session_epoch: 0,
            captured_unix_ms,
            session_time: None,
            tick,
            tick_rate,
            speed: None,
            throttle: None,
            brake: None,
            steering_wheel_angle: None,
            steering_road_wheel: None,
            steering_ratio: None,
            gear: None,
            rpm: None,
            yaw_rate: None,
            lateral_accel: None,
            longitudinal_accel: None,
            lap_dist_pct: None,
            latitude: None,
            longitude: None,
            lap: None,
            laps_remaining: None,
            position: None,
            class_position: None,
            flag_state: None,
            session_flags: None,
            abs_active: None,
            brake_bias: None,
            fuel: None,
            fuel_pct: None,
            fuel_laps_remaining: None,
            fuel_hard_limit_laps: None,
            last_lap: None,
            leader_last_lap: None,
            ahead_gap: None,
            ahead_gap_trend: None,
            behind_gap: None,
            behind_gap_trend: None,
            leader_gap: None,
            pace_band_min: None,
            pace_band_max: None,
            run_phase: None,
            green_run_laps: None,
            mandatory_repair: None,
            optional_repair: None,
            on_pit_road: None,
            tow_active: None,
            track_temperature: None,
            air_temperature: None,
            incidents: None,
            pace_delta_to_leader: None,
            cue: None,
        }
    }
}

//! The live engine: the reader-side normalisations of `IRacingSdkTelemetrySource.cs`, the session
//! boundary rule of `LiveTelemetryService`, and the rules and cues of `LiveTelemetryEngine`
//! (`LiveTelemetry.cs`), driven one `Sample` at a time.

use coach_wire::live_channels::{GapTrend, Priority, RunPhase};
use std::collections::VecDeque;

use crate::sample::Sample;
use crate::session::{near_center, road_wheel_radians};
use crate::state::{CueState, LiveState};
use crate::truth;

/// iRacing's "no lap limit" value, and the largest count treated as real.
const UNLIMITED_LAPS_SENTINEL: f64 = 32767.0;
const IMPLAUSIBLE_LAPS_CEILING: f64 = 10_000.0;
const GRAVITY: f64 = 9.80665;
const BRAKE_ZONES: usize = 20;
const GAP_WINDOW_MS: u64 = 8_000;
const GAP_TREND_SPAN_MS: u64 = 3_000;
/// Preallocated gap-history slots: 8 s at the SDK's 240 Hz ceiling, plus one.
const GAP_HISTORY_SLOTS: usize = 2_048;
const SAFE_GLANCE_URGENT: u8 = Priority::Strategy as u8;

// Cue ids (`live-channels.json` `cues`).
const CUE_BLACK_FLAG: u8 = 1;
const CUE_TOW: u8 = 2;
const CUE_MANDATORY_REPAIR: u8 = 3;
const CUE_FUEL_CRITICAL: u8 = 4;
const CUE_FUEL_LIMIT: u8 = 5;
const CUE_FLAGS_UNKNOWN: u8 = 6;
const CUE_CAUTION: u8 = 7;
const CUE_PIT_ROAD: u8 = 8;
const CUE_GAP_CLOSING: u8 = 9;
const CUE_PACE_OUTSIDE: u8 = 10;
const CUE_BRAKING_OVERSHOOT: u8 = 11;
const CUE_BUILDING_PACE: u8 = 12;
const CUE_NO_ACTION: u8 = 13;
const CUE_WAITING: u8 = 14;
const CUE_PAUSED: u8 = 15;

/// Stand-in for a table lookup that cannot fail (every id above is in `CUES`; a test checks).
const NO_CUE: CueState = CueState {
    id: CUE_NO_ACTION,
    priority: Priority::Information,
    evidence: coach_wire::live_channels::Evidence::Derived,
    arg_count: 0,
    args: [0.0; 4],
};

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

/// A bounded queue of at most eight values (`EnqueueBounded(queue, value, 8)`).
#[derive(Debug, Clone, Copy)]
struct Ring8 {
    values: [f64; 8],
    len: usize,
    head: usize,
}

impl Ring8 {
    const fn new() -> Self {
        Ring8 {
            values: [0.0; 8],
            len: 0,
            head: 0,
        }
    }

    fn clear(&mut self) {
        self.len = 0;
        self.head = 0;
    }

    fn push(&mut self, value: f64) {
        if self.len == 8 {
            self.values[self.head] = value;
            self.head = (self.head + 1) % 8;
        } else {
            self.values[(self.head + self.len) % 8] = value;
            self.len += 1;
        }
    }

    /// Insertion-ordered copy, oldest first.
    fn ordered(&self, out: &mut [f64; 8]) -> usize {
        for (slot, target) in out.iter_mut().enumerate().take(self.len) {
            *target = self.values[(self.head + slot) % 8];
        }
        self.len
    }

    /// `Median`: the middle value, or the mean of the two middle values for an even count.
    fn median(&self) -> Option<f64> {
        if self.len == 0 {
            return None;
        }
        let mut sorted = [0.0; 8];
        let len = self.ordered(&mut sorted);
        let sorted = &mut sorted[..len];
        sorted.sort_by(f64::total_cmp);
        Some(if len % 2 == 1 {
            sorted[len / 2]
        } else {
            (sorted[len / 2 - 1] + sorted[len / 2]) / 2.0
        })
    }

    /// Median of `|value - center|`.
    fn median_deviation(&self, center: f64) -> Option<f64> {
        if self.len == 0 {
            return None;
        }
        let mut deviations = [0.0; 8];
        let len = self.ordered(&mut deviations);
        for value in &mut deviations[..len] {
            *value = (*value - center).abs();
        }
        let sorted = &mut deviations[..len];
        sorted.sort_by(f64::total_cmp);
        Some(if len % 2 == 1 {
            sorted[len / 2]
        } else {
            (sorted[len / 2 - 1] + sorted[len / 2]) / 2.0
        })
    }
}

/// `NormalizeSessionPhase`: qualifying, race, practice, warmup, else the lowercase alphanumerics.
#[derive(Debug, Clone, PartialEq, Eq)]
enum Phase {
    Qualifying,
    Race,
    Practice,
    Warmup,
    Other(String),
}

fn normalize_phase(value: &str) -> Option<Phase> {
    let normalized: String = value
        .chars()
        .filter(|c| c.is_alphanumeric())
        .flat_map(char::to_lowercase)
        .collect();
    if normalized.is_empty() {
        None
    } else if normalized.contains("qual") {
        Some(Phase::Qualifying)
    } else if normalized.contains("race") {
        Some(Phase::Race)
    } else if normalized.contains("practice") {
        Some(Phase::Practice)
    } else if normalized.contains("warmup") {
        Some(Phase::Warmup)
    } else {
        Some(Phase::Other(normalized))
    }
}

/// The `LiveTelemetrySample` of C#: the raw sample after the reader's normalisations.
struct Norm {
    ts: u64,
    flags_known: bool,
    flag_state: coach_wire::live_channels::FlagState,
    caution: bool,
    black: bool,
    repair: bool,
    towing: bool,
    lap: Option<i32>,
    laps_remaining: Option<i32>,
    overall_position: Option<i32>,
    class_position: Option<i32>,
    gap_leader: Option<f64>,
    gap_ahead: Option<f64>,
    gap_behind: Option<f64>,
    last_lap: Option<f64>,
    leader_last_lap: Option<f64>,
    fuel: Option<f64>,
    fuel_pct: Option<f64>,
    track_temp: Option<f64>,
    on_pit_road: bool,
    mandatory_repair: Option<f64>,
    optional_repair: Option<f64>,
    wheel: Option<f64>,
    ratio: Option<f64>,
    brake: Option<f64>,
    lateral_g: Option<f64>,
    lap_dist: Option<f64>,
}

/// `LapsRemainingOrUnknown`.
fn laps_remaining_or_unknown(value: Option<f64>) -> Option<i32> {
    let laps = value.filter(|v| v.is_finite() && *v >= 0.0)?;
    if laps >= UNLIMITED_LAPS_SENTINEL || laps >= IMPLAUSIBLE_LAPS_CEILING {
        return None;
    }
    Some((laps.ceil() as i32).max(0))
}

fn positive_i32(value: Option<i32>) -> Option<i32> {
    value.filter(|v| *v > 0)
}

fn positive_f64(value: Option<f64>) -> Option<f64> {
    value.filter(|v| *v > 0.0 && v.is_finite())
}

fn non_negative(value: Option<f64>) -> Option<f64> {
    value.filter(|v| *v >= 0.0 && v.is_finite())
}

fn percentage(value: Option<f64>) -> Option<f64> {
    value.filter(|v| (0.0..=1.0).contains(v) && v.is_finite())
}

fn array_value<T: Copy>(values: &[T], index: Option<i32>) -> Option<T> {
    let index = usize::try_from(index?).ok()?;
    values.get(index).copied()
}

/// `FindByPosition`: the first index holding `value`.
fn find_by_position(positions: &[i32], value: i32) -> Option<usize> {
    positions.iter().position(|p| *p == value)
}

/// `SameLapGap`: `f2` differences are float arithmetic, kept when finite and not negative.
fn same_lap_gap(
    f2: &[f32],
    laps: &[i32],
    player: Option<usize>,
    target: Option<usize>,
    to_target: bool,
) -> Option<f64> {
    let (player, target) = (player?, target?);
    if player >= f2.len()
        || target >= f2.len()
        || player >= laps.len()
        || target >= laps.len()
        || laps[player] != laps[target]
    {
        return None;
    }
    let value: f32 = if to_target {
        f2[player] - f2[target]
    } else {
        f2[target] - f2[player]
    };
    (value.is_finite() && value >= 0.0).then_some(f64::from(value))
}

fn normalise(sample: &Sample, ratio: Option<f64>, one_lap_to_green_is_caution: bool) -> Norm {
    let player_index = sample.player_car_idx;
    let player = player_index.and_then(|i| usize::try_from(i).ok());
    let positions = &sample.car_idx_position;
    let class_positions = &sample.car_idx_class_position;
    let overall = sample
        .player_car_position
        .or_else(|| array_value(positions, player_index));
    let class_position = sample
        .player_car_class_position
        .or_else(|| array_value(class_positions, player_index));
    let leader = find_by_position(positions, 1);
    // C#: ahead only when the class position is > 1; behind for any present class position,
    // unfiltered (a class position of 0 therefore finds the class leader).
    let ahead = class_position
        .filter(|c| *c > 1)
        .and_then(|c| find_by_position(class_positions, c - 1));
    let behind = class_position.and_then(|c| find_by_position(class_positions, c.wrapping_add(1)));
    let f2 = &sample.car_idx_f2_time;
    let laps = &sample.car_idx_lap;
    let gap_leader = if overall == Some(1) {
        Some(0.0)
    } else {
        same_lap_gap(f2, laps, player, leader, true)
    };
    let flags = sample.session_flags;
    let state = truth::racing_state_in(flags, one_lap_to_green_is_caution);
    let leader_last_lap = leader
        .and_then(|index| sample.car_idx_last_lap_time.get(index))
        .map(|v| f64::from(*v));
    Norm {
        ts: sample.captured_unix_ms,
        flags_known: flags.is_some(),
        flag_state: state,
        caution: state == coach_wire::live_channels::FlagState::Caution,
        black: matches!(
            state,
            coach_wire::live_channels::FlagState::Black
                | coach_wire::live_channels::FlagState::Disqualified
        ),
        repair: truth::repair_required(flags),
        towing: sample.player_car_tow_time.unwrap_or(0.0) > 0.0,
        lap: sample.lap.map(|raw| raw.wrapping_add(1).max(1)),
        laps_remaining: laps_remaining_or_unknown(
            sample.session_laps_remain_ex.or(sample.session_laps_remain),
        ),
        overall_position: positive_i32(overall),
        class_position: positive_i32(class_position),
        gap_leader: non_negative(gap_leader),
        gap_ahead: non_negative(same_lap_gap(f2, laps, player, ahead, true)),
        gap_behind: non_negative(same_lap_gap(f2, laps, player, behind, false)),
        last_lap: positive_f64(sample.lap_last_lap_time),
        leader_last_lap: positive_f64(leader_last_lap),
        fuel: non_negative(sample.fuel_level),
        fuel_pct: percentage(sample.fuel_level_pct),
        track_temp: sample.track_temp_crew.or(sample.track_temp),
        on_pit_road: sample.on_pit_road.unwrap_or(false),
        mandatory_repair: non_negative(sample.pit_repair_left),
        optional_repair: non_negative(sample.pit_opt_repair_left),
        wheel: sample.steering_wheel_angle,
        ratio,
        brake: sample.brake,
        lateral_g: sample.lat_accel.map(|v| v / GRAVITY),
        lap_dist: truth::normalize_lap_distance(sample.lap_dist_pct),
    }
}

/// The identity values `ObserveSessionBoundary` remembers between connected samples.
#[derive(Debug, Default)]
struct BoundaryMemory {
    timestamp: Option<u64>,
    lap: Option<i32>,
    session_unique_id: Option<i64>,
    subsession_id: Option<i64>,
    session_num: Option<i32>,
    phase: Option<Phase>,
}

pub struct Engine {
    config: EngineConfig,
    paused: bool,

    // Session boundary (service) state.
    epoch: u32,
    prev_connected: bool,
    boundary: BoundaryMemory,
    phase_source: Option<String>,
    phase_value: Option<Phase>,

    // Engine rules.
    gap_ahead: VecDeque<(u64, f64)>,
    gap_behind: VecDeque<(u64, f64)>,
    clean_lap_times: Ring8,
    fuel_per_lap: Ring8,
    last_lap: Option<i32>,
    fuel_at_lap_start: Option<f64>,
    green_laps: u32,
    caution_laps: u32,
    displayed_cue: Option<CueState>,
    persistent_cue: Option<CueState>,
    brake_baseline: [Ring8; BRAKE_ZONES],
    brake_streaks: [u32; BRAKE_ZONES],
    lap_peaks: [f64; BRAKE_ZONES],
    lap_peak_order: [u8; BRAKE_ZONES],
    lap_peak_count: usize,
    lap_fully_observed: bool,
    lap_session_confounded: bool,
    lap_input_confounded: bool,
    lap_under_caution: bool,
    lap_was_on_pit: bool,
    was_on_pit: bool,
    was_connected: bool,
}

impl Engine {
    pub fn new(config: EngineConfig) -> Engine {
        Engine {
            config,
            paused: false,
            epoch: 0,
            prev_connected: false,
            boundary: BoundaryMemory::default(),
            phase_source: None,
            phase_value: None,
            gap_ahead: VecDeque::with_capacity(GAP_HISTORY_SLOTS),
            gap_behind: VecDeque::with_capacity(GAP_HISTORY_SLOTS),
            clean_lap_times: Ring8::new(),
            fuel_per_lap: Ring8::new(),
            last_lap: None,
            fuel_at_lap_start: None,
            green_laps: 0,
            caution_laps: 0,
            displayed_cue: None,
            persistent_cue: None,
            brake_baseline: [Ring8::new(); BRAKE_ZONES],
            brake_streaks: [0; BRAKE_ZONES],
            lap_peaks: [0.0; BRAKE_ZONES],
            lap_peak_order: [0; BRAKE_ZONES],
            lap_peak_count: 0,
            lap_fully_observed: false,
            lap_session_confounded: false,
            lap_input_confounded: false,
            lap_under_caution: false,
            lap_was_on_pit: false,
            was_on_pit: false,
            was_connected: false,
        }
    }

    pub fn set_safe_glance(&mut self, enabled: bool) {
        self.config.safe_glance = enabled;
    }

    /// Tray "Pause coaching cue"; not persisted.
    pub fn set_paused(&mut self, paused: bool) {
        self.paused = paused;
    }

    /// One call per new SDK tick (and once per second while disconnected). Applies the epoch rule,
    /// the reader normalisations, the engine rules and cues, and the safe-glance hold. Never
    /// blocks, never allocates after warm-up.
    pub fn on_tick(&mut self, sample: &Sample) -> LiveState {
        if !sample.connected {
            return self.disconnected(sample);
        }
        let ratio = sample.session.as_deref().and_then(|ctx| ctx.steering_ratio);
        // The session phase gates the one-lap-to-green bit, so it is resolved before the flags.
        self.refresh_phase(sample);
        let norm = normalise(sample, ratio, self.one_lap_to_green_is_caution());

        let changed = self.observe_boundary(sample, &norm);
        if changed {
            self.epoch = self.epoch.wrapping_add(1);
            self.reset_state();
            self.was_connected = false;
        }
        self.prev_connected = true;

        let out = self.update(&norm);
        self.build_state(sample, &norm, &out, changed)
    }

    // ---- session boundary (LiveTelemetryService.ObserveSessionBoundary) ----

    fn disconnected(&mut self, sample: &Sample) -> LiveState {
        // MarkDisconnectedBoundary: forget the identity of the last connected stream. The epoch
        // does not change here; the next connected sample starts a new one (contract 4.4).
        self.prev_connected = false;
        self.boundary = BoundaryMemory::default();
        // LiveTelemetryEngine.Update, disconnected branch.
        if self.was_connected {
            self.reset_state();
        }
        self.was_connected = false;
        let mut state = LiveState::blank(sample.captured_unix_ms, sample.tick, sample.tick_rate);
        state.session_epoch = self.epoch;
        state.coaching_paused = self.paused;
        state.cue = CueState::from_table(CUE_WAITING, &[]);
        state
    }

    /// The normalised session phase of the sample's session number; recomputed (and allocated)
    /// only when the session type text changes.
    fn refresh_phase(&mut self, sample: &Sample) {
        let source: Option<&str> = sample
            .session
            .as_deref()
            .zip(sample.session_num)
            .and_then(|(ctx, num)| ctx.session_type(i64::from(num)));
        if self.phase_source.as_deref() != source {
            self.phase_value = source.and_then(normalize_phase);
            self.phase_source = source.map(str::to_string);
        }
    }

    /// One lap to green (`0x200`) is a caution bit in a Race and when the session type is not
    /// known; in Offline Testing, Practice and Qualifying iRacing holds it with no caution
    /// (`truth::racing_state_in`).
    fn one_lap_to_green_is_caution(&self) -> bool {
        matches!(self.phase_value, None | Some(Phase::Race))
    }

    fn observe_boundary(&mut self, sample: &Sample, norm: &Norm) -> bool {
        let phase = self.phase_value.as_ref();
        let subsession = sample.session.as_deref().and_then(|ctx| ctx.subsession_id);
        let memory = &mut self.boundary;
        let changed = !self.prev_connected
            || memory.timestamp.is_some_and(|last| norm.ts < last)
            || matches!((memory.lap, norm.lap), (Some(last), Some(now)) if now < last)
            || identity_changed(memory.session_unique_id, sample.session_unique_id)
            || identity_changed(memory.subsession_id, subsession)
            || identity_changed(memory.session_num, sample.session_num)
            || matches!((&memory.phase, phase), (Some(last), Some(now)) if last != now);
        memory.timestamp = Some(norm.ts);
        if norm.lap.is_some() {
            memory.lap = norm.lap;
        }
        if sample.session_unique_id.is_some() {
            memory.session_unique_id = sample.session_unique_id;
        }
        if subsession.is_some() {
            memory.subsession_id = subsession;
        }
        if sample.session_num.is_some() {
            memory.session_num = sample.session_num;
        }
        if let Some(phase) = phase
            && memory.phase.as_ref() != Some(phase)
        {
            memory.phase = Some(phase.clone());
        }
        changed
    }

    // ---- engine rules (LiveTelemetryEngine) ----

    fn reset_state(&mut self) {
        self.gap_ahead.clear();
        self.gap_behind.clear();
        self.clean_lap_times.clear();
        self.fuel_per_lap.clear();
        for ring in &mut self.brake_baseline {
            ring.clear();
        }
        self.brake_streaks = [0; BRAKE_ZONES];
        self.lap_peak_count = 0;
        self.last_lap = None;
        self.fuel_at_lap_start = None;
        self.green_laps = 0;
        self.caution_laps = 0;
        self.displayed_cue = None;
        self.persistent_cue = None;
        self.lap_fully_observed = false;
        self.lap_session_confounded = false;
        self.lap_input_confounded = false;
        self.lap_under_caution = false;
        self.lap_was_on_pit = false;
        self.was_on_pit = false;
    }

    fn update(&mut self, n: &Norm) -> UpdateOut {
        self.was_connected = true;

        let lap_advanced = matches!((self.last_lap, n.lap), (Some(last), Some(now)) if now > last);
        let sequential_lap = lap_advanced
            && matches!((self.last_lap, n.lap), (Some(last), Some(now)) if last.checked_add(1) == Some(now));
        if lap_advanced {
            self.complete_observed_lap(n, sequential_lap);
            self.begin_observed_lap(n, sequential_lap);
        } else if self.last_lap.is_none() {
            self.begin_observed_lap(n, false);
        } else if matches!((self.last_lap, n.lap), (Some(last), Some(now)) if now < last) {
            self.reset_state();
            self.was_connected = true;
            self.begin_observed_lap(n, false);
        }
        self.observe_current_lap(n);
        if n.lap.is_some() {
            self.last_lap = n.lap;
        }

        let ahead = build_gap(&mut self.gap_ahead, n.gap_ahead, n);
        let behind = build_gap(&mut self.gap_behind, n.gap_behind, n);
        let run_phase = if self.green_laps < 5 {
            RunPhase::Early
        } else if self.green_laps < 20 {
            RunPhase::Middle
        } else {
            RunPhase::Late
        };
        let pace = self.pace_target();
        let average_fuel = (self.fuel_per_lap.len >= 2)
            .then(|| self.fuel_per_lap.median())
            .flatten();
        let hard_limit = match (average_fuel, n.fuel) {
            (Some(average), Some(fuel)) => Some(
                ((fuel - (0.5_f64).min(average * 0.5)) / average)
                    .floor()
                    .max(0.0) as i32,
            ),
            _ => None,
        };
        let fuel_laps = match (average_fuel, n.fuel) {
            (Some(average), Some(fuel)) => Some((fuel / average).max(0.0)),
            _ => None,
        };

        let measured_straight = n.brake.is_some_and(|b| b.is_finite() && b < 0.05)
            && near_center(n.wheel, n.ratio)
            && n.lateral_g.is_some_and(|g| g.is_finite() && g.abs() < 0.35);
        let glance_opportunity = n.caution || n.on_pit_road || lap_advanced || measured_straight;

        let mut candidate = self.build_cue(n, ahead.trend, ahead.seconds, hard_limit, pace.max);
        let urgent = (candidate.priority as u8) >= SAFE_GLANCE_URGENT;
        if self.paused {
            candidate = CueState::from_table(CUE_PAUSED, &[]).unwrap_or(candidate);
        } else if self.config.safe_glance
            && !glance_opportunity
            && !urgent
            && self.displayed_cue.is_some()
        {
            candidate = self.displayed_cue.unwrap_or(candidate);
        } else {
            self.displayed_cue = Some(candidate);
        }

        UpdateOut {
            cue: candidate,
            ahead,
            behind,
            run_phase,
            pace,
            hard_limit,
            fuel_laps,
        }
    }

    fn pace_target(&self) -> PaceTarget {
        if self.clean_lap_times.len < 3 {
            return PaceTarget {
                min: None,
                max: None,
            };
        }
        let baseline = self.clean_lap_times.median().unwrap_or(0.0);
        let half_band = (0.15_f64).max(baseline * 0.005);
        PaceTarget {
            min: Some(baseline - half_band),
            max: Some(baseline + half_band),
        }
    }

    fn build_cue(
        &self,
        n: &Norm,
        ahead_trend: GapTrend,
        ahead_seconds: Option<f64>,
        hard_limit: Option<i32>,
        pace_max: Option<f64>,
    ) -> CueState {
        let table = |id: u8, args: &[f32]| CueState::from_table(id, args).unwrap_or(NO_CUE);
        if n.black {
            return table(CUE_BLACK_FLAG, &[]);
        }
        if n.towing {
            return table(CUE_TOW, &[]);
        }
        if let Some(seconds) = n.mandatory_repair.filter(|s| *s > 0.0) {
            return table(CUE_MANDATORY_REPAIR, &[seconds as f32]);
        }
        if let Some(limit) = hard_limit {
            if limit <= 1 {
                return table(CUE_FUEL_CRITICAL, &[]);
            }
            if limit <= 3 {
                return table(CUE_FUEL_LIMIT, &[limit as f32]);
            }
        }
        if !n.flags_known {
            return table(CUE_FLAGS_UNKNOWN, &[]);
        }
        if n.caution {
            return table(CUE_CAUTION, &[]);
        }
        if n.on_pit_road {
            return table(CUE_PIT_ROAD, &[]);
        }
        if let Some(seconds) = ahead_seconds.filter(|s| *s < 1.0)
            && ahead_trend == GapTrend::Closing
        {
            return table(CUE_GAP_CLOSING, &[seconds as f32]);
        }
        if let (Some(max), Some(last)) = (pace_max, n.last_lap)
            && last > max + 0.3
        {
            return table(CUE_PACE_OUTSIDE, &[]);
        }
        if let Some(cue) = self.persistent_cue {
            return cue;
        }
        if pace_max.is_none() {
            return table(CUE_BUILDING_PACE, &[]);
        }
        table(CUE_NO_ACTION, &[])
    }

    fn begin_observed_lap(&mut self, n: &Norm, fully_observed: bool) {
        self.lap_fully_observed = fully_observed;
        self.lap_session_confounded = false;
        self.lap_input_confounded = false;
        self.lap_under_caution = false;
        self.lap_was_on_pit = false;
        self.lap_peak_count = 0;
        self.fuel_at_lap_start = if fully_observed { n.fuel } else { None };
    }

    fn observe_current_lap(&mut self, n: &Norm) {
        if n.on_pit_road && !self.was_on_pit {
            self.green_laps = 0;
            self.caution_laps = 0;
        }
        self.was_on_pit = n.on_pit_road;

        let session_confounded =
            !n.flags_known || n.caution || n.on_pit_road || n.repair || n.towing || n.black;
        self.lap_session_confounded |= session_confounded;
        self.lap_input_confounded |= session_confounded
            || n.gap_ahead.is_some_and(|g| g < 1.5)
            || n.gap_behind.is_some_and(|g| g < 0.75);
        self.lap_under_caution |= n.caution;
        self.lap_was_on_pit |= n.on_pit_road;

        // `is not >= 0 or >= 1`: a lap distance must be in [0, 1); a brake must be >= 0.
        let Some(distance) = n.lap_dist.filter(|d| (0.0..1.0).contains(d)) else {
            return;
        };
        let Some(brake) = n.brake.filter(|b| *b >= 0.0) else {
            return;
        };
        let zone = ((distance * 20.0) as usize).min(BRAKE_ZONES - 1);
        let seen = self.lap_peak_order[..self.lap_peak_count].contains(&(zone as u8));
        if !seen {
            self.lap_peak_order[self.lap_peak_count] = zone as u8;
            self.lap_peak_count += 1;
            self.lap_peaks[zone] = brake;
        } else if brake > self.lap_peaks[zone] {
            self.lap_peaks[zone] = brake;
        }
    }

    fn complete_observed_lap(&mut self, n: &Norm, sequential_lap: bool) {
        let fully_observed = sequential_lap && self.lap_fully_observed;
        let boundary_confounded =
            !n.flags_known || n.caution || n.on_pit_road || n.repair || n.towing || n.black;
        let clean_session_lap =
            fully_observed && !self.lap_session_confounded && !boundary_confounded;
        let completed_under_caution = self.lap_under_caution || n.caution;
        let completed_on_pit = self.lap_was_on_pit || n.on_pit_road;
        if clean_session_lap && !completed_on_pit {
            self.green_laps += 1;
        } else if fully_observed && !completed_on_pit && completed_under_caution {
            self.caution_laps += 1;
        }

        if clean_session_lap
            && let Some(last) = n
                .last_lap
                .filter(|l| *l > 0.0 && *l < 3600.0 && l.is_finite())
        {
            self.clean_lap_times.push(last);
        }

        if clean_session_lap
            && let Some(start) = self
                .fuel_at_lap_start
                .filter(|s| *s >= 0.0 && s.is_finite())
            && let Some(end) = n.fuel.filter(|e| *e >= 0.0 && e.is_finite())
        {
            let used = start - end;
            if used > 0.05 && used < 20.0 {
                self.fuel_per_lap.push(used);
            }
        }

        let eligible = fully_observed && !self.lap_input_confounded && !boundary_confounded;
        self.complete_driver_input_lap(eligible);
    }

    fn complete_driver_input_lap(&mut self, eligible: bool) {
        if !eligible || self.lap_peak_count == 0 {
            self.lap_peak_count = 0;
            return;
        }

        // Medians of the baselines that hold at least three peaks, taken before this lap changes any.
        let mut medians = [None::<f64>; BRAKE_ZONES];
        let mut strongest = 0.0_f64;
        let mut any = false;
        for (zone, ring) in self.brake_baseline.iter().enumerate() {
            if ring.len >= 3 {
                let median = ring.median().unwrap_or(0.0);
                medians[zone] = Some(median);
                strongest = if any { strongest.max(median) } else { median };
                any = true;
            }
        }
        let strongest_baseline = if any { strongest } else { 0.0 };

        let mut new_cue: Option<CueState> = None;
        let mut cue_severity = 0.0_f64;
        for slot in 0..self.lap_peak_count {
            let zone = usize::from(self.lap_peak_order[slot]);
            let peak = self.lap_peaks[zone];
            let baseline = &mut self.brake_baseline[zone];

            if baseline.len >= 3
                && let Some(median) = medians[zone]
                && strongest_baseline > 0.0
                && median >= strongest_baseline * 0.35
            {
                let repeatability_band =
                    (median * 0.08).max(baseline.median_deviation(median).unwrap_or(0.0) * 3.0);
                let excess = peak - median;
                if excess > repeatability_band {
                    let streak = self.brake_streaks[zone] + 1;
                    self.brake_streaks[zone] = streak;
                    if streak >= 3 && excess / median > cue_severity {
                        cue_severity = excess / median;
                        new_cue = CueState::from_table(
                            CUE_BRAKING_OVERSHOOT,
                            &[(zone * 5) as f32, (excess / median) as f32, streak as f32],
                        );
                    }
                    continue;
                }
                self.brake_streaks[zone] = 0;
            }

            baseline.push(peak);
        }
        self.persistent_cue = new_cue;
        self.lap_peak_count = 0;
    }

    // ---- output ----

    fn build_state(
        &self,
        sample: &Sample,
        n: &Norm,
        out: &UpdateOut,
        first_of_epoch: bool,
    ) -> LiveState {
        let road = road_wheel_radians(n.wheel, n.ratio);
        let flag_state = n.flag_state;
        let incidents = sample
            .player_car_my_incident_count
            .or(sample.player_incidents);
        let pace_delta = match (n.last_lap, n.leader_last_lap) {
            (Some(last), Some(leader)) => Some(last - leader),
            _ => None,
        };
        LiveState {
            connected: true,
            on_track: sample.is_on_track == Some(true),
            in_garage: sample.is_in_garage == Some(true),
            first_of_epoch,
            coaching_paused: self.paused,
            session_epoch: self.epoch,
            captured_unix_ms: sample.captured_unix_ms,
            session_time: sample.session_time.filter(|t| t.is_finite()),
            tick: sample.tick,
            tick_rate: sample.tick_rate,
            speed: f32_of(sample.speed),
            throttle: f32_of(sample.throttle),
            brake: f32_of(sample.brake),
            steering_wheel_angle: f32_of(n.wheel),
            steering_road_wheel: f32_of(road),
            steering_ratio: f32_of(n.ratio),
            gear: sample.gear.and_then(|g| i8::try_from(g).ok()),
            rpm: f32_of(sample.rpm),
            yaw_rate: f32_of(sample.yaw_rate),
            lateral_accel: f32_of(sample.lat_accel),
            longitudinal_accel: f32_of(sample.long_accel),
            lap_dist_pct: f32_of(n.lap_dist),
            latitude: sample.lat.filter(|v| v.is_finite()),
            longitude: sample.lon.filter(|v| v.is_finite()),
            lap: n.lap.and_then(|l| u16::try_from(l).ok()),
            laps_remaining: n.laps_remaining.and_then(|l| u16::try_from(l).ok()),
            position: n.overall_position.and_then(|p| u8::try_from(p).ok()),
            class_position: n.class_position.and_then(|p| u8::try_from(p).ok()),
            flag_state: Some(flag_state),
            session_flags: sample.session_flags,
            abs_active: sample.brake_abs_active,
            brake_bias: f32_of(sample.dc_brake_bias),
            fuel: f32_of(n.fuel),
            fuel_pct: f32_of(n.fuel_pct),
            fuel_laps_remaining: f32_of(out.fuel_laps),
            fuel_hard_limit_laps: out.hard_limit.and_then(|l| u16::try_from(l).ok()),
            last_lap: f32_of(n.last_lap),
            leader_last_lap: f32_of(n.leader_last_lap),
            ahead_gap: f32_of(out.ahead.seconds),
            ahead_gap_trend: Some(out.ahead.trend),
            behind_gap: f32_of(out.behind.seconds),
            behind_gap_trend: Some(out.behind.trend),
            leader_gap: f32_of(n.gap_leader),
            pace_band_min: f32_of(out.pace.min),
            pace_band_max: f32_of(out.pace.max),
            run_phase: Some(out.run_phase),
            green_run_laps: u16::try_from(self.green_laps).ok(),
            mandatory_repair: f32_of(n.mandatory_repair),
            optional_repair: f32_of(n.optional_repair),
            on_pit_road: Some(n.on_pit_road),
            tow_active: sample.player_car_tow_time.map(|_| n.towing),
            track_temperature: f32_of(n.track_temp),
            air_temperature: f32_of(sample.air_temp),
            incidents: incidents.and_then(|i| u16::try_from(i).ok()),
            pace_delta_to_leader: f32_of(pace_delta),
            cue: Some(out.cue),
        }
    }
}

fn identity_changed<T: PartialEq + Copy>(previous: Option<T>, current: Option<T>) -> bool {
    matches!((previous, current), (Some(a), Some(b)) if a != b)
}

/// A finite double narrowed to a finite float.
fn f32_of(value: Option<f64>) -> Option<f32> {
    value
        .filter(|v| v.is_finite())
        .map(|v| v as f32)
        .filter(|v| v.is_finite())
}

struct GapState {
    seconds: Option<f64>,
    trend: GapTrend,
}

struct PaceTarget {
    min: Option<f64>,
    max: Option<f64>,
}

struct UpdateOut {
    cue: CueState,
    ahead: GapState,
    behind: GapState,
    run_phase: RunPhase,
    pace: PaceTarget,
    hard_limit: Option<i32>,
    fuel_laps: Option<f64>,
}

/// `BuildGap`: unavailable or stale while the value is missing or the race state confounds it;
/// otherwise an 8 s history whose first entry, at least 3 s old, sets the trend.
fn build_gap(history: &mut VecDeque<(u64, f64)>, value: Option<f64>, n: &Norm) -> GapState {
    let usable = value.filter(|v| *v >= 0.0);
    let Some(seconds) = usable.filter(|_| n.flags_known && !n.caution && !n.on_pit_road) else {
        return GapState {
            seconds: value,
            trend: if value.is_some() {
                GapTrend::Stale
            } else {
                GapTrend::Unavailable
            },
        };
    };
    history.push_back((n.ts, seconds));
    while let Some(&(at, _)) = history.front() {
        if n.ts.saturating_sub(at) > GAP_WINDOW_MS {
            history.pop_front();
        } else {
            break;
        }
    }
    let mut trend = GapTrend::Stable;
    if let Some(&(at, first)) = history.front()
        && history.len() >= 2
        && n.ts.saturating_sub(at) >= GAP_TREND_SPAN_MS
    {
        let delta = seconds - first;
        trend = if delta < -0.15 {
            GapTrend::Closing
        } else if delta > 0.15 {
            GapTrend::Growing
        } else {
            GapTrend::Stable
        };
    }
    GapState {
        seconds: Some(seconds),
        trend,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use coach_wire::live_channels::CUES;

    #[test]
    fn every_cue_id_the_engine_uses_is_in_the_table() {
        for id in [
            CUE_BLACK_FLAG,
            CUE_TOW,
            CUE_MANDATORY_REPAIR,
            CUE_FUEL_CRITICAL,
            CUE_FUEL_LIMIT,
            CUE_FLAGS_UNKNOWN,
            CUE_CAUTION,
            CUE_PIT_ROAD,
            CUE_GAP_CLOSING,
            CUE_PACE_OUTSIDE,
            CUE_BRAKING_OVERSHOOT,
            CUE_BUILDING_PACE,
            CUE_NO_ACTION,
            CUE_WAITING,
            CUE_PAUSED,
        ] {
            assert!(CueState::from_table(id, &[]).is_some(), "cue {id}");
            assert!(CUES.iter().any(|spec| spec.id == id));
        }
        assert_eq!(CUES.len(), 15);
    }

    #[test]
    fn ring_keeps_the_newest_eight_and_takes_medians_like_the_csharp_queue() {
        let mut ring = Ring8::new();
        assert_eq!(ring.median(), None);
        for value in [5.0, 1.0, 3.0] {
            ring.push(value);
        }
        assert_eq!(ring.median(), Some(3.0));
        ring.push(4.0);
        assert_eq!(
            ring.median(),
            Some(3.5),
            "an even count averages the two middle values"
        );
        for value in 10..20 {
            ring.push(f64::from(value));
        }
        assert_eq!(ring.len, 8);
        assert_eq!(ring.median(), Some(15.5), "only 12..=19 remain");
        assert_eq!(ring.median_deviation(15.5), Some(2.0));
        ring.clear();
        assert_eq!(ring.median(), None);
    }

    #[test]
    fn phase_normalisation_follows_normalize_session_phase() {
        assert_eq!(normalize_phase("Lone Qualify"), Some(Phase::Qualifying));
        assert_eq!(normalize_phase("Open Qualify"), Some(Phase::Qualifying));
        assert_eq!(normalize_phase("Race"), Some(Phase::Race));
        assert_eq!(normalize_phase("Practice"), Some(Phase::Practice));
        assert_eq!(normalize_phase("Warmup"), Some(Phase::Warmup));
        assert_eq!(
            normalize_phase("Offline Testing"),
            Some(Phase::Other("offlinetesting".into()))
        );
        assert_eq!(normalize_phase(" - "), None);
    }

    #[test]
    fn laps_remaining_rule() {
        assert_eq!(laps_remaining_or_unknown(Some(32767.0)), None);
        assert_eq!(laps_remaining_or_unknown(Some(9999.0)), Some(9999));
        assert_eq!(laps_remaining_or_unknown(Some(0.2)), Some(1));
        assert_eq!(laps_remaining_or_unknown(Some(-0.5)), None);
        assert_eq!(laps_remaining_or_unknown(None), None);
    }
}

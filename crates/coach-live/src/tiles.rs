//! Tile and cue text for the overlay (`LiveTelemetryCatalog.cs`, `LiveTelemetry.cs` cue strings).
//! Text follows the C# formatting byte for byte; see `format.rs` for the number rules.

use coach_wire::live_channels::{
    ArgFormat, CUES, Conversion, FlagState, RunPhase, TILES, TileSpec, conversion,
};

use crate::format;
use crate::state::{CueState, LiveState};
use crate::truth;

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

impl TileText {
    fn missing(state: TileState) -> TileText {
        let primary = match state {
            TileState::Waiting => "Waiting",
            TileState::InsufficientEvidence => "Insufficient evidence",
            TileState::NotMeasured | TileState::Value => "Not measured",
        };
        TileText {
            primary: primary.to_string(),
            secondary: None,
            value: None,
            min: None,
            max: None,
            state,
        }
    }

    fn value(primary: String, value: f64) -> TileText {
        TileText {
            primary,
            secondary: None,
            value: Some(value),
            min: None,
            max: None,
            state: TileState::Value,
        }
    }

    fn with_secondary(mut self, secondary: impl Into<String>) -> TileText {
        self.secondary = Some(secondary.into());
        self
    }

    fn with_bounds(mut self, min: Option<f64>, max: Option<f64>) -> TileText {
        self.min = min;
        self.max = max;
        self
    }
}

/// `LiveTelemetryCatalog.Read` rules (`LiveTelemetryCatalog.cs:57-108, 269-373`). Unit from
/// `TileSpec.units` (default first) converted with `coach_wire::live_channels::conversion`;
/// precision clamped 0..=3; lap times `m:ss.fff` at >= 60 s else `s.fff`. The unit is part of
/// `primary` after a space (C# shows `DisplayValue` and `Unit` side by side), except for the
/// time-valued tiles. `None` for an unknown tile id.
pub fn format_tile(
    tile_id: &str,
    state: &LiveState,
    unit: Option<&str>,
    precision: Option<u8>,
) -> Option<TileText> {
    let spec = TILES.iter().find(|spec| spec.id == tile_id)?;
    if !state.connected {
        return Some(TileText::missing(TileState::Waiting));
    }
    let unit = match unit {
        Some(requested) if spec.units.contains(&requested) => requested,
        _ => spec.units.first().copied().unwrap_or(""),
    };
    let precision = usize::from(precision.unwrap_or(spec.precision).min(3));
    Some(read(spec, state, unit, precision))
}

fn read(spec: &TileSpec, s: &LiveState, unit: &str, precision: usize) -> TileText {
    match spec.id {
        "air-temperature" => number(s.air_temperature, "C", unit, precision),
        "ahead-gap" => number(s.ahead_gap, "s", "s", precision),
        "behind-gap" => number(s.behind_gap, "s", "s", precision),
        "brake" => percent(s.brake, precision),
        "brake-bias" => number(s.brake_bias, "%", "%", precision),
        "class-position" => position(s.class_position),
        "coach-cue" => coach_cue(s),
        "flag" => flag(s),
        "fuel" => fuel(s, unit, precision),
        "fuel-laps" => number(s.fuel_laps_remaining, "lap", "lap", precision),
        "gear" => number(s.gear, "", "", 0),
        "lap" => number(s.lap, "", "", 0),
        "laps-remaining" => number(s.laps_remaining, "lap", "lap", 0),
        "last-lap" => last_lap(s),
        "lateral-acceleration" => number(s.lateral_accel, "m/s2", unit, precision),
        "leader-gap" => leader_gap(s, precision),
        "leader-last-lap" => lap_time_tile(s.leader_last_lap),
        "longitudinal-acceleration" => number(s.longitudinal_accel, "m/s2", unit, precision),
        "mandatory-repair" => number(s.mandatory_repair, "s", "s", 0),
        "on-pit-road" => on_pit_road(s),
        "optional-repair" => number(s.optional_repair, "s", "s", 0),
        "pace-range" => pace_range(s),
        "pit-window" => pit_window(s),
        "position" => position(s.position),
        "rpm" => number(s.rpm, "rpm", "rpm", 0),
        "speed" => number(s.speed, "m/s", unit, precision),
        "steering" => steering(s, unit, precision),
        "steering-wheel-angle" => number(s.steering_wheel_angle, "rad", unit, precision),
        "throttle" => percent(s.throttle, precision),
        "tire-phase" => tire_phase(s),
        "track-temperature" => number(s.track_temperature, "C", unit, precision),
        "yaw-rate" => number(s.yaw_rate, "rad/s", unit, precision),
        _ => TileText::missing(TileState::NotMeasured),
    }
}

/// Converts a value from its channel unit to the display unit; units the table does not relate
/// are left as they are.
fn convert(value: f64, from: &str, to: &str) -> f64 {
    match conversion(from, to) {
        Some(Conversion::Scale(scale)) => value * scale,
        Some(Conversion::ScaleOffset { scale, offset }) => value * scale + offset,
        None => value,
    }
}

/// The unit as C# shows it. Time-valued tiles and unitless tiles show none.
fn unit_label(unit: &str) -> &str {
    match unit {
        "C" => "°C",
        "F" => "°F",
        "m/s2" => "m/s²",
        "lap" => "laps",
        "time" => "",
        other => other,
    }
}

fn with_unit(text: String, unit: &str) -> String {
    let label = unit_label(unit);
    if label.is_empty() {
        text
    } else {
        format!("{text} {label}")
    }
}

/// `Number<T>`: the value converted to the display unit and formatted `F{precision}`.
fn number(value: Option<impl Into<f64>>, from: &str, unit: &str, precision: usize) -> TileText {
    let Some(raw) = value.map(Into::into).filter(|v: &f64| v.is_finite()) else {
        return TileText::missing(TileState::NotMeasured);
    };
    let converted = convert(raw, from, unit);
    TileText::value(
        with_unit(format::fixed(converted, precision), unit),
        converted,
    )
}

/// `Percent`: a 0..1 input shown as 0..100 %, bounds 0 and 100.
fn percent(value: Option<f32>, precision: usize) -> TileText {
    let Some(raw) = value.map(f64::from).filter(|v| v.is_finite()) else {
        return TileText::missing(TileState::NotMeasured);
    };
    let converted = convert(raw, "ratio", "%");
    TileText::value(
        with_unit(format::fixed(converted, precision), "%"),
        converted,
    )
    .with_bounds(Some(0.0), Some(100.0))
}

fn position(value: Option<u8>) -> TileText {
    match value {
        Some(position) if position > 0 => {
            TileText::value(format!("P{position}"), f64::from(position))
        }
        _ => TileText::missing(TileState::NotMeasured),
    }
}

fn lap_time_tile(value: Option<f32>) -> TileText {
    match value.map(f64::from).filter(|v| *v > 0.0 && v.is_finite()) {
        Some(seconds) => TileText::value(format::lap_time(seconds), seconds),
        None => TileText::missing(TileState::NotMeasured),
    }
}

fn last_lap(s: &LiveState) -> TileText {
    let Some(seconds) = s.last_lap.map(f64::from).filter(|v| v.is_finite()) else {
        return TileText::missing(TileState::NotMeasured);
    };
    let secondary = match s
        .pace_delta_to_leader
        .map(f64::from)
        .filter(|v| v.is_finite())
    {
        Some(delta) => format!("{} s vs leader last lap", format::signed(delta, 3)),
        None => "Leader comparison not measured".to_string(),
    };
    TileText::value(format::lap_time(seconds), seconds).with_secondary(secondary)
}

fn leader_gap(s: &LiveState, precision: usize) -> TileText {
    if s.position == Some(1) {
        return TileText::value("Leader".to_string(), 0.0).with_bounds(Some(0.0), None);
    }
    number(s.leader_gap, "s", "s", precision)
}

fn coach_cue(s: &LiveState) -> TileText {
    let Some(cue) = s.cue else {
        return TileText::missing(TileState::NotMeasured);
    };
    let text = TileText::value(cue_text(&cue), f64::from(cue.priority as u8))
        .with_bounds(Some(0.0), Some(100.0));
    match CUES
        .iter()
        .find(|spec| spec.id == cue.id)
        .and_then(|spec| spec.basis)
    {
        Some(basis) => text.with_secondary(basis),
        None => text,
    }
}

/// `LiveTelemetryMetricEncoding.Flag` ordinals: Green 0, Blue 1, White 2, Checkered 3, Yellow 4,
/// Black 5, Red 6, Other 7.
fn flag(s: &LiveState) -> TileText {
    let Some(state) = s.flag_state else {
        return TileText::missing(TileState::NotMeasured);
    };
    let ordinal = match state {
        FlagState::Racing | FlagState::Green => 0.0,
        FlagState::White => 2.0,
        FlagState::Checkered => 3.0,
        FlagState::Caution => 4.0,
        FlagState::Black => 5.0,
        FlagState::Red => 6.0,
        FlagState::Disqualified | FlagState::Unknown => 7.0,
    };
    let secondary = if state == FlagState::Racing {
        "Recorded session state"
    } else {
        "iRacing race status"
    };
    TileText::value(truth::display_flag(state).to_string(), ordinal)
        .with_secondary(secondary)
        .with_bounds(Some(0.0), Some(7.0))
}

fn fuel(s: &LiveState, unit: &str, precision: usize) -> TileText {
    let Some(liters) = s.fuel.map(f64::from).filter(|v| v.is_finite()) else {
        return TileText::missing(TileState::NotMeasured);
    };
    let value = convert(liters, "L", unit);
    let secondary = match s
        .fuel_laps_remaining
        .map(f64::from)
        .filter(|v| v.is_finite())
    {
        Some(laps) => format!("{} estimated laps", format::custom(laps, 1)),
        None => "Lap estimate needs clean fuel-use evidence".to_string(),
    };
    let capacity = s
        .fuel_pct
        .map(f64::from)
        .filter(|pct| *pct > 0.0001)
        .map(|pct| value / pct);
    TileText::value(with_unit(format::fixed(value, precision), unit), value)
        .with_secondary(secondary)
        .with_bounds(Some(0.0), capacity)
}

/// Road-wheel steering, or "Ratio not recorded" when the setup states no ratio; the wheel angle
/// is never shown under this name.
fn steering(s: &LiveState, unit: &str, precision: usize) -> TileText {
    if let Some(road) = s
        .steering_road_wheel
        .map(f64::from)
        .filter(|v| v.is_finite())
    {
        let converted = convert(road, "rad", unit);
        let text = TileText::value(
            with_unit(format::fixed(converted, precision), unit),
            converted,
        );
        return match s.steering_ratio.map(f64::from).filter(|v| v.is_finite()) {
            Some(ratio) => text.with_secondary(format!(
                "{}:1 steering ratio",
                format::custom_optional(ratio, 2)
            )),
            None => text,
        };
    }
    if s.steering_wheel_angle.is_none() {
        return TileText::missing(TileState::NotMeasured);
    }
    TileText {
        primary: "Ratio not recorded".to_string(),
        secondary: None,
        value: None,
        min: None,
        max: None,
        state: TileState::NotMeasured,
    }
}

fn on_pit_road(s: &LiveState) -> TileText {
    let Some(on) = s.on_pit_road else {
        return TileText::missing(TileState::NotMeasured);
    };
    TileText::value(
        (if on { "On pit road" } else { "On track" }).to_string(),
        if on { 1.0 } else { 0.0 },
    )
    .with_secondary("Recorded OnPitRoad state")
    .with_bounds(Some(0.0), Some(1.0))
}

fn phase_text(phase: Option<RunPhase>) -> Option<(&'static str, f64)> {
    match phase? {
        RunPhase::Early => Some(("Early run", 0.0)),
        RunPhase::Middle => Some(("Middle run", 1.0)),
        RunPhase::Late => Some(("Late run", 2.0)),
        RunPhase::Unknown => None,
    }
}

fn pace_range(s: &LiveState) -> TileText {
    let (Some(min), Some(max)) = (
        s.pace_band_min.map(f64::from),
        s.pace_band_max.map(f64::from),
    ) else {
        return TileText::missing(TileState::InsufficientEvidence);
    };
    let midpoint = (min + max) / 2.0;
    let text = TileText::value(
        format!("{}\u{2013}{}", format::lap_time(min), format::lap_time(max)),
        midpoint,
    );
    match phase_text(s.run_phase) {
        Some((phase, _)) => text.with_secondary(phase),
        None => text,
    }
}

fn pit_window(s: &LiveState) -> TileText {
    match s.fuel_hard_limit_laps {
        Some(laps) => TileText::value(with_unit(laps.to_string(), "lap"), f64::from(laps))
            .with_secondary("Fuel hard limit; strategic window not established"),
        None => TileText::missing(TileState::InsufficientEvidence),
    }
}

fn tire_phase(s: &LiveState) -> TileText {
    match phase_text(s.run_phase) {
        Some((phase, ordinal)) => TileText::value(phase.to_string(), ordinal)
            .with_secondary("Observed clean laps since pit road")
            .with_bounds(Some(0.0), Some(2.0)),
        None => TileText::missing(TileState::InsufficientEvidence),
    }
}

/// `LiveTelemetryCatalog.ApplyRange` (`:375-393`): the display range for gauge, bar and trend
/// styles from `text` (min/max, value), the tile's catalog min/max and `trend` (the caller's history
/// for this tile in the same display unit; non-finite entries ignored). `None` when `text.value`
/// is `None` or non-finite, or the tile is unknown.
pub fn display_range(tile_id: &str, text: &TileText, trend: &[f64]) -> Option<(f64, f64)> {
    let spec = TILES.iter().find(|spec| spec.id == tile_id)?;
    let value = text.value.filter(|v| v.is_finite())?;
    let mut observed_min = value;
    let mut observed_max = value;
    for entry in trend.iter().copied().filter(|v| v.is_finite()) {
        observed_min = observed_min.min(entry);
        observed_max = observed_max.max(entry);
    }
    let span = observed_max - observed_min;
    let padding = if span > 0.0001 {
        span * 0.08
    } else {
        (observed_max.abs() * 0.08).max(1.0)
    };
    let mut minimum = text.min.or(spec.min).unwrap_or(observed_min - padding);
    let mut maximum = text.max.or(spec.max).unwrap_or(observed_max + padding);
    if maximum <= minimum {
        let fallback = (value.abs() * 0.08).max(1.0);
        if text.max.is_none() && spec.max.is_none() {
            maximum = minimum + fallback;
        } else if text.min.is_none() && spec.min.is_none() {
            minimum = maximum - fallback;
        }
    }
    Some((minimum, maximum))
}

/// Cue text from `CUES[id].template` and the cue's args (`ceil`, `int`, `fixed2`, `percent0`).
/// An id outside the table gives an empty string.
pub fn cue_text(cue: &CueState) -> String {
    let Some(spec) = CUES.iter().find(|spec| spec.id == cue.id) else {
        return String::new();
    };
    let mut text = spec.template.to_string();
    for (index, arg_format) in spec.args.iter().enumerate() {
        let value = f64::from(cue.args[index]);
        let rendered = match arg_format {
            ArgFormat::Ceil => format::ceiling(value),
            ArgFormat::Int => format::whole(value),
            ArgFormat::Fixed2 => format::custom(value, 2),
            ArgFormat::Percent0 => format::percent0(value),
        };
        text = text.replace(&format!("{{{index}}}"), &rendered);
    }
    text
}

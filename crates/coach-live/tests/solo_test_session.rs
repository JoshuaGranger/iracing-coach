//! A solo Offline Testing session: the coach-cue and "Laps to pit" tiles of the bot-driven G4 run
//! (`docs/08-rewrite/acceptance/g4-performance.md`). iRacing holds `SessionFlags` one-lap-to-green
//! (`0x200`) through the whole session, with startHidden (`0x10000000`) and, in the garage and pits,
//! servicible (`0x40000`); the words below are the ones the real Test Drive recording
//! `test-data/sdk/g3-garage-pit-2026-10-03.sdkfix` carries. Read as a caution, that bit turned every
//! lap into a caution lap, so the cue said "Caution" for the whole run and no lap was clean enough
//! to give a fuel-use sample.

use std::sync::Arc;

use coach_live::{Engine, EngineConfig, LiveState, Sample, SessionContext};
use coach_wire::live_channels::FlagState;

mod common;

const TEST_FLAGS: u32 = 0x1000_0200;
const TEST_FLAGS_SERVICIBLE: u32 = 0x1004_0200;
const CUE_CAUTION: u8 = 7;
const CUE_PIT_ROAD: u8 = 8;
const TICKS_PER_LAP: usize = 8;
const FUEL_PER_LAP: f64 = 0.45;
const TANK: f64 = 70.0;

fn context(session_type: &str) -> Arc<SessionContext> {
    Arc::new(SessionContext {
        session_types: vec![(0, session_type.to_string())],
        ..SessionContext::default()
    })
}

struct Run {
    engine: Engine,
    ms: u64,
}

impl Run {
    fn new() -> Run {
        Run {
            engine: Engine::new(EngineConfig::default()),
            ms: 1_700_000_000_000,
        }
    }

    /// One tick of a car on its `raw_lap` (0-based, as the SDK counts), `tick` of eight round it.
    fn step(
        &mut self,
        ctx: Option<&Arc<SessionContext>>,
        raw_lap: i32,
        tick: usize,
        fuel: f64,
        flags: u32,
        on_pit_road: bool,
    ) -> LiveState {
        self.ms += 3_000;
        let sample = Sample {
            captured_unix_ms: self.ms,
            connected: true,
            tick: (self.ms / 16) as u32,
            tick_rate: 60,
            session: ctx.cloned(),
            session_num: Some(0),
            session_flags: Some(flags),
            lap: Some(raw_lap),
            lap_dist_pct: Some(tick as f64 / TICKS_PER_LAP as f64),
            lap_last_lap_time: (raw_lap > 0).then_some(23.6),
            fuel_level: Some(fuel),
            fuel_level_pct: Some(fuel / TANK),
            on_pit_road: Some(on_pit_road),
            ..Sample::default()
        };
        self.engine.on_tick(&sample)
    }
}

/// Fuel at the start of `raw_lap` for a stint filled at `fill_lap`.
fn fuel_at(raw_lap: i32, fill_lap: i32) -> f64 {
    TANK - f64::from(raw_lap - fill_lap) * FUEL_PER_LAP
}

#[test]
fn the_cue_never_says_caution_in_a_solo_test_session() {
    let ctx = context("Offline Testing");
    let mut run = Run::new();
    for raw_lap in 0..50 {
        for tick in 0..TICKS_PER_LAP {
            let fuel = fuel_at(raw_lap, 0) - FUEL_PER_LAP * tick as f64 / TICKS_PER_LAP as f64;
            let state = run.step(Some(&ctx), raw_lap, tick, fuel, TEST_FLAGS, false);
            assert_eq!(
                state.flag_state,
                Some(FlagState::Racing),
                "{raw_lap}.{tick}"
            );
            assert_ne!(state.cue.unwrap().id, CUE_CAUTION, "{raw_lap}.{tick}");
            assert_eq!(
                state.session_flags,
                Some(TEST_FLAGS),
                "the raw word is kept"
            );
        }
    }
}

#[test]
fn the_laps_to_pit_estimate_appears_once_two_clean_laps_have_been_driven() {
    let first =
        common::load("solo-test-session.json")["fuel_estimate"]["first_raw_lap_with_estimate"]
            .as_i64()
            .unwrap() as i32;
    let ctx = context("Offline Testing");
    let mut run = Run::new();
    for raw_lap in 0..8 {
        for tick in 0..TICKS_PER_LAP {
            let fuel = fuel_at(raw_lap, 0) - FUEL_PER_LAP * tick as f64 / TICKS_PER_LAP as f64;
            let state = run.step(Some(&ctx), raw_lap, tick, fuel, TEST_FLAGS, false);
            // The first lap is only part observed; laps 2 and 3 complete with a fuel sample each,
            // so the third lap-counter change is the first with an estimate (C# needs two samples).
            let expected = raw_lap >= first;
            assert_eq!(
                state.fuel_laps_remaining.is_some(),
                expected,
                "{raw_lap}.{tick}"
            );
            assert_eq!(
                state.fuel_hard_limit_laps.is_some(),
                expected,
                "{raw_lap}.{tick}"
            );
            if expected {
                let laps = state.fuel_laps_remaining.unwrap();
                let average = FUEL_PER_LAP as f32;
                assert!((laps - state.fuel.unwrap() / average).abs() < 0.01);
                assert!(state.fuel_hard_limit_laps.unwrap() >= 100);
            }
        }
    }
}

#[test]
fn two_stints_with_a_pit_stop_keep_estimating_and_say_pit_road_not_caution() {
    let ctx = context("Offline Testing");
    let mut run = Run::new();
    let mut estimates_after_stop = 0;
    for raw_lap in 0..50 {
        let fill_lap = if raw_lap < 25 { 0 } else { 25 };
        for tick in 0..TICKS_PER_LAP {
            let in_pits = raw_lap == 24 && (3..6).contains(&tick);
            // The stop refills the tank during the pit-road ticks of lap 24.
            let fuel = if raw_lap == 24 && tick >= 4 {
                TANK
            } else {
                fuel_at(raw_lap, fill_lap) - FUEL_PER_LAP * tick as f64 / TICKS_PER_LAP as f64
            };
            let flags = if in_pits {
                TEST_FLAGS_SERVICIBLE
            } else {
                TEST_FLAGS
            };
            let state = run.step(Some(&ctx), raw_lap, tick, fuel, flags, in_pits);
            let cue = state.cue.unwrap().id;
            assert_ne!(cue, CUE_CAUTION, "{raw_lap}.{tick}");
            if in_pits && state.cue.unwrap().id != CUE_PIT_ROAD {
                // The safe-glance hold may keep the previous cue, but pit road is a glance moment.
                panic!("pit road cue expected at {raw_lap}.{tick}, got {cue}");
            }
            if raw_lap >= 27 && state.fuel_laps_remaining.is_some() {
                estimates_after_stop += 1;
            }
        }
    }
    assert_eq!(
        estimates_after_stop,
        23 * TICKS_PER_LAP,
        "stint two estimates every tick"
    );
}

fn flag_name(state: Option<FlagState>) -> &'static str {
    match state.unwrap() {
        FlagState::Unknown => "unknown",
        FlagState::Racing => "racing",
        FlagState::Green => "green",
        FlagState::White => "white",
        FlagState::Checkered => "checkered",
        FlagState::Caution => "caution",
        FlagState::Red => "red",
        FlagState::Black => "black",
        FlagState::Disqualified => "disqualified",
    }
}

/// Every session type against the recorded flag words: one lap to green is a caution in a Race and
/// when the type is unknown only; a real caution bit is one everywhere; the cue follows the state.
#[test]
fn the_flag_vectors_hold_in_every_session_type() {
    let doc = common::load("solo-test-session.json");
    let cases = doc["flag_cases"].as_array().unwrap();
    assert_eq!(cases.len(), 44);
    for case in cases {
        let ctx = case["session_type"].as_str().map(context);
        let flags = u32::try_from(case["session_flags"].as_u64().unwrap()).unwrap();
        let label = format!("{} {flags:#x}", case["session_type"]);
        let mut run = Run::new();
        let state = run.step(ctx.as_ref(), 0, 0, TANK, flags, false);
        assert_eq!(
            flag_name(state.flag_state),
            case["flag_state"].as_str().unwrap(),
            "{label}: {}",
            case["note"]
        );
        assert_eq!(
            state.cue.unwrap().id == CUE_CAUTION,
            case["caution_cue"].as_bool().unwrap(),
            "{label}"
        );
    }
}

#[test]
fn a_session_change_from_a_test_into_a_race_restores_the_race_meaning() {
    let test = context("Offline Testing");
    let race = Arc::new(SessionContext {
        session_types: vec![(0, "Offline Testing".into()), (1, "Race".into())],
        ..SessionContext::default()
    });
    let mut run = Run::new();
    let state = run.step(Some(&test), 0, 0, TANK, TEST_FLAGS, false);
    assert_eq!(state.flag_state, Some(FlagState::Racing));
    // The same context now names session 1 as the Race; the sample's own session number decides.
    run.ms += 1;
    let sample = Sample {
        captured_unix_ms: run.ms,
        connected: true,
        session: Some(race),
        session_num: Some(1),
        session_flags: Some(TEST_FLAGS),
        lap: Some(0),
        ..Sample::default()
    };
    let state = run.engine.on_tick(&sample);
    assert_eq!(state.flag_state, Some(FlagState::Caution));
}

//! Engine behaviour the C# vectors do not reach: non-finite SDK values, values that do not fit
//! their channel, presence-based fallbacks, and the disconnected and paused states.

use std::sync::Arc;

use coach_live::{Engine, EngineConfig, LiveState, Sample, SessionContext};
use coach_wire::live_channels::{Evidence, FlagState, GapTrend, Priority, RunPhase};

fn connected(ms: u64) -> Sample {
    Sample {
        captured_unix_ms: ms,
        connected: true,
        tick: 10,
        tick_rate: 60,
        session_flags: Some(0x4),
        lap: Some(4),
        ..Sample::default()
    }
}

fn tick(engine: &mut Engine, sample: &Sample) -> LiveState {
    engine.on_tick(sample)
}

#[test]
fn a_connected_sample_with_nothing_else_is_all_missing_but_never_zero() {
    let mut engine = Engine::new(EngineConfig::default());
    let state = tick(&mut engine, &connected(1000));
    assert!(state.connected && state.first_of_epoch);
    assert_eq!(state.session_epoch, 1);
    assert_eq!(state.flag_state, Some(FlagState::Green));
    assert_eq!(state.lap, Some(5));
    assert_eq!(state.run_phase, Some(RunPhase::Early));
    assert_eq!(state.green_run_laps, Some(0));
    assert_eq!(
        state.on_pit_road,
        Some(false),
        "a missing OnPitRoad counts as false (C# ?? false)"
    );
    assert_eq!(
        state.tow_active, None,
        "a missing tow timer is not 'not towing'"
    );
    for missing in [
        state.speed,
        state.throttle,
        state.brake,
        state.steering_wheel_angle,
        state.steering_road_wheel,
        state.steering_ratio,
        state.rpm,
        state.yaw_rate,
        state.lateral_accel,
        state.longitudinal_accel,
        state.lap_dist_pct,
        state.brake_bias,
        state.fuel,
        state.fuel_pct,
        state.fuel_laps_remaining,
        state.last_lap,
        state.leader_last_lap,
        state.ahead_gap,
        state.behind_gap,
        state.leader_gap,
        state.pace_band_min,
        state.pace_band_max,
        state.mandatory_repair,
        state.optional_repair,
        state.track_temperature,
        state.air_temperature,
        state.pace_delta_to_leader,
    ] {
        assert_eq!(missing, None);
    }
    assert_eq!(
        (
            state.gear,
            state.laps_remaining,
            state.position,
            state.incidents
        ),
        (None, None, None, None)
    );
    assert_eq!(
        (state.latitude, state.longitude, state.session_time),
        (None, None, None)
    );
    assert_eq!((state.fuel_hard_limit_laps, state.abs_active), (None, None));
    assert_eq!(state.ahead_gap_trend, Some(GapTrend::Unavailable));
    let cue = state.cue.unwrap();
    assert_eq!(
        (cue.id, cue.priority, cue.evidence),
        (12, Priority::Information, Evidence::Unavailable)
    );
}

#[test]
fn non_finite_values_are_missing_in_the_output() {
    let mut engine = Engine::new(EngineConfig::default());
    let mut sample = connected(1000);
    sample.speed = Some(f64::NAN);
    sample.throttle = Some(f64::INFINITY);
    sample.rpm = Some(1e39); // finite, but not an f32
    sample.lat = Some(f64::NEG_INFINITY);
    sample.session_time = Some(f64::NAN);
    sample.air_temp = Some(21.0);
    sample.lat_accel = Some(f64::NAN);
    sample.steering_wheel_angle = Some(f64::NAN);
    sample.dc_brake_bias = Some(f64::NAN);
    let state = tick(&mut engine, &sample);
    assert_eq!(state.speed, None);
    assert_eq!(state.throttle, None);
    assert_eq!(state.rpm, None);
    assert_eq!(state.latitude, None);
    assert_eq!(state.session_time, None);
    assert_eq!(state.lateral_accel, None);
    assert_eq!(state.steering_wheel_angle, None);
    assert_eq!(state.brake_bias, None);
    assert_eq!(state.air_temperature, Some(21.0));
}

#[test]
fn fallbacks_select_by_presence_not_by_finiteness() {
    let mut engine = Engine::new(EngineConfig::default());
    let mut sample = connected(1000);
    // C# `ReadDouble(a) ?? ReadDouble(b)`: a present NaN does not fall back.
    sample.track_temp_crew = Some(f64::NAN);
    sample.track_temp = Some(29.0);
    sample.session_laps_remain_ex = Some(f64::NAN);
    sample.session_laps_remain = Some(12.0);
    sample.player_car_my_incident_count = None;
    sample.player_incidents = Some(3);
    let state = tick(&mut engine, &sample);
    assert_eq!(state.track_temperature, None);
    assert_eq!(state.laps_remaining, None);
    assert_eq!(state.incidents, Some(3));

    let mut engine = Engine::new(EngineConfig::default());
    sample.track_temp_crew = None;
    sample.session_laps_remain_ex = None;
    sample.player_car_my_incident_count = Some(5);
    let state = tick(&mut engine, &sample);
    assert_eq!(state.track_temperature, Some(29.0));
    assert_eq!(state.laps_remaining, Some(12));
    assert_eq!(state.incidents, Some(5));
}

#[test]
fn values_that_do_not_fit_their_channel_are_absent() {
    let mut engine = Engine::new(EngineConfig::default());
    let mut sample = connected(1000);
    sample.lap = Some(70_000);
    sample.gear = Some(300);
    sample.player_car_position = Some(256);
    sample.player_car_class_position = Some(-1);
    sample.player_incidents = Some(-3);
    let state = tick(&mut engine, &sample);
    assert_eq!(state.lap, None);
    assert_eq!(state.gear, None);
    assert_eq!(state.position, None);
    assert_eq!(state.class_position, None);
    assert_eq!(state.incidents, None);
    sample.lap = Some(i32::MAX); // C# `raw + 1` wraps negative; the engine lap stays 1
    assert_eq!(tick(&mut engine, &sample).lap, Some(1));
}

#[test]
fn a_nan_scoring_element_makes_that_gap_missing_and_other_gaps_survive() {
    let mut engine = Engine::new(EngineConfig::default());
    let mut sample = connected(1000);
    sample.player_car_idx = Some(0);
    sample.player_car_class_position = Some(3);
    sample.car_idx_class_position = vec![3, 2, 4, 1];
    sample.car_idx_position = vec![3, 2, 4, 1];
    sample.car_idx_lap = vec![5, 5, 5, 5];
    sample.car_idx_f2_time = vec![100.0, f32::NAN, 101.5, 90.0];
    let state = tick(&mut engine, &sample);
    assert_eq!(state.ahead_gap, None, "the NaN element is the car ahead");
    assert_eq!(state.behind_gap, Some(1.5));
    assert_eq!(state.leader_gap, Some(10.0));
    assert_eq!(state.position, Some(3));
}

#[test]
fn a_gap_to_a_car_on_another_lap_is_missing() {
    let mut engine = Engine::new(EngineConfig::default());
    let mut sample = connected(1000);
    sample.player_car_idx = Some(0);
    sample.player_car_class_position = Some(2);
    sample.car_idx_class_position = vec![2, 1, 3];
    sample.car_idx_lap = vec![5, 6, 5];
    sample.car_idx_f2_time = vec![100.0, 98.0, 101.0];
    let state = tick(&mut engine, &sample);
    assert_eq!(state.ahead_gap, None);
    assert_eq!(state.ahead_gap_trend, Some(GapTrend::Unavailable));
    assert_eq!(state.behind_gap, Some(1.0));
    assert_eq!(state.behind_gap_trend, Some(GapTrend::Stable));
}

#[test]
fn the_leader_gap_is_zero_when_the_player_leads() {
    let mut engine = Engine::new(EngineConfig::default());
    let mut sample = connected(1000);
    sample.player_car_position = Some(1);
    sample.player_car_class_position = Some(1);
    let state = tick(&mut engine, &sample);
    assert_eq!(state.leader_gap, Some(0.0));
    assert_eq!(state.position, Some(1));
}

#[test]
fn disconnected_states_say_waiting_and_carry_the_pause_flag() {
    let mut engine = Engine::new(EngineConfig::default());
    engine.set_paused(true);
    let state = tick(
        &mut engine,
        &Sample {
            captured_unix_ms: 500,
            tick: 3,
            tick_rate: 60,
            session_flags: Some(0x4),
            speed: Some(30.0),
            ..Sample::default()
        },
    );
    assert!(!state.connected && !state.first_of_epoch && !state.on_track && !state.in_garage);
    assert!(state.coaching_paused);
    assert_eq!(
        state.speed, None,
        "a disconnected sample publishes no values"
    );
    assert_eq!(state.flag_state, None);
    assert_eq!(
        state.cue.unwrap().id,
        14,
        "waiting wins over paused while disconnected"
    );
    assert_eq!(state.session_epoch, 0);
    assert_eq!(
        (state.captured_unix_ms, state.tick, state.tick_rate),
        (500, 3, 60)
    );
}

#[test]
fn pausing_replaces_the_cue_and_resuming_restores_it() {
    let mut engine = Engine::new(EngineConfig::default());
    let state = tick(&mut engine, &connected(1000));
    assert_eq!(state.cue.unwrap().id, 12);
    engine.set_paused(true);
    let paused = tick(&mut engine, &connected(1100));
    assert!(paused.coaching_paused);
    let cue = paused.cue.unwrap();
    assert_eq!(
        (cue.id, cue.priority, cue.evidence),
        (15, Priority::Information, Evidence::Measured)
    );
    engine.set_paused(false);
    let resumed = tick(&mut engine, &connected(1200));
    assert!(!resumed.coaching_paused);
    assert_eq!(resumed.cue.unwrap().id, 12);
}

#[test]
fn urgent_cues_break_through_the_safe_glance_hold() {
    let mut engine = Engine::new(EngineConfig { safe_glance: true });
    let mut cornering = connected(1000);
    cornering.brake = Some(0.4);
    cornering.steering_wheel_angle = Some(0.5);
    cornering.lat_accel = Some(15.0);
    let first = tick(&mut engine, &cornering);
    assert_eq!(first.cue.unwrap().id, 12);
    // A low-priority change while cornering is held back...
    cornering.captured_unix_ms = 1100;
    let held = tick(&mut engine, &cornering);
    assert_eq!(held.cue.unwrap().id, 12);
    // ... but a critical one is not.
    cornering.captured_unix_ms = 1200;
    cornering.player_car_tow_time = Some(5.0);
    let tow = tick(&mut engine, &cornering);
    assert_eq!(tow.cue.unwrap().id, 2);
    // Turning the hold off shows ordinary changes at once.
    engine.set_safe_glance(false);
    cornering.captured_unix_ms = 1300;
    cornering.player_car_tow_time = Some(0.0);
    cornering.on_pit_road = Some(true);
    assert_eq!(tick(&mut engine, &cornering).cue.unwrap().id, 8);
}

#[test]
fn the_epoch_wraps_without_panicking_and_reconnects_start_a_new_one() {
    let mut engine = Engine::new(EngineConfig::default());
    let a = tick(&mut engine, &connected(1000));
    let b = tick(&mut engine, &connected(1100));
    assert_eq!(
        (
            a.session_epoch,
            a.first_of_epoch,
            b.session_epoch,
            b.first_of_epoch
        ),
        (1, true, 1, false)
    );
    let gone = tick(
        &mut engine,
        &Sample {
            captured_unix_ms: 1200,
            ..Sample::default()
        },
    );
    assert_eq!((gone.session_epoch, gone.first_of_epoch), (1, false));
    let back = tick(&mut engine, &connected(1300));
    assert_eq!((back.session_epoch, back.first_of_epoch), (2, true));
}

#[test]
fn identity_changes_between_known_values_start_a_new_epoch_but_unknown_ones_do_not() {
    let mut engine = Engine::new(EngineConfig::default());
    let mut sample = connected(1000);
    sample.session_unique_id = Some(7);
    assert_eq!(tick(&mut engine, &sample).session_epoch, 1);
    sample.captured_unix_ms = 1100;
    sample.session_unique_id = None; // unknown: no change
    assert_eq!(tick(&mut engine, &sample).session_epoch, 1);
    sample.captured_unix_ms = 1200;
    sample.session_unique_id = Some(7); // back to the same value
    assert_eq!(tick(&mut engine, &sample).session_epoch, 1);
    sample.captured_unix_ms = 1300;
    sample.session_unique_id = Some(8);
    assert_eq!(tick(&mut engine, &sample).session_epoch, 2);
    // The subsession and the session phase are read from the context.
    let practice = Arc::new(SessionContext {
        subsession_id: Some(1),
        session_types: vec![
            (0, "Practice".into()),
            (1, "Offline Testing".into()),
            (2, "Lone Qualify".into()),
        ],
        ..SessionContext::default()
    });
    sample.session = Some(Arc::clone(&practice));
    sample.session_num = Some(0);
    sample.captured_unix_ms = 1400;
    assert_eq!(
        tick(&mut engine, &sample).session_epoch,
        2,
        "a context appearing is not a change"
    );
    sample.session_num = Some(1);
    sample.captured_unix_ms = 1500;
    assert_eq!(
        tick(&mut engine, &sample).session_epoch,
        3,
        "practice to offline testing"
    );
    sample.session_num = Some(2);
    sample.captured_unix_ms = 1600;
    assert_eq!(
        tick(&mut engine, &sample).session_epoch,
        4,
        "offline testing to qualifying"
    );
}

#[test]
fn the_engine_lap_decreasing_resets_evidence() {
    let mut engine = Engine::new(EngineConfig::default());
    let mut sample = connected(1000);
    sample.lap_last_lap_time = Some(30.0);
    for (index, lap) in (3..=7).enumerate() {
        sample.lap = Some(lap);
        sample.captured_unix_ms = 1000 + index as u64 * 1000;
        tick(&mut engine, &sample);
    }
    sample.lap = Some(7);
    sample.captured_unix_ms = 7000;
    let before = tick(&mut engine, &sample);
    assert!(before.pace_band_min.is_some());
    sample.lap = Some(0);
    sample.captured_unix_ms = 8000;
    let after = tick(&mut engine, &sample);
    assert!(after.pace_band_min.is_none());
    assert!(after.first_of_epoch);
}

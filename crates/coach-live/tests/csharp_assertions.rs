//! The assertions of the C# `LiveTelemetry_*` engine tests (`CoordinatorTests.cs:2421-2808`),
//! `SteeringBasisTests.cs` (live cases) and the `LiveMonitorTests.cs` sentinels, restated against
//! the Rust engine over the scenarios converted from them (see each scenario's `origin`).

mod common;

use std::sync::Arc;

use coach_live::{
    Engine, EngineConfig, LiveState, TileState, cue_text, display_range, format_tile,
};
use coach_wire::live_channels::{Evidence, GapTrend, Priority, RunPhase};
use common::{context, load, sample_from};
use serde_json::{Map, Value};

fn near(actual: Option<f32>, expected: f64, tolerance: f64) -> bool {
    actual.is_some_and(|v| (f64::from(v) - expected).abs() <= tolerance)
}

/// The scenario with this name, run through a fresh engine; one state per step.
fn run(doc: &Value, name: &str) -> Vec<LiveState> {
    let scenario = doc["scenarios"]
        .as_array()
        .unwrap()
        .iter()
        .find(|s| s["name"] == name)
        .unwrap_or_else(|| panic!("no scenario {name}"));
    let mut engine = Engine::new(EngineConfig {
        safe_glance: scenario["safe_glance"].as_bool().unwrap(),
    });
    let mut session = context(&scenario["session"]);
    let mut values = Map::new();
    let mut states = Vec::new();
    for step in scenario["steps"].as_array().unwrap() {
        if step.get("session").is_some() {
            session = context(&step["session"]);
        }
        for (key, value) in step["set"].as_object().unwrap() {
            values.insert(key.clone(), value.clone());
        }
        let mut sample = sample_from(&values);
        sample.connected = step["connected"].as_bool().unwrap();
        sample.captured_unix_ms = step["t"].as_u64().unwrap();
        sample.tick = u32::try_from(step["tick"].as_u64().unwrap()).unwrap();
        sample.tick_rate = u16::try_from(step["tick_rate"].as_u64().unwrap()).unwrap();
        sample.session = Some(Arc::clone(&session));
        engine.set_paused(step["paused"].as_bool().unwrap());
        states.push(engine.on_tick(&sample));
    }
    states
}

#[test]
fn critical_state_wins_and_is_urgent() {
    let doc = load("engine.json");
    let s = run(&doc, "critical_and_urgent_override");
    let cue = s[1].cue.unwrap();
    assert_eq!(cue.priority, Priority::Critical);
    assert!(cue_text(&cue).contains("Black flag"));
}

#[test]
fn gap_trend_rolls_and_caution_suppresses_it() {
    let doc = load("engine.json");
    let s = run(&doc, "rolling_gap_trend_and_caution");
    assert_eq!(s[1].ahead_gap_trend, Some(GapTrend::Closing));
    assert_eq!(
        s[1].behind_gap_trend,
        Some(GapTrend::Growing),
        "a larger physical gap to the car behind is growing"
    );
    assert_eq!(s[2].ahead_gap_trend, Some(GapTrend::Stale));
    assert!(
        near(s[2].ahead_gap, 0.5, 1e-6),
        "the value is kept while the trend is stale"
    );
}

#[test]
fn fuel_hard_limit_is_not_a_pit_window() {
    let doc = load("engine.json");
    let s = run(&doc, "fuel_hard_limit_vs_window");
    assert_eq!(s[3].fuel_hard_limit_laps, Some(2));
}

#[test]
fn three_clean_laps_give_a_pace_band_and_pace_stays_apart_from_the_race_gap() {
    let doc = load("engine.json");
    let s = run(&doc, "three_clean_laps_pace_band");
    assert!(s[4].pace_band_min.is_some() && s[4].pace_band_max.is_some());
    assert!(near(s[4].pace_delta_to_leader, 0.4, 1e-4));
    assert!(near(s[4].leader_gap, 4.5, 1e-4));
    assert!(
        s[2].pace_band_min.is_none(),
        "two clean laps are not a baseline"
    );
}

#[test]
fn safe_glance_delays_an_ordinary_cue_until_a_straight() {
    let doc = load("engine.json");
    let s = run(&doc, "safe_glance_delays_traffic_cue");
    assert_eq!(
        s[1].cue.unwrap().id,
        s[0].cue.unwrap().id,
        "held through the corner"
    );
    let straight = s[2].cue.unwrap();
    assert!(cue_text(&straight).contains("ahead"));
    assert_eq!(straight.id, 9);
}

#[test]
fn missing_dynamics_are_not_a_straight() {
    let doc = load("engine.json");
    let s = run(&doc, "missing_dynamics_are_not_a_straight");
    assert_eq!(
        s[1].cue.unwrap().id,
        s[0].cue.unwrap().id,
        "no measured straight: the cue is held"
    );
    assert!(
        s[1].brake.is_none() && s[1].lateral_accel.is_none() && s[1].steering_wheel_angle.is_none()
    );
    assert_eq!(s[2].cue.unwrap().id, 9);
}

#[test]
fn only_fully_observed_clean_laps_feed_pace_and_fuel() {
    let doc = load("engine.json");
    let s = run(&doc, "fully_observed_clean_laps_only");
    assert!(
        s[1].fuel_laps_remaining.is_none(),
        "a partial first lap must not seed fuel range"
    );
    assert!(
        s[2].fuel_laps_remaining.is_none(),
        "one measured burn is not a baseline"
    );
    assert!(
        s[4].fuel_laps_remaining.is_none(),
        "a caution anywhere on the lap excludes it"
    );
    assert!(
        s[5].fuel_laps_remaining.is_some(),
        "two fully observed clean burns support an estimate"
    );
    assert!(
        s[5].pace_band_min.is_none(),
        "only two clean laps exist at this point"
    );
    assert!(s[6].pace_band_min.is_some());
}

#[test]
fn run_phase_resets_when_pit_road_is_observed() {
    let doc = load("engine.json");
    let s = run(&doc, "run_phase_resets_on_pit_road");
    assert_eq!(s[6].green_run_laps, Some(5));
    assert_eq!(s[6].run_phase, Some(RunPhase::Middle));
    assert_eq!(s[7].green_run_laps, Some(0));
    assert_eq!(s[7].run_phase, Some(RunPhase::Early));
}

#[test]
fn run_phase_counts_only_clean_green_laps() {
    let doc = load("engine.json");
    let s = run(&doc, "run_phase_counts_clean_green_laps");
    assert_eq!(
        s[3].green_run_laps,
        Some(0),
        "a repair-confounded lap cannot advance the run"
    );
    assert_eq!(
        s[5].green_run_laps,
        Some(0),
        "a caution-confounded lap cannot advance the run"
    );
    assert_eq!(s[6].green_run_laps, Some(1));
}

#[test]
fn a_driving_cue_needs_a_clean_baseline_and_persistence() {
    let doc = load("engine.json");
    let s = run(&doc, "driver_input_cue_needs_baseline_and_persistence");
    assert_ne!(
        s[5].cue.unwrap().priority,
        Priority::Coaching,
        "one unusual pass is not a cue"
    );
    let cue = s[7].cue.unwrap();
    assert_eq!(cue.priority, Priority::Coaching);
    let text = cue_text(&cue);
    assert!(
        text.contains("Braking zone")
            && text.contains("clean baseline")
            && text.contains("3 comparable laps"),
        "{text}"
    );
    assert_eq!(cue.evidence, Evidence::Derived);
    assert_eq!(
        text,
        "Braking zone near 25% lap: peak 60% above your clean baseline on 3 comparable laps"
    );
}

#[test]
fn a_large_overshoot_is_grouped_like_the_csharp_percent_format() {
    let doc = load("engine.json");
    let s = run(&doc, "braking_overshoot_large_ratio");
    let texts: Vec<String> = s
        .iter()
        .filter_map(|state| state.cue)
        .map(|cue| cue_text(&cue))
        .collect();
    assert!(
        texts
            .iter()
            .any(|t| t.starts_with("Braking zone near 25% lap: peak 1,567% above")),
        "{texts:?}"
    );
}

#[test]
fn repair_confounded_laps_make_no_driving_cue() {
    let doc = load("engine.json");
    let s = run(&doc, "repair_confounded_laps_make_no_driver_cue");
    let cue = s[6].cue.unwrap();
    assert_ne!(cue.priority, Priority::Coaching);
    assert!(!cue_text(&cue).contains("Braking zone"));
}

#[test]
fn a_disconnect_clears_pace_and_input_baselines() {
    let doc = load("engine.json");
    let s = run(&doc, "disconnect_clears_baselines");
    assert!(s[4].pace_band_min.is_some());
    assert!(
        !s[5].connected && s[5].cue.unwrap().id == 14,
        "a disconnected state says waiting"
    );
    assert!(s[6].pace_band_min.is_none());
    assert_eq!(s[6].cue.unwrap().id, 12);
}

#[test]
fn the_session_epoch_advances_only_at_stream_boundaries() {
    let doc = load("engine.json");
    let s = run(&doc, "session_epoch_boundaries");
    let epochs: Vec<u32> = s.iter().map(|state| state.session_epoch).collect();
    // [0] disconnected before the first connection; [1] connected: a new epoch; laps 3..6 keep it;
    // the lap counter reset starts another; a disconnect leaves it alone (contract 4.4); the
    // reconnect starts the next, and ordinary progress after it keeps it.
    assert_eq!(epochs, [0, 1, 1, 1, 1, 1, 2, 2, 3, 3]);
    let first: Vec<bool> = s.iter().map(|state| state.first_of_epoch).collect();
    assert_eq!(
        first,
        [
            false, true, false, false, false, false, true, false, true, false
        ]
    );
    assert!(
        s[6].pace_band_min.is_none() && s[6].fuel_laps_remaining.is_none(),
        "evidence must not cross a boundary"
    );
    assert!(
        s[5].fuel_laps_remaining.is_some(),
        "the pre-reset session established a fuel baseline"
    );
}

#[test]
fn the_unlimited_laps_sentinel_is_unknown() {
    let doc = load("engine.json");
    let s = run(&doc, "laps_remaining_sentinels");
    // Inputs: 32767, 32768, 50000, -1, NaN, 0, 4.2, 43, 9999.5, 10000, 1. The ceiling test runs
    // before the round-up, so 9999.5 is a real count (10000) and 10000 itself is not.
    let got: Vec<Option<u16>> = s.iter().map(|state| state.laps_remaining).collect();
    let expected: Vec<Option<u16>> = vec![
        None,
        None,
        None,
        None,
        None,
        Some(0),
        Some(5),
        Some(43),
        Some(10_000),
        None,
        Some(1),
    ];
    assert_eq!(got, expected);
}

#[test]
fn road_wheel_steering_is_the_wheel_over_the_setup_ratio() {
    let doc = load("engine.json");
    let s = run(&doc, "road_wheel_steering_from_the_setup_ratio");
    // Step 0: wheel 0.6 rad, ratio 12 -> 0.05 rad at the road wheel.
    assert!(near(s[0].steering_road_wheel, 0.05, 1e-7));
    assert_eq!(s[0].steering_ratio, Some(12.0));
    // Ratio 10:1, then none (step 3), then unreadable 12:2 (step 4): the road wheel is missing and
    // the wheel angle stays available under its own name.
    assert_eq!(s[2].steering_ratio, Some(10.0));
    assert_eq!(s[3].steering_ratio, None);
    assert_eq!(s[3].steering_road_wheel, None);
    assert!(s[3].steering_wheel_angle.is_some());
    assert_eq!(s[4].steering_ratio, None);
    assert_eq!(s[5].steering_ratio, Some(16.5));
    let with = format_tile("steering", &s[0], None, None).unwrap();
    assert_eq!(with.state, TileState::Value);
    assert_eq!(with.secondary.as_deref(), Some("12:1 steering ratio"));
    let without = format_tile("steering", &s[3], None, None).unwrap();
    assert_eq!(without.primary, "Ratio not recorded");
    assert_eq!(without.state, TileState::NotMeasured);
    assert_eq!(without.value, None);
    let wheel = format_tile("steering-wheel-angle", &s[3], None, None).unwrap();
    assert_eq!(wheel.state, TileState::Value);
}

#[test]
fn semantic_ranges_apply_to_bar_capable_readings() {
    let doc = load("engine.json");
    let s = run(&doc, "catalog_ranges_and_stepped_metrics");
    let bias = format_tile("brake-bias", &s[0], None, None).unwrap();
    assert!((bias.value.unwrap() - 54.7).abs() < 1e-5);
    assert_eq!(display_range("brake-bias", &bias, &[]), Some((40.0, 70.0)));
    let fuel = format_tile("fuel", &s[2], Some("L"), Some(1)).unwrap();
    assert!((fuel.value.unwrap() - 10.0).abs() < 1e-6);
    assert_eq!(fuel.min, Some(0.0));
    assert!((fuel.max.unwrap() - 20.0).abs() < 1e-5);
    assert_eq!(
        display_range("fuel", &fuel, &[]),
        Some((0.0, fuel.max.unwrap()))
    );
    assert_eq!(fuel.primary, "10.0 L");
    // Categorical readings keep their stepped ordinals: caution is yellow (4), pit road is 1.
    let flag = format_tile("flag", &s[2], None, None).unwrap();
    assert_eq!((flag.primary.as_str(), flag.value), ("CAUTION", Some(4.0)));
    let pit = format_tile("on-pit-road", &s[2], None, None).unwrap();
    assert_eq!(
        (pit.primary.as_str(), pit.value),
        ("On pit road", Some(1.0))
    );
}

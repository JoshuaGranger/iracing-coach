//! Engine tick cost: one `Engine::on_tick` plus one `encode_frame` per SDK tick, on a 64-car
//! field at 60 Hz with a lap every 90 s of sim time. The acceptance bound is a median under
//! 20 microseconds per engine tick (`workstreams.md`, W2).
//!
//! Run with `cargo bench -p coach-live`; it prints the figures and fails above the bound.

use std::hint::black_box;
use std::sync::Arc;
use std::time::Instant;

use coach_live::{Engine, EngineConfig, Sample, SessionContext};

const CARS: usize = 64;
const TICKS: usize = 300_000;
const BOUND_NS: u64 = 20_000;

fn sample(tick: usize, session: &Arc<SessionContext>) -> Sample {
    let lap_ticks = 90 * 60;
    let lap = tick / lap_ticks;
    let progress = (tick % lap_ticks) as f64 / lap_ticks as f64;
    let braking = (progress * 20.0).fract() < 0.1;
    let mut s = Sample {
        captured_unix_ms: 1_700_000_000_000 + (tick as u64) * 1000 / 60,
        connected: true,
        tick: tick as u32,
        tick_rate: 60,
        session: Some(Arc::clone(session)),
        player_car_idx: Some(10),
        player_car_position: Some(12),
        player_car_class_position: Some(5),
        session_flags: Some(0x4),
        lap: Some(lap as i32),
        session_laps_remain_ex: Some(30.0),
        lap_last_lap_time: Some(90.2),
        fuel_level: Some(40.0 - lap as f64 * 1.9),
        fuel_level_pct: Some(0.6),
        track_temp_crew: Some(31.0),
        air_temp: Some(22.0),
        dc_brake_bias: Some(54.0),
        on_pit_road: Some(false),
        steering_wheel_angle: Some(if braking { 0.5 } else { 0.02 }),
        throttle: Some(if braking { 0.0 } else { 0.8 }),
        brake: Some(if braking { 0.6 } else { 0.0 }),
        gear: Some(4),
        rpm: Some(6200.0),
        lat_accel: Some(if braking { 12.0 } else { 0.4 }),
        speed: Some(55.0),
        lap_dist_pct: Some(progress),
        lat: Some(39.78),
        lon: Some(-86.24),
        session_time: Some(tick as f64 / 60.0),
        session_num: Some(1),
        session_unique_id: Some(99),
        is_on_track: Some(true),
        ..Sample::default()
    };
    s.car_idx_position = (0..CARS as i32).map(|i| i + 1).collect();
    s.car_idx_class_position = (0..CARS as i32).map(|i| i % 8 + 1).collect();
    s.car_idx_lap = vec![lap as i32; CARS];
    s.car_idx_f2_time = (0..CARS)
        .map(|i| 200.0 - i as f32 * 0.7 + progress as f32)
        .collect();
    s.car_idx_last_lap_time = vec![90.0; CARS];
    s
}

fn main() {
    let session = Arc::new(SessionContext {
        subsession_id: Some(8001),
        session_types: vec![(1, "Race".to_string())],
        steering_ratio: Some(12.0),
        ..SessionContext::default()
    });
    // One lap of samples reused with advancing time and lap counters, built outside the timed code.
    let samples: Vec<Sample> = (0..TICKS).map(|tick| sample(tick, &session)).collect();
    let mut engine = Engine::new(EngineConfig::default());
    let mut frame = Vec::with_capacity(256);

    for s in samples.iter().take(5_000) {
        black_box(engine.on_tick(s));
    }

    let mut engine_ns = Vec::with_capacity(TICKS);
    let mut frame_ns = Vec::with_capacity(TICKS);
    for (index, s) in samples.iter().enumerate() {
        let started = Instant::now();
        let state = black_box(engine.on_tick(black_box(s)));
        let engine_done = Instant::now();
        state.encode_frame(index as u32, &mut frame);
        black_box(&frame);
        let frame_done = Instant::now();
        engine_ns.push(engine_done.duration_since(started).as_nanos() as u64);
        frame_ns.push(frame_done.duration_since(engine_done).as_nanos() as u64);
    }
    let report = |name: &str, mut values: Vec<u64>| -> u64 {
        values.sort_unstable();
        let median = values[values.len() / 2];
        let p99 = values[values.len() * 99 / 100];
        let max = values[values.len() - 1];
        println!(
            "{name}: median {median} ns, p99 {p99} ns, max {max} ns over {} ticks",
            values.len()
        );
        median
    };
    let engine_median = report("engine tick", engine_ns);
    report("frame encode", frame_ns);
    assert!(
        engine_median < BOUND_NS,
        "engine tick median {engine_median} ns is not under {BOUND_NS} ns"
    );
}

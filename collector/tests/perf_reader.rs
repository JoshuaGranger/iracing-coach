//! What one reader tick costs (SDK poll, engine, frame encode, queue) on a moving car, measured
//! over a virtual-clock replay with no sleeping. Ignored by default (timing; wants a release build):
//! `cargo test --release -p coach-collector --test perf_reader -- --ignored --nocapture`.

mod support;

use std::time::Instant;

use coach_collector::fixture::synthetic_laps;
use support::*;

#[test]
#[ignore = "timing; run in release"]
fn a_moving_car_tick_costs_the_reader_microseconds_not_milliseconds() {
    let data = synthetic_laps(120.0, 60);
    let mut h = Harness::new(&data);
    h.hub.stream().on_start();
    let started = Instant::now();
    h.run();
    let elapsed = started.elapsed();
    let ticks = h.pipeline.stats().ticks;
    let per_tick_us = elapsed.as_secs_f64() * 1e6 / ticks as f64;
    // At 60 ticks/s the reader's share of one core, in percent.
    println!(
        "perf-reader ticks={ticks} frames={} per_tick_us={per_tick_us:.1} core_pct_at_60hz={:.3}",
        h.frames.len(),
        per_tick_us * 60.0 / 1e6 * 100.0
    );
    assert!(ticks >= 7_000);
    assert!(
        per_tick_us < 100.0,
        "{per_tick_us:.1} us per tick is more than the 0.6 % of a core the reader may use"
    );
}

//! The collector's CPU budget on a moving car (gate G4-3: at most 1.5 % of one core), measured by
//! the collector's own accounting (`run --cpu-report`) on a synthetic lapping run replayed in real
//! time at 60 Hz, with the overlay drawn and the stream wanted by two viewers (the mock server
//! sends `viewers: 2` and `stream_wanted: true` in its welcome and counts the frames it receives).
//!
//! It runs the real release binary for about a minute and shows the click-through overlay on the
//! desktop, so it is ignored by default:
//!
//! ```text
//! cargo build --release -p coach-collector
//! cargo test -p coach-collector --test perf_cpu -- --ignored --nocapture
//! ```
//!
//! Environment: `COACH_PERF_BIN` (binary to measure; default the release build beside the test
//! target), `COACH_PERF_SECS` (replay length, default 52), `COACH_PERF_LIMIT` (percent, default
//! 1.5), `COACH_PERF_NO_UI=1` (headless), `COACH_PERF_LAYOUT=busy` (a monitor layout whose tiles
//! all change with the car: speed and RPM readouts, throttle and brake gauges, a speed trend and
//! a steering trend; the default is the factory layout, as on the rig).
#![cfg(windows)]

mod support;

use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

use coach_collector::config::{CachedLayout, Config};
use coach_collector::fixture::synthetic_laps;
use serde_json::json;
use support::*;

/// Tiles that all change while the car moves: the worst case for the overlay.
fn busy_layout() -> serde_json::Value {
    json!({
        "rows": 3, "columns": 3,
        "tiles": [
            {"tile_id": "a", "metric": "speed", "row": 0, "column": 0, "row_span": 1, "column_span": 1, "style": "number", "unit": "mph", "precision": 1},
            {"tile_id": "b", "metric": "rpm", "row": 0, "column": 1, "row_span": 1, "column_span": 1, "style": "number"},
            {"tile_id": "c", "metric": "throttle", "row": 0, "column": 2, "row_span": 1, "column_span": 1, "style": "gauge", "accent": "green"},
            {"tile_id": "d", "metric": "speed", "row": 1, "column": 0, "row_span": 1, "column_span": 2, "style": "trend", "unit": "mph"},
            {"tile_id": "e", "metric": "brake", "row": 1, "column": 2, "row_span": 1, "column_span": 1, "style": "gauge", "accent": "coral"},
            {"tile_id": "f", "metric": "gear", "row": 2, "column": 0, "row_span": 1, "column_span": 1, "style": "number"},
            {"tile_id": "g", "metric": "steering", "row": 2, "column": 1, "row_span": 1, "column_span": 1, "style": "trend"},
            {"tile_id": "h", "metric": "lateral-acceleration", "row": 2, "column": 2, "row_span": 1, "column_span": 1, "style": "number", "precision": 1}
        ]
    })
}

fn binary() -> PathBuf {
    if let Ok(p) = std::env::var("COACH_PERF_BIN") {
        return PathBuf::from(p);
    }
    // CARGO_BIN_EXE is <target>/debug/coach-collector.exe: the release build sits beside `debug`.
    let debug = PathBuf::from(env!("CARGO_BIN_EXE_coach-collector"));
    let release = debug
        .parent()
        .and_then(|d| d.parent())
        .map(|t| t.join("release").join("coach-collector.exe"))
        .unwrap();
    assert!(
        release.exists(),
        "build the release binary first: cargo build --release -p coach-collector ({})",
        release.display()
    );
    release
}

fn field(line: &str, name: &str) -> Option<f64> {
    line.split_whitespace()
        .find_map(|w| w.strip_prefix(name)?.strip_prefix('=')?.parse().ok())
}

#[test]
#[ignore = "runs the release collector for about a minute and shows its overlay"]
fn a_moving_car_streaming_to_two_viewers_costs_the_collector_under_the_budget() {
    let secs: f64 = std::env::var("COACH_PERF_SECS")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(52.0);
    let limit: f64 = std::env::var("COACH_PERF_LIMIT")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(1.5);
    let no_ui = std::env::var("COACH_PERF_NO_UI").is_ok_and(|v| v == "1");

    let root = temp_dir("perf-cpu");
    let fixture = root.join("laps.sdkfix");
    write_file(&fixture, &synthetic_laps(secs, 60));

    if std::env::var("COACH_PERF_LAYOUT").is_ok_and(|v| v == "busy") {
        // The overlay starts from the layout cached in the config (the server sends none here).
        let config = Config {
            layout: Some(CachedLayout {
                layout_id: "perf-busy".into(),
                version: 1,
                layout: busy_layout(),
            }),
            ..Config::default()
        };
        std::fs::create_dir_all(root.join("state")).unwrap();
        config.save(&root.join("state")).unwrap();
    }

    let mock = Mock::start();
    mock.with(|s| {
        s.welcome_stream_wanted = true;
        s.welcome_viewers = 2;
    });

    let mut cmd = Command::new(binary());
    cmd.args(["run", "--replay"])
        .arg(&fixture)
        .args(["--speed", "1", "--cpu-report", "--server"])
        .arg(mock.url())
        .arg("--state-dir")
        .arg(root.join("state"))
        .arg("--iracing-root")
        .arg(&root)
        .stdout(Stdio::piped());
    if no_ui {
        cmd.arg("--no-ui");
    }
    let mut child = cmd.spawn().unwrap();
    let stdout = child.stdout.take().unwrap();
    let reader = std::thread::spawn(move || {
        BufReader::new(stdout)
            .lines()
            .map_while(Result::ok)
            .collect::<Vec<_>>()
    });

    // The replay ends after `secs`; the overlay keeps the process alive, so stop it a moment later.
    let started = Instant::now();
    while started.elapsed() < Duration::from_secs_f64(secs + 2.0) {
        if child.try_wait().unwrap().is_some() {
            break;
        }
        std::thread::sleep(Duration::from_millis(100));
    }
    let frames = mock.with(|s| s.ws_binary.len());
    let _ = child.kill();
    let _ = child.wait();
    let lines = reader.join().unwrap();

    let windows: Vec<&String> = lines
        .iter()
        .filter(|l| l.starts_with("cpu-report label=window"))
        .collect();
    for l in &lines {
        println!("{l}");
    }
    assert!(windows.len() >= 3, "need at least 3 windows: {lines:?}");
    // The first window includes start-up (fixture load, window and connection set-up).
    let mean_of = |name: &str| {
        let v: Vec<f64> = windows
            .iter()
            .skip(1)
            .map(|l| field(l, name).unwrap())
            .collect();
        v.iter().sum::<f64>() / v.len() as f64
    };
    let mean = mean_of("process_pct");
    println!(
        "perf-cpu mean_process_pct={mean:.3} mean_process_cycles_pct={:.3} windows={}          frames_received={frames} limit={limit}",
        mean_of("process_cycles_pct"),
        windows.len() - 1
    );
    assert!(
        frames as f64 >= (secs - 8.0) * 55.0,
        "the stream carried {frames} frames: it was not streaming at 60 Hz"
    );
    assert!(
        mean <= limit,
        "collector CPU {mean:.3} % of one core exceeds {limit} %"
    );
}

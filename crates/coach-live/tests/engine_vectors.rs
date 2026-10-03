//! Engine and tile vectors: scripted SDK values in, the C# monitor's answers out.
//!
//! `tests/vectors/engine.json` was produced by running the real C# `IRacingSdkTelemetrySource`
//! over a synthetic SDK memory map, the real `LiveTelemetryService` (session epoch, engine reset,
//! safe-glance hold) and the real `LiveTelemetryCatalog.Read` (tile text and display range);
//! see `tests/vectors/README.md`. Each step carries the cumulative SDK values (`set` is a delta)
//! and the cumulative expectation (`expect` is a delta). Scenarios converted from the C# unit tests
//! name them in `origin`.

use std::sync::Arc;

mod common;

use coach_live::{
    Engine, EngineConfig, LiveState, TileState, cue_text, decode_frame, display_range, format_tile,
};
use coach_wire::live_channels::{Evidence, FlagState, GapTrend, RunPhase};
use common::{context, f64_of, load, sample_from};
use serde_json::{Map, Value};

fn flag_name(state: Option<FlagState>) -> Option<&'static str> {
    Some(match state? {
        FlagState::Unknown => "unknown",
        FlagState::Racing => "racing",
        FlagState::Green => "green",
        FlagState::White => "white",
        FlagState::Checkered => "checkered",
        FlagState::Caution => "caution",
        FlagState::Red => "red",
        FlagState::Black => "black",
        FlagState::Disqualified => "disqualified",
    })
}

fn trend_name(trend: Option<GapTrend>) -> Option<&'static str> {
    Some(match trend? {
        GapTrend::Unavailable => "unavailable",
        GapTrend::Stale => "stale",
        GapTrend::Closing => "closing",
        GapTrend::Stable => "stable",
        GapTrend::Growing => "growing",
    })
}

fn phase_name(phase: Option<RunPhase>) -> Option<&'static str> {
    Some(match phase? {
        RunPhase::Unknown => "unknown",
        RunPhase::Early => "early",
        RunPhase::Middle => "middle",
        RunPhase::Late => "late",
    })
}

fn evidence_name(evidence: Evidence) -> &'static str {
    match evidence {
        Evidence::Measured => "measured",
        Evidence::Derived => "derived",
        Evidence::Unavailable => "unavailable",
    }
}

/// Checks every key of the cumulative expectation against the engine's state.
fn check_state(label: &str, state: &LiveState, expect: &Map<String, Value>) {
    let float = |name: &str, actual: Option<f32>| {
        // A non-finite C# value (a NaN the reader passed through) is missing on the wire.
        let expected = f64_of(&expect[name])
            .filter(|v| v.is_finite())
            .map(|v| v as f32);
        assert_eq!(actual, expected, "{label}: {name}");
    };
    // A value that does not fit its channel type is absent on the wire.
    let int = |name: &str, actual: Option<i64>, range: std::ops::RangeInclusive<i64>| {
        let expected = expect[name].as_i64().filter(|v| range.contains(v));
        assert_eq!(actual, expected, "{label}: {name}");
    };
    assert_eq!(
        Some(state.connected),
        expect["connected"].as_bool(),
        "{label}: connected"
    );
    assert_eq!(
        Some(state.first_of_epoch),
        expect["first_of_epoch"].as_bool(),
        "{label}: first_of_epoch"
    );
    assert_eq!(
        Some(u64::from(state.session_epoch)),
        expect["session_epoch"].as_u64(),
        "{label}: session_epoch"
    );
    assert_eq!(
        flag_name(state.flag_state),
        expect["flag_state"].as_str(),
        "{label}: flag_state"
    );
    int("lap", state.lap.map(i64::from), 0..=65535);
    int(
        "laps_remaining",
        state.laps_remaining.map(i64::from),
        0..=65535,
    );
    int("position", state.position.map(i64::from), 0..=255);
    int(
        "class_position",
        state.class_position.map(i64::from),
        0..=255,
    );
    int(
        "green_run_laps",
        state.green_run_laps.map(i64::from),
        0..=65535,
    );
    int(
        "fuel_hard_limit_laps",
        state.fuel_hard_limit_laps.map(i64::from),
        0..=65535,
    );
    int("gear", state.gear.map(i64::from), -128..=127);
    float("ahead_gap", state.ahead_gap);
    float("behind_gap", state.behind_gap);
    float("leader_gap", state.leader_gap);
    float("last_lap", state.last_lap);
    float("leader_last_lap", state.leader_last_lap);
    float("pace_delta_to_leader", state.pace_delta_to_leader);
    float("pace_band_min", state.pace_band_min);
    float("pace_band_max", state.pace_band_max);
    float("fuel_laps_remaining", state.fuel_laps_remaining);
    float("fuel", state.fuel);
    float("fuel_pct", state.fuel_pct);
    float("mandatory_repair", state.mandatory_repair);
    float("optional_repair", state.optional_repair);
    float("brake_bias", state.brake_bias);
    float("track_temperature", state.track_temperature);
    float("air_temperature", state.air_temperature);
    float("steering_road_wheel", state.steering_road_wheel);
    float("steering_ratio", state.steering_ratio);
    float("lap_dist_pct", state.lap_dist_pct);
    float("throttle", state.throttle);
    float("brake", state.brake);
    float("rpm", state.rpm);
    assert_eq!(
        state.on_pit_road,
        expect["on_pit_road"].as_bool(),
        "{label}: on_pit_road"
    );
    assert_eq!(
        trend_name(state.ahead_gap_trend),
        expect["ahead_gap_trend"].as_str(),
        "{label}: ahead_gap_trend"
    );
    assert_eq!(
        trend_name(state.behind_gap_trend),
        expect["behind_gap_trend"].as_str(),
        "{label}: behind_gap_trend"
    );
    assert_eq!(
        phase_name(state.run_phase),
        expect["run_phase"].as_str(),
        "{label}: run_phase"
    );

    let cue = state.cue.expect("the engine always states a cue");
    let expected = &expect["cue"];
    assert_eq!(
        Some(u64::from(cue.id)),
        expected["id"].as_u64(),
        "{label}: cue id"
    );
    assert_eq!(
        Some(u64::from(cue.priority as u8)),
        expected["priority"].as_u64(),
        "{label}: cue priority"
    );
    assert_eq!(
        Some(evidence_name(cue.evidence)),
        expected["evidence"].as_str(),
        "{label}: cue evidence"
    );
    assert_eq!(
        Some(cue_text(&cue).as_str()),
        expected["text"].as_str(),
        "{label}: cue text"
    );
}

/// Every state the engine produces encodes to a frame that decodes back to the same values.
fn check_frame_round_trip(label: &str, state: &LiveState, seq: u32) {
    let mut bytes = Vec::new();
    state.encode_frame(seq, &mut bytes);
    let frame = decode_frame(&bytes).unwrap_or_else(|e| panic!("{label}: {e}"));
    assert_eq!(frame.values.len(), 45, "{label}");
    assert_eq!(
        frame.values.as_slice(),
        state.channel_values().as_slice(),
        "{label}: values"
    );
    assert_eq!(frame.cue, state.cue, "{label}: cue");
    assert_eq!(frame.header.seq, seq, "{label}: seq");
    assert_eq!(
        frame.header.session_epoch, state.session_epoch,
        "{label}: epoch"
    );
    assert_eq!(
        frame.header.captured_unix_ms, state.captured_unix_ms,
        "{label}: clock"
    );
    assert_eq!(frame.header.tick, state.tick, "{label}: tick");
    assert_eq!(
        frame.header.tick_rate, state.tick_rate,
        "{label}: tick rate"
    );
    match state.session_time {
        Some(t) => assert_eq!(frame.header.session_time, t, "{label}: session time"),
        None => assert!(frame.header.session_time.is_nan(), "{label}: session time"),
    }
    let flags = frame.header.flags;
    assert_eq!(flags & 1 != 0, state.connected, "{label}: connected bit");
    assert_eq!(flags & 2 != 0, state.on_track, "{label}: on-track bit");
    assert_eq!(flags & 4 != 0, state.in_garage, "{label}: in-garage bit");
    assert_eq!(
        flags & 8 != 0,
        state.on_pit_road == Some(true),
        "{label}: pit-road bit"
    );
    assert_eq!(
        flags & 16 != 0,
        state.first_of_epoch,
        "{label}: first-of-epoch bit"
    );
    assert_eq!(
        flags & 32 != 0,
        state.coaching_paused,
        "{label}: paused bit"
    );
    assert_eq!(
        flags & 0xC0,
        0,
        "{label}: backlog and reserved bits are clear"
    );
}

fn close(actual: f64, expected: f64) -> bool {
    (actual - expected).abs() <= 1e-5 * expected.abs().max(1.0)
}

/// Milliseconds of an `m:ss.fff` or `s.fff` lap time.
fn lap_ms(text: &str) -> i64 {
    let (minutes, rest) = text
        .split_once(':')
        .map_or((0, text), |(m, r)| (m.parse::<i64>().unwrap(), r));
    let (seconds, millis) = rest.split_once('.').unwrap();
    (minutes * 60 + seconds.parse::<i64>().unwrap()) * 1000 + millis.parse::<i64>().unwrap()
}

/// The pace band is derived in double precision in C# but travels as f32 (`live-channels.json`
/// channels 33 and 34), and lap times are truncated to milliseconds, so a bound that sits on an
/// exact millisecond can read 1 ms low. Anything beyond that is a defect.
fn pace_range_matches(actual: &str, expected: &str) -> bool {
    let a: Vec<&str> = actual.split('\u{2013}').collect();
    let e: Vec<&str> = expected.split('\u{2013}').collect();
    a.len() == 2
        && e.len() == 2
        && a.iter()
            .zip(&e)
            .all(|(x, y)| (lap_ms(x) - lap_ms(y)).abs() <= 1)
}

/// Cue bases the C# strings carry and `live-channels.json` lacks (contract change request in the
/// W2 report). `format_tile` reads the table, so the tile's secondary line follows the contract
/// until it is corrected; the entries here are accepted only while the table still differs from C#.
const PENDING_BASIS_IDS: &[u8] = &[1, 3, 6, 7, 8, 10, 11, 12, 13, 15];

fn contract_basis(id: u8) -> Option<&'static str> {
    coach_wire::live_channels::CUES
        .iter()
        .find(|spec| spec.id == id)
        .and_then(|spec| spec.basis)
}

#[derive(Default)]
struct Tally {
    divergences: Vec<String>,
    csharp_bases: std::collections::BTreeMap<u8, Option<String>>,
    tiles: usize,
    pace_range_one_ms: usize,
}

fn check_tiles(label: &str, state: &LiveState, cue_id: u8, tiles: &[Value], tally: &mut Tally) {
    for tile in tiles {
        tally.tiles += 1;
        let id = tile["id"].as_str().unwrap();
        let unit = tile["unit"].as_str();
        let name = format!("{label}: tile {id} [{}]", unit.unwrap_or("-"));
        let text =
            format_tile(id, state, unit, None).unwrap_or_else(|| panic!("{name}: unknown tile"));
        let mut found: Vec<String> = Vec::new();
        let mut report = |field: &str, actual: String, expected: String| {
            if actual != expected {
                found.push(format!("{name}: {field}: got {actual:?}, C# {expected:?}"));
            }
        };
        let expected_primary = tile["primary"].as_str().unwrap();
        if id == "pace-range"
            && text.state == TileState::Value
            && pace_range_matches(&text.primary, expected_primary)
        {
            if text.primary != expected_primary {
                tally.pace_range_one_ms += 1;
            }
        } else {
            report(
                "primary",
                text.primary.clone(),
                expected_primary.to_string(),
            );
        }
        let expected_secondary = tile["secondary"].as_str().map(str::to_string);
        if id == "coach-cue" && text.state == TileState::Value {
            tally
                .csharp_bases
                .insert(cue_id, expected_secondary.clone());
            let pending = PENDING_BASIS_IDS.contains(&cue_id)
                && contract_basis(cue_id) != expected_secondary.as_deref();
            if !pending {
                report(
                    "secondary",
                    format!("{:?}", text.secondary),
                    format!("{expected_secondary:?}"),
                );
            }
        } else {
            report(
                "secondary",
                format!("{:?}", text.secondary),
                format!("{expected_secondary:?}"),
            );
        }
        let kind = match text.state {
            TileState::Value => "value",
            TileState::Waiting => "waiting",
            TileState::NotMeasured => "not_measured",
            TileState::InsufficientEvidence => "insufficient",
        };
        report(
            "state",
            kind.to_string(),
            tile["state"].as_str().unwrap().to_string(),
        );
        match (text.value, f64_of(&tile["value"])) {
            (Some(a), Some(e)) => {
                if !close(a, e) {
                    found.push(format!("{name}: value: got {a}, C# {e}"));
                }
            }
            (None, None) => {}
            (a, e) => found.push(format!("{name}: value presence: got {a:?}, C# {e:?}")),
        }
        let trend: Vec<f64> = tile["trend"]
            .as_array()
            .unwrap()
            .iter()
            .map(|v| f64_of(v).unwrap())
            .collect();
        match (display_range(id, &text, &trend), tile["range"].as_array()) {
            (Some((min, max)), Some(range)) => {
                let (emin, emax) = (f64_of(&range[0]).unwrap(), f64_of(&range[1]).unwrap());
                if !close(min, emin) || !close(max, emax) {
                    found.push(format!(
                        "{name}: range: got [{min}, {max}], C# [{emin}, {emax}]"
                    ));
                }
            }
            (None, None) => {}
            (a, e) => found.push(format!("{name}: range presence: got {a:?}, C# {e:?}")),
        }
        tally.divergences.extend(found);
    }
}

#[test]
fn engine_and_tile_vectors_match_the_csharp_monitor() {
    let doc = load("engine.json");
    assert_eq!(doc["format"], 1);
    let scenarios = doc["scenarios"].as_array().unwrap();
    assert!(scenarios.len() >= 30);
    let mut tally = Tally::default();
    let mut steps_checked = 0;
    let mut cue_ids = std::collections::BTreeSet::new();
    for scenario in scenarios {
        let name = scenario["name"].as_str().unwrap();
        let mut engine = Engine::new(EngineConfig {
            safe_glance: scenario["safe_glance"].as_bool().unwrap(),
        });
        let mut session = context(&scenario["session"]);
        let mut values = Map::new();
        let mut expect = Map::new();
        for (index, step) in scenario["steps"].as_array().unwrap().iter().enumerate() {
            let label = format!("{name} step {index}");
            if step.get("session").is_some() {
                session = context(&step["session"]);
            }
            for (key, value) in step["set"].as_object().unwrap() {
                values.insert(key.clone(), value.clone());
            }
            for (key, value) in step["expect"].as_object().unwrap() {
                expect.insert(key.clone(), value.clone());
            }
            let mut sample = sample_from(&values);
            sample.connected = step["connected"].as_bool().unwrap();
            sample.captured_unix_ms = step["t"].as_u64().unwrap();
            sample.tick = u32::try_from(step["tick"].as_u64().unwrap()).unwrap();
            sample.tick_rate = u16::try_from(step["tick_rate"].as_u64().unwrap()).unwrap();
            sample.session = Some(Arc::clone(&session));
            engine.set_paused(step["paused"].as_bool().unwrap());
            let state = engine.on_tick(&sample);
            check_state(&label, &state, &expect);
            check_frame_round_trip(&label, &state, steps_checked);
            steps_checked += 1;
            let cue_id = u8::try_from(expect["cue"]["id"].as_u64().unwrap()).unwrap();
            cue_ids.insert(cue_id);
            if let Some(tiles) = step.get("tiles").and_then(Value::as_array) {
                check_tiles(&label, &state, cue_id, tiles, &mut tally);
            }
        }
    }
    assert!(
        tally.divergences.is_empty(),
        "{} of {} tile readings differ from C# ({steps_checked} steps):\n{}",
        tally.divergences.len(),
        tally.tiles,
        tally
            .divergences
            .iter()
            .take(80)
            .cloned()
            .collect::<Vec<_>>()
            .join("\n")
    );
    // Every cue the C# engine can say appears in the vectors, so each template was byte-compared.
    let missing: Vec<u8> = (1..=15).filter(|id| !cue_ids.contains(id)).collect();
    assert!(missing.is_empty(), "cues never exercised: {missing:?}");
    // A pending basis that the table now matches must leave the list.
    for id in PENDING_BASIS_IDS {
        if let Some(csharp) = tally.csharp_bases.get(id) {
            assert!(
                contract_basis(*id) != csharp.as_deref(),
                "cue {id} basis now matches C#: remove it from PENDING_BASIS_IDS"
            );
        }
    }
    eprintln!(
        "{steps_checked} steps, {} tile readings, {} pace-range readings 1 ms low (f32 channel)",
        tally.tiles, tally.pace_range_one_ms
    );
}

/// `live-channels.json` tiles against the C# `LiveTelemetryCatalog` definitions.
#[test]
fn the_tile_table_matches_the_csharp_catalog() {
    use coach_wire::live_channels::TILES;
    let doc = load("engine.json");
    let catalog = doc["catalog"].as_array().unwrap();
    assert_eq!(catalog.len(), TILES.len());
    // These four carry their bounds on the reading in C# (status ordinals), not on the definition;
    // the table states them on the tile, which yields the same display range.
    let reading_bounds = ["coach-cue", "flag", "on-pit-road", "tire-phase"];
    for spec in TILES {
        let entry = catalog
            .iter()
            .find(|e| e["id"] == spec.id)
            .unwrap_or_else(|| panic!("tile {} is not in the C# catalog", spec.id));
        assert_eq!(entry["label"], spec.label, "{}: label", spec.id);
        let units: Vec<&str> = entry["units"]
            .as_array()
            .unwrap()
            .iter()
            .map(|u| u.as_str().unwrap())
            .collect();
        assert_eq!(units, spec.units, "{}: units", spec.id);
        assert_eq!(
            entry["default_style"], spec.style,
            "{}: default style",
            spec.id
        );
        assert_eq!(
            entry["precision"].as_u64(),
            Some(u64::from(spec.precision)),
            "{}: precision",
            spec.id
        );
        assert_eq!(
            entry["step"].as_bool(),
            Some(spec.step),
            "{}: step shape",
            spec.id
        );
        if reading_bounds.contains(&spec.id) {
            assert!(
                entry["min"].is_null() && entry["max"].is_null(),
                "{}",
                spec.id
            );
        } else {
            assert_eq!(entry["min"].as_f64(), spec.min, "{}: min", spec.id);
            assert_eq!(entry["max"].as_f64(), spec.max, "{}: max", spec.id);
        }
    }
}

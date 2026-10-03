//! Shared helpers: a builder for realistic `analysis.json` documents, a CCOL decoder that does not
//! use the crate's code, schema validation against the wire schema and temp directories.
#![allow(dead_code, clippy::chunks_exact_to_as_chunks)]

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

use serde_json::{Value, json};

pub const REPO: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/../..");
const WIRE_SCHEMA: &str = include_str!("../../../coach-wire/schema/wire.schema.json");

pub fn repo_file(relative: &str) -> PathBuf {
    Path::new(REPO).join(relative)
}

pub fn read_json(path: &Path) -> Value {
    serde_json::from_slice(&std::fs::read(path).unwrap()).unwrap()
}

pub fn scratch(name: &str) -> PathBuf {
    static COUNTER: AtomicU64 = AtomicU64::new(0);
    let dir = Path::new(env!("CARGO_TARGET_TMPDIR")).join(format!(
        "{name}-{}-{}",
        std::process::id(),
        COUNTER.fetch_add(1, Ordering::Relaxed)
    ));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    dir
}

pub fn write_doc(dir: &Path, doc: &Value) -> PathBuf {
    let path = dir.join("analysis.json");
    std::fs::write(&path, serde_json::to_vec(doc).unwrap()).unwrap();
    path
}

pub fn view(out: &Path, file: &str) -> Value {
    read_json(&out.join(file))
}

// ---- schema ----

pub fn validator(name: &str) -> jsonschema::Validator {
    let mut root: Value = serde_json::from_str(WIRE_SCHEMA).unwrap();
    root["$ref"] = json!(format!("#/$defs/{name}"));
    jsonschema::validator_for(&root).unwrap()
}

pub fn assert_valid(name: &str, value: &Value) {
    let validator = validator(name);
    let errors: Vec<String> = validator
        .iter_errors(value)
        .take(5)
        .map(|e| format!("{e} at {}", e.instance_path()))
        .collect();
    assert!(errors.is_empty(), "{name} does not validate: {errors:#?}");
}

pub fn wire_schema() -> Value {
    serde_json::from_str(WIRE_SCHEMA).unwrap()
}

// ---- CCOL ----

pub struct Ccol {
    pub header: Value,
    /// Channel id -> laps x bins values, lap-major, NaN for missing.
    pub columns: BTreeMap<String, Vec<f64>>,
    pub dtypes: BTreeMap<String, String>,
    pub total_len: usize,
}

pub fn decode_ccol(bytes: &[u8]) -> Ccol {
    assert_eq!(&bytes[..4], b"CCOL");
    assert_eq!(u16::from_le_bytes([bytes[4], bytes[5]]), 1);
    assert_eq!(u16::from_le_bytes([bytes[6], bytes[7]]), 0);
    let header_len = u32::from_le_bytes(bytes[8..12].try_into().unwrap()) as usize;
    let header: Value = serde_json::from_slice(&bytes[12..12 + header_len]).unwrap();
    let data_start = (12 + header_len).div_ceil(8) * 8;
    assert!(
        bytes[12 + header_len..data_start].iter().all(|b| *b == 0),
        "header padding"
    );
    let bins = header["bins"].as_u64().unwrap() as usize;
    let laps = header["laps"].as_array().unwrap().len();
    let mut columns = BTreeMap::new();
    let mut dtypes = BTreeMap::new();
    for channel in header["channels"].as_array().unwrap() {
        let id = channel["id"].as_str().unwrap().to_string();
        let dtype = channel["dtype"].as_str().unwrap();
        let offset = data_start + channel["offset"].as_u64().unwrap() as usize;
        let length = channel["byte_length"].as_u64().unwrap() as usize;
        assert_eq!(offset % 8, 0, "{id} alignment");
        let width = match dtype {
            "f64" => 8,
            "i16" => 2,
            "u8" => 1,
            other => panic!("dtype {other}"),
        };
        assert_eq!(length, laps * bins * width, "{id} length");
        let block = &bytes[offset..offset + length];
        let missing = channel["missing"].as_i64();
        let values: Vec<f64> = match dtype {
            "f64" => {
                assert!(missing.is_none());
                block
                    .chunks_exact(8)
                    .map(|c| f64::from_le_bytes(c.try_into().unwrap()))
                    .collect()
            }
            "i16" => block
                .chunks_exact(2)
                .map(|c| i64::from(i16::from_le_bytes(c.try_into().unwrap())))
                .map(|v| {
                    if Some(v) == missing {
                        f64::NAN
                    } else {
                        v as f64
                    }
                })
                .collect(),
            _ => block
                .iter()
                .map(|v| i64::from(*v))
                .map(|v| {
                    if Some(v) == missing {
                        f64::NAN
                    } else {
                        v as f64
                    }
                })
                .collect(),
        };
        dtypes.insert(id.clone(), dtype.to_string());
        columns.insert(id, values);
    }
    Ccol {
        header,
        columns,
        dtypes,
        total_len: bytes.len(),
    }
}

/// Expected channel values straight from the backend document: laps x bins, NaN for missing.
pub fn expected_columns(
    doc: &Value,
    lap_filter: Option<&dyn Fn(i64) -> bool>,
) -> (Vec<i64>, usize, BTreeMap<String, Vec<f64>>) {
    let traces = &doc["lap_traces"];
    let bins = traces["distance_bins_per_lap"].as_u64().unwrap_or(160) as usize;
    let mut laps = Vec::new();
    let mut by_key: BTreeMap<String, Vec<f64>> = BTreeMap::new();
    for trace in traces["traces"].as_array().unwrap() {
        let lap = trace["lap"].as_i64().unwrap();
        if lap_filter.is_some_and(|f| !f(lap)) {
            continue;
        }
        laps.push(lap);
        let row_start = laps.len() - 1;
        let mut note = |key: String, bin: usize, value: f64| {
            let column = by_key.entry(key).or_default();
            column.resize((row_start + 1) * bins, f64::NAN);
            column[row_start * bins + bin] = value;
        };
        for point in trace["points"].as_array().unwrap() {
            let bin =
                ((point["lap_pct"].as_f64().unwrap() * bins as f64).floor() as usize).min(bins - 1);
            for (key, value) in point.as_object().unwrap() {
                if key == "additional_signals" {
                    for (id, v) in value.as_object().unwrap() {
                        if let Some(x) = v.as_f64() {
                            note(format!("additional_signals.{id}"), bin, x);
                        }
                    }
                } else if let Some(x) = value.as_f64() {
                    note(key.clone(), bin, x);
                }
            }
        }
    }
    let total = laps.len() * bins;
    for column in by_key.values_mut() {
        column.resize(total, f64::NAN);
    }
    (laps, bins, by_key)
}

pub fn same(a: f64, b: f64) -> bool {
    (a.is_nan() && b.is_nan()) || a == b
}

/// Asserts that every CCOL channel equals the backend values at its `source_key`, bit for bit for
/// floats, and that nothing but the catalog's signals was written.
pub fn assert_ccol_matches(
    ccol: &Ccol,
    catalog: &Value,
    doc: &Value,
    lap_filter: Option<&dyn Fn(i64) -> bool>,
) {
    let (laps, bins, expected) = expected_columns(doc, lap_filter);
    let header_laps: Vec<i64> = ccol.header["laps"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_i64().unwrap())
        .collect();
    assert_eq!(header_laps, laps);
    assert_eq!(ccol.header["bins"].as_u64().unwrap() as usize, bins);
    let signals = catalog["signals"].as_array().unwrap();
    for (id, values) in &ccol.columns {
        let signal = signals
            .iter()
            .find(|s| s["id"] == *id)
            .unwrap_or_else(|| panic!("{id} is not in the catalog"));
        assert_eq!(signal["dtype"].as_str().unwrap(), ccol.dtypes[id]);
        let key = signal["source_key"].as_str().unwrap();
        let want = expected
            .get(key)
            .unwrap_or_else(|| panic!("no backend values for {key}"));
        assert_eq!(values.len(), want.len(), "{id}");
        for (i, (a, b)) in values.iter().zip(want).enumerate() {
            assert!(
                same(*a, *b) && (a.is_nan() || a.to_bits() == b.to_bits() || *a == *b),
                "{id} ({key}) lap index {} bin {}: decoded {a} vs backend {b}",
                i / bins,
                i % bins
            );
        }
    }
}

// ---- synthetic analysis documents ----

pub struct Options {
    pub laps: usize,
    /// Points per lap (bins that carry a point); bins per lap stay 160.
    pub points: usize,
    pub extras: &'static [&'static str],
    pub with_investigation: bool,
    pub geometry: bool,
    pub runs: usize,
}

impl Default for Options {
    fn default() -> Options {
        Options {
            laps: 12,
            points: 150,
            extras: &[
                "lf-pressure",
                "on-pit-road",
                "overall-position",
                "clutch",
                "fuel-level",
            ],
            with_investigation: true,
            geometry: true,
            runs: 2,
        }
    }
}

fn r(value: f64, digits: i32) -> f64 {
    let scale = 10f64.powi(digits);
    (value * scale).round() / scale
}

/// A small deterministic generator (no external crates).
struct Lcg(u64);

impl Lcg {
    fn next(&mut self) -> f64 {
        self.0 = self
            .0
            .wrapping_mul(6364136223846793005)
            .wrapping_add(1442695040888963407);
        ((self.0 >> 33) as f64) / ((1u64 << 31) as f64)
    }
}

pub fn lap_row(lap: usize, flag: &str) -> Value {
    json!({
        "caution_fraction": if flag == "green" { 0.0 } else { 1.0 },
        "clean_context": {"on_track_fraction": 1.0, "traffic_proximity_fraction": 0.0, "traffic_rule": "car ahead within max(25 m, 0.75 s) or behind within max(15 m, 0.40 s) for at least 10% of observed lap time", "traffic_screened": true},
        "complete": true,
        "conditions": {"air_density_lb_ft3": 0.072, "air_pressure_inhg": 29.19, "air_temperature_f": 72.3, "fog_percent": 0.0, "precipitation_percent": 0.0, "relative_humidity_percent": 79.5, "sky": "Mostly cloudy", "track_temperature_f": 92.0, "track_usage": "clean", "track_usage_percent": null, "track_wetness_state": 1.0, "weather_declared_wet": false, "wind_direction_degrees": 77.5, "wind_speed_mph": 10.3},
        "controls": {"brake_energy_proxy": 0.0, "brake_max": 0.0, "brake_mean": 0.0, "brake_steer_overlap_s": 0.0, "lateral_g_exposure_proxy": 21.574, "long_accel_mean_mps2": -0.398, "steering_abs_mean_rad": 0.29, "steering_corrections": 13, "steering_work_proxy": 478.453, "throttle_mean": 0.113},
        "damage_repair_context": {"automatic_coaching_reference_eligible": true, "episode_ids": [], "exclusion_reason_codes": [], "lap": lap, "run_reference_status": "no_recorded_damage_disruption_exclusion"},
        "end_index": 6123 + lap * 1000,
        "end_time": 315.983 + lap as f64 * 31.0,
        "flag_state": flag,
        "flag_states": [if flag == "green" { "green" } else { "yellow" }],
        "fuel": {"end_l": 68.273, "start_l": 68.533, "used_gal": 0.069, "used_l": 0.26},
        "green_fraction": if flag == "green" { 1.0 } else { 0.0 },
        "lap": lap,
        "lap_time_s": r(31.0 + (lap % 7) as f64 * 0.013, 3),
        "pit_entry": false,
        "pit_exit": false,
        "pit_time_s": 0.0,
        "position": {"best": 5, "class_end": 5, "class_start": 5, "end": 5, "start": 5, "worst": 5},
        "racing_state_fraction": 1.0,
        "speed": {"average_mph": 77.476, "maximum_mph": 102.365, "minimum_mph": 51.366},
        "start_index": 5167 + lap * 1000,
        "start_time": 268.183 + lap as f64 * 31.0,
        "vehicle_dynamics": {"abs": {"active_s": 0.0, "cut_max": 1.0, "cut_mean": 1.0}, "motion": {"pitch_rate_deg_s": {"abs_max": 5.43, "abs_mean": 1.275, "abs_p95": 3.367}, "roll_rate_deg_s": {"abs_max": 12.562, "abs_mean": 1.32, "abs_p95": 3.168}, "yaw_rate_deg_s": {"abs_max": 21.736, "abs_mean": 8.366, "abs_p95": 17.836}}, "wheel_speed": {"channels": ["LFspeed", "LRspeed", "RFspeed", "RRspeed"], "ratio_vs_vehicle_speed": {"LF": {"median": -0.0037, "p05": -0.0082, "p95": 0.0005}, "LR": {"median": -0.0042, "p05": -0.0111, "p95": 0.0015}}, "tire_distance_m": {"LF": 1700.0, "LR": 1700.0}, "valid_ratio_samples": 957}},
    })
}

pub fn run_row(number: usize, laps: &[usize]) -> Value {
    json!({
        "caution_laps": 0.0,
        "coaching_reference_lap_numbers": laps,
        "damage_repair_context": {"automatic_coaching_reference_eligible": true, "reason_codes": [], "exclusion_reason_codes": [], "run_number": number},
        "driving_load": {"early_brake_vs_late_percent": -2.5, "early_steer_vs_late_percent": 8.1},
        "ended_under_caution": false,
        "ended_with_pit_stop": true,
        "fuel": {"used_gal": 7.326},
        "green_laps": laps.len() as f64,
        "lap_numbers": laps,
        "pace": {"early_average_lap_s": 31.317, "early_to_late_delta_s": 0.617, "green_lap_time_slope_s_per_lap": 0.1196, "late_average_lap_s": 31.933},
        "pit_service": {"start_time": 1347.5, "end_time": 1361.4, "fuel_added_l": 6.3, "tires_changed_observed": ["LF"], "penalty_served_s": 0.0},
        "run_number": number,
        "tire_measurement_status": "measured_at_stop",
        "tire_observation": {"lowest_remaining_percent": 84.18, "lowest_remaining_tire": "RF", "tires": {"LF": {"average_remaining_percent": 96.37, "pressure": {"psi": 30.1, "kind": "hot"}}}},
        "future_backend_field": {"kept": [1, 2, 3]},
    })
}

fn point(lap: usize, bin: usize, bins: usize, rng: &mut Lcg, extras: &[&str]) -> Value {
    let phase = (bin as f64 + 0.5) / bins as f64;
    let angle = std::f64::consts::TAU * phase;
    let speed = 100.0 + 40.0 * angle.sin() + lap as f64 * 0.01 + rng.next();
    let mut additional = serde_json::Map::new();
    for id in extras {
        let value = match *id {
            "on-pit-road" => 0.0,
            "overall-position" => 5.0,
            "clutch" => 100.0,
            "lf-pressure" => r(18.0 + rng.next(), 2),
            _ => r(17.0 + rng.next() * 3.0, 3),
        };
        // an occasional gap, as when a channel was not recorded for that bin
        if !(bin % 29 == 7 && *id == "fuel-level") {
            additional.insert((*id).to_string(), json!(value));
        }
    }
    let nullable = |x: f64, digits: i32, skip: bool| {
        if skip {
            Value::Null
        } else {
            json!(r(x, digits))
        }
    };
    json!({
        "additional_signals": additional,
        "brake": nullable((angle.cos() * 0.5 + 0.5).powi(8), 4, false),
        "brake_mean": 0.0,
        "gear": if bin % 17 == 3 { Value::Null } else { json!(3 + (bin / 40) as i64) },
        "lap_pct": r((bin as f64 + 0.5) / bins as f64, 6),
        "lateral_g": nullable(angle.sin() * 1.5, 4, false),
        "latitude": json!(r(43.35 + 0.01 * angle.sin(), 8)),
        "longitude": json!(r(-71.46 + 0.012 * angle.cos(), 8)),
        "longitudinal_g": nullable(angle.cos() * 0.4, 4, false),
        "rpm": json!(r(3000.0 + 2500.0 * (angle * 2.0).sin(), 0)),
        "samples": 8,
        "session_time_s": r(lap as f64 * 31.0 + phase * 31.0, 6),
        "slip_angle_deg": nullable(angle.sin() * 2.0, 3, false),
        "speed_max_mph": r(speed + 0.4, 3),
        "speed_min_mph": r(speed - 0.4, 3),
        "speed_mph": r(speed, 3),
        "steering_abs_peak_rad": nullable(angle.sin().abs() * 0.4, 5, bin % 31 == 5),
        "steering_rad": nullable(angle.sin() * 0.4, 5, bin % 31 == 5),
        "throttle": nullable(0.5 + 0.5 * angle.sin(), 4, false),
        "throttle_min": 0.0,
        "tire_stress_proxy": nullable(0.2 + 0.1 * angle.cos(), 4, false),
        "yaw_rate_deg_s": nullable(angle.cos() * 9.0, 3, false),
    })
}

fn geometry() -> Value {
    let n = 200;
    let path: Vec<Value> = (0..n)
        .map(|i| {
            let phase = i as f64 / n as f64;
            let a = std::f64::consts::TAU * phase;
            json!({"lap_pct": r(phase, 5), "observations": 6, "x": r(0.5 + 0.4 * a.cos(), 6), "y": r(0.5 + 0.3 * a.sin(), 6)})
        })
        .collect();
    json!({
        "cache": {"path": "<path>"},
        "contributing_source_sha256": ["a".repeat(64)],
        "coordinate_system": "normalized_local_vector",
        "geometry_hash": "b".repeat(64),
        "geometry_provenance": {"normalization_transform": {"source_bounds": {"minimum_x": -71.472, "maximum_x": -71.448, "minimum_y": 43.34, "maximum_y": 43.36}, "normalization_scale": 0.024}, "observations": [{"observation_id": "o1", "source_sha256": ["a".repeat(64)]}], "selected_observation_id": "o1"},
        "main_path": path,
        "observed_source_sha256": ["a".repeat(64), "c".repeat(64)],
        "pit_commitment_line": {"a": {"x": 0.1, "y": 0.2}, "b": {"x": 0.12, "y": 0.2}},
        "pit_entry_path": [{"x": 0.1, "y": 0.2, "lap_pct": 0.9}, {"x": 0.12, "y": 0.22, "lap_pct": 0.92}],
        "pit_exit_path": [],
        "pit_lane": [{"x": 0.1, "y": 0.2}, {"x": 0.2, "y": 0.2}, {"x": 0.3, "y": null}],
        "pit_merge_line": null,
        "quality": {"closure_distance": 0.0, "lap_percent_coverage": 1.0, "main_loop_complete": true, "maximum_lap_percent_gap": 0.01, "main_path_points": 200},
        "schema_version": 1,
        "source_sha256": ["a".repeat(64)],
        "start_finish_line": {"a": {"x": 0.9, "y": 0.5, "lap_pct": 0.0}, "b": {"x": 0.9, "y": 0.52, "lap_pct": 0.0}},
        "status": "usable",
        "track_config": "Oval",
        "track_configuration_key": "9001-oval",
        "track_id": 9001,
        "track_name": "Synthetic Speedway",
        "transform": {"source_bounds": {"minimum_x": -71.472, "maximum_x": -71.448, "minimum_y": 43.34, "maximum_y": 43.36}, "normalization_scale": 0.024},
        "unavailable_reasons": [],
    })
}

pub fn analysis_doc(id: &str, options: &Options) -> Value {
    let bins = 160;
    let mut rng = Lcg(0x9e3779b97f4a7c15);
    let flag = |lap: usize| {
        if lap.is_multiple_of(9) {
            "caution"
        } else {
            "green"
        }
    };
    let laps: Vec<Value> = (1..=options.laps)
        .map(|lap| lap_row(lap, flag(lap)))
        .collect();
    let step = options.laps.div_ceil(options.runs.max(1));
    let runs: Vec<Value> = (0..options.runs)
        .map(|k| {
            let numbers: Vec<usize> =
                ((k * step + 1)..=((k + 1) * step).min(options.laps)).collect();
            run_row(k + 1, &numbers)
        })
        .collect();
    let stride = (bins as f64 / options.points as f64).max(1.0);
    let traces: Vec<Value> = (1..=options.laps)
        .map(|lap| {
            let mut used = std::collections::BTreeSet::new();
            let points: Vec<Value> = (0..options.points)
                .map(|i| ((i as f64 * stride) as usize).min(bins - 1))
                .filter(|b| used.insert(*b))
                .map(|b| point(lap, b, bins, &mut rng, options.extras))
                .collect();
            json!({
                "caution_fraction": if flag(lap) == "green" { 0.0 } else { 1.0 },
                "complete": true,
                "conditions": {"sky": "Mostly cloudy", "track_temperature_f": 92.0, "air_temperature_f": 72.3, "wind_speed_mph": 10.3, "weather_declared_wet": false},
                "flag_state": flag(lap),
                "flag_states": [flag(lap)],
                "fuel_used_gal": 0.069,
                "green_fraction": if flag(lap) == "green" { 1.0 } else { 0.0 },
                "lap": lap,
                "lap_time_s": r(31.0 + (lap % 7) as f64 * 0.013, 3),
                "pit_entry": false,
                "pit_exit": false,
                "pit_time_s": 0.0,
                "points": points,
                "unknown_trace_field": {"kept": true},
            })
        })
        .collect();
    let catalog: Vec<Value> = options
        .extras
        .iter()
        .map(|id| json!({"category": "Controls", "description": "x", "evidence_type": "measured", "id": id, "name": format!("Name of {id}"), "source_channels": ["X"], "unit": if *id == "lf-pressure" { "psi" } else { "%" }}))
        .collect();
    let investigation = if options.with_investigation {
        read_json(&repo_file("test-data/race-investigation.json"))
    } else {
        json!({})
    };
    let mut doc = json!({
        "schema_version": 2,
        "analysis_id": id,
        "analysis_profile_version": "post-race-foundations-v23",
        "analysis_profile": {"x": 1},
        "analyzed_at": "2026-10-02T12:00:00+00:00",
        "coaching_signals": [{"finding": "x"}],
        "conditions": {"air_temp_c": 22.0, "track_temp_c": 33.0},
        "corner_tire_age": {"runs": [{"run_number": 1}], "schema_version": 1},
        "damage_repair": {"status": "none", "summary": {"pit_road_episodes": 1, "tow_episodes": 0}, "episodes": [], "limitations": [], "incident_points": {"events": []}},
        "data_quality": {"confidence": "high", "missing": []},
        "driver_adjustments": {},
        "field_pace": {"schema_version": 1, "status": "unavailable", "cars": [], "relative": []},
        "garage61_representative_laps": {"representative_laps": []},
        "groove_evolution": {"schema_version": 2},
        "identity": {
            "car_id": 9002, "car_name": "NASCAR Test Car", "car_path": "stockcars2 testcar", "category": "Oval", "customer_id": 1,
            "driver_irating": null, "driver_name": "Test Driver", "event_type": "Race", "is_fixed_setup": false,
            "season_id": 202603, "session_id": 7001, "session_start": "2026-05-28T20:26:40+00:00", "session_unique_id": 3,
            "setup": {"Tires": {"LeftFront": {"ColdPressure": "25.0 psi"}}}, "setup_fingerprint": "08c544f2b82afbb0", "setup_parameter_count": 1,
            "subsession_id": 8001, "track_config": "Oval", "track_id": 9001, "track_name": "Synthetic Speedway", "track_path": "synthetic speedway",
            "unknown_identity_field": {"deep": {"kept": 1}},
        },
    });
    let lap_traces = json!({
        "additional_signal_catalog": catalog,
        "distance_bins_per_lap": bins,
        "downsampling": "bins",
        "schema_version": 2,
        "sector_start_pcts": [0.5, 0.0],
        "steering": {"basis": "road_wheel", "definition": "d", "degree_digits": 2, "label": "Steering (road wheel)", "ratio_source": "setup", "reason": null, "setup_steering_offset": null, "status": "recorded", "steering_ratio": 12.0, "steering_ratio_text": "12.0:1", "unit": "deg", "value_unit": "rad"},
        "tire_stress": {"evidence_class": "proxy"},
        "trace_count": options.laps,
        "traces": traces,
    });
    let rest = json!({
        "laps": laps,
        "pace_attribution": {"schema_version": 1},
        "race_grades": {"overall_grade": "B", "rubric_version": "v1", "categories": [{"key": "pace", "label": "Pace", "grade": "B", "score": 80.0}], "unavailable_categories": []},
        "race_investigation": investigation,
        "race_replay": {"available": false},
        "race_summary": {"recorded_laps": options.laps, "scheduled_laps": options.laps, "pit_stops_detected": 1, "starting_position": 5, "final_recorded_position": 5},
        "race_timeline": {"events": []},
        "runs": runs,
        "setup_telemetry": {"summary": {}},
        "source": {
            "analysis_cache": {"hit": false},
            "channel_coverage": {"analyzed_count": 7},
            "fingerprints": [{"sha256": "c".repeat(64), "path": "<path>", "size": 1}, {"sha256": "d".repeat(64)}],
            "selection": {"sim_session_num": 1, "sim_session_type": "Race", "subsession_id": 8001},
            "telemetry_files": ["<path>"],
        },
        "strategy": {"confidence": "high", "forecast": {"status": "usable"}, "pit_assessments": []},
        "strategy_planning": {"schema_version": 1},
        "technical_insights": [{"area": "pace", "finding": "x"}],
        "tire_learning": {"observations": 0},
        "track_geometry": if options.geometry { geometry() } else { json!({"status": "unavailable", "main_path": []}) },
        "track_profile": {"bins": 200, "detected_corner_segments": [{"segment": 1, "start_pct": 0.1, "end_pct": 0.2, "label": "Load zone 1"}], "profile": [{"bin": 0}], "shape": [{"lap_pct": 0.0, "x": 0.1, "y": 0.2}, {"lap_pct": 0.5, "x": 0.4, "y": 0.2}, {"lap_pct": 0.9, "x": 0.4, "y": 0.6}]},
    });
    let object = doc.as_object_mut().unwrap();
    object.insert("lap_traces".into(), lap_traces);
    object.extend(rest.as_object().unwrap().clone());
    doc
}

pub fn nested(doc: &Value) -> Value {
    let mut view = doc.clone();
    let id = view["analysis_id"].clone();
    let object = view.as_object_mut().unwrap();
    object.remove("analysis_id");
    object.insert("schema_version".into(), json!(1));
    json!({"analysis_id": id, "analysis_view": view})
}

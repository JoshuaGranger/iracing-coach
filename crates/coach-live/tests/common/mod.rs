//! Helpers shared by the vector tests: loading the C#-derived engine vectors and building a
//! `Sample` from their JSON.
#![allow(dead_code)]

use std::path::PathBuf;
use std::sync::Arc;

use coach_live::{Sample, SessionContext};
use serde_json::{Map, Value};

pub fn vectors_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/vectors")
}

pub fn load(name: &str) -> Value {
    let path = vectors_dir().join(name);
    let text = std::fs::read_to_string(&path).unwrap_or_else(|e| panic!("{}: {e}", path.display()));
    serde_json::from_str(&text).unwrap_or_else(|e| panic!("{}: {e}", path.display()))
}

pub fn f64_of(value: &Value) -> Option<f64> {
    match value {
        Value::Null => None,
        Value::Number(n) => n.as_f64(),
        Value::String(s) => match s.as_str() {
            "NaN" => Some(f64::NAN),
            "Infinity" => Some(f64::INFINITY),
            "-Infinity" => Some(f64::NEG_INFINITY),
            other => panic!("unexpected number text {other}"),
        },
        other => panic!("unexpected number {other}"),
    }
}

pub fn i32_of(value: &Value) -> Option<i32> {
    value.as_i64().map(|v| i32::try_from(v).expect("i32"))
}

pub fn arr_i32(value: &Value) -> Vec<i32> {
    value.as_array().map_or_else(Vec::new, |a| {
        a.iter().map(|v| i32_of(v).expect("int")).collect()
    })
}

pub fn arr_f32(value: &Value) -> Vec<f32> {
    value.as_array().map_or_else(Vec::new, |a| {
        a.iter().map(|v| f64_of(v).expect("float") as f32).collect()
    })
}

pub fn context(session: &Value) -> Arc<SessionContext> {
    let types = session["session_types"]
        .as_array()
        .unwrap()
        .iter()
        .map(|pair| {
            (
                pair[0].as_i64().unwrap(),
                pair[1].as_str().unwrap().to_string(),
            )
        })
        .collect();
    Arc::new(SessionContext {
        subsession_id: session["subsession_id"].as_i64(),
        session_types: types,
        steering_ratio: session["steering_ratio"].as_f64(),
        ..SessionContext::default()
    })
}

pub fn sample_from(values: &Map<String, Value>) -> Sample {
    let get = |name: &str| values.get(name).unwrap_or(&Value::Null);
    let f = |name: &str| f64_of(get(name));
    let i = |name: &str| i32_of(get(name));
    let b = |name: &str| get(name).as_bool();
    Sample {
        player_car_idx: i("player_car_idx"),
        player_car_position: i("player_car_position"),
        player_car_class_position: i("player_car_class_position"),
        car_idx_position: arr_i32(get("car_idx_position")),
        car_idx_class_position: arr_i32(get("car_idx_class_position")),
        car_idx_lap: arr_i32(get("car_idx_lap")),
        car_idx_f2_time: arr_f32(get("car_idx_f2_time")),
        car_idx_last_lap_time: arr_f32(get("car_idx_last_lap_time")),
        session_flags: get("session_flags")
            .as_u64()
            .map(|v| u32::try_from(v).expect("u32")),
        player_car_tow_time: f("player_car_tow_time"),
        lap: i("lap"),
        session_laps_remain_ex: f("session_laps_remain_ex"),
        session_laps_remain: f("session_laps_remain"),
        lap_last_lap_time: f("lap_last_lap_time"),
        fuel_level: f("fuel_level"),
        fuel_level_pct: f("fuel_level_pct"),
        track_temp_crew: f("track_temp_crew"),
        track_temp: f("track_temp"),
        air_temp: f("air_temp"),
        dc_brake_bias: f("dc_brake_bias"),
        on_pit_road: b("on_pit_road"),
        pit_repair_left: f("pit_repair_left"),
        pit_opt_repair_left: f("pit_opt_repair_left"),
        steering_wheel_angle: f("steering_wheel_angle"),
        throttle: f("throttle"),
        brake: f("brake"),
        brake_abs_active: b("brake_abs_active"),
        gear: i("gear"),
        rpm: f("rpm"),
        yaw_rate: f("yaw_rate"),
        lat_accel: f("lat_accel"),
        long_accel: f("long_accel"),
        speed: f("speed"),
        lap_dist_pct: f("lap_dist_pct"),
        lat: f("lat"),
        lon: f("lon"),
        session_time: f("session_time"),
        session_num: i("session_num"),
        session_unique_id: get("session_unique_id").as_i64(),
        player_car_my_incident_count: i("player_car_my_incident_count"),
        player_incidents: i("player_incidents"),
        is_on_track: b("is_on_track"),
        is_in_garage: b("is_in_garage"),
        ..Sample::default()
    }
}

//! `Sample::read_row` over real SDK bytes: the engine vectors' SDK values are written into a
//! synthetic live map, parsed with `coach_ibt::VarTable::parse_live`, and read back into a `Sample`
//! that must equal the one built straight from the vector JSON. This pins every variable name,
//! reader choice and the `BrakeABSactive` fallback of table 4.3.

mod common;

use coach_ibt::{SdkHeader, VarTable};
use coach_live::Sample;
use common::{f64_of, load, sample_from};
use serde_json::{Map, Value};

/// SDK types: 1 bool, 2 int, 3 bitfield, 4 float, 5 double.
const VARS: &[(&str, i32, i32, &str)] = &[
    ("PlayerCarIdx", 2, 1, "player_car_idx"),
    ("PlayerCarPosition", 2, 1, "player_car_position"),
    ("PlayerCarClassPosition", 2, 1, "player_car_class_position"),
    ("CarIdxPosition", 2, 8, "car_idx_position"),
    ("CarIdxClassPosition", 2, 8, "car_idx_class_position"),
    ("CarIdxLap", 2, 8, "car_idx_lap"),
    ("CarIdxF2Time", 4, 8, "car_idx_f2_time"),
    ("CarIdxLastLapTime", 4, 8, "car_idx_last_lap_time"),
    ("SessionFlags", 3, 1, "session_flags"),
    ("PlayerCarTowTime", 4, 1, "player_car_tow_time"),
    ("Lap", 2, 1, "lap"),
    ("SessionLapsRemainEx", 5, 1, "session_laps_remain_ex"),
    ("SessionLapsRemain", 2, 1, "session_laps_remain"),
    ("LapLastLapTime", 4, 1, "lap_last_lap_time"),
    ("FuelLevel", 4, 1, "fuel_level"),
    ("FuelLevelPct", 4, 1, "fuel_level_pct"),
    ("TrackTempCrew", 4, 1, "track_temp_crew"),
    ("TrackTemp", 4, 1, "track_temp"),
    ("AirTemp", 4, 1, "air_temp"),
    ("dcBrakeBias", 4, 1, "dc_brake_bias"),
    ("OnPitRoad", 1, 1, "on_pit_road"),
    ("PitRepairLeft", 4, 1, "pit_repair_left"),
    ("PitOptRepairLeft", 4, 1, "pit_opt_repair_left"),
    ("SteeringWheelAngle", 4, 1, "steering_wheel_angle"),
    ("Throttle", 4, 1, "throttle"),
    ("Brake", 4, 1, "brake"),
    ("BrakeABSactive", 1, 1, "brake_abs_active"),
    ("Gear", 2, 1, "gear"),
    ("RPM", 4, 1, "rpm"),
    ("YawRate", 4, 1, "yaw_rate"),
    ("LatAccel", 4, 1, "lat_accel"),
    ("LongAccel", 4, 1, "long_accel"),
    ("Speed", 4, 1, "speed"),
    ("LapDistPct", 4, 1, "lap_dist_pct"),
    ("Lat", 5, 1, "lat"),
    ("Lon", 5, 1, "lon"),
    ("SessionTime", 5, 1, "session_time"),
    ("SessionNum", 2, 1, "session_num"),
    ("SessionUniqueID", 2, 1, "session_unique_id"),
    (
        "PlayerCarMyIncidentCount",
        2,
        1,
        "player_car_my_incident_count",
    ),
    ("PlayerIncidents", 2, 1, "player_incidents"),
    ("IsOnTrack", 1, 1, "is_on_track"),
    ("IsInGarage", 1, 1, "is_in_garage"),
];

const HEADER_LEN: usize = 112;
const VAR_HEADER_LEN: usize = 144;

fn width(ty: i32) -> usize {
    match ty {
        1 => 1,
        5 => 8,
        _ => 4,
    }
}

struct Map_ {
    bytes: Vec<u8>,
    row_offset: usize,
    row_len: usize,
    offsets: Vec<usize>,
}

fn put_i32(bytes: &mut [u8], at: usize, value: i32) {
    bytes[at..at + 4].copy_from_slice(&value.to_le_bytes());
}

fn build_map(omit: &[&str]) -> Map_ {
    let vars: Vec<&(&str, i32, i32, &str)> = VARS.iter().filter(|v| !omit.contains(&v.0)).collect();
    let mut offsets = Vec::new();
    let mut cursor: usize = 0;
    for (_, ty, count, _) in &vars {
        let w = width(*ty);
        cursor = cursor.div_ceil(w) * w;
        offsets.push(cursor);
        cursor += w * *count as usize;
    }
    let row_len = cursor.div_ceil(16) * 16;
    let session_offset = (HEADER_LEN + vars.len() * VAR_HEADER_LEN).div_ceil(16) * 16;
    let row_offset = session_offset + 64;
    let mut bytes = vec![0u8; row_offset + row_len];
    put_i32(&mut bytes, 0, 2);
    put_i32(&mut bytes, 4, 1);
    put_i32(&mut bytes, 8, 60);
    put_i32(&mut bytes, 12, 1);
    put_i32(&mut bytes, 16, 4);
    put_i32(&mut bytes, 20, session_offset as i32);
    put_i32(&mut bytes, 24, vars.len() as i32);
    put_i32(&mut bytes, 28, HEADER_LEN as i32);
    put_i32(&mut bytes, 32, 1);
    put_i32(&mut bytes, 36, row_len as i32);
    put_i32(&mut bytes, 48, 1); // tick
    put_i32(&mut bytes, 52, row_offset as i32);
    for (index, (name, ty, count, _)) in vars.iter().enumerate() {
        let start = HEADER_LEN + index * VAR_HEADER_LEN;
        put_i32(&mut bytes, start, *ty);
        put_i32(&mut bytes, start + 4, offsets[index] as i32);
        put_i32(&mut bytes, start + 8, *count);
        bytes[start + 16..start + 16 + name.len()].copy_from_slice(name.as_bytes());
    }
    Map_ {
        bytes,
        row_offset,
        row_len,
        offsets,
    }
}

fn write_scalar(row: &mut [u8], at: usize, ty: i32, value: &Value) {
    match ty {
        1 => row[at] = u8::from(value.as_bool().unwrap()),
        2 => row[at..at + 4].copy_from_slice(&(value.as_i64().unwrap() as i32).to_le_bytes()),
        3 => row[at..at + 4].copy_from_slice(&(value.as_u64().unwrap() as u32).to_le_bytes()),
        4 => row[at..at + 4].copy_from_slice(&(f64_of(value).unwrap() as f32).to_le_bytes()),
        5 => row[at..at + 8].copy_from_slice(&f64_of(value).unwrap().to_le_bytes()),
        _ => unreachable!(),
    }
}

fn fill_row(map: &Map_, omit: &[&str], values: &Map<String, Value>) -> Vec<u8> {
    let mut row = vec![0u8; map.row_len];
    let vars: Vec<&(&str, i32, i32, &str)> = VARS.iter().filter(|v| !omit.contains(&v.0)).collect();
    for (index, (_, ty, count, key)) in vars.iter().enumerate() {
        let Some(value) = values.get(*key).filter(|v| !v.is_null()) else {
            continue;
        };
        if *count == 1 {
            write_scalar(&mut row, map.offsets[index], *ty, value);
        } else {
            for (i, element) in value.as_array().unwrap().iter().enumerate() {
                write_scalar(&mut row, map.offsets[index] + i * width(*ty), *ty, element);
            }
        }
    }
    row
}

fn same(label: &str, actual: &Sample, expected: &Sample) {
    // Sample has no PartialEq; its Debug text is exact (shortest round-trip floats, NaN).
    assert_eq!(format!("{actual:?}"), format!("{expected:?}"), "{label}");
}

#[test]
fn read_row_reads_every_vector_value_back() {
    let doc = load("engine.json");
    let mut rows = 0;
    for scenario in doc["scenarios"].as_array().unwrap() {
        let name = scenario["name"].as_str().unwrap();
        let omit: Vec<&str> = scenario["omitted_variables"]
            .as_array()
            .unwrap()
            .iter()
            .map(|v| v.as_str().unwrap())
            .collect();
        let layout = build_map(&omit);
        let header = SdkHeader::parse(&layout.bytes[..HEADER_LEN]).unwrap();
        let table = VarTable::parse_live(&layout.bytes, &header).unwrap();
        assert_eq!(
            table.len(),
            VARS.len() - omit.len(),
            "{name}: variables parsed"
        );
        let mut values = Map::new();
        for (index, step) in scenario["steps"].as_array().unwrap().iter().enumerate() {
            for (key, value) in step["set"].as_object().unwrap() {
                values.insert(key.clone(), value.clone());
            }
            if !step["connected"].as_bool().unwrap() {
                continue;
            }
            let row = fill_row(&layout, &omit, &values);
            let mut actual = Sample {
                player_incidents: Some(-1),
                car_idx_lap: vec![9, 9],
                ..Sample::default()
            };
            actual.read_row(&table, &row);
            same(
                &format!("{name} step {index}"),
                &actual,
                &sample_from(&values),
            );
            rows += 1;
        }
    }
    assert!(rows > 1000);
}

#[test]
fn read_row_leaves_collector_fields_alone_and_clear_keeps_capacity() {
    let layout = build_map(&[]);
    let header = SdkHeader::parse(&layout.bytes[..HEADER_LEN]).unwrap();
    let table = VarTable::parse_live(&layout.bytes, &header).unwrap();
    let mut sample = Sample {
        captured_unix_ms: 42,
        connected: true,
        tick: 7,
        tick_rate: 60,
        ..Sample::default()
    };
    let row = vec![0u8; layout.row_len];
    sample.read_row(&table, &row);
    assert_eq!(
        (
            sample.captured_unix_ms,
            sample.connected,
            sample.tick,
            sample.tick_rate
        ),
        (42, true, 7, 60)
    );
    assert_eq!(sample.car_idx_position.len(), 8);
    let capacity = sample.car_idx_f2_time.capacity();
    sample.clear();
    assert!(sample.car_idx_position.is_empty() && sample.car_idx_f2_time.is_empty());
    assert!(sample.car_idx_f2_time.capacity() >= capacity.min(8));
    assert!(!sample.connected && sample.captured_unix_ms == 0 && sample.session.is_none());
    assert_eq!(sample.lap, None);
    let _ = layout.row_offset;
}

#[test]
fn brake_abs_falls_back_to_a_float_variable_above_one_half() {
    // BrakeABSactive published as a float (type 4): present, compared with 0.5; NaN is false.
    for (value, expected) in [
        (0.0_f32, false),
        (0.5, false),
        (0.51, true),
        (1.0, true),
        (f32::NAN, false),
    ] {
        let mut bytes = vec![0u8; 512];
        put_i32(&mut bytes, 4, 1);
        put_i32(&mut bytes, 24, 1);
        put_i32(&mut bytes, 28, HEADER_LEN as i32);
        put_i32(&mut bytes, 32, 1);
        put_i32(&mut bytes, 36, 16);
        put_i32(&mut bytes, 48, 1);
        put_i32(&mut bytes, 52, 300);
        put_i32(&mut bytes, 16, 4);
        put_i32(&mut bytes, 20, 256);
        put_i32(&mut bytes, HEADER_LEN, 4); // float
        put_i32(&mut bytes, HEADER_LEN + 4, 0);
        put_i32(&mut bytes, HEADER_LEN + 8, 1);
        bytes[HEADER_LEN + 16..HEADER_LEN + 30].copy_from_slice(b"BrakeABSactive");
        bytes[300..304].copy_from_slice(&value.to_le_bytes());
        let header = SdkHeader::parse(&bytes[..HEADER_LEN]).unwrap();
        let table = VarTable::parse_live(&bytes, &header).unwrap();
        let mut sample = Sample::default();
        sample.read_row(&table, &bytes[300..316]);
        assert_eq!(sample.brake_abs_active, Some(expected), "{value}");
    }
}

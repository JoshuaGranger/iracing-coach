//! Frame codec against the golden vectors in `docs/08-rewrite/contracts/vectors/live-frame/`
//! (written by `tools/gen_frame_vectors.py` from the written contract, not from this codec).

use std::path::PathBuf;

use coach_live::{
    ChannelValue, CueState, DecodedFrame, Engine, EngineConfig, FRAME_FIXED_HEADER_LEN, FrameError,
    LiveState, Sample, decode_frame, parse_header, set_backlog_flag,
};
use coach_wire::live_channels::{
    CHANNELS, ChannelType, Evidence, FlagState, GapTrend, Priority, RunPhase, TABLE_VERSION,
};
use serde_json::Value;

fn frames() -> Value {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../docs/08-rewrite/contracts/vectors/live-frame/frames.json");
    serde_json::from_str(
        &std::fs::read_to_string(&path).unwrap_or_else(|e| panic!("{}: {e}", path.display())),
    )
    .unwrap()
}

fn unhex(text: &str) -> Vec<u8> {
    (0..text.len() / 2)
        .map(|i| u8::from_str_radix(&text[2 * i..2 * i + 2], 16).unwrap())
        .collect()
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

fn blank_state() -> LiveState {
    Engine::new(EngineConfig::default()).on_tick(&Sample::default())
}

fn cue_from(json: &Value) -> Option<CueState> {
    if json.is_null() {
        return None;
    }
    let args = json["args"].as_array().unwrap();
    let mut slots = [0.0_f32; 4];
    for (slot, arg) in slots.iter_mut().zip(args) {
        *slot = arg.as_f64().unwrap() as f32;
    }
    Some(CueState {
        id: json["id"].as_u64().unwrap() as u8,
        priority: Priority::from_u8(json["priority"].as_u64().unwrap() as u8).unwrap(),
        evidence: Evidence::from_u8(json["evidence"].as_u64().unwrap() as u8).unwrap(),
        arg_count: args.len() as u8,
        args: slots,
    })
}

fn set_channel(st: &mut LiveState, id: &str, v: &Value) {
    let f = v.as_f64().map(|x| x as f32);
    let u = v.as_u64();
    let i = v.as_i64();
    match id {
        "speed" => st.speed = f,
        "throttle" => st.throttle = f,
        "brake" => st.brake = f,
        "steering_wheel_angle" => st.steering_wheel_angle = f,
        "steering_road_wheel" => st.steering_road_wheel = f,
        "steering_ratio" => st.steering_ratio = f,
        "gear" => st.gear = i.map(|x| x as i8),
        "rpm" => st.rpm = f,
        "yaw_rate" => st.yaw_rate = f,
        "lateral_accel" => st.lateral_accel = f,
        "longitudinal_accel" => st.longitudinal_accel = f,
        "lap_dist_pct" => st.lap_dist_pct = f,
        "latitude" => st.latitude = v.as_f64(),
        "longitude" => st.longitude = v.as_f64(),
        "lap" => st.lap = u.map(|x| x as u16),
        "laps_remaining" => st.laps_remaining = u.map(|x| x as u16),
        "position" => st.position = u.map(|x| x as u8),
        "class_position" => st.class_position = u.map(|x| x as u8),
        "flag_state" => st.flag_state = u.map(|x| FlagState::from_u8(x as u8).unwrap()),
        "session_flags" => st.session_flags = u.map(|x| x as u32),
        "abs_active" => st.abs_active = v.as_bool(),
        "brake_bias" => st.brake_bias = f,
        "fuel" => st.fuel = f,
        "fuel_pct" => st.fuel_pct = f,
        "fuel_laps_remaining" => st.fuel_laps_remaining = f,
        "fuel_hard_limit_laps" => st.fuel_hard_limit_laps = u.map(|x| x as u16),
        "last_lap" => st.last_lap = f,
        "leader_last_lap" => st.leader_last_lap = f,
        "ahead_gap" => st.ahead_gap = f,
        "ahead_gap_trend" => st.ahead_gap_trend = u.map(|x| GapTrend::from_u8(x as u8).unwrap()),
        "behind_gap" => st.behind_gap = f,
        "behind_gap_trend" => st.behind_gap_trend = u.map(|x| GapTrend::from_u8(x as u8).unwrap()),
        "leader_gap" => st.leader_gap = f,
        "pace_band_min" => st.pace_band_min = f,
        "pace_band_max" => st.pace_band_max = f,
        "run_phase" => st.run_phase = u.map(|x| RunPhase::from_u8(x as u8).unwrap()),
        "green_run_laps" => st.green_run_laps = u.map(|x| x as u16),
        "mandatory_repair" => st.mandatory_repair = f,
        "optional_repair" => st.optional_repair = f,
        "on_pit_road" => st.on_pit_road = v.as_bool(),
        "tow_active" => st.tow_active = v.as_bool(),
        "track_temperature" => st.track_temperature = f,
        "air_temperature" => st.air_temperature = f,
        "incidents" => st.incidents = u.map(|x| x as u16),
        "pace_delta_to_leader" => st.pace_delta_to_leader = f,
        other => panic!("channel {other} is not mapped by the test"),
    }
}

fn state_from(decoded: &Value) -> LiveState {
    let header = &decoded["header"];
    let flags = header["flags"].as_u64().unwrap();
    let mut st = blank_state();
    st.connected = flags & 1 != 0;
    st.on_track = flags & 2 != 0;
    st.in_garage = flags & 4 != 0;
    st.first_of_epoch = flags & 16 != 0;
    st.coaching_paused = flags & 32 != 0;
    st.session_epoch = header["session_epoch"].as_u64().unwrap() as u32;
    st.captured_unix_ms = header["captured_unix_ms"].as_u64().unwrap();
    st.session_time = header["session_time"].as_f64();
    st.tick = header["tick"].as_u64().unwrap() as u32;
    st.tick_rate = header["tick_rate"].as_u64().unwrap() as u16;
    for (id, value) in decoded["values"].as_object().unwrap() {
        if !value.is_null() {
            set_channel(&mut st, id, value);
        }
    }
    st.cue = cue_from(&decoded["cue"]);
    st
}

fn expected_value(ty: ChannelType, v: &Value) -> Option<ChannelValue> {
    if v.is_null() {
        return None;
    }
    Some(match ty {
        ChannelType::F32 => ChannelValue::F32(v.as_f64().unwrap() as f32),
        ChannelType::F64 => ChannelValue::F64(v.as_f64().unwrap()),
        ChannelType::U8 => ChannelValue::U8(v.as_u64().unwrap() as u8),
        ChannelType::I8 => ChannelValue::I8(v.as_i64().unwrap() as i8),
        ChannelType::U16 => ChannelValue::U16(v.as_u64().unwrap() as u16),
        ChannelType::U32 => ChannelValue::U32(v.as_u64().unwrap() as u32),
        ChannelType::Bool8 => ChannelValue::Bool(v.as_bool().unwrap()),
        ChannelType::Enum8 => ChannelValue::Enum(v.as_u64().unwrap() as u8),
    })
}

fn assert_decoded(name: &str, frame: &DecodedFrame, expected: &Value) {
    let header = &expected["header"];
    let h = &frame.header;
    assert_eq!(
        u64::from(h.version),
        header["version"].as_u64().unwrap(),
        "{name}: version"
    );
    assert_eq!(
        u64::from(h.flags),
        header["flags"].as_u64().unwrap(),
        "{name}: flags"
    );
    assert_eq!(
        u64::from(h.seq),
        header["seq"].as_u64().unwrap(),
        "{name}: seq"
    );
    assert_eq!(
        u64::from(h.session_epoch),
        header["session_epoch"].as_u64().unwrap(),
        "{name}: epoch"
    );
    assert_eq!(
        h.captured_unix_ms,
        header["captured_unix_ms"].as_u64().unwrap(),
        "{name}: clock"
    );
    match header["session_time"].as_f64() {
        Some(t) => assert_eq!(h.session_time, t, "{name}: session_time"),
        None => assert!(h.session_time.is_nan(), "{name}: session_time is NaN"),
    }
    assert_eq!(
        u64::from(h.tick),
        header["tick"].as_u64().unwrap(),
        "{name}: tick"
    );
    assert_eq!(
        u64::from(h.tick_rate),
        header["tick_rate"].as_u64().unwrap(),
        "{name}: tick_rate"
    );
    assert_eq!(
        u64::from(h.table_version),
        header["table_version"].as_u64().unwrap(),
        "{name}: table_version"
    );
    assert_eq!(
        u64::from(h.channel_count),
        header["channel_count"].as_u64().unwrap(),
        "{name}: channel_count"
    );
    assert_eq!(
        frame.values.len(),
        usize::from(h.channel_count),
        "{name}: values length"
    );
    for (index, spec) in CHANNELS.iter().enumerate().take(frame.values.len()) {
        let want = expected_value(spec.ty, &expected["values"][spec.id]);
        assert_eq!(frame.values[index], want, "{name}: channel {}", spec.id);
    }
    assert_eq!(frame.cue, cue_from(&expected["cue"]), "{name}: cue");
}

#[test]
fn the_channel_table_is_the_one_the_vectors_were_written_for() {
    let doc = frames();
    assert_eq!(
        doc["table_version"].as_u64(),
        Some(u64::from(TABLE_VERSION))
    );
    assert_eq!(doc["channel_count"].as_u64(), Some(CHANNELS.len() as u64));
    assert_eq!(CHANNELS.len(), 45);
}

#[test]
fn encoder_reproduces_the_golden_bytes() {
    let doc = frames();
    for case in doc["encode"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let state = state_from(&case["decoded"]);
        let mut out = vec![0xAA; 7]; // encode_frame clears the buffer first
        state.encode_frame(
            case["decoded"]["header"]["seq"].as_u64().unwrap() as u32,
            &mut out,
        );
        assert_eq!(hex(&out), case["hex"].as_str().unwrap(), "{name}");
    }
}

#[test]
fn decoder_reads_every_golden_frame() {
    let doc = frames();
    for case in doc["encode"]
        .as_array()
        .unwrap()
        .iter()
        .chain(doc["decode_only"].as_array().unwrap())
    {
        let name = case["name"].as_str().unwrap();
        let bytes = unhex(case["hex"].as_str().unwrap());
        let frame = decode_frame(&bytes).unwrap_or_else(|e| panic!("{name}: {e}"));
        assert_decoded(name, &frame, &case["decoded"]);
        // The header check alone agrees with the full decode.
        let parsed = parse_header(&bytes).unwrap();
        assert_eq!(
            parsed.session_time.to_bits(),
            frame.header.session_time.to_bits(),
            "{name}: parse_header time"
        );
        let strip = |h: coach_live::FrameHeader| coach_live::FrameHeader {
            session_time: 0.0,
            ..h
        };
        assert_eq!(strip(parsed), strip(frame.header), "{name}: parse_header");
    }
}

#[test]
fn malformed_frames_are_rejected_with_the_right_error() {
    let doc = frames();
    for case in doc["reject"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let bytes = unhex(case["hex"].as_str().unwrap());
        let error = decode_frame(&bytes).expect_err(name);
        let kind = case["error"].as_str().unwrap();
        let ok = match (&error, kind) {
            (FrameError::TooShort { .. }, "TooShort") => true,
            (FrameError::BadMagic, "BadMagic") => true,
            (FrameError::UnsupportedVersion(v), "UnsupportedVersion") => {
                v.to_string() == case["detail"].as_str().unwrap()
            }
            (FrameError::TableNewer { frame, own }, "TableNewer") => {
                *frame == TABLE_VERSION + 1 && *own == TABLE_VERSION
            }
            (FrameError::TooManyChannels { frame, own }, "TooManyChannels") => {
                usize::from(*frame) == CHANNELS.len() + 1 && usize::from(*own) == CHANNELS.len()
            }
            (FrameError::BadCue(detail), "BadCue") => *detail == case["detail"].as_str().unwrap(),
            _ => false,
        };
        assert!(ok, "{name}: got {error:?}, want {kind}");
        // The server's header check rejects exactly the header-level defects and nothing else.
        let header_level = matches!(
            kind,
            "BadMagic" | "UnsupportedVersion" | "TableNewer" | "TooManyChannels"
        ) || (kind == "TooShort" && bytes.len() < FRAME_FIXED_HEADER_LEN + 6);
        assert_eq!(
            parse_header(&bytes).is_err(),
            header_level,
            "{name}: parse_header"
        );
    }
}

#[test]
fn too_short_reports_what_it_needed() {
    let doc = frames();
    let good = unhex(doc["encode"][0]["hex"].as_str().unwrap());
    assert_eq!(
        decode_frame(&good[..20]),
        Err(FrameError::TooShort {
            needed: FRAME_FIXED_HEADER_LEN,
            len: 20
        })
    );
    assert_eq!(
        parse_header(&good[..40]),
        Err(FrameError::TooShort {
            needed: FRAME_FIXED_HEADER_LEN + 6,
            len: 40
        })
    );
    assert_eq!(
        decode_frame(&[]),
        Err(FrameError::TooShort {
            needed: FRAME_FIXED_HEADER_LEN,
            len: 0
        })
    );
}

#[test]
fn decoder_never_panics_on_any_truncation_or_byte_flip() {
    let doc = frames();
    for case in doc["encode"].as_array().unwrap() {
        let bytes = unhex(case["hex"].as_str().unwrap());
        for cut in 0..bytes.len() {
            let _ = decode_frame(&bytes[..cut]);
            let _ = parse_header(&bytes[..cut]);
        }
        for index in 0..bytes.len() {
            for flip in [0x01u8, 0x80, 0xFF] {
                let mut mutated = bytes.clone();
                mutated[index] ^= flip;
                let _ = decode_frame(&mutated);
            }
        }
    }
}

#[test]
fn backlog_flag_is_set_in_place_and_nothing_else_changes() {
    let doc = frames();
    let original = unhex(doc["encode"][1]["hex"].as_str().unwrap());
    let mut frame = original.clone();
    set_backlog_flag(&mut frame).unwrap();
    assert_eq!(frame[3], original[3] | 0x40);
    assert_eq!(&frame[..3], &original[..3]);
    assert_eq!(&frame[4..], &original[4..]);
    let decoded = decode_frame(&frame).unwrap();
    assert_eq!(decoded.header.flags & 0x40, 0x40);
    assert_eq!(
        set_backlog_flag(&mut [0u8; 10]),
        Err(FrameError::TooShort {
            needed: FRAME_FIXED_HEADER_LEN,
            len: 10
        })
    );
    let mut bad = original.clone();
    bad[0] = 0;
    assert_eq!(set_backlog_flag(&mut bad), Err(FrameError::BadMagic));
}

#[test]
fn channel_order_and_types_follow_the_table() {
    let st = blank_state();
    let values = st.channel_values();
    assert_eq!(values.len(), CHANNELS.len());
    for (index, spec) in CHANNELS.iter().enumerate() {
        assert_eq!(usize::from(spec.index), index);
    }
    // A fully populated state carries a value of the table's type at every index.
    let doc = frames();
    let full = state_from(&doc["encode"][0]["decoded"]);
    for (index, value) in full.channel_values().iter().enumerate() {
        let spec = &CHANNELS[index];
        let matches = matches!(
            (spec.ty, value),
            (ChannelType::F32, Some(ChannelValue::F32(_)))
                | (ChannelType::F64, Some(ChannelValue::F64(_)))
                | (ChannelType::U8, Some(ChannelValue::U8(_)))
                | (ChannelType::I8, Some(ChannelValue::I8(_)))
                | (ChannelType::U16, Some(ChannelValue::U16(_)))
                | (ChannelType::U32, Some(ChannelValue::U32(_)))
                | (ChannelType::Bool8, Some(ChannelValue::Bool(_)))
                | (ChannelType::Enum8, Some(ChannelValue::Enum(_)))
        );
        assert!(
            matches,
            "channel {} ({:?}) holds {:?}",
            spec.id, spec.ty, value
        );
    }
}

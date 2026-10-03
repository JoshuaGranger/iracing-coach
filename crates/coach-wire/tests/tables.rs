use coach_wire::live_channels::*;
use coach_wire::setup_keys;

#[test]
fn channel_table_matches_the_contract() {
    assert_eq!(TABLE_VERSION, 1);
    assert_eq!(CHANNELS.len(), 45);
    for (i, c) in CHANNELS.iter().enumerate() {
        assert_eq!(usize::from(c.index), i, "{}", c.id);
        assert_eq!(
            c.ty == ChannelType::Enum8,
            c.enum_name.is_some(),
            "{}",
            c.id
        );
    }
    assert_eq!(CHANNELS[6].id, "gear");
    assert_eq!(CHANNELS[6].ty, ChannelType::I8);
    assert_eq!(CHANNELS[44].id, "pace_delta_to_leader");
}

#[test]
fn cues_are_numbered_from_one() {
    assert_eq!(CUES.len(), 15);
    for (i, c) in CUES.iter().enumerate() {
        assert_eq!(usize::from(c.id), i + 1, "{}", c.key);
    }
    let braking = &CUES[10];
    assert_eq!(braking.key, "braking_overshoot");
    assert_eq!(
        braking.args,
        [ArgFormat::Int, ArgFormat::Percent0, ArgFormat::Int]
    );
    assert_eq!(braking.priority, Priority::Coaching);
    assert_eq!(CUES[1].basis, Some("PlayerCarTowTime"));
}

#[test]
fn tiles_reference_known_channels() {
    assert_eq!(TILES.len(), 32);
    for t in TILES {
        for ch in t.channels {
            assert!(
                *ch == "cue" || CHANNELS.iter().any(|c| c.id == *ch),
                "{} -> {ch}",
                t.id
            );
        }
    }
    let pace = TILES.iter().find(|t| t.id == "pace-range").unwrap();
    assert_eq!(pace.channels, ["pace_band_min", "pace_band_max"]);
    let fuel = TILES.iter().find(|t| t.id == "fuel").unwrap();
    assert_eq!(fuel.units, ["US gal", "L"]);
}

#[test]
fn enums_carry_wire_bytes() {
    assert_eq!(FlagState::from_u8(8), Some(FlagState::Disqualified));
    assert_eq!(FlagState::from_u8(9), None);
    assert_eq!(GapTrend::Growing as u8, 4);
    assert_eq!(RunPhase::from_u8(3), Some(RunPhase::Late));
    assert_eq!(Evidence::Unavailable as u8, 2);
    assert_eq!(Priority::PitService as u8, 50);
    assert_eq!(Priority::from_u8(100), Some(Priority::Critical));
    assert_eq!(Priority::from_u8(11), None);
}

#[test]
fn conversions() {
    assert_eq!(conversion("m/s", "m/s"), Some(Conversion::Scale(1.0)));
    assert_eq!(conversion("m/s", "km/h"), Some(Conversion::Scale(3.6)));
    assert_eq!(
        conversion("C", "F"),
        Some(Conversion::ScaleOffset {
            scale: 1.8,
            offset: 32.0
        })
    );
    assert_eq!(conversion("L", "mph"), None);
}

#[test]
fn setup_key_patterns_are_present() {
    assert_eq!(setup_keys::VERSION, 1);
    assert_eq!(setup_keys::EXCLUDED_LEAF_PATTERNS.len(), 7);
    assert!(setup_keys::EXCLUDED_LEAF_PATTERNS.contains(&("^CornerWeight$", "derived")));
}

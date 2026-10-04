//! The racing-truth policy (`LiveTruthPolicy.cs`, mirrored by the backend's `live_truth.py`):
//! what each session-flag word means, and which lap distances are real.

use coach_wire::live_channels::FlagState;

/// Caution bits: yellow `0x8`, yellow waving `0x100`, one lap to green `0x200`, caution `0x4000`
/// and caution waving `0x8000`. Equals the backend's `caution_mask` in
/// `test-data/live-truth-conformance-v1.json`; the C# `dotnet_legacy_caution_mask` (`0xC008`)
/// omitted `0x100` and `0x200` and is retired.
pub(crate) const CAUTION_MASK: u32 = 0x0000_C308;
/// One lap to green. iRacing holds it on every sample of an Offline Testing session (the real
/// Test Drive recording `test-data/sdk/g3-garage-pit-2026-10-03.sdkfix` reads `0x10000200` and
/// `0x10040200` throughout) and through a Lone Qualify out-lap, with no yellow or caution bit.
/// It is a caution bit only in a Race (or when the session type is unknown), the rule of the
/// backend's `analysis_engine._caution_flag_series`; a yellow or caution bit counts in every session.
/// The engine reads the session type of each sample; the backend reads the recording's own type
/// and per-sample `SessionNum` only for a recording that is not a Race. They agree on a recording
/// of one session; a recording whose type is Race but which also holds an earlier Practice or
/// Qualify keeps `0x200` as a caution in that part in the backend and not here (docs/08-rewrite/reviews/bugfix-wave-2-review-codex-sol.md, finding 3).
const ONE_LAP_TO_GREEN: u32 = 0x0000_0200;
const REPAIR_MASK: u32 = 0x0010_0000;
const DISQUALIFIED: u32 = 0x0002_0000;
const BLACK: u32 = 0x0001_0000;
const RED: u32 = 0x0000_0010;
const CHECKERED: u32 = 0x0000_0001;
const WHITE: u32 = 0x0000_0002;
const GREEN: u32 = 0x0000_0004 | 0x8000_0000;

/// First match wins in the order disqualified, black, red, caution, checkered, white, green, else
/// racing; no flag word at all is unknown, never racing.
///
/// This is the session-agnostic reading (one lap to green is a caution bit); the live engine uses
/// [`racing_state_in`] with the session's own rule.
#[cfg(test)]
pub(crate) fn racing_state(session_flags: Option<u32>) -> FlagState {
    racing_state_in(session_flags, true)
}

/// [`racing_state`] for a session in which one lap to green is a caution bit only when
/// `one_lap_to_green_is_caution` (a Race, or a session whose type is not known).
pub(crate) fn racing_state_in(
    session_flags: Option<u32>,
    one_lap_to_green_is_caution: bool,
) -> FlagState {
    let Some(flags) = session_flags else {
        return FlagState::Unknown;
    };
    let caution_mask = if one_lap_to_green_is_caution {
        CAUTION_MASK
    } else {
        CAUTION_MASK & !ONE_LAP_TO_GREEN
    };
    if flags & DISQUALIFIED != 0 {
        FlagState::Disqualified
    } else if flags & BLACK != 0 {
        FlagState::Black
    } else if flags & RED != 0 {
        FlagState::Red
    } else if flags & caution_mask != 0 {
        FlagState::Caution
    } else if flags & CHECKERED != 0 {
        FlagState::Checkered
    } else if flags & WHITE != 0 {
        FlagState::White
    } else if flags & GREEN != 0 {
        FlagState::Green
    } else {
        FlagState::Racing
    }
}

/// The repair-required bit; it carries no duration.
pub(crate) fn repair_required(session_flags: Option<u32>) -> bool {
    session_flags.is_some_and(|flags| flags & REPAIR_MASK != 0)
}

/// A lap distance is real only in `[0, 1]` and finite.
pub(crate) fn normalize_lap_distance(value: Option<f64>) -> Option<f64> {
    value.filter(|v| v.is_finite() && (0.0..=1.0).contains(v))
}

/// The display text of a flag state (`LiveTruthPolicy.DisplayFlag`).
pub(crate) fn display_flag(state: FlagState) -> &'static str {
    match state {
        FlagState::Disqualified => "DISQUALIFIED",
        FlagState::Black => "BLACK FLAG",
        FlagState::Red => "RED",
        FlagState::Caution => "CAUTION",
        FlagState::Checkered => "CHECKERED",
        FlagState::White => "WHITE",
        FlagState::Green => "GREEN",
        FlagState::Racing => "RACING",
        FlagState::Unknown => "UNKNOWN",
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    fn conformance() -> serde_json::Value {
        let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../../test-data/live-truth-conformance-v1.json");
        serde_json::from_str(&std::fs::read_to_string(path).expect("conformance file"))
            .expect("conformance json")
    }

    fn state_name(state: FlagState) -> &'static str {
        match state {
            FlagState::Unknown => "unknown",
            FlagState::Racing => "racing",
            FlagState::Green => "green",
            FlagState::White => "white",
            FlagState::Checkered => "checkered",
            FlagState::Caution => "caution",
            FlagState::Red => "red",
            FlagState::Black => "black",
            FlagState::Disqualified => "disqualified",
        }
    }

    #[test]
    fn caution_mask_is_the_backend_value_not_the_legacy_dotnet_one() {
        let doc = conformance();
        assert_eq!(doc["policy_version"], 1);
        assert_eq!(doc["caution_mask"].as_u64(), Some(u64::from(CAUTION_MASK)));
        let legacy = doc["dotnet_legacy_caution_mask"].as_u64().unwrap();
        let missing = doc["caution_bits_missing_from_dotnet"].as_u64().unwrap();
        assert_eq!(legacy, 0xC008);
        assert_eq!(u64::from(CAUTION_MASK), legacy | missing);
        assert_eq!(legacy & missing, 0);
        // The mask is the union of the named caution bits in the file's own flag table.
        let bits = &doc["session_flag_bits"];
        let union: u64 = [
            "yellow",
            "yellow_waving",
            "one_lap_to_green",
            "caution",
            "caution_waving",
        ]
        .iter()
        .map(|name| bits[*name].as_u64().unwrap())
        .fold(0, |a, b| a | b);
        assert_eq!(union, u64::from(CAUTION_MASK));
    }

    #[test]
    fn every_flag_vector_decodes_as_the_backend_does() {
        let doc = conformance();
        let vectors = doc["flag_vectors"].as_array().unwrap();
        assert_eq!(vectors.len(), 53);
        let mut checked = 0;
        for vector in vectors {
            let case = vector["case"].as_str().unwrap();
            // A JSON boolean is not a flag word; the Rust input type cannot express it.
            let flags = match &vector["session_flags"] {
                serde_json::Value::Null => None,
                serde_json::Value::Number(n) => Some(u32::try_from(n.as_u64().unwrap()).unwrap()),
                serde_json::Value::Bool(_) => continue,
                other => panic!("{case}: unexpected {other}"),
            };
            let expected = vector["expected_racing_state"].as_str().unwrap();
            assert_eq!(state_name(racing_state(flags)), expected, "{case}");
            let repair = match vector["expected_repair_state"].as_str().unwrap() {
                "required" => Some(true),
                "not_required" => Some(false),
                _ => None,
            };
            if let Some(required) = repair {
                assert_eq!(repair_required(flags), required, "{case}");
            } else {
                assert_eq!(flags, None, "{case}");
            }
            checked += 1;
        }
        assert_eq!(checked, 52);
    }

    #[test]
    fn one_lap_to_green_is_a_caution_only_where_the_session_says_so() {
        // The recorded Offline Testing words: startHidden | oneLapToGreen, and with servicible.
        for flags in [0x1000_0200_u32, 0x1004_0200] {
            assert_eq!(racing_state_in(Some(flags), false), FlagState::Racing);
            assert_eq!(racing_state_in(Some(flags), true), FlagState::Caution);
            // A real caution bit counts in every session.
            for bit in [0x8_u32, 0x100, 0x4000, 0x8000] {
                assert_eq!(
                    racing_state_in(Some(flags | bit), false),
                    FlagState::Caution,
                    "{flags:#x} | {bit:#x}"
                );
            }
        }
        // Higher states keep their order whatever the session.
        assert_eq!(racing_state_in(Some(0x200 | 0x10), false), FlagState::Red);
        assert_eq!(racing_state_in(Some(0x200 | 0x4), false), FlagState::Green);
        assert_eq!(
            racing_state_in(Some(0x200 | 0x1), false),
            FlagState::Checkered
        );
        assert_eq!(racing_state_in(None, false), FlagState::Unknown);
    }

    #[test]
    fn precedence_order_is_the_files_order() {
        let doc = conformance();
        let order: Vec<&str> = doc["racing_state_precedence"]
            .as_array()
            .unwrap()
            .iter()
            .map(|v| v.as_str().unwrap())
            .collect();
        assert_eq!(
            order,
            [
                "disqualified",
                "black",
                "red",
                "caution",
                "checkered",
                "white",
                "green"
            ]
        );
    }

    #[test]
    fn lap_distance_vectors() {
        let doc = conformance();
        for vector in doc["lap_distance_percent_vectors"].as_array().unwrap() {
            let case = vector["case"].as_str().unwrap();
            let value = match &vector["value"] {
                serde_json::Value::Number(n) => n.as_f64(),
                _ => None, // absent, boolean and string inputs are not observations
            };
            let expected = vector["expected"].as_f64();
            assert_eq!(normalize_lap_distance(value), expected, "{case}");
        }
    }

    #[test]
    fn non_finite_lap_distance_is_refused() {
        assert_eq!(normalize_lap_distance(Some(f64::NAN)), None);
        assert_eq!(normalize_lap_distance(Some(f64::INFINITY)), None);
    }
}

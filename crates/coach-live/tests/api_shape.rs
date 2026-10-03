//! Compile-time check that the public signatures match `contracts/crate-api.md` section 4.
//! Nothing is called: the bodies are stubs until W2 implements them.
#![allow(clippy::type_complexity)]

use coach_live::*;

#[test]
fn signatures_match_the_contract() {
    let _: fn(EngineConfig) -> Engine = Engine::new;
    let _: fn(&mut Engine, &Sample) -> LiveState = Engine::on_tick;
    let _: fn(&LiveState, u32, &mut Vec<u8>) = LiveState::encode_frame;
    let _: fn(&[u8]) -> Result<FrameHeader, FrameError> = parse_header;
    let _: fn(&mut [u8]) -> Result<(), FrameError> = set_backlog_flag;
    let _: fn(&[u8]) -> Result<DecodedFrame, FrameError> = decode_frame;
    let _: fn(&str, &LiveState, Option<&str>, Option<u8>) -> Option<TileText> = format_tile;
    let _: fn(&str, &TileText, &[f64]) -> Option<(f64, f64)> = display_range;
    let _: fn(&CueState) -> String = cue_text;
    let _: fn(&coach_ibt::SessionInfo, i32) -> SessionContext = SessionContext::from_session_info;
    let _: fn(&mut Sample, &coach_ibt::VarTable, &[u8]) = Sample::read_row;
    assert_eq!(FRAME_FIXED_HEADER_LEN, 38);
    assert!(EngineConfig::default().safe_glance);
}

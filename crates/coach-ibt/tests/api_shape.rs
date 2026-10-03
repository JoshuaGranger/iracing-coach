//! Compile-time check that the public signatures match `contracts/crate-api.md` section 3.
//! Nothing is called: the bodies are stubs until W1 implements them.
#![allow(clippy::type_complexity)]

use std::collections::BTreeMap;
use std::path::Path;
use std::time::SystemTime;

use coach_ibt::*;

#[test]
fn signatures_match_the_contract() {
    let _: fn(&[u8]) -> Result<SdkHeader, IbtError> = SdkHeader::parse;
    let _: fn(&SdkHeader, usize) -> Option<VarBuf> = SdkHeader::newest_buffer;
    let _: fn(&[u8], i32, i32) -> Result<VarTable, IbtError> = VarTable::parse_ibt;
    let _: fn(&[u8], &SdkHeader) -> Result<VarTable, IbtError> = VarTable::parse_live;
    let _: fn(&VarTable, &str, &[u8]) -> Option<f64> = VarTable::f64;
    let _: fn(&VarTable, &str, &[u8], &mut Vec<i32>) = VarTable::i32_array;
    let _: fn(&Path) -> Result<IbtFile, IbtError> = IbtFile::open;
    let _: fn(&IbtFile) -> String = IbtFile::session_info_text;
    let _: fn(&IbtFile) -> Result<RecordingSummary, IbtError> = IbtFile::summary;
    let _: fn(&str) -> Result<SessionInfo, IbtError> = SessionInfo::parse;
    let _: fn(&SessionInfo) -> WeekendInfo = SessionInfo::weekend;
    let _: fn(&SessionInfo) -> Vec<SessionEntry> = SessionInfo::sessions;
    let _: fn(&SessionInfo) -> Option<PlayerDriver> = SessionInfo::driver;
    let _: fn(&SessionInfo) -> DriverSetup = SessionInfo::driver_setup;
    let _: fn(&SessionInfo) -> Option<CarSetup> = SessionInfo::car_setup;
    let _: fn(&BTreeMap<String, String>) -> String = setup_values_fingerprint;
    let _: fn(&Path, SystemTime) -> Finalization = finalization_check;
    let _: fn(&Path, &mut dyn FnMut(u64) -> bool) -> Result<HashOutcome, IbtError> = sha256_file;
    assert_eq!(SDK_HEADER_LEN, 112);
    assert_eq!(VAR_HEADER_LEN, 144);
}

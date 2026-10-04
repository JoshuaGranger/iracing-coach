//! Setup identity (`setup-keys.json`) on the real Supra `CarSetup` sections of the G3 rig capture
//! (`test-data/sdk/g3-garage-pit-2026-10-03.sdkfix`: iRacing's keys and publish sequence, numbers
//! scaled per key so equal values stay equal). Every garage load, Apply and exit republishes the
//! anti-roll-bar preload (`Chassis.FrontArb.Preload`) a few tenths apart, as the 2026-10-04 harvest
//! saw; those publishes are one setup.

use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;

use coach_ibt::{
    CarSetup, SessionInfo, decode_session_info, is_identity_excluded,
    setup_values_fingerprint as fingerprint,
};

const FIXTURE: &str = "../../test-data/sdk/g3-garage-pit-2026-10-03.sdkfix";

fn le_u32(bytes: &[u8], at: usize) -> usize {
    u32::from_le_bytes(bytes[at..at + 4].try_into().expect("4 bytes")) as usize
}

/// The `CarSetup` of every session-info version whose values changed, in publish order. Records:
/// `u8 kind, u32 length, payload`; kind 1 is `u32 map_len` and the map's static region, whose header
/// holds the session-info length (offset 16) and offset (offset 20) (`collector/src/fixture.rs`).
fn published_setups() -> Vec<CarSetup> {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join(FIXTURE);
    let bytes = std::fs::read(&path).expect("the G3 fixture");
    assert_eq!(&bytes[..8], b"IRCSDKF1");
    let mut map: Vec<u8> = Vec::new();
    let mut out: Vec<CarSetup> = Vec::new();
    let mut at = 8;
    while at + 5 <= bytes.len() {
        let (kind, len) = (bytes[at], le_u32(&bytes, at + 1));
        let payload = &bytes[at + 5..at + 5 + len];
        at += 5 + len;
        if kind != 1 {
            continue;
        }
        let region = &payload[4..];
        if map.len() < region.len() {
            map.resize(region.len(), 0);
        }
        map[..region.len()].copy_from_slice(region);
        let (text_len, text_at) = (le_u32(&map, 16), le_u32(&map, 20));
        let text = decode_session_info(&map[text_at..text_at + text_len]);
        let info = SessionInfo::parse(&text).expect("session info parses");
        if let Some(setup) = info.car_setup()
            && out.last().is_none_or(|last| last.values != setup.values)
        {
            out.push(setup);
        }
    }
    out
}

fn changed_keys(a: &CarSetup, b: &CarSetup) -> BTreeSet<String> {
    a.values
        .keys()
        .chain(b.values.keys())
        .filter(|k| a.values.get(*k) != b.values.get(*k))
        .cloned()
        .collect()
}

#[test]
fn every_preload_the_supra_publishes_is_a_derived_readout() {
    let setups = published_setups();
    assert!(setups.len() >= 20, "{} CarSetup versions", setups.len());
    let keys: BTreeSet<&str> = setups
        .iter()
        .flat_map(|s| s.values.keys().map(String::as_str))
        .collect();
    let bare: Vec<&str> = keys
        .iter()
        .copied()
        .filter(|k| {
            k.rsplit('.')
                .next()
                .is_some_and(|l| l.eq_ignore_ascii_case("Preload"))
        })
        .collect();
    assert_eq!(
        bare,
        ["Chassis.FrontArb.Preload"],
        "the only leaf named Preload is the front bar's"
    );
    let preloads: Vec<&str> = keys
        .iter()
        .copied()
        .filter(|k| k.to_ascii_lowercase().contains("preload"))
        .collect();
    assert_eq!(
        preloads,
        [
            "Chassis.FrontArb.Preload",
            "Chassis.RightRear.TruckArmPreload"
        ]
    );
    assert!(preloads.iter().all(|k| is_identity_excluded(k)));
    // the bar's own adjustments stay part of the identity
    for key in [
        "Chassis.FrontArb.LinkSlack",
        "Chassis.FrontArb.Diameter",
        "Chassis.FrontArb.ArmAsymmetry",
    ] {
        assert!(keys.contains(key), "{key}");
        assert!(!is_identity_excluded(key), "{key}");
    }
}

#[test]
fn publishes_that_differ_only_in_derived_values_share_one_fingerprint() {
    let setups = published_setups();
    let mut preload_only = 0;
    let mut settings_changed = 0;
    for pair in setups.windows(2) {
        let changed = changed_keys(&pair[0], &pair[1]);
        let derived_only = changed.iter().all(|k| is_identity_excluded(k));
        let (a, b) = (fingerprint(&pair[0].values), fingerprint(&pair[1].values));
        if derived_only {
            assert_eq!(
                a, b,
                "counts {:?} -> {:?}: {changed:?}",
                pair[0].update_count, pair[1].update_count
            );
            if changed.contains("Chassis.FrontArb.Preload") {
                preload_only += 1;
            }
        } else {
            assert_ne!(
                a, b,
                "counts {:?} -> {:?}: {changed:?}",
                pair[0].update_count, pair[1].update_count
            );
            settings_changed += 1;
        }
    }
    assert!(
        preload_only >= 4,
        "{preload_only} publishes moved only the bar preload among the settings"
    );
    assert!(settings_changed >= 3, "{settings_changed} real changes");
}

#[test]
fn the_last_garage_snapshot_and_the_values_at_apply_and_close_are_one_setup() {
    // The first visit: the last snapshot before Apply & Close (count 7) and the publish at the exit
    // (count 8, the values the commit carries) differ in the preload and the corner weights only,
    // the shape of setups 70 and 71 of the harvest.
    let setups = published_setups();
    let at = |count: i64| {
        setups
            .iter()
            .find(|s| s.update_count == Some(count))
            .unwrap_or_else(|| panic!("UpdateCount {count}"))
    };
    let (snapshot, exit) = (at(7), at(8));
    let changed = changed_keys(snapshot, exit);
    assert!(changed.contains("Chassis.FrontArb.Preload"), "{changed:?}");
    assert!(
        changed.iter().all(|k| is_identity_excluded(k)),
        "{changed:?}"
    );
    assert_eq!(fingerprint(&snapshot.values), fingerprint(&exit.values));
}

#[test]
fn a_setup_differing_only_in_the_front_bar_preload_keeps_its_fingerprint() {
    let setups = published_setups();
    let base: BTreeMap<String, String> = setups.last().expect("a setup").values.clone();
    let with = |key: &str, value: Option<&str>| {
        let mut values = base.clone();
        match value {
            Some(v) => values.insert(key.to_owned(), v.to_owned()),
            None => values.remove(key),
        };
        fingerprint(&values)
    };
    let original = fingerprint(&base);
    assert_eq!(with("Chassis.FrontArb.Preload", Some("-6.9 Nm")), original);
    assert_eq!(with("Chassis.FrontArb.Preload", Some("-7.1 Nm")), original);
    assert_eq!(with("Chassis.FrontArb.Preload", None), original);
    assert_eq!(
        with("Chassis.RightRear.TruckArmPreload", Some("0.0 Nm")),
        original
    );
    assert_ne!(with("Chassis.FrontArb.LinkSlack", Some("-9 mm")), original);
    assert_ne!(
        with("Tires.LeftFront.ColdPressure", Some("999 kPa")),
        original
    );
}

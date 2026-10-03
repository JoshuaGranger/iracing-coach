//! `SessionContext::from_session_info` over text parsed by `coach_ibt`: the session-info YAML the
//! C# harness fed its reader (`info_text` in the engine vectors) must give the context the C#
//! regex readers produced, and a real recording's session info must give a sensible one.

mod common;

use std::path::PathBuf;

use coach_ibt::{IbtFile, SessionInfo};
use coach_live::SessionContext;
use common::load;
use serde_json::Value;

fn check(label: &str, session: &Value) {
    let info = SessionInfo::parse(session["info_text"].as_str().unwrap())
        .unwrap_or_else(|e| panic!("{label}: {e}"));
    let ctx = SessionContext::from_session_info(&info, 5);
    assert_eq!(ctx.session_info_update, 5, "{label}");
    assert_eq!(
        ctx.subsession_id,
        session["subsession_id"].as_i64(),
        "{label}: subsession"
    );
    assert_eq!(
        ctx.steering_ratio,
        session["steering_ratio"].as_f64(),
        "{label}: steering ratio"
    );
    let types: Vec<(i64, String)> = session["session_types"]
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
    assert_eq!(ctx.session_types, types, "{label}: session types");
    assert_eq!(ctx.driver_car_idx, Some(0), "{label}");
    assert_eq!(ctx.driver_user_id, Some(321), "{label}");
    assert_eq!(
        ctx.driver_user_name.as_deref(),
        Some("Joshua Granger"),
        "{label}"
    );
    assert_eq!(ctx.car_path.as_deref(), Some("toyotagr86"), "{label}");
    assert_eq!(
        ctx.track.as_deref(),
        Some("Indianapolis Raceway Park"),
        "{label}"
    );
    assert_eq!(ctx.track_config.as_deref(), Some("Road"), "{label}");
}

#[test]
fn the_vector_session_texts_give_the_contexts_the_csharp_readers_produced() {
    let doc = load("engine.json");
    let mut seen = std::collections::BTreeSet::new();
    for scenario in doc["scenarios"].as_array().unwrap() {
        let name = scenario["name"].as_str().unwrap();
        check(name, &scenario["session"]);
        seen.insert(
            scenario["session"]["info_text"]
                .as_str()
                .unwrap()
                .to_string(),
        );
        for (index, step) in scenario["steps"].as_array().unwrap().iter().enumerate() {
            if let Some(session) = step.get("session") {
                check(&format!("{name} step {index}"), session);
                seen.insert(session["info_text"].as_str().unwrap().to_string());
            }
        }
    }
    // The ratio variants (12:1, 10:1, none, unreadable 12:2, 16.5:1) and the session switches.
    assert!(seen.len() >= 6, "{} distinct session texts", seen.len());
}

#[test]
fn a_recording_session_info_gives_a_context() {
    let path =
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../test-data/ibt/synthetic-race.ibt");
    let file = IbtFile::open(&path).expect("synthetic recording opens");
    let ctx =
        SessionContext::from_session_info(&file.session_info().expect("session info parses"), 1);
    assert!(ctx.subsession_id.is_some(), "{ctx:?}");
    assert!(!ctx.session_types.is_empty(), "{ctx:?}");
    assert!(ctx.driver_user_id.is_some(), "{ctx:?}");
    assert!(ctx.car_path.is_some() && ctx.track.is_some(), "{ctx:?}");
    // The type of the first listed session resolves by its number.
    let (number, kind) = ctx.session_types[0].clone();
    assert_eq!(ctx.session_type(number), Some(kind.as_str()));
}

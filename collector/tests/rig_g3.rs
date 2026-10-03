//! Gate G3 on a real recording: `test-data/sdk/g3-garage-pit-2026-10-03.sdkfix` is the sanitized
//! SDK capture of the rig session (solo Test Drive, Toyota Supra at Las Vegas, 2026-10-03):
//! the session-start garage, one long visit with two file loads, applies, an unsaved edit and an
//! Apply and Close, the car in its pit stall on track, then three more visits (leave without
//! Apply, Revert, reload of the same file). `--replay` with `--dry-run` must reproduce what the
//! live dry run saw on the rig, once the defects that run found are fixed.

mod support;

use std::path::PathBuf;

use coach_collector::app;
use coach_collector::cli::RunOptions;
use serde_json::Value;
use support::*;

const FIXTURE: &str = "../test-data/sdk/g3-garage-pit-2026-10-03.sdkfix";
const CAR: &str = "stockcars2 supra2019";
const BASE: &str = "Base LV R.sto";
const EDITED: &str = "Test LV R CAM.sto";

fn fixture_path() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(FIXTURE)
}

#[test]
fn the_sanitized_rig_capture_replays_into_the_garage_events_the_rig_saw() {
    let root = temp_dir("rig-g3");
    let setups = root.join("setups");
    let base_bytes = b"synthetic base setup payload".to_vec();
    let edited_bytes = b"synthetic edited setup payload".to_vec();
    write_file(&setups.join(CAR).join(BASE), &base_bytes);
    write_file(&setups.join(CAR).join(EDITED), &edited_bytes);
    let before = tree_hash(&setups);
    let state_dir = root.join("state");

    let report = app::run(RunOptions {
        replay: Some(fixture_path()),
        speed: 0.0,
        dry_run: true,
        no_ui: true,
        exit_at_end: true,
        iracing_root: Some(root.clone()),
        state_dir: Some(state_dir.clone()),
        ..RunOptions::default()
    })
    .unwrap();
    assert!(
        report.pipeline.ticks > 1_000,
        "{} ticks",
        report.pipeline.ticks
    );

    let log = std::fs::read_to_string(state_dir.join("dry-run.jsonl")).unwrap();
    let lines: Vec<Value> = log
        .lines()
        .map(|l| serde_json::from_str(l).unwrap())
        .collect();
    let of = |k: &str| lines.iter().filter(|l| l["kind"] == k).collect::<Vec<_>>();

    // Every UpdateCount change seen in a garage visit, grouped by visit.
    let mut visits: Vec<(String, Vec<i64>)> = Vec::new();
    for s in of("garage.snapshot") {
        let id = s["snapshot"]["visit_id"].as_str().unwrap().to_string();
        let n = s["snapshot"]["update_count"].as_i64().unwrap();
        match visits.iter_mut().find(|(v, _)| *v == id) {
            Some((_, counts)) => counts.push(n),
            None => visits.push((id, vec![n])),
        }
    }
    let groups: Vec<Vec<i64>> = visits.iter().map(|(_, c)| c.clone()).collect();
    assert_eq!(
        groups,
        [
            vec![1],
            vec![2, 3, 4, 5, 6, 7, 8],
            vec![10, 11, 12],
            vec![13, 14, 15, 16, 17, 18],
            vec![19, 20, 21],
        ],
        "five visits; count 9 arrived at the session screen, outside any visit"
    );

    // One commit per visit, in order: the two that loaded a file are named and hashed, the three
    // that ended on the same name (discard, revert, reload of the file already loaded) are not.
    let commits = of("garage.commit");
    assert_eq!(commits.len(), 5);
    let ids: Vec<&str> = commits
        .iter()
        .map(|c| c["commit"]["visit_id"].as_str().unwrap())
        .collect();
    let expected: Vec<&str> = visits.iter().map(|(v, _)| v.as_str()).collect();
    assert_eq!(ids, expected);
    let names: Vec<Option<&str>> = commits
        .iter()
        .map(|c| c["commit"]["setup_name"].as_str())
        .collect();
    assert_eq!(names, [Some(BASE), Some(EDITED), None, None, None]);
    assert_eq!(commits[0]["commit"]["sto_sha256"], sha256_hex(&base_bytes));
    assert_eq!(
        commits[1]["commit"]["sto_sha256"],
        sha256_hex(&edited_bytes)
    );
    assert_eq!(commits[1]["commit"]["is_modified"], true);
    assert_eq!(commits[1]["commit"]["update_count"], 8);
    assert!(
        commits[2..]
            .iter()
            .all(|c| c["commit"]["sto_sha256"].is_null()
                && c["commit"]["update_count"].is_i64()
                && !c["values"].is_null())
    );
    // The named commit's values are the last snapshot of its visit, and the earlier loads of that
    // visit are snapshots only: nothing in them names a file.
    let last_of_visit = of("garage.snapshot")
        .into_iter()
        .find(|s| s["snapshot"]["update_count"] == 8)
        .unwrap();
    assert_eq!(commits[1]["values"], last_of_visit["snapshot"]["values"]);
    assert!(
        of("garage.snapshot")
            .iter()
            .all(|s| s["snapshot"].get("setup_name").is_none())
    );

    // Garage, pit stall and on track all appear in the frame summaries.
    let frames = of("frames");
    assert!(frames.iter().any(|f| f["in_garage"] == true));
    assert!(frames.iter().any(|f| f["on_track"] == true));
    assert!(frames.iter().any(|f| f["connected"] == false));

    assert_eq!(tree_hash(&setups), before, "setups untouched");
    assert!(!state_dir.join("journal.jsonl").exists());
    let _ = std::fs::remove_dir_all(&root);
}

#[test]
fn the_committed_fixture_carries_no_names_ids_or_purchased_setup_files() {
    let bytes = std::fs::read(fixture_path()).unwrap();
    for word in [
        "Joshua", "Granger", "1507810", "Maconi", "NOAPS", "claude", "joshu",
    ] {
        assert!(
            !bytes.windows(word.len()).any(|w| w == word.as_bytes()),
            "{word} found in the fixture"
        );
    }
    assert!(bytes.len() < 4_000_000, "{} bytes", bytes.len());
}

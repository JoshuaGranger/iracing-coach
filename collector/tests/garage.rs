//! Passive garage capture end to end on a replayed SDK fixture: the P5 sequence (several loads in
//! one visit, the name published only at exit), journaling, the `.sto` hash under the on-track
//! rule, and the `--dry-run` log.

mod support;

use coach_collector::app;
use coach_collector::cli::RunOptions;
use coach_collector::fixture::MapBuilder;
use coach_collector::garage_io::GarageIo;
use coach_collector::gate::Evidence;
use coach_collector::journal::GarageOut;
use serde_json::Value;
use support::*;

#[test]
fn the_p5_sequence_commits_only_the_last_loaded_file_with_its_hash() {
    let b = MapBuilder::new(60).engine_vars();
    let (setups, sto_bytes) = setups_with_sto();
    let before = tree_hash(&setups);
    let mut h = Harness::new(&p5_fixture(&b, STO_NAME, 6_000));
    h.run();
    let actions = h.garage_actions();
    let io = GarageIo::new(h.hub.clone(), setups.clone(), None);
    for a in actions {
        io.journal(a);
    }
    assert_eq!(
        io.resolve_pending(),
        0,
        "off track at the end: the hash resolves"
    );

    let out = h.hub.journal().take_unsent(1);
    let snapshots: Vec<i64> = out
        .iter()
        .filter_map(|m| match m {
            GarageOut::Snapshot(s) => Some(s.update_count),
            _ => None,
        })
        .collect();
    assert_eq!(
        snapshots,
        [3, 4, 5, 6, 7, 8, 9, 10],
        "every UpdateCount change in the visit, the last one arriving after the exit"
    );
    let commits: Vec<_> = out
        .iter()
        .filter_map(|m| match m {
            GarageOut::Commit { entry, values } => Some((entry.clone(), values.clone())),
            _ => None,
        })
        .collect();
    assert_eq!(commits.len(), 1);
    let (commit, values) = &commits[0];
    assert_eq!(commit.setup_name.as_deref(), Some(STO_NAME));
    assert_eq!(
        commit.update_count,
        Some(10),
        "earlier loads stay values-only"
    );
    assert_eq!(
        commit.sto_sha256.as_deref(),
        Some(sha256_hex(&sto_bytes).as_str())
    );
    assert!(!commit.ambiguous && !commit.hash_pending);
    assert_eq!(commit.is_modified, Some(true));
    assert_eq!(commit.load_type.as_deref(), Some("user"));
    assert_eq!(commit.car_path.as_deref(), Some(CAR));
    assert_eq!(commit.driver.as_ref().unwrap()["customer_id"], 123456);
    assert_eq!(
        commit.session.as_ref().unwrap()["track"],
        "Las Vegas Motor Speedway"
    );
    assert_eq!(values["Tires.LeftFront.ColdPressure"], "155 kPa");
    assert_eq!(values["Chassis.Front.SteeringRatio"], "16.5:1");
    assert_eq!(
        values["UpdateCount"], "10",
        "all CarSetup leaves, UpdateCount included"
    );

    // One visit, one id; the snapshots before the last carry no name.
    let ids: std::collections::BTreeSet<&str> = out
        .iter()
        .map(|m| match m {
            GarageOut::Snapshot(s) => s.visit_id.as_str(),
            GarageOut::Commit { entry, .. } => entry.visit_id.as_str(),
        })
        .collect();
    assert_eq!(ids.len(), 1);

    // The named file is queued for upload; nothing under the setups folder changed.
    let pending = h.hub.journal().pending_recordings();
    assert_eq!(pending.len(), 1);
    assert_eq!(pending[0].kind, "sto");
    assert_eq!(
        pending[0].relative_path.as_deref(),
        Some(&format!("{CAR}/{STO_NAME}")[..])
    );
    assert_eq!(
        pending[0].sha256.as_deref(),
        Some(sha256_hex(&sto_bytes).as_str())
    );
    assert_eq!(
        tree_hash(&setups),
        before,
        "the collector never writes the setups folder"
    );
}

#[test]
fn the_sto_is_hashed_only_while_the_car_is_off_track() {
    let b = MapBuilder::new(60).engine_vars();
    let (setups, sto_bytes) = setups_with_sto();
    let mut h = Harness::new(&p5_fixture(&b, STO_NAME, 1_000));
    h.run();
    let io = GarageIo::new(h.hub.clone(), setups, None);
    for a in h.garage_actions() {
        io.journal(a);
    }
    h.hub.gate.observe(Evidence::Live {
        on_track: Some(true),
    });
    assert_eq!(
        io.resolve_pending(),
        1,
        "on track: nothing is searched or hashed"
    );
    assert_eq!(h.hub.journal().commits_needing_hash().len(), 1);
    assert!(
        h.hub
            .journal()
            .take_unsent(1)
            .iter()
            .all(|m| matches!(m, GarageOut::Snapshot(_))),
        "the commit is held back until its hash resolves"
    );
    h.hub.gate.observe(Evidence::Live {
        on_track: Some(false),
    });
    assert_eq!(io.resolve_pending(), 0);
    let out = h.hub.journal().take_unsent(2);
    let sha = out.iter().find_map(|m| match m {
        GarageOut::Commit { entry, .. } => entry.sto_sha256.clone(),
        _ => None,
    });
    assert_eq!(sha, Some(sha256_hex(&sto_bytes)));
}

#[test]
fn leaving_without_a_name_change_commits_values_only() {
    let b = MapBuilder::new(60).engine_vars();
    let (setups, _) = setups_with_sto();
    // The name stays R.sto: no new name arrives at exit.
    let mut h = Harness::new(&p5_fixture(&b, "R.sto", 6_000));
    h.run();
    let io = GarageIo::new(h.hub.clone(), setups, None);
    for a in h.garage_actions() {
        io.journal(a);
    }
    assert_eq!(io.resolve_pending(), 0);
    let out = h.hub.journal().take_unsent(1);
    let commit = out
        .iter()
        .find_map(|m| match m {
            GarageOut::Commit { entry, .. } => Some(entry.clone()),
            _ => None,
        })
        .expect("a commit after the 5 s window");
    assert_eq!(
        commit.setup_name, None,
        "the session-start name is never attached"
    );
    assert_eq!(commit.sto_sha256, None);
    assert_eq!(commit.update_count, Some(10));
    assert!(
        h.hub.journal().pending_recordings().is_empty(),
        "no file to upload"
    );
}

#[test]
fn a_name_shared_by_two_files_is_ambiguous_and_an_unknown_name_has_no_hash() {
    let b = MapBuilder::new(60).engine_vars();
    let (setups, _) = setups_with_sto();
    write_file(
        &setups.join(CAR).join("shop").join(STO_NAME),
        b"a different file, same name",
    );
    write_file(&setups.join(CAR).join("shop").join("nested.sto"), b"x");
    std::fs::remove_file(setups.join(CAR).join(STO_NAME)).unwrap();
    write_file(
        &setups.join(CAR).join("mine").join(STO_NAME),
        b"yet another",
    );
    let mut h = Harness::new(&p5_fixture(&b, STO_NAME, 1_000));
    h.run();
    let io = GarageIo::new(h.hub.clone(), setups.clone(), None);
    for a in h.garage_actions() {
        io.journal(a);
    }
    assert_eq!(io.resolve_pending(), 0);
    let commit = h
        .hub
        .journal()
        .take_unsent(1)
        .into_iter()
        .find_map(|m| match m {
            GarageOut::Commit { entry, .. } => Some(entry),
            _ => None,
        })
        .unwrap();
    assert!(commit.ambiguous);
    assert_eq!(commit.sto_sha256, None);
    assert_eq!(
        commit.setup_name.as_deref(),
        Some(STO_NAME),
        "the name is still recorded"
    );

    // A name with no file at all.
    std::fs::remove_dir_all(setups.join(CAR).join("shop")).unwrap();
    std::fs::remove_dir_all(setups.join(CAR).join("mine")).unwrap();
    let mut h2 = Harness::new(&p5_fixture(&b, STO_NAME, 1_000));
    h2.run();
    let io2 = GarageIo::new(h2.hub.clone(), setups, None);
    for a in h2.garage_actions() {
        io2.journal(a);
    }
    assert_eq!(io2.resolve_pending(), 0);
    let commit = h2
        .hub
        .journal()
        .take_unsent(1)
        .into_iter()
        .find_map(|m| match m {
            GarageOut::Commit { entry, .. } => Some(entry),
            _ => None,
        })
        .unwrap();
    assert!(!commit.ambiguous);
    assert_eq!(commit.sto_sha256, None);
}

#[test]
fn dry_run_writes_the_captures_and_a_frame_summary_without_a_server() {
    let b = MapBuilder::new(60).engine_vars();
    let (setups, sto_bytes) = setups_with_sto();
    let root = setups.parent().unwrap().to_path_buf();
    let fixture_path = root.join("p5.sdkfix");
    write_file(&fixture_path, &p5_fixture(&b, STO_NAME, 3_000));
    let before = tree_hash(&setups);
    let state_dir = root.join("state");

    let report = app::run(RunOptions {
        replay: Some(fixture_path),
        speed: 0.0,
        dry_run: true,
        no_ui: true,
        exit_at_end: true,
        iracing_root: Some(root.clone()),
        state_dir: Some(state_dir.clone()),
        ..RunOptions::default()
    })
    .unwrap();
    assert!(report.pipeline.ticks > 40);
    assert!(
        report.pipeline.frames_encoded > 0,
        "dry run encodes frames without viewers"
    );
    assert!(report.pipeline.garage_actions >= 9);

    let log = std::fs::read_to_string(state_dir.join("dry-run.jsonl")).unwrap();
    let lines: Vec<Value> = log
        .lines()
        .map(|l| serde_json::from_str(l).unwrap())
        .collect();
    let kind = |k: &str| lines.iter().filter(|l| l["kind"] == k).collect::<Vec<_>>();
    assert_eq!(kind("garage.snapshot").len(), 8);
    let commits = kind("garage.commit");
    assert_eq!(commits.len(), 1);
    assert_eq!(commits[0]["commit"]["setup_name"], STO_NAME);
    assert_eq!(commits[0]["commit"]["sto_sha256"], sha256_hex(&sto_bytes));
    assert_eq!(
        commits[0]["values"]["Tires.LeftFront.ColdPressure"],
        "155 kPa"
    );
    let frames = kind("frames");
    assert!(!frames.is_empty());
    assert!(frames[0]["frames"].as_u64().unwrap() > 0 && frames[0]["bytes"].as_u64().unwrap() > 0);
    assert_eq!(tree_hash(&setups), before, "setups untouched");
    assert!(
        !state_dir.join("journal.jsonl").exists(),
        "a dry run keeps no journal"
    );
    let _ = std::fs::remove_dir_all(&root);
}

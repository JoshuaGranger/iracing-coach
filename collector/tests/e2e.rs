//! The whole collector (`app::run`) on a replayed SDK fixture against the in-test server: presence,
//! live frames, garage events, the `.sto` upload and a recording upload, then the journal state a
//! restart would find. This is the dev-PC end to end the rig gates build on.

mod support;

use coach_collector::app;
use coach_collector::cli::RunOptions;
use coach_collector::fixture::{MapBuilder, SessionSpec};
use coach_collector::journal::Journal;
use coach_live::parse_header;
use support::*;

fn finalized_recording(path: &std::path::Path) -> Vec<u8> {
    let b = MapBuilder::new(60).engine_vars();
    let rows: Vec<_> = (0..50)
        .map(|i| {
            let mut r = b.row();
            r.set_f64("Speed", f64::from(i));
            r
        })
        .collect();
    let bytes = b.ibt_bytes(&SessionSpec::default().yaml(), &rows);
    write_file(path, &bytes);
    age_file(path, 60);
    bytes
}

#[test]
fn replay_to_server_streams_frames_captures_the_garage_and_uploads_files() {
    let b = MapBuilder::new(60).engine_vars();
    let (setups, sto_bytes) = setups_with_sto();
    let root = setups.parent().unwrap().to_path_buf();
    let ibt = finalized_recording(
        &root
            .join("telemetry")
            .join("stockcars2 supra2019_vegas oval 2026-10-02 18-18-02.ibt"),
    );
    let fixture_path = root.join("p5.sdkfix");
    write_file(&fixture_path, &p5_fixture(&b, STO_NAME, 3_000));
    let state_dir = root.join("state");

    let mock = Mock::start();
    mock.with(|s| s.welcome_stream_wanted = true);

    // 8x real time: the 6 s timeline takes under a second; the server is connected by then.
    let report = app::run(RunOptions {
        server: Some(mock.url()),
        replay: Some(fixture_path),
        speed: 8.0,
        no_ui: true,
        exit_at_end: true,
        drain_secs: 30,
        iracing_root: Some(root.clone()),
        state_dir: Some(state_dir.clone()),
        ..RunOptions::default()
    })
    .unwrap();
    assert!(report.pipeline.ticks > 50);
    assert!(!report.restart);

    mock.with(|s| {
        assert!(s.schema_errors.is_empty(), "{:?}", s.schema_errors);
        // Presence.
        let hello = s
            .ws_text
            .iter()
            .find(|(_, v)| v["type"] == "hello")
            .unwrap()
            .1
            .clone();
        assert_eq!(hello["protocol"], 1);
        let statuses: Vec<_> = s
            .ws_text
            .iter()
            .filter(|(_, v)| v["type"] == "status")
            .map(|(_, v)| v.clone())
            .collect();
        assert!(
            statuses
                .iter()
                .any(|v| v["sim"] == "in_session" && v["driver"]["customer_id"] == 123456)
        );
        assert_eq!(
            statuses.last().unwrap()["sim"],
            "not_running",
            "the replay ended like a sim that quit"
        );

        // Live frames: the server asked for them (welcome.stream_wanted), valid headers.
        assert!(s.ws_binary.len() > 5, "{} frames", s.ws_binary.len());
        for (_, f) in &s.ws_binary {
            let h = parse_header(f).unwrap();
            assert_eq!(h.version, 1);
            assert_eq!(h.table_version, coach_wire::live_channels::TABLE_VERSION);
        }

        // Garage: eight snapshots and one commit with the hash of the named file.
        let snaps = s
            .ws_text
            .iter()
            .filter(|(_, v)| v["type"] == "garage.snapshot")
            .count();
        assert_eq!(snaps, 8);
        let commit = s
            .ws_text
            .iter()
            .find(|(_, v)| v["type"] == "garage.commit")
            .unwrap()
            .1
            .clone();
        assert_eq!(commit["setup_name"], STO_NAME);
        assert_eq!(commit["sto_sha256"], sha256_hex(&sto_bytes));
        assert_eq!(commit["values"]["Tires.LeftFront.ColdPressure"], "155 kPa");

        // Uploads: the recording and both setup files, byte for byte.
        assert!(
            s.uploads
                .get(&sha256_hex(&ibt))
                .is_some_and(|u| u.verified && u.part == ibt)
        );
        assert!(
            s.uploads
                .get(&sha256_hex(&sto_bytes))
                .is_some_and(|u| u.verified)
        );
        assert!(
            s.uploads
                .values()
                .any(|u| u.file_name == "R.sto" && u.verified)
        );
        let sto = s
            .creates
            .iter()
            .find(|c| c["file_name"] == STO_NAME)
            .unwrap();
        assert_eq!(sto["relative_path"], format!("{CAR}/{STO_NAME}"));
        assert_eq!(
            sto["customer_id"], 123456,
            "attributed to the driver seen in session info"
        );
    });

    // What a restart would find: nothing pending, everything acknowledged.
    let journal = Journal::open(&state_dir.join("journal.jsonl")).unwrap();
    assert!(journal.pending_recordings().is_empty());
    assert_eq!(journal.garage_pending(), 0);
    let config: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(state_dir.join("collector.json")).unwrap())
            .unwrap();
    assert_eq!(config["collector_id"], hello_id(&mock));
    let _ = std::fs::remove_dir_all(&root);
}

fn hello_id(mock: &Mock) -> serde_json::Value {
    mock.with(|s| {
        s.ws_text
            .iter()
            .find(|(_, v)| v["type"] == "hello")
            .unwrap()
            .1["collector_id"]
            .clone()
    })
}

#[test]
fn a_collector_with_no_server_still_captures_and_queues() {
    let b = MapBuilder::new(60).engine_vars();
    let (setups, _) = setups_with_sto();
    let root = setups.parent().unwrap().to_path_buf();
    finalized_recording(&root.join("telemetry").join("offline.ibt"));
    let fixture_path = root.join("p5.sdkfix");
    write_file(&fixture_path, &p5_fixture(&b, STO_NAME, 1_000));
    let state_dir = root.join("state");
    // Nothing listens on this address.
    let dead = {
        let l = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        format!("http://{}", l.local_addr().unwrap())
    };
    let report = app::run(RunOptions {
        server: Some(dead),
        replay: Some(fixture_path),
        speed: 0.0,
        no_ui: true,
        exit_at_end: true,
        drain_secs: 1,
        iracing_root: Some(root.clone()),
        state_dir: Some(state_dir.clone()),
        ..RunOptions::default()
    })
    .unwrap();
    assert!(report.pipeline.frames_encoded == 0, "nobody is watching");
    assert!(
        report.pipeline.garage_actions >= 9,
        "garage capture runs without the server"
    );
    let journal = Journal::open(&state_dir.join("journal.jsonl")).unwrap();
    assert_eq!(
        journal.garage_pending(),
        9,
        "eight snapshots and a commit wait for the server"
    );
    assert!(
        !journal.pending_recordings().is_empty(),
        "uploads wait in the journal"
    );
    let _ = std::fs::remove_dir_all(&root);
}

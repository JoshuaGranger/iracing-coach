//! `coach-collector backfill` (D12 step 3) against the in-test server mock: the recordings below
//! a folder, the telemetry folder and the legacy raw archive go through the normal upload path,
//! once, read-only; what the server holds is skipped; a dry run lists and touches nothing.

mod support;

use std::io;
use std::path::{Path, PathBuf};

use coach_collector::backfill::{Summary, run_with};
use coach_collector::cli::BackfillOptions;
use coach_collector::fixture::{MapBuilder, SessionSpec};
use coach_collector::sdk::{MapSource, SdkMap};
use support::*;

/// iRacing is not running: the mapping does not exist, so file work is allowed at once.
struct NoSim;

impl MapSource for NoSim {
    fn open(&mut self) -> io::Result<Option<Box<dyn SdkMap>>> {
        Ok(None)
    }
}

struct Env {
    root: PathBuf,
    options: BackfillOptions,
}

impl Env {
    fn new() -> Env {
        let root = temp_dir("backfill");
        let documents = root.join("Documents");
        let options = BackfillOptions {
            state_dir: Some(root.join("state")),
            iracing_root: Some(documents.join("iRacing")),
            ..BackfillOptions::default()
        };
        Env { root, options }
    }

    fn telemetry(&self) -> PathBuf {
        self.root.join("Documents/iRacing/telemetry")
    }

    fn archive(&self) -> PathBuf {
        self.root
            .join("Documents/iRacing Coach/data/telemetry-traces/raw")
    }

    fn run(&self, options: &BackfillOptions) -> (Summary, String) {
        let mut out = Vec::new();
        let summary = run_with(options, Box::new(NoSim), &mut out).unwrap();
        (summary, String::from_utf8(out).unwrap())
    }
}

impl Drop for Env {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.root);
    }
}

fn recording(path: &Path, rows: usize, seed: u32) -> Vec<u8> {
    let builder = MapBuilder::new(60).engine_vars();
    let mut data = Vec::new();
    for i in 0..rows {
        let mut row = builder.row();
        row.set_f64("Speed", f64::from(seed) * 1000.0 + i as f64);
        data.push(row);
    }
    let bytes = builder.ibt_bytes(&SessionSpec::default().yaml(), &data);
    write_file(path, &bytes);
    age_file(path, 3_600);
    bytes
}

#[test]
fn backfill_uploads_every_recording_once_read_only_and_a_dry_run_only_lists() {
    let mut env = Env::new();
    let mock = Mock::start();

    // The live telemetry folder, two archived recordings (one folder per hash, a manifest naming
    // the original file), a folder given on the command line with a recording two levels down, a
    // recording the server already holds, a header with no records, and files that are not
    // recordings.
    let live = recording(&env.telemetry().join("live 2026-10-02 10-00-00.ibt"), 12, 1);
    let archived = recording(&env.archive().join("aaaa/archived-name.ibt"), 14, 2);
    write_file(
        &env.archive().join("aaaa/manifest.json"),
        br#"{"original_path": "C:\\Users\\x\\Documents\\iRacing\\telemetry\\stockcars2 supra2019_bristol 2026-09-20 21-08-55.ibt"}"#,
    );
    let held = recording(&env.archive().join("bbbb/held.ibt"), 16, 3);
    let extra = env.root.join("old drive");
    let deep = recording(&extra.join("2025/season 4/deep.ibt"), 18, 4);
    let empty_path = extra.join("headerless.ibt");
    let mut empty = MapBuilder::new(60)
        .engine_vars()
        .ibt_bytes(&SessionSpec::default().yaml(), &[]);
    empty.resize(empty.len() + 50_000, 0);
    write_file(&empty_path, &empty);
    age_file(&empty_path, 3_600);
    write_file(&extra.join("notes.txt"), b"not a recording");
    write_file(&env.archive().join("aaaa/state.json"), b"{}");
    mock.with(|s| {
        s.uploads.insert(
            sha256_hex(&held),
            Upload {
                size: held.len() as u64,
                kind: "ibt".into(),
                file_name: "held.ibt".into(),
                relative_path: None,
                part: held.clone(),
                verified: true,
            },
        );
    });
    let before: Vec<(String, String)> = [env.telemetry(), env.archive(), extra.clone()]
        .iter()
        .flat_map(|d| tree_hash(d))
        .collect();

    env.options.server = Some(mock.url());
    env.options.folders = vec![extra.clone()];

    // A dry run lists every recording with what would happen and writes nothing at all.
    let mut dry = env.options.clone();
    dry.dry_run = true;
    let (summary, text) = env.run(&dry);
    assert_eq!(summary.listed, 5, "{text}");
    for name in [
        "live 2026-10-02 10-00-00.ibt",
        "archived-name.ibt",
        "held.ibt",
        "deep.ibt",
        "headerless.ibt",
    ] {
        assert!(text.contains(name), "{name} missing from\n{text}");
    }
    assert!(
        text.contains("as stockcars2 supra2019_bristol 2026-09-20 21-08-55.ibt"),
        "the archived recording is uploaded under its original name:\n{text}"
    );
    assert!(
        !text.contains("notes.txt") && !text.contains("state.json"),
        "{text}"
    );
    assert!(
        text.trim_end().ends_with("0 already held, 0 refused"),
        "{text}"
    );
    assert!(!env.root.join("state").exists(), "a dry run writes nothing");
    assert_eq!(mock.with(|s| s.creates.len()), 0, "and sends nothing");

    // The real run.
    let (summary, text) = env.run(&env.options);
    assert_eq!(
        (summary.uploaded, summary.held, summary.rejected.len()),
        (3, 1, 1),
        "{text}"
    );
    assert!(summary.rejected[0].0.ends_with("headerless.ibt"));
    assert!(summary.rejected[0].1.contains("0 records"));
    mock.with(|s| {
        for bytes in [&live, &archived, &deep, &held] {
            assert!(s.uploads[&sha256_hex(bytes)].verified);
        }
        assert_eq!(s.creates.len(), 4, "the headerless file is never offered");
        let names: Vec<&str> = s
            .creates
            .iter()
            .map(|c| c["file_name"].as_str().unwrap())
            .collect();
        assert!(
            names.contains(&"stockcars2 supra2019_bristol 2026-09-20 21-08-55.ibt"),
            "{names:?}"
        );
        assert!(names.contains(&"live 2026-10-02 10-00-00.ibt"), "{names:?}");
        assert!(s.creates.iter().all(|c| c["kind"] == "ibt"));
        assert!(s.schema_errors.is_empty(), "{:?}", s.schema_errors);
        // Only the held recording needed no bytes.
        assert!(s.puts.iter().all(|p| p.sha != sha256_hex(&held)));
    });

    // Idempotent: a second run (a restart of an interrupted one looks the same) hashes and posts
    // nothing, and the dry run now reports the state.
    let creates = mock.with(|s| s.creates.len());
    let (summary, _) = env.run(&env.options);
    assert_eq!((summary.uploaded, summary.held), (0, 0));
    assert_eq!(mock.with(|s| s.creates.len()), creates);
    let (_, text) = env.run(&dry);
    assert!(
        text.trim_end()
            .ends_with("0 to queue (0 bytes), 4 already held, 1 refused"),
        "{text}"
    );

    // Read-only: every recording is byte for byte where it was, and nothing was added beside it.
    let after: Vec<(String, String)> = [env.telemetry(), env.archive(), extra.clone()]
        .iter()
        .flat_map(|d| tree_hash(d))
        .collect();
    assert_eq!(after, before);
}

#[cfg(windows)]
#[test]
fn a_second_backfill_is_refused_while_the_first_holds_the_lock() {
    use std::os::windows::fs::OpenOptionsExt;

    let mut env = Env::new();
    let mock = Mock::start();
    env.options.server = Some(mock.url());
    std::fs::create_dir_all(env.root.join("state")).unwrap();
    let _first = std::fs::OpenOptions::new()
        .create(true)
        .truncate(false)
        .write(true)
        .share_mode(0)
        .open(env.root.join("state/backfill.lock"))
        .unwrap();
    let error = run_with(&env.options, Box::new(NoSim), &mut Vec::new()).unwrap_err();
    assert!(error.contains("another backfill is running"), "{error}");
    // A dry run writes nothing, so it needs no lock.
    let dry = BackfillOptions {
        dry_run: true,
        ..env.options.clone()
    };
    run_with(&dry, Box::new(NoSim), &mut Vec::new()).unwrap();
}

//! File-level behaviour on copies of the committed fixtures: truncation, the finalization gate,
//! streaming hashes and the last-record rule of edge reads.

use std::fs::File;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::time::{Duration, SystemTime};

use coach_ibt::*;
use sha2::{Digest, Sha256};

const SYNTHETIC_SIZE: u64 = 7616;
const SYNTHETIC_BUFFER_OFFSET: usize = 1856;
const SYNTHETIC_ROW: usize = 48;
const SYNTHETIC_RECORDS: usize = 120;

fn fixture(name: &str) -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../test-data/ibt")
        .join(name)
}

/// A private copy under the cargo test scratch directory, with `edit` applied to its bytes.
fn copy_of(name: &str, label: &str, edit: impl FnOnce(&mut Vec<u8>)) -> PathBuf {
    let dir = PathBuf::from(env!("CARGO_TARGET_TMPDIR")).join("coach-ibt-files");
    std::fs::create_dir_all(&dir).expect("scratch dir");
    let path = dir.join(format!("{label}.ibt"));
    let mut bytes = std::fs::read(fixture(name)).expect("fixture");
    edit(&mut bytes);
    std::fs::write(&path, bytes).expect("write copy");
    let old = SystemTime::now() - Duration::from_secs(3600);
    File::options()
        .write(true)
        .open(&path)
        .expect("open copy")
        .set_modified(old)
        .expect("set mtime");
    path
}

fn sha256_hex(bytes: &[u8]) -> String {
    Sha256::digest(bytes)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

fn var_offset(path: &Path, name: &str) -> usize {
    IbtFile::open(path)
        .expect("opens")
        .vars
        .get(name)
        .expect("variable")
        .offset as usize
}

fn record_at(record: usize, offset: usize) -> usize {
    SYNTHETIC_BUFFER_OFFSET + record * SYNTHETIC_ROW + offset
}

#[test]
fn truncated_fixture_is_rejected() {
    match IbtFile::open(&fixture("truncated-race.ibt")) {
        Err(IbtError::Truncated {
            region,
            start,
            end,
            available,
        }) => {
            assert_eq!(region, "telemetry sample buffers");
            assert_eq!((start, end, available), (1856, 7616, 7615));
        }
        other => panic!("expected Truncated, got {other:?}"),
    }
    match finalization_check(&fixture("truncated-race.ibt"), SystemTime::now()) {
        Finalization::NotReady(NotReadyReason::Unreadable(message)) => {
            assert!(message.contains("telemetry sample buffers"), "{message}");
        }
        other => panic!("expected Unreadable, got {other:?}"),
    }
}

#[test]
fn a_directory_is_not_a_recording() {
    let dir = PathBuf::from(env!("CARGO_TARGET_TMPDIR"));
    assert!(matches!(IbtFile::open(&dir), Err(IbtError::Format(_))));
    assert!(matches!(
        IbtFile::open(&dir.join("absent.ibt")),
        Err(IbtError::Io(_))
    ));
}

#[test]
fn synthetic_fixture_layout() {
    let file = IbtFile::open(&fixture("synthetic-race.ibt")).expect("opens");
    assert_eq!(file.file_size, SYNTHETIC_SIZE);
    assert_eq!(
        (file.buffer_offset, file.buffer_length, file.record_count),
        (1856, 48, 120)
    );
    assert_eq!(file.vars.len(), 8);
    let summary = file.summary().expect("summary");
    assert_eq!(summary.subsession_id, Some(8001));
    assert_eq!(summary.sim_session_num, Some(1));
    assert!(summary.is_race);
    assert_eq!(summary.time_source, TimeSource::DiskHeader);
    assert_eq!(
        summary.end_time_utc.as_deref(),
        Some("2026-05-28T20:26:41.983333+00:00")
    );
    let info = file.session_info().expect("session info");
    assert_eq!(info.driver().and_then(|d| d.user_id), Some(1));
    assert_eq!(
        info.car_setup()
            .map(|c| c.values.into_iter().collect::<Vec<_>>()),
        Some(vec![(
            "Tires.LeftFront.ColdPressure".to_owned(),
            "25.0 psi".to_owned()
        )])
    );
}

#[test]
fn edge_reads_return_the_last_record_as_stored() {
    let path = copy_of("synthetic-race.ibt", "edges", |_| {});
    let (session_num, speed) = (var_offset(&path, "SessionNum"), var_offset(&path, "Speed"));
    let path = copy_of("synthetic-race.ibt", "edges", |bytes| {
        let last = SYNTHETIC_RECORDS - 1;
        bytes[record_at(0, session_num)..][..4].copy_from_slice(&7i32.to_le_bytes());
        bytes[record_at(last, session_num)..][..4].copy_from_slice(&3i32.to_le_bytes());
        bytes[record_at(last, speed)..][..4].copy_from_slice(&f32::NAN.to_le_bytes());
    });
    let file = IbtFile::open(&path).expect("opens");
    assert_eq!(
        file.edge_scalar("SessionNum").expect("reads"),
        Some(SdkScalar::Int(3))
    );
    // A NaN last record never falls back to the first record.
    match file.edge_scalar("Speed").expect("reads") {
        Some(SdkScalar::Float(value)) => assert!(value.is_nan()),
        other => panic!("expected NaN, got {other:?}"),
    }
    assert_eq!(file.edge_scalar("Missing").expect("reads"), None);
    assert_eq!(file.summary().expect("summary").sim_session_num, Some(3));
}

#[test]
fn finalization_gate_follows_the_python_rule() {
    let path = copy_of("synthetic-race.ibt", "ready", |_| {});
    let now = SystemTime::now();
    match finalization_check(&path, now) {
        Finalization::Ready {
            size, record_count, ..
        } => assert_eq!((size, record_count), (SYNTHETIC_SIZE, 120)),
        other => panic!("expected Ready, got {other:?}"),
    }
    let mtime = std::fs::metadata(&path)
        .and_then(|m| m.modified())
        .expect("mtime");
    assert_eq!(
        finalization_check(&path, mtime + Duration::from_millis(1500)),
        Finalization::NotReady(NotReadyReason::TooRecent {
            age: Duration::from_millis(1500)
        })
    );
    assert_eq!(
        finalization_check(&path, mtime - Duration::from_secs(5)),
        Finalization::NotReady(NotReadyReason::TooRecent {
            age: Duration::ZERO
        })
    );

    let grown = copy_of("synthetic-race.ibt", "grown", |bytes| bytes.push(0));
    assert_eq!(
        finalization_check(&grown, now),
        Finalization::NotReady(NotReadyReason::SizeMismatch {
            expected: SYNTHETIC_SIZE,
            actual: SYNTHETIC_SIZE + 1
        })
    );

    let empty = copy_of("synthetic-race.ibt", "no-records", |bytes| {
        bytes[SDK_HEADER_LEN + 28..SDK_HEADER_LEN + 32].copy_from_slice(&0i32.to_le_bytes());
    });
    assert_eq!(
        finalization_check(&empty, now),
        Finalization::NotReady(NotReadyReason::NoRecords)
    );

    let missing = path.with_file_name("does-not-exist.ibt");
    assert!(matches!(
        finalization_check(&missing, now),
        Finalization::NotReady(NotReadyReason::Unreadable(_))
    ));
}

#[cfg(windows)]
#[test]
fn finalization_gate_waits_for_the_writer() {
    let path = copy_of("synthetic-race.ibt", "in-use", |_| {});
    let writer = std::fs::OpenOptions::new()
        .append(true)
        .open(&path)
        .expect("writer");
    assert_eq!(
        finalization_check(&path, SystemTime::now()),
        Finalization::NotReady(NotReadyReason::InUse)
    );
    drop(writer);
    assert!(matches!(
        finalization_check(&path, SystemTime::now()),
        Finalization::Ready { .. }
    ));
}

#[test]
fn streaming_hash_checks_before_every_block() {
    let dir = PathBuf::from(env!("CARGO_TARGET_TMPDIR")).join("coach-ibt-files");
    std::fs::create_dir_all(&dir).expect("scratch dir");
    let path = dir.join("hash.bin");
    let bytes: Vec<u8> = (0..HASH_BLOCK_BYTES + 10)
        .map(|i| (i % 251) as u8)
        .collect();
    File::create(&path)
        .and_then(|mut f| f.write_all(&bytes))
        .expect("write");

    let mut calls = Vec::new();
    let outcome = sha256_file(&path, &mut |read| {
        calls.push(read);
        true
    })
    .expect("hashes");
    assert_eq!(
        outcome,
        HashOutcome::Complete {
            sha256: sha256_hex(&bytes),
            bytes: bytes.len() as u64
        }
    );
    assert_eq!(calls, [0, HASH_BLOCK_BYTES as u64]);

    let mut seen = 0;
    let outcome = sha256_file(&path, &mut |_| {
        seen += 1;
        seen == 1
    })
    .expect("hashes");
    assert_eq!(
        outcome,
        HashOutcome::Aborted {
            bytes_read: HASH_BLOCK_BYTES as u64
        }
    );
    assert_eq!(
        sha256_file(&path, &mut |_| false).expect("hashes"),
        HashOutcome::Aborted { bytes_read: 0 }
    );

    let empty = dir.join("empty.bin");
    File::create(&empty).expect("create");
    assert_eq!(
        sha256_file(&empty, &mut |_| true).expect("hashes"),
        HashOutcome::Complete {
            sha256: sha256_hex(b""),
            bytes: 0
        }
    );
    assert!(matches!(
        sha256_file(&dir.join("absent.bin"), &mut |_| true),
        Err(IbtError::Io(_))
    ));
}

#[test]
fn session_info_hash_and_text() {
    let file = IbtFile::open(&fixture("synthetic-race.ibt")).expect("opens");
    assert_eq!(
        file.session_info_sha256(),
        sha256_hex(&file.session_info_bytes)
    );
    assert_eq!(
        file.session_info_text(),
        decode_session_info(&file.session_info_bytes)
    );
    assert_eq!(decode_session_info(b"\xef\xbb\xbfA: 1\0B"), "A: 1");
    assert_eq!(
        decode_session_info(b"\xef\xbb\xbfA: \xe9"),
        "\u{ef}\u{bb}\u{bf}A: \u{e9}"
    );
}

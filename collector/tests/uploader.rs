//! The uploader against an in-test HTTP mock of `POST /api/v1/uploads` and
//! `PUT /api/v1/uploads/{sha256}` (`collector-protocol.md` section 4).

mod support;

use std::path::PathBuf;
use std::sync::atomic::Ordering;
use std::sync::{Arc, Mutex};

use coach_collector::config::Paths;
use coach_collector::fixture::{MapBuilder, SessionSpec};
use coach_collector::gate::Evidence;
use coach_collector::hub::Hub;
use coach_collector::journal::Journal;
use coach_collector::uploader::{Step, Uploader, UploaderSettings};
use coach_collector::util::{Clock, ManualClock};
use serde_json::json;
use support::*;

struct Env {
    root: PathBuf,
    paths: Paths,
    clock: Arc<ManualClock>,
    hub: Arc<Hub>,
}

const COLLECTOR_ID: &str = "11111111-2222-4333-8444-555555555555";

impl Env {
    fn new() -> Env {
        let root = temp_dir("uploader");
        let paths = Paths {
            state_dir: root.join("state"),
            iracing_root: root.join("iRacing"),
        };
        std::fs::create_dir_all(paths.telemetry_dir()).unwrap();
        std::fs::create_dir_all(paths.setups_dir()).unwrap();
        let clock = Arc::new(ManualClock::new(1_790_000_000_000));
        let hub = Hub::new(
            clock.clone(),
            Journal::open(&paths.journal_path()).unwrap(),
            true,
        );
        let env = Env {
            root,
            paths,
            clock,
            hub,
        };
        env.set_off_track();
        env
    }

    /// A second process life: same folders and journal, fresh hub and clock.
    fn restarted(&self) -> Env {
        let clock = Arc::new(ManualClock::new(1_790_000_100_000));
        let hub = Hub::new(
            clock.clone(),
            Journal::open(&self.paths.journal_path()).unwrap(),
            true,
        );
        let env = Env {
            root: self.root.clone(),
            paths: self.paths.clone(),
            clock,
            hub,
        };
        env.set_off_track();
        env
    }

    fn set_off_track(&self) {
        self.hub.gate.observe(Evidence::Live {
            on_track: Some(false),
        });
    }

    fn set_on_track(&self) {
        self.hub.gate.observe(Evidence::Live {
            on_track: Some(true),
        });
    }

    /// Moves time forward and keeps the (off-track) evidence fresh.
    fn advance(&self, ms: u64) {
        self.clock.advance(ms);
        self.set_off_track();
    }

    fn uploader(&self, server: &str, chunk: usize) -> Uploader {
        let mut settings = UploaderSettings::new(COLLECTOR_ID);
        settings.chunk_bytes = chunk;
        settings.server_backoff_ms = vec![1_000, 2_000];
        Uploader::new(self.hub.clone(), server, self.paths.clone(), settings)
    }

    /// Steps until the uploader reports idle with nothing pending (or the budget runs out).
    fn drain(&self, up: &mut Uploader, budget: usize) {
        for _ in 0..budget {
            match up.step() {
                Step::Worked => {}
                Step::Idle(_) if up.pending() == 0 => return,
                Step::Idle(d) => self.advance(d.as_millis() as u64 + 1),
            }
        }
    }

    fn pending_journal(&self) -> usize {
        self.hub.journal().pending_recordings().len()
    }
}

impl Drop for Env {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.root);
    }
}

/// A finalized recording of roughly `rows * 1.5 KB`, with distinct content per `seed`.
fn recording(path: &std::path::Path, rows: usize, seed: u32) -> Vec<u8> {
    let builder = MapBuilder::new(60).engine_vars();
    let mut data = Vec::new();
    for i in 0..rows {
        let mut row = builder.row();
        row.set_f64("Speed", f64::from(seed) * 1000.0 + i as f64);
        data.push(row);
    }
    let bytes = builder.ibt_bytes(&SessionSpec::default().yaml(), &data);
    write_file(path, &bytes);
    age_file(path, 30);
    bytes
}

#[test]
fn uploads_a_recording_in_chunks_then_drops_the_journal_entry() {
    let env = Env::new();
    let mock = Mock::start();
    let path = env
        .paths
        .telemetry_dir()
        .join("stockcars2 supra2019_vegas oval 2026-10-02 10-00-00.ibt");
    let bytes = recording(&path, 200, 1);
    let chunk = 64 * 1024;
    let mut up = env.uploader(&mock.url(), chunk);
    env.drain(&mut up, 50);

    mock.with(|s| {
        let sha = sha256_hex(&bytes);
        let upload = &s.uploads[&sha];
        assert!(upload.verified);
        assert_eq!(upload.part, bytes);
        assert_eq!(s.puts.len(), bytes.len().div_ceil(chunk));
        let mut next = 0;
        for put in &s.puts {
            assert_eq!(put.start, next, "contiguous ranges");
            assert_eq!(put.len, put.content_length);
            assert_eq!(put.total, bytes.len() as u64);
            next = put.end + 1;
        }
        assert_eq!(next, bytes.len() as u64);
        let create = &s.creates[0];
        assert_eq!(create["kind"], "ibt");
        assert_eq!(create["size"], bytes.len());
        assert_eq!(create["collector_id"], COLLECTOR_ID);
        assert!(create["relative_path"].is_null());
        assert_eq!(
            create["file_name"],
            "stockcars2 supra2019_vegas oval 2026-10-02 10-00-00.ibt"
        );
        assert!(create["source_mtime"].as_str().unwrap().ends_with('Z'));
        assert!(s.schema_errors.is_empty(), "{:?}", s.schema_errors);
    });
    assert_eq!(env.pending_journal(), 0);
    assert_eq!(env.hub.uploads.uploads_pending.load(Ordering::Relaxed), 0);

    // The same file is remembered: a rescan posts nothing.
    let creates = mock.with(|s| s.creates.len());
    up.request_rescan();
    env.drain(&mut up, 10);
    assert_eq!(mock.with(|s| s.creates.len()), creates);
}

#[test]
fn recordings_go_before_setup_files_and_setups_keep_their_relative_path() {
    let env = Env::new();
    let mock = Mock::start();
    write_file(
        &env.paths
            .setups_dir()
            .join("stockcars2 supra2019/shop/Qualy.sto"),
        b"setup bytes one",
    );
    write_file(
        &env.paths
            .setups_dir()
            .join("stockcars2 supra2019/-Current-"),
        b"not a setup file",
    );
    write_file(
        &env.paths
            .setups_dir()
            .join("stockcars2 supra2019/export.htm"),
        b"<html>garage</html>",
    );
    let ibt = recording(&env.paths.telemetry_dir().join("a.ibt"), 5, 2);
    let mut up = env.uploader(&mock.url(), 1 << 20);
    env.drain(&mut up, 50);
    mock.with(|s| {
        let kinds: Vec<&str> = s
            .creates
            .iter()
            .map(|c| c["kind"].as_str().unwrap())
            .collect();
        assert_eq!(kinds[0], "ibt", "recordings first: {kinds:?}");
        assert_eq!(kinds.len(), 3);
        let sto = s.creates.iter().find(|c| c["kind"] == "sto").unwrap();
        assert_eq!(sto["relative_path"], "stockcars2 supra2019/shop/Qualy.sto");
        assert_eq!(sto["file_name"], "Qualy.sto");
        let htm = s.creates.iter().find(|c| c["kind"] == "htm").unwrap();
        assert_eq!(htm["relative_path"], "stockcars2 supra2019/export.htm");
        assert!(s.uploads.contains_key(&sha256_hex(&ibt)));
        assert!(s.schema_errors.is_empty(), "{:?}", s.schema_errors);
    });
}

#[test]
fn a_renamed_setup_posts_an_occurrence_without_sending_bytes_again() {
    let env = Env::new();
    let mock = Mock::start();
    let a = env.paths.setups_dir().join("car/one.sto");
    write_file(&a, b"identical setup bytes");
    let mut up = env.uploader(&mock.url(), 1 << 20);
    env.drain(&mut up, 20);
    assert_eq!(mock.with(|s| (s.creates.len(), s.puts.len())), (1, 1));
    std::fs::copy(&a, env.paths.setups_dir().join("car/two.sto")).unwrap();
    up.request_rescan();
    env.drain(&mut up, 20);
    mock.with(|s| {
        assert_eq!(s.creates.len(), 2, "every create records an occurrence");
        assert_eq!(s.creates[1]["file_name"], "two.sto");
        assert_eq!(s.puts.len(), 1, "the server already held the bytes");
    });
    assert_eq!(env.pending_journal(), 0);
}

#[test]
fn resumes_from_the_offset_the_server_reports() {
    let env = Env::new();
    let mock = Mock::start();
    let path = env.paths.telemetry_dir().join("r.ibt");
    let bytes = recording(&path, 100, 3);
    let sha = sha256_hex(&bytes);
    mock.with(|s| {
        s.uploads.insert(
            sha.clone(),
            Upload {
                size: bytes.len() as u64,
                kind: "ibt".into(),
                file_name: "r.ibt".into(),
                relative_path: None,
                part: bytes[..100_000].to_vec(),
                verified: false,
            },
        );
    });
    let mut up = env.uploader(&mock.url(), 64 * 1024);
    env.drain(&mut up, 30);
    mock.with(|s| {
        assert_eq!(
            s.puts[0].start, 100_000,
            "continues where the server stopped"
        );
        assert!(s.uploads[&sha].verified);
        assert_eq!(s.uploads[&sha].part, bytes);
    });
}

#[test]
fn a_conflict_answer_moves_to_the_reported_offset() {
    let env = Env::new();
    let mock = Mock::start();
    let bytes = recording(&env.paths.telemetry_dir().join("c.ibt"), 60, 4);
    mock.with(|s| s.put_failures.push_back(support::PutFail::Conflict));
    let mut up = env.uploader(&mock.url(), 32 * 1024);
    env.drain(&mut up, 30);
    mock.with(|s| {
        assert!(s.uploads[&sha256_hex(&bytes)].verified);
        assert_eq!(s.puts[0].start, 0);
        assert_eq!(s.puts[1].start, 0, "the refused range is sent again");
    });
}

#[test]
fn retry_after_on_503_is_honoured() {
    let env = Env::new();
    let mock = Mock::start();
    let bytes = recording(&env.paths.telemetry_dir().join("s.ibt"), 20, 5);
    mock.with(|s| s.create_failures.push_back(503));
    let mut up = env.uploader(&mock.url(), 1 << 20);
    assert_eq!(up.step(), Step::Worked, "scan");
    assert_eq!(up.step(), Step::Worked, "the 503");
    assert_eq!(mock.with(|s| s.creates.len()), 0);
    assert!(matches!(up.step(), Step::Idle(_)), "backing off");
    env.advance(500);
    assert!(matches!(up.step(), Step::Idle(_)), "Retry-After is 1 s");
    env.advance(600);
    env.drain(&mut up, 20);
    assert!(mock.with(|s| s.uploads[&sha256_hex(&bytes)].verified));
}

#[test]
fn a_hash_mismatch_forgets_the_hash_and_starts_over() {
    let env = Env::new();
    let mock = Mock::start();
    let path = env.paths.telemetry_dir().join("m.ibt");
    let bytes = recording(&path, 20, 6);
    mock.with(|s| {
        s.put_failures
            .push_back(support::PutFail::Status(422, None))
    });
    let mut up = env.uploader(&mock.url(), 1 << 20);
    for _ in 0..3 {
        up.step();
    }
    let key = path.to_string_lossy().into_owned();
    let remembered = env.hub.journal().recording(&key).map(|e| e.sha256.clone());
    assert_eq!(remembered, Some(None), "the remembered hash was cleared");
    env.advance(2_500);
    env.drain(&mut up, 30);
    assert!(mock.with(|s| s.uploads[&sha256_hex(&bytes)].verified));
}

#[test]
fn an_unreachable_server_is_retried_with_backoff_then_the_upload_completes() {
    let env = Env::new();
    // Reserve a port, release it, and start the server on it later.
    let addr = {
        let l = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        l.local_addr().unwrap()
    };
    let bytes = recording(&env.paths.telemetry_dir().join("d.ibt"), 20, 7);
    let mut up = env.uploader(&format!("http://{addr}"), 1 << 20);
    up.step(); // scan
    up.step(); // connection refused
    assert_eq!(up.pending(), 1);
    assert_eq!(env.pending_journal(), 1, "stays journaled");
    let mock = Mock::start_on(&addr.to_string());
    assert!(matches!(up.step(), Step::Idle(_)), "backoff");
    env.advance(1_100);
    env.drain(&mut up, 20);
    assert!(mock.with(|s| s.uploads[&sha256_hex(&bytes)].verified));
}

#[test]
fn nothing_is_read_or_sent_while_the_car_is_on_track() {
    let env = Env::new();
    let mock = Mock::start();
    let bytes = recording(&env.paths.telemetry_dir().join("t.ibt"), 20, 8);
    env.set_on_track();
    let mut up = env.uploader(&mock.url(), 1 << 20);
    for _ in 0..5 {
        assert!(matches!(up.step(), Step::Idle(_)));
    }
    assert_eq!(mock.with(|s| s.creates.len()), 0);
    assert!(
        env.hub
            .uploads
            .waiting_for_off_track
            .load(Ordering::Relaxed)
    );
    assert_eq!(
        env.pending_journal(),
        0,
        "not even journaled: no file work at all"
    );
    env.set_off_track();
    env.drain(&mut up, 20);
    assert!(mock.with(|s| s.uploads[&sha256_hex(&bytes)].verified));
    assert!(
        !env.hub
            .uploads
            .waiting_for_off_track
            .load(Ordering::Relaxed)
    );
}

// Windows only: how far a writer runs ahead of a slow reader depends on the kernel's socket
// buffers, which the client bounds with SO_SNDBUF on Windows (see `http::bound_send_buffer`).
#[cfg(windows)]
#[test]
fn an_in_flight_chunk_is_abandoned_within_100_ms_when_the_car_goes_on_track() {
    use std::time::{Duration, Instant};
    let env = Env::new();
    let mock = Mock::start();
    let path = env.paths.telemetry_dir().join("g.ibt");
    let bytes = recording(&path, 12_500, 9); // about 17.6 MB
    assert!(bytes.len() > 16 * 1024 * 1024);
    let flipped: Arc<Mutex<Option<Instant>>> = Arc::new(Mutex::new(None));
    {
        let hub = env.hub.clone();
        let flipped = flipped.clone();
        mock.with(|s| {
            s.body_rate = 4 * 1024 * 1024;
            s.body_hook = Some(Box::new(move |_, read| {
                let mut at = flipped.lock().unwrap();
                if read >= 8 * 1024 && at.is_none() {
                    hub.gate.observe(Evidence::Live {
                        on_track: Some(true),
                    });
                    *at = Some(Instant::now());
                }
            }));
        });
    }
    let mut up = env.uploader(&mock.url(), 8 * 1024 * 1024);
    up.step(); // scan
    let outcome = up.step();
    let returned = Instant::now();
    assert_eq!(outcome, Step::Worked);
    let flipped_at = flipped.lock().unwrap().expect("the chunk started");
    let lag = returned.duration_since(flipped_at);
    assert!(lag < Duration::from_millis(100), "abort took {lag:?}");
    assert!(wait_until(Duration::from_secs(2), || mock
        .with(|s| s.aborted_puts)
        == 1));
    mock.with(|s| {
        assert!(s.puts.is_empty(), "no chunk completed");
        assert_eq!(s.uploads[&sha256_hex(&bytes)].part.len(), 0);
    });
    assert_eq!(env.pending_journal(), 1, "still queued");
    assert!(matches!(up.step(), Step::Idle(_)), "paused while on track");
    assert_eq!(mock.with(|s| s.puts.len()), 0);

    // Off track again: it finishes (hook removed).
    mock.with(|s| {
        s.body_hook = None;
        s.body_rate = 0;
    });
    env.set_off_track();
    env.drain(&mut up, 20);
    assert!(mock.with(|s| s.uploads[&sha256_hex(&bytes)].verified));
}

// Windows only, like the test above: it relies on the bounded socket buffers. The server reads a
// little of the chunk and then stops reading: the client's write blocks on a full socket, and the
// on-track gate must still end the request within 100 ms.
#[cfg(windows)]
#[test]
fn a_chunk_is_abandoned_within_100_ms_even_when_the_server_stops_reading() {
    use std::time::{Duration, Instant};
    let env = Env::new();
    let mock = Mock::start();
    let path = env.paths.telemetry_dir().join("stall.ibt");
    let bytes = recording(&path, 12_500, 14); // about 17.6 MB
    assert!(bytes.len() > 16 * 1024 * 1024);
    mock.with(|s| s.stall_after = Some(64 * 1024));
    let mut up = env.uploader(&mock.url(), 8 * 1024 * 1024);
    up.step(); // scan

    // The car goes on track half a second after the server stalled: the socket is long full.
    let flipped: Arc<Mutex<Option<Instant>>> = Arc::new(Mutex::new(None));
    let watcher = {
        let hub = env.hub.clone();
        let flipped = flipped.clone();
        let stalled = {
            let state = mock.state_handle();
            move || state.lock().unwrap().stalled
        };
        std::thread::spawn(move || {
            assert!(wait_until(Duration::from_secs(10), &stalled));
            std::thread::sleep(Duration::from_millis(500));
            *flipped.lock().unwrap() = Some(Instant::now());
            hub.gate.observe(Evidence::Live {
                on_track: Some(true),
            });
        })
    };
    let outcome = up.step();
    let returned = Instant::now();
    watcher.join().unwrap();
    assert_eq!(outcome, Step::Worked);
    let lag = returned.duration_since(flipped.lock().unwrap().expect("the server stalled"));
    assert!(
        lag < Duration::from_millis(100),
        "abandoning a blocked write took {lag:?}"
    );
    mock.with(|s| {
        assert!(s.puts.is_empty(), "no chunk completed");
        s.stall_after = None; // release the stalled handler
    });
    assert_eq!(env.pending_journal(), 1, "still queued");
    assert!(matches!(up.step(), Step::Idle(_)), "paused while on track");

    // Off track again and the server healthy: the file goes through.
    env.set_off_track();
    env.drain(&mut up, 20);
    assert!(mock.with(|s| s.uploads[&sha256_hex(&bytes)].verified));
}

#[test]
fn a_restart_resumes_from_the_server_offset_without_hashing_again() {
    let env = Env::new();
    let mock = Mock::start();
    let path = env.paths.telemetry_dir().join("resume.ibt");
    let bytes = recording(&path, 200, 10);
    let sha = sha256_hex(&bytes);
    let chunk = 64 * 1024;
    {
        let hub = env.hub.clone();
        let puts_seen = Arc::new(Mutex::new(0usize));
        mock.with(|s| {
            s.body_hook = Some(Box::new(move |_, read| {
                // Close the gate on the third chunk, as the car goes on track.
                let mut n = puts_seen.lock().unwrap();
                if read == 4096 {
                    *n += 1;
                    if *n == 3 {
                        hub.gate.observe(Evidence::Live {
                            on_track: Some(true),
                        });
                    }
                }
            }));
        });
    }
    let mut up = env.uploader(&mock.url(), chunk);
    for _ in 0..8 {
        up.step();
    }
    // A 64 KiB chunk fits the socket buffers, so the chunk that was in flight when the gate
    // closed may still complete; at least two chunks are durable and the file is not.
    let held = mock.with(|s| s.uploads[&sha].part.len());
    assert!(
        held >= 2 * chunk && held < bytes.len(),
        "{held} bytes durable"
    );
    assert_eq!(held % chunk, 0);
    let key = path.to_string_lossy().into_owned();
    assert!(
        env.hub.journal().recording(&key).unwrap().sha256.as_deref() == Some(sha.as_str()),
        "the hash is journaled"
    );
    drop(up);

    // The process restarts: new hub, journal replayed from disk.
    let env2 = env.restarted();
    assert_eq!(env2.pending_journal(), 1);
    mock.with(|s| {
        s.body_hook = None;
        s.puts.clear();
    });
    let mut up2 = env2.uploader(&mock.url(), chunk);
    env2.drain(&mut up2, 40);
    mock.with(|s| {
        assert_eq!(
            s.puts[0].start, held as u64,
            "resumed at the server's offset"
        );
        assert!(s.uploads[&sha].verified);
        assert_eq!(s.uploads[&sha].part, bytes);
    });
    assert_eq!(env2.pending_journal(), 0);
}

#[test]
fn a_recording_still_being_written_waits_for_the_finalization_gate() {
    let env = Env::new();
    let mock = Mock::start();
    let path = env.paths.telemetry_dir().join("live.ibt");
    let bytes = recording(&path, 10, 11);
    // Fresh modification time: iRacing may still be writing.
    std::fs::OpenOptions::new()
        .write(true)
        .open(&path)
        .unwrap()
        .set_modified(std::time::SystemTime::now())
        .unwrap();
    let mut up = env.uploader(&mock.url(), 1 << 20);
    up.step(); // scan
    up.step(); // not final
    assert_eq!(mock.with(|s| s.creates.len()), 0);
    age_file(&path, 30);
    env.advance(5_000);
    env.drain(&mut up, 20);
    assert!(mock.with(|s| s.uploads[&sha256_hex(&bytes)].verified));

    // A file whose size does not match its header is never final.
    let torn = env.paths.telemetry_dir().join("torn.ibt");
    let mut data = recording(&torn, 10, 12);
    data.truncate(data.len() - 7);
    write_file(&torn, &data);
    age_file(&torn, 30);
    let creates = mock.with(|s| s.creates.len());
    up.request_rescan();
    for _ in 0..6 {
        up.step();
        env.advance(10_000);
    }
    assert_eq!(mock.with(|s| s.creates.len()), creates);
}

#[test]
fn the_create_carries_the_driver_the_collector_last_saw() {
    let env = Env::new();
    let mock = Mock::start();
    env.hub.set_status(coach_collector::hub::StatusModel {
        driver: Some(json!({"customer_id": 123456, "name": "Joshua Granger"})),
        ..Default::default()
    });
    write_file(&env.paths.setups_dir().join("car/x.sto"), b"abc");
    let mut up = env.uploader(&mock.url(), 1 << 20);
    env.drain(&mut up, 20);
    assert_eq!(
        mock.with(|s| s.creates[0]["customer_id"].clone()),
        json!(123456)
    );
}

#[test]
fn a_file_deleted_before_upload_is_dropped_from_the_journal() {
    let env = Env::new();
    let mock = Mock::start();
    let path = env.paths.telemetry_dir().join("gone.ibt");
    recording(&path, 10, 13);
    mock.with(|s| s.create_failures.push_back(503));
    let mut up = env.uploader(&mock.url(), 1 << 20);
    up.step();
    up.step();
    assert_eq!(env.pending_journal(), 1);
    std::fs::remove_file(&path).unwrap();
    up.request_rescan();
    env.advance(2_000);
    up.step();
    assert_eq!(up.pending(), 0);
    assert_eq!(env.pending_journal(), 0);
    let _ = env.clock.unix_ms();
}

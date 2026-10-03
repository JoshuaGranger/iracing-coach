//! The reader, engine and frame pipeline over replayed SDK fixtures (`--replay`): decimation to
//! 60 frames/s, the epoch marker, stream control, presence, the on-track evidence, torn reads and
//! the `record-sdk` round trip.

mod support;

use std::io;
use std::sync::Mutex;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::Duration;

use coach_collector::fixture::{MapBuilder, Row, SessionSpec, Step, ty};
use coach_collector::gate::Evidence;
use coach_collector::hub::SimKind;
use coach_collector::sdk::{RowRead, SdkMap, read_header, read_newest_row};
use coach_collector::util::Clock;
use coach_live::{ChannelValue, decode_frame};
use support::*;

fn spec() -> SessionSpec {
    SessionSpec {
        subsession_id: 4242,
        ..SessionSpec::default()
    }
}

/// `count` ticks starting at SDK tick `first`, one every `period_ms`; `edit(i, row)` adjusts a row.
fn ticks(
    b: &MapBuilder,
    first: i32,
    count: usize,
    period_ms: u32,
    mut edit: impl FnMut(usize, &mut Row),
) -> Vec<Step> {
    (0..count)
        .map(|i| {
            let mut row = driving_row(b, i as f64 * 0.01);
            edit(i, &mut row);
            Step::Tick {
                t_ms: i as u32 * period_ms,
                tick: first + i as i32,
                row,
            }
        })
        .collect()
}

fn fixture(b: &MapBuilder, steps: Vec<Step>) -> Vec<u8> {
    let mut all = vec![Step::Static {
        status: 1,
        update: 1,
        session_info: spec().yaml(),
    }];
    all.extend(steps);
    b.write_steps(Vec::new(), &all).unwrap()
}

#[test]
fn a_240_hz_recording_streams_at_60_frames_per_second_with_the_epoch_marker_carried() {
    let b = MapBuilder::new(240).engine_vars();
    // The session changes at tick 1302, which the 1-in-4 decimation skips.
    let steps = ticks(&b, 1000, 480, 4, |i, row| {
        if i >= 302 {
            row.set_i32("SessionUniqueID", 2);
        }
    });
    let mut h = Harness::new(&fixture(&b, steps));
    h.hub.stream().on_start();
    h.run();

    assert_eq!(h.frames.len(), 120, "480 ticks at 240 Hz");
    let decoded: Vec<_> = h.frames.iter().map(|f| decode_frame(f).unwrap()).collect();
    for (i, d) in decoded.iter().enumerate() {
        assert_eq!(d.header.seq, i as u32, "sequence numbers are consecutive");
        assert_eq!(d.header.tick, 1000 + 4 * i as u32, "every 4th tick");
        assert_eq!(d.header.tick_rate, 240);
        assert_eq!(
            d.header.table_version,
            coach_wire::live_channels::TABLE_VERSION
        );
        assert_eq!(d.header.version, 1);
        // Channel 0 is speed (f32, m/s).
        let expected = (4 * i) as f32 * 0.01;
        match d.values[0] {
            Some(ChannelValue::F32(v)) => {
                assert!((v - expected).abs() < 1e-4, "frame {i}: {v} vs {expected}")
            }
            other => panic!("speed missing: {other:?}"),
        }
    }
    // Bit 4 marks the first frame of the stream and the first frame sent after the boundary.
    let marked: Vec<usize> = decoded
        .iter()
        .enumerate()
        .filter(|(_, d)| d.header.flags & 0x10 != 0)
        .map(|(i, _)| i)
        .collect();
    assert_eq!(
        marked,
        [0, 76],
        "tick 1304 is the first sent frame of the new epoch"
    );
    let epochs: Vec<u32> = decoded.iter().map(|d| d.header.session_epoch).collect();
    assert_eq!(epochs[75], epochs[0]);
    assert_eq!(
        epochs[76],
        epochs[0] + 1,
        "the engine bumped the epoch at the session change"
    );
    assert!(
        decoded.iter().all(|d| d.header.flags & 0x01 != 0),
        "sim connected"
    );
    assert!(
        decoded.iter().all(|d| d.header.flags & 0x02 != 0),
        "on track"
    );
}

#[test]
fn a_60_hz_recording_sends_every_tick() {
    let b = MapBuilder::new(60).engine_vars();
    let mut h = Harness::new(&fixture(&b, ticks(&b, 5, 90, 16, |_, _| {})));
    h.hub.stream().on_start();
    h.run();
    assert_eq!(h.frames.len(), 90);
    assert_eq!(h.pipeline.stats().frames_encoded, 90);
    assert!(
        h.frames.iter().all(|f| f.len() > 38 && f.len() < 400),
        "about 200 bytes per frame"
    );
}

#[test]
fn no_frame_is_encoded_without_demand_and_the_latch_holds_until_the_session_ends() {
    let b = MapBuilder::new(60).engine_vars();
    let steps = ticks(&b, 0, 300, 16, |i, row| {
        if i >= 200 {
            row.set_i32("SessionUniqueID", 9);
        }
    });
    let mut h = Harness::new(&fixture(&b, steps));
    // Ticks 0..100: nobody watching.
    for _ in 0..100 {
        h.step();
    }
    assert!(h.frames.is_empty());
    assert_eq!(h.pipeline.stats().frames_encoded, 0);
    // A viewer arrives, then leaves; streaming continues until the stream session ends.
    h.hub.stream().on_start();
    for _ in 0..50 {
        h.step();
    }
    h.hub.stream().on_release();
    let before = h.frames.len();
    assert_eq!(before, 50);
    while h.step() {}
    // Ticks 150..199 still stream (latch), 200.. do not (SessionUniqueID changed, no demand).
    assert_eq!(
        h.frames.len(),
        50 + 50,
        "latch kept it going until tick 199"
    );
    assert!(!h.hub.stream().wants_stream());
    assert!(!h.hub.status().streaming);
}

#[test]
fn presence_follows_the_sim_and_the_gate_follows_is_on_track() {
    let b = MapBuilder::new(60).engine_vars();
    let mut steps = vec![Step::Status { t_ms: 0, status: 0 }];
    steps.push(Step::Status {
        t_ms: 100,
        status: 1,
    });
    // On track for a second, then in the garage (IsOnTrack 0, IsInGarage 1).
    steps.extend(ticks(&b, 10, 60, 16, |_, _| {}));
    steps.extend(ticks(&b, 100, 30, 16, |_, row| {
        row.set_bool("IsOnTrack", false)
            .set_bool("IsInGarage", true);
    }));
    let mut h = Harness::new(&fixture(&b, steps));
    // Status 0: not running, the map exists but status bit 0 is clear.
    h.step();
    assert_eq!(h.hub.status().sim, SimKind::NotRunning);
    assert_eq!(h.reader.evidence(), Evidence::StatusClear);
    assert!(
        !h.hub.gate.allows_file_work(),
        "status clear needs 2 s of evidence"
    );
    for _ in 0..61 {
        h.step();
    }
    let s = h.hub.status();
    assert_eq!(s.sim, SimKind::InSession);
    assert!(s.connected && s.on_track && !s.in_garage);
    assert_eq!(s.driver.as_ref().unwrap()["customer_id"], 123456);
    let session = s.session.as_ref().unwrap();
    assert_eq!(session["track"], "Las Vegas Motor Speedway");
    assert_eq!(session["car_path"], "stockcars2 supra2019");
    assert_eq!(session["subsession_id"], 4242);
    assert_eq!(session["session_type"], "Offline Testing");
    assert!(!h.hub.gate.allows_file_work(), "IsOnTrack is 1");
    for _ in 0..30 {
        h.step();
    }
    let s = h.hub.status();
    assert!(!s.on_track && s.in_garage);
    assert!(h.hub.gate.allows_file_work(), "fresh off-track evidence");
    // The replay ended: with no further ticks the evidence goes stale and file work stops.
    h.clock.advance(1_500);
    h.reader.poll(h.clock.mono_ms(), h.clock.unix_ms());
    h.hub.gate.observe(h.reader.evidence());
    assert!(!h.hub.gate.allows_file_work(), "stale counts as on track");
    assert_eq!(h.reader.evidence(), Evidence::Stale);
}

#[test]
fn a_sim_that_disconnects_resets_the_engine_and_ends_the_stream_session() {
    let b = MapBuilder::new(60).engine_vars();
    let mut steps = ticks(&b, 0, 60, 16, |_, _| {});
    steps.push(Step::Status {
        t_ms: 2_000,
        status: 0,
    });
    let mut h = Harness::new(&fixture(&b, steps));
    h.hub.stream().on_start();
    h.hub.stream().on_release();
    h.run();
    assert_eq!(h.hub.status().sim, SimKind::NotRunning);
    assert!(
        !h.hub.stream().latch(),
        "the sim disconnected: the stream session ended"
    );
    assert!(!h.hub.stream().streaming(false));
    // The last published live state is the disconnected one, with the "waiting" cue.
    let live = h.hub.live().unwrap();
    assert!(!live.connected);
}

/// A map whose header is rewritten under the reader's feet.
struct Tearing {
    data: Mutex<Vec<u8>>,
    reads: AtomicUsize,
    /// After the n-th `read` call (1-based), bump the newest buffer's tick by one.
    tear_after: Mutex<Vec<usize>>,
}

impl SdkMap for Tearing {
    fn len(&self) -> usize {
        self.data.lock().unwrap().len()
    }
    fn read(&self, offset: usize, dst: &mut [u8]) -> bool {
        let mut data = self.data.lock().unwrap();
        let ok = match data.get(offset..offset + dst.len()) {
            Some(src) => {
                dst.copy_from_slice(src);
                true
            }
            None => false,
        };
        let n = self.reads.fetch_add(1, Ordering::SeqCst) + 1;
        if self.tear_after.lock().unwrap().contains(&n) {
            // iRacing reuses the oldest buffer: the highest tick moves on and its row changes.
            let (tick_at, row_at) = (
                48usize,
                i32::from_le_bytes(data[52..56].try_into().unwrap()) as usize,
            );
            let tick = i32::from_le_bytes(data[tick_at..tick_at + 4].try_into().unwrap()) + 1;
            data[tick_at..tick_at + 4].copy_from_slice(&tick.to_le_bytes());
            data[row_at] ^= 0xFF;
        }
        ok
    }
    fn wait(&mut self, _: Duration) {}
}

fn tearing_map() -> Tearing {
    let b = MapBuilder::new(60).var("Speed", ty::FLOAT, 1, "m/s");
    let mut bytes = b.static_region(1, 1, "---\n...\n");
    bytes.resize(b.map_len(), 0);
    // One buffer in use: tick 10 at the first buffer.
    bytes[48..52].copy_from_slice(&10i32.to_le_bytes());
    Tearing {
        data: Mutex::new(bytes),
        reads: AtomicUsize::new(0),
        tear_after: Mutex::new(Vec::new()),
    }
}

#[test]
fn a_torn_read_is_retried_once_then_given_up() {
    // The buffer changes after the row copy (read 1): the retry sees the new tick and succeeds.
    let map = tearing_map();
    let header = read_header(&map).unwrap();
    map.reads.store(0, Ordering::SeqCst);
    *map.tear_after.lock().unwrap() = vec![1];
    let mut row = Vec::new();
    let (outcome, after) = read_newest_row(&map, &header, &mut row);
    assert_eq!(
        outcome,
        RowRead::Row { tick: 11 },
        "the retry read the rewritten buffer"
    );
    assert_eq!(after.var_bufs[0].tick_count, 11);

    // It changes again during the retry: the sample is dropped, not delivered half-written.
    let map = tearing_map();
    let header = read_header(&map).unwrap();
    map.reads.store(0, Ordering::SeqCst);
    *map.tear_after.lock().unwrap() = vec![1, 3];
    let (outcome, _) = read_newest_row(&map, &header, &mut row);
    assert_eq!(outcome, RowRead::Torn);

    // No tear: the first copy stands.
    let map = tearing_map();
    let header = read_header(&map).unwrap();
    let (outcome, _) = read_newest_row(&map, &header, &mut row);
    assert_eq!(outcome, RowRead::Row { tick: 10 });
}

#[test]
fn record_sdk_round_trips_through_a_replay() {
    let b = MapBuilder::new(120).engine_vars();
    let steps = ticks(&b, 500, 200, 8, |i, row| {
        row.set_f64("Throttle", (i % 10) as f64 / 10.0);
    });
    let original = fixture(&b, steps);

    // Play it once to get the reference frames.
    let mut a = Harness::new(&original);
    a.hub.stream().on_start();
    a.run();

    // Record a replay of it with the recorder, then play the recording.
    let clock = std::sync::Arc::new(coach_collector::util::ManualClock::new(0));
    let mut source = coach_collector::fixture::ReplaySource::from_bytes(
        &original,
        coach_collector::fixture::Pacing::Virtual(clock),
    )
    .unwrap();
    let mut map = coach_collector::sdk::MapSource::open(&mut source)
        .unwrap()
        .unwrap();
    let mut recorded = Vec::new();
    let summary = {
        let mut writer = coach_collector::fixture::FixtureWriter::new(&mut recorded).unwrap();
        let s = coach_collector::record::record_from(map.as_mut(), 0.4, &mut writer).unwrap();
        writer.finish().unwrap();
        s
    };
    assert_eq!(summary.ticks, 200, "every tick recorded once");
    assert!(summary.static_regions >= 1);

    let mut c = Harness::new(&recorded);
    c.hub.stream().on_start();
    c.run();
    assert_eq!(c.frames.len(), a.frames.len());
    for (x, y) in a.frames.iter().zip(&c.frames) {
        // Everything but the capture time (the recorder's own timeline) is identical.
        let (dx, dy) = (decode_frame(x).unwrap(), decode_frame(y).unwrap());
        assert_eq!(dx.header.tick, dy.header.tick);
        assert_eq!(dx.values, dy.values);
        assert_eq!(dx.cue, dy.cue);
    }
    let _ = io::sink();
}

/// A map that never connects, and a source that counts how often it is opened.
struct DeadMap(Vec<u8>);

impl SdkMap for DeadMap {
    fn len(&self) -> usize {
        self.0.len()
    }
    fn read(&self, offset: usize, dst: &mut [u8]) -> bool {
        match self.0.get(offset..offset + dst.len()) {
            Some(src) => {
                dst.copy_from_slice(src);
                true
            }
            None => false,
        }
    }
    fn wait(&mut self, _: Duration) {}
}

struct CountingSource {
    opens: std::sync::Arc<AtomicUsize>,
    map_exists: bool,
}

impl coach_collector::sdk::MapSource for CountingSource {
    fn open(&mut self) -> io::Result<Option<Box<dyn SdkMap>>> {
        self.opens.fetch_add(1, Ordering::SeqCst);
        if !self.map_exists {
            return Ok(None);
        }
        let b = MapBuilder::new(60).var("Speed", ty::FLOAT, 1, "m/s");
        let mut bytes = b.static_region(
            0, 1, "---
...
",
        );
        bytes.resize(b.map_len(), 0);
        Ok(Some(Box::new(DeadMap(bytes))))
    }
}

#[test]
fn a_map_that_stays_dead_is_closed_and_opened_again_so_a_new_iracing_run_is_found() {
    let opens = std::sync::Arc::new(AtomicUsize::new(0));
    let mut reader = coach_collector::sdk::SdkReader::new(Box::new(CountingSource {
        opens: opens.clone(),
        map_exists: true,
    }));
    let mut now = 0u64;
    while now <= 10_000 {
        reader.poll(now, 1_790_000_000_000 + now);
        now += 100;
    }
    let n = opens.load(Ordering::SeqCst);
    assert!(
        (2..=4).contains(&n),
        "reopened after 3 s of status clear, not every pass: {n} opens"
    );
    assert_eq!(reader.evidence(), Evidence::StatusClear);

    // No map at all: probed once per second, evidence says iRacing is not running.
    let opens = std::sync::Arc::new(AtomicUsize::new(0));
    let mut reader = coach_collector::sdk::SdkReader::new(Box::new(CountingSource {
        opens: opens.clone(),
        map_exists: false,
    }));
    let mut disconnected_pulses = 0;
    for step in 0..50u64 {
        let poll = reader.poll(step * 100, step * 100);
        if poll == coach_collector::sdk::Poll::Disconnected {
            disconnected_pulses += 1;
        }
    }
    assert_eq!(
        opens.load(Ordering::SeqCst),
        5,
        "one probe per second over 5 s"
    );
    assert_eq!(
        disconnected_pulses, 5,
        "one disconnected sample per second for the engine"
    );
    assert_eq!(reader.evidence(), Evidence::NoMap);
}

/// Writes a synthetic fixture for manual runs: `COACH_WRITE_FIXTURE=<file> COACH_FIXTURE_SECONDS=20
/// cargo test -p coach-collector --test replay_pipeline write_a_synthetic_fixture -- --ignored`.
#[test]
#[ignore = "writes a file when COACH_WRITE_FIXTURE is set"]
fn write_a_synthetic_fixture() {
    let Some(path) = std::env::var_os("COACH_WRITE_FIXTURE") else {
        return;
    };
    let seconds: usize = std::env::var("COACH_FIXTURE_SECONDS")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(20);
    let b = MapBuilder::new(60).engine_vars();
    let steps = ticks(&b, 0, seconds * 60, 1000 / 60, |i, row| {
        let t = i as f64 / 60.0;
        row.set_f64("Speed", 55.0 + (t * 0.7).sin() * 12.0)
            .set_f64("Throttle", 0.5 + (t * 1.3).sin() * 0.5)
            .set_f64("Brake", (0.5 - (t * 1.3).sin()).max(0.0))
            .set_f64("SteeringWheelAngle", (t * 0.9).sin() * 1.2)
            .set_f64("LapDistPct", (t / 24.0).fract())
            .set_f64("RPM", 6500.0 + (t * 2.0).sin() * 800.0)
            .set_i32("Gear", 4)
            .set_bool("IsOnTrack", true);
    });
    std::fs::write(path, fixture(&b, steps)).unwrap();
}

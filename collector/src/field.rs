//! Durable raw opponent SDK evidence. The reader only tries a bounded queue; disk work lives
//! on a separate thread. Closed segments use the normal, off-track-only upload pipeline.
use std::fs::{self, File, OpenOptions};
use std::io::{self, BufRead, BufReader, Read, Write};
use std::path::{Path, PathBuf};
use std::sync::{Arc, mpsc};

use coach_ibt::{VarTable, VarType};
use serde_json::{Value, json};

pub const CHANNELS: &[&str] = &[
    "SessionTime",
    "SessionNum",
    "SessionState",
    "SessionFlags",
    "SessionUniqueID",
    "PlayerCarIdx",
    "CarIdxLapCompleted",
    "CarIdxLap",
    "CarIdxLapDistPct",
    "CarIdxPosition",
    "CarIdxClassPosition",
    "CarIdxOnPitRoad",
    "CarIdxTrackSurface",
    "CarIdxLastLapTime",
    "CarIdxBestLapTime",
];
const MAX_SEGMENT: u64 = 64 * 1024 * 1024;
const MAX_LOCAL: u64 = 2 * 1024 * 1024 * 1024;

pub struct FieldSample {
    pub row: Value,
    pub session_info: Arc<str>,
}

/// Preserve SDK values and car-index array membership, including null nonfinite elements.
pub fn channels(vars: &VarTable, row: &[u8]) -> Value {
    let mut out = serde_json::Map::new();
    for &name in CHANNELS {
        let value = vars.get(name).map_or(Value::Null, |var| {
            if !name.starts_with("CarIdx") {
                return match var.var_type {
                    VarType::Bool => vars.bool(name, row).map_or(Value::Null, |n| json!(n)),
                    VarType::Int | VarType::BitField | VarType::Char => {
                        vars.i64(name, row).map_or(Value::Null, |n| json!(n))
                    }
                    VarType::Float | VarType::Double => {
                        vars.f64(name, row).map_or(Value::Null, |n| json!(n))
                    }
                };
            }
            let Ok(count) = usize::try_from(var.count) else {
                return Value::Null;
            };
            let Ok(start) = usize::try_from(var.offset) else {
                return Value::Null;
            };
            let width = match var.var_type {
                VarType::Bool | VarType::Char => 1,
                VarType::Int | VarType::BitField | VarType::Float => 4,
                VarType::Double => 8,
            };
            if count > 64
                || start
                    .checked_add(count * width)
                    .is_none_or(|end| end > row.len())
            {
                return Value::Null;
            }
            Value::Array(
                (0..count)
                    .map(|i| {
                        let b = &row[start + i * width..start + (i + 1) * width];
                        match var.var_type {
                            VarType::Bool => json!(b[0] != 0),
                            VarType::Char => json!(b[0]),
                            VarType::Int => json!(i32::from_le_bytes(b.try_into().unwrap())),
                            VarType::BitField => json!(u32::from_le_bytes(b.try_into().unwrap())),
                            VarType::Float => json!(f32::from_le_bytes(b.try_into().unwrap())),
                            VarType::Double => json!(f64::from_le_bytes(b.try_into().unwrap())),
                        }
                    })
                    .collect(),
            )
        });
        out.insert(name.into(), value);
    }
    Value::Object(out)
}

pub struct Capture {
    tx: mpsc::SyncSender<Option<FieldSample>>,
    last_ms: Option<u64>,
    dropped: u64,
    hub: Arc<crate::hub::Hub>,
}

impl Capture {
    pub fn observe(&mut self, reader: &crate::sdk::SdkReader, connected: bool) {
        if !connected {
            self.last_ms = None;
            let _ = self.tx.try_send(None);
            return;
        }
        let now = reader.sample().captured_unix_ms;
        if self
            .last_ms
            .is_some_and(|last| now >= last && now - last < 100)
        {
            return;
        }
        self.last_ms = Some(now);
        let Some(mut sample) = reader.field_sample() else {
            self.hub
                .field_capture_gaps
                .fetch_add(1, std::sync::atomic::Ordering::Relaxed);
            return;
        };
        sample.row["dropped_samples"] = json!(self.dropped);
        match self.tx.try_send(Some(sample)) {
            Ok(()) => self.dropped = 0,
            Err(_) => {
                self.dropped += 1;
                self.hub
                    .field_capture_gaps
                    .fetch_add(1, std::sync::atomic::Ordering::Relaxed);
            }
        }
    }
}

pub fn start(
    dir: PathBuf,
    hub: Arc<crate::hub::Hub>,
) -> io::Result<(Capture, std::thread::JoinHandle<()>)> {
    fs::create_dir_all(&dir)?;
    recover(&dir)?;
    let (tx, rx) = mpsc::sync_channel::<Option<FieldSample>>(32);
    let reader_hub = hub.clone();
    let thread = std::thread::Builder::new()
        .name("field-io".into())
        .spawn(move || {
            crate::cpu::register_thread("field-io");
            #[cfg(windows)]
            crate::win::enter_background_mode();
            let mut writer = Writer::new(dir);
            let mut closed = 0;
            let mut retry_at = 0;
            let mut dropped = 0u64;
            for sample in rx {
                let result = match sample {
                    Some(mut sample) => {
                        let now = sample.row["captured_unix_ms"].as_u64().unwrap_or(0);
                        if now < retry_at {
                            dropped += 1;
                            continue;
                        }
                        let queued = sample.row["dropped_samples"].as_u64().unwrap_or(0);
                        if queued > 0 {
                            crate::warn!(
                                "field capture gap: {queued} samples missed by writer queue"
                            );
                        }
                        sample.row["dropped_samples"] = json!(dropped + queued);
                        let result = writer.write(sample);
                        if result.is_err() {
                            retry_at = now.saturating_add(60_000);
                            dropped += 1;
                        } else {
                            dropped = 0;
                        }
                        result
                    }
                    None => writer.close(),
                };
                if let Err(error) = result {
                    hub.field_capture_gaps
                        .fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                    crate::error!("field capture gap: {error}");
                    // Recover this partial on the next startup; never publish a possibly torn segment.
                    writer.file = None;
                }
                if writer.closed != closed {
                    closed = writer.closed;
                    hub.field_generation
                        .fetch_add(1, std::sync::atomic::Ordering::SeqCst);
                    hub.wake_uploader();
                }
            }
            if let Err(error) = writer.close() {
                crate::error!("field capture finalize: {error}");
            }
            if writer.closed != closed {
                hub.field_generation
                    .fetch_add(1, std::sync::atomic::Ordering::SeqCst);
                hub.wake_uploader();
            }
        })?;
    Ok((
        Capture {
            tx,
            last_ms: None,
            dropped: 0,
            hub: reader_hub,
        },
        thread,
    ))
}

struct Writer {
    dir: PathBuf,
    file: Option<(File, PathBuf)>,
    key: Value,
    update: Option<i64>,
    start_ms: u64,
    sync_ms: u64,
    bytes: u64,
    closed: u64,
    last_ms: u64,
}
impl Writer {
    fn new(dir: PathBuf) -> Self {
        Self {
            dir,
            file: None,
            key: Value::Null,
            update: None,
            start_ms: 0,
            sync_ms: 0,
            bytes: 0,
            closed: 0,
            last_ms: 0,
        }
    }
    fn close(&mut self) -> io::Result<()> {
        if let Some((file, path)) = self.file.take() {
            file.sync_all()?;
            drop(file);
            fs::rename(&path, path.with_extension("jsonl"))?;
            self.closed += 1;
        }
        Ok(())
    }
    fn write(&mut self, mut sample: FieldSample) -> io::Result<()> {
        let now = sample.row["captured_unix_ms"].as_u64().unwrap_or(0);
        let key = json!([
            sample.row["driver_id"],
            sample.row["subsession_id"],
            sample.row["channels"]["SessionNum"],
            sample.row["channels"]["SessionUniqueID"]
        ]);
        let update = sample.row["session_info_update"].as_i64();
        if self.file.is_some()
            && (self.key != key
                || now < self.last_ms
                || now.saturating_sub(self.start_ms) >= 60_000)
        {
            self.close()?;
        }
        if self.file.is_none() || self.update != update {
            sample.row["session_info"] = json!(&*sample.session_info);
        }
        let mut bytes = serde_json::to_vec(&sample.row)?;
        bytes.push(b'\n');
        if bytes.len() > 1024 * 1024 {
            return Err(io::Error::other("field sample exceeds 1 MiB"));
        }
        if self.bytes + bytes.len() as u64 > MAX_SEGMENT && self.file.is_some() {
            self.close()?;
            sample.row["session_info"] = json!(&*sample.session_info);
            bytes = serde_json::to_vec(&sample.row)?;
            bytes.push(b'\n');
        }
        if self.file.is_none() {
            let total: u64 = fs::read_dir(&self.dir)?
                .filter_map(Result::ok)
                .filter_map(|e| e.metadata().ok())
                .map(|m| m.len())
                .sum();
            if total >= MAX_LOCAL {
                return Err(io::Error::other(
                    "local field spool full; retained evidence, capture paused",
                ));
            }
            let mut suffix = 0;
            let (file, path) = loop {
                let path = self.dir.join(format!(
                    "{now}-{}-{suffix}.field.partial",
                    std::process::id()
                ));
                match OpenOptions::new().write(true).create_new(true).open(&path) {
                    Ok(file) => break (file, path),
                    Err(e) if e.kind() == io::ErrorKind::AlreadyExists => suffix += 1,
                    Err(e) => return Err(e),
                }
            };
            self.file = Some((file, path));
            self.key = key;
            self.start_ms = now;
            self.sync_ms = now;
            self.bytes = 0;
        }
        let file = &mut self.file.as_mut().unwrap().0;
        file.write_all(&bytes)?;
        self.bytes += bytes.len() as u64;
        // First row and every second are durable even if the process is terminated on track.
        if self.bytes == bytes.len() as u64 || now.saturating_sub(self.sync_ms) >= 1000 {
            file.sync_data()?;
            self.sync_ms = now;
        }
        self.update = update;
        self.last_ms = now;
        Ok(())
    }
}

/// Recover only complete valid lines; a crash can leave a partial final write. Never erase the
/// tail: preserve it in the partial file and copy the valid prefix into a closed segment.
fn recover(dir: &Path) -> io::Result<()> {
    for entry in fs::read_dir(dir)? {
        let path = entry?.path();
        if path.extension().is_none_or(|e| e != "partial") {
            continue;
        }
        let ready = path.with_extension("jsonl");
        if ready.exists() {
            continue;
        }
        let mut reader = BufReader::new(File::open(&path)?);
        let mut lines = Vec::new();
        let mut valid = 0;
        loop {
            lines.clear();
            if reader.read_until(b'\n', &mut lines)? == 0 {
                break;
            }
            if !lines.ends_with(b"\n") || serde_json::from_slice::<Value>(&lines).is_err() {
                break;
            }
            valid += lines.len() as u64;
        }
        if valid == 0 {
            continue;
        }
        let mut source = File::open(&path)?;
        let recovering = path.with_extension("recovery");
        let mut out = OpenOptions::new()
            .write(true)
            .create(true)
            .truncate(true)
            .open(&recovering)?;
        io::copy(
            &mut std::io::Read::by_ref(&mut source).take(valid),
            &mut out,
        )?;
        out.sync_all()?;
        drop(out);
        fs::rename(recovering, ready)?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    fn dir(name: &str) -> PathBuf {
        let path = std::env::temp_dir().join(format!(
            "coach-field-{}-{name}-{}",
            std::process::id(),
            crate::util::unix_ms_now()
        ));
        fs::create_dir_all(&path).unwrap();
        path
    }
    fn sample(ms: u64, update: i64, phase: i32) -> FieldSample {
        FieldSample {
            session_info: Arc::from("WeekendInfo:\n  SubSessionID: 9001\n"),
            row: json!({"schema_version":1,"captured_unix_ms":ms,"tick":ms,
                "session_info_update":update,"driver_id":1,"subsession_id":9001,
                "channels":{"SessionNum":phase,"SessionUniqueID":100,"CarIdxLapCompleted":[1,2]}}),
        }
    }
    #[test]
    fn rotates_with_identity_and_preserves_snapshot_per_segment() {
        let dir = dir("rotation");
        let mut writer = Writer::new(dir.clone());
        writer.write(sample(1000, 1, 0)).unwrap();
        writer.write(sample(1100, 1, 0)).unwrap();
        writer.write(sample(1200, 2, 0)).unwrap();
        writer.write(sample(1300, 2, 1)).unwrap();
        writer.close().unwrap();
        let files = crate::scan::scan_fields(&dir, &|| true).unwrap();
        assert_eq!(files.len(), 2);
        for file in files {
            let rows: Vec<Value> = BufReader::new(File::open(file.path).unwrap())
                .lines()
                .map(|line| serde_json::from_str(&line.unwrap()).unwrap())
                .collect();
            assert!(rows[0]["session_info"].is_string());
            if rows.len() == 3 {
                assert!(rows[1].get("session_info").is_none());
                assert!(rows[2]["session_info"].is_string());
            }
        }
    }
    #[test]
    fn crash_recovery_retains_original_and_never_publishes_torn_tail() {
        let dir = dir("recovery");
        let path = dir.join("crash.field.partial");
        let content = b"{\"schema_version\":1}\n{\"broken\":";
        fs::write(&path, content).unwrap();
        recover(&dir).unwrap();
        recover(&dir).unwrap();
        assert_eq!(fs::read(&path).unwrap(), content);
        assert_eq!(
            fs::read(path.with_extension("jsonl")).unwrap(),
            b"{\"schema_version\":1}\n"
        );
        assert!(crate::scan::scan_fields(&dir, &|| false).is_err());
    }
    #[test]
    fn bounded_queue_backpressure_never_blocks_reader() {
        use crate::fixture::{MapBuilder, Pacing, ReplaySource, SessionSpec};
        use crate::util::{Clock, ManualClock};
        let b = MapBuilder::new(60).engine_vars();
        let ticks: Vec<_> = (0..3)
            .map(|i| {
                let mut row = b.row();
                row.set_i32("SessionNum", 0)
                    .set_f64("SessionTime", i as f64);
                (i as u32 * 100, i + 1, row)
            })
            .collect();
        let bytes = b
            .write_fixture(Vec::new(), &SessionSpec::default().yaml(), &ticks)
            .unwrap();
        let clock = Arc::new(ManualClock::new(1_790_000_000_000));
        let hub = crate::hub::Hub::new(clock.clone(), crate::journal::Journal::in_memory(), true);
        let source = ReplaySource::from_bytes(&bytes, Pacing::Virtual(clock.clone())).unwrap();
        let mut reader = crate::sdk::SdkReader::new(Box::new(source));
        reader.poll(clock.mono_ms(), clock.unix_ms());
        let (tx, rx) = mpsc::sync_channel(1);
        let mut capture = Capture {
            tx,
            last_ms: None,
            dropped: 0,
            hub: hub.clone(),
        };
        for _ in 0..2 {
            reader.wait(std::time::Duration::from_millis(100));
            assert_eq!(
                reader.poll(clock.mono_ms(), clock.unix_ms()),
                crate::sdk::Poll::Tick
            );
            capture.observe(&reader, true);
        }
        assert_eq!(
            hub.field_capture_gaps
                .load(std::sync::atomic::Ordering::Relaxed),
            1
        );
        assert!(rx.recv().unwrap().is_some());
        reader.wait(std::time::Duration::from_millis(100));
        reader.poll(clock.mono_ms(), clock.unix_ms());
        capture.observe(&reader, true);
        assert_eq!(rx.recv().unwrap().unwrap().row["dropped_samples"], 1);
    }
    #[test]
    fn disk_failure_leaves_no_ready_segment() {
        let dir = dir("failure");
        let blocker = dir.join("file");
        fs::write(&blocker, b"blocked").unwrap();
        let mut writer = Writer::new(blocker);
        assert!(writer.write(sample(1000, 1, 0)).is_err());
        assert!(crate::scan::scan_fields(&dir, &|| true).unwrap().is_empty());
    }

    #[test]
    fn full_spool_preserves_unacknowledged_evidence() {
        let dir = dir("full");
        let retained = dir.join("retained.field.jsonl");
        File::create(&retained).unwrap().set_len(MAX_LOCAL).unwrap();
        let mut writer = Writer::new(dir);
        assert!(writer.write(sample(1000, 1, 0)).is_err());
        assert_eq!(fs::metadata(retained).unwrap().len(), MAX_LOCAL);
        assert!(writer.file.is_none());
    }

    /// Explicit fixture refresh: cargo test -p coach-collector --lib emit_sanitized_field_fixture -- --ignored
    #[test]
    #[ignore = "explicitly regenerates the sanitized collector-to-worker corpus"]
    fn emit_sanitized_field_fixture() {
        use crate::fixture::{MapBuilder, Pacing, ReplaySource, Step, ty};
        use crate::util::{Clock, ManualClock};
        let root = Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap();
        let mut yaml = coach_ibt::IbtFile::open(&root.join("test-data/ibt/synthetic-race.ibt"))
            .unwrap()
            .session_info_text();
        yaml = yaml.replace("SessionInfo:", "    - CarIdx: 1\n      UserID: 2\n      UserName: Synthetic Rival\n      CarID: 9002\n      CarClassID: 90\n      CarPath: synthetic test car\n      CarScreenName: NASCAR Test Car\nSessionInfo:");
        let b = MapBuilder::new(60)
            .engine_vars()
            .var("SessionState", ty::INT, 1, "")
            .var("CarIdxLapCompleted", ty::INT, 2, "")
            .var("CarIdxLap", ty::INT, 2, "")
            .var("CarIdxLapDistPct", ty::FLOAT, 2, "%")
            .var("CarIdxPosition", ty::INT, 2, "")
            .var("CarIdxClassPosition", ty::INT, 2, "")
            .var("CarIdxOnPitRoad", ty::BOOL, 2, "")
            .var("CarIdxTrackSurface", ty::INT, 2, "")
            .var("CarIdxLastLapTime", ty::FLOAT, 2, "s")
            .var("CarIdxBestLapTime", ty::FLOAT, 2, "s");
        let mut steps = vec![Step::Static {
            status: 1,
            update: 1,
            session_info: yaml,
        }];
        for seconds in 0..180 {
            let mut row = b.row();
            // Opponent's first two laps are 50s; the third lap includes a recorded 10s pit visit.
            let opponent_time = seconds - (seconds - 110).clamp(0, 10);
            row.set_i32("SessionNum", 1)
                .set_i32("SessionUniqueID", 7001)
                .set_f64("SessionTime", f64::from(seconds))
                .set_bool("IsOnTrack", true)
                .set_i32("SessionState", 4)
                .set_array_i32("CarIdxLapCompleted", 0, seconds / 52)
                .set_array_i32("CarIdxLapCompleted", 1, opponent_time / 50)
                .set_array_i32("CarIdxLap", 1, opponent_time / 50 + 1)
                .set_array_f32("CarIdxLapDistPct", 1, (opponent_time % 50) as f32 / 50.0)
                .set_array_i32("CarIdxPosition", 1, 2)
                .set_array_i32("CarIdxClassPosition", 1, 2)
                .set_array_bool("CarIdxOnPitRoad", 1, (110..120).contains(&seconds))
                .set_array_i32(
                    "CarIdxTrackSurface",
                    1,
                    if (110..120).contains(&seconds) { 1 } else { 3 },
                )
                .set_array_f32(
                    "CarIdxLastLapTime",
                    1,
                    if seconds >= 160 {
                        60.0
                    } else if seconds >= 50 {
                        50.0
                    } else {
                        -1.0
                    },
                )
                .set_array_f32(
                    "CarIdxBestLapTime",
                    1,
                    if seconds >= 50 { 50.0 } else { -1.0 },
                );
            steps.push(Step::Tick {
                t_ms: seconds as u32 * 1000,
                tick: seconds * 60 + 1,
                row,
            });
        }
        let bytes = b.write_steps(Vec::new(), &steps).unwrap();
        let clock = Arc::new(ManualClock::new(1_790_000_000_000));
        let source = ReplaySource::from_bytes(&bytes, Pacing::Virtual(clock.clone())).unwrap();
        let mut reader = crate::sdk::SdkReader::new(Box::new(source));
        let out = root.join("test-data/field");
        fs::create_dir_all(&out).unwrap();
        fs::write(out.join("synthetic-opponents.sdkfix"), bytes).unwrap();
        let mut writer = Writer::new(dir("fixture"));
        reader.poll(clock.mono_ms(), clock.unix_ms());
        loop {
            reader.wait(std::time::Duration::from_millis(100));
            if reader.poll(clock.mono_ms(), clock.unix_ms()) == crate::sdk::Poll::Tick {
                writer.write(reader.field_sample().unwrap()).unwrap();
            }
            if reader.at_end() {
                break;
            }
        }
        writer.close().unwrap();
        let mut files = crate::scan::scan_fields(&writer.dir, &|| true).unwrap();
        files.sort_by(|a, b| a.path.cmp(&b.path));
        for (index, file) in files.iter().enumerate() {
            fs::copy(
                &file.path,
                out.join(format!("synthetic-opponents-{index:03}.field.jsonl")),
            )
            .unwrap();
        }
    }
}

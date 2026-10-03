//! SDK byte fixtures: a recorded (or synthesized) sequence of shared-memory states that the
//! collector replays through the same reader code as the real map (`--replay <fixture>`), plus a
//! builder for synthetic maps used by tests and by `record-sdk`'s consumers.
//!
//! File format (`.sdkfix`, little-endian): the 8-byte magic `IRCSDKF1`, then records of
//! `u8 kind, u32 payload_len, payload`:
//!
//! | kind | payload |
//! |---|---|
//! | 1 static | `u32 map_len`, then the map bytes `[0, static_end)`: header, variable headers, session info |
//! | 2 tick | `u32 t_ms, i32 status, i32 tick_rate, i32 session_info_update, i32 tick`, then one sample row |
//! | 3 status | `u32 t_ms, i32 status` (header status changed, no new row) |
//!
//! A static record is written first and again whenever the variable table or session info changes.
//! On replay a tick record patches the header (status, tick rate, session-info counter), writes the
//! row into buffer `tick % num_buf` and points that buffer's header entry at it, as iRacing does.

use std::collections::BTreeMap;
use std::io::{self, Read, Write};
use std::path::Path;
use std::sync::Arc;
use std::thread;
use std::time::{Duration, Instant};

use crate::sdk::{MapSource, SdkMap};
use crate::util::ManualClock;

pub const MAGIC: &[u8; 8] = b"IRCSDKF1";
const KIND_STATIC: u8 = 1;
const KIND_TICK: u8 = 2;
const KIND_STATUS: u8 = 3;

const HEADER_LEN: usize = 112;
const VAR_HEADER_LEN: usize = 144;

// ---- writer ----

pub struct FixtureWriter<W: Write> {
    out: W,
}

impl<W: Write> FixtureWriter<W> {
    pub fn new(mut out: W) -> io::Result<Self> {
        out.write_all(MAGIC)?;
        Ok(FixtureWriter { out })
    }

    fn record(&mut self, kind: u8, payload: &[u8]) -> io::Result<()> {
        self.out.write_all(&[kind])?;
        self.out.write_all(&(payload.len() as u32).to_le_bytes())?;
        self.out.write_all(payload)
    }

    pub fn write_static(&mut self, map_len: u32, bytes: &[u8]) -> io::Result<()> {
        let mut payload = Vec::with_capacity(4 + bytes.len());
        payload.extend_from_slice(&map_len.to_le_bytes());
        payload.extend_from_slice(bytes);
        self.record(KIND_STATIC, &payload)
    }

    pub fn write_tick(
        &mut self,
        t_ms: u32,
        status: i32,
        tick_rate: i32,
        session_info_update: i32,
        tick: i32,
        row: &[u8],
    ) -> io::Result<()> {
        let mut payload = Vec::with_capacity(20 + row.len());
        payload.extend_from_slice(&t_ms.to_le_bytes());
        for v in [status, tick_rate, session_info_update, tick] {
            payload.extend_from_slice(&v.to_le_bytes());
        }
        payload.extend_from_slice(row);
        self.record(KIND_TICK, &payload)
    }

    pub fn write_status(&mut self, t_ms: u32, status: i32) -> io::Result<()> {
        let mut payload = Vec::with_capacity(8);
        payload.extend_from_slice(&t_ms.to_le_bytes());
        payload.extend_from_slice(&status.to_le_bytes());
        self.record(KIND_STATUS, &payload)
    }

    pub fn finish(mut self) -> io::Result<W> {
        self.out.flush()?;
        Ok(self.out)
    }
}

// ---- replay ----

#[derive(Debug, Clone)]
enum Record {
    Static {
        map_len: usize,
        bytes: Vec<u8>,
    },
    Tick {
        t_ms: u32,
        status: i32,
        tick_rate: i32,
        update: i32,
        tick: i32,
        row: Vec<u8>,
    },
    Status {
        t_ms: u32,
        status: i32,
    },
}

fn parse_records(data: &[u8]) -> io::Result<Vec<Record>> {
    let bad = |m: &str| io::Error::new(io::ErrorKind::InvalidData, m.to_string());
    if data.len() < MAGIC.len() || &data[..MAGIC.len()] != MAGIC {
        return Err(bad("not an SDK fixture (bad magic)"));
    }
    let mut pos = MAGIC.len();
    let mut out = Vec::new();
    while pos < data.len() {
        if pos + 5 > data.len() {
            return Err(bad("truncated record header"));
        }
        let kind = data[pos];
        let len = u32::from_le_bytes(data[pos + 1..pos + 5].try_into().unwrap()) as usize;
        pos += 5;
        if pos + len > data.len() {
            return Err(bad("truncated record payload"));
        }
        let p = &data[pos..pos + len];
        pos += len;
        let i32_at = |o: usize| i32::from_le_bytes(p[o..o + 4].try_into().unwrap());
        let u32_at = |o: usize| u32::from_le_bytes(p[o..o + 4].try_into().unwrap());
        out.push(match kind {
            KIND_STATIC if len >= 4 => Record::Static {
                map_len: u32_at(0) as usize,
                bytes: p[4..].to_vec(),
            },
            KIND_TICK if len >= 20 => Record::Tick {
                t_ms: u32_at(0),
                status: i32_at(4),
                tick_rate: i32_at(8),
                update: i32_at(12),
                tick: i32_at(16),
                row: p[20..].to_vec(),
            },
            KIND_STATUS if len >= 8 => Record::Status {
                t_ms: u32_at(0),
                status: i32_at(4),
            },
            _ => return Err(bad("unknown or short record")),
        });
    }
    if !matches!(out.first(), Some(Record::Static { .. })) {
        return Err(bad("fixture must start with a static record"));
    }
    Ok(out)
}

/// How a replay is paced.
#[derive(Clone)]
pub enum Pacing {
    /// Advance a manual clock to each record's time and never sleep (tests, `--speed 0`).
    Virtual(Arc<ManualClock>),
    /// Sleep until each record's time divided by `speed`.
    Realtime { speed: f64 },
}

pub struct ReplayMap {
    map: Vec<u8>,
    records: Vec<Record>,
    next: usize,
    pacing: Pacing,
    started: Option<Instant>,
    base_t_ms: u32,
}

impl ReplayMap {
    fn new(records: Vec<Record>, pacing: Pacing) -> ReplayMap {
        let mut replay = ReplayMap {
            map: Vec::new(),
            records,
            next: 0,
            pacing,
            started: None,
            base_t_ms: 0,
        };
        // The first record is a static region: apply it so the map exists before any tick.
        replay.apply_next();
        replay
    }

    fn apply_next(&mut self) -> Option<u32> {
        let record = self.records.get(self.next)?.clone();
        let first = self.next == 0;
        self.next += 1;
        match record {
            Record::Static { map_len, bytes } => {
                if self.map.len() != map_len {
                    self.map.resize(map_len, 0);
                }
                let n = bytes.len().min(self.map.len());
                self.map[..n].copy_from_slice(&bytes[..n]);
                if first {
                    // Until the first tick record the replayed sim is "not connected", so the
                    // reader sees no phantom sample in buffers that have no row yet.
                    self.put_i32(4, 0);
                }
                None
            }
            Record::Status { t_ms, status } => {
                self.put_i32(4, status);
                Some(t_ms)
            }
            Record::Tick {
                t_ms,
                status,
                tick_rate,
                update,
                tick,
                row,
            } => {
                self.put_i32(4, status);
                self.put_i32(8, tick_rate);
                self.put_i32(12, update);
                let num_buf = self.get_i32(32).clamp(1, 4) as usize;
                let slot = (tick as u32 as usize) % num_buf;
                let entry = 48 + 16 * slot;
                let offset = self.get_i32(entry + 4);
                if offset >= HEADER_LEN as i32 {
                    let off = offset as usize;
                    if off + row.len() <= self.map.len() {
                        self.map[off..off + row.len()].copy_from_slice(&row);
                    }
                }
                self.put_i32(entry, tick);
                Some(t_ms)
            }
        }
    }

    fn put_i32(&mut self, at: usize, v: i32) {
        if at + 4 <= self.map.len() {
            self.map[at..at + 4].copy_from_slice(&v.to_le_bytes());
        }
    }

    fn get_i32(&self, at: usize) -> i32 {
        self.map
            .get(at..at + 4)
            .map_or(0, |b| i32::from_le_bytes(b.try_into().unwrap()))
    }

    fn pace(&mut self, t_ms: u32) {
        match &self.pacing {
            Pacing::Virtual(clock) => {
                clock.set_mono(u64::from(t_ms.saturating_sub(self.base_t_ms)))
            }
            Pacing::Realtime { speed } => {
                let started = *self.started.get_or_insert_with(Instant::now);
                let due = Duration::from_secs_f64(
                    f64::from(t_ms.saturating_sub(self.base_t_ms)) / 1000.0 / speed.max(0.01),
                );
                if let Some(wait) = due.checked_sub(started.elapsed()) {
                    thread::sleep(wait);
                }
            }
        }
    }
}

impl SdkMap for ReplayMap {
    fn len(&self) -> usize {
        self.map.len()
    }

    fn read(&self, offset: usize, dst: &mut [u8]) -> bool {
        match self.map.get(offset..offset.saturating_add(dst.len())) {
            Some(src) => {
                dst.copy_from_slice(src);
                true
            }
            None => false,
        }
    }

    /// Applies records up to and including the next tick or status record, pacing to its time.
    fn wait(&mut self, _timeout: Duration) {
        while self.next < self.records.len() {
            if let Some(t_ms) = self.apply_next() {
                if self.started.is_none() && matches!(self.pacing, Pacing::Realtime { .. }) {
                    self.base_t_ms = t_ms;
                }
                self.pace(t_ms);
                return;
            }
        }
    }

    fn at_end(&self) -> bool {
        self.next >= self.records.len()
    }
}

pub struct ReplaySource {
    records: Option<Vec<Record>>,
    pacing: Pacing,
}

impl ReplaySource {
    pub fn from_bytes(data: &[u8], pacing: Pacing) -> io::Result<ReplaySource> {
        Ok(ReplaySource {
            records: Some(parse_records(data)?),
            pacing,
        })
    }

    pub fn from_file(path: &Path, pacing: Pacing) -> io::Result<ReplaySource> {
        let mut data = Vec::new();
        std::fs::File::open(path)?.read_to_end(&mut data)?;
        ReplaySource::from_bytes(&data, pacing)
    }
}

impl MapSource for ReplaySource {
    fn open(&mut self) -> io::Result<Option<Box<dyn SdkMap>>> {
        Ok(self
            .records
            .take()
            .map(|r| Box::new(ReplayMap::new(r, self.pacing.clone())) as Box<dyn SdkMap>))
    }
}

// ---- synthetic maps ----

/// SDK variable type codes.
pub mod ty {
    pub const CHAR: i32 = 0;
    pub const BOOL: i32 = 1;
    pub const INT: i32 = 2;
    pub const BITFIELD: i32 = 3;
    pub const FLOAT: i32 = 4;
    pub const DOUBLE: i32 = 5;
}

fn width(ty: i32) -> usize {
    match ty {
        ty::CHAR | ty::BOOL => 1,
        ty::DOUBLE => 8,
        _ => 4,
    }
}

#[derive(Debug, Clone)]
struct VarDef {
    name: String,
    ty: i32,
    count: i32,
    unit: String,
    offset: usize,
}

/// Builds the static region and sample rows of a synthetic map.
#[derive(Debug, Clone)]
pub struct MapBuilder {
    vars: Vec<VarDef>,
    row_len: usize,
    tick_rate: i32,
    session_capacity: usize,
    num_buf: i32,
}

impl MapBuilder {
    pub fn new(tick_rate: i32) -> MapBuilder {
        MapBuilder {
            vars: Vec::new(),
            row_len: 0,
            tick_rate,
            session_capacity: 16 * 1024,
            num_buf: 3,
        }
    }

    /// Adds a variable; offsets are assigned in order with natural alignment.
    pub fn var(mut self, name: &str, var_type: i32, count: i32, unit: &str) -> MapBuilder {
        let w = width(var_type);
        let offset = self.row_len.div_ceil(w) * w;
        self.row_len = offset + w * count.max(1) as usize;
        self.vars.push(VarDef {
            name: name.to_string(),
            ty: var_type,
            count: count.max(1),
            unit: unit.to_string(),
            offset,
        });
        self
    }

    /// The variables the live engine reads (`crate-api.md` table 4.3) plus `IsInGarage`.
    pub fn engine_vars(self) -> MapBuilder {
        use ty::*;
        self.var("SessionTime", DOUBLE, 1, "s")
            .var("SessionNum", INT, 1, "")
            .var("SessionUniqueID", INT, 1, "")
            .var("SessionFlags", BITFIELD, 1, "irsdk_Flags")
            .var("Lap", INT, 1, "")
            .var("LapDistPct", FLOAT, 1, "%")
            .var("LapLastLapTime", FLOAT, 1, "s")
            .var("SessionLapsRemainEx", INT, 1, "")
            .var("SessionLapsRemain", INT, 1, "")
            .var("Speed", FLOAT, 1, "m/s")
            .var("Throttle", FLOAT, 1, "%")
            .var("Brake", FLOAT, 1, "%")
            .var("SteeringWheelAngle", FLOAT, 1, "rad")
            .var("Gear", INT, 1, "")
            .var("RPM", FLOAT, 1, "revs/min")
            .var("YawRate", FLOAT, 1, "rad/s")
            .var("LatAccel", FLOAT, 1, "m/s^2")
            .var("LongAccel", FLOAT, 1, "m/s^2")
            .var("Lat", DOUBLE, 1, "deg")
            .var("Lon", DOUBLE, 1, "deg")
            .var("FuelLevel", FLOAT, 1, "l")
            .var("FuelLevelPct", FLOAT, 1, "%")
            .var("TrackTemp", FLOAT, 1, "C")
            .var("AirTemp", FLOAT, 1, "C")
            .var("dcBrakeBias", FLOAT, 1, "")
            .var("OnPitRoad", BOOL, 1, "")
            .var("IsOnTrack", BOOL, 1, "")
            .var("IsInGarage", BOOL, 1, "")
            .var("PlayerCarIdx", INT, 1, "")
            .var("PlayerCarPosition", INT, 1, "")
            .var("PlayerCarClassPosition", INT, 1, "")
            .var("PlayerCarMyIncidentCount", INT, 1, "x")
            .var("PlayerCarTowTime", FLOAT, 1, "s")
            .var("CarIdxPosition", INT, 64, "")
            .var("CarIdxClassPosition", INT, 64, "")
            .var("CarIdxLap", INT, 64, "")
            .var("CarIdxF2Time", FLOAT, 64, "s")
            .var("CarIdxLastLapTime", FLOAT, 64, "s")
    }

    pub fn session_capacity(mut self, bytes: usize) -> MapBuilder {
        self.session_capacity = bytes;
        self
    }

    pub fn row_len(&self) -> usize {
        self.row_len.div_ceil(16) * 16
    }

    fn layout(&self) -> (usize, usize, usize) {
        let var_off = HEADER_LEN;
        let session_off = var_off + self.vars.len() * VAR_HEADER_LEN;
        let buf0 = (session_off + self.session_capacity).div_ceil(16) * 16;
        (var_off, session_off, buf0)
    }

    pub fn map_len(&self) -> usize {
        let (_, _, buf0) = self.layout();
        buf0 + self.num_buf as usize * self.row_len()
    }

    /// The bytes `[0, static_end)` for a given status and session-info text.
    pub fn static_region(&self, status: i32, session_update: i32, session_info: &str) -> Vec<u8> {
        let (var_off, session_off, buf0) = self.layout();
        let mut bytes = vec![0u8; session_off + self.session_capacity];
        let put = |bytes: &mut Vec<u8>, at: usize, v: i32| {
            bytes[at..at + 4].copy_from_slice(&v.to_le_bytes())
        };
        put(&mut bytes, 0, 2); // ver
        put(&mut bytes, 4, status);
        put(&mut bytes, 8, self.tick_rate);
        put(&mut bytes, 12, session_update);
        let text = session_info.as_bytes();
        let len = (text.len() + 1).min(self.session_capacity);
        put(&mut bytes, 16, len as i32);
        put(&mut bytes, 20, session_off as i32);
        put(&mut bytes, 24, self.vars.len() as i32);
        put(&mut bytes, 28, var_off as i32);
        put(&mut bytes, 32, self.num_buf);
        put(&mut bytes, 36, self.row_len() as i32);
        for k in 0..self.num_buf as usize {
            put(&mut bytes, 48 + 16 * k, 0);
            put(
                &mut bytes,
                48 + 16 * k + 4,
                (buf0 + k * self.row_len()) as i32,
            );
        }
        for (i, v) in self.vars.iter().enumerate() {
            let at = var_off + i * VAR_HEADER_LEN;
            put(&mut bytes, at, v.ty);
            put(&mut bytes, at + 4, v.offset as i32);
            put(&mut bytes, at + 8, v.count);
            for (field, text, cap) in [
                (16, v.name.as_str(), 32),
                (48, "", 64),
                (112, v.unit.as_str(), 32),
            ] {
                let b = text.as_bytes();
                let n = b.len().min(cap - 1);
                bytes[at + field..at + field + n].copy_from_slice(&b[..n]);
            }
        }
        let n = text.len().min(self.session_capacity - 1);
        bytes[session_off..session_off + n].copy_from_slice(&text[..n]);
        bytes
    }

    pub fn row(&self) -> Row {
        let mut index = BTreeMap::new();
        for v in &self.vars {
            index.insert(v.name.clone(), (v.ty, v.offset, v.count as usize));
        }
        Row {
            bytes: vec![0u8; self.row_len()],
            index,
        }
    }

    /// Writes a complete fixture: the static region, then `ticks` as `(t_ms, tick, row)`.
    pub fn write_fixture<W: Write>(
        &self,
        out: W,
        session_info: &str,
        ticks: &[(u32, i32, Row)],
    ) -> io::Result<W> {
        let mut w = FixtureWriter::new(out)?;
        let static_bytes = self.static_region(1, 1, session_info);
        w.write_static(self.map_len() as u32, &static_bytes)?;
        for (t_ms, tick, row) in ticks {
            w.write_tick(*t_ms, 1, self.tick_rate, 1, *tick, &row.bytes)?;
        }
        w.finish()
    }

    /// Writes a fixture from explicit steps, so the session info and status can change mid-run.
    pub fn write_steps<W: Write>(&self, out: W, steps: &[Step]) -> io::Result<W> {
        let mut w = FixtureWriter::new(out)?;
        let (mut status, mut update) = (1, 1);
        for step in steps {
            match step {
                Step::Static {
                    status: s,
                    update: u,
                    session_info,
                } => {
                    status = *s;
                    update = *u;
                    let bytes = self.static_region(status, update, session_info);
                    w.write_static(self.map_len() as u32, &bytes)?;
                }
                Step::Tick { t_ms, tick, row } => {
                    w.write_tick(*t_ms, status, self.tick_rate, update, *tick, &row.bytes)?;
                }
                Step::Status { t_ms, status: s } => {
                    status = *s;
                    w.write_status(*t_ms, status)?;
                }
            }
        }
        w.finish()
    }

    /// A well-formed `.ibt` (disk header, variable table, session info, `rows` sample rows) whose
    /// size equals `buffer_offset + record_count * buffer_length`, so it passes the finalization
    /// gate. For tests that need recordings without a private corpus.
    pub fn ibt_bytes(&self, session_info: &str, rows: &[Row]) -> Vec<u8> {
        let row_len = self.row_len();
        let var_off = 144usize;
        let session_off = var_off + self.vars.len() * VAR_HEADER_LEN;
        let text = session_info.as_bytes();
        let session_len = text.len() + 1;
        let buf_off = (session_off + session_len).div_ceil(16) * 16;
        let mut out = vec![0u8; buf_off + rows.len() * row_len];
        let put = |out: &mut Vec<u8>, at: usize, v: i32| {
            out[at..at + 4].copy_from_slice(&v.to_le_bytes())
        };
        put(&mut out, 0, 2);
        put(&mut out, 8, self.tick_rate);
        put(&mut out, 16, session_len as i32);
        put(&mut out, 20, session_off as i32);
        put(&mut out, 24, self.vars.len() as i32);
        put(&mut out, 28, var_off as i32);
        put(&mut out, 32, 1);
        put(&mut out, 36, row_len as i32);
        put(&mut out, 48, rows.len() as i32);
        put(&mut out, 52, buf_off as i32);
        // Disk sub-header: start date, start time, end time, lap count, record count.
        out[112..120].copy_from_slice(&1_790_000_000i64.to_le_bytes());
        out[120..128].copy_from_slice(&0f64.to_le_bytes());
        out[128..136]
            .copy_from_slice(&(rows.len() as f64 / f64::from(self.tick_rate.max(1))).to_le_bytes());
        put(&mut out, 136, 1);
        put(&mut out, 140, rows.len() as i32);
        for (i, v) in self.vars.iter().enumerate() {
            let at = var_off + i * VAR_HEADER_LEN;
            put(&mut out, at, v.ty);
            put(&mut out, at + 4, v.offset as i32);
            put(&mut out, at + 8, v.count);
            for (field, text, cap) in [(16, v.name.as_str(), 32), (112, v.unit.as_str(), 32)] {
                let b = text.as_bytes();
                let n = b.len().min(cap - 1);
                out[at + field..at + field + n].copy_from_slice(&b[..n]);
            }
        }
        out[session_off..session_off + text.len()].copy_from_slice(text);
        for (i, row) in rows.iter().enumerate() {
            let at = buf_off + i * row_len;
            out[at..at + row_len].copy_from_slice(&row.bytes);
        }
        out
    }
}

/// One step of a synthetic fixture.
#[derive(Debug, Clone)]
pub enum Step {
    /// A new static region (session info or status changed).
    Static {
        status: i32,
        update: i32,
        session_info: String,
    },
    Tick {
        t_ms: u32,
        tick: i32,
        row: Row,
    },
    Status {
        t_ms: u32,
        status: i32,
    },
}

/// One sample row under construction.
#[derive(Debug, Clone)]
pub struct Row {
    pub bytes: Vec<u8>,
    index: BTreeMap<String, (i32, usize, usize)>,
}

impl Row {
    fn slot(&self, name: &str) -> (i32, usize, usize) {
        *self
            .index
            .get(name)
            .unwrap_or_else(|| panic!("variable {name} not in the map"))
    }

    pub fn set_f64(&mut self, name: &str, v: f64) -> &mut Row {
        let (t, off, _) = self.slot(name);
        match t {
            ty::DOUBLE => self.bytes[off..off + 8].copy_from_slice(&v.to_le_bytes()),
            ty::FLOAT => self.bytes[off..off + 4].copy_from_slice(&(v as f32).to_le_bytes()),
            ty::INT | ty::BITFIELD => {
                self.bytes[off..off + 4].copy_from_slice(&(v as i32).to_le_bytes())
            }
            _ => self.bytes[off] = u8::from(v != 0.0),
        }
        self
    }

    pub fn set_i32(&mut self, name: &str, v: i32) -> &mut Row {
        self.set_f64(name, f64::from(v))
    }

    pub fn set_bool(&mut self, name: &str, v: bool) -> &mut Row {
        self.set_f64(name, if v { 1.0 } else { 0.0 })
    }

    pub fn set_bits(&mut self, name: &str, v: u32) -> &mut Row {
        let (_, off, _) = self.slot(name);
        self.bytes[off..off + 4].copy_from_slice(&v.to_le_bytes());
        self
    }

    pub fn set_array_f32(&mut self, name: &str, idx: usize, v: f32) -> &mut Row {
        let (_, off, count) = self.slot(name);
        assert!(idx < count);
        let at = off + 4 * idx;
        self.bytes[at..at + 4].copy_from_slice(&v.to_le_bytes());
        self
    }

    pub fn set_array_i32(&mut self, name: &str, idx: usize, v: i32) -> &mut Row {
        let (_, off, count) = self.slot(name);
        assert!(idx < count);
        let at = off + 4 * idx;
        self.bytes[at..at + 4].copy_from_slice(&v.to_le_bytes());
        self
    }
}

/// Inputs for a synthetic session-info document in iRacing's layout.
#[derive(Debug, Clone)]
pub struct SessionSpec {
    pub subsession_id: i64,
    pub track_name: String,
    pub track_display: String,
    pub session_type: String,
    pub car_path: String,
    pub user_id: i64,
    pub user_name: String,
    pub setup_name: String,
    pub setup_modified: bool,
    pub update_count: i64,
    /// Extra `CarSetup` leaves as `(section path, key, value)`; `("Tires.LeftFront", "ColdPressure", "152 kPa")`.
    pub setup_leaves: Vec<(String, String, String)>,
}

impl Default for SessionSpec {
    fn default() -> Self {
        SessionSpec {
            subsession_id: 0,
            track_name: "vegas oval".into(),
            track_display: "Las Vegas Motor Speedway".into(),
            session_type: "Offline Testing".into(),
            car_path: "stockcars2 supra2019".into(),
            user_id: 123_456,
            user_name: "Joshua Granger".into(),
            setup_name: "base.sto".into(),
            setup_modified: false,
            update_count: 2,
            setup_leaves: vec![
                (
                    "Tires.LeftFront".into(),
                    "ColdPressure".into(),
                    "152 kPa".into(),
                ),
                (
                    "Chassis.Front".into(),
                    "SteeringRatio".into(),
                    "16.5:1".into(),
                ),
            ],
        }
    }
}

impl SessionSpec {
    pub fn yaml(&self) -> String {
        let mut y = String::from("---\n");
        y.push_str(&format!(
            "WeekendInfo:\n TrackName: {}\n TrackDisplayName: {}\n TrackConfigName: ~\n TrackID: 18\n SubSessionID: {}\n WeekendOptions:\n  IsFixedSetup: 0\n",
            self.track_name, self.track_display, self.subsession_id
        ));
        y.push_str(&format!(
            "SessionInfo:\n Sessions:\n - SessionNum: 0\n   SessionType: {}\n   SessionName: ~\n",
            self.session_type
        ));
        y.push_str(&format!(
            "DriverInfo:\n DriverCarIdx: 0\n DriverSetupName: {}\n DriverSetupIsModified: {}\n DriverSetupLoadTypeName: user\n Drivers:\n - CarIdx: 0\n   UserName: {}\n   UserID: {}\n   CarPath: {}\n   CarScreenName: Toyota Supra\n   IRating: 2000\n",
            self.setup_name,
            u8::from(self.setup_modified),
            self.user_name,
            self.user_id,
            self.car_path
        ));
        y.push_str(&format!("CarSetup:\n UpdateCount: {}\n", self.update_count));
        let mut sections: BTreeMap<&str, Vec<(&str, &str)>> = BTreeMap::new();
        for (section, key, value) in &self.setup_leaves {
            sections
                .entry(section.as_str())
                .or_default()
                .push((key, value));
        }
        let mut written: Vec<String> = Vec::new();
        for (section, leaves) in &sections {
            let parts: Vec<&str> = section.split('.').collect();
            for (depth, part) in parts.iter().enumerate() {
                let path = parts[..=depth].join(".");
                if written.contains(&path) {
                    continue;
                }
                y.push_str(&format!("{}{}:\n", " ".repeat(depth + 1), part));
                written.push(path);
            }
            for (key, value) in leaves {
                y.push_str(&format!(
                    "{}{}: {}\n",
                    " ".repeat(parts.len() + 1),
                    key,
                    value
                ));
            }
        }
        y.push_str("...\n");
        y
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn replay_applies_ticks_in_a_ring_and_reports_the_end() {
        let b = MapBuilder::new(60).var("Speed", ty::FLOAT, 1, "m/s");
        let mut ticks = Vec::new();
        for i in 0..5 {
            let mut row = b.row();
            row.set_f64("Speed", f64::from(i));
            ticks.push((i as u32 * 16, 100 + i, row));
        }
        let data = b.write_fixture(Vec::new(), "---\n...\n", &ticks).unwrap();
        let clock = Arc::new(ManualClock::new(1_000));
        let mut src = ReplaySource::from_bytes(&data, Pacing::Virtual(clock.clone())).unwrap();
        let mut map = src.open().unwrap().unwrap();
        assert!(src.open().unwrap().is_none());
        assert_eq!(map.len(), b.map_len());
        let mut header = [0u8; 112];
        assert!(map.read(0, &mut header));
        assert_eq!(
            i32::from_le_bytes(header[4..8].try_into().unwrap()),
            0,
            "not connected until the first tick"
        );
        for i in 0..5 {
            map.wait(Duration::from_millis(1));
            assert!(map.read(0, &mut header));
            assert_eq!(i32::from_le_bytes(header[4..8].try_into().unwrap()), 1);
            assert_eq!(
                crate::util::Clock::mono_ms(clock.as_ref()),
                u64::from(i as u32 * 16)
            );
        }
        assert!(map.at_end());
    }

    #[test]
    fn rejects_foreign_files() {
        assert!(ReplaySource::from_bytes(b"nope", Pacing::Realtime { speed: 1.0 }).is_err());
        let mut data = MAGIC.to_vec();
        data.extend_from_slice(&[2, 1, 0, 0, 0, 0]);
        assert!(ReplaySource::from_bytes(&data, Pacing::Realtime { speed: 1.0 }).is_err());
    }

    #[test]
    fn session_yaml_nests_sections() {
        let yaml = SessionSpec::default().yaml();
        assert!(yaml.contains(
            "\nCarSetup:\n UpdateCount: 2\n Chassis:\n  Front:\n   SteeringRatio: 16.5:1\n"
        ));
        assert!(yaml.contains(" Tires:\n  LeftFront:\n   ColdPressure: 152 kPa\n"));
    }
}

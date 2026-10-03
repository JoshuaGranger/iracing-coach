//! The SDK reader: iRacing's shared-memory map (or a replay of one) in, `Sample`s out.
//!
//! The map is accessed through the `SdkMap` trait by copying (never by borrowing the live memory),
//! so the real Win32 map and the replay fixture run the same code. The reader waits on the
//! data-valid event instead of polling, guards against torn reads (copy the newest buffer, re-read
//! its tick, retry once), re-parses the session info only when the header counter changes, and
//! counts data as live only with status bit 0 set and an advancing tick (P5: stale maps persist
//! with status 0).

use std::io;
use std::sync::Arc;
use std::time::Duration;

use coach_ibt::{SDK_HEADER_LEN, SdkHeader, SessionInfo, VarBuf, VarTable, decode_session_info};
use coach_live::{Sample, SessionContext};
use serde_json::json;

use crate::garage::SetupView;
use crate::gate::Evidence;

/// A readable region of memory shared with iRacing, or a replay of one.
pub trait SdkMap: Send {
    /// Length of the mapped region in bytes.
    fn len(&self) -> usize;
    fn is_empty(&self) -> bool {
        self.len() == 0
    }
    /// Copies `dst.len()` bytes starting at `offset`; false when the range is outside the map.
    fn read(&self, offset: usize, dst: &mut [u8]) -> bool;
    /// Blocks up to `timeout` for fresh data (the data-valid event, or a timer when it is absent).
    fn wait(&mut self, timeout: Duration);
    /// True once a replay has delivered its last record.
    fn at_end(&self) -> bool {
        false
    }
}

/// Opens the map. `Ok(None)` means the mapping does not exist (iRacing is not running); any other
/// failure is an error, which is unknown evidence, not permission for file work.
pub trait MapSource: Send {
    fn open(&mut self) -> io::Result<Option<Box<dyn SdkMap>>>;
}

/// What one pass of the reader produced.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Poll {
    /// Nothing new.
    NoData,
    /// `sample()` holds a new connected sample.
    Tick,
    /// `sample()` holds a disconnected sample (at most one per second while disconnected or stale).
    Disconnected,
}

/// The newest row copied out of the map.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RowRead {
    /// A consistent row of `tick` is in the buffer.
    Row { tick: i32 },
    /// No buffer qualifies.
    NoBuffer,
    /// The buffer changed under the copy twice in a row.
    Torn,
}

/// Reads the header, or None when the map is too short or unreadable.
pub fn read_header(map: &dyn SdkMap) -> Option<SdkHeader> {
    let mut bytes = [0u8; SDK_HEADER_LEN];
    if !map.read(0, &mut bytes) {
        return None;
    }
    SdkHeader::parse(&bytes).ok()
}

/// Copies the newest buffer's row into `row` with the torn-read guard: after the copy the header
/// is read again and the buffer must still carry the same tick at the same offset; otherwise one
/// retry, then `Torn`. Returns the header that validated the copy.
pub fn read_newest_row(
    map: &dyn SdkMap,
    header: &SdkHeader,
    row: &mut Vec<u8>,
) -> (RowRead, SdkHeader) {
    let mut header = *header;
    for _ in 0..2 {
        let Some(buf) = header.newest_buffer(map.len()) else {
            return (RowRead::NoBuffer, header);
        };
        row.resize(header.buf_len.max(0) as usize, 0);
        if !map.read(buf.buf_offset as usize, row) {
            return (RowRead::NoBuffer, header);
        }
        match read_header(map) {
            Some(after) if buffer_unchanged(&after, buf) => {
                return (
                    RowRead::Row {
                        tick: buf.tick_count,
                    },
                    after,
                );
            }
            Some(after) => header = after,
            None => return (RowRead::NoBuffer, header),
        }
    }
    (RowRead::Torn, header)
}

fn buffer_unchanged(header: &SdkHeader, buf: VarBuf) -> bool {
    header
        .var_bufs
        .iter()
        .take(header.num_buf.clamp(0, 4) as usize)
        .any(|b| b.buf_offset == buf.buf_offset && b.tick_count == buf.tick_count)
}

/// Copies the whole map (used when the variable table or session info must be re-read).
fn read_all(map: &dyn SdkMap) -> Option<Vec<u8>> {
    let mut bytes = vec![0u8; map.len()];
    map.read(0, &mut bytes).then_some(bytes)
}

/// Staleness limits (`collector-protocol.md` section 4 and the `Sample.connected` contract).
const LIVE_FRESH_MS: u64 = 1_000;
const CONNECTED_FRESH_MS: u64 = 2_000;
const REOPEN_INTERVAL_MS: u64 = 1_000;
const DISCONNECTED_PULSE_MS: u64 = 1_000;
/// A map that stays dead this long is closed and opened again: iRacing creates a new mapping object
/// each run, and our handle would keep the old one alive and stale forever.
const REOPEN_AFTER_CLEAR_MS: u64 = 3_000;
const REOPEN_AFTER_STALE_MS: u64 = 5_000;

pub struct SdkReader {
    source: Box<dyn MapSource>,
    map: Option<Box<dyn SdkMap>>,
    last_open_ms: Option<u64>,
    header: Option<SdkHeader>,
    vars: Option<(VarTable, i32, i32)>,
    session_update: Option<i32>,
    context: Option<Arc<SessionContext>>,
    setup: SetupView,
    /// Set on the 0 to 1 status edge so the session info is read again even if the counter
    /// happens to repeat.
    force_reparse: bool,
    was_connected: bool,
    row: Vec<u8>,
    sample: Sample,
    last_tick: Option<i32>,
    last_advance_ms: Option<u64>,
    last_pulse_ms: Option<u64>,
    evidence: Evidence,
    warned_yaml: Option<i32>,
    /// Since when the map has been unusable (status clear, or ticks stopped).
    dead_since: Option<u64>,
}

impl SdkReader {
    pub fn new(source: Box<dyn MapSource>) -> SdkReader {
        SdkReader {
            source,
            map: None,
            last_open_ms: None,
            header: None,
            vars: None,
            session_update: None,
            context: None,
            setup: SetupView::default(),
            force_reparse: true,
            was_connected: false,
            row: Vec::new(),
            sample: Sample::default(),
            last_tick: None,
            last_advance_ms: None,
            last_pulse_ms: None,
            evidence: Evidence::NoMap,
            warned_yaml: None,
            dead_since: None,
        }
    }

    pub fn sample(&self) -> &Sample {
        &self.sample
    }

    pub fn setup(&self) -> &SetupView {
        &self.setup
    }

    pub fn context(&self) -> Option<&Arc<SessionContext>> {
        self.context.as_ref()
    }

    pub fn evidence(&self) -> Evidence {
        self.evidence
    }

    pub fn header(&self) -> Option<&SdkHeader> {
        self.header.as_ref()
    }

    pub fn at_end(&self) -> bool {
        self.map.as_ref().is_some_and(|m| m.at_end())
    }

    /// Waits for the data-valid event for at most `timeout`; while no map is open it simply sleeps
    /// (the probe itself is paced inside `poll`).
    pub fn wait(&mut self, timeout: Duration) {
        match self.map.as_mut() {
            Some(map) => map.wait(timeout),
            None => std::thread::sleep(timeout),
        }
    }

    /// One pass. `now_ms` is the monotonic clock, `unix_ms` the wall clock stamped on samples.
    pub fn poll(&mut self, now_ms: u64, unix_ms: u64) -> Poll {
        if self.map.is_none() {
            if self
                .last_open_ms
                .is_some_and(|t| now_ms.saturating_sub(t) < REOPEN_INTERVAL_MS)
            {
                // Between attempts the last evidence stands: `NoMap` only after an open that
                // found no mapping, `Stale` after a failed one.
                return Poll::NoData;
            }
            self.last_open_ms = Some(now_ms);
            match self.source.open() {
                Ok(Some(map)) => self.map = Some(map),
                Ok(None) => {
                    self.evidence = Evidence::NoMap;
                    return self.disconnected_pulse(now_ms, unix_ms);
                }
                Err(e) => {
                    // Access denied, out of resources, a mapping that cannot be sized: iRacing
                    // may well be running on track.
                    crate::debug!("sdk map open failed: {e}");
                    self.evidence = Evidence::Stale;
                    return self.disconnected_pulse(now_ms, unix_ms);
                }
            }
        }

        let Some(header) = self.map.as_deref().and_then(read_header) else {
            // The map exists but its header cannot be read: unknown, not absent.
            self.close_map();
            self.evidence = Evidence::Stale;
            return self.disconnected_pulse(now_ms, unix_ms);
        };
        self.header = Some(header);

        if !header.connected() {
            self.on_status_clear();
            self.evidence = Evidence::StatusClear;
            if self.dead_for(now_ms) >= REOPEN_AFTER_CLEAR_MS {
                self.close_map();
            }
            return self.disconnected_pulse(now_ms, unix_ms);
        }
        if !self.was_connected {
            self.force_reparse = true;
            self.last_tick = None;
            self.last_advance_ms = None;
            self.dead_since = None;
        }
        self.was_connected = true;

        let map_len = self.map.as_ref().map_or(0, |m| m.len());
        if let Err(e) = header.validate_live(map_len) {
            crate::warn!("sdk map rejected: {e}");
            self.evidence = Evidence::Stale;
            return self.disconnected_pulse(now_ms, unix_ms);
        }

        if !self.refresh_static(&header) {
            self.evidence = Evidence::Stale;
            return self.disconnected_pulse(now_ms, unix_ms);
        }

        let Some(map) = self.map.as_deref() else {
            return Poll::NoData;
        };
        let (read, after) = read_newest_row(map, &header, &mut self.row);
        self.header = Some(after);
        match read {
            RowRead::Row { tick } if self.last_tick != Some(tick) => {
                self.last_tick = Some(tick);
                self.last_advance_ms = Some(now_ms);
                self.dead_since = None;
                let Some((vars, _, _)) = self.vars.as_ref() else {
                    return Poll::NoData;
                };
                self.sample.clear();
                self.sample.captured_unix_ms = unix_ms;
                self.sample.connected = true;
                self.sample.tick = tick as u32;
                self.sample.tick_rate = after.tick_rate.clamp(0, u16::MAX as i32) as u16;
                self.sample.session = self.context.clone();
                self.sample.read_row(vars, &self.row);
                self.evidence = Evidence::Live {
                    on_track: self.sample.is_on_track,
                };
                Poll::Tick
            }
            RowRead::Row { .. } | RowRead::NoBuffer | RowRead::Torn => {
                let since = self
                    .last_advance_ms
                    .map_or(u64::MAX, |t| now_ms.saturating_sub(t));
                if since <= LIVE_FRESH_MS {
                    return Poll::NoData;
                }
                self.evidence = Evidence::Stale;
                if self.dead_for(now_ms) >= REOPEN_AFTER_STALE_MS {
                    self.close_map();
                }
                if since > CONNECTED_FRESH_MS || self.last_advance_ms.is_none() {
                    return self.disconnected_pulse(now_ms, unix_ms);
                }
                Poll::NoData
            }
        }
    }

    /// How long the map has been unusable, starting the count on the first call.
    fn dead_for(&mut self, now_ms: u64) -> u64 {
        now_ms.saturating_sub(*self.dead_since.get_or_insert(now_ms))
    }

    fn on_status_clear(&mut self) {
        self.was_connected = false;
        self.last_tick = None;
        self.last_advance_ms = None;
        self.vars = None;
        self.session_update = None;
    }

    fn close_map(&mut self) {
        self.dead_since = None;
        self.map = None;
        self.header = None;
        self.on_status_clear();
    }

    /// A disconnected sample at most once per second (the engine resets on it).
    fn disconnected_pulse(&mut self, now_ms: u64, unix_ms: u64) -> Poll {
        if self
            .last_pulse_ms
            .is_some_and(|t| now_ms.saturating_sub(t) < DISCONNECTED_PULSE_MS)
        {
            return Poll::NoData;
        }
        self.last_pulse_ms = Some(now_ms);
        self.sample.clear();
        self.sample.captured_unix_ms = unix_ms;
        self.sample.connected = false;
        self.sample.tick = self.last_tick.unwrap_or(0) as u32;
        self.sample.tick_rate = self
            .header
            .map_or(0, |h| h.tick_rate.clamp(0, u16::MAX as i32) as u16);
        self.sample.session = self.context.clone();
        Poll::Disconnected
    }

    /// Re-reads the variable table and session info when their header fields changed. False when
    /// the map cannot be used yet.
    fn refresh_static(&mut self, header: &SdkHeader) -> bool {
        let vars_changed = self
            .vars
            .as_ref()
            .is_none_or(|(_, n, off)| *n != header.num_vars || *off != header.var_header_offset);
        let info_changed =
            self.force_reparse || self.session_update != Some(header.session_info_update);
        if !vars_changed && !info_changed {
            return true;
        }
        let Some(map) = self.map.as_deref() else {
            return false;
        };
        if vars_changed {
            let Some(all) = read_all(map) else {
                return false;
            };
            match VarTable::parse_live(&all, header) {
                Ok(table) => self.vars = Some((table, header.num_vars, header.var_header_offset)),
                Err(e) => {
                    crate::warn!("variable table rejected: {e}");
                    return false;
                }
            }
        }
        if info_changed {
            let mut bytes = vec![0u8; header.session_info_len.max(0) as usize];
            if !map.read(header.session_info_offset.max(0) as usize, &mut bytes) {
                return false;
            }
            // Torn guard: the counter must not move while the text is copied.
            match read_header(map) {
                Some(after) if after.session_info_update == header.session_info_update => {}
                _ => return self.vars.is_some() && self.session_update.is_some(),
            }
            self.session_update = Some(header.session_info_update);
            self.force_reparse = false;
            let text = decode_session_info(&bytes);
            match SessionInfo::parse(&text) {
                Ok(info) => self.apply_session_info(&info, header.session_info_update),
                Err(e) => {
                    if self.warned_yaml != Some(header.session_info_update) {
                        crate::warn!("session info not parsed: {e}");
                        self.warned_yaml = Some(header.session_info_update);
                    }
                }
            }
        }
        self.vars.is_some()
    }

    fn apply_session_info(&mut self, info: &SessionInfo, update: i32) {
        let ctx = SessionContext::from_session_info(info, update);
        let driver = info.driver();
        let setup = info.driver_setup();
        let car = info.car_setup();
        self.setup = SetupView {
            update_count: car.as_ref().and_then(|c| c.update_count),
            values: car.map(|c| c.values).unwrap_or_default(),
            // While a session loads iRacing publishes the placeholder `INVALID_LOADTYPE` (load
            // type `invalid`, seen on the rig, G3): it names no file.
            name: setup
                .name
                .filter(|n| !n.eq_ignore_ascii_case("INVALID_LOADTYPE")),
            is_modified: setup.is_modified,
            load_type: setup.load_type_name,
            car_path: driver
                .as_ref()
                .and_then(|d| d.car_path.clone())
                .or_else(|| ctx.car_path.clone()),
            driver: driver.as_ref().and_then(|d| {
                d.user_id
                    .map(|id| json!({"customer_id": id, "name": d.user_name}))
            }),
            session: None,
        };
        self.context = Some(Arc::new(ctx));
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::gate::TrackGate;
    use crate::util::ManualClock;

    /// A map too short to hold the header: it opens, but nothing can be read from it.
    struct ShortMap;

    impl SdkMap for ShortMap {
        fn len(&self) -> usize {
            8
        }
        fn read(&self, offset: usize, dst: &mut [u8]) -> bool {
            offset + dst.len() <= 8 && {
                dst.fill(0);
                true
            }
        }
        fn wait(&mut self, _: Duration) {}
    }

    enum Open {
        /// The mapping does not exist.
        Absent,
        /// Opening failed for another reason (access denied, no resources).
        Failed,
        /// The mapping opens but its header is unreadable.
        ShortHeader,
    }

    impl MapSource for Open {
        fn open(&mut self) -> io::Result<Option<Box<dyn SdkMap>>> {
            match self {
                Open::Absent => Ok(None),
                Open::Failed => Err(io::Error::other("access denied")),
                Open::ShortHeader => Ok(Some(Box::new(ShortMap))),
            }
        }
    }

    /// What the gate allows after the reader's pass at `now_ms` (the reader loop reports its
    /// evidence to the gate after every pass).
    fn allows(reader: &mut SdkReader, gate: &TrackGate, now_ms: u64) -> bool {
        reader.poll(now_ms, now_ms);
        gate.observe(reader.evidence());
        gate.allows_file_work()
    }

    fn gate() -> Arc<TrackGate> {
        TrackGate::new(Arc::new(ManualClock::new(0)))
    }

    #[test]
    fn a_missing_mapping_is_no_map_and_allows_file_work() {
        let gate = gate();
        let mut reader = SdkReader::new(Box::new(Open::Absent));
        assert!(allows(&mut reader, &gate, 0));
        assert_eq!(reader.evidence(), Evidence::NoMap);
        // Between two attempts nothing changes.
        assert!(allows(&mut reader, &gate, 100));
    }

    #[test]
    fn an_open_failure_is_unknown_evidence_before_and_between_attempts() {
        let gate = gate();
        let mut reader = SdkReader::new(Box::new(Open::Failed));
        assert!(!allows(&mut reader, &gate, 0));
        assert_eq!(reader.evidence(), Evidence::Stale);
        // The throttled passes after the failed attempt must not flip to "no map".
        assert!(!allows(&mut reader, &gate, 100));
        assert!(!allows(&mut reader, &gate, 900));
        assert!(!allows(&mut reader, &gate, 1_000), "the retry fails again");
    }

    #[test]
    fn an_unreadable_header_is_unknown_evidence_and_stays_so_until_the_map_is_absent() {
        let gate = gate();
        let mut reader = SdkReader::new(Box::new(Open::ShortHeader));
        assert!(!allows(&mut reader, &gate, 0));
        assert_eq!(reader.evidence(), Evidence::Stale);
        // The map was closed; during the wait before the next open it is still unknown.
        assert!(!allows(&mut reader, &gate, 200));
        assert_eq!(reader.evidence(), Evidence::Stale);
        // The mapping then disappears: only now is it "not running".
        reader.source = Box::new(Open::Absent);
        assert!(allows(&mut reader, &gate, 1_200));
        assert_eq!(reader.evidence(), Evidence::NoMap);
    }
}

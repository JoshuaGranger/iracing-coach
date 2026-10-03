//! Collector hub and live relay (`collector-protocol.md` sections 5-7, `live-frame.md`):
//! collector presence, viewer counting and stream control, per-driver backlog, per-browser
//! decimation and bounded queues.

use std::collections::{HashMap, VecDeque};
use std::sync::atomic::{AtomicBool, AtomicU32, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Instant;

use axum::body::Bytes;
use serde_json::{Value, json};
use tokio::sync::{Notify, mpsc};

use crate::util;

pub const FRAME_HEADER_LEN: usize = 38;
/// 10 minutes at 10 Hz.
pub const BACKLOG_FRAMES: usize = 6000;
pub const BACKLOG_GAP_MS: u64 = 100;

#[derive(Debug)]
pub enum Outgoing {
    Json(Value),
    Close(u16, &'static str),
}

pub struct CollectorConn {
    pub conn_id: u64,
    pub tx: mpsc::UnboundedSender<Outgoing>,
    pub machine: String,
    pub version: String,
    pub protocol: i64,
    pub compatible: bool,
    pub online: bool,
    pub last_seen: i64,
    pub status: Option<Value>,
    pub driver: Option<i64>,
    pub streaming: bool,
    pub newer_than_server: bool,
    pub bad_frame_logged: bool,
    pub frames_relayed: u64,
    pub frames_invalid: u64,
}

pub struct Viewer {
    pub id: u64,
    pub customer: i64,
    pub rate: AtomicU32,
    queue: Mutex<VecDeque<Bytes>>,
    pub notify: Notify,
    last_sent_ms: AtomicU64,
    /// When this viewer began dropping frames and has not caught up since (`drain`).
    behind_since: Mutex<Option<Instant>>,
    /// The queue overflowed since the last `drain`.
    overflowed: AtomicBool,
    pub closed: AtomicBool,
}

impl Viewer {
    fn capacity(&self) -> usize {
        (self.rate.load(Ordering::Relaxed) as usize).max(2)
    }

    /// Applies the viewer's decimation and bounded queue; overflow drops the queue and keeps
    /// the newest frame.
    fn offer(&self, frame: &Bytes, captured_ms: u64) {
        let rate = self.rate.load(Ordering::Relaxed).max(1) as u64;
        let gap = 1000 / rate;
        let last = self.last_sent_ms.load(Ordering::Relaxed);
        let due = last == 0 || captured_ms < last || captured_ms + 2 >= last + gap;
        if !due {
            return;
        }
        self.last_sent_ms
            .store(captured_ms.max(1), Ordering::Relaxed);
        let mut queue = self.queue.lock().unwrap();
        if queue.len() >= self.capacity() {
            queue.clear();
            self.overflowed.store(true, Ordering::Relaxed);
            let mut behind = self.behind_since.lock().unwrap();
            behind.get_or_insert_with(Instant::now);
        }
        queue.push_back(frame.clone());
        drop(queue);
        self.notify.notify_one();
    }

    pub fn push_backlog(&self, frame: Bytes) {
        // Backlog frames are queued beyond the live capacity; they drain first.
        self.queue.lock().unwrap().push_back(frame);
        self.notify.notify_one();
    }

    /// Takes the queued frames. The viewer has caught up only when nothing overflowed since the
    /// previous drain: a drain that merely emptied a queue the sender kept overflowing does not
    /// reset the lag (`collector-protocol.md` section 7).
    pub fn drain(&self) -> Vec<Bytes> {
        let frames: Vec<Bytes> = self.queue.lock().unwrap().drain(..).collect();
        if !self.overflowed.swap(false, Ordering::Relaxed) {
            *self.behind_since.lock().unwrap() = None;
        }
        frames
    }

    /// How long the viewer has been dropping frames without catching up.
    pub fn behind_for(&self) -> Option<std::time::Duration> {
        self.behind_since.lock().unwrap().map(|t| t.elapsed())
    }
}

#[derive(Default)]
struct Inner {
    collectors: HashMap<String, CollectorConn>,
    viewers: HashMap<i64, Vec<Arc<Viewer>>>,
    backlog: HashMap<i64, (VecDeque<Bytes>, u64)>,
}

#[derive(Default)]
pub struct Hub {
    inner: Mutex<Inner>,
    next_id: AtomicU64,
}

#[derive(Debug, PartialEq)]
pub enum FrameVerdict {
    Relayed,
    /// Header invalid; dropped (logged once per connection).
    Invalid(String),
    /// `table_version` above the server's; not relayed.
    NewerTable,
    /// The collector has not reported a driver yet.
    NoDriver,
}

fn u16_at(b: &[u8], at: usize) -> u16 {
    u16::from_le_bytes([b[at], b[at + 1]])
}

/// Validates the fixed header (bytes 0 to 37) against this server's channel table.
pub fn validate_frame(frame: &[u8]) -> std::result::Result<(), FrameVerdict> {
    if frame.len() < FRAME_HEADER_LEN {
        return Err(FrameVerdict::Invalid(format!(
            "frame is {} bytes, header is 38",
            frame.len()
        )));
    }
    if &frame[0..2] != b"LF" {
        return Err(FrameVerdict::Invalid("bad magic".into()));
    }
    if frame[2] != 1 {
        return Err(FrameVerdict::Invalid(format!(
            "unsupported frame version {}",
            frame[2]
        )));
    }
    let table = u16_at(frame, 34);
    let channels = u16_at(frame, 36) as usize;
    if table > coach_wire::live_channels::TABLE_VERSION {
        return Err(FrameVerdict::NewerTable);
    }
    if table != coach_wire::live_channels::TABLE_VERSION {
        return Err(FrameVerdict::Invalid(format!(
            "unknown table version {table}"
        )));
    }
    if channels > coach_wire::live_channels::CHANNELS.len() {
        return Err(FrameVerdict::Invalid(format!(
            "channel_count {channels} exceeds the table"
        )));
    }
    if frame.len() < FRAME_HEADER_LEN + channels.div_ceil(8) {
        return Err(FrameVerdict::Invalid(
            "frame is shorter than its presence bitmap".into(),
        ));
    }
    Ok(())
}

pub fn captured_ms(frame: &[u8]) -> u64 {
    let mut b = [0u8; 8];
    b.copy_from_slice(&frame[12..20]);
    u64::from_le_bytes(b)
}

impl Hub {
    pub fn next_id(&self) -> u64 {
        self.next_id.fetch_add(1, Ordering::SeqCst) + 1
    }

    /// Registers a collector connection; an older connection with the same id is closed (4002).
    pub fn register(&self, collector_id: &str, conn: CollectorConn) -> bool {
        let mut inner = self.inner.lock().unwrap();
        let replaced = if let Some(old) = inner.collectors.remove(collector_id) {
            if old.online {
                let _ = old.tx.send(Outgoing::Close(4002, "duplicate_collector"));
            }
            true
        } else {
            false
        };
        inner.collectors.insert(collector_id.to_string(), conn);
        replaced
    }

    /// Marks a connection offline (only when it is still the registered one).
    pub fn disconnect(&self, collector_id: &str, conn_id: u64) -> Option<Value> {
        let mut inner = self.inner.lock().unwrap();
        let driver;
        {
            let c = inner.collectors.get_mut(collector_id)?;
            if c.conn_id != conn_id {
                return None;
            }
            c.online = false;
            c.streaming = false;
            driver = c.driver;
        }
        if let Some(driver) = driver
            && !inner
                .collectors
                .values()
                .any(|c| c.online && c.driver == Some(driver) && c.streaming)
        {
            inner.backlog.remove(&driver);
        }
        let viewers = driver
            .map(|d| inner.viewers.get(&d).map(|v| v.len()).unwrap_or(0))
            .unwrap_or(0);
        inner
            .collectors
            .get(collector_id)
            .map(|c| presence_json(collector_id, c, viewers))
    }

    pub fn touch(&self, collector_id: &str, conn_id: u64) {
        if let Some(c) = self.inner.lock().unwrap().collectors.get_mut(collector_id)
            && c.conn_id == conn_id
        {
            c.last_seen = util::unix_now();
        }
    }

    /// Stores a status; returns the presence JSON, whether the driver changed, and the viewer
    /// count for the (new) driver so the caller can start the stream.
    pub fn update_status(
        &self,
        collector_id: &str,
        conn_id: u64,
        status: Value,
    ) -> Option<StatusUpdate> {
        let mut inner = self.inner.lock().unwrap();
        let c = inner.collectors.get_mut(collector_id)?;
        if c.conn_id != conn_id {
            return None;
        }
        c.last_seen = util::unix_now();
        let new_driver = status["driver"]["customer_id"].as_i64();
        let driver_changed = new_driver != c.driver;
        let previous_driver = c.driver;
        c.driver = new_driver;
        let was_streaming = c.streaming;
        c.streaming = status["streaming"].as_bool().unwrap_or(false);
        let streaming_stopped = was_streaming && !c.streaming;
        c.status = Some(status);
        let driver = c.driver;
        let viewers = driver
            .map(|d| inner.viewers.get(&d).map(|v| v.len()).unwrap_or(0))
            .unwrap_or(0);
        if streaming_stopped && let Some(d) = driver {
            inner.backlog.remove(&d);
        }
        let c = inner.collectors.get(collector_id)?;
        Some(StatusUpdate {
            presence: presence_json(collector_id, c, viewers),
            driver,
            driver_changed,
            previous_driver,
            viewers,
        })
    }

    pub fn viewer_count(&self, customer: i64) -> usize {
        self.inner
            .lock()
            .unwrap()
            .viewers
            .get(&customer)
            .map(|v| v.len())
            .unwrap_or(0)
    }

    pub fn is_live(&self, customer: i64) -> bool {
        self.inner.lock().unwrap().collectors.values().any(|c| {
            c.online
                && c.driver == Some(customer)
                && c.status.as_ref().is_some_and(|s| s["sim"] == "in_session")
        })
    }

    pub fn collectors_for(&self, customer: i64) -> Vec<String> {
        self.inner
            .lock()
            .unwrap()
            .collectors
            .iter()
            .filter(|(_, c)| c.online && c.driver == Some(customer))
            .map(|(id, _)| id.clone())
            .collect()
    }

    /// `stream_wanted` and the viewer count for a collector's current driver.
    pub fn demand_for(&self, collector_id: &str) -> (bool, usize) {
        let inner = self.inner.lock().unwrap();
        let driver = inner.collectors.get(collector_id).and_then(|c| c.driver);
        let viewers = driver
            .map(|d| inner.viewers.get(&d).map(|v| v.len()).unwrap_or(0))
            .unwrap_or(0);
        (viewers > 0, viewers)
    }

    pub fn send(&self, collector_id: &str, message: Value) {
        if let Some(c) = self.inner.lock().unwrap().collectors.get(collector_id)
            && c.online
        {
            let _ = c.tx.send(Outgoing::Json(message));
        }
    }

    /// Adds a browser socket and tells the driver's collectors about the new demand.
    pub fn add_viewer(&self, customer: i64, rate: u32) -> (Arc<Viewer>, Vec<Bytes>, usize) {
        let viewer = Arc::new(Viewer {
            id: self.next_id(),
            customer,
            rate: AtomicU32::new(rate),
            queue: Mutex::new(VecDeque::new()),
            notify: Notify::new(),
            last_sent_ms: AtomicU64::new(0),
            behind_since: Mutex::new(None),
            overflowed: AtomicBool::new(false),
            closed: AtomicBool::new(false),
        });
        let mut inner = self.inner.lock().unwrap();
        let before = inner.viewers.get(&customer).map(|v| v.len()).unwrap_or(0);
        inner
            .viewers
            .entry(customer)
            .or_default()
            .push(viewer.clone());
        let count = before + 1;
        // backlog: copies with bit6 set, decimated to this viewer's rate
        let backlog: Vec<Bytes> = inner
            .backlog
            .get(&customer)
            .map(|(frames, _)| {
                let gap = 1000 / rate.max(1) as u64;
                let mut last = 0u64;
                frames
                    .iter()
                    .filter(|f| {
                        let t = captured_ms(f);
                        let take = last == 0 || t + 2 >= last + gap || t < last;
                        if take {
                            last = t;
                        }
                        take
                    })
                    .map(|f| {
                        let mut copy = f.to_vec();
                        copy[3] |= 0x40;
                        Bytes::from(copy)
                    })
                    .collect()
            })
            .unwrap_or_default();
        for c in inner
            .collectors
            .values()
            .filter(|c| c.online && c.driver == Some(customer))
        {
            let _ =
                c.tx.send(Outgoing::Json(json!({"type": "viewers", "count": count})));
            if before == 0 {
                let _ = c.tx.send(Outgoing::Json(
                    json!({"type": "stream.start", "reason": "viewer"}),
                ));
            }
        }
        (viewer, backlog, count)
    }

    pub fn remove_viewer(&self, customer: i64, viewer_id: u64) {
        let mut inner = self.inner.lock().unwrap();
        let Some(list) = inner.viewers.get_mut(&customer) else {
            return;
        };
        let before = list.len();
        list.retain(|v| v.id != viewer_id);
        let after = list.len();
        if after == 0 {
            inner.viewers.remove(&customer);
        }
        if before == after {
            return;
        }
        for c in inner
            .collectors
            .values()
            .filter(|c| c.online && c.driver == Some(customer))
        {
            let _ =
                c.tx.send(Outgoing::Json(json!({"type": "viewers", "count": after})));
            if after == 0 {
                let _ = c.tx.send(Outgoing::Json(json!({"type": "stream.release"})));
            }
        }
    }

    /// Validates and fans out one collector frame.
    pub fn relay(&self, collector_id: &str, conn_id: u64, frame: Bytes) -> FrameVerdict {
        let mut inner = self.inner.lock().unwrap();
        let driver = {
            let Some(c) = inner.collectors.get_mut(collector_id) else {
                return FrameVerdict::NoDriver;
            };
            if c.conn_id != conn_id {
                return FrameVerdict::NoDriver;
            }
            match validate_frame(&frame) {
                Ok(()) => {}
                Err(FrameVerdict::NewerTable) => {
                    c.newer_than_server = true;
                    c.compatible = false;
                    return FrameVerdict::NewerTable;
                }
                Err(verdict) => {
                    c.frames_invalid += 1;
                    if let FrameVerdict::Invalid(reason) = &verdict
                        && !c.bad_frame_logged
                    {
                        c.bad_frame_logged = true;
                        tracing::error!(collector = collector_id, %reason, "invalid live frame dropped");
                    }
                    return verdict;
                }
            }
            c.frames_relayed += 1;
            let Some(driver) = c.driver else {
                return FrameVerdict::NoDriver;
            };
            driver
        };
        let ms = captured_ms(&frame);
        // backlog at 10 Hz
        let entry = inner.backlog.entry(driver).or_default();
        if entry.1 == 0 || ms + 2 >= entry.1 + BACKLOG_GAP_MS || ms < entry.1 {
            entry.1 = ms.max(1);
            if entry.0.len() >= BACKLOG_FRAMES {
                entry.0.pop_front();
            }
            entry.0.push_back(frame.clone());
        }
        if let Some(viewers) = inner.viewers.get(&driver) {
            for v in viewers {
                v.offer(&frame, ms);
            }
        }
        FrameVerdict::Relayed
    }

    pub fn backlog_len(&self, customer: i64) -> usize {
        self.inner
            .lock()
            .unwrap()
            .backlog
            .get(&customer)
            .map(|b| b.0.len())
            .unwrap_or(0)
    }

    pub fn collector_flags(&self, collector_id: &str) -> Option<(u64, u64, bool)> {
        self.inner
            .lock()
            .unwrap()
            .collectors
            .get(collector_id)
            .map(|c| (c.frames_relayed, c.frames_invalid, c.newer_than_server))
    }

    pub fn presence_list(&self) -> Vec<Value> {
        let inner = self.inner.lock().unwrap();
        let mut list: Vec<Value> = inner
            .collectors
            .iter()
            .map(|(id, c)| {
                let viewers = c
                    .driver
                    .map(|d| inner.viewers.get(&d).map(|v| v.len()).unwrap_or(0))
                    .unwrap_or(0);
                presence_json(id, c, viewers)
            })
            .collect();
        list.sort_by_key(|v| v["collector_id"].as_str().unwrap_or_default().to_string());
        list
    }

    /// Collectors silent for longer than `idle_secs`.
    pub fn stale(&self, idle_secs: i64) -> Vec<(String, u64)> {
        let now = util::unix_now();
        self.inner
            .lock()
            .unwrap()
            .collectors
            .iter()
            .filter(|(_, c)| c.online && now - c.last_seen > idle_secs)
            .map(|(id, c)| (id.clone(), c.conn_id))
            .collect()
    }
}

pub struct StatusUpdate {
    pub presence: Value,
    pub driver: Option<i64>,
    pub driver_changed: bool,
    /// The driver before this status (the demand the collector holds belongs to it).
    pub previous_driver: Option<i64>,
    pub viewers: usize,
}

pub fn presence_json(collector_id: &str, c: &CollectorConn, viewers: usize) -> Value {
    json!({
        "collector_id": collector_id,
        "machine": c.machine,
        "collector_version": c.version,
        "protocol": c.protocol,
        "compatible": c.compatible,
        "online": c.online,
        "last_seen_at": util::iso_from_unix(c.last_seen),
        "status": c.status,
        "viewers": viewers,
    })
}

/// Built-in live dashboard (`factory-default`), immutable.
pub fn default_live_layout() -> Value {
    let tile = |id: &str, metric: &str, row: i64, col: i64, style: &str| json!({"tile_id": id, "metric": metric, "row": row, "column": col, "row_span": 1, "column_span": 1, "style": style});
    json!({
        "rows": 2,
        "columns": 3,
        "tiles": [
            tile("position", "position", 0, 0, "number"),
            tile("last-lap", "last-lap", 0, 1, "number"),
            tile("leader-gap", "leader-gap", 0, 2, "number"),
            tile("fuel", "fuel", 1, 0, "bar"),
            tile("pit-window", "pit-window", 1, 1, "number"),
            tile("coach-cue", "coach-cue", 1, 2, "status"),
        ],
    })
}

/// Built-in trace layout (`default`), immutable: the six critical channels.
pub fn default_trace_layout() -> Value {
    json!({
        "rows": [
            {"row_id": "speed", "label": null, "signals": ["speed"]},
            {"row_id": "delta", "label": null, "signals": ["delta"]},
            {"row_id": "pedals", "label": null, "signals": ["throttle", "brake"]},
            {"row_id": "steering", "label": null, "signals": ["steering"]},
            {"row_id": "slip", "label": null, "signals": ["slip"]},
        ]
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn viewer(rate: u32) -> Viewer {
        Viewer {
            id: 1,
            customer: 1,
            rate: AtomicU32::new(rate),
            queue: Mutex::new(VecDeque::new()),
            notify: Notify::new(),
            last_sent_ms: AtomicU64::new(0),
            behind_since: Mutex::new(None),
            overflowed: AtomicBool::new(false),
            closed: AtomicBool::new(false),
        }
    }

    #[test]
    fn lag_lasts_while_the_queue_keeps_overflowing_and_ends_when_the_viewer_keeps_up() {
        let v = viewer(2);
        let frame = Bytes::from_static(b"x");
        for i in 0..5 {
            v.offer(&frame, 1_000 + i * 600);
        }
        assert!(v.behind_for().is_some(), "the queue overflowed");
        // emptying the queue is not catching up while the sender kept overflowing it
        assert_eq!(v.drain().len(), 1);
        assert!(v.behind_for().is_some());
        // a drain after a stretch without overflow ends the lag
        v.offer(&frame, 10_000);
        assert_eq!(v.drain().len(), 1);
        assert!(v.behind_for().is_none());
    }
}

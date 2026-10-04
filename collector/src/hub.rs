//! State shared between the collector's threads: the reader and engine, the link, the uploader
//! and the overlay. Every field has one writer; readers take short locks and never wait on I/O.

use std::collections::{BTreeMap, VecDeque};
use std::sync::atomic::{AtomicBool, AtomicI64, AtomicU64, Ordering};
use std::sync::{Arc, Condvar, Mutex, MutexGuard};
use std::time::Duration;

use coach_live::LiveState;
use serde_json::Value;

use crate::gate::TrackGate;
use crate::journal::Journal;
use crate::stream::StreamControl;
use crate::util::Clock;

fn lock<T>(m: &Mutex<T>) -> MutexGuard<'_, T> {
    m.lock().unwrap_or_else(|e| e.into_inner())
}

/// Frames waiting to go out: a bounded queue (30 frames, 0.5 s at 60 Hz) that drops the oldest
/// frame on overflow, so the producer never blocks.
pub struct FrameQueue {
    queue: Mutex<VecDeque<Vec<u8>>>,
    ready: Condvar,
    capacity: usize,
    dropped: AtomicU64,
    sent: AtomicU64,
}

pub const FRAME_QUEUE_CAPACITY: usize = 30;

impl FrameQueue {
    pub fn new(capacity: usize) -> FrameQueue {
        FrameQueue {
            queue: Mutex::new(VecDeque::with_capacity(capacity)),
            ready: Condvar::new(),
            capacity,
            dropped: AtomicU64::new(0),
            sent: AtomicU64::new(0),
        }
    }

    /// Queues a frame; returns true when the oldest frame had to be dropped to make room.
    pub fn push(&self, frame: Vec<u8>) -> bool {
        let dropped = {
            let mut q = lock(&self.queue);
            let dropped = q.len() >= self.capacity;
            if dropped {
                q.pop_front();
            }
            q.push_back(frame);
            dropped
        };
        if dropped {
            self.dropped.fetch_add(1, Ordering::Relaxed);
        }
        self.ready.notify_one();
        dropped
    }

    pub fn pop(&self) -> Option<Vec<u8>> {
        lock(&self.queue).pop_front()
    }

    /// Waits up to `timeout` for a frame to arrive.
    pub fn wait(&self, timeout: Duration) {
        let q = lock(&self.queue);
        if q.is_empty() {
            let _ = self.ready.wait_timeout(q, timeout);
        }
    }

    pub fn clear(&self) {
        lock(&self.queue).clear();
    }

    pub fn len(&self) -> usize {
        lock(&self.queue).len()
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }

    pub fn dropped(&self) -> u64 {
        self.dropped.load(Ordering::Relaxed)
    }

    pub fn sent(&self) -> u64 {
        self.sent.load(Ordering::Relaxed)
    }

    pub fn note_sent(&self) {
        self.sent.fetch_add(1, Ordering::Relaxed);
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SimKind {
    NotRunning,
    Menus,
    InSession,
}

impl SimKind {
    pub fn as_str(self) -> &'static str {
        match self {
            SimKind::NotRunning => "not_running",
            SimKind::Menus => "menus",
            SimKind::InSession => "in_session",
        }
    }
}

/// The fields of `CollectorStatusBody` the reader owns.
#[derive(Debug, Clone, PartialEq)]
pub struct StatusModel {
    pub sim: SimKind,
    pub connected: bool,
    pub on_track: bool,
    pub in_garage: bool,
    pub on_pit_road: bool,
    pub streaming: bool,
    /// `LiveSessionRef` as JSON.
    pub session: Option<Value>,
    /// `DriverRef` as JSON.
    pub driver: Option<Value>,
}

impl Default for StatusModel {
    fn default() -> Self {
        StatusModel {
            sim: SimKind::NotRunning,
            connected: false,
            on_track: false,
            in_garage: false,
            on_pit_road: false,
            streaming: false,
            session: None,
            driver: None,
        }
    }
}

/// Upload queue figures for the status message and the tray.
#[derive(Debug, Default)]
pub struct UploadStats {
    pub uploads_pending: AtomicI64,
    pub bytes_pending: AtomicI64,
    /// Set while the uploader is waiting for off-track evidence with work queued.
    pub waiting_for_off_track: AtomicBool,
    /// Folder scans completed since start.
    pub scans_done: AtomicU64,
    /// Files given up on (unreadable, or refused by the server for good): not pending, not in
    /// `bytes_pending`, tried again only when the file changes. The journal holds the reasons.
    pub rejected: AtomicI64,
    pub rejected_bytes: AtomicI64,
    /// Files the server took from this process, and files it already held, since start.
    pub files_uploaded: AtomicU64,
    pub files_held: AtomicU64,
}

/// A live layout as the server last sent it.
#[derive(Debug, Clone, PartialEq)]
pub struct LayoutState {
    pub layout_id: String,
    pub version: i64,
    pub layout: Value,
}

/// What the link knows about the server, for the tray and tests.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct LinkInfo {
    pub connected: bool,
    pub viewers: i64,
    pub server_version: Option<String>,
    pub update_needed: bool,
    pub ingest_states: BTreeMap<String, String>,
    pub last_error: Option<String>,
}

pub struct Hub {
    pub clock: Arc<dyn Clock>,
    pub gate: Arc<TrackGate>,
    pub journal: Mutex<Journal>,
    pub stream: Mutex<StreamControl>,
    pub frames: FrameQueue,
    pub uploads: UploadStats,
    status: Mutex<StatusModel>,
    /// `status.streaming`, readable without cloning the status (the link asks every pass).
    streaming: AtomicBool,
    status_gen: AtomicU64,
    /// The generation of the status the link last sent (for a clean drain at exit).
    status_sent_gen: AtomicU64,
    layout: Mutex<Option<LayoutState>>,
    layout_gen: AtomicU64,
    live: Mutex<Option<LiveState>>,
    live_gen: AtomicU64,
    link: Mutex<LinkInfo>,
    /// Raised when a replay has been consumed (`--exit-at-end`).
    pub replay_done: AtomicBool,
    pub paused: AtomicBool,
    pub safe_glance: AtomicBool,
    pub shutdown: AtomicBool,
    /// Wakes the uploader thread (directory event, new work, off-track evidence, shutdown).
    pub uploader_wake: (Mutex<bool>, Condvar),
}

impl Hub {
    pub fn new(clock: Arc<dyn Clock>, journal: Journal, safe_glance: bool) -> Arc<Hub> {
        Arc::new(Hub {
            gate: TrackGate::new(clock.clone()),
            clock,
            journal: Mutex::new(journal),
            stream: Mutex::new(StreamControl::new()),
            frames: FrameQueue::new(FRAME_QUEUE_CAPACITY),
            uploads: UploadStats::default(),
            status: Mutex::new(StatusModel::default()),
            streaming: AtomicBool::new(false),
            status_gen: AtomicU64::new(0),
            status_sent_gen: AtomicU64::new(0),
            layout: Mutex::new(None),
            layout_gen: AtomicU64::new(0),
            live: Mutex::new(None),
            live_gen: AtomicU64::new(0),
            link: Mutex::new(LinkInfo::default()),
            replay_done: AtomicBool::new(false),
            paused: AtomicBool::new(false),
            safe_glance: AtomicBool::new(safe_glance),
            shutdown: AtomicBool::new(false),
            uploader_wake: (Mutex::new(false), Condvar::new()),
        })
    }

    pub fn journal(&self) -> MutexGuard<'_, Journal> {
        lock(&self.journal)
    }

    pub fn stream(&self) -> MutexGuard<'_, StreamControl> {
        lock(&self.stream)
    }

    pub fn is_shutdown(&self) -> bool {
        self.shutdown.load(Ordering::SeqCst)
    }

    pub fn request_shutdown(&self) {
        self.shutdown.store(true, Ordering::SeqCst);
        self.wake_uploader();
        self.frames.clear();
    }

    // ---- status ----

    pub fn status(&self) -> StatusModel {
        lock(&self.status).clone()
    }

    /// Replaces the status; bumps the generation only when something changed.
    pub fn set_status(&self, next: StatusModel) {
        let mut guard = lock(&self.status);
        if *guard != next {
            self.streaming.store(next.streaming, Ordering::SeqCst);
            *guard = next;
            self.status_gen.fetch_add(1, Ordering::SeqCst);
        }
    }

    /// Whether the status says the collector is streaming (no clone of the status).
    pub fn is_streaming(&self) -> bool {
        self.streaming.load(Ordering::SeqCst)
    }

    pub fn status_gen(&self) -> u64 {
        self.status_gen.load(Ordering::SeqCst)
    }

    pub fn note_status_sent(&self, generation: u64) {
        self.status_sent_gen.store(generation, Ordering::SeqCst);
    }

    /// True when the link has sent the latest status.
    pub fn status_flushed(&self) -> bool {
        self.status_sent_gen.load(Ordering::SeqCst) == self.status_gen()
    }

    // ---- layout ----

    pub fn layout(&self) -> Option<LayoutState> {
        lock(&self.layout).clone()
    }

    pub fn set_layout(&self, layout: LayoutState) {
        *lock(&self.layout) = Some(layout);
        self.layout_gen.fetch_add(1, Ordering::SeqCst);
    }

    pub fn layout_gen(&self) -> u64 {
        self.layout_gen.load(Ordering::SeqCst)
    }

    // ---- live state for the overlay ----

    /// Publishes the latest state without ever blocking the reader: if the overlay holds the lock
    /// the update is skipped (the next tick replaces it anyway).
    pub fn publish_live(&self, state: &LiveState) {
        if let Ok(mut guard) = self.live.try_lock() {
            *guard = Some(*state);
            self.live_gen.fetch_add(1, Ordering::Release);
        }
    }

    pub fn live(&self) -> Option<LiveState> {
        *lock(&self.live)
    }

    pub fn live_gen(&self) -> u64 {
        self.live_gen.load(Ordering::Acquire)
    }

    // ---- link ----

    pub fn link_info(&self) -> LinkInfo {
        lock(&self.link).clone()
    }

    pub fn update_link(&self, f: impl FnOnce(&mut LinkInfo)) {
        f(&mut lock(&self.link));
    }

    // ---- uploader wake ----

    pub fn wake_uploader(&self) {
        let (flag, cv) = &self.uploader_wake;
        *lock(flag) = true;
        cv.notify_all();
    }

    /// Sleeps until woken or `timeout` passes; returns true when woken.
    pub fn uploader_sleep(&self, timeout: Duration) -> bool {
        let (flag, cv) = &self.uploader_wake;
        let mut guard = lock(flag);
        if !*guard {
            guard = cv
                .wait_timeout(guard, timeout)
                .unwrap_or_else(|e| e.into_inner())
                .0;
        }
        std::mem::take(&mut *guard)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn queue_drops_the_oldest_frame_when_full() {
        let q = FrameQueue::new(3);
        for i in 0..5u8 {
            q.push(vec![i]);
        }
        assert_eq!(q.dropped(), 2);
        assert_eq!(q.pop(), Some(vec![2]));
        assert_eq!(q.len(), 2);
        q.clear();
        assert!(q.is_empty());
    }

    #[test]
    fn a_waiting_consumer_wakes_when_a_frame_is_queued_and_not_before() {
        let q = Arc::new(FrameQueue::new(4));
        let waiter = {
            let q = q.clone();
            std::thread::spawn(move || {
                let started = std::time::Instant::now();
                q.wait(Duration::from_secs(5));
                (started.elapsed(), q.len())
            })
        };
        std::thread::sleep(Duration::from_millis(50));
        q.push(vec![1]);
        let (waited, len) = waiter.join().unwrap();
        assert!(waited >= Duration::from_millis(40), "slept until the push");
        assert!(waited < Duration::from_secs(2), "woken by the push");
        assert_eq!(len, 1);
        // With a frame already queued, or with nothing and a short limit, it returns promptly.
        let started = std::time::Instant::now();
        q.wait(Duration::from_secs(5));
        assert!(started.elapsed() < Duration::from_millis(500));
        q.clear();
        let started = std::time::Instant::now();
        q.wait(Duration::from_millis(30));
        assert!(started.elapsed() < Duration::from_secs(2));
    }

    #[test]
    fn status_generation_moves_only_on_change() {
        let hub = Hub::new(
            Arc::new(crate::util::SystemClock::new()),
            Journal::in_memory(),
            true,
        );
        let g0 = hub.status_gen();
        hub.set_status(StatusModel::default());
        assert_eq!(hub.status_gen(), g0);
        hub.set_status(StatusModel {
            connected: true,
            ..StatusModel::default()
        });
        assert_eq!(hub.status_gen(), g0 + 1);
    }

    #[test]
    fn the_streaming_flag_follows_the_status() {
        let hub = Hub::new(
            Arc::new(crate::util::SystemClock::new()),
            Journal::in_memory(),
            true,
        );
        assert!(!hub.is_streaming());
        hub.set_status(StatusModel {
            connected: true,
            streaming: true,
            ..StatusModel::default()
        });
        assert!(hub.is_streaming());
        hub.set_status(StatusModel::default());
        assert!(!hub.is_streaming());
    }

    #[test]
    fn uploader_sleep_returns_early_when_woken() {
        let hub = Hub::new(
            Arc::new(crate::util::SystemClock::new()),
            Journal::in_memory(),
            true,
        );
        hub.wake_uploader();
        assert!(hub.uploader_sleep(Duration::from_secs(5)));
        assert!(!hub.uploader_sleep(Duration::from_millis(5)));
    }
}

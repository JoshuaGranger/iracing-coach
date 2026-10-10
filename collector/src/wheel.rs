//! Optional, per-launch wheel interruption recorder, ported from the recovered desktop source.
//! The SDK thread publishes only its existing steering observation. UI commands never wait on
//! DirectInput, storage, ZIP output or the elevated USB helper. Capture is local and never uploaded.

use std::collections::VecDeque;
use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Seek, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::mpsc::{self, Receiver, SyncSender};
use std::sync::{Arc, Mutex};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use serde_json::json;
use sha2::{Digest, Sha256};

use crate::util::{hex, iso_utc, unix_ms_now, uuid_v4};

pub const SAMPLE_HZ: u64 = 10;
pub const HISTORY_SAMPLES: usize = 600;
pub const MAX_INCIDENTS: usize = 8;
pub const ROOT_BUDGET: u64 = 192 * 1024 * 1024;
pub const SEGMENT_BUDGET: u64 = 1024 * 1024;
const SESSION_RESERVE: u64 = 40 * 1024 * 1024;
const SAMPLE_PERIOD: Duration = Duration::from_millis(100);
const LIMITATIONS: &str = "A flat steering value is not a disconnect. Raw axis units depend on the driver. SDK angles reflect the simulator, not confirmed physical motion. USB traces can miss events and cannot prove the FFB path or root cause. Incomplete post-event windows are labelled in incident JSON. Nothing is uploaded.";

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "PascalCase")]
pub struct WheelDevice {
    pub id: String,
    pub name: String,
    pub product_guid: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WheelReading {
    pub availability: String,
    pub axis: Option<i32>,
    pub native_error: Option<String>,
}

pub trait WheelInputSource: Send {
    fn enumerate(&mut self) -> Result<Vec<WheelDevice>, String>;
    fn select(&mut self, device_id: &str, axis: u8, window: isize) -> Result<(), String>;
    fn read(&mut self) -> WheelReading;
}

pub trait WheelUsbTrace: Send {
    fn status(&mut self) -> String;
    fn safe_to_export(&mut self) -> bool;
    fn start(&mut self, session_directory: &Path) -> Result<(), String>;
    fn heartbeat(&mut self);
    fn stop(&mut self) -> Result<(), String>;
    /// Explicit recovery only: stops interrupted same-user traces and never starts a capture.
    fn recover(&mut self) -> Result<(), String> {
        Err("Interrupted USB recovery is unavailable for this source".into())
    }
    fn can_recover(&mut self) -> bool {
        true
    }
}

enum UsbCommand {
    Start(PathBuf),
    Stop,
    Refresh,
    Recover,
    #[cfg(not(windows))]
    Heartbeat,
    Exit,
}
#[derive(Clone)]
struct UsbSnapshot {
    status: String,
    safe: bool,
    can_recover: bool,
}

/// UAC and bounded logman cleanup have their own worker. A cancelled/slow administrator prompt
/// cannot stall ordinary wheel samples, incident marks, or command/status snapshots.
struct UsbWorker {
    sender: SyncSender<UsbCommand>,
    snapshot: Arc<Mutex<UsbSnapshot>>,
    pending: Arc<AtomicUsize>,
}

impl UsbWorker {
    fn new(mut usb: Box<dyn WheelUsbTrace>) -> Self {
        let (sender, receiver) = mpsc::sync_channel(16);
        let snapshot = Arc::new(Mutex::new(UsbSnapshot {
            status: "Off".into(),
            safe: true,
            can_recover: true,
        }));
        let worker_snapshot = snapshot.clone();
        let pending = Arc::new(AtomicUsize::new(0));
        let worker_pending = pending.clone();
        thread::Builder::new().name("wheel-usb-control".into()).spawn(move || {
            while let Ok(command) = receiver.recv() {
                let operation = matches!(command, UsbCommand::Start(_) | UsbCommand::Stop | UsbCommand::Recover);
                let result = match command {
                    UsbCommand::Start(path) => usb.start(&path),
                    UsbCommand::Stop => usb.stop(),
                    UsbCommand::Recover => usb.recover(),
                    UsbCommand::Refresh => Ok(()),
                    #[cfg(not(windows))]
                    UsbCommand::Heartbeat => { usb.heartbeat(); Ok(()) },
                    UsbCommand::Exit => { let _ = usb.stop(); break; },
                };
                let mut status = usb.status();
                let safe = usb.safe_to_export();
                let can_recover = usb.can_recover();
                if let Err(error) = result { status = format!("USB operation failed: {error}. Cleanup must be confirmed before export."); }
                *worker_snapshot.lock().unwrap() = UsbSnapshot { status, safe, can_recover };
                if operation { worker_pending.fetch_sub(1, Ordering::Release); }
            }
        }).expect("cannot start optional USB control worker");
        Self {
            sender,
            snapshot,
            pending,
        }
    }

    fn operation(&self, command: UsbCommand) -> Result<(), String> {
        self.pending.fetch_add(1, Ordering::AcqRel);
        if self.sender.try_send(command).is_err() {
            self.pending.fetch_sub(1, Ordering::Release);
            return Err("USB control worker is busy or unavailable".into());
        }
        Ok(())
    }

    fn start(&self, directory: &Path) -> Result<(), String> {
        self.operation(UsbCommand::Start(directory.to_path_buf()))
    }
    fn recover(&self) -> Result<(), String> {
        self.operation(UsbCommand::Recover)
    }
    fn is_pending(&self) -> bool {
        self.pending.load(Ordering::Acquire) > 0
    }
    fn can_recover(&self) -> bool {
        !self.is_pending() && self.snapshot.lock().unwrap().can_recover
    }
    #[cfg(not(windows))]
    fn heartbeat(&self) {
        let _ = self.sender.try_send(UsbCommand::Heartbeat);
    }
    fn status(&self) -> String {
        if self.pending.load(Ordering::Acquire) > 0 {
            return "USB operation pending; confirm any administrator prompt.".into();
        }
        self.snapshot.lock().unwrap().status.clone()
    }
    fn refresh(&self) {
        if self.pending.load(Ordering::Acquire) == 0 {
            let _ = self.sender.try_send(UsbCommand::Refresh);
        }
    }
    fn safe_to_export(&self) -> bool {
        self.pending.load(Ordering::Acquire) == 0 && self.snapshot.lock().unwrap().safe
    }
    fn stop(&self) -> Result<(), String> {
        self.operation(UsbCommand::Stop)?;
        let start = Instant::now();
        while self.pending.load(Ordering::Acquire) > 0 && start.elapsed() < Duration::from_secs(3) {
            thread::sleep(Duration::from_millis(20));
        }
        if self.pending.load(Ordering::Acquire) > 0 {
            return Err("helper cleanup is still pending".into());
        }
        Ok(())
    }
    fn request_stop(&self) {
        let _ = self.operation(UsbCommand::Stop);
    }
}

impl Drop for UsbWorker {
    fn drop(&mut self) {
        let _ = self.sender.try_send(UsbCommand::Exit);
    }
}

pub type SourceFactory = Arc<dyn Fn() -> Box<dyn WheelInputSource> + Send + Sync>;

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "PascalCase")]
pub struct WheelDiagnosticStatus {
    pub running: bool,
    pub busy: bool,
    pub message: String,
    pub availability: String,
    pub raw_axis: Option<i32>,
    pub incidents: usize,
    pub usb: String,
    pub export_path: Option<PathBuf>,
    pub devices: Vec<WheelDevice>,
    pub has_capture: bool,
    pub can_export: bool,
    pub can_start_usb: bool,
    pub can_recover_usb: bool,
}

impl Default for WheelDiagnosticStatus {
    fn default() -> Self {
        Self {
            running: false,
            busy: false,
            message: "Disabled. Enable before racing.".into(),
            availability: "Off".into(),
            raw_axis: None,
            incidents: 0,
            usb: "Off".into(),
            export_path: None,
            devices: Vec::new(),
            has_capture: false,
            can_export: false,
            can_start_usb: false,
            can_recover_usb: true,
        }
    }
}

pub type WheelStatus = WheelDiagnosticStatus;
pub type WheelHandle = WheelDiagnostics;

#[derive(Debug, Clone)]
pub enum WheelCommand {
    Refresh,
    Enable {
        device: WheelDevice,
        axis: u8,
        window: isize,
    },
    Disable,
    MarkIncident,
    Export,
    StartUsb,
    RecoverUsb,
    Shutdown,
}

#[derive(Clone)]
struct SdkObservation {
    utc: u64,
    monotonic: Instant,
    steering: Option<f64>,
    connected: bool,
}

struct Shared {
    status: Mutex<WheelStatus>,
    sdk: Mutex<Option<SdkObservation>>,
    observing: AtomicBool,
    finished: AtomicBool,
}

struct HandleInner {
    commands: SyncSender<StampedCommand>,
    shared: Arc<Shared>,
}

impl Drop for HandleInner {
    fn drop(&mut self) {
        let _ = self
            .commands
            .try_send(StampedCommand::new(WheelCommand::Shutdown));
    }
}

#[derive(Clone)]
pub struct WheelDiagnostics {
    inner: Arc<HandleInner>,
}

struct StampedCommand {
    command: WheelCommand,
    at: Instant,
    utc: u64,
}
impl StampedCommand {
    fn new(command: WheelCommand) -> Self {
        Self {
            command,
            at: Instant::now(),
            utc: unix_ms_now(),
        }
    }
}

pub fn default_root() -> PathBuf {
    crate::config::Paths::default_paths()
        .state_dir
        .join("wheel-diagnostics")
}

pub fn default_exports() -> PathBuf {
    let paths = crate::config::Paths::default_paths();
    paths
        .iracing_root
        .parent()
        .unwrap_or(&paths.iracing_root)
        .join("iRacing Coach/exports/wheel-diagnostics")
}

impl WheelDiagnostics {
    pub fn new(
        root: PathBuf,
        exports: PathBuf,
        factory: SourceFactory,
        usb: Box<dyn WheelUsbTrace>,
    ) -> Self {
        let shared = Arc::new(Shared {
            status: Mutex::new(WheelStatus::default()),
            sdk: Mutex::new(None),
            observing: AtomicBool::new(false),
            finished: AtomicBool::new(false),
        });
        let (commands, receiver) = mpsc::sync_channel(16);
        let manager_shared = shared.clone();
        thread::Builder::new()
            .name("wheel-diagnostics".into())
            .spawn(move || {
                #[cfg(windows)]
                crate::win::enter_background_mode();
                let mut manager = Manager::new(root, exports, factory, usb, manager_shared);
                manager.run(receiver);
            })
            .expect("cannot start the optional diagnostics command worker");
        Self {
            inner: Arc::new(HandleInner { commands, shared }),
        }
    }

    #[cfg(windows)]
    pub fn native(state_dir: &Path) -> Self {
        Self::new(
            state_dir.join("wheel-diagnostics"),
            default_exports(),
            Arc::new(|| Box::new(crate::win::direct_input::DirectInputWheelSource::new())),
            Box::new(crate::win::wheel_usb::NativeWheelUsbTrace::with_root(
                state_dir.join("wheel-diagnostics"),
            )),
        )
    }

    pub fn send(&self, command: WheelCommand) -> bool {
        self.inner
            .commands
            .try_send(StampedCommand::new(command))
            .is_ok()
    }

    pub fn try_snapshot(&self) -> Option<WheelStatus> {
        self.inner.shared.status.try_lock().ok().map(|s| s.clone())
    }

    /// Called from the existing SDK feed only; disabled capture costs one relaxed atomic read.
    pub fn observe_sdk(&self, utc: u64, steering: Option<f64>, connected: bool) {
        if !self.inner.shared.observing.load(Ordering::Relaxed) {
            return;
        }
        if let Ok(mut sdk) = self.inner.shared.sdk.try_lock() {
            *sdk = Some(SdkObservation {
                utc,
                monotonic: Instant::now(),
                steering: steering.filter(|x| x.is_finite()),
                connected,
            });
        }
    }

    pub fn set_sdk_connected(&self, connected: bool) {
        if !self.inner.shared.observing.load(Ordering::Relaxed) {
            return;
        }
        if let Ok(mut sdk) = self.inner.shared.sdk.try_lock()
            && let Some(observation) = sdk.as_mut()
        {
            observation.connected = connected;
        }
    }

    /// App shutdown runs after the native UI loop ends; no UI thread ever joins a driver reader.
    pub fn shutdown_wait(&self, timeout: Duration) -> bool {
        let _ = self.send(WheelCommand::Shutdown);
        let start = Instant::now();
        while !self.inner.shared.finished.load(Ordering::Acquire) && start.elapsed() < timeout {
            thread::sleep(Duration::from_millis(20));
        }
        self.inner.shared.finished.load(Ordering::Acquire)
    }
}

enum ReaderEvent {
    Sample(WheelReading, Instant, u64, Option<SdkObservation>),
    Failed(String),
}

struct ReaderWorker {
    join: JoinHandle<()>,
    cancel: Arc<AtomicBool>,
    read_started: Arc<Mutex<Option<Instant>>>,
    events: Receiver<ReaderEvent>,
    timed_out: bool,
}

impl ReaderWorker {
    fn start(
        factory: SourceFactory,
        shared: Arc<Shared>,
        device: WheelDevice,
        axis: u8,
        window: isize,
    ) -> Self {
        let cancel = Arc::new(AtomicBool::new(false));
        let read_started = Arc::new(Mutex::new(None));
        let (sender, events) = mpsc::sync_channel(64);
        let stop = cancel.clone();
        let started = read_started.clone();
        let join = thread::Builder::new()
            .name("wheel-input".into())
            .spawn(move || {
                *started.lock().unwrap() = Some(Instant::now());
                let mut source = factory();
                if let Err(error) = source.select(&device.id, axis, window) {
                    let _ = sender.try_send(ReaderEvent::Failed(error));
                    return;
                }
                *started.lock().unwrap() = None;
                while !stop.load(Ordering::Acquire) {
                    let begin = Instant::now();
                    *started.lock().unwrap() = Some(begin);
                    let reading = source.read();
                    let now = Instant::now();
                    *started.lock().unwrap() = None;
                    let sdk = shared.sdk.lock().unwrap().clone();
                    match sender.try_send(ReaderEvent::Sample(reading, now, unix_ms_now(), sdk)) {
                        Ok(()) => {}
                        Err(mpsc::TrySendError::Disconnected(_)) => break,
                        Err(mpsc::TrySendError::Full(_)) => {
                            // Slow storage cannot silently manufacture a complete incident window.
                            let _ = sender.send(ReaderEvent::Failed(
                                "Recorder could not keep up with bounded input history".into(),
                            ));
                            break;
                        }
                    }
                    if now.duration_since(begin) > Duration::from_secs(1) {
                        let _ = sender
                            .try_send(ReaderEvent::Failed("Wheel read exceeded one second".into()));
                        break;
                    }
                    // No tight catch-up loop after a slow driver call; cancellation wakes promptly.
                    let until = Instant::now() + SAMPLE_PERIOD;
                    while !stop.load(Ordering::Acquire) && Instant::now() < until {
                        thread::sleep(Duration::from_millis(10));
                    }
                }
            })
            .expect("cannot start the wheel input reader");
        Self {
            join,
            cancel,
            read_started,
            events,
            timed_out: false,
        }
    }
}

struct Manager {
    root: PathBuf,
    exports: PathBuf,
    factory: SourceFactory,
    usb: UsbWorker,
    shared: Arc<Shared>,
    status: WheelStatus,
    reader: Option<ReaderWorker>,
    recorder: Option<Recorder>,
    enumeration: Option<JoinHandle<Result<Vec<WheelDevice>, String>>>,
    heartbeat: Instant,
    usb_requested: bool,
    recovering: bool,
}

impl Manager {
    fn new(
        root: PathBuf,
        exports: PathBuf,
        factory: SourceFactory,
        usb: Box<dyn WheelUsbTrace>,
        shared: Arc<Shared>,
    ) -> Self {
        Self {
            root,
            exports,
            factory,
            usb: UsbWorker::new(usb),
            shared,
            status: WheelStatus::default(),
            reader: None,
            recorder: None,
            enumeration: None,
            heartbeat: Instant::now(),
            usb_requested: false,
            recovering: false,
        }
    }

    fn publish(&self) {
        // Snapshot locks contain no native calls, filesystem work, or waits.
        let mut snapshot = self.status.clone();
        snapshot.busy |= self.recovering && !self.usb.can_recover();
        snapshot.can_start_usb = snapshot.running && !snapshot.busy && self.usb.safe_to_export();
        snapshot.can_recover_usb = !snapshot.running
            && !snapshot.busy
            && self.reader.is_none()
            && self.enumeration.is_none()
            && self.usb.can_recover();
        if let Ok(mut status) = self.shared.status.lock() {
            *status = snapshot;
        }
    }

    fn fail(&mut self, message: String) {
        self.status.message = message;
        self.publish();
    }

    fn run(&mut self, commands: Receiver<StampedCommand>) {
        loop {
            let delay = if self.reader.is_some() || self.enumeration.is_some() {
                Duration::from_millis(40)
            } else {
                Duration::from_secs(1)
            };
            match commands.recv_timeout(delay) {
                Ok(StampedCommand {
                    command: WheelCommand::Shutdown,
                    ..
                })
                | Err(mpsc::RecvTimeoutError::Disconnected) => break,
                Ok(command) => self.command(command),
                Err(mpsc::RecvTimeoutError::Timeout) => {}
            }
            self.poll_reader();
            self.poll_enumeration();
            if self.heartbeat.elapsed() >= Duration::from_secs(1) {
                self.heartbeat = Instant::now();
                #[cfg(windows)]
                if self.usb_requested
                    && self.status.running
                    && let Some(recorder) = &self.recorder
                    && let Err(error) =
                        crate::win::wheel_usb::heartbeat_directory(&recorder.directory)
                {
                    self.status.message = format!(
                        "USB heartbeat failed: {error}; helper watchdog will attempt cleanup."
                    );
                }
                #[cfg(not(windows))]
                if self.usb_requested && self.status.running {
                    self.usb.heartbeat();
                }
                if self.usb_requested {
                    self.usb.refresh();
                }
                self.status.usb = self.usb.status();
                if let Some(recorder) = &mut self.recorder
                    && let Err(error) = recorder.flush().and_then(|_| ensure_budget(&self.root))
                {
                    self.stop_with_failure(format!(
                        "Capture stopped (storage): {error}; no cause inferred."
                    ));
                }
                self.status.can_export =
                    self.recorder.is_some() && self.reader.is_none() && self.usb.safe_to_export();
                self.publish();
            }
        }
        self.stop_reader(Duration::from_secs(3));
        if let Some(recorder) = &mut self.recorder {
            let _ = recorder.flush();
            recorder.close_segment();
        }
        self.shared.observing.store(false, Ordering::Release);
        self.shared.finished.store(true, Ordering::Release);
    }

    fn command(&mut self, command: StampedCommand) {
        self.poll_reader();
        match command.command {
            WheelCommand::Refresh => {
                if self.enumeration.is_none() {
                    self.status.busy = true;
                    self.publish();
                    let factory = self.factory.clone();
                    self.enumeration = Some(thread::spawn(move || factory().enumerate()));
                }
            }
            WheelCommand::Enable {
                device,
                axis,
                window,
            } => self.enable(device, axis, window),
            WheelCommand::Disable => {
                self.status.busy = true;
                self.publish();
                self.stop_reader(Duration::from_secs(3));
                if self.reader.is_none() {
                    self.status.message = "Stopped. History is ready to export.".into();
                }
                self.status.busy = false;
                self.publish();
            }
            WheelCommand::MarkIncident => {
                if self.status.running
                    && let Some(recorder) = &mut self.recorder
                {
                    let marked_ms = command
                        .at
                        .saturating_duration_since(recorder.start)
                        .as_secs_f64()
                        * 1000.0;
                    match recorder.mark("Driver marked an incident", marked_ms, command.utc) {
                        Ok(()) => self.status.incidents = recorder.incidents.len(),
                        Err(error) => {
                            self.stop_with_failure(format!("Incident could not be saved: {error}"))
                        }
                    }
                    self.publish();
                }
            }
            WheelCommand::Export => self.export(),
            WheelCommand::StartUsb => {
                if self.status.running
                    && let Some(recorder) = &self.recorder
                {
                    if !self.usb.safe_to_export() {
                        self.fail("An earlier USB operation or cleanup is still active.".into());
                        return;
                    }
                    self.status.busy = true;
                    self.publish();
                    #[cfg(windows)]
                    if let Err(error) =
                        crate::win::wheel_usb::prepare_directory(&recorder.directory)
                    {
                        self.status.busy = false;
                        self.fail(format!("Optional USB trace unavailable: {error}"));
                        return;
                    }
                    self.usb_requested = true;
                    if let Err(error) = self.usb.start(&recorder.directory) {
                        self.status.message = format!("Optional USB trace unavailable: {error}");
                    }
                    self.status.usb = self.usb.status();
                    self.status.busy = false;
                    self.publish();
                }
            }
            WheelCommand::RecoverUsb => {
                if self.status.running
                    || self.status.busy
                    || self.reader.is_some()
                    || self.enumeration.is_some()
                    || !self.usb.can_recover()
                {
                    self.fail(
                        "Stop capture and wait for any USB operation before recovery.".into(),
                    );
                    return;
                }
                // No Recorder::start, reservation, pruning, heartbeat, or new trace here.
                self.usb_requested = true;
                self.recovering = true;
                if let Err(error) = self.usb.recover() {
                    self.status.message = format!("Interrupted USB recovery unavailable: {error}");
                } else {
                    self.status.message = "Recovery requested; confirm the administrator prompt. Recovery does not start a trace.".into();
                }
                self.status.usb = self.usb.status();
                self.publish();
            }
            WheelCommand::Shutdown => {}
        }
    }

    fn enable(&mut self, device: WheelDevice, axis: u8, window: isize) {
        self.poll_reader();
        if self.reader.is_some() || self.enumeration.is_some() || !self.usb.safe_to_export() {
            self.fail("Previous reader or USB cleanup is still active. Capture cannot restart until it exits.".into());
            return;
        }
        if axis > 5 || window == 0 {
            self.fail(
                "Select a controller and its steering axis in the native diagnostics window."
                    .into(),
            );
            return;
        }
        self.status.busy = true;
        self.publish();
        match Recorder::start(&self.root, &device, axis, SEGMENT_BUDGET) {
            Ok(recorder) => {
                self.recorder = Some(recorder);
                self.usb_requested = false;
                self.recovering = false;
                *self.shared.sdk.lock().unwrap() = None;
                self.shared.observing.store(true, Ordering::Release);
                self.reader = Some(ReaderWorker::start(
                    self.factory.clone(),
                    self.shared.clone(),
                    device,
                    axis,
                    window,
                ));
                self.status.running = true;
                self.status.has_capture = true;
                self.status.availability = "Starting".into();
                self.status.raw_axis = None;
                self.status.incidents = 0;
                self.status.export_path = None;
                self.status.can_export = false;
                self.status.message =
                    "Recording locally. Mark an incident as soon as practical.".into();
            }
            Err(error) => {
                self.status.message =
                    format!("Capture could not start: {error}. Check the local diagnostics folder.")
            }
        }
        self.status.busy = false;
        self.publish();
    }

    fn poll_enumeration(&mut self) {
        if self
            .enumeration
            .as_ref()
            .is_some_and(JoinHandle::is_finished)
        {
            match self.enumeration.take().unwrap().join() {
                Ok(Ok(devices)) => {
                    self.status.devices = devices.into_iter().take(32).collect();
                    if !self.status.running {
                        self.status.message = if self.status.devices.is_empty() {
                            "No attached controllers. Connect the wheel and refresh.".into()
                        } else {
                            "Choose the wheel and its steering axis, then enable before racing."
                                .into()
                        };
                    }
                }
                Ok(Err(error)) => self.status.message = format!("Controllers unavailable: {error}"),
                Err(_) => self.status.message = "Controller refresh failed.".into(),
            }
            self.status.busy = false;
            self.publish();
        }
    }

    fn stop_with_failure(&mut self, message: String) {
        if let Some(reader) = &self.reader {
            reader.cancel.store(true, Ordering::Release);
        }
        if let Some(recorder) = &mut self.recorder {
            recorder.failure = Some(message.clone());
        }
        self.status.running = false;
        self.shared.observing.store(false, Ordering::Release);
        self.status.message = message;
        self.publish();
    }

    fn poll_reader(&mut self) {
        let mut events = Vec::new();
        if let Some(reader) = &mut self.reader {
            while let Ok(event) = reader.events.try_recv() {
                events.push(event);
            }
            let hung = reader
                .read_started
                .lock()
                .unwrap()
                .is_some_and(|start| start.elapsed() > Duration::from_secs(1));
            if hung && !reader.timed_out {
                reader.timed_out = true;
                reader.cancel.store(true, Ordering::Release);
                self.stop_with_failure("Wheel read exceeded one second. Capture stopped; export/restart wait for the reader to exit. No cause inferred.".into());
            }
        }
        for event in events {
            match event {
                ReaderEvent::Sample(reading, now, utc, sdk) => {
                    if let Some(recorder) = &mut self.recorder {
                        match recorder.sample(reading.clone(), now, utc, sdk) {
                            Ok(()) => {
                                self.status.availability = reading.availability;
                                self.status.raw_axis = reading.axis;
                                self.status.incidents = recorder.incidents.len();
                            }
                            Err(error) => self.stop_with_failure(format!(
                                "Capture stopped (storage): {error}; no cause inferred."
                            )),
                        }
                    }
                }
                ReaderEvent::Failed(error) => self.stop_with_failure(format!(
                    "Capture stopped: {error}. Driver/access may be unavailable; no cause inferred."
                )),
            }
        }
        if self
            .reader
            .as_ref()
            .is_some_and(|reader| reader.join.is_finished())
        {
            let reader = self.reader.take().unwrap();
            if reader.join.join().is_err() {
                self.stop_with_failure("Wheel reader failed; no cause inferred.".into());
            }
            if let Some(recorder) = &mut self.recorder {
                if let Err(error) = recorder.flush() {
                    recorder.failure = Some(format!("Incident flush failed: {error}"));
                    self.status.message = format!("Incident flush failed: {error}");
                }
                recorder.close_segment();
            }
            self.persist_usb_stop();
            self.usb.request_stop();
            self.status.usb = self.usb.status();
            self.status.running = false;
            self.shared.observing.store(false, Ordering::Release);
            self.status.can_export = self.recorder.is_some() && self.usb.safe_to_export();
        }
        self.publish();
    }

    fn stop_reader(&mut self, timeout: Duration) {
        self.persist_usb_stop();
        if let Some(reader) = &self.reader {
            reader.cancel.store(true, Ordering::Release);
        }
        self.status.running = false;
        self.shared.observing.store(false, Ordering::Release);
        let start = Instant::now();
        while self.reader.is_some() && start.elapsed() < timeout {
            self.poll_reader();
            if self.reader.is_some() {
                thread::sleep(Duration::from_millis(20));
            }
        }
        if self.reader.is_some() {
            self.status.can_export = false;
            self.status.message = "Reader did not stop within 3 seconds. Export and another capture wait until it exits.".into();
        }
        if let Err(error) = self.usb.stop() {
            self.status.message = format!("USB cleanup request failed: {error}");
        }
        self.status.usb = self.usb.status();
        self.publish();
    }

    fn persist_usb_stop(&mut self) {
        #[cfg(windows)]
        if self.usb_requested
            && let Some(recorder) = &self.recorder
            && let Err(error) = crate::win::wheel_usb::request_stop_directory(&recorder.directory)
        {
            self.status.message = format!(
                "USB stop intent could not be written: {error}; helper watchdog will attempt cleanup."
            );
        }
    }

    fn export(&mut self) {
        self.status.busy = true;
        self.publish();
        self.stop_reader(Duration::from_secs(3));
        if self.reader.is_some() || !self.usb.safe_to_export() {
            self.status.message =
                "Export unavailable until the reader exits and USB cleanup is confirmed.".into();
        } else if let Some(recorder) = self.recorder.as_mut() {
            let result = recorder.export(&self.exports, &self.status);
            match result {
                Ok(path) => {
                    self.status.export_path = Some(path);
                    self.status.message =
                        "Export saved locally. Review the bundle before sharing.".into();
                }
                Err(error) => {
                    self.status.message =
                        format!("Export failed: {error}. Check free space and local folder access.")
                }
            }
        } else {
            self.status.message = "Enable the recorder before exporting a capture.".into();
        }
        self.status.can_export =
            self.recorder.is_some() && self.reader.is_none() && self.usb.safe_to_export();
        self.status.busy = false;
        self.publish();
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "PascalCase")]
pub struct WheelDiagnosticRecord {
    pub utc: String,
    pub monotonic_ticks: u64,
    pub elapsed_ms: f64,
    pub kind: String,
    pub availability: String,
    pub raw_axis: Option<i32>,
    pub native_error: Option<String>,
    pub sdk_steering_radians: Option<f64>,
    pub sdk_utc: Option<String>,
    pub sdk_age_ms: Option<f64>,
    pub sdk_connected: bool,
    pub incident_id: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "PascalCase")]
pub struct WheelIncident {
    pub id: String,
    pub reason: String,
    pub marked_ms: f64,
    pub post_window_complete: bool,
    pub records: Vec<WheelDiagnosticRecord>,
}

struct Recorder {
    root: PathBuf,
    directory: PathBuf,
    start: Instant,
    history: VecDeque<WheelDiagnosticRecord>,
    incidents: Vec<WheelIncident>,
    segment: Option<File>,
    segment_index: usize,
    segment_bytes: u64,
    segment_budget: u64,
    previous: String,
    last_auto_mark: Option<f64>,
    failure: Option<String>,
}

impl Recorder {
    fn start(root: &Path, device: &WheelDevice, axis: u8, segment_budget: u64) -> io::Result<Self> {
        if !(1024..=SEGMENT_BUDGET).contains(&segment_budget) {
            return Err(io::Error::other("invalid segment budget"));
        }
        make_safe_directory(root)?;
        prune(root, None, 2)?;
        if size_of_root(root)? + SESSION_RESERVE > ROOT_BUDGET {
            // Completed recovery can make formerly protected sessions removable. Keep the
            // usual three captures where space permits, then reclaim only confirmed-safe files.
            prune(root, None, 0)?;
        }
        if size_of_root(root)? + SESSION_RESERVE > ROOT_BUDGET {
            return Err(io::Error::other(
                "Diagnostics storage is full; recover interrupted USB sessions before another capture",
            ));
        }
        let session = uuid_v4().replace('-', "");
        let directory = root.join(&session);
        fs::create_dir(&directory)?;
        let start = Instant::now();
        let key = hex(&Sha256::digest(
            format!("{}{session}", device.id).as_bytes(),
        ));
        write_json(
            &directory,
            "metadata.json",
            &json!({
                "SchemaVersion": 1, "AppVersion": env!("CARGO_PKG_VERSION"), "OS": std::env::consts::OS,
                "Device": clean(&device.name, 120), "DirectInputProductGuid": device.product_guid,
                "DeviceKey": &key[..16], "Axis": axis, "SampleHz": SAMPLE_HZ, "StopwatchFrequency": 1_000_000_000_u64,
                "StartUtc": iso_utc(unix_ms_now()), "StartTicks": 0, "HistorySeconds": 60,
                "IncidentBeforeSeconds": 10, "IncidentAfterSeconds": 20, "MaxIncidents": MAX_INCIDENTS,
                "Privacy": "Selected wheel axis and availability; existing public SDK steering only. Optional USB trace contains system-wide USB state/device metadata without transfer payload keywords. No uploads."
            }),
        )?;
        Ok(Self {
            root: root.to_path_buf(),
            directory,
            start,
            history: VecDeque::new(),
            incidents: Vec::new(),
            segment: None,
            segment_index: 0,
            segment_bytes: 0,
            segment_budget,
            previous: "Starting".into(),
            last_auto_mark: None,
            failure: None,
        })
    }

    fn sample(
        &mut self,
        reading: WheelReading,
        now: Instant,
        utc: u64,
        sdk: Option<SdkObservation>,
    ) -> io::Result<()> {
        let elapsed = now.saturating_duration_since(self.start);
        let record = WheelDiagnosticRecord {
            utc: iso_utc(utc),
            monotonic_ticks: elapsed.as_nanos().min(u64::MAX as u128) as u64,
            elapsed_ms: elapsed.as_secs_f64() * 1000.0,
            kind: "sample".into(),
            availability: clean(&reading.availability, 80),
            raw_axis: reading.axis,
            native_error: reading.native_error.as_deref().map(|x| clean(x, 240)),
            sdk_steering_radians: sdk.as_ref().and_then(|x| x.steering),
            sdk_utc: sdk.as_ref().map(|x| iso_utc(x.utc)),
            sdk_age_ms: sdk
                .as_ref()
                .map(|x| now.saturating_duration_since(x.monotonic).as_secs_f64() * 1000.0),
            sdk_connected: sdk.as_ref().is_some_and(|x| x.connected),
            incident_id: None,
        };
        self.history.push_back(record.clone());
        while self.history.len() > HISTORY_SAMPLES {
            self.history.pop_front();
        }
        self.write_record(&record)?;
        // Incident post windows are bounded even when driver timing or a hung call is irregular.
        for incident in &mut self.incidents {
            if !incident.post_window_complete && record.elapsed_ms >= incident.marked_ms {
                if record.elapsed_ms <= incident.marked_ms + 20_000.0 {
                    if incident.records.len() < 302
                        && incident
                            .records
                            .last()
                            .is_none_or(|x| x.monotonic_ticks != record.monotonic_ticks)
                    {
                        incident.records.push(record.clone());
                    }
                } else {
                    incident.post_window_complete = true;
                }
            }
        }
        if self.previous == "Available"
            && reading.availability != "Available"
            && self
                .last_auto_mark
                .is_none_or(|mark| record.elapsed_ms - mark > 30_000.0)
        {
            self.mark("Input availability changed", record.elapsed_ms, utc)?;
            self.last_auto_mark = Some(record.elapsed_ms);
        }
        self.previous = reading.availability;
        Ok(())
    }

    fn mark(&mut self, reason: &str, marked_ms: f64, utc: u64) -> io::Result<()> {
        let Some(last) = self
            .history
            .iter()
            .rev()
            .find(|x| x.elapsed_ms <= marked_ms)
        else {
            return Ok(());
        };
        let mut marker = last.clone();
        marker.utc = iso_utc(utc);
        marker.elapsed_ms = marked_ms;
        marker.monotonic_ticks = (marked_ms * 1_000_000.0) as u64;
        marker.kind = "incident".into();
        marker.raw_axis = None;
        marker.sdk_age_ms = marker
            .sdk_age_ms
            .map(|age| age + (marked_ms - last.elapsed_ms).max(0.0));
        let incident = WheelIncident {
            id: uuid_v4().replace('-', ""),
            reason: reason.into(),
            marked_ms,
            post_window_complete: false,
            records: self
                .history
                .iter()
                .filter(|x| {
                    x.elapsed_ms >= marked_ms - 10_000.0 && x.elapsed_ms <= marked_ms + 20_000.0
                })
                .take(302)
                .cloned()
                .collect(),
        };
        if self.incidents.len() >= MAX_INCIDENTS {
            let oldest = self.incidents.remove(0);
            remove_owned_file(&self.directory.join(format!("incident-{}.json", oldest.id)))?;
        }
        marker.incident_id = Some(incident.id.clone());
        self.write_record(&marker)?;
        write_json(
            &self.directory,
            &format!("incident-{}.json", incident.id),
            &incident,
        )?;
        self.incidents.push(incident);
        Ok(())
    }

    fn write_record(&mut self, record: &WheelDiagnosticRecord) -> io::Result<()> {
        let mut bytes = serde_json::to_vec(record)?;
        bytes.push(b'\n');
        if bytes.len() as u64 > self.segment_budget {
            return Err(io::Error::other("sample exceeds its segment budget"));
        }
        if self.segment.is_none() || self.segment_bytes + bytes.len() as u64 > self.segment_budget {
            self.close_segment();
            let path = self
                .directory
                .join(format!("samples-{:06}.ndjson", self.segment_index));
            self.segment_index += 1;
            self.segment = Some(OpenOptions::new().create_new(true).write(true).open(path)?);
            self.segment_bytes = 0;
            let mut segments = Vec::new();
            for entry in fs::read_dir(&self.directory)? {
                let path = entry?.path();
                if path
                    .file_name()
                    .and_then(|x| x.to_str())
                    .is_some_and(is_sample_name)
                {
                    segments.push(path);
                }
            }
            segments.sort();
            for old in segments.iter().take(segments.len().saturating_sub(4)) {
                remove_owned_file(old)?;
            }
        }
        self.segment.as_mut().unwrap().write_all(&bytes)?;
        self.segment_bytes += bytes.len() as u64;
        Ok(())
    }

    fn flush(&mut self) -> io::Result<()> {
        if let Some(segment) = &mut self.segment {
            segment.flush()?;
        }
        for incident in &self.incidents {
            write_json(
                &self.directory,
                &format!("incident-{}.json", incident.id),
                incident,
            )?;
        }
        Ok(())
    }

    fn close_segment(&mut self) {
        self.segment.take();
    }

    fn export(&mut self, exports: &Path, status: &WheelStatus) -> io::Result<PathBuf> {
        self.flush()?;
        self.close_segment();
        make_safe_directory(exports)?;
        let mut status = status.clone();
        status.export_path = None;
        // Enumerated controllers are UI state, not capture evidence. Never export other device
        // identifiers (the selected instance id is represented only by its salted DeviceKey).
        status.devices.clear();
        write_json(
            &self.directory,
            "manifest.json",
            &json!({
                "SchemaVersion": 1, "ExportUtc": iso_utc(unix_ms_now()), "CaptureFailure": self.failure,
                "Status": status, "RootBudget": ROOT_BUDGET, "Limitations": LIMITATIONS,
            }),
        )?;
        prune(&self.root, Some(&self.directory), 2)?;
        ensure_budget(&self.root)?;
        let mut files = Vec::new();
        let mut size = 0_u64;
        for entry in fs::read_dir(&self.directory)? {
            let path = entry?.path();
            let name = path.file_name().and_then(|x| x.to_str()).unwrap_or("");
            if is_export_name(name) {
                let metadata = safe_file_metadata(&path)?;
                size = size
                    .checked_add(metadata.len())
                    .ok_or_else(|| io::Error::other("bundle size overflow"))?;
                files.push(path);
            }
        }
        if size > SESSION_RESERVE {
            return Err(io::Error::other("diagnostic bundle exceeds 40 MiB"));
        }
        if files.len() > 32 {
            return Err(io::Error::other("too many diagnostic bundle entries"));
        }
        files.sort();
        let destination = exports.join(format!(
            "wheel-diagnostics-{}-{}.zip",
            unix_ms_now(),
            uuid_v4().replace('-', "")
        ));
        zip_stored(&destination, &files)?;
        Ok(destination)
    }
}

fn clean(input: &str, max: usize) -> String {
    input
        .chars()
        .filter(|x| !x.is_control())
        .take(max)
        .collect()
}

fn redirected(metadata: &fs::Metadata) -> bool {
    if metadata.file_type().is_symlink() {
        return true;
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        metadata.file_attributes() & 0x400 != 0
    }
    #[cfg(not(windows))]
    {
        false
    }
}

pub fn safe_directory(path: &Path) -> io::Result<()> {
    for ancestor in path.ancestors() {
        if ancestor.as_os_str().is_empty() {
            continue;
        }
        let metadata = fs::symlink_metadata(ancestor)?;
        if redirected(&metadata) || !metadata.is_dir() {
            return Err(io::Error::other("redirected diagnostics directory"));
        }
    }
    Ok(())
}

fn make_safe_directory(path: &Path) -> io::Result<()> {
    for ancestor in path.ancestors() {
        if ancestor.as_os_str().is_empty() {
            continue;
        }
        match fs::symlink_metadata(ancestor) {
            Ok(metadata) if redirected(&metadata) || !metadata.is_dir() => {
                return Err(io::Error::other("redirected diagnostics directory"));
            }
            Ok(_) => {}
            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
            Err(error) => return Err(error),
        }
    }
    fs::create_dir_all(path)?;
    safe_directory(path)
}

fn safe_file_metadata(path: &Path) -> io::Result<fs::Metadata> {
    let metadata = fs::symlink_metadata(path)?;
    if redirected(&metadata) || !metadata.is_file() {
        return Err(io::Error::other("redirected diagnostic file"));
    }
    Ok(metadata)
}

fn remove_owned_file(path: &Path) -> io::Result<()> {
    match safe_file_metadata(path) {
        Ok(_) => fs::remove_file(path),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error),
    }
}

fn write_json<T: Serialize>(directory: &Path, name: &str, value: &T) -> io::Result<()> {
    safe_directory(directory)?;
    let destination = directory.join(name);
    if destination.exists() {
        safe_file_metadata(&destination)?;
    }
    let temporary = directory.join(format!("{name}.tmp"));
    if temporary.exists() {
        remove_owned_file(&temporary)?;
    }
    let mut output = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&temporary)?;
    let result = (|| {
        output.write_all(&serde_json::to_vec(value)?)?;
        output.sync_all()?;
        drop(output);
        fs::rename(&temporary, &destination)
    })();
    if result.is_err() {
        let _ = remove_owned_file(&temporary);
    }
    result
}

fn is_guid(name: &str) -> bool {
    name.len() == 32 && name.bytes().all(|x| x.is_ascii_hexdigit())
}
fn is_sample_name(name: &str) -> bool {
    name.strip_prefix("samples-")
        .and_then(|x| x.strip_suffix(".ndjson"))
        .is_some_and(|x| x.len() >= 6 && x.bytes().all(|b| b.is_ascii_digit()))
}
fn is_incident_name(name: &str) -> bool {
    name.strip_prefix("incident-")
        .and_then(|x| x.strip_suffix(".json"))
        .is_some_and(is_guid)
}
fn is_export_name(name: &str) -> bool {
    matches!(name, "metadata.json" | "manifest.json" | "usb-status.json")
        || is_sample_name(name)
        || is_incident_name(name)
        || name
            .strip_prefix("usb")
            .and_then(|x| x.strip_suffix(".etl"))
            .is_some_and(|x| x.chars().all(|c| c.is_ascii_alphanumeric() || c == '-'))
}

fn session_directories(root: &Path) -> io::Result<Vec<PathBuf>> {
    safe_directory(root)?;
    let mut result = Vec::new();
    for entry in fs::read_dir(root)? {
        let path = entry?.path();
        if path
            .file_name()
            .and_then(|x| x.to_str())
            .is_some_and(is_guid)
        {
            let metadata = fs::symlink_metadata(&path)?;
            if redirected(&metadata) || !metadata.is_dir() {
                return Err(io::Error::other("redirected diagnostic session"));
            }
            result.push(path);
        }
    }
    Ok(result)
}

fn size_of_root(root: &Path) -> io::Result<u64> {
    let mut size = 0_u64;
    for directory in session_directories(root)? {
        for entry in fs::read_dir(directory)? {
            let metadata = safe_file_metadata(&entry?.path())?;
            size = size
                .checked_add(metadata.len())
                .ok_or_else(|| io::Error::other("diagnostic storage overflow"))?;
        }
    }
    Ok(size)
}

fn ensure_budget(root: &Path) -> io::Result<()> {
    if size_of_root(root)? > ROOT_BUDGET {
        return Err(io::Error::other("diagnostic storage budget reached"));
    }
    Ok(())
}

fn prune(root: &Path, current: Option<&Path>, keep_others: usize) -> io::Result<()> {
    let mut directories = session_directories(root)?;
    directories.retain(|directory| Some(directory.as_path()) != current);
    directories.sort_by_key(|directory| {
        std::cmp::Reverse(fs::metadata(directory).and_then(|m| m.modified()).ok())
    });
    for directory in directories.into_iter().skip(keep_others) {
        let mut files = Vec::new();
        let mut removable = true;
        for entry in fs::read_dir(&directory)? {
            let path = entry?.path();
            let name = path.file_name().and_then(|x| x.to_str()).unwrap_or("");
            if safe_file_metadata(&path).is_err()
                || (!is_export_name(name)
                    && !matches!(name, "usb-owner.json" | "usb-stop" | "usb-heartbeat"))
            {
                removable = false;
                break;
            }
            files.push(path);
        }
        if directory.join("usb-owner.json").exists() {
            let state = fs::read(directory.join("usb-status.json"))
                .ok()
                .and_then(|bytes| serde_json::from_slice::<serde_json::Value>(&bytes).ok());
            if !state
                .as_ref()
                .and_then(|x| x.get("State").or_else(|| x.get("state")))
                .and_then(|x| x.as_str())
                .is_some_and(|x| matches!(x, "Stopped" | "Unavailable"))
            {
                removable = false;
            }
        }
        if removable {
            for file in files {
                remove_owned_file(&file)?;
            }
            fs::remove_dir(&directory)?;
        }
    }
    Ok(())
}

/// Narrow standard ZIP (stored entries), bounded below ZIP32 limits. Temporary output is removed
/// on failure and synced before the durable export appears. No archive ever contains arbitrary files.
fn zip_stored(destination: &Path, files: &[PathBuf]) -> io::Result<()> {
    let temporary = destination.with_extension("zip.tmp");
    let result = (|| {
        let mut output = OpenOptions::new()
            .create_new(true)
            .write(true)
            .open(&temporary)?;
        let mut central = Vec::new();
        for path in files {
            let metadata = safe_file_metadata(path)?;
            if metadata.len() > SESSION_RESERVE {
                return Err(io::Error::other("file exceeds bundle budget"));
            }
            let mut input = File::open(path)?;
            let mut data = Vec::new();
            Read::by_ref(&mut input)
                .take(SESSION_RESERVE + 1)
                .read_to_end(&mut data)?;
            if data.len() as u64 != metadata.len() {
                return Err(io::Error::other("diagnostic file changed during export"));
            }
            let name = path
                .file_name()
                .and_then(|x| x.to_str())
                .ok_or_else(|| io::Error::other("invalid diagnostic filename"))?
                .as_bytes();
            let offset = output.stream_position()? as u32;
            let crc = crc32(&data);
            let size = data.len() as u32;
            output.write_all(&0x04034b50_u32.to_le_bytes())?;
            // Earliest valid DOS ZIP date: 1980-01-01. Zero encodes an invalid month/day.
            for value in [20_u16, 0, 0, 0, 0x0021] {
                output.write_all(&value.to_le_bytes())?;
            }
            for value in [crc, size, size] {
                output.write_all(&value.to_le_bytes())?;
            }
            output.write_all(&(name.len() as u16).to_le_bytes())?;
            output.write_all(&0_u16.to_le_bytes())?;
            output.write_all(name)?;
            output.write_all(&data)?;
            central.extend_from_slice(&0x02014b50_u32.to_le_bytes());
            for value in [20_u16, 20, 0, 0, 0, 0x0021] {
                central.extend_from_slice(&value.to_le_bytes());
            }
            for value in [crc, size, size] {
                central.extend_from_slice(&value.to_le_bytes());
            }
            for value in [name.len() as u16, 0, 0, 0, 0] {
                central.extend_from_slice(&value.to_le_bytes());
            }
            central.extend_from_slice(&0_u32.to_le_bytes());
            central.extend_from_slice(&offset.to_le_bytes());
            central.extend_from_slice(name);
        }
        let central_offset = output.stream_position()? as u32;
        output.write_all(&central)?;
        output.write_all(&0x06054b50_u32.to_le_bytes())?;
        for value in [0_u16, 0, files.len() as u16, files.len() as u16] {
            output.write_all(&value.to_le_bytes())?;
        }
        output.write_all(&(central.len() as u32).to_le_bytes())?;
        output.write_all(&central_offset.to_le_bytes())?;
        output.write_all(&0_u16.to_le_bytes())?;
        output.sync_all()?;
        drop(output);
        fs::rename(&temporary, destination)
    })();
    if result.is_err() {
        let _ = remove_owned_file(&temporary);
    }
    result
}

fn crc32(bytes: &[u8]) -> u32 {
    let mut crc = !0_u32;
    for byte in bytes {
        crc ^= *byte as u32;
        for _ in 0..8 {
            crc = (crc >> 1) ^ (0xedb88320_u32 & (0_u32.wrapping_sub(crc & 1)));
        }
    }
    !crc
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::AtomicUsize;

    struct TestFolder(PathBuf);
    impl TestFolder {
        fn new() -> Self {
            Self(std::env::temp_dir().join(format!("coach-wheel-{}", uuid_v4())))
        }
        fn root(&self) -> PathBuf {
            self.0.join("rolling")
        }
        fn exports(&self) -> PathBuf {
            self.0.join("exports")
        }
    }
    impl Drop for TestFolder {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }
    fn device() -> WheelDevice {
        WheelDevice {
            id: "50000000-0000-0000-0000-000000000001".into(),
            name: "Synthetic wheel".into(),
            product_guid: None,
        }
    }
    fn reading(availability: &str) -> WheelReading {
        WheelReading {
            availability: availability.into(),
            axis: (availability == "Available").then_some(100),
            native_error: None,
        }
    }
    fn at(recorder: &mut Recorder, ms: u64, state: &str) {
        let now = recorder.start + Duration::from_millis(ms);
        recorder
            .sample(reading(state), now, 1_700_000_000_000 + ms, None)
            .unwrap();
    }
    fn wait(mut check: impl FnMut() -> bool) {
        let deadline = Instant::now() + Duration::from_secs(6);
        while !check() {
            assert!(
                Instant::now() < deadline,
                "diagnostics did not reach expected state"
            );
            thread::sleep(Duration::from_millis(20));
        }
    }
    fn snapshot(handle: &WheelHandle) -> WheelStatus {
        loop {
            if let Some(status) = handle.try_snapshot() {
                return status;
            }
            thread::yield_now();
        }
    }
    struct FakeInput {
        reads: Arc<AtomicUsize>,
        value: Arc<Mutex<WheelReading>>,
        select_error: bool,
        hold: Option<Arc<AtomicBool>>,
    }
    impl WheelInputSource for FakeInput {
        fn enumerate(&mut self) -> Result<Vec<WheelDevice>, String> {
            Ok(vec![device()])
        }
        fn select(&mut self, _: &str, _: u8, _: isize) -> Result<(), String> {
            if self.select_error {
                Err("Missing driver".into())
            } else {
                Ok(())
            }
        }
        fn read(&mut self) -> WheelReading {
            self.reads.fetch_add(1, Ordering::Relaxed);
            if let Some(hold) = &self.hold {
                while hold.load(Ordering::Acquire) {
                    thread::sleep(Duration::from_millis(10));
                }
            }
            self.value.lock().unwrap().clone()
        }
    }
    struct FakeUsb {
        starts: Arc<AtomicUsize>,
        recoveries: Arc<AtomicUsize>,
        recovery_directory: Option<PathBuf>,
        safe: bool,
        message: String,
        delay: Duration,
    }
    impl FakeUsb {
        fn off() -> Self {
            Self {
                starts: Arc::new(AtomicUsize::new(0)),
                recoveries: Arc::new(AtomicUsize::new(0)),
                recovery_directory: None,
                safe: true,
                message: "Off".into(),
                delay: Duration::ZERO,
            }
        }
    }
    impl WheelUsbTrace for FakeUsb {
        fn status(&mut self) -> String {
            self.message.clone()
        }
        fn safe_to_export(&mut self) -> bool {
            self.safe
        }
        fn start(&mut self, _: &Path) -> Result<(), String> {
            self.starts.fetch_add(1, Ordering::Relaxed);
            thread::sleep(self.delay);
            Ok(())
        }
        fn heartbeat(&mut self) {}
        fn stop(&mut self) -> Result<(), String> {
            Ok(())
        }
        fn recover(&mut self) -> Result<(), String> {
            self.recoveries.fetch_add(1, Ordering::Relaxed);
            thread::sleep(self.delay);
            let directory = self
                .recovery_directory
                .as_ref()
                .ok_or("No fake recovery directory")?;
            write_json(directory, "usb-status.json", &json!({"State":"Stopped"}))
                .map_err(|e| e.to_string())?;
            self.safe = true;
            self.message = "Interrupted USB cleanup confirmed; no new trace started.".into();
            Ok(())
        }
    }
    fn handle(
        folder: &TestFolder,
        usb: FakeUsb,
        select_error: bool,
        hold: Option<Arc<AtomicBool>>,
    ) -> (WheelHandle, Arc<AtomicUsize>, Arc<Mutex<WheelReading>>) {
        let reads = Arc::new(AtomicUsize::new(0));
        let value = Arc::new(Mutex::new(reading("Available")));
        let factory: SourceFactory = {
            let reads = reads.clone();
            let value = value.clone();
            Arc::new(move || {
                Box::new(FakeInput {
                    reads: reads.clone(),
                    value: value.clone(),
                    select_error,
                    hold: hold.clone(),
                })
            })
        };
        (
            WheelDiagnostics::new(folder.root(), folder.exports(), factory, Box::new(usb)),
            reads,
            value,
        )
    }
    fn enable(handle: &WheelHandle) {
        assert!(handle.send(WheelCommand::Enable {
            device: device(),
            axis: 0,
            window: 123
        }));
    }
    fn zip_entries(path: &Path) -> Vec<(String, Vec<u8>)> {
        let bytes = fs::read(path).unwrap();
        let mut offset = 0;
        let mut entries = Vec::new();
        while bytes.get(offset..offset + 4) == Some(&0x04034b50_u32.to_le_bytes()) {
            assert_eq!(
                u16::from_le_bytes(bytes[offset + 12..offset + 14].try_into().unwrap()),
                0x0021,
                "ZIP dates must encode a valid month and day"
            );
            let size =
                u32::from_le_bytes(bytes[offset + 18..offset + 22].try_into().unwrap()) as usize;
            let names =
                u16::from_le_bytes(bytes[offset + 26..offset + 28].try_into().unwrap()) as usize;
            let extras =
                u16::from_le_bytes(bytes[offset + 28..offset + 30].try_into().unwrap()) as usize;
            let name = String::from_utf8(bytes[offset + 30..offset + 30 + names].to_vec()).unwrap();
            let data = offset + 30 + names + extras;
            entries.push((name, bytes[data..data + size].to_vec()));
            offset = data + size;
        }
        entries
    }

    #[test]
    fn default_off_touches_no_device_usb_or_directory_and_is_per_launch() {
        let folder = TestFolder::new();
        let calls = Arc::new(AtomicUsize::new(0));
        let factory: SourceFactory = {
            let calls = calls.clone();
            Arc::new(move || {
                calls.fetch_add(1, Ordering::Relaxed);
                panic!("OFF must not open input")
            })
        };
        let usb = FakeUsb::off();
        let starts = usb.starts.clone();
        let handle = WheelDiagnostics::new(folder.root(), folder.exports(), factory, Box::new(usb));
        assert!(!snapshot(&handle).running);
        assert!(handle.send(WheelCommand::MarkIncident));
        assert!(handle.shutdown_wait(Duration::from_secs(2)));
        assert_eq!(calls.load(Ordering::Relaxed), 0);
        assert_eq!(starts.load(Ordering::Relaxed), 0);
        assert!(!folder.0.exists());
    }

    #[test]
    fn clocks_sdk_connection_and_partial_windows_match_legacy_semantics() {
        let folder = TestFolder::new();
        let mut recorder = Recorder::start(&folder.root(), &device(), 0, SEGMENT_BUDGET).unwrap();
        let observation = SdkObservation {
            utc: 1_699_992_800_000,
            monotonic: recorder.start,
            steering: Some(0.75),
            connected: true,
        };
        recorder
            .sample(
                reading("Available"),
                recorder.start + Duration::from_millis(200),
                1_700_000_000_200,
                Some(observation.clone()),
            )
            .unwrap();
        let mut disconnected = observation;
        disconnected.connected = false;
        recorder
            .sample(
                reading("Available"),
                recorder.start + Duration::from_millis(400),
                1_700_000_000_400,
                Some(disconnected),
            )
            .unwrap();
        recorder
            .mark("Driver marked an incident", 450.0, 1_700_000_000_450)
            .unwrap();
        recorder.flush().unwrap();
        assert_eq!(recorder.history[0].sdk_age_ms, Some(200.0));
        assert_eq!(recorder.history[0].sdk_steering_radians, Some(0.75));
        assert!(!recorder.history[1].sdk_connected);
        assert!(!recorder.incidents[0].post_window_complete);
        for record in &recorder.history {
            assert_eq!(
                record.elapsed_ms,
                record.monotonic_ticks as f64 / 1_000_000.0
            );
        }
        assert!(
            recorder.incidents[0]
                .records
                .iter()
                .all(|x| x.elapsed_ms < recorder.incidents[0].marked_ms)
        );
        let marker_age = recorder.history.back().unwrap().sdk_age_ms.unwrap() + 50.0;
        let files: Vec<_> = fs::read_dir(&recorder.directory)
            .unwrap()
            .map(|x| x.unwrap().path())
            .filter(|x| is_sample_name(x.file_name().unwrap().to_str().unwrap()))
            .collect();
        let marker: WheelDiagnosticRecord = serde_json::from_str(
            fs::read_to_string(&files[0])
                .unwrap()
                .lines()
                .last()
                .unwrap(),
        )
        .unwrap();
        assert_eq!(marker.sdk_age_ms, Some(marker_age));
    }

    #[test]
    fn flat_axis_is_not_disconnect_and_availability_changes_are_throttled() {
        let folder = TestFolder::new();
        let mut recorder = Recorder::start(&folder.root(), &device(), 0, SEGMENT_BUDGET).unwrap();
        for ms in (0..30_000).step_by(100) {
            at(&mut recorder, ms, "Available");
        }
        assert!(recorder.incidents.is_empty());
        at(&mut recorder, 30_000, "Not attached");
        assert_eq!(recorder.incidents.len(), 1);
        at(&mut recorder, 30_100, "Available");
        at(&mut recorder, 30_200, "Read failed");
        assert_eq!(recorder.incidents.len(), 1);
        at(&mut recorder, 60_100, "Available");
        at(&mut recorder, 60_200, "Not attached");
        assert_eq!(recorder.incidents.len(), 2);
        assert!(
            recorder
                .history
                .iter()
                .any(|x| x.availability == "Not attached" && x.raw_axis.is_none())
        );
    }

    #[test]
    fn incident_pre_and_post_windows_and_ring_are_bounded() {
        let folder = TestFolder::new();
        let mut recorder = Recorder::start(&folder.root(), &device(), 0, SEGMENT_BUDGET).unwrap();
        for ms in (0..80_000).step_by(100) {
            at(&mut recorder, ms, "Available");
        }
        assert_eq!(recorder.history.len(), HISTORY_SAMPLES);
        recorder
            .mark("Driver marked an incident", 79_950.0, 1_700_000_079_950)
            .unwrap();
        for ms in (80_000..100_100).step_by(100) {
            at(&mut recorder, ms, "Available");
        }
        let incident = &recorder.incidents[0];
        assert!(incident.post_window_complete);
        assert!(
            incident
                .records
                .iter()
                .all(|x| x.elapsed_ms >= incident.marked_ms - 10_000.0
                    && x.elapsed_ms <= incident.marked_ms + 20_000.0)
        );
        assert!(
            incident
                .records
                .iter()
                .any(|x| x.elapsed_ms > incident.marked_ms + 19_000.0)
        );
        assert!(incident.records.len() <= 302);
        for _ in 0..20 {
            recorder
                .mark("Driver marked an incident", 100_000.0, 1_700_000_100_000)
                .unwrap();
        }
        assert_eq!(recorder.incidents.len(), MAX_INCIDENTS);
        assert_eq!(
            fs::read_dir(&recorder.directory)
                .unwrap()
                .filter(|e| is_incident_name(e.as_ref().unwrap().file_name().to_str().unwrap()))
                .count(),
            MAX_INCIDENTS
        );
    }

    #[test]
    fn rolling_segments_stay_within_four_files_and_exact_byte_bound() {
        let folder = TestFolder::new();
        let mut recorder = Recorder::start(&folder.root(), &device(), 0, 1024).unwrap();
        for ms in (0..10_000).step_by(100) {
            at(&mut recorder, ms, "Available");
        }
        recorder.close_segment();
        let files: Vec<_> = fs::read_dir(&recorder.directory)
            .unwrap()
            .map(|x| x.unwrap().path())
            .filter(|x| is_sample_name(x.file_name().unwrap().to_str().unwrap()))
            .collect();
        assert_eq!(files.len(), 4);
        assert!(files.iter().all(|x| fs::metadata(x).unwrap().len() <= 1024));
        assert!(!recorder.directory.join("samples-000000.ndjson").exists());
    }

    #[test]
    fn pruning_keeps_three_normal_sessions_and_durable_exports_and_unknown_files() {
        let folder = TestFolder::new();
        let mut exports = Vec::new();
        for _ in 0..5 {
            let mut recorder =
                Recorder::start(&folder.root(), &device(), 0, SEGMENT_BUDGET).unwrap();
            at(&mut recorder, 0, "Available");
            exports.push(
                recorder
                    .export(&folder.exports(), &WheelStatus::default())
                    .unwrap(),
            );
            thread::sleep(Duration::from_millis(5));
        }
        assert_eq!(session_directories(&folder.root()).unwrap().len(), 3);
        assert!(exports.iter().all(|x| x.exists()));
        let protected = folder.root().join("11111111111111111111111111111111");
        fs::create_dir(&protected).unwrap();
        fs::write(protected.join("other-user-file.txt"), "preserve").unwrap();
        prune(&folder.root(), None, 0).unwrap();
        assert!(protected.join("other-user-file.txt").exists());
        assert!(exports.iter().all(|x| x.exists()));
    }

    #[test]
    fn uncertain_usb_sessions_are_never_pruned_or_exported() {
        let folder = TestFolder::new();
        make_safe_directory(&folder.root()).unwrap();
        let protected = folder.root().join("22222222222222222222222222222222");
        fs::create_dir(&protected).unwrap();
        fs::write(protected.join("usb-owner.json"), "{}").unwrap();
        fs::write(
            protected.join("usb-status.json"),
            r#"{"State":"Cleanup uncertain"}"#,
        )
        .unwrap();
        prune(&folder.root(), None, 0).unwrap();
        assert!(protected.exists());
        let mut usb = FakeUsb::off();
        usb.safe = false;
        let (handle, reads, _) = handle(&folder, usb, false, None);
        enable(&handle);
        wait(|| reads.load(Ordering::Relaxed) > 0);
        assert!(handle.send(WheelCommand::Export));
        wait(|| !snapshot(&handle).busy && !snapshot(&handle).running);
        assert!(!snapshot(&handle).can_export);
        assert!(snapshot(&handle).export_path.is_none());
        assert!(!folder.exports().exists());
        assert!(handle.shutdown_wait(Duration::from_secs(2)));
    }

    #[test]
    fn budget_reservation_fails_without_deleting_unresolved_sessions() {
        let folder = TestFolder::new();
        make_safe_directory(&folder.root()).unwrap();
        let protected = folder.root().join("33333333333333333333333333333333");
        fs::create_dir(&protected).unwrap();
        fs::write(protected.join("usb-owner.json"), "{}").unwrap();
        let file = File::create(protected.join("usb.etl")).unwrap();
        file.set_len(ROOT_BUDGET).unwrap();
        assert!(Recorder::start(&folder.root(), &device(), 0, SEGMENT_BUDGET).is_err());
        assert!(protected.exists());
        file.set_len(ROOT_BUDGET + 1).unwrap();
        assert!(ensure_budget(&folder.root()).is_err());
    }

    #[test]
    fn explicit_recovery_works_off_at_full_root_and_enables_safe_pruning_without_starting_trace() {
        let folder = TestFolder::new();
        make_safe_directory(&folder.root()).unwrap();
        let protected = folder.root().join("44444444444444444444444444444444");
        fs::create_dir(&protected).unwrap();
        fs::write(protected.join("usb-owner.json"), "{}").unwrap();
        fs::write(
            protected.join("usb-status.json"),
            r#"{"State":"Cleanup uncertain"}"#,
        )
        .unwrap();
        File::create(protected.join("usb.etl"))
            .unwrap()
            .set_len(ROOT_BUDGET)
            .unwrap();
        let mut usb = FakeUsb::off();
        usb.recovery_directory = Some(protected.clone());
        usb.delay = Duration::from_millis(400);
        let recoveries = usb.recoveries.clone();
        let starts = usb.starts.clone();
        let (handle, reads, _) = handle(&folder, usb, false, None);
        enable(&handle);
        wait(|| snapshot(&handle).message.contains("storage is full"));
        assert!(!snapshot(&handle).running);
        assert!(snapshot(&handle).can_recover_usb);
        assert_eq!(reads.load(Ordering::Relaxed), 0);
        assert!(protected.exists());
        assert!(handle.send(WheelCommand::RecoverUsb));
        wait(|| recoveries.load(Ordering::Relaxed) == 1);
        assert!(snapshot(&handle).busy);
        assert!(!snapshot(&handle).can_recover_usb);
        assert!(!snapshot(&handle).can_start_usb);
        assert!(!snapshot(&handle).running);
        assert_eq!(starts.load(Ordering::Relaxed), 0);
        // The separate USB worker can wait at a prompt without blocking status or commands.
        assert!(handle.send(WheelCommand::RecoverUsb));
        wait(|| snapshot(&handle).message.contains("wait for any USB"));
        wait(|| snapshot(&handle).can_recover_usb);
        assert_eq!(recoveries.load(Ordering::Relaxed), 1);
        assert!(
            protected.exists(),
            "recovery alone does not delete retained capture data"
        );
        enable(&handle);
        wait(|| reads.load(Ordering::Relaxed) > 0);
        assert!(
            !protected.exists(),
            "only confirmed-safe history can be reclaimed for capture"
        );
        assert_eq!(starts.load(Ordering::Relaxed), 0);
        assert!(handle.shutdown_wait(Duration::from_secs(2)));
    }

    #[test]
    fn missing_driver_leaves_failure_metadata_and_exportable_capture() {
        let folder = TestFolder::new();
        let (handle, _, _) = handle(&folder, FakeUsb::off(), true, None);
        enable(&handle);
        wait(|| snapshot(&handle).has_capture && !snapshot(&handle).running);
        assert!(snapshot(&handle).message.contains("Missing driver"));
        assert!(handle.send(WheelCommand::Export));
        wait(|| snapshot(&handle).export_path.is_some());
        let entries = zip_entries(snapshot(&handle).export_path.as_ref().unwrap());
        let manifest: serde_json::Value =
            serde_json::from_slice(&entries.iter().find(|x| x.0 == "manifest.json").unwrap().1)
                .unwrap();
        assert!(
            manifest["CaptureFailure"]
                .as_str()
                .unwrap()
                .contains("Missing driver")
        );
        assert!(handle.shutdown_wait(Duration::from_secs(2)));
    }

    #[test]
    fn declined_or_unavailable_usb_and_slow_prompt_do_not_stop_ordinary_capture() {
        for message in [
            "Off: administrator prompt cancelled.",
            "USB ETW unavailable: no provider.",
        ] {
            let folder = TestFolder::new();
            let mut usb = FakeUsb::off();
            usb.message = message.into();
            usb.delay = Duration::from_millis(400);
            let starts = usb.starts.clone();
            let (handle, reads, _) = handle(&folder, usb, false, None);
            enable(&handle);
            wait(|| reads.load(Ordering::Relaxed) >= 2);
            assert!(handle.send(WheelCommand::StartUsb));
            let before = reads.load(Ordering::Relaxed);
            wait(|| starts.load(Ordering::Relaxed) > 0);
            wait(|| reads.load(Ordering::Relaxed) >= before + 5);
            assert!(snapshot(&handle).running);
            assert!(handle.send(WheelCommand::Export));
            wait(|| snapshot(&handle).export_path.is_some());
            assert!(handle.shutdown_wait(Duration::from_secs(2)));
        }
    }

    #[test]
    fn hung_reader_stops_status_and_gates_export_and_restart_until_it_exits() {
        let folder = TestFolder::new();
        let hold = Arc::new(AtomicBool::new(true));
        let (handle, reads, _) = handle(&folder, FakeUsb::off(), false, Some(hold.clone()));
        enable(&handle);
        wait(|| reads.load(Ordering::Relaxed) == 1);
        wait(|| !snapshot(&handle).running);
        assert!(!snapshot(&handle).can_export);
        assert!(snapshot(&handle).message.contains("one second"));
        let start = Instant::now();
        assert!(handle.send(WheelCommand::Export));
        let _ = handle.try_snapshot();
        assert!(start.elapsed() < Duration::from_millis(100));
        wait(|| !snapshot(&handle).busy);
        assert!(snapshot(&handle).export_path.is_none());
        enable(&handle);
        wait(|| snapshot(&handle).message.contains("Previous reader"));
        assert_eq!(reads.load(Ordering::Relaxed), 1);
        hold.store(false, Ordering::Release);
        wait(|| snapshot(&handle).can_export);
        assert!(handle.send(WheelCommand::Export));
        wait(|| snapshot(&handle).export_path.is_some());
        assert!(handle.shutdown_wait(Duration::from_secs(2)));
    }

    #[test]
    fn explicit_export_is_allowlisted_and_evidence_is_retained_for_independent_zipfile_validation()
    {
        let folder = TestFolder::new();
        let mut recorder = Recorder::start(&folder.root(), &device(), 0, SEGMENT_BUDGET).unwrap();
        for ms in (0..12_000).step_by(100) {
            at(&mut recorder, ms, "Available");
        }
        recorder
            .mark("Driver marked an incident", 11_950.0, 1_700_000_011_950)
            .unwrap();
        for ms in (12_000..32_100).step_by(100) {
            at(&mut recorder, ms, "Available");
        }
        fs::write(recorder.directory.join("settings.json"), "never export").unwrap();
        fs::write(recorder.directory.join("usb-owner.json"), "never export").unwrap();
        let status = WheelStatus {
            devices: vec![device()],
            ..WheelStatus::default()
        };
        let exports = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .unwrap()
            .join("target/wheel-export-validation");
        let path = recorder.export(&exports, &status).unwrap();
        let entries = zip_entries(&path);
        assert!(entries.iter().any(|x| x.0 == "metadata.json"));
        assert!(entries.iter().all(|x| is_export_name(&x.0)));
        assert!(!entries.iter().any(|x| x.0.contains("owner")
            || x.0.contains("settings")
            || x.0.contains("heartbeat")));
        let manifest: serde_json::Value =
            serde_json::from_slice(&entries.iter().find(|x| x.0 == "manifest.json").unwrap().1)
                .unwrap();
        assert_eq!(manifest["Status"]["Devices"], json!([]));
        assert_eq!(crc32(b"123456789"), 0xcbf43926);
        println!("independent ZIP validation evidence: {}", path.display());
    }
}

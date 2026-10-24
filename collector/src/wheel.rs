//! Persistent wheel interruption recorder, ported from the recovered desktop source.
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
const SESSION_RESERVE: u64 = 96 * 1024 * 1024;
/// One minute per segment, plus the partial boundary minute. Memory stays at 60 seconds.
pub const RETAINED_MINUTES: usize = 60;
const MAX_SAMPLE_SEGMENTS: usize = RETAINED_MINUTES + 1;
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
    fn snapshot(&mut self) -> Result<Option<UsbIncidentSnapshot>, String> {
        Ok(None)
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "PascalCase")]
pub struct UsbIncidentSnapshot {
    pub path: PathBuf,
    pub requested_utc: String,
    pub completed_utc: String,
    pub trace_restarted: bool,
    pub limitations: String,
    pub trace_stopped_utc: Option<String>,
    pub trace_restarted_utc: Option<String>,
    pub interruption_ms: Option<u64>,
}

enum UsbCommand {
    Start(PathBuf),
    Stop,
    Refresh,
    Recover,
    Snapshot(SyncSender<Result<Option<UsbIncidentSnapshot>, String>>),
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
    prompt_pending: Arc<AtomicUsize>,
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
        let prompt_pending = Arc::new(AtomicUsize::new(0));
        let worker_prompt_pending = prompt_pending.clone();
        thread::Builder::new().name("wheel-usb-control".into()).spawn(move || {
            while let Ok(command) = receiver.recv() {
                let prompt = matches!(command, UsbCommand::Start(_) | UsbCommand::Recover);
                let operation = matches!(command, UsbCommand::Start(_) | UsbCommand::Stop | UsbCommand::Recover | UsbCommand::Snapshot(_));
                let result = match command {
                    UsbCommand::Start(path) => usb.start(&path),
                    UsbCommand::Stop => usb.stop(),
                    UsbCommand::Recover => usb.recover(),
                    UsbCommand::Snapshot(reply) => { let _ = reply.send(usb.snapshot()); Ok(()) },
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
                if prompt { worker_prompt_pending.fetch_sub(1, Ordering::Release); }
            }
        }).expect("cannot start optional USB control worker");
        Self {
            sender,
            snapshot,
            pending,
            prompt_pending,
        }
    }

    fn operation(&self, command: UsbCommand) -> Result<(), String> {
        let prompt = matches!(command, UsbCommand::Start(_) | UsbCommand::Recover);
        self.pending.fetch_add(1, Ordering::AcqRel);
        if prompt {
            self.prompt_pending.fetch_add(1, Ordering::AcqRel);
        }
        if self.sender.try_send(command).is_err() {
            self.pending.fetch_sub(1, Ordering::Release);
            if prompt {
                self.prompt_pending.fetch_sub(1, Ordering::Release);
            }
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
    fn snapshot(&self) -> Result<Receiver<Result<Option<UsbIncidentSnapshot>, String>>, String> {
        let (sender, receiver) = mpsc::sync_channel(1);
        self.operation(UsbCommand::Snapshot(sender))?;
        Ok(receiver)
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
            return if self.prompt_pending.load(Ordering::Acquire) > 0 {
                "USB operation pending; confirm any administrator prompt."
            } else {
                "USB snapshot or cleanup pending."
            }
            .into();
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

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum DeviceRole {
    Wheel,
    Pedals,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "PascalCase")]
pub struct DeviceMonitorConfig {
    pub enabled: bool,
    pub device: Option<WheelDevice>,
    pub axes: Vec<u8>,
}

impl DeviceMonitorConfig {
    fn for_role(role: DeviceRole) -> Self {
        Self {
            enabled: true,
            device: None,
            axes: if role == DeviceRole::Wheel {
                vec![0]
            } else {
                (0..6).collect()
            },
        }
    }
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(rename_all = "PascalCase")]
pub struct AxisStatus {
    pub axis: u8,
    pub value: Option<i32>,
    #[serde(default)]
    pub availability: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub native_error: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "PascalCase")]
pub struct DeviceMonitorStatus {
    pub config: DeviceMonitorConfig,
    pub availability: String,
    pub axes: Vec<AxisStatus>,
    pub last_sample_age_ms: Option<u64>,
    pub last_sample_utc: Option<String>,
    #[serde(skip)]
    sample_at: Option<Instant>,
}

impl DeviceMonitorStatus {
    fn new(config: DeviceMonitorConfig) -> Self {
        Self {
            availability: if !config.enabled {
                "Disabled"
            } else if config.device.is_none() {
                "Not selected"
            } else {
                "Not recording"
            }
            .into(),
            axes: Vec::new(),
            last_sample_age_ms: None,
            last_sample_utc: None,
            sample_at: None,
            config,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "PascalCase")]
pub struct WheelCaptureSummary {
    pub id: String,
    pub directory: PathBuf,
    pub started_utc: String,
    pub last_sample_utc: Option<String>,
    pub sample_count: u64,
    pub incidents: usize,
    pub can_export: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "PascalCase", default)]
struct Preferences {
    auto_record: bool,
    auto_usb: bool,
    device: Option<WheelDevice>,
    axis: u8,
    wheel: DeviceMonitorConfig,
    pedals: DeviceMonitorConfig,
    #[serde(flatten)]
    extra: serde_json::Map<String, serde_json::Value>,
}

impl Default for Preferences {
    fn default() -> Self {
        Self {
            auto_record: true,
            auto_usb: true,
            device: None,
            axis: 0,
            wheel: DeviceMonitorConfig::for_role(DeviceRole::Wheel),
            pedals: DeviceMonitorConfig::for_role(DeviceRole::Pedals),
            extra: Default::default(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "PascalCase")]
struct ExportReceipt {
    path: PathBuf,
    bytes: u64,
    utc: String,
}

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
    pub auto_record: bool,
    pub auto_usb: bool,
    pub selected_device_name: Option<String>,
    pub selected_axis: u8,
    pub sample_count: u64,
    pub capture_started_utc: Option<String>,
    pub last_sample_utc: Option<String>,
    pub last_sample_age_ms: Option<u64>,
    #[serde(skip)]
    pub(crate) last_sample_instant: Option<Instant>,
    pub dropped_samples: u64,
    pub retained_from_utc: Option<String>,
    pub capture_directory: Option<PathBuf>,
    pub exports_directory: Option<PathBuf>,
    pub export_bytes: Option<u64>,
    pub exported_utc: Option<String>,
    pub retained_captures: Vec<WheelCaptureSummary>,
    pub wheel: DeviceMonitorStatus,
    pub pedals: DeviceMonitorStatus,
    pub report_pending: bool,
    pub report_sequence: u64,
    pub report_error: Option<String>,
}

impl Default for WheelDiagnosticStatus {
    fn default() -> Self {
        Self {
            running: false,
            busy: false,
            message: "Starting diagnostics…".into(),
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
            auto_record: true,
            auto_usb: true,
            selected_device_name: None,
            selected_axis: 0,
            sample_count: 0,
            capture_started_utc: None,
            last_sample_utc: None,
            last_sample_age_ms: None,
            last_sample_instant: None,
            dropped_samples: 0,
            retained_from_utc: None,
            capture_directory: None,
            exports_directory: None,
            export_bytes: None,
            exported_utc: None,
            retained_captures: Vec::new(),
            wheel: DeviceMonitorStatus::new(DeviceMonitorConfig::for_role(DeviceRole::Wheel)),
            pedals: DeviceMonitorStatus::new(DeviceMonitorConfig::for_role(DeviceRole::Pedals)),
            report_pending: false,
            report_sequence: 0,
            report_error: None,
        }
    }
}

pub type WheelStatus = WheelDiagnosticStatus;
pub type WheelHandle = WheelDiagnostics;

#[derive(Debug, Clone)]
pub enum WheelCommand {
    Initialize {
        window: isize,
    },
    SetAutoRecord(bool),
    SetAutoUsb(bool),
    ConfigureDevice {
        role: DeviceRole,
        config: DeviceMonitorConfig,
    },
    Run {
        window: isize,
    },
    Stop,
    ReportDiagnosticIncident,
    Refresh,
    Enable {
        device: WheelDevice,
        axis: u8,
        window: isize,
    },
    Disable,
    MarkIncident,
    Export,
    ExportCapture {
        session_id: String,
    },
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
    report_admitted: AtomicBool,
    shutdown_requested: AtomicBool,
}

struct HandleInner {
    commands: SyncSender<StampedCommand>,
    shared: Arc<Shared>,
}

impl Drop for HandleInner {
    fn drop(&mut self) {
        self.shared
            .shutdown_requested
            .store(true, Ordering::Release);
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
        Self::with_archive_writer(root, exports, factory, usb, Arc::new(PreparedReport::save))
    }

    fn with_archive_writer(
        root: PathBuf,
        exports: PathBuf,
        factory: SourceFactory,
        usb: Box<dyn WheelUsbTrace>,
        archive_writer: ArchiveWriter,
    ) -> Self {
        let shared = Arc::new(Shared {
            status: Mutex::new(WheelStatus::default()),
            sdk: Mutex::new(None),
            observing: AtomicBool::new(false),
            finished: AtomicBool::new(false),
            report_admitted: AtomicBool::new(false),
            shutdown_requested: AtomicBool::new(false),
        });
        let (commands, receiver) = mpsc::sync_channel(16);
        let manager_shared = shared.clone();
        thread::Builder::new()
            .name("wheel-diagnostics".into())
            .spawn(move || {
                #[cfg(windows)]
                crate::win::enter_background_mode();
                let mut manager = Manager::new(root, exports, factory, usb, manager_shared);
                manager.archive_writer = archive_writer;
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
        let shutdown = matches!(command, WheelCommand::Shutdown);
        if !shutdown && self.inner.shared.shutdown_requested.load(Ordering::Acquire) {
            return false;
        }
        let already_shutting_down = shutdown
            && self
                .inner
                .shared
                .shutdown_requested
                .swap(true, Ordering::AcqRel);
        let report = matches!(command, WheelCommand::ReportDiagnosticIncident);
        if report
            && self
                .inner
                .shared
                .report_admitted
                .swap(true, Ordering::AcqRel)
        {
            return false;
        }
        let sent = self
            .inner
            .commands
            .try_send(StampedCommand::new(command))
            .is_ok();
        if report && !sent {
            self.inner
                .shared
                .report_admitted
                .store(false, Ordering::Release);
        }
        if shutdown && !sent && !already_shutting_down {
            self.inner
                .shared
                .shutdown_requested
                .store(false, Ordering::Release);
        }
        sent
    }

    pub fn try_snapshot(&self) -> Option<WheelStatus> {
        self.inner.shared.status.try_lock().ok().map(|s| {
            let mut snapshot = s.clone();
            snapshot.last_sample_age_ms = snapshot
                .last_sample_instant
                .map(|at| at.elapsed().as_millis().min(u64::MAX as u128) as u64);
            for monitor in [&mut snapshot.wheel, &mut snapshot.pedals] {
                monitor.last_sample_age_ms = monitor
                    .sample_at
                    .map(|at| at.elapsed().as_millis().min(u64::MAX as u128) as u64);
            }
            snapshot
        })
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
    Sample(
        WheelReading,
        Instant,
        u64,
        Option<SdkObservation>,
        u64,
        Vec<AxisStatus>,
    ),
    Gap(u64, Instant, u64),
    Failed(String),
}

struct ReaderWorker {
    role: DeviceRole,
    device_id: Option<String>,
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
        device: Option<WheelDevice>,
        axis: u8,
        window: isize,
    ) -> Self {
        Self::start_monitor(
            factory,
            shared,
            DeviceRole::Wheel,
            device,
            vec![axis],
            window,
        )
    }

    fn start_monitor(
        factory: SourceFactory,
        shared: Arc<Shared>,
        role: DeviceRole,
        device: Option<WheelDevice>,
        axes: Vec<u8>,
        window: isize,
    ) -> Self {
        Self::start_monitor_after(factory, shared, role, device, axes, window, Duration::ZERO)
    }

    fn start_monitor_after(
        factory: SourceFactory,
        shared: Arc<Shared>,
        role: DeviceRole,
        device: Option<WheelDevice>,
        axes: Vec<u8>,
        window: isize,
        retry_delay: Duration,
    ) -> Self {
        let cancel = Arc::new(AtomicBool::new(false));
        let device_id = device.as_ref().map(|d| d.id.clone());
        let read_started = Arc::new(Mutex::new(None));
        let (sender, events) = mpsc::sync_channel(64);
        let stop = cancel.clone();
        let started = read_started.clone();
        let join = thread::Builder::new()
            .name("wheel-input".into())
            .spawn(move || {
                *started.lock().unwrap() = Some(Instant::now());
                let mut sources: Vec<_> = device
                    .as_ref()
                    .map(|_| {
                        axes.iter()
                            .map(|axis| {
                                (
                                    *axis,
                                    factory(),
                                    false,
                                    Instant::now() + retry_delay,
                                    None::<String>,
                                )
                            })
                            .collect()
                    })
                    .unwrap_or_default();
                let mut dropped = 0;
                *started.lock().unwrap() = None;
                while !stop.load(Ordering::Acquire) {
                    let begin = Instant::now();
                    *started.lock().unwrap() = Some(begin);
                    let mut values = Vec::new();
                    let mut readings = Vec::new();
                    if let Some(device) = &device {
                        for (axis, source, selected, next_select, select_error) in &mut sources {
                            if !*selected && begin >= *next_select {
                                match source.select(&device.id, *axis, window) {
                                    Ok(()) => {
                                        *selected = true;
                                        *select_error = None;
                                    }
                                    Err(error) => {
                                        *select_error = Some(error);
                                        *next_select = begin + Duration::from_secs(2);
                                    }
                                }
                            }
                            let reading = if *selected {
                                source.read()
                            } else {
                                WheelReading {
                                    availability: "Device unavailable".into(),
                                    axis: None,
                                    native_error: select_error.clone(),
                                }
                            };
                            values.push(AxisStatus {
                                axis: *axis,
                                value: reading.axis,
                                availability: reading.availability.clone(),
                                native_error: reading.native_error.clone(),
                            });
                            readings.push(reading);
                        }
                    }
                    let mut reading = readings
                        .iter()
                        .find(|r| r.availability == "Available" && r.axis.is_some())
                        .or_else(|| readings.first())
                        .cloned()
                        .unwrap_or(WheelReading {
                            availability: "Device not selected; SDK + USB only".into(),
                            axis: None,
                            native_error: None,
                        });
                    let missing_supported: Vec<_> = sources
                        .iter()
                        .zip(&readings)
                        .filter(|((_, _, selected, _, _), value)| {
                            *selected && (value.availability != "Available" || value.axis.is_none())
                        })
                        .map(|((axis, ..), _)| axis.to_string())
                        .collect();
                    if reading.availability == "Available" && !missing_supported.is_empty() {
                        reading.availability = "Partial input".into();
                        reading.native_error = Some(format!(
                            "Supported axes unavailable: {}",
                            missing_supported.join(", ")
                        ));
                    }
                    let now = Instant::now();
                    *started.lock().unwrap() = None;
                    let sdk = shared.sdk.lock().unwrap().clone();
                    match sender.try_send(ReaderEvent::Sample(
                        reading,
                        now,
                        unix_ms_now(),
                        sdk,
                        dropped,
                        values,
                    )) {
                        Ok(()) => {
                            dropped = 0;
                        }
                        Err(mpsc::TrySendError::Disconnected(_)) => break,
                        Err(mpsc::TrySendError::Full(_)) => {
                            // Keep observing after a storage/export pause. The next accepted sample
                            // explicitly records how much history was missed; never claim continuity.
                            dropped += 1;
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
                if dropped > 0 {
                    let _ = sender.send(ReaderEvent::Gap(dropped, Instant::now(), unix_ms_now()));
                }
            })
            .expect("cannot start the wheel input reader");
        Self {
            role,
            device_id,
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
    pedal_reader: Option<ReaderWorker>,
    recorder: Option<Recorder>,
    enumeration: Option<JoinHandle<Result<Vec<WheelDevice>, String>>>,
    heartbeat: Instant,
    usb_requested: bool,
    recovering: bool,
    preferences: Preferences,
    initialized: bool,
    startup_window: Option<isize>,
    export_blocked: bool,
    native_window: isize,
    pending_report: Option<PendingReport>,
    pending_archive: Option<PendingArchive>,
    archive_writer: ArchiveWriter,
}

struct PendingReport {
    at: Instant,
    utc: u64,
    requested: Instant,
    reply: Receiver<Result<Option<UsbIncidentSnapshot>, String>>,
    directory: PathBuf,
    status: WheelStatus,
}

type ArchiveWriter = Arc<dyn Fn(PreparedReport) -> io::Result<ReportSaved> + Send + Sync>;

struct PendingArchive {
    reply: Receiver<io::Result<ReportSaved>>,
}

struct ReportSaved {
    receipt: ExportReceipt,
    warning: Option<String>,
    usb_included: bool,
}

impl Manager {
    fn new(
        root: PathBuf,
        exports: PathBuf,
        factory: SourceFactory,
        usb: Box<dyn WheelUsbTrace>,
        shared: Arc<Shared>,
    ) -> Self {
        let mut preferences =
            read_json_bounded::<Preferences>(&root.join("preferences.json")).unwrap_or_default();
        let has_monitor_config =
            read_json_bounded::<serde_json::Value>(&root.join("preferences.json"))
                .ok()
                .is_some_and(|v| v.get("Wheel").is_some());
        if !has_monitor_config && preferences.wheel.device.is_none() && preferences.device.is_some()
        {
            preferences.wheel.device = preferences.device.clone();
            preferences.wheel.axes = vec![preferences.axis];
        }
        let mut status = WheelStatus {
            auto_record: preferences.auto_record,
            auto_usb: preferences.auto_usb,
            selected_device_name: preferences.device.as_ref().map(|x| x.name.clone()),
            selected_axis: preferences.axis,
            exports_directory: Some(exports.clone()),
            ..WheelStatus::default()
        };
        status.wheel = DeviceMonitorStatus::new(preferences.wheel.clone());
        status.pedals = DeviceMonitorStatus::new(preferences.pedals.clone());
        if let Ok(receipt) = read_json_bounded::<ExportReceipt>(&root.join("last-export.json"))
            && safe_file_metadata(&receipt.path).is_ok_and(|m| m.len() == receipt.bytes)
        {
            status.export_path = Some(receipt.path);
            status.export_bytes = Some(receipt.bytes);
            status.exported_utc = Some(receipt.utc);
        }
        status.retained_captures = capture_history(&root).unwrap_or_default();
        Self {
            root,
            exports,
            factory,
            usb: UsbWorker::new(usb),
            shared,
            status,
            reader: None,
            pedal_reader: None,
            recorder: None,
            enumeration: None,
            heartbeat: Instant::now(),
            usb_requested: false,
            recovering: false,
            preferences,
            initialized: false,
            startup_window: None,
            export_blocked: false,
            native_window: 0,
            pending_report: None,
            pending_archive: None,
            archive_writer: Arc::new(PreparedReport::save),
        }
    }

    fn publish(&self) {
        // Snapshot locks contain no native calls, filesystem work, or waits.
        let mut snapshot = self.status.clone();
        if let Some(recorder) = &self.recorder {
            snapshot.sample_count = recorder.sample_count;
            snapshot.capture_started_utc = Some(recorder.started_utc.clone());
            snapshot.last_sample_utc = recorder.last_sample_utc.clone();
            snapshot.last_sample_age_ms = recorder
                .last_sample_at
                .map(|at| at.elapsed().as_millis().min(u64::MAX as u128) as u64);
            snapshot.last_sample_instant = recorder.last_sample_at;
            snapshot.dropped_samples = recorder.dropped_samples;
            snapshot.retained_from_utc = recorder.segment_starts.front().cloned();
            snapshot.capture_directory = Some(recorder.directory.clone());
            snapshot.can_export = !snapshot.busy
                && (self.reader.as_ref().is_none_or(|r| !r.timed_out))
                && !self.usb.is_pending()
                && (self.status.running || self.usb.safe_to_export())
                && (!self.export_blocked || self.usb.safe_to_export());
        }
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
        let mut shutting_down = false;
        loop {
            let delay = if self.reader.is_some()
                || self.enumeration.is_some()
                || self.status.report_pending
            {
                Duration::from_millis(40)
            } else {
                Duration::from_secs(1)
            };
            if shutting_down {
                if !self.status.report_pending {
                    break;
                }
                // shutdown_wait keeps its caller's bound; this actor retains the accepted
                // report and USB heartbeat until the archive has a terminal result.
                thread::sleep(Duration::from_millis(40));
            } else {
                match commands.recv_timeout(delay) {
                    Ok(StampedCommand {
                        command: WheelCommand::Shutdown,
                        ..
                    })
                    | Err(mpsc::RecvTimeoutError::Disconnected) => {
                        self.shared
                            .shutdown_requested
                            .store(true, Ordering::Release);
                        shutting_down = true;
                        if !self.status.report_pending {
                            break;
                        }
                    }
                    Ok(command) => self.command(command),
                    Err(mpsc::RecvTimeoutError::Timeout) => {}
                }
            }
            self.poll_reader();
            self.poll_enumeration();
            self.poll_report();
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
            WheelCommand::Initialize { window } => {
                if self.initialized {
                    return;
                }
                self.initialized = true;
                self.native_window = window;
                self.startup_window = Some(window);
                self.status.message = if self.preferences.auto_record {
                    "Starting automatic diagnostics…".into()
                } else {
                    "Recording is off. Automatic recording is disabled.".into()
                };
                self.refresh();
            }
            WheelCommand::SetAutoRecord(value) => {
                self.preferences.auto_record = value;
                self.status.auto_record = value;
                self.save_preferences();
                self.publish();
            }
            WheelCommand::SetAutoUsb(value) => {
                let was_enabled = self.preferences.auto_usb;
                self.preferences.auto_usb = value;
                self.status.auto_usb = value;
                self.save_preferences();
                if value && self.status.running {
                    self.start_usb();
                }
                if !value && was_enabled && self.usb_requested {
                    self.persist_usb_stop();
                    self.usb.request_stop();
                    self.status.usb = self.usb.status();
                }
                self.publish();
            }
            WheelCommand::ConfigureDevice { role, mut config } => {
                config.axes.sort_unstable();
                config.axes.dedup();
                if config.axes.is_empty()
                    || config.axes.len() > 6
                    || config.axes.iter().any(|axis| *axis > 5)
                {
                    self.fail("Device axes must be within X, Y, Z, Rx, Ry and Rz.".into());
                    return;
                }
                match role {
                    DeviceRole::Wheel => {
                        self.preferences.device = config.device.clone();
                        self.preferences.axis = config.axes[0];
                        self.preferences.wheel = config.clone();
                        self.status.wheel = DeviceMonitorStatus::new(config);
                    }
                    DeviceRole::Pedals => {
                        self.preferences.pedals = config.clone();
                        self.status.pedals = DeviceMonitorStatus::new(config);
                    }
                }
                self.save_preferences();
                if self.status.running {
                    self.reconfigure_role(role);
                }
                self.publish();
            }
            WheelCommand::Run { window } => {
                self.native_window = window;
                let config = self.preferences.wheel.clone();
                self.enable_capture(
                    if config.enabled { config.device } else { None },
                    config.axes.first().copied().unwrap_or(0),
                    window,
                );
            }
            WheelCommand::ReportDiagnosticIncident => self.report_incident(command.at, command.utc),
            WheelCommand::Refresh => {
                self.refresh();
            }
            WheelCommand::Enable {
                device,
                axis,
                window,
            } => self.enable(device, axis, window),
            WheelCommand::Disable | WheelCommand::Stop => {
                self.status.busy = true;
                self.publish();
                self.stop_reader(Duration::from_secs(3));
                if self.reader.is_none() {
                    self.status.message = "Stopped. History is ready to export.".into();
                }
                self.status.busy = self.status.report_pending;
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
            WheelCommand::ExportCapture { session_id } => self.export_capture(&session_id),
            WheelCommand::StartUsb => {
                self.start_usb();
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

    fn refresh(&mut self) {
        if self.enumeration.is_none() {
            if !self.status.running {
                self.status.busy = true;
            }
            self.publish();
            let factory = self.factory.clone();
            self.enumeration = Some(thread::spawn(move || factory().enumerate()));
        }
    }

    fn save_preferences(&mut self) {
        let result = (|| {
            make_safe_directory(&self.root)?;
            let mut updated = serde_json::to_value(&self.preferences)?;
            if let Ok(previous) =
                read_json_bounded::<serde_json::Value>(&self.root.join("preferences.json"))
            {
                merge_unknown_fields(&previous, &mut updated);
            }
            write_json(&self.root, "preferences.json", &updated)
        })();
        if let Err(error) = result {
            self.status.message = format!("Recording preference could not be saved: {error}");
        }
    }

    fn reconfigure_role(&mut self, role: DeviceRole) {
        let mut discarded = 0_u64;
        let slot = if role == DeviceRole::Wheel {
            &mut self.reader
        } else {
            &mut self.pedal_reader
        };
        if let Some(reader) = slot {
            reader.cancel.store(true, Ordering::Release);
            let start = Instant::now();
            while !reader.join.is_finished() && start.elapsed() < Duration::from_secs(1) {
                // Drain to let a final explicit backpressure-gap event finish without blocking.
                while let Ok(event) = reader.events.try_recv() {
                    discarded += match event {
                        ReaderEvent::Sample(_, _, _, _, dropped, _) => dropped + 1,
                        ReaderEvent::Gap(dropped, ..) => dropped,
                        ReaderEvent::Failed(_) => 0,
                    };
                }
                thread::sleep(Duration::from_millis(10));
            }
            if !reader.join.is_finished() {
                self.status.message =
                    "Device change waits for an outstanding driver read to exit.".into();
                if discarded > 0
                    && let Some(recorder) = &mut self.recorder
                {
                    let _ = recorder.gap(discarded, Instant::now(), unix_ms_now());
                }
                return;
            }
            while let Ok(event) = reader.events.try_recv() {
                discarded += match event {
                    ReaderEvent::Sample(_, _, _, _, dropped, _) => dropped + 1,
                    ReaderEvent::Gap(dropped, ..) => dropped,
                    ReaderEvent::Failed(_) => 0,
                };
            }
        }
        if let Some(reader) = slot.take() {
            let _ = reader.join.join();
        }
        let config = if role == DeviceRole::Wheel {
            &self.preferences.wheel
        } else {
            &self.preferences.pedals
        };
        if (config.enabled && config.device.is_some()) || role == DeviceRole::Wheel {
            *slot = Some(ReaderWorker::start_monitor(
                self.factory.clone(),
                self.shared.clone(),
                role,
                if config.enabled {
                    config.device.clone()
                } else {
                    None
                },
                config.axes.clone(),
                self.native_window,
            ));
        }
        if discarded > 0
            && let Some(recorder) = &mut self.recorder
        {
            let _ = recorder.gap(discarded, Instant::now(), unix_ms_now());
        }
    }

    fn start_usb(&mut self) {
        if !self.status.running {
            return;
        }
        let Some(recorder) = &self.recorder else {
            return;
        };
        if !self.usb.safe_to_export() {
            self.fail("An earlier USB operation or cleanup is still active.".into());
            return;
        }
        #[cfg(windows)]
        if let Err(error) = crate::win::wheel_usb::prepare_directory(&recorder.directory) {
            self.fail(format!("USB trace unavailable: {error}"));
            return;
        }
        self.usb_requested = true;
        if let Err(error) = self.usb.start(&recorder.directory) {
            self.status.message = format!("USB trace unavailable: {error}");
        }
        self.status.usb = self.usb.status();
        self.publish();
    }

    fn enable(&mut self, device: WheelDevice, axis: u8, window: isize) {
        if axis > 5 || window == 0 {
            self.fail(
                "Select a controller and its steering axis in the native diagnostics window."
                    .into(),
            );
            return;
        }
        self.enable_capture(Some(device), axis, window);
    }

    fn enable_capture(&mut self, device: Option<WheelDevice>, axis: u8, window: isize) {
        if self.status.report_pending {
            self.fail(
                "Wait for the pending incident report before starting another recording.".into(),
            );
            return;
        }
        self.poll_reader();
        if self.status.running {
            self.stop_reader(Duration::from_secs(3));
        }
        if self.reader.is_some()
            || self.pedal_reader.is_some()
            || self.enumeration.is_some()
            || !self.usb.safe_to_export()
        {
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
        let capture_device = device.clone().unwrap_or(WheelDevice {
            id: "sdk-only".into(),
            name: "No physical wheel selected (SDK + USB only)".into(),
            product_guid: None,
        });
        match Recorder::start(&self.root, &capture_device, axis, SEGMENT_BUDGET) {
            Ok(recorder) => {
                if let Some(device) = &device {
                    self.preferences.device = Some(device.clone());
                    self.preferences.axis = axis;
                    self.preferences.wheel.device = Some(device.clone());
                    self.preferences.wheel.axes = vec![axis];
                }
                self.save_preferences();
                self.recorder = Some(recorder);
                self.usb_requested = false;
                self.recovering = false;
                self.export_blocked = false;
                *self.shared.sdk.lock().unwrap() = None;
                self.shared.observing.store(true, Ordering::Release);
                self.reader = Some(ReaderWorker::start(
                    self.factory.clone(),
                    self.shared.clone(),
                    device.clone(),
                    axis,
                    window,
                ));
                self.native_window = window;
                self.status.wheel = DeviceMonitorStatus::new(self.preferences.wheel.clone());
                self.status.pedals = DeviceMonitorStatus::new(self.preferences.pedals.clone());
                if self.preferences.pedals.enabled && self.preferences.pedals.device.is_some() {
                    self.pedal_reader = Some(ReaderWorker::start_monitor(
                        self.factory.clone(),
                        self.shared.clone(),
                        DeviceRole::Pedals,
                        self.preferences.pedals.device.clone(),
                        self.preferences.pedals.axes.clone(),
                        window,
                    ));
                }
                self.status.running = true;
                self.status.has_capture = true;
                self.status.availability = "Starting".into();
                self.status.raw_axis = None;
                self.status.incidents = 0;
                self.status.selected_device_name = device.as_ref().map(|d| d.name.clone());
                self.status.selected_axis = axis;
                self.status.can_export = false;
                self.status.message =
                    "Monitoring wheel, pedals and SDK. Input interruptions are saved automatically; report an incident to save the preceding five minutes.".into();
                self.status.retained_captures = capture_history(&self.root).unwrap_or_default();
            }
            Err(error) => {
                self.status.message =
                    format!("Capture could not start: {error}. Check the local diagnostics folder.")
            }
        }
        self.status.busy = false;
        self.publish();
        if self.status.running && self.preferences.auto_usb {
            self.start_usb();
        }
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
            self.status.busy = self.status.report_pending;
            if let Some(window) = self.startup_window.take()
                && self.preferences.auto_record
            {
                let selected = if self.preferences.wheel.enabled {
                    self.preferences.wheel.device.clone().or_else(|| {
                        let candidates: Vec<_> = self
                            .status
                            .devices
                            .iter()
                            .filter(|d| {
                                let name = d.name.to_lowercase();
                                !name.contains("pedal")
                                    && ["wheel", "steering", "simucube"]
                                        .iter()
                                        .any(|term| name.contains(term))
                            })
                            .collect();
                        (candidates.len() == 1).then(|| candidates[0].clone())
                    })
                } else {
                    None
                };
                if self.preferences.pedals.device.is_none() {
                    let pedals: Vec<_> = self
                        .status
                        .devices
                        .iter()
                        .filter(|d| d.name.to_lowercase().contains("pedal"))
                        .collect();
                    if pedals.len() == 1 {
                        self.preferences.pedals.device = Some(pedals[0].clone());
                    }
                }
                self.enable_capture(selected, self.preferences.axis, window);
            }
            self.publish();
        }
    }

    fn stop_with_failure(&mut self, message: String) {
        if let Some(reader) = &self.reader {
            reader.cancel.store(true, Ordering::Release);
        }
        if let Some(reader) = &self.pedal_reader {
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
        let mut ended = false;
        for reader in [&mut self.reader, &mut self.pedal_reader]
            .into_iter()
            .flatten()
        {
            let config = if reader.role == DeviceRole::Wheel {
                &self.preferences.wheel
            } else {
                &self.preferences.pedals
            };
            let expected_id = if config.enabled {
                config.device.as_ref().map(|d| d.id.clone())
            } else {
                None
            };
            let same_source = reader.device_id == expected_id;
            while let Ok(event) = reader.events.try_recv() {
                events.push((reader.role, same_source, event));
            }
            let hung = reader
                .read_started
                .lock()
                .unwrap()
                .is_some_and(|start| start.elapsed() > Duration::from_secs(1));
            if hung && !reader.timed_out {
                reader.timed_out = true;
                reader.cancel.store(true, Ordering::Release);
                let monitor = if reader.role == DeviceRole::Wheel {
                    &mut self.status.wheel
                } else {
                    &mut self.status.pedals
                };
                monitor.availability = "Driver read stalled; no fresh device data".into();
                self.status.message = format!(
                    "{:?} read exceeded one second. Other monitoring continues; no cause inferred.",
                    reader.role
                );
                if let Some(recorder) = &mut self.recorder {
                    recorder.failure = Some(self.status.message.clone());
                }
            }
        }
        let drained_at = Instant::now();
        events.sort_by_key(|(_, _, event)| match event {
            ReaderEvent::Sample(_, at, ..) | ReaderEvent::Gap(_, at, _) => *at,
            ReaderEvent::Failed(_) => drained_at,
        });
        for (role, same_source, event) in events {
            match event {
                ReaderEvent::Sample(reading, now, utc, sdk, dropped, axes) => {
                    if !same_source {
                        if let Some(recorder) = &mut self.recorder {
                            let _ = recorder.gap(dropped + 1, now, utc);
                        }
                        continue;
                    }
                    let monitor = if role == DeviceRole::Wheel {
                        &mut self.status.wheel
                    } else {
                        &mut self.status.pedals
                    };
                    monitor.availability = if monitor.config.enabled {
                        reading.availability.clone()
                    } else {
                        "Disabled".into()
                    };
                    monitor.axes = if monitor.config.enabled {
                        axes.clone()
                    } else {
                        Vec::new()
                    };
                    if monitor.config.enabled && reading.availability == "Available" {
                        monitor.sample_at = Some(now);
                        monitor.last_sample_utc = Some(iso_utc(utc));
                    }
                    if let Some(recorder) = &mut self.recorder {
                        match recorder.gap(dropped, now, utc).and_then(|_| {
                            recorder.sample_role(reading.clone(), now, utc, sdk, role, axes)
                        }) {
                            Ok(()) => {
                                if role == DeviceRole::Wheel {
                                    self.status.availability = reading.availability;
                                    self.status.raw_axis = reading.axis;
                                }
                                self.status.incidents = recorder.incidents.len();
                            }
                            Err(error) => self.stop_with_failure(format!(
                                "Capture stopped (storage): {error}; no cause inferred."
                            )),
                        }
                    }
                }
                ReaderEvent::Gap(dropped, now, utc) => {
                    if let Some(recorder) = &mut self.recorder
                        && let Err(error) = recorder.gap(dropped, now, utc)
                    {
                        self.stop_with_failure(format!("Capture gap could not be saved: {error}"));
                    }
                }
                ReaderEvent::Failed(error) => {
                    let monitor = if role == DeviceRole::Wheel {
                        &mut self.status.wheel
                    } else {
                        &mut self.status.pedals
                    };
                    monitor.availability = format!("Device read failed: {error}");
                    self.status.message = format!("{role:?}: {error}. Other monitoring continues.");
                    if let Some(recorder) = &mut self.recorder {
                        recorder.failure = Some(self.status.message.clone());
                    }
                }
            }
        }
        for (role, slot) in [
            (DeviceRole::Wheel, &mut self.reader),
            (DeviceRole::Pedals, &mut self.pedal_reader),
        ] {
            if slot
                .as_ref()
                .is_some_and(|reader| reader.join.is_finished())
            {
                ended = true;
                let reader = slot.take().unwrap();
                if reader.join.join().is_err() {
                    self.status.message =
                        format!("{role:?} reader failed; other monitoring continues.");
                }
                let config = if role == DeviceRole::Wheel {
                    &self.preferences.wheel
                } else {
                    &self.preferences.pedals
                };
                if self.status.running
                    && (role == DeviceRole::Wheel || (config.enabled && config.device.is_some()))
                {
                    // Recreate only after the old driver call has exited, with a bounded retry delay.
                    *slot = Some(ReaderWorker::start_monitor_after(
                        self.factory.clone(),
                        self.shared.clone(),
                        role,
                        if config.enabled {
                            config.device.clone()
                        } else {
                            None
                        },
                        config.axes.clone(),
                        self.native_window,
                        Duration::from_secs(2),
                    ));
                }
            }
        }
        if ended && !self.status.running && self.reader.is_none() && self.pedal_reader.is_none() {
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
        if let Some(reader) = &self.pedal_reader {
            reader.cancel.store(true, Ordering::Release);
        }
        self.status.running = false;
        self.shared.observing.store(false, Ordering::Release);
        let start = Instant::now();
        while (self.reader.is_some() || self.pedal_reader.is_some()) && start.elapsed() < timeout {
            self.poll_reader();
            if self.reader.is_some() || self.pedal_reader.is_some() {
                thread::sleep(Duration::from_millis(20));
            }
        }
        if self.reader.is_some() || self.pedal_reader.is_some() {
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
        // The input reader stays alive. Only ETL must be finalized before it can be copied.
        if self.usb_requested {
            self.persist_usb_stop();
            if let Err(error) = self.usb.stop() {
                self.status.message = format!("Export waiting for USB cleanup: {error}");
            }
            self.status.usb = self.usb.status();
        }
        if self.reader.as_ref().is_some_and(|r| r.timed_out) || !self.usb.safe_to_export() {
            self.export_blocked = true;
            self.status.message =
                "Export unavailable until the reader exits and USB cleanup is confirmed.".into();
        } else if let Some(recorder) = self.recorder.as_mut() {
            self.export_blocked = false;
            let result = recorder.export(&self.exports, &self.status);
            self.finish_export(result);
        } else {
            self.status.message = "Enable the recorder before exporting a capture.".into();
        }
        self.status.can_export =
            self.recorder.is_some() && self.reader.is_none() && self.usb.safe_to_export();
        self.status.busy = false;
        self.publish();
    }

    fn export_capture(&mut self, id: &str) {
        if self
            .recorder
            .as_ref()
            .is_some_and(|r| r.directory.file_name().and_then(|x| x.to_str()) == Some(id))
        {
            self.export();
            return;
        }
        if !is_guid(id) {
            self.fail("Saved capture identifier is invalid.".into());
            return;
        }
        let directory = self.root.join(id);
        if !capture_safe_to_export(&directory) {
            self.fail("Saved capture needs interrupted USB recovery before export.".into());
            return;
        }
        self.status.busy = true;
        self.publish();
        let old_status =
            read_json_bounded::<serde_json::Value>(&directory.join("manifest.json")).ok();
        let failure = old_status
            .as_ref()
            .and_then(|v| v.get("CaptureFailure"))
            .and_then(|v| v.as_str());
        let mut status = WheelStatus {
            message: "Retained capture".into(),
            has_capture: true,
            usb: "See retained usb-status.json".into(),
            ..WheelStatus::default()
        };
        if let Some(summary) = self.status.retained_captures.iter().find(|s| s.id == id) {
            status.sample_count = summary.sample_count;
            status.capture_started_utc = Some(summary.started_utc.clone());
            status.last_sample_utc = summary.last_sample_utc.clone();
            status.incidents = summary.incidents;
        }
        let result = export_directory(&self.root, &directory, &self.exports, &status, failure);
        self.finish_export(result);
        self.status.busy = false;
        self.publish();
    }

    fn finish_export(&mut self, result: io::Result<PathBuf>) {
        match result {
            Ok(path) => {
                let bytes = safe_file_metadata(&path).map(|m| m.len()).unwrap_or(0);
                let utc = iso_utc(unix_ms_now());
                let receipt = ExportReceipt {
                    path: path.clone(),
                    bytes,
                    utc: utc.clone(),
                };
                let saved = write_json(&self.root, "last-export.json", &receipt);
                self.status.export_path = Some(path);
                self.status.export_bytes = Some(bytes);
                self.status.exported_utc = Some(utc);
                self.status.message = if self.status.running {
                    "ZIP verified and saved. Input recording continues. USB trace ended for export; start USB again to resume it."
                } else { "ZIP verified and saved locally." }.into();
                if let Err(error) = saved {
                    self.status
                        .message
                        .push_str(&format!(" Export receipt could not be saved: {error}"));
                }
                self.status.retained_captures = capture_history(&self.root).unwrap_or_default();
            }
            Err(error) => {
                self.status.message =
                    format!("Export failed: {error}. Check free space and local folder access.")
            }
        }
    }

    fn report_incident(&mut self, at: Instant, utc: u64) {
        if self.status.report_pending {
            return;
        }
        self.status.report_sequence = self.status.report_sequence.saturating_add(1);
        self.status.report_pending = true;
        self.status.report_error = None;
        self.status.busy = true;
        self.status.message = if self.status.running {
            "Saving the preceding five minutes. Monitoring continues."
        } else {
            "Saving the preceding five minutes from retained samples."
        }
        .into();
        self.publish();
        let requested_status = self.shared.status.lock().unwrap().clone();
        if self.usb_requested && !self.usb.is_pending() {
            match self.usb.snapshot() {
                Ok(reply) => {
                    self.pending_report = Some(PendingReport {
                        at,
                        utc,
                        requested: Instant::now(),
                        reply,
                        directory: self
                            .recorder
                            .as_ref()
                            .map(|r| r.directory.clone())
                            .unwrap_or_default(),
                        status: requested_status,
                    });
                    return;
                }
                Err(error) => {
                    self.start_report_archive(at, utc, None, Some(&error), requested_status);
                    return;
                }
            }
        }
        self.start_report_archive(at, utc, None, Some("No finalized USB snapshot was available; USB may be off or awaiting an administrator prompt."), requested_status);
    }

    fn poll_report(&mut self) {
        self.poll_usb_report();
        let result =
            self.pending_archive
                .as_ref()
                .and_then(|archive| match archive.reply.try_recv() {
                    Ok(result) => Some(result),
                    Err(mpsc::TryRecvError::Disconnected) => Some(Err(io::Error::other(
                        "Incident archive worker ended before confirmation",
                    ))),
                    Err(mpsc::TryRecvError::Empty) => None,
                });
        if let Some(result) = result {
            self.pending_archive = None;
            self.finish_report(result);
        }
    }

    fn poll_usb_report(&mut self) {
        let Some(pending) = &self.pending_report else {
            return;
        };
        let result = match pending.reply.try_recv() {
            Ok(result) => Some(result),
            Err(mpsc::TryRecvError::Disconnected) => {
                Some(Err("USB snapshot worker ended before confirmation".into()))
            }
            Err(mpsc::TryRecvError::Empty)
                if pending.requested.elapsed() > Duration::from_secs(20) =>
            {
                Some(Err(
                    "USB snapshot confirmation timed out; no live ETL was copied".into(),
                ))
            }
            Err(mpsc::TryRecvError::Empty) => None,
        };
        if let Some(result) = result {
            let pending = self.pending_report.take().unwrap();
            if self
                .recorder
                .as_ref()
                .is_none_or(|r| r.directory != pending.directory)
            {
                self.finish_report(Err(io::Error::other("The original capture is no longer active. Any USB snapshot remains in its original capture folder.")));
                return;
            }
            match result {
                Ok(snapshot) => self.start_report_archive(
                    pending.at,
                    pending.utc,
                    snapshot,
                    None,
                    pending.status,
                ),
                Err(error) => self.start_report_archive(
                    pending.at,
                    pending.utc,
                    None,
                    Some(&error),
                    pending.status,
                ),
            }
        }
    }

    fn start_report_archive(
        &mut self,
        at: Instant,
        utc: u64,
        snapshot: Option<UsbIncidentSnapshot>,
        usb_error: Option<&str>,
        requested_status: WheelStatus,
    ) {
        let prepared = match self.recorder.as_mut() {
            Some(recorder) => recorder.prepare_report(
                &self.exports,
                &requested_status,
                at,
                utc,
                snapshot,
                usb_error,
            ),
            None => Err(io::Error::other(
                "No recorded samples are available for an incident report",
            )),
        };
        let prepared = match prepared {
            Ok(prepared) => prepared,
            Err(error) => {
                self.finish_report(Err(error));
                return;
            }
        };
        let (sender, reply) = mpsc::sync_channel(1);
        let writer = self.archive_writer.clone();
        match thread::Builder::new()
            .name("wheel-incident-archive".into())
            .spawn(move || {
                #[cfg(windows)]
                crate::win::enter_background_mode();
                let _ = sender.send(writer(prepared));
            }) {
            Ok(_) => {
                self.pending_archive = Some(PendingArchive { reply });
            }
            Err(error) => self.finish_report(Err(io::Error::other(format!(
                "Could not start incident archive worker: {error}"
            )))),
        }
    }

    fn finish_report(&mut self, result: io::Result<ReportSaved>) {
        match result {
            Ok(saved) => {
                self.status.export_path = Some(saved.receipt.path);
                self.status.export_bytes = Some(saved.receipt.bytes);
                self.status.exported_utc = Some(saved.receipt.utc);
                self.status.message = if saved.usb_included {
                    "Incident ZIP verified and saved with USB data."
                } else {
                    "Incident ZIP verified and saved; USB data was unavailable."
                }
                .into();
                if let Some(warning) = saved.warning {
                    self.status.message.push_str(&format!(" {warning}"));
                }
            }
            Err(error) => {
                let message = format!("Incident report failed: {error}");
                self.status.report_error = Some(message.clone());
                self.status.message = message;
            }
        }
        self.status.report_pending = false;
        self.status.busy = false;
        self.shared.report_admitted.store(false, Ordering::Release);
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
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub dropped_samples: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub device_role: Option<DeviceRole>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub axes: Vec<AxisStatus>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "PascalCase")]
pub struct WheelIncident {
    pub id: String,
    pub reason: String,
    pub marked_ms: f64,
    pub post_window_complete: bool,
    pub records: Vec<WheelDiagnosticRecord>,
    #[serde(default)]
    pub dropped_samples: u64,
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
    segment_minute: Option<u64>,
    segment_budget: u64,
    previous: String,
    previous_pedals: String,
    last_auto_mark: Option<f64>,
    failure: Option<String>,
    wheel_failure: Option<String>,
    pedal_failure: Option<String>,
    started_utc: String,
    sample_count: u64,
    last_sample_utc: Option<String>,
    last_sample_at: Option<Instant>,
    segment_starts: VecDeque<String>,
    dropped_samples: u64,
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
        let started_utc = iso_utc(unix_ms_now());
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
                "StartUtc": started_utc, "StartTicks": 0, "HistorySeconds": 60, "DiskHistorySeconds": RETAINED_MINUTES * 60,
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
            segment_minute: None,
            segment_budget,
            previous: "Starting".into(),
            previous_pedals: "Starting".into(),
            last_auto_mark: None,
            failure: None,
            wheel_failure: None,
            pedal_failure: None,
            started_utc,
            sample_count: 0,
            last_sample_utc: None,
            last_sample_at: None,
            segment_starts: VecDeque::new(),
            dropped_samples: 0,
        })
    }

    #[cfg(test)]
    fn sample(
        &mut self,
        reading: WheelReading,
        now: Instant,
        utc: u64,
        sdk: Option<SdkObservation>,
    ) -> io::Result<()> {
        let axes = vec![AxisStatus {
            axis: 0,
            value: reading.axis,
            availability: reading.availability.clone(),
            native_error: reading.native_error.clone(),
        }];
        self.sample_role(reading, now, utc, sdk, DeviceRole::Wheel, axes)
    }

    fn sample_role(
        &mut self,
        reading: WheelReading,
        now: Instant,
        utc: u64,
        sdk: Option<SdkObservation>,
        role: DeviceRole,
        axes: Vec<AxisStatus>,
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
            dropped_samples: None,
            device_role: (!axes.is_empty()).then_some(role),
            axes,
        };
        self.history.push_back(record.clone());
        while self
            .history
            .iter()
            .filter(|r| r.device_role == record.device_role)
            .count()
            > HISTORY_SAMPLES
        {
            if let Some(index) = self
                .history
                .iter()
                .position(|r| r.device_role == record.device_role)
            {
                self.history.remove(index);
            }
        }
        self.write_record(&record)?;
        self.sample_count += 1;
        self.last_sample_utc = Some(record.utc.clone());
        self.last_sample_at = Some(now);
        let failure = if role == DeviceRole::Wheel {
            &mut self.wheel_failure
        } else {
            &mut self.pedal_failure
        };
        if !record.axes.is_empty() && reading.availability != "Available" {
            *failure = Some(format!(
                "{role:?} unavailable: {}. SDK and USB capture continue.",
                reading
                    .native_error
                    .as_deref()
                    .unwrap_or("unknown driver error")
            ));
        } else if reading.availability == "Available" {
            *failure = None;
        }
        // Incident post windows are bounded even when driver timing or a hung call is irregular.
        for incident in &mut self.incidents {
            if !incident.post_window_complete && record.elapsed_ms >= incident.marked_ms {
                if record.elapsed_ms <= incident.marked_ms + 20_000.0 {
                    if incident.records.len() < HISTORY_SAMPLES + 2
                        && incident
                            .records
                            .last()
                            .is_none_or(|x| x.monotonic_ticks != record.monotonic_ticks)
                    {
                        incident.records.push(record.clone());
                    }
                } else {
                    incident.post_window_complete = incident.dropped_samples == 0;
                }
            }
        }
        let previous = if role == DeviceRole::Wheel {
            &self.previous
        } else {
            &self.previous_pedals
        };
        if !record.axes.is_empty()
            && previous == "Available"
            && reading.availability != "Available"
            && self
                .last_auto_mark
                .is_none_or(|mark| record.elapsed_ms - mark > 30_000.0)
        {
            self.mark(
                &format!("{role:?} input availability changed"),
                record.elapsed_ms,
                utc,
            )?;
            self.last_auto_mark = Some(record.elapsed_ms);
        }
        if role == DeviceRole::Wheel {
            self.previous = reading.availability;
        } else {
            self.previous_pedals = reading.availability;
        }
        Ok(())
    }

    fn gap(&mut self, dropped: u64, now: Instant, utc: u64) -> io::Result<()> {
        if dropped == 0 {
            return Ok(());
        }
        self.dropped_samples = self.dropped_samples.saturating_add(dropped);
        for incident in &mut self.incidents {
            if !incident.post_window_complete {
                incident.dropped_samples = incident.dropped_samples.saturating_add(dropped);
            }
        }
        let elapsed = now.saturating_duration_since(self.start);
        let gap = WheelDiagnosticRecord {
            utc: iso_utc(utc),
            monotonic_ticks: elapsed.as_nanos().min(u64::MAX as u128) as u64,
            elapsed_ms: elapsed.as_secs_f64() * 1000.0,
            kind: "capture_gap".into(),
            availability: "Capture gap; input state unknown".into(),
            raw_axis: None,
            native_error: Some(format!(
                "Recorder queue was full; {dropped} samples were not saved"
            )),
            sdk_steering_radians: None,
            sdk_utc: None,
            sdk_age_ms: None,
            sdk_connected: false,
            incident_id: None,
            dropped_samples: Some(dropped),
            device_role: None,
            axes: Vec::new(),
        };
        self.write_record(&gap)?;
        self.history.push_back(gap.clone());
        while self.history.len() > HISTORY_SAMPLES {
            self.history.pop_front();
        }
        self.mark(
            "Recorder history gap; no disconnect inferred",
            gap.elapsed_ms,
            utc,
        )
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
        marker.axes.clear();
        marker.dropped_samples = None;
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
                .take(HISTORY_SAMPLES + 2)
                .cloned()
                .collect(),
            dropped_samples: self
                .history
                .iter()
                .filter(|x| {
                    x.elapsed_ms >= marked_ms - 10_000.0 && x.elapsed_ms <= marked_ms + 20_000.0
                })
                .filter_map(|x| x.dropped_samples)
                .sum(),
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
        let minute =
            ((record.elapsed_ms.max(0.0) / 60_000.0) as u64).max(self.segment_minute.unwrap_or(0));
        if self.segment.is_none()
            || self.segment_minute != Some(minute)
            || self.segment_bytes + bytes.len() as u64 > self.segment_budget
        {
            self.close_segment();
            let path = self
                .directory
                .join(format!("samples-{:06}.ndjson", self.segment_index));
            self.segment_index += 1;
            self.segment = Some(OpenOptions::new().create_new(true).write(true).open(path)?);
            self.segment_bytes = 0;
            self.segment_minute = Some(minute);
            self.segment_starts.push_back(record.utc.clone());
            while self.segment_starts.len() > MAX_SAMPLE_SEGMENTS {
                self.segment_starts.pop_front();
            }
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
            for old in segments
                .iter()
                .take(segments.len().saturating_sub(MAX_SAMPLE_SEGMENTS))
            {
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
        write_json(
            &self.directory,
            "capture-summary.json",
            &json!({
                "StartedUtc": self.started_utc, "LastSampleUtc": self.last_sample_utc,
                "SampleCount": self.sample_count, "Incidents": self.incidents.len(),
                "DroppedSamples": self.dropped_samples, "CaptureFailure": self.failure_text(),
            }),
        )?;
        Ok(())
    }

    fn close_segment(&mut self) {
        self.segment.take();
    }

    fn export(&mut self, exports: &Path, status: &WheelStatus) -> io::Result<PathBuf> {
        self.flush()?;
        self.close_segment();
        let failure = self.failure_text();
        export_directory(
            &self.root,
            &self.directory,
            exports,
            status,
            failure.as_deref(),
        )
    }

    fn failure_text(&self) -> Option<String> {
        let failures: Vec<_> = [&self.failure, &self.wheel_failure, &self.pedal_failure]
            .into_iter()
            .flatten()
            .cloned()
            .collect();
        (!failures.is_empty()).then(|| failures.join("; "))
    }

    #[cfg(test)]
    fn report(
        &mut self,
        exports: &Path,
        status: &WheelStatus,
        at: Instant,
        utc: u64,
        usb_snapshot: Option<&UsbIncidentSnapshot>,
        usb_error: Option<&str>,
    ) -> io::Result<PathBuf> {
        self.prepare_report(exports, status, at, utc, usb_snapshot.cloned(), usb_error)?
            .save()
            .map(|saved| saved.receipt.path)
    }

    fn prepare_report(
        &mut self,
        exports: &Path,
        status: &WheelStatus,
        at: Instant,
        utc: u64,
        usb_snapshot: Option<UsbIncidentSnapshot>,
        usb_error: Option<&str>,
    ) -> io::Result<PreparedReport> {
        // Seal the current segment, then retain handles to the immutable files. Archiving can
        // read them independently even when the live ring rotates and removes their names.
        if let Some(segment) = &mut self.segment {
            segment.flush()?;
        }
        self.close_segment();
        let end_ms = at.saturating_duration_since(self.start).as_secs_f64() * 1000.0;
        let mut segments: Vec<_> = fs::read_dir(&self.directory)?
            .filter_map(Result::ok)
            .map(|e| e.path())
            .filter(|p| {
                p.file_name()
                    .and_then(|n| n.to_str())
                    .is_some_and(is_sample_name)
            })
            .collect();
        segments.sort();
        let mut inputs = Vec::new();
        for path in segments {
            let bytes = safe_file_metadata(&path)?.len();
            if bytes > SEGMENT_BUDGET {
                return Err(io::Error::other("sample segment exceeds its budget"));
            }
            inputs.push(ReportSegment {
                input: File::open(path)?,
                bytes,
            });
        }
        Ok(PreparedReport {
            root: self.root.clone(),
            directory: self.directory.clone(),
            exports: exports.to_path_buf(),
            metadata: read_json_bounded(&self.directory.join("metadata.json"))?,
            status: status.clone(),
            end_ms,
            utc,
            segments: inputs,
            failure: self.failure_text(),
            usb_snapshot,
            usb_error: usb_error.map(str::to_owned),
        })
    }
}

struct ReportSegment {
    input: File,
    bytes: u64,
}

struct PreparedReport {
    root: PathBuf,
    directory: PathBuf,
    exports: PathBuf,
    metadata: serde_json::Value,
    status: WheelStatus,
    end_ms: f64,
    utc: u64,
    segments: Vec<ReportSegment>,
    failure: Option<String>,
    usb_snapshot: Option<UsbIncidentSnapshot>,
    usb_error: Option<String>,
}

impl PreparedReport {
    fn save(mut self) -> io::Result<ReportSaved> {
        let exports = self.exports.as_path();
        let status = &self.status;
        let usb_snapshot = self.usb_snapshot.as_ref();
        let usb_error = self.usb_error.as_deref();
        let end_ms = self.end_ms;
        let utc = self.utc;
        let start_ms = (end_ms - 300_000.0).max(0.0);
        let mut records = Vec::new();
        for segment in &mut self.segments {
            let mut text = String::new();
            Read::by_ref(&mut segment.input)
                .take(segment.bytes + 1)
                .read_to_string(&mut text)?;
            if text.len() as u64 != segment.bytes {
                return Err(io::Error::other(
                    "sealed sample segment changed during export",
                ));
            }
            for line in text.lines() {
                let record: WheelDiagnosticRecord = serde_json::from_str(line)?;
                if record.elapsed_ms >= start_ms && record.elapsed_ms <= end_ms {
                    records.push(record);
                }
            }
        }
        records.sort_by(|a, b| a.elapsed_ms.total_cmp(&b.elapsed_ms));
        let samples: Vec<_> = records.iter().filter(|r| r.kind == "sample").collect();
        let Some(first) = samples.first() else {
            return Err(io::Error::other(
                "No samples fall within the preceding five minutes",
            ));
        };
        let last = samples.last().unwrap();
        let dropped: u64 = records.iter().filter_map(|r| r.dropped_samples).sum();
        let report = json!({
            "SchemaVersion": 1, "Kind": "ManualDiagnosticIncident", "RequestedAtUtc": iso_utc(utc),
            "RequestedWindowSeconds": 300, "SavedStartUtc": first.utc, "SavedEndUtc": last.utc,
            "SavedSpanSeconds": (last.elapsed_ms - first.elapsed_ms) / 1000.0,
            "SampleCount": samples.len(), "DroppedSamples": dropped,
            "FullFiveMinutesAvailable": end_ms >= 300_000.0 && first.elapsed_ms <= start_ms + 200.0 && end_ms - last.elapsed_ms <= 200.0 && dropped == 0,
            "UsbTraceIncluded": usb_snapshot.is_some(), "UsbTraceStatus": status.usb,
            "UsbSnapshot": usb_snapshot.map(|s| json!({ "RequestedUtc": s.requested_utc, "CompletedUtc": s.completed_utc, "TraceRestarted": s.trace_restarted, "TraceStoppedUtc": s.trace_stopped_utc, "TraceRestartedUtc": s.trace_restarted_utc, "InterruptionMs": s.interruption_ms, "Limitations": s.limitations })),
            "UsbTraceOmissionReason": if usb_snapshot.is_none() { Some(usb_error.unwrap_or("No USB trace was running")) } else { None },
            "CaptureFailure": self.failure,
        });
        make_safe_directory(exports)?;
        let staging = exports.join(format!("incident-staging-{}", uuid_v4().replace('-', "")));
        fs::create_dir(&staging)?;
        let result = (|| {
            write_json(&staging, "metadata.json", &self.metadata)?;
            write_json(&staging, "report.json", &report)?;
            if let Some(snapshot) = usb_snapshot {
                if snapshot.path.parent() != Some(self.directory.as_path())
                    || !snapshot
                        .path
                        .file_name()
                        .and_then(|n| n.to_str())
                        .is_some_and(|n| n.starts_with("usb-snapshot-") && is_export_name(n))
                    || safe_file_metadata(&snapshot.path)?.len() > 36 * 1024 * 1024
                {
                    return Err(io::Error::other("USB snapshot path or size is invalid"));
                }
                let mut input = File::open(&snapshot.path)?;
                let mut output = OpenOptions::new()
                    .create_new(true)
                    .write(true)
                    .open(staging.join("usb-incident.etl"))?;
                io::copy(&mut input, &mut output)?;
                output.sync_all()?;
            }
            write_json(
                &staging,
                "capture-summary.json",
                &json!({
                    "StartedUtc": first.utc, "LastSampleUtc": last.utc, "SampleCount": samples.len(),
                    "Incidents": 0, "DroppedSamples": dropped, "CaptureFailure": self.failure,
                }),
            )?;
            let mut output = OpenOptions::new()
                .create_new(true)
                .write(true)
                .open(staging.join("samples-000000.ndjson"))?;
            for record in &records {
                serde_json::to_writer(&mut output, record)?;
                output.write_all(b"\n")?;
            }
            output.sync_all()?;
            let mut report_status = status.clone();
            report_status.message =
                "Manual incident report; see report.json for actual coverage and USB omission"
                    .into();
            export_directory(
                &self.root,
                &staging,
                exports,
                &report_status,
                self.failure.as_deref(),
            )
        })();
        // Only the private staging directory and files just authored above are removed.
        if staging.parent() == Some(exports) {
            if let Ok(entries) = fs::read_dir(&staging) {
                for entry in entries.flatten() {
                    let path = entry.path();
                    if path
                        .file_name()
                        .and_then(|n| n.to_str())
                        .is_some_and(is_export_name)
                    {
                        let _ = remove_owned_file(&path);
                    }
                }
            }
            let _ = fs::remove_dir(&staging);
        }
        let path = result?;
        let receipt = ExportReceipt {
            bytes: safe_file_metadata(&path)?.len(),
            path,
            utc: iso_utc(unix_ms_now()),
        };
        let mut warnings = Vec::new();
        if let Err(error) = write_json(&self.root, "last-export.json", &receipt) {
            warnings.push(format!("Export receipt could not be saved: {error}"));
        }
        if let Some(snapshot) = usb_snapshot
            && let Err(error) = remove_owned_file(&snapshot.path)
        {
            warnings.push(format!(
                "USB snapshot remains at {}: {error}",
                snapshot.path.display()
            ));
        }
        Ok(ReportSaved {
            receipt,
            warning: (!warnings.is_empty()).then(|| warnings.join(" ")),
            usb_included: usb_snapshot.is_some(),
        })
    }
}

fn export_directory(
    root: &Path,
    directory: &Path,
    exports: &Path,
    status: &WheelStatus,
    failure: Option<&str>,
) -> io::Result<PathBuf> {
    safe_directory(directory)?;
    if !capture_safe_to_export(directory) {
        return Err(io::Error::other("USB cleanup is not confirmed"));
    }
    make_safe_directory(exports)?;
    let mut status = status.clone();
    status.export_path = None;
    // Enumerated controllers are UI state, not capture evidence. Never export other device
    // identifiers (the selected instance id is represented only by its salted DeviceKey).
    status.devices.clear();
    status.busy = false;
    status.report_pending = false;
    status.report_error = None;
    for monitor in [&mut status.wheel, &mut status.pedals] {
        if let Some(device) = &mut monitor.config.device {
            device.id.clear();
        }
    }
    status.retained_captures.clear();
    status.capture_directory = None;
    status.exports_directory = None;
    if let Ok(summary) =
        read_json_bounded::<serde_json::Value>(&directory.join("capture-summary.json"))
    {
        status.sample_count = summary
            .get("SampleCount")
            .and_then(|v| v.as_u64())
            .unwrap_or(0);
        status.capture_started_utc = summary
            .get("StartedUtc")
            .and_then(|v| v.as_str())
            .map(str::to_owned);
        status.last_sample_utc = summary
            .get("LastSampleUtc")
            .and_then(|v| v.as_str())
            .map(str::to_owned);
        status.dropped_samples = summary
            .get("DroppedSamples")
            .and_then(|v| v.as_u64())
            .unwrap_or(0);
        status.incidents = summary
            .get("Incidents")
            .and_then(|v| v.as_u64())
            .unwrap_or(0) as usize;
    }
    status.exported_utc = None;
    status.export_bytes = None;
    if let Ok(metadata) = read_json_bounded::<serde_json::Value>(&directory.join("metadata.json")) {
        status.selected_device_name = metadata
            .get("Device")
            .and_then(|v| v.as_str())
            .map(str::to_owned);
        status.selected_axis = metadata.get("Axis").and_then(|v| v.as_u64()).unwrap_or(0) as u8;
    }
    write_json(
        directory,
        "manifest.json",
        &json!({
            "SchemaVersion": 1, "ExportUtc": iso_utc(unix_ms_now()), "CaptureFailure": failure,
            "Status": status, "RootBudget": ROOT_BUDGET, "Limitations": LIMITATIONS,
        }),
    )?;
    // Exporting is read-only with respect to other retained captures.
    ensure_budget(root)?;
    let mut files = Vec::new();
    let mut size = 0_u64;
    for entry in fs::read_dir(directory)? {
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
        return Err(io::Error::other("diagnostic bundle exceeds 96 MiB"));
    }
    if files.len() > MAX_SAMPLE_SEGMENTS + MAX_INCIDENTS + 16 {
        return Err(io::Error::other("too many diagnostic bundle entries"));
    }
    files.sort();
    let destination = exports.join(format!(
        "wheel-diagnostics-{}-{}.zip",
        unix_ms_now(),
        uuid_v4().replace('-', "")
    ));
    zip_stored(&destination, &files)?;
    verify_export(&destination)?;
    Ok(destination)
}

fn clean(input: &str, max: usize) -> String {
    input
        .chars()
        .filter(|x| !x.is_control())
        .take(max)
        .collect()
}

fn merge_unknown_fields(previous: &serde_json::Value, updated: &mut serde_json::Value) {
    if let (Some(previous), Some(updated)) = (previous.as_object(), updated.as_object_mut()) {
        for (key, old_value) in previous {
            match updated.get_mut(key) {
                Some(new_value) => merge_unknown_fields(old_value, new_value),
                None => {
                    updated.insert(key.clone(), old_value.clone());
                }
            }
        }
    }
}

fn read_json_bounded<T: serde::de::DeserializeOwned>(path: &Path) -> io::Result<T> {
    if let Some(parent) = path.parent() {
        safe_directory(parent)?;
    }
    if safe_file_metadata(path)?.len() > 64 * 1024 {
        return Err(io::Error::other("diagnostic JSON exceeds its size limit"));
    }
    serde_json::from_slice(&fs::read(path)?).map_err(io::Error::other)
}

fn capture_safe_to_export(directory: &Path) -> bool {
    if safe_directory(directory).is_err()
        || safe_file_metadata(&directory.join("metadata.json")).is_err()
    {
        return false;
    }
    if !directory.join("usb-owner.json").exists() {
        return true;
    }
    read_json_bounded::<serde_json::Value>(&directory.join("usb-status.json"))
        .ok()
        .and_then(|v| {
            v.get("State")
                .or_else(|| v.get("state"))
                .and_then(|s| s.as_str())
                .map(str::to_owned)
        })
        .is_some_and(|state| matches!(state.as_str(), "Stopped" | "Unavailable"))
}

fn capture_history(root: &Path) -> io::Result<Vec<WheelCaptureSummary>> {
    if !root.exists() {
        return Ok(Vec::new());
    }
    let mut history = Vec::new();
    for directory in session_directories(root)? {
        let Ok(metadata) = read_json_bounded::<serde_json::Value>(&directory.join("metadata.json"))
        else {
            continue;
        };
        let Some(started_utc) = metadata.get("StartUtc").and_then(|v| v.as_str()) else {
            continue;
        };
        let mut summary = WheelCaptureSummary {
            id: directory
                .file_name()
                .unwrap()
                .to_string_lossy()
                .into_owned(),
            directory: directory.clone(),
            started_utc: started_utc.into(),
            last_sample_utc: None,
            sample_count: 0,
            incidents: 0,
            can_export: capture_safe_to_export(&directory),
        };
        if let Ok(saved) =
            read_json_bounded::<serde_json::Value>(&directory.join("capture-summary.json"))
        {
            summary.last_sample_utc = saved
                .get("LastSampleUtc")
                .and_then(|v| v.as_str())
                .map(str::to_owned);
            summary.sample_count = saved
                .get("SampleCount")
                .and_then(|v| v.as_u64())
                .unwrap_or(0);
            summary.incidents =
                saved.get("Incidents").and_then(|v| v.as_u64()).unwrap_or(0) as usize;
        } else {
            // Pre-upgrade sessions have no summary. Recover coverage from their existing files.
            let mut segments = Vec::new();
            for entry in fs::read_dir(&directory)? {
                let path = entry?.path();
                let name = path.file_name().and_then(|n| n.to_str()).unwrap_or("");
                if is_sample_name(name) {
                    segments.push(path);
                } else if is_incident_name(name) {
                    summary.incidents += 1;
                }
            }
            segments.sort();
            for path in segments {
                if safe_file_metadata(&path)?.len() > SEGMENT_BUDGET {
                    continue;
                }
                for line in fs::read_to_string(path)?.lines() {
                    if let Ok(record) = serde_json::from_str::<WheelDiagnosticRecord>(line)
                        && record.kind == "sample"
                    {
                        summary.sample_count += 1;
                        summary.last_sample_utc = Some(record.utc);
                    }
                }
            }
        }
        history.push(summary);
    }
    history.sort_by(|a, b| b.started_utc.cmp(&a.started_utc).then(b.id.cmp(&a.id)));
    Ok(history)
}

fn verify_export(path: &Path) -> io::Result<()> {
    let size = safe_file_metadata(path)?.len();
    if !(22..=SESSION_RESERVE + 1024 * 1024).contains(&size) {
        return Err(io::Error::other("ZIP size verification failed"));
    }
    let data = fs::read(path)?;
    let invalid = || io::Error::other("ZIP contents verification failed");
    let number = |offset: usize, bytes: usize| -> io::Result<u32> {
        let value = data.get(offset..offset + bytes).ok_or_else(invalid)?;
        Ok(if bytes == 2 {
            u16::from_le_bytes(value.try_into().unwrap()) as u32
        } else {
            u32::from_le_bytes(value.try_into().unwrap())
        })
    };
    let mut offset = 0;
    let mut entries = 0;
    while number(offset, 4)? == 0x04034b50 {
        if number(offset + 8, 2)? != 0 {
            return Err(invalid());
        }
        let expected_crc = number(offset + 14, 4)?;
        let bytes = number(offset + 18, 4)? as usize;
        let name_len = number(offset + 26, 2)? as usize;
        let extra_len = number(offset + 28, 2)? as usize;
        let name = data
            .get(offset + 30..offset + 30 + name_len)
            .ok_or_else(invalid)?;
        if !std::str::from_utf8(name).is_ok_and(is_export_name) {
            return Err(invalid());
        }
        let start = offset + 30 + name_len + extra_len;
        let contents = data.get(start..start + bytes).ok_or_else(invalid)?;
        if crc32(contents) != expected_crc {
            return Err(invalid());
        }
        offset = start + bytes;
        entries += 1;
    }
    let footer = data.len() - 22;
    if entries == 0
        || number(offset, 4)? != 0x02014b50
        || number(footer, 4)? != 0x06054b50
        || number(footer + 10, 2)? != entries
        || number(footer + 16, 4)? as usize != offset
        || number(footer + 12, 4)? as usize != footer.saturating_sub(offset)
    {
        return Err(invalid());
    }
    Ok(())
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
    matches!(
        name,
        "metadata.json"
            | "manifest.json"
            | "usb-status.json"
            | "capture-summary.json"
            | "report.json"
    ) || is_sample_name(name)
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
                    && !matches!(
                        name,
                        "usb-owner.json"
                            | "usb-stop"
                            | "usb-heartbeat"
                            | "usb-snapshot-request.json"
                            | "usb-snapshot-result.json"
                            | "usb-snapshot-request.json.tmp"
                            | "usb-snapshot-result.json.tmp"
                    ))
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
    const TABLE: [u32; 256] = {
        let mut table = [0_u32; 256];
        let mut index = 0;
        while index < 256 {
            let mut entry = index as u32;
            let mut bit = 0;
            while bit < 8 {
                entry = (entry >> 1) ^ (0xedb88320_u32 & (0_u32.wrapping_sub(entry & 1)));
                bit += 1;
            }
            table[index] = entry;
            index += 1;
        }
        table
    };
    let mut crc = !0_u32;
    for byte in bytes {
        crc = (crc >> 8) ^ TABLE[((crc ^ *byte as u32) & 0xff) as usize];
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
        stop_delay: Duration,
        stops: Arc<AtomicUsize>,
        snapshots: Arc<AtomicUsize>,
        snapshot_enabled: bool,
        snapshot_delay: Duration,
        directory: Option<PathBuf>,
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
                stop_delay: Duration::ZERO,
                stops: Arc::new(AtomicUsize::new(0)),
                snapshots: Arc::new(AtomicUsize::new(0)),
                snapshot_enabled: false,
                snapshot_delay: Duration::ZERO,
                directory: None,
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
        fn start(&mut self, directory: &Path) -> Result<(), String> {
            self.directory = Some(directory.to_path_buf());
            self.starts.fetch_add(1, Ordering::Relaxed);
            thread::sleep(self.delay);
            Ok(())
        }
        fn heartbeat(&mut self) {}
        fn stop(&mut self) -> Result<(), String> {
            self.stops.fetch_add(1, Ordering::Relaxed);
            thread::sleep(self.stop_delay);
            Ok(())
        }
        fn snapshot(&mut self) -> Result<Option<UsbIncidentSnapshot>, String> {
            self.snapshots.fetch_add(1, Ordering::Relaxed);
            thread::sleep(self.snapshot_delay);
            if !self.snapshot_enabled {
                return Ok(None);
            }
            let path = self
                .directory
                .as_ref()
                .ok_or("No running fake USB trace")?
                .join(format!("usb-snapshot-{}.etl", uuid_v4().replace('-', "")));
            fs::write(&path, b"finalized synthetic USB snapshot").map_err(|e| e.to_string())?;
            Ok(Some(UsbIncidentSnapshot {
                path,
                requested_utc: iso_utc(unix_ms_now()),
                completed_utc: iso_utc(unix_ms_now()),
                trace_restarted: true,
                limitations: "Bounded circular trace may include earlier context".into(),
                trace_stopped_utc: None,
                trace_restarted_utc: None,
                interruption_ms: Some(1),
            }))
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
        handle_with_writer(
            folder,
            usb,
            select_error,
            hold,
            Arc::new(PreparedReport::save),
        )
    }

    fn handle_with_writer(
        folder: &TestFolder,
        usb: FakeUsb,
        select_error: bool,
        hold: Option<Arc<AtomicBool>>,
        writer: ArchiveWriter,
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
            WheelDiagnostics::with_archive_writer(
                folder.root(),
                folder.exports(),
                factory,
                Box::new(usb),
                writer,
            ),
            reads,
            value,
        )
    }
    fn enable(handle: &WheelHandle) {
        assert!(handle.send(WheelCommand::SetAutoUsb(false)));
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
    fn construction_waits_for_native_window_before_opening_device_or_usb() {
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
    fn native_initialization_starts_recording_and_usb_by_default_once() {
        let folder = TestFolder::new();
        let usb = FakeUsb::off();
        let starts = usb.starts.clone();
        let (handle, reads, _) = handle(&folder, usb, false, None);
        assert!(handle.send(WheelCommand::Initialize { window: 123 }));
        wait(|| snapshot(&handle).sample_count > 0 && starts.load(Ordering::Relaxed) == 1);
        let status = snapshot(&handle);
        assert!(status.running && status.auto_record && status.auto_usb);
        assert_eq!(status.wheel.config.device.as_ref().unwrap().id, device().id);
        assert_eq!(status.wheel.availability, "Available");
        assert!(status.wheel.last_sample_age_ms.unwrap() < 1000);
        assert!(handle.send(WheelCommand::Initialize { window: 123 }));
        wait(|| reads.load(Ordering::Relaxed) >= 3);
        assert_eq!(starts.load(Ordering::Relaxed), 1);
        assert!(handle.shutdown_wait(Duration::from_secs(2)));
    }

    #[test]
    fn device_enable_axis_and_startup_choices_restore_independently() {
        let folder = TestFolder::new();
        let (first, _, _) = handle(&folder, FakeUsb::off(), false, None);
        assert!(first.send(WheelCommand::SetAutoUsb(false)));
        assert!(first.send(WheelCommand::ConfigureDevice {
            role: DeviceRole::Wheel,
            config: DeviceMonitorConfig {
                enabled: true,
                device: Some(device()),
                axes: vec![3]
            }
        }));
        assert!(first.send(WheelCommand::ConfigureDevice {
            role: DeviceRole::Pedals,
            config: DeviceMonitorConfig {
                enabled: false,
                device: Some(device()),
                axes: vec![1, 2, 5]
            }
        }));
        assert!(first.send(WheelCommand::Run { window: 123 }));
        wait(|| snapshot(&first).wheel.availability == "Available");
        assert_eq!(snapshot(&first).pedals.availability, "Disabled");
        assert!(first.send(WheelCommand::Stop));
        wait(|| !snapshot(&first).running && !snapshot(&first).busy);
        assert!(
            snapshot(&first).auto_record,
            "Stop affects this run, not the saved startup preference"
        );
        assert!(first.shutdown_wait(Duration::from_secs(2)));
        let (second, _, _) = handle(&folder, FakeUsb::off(), false, None);
        assert!(second.send(WheelCommand::Initialize { window: 123 }));
        wait(|| snapshot(&second).wheel.availability == "Available");
        assert_eq!(snapshot(&second).wheel.config.axes, vec![3]);
        assert_eq!(snapshot(&second).pedals.config.axes, vec![1, 2, 5]);
        assert!(!snapshot(&second).pedals.config.enabled);
        assert!(second.send(WheelCommand::SetAutoRecord(false)));
        wait(|| !snapshot(&second).auto_record);
        assert!(
            snapshot(&second).running,
            "Startup checkbox does not silently stop the current run"
        );
        assert!(second.shutdown_wait(Duration::from_secs(2)));
        let (third, reads, _) = handle(&folder, FakeUsb::off(), false, None);
        assert!(third.send(WheelCommand::Initialize { window: 123 }));
        wait(|| !snapshot(&third).devices.is_empty() && !snapshot(&third).busy);
        assert!(!snapshot(&third).running);
        assert_eq!(reads.load(Ordering::Relaxed), 0);
        assert!(third.shutdown_wait(Duration::from_secs(2)));
    }

    #[test]
    fn independent_monitors_support_shared_controller_and_disable_one_without_stopping_usb() {
        let folder = TestFolder::new();
        let usb = FakeUsb::off();
        let starts = usb.starts.clone();
        let stops = usb.stops.clone();
        let (handle, _, _) = handle(&folder, usb, false, None);
        assert!(handle.send(WheelCommand::ConfigureDevice {
            role: DeviceRole::Pedals,
            config: DeviceMonitorConfig {
                enabled: true,
                device: Some(device()),
                axes: vec![1, 2, 5]
            }
        }));
        assert!(handle.send(WheelCommand::Initialize { window: 123 }));
        wait(|| {
            snapshot(&handle).wheel.availability == "Available"
                && snapshot(&handle).pedals.availability == "Available"
        });
        assert_eq!(
            snapshot(&handle)
                .pedals
                .axes
                .iter()
                .map(|a| a.axis)
                .collect::<Vec<_>>(),
            vec![1, 2, 5]
        );
        let mut config = snapshot(&handle).pedals.config;
        config.enabled = false;
        assert!(handle.send(WheelCommand::ConfigureDevice {
            role: DeviceRole::Pedals,
            config
        }));
        wait(|| snapshot(&handle).pedals.availability == "Disabled");
        assert!(snapshot(&handle).running);
        assert_eq!(snapshot(&handle).wheel.availability, "Available");
        assert_eq!(starts.load(Ordering::Relaxed), 1);
        assert_eq!(stops.load(Ordering::Relaxed), 0);
        assert!(handle.shutdown_wait(Duration::from_secs(2)));
    }

    #[test]
    fn manual_report_has_exact_preceding_five_minutes_and_is_independent_of_auto_marks() {
        let folder = TestFolder::new();
        let mut recorder = Recorder::start(&folder.root(), &device(), 0, SEGMENT_BUDGET).unwrap();
        for ms in (0..=420_000).step_by(1000) {
            at(&mut recorder, ms, "Available");
        }
        recorder
            .mark("Separate automatic incident", 410_000.0, 1_700_000_410_000)
            .unwrap();
        let prior_incidents = recorder.incidents.len();
        let path = recorder
            .report(
                &folder.exports(),
                &WheelStatus::default(),
                recorder.start + Duration::from_secs(420),
                1_700_000_420_000,
                None,
                None,
            )
            .unwrap();
        let entries = zip_entries(&path);
        let report: serde_json::Value =
            serde_json::from_slice(&entries.iter().find(|e| e.0 == "report.json").unwrap().1)
                .unwrap();
        assert_eq!(report["RequestedWindowSeconds"], 300);
        assert_eq!(report["FullFiveMinutesAvailable"], true);
        assert_eq!(report["SavedSpanSeconds"], 300.0);
        assert_eq!(recorder.incidents.len(), prior_incidents);
        let samples = &entries
            .iter()
            .find(|e| e.0 == "samples-000000.ndjson")
            .unwrap()
            .1;
        let records: Vec<WheelDiagnosticRecord> = std::str::from_utf8(samples)
            .unwrap()
            .lines()
            .map(|l| serde_json::from_str(l).unwrap())
            .collect();
        assert!(
            records
                .iter()
                .all(|r| (120_000.0..=420_000.0).contains(&r.elapsed_ms))
        );
        assert_eq!(records.iter().filter(|r| r.kind == "sample").count(), 301);
        verify_export(&path).unwrap();
    }

    #[test]
    fn partial_report_and_capture_gap_never_claim_five_minutes_of_complete_data() {
        let folder = TestFolder::new();
        let mut recorder = Recorder::start(&folder.root(), &device(), 0, SEGMENT_BUDGET).unwrap();
        for ms in [1000, 2000, 3000] {
            at(&mut recorder, ms, "Available");
        }
        recorder
            .gap(
                7,
                recorder.start + Duration::from_millis(2500),
                1_700_000_002_500,
            )
            .unwrap();
        let path = recorder
            .report(
                &folder.exports(),
                &WheelStatus::default(),
                recorder.start + Duration::from_millis(3500),
                1_700_000_003_500,
                None,
                None,
            )
            .unwrap();
        let entries = zip_entries(&path);
        let report: serde_json::Value =
            serde_json::from_slice(&entries.iter().find(|e| e.0 == "report.json").unwrap().1)
                .unwrap();
        assert_eq!(report["FullFiveMinutesAvailable"], false);
        assert_eq!(report["SavedSpanSeconds"], 2.0);
        assert_eq!(report["DroppedSamples"], 7);
        assert_eq!(report["UsbTraceIncluded"], false);
        assert!(
            report["UsbTraceOmissionReason"]
                .as_str()
                .unwrap()
                .contains("No USB")
        );
    }

    #[test]
    fn slow_incident_archive_keeps_health_heartbeat_and_single_admission_through_shutdown() {
        struct Release(Arc<AtomicBool>);
        impl Drop for Release {
            fn drop(&mut self) {
                self.0.store(false, Ordering::Release);
            }
        }
        let folder = TestFolder::new();
        let paused = Arc::new(AtomicBool::new(true));
        let _release = Release(paused.clone());
        let entered = Arc::new(AtomicBool::new(false));
        let writer: ArchiveWriter = {
            let paused = paused.clone();
            let entered = entered.clone();
            Arc::new(move |prepared| {
                entered.store(true, Ordering::Release);
                while paused.load(Ordering::Acquire) {
                    thread::sleep(Duration::from_millis(10));
                }
                prepared.save()
            })
        };
        let mut usb = FakeUsb::off();
        usb.snapshot_enabled = true;
        let starts = usb.starts.clone();
        let stops = usb.stops.clone();
        let snapshots = usb.snapshots.clone();
        let (handle, reads, _) = handle_with_writer(&folder, usb, false, None, writer);
        assert!(handle.send(WheelCommand::Initialize { window: 123 }));
        wait(|| snapshot(&handle).sample_count >= 3 && starts.load(Ordering::Relaxed) == 1);
        assert!(handle.send(WheelCommand::ReportDiagnosticIncident));
        assert!(!handle.send(WheelCommand::ReportDiagnosticIncident));
        wait(|| entered.load(Ordering::Acquire));
        let before = reads.load(Ordering::Relaxed);
        let directory = snapshot(&handle).capture_directory.unwrap();
        #[cfg(windows)]
        let heartbeat_before = {
            wait(|| directory.join("usb-heartbeat").exists());
            fs::metadata(directory.join("usb-heartbeat"))
                .unwrap()
                .modified()
                .unwrap()
        };
        let until = Instant::now() + Duration::from_millis(2200);
        while Instant::now() < until {
            let state = snapshot(&handle);
            assert!(state.running && state.report_pending);
            assert_eq!(state.report_sequence, 1);
            assert_eq!(state.wheel.availability, "Available");
            assert!(state.wheel.last_sample_age_ms.unwrap() < 1000);
            assert!(state.last_sample_age_ms.unwrap() < 1000);
            assert!(!handle.send(WheelCommand::ReportDiagnosticIncident));
            thread::sleep(Duration::from_millis(50));
        }
        assert!(reads.load(Ordering::Relaxed) >= before + 20);
        #[cfg(windows)]
        assert!(
            fs::metadata(directory.join("usb-heartbeat"))
                .unwrap()
                .modified()
                .unwrap()
                > heartbeat_before
        );
        assert_eq!(snapshots.load(Ordering::Relaxed), 1);
        assert_eq!(starts.load(Ordering::Relaxed), 1);
        assert_eq!(stops.load(Ordering::Relaxed), 0);
        assert!(!handle.shutdown_wait(Duration::from_millis(50)));
        assert!(snapshot(&handle).report_pending);
        assert!(!handle.inner.shared.finished.load(Ordering::Acquire));
        assert!(handle.inner.shared.report_admitted.load(Ordering::Acquire));
        paused.store(false, Ordering::Release);
        assert!(handle.shutdown_wait(Duration::from_secs(3)));
        let state = snapshot(&handle);
        assert!(!state.report_pending && state.report_error.is_none());
        assert_eq!(state.report_sequence, 1);
        verify_export(state.export_path.as_ref().unwrap()).unwrap();
        let receipt: ExportReceipt =
            read_json_bounded(&folder.root().join("last-export.json")).unwrap();
        assert_eq!(Some(receipt.path), state.export_path);
        assert_eq!(Some(receipt.bytes), state.export_bytes);
    }

    #[test]
    fn incident_archive_failure_releases_admission_and_next_report_has_new_sequence() {
        let folder = TestFolder::new();
        let calls = Arc::new(AtomicUsize::new(0));
        let writer: ArchiveWriter = {
            let calls = calls.clone();
            Arc::new(move |prepared| {
                if calls.fetch_add(1, Ordering::AcqRel) == 0 {
                    Err(io::Error::other("Synthetic disk failure"))
                } else {
                    prepared.save()
                }
            })
        };
        let (handle, _, _) = handle_with_writer(&folder, FakeUsb::off(), false, None, writer);
        enable(&handle);
        wait(|| snapshot(&handle).sample_count >= 3);
        assert!(handle.send(WheelCommand::ReportDiagnosticIncident));
        wait(|| snapshot(&handle).report_sequence == 1 && !snapshot(&handle).report_pending);
        assert!(
            snapshot(&handle)
                .report_error
                .unwrap()
                .contains("Synthetic disk failure")
        );
        assert!(snapshot(&handle).export_path.is_none());
        assert!(snapshot(&handle).running);
        assert!(handle.send(WheelCommand::ReportDiagnosticIncident));
        wait(|| snapshot(&handle).report_sequence == 2 && !snapshot(&handle).report_pending);
        assert!(snapshot(&handle).report_error.is_none());
        verify_export(snapshot(&handle).export_path.as_ref().unwrap()).unwrap();
        assert!(handle.shutdown_wait(Duration::from_secs(2)));
    }

    #[test]
    fn prepared_incident_survives_live_segment_rotation_without_copying_on_manager() {
        let folder = TestFolder::new();
        let mut recorder = Recorder::start(&folder.root(), &device(), 0, 1024).unwrap();
        for ms in (0..10_000).step_by(100) {
            at(&mut recorder, ms, "Available");
        }
        let prepared = recorder
            .prepare_report(
                &folder.exports(),
                &WheelStatus::default(),
                recorder.start + Duration::from_secs(10),
                1_700_000_010_000,
                None,
                None,
            )
            .unwrap();
        for ms in (10_000..30_000).step_by(100) {
            at(&mut recorder, ms, "Available");
        }
        let saved = prepared.save().unwrap();
        let entries = zip_entries(&saved.receipt.path);
        let samples = &entries
            .iter()
            .find(|e| e.0 == "samples-000000.ndjson")
            .unwrap()
            .1;
        let records: Vec<WheelDiagnosticRecord> = std::str::from_utf8(samples)
            .unwrap()
            .lines()
            .map(|line| serde_json::from_str(line).unwrap())
            .collect();
        assert!(!records.is_empty());
        assert!(records.iter().all(|record| record.elapsed_ms < 10_000.0));
        assert!(recorder.sample_count > records.len() as u64);
        verify_export(&saved.receipt.path).unwrap();
    }

    #[test]
    fn manual_report_waits_for_finalized_usb_snapshot_while_samples_continue() {
        let folder = TestFolder::new();
        let mut usb = FakeUsb::off();
        usb.snapshot_enabled = true;
        usb.snapshot_delay = Duration::from_millis(800);
        let starts = usb.starts.clone();
        let stops = usb.stops.clone();
        let snapshots = usb.snapshots.clone();
        let (handle, reads, _) = handle(&folder, usb, false, None);
        assert!(handle.send(WheelCommand::Initialize { window: 123 }));
        wait(|| snapshot(&handle).sample_count >= 3 && starts.load(Ordering::Relaxed) == 1);
        let before = reads.load(Ordering::Relaxed);
        assert!(handle.send(WheelCommand::ReportDiagnosticIncident));
        wait(|| snapshot(&handle).report_pending);
        wait(|| reads.load(Ordering::Relaxed) >= before + 4);
        assert!(snapshot(&handle).running && snapshot(&handle).report_pending);
        assert!(snapshot(&handle).wheel.last_sample_age_ms.unwrap() < 500);
        wait(|| !snapshot(&handle).report_pending && snapshot(&handle).export_path.is_some());
        let status = snapshot(&handle);
        assert!(status.report_error.is_none());
        assert!(status.running);
        assert_eq!(snapshots.load(Ordering::Relaxed), 1);
        assert_eq!(stops.load(Ordering::Relaxed), 0);
        assert_eq!(starts.load(Ordering::Relaxed), 1);
        let entries = zip_entries(status.export_path.as_ref().unwrap());
        assert!(
            entries
                .iter()
                .any(|e| e.0 == "usb-incident.etl" && e.1 == b"finalized synthetic USB snapshot")
        );
        let report: serde_json::Value =
            serde_json::from_slice(&entries.iter().find(|e| e.0 == "report.json").unwrap().1)
                .unwrap();
        assert_eq!(report["UsbTraceIncluded"], true);
        assert_eq!(report["UsbSnapshot"]["TraceRestarted"], true);
        assert!(status.export_bytes.unwrap() > 0);
        assert!(handle.shutdown_wait(Duration::from_secs(2)));
    }

    #[test]
    fn source_freshness_ages_even_if_manager_is_blocked_during_export() {
        let folder = TestFolder::new();
        let mut usb = FakeUsb::off();
        usb.stop_delay = Duration::from_millis(2400);
        let (handle, reads, _) = handle(&folder, usb, false, None);
        assert!(handle.send(WheelCommand::Initialize { window: 123 }));
        wait(|| snapshot(&handle).sample_count >= 2 && snapshot(&handle).can_export);
        assert!(handle.send(WheelCommand::Export));
        wait(|| snapshot(&handle).busy);
        let before = reads.load(Ordering::Relaxed);
        thread::sleep(Duration::from_millis(2100));
        let status = snapshot(&handle);
        assert!(status.last_sample_age_ms.unwrap() >= 2000);
        assert!(status.wheel.last_sample_age_ms.unwrap() >= 2000);
        assert!(reads.load(Ordering::Relaxed) > before);
        wait(|| snapshot(&handle).export_path.is_some());
        assert!(handle.shutdown_wait(Duration::from_secs(4)));
    }

    #[test]
    fn role_failures_are_not_cleared_by_other_healthy_devices() {
        let folder = TestFolder::new();
        let mut recorder = Recorder::start(&folder.root(), &device(), 0, SEGMENT_BUDGET).unwrap();
        recorder
            .sample_role(
                WheelReading {
                    availability: "Device unavailable".into(),
                    axis: None,
                    native_error: Some("Pedal driver missing".into()),
                },
                recorder.start,
                1_700_000_000_000,
                None,
                DeviceRole::Pedals,
                vec![AxisStatus {
                    axis: 1,
                    value: None,
                    ..Default::default()
                }],
            )
            .unwrap();
        at(&mut recorder, 100, "Available");
        assert!(
            recorder
                .failure_text()
                .unwrap()
                .contains("Pedals unavailable: Pedal driver missing")
        );
    }

    #[test]
    fn bounded_queue_overflow_records_gap_and_reader_keeps_sampling() {
        let folder = TestFolder::new();
        let reads = Arc::new(AtomicUsize::new(0));
        let value = Arc::new(Mutex::new(reading("Available")));
        let factory: SourceFactory = {
            let reads = reads.clone();
            let value = value.clone();
            Arc::new(move || {
                Box::new(FakeInput {
                    reads: reads.clone(),
                    value: value.clone(),
                    select_error: false,
                    hold: None,
                })
            })
        };
        let shared = Arc::new(Shared {
            status: Mutex::new(WheelStatus::default()),
            sdk: Mutex::new(None),
            observing: AtomicBool::new(true),
            finished: AtomicBool::new(false),
            report_admitted: AtomicBool::new(false),
            shutdown_requested: AtomicBool::new(false),
        });
        let worker = ReaderWorker::start(factory, shared, Some(device()), 0, 123);
        let deadline = Instant::now() + Duration::from_secs(12);
        while reads.load(Ordering::Relaxed) < 70 {
            assert!(Instant::now() < deadline);
            thread::sleep(Duration::from_millis(20));
        }
        assert!(
            !worker.join.is_finished(),
            "A full queue must not permanently stop recording"
        );
        let mut drained = 0;
        while let Ok(event) = worker.events.try_recv() {
            assert!(!matches!(event, ReaderEvent::Failed(_)));
            drained += 1;
        }
        assert_eq!(drained, 64);
        let (reading, now, utc, sdk, dropped, axes) =
            match worker.events.recv_timeout(Duration::from_secs(1)).unwrap() {
                ReaderEvent::Sample(reading, now, utc, sdk, dropped, axes) => {
                    (reading, now, utc, sdk, dropped, axes)
                }
                _ => panic!("Expected the reader to resume after queue capacity became available"),
            };
        assert!(dropped >= 6);
        let mut recorder = Recorder::start(&folder.root(), &device(), 0, SEGMENT_BUDGET).unwrap();
        recorder.gap(dropped, now, utc).unwrap();
        recorder
            .sample_role(reading, now, utc, sdk, DeviceRole::Wheel, axes)
            .unwrap();
        recorder.flush().unwrap();
        assert_eq!(recorder.dropped_samples, dropped);
        assert_eq!(recorder.sample_count, 1);
        assert!(
            recorder
                .history
                .iter()
                .any(|r| r.kind == "capture_gap" && r.dropped_samples == Some(dropped))
        );
        assert!(!recorder.incidents[0].post_window_complete);
        worker.cancel.store(true, Ordering::Release);
        worker.join.join().unwrap();
    }

    #[test]
    fn retained_legacy_capture_exports_after_restart_and_verified_receipt_restores() {
        let folder = TestFolder::new();
        let mut recorder = Recorder::start(&folder.root(), &device(), 0, SEGMENT_BUDGET).unwrap();
        for ms in [0, 100, 200] {
            at(&mut recorder, ms, "Available");
        }
        recorder.flush().unwrap();
        recorder.close_segment();
        let id = recorder
            .directory
            .file_name()
            .unwrap()
            .to_str()
            .unwrap()
            .to_owned();
        fs::remove_file(recorder.directory.join("capture-summary.json")).unwrap();
        drop(recorder);
        let (first, _, _) = handle(&folder, FakeUsb::off(), false, None);
        wait(|| !snapshot(&first).retained_captures.is_empty());
        assert_eq!(snapshot(&first).retained_captures[0].sample_count, 3);
        assert!(first.send(WheelCommand::ExportCapture { session_id: id }));
        wait(|| snapshot(&first).export_path.is_some());
        let exported = snapshot(&first);
        assert!(exported.export_bytes.unwrap() > 0);
        let entries = zip_entries(exported.export_path.as_ref().unwrap());
        let manifest: serde_json::Value =
            serde_json::from_slice(&entries.iter().find(|e| e.0 == "manifest.json").unwrap().1)
                .unwrap();
        assert_eq!(manifest["Status"]["Running"], false);
        assert_eq!(manifest["Status"]["SelectedDeviceName"], "Synthetic wheel");
        assert!(first.shutdown_wait(Duration::from_secs(2)));
        let (second, _, _) = handle(&folder, FakeUsb::off(), false, None);
        wait(|| snapshot(&second).export_path.is_some());
        assert_eq!(snapshot(&second).export_path, exported.export_path);
        assert_eq!(snapshot(&second).export_bytes, exported.export_bytes);
        assert!(second.shutdown_wait(Duration::from_secs(2)));
    }

    #[test]
    fn export_verification_detects_contents_changed_after_zip_write() {
        let folder = TestFolder::new();
        let mut recorder = Recorder::start(&folder.root(), &device(), 0, SEGMENT_BUDGET).unwrap();
        at(&mut recorder, 0, "Available");
        let path = recorder
            .export(&folder.exports(), &WheelStatus::default())
            .unwrap();
        verify_export(&path).unwrap();
        let mut bytes = fs::read(&path).unwrap();
        let name_len = u16::from_le_bytes(bytes[26..28].try_into().unwrap()) as usize;
        bytes[30 + name_len] ^= 1;
        fs::write(&path, bytes).unwrap();
        assert!(verify_export(&path).is_err());
    }

    #[test]
    fn future_nested_preferences_survive_configuration_changes_and_restart() {
        let folder = TestFolder::new();
        make_safe_directory(&folder.root()).unwrap();
        let mut saved = serde_json::to_value(Preferences::default()).unwrap();
        saved["Wheel"]["Device"] = serde_json::to_value(device()).unwrap();
        saved["Wheel"]["FutureMonitorOption"] = json!({"Mode":"future-policy"});
        saved["Wheel"]["Device"]["FutureDeviceOption"] = json!({"DriverHint":42});
        saved["Pedals"]["FuturePedalOption"] = json!(true);
        write_json(&folder.root(), "preferences.json", &saved).unwrap();
        let (first, _, _) = handle(&folder, FakeUsb::off(), false, None);
        wait(|| snapshot(&first).wheel.config.device.is_some());
        let mut config = snapshot(&first).wheel.config;
        config.enabled = false;
        assert!(first.send(WheelCommand::ConfigureDevice {
            role: DeviceRole::Wheel,
            config
        }));
        wait(|| !snapshot(&first).wheel.config.enabled);
        assert!(first.shutdown_wait(Duration::from_secs(2)));
        let (second, _, _) = handle(&folder, FakeUsb::off(), false, None);
        assert!(second.send(WheelCommand::SetAutoRecord(false)));
        wait(|| !snapshot(&second).auto_record);
        let persisted: serde_json::Value =
            read_json_bounded(&folder.root().join("preferences.json")).unwrap();
        assert_eq!(
            persisted["Wheel"]["FutureMonitorOption"]["Mode"],
            "future-policy"
        );
        assert_eq!(
            persisted["Wheel"]["Device"]["FutureDeviceOption"]["DriverHint"],
            42
        );
        assert_eq!(persisted["Pedals"]["FuturePedalOption"], true);
        assert!(second.shutdown_wait(Duration::from_secs(2)));
    }

    #[test]
    fn late_manual_report_after_stop_saves_retained_samples() {
        let folder = TestFolder::new();
        let (handle, _, _) = handle(&folder, FakeUsb::off(), false, None);
        enable(&handle);
        wait(|| snapshot(&handle).sample_count >= 3);
        assert!(handle.send(WheelCommand::Stop));
        wait(|| !snapshot(&handle).running && !snapshot(&handle).busy);
        assert!(handle.send(WheelCommand::ReportDiagnosticIncident));
        wait(|| snapshot(&handle).export_path.is_some() && !snapshot(&handle).report_pending);
        let status = snapshot(&handle);
        assert!(!status.running);
        assert!(status.report_error.is_none());
        let entries = zip_entries(status.export_path.as_ref().unwrap());
        let report: serde_json::Value =
            serde_json::from_slice(&entries.iter().find(|e| e.0 == "report.json").unwrap().1)
                .unwrap();
        assert!(report["SampleCount"].as_u64().unwrap() >= 3);
        assert_eq!(report["FullFiveMinutesAvailable"], false);
        assert!(handle.shutdown_wait(Duration::from_secs(2)));
    }

    #[test]
    fn disabled_sources_do_not_open_any_controller() {
        let folder = TestFolder::new();
        let factory: SourceFactory =
            Arc::new(|| panic!("Disabled physical sources must not be opened"));
        let handle = WheelDiagnostics::new(
            folder.root(),
            folder.exports(),
            factory,
            Box::new(FakeUsb::off()),
        );
        for role in [DeviceRole::Wheel, DeviceRole::Pedals] {
            assert!(handle.send(WheelCommand::ConfigureDevice {
                role,
                config: DeviceMonitorConfig {
                    enabled: false,
                    device: Some(device()),
                    axes: vec![0]
                }
            }));
        }
        assert!(handle.send(WheelCommand::Run { window: 123 }));
        wait(|| snapshot(&handle).sample_count >= 2);
        assert_eq!(snapshot(&handle).wheel.availability, "Disabled");
        assert_eq!(snapshot(&handle).pedals.availability, "Disabled");
        assert!(snapshot(&handle).wheel.axes.is_empty());
        assert!(handle.shutdown_wait(Duration::from_secs(2)));
    }

    #[test]
    fn pedals_are_not_guessed_as_wheel_and_retry_selection_with_partial_axis_support() {
        struct PedalInput {
            ready: Arc<AtomicBool>,
            axis: u8,
        }
        impl WheelInputSource for PedalInput {
            fn enumerate(&mut self) -> Result<Vec<WheelDevice>, String> {
                Ok(vec![WheelDevice {
                    id: "50000000-0000-0000-0000-000000000002".into(),
                    name: "Synthetic pedals".into(),
                    product_guid: None,
                }])
            }
            fn select(&mut self, _: &str, axis: u8, _: isize) -> Result<(), String> {
                if !self.ready.load(Ordering::Acquire) {
                    return Err("Device powered off".into());
                }
                if axis == 2 {
                    return Err("Axis not supported".into());
                }
                self.axis = axis;
                Ok(())
            }
            fn read(&mut self) -> WheelReading {
                WheelReading {
                    availability: "Available".into(),
                    axis: Some(100 + self.axis as i32),
                    native_error: None,
                }
            }
        }
        let folder = TestFolder::new();
        let ready = Arc::new(AtomicBool::new(false));
        let factory: SourceFactory = {
            let ready = ready.clone();
            Arc::new(move || {
                Box::new(PedalInput {
                    ready: ready.clone(),
                    axis: 0,
                })
            })
        };
        let handle = WheelDiagnostics::new(
            folder.root(),
            folder.exports(),
            factory,
            Box::new(FakeUsb::off()),
        );
        assert!(handle.send(WheelCommand::Initialize { window: 123 }));
        wait(|| snapshot(&handle).pedals.availability == "Device unavailable");
        assert!(snapshot(&handle).wheel.config.device.is_none());
        assert!(
            snapshot(&handle)
                .pedals
                .config
                .device
                .as_ref()
                .unwrap()
                .name
                .contains("pedals")
        );
        assert!(snapshot(&handle).running);
        ready.store(true, Ordering::Release);
        wait(|| snapshot(&handle).pedals.availability == "Available");
        let status = snapshot(&handle);
        assert!(status.pedals.last_sample_age_ms.unwrap() < 1000);
        let unsupported = status
            .pedals
            .axes
            .iter()
            .find(|axis| axis.axis == 2)
            .unwrap();
        assert_eq!(unsupported.value, None);
        assert_eq!(unsupported.availability, "Device unavailable");
        assert_eq!(
            unsupported.native_error.as_deref(),
            Some("Axis not supported")
        );
        assert_eq!(
            status
                .pedals
                .axes
                .iter()
                .find(|axis| axis.axis == 0)
                .unwrap()
                .value,
            Some(100)
        );
        assert!(handle.shutdown_wait(Duration::from_secs(2)));
    }

    #[test]
    fn accepted_pedal_axis_failure_is_partial_input_even_when_another_axis_works() {
        struct SplitPedals {
            axis: u8,
            brake_present: Arc<AtomicBool>,
        }
        impl WheelInputSource for SplitPedals {
            fn enumerate(&mut self) -> Result<Vec<WheelDevice>, String> {
                Ok(vec![device()])
            }
            fn select(&mut self, _: &str, axis: u8, _: isize) -> Result<(), String> {
                self.axis = axis;
                Ok(())
            }
            fn read(&mut self) -> WheelReading {
                if self.axis == 1 && !self.brake_present.load(Ordering::Acquire) {
                    WheelReading {
                        availability: "Read unavailable".into(),
                        axis: None,
                        native_error: Some("Brake input read failed".into()),
                    }
                } else {
                    reading("Available")
                }
            }
        }
        let folder = TestFolder::new();
        let brake_present = Arc::new(AtomicBool::new(true));
        let factory: SourceFactory = {
            let brake_present = brake_present.clone();
            Arc::new(move || {
                Box::new(SplitPedals {
                    axis: 0,
                    brake_present: brake_present.clone(),
                })
            })
        };
        let handle = WheelDiagnostics::new(
            folder.root(),
            folder.exports(),
            factory,
            Box::new(FakeUsb::off()),
        );
        assert!(handle.send(WheelCommand::ConfigureDevice {
            role: DeviceRole::Pedals,
            config: DeviceMonitorConfig {
                enabled: true,
                device: Some(device()),
                axes: vec![0, 1]
            }
        }));
        assert!(handle.send(WheelCommand::Run { window: 123 }));
        wait(|| snapshot(&handle).pedals.availability == "Available");
        brake_present.store(false, Ordering::Release);
        wait(|| snapshot(&handle).pedals.availability == "Partial input");
        assert!(
            snapshot(&handle)
                .pedals
                .axes
                .iter()
                .any(|axis| axis.axis == 0 && axis.value.is_some())
        );
        assert!(
            snapshot(&handle)
                .pedals
                .axes
                .iter()
                .any(|axis| axis.axis == 1 && axis.value.is_none())
        );
        brake_present.store(true, Ordering::Release);
        wait(|| snapshot(&handle).pedals.availability == "Available");
        assert!(handle.shutdown_wait(Duration::from_secs(2)));
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
    fn full_hour_survives_late_export_with_bounded_memory_and_disk() {
        let folder = TestFolder::new();
        let mut recorder = Recorder::start(&folder.root(), &device(), 0, SEGMENT_BUDGET).unwrap();
        // Race for more than an hour, then export after an interruption at minute 2.
        // Its automatic incident survives without any manual click during the race.
        for ms in (0..3_720_000).step_by(100) {
            at(
                &mut recorder,
                ms,
                if ms == 120_000 {
                    "Not attached"
                } else {
                    "Available"
                },
            );
        }
        recorder.flush().unwrap();
        assert_eq!(recorder.history.len(), HISTORY_SAMPLES);
        assert_eq!(recorder.incidents.len(), 1);
        assert!(recorder.incidents[0].post_window_complete);
        let mut samples: Vec<_> = fs::read_dir(&recorder.directory)
            .unwrap()
            .map(|entry| entry.unwrap().path())
            .filter(|path| {
                path.file_name()
                    .and_then(|x| x.to_str())
                    .is_some_and(is_sample_name)
            })
            .collect();
        samples.sort();
        assert_eq!(samples.len(), MAX_SAMPLE_SEGMENTS);
        let oldest = fs::read_to_string(&samples[0]).unwrap();
        let first: WheelDiagnosticRecord =
            serde_json::from_str(oldest.lines().next().unwrap()).unwrap();
        assert!(first.elapsed_ms <= 3_720_000.0 - 3_600_000.0);
        assert!(size_of_root(&folder.root()).unwrap() < SESSION_RESERVE);
        let metadata: serde_json::Value = serde_json::from_str(
            &fs::read_to_string(recorder.directory.join("metadata.json")).unwrap(),
        )
        .unwrap();
        assert_eq!(metadata["DiskHistorySeconds"], 3600);
        let path = recorder
            .export(&folder.exports(), &WheelStatus::default())
            .unwrap();
        let entries = zip_entries(&path);
        assert_eq!(
            entries
                .iter()
                .filter(|entry| is_sample_name(&entry.0))
                .count(),
            MAX_SAMPLE_SEGMENTS
        );
        assert_eq!(
            entries
                .iter()
                .filter(|entry| is_incident_name(&entry.0))
                .count(),
            1
        );
        assert!(entries.iter().all(|entry| is_export_name(&entry.0)));
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
    fn rolling_segments_stay_within_segment_count_and_exact_byte_bound() {
        let folder = TestFolder::new();
        let mut recorder = Recorder::start(&folder.root(), &device(), 0, 1024).unwrap();
        for ms in (0..40_000).step_by(100) {
            at(&mut recorder, ms, "Available");
        }
        recorder.close_segment();
        let files: Vec<_> = fs::read_dir(&recorder.directory)
            .unwrap()
            .map(|x| x.unwrap().path())
            .filter(|x| is_sample_name(x.file_name().unwrap().to_str().unwrap()))
            .collect();
        assert_eq!(files.len(), MAX_SAMPLE_SEGMENTS);
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
        let mut recorder = Recorder::start(&folder.root(), &device(), 0, SEGMENT_BUDGET).unwrap();
        at(&mut recorder, 0, "Available");
        write_json(&recorder.directory, "usb-owner.json", &json!({})).unwrap();
        write_json(
            &recorder.directory,
            "usb-status.json",
            &json!({"State":"Cleanup uncertain"}),
        )
        .unwrap();
        assert!(
            recorder
                .export(&folder.exports(), &WheelStatus::default())
                .is_err()
        );
        assert!(!capture_safe_to_export(&recorder.directory));
        assert!(!folder.exports().exists());
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
    fn missing_driver_records_failure_and_keeps_sdk_usb_capture_running() {
        let folder = TestFolder::new();
        let (handle, _, _) = handle(&folder, FakeUsb::off(), true, None);
        enable(&handle);
        wait(|| {
            snapshot(&handle).sample_count > 0
                && snapshot(&handle).availability == "Device unavailable"
        });
        assert!(snapshot(&handle).running);
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
    fn hung_reader_marks_device_unavailable_and_gates_full_export_restart_until_it_exits() {
        let folder = TestFolder::new();
        let hold = Arc::new(AtomicBool::new(true));
        let (handle, reads, _) = handle(&folder, FakeUsb::off(), false, Some(hold.clone()));
        enable(&handle);
        wait(|| reads.load(Ordering::Relaxed) == 1);
        wait(|| snapshot(&handle).wheel.availability.contains("stalled"));
        assert!(
            snapshot(&handle).running,
            "One stalled physical source must not stop global monitoring"
        );
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
        write_json(
            &recorder.directory,
            "usb-status.json",
            &json!({"State":"Stopped"}),
        )
        .unwrap();
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

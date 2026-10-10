//! Explicit, same-executable USB ETW helper. Ordinary wheel capture stays unelevated. No trace
//! begins at construction/startup; only the recorder's separate administrator-prompt action
//! calls `start`. Tests inject every trace operation and never elevate or start real ETW.

use std::fs::{self, OpenOptions};
use std::io::{Read, Write};
use std::os::windows::fs::{MetadataExt, OpenOptionsExt};
use std::os::windows::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use windows::Win32::Foundation::{
    CloseHandle, ERROR_WMI_INSTANCE_NOT_FOUND, FILETIME, GetLastError, HANDLE, HLOCAL, LocalFree,
    WAIT_OBJECT_0,
};
use windows::Win32::Security::Authorization::ConvertSidToStringSidW;
use windows::Win32::Security::{GetTokenInformation, TOKEN_QUERY, TOKEN_USER, TokenUser};
use windows::Win32::System::Com::CoTaskMemFree;
use windows::Win32::System::Diagnostics::Etw::{
    CONTROLTRACE_HANDLE, ControlTraceW, EVENT_TRACE_CONTROL_QUERY, EVENT_TRACE_PROPERTIES,
};
use windows::Win32::System::Performance::{QueryPerformanceCounter, QueryPerformanceFrequency};
use windows::Win32::System::SystemInformation::GetSystemDirectoryW;
use windows::Win32::System::Threading::{
    GetCurrentProcess, GetCurrentProcessId, GetExitCodeProcess, GetProcessTimes, OpenProcess,
    OpenProcessToken, PROCESS_NAME_WIN32, PROCESS_QUERY_LIMITED_INFORMATION,
    QueryFullProcessImageNameW, WaitForSingleObject,
};
use windows::Win32::UI::Shell::{
    FOLDERID_LocalAppData, KF_FLAG_DEFAULT, SEE_MASK_FLAG_NO_UI, SEE_MASK_NOCLOSEPROCESS,
    SHELLEXECUTEINFOW, SHGetKnownFolderPath, ShellExecuteExW,
};
use windows::Win32::UI::WindowsAndMessaging::SW_HIDE;
use windows::core::{PCWSTR, PWSTR};

use super::{quote_arg, wide};
use crate::wheel::WheelUsbTrace;

const REPARSE_POINT: u32 = 0x400;
const OPEN_REPARSE_POINT: u32 = 0x0020_0000;
const CREATE_NO_WINDOW: u32 = 0x0800_0000;
const METADATA_LIMIT: u64 = 16 * 1024;
// Only brief file operations share this gate, never a prompt, native input read or tool wait.
static WRITE_GATE: Mutex<()> = Mutex::new(());
const PROVIDERS: [&str; 3] = [
    "Microsoft-Windows-USB-USBHUB3",
    "Microsoft-Windows-USB-UCX",
    "Microsoft-Windows-USB-USBXHCI",
];

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "PascalCase")]
pub struct UsbState {
    pub state: String,
    pub message: String,
    pub providers: Vec<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "PascalCase")]
struct UsbOwner {
    parent_id: u32,
    parent_start_filetime: u64,
    sid: String,
}

#[derive(Debug)]
struct HelperArgs {
    session: String,
    parent_id: u32,
    parent_start_filetime: u64,
    sid: String,
}

impl HelperArgs {
    fn recovery(args: &[String]) -> Option<Self> {
        if args.len() != 4 || args[0] != "--wheel-usb-recover" {
            return None;
        }
        // Reuse the exact parent/SID validator; recovery accepts no supplied directory/session.
        let capture = [
            "--wheel-usb-helper".into(),
            "00000000000000000000000000000000".into(),
            args[1].clone(),
            args[2].clone(),
            args[3].clone(),
        ];
        Self::parse(&capture)
    }

    fn parse(args: &[String]) -> Option<Self> {
        if args.len() != 5 || args[0] != "--wheel-usb-helper" || !session_name(&args[1]) {
            return None;
        }
        let parent_id = args[2].parse().ok().filter(|n| *n != 0)?;
        let parent_start_filetime = args[3].parse().ok().filter(|n| *n != 0)?;
        let sid = &args[4];
        if sid.len() > 184
            || !sid.starts_with("S-1-")
            || !sid[4..]
                .split('-')
                .all(|p| !p.is_empty() && p.bytes().all(|c| c.is_ascii_digit()))
        {
            return None;
        }
        Some(Self {
            session: args[1].clone(),
            parent_id,
            parent_start_filetime,
            sid: sid.clone(),
        })
    }
}

struct OwnedHandle(HANDLE);

// SAFETY: this is an owned kernel handle, never shared or used without mutable recorder access.
unsafe impl Send for OwnedHandle {}

impl Drop for OwnedHandle {
    fn drop(&mut self) {
        // SAFETY: closes the one owned kernel handle, exactly once.
        unsafe {
            let _ = CloseHandle(self.0);
        }
    }
}

/// Worker-owned client; launch/stop waits never block the native UI message pump.
pub struct NativeWheelUsbTrace {
    helper: Option<OwnedHandle>,
    directory: Option<PathBuf>,
    configured_root: Option<PathBuf>,
    recovering: bool,
    unconfirmed_cleanup: bool,
    message: String,
}

impl Default for NativeWheelUsbTrace {
    fn default() -> Self {
        Self::new()
    }
}

impl NativeWheelUsbTrace {
    pub fn new() -> Self {
        Self {
            helper: None,
            directory: None,
            configured_root: None,
            recovering: false,
            unconfirmed_cleanup: false,
            message: "Off".into(),
        }
    }

    pub fn with_root(root: PathBuf) -> Self {
        let mut trace = Self::new();
        trace.configured_root = Some(root);
        trace
    }

    fn allowed_root(&self) -> Result<PathBuf, String> {
        let root = native_root()?;
        if self
            .configured_root
            .as_ref()
            .is_some_and(|configured| !same_path(configured, &root))
        {
            return Err("The USB helper requires the default machine-local diagnostics folder without redirects.".into());
        }
        Ok(root)
    }

    fn exit_code(&self) -> Option<u32> {
        let helper = self.helper.as_ref()?;
        // SAFETY: helper is an owned process handle with synchronize/query rights from Shell.
        unsafe {
            if WaitForSingleObject(helper.0, 0) != WAIT_OBJECT_0 {
                return None;
            }
            let mut code = 0;
            GetExitCodeProcess(helper.0, &mut code).ok().map(|()| code)
        }
    }

    fn state(&self) -> Option<UsbState> {
        read_json(&self.directory.as_ref()?.join(if self.recovering {
            "usb-recovery-status.json"
        } else {
            "usb-status.json"
        }))
        .ok()
    }

    fn accept_launch(&mut self, result: Result<OwnedHandle, u32>) -> Result<(), String> {
        match result {
            Ok(handle) => {
                self.helper = Some(handle);
                self.message = if self.recovering {
                    "Recovering interrupted USB traces; no new trace starts…"
                } else {
                    "Starting optional USB state trace…"
                }
                .into();
                Ok(())
            }
            Err(code) => {
                self.helper = None;
                self.message = launch_failure(code);
                if self.unconfirmed_cleanup {
                    self.message = format!(
                        "Cleanup uncertain. {} Retry the recovery action.",
                        self.message
                    );
                    return Err(self.message.clone());
                }
                if code == 1223 {
                    Ok(())
                } else {
                    Err(self.message.clone())
                }
            }
        }
    }
}

impl WheelUsbTrace for NativeWheelUsbTrace {
    fn status(&mut self) -> String {
        let state = self.state();
        if self.helper.is_some() && self.exit_code().is_some() && !self.safe_to_export() {
            return "USB helper exited without confirmed cleanup. Recover using the next explicit USB prompt.".into();
        }
        if self.exit_code() == Some(2) && state.is_none() {
            return "Unavailable: helper could not validate the same-user capture. No USB trace started.".into();
        }
        state.map_or_else(|| self.message.clone(), |state| state.message)
    }

    fn safe_to_export(&mut self) -> bool {
        if self.helper.is_none() {
            return !self.unconfirmed_cleanup;
        }
        let Some(code) = self.exit_code() else {
            return false;
        };
        let state = self.state();
        let confirmed = if self.recovering {
            state.as_ref().is_some_and(|s| s.state == "Stopped")
        } else {
            cleanup_exportable(code, state.as_ref())
        };
        if confirmed {
            self.unconfirmed_cleanup = false;
        }
        confirmed
    }

    fn start(&mut self, session_directory: &Path) -> Result<(), String> {
        if self.helper.is_some() && self.exit_code().is_none() {
            return Ok(());
        }
        if !self.safe_to_export() {
            self.message = "Cleanup uncertain. Restart Coach and use the explicit USB prompt to recover its interrupted trace.".into();
            return Err(self.message.clone());
        }
        let root = self.allowed_root()?;
        let session = session_directory
            .file_name()
            .and_then(|v| v.to_str())
            .filter(|v| session_name(v))
            .ok_or("Invalid diagnostics session.")?;
        if !same_path(
            session_directory
                .parent()
                .ok_or("Missing diagnostics root.")?,
            &root,
        ) || !safe_directory(session_directory)
        {
            return Err("The USB helper requires the default machine-local diagnostics folder without redirects.".into());
        }
        let executable =
            std::env::current_exe().map_err(|_| "Cannot identify the companion executable.")?;
        // SAFETY: borrowed pseudo-handle is not closed; identity/time queries retain no pointers.
        let (pid, start, sid) = unsafe {
            (
                GetCurrentProcessId(),
                process_creation(GetCurrentProcess())?,
                process_sid(GetCurrentProcess())?,
            )
        };
        self.directory = Some(session_directory.to_path_buf());
        self.recovering = false;
        if session_directory
            .join("usb-stop")
            .try_exists()
            .map_err(|e| e.to_string())?
        {
            self.message = "Off: USB start was cancelled before the administrator prompt.".into();
            return Ok(());
        }
        let path = session_directory.join("usb-status.json");
        if path.exists() {
            checked_metadata(&path).map_err(|e| e.to_string())?;
            fs::remove_file(path).map_err(|e| e.to_string())?;
        }
        self.heartbeat();
        let arguments = [
            "--wheel-usb-helper".into(),
            session.into(),
            pid.to_string(),
            start.to_string(),
            sid,
        ];
        self.accept_launch(launch_helper(&executable, &arguments))
    }

    fn can_recover(&mut self) -> bool {
        self.helper.is_none() || self.exit_code().is_some()
    }

    fn recover(&mut self) -> Result<(), String> {
        if !self.can_recover() {
            return Err("The USB helper is still finishing its previous action.".into());
        }
        let root = self.allowed_root()?;
        if !safe_directory(&root) {
            return Err(
                "No safe machine-local diagnostics folder is available for recovery.".into(),
            );
        }
        let executable =
            std::env::current_exe().map_err(|_| "Cannot identify the companion executable.")?;
        // SAFETY: borrowed pseudo-handle is not closed; identity/time queries retain no pointers.
        let (pid, start, sid) = unsafe {
            (
                GetCurrentProcessId(),
                process_creation(GetCurrentProcess())?,
                process_sid(GetCurrentProcess())?,
            )
        };
        let unconfirmed = !self.safe_to_export();
        let path = root.join("usb-recovery-status.json");
        if path.exists() {
            checked_metadata(&path).map_err(|e| e.to_string())?;
            fs::remove_file(path).map_err(|e| e.to_string())?;
        }
        self.unconfirmed_cleanup |= unconfirmed;
        self.helper = None;
        self.directory = Some(root);
        self.recovering = true;
        let arguments = [
            "--wheel-usb-recover".into(),
            pid.to_string(),
            start.to_string(),
            sid,
        ];
        self.accept_launch(launch_helper(&executable, &arguments))
    }

    fn heartbeat(&mut self) {
        if !self.recovering
            && let Some(directory) = &self.directory
            && heartbeat_directory(directory).is_err()
        {
            self.message =
                "USB heartbeat could not be written; helper watchdog will attempt cleanup.".into();
        }
    }

    fn stop(&mut self) -> Result<(), String> {
        if self.helper.is_none() {
            return Ok(());
        }
        if !self.recovering
            && let Some(directory) = &self.directory
        {
            request_stop_directory(directory)?;
        }
        let started = Instant::now();
        while self.exit_code().is_none() && started.elapsed() < Duration::from_secs(3) {
            thread::sleep(Duration::from_millis(50));
        }
        if !self.safe_to_export() {
            self.message =
                "USB stop is pending or cleanup is uncertain; export remains blocked.".into();
            return Err(self.message.clone());
        }
        Ok(())
    }
}

impl Drop for NativeWheelUsbTrace {
    fn drop(&mut self) {
        let _ = self.stop();
    }
}

/// Main calls this before the ordinary CLI and single-instance guard. No supplied paths are
/// accepted. Invalid arguments or different-user elevation return 2 before any trace operation.
pub fn run_helper(arguments: &[String]) -> i32 {
    if arguments
        .first()
        .is_some_and(|arg| arg == "--wheel-usb-recover")
    {
        return run_recovery_helper(arguments);
    }
    let Some(args) = HelperArgs::parse(arguments) else {
        return 2;
    };
    let Ok(root) = native_root() else {
        return 2;
    };
    let directory = root.join(&args.session);
    if !safe_session_directory(&directory) {
        return 2;
    }
    // SAFETY: current-process pseudo-handle is borrowed and identity is copied into a String.
    let Ok(sid) = (unsafe { process_sid(GetCurrentProcess()) }) else {
        return 2;
    };
    if sid != args.sid
        || !parent_matches(&args, true)
        || !heartbeat_fresh(&directory)
        || directory.join("usb-stop").exists()
    {
        return 2;
    }
    let mut operations = NativeTraceOps;
    if recover(&root, &sid, &mut operations, &args).is_err() {
        let _ = write_state(
            &directory,
            "Cleanup uncertain",
            "Interrupted USB cleanup could not be confirmed. No new trace started.",
            &[],
        );
        return 1;
    }
    if directory.join("usb-stop").exists()
        || !parent_matches(&args, true)
        || !heartbeat_fresh(&directory)
    {
        return 2;
    }
    let owner = UsbOwner {
        parent_id: args.parent_id,
        parent_start_filetime: args.parent_start_filetime,
        sid: sid.clone(),
    };
    if write_json(&directory, "usb-owner.json", &owner).is_err() {
        return 2;
    }
    let name = trace_name(&sid, &args.session);
    capture_owned_trace(&directory, &name, &mut operations, || {
        parent_matches(&args, false)
            && !directory.join("usb-stop").exists()
            && heartbeat_fresh(&directory)
    })
}

fn run_recovery_helper(arguments: &[String]) -> i32 {
    let Some(args) = HelperArgs::recovery(arguments) else {
        return 2;
    };
    let Ok(root) = native_root() else {
        return 2;
    };
    // SAFETY: current-process pseudo-handle is borrowed, and copied SID is ordinary text.
    let Ok(sid) = (unsafe { process_sid(GetCurrentProcess()) }) else {
        return 2;
    };
    if sid != args.sid || !parent_matches(&args, true) || !safe_directory(&root) {
        return 2;
    }
    if write_recovery_state(
        &root,
        "Starting",
        "Recovering interrupted USB traces; no new trace starts.",
    )
    .is_err()
    {
        return 2;
    }
    let mut operations = NativeTraceOps;
    // An exited trace helper can leave this requester's own trace behind. The client allows
    // recovery only after that helper has exited; other live Coach parents remain untouched.
    let result = recover_authorized(
        &root,
        &sid,
        &mut operations,
        |owner| {
            !same_owner_parent(owner, &args)
                && parent_matches(
                    &HelperArgs {
                        session: String::new(),
                        parent_id: owner.parent_id,
                        parent_start_filetime: owner.parent_start_filetime,
                        sid: sid.clone(),
                    },
                    false,
                )
        },
        || parent_matches(&args, true),
    );
    match result {
        Ok(()) => {
            if write_recovery_state(
                &root,
                "Stopped",
                "Interrupted USB traces checked and stopped. No new trace started.",
            )
            .is_ok()
            {
                0
            } else {
                1
            }
        }
        Err(_) => {
            let _ = write_recovery_state(
                &root,
                "Cleanup uncertain",
                "Interrupted USB cleanup could not be confirmed. Recordings are preserved; retry recovery.",
            );
            1
        }
    }
}

fn same_owner_parent(owner: &UsbOwner, args: &HelperArgs) -> bool {
    owner.parent_id == args.parent_id
        && owner.parent_start_filetime == args.parent_start_filetime
        && owner.sid == args.sid
}

fn write_recovery_state(root: &Path, state: &str, message: &str) -> std::io::Result<()> {
    write_json(
        root,
        "usb-recovery-status.json",
        &UsbState {
            state: state.into(),
            message: message.into(),
            providers: vec![],
        },
    )
}

/// Called only on the manager's explicit USB action before enqueueing actor Start, never while
/// an earlier launch/stop is pending. Native start never removes a newer stop intent.
pub fn prepare_directory(directory: &Path) -> Result<(), String> {
    if !safe_directory(directory) {
        return Err("Redirected USB diagnostics directory.".into());
    }
    let stop = directory.join("usb-stop");
    if stop.try_exists().map_err(|e| e.to_string())? {
        checked_metadata(&stop).map_err(|e| e.to_string())?;
        fs::remove_file(stop).map_err(|e| e.to_string())?;
    }
    heartbeat_directory(directory)
}

/// Independent of the UAC actor, so a consent dialog cannot age the helper's heartbeat.
pub fn heartbeat_directory(directory: &Path) -> Result<(), String> {
    let (ticks, _) = monotonic().ok_or("USB monotonic clock unavailable.")?;
    write_bytes(directory, "usb-heartbeat", ticks.to_string().as_bytes()).map_err(|e| e.to_string())
}

/// Persist before enqueueing actor Stop; a later administrator consent must observe it.
pub fn request_stop_directory(directory: &Path) -> Result<(), String> {
    write_bytes(directory, "usb-stop", b"stop").map_err(|e| e.to_string())
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum TraceQuery {
    Absent,
    Present,
    Unavailable(u32),
}

trait TraceOps {
    fn run(&mut self, arguments: &[String]) -> i32;
    fn query(&mut self, name: &str) -> TraceQuery;
}

struct NativeTraceOps;

impl TraceOps for NativeTraceOps {
    fn run(&mut self, arguments: &[String]) -> i32 {
        run_logman(arguments)
    }
    fn query(&mut self, name: &str) -> TraceQuery {
        query_trace(name)
    }
}

fn capture_owned_trace(
    directory: &Path,
    name: &str,
    ops: &mut impl TraceOps,
    mut keep_running: impl FnMut() -> bool,
) -> i32 {
    let mut enabled = Vec::new();
    let mut attempted_start = false;
    if write_state(directory, "Starting", "Starting USB state trace.", &enabled).is_err() {
        return 2;
    }
    // Only our fixed USB 3 stack providers with Default, which excludes transfer events.
    for provider in PROVIDERS {
        if !keep_running() {
            break;
        }
        attempted_start = true;
        let arguments = if enabled.is_empty() {
            start_arguments(name, &directory.join("usb.etl"), provider)
        } else {
            update_arguments(name, provider)
        };
        if ops.run(&arguments) == 0 {
            enabled.push(provider.to_string());
        } else if enabled.is_empty() {
            // A failed/expired tool may still have created a native session. Do not layer
            // another start over an unconfirmed partial session.
            let stop = ops.run(&stop_arguments(name));
            if !cleanup_confirmed(stop, ops.query(name)) {
                break;
            }
        }
    }
    let code = if enabled.is_empty() {
        1
    } else {
        if write_state(
            directory,
            "Recording",
            &format!(
                "USB state trace recording ({}/3 USB 3 stack providers).",
                enabled.len()
            ),
            &enabled,
        )
        .is_ok()
        {
            while keep_running() {
                thread::sleep(Duration::from_millis(500));
            }
            0
        } else {
            1
        }
    };
    if !attempted_start {
        return if write_state(
            directory,
            "Unavailable",
            "USB start cancelled; no trace started.",
            &[],
        )
        .is_ok()
        {
            0
        } else {
            1
        };
    }
    // Always clean up after any attempted start, even if no provider was confirmed enabled.
    let stopped = ops.run(&stop_arguments(name));
    let query = ops.query(name);
    let ended = cleanup_confirmed(stopped, query);
    let (state, message) = if !ended {
        (
            "Cleanup uncertain",
            "USB cleanup uncertain; recover using the next explicit USB prompt.",
        )
    } else if enabled.is_empty() {
        (
            "Unavailable",
            "USB ETW unavailable; no active named session remains. Wheel capture remains usable.",
        )
    } else if stopped == 0 {
        ("Stopped", "USB trace stopped and flushed.")
    } else {
        ("Stopped", "USB session has ended; trace may be incomplete.")
    };
    if write_state(directory, state, message, &enabled).is_err() {
        return 1;
    }
    if ended { code } else { 1 }
}

fn recover(
    root: &Path,
    sid: &str,
    ops: &mut impl TraceOps,
    invoking_parent: &HelperArgs,
) -> Result<(), String> {
    let directory = root.join(&invoking_parent.session);
    recover_authorized(
        root,
        sid,
        ops,
        |owner| {
            parent_matches(
                &HelperArgs {
                    session: String::new(),
                    parent_id: owner.parent_id,
                    parent_start_filetime: owner.parent_start_filetime,
                    sid: sid.into(),
                },
                false,
            )
        },
        || {
            parent_matches(invoking_parent, true)
                && heartbeat_fresh(&directory)
                && !directory.join("usb-stop").exists()
        },
    )
}

#[cfg(test)]
fn recover_with_parent_check(
    root: &Path,
    sid: &str,
    ops: &mut impl TraceOps,
    parent_alive: impl Fn(&UsbOwner) -> bool,
) -> Result<(), String> {
    recover_authorized(root, sid, ops, parent_alive, || true)
}

fn recover_authorized(
    root: &Path,
    sid: &str,
    ops: &mut impl TraceOps,
    parent_alive: impl Fn(&UsbOwner) -> bool,
    authorize: impl Fn() -> bool,
) -> Result<(), String> {
    if !safe_directory(root) {
        return Err("Redirected USB diagnostics root.".into());
    }
    let directories = fs::read_dir(root).map_err(|e| e.to_string())?;
    let mut examined = 0;
    for (scanned, entry) in directories.enumerate() {
        if scanned >= 4096 {
            return Err("Too many diagnostics entries to safely inspect.".into());
        }
        if !authorize() {
            return Err(
                "Recovery cancelled because its companion parent is no longer valid.".into(),
            );
        }
        let directory = entry.map_err(|e| e.to_string())?.path();
        let Some(session) = directory
            .file_name()
            .and_then(|s| s.to_str())
            .filter(|s| session_name(s))
        else {
            continue;
        };
        if !safe_session_directory(&directory) {
            continue;
        }
        let Ok(owner) = read_json::<UsbOwner>(&directory.join("usb-owner.json")) else {
            continue;
        };
        if owner.sid != sid || owner.parent_id == 0 || owner.parent_start_filetime == 0 {
            continue;
        }
        if read_json::<UsbState>(&directory.join("usb-status.json"))
            .is_ok_and(|state| matches!(state.state.as_str(), "Stopped" | "Unavailable"))
        {
            continue;
        }
        if parent_alive(&owner) {
            continue;
        }
        examined += 1;
        if examined > 16 {
            return Err("Recovery checked 16 interrupted USB sessions. Run recovery again to check the rest.".into());
        }
        if !authorize() {
            return Err(
                "Recovery cancelled because its companion parent is no longer valid.".into(),
            );
        }
        let name = trace_name(sid, session);
        let stopped = ops.run(&stop_arguments(&name));
        if !authorize() {
            let _ = write_state(
                &directory,
                "Cleanup uncertain",
                "Recovery parent exited before cleanup could be queried.",
                &[],
            );
            return Err("Recovery parent exited before cleanup could be confirmed.".into());
        }
        if !cleanup_confirmed(stopped, ops.query(&name)) {
            let _ = write_state(
                &directory,
                "Cleanup uncertain",
                "Interrupted USB cleanup could not be confirmed.",
                &[],
            );
            return Err("Interrupted USB cleanup could not be confirmed.".into());
        }
        write_state(
            &directory,
            "Stopped",
            "Interrupted USB trace recovered and stopped.",
            &[],
        )
        .map_err(|e| e.to_string())?;
    }
    Ok(())
}

fn start_arguments(name: &str, output: &Path, provider: &str) -> Vec<String> {
    [
        "start",
        name,
        "-ets",
        "-o",
        &output.to_string_lossy(),
        "-f",
        "bincirc",
        "-max",
        "32",
        "-nb",
        "4",
        "16",
        "-bs",
        "64",
        "-p",
        provider,
        "(Default)",
    ]
    .iter()
    .map(|s| (*s).to_string())
    .collect()
}
fn update_arguments(name: &str, provider: &str) -> Vec<String> {
    ["update", name, "-ets", "-p", provider, "(Default)"]
        .iter()
        .map(|s| (*s).to_string())
        .collect()
}
fn stop_arguments(name: &str) -> Vec<String> {
    ["stop", name, "-ets"]
        .iter()
        .map(|s| (*s).to_string())
        .collect()
}
fn trace_name(sid: &str, session: &str) -> String {
    let hash = Sha256::digest(sid.as_bytes());
    let short: String = hash[..6].iter().map(|byte| format!("{byte:02X}")).collect();
    format!("iRacingCoachWheel-{short}-{session}")
}
fn session_name(name: &str) -> bool {
    name.len() == 32 && name.bytes().all(|c| c.is_ascii_hexdigit())
}
fn same_path(a: &Path, b: &Path) -> bool {
    a.to_string_lossy()
        .eq_ignore_ascii_case(&b.to_string_lossy())
}
fn launch_failure(code: u32) -> String {
    if code == 1223 {
        "Off: administrator prompt cancelled.".into()
    } else {
        format!("Unavailable: helper launch failed ({code}).")
    }
}
fn cleanup_exportable(code: u32, state: Option<&UsbState>) -> bool {
    state.is_some_and(|s| matches!(s.state.as_str(), "Stopped" | "Unavailable"))
        || code == 2 && state.is_none()
}
fn cleanup_confirmed(stopped: i32, query: TraceQuery) -> bool {
    match query {
        TraceQuery::Present => false,
        TraceQuery::Absent => true,
        TraceQuery::Unavailable(_) => stopped == 0,
    }
}

fn native_root() -> Result<PathBuf, String> {
    // SAFETY: known-folder API returns a CoTaskMem-allocated string, copied and freed once.
    unsafe {
        let path = SHGetKnownFolderPath(&FOLDERID_LocalAppData, KF_FLAG_DEFAULT, None)
            .map_err(|e| e.to_string())?;
        let text = path.to_string().map_err(|e| e.to_string());
        CoTaskMemFree(Some(path.0.cast()));
        text.map(|p| {
            PathBuf::from(p)
                .join("iRacingCoach")
                .join("wheel-diagnostics")
        })
    }
}

fn launch_helper(executable: &Path, arguments: &[String]) -> Result<OwnedHandle, u32> {
    let executable = wide(&executable.to_string_lossy());
    let arguments = wide(
        &arguments
            .iter()
            .map(|a| quote_arg(a))
            .collect::<Vec<_>>()
            .join(" "),
    );
    let verb = wide("runas");
    let mut info = SHELLEXECUTEINFOW {
        cbSize: size_of::<SHELLEXECUTEINFOW>() as u32,
        fMask: SEE_MASK_NOCLOSEPROCESS | SEE_MASK_FLAG_NO_UI,
        lpVerb: PCWSTR(verb.as_ptr()),
        lpFile: PCWSTR(executable.as_ptr()),
        lpParameters: PCWSTR(arguments.as_ptr()),
        nShow: SW_HIDE.0,
        ..Default::default()
    };
    // SAFETY: all strings remain live through ShellExecuteExW; returned process handle is owned.
    unsafe {
        if ShellExecuteExW(&mut info).is_err() {
            return Err(GetLastError().0);
        }
        if info.hProcess.is_invalid() {
            return Err(0);
        }
    }
    Ok(OwnedHandle(info.hProcess))
}

fn process_creation(handle: HANDLE) -> Result<u64, String> {
    let mut creation = FILETIME::default();
    let mut exit = FILETIME::default();
    let mut kernel = FILETIME::default();
    let mut user = FILETIME::default();
    // SAFETY: handle is valid and all FILETIME out pointers are writable.
    unsafe { GetProcessTimes(handle, &mut creation, &mut exit, &mut kernel, &mut user) }
        .map_err(|e| e.to_string())?;
    Ok((u64::from(creation.dwHighDateTime) << 32) | u64::from(creation.dwLowDateTime))
}

fn process_sid(handle: HANDLE) -> Result<String, String> {
    let mut token = HANDLE::default();
    // SAFETY: opens a query-only token from a valid process handle and owns that token.
    unsafe { OpenProcessToken(handle, TOKEN_QUERY, &mut token) }.map_err(|e| e.to_string())?;
    let token = OwnedHandle(token);
    let mut bytes = 0;
    // SAFETY: first call requests size only. Allocate aligned pointer-sized storage for TOKEN_USER.
    unsafe {
        let _ = GetTokenInformation(token.0, TokenUser, None, 0, &mut bytes);
    }
    if bytes == 0 || bytes > 4096 {
        return Err("Cannot validate process user.".into());
    }
    let mut buffer = vec![0_usize; (bytes as usize).div_ceil(size_of::<usize>())];
    // SAFETY: buffer is aligned and large enough; TOKEN_USER.Sid points inside it until copied.
    unsafe {
        GetTokenInformation(
            token.0,
            TokenUser,
            Some(buffer.as_mut_ptr().cast()),
            bytes,
            &mut bytes,
        )
        .map_err(|e| e.to_string())?;
        let user = &*buffer.as_ptr().cast::<TOKEN_USER>();
        let mut text = PWSTR::null();
        ConvertSidToStringSidW(user.User.Sid, &mut text).map_err(|e| e.to_string())?;
        let copied = text.to_string().map_err(|e| e.to_string());
        let _ = LocalFree(Some(HLOCAL(text.0.cast())));
        copied
    }
}

fn parent_matches(args: &HelperArgs, verify_executable: bool) -> bool {
    // SAFETY: PID query handle is owned and closed; the parent is never modified.
    let Ok(handle) =
        (unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, args.parent_id) })
    else {
        return false;
    };
    let handle = OwnedHandle(handle);
    let mut exit = 0;
    // SAFETY: all queries use a live owned process handle and writable out buffers.
    unsafe {
        if GetExitCodeProcess(handle.0, &mut exit).is_err() || exit != 259 {
            return false;
        }
    }
    if process_creation(handle.0).ok() != Some(args.parent_start_filetime)
        || process_sid(handle.0).ok().as_deref() != Some(args.sid.as_str())
    {
        return false;
    }
    if !verify_executable {
        return true;
    }
    let mut text = vec![0_u16; 32768];
    let mut length = text.len() as u32;
    // SAFETY: text is writable; process-image API returns only the parent's executable path.
    if unsafe {
        QueryFullProcessImageNameW(
            handle.0,
            PROCESS_NAME_WIN32,
            PWSTR(text.as_mut_ptr()),
            &mut length,
        )
    }
    .is_err()
    {
        return false;
    }
    let parent = PathBuf::from(String::from_utf16_lossy(&text[..length as usize]));
    std::env::current_exe().is_ok_and(|current| same_path(&parent, &current))
}

fn monotonic() -> Option<(i64, i64)> {
    let mut ticks = 0;
    let mut frequency = 0;
    // SAFETY: QPC/QPF write only these counters and retain no pointers.
    unsafe {
        QueryPerformanceCounter(&mut ticks).ok()?;
        QueryPerformanceFrequency(&mut frequency).ok()?;
    }
    (frequency > 0).then_some((ticks, frequency))
}
fn heartbeat_fresh(directory: &Path) -> bool {
    let Ok(text) = read_bytes(&directory.join("usb-heartbeat")) else {
        return false;
    };
    let Some(then) = String::from_utf8(text)
        .ok()
        .and_then(|t| t.parse::<i64>().ok())
    else {
        return false;
    };
    let Some((now, frequency)) = monotonic() else {
        return false;
    };
    heartbeat_age_fresh(now, then, frequency)
}
fn heartbeat_age_fresh(now: i64, then: i64, frequency: i64) -> bool {
    now.checked_sub(then)
        .is_some_and(|age| age >= 0 && frequency > 0 && age < frequency.saturating_mul(10))
}

fn query_trace(name: &str) -> TraceQuery {
    // Native structure plus two bounded wide-string buffers, naturally aligned. No offsets
    // are hard-coded to an architecture's struct size.
    let header = size_of::<EVENT_TRACE_PROPERTIES>();
    let size = header + 2 * 2048;
    let mut storage = vec![0_usize; size.div_ceil(size_of::<usize>())];
    let name = wide(name);
    // SAFETY: properties is aligned, allocated for struct plus buffers. QUERY does not modify
    // tracing; only ERROR_WMI_INSTANCE_NOT_FOUND is treated as absence.
    let code = unsafe {
        let properties = storage.as_mut_ptr().cast::<EVENT_TRACE_PROPERTIES>();
        *properties = EVENT_TRACE_PROPERTIES::default();
        (*properties).Wnode.BufferSize = size as u32;
        (*properties).LoggerNameOffset = header as u32;
        (*properties).LogFileNameOffset = (header + 2048) as u32;
        ControlTraceW(
            CONTROLTRACE_HANDLE { Value: 0 },
            PCWSTR(name.as_ptr()),
            properties,
            EVENT_TRACE_CONTROL_QUERY,
        )
    };
    if code == ERROR_WMI_INSTANCE_NOT_FOUND {
        TraceQuery::Absent
    } else if code.0 == 0 {
        TraceQuery::Present
    } else {
        TraceQuery::Unavailable(code.0)
    }
}

fn run_logman(arguments: &[String]) -> i32 {
    let mut system = [0_u16; 32768];
    // SAFETY: system buffer is writable for the bounded system-directory result.
    let length = unsafe { GetSystemDirectoryW(Some(&mut system)) } as usize;
    if length == 0 || length >= system.len() {
        return -1;
    }
    let executable = PathBuf::from(String::from_utf16_lossy(&system[..length])).join("logman.exe");
    let Ok(mut child) = Command::new(executable)
        .args(arguments)
        .creation_flags(CREATE_NO_WINDOW)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
    else {
        return -1;
    };
    // Tool prose is discarded: it may contain localized user/device/path information.
    let start = Instant::now();
    loop {
        match child.try_wait() {
            Ok(Some(status)) => return status.code().unwrap_or(-1),
            Err(_) => {
                let _ = child.kill();
                let _ = child.try_wait();
                return -1;
            }
            Ok(None) => {}
        }
        if start.elapsed() >= Duration::from_secs(2) {
            let _ = child.kill();
            let _ = child.try_wait();
            return -1;
        }
        thread::sleep(Duration::from_millis(20));
    }
}

fn safe_directory(path: &Path) -> bool {
    path.ancestors().all(|part| {
        fs::symlink_metadata(part)
            .is_ok_and(|m| m.is_dir() && m.file_attributes() & REPARSE_POINT == 0)
    })
}
fn safe_session_directory(path: &Path) -> bool {
    if !safe_directory(path) {
        return false;
    }
    fs::read_dir(path).is_ok_and(|entries| {
        entries
            .into_iter()
            .all(|entry| entry.is_ok_and(|entry| checked_metadata(&entry.path()).is_ok()))
    })
}
fn checked_metadata(path: &Path) -> std::io::Result<fs::Metadata> {
    if !path.parent().is_some_and(safe_directory) {
        return Err(std::io::Error::other(
            "Redirected USB diagnostics directory.",
        ));
    }
    let metadata = fs::symlink_metadata(path)?;
    if !metadata.is_file() || metadata.file_attributes() & REPARSE_POINT != 0 {
        return Err(std::io::Error::other("Redirected USB diagnostics file."));
    }
    Ok(metadata)
}
fn read_bytes(path: &Path) -> std::io::Result<Vec<u8>> {
    if checked_metadata(path)?.len() > METADATA_LIMIT {
        return Err(std::io::Error::other("USB metadata is too large."));
    }
    let mut file = OpenOptions::new()
        .read(true)
        .custom_flags(OPEN_REPARSE_POINT)
        .open(path)?;
    if file.metadata()?.file_attributes() & REPARSE_POINT != 0 {
        return Err(std::io::Error::other("Redirected USB metadata."));
    }
    let mut bytes = Vec::new();
    (&mut file)
        .take(METADATA_LIMIT + 1)
        .read_to_end(&mut bytes)?;
    if bytes.len() as u64 > METADATA_LIMIT {
        return Err(std::io::Error::other("USB metadata is too large."));
    }
    Ok(bytes)
}
fn read_json<T: for<'a> Deserialize<'a>>(path: &Path) -> std::io::Result<T> {
    serde_json::from_slice(&read_bytes(path)?).map_err(std::io::Error::other)
}
fn write_bytes(directory: &Path, name: &str, bytes: &[u8]) -> std::io::Result<()> {
    let _guard = WRITE_GATE
        .lock()
        .map_err(|_| std::io::Error::other("USB metadata write lock unavailable."))?;
    if !safe_directory(directory) {
        return Err(std::io::Error::other(
            "Redirected USB diagnostics directory.",
        ));
    }
    let path = directory.join(name);
    let temporary = directory.join(format!("{name}.tmp"));
    for file in [&path, &temporary] {
        if file.exists() {
            checked_metadata(file)?;
        }
    }
    let mut file = OpenOptions::new()
        .create(true)
        .truncate(true)
        .write(true)
        .custom_flags(OPEN_REPARSE_POINT)
        .open(&temporary)?;
    if file.metadata()?.file_attributes() & REPARSE_POINT != 0 {
        return Err(std::io::Error::other("Redirected USB temporary file."));
    }
    file.write_all(bytes)?;
    file.sync_all()?;
    drop(file);
    fs::rename(temporary, path)
}
fn write_json(directory: &Path, name: &str, value: &impl Serialize) -> std::io::Result<()> {
    write_bytes(
        directory,
        name,
        &serde_json::to_vec(value).map_err(std::io::Error::other)?,
    )
}
fn write_state(
    directory: &Path,
    state: &str,
    message: &str,
    providers: &[String],
) -> std::io::Result<()> {
    write_json(
        directory,
        "usb-status.json",
        &UsbState {
            state: state.into(),
            message: message.into(),
            providers: providers.to_vec(),
        },
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    struct FakeOps {
        commands: Vec<Vec<String>>,
        result: i32,
        query: TraceQuery,
    }
    impl TraceOps for FakeOps {
        fn run(&mut self, args: &[String]) -> i32 {
            self.commands.push(args.to_vec());
            self.result
        }
        fn query(&mut self, _: &str) -> TraceQuery {
            self.query
        }
    }
    fn directory() -> PathBuf {
        let path = std::env::temp_dir().join(format!(
            "coach-usb-test-{}-{}",
            std::process::id(),
            monotonic().unwrap().0
        ));
        fs::create_dir_all(&path).unwrap();
        path
    }

    #[test]
    fn invalid_helper_arguments_return_before_identity_or_trace_calls() {
        let invalid: Vec<String> = ["--wheel-usb-helper", "../other", "123", "123", "S-1-5-21"]
            .iter()
            .map(|v| (*v).into())
            .collect();
        assert_eq!(run_helper(&invalid), 2);
        let mut valid = invalid;
        valid[1] = "0123456789abcdef0123456789abcdef".into();
        assert!(HelperArgs::parse(&valid).is_some());
        valid[2] = "0".into();
        assert!(HelperArgs::parse(&valid).is_none());
        valid[2] = "123".into();
        valid[4] = "user/path".into();
        assert!(HelperArgs::parse(&valid).is_none());
    }

    #[test]
    fn recovery_arguments_accept_only_parent_identity_and_reject_paths_before_operations() {
        let valid = ["--wheel-usb-recover", "123", "456", "S-1-5-21-1"].map(str::to_owned);
        let parsed = HelperArgs::recovery(&valid).unwrap();
        assert_eq!(parsed.parent_id, 123);
        assert_eq!(parsed.parent_start_filetime, 456);
        assert_eq!(parsed.sid, "S-1-5-21-1");
        for invalid in [
            vec!["--wheel-usb-recover", "0", "456", "S-1-5-21-1"],
            vec!["--wheel-usb-recover", "123", "0", "S-1-5-21-1"],
            vec!["--wheel-usb-recover", "123", "456", "S-1-user"],
            vec![
                "--wheel-usb-recover",
                "123",
                "456",
                "S-1-5-21-1",
                "C:\\other",
            ],
            vec!["--wheel-usb-recover", "C:\\other", "456", "S-1-5-21-1"],
        ] {
            let invalid: Vec<_> = invalid.into_iter().map(str::to_owned).collect();
            assert!(HelperArgs::recovery(&invalid).is_none());
            assert_eq!(run_helper(&invalid), 2);
        }
    }

    #[test]
    fn configured_test_root_rejects_capture_and_recovery_before_an_administrator_prompt() {
        let root = directory();
        let mut client = NativeWheelUsbTrace::with_root(root.clone());
        assert!(
            client
                .recover()
                .unwrap_err()
                .contains("default machine-local")
        );
        assert!(
            client
                .start(&root.join("00000000000000000000000000000001"))
                .unwrap_err()
                .contains("default machine-local")
        );
        assert!(client.helper.is_none());
        assert!(client.can_recover());
        assert!(client.safe_to_export());
        assert!(fs::read_dir(&root).unwrap().next().is_none());
        fs::remove_dir(root).unwrap();
    }

    #[test]
    fn commands_limit_payload_scope_memory_storage_and_session_lifetime() {
        for provider in PROVIDERS {
            let args = start_arguments("owned", Path::new(r"C:\safe\usb.etl"), provider);
            assert_eq!(
                args,
                [
                    "start",
                    "owned",
                    "-ets",
                    "-o",
                    r"C:\safe\usb.etl",
                    "-f",
                    "bincirc",
                    "-max",
                    "32",
                    "-nb",
                    "4",
                    "16",
                    "-bs",
                    "64",
                    "-p",
                    provider,
                    "(Default)"
                ]
            );
            assert_eq!(
                update_arguments("owned", provider),
                ["update", "owned", "-ets", "-p", provider, "(Default)"]
            );
        }
        assert_eq!(stop_arguments("owned"), ["stop", "owned", "-ets"]);
        assert_ne!(
            trace_name("S-1-5-21-1", "session"),
            trace_name("S-1-5-21-2", "session")
        );
        assert_eq!(launch_failure(1223), "Off: administrator prompt cancelled.");
        assert!(launch_failure(5).starts_with("Unavailable:"));
    }

    #[test]
    fn declined_uac_or_launch_unavailability_never_creates_an_active_client() {
        let mut client = NativeWheelUsbTrace::new();
        assert!(client.accept_launch(Err(1223)).is_ok());
        assert_eq!(client.status(), "Off: administrator prompt cancelled.");
        assert!(client.safe_to_export());
        assert!(client.helper.is_none());
        assert!(client.accept_launch(Err(5)).is_err());
        assert_eq!(client.status(), "Unavailable: helper launch failed (5).");
        assert!(client.safe_to_export());
    }

    #[test]
    fn failed_start_and_failed_cleanup_block_export_until_typed_absence() {
        let path = directory();
        let mut ops = FakeOps {
            commands: vec![],
            result: -1,
            query: TraceQuery::Unavailable(5),
        };
        assert_eq!(
            capture_owned_trace(&path, "fake-owned", &mut ops, || true),
            1
        );
        let state: UsbState = read_json(&path.join("usb-status.json")).unwrap();
        assert_eq!(state.state, "Cleanup uncertain");
        assert!(!cleanup_exportable(1, Some(&state)));
        assert!(ops.commands.iter().any(|a| a[0] == "stop"));
        ops.query = TraceQuery::Absent;
        assert_eq!(
            capture_owned_trace(&path, "fake-owned", &mut ops, || true),
            1
        );
        let state: UsbState = read_json(&path.join("usb-status.json")).unwrap();
        assert_eq!(state.state, "Unavailable");
        assert!(cleanup_exportable(1, Some(&state)));
        fs::remove_file(path.join("usb-status.json")).unwrap();
        fs::remove_dir(path).unwrap();
    }

    #[test]
    fn successful_capture_stops_all_providers_and_helper_exit_is_not_proof_of_cleanup() {
        let path = directory();
        let mut ops = FakeOps {
            commands: vec![],
            result: 0,
            query: TraceQuery::Absent,
        };
        let mut calls = 0;
        assert_eq!(
            capture_owned_trace(&path, "fake-owned", &mut ops, || {
                calls += 1;
                calls <= 3
            }),
            0
        );
        let state: UsbState = read_json(&path.join("usb-status.json")).unwrap();
        assert_eq!(state.state, "Stopped");
        assert_eq!(state.providers.len(), 3);
        assert!(cleanup_exportable(0, Some(&state)));
        assert!(!cleanup_exportable(0, None));
        assert!(cleanup_exportable(2, None));
        assert_eq!(ops.commands.last().unwrap(), &stop_arguments("fake-owned"));
        fs::remove_file(path.join("usb-status.json")).unwrap();
        fs::remove_dir(path).unwrap();
    }

    #[test]
    fn recovery_stops_only_flat_same_user_owned_sessions_with_a_dead_parent() {
        let root = directory();
        let sid = "S-1-5-21-1";
        let sessions = [
            "00000000000000000000000000000001",
            "00000000000000000000000000000002",
            "00000000000000000000000000000003",
        ];
        for session in sessions {
            let path = root.join(session);
            fs::create_dir(&path).unwrap();
            write_json(
                &path,
                "usb-owner.json",
                &UsbOwner {
                    parent_id: 123,
                    parent_start_filetime: 456,
                    sid: if session == sessions[1] {
                        "S-1-5-21-2"
                    } else {
                        sid
                    }
                    .into(),
                },
            )
            .unwrap();
        }
        let nested = root.join(sessions[2]).join("nested");
        fs::create_dir(&nested).unwrap();
        let mut ops = FakeOps {
            commands: vec![],
            result: -1,
            query: TraceQuery::Absent,
        };
        recover_with_parent_check(&root, sid, &mut ops, |_| true).unwrap();
        assert!(ops.commands.is_empty());
        recover_with_parent_check(&root, sid, &mut ops, |_| false).unwrap();
        assert_eq!(
            ops.commands,
            vec![stop_arguments(&trace_name(sid, sessions[0]))]
        );
        assert_eq!(
            read_json::<UsbState>(&root.join(sessions[0]).join("usb-status.json"))
                .unwrap()
                .state,
            "Stopped"
        );
        assert!(!root.join(sessions[1]).join("usb-status.json").exists());
        assert!(!root.join(sessions[2]).join("usb-status.json").exists());
        fs::remove_dir(nested).unwrap();
        for session in sessions {
            let path = root.join(session);
            fs::remove_file(path.join("usb-owner.json")).unwrap();
            if path.join("usb-status.json").exists() {
                fs::remove_file(path.join("usb-status.json")).unwrap();
            }
            fs::remove_dir(path).unwrap();
        }
        fs::remove_dir(root).unwrap();
    }

    #[test]
    fn recovery_without_capture_reserve_advances_past_sixteen_and_preserves_trace_data() {
        let root = directory();
        let sid = "S-1-5-21-1";
        let mut paths = Vec::new();
        for id in 1..=18 {
            let path = root.join(format!("{id:032x}"));
            fs::create_dir(&path).unwrap();
            write_json(
                &path,
                "usb-owner.json",
                &UsbOwner {
                    parent_id: 123,
                    parent_start_filetime: 456,
                    sid: sid.into(),
                },
            )
            .unwrap();
            write_state(&path, "Cleanup uncertain", "Interrupted", &[]).unwrap();
            fs::write(path.join("usb.etl"), b"retained trace bytes").unwrap();
            paths.push(path);
        }
        let mut ops = FakeOps {
            commands: vec![],
            result: -1,
            query: TraceQuery::Absent,
        };
        assert!(recover_with_parent_check(&root, sid, &mut ops, |_| false).is_err());
        assert_eq!(ops.commands.len(), 16);
        recover_with_parent_check(&root, sid, &mut ops, |_| false).unwrap();
        assert_eq!(ops.commands.len(), 18);
        assert!(
            ops.commands
                .iter()
                .all(|args| args.len() == 3 && args[0] == "stop" && args[2] == "-ets")
        );
        assert_eq!(fs::read_dir(&root).unwrap().count(), 18);
        for path in paths {
            assert_eq!(
                read_json::<UsbState>(&path.join("usb-status.json"))
                    .unwrap()
                    .state,
                "Stopped"
            );
            assert_eq!(
                fs::read(path.join("usb.etl")).unwrap(),
                b"retained trace bytes"
            );
            for name in ["usb-owner.json", "usb-status.json", "usb.etl"] {
                fs::remove_file(path.join(name)).unwrap();
            }
            fs::remove_dir(path).unwrap();
        }
        fs::remove_dir(root).unwrap();
    }

    #[test]
    fn recovery_parent_revalidation_and_present_query_preserve_uncertainty_and_bytes() {
        let root = directory();
        let sid = "S-1-5-21-1";
        let path = root.join("00000000000000000000000000000001");
        fs::create_dir(&path).unwrap();
        write_json(
            &path,
            "usb-owner.json",
            &UsbOwner {
                parent_id: 123,
                parent_start_filetime: 456,
                sid: sid.into(),
            },
        )
        .unwrap();
        write_state(&path, "Cleanup uncertain", "Interrupted", &[]).unwrap();
        fs::write(path.join("usb.etl"), b"preserved").unwrap();
        let mut ops = FakeOps {
            commands: vec![],
            result: 0,
            query: TraceQuery::Present,
        };
        let authorizations = std::cell::Cell::new(0);
        assert!(
            recover_authorized(
                &root,
                sid,
                &mut ops,
                |_| false,
                || {
                    authorizations.set(authorizations.get() + 1);
                    authorizations.get() == 1 // Parent exits between inspection and native stop.
                }
            )
            .is_err()
        );
        assert!(ops.commands.is_empty());
        assert!(recover_with_parent_check(&root, sid, &mut ops, |_| false).is_err());
        assert_eq!(
            ops.commands,
            vec![stop_arguments(&trace_name(
                sid,
                "00000000000000000000000000000001"
            ))]
        );
        assert_eq!(
            read_json::<UsbState>(&path.join("usb-status.json"))
                .unwrap()
                .state,
            "Cleanup uncertain"
        );
        assert_eq!(fs::read(path.join("usb.etl")).unwrap(), b"preserved");
        let args =
            HelperArgs::recovery(&["--wheel-usb-recover", "123", "456", sid].map(str::to_owned))
                .unwrap();
        let owner: UsbOwner = read_json(&path.join("usb-owner.json")).unwrap();
        assert!(same_owner_parent(&owner, &args));
        assert!(!same_owner_parent(
            &owner,
            &HelperArgs {
                parent_start_filetime: 457,
                ..args
            }
        ));
        for name in ["usb-owner.json", "usb-status.json", "usb.etl"] {
            fs::remove_file(path.join(name)).unwrap();
        }
        fs::remove_dir(path).unwrap();
        fs::remove_dir(root).unwrap();
    }

    #[test]
    fn declining_recovery_never_erases_prior_cleanup_uncertainty() {
        let mut client = NativeWheelUsbTrace::new();
        client.recovering = true;
        client.unconfirmed_cleanup = true;
        assert!(client.accept_launch(Err(1223)).is_err());
        assert!(!client.safe_to_export());
        assert!(client.can_recover());
        assert!(client.status().contains("Cleanup uncertain"));
    }

    #[test]
    fn heartbeat_uses_monotonic_age_and_rejects_future_or_stale_values() {
        assert!(heartbeat_age_fresh(1000, 950, 10));
        assert!(!heartbeat_age_fresh(1000, 1001, 10));
        assert!(!heartbeat_age_fresh(1000, 900, 10));
        assert!(!heartbeat_age_fresh(1000, 950, 0));
        assert!(!heartbeat_age_fresh(i64::MAX, i64::MIN, 10));
    }

    #[test]
    fn manager_stop_intent_survives_heartbeat_while_a_prompt_is_pending() {
        let path = directory();
        prepare_directory(&path).unwrap();
        assert!(heartbeat_fresh(&path));
        request_stop_directory(&path).unwrap();
        heartbeat_directory(&path).unwrap();
        assert!(path.join("usb-stop").exists());
        assert!(heartbeat_fresh(&path));
        prepare_directory(&path).unwrap(); // A subsequent explicit action clears old intent.
        assert!(!path.join("usb-stop").exists());
        fs::remove_file(path.join("usb-heartbeat")).unwrap();
        fs::remove_dir(path).unwrap();
    }

    #[test]
    fn a_present_trace_overrules_successful_tool_prose_and_blocks_export() {
        let path = directory();
        let mut ops = FakeOps {
            commands: vec![],
            result: 0,
            query: TraceQuery::Present,
        };
        let mut calls = 0;
        assert_eq!(
            capture_owned_trace(&path, "fake-owned", &mut ops, || {
                calls += 1;
                calls <= 3
            }),
            1
        );
        let state: UsbState = read_json(&path.join("usb-status.json")).unwrap();
        assert_eq!(state.state, "Cleanup uncertain");
        assert!(!cleanup_exportable(1, Some(&state)));
        assert!(!cleanup_confirmed(0, TraceQuery::Present));
        assert!(!cleanup_confirmed(-1, TraceQuery::Unavailable(5)));
        assert!(cleanup_confirmed(-1, TraceQuery::Absent));
        fs::remove_file(path.join("usb-status.json")).unwrap();
        fs::remove_dir(path).unwrap();
    }

    #[test]
    fn stop_during_provider_startup_enables_no_further_providers_and_confirms_cleanup() {
        let path = directory();
        let mut ops = FakeOps {
            commands: vec![],
            result: 0,
            query: TraceQuery::Absent,
        };
        let mut checks = 0;
        assert_eq!(
            capture_owned_trace(&path, "fake-owned", &mut ops, || {
                checks += 1;
                checks == 1
            }),
            0
        );
        assert_eq!(ops.commands.len(), 2);
        assert_eq!(ops.commands[0][0], "start");
        assert_eq!(ops.commands[1], stop_arguments("fake-owned"));
        let state: UsbState = read_json(&path.join("usb-status.json")).unwrap();
        assert_eq!(state.state, "Stopped");
        assert_eq!(state.providers.len(), 1);
        ops.commands.clear();
        assert_eq!(
            capture_owned_trace(&path, "fake-owned", &mut ops, || false),
            0
        );
        assert!(ops.commands.is_empty());
        assert_eq!(
            read_json::<UsbState>(&path.join("usb-status.json"))
                .unwrap()
                .state,
            "Unavailable"
        );
        fs::remove_file(path.join("usb-status.json")).unwrap();
        fs::remove_dir(path).unwrap();
    }

    #[test]
    fn failed_start_never_layers_another_start_over_a_still_present_session() {
        struct PartialStart {
            commands: Vec<Vec<String>>,
        }
        impl TraceOps for PartialStart {
            fn run(&mut self, args: &[String]) -> i32 {
                self.commands.push(args.to_vec());
                if self.commands.len() == 1 { -1 } else { 0 }
            }
            fn query(&mut self, _: &str) -> TraceQuery {
                TraceQuery::Present
            }
        }
        let path = directory();
        let mut ops = PartialStart { commands: vec![] };
        assert_eq!(
            capture_owned_trace(&path, "fake-owned", &mut ops, || true),
            1
        );
        assert_eq!(
            ops.commands
                .iter()
                .map(|args| args[0].as_str())
                .collect::<Vec<_>>(),
            ["start", "stop", "stop"]
        );
        assert_eq!(
            read_json::<UsbState>(&path.join("usb-status.json"))
                .unwrap()
                .state,
            "Cleanup uncertain"
        );
        fs::remove_file(path.join("usb-status.json")).unwrap();
        fs::remove_dir(path).unwrap();
    }
}

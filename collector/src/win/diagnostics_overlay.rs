//! Independent, low-cost device health overlay. It never acquires input or sends FFB.

use serde::{Deserialize, Serialize};
use std::{
    cell::RefCell,
    path::PathBuf,
    rc::Rc,
    time::{Duration, Instant},
};
use windows::Win32::Foundation::{HWND, LPARAM, LRESULT, POINT, RECT, WPARAM};
use windows::Win32::Graphics::Gdi::*;
use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::UI::Controls::*;
use windows::Win32::UI::HiDpi::GetDpiForWindow;
use windows::Win32::UI::Input::KeyboardAndMouse::{
    GetCapture, ReleaseCapture, SetCapture, TME_LEAVE, TRACKMOUSEEVENT, TrackMouseEvent,
};
use windows::Win32::UI::WindowsAndMessaging::*;
use windows::core::{BOOL, PCWSTR};

use super::{
    dialog_theme::{DialogTheme, DpiAwarenessGuard, palette},
    render::colorref,
    wide,
};
use crate::wheel::{DeviceMonitorStatus, WheelCommand, WheelHandle, WheelStatus};

const CLASS: &str = "CoachDeviceHealthOverlay";
const WIDTH: i32 = 180;
const HEIGHT: i32 = 52;
const INCIDENT_LEFT: i32 = 128;
const INCIDENT_RIGHT: i32 = 172;
const INCIDENT_TOP: i32 = 4;
const INCIDENT_BOTTOM: i32 = 48;
const CLICK_COOLDOWN: Duration = Duration::from_millis(750);
const SAVED_FEEDBACK: Duration = Duration::from_secs(4);
const CLICK_ANIMATION_TIMER: usize = 1;

fn click_animation_duration() -> Duration {
    Duration::from_millis(u64::from(palette().hover_ms) * 2)
}

/// A single small compression/rebound, with a fixed end and no idle animation.
fn click_scale(started: Option<Instant>, now: Instant) -> u16 {
    let Some(started) = started else { return 1000 };
    let elapsed = now.saturating_duration_since(started);
    let duration = click_animation_duration();
    if elapsed >= duration || duration.is_zero() {
        return 1000;
    }
    let progress = elapsed.as_secs_f32() / duration.as_secs_f32();
    let ease_out = |t: f32| 1.0 - (1.0 - t).powi(3);
    if progress < 0.5 {
        (900.0 + 180.0 * ease_out(progress * 2.0)).round() as u16
    } else {
        (1080.0 - 80.0 * ease_out((progress - 0.5) * 2.0)).round() as u16
    }
}

unsafe fn animations_enabled() -> bool {
    let mut enabled = BOOL(0);
    unsafe {
        SystemParametersInfoW(
            SPI_GETCLIENTAREAANIMATION,
            0,
            Some((&mut enabled as *mut BOOL).cast()),
            SYSTEM_PARAMETERS_INFO_UPDATE_FLAGS(0),
        )
        .is_ok()
            && enabled.as_bool()
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum ReportPhase {
    Unavailable,
    Ready,
    Saving,
    Saved,
    Failed,
}

/// Keep a submitted click latched until the recorder acknowledges its sequence.
/// A timer can otherwise overwrite optimistic UI state with a pre-click snapshot.
#[derive(Default)]
struct ReportFeedback {
    submitted_after: Option<u64>,
    observed_sequence: u64,
    was_pending: bool,
    last_click: Option<Instant>,
    saved_until: Option<Instant>,
    submission_failed: bool,
}

impl ReportFeedback {
    fn from_snapshot(status: &WheelStatus) -> Self {
        Self {
            observed_sequence: status.report_sequence,
            was_pending: status.report_pending,
            ..Default::default()
        }
    }

    fn observe(&mut self, status: &WheelStatus, now: Instant) {
        let advanced = status.report_sequence != self.observed_sequence;
        if self
            .submitted_after
            .is_some_and(|sequence| sequence != status.report_sequence)
        {
            self.submitted_after = None;
        }
        if advanced || status.report_pending {
            self.submission_failed = false;
            self.saved_until = None;
        }
        if (advanced || self.was_pending) && !status.report_pending {
            self.saved_until = (status.report_error.is_none() && status.export_path.is_some())
                .then_some(now + SAVED_FEEDBACK);
        }
        self.observed_sequence = status.report_sequence;
        self.was_pending = status.report_pending;
    }

    fn phase(&self, status: &WheelStatus, now: Instant) -> ReportPhase {
        if self.submitted_after.is_some() || status.report_pending {
            ReportPhase::Saving
        } else if self.submission_failed || status.report_error.is_some() {
            ReportPhase::Failed
        } else if self.saved_until.is_some_and(|until| now < until) {
            ReportPhase::Saved
        } else if status.has_capture && status.sample_count > 0 && !status.busy {
            ReportPhase::Ready
        } else {
            ReportPhase::Unavailable
        }
    }

    fn can_submit(&self, status: &WheelStatus, now: Instant) -> bool {
        status.has_capture
            && status.sample_count > 0
            && !status.busy
            && self.submitted_after.is_none()
            && !status.report_pending
            && self
                .last_click
                .is_none_or(|last| now.saturating_duration_since(last) >= CLICK_COOLDOWN)
    }

    fn submitted(&mut self, status: &WheelStatus, now: Instant, accepted: bool) {
        self.last_click = Some(now);
        self.saved_until = None;
        self.submission_failed = !accepted;
        self.submitted_after = accepted.then_some(status.report_sequence);
    }
}

#[derive(Default)]
struct PointerState {
    over_action: bool,
    armed: bool,
}

impl PointerState {
    fn press(&mut self, inside: bool, enabled: bool) -> bool {
        self.over_action = inside;
        self.armed = inside && enabled;
        self.armed
    }

    fn release(&mut self, inside: bool) -> bool {
        self.over_action = inside;
        let clicked = self.armed && inside;
        self.armed = false;
        clicked
    }

    fn cancel(&mut self) {
        self.armed = false;
    }
}

fn action_contains(x: i32, y: i32, dpi: u32) -> bool {
    let px = |value| value * dpi as i32 / 96;
    x >= px(INCIDENT_LEFT)
        && x < px(INCIDENT_RIGHT)
        && y >= px(INCIDENT_TOP)
        && y < px(INCIDENT_BOTTOM)
}

#[derive(Clone, Copy, PartialEq, Eq)]
struct Visual {
    wheel: Health,
    pedals: Health,
    report: ReportPhase,
    hovered: bool,
    pressed: bool,
    click_scale: u16,
}

#[derive(Serialize, Deserialize)]
#[serde(default, rename_all = "PascalCase")]
pub(super) struct UiPreferences {
    pub show_overlay: bool,
    pub left: Option<i32>,
    pub top: Option<i32>,
    #[serde(flatten)]
    extra: serde_json::Map<String, serde_json::Value>,
}

impl Default for UiPreferences {
    fn default() -> Self {
        Self {
            show_overlay: true,
            left: None,
            top: None,
            extra: Default::default(),
        }
    }
}

pub(super) struct DiagnosticsUiState {
    pub preferences: UiPreferences,
    path: Option<PathBuf>,
    pub error: Option<String>,
}

impl DiagnosticsUiState {
    pub fn load(path: Option<PathBuf>) -> Self {
        let mut state = Self {
            preferences: UiPreferences::default(),
            path,
            error: None,
        };
        if let Some(path) = &state.path {
            match std::fs::read(path) {
                Ok(bytes) => match serde_json::from_slice(&bytes) {
                    Ok(preferences) => state.preferences = preferences,
                    Err(_) => state.error = Some("Overlay preferences could not be read.".into()),
                },
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
                Err(_) => state.error = Some("Overlay preferences could not be read.".into()),
            }
        }
        state
    }

    pub fn save(&mut self) {
        let Some(path) = &self.path else {
            return;
        };
        let result = (|| -> std::io::Result<()> {
            if let Some(parent) = path.parent() {
                std::fs::create_dir_all(parent)?;
            }
            let temporary = path.with_extension(format!("{}.tmp", crate::util::uuid_v4()));
            let bytes =
                serde_json::to_vec_pretty(&self.preferences).map_err(std::io::Error::other)?;
            std::fs::write(&temporary, bytes)?;
            // Replace the small UI settings file atomically; a crash keeps the previous version.
            unsafe {
                windows::Win32::Storage::FileSystem::MoveFileExW(
                    PCWSTR(wide(&temporary.to_string_lossy()).as_ptr()),
                    PCWSTR(wide(&path.to_string_lossy()).as_ptr()),
                    windows::Win32::Storage::FileSystem::MOVEFILE_REPLACE_EXISTING
                        | windows::Win32::Storage::FileSystem::MOVEFILE_WRITE_THROUGH,
                )
                .map_err(std::io::Error::other)
            }
        })();
        self.error = result
            .err()
            .map(|_| "Overlay preferences could not be saved.".into());
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) enum Health {
    Disabled,
    Good,
    Bad,
}

pub(super) fn health(running: bool, monitor: &DeviceMonitorStatus) -> Health {
    if !monitor.config.enabled {
        return Health::Disabled;
    }
    if running
        && monitor.config.device.is_some()
        && monitor.availability == "Available"
        && monitor.last_sample_age_ms.is_some_and(|age| age <= 2_000)
        && monitor.axes.iter().any(|axis| axis.value.is_some())
    {
        Health::Good
    } else {
        Health::Bad
    }
}

pub(super) fn health_color(state: Health) -> u32 {
    match state {
        Health::Disabled => palette().muted,
        Health::Good => palette().success,
        Health::Bad => palette().danger,
    }
}

pub(super) fn health_label(running: bool, monitor: &DeviceMonitorStatus) -> &'static str {
    if !monitor.config.enabled {
        "Disabled"
    } else if !running {
        "Stopped"
    } else if monitor.config.device.is_none() {
        "Select device"
    } else if health(running, monitor) == Health::Good {
        "Connected"
    } else if monitor.last_sample_age_ms.is_none() {
        "Waiting"
    } else if monitor.last_sample_age_ms.is_some_and(|age| age > 2_000) {
        "Stale input"
    } else {
        "Unavailable"
    }
}

#[derive(Default)]
pub struct DiagnosticsOverlay {
    window: HWND,
}

impl DiagnosticsOverlay {
    pub(super) fn update(
        &mut self,
        wheel: &WheelHandle,
        snapshot: &WheelStatus,
        ui: Rc<RefCell<DiagnosticsUiState>>,
    ) {
        if !ui.borrow().preferences.show_overlay {
            if !self.window.is_invalid() {
                unsafe {
                    let _ = KillTimer(Some(self.window), CLICK_ANIMATION_TIMER);
                    let pointer = GetWindowLongPtrW(self.window, GWLP_USERDATA) as *mut Overlay;
                    if !pointer.is_null() {
                        (*pointer).click_animation = None;
                    }
                    let _ = ShowWindow(self.window, SW_HIDE);
                }
            }
            return;
        }
        // SAFETY: this object and its native state live exclusively on the companion UI thread.
        unsafe {
            if self.window.is_invalid() {
                match create(wheel.clone(), snapshot.clone(), ui) {
                    Ok(window) => self.window = window,
                    Err(_) => return,
                }
            }
            let pointer = GetWindowLongPtrW(self.window, GWLP_USERDATA) as *mut Overlay;
            if !pointer.is_null() {
                let state = &mut *pointer;
                state.feedback.observe(snapshot, Instant::now());
                state.snapshot = snapshot.clone();
            }
            refresh_window(self.window);
            if !IsWindowVisible(self.window).as_bool() {
                let _ = ShowWindow(self.window, SW_SHOWNOACTIVATE);
            }
        }
    }
}

impl Drop for DiagnosticsOverlay {
    fn drop(&mut self) {
        if !self.window.is_invalid() {
            unsafe {
                let _ = DestroyWindow(self.window);
            }
        }
    }
}

// The overlay has no visible words. Its native accessible name describes the icons and action.
fn action_description(status: &WheelStatus, phase: ReportPhase) -> String {
    match phase {
        ReportPhase::Saving if status.running => "Saving incident. Monitoring continues.".into(),
        ReportPhase::Saving => "Saving incident from the recorded history.".into(),
        ReportPhase::Saved => "Incident saved. Open files in USB diagnostics to review it.".into(),
        ReportPhase::Failed => status.report_error.as_ref().map_or_else(
            || "Could not request an incident report. Try again.".into(),
            |error| format!("{error} Click to retry."),
        ),
        ReportPhase::Unavailable if status.busy => "Recorder is busy. Please wait.".into(),
        ReportPhase::Unavailable if status.running => "Waiting for recorded samples.".into(),
        ReportPhase::Unavailable => {
            "Start recording in USB diagnostics to save an incident.".into()
        }
        ReportPhase::Ready => "Save recent diagnostic history (up to 5 minutes).".into(),
    }
}

fn overlay_title(status: &WheelStatus, phase: ReportPhase) -> String {
    format!(
        "Coach USB diagnostics. Wheel: {}. Pedals: {}. {} Drag the device area or an edge to move.",
        health_label(status.running, &status.wheel),
        health_label(status.running, &status.pedals),
        action_description(status, phase),
    )
}

impl Overlay {
    fn visual(&self, now: Instant) -> Visual {
        Visual {
            wheel: health(self.snapshot.running, &self.snapshot.wheel),
            pedals: health(self.snapshot.running, &self.snapshot.pedals),
            report: self.feedback.phase(&self.snapshot, now),
            hovered: self.pointer.over_action && self.feedback.can_submit(&self.snapshot, now),
            pressed: self.pointer.armed && self.pointer.over_action,
            click_scale: click_scale(self.click_animation, now),
        }
    }
}

/// Update native text and invalidate only after releasing the mutable state borrow.
/// Tooltip and window text messages may synchronously reenter the window procedure.
unsafe fn refresh_window(window: HWND) {
    unsafe {
        let pointer = GetWindowLongPtrW(window, GWLP_USERDATA) as *mut Overlay;
        if pointer.is_null() {
            return;
        }
        let (repaint, title, tooltip, tips) = {
            let state = &mut *pointer;
            let visual = state.visual(Instant::now());
            let repaint = state.painted != Some(visual);
            state.painted = Some(visual);
            let title = overlay_title(&state.snapshot, visual.report);
            let title = if title == state.title {
                None
            } else {
                state.title = title.clone();
                Some(wide(&title))
            };
            let labels = [
                format!(
                    "Wheel: {}",
                    health_label(state.snapshot.running, &state.snapshot.wheel)
                ),
                format!(
                    "Pedals: {}",
                    health_label(state.snapshot.running, &state.snapshot.pedals)
                ),
                action_description(&state.snapshot, visual.report),
            ];
            let mut changed = Vec::new();
            for (index, label) in labels.iter().enumerate() {
                let value = wide(label);
                if state.tip_text[index] != value {
                    let previous = std::mem::replace(&mut state.tip_text[index], value);
                    changed.push((index, state.tip_text[index].as_mut_ptr(), previous));
                }
            }
            (repaint, title, state.tooltip, changed)
        };
        if let Some(title) = title {
            let _ = SetWindowTextW(window, PCWSTR(title.as_ptr()));
        }
        // The tooltip retains its text pointer; keep the prior buffer alive until replacement.
        for (index, value, _previous) in tips {
            let info = TTTOOLINFOW {
                cbSize: std::mem::size_of::<TTTOOLINFOW>() as u32,
                hwnd: window,
                uId: index + 1,
                lpszText: windows::core::PWSTR(value),
                ..Default::default()
            };
            SendMessageW(
                tooltip,
                TTM_UPDATETIPTEXTW,
                None,
                Some(LPARAM((&info as *const TTTOOLINFOW) as isize)),
            );
        }
        if repaint {
            let _ = InvalidateRect(Some(window), None, false);
        }
    }
}

unsafe fn tooltip_layout(window: HWND, add: bool) {
    unsafe {
        let pointer = GetWindowLongPtrW(window, GWLP_USERDATA) as *mut Overlay;
        let state = &*pointer;
        let tooltip = state.tooltip;
        let dpi = state.dpi;
        let font = state.theme.font;
        let values: Vec<_> = state.tip_text.iter().map(|text| text.as_ptr()).collect();
        let px = |value| value * dpi as i32 / 96;
        SendMessageW(
            tooltip,
            WM_SETFONT,
            Some(WPARAM(font.0 as usize)),
            Some(LPARAM(1)),
        );
        for (index, (left, right)) in [(16, 58), (62, 110), (INCIDENT_LEFT, INCIDENT_RIGHT)]
            .iter()
            .enumerate()
        {
            let info = TTTOOLINFOW {
                cbSize: std::mem::size_of::<TTTOOLINFOW>() as u32,
                uFlags: TTF_SUBCLASS,
                hwnd: window,
                uId: index + 1,
                rect: RECT {
                    left: px(*left),
                    top: px(INCIDENT_TOP),
                    right: px(*right),
                    bottom: px(INCIDENT_BOTTOM),
                },
                lpszText: windows::core::PWSTR(values[index] as *mut u16),
                ..Default::default()
            };
            SendMessageW(
                tooltip,
                if add { TTM_ADDTOOLW } else { TTM_NEWTOOLRECTW },
                None,
                Some(LPARAM((&info as *const TTTOOLINFOW) as isize)),
            );
        }
    };
}

struct Overlay {
    wheel: WheelHandle,
    snapshot: WheelStatus,
    ui: Rc<RefCell<DiagnosticsUiState>>,
    dpi: u32,
    feedback: ReportFeedback,
    pointer: PointerState,
    click_animation: Option<Instant>,
    painted: Option<Visual>,
    title: String,
    tooltip: HWND,
    tip_text: [Vec<u16>; 3],
    theme: DialogTheme,
}

unsafe fn create(
    wheel: WheelHandle,
    snapshot: WheelStatus,
    ui: Rc<RefCell<DiagnosticsUiState>>,
) -> windows::core::Result<HWND> {
    // SAFETY: standard native class and owned state; no hardware or shell calls.
    unsafe {
        let _dpi_context = DpiAwarenessGuard::per_monitor_v2()?;
        let instance = GetModuleHandleW(PCWSTR::null())?;
        let class = wide(CLASS);
        RegisterClassW(&WNDCLASSW {
            hInstance: instance.into(),
            lpfnWndProc: Some(window_proc),
            lpszClassName: PCWSTR(class.as_ptr()),
            hCursor: LoadCursorW(None, IDC_ARROW)?,
            ..Default::default()
        });
        let mut area = RECT::default();
        let _ = SystemParametersInfoW(
            SPI_GETWORKAREA,
            0,
            Some((&mut area as *mut RECT).cast()),
            SYSTEM_PARAMETERS_INFO_UPDATE_FLAGS(0),
        );
        let (x, y) = {
            let ui = ui.borrow();
            (
                ui.preferences.left.unwrap_or(area.right - WIDTH - 24),
                ui.preferences.top.unwrap_or(area.top + 24),
            )
        };
        // Clamp a disconnected monitor's saved position back to an available work area.
        let mut rect = RECT {
            left: x,
            top: y,
            right: x.saturating_add(WIDTH),
            bottom: y.saturating_add(HEIGHT),
        };
        let monitor = MonitorFromRect(&rect, MONITOR_DEFAULTTONEAREST);
        let mut info = MONITORINFO {
            cbSize: std::mem::size_of::<MONITORINFO>() as u32,
            ..Default::default()
        };
        let _ = GetMonitorInfoW(monitor, &mut info);
        rect.left = rect.left.clamp(
            info.rcWork.left,
            (info.rcWork.right - WIDTH).max(info.rcWork.left),
        );
        rect.top = rect.top.clamp(
            info.rcWork.top,
            (info.rcWork.bottom - HEIGHT).max(info.rcWork.top),
        );
        let window = CreateWindowExW(
            WS_EX_TOPMOST | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW,
            PCWSTR(class.as_ptr()),
            PCWSTR(wide("Coach USB diagnostics").as_ptr()),
            WS_POPUP,
            rect.left,
            rect.top,
            WIDTH,
            HEIGHT,
            None,
            None,
            Some(instance.into()),
            None,
        )?;
        let dpi = GetDpiForWindow(window).max(96);
        let feedback = ReportFeedback::from_snapshot(&snapshot);
        let _ = InitCommonControlsEx(&INITCOMMONCONTROLSEX {
            dwSize: std::mem::size_of::<INITCOMMONCONTROLSEX>() as u32,
            dwICC: ICC_WIN95_CLASSES,
        });
        let tooltip = CreateWindowExW(
            WS_EX_TOPMOST | WS_EX_NOACTIVATE,
            TOOLTIPS_CLASSW,
            PCWSTR::null(),
            WS_POPUP | WINDOW_STYLE(TTS_ALWAYSTIP | TTS_NOPREFIX),
            CW_USEDEFAULT,
            CW_USEDEFAULT,
            CW_USEDEFAULT,
            CW_USEDEFAULT,
            Some(window),
            None,
            Some(instance.into()),
            None,
        )
        .unwrap_or_default();
        let _ = SetWindowTheme(
            tooltip,
            PCWSTR(wide("").as_ptr()),
            PCWSTR(wide("").as_ptr()),
        );
        SendMessageW(
            tooltip,
            TTM_SETTIPBKCOLOR,
            Some(WPARAM(colorref(palette().surface).0 as usize)),
            None,
        );
        SendMessageW(
            tooltip,
            TTM_SETTIPTEXTCOLOR,
            Some(WPARAM(colorref(palette().text).0 as usize)),
            None,
        );
        SendMessageW(tooltip, TTM_SETMAXTIPWIDTH, None, Some(LPARAM(320)));
        SendMessageW(
            tooltip,
            TTM_SETDELAYTIME,
            Some(WPARAM(TTDT_INITIAL as usize)),
            Some(LPARAM(700)),
        );
        SendMessageW(
            tooltip,
            TTM_SETDELAYTIME,
            Some(WPARAM(TTDT_AUTOPOP as usize)),
            Some(LPARAM(4000)),
        );
        SetWindowLongPtrW(
            window,
            GWLP_USERDATA,
            Box::into_raw(Box::new(Overlay {
                wheel,
                snapshot,
                ui,
                dpi,
                feedback,
                pointer: PointerState::default(),
                click_animation: None,
                painted: None,
                title: String::new(),
                tooltip,
                tip_text: [wide("Wheel"), wide("Pedals"), wide("Save incident")],
                theme: DialogTheme::new(dpi),
            })) as isize,
        );
        tooltip_layout(window, true);
        round_window(window, dpi);
        refresh_window(window);
        let width = WIDTH * dpi as i32 / 96;
        let height = HEIGHT * dpi as i32 / 96;
        rect.left = rect.left.clamp(
            info.rcWork.left,
            (info.rcWork.right - width).max(info.rcWork.left),
        );
        rect.top = rect.top.clamp(
            info.rcWork.top,
            (info.rcWork.bottom - height).max(info.rcWork.top),
        );
        let _ = SetWindowPos(
            window,
            Some(HWND_TOPMOST),
            rect.left,
            rect.top,
            width,
            height,
            SWP_NOACTIVATE,
        );
        Ok(window)
    }
}

unsafe extern "system" fn window_proc(
    window: HWND,
    message: u32,
    wparam: WPARAM,
    lparam: LPARAM,
) -> LRESULT {
    // Native calls can synchronously send more messages. Keep state borrows inside
    // short scopes and release them before capture, position or text operations.
    unsafe {
        if message == WM_MOUSEACTIVATE {
            return LRESULT(MA_NOACTIVATE as isize);
        }
        if message == WM_ERASEBKGND {
            return LRESULT(1);
        }
        if !matches!(
            message,
            WM_PAINT
                | WM_TIMER
                | WM_NCHITTEST
                | WM_LBUTTONDOWN
                | WM_LBUTTONUP
                | WM_MOUSEMOVE
                | WM_MOUSELEAVE
                | WM_CAPTURECHANGED
                | WM_CANCELMODE
                | WM_SETCURSOR
                | WM_EXITSIZEMOVE
                | WM_DPICHANGED
                | WM_NCDESTROY
        ) {
            return DefWindowProcW(window, message, wparam, lparam);
        }
        let pointer = GetWindowLongPtrW(window, GWLP_USERDATA) as *mut Overlay;
        if pointer.is_null() {
            return DefWindowProcW(window, message, wparam, lparam);
        }
        match message {
            WM_PAINT => {
                paint(window, &*pointer);
                LRESULT(0)
            }
            WM_NCHITTEST => {
                let mut rect = RECT::default();
                let _ = GetWindowRect(window, &mut rect);
                let x = (lparam.0 as u32 & 0xffff) as i16 as i32 - rect.left;
                let y = ((lparam.0 as u32 >> 16) & 0xffff) as i16 as i32 - rect.top;
                let edge = 4 * (*pointer).dpi as i32 / 96;
                if x < edge
                    || y < edge
                    || x >= rect.right - rect.left - edge
                    || y >= rect.bottom - rect.top - edge
                {
                    LRESULT(HTCAPTION as isize)
                } else {
                    LRESULT(HTCLIENT as isize)
                }
            }
            WM_MOUSEMOVE => {
                let x = (lparam.0 as u32 & 0xffff) as i16 as i32;
                let y = ((lparam.0 as u32 >> 16) & 0xffff) as i16 as i32;
                let over_action = action_contains(x, y, (*pointer).dpi);
                let changed = (*pointer).pointer.over_action != over_action;
                (*pointer).pointer.over_action = over_action;
                let mut tracking = TRACKMOUSEEVENT {
                    cbSize: std::mem::size_of::<TRACKMOUSEEVENT>() as u32,
                    dwFlags: TME_LEAVE,
                    hwndTrack: window,
                    ..Default::default()
                };
                let _ = TrackMouseEvent(&mut tracking);
                if changed {
                    refresh_window(window);
                }
                LRESULT(0)
            }
            WM_MOUSELEAVE => {
                (*pointer).pointer.over_action = false;
                refresh_window(window);
                LRESULT(0)
            }
            WM_LBUTTONDOWN => {
                let x = (lparam.0 as u32 & 0xffff) as i16 as i32;
                let y = ((lparam.0 as u32 >> 16) & 0xffff) as i16 as i32;
                let (inside, armed, tooltip) = {
                    let state = &mut *pointer;
                    let inside = action_contains(x, y, state.dpi);
                    let enabled = state.feedback.can_submit(&state.snapshot, Instant::now());
                    (inside, state.pointer.press(inside, enabled), state.tooltip)
                };
                SendMessageW(tooltip, TTM_POP, None, None);
                if armed {
                    SetCapture(window);
                    refresh_window(window);
                } else if !inside {
                    // The device area also works as a generous drag handle.
                    let mut point = POINT { x, y };
                    let _ = ClientToScreen(window, &mut point);
                    let screen = ((point.y as u32 & 0xffff) << 16) | (point.x as u32 & 0xffff);
                    SendMessageW(
                        window,
                        WM_NCLBUTTONDOWN,
                        Some(WPARAM(HTCAPTION as usize)),
                        Some(LPARAM(screen as isize)),
                    );
                }
                LRESULT(0)
            }
            WM_LBUTTONUP => {
                let x = (lparam.0 as u32 & 0xffff) as i16 as i32;
                let y = ((lparam.0 as u32 >> 16) & 0xffff) as i16 as i32;
                let (had_capture, accepted_click) = {
                    let state = &mut *pointer;
                    let had_capture = state.pointer.armed;
                    let mut accepted_click = false;
                    let now = Instant::now();
                    if state.pointer.release(action_contains(x, y, state.dpi))
                        && state.feedback.can_submit(&state.snapshot, now)
                    {
                        let accepted = state.wheel.send(WheelCommand::ReportDiagnosticIncident);
                        state.feedback.submitted(&state.snapshot, now, accepted);
                        accepted_click = accepted;
                    }
                    (had_capture, accepted_click)
                };
                if had_capture {
                    let _ = ReleaseCapture();
                }
                if accepted_click && animations_enabled() {
                    (*pointer).click_animation = Some(Instant::now());
                    if SetTimer(Some(window), CLICK_ANIMATION_TIMER, 16, None) == 0 {
                        (*pointer).click_animation = None;
                    }
                }
                refresh_window(window);
                LRESULT(0)
            }
            WM_TIMER if wparam.0 == CLICK_ANIMATION_TIMER => {
                let now = Instant::now();
                if (*pointer).click_animation.is_none_or(|start| {
                    now.saturating_duration_since(start) >= click_animation_duration()
                }) {
                    (*pointer).click_animation = None;
                    let _ = KillTimer(Some(window), CLICK_ANIMATION_TIMER);
                }
                refresh_window(window);
                LRESULT(0)
            }
            WM_CAPTURECHANGED | WM_CANCELMODE => {
                (*pointer).pointer.cancel();
                if message == WM_CANCELMODE && GetCapture() == window {
                    let _ = ReleaseCapture();
                }
                refresh_window(window);
                LRESULT(0)
            }
            WM_SETCURSOR => {
                let mut point = POINT::default();
                let _ = GetCursorPos(&mut point);
                let _ = ScreenToClient(window, &mut point);
                let state = &*pointer;
                let cursor = if action_contains(point.x, point.y, state.dpi) {
                    if state.feedback.can_submit(&state.snapshot, Instant::now()) {
                        IDC_HAND
                    } else {
                        IDC_ARROW
                    }
                } else {
                    IDC_SIZEALL
                };
                SetCursor(LoadCursorW(None, cursor).ok());
                LRESULT(1)
            }
            WM_EXITSIZEMOVE => {
                save_position(window, pointer);
                LRESULT(0)
            }
            WM_DPICHANGED => {
                let dpi = (wparam.0 & 0xffff) as u32;
                let old_theme = {
                    let state = &mut *pointer;
                    state.dpi = dpi;
                    state.painted = None;
                    state.pointer.cancel();
                    std::mem::replace(&mut state.theme, DialogTheme::new(dpi))
                };
                if GetCapture() == window {
                    let _ = ReleaseCapture();
                }
                let rect = &*(lparam.0 as *const RECT);
                let (left, top) = clamp_position(rect.left, rect.top, dpi);
                let _ = SetWindowPos(
                    window,
                    None,
                    left,
                    top,
                    WIDTH * dpi as i32 / 96,
                    HEIGHT * dpi as i32 / 96,
                    SWP_NOACTIVATE | SWP_NOZORDER,
                );
                tooltip_layout(window, false);
                round_window(window, dpi);
                save_position(window, pointer);
                drop(old_theme);
                refresh_window(window);
                LRESULT(0)
            }
            WM_NCDESTROY => {
                let _ = KillTimer(Some(window), CLICK_ANIMATION_TIMER);
                SetWindowLongPtrW(window, GWLP_USERDATA, 0);
                let state = Box::from_raw(pointer);
                if IsWindow(Some(state.tooltip)).as_bool() {
                    let _ = DestroyWindow(state.tooltip);
                }
                drop(state);
                DefWindowProcW(window, message, wparam, lparam)
            }
            _ => DefWindowProcW(window, message, wparam, lparam),
        }
    }
}

unsafe fn clamp_position(left: i32, top: i32, dpi: u32) -> (i32, i32) {
    unsafe {
        let width = WIDTH * dpi as i32 / 96;
        let height = HEIGHT * dpi as i32 / 96;
        let rect = RECT {
            left,
            top,
            right: left.saturating_add(width),
            bottom: top.saturating_add(height),
        };
        let monitor = MonitorFromRect(&rect, MONITOR_DEFAULTTONEAREST);
        let mut info = MONITORINFO {
            cbSize: std::mem::size_of::<MONITORINFO>() as u32,
            ..Default::default()
        };
        if !GetMonitorInfoW(monitor, &mut info).as_bool() {
            return (left, top);
        }
        (
            left.clamp(
                info.rcWork.left,
                (info.rcWork.right - width).max(info.rcWork.left),
            ),
            top.clamp(
                info.rcWork.top,
                (info.rcWork.bottom - height).max(info.rcWork.top),
            ),
        )
    }
}

unsafe fn save_position(window: HWND, pointer: *mut Overlay) {
    unsafe {
        let mut rect = RECT::default();
        if GetWindowRect(window, &mut rect).is_ok() {
            let mut ui = (*pointer).ui.borrow_mut();
            ui.preferences.left = Some(rect.left);
            ui.preferences.top = Some(rect.top);
            ui.save();
        }
    }
}

unsafe fn round_window(window: HWND, dpi: u32) {
    unsafe {
        let px = |value| value * dpi as i32 / 96;
        let region = CreateRoundRectRgn(0, 0, px(WIDTH) + 1, px(HEIGHT) + 1, px(18), px(18));
        // A successful SetWindowRgn transfers ownership to Windows.
        if SetWindowRgn(window, Some(region), true) == 0 {
            let _ = DeleteObject(HGDIOBJ(region.0));
        }
    }
}

unsafe fn paint(window: HWND, state: &Overlay) {
    // Render small vectors at 3x and downsample once. Repaints happen only on state
    // or pointer changes, plus the brief accepted-click animation. Nothing animates at idle.
    unsafe {
        let mut ps = PAINTSTRUCT::default();
        let dc = BeginPaint(window, &mut ps);
        let mut bounds = RECT::default();
        let _ = GetClientRect(window, &mut bounds);
        let width = bounds.right.max(1);
        let height = bounds.bottom.max(1);
        let memory = CreateCompatibleDC(Some(dc));
        let bitmap = CreateCompatibleBitmap(dc, width * 3, height * 3);
        let previous = SelectObject(memory, HGDIOBJ(bitmap.0));
        let canvas = RECT {
            left: 0,
            top: 0,
            right: width * 3,
            bottom: height * 3,
        };
        let px = |value: i32| value * state.dpi as i32 * 3 / 96;
        fill(memory, canvas, palette().app);
        rounded(
            memory,
            RECT {
                left: px(1),
                top: px(1),
                right: canvas.right - px(1),
                bottom: canvas.bottom - px(1),
            },
            palette().surface,
            palette().surface_hover,
            px(18),
        );
        let visual = state.visual(Instant::now());
        device_symbol(
            memory,
            px(36),
            px(26),
            px(12),
            false,
            health_color(visual.wheel),
        );
        device_symbol(
            memory,
            px(86),
            px(26),
            px(12),
            true,
            health_color(visual.pedals),
        );
        fill(
            memory,
            RECT {
                left: px(116),
                top: px(16),
                right: px(117),
                bottom: px(36),
            },
            palette().surface_hover,
        );
        let background = if visual.pressed {
            palette().accent_fill
        } else if visual.hovered {
            palette().surface_hover
        } else {
            palette().surface
        };
        if visual.hovered || visual.pressed {
            rounded(
                memory,
                RECT {
                    left: px(INCIDENT_LEFT),
                    top: px(INCIDENT_TOP),
                    right: px(INCIDENT_RIGHT),
                    bottom: px(INCIDENT_BOTTOM),
                },
                background,
                background,
                px(12),
            );
        }
        let stroke = match visual.report {
            ReportPhase::Saving => palette().warning,
            ReportPhase::Saved => palette().success,
            ReportPhase::Failed => palette().danger,
            ReportPhase::Ready => palette().danger,
            ReportPhase::Unavailable => palette().muted,
        };
        incident_symbol(
            memory,
            px(150),
            px(26 + i32::from(visual.pressed)),
            px(12) * i32::from(visual.click_scale) / 1000,
            stroke,
            background,
            px(2),
        );
        if matches!(
            visual.report,
            ReportPhase::Saving | ReportPhase::Saved | ReportPhase::Failed
        ) {
            rounded(
                memory,
                RECT {
                    left: px(143),
                    top: px(43),
                    right: px(157),
                    bottom: px(45),
                },
                stroke,
                stroke,
                px(2),
            );
        }
        let previous_mode = SetStretchBltMode(dc, HALFTONE);
        let mut previous_origin = POINT::default();
        let _ = SetBrushOrgEx(dc, 0, 0, Some(&mut previous_origin));
        let _ = StretchBlt(
            dc,
            0,
            0,
            width,
            height,
            Some(memory),
            0,
            0,
            width * 3,
            height * 3,
            SRCCOPY,
        );
        let _ = SetBrushOrgEx(dc, previous_origin.x, previous_origin.y, None);
        SetStretchBltMode(dc, STRETCH_BLT_MODE(previous_mode));
        SelectObject(memory, previous);
        let _ = DeleteObject(HGDIOBJ(bitmap.0));
        let _ = DeleteDC(memory);
        let _ = EndPaint(window, &ps);
    }
}

/// A vector octagonal exclamation icon: no font, text label, or status decoration.
unsafe fn incident_symbol(
    dc: HDC,
    x: i32,
    y: i32,
    radius: i32,
    stroke: u32,
    background: u32,
    thickness: i32,
) {
    unsafe {
        let corner = radius * 2 / 5;
        let points = [
            windows::Win32::Foundation::POINT {
                x: x - corner,
                y: y - radius,
            },
            windows::Win32::Foundation::POINT {
                x: x + corner,
                y: y - radius,
            },
            windows::Win32::Foundation::POINT {
                x: x + radius,
                y: y - corner,
            },
            windows::Win32::Foundation::POINT {
                x: x + radius,
                y: y + corner,
            },
            windows::Win32::Foundation::POINT {
                x: x + corner,
                y: y + radius,
            },
            windows::Win32::Foundation::POINT {
                x: x - corner,
                y: y + radius,
            },
            windows::Win32::Foundation::POINT {
                x: x - radius,
                y: y + corner,
            },
            windows::Win32::Foundation::POINT {
                x: x - radius,
                y: y - corner,
            },
        ];
        let pen = CreatePen(PS_SOLID, thickness, colorref(stroke));
        let brush = CreateSolidBrush(colorref(background));
        let old_pen = SelectObject(dc, HGDIOBJ(pen.0));
        let old_brush = SelectObject(dc, HGDIOBJ(brush.0));
        let _ = Polygon(dc, &points);
        let _ = MoveToEx(dc, x, y - radius / 2, None);
        let _ = LineTo(dc, x, y + radius / 6);
        SelectObject(dc, old_brush);
        let _ = DeleteObject(HGDIOBJ(brush.0));
        let dot = CreateSolidBrush(colorref(stroke));
        SelectObject(dc, HGDIOBJ(dot.0));
        let dot_radius = (thickness / 2).max(1);
        let dot_y = y + radius / 2;
        let _ = Ellipse(
            dc,
            x - dot_radius,
            dot_y - dot_radius,
            x + dot_radius + 1,
            dot_y + dot_radius + 1,
        );
        SelectObject(dc, old_brush);
        SelectObject(dc, old_pen);
        let _ = DeleteObject(HGDIOBJ(dot.0));
        let _ = DeleteObject(HGDIOBJ(pen.0));
    }
}

pub(super) unsafe fn fill(dc: HDC, rect: RECT, color: u32) {
    unsafe {
        let brush = CreateSolidBrush(colorref(color));
        FillRect(dc, &rect, brush);
        let _ = DeleteObject(HGDIOBJ(brush.0));
    }
}

pub(super) unsafe fn rounded(dc: HDC, rect: RECT, background: u32, border: u32, radius: i32) {
    unsafe {
        let brush = CreateSolidBrush(colorref(background));
        let pen = CreatePen(PS_SOLID, 1, colorref(border));
        let old_brush = SelectObject(dc, HGDIOBJ(brush.0));
        let old_pen = SelectObject(dc, HGDIOBJ(pen.0));
        let _ = RoundRect(
            dc,
            rect.left,
            rect.top,
            rect.right,
            rect.bottom,
            radius,
            radius,
        );
        SelectObject(dc, old_brush);
        SelectObject(dc, old_pen);
        let _ = DeleteObject(HGDIOBJ(brush.0));
        let _ = DeleteObject(HGDIOBJ(pen.0));
    }
}

pub(super) unsafe fn text(
    dc: HDC,
    font: HFONT,
    value: &str,
    mut rect: RECT,
    color: u32,
    flags: DRAW_TEXT_FORMAT,
) {
    // DrawTextW's ellipsis path can inspect the first code unit even with cchText == 0.
    // An empty Vec has a non-dereferenceable dangling pointer, so never pass it to USER32.
    if value.is_empty() || rect.right <= rect.left || rect.bottom <= rect.top {
        return;
    }
    unsafe {
        let old = SelectObject(dc, HGDIOBJ(font.0));
        SetBkMode(dc, TRANSPARENT);
        SetTextColor(dc, colorref(color));
        let mut value: Vec<_> = value.encode_utf16().collect();
        DrawTextW(
            dc,
            &mut value,
            &mut rect,
            flags | DT_NOPREFIX | DT_END_ELLIPSIS,
        );
        SelectObject(dc, old);
    }
}

pub(super) unsafe fn device_symbol(dc: HDC, x: i32, y: i32, radius: i32, pedals: bool, color: u32) {
    unsafe {
        let pen = CreatePen(PS_SOLID, (radius / 7).max(2), colorref(color));
        let old_pen = SelectObject(dc, HGDIOBJ(pen.0));
        let old_brush = SelectObject(dc, GetStockObject(NULL_BRUSH));
        if pedals {
            let width = (radius * 2 / 5).max(3);
            for offset in [-radius, -width / 2, radius - width] {
                let _ = RoundRect(
                    dc,
                    x + offset,
                    y - radius + 2,
                    x + offset + width,
                    y + radius - 2,
                    3,
                    3,
                );
            }
            let _ = MoveToEx(dc, x - radius - 2, y + radius + 2, None);
            let _ = LineTo(dc, x + radius + 2, y + radius + 2);
        } else {
            let _ = Ellipse(dc, x - radius, y - radius, x + radius, y + radius);
            let _ = Ellipse(
                dc,
                x - radius / 4,
                y - radius / 4,
                x + radius / 4,
                y + radius / 4,
            );
            for (to_x, to_y) in [(x - radius, y), (x + radius, y), (x, y + radius)] {
                let _ = MoveToEx(dc, x, y, None);
                let _ = LineTo(dc, to_x, to_y);
            }
        }
        SelectObject(dc, old_brush);
        SelectObject(dc, old_pen);
        let _ = DeleteObject(HGDIOBJ(pen.0));
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn overlay_preferences_preserve_unknown_fields() {
        let source =
            br#"{"ShowOverlay":false,"Left":120,"Top":44,"FutureOption":{"retained":true}}"#;
        let preferences: UiPreferences = serde_json::from_slice(source).unwrap();
        let roundtrip = serde_json::to_value(preferences).unwrap();
        assert_eq!(roundtrip["FutureOption"]["retained"], true);
        assert_eq!(roundtrip["ShowOverlay"], false);
    }

    #[test]
    fn icon_only_overlay_keeps_action_and_feedback_in_native_accessible_name() {
        let mut status = WheelStatus::default();
        assert!(overlay_title(&status, ReportPhase::Ready).contains("up to 5 minutes"));
        assert!(overlay_title(&status, ReportPhase::Saving).contains("Saving incident"));
        assert!(overlay_title(&status, ReportPhase::Saved).contains("Incident saved"));
        status.report_error = Some("Disk unavailable".into());
        assert!(overlay_title(&status, ReportPhase::Failed).contains("Disk unavailable"));
    }

    #[test]
    fn report_click_stays_latched_through_stale_snapshots_and_acknowledgement() {
        let now = Instant::now();
        let mut status = WheelStatus {
            has_capture: true,
            sample_count: 100,
            export_path: Some(PathBuf::from("earlier-incident.zip")),
            ..Default::default()
        };
        let mut feedback = ReportFeedback::from_snapshot(&status);
        assert_eq!(feedback.phase(&status, now), ReportPhase::Ready);
        feedback.submitted(&status, now, true);
        for tick in 1..=5 {
            let at = now + Duration::from_millis(tick * 500);
            feedback.observe(&status, at);
            assert_eq!(feedback.phase(&status, at), ReportPhase::Saving);
            assert!(!feedback.can_submit(&status, at));
        }
        status.report_sequence += 1;
        status.report_pending = true;
        feedback.observe(&status, now + Duration::from_secs(3));
        assert_eq!(feedback.phase(&status, now), ReportPhase::Saving);
        status.report_pending = false;
        status.export_path = Some(PathBuf::from("new-incident.zip"));
        feedback.observe(&status, now + Duration::from_secs(4));
        assert_eq!(
            feedback.phase(&status, now + Duration::from_secs(4)),
            ReportPhase::Saved
        );
        assert_eq!(
            feedback.phase(&status, now + Duration::from_secs(9)),
            ReportPhase::Ready
        );
    }

    #[test]
    fn fast_completion_does_not_turn_double_click_into_two_reports() {
        let now = Instant::now();
        let mut status = WheelStatus {
            has_capture: true,
            sample_count: 1,
            ..Default::default()
        };
        let mut feedback = ReportFeedback::from_snapshot(&status);
        feedback.submitted(&status, now, true);
        status.report_sequence = 1;
        status.export_path = Some(PathBuf::from("saved.zip"));
        feedback.observe(&status, now + Duration::from_millis(20));
        assert!(!feedback.can_submit(&status, now + Duration::from_millis(100)));
        assert!(feedback.can_submit(&status, now + Duration::from_secs(1)));
        feedback.submitted(&status, now + Duration::from_secs(1), true);
        status.report_sequence = 2;
        status.report_error = Some("Disk full".into());
        feedback.observe(&status, now + Duration::from_secs(2));
        assert_eq!(
            feedback.phase(&status, now + Duration::from_secs(2)),
            ReportPhase::Failed
        );
        assert!(
            feedback.saved_until.is_none(),
            "an old ZIP is not evidence that a new save worked"
        );
    }

    #[test]
    fn incident_requires_a_paired_press_and_release_inside_its_full_bounds() {
        let mut pointer = PointerState::default();
        assert!(
            !pointer.release(true),
            "release from another window is not a click"
        );
        assert!(!pointer.press(false, true));
        assert!(!pointer.release(true));
        assert!(pointer.press(true, true));
        assert!(!pointer.release(false), "dragging off cancels");
        assert!(pointer.press(true, true));
        pointer.cancel();
        assert!(!pointer.release(true), "capture loss cancels");
        assert!(!pointer.press(true, false));
        assert!(!pointer.release(true));
        assert!(pointer.press(true, true));
        assert!(pointer.release(true));
        assert!(!pointer.release(true));
        for dpi in [96, 144, 192] {
            let px = |value| value * dpi as i32 / 96;
            assert!(action_contains(px(150), px(26), dpi));
            assert!(!action_contains(px(150), px(2), dpi));
            assert!(!action_contains(px(150), px(50), dpi));
            assert!(!action_contains(px(100), px(26), dpi));
            assert!(!action_contains(px(179), px(26), dpi));
        }
    }

    #[test]
    fn device_health_requires_enabled_recent_successful_input() {
        let mut status = WheelStatus::default();
        assert_eq!(health(true, &status.wheel), Health::Bad);
        status.wheel.config.device = Some(crate::wheel::WheelDevice {
            id: "software-only".into(),
            name: "Fake wheel".into(),
            product_guid: None,
        });
        status.wheel.availability = "Available".into();
        status.wheel.last_sample_age_ms = Some(20);
        assert_eq!(
            health(true, &status.wheel),
            Health::Bad,
            "driver state alone does not prove an axis was read"
        );
        status.wheel.axes.push(crate::wheel::AxisStatus {
            axis: 0,
            value: Some(0),
            ..Default::default()
        });
        assert_eq!(health(true, &status.wheel), Health::Good);
        status.wheel.last_sample_age_ms = Some(2001);
        assert_eq!(health(true, &status.wheel), Health::Bad);
        status.wheel.config.enabled = false;
        assert_eq!(health(true, &status.wheel), Health::Disabled);
    }

    #[test]
    fn independent_overlay_software_preview_and_position_restore() {
        use super::super::wheel_panel::tests::{handle, save_bitmap};
        let wheel = handle();
        let mut snapshot = WheelStatus {
            running: true,
            has_capture: true,
            sample_count: 100,
            ..Default::default()
        };
        snapshot.wheel.config.device = Some(crate::wheel::WheelDevice {
            id: "fake".into(),
            name: "Fake wheel".into(),
            product_guid: None,
        });
        snapshot.wheel.availability = "Available".into();
        snapshot.wheel.last_sample_age_ms = Some(20);
        snapshot.wheel.axes.push(crate::wheel::AxisStatus {
            axis: 0,
            value: Some(12),
            ..Default::default()
        });
        snapshot.pedals = snapshot.wheel.clone();
        snapshot.pedals.config.device.as_mut().unwrap().name = "Fake pedals".into();
        let ui = Rc::new(RefCell::new(DiagnosticsUiState::load(None)));
        let mut overlay = DiagnosticsOverlay::default();
        overlay.update(&wheel, &snapshot, ui.clone());
        unsafe {
            assert!(IsWindowVisible(overlay.window).as_bool());
            let region = CreateRectRgn(0, 0, 0, 0);
            assert!(!region.is_invalid());
            assert_eq!(GetWindowRgn(overlay.window, region), COMPLEXREGION);
            let mut region_bounds = RECT::default();
            assert_eq!(GetRgnBox(region, &mut region_bounds), COMPLEXREGION);
            for (x, y) in [
                (region_bounds.left, region_bounds.top),
                (region_bounds.right - 1, region_bounds.top),
                (region_bounds.left, region_bounds.bottom - 1),
                (region_bounds.right - 1, region_bounds.bottom - 1),
            ] {
                assert!(
                    !PtInRegion(region, x, y).as_bool(),
                    "corner lies outside the actual window"
                );
            }
            assert!(
                PtInRegion(
                    region,
                    (region_bounds.left + region_bounds.right) / 2,
                    (region_bounds.top + region_bounds.bottom) / 2
                )
                .as_bool()
            );
            let _ = DeleteObject(HGDIOBJ(region.0));
            if let Ok(path) = std::env::var("COACH_DIAGNOSTICS_OVERLAY_SNAPSHOT") {
                save_bitmap(overlay.window, std::path::Path::new(&path));
            }
            let pointer = GetWindowLongPtrW(overlay.window, GWLP_USERDATA) as *mut Overlay;
            let click_x = 150 * (*pointer).dpi / 96;
            let click_y = 26 * (*pointer).dpi / 96;
            let at = LPARAM(((click_y << 16) | click_x) as isize);
            let foreground = GetForegroundWindow();
            SendMessageW(overlay.window, WM_LBUTTONUP, None, Some(at));
            assert!((*pointer).feedback.submitted_after.is_none());
            SendMessageW(overlay.window, WM_LBUTTONDOWN, None, Some(at));
            assert_eq!(GetCapture(), overlay.window);
            let mut suggested = RECT::default();
            GetWindowRect(overlay.window, &mut suggested).unwrap();
            let dpi = (*pointer).dpi as usize;
            SendMessageW(
                overlay.window,
                WM_DPICHANGED,
                Some(WPARAM(dpi | (dpi << 16))),
                Some(LPARAM((&suggested as *const RECT) as isize)),
            );
            assert_ne!(GetCapture(), overlay.window, "DPI change cancels capture");
            SendMessageW(overlay.window, WM_LBUTTONUP, None, Some(at));
            assert!((*pointer).feedback.submitted_after.is_none());
            SendMessageW(overlay.window, WM_LBUTTONDOWN, None, Some(at));
            SendMessageW(overlay.window, WM_LBUTTONUP, None, Some(at));
            assert!((*pointer).feedback.submitted_after.is_some());
            let first_click = (*pointer).feedback.last_click;
            overlay.update(&wheel, &snapshot, ui.clone());
            SendMessageW(overlay.window, WM_LBUTTONDOWN, None, Some(at));
            SendMessageW(overlay.window, WM_LBUTTONUP, None, Some(at));
            assert_eq!(
                (*pointer).feedback.last_click,
                first_click,
                "stale status cannot re-arm a report"
            );
            assert_eq!(
                GetForegroundWindow(),
                foreground,
                "report click must not take foreground focus"
            );
            let start = Instant::now();
            assert_eq!(click_scale(Some(start), start), 900);
            assert!(click_scale(Some(start), start + click_animation_duration() / 2) > 1000);
            (*pointer).click_animation = Some(start - click_animation_duration());
            SendMessageW(
                overlay.window,
                WM_TIMER,
                Some(WPARAM(CLICK_ANIMATION_TIMER)),
                None,
            );
            assert!(
                (*pointer).click_animation.is_none(),
                "the click animation stops completely"
            );
            assert_eq!((*pointer).visual(Instant::now()).click_scale, 1000);
            if let Ok(directory) = std::env::var("COACH_DIAGNOSTICS_OVERLAY_PREVIEW_DIR") {
                let directory = std::path::Path::new(&directory);
                std::fs::create_dir_all(directory).unwrap();
                save_bitmap(overlay.window, &directory.join("saving.bmp"));
                snapshot.report_sequence = 1;
                snapshot.export_path = Some(PathBuf::from("software-incident.zip"));
                overlay.update(&wheel, &snapshot, ui.clone());
                save_bitmap(overlay.window, &directory.join("saved.bmp"));
                snapshot.report_error = Some("The report folder could not be written.".into());
                overlay.update(&wheel, &snapshot, ui.clone());
                save_bitmap(overlay.window, &directory.join("failed.bmp"));
                snapshot.report_error = None;
                (*pointer).feedback.saved_until = None;
                (*pointer).pointer.over_action = false;
                refresh_window(overlay.window);
            }
            let _ = SetWindowPos(
                overlay.window,
                None,
                260,
                120,
                0,
                0,
                SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE,
            );
            SendMessageW(overlay.window, WM_EXITSIZEMOVE, None, None);
            assert_eq!(ui.borrow().preferences.left, Some(260));
            assert_eq!(ui.borrow().preferences.top, Some(120));
            snapshot.running = false;
            snapshot.pedals.config.enabled = false;
            overlay.update(&wheel, &snapshot, ui.clone());
            assert!(
                !(*pointer).snapshot.running,
                "model updates even when device colors remain unchanged"
            );
            snapshot.report_sequence += 1;
            snapshot.export_path = Some(PathBuf::from("software-incident.zip"));
            overlay.update(&wheel, &snapshot, ui.clone());
            assert!((*pointer).feedback.saved_until.is_some());
            ui.borrow_mut().preferences.show_overlay = false;
            overlay.update(&wheel, &snapshot, ui.clone());
            assert!(!IsWindowVisible(overlay.window).as_bool());
        }
        drop(overlay);
        assert!(wheel.shutdown_wait(Duration::from_secs(2)));
    }
}

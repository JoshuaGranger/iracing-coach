//! Native USB diagnostics. Capture lives independently of the panel and overlay.

use super::{
    diagnostics_overlay::{
        DiagnosticsOverlay, DiagnosticsUiState, device_symbol, fill, health, health_color,
        health_label, rounded, text,
    },
    dialog_theme::{DialogTheme, DpiAwarenessGuard, apply_titlebar, palette, style_control},
    render::colorref,
    wide,
};
use crate::wheel::{
    DeviceMonitorConfig, DeviceRole, WheelCommand, WheelDevice, WheelHandle, WheelStatus,
};
use std::{cell::RefCell, collections::HashMap, path::PathBuf, rc::Rc};
use windows::Win32::Foundation::{HANDLE, HWND, LPARAM, LRESULT, RECT, WPARAM};
use windows::Win32::Graphics::Gdi::*;
use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::UI::Controls::{
    DRAWITEMSTRUCT, MEASUREITEMSTRUCT, ODS_DISABLED, ODS_FOCUS, ODS_SELECTED,
};
use windows::Win32::UI::HiDpi::{AdjustWindowRectExForDpi, GetDpiForWindow};
use windows::Win32::UI::Input::KeyboardAndMouse::{EnableWindow, SetFocus};
use windows::Win32::UI::Shell::{DefSubclassProc, RemoveWindowSubclass, SetWindowSubclass};
use windows::Win32::UI::WindowsAndMessaging::*;
use windows::core::PCWSTR;

const CLASS: &str = "CoachUsbDiagnostics";
const WIDTH: i32 = 720;
const HEIGHT: i32 = 490;
const POLL: usize = 1;
const WHEEL_ENABLED: usize = 301;
const PEDALS_ENABLED: usize = 302;
const WHEEL_DEVICE: usize = 303;
const PEDALS_DEVICE: usize = 304;
const RUN: usize = 305;
const STARTUP: usize = 306;
const SHOW_OVERLAY: usize = 307;
const REPORT: usize = 308;
const FILES: usize = 309;
const TRACE: usize = 310;
const WHEEL_AXIS: usize = 311;
const AXIS_LABELS: [&str; 6] = [
    "X axis", "Y axis", "Z axis", "Rx axis", "Ry axis", "Rz axis",
];
const CLOSE: usize = 2;

pub struct WheelPanel {
    window: HWND,
    ui: Rc<RefCell<DiagnosticsUiState>>,
    overlay: DiagnosticsOverlay,
}
impl Default for WheelPanel {
    fn default() -> Self {
        Self {
            window: HWND::default(),
            ui: Rc::new(RefCell::new(DiagnosticsUiState::load(None))),
            overlay: DiagnosticsOverlay::default(),
        }
    }
}
impl WheelPanel {
    pub fn with_state_dir(state_dir: PathBuf) -> Self {
        Self {
            window: HWND::default(),
            ui: Rc::new(RefCell::new(DiagnosticsUiState::load(Some(
                state_dir.join("diagnostics-ui.json"),
            )))),
            overlay: DiagnosticsOverlay::default(),
        }
    }
    pub fn show(&mut self, owner: HWND, wheel: &WheelHandle) -> Result<(), String> {
        // SAFETY: all native window operations run on the companion's UI thread.
        unsafe {
            if self.window.is_invalid() {
                self.window =
                    create(owner, wheel.clone(), self.ui.clone()).map_err(|e| e.to_string())?;
            }
            let _ = ShowWindow(self.window, SW_SHOWNORMAL);
            SetTimer(Some(self.window), POLL, 500, None);
            if let Some(panel) = state(self.window) {
                panel.refresh();
            }
            let _ = SetForegroundWindow(self.window);
            let _ = SetFocus(Some(
                GetDlgItem(Some(self.window), RUN as i32).unwrap_or_default(),
            ));
        }
        Ok(())
    }
    /// Runs even with the panel hidden. The diagnostics overlay has independent visibility.
    pub fn on_timer(&mut self, wheel: &WheelHandle) {
        if let Some(snapshot) = wheel.try_snapshot() {
            self.overlay.update(wheel, &snapshot, self.ui.clone());
        }
    }
    pub fn translate(&self, message: &MSG) -> bool {
        unsafe {
            !self.window.is_invalid()
                && IsWindowVisible(self.window).as_bool()
                && IsDialogMessageW(self.window, message).as_bool()
        }
    }
}
impl Drop for WheelPanel {
    fn drop(&mut self) {
        if !self.window.is_invalid() {
            unsafe {
                let _ = DestroyWindow(self.window);
            }
        }
    }
}
struct Control {
    window: HWND,
    bounds: RECT,
}
struct Panel {
    window: HWND,
    owner: HWND,
    wheel: WheelHandle,
    ui: Rc<RefCell<DiagnosticsUiState>>,
    theme: DialogTheme,
    heading: HFONT,
    dpi: u32,
    controls: Vec<Control>,
    devices: Vec<WheelDevice>,
    device_labels: Vec<String>,
    last: Option<WheelStatus>,
    labels: HashMap<usize, String>,
    enabled: HashMap<usize, bool>,
    checks: HashMap<usize, bool>,
    paint_key: String,
    note: String,
}
impl Panel {
    fn px(&self, value: i32) -> i32 {
        value * self.dpi as i32 / 96
    }
    fn control(&self, id: usize) -> HWND {
        unsafe { GetDlgItem(Some(self.window), id as i32).unwrap_or_default() }
    }
    fn label(&mut self, id: usize, value: &str) {
        if self.labels.get(&id).is_some_and(|old| old == value) {
            return;
        }
        self.labels.insert(id, value.into());
        unsafe {
            let _ = SetWindowTextW(self.control(id), PCWSTR(wide(value).as_ptr()));
        }
    }
    fn enabled(&mut self, id: usize, value: bool) {
        if self.enabled.get(&id) == Some(&value) {
            return;
        }
        self.enabled.insert(id, value);
        unsafe {
            let _ = EnableWindow(self.control(id), value);
        }
    }
    fn checked(&mut self, id: usize, value: bool) {
        if self.checks.get(&id) == Some(&value) {
            return;
        }
        self.checks.insert(id, value);
        unsafe {
            SetWindowLongPtrW(self.control(id), GWLP_USERDATA, isize::from(value));
            let _ = InvalidateRect(Some(self.control(id)), None, false);
        }
    }
    fn refresh(&mut self) {
        // Keep the last valid view during a transient snapshot-lock miss.
        if let Some(snapshot) = self.wheel.try_snapshot() {
            self.render(snapshot);
        }
    }
    fn render(&mut self, snapshot: WheelStatus) {
        let mut devices = snapshot.devices.clone();
        for monitor in [&snapshot.wheel, &snapshot.pedals] {
            if let Some(selected) = &monitor.config.device
                && !devices.iter().any(|device| device.id == selected.id)
            {
                devices.push(selected.clone());
            }
        }
        let labels: Vec<_> = devices
            .iter()
            .map(|device| {
                if snapshot
                    .devices
                    .iter()
                    .any(|connected| connected.id == device.id)
                {
                    device.name.clone()
                } else {
                    format!("{} (disconnected)", device.name)
                }
            })
            .collect();
        let busy = snapshot.busy && !snapshot.running;
        if self.devices != devices || self.device_labels != labels {
            self.devices = devices;
            self.device_labels = labels;
            for id in [WHEEL_DEVICE, PEDALS_DEVICE] {
                unsafe {
                    let combo = self.control(id);
                    SendMessageW(combo, WM_SETREDRAW, Some(WPARAM(0)), None);
                    SendMessageW(combo, CB_RESETCONTENT, None, None);
                    SendMessageW(
                        combo,
                        CB_ADDSTRING,
                        None,
                        Some(LPARAM(wide("Select device").as_ptr() as isize)),
                    );
                    for label in &self.device_labels {
                        SendMessageW(
                            combo,
                            CB_ADDSTRING,
                            None,
                            Some(LPARAM(wide(label).as_ptr() as isize)),
                        );
                    }
                    SendMessageW(combo, WM_SETREDRAW, Some(WPARAM(1)), None);
                    let _ = InvalidateRect(Some(combo), None, false);
                }
            }
        }
        for (role, check, combo) in [
            (DeviceRole::Wheel, WHEEL_ENABLED, WHEEL_DEVICE),
            (DeviceRole::Pedals, PEDALS_ENABLED, PEDALS_DEVICE),
        ] {
            let monitor = if role == DeviceRole::Wheel {
                &snapshot.wheel
            } else {
                &snapshot.pedals
            };
            let index = monitor
                .config
                .device
                .as_ref()
                .and_then(|selected| {
                    self.devices
                        .iter()
                        .position(|device| device.id == selected.id)
                })
                .map_or(0, |i| i + 1);
            unsafe {
                if SendMessageW(self.control(combo), CB_GETCURSEL, None, None).0 != index as isize {
                    SendMessageW(self.control(combo), CB_SETCURSEL, Some(WPARAM(index)), None);
                }
            }
            self.checked(check, monitor.config.enabled);
            self.enabled(check, !busy);
            self.enabled(combo, monitor.config.enabled && !busy);
        }
        let axis = snapshot
            .wheel
            .config
            .axes
            .first()
            .copied()
            .unwrap_or(0)
            .min(5) as usize;
        // SAFETY: the six native choices correspond directly to the recorder's axis indices.
        unsafe {
            if SendMessageW(self.control(WHEEL_AXIS), CB_GETCURSEL, None, None).0 != axis as isize {
                SendMessageW(
                    self.control(WHEEL_AXIS),
                    CB_SETCURSEL,
                    Some(WPARAM(axis)),
                    None,
                );
            }
        }
        self.enabled(WHEEL_AXIS, snapshot.wheel.config.enabled && !busy);
        self.checked(STARTUP, snapshot.auto_record);
        let show_overlay = self.ui.borrow().preferences.show_overlay;
        self.checked(SHOW_OVERLAY, show_overlay);
        self.enabled(STARTUP, !busy);
        self.enabled(SHOW_OVERLAY, true);
        self.enabled(RUN, !busy);
        self.label(
            RUN,
            if snapshot.running {
                "Stop recording"
            } else {
                "Start recording"
            },
        );
        self.label(
            REPORT,
            if snapshot.report_pending {
                "Saving incident…"
            } else {
                "Report diagnostic incident"
            },
        );
        self.enabled(
            REPORT,
            snapshot.has_capture && !snapshot.report_pending && !busy && snapshot.sample_count > 0,
        );
        self.enabled(
            FILES,
            snapshot.export_path.is_some() || snapshot.exports_directory.is_some(),
        );
        self.label(
            TRACE,
            if needs_recovery(&snapshot) {
                "Recover trace"
            } else {
                "Enable USB trace"
            },
        );
        self.enabled(
            TRACE,
            !busy
                && (snapshot.can_start_usb
                    || (needs_recovery(&snapshot) && snapshot.can_recover_usb)),
        );
        unsafe {
            let _ = ShowWindow(
                self.control(TRACE),
                if snapshot.usb.contains("trace recording (") {
                    SW_HIDE
                } else {
                    SW_SHOWNOACTIVATE
                },
            );
        }
        let key = format!(
            "{}|{}|{}|{}|{}|{}|{}|{:?}|{:?}",
            snapshot.running,
            health_label(snapshot.running, &snapshot.wheel),
            health_label(snapshot.running, &snapshot.pedals),
            snapshot.usb,
            snapshot.message,
            snapshot.report_pending,
            self.note,
            snapshot.export_path,
            snapshot.report_error
        );
        if self.paint_key != key {
            self.paint_key = key;
            unsafe {
                let _ = InvalidateRect(Some(self.window), None, false);
            }
        }
        self.last = Some(snapshot);
    }
    fn send(&mut self, command: WheelCommand) {
        self.note = if self.wheel.send(command) {
            String::new()
        } else {
            "Recorder is busy. Try again.".into()
        };
        self.refresh();
    }
    fn configure(&mut self, role: DeviceRole, enabled: Option<bool>) {
        let Some(snapshot) = &self.last else {
            return;
        };
        let monitor = if role == DeviceRole::Wheel {
            &snapshot.wheel
        } else {
            &snapshot.pedals
        };
        let combo = if role == DeviceRole::Wheel {
            WHEEL_DEVICE
        } else {
            PEDALS_DEVICE
        };
        let mut config: DeviceMonitorConfig = monitor.config.clone();
        if let Some(enabled) = enabled {
            config.enabled = enabled;
        } else {
            let index = unsafe { SendMessageW(self.control(combo), CB_GETCURSEL, None, None).0 };
            config.device = usize::try_from(index)
                .ok()
                .and_then(|i| i.checked_sub(1))
                .and_then(|i| self.devices.get(i))
                .cloned();
        }
        self.send(WheelCommand::ConfigureDevice { role, config });
    }
    fn command(&mut self, id: usize) {
        match id {
            WHEEL_AXIS => {
                let Some(snapshot) = &self.last else {
                    return;
                };
                // SAFETY: selection is from the six fixed axis entries in the owned control.
                let axis =
                    unsafe { SendMessageW(self.control(WHEEL_AXIS), CB_GETCURSEL, None, None).0 };
                let Ok(axis) = u8::try_from(axis) else {
                    return;
                };
                if axis > 5 {
                    return;
                }
                let mut config = snapshot.wheel.config.clone();
                config.axes = vec![axis];
                // Keep the native selection visible while the asynchronous recorder applies it.
                if !self.wheel.send(WheelCommand::ConfigureDevice {
                    role: DeviceRole::Wheel,
                    config,
                }) {
                    self.note = "Wheel axis could not be changed. Try again.".into();
                    self.refresh();
                }
            }
            WHEEL_ENABLED => self.configure(
                DeviceRole::Wheel,
                Some(!self.checks.get(&id).copied().unwrap_or(true)),
            ),
            PEDALS_ENABLED => self.configure(
                DeviceRole::Pedals,
                Some(!self.checks.get(&id).copied().unwrap_or(true)),
            ),
            WHEEL_DEVICE => self.configure(DeviceRole::Wheel, None),
            PEDALS_DEVICE => self.configure(DeviceRole::Pedals, None),
            STARTUP => self.send(WheelCommand::SetAutoRecord(
                !self.checks.get(&id).copied().unwrap_or(true),
            )),
            SHOW_OVERLAY => {
                let value = !self.ui.borrow().preferences.show_overlay;
                {
                    let mut ui = self.ui.borrow_mut();
                    ui.preferences.show_overlay = value;
                    ui.save();
                }
                self.checked(SHOW_OVERLAY, value);
            }
            RUN => {
                if self.last.as_ref().is_some_and(|s| s.running) {
                    self.send(WheelCommand::Stop);
                } else {
                    self.send(WheelCommand::Run {
                        window: self.owner.0 as isize,
                    });
                }
            }
            REPORT => self.send(WheelCommand::ReportDiagnosticIncident),
            TRACE => {
                if self.last.as_ref().is_some_and(needs_recovery) {
                    self.send(WheelCommand::RecoverUsb);
                } else {
                    self.send(WheelCommand::StartUsb);
                }
            }
            FILES => {
                if let Some(snapshot) = &self.last {
                    if let Some(path) = snapshot.export_path.clone() {
                        open_files(path, true);
                    } else if let Some(path) = snapshot.exports_directory.clone() {
                        open_files(path, false);
                    }
                }
            }
            CLOSE => self.hide(),
            _ => {}
        }
    }
    fn hide(&self) {
        unsafe {
            let _ = KillTimer(Some(self.window), POLL);
            let _ = ShowWindow(self.window, SW_HIDE);
        }
    }
    fn layout(&mut self) {
        for control in &self.controls {
            self.theme.apply_font(control.window);
            unsafe {
                let b = control.bounds;
                let _ = SetWindowPos(
                    control.window,
                    None,
                    self.px(b.left),
                    self.px(b.top),
                    self.px(b.right - b.left),
                    self.px(b.bottom - b.top),
                    SWP_NOACTIVATE | SWP_NOZORDER,
                );
                let id = GetDlgCtrlID(control.window) as usize;
                if matches!(id, WHEEL_DEVICE | PEDALS_DEVICE | WHEEL_AXIS) {
                    SendMessageW(
                        control.window,
                        CB_SETITEMHEIGHT,
                        Some(WPARAM(usize::MAX)),
                        Some(LPARAM(self.px(26) as isize)),
                    );
                    SendMessageW(
                        control.window,
                        CB_SETITEMHEIGHT,
                        Some(WPARAM(0)),
                        Some(LPARAM(self.px(26) as isize)),
                    );
                }
            }
        }
    }
}
fn needs_recovery(status: &WheelStatus) -> bool {
    status.usb.to_lowercase().contains("cleanup")
        || status.usb.to_lowercase().contains("interrupted")
        || (!status.running && status.message.to_lowercase().contains("recover"))
}
fn open_files(path: PathBuf, select: bool) {
    let _ = std::thread::Builder::new()
        .name("diagnostics-open-files".into())
        .spawn(move || {
            if !select && let Err(error) = std::fs::create_dir_all(&path) {
                crate::warn!("diagnostic exports folder could not be opened: {error}");
                return;
            }
            let mut command = std::process::Command::new("explorer.exe");
            if select {
                command.arg("/select,");
            }
            let _ = command.arg(path).spawn();
        });
}
pub(super) fn capture_indicator(status: &WheelStatus) -> String {
    if !status.running {
        "DIAGNOSTICS STOPPED".into()
    } else if status.last_sample_age_ms.is_none_or(|age| age > 2_000) {
        "DIAGNOSTICS WAITING FOR INPUT".into()
    } else {
        "DIAGNOSTICS RUNNING".into()
    }
}
unsafe fn create(
    owner: HWND,
    wheel: WheelHandle,
    ui: Rc<RefCell<DiagnosticsUiState>>,
) -> windows::core::Result<HWND> {
    let _dpi = DpiAwarenessGuard::per_monitor_v2()?;
    // SAFETY: standard native class, owned controls and one boxed state on this UI thread.
    unsafe {
        let instance = GetModuleHandleW(PCWSTR::null())?;
        let class = wide(CLASS);
        RegisterClassW(&WNDCLASSW {
            hInstance: instance.into(),
            lpfnWndProc: Some(window_proc),
            lpszClassName: PCWSTR(class.as_ptr()),
            hCursor: LoadCursorW(None, IDC_ARROW)?,
            ..Default::default()
        });
        let style = WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX | WS_CLIPCHILDREN;
        let window = CreateWindowExW(
            WS_EX_CONTROLPARENT,
            PCWSTR(class.as_ptr()),
            PCWSTR(wide("USB diagnostics — iRacing Coach").as_ptr()),
            style,
            CW_USEDEFAULT,
            CW_USEDEFAULT,
            WIDTH,
            HEIGHT,
            Some(owner),
            None,
            Some(instance.into()),
            None,
        )?;
        apply_titlebar(window);
        let dpi = GetDpiForWindow(window).max(96);
        let theme = DialogTheme::new(dpi);
        let _ = SetPropW(
            window,
            PCWSTR(wide("CoachDiagnosticsDpi").as_ptr()),
            Some(HANDLE(dpi as usize as *mut _)),
        );
        let heading = CreateFontW(
            -((24 * dpi / 96) as i32),
            0,
            0,
            0,
            600,
            0,
            0,
            0,
            DEFAULT_CHARSET,
            OUT_DEFAULT_PRECIS,
            CLIP_DEFAULT_PRECIS,
            CLEARTYPE_QUALITY,
            0,
            PCWSTR(wide("Segoe UI").as_ptr()),
        );
        let mut panel = Box::new(Panel {
            window,
            owner,
            wheel,
            ui,
            theme,
            heading,
            dpi,
            controls: Vec::new(),
            devices: Vec::new(),
            device_labels: Vec::new(),
            last: None,
            labels: HashMap::new(),
            enabled: HashMap::new(),
            checks: HashMap::new(),
            paint_key: String::new(),
            note: String::new(),
        });
        for (id, label, x, y, w, h, combo) in [
            (WHEEL_ENABLED, "Monitor wheel", 38, 106, 28, 28, false),
            (WHEEL_DEVICE, "", 178, 96, 314, 240, true),
            (WHEEL_AXIS, "", 360, 138, 132, 200, true),
            (PEDALS_ENABLED, "Monitor pedals", 38, 218, 28, 28, false),
            (PEDALS_DEVICE, "", 178, 216, 314, 240, true),
            (STARTUP, "Run on startup", 24, 298, 258, 30, false),
            (
                SHOW_OVERLAY,
                "Show diagnostic overlay",
                24,
                338,
                300,
                30,
                false,
            ),
            (TRACE, "Enable USB trace", 354, 338, 156, 30, false),
            (RUN, "Start recording", 522, 298, 174, 38, false),
            (
                REPORT,
                "Report diagnostic incident",
                24,
                390,
                344,
                44,
                false,
            ),
            (FILES, "Open files", 384, 390, 148, 44, false),
            (CLOSE, "Close", 548, 390, 148, 44, false),
        ] {
            let kind = if combo { "COMBOBOX" } else { "BUTTON" };
            let extra = if combo {
                WINDOW_STYLE((CBS_DROPDOWNLIST | CBS_OWNERDRAWFIXED | CBS_HASSTRINGS) as u32)
                    | WS_VSCROLL
            } else {
                WINDOW_STYLE(BS_OWNERDRAW as u32)
            };
            let child = CreateWindowExW(
                WINDOW_EX_STYLE(0),
                PCWSTR(wide(kind).as_ptr()),
                PCWSTR(wide(label).as_ptr()),
                WS_CHILD | WS_VISIBLE | WS_TABSTOP | extra,
                x,
                y,
                w,
                h,
                Some(window),
                Some(HMENU(id as *mut _)),
                Some(instance.into()),
                None,
            )?;
            style_control(child);
            if combo {
                let labels = if id == WHEEL_AXIS {
                    &AXIS_LABELS[..]
                } else {
                    &["Select device"][..]
                };
                for label in labels {
                    SendMessageW(
                        child,
                        CB_ADDSTRING,
                        None,
                        Some(LPARAM(wide(label).as_ptr() as isize)),
                    );
                }
                SendMessageW(child, CB_SETCURSEL, Some(WPARAM(0)), None);
                let _ = SetWindowSubclass(child, Some(combo_proc), 1, 0);
            }
            panel.controls.push(Control {
                window: child,
                bounds: RECT {
                    left: x,
                    top: y,
                    right: x + w,
                    bottom: y + h,
                },
            });
        }
        panel.layout();
        let mut outer = RECT {
            left: 0,
            top: 0,
            right: panel.px(WIDTH),
            bottom: panel.px(HEIGHT),
        };
        let _ = AdjustWindowRectExForDpi(&mut outer, style, false, WS_EX_CONTROLPARENT, dpi);
        let _ = SetWindowPos(
            window,
            None,
            0,
            0,
            outer.right - outer.left,
            outer.bottom - outer.top,
            SWP_NOMOVE | SWP_NOACTIVATE | SWP_NOZORDER,
        );
        SetWindowLongPtrW(window, GWLP_USERDATA, Box::into_raw(panel) as isize);
        Ok(window)
    }
}
unsafe fn state(window: HWND) -> Option<&'static mut Panel> {
    unsafe { (GetWindowLongPtrW(window, GWLP_USERDATA) as *mut Panel).as_mut() }
}
// Native synchronous drawing notifications do not borrow Panel: they can arrive during render().
unsafe fn control_color(dc: HDC) -> LRESULT {
    use std::sync::OnceLock;
    static BRUSH: OnceLock<usize> = OnceLock::new();
    unsafe {
        SetTextColor(dc, colorref(palette().text));
        SetBkColor(dc, colorref(palette().surface));
        LRESULT(
            *BRUSH.get_or_init(|| CreateSolidBrush(colorref(palette().surface)).0 as usize)
                as isize,
        )
    }
}
unsafe fn control_dpi(window: HWND) -> u32 {
    unsafe {
        let dpi = GetPropW(window, PCWSTR(wide("CoachDiagnosticsDpi").as_ptr())).0 as usize as u32;
        if dpi >= 96 {
            dpi
        } else {
            GetDpiForWindow(window).max(96)
        }
    }
}

// Preserve the native combobox's keyboard, accessibility and drop-down behavior; paint its
// closed field and arrow together so Windows' light classic frame never flashes through.
unsafe extern "system" fn combo_proc(
    window: HWND,
    message: u32,
    wparam: WPARAM,
    lparam: LPARAM,
    _: usize,
    _: usize,
) -> LRESULT {
    unsafe {
        if message == WM_ERASEBKGND {
            return LRESULT(1);
        }
        if message == WM_NCDESTROY {
            let _ = RemoveWindowSubclass(window, Some(combo_proc), 1);
        }
        if message != WM_PAINT {
            return DefSubclassProc(window, message, wparam, lparam);
        }
        let parent = GetParent(window).unwrap_or_default();
        let dpi = control_dpi(parent);
        let px = |v: i32| v * dpi as i32 / 96;
        let mut ps = PAINTSTRUCT::default();
        let dc = BeginPaint(window, &mut ps);
        let mut bounds = RECT::default();
        let _ = GetClientRect(window, &mut bounds);
        let memory = CreateCompatibleDC(Some(dc));
        let bitmap = CreateCompatibleBitmap(dc, bounds.right.max(1), bounds.bottom.max(1));
        let old_bitmap = SelectObject(memory, HGDIOBJ(bitmap.0));
        fill(memory, bounds, palette().surface);
        rounded(
            memory,
            RECT {
                right: bounds.right - 1,
                bottom: bounds.bottom - 1,
                ..bounds
            },
            palette().app,
            palette().border,
            px(6),
        );
        let index = SendMessageW(window, CB_GETCURSEL, None, None).0.max(0) as usize;
        let len = SendMessageW(window, CB_GETLBTEXTLEN, Some(WPARAM(index)), None).0;
        let mut value = vec![0u16; (len.max(0) as usize).min(4096) + 1];
        let label = if (0..4096).contains(&len) {
            SendMessageW(
                window,
                CB_GETLBTEXT,
                Some(WPARAM(index)),
                Some(LPARAM(value.as_mut_ptr() as isize)),
            );
            String::from_utf16_lossy(&value[..len as usize])
        } else {
            "Select device".into()
        };
        let enabled =
            windows::Win32::UI::Input::KeyboardAndMouse::IsWindowEnabled(window).as_bool();
        let font = HFONT(SendMessageW(window, WM_GETFONT, None, None).0 as *mut _);
        text(
            memory,
            font,
            &label,
            RECT {
                left: px(11),
                top: 0,
                right: bounds.right - px(30),
                bottom: bounds.bottom,
            },
            if enabled {
                palette().text
            } else {
                palette().muted
            },
            DT_LEFT | DT_VCENTER | DT_SINGLELINE,
        );
        let pen = CreatePen(PS_SOLID, px(1).max(1), colorref(palette().muted));
        let old_pen = SelectObject(memory, HGDIOBJ(pen.0));
        let middle = bounds.bottom / 2;
        let _ = MoveToEx(memory, bounds.right - px(21), middle - px(2), None);
        let _ = LineTo(memory, bounds.right - px(16), middle + px(3));
        let _ = LineTo(memory, bounds.right - px(11), middle - px(2));
        SelectObject(memory, old_pen);
        let _ = DeleteObject(HGDIOBJ(pen.0));
        if windows::Win32::UI::Input::KeyboardAndMouse::GetFocus() == window {
            let focus = RECT {
                left: px(4),
                top: px(4),
                right: bounds.right - px(4),
                bottom: bounds.bottom - px(4),
            };
            let _ = DrawFocusRect(memory, &focus);
        }
        let _ = BitBlt(
            dc,
            0,
            0,
            bounds.right,
            bounds.bottom,
            Some(memory),
            0,
            0,
            SRCCOPY,
        );
        SelectObject(memory, old_bitmap);
        let _ = DeleteObject(HGDIOBJ(bitmap.0));
        let _ = DeleteDC(memory);
        let _ = EndPaint(window, &ps);
        LRESULT(0)
    }
}
unsafe fn draw_control(window: HWND, item: &DRAWITEMSTRUCT) {
    unsafe {
        let theme = DialogTheme::new(control_dpi(window));
        let dpi = control_dpi(window) as i32;
        let px = |v: i32| v * dpi / 96;
        let id = item.CtlID as usize;
        if matches!(id, WHEEL_DEVICE | PEDALS_DEVICE | WHEEL_AXIS) {
            fill(
                item.hDC,
                item.rcItem,
                if item.itemState.0 & ODS_SELECTED.0 != 0 {
                    palette().surface_hover
                } else {
                    palette().surface
                },
            );
            let index = if item.itemID == u32::MAX {
                SendMessageW(item.hwndItem, CB_GETCURSEL, None, None)
                    .0
                    .max(0) as usize
            } else {
                item.itemID as usize
            };
            let len = SendMessageW(item.hwndItem, CB_GETLBTEXTLEN, Some(WPARAM(index)), None).0;
            let mut value = vec![0u16; (len.max(0) as usize).min(4096) + 1];
            let label = if (0..4096).contains(&len) {
                SendMessageW(
                    item.hwndItem,
                    CB_GETLBTEXT,
                    Some(WPARAM(index)),
                    Some(LPARAM(value.as_mut_ptr() as isize)),
                );
                String::from_utf16_lossy(&value[..len as usize])
            } else {
                "Select device".into()
            };
            let mut bounds = item.rcItem;
            bounds.left += px(10);
            bounds.right -= px(6);
            text(
                item.hDC,
                theme.font,
                &label,
                bounds,
                if item.itemState.0 & ODS_DISABLED.0 != 0 {
                    palette().muted
                } else {
                    palette().text
                },
                DT_LEFT | DT_SINGLELINE | DT_VCENTER,
            );
            if item.itemState.0 & ODS_FOCUS.0 != 0 {
                let _ = DrawFocusRect(item.hDC, &item.rcItem);
            }
        } else if matches!(id, WHEEL_ENABLED | PEDALS_ENABLED | STARTUP | SHOW_OVERLAY) {
            let disabled = item.itemState.0 & ODS_DISABLED.0 != 0;
            let checked = GetWindowLongPtrW(item.hwndItem, GWLP_USERDATA) != 0;
            let row = matches!(id, WHEEL_ENABLED | PEDALS_ENABLED);
            fill(
                item.hDC,
                item.rcItem,
                if row {
                    palette().surface
                } else {
                    palette().app
                },
            );
            let top = item.rcItem.top + (item.rcItem.bottom - item.rcItem.top - px(19)) / 2;
            let check = RECT {
                left: item.rcItem.left + px(3),
                top,
                right: item.rcItem.left + px(22),
                bottom: top + px(19),
            };
            rounded(
                item.hDC,
                check,
                if checked && !disabled {
                    palette().accent
                } else {
                    palette().surface
                },
                if disabled {
                    palette().surface_hover
                } else {
                    palette().border
                },
                px(4),
            );
            if checked {
                let pen = CreatePen(
                    PS_SOLID,
                    px(2).max(1),
                    colorref(if disabled {
                        palette().muted
                    } else {
                        palette().app
                    }),
                );
                let old = SelectObject(item.hDC, HGDIOBJ(pen.0));
                let _ = MoveToEx(item.hDC, check.left + px(4), check.top + px(9), None);
                let _ = LineTo(item.hDC, check.left + px(8), check.top + px(13));
                let _ = LineTo(item.hDC, check.left + px(15), check.top + px(5));
                SelectObject(item.hDC, old);
                let _ = DeleteObject(HGDIOBJ(pen.0));
            }
            if !row {
                let mut value = [0u16; 128];
                let len = GetWindowTextW(item.hwndItem, &mut value).max(0) as usize;
                text(
                    item.hDC,
                    theme.font,
                    &String::from_utf16_lossy(&value[..len]),
                    RECT {
                        left: check.right + px(10),
                        ..item.rcItem
                    },
                    if disabled {
                        palette().muted
                    } else {
                        palette().text
                    },
                    DT_LEFT | DT_VCENTER | DT_SINGLELINE,
                );
            }
            if item.itemState.0 & ODS_FOCUS.0 != 0 {
                let _ = DrawFocusRect(item.hDC, &item.rcItem);
            }
        } else {
            theme.paint_button(item, id == REPORT);
        }
    }
}
unsafe extern "system" fn window_proc(
    window: HWND,
    message: u32,
    wparam: WPARAM,
    lparam: LPARAM,
) -> LRESULT {
    unsafe {
        match message {
            WM_CTLCOLORSTATIC | WM_CTLCOLORBTN | WM_CTLCOLOREDIT | WM_CTLCOLORLISTBOX => {
                return control_color(HDC(wparam.0 as *mut _));
            }
            WM_DRAWITEM => {
                draw_control(window, &*(lparam.0 as *const DRAWITEMSTRUCT));
                return LRESULT(1);
            }
            WM_MEASUREITEM => {
                let item = &mut *(lparam.0 as *mut MEASUREITEMSTRUCT);
                item.itemHeight = 26 * control_dpi(window) / 96;
                return LRESULT(1);
            }
            WM_ERASEBKGND => return LRESULT(1),
            WM_COMMAND
                if matches!(wparam.0 & 0xffff, WHEEL_DEVICE | PEDALS_DEVICE | WHEEL_AXIS)
                    && (wparam.0 >> 16) & 0xffff != CBN_SELCHANGE as usize =>
            {
                return LRESULT(0);
            }
            WM_PAINT | WM_TIMER | WM_COMMAND | WM_CLOSE | WM_DPICHANGED | WM_NCDESTROY => {}
            _ => return DefWindowProcW(window, message, wparam, lparam),
        }
        let Some(panel) = state(window) else {
            return DefWindowProcW(window, message, wparam, lparam);
        };
        match message {
            WM_PAINT => {
                paint(panel);
                LRESULT(0)
            }
            WM_TIMER if wparam.0 == POLL => {
                panel.refresh();
                LRESULT(0)
            }
            WM_COMMAND => {
                panel.command(wparam.0 & 0xffff);
                LRESULT(0)
            }
            WM_CLOSE => {
                panel.hide();
                LRESULT(0)
            }
            WM_DPICHANGED => {
                panel.dpi = (wparam.0 & 0xffff) as u32;
                let _ = SetPropW(
                    window,
                    PCWSTR(wide("CoachDiagnosticsDpi").as_ptr()),
                    Some(HANDLE(panel.dpi as usize as *mut _)),
                );
                let old_theme = std::mem::replace(&mut panel.theme, DialogTheme::new(panel.dpi));
                let old_heading = panel.heading;
                panel.heading = CreateFontW(
                    -panel.px(24),
                    0,
                    0,
                    0,
                    600,
                    0,
                    0,
                    0,
                    DEFAULT_CHARSET,
                    OUT_DEFAULT_PRECIS,
                    CLIP_DEFAULT_PRECIS,
                    CLEARTYPE_QUALITY,
                    0,
                    PCWSTR(wide("Segoe UI").as_ptr()),
                );
                panel.layout();
                drop(old_theme);
                let _ = DeleteObject(HGDIOBJ(old_heading.0));
                let rect = &*(lparam.0 as *const RECT);
                let _ = SetWindowPos(
                    window,
                    None,
                    rect.left,
                    rect.top,
                    rect.right - rect.left,
                    rect.bottom - rect.top,
                    SWP_NOACTIVATE | SWP_NOZORDER,
                );
                let _ = InvalidateRect(Some(window), None, false);
                LRESULT(0)
            }
            WM_NCDESTROY => {
                let pointer = GetWindowLongPtrW(window, GWLP_USERDATA) as *mut Panel;
                SetWindowLongPtrW(window, GWLP_USERDATA, 0);
                let _ = RemovePropW(window, PCWSTR(wide("CoachDiagnosticsDpi").as_ptr()));
                let _ = DeleteObject(HGDIOBJ((*pointer).heading.0));
                drop(Box::from_raw(pointer));
                DefWindowProcW(window, message, wparam, lparam)
            }
            _ => DefWindowProcW(window, message, wparam, lparam),
        }
    }
}
unsafe fn paint(panel: &Panel) {
    unsafe {
        let mut ps = PAINTSTRUCT::default();
        let dc = BeginPaint(panel.window, &mut ps);
        let mut bounds = RECT::default();
        let _ = GetClientRect(panel.window, &mut bounds);
        let memory = CreateCompatibleDC(Some(dc));
        let bitmap = CreateCompatibleBitmap(dc, bounds.right.max(1), bounds.bottom.max(1));
        let old = SelectObject(memory, HGDIOBJ(bitmap.0));
        fill(memory, bounds, palette().app);
        let rect = |x, y, w, h| RECT {
            left: panel.px(x),
            top: panel.px(y),
            right: panel.px(x + w),
            bottom: panel.px(y + h),
        };
        text(
            memory,
            panel.heading,
            "USB diagnostics",
            rect(24, 20, 390, 38),
            palette().text,
            DT_LEFT | DT_VCENTER | DT_SINGLELINE,
        );
        let running = panel.last.as_ref().is_some_and(|s| s.running);
        let color = if running {
            palette().success
        } else {
            palette().muted
        };
        rounded(
            memory,
            rect(548, 24, 148, 32),
            palette().surface,
            palette().border,
            panel.px(8),
        );
        text(
            memory,
            panel.theme.font,
            if running {
                "●  Running"
            } else {
                "●  Stopped"
            },
            rect(558, 24, 128, 32),
            color,
            DT_CENTER | DT_SINGLELINE | DT_VCENTER,
        );
        for (pedals, y, title) in [(false, 82, "Wheel"), (true, 194, "Pedals")] {
            rounded(
                memory,
                rect(24, y, 672, if pedals { 76 } else { 96 }),
                palette().surface,
                palette().border,
                panel.px(10),
            );
            if let Some(status) = &panel.last {
                let monitor = if pedals {
                    &status.pedals
                } else {
                    &status.wheel
                };
                let color = health_color(health(status.running, monitor));
                device_symbol(
                    memory,
                    panel.px(92),
                    panel.px(y + 28),
                    panel.px(14),
                    pedals,
                    color,
                );
                text(
                    memory,
                    panel.theme.font,
                    title,
                    rect(74, y + 48, 82, 20),
                    palette().text,
                    DT_LEFT | DT_VCENTER | DT_SINGLELINE,
                );
                text(
                    memory,
                    panel.theme.font,
                    health_label(status.running, monitor),
                    rect(518, y + 20, 160, 26),
                    color,
                    DT_LEFT | DT_VCENTER | DT_SINGLELINE,
                );
            }
        }
        text(
            memory,
            panel.theme.font,
            "Steering axis",
            rect(178, 138, 166, 28),
            palette().muted,
            DT_LEFT | DT_VCENTER | DT_SINGLELINE,
        );
        if let Some(status) = &panel.last {
            let usb = if status.usb.contains("trace recording (") {
                "USB trace on"
            } else if status.usb.to_lowercase().contains("pending")
                || status.usb.contains("Starting")
            {
                "USB permission pending"
            } else {
                "USB trace off"
            };
            text(
                memory,
                panel.theme.font,
                usb,
                rect(516, 342, 180, 24),
                if usb == "USB trace on" {
                    palette().muted
                } else {
                    palette().warning
                },
                DT_RIGHT | DT_VCENTER | DT_SINGLELINE,
            );
            let ui_error = panel.ui.borrow().error.clone();
            let detail = if !panel.note.is_empty() {
                panel.note.as_str()
            } else if let Some(error) = &ui_error {
                error.as_str()
            } else if needs_recovery(status) && status.running {
                "Stop recording to recover the USB trace."
            } else if !status.running && status.has_capture {
                status.message.as_str()
            } else if usb != "USB trace on" {
                status.usb.as_str()
            } else {
                ""
            };
            text(
                memory,
                panel.theme.font,
                detail,
                rect(24, 368, 672, 18),
                palette().muted,
                DT_LEFT | DT_VCENTER | DT_SINGLELINE,
            );
            let saved = if let Some(error) = &status.report_error {
                format!("Save failed: {error}")
            } else if status.report_pending {
                "Saving the preceding 5 minutes…".into()
            } else if let Some(path) = &status.export_path {
                let when = status.exported_utc.as_deref().unwrap_or("");
                format!(
                    "Saved {} UTC · {:.1} MB · {}",
                    when.get(11..19).unwrap_or(when),
                    status.export_bytes.unwrap_or(0) as f64 / 1_048_576.0,
                    path.file_name().unwrap_or_default().to_string_lossy()
                )
            } else {
                "Report saves the preceding 5 minutes. Recording keeps running.".into()
            };
            text(
                memory,
                panel.theme.font,
                &saved,
                rect(24, 448, 672, 24),
                if status.report_error.is_some() {
                    palette().danger
                } else {
                    palette().muted
                },
                DT_LEFT | DT_VCENTER | DT_SINGLELINE,
            );
        }
        let _ = BitBlt(
            dc,
            ps.rcPaint.left,
            ps.rcPaint.top,
            ps.rcPaint.right - ps.rcPaint.left,
            ps.rcPaint.bottom - ps.rcPaint.top,
            Some(memory),
            ps.rcPaint.left,
            ps.rcPaint.top,
            SRCCOPY,
        );
        SelectObject(memory, old);
        let _ = DeleteObject(HGDIOBJ(bitmap.0));
        let _ = DeleteDC(memory);
        let _ = EndPaint(panel.window, &ps);
    }
}

#[cfg(test)]
pub(super) mod tests {
    use super::*;
    use crate::wheel::{WheelInputSource, WheelReading, WheelUsbTrace};
    use std::{path::Path, sync::Arc, time::Duration};
    use windows::Win32::UI::Input::KeyboardAndMouse::GetFocus;
    struct NoInput;
    impl WheelInputSource for NoInput {
        fn enumerate(&mut self) -> Result<Vec<WheelDevice>, String> {
            Ok(Vec::new())
        }
        fn select(&mut self, _: &str, _: u8, _: isize) -> Result<(), String> {
            panic!("UI smoke never acquires input")
        }
        fn read(&mut self) -> WheelReading {
            panic!("UI smoke never reads input")
        }
    }
    struct NoUsb;
    impl WheelUsbTrace for NoUsb {
        fn status(&mut self) -> String {
            "Off".into()
        }
        fn safe_to_export(&mut self) -> bool {
            true
        }
        fn start(&mut self, _: &Path) -> Result<(), String> {
            panic!("UI smoke never starts USB")
        }
        fn heartbeat(&mut self) {}
        fn stop(&mut self) -> Result<(), String> {
            Ok(())
        }
    }
    pub(crate) fn handle() -> WheelHandle {
        let root =
            std::env::temp_dir().join(format!("coach-diagnostics-ui-{}", crate::util::uuid_v4()));
        WheelHandle::new(
            root.join("captures"),
            root.join("exports"),
            Arc::new(|| Box::new(NoInput)),
            Box::new(NoUsb),
        )
    }
    unsafe fn owner() -> HWND {
        unsafe {
            CreateWindowExW(
                WINDOW_EX_STYLE(0),
                PCWSTR(wide("STATIC").as_ptr()),
                PCWSTR(wide("Diagnostics software-only test").as_ptr()),
                WINDOW_STYLE(0),
                0,
                0,
                0,
                0,
                None,
                None,
                None,
                None,
            )
            .unwrap()
        }
    }
    #[test]
    fn panel_and_native_controls_keep_per_monitor_v2_awareness_after_creation_scope() {
        use windows::Win32::UI::HiDpi::{
            AreDpiAwarenessContextsEqual, DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2,
            GetThreadDpiAwarenessContext, GetWindowDpiAwarenessContext,
        };
        let wheel = handle();
        let owner = unsafe { owner() };
        let mut window = WheelPanel::default();
        // Create the real native panel hidden: this checks actual Windows window awareness and
        // the current monitor's DPI without taking focus or claiming mixed-monitor validation.
        unsafe {
            let caller_context = GetThreadDpiAwarenessContext();
            window.window = create(owner, wheel.clone(), window.ui.clone()).unwrap();
            assert!(
                AreDpiAwarenessContextsEqual(GetThreadDpiAwarenessContext(), caller_context,)
                    .as_bool()
            );
            assert!(
                AreDpiAwarenessContextsEqual(
                    GetWindowDpiAwarenessContext(window.window),
                    DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2,
                )
                .as_bool()
            );
            let native_dpi = GetDpiForWindow(window.window);
            assert!(native_dpi >= 96);
            let panel = state(window.window).unwrap();
            assert_eq!(panel.dpi, native_dpi);
            for control in &panel.controls {
                assert!(
                    AreDpiAwarenessContextsEqual(
                        GetWindowDpiAwarenessContext(control.window),
                        DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2,
                    )
                    .as_bool()
                );
                assert_eq!(GetDpiForWindow(control.window), native_dpi);
            }
            assert!(!IsWindowVisible(window.window).as_bool());
        }
        drop(window);
        unsafe {
            let _ = DestroyWindow(owner);
        }
        assert!(wheel.shutdown_wait(Duration::from_secs(2)));
    }
    #[test]
    fn dark_panel_keyboard_changed_only_updates_and_synthetic_150_percent_layout() {
        let wheel = handle();
        let owner = unsafe { owner() };
        let mut window = WheelPanel::default();
        window.show(owner, &wheel).unwrap();
        unsafe {
            let panel = state(window.window).unwrap();
            let mut snapshot = wheel.try_snapshot().unwrap();
            snapshot.devices = vec![
                WheelDevice {
                    id: "fake-wheel".into(),
                    name: "SIMAGIC Alpha EVO Wheelbase".into(),
                    product_guid: None,
                },
                WheelDevice {
                    id: "fake-pedals".into(),
                    name: "SIMAGIC P1000 Pedals".into(),
                    product_guid: None,
                },
            ];
            snapshot.wheel.config.device = Some(snapshot.devices[0].clone());
            snapshot.pedals.config.device = Some(snapshot.devices[1].clone());
            snapshot.wheel.config.axes = vec![2];
            panel.render(snapshot.clone());
            assert_eq!(
                SendMessageW(panel.control(WHEEL_AXIS), CB_GETCOUNT, None, None).0,
                6
            );
            assert_eq!(
                SendMessageW(panel.control(WHEEL_AXIS), CB_GETCURSEL, None, None).0,
                2
            );
            SendMessageW(
                panel.control(WHEEL_AXIS),
                CB_SETCURSEL,
                Some(WPARAM(4)),
                None,
            );
            SendMessageW(
                window.window,
                WM_COMMAND,
                Some(WPARAM(WHEEL_AXIS | ((CBN_SELCHANGE as usize) << 16))),
                None,
            );
            let applied = std::time::Instant::now();
            while wheel
                .try_snapshot()
                .is_none_or(|status| status.wheel.config.axes != [4])
            {
                assert!(
                    applied.elapsed() < Duration::from_secs(2),
                    "axis choice did not reach recorder"
                );
                std::thread::sleep(Duration::from_millis(10));
            }
            assert!(
                !wheel.try_snapshot().unwrap().running,
                "selecting an axis does not start hardware monitoring"
            );
            snapshot.wheel.config.axes = vec![4];
            snapshot.running = true;
            snapshot.has_capture = true;
            snapshot.last_sample_age_ms = Some(20);
            snapshot.sample_count = 1234;
            snapshot.wheel.availability = "Available".into();
            snapshot.wheel.last_sample_age_ms = Some(20);
            snapshot.wheel.axes.push(crate::wheel::AxisStatus {
                axis: 4,
                value: Some(32767),
                ..Default::default()
            });
            snapshot.pedals.availability = "Available".into();
            snapshot.pedals.last_sample_age_ms = Some(20);
            snapshot.pedals.axes.push(crate::wheel::AxisStatus {
                axis: 0,
                value: Some(1024),
                ..Default::default()
            });
            snapshot.usb = "USB state trace recording (3/3 USB 3 stack providers).".into();
            panel.render(snapshot.clone());
            assert_eq!(
                SendMessageW(panel.control(WHEEL_AXIS), CB_GETCURSEL, None, None).0,
                4
            );
            let labels = panel.labels.clone();
            let enabled = panel.enabled.clone();
            let checks = panel.checks.clone();
            panel.render(snapshot.clone());
            assert_eq!(panel.labels, labels);
            assert_eq!(panel.enabled, enabled);
            assert_eq!(panel.checks, checks);
            let _ = SetFocus(Some(panel.control(STARTUP)));
            let tab = MSG {
                hwnd: panel.control(STARTUP),
                message: WM_KEYDOWN,
                wParam: WPARAM(windows::Win32::UI::Input::KeyboardAndMouse::VK_TAB.0 as usize),
                ..Default::default()
            };
            assert!(window.translate(&tab));
            assert_eq!(GetFocus(), panel.control(SHOW_OVERLAY));
            if let Ok(path) = std::env::var("COACH_WHEEL_UI_SNAPSHOT") {
                save_bitmap(window.window, Path::new(&path));
            }
            // This deliberately injected DPI message checks layout/rendering at 150%. It does
            // not simulate a physical move between monitors; actual awareness is checked below.
            let mut outer = RECT {
                left: 0,
                top: 0,
                right: WIDTH * 3 / 2,
                bottom: HEIGHT * 3 / 2,
            };
            let _ = AdjustWindowRectExForDpi(
                &mut outer,
                WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX | WS_CLIPCHILDREN,
                false,
                WS_EX_CONTROLPARENT,
                144,
            );
            SendMessageW(
                window.window,
                WM_DPICHANGED,
                Some(WPARAM(144 | (144 << 16))),
                Some(LPARAM((&outer as *const RECT) as isize)),
            );
            assert_eq!(panel.px(20), 30);
            if let Ok(path) = std::env::var("COACH_WHEEL_UI_SNAPSHOT_150") {
                save_bitmap(window.window, Path::new(&path));
            }
            let escape = MSG {
                hwnd: panel.control(CLOSE),
                message: WM_KEYDOWN,
                wParam: WPARAM(windows::Win32::UI::Input::KeyboardAndMouse::VK_ESCAPE.0 as usize),
                ..Default::default()
            };
            assert!(window.translate(&escape));
            assert!(!IsWindowVisible(window.window).as_bool());
        }
        drop(window);
        unsafe {
            let _ = DestroyWindow(owner);
        }
        assert!(wheel.shutdown_wait(Duration::from_secs(2)));
    }
    pub(crate) unsafe fn save_bitmap(window: HWND, path: &Path) {
        use windows::Win32::Graphics::Gdi::*;
        // SAFETY: captures only the isolated test window; GDI allocations are restored/released.
        unsafe {
            let _ = RedrawWindow(
                Some(window),
                None,
                None,
                RDW_UPDATENOW | RDW_ALLCHILDREN | RDW_INVALIDATE,
            );
            let mut rect = RECT::default();
            GetClientRect(window, &mut rect).unwrap();
            let (width, height) = (rect.right - rect.left, rect.bottom - rect.top);
            let dc = GetDC(Some(window));
            let copy = CreateCompatibleDC(Some(dc));
            let bitmap = CreateCompatibleBitmap(dc, width, height);
            let old = SelectObject(copy, HGDIOBJ(bitmap.0));
            BitBlt(copy, 0, 0, width, height, Some(dc), 0, 0, SRCCOPY).unwrap();
            SelectObject(copy, old);
            let mut info = BITMAPINFO {
                bmiHeader: BITMAPINFOHEADER {
                    biSize: std::mem::size_of::<BITMAPINFOHEADER>() as u32,
                    biWidth: width,
                    biHeight: -height,
                    biPlanes: 1,
                    biBitCount: 32,
                    biCompression: BI_RGB.0,
                    ..Default::default()
                },
                ..Default::default()
            };
            let mut pixels = vec![0_u8; (width * height * 4) as usize];
            assert_ne!(
                GetDIBits(
                    copy,
                    bitmap,
                    0,
                    height as u32,
                    Some(pixels.as_mut_ptr().cast()),
                    &mut info,
                    DIB_RGB_COLORS
                ),
                0
            );
            let mut bmp = Vec::new();
            bmp.extend_from_slice(b"BM");
            bmp.extend_from_slice(&(54 + pixels.len() as u32).to_le_bytes());
            bmp.extend_from_slice(&[0; 4]);
            bmp.extend_from_slice(&54_u32.to_le_bytes());
            bmp.extend_from_slice(&40_u32.to_le_bytes());
            bmp.extend_from_slice(&width.to_le_bytes());
            bmp.extend_from_slice(&(-height).to_le_bytes());
            bmp.extend_from_slice(&1_u16.to_le_bytes());
            bmp.extend_from_slice(&32_u16.to_le_bytes());
            bmp.extend_from_slice(&[0; 24]);
            bmp.extend_from_slice(&pixels);
            if let Some(parent) = path.parent() {
                std::fs::create_dir_all(parent).unwrap();
            }
            std::fs::write(path, bmp).unwrap();
            let _ = DeleteObject(HGDIOBJ(bitmap.0));
            let _ = DeleteDC(copy);
            ReleaseDC(Some(window), dc);
        }
    }
}

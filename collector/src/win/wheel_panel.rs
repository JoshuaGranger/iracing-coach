//! Optional, local wheel recorder controls. The modeless window never owns capture: hiding it
//! cancels its 1 Hz UI timer, while the recorder's worker keeps running in the tray.

use std::path::PathBuf;
use std::time::{Duration, Instant};

use windows::Win32::Foundation::{HINSTANCE, HWND, LPARAM, LRESULT, RECT, WPARAM};
use windows::Win32::Graphics::Gdi::{
    CLIP_DEFAULT_PRECIS, COLOR_WINDOW, CreateFontW, DEFAULT_CHARSET, DEFAULT_QUALITY, DeleteObject,
    HBRUSH, HFONT, HGDIOBJ, OUT_DEFAULT_PRECIS,
};
use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::UI::Controls::SetScrollInfo;
use windows::Win32::UI::HiDpi::{
    AdjustWindowRectExForDpi, DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2, GetDpiForWindow,
    SetThreadDpiAwarenessContext,
};
use windows::Win32::UI::Input::KeyboardAndMouse::{EnableWindow, GetFocus, SetFocus};
use windows::Win32::UI::WindowsAndMessaging::*;
use windows::core::PCWSTR;

use super::wide;
use crate::wheel::{WheelCommand, WheelHandle, WheelStatus};

const CLASS: &str = "CoachWheelDiagnostics";
const WIDTH: i32 = 700;
const HEIGHT: i32 = 610;
const POLL: usize = 1;
const REFRESH: usize = 301;
const DEVICE: usize = 302;
const AXIS: usize = 303;
const ENABLE: usize = 304;
const DISABLE: usize = 305;
const MARK: usize = 306;
const EXPORT: usize = 307;
const REVEAL: usize = 308;
const USB: usize = 309;
const CLOSE: usize = 2;
const MESSAGE: usize = 310;
const INPUT: usize = 311;
const USB_STATUS: usize = 312;
const RECOVER_USB: usize = 313;
const REPOSITION: u32 = WM_APP + 23;

#[derive(Default)]
pub struct WheelPanel {
    window: HWND,
}

impl WheelPanel {
    pub fn show(&mut self, owner: HWND, wheel: &WheelHandle) -> Result<(), String> {
        // SAFETY: all window/control operations run on the collector's UI thread.
        unsafe {
            if self.window.is_invalid() {
                self.window = create(owner, wheel.clone()).map_err(|e| e.to_string())?;
            }
            let _ = ShowWindow(self.window, SW_SHOWNORMAL);
            SetTimer(Some(self.window), POLL, 1_000, None);
            if let Some(panel) = state(self.window) {
                panel.refresh();
            }
            let _ = SetForegroundWindow(self.window);
            let _ = SetFocus(Some(
                GetDlgItem(Some(self.window), REFRESH as i32).unwrap_or_default(),
            ));
        }
        Ok(())
    }

    /// Routes Tab, Shift-Tab, mnemonic keys and Escape through the native dialog manager.
    pub fn translate(&self, message: &MSG) -> bool {
        // SAFETY: the modeless window stays alive until this owner is dropped.
        unsafe {
            if self.window.is_invalid() || !IsWindowVisible(self.window).as_bool() {
                return false;
            }
            let used = IsDialogMessageW(self.window, message).as_bool();
            if used && let Some(panel) = state(self.window) {
                panel.reveal_focus();
            }
            used
        }
    }
}

impl Drop for WheelPanel {
    fn drop(&mut self) {
        if !self.window.is_invalid() {
            // SAFETY: only destroys the local dialog and its controls; never joins workers.
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
    controls: Vec<Control>,
    font: HFONT,
    dpi: u32,
    scroll: i32,
    devices: Vec<(String, String)>,
    last: Option<WheelStatus>,
    pending_until: Option<Instant>,
    note: String,
}

impl Panel {
    fn control(&self, id: usize) -> HWND {
        // SAFETY: querying an application-owned child window.
        unsafe { GetDlgItem(Some(self.window), id as i32).unwrap_or_default() }
    }

    fn text(&self, id: usize, text: &str) {
        // SAFETY: the string is copied by our own standard STATIC control.
        unsafe {
            let _ = SetWindowTextW(self.control(id), PCWSTR(wide(text).as_ptr()));
        }
    }

    fn enabled(&self, id: usize, enabled: bool) {
        // SAFETY: application-owned standard control on its UI thread.
        unsafe {
            let _ = EnableWindow(self.control(id), enabled);
        }
    }

    fn selected(&self) -> Option<usize> {
        // SAFETY: standard synchronous combobox message, no application worker called.
        let selected = unsafe { SendMessageW(self.control(DEVICE), CB_GETCURSEL, None, None).0 };
        usize::try_from(selected).ok()?.checked_sub(1)
    }

    fn refresh(&mut self) {
        if let Some(snapshot) = self.wheel.try_snapshot() {
            self.render(snapshot);
        } else {
            self.text(MESSAGE, "Recorder state is updating. Try again shortly.");
            for id in [
                REFRESH,
                DEVICE,
                AXIS,
                ENABLE,
                DISABLE,
                MARK,
                EXPORT,
                USB,
                RECOVER_USB,
                REVEAL,
            ] {
                self.enabled(id, false);
            }
        }
    }

    fn render(&mut self, snapshot: WheelStatus) {
        let devices: Vec<_> = snapshot
            .devices
            .iter()
            .map(|d| (d.id.clone(), d.name.clone()))
            .collect();
        if self.devices != devices {
            let previous = self
                .selected()
                .and_then(|i| self.devices.get(i))
                .map(|d| d.0.clone());
            self.devices = devices;
            // SAFETY: standard control copies these strings and contains no driver input.
            unsafe {
                let combo = self.control(DEVICE);
                SendMessageW(combo, CB_RESETCONTENT, None, None);
                combo_add(combo, "Select the wheel");
                for (_, name) in &self.devices {
                    combo_add(combo, name);
                }
                let selected = previous
                    .and_then(|id| self.devices.iter().position(|d| d.0 == id))
                    .map_or(0, |i| i + 1);
                SendMessageW(combo, CB_SETCURSEL, Some(WPARAM(selected)), None);
            }
        }
        self.text(
            INPUT,
            &format!(
                "Input: {}    Raw axis: {}    Retained incidents: {}",
                snapshot.availability,
                snapshot
                    .raw_axis
                    .map_or_else(|| "Unavailable".into(), |x| x.to_string()),
                snapshot.incidents
            ),
        );
        self.text(
            USB_STATUS,
            &format!(
                "USB trace: {}\nStops with the recorder or Coach. No service is installed.",
                snapshot.usb
            ),
        );
        let message = if self.note.is_empty() {
            snapshot.message.clone()
        } else {
            format!("{} {}", snapshot.message, self.note)
        };
        self.text(MESSAGE, &message);
        let busy = snapshot.busy
            || self
                .pending_until
                .is_some_and(|deadline| Instant::now() < deadline);
        self.enabled(REFRESH, !busy && !snapshot.running);
        self.enabled(
            DEVICE,
            !busy && !snapshot.running && !self.devices.is_empty(),
        );
        self.enabled(AXIS, !busy && !snapshot.running);
        self.enabled(
            ENABLE,
            !busy && !snapshot.running && self.selected().is_some(),
        );
        self.enabled(DISABLE, !busy && snapshot.running);
        self.enabled(MARK, !busy && snapshot.running);
        self.enabled(
            EXPORT,
            !busy && snapshot.has_capture && (snapshot.running || snapshot.can_export),
        );
        self.enabled(USB, !busy && snapshot.running && snapshot.can_start_usb);
        self.enabled(
            RECOVER_USB,
            !busy && !snapshot.running && snapshot.can_recover_usb,
        );
        self.enabled(REVEAL, snapshot.export_path.is_some());
        self.last = Some(snapshot);
    }

    fn send(&mut self, command: WheelCommand) {
        if self.wheel.send(command) {
            self.note.clear();
            self.pending_until = Some(Instant::now() + Duration::from_millis(900));
        } else {
            self.note = "Recorder action could not be queued. Try again shortly.".into();
        }
        self.refresh();
    }

    fn command(&mut self, id: usize) {
        match id {
            REFRESH => self.send(WheelCommand::Refresh),
            DEVICE | AXIS => self.refresh(),
            ENABLE => {
                let Some(index) = self.selected() else {
                    return;
                };
                let Some(device) = self
                    .last
                    .as_ref()
                    .and_then(|s| s.devices.get(index))
                    .cloned()
                else {
                    return;
                };
                // SAFETY: standard combobox selection is in the six fixed axis entries.
                let axis = unsafe { SendMessageW(self.control(AXIS), CB_GETCURSEL, None, None).0 };
                if let Ok(axis) = u8::try_from(axis) {
                    self.send(WheelCommand::Enable {
                        device,
                        axis,
                        window: self.owner.0 as isize,
                    });
                }
            }
            DISABLE => self.send(WheelCommand::Disable),
            MARK => self.send(WheelCommand::MarkIncident),
            EXPORT => self.send(WheelCommand::Export),
            USB => self.send(WheelCommand::StartUsb),
            RECOVER_USB => self.send(WheelCommand::RecoverUsb),
            REVEAL => {
                if let Some(path) = self.last.as_ref().and_then(|s| s.export_path.clone()) {
                    reveal(path);
                }
            }
            CLOSE => self.hide(),
            _ => {}
        }
    }

    fn hide(&self) {
        // SAFETY: hide only this dialog; capture and USB lifetime are owned by the worker.
        unsafe {
            let _ = KillTimer(Some(self.window), POLL);
            let _ = ShowWindow(self.window, SW_HIDE);
        }
    }

    fn px(&self, value: i32) -> i32 {
        value * self.dpi as i32 / 96
    }

    fn layout(&mut self) {
        // SAFETY: owned font and controls; the previous font is removed after all controls switch.
        unsafe {
            let font = CreateFontW(
                -self.px(14),
                0,
                0,
                0,
                400,
                0,
                0,
                0,
                DEFAULT_CHARSET,
                OUT_DEFAULT_PRECIS,
                CLIP_DEFAULT_PRECIS,
                DEFAULT_QUALITY,
                0,
                PCWSTR(wide("Segoe UI").as_ptr()),
            );
            for control in &self.controls {
                SendMessageW(
                    control.window,
                    WM_SETFONT,
                    Some(WPARAM(font.0 as usize)),
                    Some(LPARAM(1)),
                );
            }
            if !self.font.is_invalid() {
                let _ = DeleteObject(HGDIOBJ(self.font.0));
            }
            self.font = font;
            self.reposition();
        }
    }

    fn reposition(&mut self) {
        // SAFETY: positions our own children and adjusts this window's native scrollbar.
        unsafe {
            let mut client = RECT::default();
            let _ = GetClientRect(self.window, &mut client);
            let total = self.px(HEIGHT);
            self.scroll = self.scroll.clamp(0, (total - client.bottom).max(0));
            for control in &self.controls {
                let b = control.bounds;
                let _ = SetWindowPos(
                    control.window,
                    None,
                    self.px(b.left),
                    self.px(b.top) - self.scroll,
                    self.px(b.right - b.left),
                    self.px(b.bottom - b.top),
                    SWP_NOACTIVATE | SWP_NOZORDER,
                );
            }
            let info = SCROLLINFO {
                cbSize: std::mem::size_of::<SCROLLINFO>() as u32,
                fMask: SIF_RANGE | SIF_PAGE | SIF_POS,
                nMin: 0,
                nMax: total - 1,
                nPage: client.bottom.max(1) as u32,
                nPos: self.scroll,
                ..Default::default()
            };
            SetScrollInfo(self.window, SB_VERT, &info, true);
        }
    }

    fn scroll(&mut self, action: SCROLLBAR_COMMAND) {
        let mut info = SCROLLINFO {
            cbSize: std::mem::size_of::<SCROLLINFO>() as u32,
            fMask: SIF_ALL,
            ..Default::default()
        };
        // SAFETY: scrollbar query for this owned window.
        unsafe {
            let _ = GetScrollInfo(self.window, SB_VERT, &mut info);
        }
        self.scroll = match action {
            SB_LINEUP => self.scroll - self.px(24),
            SB_LINEDOWN => self.scroll + self.px(24),
            SB_PAGEUP => self.scroll - info.nPage as i32,
            SB_PAGEDOWN => self.scroll + info.nPage as i32,
            SB_THUMBTRACK | SB_THUMBPOSITION => info.nTrackPos,
            SB_TOP => 0,
            SB_BOTTOM => info.nMax,
            _ => self.scroll,
        };
        self.reposition();
    }

    fn reveal_focus(&mut self) {
        // SAFETY: native dialog focus is an owned child, if present.
        let focus = unsafe { GetFocus() };
        let Some(control) = self.controls.iter().find(|c| c.window == focus) else {
            return;
        };
        let top = self.px(control.bounds.top);
        let bottom = top + self.px((control.bounds.bottom - control.bounds.top).min(34));
        let mut client = RECT::default();
        // SAFETY: querying this owned window's client rectangle.
        unsafe {
            let _ = GetClientRect(self.window, &mut client);
        }
        if top < self.scroll {
            self.scroll = top;
        } else if bottom > self.scroll + client.bottom {
            self.scroll = bottom - client.bottom;
        }
        self.reposition();
    }
}

fn reveal(path: PathBuf) {
    // Shell startup is outside the message loop and never reads/copies the archive.
    let _ = std::thread::Builder::new()
        .name("wheel-bundle-reveal".into())
        .spawn(move || {
            let _ = std::process::Command::new("explorer.exe")
                .arg("/select,")
                .arg(path)
                .spawn();
        });
}

unsafe fn combo_add(window: HWND, text: &str) {
    // SAFETY: standard combobox copies the NUL-terminated text synchronously.
    unsafe {
        SendMessageW(
            window,
            CB_ADDSTRING,
            None,
            Some(LPARAM(wide(text).as_ptr() as isize)),
        );
    }
}

unsafe fn state(window: HWND) -> Option<&'static mut Panel> {
    // SAFETY: set once after creation, cleared and freed on WM_NCDESTROY, UI thread only.
    unsafe { (GetWindowLongPtrW(window, GWLP_USERDATA) as *mut Panel).as_mut() }
}

unsafe fn create(owner: HWND, wheel: WheelHandle) -> windows::core::Result<HWND> {
    // SAFETY: class/child creation and owned Box stay on this UI thread.
    unsafe {
        let previous = SetThreadDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
        let result = create_inner(owner, wheel);
        SetThreadDpiAwarenessContext(previous);
        result
    }
}

unsafe fn create_inner(owner: HWND, wheel: WheelHandle) -> windows::core::Result<HWND> {
    // SAFETY: standard window creation; Windows copies names before returning.
    unsafe {
        let instance: HINSTANCE = GetModuleHandleW(PCWSTR::null())?.into();
        let class = WNDCLASSEXW {
            cbSize: std::mem::size_of::<WNDCLASSEXW>() as u32,
            lpfnWndProc: Some(window_proc),
            hInstance: instance,
            hCursor: LoadCursorW(None, IDC_ARROW)?,
            hbrBackground: HBRUSH((COLOR_WINDOW.0 + 1) as usize as *mut _),
            lpszClassName: PCWSTR(wide(CLASS).as_ptr()),
            ..Default::default()
        };
        RegisterClassExW(&class);
        let window = CreateWindowExW(
            WS_EX_CONTROLPARENT,
            PCWSTR(wide(CLASS).as_ptr()),
            PCWSTR(wide("Wheel interruption recorder — iRacing Coach").as_ptr()),
            WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX | WS_VSCROLL,
            CW_USEDEFAULT,
            CW_USEDEFAULT,
            WIDTH,
            HEIGHT,
            Some(owner),
            None,
            Some(instance),
            None,
        )?;
        let mut panel = Box::new(Panel {
            window,
            owner,
            wheel,
            controls: Vec::new(),
            font: HFONT::default(),
            dpi: GetDpiForWindow(window).max(96),
            scroll: 0,
            devices: Vec::new(),
            last: None,
            pending_until: None,
            note: String::new(),
        });
        let controls = [
            (
                "STATIC",
                "Wheel recorder  -  start before racing, export when parked",
                0,
                16,
                14,
                664,
                26,
            ),
            (
                "STATIC",
                "Keeps the last 60 minutes. Input interruptions are marked automatically.\nYou can close this window and keep racing; capture continues in the tray.",
                0,
                16,
                48,
                664,
                42,
            ),
            ("STATIC", "1. Select your wheel", 0, 16, 104, 446, 20),
            ("BUTTON", "&Refresh", REFRESH, 552, 96, 128, 32),
            ("COMBOBOX", "", DEVICE, 16, 132, 446, 240),
            ("STATIC", "Steering &axis", 0, 480, 104, 200, 20),
            ("COMBOBOX", "", AXIS, 480, 132, 200, 240),
            ("BUTTON", "&Start recording", ENABLE, 16, 178, 170, 38),
            ("BUTTON", "&Stop", DISABLE, 198, 178, 114, 38),
            ("BUTTON", "&Mark issue now", MARK, 324, 178, 174, 38),
            ("STATIC", "", INPUT, 16, 232, 664, 26),
            ("STATIC", "", MESSAGE, 16, 266, 664, 46),
            ("BUTTON", "Stop and &save capture", EXPORT, 16, 324, 220, 38),
            ("BUTTON", "&Open saved capture", REVEAL, 248, 324, 206, 38),
            (
                "STATIC",
                "Saved capture includes the hour's axis history and up to 8 issue windows (10 s before,\n20 s after). A held axis is normal; input errors do not prove the cause of an interruption.",
                0,
                16,
                374,
                664,
                42,
            ),
            (
                "STATIC",
                "Optional USB details  -  administrator permission required",
                0,
                16,
                432,
                664,
                24,
            ),
            ("BUTTON", "Start &USB trace", USB, 16, 464, 200, 32),
            (
                "BUTTON",
                "&Recover interrupted trace",
                RECOVER_USB,
                228,
                464,
                260,
                32,
            ),
            ("STATIC", "", USB_STATUS, 16, 508, 664, 46),
            (
                "STATIC",
                "Local only. USB details can include other device identifiers. Review before sharing.",
                0,
                16,
                564,
                538,
                32,
            ),
            ("BUTTON", "&Close", CLOSE, 566, 564, 114, 32),
        ];
        for (kind, text, id, x, y, w, h) in controls {
            let extra = match kind {
                "BUTTON" => WS_TABSTOP,
                "COMBOBOX" => WS_TABSTOP | WS_VSCROLL | WINDOW_STYLE(CBS_DROPDOWNLIST as u32),
                _ => WINDOW_STYLE(0),
            };
            match CreateWindowExW(
                WINDOW_EX_STYLE(0),
                PCWSTR(wide(kind).as_ptr()),
                PCWSTR(wide(text).as_ptr()),
                WS_CHILD | WS_VISIBLE | extra,
                x,
                y,
                w,
                h,
                Some(window),
                Some(HMENU(id as *mut _)),
                Some(instance),
                None,
            ) {
                Ok(child) => panel.controls.push(Control {
                    window: child,
                    bounds: RECT {
                        left: x,
                        top: y,
                        right: x + w,
                        bottom: y + h,
                    },
                }),
                Err(error) => {
                    let _ = DestroyWindow(window);
                    return Err(error);
                }
            }
        }
        combo_add(panel.control(DEVICE), "Select the wheel");
        SendMessageW(panel.control(DEVICE), CB_SETCURSEL, Some(WPARAM(0)), None);
        for axis in ["X (common steering)", "Y", "Z", "Rx", "Ry", "Rz"] {
            combo_add(panel.control(AXIS), axis);
        }
        SendMessageW(panel.control(AXIS), CB_SETCURSEL, Some(WPARAM(0)), None);
        let dpi = panel.dpi;
        let mut bounds = RECT {
            left: 0,
            top: 0,
            right: panel.px(WIDTH),
            bottom: panel.px(HEIGHT),
        };
        let _ = AdjustWindowRectExForDpi(
            &mut bounds,
            WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX | WS_VSCROLL,
            false,
            WS_EX_CONTROLPARENT,
            dpi,
        );
        let mut work = RECT::default();
        let _ = SystemParametersInfoW(
            SPI_GETWORKAREA,
            0,
            Some((&mut work as *mut RECT).cast()),
            SYSTEM_PARAMETERS_INFO_UPDATE_FLAGS(0),
        );
        let height = (bounds.bottom - bounds.top).min((work.bottom - work.top - 24).max(240));
        let width = bounds.right - bounds.left;
        let _ = SetWindowPos(
            window,
            None,
            work.left + ((work.right - work.left - width) / 2).max(0),
            work.top + 12,
            width,
            height,
            SWP_NOACTIVATE | SWP_NOZORDER,
        );
        panel.layout();
        panel.refresh();
        SetWindowLongPtrW(window, GWLP_USERDATA, Box::into_raw(panel) as isize);
        Ok(window)
    }
}

unsafe extern "system" fn window_proc(
    window: HWND,
    message: u32,
    wparam: WPARAM,
    lparam: LPARAM,
) -> LRESULT {
    // SAFETY: our modeless dialog class; state pointer is confined to this UI thread.
    unsafe {
        if message == WM_COMMAND
            && matches!(wparam.0 & 0xFFFF, DEVICE | AXIS)
            && (wparam.0 >> 16) & 0xFFFF != CBN_SELCHANGE as usize
        {
            // Disabling a focused combo synchronously notifies its parent. Focus/dropdown
            // notifications are not a selection change and must not re-enter Panel::render.
            return LRESULT(0);
        }
        if message == WM_NCDESTROY {
            let pointer = GetWindowLongPtrW(window, GWLP_USERDATA) as *mut Panel;
            SetWindowLongPtrW(window, GWLP_USERDATA, 0);
            if !pointer.is_null() {
                let panel = Box::from_raw(pointer);
                if !panel.font.is_invalid() {
                    let _ = DeleteObject(HGDIOBJ(panel.font.0));
                }
            }
        } else if message == WM_SIZE {
            // SetWindowPos/SetScrollInfo can synchronously re-enter this procedure. Defer layout
            // so a nested message never borrows Panel while its outer handler still uses it.
            if wparam.0 == SIZE_MINIMIZED as usize {
                let _ = KillTimer(Some(window), POLL);
            } else {
                if IsWindowVisible(window).as_bool() {
                    SetTimer(Some(window), POLL, 1_000, None);
                }
                let _ = PostMessageW(Some(window), REPOSITION, WPARAM(0), LPARAM(0));
            }
            return LRESULT(0);
        } else if matches!(
            message,
            WM_COMMAND
                | WM_TIMER
                | WM_CLOSE
                | REPOSITION
                | WM_VSCROLL
                | WM_MOUSEWHEEL
                | WM_DPICHANGED
                | WM_DESTROY
        ) && let Some(panel) = state(window)
        {
            match message {
                WM_COMMAND => {
                    panel.command(wparam.0 & 0xFFFF);
                    return LRESULT(0);
                }
                WM_TIMER if wparam.0 == POLL => {
                    if IsWindowVisible(window).as_bool() && !IsIconic(window).as_bool() {
                        panel.refresh();
                    }
                    return LRESULT(0);
                }
                WM_CLOSE => {
                    panel.hide();
                    return LRESULT(0);
                }
                REPOSITION => {
                    panel.reposition();
                    return LRESULT(0);
                }
                WM_VSCROLL => {
                    panel.scroll(SCROLLBAR_COMMAND((wparam.0 & 0xFFFF) as i32));
                    return LRESULT(0);
                }
                WM_MOUSEWHEEL => {
                    panel.scroll(if ((wparam.0 >> 16) as i16) > 0 {
                        SB_LINEUP
                    } else {
                        SB_LINEDOWN
                    });
                    return LRESULT(0);
                }
                WM_DPICHANGED => {
                    panel.dpi = (wparam.0 & 0xFFFF) as u32;
                    let suggested = &*(lparam.0 as *const RECT);
                    let _ = SetWindowPos(
                        window,
                        None,
                        suggested.left,
                        suggested.top,
                        suggested.right - suggested.left,
                        suggested.bottom - suggested.top,
                        SWP_NOACTIVATE | SWP_NOZORDER,
                    );
                    panel.layout();
                    return LRESULT(0);
                }
                WM_DESTROY => {
                    let _ = KillTimer(Some(window), POLL);
                }
                _ => {}
            }
        }
        DefWindowProcW(window, message, wparam, lparam)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::wheel::{WheelDevice, WheelInputSource, WheelReading, WheelUsbTrace};
    use std::path::Path;
    use std::sync::{Arc, Condvar, Mutex};
    use windows::Win32::UI::Input::KeyboardAndMouse::{IsWindowEnabled, VK_ESCAPE, VK_TAB};

    struct NoInput;
    impl WheelInputSource for NoInput {
        fn enumerate(&mut self) -> Result<Vec<WheelDevice>, String> {
            Ok(Vec::new())
        }
        fn select(&mut self, _: &str, _: u8, _: isize) -> Result<(), String> {
            panic!("UI smoke must not acquire input")
        }
        fn read(&mut self) -> WheelReading {
            panic!("UI smoke must not read input")
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
            panic!("UI smoke must not start USB tracing")
        }
        fn heartbeat(&mut self) {}
        fn stop(&mut self) -> Result<(), String> {
            Ok(())
        }
    }

    fn handle(factory: crate::wheel::SourceFactory) -> WheelHandle {
        let root = std::env::temp_dir().join(format!(
            "coach-wheel-ui-{}-{}",
            std::process::id(),
            crate::util::uuid_v4()
        ));
        WheelHandle::new(
            root.join("captures"),
            root.join("exports"),
            factory,
            Box::new(NoUsb),
        )
    }

    fn owner() -> HWND {
        // SAFETY: isolated test-thread window, never the installed collector's window.
        unsafe {
            CreateWindowExW(
                WINDOW_EX_STYLE(0),
                PCWSTR(wide("STATIC").as_ptr()),
                PCWSTR(wide("Coach wheel UI smoke owner").as_ptr()),
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

    fn enabled(panel: &Panel, id: usize) -> bool {
        // SAFETY: querying an isolated test control.
        unsafe { IsWindowEnabled(panel.control(id)).as_bool() }
    }

    #[test]
    fn native_dialog_default_off_selection_keyboard_dpi_and_busy_controls() {
        let wheel = handle(Arc::new(|| Box::new(NoInput)));
        let owner = owner();
        let mut window = WheelPanel::default();
        window.show(owner, &wheel).unwrap();
        // SAFETY: our isolated dialog on this test's GUI thread; no native input/ETW actions.
        unsafe {
            let panel = state(window.window).unwrap();
            assert!(enabled(panel, REFRESH));
            assert!(!enabled(panel, DEVICE));
            for id in [ENABLE, DISABLE, MARK, EXPORT, USB, REVEAL] {
                assert!(!enabled(panel, id));
            }
            assert!(!wheel.try_snapshot().unwrap().running);
            let tab = MSG {
                hwnd: panel.control(REFRESH),
                message: WM_KEYDOWN,
                wParam: WPARAM(VK_TAB.0 as usize),
                ..Default::default()
            };
            let axis_window = panel.control(AXIS);
            assert!(window.translate(&tab));
            assert_eq!(GetFocus(), axis_window);
            let panel = state(window.window).unwrap();

            let mut ready = WheelStatus::default();
            ready.devices.push(WheelDevice {
                id: "software-only".into(),
                name: "Software smoke wheel (no input)".into(),
                product_guid: None,
            });
            ready.message =
                "Choose the wheel and axis. This is a software-only smoke state.".into();
            panel.render(ready.clone());
            assert!(enabled(panel, DEVICE));
            assert!(!enabled(panel, ENABLE));
            SendMessageW(panel.control(DEVICE), CB_SETCURSEL, Some(WPARAM(1)), None);
            panel.render(ready.clone());
            assert!(enabled(panel, ENABLE));
            let mut interrupted = ready.clone();
            interrupted.can_recover_usb = true;
            interrupted.message =
                "Capture refused: rolling storage is full of unresolved USB traces.".into();
            interrupted.usb = "Cleanup needs an administrator prompt.".into();
            panel.render(interrupted.clone());
            assert!(
                enabled(panel, RECOVER_USB),
                "explicit USB recovery must remain reachable while capture is Off and storage is full"
            );
            for id in [DISABLE, MARK, EXPORT, USB] {
                assert!(!enabled(panel, id));
            }
            let _ = SetFocus(Some(panel.control(DEVICE)));
            interrupted.busy = true;
            panel.render(interrupted);
            for id in [ENABLE, USB, RECOVER_USB] {
                assert!(
                    !enabled(panel, id),
                    "pending recovery must not permit capture or another USB operation"
                );
            }
            assert_eq!(
                panel.selected(),
                Some(0),
                "disabling a focused combo must preserve selection without a nested refresh"
            );
            panel.render(ready.clone());
            if let Ok(path) = std::env::var("COACH_WHEEL_UI_SNAPSHOT") {
                save_bitmap(window.window, Path::new(&path));
            }

            panel.dpi = 144;
            let _ = SetWindowPos(
                window.window,
                None,
                0,
                0,
                1_064,
                500,
                SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE,
            );
            panel.layout();
            assert_eq!(panel.px(14), 21);
            let _ = SetFocus(Some(panel.control(CLOSE)));
            panel.reveal_focus();
            assert!(
                panel.scroll > 0,
                "high-DPI off-screen keyboard focus must scroll into view"
            );
            let mut active = ready;
            active.running = true;
            active.can_start_usb = true;
            active.can_recover_usb = true;
            active.has_capture = true;
            active.raw_axis = Some(32_000);
            active.availability = "Available".into();
            panel.render(active.clone());
            assert!(enabled(panel, MARK));
            assert!(enabled(panel, DISABLE));
            assert!(
                enabled(panel, EXPORT),
                "Stop and export must work during capture"
            );
            assert!(enabled(panel, USB));
            assert!(
                !enabled(panel, RECOVER_USB),
                "recovery-only operation must not interrupt an active capture"
            );
            assert!(!enabled(panel, REFRESH));
            active.busy = true;
            panel.render(active);
            for id in [REFRESH, ENABLE, DISABLE, MARK, EXPORT, USB, RECOVER_USB] {
                assert!(!enabled(panel, id));
            }
            assert!(enabled(panel, CLOSE));
            let escape = MSG {
                hwnd: panel.control(CLOSE),
                message: WM_KEYDOWN,
                wParam: WPARAM(VK_ESCAPE.0 as usize),
                ..Default::default()
            };
            assert!(window.translate(&escape));
            assert!(!IsWindowVisible(window.window).as_bool());
            assert!(
                !wheel.try_snapshot().unwrap().running,
                "rendering fake state must never acquire a device"
            );
        }
        drop(window);
        // SAFETY: isolated test owner teardown; recorder cleanup occurs outside the GUI handlers.
        unsafe {
            let _ = DestroyWindow(owner);
        }
        assert!(wheel.shutdown_wait(Duration::from_secs(2)));
    }

    struct SlowRefresh(Arc<(Mutex<bool>, Condvar)>);
    impl WheelInputSource for SlowRefresh {
        fn enumerate(&mut self) -> Result<Vec<WheelDevice>, String> {
            let (lock, cv) = &*self.0;
            let mut released = lock.lock().unwrap();
            while !*released {
                released = cv.wait(released).unwrap();
            }
            Ok(Vec::new())
        }
        fn select(&mut self, _: &str, _: u8, _: isize) -> Result<(), String> {
            panic!("no acquisition in smoke")
        }
        fn read(&mut self) -> WheelReading {
            panic!("no input in smoke")
        }
    }

    #[test]
    fn native_dialog_closes_while_fake_enumeration_is_blocked() {
        let gate = Arc::new((Mutex::new(false), Condvar::new()));
        let factory_gate = gate.clone();
        let wheel = handle(Arc::new(move || {
            Box::new(SlowRefresh(factory_gate.clone()))
        }));
        let owner = owner();
        let mut window = WheelPanel::default();
        window.show(owner, &wheel).unwrap();
        let queued = Instant::now();
        // SAFETY: owned dialog commands only; Refresh runs exclusively in a fake worker.
        unsafe {
            state(window.window).unwrap().command(REFRESH);
        }
        assert!(queued.elapsed() < Duration::from_millis(250));
        let wait = Instant::now();
        while !wheel.try_snapshot().is_some_and(|s| s.busy)
            && wait.elapsed() < Duration::from_secs(2)
        {
            std::thread::sleep(Duration::from_millis(10));
        }
        assert!(wheel.try_snapshot().unwrap().busy);
        let close = Instant::now();
        // SAFETY: hiding does not send a stop, wait, export, UAC or thread-join operation.
        unsafe {
            state(window.window).unwrap().hide();
        }
        assert!(close.elapsed() < Duration::from_millis(250));
        assert!(wheel.try_snapshot().unwrap().busy);
        {
            let (lock, cv) = &*gate;
            *lock.lock().unwrap() = true;
            cv.notify_all();
        }
        let wait = Instant::now();
        while wheel.try_snapshot().is_none_or(|s| s.busy) && wait.elapsed() < Duration::from_secs(2)
        {
            std::thread::sleep(Duration::from_millis(10));
        }
        assert!(!wheel.try_snapshot().unwrap().busy);
        assert!(wheel.try_snapshot().unwrap().devices.is_empty());
        drop(window);
        // SAFETY: isolated test owner teardown.
        unsafe {
            let _ = DestroyWindow(owner);
        }
        assert!(wheel.shutdown_wait(Duration::from_secs(2)));
    }

    struct BlockedRecovery {
        gate: Arc<(Mutex<bool>, Condvar)>,
        calls: Arc<std::sync::atomic::AtomicUsize>,
    }
    impl WheelUsbTrace for BlockedRecovery {
        fn status(&mut self) -> String {
            "Software recovery complete; no trace started.".into()
        }
        fn safe_to_export(&mut self) -> bool {
            true
        }
        fn start(&mut self, _: &Path) -> Result<(), String> {
            panic!("recovery must never start a USB trace")
        }
        fn heartbeat(&mut self) {}
        fn stop(&mut self) -> Result<(), String> {
            Ok(())
        }
        fn recover(&mut self) -> Result<(), String> {
            self.calls
                .fetch_add(1, std::sync::atomic::Ordering::Relaxed);
            let (lock, cv) = &*self.gate;
            let mut released = lock.lock().unwrap();
            while !*released {
                released = cv.wait(released).unwrap();
            }
            Ok(())
        }
    }

    #[test]
    fn explicit_recovery_while_off_never_starts_capture_and_does_not_block_close() {
        use std::sync::atomic::{AtomicUsize, Ordering};
        let root = std::env::temp_dir().join(format!(
            "coach-wheel-ui-recovery-{}",
            crate::util::uuid_v4()
        ));
        let gate = Arc::new((Mutex::new(false), Condvar::new()));
        let calls = Arc::new(AtomicUsize::new(0));
        let wheel = WheelHandle::new(
            root.join("captures"),
            root.join("exports"),
            Arc::new(|| Box::new(NoInput)),
            Box::new(BlockedRecovery {
                gate: gate.clone(),
                calls: calls.clone(),
            }),
        );
        let owner = owner();
        let mut window = WheelPanel::default();
        window.show(owner, &wheel).unwrap();
        let queued = Instant::now();
        // SAFETY: the explicit control talks only to our blocked software recovery actor.
        unsafe {
            let panel = state(window.window).unwrap();
            assert!(enabled(panel, RECOVER_USB));
            assert!(!enabled(panel, USB));
            panel.command(RECOVER_USB);
        }
        assert!(queued.elapsed() < Duration::from_millis(250));
        let wait = Instant::now();
        while (!wheel.try_snapshot().is_some_and(|s| s.busy) || calls.load(Ordering::Relaxed) != 1)
            && wait.elapsed() < Duration::from_secs(2)
        {
            std::thread::sleep(Duration::from_millis(10));
        }
        let mut pending = wheel.try_snapshot().unwrap();
        assert!(pending.busy);
        assert!(!pending.running);
        assert!(!pending.has_capture);
        assert!(!pending.can_start_usb);
        assert!(!pending.can_recover_usb);
        let device = WheelDevice {
            id: "software-only".into(),
            name: "Software smoke wheel (no input)".into(),
            product_guid: None,
        };
        pending.devices.push(device.clone());
        // SAFETY: simulated selection verifies the pending-state guard even after local throttling.
        unsafe {
            let panel = state(window.window).unwrap();
            panel.pending_until = None;
            panel.render(pending.clone());
            SendMessageW(panel.control(DEVICE), CB_SETCURSEL, Some(WPARAM(1)), None);
            panel.render(pending);
            for id in [ENABLE, USB, RECOVER_USB] {
                assert!(!enabled(panel, id));
            }
        }
        assert!(wheel.send(WheelCommand::Enable {
            device,
            axis: 0,
            window: owner.0 as isize,
        }));
        let wait = Instant::now();
        while !wheel
            .try_snapshot()
            .is_some_and(|s| s.message.contains("cannot restart"))
            && wait.elapsed() < Duration::from_secs(2)
        {
            std::thread::sleep(Duration::from_millis(10));
        }
        assert!(
            wheel
                .try_snapshot()
                .unwrap()
                .message
                .contains("cannot restart")
        );
        assert!(
            !root.exists(),
            "recovery must not reserve or create a capture"
        );
        assert_eq!(calls.load(Ordering::Relaxed), 1);
        let close = Instant::now();
        drop(window);
        assert!(close.elapsed() < Duration::from_millis(250));
        assert!(wheel.try_snapshot().unwrap().busy);
        {
            let (lock, cv) = &*gate;
            *lock.lock().unwrap() = true;
            cv.notify_all();
        }
        // SAFETY: isolated test owner teardown; worker shutdown is outside the GUI handler.
        unsafe {
            let _ = DestroyWindow(owner);
        }
        assert!(wheel.shutdown_wait(Duration::from_secs(2)));
        assert!(!root.exists());
    }

    struct FakeWheel(Arc<std::sync::atomic::AtomicUsize>);
    impl WheelInputSource for FakeWheel {
        fn enumerate(&mut self) -> Result<Vec<WheelDevice>, String> {
            Ok(vec![WheelDevice {
                id: "software-only".into(),
                name: "Software smoke wheel".into(),
                product_guid: None,
            }])
        }
        fn select(&mut self, _: &str, _: u8, _: isize) -> Result<(), String> {
            Ok(())
        }
        fn read(&mut self) -> WheelReading {
            self.0.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
            WheelReading {
                availability: "Available".into(),
                axis: Some(12_000),
                native_error: None,
            }
        }
    }

    #[test]
    fn fake_capture_continues_after_panel_close_and_ui_destroy() {
        use std::sync::atomic::{AtomicUsize, Ordering};
        let reads = Arc::new(AtomicUsize::new(0));
        let reader_reads = reads.clone();
        let wheel = handle(Arc::new(move || Box::new(FakeWheel(reader_reads.clone()))));
        let owner = owner();
        let mut window = WheelPanel::default();
        window.show(owner, &wheel).unwrap();
        assert!(wheel.send(WheelCommand::Refresh));
        let wait = Instant::now();
        while wheel.try_snapshot().is_none_or(|s| s.devices.is_empty())
            && wait.elapsed() < Duration::from_secs(2)
        {
            std::thread::sleep(Duration::from_millis(10));
        }
        // SAFETY: real dialog commands route only to our injected fake input source and NoUsb.
        unsafe {
            let panel = state(window.window).unwrap();
            panel.refresh();
            SendMessageW(panel.control(DEVICE), CB_SETCURSEL, Some(WPARAM(1)), None);
            panel.command(ENABLE);
        }
        let wait = Instant::now();
        while reads.load(Ordering::Relaxed) == 0 && wait.elapsed() < Duration::from_secs(2) {
            std::thread::sleep(Duration::from_millis(10));
        }
        assert!(wheel.try_snapshot().unwrap().running);
        // SAFETY: close hides only the isolated UI; no device acquisition/ETW occurs in this test.
        unsafe {
            state(window.window).unwrap().hide();
        }
        let before = reads.load(Ordering::Relaxed);
        std::thread::sleep(Duration::from_millis(250));
        assert!(
            reads.load(Ordering::Relaxed) > before,
            "hidden panel must not stop capture"
        );
        drop(window);
        // SAFETY: only the isolated software owner is destroyed; fake worker continues independently.
        unsafe {
            let _ = DestroyWindow(owner);
        }
        let before = reads.load(Ordering::Relaxed);
        std::thread::sleep(Duration::from_millis(250));
        assert!(
            reads.load(Ordering::Relaxed) > before,
            "UI teardown must not join or stop reader"
        );
        assert!(
            wheel.shutdown_wait(Duration::from_secs(2)),
            "process lifecycle stops fake reader after the UI loop"
        );
    }

    unsafe fn save_bitmap(window: HWND, path: &Path) {
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

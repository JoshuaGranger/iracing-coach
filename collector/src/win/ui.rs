//! The overlay window, the tray icon and menu, the global hotkey and the server-address prompt,
//! all on the main thread's message loop. The overlay is a layered, topmost, draggable,
//! no-activate tool window (proved in P3). It checks for new figures 10 times a second and redraws
//! only the tiles whose text, accent or bar position changed, into a back buffer it keeps, then
//! copies just the changed area to the window (a moving car therefore costs a few small redraws
//! a second per changing tile, not a full 540 x 360 repaint with a new bitmap each time).

use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicIsize, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Instant;

use windows::Win32::Foundation::{COLORREF, HINSTANCE, HWND, LPARAM, LRESULT, POINT, RECT, WPARAM};
use windows::Win32::Graphics::Gdi::{
    BeginPaint, BitBlt, CreateBitmap, CreateCompatibleBitmap, CreateCompatibleDC, DeleteDC,
    DeleteObject, EndPaint, GetMonitorInfoW, HGDIOBJ, InvalidateRect, MONITOR_DEFAULTTONEAREST,
    MONITORINFO, MonitorFromPoint, PAINTSTRUCT, SRCCOPY, SelectObject,
};
use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::UI::Input::KeyboardAndMouse::{
    HOT_KEY_MODIFIERS, MOD_NOREPEAT, RegisterHotKey, UnregisterHotKey,
};
use windows::Win32::UI::Shell::{
    NIF_ICON, NIF_MESSAGE, NIF_TIP, NIM_ADD, NIM_DELETE, NIM_MODIFY, NOTIFYICONDATAW,
    Shell_NotifyIconW, ShellExecuteW,
};
use windows::Win32::UI::WindowsAndMessaging::{
    AppendMenuW, CreateIconIndirect, CreatePopupMenu, CreateWindowExW, DefWindowProcW, DestroyIcon,
    DestroyMenu, DestroyWindow, DispatchMessageW, GWLP_USERDATA, GetCursorPos, GetMessageW,
    GetWindowLongPtrW, GetWindowRect, GetWindowTextW, HICON, HMENU, HTCAPTION, HWND_TOPMOST,
    ICONINFO, IDC_ARROW, IsDialogMessageW, KillTimer, LWA_ALPHA, LoadCursorW, MENU_ITEM_FLAGS,
    MF_CHECKED, MF_DISABLED, MF_GRAYED, MF_POPUP, MF_SEPARATOR, MF_STRING, MSG, PostMessageW,
    PostQuitMessage, RegisterClassExW, SM_CXSCREEN, SPI_GETWORKAREA, SW_HIDE, SW_SHOWNOACTIVATE,
    SW_SHOWNORMAL, SWP_NOACTIVATE, SWP_NOMOVE, SWP_NOSIZE, SetForegroundWindow,
    SetLayeredWindowAttributes, SetTimer, SetWindowLongPtrW, SetWindowPos, ShowWindow,
    SystemParametersInfoW, TPM_BOTTOMALIGN, TPM_LEFTALIGN, TPM_RIGHTBUTTON, TrackPopupMenu,
    TranslateMessage, WINDOW_EX_STYLE, WINDOW_STYLE, WM_APP, WM_COMMAND, WM_DESTROY, WM_ERASEBKGND,
    WM_EXITSIZEMOVE, WM_HOTKEY, WM_LBUTTONUP, WM_MOUSEACTIVATE, WM_NCHITTEST, WM_NULL, WM_PAINT,
    WM_RBUTTONUP, WM_TIMER, WNDCLASSEXW, WS_BORDER, WS_CAPTION, WS_CHILD, WS_EX_DLGMODALFRAME,
    WS_EX_LAYERED, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW, WS_EX_TOPMOST, WS_POPUP, WS_SYSMENU,
    WS_TABSTOP, WS_VISIBLE,
};
use windows::core::PCWSTR;

use super::render::{Dirty, Frame, Renderer, window_size};
use super::wheel_panel::WheelPanel;
use super::{QuitEvent, wide};
use crate::config::{Config, normalize_server};
use crate::hotkey::parse_hotkey;
use crate::hub::Hub;
use crate::layout::Layout;
use crate::wheel::{WheelCommand, WheelHandle};

const TRAY_MSG: u32 = WM_APP + 1;
const TIMER_ID: usize = 1;
/// The controller's timer: the overlay checks the live state this often, so it repaints at most
/// 10 times a second.
const TIMER_MS: u32 = 100;
/// With no new live state, the tiles are still compared this often (text that depends on time).
const IDLE_CHECK_MS: u128 = 1_000;
/// The tray tooltip and icon are refreshed this often.
const TRAY_REFRESH_MS: u128 = 500;
const HOTKEY_ID: i32 = 0x4937;

const ID_STATUS: usize = 100;
const ID_SERVER_STATUS: usize = 101;
const ID_SHOW: usize = 110;
const ID_SCALE_BASE: usize = 120;
const ID_PAUSE: usize = 130;
const ID_OPEN: usize = 140;
const ID_SERVER: usize = 150;
const ID_WHEEL_RECORDER: usize = 160;
const ID_WHEEL_MARK: usize = 161;
const ID_EXIT: usize = 199;

const SCALES: [(&str, f64); 3] = [("Compact", 0.8), ("Standard", 1.0), ("Expanded", 1.25)];

pub struct UiParams {
    pub hub: Arc<Hub>,
    pub config: Config,
    pub state_dir: PathBuf,
    pub quit: Option<QuitEvent>,
    pub wheel: WheelHandle,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum UiExit {
    Normal,
    /// The server address changed: the caller starts a fresh process.
    Restart,
}

struct App {
    hub: Arc<Hub>,
    config: Config,
    state_dir: PathBuf,
    quit: Option<QuitEvent>,
    wheel: WheelHandle,
    wheel_panel: WheelPanel,
    overlay: HWND,
    controller: HWND,
    renderer: Renderer,
    layout: Layout,
    layout_gen: u64,
    last_live_gen: u64,
    last_check: Instant,
    last_tray: Instant,
    last_topmost: Instant,
    /// What the next paint has to redraw: everything, or these tiles.
    repaint: Repaint,
    /// The surface the tiles are drawn on between paints.
    back: Option<BackBuffer>,
    visible: bool,
    icon_live: HICON,
    icon_idle: HICON,
    tray_live: Option<bool>,
    tray_tip: String,
    restart: bool,
}

/// What the next `WM_PAINT` has to draw into the back buffer.
enum Repaint {
    All,
    Tiles(Vec<usize>),
}

/// A memory device context and bitmap the size of the overlay, kept between paints.
struct BackBuffer {
    dc: windows::Win32::Graphics::Gdi::HDC,
    bitmap: windows::Win32::Graphics::Gdi::HBITMAP,
    old: HGDIOBJ,
    width: i32,
    height: i32,
}

impl BackBuffer {
    /// # Safety
    /// `window_dc` must be a valid device context.
    unsafe fn create(
        window_dc: windows::Win32::Graphics::Gdi::HDC,
        width: i32,
        height: i32,
    ) -> BackBuffer {
        // SAFETY: a memory DC and a compatible bitmap, freed in `destroy`.
        unsafe {
            let dc = CreateCompatibleDC(Some(window_dc));
            let bitmap = CreateCompatibleBitmap(window_dc, width, height);
            let old = SelectObject(dc, HGDIOBJ(bitmap.0));
            BackBuffer {
                dc,
                bitmap,
                old,
                width,
                height,
            }
        }
    }

    fn destroy(self) {
        // SAFETY: the objects were created in `create` and are released once.
        unsafe {
            SelectObject(self.dc, self.old);
            let _ = DeleteObject(HGDIOBJ(self.bitmap.0));
            let _ = DeleteDC(self.dc);
        }
    }
}

pub fn run(params: UiParams) -> UiExit {
    // SAFETY: standard window class registration and message loop on the calling thread; the
    // `App` box is owned by this function and freed after the loop ends.
    unsafe {
        let instance: HINSTANCE = GetModuleHandleW(PCWSTR::null())
            .map(|h| h.into())
            .unwrap_or_default();
        register_class(instance, "CoachOverlayWindow", Some(overlay_proc), true);
        register_class(
            instance,
            "CoachControllerWindow",
            Some(controller_proc),
            false,
        );

        let layout = initial_layout(&params.hub, &params.config);
        let scale = params.config.placement.scale;
        let (w, h) = window_size(scale);
        let (x, y) = initial_position(&params.config, w, h);
        let overlay = CreateWindowExW(
            WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW,
            PCWSTR(wide("CoachOverlayWindow").as_ptr()),
            PCWSTR(wide("iRacing Coach monitor").as_ptr()),
            WS_POPUP,
            x,
            y,
            w,
            h,
            None,
            None,
            Some(instance),
            None,
        )
        .expect("create overlay window");
        let _ = SetLayeredWindowAttributes(overlay, COLORREF(0), 236, LWA_ALPHA);
        let controller = CreateWindowExW(
            WINDOW_EX_STYLE(0),
            PCWSTR(wide("CoachControllerWindow").as_ptr()),
            PCWSTR(wide("iRacing Coach").as_ptr()),
            WINDOW_STYLE(0),
            0,
            0,
            0,
            0,
            None,
            None,
            Some(instance),
            None,
        )
        .expect("create controller window");

        let app = Box::new(App {
            hub: params.hub,
            config: params.config,
            state_dir: params.state_dir,
            quit: params.quit,
            wheel: params.wheel,
            wheel_panel: WheelPanel::default(),
            overlay,
            controller,
            renderer: Renderer::new(),
            layout,
            layout_gen: 0,
            last_live_gen: u64::MAX,
            last_check: Instant::now(),
            last_tray: Instant::now(),
            last_topmost: Instant::now(),
            repaint: Repaint::All,
            back: None,
            visible: false,
            icon_live: make_icon(0x57EEA0),
            icon_idle: make_icon(0x8E98A5),
            tray_live: None,
            tray_tip: String::new(),
            restart: false,
        });
        let app_ptr = Box::into_raw(app);
        SetWindowLongPtrW(overlay, GWLP_USERDATA, app_ptr as isize);
        SetWindowLongPtrW(controller, GWLP_USERDATA, app_ptr as isize);

        {
            let app = &mut *app_ptr;
            app.register_hotkey();
            app.update_tray(true);
        }
        SetTimer(Some(controller), TIMER_ID, TIMER_MS, None);

        let mut msg = MSG::default();
        while GetMessageW(&mut msg, None, 0, 0).as_bool() {
            if (&*app_ptr).wheel_panel.translate(&msg) {
                continue;
            }
            let _ = TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }

        let mut app = Box::from_raw(app_ptr);
        app.shutdown();
        if app.restart {
            UiExit::Restart
        } else {
            UiExit::Normal
        }
    }
}

unsafe fn register_class(
    instance: HINSTANCE,
    name: &str,
    proc: Option<unsafe extern "system" fn(HWND, u32, WPARAM, LPARAM) -> LRESULT>,
    cursor: bool,
) {
    let class_name = wide(name);
    let class = WNDCLASSEXW {
        cbSize: std::mem::size_of::<WNDCLASSEXW>() as u32,
        lpfnWndProc: proc,
        hInstance: instance,
        // SAFETY: loading a stock cursor.
        hCursor: if cursor {
            unsafe { LoadCursorW(None, IDC_ARROW).unwrap_or_default() }
        } else {
            Default::default()
        },
        lpszClassName: PCWSTR(class_name.as_ptr()),
        ..WNDCLASSEXW::default()
    };
    // SAFETY: the class name buffer lives until the call returns (the system copies it).
    unsafe {
        RegisterClassExW(&class);
    }
}

fn initial_layout(hub: &Hub, config: &Config) -> Layout {
    hub.layout()
        .and_then(|l| Layout::from_json(&l.layout))
        .or_else(|| {
            config
                .layout
                .as_ref()
                .and_then(|l| Layout::from_json(&l.layout))
        })
        .unwrap_or_else(Layout::default_layout)
}

/// The work area containing `(x, y)`.
unsafe fn work_area_at(x: i32, y: i32) -> RECT {
    // SAFETY: plain queries into out parameters.
    unsafe {
        let monitor = MonitorFromPoint(POINT { x, y }, MONITOR_DEFAULTTONEAREST);
        let mut info = MONITORINFO {
            cbSize: std::mem::size_of::<MONITORINFO>() as u32,
            ..MONITORINFO::default()
        };
        if GetMonitorInfoW(monitor, &mut info).as_bool() {
            return info.rcWork;
        }
        let mut rect = RECT::default();
        let _ = SystemParametersInfoW(
            SPI_GETWORKAREA,
            0,
            Some((&mut rect as *mut RECT).cast()),
            Default::default(),
        );
        if rect.right == 0 {
            rect.right = windows::Win32::UI::WindowsAndMessaging::GetSystemMetrics(SM_CXSCREEN);
            rect.bottom = 1080;
        }
        rect
    }
}

/// The saved position clamped into the nearest work area, else 24 px inside the top-right corner
/// of the primary monitor.
fn initial_position(config: &Config, w: i32, h: i32) -> (i32, i32) {
    // SAFETY: monitor queries only.
    unsafe {
        match (config.placement.left, config.placement.top) {
            (Some(l), Some(t)) => {
                let area = work_area_at(l, t);
                (
                    l.clamp(area.left, (area.right - w).max(area.left)),
                    t.clamp(area.top, (area.bottom - h).max(area.top)),
                )
            }
            _ => {
                let area = work_area_at(0, 0);
                ((area.right - w - 24).max(area.left), area.top + 24)
            }
        }
    }
}

/// A 32x32 ring icon in `rgb`.
fn make_icon(rgb: u32) -> HICON {
    let (r, g, b) = (
        ((rgb >> 16) & 0xFF) as u8,
        ((rgb >> 8) & 0xFF) as u8,
        (rgb & 0xFF) as u8,
    );
    let mut pixels = vec![0u8; 32 * 32 * 4];
    for y in 0..32i32 {
        for x in 0..32i32 {
            let d = (f64::from((x - 16) * (x - 16) + (y - 16) * (y - 16))).sqrt();
            let alpha = if (8.0..13.0).contains(&d) || d < 4.0 {
                255
            } else {
                0
            };
            let i = ((y * 32 + x) * 4) as usize;
            pixels[i..i + 4].copy_from_slice(&[b, g, r, alpha]);
        }
    }
    let mask = [0u8; 32 * 4];
    // SAFETY: the bitmaps copy the buffers; they are deleted after the icon is created.
    unsafe {
        let color = CreateBitmap(32, 32, 1, 32, Some(pixels.as_ptr().cast()));
        let mono = CreateBitmap(32, 32, 1, 1, Some(mask.as_ptr().cast()));
        let info = ICONINFO {
            fIcon: true.into(),
            xHotspot: 0,
            yHotspot: 0,
            hbmMask: mono,
            hbmColor: color,
        };
        let icon = CreateIconIndirect(&info).unwrap_or_default();
        let _ = DeleteObject(HGDIOBJ(color.0));
        let _ = DeleteObject(HGDIOBJ(mono.0));
        icon
    }
}

impl App {
    fn register_hotkey(&mut self) {
        if let Some((mods, vk)) = parse_hotkey(&self.config.hotkey) {
            // SAFETY: registers against our own controller window.
            let ok = unsafe {
                RegisterHotKey(
                    Some(self.controller),
                    HOTKEY_ID,
                    HOT_KEY_MODIFIERS(mods) | MOD_NOREPEAT,
                    vk,
                )
                .is_ok()
            };
            if !ok {
                crate::warn!("hotkey {:?} could not be registered", self.config.hotkey);
            }
        }
    }

    fn save_config(&self) {
        if let Err(e) = self.config.save(&self.state_dir) {
            crate::warn!("saving collector.json failed: {e}");
        }
    }

    fn shutdown(&mut self) {
        // SAFETY: tears down what `run` created.
        unsafe {
            let _ = UnregisterHotKey(Some(self.controller), HOTKEY_ID);
            self.tray(NIM_DELETE, false, "");
            let _ = KillTimer(Some(self.controller), TIMER_ID);
            let _ = DestroyIcon(self.icon_live);
            let _ = DestroyIcon(self.icon_idle);
            let _ = DestroyWindow(self.overlay);
        }
        if let Some(back) = self.back.take() {
            back.destroy();
        }
        self.hub.request_shutdown();
    }

    // ---- tray ----

    fn tray(&self, message: windows::Win32::UI::Shell::NOTIFY_ICON_MESSAGE, live: bool, tip: &str) {
        let mut data = NOTIFYICONDATAW {
            cbSize: std::mem::size_of::<NOTIFYICONDATAW>() as u32,
            hWnd: self.controller,
            uID: 1,
            uFlags: NIF_MESSAGE | NIF_ICON | NIF_TIP,
            uCallbackMessage: TRAY_MSG,
            hIcon: if live { self.icon_live } else { self.icon_idle },
            ..NOTIFYICONDATAW::default()
        };
        for (slot, unit) in data.szTip.iter_mut().zip(tip.encode_utf16().take(127)) {
            *slot = unit;
        }
        // SAFETY: `data` is fully initialised for the call.
        unsafe {
            let _ = Shell_NotifyIconW(message, &data);
        }
    }

    fn status_lines(&self) -> (String, String) {
        let live = self.hub.live();
        let sim = match live {
            Some(s) if s.connected => match s.lap {
                Some(lap) => format!("iRacing connected · lap {lap}"),
                None => "iRacing connected".to_string(),
            },
            _ => "Waiting for iRacing".to_string(),
        };
        let link = self.hub.link_info();
        let pending = self.hub.uploads.uploads_pending.load(Ordering::Relaxed);
        let server = if link.update_needed {
            "Collector update needed".to_string()
        } else if link.connected {
            format!(
                "Server connected · {} viewer{}",
                link.viewers,
                if link.viewers == 1 { "" } else { "s" }
            )
        } else if pending > 0 {
            format!(
                "Server unreachable · {pending} upload{} waiting",
                if pending == 1 { "" } else { "s" }
            )
        } else {
            "Server unreachable".to_string()
        };
        // Recordings the collector gave up on (unreadable or refused for good) are not pending
        // and would otherwise go unmentioned; the log and journal carry the reasons.
        let rejected = self.hub.uploads.rejected.load(Ordering::Relaxed);
        let server = if rejected > 0 {
            format!(
                "{server} · {rejected} recording{} not uploaded",
                if rejected == 1 { "" } else { "s" }
            )
        } else {
            server
        };
        (sim, server)
    }

    fn update_tray(&mut self, force: bool) {
        let (sim, server) = self.status_lines();
        let tip = format!("iRacing Coach · {sim} · {server}");
        let live = self.hub.live().is_some_and(|s| s.connected);
        if force || self.tray_live != Some(live) || self.tray_tip != tip {
            let message = if self.tray_live.is_none() {
                NIM_ADD
            } else {
                NIM_MODIFY
            };
            self.tray(message, live, &tip);
            self.tray_live = Some(live);
            self.tray_tip = tip;
        }
    }

    fn show_menu(&mut self) {
        // SAFETY: builds and shows a popup menu owned by this call.
        unsafe {
            let menu = CreatePopupMenu().expect("popup menu");
            let (sim, server) = self.status_lines();
            let _ = AppendMenuW(
                menu,
                MF_STRING | MF_DISABLED | MF_GRAYED,
                ID_STATUS,
                PCWSTR(wide(&sim).as_ptr()),
            );
            let _ = AppendMenuW(
                menu,
                MF_STRING | MF_DISABLED | MF_GRAYED,
                ID_SERVER_STATUS,
                PCWSTR(wide(&server).as_ptr()),
            );
            let _ = AppendMenuW(menu, MF_SEPARATOR, 0, PCWSTR::null());
            let check = |on: bool| if on { MF_CHECKED } else { MENU_ITEM_FLAGS(0) };
            let _ = AppendMenuW(
                menu,
                MF_STRING | check(self.config.monitor_visible),
                ID_SHOW,
                PCWSTR(wide("Show monitor").as_ptr()),
            );
            let sizes = CreatePopupMenu().expect("size menu");
            for (i, (name, scale)) in SCALES.iter().enumerate() {
                let on = (self.config.placement.scale - scale).abs() < 0.01;
                let _ = AppendMenuW(
                    sizes,
                    MF_STRING | check(on),
                    ID_SCALE_BASE + i,
                    PCWSTR(wide(name).as_ptr()),
                );
            }
            let _ = AppendMenuW(
                menu,
                MF_STRING | MF_POPUP,
                sizes.0 as usize,
                PCWSTR(wide("Monitor size").as_ptr()),
            );
            let paused = self.hub.paused.load(Ordering::Relaxed);
            let _ = AppendMenuW(
                menu,
                MF_STRING | check(paused),
                ID_PAUSE,
                PCWSTR(wide("Pause coaching cue").as_ptr()),
            );
            let _ = AppendMenuW(menu, MF_SEPARATOR, 0, PCWSTR::null());
            let _ = AppendMenuW(
                menu,
                MF_STRING,
                ID_OPEN,
                PCWSTR(wide("Open Coach").as_ptr()),
            );
            let _ = AppendMenuW(
                menu,
                MF_STRING,
                ID_SERVER,
                PCWSTR(wide("Set server address...").as_ptr()),
            );
            let troubleshooting = CreatePopupMenu().expect("troubleshooting menu");
            let _ = AppendMenuW(
                troubleshooting,
                MF_STRING,
                ID_WHEEL_RECORDER,
                PCWSTR(wide("Wheel interruption recorder...").as_ptr()),
            );
            let _ = AppendMenuW(
                menu,
                MF_STRING | MF_POPUP,
                troubleshooting.0 as usize,
                PCWSTR(wide("Troubleshooting").as_ptr()),
            );
            let can_mark = self
                .wheel
                .try_snapshot()
                .is_some_and(|s| s.running && !s.busy);
            let _ = AppendMenuW(
                menu,
                MF_STRING
                    | if can_mark {
                        MENU_ITEM_FLAGS(0)
                    } else {
                        MF_DISABLED | MF_GRAYED
                    },
                ID_WHEEL_MARK,
                PCWSTR(wide("Mark wheel incident").as_ptr()),
            );
            let _ = AppendMenuW(menu, MF_SEPARATOR, 0, PCWSTR::null());
            let _ = AppendMenuW(menu, MF_STRING, ID_EXIT, PCWSTR(wide("Exit").as_ptr()));

            let mut pt = POINT::default();
            let _ = GetCursorPos(&mut pt);
            let _ = SetForegroundWindow(self.controller);
            let _ = TrackPopupMenu(
                menu,
                TPM_LEFTALIGN | TPM_BOTTOMALIGN | TPM_RIGHTBUTTON,
                pt.x,
                pt.y,
                None,
                self.controller,
                None,
            );
            let _ = PostMessageW(Some(self.controller), WM_NULL, WPARAM(0), LPARAM(0));
            let _ = DestroyMenu(menu);
        }
    }

    fn on_command(&mut self, id: usize) {
        match id {
            ID_SHOW => {
                self.config.monitor_visible = !self.config.monitor_visible;
                self.save_config();
                let connected = self.hub.live().is_some_and(|s| s.connected);
                self.apply_visibility(connected);
            }
            i if (ID_SCALE_BASE..ID_SCALE_BASE + SCALES.len()).contains(&i) => {
                self.config.placement.scale = SCALES[i - ID_SCALE_BASE].1;
                self.resize_overlay();
                self.save_config();
            }
            ID_PAUSE => {
                let now = !self.hub.paused.load(Ordering::Relaxed);
                self.hub.paused.store(now, Ordering::Relaxed);
            }
            ID_OPEN => {
                let url = wide(&self.config.server);
                // SAFETY: opens the default browser on the server address.
                unsafe {
                    ShellExecuteW(
                        None,
                        PCWSTR(wide("open").as_ptr()),
                        PCWSTR(url.as_ptr()),
                        PCWSTR::null(),
                        PCWSTR::null(),
                        SW_SHOWNORMAL,
                    );
                }
            }
            ID_SERVER => self.change_server(),
            ID_WHEEL_RECORDER => {
                if let Err(error) = self.wheel_panel.show(self.controller, &self.wheel) {
                    crate::warn!("wheel recorder window unavailable: {error}");
                }
            }
            ID_WHEEL_MARK => {
                if self
                    .wheel
                    .try_snapshot()
                    .is_some_and(|s| s.running && !s.busy)
                {
                    let _ = self.wheel.send(WheelCommand::MarkIncident);
                }
            }
            ID_EXIT => {
                // SAFETY: asks our own window to close.
                unsafe {
                    let _ = DestroyWindow(self.controller);
                }
            }
            _ => {}
        }
    }

    fn change_server(&mut self) {
        // SAFETY: runs a modal prompt on this thread.
        let answer = unsafe { prompt_server(self.controller, &self.config.server) };
        let Some(text) = answer else {
            return;
        };
        match normalize_server(&text) {
            Ok(server) if server != self.config.server => {
                self.config.server = server;
                self.save_config();
                self.restart = true;
                // SAFETY: closing our own window ends the loop; the caller restarts the process.
                unsafe {
                    let _ = DestroyWindow(self.controller);
                }
            }
            Ok(_) => {}
            Err(e) => {
                // SAFETY: a plain message box.
                unsafe {
                    windows::Win32::UI::WindowsAndMessaging::MessageBoxW(
                        Some(self.controller),
                        PCWSTR(wide(&e.to_string()).as_ptr()),
                        PCWSTR(wide("Server address").as_ptr()),
                        windows::Win32::UI::WindowsAndMessaging::MB_OK,
                    );
                }
            }
        }
    }

    // ---- overlay ----

    fn resize_overlay(&mut self) {
        let (w, h) = window_size(self.config.placement.scale);
        // SAFETY: repositions our own window without activating it.
        unsafe {
            let mut rect = RECT::default();
            let _ = GetWindowRect(self.overlay, &mut rect);
            let _ = SetWindowPos(
                self.overlay,
                Some(HWND_TOPMOST),
                rect.left,
                rect.top,
                w,
                h,
                SWP_NOACTIVATE,
            );
        }
        self.invalidate();
    }

    /// Repaints the whole overlay at the next paint.
    fn invalidate(&mut self) {
        self.repaint = Repaint::All;
        // SAFETY: invalidates our own window.
        unsafe {
            let _ = InvalidateRect(Some(self.overlay), None, false);
        }
        self.last_check = Instant::now();
    }

    /// Repaints only `tiles` at the next paint (unless a full repaint is already due).
    fn invalidate_tiles(&mut self, tiles: &[usize], width: i32, height: i32) {
        let rects =
            super::render::tile_rects(&self.layout, width, height, self.config.placement.scale);
        if let Repaint::Tiles(pending) = &mut self.repaint {
            for &i in tiles {
                if !pending.contains(&i) {
                    pending.push(i);
                }
            }
        }
        for &i in tiles {
            if let Some(r) = rects.get(i) {
                let area = RECT {
                    left: r.x,
                    top: r.y,
                    right: r.x + r.w,
                    bottom: r.y + r.h,
                };
                // SAFETY: invalidates a rectangle of our own window.
                unsafe {
                    let _ = InvalidateRect(Some(self.overlay), Some(&area), false);
                }
            }
        }
    }

    fn apply_visibility(&mut self, connected: bool) {
        let want = self.config.monitor_visible && connected;
        if want != self.visible {
            self.visible = want;
            // SAFETY: shows or hides our own window without activating it.
            unsafe {
                let _ = ShowWindow(self.overlay, if want { SW_SHOWNOACTIVATE } else { SW_HIDE });
            }
            if want {
                self.invalidate();
            }
        }
    }

    fn save_placement(&mut self) {
        // SAFETY: reads our own window rectangle.
        let rect = unsafe {
            let mut rect = RECT::default();
            let _ = GetWindowRect(self.overlay, &mut rect);
            rect
        };
        self.config.placement.left = Some(rect.left);
        self.config.placement.top = Some(rect.top);
        self.save_config();
    }

    fn on_timer(&mut self) {
        if self.hub.is_shutdown() || self.quit.as_ref().is_some_and(QuitEvent::is_set) {
            // SAFETY: asks our own window to close.
            unsafe {
                let _ = DestroyWindow(self.controller);
            }
            return;
        }
        let layout_gen = self.hub.layout_gen();
        if layout_gen != self.layout_gen {
            self.layout_gen = layout_gen;
            if let Some(state) = self.hub.layout()
                && let Some(layout) = Layout::from_json(&state.layout)
            {
                self.layout = layout;
                self.config.layout = Some(crate::config::CachedLayout {
                    layout_id: state.layout_id,
                    version: state.version,
                    layout: state.layout,
                });
                self.save_config();
            }
            self.invalidate();
        }
        let live = self.hub.live();
        self.apply_visibility(live.is_some_and(|s| s.connected));
        if self.last_tray.elapsed().as_millis() >= TRAY_REFRESH_MS {
            self.last_tray = Instant::now();
            self.update_tray(false);
        }
        if !self.visible {
            return;
        }
        if self.last_topmost.elapsed().as_secs() >= 2 {
            self.last_topmost = Instant::now();
            // SAFETY: re-asserts topmost without moving, sizing or activating.
            unsafe {
                let _ = SetWindowPos(
                    self.overlay,
                    Some(HWND_TOPMOST),
                    0,
                    0,
                    0,
                    0,
                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
                );
            }
        }
        let live_gen = self.hub.live_gen();
        if live_gen == self.last_live_gen && self.last_check.elapsed().as_millis() < IDLE_CHECK_MS {
            return;
        }
        self.last_live_gen = live_gen;
        self.last_check = Instant::now();
        let (w, h) = window_size(self.config.placement.scale);
        let frame = Frame {
            width: w,
            height: h,
            scale: self.config.placement.scale,
            layout: &self.layout,
            state: live.as_ref(),
            now_ms: self.hub.clock.mono_ms(),
        };
        match self.renderer.dirty(&frame) {
            Dirty::Nothing => {}
            Dirty::All => self.invalidate(),
            Dirty::Tiles(tiles) => self.invalidate_tiles(&tiles, w, h),
        }
    }

    fn paint(&mut self) {
        // SAFETY: standard paint cycle on our own window; the back buffer is owned by `self`.
        unsafe {
            let mut ps = PAINTSTRUCT::default();
            let hdc = BeginPaint(self.overlay, &mut ps);
            let (w, h) = window_size(self.config.placement.scale);
            if self
                .back
                .as_ref()
                .is_some_and(|b| b.width != w || b.height != h)
                && let Some(old) = self.back.take()
            {
                old.destroy();
            }
            if self.back.is_none() {
                self.back = Some(BackBuffer::create(hdc, w, h));
                self.repaint = Repaint::All;
            }
            if let Some(back) = &self.back {
                let state = self.hub.live();
                let frame = Frame {
                    width: w,
                    height: h,
                    scale: self.config.placement.scale,
                    layout: &self.layout,
                    state: state.as_ref(),
                    now_ms: self.hub.clock.mono_ms(),
                };
                match std::mem::replace(&mut self.repaint, Repaint::Tiles(Vec::new())) {
                    Repaint::All => self.renderer.paint(back.dc, &frame),
                    Repaint::Tiles(tiles) if !tiles.is_empty() => {
                        self.renderer.paint_tiles(back.dc, &frame, &tiles)
                    }
                    Repaint::Tiles(_) => {}
                }
                // Only the invalidated area is copied: that is what changed on screen.
                let area = ps.rcPaint;
                let (cw, ch) = (area.right - area.left, area.bottom - area.top);
                if cw > 0 && ch > 0 {
                    let _ = BitBlt(
                        hdc,
                        area.left,
                        area.top,
                        cw,
                        ch,
                        Some(back.dc),
                        area.left,
                        area.top,
                        SRCCOPY,
                    );
                }
            }
            let _ = EndPaint(self.overlay, &ps);
        }
    }
}

unsafe fn app_of(hwnd: HWND) -> Option<&'static mut App> {
    // SAFETY: the pointer is set once by `run` and valid until the loop ends.
    unsafe {
        let ptr = GetWindowLongPtrW(hwnd, GWLP_USERDATA) as *mut App;
        ptr.as_mut()
    }
}

unsafe extern "system" fn overlay_proc(
    hwnd: HWND,
    msg: u32,
    wparam: WPARAM,
    lparam: LPARAM,
) -> LRESULT {
    // SAFETY: window procedure for our own class on the UI thread.
    unsafe {
        match msg {
            WM_PAINT => {
                if let Some(app) = app_of(hwnd) {
                    app.paint();
                    return LRESULT(0);
                }
            }
            WM_ERASEBKGND => return LRESULT(1),
            WM_MOUSEACTIVATE => return LRESULT(3), // MA_NOACTIVATE
            WM_NCHITTEST => {
                return LRESULT(HTCAPTION as isize);
            }
            WM_EXITSIZEMOVE => {
                if let Some(app) = app_of(hwnd) {
                    app.save_placement();
                }
            }
            _ => {}
        }
        DefWindowProcW(hwnd, msg, wparam, lparam)
    }
}

unsafe extern "system" fn controller_proc(
    hwnd: HWND,
    msg: u32,
    wparam: WPARAM,
    lparam: LPARAM,
) -> LRESULT {
    // SAFETY: window procedure for our own class on the UI thread.
    unsafe {
        match msg {
            WM_TIMER => {
                if let Some(app) = app_of(hwnd) {
                    app.on_timer();
                }
                return LRESULT(0);
            }
            TRAY_MSG => {
                let event = lparam.0 as u32 & 0xFFFF;
                if (event == WM_RBUTTONUP || event == WM_LBUTTONUP)
                    && let Some(app) = app_of(hwnd)
                {
                    app.show_menu();
                }
                return LRESULT(0);
            }
            WM_COMMAND => {
                if let Some(app) = app_of(hwnd) {
                    app.on_command(wparam.0 & 0xFFFF);
                }
                return LRESULT(0);
            }
            WM_HOTKEY => {
                if let Some(app) = app_of(hwnd) {
                    app.on_command(ID_SHOW);
                }
                return LRESULT(0);
            }
            WM_DESTROY => {
                PostQuitMessage(0);
                return LRESULT(0);
            }
            _ => {}
        }
        DefWindowProcW(hwnd, msg, wparam, lparam)
    }
}

// ---- server address prompt ----

static DIALOG_DONE: AtomicBool = AtomicBool::new(false);
static DIALOG_RESULT: Mutex<Option<String>> = Mutex::new(None);
static DIALOG_EDIT: AtomicIsize = AtomicIsize::new(0);

const ID_DLG_OK: usize = 1;
const ID_DLG_CANCEL: usize = 2;

/// A small modal prompt for the server address; None when cancelled.
///
/// # Safety
/// Must run on the UI thread.
unsafe fn prompt_server(owner: HWND, current: &str) -> Option<String> {
    // SAFETY: creates, runs and destroys a dialog window and its children on this thread.
    unsafe {
        let instance: HINSTANCE = GetModuleHandleW(PCWSTR::null())
            .map(|h| h.into())
            .unwrap_or_default();
        register_class(instance, "CoachServerDialog", Some(dialog_proc), true);
        DIALOG_DONE.store(false, Ordering::SeqCst);
        *DIALOG_RESULT.lock().unwrap_or_else(|e| e.into_inner()) = None;
        let area = work_area_at(0, 0);
        let (w, h) = (400, 132);
        let dlg = CreateWindowExW(
            WS_EX_DLGMODALFRAME | WS_EX_TOPMOST,
            PCWSTR(wide("CoachServerDialog").as_ptr()),
            PCWSTR(wide("Server address").as_ptr()),
            WS_POPUP | WS_CAPTION | WS_SYSMENU | WS_VISIBLE,
            area.left + (area.right - area.left - w) / 2,
            area.top + (area.bottom - area.top - h) / 3,
            w,
            h,
            Some(owner),
            None,
            Some(instance),
            None,
        )
        .ok()?;
        let child = |class: &str,
                     text: &str,
                     style: WINDOW_STYLE,
                     x: i32,
                     y: i32,
                     cw: i32,
                     ch: i32,
                     id: usize| {
            CreateWindowExW(
                WINDOW_EX_STYLE(0),
                PCWSTR(wide(class).as_ptr()),
                PCWSTR(wide(text).as_ptr()),
                WS_CHILD | WS_VISIBLE | style,
                x,
                y,
                cw,
                ch,
                Some(dlg),
                Some(HMENU(id as *mut _)),
                Some(instance),
                None,
            )
            .unwrap_or_default()
        };
        child(
            "STATIC",
            "Collector server (host:port)",
            WINDOW_STYLE(0),
            12,
            10,
            360,
            18,
            0,
        );
        let edit = child(
            "EDIT",
            current,
            WS_BORDER | WS_TABSTOP | WINDOW_STYLE(0x80),
            12,
            32,
            360,
            24,
            0,
        );
        DIALOG_EDIT.store(edit.0 as isize, Ordering::SeqCst);
        child(
            "BUTTON",
            "OK",
            WS_TABSTOP | WINDOW_STYLE(0x1),
            200,
            66,
            80,
            26,
            ID_DLG_OK,
        );
        child(
            "BUTTON",
            "Cancel",
            WS_TABSTOP,
            292,
            66,
            80,
            26,
            ID_DLG_CANCEL,
        );
        let _ = SetForegroundWindow(dlg);

        let mut msg = MSG::default();
        while !DIALOG_DONE.load(Ordering::SeqCst) && GetMessageW(&mut msg, None, 0, 0).as_bool() {
            if !IsDialogMessageW(dlg, &msg).as_bool() {
                let _ = TranslateMessage(&msg);
                DispatchMessageW(&msg);
            }
        }
        DIALOG_RESULT
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .take()
    }
}

unsafe extern "system" fn dialog_proc(
    hwnd: HWND,
    msg: u32,
    wparam: WPARAM,
    lparam: LPARAM,
) -> LRESULT {
    // SAFETY: window procedure for our own dialog class on the UI thread.
    unsafe {
        match msg {
            WM_COMMAND => match wparam.0 & 0xFFFF {
                ID_DLG_OK => {
                    let edit = HWND(DIALOG_EDIT.load(Ordering::SeqCst) as *mut _);
                    let mut buf = [0u16; 256];
                    let n = GetWindowTextW(edit, &mut buf).max(0) as usize;
                    *DIALOG_RESULT.lock().unwrap_or_else(|e| e.into_inner()) =
                        Some(String::from_utf16_lossy(&buf[..n]));
                    let _ = DestroyWindow(hwnd);
                    return LRESULT(0);
                }
                ID_DLG_CANCEL => {
                    let _ = DestroyWindow(hwnd);
                    return LRESULT(0);
                }
                _ => {}
            },
            windows::Win32::UI::WindowsAndMessaging::WM_CLOSE => {
                let _ = DestroyWindow(hwnd);
                return LRESULT(0);
            }
            WM_DESTROY => {
                DIALOG_DONE.store(true, Ordering::SeqCst);
                // Wake the nested loop so it sees the flag.
                let _ = PostMessageW(None, WM_NULL, WPARAM(0), LPARAM(0));
                return LRESULT(0);
            }
            _ => {}
        }
        DefWindowProcW(hwnd, msg, wparam, lparam)
    }
}

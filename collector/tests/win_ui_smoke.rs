//! Starts the real collector binary with its overlay, tray and hotkey code on a replayed fixture
//! (`--replay --dry-run`, no server) and checks the overlay window it creates. It briefly shows a
//! draggable window on the desktop, so it is ignored by default:
//! `cargo test -p coach-collector --test win_ui_smoke -- --ignored`.
#![cfg(windows)]

mod support;

use std::process::Command;
use std::time::{Duration, Instant};

use coach_collector::fixture::{MapBuilder, SessionSpec, Step};
use coach_collector::win::wide;
use support::*;
use windows::Win32::Foundation::{HWND, LPARAM, RECT};
use windows::Win32::UI::WindowsAndMessaging::{
    FindWindowExW, GWL_EXSTYLE, GetWindowLongPtrW, GetWindowRect, GetWindowThreadProcessId,
    HTCAPTION, HTCLIENT, IsWindowVisible, SendMessageW, WM_APP, WM_CLOSE, WM_LBUTTONUP,
    WM_MOUSEACTIVATE, WM_NCHITTEST, WS_EX_LAYERED, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW,
    WS_EX_TOPMOST, WS_EX_TRANSPARENT,
};
use windows::core::PCWSTR;

fn window_for_process(class: &[u16], process: u32) -> Option<HWND> {
    let mut previous = None;
    loop {
        // SAFETY: enumerates matching windows, then filters by the isolated child process.
        let window =
            unsafe { FindWindowExW(None, previous, PCWSTR(class.as_ptr()), PCWSTR::null()) }
                .ok()?;
        let mut owner = 0;
        unsafe {
            GetWindowThreadProcessId(window, Some(&mut owner));
        }
        if owner == process {
            return Some(window);
        }
        previous = Some(window);
    }
}

fn save_preview(window: HWND, path: &std::path::Path) {
    use windows::Win32::Graphics::Gdi::*;
    // SAFETY: snapshot of the isolated child collector window only, with owned GDI resources.
    unsafe {
        let _ = RedrawWindow(Some(window), None, None, RDW_INVALIDATE | RDW_UPDATENOW);
        let mut rect = RECT::default();
        windows::Win32::UI::WindowsAndMessaging::GetClientRect(window, &mut rect).unwrap();
        let dc = GetDC(Some(window));
        let memory = CreateCompatibleDC(Some(dc));
        let bitmap = CreateCompatibleBitmap(dc, rect.right, rect.bottom);
        let old = SelectObject(memory, HGDIOBJ(bitmap.0));
        BitBlt(
            memory,
            0,
            0,
            rect.right,
            rect.bottom,
            Some(dc),
            0,
            0,
            SRCCOPY,
        )
        .unwrap();
        SelectObject(memory, old);
        let mut info = BITMAPINFO {
            bmiHeader: BITMAPINFOHEADER {
                biSize: 40,
                biWidth: rect.right,
                biHeight: -rect.bottom,
                biPlanes: 1,
                biBitCount: 32,
                biCompression: BI_RGB.0,
                ..Default::default()
            },
            ..Default::default()
        };
        let mut pixels = vec![0u8; (rect.right * rect.bottom * 4) as usize];
        assert_ne!(
            GetDIBits(
                memory,
                bitmap,
                0,
                rect.bottom as u32,
                Some(pixels.as_mut_ptr().cast()),
                &mut info,
                DIB_RGB_COLORS
            ),
            0
        );
        let mut bytes = Vec::from(&b"BM"[..]);
        bytes.extend_from_slice(&(54 + pixels.len() as u32).to_le_bytes());
        bytes.extend_from_slice(&0u32.to_le_bytes());
        bytes.extend_from_slice(&54u32.to_le_bytes());
        bytes.extend_from_slice(&40u32.to_le_bytes());
        bytes.extend_from_slice(&rect.right.to_le_bytes());
        bytes.extend_from_slice(&(-rect.bottom).to_le_bytes());
        bytes.extend_from_slice(&1u16.to_le_bytes());
        bytes.extend_from_slice(&32u16.to_le_bytes());
        bytes.extend_from_slice(&[0; 24]);
        bytes.extend_from_slice(&pixels);
        std::fs::write(path, bytes).unwrap();
        let _ = DeleteObject(HGDIOBJ(bitmap.0));
        let _ = DeleteDC(memory);
        ReleaseDC(Some(window), dc);
    }
}

#[test]
#[ignore = "shows a draggable window on the desktop for a few seconds"]
fn tray_toggles_monitor_and_lock_controls_drag_without_activation() {
    let b = MapBuilder::new(60).engine_vars();
    let mut steps = vec![Step::Static {
        status: 1,
        update: 1,
        session_info: SessionSpec::default().yaml(),
    }];
    for i in 0..360 {
        steps.push(Step::Tick {
            t_ms: i * 16,
            tick: i as i32,
            row: driving_row(&b, 40.0 + i as f64 * 0.05),
        });
    }
    let root = temp_dir("ui-smoke");
    let fixture = root.join("ui.sdkfix");
    write_file(&fixture, &b.write_steps(Vec::new(), &steps).unwrap());

    write_file(
        &root.join("state/wheel-diagnostics/preferences.json"),
        br#"{"AutoRecord":false}"#,
    );
    let mut child = Command::new(env!("CARGO_BIN_EXE_coach-collector"))
        .env("COACH_TEST_INSTANCE_ID", coach_collector::util::uuid_v4())
        .args(["run", "--replay"])
        .arg(&fixture)
        .args(["--speed", "1", "--dry-run", "--state-dir"])
        .arg(root.join("state"))
        .arg("--iracing-root")
        .arg(&root)
        .spawn()
        .unwrap();

    let class = wide("CoachOverlayWindow");
    let find = || window_for_process(&class, child.id());
    let mut seen = None;
    let end = Instant::now() + Duration::from_secs(8);
    while Instant::now() < end {
        if let Some(hwnd) = find()
            && unsafe { IsWindowVisible(hwnd).as_bool() }
        {
            seen = Some(hwnd);
            break;
        }
        std::thread::sleep(Duration::from_millis(50));
    }
    let hwnd = seen.expect("the overlay window became visible while the sim was connected");
    // SAFETY: queries about a window of the child process.
    unsafe {
        let ex = GetWindowLongPtrW(hwnd, GWL_EXSTYLE) as u32;
        for (flag, name) in [
            (WS_EX_LAYERED, "layered"),
            (WS_EX_TOPMOST, "topmost"),
            (WS_EX_NOACTIVATE, "no-activate"),
            (WS_EX_TOOLWINDOW, "tool window"),
        ] {
            assert!(ex & flag.0 != 0, "{name} style missing: {ex:#x}");
        }
        assert_eq!(
            ex & WS_EX_TRANSPARENT.0,
            0,
            "dragging must accept pointer input"
        );
        assert_eq!(
            SendMessageW(hwnd, WM_NCHITTEST, None, Some(LPARAM(0))).0,
            HTCAPTION as isize
        );
        assert_eq!(
            SendMessageW(hwnd, WM_MOUSEACTIVATE, None, None).0,
            3,
            "drag must not activate the overlay"
        );
        let mut rect = RECT::default();
        GetWindowRect(hwnd, &mut rect).unwrap();
        assert_eq!((rect.right - rect.left, rect.bottom - rect.top), (540, 386));
    }

    let controller = window_for_process(&wide("CoachControllerWindow"), child.id()).unwrap();
    // SAFETY: messages exercise exactly the tray left-click event and local lock on the test app.
    unsafe {
        SendMessageW(
            controller,
            WM_APP + 1,
            None,
            Some(LPARAM(WM_LBUTTONUP as isize)),
        );
        assert!(!IsWindowVisible(hwnd).as_bool());
        SendMessageW(
            controller,
            WM_APP + 1,
            None,
            Some(LPARAM(WM_LBUTTONUP as isize)),
        );
        assert!(IsWindowVisible(hwnd).as_bool());
        let mut rect = RECT::default();
        GetWindowRect(hwnd, &mut rect).unwrap();
        let point =
            LPARAM((((rect.top + 100) as u32) << 16 | (rect.left + 100) as u32 & 0xFFFF) as isize);
        assert_eq!(
            SendMessageW(hwnd, WM_NCHITTEST, None, Some(point)).0,
            HTCAPTION as isize
        );
        SendMessageW(hwnd, WM_LBUTTONUP, None, Some(LPARAM((12 << 16) | 525)));
        assert_eq!(
            SendMessageW(hwnd, WM_NCHITTEST, None, Some(point)).0,
            HTCLIENT as isize
        );
    }
    if let Ok(path) = std::env::var("COACH_MONITOR_UI_SNAPSHOT") {
        save_preview(hwnd, std::path::Path::new(&path));
    }
    let config = coach_collector::config::Config::load(&root.join("state"));
    assert!(
        config.monitor_locked,
        "lock is retained for the next launch"
    );
    assert!(config.monitor_visible);
    // The replay can disconnect without defeating the explicit show/hide preference.
    std::thread::sleep(Duration::from_secs(6));
    assert!(unsafe { IsWindowVisible(hwnd).as_bool() });
    unsafe {
        SendMessageW(controller, WM_CLOSE, None, None);
    }
    let exit_deadline = Instant::now() + Duration::from_secs(8);
    let status = loop {
        if let Some(s) = child.try_wait().unwrap() {
            break s;
        }
        assert!(Instant::now() < exit_deadline, "the collector did not exit");
        std::thread::sleep(Duration::from_millis(50));
    };
    assert!(status.success(), "{status:?}");
    // The config was written for the next start.
    assert!(root.join("state").join("collector.json").exists());
    let _ = std::fs::remove_dir_all(&root);
}

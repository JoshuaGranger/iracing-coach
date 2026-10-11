//! Starts the real collector binary with its overlay, tray and hotkey code on a replayed fixture
//! (`--replay --dry-run`, no server) and checks the overlay window it creates. It briefly shows a
//! draggable window on the desktop, so it is ignored by default:
//! `cargo test -p coach-collector --test win_ui_smoke -- --ignored`.
#![cfg(windows)]

mod support;

use std::process::Command;
use std::time::{Duration, Instant};

use coach_collector::fixture::{MapBuilder, SessionSpec, Step};
use coach_collector::win::{QuitEvent, wide};
use support::*;
use windows::Win32::Foundation::{LPARAM, RECT};
use windows::Win32::UI::WindowsAndMessaging::{
    FindWindowW, GWL_EXSTYLE, GetWindowLongPtrW, GetWindowRect, HTCAPTION, IsWindowVisible,
    SendMessageW, WM_MOUSEACTIVATE, WM_NCHITTEST, WS_EX_LAYERED, WS_EX_NOACTIVATE,
    WS_EX_TOOLWINDOW, WS_EX_TOPMOST, WS_EX_TRANSPARENT,
};
use windows::core::PCWSTR;

#[test]
#[ignore = "shows a draggable window on the desktop for a few seconds"]
fn the_overlay_is_always_draggable_without_activation_and_exits_cleanly() {
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

    let mut child = Command::new(env!("CARGO_BIN_EXE_coach-collector"))
        .args(["run", "--replay"])
        .arg(&fixture)
        .args(["--speed", "1", "--dry-run", "--state-dir"])
        .arg(root.join("state"))
        .arg("--iracing-root")
        .arg(&root)
        .spawn()
        .unwrap();

    let class = wide("CoachOverlayWindow");
    let find = || unsafe { FindWindowW(PCWSTR(class.as_ptr()), PCWSTR::null()).ok() };
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
        assert_eq!((rect.right - rect.left, rect.bottom - rect.top), (540, 360));
    }

    // The replay ends (the sim "quits"): the overlay hides itself.
    let hidden = wait_until(Duration::from_secs(8), || {
        find().is_none_or(|h| unsafe { !IsWindowVisible(h).as_bool() })
    });
    assert!(hidden, "the monitor hides when the sim is gone");

    assert!(
        QuitEvent::signal_running(),
        "the collector listens for the quit event"
    );
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

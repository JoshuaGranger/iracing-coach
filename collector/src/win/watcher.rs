//! Directory-change notification for `Documents\iRacing\telemetry` and `...\setups`
//! (`ReadDirectoryChangesW`). It only asks the uploader to scan again: the notification costs the
//! kernel nothing on the sim and no file is opened here. The scan itself waits for off-track
//! evidence.

use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use windows::Win32::Foundation::{CloseHandle, HANDLE, WAIT_OBJECT_0};
use windows::Win32::Storage::FileSystem::{
    CreateFileW, FILE_FLAG_BACKUP_SEMANTICS, FILE_FLAG_OVERLAPPED, FILE_LIST_DIRECTORY,
    FILE_NOTIFY_CHANGE_DIR_NAME, FILE_NOTIFY_CHANGE_FILE_NAME, FILE_NOTIFY_CHANGE_LAST_WRITE,
    FILE_NOTIFY_CHANGE_SIZE, FILE_SHARE_DELETE, FILE_SHARE_READ, FILE_SHARE_WRITE, OPEN_EXISTING,
    ReadDirectoryChangesW,
};
use windows::Win32::System::IO::{CancelIoEx, GetOverlappedResult, OVERLAPPED};
use windows::Win32::System::Threading::{CreateEventW, WaitForSingleObject};
use windows::core::PCWSTR;

use super::wide;
use crate::hub::Hub;

/// Minimum spacing between wake-ups; an active recording changes its file continuously.
const WAKE_SPACING: Duration = Duration::from_secs(2);

pub fn spawn(hub: Arc<Hub>, dirs: Vec<PathBuf>) -> Vec<JoinHandle<()>> {
    dirs.into_iter()
        .map(|dir| {
            let hub = hub.clone();
            thread::Builder::new()
                .name("watcher".into())
                .spawn(move || watch(&hub, &dir))
                .expect("spawn watcher thread")
        })
        .collect()
}

fn watch(hub: &Hub, dir: &Path) {
    while !hub.is_shutdown() {
        if !dir.is_dir() {
            sleep_unless_shutdown(hub, Duration::from_secs(5));
            continue;
        }
        if watch_once(hub, dir).is_err() {
            sleep_unless_shutdown(hub, Duration::from_secs(5));
        }
    }
}

fn sleep_unless_shutdown(hub: &Hub, total: Duration) {
    let end = Instant::now() + total;
    while Instant::now() < end && !hub.is_shutdown() {
        thread::sleep(Duration::from_millis(100));
    }
}

fn watch_once(hub: &Hub, dir: &Path) -> windows::core::Result<()> {
    let name = wide(&dir.to_string_lossy());
    // SAFETY: handles are closed on every path below; the buffer and OVERLAPPED outlive the
    // pending operation because it is cancelled and awaited before they go out of scope.
    unsafe {
        let handle = CreateFileW(
            PCWSTR(name.as_ptr()),
            FILE_LIST_DIRECTORY.0,
            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
            None,
            OPEN_EXISTING,
            FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OVERLAPPED,
            None,
        )?;
        let event = CreateEventW(None, true, false, PCWSTR::null())?;
        let result = pump(hub, handle, event);
        let _ = CloseHandle(event);
        let _ = CloseHandle(handle);
        result
    }
}

unsafe fn pump(hub: &Hub, handle: HANDLE, event: HANDLE) -> windows::core::Result<()> {
    let mut buffer = vec![0u8; 16 * 1024];
    let mut last_wake: Option<Instant> = None;
    let filter = FILE_NOTIFY_CHANGE_FILE_NAME
        | FILE_NOTIFY_CHANGE_DIR_NAME
        | FILE_NOTIFY_CHANGE_SIZE
        | FILE_NOTIFY_CHANGE_LAST_WRITE;
    while !hub.is_shutdown() {
        let mut overlapped = OVERLAPPED {
            hEvent: event,
            ..OVERLAPPED::default()
        };
        // SAFETY: see `watch_once`.
        unsafe {
            ReadDirectoryChangesW(
                handle,
                buffer.as_mut_ptr().cast(),
                buffer.len() as u32,
                true,
                filter,
                None,
                Some(&mut overlapped),
                None,
            )?;
            loop {
                if WaitForSingleObject(event, 500) == WAIT_OBJECT_0 {
                    break;
                }
                if hub.is_shutdown() {
                    let _ = CancelIoEx(handle, Some(&overlapped));
                    let mut n = 0u32;
                    let _ = GetOverlappedResult(handle, &overlapped, &mut n, true);
                    return Ok(());
                }
            }
            let mut n = 0u32;
            GetOverlappedResult(handle, &overlapped, &mut n, false)?;
        }
        // Space wake-ups out: sleep off the rest of the interval (the kernel keeps buffering what
        // happens meanwhile), then wake the uploader once for the whole burst.
        if let Some(t) = last_wake {
            let since = t.elapsed();
            if since < WAKE_SPACING {
                sleep_unless_shutdown(hub, WAKE_SPACING - since);
            }
        }
        last_wake = Some(Instant::now());
        hub.wake_uploader();
    }
    Ok(())
}

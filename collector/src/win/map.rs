//! iRacing's shared-memory map (`Local\IRSDKMemMapFileName`), opened read-only, and its
//! data-valid event (`Local\IRSDKDataValidEvent`). Reads copy out of the view; nothing here ever
//! hands out a reference into memory another process is writing.

use std::io;
use std::time::Duration;

use windows::Win32::Foundation::{CloseHandle, HANDLE};
use windows::Win32::System::Memory::{
    FILE_MAP_READ, MEMORY_BASIC_INFORMATION, MEMORY_MAPPED_VIEW_ADDRESS, MapViewOfFile,
    OpenFileMappingW, UnmapViewOfFile, VirtualQuery,
};
use windows::Win32::System::Threading::{
    CREATE_WAITABLE_TIMER_HIGH_RESOLUTION, CreateWaitableTimerExW, OpenEventW,
    SYNCHRONIZATION_SYNCHRONIZE, SetWaitableTimer, TIMER_ALL_ACCESS, WaitForSingleObject,
};
use windows::core::PCWSTR;

use super::wide;
use crate::sdk::{MapSource, SdkMap};

const MAP_NAME: &str = "Local\\IRSDKMemMapFileName";
const EVENT_NAME: &str = "Local\\IRSDKDataValidEvent";

/// Opens the real map; `Ok(None)` while iRacing has never run.
pub struct Win32MapSource;

impl MapSource for Win32MapSource {
    fn open(&mut self) -> io::Result<Option<Box<dyn SdkMap>>> {
        let name = wide(MAP_NAME);
        // SAFETY: the name is NUL-terminated; every handle and view is released in Drop.
        unsafe {
            let mapping = match OpenFileMappingW(FILE_MAP_READ.0, false, PCWSTR(name.as_ptr())) {
                Ok(h) => h,
                Err(_) => return Ok(None),
            };
            let view = MapViewOfFile(mapping, FILE_MAP_READ, 0, 0, 0);
            if view.Value.is_null() {
                let err = io::Error::last_os_error();
                let _ = CloseHandle(mapping);
                return Err(err);
            }
            let mut info = MEMORY_BASIC_INFORMATION::default();
            let got = VirtualQuery(
                Some(view.Value as *const _),
                &mut info,
                std::mem::size_of::<MEMORY_BASIC_INFORMATION>(),
            );
            if got == 0 || info.RegionSize == 0 {
                let _ = UnmapViewOfFile(view);
                let _ = CloseHandle(mapping);
                return Err(io::Error::other("cannot size the SDK map"));
            }
            let event_name = wide(EVENT_NAME);
            let event = OpenEventW(
                SYNCHRONIZATION_SYNCHRONIZE,
                false,
                PCWSTR(event_name.as_ptr()),
            )
            .ok();
            let timer = if event.is_none() {
                CreateWaitableTimerExW(
                    None,
                    PCWSTR::null(),
                    CREATE_WAITABLE_TIMER_HIGH_RESOLUTION,
                    TIMER_ALL_ACCESS.0,
                )
                .ok()
            } else {
                None
            };
            Ok(Some(Box::new(Win32Map {
                mapping,
                view,
                len: info.RegionSize,
                event,
                timer,
            })))
        }
    }
}

pub struct Win32Map {
    mapping: HANDLE,
    view: MEMORY_MAPPED_VIEW_ADDRESS,
    len: usize,
    event: Option<HANDLE>,
    timer: Option<HANDLE>,
}

// SAFETY: the handles and the read-only view are plain process-wide resources.
unsafe impl Send for Win32Map {}

impl SdkMap for Win32Map {
    fn len(&self) -> usize {
        self.len
    }

    fn read(&self, offset: usize, dst: &mut [u8]) -> bool {
        let Some(end) = offset.checked_add(dst.len()) else {
            return false;
        };
        if end > self.len {
            return false;
        }
        // SAFETY: [offset, end) lies inside the mapped view; the source is only read, and a torn
        // value is handled by the reader's tick re-check.
        unsafe {
            std::ptr::copy_nonoverlapping(
                (self.view.Value as *const u8).add(offset),
                dst.as_mut_ptr(),
                dst.len(),
            );
        }
        true
    }

    fn wait(&mut self, timeout: Duration) {
        let ms = timeout.as_millis().clamp(1, u128::from(u32::MAX)) as u32;
        // SAFETY: waits on handles owned by this struct.
        unsafe {
            if let Some(event) = self.event {
                let _ = WaitForSingleObject(event, ms);
                return;
            }
            if let Some(timer) = self.timer {
                // 0.8 of the tick period, from the header's tick rate (bytes 8..12).
                let mut rate = [0u8; 4];
                let tick_rate = if self.read(8, &mut rate) {
                    i32::from_le_bytes(rate).clamp(1, 1000)
                } else {
                    60
                };
                let period_100ns = (10_000_000.0 * 0.8 / f64::from(tick_rate)) as i64;
                let due = -period_100ns.max(10_000);
                if SetWaitableTimer(timer, &due, 0, None, None, false).is_ok() {
                    let _ = WaitForSingleObject(timer, ms);
                    return;
                }
            }
        }
        std::thread::sleep(Duration::from_millis(u64::from(ms.min(4))));
    }
}

impl Drop for Win32Map {
    fn drop(&mut self) {
        // SAFETY: each handle and the view were created in `open` and are released once.
        unsafe {
            let _ = UnmapViewOfFile(self.view);
            let _ = CloseHandle(self.mapping);
            if let Some(h) = self.event {
                let _ = CloseHandle(h);
            }
            if let Some(h) = self.timer {
                let _ = CloseHandle(h);
            }
        }
    }
}

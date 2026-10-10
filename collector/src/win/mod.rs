//! Windows-only parts: the shared-memory map, the overlay and tray, the directory watcher, thread
//! priority, the single-instance mutex and the install commands.

pub mod direct_input;
pub mod install;
pub mod map;
pub mod render;
pub mod ui;
pub mod watcher;
pub mod wheel_panel;
pub mod wheel_usb;

use std::path::PathBuf;

use windows::Win32::Foundation::{
    CloseHandle, ERROR_ALREADY_EXISTS, GetLastError, HANDLE, WAIT_OBJECT_0,
};
use windows::Win32::System::Com::CoTaskMemFree;
use windows::Win32::System::Threading::{
    CreateEventW, CreateMutexW, GetCurrentThread, OpenEventW, SYNCHRONIZATION_SYNCHRONIZE,
    SetEvent, SetThreadPriority, THREAD_MODE_BACKGROUND_BEGIN, WaitForSingleObject,
};
use windows::Win32::UI::Shell::{FOLDERID_Documents, KF_FLAG_DEFAULT, SHGetKnownFolderPath};
use windows::core::PCWSTR;

pub const INSTANCE_MUTEX: &str = "Local\\iRacingCoach.Collector";
pub const QUIT_EVENT: &str = "Local\\iRacingCoach.Collector.Quit";

/// Integration tests isolate their processes from the real collector in the same Windows
/// session. Detached children inherit the same environment and therefore the same namespace.
fn instance_name(base: &str) -> String {
    std::env::var("COACH_TEST_INSTANCE_ID")
        .ok()
        .filter(|id| !id.is_empty())
        .map_or_else(|| base.to_string(), |id| format!("{base}.{id}"))
}

/// A Start menu launch gets a private console because the binary also exposes a CLI. Hide only
/// that private window; a PowerShell/cmd console shared with its caller must remain visible.
pub fn hide_own_console() {
    use windows::Win32::System::Console::{GetConsoleProcessList, GetConsoleWindow};
    use windows::Win32::System::Threading::GetCurrentProcessId;
    use windows::Win32::UI::WindowsAndMessaging::{SW_HIDE, ShowWindow};
    let mut processes = [0];
    // SAFETY: the one-element buffer is writable and these calls retain no pointers.
    unsafe {
        if GetConsoleProcessList(&mut processes) == 1 && processes[0] == GetCurrentProcessId() {
            let window = GetConsoleWindow();
            if !window.is_invalid() {
                let _ = ShowWindow(window, SW_HIDE);
            }
        }
    }
}

/// NUL-terminated UTF-16.
pub fn wide(s: &str) -> Vec<u16> {
    s.encode_utf16().chain(std::iter::once(0)).collect()
}

/// The user's Documents folder (honours folder redirection), or None when it cannot be resolved.
pub fn known_folder_documents() -> Option<PathBuf> {
    // SAFETY: SHGetKnownFolderPath returns a CoTaskMem string we free after copying it.
    unsafe {
        let path = SHGetKnownFolderPath(&FOLDERID_Documents, KF_FLAG_DEFAULT, None).ok()?;
        let text = path.to_string().ok();
        CoTaskMemFree(Some(path.0 as *const _));
        text.map(PathBuf::from)
    }
}

/// Starts `command_line` as a detached process that inherits no handles: not the caller's output
/// pipes (a remote job or a shell would wait for them to close), not its console. `command_line`
/// is the full Windows command line, program first.
pub fn spawn_detached(command_line: &str) -> std::io::Result<()> {
    use windows::Win32::System::Threading::{
        CREATE_NEW_PROCESS_GROUP, CREATE_NO_WINDOW, CreateProcessW, DETACHED_PROCESS,
        PROCESS_INFORMATION, STARTUPINFOW,
    };
    use windows::core::PWSTR;
    let mut line = wide(command_line);
    let startup = STARTUPINFOW {
        cb: std::mem::size_of::<STARTUPINFOW>() as u32,
        ..STARTUPINFOW::default()
    };
    let mut info = PROCESS_INFORMATION::default();
    // SAFETY: `line` is a writable NUL-terminated buffer that outlives the call; the returned
    // handles are closed right away.
    unsafe {
        CreateProcessW(
            PCWSTR::null(),
            Some(PWSTR(line.as_mut_ptr())),
            None,
            None,
            false,
            DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW,
            None,
            PCWSTR::null(),
            &startup,
            &mut info,
        )
        .map_err(|e| std::io::Error::other(e.to_string()))?;
        let _ = CloseHandle(info.hProcess);
        let _ = CloseHandle(info.hThread);
    }
    Ok(())
}

/// One command-line argument, quoted for `CreateProcess` when it needs it.
pub fn quote_arg(arg: &str) -> String {
    if !arg.is_empty() && !arg.contains([' ', '\t', '"']) {
        return arg.to_string();
    }
    let mut out = String::from("\"");
    let mut backslashes = 0;
    for c in arg.chars() {
        match c {
            '\\' => backslashes += 1,
            '"' => {
                out.push_str(&"\\".repeat(backslashes * 2 + 1));
                out.push('"');
                backslashes = 0;
            }
            _ => {
                out.push_str(&"\\".repeat(backslashes));
                out.push(c);
                backslashes = 0;
            }
        }
    }
    out.push_str(&"\\".repeat(backslashes * 2));
    out.push('"');
    out
}

/// Lowers the calling thread's CPU, I/O and memory priority so file work never competes with the
/// simulator (background mode). Failure is harmless and ignored.
pub fn enter_background_mode() {
    // SAFETY: plain Win32 call on the current thread.
    unsafe {
        let _ = SetThreadPriority(GetCurrentThread(), THREAD_MODE_BACKGROUND_BEGIN);
    }
}

/// Holds the single-instance mutex for the life of the process.
pub struct InstanceGuard {
    handle: HANDLE,
}

impl InstanceGuard {
    /// None when another collector already runs in this session.
    pub fn acquire() -> Option<InstanceGuard> {
        let name = wide(&instance_name(INSTANCE_MUTEX));
        // SAFETY: the name buffer outlives the call; the handle is closed in Drop.
        unsafe {
            let handle = CreateMutexW(None, true, PCWSTR(name.as_ptr())).ok()?;
            if GetLastError() == ERROR_ALREADY_EXISTS {
                let _ = CloseHandle(handle);
                return None;
            }
            Some(InstanceGuard { handle })
        }
    }

    /// True when a collector holds the mutex (used by install and uninstall).
    pub fn is_running() -> bool {
        let name = wide(&instance_name(INSTANCE_MUTEX));
        // SAFETY: as above.
        unsafe {
            match CreateMutexW(None, false, PCWSTR(name.as_ptr())) {
                Ok(handle) => {
                    let existed = GetLastError() == ERROR_ALREADY_EXISTS;
                    let _ = CloseHandle(handle);
                    existed
                }
                Err(_) => false,
            }
        }
    }
}

impl Drop for InstanceGuard {
    fn drop(&mut self) {
        // SAFETY: the handle came from CreateMutexW and is closed once.
        unsafe {
            let _ = CloseHandle(self.handle);
        }
    }
}

/// The event another process signals to ask the running collector to exit.
pub struct QuitEvent {
    handle: HANDLE,
}

impl QuitEvent {
    pub fn create() -> Option<QuitEvent> {
        let name = wide(&instance_name(QUIT_EVENT));
        // SAFETY: as above; a manual-reset event so every waiter sees it.
        let handle = unsafe { CreateEventW(None, true, false, PCWSTR(name.as_ptr())).ok()? };
        Some(QuitEvent { handle })
    }

    pub fn is_set(&self) -> bool {
        // SAFETY: zero-timeout wait on our own handle.
        unsafe { WaitForSingleObject(self.handle, 0) == WAIT_OBJECT_0 }
    }

    /// Asks a running collector to exit; false when none is listening.
    pub fn signal_running() -> bool {
        let name = wide(&instance_name(QUIT_EVENT));
        // SAFETY: opens the named event, sets it, closes the handle.
        unsafe {
            let Ok(handle) = OpenEventW(
                SYNCHRONIZATION_SYNCHRONIZE | windows::Win32::System::Threading::EVENT_MODIFY_STATE,
                false,
                PCWSTR(name.as_ptr()),
            ) else {
                return false;
            };
            let ok = SetEvent(handle).is_ok();
            let _ = CloseHandle(handle);
            ok
        }
    }
}

impl Drop for QuitEvent {
    fn drop(&mut self) {
        // SAFETY: closed once.
        unsafe {
            let _ = CloseHandle(self.handle);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::quote_arg;

    #[test]
    fn arguments_are_quoted_for_createprocess() {
        assert_eq!(quote_arg("run"), "run");
        assert_eq!(
            quote_arg(r"C:\Program Files\x\a.exe"),
            r#""C:\Program Files\x\a.exe""#
        );
        assert_eq!(quote_arg(r"C:\dir with space\"), r#""C:\dir with space\\""#);
        assert_eq!(quote_arg(r#"say "hi""#), r#""say \"hi\"""#);
        assert_eq!(quote_arg(""), r#""""#);
    }
}

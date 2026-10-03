//! `install` and `uninstall`: no installer framework, no admin rights, no firewall rule.
//! `install --server <url>` writes the config, sets the per-user Run value and starts the
//! collector in the current interactive session; `uninstall` removes the Run value and the
//! collector's own files only (never anything under `Documents\iRacing`).

use std::fs;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use windows::Win32::Foundation::ERROR_SUCCESS;
use windows::Win32::System::Registry::{
    HKEY, HKEY_CURRENT_USER, KEY_SET_VALUE, REG_OPTION_NON_VOLATILE, REG_SZ, RegCloseKey,
    RegCreateKeyExW, RegDeleteValueW, RegOpenKeyExW, RegSetValueExW,
};
use windows::core::PCWSTR;

use super::{InstanceGuard, QuitEvent, wide};
use crate::config::{CONFIG_FILE, Config, Paths, normalize_server};

const RUN_KEY: &str = "Software\\Microsoft\\Windows\\CurrentVersion\\Run";
/// Tests point the Run value at a throwaway key instead of the real one.
const RUN_KEY_OVERRIDE_ENV: &str = "COACH_TEST_RUN_KEY";

fn run_key() -> String {
    std::env::var(RUN_KEY_OVERRIDE_ENV).unwrap_or_else(|_| RUN_KEY.to_string())
}

/// `--state-dir` and `--iracing-root` as given to `install`, forwarded to the started process and
/// the Run value so a non-default layout survives a restart.
fn forwarded_args(paths: &Paths) -> Vec<String> {
    let default = Paths::default_paths();
    let mut args = Vec::new();
    if paths.state_dir != default.state_dir {
        args.push("--state-dir".to_string());
        args.push(paths.state_dir.to_string_lossy().into_owned());
    }
    if paths.iracing_root != default.iracing_root {
        args.push("--iracing-root".to_string());
        args.push(paths.iracing_root.to_string_lossy().into_owned());
    }
    args
}
const RUN_VALUE: &str = "iRacing Coach Collector";

pub fn install(server: Option<&str>, paths: &Paths) -> Result<(), String> {
    let exe = std::env::current_exe().map_err(|e| format!("cannot locate the executable: {e}"))?;
    let mut config = Config::load(&paths.state_dir);
    if let Some(server) = server {
        config.server = normalize_server(server).map_err(|e| e.to_string())?;
    }
    config
        .save(&paths.state_dir)
        .map_err(|e| format!("cannot write {}: {e}", paths.state_dir.display()))?;
    let forwarded = forwarded_args(paths);
    set_run_value(&exe, &forwarded)?;
    // A collector from an earlier install is asked to exit so the new settings apply.
    stop_running(Duration::from_secs(8))?;
    let mut line = super::quote_arg(&exe.to_string_lossy());
    line.push_str(" run");
    for arg in &forwarded {
        line.push(' ');
        line.push_str(&super::quote_arg(arg));
    }
    super::spawn_detached(&line).map_err(|e| format!("cannot start {}: {e}", exe.display()))?;
    println!("installed {}", exe.display());
    println!("server  {}", config.server);
    println!("id      {}", config.collector_id);
    println!("state   {}", paths.state_dir.display());
    println!("started; it also starts at sign-in (HKCU Run value \"{RUN_VALUE}\")");
    Ok(())
}

pub fn uninstall(paths: &Paths) -> Result<(), String> {
    stop_running(Duration::from_secs(8))?;
    remove_run_value()?;
    let mut removed = Vec::new();
    for name in [
        CONFIG_FILE,
        "collector.json.tmp",
        "journal.jsonl",
        "journal.jsonl.tmp",
        "dry-run.jsonl",
    ] {
        let path = paths.state_dir.join(name);
        if path.is_file() && fs::remove_file(&path).is_ok() {
            removed.push(path);
        }
    }
    // The state folder name is shared with the retired desktop app, so only the collector's own
    // log files go, and folders only when they end up empty.
    let logs = paths.logs_dir();
    for name in ["collector.log", "collector.log.1"] {
        let path = logs.join(name);
        if path.is_file() && fs::remove_file(&path).is_ok() {
            removed.push(path);
        }
    }
    let _ = fs::remove_dir(&logs);
    let _ = fs::remove_dir(&paths.state_dir);
    for path in &removed {
        println!("removed {}", path.display());
    }
    println!("removed the Run value \"{RUN_VALUE}\"");
    schedule_exe_removal()?;
    Ok(())
}

/// Asks a running collector to exit and waits until the single-instance mutex is free.
fn stop_running(wait: Duration) -> Result<(), String> {
    if !InstanceGuard::is_running() {
        return Ok(());
    }
    QuitEvent::signal_running();
    let deadline = Instant::now() + wait;
    while Instant::now() < deadline {
        if !InstanceGuard::is_running() {
            return Ok(());
        }
        std::thread::sleep(Duration::from_millis(100));
    }
    Err("the running collector did not exit; close it from its tray menu and try again".into())
}

fn set_run_value(exe: &Path, forwarded: &[String]) -> Result<(), String> {
    let mut command = format!("\"{}\" run", exe.display());
    for arg in forwarded {
        command.push_str(&format!(" \"{arg}\""));
    }
    let data: Vec<u8> = wide(&command)
        .iter()
        .flat_map(|u| u.to_le_bytes())
        .collect();
    // SAFETY: the key handle is closed on every path; buffers outlive the calls.
    unsafe {
        let mut key = HKEY::default();
        let status = RegCreateKeyExW(
            HKEY_CURRENT_USER,
            PCWSTR(wide(&run_key()).as_ptr()),
            None,
            PCWSTR::null(),
            REG_OPTION_NON_VOLATILE,
            KEY_SET_VALUE,
            None,
            &mut key,
            None,
        );
        if status != ERROR_SUCCESS {
            return Err(format!("cannot open the Run key ({})", status.0));
        }
        let status = RegSetValueExW(
            key,
            PCWSTR(wide(RUN_VALUE).as_ptr()),
            None,
            REG_SZ,
            Some(&data),
        );
        let _ = RegCloseKey(key);
        if status != ERROR_SUCCESS {
            return Err(format!("cannot set the Run value ({})", status.0));
        }
    }
    Ok(())
}

fn remove_run_value() -> Result<(), String> {
    // SAFETY: as above.
    unsafe {
        let mut key = HKEY::default();
        let status = RegOpenKeyExW(
            HKEY_CURRENT_USER,
            PCWSTR(wide(&run_key()).as_ptr()),
            None,
            KEY_SET_VALUE,
            &mut key,
        );
        if status != ERROR_SUCCESS {
            return Ok(()); // no Run key, nothing to remove
        }
        let status = RegDeleteValueW(key, PCWSTR(wide(RUN_VALUE).as_ptr()));
        let _ = RegCloseKey(key);
        // ERROR_FILE_NOT_FOUND (2): the value was not there.
        if status != ERROR_SUCCESS && status.0 != 2 {
            return Err(format!("cannot remove the Run value ({})", status.0));
        }
    }
    Ok(())
}

/// The running executable cannot delete itself: a detached `cmd` removes it (and its folder when
/// empty) a moment after this process exits. Only the installed copy under
/// `Programs\iRacingCoach` is ever removed.
fn schedule_exe_removal() -> Result<(), String> {
    let exe: PathBuf = std::env::current_exe().map_err(|e| e.to_string())?;
    let installed = exe
        .parent()
        .and_then(Path::file_name)
        .is_some_and(|n| n.eq_ignore_ascii_case("iRacingCoach"))
        && exe
            .file_name()
            .is_some_and(|n| n.eq_ignore_ascii_case("coach-collector.exe"));
    if !installed {
        println!("left {} in place (not an installed copy)", exe.display());
        return Ok(());
    }
    let dir = exe.parent().map(Path::to_path_buf).unwrap_or_default();
    let script = format!(
        "ping -n 3 127.0.0.1 >nul & del /f /q \"{}\" & rmdir \"{}\"",
        exe.display(),
        dir.display()
    );
    // cmd strips the outer quotes of `/C "..."`, so the inner ones stay as they are.
    super::spawn_detached(&format!("cmd.exe /C \"{script}\""))
        .map_err(|e| format!("cannot schedule removal of {}: {e}", exe.display()))?;
    println!("removing {} after exit", exe.display());
    Ok(())
}

//! `install` and `uninstall` against a throwaway registry key and folders (`COACH_TEST_RUN_KEY`
//! redirects the Run value; the real `HKCU\...\Run` is never touched). It starts a collector for a
//! few seconds, so it is ignored by default:
//! `cargo test -p coach-collector --test win_install -- --ignored`.
#![cfg(windows)]

mod support;

use std::path::Path;
use std::process::Command;
use std::time::{Duration, Instant};

use coach_collector::win::InstanceGuard;
use support::*;

const KEY: &str = r"Software\iRacingCoachTest\Run";
const VALUE: &str = "iRacing Coach Collector";

fn reg_query() -> Option<String> {
    let out = Command::new("reg")
        .args(["query", &format!(r"HKCU\{KEY}"), "/v", VALUE])
        .output()
        .unwrap();
    out.status
        .success()
        .then(|| String::from_utf8_lossy(&out.stdout).into_owned())
}

fn run(exe: &Path, args: &[&str]) -> std::process::Output {
    Command::new(exe)
        .args(args)
        .env("COACH_TEST_RUN_KEY", KEY)
        .output()
        .unwrap()
}

#[test]
#[ignore = "starts a collector with a tray icon for a few seconds"]
fn install_sets_the_run_value_and_starts_it_and_uninstall_removes_everything_it_made() {
    assert!(
        !InstanceGuard::is_running(),
        "stop the running collector first"
    );
    let root = temp_dir("install");
    let app_dir = root.join("iRacingCoach");
    std::fs::create_dir_all(&app_dir).unwrap();
    let exe = app_dir.join("coach-collector.exe");
    std::fs::copy(env!("CARGO_BIN_EXE_coach-collector"), &exe).unwrap();
    let state = root.join("state");
    let iracing = root.join("iRacing");
    std::fs::create_dir_all(&iracing).unwrap();

    let out = run(
        &exe,
        &[
            "install",
            "--server",
            "127.0.0.1:9",
            "--state-dir",
            state.to_str().unwrap(),
            "--iracing-root",
            iracing.to_str().unwrap(),
        ],
    );
    assert!(
        out.status.success(),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    let text = String::from_utf8_lossy(&out.stdout);
    assert!(text.contains("http://127.0.0.1:9"), "{text}");

    let value = reg_query().expect("the Run value was written");
    assert!(
        value.contains("coach-collector.exe") && value.contains(" run"),
        "{value}"
    );
    assert!(
        value.contains("--state-dir"),
        "a non-default layout is kept: {value}"
    );
    let config: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(state.join("collector.json")).unwrap())
            .unwrap();
    assert_eq!(config["server"], "http://127.0.0.1:9");
    assert_eq!(config["collector_id"].as_str().unwrap().len(), 36);
    assert!(
        wait_until(Duration::from_secs(10), InstanceGuard::is_running),
        "started in this session"
    );

    // A second install replaces the running instance instead of starting a duplicate.
    let again = run(
        &exe,
        &[
            "install",
            "--state-dir",
            state.to_str().unwrap(),
            "--iracing-root",
            iracing.to_str().unwrap(),
        ],
    );
    assert!(
        again.status.success(),
        "{}",
        String::from_utf8_lossy(&again.stderr)
    );
    assert!(wait_until(
        Duration::from_secs(10),
        InstanceGuard::is_running
    ));

    let out = run(&exe, &["uninstall", "--state-dir", state.to_str().unwrap()]);
    assert!(
        out.status.success(),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    assert!(!InstanceGuard::is_running(), "stopped");
    assert!(reg_query().is_none(), "Run value removed");
    assert!(!state.join("collector.json").exists());
    assert!(!state.exists() || std::fs::read_dir(&state).unwrap().next().is_none());
    // The installed copy removes itself shortly after exit.
    let end = Instant::now() + Duration::from_secs(10);
    while exe.exists() && Instant::now() < end {
        std::thread::sleep(Duration::from_millis(200));
    }
    assert!(!exe.exists(), "the installed executable was removed");
    assert!(iracing.exists(), "nothing of iRacing's was touched");

    let _ = Command::new("reg")
        .args(["delete", r"HKCU\Software\iRacingCoachTest", "/f"])
        .output();
    let _ = std::fs::remove_dir_all(&root);
}

//! Installer lifecycle against a throwaway Run key, state folder and named-object namespace.
//! The listener below holds only the mutex and quit event: it never runs the collector app,
//! reads the SDK, draws a window, or touches production settings.
#![cfg(windows)]

use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

use coach_collector::util::uuid_v4;
use coach_collector::win::{InstanceGuard, QuitEvent};

const RUN_VALUE: &str = "iRacing Coach Collector";

struct TestInstall {
    root: PathBuf,
    exe: PathBuf,
    state: PathBuf,
    key: String,
    namespace: String,
}

impl TestInstall {
    fn new() -> Self {
        let namespace = uuid_v4();
        let root = std::env::temp_dir().join(format!("coach-lifecycle-{namespace}"));
        let app = root.join("iRacingCoach");
        let state = root.join("state");
        std::fs::create_dir_all(&app).unwrap();
        std::fs::create_dir_all(state.join("logs")).unwrap();
        let exe = app.join("coach-collector.exe");
        std::fs::copy(env!("CARGO_BIN_EXE_coach-collector"), &exe).unwrap();
        for file in ["collector.json", "journal.jsonl", "logs/collector.log"] {
            std::fs::write(state.join(file), format!("preserve {file}\n")).unwrap();
        }
        let key = format!(r"Software\iRacingCoachTest\{namespace}\Run");
        let out = Command::new("reg")
            .args([
                "add",
                &format!(r"HKCU\{key}"),
                "/v",
                RUN_VALUE,
                "/t",
                "REG_SZ",
                "/d",
                "test-only startup",
                "/f",
            ])
            .output()
            .unwrap();
        assert!(
            out.status.success(),
            "{}",
            String::from_utf8_lossy(&out.stderr)
        );
        Self {
            root,
            exe,
            state,
            key,
            namespace,
        }
    }

    fn run(&self, args: &[&str]) -> std::process::Output {
        Command::new(&self.exe)
            .args(args)
            .env("COACH_TEST_RUN_KEY", &self.key)
            .env("COACH_TEST_INSTANCE_ID", &self.namespace)
            .output()
            .unwrap()
    }

    fn run_value_exists(&self) -> bool {
        Command::new("reg")
            .args(["query", &format!(r"HKCU\{}", self.key), "/v", RUN_VALUE])
            .output()
            .unwrap()
            .status
            .success()
    }

    fn assert_files_preserved(&self) {
        for file in ["collector.json", "journal.jsonl", "logs/collector.log"] {
            assert_eq!(
                std::fs::read_to_string(self.state.join(file)).unwrap(),
                format!("preserve {file}\n")
            );
        }
        assert!(self.exe.is_file(), "Inno owns executable removal");
    }

    fn listener(&self, stubborn: bool, delay_ms: u64) -> Listener {
        let ready = self.root.join("listener-ready");
        let child = Command::new(std::env::current_exe().unwrap())
            .args(["--exact", "lifecycle_listener_helper", "--nocapture"])
            .env("COACH_TEST_LISTENER_READY", &ready)
            .env("COACH_TEST_INSTANCE_ID", &self.namespace)
            .env(
                "COACH_TEST_LISTENER_STUBBORN",
                if stubborn { "1" } else { "0" },
            )
            .env("COACH_TEST_LISTENER_DELAY_MS", delay_ms.to_string())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .unwrap();
        let listener = Listener(child);
        assert!(wait_until(Duration::from_secs(5), || ready.exists()));
        listener
    }
}

impl Drop for TestInstall {
    fn drop(&mut self) {
        let parent = self.key.strip_suffix(r"\Run").unwrap();
        let _ = Command::new("reg")
            .args(["delete", &format!(r"HKCU\{parent}"), "/f"])
            .output();
        let _ = std::fs::remove_dir_all(&self.root);
    }
}

struct Listener(Child);

impl Drop for Listener {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

fn wait_until(timeout: Duration, mut condition: impl FnMut() -> bool) -> bool {
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if condition() {
            return true;
        }
        std::thread::sleep(Duration::from_millis(20));
    }
    condition()
}

fn assert_ok(out: std::process::Output) {
    assert!(
        out.status.success(),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
}

#[test]
fn stop_waits_for_listener_and_keeps_startup_and_files() {
    let install = TestInstall::new();
    // Expose the acquired mutex before creating the event, as the real app's startup does.
    let mut listener = install.listener(false, 250);
    assert_ok(install.run(&["stop"]));
    assert!(wait_until(Duration::from_secs(2), || {
        listener.0.try_wait().unwrap().is_some_and(|s| s.success())
    }));
    install.assert_files_preserved();
    assert!(install.run_value_exists());
    // Already stopped is also successful.
    assert_ok(install.run(&["stop"]));
}

#[test]
fn installer_uninstall_stops_and_removes_only_startup() {
    let install = TestInstall::new();
    let mut listener = install.listener(false, 0);
    let args = [
        "uninstall",
        "--installer",
        "--state-dir",
        install.state.to_str().unwrap(),
    ];
    assert_ok(install.run(&args));
    assert!(wait_until(Duration::from_secs(2), || {
        listener.0.try_wait().unwrap().is_some_and(|s| s.success())
    }));
    install.assert_files_preserved();
    assert!(!install.run_value_exists());
    // An installer retry after startup removal remains idempotent.
    assert_ok(install.run(&args));
    install.assert_files_preserved();
}

#[test]
fn installer_uninstall_timeout_keeps_startup_and_files() {
    let install = TestInstall::new();
    let _listener = install.listener(true, 0);
    let out = install.run(&[
        "uninstall",
        "--installer",
        "--state-dir",
        install.state.to_str().unwrap(),
    ]);
    assert_eq!(out.status.code(), Some(1));
    assert!(String::from_utf8_lossy(&out.stderr).contains("did not exit"));
    install.assert_files_preserved();
    assert!(
        install.run_value_exists(),
        "stop failure aborts startup removal"
    );
}

#[test]
fn lifecycle_listener_helper() {
    let Ok(ready) = std::env::var("COACH_TEST_LISTENER_READY") else {
        return;
    };
    let _guard = InstanceGuard::acquire().expect("the namespace is unique to this test");
    std::fs::write(Path::new(&ready), "mutex acquired").unwrap();
    let delay: u64 = std::env::var("COACH_TEST_LISTENER_DELAY_MS")
        .unwrap()
        .parse()
        .unwrap();
    std::thread::sleep(Duration::from_millis(delay));
    let quit = QuitEvent::create().unwrap();
    let stubborn = std::env::var("COACH_TEST_LISTENER_STUBBORN").unwrap() == "1";
    while stubborn || !quit.is_set() {
        std::thread::sleep(Duration::from_millis(20));
    }
}

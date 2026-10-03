//! Python worker supervision (`worker-interface.md`): one process per job, JSON-RPC over stdin and
//! stdout, process-group kill on timeout or cancel, OOM detection, stderr ring.

use std::collections::BTreeMap;
use std::path::PathBuf;
use std::process::Stdio;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;

use serde_json::{Value, json};
use tokio::io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader};
use tokio::process::Command;
use tokio::sync::Notify;

use crate::config::WorkerConfig;

const STDERR_RING: usize = 1 << 20;

#[derive(Default)]
struct CancelInner {
    flag: AtomicBool,
    notify: Notify,
}

/// Cooperative cancel for one job.
#[derive(Clone, Default)]
pub struct Cancel(Arc<CancelInner>);

impl Cancel {
    pub fn cancel(&self) {
        self.0.flag.store(true, Ordering::SeqCst);
        self.0.notify.notify_waiters();
    }
    pub fn is_cancelled(&self) -> bool {
        self.0.flag.load(Ordering::SeqCst)
    }
    pub async fn cancelled(&self) {
        loop {
            let notified = self.0.notify.notified();
            if self.is_cancelled() {
                return;
            }
            notified.await;
        }
    }
}

#[derive(Debug, Clone)]
pub struct WorkerCall {
    pub tool: String,
    pub arguments: Value,
    /// `IRACING_COACH_IRACING_ROOT` (the job root).
    pub iracing_root: PathBuf,
    /// `IRACING_COACH_DATA` (the driver's archive).
    pub archive_root: PathBuf,
    /// `HOME` and `TMPDIR`.
    pub home: PathBuf,
    pub timeout: Duration,
    /// Set only for Garage61 jobs.
    pub garage61_token_file: Option<PathBuf>,
    /// Filled with the child pid at spawn so the caller can clean up after a kill.
    pub pid: Arc<std::sync::atomic::AtomicU32>,
}

#[derive(Debug, Clone, PartialEq)]
pub enum WorkerError {
    Timeout,
    Cancelled,
    Oom,
    Crash { code: Option<i32>, stderr: String },
    ToolError { error: String, message: String },
    Protocol(String),
    Spawn(String),
}

impl WorkerError {
    /// `jobs.error_code`.
    pub fn code(&self) -> &'static str {
        match self {
            WorkerError::Timeout => "timeout",
            WorkerError::Cancelled => "cancelled",
            WorkerError::Oom => "worker_oom",
            WorkerError::Crash { .. } | WorkerError::Spawn(_) => "worker_crash",
            WorkerError::ToolError { .. } => "tool_error",
            WorkerError::Protocol(_) => "worker_protocol",
        }
    }
    pub fn message(&self) -> String {
        match self {
            WorkerError::Timeout => "the worker did not answer in time and was stopped".into(),
            WorkerError::Cancelled => "cancelled".into(),
            WorkerError::Oom => "the worker was killed for memory".into(),
            WorkerError::Crash { code, stderr } => {
                format!(
                    "worker exited ({code:?}) before answering: {}",
                    tail(stderr, 600)
                )
            }
            WorkerError::ToolError { error, message } => format!("{error}: {message}"),
            WorkerError::Protocol(m) => m.clone(),
            WorkerError::Spawn(m) => m.clone(),
        }
    }
    /// True when the process was killed (its `*.<pid>.tmp` files must be cleaned).
    pub fn killed(&self) -> bool {
        matches!(
            self,
            WorkerError::Timeout
                | WorkerError::Cancelled
                | WorkerError::Oom
                | WorkerError::Crash { .. }
        )
    }
}

fn tail(text: &str, max: usize) -> String {
    let start = text.len().saturating_sub(max);
    let start = (start..text.len())
        .find(|i| text.is_char_boundary(*i))
        .unwrap_or(text.len());
    text[start..].to_string()
}

pub fn worker_env(call: &WorkerCall) -> BTreeMap<String, String> {
    let mut env = BTreeMap::new();
    let path = |p: &PathBuf| p.to_string_lossy().into_owned();
    env.insert(
        "IRACING_COACH_IRACING_ROOT".into(),
        path(&call.iracing_root),
    );
    env.insert("IRACING_COACH_DATA".into(), path(&call.archive_root));
    env.insert("IRACING_COACH_RAW_ARCHIVE".into(), "external".into());
    env.insert("IRACING_COACH_NATIVE_STAGES".into(), String::new());
    env.insert("PYTHONUTF8".into(), "1".into());
    env.insert("PYTHONDONTWRITEBYTECODE".into(), "1".into());
    env.insert("LANG".into(), "C.UTF-8".into());
    env.insert("HOME".into(), path(&call.home));
    env.insert("TMPDIR".into(), path(&call.home));
    env.insert("PATH".into(), "/usr/local/bin:/usr/bin:/bin".into());
    if let Some(token) = &call.garage61_token_file {
        env.insert("IRACING_COACH_GARAGE61_TOKEN_FILE".into(), path(token));
    }
    // Windows (dev only) needs a few variables for the interpreter to start.
    #[cfg(windows)]
    env.insert("USERPROFILE".into(), path(&call.home));
    #[cfg(windows)]
    for key in ["SystemRoot", "SYSTEMROOT", "PATH", "PATHEXT", "TEMP", "TMP"] {
        if let Ok(v) = std::env::var(key) {
            env.insert(key.into(), v);
        }
    }
    env
}

/// Runs one tool call in a fresh worker process.
pub async fn run_worker(
    cfg: &WorkerConfig,
    call: &WorkerCall,
    cancel: &Cancel,
    version: &str,
) -> Result<Value, WorkerError> {
    let Some(program) = cfg.command.first() else {
        return Err(WorkerError::Spawn("no worker command configured".into()));
    };
    std::fs::create_dir_all(&call.home).map_err(|e| WorkerError::Spawn(e.to_string()))?;
    let mut cmd = Command::new(program);
    cmd.args(&cfg.command[1..])
        .env_clear()
        .envs(worker_env(call))
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true);
    crate::sys::configure_child(&mut cmd, cfg.set_oom_score);
    let oom_before = crate::sys::cgroup_oom_kills();
    let mut child = cmd
        .spawn()
        .map_err(|e| WorkerError::Spawn(format!("spawn {program}: {e}")))?;
    let pid = child.id().unwrap_or(0);
    call.pid.store(pid, Ordering::SeqCst);

    let mut stdin = child.stdin.take().expect("stdin piped");
    let stdout = child.stdout.take().expect("stdout piped");
    let mut stderr = child.stderr.take().expect("stderr piped");

    let stderr_task = tokio::spawn(async move {
        let mut ring: Vec<u8> = Vec::new();
        let mut buf = [0u8; 8192];
        loop {
            match stderr.read(&mut buf).await {
                Ok(0) | Err(_) => break,
                Ok(n) => {
                    ring.extend_from_slice(&buf[..n]);
                    if ring.len() > STDERR_RING {
                        let drop = ring.len() - STDERR_RING;
                        ring.drain(..drop);
                    }
                }
            }
        }
        String::from_utf8_lossy(&ring).into_owned()
    });

    let requests = [
        json!({"jsonrpc":"2.0","id":1,"method":"initialize","params":{
            "protocolVersion":"2025-06-18","capabilities":{},
            "clientInfo":{"name":"coach-server","version":version}}}),
        json!({"jsonrpc":"2.0","method":"notifications/initialized"}),
        json!({"jsonrpc":"2.0","id":2,"method":"tools/call","params":{
            "name":call.tool,"arguments":call.arguments}}),
    ];
    let mut payload = String::new();
    for request in &requests {
        payload.push_str(&request.to_string());
        payload.push('\n');
    }

    // One deadline and one cancellation cover stdin delivery, the response and the exit: a
    // worker that stops reading, closes stdout without exiting, or never exits cannot hold the
    // lane or its memory reservation past the timeout.
    let mut response: Option<Value> = None;
    let mut status: Option<std::process::ExitStatus> = None;
    let exchange = async {
        // A worker that exits early closes the pipe; that is reported from its exit status below.
        let _ = stdin.write_all(payload.as_bytes()).await;
        let _ = stdin.shutdown().await;
        drop(stdin);
        let mut lines = BufReader::new(stdout).lines();
        while let Ok(Some(line)) = lines.next_line().await {
            let Ok(message) = serde_json::from_str::<Value>(line.trim()) else {
                continue;
            };
            if message["id"] == json!(2) {
                response = Some(message);
                break;
            }
        }
        if response.is_some() {
            // The process should exit on its own (stdin is closed); a lingering one is stopped
            // below once the answer is in hand.
            status = tokio::time::timeout(EXIT_LINGER, child.wait())
                .await
                .ok()
                .and_then(|r| r.ok());
        } else {
            status = child.wait().await.ok();
        }
    };
    enum End {
        Finished,
        Timeout,
        Cancelled,
    }
    let end = tokio::select! {
        _ = exchange => End::Finished,
        _ = tokio::time::sleep(call.timeout) => End::Timeout,
        _ = cancel.cancelled() => End::Cancelled,
    };

    let grace = cfg.kill_grace;
    if let Some(message) = &response {
        // The answer arrived: a process that has not exited is stopped, then it is parsed.
        if status.is_none() {
            kill_group(&mut child, pid, grace).await;
        }
        stderr_task.abort();
        return parse_response(message);
    }
    match end {
        End::Timeout => {
            kill_group(&mut child, pid, grace).await;
            stderr_task.abort();
            Err(WorkerError::Timeout)
        }
        End::Cancelled => {
            kill_group(&mut child, pid, grace).await;
            stderr_task.abort();
            Err(WorkerError::Cancelled)
        }
        End::Finished => {
            let stderr_text = tokio::time::timeout(Duration::from_secs(2), stderr_task)
                .await
                .ok()
                .and_then(|r| r.ok())
                .unwrap_or_default();
            let code = status.as_ref().and_then(|s| s.code());
            #[cfg(unix)]
            let signal = {
                use std::os::unix::process::ExitStatusExt;
                status.as_ref().and_then(|s| s.signal())
            };
            #[cfg(not(unix))]
            let signal: Option<i32> = None;
            if is_oom_exit(signal, oom_before, crate::sys::cgroup_oom_kills()) {
                return Err(WorkerError::Oom);
            }
            Err(WorkerError::Crash {
                code,
                stderr: stderr_text,
            })
        }
    }
}

/// How long a worker that has answered may take to exit before its group is stopped.
const EXIT_LINGER: Duration = Duration::from_secs(10);

/// SIGKILL together with a cgroup `oom_kill` increase is the kernel killing the worker for memory.
pub fn is_oom_exit(signal: Option<i32>, oom_before: u64, oom_after: u64) -> bool {
    signal == Some(9) && oom_after > oom_before
}

/// Stops the worker's whole process group: SIGTERM, then SIGKILL for every member still alive
/// when the grace ends, even if the leader itself has already exited (a descendant that ignores
/// SIGTERM must not outlive the job).
async fn kill_group(child: &mut tokio::process::Child, pid: u32, grace: Duration) {
    crate::sys::signal_group(pid, false);
    let deadline = tokio::time::Instant::now() + grace;
    loop {
        // reaps the leader as soon as it exits, so an empty group is seen as gone
        let _ = child.try_wait();
        if !crate::sys::group_alive(pid) {
            break;
        }
        if tokio::time::Instant::now() >= deadline {
            crate::sys::signal_group(pid, true);
            break;
        }
        tokio::time::sleep(Duration::from_millis(20)).await;
    }
    let _ = child.start_kill();
    let _ = child.wait().await;
}

fn parse_response(message: &Value) -> Result<Value, WorkerError> {
    if let Some(error) = message.get("error") {
        return Err(WorkerError::Protocol(format!("JSON-RPC error: {error}")));
    }
    let result = &message["result"];
    let text = result["content"][0]["text"]
        .as_str()
        .ok_or_else(|| WorkerError::Protocol("response has no content[0].text".into()))?;
    let value: Value = serde_json::from_str(text)
        .map_err(|e| WorkerError::Protocol(format!("tool result is not JSON: {e}")))?;
    if result["isError"].as_bool() == Some(true) {
        return Err(WorkerError::ToolError {
            error: value["error"].as_str().unwrap_or("tool_error").to_string(),
            message: value["message"].as_str().unwrap_or(text).to_string(),
        });
    }
    Ok(value)
}

/// After a killed worker: delete its `*.<pid>.tmp` files at once; `.write-*` and `.copy-*` only
/// when no other worker is running for that driver.
pub fn cleanup_after_kill(archive_root: &std::path::Path, pid: Option<u32>, driver_idle: bool) {
    fn walk(dir: &std::path::Path, pid: Option<u32>, driver_idle: bool) {
        let Ok(entries) = std::fs::read_dir(dir) else {
            return;
        };
        for entry in entries.flatten() {
            let path = entry.path();
            let name = entry.file_name().to_string_lossy().into_owned();
            if path.is_dir() {
                walk(&path, pid, driver_idle);
                continue;
            }
            let pid_tmp = pid.is_some_and(|p| name.ends_with(&format!(".{p}.tmp")));
            let stray = driver_idle && (name.starts_with(".write-") || name.starts_with(".copy-"));
            if pid_tmp || stray {
                let _ = std::fs::remove_file(&path);
            }
        }
    }
    walk(archive_root, pid, driver_idle);
}

/// Removes the `.write-*` and `.copy-*` temp files killed workers left under a driver's archive.
/// Only safe while no worker of that driver runs (their names are random, `storage.py`).
pub fn cleanup_strays(archive_root: &std::path::Path) -> usize {
    fn walk(dir: &std::path::Path) -> usize {
        let Ok(entries) = std::fs::read_dir(dir) else {
            return 0;
        };
        let mut removed = 0;
        for entry in entries.flatten() {
            let path = entry.path();
            let name = entry.file_name().to_string_lossy().into_owned();
            if path.is_dir() {
                removed += walk(&path);
            } else if (name.starts_with(".write-") || name.starts_with(".copy-"))
                && std::fs::remove_file(&path).is_ok()
            {
                removed += 1;
            }
        }
        removed
    }
    walk(archive_root)
}

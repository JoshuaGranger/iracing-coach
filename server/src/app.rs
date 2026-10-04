//! Shared application state and start-up.

use std::collections::HashMap;
use std::path::Path;
use std::sync::{Arc, Mutex};
use std::time::Instant;

use crate::adapters::{IbtParser, Projector, RecordingParser, ViewsProjector};
use crate::archiver::Archiver;
use crate::config::Config;
use crate::db::Store;
use crate::error::Result;
use crate::events::EventBus;
use crate::faults::Faults;
use crate::hub::Hub;
use crate::jobs::JobRuntime;

pub trait DiskProbe: Send + Sync {
    fn free_bytes(&self, path: &Path) -> u64;
}

pub struct SystemDisk;

impl DiskProbe for SystemDisk {
    fn free_bytes(&self, path: &Path) -> u64 {
        crate::sys::free_bytes(path).unwrap_or(u64::MAX / 2)
    }
}

/// Identity of the Python analyzer bundle the worker runs (idempotency key part, status).
#[derive(Debug, Clone, Default)]
pub struct WorkerIdentity {
    pub bundle_digest: String,
    pub profile_version: Option<String>,
}

impl WorkerIdentity {
    /// SHA-256 over the sorted `.py` files of the scripts directory, and the profile version
    /// text from `analysis_engine.py`.
    pub fn from_scripts(dir: Option<&Path>) -> WorkerIdentity {
        let Some(dir) = dir.filter(|d| d.is_dir()) else {
            return WorkerIdentity {
                bundle_digest: "unversioned".into(),
                profile_version: None,
            };
        };
        let mut names: Vec<_> = std::fs::read_dir(dir)
            .map(|rd| {
                rd.flatten()
                    .map(|e| e.path())
                    .filter(|p| p.extension().is_some_and(|x| x == "py"))
                    .collect()
            })
            .unwrap_or_default();
        names.sort();
        let mut hasher = sha2::Sha256::default();
        for path in &names {
            use sha2::Digest;
            hasher.update(path.file_name().unwrap_or_default().as_encoded_bytes());
            hasher.update([0]);
            if let Ok(bytes) = std::fs::read(path) {
                hasher.update(&bytes);
            }
            hasher.update([0]);
        }
        use sha2::Digest;
        let digest = crate::util::hex(&hasher.finalize())[..16].to_string();
        let profile = std::fs::read_to_string(dir.join("analysis_engine.py"))
            .ok()
            .and_then(|text| {
                text.lines()
                    .find_map(|l| l.strip_prefix("ANALYSIS_PROFILE_VERSION = "))
                    .map(|v| v.trim().trim_matches('"').to_string())
            });
        WorkerIdentity {
            bundle_digest: digest,
            profile_version: profile,
        }
    }
}

/// Version text of the worker's interpreter (`python3.12 -V`), or None when it cannot be run.
pub fn interpreter_version(command: &[String]) -> Option<String> {
    let program = command.iter().find(|part| {
        std::path::Path::new(part)
            .file_name()
            .and_then(|n| n.to_str())
            .is_some_and(|n| n.to_lowercase().starts_with("python"))
    })?;
    let output = std::process::Command::new(program)
        .arg("-V")
        .stdin(std::process::Stdio::null())
        .output()
        .ok()?;
    let text = String::from_utf8_lossy(if output.stdout.is_empty() {
        &output.stderr
    } else {
        &output.stdout
    })
    .trim()
    .to_string();
    text.strip_prefix("Python ").map(str::to_string)
}

/// Per-key async mutexes (per-hash upload locks, idempotency keys).
#[derive(Default)]
pub struct LockMap {
    locks: Mutex<HashMap<String, Arc<tokio::sync::Mutex<()>>>>,
}

impl LockMap {
    fn entry(&self, key: &str) -> Arc<tokio::sync::Mutex<()>> {
        let mut map = self.locks.lock().unwrap();
        if map.len() > 4096 {
            map.retain(|_, v| Arc::strong_count(v) > 1);
        }
        map.entry(key.to_string()).or_default().clone()
    }
    pub fn try_lock(&self, key: &str) -> Option<tokio::sync::OwnedMutexGuard<()>> {
        self.entry(key).try_lock_owned().ok()
    }
    pub async fn lock(&self, key: &str) -> tokio::sync::OwnedMutexGuard<()> {
        self.entry(key).lock_owned().await
    }
}

/// Spool admission (`collector-protocol.md` section 4): every reservation is checked and
/// registered under `gate`, so concurrent creates for different hashes cannot over-admit.
/// Open uploads hold their reservation in SQLite (`size - durable_offset`); a restage holds
/// its bytes here until its file is in `ready/` and counted.
#[derive(Default)]
pub struct SpoolLedger {
    pub gate: tokio::sync::Mutex<()>,
    restaging: Mutex<u64>,
}

impl SpoolLedger {
    pub fn restaging_bytes(&self) -> u64 {
        *self.restaging.lock().unwrap()
    }

    /// Registers `bytes` of restage reservation (caller holds `gate` and has checked admission).
    pub fn hold_restage(self: &Arc<Self>, bytes: u64) -> RestageReservation {
        *self.restaging.lock().unwrap() += bytes;
        RestageReservation {
            ledger: self.clone(),
            bytes,
        }
    }
}

/// Releases a restage reservation when dropped, on every path.
pub struct RestageReservation {
    ledger: Arc<SpoolLedger>,
    bytes: u64,
}

impl Drop for RestageReservation {
    fn drop(&mut self) {
        let mut held = self.ledger.restaging.lock().unwrap();
        *held = held.saturating_sub(self.bytes);
    }
}

pub struct App {
    pub cfg: Config,
    pub store: Arc<Store>,
    pub events: EventBus,
    pub faults: Arc<Faults>,
    pub archiver: Arc<Archiver>,
    pub parser: Arc<dyn RecordingParser>,
    pub projector: Arc<dyn Projector>,
    pub disk: Arc<dyn DiskProbe>,
    pub hub: Arc<Hub>,
    pub jobs: Arc<JobRuntime>,
    pub upload_locks: LockMap,
    pub idem_locks: LockMap,
    pub spool: Arc<SpoolLedger>,
    pub worker_identity: WorkerIdentity,
    /// Python MCP contracts that worker results are validated against.
    pub contracts: crate::schema::WorkerContracts,
    pub python_version: tokio::sync::OnceCell<Option<String>>,
    pub started: Instant,
}

pub type AppState = Arc<App>;

/// Replaceable parts (tests substitute fixtures for the library crates).
pub struct Parts {
    pub parser: Arc<dyn RecordingParser>,
    pub projector: Arc<dyn Projector>,
    pub disk: Arc<dyn DiskProbe>,
    pub faults: Arc<Faults>,
    pub worker_identity: Option<WorkerIdentity>,
}

impl Default for Parts {
    fn default() -> Self {
        Parts {
            parser: Arc::new(IbtParser),
            projector: Arc::new(ViewsProjector),
            disk: Arc::new(SystemDisk),
            faults: Arc::new(Faults::default()),
            worker_identity: None,
        }
    }
}

impl App {
    /// Opens the store, starts the archiver, reconciles setup identity and the spool against
    /// SQLite (before any listener opens) and starts the background loops when `cfg.background`
    /// is set.
    pub async fn start(cfg: Config, parts: Parts) -> Result<AppState> {
        cfg.ensure_dirs()?;
        if !cfg.contracts_dir.is_dir() {
            tracing::warn!(dir = %cfg.contracts_dir.display(), "COACH_CONTRACTS_DIR is not a directory: worker results cannot be validated and their jobs will fail");
        }
        let store = Arc::new(Store::open(&cfg.db_path(), &cfg.app_version)?);
        let archiver = Archiver::start(
            cfg.nas_root.clone(),
            parts.faults.clone(),
            cfg.nas_stall_after,
        );
        let worker_identity = parts
            .worker_identity
            .unwrap_or_else(|| WorkerIdentity::from_scripts(cfg.worker.scripts_dir.as_deref()));
        let app = Arc::new(App {
            store,
            events: EventBus::default(),
            faults: parts.faults,
            archiver,
            parser: parts.parser,
            projector: parts.projector,
            disk: parts.disk,
            hub: Arc::new(Hub::default()),
            jobs: Arc::new(JobRuntime::default()),
            upload_locks: LockMap::default(),
            idem_locks: LockMap::default(),
            spool: Arc::new(SpoolLedger::default()),
            worker_identity,
            contracts: crate::schema::WorkerContracts::new(cfg.contracts_dir.clone()),
            python_version: tokio::sync::OnceCell::new(),
            started: Instant::now(),
            cfg,
        });
        // Before anything stores an observation under the current rule (setup-keys.json).
        crate::setup_identity::reconcile_on_start(&app).await;
        crate::ingest::reconcile(&app).await?;
        crate::jobs::recover(&app).await?;
        if app.cfg.background {
            crate::jobs::spawn_lanes(&app);
            crate::scheduler::spawn(&app);
            crate::ingest::spawn_pending(&app);
        }
        Ok(app)
    }
}

//! Server configuration: directories, limits, worker command. Environment variables are read
//! once at start; `--dev` points every directory under a local folder.

use std::net::SocketAddr;
use std::path::PathBuf;
use std::time::Duration;

pub const GIB: u64 = 1 << 30;
pub const MIB: u64 = 1 << 20;

#[derive(Debug, Clone)]
pub struct WorkerConfig {
    /// Program and leading arguments, e.g. `nice -n 10 python3.12 -X utf8 -u .../mcp_server.py`.
    pub command: Vec<String>,
    /// Directory holding `mcp_server.py` (for the analyzer bundle digest and profile version).
    pub scripts_dir: Option<PathBuf>,
    pub analyze_timeout: Duration,
    pub discover_timeout: Duration,
    pub light_timeout: Duration,
    /// SIGTERM to SIGKILL grace.
    pub kill_grace: Duration,
    pub set_oom_score: bool,
}

#[derive(Debug, Clone)]
pub struct Config {
    pub bind: SocketAddr,
    pub dev: bool,
    pub data_dir: PathBuf,
    /// `$NAS`, e.g. `/nas/apps/iracing-coach`.
    pub nas_root: PathBuf,
    pub web_dir: Option<PathBuf>,
    pub worker: WorkerConfig,
    pub spool_limit_bytes: u64,
    pub min_free_bytes: u64,
    pub max_upload_bytes: u64,
    pub max_chunk_bytes: u64,
    /// Total memory the worker jobs may declare, with `server_reserve_bytes` added (3.7 GiB).
    pub memory_budget_bytes: u64,
    pub server_reserve_bytes: u64,
    /// Analysis admission ceiling (3.0 GiB).
    pub analysis_ceiling_bytes: u64,
    /// Seconds after which an idle `.part` is deleted and its reservation released.
    pub part_idle_secs: i64,
    /// Local-day offset from UTC in minutes for the 04:00 backup.
    pub tz_offset_minutes: i64,
    pub nas_stall_after: Duration,
    pub collector_idle: Duration,
    pub collector_ping: Duration,
    pub collector_hello_timeout: Duration,
    pub retry_after_secs: u32,
    /// How long a setup comparison waits for its job before answering 202 (10 s).
    pub inline_wait: Duration,
    /// Run background loops (job lanes, scheduler, archiver retries). Tests turn pieces off.
    pub background: bool,
    pub app_version: String,
}

impl Config {
    pub fn db_path(&self) -> PathBuf {
        self.data_dir.join("db").join("coach.sqlite3")
    }
    pub fn spool_incoming(&self) -> PathBuf {
        self.data_dir.join("spool").join("incoming")
    }
    pub fn spool_ready(&self) -> PathBuf {
        self.data_dir.join("spool").join("ready")
    }
    pub fn py_root(&self, customer_id: i64) -> PathBuf {
        self.data_dir.join("py").join(customer_id.to_string())
    }
    pub fn py_archive(&self, customer_id: i64) -> PathBuf {
        self.py_root(customer_id).join("archive")
    }
    pub fn job_root(&self, customer_id: i64, job_id: i64) -> PathBuf {
        self.py_root(customer_id)
            .join("jobs")
            .join(job_id.to_string())
    }
    pub fn views_dir(&self, analysis_id: &str, projection: u32) -> PathBuf {
        self.data_dir
            .join("views")
            .join(analysis_id)
            .join(format!("p{projection}"))
    }
    pub fn snapshots_dir(&self) -> PathBuf {
        self.data_dir.join("snapshots")
    }
    pub fn run_dir(&self) -> PathBuf {
        self.data_dir.join("run")
    }

    /// Directories the server needs to exist before it opens the database.
    pub fn ensure_dirs(&self) -> std::io::Result<()> {
        for dir in [
            self.data_dir.join("db"),
            self.spool_incoming(),
            self.spool_ready(),
            self.data_dir.join("py"),
            self.data_dir.join("views"),
            self.snapshots_dir(),
            self.run_dir(),
        ] {
            std::fs::create_dir_all(dir)?;
        }
        Ok(())
    }

    pub fn from_env(dev: bool) -> Result<Config, String> {
        let env = |name: &str| std::env::var(name).ok().filter(|v| !v.is_empty());
        let bind: SocketAddr = env("COACH_BIND")
            .unwrap_or_else(|| {
                if dev {
                    "127.0.0.1:8210"
                } else {
                    "0.0.0.0:8080"
                }
                .to_string()
            })
            .parse()
            .map_err(|e| format!("COACH_BIND: {e}"))?;
        let dev_root = PathBuf::from(env("COACH_DEV_DIR").unwrap_or_else(|| "dev-data".into()));
        let data_dir = env("COACH_DATA").map(PathBuf::from).unwrap_or_else(|| {
            if dev {
                dev_root.join("data")
            } else {
                "/data".into()
            }
        });
        let nas_root = env("COACH_NAS").map(PathBuf::from).unwrap_or_else(|| {
            if dev {
                dev_root.join("nas")
            } else {
                "/nas/apps/iracing-coach".into()
            }
        });
        let web_dir = env("COACH_WEB_DIR").map(PathBuf::from).or_else(|| {
            let default: PathBuf = if dev {
                "web/dist".into()
            } else {
                "/app/web/dist".into()
            };
            default.is_dir().then_some(default)
        });
        let scripts_dir = env("COACH_WORKER_SCRIPTS").map(PathBuf::from).or_else(|| {
            let default: PathBuf = if dev {
                "iracing-coach/skills/analyze-iracing-race/scripts".into()
            } else {
                "/app/iracing-coach/skills/analyze-iracing-race/scripts".into()
            };
            default.is_dir().then_some(default)
        });
        let python = env("COACH_PYTHON").unwrap_or_else(|| "python3.12".into());
        let command = match env("COACH_WORKER_COMMAND") {
            Some(text) => text.split_whitespace().map(str::to_string).collect(),
            None => {
                let script = scripts_dir
                    .clone()
                    .unwrap_or_else(|| {
                        "/app/iracing-coach/skills/analyze-iracing-race/scripts".into()
                    })
                    .join("mcp_server.py");
                let mut command: Vec<String> = Vec::new();
                if !dev {
                    command.extend(["nice".into(), "-n".into(), "10".into()]);
                }
                command.extend([python, "-X".into(), "utf8".into(), "-u".into()]);
                command.push(script.to_string_lossy().into_owned());
                command
            }
        };
        let mut config = Config::base(data_dir, nas_root, command);
        config.bind = bind;
        config.dev = dev;
        config.web_dir = web_dir;
        config.worker.scripts_dir = scripts_dir;
        config.worker.set_oom_score = !dev;
        if let Some(v) = env("COACH_SPOOL_LIMIT_BYTES").and_then(|v| v.parse().ok()) {
            config.spool_limit_bytes = v;
        }
        if let Some(v) = env("COACH_TZ_OFFSET_MINUTES").and_then(|v| v.parse().ok()) {
            config.tz_offset_minutes = v;
        }
        Ok(config)
    }

    /// Production defaults over the given directories.
    pub fn base(data_dir: PathBuf, nas_root: PathBuf, worker_command: Vec<String>) -> Config {
        Config {
            bind: "127.0.0.1:0".parse().unwrap(),
            dev: false,
            data_dir,
            nas_root,
            web_dir: None,
            worker: WorkerConfig {
                command: worker_command,
                scripts_dir: None,
                analyze_timeout: Duration::from_secs(600),
                discover_timeout: Duration::from_secs(120),
                light_timeout: Duration::from_secs(180),
                kill_grace: Duration::from_secs(5),
                set_oom_score: true,
            },
            spool_limit_bytes: 20 * GIB,
            min_free_bytes: 4 * GIB,
            max_upload_bytes: 4 * GIB,
            max_chunk_bytes: 8 * MIB,
            memory_budget_bytes: (3.7 * GIB as f64) as u64,
            server_reserve_bytes: (0.3 * GIB as f64) as u64,
            analysis_ceiling_bytes: 3 * GIB,
            part_idle_secs: 24 * 3600,
            tz_offset_minutes: 0,
            nas_stall_after: Duration::from_secs(30),
            collector_idle: Duration::from_secs(15),
            collector_ping: Duration::from_secs(15),
            collector_hello_timeout: Duration::from_secs(5),
            retry_after_secs: 30,
            inline_wait: Duration::from_secs(10),
            background: true,
            app_version: env!("CARGO_PKG_VERSION").to_string(),
        }
    }
}

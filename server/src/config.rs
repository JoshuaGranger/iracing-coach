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
    /// Directory holding the worker script (for the analyzer bundle digest and profile version).
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
    /// The Python MCP contracts that worker results are validated against.
    pub contracts_dir: PathBuf,
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
    /// A browser that stays behind this long is closed with 4008 (`collector-protocol.md` section 7).
    pub slow_browser: Duration,
    /// How long a restage waits for spool admission before it fails.
    pub restage_wait: Duration,
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

    /// Reads the environment of `contracts/server-config.md`; an empty value counts as unset.
    pub fn from_env(dev: bool) -> Result<Config, String> {
        Config::from_vars(dev, &|name| std::env::var(name).ok())
    }

    /// `from_env` over any variable source (the config test feeds the image environment).
    pub fn from_vars(dev: bool, source: &dyn Fn(&str) -> Option<String>) -> Result<Config, String> {
        let env = |name: &str| source(name).filter(|v| !v.is_empty());
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
        let data_dir = env("COACH_DATA_DIR").map(PathBuf::from).unwrap_or_else(|| {
            if dev {
                dev_root.join("data")
            } else {
                "/data".into()
            }
        });
        let nas_root = env("COACH_NAS_DIR").map(PathBuf::from).unwrap_or_else(|| {
            if dev {
                dev_root.join("nas")
            } else {
                "/nas/apps/iracing-coach".into()
            }
        });
        // A missing directory disables static hosting (the router checks).
        let web_dir: PathBuf = env("COACH_WEB_DIR").map(PathBuf::from).unwrap_or_else(|| {
            if dev {
                "web/dist".into()
            } else {
                "/app/web".into()
            }
        });
        let script: PathBuf = env("COACH_WORKER_SCRIPT")
            .map(PathBuf::from)
            .unwrap_or_else(|| {
                if dev {
                    "iracing-coach/skills/analyze-iracing-race/scripts/mcp_server.py".into()
                } else {
                    "/app/iracing-coach/skills/analyze-iracing-race/scripts/mcp_server.py".into()
                }
            });
        // The script's directory is where the analyzer digest and profile are read from.
        let scripts_dir = script
            .parent()
            .filter(|p| !p.as_os_str().is_empty())
            .map(PathBuf::from);
        let contracts_dir: PathBuf = env("COACH_CONTRACTS_DIR")
            .map(PathBuf::from)
            .unwrap_or_else(|| {
                if dev {
                    "contracts".into()
                } else {
                    "/app/contracts".into()
                }
            });
        let python = env("COACH_PYTHON").unwrap_or_else(|| {
            if dev {
                "python3.12".into()
            } else {
                "/usr/local/bin/python3.12".into()
            }
        });
        let command = match env("COACH_WORKER_COMMAND") {
            Some(text) => text.split_whitespace().map(str::to_string).collect(),
            None => {
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
        config.web_dir = Some(web_dir);
        config.contracts_dir = contracts_dir;
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
            contracts_dir: "contracts".into(),
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
            slow_browser: Duration::from_secs(10),
            restage_wait: Duration::from_secs(300),
            retry_after_secs: 30,
            inline_wait: Duration::from_secs(10),
            background: true,
            app_version: env!("CARGO_PKG_VERSION").to_string(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    fn vars(pairs: &[(&str, &str)]) -> impl Fn(&str) -> Option<String> {
        let map: HashMap<String, String> = pairs
            .iter()
            .map(|(k, v)| (k.to_string(), v.to_string()))
            .collect();
        move |name| map.get(name).cloned()
    }

    /// The image environment (`deploy/mini-pc/Dockerfile`, `contracts/server-config.md`).
    const IMAGE: &[(&str, &str)] = &[
        ("COACH_BIND", "0.0.0.0:8080"),
        ("COACH_DATA_DIR", "/data"),
        ("COACH_NAS_DIR", "/nas/apps/iracing-coach"),
        ("COACH_WEB_DIR", "/app/web"),
        ("COACH_PYTHON", "/usr/local/bin/python3.12"),
        (
            "COACH_WORKER_SCRIPT",
            "/app/iracing-coach/skills/analyze-iracing-race/scripts/mcp_server.py",
        ),
        ("COACH_CONTRACTS_DIR", "/app/contracts"),
    ];

    #[test]
    fn the_image_environment_sets_every_directory_and_the_worker() {
        let config = Config::from_vars(false, &vars(IMAGE)).unwrap();
        assert_eq!(config.bind, "0.0.0.0:8080".parse().unwrap());
        assert_eq!(config.data_dir, PathBuf::from("/data"));
        assert_eq!(config.nas_root, PathBuf::from("/nas/apps/iracing-coach"));
        assert_eq!(config.web_dir, Some(PathBuf::from("/app/web")));
        assert_eq!(config.contracts_dir, PathBuf::from("/app/contracts"));
        let script = "/app/iracing-coach/skills/analyze-iracing-race/scripts/mcp_server.py";
        assert_eq!(
            config.worker.scripts_dir,
            Some(PathBuf::from(
                "/app/iracing-coach/skills/analyze-iracing-race/scripts"
            ))
        );
        assert_eq!(
            config.worker.command,
            [
                "nice",
                "-n",
                "10",
                "/usr/local/bin/python3.12",
                "-X",
                "utf8",
                "-u",
                script
            ]
        );
        assert!(config.worker.set_oom_score);
    }

    #[test]
    fn production_defaults_equal_the_image_values() {
        let defaults = Config::from_vars(false, &vars(&[])).unwrap();
        let image = Config::from_vars(false, &vars(IMAGE)).unwrap();
        assert_eq!(defaults.bind, image.bind);
        assert_eq!(defaults.data_dir, image.data_dir);
        assert_eq!(defaults.nas_root, image.nas_root);
        assert_eq!(defaults.web_dir, image.web_dir);
        assert_eq!(defaults.contracts_dir, image.contracts_dir);
        assert_eq!(defaults.worker.command, image.worker.command);
        assert_eq!(defaults.worker.scripts_dir, image.worker.scripts_dir);
    }

    #[test]
    fn empty_values_count_as_unset_and_dev_uses_local_directories() {
        let config = Config::from_vars(
            true,
            &vars(&[("COACH_DATA_DIR", ""), ("COACH_WORKER_SCRIPT", "")]),
        )
        .unwrap();
        assert_eq!(config.bind, "127.0.0.1:8210".parse().unwrap());
        assert_eq!(config.data_dir, PathBuf::from("dev-data/data"));
        assert_eq!(config.nas_root, PathBuf::from("dev-data/nas"));
        assert_eq!(config.web_dir, Some(PathBuf::from("web/dist")));
        assert_eq!(config.contracts_dir, PathBuf::from("contracts"));
        assert_eq!(
            config.worker.scripts_dir,
            Some(PathBuf::from(
                "iracing-coach/skills/analyze-iracing-race/scripts"
            ))
        );
        assert_eq!(config.worker.command[0], "python3.12");
        let moved = Config::from_vars(true, &vars(&[("COACH_DEV_DIR", "elsewhere")])).unwrap();
        assert_eq!(moved.data_dir, PathBuf::from("elsewhere/data"));
        assert_eq!(moved.nas_root, PathBuf::from("elsewhere/nas"));
    }

    #[test]
    fn the_worker_command_override_and_optional_limits() {
        let config = Config::from_vars(
            false,
            &vars(&[
                ("COACH_WORKER_COMMAND", "python3 -u stub.py"),
                ("COACH_SPOOL_LIMIT_BYTES", "1234"),
                ("COACH_TZ_OFFSET_MINUTES", "-300"),
            ]),
        )
        .unwrap();
        assert_eq!(config.worker.command, ["python3", "-u", "stub.py"]);
        assert_eq!(config.spool_limit_bytes, 1234);
        assert_eq!(config.tz_offset_minutes, -300);
        assert!(Config::from_vars(false, &vars(&[("COACH_BIND", "nonsense")])).is_err());
    }
}

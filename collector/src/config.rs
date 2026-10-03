//! Collector state on the sim PC: `collector.json` (server address, collector id, placement,
//! cached layout) and the folder layout. Rebuildable except the placement.

use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::util::uuid_v4;

pub const DEFAULT_SERVER: &str = "http://192.168.1.81:8210";
pub const CONFIG_FILE: &str = "collector.json";

/// Machine-local overlay placement. Never synced to the server.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Placement {
    pub left: Option<i32>,
    pub top: Option<i32>,
    pub scale: f64,
    pub monitor: Option<String>,
}

impl Default for Placement {
    fn default() -> Self {
        Placement {
            left: None,
            top: None,
            scale: 1.0,
            monitor: None,
        }
    }
}

/// The last acknowledged live layout, kept so the overlay draws the right tiles before the server
/// answers.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CachedLayout {
    pub layout_id: String,
    pub version: i64,
    pub layout: Value,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct Config {
    pub collector_id: String,
    pub server: String,
    pub placement: Placement,
    pub monitor_visible: bool,
    pub safe_glance: bool,
    /// `ctrl+shift+L` style text; empty = no hotkey.
    pub hotkey: String,
    pub layout: Option<CachedLayout>,
}

impl Default for Config {
    fn default() -> Self {
        Config {
            collector_id: uuid_v4(),
            server: DEFAULT_SERVER.to_string(),
            placement: Placement::default(),
            monitor_visible: true,
            safe_glance: true,
            hotkey: String::new(),
            layout: None,
        }
    }
}

impl Config {
    /// Loads `collector.json` from `state_dir`. A missing or unreadable file yields defaults with a
    /// fresh collector id (not written until `save`); a file without an id gets one.
    pub fn load(state_dir: &Path) -> Config {
        let path = state_dir.join(CONFIG_FILE);
        let mut cfg: Config = fs::read_to_string(&path)
            .ok()
            .and_then(|text| serde_json::from_str(text.trim_start_matches('\u{feff}')).ok())
            .unwrap_or_default();
        if uuid_like(&cfg.collector_id).is_none() {
            cfg.collector_id = uuid_v4();
        }
        if let Ok(server) = normalize_server(&cfg.server) {
            cfg.server = server;
        } else {
            cfg.server = DEFAULT_SERVER.to_string();
        }
        cfg
    }

    /// Writes through a temp file and a rename so a crash never leaves a torn file.
    pub fn save(&self, state_dir: &Path) -> std::io::Result<()> {
        fs::create_dir_all(state_dir)?;
        let path = state_dir.join(CONFIG_FILE);
        let tmp = state_dir.join(format!("{CONFIG_FILE}.tmp"));
        let text = serde_json::to_string_pretty(self).map_err(std::io::Error::other)?;
        {
            let mut f = fs::File::create(&tmp)?;
            f.write_all(text.as_bytes())?;
            f.sync_all()?;
        }
        fs::rename(&tmp, &path)
    }
}

fn uuid_like(s: &str) -> Option<()> {
    let b = s.as_bytes();
    (b.len() == 36 && [8, 13, 18, 23].iter().all(|&i| b[i] == b'-')).then_some(())
}

/// Where the collector keeps its state and finds iRacing's files.
#[derive(Debug, Clone)]
pub struct Paths {
    /// `%LOCALAPPDATA%\iRacingCoach`.
    pub state_dir: PathBuf,
    /// `Documents\iRacing` (telemetry and setups are below it).
    pub iracing_root: PathBuf,
}

impl Paths {
    pub fn telemetry_dir(&self) -> PathBuf {
        self.iracing_root.join("telemetry")
    }
    pub fn setups_dir(&self) -> PathBuf {
        self.iracing_root.join("setups")
    }
    pub fn journal_path(&self) -> PathBuf {
        self.state_dir.join("journal.jsonl")
    }
    pub fn logs_dir(&self) -> PathBuf {
        self.state_dir.join("logs")
    }
    pub fn dry_run_log(&self) -> PathBuf {
        self.state_dir.join("dry-run.jsonl")
    }
    pub fn default_paths() -> Paths {
        Paths {
            state_dir: default_state_dir(),
            iracing_root: default_documents_dir().join("iRacing"),
        }
    }
}

fn default_state_dir() -> PathBuf {
    if let Some(local) = std::env::var_os("LOCALAPPDATA") {
        return PathBuf::from(local).join("iRacingCoach");
    }
    if let Some(home) = std::env::var_os("HOME") {
        return PathBuf::from(home).join(".local/share/iRacingCoach");
    }
    PathBuf::from("iRacingCoach-state")
}

fn default_documents_dir() -> PathBuf {
    #[cfg(windows)]
    if let Some(dir) = crate::win::known_folder_documents() {
        return dir;
    }
    if let Some(profile) = std::env::var_os("USERPROFILE").or_else(|| std::env::var_os("HOME")) {
        return PathBuf::from(profile).join("Documents");
    }
    PathBuf::from("Documents")
}

#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
#[error("{0}")]
pub struct ServerAddressError(pub String);

/// Accepts `192.168.1.81:8210`, `http://host:port` and `http://host:port/`; returns
/// `http://host[:port]`. Only plain http is supported (D7).
pub fn normalize_server(input: &str) -> Result<String, ServerAddressError> {
    let trimmed = input.trim().trim_end_matches('/');
    let rest = match trimmed.split_once("://") {
        Some(("http", rest)) => rest,
        Some((scheme, _)) => {
            return Err(ServerAddressError(format!(
                "unsupported scheme {scheme}: the server speaks plain http"
            )));
        }
        None => trimmed,
    };
    if rest.is_empty() || rest.contains('/') || rest.contains(char::is_whitespace) {
        return Err(ServerAddressError(format!(
            "not a server address: {input:?}"
        )));
    }
    let (host, port) = split_host_port(rest)?;
    Ok(match port {
        Some(p) => format!("http://{host}:{p}"),
        None => format!("http://{host}"),
    })
}

fn split_host_port(rest: &str) -> Result<(String, Option<u16>), ServerAddressError> {
    if let Some(bracketed) = rest.strip_prefix('[') {
        let (host, tail) = bracketed
            .split_once(']')
            .ok_or_else(|| ServerAddressError(format!("unclosed [ in {rest:?}")))?;
        let port = match tail.strip_prefix(':') {
            Some(p) => Some(parse_port(p)?),
            None if tail.is_empty() => None,
            None => {
                return Err(ServerAddressError(format!(
                    "not a server address: {rest:?}"
                )));
            }
        };
        return Ok((format!("[{host}]"), port));
    }
    match rest.rsplit_once(':') {
        Some((host, p)) if !host.is_empty() && !host.contains(':') => {
            Ok((host.to_string(), Some(parse_port(p)?)))
        }
        Some(_) => Err(ServerAddressError(format!(
            "not a server address: {rest:?}"
        ))),
        None => Ok((rest.to_string(), None)),
    }
}

fn parse_port(p: &str) -> Result<u16, ServerAddressError> {
    p.parse::<u16>()
        .ok()
        .filter(|&n| n != 0)
        .ok_or_else(|| ServerAddressError(format!("bad port {p:?}")))
}

/// `host:port` for a TCP connection to a normalized server address.
pub fn server_authority(server: &str) -> String {
    let rest = server.strip_prefix("http://").unwrap_or(server);
    let has_port = if rest.starts_with('[') {
        rest.contains("]:")
    } else {
        rest.contains(':')
    };
    if has_port {
        rest.to_string()
    } else {
        format!("{rest}:80")
    }
}

/// `ws://host:port/ws/collector` for a normalized server address.
pub fn collector_ws_url(server: &str) -> String {
    format!(
        "ws://{}/ws/collector",
        server.strip_prefix("http://").unwrap_or(server)
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn server_addresses_normalize() {
        assert_eq!(
            normalize_server("192.168.1.81:8210").unwrap(),
            "http://192.168.1.81:8210"
        );
        assert_eq!(
            normalize_server(" http://minipc.tail4e6ca6.ts.net:8210/ ").unwrap(),
            "http://minipc.tail4e6ca6.ts.net:8210"
        );
        assert_eq!(normalize_server("host").unwrap(), "http://host");
        assert_eq!(normalize_server("[::1]:80").unwrap(), "http://[::1]:80");
        assert!(normalize_server("https://x:1").is_err());
        assert!(normalize_server("http://x/path").is_err());
        assert!(normalize_server("x:0").is_err());
        assert!(normalize_server("").is_err());
    }

    #[test]
    fn authority_and_ws_url() {
        assert_eq!(server_authority("http://a:8210"), "a:8210");
        assert_eq!(server_authority("http://a"), "a:80");
        assert_eq!(server_authority("http://[::1]:9"), "[::1]:9");
        assert_eq!(
            collector_ws_url("http://a:8210"),
            "ws://a:8210/ws/collector"
        );
    }

    #[test]
    fn config_round_trips_and_repairs() {
        let dir = std::env::temp_dir().join(format!("coach-cfg-{}", uuid_v4()));
        let mut cfg = Config::load(&dir);
        assert_eq!(cfg.server, DEFAULT_SERVER);
        cfg.placement.left = Some(40);
        cfg.hotkey = "ctrl+shift+L".into();
        cfg.save(&dir).unwrap();
        let back = Config::load(&dir);
        assert_eq!(back, cfg);
        fs::write(dir.join(CONFIG_FILE), r#"{"server": "10.0.0.2:9"}"#).unwrap();
        let repaired = Config::load(&dir);
        assert_eq!(repaired.server, "http://10.0.0.2:9");
        assert_eq!(repaired.collector_id.len(), 36);
        fs::remove_dir_all(&dir).unwrap();
    }
}

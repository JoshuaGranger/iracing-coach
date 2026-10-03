//! File logging: `logs\collector.log`, rotated at 5 MB, one previous file kept (two files of
//! 5 MB at most). Log lines are the only disk writes the collector makes on track besides garage
//! journal entries, so the level defaults to `info` and the hot paths never log.
//!
//! A small logger of its own (the `info!`, `warn!`, `error!` and `debug!` macros of this crate)
//! instead of `tracing`: the executable is held to 3 MB and the subscriber stack costs more than
//! everything else the collector logs with.

use std::fmt;
use std::fs::{self, File, OpenOptions};
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, OnceLock};

use crate::util::{iso_utc, unix_ms_now};

pub const MAX_LOG_BYTES: u64 = 5 * 1024 * 1024;

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum Level {
    Error = 1,
    Warn,
    Info,
    Debug,
}

impl Level {
    fn label(self) -> &'static str {
        match self {
            Level::Error => "ERROR",
            Level::Warn => "WARN ",
            Level::Info => "INFO ",
            Level::Debug => "DEBUG",
        }
    }
}

#[derive(Clone)]
pub struct RotatingLog {
    inner: Arc<Mutex<Inner>>,
}

struct Inner {
    path: PathBuf,
    file: Option<File>,
    written: u64,
    max: u64,
}

impl RotatingLog {
    pub fn open(dir: &Path, max_bytes: u64) -> io::Result<RotatingLog> {
        fs::create_dir_all(dir)?;
        let path = dir.join("collector.log");
        let written = fs::metadata(&path).map(|m| m.len()).unwrap_or(0);
        let file = OpenOptions::new().create(true).append(true).open(&path)?;
        Ok(RotatingLog {
            inner: Arc::new(Mutex::new(Inner {
                path,
                file: Some(file),
                written,
                max: max_bytes,
            })),
        })
    }

    pub fn writer(&self) -> LogWriter {
        LogWriter(self.inner.clone())
    }
}

impl Inner {
    fn rotate(&mut self) -> io::Result<()> {
        self.file = None;
        let previous = self.path.with_file_name("collector.log.1");
        let _ = fs::remove_file(&previous);
        fs::rename(&self.path, &previous)?;
        self.file = Some(
            OpenOptions::new()
                .create(true)
                .append(true)
                .open(&self.path)?,
        );
        self.written = 0;
        Ok(())
    }
}

pub struct LogWriter(Arc<Mutex<Inner>>);

impl Write for LogWriter {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        let mut inner = self.0.lock().unwrap_or_else(|e| e.into_inner());
        if inner.written + buf.len() as u64 > inner.max && inner.written > 0 {
            inner.rotate()?;
        }
        let file = inner
            .file
            .as_mut()
            .ok_or_else(|| io::Error::other("log file closed"))?;
        file.write_all(buf)?;
        inner.written += buf.len() as u64;
        Ok(buf.len())
    }

    fn flush(&mut self) -> io::Result<()> {
        let mut inner = self.0.lock().unwrap_or_else(|e| e.into_inner());
        match inner.file.as_mut() {
            Some(f) => f.flush(),
            None => Ok(()),
        }
    }
}

enum Sink {
    File(LogWriter),
    Stderr,
}

struct Logger {
    level: Level,
    sink: Mutex<Sink>,
}

static LOGGER: OnceLock<Logger> = OnceLock::new();

/// Installs the process logger; the first call wins, later ones are ignored.
pub fn init(level: Level, file: Option<&RotatingLog>) {
    let sink = match file {
        Some(log) => Sink::File(log.writer()),
        None => Sink::Stderr,
    };
    let _ = LOGGER.set(Logger {
        level,
        sink: Mutex::new(sink),
    });
}

/// Writes one line when a logger is installed and `level` is enabled.
pub fn log(level: Level, args: fmt::Arguments<'_>) {
    let Some(logger) = LOGGER.get() else {
        return;
    };
    if level > logger.level {
        return;
    }
    let thread = std::thread::current();
    let line = format!(
        "{} {} [{}] {args}\n",
        iso_utc(unix_ms_now()),
        level.label(),
        thread.name().unwrap_or("-"),
    );
    let mut sink = logger.sink.lock().unwrap_or_else(|e| e.into_inner());
    match &mut *sink {
        Sink::File(w) => {
            let _ = w.write_all(line.as_bytes());
        }
        Sink::Stderr => {
            let _ = io::stderr().write_all(line.as_bytes());
        }
    }
}

#[macro_export]
macro_rules! error {
    ($($arg:tt)*) => { $crate::logging::log($crate::logging::Level::Error, format_args!($($arg)*)) };
}

#[macro_export]
macro_rules! warn {
    ($($arg:tt)*) => { $crate::logging::log($crate::logging::Level::Warn, format_args!($($arg)*)) };
}

#[macro_export]
macro_rules! info {
    ($($arg:tt)*) => { $crate::logging::log($crate::logging::Level::Info, format_args!($($arg)*)) };
}

#[macro_export]
macro_rules! debug {
    ($($arg:tt)*) => { $crate::logging::log($crate::logging::Level::Debug, format_args!($($arg)*)) };
}

/// The log level from `COACH_LOG` or `RUST_LOG`: a bare level (`debug`) or directives such as
/// `coach_collector=debug,warn`, of which the most verbose level wins. Default `info`.
pub fn level_from_env() -> Level {
    let text = std::env::var("COACH_LOG")
        .or_else(|_| std::env::var("RUST_LOG"))
        .unwrap_or_default();
    parse_level(&text).unwrap_or(Level::Info)
}

fn parse_level(text: &str) -> Option<Level> {
    text.split(',')
        .filter_map(|d| {
            let level = d.rsplit('=').next()?.trim().to_ascii_lowercase();
            match level.as_str() {
                "error" => Some(Level::Error),
                "warn" => Some(Level::Warn),
                "info" => Some(Level::Info),
                "debug" | "trace" => Some(Level::Debug),
                _ => None,
            }
        })
        .max()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::util::uuid_v4;

    #[test]
    fn levels_parse_from_directives() {
        assert_eq!(parse_level("debug"), Some(Level::Debug));
        assert_eq!(
            parse_level("coach_collector=trace,warn"),
            Some(Level::Debug)
        );
        assert_eq!(parse_level("WARN"), Some(Level::Warn));
        assert_eq!(parse_level(""), None);
        assert_eq!(parse_level("nonsense"), None);
    }

    #[test]
    fn rotates_and_keeps_one_previous_file() {
        let dir = std::env::temp_dir().join(format!("coach-log-{}", uuid_v4()));
        let log = RotatingLog::open(&dir, 100).unwrap();
        let mut w = log.writer();
        for i in 0..20 {
            writeln!(w, "line number {i:04} padded to a fixed width......").unwrap();
        }
        w.flush().unwrap();
        let current = fs::metadata(dir.join("collector.log")).unwrap().len();
        let previous = fs::metadata(dir.join("collector.log.1")).unwrap().len();
        assert!(current <= 100 && previous <= 100, "{current} {previous}");
        assert!(!dir.join("collector.log.2").exists());
        fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn a_line_carries_time_level_thread_and_message() {
        let dir = std::env::temp_dir().join(format!("coach-log-{}", uuid_v4()));
        let log = RotatingLog::open(&dir, MAX_LOG_BYTES).unwrap();
        init(Level::Info, Some(&log));
        // The first `init` in the process wins; check the line only when it was ours.
        crate::info!("hello {}", 7);
        crate::debug!("not written at info");
        let text = fs::read_to_string(dir.join("collector.log")).unwrap_or_default();
        if !text.is_empty() {
            assert!(text.contains(" INFO  ["), "{text}");
            assert!(text.contains("] hello 7\n"), "{text}");
            assert!(!text.contains("not written"));
        }
        let _ = fs::remove_dir_all(&dir);
    }
}

//! Reads one `analysis.json` and writes the bounded view files the server serves. Copies backend
//! values; computes no telemetry math. Public API: `docs/08-rewrite/contracts/crate-api.md` section 5.
#![forbid(unsafe_code)]
// Scaffold stubs: remove this allow as the bodies are implemented.
#![allow(unused_variables)]

use std::path::{Path, PathBuf};

pub const PROJECTION_VERSION: u32 = 1;

/// Writes every view file into `out_dir` (`/data/views/<analysis_id>/p<PROJECTION_VERSION>/`),
/// which must not exist: files go to a sibling `<out_dir>.tmp-<random>` that is fsynced and
/// renamed; on error the temp directory is removed and nothing is left at `out_dir`.
pub fn project(analysis_json: &Path, out_dir: &Path) -> Result<ProjectionSummary, ProjectError> {
    unimplemented!()
}

#[derive(Debug, Clone, PartialEq, serde::Serialize)]
pub struct ProjectionSummary {
    /// `analysis.json` `analysis_id`.
    pub analysis_id: String,
    pub projection_version: u32,
    /// `analysis_view.schema_version`.
    pub schema_version: i64,
    /// `analysis_view.analysis_profile_version`.
    pub profile_version: String,
    /// `laps[]` length.
    pub lap_count: u32,
    /// `lap_traces` distance bins (160 today).
    pub bins_per_lap: u32,
    /// `TraceSignal` ids in catalog order.
    pub trace_channels: Vec<String>,
    /// Files actually written.
    pub files: Vec<ViewFile>,
    /// Numbers replaced by null.
    pub nonfinite_nulled: u64,
    pub elapsed_ms: u64,
}

#[derive(Debug, Clone, PartialEq, serde::Serialize)]
pub struct ViewFile {
    pub kind: ViewKind,
    pub file_name: String,
    pub bytes: u64,
    pub sha256: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ViewKind {
    Race,
    Runs,
    Investigation,
    TraceCatalog,
    Traces,
    Track,
    Tuning,
}

/// Serves `GET /api/v1/analyses/{id}/traces`: reads `traces.ccol` and `traces-catalog.json` from
/// `view_dir`, parses the query values exactly as received (`laps` "1-40,52", `channels` comma
/// ids; None = all laps / the catalog's default set) and returns a CCOL body holding only the
/// selection.
pub fn select_traces(
    view_dir: &Path,
    laps: Option<&str>,
    channels: Option<&str>,
    max_bytes: u64,
) -> Result<Vec<u8>, SelectError> {
    unimplemented!()
}

#[derive(Debug, thiserror::Error)]
pub enum ProjectError {
    #[error("I/O: {0}")]
    Io(#[from] std::io::Error),
    #[error("malformed analysis.json at {path}: {message}")]
    Json { path: String, message: String },
    #[error("missing required field {0}")]
    MissingField(String),
    #[error("{field} {found} is not supported (supported: {supported})")]
    IncompatibleVersion {
        field: &'static str,
        found: String,
        supported: String,
    },
    #[error("{kind:?} view is {bytes} bytes, bound {limit}")]
    TooLarge {
        kind: ViewKind,
        bytes: u64,
        limit: u64,
    },
    #[error("{0} already exists")]
    OutputExists(PathBuf),
}

#[derive(Debug, thiserror::Error)]
pub enum SelectError {
    #[error("I/O: {0}")]
    Io(#[from] std::io::Error),
    /// Server: 400 `invalid_request`.
    #[error("invalid laps selection: {0}")]
    BadLaps(String),
    /// 400 `invalid_request`.
    #[error("unknown channel {0}")]
    UnknownChannel(String),
    /// 413.
    #[error("selection is {bytes} bytes, bound {limit}")]
    TooLarge { bytes: u64, limit: u64 },
    /// 500; the server re-projects.
    #[error("corrupt view file: {0}")]
    Corrupt(String),
}

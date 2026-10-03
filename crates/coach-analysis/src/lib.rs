//! Native analysis stages that replace stages of the Python worker, each gated on
//! byte-identical output against the Python oracle (owner decision D2).
//!
//! Phase 1 (this crate today): the IBT decode stage. [`decode::decode`] is
//! `ibt_reader.load_telemetry`'s sample decode; [`store`] writes it as a coach column store,
//! which the worker reads when `IRACING_COACH_NATIVE_STAGES` names `decode`
//! (`docs/08-rewrite/contracts/worker-interface.md` section 7,
//! `docs/08-rewrite/port/phase1-decode.md`). Header, variable table and bounds come from
//! `coach_ibt`; nothing here parses them again.
//!
//! Phase 2: [`kernels`], the per-sample analysis kernels over the store
//! (`docs/08-rewrite/port/phase2-plan.md`).
#![forbid(unsafe_code)]

pub mod decode;
pub mod golden;
pub mod kernels;
pub mod request;
pub mod sampling;
pub mod store;

pub use decode::{Column, Decoded, Element, SourceLayout, Value, decode, select_variables};
pub use request::{DecodeRequest, REQUEST_VERSION};
pub use sampling::SamplingPlan;
pub use store::{ColumnStore, FORMAT_VERSION, StoreHeader};

#[derive(Debug, thiserror::Error)]
pub enum DecodeError {
    #[error(transparent)]
    Ibt(#[from] coach_ibt::IbtError),
    #[error("I/O: {0}")]
    Io(#[from] std::io::Error),
    /// Python `IbtChannelError` (`_normalise_channels`).
    #[error("requested channel(s) not present: {missing}; available channels begin: {preview}")]
    Channel { missing: String, preview: String },
    /// Python `IbtChannelError` for an empty name.
    #[error("channel names must be non-empty strings")]
    EmptyChannelName,
    /// Python `ValueError` from `_sampling_plan`.
    #[error("target_hz must be a positive finite number or None")]
    TargetHz,
    /// Python `OverflowError` from `int(position)` in `_iter_sample_indices` when the step is
    /// infinite (a positive target below about `tick_rate / f64::MAX`).
    #[error("cannot convert float infinity to integer")]
    SampleIndexOverflow,
    #[error("request: {0}")]
    Request(String),
    #[error("column store: {0}")]
    Store(String),
}

impl DecodeError {
    /// The exception class `ibt_reader.load_telemetry` raises for the same input, so the parity
    /// gate compares rejections by category (`coach-analysis golden`); None for a failure the
    /// oracle has no counterpart for (a bad request or store). `coach_ibt`'s errors map as in
    /// `crates/coach-ibt/tests/parity.rs`.
    pub fn python_class(&self) -> Option<&'static str> {
        fn io_class(error: &std::io::Error) -> &'static str {
            match error.kind() {
                std::io::ErrorKind::NotFound => "FileNotFoundError",
                std::io::ErrorKind::PermissionDenied => "PermissionError",
                _ => "OSError",
            }
        }
        Some(match self {
            DecodeError::Ibt(error) => match error {
                coach_ibt::IbtError::Io(error) => io_class(error),
                coach_ibt::IbtError::Truncated { .. } => "IbtTruncatedError",
                coach_ibt::IbtError::Bounds { .. } => "IbtBoundsError",
                coach_ibt::IbtError::UnsupportedVarType { .. } => "IbtTypeError",
                coach_ibt::IbtError::Yaml { .. } => "IbtYamlError",
                coach_ibt::IbtError::Format(_) => "IbtFormatError",
            },
            DecodeError::Io(error) => io_class(error),
            DecodeError::Channel { .. } | DecodeError::EmptyChannelName => "IbtChannelError",
            DecodeError::TargetHz => "ValueError",
            DecodeError::SampleIndexOverflow => "OverflowError",
            DecodeError::Request(_) | DecodeError::Store(_) => return None,
        })
    }
}

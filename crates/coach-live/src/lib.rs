//! The live engine: one `Sample` per SDK tick in, one `LiveState` out; the live frame codec and
//! tile formatting. Public API: `docs/08-rewrite/contracts/crate-api.md` section 4.
//!
//! Behaviour is a port of the C# live monitor (`LiveTelemetry.cs`, `LiveTruthPolicy.cs`,
//! `LiveTelemetryCatalog.cs`, `LiveSteering.cs` and the reader normalisations of
//! `IRacingSdkTelemetrySource.cs`); the vectors under `tests/vectors/` pin it.
#![forbid(unsafe_code)]

mod engine;
mod format;
mod frame;
mod sample;
mod session;
mod state;
mod tiles;
mod truth;

pub use engine::{Engine, EngineConfig};
pub use frame::{
    ChannelValue, DecodedFrame, FRAME_FIXED_HEADER_LEN, FRAME_MAGIC, FRAME_VERSION, FrameError,
    FrameHeader, decode_frame, parse_header, set_backlog_flag,
};
pub use sample::Sample;
pub use session::SessionContext;
pub use state::{CueState, LiveState};
pub use tiles::{TileState, TileText, cue_text, display_range, format_tile};

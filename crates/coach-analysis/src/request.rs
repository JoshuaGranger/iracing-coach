//! The decode request the Python seam writes to `coach-analysis decode` on standard input
//! (`worker-interface.md` section 7):
//!
//! ```json
//! {"version": 1, "source": "<ibt path>", "out": "<store path>",
//!  "channels": ["Speed", ...] | null, "target_hz": "<repr(float(target_hz))>" | null}
//! ```
//!
//! `target_hz` travels as Python's `repr` text so its value parses exactly; null is native
//! rate, and `channels` null decodes every variable (`load_telemetry(channels=None)`).

use std::path::PathBuf;
use std::time::Instant;

use serde::{Deserialize, Serialize};

use crate::{DecodeError, decode, store};

pub const REQUEST_VERSION: u32 = 1;

#[derive(Debug, Clone, PartialEq, Deserialize)]
pub struct DecodeRequest {
    pub version: u32,
    pub source: PathBuf,
    pub out: PathBuf,
    #[serde(default)]
    pub channels: Option<Vec<String>>,
    #[serde(default)]
    pub target_hz: Option<String>,
}

/// One line of JSON on standard output after a successful decode.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct DecodeSummary {
    pub ok: bool,
    pub format_version: u32,
    pub sample_count: usize,
    pub columns: usize,
    pub bytes: u64,
    pub decode_ms: f64,
    pub write_ms: f64,
}

impl DecodeRequest {
    pub fn parse(text: &str) -> Result<DecodeRequest, DecodeError> {
        let request: DecodeRequest =
            serde_json::from_str(text).map_err(|error| DecodeError::Request(error.to_string()))?;
        if request.version != REQUEST_VERSION {
            return Err(DecodeError::Request(format!(
                "request version {} is not supported (expected {REQUEST_VERSION})",
                request.version
            )));
        }
        Ok(request)
    }

    /// `target_hz` as a float; text that is not a float is the Python `ValueError`.
    pub fn target_hz(&self) -> Result<Option<f64>, DecodeError> {
        self.target_hz
            .as_deref()
            .map(|text| {
                text.trim()
                    .parse::<f64>()
                    .map_err(|_| DecodeError::TargetHz)
            })
            .transpose()
    }

    pub fn run(&self) -> Result<DecodeSummary, DecodeError> {
        let started = Instant::now();
        let decoded = decode::decode(&self.source, self.channels.as_deref(), self.target_hz()?)?;
        let decoded_at = Instant::now();
        let bytes = store::write(&decoded, &self.out)?;
        Ok(DecodeSummary {
            ok: true,
            format_version: store::FORMAT_VERSION,
            sample_count: decoded.sample_indices.len(),
            columns: decoded.columns.len(),
            bytes,
            decode_ms: (decoded_at - started).as_secs_f64() * 1000.0,
            write_ms: decoded_at.elapsed().as_secs_f64() * 1000.0,
        })
    }
}

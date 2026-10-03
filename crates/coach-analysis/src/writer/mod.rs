//! Port phase 3 (boundary D, `contracts/worker-interface.md` section 7): the native
//! `analysis.json` writer. The worker hands its final analysis object to
//! `coach-analysis write`, which writes exactly the bytes CPython 3.12's
//! `json.dumps(value, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=...,
//! allow_nan=...)` plus the terminator would encode to UTF-8 (`docs/08-rewrite/port/phase3-writer.md`).
//!
//! Standard input: one JSON request line, then the value as a pickle (protocol 5, written by
//! `native_stages._WriterPickler`). Standard output: one JSON answer line, then for
//! `"output": "bytes"` exactly `length` bytes.
//!
//! ```text
//! request  {"version": 1, "ensure_ascii": bool, "allow_nan": bool, "sort_keys": true,
//!           "separators": [",", ":"], "indent": null, "terminator": "" | "\n" | "\r\n",
//!           "output": "bytes" | "file" | "sha256", "path": "<file>" (file only),
//!           "marker": ["<module>", "<qualname>"], "max_str_digits": n}
//! answer   {"ok": true, "length": n}                  bytes (then n bytes) or file (written)
//!          {"ok": true, "length": n, "sha256": hex}   sha256 of the encoded text
//!          {"raises": {"class": "ValueError" | "TypeError", "message": "..."}}
//!          {"raises": {"class": "UnicodeEncodeError", "encoding": "utf-8", "start": s, "end": e,
//!                      "characters": [code points], "reason": "surrogates not allowed"}}
//!          {"python_exception": i}                     re-raise the seam's recorded exception i
//!          {"fallback": "<reason>"}                    the seam writes this call with the oracle
//! ```
//!
//! A raise or fallback writes nothing. Exit status 0 for every answer; 1 for a request or pickle
//! the seam never writes (message on standard error).

pub mod json;
pub mod pickle;

use std::io::Write;

use serde::Deserialize;
use sha2::{Digest, Sha256};

pub const WRITER_VERSION: u32 = 1;

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WriteRequest {
    pub version: u32,
    pub ensure_ascii: bool,
    pub allow_nan: bool,
    pub sort_keys: bool,
    pub separators: (String, String),
    pub indent: Option<serde_json::Value>,
    pub terminator: String,
    pub output: Output,
    #[serde(default)]
    pub path: Option<String>,
    pub marker: (String, String),
    pub max_str_digits: u64,
}

#[derive(Debug, Deserialize, Clone, Copy, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum Output {
    Bytes,
    File,
    Sha256,
}

/// A request or pickle the seam never writes.
#[derive(Debug)]
pub struct WriterError(pub String);

impl std::fmt::Display for WriterError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for WriterError {}

impl WriteRequest {
    fn check(&self) -> Result<(), WriterError> {
        let bad = |message: &str| Err(WriterError(format!("request: {message}")));
        if self.version != WRITER_VERSION {
            return bad(&format!("version {} is not {WRITER_VERSION}", self.version));
        }
        // The one json.dumps configuration the worker uses (compact, sorted, default=str).
        if !self.sort_keys || self.separators != (",".to_owned(), ":".to_owned()) {
            return bad("only sort_keys=True with separators (',', ':') is written natively");
        }
        if self.indent.as_ref().is_some_and(|indent| !indent.is_null()) {
            return bad("indent must be null");
        }
        if !matches!(self.terminator.as_str(), "" | "\n" | "\r\n") {
            return bad("terminator must be empty, \\n or \\r\\n");
        }
        if (self.output == Output::File) != self.path.is_some() {
            return bad("path is required for output file and only there");
        }
        Ok(())
    }
}

/// The answer line and, for `bytes`, the payload.
pub struct Answer {
    pub line: serde_json::Value,
    pub payload: Vec<u8>,
}

/// Runs one write: `input` is the request line and the pickle.
pub fn run(input: &[u8]) -> Result<Answer, WriterError> {
    let split = input
        .iter()
        .position(|&byte| byte == b'\n')
        .ok_or_else(|| WriterError("no request line".into()))?;
    let request: WriteRequest = serde_json::from_slice(&input[..split])
        .map_err(|error| WriterError(format!("request: {error}")))?;
    request.check()?;
    let graph = pickle::read(&input[split + 1..], (&request.marker.0, &request.marker.1))
        .map_err(|error| WriterError(error.to_string()))?;
    let options = json::Options {
        ensure_ascii: request.ensure_ascii,
        allow_nan: request.allow_nan,
        max_str_digits: (request.max_str_digits > 0).then_some(request.max_str_digits),
    };
    let mut out = Vec::new();
    if let Err(stop) = json::encode(&graph, &options, &mut out) {
        return Ok(Answer {
            line: stop_line(stop),
            payload: Vec::new(),
        });
    }
    // str.encode("utf-8") of the text (only an ensure_ascii=False text can hold a surrogate).
    if let Some((start, end, characters)) = json::surrogate_run(&out) {
        return Ok(Answer {
            line: serde_json::json!({"raises": {
                "class": "UnicodeEncodeError",
                "encoding": "utf-8",
                "start": start,
                "end": end,
                "characters": characters,
                "reason": "surrogates not allowed",
            }}),
            payload: Vec::new(),
        });
    }
    out.extend_from_slice(request.terminator.as_bytes());
    let length = out.len();
    Ok(match request.output {
        Output::Bytes => Answer {
            line: serde_json::json!({"ok": true, "length": length}),
            payload: out,
        },
        Output::File => {
            let path = request.path.as_deref().unwrap_or_default();
            std::fs::write(path, &out)
                .map_err(|error| WriterError(format!("writing {path}: {error}")))?;
            Answer {
                line: serde_json::json!({"ok": true, "length": length}),
                payload: Vec::new(),
            }
        }
        Output::Sha256 => {
            let digest = Sha256::digest(&out);
            let hex: String = digest.iter().map(|byte| format!("{byte:02x}")).collect();
            Answer {
                line: serde_json::json!({"ok": true, "length": length, "sha256": hex}),
                payload: Vec::new(),
            }
        }
    })
}

fn stop_line(stop: json::Stop) -> serde_json::Value {
    match stop {
        json::Stop::Raise { class, message } => {
            serde_json::json!({"raises": {"class": class, "message": message}})
        }
        json::Stop::Python(index) => serde_json::json!({ "python_exception": index }),
        json::Stop::Fallback(reason) => serde_json::json!({ "fallback": reason }),
    }
}

/// `coach-analysis write`: standard input to standard output.
pub fn serve(mut input: impl std::io::Read, mut output: impl Write) -> Result<(), WriterError> {
    let mut data = Vec::new();
    input
        .read_to_end(&mut data)
        .map_err(|error| WriterError(format!("reading standard input: {error}")))?;
    let answer = run(&data)?;
    drop(data);
    let io = |error: std::io::Error| WriterError(format!("writing standard output: {error}"));
    output
        .write_all(answer.line.to_string().as_bytes())
        .map_err(io)?;
    output.write_all(b"\n").map_err(io)?;
    output.write_all(&answer.payload).map_err(io)?;
    output.flush().map_err(io)
}

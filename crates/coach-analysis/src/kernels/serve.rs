//! `coach-analysis kernels`: the child process the worker's `native_stages.kernel_session`
//! starts once per analysis (`contracts/worker-interface.md` section 7, boundary B;
//! `docs/08-rewrite/port/phase2-plan.md` section 4.2).
//!
//! One JSON object per line on standard input, one answer per line on standard output, in
//! order; diagnostics go to standard error. Values are codec version 1 (`native_codec.py`),
//! every list inline.
//!
//! ```text
//! start   {"version": 1, "codec": 1, "store": "<path>", "table": <table.json fields>,
//!          "steering_basis": <steering_basis.SteeringBasis>}
//!         -> {"ok": true, "length": n, "entries": ["analysis_engine._lap_summaries", ...]}
//! call    {"entry": "<module.name>", "args": [...], "kwargs": {...}}
//!         -> {"result": ..., "accessed": [...]}
//!          | {"raises": {"class": ..., "message": ...}, "accessed": [...]}
//!          | {"error": "..."}
//! ```
//!
//! A start that fails answers `{"error": ...}` and the process exits 1; a call that fails
//! answers `{"error": ...}` and the session goes on (the worker fails the job). End of input
//! ends the session with exit status 0.
//!
//! This file is compiled into the `coach-analysis` binary (`main.rs`), so it names the library
//! by its crate path.

use std::io::{BufRead, Write};
use std::path::Path;

use coach_analysis::ColumnStore;
use coach_analysis::kernels::dispatch::{self, Call};
use coach_analysis::kernels::{
    Answer, Dict, KernelError, Outcome, PyValue, Session, TableMeta, codec,
};

/// The `version` of the start request this build serves.
pub const PROTOCOL_VERSION: i64 = 1;

/// A failure that ends the process: the start was refused, or standard input or output failed.
#[derive(Debug, thiserror::Error)]
pub enum ServeError {
    #[error("start: {0}")]
    Start(String),
    #[error("I/O: {0}")]
    Io(#[from] std::io::Error),
}

/// Serves one session: the start line, then calls until end of input.
pub fn serve(mut input: impl BufRead, mut output: impl Write) -> Result<(), ServeError> {
    let mut line = String::new();
    if input.read_line(&mut line)? == 0 {
        return Err(ServeError::Start(
            "no start request before end of input".to_owned(),
        ));
    }
    let session = match start(&line) {
        Ok(session) => session,
        Err(error) => {
            let message = error.to_string();
            write_line(&mut output, &error_answer(&message))?;
            return Err(ServeError::Start(message));
        }
    };
    let entries = dispatch::entries()
        .map(|entry| PyValue::Str(entry.name.to_owned()))
        .collect();
    let ready = Dict::new()
        .with("ok", PyValue::Bool(true))
        .with("length", length_value(session.meta().length)?)
        .with("entries", PyValue::List(entries));
    write_line(&mut output, &encoded(&PyValue::Dict(ready)))?;
    loop {
        line.clear();
        if input.read_line(&mut line)? == 0 {
            return Ok(());
        }
        let answer = match request(&line) {
            Ok((entry, mut call)) => answer_text(session.serve(&entry, &mut call)),
            Err(error) => error_answer(&error.to_string()),
        };
        write_line(&mut output, &answer)?;
    }
}

fn length_value(length: usize) -> Result<PyValue, ServeError> {
    i64::try_from(length)
        .map(PyValue::Int)
        .map_err(|_| ServeError::Start(format!("length {length} exceeds i64")))
}

/// Opens the session a start request names: the store, the table metadata, the basis.
fn start(line: &str) -> Result<Session, KernelError> {
    let document = codec::decode(line.trim_end_matches(['\r', '\n']), None)?;
    let field = |name: &str| {
        document
            .get(name)
            .ok_or_else(|| KernelError::codec(format!("start request: {name:?} missing")))
    };
    if field("version")?.as_int() != Some(PROTOCOL_VERSION) {
        return Err(KernelError::codec(format!(
            "start request version {:?} is not {PROTOCOL_VERSION}",
            field("version")?
        )));
    }
    if field("codec")?.as_int() != Some(codec::CODEC_VERSION) {
        return Err(KernelError::codec(format!(
            "start request codec {:?} is not {}",
            field("codec")?,
            codec::CODEC_VERSION
        )));
    }
    let store = field("store")?
        .as_str()
        .ok_or_else(|| KernelError::codec("start request: store is not a path"))?;
    let store = ColumnStore::read(Path::new(store))
        .map_err(|error| KernelError::internal(format!("store {store}: {error}")))?;
    let meta = TableMeta::from_value(field("table")?)?;
    Session::new(store, meta, field("steering_basis")?.clone())
}

/// One call request: its entry label and arguments.
fn request(line: &str) -> Result<(String, Call), KernelError> {
    let document = codec::decode(line.trim_end_matches(['\r', '\n']), None)?;
    let Some(fields) = document.as_dict() else {
        return Err(KernelError::codec("a call request is not an object"));
    };
    for (key, _) in fields.iter() {
        let name = key.as_str().unwrap_or_default();
        if !matches!(name, "entry" | "args" | "kwargs") {
            return Err(KernelError::codec(format!(
                "call request: unknown field {name:?}"
            )));
        }
    }
    let entry = fields
        .get("entry")
        .and_then(PyValue::as_str)
        .ok_or_else(|| KernelError::codec("call request: entry missing"))?
        .to_owned();
    let take = |name: &str| {
        fields
            .get(name)
            .cloned()
            .ok_or_else(|| KernelError::codec(format!("call request: {name} missing")))
    };
    Ok((entry, Call::from_values(take("args")?, take("kwargs")?)?))
}

fn accessed_value(answer: &Answer) -> PyValue {
    PyValue::List(answer.accessed.iter().cloned().map(PyValue::Str).collect())
}

/// The answer line for a call's outcome.
fn answer_text(answer: Answer) -> String {
    let accessed = accessed_value(&answer);
    let document = match answer.outcome {
        Outcome::Value(value) => Dict::new().with("result", value).with("accessed", accessed),
        Outcome::Raises { class, message } => Dict::new()
            .with(
                "raises",
                PyValue::Dict(
                    Dict::new()
                        .with("class", PyValue::Str(class))
                        .with("message", PyValue::Str(message)),
                ),
            )
            .with("accessed", accessed),
        Outcome::Error(message) => return error_answer(&message),
    };
    encoded(&PyValue::Dict(document))
}

/// `value` as one codec line, or an error answer when the codec has no rule for it.
fn encoded(value: &PyValue) -> String {
    codec::to_json(value)
        .unwrap_or_else(|error| error_answer(&format!("answer not encodable: {error}")))
}

fn error_answer(message: &str) -> String {
    let mut out = String::from("{\"error\":");
    codec::write_string(&mut out, message);
    out.push('}');
    out
}

fn write_line(output: &mut impl Write, text: &str) -> std::io::Result<()> {
    output.write_all(text.as_bytes())?;
    output.write_all(b"\n")?;
    output.flush()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn run(input: &str) -> (Result<(), ServeError>, Vec<String>) {
        let mut out = Vec::new();
        let result = serve(input.as_bytes(), &mut out);
        let text = String::from_utf8(out).expect("answers are UTF-8");
        (result, text.lines().map(str::to_owned).collect())
    }

    #[test]
    fn a_refused_start_answers_an_error_and_ends() {
        for (input, message) in [
            ("", "no start request"),
            ("{\"version\":2,\"codec\":1}\n", "version"),
            ("{\"version\":1,\"codec\":2}\n", "codec"),
            (
                "{\"version\":1,\"codec\":1,\"store\":1}\n",
                "store is not a path",
            ),
            ("not json\n", "codec"),
        ] {
            let (result, lines) = run(input);
            let error = result.expect_err(input).to_string();
            assert!(error.contains(message), "{input:?}: {error}");
            if !input.is_empty() {
                assert_eq!(lines.len(), 1, "{input:?}");
                assert!(
                    lines[0].starts_with("{\"error\":"),
                    "{input:?}: {}",
                    lines[0]
                );
            }
        }
        let (result, lines) = run(
            "{\"version\":1,\"codec\":1,\"store\":\"no-such-store.ccs\",\"table\":{},\"steering_basis\":null}\n",
        );
        assert!(result.is_err());
        assert!(lines[0].contains("no-such-store.ccs"), "{}", lines[0]);
    }

    #[test]
    fn call_requests_are_checked_before_dispatch() {
        let checks = [
            ("[]", "not an object"),
            ("{\"entry\":\"x\",\"args\":[]}", "kwargs missing"),
            ("{\"args\":[],\"kwargs\":{}}", "entry missing"),
            (
                "{\"entry\":\"x\",\"args\":[],\"kwargs\":{},\"extra\":1}",
                "unknown field",
            ),
            ("{\"entry\":\"x\",\"args\":{},\"kwargs\":{}}", "not a list"),
            ("{\"entry\":\"x\",\"args\":[NaN],\"kwargs\":{}}", "codec"),
        ];
        for (line, message) in checks {
            let error = request(line).expect_err(line).to_string();
            assert!(error.contains(message), "{line}: {error}");
        }
        let (entry, call) =
            request("{\"entry\":\"pit_loss.measure_pit_loss\",\"args\":[{\"$table\":1}],\"kwargs\":{\"bins\":200}}\n")
                .expect("a well-formed request");
        assert_eq!(entry, "pit_loss.measure_pit_loss");
        assert!(matches!(call.args.as_slice(), [PyValue::Table]));
        assert_eq!(call.kwargs.len(), 1);
    }

    #[test]
    fn answers_carry_the_outcome_and_the_accessed_channels() {
        let accessed = ["Lap".to_owned(), "Speed".to_owned()].into_iter().collect();
        let value = Answer {
            outcome: Outcome::Value(PyValue::List(vec![PyValue::Float(-0.0), PyValue::Int(3)])),
            accessed,
        };
        assert_eq!(
            answer_text(value),
            "{\"result\":[-0.0,3],\"accessed\":[\"Lap\",\"Speed\"]}"
        );
        let raises = Answer {
            outcome: Outcome::Raises {
                class: "ValueError".to_owned(),
                message: "bad \"lap\"".to_owned(),
            },
            accessed: Default::default(),
        };
        assert_eq!(
            answer_text(raises),
            "{\"raises\":{\"class\":\"ValueError\",\"message\":\"bad \\\"lap\\\"\"},\"accessed\":[]}"
        );
        let error = Answer {
            outcome: Outcome::Error("entry x is not provided by this build".to_owned()),
            accessed: Default::default(),
        };
        assert_eq!(
            answer_text(error),
            "{\"error\":\"entry x is not provided by this build\"}"
        );
        let nan = Answer {
            outcome: Outcome::Value(PyValue::Float(f64::NAN)),
            accessed: Default::default(),
        };
        assert_eq!(
            answer_text(nan),
            "{\"result\":{\"$f\":\"nan\"},\"accessed\":[]}"
        );
    }
}

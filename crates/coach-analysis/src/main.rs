//! `coach-analysis`: the native stages as a command the Python worker runs.
//!
//! ```text
//! coach-analysis decode                               request JSON on stdin (request.rs); writes
//!                                                     the column store, one JSON line on stdout
//! coach-analysis golden <ibt> [--target-hz <float>]   decode golden (emit_ibt_golden.py fields)
//! coach-analysis inspect <store>                      the store header as JSON
//! coach-analysis --version
//! ```
//!
//! Exit status 0 on success, 1 when the stage fails (message on stderr), 2 on a usage error.
//! A failed `golden` also prints its rejection on stdout (`golden::rejection`: the oracle's
//! exception class and the message) for the parity gate; any other exit status (a panic is
//! 101) is never a rejection.

use std::io::Read;
use std::path::Path;
use std::process::ExitCode;

use coach_analysis::{ColumnStore, DecodeError, DecodeRequest, decode, golden};
use coach_ibt::IbtFile;

const USAGE: &str = "usage: coach-analysis decode < request.json\n       coach-analysis golden <ibt> [--target-hz <float>]\n       coach-analysis inspect <store>\n       coach-analysis --version";

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let result = match args.first().map(String::as_str) {
        Some("decode") if args.len() == 1 => run_decode(),
        Some("golden") => match args.as_slice() {
            [_, ibt] => run_golden(Path::new(ibt), None),
            [_, ibt, flag, rate] if flag == "--target-hz" => run_golden(Path::new(ibt), Some(rate)),
            _ => return usage(),
        },
        Some("inspect") if args.len() == 2 => run_inspect(Path::new(&args[1])),
        Some("--version") if args.len() == 1 => {
            println!("coach-analysis {}", env!("CARGO_PKG_VERSION"));
            Ok(())
        }
        _ => return usage(),
    };
    match result {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            if args[0] == "golden" {
                println!("{}", golden::rejection(&error));
            }
            eprintln!("coach-analysis {}: {error}", args[0]);
            ExitCode::from(1)
        }
    }
}

fn usage() -> ExitCode {
    eprintln!("{USAGE}");
    ExitCode::from(2)
}

fn to_json<T: serde::Serialize>(value: &T) -> Result<String, DecodeError> {
    serde_json::to_string(value).map_err(|error| DecodeError::Store(error.to_string()))
}

fn run_decode() -> Result<(), DecodeError> {
    let mut text = String::new();
    std::io::stdin().read_to_string(&mut text)?;
    let summary = DecodeRequest::parse(&text)?.run()?;
    println!("{}", to_json(&summary)?);
    Ok(())
}

fn run_golden(ibt: &Path, target_hz: Option<&String>) -> Result<(), DecodeError> {
    let target = target_hz
        .map(|text| text.parse::<f64>().map_err(|_| DecodeError::TargetHz))
        .transpose()?;
    let decoded = decode(ibt, None, target)?;
    let vars = IbtFile::open(ibt)?.vars;
    let units = |name: &str| {
        vars.get(name)
            .map(|var| var.unit.clone())
            .unwrap_or_default()
    };
    println!("{}", to_json(&golden::golden(&decoded, &units))?);
    Ok(())
}

fn run_inspect(path: &Path) -> Result<(), DecodeError> {
    let store = ColumnStore::read(path)?;
    println!("{}", to_json(&store.header)?);
    Ok(())
}

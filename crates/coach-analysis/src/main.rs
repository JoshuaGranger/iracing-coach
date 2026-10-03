//! `coach-analysis`: the native stages as a command the Python worker runs.
//!
//! ```text
//! coach-analysis decode                               request JSON on stdin (request.rs); writes
//!                                                     the column store, one JSON line on stdout
//! coach-analysis golden <ibt> [--target-hz <float>]   decode golden (emit_ibt_golden.py fields)
//! coach-analysis inspect <store>                      the store header as JSON
//! coach-analysis kernels                              the kernels child: JSON lines on stdin and
//!                                                     stdout until end of input (kernels/serve.rs)
//! coach-analysis merge                                merge request JSON on stdin: a group's decode
//!                                                     stores into one merge store, one JSON line out
//! coach-analysis --version
//! ```
//!
//! Exit status 0 on success, 1 when the stage fails (message on stderr), 2 on a usage error.
//! A failed `golden` also prints its rejection on stdout (`golden::rejection`: the oracle's
//! exception class and the message) for the parity gate; any other exit status (a panic is
//! 101) is never a rejection.

use std::io::{BufWriter, Read};
use std::path::Path;
use std::process::ExitCode;

use coach_analysis::{ColumnStore, DecodeError, DecodeRequest, decode, golden};
use coach_ibt::IbtFile;

// The library owns the kernels/ module tree; the child-process protocol is compiled here.
#[path = "kernels/serve.rs"]
mod serve;

const USAGE: &str = "usage: coach-analysis decode < request.json\n       coach-analysis golden <ibt> [--target-hz <float>]\n       coach-analysis inspect <store>\n       coach-analysis kernels\n       coach-analysis merge < request.json\n       coach-analysis --version";

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.first().map(String::as_str) == Some("kernels") {
        return run_kernels(&args);
    }
    let result = match args.first().map(String::as_str) {
        Some("decode") if args.len() == 1 => run_decode(),
        Some("merge") if args.len() == 1 => run_merge(),
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

fn run_kernels(args: &[String]) -> ExitCode {
    if args.len() != 1 {
        return usage();
    }
    let output = BufWriter::new(std::io::stdout().lock());
    match serve::serve(std::io::stdin().lock(), output) {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("coach-analysis kernels: {error}");
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

/// `coach-analysis merge` (worker-interface.md section 7, boundary B): the column merge of a
/// multi-file group (`kernels::table::merge`, unit U1) written as a `merge`-stage store.
#[derive(serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct MergeRequest {
    version: u32,
    /// The group's decode stores in recording order.
    inputs: Vec<String>,
    /// The merged channel order `workflow._load_analysis_telemetry` computed; the store's
    /// columns must be exactly these.
    channels: Vec<String>,
    out: String,
}

fn run_merge() -> Result<(), DecodeError> {
    let mut text = String::new();
    std::io::stdin().read_to_string(&mut text)?;
    let request: MergeRequest =
        serde_json::from_str(&text).map_err(|error| DecodeError::Request(error.to_string()))?;
    if request.version != coach_analysis::REQUEST_VERSION {
        return Err(DecodeError::Request(format!(
            "version {} is not {}",
            request.version,
            coach_analysis::REQUEST_VERSION
        )));
    }
    let stores = request
        .inputs
        .iter()
        .map(|path| ColumnStore::read(Path::new(path)))
        .collect::<Result<Vec<_>, _>>()?;
    let inputs: Vec<&ColumnStore> = stores.iter().collect();
    let merged = coach_analysis::kernels::table::merge::merge(&inputs, &request.channels)
        .map_err(|error| DecodeError::Store(error.to_string()))?;
    let names: Vec<&str> = merged
        .columns
        .iter()
        .map(|column| column.name.as_str())
        .collect();
    if names != request.channels {
        return Err(DecodeError::Store(format!(
            "merged columns {names:?} are not the requested channels"
        )));
    }
    let bytes = coach_analysis::store::write_merged(&merged, Path::new(&request.out))?;
    println!(
        "{}",
        to_json(&serde_json::json!({
            "out": request.out,
            "bytes": bytes,
            "sample_count": merged.sample_indices.len(),
        }))?
    );
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

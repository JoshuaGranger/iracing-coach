//! Parity with the Python oracle (`ibt_reader.py`): every value `tools/emit_session_golden.py`
//! records must be what this crate produces for the same bytes.
//!
//! Committed goldens: `tests/golden/*.golden.json` (from `test-data/ibt`, regenerate with
//! `emit_session_golden.py test-data\ibt crates\coach-ibt\tests\golden`) and
//! `tests/golden/yaml/*.cases.golden.json` (from `tests/yaml-cases`, regenerate with
//! `emit_session_golden.py crates\coach-ibt\tests\yaml-cases crates\coach-ibt\tests\golden\yaml`).
//! The ignored test runs the emitter over `COACH_PRIVATE_CORPUS` itself.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{Duration, UNIX_EPOCH};

use coach_ibt::*;
use serde_json::Value;
use sha2::{Digest, Sha256};

const DEFAULT_PYTHON: &str =
    r"C:\Users\joshu\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe";
/// Windows CPython's `datetime.fromtimestamp(t, timezone.utc)` raises `OSError` outside
/// [-43200, 32536850399] (CRT limits, measured with 3.12.14); the contract follows Linux CPython
/// (years 1 to 9999), so a Windows golden's missing UTC text is accepted outside that range.
const WINDOWS_TIMESTAMP_RANGE: std::ops::RangeInclusive<f64> = -43_200.0..=32_536_850_399.0;

fn crate_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

fn repository() -> PathBuf {
    crate_dir().join("..").join("..")
}

fn sha256_hex(bytes: &[u8]) -> String {
    Sha256::digest(bytes)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

fn read_json(path: &Path) -> Value {
    let text = std::fs::read_to_string(path).unwrap_or_else(|e| panic!("{}: {e}", path.display()));
    serde_json::from_str(&text).unwrap_or_else(|e| panic!("{}: {e}", path.display()))
}

fn goldens(dir: &Path, suffix: &str) -> Vec<PathBuf> {
    let mut found: Vec<PathBuf> = std::fs::read_dir(dir)
        .unwrap_or_else(|e| panic!("{}: {e}", dir.display()))
        .filter_map(|entry| entry.ok().map(|entry| entry.path()))
        .filter(|path| path.is_file() && path.to_string_lossy().ends_with(suffix))
        .collect();
    found.sort();
    found
}

// ---- Python values ----

#[derive(Debug, Clone)]
enum Py {
    None,
    Bool(bool),
    /// Decimal text.
    Int(String),
    Float(f64),
    Str(String),
    List(Vec<Py>),
    Map(Vec<(String, Py)>),
    Unsupported,
}

fn golden_py(value: &Value) -> Py {
    match value {
        Value::Null => Py::None,
        Value::Bool(value) => Py::Bool(*value),
        Value::String(text) => Py::Str(text.clone()),
        Value::Array(items) => Py::List(items.iter().map(golden_py).collect()),
        Value::Object(map) => {
            if let Some(Value::String(text)) = map.get("int") {
                Py::Int(text.clone())
            } else if let Some(Value::String(text)) = map.get("float") {
                Py::Float(text.parse().unwrap_or_else(|_| panic!("float repr {text}")))
            } else if let Some(Value::Array(entries)) = map.get("map") {
                Py::Map(
                    entries
                        .iter()
                        .map(|entry| {
                            (
                                entry[0].as_str().unwrap_or_default().to_owned(),
                                golden_py(&entry[1]),
                            )
                        })
                        .collect(),
                )
            } else if map.contains_key("unsupported") {
                Py::Unsupported
            } else {
                panic!("untagged golden object {value}")
            }
        }
        Value::Number(number) => Py::Int(number.to_string()),
    }
}

/// serde output of this crate's types (`Serialize` field names match the golden keys).
fn serde_py(value: &Value) -> Py {
    match value {
        Value::Null => Py::None,
        Value::Bool(value) => Py::Bool(*value),
        Value::String(text) => Py::Str(text.clone()),
        Value::Array(items) => Py::List(items.iter().map(serde_py).collect()),
        Value::Object(map) => Py::Map(map.iter().map(|(k, v)| (k.clone(), serde_py(v))).collect()),
        Value::Number(number) if number.is_f64() => Py::Float(number.as_f64().unwrap_or(f64::NAN)),
        Value::Number(number) => Py::Int(number.to_string()),
    }
}

fn scalar_py(scalar: &YamlScalar) -> Py {
    match scalar {
        YamlScalar::Null => Py::None,
        YamlScalar::Bool(value) => Py::Bool(*value),
        YamlScalar::Int(value) => Py::Int(value.to_string()),
        YamlScalar::Float(value) => Py::Float(*value),
        YamlScalar::Str(text) => Py::Str(text.clone()),
    }
}

/// Python dict semantics for a block map: first position, last value.
fn dict_view(entries: &[(String, YamlNode)]) -> Vec<(&str, &YamlNode)> {
    let mut view: Vec<(&str, &YamlNode)> = Vec::new();
    for (key, node) in entries {
        match view
            .iter_mut()
            .find(|(existing, _)| *existing == key.as_str())
        {
            Some(slot) => slot.1 = node,
            None => view.push((key, node)),
        }
    }
    view
}

fn node_py(node: &YamlNode) -> Py {
    match node {
        YamlNode::Map(entries) => Py::Map(
            dict_view(entries)
                .into_iter()
                .map(|(key, node)| (key.to_owned(), node_py(node)))
                .collect(),
        ),
        YamlNode::List(items) => Py::List(items.iter().map(node_py).collect()),
        YamlNode::Scalar(scalar) => scalar_py(&scalar.typed()),
        YamlNode::Literal(scalar) => scalar_py(scalar),
        YamlNode::Null => Py::None,
    }
}

fn sorted(value: Py) -> Py {
    match value {
        Py::Map(mut entries) => {
            entries.sort_by(|a, b| a.0.cmp(&b.0));
            Py::Map(entries.into_iter().map(|(k, v)| (k, sorted(v))).collect())
        }
        Py::List(items) => Py::List(items.into_iter().map(sorted).collect()),
        other => other,
    }
}

/// Records every difference between the oracle's value and ours under `path`.
fn diff(path: &str, golden: &Py, actual: &Py, out: &mut Vec<String>) {
    let same = match (golden, actual) {
        (Py::None, Py::None) => true,
        (Py::Bool(a), Py::Bool(b)) => a == b,
        (Py::Int(a), Py::Int(b)) => a == b,
        // Plain integers beyond i64 stay text in the port (crate-api.md 3.5).
        (Py::Int(a), Py::Str(b)) => a.parse::<i64>().is_err() && b.trim_start_matches('+') == a,
        (Py::Float(a), Py::Float(b)) => a.to_bits() == b.to_bits() || (a.is_nan() && b.is_nan()),
        (Py::Str(a), Py::Str(b)) => a == b,
        (Py::List(a), Py::List(b)) => {
            if a.len() == b.len() {
                for (index, (x, y)) in a.iter().zip(b).enumerate() {
                    diff(&format!("{path}[{index}]"), x, y, out);
                }
                return;
            }
            false
        }
        (Py::Map(a), Py::Map(b)) => {
            let keys = |m: &Vec<(String, Py)>| m.iter().map(|(k, _)| k.clone()).collect::<Vec<_>>();
            if keys(a) == keys(b) {
                for ((key, x), (_, y)) in a.iter().zip(b) {
                    diff(&format!("{path}.{key}"), x, y, out);
                }
                return;
            }
            false
        }
        _ => false,
    };
    if !same {
        out.push(format!("{path}: python {golden:?}, rust {actual:?}"));
    }
}

// ---- checks ----

fn field(golden: &Value, key: &str) -> Value {
    golden.get(key).cloned().unwrap_or(Value::Null)
}

fn int(golden: &Value, key: &str) -> i64 {
    golden
        .get(key)
        .and_then(Value::as_i64)
        .unwrap_or_else(|| panic!("golden lacks integer {key}"))
}

/// Python exception class -> the IbtError variant the port must return.
fn check_error(context: &str, golden: &Value, error: &IbtError, out: &mut Vec<String>) {
    let class = golden["class"].as_str().unwrap_or_default();
    let matches = match (class, error) {
        (
            "IbtTruncatedError",
            IbtError::Truncated {
                start,
                end,
                available,
                ..
            },
        ) => {
            let expected = (
                golden.get("start"),
                golden.get("end"),
                golden.get("available"),
            );
            match expected {
                (Some(s), Some(e), Some(a)) => {
                    s.as_u64() == Some(*start)
                        && e.as_u64() == Some(*end)
                        && a.as_u64() == Some(*available)
                }
                _ => true,
            }
        }
        ("IbtBoundsError", IbtError::Bounds { .. }) => true,
        ("IbtTypeError", IbtError::UnsupportedVarType { .. }) => true,
        ("IbtFormatError", IbtError::Format(_)) => true,
        ("IbtYamlError", IbtError::Yaml { line, .. }) => golden["line"]
            .as_u64()
            .is_none_or(|l| l == u64::from(*line)),
        // literal_eval's TypeError and the parser's RecursionError escape Python uncaught.
        ("TypeError" | "RecursionError", IbtError::Yaml { .. }) => true,
        ("FileNotFoundError" | "PermissionError" | "OSError", IbtError::Io(_)) => true,
        _ => false,
    };
    if !matches {
        out.push(format!(
            "{context}: python {class} ({}), rust {error}",
            golden["message"]
        ));
    }
}

fn summary_py(summary: &RecordingSummary) -> Py {
    let mut value = serde_json::to_value(summary).expect("summary serializes");
    // serde writes a non-finite f64 as null; compare the floats themselves.
    value["start_time_unix"] = Value::Null;
    value["end_time_unix"] = Value::Null;
    let Py::Map(mut entries) = serde_py(&value) else {
        unreachable!("summary is an object")
    };
    for (key, slot) in &mut entries {
        match key.as_str() {
            "start_time_unix" => *slot = Py::Float(summary.start_time_unix),
            "end_time_unix" => *slot = Py::Float(summary.end_time_unix),
            _ => {}
        }
    }
    Py::Map(entries)
}

/// Windows goldens report no UTC text outside `WINDOWS_TIMESTAMP_RANGE`; the port follows Linux.
fn allow_windows_gmtime(golden: &mut Py, summary: &RecordingSummary, platform: &str) {
    if platform != "win32" {
        return;
    }
    let Py::Map(entries) = golden else { return };
    for (key, epoch) in [
        ("start_time_utc", summary.start_time_unix),
        ("end_time_utc", summary.end_time_unix),
    ] {
        let actual = if key == "start_time_utc" {
            &summary.start_time_utc
        } else {
            &summary.end_time_utc
        };
        if let Some((_, slot)) = entries.iter_mut().find(|(k, _)| k == key)
            && matches!(slot, Py::None)
            && !WINDOWS_TIMESTAMP_RANGE.contains(&epoch.floor())
            && let Some(text) = actual
        {
            *slot = Py::Str(text.clone());
        }
    }
}

fn leaves<'a>(prefix: &str, node: &'a YamlNode, out: &mut Vec<(String, &'a YamlNode)>) {
    let join = |segment: &str| {
        if prefix.is_empty() {
            segment.to_owned()
        } else {
            format!("{prefix}.{segment}")
        }
    };
    match node {
        YamlNode::Map(entries) => {
            for (key, child) in dict_view(entries) {
                leaves(&join(key), child, out);
            }
        }
        YamlNode::List(items) => {
            for (index, child) in items.iter().enumerate() {
                leaves(&join(&index.to_string()), child, out);
            }
        }
        YamlNode::Null | YamlNode::Literal(YamlScalar::Null) => {}
        YamlNode::Scalar(scalar) if scalar.typed() == YamlScalar::Null => {}
        leaf => out.push((prefix.to_owned(), leaf)),
    }
}

fn check_car_setup(context: &str, golden: &Value, info: &SessionInfo, out: &mut Vec<String>) {
    let actual = info.car_setup();
    let (Some(golden), Some(actual)) = (golden.as_object(), actual.as_ref()) else {
        if golden.is_null() != actual.is_none() {
            out.push(format!(
                "{context}.car_setup: python {golden}, rust {actual:?}"
            ));
        }
        return;
    };
    let update = actual
        .update_count
        .map_or(Py::None, |n| Py::Int(n.to_string()));
    diff(
        &format!("{context}.car_setup.update_count"),
        &golden_py(&golden["update_count"]),
        &update,
        out,
    );
    let Some(node) = info.get("CarSetup") else {
        out.push(format!("{context}.car_setup: no CarSetup node"));
        return;
    };
    let mut ours = Vec::new();
    leaves("", node, &mut ours);
    let theirs = golden["leaves"].as_array().cloned().unwrap_or_default();
    let our_paths: Vec<&str> = ours.iter().map(|(path, _)| path.as_str()).collect();
    let their_paths: Vec<&str> = theirs
        .iter()
        .map(|leaf| leaf[0].as_str().unwrap_or_default())
        .collect();
    if our_paths != their_paths {
        out.push(format!(
            "{context}.car_setup paths: python {their_paths:?}, rust {our_paths:?}"
        ));
        return;
    }
    if actual.values.len() != ours.len() {
        out.push(format!(
            "{context}.car_setup.values has {} keys for {} leaves",
            actual.values.len(),
            ours.len()
        ));
    }
    for ((path, node), leaf) in ours.iter().zip(&theirs) {
        let value = actual.values.get(path).map(String::as_str);
        let context = format!("{context}.car_setup[{path}]");
        match node {
            // A literal's published text is Python str() of its value.
            YamlNode::Literal(_) => {
                if value != leaf[1].as_str() {
                    out.push(format!("{context}: python str {}, rust {value:?}", leaf[1]));
                }
            }
            YamlNode::Scalar(scalar) => {
                diff(
                    &context,
                    &golden_py(&leaf[2]),
                    &scalar_py(&scalar.typed()),
                    out,
                );
                if value != Some(scalar.text()) {
                    out.push(format!(
                        "{context}: value {value:?} is not the published text {:?}",
                        scalar.text()
                    ));
                }
            }
            _ => out.push(format!("{context}: unexpected node {node:?}")),
        }
    }
}

/// Session text, tree, extracts and summary of one session-info block.
fn check_session(
    context: &str,
    golden: &Value,
    file: &IbtFile,
    platform: &str,
    out: &mut Vec<String>,
) {
    let text = file.session_info_text();
    match golden["session_info_text_sha256"].as_str() {
        Some(expected) => {
            if sha256_hex(text.as_bytes()) != expected {
                out.push(format!(
                    "{context}: decoded session-info text differs from Python's"
                ));
            }
        }
        None => {
            let cut = file
                .session_info_bytes
                .split(|&b| b == 0)
                .next()
                .unwrap_or_default();
            if !cut.iter().all(u8::is_ascii_whitespace) {
                out.push(format!(
                    "{context}: Python skipped a non-blank session-info block"
                ));
            }
        }
    }
    let info = match file.session_info() {
        Ok(info) => info,
        Err(error) => {
            if golden["unsupported"].is_null() {
                out.push(format!("{context}: python parsed, rust {error}"));
            }
            return;
        }
    };
    if let Some(reason) = golden["unsupported"].as_str() {
        out.push(format!(
            "{context}: python value ({reason}) must be rejected, rust parsed"
        ));
        return;
    }
    diff(
        &format!("{context}.tree"),
        &golden_py(&golden["session_info"]),
        &node_py(&info.root),
        out,
    );

    let extracts = &golden["extracts"];
    let ours = |value: Value| sorted(serde_py(&value));
    let theirs = |value: &Value| sorted(golden_py(value));
    diff(
        &format!("{context}.weekend"),
        &theirs(&extracts["weekend"]),
        &ours(serde_json::to_value(info.weekend()).expect("serializes")),
        out,
    );
    diff(
        &format!("{context}.sessions"),
        &theirs(&extracts["sessions"]),
        &ours(serde_json::to_value(info.sessions()).expect("serializes")),
        out,
    );
    diff(
        &format!("{context}.driver"),
        &theirs(&extracts["driver"]),
        &ours(serde_json::to_value(info.driver()).expect("serializes")),
        out,
    );
    diff(
        &format!("{context}.driver_setup"),
        &theirs(&extracts["driver_setup"]),
        &ours(serde_json::to_value(info.driver_setup()).expect("serializes")),
        out,
    );
    check_car_setup(context, &extracts["car_setup"], &info, out);

    match file.summary() {
        Ok(summary) => {
            let mut expected = golden_py(&golden["summary"]);
            allow_windows_gmtime(&mut expected, &summary, platform);
            diff(
                &format!("{context}.summary"),
                &sorted(expected),
                &sorted(summary_py(&summary)),
                out,
            );
        }
        Err(error) => out.push(format!("{context}.summary: {error}")),
    }
}

fn edge_py(scalar: SdkScalar) -> Py {
    match scalar {
        SdkScalar::Char(value) => Py::Int(value.to_string()),
        SdkScalar::Bool(value) => Py::Bool(value),
        SdkScalar::Int(value) => Py::Int(value.to_string()),
        SdkScalar::BitField(value) => Py::Int(value.to_string()),
        SdkScalar::Float(value) => Py::Float(f64::from(value)),
        SdkScalar::Double(value) => Py::Float(value),
    }
}

/// Header, variables, session info, summary and every scalar edge read of one recording.
fn check_ibt(golden: &Value, path: &Path, out: &mut Vec<String>) {
    let context = golden["source"].as_str().unwrap_or("?").to_owned();
    let platform = golden["platform"].as_str().unwrap_or_default();
    if golden["outcome"] == "error" {
        let result = IbtFile::open(path).and_then(|file| file.summary());
        match result {
            Ok(_) => out.push(format!(
                "{context}: python rejected the file, rust accepted it"
            )),
            Err(error) => check_error(&context, &golden["error"], &error, out),
        }
        return;
    }
    let file = match IbtFile::open(path) {
        Ok(file) => file,
        Err(error) => {
            out.push(format!("{context}: python opened the file, rust {error}"));
            return;
        }
    };
    let header = &golden["header"];
    let ours = [
        ("version", file.header.ver),
        ("status", file.header.status),
        ("tick_rate", file.header.tick_rate),
        ("session_info_update", file.header.session_info_update),
        ("session_info_length", file.header.session_info_len),
        ("session_info_offset", file.header.session_info_offset),
        ("num_variables", file.header.num_vars),
        ("variable_header_offset", file.header.var_header_offset),
        ("num_buffers", file.header.num_buf),
        ("buffer_length", file.header.buf_len),
    ];
    for (key, value) in ours {
        if int(header, key) != i64::from(value) {
            out.push(format!(
                "{context}.header.{key}: python {}, rust {value}",
                header[key]
            ));
        }
    }
    let buffers: Vec<(i64, i64)> = field(golden, "buffers")
        .as_array()
        .map(|items| {
            items
                .iter()
                .map(|b| (b[0].as_i64().unwrap_or(-1), b[1].as_i64().unwrap_or(-1)))
                .collect()
        })
        .unwrap_or_default();
    let our_buffers: Vec<(i64, i64)> = file.header.var_bufs[..file.header.num_buf as usize]
        .iter()
        .map(|b| (i64::from(b.tick_count), i64::from(b.buf_offset)))
        .collect();
    if buffers != our_buffers {
        out.push(format!(
            "{context}.buffers: python {buffers:?}, rust {our_buffers:?}"
        ));
    }
    let disk = Py::Map(vec![
        (
            "session_start_date".into(),
            Py::Int(file.disk.session_start_date.to_string()),
        ),
        (
            "session_start_time".into(),
            Py::Float(file.disk.session_start_time),
        ),
        (
            "session_end_time".into(),
            Py::Float(file.disk.session_end_time),
        ),
        (
            "session_lap_count".into(),
            Py::Int(file.disk.session_lap_count.to_string()),
        ),
        (
            "session_record_count".into(),
            Py::Int(file.disk.session_record_count.to_string()),
        ),
    ]);
    diff(
        &format!("{context}.disk"),
        &golden_py(&golden["disk"]),
        &disk,
        out,
    );
    for (key, value) in [
        ("file_size", file.file_size),
        ("buffer_offset", file.buffer_offset),
        ("record_count", u64::from(file.record_count)),
    ] {
        if golden[key].as_u64() != Some(value) {
            out.push(format!(
                "{context}.{key}: python {}, rust {value}",
                golden[key]
            ));
        }
    }
    let variables: Vec<Value> = file
        .vars
        .iter()
        .map(|v| {
            serde_json::json!([
                v.name,
                v.desc,
                v.unit,
                v.var_type as i32,
                v.offset,
                v.count,
                v.count_as_time
            ])
        })
        .collect();
    if Value::Array(variables) != golden["variables"] {
        out.push(format!("{context}.variables differ"));
    }
    if golden["session_info_sha256"].as_str() != Some(file.session_info_sha256().as_str()) {
        out.push(format!("{context}.session_info_sha256 differs"));
    }
    check_session(&context, golden, &file, platform, out);

    let edges = golden["edges"].as_object().cloned().unwrap_or_default();
    for variable in file.vars.iter() {
        let actual = match file.edge_scalar(&variable.name) {
            Ok(value) => value.map_or(Py::None, edge_py),
            Err(error) => {
                out.push(format!("{context}.edge[{}]: {error}", variable.name));
                continue;
            }
        };
        let expected = edges.get(&variable.name).map_or(Py::None, golden_py);
        diff(
            &format!("{context}.edge[{}]", variable.name),
            &expected,
            &actual,
            out,
        );
    }
}

/// `split_cases` in the emitter: marker lines `%%% name k=v ...`, raw bodies.
fn split_cases(data: &[u8]) -> Vec<(String, BTreeMap<String, String>, Vec<u8>)> {
    const MARKER: &[u8] = b"%%% ";
    let mut starts: Vec<usize> = Vec::new();
    if data.starts_with(MARKER) {
        starts.push(0);
    }
    starts.extend(
        (0..data.len())
            .filter(|&i| data[i] == b'\n' && data[i + 1..].starts_with(MARKER))
            .map(|i| i + 1),
    );
    let mut cases = Vec::new();
    for (position, &start) in starts.iter().enumerate() {
        let line_end = data[start..]
            .iter()
            .position(|&b| b == b'\n')
            .map_or(data.len(), |i| start + i);
        let words: Vec<String> = String::from_utf8_lossy(&data[start + MARKER.len()..line_end])
            .split_whitespace()
            .map(str::to_owned)
            .collect();
        let options = words[1..]
            .iter()
            .filter_map(|word| {
                word.split_once('=')
                    .map(|(k, v)| (k.to_owned(), v.to_owned()))
            })
            .collect();
        let end = starts.get(position + 1).copied().unwrap_or(data.len());
        let body = data.get(line_end + 1..end).unwrap_or_default().to_vec();
        cases.push((words[0].clone(), options, body));
    }
    cases
}

fn case_file(body: Vec<u8>, options: &BTreeMap<String, String>, path: &Path) -> IbtFile {
    let option = |key: &str| options.get(key).map(String::as_str).unwrap_or("0");
    IbtFile {
        path: path.to_path_buf(),
        file_size: 0,
        mtime: UNIX_EPOCH + Duration::new(1_500_000_000, 250_000_000),
        header: SdkHeader::parse(&[0; SDK_HEADER_LEN]).expect("zero header parses"),
        disk: DiskSubHeader {
            session_start_date: option("start_date").parse().expect("start_date"),
            session_start_time: option("start_time").parse().expect("start_time"),
            session_end_time: option("end_time").parse().expect("end_time"),
            session_lap_count: 0,
            session_record_count: 0,
        },
        vars: VarTable::default(),
        buffer_offset: 0,
        buffer_length: 0,
        record_count: 0,
        session_info_bytes: body,
    }
}

fn check_cases(golden: &Value, path: &Path, out: &mut Vec<String>) -> usize {
    let data = std::fs::read(path).unwrap_or_else(|e| panic!("{}: {e}", path.display()));
    let source = golden["source"].as_str().unwrap_or("?");
    assert_eq!(
        golden["source_sha256"].as_str(),
        Some(sha256_hex(&data).as_str()),
        "{source} changed after its golden was emitted; re-run tools/emit_session_golden.py"
    );
    assert_eq!(
        golden["case_mtime"]["float"], "1500000000.25",
        "case mtime changed in the emitter"
    );
    let platform = golden["platform"].as_str().unwrap_or_default();
    let cases = split_cases(&data);
    let expected = golden["cases"].as_array().cloned().unwrap_or_default();
    assert_eq!(cases.len(), expected.len(), "{source}: case count");
    for ((name, options, body), golden) in cases.into_iter().zip(&expected) {
        assert_eq!(
            golden["name"].as_str(),
            Some(name.as_str()),
            "{source}: case order"
        );
        let context = format!("{source}#{name}");
        let file = case_file(body, &options, path);
        if golden["outcome"] == "error" {
            match file.session_info() {
                Ok(_) => out.push(format!(
                    "{context}: python rejected the text, rust parsed it"
                )),
                Err(error) => check_error(&context, &golden["error"], &error, out),
            }
        } else {
            check_session(&context, golden, &file, platform, out);
        }
    }
    expected.len()
}

fn print_literals(golden: &Value) {
    let mut literals: Vec<String> = Vec::new();
    let mut collect = |record: &Value| {
        if let Some(items) = record["literals"].as_array() {
            literals.extend(items.iter().filter_map(Value::as_str).map(str::to_owned));
        }
    };
    match golden["cases"].as_array() {
        Some(cases) => cases.iter().for_each(&mut collect),
        None => collect(golden),
    }
    println!(
        "{}: {} quoted or flow value(s){}",
        golden["source"].as_str().unwrap_or("?"),
        literals.len(),
        if literals.is_empty() {
            String::new()
        } else {
            format!(": {literals:?}")
        }
    );
}

fn assert_clean(out: Vec<String>) {
    assert!(
        out.is_empty(),
        "{} difference(s) from the Python oracle:\n{}",
        out.len(),
        out.join("\n")
    );
}

#[test]
fn committed_fixtures_match_python() {
    let goldens = goldens(&crate_dir().join("tests").join("golden"), ".golden.json");
    let mut out = Vec::new();
    let mut checked = Vec::new();
    for golden_path in goldens {
        let golden = read_json(&golden_path);
        let source = golden["source"].as_str().expect("source").to_owned();
        let path = repository().join(&source);
        match golden["kind"].as_str() {
            Some("ibt") => check_ibt(&golden, &path, &mut out),
            Some("cases") => {
                check_cases(&golden, &path, &mut out);
            }
            other => panic!("{}: unknown kind {other:?}", golden_path.display()),
        }
        print_literals(&golden);
        checked.push(source);
    }
    for required in [
        "test-data/ibt/synthetic-race.ibt",
        "test-data/ibt/truncated-race.ibt",
    ] {
        assert!(
            checked.iter().any(|s| s == required),
            "no golden for {required}"
        );
    }
    assert_clean(out);
}

#[test]
fn yaml_cases_match_python() {
    let dir = crate_dir().join("tests").join("golden").join("yaml");
    let mut out = Vec::new();
    let mut sources = Vec::new();
    let mut total = 0;
    for golden_path in goldens(&dir, ".cases.golden.json") {
        let golden = read_json(&golden_path);
        let source = golden["source"].as_str().expect("source").to_owned();
        total += check_cases(&golden, &repository().join(&source), &mut out);
        print_literals(&golden);
        sources.push(source);
    }
    let case_dir = crate_dir().join("tests").join("yaml-cases");
    for entry in std::fs::read_dir(&case_dir)
        .expect("yaml-cases")
        .filter_map(Result::ok)
    {
        let name = entry.file_name().to_string_lossy().into_owned();
        let label = format!("crates/coach-ibt/tests/yaml-cases/{name}");
        assert!(
            sources.contains(&label),
            "{label} has no golden; run tools/emit_session_golden.py"
        );
    }
    assert!(total >= 50, "only {total} cases checked");
    assert_clean(out);
}

#[test]
#[ignore = "needs COACH_PRIVATE_CORPUS (a folder of private .ibt recordings) and the Python oracle"]
fn private_corpus_matches_python() {
    let corpus = PathBuf::from(
        std::env::var_os("COACH_PRIVATE_CORPUS")
            .expect("set COACH_PRIVATE_CORPUS to a folder of .ibt recordings"),
    );
    let golden_dir = match std::env::var_os("COACH_PRIVATE_GOLDEN") {
        Some(dir) => PathBuf::from(dir),
        None => {
            let out = PathBuf::from(env!("CARGO_TARGET_TMPDIR")).join("private-session-golden");
            if out.exists() {
                std::fs::remove_dir_all(&out).expect("clear previous private goldens");
            }
            let python = std::env::var_os("COACH_PYTHON").unwrap_or_else(|| DEFAULT_PYTHON.into());
            let emitter = crate_dir().join("tools").join("emit_session_golden.py");
            let status = Command::new(&python)
                .args(["-X", "utf8", "-W", "ignore"])
                .arg(&emitter)
                .arg(&corpus)
                .arg(&out)
                .status()
                .unwrap_or_else(|e| {
                    panic!(
                        "cannot run {}: {e} (set COACH_PYTHON)",
                        python.to_string_lossy()
                    )
                });
            assert!(status.success(), "emit_session_golden.py failed");
            out
        }
    };
    let mut out = Vec::new();
    let mut recordings = 0;
    for golden_path in goldens(&golden_dir, ".golden.json") {
        let golden = read_json(&golden_path);
        if golden["kind"] != "ibt" {
            continue;
        }
        let source = golden["source"].as_str().expect("source");
        let path = [corpus.join(source), repository().join(source)]
            .into_iter()
            .find(|path| path.is_file())
            .unwrap_or_else(|| panic!("{source} not found under {}", corpus.display()));
        check_ibt(&golden, &path, &mut out);
        print_literals(&golden);
        recordings += 1;
    }
    println!("{recordings} private recording(s) checked");
    assert!(
        recordings > 0,
        "no .ibt recordings under {}",
        corpus.display()
    );
    assert_clean(out);
}

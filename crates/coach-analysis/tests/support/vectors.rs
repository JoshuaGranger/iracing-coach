//! Replay of the oracle's recorded kernel calls (`docs/08-rewrite/port/phase2-plan.md` section
//! 3, "Rust replay test").
//!
//! Vector roots, each holding `<recording>/table.ccs`, `table.json` and
//! `<unit>/<module>.<entry>/<call>.json` (`.ccs`) with `<unit>/index.json`:
//!
//! - `tests/vectors/kernels/<platform>/` (committed fixture and synthetic tables);
//! - `$COACH_KERNEL_VECTORS/<platform>/` when the variable is set (the private corpus, local
//!   only); a relative path is taken from the repository root.
//!
//! `<platform>` is this build's (`windows` or `linux`): a test compares against the vectors its
//! own platform's interpreter wrote. For every call of a unit, [`replay_unit`] decodes the
//! arguments, runs the native entry through `Session::call`, and compares the result (or the
//! raised class and message), every argument afterwards (the recorded `mutated` value, else the
//! value on entry) and the accessed channels exactly. The first difference fails the test with
//! the recording, entry, call number and value path.

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

use coach_analysis::kernels::{
    Answer, Call, Outcome, PyValue, Session, UNITS, VectorStore, codec, same,
};
use sha2::{Digest, Sha256};

pub fn platform() -> &'static str {
    if cfg!(windows) { "windows" } else { "linux" }
}

pub fn crate_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

pub fn repository() -> PathBuf {
    crate_dir().join("..").join("..")
}

/// The vector roots for this platform, committed first; missing roots are skipped by callers.
pub fn roots() -> Vec<PathBuf> {
    let mut roots = vec![
        crate_dir()
            .join("tests")
            .join("vectors")
            .join("kernels")
            .join(platform()),
    ];
    if let Some(extra) = std::env::var_os("COACH_KERNEL_VECTORS") {
        let extra = PathBuf::from(extra);
        let extra = if extra.is_absolute() {
            extra
        } else {
            repository().join(extra)
        };
        roots.push(extra.join(platform()));
    }
    roots
}

fn sorted_dirs(folder: &Path) -> Vec<PathBuf> {
    let mut dirs: Vec<PathBuf> = std::fs::read_dir(folder)
        .map(|entries| {
            entries
                .filter_map(Result::ok)
                .map(|entry| entry.path())
                .filter(|path| path.is_dir())
                .collect()
        })
        .unwrap_or_default();
    dirs.sort();
    dirs
}

fn name_of(path: &Path) -> String {
    path.file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_default()
}

/// Recording folders (those with a `table.json`) under every root, in name order per root.
pub fn recordings() -> Vec<PathBuf> {
    roots()
        .iter()
        .flat_map(|root| sorted_dirs(root))
        .filter(|folder| folder.join("table.json").is_file())
        .collect()
}

/// The call documents of one unit folder: `(entry label, file)`, entries and calls in name order
/// (`probes/` is not calls).
pub fn call_files(unit_dir: &Path) -> Vec<(String, PathBuf)> {
    let mut out = Vec::new();
    for entry_dir in sorted_dirs(unit_dir) {
        let entry = name_of(&entry_dir);
        if entry == "probes" {
            continue;
        }
        let mut files: Vec<PathBuf> = std::fs::read_dir(&entry_dir)
            .map(|entries| {
                entries
                    .filter_map(Result::ok)
                    .map(|e| e.path())
                    .filter(|p| p.extension().is_some_and(|x| x == "json"))
                    .collect()
            })
            .unwrap_or_default();
        files.sort();
        out.extend(files.into_iter().map(|file| (entry.clone(), file)));
    }
    out
}

fn sha256_hex(bytes: &[u8]) -> String {
    Sha256::digest(bytes)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

fn files_under(folder: &Path, out: &mut Vec<PathBuf>) {
    let Ok(entries) = std::fs::read_dir(folder) else {
        return;
    };
    for entry in entries.filter_map(Result::ok) {
        let path = entry.path();
        if path.is_dir() {
            files_under(&path, out);
        } else if name_of(&path) != "index.json" {
            out.push(path);
        }
    }
}

/// Checks `<unit>/index.json`: exactly the files present, each with its SHA-256, and per entry
/// as many call documents as `calls` counts. Returns the counts.
pub fn verify_index(unit_dir: &Path) -> Result<BTreeMap<String, i64>, String> {
    let at = |message: String| format!("{}: {message}", unit_dir.join("index.json").display());
    let text =
        std::fs::read_to_string(unit_dir.join("index.json")).map_err(|e| at(e.to_string()))?;
    let index = codec::decode(&text, None).map_err(|e| at(e.to_string()))?;
    let listed = index
        .get("files")
        .and_then(PyValue::as_dict)
        .ok_or_else(|| at("no files map".into()))?;
    let mut present = Vec::new();
    files_under(unit_dir, &mut present);
    let mut names = BTreeSet::new();
    for path in &present {
        let relative = path
            .strip_prefix(unit_dir)
            .map_err(|e| at(e.to_string()))?
            .components()
            .map(|c| c.as_os_str().to_string_lossy().into_owned())
            .collect::<Vec<_>>()
            .join("/");
        let expected = listed
            .get(&relative)
            .and_then(PyValue::as_str)
            .ok_or_else(|| at(format!("{relative} is not listed")))?;
        let bytes = std::fs::read(path).map_err(|e| at(e.to_string()))?;
        if sha256_hex(&bytes) != expected {
            return Err(at(format!("{relative}: SHA-256 differs from the index")));
        }
        names.insert(relative);
    }
    if names.len() != listed.len() {
        return Err(at(format!(
            "{} files listed, {} present",
            listed.len(),
            names.len()
        )));
    }
    let calls = index
        .get("calls")
        .and_then(PyValue::as_dict)
        .ok_or_else(|| at("no calls map".into()))?;
    let files = call_files(unit_dir);
    let mut counts = BTreeMap::new();
    for (label, count) in calls.iter() {
        let (Some(label), Some(count)) = (label.as_str(), count.as_int()) else {
            return Err(at("malformed calls map".into()));
        };
        let found = files.iter().filter(|(entry, _)| entry == label).count();
        if usize::try_from(count).ok() != Some(found) {
            return Err(at(format!(
                "{label}: index counts {count} calls, {found} present"
            )));
        }
        counts.insert(label.to_owned(), count);
    }
    if let Some((entry, _)) = files.iter().find(|(entry, _)| !counts.contains_key(entry)) {
        return Err(at(format!("{entry} has call files but no count")));
    }
    Ok(counts)
}

/// A call document's text, decoded, with its vector store when one exists.
pub struct Document {
    pub text: String,
    pub value: PyValue,
    pub store: Option<Vec<u8>>,
}

pub fn load_document(json: &Path) -> Result<Document, String> {
    let at = |message: String| format!("{}: {message}", json.display());
    let text = std::fs::read_to_string(json).map_err(|e| at(e.to_string()))?;
    let ccs = json.with_extension("ccs");
    let store = if ccs.is_file() {
        Some(std::fs::read(&ccs).map_err(|e| at(e.to_string()))?)
    } else {
        None
    };
    let columns = store
        .as_deref()
        .map(VectorStore::parse)
        .transpose()
        .map_err(|e| at(format!("vector store: {e}")))?;
    let value = codec::decode(text.strip_suffix('\n').unwrap_or(&text), columns.as_ref())
        .map_err(|e| at(e.to_string()))?;
    Ok(Document { text, value, store })
}

/// What the oracle did.
#[derive(Clone, Debug)]
pub enum Recorded {
    Value(PyValue),
    Raises { class: String, message: String },
}

/// One recorded call.
pub struct CallVector {
    pub recording: String,
    pub entry: String,
    pub number: i64,
    pub file: PathBuf,
    pub call: Call,
    pub recorded: Recorded,
    pub mutated: Vec<(String, PyValue)>,
    pub accessed: BTreeSet<String>,
}

impl CallVector {
    pub fn label(&self) -> String {
        format!("{} {} call {}", self.recording, self.entry, self.number)
    }
}

/// Reads and validates one call document: codec 1, the entry and call number its path names,
/// `args` a list and `kwargs` a dict, exactly one of `result` and `raises`, `mutated` naming
/// existing arguments, `accessed` a sorted list of distinct names.
pub fn load_call(recording: &str, entry: &str, json: &Path) -> Result<CallVector, String> {
    let document = load_document(json)?;
    let at = |message: &str| format!("{}: {message}", json.display());
    let value = document.value;
    if value.get("codec").and_then(PyValue::as_int) != Some(codec::CODEC_VERSION) {
        return Err(at("not codec version 1"));
    }
    if value.get("entry").and_then(PyValue::as_str) != Some(entry) {
        return Err(at("entry differs from its folder"));
    }
    let number = value
        .get("call")
        .and_then(PyValue::as_int)
        .ok_or_else(|| at("no call number"))?;
    let stem = json
        .file_stem()
        .map(|s| s.to_string_lossy().into_owned())
        .unwrap_or_default();
    if stem.parse::<i64>().ok() != Some(number) {
        return Err(at("call number differs from its file name"));
    }
    let args = value.get("args").cloned().ok_or_else(|| at("no args"))?;
    let kwargs = value
        .get("kwargs")
        .cloned()
        .ok_or_else(|| at("no kwargs"))?;
    let call = Call::from_values(args, kwargs).map_err(|e| at(&e.to_string()))?;
    let recorded = match (value.get("result"), value.get("raises")) {
        (Some(result), None) => Recorded::Value(result.clone()),
        (None, Some(raises)) => {
            let class = raises.get("class").and_then(PyValue::as_str);
            let message = raises.get("message").and_then(PyValue::as_str);
            let (Some(class), Some(message)) = (class, message) else {
                return Err(at("malformed raises"));
            };
            Recorded::Raises {
                class: class.to_owned(),
                message: message.to_owned(),
            }
        }
        _ => return Err(at("needs exactly one of result and raises")),
    };
    let mut mutated = Vec::new();
    if let Some(map) = value.get("mutated") {
        let map = map.as_dict().ok_or_else(|| at("mutated is not a dict"))?;
        for (key, after) in map.iter() {
            let key = key.as_str().ok_or_else(|| at("mutated key is not a str"))?;
            if call.argument(key).is_none() {
                return Err(at(&format!(
                    "mutated names {key}, which the call does not have"
                )));
            }
            mutated.push((key.to_owned(), after.clone()));
        }
    }
    let mut accessed = BTreeSet::new();
    if let Some(list) = value.get("accessed") {
        let list = list.as_list().ok_or_else(|| at("accessed is not a list"))?;
        let names: Vec<&str> = list.iter().filter_map(PyValue::as_str).collect();
        if names.len() != list.len() || names.windows(2).any(|pair| pair[0] >= pair[1]) {
            return Err(at("accessed is not a sorted list of distinct names"));
        }
        accessed.extend(names.into_iter().map(str::to_owned));
    }
    Ok(CallVector {
        recording: recording.to_owned(),
        entry: entry.to_owned(),
        number,
        file: json.to_path_buf(),
        call,
        recorded,
        mutated,
        accessed,
    })
}

/// Compares what the native entry did with the record: `after` is the call's arguments once
/// the entry returned.
pub fn check(vector: &CallVector, after: &Call, answer: &Answer) -> Result<(), String> {
    let label = vector.label();
    match (&vector.recorded, &answer.outcome) {
        (Recorded::Value(expected), Outcome::Value(actual)) => {
            if let Some(difference) = same(expected, actual) {
                return Err(format!("{label}: result {difference}"));
            }
        }
        (
            Recorded::Raises { class, message },
            Outcome::Raises {
                class: actual_class,
                message: actual_message,
            },
        ) => {
            if class != actual_class || message != actual_message {
                return Err(format!(
                    "{label}: oracle raised {class}: {message:?}, native raised {actual_class}: {actual_message:?}"
                ));
            }
        }
        (_, Outcome::Error(error)) => return Err(format!("{label}: native error: {error}")),
        (Recorded::Value(_), Outcome::Raises { class, message }) => {
            return Err(format!(
                "{label}: oracle returned, native raised {class}: {message:?}"
            ));
        }
        (Recorded::Raises { class, .. }, Outcome::Value(_)) => {
            return Err(format!(
                "{label}: oracle raised {class}, native returned a value"
            ));
        }
    }
    let before = vector.call.arguments();
    let now = after.arguments();
    let keys =
        |list: &[(String, &PyValue)]| list.iter().map(|(k, _)| k.clone()).collect::<Vec<_>>();
    if keys(&before) != keys(&now) {
        return Err(format!(
            "{label}: the entry changed the argument list itself"
        ));
    }
    for (key, value) in now {
        let expected = vector
            .mutated
            .iter()
            .find(|(k, _)| *k == key)
            .map(|(_, v)| v)
            .or_else(|| vector.call.argument(&key))
            .ok_or_else(|| format!("{label}: argument {key} missing"))?;
        if let Some(difference) = same(expected, value) {
            let how = if vector.mutated.iter().any(|(k, _)| *k == key) {
                "mutated"
            } else {
                "unchanged"
            };
            return Err(format!(
                "{label}: argument {key} (oracle: {how}) {difference}"
            ));
        }
    }
    if answer.accessed != vector.accessed {
        let missing: Vec<&String> = vector.accessed.difference(&answer.accessed).collect();
        let extra: Vec<&String> = answer.accessed.difference(&vector.accessed).collect();
        return Err(format!(
            "{label}: accessed channels differ: missing {missing:?}, extra {extra:?}"
        ));
    }
    Ok(())
}

/// Calls compared per recording and entry.
pub type Counts = BTreeMap<(String, String), usize>;

/// Replays every recorded call of `unit` under every root and fails at the first difference.
/// A recorded entry that the unit's `ENTRIES` does not provide fails; a unit with no vectors
/// passes with zero calls (reported).
pub fn replay_unit(unit: &str) -> Counts {
    let provided: Vec<&str> = UNITS
        .iter()
        .find(|(name, _)| *name == unit)
        .unwrap_or_else(|| panic!("unit {unit} is not in dispatch::UNITS"))
        .1
        .iter()
        .map(|entry| entry.name)
        .collect();
    let mut counts = Counts::new();
    for folder in recordings() {
        let unit_dir = folder.join(unit);
        if !unit_dir.is_dir() {
            continue;
        }
        let recording = name_of(&folder);
        verify_index(&unit_dir).unwrap_or_else(|e| panic!("{e}"));
        let session = Session::open_vector_folder(&folder).unwrap_or_else(|e| panic!("{e}"));
        for (entry, file) in call_files(&unit_dir) {
            assert!(
                provided.contains(&entry.as_str()),
                "{recording}: {entry} is recorded under unit {unit} but {unit}::ENTRIES does not provide it"
            );
            let vector = load_call(&recording, &entry, &file).unwrap_or_else(|e| panic!("{e}"));
            let mut call = vector.call.clone();
            let answer = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                session.call(&entry, &mut call)
            }))
            .unwrap_or_else(|_| panic!("{}: the entry panicked", vector.label()));
            if let Err(failure) = check(&vector, &call, &answer) {
                panic!("{failure}");
            }
            *counts.entry((recording.clone(), entry)).or_default() += 1;
        }
    }
    let total: usize = counts.values().sum();
    println!("{unit}: {total} calls identical ({} platform)", platform());
    for ((recording, entry), n) in &counts {
        println!("  {recording} {entry}: {n}");
    }
    counts
}

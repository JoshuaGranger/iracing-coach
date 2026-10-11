//! Directory listings for the uploader: recordings under `Documents\iRacing\telemetry` and setup
//! files under `Documents\iRacing\setups`. Metadata only, read-only; nothing under
//! `Documents\iRacing` is ever written, renamed or deleted. Every listing asks `proceed` before
//! each directory read and each entry, so a scan stops within one metadata call of the on-track
//! gate closing (`collector-protocol.md` section 4); an interrupted listing is returned as such,
//! never as a short one.

use std::fs;
use std::path::{Path, PathBuf};
use std::time::UNIX_EPOCH;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Kind {
    Ibt,
    Sto,
    Htm,
    Field,
}

impl Kind {
    pub fn as_str(self) -> &'static str {
        match self {
            Kind::Ibt => "ibt",
            Kind::Sto => "sto",
            Kind::Htm => "htm",
            Kind::Field => "field",
        }
    }

    pub fn parse(s: &str) -> Option<Kind> {
        match s {
            "ibt" => Some(Kind::Ibt),
            "sto" => Some(Kind::Sto),
            "htm" => Some(Kind::Htm),
            "field" => Some(Kind::Field),
            _ => None,
        }
    }

    pub fn of_path(path: &Path) -> Option<Kind> {
        let ext = path.extension()?.to_str()?.to_ascii_lowercase();
        match ext.as_str() {
            "ibt" => Some(Kind::Ibt),
            "sto" => Some(Kind::Sto),
            "htm" => Some(Kind::Htm),
            "jsonl" if path.file_name()?.to_str()?.ends_with(".field.jsonl") => Some(Kind::Field),
            _ => None,
        }
    }
}

/// Only immutable, finalized field segments; active/crash partial files stay local.
pub fn scan_fields(dir: &Path, proceed: &dyn Fn() -> bool) -> Result<Vec<Candidate>, Interrupted> {
    if !proceed() {
        return Err(Interrupted);
    }
    let Ok(entries) = fs::read_dir(dir) else {
        return Ok(Vec::new());
    };
    let mut out = Vec::new();
    for entry in entries.flatten() {
        if !proceed() {
            return Err(Interrupted);
        }
        let path = entry.path();
        if Kind::of_path(&path) == Some(Kind::Field)
            && let Some(c) = candidate(path, Kind::Field, None)
        {
            out.push(c);
        }
    }
    Ok(newest_first(out))
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Candidate {
    pub path: PathBuf,
    pub size: u64,
    pub mtime_ms: u64,
    pub kind: Kind,
    /// For setup files: path under `setups`, forward slashes.
    pub relative_path: Option<String>,
}

impl Candidate {
    pub fn key(&self) -> String {
        self.path.to_string_lossy().into_owned()
    }

    pub fn file_name(&self) -> String {
        self.path
            .file_name()
            .map(|n| n.to_string_lossy().into_owned())
            .unwrap_or_default()
    }
}

/// The check said stop: the listing is incomplete.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Interrupted;

fn candidate(path: PathBuf, kind: Kind, relative_path: Option<String>) -> Option<Candidate> {
    let meta = fs::metadata(&path).ok()?;
    // The upload API takes files of at least one byte (a recording being created is 0 bytes).
    if !meta.is_file() || meta.len() == 0 {
        return None;
    }
    let mtime_ms = meta
        .modified()
        .ok()?
        .duration_since(UNIX_EPOCH)
        .ok()?
        .as_millis() as u64;
    Some(Candidate {
        path,
        size: meta.len(),
        mtime_ms,
        kind,
        relative_path,
    })
}

/// Every `.ibt` directly in `dir`, newest first.
pub fn scan_recordings(
    dir: &Path,
    proceed: &dyn Fn() -> bool,
) -> Result<Vec<Candidate>, Interrupted> {
    if !proceed() {
        return Err(Interrupted);
    }
    let Ok(entries) = fs::read_dir(dir) else {
        return Ok(Vec::new());
    };
    let mut out = Vec::new();
    for entry in entries.flatten() {
        if !proceed() {
            return Err(Interrupted);
        }
        let path = entry.path();
        if Kind::of_path(&path) == Some(Kind::Ibt)
            && let Some(c) = candidate(path, Kind::Ibt, None)
        {
            out.push(c);
        }
    }
    Ok(newest_first(out))
}

/// Every `.ibt` at any depth below each of `roots` (backfill: the legacy raw archive keeps one
/// folder per recording hash), newest first, each file once however many roots reach it.
pub fn scan_recordings_below(
    roots: &[PathBuf],
    proceed: &dyn Fn() -> bool,
) -> Result<Vec<Candidate>, Interrupted> {
    let mut found = Vec::new();
    for root in roots {
        walk_recordings(root, 0, proceed, &mut found)?;
    }
    let mut seen = std::collections::BTreeSet::new();
    found.retain(|c| seen.insert(c.key().to_lowercase()));
    Ok(newest_first(found))
}

fn walk_recordings(
    dir: &Path,
    depth: usize,
    proceed: &dyn Fn() -> bool,
    out: &mut Vec<Candidate>,
) -> Result<(), Interrupted> {
    if depth > 8 {
        return Ok(());
    }
    if !proceed() {
        return Err(Interrupted);
    }
    let Ok(entries) = fs::read_dir(dir) else {
        return Ok(());
    };
    for entry in entries.flatten() {
        if !proceed() {
            return Err(Interrupted);
        }
        let path = entry.path();
        let Ok(kind) = entry.file_type() else {
            continue;
        };
        if kind.is_dir() {
            walk_recordings(&path, depth + 1, proceed, out)?;
        } else if Kind::of_path(&path) == Some(Kind::Ibt)
            && let Some(c) = candidate(path, Kind::Ibt, None)
        {
            out.push(c);
        }
    }
    Ok(())
}

/// The name the recording had where iRacing wrote it, from the `manifest.json` that the legacy
/// raw archive keeps beside it (`original_path`, else a `source_discoveries[].source_path`, as a
/// `.ibt` leaf); None when there is no manifest or it names no recording.
pub fn archived_name(recording: &Path) -> Option<String> {
    let text = fs::read_to_string(recording.parent()?.join("manifest.json")).ok()?;
    let manifest: serde_json::Value =
        serde_json::from_str(text.trim_start_matches('\u{feff}')).ok()?;
    let sources = manifest.get("original_path").into_iter().chain(
        manifest
            .get("source_discoveries")
            .and_then(|d| d.as_array())
            .into_iter()
            .flatten()
            .filter_map(|d| d.get("source_path")),
    );
    sources
        .filter_map(|v| v.as_str())
        .filter_map(|p| p.rsplit(['/', '\\']).next())
        .find(|leaf| leaf.to_ascii_lowercase().ends_with(".ibt"))
        .map(str::to_string)
}

/// Newest first, ties by path. A map instead of `sort_by`: sorting a Vec of these entries costs
/// far more code than the function (the executable is held to 3 MB).
fn newest_first(list: Vec<Candidate>) -> Vec<Candidate> {
    list.into_iter()
        .map(|c| ((std::cmp::Reverse(c.mtime_ms), c.path.clone()), c))
        .collect::<std::collections::BTreeMap<_, _>>()
        .into_values()
        .collect()
}

/// Every `.sto` and `.htm` below `dir`, newest first. Files without an extension (the `-Current-`
/// files iRacing rewrites) are not setup files.
pub fn scan_setups(dir: &Path, proceed: &dyn Fn() -> bool) -> Result<Vec<Candidate>, Interrupted> {
    let mut out = Vec::new();
    walk(dir, dir, 0, proceed, &mut out)?;
    Ok(newest_first(out))
}

fn walk(
    root: &Path,
    dir: &Path,
    depth: usize,
    proceed: &dyn Fn() -> bool,
    out: &mut Vec<Candidate>,
) -> Result<(), Interrupted> {
    if depth > 8 {
        return Ok(());
    }
    if !proceed() {
        return Err(Interrupted);
    }
    let Ok(entries) = fs::read_dir(dir) else {
        return Ok(());
    };
    for entry in entries.flatten() {
        if !proceed() {
            return Err(Interrupted);
        }
        let path = entry.path();
        let Ok(kind) = entry.file_type() else {
            continue;
        };
        if kind.is_dir() {
            walk(root, &path, depth + 1, proceed, out)?;
        } else if let Some(k @ (Kind::Sto | Kind::Htm)) = Kind::of_path(&path) {
            let relative = path
                .strip_prefix(root)
                .ok()
                .map(|p| p.to_string_lossy().replace('\\', "/"));
            if let Some(c) = candidate(path, k, relative) {
                out.push(c);
            }
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::util::uuid_v4;

    #[test]
    fn lists_recordings_and_setups_with_relative_paths() {
        let root = std::env::temp_dir().join(format!("coach-scan-{}", uuid_v4()));
        let tel = root.join("telemetry");
        let setups = root.join("setups/stockcars2 supra2019/shop");
        fs::create_dir_all(&tel).unwrap();
        fs::create_dir_all(&setups).unwrap();
        fs::write(tel.join("a.ibt"), b"1").unwrap();
        fs::write(tel.join("note.txt"), b"1").unwrap();
        fs::write(root.join("setups/stockcars2 supra2019/-Current-"), b"x").unwrap();
        fs::write(root.join("setups/stockcars2 supra2019/mine.STO"), b"2").unwrap();
        fs::write(setups.join("r.htm"), b"3").unwrap();
        let rec = scan_recordings(&tel, &|| true).unwrap();
        assert_eq!(rec.len(), 1);
        assert_eq!(rec[0].kind, Kind::Ibt);
        let set = scan_setups(&root.join("setups"), &|| true).unwrap();
        let mut rel: Vec<_> = set
            .iter()
            .map(|c| c.relative_path.clone().unwrap())
            .collect();
        rel.sort();
        assert_eq!(
            rel,
            [
                "stockcars2 supra2019/mine.STO",
                "stockcars2 supra2019/shop/r.htm"
            ]
        );
        assert!(
            scan_recordings(&root.join("missing"), &|| true)
                .unwrap()
                .is_empty()
        );
        fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn a_scan_stops_part_way_when_the_check_says_stop() {
        use std::cell::Cell;
        let root = std::env::temp_dir().join(format!("coach-scan-{}", uuid_v4()));
        let setups = root.join("setups");
        for car in 0..6 {
            let dir = setups.join(format!("car{car}/shop"));
            fs::create_dir_all(&dir).unwrap();
            fs::write(dir.join("a.sto"), b"1").unwrap();
        }
        let tel = root.join("telemetry");
        fs::create_dir_all(&tel).unwrap();
        for n in 0..4 {
            fs::write(tel.join(format!("{n}.ibt")), b"1").unwrap();
        }
        // Every call to the check is counted; the gate closes after `budget` of them.
        let calls = Cell::new(0usize);
        let stops_after = |budget: usize| {
            calls.set(0);
            let calls = &calls;
            move || {
                calls.set(calls.get() + 1);
                calls.get() <= budget
            }
        };
        let full = {
            let proceed = stops_after(usize::MAX);
            assert_eq!(scan_setups(&setups, &proceed).unwrap().len(), 6);
            calls.get()
        };
        assert!(
            full > 12,
            "the walk asks per directory and per entry: {full}"
        );
        for budget in [0, 1, 5, full - 1] {
            let proceed = stops_after(budget);
            assert_eq!(
                scan_setups(&setups, &proceed),
                Err(Interrupted),
                "setups, closed after {budget} checks"
            );
            assert!(calls.get() <= budget + 1, "it stops at the first refusal");
        }
        let proceed = stops_after(2);
        assert_eq!(scan_recordings(&tel, &proceed), Err(Interrupted));
        assert_eq!(scan_recordings(&tel, &|| true).unwrap().len(), 4);
        fs::remove_dir_all(&root).unwrap();
    }
}

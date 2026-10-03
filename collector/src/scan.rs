//! Directory listings for the uploader: recordings under `Documents\iRacing\telemetry` and setup
//! files under `Documents\iRacing\setups`. Metadata only, read-only; nothing under
//! `Documents\iRacing` is ever written, renamed or deleted.

use std::fs;
use std::path::{Path, PathBuf};
use std::time::UNIX_EPOCH;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Kind {
    Ibt,
    Sto,
    Htm,
}

impl Kind {
    pub fn as_str(self) -> &'static str {
        match self {
            Kind::Ibt => "ibt",
            Kind::Sto => "sto",
            Kind::Htm => "htm",
        }
    }

    pub fn parse(s: &str) -> Option<Kind> {
        match s {
            "ibt" => Some(Kind::Ibt),
            "sto" => Some(Kind::Sto),
            "htm" => Some(Kind::Htm),
            _ => None,
        }
    }

    pub fn of_path(path: &Path) -> Option<Kind> {
        let ext = path.extension()?.to_str()?.to_ascii_lowercase();
        match ext.as_str() {
            "ibt" => Some(Kind::Ibt),
            "sto" => Some(Kind::Sto),
            "htm" => Some(Kind::Htm),
            _ => None,
        }
    }
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
pub fn scan_recordings(dir: &Path) -> Vec<Candidate> {
    let Ok(entries) = fs::read_dir(dir) else {
        return Vec::new();
    };
    let out: Vec<Candidate> = entries
        .flatten()
        .filter_map(|e| {
            let path = e.path();
            (Kind::of_path(&path) == Some(Kind::Ibt))
                .then(|| candidate(path, Kind::Ibt, None))
                .flatten()
        })
        .collect();
    newest_first(out)
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
pub fn scan_setups(dir: &Path) -> Vec<Candidate> {
    let mut out = Vec::new();
    walk(dir, dir, 0, &mut out);
    newest_first(out)
}

fn walk(root: &Path, dir: &Path, depth: usize, out: &mut Vec<Candidate>) {
    if depth > 8 {
        return;
    }
    let Ok(entries) = fs::read_dir(dir) else {
        return;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        let Ok(kind) = entry.file_type() else {
            continue;
        };
        if kind.is_dir() {
            walk(root, &path, depth + 1, out);
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
        let rec = scan_recordings(&tel);
        assert_eq!(rec.len(), 1);
        assert_eq!(rec[0].kind, Kind::Ibt);
        let set = scan_setups(&root.join("setups"));
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
        assert!(scan_recordings(&root.join("missing")).is_empty());
        fs::remove_dir_all(&root).unwrap();
    }
}

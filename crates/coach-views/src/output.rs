//! Writes the view files into a sibling temp directory, fsyncs them and renames it into place.

use std::fs::{self, File};
use std::io::{self, BufWriter, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

use sha2::{Digest, Sha256};

use crate::{Built, ProjectError, ViewFile, ViewKind, traces};

struct Hashing<W: Write> {
    inner: W,
    hasher: Sha256,
    bytes: u64,
}

impl<W: Write> Write for Hashing<W> {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        let n = self.inner.write(buf)?;
        self.hasher.update(&buf[..n]);
        self.bytes += n as u64;
        Ok(n)
    }

    fn flush(&mut self) -> io::Result<()> {
        self.inner.flush()
    }
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

fn write_file(
    dir: &Path,
    kind: ViewKind,
    name: &str,
    body: impl FnOnce(&mut dyn Write) -> io::Result<()>,
) -> io::Result<ViewFile> {
    let file = File::create(dir.join(name))?;
    let mut writer = Hashing {
        inner: BufWriter::with_capacity(1 << 20, file),
        hasher: Sha256::new(),
        bytes: 0,
    };
    body(&mut writer)?;
    writer.flush()?;
    let Hashing {
        inner,
        hasher,
        bytes,
    } = writer;
    let file = inner.into_inner().map_err(io::IntoInnerError::into_error)?;
    file.sync_all()?;
    Ok(ViewFile {
        kind,
        file_name: name.to_string(),
        bytes,
        sha256: hex(&hasher.finalize()),
    })
}

fn temp_sibling(out_dir: &Path) -> PathBuf {
    static COUNTER: AtomicU64 = AtomicU64::new(0);
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |d| d.as_nanos());
    let mut name = out_dir
        .file_name()
        .map(|n| n.to_os_string())
        .unwrap_or_default();
    name.push(format!(
        ".tmp-{:x}-{:x}-{:x}",
        std::process::id(),
        nanos,
        COUNTER.fetch_add(1, Ordering::Relaxed)
    ));
    out_dir.with_file_name(name)
}

/// Writes every view of `built` into `out_dir`, which must not exist.
pub(crate) fn write_all(out_dir: &Path, built: &Built) -> Result<Vec<ViewFile>, ProjectError> {
    if let Some(parent) = out_dir.parent().filter(|p| !p.as_os_str().is_empty()) {
        fs::create_dir_all(parent)?;
    }
    let tmp = temp_sibling(out_dir);
    fs::create_dir(&tmp)?;
    let written = write_into(&tmp, built);
    let files = match written {
        Ok(files) => files,
        Err(error) => {
            let _ = fs::remove_dir_all(&tmp);
            return Err(error.into());
        }
    };
    // Directories cannot be opened for sync on every platform; the files are already durable.
    if let Ok(dir) = File::open(&tmp) {
        let _ = dir.sync_all();
    }
    if let Err(error) = rename_with_retry(&tmp, out_dir) {
        let _ = fs::remove_dir_all(&tmp);
        return Err(if out_dir.exists() {
            ProjectError::OutputExists(out_dir.to_path_buf())
        } else {
            error.into()
        });
    }
    Ok(files)
}

/// Windows denies a directory rename while a scanner or indexer still holds a handle on a file
/// inside it; the denial clears within milliseconds, so retry briefly before giving up.
fn rename_with_retry(from: &Path, to: &Path) -> io::Result<()> {
    let mut attempt: u64 = 0;
    loop {
        match fs::rename(from, to) {
            Err(error) if error.kind() == io::ErrorKind::PermissionDenied && attempt < 20 => {
                attempt += 1;
                std::thread::sleep(std::time::Duration::from_millis(25 * attempt));
            }
            result => return result,
        }
    }
}

fn write_into(dir: &Path, built: &Built) -> io::Result<Vec<ViewFile>> {
    let mut files = Vec::new();
    for (kind, name, bytes) in &built.json_views {
        if *kind == ViewKind::Track || *kind == ViewKind::Tuning {
            continue;
        }
        files.push(write_file(dir, *kind, name, |w| w.write_all(bytes))?);
        if *kind == ViewKind::TraceCatalog {
            files.push(write_file(dir, ViewKind::Traces, "traces.ccol", |w| {
                traces::write_ccol(
                    w,
                    &built.envelope.analysis_id,
                    crate::PROJECTION_VERSION,
                    &built.prepared,
                    &built.traces,
                )
            })?);
        }
    }
    for (kind, name, bytes) in &built.json_views {
        if *kind == ViewKind::Track || *kind == ViewKind::Tuning {
            files.push(write_file(dir, *kind, name, |w| w.write_all(bytes))?);
        }
    }
    Ok(files)
}

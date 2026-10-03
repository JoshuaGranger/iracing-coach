//! The only code that touches the NAS (`nas-layout.md`). It runs on a dedicated OS thread so a
//! stalled soft NFS mount blocks archiving only. Every write is verified after flush and
//! committed by rename, manifest last.

use std::fs::{self, File, OpenOptions};
use std::io::{ErrorKind, Read, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicI64, AtomicU8, Ordering};
use std::sync::mpsc;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use serde_json::{Value, json};

use crate::error::{Error, Result};
use crate::faults::Faults;
use crate::util::{self, fsync_dir, iso_now, new_uuid, sha256_file};

pub const FORMAT_TEXT: &str = "iracing-coach-nas 1\n";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NasState {
    Ok,
    Readonly,
    Unreachable,
    Stalled,
}

impl NasState {
    pub fn as_str(self) -> &'static str {
        match self {
            NasState::Ok => "ok",
            NasState::Readonly => "readonly",
            NasState::Unreachable => "unreachable",
            NasState::Stalled => "stalled",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ArchiveKind {
    Recording,
    SetupFile,
}

#[derive(Debug, Clone)]
pub struct ArchiveSpec {
    pub sha256: String,
    pub size: u64,
    pub kind: ArchiveKind,
    /// `ibt`, `sto` or `htm`.
    pub extension: String,
    pub spool_path: PathBuf,
    /// Extra manifest fields (`original_name`, `driver`, `recording`, ...).
    pub manifest: Value,
}

enum Cmd {
    Probe(mpsc::SyncSender<NasState>),
    Archive(ArchiveSpec, mpsc::SyncSender<Result<()>>),
    Restage {
        sha256: String,
        kind: ArchiveKind,
        extension: String,
        dest: PathBuf,
        tmp_dir: PathBuf,
        /// Unix seconds the restaged file carries (the blob's clamped source mtime).
        mtime: Option<i64>,
        reply: mpsc::SyncSender<Result<()>>,
    },
    Backup {
        generation_dir: PathBuf,
        day: String,
        reply: mpsc::SyncSender<Result<bool>>,
    },
}

pub struct Archiver {
    tx: Mutex<mpsc::Sender<Cmd>>,
    state: AtomicU8,
    last_ok: AtomicI64,
    op_started: AtomicI64,
    stall_after: Duration,
    nas_root: PathBuf,
}

fn state_from_error(e: &std::io::Error, writing: bool) -> NasState {
    match e.kind() {
        ErrorKind::ReadOnlyFilesystem => NasState::Readonly,
        ErrorKind::PermissionDenied if writing => NasState::Readonly,
        _ => NasState::Unreachable,
    }
}

impl Archiver {
    /// Starts the archiver thread, creates the layout and runs crash recovery.
    pub fn start(nas_root: PathBuf, faults: Arc<Faults>, stall_after: Duration) -> Arc<Archiver> {
        let (tx, rx) = mpsc::channel::<Cmd>();
        let archiver = Arc::new(Archiver {
            tx: Mutex::new(tx),
            state: AtomicU8::new(2),
            last_ok: AtomicI64::new(0),
            op_started: AtomicI64::new(0),
            stall_after,
            nas_root,
        });
        let worker = archiver.clone();
        let (ready_tx, ready_rx) = mpsc::sync_channel::<()>(1);
        std::thread::Builder::new()
            .name("archiver".into())
            .spawn(move || {
                let core = Core {
                    root: worker.nas_root.clone(),
                    faults,
                };
                let _ = worker.run_op(|| core.recover());
                let _ = ready_tx.send(());
                loop {
                    let cmd = match rx.recv_timeout(Duration::from_secs(1)) {
                        Ok(cmd) => cmd,
                        Err(mpsc::RecvTimeoutError::Timeout) => {
                            // The handle held by the app is gone: nothing can send any more.
                            if Arc::strong_count(&worker) == 1 {
                                break;
                            }
                            continue;
                        }
                        Err(mpsc::RecvTimeoutError::Disconnected) => break,
                    };
                    match cmd {
                        Cmd::Probe(reply) => {
                            let _ = worker.run_op(|| core.probe());
                            let _ = reply.send(worker.state());
                        }
                        Cmd::Archive(spec, reply) => {
                            let r = worker.run_op(|| core.archive(&spec));
                            let _ = reply.send(r);
                        }
                        Cmd::Restage {
                            sha256,
                            kind,
                            extension,
                            dest,
                            tmp_dir,
                            mtime,
                            reply,
                        } => {
                            let r = worker.run_op(|| {
                                core.restage(&sha256, kind, &extension, &dest, &tmp_dir, mtime)
                            });
                            let _ = reply.send(r);
                        }
                        Cmd::Backup {
                            generation_dir,
                            day,
                            reply,
                        } => {
                            let r = worker.run_op(|| core.backup(&generation_dir, &day));
                            let _ = reply.send(r);
                        }
                    }
                }
            })
            .expect("spawn archiver thread");
        // Start-up recovery and the first probe finish before the first status request in the
        // normal case; an unreachable share must not hold the service back, so the wait is bounded.
        let _ = ready_rx.recv_timeout(Duration::from_secs(2));
        archiver
    }

    /// Runs one NAS operation, tracking stall time and the resulting state.
    fn run_op<T>(&self, op: impl FnOnce() -> Result<T>) -> Result<T> {
        self.op_started
            .store(util::unix_now().max(1), Ordering::SeqCst);
        let result = op();
        self.op_started.store(0, Ordering::SeqCst);
        match &result {
            Ok(_) => {
                self.state.store(0, Ordering::SeqCst);
                self.last_ok.store(util::unix_now(), Ordering::SeqCst);
            }
            Err(Error::Io(e)) => {
                let state = state_from_error(e, true);
                self.state.store(state as u8, Ordering::SeqCst);
            }
            Err(_) => {}
        }
        result
    }

    pub fn state(&self) -> NasState {
        let started = self.op_started.load(Ordering::SeqCst);
        if started > 0 && util::unix_now() - started >= self.stall_after.as_secs() as i64 {
            return NasState::Stalled;
        }
        match self.state.load(Ordering::SeqCst) {
            0 => NasState::Ok,
            1 => NasState::Readonly,
            _ => NasState::Unreachable,
        }
    }

    pub fn last_ok_at(&self) -> Option<String> {
        let t = self.last_ok.load(Ordering::SeqCst);
        (t > 0).then(|| util::iso_from_unix(t))
    }

    pub fn nas_root(&self) -> &Path {
        &self.nas_root
    }

    fn send(&self, cmd: Cmd) -> Result<()> {
        self.tx
            .lock()
            .unwrap()
            .send(cmd)
            .map_err(|_| Error::internal("archiver thread stopped"))
    }

    async fn ask<T: Send + 'static>(
        &self,
        make: impl FnOnce(mpsc::SyncSender<T>) -> Cmd,
    ) -> Result<T> {
        let (tx, rx) = mpsc::sync_channel(1);
        self.send(make(tx))?;
        tokio::task::spawn_blocking(move || rx.recv())
            .await
            .map_err(|e| Error::internal(format!("archiver wait: {e}")))?
            .map_err(|_| Error::internal("archiver dropped the request"))
    }

    pub async fn probe(&self) -> NasState {
        self.ask(Cmd::Probe).await.unwrap_or(NasState::Unreachable)
    }

    pub async fn archive(&self, spec: ArchiveSpec) -> Result<()> {
        self.ask(|reply| Cmd::Archive(spec, reply)).await?
    }

    pub async fn restage(
        &self,
        sha256: &str,
        kind: ArchiveKind,
        extension: &str,
        dest: PathBuf,
        tmp_dir: PathBuf,
        mtime: Option<i64>,
    ) -> Result<()> {
        let (sha256, extension) = (sha256.to_string(), extension.to_string());
        self.ask(|reply| Cmd::Restage {
            sha256,
            kind,
            extension,
            dest,
            tmp_dir,
            mtime,
            reply,
        })
        .await?
    }

    /// Copies one snapshot generation to `backups/<day>/`. Returns false when that day already
    /// holds a complete backup (left alone).
    pub async fn backup(&self, generation_dir: PathBuf, day: String) -> Result<bool> {
        self.ask(|reply| Cmd::Backup {
            generation_dir,
            day,
            reply,
        })
        .await?
    }
}

struct Core {
    root: PathBuf,
    faults: Arc<Faults>,
}

fn object_dir(kind: ArchiveKind) -> &'static str {
    match kind {
        ArchiveKind::Recording => "raw",
        ArchiveKind::SetupFile => "setups",
    }
}

fn manifest_kind(kind: ArchiveKind) -> &'static str {
    match kind {
        ArchiveKind::Recording => "recording",
        ArchiveKind::SetupFile => "setup_file",
    }
}

fn write_synced(path: &Path, bytes: &[u8]) -> std::io::Result<()> {
    let mut file = OpenOptions::new().write(true).create_new(true).open(path)?;
    file.write_all(bytes)?;
    file.sync_all()
}

/// Reads `path` back after dropping the client cache and returns its hash.
fn verified_hash(path: &Path) -> std::io::Result<(String, u64)> {
    if let Ok(f) = File::open(path) {
        crate::sys::drop_cache(&f);
    }
    sha256_file(path)
}

impl Core {
    fn tmp_dir(&self) -> PathBuf {
        self.root.join(".tmp")
    }

    fn final_paths(&self, kind: ArchiveKind, sha: &str, ext: &str) -> (PathBuf, PathBuf) {
        let dir = self.root.join(object_dir(kind)).join(&sha[..2]);
        (
            dir.join(format!("{sha}.{ext}")),
            dir.join(format!("{sha}.manifest.json")),
        )
    }

    /// Layout, FORMAT file, leftover temp cleanup (nas-layout.md section 6).
    fn recover(&self) -> Result<()> {
        fs::create_dir_all(&self.root)?;
        for dir in ["raw", "setups", "backups", ".tmp", ".probe", "quarantine"] {
            fs::create_dir_all(self.root.join(dir))?;
        }
        let format = self.root.join("FORMAT");
        if !format.exists() {
            let tmp = self.tmp_dir().join(format!("format.{}.tmp", new_uuid()));
            write_synced(&tmp, FORMAT_TEXT.as_bytes())?;
            fs::rename(&tmp, &format)?;
        }
        for entry in fs::read_dir(self.tmp_dir())? {
            let entry = entry?;
            let path = entry.path();
            let name = entry.file_name().to_string_lossy().into_owned();
            if path.is_dir() && name.starts_with("backup-") {
                let _ = fs::remove_dir_all(&path);
            } else if name.ends_with(".tmp") {
                let _ = fs::remove_file(&path);
            }
        }
        for entry in fs::read_dir(self.root.join(".probe"))? {
            let _ = fs::remove_file(entry?.path());
        }
        self.probe()
    }

    fn probe(&self) -> Result<()> {
        fs::create_dir_all(self.root.join(".probe"))?;
        let probe = self
            .root
            .join(".probe")
            .join(format!("probe-{}", new_uuid()));
        let payload = format!("probe {}", iso_now());
        write_synced(&probe, payload.as_bytes())?;
        let mut back = String::new();
        File::open(&probe)?.read_to_string(&mut back)?;
        let _ = fs::remove_file(&probe);
        if back != payload {
            return Err(Error::Io(std::io::Error::new(
                ErrorKind::InvalidData,
                "NAS probe read back different bytes",
            )));
        }
        Ok(())
    }

    fn manifest_matches(&self, manifest: &Path, sha: &str, size: u64) -> bool {
        fs::read_to_string(manifest)
            .ok()
            .and_then(|t| serde_json::from_str::<Value>(&t).ok())
            .map(|m| m["sha256"] == sha && m["size"] == size)
            .unwrap_or(false)
    }

    fn write_manifest(&self, spec: &ArchiveSpec, final_manifest: &Path) -> Result<()> {
        let mut manifest = match &spec.manifest {
            Value::Object(map) => Value::Object(map.clone()),
            _ => json!({}),
        };
        manifest["manifest_version"] = json!(1);
        manifest["kind"] = json!(manifest_kind(spec.kind));
        manifest["sha256"] = json!(spec.sha256);
        manifest["size"] = json!(spec.size);
        manifest["archived_at"] = json!(iso_now());
        manifest["archiver_version"] = json!(env!("CARGO_PKG_VERSION"));
        let tmp = self
            .tmp_dir()
            .join(format!("{}.manifest.{}.tmp", spec.sha256, new_uuid()));
        let text =
            serde_json::to_vec_pretty(&manifest).map_err(|e| Error::internal(e.to_string()))?;
        write_synced(&tmp, &text)?;
        self.faults.check("archive.after_manifest_tmp")?;
        fs::rename(&tmp, final_manifest)?;
        if let Some(dir) = final_manifest.parent() {
            fsync_dir(dir)?;
        }
        self.faults.check("archive.after_manifest_rename")?;
        Ok(())
    }

    /// nas-layout.md section 3.
    fn archive(&self, spec: &ArchiveSpec) -> Result<()> {
        let sha = spec.sha256.as_str();
        let (data, manifest) = self.final_paths(spec.kind, sha, &spec.extension);
        // 1. already archived
        if data.exists() && manifest.exists() && self.manifest_matches(&manifest, sha, spec.size) {
            return Ok(());
        }
        // 2. data without a (matching) manifest
        let mut need_data = true;
        if data.exists() {
            let (hash, bytes) = verified_hash(&data)?;
            if hash == sha && bytes == spec.size {
                need_data = false;
            } else {
                let quarantine = self.root.join("quarantine").join(format!(
                    "{sha}.{}.{}",
                    iso_now().replace(':', ""),
                    spec.extension
                ));
                fs::rename(&data, quarantine)?;
            }
        }
        if need_data {
            // 3. temp file, O_EXCL
            let tmp = self.tmp_dir().join(format!("{sha}.{}.tmp", new_uuid()));
            let mut out = OpenOptions::new().write(true).create_new(true).open(&tmp)?;
            self.faults.check("archive.after_tmp_create")?;
            // 4. write all bytes, fsync, close
            let result = (|| -> Result<()> {
                let mut src = File::open(&spec.spool_path)?;
                let mut buf = vec![0u8; 1 << 20];
                loop {
                    let n = src.read(&mut buf)?;
                    if n == 0 {
                        break;
                    }
                    out.write_all(&buf[..n])?;
                }
                out.sync_all()?;
                Ok(())
            })();
            drop(out);
            if let Err(e) = result {
                if !matches!(e, Error::Crash(_)) {
                    let _ = fs::remove_file(&tmp);
                }
                return Err(e);
            }
            self.faults.check("archive.after_tmp_write")?;
            // 5. hash after flush
            let (hash, bytes) = verified_hash(&tmp)?;
            if hash != sha || bytes != spec.size {
                let _ = fs::remove_file(&tmp);
                return Err(Error::Io(std::io::Error::new(
                    ErrorKind::InvalidData,
                    format!("NAS copy of {sha} read back as {hash} ({bytes} bytes)"),
                )));
            }
            self.faults.check("archive.after_readback")?;
            // 6. rename into place, fsync the directory
            let dir = data.parent().expect("object has a parent");
            fs::create_dir_all(dir)?;
            fs::rename(&tmp, &data)?;
            fsync_dir(dir)?;
            self.faults.check("archive.after_data_rename")?;
        }
        // 7. manifest
        self.write_manifest(spec, &manifest)
    }

    /// Copies a raw object back to the spool with a hash check (re-analysis of an archived-only
    /// recording). The copy gets `mtime` and is fsynced before it is renamed into place, so the
    /// worker's quiet rule passes at once and the rename publishes a durable file.
    fn restage(
        &self,
        sha: &str,
        kind: ArchiveKind,
        ext: &str,
        dest: &Path,
        tmp_dir: &Path,
        mtime: Option<i64>,
    ) -> Result<()> {
        let (data, manifest) = self.final_paths(kind, sha, ext);
        if !manifest.exists() {
            return Err(Error::not_found(format!(
                "{sha} has no manifest on the NAS"
            )));
        }
        fs::create_dir_all(tmp_dir)?;
        let tmp = tmp_dir.join(format!("{sha}.restage.{}", new_uuid()));
        let copy = (|| -> std::io::Result<()> {
            let mut src = File::open(&data)?;
            crate::sys::drop_cache(&src);
            let mut out = OpenOptions::new().write(true).create_new(true).open(&tmp)?;
            std::io::copy(&mut src, &mut out)?;
            if let Some(mtime) = mtime {
                out.set_modified(util::systime_from_unix(mtime))?;
            }
            out.sync_all()
        })();
        if let Err(e) = copy {
            let _ = fs::remove_file(&tmp);
            return Err(e.into());
        }
        let (hash, _) = sha256_file(&tmp)?;
        if hash != sha {
            let _ = fs::remove_file(&tmp);
            return Err(Error::Io(std::io::Error::new(
                ErrorKind::InvalidData,
                format!("restaged {sha} hashes to {hash}"),
            )));
        }
        if let Some(dir) = dest.parent() {
            fs::create_dir_all(dir)?;
        }
        fs::rename(&tmp, dest)?;
        if let Some(dir) = dest.parent() {
            fsync_dir(dir)?;
        }
        Ok(())
    }

    /// nas-layout.md section 5 step 3.
    fn backup(&self, generation: &Path, day: &str) -> Result<bool> {
        let final_dir = self.root.join("backups").join(day);
        if self.backup_complete(&final_dir) {
            return Ok(false);
        }
        let staging = self.tmp_dir().join(format!("backup-{day}-{}", new_uuid()));
        fs::create_dir_all(&staging)?;
        let mut files = Vec::new();
        collect_files(generation, generation, &mut files)?;
        // manifest.json last
        files.sort_by_key(|(rel, _)| rel == "manifest.json");
        for (rel, src) in &files {
            let dest = staging.join(rel);
            if let Some(parent) = dest.parent() {
                fs::create_dir_all(parent)?;
            }
            let (want, _) = sha256_file(src)?;
            {
                let mut input = File::open(src)?;
                let mut out = OpenOptions::new()
                    .write(true)
                    .create_new(true)
                    .open(&dest)?;
                std::io::copy(&mut input, &mut out)?;
                out.sync_all()?;
            }
            let (got, _) = verified_hash(&dest)?;
            if got != want {
                let _ = fs::remove_dir_all(&staging);
                return Err(Error::Io(std::io::Error::new(
                    ErrorKind::InvalidData,
                    format!("backup file {rel} read back differently"),
                )));
            }
        }
        if final_dir.exists() {
            // an incomplete day folder cannot be trusted; move it aside
            let aside = self
                .root
                .join("quarantine")
                .join(format!("backup-{day}.{}", new_uuid()));
            fs::rename(&final_dir, aside)?;
        }
        fs::rename(&staging, &final_dir)?;
        fsync_dir(&self.root.join("backups"))?;
        Ok(true)
    }

    /// A day folder is complete when its manifest exists and every listed file matches.
    fn backup_complete(&self, dir: &Path) -> bool {
        let Ok(text) = fs::read_to_string(dir.join("manifest.json")) else {
            return false;
        };
        let Ok(manifest) = serde_json::from_str::<Value>(&text) else {
            return false;
        };
        let Some(files) = manifest["backup"]["files"].as_array() else {
            return false;
        };
        files.iter().all(|f| {
            let (Some(path), Some(sha)) = (f["path"].as_str(), f["sha256"].as_str()) else {
                return false;
            };
            sha256_file(&dir.join(path))
                .map(|(h, _)| h == sha)
                .unwrap_or(false)
        })
    }
}

fn collect_files(base: &Path, dir: &Path, out: &mut Vec<(String, PathBuf)>) -> std::io::Result<()> {
    for entry in fs::read_dir(dir)? {
        let path = entry?.path();
        if path.is_dir() {
            collect_files(base, &path, out)?;
        } else {
            let rel = path
                .strip_prefix(base)
                .expect("under base")
                .to_string_lossy()
                .replace('\\', "/");
            out.push((rel, path));
        }
    }
    Ok(())
}

/// Reads every `*.manifest.json` under `raw/` and `setups/` (rebuild-index).
pub fn read_manifests(nas_root: &Path) -> std::io::Result<Vec<Value>> {
    let mut manifests = Vec::new();
    for top in ["raw", "setups"] {
        let dir = nas_root.join(top);
        if !dir.is_dir() {
            continue;
        }
        for prefix in fs::read_dir(&dir)? {
            let prefix = prefix?.path();
            if !prefix.is_dir() {
                continue;
            }
            for entry in fs::read_dir(&prefix)? {
                let path = entry?.path();
                let name = path.file_name().map(|n| n.to_string_lossy().into_owned());
                if name.is_some_and(|n| n.ends_with(".manifest.json"))
                    && let Ok(text) = fs::read_to_string(&path)
                    && let Ok(value) = serde_json::from_str::<Value>(&text)
                {
                    manifests.push(value);
                }
            }
        }
    }
    Ok(manifests)
}

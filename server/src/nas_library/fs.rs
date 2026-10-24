//! NAS operations. Called exclusively by the archiver thread.
use crate::error::{Error, Result};
use crate::faults::Faults;
use crate::util::{fsync_dir, new_uuid};
use sha2::{Digest, Sha256};
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};
pub type Gate = Arc<dyn Fn() -> bool + Send + Sync>;
pub const MARKER: &str = "library/.identity";
pub const SETUP_LIMIT: u64 = 16 << 20;
#[derive(Debug, Clone)]
pub struct InboxFile {
    pub customer: i64,
    pub car: String,
    pub relative: String,
    pub path: String,
    pub size: u64,
    pub mtime: SystemTime,
}
pub enum Request {
    Metadata {
        identity: String,
        kind: String,
        sha: String,
        size: u64,
        path: String,
        state: String,
        legacy: Option<String>,
        manifest: Option<serde_json::Value>,
    },
    Header {
        identity: String,
        path: String,
        parser: Arc<dyn crate::adapters::RecordingParser>,
    },
    Identity(Option<String>),
    Publish {
        identity: String,
        path: String,
        legacy: Option<String>,
        sha: String,
        size: u64,
        source: Option<PathBuf>,
    },
    Inspect {
        identity: String,
        path: String,
        sha: String,
        size: u64,
        fingerprint: Option<String>,
    },
    Restage {
        identity: String,
        path: String,
        sha: String,
        size: u64,
        dest: PathBuf,
        tmp: PathBuf,
        mtime: i64,
    },
    Inbox {
        identity: String,
        customers: Vec<i64>,
    },
    ReadInbox {
        identity: String,
        file: InboxFile,
    },
    Adopt {
        identity: String,
        file: InboxFile,
        path: String,
        sha: String,
    },
}
#[derive(Debug)]
pub enum Reply {
    Identity(String),
    Present(String),
    Missing,
    Conflict(String),
    Inbox(Vec<InboxFile>),
    Bytes(Vec<u8>),
    Written,
    Header(serde_json::Value),
}
fn check(gate: &Gate) -> Result<()> {
    if gate() {
        Ok(())
    } else {
        Err(Error::unavailable(
            60,
            "NAS library work paused while racing or evidence is unknown",
        ))
    }
}
pub fn component(s: &str, max: usize) -> bool {
    if s.is_empty()
        || s == "."
        || s == ".."
        || s.encode_utf16().count() > max
        || s.ends_with(['.', ' '])
        || s.chars()
            .any(|c| c.is_control() || "<>:\"/\\|?*".contains(c))
    {
        return false;
    }
    let base = s.split('.').next().unwrap_or_default().to_ascii_lowercase();
    !["con", "prn", "aux", "nul", "conin$", "conout$", "clock$"].contains(&base.as_str())
        && !(["com", "lpt"].iter().any(|p| {
            base.strip_prefix(p).is_some_and(|s| {
                matches!(
                    s,
                    "0" | "1" | "2" | "3" | "4" | "5" | "6" | "7" | "8" | "9" | "¹" | "²" | "³"
                )
            })
        }))
}
pub fn stem(name: &str, budget: usize) -> String {
    let leaf = name.rsplit(['/', '\\']).next().unwrap_or_default();
    let raw = leaf.rsplit_once('.').map(|(s, _)| s).unwrap_or(leaf);
    let mut out = String::new();
    let mut used = 0;
    for c in raw.chars().map(|c| {
        if c.is_control() || "<>:\"/\\|?*".contains(c) {
            '_'
        } else {
            c
        }
    }) {
        if used + c.len_utf16() > budget {
            break;
        }
        used += c.len_utf16();
        out.push(c);
    }
    let clean = out.trim_matches(['.', ' ']);
    if component(clean, budget) {
        clean.to_string()
    } else {
        "setup".into()
    }
}
fn regular(meta: &fs::Metadata) -> bool {
    if meta.file_type().is_symlink() {
        return false;
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        if meta.file_attributes() & 0x400 != 0 {
            return false;
        }
    }
    true
}
/// Check each existing ancestor without following symlinks or Windows reparse points.
pub fn checked(root: &Path, relative: &str) -> Result<PathBuf> {
    if relative.encode_utf16().count() > 240 {
        return Err(Error::invalid("NAS library path exceeds 240 UTF-16 units"));
    }
    let mut out = root.to_path_buf();
    let m = fs::symlink_metadata(root)?;
    if !regular(&m) || !m.is_dir() {
        return Err(Error::invalid("NAS root is not a regular directory"));
    }
    for part in relative.split('/') {
        if !component(part, 200) {
            return Err(Error::invalid("unsafe NAS library path component"));
        }
        out.push(part);
        match fs::symlink_metadata(&out) {
            Ok(m) if !regular(&m) => {
                return Err(Error::invalid("NAS symlink/reparse traversal refused"));
            }
            Ok(_) => {}
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
            Err(e) => return Err(e.into()),
        }
    }
    Ok(out)
}
fn anchor(root: &Path, relative: &str, create: bool) -> Result<crate::sys::AnchoredPath> {
    checked(root, relative)?;
    Ok(crate::sys::anchor_nas(root, relative, create)?)
}
fn read_small(path: &Path, limit: u64) -> Result<Vec<u8>> {
    let mut bytes = Vec::new();
    crate::sys::open_nofollow(path)?
        .take(limit + 1)
        .read_to_end(&mut bytes)?;
    if bytes.len() as u64 > limit {
        return Err(Error::invalid("NAS metadata exceeds its limit"));
    }
    Ok(bytes)
}
fn merge(target: &mut serde_json::Value, source: serde_json::Value) {
    match (target, source) {
        (serde_json::Value::Object(to), serde_json::Value::Object(from)) => {
            for (key, value) in from {
                merge(to.entry(key).or_insert(serde_json::Value::Null), value);
            }
        }
        (to, from) => *to = from,
    }
}
pub fn identity(root: &Path, expected: Option<&str>) -> Result<String> {
    let format = anchor(root, "FORMAT", false)?;
    if read_small(&format, 1024)? != crate::archiver::FORMAT_TEXT.as_bytes() {
        return Err(Error::unavailable(
            60,
            "NAS FORMAT mismatch; library paused",
        ));
    }
    let marker = anchor(root, MARKER, expected.is_none())?;
    match read_small(&marker, 128)
        .and_then(|b| String::from_utf8(b).map_err(|_| Error::invalid("NAS identity is not UTF-8")))
    {
        Ok(id) if !id.trim().is_empty() && expected.is_none_or(|e| e == id.trim()) => {
            Ok(id.trim().into())
        }
        Ok(_) => Err(Error::unavailable(60, "NAS library identity mismatch")),
        Err(Error::Io(e)) if expected.is_none() && e.kind() == std::io::ErrorKind::NotFound => {
            fs::create_dir_all(marker.parent().unwrap())?;
            let value = new_uuid();
            match OpenOptions::new()
                .create_new(true)
                .write(true)
                .open(&marker)
            {
                Ok(mut f) => {
                    f.write_all(value.as_bytes())?;
                    f.sync_all()?;
                    fsync_dir(marker.parent().unwrap())?;
                    let readme = anchor(root, "library/README.txt", false)?;
                    if let Ok(mut file) =
                        OpenOptions::new().create_new(true).write(true).open(readme)
                    {
                        file.write_all(b"iRacing Coach library\r\n\r\ntelemetry and replays hold the single NAS copy of each recording. setups holds locked source files and Coach revisions. Files keep their names plus a content hash.\r\n\r\nDeleting a sim-PC file does not delete its NAS archive. Deleting a library payload is remembered and sync will not recreate it. Restore the exact bytes at the same library path to restore it.\r\n\r\nTo add a setup, put a .sto in setups/<driver number>/<car>/inbox. After 10 quiet seconds Coach moves a new file into sources, imports it locked, and sends a new managed copy to the companion. Use Save As in Coach to edit it. Changed Coach revisions sync as new files; originals and older versions stay intact.\r\n")?;
                        file.sync_all()?;
                    }
                    Ok(value)
                }
                Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => identity(root, None),
                Err(e) => Err(e.into()),
            }
        }
        Err(_) => Err(Error::unavailable(
            60,
            "NAS library identity marker unavailable; no folders recreated",
        )),
    }
}
struct Verified {
    hash: String,
    size: u64,
    signature: String,
    file: File,
}
fn file_signature(file: &File) -> Result<String> {
    let m = file.metadata()?;
    Ok(format!(
        "{}:{}:{}",
        m.len(),
        m.modified()?
            .duration_since(UNIX_EPOCH)
            .map_err(|_| Error::invalid("invalid library mtime"))?
            .as_nanos(),
        crate::sys::file_identity_open(file)?
    ))
}
fn hash(path: &Path, gate: &Gate) -> Result<Verified> {
    check(gate)?;
    let meta = fs::symlink_metadata(path)?;
    if !regular(&meta) || !meta.is_file() {
        return Err(Error::invalid("library payload is not regular"));
    }
    let mut f = crate::sys::open_nofollow(path)?;
    let signature = file_signature(&f)?;
    crate::sys::drop_cache(&f);
    let mut h = Sha256::new();
    let mut size = 0;
    let mut buf = [0; 32768];
    loop {
        check(gate)?;
        let n = f.read(&mut buf)?;
        if n == 0 {
            break;
        }
        h.update(&buf[..n]);
        size += n as u64;
    }
    if file_signature(&f)? != signature || fingerprint(path)? != signature {
        return Err(Error::unavailable(
            60,
            "library payload changed during verification",
        ));
    }
    Ok(Verified {
        hash: h.finalize().iter().map(|b| format!("{b:02x}")).collect(),
        size,
        signature,
        file: f,
    })
}
fn matches(path: &Path, sha: &str, size: u64, gate: &Gate) -> Result<bool> {
    let actual = hash(path, gate)?;
    Ok(actual.hash == sha && actual.size == size)
}
fn exists(path: &Path) -> Result<bool> {
    match fs::symlink_metadata(path) {
        Ok(_) => Ok(true),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(false),
        Err(e) => Err(e.into()),
    }
}
fn fingerprint(path: &Path) -> Result<String> {
    let m = fs::symlink_metadata(path)?;
    if !regular(&m) || !m.is_file() {
        return Err(Error::invalid("library payload not a regular file"));
    }
    file_signature(&crate::sys::open_nofollow(path)?)
}
struct Claim {
    payload: crate::sys::AnchoredPath,
    published: crate::sys::AnchoredPath,
    receipt: String,
}
fn claim(root: &Path, dest: &Path, sha: &str, size: u64) -> Result<Claim> {
    let kind = dest.extension().and_then(|s| s.to_str()).unwrap_or("");
    if !matches!(kind, "ibt" | "sto" | "htm" | "rpy") || !crate::util::is_sha256_hex(sha) {
        return Err(Error::invalid("invalid relocation claim identity"));
    }
    let parent = anchor(
        root,
        &format!("library/.catalog/claims/{kind}/.create"),
        true,
    )?;
    let dir = parent.parent().unwrap().join(sha);
    #[cfg(unix)]
    let builder = {
        use std::os::unix::fs::DirBuilderExt;
        let mut builder = fs::DirBuilder::new();
        builder.mode(0o700);
        builder
    };
    #[cfg(not(unix))]
    let builder = fs::DirBuilder::new();
    let created = match builder.create(&dir) {
        Ok(()) => {
            fsync_dir(parent.parent().unwrap())?;
            true
        }
        Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => false,
        Err(e) => return Err(e.into()),
    };
    let base = format!("library/.catalog/claims/{kind}/{sha}");
    let payload = anchor(root, &format!("{base}/payload"), false)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if created {
            // The production NAS inherits 0777 even for mkdir(mode0700). Explicit
            // fchmod on this newly created, pinned directory is supported there.
            crate::sys::make_parent_private(&payload)?;
            fsync_dir(payload.parent().unwrap())?;
        }
        if fs::metadata(payload.parent().unwrap())?.mode() & 0o077 != 0 {
            return Err(Error::conflict(
                "library_conflict",
                "relocation claim directory is not private",
            ));
        }
    }
    #[cfg(not(unix))]
    let _ = created;
    let receipt = format!("{sha}:{size}\n");
    let receipt_path = anchor(root, &format!("{base}/receipt"), false)?;
    write_receipt(&receipt_path, &receipt)?;
    let published = anchor(root, &format!("{base}/published"), false)?;
    Ok(Claim {
        payload,
        published,
        receipt,
    })
}
fn write_receipt(path: &Path, receipt: &str) -> Result<()> {
    match read_small(path, 128) {
        Ok(bytes) if bytes == receipt.as_bytes() => return Ok(()),
        Ok(_) => {
            return Err(Error::conflict(
                "library_conflict",
                "relocation receipt identity differs",
            ));
        }
        Err(Error::Io(e)) if e.kind() == std::io::ErrorKind::NotFound => {}
        Err(e) => return Err(e),
    }
    // Publish complete metadata atomically inside the private, pinned directory. A
    // process kill during the write cannot leave a partial receipt blocking recovery.
    let tmp = path
        .parent()
        .unwrap()
        .join(format!("receipt-{}.tmp", new_uuid()));
    let result = (|| -> Result<()> {
        let mut file = OpenOptions::new().create_new(true).write(true).open(&tmp)?;
        file.write_all(receipt.as_bytes())?;
        file.sync_all()?;
        drop(file);
        fs::rename(&tmp, path)?;
        fsync_dir(path.parent().unwrap())?;
        Ok(())
    })();
    let _ = fs::remove_file(tmp);
    result
}
fn finish_claim(claim: &Claim, dest: &Path, verified: Verified, faults: &Faults) -> Result<Reply> {
    let signature = verified.signature.clone();
    if fingerprint(dest)? != signature || file_signature(&verified.file)? != signature {
        return Ok(Reply::Conflict(
            "published claim changed after verification".into(),
        ));
    }
    write_receipt(&claim.published, &claim.receipt)?;
    faults.check("library.after_publish_receipt")?;
    // Only the app-owned leaf inside a pinned private directory can be removed here.
    // The original source name is never unlinked, including during recovery.
    drop(verified);
    if exists(&claim.payload)? {
        fs::remove_file(&claim.payload)?;
        fsync_dir(claim.payload.parent().unwrap())?;
    }
    faults.check("library.after_claim_unlink")?;
    faults.check("archive.after_data_rename")?;
    Ok(Reply::Present(signature))
}
fn relocate(
    root: &Path,
    src: Option<&Path>,
    dest: &Path,
    sha: &str,
    size: u64,
    gate: &Gate,
    faults: &Faults,
) -> Result<Reply> {
    check(gate)?;
    let claim = claim(root, dest, sha, size)?;
    let published = exists(&claim.published)?;
    if published {
        write_receipt(&claim.published, &claim.receipt)?;
        if !exists(dest)? {
            return Err(Error::unavailable(
                60,
                "published canonical file is missing; relocation will not recreate it",
            ));
        }
    }
    if exists(dest)? {
        let verified = hash(dest, gate)?;
        if verified.hash != sha || verified.size != size {
            return Ok(Reply::Conflict(
                "canonical path holds different bytes".into(),
            ));
        }
        if exists(&claim.payload)? {
            if !crate::sys::same_file(&claim.payload, dest)? {
                return Ok(Reply::Conflict(
                    "claim and canonical payload differ; both preserved".into(),
                ));
            }
            return finish_claim(&claim, dest, verified, faults);
        }
        if !published && src.is_some_and(|p| p.exists()) {
            return Ok(Reply::Conflict(
                "source and canonical names both exist; both preserved for recovery".into(),
            ));
        }
        return Ok(Reply::Present(verified.signature));
    }
    if !exists(&claim.payload)? {
        let Some(src) = src else {
            return Ok(Reply::Missing);
        };
        if !exists(src)? {
            return Ok(Reply::Missing);
        }
        // The exclusive private directory has no externally editable target leaf.
        // A same-filesystem rename claims whichever source bytes exist atomically.
        faults.check("library.before_claim")?;
        fs::rename(src, &claim.payload)?;
        fsync_dir(claim.payload.parent().unwrap())?;
        fsync_dir(src.parent().unwrap())?;
        faults.check("library.after_claim")?;
    }
    check(gate)?;
    let verified = hash(&claim.payload, gate)?;
    if verified.hash != sha || verified.size != size {
        return Ok(Reply::Conflict(format!(
            "claimed source hash differs; bytes preserved in library/.catalog/claims/{}/{sha}/payload",
            dest.extension().unwrap().to_string_lossy()
        )));
    }
    faults.check("archive.after_readback")?;
    fs::create_dir_all(dest.parent().unwrap())?;
    match fs::hard_link(&claim.payload, dest) {
        Ok(()) => {}
        Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {
            return relocate(root, src, dest, sha, size, gate, faults);
        }
        Err(e) => return Err(e.into()),
    }
    fsync_dir(dest.parent().unwrap())?;
    faults.check("library.after_link")?;
    check(gate)?;
    finish_claim(&claim, dest, verified, faults)
}
fn inspect(
    root: &Path,
    path: &str,
    sha: &str,
    size: u64,
    known: Option<&str>,
    gate: &Gate,
) -> Result<Reply> {
    let dest = anchor(root, path, false)?;
    match fs::symlink_metadata(&dest) {
        Ok(_) => {
            let signature = fingerprint(&dest)?;
            if known == Some(signature.as_str()) {
                return Ok(Reply::Present(signature));
            }
            let verified = hash(&dest, gate)?;
            if verified.hash == sha && verified.size == size {
                Ok(Reply::Present(verified.signature))
            } else {
                Ok(Reply::Conflict(
                    "canonical payload hash mismatch; no deletion inferred".into(),
                ))
            }
        }
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
            let parent = dest.parent().unwrap();
            // `parent` is an already pinned descriptor path on Linux, not an untrusted
            // pathname. Inspect its target directory rather than the /proc fd symlink.
            let m = fs::metadata(parent)?;
            if !regular(&m) || !m.is_dir() {
                return Err(Error::unavailable(60, "library parent unavailable"));
            }
            let _ = fs::read_dir(parent)?.next().transpose()?;
            Ok(Reply::Missing)
        }
        Err(e) => Err(e.into()),
    }
}
fn inbox_walk(
    root: &Path,
    dir_relative: &str,
    owner_car: (i64, &str),
    inbox_relative: &str,
    depth: usize,
    gate: &Gate,
    out: &mut Vec<InboxFile>,
) -> Result<()> {
    let (customer, car) = owner_car;
    if depth > 8 || out.len() >= 128 {
        return Ok(());
    }
    check(gate)?;
    let pinned = match anchor(root, &format!("{dir_relative}/.scan"), false) {
        Ok(p) => p,
        Err(Error::Io(e)) if e.kind() == std::io::ErrorKind::NotFound => return Ok(()),
        Err(e) => return Err(e),
    };
    let entries = fs::read_dir(pinned.parent().unwrap())?;
    for entry in entries {
        check(gate)?;
        if out.len() >= 128 {
            break;
        }
        let entry = entry?;
        let name = entry.file_name().to_string_lossy().into_owned();
        if !component(&name, 200) {
            return Err(Error::invalid("unsafe inbox filename"));
        }
        let rel = format!("{dir_relative}/{name}");
        let path = anchor(root, &rel, false)?;
        let m = fs::symlink_metadata(&path)?;
        if !regular(&m) {
            continue;
        }
        if m.is_dir() {
            inbox_walk(
                root,
                &rel,
                (customer, car),
                &format!("{inbox_relative}{name}/"),
                depth + 1,
                gate,
                out,
            )?;
        } else if m.is_file()
            && path
                .extension()
                .is_some_and(|e| e.eq_ignore_ascii_case("sto"))
            && (1..=SETUP_LIMIT).contains(&m.len())
        {
            let mtime = m.modified()?;
            if SystemTime::now()
                .duration_since(mtime)
                .unwrap_or_default()
                .as_secs()
                < 10
            {
                continue;
            }
            out.push(InboxFile {
                customer,
                car: car.into(),
                relative: format!("{inbox_relative}{name}"),
                path: rel,
                size: m.len(),
                mtime,
            });
        }
    }
    Ok(())
}
pub fn execute(root: &Path, faults: &Faults, request: Request, gate: Gate) -> Result<Reply> {
    check(&gate)?;
    if let Request::Identity(expected) = request {
        return identity(root, expected.as_deref()).map(Reply::Identity);
    }
    let expected = match &request {
        Request::Metadata { identity, .. }
        | Request::Header { identity, .. }
        | Request::Publish { identity, .. }
        | Request::Inspect { identity, .. }
        | Request::Restage { identity, .. }
        | Request::Inbox { identity, .. }
        | Request::ReadInbox { identity, .. }
        | Request::Adopt { identity, .. } => identity,
        Request::Identity(_) => unreachable!(),
    };
    identity(root, Some(expected))?;
    match request {
        Request::Metadata {
            identity,
            kind,
            sha,
            size,
            path,
            state,
            legacy,
            manifest,
        } => {
            let dest = anchor(
                root,
                &format!("library/.catalog/{kind}/{sha}.manifest.json"),
                true,
            )?;
            let existing = if dest.is_file() {
                Some(dest.clone())
            } else {
                legacy.and_then(|p| {
                    anchor(
                        root,
                        &format!("{}/{sha}.manifest.json", p.rsplit_once('/')?.0),
                        false,
                    )
                    .ok()
                })
            };
            let mut value = if let Some(p) = existing {
                let m = match fs::symlink_metadata(&p) {
                    Ok(m) => Some(m),
                    Err(e) if e.kind() == std::io::ErrorKind::NotFound => None,
                    Err(e) => return Err(e.into()),
                };
                if m.as_ref()
                    .is_some_and(|m| regular(m) && m.is_file() && m.len() < 1 << 20)
                {
                    serde_json::from_slice::<serde_json::Value>(&read_small(&p, 1 << 20)?)
                        .map_err(|_| Error::invalid("library manifest is invalid"))?
                } else {
                    serde_json::json!({})
                }
            } else {
                serde_json::json!({})
            };
            if !value.is_object() {
                return Err(Error::invalid("library manifest must be an object"));
            }
            if value.get("sha256").is_some_and(|s| s != &sha) {
                return Err(Error::conflict(
                    "library_conflict",
                    "catalog manifest identity differs",
                ));
            }
            if let Some(fields) = manifest {
                merge(&mut value, fields);
            }
            value["manifest_version"] = serde_json::json!(1);
            value["sha256"] = serde_json::json!(sha);
            value["size"] = serde_json::json!(size);
            value["kind"] = serde_json::json!(match kind.as_str() {
                "ibt" => "recording",
                "rpy" => "replay",
                _ => "setup_file",
            });
            value["archived_at"] = serde_json::json!(crate::util::iso_now());
            value["archiver_version"] = serde_json::json!(env!("CARGO_PKG_VERSION"));
            merge(
                &mut value["library"],
                serde_json::json!({"identity":identity,"relative_path":path,"state":state,"kind":kind}),
            );
            fs::create_dir_all(dest.parent().unwrap())?;
            let tmp = anchor(
                root,
                &format!(".tmp/library-meta-{}.tmp", new_uuid()),
                false,
            )?;
            let mut out = OpenOptions::new().create_new(true).write(true).open(&tmp)?;
            out.write_all(
                &serde_json::to_vec_pretty(&value).map_err(|e| Error::internal(e.to_string()))?,
            )?;
            out.sync_all()?;
            drop(out);
            faults.check("archive.after_manifest_tmp")?;
            fs::rename(&tmp, &dest)?;
            fsync_dir(dest.parent().unwrap())?;
            faults.check("archive.after_manifest_rename")?;
            Ok(Reply::Written)
        }
        Request::Header { path, parser, .. } => {
            let path = anchor(root, &path, false)?;
            // Keep the verified leaf open while the pathname-based parser reads it. Unix
            // reopens this descriptor instead of a NAS name that can be swapped meanwhile;
            // Windows open_nofollow denies deletion/replacement of the held leaf.
            let file = crate::sys::open_nofollow(&path)?;
            let before = file_signature(&file)?;
            #[cfg(unix)]
            let parse_path = {
                use std::os::fd::AsRawFd;
                std::path::PathBuf::from(format!("/proc/self/fd/{}", file.as_raw_fd()))
            };
            #[cfg(not(unix))]
            let parse_path = path.to_path_buf();
            check(&gate)?;
            let parsed = parser.parse(&parse_path).map_err(Error::internal)?;
            if file_signature(&file)? != before {
                return Err(Error::unavailable(
                    60,
                    "recording changed during header read",
                ));
            }
            check(&gate)?;
            Ok(Reply::Header(parsed.header_json))
        }
        Request::Publish {
            path,
            legacy,
            sha,
            size,
            source,
            ..
        } => {
            let dest = anchor(root, &path, true)?;
            if let Some(legacy) = legacy {
                match anchor(root, &legacy, false) {
                    Ok(src) => {
                        let result = relocate(root, Some(&src), &dest, &sha, size, &gate, faults)?;
                        if !matches!(result, Reply::Missing) {
                            return Ok(result);
                        }
                    }
                    Err(Error::Io(e)) if e.kind() == std::io::ErrorKind::NotFound => {}
                    Err(e) => return Err(e),
                }
            }
            let recovered = relocate(root, None, &dest, &sha, size, &gate, faults)?;
            if !matches!(recovered, Reply::Missing) {
                return Ok(recovered);
            }
            let Some(source) = source else {
                return Ok(Reply::Missing);
            };
            let tmp = anchor(root, &format!(".tmp/library-{}.tmp", new_uuid()), false)?;
            let mut file = OpenOptions::new().create_new(true).write(true).open(&tmp)?;
            faults.check("archive.after_tmp_create")?;
            let copied = (|| -> Result<()> {
                let mut input = File::open(&source)?;
                let mut buf = [0; 32768];
                loop {
                    check(&gate)?;
                    let n = input.read(&mut buf)?;
                    if n == 0 {
                        break;
                    }
                    file.write_all(&buf[..n])?;
                }
                file.sync_all()?;
                Ok(())
            })();
            drop(file);
            if let Err(e) = copied {
                let _ = fs::remove_file(&tmp);
                return Err(e);
            }
            faults.check("archive.after_tmp_write")?;
            let result = relocate(root, Some(&tmp), &dest, &sha, size, &gate, faults);
            if result.is_err() && !matches!(result, Err(Error::Crash(_))) {
                let _ = fs::remove_file(&tmp);
            }
            result
        }
        Request::Inspect {
            path,
            sha,
            size,
            fingerprint,
            ..
        } => inspect(root, &path, &sha, size, fingerprint.as_deref(), &gate),
        Request::Restage {
            path,
            sha,
            size,
            dest,
            tmp,
            mtime,
            ..
        } => {
            let source = anchor(root, &path, false)?;
            if !matches(&source, &sha, size, &gate)? {
                return Err(Error::unprocessable(
                    "library_conflict",
                    "restage payload hash mismatch",
                ));
            }
            if exists(&dest)? {
                if matches(&dest, &sha, size, &gate)? {
                    return Ok(Reply::Present(fingerprint(&dest)?));
                }
                return Err(Error::unprocessable(
                    "library_conflict",
                    "spool path differs",
                ));
            }
            let temp = tmp.join(format!("{sha}.library-{}.part", new_uuid()));
            let result = (|| -> Result<()> {
                let mut input = crate::sys::open_nofollow(&source)?;
                let mut output = OpenOptions::new()
                    .create_new(true)
                    .write(true)
                    .open(&temp)?;
                let mut buf = [0; 32768];
                loop {
                    check(&gate)?;
                    let n = input.read(&mut buf)?;
                    if n == 0 {
                        break;
                    }
                    output.write_all(&buf[..n])?;
                }
                output.set_modified(
                    UNIX_EPOCH + std::time::Duration::from_secs(mtime.max(0) as u64),
                )?;
                output.sync_all()?;
                drop(output);
                if !matches(&temp, &sha, size, &gate)? {
                    return Err(Error::unprocessable(
                        "library_conflict",
                        "restage copy hash mismatch",
                    ));
                }
                match fs::hard_link(&temp, &dest) {
                    Ok(()) => {}
                    Err(e)
                        if e.kind() == std::io::ErrorKind::AlreadyExists
                            && matches(&dest, &sha, size, &gate)? => {}
                    Err(e) => return Err(e.into()),
                }
                fsync_dir(dest.parent().unwrap())?;
                Ok(())
            })();
            let _ = fs::remove_file(temp);
            result?;
            Ok(Reply::Present(fingerprint(&dest)?))
        }
        Request::Inbox { customers, .. } => {
            let mut out = Vec::new();
            for customer in customers {
                check(&gate)?;
                let dir = match anchor(root, &format!("library/setups/{customer}/.scan"), false) {
                    Ok(d) => d,
                    Err(Error::Io(e)) if e.kind() == std::io::ErrorKind::NotFound => continue,
                    Err(e) => return Err(e),
                };
                let cars = fs::read_dir(dir.parent().unwrap())?;
                for car in cars.take(256) {
                    check(&gate)?;
                    let car = car?;
                    let name = car.file_name().to_string_lossy().into_owned();
                    if !component(&name, 64) {
                        continue;
                    }
                    let m = fs::symlink_metadata(car.path())?;
                    if !regular(&m) || !m.is_dir() {
                        continue;
                    }
                    let base = format!("library/setups/{customer}/{name}/inbox");
                    let pinned = anchor(root, &format!("{base}/.scan"), true)?;
                    inbox_walk(root, &base, (customer, &name), "", 0, &gate, &mut out)?;
                    drop(pinned);
                }
            }
            Ok(Reply::Inbox(out))
        }
        Request::ReadInbox { file, .. } => {
            let path = anchor(root, &file.path, false)?;
            let before = fs::symlink_metadata(&path)?;
            if !regular(&before)
                || !before.is_file()
                || before.len() != file.size
                || before.modified()? != file.mtime
            {
                return Err(Error::unavailable(60, "inbox file changed"));
            }
            let mut bytes = Vec::with_capacity(file.size as usize);
            let mut input = crate::sys::open_nofollow(&path)?;
            let mut buf = [0; 32768];
            loop {
                check(&gate)?;
                let n = input.read(&mut buf)?;
                if n == 0 {
                    break;
                }
                if bytes.len() + n > SETUP_LIMIT as usize {
                    return Err(Error::invalid("inbox file too large"));
                }
                bytes.extend_from_slice(&buf[..n]);
            }
            let after = fs::metadata(&path)?;
            if after.len() != file.size
                || after.modified()? != file.mtime
                || bytes.len() as u64 != file.size
            {
                return Err(Error::unavailable(60, "inbox file changed while read"));
            }
            Ok(Reply::Bytes(bytes))
        }
        Request::Adopt {
            file, path, sha, ..
        } => {
            let src = anchor(root, &file.path, false)?;
            let meta = fs::symlink_metadata(&src)?;
            if meta.len() != file.size
                || meta.modified()? != file.mtime
                || SystemTime::now()
                    .duration_since(meta.modified()?)
                    .unwrap_or_default()
                    .as_secs()
                    < 10
            {
                return Err(Error::unavailable(60, "inbox changed before adoption"));
            }
            let dest = anchor(root, &path, true)?;
            relocate(root, Some(&src), &dest, &sha, file.size, &gate, faults)
        }
        Request::Identity(_) => unreachable!(),
    }
}

#[cfg(test)]
mod verification_tests {
    use super::*;

    #[cfg(unix)]
    #[test]
    fn private_mode_correction_uses_the_pinned_directory_not_a_replacement_name() {
        use std::os::unix::fs::PermissionsExt;
        let dir = tempfile::tempdir().unwrap();
        let source = dir.path().join("new-claim");
        let retained = dir.path().join("retained-claim");
        fs::create_dir(&source).unwrap();
        fs::set_permissions(&source, fs::Permissions::from_mode(0o777)).unwrap();
        let pinned = anchor(dir.path(), "new-claim/payload", false).unwrap();
        fs::rename(&source, &retained).unwrap();
        fs::create_dir(&source).unwrap();
        fs::set_permissions(&source, fs::Permissions::from_mode(0o777)).unwrap();
        crate::sys::make_parent_private(&pinned).unwrap();
        assert_eq!(
            fs::metadata(retained).unwrap().permissions().mode() & 0o777,
            0o700
        );
        assert_eq!(
            fs::metadata(source).unwrap().permissions().mode() & 0o777,
            0o777
        );
    }

    #[cfg(unix)]
    #[test]
    fn an_existing_permissive_claim_directory_is_preserved_and_rejected() {
        use std::os::unix::fs::PermissionsExt;
        let dir = tempfile::tempdir().unwrap();
        let sha = crate::util::sha256_hex(b"private-mode fixture");
        let claim_dir = dir
            .path()
            .join(format!("library/.catalog/claims/sto/{sha}"));
        fs::create_dir_all(&claim_dir).unwrap();
        fs::set_permissions(&claim_dir, fs::Permissions::from_mode(0o777)).unwrap();
        assert!(claim(dir.path(), &dir.path().join("canonical.sto"), &sha, 20).is_err());
        assert_eq!(
            fs::metadata(claim_dir).unwrap().permissions().mode() & 0o777,
            0o777
        );
    }

    #[test]
    fn a_source_replaced_before_claim_is_preserved_as_a_conflict() {
        let dir = tempfile::tempdir().unwrap();
        let src = dir.path().join("source");
        let dest = dir.path().join("canonical.sto");
        let bytes = b"verified old bytes";
        fs::write(&src, bytes).unwrap();
        let gate: Gate = Arc::new(|| true);
        let expected = crate::util::sha256_hex(bytes);
        fs::rename(&src, dir.path().join("retained-original")).unwrap();
        fs::write(&src, vec![b'X'; bytes.len()]).unwrap();
        let reply = relocate(
            dir.path(),
            Some(&src),
            &dest,
            &expected,
            bytes.len() as u64,
            &gate,
            &Faults::default(),
        )
        .unwrap();
        assert!(matches!(reply, Reply::Conflict(_)));
        let claim = claim(dir.path(), &dest, &expected, bytes.len() as u64).unwrap();
        assert_eq!(fs::read(&claim.payload).unwrap(), vec![b'X'; bytes.len()]);
        assert!(!dest.exists());
        assert_eq!(
            fs::read(dir.path().join("retained-original")).unwrap(),
            bytes
        );
    }

    #[test]
    fn a_replacement_source_name_after_atomic_move_is_never_unlinked() {
        let dir = tempfile::tempdir().unwrap();
        let src = dir.path().join("source.sto");
        let dest = dir.path().join("canonical.sto");
        let original = b"verified source bytes";
        let replacement = b"new editor save must survive";
        fs::write(&src, original).unwrap();
        let expected = crate::util::sha256_hex(original);
        let (replace_source, canonical) = (src.clone(), dest.clone());
        let gate: Gate = Arc::new(move || {
            if canonical.exists() && !replace_source.exists() {
                fs::write(&replace_source, replacement).unwrap();
            }
            true
        });
        let reply = relocate(
            dir.path(),
            Some(&src),
            &dest,
            &expected,
            original.len() as u64,
            &gate,
            &Faults::default(),
        )
        .unwrap();
        assert!(matches!(reply, Reply::Present(_)));
        assert_eq!(fs::read(src).unwrap(), replacement);
        assert_eq!(fs::read(dest).unwrap(), original);
    }

    #[test]
    fn recovery_never_unlinks_an_existing_source_name_even_when_it_was_an_alias() {
        let dir = tempfile::tempdir().unwrap();
        let src = dir.path().join("source.sto");
        let dest = dir.path().join("canonical.sto");
        let bytes = b"recovery fixture";
        fs::write(&dest, bytes).unwrap();
        fs::hard_link(&dest, &src).unwrap();
        let gate: Gate = Arc::new(|| true);
        let reply = relocate(
            dir.path(),
            Some(&src),
            &dest,
            &crate::util::sha256_hex(bytes),
            bytes.len() as u64,
            &gate,
            &Faults::default(),
        )
        .unwrap();
        assert!(matches!(reply, Reply::Conflict(_)));
        assert_eq!(fs::read(src).unwrap(), bytes);
        assert_eq!(fs::read(dest).unwrap(), bytes);
    }
}

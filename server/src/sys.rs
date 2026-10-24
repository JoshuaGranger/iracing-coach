//! The only module with `unsafe`: process groups, `oom_score_adj`, disk free space, fadvise.
//! Everything here has a safe fallback on non-Unix targets (dev and tests on Windows).

#![allow(unsafe_code)]

use std::path::Path;

/// A NAS leaf anchored to open parent directories. Renaming an ancestor cannot redirect I/O.
#[derive(Clone)]
pub struct AnchoredPath {
    path: std::path::PathBuf,
    _parents: std::sync::Arc<Vec<std::fs::File>>,
}
impl std::ops::Deref for AnchoredPath {
    type Target = Path;
    fn deref(&self) -> &Path {
        &self.path
    }
}
impl AsRef<Path> for AnchoredPath {
    fn as_ref(&self) -> &Path {
        &self.path
    }
}

/// Correct NAS create-mode inheritance only on a directory this operation just created.
/// The descriptor remains pinned, so replacing its pathname cannot redirect chmod.
#[cfg(unix)]
pub fn make_parent_private(path: &AnchoredPath) -> std::io::Result<()> {
    use std::os::unix::fs::PermissionsExt;
    path._parents
        .last()
        .ok_or_else(|| std::io::Error::from(std::io::ErrorKind::InvalidInput))?
        .set_permissions(std::fs::Permissions::from_mode(0o700))
}

pub fn anchor_nas(root: &Path, relative: &str, create: bool) -> std::io::Result<AnchoredPath> {
    let parts: Vec<_> = relative.split('/').collect();
    if parts.is_empty()
        || parts
            .iter()
            .any(|s| s.is_empty() || *s == "." || *s == ".." || s.contains(['\\', ':']))
    {
        return Err(std::io::Error::from(std::io::ErrorKind::InvalidInput));
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        use std::os::unix::io::{AsRawFd, FromRawFd};
        let mut parents = vec![
            std::fs::OpenOptions::new()
                .read(true)
                .custom_flags(libc::O_DIRECTORY | libc::O_NOFOLLOW | libc::O_CLOEXEC)
                .open(root)?,
        ];
        for part in &parts[..parts.len() - 1] {
            let part = std::ffi::CString::new(*part)
                .map_err(|_| std::io::Error::from(std::io::ErrorKind::InvalidInput))?;
            let parent = parents.last().unwrap().as_raw_fd();
            // SAFETY: valid pinned directory fd and NUL-terminated single component.
            let mut fd = unsafe {
                libc::openat(
                    parent,
                    part.as_ptr(),
                    libc::O_RDONLY | libc::O_DIRECTORY | libc::O_NOFOLLOW | libc::O_CLOEXEC,
                )
            };
            if fd < 0
                && create
                && std::io::Error::last_os_error().kind() == std::io::ErrorKind::NotFound
            {
                // SAFETY: mkdirat operates relative to the same pinned parent.
                let rc = unsafe { libc::mkdirat(parent, part.as_ptr(), 0o777) };
                if rc < 0
                    && std::io::Error::last_os_error().kind() != std::io::ErrorKind::AlreadyExists
                {
                    return Err(std::io::Error::last_os_error());
                }
                // SAFETY: opening the created component refuses a replacement symlink.
                fd = unsafe {
                    libc::openat(
                        parent,
                        part.as_ptr(),
                        libc::O_RDONLY | libc::O_DIRECTORY | libc::O_NOFOLLOW | libc::O_CLOEXEC,
                    )
                };
            }
            if fd < 0 {
                return Err(std::io::Error::last_os_error());
            }
            // SAFETY: openat returned a newly-owned valid descriptor.
            parents.push(unsafe { std::fs::File::from_raw_fd(fd) });
        }
        let path = std::path::PathBuf::from(format!(
            "/proc/self/fd/{}/{}",
            parents.last().unwrap().as_raw_fd(),
            parts.last().unwrap()
        ));
        Ok(AnchoredPath {
            path,
            _parents: std::sync::Arc::new(parents),
        })
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::{MetadataExt, OpenOptionsExt};
        fn pin(path: &Path) -> std::io::Result<std::fs::File> {
            let file = std::fs::OpenOptions::new()
                .read(true)
                .share_mode(3)
                .custom_flags(0x02000000 | 0x00200000)
                .open(path)?;
            let meta = file.metadata()?;
            if !meta.is_dir() || meta.file_attributes() & 0x400 != 0 {
                return Err(std::io::Error::from(std::io::ErrorKind::PermissionDenied));
            }
            Ok(file)
        }
        let mut current = root.to_path_buf();
        let mut parents = vec![pin(&current)?];
        for part in &parts[..parts.len() - 1] {
            current.push(part);
            match pin(&current) {
                Ok(f) => parents.push(f),
                Err(e) if create && e.kind() == std::io::ErrorKind::NotFound => {
                    match std::fs::create_dir(&current) {
                        Ok(()) => {}
                        Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {}
                        Err(e) => return Err(e),
                    }
                    parents.push(pin(&current)?);
                }
                Err(e) => return Err(e),
            }
        }
        current.push(parts.last().unwrap());
        Ok(AnchoredPath {
            path: current,
            _parents: std::sync::Arc::new(parents),
        })
    }
    #[cfg(not(any(unix, windows)))]
    {
        let _ = (root, create);
        Err(std::io::Error::from(std::io::ErrorKind::Unsupported))
    }
}

/// Open an existing NAS leaf without following a replacement symlink/reparse point.
pub fn open_nofollow(path: &Path) -> std::io::Result<std::fs::File> {
    let mut options = std::fs::OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC);
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        options.share_mode(3).custom_flags(0x00200000);
    }
    let file = options.open(path)?;
    let meta = file.metadata()?;
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        if meta.file_attributes() & 0x400 != 0 {
            return Err(std::io::Error::from(std::io::ErrorKind::PermissionDenied));
        }
    }
    if !meta.is_file() {
        return Err(std::io::Error::from(std::io::ErrorKind::InvalidInput));
    }
    Ok(file)
}

/// Physical identity, used only to recover an interrupted no-replace NAS relocation.
pub fn same_file(a: &Path, b: &Path) -> std::io::Result<bool> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        let (a, b) = (std::fs::metadata(a)?, std::fs::metadata(b)?);
        Ok(a.dev() == b.dev() && a.ino() == b.ino())
    }
    #[cfg(windows)]
    {
        use std::os::windows::io::AsRawHandle;
        #[link(name = "kernel32")]
        unsafe extern "system" {
            fn GetFileInformationByHandle(
                handle: *mut std::ffi::c_void,
                info: *mut [u32; 13],
            ) -> i32;
        }
        let (a, b) = (std::fs::File::open(a)?, std::fs::File::open(b)?);
        let (mut ai, mut bi) = ([0; 13], [0; 13]);
        // SAFETY: valid live handles and correctly-sized BY_HANDLE_FILE_INFORMATION buffers.
        if unsafe { GetFileInformationByHandle(a.as_raw_handle(), &mut ai) } == 0
            || unsafe { GetFileInformationByHandle(b.as_raw_handle(), &mut bi) } == 0
        {
            return Err(std::io::Error::last_os_error());
        }
        Ok(ai[6] == bi[6] && ai[11..13] == bi[11..13])
    }
    #[cfg(not(any(unix, windows)))]
    {
        let _ = (a, b);
        Ok(false)
    }
}

pub fn file_identity(path: &Path) -> std::io::Result<String> {
    file_identity_open(&open_nofollow(path)?)
}

pub fn file_identity_open(file: &std::fs::File) -> std::io::Result<String> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        let m = file.metadata()?;
        Ok(format!("{}:{}", m.dev(), m.ino()))
    }
    #[cfg(windows)]
    {
        use std::os::windows::io::AsRawHandle;
        #[link(name = "kernel32")]
        unsafe extern "system" {
            fn GetFileInformationByHandle(
                handle: *mut std::ffi::c_void,
                info: *mut [u32; 13],
            ) -> i32;
        }
        let mut info = [0; 13];
        // SAFETY: valid live file handle and correctly-sized Windows information buffer.
        if unsafe { GetFileInformationByHandle(file.as_raw_handle(), &mut info) } == 0 {
            return Err(std::io::Error::last_os_error());
        }
        Ok(format!("{}:{}:{}", info[6], info[11], info[12]))
    }
    #[cfg(not(any(unix, windows)))]
    {
        let _ = file;
        Ok(String::new())
    }
}

/// Free bytes on the filesystem holding `path`.
pub fn free_bytes(path: &Path) -> std::io::Result<u64> {
    #[cfg(unix)]
    {
        use std::os::unix::ffi::OsStrExt;
        let c_path = std::ffi::CString::new(path.as_os_str().as_bytes())
            .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidInput, e))?;
        let mut stat: libc::statvfs = unsafe { std::mem::zeroed() };
        // SAFETY: `c_path` is a valid NUL-terminated string and `stat` is a valid out pointer.
        let rc = unsafe { libc::statvfs(c_path.as_ptr(), &mut stat) };
        if rc != 0 {
            return Err(std::io::Error::last_os_error());
        }
        Ok((stat.f_bavail as u64).saturating_mul(stat.f_frsize as u64))
    }
    #[cfg(not(unix))]
    {
        let _ = path;
        Ok(u64::MAX / 2)
    }
}

/// Sends `signal` to the process group `pid` (the worker is its own group leader).
pub fn signal_group(pid: u32, kill: bool) {
    #[cfg(unix)]
    {
        if pid == 0 {
            return; // -0 would be the server's own process group
        }
        let signal = if kill { libc::SIGKILL } else { libc::SIGTERM };
        // SAFETY: plain syscall with integer arguments; a stale pid at worst signals nothing.
        unsafe {
            libc::kill(-(pid as i32), signal);
        }
    }
    #[cfg(not(unix))]
    {
        let _ = (pid, kill);
    }
}

/// True while any process of the group led by `pid` exists (a zombie leader counts until reaped).
pub fn group_alive(pid: u32) -> bool {
    #[cfg(unix)]
    {
        if pid == 0 {
            return false;
        }
        // SAFETY: signal 0 only checks for existence.
        let rc = unsafe { libc::kill(-(pid as i32), 0) };
        rc == 0 || std::io::Error::last_os_error().raw_os_error() != Some(libc::ESRCH)
    }
    #[cfg(not(unix))]
    {
        let _ = pid;
        false
    }
}

/// Own process group and (optionally) `oom_score_adj = 1000` for a worker child.
pub fn configure_child(cmd: &mut tokio::process::Command, set_oom_score: bool) {
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        cmd.process_group(0);
        if set_oom_score {
            // SAFETY: the closure runs between fork and exec and only calls async-signal-safe
            // functions (open, write, close) through std::fs on a fixed path.
            unsafe {
                cmd.as_std_mut().pre_exec(|| {
                    let _ = std::fs::write("/proc/self/oom_score_adj", b"1000");
                    Ok(())
                });
            }
        }
    }
    #[cfg(not(unix))]
    {
        let _ = (cmd, set_oom_score);
    }
}

/// `oom_kill` counter of this container's cgroup (Linux), 0 elsewhere.
pub fn cgroup_oom_kills() -> u64 {
    std::fs::read_to_string("/sys/fs/cgroup/memory.events")
        .ok()
        .and_then(|text| {
            text.lines()
                .find_map(|line| line.strip_prefix("oom_kill "))
                .and_then(|v| v.trim().parse().ok())
        })
        .unwrap_or(0)
}

/// Drops the page cache for a file so the next read comes from the (NFS) server.
pub fn drop_cache(file: &std::fs::File) {
    #[cfg(target_os = "linux")]
    {
        use std::os::unix::io::AsRawFd;
        // SAFETY: fd is valid for the lifetime of `file`; advisory call, failure is ignored.
        unsafe {
            libc::posix_fadvise(file.as_raw_fd(), 0, 0, libc::POSIX_FADV_DONTNEED);
        }
    }
    #[cfg(not(target_os = "linux"))]
    {
        let _ = file;
    }
}

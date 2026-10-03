//! The only module with `unsafe`: process groups, `oom_score_adj`, disk free space, fadvise.
//! Everything here has a safe fallback on non-Unix targets (dev and tests on Windows).

#![allow(unsafe_code)]

use std::path::Path;

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

//! Directory-handle-pinned managed writes. Pathname checks alone cannot prevent parent swaps.
#[cfg(any(windows, test))]
use std::fs;
use std::fs::File;
#[cfg(windows)]
use std::fs::OpenOptions;
use std::io;
use std::path::{Component, Path, PathBuf};

use crate::scan::is_reparse;
use crate::util::uuid_v4;

pub(crate) struct Dir {
    _path: PathBuf,
    _handles: Vec<File>,
}

impl Dir {
    pub(crate) fn open(path: &Path, create: bool, proceed: &dyn Fn() -> bool) -> io::Result<Self> {
        let absolute = std::path::absolute(path)?;
        let mut current = PathBuf::new();
        let mut handles = Vec::new();
        for part in absolute.components() {
            if !proceed() {
                return Err(io::Error::other("managed file work paused"));
            }
            match part {
                Component::Prefix(prefix) => {
                    current.push(prefix.as_os_str());
                    continue;
                }
                Component::RootDir => current.push(part.as_os_str()),
                Component::Normal(name) => current.push(name),
                _ => return Err(io::Error::other("non-normal managed directory path")),
            }
            let handle = open_dir(&current, handles.last(), part, create)?;
            let meta = handle.metadata()?;
            if !meta.is_dir() || is_reparse(&meta) {
                return Err(io::Error::other("managed directory is redirected"));
            }
            handles.push(handle);
        }
        if handles.is_empty() {
            return Err(io::Error::other("managed directory has no root"));
        }
        Ok(Self {
            _path: absolute,
            _handles: handles,
        })
    }
    #[cfg(unix)]
    fn handle(&self) -> &File {
        self._handles.last().expect("pinned root")
    }
    pub(crate) fn open_regular(&self, name: &str) -> io::Result<File> {
        validate_leaf(name)?;
        let file = open_read(self, name)?;
        let meta = file.metadata()?;
        if !meta.is_file() || is_reparse(&meta) {
            return Err(io::Error::other("managed target is not a regular file"));
        }
        Ok(file)
    }
    pub(crate) fn temp(&self) -> io::Result<Temp<'_>> {
        let name = format!(".coach-sync-{}.tmp", uuid_v4());
        let file = create_temp(self, &name)?;
        Ok(Temp {
            dir: self,
            _name: name,
            file: Some(file),
            cleanup: true,
            _staged: None,
        })
    }
    pub(crate) fn sync(&self) -> io::Result<()> {
        #[cfg(unix)]
        {
            self.handle().sync_all()?;
        }
        Ok(())
    }
}

pub(crate) struct Temp<'a> {
    dir: &'a Dir,
    _name: String,
    file: Option<File>,
    cleanup: bool,
    _staged: Option<String>,
}
impl Temp<'_> {
    pub(crate) fn file(&mut self) -> &mut File {
        self.file.as_mut().expect("owned temp handle")
    }
    pub(crate) fn publish_new(&self, name: &str) -> io::Result<()> {
        validate_leaf(name)?;
        publish(self, name)?;
        self.dir.sync()
    }
    pub(crate) fn replace(mut self, name: &str) -> io::Result<()> {
        validate_leaf(name)?;
        replace(&mut self, name)?;
        self.dir.sync()?;
        self.cleanup = false;
        Ok(())
    }
}
impl Drop for Temp<'_> {
    fn drop(&mut self) {
        if self.cleanup {
            cleanup(self);
        }
    }
}
fn validate_leaf(name: &str) -> io::Result<()> {
    if name.is_empty() || matches!(name, "." | "..") || name.contains(['/', '\\', ':', '\0']) {
        Err(io::Error::other("invalid managed leaf name"))
    } else {
        Ok(())
    }
}

#[cfg(windows)]
fn open_dir(
    path: &Path,
    _parent: Option<&File>,
    _part: Component<'_>,
    create: bool,
) -> io::Result<File> {
    use std::os::windows::fs::OpenOptionsExt;
    use windows::Win32::Storage::FileSystem::{
        FILE_FLAG_BACKUP_SEMANTICS, FILE_FLAG_OPEN_REPARSE_POINT, FILE_SHARE_READ, FILE_SHARE_WRITE,
    };
    let open = || {
        OpenOptions::new()
            .read(true)
            .share_mode((FILE_SHARE_READ | FILE_SHARE_WRITE).0)
            .custom_flags((FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT).0)
            .open(path)
    };
    match open() {
        Err(e) if create && e.kind() == io::ErrorKind::NotFound => {
            fs::create_dir(path)?;
            open()
        }
        result => result,
    }
}
#[cfg(windows)]
fn open_read(dir: &Dir, name: &str) -> io::Result<File> {
    use std::os::windows::fs::OpenOptionsExt;
    use windows::Win32::Storage::FileSystem::{
        FILE_FLAG_OPEN_REPARSE_POINT, FILE_SHARE_READ, FILE_SHARE_WRITE,
    };
    OpenOptions::new()
        .read(true)
        .share_mode((FILE_SHARE_READ | FILE_SHARE_WRITE).0)
        .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT.0)
        .open(dir._path.join(name))
}
#[cfg(windows)]
fn create_temp(dir: &Dir, name: &str) -> io::Result<File> {
    use std::os::windows::fs::OpenOptionsExt;
    use windows::Win32::Storage::FileSystem::{FILE_FLAG_OPEN_REPARSE_POINT, FILE_SHARE_READ};
    OpenOptions::new()
        .read(true)
        .write(true)
        .create_new(true)
        .share_mode(FILE_SHARE_READ.0)
        .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT.0)
        .open(dir._path.join(name))
}
#[cfg(windows)]
fn publish(temp: &Temp<'_>, name: &str) -> io::Result<()> {
    // Every ancestor denies DELETE sharing. The source handle denies WRITE and DELETE
    // sharing until after publication and durable receipt commit, so neither path can swap.
    fs::hard_link(temp.dir._path.join(&temp._name), temp.dir._path.join(name))
}
#[cfg(windows)]
fn replace(temp: &mut Temp<'_>, name: &str) -> io::Result<()> {
    temp.file.take();
    use std::os::windows::ffi::OsStrExt;
    use windows::Win32::Storage::FileSystem::{
        MOVEFILE_REPLACE_EXISTING, MOVEFILE_WRITE_THROUGH, MoveFileExW,
    };
    use windows::core::PCWSTR;
    let from: Vec<u16> = temp
        .dir
        ._path
        .join(&temp._name)
        .as_os_str()
        .encode_wide()
        .chain(Some(0))
        .collect();
    let to: Vec<u16> = temp
        .dir
        ._path
        .join(name)
        .as_os_str()
        .encode_wide()
        .chain(Some(0))
        .collect();
    // SAFETY: both paths are owned NUL-terminated strings beneath directory handles that
    // deny rename/delete sharing. This replaces only the app's own state file.
    unsafe {
        MoveFileExW(
            PCWSTR(from.as_ptr()),
            PCWSTR(to.as_ptr()),
            MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
        )
    }
    .map_err(|e| io::Error::from_raw_os_error(e.code().0 & 0xffff))
}
#[cfg(windows)]
fn cleanup(temp: &mut Temp<'_>) {
    temp.file.take();
    let _ = fs::remove_file(temp.dir._path.join(&temp._name));
}

#[cfg(unix)]
fn cstr(text: &std::ffi::OsStr) -> io::Result<std::ffi::CString> {
    use std::os::unix::ffi::OsStrExt;
    std::ffi::CString::new(text.as_bytes()).map_err(|_| io::Error::other("NUL in managed path"))
}
#[cfg(unix)]
fn owned_fd(fd: libc::c_int) -> io::Result<File> {
    use std::os::fd::FromRawFd;
    if fd < 0 {
        Err(io::Error::last_os_error())
    } else {
        // SAFETY: this successful open returned a new descriptor transferred to File.
        Ok(unsafe { File::from_raw_fd(fd) })
    }
}
#[cfg(unix)]
fn open_dir(
    _path: &Path,
    parent: Option<&File>,
    part: Component<'_>,
    create: bool,
) -> io::Result<File> {
    use std::os::fd::AsRawFd;
    let component = cstr(part.as_os_str())?;
    let parent = parent.map_or(libc::AT_FDCWD, AsRawFd::as_raw_fd);
    let open = || {
        // SAFETY: parent stays open and component is a NUL-terminated OS component.
        owned_fd(unsafe {
            libc::openat(
                parent,
                component.as_ptr(),
                libc::O_RDONLY | libc::O_DIRECTORY | libc::O_NOFOLLOW | libc::O_CLOEXEC,
            )
        })
    };
    match open() {
        Err(e) if create && e.kind() == io::ErrorKind::NotFound => {
            // SAFETY: the pinned parent and validated single component scope this mkdir.
            if unsafe { libc::mkdirat(parent, component.as_ptr(), 0o700) } < 0 {
                return Err(io::Error::last_os_error());
            }
            open()
        }
        result => result,
    }
}
#[cfg(unix)]
fn open_read(dir: &Dir, name: &str) -> io::Result<File> {
    use std::os::fd::AsRawFd;
    let name = cstr(std::ffi::OsStr::new(name))?;
    // SAFETY: dir owns its descriptor and name is one validated NUL-terminated leaf.
    owned_fd(unsafe {
        libc::openat(
            dir.handle().as_raw_fd(),
            name.as_ptr(),
            libc::O_RDONLY | libc::O_NOFOLLOW | libc::O_CLOEXEC,
        )
    })
}
#[cfg(target_os = "linux")]
fn create_temp(dir: &Dir, _name: &str) -> io::Result<File> {
    use std::os::fd::AsRawFd;
    // SAFETY: the dot path refers to the pinned directory; this creates an unnamed private
    // inode, so no other process can replace its staging pathname between hash and publish.
    owned_fd(unsafe {
        libc::openat(
            dir.handle().as_raw_fd(),
            c".".as_ptr(),
            libc::O_TMPFILE | libc::O_RDWR | libc::O_CLOEXEC,
            0o600,
        )
    })
}
#[cfg(all(unix, not(target_os = "linux")))]
fn create_temp(_dir: &Dir, _name: &str) -> io::Result<File> {
    Err(io::Error::other(
        "managed sync requires Linux unnamed-temp support",
    ))
}
#[cfg(unix)]
fn publish(temp: &Temp<'_>, name: &str) -> io::Result<()> {
    use std::os::fd::AsRawFd;
    let proc_path = std::ffi::CString::new(format!(
        "/proc/self/fd/{}",
        temp.file.as_ref().expect("owned temp").as_raw_fd()
    ))
    .expect("decimal descriptor");
    let target = cstr(std::ffi::OsStr::new(name))?;
    // SAFETY: proc_path refers to our retained source descriptor, target is a single leaf,
    // and dir's descriptor pins the destination directory even if an ancestor is renamed.
    if unsafe {
        libc::linkat(
            libc::AT_FDCWD,
            proc_path.as_ptr(),
            temp.dir.handle().as_raw_fd(),
            target.as_ptr(),
            libc::AT_SYMLINK_FOLLOW,
        )
    } < 0
    {
        return Err(io::Error::last_os_error());
    }
    Ok(())
}
#[cfg(unix)]
fn replace(temp: &mut Temp<'_>, name: &str) -> io::Result<()> {
    use std::os::fd::AsRawFd;
    let staging = format!(".coach-receipt-{}.tmp", uuid_v4());
    publish(temp, &staging)?;
    temp._staged = Some(staging.clone());
    let from = cstr(std::ffi::OsStr::new(&staging))?;
    let to = cstr(std::ffi::OsStr::new(name))?;
    let parent = temp.dir.handle().as_raw_fd();
    // SAFETY: both names are validated leaves relative to the same retained directory.
    if unsafe { libc::renameat(parent, from.as_ptr(), parent, to.as_ptr()) } < 0 {
        return Err(io::Error::last_os_error());
    }
    temp._staged = None;
    Ok(())
}
#[cfg(unix)]
fn cleanup(temp: &mut Temp<'_>) {
    use std::os::fd::AsRawFd;
    if let Some(staged) = temp._staged.take()
        && let Ok(name) = cstr(std::ffi::OsStr::new(&staged))
    {
        // SAFETY: this is our random staging name in the pinned directory; unlinkat does
        // not follow its final component and cannot delete a target behind a symlink.
        let _ = unsafe { libc::unlinkat(temp.dir.handle().as_raw_fd(), name.as_ptr(), 0) };
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;
    #[test]
    fn publish_is_no_replace_and_temp_is_private_until_publication() {
        let root = std::env::temp_dir().join(format!("coach-pinned-{}", uuid_v4()));
        fs::create_dir_all(&root).unwrap();
        let dir = Dir::open(&root, true, &|| true).unwrap();
        let mut temp = dir.temp().unwrap();
        temp.file().write_all(b"owned").unwrap();
        temp.file().sync_all().unwrap();
        assert_eq!(
            fs::read_dir(&root)
                .unwrap()
                .filter(|e| e
                    .as_ref()
                    .unwrap()
                    .path()
                    .extension()
                    .is_some_and(|s| s == "sto"))
                .count(),
            0
        );
        temp.publish_new("owned.sto").unwrap();
        assert!(temp.publish_new("owned.sto").is_err());
        assert_eq!(fs::read(root.join("owned.sto")).unwrap(), b"owned");
        drop(temp);
        drop(dir);
        assert_eq!(fs::read_dir(&root).unwrap().count(), 1);
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn parent_swap_cannot_redirect_create_publish_or_cleanup() {
        let root = std::env::temp_dir().join(format!("coach-pinned-{}", uuid_v4()));
        let managed = root.join("inside/Coach Synced");
        let outside = root.join("outside");
        fs::create_dir_all(&managed).unwrap();
        fs::create_dir(&outside).unwrap();
        let dir = Dir::open(&managed, false, &|| true).unwrap();
        #[cfg(windows)]
        assert!(
            fs::rename(root.join("inside"), root.join("displaced")).is_err(),
            "pinned Windows ancestor must deny rename/delete"
        );
        #[cfg(unix)]
        {
            fs::rename(root.join("inside"), root.join("displaced")).unwrap();
            std::os::unix::fs::symlink(&outside, root.join("inside")).unwrap();
        }
        let mut temp = dir.temp().unwrap();
        temp.file().write_all(b"owned").unwrap();
        temp.file().sync_all().unwrap();
        temp.publish_new("owned.sto").unwrap();
        drop(temp);
        drop(dir);
        assert_eq!(fs::read_dir(&outside).unwrap().count(), 0);
        #[cfg(windows)]
        assert_eq!(fs::read(managed.join("owned.sto")).unwrap(), b"owned");
        #[cfg(unix)]
        assert_eq!(
            fs::read(root.join("displaced/Coach Synced/owned.sto")).unwrap(),
            b"owned"
        );
        fs::remove_dir_all(root).unwrap();
    }
}

//! A read-only memory mapping of a whole file (port phase 3, `docs/08-rewrite/port/phase3-writer.md`
//! section 3): the kernels child and the merge stage map a coach column store instead of reading
//! it, so a column costs memory only when a kernel touches its pages, and the pages are file
//! backed (no commit charge; the system drops and re-reads them under pressure).
//!
//! The only `unsafe` code of the crate. No dependency is added: the two system calls per platform
//! are declared here, as `coach-numerics` declares its C library functions.
//!
//! Soundness rests on one rule, which is the caller's to keep and therefore the precondition of
//! the `unsafe` [`Mapping::open`] (Codex phase-3 review, finding 2): nothing writes or truncates
//! the file while it is mapped. The worker keeps it by mapping only a private file in the job's
//! `coach-decode-*` or `coach-merge-*` directory, which the decode and merge stages write
//! completely and exit before any reader starts. On Windows the rule is also enforced: the
//! mapping keeps its file handle, opened without write sharing, so any later open for writing
//! fails while the mapping lives (and the system refuses to truncate a mapped file). On Linux
//! nothing can enforce it (a truncation would raise `SIGBUS`), so a reader of a file it does not
//! own uses an owned buffer instead (`ColumnStore::read`). A file of zero bytes is not mapped (the
//! system refuses a zero-length mapping) and reads as an empty slice.

use std::fs::File;
use std::io;
use std::path::Path;

/// A whole file mapped read-only; unmapped on drop.
pub struct Mapping {
    base: *const u8,
    len: usize,
    /// Windows: the handle opened without write sharing, held for the mapping's life.
    _file: Option<File>,
}

/// The length of a mapping of a file of `bytes` bytes: a slice over it must not exceed
/// `isize::MAX` bytes (`slice::from_raw_parts`), which a 32-bit process could otherwise map
/// (Codex phase-3 review, finding 3).
pub fn mappable_len(bytes: u64) -> io::Result<usize> {
    usize::try_from(bytes)
        .ok()
        .filter(|&len| isize::try_from(len).is_ok())
        .ok_or_else(|| io::Error::other("file larger than a slice can address (isize::MAX)"))
}

impl std::fmt::Debug for Mapping {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Mapping").field("len", &self.len).finish()
    }
}

impl Mapping {
    /// Maps `path` read-only.
    ///
    /// # Safety
    ///
    /// Nothing may write or truncate the file while the returned mapping (or a slice of it)
    /// lives: the slices it hands out are immutable byte slices of the file's pages. On Windows
    /// the mapping holds the file open without write sharing, so an open for writing made after
    /// this call fails; one made before it makes this call fail. On other systems the caller
    /// alone keeps the rule (module documentation).
    pub unsafe fn open(path: &Path) -> io::Result<Mapping> {
        let file = sys::open(path)?;
        let len = mappable_len(file.metadata()?.len())?;
        if len == 0 {
            return Ok(Mapping {
                base: std::ptr::null(),
                len: 0,
                _file: sys::keep(file),
            });
        }
        let base = sys::map(&file, len)?;
        Ok(Mapping {
            base,
            len,
            _file: sys::keep(file),
        })
    }

    pub fn as_slice(&self) -> &[u8] {
        if self.len == 0 {
            return &[];
        }
        // SAFETY: `base` is the start of a live read-only mapping of exactly `len` bytes
        // (`len <= isize::MAX`, `mappable_len`), which stays mapped until `self` drops; the slice
        // borrows `self`. Nothing writes the file while it is mapped: the precondition of
        // `Mapping::open`, enforced on Windows by the handle `self` holds.
        unsafe { std::slice::from_raw_parts(self.base, self.len) }
    }
}

impl Drop for Mapping {
    fn drop(&mut self) {
        if self.len != 0 {
            sys::unmap(self.base, self.len);
        }
    }
}

#[cfg(windows)]
mod sys {
    use std::ffi::c_void;
    use std::fs::File;
    use std::io;
    use std::os::windows::io::AsRawHandle;

    const PAGE_READONLY: u32 = 0x02;
    const FILE_MAP_READ: u32 = 0x0004;
    const FILE_SHARE_READ: u32 = 0x1;
    const FILE_SHARE_DELETE: u32 = 0x4;

    /// Opens for reading and shares reading and deletion, not writing: while the handle lives
    /// no other open for writing succeeds, and an existing writer's handle makes this fail.
    pub fn open(path: &std::path::Path) -> io::Result<File> {
        use std::os::windows::fs::OpenOptionsExt;
        std::fs::OpenOptions::new()
            .read(true)
            .share_mode(FILE_SHARE_READ | FILE_SHARE_DELETE)
            .open(path)
    }

    /// The handle is kept for the mapping's life.
    pub fn keep(file: File) -> Option<File> {
        Some(file)
    }

    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn CreateFileMappingW(
            file: *mut c_void,
            attributes: *mut c_void,
            protect: u32,
            maximum_size_high: u32,
            maximum_size_low: u32,
            name: *const u16,
        ) -> *mut c_void;
        fn MapViewOfFile(
            mapping: *mut c_void,
            access: u32,
            offset_high: u32,
            offset_low: u32,
            bytes: usize,
        ) -> *mut c_void;
        fn UnmapViewOfFile(base: *const c_void) -> i32;
        fn CloseHandle(handle: *mut c_void) -> i32;
    }

    pub fn map(file: &File, len: usize) -> io::Result<*const u8> {
        // SAFETY: the handle is a valid open file handle for the duration of both calls; a null
        // name and null attributes are permitted; the size arguments 0/0 map the whole file.
        // The mapping object may be closed once the view exists (the view keeps it alive).
        unsafe {
            let mapping = CreateFileMappingW(
                file.as_raw_handle(),
                std::ptr::null_mut(),
                PAGE_READONLY,
                0,
                0,
                std::ptr::null(),
            );
            if mapping.is_null() {
                return Err(io::Error::last_os_error());
            }
            let view = MapViewOfFile(mapping, FILE_MAP_READ, 0, 0, len);
            let error = io::Error::last_os_error();
            CloseHandle(mapping);
            if view.is_null() {
                return Err(error);
            }
            Ok(view as *const u8)
        }
    }

    pub fn unmap(base: *const u8, _len: usize) {
        // SAFETY: `base` came from MapViewOfFile and is unmapped once, by Mapping::drop.
        unsafe {
            UnmapViewOfFile(base as *const c_void);
        }
    }
}

#[cfg(unix)]
mod sys {
    use std::ffi::c_void;
    use std::fs::File;
    use std::io;
    use std::os::fd::AsRawFd;

    const PROT_READ: i32 = 1;
    const MAP_PRIVATE: i32 = 2;

    /// A plain read-only open: Unix has no mandatory share modes (the caller keeps the rule).
    pub fn open(path: &std::path::Path) -> io::Result<File> {
        File::open(path)
    }

    /// The mapping keeps its own reference; the descriptor is closed.
    pub fn keep(_file: File) -> Option<File> {
        None
    }

    unsafe extern "C" {
        fn mmap(
            address: *mut c_void,
            length: usize,
            protect: i32,
            flags: i32,
            fd: i32,
            offset: i64,
        ) -> *mut c_void;
        fn munmap(address: *mut c_void, length: usize) -> i32;
    }

    pub fn map(file: &File, len: usize) -> io::Result<*const u8> {
        // SAFETY: a valid descriptor, a non-zero length within the file, offset 0; a read-only
        // private mapping. MAP_FAILED is (void *) -1. The descriptor may be closed afterwards.
        let base = unsafe {
            mmap(
                std::ptr::null_mut(),
                len,
                PROT_READ,
                MAP_PRIVATE,
                file.as_raw_fd(),
                0,
            )
        };
        if base as isize == -1 {
            return Err(io::Error::last_os_error());
        }
        Ok(base as *const u8)
    }

    pub fn unmap(base: *const u8, len: usize) {
        // SAFETY: `base`/`len` are exactly one live mapping, unmapped once, by Mapping::drop.
        unsafe {
            munmap(base as *mut c_void, len);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{Mapping, mappable_len};

    #[test]
    fn maps_whole_files_and_empty_ones() {
        let dir = std::env::temp_dir().join(format!("coach-mapped-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("bytes.bin");
        let data: Vec<u8> = (0..10_000u32).map(|i| (i % 251) as u8).collect();
        std::fs::write(&path, &data).unwrap();
        // SAFETY: the test's own file; nothing writes it while mapped.
        let mapping = unsafe { Mapping::open(&path) }.unwrap();
        assert_eq!(mapping.as_slice(), data.as_slice());
        // Review finding 2: on Windows the mapping denies write sharing, so the file cannot be
        // opened for writing (or truncated) while it is mapped; reading stays possible.
        #[cfg(windows)]
        {
            assert!(std::fs::OpenOptions::new().write(true).open(&path).is_err());
            assert!(std::fs::File::create(&path).is_err());
            assert_eq!(std::fs::read(&path).unwrap(), data);
        }
        assert_eq!(mapping.as_slice(), data.as_slice());
        drop(mapping);
        // Unmapped, the file is writable again.
        std::fs::write(&path, b"x").unwrap();
        let empty = dir.join("empty.bin");
        std::fs::write(&empty, b"").unwrap();
        // SAFETY: as above.
        assert!(
            unsafe { Mapping::open(&empty) }
                .unwrap()
                .as_slice()
                .is_empty()
        );
        assert!(unsafe { Mapping::open(&dir.join("missing.bin")) }.is_err());
        std::fs::remove_dir_all(&dir).unwrap();
    }

    /// Review finding 2: a file already open for writing is not mapped (Windows).
    #[cfg(windows)]
    #[test]
    fn a_file_open_for_writing_is_not_mapped() {
        let dir = std::env::temp_dir().join(format!("coach-mapped-w-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("busy.bin");
        let writer = std::fs::File::create(&path).unwrap();
        std::io::Write::write_all(&mut &writer, b"0123456789").unwrap();
        // SAFETY: the open must fail; nothing is mapped.
        assert!(unsafe { Mapping::open(&path) }.is_err());
        drop(writer);
        // SAFETY: the test's own file; nothing writes it while mapped.
        assert_eq!(
            unsafe { Mapping::open(&path) }.unwrap().as_slice(),
            b"0123456789"
        );
        std::fs::remove_dir_all(&dir).unwrap();
    }

    /// Review finding 3: a length beyond `isize::MAX` is refused before mapping.
    #[test]
    fn lengths_beyond_isize_max_are_refused() {
        assert_eq!(mappable_len(0).unwrap(), 0);
        assert_eq!(mappable_len(4096).unwrap(), 4096);
        let largest = isize::MAX as u64;
        assert_eq!(mappable_len(largest).unwrap() as u64, largest);
        assert!(mappable_len(largest + 1).is_err());
        assert!(mappable_len(u64::MAX).is_err());
    }
}

//! SQLite store: one writer thread (all writes serialized), a small pool of read-only
//! connections, additive migrations (`server/migrations/`; their concatenation is
//! `docs/08-rewrite/contracts/db-schema.sql`, which a test pins).

use std::panic::{AssertUnwindSafe, catch_unwind};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::mpsc;
use std::sync::{Arc, Mutex};
use std::thread::JoinHandle;

use rusqlite::{Connection, OpenFlags, Transaction};

use crate::error::{Error, Result};

type WriteJob = Box<dyn FnOnce(&mut Connection) + Send>;

/// (version, description, SQL). Version 1 carries its own `schema_migrations` row.
pub const MIGRATIONS: &[(i64, &str, &str)] = &[
    (
        1,
        "initial rewrite schema",
        include_str!("../migrations/0001_initial.sql"),
    ),
    (
        2,
        "garage snapshots wait for their visit commit",
        include_str!("../migrations/0002_garage_snapshots.sql"),
    ),
    (
        3,
        "lab library: recordings carry main or lab, lab windows",
        include_str!("../migrations/0003_lab_library.sql"),
    ),
    (
        4,
        "durable field recordings",
        include_str!("../migrations/0004_field_recordings.sql"),
    ),
    (
        5,
        "immutable STO working versions",
        include_str!("../migrations/0005_sto_versions.sql"),
    ),
];

pub const SCHEMA_VERSION: i64 = 5;

pub struct Store {
    writer: Mutex<Option<mpsc::Sender<WriteJob>>>,
    thread: Mutex<Option<JoinHandle<()>>>,
    readers: Vec<Arc<Mutex<Connection>>>,
    next: AtomicUsize,
    path: PathBuf,
}

fn apply_pragmas(conn: &Connection) -> rusqlite::Result<()> {
    conn.busy_timeout(std::time::Duration::from_millis(5000))?;
    conn.pragma_update(None, "foreign_keys", true)?;
    Ok(())
}

fn current_version(conn: &Connection) -> rusqlite::Result<i64> {
    let has: bool = conn.query_row(
        "SELECT COUNT(*) > 0 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'",
        [],
        |r| r.get(0),
    )?;
    if !has {
        return Ok(0);
    }
    conn.query_row(
        "SELECT COALESCE(MAX(version), 0) FROM schema_migrations",
        [],
        |r| r.get(0),
    )
}

/// Runs pending migrations. Before changing an existing database it writes
/// `pre-migrate-v<N>.sqlite3` next to it.
pub fn migrate(conn: &mut Connection, db_path: &Path, app_version: &str) -> Result<()> {
    migrate_with(conn, db_path, app_version, MIGRATIONS)
}

pub fn migrate_with(
    conn: &mut Connection,
    db_path: &Path,
    app_version: &str,
    migrations: &[(i64, &str, &str)],
) -> Result<()> {
    let current = current_version(conn)?;
    let pending: Vec<_> = migrations.iter().filter(|(v, _, _)| *v > current).collect();
    if pending.is_empty() {
        return Ok(());
    }
    if current > 0 {
        let backup = db_path.with_file_name(format!("pre-migrate-v{current}.sqlite3"));
        if !backup.exists() {
            let text = backup.to_string_lossy().replace('\'', "''");
            conn.execute_batch(&format!("VACUUM INTO '{text}'"))?;
        }
    }
    for (version, description, sql) in pending {
        if *version == 4 {
            conn.pragma_update(None, "foreign_keys", false)?;
        }
        let migrated = (|| -> Result<()> {
            let tx = conn.transaction()?;
            tx.execute_batch(sql)?;
            if *version > 1 {
                tx.execute(
                "INSERT INTO schema_migrations(version, applied_at, description, app_version) VALUES (?1, ?2, ?3, ?4)",
                rusqlite::params![version, crate::util::iso_now(), description, app_version],
            )?;
            }
            if *version == 4 {
                let violations: i64 =
                    tx.query_row("SELECT COUNT(*) FROM pragma_foreign_key_check", [], |r| {
                        r.get(0)
                    })?;
                if violations != 0 {
                    return Err(Error::internal(
                        "field migration failed foreign key validation",
                    ));
                }
            }
            tx.commit()?;
            Ok(())
        })();
        if *version == 4 {
            conn.pragma_update(None, "foreign_keys", true)?;
        }
        migrated?;
    }
    conn.execute(
        "INSERT OR IGNORE INTO server_counters(name, value) VALUES ('user_data_generation', 0)",
        [],
    )?;
    Ok(())
}

impl Store {
    pub fn open(path: &Path, app_version: &str) -> Result<Store> {
        if let Some(dir) = path.parent() {
            std::fs::create_dir_all(dir)?;
        }
        let mut conn = Connection::open(path)?;
        conn.pragma_update(None, "journal_mode", "WAL")?;
        conn.pragma_update(None, "synchronous", "FULL")?;
        apply_pragmas(&conn)?;
        migrate(&mut conn, path, app_version)?;
        conn.execute(
            "INSERT OR IGNORE INTO server_counters(name, value) VALUES ('user_data_generation', 0)",
            [],
        )?;

        let (tx, rx) = mpsc::channel::<WriteJob>();
        let thread = std::thread::Builder::new()
            .name("sqlite-writer".into())
            .spawn(move || {
                let mut conn = conn;
                while let Ok(job) = rx.recv() {
                    job(&mut conn);
                }
            })
            .map_err(|e| Error::internal(format!("spawn writer: {e}")))?;

        let mut readers = Vec::new();
        for _ in 0..4 {
            let reader = Connection::open_with_flags(
                path,
                OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
            )?;
            apply_pragmas(&reader)?;
            readers.push(Arc::new(Mutex::new(reader)));
        }
        Ok(Store {
            writer: Mutex::new(Some(tx)),
            thread: Mutex::new(Some(thread)),
            readers,
            next: AtomicUsize::new(0),
            path: path.to_path_buf(),
        })
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    fn send(&self, job: WriteJob) -> Result<()> {
        let guard = self.writer.lock().unwrap();
        let tx = guard
            .as_ref()
            .ok_or_else(|| Error::internal("store is closed"))?;
        tx.send(job)
            .map_err(|_| Error::internal("writer thread stopped"))
    }

    /// Runs `f` on the writer thread (serialized with every other write).
    pub async fn write<T, F>(&self, f: F) -> Result<T>
    where
        F: FnOnce(&mut Connection) -> Result<T> + Send + 'static,
        T: Send + 'static,
    {
        let (otx, orx) = tokio::sync::oneshot::channel();
        self.send(Box::new(move |conn| {
            let result = catch_unwind(AssertUnwindSafe(|| f(conn)))
                .unwrap_or_else(|_| Err(Error::internal("write closure panicked")));
            let _ = otx.send(result);
        }))?;
        orx.await
            .map_err(|_| Error::internal("writer dropped the request"))?
    }

    /// Blocking variant for non-async threads (the archiver).
    pub fn write_blocking<T, F>(&self, f: F) -> Result<T>
    where
        F: FnOnce(&mut Connection) -> Result<T> + Send + 'static,
        T: Send + 'static,
    {
        let (otx, orx) = mpsc::sync_channel(1);
        self.send(Box::new(move |conn| {
            let result = catch_unwind(AssertUnwindSafe(|| f(conn)))
                .unwrap_or_else(|_| Err(Error::internal("write closure panicked")));
            let _ = otx.send(result);
        }))?;
        orx.recv()
            .map_err(|_| Error::internal("writer dropped the request"))?
    }

    fn reader(&self) -> Arc<Mutex<Connection>> {
        let i = self.next.fetch_add(1, Ordering::Relaxed) % self.readers.len();
        // Prefer an idle connection, else wait on the round-robin pick.
        for k in 0..self.readers.len() {
            let candidate = &self.readers[(i + k) % self.readers.len()];
            if candidate.try_lock().is_ok() {
                return candidate.clone();
            }
        }
        self.readers[i].clone()
    }

    pub fn read_blocking<T, F>(&self, f: F) -> Result<T>
    where
        F: FnOnce(&Connection) -> Result<T>,
    {
        let reader = self.reader();
        let guard = reader.lock().unwrap();
        f(&guard)
    }

    pub async fn read<T, F>(self: &Arc<Self>, f: F) -> Result<T>
    where
        F: FnOnce(&Connection) -> Result<T> + Send + 'static,
        T: Send + 'static,
    {
        let store = self.clone();
        tokio::task::spawn_blocking(move || store.read_blocking(f))
            .await
            .map_err(|e| Error::internal(format!("read task: {e}")))?
    }
}

impl Drop for Store {
    fn drop(&mut self) {
        self.writer.lock().unwrap().take();
        if let Some(thread) = self.thread.lock().unwrap().take() {
            let _ = thread.join();
        }
    }
}

/// Bumps `user_data_generation` inside the caller's user-authored write transaction.
pub fn bump_generation(tx: &Transaction<'_>) -> rusqlite::Result<()> {
    tx.execute(
        "UPDATE server_counters SET value = value + 1 WHERE name = 'user_data_generation'",
        [],
    )?;
    Ok(())
}

pub fn user_data_generation(conn: &Connection) -> rusqlite::Result<i64> {
    conn.query_row(
        "SELECT value FROM server_counters WHERE name = 'user_data_generation'",
        [],
        |r| r.get(0),
    )
}

/// The iRating a recording carries as a rating, else `None`. iRacing writes 1 into sessions
/// without a subsession (Offline Testing, `SubSessionID` 0), which is a placeholder, not a rating.
pub fn recorded_irating(irating: Option<i64>, subsession_id: Option<i64>) -> Option<i64> {
    irating.filter(|_| subsession_id.is_some_and(|s| s > 0))
}

/// Whether a parsed recording of `customer` with a real subsession starts after `start_time_utc`,
/// so that its rating (not this recording's) is the driver's latest.
pub fn newer_rated_recording(
    conn: &Connection,
    customer_id: i64,
    sha256: &str,
    start_time_utc: Option<&str>,
) -> rusqlite::Result<bool> {
    let Some(start) = start_time_utc else {
        return Ok(false);
    };
    conn.query_row(
        "SELECT EXISTS(SELECT 1 FROM recordings WHERE customer_id = ?1 AND sha256 != ?2
           AND ingest_state IN ('parsed', 'discovered') AND subsession_id > 0 AND start_time_utc > ?3)",
        rusqlite::params![customer_id, sha256, start],
        |r| r.get(0),
    )
}

/// Upserts a driver row; the display name only changes when `name` is given.
pub fn upsert_driver(
    conn: &Connection,
    customer_id: i64,
    name: Option<&str>,
    irating: Option<i64>,
) -> rusqlite::Result<()> {
    let now = crate::util::iso_now();
    let placeholder = format!("Driver {customer_id}");
    conn.execute(
        "INSERT INTO drivers(customer_id, display_name, latest_irating, first_seen_at, last_seen_at)
         VALUES (?1, COALESCE(?2, ?3), ?4, ?5, ?5)
         ON CONFLICT(customer_id) DO UPDATE SET
           display_name = COALESCE(?2, display_name),
           latest_irating = COALESCE(?4, latest_irating),
           last_seen_at = ?5",
        rusqlite::params![customer_id, name, placeholder, irating, now],
    )?;
    Ok(())
}

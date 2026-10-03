//! `coach-collector backfill` (D12 step 3): queue every recording found below the given folders,
//! the legacy raw archive and the telemetry folder through the normal upload path, once.
//!
//! It is the uploader of `run` (finalization gate, SHA-256, create, 4 MiB chunks, resume, the same
//! on-track gate, files opened read-only with sharing) pointed at other folders, so it is
//! idempotent the same way: the server answers `verified` for bytes it holds and the file is
//! dropped, and what a restart already knows is not hashed again. It keeps its own journal
//! (`backfill-journal.jsonl`) so it never shares `journal.jsonl` with a running collector, and
//! reads that one (read only) to skip what the collector has already uploaded. Its own evidence
//! of the car being off track is a shared-memory reader in this process, like the collector's.
//! `--dry-run` lists what would be queued and touches nothing but directory metadata.

use std::fs::{File, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::sync::atomic::Ordering;
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use crate::cli::BackfillOptions;
use crate::config::{Config, Paths, normalize_server};
use crate::hub::Hub;
use crate::journal::Journal;
use crate::scan::{Candidate, Interrupted, archived_name, scan_recordings_below};
use crate::sdk::{MapSource, SdkReader};
use crate::uploader::{Step, Uploader, UploaderSettings};
use crate::util::{SystemClock, iso_utc};

/// What the dry-run listing says about one recording.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Plan {
    /// Will be hashed and offered to the server (which skips it when it holds the bytes).
    Queue,
    /// This collector already uploaded it: the server holds it.
    Held,
    /// Given up on by an earlier run, unchanged since.
    Refused(String),
}

#[derive(Debug, Clone, PartialEq)]
pub struct PlanLine {
    pub cand: Candidate,
    /// The name the recording will be uploaded under when it is not its file name.
    pub name: Option<String>,
    pub plan: Plan,
}

/// What a backfill did (or, for a dry run, would do).
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Summary {
    pub listed: usize,
    pub bytes: u64,
    pub uploaded: u64,
    pub held: u64,
    /// `(path, reason)` for every file given up on.
    pub rejected: Vec<(String, String)>,
}

/// The folders searched: the telemetry folder and the raw archive (unless `--no-archive`), then
/// the folders named on the command line.
pub fn roots(paths: &Paths, options: &BackfillOptions) -> Vec<PathBuf> {
    let mut roots = Vec::new();
    if !options.no_archive {
        roots.push(paths.telemetry_dir());
        roots.push(
            options
                .archive
                .clone()
                .unwrap_or_else(|| paths.legacy_archive_dir()),
        );
    }
    roots.extend(options.folders.iter().cloned());
    roots
}

/// The listing a dry run prints: every recording below `roots` with what would happen to it.
/// `own` is the backfill's journal and `known` the collector's, both read only. `proceed` is
/// asked before every directory and entry, as in every scan.
pub fn plan(
    roots: &[PathBuf],
    own: &Journal,
    known: &Journal,
    now_unix: u64,
    proceed: &dyn Fn() -> bool,
) -> Result<Vec<PlanLine>, Interrupted> {
    let mut lines = Vec::new();
    for cand in scan_recordings_below(roots, proceed)? {
        if !proceed() {
            return Err(Interrupted);
        }
        let key = cand.key();
        let plan = if let Some(r) = own.rejection(&key, cand.size, cand.mtime_ms) {
            Plan::Refused(r.reason.clone())
        } else if own
            .seen_hash(&key, cand.size, cand.mtime_ms, now_unix)
            .is_some()
            || known
                .seen_hash(&key, cand.size, cand.mtime_ms, now_unix)
                .is_some()
        {
            Plan::Held
        } else {
            Plan::Queue
        };
        let name = archived_name(&cand.path).filter(|n| *n != cand.file_name());
        lines.push(PlanLine { cand, name, plan });
    }
    Ok(lines)
}

/// Writes the listing and its totals.
pub fn print_plan(out: &mut dyn Write, lines: &[PlanLine]) -> std::io::Result<Summary> {
    let mut summary = Summary::default();
    let (mut queue, mut queue_bytes) = (0usize, 0u64);
    for line in lines {
        let (label, note) = match &line.plan {
            Plan::Queue => ("queue  ", String::new()),
            Plan::Held => ("held   ", String::new()),
            Plan::Refused(why) => ("refused", format!("  ({why})")),
        };
        let name = line
            .name
            .as_ref()
            .map(|n| format!("  as {n}"))
            .unwrap_or_default();
        writeln!(
            out,
            "{label} {:>14} {}  {}{name}{note}",
            line.cand.size,
            iso_utc(line.cand.mtime_ms),
            line.cand.path.display()
        )?;
        summary.listed += 1;
        summary.bytes += line.cand.size;
        match line.plan {
            Plan::Queue => {
                queue += 1;
                queue_bytes += line.cand.size;
            }
            Plan::Held => summary.held += 1,
            Plan::Refused(_) => {}
        }
    }
    let refused = lines
        .iter()
        .filter(|l| matches!(l.plan, Plan::Refused(_)))
        .count();
    writeln!(
        out,
        "{} recordings, {} bytes: {queue} to queue ({queue_bytes} bytes), {} already held, {refused} refused",
        summary.listed, summary.bytes, summary.held
    )?;
    Ok(summary)
}

/// Holds `backfill.lock` open exclusively (on Windows) for the life of the run, so two backfills
/// never append to the same journal.
fn lock(path: &Path) -> Result<File, String> {
    let mut options = OpenOptions::new();
    options.create(true).write(true).truncate(false);
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        options.share_mode(0);
    }
    options
        .open(path)
        .map_err(|e| format!("another backfill is running ({}: {e})", path.display()))
}

/// The same off-track evidence the collector gives its uploader, from this process's own
/// read-only look at the shared memory.
fn spawn_evidence(hub: Arc<Hub>, source: Box<dyn MapSource>) -> JoinHandle<()> {
    thread::spawn(move || {
        let mut reader = SdkReader::new(source);
        while !hub.is_shutdown() {
            reader.wait(Duration::from_millis(100));
            let _ = reader.poll(hub.clock.mono_ms(), hub.clock.unix_ms());
            hub.gate.observe(reader.evidence());
        }
    })
}

/// The whole command. `source` is the shared-memory map (iRacing's on the sim PC, a stand-in in
/// tests); `out` receives the listing and the progress lines.
pub fn run_with(
    options: &BackfillOptions,
    source: Box<dyn MapSource>,
    out: &mut dyn Write,
) -> Result<Summary, String> {
    let mut paths = Paths::default_paths();
    if let Some(dir) = &options.state_dir {
        paths.state_dir = dir.clone();
    }
    if let Some(dir) = &options.iracing_root {
        paths.iracing_root = dir.clone();
    }
    let io = |e: std::io::Error| e.to_string();
    let roots = roots(&paths, options);
    for root in &roots {
        if !root.is_dir() {
            writeln!(out, "not a folder, skipped: {}", root.display()).map_err(io)?;
        }
    }
    // A console command: the log goes to stderr, and nothing is created for a dry run.
    crate::logging::init(crate::logging::level_from_env(), None);
    let known = Journal::read_only(&paths.journal_path());
    let clock = Arc::new(SystemClock::new());

    let (journal, _lock) = if options.dry_run {
        (Journal::read_only(&paths.backfill_journal_path()), None)
    } else {
        std::fs::create_dir_all(&paths.state_dir)
            .map_err(|e| format!("cannot create {}: {e}", paths.state_dir.display()))?;
        let lock = lock(&paths.backfill_lock_path())?;
        (
            Journal::open(&paths.backfill_journal_path())
                .map_err(|e| format!("cannot open the backfill journal: {e}"))?,
            Some(lock),
        )
    };
    let hub = Hub::new(clock, journal, false);
    let evidence = spawn_evidence(hub.clone(), source);
    let result = if options.dry_run {
        dry_run(&hub, &roots, &known, out)
    } else {
        let mut config = Config::load(&paths.state_dir);
        if let Some(server) = &options.server {
            config.server = normalize_server(server).map_err(|e| e.to_string())?;
        }
        upload(&hub, &config, &paths, roots, known, out)
    };
    hub.request_shutdown();
    let _ = evidence.join();
    result
}

fn wait_for_off_track(hub: &Hub, out: &mut dyn Write) {
    let mut told = false;
    while !hub.gate.allows_file_work() {
        if !told {
            let _ = writeln!(out, "waiting for iRacing to be off track (or closed)");
            told = true;
        }
        thread::sleep(Duration::from_millis(100));
    }
}

fn dry_run(
    hub: &Hub,
    roots: &[PathBuf],
    known: &Journal,
    out: &mut dyn Write,
) -> Result<Summary, String> {
    let proceed = || hub.gate.allows_file_work();
    loop {
        wait_for_off_track(hub, out);
        let now = hub.clock.unix_ms();
        let own = hub.journal();
        // An interrupted scan (the car went on track part way) lists nothing: wait and scan again.
        if let Ok(lines) = plan(roots, &own, known, now, &proceed) {
            return print_plan(out, &lines).map_err(|e| e.to_string());
        }
    }
}

fn upload(
    hub: &Arc<Hub>,
    config: &Config,
    paths: &Paths,
    roots: Vec<PathBuf>,
    known: Journal,
    out: &mut dyn Write,
) -> Result<Summary, String> {
    let io = |e: std::io::Error| e.to_string();
    let mut uploader = Uploader::new(
        hub.clone(),
        &config.server,
        paths.clone(),
        UploaderSettings::new(&config.collector_id),
    )
    .for_backfill(roots, known);
    let stats = &hub.uploads;
    let (mut announced, mut reported) = (false, Instant::now());
    loop {
        let step = uploader.step();
        if !announced && stats.scans_done.load(Ordering::Relaxed) > 0 {
            announced = true;
            writeln!(
                out,
                "queued {} recordings, {} bytes (the server skips what it holds)",
                stats.uploads_pending.load(Ordering::Relaxed),
                stats.bytes_pending.load(Ordering::Relaxed)
            )
            .map_err(io)?;
        }
        if uploader.backfill_done() {
            break;
        }
        if let Step::Idle(wait) = step {
            thread::sleep(wait.min(Duration::from_millis(500)));
        }
        if announced && reported.elapsed() >= Duration::from_secs(15) {
            reported = Instant::now();
            let waiting = stats.waiting_for_off_track.load(Ordering::Relaxed);
            writeln!(
                out,
                "{} to go ({} bytes), {} uploaded, {} already held, {} refused{}",
                uploader.pending(),
                stats.bytes_pending.load(Ordering::Relaxed),
                stats.files_uploaded.load(Ordering::Relaxed),
                stats.files_held.load(Ordering::Relaxed),
                stats.rejected.load(Ordering::Relaxed),
                if waiting {
                    "; waiting for iRacing to be off track"
                } else {
                    ""
                }
            )
            .map_err(io)?;
        }
    }
    let rejected: Vec<(String, String)> = hub
        .journal()
        .rejected_recordings()
        .into_iter()
        .map(|r| (r.path, r.reason))
        .collect();
    let summary = Summary {
        listed: 0,
        bytes: 0,
        uploaded: stats.files_uploaded.load(Ordering::Relaxed),
        held: stats.files_held.load(Ordering::Relaxed),
        rejected,
    };
    for (path, why) in &summary.rejected {
        writeln!(out, "refused: {path}  ({why})").map_err(io)?;
    }
    writeln!(
        out,
        "done: {} uploaded, {} already held, {} refused",
        summary.uploaded,
        summary.held,
        summary.rejected.len()
    )
    .map_err(io)?;
    Ok(summary)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::journal::RejectedEntry;
    use crate::util::uuid_v4;
    use std::fs;

    #[test]
    fn roots_are_telemetry_archive_then_the_given_folders() {
        let paths = Paths {
            state_dir: PathBuf::from("s"),
            iracing_root: PathBuf::from("docs/iRacing"),
        };
        let mut o = BackfillOptions {
            folders: vec![PathBuf::from("extra")],
            ..BackfillOptions::default()
        };
        assert_eq!(
            roots(&paths, &o),
            [
                PathBuf::from("docs/iRacing/telemetry"),
                PathBuf::from("docs/iRacing Coach/data/telemetry-traces/raw"),
                PathBuf::from("extra"),
            ]
        );
        o.archive = Some(PathBuf::from("E:/raw"));
        assert_eq!(roots(&paths, &o)[1], PathBuf::from("E:/raw"));
        o.no_archive = true;
        o.archive = None;
        assert_eq!(roots(&paths, &o), [PathBuf::from("extra")]);
    }

    #[test]
    fn the_plan_lists_every_recording_below_a_folder_with_what_would_happen() {
        let root = std::env::temp_dir().join(format!("coach-backfill-{}", uuid_v4()));
        let archive = root.join("raw");
        fs::create_dir_all(archive.join("abc")).unwrap();
        fs::create_dir_all(archive.join("def")).unwrap();
        fs::write(archive.join("abc/recording.ibt"), b"1234").unwrap();
        fs::write(
            archive.join("abc/manifest.json"),
            r#"{"original_path": "C:\\Users\\x\\Documents\\iRacing\\telemetry\\car track 2026-09-20 21-08-55.ibt"}"#,
        )
        .unwrap();
        fs::write(archive.join("def/two.IBT"), b"12").unwrap();
        fs::write(archive.join("def/readme.txt"), b"x").unwrap();
        let mut own = Journal::in_memory();
        let held_path = archive.join("def").join("two.IBT");
        let meta = fs::metadata(&held_path).unwrap();
        let mtime = meta
            .modified()
            .unwrap()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_millis() as u64;
        own.reject_recording(RejectedEntry {
            path: held_path.to_string_lossy().into_owned(),
            size: 2,
            mtime_ms: mtime,
            kind: "ibt".into(),
            reason: "the header says it holds 0 records".into(),
            at_ms: 1,
        })
        .unwrap();
        let lines = plan(
            std::slice::from_ref(&archive),
            &own,
            &Journal::in_memory(),
            0,
            &|| true,
        )
        .unwrap();
        assert_eq!(lines.len(), 2, "only recordings, at any depth");
        let abc = lines.iter().find(|l| l.cand.size == 4).unwrap();
        assert_eq!(abc.plan, Plan::Queue);
        assert_eq!(
            abc.name.as_deref(),
            Some("car track 2026-09-20 21-08-55.ibt")
        );
        let two = lines.iter().find(|l| l.cand.size == 2).unwrap();
        assert!(matches!(&two.plan, Plan::Refused(why) if why.contains("0 records")));
        let mut text = Vec::new();
        let summary = print_plan(&mut text, &lines).unwrap();
        assert_eq!((summary.listed, summary.bytes), (2, 6));
        let text = String::from_utf8(text).unwrap();
        assert!(
            text.contains("as car track 2026-09-20 21-08-55.ibt"),
            "{text}"
        );
        assert!(
            text.ends_with(
                "2 recordings, 6 bytes: 1 to queue (4 bytes), 0 already held, 1 refused\n"
            ),
            "{text}"
        );
        // A plan is a listing: the gate decides whether it is made at all.
        assert_eq!(
            plan(
                std::slice::from_ref(&archive),
                &own,
                &Journal::in_memory(),
                0,
                &|| false
            ),
            Err(Interrupted)
        );
        fs::remove_dir_all(&root).unwrap();
    }
}

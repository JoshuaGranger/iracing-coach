//! Wires the collector together: threads, shared state, and the shutdown order. `run` is the whole
//! program for `coach-collector run`, including the `--replay` and `--dry-run` modes the tests and
//! the rig gates use.

use std::fs;
use std::sync::Arc;
use std::sync::mpsc;
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use crate::cli::RunOptions;
use crate::config::{CachedLayout, Config, Paths, normalize_server};
use crate::dry::DryLog;
use crate::fixture::{Pacing, ReplaySource};
use crate::garage::GarageAction;
use crate::garage_io::GarageIo;
use crate::hub::{Hub, LayoutState};
use crate::journal::Journal;
use crate::link::{Link, LinkSettings};
use crate::logging::{MAX_LOG_BYTES, RotatingLog};
use crate::pipeline::{Pipeline, PipelineStats, run_reader_loop};
use crate::sdk::{MapSource, SdkReader};
use crate::uploader::{Uploader, UploaderSettings};
use crate::util::{Clock, ManualClock, SystemClock, unix_ms_now};

/// What a finished run reports (tests and `--exit-at-end`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct RunReport {
    pub pipeline: PipelineStats,
    /// The tray changed the server address: start a fresh process.
    pub restart: bool,
}

pub fn resolve_paths(opts: &RunOptions) -> Paths {
    let mut paths = Paths::default_paths();
    if let Some(dir) = &opts.state_dir {
        paths.state_dir = dir.clone();
    }
    if let Some(dir) = &opts.iracing_root {
        paths.iracing_root = dir.clone();
    }
    paths
}

/// Installs the file logger (and stderr when `console`); a second call in one process is a no-op.
pub fn init_logging(paths: &Paths, console: bool) {
    let level = crate::logging::level_from_env();
    match RotatingLog::open(&paths.logs_dir(), MAX_LOG_BYTES) {
        Ok(log) if !console => crate::logging::init(level, Some(&log)),
        _ => crate::logging::init(level, None),
    }
    // A panic on any thread goes to the log (the window and tray would otherwise outlive it).
    let previous = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
        crate::error!("panic: {info}");
        previous(info);
    }));
}

fn spawn<T: Send + 'static>(name: &str, f: impl FnOnce() -> T + Send + 'static) -> JoinHandle<T> {
    thread::Builder::new()
        .name(name.to_string())
        .spawn(f)
        .unwrap_or_else(|e| panic!("cannot start the {name} thread: {e}"))
}

#[cfg(windows)]
fn background_mode() {
    crate::win::enter_background_mode();
}

#[cfg(not(windows))]
fn background_mode() {}

#[cfg(windows)]
fn real_source() -> Result<Box<dyn MapSource>, String> {
    Ok(Box::new(crate::win::map::Win32MapSource))
}

#[cfg(not(windows))]
fn real_source() -> Result<Box<dyn MapSource>, String> {
    Err("reading iRacing needs Windows; use --replay with a fixture".into())
}

pub fn run(opts: RunOptions) -> Result<RunReport, String> {
    let paths = resolve_paths(&opts);
    fs::create_dir_all(&paths.state_dir)
        .map_err(|e| format!("cannot create {}: {e}", paths.state_dir.display()))?;
    init_logging(&paths, opts.no_ui);

    #[cfg(windows)]
    let _instance = if opts.replay.is_none() {
        Some(crate::win::InstanceGuard::acquire().ok_or("another collector is already running")?)
    } else {
        None
    };

    let mut config = Config::load(&paths.state_dir);
    if !paths.state_dir.join(crate::config::CONFIG_FILE).exists() {
        config
            .save(&paths.state_dir)
            .map_err(|e| format!("cannot write the config: {e}"))?;
    }
    if let Some(server) = &opts.server {
        config.server = normalize_server(server).map_err(|e| e.to_string())?;
    }

    // The clock and the map: a replay at speed 0 runs on a virtual clock the replay advances.
    let (clock, source): (Arc<dyn Clock>, Box<dyn MapSource>) = match &opts.replay {
        Some(path) => {
            let (clock, pacing): (Arc<dyn Clock>, Pacing) = if opts.speed == 0.0 {
                let manual = Arc::new(ManualClock::new(unix_ms_now()));
                (manual.clone(), Pacing::Virtual(manual))
            } else {
                (
                    Arc::new(SystemClock::new()),
                    Pacing::Realtime { speed: opts.speed },
                )
            };
            let source = ReplaySource::from_file(path, pacing)
                .map_err(|e| format!("cannot read {}: {e}", path.display()))?;
            (clock, Box::new(source))
        }
        None => (Arc::new(SystemClock::new()), real_source()?),
    };

    let journal = if opts.dry_run {
        Journal::in_memory()
    } else {
        // When the file cannot be opened the journal keeps entries in memory, opens the file again
        // from the garage thread, and sends no garage event until then.
        Journal::open_or_unavailable(&paths.journal_path())
    };
    let hub = Hub::new(clock, journal, config.safe_glance);
    if let Some(cached) = &config.layout {
        hub.set_layout(LayoutState {
            layout_id: cached.layout_id.clone(),
            version: cached.version,
            layout: cached.layout.clone(),
        });
    }
    let dry = if opts.dry_run {
        Some(Arc::new(DryLog::create(&paths.dry_run_log()).map_err(
            |e| format!("cannot create the dry-run log: {e}"),
        )?))
    } else {
        None
    };
    crate::info!(
        "collector {} starting: server {}, replay {}, dry run {}",
        env!("CARGO_PKG_VERSION"),
        config.server,
        opts.replay.is_some(),
        opts.dry_run
    );

    // Garage events: reader thread -> this thread -> journal and hash.
    let (garage_tx, garage_rx) = mpsc::channel::<GarageAction>();
    let garage_io = GarageIo::new(hub.clone(), paths.setups_dir(), dry.clone());
    let mut garage_thread = Some(spawn("garage-io", move || {
        background_mode();
        garage_io.run(garage_rx);
    }));

    // The reader and the engine.
    let reader_thread = {
        let hub = hub.clone();
        let dry = dry.clone();
        let exit_at_end = opts.exit_at_end;
        spawn("reader", move || {
            let mut reader = SdkReader::new(source);
            let mut pipeline = Pipeline::new(hub.clone(), Some(garage_tx), dry);
            run_reader_loop(&mut reader, &mut pipeline, &hub, exit_at_end);
            pipeline.stats()
        })
    };

    let mut helpers: Vec<JoinHandle<()>> = Vec::new();
    if !opts.dry_run {
        let link_settings = LinkSettings::new(&config.server, &config.collector_id);
        let mut link = Link::new(hub.clone(), link_settings);
        helpers.push(spawn("link", move || link.run()));

        let mut uploader = Uploader::new(
            hub.clone(),
            &config.server,
            paths.clone(),
            UploaderSettings::new(&config.collector_id),
        )
        .with_clock(Arc::new(SystemClock::new()));
        helpers.push(spawn("uploader", move || {
            background_mode();
            uploader.run();
        }));

        #[cfg(windows)]
        helpers.extend(crate::win::watcher::spawn(
            hub.clone(),
            vec![paths.telemetry_dir(), paths.setups_dir()],
        ));
    }

    #[cfg_attr(not(windows), allow(unused_mut))]
    let mut restart = false;
    #[cfg(windows)]
    let use_ui = !opts.no_ui;
    #[cfg(not(windows))]
    let use_ui = false;

    let join_reader = |t: JoinHandle<PipelineStats>| {
        t.join()
            .map_err(|_| "the reader thread panicked".to_string())
    };
    let stats = if use_ui {
        #[cfg(windows)]
        {
            let exit = crate::win::ui::run(crate::win::ui::UiParams {
                hub: hub.clone(),
                config: config.clone(),
                state_dir: paths.state_dir.clone(),
                quit: crate::win::QuitEvent::create(),
            });
            restart = exit == crate::win::ui::UiExit::Restart;
        }
        hub.request_shutdown();
        join_reader(reader_thread)?
    } else if opts.exit_at_end {
        // The replay ends like a sim that quit: wait for that, let the journal thread finish
        // (its channel closed), then let the link and uploader finish what the replay queued.
        while !hub.replay_done.load(std::sync::atomic::Ordering::SeqCst)
            && !reader_thread.is_finished()
        {
            thread::sleep(Duration::from_millis(10));
        }
        if let Some(t) = garage_thread.take() {
            let _ = t.join();
        }
        drain(&hub, &opts);
        hub.request_shutdown();
        join_reader(reader_thread)?
    } else {
        #[cfg(windows)]
        let quit = crate::win::QuitEvent::create();
        while !hub.is_shutdown() {
            #[cfg(windows)]
            if quit.as_ref().is_some_and(crate::win::QuitEvent::is_set) {
                break;
            }
            thread::sleep(Duration::from_millis(200));
        }
        hub.request_shutdown();
        join_reader(reader_thread)?
    };

    if let Some(t) = garage_thread.take() {
        let _ = t.join();
    }
    for h in helpers {
        let _ = h.join();
    }
    // Keep the cached layout in the config only when the server sent one this run.
    if let Some(l) = hub.layout()
        && config.layout.as_ref().map(|c| (&c.layout_id, c.version))
            != Some((&l.layout_id, l.version))
        && !opts.dry_run
    {
        let mut cfg = Config::load(&paths.state_dir);
        cfg.layout = Some(CachedLayout {
            layout_id: l.layout_id,
            version: l.version,
            layout: l.layout,
        });
        let _ = cfg.save(&paths.state_dir);
    }
    crate::info!("collector stopped");
    Ok(RunReport {
        pipeline: stats,
        restart,
    })
}

/// Waits until the journal's garage entries are acknowledged, the upload queue is empty and the
/// frame queue is flushed, for at most `drain_secs`.
fn drain(hub: &Hub, opts: &RunOptions) {
    if opts.dry_run {
        return;
    }
    let deadline = Instant::now() + Duration::from_secs(opts.drain_secs);
    while Instant::now() < deadline {
        let garage = hub.journal().garage_pending();
        let uploads = hub
            .uploads
            .uploads_pending
            .load(std::sync::atomic::Ordering::Relaxed);
        let connected = hub.link_info().connected;
        let scanned = hub
            .uploads
            .scans_done
            .load(std::sync::atomic::Ordering::Relaxed)
            > 0;
        if connected
            && scanned
            && garage == 0
            && uploads == 0
            && hub.frames.is_empty()
            && hub.status_flushed()
        {
            return;
        }
        thread::sleep(Duration::from_millis(100));
    }
    crate::warn!("drain timed out; exiting with work still queued");
}

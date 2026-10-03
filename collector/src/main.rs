//! coach-collector: SDK reader, live overlay, garage capture and uploader for the sim PC.
//! The collector is Windows-only; other targets build the library (for tests) and a stub binary.

use std::process::ExitCode;

use coach_collector::cli::{self, Command, USAGE};

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let command = match cli::parse(&args) {
        Ok(c) => c,
        Err(message) => {
            eprintln!("coach-collector: {message}\n\n{USAGE}");
            return ExitCode::from(2);
        }
    };
    match command {
        Command::Help => {
            println!("{USAGE}");
            ExitCode::SUCCESS
        }
        Command::Version => {
            println!("coach-collector {}", env!("CARGO_PKG_VERSION"));
            ExitCode::SUCCESS
        }
        other => dispatch(other),
    }
}

fn report(result: Result<(), String>) -> ExitCode {
    match result {
        Ok(()) => ExitCode::SUCCESS,
        Err(message) => {
            eprintln!("coach-collector: {message}");
            ExitCode::FAILURE
        }
    }
}

fn dispatch(command: Command) -> ExitCode {
    use coach_collector::app;
    use coach_collector::config::Paths;
    match command {
        Command::Run(options) => {
            let result = app::run(options);
            match result {
                Ok(run) if run.restart => report(restart_self()),
                Ok(_) => ExitCode::SUCCESS,
                Err(message) => report(Err(message)),
            }
        }
        Command::Backfill(options) => report(backfill(&options)),
        Command::RecordSdk { seconds, out } => report(record_sdk(seconds, &out)),
        Command::Install {
            server,
            state_dir,
            iracing_root,
        } => {
            let mut paths = Paths::default_paths();
            if let Some(dir) = state_dir {
                paths.state_dir = dir;
            }
            if let Some(dir) = iracing_root {
                paths.iracing_root = dir;
            }
            report(install(server.as_deref(), &paths))
        }
        Command::Uninstall { state_dir } => {
            let mut paths = Paths::default_paths();
            if let Some(dir) = state_dir {
                paths.state_dir = dir;
            }
            report(uninstall(&paths))
        }
        Command::Help | Command::Version => ExitCode::SUCCESS,
    }
}

fn backfill(options: &coach_collector::cli::BackfillOptions) -> Result<(), String> {
    let source = coach_collector::app::real_source()?;
    coach_collector::backfill::run_with(options, source, &mut std::io::stdout()).map(|_| ())
}

#[cfg(windows)]
fn install(server: Option<&str>, paths: &coach_collector::config::Paths) -> Result<(), String> {
    coach_collector::win::install::install(server, paths)
}

#[cfg(windows)]
fn uninstall(paths: &coach_collector::config::Paths) -> Result<(), String> {
    coach_collector::win::install::uninstall(paths)
}

#[cfg(windows)]
fn record_sdk(seconds: f64, out: &std::path::Path) -> Result<(), String> {
    let mut source = coach_collector::win::map::Win32MapSource;
    let summary =
        coach_collector::record::record(&mut source, seconds, out).map_err(|e| e.to_string())?;
    println!(
        "recorded {} ticks, {} static regions, {} status changes to {}",
        summary.ticks,
        summary.static_regions,
        summary.status_changes,
        out.display()
    );
    Ok(())
}

#[cfg(windows)]
fn restart_self() -> Result<(), String> {
    use coach_collector::win::{quote_arg, spawn_detached};
    let exe = std::env::current_exe().map_err(|e| e.to_string())?;
    spawn_detached(&format!("{} run", quote_arg(&exe.to_string_lossy())))
        .map_err(|e| format!("cannot restart: {e}"))
}

#[cfg(not(windows))]
fn install(_: Option<&str>, _: &coach_collector::config::Paths) -> Result<(), String> {
    Err("install runs on Windows only".into())
}

#[cfg(not(windows))]
fn uninstall(_: &coach_collector::config::Paths) -> Result<(), String> {
    Err("uninstall runs on Windows only".into())
}

#[cfg(not(windows))]
fn record_sdk(_: f64, _: &std::path::Path) -> Result<(), String> {
    Err("record-sdk runs on Windows only (it reads iRacing's shared memory)".into())
}

#[cfg(not(windows))]
fn restart_self() -> Result<(), String> {
    Ok(())
}

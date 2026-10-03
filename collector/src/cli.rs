//! Command line (`coach-collector --help`).

use std::path::PathBuf;

pub const USAGE: &str = "\
coach-collector: the iRacing Coach collector for the sim PC

USAGE
  coach-collector [run] [options]      read the sim, draw the monitor, upload recordings
  coach-collector install [--server URL] [--state-dir DIR] [--iracing-root DIR]
                                       write the config, set the sign-in Run value, start it
  coach-collector uninstall            stop it and remove the Run value and its own files
  coach-collector record-sdk <seconds> <out.sdkfix>
                                       record the shared-memory map into a replay fixture
  coach-collector --version

RUN OPTIONS
  --server URL         server address for this run (default: collector.json)
  --replay FILE        read a recorded SDK fixture instead of iRacing's shared memory
  --speed N            replay speed (1 = real time, 0 = as fast as possible; default 1)
  --dry-run            no server and no uploads; write captures and a frame summary to
                       dry-run.jsonl in the state folder
  --no-ui              no overlay and no tray (headless)
  --exit-at-end        with --replay: exit once the replay ended and the work it queued drained
  --drain-secs N       longest wait for that drain (default 30)
  --iracing-root DIR   iRacing's Documents folder (default: Documents\\iRacing)
  --state-dir DIR      collector state folder (default: %LOCALAPPDATA%\\iRacingCoach)
";

#[derive(Debug, Clone, PartialEq)]
pub struct RunOptions {
    pub server: Option<String>,
    pub replay: Option<PathBuf>,
    pub speed: f64,
    pub dry_run: bool,
    pub no_ui: bool,
    pub exit_at_end: bool,
    pub drain_secs: u64,
    pub iracing_root: Option<PathBuf>,
    pub state_dir: Option<PathBuf>,
}

impl Default for RunOptions {
    fn default() -> Self {
        RunOptions {
            server: None,
            replay: None,
            speed: 1.0,
            dry_run: false,
            no_ui: false,
            exit_at_end: false,
            drain_secs: 30,
            iracing_root: None,
            state_dir: None,
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub enum Command {
    Run(RunOptions),
    Install {
        server: Option<String>,
        state_dir: Option<PathBuf>,
        iracing_root: Option<PathBuf>,
    },
    Uninstall {
        state_dir: Option<PathBuf>,
    },
    RecordSdk {
        seconds: f64,
        out: PathBuf,
    },
    Version,
    Help,
}

pub fn parse(args: &[String]) -> Result<Command, String> {
    let mut it = args.iter().peekable();
    let sub = match it.peek().map(|s| s.as_str()) {
        Some("run" | "install" | "uninstall" | "record-sdk") => it.next().cloned(),
        _ => None,
    };
    match sub.as_deref() {
        Some("install") => {
            let (mut server, mut state_dir, mut iracing_root) = (None, None, None);
            while let Some(a) = it.next() {
                match a.as_str() {
                    "--server" => server = Some(value(&mut it, a)?),
                    "--state-dir" => state_dir = Some(PathBuf::from(value(&mut it, a)?)),
                    "--iracing-root" => iracing_root = Some(PathBuf::from(value(&mut it, a)?)),
                    other => return Err(format!("unknown option {other}")),
                }
            }
            Ok(Command::Install {
                server,
                state_dir,
                iracing_root,
            })
        }
        Some("uninstall") => {
            let mut state_dir = None;
            while let Some(a) = it.next() {
                match a.as_str() {
                    "--state-dir" => state_dir = Some(PathBuf::from(value(&mut it, a)?)),
                    other => return Err(format!("unknown option {other}")),
                }
            }
            Ok(Command::Uninstall { state_dir })
        }
        Some("record-sdk") => {
            let seconds: f64 = it
                .next()
                .ok_or("record-sdk needs <seconds> <out>")?
                .parse()
                .map_err(|_| "seconds must be a number".to_string())?;
            let out = PathBuf::from(it.next().ok_or("record-sdk needs <seconds> <out>")?);
            if !(seconds.is_finite() && seconds > 0.0) {
                return Err("seconds must be positive".into());
            }
            if let Some(extra) = it.next() {
                return Err(format!("unexpected {extra}"));
            }
            Ok(Command::RecordSdk { seconds, out })
        }
        _ => {
            let mut o = RunOptions::default();
            while let Some(a) = it.next() {
                match a.as_str() {
                    "--version" | "-V" => return Ok(Command::Version),
                    "--help" | "-h" | "/?" => return Ok(Command::Help),
                    "--server" => o.server = Some(value(&mut it, a)?),
                    "--replay" => o.replay = Some(PathBuf::from(value(&mut it, a)?)),
                    "--speed" => {
                        o.speed = value(&mut it, a)?
                            .parse()
                            .ok()
                            .filter(|s: &f64| s.is_finite() && *s >= 0.0)
                            .ok_or("--speed must be a number >= 0")?;
                    }
                    "--drain-secs" => {
                        o.drain_secs = value(&mut it, a)?
                            .parse()
                            .map_err(|_| "--drain-secs must be a whole number")?;
                    }
                    "--iracing-root" => o.iracing_root = Some(PathBuf::from(value(&mut it, a)?)),
                    "--state-dir" => o.state_dir = Some(PathBuf::from(value(&mut it, a)?)),
                    "--dry-run" => o.dry_run = true,
                    "--no-ui" => o.no_ui = true,
                    "--exit-at-end" => o.exit_at_end = true,
                    other => return Err(format!("unknown option {other}")),
                }
            }
            if o.exit_at_end && o.replay.is_none() {
                return Err("--exit-at-end needs --replay".into());
            }
            Ok(Command::Run(o))
        }
    }
}

fn value<'a>(it: &mut impl Iterator<Item = &'a String>, flag: &str) -> Result<String, String> {
    it.next()
        .cloned()
        .ok_or_else(|| format!("{flag} needs a value"))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn p(args: &[&str]) -> Result<Command, String> {
        parse(&args.iter().map(|s| s.to_string()).collect::<Vec<_>>())
    }

    #[test]
    fn bare_and_run_start_the_collector() {
        assert_eq!(p(&[]), Ok(Command::Run(RunOptions::default())));
        assert_eq!(p(&["run"]), Ok(Command::Run(RunOptions::default())));
    }

    #[test]
    fn replay_dry_run_and_paths() {
        let Ok(Command::Run(o)) = p(&[
            "--replay",
            "x.sdkfix",
            "--dry-run",
            "--speed",
            "0",
            "--iracing-root",
            "D:/iR",
            "--no-ui",
        ]) else {
            panic!("parse failed");
        };
        assert_eq!(o.replay, Some(PathBuf::from("x.sdkfix")));
        assert!(o.dry_run && o.no_ui);
        assert_eq!(o.speed, 0.0);
        assert_eq!(o.iracing_root, Some(PathBuf::from("D:/iR")));
    }

    #[test]
    fn subcommands() {
        assert_eq!(
            p(&["install", "--server", "10.0.0.2:8210"]),
            Ok(Command::Install {
                server: Some("10.0.0.2:8210".into()),
                state_dir: None,
                iracing_root: None
            })
        );
        assert_eq!(
            p(&["uninstall"]),
            Ok(Command::Uninstall { state_dir: None })
        );
        assert_eq!(
            p(&["record-sdk", "30", "out.sdkfix"]),
            Ok(Command::RecordSdk {
                seconds: 30.0,
                out: PathBuf::from("out.sdkfix")
            })
        );
        assert_eq!(p(&["--version"]), Ok(Command::Version));
    }

    #[test]
    fn rejects_bad_input() {
        assert!(p(&["--bogus"]).is_err());
        assert!(p(&["--server"]).is_err());
        assert!(p(&["--speed", "-1"]).is_err());
        assert!(p(&["--exit-at-end"]).is_err());
        assert!(p(&["record-sdk", "0", "o"]).is_err());
        assert!(p(&["record-sdk", "5"]).is_err());
    }
}

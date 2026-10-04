//! The collector's own CPU accounting (`run --cpu-report`): process CPU time and, per named
//! thread, the CPU time that thread has used, sampled over wall-clock windows. The replay harness
//! (`tests/perf_cpu.rs`) and the rig read these lines; nothing here runs unless the flag is set.
//!
//! Windows only. `process_pct` is `GetProcessTimes` (kernel plus user time: the figure the rig's
//! G4 recorder reads). That clock is sampled by the scheduler tick, so over a short window it can
//! mis-charge a thread that wakes in step with the tick; the per-thread figures and
//! `process_cycles_pct` therefore come from the CPU cycle counters (`QueryThreadCycleTime`,
//! `QueryProcessCycleTime`), turned into time with a cycles-per-millisecond figure calibrated by
//! a short spin when the first snapshot is taken. On other targets every figure is zero.

use std::io::Write;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

/// One thread that registered itself: its name and an OS handle that stays valid after the
/// thread exits, so its final CPU time can still be read.
struct Registered {
    name: String,
    handle: isize,
}

static THREADS: Mutex<Vec<Registered>> = Mutex::new(Vec::new());

fn threads() -> std::sync::MutexGuard<'static, Vec<Registered>> {
    THREADS.lock().unwrap_or_else(|e| e.into_inner())
}

/// Makes the calling thread visible to `snapshot` under `name`. Cheap; call once per thread.
pub fn register_thread(name: &str) {
    if let Some(handle) = os::open_current_thread() {
        threads().push(Registered {
            name: name.to_string(),
            handle,
        });
    }
}

/// CPU time used so far, in milliseconds.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Snapshot {
    /// Kernel plus user time of the process (`GetProcessTimes`).
    pub process_ms: f64,
    /// The process's CPU cycles as milliseconds at the calibrated clock.
    pub process_cycles_ms: f64,
    /// `(thread name, cpu ms from cycles)`, in registration order. Threads sharing a name are
    /// summed.
    pub threads: Vec<(String, f64)>,
}

pub fn snapshot() -> Snapshot {
    let per_ms = os::cycles_per_ms();
    let mut out = Snapshot {
        process_ms: os::process_cpu_ms().unwrap_or(0.0),
        process_cycles_ms: os::process_cycles().map_or(0.0, |c| c as f64 / per_ms),
        threads: Vec::new(),
    };
    for t in threads().iter() {
        let ms = os::thread_cycles(t.handle).map_or(0.0, |c| c as f64 / per_ms);
        match out.threads.iter_mut().find(|(n, _)| *n == t.name) {
            Some((_, total)) => *total += ms,
            None => out.threads.push((t.name.clone(), ms)),
        }
    }
    out
}

/// The line printed for the CPU used between two snapshots over `wall`:
/// `cpu-report label=<label> wall_s=10.00 process_pct=1.23 process_cycles_pct=1.20 main=0.80
/// reader=0.20 ...`, every figure a percentage of one core.
pub fn report_line(label: &str, from: &Snapshot, to: &Snapshot, wall: Duration) -> String {
    let wall_ms = (wall.as_secs_f64() * 1000.0).max(1.0);
    let pct = |ms: f64| 100.0 * ms / wall_ms;
    let mut line = format!(
        "cpu-report label={label} wall_s={:.2} process_pct={:.3} process_cycles_pct={:.3}",
        wall.as_secs_f64(),
        pct(to.process_ms - from.process_ms),
        pct(to.process_cycles_ms - from.process_cycles_ms)
    );
    for (name, ms) in &to.threads {
        let before = from
            .threads
            .iter()
            .find(|(n, _)| n == name)
            .map_or(0.0, |(_, v)| *v);
        line.push_str(&format!(" {name}={:.3}", pct(ms - before)));
    }
    line
}

/// A background thread that prints a `cpu-report` line to stdout every `interval` and a final
/// `total` line, covering the whole run, when stopped.
pub struct Reporter {
    stop: Arc<AtomicBool>,
    handle: Option<std::thread::JoinHandle<()>>,
}

impl Reporter {
    pub fn start(interval: Duration) -> Reporter {
        let stop = Arc::new(AtomicBool::new(false));
        let flag = stop.clone();
        let started = Instant::now();
        let origin = snapshot();
        let handle = std::thread::Builder::new()
            .name("cpu-report".into())
            .spawn(move || {
                let mut last = (Instant::now(), origin.clone());
                while !flag.load(Ordering::SeqCst) {
                    std::thread::sleep(Duration::from_millis(100).min(interval));
                    if last.0.elapsed() >= interval {
                        let now = (Instant::now(), snapshot());
                        emit(&report_line(
                            "window",
                            &last.1,
                            &now.1,
                            now.0.duration_since(last.0),
                        ));
                        last = now;
                    }
                }
                emit(&report_line(
                    "total",
                    &origin,
                    &snapshot(),
                    started.elapsed(),
                ));
            })
            .ok();
        Reporter { stop, handle }
    }

    pub fn finish(mut self) {
        self.stop.store(true, Ordering::SeqCst);
        if let Some(h) = self.handle.take() {
            let _ = h.join();
        }
    }
}

fn emit(line: &str) {
    let mut out = std::io::stdout().lock();
    let _ = writeln!(out, "{line}");
    let _ = out.flush();
}

#[cfg(windows)]
mod os {
    use std::sync::OnceLock;
    use std::time::{Duration, Instant};
    use windows::Win32::Foundation::{FILETIME, HANDLE};

    use windows::Win32::System::Threading::{
        GetCurrentProcess, GetCurrentThread, GetCurrentThreadId, GetProcessTimes, OpenThread,
        THREAD_QUERY_LIMITED_INFORMATION,
    };
    use windows::Win32::System::WindowsProgramming::{QueryProcessCycleTime, QueryThreadCycleTime};

    fn ms(kernel: FILETIME, user: FILETIME) -> f64 {
        let ticks = |f: FILETIME| (u64::from(f.dwHighDateTime) << 32) | u64::from(f.dwLowDateTime);
        // FILETIME counts 100 ns.
        (ticks(kernel) + ticks(user)) as f64 / 10_000.0
    }

    pub fn open_current_thread() -> Option<isize> {
        // SAFETY: opens a query-only handle on this thread; it is kept for the process lifetime.
        unsafe {
            OpenThread(
                THREAD_QUERY_LIMITED_INFORMATION,
                false,
                GetCurrentThreadId(),
            )
            .ok()
            .map(|h| h.0 as isize)
        }
    }

    pub fn process_cpu_ms() -> Option<f64> {
        let (mut c, mut e, mut k, mut u) = (
            FILETIME::default(),
            FILETIME::default(),
            FILETIME::default(),
            FILETIME::default(),
        );
        // SAFETY: the pseudo-handle of this process and four valid out-pointers.
        unsafe { GetProcessTimes(GetCurrentProcess(), &mut c, &mut e, &mut k, &mut u).ok()? };
        Some(ms(k, u))
    }

    pub fn process_cycles() -> Option<u64> {
        let mut cycles = 0u64;
        // SAFETY: the pseudo-handle of this process and a valid out-pointer.
        unsafe { QueryProcessCycleTime(GetCurrentProcess(), &mut cycles).ok()? };
        Some(cycles)
    }

    pub fn thread_cycles(handle: isize) -> Option<u64> {
        let mut cycles = 0u64;
        // SAFETY: a handle opened by `open_current_thread` and a valid out-pointer.
        unsafe { QueryThreadCycleTime(HANDLE(handle as *mut _), &mut cycles).ok()? };
        Some(cycles)
    }

    /// CPU cycles per millisecond of CPU time, measured once by spinning the calling thread for
    /// 40 ms (so the first call takes that long).
    pub fn cycles_per_ms() -> f64 {
        static PER_MS: OnceLock<f64> = OnceLock::new();
        *PER_MS.get_or_init(|| {
            let mut start = 0u64;
            // SAFETY: the pseudo-handle of this thread and a valid out-pointer.
            unsafe {
                let _ = QueryThreadCycleTime(GetCurrentThread(), &mut start);
            }
            let began = Instant::now();
            let mut x = 1u64;
            while began.elapsed() < Duration::from_millis(40) {
                x = std::hint::black_box(x.wrapping_mul(6364136223846793005).wrapping_add(1));
            }
            let mut end = 0u64;
            // SAFETY: as above.
            unsafe {
                let _ = QueryThreadCycleTime(GetCurrentThread(), &mut end);
            }
            let ms = began.elapsed().as_secs_f64() * 1000.0;
            (end.saturating_sub(start) as f64 / ms).max(1.0)
        })
    }
}

#[cfg(not(windows))]
mod os {
    pub fn open_current_thread() -> Option<isize> {
        None
    }
    pub fn process_cpu_ms() -> Option<f64> {
        None
    }
    pub fn process_cycles() -> Option<u64> {
        None
    }
    pub fn thread_cycles(_: isize) -> Option<u64> {
        None
    }
    pub fn cycles_per_ms() -> f64 {
        1.0
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_report_line_is_a_percentage_of_one_core_per_thread() {
        let from = Snapshot {
            process_ms: 100.0,
            process_cycles_ms: 90.0,
            threads: vec![("ui".into(), 40.0), ("reader".into(), 10.0)],
        };
        let to = Snapshot {
            process_ms: 300.0,
            process_cycles_ms: 280.0,
            threads: vec![("ui".into(), 140.0), ("reader".into(), 30.0)],
        };
        let line = report_line("window", &from, &to, Duration::from_secs(10));
        assert_eq!(
            line,
            "cpu-report label=window wall_s=10.00 process_pct=2.000 process_cycles_pct=1.900 ui=1.000 reader=0.200"
        );
    }

    #[cfg(windows)]
    #[test]
    fn a_busy_thread_shows_up_in_its_own_figure_and_in_the_process() {
        register_thread("cpu-test");
        let before = snapshot();
        let end = Instant::now() + Duration::from_millis(150);
        let mut x = 1u64;
        while Instant::now() < end {
            x = std::hint::black_box(x.wrapping_mul(6364136223846793005).wrapping_add(1));
        }
        let after = snapshot();
        let own = |s: &Snapshot| {
            s.threads
                .iter()
                .find(|(n, _)| n == "cpu-test")
                .map(|(_, v)| *v)
                .unwrap()
        };
        assert!(own(&after) - own(&before) > 50.0, "the spin used CPU");
        assert!(after.process_ms - before.process_ms > 50.0);
    }
}

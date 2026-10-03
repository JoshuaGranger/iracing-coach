//! Private-corpus acceptance: projects the largest `analysis.json` under `COACH_PRIVATE_CORPUS` and
//! checks the projection budget (80 MB in <= 5 s and <= 600 MB peak) and that every view validates.
//! Never runs by default and never copies the corpus; run it in release mode:
//!
//! ```text
//! COACH_PRIVATE_CORPUS=<dir> cargo test --release -p coach-views --test corpus -- --ignored --nocapture
//! ```

mod common;

use std::fs;
use std::path::{Path, PathBuf};
use std::time::Instant;

use common::*;

fn find_documents(dir: &Path, depth: usize, found: &mut Vec<(u64, PathBuf)>) {
    let Ok(entries) = fs::read_dir(dir) else {
        return;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_dir() && depth > 0 {
            find_documents(&path, depth - 1, found);
        } else if path.file_name().is_some_and(|n| n == "analysis.json")
            && let Ok(meta) = entry.metadata()
        {
            found.push((meta.len(), path));
        }
    }
}

/// Peak working set of this process, read through PowerShell (no unsafe code in this crate).
#[cfg(windows)]
fn peak_memory_bytes() -> Option<u64> {
    let script = format!("(Get-Process -Id {}).PeakWorkingSet64", std::process::id());
    let output = std::process::Command::new("powershell.exe")
        .args(["-NoLogo", "-NoProfile", "-Command", &script])
        .output()
        .ok()?;
    String::from_utf8(output.stdout).ok()?.trim().parse().ok()
}

#[cfg(not(windows))]
fn peak_memory_bytes() -> Option<u64> {
    let status = fs::read_to_string("/proc/self/status").ok()?;
    let line = status.lines().find(|l| l.starts_with("VmHWM:"))?;
    let kib: u64 = line.split_whitespace().nth(1)?.parse().ok()?;
    Some(kib * 1024)
}

#[test]
#[ignore = "needs COACH_PRIVATE_CORPUS"]
fn the_largest_private_analysis_projects_within_budget() {
    let Some(corpus) = std::env::var_os("COACH_PRIVATE_CORPUS") else {
        eprintln!("COACH_PRIVATE_CORPUS is not set");
        return;
    };
    let mut found = Vec::new();
    find_documents(Path::new(&corpus), 8, &mut found);
    found.sort();
    let (size, input) = found
        .pop()
        .expect("no analysis.json under COACH_PRIVATE_CORPUS");
    let out = scratch("corpus").join("out");

    let started = Instant::now();
    let summary = coach_views::project(&input, &out).unwrap();
    let elapsed = started.elapsed();
    let peak = peak_memory_bytes();
    println!(
        "{} MB analysis.json: {} laps, {} channels, {:.2} s, peak {} MB, {} non-finite nulled",
        size / 1_000_000,
        summary.lap_count,
        summary.trace_channels.len(),
        elapsed.as_secs_f64(),
        peak.map_or_else(|| "unknown".to_string(), |p| (p / 1_000_000).to_string()),
        summary.nonfinite_nulled
    );
    for file in &summary.files {
        println!("  {:<20} {:>10} bytes", file.file_name, file.bytes);
    }

    if !cfg!(debug_assertions) {
        assert!(elapsed.as_secs_f64() <= 5.0, "projection took {elapsed:?}");
    }
    if let Some(peak) = peak {
        assert!(peak <= 600_000_000, "peak memory {peak} bytes");
    }
    for (file, schema) in [
        ("race.json", "RaceView"),
        ("runs.json", "RunsView"),
        ("traces-catalog.json", "TraceCatalog"),
        ("tuning.json", "TuningContextProjection"),
        ("investigation.json", "InvestigationView"),
        ("track.json", "TrackGeometry"),
    ] {
        if out.join(file).exists() {
            assert_valid(schema, &view(&out, file));
        }
    }
    let ccol = decode_ccol(&fs::read(out.join("traces.ccol")).unwrap());
    assert_valid("TraceBlobHeader", &ccol.header);
    let doc = read_json(&input);
    let catalog = view(&out, "traces-catalog.json");
    assert_ccol_matches(&ccol, &catalog, &doc, None);
}

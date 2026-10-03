//! Unit U2 (laps): replays every recorded call of `_lap_summaries`, `_service_events` and
//! `_disrupted_sample_indices` against the native entries (`phase2-plan.md` section 3).
//! Committed fixture and synthetic vectors always; the private corpus when
//! `COACH_KERNEL_VECTORS` is set.

mod support;

use support::vectors;

#[test]
fn laps_calls_replay_identically() {
    let counts = vectors::replay_unit("laps");
    let entries: std::collections::BTreeSet<&str> =
        counts.keys().map(|(_, entry)| entry.as_str()).collect();
    for entry in [
        "analysis_engine._lap_summaries",
        "analysis_engine._service_events",
        "analysis_engine._disrupted_sample_indices",
    ] {
        assert!(entries.contains(entry), "no recorded call of {entry}");
    }
}

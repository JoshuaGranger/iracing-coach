//! Unit U3 (traces): replays every recorded call of `_lap_trace_payload` and `_track_profile`
//! against the native entries (`docs/08-rewrite/port/phase2-plan.md` section 3). Committed
//! fixture and synthetic vectors always; the private corpus when `COACH_KERNEL_VECTORS` is set.

mod support;

use support::vectors;

#[test]
fn traces_calls_replay_identically() {
    let counts = vectors::replay_unit("traces");
    let entries: std::collections::BTreeSet<&str> =
        counts.keys().map(|(_, entry)| entry.as_str()).collect();
    for entry in [
        "analysis_engine._lap_trace_payload",
        "analysis_engine._track_profile",
    ] {
        assert!(entries.contains(entry), "no recorded call of {entry}");
    }
}

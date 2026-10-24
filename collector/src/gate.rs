//! The on-track gate (`collector-protocol.md` section 4 step 1).
//!
//! Uploads, hashing and folder scans may run only while the collector holds fresh off-track
//! evidence: iRacing is not running (no map, or status bit 0 clear for at least 2 s), or live data
//! (status bit 0 set, tick advanced within the last 1 s) reports `IsOnTrack == 0`. Anything else,
//! stale or unknown included, counts as on track. The SDK reader reports what it saw on every pass
//! of its loop; the uploader asks `allows()` before every chunk or block.

use std::sync::{Arc, Mutex};

use crate::util::Clock;

/// How long the status-clear state must last before it counts as "iRacing not running".
pub const STATUS_CLEAR_MS: u64 = 2_000;
/// An observation older than this is no evidence at all (the reader thread may be stalled).
pub const OBSERVATION_FRESH_MS: u64 = 1_000;

/// What the SDK reader saw on its latest pass.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Evidence {
    /// The shared-memory map does not exist.
    NoMap,
    /// The map exists but status bit 0 is clear.
    StatusClear,
    /// Status bit 0 set and the tick advanced within the last second; `on_track` is the telemetry
    /// `IsOnTrack` (None when the variable is missing).
    Live { on_track: Option<bool> },
    /// Status bit 0 set but the tick has not advanced within the last second.
    Stale,
}

#[derive(Debug, Clone, Copy)]
struct State {
    evidence: Evidence,
    in_race: Option<bool>,
    /// When the current kind of evidence began (monotonic ms).
    since: u64,
    /// When the reader last reported (monotonic ms).
    seen: u64,
}

pub struct TrackGate {
    clock: Arc<dyn Clock>,
    state: Mutex<Option<State>>,
}

impl TrackGate {
    pub fn new(clock: Arc<dyn Clock>) -> Arc<TrackGate> {
        Arc::new(TrackGate {
            clock,
            state: Mutex::new(None),
        })
    }

    /// Called by the reader on every pass, including idle ones, so the observation stays fresh.
    pub fn observe(&self, evidence: Evidence) {
        self.observe_with_session(evidence, None);
    }

    /// Bulk sync needs a known non-Race session as well as fresh off-track evidence.
    /// `None` means unknown, never an assumption that a session is safe.
    pub fn observe_with_session(&self, evidence: Evidence, in_race: Option<bool>) {
        let now = self.clock.mono_ms();
        let mut guard = self.state.lock().unwrap_or_else(|e| e.into_inner());
        let since = match *guard {
            Some(prev) if same_kind(prev.evidence, evidence) => prev.since,
            _ => now,
        };
        *guard = Some(State {
            evidence,
            in_race,
            since,
            seen: now,
        });
    }

    /// True when file work (hashing, scanning, uploading) may proceed right now.
    pub fn allows_file_work(&self) -> bool {
        self.allows(false)
    }

    /// Uploads, downloads, hashing and scans pause throughout Race, including its garage.
    /// Tiny garage captures retain their existing off-track-only semantics.
    pub fn allows_bulk_work(&self) -> bool {
        self.allows(true)
    }

    fn allows(&self, bulk: bool) -> bool {
        let now = self.clock.mono_ms();
        let guard = self.state.lock().unwrap_or_else(|e| e.into_inner());
        let Some(state) = *guard else {
            return false;
        };
        if now.saturating_sub(state.seen) > OBSERVATION_FRESH_MS {
            return false;
        }
        match state.evidence {
            Evidence::NoMap => true,
            Evidence::StatusClear => now.saturating_sub(state.since) >= STATUS_CLEAR_MS,
            Evidence::Live { on_track } => {
                on_track == Some(false) && (!bulk || state.in_race == Some(false))
            }
            Evidence::Stale => false,
        }
    }

    pub fn latest(&self) -> Option<Evidence> {
        self.state
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .map(|s| s.evidence)
    }
}

fn same_kind(a: Evidence, b: Evidence) -> bool {
    match (a, b) {
        (Evidence::Live { on_track: x }, Evidence::Live { on_track: y }) => x == y,
        _ => a == b,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::util::ManualClock;

    fn gate() -> (Arc<TrackGate>, Arc<ManualClock>) {
        let clock = Arc::new(ManualClock::new(0));
        (TrackGate::new(clock.clone()), clock)
    }

    #[test]
    fn bulk_work_requires_non_race_context_but_garage_capture_keeps_its_gate() {
        let (g, clock) = gate();
        g.observe_with_session(
            Evidence::Live {
                on_track: Some(false),
            },
            Some(true),
        );
        assert!(g.allows_file_work());
        assert!(!g.allows_bulk_work());
        g.observe(Evidence::Live {
            on_track: Some(false),
        });
        assert!(!g.allows_bulk_work(), "unknown session pauses bulk work");
        g.observe_with_session(
            Evidence::Live {
                on_track: Some(false),
            },
            Some(false),
        );
        assert!(g.allows_bulk_work());
        clock.advance(1_001);
        assert!(!g.allows_bulk_work());
        g.observe(Evidence::NoMap);
        assert!(g.allows_bulk_work());
        g.observe(Evidence::StatusClear);
        assert!(!g.allows_bulk_work());
        clock.advance(2_001);
        g.observe(Evidence::StatusClear);
        assert!(g.allows_bulk_work());
    }

    #[test]
    fn no_evidence_means_on_track() {
        let (g, _) = gate();
        assert!(!g.allows_file_work());
    }

    #[test]
    fn missing_map_allows_work_at_once() {
        let (g, _) = gate();
        g.observe(Evidence::NoMap);
        assert!(g.allows_file_work());
    }

    #[test]
    fn status_clear_needs_two_seconds() {
        let (g, clock) = gate();
        g.observe(Evidence::StatusClear);
        assert!(!g.allows_file_work());
        clock.advance(900);
        g.observe(Evidence::StatusClear);
        clock.advance(900);
        g.observe(Evidence::StatusClear);
        assert!(!g.allows_file_work());
        clock.advance(300);
        g.observe(Evidence::StatusClear);
        assert!(g.allows_file_work());
    }

    #[test]
    fn live_data_allows_only_with_is_on_track_false() {
        let (g, _) = gate();
        g.observe(Evidence::Live {
            on_track: Some(true),
        });
        assert!(!g.allows_file_work());
        g.observe(Evidence::Live { on_track: None });
        assert!(!g.allows_file_work());
        g.observe(Evidence::Live {
            on_track: Some(false),
        });
        assert!(g.allows_file_work());
        g.observe(Evidence::Stale);
        assert!(!g.allows_file_work());
    }

    #[test]
    fn a_stalled_reader_withdraws_the_evidence() {
        let (g, clock) = gate();
        g.observe(Evidence::Live {
            on_track: Some(false),
        });
        assert!(g.allows_file_work());
        clock.advance(1_001);
        assert!(!g.allows_file_work());
        g.observe(Evidence::Live {
            on_track: Some(false),
        });
        assert!(g.allows_file_work());
    }

    #[test]
    fn going_on_track_withdraws_at_once() {
        let (g, _) = gate();
        g.observe(Evidence::NoMap);
        assert!(g.allows_file_work());
        g.observe(Evidence::Live {
            on_track: Some(true),
        });
        assert!(!g.allows_file_work());
    }
}

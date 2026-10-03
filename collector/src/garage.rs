//! Passive garage capture (D9, P5 two-moment rule; `collector-protocol.md` section 8).
//!
//! iRacing publishes `CarSetup` on every garage load and apply (`UpdateCount` increments) but
//! `DriverSetupName` and `DriverSetupIsModified` change only at garage exit, for the last file
//! loaded. This state machine records a value snapshot on every `UpdateCount` change while
//! `IsInGarage` is 1 and commits the visit when the name changes at exit. Earlier loads stay
//! "values seen, name unknown". The machine is pure: the reader thread feeds it, the journal and
//! the link carry its output, the uploader thread resolves the `.sto` hash.

use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};

use serde_json::Value;

use crate::journal::{CommitEntry, SnapshotEntry};
use crate::util::{iso_utc, uuid_v4};

/// How long after leaving the garage a changed `DriverSetupName` still counts for the visit.
pub const EXIT_WINDOW_MS: u64 = 5_000;
/// `IsInGarage` back at 1 within this long of falling to 0, with the name unchanged, is a dip in
/// the same visit, not a new one (a person cannot leave and re-enter the garage this fast).
pub const REENTRY_JOIN_MS: u64 = 1_500;

/// Everything the machine reads from the session info (`CarSetup` and `DriverInfo`).
#[derive(Debug, Clone, Default, PartialEq)]
pub struct SetupView {
    pub update_count: Option<i64>,
    pub values: BTreeMap<String, String>,
    /// `DriverInfo.DriverSetupName`.
    pub name: Option<String>,
    /// `DriverSetupIsModified`.
    pub is_modified: Option<bool>,
    /// `DriverSetupLoadTypeName`.
    pub load_type: Option<String>,
    pub car_path: Option<String>,
    pub driver: Option<Value>,
    pub session: Option<Value>,
}

/// One reader pass worth of input.
#[derive(Debug, Clone, Copy)]
pub struct GarageTick<'a> {
    pub now_ms: u64,
    /// Status bit 0 set and the tick advancing.
    pub connected: bool,
    /// Telemetry `IsInGarage`.
    pub in_garage: Option<bool>,
    pub setup: &'a SetupView,
}

#[derive(Debug, Clone, PartialEq)]
pub enum GarageAction {
    Snapshot(SnapshotEntry),
    Commit(CommitEntry),
}

#[derive(Debug, Clone)]
struct Visit {
    id: String,
    entered_ms: u64,
    name_at_entry: Option<String>,
    last_count: Option<i64>,
    /// `(update_count)` of the newest snapshot of this visit.
    last_snapshot: Option<i64>,
    car_path: Option<String>,
    driver: Option<Value>,
    session: Option<Value>,
}

#[derive(Debug, Clone)]
enum State {
    Idle,
    InGarage(Visit),
    Exited { visit: Visit, exited_ms: u64 },
}

#[derive(Debug)]
pub struct GarageCapture {
    state: State,
}

impl Default for GarageCapture {
    fn default() -> Self {
        Self::new()
    }
}

impl GarageCapture {
    pub fn new() -> GarageCapture {
        GarageCapture { state: State::Idle }
    }

    pub fn in_visit(&self) -> bool {
        !matches!(self.state, State::Idle)
    }

    pub fn observe(&mut self, tick: &GarageTick<'_>) -> Vec<GarageAction> {
        let mut out = Vec::new();
        let in_garage = tick.in_garage == Some(true);

        if !tick.connected {
            // The sim went away. A visit that never reached its exit is abandoned (its snapshots
            // stay as "values seen"); one that did exit closes without a name.
            if let State::Exited { visit, exited_ms } =
                std::mem::replace(&mut self.state, State::Idle)
            {
                self.finish(visit, exited_ms, None, tick, &mut out);
            }
            return out;
        }

        loop {
            match std::mem::replace(&mut self.state, State::Idle) {
                State::Idle => {
                    if in_garage {
                        self.state = State::InGarage(open_visit(tick));
                    }
                    break;
                }
                State::InGarage(mut visit) => {
                    snapshot_if_changed(&mut visit, tick, &mut out);
                    if in_garage {
                        self.state = State::InGarage(visit);
                        break;
                    }
                    self.state = State::Exited {
                        visit,
                        exited_ms: tick.now_ms,
                    };
                    // Evaluate the name at once: it may already have changed.
                }
                State::Exited {
                    mut visit,
                    exited_ms,
                } => {
                    if in_garage {
                        if tick.now_ms.saturating_sub(exited_ms) <= REENTRY_JOIN_MS {
                            // A dip, not an exit: iRacing drops `IsInGarage` for a moment while
                            // it applies a setup (seen on the rig, G3), and the SDK name has not
                            // changed. The visit carries on, so its snapshots and its one
                            // commit stay together.
                            snapshot_if_changed(&mut visit, tick, &mut out);
                            self.state = State::InGarage(visit);
                            break;
                        }
                        // Back in the garage before the window closed. Close the old visit
                        // first, as if the window had ended, from its own last snapshot: this
                        // tick's count and values belong to the new visit. The new visit's
                        // baseline is the old visit's last count, so a count that changed
                        // meanwhile is the new visit's first snapshot.
                        let carried = visit.last_count;
                        self.finish(visit, exited_ms, None, tick, &mut out);
                        let mut next = open_visit(tick);
                        next.last_count = carried;
                        self.state = State::InGarage(next);
                        continue;
                    }
                    snapshot_if_changed(&mut visit, tick, &mut out);
                    if let Some(name) = changed_name(&visit, tick.setup) {
                        self.finish(visit, exited_ms, Some(name), tick, &mut out);
                    } else if tick.now_ms.saturating_sub(exited_ms) >= EXIT_WINDOW_MS {
                        self.finish(visit, exited_ms, None, tick, &mut out);
                    } else {
                        self.state = State::Exited { visit, exited_ms };
                    }
                    break;
                }
            }
        }
        out
    }

    /// Closes `visit`. A visit that saw no value change has nothing to commit.
    fn finish(
        &self,
        mut visit: Visit,
        exited_ms: u64,
        name: Option<String>,
        tick: &GarageTick<'_>,
        out: &mut Vec<GarageAction>,
    ) {
        if visit.last_snapshot.is_none() {
            if name.is_none() {
                return;
            }
            // The name changed without a published value change: record the current values so
            // the commit has something to link.
            if tick.setup.update_count.is_some() || !tick.setup.values.is_empty() {
                let count = tick.setup.update_count.unwrap_or(0);
                out.push(GarageAction::Snapshot(snapshot_entry(&visit, count, tick)));
                visit.last_snapshot = Some(count);
            }
        }
        // The SDK name did not change, so the garage loaded no other file than the one it already
        // named, or Reverted, or the same file again. When the setup is then unmodified its values
        // are that file's (D9): the commit carries the exit name, and the hash resolved from it.
        let name = name.or_else(|| unmodified_exit_name(tick.setup));
        let named = name.is_some();
        out.push(GarageAction::Commit(CommitEntry {
            visit_id: visit.id.clone(),
            update_count: visit.last_snapshot,
            entered_at: iso_utc(visit.entered_ms),
            exited_at: iso_utc(exited_ms),
            car_path: visit
                .car_path
                .clone()
                .or_else(|| tick.setup.car_path.clone()),
            driver: visit.driver.clone().or_else(|| tick.setup.driver.clone()),
            session: visit.session.clone().or_else(|| tick.setup.session.clone()),
            setup_name: name,
            sto_sha256: None,
            ambiguous: false,
            is_modified: if named { tick.setup.is_modified } else { None },
            load_type: if named {
                tick.setup.load_type.clone()
            } else {
                None
            },
            hash_pending: named,
        }));
    }
}

fn open_visit(tick: &GarageTick<'_>) -> Visit {
    Visit {
        id: uuid_v4(),
        entered_ms: tick.now_ms,
        name_at_entry: tick.setup.name.clone(),
        last_count: tick.setup.update_count,
        last_snapshot: None,
        car_path: tick.setup.car_path.clone(),
        driver: tick.setup.driver.clone(),
        session: tick.setup.session.clone(),
    }
}

fn snapshot_entry(visit: &Visit, count: i64, tick: &GarageTick<'_>) -> SnapshotEntry {
    SnapshotEntry {
        visit_id: visit.id.clone(),
        update_count: count,
        observed_at: iso_utc(tick.now_ms),
        car_path: tick.setup.car_path.clone().or(visit.car_path.clone()),
        driver: tick.setup.driver.clone().or(visit.driver.clone()),
        session: tick.setup.session.clone().or(visit.session.clone()),
        values: tick.setup.values.clone(),
    }
}

fn snapshot_if_changed(visit: &mut Visit, tick: &GarageTick<'_>, out: &mut Vec<GarageAction>) {
    let Some(count) = tick.setup.update_count else {
        return;
    };
    if visit.last_count == Some(count) {
        return;
    }
    visit.last_count = Some(count);
    visit.last_snapshot = Some(count);
    out.push(GarageAction::Snapshot(snapshot_entry(visit, count, tick)));
}

/// `DriverSetupName` at the exit when `DriverSetupIsModified` is 0 there: the setup is exactly
/// that file. Unknown or modified, it is not a claim the collector can make.
fn unmodified_exit_name(setup: &SetupView) -> Option<String> {
    if setup.is_modified == Some(false) {
        setup.name.clone().filter(|n| !n.is_empty())
    } else {
        None
    }
}

/// The new `DriverSetupName` when it differs from the one at garage entry.
fn changed_name(visit: &Visit, setup: &SetupView) -> Option<String> {
    match (&setup.name, &visit.name_at_entry) {
        (Some(now), Some(then)) if now != then => Some(now.clone()),
        (Some(now), None) => Some(now.clone()),
        _ => None,
    }
}

/// Where the setup file named by the SDK lives.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum StoResolution {
    Found(PathBuf),
    /// Several files carry the name: no hash, `ambiguous: true`.
    Ambiguous,
    NotFound,
}

/// `setups\<car_path>\<name>`, else a search of its subfolders (names compare case-insensitively
/// on the file name only). Read-only.
pub fn resolve_sto(setups_dir: &Path, car_path: Option<&str>, name: &str) -> StoResolution {
    let Some(car_path) = car_path.filter(|c| !c.is_empty() && !c.contains(['/', '\\'])) else {
        return StoResolution::NotFound;
    };
    if name.is_empty() || name.contains(['/', '\\']) {
        return StoResolution::NotFound;
    }
    let car_dir = setups_dir.join(car_path);
    let direct = car_dir.join(name);
    if direct.is_file() {
        return StoResolution::Found(direct);
    }
    let mut matches = Vec::new();
    collect_named(&car_dir, name, 0, &mut matches);
    match matches.len() {
        0 => StoResolution::NotFound,
        1 => StoResolution::Found(matches.remove(0)),
        _ => StoResolution::Ambiguous,
    }
}

fn collect_named(dir: &Path, name: &str, depth: usize, out: &mut Vec<PathBuf>) {
    if depth > 6 {
        return;
    }
    let Ok(entries) = fs::read_dir(dir) else {
        return;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        let Ok(kind) = entry.file_type() else {
            continue;
        };
        if kind.is_dir() {
            collect_named(&path, name, depth + 1, out);
        } else if kind.is_file()
            && path
                .file_name()
                .and_then(|n| n.to_str())
                .is_some_and(|n| n.eq_ignore_ascii_case(name))
        {
            out.push(path);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::util::uuid_v4;

    fn view(count: i64, name: &str, modified: Option<bool>) -> SetupView {
        SetupView {
            update_count: Some(count),
            values: BTreeMap::from([(
                "Tires.LeftFront.ColdPressure".to_string(),
                format!("{count} kPa"),
            )]),
            name: Some(name.to_string()),
            is_modified: modified,
            load_type: Some("user".into()),
            car_path: Some("stockcars2 supra2019".into()),
            driver: None,
            session: None,
        }
    }

    fn step(
        g: &mut GarageCapture,
        now: u64,
        in_garage: bool,
        setup: &SetupView,
    ) -> Vec<GarageAction> {
        g.observe(&GarageTick {
            now_ms: now,
            connected: true,
            in_garage: Some(in_garage),
            setup,
        })
    }

    #[test]
    fn p5_sequence_names_only_the_last_file_loaded() {
        let mut g = GarageCapture::new();
        // Session start: name R, count 2, not in the garage.
        assert!(step(&mut g, 0, false, &view(2, "R.sto", Some(false))).is_empty());
        // Garage opens; count already 3 on the first look: baseline, no snapshot yet.
        assert!(step(&mut g, 1_000, true, &view(3, "R.sto", Some(false))).is_empty());
        // Load Q (count 4), apply (5), load R CAM (6), apply (7), edit (8), apply (9): the name stays R.
        let mut snapshots = 0;
        for (t, n) in [
            (2_000, 4),
            (3_000, 5),
            (4_000, 6),
            (5_000, 7),
            (6_000, 8),
            (7_000, 9),
        ] {
            let out = step(&mut g, t, true, &view(n, "R.sto", Some(false)));
            snapshots += out
                .iter()
                .filter(|a| matches!(a, GarageAction::Snapshot(_)))
                .count();
        }
        assert_eq!(snapshots, 6);
        // Apply and close: IsInGarage drops first, then count 10 and the new name arrive.
        assert!(step(&mut g, 8_000, false, &view(9, "R.sto", Some(false))).is_empty());
        let out = step(
            &mut g,
            8_270,
            false,
            &view(10, "claude LV R CAM.sto", Some(true)),
        );
        match &out[..] {
            [GarageAction::Snapshot(s), GarageAction::Commit(c)] => {
                assert_eq!(s.update_count, 10, "post-exit update belongs to the visit");
                assert_eq!(c.update_count, Some(10));
                assert_eq!(c.setup_name.as_deref(), Some("claude LV R CAM.sto"));
                assert_eq!(c.is_modified, Some(true));
                assert!(c.hash_pending);
                assert_eq!(c.visit_id, s.visit_id);
            }
            other => panic!("unexpected {other:?}"),
        }
        assert!(!g.in_visit());
    }

    /// One visit that changes a value and leaves with the name unchanged (`R.sto`), the exit state
    /// reporting `modified`; the commit that results after the five second window.
    fn unchanged_name_commit(modified: Option<bool>) -> CommitEntry {
        let mut g = GarageCapture::new();
        step(&mut g, 0, false, &view(2, "R.sto", Some(false)));
        step(&mut g, 100, true, &view(2, "R.sto", Some(false)));
        let out = step(&mut g, 200, true, &view(3, "R.sto", Some(false)));
        assert!(matches!(&out[..], [GarageAction::Snapshot(_)]));
        assert!(step(&mut g, 300, false, &view(3, "R.sto", modified)).is_empty());
        assert!(step(&mut g, 4_000, false, &view(3, "R.sto", modified)).is_empty());
        match &step(&mut g, 5_400, false, &view(3, "R.sto", modified))[..] {
            [GarageAction::Commit(c)] => c.clone(),
            other => panic!("unexpected {other:?}"),
        }
    }

    #[test]
    fn leaving_without_a_name_change_commits_after_five_seconds_with_the_exit_name_when_unmodified()
    {
        // The SDK name cannot change when the file already named is loaded again or after a
        // Revert, but unmodified means the values are that file's: name and hash go with them.
        let c = unchanged_name_commit(Some(false));
        assert_eq!(c.setup_name.as_deref(), Some("R.sto"));
        assert_eq!(c.update_count, Some(3));
        assert_eq!(c.is_modified, Some(false));
        assert_eq!(c.load_type.as_deref(), Some("user"));
        assert!(c.hash_pending, "the hash is resolved from the name");
    }

    #[test]
    fn leaving_without_a_name_change_commits_values_only_when_modified_or_unknown() {
        for modified in [Some(true), None] {
            let c = unchanged_name_commit(modified);
            assert_eq!(c.setup_name, None, "{modified:?}: no file can be claimed");
            assert_eq!(c.update_count, Some(3));
            assert!(!c.hash_pending);
            assert_eq!(c.is_modified, None);
        }
    }

    #[test]
    fn the_exit_state_is_read_when_the_window_closes_not_at_the_exit_tick() {
        // iRacing publishes the new name and flag after the exit tick (50 to 270 ms later): a
        // stale `modified` at the exit tick must not decide the commit.
        let mut g = GarageCapture::new();
        step(&mut g, 0, false, &view(2, "R.sto", Some(false)));
        step(&mut g, 100, true, &view(2, "R.sto", Some(false)));
        step(&mut g, 200, true, &view(3, "R.sto", Some(true)));
        assert!(step(&mut g, 300, false, &view(3, "R.sto", Some(true))).is_empty());
        assert!(step(&mut g, 400, false, &view(3, "R.sto", Some(false))).is_empty());
        let out = step(&mut g, 5_400, false, &view(3, "R.sto", Some(false)));
        assert!(
            matches!(&out[..], [GarageAction::Commit(c)] if c.setup_name.as_deref() == Some("R.sto"))
        );
    }

    #[test]
    fn a_visit_without_any_value_change_leaves_no_trace() {
        let mut g = GarageCapture::new();
        step(&mut g, 0, true, &view(2, "R.sto", Some(false)));
        step(&mut g, 100, false, &view(2, "R.sto", Some(false)));
        assert!(step(&mut g, 6_000, false, &view(2, "R.sto", Some(false))).is_empty());
        assert!(!g.in_visit());
    }

    #[test]
    fn a_name_change_without_a_value_change_synthesizes_a_snapshot() {
        let mut g = GarageCapture::new();
        step(&mut g, 0, true, &view(2, "R.sto", Some(false)));
        step(&mut g, 100, false, &view(2, "R.sto", Some(false)));
        let out = step(&mut g, 400, false, &view(2, "Q.sto", Some(false)));
        assert!(
            matches!(&out[..], [GarageAction::Snapshot(s), GarageAction::Commit(c)]
            if s.update_count == 2 && c.update_count == Some(2) && c.setup_name.as_deref() == Some("Q.sto"))
        );
    }

    #[test]
    fn reentering_before_the_window_closes_closes_the_old_visit_first() {
        let mut g = GarageCapture::new();
        step(&mut g, 0, true, &view(2, "R.sto", Some(false)));
        step(&mut g, 100, true, &view(3, "R.sto", Some(false)));
        step(&mut g, 200, false, &view(3, "R.sto", Some(false)));
        let out = step(&mut g, 2_000, true, &view(3, "R.sto", Some(true)));
        assert!(matches!(&out[..], [GarageAction::Commit(c)] if c.setup_name.is_none()));
        assert!(g.in_visit(), "a new visit is open");
    }

    #[test]
    fn reentry_with_a_changed_count_commits_the_old_visit_before_the_new_snapshot() {
        let mut g = GarageCapture::new();
        step(&mut g, 0, true, &view(2, "R.sto", Some(false)));
        let first = step(&mut g, 100, true, &view(3, "R.sto", Some(false)));
        let old_id = match &first[..] {
            [GarageAction::Snapshot(s)] => s.visit_id.clone(),
            other => panic!("unexpected {other:?}"),
        };
        step(&mut g, 200, false, &view(3, "R.sto", Some(true)));
        // Back inside the window with count 4: that value is the new visit's, not the old one's.
        let out = step(&mut g, 2_000, true, &view(4, "R.sto", Some(true)));
        match &out[..] {
            [GarageAction::Commit(c), GarageAction::Snapshot(s)] => {
                assert_eq!(c.visit_id, old_id);
                assert_eq!(
                    c.update_count,
                    Some(3),
                    "the old visit keeps its own snapshot"
                );
                assert_eq!(c.setup_name, None);
                assert_ne!(s.visit_id, old_id, "the snapshot opens the new visit");
                assert_eq!(s.update_count, 4);
                assert_eq!(s.values["Tires.LeftFront.ColdPressure"], "4 kPa");
            }
            other => panic!("unexpected {other:?}"),
        }
        // The new visit carries on from there: its commit names count 4, never the old visit.
        step(&mut g, 2_100, false, &view(4, "R.sto", Some(false)));
        let out = step(&mut g, 2_300, false, &view(4, "Q.sto", Some(true)));
        match &out[..] {
            [GarageAction::Commit(c)] => {
                assert_ne!(c.visit_id, old_id);
                assert_eq!(c.update_count, Some(4));
                assert_eq!(c.setup_name.as_deref(), Some("Q.sto"));
            }
            other => panic!("unexpected {other:?}"),
        }
    }

    #[test]
    fn a_dip_of_in_garage_while_a_setup_is_applied_does_not_split_the_visit() {
        // Found on the rig (G3, 2026-10-03): every Apply or load dropped IsInGarage for a moment,
        // so each UpdateCount change opened a visit of its own and none could carry the name.
        let mut g = GarageCapture::new();
        step(&mut g, 0, false, &view(2, "R.sto", Some(false)));
        step(&mut g, 100, true, &view(2, "R.sto", Some(false)));
        let mut ids = std::collections::BTreeSet::new();
        let mut t = 200;
        for count in 3..=6 {
            // The count changes during the dip: 300 ms with IsInGarage at 0.
            step(&mut g, t, false, &view(count - 1, "R.sto", Some(false)));
            let out = step(&mut g, t + 300, true, &view(count, "R.sto", Some(false)));
            match &out[..] {
                [GarageAction::Snapshot(s)] => {
                    assert_eq!(s.update_count, count);
                    ids.insert(s.visit_id.clone());
                }
                other => panic!("unexpected {other:?}"),
            }
            t += 1_000;
        }
        assert_eq!(ids.len(), 1, "one visit across every dip");
        // The real exit: IsInGarage falls, the name follows 270 ms later.
        assert!(step(&mut g, t, false, &view(6, "R.sto", Some(false))).is_empty());
        let out = step(&mut g, t + 270, false, &view(7, "Q.sto", Some(true)));
        match &out[..] {
            [GarageAction::Snapshot(s), GarageAction::Commit(c)] => {
                assert_eq!(s.update_count, 7);
                assert_eq!(c.visit_id, s.visit_id);
                assert_eq!(&s.visit_id, ids.iter().next().unwrap());
                assert_eq!(c.setup_name.as_deref(), Some("Q.sto"));
            }
            other => panic!("unexpected {other:?}"),
        }
    }

    #[test]
    fn a_dip_that_lasts_longer_than_the_join_window_is_an_exit_and_a_new_visit() {
        let mut g = GarageCapture::new();
        step(&mut g, 0, true, &view(2, "R.sto", Some(false)));
        step(&mut g, 100, true, &view(3, "R.sto", Some(false)));
        step(&mut g, 200, false, &view(3, "R.sto", Some(true)));
        let out = step(
            &mut g,
            200 + REENTRY_JOIN_MS + 1,
            true,
            &view(3, "R.sto", Some(true)),
        );
        assert!(matches!(&out[..], [GarageAction::Commit(c)] if c.setup_name.is_none()));
    }

    #[test]
    fn a_visit_that_reloads_the_named_file_and_leaves_unmodified_carries_that_name() {
        // G3 case 4: the file already named is loaded again, the name cannot change.
        let mut g = GarageCapture::new();
        step(&mut g, 0, false, &view(18, "R.sto", Some(true)));
        step(&mut g, 100, true, &view(18, "R.sto", Some(true)));
        step(&mut g, 200, false, &view(18, "R.sto", Some(true)));
        let out = step(&mut g, 700, true, &view(19, "R.sto", Some(true)));
        assert!(matches!(&out[..], [GarageAction::Snapshot(s)] if s.update_count == 19));
        let out = step(&mut g, 5_000, false, &view(21, "R.sto", Some(false)));
        assert!(matches!(&out[..], [GarageAction::Snapshot(s)] if s.update_count == 21));
        let out = step(&mut g, 10_100, false, &view(21, "R.sto", Some(false)));
        match &out[..] {
            [GarageAction::Commit(c)] => {
                assert_eq!(c.update_count, Some(21));
                assert_eq!(c.setup_name.as_deref(), Some("R.sto"));
                assert_eq!(c.is_modified, Some(false));
                assert!(c.hash_pending);
            }
            other => panic!("unexpected {other:?}"),
        }
    }

    #[test]
    fn a_disconnect_abandons_an_open_visit_and_closes_an_exited_one() {
        let mut g = GarageCapture::new();
        step(&mut g, 0, true, &view(2, "R.sto", Some(false)));
        step(&mut g, 100, true, &view(3, "R.sto", Some(false)));
        let gone = |g: &mut GarageCapture| {
            g.observe(&GarageTick {
                now_ms: 500,
                connected: false,
                in_garage: None,
                setup: &SetupView::default(),
            })
        };
        assert!(gone(&mut g).is_empty());
        assert!(!g.in_visit());
        step(&mut g, 600, true, &view(3, "R.sto", Some(false)));
        step(&mut g, 700, true, &view(4, "R.sto", Some(false)));
        step(&mut g, 800, false, &view(4, "R.sto", Some(false)));
        let out = gone(&mut g);
        assert!(matches!(&out[..], [GarageAction::Commit(c)] if c.setup_name.is_none()));
    }

    #[test]
    fn sto_resolution_prefers_the_direct_path_and_flags_duplicates() {
        let root = std::env::temp_dir().join(format!("coach-sto-{}", uuid_v4()));
        let car = root.join("stockcars2 supra2019");
        fs::create_dir_all(car.join("shop/2026S4")).unwrap();
        fs::create_dir_all(car.join("mine")).unwrap();
        fs::write(car.join("direct.sto"), b"1").unwrap();
        fs::write(car.join("shop/2026S4/Nested.sto"), b"2").unwrap();
        fs::write(car.join("shop/dup.sto"), b"3").unwrap();
        fs::write(car.join("mine/DUP.sto"), b"4").unwrap();
        assert!(matches!(
            resolve_sto(&root, Some("stockcars2 supra2019"), "direct.sto"),
            StoResolution::Found(p) if p.ends_with("direct.sto")
        ));
        assert!(matches!(
            resolve_sto(&root, Some("stockcars2 supra2019"), "nested.sto"),
            StoResolution::Found(_)
        ));
        assert_eq!(
            resolve_sto(&root, Some("stockcars2 supra2019"), "dup.sto"),
            StoResolution::Ambiguous
        );
        assert_eq!(
            resolve_sto(&root, Some("stockcars2 supra2019"), "missing.sto"),
            StoResolution::NotFound
        );
        assert_eq!(
            resolve_sto(&root, None, "direct.sto"),
            StoResolution::NotFound
        );
        assert_eq!(
            resolve_sto(&root, Some("stockcars2 supra2019"), "../x.sto"),
            StoResolution::NotFound
        );
        fs::remove_dir_all(&root).unwrap();
    }
}

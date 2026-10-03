//! The `kernels` process's hand-off (`docs/08-rewrite/port/phase2-plan.md` section 4.2,
//! `dispatch::HANDOFF`): `_setup_channel_series` keeps the series and answers a handle, which
//! `_setup_telemetry` and `_track_profile` take in place of the dict. The vectors record the
//! dict; this replays each recorded `_setup_telemetry` and `_track_profile` call with the
//! handle instead and requires the recorded result and accessed channels. Committed vectors
//! always; the private corpus when `COACH_KERNEL_VECTORS` is set.

mod support;

use coach_analysis::kernels::{Outcome, PyValue, Session};
use support::vectors;

const SERIES: &str = "analysis_engine._setup_channel_series";
/// (unit, entry, parameter name, its position) of the setup series' readers.
const READERS: &[(&str, &str, &str, usize)] = &[
    ("table", "analysis_engine._setup_telemetry", "series", 1),
    (
        "traces",
        "analysis_engine._track_profile",
        "setup_series",
        2,
    ),
];

/// `call` with the setup series argument replaced by `handle`; None when the call relied on
/// the parameter's default (`_track_profile(table)` reads no series).
fn with_handle(
    mut call: coach_analysis::kernels::Call,
    name: &str,
    position: usize,
    handle: &PyValue,
) -> Option<coach_analysis::kernels::Call> {
    if let Some(slot) = call.args.get_mut(position) {
        *slot = handle.clone();
        return Some(call);
    }
    let (_, value) = call.kwargs.iter_mut().find(|(key, _)| key == name)?;
    *value = handle.clone();
    Some(call)
}

#[test]
fn setup_series_handle_replays_its_readers_identically() {
    let mut compared = 0usize;
    for folder in vectors::recordings() {
        let recording = folder
            .file_name()
            .map(|name| name.to_string_lossy().into_owned())
            .unwrap_or_default();
        let session = Session::open_vector_folder(&folder).unwrap_or_else(|e| panic!("{e}"));
        let mut handle = None;
        for &(unit, entry, name, position) in READERS {
            let unit_dir = folder.join(unit);
            if !unit_dir.is_dir() {
                continue;
            }
            for (recorded_entry, file) in vectors::call_files(&unit_dir) {
                if recorded_entry != entry {
                    continue;
                }
                let mut vector =
                    vectors::load_call(&recording, entry, &file).unwrap_or_else(|e| panic!("{e}"));
                let handle = handle.get_or_insert_with(|| {
                    let mut call = coach_analysis::kernels::Call {
                        args: vec![PyValue::Table],
                        kwargs: Vec::new(),
                    };
                    match session.serve(SERIES, &mut call).outcome {
                        Outcome::Value(value @ PyValue::Handle(_)) => value,
                        other => panic!("{recording}: {SERIES} answered {other:?}, not a handle"),
                    }
                });
                let Some(call) = with_handle(vector.call.clone(), name, position, handle) else {
                    continue;
                };
                assert!(
                    vector.mutated.is_empty(),
                    "{}: the oracle mutated an argument",
                    vector.label()
                );
                vector.call = call;
                let mut call = vector.call.clone();
                let answer = session.serve(entry, &mut call);
                if let Err(failure) = vectors::check(&vector, &call, &answer) {
                    panic!("handle: {failure}");
                }
                compared += 1;
            }
        }
    }
    println!(
        "setup series handle: {compared} reader calls identical ({} platform)",
        vectors::platform()
    );
    assert!(compared > 0, "no recorded reader of the setup series");
}

#[test]
fn the_replay_entry_returns_the_series_and_the_handoff_a_handle() {
    let folder = vectors::recordings()
        .into_iter()
        .find(|folder| folder.join("table").is_dir())
        .expect("a committed vector folder with table calls");
    let session = Session::open_vector_folder(&folder).unwrap_or_else(|e| panic!("{e}"));
    let mut call = coach_analysis::kernels::Call {
        args: vec![PyValue::Table],
        kwargs: Vec::new(),
    };
    let replay = session.call(SERIES, &mut call.clone());
    assert!(
        matches!(replay.outcome, Outcome::Value(PyValue::Dict(_))),
        "{:?}",
        replay.outcome
    );
    let served = session.serve(SERIES, &mut call);
    assert!(
        matches!(served.outcome, Outcome::Value(PyValue::Handle(_))),
        "{:?}",
        served.outcome
    );
    assert_eq!(replay.accessed, served.accessed);
    let mut stray = coach_analysis::kernels::Call {
        args: vec![PyValue::Int(1)],
        kwargs: Vec::new(),
    };
    assert!(matches!(
        session.serve(SERIES, &mut stray).outcome,
        Outcome::Error(_)
    ));
}

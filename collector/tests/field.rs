//! A raw SDK replay captures opponents without viewers and uploads only after parking.
mod support;
use coach_collector::fixture::{MapBuilder, SessionSpec, Step, ty};
use coach_collector::{app, cli::RunOptions};
use serde_json::Value;
use support::*;

#[test]
fn replay_opponents_are_durable_and_upload_without_live_viewers() {
    let root = temp_dir("field-e2e");
    let b = MapBuilder::new(60)
        .engine_vars()
        .var("CarIdxLapCompleted", ty::INT, 2, "")
        .var("CarIdxLapDistPct", ty::FLOAT, 2, "%")
        .var("CarIdxPosition", ty::INT, 2, "");
    let mut steps = vec![Step::Static {
        status: 1,
        update: 1,
        session_info: SessionSpec::default().yaml(),
    }];
    for i in 0..61 {
        let mut row = b.row();
        row.set_i32("SessionNum", 0)
            .set_i32("SessionUniqueID", 99)
            .set_bool("IsOnTrack", i < 50)
            .set_f64("SessionTime", f64::from(i) / 10.0)
            .set_array_i32("CarIdxLapCompleted", 0, 1)
            .set_array_i32("CarIdxLapCompleted", 1, i / 20)
            .set_array_i32("CarIdxPosition", 1, 2)
            .set_array_f32("CarIdxLapDistPct", 1, (i % 20) as f32 / 20.0);
        steps.push(Step::Tick {
            t_ms: i as u32 * 100,
            tick: i + 1,
            row,
        });
    }
    let fixture = root.join("field.sdkfix");
    write_file(&fixture, &b.write_steps(Vec::new(), &steps).unwrap());
    let mock = Mock::start(); // welcome.stream_wanted=false: no web viewers.
    app::run(RunOptions {
        server: Some(mock.url()),
        replay: Some(fixture),
        speed: 4.0,
        no_ui: true,
        exit_at_end: true,
        drain_secs: 15,
        iracing_root: Some(root.clone()),
        state_dir: Some(root.join("state")),
        ..RunOptions::default()
    })
    .unwrap();
    mock.with(|s| {
        assert!(s.schema_errors.is_empty(), "{:?}", s.schema_errors);
        assert!(s.ws_binary.is_empty());
        let fields: Vec<_> = s.uploads.values().filter(|u| u.kind == "field").collect();
        assert_eq!(fields.len(), 1);
        assert!(fields[0].verified);
        let text = std::str::from_utf8(&fields[0].part).unwrap();
        let rows: Vec<Value> = text
            .lines()
            .map(|line| serde_json::from_str(line).unwrap())
            .collect();
        // 4x replay speed uses real timestamps: the 6s fixture lasts 1.5s at at most 10Hz.
        assert!(rows.len() >= 10, "{}", rows.len());
        assert!(rows[0]["session_info"].is_string());
        assert_eq!(rows[0]["channels"]["SessionNum"].as_i64(), Some(0));
        assert!(
            rows.iter()
                .any(|r| r["channels"]["CarIdxLapCompleted"][1] == 2)
        );
    });
}

#[test]
fn unavailable_field_disk_keeps_sdk_and_live_pipeline_running() {
    let root = temp_dir("field-unavailable");
    let state = root.join("state");
    std::fs::create_dir_all(&state).unwrap();
    write_file(&state.join("field"), b"blocked path");
    let b = MapBuilder::new(60).engine_vars();
    let mut row = b.row();
    row.set_i32("SessionNum", 0).set_f64("SessionTime", 1.0);
    let fixture = root.join("capture.sdkfix");
    write_file(
        &fixture,
        &b.write_fixture(Vec::new(), &SessionSpec::default().yaml(), &[(0, 1, row)])
            .unwrap(),
    );
    let report = app::run(RunOptions {
        replay: Some(fixture),
        state_dir: Some(state.clone()),
        iracing_root: Some(root),
        dry_run: true,
        no_ui: true,
        exit_at_end: true,
        speed: 0.0,
        ..RunOptions::default()
    })
    .unwrap();
    assert!(report.pipeline.ticks >= 1);
    assert!(report.pipeline.frames_encoded >= 1);
    assert_eq!(std::fs::read(state.join("field")).unwrap(), b"blocked path");
}

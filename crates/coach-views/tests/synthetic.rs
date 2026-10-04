//! Projection of realistic analysis documents: view contents, schema validity, CCOL exactness, bounds,
//! the shrink ladder, atomic output and trace selection.

mod common;

type Mutation = fn(&mut serde_json::Value);

use std::fs;

use coach_views::{
    PROJECTION_VERSION, ProjectError, SelectError, ViewKind, project, select_traces,
};
use common::*;
use serde_json::{Value, json};

fn project_doc(doc: &Value) -> (std::path::PathBuf, coach_views::ProjectionSummary) {
    let dir = scratch("proj");
    let input = write_doc(&dir, doc);
    let out = dir.join("views").join("a").join("p1");
    let summary = project(&input, &out).unwrap();
    (out, summary)
}

fn kinds(summary: &coach_views::ProjectionSummary) -> Vec<ViewKind> {
    summary.files.iter().map(|f| f.kind).collect()
}

#[test]
fn summary_names_every_written_file_with_its_hash() {
    let doc = analysis_doc("abc123", &Options::default());
    let (out, summary) = project_doc(&doc);
    assert_eq!(summary.analysis_id, "abc123");
    assert_eq!(summary.projection_version, PROJECTION_VERSION);
    assert_eq!(summary.schema_version, 1);
    assert_eq!(summary.profile_version, "post-race-foundations-v23");
    assert_eq!(summary.lap_count, 12);
    assert_eq!(summary.bins_per_lap, 160);
    assert_eq!(summary.nonfinite_nulled, 0);
    assert_eq!(
        kinds(&summary),
        [
            ViewKind::Race,
            ViewKind::Runs,
            ViewKind::Investigation,
            ViewKind::TraceCatalog,
            ViewKind::Traces,
            ViewKind::Track,
            ViewKind::Tuning
        ]
    );
    for file in &summary.files {
        let bytes = fs::read(out.join(&file.file_name)).unwrap();
        assert_eq!(bytes.len() as u64, file.bytes, "{}", file.file_name);
        let digest = sha2_hex(&bytes);
        assert_eq!(digest, file.sha256, "{}", file.file_name);
    }
    let names: Vec<_> = fs::read_dir(&out)
        .unwrap()
        .map(|e| e.unwrap().file_name().into_string().unwrap())
        .collect();
    assert_eq!(names.len(), summary.files.len());
}

fn sha2_hex(bytes: &[u8]) -> String {
    use sha2::{Digest, Sha256};
    Sha256::digest(bytes)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

#[test]
fn every_view_validates_against_the_wire_schema() {
    let doc = analysis_doc("abc123", &Options::default());
    let (out, summary) = project_doc(&doc);
    for (file, schema) in [
        ("race.json", "RaceView"),
        ("runs.json", "RunsView"),
        ("investigation.json", "InvestigationView"),
        ("traces-catalog.json", "TraceCatalog"),
        ("track.json", "TrackGeometry"),
        ("tuning.json", "TuningContextProjection"),
    ] {
        assert_valid(schema, &view(&out, file));
    }
    let ccol = fs::read(out.join("traces.ccol")).unwrap();
    let decoded = decode_ccol(&ccol);
    assert_valid("TraceBlobHeader", &decoded.header);
    assert_eq!(decoded.header["projection_version"], PROJECTION_VERSION);
    assert_eq!(decoded.header["analysis_id"], "abc123");
    let catalog = view(&out, "traces-catalog.json");
    let ids: Vec<&str> = catalog["signals"]
        .as_array()
        .unwrap()
        .iter()
        .map(|s| s["id"].as_str().unwrap())
        .collect();
    assert_eq!(
        ids,
        summary
            .trace_channels
            .iter()
            .map(String::as_str)
            .collect::<Vec<_>>()
    );
}

#[test]
fn ccol_reproduces_every_backend_value_and_its_missingness_exactly() {
    let doc = analysis_doc(
        "exact",
        &Options {
            laps: 20,
            ..Options::default()
        },
    );
    let (out, _) = project_doc(&doc);
    let catalog = view(&out, "traces-catalog.json");
    let ccol = decode_ccol(&fs::read(out.join("traces.ccol")).unwrap());
    assert_ccol_matches(&ccol, &catalog, &doc, None);

    // the channels the doc records are all there, with the narrowest exact dtype
    let signals = catalog["signals"].as_array().unwrap();
    let dtype = |id: &str| {
        signals.iter().find(|s| s["id"] == id).unwrap()["dtype"]
            .as_str()
            .unwrap()
            .to_string()
    };
    assert_eq!(dtype("speed"), "f64");
    assert_eq!(dtype("gear"), "u8");
    assert_eq!(dtype("rpm"), "i16");
    assert_eq!(dtype("on-pit-road"), "u8");
    assert_eq!(dtype("overall-position"), "u8");
    assert_eq!(dtype("clutch"), "u8");
    assert_eq!(dtype("lf-pressure"), "f64");
    // a bin with no point is missing in every channel
    let speed = &ccol.columns["speed"];
    assert!(speed.iter().filter(|v| v.is_nan()).count() > 0);
    assert!(
        ccol.columns["gear"].iter().filter(|v| v.is_nan()).count()
            > speed.iter().filter(|v| v.is_nan()).count()
    );
}

#[test]
fn signals_carry_backend_units_labels_and_the_steering_basis() {
    let doc = analysis_doc("sig", &Options::default());
    let (out, _) = project_doc(&doc);
    let catalog = view(&out, "traces-catalog.json");
    let signals = catalog["signals"].as_array().unwrap();
    let find = |id: &str| signals.iter().find(|s| s["id"] == id).unwrap();
    assert_eq!(find("speed")["source_key"], "speed_mph");
    assert_eq!(find("speed")["unit"], "mph");
    assert_eq!(find("speed")["default"], true);
    assert_eq!(find("throttle")["minimum"], 0.0);
    assert_eq!(find("steering")["label"], "Steering (road wheel)");
    assert_eq!(find("steering")["digits"], 2);
    assert_eq!(
        find("lf-pressure")["source_key"],
        "additional_signals.lf-pressure"
    );
    assert_eq!(find("lf-pressure")["unit"], "psi");
    assert_eq!(find("lf-pressure")["short_label"], "LF psi");
    assert_eq!(find("lf-pressure")["label"], "Name of lf-pressure");
    assert_eq!(catalog["steering_basis"]["basis"], "road_wheel");
    assert_eq!(catalog["steering_basis"]["steering_ratio"], 12.0);
    assert_eq!(catalog["steering_basis"]["digits"], 2);
    assert_eq!(catalog["sector_start_pcts"], json!([0.0, 0.5]));
    assert_eq!(catalog["budgets"]["vertex_budget_per_signal"], 48000);
    assert!(catalog.get("omitted_signals").is_none());
    // the catalog's order: fixed channels, then the backend's additional catalog order
    let ids: Vec<&str> = signals.iter().map(|s| s["id"].as_str().unwrap()).collect();
    let tail: Vec<&str> = ids
        .iter()
        .copied()
        .skip_while(|id| *id != "lf-pressure")
        .collect();
    assert_eq!(
        tail,
        [
            "lf-pressure",
            "on-pit-road",
            "overall-position",
            "clutch",
            "fuel-level"
        ]
    );
}

#[test]
fn trace_laps_join_comparison_eligibility_and_keep_unknown_fields() {
    let doc = analysis_doc("join", &Options::default());
    let (out, _) = project_doc(&doc);
    let catalog = view(&out, "traces-catalog.json");
    let race = view(&out, "race.json");
    let trace_laps = catalog["laps"].as_array().unwrap();
    let race_laps = race["laps"].as_array().unwrap();
    assert_eq!(trace_laps.len(), 12);
    for (t, r) in trace_laps.iter().zip(race_laps) {
        assert_eq!(t["lap"], r["lap"]);
        assert_eq!(t["comparison_eligible"], r["comparison_eligible"]);
        assert_eq!(t["exclusion_reasons"], r["exclusion_reasons"]);
        assert_eq!(t["unknown_trace_field"], json!({"kept": true}));
        assert!(t.get("points").is_none());
    }
    // lap 9 is a caution lap, lap 10 restarts: the C# rule
    assert_eq!(trace_laps[8]["comparison_eligible"], false);
    assert_eq!(
        trace_laps[8]["exclusion_reasons"],
        json!(["caution_or_mixed"])
    );
    assert_eq!(trace_laps[9]["exclusion_reasons"], json!(["restart"]));
    assert_eq!(trace_laps[0]["comparison_eligible"], true);
    assert_eq!(trace_laps[0]["run_number"], 1);
    assert_eq!(trace_laps[11]["run_number"], 2);
}

#[test]
fn a_trace_without_a_lap_row_is_not_recorded_as_eligible() {
    let mut doc = analysis_doc(
        "orphan",
        &Options {
            laps: 3,
            ..Options::default()
        },
    );
    doc["laps"].as_array_mut().unwrap().remove(1);
    let (out, _) = project_doc(&doc);
    let catalog = view(&out, "traces-catalog.json");
    let lap2 = &catalog["laps"][1];
    assert_eq!(lap2["lap"], 2);
    assert_eq!(lap2["comparison_eligible"], false);
    assert_eq!(
        lap2["exclusion_reasons"],
        json!(["eligibility_not_recorded"])
    );
}

#[test]
fn sector_times_are_session_time_between_the_sector_boundaries() {
    let doc = analysis_doc(
        "sectors",
        &Options {
            laps: 3,
            points: 160,
            ..Options::default()
        },
    );
    let (out, _) = project_doc(&doc);
    let catalog = view(&out, "traces-catalog.json");
    assert_eq!(catalog["sector_start_pcts"], json!([0.0, 0.5]));
    assert_valid("TraceCatalog", &catalog);
    for lap in catalog["laps"].as_array().unwrap() {
        // session time runs 31 s a lap; the bins span 0.003125 to 0.996875 of it, so a boundary
        // outside them reads the nearest bin: both sectors are 15.403 s, a bin short of the lap
        assert_eq!(lap["sector_times_s"], json!([15.403, 15.403]), "{lap}");
        let sum: f64 = lap["sector_times_s"]
            .as_array()
            .unwrap()
            .iter()
            .map(|v| v.as_f64().unwrap())
            .sum();
        assert!((31.0 - sum).abs() < 0.2, "{sum}");
    }

    // a lap whose bins stop at 62 % has no time at the end of the last sector; the first sector
    // is still measured
    let mut doc = doc;
    doc["lap_traces"]["traces"][1]["points"]
        .as_array_mut()
        .unwrap()
        .truncate(100);
    let (out, _) = project_doc(&doc);
    let catalog = view(&out, "traces-catalog.json");
    assert_eq!(
        catalog["laps"][0]["sector_times_s"],
        json!([15.403, 15.403])
    );
    assert_eq!(catalog["laps"][1]["sector_times_s"], json!([15.403, null]));
    assert_valid("TraceCatalog", &catalog);

    // a lap with no session time at all has none, and a session without sectors lists none
    for point in doc["lap_traces"]["traces"][2]["points"]
        .as_array_mut()
        .unwrap()
    {
        point["session_time_s"] = Value::Null;
    }
    let (out, _) = project_doc(&doc);
    assert_eq!(
        view(&out, "traces-catalog.json")["laps"][2]["sector_times_s"],
        json!([null, null])
    );
    doc["lap_traces"]["sector_start_pcts"] = json!([0.0]);
    let (out, _) = project_doc(&doc);
    let catalog = view(&out, "traces-catalog.json");
    assert!(catalog["laps"][0].get("sector_times_s").is_none());
    assert_valid("TraceCatalog", &catalog);
}

#[test]
fn sector_times_survive_the_catalog_shrink_ladder() {
    let mut doc = analysis_doc(
        "sectors-shrink",
        &Options {
            laps: 12,
            points: 160,
            ..Options::default()
        },
    );
    for trace in doc["lap_traces"]["traces"].as_array_mut().unwrap() {
        trace["junk"] = json!("z".repeat(60_000));
    }
    let (out, _) = project_doc(&doc);
    assert!(size(&out, "traces-catalog.json") <= 512 * 1024);
    let catalog = view(&out, "traces-catalog.json");
    assert!(catalog["laps"][0].get("junk").is_none());
    assert_eq!(
        catalog["laps"][0]["sector_times_s"],
        json!([15.403, 15.403])
    );
}

#[test]
fn pit_stops_list_each_service_with_its_lap_tires_and_fuel() {
    let mut doc = analysis_doc("stops", &Options::default());
    let runs = doc["runs"].as_array_mut().unwrap();
    assert!(runs.len() >= 2);
    runs[0]["end_lap"] = json!(6);
    runs[0]["pit_service"] = json!({"start_time": 1347.5, "end_time": 1361.4, "fuel_added_l": 45.402,
        "tires_changed_observed": ["RR", "LF", "LR", "RF"]});
    runs[1]["end_lap"] = json!(12);
    runs[1]["ended_with_post_run_service"] = json!(true);
    runs[1]["pit_service"] = json!({"start_time": 2000.0, "end_time": 2004.0, "fuel_added_l": 0.0,
        "tires_changed_observed": []});
    let (out, _) = project_doc(&doc);
    let race = view(&out, "race.json");
    assert_valid("RaceView", &race);
    assert_eq!(
        race["pit_stops"],
        json!([
            {"run_number": 1, "lap": 6, "start_time_s": 1347.5, "end_time_s": 1361.4, "service_s": 13.9,
             "tires_changed": ["LF", "LR", "RF", "RR"], "fuel_added_l": 45.402, "fuel_added_gal": 11.994,
             "post_run_service": false},
            {"run_number": 2, "lap": 12, "start_time_s": 2000.0, "end_time_s": 2004.0, "service_s": 4.0,
             "tires_changed": [], "fuel_added_l": 0.0, "fuel_added_gal": 0.0, "post_run_service": true}
        ])
    );

    // no detected service: an empty list, still a valid view
    for run in doc["runs"].as_array_mut().unwrap() {
        run.as_object_mut().unwrap().remove("pit_service");
    }
    let (out, _) = project_doc(&doc);
    let race = view(&out, "race.json");
    assert_eq!(race["pit_stops"], json!([]));
    assert_valid("RaceView", &race);
}

#[test]
fn race_runs_and_investigation_pass_backend_subtrees_through_with_unknown_fields() {
    let doc = analysis_doc("pass", &Options::default());
    let (out, _) = project_doc(&doc);
    let race = view(&out, "race.json");
    assert_eq!(race["identity"], doc["identity"]);
    assert_eq!(race["race_summary"], doc["race_summary"]);
    assert_eq!(race["strategy"], doc["strategy"]);
    assert_eq!(race["damage_repair"], doc["damage_repair"]);
    assert_eq!(race["field_pace"], doc["field_pace"]);
    assert_eq!(
        race["identity"]["unknown_identity_field"],
        json!({"deep": {"kept": 1}})
    );
    let lap = &race["laps"][0];
    assert_eq!(lap["vehicle_dynamics"], doc["laps"][0]["vehicle_dynamics"]);
    assert_eq!(lap["pit_in"], false);
    assert_eq!(lap["run_age_laps"], 1.0);
    let runs = view(&out, "runs.json");
    assert_eq!(runs["runs"], doc["runs"]);
    assert_eq!(
        runs["runs"][0]["future_backend_field"],
        json!({"kept": [1, 2, 3]})
    );
    assert_eq!(runs["corner_tire_age"], doc["corner_tire_age"]);
    let inv = view(&out, "investigation.json");
    assert_eq!(inv["race_investigation"], doc["race_investigation"]);
    assert_eq!(
        inv["race_investigation"]["fixture_metadata"]["synthetic"],
        true
    );
}

#[test]
fn a_backend_without_an_investigation_writes_none() {
    let doc = analysis_doc(
        "none",
        &Options {
            with_investigation: false,
            ..Options::default()
        },
    );
    let (out, summary) = project_doc(&doc);
    assert!(!kinds(&summary).contains(&ViewKind::Investigation));
    assert!(!out.join("investigation.json").exists());
    let mut doc = analysis_doc("absent", &Options::default());
    doc.as_object_mut().unwrap().remove("race_investigation");
    let (_, summary) = project_doc(&doc);
    assert!(!kinds(&summary).contains(&ViewKind::Investigation));
}

#[test]
fn track_geometry_is_copied_in_the_backends_space_without_reprojection() {
    let doc = analysis_doc("track", &Options::default());
    let (out, _) = project_doc(&doc);
    let track = view(&out, "track.json");
    let source = &doc["track_geometry"];
    assert_eq!(track["coordinate_space"], "normalized_local_vector");
    assert_eq!(track["status"], "usable");
    assert_eq!(track["track_configuration_key"], "9001-oval");
    assert_eq!(track["geometry_hash"], "b".repeat(64));
    let main = source["main_path"].as_array().unwrap();
    assert_eq!(
        track["main_path"]["x"].as_array().unwrap().len(),
        main.len()
    );
    for (i, p) in main.iter().enumerate() {
        assert_eq!(track["main_path"]["x"][i], p["x"]);
        assert_eq!(track["main_path"]["y"][i], p["y"]);
        assert_eq!(track["main_path"]["pct"][i], p["lap_pct"]);
    }
    // the pit lane keeps only points with finite x and y; an empty exit path is null
    assert_eq!(track["pit_lane"]["x"], json!([0.1, 0.2]));
    assert!(track["pit_lane"]["pct"][0].is_null());
    assert_eq!(track["pit_entry"]["pct"], json!([0.9, 0.92]));
    assert!(track["pit_exit"].is_null());
    assert!(track["pit_merge_line"].is_null());
    assert_eq!(
        track["start_finish"]["a"],
        json!({"x": 0.9, "y": 0.5, "lap_pct": 0.0})
    );
    assert_eq!(
        track["pit_commitment_line"]["b"],
        json!({"x": 0.12, "y": 0.2, "lap_pct": null})
    );
    assert_eq!(track["transform"], source["transform"]);
    assert_eq!(track["quality"], source["quality"]);
    assert_eq!(track["provenance"], source["geometry_provenance"]);
}

#[test]
fn track_transform_falls_back_to_the_provenance_and_unusable_geometry_writes_no_track() {
    let mut doc = analysis_doc("fallback", &Options::default());
    doc["track_geometry"]
        .as_object_mut()
        .unwrap()
        .remove("transform");
    let (out, _) = project_doc(&doc);
    let track = view(&out, "track.json");
    assert_eq!(
        track["transform"],
        doc["track_geometry"]["geometry_provenance"]["normalization_transform"]
    );

    // (change to the backend geometry, points the tuning map then has): the tuning map does not need the
    // geometry to be drawable; it uses any main path with two points, else the profile's shape
    let cases: [(Mutation, usize); 4] = [
        (|g| g["status"] = json!("unavailable"), 200),
        (|g| g["coordinate_system"] = json!("gps_degrees"), 200),
        (
            |g| g["main_path"] = json!([{"x": 0.1, "y": 0.1}, {"x": 0.2, "y": 0.2}]),
            2,
        ),
        (|g| g["main_path"] = json!([]), 3),
    ];
    for (mutate, map_points) in cases {
        let mut doc = analysis_doc("nogeo", &Options::default());
        mutate(&mut doc["track_geometry"]);
        let (out, summary) = project_doc(&doc);
        assert!(!kinds(&summary).contains(&ViewKind::Track));
        assert!(!out.join("track.json").exists());
        let tuning = view(&out, "tuning.json");
        assert_eq!(tuning["map"]["path"].as_array().unwrap().len(), map_points);
    }
}

#[test]
fn tuning_view_applies_the_c_sharp_identity_eligibility_and_map_rules() {
    let doc = analysis_doc("tune", &Options::default());
    let (out, _) = project_doc(&doc);
    let tuning = view(&out, "tuning.json");
    assert_eq!(tuning["analysis_id"], "tune");
    assert_eq!(tuning["eligible"], true);
    assert_eq!(tuning["can_use_as_evidence"], true);
    assert_eq!(tuning["reasons"], json!([]));
    let id = &tuning["identity"];
    assert_eq!(id["iracing_session_id"], "7001");
    assert_eq!(id["session_unique_id"], 3);
    assert_eq!(id["subsession_id"], 8001);
    assert_eq!(id["session_type"], "Race");
    assert_eq!(id["track_configuration_key"], "9001-oval");
    assert_eq!(id["track"], "Synthetic Speedway");
    assert_eq!(id["layout"], "Oval");
    assert_eq!(id["car_id"], 9002);
    assert_eq!(id["car"], "NASCAR Test Car");
    assert_eq!(id["setup_type"], "Open");
    assert_eq!(id["embedded_setup_available"], true);
    assert_eq!(
        id["source_sha256"],
        json!(["a".repeat(64), "c".repeat(64), "d".repeat(64)])
    );
    let map = &tuning["map"];
    assert_eq!(map["map_identity"], "9001-oval");
    assert_eq!(map["source_label"], "Recorded racing geometry");
    assert_eq!(map["confidence"], "Recorded");
    assert_eq!(map["is_verified"], false);
    assert_eq!(map["geometry_hash"], "b".repeat(64));
    assert_eq!(map["path"].as_array().unwrap().len(), 200);
    assert_eq!(
        map["path"][1],
        json!({"lap_pct": 0.005, "x": doc["track_geometry"]["main_path"][1]["x"], "y": doc["track_geometry"]["main_path"][1]["y"]})
    );
    assert_eq!(map["turns"][0]["corner_id"], "detected-1");
    assert_eq!(map["turns"][0]["confidence"], "Detected");
    assert_eq!(map["turns"][0]["user_verified"], false);
    // both runs have six reference laps; ascending run number
    let offered: Vec<i64> = tuning["representative_runs"]
        .as_array()
        .unwrap()
        .iter()
        .map(|r| r["run_number"].as_i64().unwrap())
        .collect();
    assert_eq!(offered, [1, 2]);
    // runs with fewer than six reference laps are not offered
    let short = analysis_doc(
        "tune3",
        &Options {
            laps: 10,
            runs: 2,
            ..Options::default()
        },
    );
    let (out_short, _) = project_doc(&short);
    assert_eq!(
        view(&out_short, "tuning.json")["representative_runs"],
        json!([])
    );

    let mut big = analysis_doc(
        "tune2",
        &Options {
            laps: 14,
            runs: 1,
            ..Options::default()
        },
    );
    big["identity"]["is_fixed_setup"] = json!(true);
    let (out, _) = project_doc(&big);
    let tuning = view(&out, "tuning.json");
    assert_eq!(tuning["eligible"], false);
    assert_eq!(tuning["can_use_as_evidence"], true);
    assert_eq!(tuning["identity"]["setup_type"], "Fixed");
    assert_eq!(tuning["representative_runs"].as_array().unwrap().len(), 1);
    assert_eq!(tuning["representative_runs"][0]["run_number"], 1);
}

#[test]
fn non_finite_tokens_become_missing_and_are_counted() {
    let doc = analysis_doc(
        "nan",
        &Options {
            laps: 2,
            ..Options::default()
        },
    );
    let mut text = serde_json::to_string(&doc).unwrap();
    // legacy analyzers wrote Python's bare tokens; text inside strings must survive
    text = text.replacen("\"speed_mph\":", "\"speed_mph\":NaN,\"unused\":", 1);
    text = text.replacen("\"lap_time_s\":", "\"lap_time_s\":Infinity,\"x\":", 1);
    text = text.replacen(
        "\"driver_name\":\"Test Driver\"",
        "\"driver_name\":\"NaN Infinity\"",
        1,
    );
    let dir = scratch("nan");
    let input = dir.join("analysis.json");
    fs::write(&input, text).unwrap();
    let out = dir.join("out");
    let summary = project(&input, &out).unwrap();
    assert_eq!(summary.nonfinite_nulled, 2);
    let race = view(&out, "race.json");
    assert_eq!(race["identity"]["driver_name"], "NaN Infinity");
    let ccol = decode_ccol(&fs::read(out.join("traces.ccol")).unwrap());
    // the first point of lap 1 lost its speed; the rest are untouched
    assert!(ccol.columns["speed"][0].is_nan());
    assert!(ccol.columns["speed"].iter().filter(|v| !v.is_nan()).count() > 100);
}

#[test]
fn output_must_not_exist_and_a_failed_projection_leaves_nothing() {
    let doc = analysis_doc(
        "atomic",
        &Options {
            laps: 2,
            ..Options::default()
        },
    );
    let dir = scratch("atomic");
    let input = write_doc(&dir, &doc);
    let out = dir.join("views").join("p1");
    project(&input, &out).unwrap();
    let again = project(&input, &out).unwrap_err();
    assert!(matches!(again, ProjectError::OutputExists(_)));
    // untouched by the failed second attempt
    assert!(out.join("race.json").exists());

    // failure after parsing: no directory, no temp sibling
    let mut bad = doc.clone();
    bad["schema_version"] = json!(3);
    let bad_input = dir.join("bad.json");
    fs::write(&bad_input, serde_json::to_vec(&bad).unwrap()).unwrap();
    let failed_out = dir.join("views").join("failed");
    assert!(matches!(
        project(&bad_input, &failed_out),
        Err(ProjectError::IncompatibleVersion { .. })
    ));
    assert!(!failed_out.exists());
    let siblings: Vec<_> = fs::read_dir(dir.join("views"))
        .unwrap()
        .map(|e| e.unwrap().file_name())
        .collect();
    assert_eq!(siblings.len(), 1, "{siblings:?}");
}

#[test]
fn a_missing_input_is_an_io_error_and_leaves_nothing() {
    let dir = scratch("io");
    let out = dir.join("views").join("p1");
    let err = project(&dir.join("absent.json"), &out).unwrap_err();
    assert!(matches!(err, ProjectError::Io(_)));
    assert!(!out.exists());
}

#[test]
fn malformed_json_is_reported_with_its_position() {
    let dir = scratch("bad");
    let input = dir.join("analysis.json");
    fs::write(&input, b"{\"schema_version\": 2, \"laps\": [1, 2").unwrap();
    let err = project(&input, &dir.join("out")).unwrap_err();
    match err {
        ProjectError::Json { path, .. } => assert!(path.starts_with("line "), "{path}"),
        other => panic!("{other:?}"),
    }
}

#[test]
fn a_real_backend_document_projects() {
    // Written by the Python worker for test-data/ibt/synthetic-race.ibt, paths removed.
    let input = repo_file("crates/coach-views/tests/data/synthetic-race.analysis.json");
    let dir = scratch("real");
    let out = dir.join("out");
    let summary = project(&input, &out).unwrap();
    assert_eq!(summary.profile_version, "post-race-foundations-v23");
    assert_eq!(summary.lap_count, 1);
    assert_eq!(summary.bins_per_lap, 160);
    // no geometry and no GPS: no track view, no latitude or longitude channel
    assert!(!kinds(&summary).contains(&ViewKind::Track));
    assert!(!summary.trace_channels.iter().any(|c| c == "latitude"));
    assert!(summary.trace_channels.iter().any(|c| c == "speed"));
    for (file, schema) in [
        ("race.json", "RaceView"),
        ("runs.json", "RunsView"),
        ("investigation.json", "InvestigationView"),
        ("traces-catalog.json", "TraceCatalog"),
        ("tuning.json", "TuningContextProjection"),
    ] {
        assert_valid(schema, &view(&out, file));
    }
    let doc = read_json(&input);
    let catalog = view(&out, "traces-catalog.json");
    assert_eq!(catalog["steering_basis"]["basis"], "steering_wheel");
    assert_eq!(catalog["steering_basis"]["digits"], 1);
    assert_ccol_matches(
        &decode_ccol(&fs::read(out.join("traces.ccol")).unwrap()),
        &catalog,
        &doc,
        None,
    );
    let tuning = view(&out, "tuning.json");
    assert_eq!(tuning["identity"]["track"], "Synthetic Speedway");
    assert_eq!(tuning["identity"]["car"], "NASCAR Test Car");
}

// ---- bounds and the shrink ladder ----

fn size(out: &std::path::Path, file: &str) -> u64 {
    fs::metadata(out.join(file)).unwrap().len()
}

#[test]
fn payload_bounds_hold_for_a_synthetic_500_lap_analysis() {
    let options = Options {
        laps: 500,
        points: 40,
        runs: 12,
        ..Options::default()
    };
    let doc = analysis_doc("big", &options);
    let (out, summary) = project_doc(&doc);
    assert_eq!(summary.lap_count, 500);
    assert!(
        size(&out, "race.json") <= 2 * 1024 * 1024,
        "race {}",
        size(&out, "race.json")
    );
    assert!(size(&out, "runs.json") <= 1024 * 1024);
    assert!(size(&out, "investigation.json") <= 4 * 1024 * 1024);
    assert!(
        size(&out, "traces-catalog.json") <= 512 * 1024,
        "catalog {}",
        size(&out, "traces-catalog.json")
    );
    assert!(size(&out, "track.json") <= 512 * 1024);
    assert!(size(&out, "tuning.json") <= 1024 * 1024);
    // the default selection of all 500 laps fits one traces request
    let body = select_traces(&out, None, None, 16 * 1024 * 1024).unwrap();
    assert!(body.len() <= 16 * 1024 * 1024, "{}", body.len());
    assert_eq!(
        decode_ccol(&body).header["laps"].as_array().unwrap().len(),
        500
    );
    // every channel at once is stored whole and still decodes exactly
    let ccol = fs::read(out.join("traces.ccol")).unwrap();
    println!(
        "500 laps, {} channels: traces.ccol {} bytes, default selection {} bytes",
        summary.trace_channels.len(),
        ccol.len(),
        body.len()
    );
    let catalog = view(&out, "traces-catalog.json");
    assert_ccol_matches(&decode_ccol(&ccol), &catalog, &doc, None);
    for (file, schema) in [
        ("race.json", "RaceView"),
        ("traces-catalog.json", "TraceCatalog"),
        ("runs.json", "RunsView"),
    ] {
        assert_valid(schema, &view(&out, file));
    }
}

#[test]
fn an_oversized_race_view_shrinks_unread_subtrees_first_then_unread_lap_fields() {
    // field_pace and conditions are read by no screen: they go first and everything else stays whole.
    let mut doc = analysis_doc(
        "shrink1",
        &Options {
            laps: 20,
            ..Options::default()
        },
    );
    doc["field_pace"]["cars"] = json!(vec!["x".repeat(1000); 2400]);
    let (out, _) = project_doc(&doc);
    let race = view(&out, "race.json");
    assert!(race["field_pace"].is_null());
    assert!(race["conditions"].is_null());
    assert_eq!(
        race["laps"][0]["vehicle_dynamics"],
        doc["laps"][0]["vehicle_dynamics"]
    );
    assert_valid("RaceView", &race);

    // fat lap rows next: unread lap fields go, every consumed field and the computed ones stay
    let mut doc = analysis_doc(
        "shrink2",
        &Options {
            laps: 20,
            ..Options::default()
        },
    );
    for lap in doc["laps"].as_array_mut().unwrap() {
        lap["junk"] = json!("y".repeat(120_000));
    }
    let (out, _) = project_doc(&doc);
    let race = view(&out, "race.json");
    assert!(size(&out, "race.json") <= 2 * 1024 * 1024);
    let lap = &race["laps"][3];
    assert!(lap.get("junk").is_none() && lap.get("vehicle_dynamics").is_none());
    for key in [
        "lap",
        "lap_time_s",
        "complete",
        "flag_state",
        "green_fraction",
        "caution_fraction",
        "pit_time_s",
        "racing_state_fraction",
    ] {
        assert_eq!(lap[key], doc["laps"][3][key], "{key}");
    }
    assert_eq!(lap["position"], json!({"start": 5, "end": 5}));
    assert_eq!(
        lap["clean_context"],
        json!({"on_track_fraction": 1.0, "traffic_proximity_fraction": 0.0})
    );
    assert_eq!(
        lap["damage_repair_context"],
        json!({"automatic_coaching_reference_eligible": true, "exclusion_reason_codes": []})
    );
    assert_eq!(lap["comparison_eligible"], true);
    assert_eq!(lap["exclusion_reasons"], json!([]));
    // sub-trees that fit were left alone
    assert_eq!(race["identity"], doc["identity"]);
    assert_valid("RaceView", &race);
}

#[test]
fn a_view_that_cannot_shrink_to_its_bound_fails_instead_of_being_served() {
    let mut doc = analysis_doc(
        "toolarge",
        &Options {
            laps: 2,
            ..Options::default()
        },
    );
    // recorded_laps is a consumed field: it may not be dropped
    doc["race_summary"]["recorded_laps"] = json!("z".repeat(2_300_000));
    let dir = scratch("toolarge");
    let input = write_doc(&dir, &doc);
    let out = dir.join("out");
    let err = project(&input, &out).unwrap_err();
    assert!(
        matches!(
            err,
            ProjectError::TooLarge {
                kind: ViewKind::Race,
                limit: 2_097_152,
                ..
            }
        ),
        "{err:?}"
    );
    assert!(!out.exists());
}

#[test]
fn runs_and_tuning_shrink_unread_run_fields() {
    let mut doc = analysis_doc(
        "runs",
        &Options {
            laps: 14,
            runs: 1,
            ..Options::default()
        },
    );
    doc["runs"][0]["junk"] = json!("q".repeat(1_200_000));
    doc["corner_tire_age"]["runs"] = json!(vec!["c".repeat(100); 10]);
    let (out, _) = project_doc(&doc);
    let runs = view(&out, "runs.json");
    assert!(runs["corner_tire_age"].is_null());
    assert!(runs["runs"][0].get("junk").is_none());
    assert_eq!(runs["runs"][0]["pace"], doc["runs"][0]["pace"]);
    assert_eq!(
        runs["runs"][0]["tire_observation"]["tires"],
        doc["runs"][0]["tire_observation"]["tires"]
    );
    assert_valid("RunsView", &runs);
    let tuning = view(&out, "tuning.json");
    assert!(tuning["representative_runs"][0].get("junk").is_none());
    assert_valid("TuningContextProjection", &tuning);
}

#[test]
fn a_geometry_view_over_its_bound_drops_provenance_first() {
    let mut doc = analysis_doc(
        "geo",
        &Options {
            laps: 2,
            ..Options::default()
        },
    );
    doc["track_geometry"]["geometry_provenance"]["observations"] =
        json!(vec![json!({"id": "w".repeat(900)}); 700]);
    let (out, _) = project_doc(&doc);
    let track = view(&out, "track.json");
    assert_eq!(track["provenance"], json!({}));
    assert_eq!(track["main_path"]["x"].as_array().unwrap().len(), 200);
    assert_valid("TrackGeometry", &track);
}

// ---- the 128-signal limit ----

/// `count` extra signal ids `s000`, `s001`, ... that live for the whole test run.
fn extra_ids(count: usize) -> &'static [&'static str] {
    let ids: Vec<&'static str> = (0..count)
        .map(|i| &*Box::leak(format!("s{i:03}").into_boxed_str()))
        .collect();
    Box::leak(ids.into_boxed_slice())
}

#[test]
fn signals_past_the_contract_limit_are_listed_not_served() {
    let doc = analysis_doc(
        "many",
        &Options {
            laps: 2,
            extras: extra_ids(130),
            ..Options::default()
        },
    );
    let (out, summary) = project_doc(&doc);
    assert_eq!(summary.trace_channels.len(), 128);
    let catalog = view(&out, "traces-catalog.json");
    assert_eq!(catalog["signals"].as_array().unwrap().len(), 128);
    // 15 fixed channels, then the first 113 of the backend's order; only the 17 past the cap are omitted
    let omitted: Vec<String> = (113..130).map(|i| format!("s{i:03}")).collect();
    assert_eq!(catalog["omitted_signals"], json!(omitted));
    assert_valid("TraceCatalog", &catalog);
    let ccol = decode_ccol(&fs::read(out.join("traces.ccol")).unwrap());
    assert_eq!(ccol.columns.len(), 128);
}

#[test]
fn the_76_signals_of_a_real_recording_are_all_served() {
    // 15 fixed channels and 61 additional signals, as in the private recordings
    let doc = analysis_doc(
        "real",
        &Options {
            laps: 2,
            extras: extra_ids(61),
            ..Options::default()
        },
    );
    let (out, summary) = project_doc(&doc);
    assert_eq!(summary.trace_channels.len(), 76);
    let catalog = view(&out, "traces-catalog.json");
    assert_eq!(catalog["signals"].as_array().unwrap().len(), 76);
    assert!(catalog.get("omitted_signals").is_none());
    assert_valid("TraceCatalog", &catalog);
    let ccol = decode_ccol(&fs::read(out.join("traces.ccol")).unwrap());
    assert_eq!(ccol.columns.len(), 76);
}

// ---- select_traces ----

fn selection_fixture() -> (std::path::PathBuf, Value) {
    let doc = analysis_doc(
        "sel",
        &Options {
            laps: 30,
            ..Options::default()
        },
    );
    let (out, _) = project_doc(&doc);
    (out, doc)
}

#[test]
fn select_defaults_to_the_catalogs_default_channels_and_all_laps() {
    let (out, doc) = selection_fixture();
    let catalog = view(&out, "traces-catalog.json");
    let body = select_traces(&out, None, None, 16 * 1024 * 1024).unwrap();
    let ccol = decode_ccol(&body);
    let defaults: Vec<&str> = catalog["signals"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|s| s["default"] == true)
        .map(|s| s["id"].as_str().unwrap())
        .collect();
    let got: Vec<&str> = ccol.columns.keys().map(String::as_str).collect();
    let mut want = defaults.clone();
    want.sort_unstable();
    assert_eq!(got, want);
    assert_ccol_matches(&ccol, &catalog, &doc, None);
    assert_valid("TraceBlobHeader", &ccol.header);
}

#[test]
fn select_returns_only_the_requested_laps_and_channels() {
    let (out, doc) = selection_fixture();
    let catalog = view(&out, "traces-catalog.json");
    let body = select_traces(
        &out,
        Some("2-4,9,28-99"),
        Some("speed, gear ,lf-pressure"),
        16 * 1024 * 1024,
    )
    .unwrap();
    let ccol = decode_ccol(&body);
    assert_eq!(ccol.header["laps"], json!([2, 3, 4, 9, 28, 29, 30]));
    assert_eq!(
        ccol.columns.keys().map(String::as_str).collect::<Vec<_>>(),
        ["gear", "lf-pressure", "speed"]
    );
    let keep = |lap: i64| (2..=4).contains(&lap) || lap == 9 || lap >= 28;
    assert_ccol_matches(&ccol, &catalog, &doc, Some(&keep));
    // channels keep the stored dtype and sentinel
    assert_eq!(ccol.dtypes["gear"], "u8");
    // a selection of laps the analysis does not have is an empty, valid container
    let empty = decode_ccol(&select_traces(&out, Some("500-600"), Some("speed"), 1 << 20).unwrap());
    assert_eq!(empty.header["laps"], json!([]));
    assert_eq!(empty.columns["speed"].len(), 0);
}

#[test]
fn select_rejects_bad_queries_unknown_channels_and_oversized_selections() {
    let (out, _) = selection_fixture();
    for bad in ["", "x", "1,,2", "3-1", "1-"] {
        assert!(
            matches!(
                select_traces(&out, Some(bad), None, 1 << 24),
                Err(SelectError::BadLaps(_))
            ),
            "{bad:?}"
        );
    }
    assert!(matches!(
        select_traces(&out, None, Some("speed,nonsense"), 1 << 24),
        Err(SelectError::UnknownChannel(id)) if id == "nonsense"
    ));
    assert!(matches!(
        select_traces(&out, None, Some(""), 1 << 24),
        Err(SelectError::UnknownChannel(_))
    ));
    match select_traces(&out, None, Some("speed"), 1000) {
        Err(SelectError::TooLarge { bytes, limit }) => {
            assert_eq!(limit, 1000);
            assert!(bytes > 1000);
        }
        other => panic!("{other:?}"),
    }
    // the size reported is the size that would have been served
    let ok = select_traces(&out, None, Some("speed"), 1 << 24).unwrap();
    match select_traces(&out, None, Some("speed"), ok.len() as u64 - 1) {
        Err(SelectError::TooLarge { bytes, .. }) => assert_eq!(bytes, ok.len() as u64),
        other => panic!("{other:?}"),
    }
    assert!(select_traces(&out, None, Some("speed"), ok.len() as u64).is_ok());
}

#[test]
fn select_reports_damaged_view_files_as_corrupt() {
    let (out, _) = selection_fixture();
    let path = out.join("traces.ccol");
    let good = fs::read(&path).unwrap();
    let cases: [(&str, Vec<u8>); 4] = [
        ("magic", [b"XXXX".as_slice(), &good[4..]].concat()),
        ("short", good[..8].to_vec()),
        ("truncated data", good[..good.len() - 100].to_vec()),
        ("header length", {
            let mut v = good.clone();
            v[8..12].copy_from_slice(&u32::MAX.to_le_bytes());
            v
        }),
    ];
    for (name, bytes) in cases {
        fs::write(&path, bytes).unwrap();
        let result = select_traces(&out, None, Some("speed"), 1 << 24);
        assert!(
            matches!(result, Err(SelectError::Corrupt(_))),
            "{name}: {result:?}"
        );
    }
    fs::write(&path, &good).unwrap();
    fs::remove_file(out.join("traces-catalog.json")).unwrap();
    assert!(matches!(
        select_traces(&out, None, None, 1 << 24),
        Err(SelectError::Io(_))
    ));
}

#[test]
fn select_with_no_traces_returns_an_empty_container() {
    let mut doc = analysis_doc(
        "empty",
        &Options {
            laps: 1,
            ..Options::default()
        },
    );
    doc["lap_traces"]["traces"] = json!([]);
    let (out, summary) = project_doc(&doc);
    assert!(summary.trace_channels.is_empty());
    let ccol = decode_ccol(&select_traces(&out, None, None, 1 << 20).unwrap());
    assert_eq!(ccol.header["laps"], json!([]));
    assert_eq!(ccol.header["channels"], json!([]));
}

// ---- malformed traces ----

#[test]
fn two_traces_for_one_lap_or_two_points_in_one_bin_are_rejected() {
    let mut doc = analysis_doc(
        "dup",
        &Options {
            laps: 2,
            ..Options::default()
        },
    );
    doc["lap_traces"]["traces"][1]["lap"] = json!(1);
    let dir = scratch("dup");
    let err = project(&write_doc(&dir, &doc), &dir.join("out")).unwrap_err();
    assert!(
        matches!(err, ProjectError::Json { ref path, .. } if path.contains("traces[1].lap")),
        "{err:?}"
    );

    let mut doc = analysis_doc(
        "dupbin",
        &Options {
            laps: 1,
            ..Options::default()
        },
    );
    let first = doc["lap_traces"]["traces"][0]["points"][0].clone();
    doc["lap_traces"]["traces"][0]["points"]
        .as_array_mut()
        .unwrap()
        .push(first);
    let dir = scratch("dupbin");
    let err = project(&write_doc(&dir, &doc), &dir.join("out")).unwrap_err();
    assert!(
        matches!(err, ProjectError::Json { ref message, .. } if message.contains("distance bin")),
        "{err:?}"
    );
}

#[test]
fn bins_per_lap_follows_the_backend() {
    let mut doc = analysis_doc(
        "bins",
        &Options {
            laps: 1,
            points: 40,
            ..Options::default()
        },
    );
    // 80 bins: lap_pct values are bin centres of 160, so renumber the points' fractions
    doc["lap_traces"]["distance_bins_per_lap"] = json!(80);
    for (i, point) in doc["lap_traces"]["traces"][0]["points"]
        .as_array_mut()
        .unwrap()
        .iter_mut()
        .enumerate()
    {
        point["lap_pct"] = json!(((i as f64 + 0.5) / 80.0 * 1e6).round() / 1e6);
    }
    let (out, summary) = project_doc(&doc);
    assert_eq!(summary.bins_per_lap, 80);
    let catalog = view(&out, "traces-catalog.json");
    assert_eq!(catalog["bins_per_lap"], 80);
    assert_ccol_matches(
        &decode_ccol(&fs::read(out.join("traces.ccol")).unwrap()),
        &catalog,
        &doc,
        None,
    );

    doc["lap_traces"]["distance_bins_per_lap"] = json!(0);
    let dir = scratch("bins0");
    assert!(matches!(
        project(&write_doc(&dir, &doc), &dir.join("o")),
        Err(ProjectError::Json { .. })
    ));
}

#[test]
fn a_null_or_absent_track_profile_still_projects_every_view() {
    // Offline Testing recordings spent on pit road have no usable sample: the analyzer leaves
    // `track_profile` null, and the projection used to fail with "missing required field".
    let reference = analysis_doc("profile", &Options::default());
    let (reference_out, _) = project_doc(&reference);
    for absent in [false, true] {
        let mut doc = reference.clone();
        if absent {
            doc.as_object_mut().unwrap().remove("track_profile");
        } else {
            doc["track_profile"] = Value::Null;
        }
        let (out, summary) = project_doc(&doc);
        for kind in [
            ViewKind::Race,
            ViewKind::Runs,
            ViewKind::TraceCatalog,
            ViewKind::Traces,
            ViewKind::Tuning,
            ViewKind::Track,
        ] {
            assert!(kinds(&summary).contains(&kind), "{kind:?} absent={absent}");
        }
        // the geometry still draws the track and the tuning map; nothing else read the profile
        assert_eq!(view(&out, "track.json"), view(&reference_out, "track.json"));
        let tuning = view(&out, "tuning.json");
        assert_eq!(
            tuning["map"]["path"].as_array().map(Vec::len),
            view(&reference_out, "tuning.json")["map"]["path"]
                .as_array()
                .map(Vec::len)
        );
        for (file, schema) in [
            ("race.json", "RaceView"),
            ("runs.json", "RunsView"),
            ("traces-catalog.json", "TraceCatalog"),
            ("track.json", "TrackGeometry"),
            ("tuning.json", "TuningContextProjection"),
        ] {
            assert_valid(schema, &view(&out, file));
        }
    }
}

#[test]
fn without_a_track_profile_or_a_drawable_path_the_track_is_absent_and_the_tuning_map_empty() {
    let mut doc = analysis_doc("noprofile", &Options::default());
    doc["track_profile"] = Value::Null;
    doc["track_geometry"]["status"] = json!("unavailable");
    doc["track_geometry"]["main_path"] = json!([]);
    let (out, summary) = project_doc(&doc);
    assert!(!kinds(&summary).contains(&ViewKind::Track));
    assert!(!out.join("track.json").exists());
    for kind in [ViewKind::Race, ViewKind::Runs, ViewKind::Tuning] {
        assert!(kinds(&summary).contains(&kind), "{kind:?}");
    }
    let tuning = view(&out, "tuning.json");
    assert!(tuning["map"].is_null() || tuning["map"]["path"].as_array().unwrap().is_empty());
}

#[test]
fn a_track_profile_of_another_type_is_still_rejected() {
    let mut doc = analysis_doc("profiletype", &Options::default());
    doc["track_profile"] = json!([1, 2]);
    let dir = scratch("profiletype");
    let input = write_doc(&dir, &doc);
    let err = project(&input, &dir.join("views").join("p1")).unwrap_err();
    assert!(
        matches!(err, ProjectError::Json { ref path, .. } if path == "track_profile"),
        "{err:?}"
    );
}

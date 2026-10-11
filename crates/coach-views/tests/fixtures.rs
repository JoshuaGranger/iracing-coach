//! The sanitized fixtures under `test-data/`: `analysis-view-*.json` (the `analysis_view` envelope's
//! valid, unknown-field and defective cases) and `race-investigation.json`.

mod common;

use std::path::PathBuf;

use coach_views::{ProjectError, ViewKind, project};
use common::*;
use serde_json::{Value, json};

fn vector(name: &str) -> Value {
    let mut fixture = read_json(&repo_file(&format!("test-data/analysis-view-{name}.json")));
    // Scaffold fixtures used an empty object for this formerly unread subtree. Model an older
    // analysis without the optional producer, leaving the immutable fixture bytes untouched.
    if fixture["pace_attribution"]
        .as_object()
        .is_some_and(|p| p.is_empty())
    {
        fixture.as_object_mut().unwrap().remove("pace_attribution");
    }
    fixture
}

/// The analyze result's nested form around a bare envelope fixture.
fn wrapped(view: Value) -> Value {
    json!({"analysis_id": "fixture-id", "analysis_view": view})
}

fn run(
    doc: &Value,
) -> (
    PathBuf,
    Result<coach_views::ProjectionSummary, ProjectError>,
) {
    let dir = scratch("fixture");
    let input = write_doc(&dir, doc);
    let out = dir.join("out");
    let result = project(&input, &out);
    (out, result)
}

#[test]
fn the_valid_envelope_projects_and_every_view_validates() {
    let (out, result) = run(&wrapped(vector("valid")));
    let summary = result.unwrap();
    assert_eq!(summary.analysis_id, "fixture-id");
    assert_eq!(summary.schema_version, 1);
    assert_eq!(summary.profile_version, "post-race-foundations-v13");
    assert_eq!(summary.lap_count, 1);
    assert_eq!(summary.bins_per_lap, 160);
    assert!(summary.trace_channels.is_empty());
    // no race_investigation and no usable geometry in the fixture
    let kinds: Vec<ViewKind> = summary.files.iter().map(|f| f.kind).collect();
    assert_eq!(
        kinds,
        [
            ViewKind::Race,
            ViewKind::Runs,
            ViewKind::TraceCatalog,
            ViewKind::Traces,
            ViewKind::Tuning
        ]
    );
    let race = view(&out, "race.json");
    assert_valid("RaceView", &race);
    assert_eq!(race["laps"][0]["lap"], 1);
    assert_eq!(race["laps"][0]["lap_time_s"], 30.5);
    assert_eq!(race["laps"][0]["comparison_eligible"], false); // no flag_state: not "green"
    assert_eq!(
        race["race_summary"],
        json!({"recorded_laps": 12, "scheduled_laps": 20})
    );
    assert_eq!(
        race["identity"],
        json!({"subsession_id": 8001, "track": "Sanitized Oval"})
    );
    assert_valid("RunsView", &view(&out, "runs.json"));
    assert_eq!(
        view(&out, "runs.json")["runs"],
        json!([{"laps": 12, "run": 1}])
    );
    assert_valid("TraceCatalog", &view(&out, "traces-catalog.json"));
    assert_valid("TuningContextProjection", &view(&out, "tuning.json"));
    let ccol = decode_ccol(&std::fs::read(out.join("traces.ccol")).unwrap());
    assert_eq!(ccol.header["laps"], json!([]));
}

#[test]
fn empty_sections_are_legal_and_not_treated_as_absent() {
    let (out, result) = run(&wrapped(vector("valid-empty-sections")));
    let summary = result.unwrap();
    assert_eq!(summary.profile_version, "");
    assert_eq!(summary.lap_count, 0);
    let race = view(&out, "race.json");
    assert_valid("RaceView", &race);
    assert_eq!(race["laps"], json!([]));
    assert_eq!(race["identity"], json!({}));
    assert_eq!(view(&out, "runs.json")["runs"], json!([]));
    let tuning = view(&out, "tuning.json");
    assert_valid("TuningContextProjection", &tuning);
    assert_eq!(tuning["eligible"], false);
    assert_eq!(tuning["identity"]["setup_type"], "Unknown");
    assert_valid("TraceCatalog", &view(&out, "traces-catalog.json"));
}

#[test]
fn unknown_fields_are_accepted_and_preserved_inside_passed_through_subtrees() {
    let (_, result) = run(&wrapped(vector("unknown-field")));
    result.unwrap();
    let mut view_json = vector("valid");
    view_json["identity"]["a_field_from_a_newer_producer"] = json!({"kept": true});
    view_json["laps"][0]["another_new_field"] = json!([1, 2]);
    view_json["runs"][0]["third_new_field"] = json!("x");
    let (out, result) = run(&wrapped(view_json));
    result.unwrap();
    assert_eq!(
        view(&out, "race.json")["identity"]["a_field_from_a_newer_producer"],
        json!({"kept": true})
    );
    assert_eq!(
        view(&out, "race.json")["laps"][0]["another_new_field"],
        json!([1, 2])
    );
    assert_eq!(view(&out, "runs.json")["runs"][0]["third_new_field"], "x");
}

#[test]
fn defective_envelopes_fail_with_a_named_field() {
    let (_, result) = run(&wrapped(vector("missing-required")));
    assert!(
        matches!(result, Err(ProjectError::MissingField(ref f)) if f == "data_quality"),
        "{result:?}"
    );

    let (_, result) = run(&wrapped(vector("null-required")));
    assert!(
        matches!(result, Err(ProjectError::MissingField(ref f)) if f == "identity"),
        "{result:?}"
    );

    let (out, result) = run(&wrapped(vector("wrong-type")));
    match result {
        Err(ProjectError::Json { path, message }) => {
            assert_eq!(path, "laps");
            assert!(message.contains("array"), "{message}");
        }
        other => panic!("{other:?}"),
    }
    assert!(!out.exists());
}

#[test]
fn wrong_future_and_boolean_versions_are_incompatible_not_accepted_forward() {
    for (name, found) in [
        ("wrong-version", "2"),
        ("future-version", "99"),
        ("boolean-version", "true"),
    ] {
        let (out, result) = run(&wrapped(vector(name)));
        match result {
            Err(ProjectError::IncompatibleVersion {
                field,
                found: got,
                supported,
            }) => {
                assert_eq!(field, "analysis_view.schema_version", "{name}");
                assert_eq!(got, found, "{name}");
                assert_eq!(supported, "1");
            }
            other => panic!("{name}: {other:?}"),
        }
        assert!(!out.exists(), "{name}");
    }
}

#[test]
fn a_bare_envelope_is_not_an_analysis_document() {
    // flat documents are the analysis document (schema 2) and need their analysis_id
    let (_, result) = run(&vector("valid"));
    assert!(
        matches!(result, Err(ProjectError::MissingField(ref f)) if f == "analysis_id"),
        "{result:?}"
    );
    let mut flat = vector("valid");
    flat["analysis_id"] = json!("x");
    let (_, result) = run(&flat);
    match result {
        Err(ProjectError::IncompatibleVersion {
            field,
            found,
            supported,
        }) => {
            assert_eq!(
                (field, found.as_str(), supported.as_str()),
                ("schema_version", "1", "2")
            );
        }
        other => panic!("{other:?}"),
    }
    // the same content as an analysis document (schema 2) projects
    flat["schema_version"] = json!(2);
    let (_, result) = run(&flat);
    assert_eq!(result.unwrap().analysis_id, "x");
    // no analysis id anywhere
    let (_, result) = run(&json!({"analysis_view": vector("valid")}));
    assert!(
        matches!(result, Err(ProjectError::MissingField(ref f)) if f == "analysis_id"),
        "{result:?}"
    );
    // not an object
    let (_, result) = run(&json!([1, 2]));
    assert!(
        matches!(result, Err(ProjectError::Json { .. })),
        "{result:?}"
    );
}

#[test]
fn the_optional_fields_of_later_envelopes_may_be_absent_but_not_mistyped() {
    let mut v = vector("valid");
    v["field_pace"] = json!([]);
    let (_, result) = run(&wrapped(v));
    assert!(
        matches!(result, Err(ProjectError::Json { ref path, .. }) if path == "field_pace"),
        "{result:?}"
    );
    let mut v = vector("valid");
    v["field_pace"] = Value::Null;
    v["strategy_planning"] = Value::Null;
    let (out, result) = run(&wrapped(v));
    result.unwrap();
    assert!(view(&out, "race.json")["field_pace"].is_null());
}

// ---- race-investigation.json ----

fn with_investigation(investigation: Value) -> Value {
    let mut v = vector("valid");
    v["race_investigation"] = investigation;
    wrapped(v)
}

#[test]
fn the_investigation_fixture_passes_through_whole_and_validates() {
    let investigation = read_json(&repo_file("test-data/race-investigation.json"));
    let (out, result) = run(&with_investigation(investigation.clone()));
    let summary = result.unwrap();
    assert!(
        summary
            .files
            .iter()
            .any(|f| f.kind == ViewKind::Investigation)
    );
    let served = view(&out, "investigation.json");
    assert_valid("InvestigationView", &served);
    assert_eq!(served["race_investigation"], investigation);
    // everything the browser reads is there, including unknown fields
    let inv = &served["race_investigation"];
    assert_eq!(inv["schema_version"], 2);
    assert_eq!(inv["projection_version"], "sections-trials-v9");
    assert_eq!(
        inv["default_comparison_id"],
        investigation["default_comparison_id"]
    );
    assert_eq!(inv["fixture_metadata"]["synthetic"], true);
    assert_eq!(
        inv["comparison_coverage"],
        investigation["comparison_coverage"]
    );
    assert_eq!(
        inv["comparisons"][0]["inspection"],
        investigation["comparisons"][0]["inspection"]
    );
    assert_eq!(inv["runs"][0]["fit"], investigation["runs"][0]["fit"]);
    // the investigation's green exposure reaches the lap rows
    let race = view(&out, "race.json");
    assert_eq!(
        race["laps"][0]["run_age_laps"],
        investigation["laps"][0]["green_exposure"]
    );
}

#[test]
fn an_unknown_investigation_field_survives() {
    let mut investigation = read_json(&repo_file("test-data/race-investigation.json"));
    investigation["field_from_a_newer_backend"] = json!({"a": [1, {"b": 2}]});
    investigation["comparisons"][0]["new_comparison_field"] = json!(7);
    let (out, result) = run(&with_investigation(investigation.clone()));
    result.unwrap();
    assert_eq!(
        view(&out, "investigation.json")["race_investigation"],
        investigation
    );
}

#[test]
fn an_investigation_of_another_schema_version_is_incompatible() {
    for version in [json!(1), json!(3), json!(99), json!(true), json!("2")] {
        let mut investigation = read_json(&repo_file("test-data/race-investigation.json"));
        investigation["schema_version"] = version.clone();
        let (out, result) = run(&with_investigation(investigation));
        match result {
            Err(ProjectError::IncompatibleVersion {
                field,
                found,
                supported,
            }) => {
                assert_eq!(field, "race_investigation.schema_version");
                assert_eq!(found, version.to_string());
                assert_eq!(supported, "2");
            }
            other => panic!("{version}: {other:?}"),
        }
        assert!(!out.exists());
    }
    let mut investigation = read_json(&repo_file("test-data/race-investigation.json"));
    investigation
        .as_object_mut()
        .unwrap()
        .remove("schema_version");
    let (_, result) = run(&with_investigation(investigation));
    assert!(
        matches!(result, Err(ProjectError::MissingField(ref f)) if f == "race_investigation.schema_version"),
        "{result:?}"
    );
}

#[test]
fn an_empty_investigation_means_the_backend_has_none() {
    let (out, result) = run(&with_investigation(json!({})));
    let summary = result.unwrap();
    assert!(
        !summary
            .files
            .iter()
            .any(|f| f.kind == ViewKind::Investigation)
    );
    assert!(!out.join("investigation.json").exists());
}

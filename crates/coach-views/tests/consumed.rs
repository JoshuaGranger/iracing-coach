//! `src/consumed.rs` is a copy of the `x-consumed-fields` lists in `api.openapi.yaml`; the generated
//! `wire.schema.json` carries them too, so a contract change that is not copied here fails this test.

mod common;

use coach_views::consumed;
use common::wire_schema;
use serde_json::Value;

fn listed(node: &Value) -> Vec<String> {
    node["x-consumed-fields"]
        .as_array()
        .unwrap_or_else(|| panic!("no x-consumed-fields in {node}"))
        .iter()
        .map(|v| v.as_str().unwrap().to_string())
        .collect()
}

fn copy(list: &[&str]) -> Vec<String> {
    list.iter().map(|s| s.to_string()).collect()
}

#[test]
fn the_lists_equal_the_contracts() {
    let schema = wire_schema();
    let defs = &schema["$defs"];
    let race = &defs["RaceView"]["allOf"][1]["properties"];
    let runs = &defs["RunsView"]["allOf"][1]["properties"];
    let track = &defs["TrackGeometry"]["allOf"][1]["properties"];
    let cases: [(&str, &Value, &[&str]); 19] = [
        (
            "PaceAttribution",
            &defs["PaceAttribution"],
            consumed::PACE_ATTRIBUTION,
        ),
        ("Lap", &defs["Lap"], consumed::LAP),
        ("BackendRun", &defs["BackendRun"], consumed::BACKEND_RUN),
        ("TraceLap", &defs["TraceLap"], consumed::TRACE_LAP),
        (
            "TraceLap.conditions",
            &defs["TraceLap"]["properties"]["conditions"],
            consumed::TRACE_LAP_CONDITIONS,
        ),
        (
            "SteeringBasis",
            &defs["SteeringBasis"],
            consumed::STEERING_BASIS,
        ),
        (
            "RaceView.identity",
            &race["identity"],
            consumed::RACE_IDENTITY,
        ),
        (
            "RaceView.race_summary",
            &race["race_summary"],
            consumed::RACE_RACE_SUMMARY,
        ),
        (
            "RaceView.race_grades",
            &race["race_grades"],
            consumed::RACE_RACE_GRADES,
        ),
        (
            "RaceView.strategy",
            &race["strategy"],
            consumed::RACE_STRATEGY,
        ),
        (
            "RaceView.damage_repair",
            &race["damage_repair"],
            consumed::RACE_DAMAGE_REPAIR,
        ),
        (
            "RaceView.field_pace",
            &race["field_pace"],
            consumed::RACE_FIELD_PACE,
        ),
        (
            "RaceView.conditions",
            &race["conditions"],
            consumed::RACE_CONDITIONS,
        ),
        (
            "RaceView.data_quality",
            &race["data_quality"],
            consumed::RACE_DATA_QUALITY,
        ),
        (
            "InvestigationView.race_investigation",
            &defs["InvestigationView"]["allOf"][1]["properties"]["race_investigation"],
            consumed::INVESTIGATION,
        ),
        (
            "RunsView.corner_tire_age",
            &runs["corner_tire_age"],
            consumed::RUNS_CORNER_TIRE_AGE,
        ),
        (
            "RunsView.tire_learning",
            &runs["tire_learning"],
            consumed::RUNS_TIRE_LEARNING,
        ),
        (
            "TrackGeometry.quality",
            &track["quality"],
            consumed::TRACK_QUALITY,
        ),
        (
            "TrackGeometry.provenance",
            &track["provenance"],
            consumed::TRACK_PROVENANCE,
        ),
    ];
    for (name, node, copied) in cases {
        assert_eq!(listed(node), copy(copied), "{name}");
    }
}

#[test]
fn every_x_source_object_has_a_list_here() {
    // an x-source object added to a view schema must be added to the cases above
    fn walk(node: &Value, path: &str, found: &mut Vec<String>) {
        match node {
            Value::Object(map) => {
                if map.contains_key("x-source") && map.contains_key("x-consumed-fields") {
                    found.push(path.to_string());
                }
                for (key, value) in map {
                    walk(value, &format!("{path}.{key}"), found);
                }
            }
            Value::Array(items) => {
                for (i, value) in items.iter().enumerate() {
                    walk(value, &format!("{path}[{i}]"), found);
                }
            }
            _ => {}
        }
    }
    let schema = wire_schema();
    let defs = schema["$defs"].as_object().unwrap();
    let projected = [
        "RaceView",
        "RunsView",
        "InvestigationView",
        "TrackGeometry",
        "TraceCatalog",
        "TraceLap",
        "BackendRun",
        "SteeringBasis",
    ];
    let mut found = Vec::new();
    for name in projected {
        walk(&defs[name], name, &mut found);
    }
    found.sort();
    let expected: Vec<String> = [
        "BackendRun",
        "InvestigationView.allOf[1].properties.race_investigation",
        "RaceView.allOf[1].properties.conditions",
        "RaceView.allOf[1].properties.damage_repair",
        "RaceView.allOf[1].properties.data_quality",
        "RaceView.allOf[1].properties.field_pace",
        "RaceView.allOf[1].properties.identity",
        "RaceView.allOf[1].properties.race_grades",
        "RaceView.allOf[1].properties.race_summary",
        "RaceView.allOf[1].properties.strategy",
        "RunsView.allOf[1].properties.corner_tire_age",
        "RunsView.allOf[1].properties.tire_learning",
        "SteeringBasis",
        "TraceLap.properties.conditions",
        "TrackGeometry.allOf[1].properties.provenance",
        "TrackGeometry.allOf[1].properties.quality",
    ]
    .iter()
    .map(|s| s.to_string())
    .collect();
    assert_eq!(found, expected);
}

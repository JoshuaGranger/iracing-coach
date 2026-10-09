//! Producer values are passed through exactly; only section identity, ordering and the bound change.
mod common;

use coach_views::{ProjectError, project};
use common::*;
use serde_json::{Value, json};

fn priority(start: f64, end: f64, loss: f64, grade: Value) -> Value {
    json!({"name":"unrelated","start_pct":start,"end_pct":end,"recoverable_s":loss,
        "best_lap":17,"best_s":4.1,"median_s":4.41,"tire_energy_share":0.18,
        "tire_energy_grade":grade,"future_optional":{"kept":true}})
}

#[test]
fn flat_and_nested_projection_copy_quantities_and_attach_only_unique_overlap() {
    for nested in [false, true] {
        let mut doc = analysis_doc("pace", &Options::default());
        doc["race_investigation"]["sections"]["sections"] = json!([
            {"id":"a","label":"A","start_pct":0.0,"end_pct":0.25},
            {"id":"b","label":"B","start_pct":0.25,"end_pct":0.5}
        ]);
        let priorities = json!([
            priority(0.0, 0.2, 0.31, json!("slip_weighted")),
            priority(0.3, 0.5, 0.5, json!("load_only")),
            priority(0.0, 1.0, 0.9, Value::Null),
            priority(0.0, 0.5, 0.7, json!("slip_weighted"))
        ]);
        doc["pace_attribution"] =
            json!({"schema_version":1,"priorities":priorities,"future_optional":{"kept":true}});
        if nested {
            doc["schema_version"] = json!(1);
            doc = json!({"analysis_id":"pace","analysis_view":doc});
        }
        let dir = scratch("pace");
        let out = dir.join("views");
        project(&write_doc(&dir, &doc), &out).unwrap();
        let inv = view(&out, "investigation.json");
        assert_valid("InvestigationView", &inv);
        let projected = &inv["pace_attribution"];
        assert_eq!(projected["future_optional"], json!({"kept":true}));
        for (rank, (index, id)) in [
            (2, Value::Null),
            (3, Value::Null),
            (1, json!("b")),
            (0, json!("a")),
        ]
        .into_iter()
        .enumerate()
        {
            let mut expected = priorities[index].clone();
            expected["section_id"] = id;
            assert_eq!(projected["priorities"][rank], expected);
        }
    }
}

#[test]
fn older_analysis_projects_null_and_present_incompatible_payload_fails() {
    let mut doc = analysis_doc("old", &Options::default());
    doc.as_object_mut().unwrap().remove("pace_attribution");
    let dir = scratch("old-pace");
    let out = dir.join("views");
    project(&write_doc(&dir, &doc), &out).unwrap();
    assert!(view(&out, "investigation.json")["pace_attribution"].is_null());
    doc["pace_attribution"] = json!({"schema_version":2,"priorities":[]});
    assert!(matches!(
        project(&write_doc(&dir, &doc), &dir.join("bad")),
        Err(ProjectError::IncompatibleVersion {
            field: "pace_attribution.schema_version",
            ..
        })
    ));
}

#[test]
fn shrink_keeps_all_consumed_pace_fields_and_at_most_32_priorities() {
    let mut doc = analysis_doc("bounded", &Options::default());
    doc["pace_attribution"] = json!({"schema_version":1,"priorities":(0..40).map(|i| priority(0.0,0.1,i as f64,json!("load_only"))).collect::<Vec<_>>(),"future_big":"x".repeat(400_000)});
    let dir = scratch("bounded-pace");
    let out = dir.join("views");
    project(&write_doc(&dir, &doc), &out).unwrap();
    let inv = view(&out, "investigation.json");
    assert_valid("InvestigationView", &inv);
    let priorities = inv["pace_attribution"]["priorities"].as_array().unwrap();
    assert_eq!(priorities.len(), 32);
    assert_eq!(priorities[0]["recoverable_s"], 39.0);
    assert_eq!(priorities[31]["recoverable_s"], 8.0);
    for p in priorities {
        assert_eq!(p["best_lap"], 17);
        assert_eq!(p["tire_energy_share"], 0.18);
        assert_eq!(p["tire_energy_grade"], "load_only");
        assert!(p.as_object().unwrap().contains_key("section_id"));
    }
}

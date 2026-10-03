use coach_wire::api::{CollectorClientMessage, Health, HealthStatus, Lap};
use serde_json::{Value, json};

const SCHEMA: &str = include_str!("../schema/wire.schema.json");

fn schema() -> Value {
    serde_json::from_str(SCHEMA).unwrap()
}

#[test]
fn every_component_schema_compiles_and_resolves_its_refs() {
    let doc = schema();
    let defs = doc["$defs"].as_object().unwrap();
    assert!(defs.len() >= 100, "{} definitions", defs.len());
    for name in defs.keys() {
        let mut root = doc.clone();
        root["$ref"] = json!(format!("#/$defs/{name}"));
        jsonschema::validator_for(&root).unwrap_or_else(|e| panic!("{name}: {e}"));
    }
}

#[test]
fn schema_validates_and_rejects_instances() {
    let mut root = schema();
    root["$ref"] = json!("#/$defs/Health");
    let validator = jsonschema::validator_for(&root).unwrap();
    assert!(
        validator
            .is_valid(&json!({"status": "ok", "server_version": "0.19.0", "schema_version": 1}))
    );
    assert!(
        !validator
            .is_valid(&json!({"status": "down", "server_version": "0.19.0", "schema_version": 1}))
    );
}

#[test]
fn health_round_trips() {
    let health: Health = serde_json::from_value(
        json!({"status": "ok", "server_version": "0.19.0", "schema_version": 1}),
    )
    .unwrap();
    assert_eq!(health.status, HealthStatus::Ok);
    assert_eq!(
        serde_json::to_value(&health).unwrap(),
        json!({"status": "ok", "server_version": "0.19.0", "schema_version": 1})
    );
    assert!(
        serde_json::from_value::<Health>(
            json!({"status": "down", "server_version": "x", "schema_version": 1})
        )
        .is_err()
    );
}

#[test]
fn unknown_backend_fields_round_trip() {
    let input = json!({
        "lap": 7, "lap_time_s": 92.5, "comparison_eligible": false, "exclusion_reasons": ["pit"],
        "future_backend_field": {"a": [1, 2]}
    });
    let lap: Lap = serde_json::from_value(input.clone()).unwrap();
    assert_eq!(lap.lap, 7);
    assert_eq!(serde_json::to_value(&lap).unwrap(), input);
}

#[test]
fn collector_messages_dispatch_on_type() {
    let hello = json!({
        "type": "hello", "protocol": 1, "collector_id": "9b2c3a52-7a1e-4c5e-9a53-0d6f4d1d2f10",
        "machine": "DESKTOP-SIM", "collector_version": "0.19.0", "live_table_version": 1
    });
    let message: CollectorClientMessage = serde_json::from_value(hello).unwrap();
    assert!(matches!(message, CollectorClientMessage::CollectorHello(_)));
    let unknown = json!({"type": "nonsense", "protocol": 1});
    assert!(serde_json::from_value::<CollectorClientMessage>(unknown).is_err());
}

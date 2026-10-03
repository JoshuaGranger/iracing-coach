use std::{env, fs, path::Path};

use schemars::schema::{InstanceType, SchemaObject};
use serde_json::{Map, Value, json};
use typify::{TypeSpace, TypeSpaceSettings};

/// Wire timestamps and ids travel as text; keep them `String` (no chrono or uuid in the wire crate).
fn string_format(format: &str) -> SchemaObject {
    SchemaObject {
        instance_type: Some(InstanceType::String.into()),
        format: Some(format.to_string()),
        ..Default::default()
    }
}

/// Keywords whose value is a map of name -> schema.
const SCHEMA_MAPS: [&str; 3] = ["properties", "$defs", "patternProperties"];
/// Keywords whose value is one schema or an array of schemas.
const SCHEMA_SLOTS: [&str; 6] = [
    "items",
    "allOf",
    "oneOf",
    "anyOf",
    "additionalProperties",
    "not",
];

/// Rewrites the contract's JSON Schema 2020-12 into the shapes typify turns into precise Rust:
/// - `{"const": x}` becomes a one-value enum, so a `kind` or `type` discriminator is a real type
///   and `oneOf` members that differ only by it stay distinguishable;
/// - `additionalProperties: true` becomes `{}`, so unknown fields are kept in a map (README rule).
fn normalize(schema: &mut Value) {
    let Value::Object(object) = schema else {
        return;
    };
    if let Some(constant) = object.remove("const") {
        if !object.contains_key("type") {
            let ty = match &constant {
                Value::String(_) => "string",
                Value::Bool(_) => "boolean",
                Value::Number(n) if n.is_i64() || n.is_u64() => "integer",
                Value::Number(_) => "number",
                _ => "null",
            };
            object.insert("type".into(), json!(ty));
        }
        object.entry("enum").or_insert_with(|| json!([constant]));
    }
    if object.get("additionalProperties") == Some(&Value::Bool(true)) {
        object.insert("additionalProperties".into(), Value::Object(Map::new()));
    }
    for key in SCHEMA_MAPS {
        if let Some(Value::Object(map)) = object.get_mut(key) {
            map.values_mut().for_each(normalize);
        }
    }
    for key in SCHEMA_SLOTS {
        match object.get_mut(key) {
            Some(Value::Array(items)) => items.iter_mut().for_each(normalize),
            Some(value @ Value::Object(_)) => normalize(value),
            _ => {}
        }
    }
}

fn main() {
    let schema_path = "schema/wire.schema.json";
    println!("cargo:rerun-if-changed={schema_path}");
    println!("cargo:rerun-if-changed=build.rs");

    let content = fs::read_to_string(schema_path).expect("read schema/wire.schema.json");
    let mut document: Value =
        serde_json::from_str(&content).expect("parse schema/wire.schema.json");
    normalize(&mut document);
    let schema: schemars::schema::RootSchema =
        serde_json::from_value(document).expect("wire schema as RootSchema");

    let mut settings = TypeSpaceSettings::default();
    settings
        .with_derive("PartialEq".to_string())
        .with_map_type("::std::collections::BTreeMap".to_string())
        .with_conversion(string_format("date-time"), "String", std::iter::empty())
        .with_conversion(string_format("uuid"), "String", std::iter::empty());
    let mut type_space = TypeSpace::new(&settings);
    type_space
        .add_root_schema(schema)
        .expect("typify wire schema");

    let file = syn::parse2::<syn::File>(type_space.to_stream()).expect("parse generated tokens");
    let out = Path::new(&env::var("OUT_DIR").expect("OUT_DIR")).join("api.rs");
    fs::write(out, prettyplease::unparse(&file)).expect("write api.rs");
}

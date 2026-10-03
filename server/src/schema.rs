//! Request validation against the contract's component schemas (`wire.schema.json`, generated
//! from `api.openapi.yaml`). Requests are validated as JSON values so closed objects, lengths and
//! enums are enforced exactly as the contract states, without a typed struct per request.

use std::collections::HashMap;
use std::sync::{Arc, Mutex, OnceLock};

use jsonschema::Validator;
use serde_json::Value;

use crate::error::{Error, Result};

const WIRE_SCHEMA: &str = include_str!("../../crates/coach-wire/schema/wire.schema.json");

pub struct Schemas {
    doc: Value,
    cache: Mutex<HashMap<String, Arc<Validator>>>,
}

impl Schemas {
    pub fn global() -> &'static Schemas {
        static CELL: OnceLock<Schemas> = OnceLock::new();
        CELL.get_or_init(|| Schemas {
            doc: serde_json::from_str(WIRE_SCHEMA).expect("wire.schema.json parses"),
            cache: Mutex::new(HashMap::new()),
        })
    }

    pub fn doc(&self) -> &Value {
        &self.doc
    }

    pub fn has(&self, name: &str) -> bool {
        self.doc["$defs"].get(name).is_some()
    }

    fn validator(&self, name: &str) -> Result<Arc<Validator>> {
        if let Some(v) = self.cache.lock().unwrap().get(name) {
            return Ok(v.clone());
        }
        if !self.has(name) {
            return Err(Error::internal(format!("unknown wire schema {name}")));
        }
        let mut root = self.doc.clone();
        root["$ref"] = Value::String(format!("#/$defs/{name}"));
        let validator = jsonschema::validator_for(&root)
            .map_err(|e| Error::internal(format!("compile schema {name}: {e}")))?;
        let validator = Arc::new(validator);
        self.cache
            .lock()
            .unwrap()
            .insert(name.to_string(), validator.clone());
        Ok(validator)
    }

    /// Up to five error messages, or Ok.
    pub fn check(
        &self,
        name: &str,
        instance: &Value,
    ) -> Result<std::result::Result<(), Vec<String>>> {
        let validator = self.validator(name)?;
        let errors: Vec<String> = validator
            .iter_errors(instance)
            .take(5)
            .map(|e| {
                let path = e.instance_path().to_string();
                if path.is_empty() {
                    e.to_string()
                } else {
                    format!("{path}: {e}")
                }
            })
            .collect();
        Ok(if errors.is_empty() {
            Ok(())
        } else {
            Err(errors)
        })
    }
}

/// 400 `invalid_request` when `instance` does not satisfy the named schema.
pub fn validate_request(name: &str, instance: &Value) -> Result<()> {
    match Schemas::global().check(name, instance)? {
        Ok(()) => Ok(()),
        Err(errors) => Err(Error::Api(
            crate::error::ApiError::new(
                axum::http::StatusCode::BAD_REQUEST,
                "invalid_request",
                format!("{name}: {}", errors.join("; ")),
            )
            .with_details(serde_json::json!({ "errors": errors })),
        )),
    }
}

/// Parses a JSON body and validates it.
pub fn parse_request(name: &str, body: &[u8]) -> Result<Value> {
    let value: Value = serde_json::from_slice(body)
        .map_err(|e| Error::invalid(format!("body is not valid JSON: {e}")))?;
    validate_request(name, &value)?;
    Ok(value)
}

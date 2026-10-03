//! Lenient JSON readers with the semantics of the C# `RuntimeMapper` helpers: a wrong type reads as
//! absent, a non-finite number is absent, a number read as text is its JSON text.

use serde_json::{Map, Value};

pub(crate) type Obj = Map<String, Value>;

pub(crate) fn num_of(value: &Value) -> Option<f64> {
    match value {
        Value::Number(n) => n.as_f64().filter(|x| x.is_finite()),
        _ => None,
    }
}

pub(crate) fn num(map: &Obj, key: &str) -> Option<f64> {
    map.get(key).and_then(num_of)
}

/// An integer, or a float within 1e-9 of one (C# `NullableIntegerValue`).
pub(crate) fn int_of(value: &Value) -> Option<i64> {
    let Value::Number(n) = value else {
        return None;
    };
    if let Some(i) = n.as_i64() {
        return Some(i);
    }
    let x = n.as_f64()?;
    let rounded = x.round();
    (x.is_finite() && (x - rounded).abs() <= 1e-9 && rounded.abs() < 9.0e15)
        .then_some(rounded as i64)
}

pub(crate) fn int(map: &Obj, key: &str) -> Option<i64> {
    map.get(key).and_then(int_of)
}

/// A string, or a number as JSON text; anything else is absent.
pub(crate) fn text_of(value: &Value) -> Option<String> {
    match value {
        Value::String(s) => Some(s.clone()),
        Value::Number(n) => Some(n.to_string()),
        _ => None,
    }
}

pub(crate) fn text(map: &Obj, key: &str) -> Option<String> {
    map.get(key).and_then(text_of)
}

/// `text` that is not empty or whitespace.
pub(crate) fn nonblank(map: &Obj, key: &str) -> Option<String> {
    text(map, key).filter(|s| !s.trim().is_empty())
}

pub(crate) fn flag(map: &Obj, key: &str) -> Option<bool> {
    map.get(key).and_then(Value::as_bool)
}

pub(crate) fn obj<'a>(map: &'a Obj, key: &str) -> Option<&'a Obj> {
    map.get(key).and_then(Value::as_object)
}

pub(crate) fn arr<'a>(map: &'a Obj, key: &str) -> &'a [Value] {
    map.get(key)
        .and_then(Value::as_array)
        .map_or(&[], Vec::as_slice)
}

/// 64 hex characters, lowercase, unique, ascending (C# `Hashes`).
pub(crate) fn hashes<'a>(values: impl IntoIterator<Item = &'a Value>) -> Vec<String> {
    let mut out: Vec<String> = values
        .into_iter()
        .filter_map(text_of)
        .filter(|s| s.len() == 64 && s.bytes().all(|b| b.is_ascii_hexdigit()))
        .map(|s| s.to_ascii_lowercase())
        .collect();
    out.sort();
    out.dedup();
    out
}

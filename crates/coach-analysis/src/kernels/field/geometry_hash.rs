//! `race_foundations.track_geometry_sha256`: the SHA-256 of
//! `json.dumps(canonical_geometry, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
//! default=str)` encoded as UTF-8.
//!
//! The writer is `json.dumps` with those flags: keys sorted by code point, `float.__repr__` for
//! floats (`NaN`, `Infinity`, `-Infinity` allowed, as `allow_nan` defaults to true), `int` as
//! decimal, strings escaped as `c_encode_basestring` does. `default=str` applies to values JSON
//! cannot encode; the geometry layers hold none, so meeting one is an `Internal` error rather
//! than a guess at its `str()`.

use sha2::{Digest, Sha256};

use crate::kernels::codec::write_string;
use crate::kernels::error::KernelError;
use crate::kernels::value::{Dict, PyValue};

/// `track_geometry_sha256(value)` of the geometry result dict.
pub fn track_geometry_sha256(value: &Dict) -> Result<String, KernelError> {
    let empty = PyValue::List(Vec::new());
    let none = PyValue::None;
    // `value.get(key) or []` for the layers, `value.get(key)` for the rest.
    let layer = |key: &str| -> Result<&PyValue, KernelError> {
        match value.get(key) {
            Some(found) if found.truthy()? => Ok(found),
            _ => Ok(&empty),
        }
    };
    let plain = |key: &str| value.get(key).unwrap_or(&none);
    let canonical: [(&str, &PyValue); 9] = [
        ("track_configuration_key", plain("track_configuration_key")),
        ("coordinate_system", plain("coordinate_system")),
        ("main_path", layer("main_path")?),
        ("pit_lane", layer("pit_lane")?),
        ("pit_entry_path", layer("pit_entry_path")?),
        ("pit_exit_path", layer("pit_exit_path")?),
        ("start_finish_line", plain("start_finish_line")),
        ("pit_commitment_line", plain("pit_commitment_line")),
        ("pit_merge_line", plain("pit_merge_line")),
    ];
    let mut text = String::new();
    let mut sorted = canonical;
    sorted.sort_by(|a, b| a.0.cmp(b.0));
    text.push('{');
    for (index, (key, item)) in sorted.iter().enumerate() {
        if index > 0 {
            text.push(',');
        }
        write_string(&mut text, key);
        text.push(':');
        write_value(&mut text, item)?;
    }
    text.push('}');
    let digest = Sha256::digest(text.as_bytes());
    Ok(digest.iter().map(|byte| format!("{byte:02x}")).collect())
}

/// `json.dumps` of `value` with `sort_keys=True`, compact separators, `ensure_ascii=False`.
pub fn write_value(out: &mut String, value: &PyValue) -> Result<(), KernelError> {
    match value {
        PyValue::None => out.push_str("null"),
        PyValue::Bool(true) => out.push_str("true"),
        PyValue::Bool(false) => out.push_str("false"),
        PyValue::Int(i) => out.push_str(&i.to_string()),
        PyValue::BigInt(text) => out.push_str(text),
        PyValue::Float(x) => out.push_str(&coach_numerics::json_float(*x, true)?),
        PyValue::Str(text) => write_string(out, text),
        PyValue::List(items) | PyValue::Tuple(items) => {
            out.push('[');
            for (index, item) in items.iter().enumerate() {
                if index > 0 {
                    out.push(',');
                }
                write_value(out, item)?;
            }
            out.push(']');
        }
        PyValue::Dict(dict) => {
            let mut items: Vec<(&str, &PyValue)> = Vec::with_capacity(dict.len());
            for (key, item) in dict.iter() {
                let PyValue::Str(key) = key else {
                    return Err(KernelError::internal(
                        "geometry hash: a dict key is not a str",
                    ));
                };
                items.push((key, item));
            }
            // Python sorts str keys by code point, which is UTF-8 byte order.
            items.sort_by(|a, b| a.0.cmp(b.0));
            out.push('{');
            for (index, (key, item)) in items.iter().enumerate() {
                if index > 0 {
                    out.push(',');
                }
                write_string(out, key);
                out.push(':');
                write_value(out, item)?;
            }
            out.push('}');
        }
        other => {
            return Err(KernelError::internal(format!(
                "geometry hash: no JSON form for a {}",
                other.type_name()
            )));
        }
    }
    Ok(())
}

//! The decoded-matrix golden of `tools/parity/emit_ibt_golden.py`, computed from a native
//! decode, so the two can be compared field by field (port phase 1 gate).
//!
//! `matrix_sha256` hashes, per variable in order: the name's UTF-8, `<iii` (type code, count,
//! sample count), then every value re-packed as Python packs it: `<?` for bool, `<i`, `<I`,
//! `<f` of the widened float, `<d`; text as `<i` byte length and UTF-8 bytes.

use serde::Serialize;
use sha2::{Digest, Sha256};

use crate::DecodeError;
use crate::decode::{Decoded, Element};

/// `struct.pack("<f", float(x))` for a stored f32 `x`: widening and narrowing return the same
/// bits, except that the hardware conversion quiets a signalling NaN on the way.
fn python_f32_bits(value: f32) -> u32 {
    let bits = value.to_bits();
    if value.is_nan() {
        bits | 0x0040_0000
    } else {
        bits
    }
}

pub fn matrix_sha256(decoded: &Decoded) -> String {
    let mut digest = Sha256::new();
    let samples = decoded.sample_indices.len();
    for column in &decoded.columns {
        digest.update(column.name.as_bytes());
        digest.update((column.var_type as i32).to_le_bytes());
        digest.update((column.count as i32).to_le_bytes());
        digest.update((samples as i32).to_le_bytes());
        match column.element {
            Element::Utf8 => {
                for index in 0..samples {
                    let text = column.text(index).unwrap_or_default();
                    digest.update((text.len() as i32).to_le_bytes());
                    digest.update(text.as_bytes());
                }
            }
            Element::F32 => {
                for word in column.data.as_chunks::<4>().0 {
                    let value = f32::from_le_bytes(*word);
                    digest.update(python_f32_bits(value).to_le_bytes());
                }
            }
            // bool is stored 0/1 (`<?` packs True as 1); the other types are stored as packed.
            Element::Bool | Element::I32 | Element::U32 | Element::F64 => {
                digest.update(&column.data);
            }
        }
    }
    digest
        .finalize()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct GoldenVariable {
    pub name: String,
    pub type_code: i32,
    pub count: u32,
    pub offset: i32,
    pub unit: String,
}

/// `emit_ibt_golden.emit()` without `spot_values`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Golden {
    pub target_hz: Option<f64>,
    pub native_tick_rate_hz: i32,
    pub sample_rate_hz: f64,
    pub source_record_count: u32,
    pub sample_count: usize,
    pub sample_indices: Vec<u32>,
    pub variables: Vec<GoldenVariable>,
    pub matrix_sha256: String,
}

/// The rejection `coach-analysis golden` prints on standard output when the decode fails, so
/// the gate can compare it with the oracle's exception by category, never merely "both
/// failed": the Python class (null when the oracle has none), the message, and for a
/// truncation the region and byte range Python's `IbtTruncatedError` text states.
pub fn rejection(error: &DecodeError) -> serde_json::Value {
    let truncated = match error {
        DecodeError::Ibt(coach_ibt::IbtError::Truncated {
            region,
            start,
            end,
            available,
        }) => {
            serde_json::json!({"region": region, "start": start, "end": end, "available": available})
        }
        _ => serde_json::Value::Null,
    };
    serde_json::json!({"error": {
        "class": error.python_class(),
        "message": error.to_string(),
        "truncated": truncated,
    }})
}

/// The golden of `decoded`; `units` gives each column's unit (the decode keeps none).
pub fn golden(decoded: &Decoded, units: &dyn Fn(&str) -> String) -> Golden {
    Golden {
        target_hz: decoded.target_hz,
        native_tick_rate_hz: decoded.source.tick_rate,
        sample_rate_hz: decoded.plan.output_rate,
        source_record_count: decoded.source.record_count,
        sample_count: decoded.sample_indices.len(),
        sample_indices: decoded.sample_indices.clone(),
        variables: decoded
            .columns
            .iter()
            .map(|column| GoldenVariable {
                name: column.name.clone(),
                type_code: column.var_type as i32,
                count: column.count as u32,
                offset: column.var_offset,
                unit: units(&column.name),
            })
            .collect(),
        matrix_sha256: matrix_sha256(decoded),
    }
}

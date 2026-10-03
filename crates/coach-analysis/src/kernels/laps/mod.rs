//! Unit U2: lap segmentation, summaries and disruption scans (`phase2-plan.md` section 2).
//!
//! Native replacements for `analysis_engine._lap_summaries` (with `_vehicle_dynamics_summary`,
//! `_dynamics_samples`, `_lap_conditions`, `_count_corrections`, `_absolute_summary`,
//! `_positive_increase`, `_racing_state`, `_on_track`, `_session_track_usage`),
//! `_service_events` and `_disrupted_sample_indices`. Each returns the oracle's Python objects:
//! types, float bits and dict insertion order. `_runs`, the tyre lifecycle and the damage
//! orchestration stay Python, so nothing here reproduces their mutations of laps and runs.
//!
//! The oracle evaluates per-sample predicates once per table and folds each lap's slice of
//! them in sample order; here each predicate is evaluated per sample inside the lap's fold,
//! which gives the same values in the same order without a column per predicate.

mod conditions;
mod disruption;
mod dynamics;
mod service;
mod summaries;

pub use disruption::{Epsilon, disrupted_sample_indices};
pub use service::service_events;
pub use summaries::lap_summaries;

use std::collections::HashSet;

use coach_numerics as num;

use super::dispatch::{Call, Entry};
use super::error::KernelError;
use super::session::Session;
use super::table::{SteeringBasis, Table};
use super::value::PyValue;

const LAP_SUMMARIES: &str = "analysis_engine._lap_summaries";
const SERVICE_EVENTS: &str = "analysis_engine._service_events";
const DISRUPTED_SAMPLE_INDICES: &str = "analysis_engine._disrupted_sample_indices";

/// The oracle entry points this unit replaces (`dispatch::UNITS`).
pub const ENTRIES: &[Entry] = &[
    Entry {
        name: LAP_SUMMARIES,
        run: run_lap_summaries,
    },
    Entry {
        name: SERVICE_EVENTS,
        run: run_service_events,
    },
    Entry {
        name: DISRUPTED_SAMPLE_INDICES,
        run: run_disrupted_sample_indices,
    },
];

/// `_lap_summaries(table)`. The steering basis is the one Python's `_steering_basis(table)`
/// cached before the call (the session's).
fn run_lap_summaries(session: &Session, call: &mut Call) -> Result<PyValue, KernelError> {
    let bound = call.bind(LAP_SUMMARIES, &["table"], &[])?;
    bound.require_table(call, "table")?;
    let table = Table::of(session)?;
    let basis = SteeringBasis::from_value(session.steering_basis())?;
    lap_summaries(&table, &basis)
}

/// `_service_events(table)`.
fn run_service_events(session: &Session, call: &mut Call) -> Result<PyValue, KernelError> {
    let bound = call.bind(SERVICE_EVENTS, &["table"], &[])?;
    bound.require_table(call, "table")?;
    let table = Table::of(session)?;
    service_events(&table)
}

/// `_disrupted_sample_indices(table, timer_epsilon_s, *, mandatory_recorded,
/// optional_recorded, session_flags_channel, fast_increment_indices)`: every parameter is
/// required (none has a default).
fn run_disrupted_sample_indices(
    session: &Session,
    call: &mut Call,
) -> Result<PyValue, KernelError> {
    let entry = DISRUPTED_SAMPLE_INDICES;
    let bound = call.bind(
        entry,
        &["table", "timer_epsilon_s"],
        &[
            "mandatory_recorded",
            "optional_recorded",
            "session_flags_channel",
            "fast_increment_indices",
        ],
    )?;
    bound.require_table(call, "table")?;
    let unsupported = |name: &str, value: &PyValue| {
        KernelError::internal(format!("{entry}: {name} is a {}", value.type_name()))
    };
    let epsilon = match bound.require(call, "timer_epsilon_s")? {
        PyValue::Float(x) => Epsilon::Float(*x),
        PyValue::Int(i) => Epsilon::Int(*i),
        PyValue::Bool(b) => Epsilon::Int(i64::from(*b)),
        other => return Err(unsupported("timer_epsilon_s", other)),
    };
    let mandatory = bound.require(call, "mandatory_recorded")?.truthy()?;
    let optional = bound.require(call, "optional_recorded")?.truthy()?;
    let channel = match bound.require(call, "session_flags_channel")? {
        PyValue::Str(name) => Some(name.as_str()),
        PyValue::None => None,
        other if !other.truthy()? => None,
        other => return Err(unsupported("session_flags_channel", other)),
    };
    let fast = match bound.require(call, "fast_increment_indices")? {
        PyValue::Set(items) | PyValue::List(items) | PyValue::Tuple(items) => index_members(items),
        other => return Err(unsupported("fast_increment_indices", other)),
    };
    let table = Table::of(session)?;
    disrupted_sample_indices(&table, epsilon, mandatory, optional, channel, &fast)
}

/// The sample indices `index in items` matches: ints, bools and integral floats; any other
/// member equals no index.
fn index_members(items: &[PyValue]) -> HashSet<i64> {
    items
        .iter()
        .filter_map(|item| match item {
            PyValue::Int(i) => Some(*i),
            PyValue::Bool(b) => Some(i64::from(*b)),
            PyValue::Float(x) if x.fract() == 0.0 && x.abs() < 9.2e18 => Some(*x as i64),
            _ => None,
        })
        .collect()
}

/// `_round(value, digits)`: `round(value, digits)` of a finite number, else `None`.
fn round(value: Option<f64>, digits: i64) -> Result<PyValue, KernelError> {
    match value {
        Some(x) if x.is_finite() => Ok(PyValue::Float(num::round_ndigits(x, digits)?)),
        _ => Ok(PyValue::None),
    }
}

/// `_round(value)` (three digits).
fn round3(value: Option<f64>) -> Result<PyValue, KernelError> {
    round(value, 3)
}

/// `_round` of a builtin `sum` result: an int (`sum` of nothing) is `float(int)` first.
fn round_num(value: num::Num, digits: i64) -> Result<PyValue, KernelError> {
    round(Some(value.to_f64()), digits)
}

/// `_finite_values(values)`: the finite numbers, in order.
fn finite_values(values: &[f64]) -> Vec<f64> {
    values.iter().copied().filter(|x| x.is_finite()).collect()
}

/// `_mean(values)` of floats: `statistics.fmean` of the finite ones, `None` when there are none.
fn mean(values: &[f64]) -> Result<Option<f64>, KernelError> {
    if values.iter().all(|x| x.is_finite()) {
        if values.is_empty() {
            return Ok(None);
        }
        return Ok(Some(num::fmean(values)?));
    }
    let numbers = finite_values(values);
    if numbers.is_empty() {
        Ok(None)
    } else {
        Ok(Some(num::fmean(&numbers)?))
    }
}

/// `_median(values)` of floats: `statistics.median` of the finite ones, `None` when there are none.
fn median(values: &[f64]) -> Result<Option<f64>, KernelError> {
    let numbers = finite_values(values);
    if numbers.is_empty() {
        Ok(None)
    } else {
        Ok(Some(num::median_floats(&numbers)?))
    }
}

/// `_percentile(values, percentile)`: `_percentile_of_sorted(sorted(_finite_values(values)))`.
fn percentile(values: &[f64], percentile: f64) -> Result<Option<f64>, KernelError> {
    percentile_of_sorted(
        &num::sorted_floats(&finite_values(values), false),
        percentile,
    )
}

/// `_percentile_of_sorted(numbers, percentile)` of finite numbers already sorted.
fn percentile_of_sorted(numbers: &[f64], percentile: f64) -> Result<Option<f64>, KernelError> {
    match numbers {
        [] => Ok(None),
        [only] => Ok(Some(*only)),
        _ => {
            // max(0.0, min(1.0, percentile)) * (len(numbers) - 1)
            let clamped = py_max2(0.0, py_min2(1.0, percentile));
            let position = clamped * (numbers.len() - 1) as f64;
            let lower = num::floor(position)?;
            let upper = num::ceil(position)?;
            let at = |index: i64| {
                usize::try_from(index)
                    .ok()
                    .and_then(|i| numbers.get(i))
                    .copied()
                    .ok_or_else(|| KernelError::internal("percentile position out of range"))
            };
            if lower == upper {
                return Ok(Some(at(lower)?));
            }
            let fraction = position - lower as f64;
            Ok(Some(at(lower)? * (1.0 - fraction) + at(upper)? * fraction))
        }
    }
}

/// `_median_of_sorted(numbers)` of a non-empty sorted sequence.
fn median_of_sorted(numbers: &[f64]) -> f64 {
    let count = numbers.len();
    if count % 2 == 1 {
        numbers[count / 2]
    } else {
        let middle = count / 2;
        (numbers[middle - 1] + numbers[middle]) / 2.0
    }
}

/// Builtin `min(a, b)`: `b` only when `b < a` (the first of equal values is kept).
fn py_min2(a: f64, b: f64) -> f64 {
    if b < a { b } else { a }
}

/// Builtin `max(a, b)`: `b` only when `b > a`.
fn py_max2(a: f64, b: f64) -> f64 {
    if b > a { b } else { a }
}

/// Builtin `min(values)` of a non-empty sequence of floats (the first of equal values).
fn py_min(values: &[f64]) -> Option<f64> {
    let (&first, rest) = values.split_first()?;
    Some(
        rest.iter()
            .fold(first, |best, &x| if x < best { x } else { best }),
    )
}

/// Builtin `max(values)` of a non-empty sequence of floats (the first of equal values).
fn py_max(values: &[f64]) -> Option<f64> {
    let (&first, rest) = values.split_first()?;
    Some(
        rest.iter()
            .fold(first, |best, &x| if x > best { x } else { best }),
    )
}

/// `int(x)` of a finite float as a Python int: `Int` within `i64`, otherwise its exact decimal
/// text (`BigInt`), as CPython's `int()` returns it.
fn int_value(x: f64) -> Result<PyValue, KernelError> {
    match num::int_from_float(x) {
        Ok(value) => Ok(PyValue::Int(value)),
        Err(error) if error.kind == num::PyErrorKind::IntBeyondI64 => {
            Ok(PyValue::BigInt(format!("{:.0}", x.trunc())))
        }
        Err(error) => Err(error.into()),
    }
}

/// Limbs of a two's-complement integer wide enough for `int(x)` of any finite float
/// (`|int(x)| < 2**1024`) with room for the sign.
const WIDE_LIMBS: usize = 17;

/// `int(x)` of a finite float in [`WIDE_LIMBS`] little-endian two's-complement limbs.
fn wide_int(x: f64) -> [u64; WIDE_LIMBS] {
    let mut limbs = [0u64; WIDE_LIMBS];
    let whole = x.trunc().abs();
    if whole >= 1.0 {
        let bits = whole.to_bits();
        let exponent = ((bits >> 52) & 0x7FF) as i64 - 1075;
        let mantissa = (bits & ((1 << 52) - 1)) | (1 << 52);
        if exponent < 0 {
            limbs[0] = mantissa >> -exponent;
        } else {
            let (word, shift) = ((exponent / 64) as usize, (exponent % 64) as u32);
            limbs[word] = mantissa << shift;
            if shift > 0 && word + 1 < WIDE_LIMBS {
                limbs[word + 1] = mantissa >> (64 - shift);
            }
        }
    }
    if x < 0.0 {
        let mut carry = true;
        for limb in &mut limbs {
            let (value, overflow) = (!*limb).overflowing_add(u64::from(carry));
            *limb = value;
            carry = overflow;
        }
    }
    limbs
}

/// `value |= int(x)` folded over finite floats from `0`, as the Python int it is: `Int` within
/// `i64`, otherwise its exact decimal text (`BigInt`).
fn int_or(values: &[f64]) -> PyValue {
    let mut limbs = [0u64; WIDE_LIMBS];
    for &x in values {
        for (limb, part) in limbs.iter_mut().zip(wide_int(x)) {
            *limb |= part;
        }
    }
    let negative = limbs[WIDE_LIMBS - 1] >> 63 == 1;
    let sign_word = if negative { u64::MAX } else { 0 };
    if limbs[1..].iter().all(|&limb| limb == sign_word) && (limbs[0] >> 63 == u64::from(negative)) {
        return PyValue::Int(limbs[0] as i64);
    }
    if negative {
        let mut carry = true;
        for limb in &mut limbs {
            let (value, overflow) = (!*limb).overflowing_add(u64::from(carry));
            *limb = value;
            carry = overflow;
        }
    }
    // Magnitude to decimal, nineteen digits at a time.
    const CHUNK: u64 = 10_000_000_000_000_000_000;
    let mut chunks = Vec::new();
    while limbs.iter().any(|&limb| limb != 0) {
        let mut remainder: u128 = 0;
        for limb in limbs.iter_mut().rev() {
            let current = (remainder << 64) | u128::from(*limb);
            *limb = (current / u128::from(CHUNK)) as u64;
            remainder = current % u128::from(CHUNK);
        }
        chunks.push(remainder as u64);
    }
    let mut text = String::from(if negative { "-" } else { "" });
    for (index, chunk) in chunks.iter().rev().enumerate() {
        if index == 0 {
            text.push_str(&chunk.to_string());
        } else {
            text.push_str(&format!("{chunk:019}"));
        }
    }
    PyValue::BigInt(text)
}

/// `int(x)` of a finite float for index arithmetic: an int beyond `i64` saturates, which
/// compares and clamps exactly as the unbounded Python int does against sample indices.
fn int_saturating(x: f64) -> Result<i64, KernelError> {
    match num::int_from_float(x) {
        Ok(value) => Ok(value),
        Err(error) if error.kind == num::PyErrorKind::IntBeyondI64 => {
            Ok(if x > 0.0 { i64::MAX } else { i64::MIN })
        }
        Err(error) => Err(error.into()),
    }
}

/// `_finite(table.metadata.get("sample_rate") or table.metadata.get("tick_rate")) or 20.0`.
fn sample_rate(metadata: &PyValue) -> Result<f64, KernelError> {
    let chosen = match metadata.get("sample_rate") {
        Some(value) if value.truthy()? => Some(value),
        _ => metadata.get("tick_rate"),
    };
    let rate = match chosen {
        Some(value) => value.finite()?,
        None => None,
    };
    Ok(match rate {
        Some(x) if x != 0.0 => x,
        _ => 20.0,
    })
}

/// Python `str.isspace` for one character: the ASCII separators `\x1c`-`\x1f` are whitespace
/// to Python, not to Rust's `char::is_whitespace`.
fn py_space(c: char) -> bool {
    c.is_whitespace() || ('\x1c'..='\x1f').contains(&c)
}

/// Python `str.strip()`.
fn py_strip(text: &str) -> &str {
    text.trim_matches(py_space)
}

/// Python `str(value)` for the scalars session info holds.
fn py_str(value: &PyValue) -> Result<String, KernelError> {
    Ok(match value {
        PyValue::None => "None".to_owned(),
        PyValue::Bool(true) => "True".to_owned(),
        PyValue::Bool(false) => "False".to_owned(),
        PyValue::Int(i) => i.to_string(),
        PyValue::BigInt(text) => text.clone(),
        PyValue::Float(x) => num::float_repr(*x),
        PyValue::Str(text) => text.clone(),
        other => {
            return Err(KernelError::internal(format!(
                "str() of a {} in session info is not ported",
                other.type_name()
            )));
        }
    })
}

/// Python `repr(text)` (the message of a `KeyError` on a `str` key).
fn py_repr_str(text: &str) -> String {
    let quote = if text.contains('\'') && !text.contains('"') {
        '"'
    } else {
        '\''
    };
    let mut out = String::with_capacity(text.len() + 2);
    out.push(quote);
    for c in text.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            c if (c as u32) < 0x20 || c as u32 == 0x7f => {
                out.push_str(&format!("\\x{:02x}", c as u32));
            }
            c if !py_printable(c) => {
                let code = c as u32;
                if code <= 0xff {
                    out.push_str(&format!("\\x{code:02x}"));
                } else if code <= 0xffff {
                    out.push_str(&format!("\\u{code:04x}"));
                } else {
                    out.push_str(&format!("\\U{code:08x}"));
                }
            }
            c => out.push(c),
        }
    }
    out.push(quote);
    out
}

/// `str.isprintable` for `repr`, from CPython's own table (`coach_numerics::unicode`).
fn py_printable(c: char) -> bool {
    coach_numerics::unicode::is_printable(c)
}

/// `_path_get(value, *keys)`.
fn path_get<'a>(value: &'a PyValue, keys: &[&str]) -> Option<&'a PyValue> {
    let mut current = value;
    for key in keys {
        current = current.as_dict()?.get(key)?;
    }
    Some(current)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn percentile_matches_the_oracle_formula() {
        let numbers = [1.0, 2.0, 4.0, 8.0];
        // position 0.05 * 3 = 0.15: 1.0 * 0.85 + 2.0 * 0.15
        assert_eq!(
            percentile_of_sorted(&numbers, 0.05).unwrap(),
            Some(1.0 * (1.0 - 0.15000000000000002) + 2.0 * 0.15000000000000002)
        );
        assert_eq!(percentile_of_sorted(&[3.0], 0.95).unwrap(), Some(3.0));
        assert_eq!(percentile_of_sorted(&[], 0.95).unwrap(), None);
    }

    #[test]
    fn min_and_max_keep_the_first_of_equal_values() {
        assert!(py_min(&[0.0, -0.0]).unwrap().is_sign_positive());
        assert!(py_min(&[-0.0, 0.0]).unwrap().is_sign_negative());
        assert!(py_max(&[-0.0, 0.0]).unwrap().is_sign_negative());
        assert!(py_max2(0.0, -0.0).is_sign_positive());
    }

    #[test]
    fn big_ints_keep_their_exact_decimal_text() {
        assert!(matches!(int_value(2.7).unwrap(), PyValue::Int(2)));
        assert!(matches!(int_value(-0.0).unwrap(), PyValue::Int(0)));
        match int_value(1e19).unwrap() {
            PyValue::BigInt(text) => assert_eq!(text, "10000000000000000000"),
            other => panic!("{other:?}"),
        }
        assert_eq!(int_saturating(1e300).unwrap(), i64::MAX);
    }

    #[test]
    fn strip_and_repr_follow_python() {
        assert_eq!(py_strip("\x1c a \u{3000}"), "a");
        assert_eq!(py_repr_str("Lap"), "'Lap'");
        assert_eq!(py_repr_str("it's"), "\"it's\"");
        assert_eq!(py_repr_str("a\tb"), "'a\\tb'");
    }
}

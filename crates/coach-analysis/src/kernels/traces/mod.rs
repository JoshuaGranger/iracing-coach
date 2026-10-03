//! Unit U3: the distance-domain trace payload and the track profile
//! (`docs/08-rewrite/port/phase2-plan.md` section 2, U3).
//!
//! Entry points: `analysis_engine._lap_trace_payload` ([`payload`], with
//! `_additional_trace_sources`, `_present_values`, `_binary_column` and `_sideslip_column` in
//! [`signals`]) and `analysis_engine._track_profile` ([`profile`]). Both read the table's
//! normalised views (unit U1) and return the oracle's dicts in its key order.
//!
//! Per-bin work streams over sample indices: each bin gathers the present values of one column
//! into a reused scratch buffer in sample order, which is the order `_present_values` and the
//! oracle's per-bin lists hold them in, and reduces them with the oracle's reducer.

mod payload;
mod profile;
mod signals;

use std::rc::Rc;

use super::dispatch::{Call, Entry};
use super::error::KernelError;
use super::session::Session;
use super::table::{SetupSeries, SteeringBasis, Table, View};
use super::value::PyValue;

pub use payload::lap_trace_payload;
pub use profile::{SetupTrace, setup_traces, track_profile};

const LAP_TRACE_PAYLOAD: &str = "analysis_engine._lap_trace_payload";
const TRACK_PROFILE: &str = "analysis_engine._track_profile";

/// The oracle entry points this unit replaces (`dispatch::UNITS`).
pub const ENTRIES: &[Entry] = &[
    Entry {
        name: LAP_TRACE_PAYLOAD,
        run: run_lap_trace_payload,
    },
    Entry {
        name: TRACK_PROFILE,
        run: run_track_profile,
    },
];

/// An optional `int` parameter with the oracle's default.
fn int_parameter(
    entry: &str,
    name: &str,
    value: Option<&PyValue>,
    default: i64,
) -> Result<i64, KernelError> {
    match value {
        None => Ok(default),
        Some(PyValue::Int(number)) => Ok(*number),
        Some(other) => Err(KernelError::internal(format!(
            "{entry}: {name} is a {}, not an int",
            other.type_name()
        ))),
    }
}

/// The recording's steering basis as `_steering_basis(table)` caches it (Python computes it and
/// sends it with the table).
fn table_basis(session: &Session) -> Result<SteeringBasis, KernelError> {
    SteeringBasis::from_value(session.steering_basis())
}

/// `_lap_trace_payload(table, laps, *, maximum_bins=160, steering_basis=None)`.
fn run_lap_trace_payload(session: &Session, call: &mut Call) -> Result<PyValue, KernelError> {
    let bound = call.bind(
        LAP_TRACE_PAYLOAD,
        &["table", "laps"],
        &["maximum_bins", "steering_basis"],
    )?;
    bound.require_table(call, "table")?;
    let laps = match bound.require(call, "laps")? {
        PyValue::List(items) | PyValue::Tuple(items) => items.as_slice(),
        other => {
            return Err(KernelError::internal(format!(
                "{LAP_TRACE_PAYLOAD}: laps is a {}, not a list",
                other.type_name()
            )));
        }
    };
    let maximum_bins = int_parameter(
        LAP_TRACE_PAYLOAD,
        "maximum_bins",
        bound.get(call, "maximum_bins"),
        160,
    )?;
    // steering_basis or _steering_basis(table)
    let basis = match bound.get(call, "steering_basis") {
        Some(value) if value.truthy()? => SteeringBasis::from_value(value)?,
        _ => table_basis(session)?,
    };
    let table = Table::of(session)?;
    let result = lap_trace_payload(&table, laps, maximum_bins, &basis);
    table.check()?;
    result
}

/// `_track_profile(table, bins=200, setup_series=None)`.
fn run_track_profile(session: &Session, call: &mut Call) -> Result<PyValue, KernelError> {
    let bound = call.bind(TRACK_PROFILE, &["table", "bins", "setup_series"], &[])?;
    bound.require_table(call, "table")?;
    let bins = int_parameter(TRACK_PROFILE, "bins", bound.get(call, "bins"), 200)?;
    let series = bound.get(call, "setup_series");
    let table = Table::of(session)?;
    // _steering_basis(table) and the setup series are read where the oracle reads them, after
    // the early return for a table without lap distance.
    let basis = || table_basis(session);
    // setup_series or {}: a dict by value (the vectors) or the handle of U1's SetupSeries.
    let setup = || -> Result<Vec<SetupTrace>, KernelError> {
        match series {
            Some(handle @ PyValue::Handle(_)) => {
                Ok(setup_traces(&*session.handle::<SetupSeries>(handle)?))
            }
            Some(value) if value.truthy()? => Ok(setup_traces(&SetupSeries::from_value(value)?)),
            _ => Ok(Vec::new()),
        }
    };
    let result = track_profile(&table, bins, &basis, &setup);
    table.check()?;
    result
}

/// One normalised per-sample column: a table view, or a column this unit derives.
///
/// A derived column stores `None` as NaN: every derivation here maps a finite number to a
/// number that is never NaN (products and quotients of finite numbers by finite non-zero
/// constants, `atan2` of finite numbers, absolute values), so NaN is free to mean "missing".
#[derive(Clone)]
pub(crate) enum Col {
    View(Rc<View>),
    Derived(Rc<Vec<f64>>),
}

impl Col {
    #[inline]
    pub(crate) fn at(&self, index: usize) -> Option<f64> {
        match self {
            Col::View(view) => view.get(index),
            Col::Derived(values) => {
                let value = values[index];
                (!value.is_nan()).then_some(value)
            }
        }
    }

    pub(crate) fn len(&self) -> usize {
        match self {
            Col::View(view) => view.len(),
            Col::Derived(values) => values.len(),
        }
    }

    pub(crate) fn is_empty(&self) -> bool {
        self.len() == 0
    }

    /// `[f(n) for n in column if n is not None]` kept in place: `None` stays `None`.
    pub(crate) fn map(&self, f: impl Fn(f64) -> f64) -> Col {
        Col::Derived(Rc::new(
            (0..self.len())
                .map(|index| self.at(index).map_or(f64::NAN, &f))
                .collect(),
        ))
    }

    /// Whether every sample is `None`.
    pub(crate) fn all_missing(&self) -> bool {
        (0..self.len()).all(|index| self.at(index).is_none())
    }

    /// Push the present values at `indices` (ascending) onto `out`, in sample order:
    /// `_present_values` and `[v for i in indices if (v := column[i]) is not None]`.
    #[inline]
    pub(crate) fn gather(&self, indices: &[u32], out: &mut Vec<f64>) {
        out.clear();
        out.extend(indices.iter().filter_map(|&index| self.at(index as usize)));
    }
}

/// `_finite_after_transform` / `_normalize_column` of a derived column: a non-finite value
/// becomes `None` (the oracle's `sum` shortcut only skips this check when it cannot change
/// anything).
pub(crate) fn finite_after_transform(col: Col) -> Col {
    match col {
        Col::Derived(values) if values.iter().any(|value| value.is_infinite()) => {
            Col::Derived(Rc::new(
                values
                    .iter()
                    .map(|&value| if value.is_finite() { value } else { f64::NAN })
                    .collect(),
            ))
        }
        other => other,
    }
}

/// `max(items)`: the first of equal maxima (`-0.0` before `0.0` stays `-0.0`).
pub(crate) fn first_max(items: &[f64]) -> Option<f64> {
    let (&first, rest) = items.split_first()?;
    Some(
        rest.iter()
            .fold(first, |best, &item| if item > best { item } else { best }),
    )
}

/// `min(items)`: the first of equal minima.
pub(crate) fn first_min(items: &[f64]) -> Option<f64> {
    let (&first, rest) = items.split_first()?;
    Some(
        rest.iter()
            .fold(first, |best, &item| if item < best { item } else { best }),
    )
}

/// `max(items, key=abs)`: the first item of the largest magnitude (`x` before `-x`).
pub(crate) fn signed_peak(items: &[f64]) -> Option<f64> {
    let (&first, rest) = items.split_first()?;
    Some(rest.iter().fold(
        first,
        |best, &item| {
            if item.abs() > best.abs() { item } else { best }
        },
    ))
}

/// `list.sort()` of floats in place. Without NaN the order is total up to equal values, so
/// any stable sort gives CPython's result (`-0.0` and `0.0` keep their sample order); with a
/// NaN the comparison sequence decides, and `coach_numerics::sorted_floats` repeats it.
pub(crate) fn sort_floats(items: &mut [f64]) {
    if items.iter().any(|item| item.is_nan()) {
        let sorted = coach_numerics::sorted_floats(items, false);
        items.copy_from_slice(&sorted);
    } else {
        items.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    }
}

/// `statistics.median` of a non-empty list: a stable sort, then the middle item or the mean
/// of the middle two (`_median_of_sorted`). Sorts `items` in place.
pub(crate) fn median_in_place(items: &mut [f64]) -> Option<f64> {
    if items.is_empty() {
        return None;
    }
    sort_floats(items);
    Some(median_of_sorted(items))
}

/// `_median_of_sorted`.
pub(crate) fn median_of_sorted(items: &[f64]) -> f64 {
    let count = items.len();
    let middle = count / 2;
    if count % 2 == 1 {
        items[middle]
    } else {
        (items[middle - 1] + items[middle]) / 2.0
    }
}

/// `_percentile_of_sorted(numbers, percentile)`.
pub(crate) fn percentile_of_sorted(
    items: &[f64],
    percentile: f64,
) -> Result<Option<f64>, KernelError> {
    match items.len() {
        0 => return Ok(None),
        1 => return Ok(Some(items[0])),
        _ => {}
    }
    // max(0.0, min(1.0, percentile)): min keeps 1.0 unless percentile < 1.0, max keeps 0.0
    // unless the result > 0.0.
    let clamped = if percentile < 1.0 { percentile } else { 1.0 };
    let clamped = if clamped > 0.0 { clamped } else { 0.0 };
    let position = clamped * (items.len() - 1) as f64;
    let lower = coach_numerics::floor(position)?;
    let upper = coach_numerics::ceil(position)?;
    let at = |index: i64| -> Result<f64, KernelError> {
        usize::try_from(index)
            .ok()
            .and_then(|index| items.get(index).copied())
            .ok_or_else(|| KernelError::raises("IndexError", "list index out of range"))
    };
    if lower == upper {
        return at(lower).map(Some);
    }
    let fraction = position - lower as f64;
    Ok(Some(at(lower)? * (1.0 - fraction) + at(upper)? * fraction))
}

/// `_finite_values` of numbers that are already floats: the finite ones, in order.
pub(crate) fn finite_only(items: &mut Vec<f64>) {
    if items.iter().any(|item| !item.is_finite()) {
        items.retain(|item| item.is_finite());
    }
}

/// `_round(value, digits)` of a float or `None`: `None` unless finite, else `round(value,
/// digits)`.
pub(crate) fn round_opt(value: Option<f64>, digits: i64) -> Result<PyValue, KernelError> {
    match value {
        Some(number) if number.is_finite() => Ok(PyValue::Float(coach_numerics::round_ndigits(
            number, digits,
        )?)),
        _ => Ok(PyValue::None),
    }
}

/// `x or 0.0` for an optional float: `None`, `0.0` and `-0.0` become `0.0`.
#[inline]
pub(crate) fn or_zero(value: Option<f64>) -> f64 {
    match value {
        Some(number) if number != 0.0 => number,
        _ => 0.0,
    }
}

/// `PyValue::Dict` from string keys in order.
pub(crate) fn dict<const N: usize>(items: [(&str, PyValue); N]) -> PyValue {
    let mut dict = super::value::Dict::with_capacity(N);
    for (key, value) in items {
        dict.insert(key, value);
    }
    PyValue::Dict(dict)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn bits(value: Option<f64>) -> Option<u64> {
        value.map(f64::to_bits)
    }

    // Expected values are CPython 3.12's for the same expressions.
    #[test]
    fn reducers_keep_the_first_of_equal_items() {
        assert_eq!(bits(first_max(&[-0.0, 0.0])), bits(Some(-0.0)));
        assert_eq!(bits(first_max(&[0.0, -0.0])), bits(Some(0.0)));
        assert_eq!(bits(first_min(&[0.0, -0.0])), bits(Some(0.0)));
        assert_eq!(signed_peak(&[3.0, -3.0]), Some(3.0));
        assert_eq!(signed_peak(&[-3.0, 3.0]), Some(-3.0));
        assert_eq!(signed_peak(&[]), None);
    }

    #[test]
    fn median_sorts_stably() {
        assert_eq!(bits(median_in_place(&mut [0.0, -0.0])), bits(Some(0.0)));
        assert_eq!(
            bits(median_in_place(&mut [-0.0, 0.0, -0.0])),
            bits(Some(0.0))
        );
        assert_eq!(
            bits(median_in_place(&mut [0.0, -0.0, 5.0])),
            bits(Some(-0.0))
        );
        assert_eq!(median_in_place(&mut []), None);
    }

    #[test]
    fn percentile_interpolates_between_neighbours() {
        assert_eq!(
            percentile_of_sorted(&[1.0, 2.0, 3.0, 4.0], 0.05)
                .ok()
                .flatten(),
            Some(1.15)
        );
        assert_eq!(percentile_of_sorted(&[7.0], 0.9).ok().flatten(), Some(7.0));
        assert_eq!(percentile_of_sorted(&[], 0.5).ok().flatten(), None);
    }
}

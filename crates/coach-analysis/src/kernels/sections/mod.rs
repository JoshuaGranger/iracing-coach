//! Unit U4: sections and clean-lap screening (`docs/08-rewrite/port/phase2-plan.md` section 2).
//!
//! Entry points: `analysis_engine._corner_tire_age_summary` ([`corner_age`], with the lap
//! screen, tyre-age context and run phases in [`phases`] and `_zone_lap_metric` in
//! [`zone_metric`]), `analysis_engine._pace_attribution` and `analysis_engine._clean_lap_traces`
//! ([`time_loss`] and [`lap_reference`], with [`tire_energy`]), and
//! `pit_loss.measure_pit_loss` ([`pit_loss`]). Each returns the oracle's Python objects: types,
//! float bits, dict insertion order; `LapTrace` and `PitLossReport` as the dataclasses the
//! codec rebuilds. `_strategy_planning` and `strategy_model` stay Python.
//!
//! Per-sample reads go through [`Seq`]: a table column read raw, exactly as the oracle indexes
//! `table.get(...)` (`_finite`, `_bool` and `lap_reference._finite` per sample, not the cached
//! views), or a list argument.

pub mod corner_age;
pub mod lap_reference;
pub mod phases;
pub mod pit_loss;
pub mod time_loss;
pub mod tire_energy;
pub mod zone_metric;

use super::dispatch::Entry;
use super::error::KernelError;
use super::table::{Raw, Table, View};
use super::value::PyValue;

pub(crate) type KResult<T> = Result<T, KernelError>;

/// The oracle entry points this unit replaces (`dispatch::UNITS`).
pub const ENTRIES: &[Entry] = &[
    Entry {
        name: "analysis_engine._corner_tire_age_summary",
        run: corner_age::corner_tire_age_summary_entry,
    },
    Entry {
        name: "analysis_engine._pace_attribution",
        run: time_loss::pace_attribution_entry,
    },
    Entry {
        name: "analysis_engine._clean_lap_traces",
        run: lap_reference::clean_lap_traces_entry,
    },
    Entry {
        name: "pit_loss.measure_pit_loss",
        run: pit_loss::measure_pit_loss_entry,
    },
];

/// One per-sample value as Python holds it in a column or list.
#[derive(Clone, Copy, Debug)]
pub enum Cell<'a> {
    None,
    Bool(bool),
    Int(i64),
    Float(f64),
    Str(&'a str),
    /// An array sample (a Python list): `float()` fails, truthy when non-empty.
    Container(bool),
    /// Anything else (a container argument, a big int).
    Value(&'a PyValue),
}

impl<'a> Cell<'a> {
    pub fn of(value: &'a PyValue) -> Cell<'a> {
        match value {
            PyValue::None => Cell::None,
            PyValue::Bool(b) => Cell::Bool(*b),
            PyValue::Int(i) => Cell::Int(*i),
            PyValue::Float(x) => Cell::Float(*x),
            PyValue::Str(s) => Cell::Str(s),
            other => Cell::Value(other),
        }
    }
}

/// A sequence the oracle indexes per sample: a table column or a list argument.
pub trait Seq {
    fn len(&self) -> usize;
    fn is_empty(&self) -> bool {
        self.len() == 0
    }
    /// Item `index` (`index < len()`).
    fn cell(&self, index: usize) -> Cell<'_>;
}

impl Seq for &[PyValue] {
    fn len(&self) -> usize {
        <[PyValue]>::len(self)
    }

    fn cell(&self, index: usize) -> Cell<'_> {
        Cell::of(&self[index])
    }
}

impl Seq for Vec<f64> {
    fn len(&self) -> usize {
        Vec::len(self)
    }

    fn cell(&self, index: usize) -> Cell<'_> {
        Cell::Float(self[index])
    }
}

impl Seq for Vec<i64> {
    fn len(&self) -> usize {
        Vec::len(self)
    }

    fn cell(&self, index: usize) -> Cell<'_> {
        Cell::Int(self[index])
    }
}

impl Seq for View {
    fn len(&self) -> usize {
        View::len(self)
    }

    fn cell(&self, index: usize) -> Cell<'_> {
        self.get(index).map_or(Cell::None, Cell::Float)
    }
}

/// A raw table column, sample by sample as `table.get(...)` holds it.
impl Seq for Raw<'_> {
    fn len(&self) -> usize {
        Raw::len(self)
    }

    fn cell(&self, index: usize) -> Cell<'_> {
        if !self.is_present(index) {
            return Cell::None;
        }
        match self {
            Raw::Bool(v, _) => Cell::Bool(v[index] != 0),
            Raw::I32(v, _) => Cell::Int(i64::from(v[index])),
            Raw::U32(v, _) => Cell::Int(i64::from(v[index])),
            Raw::F32(v, _) => Cell::Float(f64::from(v[index])),
            Raw::F64(v, _) => Cell::Float(v[index]),
            Raw::Text(v, _) => Cell::Str(v.get(index)),
            Raw::Array(v, _) => Cell::Container(v.count > 0),
            Raw::Repeat(value, _) => Cell::of(value),
        }
    }
}

/// A sequence argument: a table column by reference or a list.
pub enum SeqArg<'a> {
    Column(Raw<'a>),
    Items(&'a [PyValue]),
}

impl SeqArg<'_> {
    pub fn as_seq(&self) -> &dyn Seq {
        match self {
            SeqArg::Column(raw) => raw,
            SeqArg::Items(items) => items,
        }
    }
}

/// A sequence argument resolved against the table: `{"$table_column": name}` reads the raw
/// column without recording an access (the caller's `table.get` recorded it), a list or tuple
/// is itself.
pub fn resolve_seq<'a>(table: &'a Table, value: &'a PyValue) -> KResult<SeqArg<'a>> {
    match value {
        PyValue::TableColumn(name) => Ok(SeqArg::Column(
            table.raw(&super::table::ColumnKey::Channel(name.clone()))?,
        )),
        PyValue::List(items) | PyValue::Tuple(items) => Ok(SeqArg::Items(items)),
        other => Err(KernelError::internal(format!(
            "a {} where a sequence is expected",
            other.type_name()
        ))),
    }
}

/// `seq[index]` for a Python int index: negative counts from the end; out of range is the
/// oracle's `IndexError`.
pub fn at(seq: &dyn Seq, index: i64) -> KResult<Cell<'_>> {
    let length = seq.len() as i64;
    let position = if index < 0 { index + length } else { index };
    if position < 0 || position >= length {
        return Err(KernelError::raises("IndexError", "list index out of range"));
    }
    Ok(seq.cell(position as usize))
}

/// `float(text)` for `_finite`: `None` where Python raises `ValueError`.
fn parse_finite(text: &str) -> KResult<Option<f64>> {
    match coach_numerics::parse_float(text) {
        Ok(number) => Ok(number.is_finite().then_some(number)),
        Err(error) if error.kind == coach_numerics::PyErrorKind::ValueError => Ok(None),
        Err(error) => Err(error.into()),
    }
}

/// `analysis_engine._finite(value)` of one sample.
pub fn finite(cell: Cell<'_>) -> KResult<Option<f64>> {
    match cell {
        Cell::Float(x) => Ok(x.is_finite().then_some(x)),
        Cell::None => Ok(None),
        Cell::Bool(b) => Ok(Some(f64::from(u8::from(b)))),
        Cell::Int(i) => Ok(Some(i as f64)),
        Cell::Str(text) => parse_finite(text),
        Cell::Container(_) => Ok(None),
        Cell::Value(value) => value.finite(),
    }
}

/// `analysis_engine._finite(value)` of an argument value.
pub fn finite_value(value: &PyValue) -> KResult<Option<f64>> {
    value.finite()
}

/// `lap_reference._finite(value)`: a `bool` is `None`; `float()` failures (`TypeError`,
/// `ValueError`, `OverflowError`) are `None`.
pub fn lr_finite(cell: Cell<'_>) -> KResult<Option<f64>> {
    match cell {
        Cell::Bool(_) | Cell::None | Cell::Container(_) => Ok(None),
        Cell::Float(x) => Ok(x.is_finite().then_some(x)),
        Cell::Int(i) => Ok(Some(i as f64)),
        Cell::Str(text) => parse_finite(text),
        Cell::Value(PyValue::Bool(_)) => Ok(None),
        Cell::Value(value) => match value.finite() {
            Err(KernelError::Raises { class, .. }) if class == "OverflowError" => Ok(None),
            other => other,
        },
    }
}

/// `lap_reference._finite(value)` of an argument value.
pub fn lr_finite_value(value: &PyValue) -> KResult<Option<f64>> {
    lr_finite(Cell::of(value))
}

/// `str.isspace()` for one character.
fn py_space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

/// `bool(value)` of one sample.
pub fn truthy(cell: Cell<'_>) -> KResult<bool> {
    Ok(match cell {
        Cell::None => false,
        Cell::Bool(b) => b,
        Cell::Int(i) => i != 0,
        Cell::Float(x) => x != 0.0,
        Cell::Str(s) => !s.is_empty(),
        Cell::Container(nonempty) => nonempty,
        Cell::Value(value) => value.truthy()?,
    })
}

/// `analysis_engine._bool(value)`: a `str` is `strip().lower() in {"1", "true", "yes", "on"}`.
pub fn py_bool(cell: Cell<'_>) -> KResult<bool> {
    match cell {
        Cell::Str(text) => {
            let stripped = text.trim_matches(py_space);
            // No non-ASCII character lower-cases into these words.
            Ok(matches!(
                stripped.to_ascii_lowercase().as_str(),
                "1" | "true" | "yes" | "on"
            ) && stripped.is_ascii())
        }
        other => truthy(other),
    }
}

/// `analysis_engine._fraction(value)`: `max(0.0, min(1.0, n))` after scaling percentages.
pub fn fraction(cell: Cell<'_>) -> KResult<Option<f64>> {
    let Some(mut number) = finite(cell)? else {
        return Ok(None);
    };
    if number.abs() > 1.5 {
        number /= 100.0;
    }
    let capped = if number < 1.0 { number } else { 1.0 };
    Ok(Some(if capped > 0.0 { capped } else { 0.0 }))
}

/// `round(x, digits)` of a float.
pub fn round_float(x: f64, digits: i64) -> KResult<f64> {
    Ok(coach_numerics::round_ndigits(x, digits)?)
}

/// `round(x, digits) if x is not None else None` (the dataclass payloads; no finite check).
pub fn round_opt(value: Option<f64>, digits: i64) -> KResult<PyValue> {
    value.map_or(Ok(PyValue::None), |x| {
        Ok(PyValue::Float(round_float(x, digits)?))
    })
}

/// `analysis_engine._round(value, digits)` of a float or `None`: `None` unless finite.
pub fn ae_round(value: Option<f64>, digits: i64) -> KResult<PyValue> {
    match value {
        Some(x) if x.is_finite() => Ok(PyValue::Float(round_float(x, digits)?)),
        _ => Ok(PyValue::None),
    }
}

/// `analysis_engine._round(value, digits)` of an argument value.
pub fn ae_round_value(value: &PyValue, digits: i64) -> KResult<PyValue> {
    ae_round(value.finite()?, digits)
}

/// `str(value)` for the scalar values the oracle formats; anything else is not accepted.
pub fn py_str(value: &PyValue) -> KResult<String> {
    Ok(match value {
        PyValue::Str(s) => s.clone(),
        PyValue::None => "None".to_owned(),
        PyValue::Bool(true) => "True".to_owned(),
        PyValue::Bool(false) => "False".to_owned(),
        PyValue::Int(i) => i.to_string(),
        PyValue::BigInt(text) => text.clone(),
        PyValue::Float(x) => coach_numerics::float_repr(*x),
        other => {
            return Err(KernelError::internal(format!(
                "str() of a {} is not supported here",
                other.type_name()
            )));
        }
    })
}

/// `int(value)` for the scalar values the oracle converts.
pub fn py_int(value: &PyValue) -> KResult<i64> {
    match value {
        PyValue::Bool(b) => Ok(i64::from(*b)),
        PyValue::Int(i) => Ok(*i),
        PyValue::Float(x) => Ok(coach_numerics::int_from_float(*x)?),
        PyValue::None => Err(KernelError::raises(
            "TypeError",
            "int() argument must be a string, a bytes-like object or a real number, not 'NoneType'",
        )),
        PyValue::Str(text) => match coach_numerics::parse_int(text) {
            Ok(number) => Ok(number),
            Err(error) if error.kind == coach_numerics::PyErrorKind::ValueError => {
                if text
                    .chars()
                    .all(|c| c.is_ascii_graphic() && c != '\'' && c != '\\' || c == ' ')
                {
                    Err(KernelError::raises(
                        "ValueError",
                        format!("invalid literal for int() with base 10: '{text}'"),
                    ))
                } else {
                    Err(KernelError::internal(
                        "int() of this text is not supported here",
                    ))
                }
            }
            Err(error) => Err(error.into()),
        },
        other => Err(KernelError::internal(format!(
            "int() of a {} is not supported here",
            other.type_name()
        ))),
    }
}

/// `int(x)` of a finite float (`int(_finite(...))`).
pub fn int_of(x: f64) -> KResult<i64> {
    Ok(coach_numerics::int_from_float(x)?)
}

/// `min(items)` of floats: the first of equal minima.
pub fn float_min(items: &[f64]) -> Option<f64> {
    let (&first, rest) = items.split_first()?;
    Some(
        rest.iter()
            .fold(first, |best, &item| if item < best { item } else { best }),
    )
}

/// `max(items)` of floats: the first of equal maxima.
pub fn float_max(items: &[f64]) -> Option<f64> {
    let (&first, rest) = items.split_first()?;
    Some(
        rest.iter()
            .fold(first, |best, &item| if item > best { item } else { best }),
    )
}

static NONE: PyValue = PyValue::None;

/// `mapping.get(key)` on a value the oracle treats as a mapping (`None` for a missing key).
pub fn get<'a>(mapping: &'a PyValue, key: &str) -> KResult<&'a PyValue> {
    match mapping {
        PyValue::Dict(dict) => Ok(dict.get(key).unwrap_or(&NONE)),
        other => Err(KernelError::internal(format!(
            "{key:?} read from a {}, not a dict",
            other.type_name()
        ))),
    }
}

/// `(value or {}).get(key)`: a falsy value reads as an empty dict.
pub fn get_or_empty<'a>(value: &'a PyValue, key: &str) -> KResult<&'a PyValue> {
    if value.truthy()? {
        get(value, key)
    } else {
        Ok(&NONE)
    }
}

/// The sort key `_finite(item.get("lap")) or -1.0` of a lap.
pub fn lap_sort_key(lap: &PyValue) -> KResult<f64> {
    match finite_value(get(lap, "lap")?)? {
        Some(x) if x != 0.0 => Ok(x),
        _ => Ok(-1.0),
    }
}

/// `sorted(laps, key=lambda item: _finite(item.get("lap")) or -1.0)` (stable).
pub fn sorted_laps(laps: &[PyValue]) -> KResult<Vec<&PyValue>> {
    let keys = laps
        .iter()
        .map(lap_sort_key)
        .collect::<KResult<Vec<f64>>>()?;
    let order = coach_numerics::sort_permutation(laps.len(), &mut |i, j| keys[i] < keys[j], false);
    Ok(order.into_iter().map(|index| &laps[index]).collect())
}

/// The list items of a `list` or `tuple` argument.
pub fn items<'a>(value: &'a PyValue, what: &str) -> KResult<&'a [PyValue]> {
    match value {
        PyValue::List(items) | PyValue::Tuple(items) => Ok(items),
        other => Err(KernelError::internal(format!(
            "{what} is a {}, not a list",
            other.type_name()
        ))),
    }
}

//! The Python values a kernel entry receives and returns: an ordered tree that keeps every
//! distinction `analysis.json` and the oracle's callers can observe (`int`, `bool` and `float`
//! apart, float bits, dict insertion order, tuples apart from lists).
//!
//! The codec (`codec.rs`, codec version 1 of `tools/kernel_codec.py`) reads and writes it;
//! [`same`] is the replay comparison.

use std::cmp::Ordering;
use std::fmt::Write as _;

use super::error::KernelError;

/// One Python value.
#[derive(Clone, Debug)]
pub enum PyValue {
    None,
    Bool(bool),
    Int(i64),
    /// An `int` outside `i64`, as its canonical decimal text (`{"$int": "..."}`).
    BigInt(String),
    Float(f64),
    Str(String),
    List(Vec<PyValue>),
    Tuple(Vec<PyValue>),
    /// A `set` or `frozenset`. The codec writes its elements in Python `sorted` order; build one
    /// with [`PyValue::set`], which sorts and removes duplicates as Python would.
    Set(Vec<PyValue>),
    Dict(Dict),
    Dataclass(Box<Dataclass>),
    /// The analysis `TelemetryTable` passed by reference (`{"$table": 1}`).
    Table,
    /// The table's `channels` mapping (`{"$table_channels": 1}`).
    TableChannels,
    /// One of the table's channel lists, by identity (`{"$table_column": name}`).
    TableColumn(String),
    /// A value the kernel process keeps between calls (`{"$handle": n}`, `Session::put_handle`).
    Handle(u64),
}

/// A dataclass instance: its qualified class (`module.Class`) and fields in declared order.
#[derive(Clone, Debug)]
pub struct Dataclass {
    pub class: String,
    pub fields: Vec<(String, PyValue)>,
}

impl Dataclass {
    pub fn field(&self, name: &str) -> Option<&PyValue> {
        self.fields
            .iter()
            .find(|(key, _)| key == name)
            .map(|(_, value)| value)
    }
}

/// A Python `dict`: insertion-ordered, keys compared as Python compares hashable keys
/// (`1`, `1.0` and `True` are one key).
#[derive(Clone, Debug, Default)]
pub struct Dict {
    items: Vec<(PyValue, PyValue)>,
}

impl Dict {
    pub fn new() -> Dict {
        Dict::default()
    }

    pub fn with_capacity(capacity: usize) -> Dict {
        Dict {
            items: Vec::with_capacity(capacity),
        }
    }

    pub fn len(&self) -> usize {
        self.items.len()
    }

    pub fn is_empty(&self) -> bool {
        self.items.is_empty()
    }

    pub fn iter(&self) -> impl Iterator<Item = (&PyValue, &PyValue)> {
        self.items.iter().map(|(key, value)| (key, value))
    }

    pub fn keys(&self) -> impl Iterator<Item = &PyValue> {
        self.items.iter().map(|(key, _)| key)
    }

    pub fn values(&self) -> impl Iterator<Item = &PyValue> {
        self.items.iter().map(|(_, value)| value)
    }

    pub fn into_items(self) -> Vec<(PyValue, PyValue)> {
        self.items
    }

    /// `d.get(key)` for a `str` key.
    pub fn get(&self, key: &str) -> Option<&PyValue> {
        self.items
            .iter()
            .find(|(k, _)| matches!(k, PyValue::Str(s) if s == key))
            .map(|(_, value)| value)
    }

    pub fn get_mut(&mut self, key: &str) -> Option<&mut PyValue> {
        self.items
            .iter_mut()
            .find(|(k, _)| matches!(k, PyValue::Str(s) if s == key))
            .map(|(_, value)| value)
    }

    pub fn contains(&self, key: &str) -> bool {
        self.get(key).is_some()
    }

    /// `d.get(key)` for any hashable key.
    pub fn get_key(&self, key: &PyValue) -> Option<&PyValue> {
        self.items
            .iter()
            .find(|(k, _)| key_eq(k, key))
            .map(|(_, value)| value)
    }

    /// `d[key] = value`: an existing key keeps its position (and its original key object, as
    /// Python keeps it) and takes the new value; the old value is returned.
    pub fn insert(
        &mut self,
        key: impl Into<PyValue>,
        value: impl Into<PyValue>,
    ) -> Option<PyValue> {
        let key = key.into();
        let value = value.into();
        match self.items.iter_mut().find(|(k, _)| key_eq(k, &key)) {
            Some((_, slot)) => Some(std::mem::replace(slot, value)),
            None => {
                self.items.push((key, value));
                None
            }
        }
    }

    /// Appends a key the caller knows is not present (an output dict built in the oracle's
    /// order without the linear search of [`Dict::insert`]); checked in debug builds.
    pub fn push_new(&mut self, key: impl Into<PyValue>, value: impl Into<PyValue>) {
        let key = key.into();
        debug_assert!(
            !self.items.iter().any(|(k, _)| key_eq(k, &key)),
            "push_new of a present key"
        );
        self.items.push((key, value.into()));
    }

    /// A dict of keys the caller has already proven distinct (the codec reader).
    pub(super) fn from_distinct(items: Vec<(PyValue, PyValue)>) -> Dict {
        Dict { items }
    }

    /// Builder form of [`Dict::insert`].
    pub fn with(mut self, key: impl Into<PyValue>, value: impl Into<PyValue>) -> Dict {
        self.insert(key, value);
        self
    }

    /// `d.pop(key, None)` for a `str` key; the other keys keep their order.
    pub fn remove(&mut self, key: &str) -> Option<PyValue> {
        let index = self
            .items
            .iter()
            .position(|(k, _)| matches!(k, PyValue::Str(s) if s == key))?;
        Some(self.items.remove(index).1)
    }
}

impl FromIterator<(PyValue, PyValue)> for Dict {
    fn from_iter<I: IntoIterator<Item = (PyValue, PyValue)>>(iter: I) -> Dict {
        let mut dict = Dict::new();
        for (key, value) in iter {
            dict.insert(key, value);
        }
        dict
    }
}

impl From<bool> for PyValue {
    fn from(value: bool) -> PyValue {
        PyValue::Bool(value)
    }
}

impl From<i64> for PyValue {
    fn from(value: i64) -> PyValue {
        PyValue::Int(value)
    }
}

impl From<f64> for PyValue {
    fn from(value: f64) -> PyValue {
        PyValue::Float(value)
    }
}

impl From<&str> for PyValue {
    fn from(value: &str) -> PyValue {
        PyValue::Str(value.to_owned())
    }
}

impl From<String> for PyValue {
    fn from(value: String) -> PyValue {
        PyValue::Str(value)
    }
}

impl From<Vec<PyValue>> for PyValue {
    fn from(value: Vec<PyValue>) -> PyValue {
        PyValue::List(value)
    }
}

impl From<Dict> for PyValue {
    fn from(value: Dict) -> PyValue {
        PyValue::Dict(value)
    }
}

impl<T: Into<PyValue>> From<Option<T>> for PyValue {
    fn from(value: Option<T>) -> PyValue {
        value.map_or(PyValue::None, Into::into)
    }
}

impl PyValue {
    /// The Python type name, for messages.
    pub fn type_name(&self) -> &'static str {
        match self {
            PyValue::None => "NoneType",
            PyValue::Bool(_) => "bool",
            PyValue::Int(_) | PyValue::BigInt(_) => "int",
            PyValue::Float(_) => "float",
            PyValue::Str(_) => "str",
            PyValue::List(_) => "list",
            PyValue::Tuple(_) => "tuple",
            PyValue::Set(_) => "set",
            PyValue::Dict(_) => "dict",
            PyValue::Dataclass(_) => "dataclass",
            PyValue::Table => "TelemetryTable",
            PyValue::TableChannels => "dict (table channels)",
            PyValue::TableColumn(_) => "list (table column)",
            PyValue::Handle(_) => "handle",
        }
    }

    /// `set(items)` written as the codec writes it: duplicates removed (the first of equal
    /// keys kept), elements in `sorted` order; `TypeError` when two elements do not compare.
    pub fn set(items: Vec<PyValue>) -> Result<PyValue, KernelError> {
        let mut unique: Vec<PyValue> = Vec::with_capacity(items.len());
        for item in items {
            if !unique.iter().any(|seen| key_eq(seen, &item)) {
                unique.push(item);
            }
        }
        Ok(PyValue::Set(sorted(unique)?))
    }

    /// `analysis_engine._finite(value)` (phase2-plan.md section 2, shared conventions): a
    /// `float`, `int` or `bool` as `f64` when finite, a `str` through `float()`, anything else
    /// `None`. An `int` too large for a float raises `OverflowError` as `float(int)` does (the
    /// oracle catches only `TypeError` and `ValueError`).
    pub fn finite(&self) -> Result<Option<f64>, KernelError> {
        let number = match self {
            PyValue::Float(x) => *x,
            PyValue::Int(i) => *i as f64,
            PyValue::Bool(b) => f64::from(u8::from(*b)),
            PyValue::BigInt(text) => big_to_f64(text)?,
            PyValue::Str(text) => match coach_numerics::parse_float(text) {
                Ok(x) => x,
                Err(error) if error.kind == coach_numerics::PyErrorKind::ValueError => {
                    return Ok(None);
                }
                Err(error) => return Err(error.into()),
            },
            _ => return Ok(None),
        };
        Ok(number.is_finite().then_some(number))
    }

    /// Python truthiness. A table reference has no length here: the table itself is true; its
    /// channel map and columns are resolved through the session by the caller.
    pub fn truthy(&self) -> Result<bool, KernelError> {
        Ok(match self {
            PyValue::None => false,
            PyValue::Bool(b) => *b,
            PyValue::Int(i) => *i != 0,
            PyValue::BigInt(_) => true,
            PyValue::Float(x) => *x != 0.0,
            PyValue::Str(s) => !s.is_empty(),
            PyValue::List(items) | PyValue::Tuple(items) | PyValue::Set(items) => !items.is_empty(),
            PyValue::Dict(dict) => !dict.is_empty(),
            PyValue::Dataclass(_) | PyValue::Table | PyValue::Handle(_) => true,
            PyValue::TableChannels | PyValue::TableColumn(_) => {
                return Err(KernelError::internal(
                    "truthiness of a table reference: resolve it through the session",
                ));
            }
        })
    }

    pub fn is_none(&self) -> bool {
        matches!(self, PyValue::None)
    }

    pub fn as_str(&self) -> Option<&str> {
        match self {
            PyValue::Str(s) => Some(s),
            _ => None,
        }
    }

    /// An exact `int` (not a `bool`).
    pub fn as_int(&self) -> Option<i64> {
        match self {
            PyValue::Int(i) => Some(*i),
            _ => None,
        }
    }

    /// An exact `float`.
    pub fn as_float(&self) -> Option<f64> {
        match self {
            PyValue::Float(x) => Some(*x),
            _ => None,
        }
    }

    pub fn as_bool(&self) -> Option<bool> {
        match self {
            PyValue::Bool(b) => Some(*b),
            _ => None,
        }
    }

    pub fn as_list(&self) -> Option<&[PyValue]> {
        match self {
            PyValue::List(items) => Some(items),
            _ => None,
        }
    }

    pub fn as_dict(&self) -> Option<&Dict> {
        match self {
            PyValue::Dict(dict) => Some(dict),
            _ => None,
        }
    }

    pub fn as_dict_mut(&mut self) -> Option<&mut Dict> {
        match self {
            PyValue::Dict(dict) => Some(dict),
            _ => None,
        }
    }

    pub fn as_dataclass(&self) -> Option<&Dataclass> {
        match self {
            PyValue::Dataclass(value) => Some(value),
            _ => None,
        }
    }

    /// `value.get(key)` on a `str`-keyed dict, `None` for a missing key or a value that is not
    /// a dict (`lap.get("lap")`).
    pub fn get(&self, key: &str) -> Option<&PyValue> {
        self.as_dict().and_then(|dict| dict.get(key))
    }
}

/// `float(int)` for an int beyond `i64`: correctly rounded, `OverflowError` past the float range.
fn big_to_f64(text: &str) -> Result<f64, KernelError> {
    let value: f64 = text
        .parse()
        .map_err(|_| KernelError::internal(format!("malformed big int {text:?}")))?;
    if value.is_finite() {
        Ok(value)
    } else {
        Err(KernelError::raises(
            "OverflowError",
            "int too large to convert to float",
        ))
    }
}

/// A canonical signed decimal integer compared with another one.
fn cmp_decimal(a: &str, b: &str) -> Ordering {
    let (a_neg, a_digits) = a.strip_prefix('-').map_or((false, a), |d| (true, d));
    let (b_neg, b_digits) = b.strip_prefix('-').map_or((false, b), |d| (true, d));
    let magnitude = a_digits
        .len()
        .cmp(&b_digits.len())
        .then_with(|| a_digits.cmp(b_digits));
    match (a_neg, b_neg) {
        (false, false) => magnitude,
        (true, true) => magnitude.reverse(),
        (false, true) => Ordering::Greater,
        (true, false) => Ordering::Less,
    }
}

/// An int (as decimal text) compared exactly with a float; `None` for NaN.
fn cmp_decimal_float(int: &str, float: f64) -> Option<Ordering> {
    if float.is_nan() {
        return None;
    }
    if float.is_infinite() {
        return Some(if float > 0.0 {
            Ordering::Less
        } else {
            Ordering::Greater
        });
    }
    let whole = float.trunc();
    let whole_text = format!("{whole:.0}");
    let whole_text = if whole_text == "-0" { "0" } else { &whole_text };
    Some(match cmp_decimal(int, whole_text) {
        Ordering::Equal => 0.0f64.partial_cmp(&(float - whole))?,
        other => other,
    })
}

fn int_text(value: &PyValue) -> Option<String> {
    match value {
        PyValue::Bool(b) => Some(u8::from(*b).to_string()),
        PyValue::Int(i) => Some(i.to_string()),
        PyValue::BigInt(text) => Some(text.clone()),
        _ => None,
    }
}

/// Python's `a <=> b` for two numbers (`bool`, `int`, `float`); `None` when either is not a
/// number or either is NaN.
fn cmp_numbers(a: &PyValue, b: &PyValue) -> Option<Ordering> {
    use coach_numerics::Num;
    let small = |value: &PyValue| match value {
        PyValue::Bool(b) => Some(Num::Int(i64::from(*b))),
        PyValue::Int(i) => Some(Num::Int(*i)),
        PyValue::Float(x) => Some(Num::Float(*x)),
        _ => None,
    };
    if let (Some(x), Some(y)) = (small(a), small(b)) {
        return coach_numerics::py_cmp(x, y);
    }
    match (a, b) {
        (PyValue::Float(x), other) => {
            cmp_decimal_float(&int_text(other)?, *x).map(Ordering::reverse)
        }
        (other, PyValue::Float(y)) => cmp_decimal_float(&int_text(other)?, *y),
        _ => Some(cmp_decimal(&int_text(a)?, &int_text(b)?)),
    }
}

/// Equality of two dict keys or set elements as Python compares hashable values.
pub fn key_eq(a: &PyValue, b: &PyValue) -> bool {
    match (a, b) {
        (PyValue::None, PyValue::None) => true,
        (PyValue::Str(x), PyValue::Str(y)) => x == y,
        (PyValue::Tuple(x), PyValue::Tuple(y)) | (PyValue::Set(x), PyValue::Set(y)) => {
            x.len() == y.len() && x.iter().zip(y).all(|(p, q)| key_eq(p, q))
        }
        (PyValue::TableColumn(x), PyValue::TableColumn(y)) => x == y,
        (PyValue::Handle(x), PyValue::Handle(y)) => x == y,
        _ => cmp_numbers(a, b) == Some(Ordering::Equal),
    }
}

/// Python's `a < b` for the values `sorted` meets in a set: numbers, strings (code point
/// order) and tuples of them; `TypeError` for any other pair.
pub fn py_lt(a: &PyValue, b: &PyValue) -> Result<bool, KernelError> {
    match (a, b) {
        (PyValue::Str(x), PyValue::Str(y)) => Ok(x < y),
        (PyValue::Tuple(x), PyValue::Tuple(y)) => {
            for (p, q) in x.iter().zip(y) {
                if !key_eq(p, q) {
                    return py_lt(p, q);
                }
            }
            Ok(x.len() < y.len())
        }
        _ => {
            let numeric = |value: &PyValue| {
                matches!(
                    value,
                    PyValue::Bool(_) | PyValue::Int(_) | PyValue::BigInt(_) | PyValue::Float(_)
                )
            };
            if numeric(a) && numeric(b) {
                Ok(cmp_numbers(a, b) == Some(Ordering::Less))
            } else {
                Err(KernelError::raises(
                    "TypeError",
                    format!(
                        "'<' not supported between instances of '{}' and '{}'",
                        a.type_name(),
                        b.type_name()
                    ),
                ))
            }
        }
    }
}

/// `sorted(items)`: CPython's stable sort under [`py_lt`].
pub fn sorted(mut items: Vec<PyValue>) -> Result<Vec<PyValue>, KernelError> {
    let mut failure = None;
    coach_numerics::sort_by(
        &mut items,
        |a, b| match py_lt(a, b) {
            Ok(less) => less,
            Err(error) => {
                failure.get_or_insert(error);
                false
            }
        },
        false,
    );
    match failure {
        Some(error) => Err(error),
        None => Ok(items),
    }
}

/// The first place two values differ, as a path (`["laps"][3]["lap_time_s"]`) and what differs.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Difference {
    pub path: String,
    pub detail: String,
}

impl std::fmt::Display for Difference {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(
            f,
            "at {}: {}",
            if self.path.is_empty() {
                "<root>"
            } else {
                &self.path
            },
            self.detail
        )
    }
}

/// Exact equality as the replay requires (`kernel_codec.same`): the same variant (`int` is not
/// `float` is not `bool`), the same float bits (any NaN equals any NaN), the same key order,
/// the same strings. `None` when equal.
pub fn same(expected: &PyValue, actual: &PyValue) -> Option<Difference> {
    let mut path = String::new();
    diff(expected, actual, &mut path).map(|detail| Difference { path, detail })
}

fn short(value: &PyValue) -> String {
    let text = super::codec::to_json(value).unwrap_or_else(|_| format!("{value:?}"));
    if text.chars().count() > 120 {
        let cut: String = text.chars().take(117).collect();
        format!("{cut}...")
    } else {
        text
    }
}

fn diff(expected: &PyValue, actual: &PyValue, path: &mut String) -> Option<String> {
    let mismatch = || {
        Some(format!(
            "expected {} {}, got {} {}",
            expected.type_name(),
            short(expected),
            actual.type_name(),
            short(actual)
        ))
    };
    match (expected, actual) {
        (PyValue::None, PyValue::None)
        | (PyValue::Table, PyValue::Table)
        | (PyValue::TableChannels, PyValue::TableChannels) => None,
        (PyValue::Bool(x), PyValue::Bool(y)) => (x != y).then(mismatch).flatten(),
        (PyValue::Int(x), PyValue::Int(y)) => (x != y).then(mismatch).flatten(),
        (PyValue::BigInt(x), PyValue::BigInt(y))
        | (PyValue::Str(x), PyValue::Str(y))
        | (PyValue::TableColumn(x), PyValue::TableColumn(y)) => (x != y).then(mismatch).flatten(),
        (PyValue::Handle(x), PyValue::Handle(y)) => (x != y).then(mismatch).flatten(),
        (PyValue::Float(x), PyValue::Float(y)) => {
            let equal = x.to_bits() == y.to_bits() || (x.is_nan() && y.is_nan());
            (!equal).then(|| {
                format!(
                    "expected float {} ({:#018x}), got float {} ({:#018x})",
                    coach_numerics::float_repr(*x),
                    x.to_bits(),
                    coach_numerics::float_repr(*y),
                    y.to_bits()
                )
            })
        }
        (PyValue::List(x), PyValue::List(y))
        | (PyValue::Tuple(x), PyValue::Tuple(y))
        | (PyValue::Set(x), PyValue::Set(y)) => {
            let mark = path.len();
            for (index, (p, q)) in x.iter().zip(y).enumerate() {
                let _ = write!(path, "[{index}]");
                if let Some(detail) = diff(p, q, path) {
                    return Some(detail);
                }
                path.truncate(mark);
            }
            (x.len() != y.len()).then(|| {
                format!(
                    "expected {} of {} items, got {}",
                    expected.type_name(),
                    x.len(),
                    y.len()
                )
            })
        }
        (PyValue::Dict(x), PyValue::Dict(y)) => {
            let mark = path.len();
            for (index, ((k1, v1), (k2, v2))) in x.iter().zip(y.iter()).enumerate() {
                if let Some(detail) = diff(k1, k2, path) {
                    path.push_str(&format!("<key {index}>"));
                    return Some(format!("key order or key differs: {detail}"));
                }
                let _ = write!(path, "[{}]", short(k1));
                if let Some(detail) = diff(v1, v2, path) {
                    return Some(detail);
                }
                path.truncate(mark);
            }
            if x.len() != y.len() {
                let extra: Vec<String> = if x.len() > y.len() {
                    x.keys().skip(y.len()).map(short).collect()
                } else {
                    y.keys().skip(x.len()).map(short).collect()
                };
                return Some(format!(
                    "expected {} keys, got {} (first unmatched keys: {})",
                    x.len(),
                    y.len(),
                    extra.into_iter().take(5).collect::<Vec<_>>().join(", ")
                ));
            }
            None
        }
        (PyValue::Dataclass(x), PyValue::Dataclass(y)) => {
            if x.class != y.class {
                return Some(format!("expected dataclass {}, got {}", x.class, y.class));
            }
            let mark = path.len();
            for ((n1, v1), (n2, v2)) in x.fields.iter().zip(&y.fields) {
                if n1 != n2 {
                    return Some(format!("field order differs: {n1} against {n2}"));
                }
                let _ = write!(path, ".{n1}");
                if let Some(detail) = diff(v1, v2, path) {
                    return Some(detail);
                }
                path.truncate(mark);
            }
            (x.fields.len() != y.fields.len())
                .then(|| format!("expected {} fields, got {}", x.fields.len(), y.fields.len()))
        }
        _ => mismatch(),
    }
}

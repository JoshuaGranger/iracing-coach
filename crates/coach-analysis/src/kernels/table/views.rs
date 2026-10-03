//! `TelemetryTable` over a coach column store: raw columns at their SDK width, the cached
//! finite, fraction, boolean, flag-bit and wheel-ratio views, and the record of accessed
//! channels (`analysis_engine.TelemetryTable`, `_normalize_column`, `_fraction_column`,
//! `_bool_column`, `_distinct_map`/`_flag_bits`, `_wheel_ratio_column`).
//!
//! Each view is `[f(v) for v in table.get(*aliases, default=default)]` for the Python helper
//! `f` it is named after, computed once per column key and shared (`TelemetryTable._cached`).
//! `_normalize_column`'s `isfinite(sum(converted, 0.0))` test only chooses between two paths
//! that return the same values and the same `complete` flag, so the view computes the
//! definition: every sample through `_finite`, `complete` when none is `None`.

use std::cell::{OnceCell, RefCell};
use std::collections::{BTreeSet, HashMap};
use std::rc::Rc;

use crate::decode::Element;
use crate::kernels::error::KernelError;
use crate::kernels::session::{AccessRecord, Session, TableMeta};
use crate::kernels::value::PyValue;
use crate::store::ColumnStore;

use super::steering::SteeringBasis;
use super::units::{Op, apply_steps};

/// `str.isspace()` for one character (what `str.strip()` removes).
pub(crate) fn is_py_space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

/// `analysis_engine._bool` of a `str`: `value.strip().lower() in {"1", "true", "yes", "on"}`.
/// No non-ASCII character lower-cases into those ASCII words, so after the strip the test is
/// an ASCII comparison.
pub(crate) fn text_truth(text: &str) -> bool {
    let stripped = text.trim_matches(is_py_space);
    stripped.is_ascii()
        && matches!(
            stripped.to_ascii_lowercase().as_str(),
            "1" | "true" | "yes" | "on"
        )
}

/// A column key (`TelemetryTable._column_key`): the first alias present in the table, else the
/// default value every sample takes.
#[derive(Clone, Debug)]
pub enum ColumnKey {
    Channel(String),
    Default(PyValue),
}

impl ColumnKey {
    /// The view-cache key. Python keys a default by `("<default>", type, value)` and compares
    /// keys with `==`, so `-0.0` finds a view built for `0.0` (and the reverse): whichever was
    /// built first is returned for both, as here. Other equal keys build equal views.
    fn cache_key(&self) -> String {
        match self {
            ColumnKey::Channel(name) => format!("c:{name}"),
            ColumnKey::Default(PyValue::Float(zero)) if *zero == 0.0 => "d:Float(0.0)".to_owned(),
            ColumnKey::Default(value) => format!("d:{value:?}"),
        }
    }
}

impl PartialEq for ColumnKey {
    fn eq(&self, other: &ColumnKey) -> bool {
        self.cache_key() == other.cache_key()
    }
}

/// Presence bytes of a merged store's column (1 = value); `None` when every cell is present.
pub type Present<'a> = Option<&'a [u8]>;

/// A `utf8` column: sample `i` is `bytes[offsets[i]..offsets[i + 1]]`.
#[derive(Clone, Copy, Debug)]
pub struct TextColumn<'a> {
    bytes: &'a [u8],
    offsets: &'a [u64],
}

impl<'a> TextColumn<'a> {
    pub fn len(&self) -> usize {
        self.offsets.len().saturating_sub(1)
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }

    /// Sample `index`'s text (`ColumnStore::parse` checked the offsets and the UTF-8).
    pub fn get(&self, index: usize) -> &'a str {
        let start = self.offsets[index] as usize;
        let end = self.offsets[index + 1] as usize;
        std::str::from_utf8(&self.bytes[start..end]).unwrap_or_default()
    }
}

/// The elements of an array variable (`count > 1`), sample-major.
#[derive(Clone, Copy, Debug)]
pub enum ArrayElements<'a> {
    Bool(&'a [u8]),
    I32(&'a [i32]),
    U32(&'a [u32]),
    F32(&'a [f32]),
    F64(&'a [f64]),
}

/// An array variable: sample `i` is the Python list of elements `i * count .. (i + 1) * count`.
#[derive(Clone, Copy, Debug)]
pub struct ArrayColumn<'a> {
    pub count: usize,
    pub elements: ArrayElements<'a>,
}

impl ArrayColumn<'_> {
    pub fn row(&self, index: usize) -> PyValue {
        let range = index * self.count..(index + 1) * self.count;
        PyValue::List(match self.elements {
            ArrayElements::Bool(v) => v[range].iter().map(|&b| PyValue::Bool(b != 0)).collect(),
            ArrayElements::I32(v) => v[range]
                .iter()
                .map(|&x| PyValue::Int(i64::from(x)))
                .collect(),
            ArrayElements::U32(v) => v[range]
                .iter()
                .map(|&x| PyValue::Int(i64::from(x)))
                .collect(),
            ArrayElements::F32(v) => v[range]
                .iter()
                .map(|&x| PyValue::Float(f64::from(x)))
                .collect(),
            ArrayElements::F64(v) => v[range].iter().map(|&x| PyValue::Float(x)).collect(),
        })
    }
}

/// One raw column as `table.get` returns it: the decoded values at their SDK width, with the
/// merged store's presence (a missing cell is Python's `None`), or a default repeated over the
/// table (`[default] * length`).
#[derive(Clone, Debug)]
pub enum Raw<'a> {
    Bool(&'a [u8], Present<'a>),
    I32(&'a [i32], Present<'a>),
    U32(&'a [u32], Present<'a>),
    F32(&'a [f32], Present<'a>),
    F64(&'a [f64], Present<'a>),
    Text(TextColumn<'a>, Present<'a>),
    Array(ArrayColumn<'a>, Present<'a>),
    /// `[value] * length`.
    Repeat(PyValue, usize),
}

impl Raw<'_> {
    pub fn len(&self) -> usize {
        match self {
            Raw::Bool(v, _) => v.len(),
            Raw::I32(v, _) => v.len(),
            Raw::U32(v, _) => v.len(),
            Raw::F32(v, _) => v.len(),
            Raw::F64(v, _) => v.len(),
            Raw::Text(v, _) => v.len(),
            Raw::Array(v, _) => match v.elements {
                ArrayElements::Bool(e) => e.len() / v.count,
                ArrayElements::I32(e) => e.len() / v.count,
                ArrayElements::U32(e) => e.len() / v.count,
                ArrayElements::F32(e) => e.len() / v.count,
                ArrayElements::F64(e) => e.len() / v.count,
            },
            Raw::Repeat(_, length) => *length,
        }
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }

    fn present_bytes(&self) -> Present<'_> {
        match self {
            Raw::Bool(_, p)
            | Raw::I32(_, p)
            | Raw::U32(_, p)
            | Raw::F32(_, p)
            | Raw::F64(_, p)
            | Raw::Text(_, p)
            | Raw::Array(_, p) => *p,
            Raw::Repeat(..) => None,
        }
    }

    /// Whether sample `index` holds a value (not `None`); a repeated `None` default is absent.
    pub fn is_present(&self, index: usize) -> bool {
        match self {
            Raw::Repeat(value, _) => !value.is_none(),
            _ => self.present_bytes().is_none_or(|p| p[index] != 0),
        }
    }

    /// Sample `index` as the Python object the oracle's list holds.
    pub fn value(&self, index: usize) -> PyValue {
        if !self.is_present(index) {
            return PyValue::None;
        }
        match self {
            Raw::Bool(v, _) => PyValue::Bool(v[index] != 0),
            Raw::I32(v, _) => PyValue::Int(i64::from(v[index])),
            Raw::U32(v, _) => PyValue::Int(i64::from(v[index])),
            Raw::F32(v, _) => PyValue::Float(f64::from(v[index])),
            Raw::F64(v, _) => PyValue::Float(v[index]),
            Raw::Text(v, _) => PyValue::Str(v.get(index).to_owned()),
            Raw::Array(v, _) => v.row(index),
            Raw::Repeat(value, _) => value.clone(),
        }
    }

    /// `analysis_engine._finite(column[index])`.
    pub fn finite(&self, index: usize) -> Result<Option<f64>, KernelError> {
        if !self.is_present(index) {
            return Ok(None);
        }
        let number = match self {
            Raw::Bool(v, _) => f64::from(u8::from(v[index] != 0)),
            Raw::I32(v, _) => f64::from(v[index]),
            Raw::U32(v, _) => f64::from(v[index]),
            Raw::F32(v, _) => f64::from(v[index]),
            Raw::F64(v, _) => v[index],
            Raw::Text(v, _) => return text_finite(v.get(index)),
            // float(list) raises TypeError, which _finite maps to None.
            Raw::Array(..) => return Ok(None),
            Raw::Repeat(value, _) => return value.finite(),
        };
        Ok(number.is_finite().then_some(number))
    }

    /// `analysis_engine._bool(column[index])` (`_bool_column`: `str.strip().lower()` for text,
    /// `bool(value)` otherwise, so a NaN float is true and a missing cell false).
    pub fn boolean(&self, index: usize) -> Result<bool, KernelError> {
        if !self.is_present(index) {
            return Ok(false);
        }
        Ok(match self {
            Raw::Bool(v, _) => v[index] != 0,
            Raw::I32(v, _) => v[index] != 0,
            Raw::U32(v, _) => v[index] != 0,
            Raw::F32(v, _) => v[index] != 0.0,
            Raw::F64(v, _) => v[index] != 0.0,
            Raw::Text(v, _) => text_truth(v.get(index)),
            Raw::Array(v, _) => v.count > 0,
            Raw::Repeat(value, _) => match value {
                PyValue::Str(text) => text_truth(text),
                other => other.truthy()?,
            },
        })
    }
}

/// `float(text)` as `_finite` applies it: a `ValueError` is `None`, a non-finite result `None`.
fn text_finite(text: &str) -> Result<Option<f64>, KernelError> {
    PyValue::Str(text.to_owned()).finite()
}

/// A per-sample float view: values with presence (`None` where absent) and the `complete`
/// flag the oracle returns beside it. Values of a transformed view may be non-finite (no
/// finite check follows `_apply_steps`).
#[derive(Clone, Debug, Default)]
pub struct View {
    values: Vec<f64>,
    /// One bit per sample, set when present; `None` when every sample is present.
    present: Option<Vec<u64>>,
    complete: bool,
}

impl View {
    /// A view of `options`, `complete` when none is `None`.
    pub fn from_options(options: impl IntoIterator<Item = Option<f64>>) -> View {
        let mut builder = ViewBuilder::default();
        for option in options {
            builder.push(option);
        }
        let complete = builder.all_present();
        builder.finish(complete)
    }

    /// A view of `options` with the `complete` flag the caller computed (`_apply_steps` keeps
    /// its input's flag).
    pub fn from_options_with(
        options: impl IntoIterator<Item = Option<f64>>,
        complete: bool,
    ) -> View {
        let mut builder = ViewBuilder::default();
        for option in options {
            builder.push(option);
        }
        builder.finish(complete)
    }

    pub fn len(&self) -> usize {
        self.values.len()
    }

    pub fn is_empty(&self) -> bool {
        self.values.is_empty()
    }

    pub fn get(&self, index: usize) -> Option<f64> {
        let present = self
            .present
            .as_ref()
            .is_none_or(|bits| bits[index / 64] >> (index % 64) & 1 == 1);
        present.then(|| self.values[index])
    }

    /// The flag the oracle returns with the list (`_normalize_column`'s "no `None`").
    pub fn complete(&self) -> bool {
        self.complete
    }

    /// Whether no sample is `None` (independent of the returned flag).
    pub fn all_present(&self) -> bool {
        match &self.present {
            None => true,
            Some(bits) => (0..self.values.len()).all(|i| bits[i / 64] >> (i % 64) & 1 == 1),
        }
    }

    pub fn iter(&self) -> impl DoubleEndedIterator<Item = Option<f64>> + ExactSizeIterator + '_ {
        (0..self.values.len()).map(|index| self.get(index))
    }

    /// The view's present values in sample order (`[v for v in values if v is not None]`).
    pub fn present_values(&self) -> Vec<f64> {
        self.iter().flatten().collect()
    }

    /// The view as the Python list it stands for (`float` or `None` per sample).
    pub fn to_value(&self) -> PyValue {
        PyValue::List(
            self.iter()
                .map(|value| value.map_or(PyValue::None, PyValue::Float))
                .collect(),
        )
    }
}

/// Builds a [`View`] sample by sample.
#[derive(Default)]
pub struct ViewBuilder {
    values: Vec<f64>,
    bits: Vec<u64>,
    missing: bool,
}

impl ViewBuilder {
    pub fn with_capacity(capacity: usize) -> ViewBuilder {
        ViewBuilder {
            values: Vec::with_capacity(capacity),
            bits: Vec::with_capacity(capacity.div_ceil(64)),
            missing: false,
        }
    }

    pub fn push(&mut self, value: Option<f64>) {
        let index = self.values.len();
        if index.is_multiple_of(64) {
            self.bits.push(0);
        }
        match value {
            Some(number) => {
                self.values.push(number);
                self.bits[index / 64] |= 1 << (index % 64);
            }
            None => {
                self.values.push(0.0);
                self.missing = true;
            }
        }
    }

    pub fn all_present(&self) -> bool {
        !self.missing
    }

    pub fn finish(self, complete: bool) -> View {
        View {
            values: self.values,
            present: self.missing.then_some(self.bits),
            complete,
        }
    }
}

/// A column's values converted once from the store's little-endian bytes.
enum Typed {
    Bytes,
    I32(Vec<i32>),
    U32(Vec<u32>),
    F32(Vec<f32>),
    F64(Vec<f64>),
    Offsets(Vec<u64>),
}

struct ColumnSlot {
    element: Element,
    count: usize,
    typed: OnceCell<Typed>,
}

/// Every per-key view cache of a table.
#[derive(Default)]
struct Caches {
    finite: HashMap<String, Rc<View>>,
    fraction: HashMap<String, Rc<View>>,
    boolean: HashMap<String, Rc<Vec<bool>>>,
    flags: HashMap<String, Rc<Vec<i64>>>,
    wheel: HashMap<(String, String), Rc<View>>,
    steering: HashMap<u64, Rc<View>>,
}

/// The analysis `TelemetryTable` (unit U1, milestone M1 of `phase2-plan.md` section 2).
///
/// A view that cannot be built as the oracle builds it (a text cell `float()` accepts but this
/// port does not, a flag beyond `i64`, `OverflowError` from a huge default) records a failure
/// instead of a value; [`Table::check`] returns it, and every entry calls it before returning,
/// so such a table never yields a result. The failure is sticky because the view is cached.
pub struct Table {
    store: Rc<ColumnStore>,
    meta: Rc<TableMeta>,
    index: HashMap<String, usize>,
    units: HashMap<String, String>,
    columns: Vec<ColumnSlot>,
    length: usize,
    access: AccessRecord,
    caches: RefCell<Caches>,
    failure: RefCell<Option<KernelError>>,
}

impl Table {
    /// The session's table, built on first use.
    pub fn of(session: &Session) -> Result<Rc<Table>, KernelError> {
        session.cached(|session| {
            Table::new(
                Rc::clone(session.store()),
                Rc::clone(session.meta()),
                session.access().clone(),
            )
        })
    }

    /// A table over `store` (decode or merge stage) with the oracle's metadata.
    pub fn from_store(store: ColumnStore, meta: TableMeta) -> Result<Table, KernelError> {
        Table::new(Rc::new(store), Rc::new(meta), AccessRecord::default())
    }

    pub fn new(
        store: Rc<ColumnStore>,
        meta: Rc<TableMeta>,
        access: AccessRecord,
    ) -> Result<Table, KernelError> {
        let length = usize::try_from(store.header.sampling.sample_count)
            .map_err(|_| KernelError::internal("sample count beyond usize"))?;
        if length != meta.length {
            return Err(KernelError::internal(format!(
                "the store holds {length} samples, the table {}",
                meta.length
            )));
        }
        let names: Vec<&str> = store
            .header
            .columns
            .iter()
            .map(|c| c.name.as_str())
            .collect();
        if names != meta.channel_names {
            return Err(KernelError::internal(
                "the store's columns are not the table's channels in the table's order",
            ));
        }
        let mut columns = Vec::with_capacity(names.len());
        for header in &store.header.columns {
            let element = Element::from_name(&header.element)
                .ok_or_else(|| KernelError::internal("unknown column element"))?;
            columns.push(ColumnSlot {
                element,
                count: header.count as usize,
                typed: OnceCell::new(),
            });
        }
        let index = names
            .iter()
            .enumerate()
            .map(|(i, name)| ((*name).to_owned(), i))
            .collect();
        let units = meta.channel_units.iter().cloned().collect();
        Ok(Table {
            store,
            meta,
            index,
            units,
            columns,
            length,
            access,
            caches: RefCell::new(Caches::default()),
            failure: RefCell::new(None),
        })
    }

    pub fn len(&self) -> usize {
        self.length
    }

    pub fn is_empty(&self) -> bool {
        self.length == 0
    }

    pub fn meta(&self) -> &TableMeta {
        &self.meta
    }

    /// `metadata.get(key)` of the table (`TelemetryTable.metadata`).
    pub fn metadata(&self, key: &str) -> Option<&PyValue> {
        self.meta.metadata.get(key)
    }

    /// `name in table.channels`.
    pub fn contains(&self, name: &str) -> bool {
        self.index.contains_key(name)
    }

    /// `TelemetryTable.unit(channel)`: `channel_units.get(channel)` for a non-empty channel.
    pub fn unit(&self, channel: Option<&str>) -> Option<&str> {
        match channel {
            Some(name) if !name.is_empty() => self.units.get(name).map(String::as_str),
            _ => None,
        }
    }

    /// The first alias present in the table.
    fn first(&self, aliases: &[&str]) -> Option<usize> {
        aliases
            .iter()
            .find_map(|alias| self.index.get(*alias).copied())
    }

    fn record_index(&self, column: usize) {
        self.access.record(&self.meta.channel_names[column]);
    }

    /// `TelemetryTable._record_access`: records the first present alias.
    pub fn record_access(&self, aliases: &[&str]) {
        if let Some(column) = self.first(aliases) {
            self.record_index(column);
        }
    }

    /// `TelemetryTable.has(*aliases)`: records the first present alias.
    pub fn has(&self, aliases: &[&str]) -> bool {
        self.record_access(aliases);
        self.first(aliases).is_some()
    }

    /// `TelemetryTable.get(*aliases, default=default)`: records the first present alias.
    pub fn get(&self, aliases: &[&str], default: PyValue) -> Raw<'_> {
        self.resolve(aliases, default).1
    }

    /// `TelemetryTable.resolve(*aliases, default=default)`: records the first present alias.
    pub fn resolve(&self, aliases: &[&str], default: PyValue) -> (Option<&str>, Raw<'_>) {
        match self.first(aliases) {
            Some(column) => {
                self.record_index(column);
                (
                    Some(self.meta.channel_names[column].as_str()),
                    self.raw_column(column),
                )
            }
            None => (None, Raw::Repeat(default, self.length)),
        }
    }

    /// `TelemetryTable._column_key` (records nothing).
    pub fn key(&self, aliases: &[&str], default: PyValue) -> ColumnKey {
        match self.first(aliases) {
            Some(column) => ColumnKey::Channel(self.meta.channel_names[column].clone()),
            None => ColumnKey::Default(default),
        }
    }

    /// `TelemetryTable._raw_column(key)` (records nothing).
    pub fn raw(&self, key: &ColumnKey) -> Result<Raw<'_>, KernelError> {
        match key {
            ColumnKey::Channel(name) => {
                let column = *self.index.get(name).ok_or_else(|| {
                    KernelError::internal(format!("channel {name:?} is not in the table"))
                })?;
                Ok(self.raw_column(column))
            }
            ColumnKey::Default(value) => Ok(Raw::Repeat(value.clone(), self.length)),
        }
    }

    /// Channel `column`'s raw values (the store checked every block's length).
    fn raw_column(&self, column: usize) -> Raw<'_> {
        let slot = &self.columns[column];
        let header = &self.store.header.columns[column];
        let data = self.store.block(header.data).unwrap_or_default();
        let present = self.store.present(column);
        let typed = slot.typed.get_or_init(|| {
            convert(
                slot.element,
                data,
                header.text_offsets.and_then(|b| self.store.block(b)),
            )
        });
        if slot.element == Element::Utf8 {
            let Typed::Offsets(offsets) = typed else {
                unreachable!("utf8 columns convert to offsets")
            };
            return Raw::Text(
                TextColumn {
                    bytes: data,
                    offsets,
                },
                present,
            );
        }
        let elements = match typed {
            Typed::Bytes => ArrayElements::Bool(data),
            Typed::I32(v) => ArrayElements::I32(v),
            Typed::U32(v) => ArrayElements::U32(v),
            Typed::F32(v) => ArrayElements::F32(v),
            Typed::F64(v) => ArrayElements::F64(v),
            Typed::Offsets(_) => unreachable!("only utf8 columns convert to offsets"),
        };
        if slot.count != 1 {
            return Raw::Array(
                ArrayColumn {
                    count: slot.count,
                    elements,
                },
                present,
            );
        }
        match elements {
            ArrayElements::Bool(v) => Raw::Bool(v, present),
            ArrayElements::I32(v) => Raw::I32(v, present),
            ArrayElements::U32(v) => Raw::U32(v, present),
            ArrayElements::F32(v) => Raw::F32(v, present),
            ArrayElements::F64(v) => Raw::F64(v, present),
        }
    }

    /// Records a failure the next [`Table::check`] reports (the first one is kept).
    pub fn fail(&self, error: KernelError) {
        self.failure.borrow_mut().get_or_insert(error);
    }

    /// The first view failure of this table (see the type's documentation), or `Ok`.
    pub fn check(&self) -> Result<(), KernelError> {
        match &*self.failure.borrow() {
            Some(error) => Err(error.clone()),
            None => Ok(()),
        }
    }

    /// `TelemetryTable.normalized(*aliases, default=default)`: records the first present alias.
    pub fn normalized(&self, aliases: &[&str], default: PyValue) -> Rc<View> {
        self.record_access(aliases);
        self.finite_for(&self.key(aliases, default))
    }

    /// `TelemetryTable.fractions(*aliases, default=default)`: records the first present alias.
    pub fn fractions(&self, aliases: &[&str], default: PyValue) -> Rc<View> {
        self.record_access(aliases);
        self.fractions_for(&self.key(aliases, default))
    }

    /// `TelemetryTable.booleans(*aliases, default=default)`: records the first present alias.
    pub fn booleans(&self, aliases: &[&str], default: PyValue) -> Rc<Vec<bool>> {
        self.record_access(aliases);
        self.booleans_for(&self.key(aliases, default))
    }

    /// `TelemetryTable.flag_bits(*aliases, default=default)` (the oracle's default is `0`):
    /// records the first present alias.
    pub fn flag_bits(&self, aliases: &[&str], default: PyValue) -> Rc<Vec<i64>> {
        self.record_access(aliases);
        self.flag_bits_for(&self.key(aliases, default))
    }

    /// `TelemetryTable._finite_for(key)`: `[_finite(v) for v in column]` and `complete`.
    pub fn finite_for(&self, key: &ColumnKey) -> Rc<View> {
        let cache = key.cache_key();
        if let Some(view) = self.caches.borrow().finite.get(&cache) {
            return Rc::clone(view);
        }
        let view = Rc::new(self.build_finite(key));
        self.caches
            .borrow_mut()
            .finite
            .insert(cache, Rc::clone(&view));
        view
    }

    fn build_finite(&self, key: &ColumnKey) -> View {
        let raw = match self.raw(key) {
            Ok(raw) => raw,
            Err(error) => {
                self.fail(error);
                return View::from_options(std::iter::repeat_n(None, self.length));
            }
        };
        if let Raw::Repeat(value, length) = &raw {
            // [_finite(default)] * length, complete when a number or empty.
            let number = value.finite().unwrap_or_else(|error| {
                self.fail(error);
                None
            });
            return View::from_options_with(
                std::iter::repeat_n(number, *length),
                number.is_some() || *length == 0,
            );
        }
        let mut builder = ViewBuilder::with_capacity(raw.len());
        for index in 0..raw.len() {
            let value = raw.finite(index).unwrap_or_else(|error| {
                self.fail(error);
                None
            });
            builder.push(value);
        }
        let complete = builder.all_present();
        builder.finish(complete)
    }

    /// `TelemetryTable._fractions_for(key)`: `_fraction_column` of the finite view, with its
    /// `complete` flag.
    pub fn fractions_for(&self, key: &ColumnKey) -> Rc<View> {
        let cache = key.cache_key();
        if let Some(view) = self.caches.borrow().fraction.get(&cache) {
            return Rc::clone(view);
        }
        let finite = self.finite_for(key);
        let view = Rc::new(View::from_options_with(
            finite.iter().map(|value| value.map(fraction)),
            finite.complete(),
        ));
        self.caches
            .borrow_mut()
            .fraction
            .insert(cache, Rc::clone(&view));
        view
    }

    /// `TelemetryTable._booleans_for(key)`: `_bool_column` of the raw column.
    pub fn booleans_for(&self, key: &ColumnKey) -> Rc<Vec<bool>> {
        let cache = key.cache_key();
        if let Some(view) = self.caches.borrow().boolean.get(&cache) {
            return Rc::clone(view);
        }
        let values = match self.raw(key) {
            Ok(raw) => (0..raw.len())
                .map(|index| {
                    raw.boolean(index).unwrap_or_else(|error| {
                        self.fail(error);
                        false
                    })
                })
                .collect(),
            Err(error) => {
                self.fail(error);
                vec![false; self.length]
            }
        };
        let view = Rc::new(values);
        self.caches
            .borrow_mut()
            .boolean
            .insert(cache, Rc::clone(&view));
        view
    }

    /// `TelemetryTable._flag_bits_for(key)`: `int(n or 0)` of the finite view, each as the low
    /// 64 bits of the Python int in two's complement. Every reader tests or combines bits below
    /// `2**63`, which the low bits give exactly for an int of any size; the one reader that
    /// returns the ints themselves (`_caution_flag_series`) takes a value beyond `i64` from the
    /// finite view ([`Table::flag_values`]).
    pub fn flag_bits_for(&self, key: &ColumnKey) -> Rc<Vec<i64>> {
        let cache = key.cache_key();
        if let Some(view) = self.caches.borrow().flags.get(&cache) {
            return Rc::clone(view);
        }
        let finite = self.finite_for(key);
        let values = finite
            .iter()
            // int(n or 0): None, 0.0 and -0.0 are 0; the finite view holds no NaN or infinity.
            .map(|value| match value.filter(|number| *number != 0.0) {
                None => 0,
                Some(number) => {
                    coach_numerics::int_from_float_low64(number).unwrap_or_else(|error| {
                        self.fail(error.into());
                        0
                    })
                }
            })
            .collect();
        let view = Rc::new(values);
        self.caches
            .borrow_mut()
            .flags
            .insert(cache, Rc::clone(&view));
        view
    }

    /// `_flag_bits_for(key)` as the Python ints themselves: a flag view value within `i64` as
    /// it is, one beyond it as `int(n)` of the finite view's float, exactly.
    pub fn flag_values(&self, key: &ColumnKey, flags: &[i64]) -> Vec<PyValue> {
        let finite = self.finite_for(key);
        flags
            .iter()
            .enumerate()
            .map(|(index, &low)| match finite.get(index) {
                Some(number) if coach_numerics::int_from_float(number).is_err() => {
                    PyValue::BigInt(format!("{:.0}", number.trunc()))
                }
                _ => PyValue::Int(low),
            })
            .collect()
    }

    /// `TelemetryTable._wheel_ratios(speed_key, wheel_key)`: `_wheel_ratio_column` of the two
    /// finite views (`None` below 10 m/s, for a missing sample, or beyond +/-200 %).
    pub fn wheel_ratios(&self, speed: &ColumnKey, wheel: &ColumnKey) -> Rc<View> {
        let cache = (speed.cache_key(), wheel.cache_key());
        if let Some(view) = self.caches.borrow().wheel.get(&cache) {
            return Rc::clone(view);
        }
        let car = self.finite_for(speed);
        let wheel = self.finite_for(wheel);
        let view = Rc::new(View::from_options(car.iter().zip(wheel.iter()).map(
            |(car, wheel)| match (car, wheel) {
                (Some(car), Some(wheel)) if car >= 10.0 => {
                    let ratio = (wheel - car) / car;
                    (ratio.abs() <= 2.0).then_some(ratio)
                }
                _ => None,
            },
        )));
        self.caches
            .borrow_mut()
            .wheel
            .insert(cache, Rc::clone(&view));
        view
    }

    /// `_steering_column(table, basis)`: `SteeringWheelAngle` normalised (recorded), divided by
    /// the ratio on a road-wheel basis (cached by ratio), unchanged otherwise.
    pub fn steering_column(&self, basis: &SteeringBasis) -> Rc<View> {
        let values = self.normalized(&["SteeringWheelAngle"], PyValue::None);
        let Some(ratio) = basis.road_wheel_ratio() else {
            return values;
        };
        if let Some(view) = self.caches.borrow().steering.get(&ratio.to_bits()) {
            return Rc::clone(view);
        }
        let view = Rc::new(apply_steps(&values, &[(Op::Div, ratio)]));
        self.caches
            .borrow_mut()
            .steering
            .insert(ratio.to_bits(), Rc::clone(&view));
        view
    }

    /// The channels recorded since the last take, sorted.
    pub fn take_accessed(&self) -> BTreeSet<String> {
        self.access.take()
    }
}

/// `_fraction(n)` of a finite number: `/ 100.0` when `abs(n) > 1.5`, then `min(1.0, n)` and
/// `max(0.0, n)` spelled as the builtins compare (`-0.0` becomes `0.0`, `1.0` stays 1.0).
pub fn fraction(number: f64) -> f64 {
    let number = if number.abs() > 1.5 {
        number / 100.0
    } else {
        number
    };
    let number = if number < 1.0 { number } else { 1.0 };
    if number > 0.0 { number } else { 0.0 }
}

fn words<const N: usize, T>(data: &[u8], from: fn([u8; N]) -> T) -> Vec<T> {
    data.as_chunks::<N>()
        .0
        .iter()
        .map(|word| from(*word))
        .collect()
}

fn convert(element: Element, data: &[u8], offsets: Option<&[u8]>) -> Typed {
    match element {
        Element::Bool => Typed::Bytes,
        Element::I32 => Typed::I32(words(data, i32::from_le_bytes)),
        Element::U32 => Typed::U32(words(data, u32::from_le_bytes)),
        Element::F32 => Typed::F32(words(data, f32::from_le_bytes)),
        Element::F64 => Typed::F64(words(data, f64::from_le_bytes)),
        Element::Utf8 => Typed::Offsets(words(offsets.unwrap_or_default(), u64::from_le_bytes)),
    }
}

//! Unit U5: field geometry and groove (`docs/08-rewrite/port/phase2-plan.md` section 2).
//!
//! Native replacements for `groove_analysis.analyze_groove_evolution` ([`groove`]) and
//! `race_foundations.build_track_geometry` ([`geometry`], with the geometry identity hash in
//! [`geometry_hash`]), both reached through `analysis_engine`'s module globals. Each returns the
//! oracle's Python objects: types, float bits and dict insertion order.
//!
//! Both entries read the table's raw channels as the oracle passes them: `build_track_geometry`
//! gets `table.channels` (`{"$table_channels": 1}`), `analyze_groove_evolution` a dict of
//! `table.get` columns (`{"$table_column": name}`) with `SessionFlags` replaced by
//! `_caution_flag_series` (a list of ints). Neither records an accessed channel: the oracle
//! records none during either call. Raw columns are read from the session's store at their
//! SDK width ([`Series`]); per sample they convert exactly as the decoded Python value would
//! (`f32` widened, `int` and `bool` as Python numbers, `char` text through `float()`).

pub mod geometry;
pub mod geometry_hash;
pub mod groove;

use coach_numerics as num;

use super::dispatch::{Call, Entry};
use super::error::KernelError;
use super::session::Session;
use super::value::{Dict, PyValue};
use crate::decode::Element;
use crate::store::ColumnHeader;

/// The oracle entry points this unit replaces (`dispatch::UNITS`).
pub const ENTRIES: &[Entry] = &[
    Entry {
        name: "analysis_engine.analyze_groove_evolution",
        run: run_groove,
    },
    Entry {
        name: "analysis_engine.build_track_geometry",
        run: run_geometry,
    },
];

fn run_groove(session: &Session, call: &mut Call) -> Result<PyValue, KernelError> {
    const NAME: &str = "analysis_engine.analyze_groove_evolution";
    let bound = call.bind(
        NAME,
        &["channels"],
        &[
            "runs",
            "laps",
            "load_zones",
            "channel_units",
            "track_type",
            "bins",
        ],
    )?;
    let channels = Channels::new(session, bound.require(call, "channels")?)?;
    let none = PyValue::None;
    groove::analyze_groove_evolution(
        &channels,
        &groove::GrooveArgs {
            runs: bound.require(call, "runs")?,
            laps: bound.require(call, "laps")?,
            load_zones: bound.require(call, "load_zones")?,
            channel_units: bound.get(call, "channel_units").unwrap_or(&none),
            track_type: bound.get(call, "track_type").unwrap_or(&none),
            bins: bound.get(call, "bins"),
        },
    )
}

fn run_geometry(session: &Session, call: &mut Call) -> Result<PyValue, KernelError> {
    const NAME: &str = "analysis_engine.build_track_geometry";
    let bound = call.bind(
        NAME,
        &["channels", "metadata", "identity", "source_fingerprints"],
        &["main_bins"],
    )?;
    let channels = Channels::new(session, bound.require(call, "channels")?)?;
    let empty = PyValue::Tuple(Vec::new());
    geometry::build_track_geometry(
        &channels,
        bound.require(call, "metadata")?,
        bound.require(call, "identity")?,
        bound.get(call, "source_fingerprints").unwrap_or(&empty),
        bound.get(call, "main_bins"),
    )
}

// ---------------------------------------------------------------------------------------------
// Columns
// ---------------------------------------------------------------------------------------------

/// One raw table column in the store: what `table.channels[name]` holds, one Python value per
/// sample (`float` from `f32`/`f64`, `int` from `i32`/`u32`, `bool`, `str` from `char`, a
/// `list` per sample for an array channel, `None` for a merged store's missing cell).
#[derive(Clone, Copy)]
pub struct RawColumn<'a> {
    element: Element,
    count: usize,
    len: usize,
    data: &'a [u8],
    /// `utf8` only: `len + 1` little-endian u64 offsets into `data`.
    offsets: &'a [u8],
    /// A merged store's presence bytes (0 = the cell is `None`); `None` when every cell holds a
    /// value.
    present: Option<&'a [u8]>,
}

impl<'a> RawColumn<'a> {
    fn from_store(
        session: &'a Session,
        index: usize,
        header: &ColumnHeader,
    ) -> Result<Self, KernelError> {
        let store = session.store();
        let bad = |what: &str| KernelError::internal(format!("column {:?}: {what}", header.name));
        let element = Element::from_name(&header.element).ok_or_else(|| bad("unknown element"))?;
        let len =
            usize::try_from(store.header.sampling.sample_count).map_err(|_| bad("sample count"))?;
        let count = header.count as usize;
        let data = store.block(header.data).ok_or_else(|| bad("data block"))?;
        let offsets = match (element, header.text_offsets) {
            (Element::Utf8, Some(block)) => store
                .block(block)
                .ok_or_else(|| bad("text offsets block"))?,
            (Element::Utf8, None) => return Err(bad("utf8 column without offsets")),
            _ => &[],
        };
        // ColumnStore::parse validated the block lengths; check the shapes this reader indexes.
        let expected = len
            .checked_mul(count)
            .and_then(|n| n.checked_mul(element.width()));
        if element == Element::Utf8 {
            if offsets.len() != (len + 1) * 8 {
                return Err(bad("text offsets length"));
            }
        } else if expected != Some(data.len()) {
            return Err(bad("data length"));
        }
        let present = store.present(index);
        if present.is_some_and(|bytes| bytes.len() != len) {
            return Err(bad("presence length"));
        }
        Ok(RawColumn {
            element,
            count,
            len,
            data,
            offsets,
            present,
        })
    }

    fn scalar(&self, index: usize) -> Scalar<'a> {
        if self.present.is_some_and(|bytes| bytes[index] == 0) {
            return Scalar::None;
        }
        if self.element != Element::Utf8 && self.count != 1 {
            return Scalar::List;
        }
        let word = |width: usize| &self.data[index * width..index * width + width];
        match self.element {
            Element::Bool => Scalar::Bool(self.data[index] != 0),
            Element::I32 => Scalar::Int(i64::from(i32::from_le_bytes(
                word(4).try_into().expect("4 bytes"),
            ))),
            Element::U32 => Scalar::Int(i64::from(u32::from_le_bytes(
                word(4).try_into().expect("4 bytes"),
            ))),
            Element::F32 => Scalar::Float(f64::from(f32::from_le_bytes(
                word(4).try_into().expect("4 bytes"),
            ))),
            Element::F64 => Scalar::Float(f64::from_le_bytes(word(8).try_into().expect("8 bytes"))),
            Element::Utf8 => {
                let offset = |i: usize| {
                    u64::from_le_bytes(self.offsets[i * 8..i * 8 + 8].try_into().expect("8 bytes"))
                        as usize
                };
                let piece = self
                    .data
                    .get(offset(index)..offset(index + 1))
                    .unwrap_or_default();
                Scalar::Str(std::str::from_utf8(piece).unwrap_or_default())
            }
        }
    }
}

/// One sample of a raw column, as the Python value it decodes to.
enum Scalar<'a> {
    Bool(bool),
    Int(i64),
    Float(f64),
    Str(&'a str),
    /// An array channel's per-sample list (never empty: the store requires `count >= 1`).
    List,
    /// A merged store's missing cell: Python's `None`.
    None,
}

/// A sequence the oracle indexes per sample: a raw table column or a list argument.
#[derive(Clone, Copy)]
pub enum Series<'a> {
    Raw(RawColumn<'a>),
    Values(&'a [PyValue]),
}

impl<'a> Series<'a> {
    pub fn len(&self) -> usize {
        match self {
            Series::Raw(column) => column.len,
            Series::Values(values) => values.len(),
        }
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }

    /// `_finite(values[index])` of `race_foundations` and `groove_analysis` (both catch
    /// `TypeError`, `ValueError` and `OverflowError`, so every failure is `None`).
    pub fn finite(&self, index: usize) -> Option<f64> {
        match self {
            Series::Raw(column) => match column.scalar(index) {
                Scalar::Bool(b) => Some(f64::from(u8::from(b))),
                Scalar::Int(i) => Some(i as f64),
                Scalar::Float(x) => x.is_finite().then_some(x),
                Scalar::Str(text) => parse_finite(text),
                Scalar::List | Scalar::None => None,
            },
            Series::Values(values) => finite(&values[index]),
        }
    }

    /// `bool(values[index])`.
    pub fn truthy(&self, index: usize) -> Result<bool, KernelError> {
        match self {
            Series::Raw(column) => Ok(match column.scalar(index) {
                Scalar::Bool(b) => b,
                Scalar::Int(i) => i != 0,
                Scalar::Float(x) => x != 0.0,
                Scalar::Str(text) => !text.is_empty(),
                Scalar::List => true,
                Scalar::None => false,
            }),
            Series::Values(values) => values[index].truthy(),
        }
    }
}

/// The `channels` mapping an entry receives: the table's own (`table.channels`) or a dict of
/// columns and lists.
pub enum Channels<'a> {
    Table(&'a Session),
    Dict(&'a Session, &'a Dict),
}

impl<'a> Channels<'a> {
    fn new(session: &'a Session, value: &'a PyValue) -> Result<Self, KernelError> {
        match value {
            PyValue::TableChannels => Ok(Channels::Table(session)),
            PyValue::Dict(dict) => Ok(Channels::Dict(session, dict)),
            other => Err(KernelError::internal(format!(
                "channels is a {}, not a mapping",
                other.type_name()
            ))),
        }
    }

    fn table_column(session: &'a Session, name: &str) -> Result<Option<Series<'a>>, KernelError> {
        match session
            .store()
            .header
            .columns
            .iter()
            .enumerate()
            .find(|(_, c)| c.name == name)
        {
            Some((index, header)) => Ok(Some(Series::Raw(RawColumn::from_store(
                session, index, header,
            )?))),
            None => Ok(None),
        }
    }

    /// `name in channels`.
    pub fn contains(&self, name: &str) -> bool {
        match self {
            Channels::Table(session) => session.meta().channel_names.iter().any(|n| n == name),
            Channels::Dict(_, dict) => dict.contains(name),
        }
    }

    /// `channels.get(name)` when it is a sequence the entries index (`_series`: a `list` or
    /// `tuple`, a table column), `Ok(None)` when missing or not a sequence.
    pub fn series(&self, name: &str) -> Result<Option<Series<'a>>, KernelError> {
        match self {
            Channels::Table(session) => Self::table_column(session, name),
            Channels::Dict(session, dict) => match dict.get(name) {
                Some(PyValue::TableColumn(column)) => match Self::table_column(session, column)? {
                    Some(series) => Ok(Some(series)),
                    None => Err(KernelError::internal(format!(
                        "table column {column:?} is not in the store"
                    ))),
                },
                Some(PyValue::List(values) | PyValue::Tuple(values)) => {
                    Ok(Some(Series::Values(values)))
                }
                _ => Ok(None),
            },
        }
    }

    /// `channels[name]` for a present key that must be a sequence (`build_track_geometry`).
    fn required(&self, name: &str) -> Result<Series<'a>, KernelError> {
        self.series(name)?
            .ok_or_else(|| KernelError::internal(format!("channels[{name:?}] is not a sequence")))
    }

    /// `channels.get(name)` is present and truthy (a non-empty sequence).
    fn truthy(&self, name: &str) -> Result<bool, KernelError> {
        match self {
            Channels::Dict(_, dict) => match dict.get(name) {
                None => Ok(false),
                Some(PyValue::TableColumn(_)) => Ok(!self.required(name)?.is_empty()),
                Some(value) => value.truthy(),
            },
            Channels::Table(_) => Ok(self.series(name)?.is_some_and(|s| !s.is_empty())),
        }
    }
}

// ---------------------------------------------------------------------------------------------
// Python value helpers shared by both entries
// ---------------------------------------------------------------------------------------------

/// `float(text)` when finite; a `ValueError` (or anything else) is `None`.
fn parse_finite(text: &str) -> Option<f64> {
    num::parse_float(text).ok().filter(|x| x.is_finite())
}

/// `_finite(value)` of `groove_analysis` and `race_foundations`: `float(value)` when finite,
/// `None` for every exception those modules catch (`TypeError`, `ValueError`,
/// `OverflowError`).
pub fn finite(value: &PyValue) -> Option<f64> {
    match value {
        PyValue::Bool(b) => Some(f64::from(u8::from(*b))),
        PyValue::Int(i) => Some(*i as f64),
        PyValue::BigInt(text) => text.parse::<f64>().ok().filter(|x| x.is_finite()),
        PyValue::Float(x) => x.is_finite().then_some(*x),
        PyValue::Str(text) => parse_finite(text),
        _ => None,
    }
}

/// `round(x, digits)` of a float.
fn round(x: f64, digits: i64) -> Result<f64, KernelError> {
    Ok(num::round_ndigits(x, digits)?)
}

/// `int(x)` of a finite float, as an `int` value (`BigInt` beyond `i64`).
fn int_value(x: f64) -> Result<PyValue, KernelError> {
    match num::int_from_float(x) {
        Ok(i) => Ok(PyValue::Int(i)),
        Err(error) if error.kind == num::PyErrorKind::IntBeyondI64 => {
            Ok(PyValue::BigInt(format!("{:.0}", x.trunc())))
        }
        Err(error) => Err(error.into()),
    }
}

/// `int(x)` of a finite float where the result is used as a number (dict keys, comparisons):
/// beyond `i64` is a port limit.
fn int_of(x: f64) -> Result<i64, KernelError> {
    Ok(num::int_from_float(x)?)
}

/// `statistics.median` of finite floats (never empty).
fn median(values: &[f64]) -> Result<f64, KernelError> {
    Ok(num::median_floats(values)?)
}

/// Python `str(value)` of the values these entries format (`str(track_id)`, `str(sha256)`).
pub fn py_str(value: &PyValue) -> Result<String, KernelError> {
    Ok(match value {
        PyValue::Str(text) => text.clone(),
        other => py_repr(other)?,
    })
}

/// Python `repr(value)` for plain data (`str()` of a container uses `repr` of its items).
pub fn py_repr(value: &PyValue) -> Result<String, KernelError> {
    Ok(match value {
        PyValue::None => "None".to_owned(),
        PyValue::Bool(true) => "True".to_owned(),
        PyValue::Bool(false) => "False".to_owned(),
        PyValue::Int(i) => i.to_string(),
        PyValue::BigInt(text) => text.clone(),
        PyValue::Float(x) => num::float_repr(*x),
        PyValue::Str(text) => str_repr(text),
        PyValue::List(items) => format!("[{}]", join_repr(items)?),
        PyValue::Tuple(items) if items.len() == 1 => format!("({},)", py_repr(&items[0])?),
        PyValue::Tuple(items) => format!("({})", join_repr(items)?),
        PyValue::Set(items) if items.is_empty() => "set()".to_owned(),
        PyValue::Dict(dict) => {
            let mut parts = Vec::with_capacity(dict.len());
            for (key, item) in dict.iter() {
                parts.push(format!("{}: {}", py_repr(key)?, py_repr(item)?));
            }
            format!("{{{}}}", parts.join(", "))
        }
        other => {
            return Err(KernelError::internal(format!(
                "str() of a {} is not ported",
                other.type_name()
            )));
        }
    })
}

fn join_repr(items: &[PyValue]) -> Result<String, KernelError> {
    let parts = items.iter().map(py_repr).collect::<Result<Vec<_>, _>>()?;
    Ok(parts.join(", "))
}

/// `repr(str)`: single quotes unless the text holds `'` and no `"`; backslash escapes for the
/// quote, backslash, `\t`, `\n`, `\r` and other non-printable characters.
fn str_repr(text: &str) -> String {
    let quote = if text.contains('\'') && !text.contains('"') {
        '"'
    } else {
        '\''
    };
    let mut out = String::with_capacity(text.len() + 2);
    out.push(quote);
    for character in text.chars() {
        match character {
            '\\' => out.push_str("\\\\"),
            '\t' => out.push_str("\\t"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            c if is_printable(c) => out.push(c),
            c => {
                let code = c as u32;
                if code < 0x100 {
                    out.push_str(&format!("\\x{code:02x}"));
                } else if code < 0x10000 {
                    out.push_str(&format!("\\u{code:04x}"));
                } else {
                    out.push_str(&format!("\\U{code:08x}"));
                }
            }
        }
    }
    out.push(quote);
    out
}

/// `str.isprintable` for one character, from CPython's own table (`coach_numerics::unicode`).
fn is_printable(c: char) -> bool {
    num::unicode::is_printable(c)
}

/// `str.isspace` for one character: Unicode whitespace plus the ASCII separators U+001C to
/// U+001F, which Python counts and Rust does not.
pub fn is_space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

/// `str.strip()`.
pub fn strip(text: &str) -> &str {
    text.trim_matches(is_space)
}

/// `str.casefold()`: Unicode full case folding. Rust's lowercase mapping plus the full and
/// simple foldings where it differs (the ones that can decide an ASCII comparison or slug).
pub fn casefold(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for c in text.chars() {
        match c {
            '\u{DF}' | '\u{1E9E}' => out.push_str("ss"),
            '\u{17F}' => out.push('s'),
            '\u{149}' => out.push_str("\u{2BC}n"),
            '\u{1F0}' => out.push_str("j\u{30C}"),
            '\u{1E96}' => out.push_str("h\u{331}"),
            '\u{1E97}' => out.push_str("t\u{308}"),
            '\u{1E98}' => out.push_str("w\u{30A}"),
            '\u{1E99}' => out.push_str("y\u{30A}"),
            '\u{1E9A}' => out.push_str("a\u{2BE}"),
            '\u{FB00}' => out.push_str("ff"),
            '\u{FB01}' => out.push_str("fi"),
            '\u{FB02}' => out.push_str("fl"),
            '\u{FB03}' => out.push_str("ffi"),
            '\u{FB04}' => out.push_str("ffl"),
            '\u{FB05}' | '\u{FB06}' => out.push_str("st"),
            '\u{3C2}' => out.push('\u{3C3}'),
            '\u{B5}' => out.push('\u{3BC}'),
            '\u{345}' => out.push('\u{3B9}'),
            c if c.is_ascii() => out.push(c.to_ascii_lowercase()),
            c => out.extend(c.to_lowercase()),
        }
    }
    out
}

/// Iterates `value or ()` as a `for` loop would: a list, tuple or set's items, a dict's keys,
/// a string's characters; `TypeError` for anything else that is truthy.
fn iterate(value: Option<&PyValue>) -> Result<Vec<PyValue>, KernelError> {
    let Some(value) = value else {
        return Ok(Vec::new());
    };
    if !value.truthy()? {
        return Ok(Vec::new());
    }
    match value {
        PyValue::List(items) | PyValue::Tuple(items) | PyValue::Set(items) => Ok(items.clone()),
        PyValue::Dict(dict) => Ok(dict.keys().cloned().collect()),
        PyValue::Str(text) => Ok(text.chars().map(|c| PyValue::Str(c.to_string())).collect()),
        other => Err(KernelError::raises(
            "TypeError",
            format!("'{}' object is not iterable", other.type_name()),
        )),
    }
}

/// `isinstance(value, Mapping)`.
fn as_mapping(value: &PyValue) -> Option<&Dict> {
    value.as_dict()
}

/// The items of a sequence argument (`runs`, `laps`, `load_zones`, `source_fingerprints`):
/// `list(value)` of a list or tuple.
fn sequence<'v>(value: &'v PyValue, what: &str) -> Result<&'v [PyValue], KernelError> {
    match value {
        PyValue::List(items) | PyValue::Tuple(items) => Ok(items),
        other => Err(KernelError::internal(format!(
            "{what} is a {}, not a list",
            other.type_name()
        ))),
    }
}

/// `format(x, spec)`.
fn format(x: f64, spec: &str) -> Result<String, KernelError> {
    Ok(num::format_float(x, spec)?)
}

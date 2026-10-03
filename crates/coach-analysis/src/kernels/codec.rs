//! Codec version 1 (`crates/coach-analysis/tools/kernel_codec.py`): [`PyValue`] to and from
//! its JSON text, and the per-call vector store (`.ccs`, stage `vector`) that holds
//! column-valued lists.
//!
//! The reader accepts exactly what the Python encoder writes and refuses anything else: no
//! `NaN` or `Infinity` tokens, no integer token outside `i64`, no duplicate key, no unknown `$`
//! marker, no float token that overflows. The writer produces the bytes `kernel_codec.dumps`
//! produces for the same value (compact separators, `ensure_ascii=False`, floats by
//! `float.__repr__`), and [`ColumnSink`] the bytes `kernel_codec.ColumnSink.write` produces.

use std::collections::HashMap;

use super::error::KernelError;
use super::value::{Dataclass, Dict, PyValue, sorted};

pub const CODEC_VERSION: i64 = 1;
/// Lists of at least this many items of one scalar type go to the vector store.
pub const COLUMN_MIN: usize = 256;
const MAX_DEPTH: usize = 256;
const ALIGN: usize = 8;

fn align(value: usize) -> usize {
    value.div_ceil(ALIGN) * ALIGN
}

// ---------------------------------------------------------------------------------------------
// Reading
// ---------------------------------------------------------------------------------------------

/// Decodes one codec document; `columns` resolves `{"$column": id}` (none: a reference fails).
pub fn decode(text: &str, columns: Option<&VectorStore>) -> Result<PyValue, KernelError> {
    let mut parser = Parser {
        bytes: text.as_bytes(),
        at: 0,
        columns,
        depth: 0,
    };
    parser.skip_space();
    let value = parser.value()?;
    parser.skip_space();
    if parser.at != parser.bytes.len() {
        return Err(parser.error("trailing data after the document"));
    }
    Ok(value)
}

struct Parser<'a> {
    bytes: &'a [u8],
    at: usize,
    columns: Option<&'a VectorStore>,
    depth: usize,
}

impl Parser<'_> {
    fn error(&self, message: &str) -> KernelError {
        KernelError::codec(format!("{message} (byte {})", self.at))
    }

    fn skip_space(&mut self) {
        while let Some(b' ' | b'\t' | b'\n' | b'\r') = self.bytes.get(self.at) {
            self.at += 1;
        }
    }

    fn expect(&mut self, literal: &[u8]) -> Result<(), KernelError> {
        if self.bytes[self.at..].starts_with(literal) {
            self.at += literal.len();
            Ok(())
        } else {
            Err(self.error("invalid literal"))
        }
    }

    fn value(&mut self) -> Result<PyValue, KernelError> {
        match self.bytes.get(self.at) {
            None => Err(self.error("unexpected end of input")),
            Some(b'{') => self.nested(Parser::object),
            Some(b'[') => self.nested(|p| p.array().map(PyValue::List)),
            Some(b'"') => self.string().map(PyValue::Str),
            Some(b't') => self.expect(b"true").map(|()| PyValue::Bool(true)),
            Some(b'f') => self.expect(b"false").map(|()| PyValue::Bool(false)),
            Some(b'n') => self.expect(b"null").map(|()| PyValue::None),
            Some(b'-' | b'0'..=b'9') => self.number(),
            Some(_) => Err(self.error("unexpected character")),
        }
    }

    fn nested(
        &mut self,
        parse: impl FnOnce(&mut Self) -> Result<PyValue, KernelError>,
    ) -> Result<PyValue, KernelError> {
        self.depth += 1;
        if self.depth > MAX_DEPTH {
            return Err(self.error("nesting deeper than 256"));
        }
        let value = parse(self);
        self.depth -= 1;
        value
    }

    fn array(&mut self) -> Result<Vec<PyValue>, KernelError> {
        self.at += 1;
        let mut items = Vec::new();
        self.skip_space();
        if self.bytes.get(self.at) == Some(&b']') {
            self.at += 1;
            return Ok(items);
        }
        loop {
            self.skip_space();
            items.push(self.value()?);
            self.skip_space();
            match self.bytes.get(self.at) {
                Some(b',') => self.at += 1,
                Some(b']') => {
                    self.at += 1;
                    return Ok(items);
                }
                _ => return Err(self.error("expected ',' or ']'")),
            }
        }
    }

    fn object(&mut self) -> Result<PyValue, KernelError> {
        let start = self.at;
        self.at += 1;
        let mut entries: Vec<(String, PyValue)> = Vec::new();
        let mut seen: std::collections::HashSet<String> = std::collections::HashSet::new();
        self.skip_space();
        if self.bytes.get(self.at) == Some(&b'}') {
            self.at += 1;
        } else {
            loop {
                self.skip_space();
                if self.bytes.get(self.at) != Some(&b'"') {
                    return Err(self.error("expected a string key"));
                }
                let key = self.string()?;
                self.skip_space();
                if self.bytes.get(self.at) != Some(&b':') {
                    return Err(self.error("expected ':'"));
                }
                self.at += 1;
                self.skip_space();
                let value = self.value()?;
                if !seen.insert(key.clone()) {
                    return Err(self.error(&format!("duplicate key {key:?}")));
                }
                entries.push((key, value));
                self.skip_space();
                match self.bytes.get(self.at) {
                    Some(b',') => self.at += 1,
                    Some(b'}') => {
                        self.at += 1;
                        break;
                    }
                    _ => return Err(self.error("expected ',' or '}'")),
                }
            }
        }
        interpret(entries, self.columns)
            .map_err(|error| KernelError::codec(format!("{error} (object at byte {start})")))
    }

    fn hex4(&mut self) -> Result<u32, KernelError> {
        let digits = self
            .bytes
            .get(self.at..self.at + 4)
            .and_then(|d| std::str::from_utf8(d).ok())
            .filter(|d| d.bytes().all(|b| b.is_ascii_hexdigit()))
            .ok_or_else(|| self.error("bad \\u escape"))?;
        self.at += 4;
        u32::from_str_radix(digits, 16).map_err(|_| self.error("bad \\u escape"))
    }

    fn string(&mut self) -> Result<String, KernelError> {
        self.at += 1;
        let mut out = String::new();
        loop {
            let run_start = self.at;
            while let Some(&b) = self.bytes.get(self.at) {
                if b == b'"' || b == b'\\' || b < 0x20 {
                    break;
                }
                self.at += 1;
            }
            // The input is a &str and the run stops only at ASCII bytes: a valid UTF-8 slice.
            out.push_str(
                std::str::from_utf8(&self.bytes[run_start..self.at])
                    .map_err(|_| self.error("invalid UTF-8"))?,
            );
            match self.bytes.get(self.at) {
                None => return Err(self.error("unterminated string")),
                Some(b'"') => {
                    self.at += 1;
                    return Ok(out);
                }
                Some(b'\\') => {
                    self.at += 1;
                    let escape = *self
                        .bytes
                        .get(self.at)
                        .ok_or_else(|| self.error("unterminated escape"))?;
                    self.at += 1;
                    match escape {
                        b'"' => out.push('"'),
                        b'\\' => out.push('\\'),
                        b'/' => out.push('/'),
                        b'b' => out.push('\u{8}'),
                        b'f' => out.push('\u{c}'),
                        b'n' => out.push('\n'),
                        b'r' => out.push('\r'),
                        b't' => out.push('\t'),
                        b'u' => {
                            let high = self.hex4()?;
                            let code = if (0xD800..0xDC00).contains(&high) {
                                if !self.bytes[self.at..].starts_with(b"\\u") {
                                    return Err(self.error("lone surrogate"));
                                }
                                self.at += 2;
                                let low = self.hex4()?;
                                if !(0xDC00..0xE000).contains(&low) {
                                    return Err(self.error("lone surrogate"));
                                }
                                0x10000 + ((high - 0xD800) << 10) + (low - 0xDC00)
                            } else {
                                high
                            };
                            out.push(
                                char::from_u32(code).ok_or_else(|| self.error("lone surrogate"))?,
                            );
                        }
                        _ => return Err(self.error("unknown escape")),
                    }
                }
                Some(_) => return Err(self.error("control character in string")),
            }
        }
    }

    fn number(&mut self) -> Result<PyValue, KernelError> {
        let start = self.at;
        let digits = |p: &mut Self| {
            let begin = p.at;
            while p.bytes.get(p.at).is_some_and(u8::is_ascii_digit) {
                p.at += 1;
            }
            p.at - begin
        };
        if self.bytes.get(self.at) == Some(&b'-') {
            self.at += 1;
        }
        let int_start = self.at;
        let count = digits(self);
        if count == 0 || (count > 1 && self.bytes[int_start] == b'0') {
            return Err(self.error("malformed number"));
        }
        let mut float = false;
        if self.bytes.get(self.at) == Some(&b'.') {
            self.at += 1;
            float = true;
            if digits(self) == 0 {
                return Err(self.error("malformed number"));
            }
        }
        if let Some(b'e' | b'E') = self.bytes.get(self.at) {
            self.at += 1;
            float = true;
            if let Some(b'+' | b'-') = self.bytes.get(self.at) {
                self.at += 1;
            }
            if digits(self) == 0 {
                return Err(self.error("malformed number"));
            }
        }
        // ASCII by construction.
        let token = std::str::from_utf8(&self.bytes[start..self.at]).unwrap_or_default();
        if float {
            // Correctly rounded, as Python's float(): a token written by repr reads back exactly.
            let value: f64 = token.parse().map_err(|_| self.error("malformed number"))?;
            if !value.is_finite() {
                return Err(self.error(
                    "float token outside the float range (non-finite floats are {\"$f\": ...})",
                ));
            }
            Ok(PyValue::Float(value))
        } else {
            token
                .parse::<i64>()
                .map(PyValue::Int)
                .map_err(|_| self.error("integer token outside i64 (those are {\"$int\": ...})"))
        }
    }
}

/// An object's entries as a value: a plain `str`-keyed dict, or the marker they spell.
fn interpret(
    mut entries: Vec<(String, PyValue)>,
    columns: Option<&VectorStore>,
) -> Result<PyValue, KernelError> {
    if !entries.iter().any(|(key, _)| key.starts_with('$')) {
        // Keys are distinct strings (checked while parsing).
        return Ok(PyValue::Dict(Dict::from_distinct(
            entries
                .into_iter()
                .map(|(key, value)| (PyValue::Str(key), value))
                .collect(),
        )));
    }
    let bad = |what: &str| Err(KernelError::codec(format!("malformed {what}")));
    if entries.len() == 1 {
        let (key, payload) = entries.pop().unwrap_or((String::new(), PyValue::None));
        return match (key.as_str(), payload) {
            ("$f", PyValue::Str(text)) => match text.as_str() {
                "nan" => Ok(PyValue::Float(f64::NAN)),
                "inf" => Ok(PyValue::Float(f64::INFINITY)),
                "-inf" => Ok(PyValue::Float(f64::NEG_INFINITY)),
                _ => bad("$f"),
            },
            ("$int", PyValue::Str(text)) => big_int(&text),
            ("$column", PyValue::Str(id)) => match columns {
                Some(store) => store.values(&id),
                None => Err(KernelError::codec(format!(
                    "column {id:?} referenced but the call has no vector store"
                ))),
            },
            ("$tuple", PyValue::List(items)) => Ok(PyValue::Tuple(items)),
            ("$set", PyValue::List(items)) => Ok(PyValue::Set(items)),
            ("$dict", PyValue::List(pairs)) => {
                let mut dict = Dict::with_capacity(pairs.len());
                for pair in pairs {
                    match pair {
                        PyValue::List(mut kv) if kv.len() == 2 => {
                            let value = kv.pop().unwrap_or(PyValue::None);
                            let key = kv.pop().unwrap_or(PyValue::None);
                            dict.insert(key, value);
                        }
                        _ => return bad("$dict pair"),
                    }
                }
                Ok(PyValue::Dict(dict))
            }
            ("$table", PyValue::Int(1)) => Ok(PyValue::Table),
            ("$table_channels", PyValue::Int(1)) => Ok(PyValue::TableChannels),
            ("$table_column", PyValue::Str(name)) => Ok(PyValue::TableColumn(name)),
            ("$handle", PyValue::Int(n)) if n >= 0 => Ok(PyValue::Handle(n as u64)),
            (key, _) => Err(KernelError::codec(format!(
                "unknown or malformed marker {key:?}"
            ))),
        };
    }
    if entries.len() == 2 {
        let class = entries.iter().position(|(key, _)| key == "$dataclass");
        let fields = entries.iter().position(|(key, _)| key == "fields");
        if let (Some(class), Some(fields)) = (class, fields) {
            let fields = std::mem::replace(&mut entries[fields].1, PyValue::None);
            let class = std::mem::replace(&mut entries[class].1, PyValue::None);
            let (PyValue::Str(class), PyValue::Dict(fields)) = (class, fields) else {
                return bad("$dataclass");
            };
            let mut named = Vec::with_capacity(fields.len());
            for (key, value) in fields.into_items() {
                let PyValue::Str(key) = key else {
                    return bad("$dataclass field name");
                };
                named.push((key, value));
            }
            return Ok(PyValue::Dataclass(Box::new(Dataclass {
                class,
                fields: named,
            })));
        }
    }
    let mut keys: Vec<&str> = entries.iter().map(|(key, _)| key.as_str()).collect();
    keys.sort_unstable();
    Err(KernelError::codec(format!("unknown marker in {keys:?}")))
}

/// `int(text)` for a `$int` payload: canonical decimal digits only.
fn big_int(text: &str) -> Result<PyValue, KernelError> {
    let digits = text.strip_prefix('-').unwrap_or(text);
    let canonical = !digits.is_empty()
        && digits.bytes().all(|b| b.is_ascii_digit())
        && (digits == "0" || !digits.starts_with('0'))
        && text != "-0";
    if !canonical {
        return Err(KernelError::codec(format!("malformed $int {text:?}")));
    }
    Ok(match text.parse::<i64>() {
        Ok(value) => PyValue::Int(value),
        Err(_) => PyValue::BigInt(text.to_owned()),
    })
}

// ---------------------------------------------------------------------------------------------
// Writing
// ---------------------------------------------------------------------------------------------

/// The canonical codec text of `value` with every list inline (the `kernels` hand-off form).
pub fn to_json(value: &PyValue) -> Result<String, KernelError> {
    encode(value, None)
}

/// The canonical codec text of `value`, column-valued lists going to `sink` when given
/// (`kernel_codec.encode` followed by `kernel_codec.dumps`).
pub fn encode(value: &PyValue, sink: Option<&mut ColumnSink>) -> Result<String, KernelError> {
    let mut writer = Writer {
        out: String::new(),
        sink,
    };
    writer.value(value)?;
    Ok(writer.out)
}

struct Writer<'a> {
    out: String,
    sink: Option<&'a mut ColumnSink>,
}

impl Writer<'_> {
    fn value(&mut self, value: &PyValue) -> Result<(), KernelError> {
        match value {
            PyValue::None => self.out.push_str("null"),
            PyValue::Bool(true) => self.out.push_str("true"),
            PyValue::Bool(false) => self.out.push_str("false"),
            PyValue::Int(i) => self.out.push_str(&i.to_string()),
            PyValue::BigInt(text) => {
                self.out.push_str("{\"$int\":");
                write_string(&mut self.out, text);
                self.out.push('}');
            }
            PyValue::Float(x) => {
                if x.is_finite() {
                    self.out.push_str(&coach_numerics::float_repr(*x));
                } else {
                    let name = if x.is_nan() {
                        "nan"
                    } else if *x > 0.0 {
                        "inf"
                    } else {
                        "-inf"
                    };
                    self.out.push_str("{\"$f\":\"");
                    self.out.push_str(name);
                    self.out.push_str("\"}");
                }
            }
            PyValue::Str(text) => write_string(&mut self.out, text),
            PyValue::List(items) => {
                if let Some(sink) = self.sink.as_deref_mut()
                    && let Some(id) = sink.add(items)?
                {
                    self.out.push_str("{\"$column\":");
                    write_string(&mut self.out, &id);
                    self.out.push('}');
                } else {
                    self.sequence(items)?;
                }
            }
            PyValue::Tuple(items) => {
                self.out.push_str("{\"$tuple\":");
                self.sequence(items)?;
                self.out.push('}');
            }
            PyValue::Set(items) => {
                // Python writes sorted(set); a set built by hand is sorted here.
                let ordered = sorted(items.clone())
                    .map_err(|error| KernelError::codec(format!("unsortable set: {error}")))?;
                self.out.push_str("{\"$set\":");
                self.sequence(&ordered)?;
                self.out.push('}');
            }
            PyValue::Dict(dict) => {
                let plain = dict
                    .keys()
                    .all(|key| matches!(key, PyValue::Str(s) if !s.starts_with('$')));
                if plain {
                    self.out.push('{');
                    for (index, (key, item)) in dict.iter().enumerate() {
                        if index > 0 {
                            self.out.push(',');
                        }
                        if let PyValue::Str(key) = key {
                            write_string(&mut self.out, key);
                        }
                        self.out.push(':');
                        self.value(item)?;
                    }
                    self.out.push('}');
                } else {
                    self.out.push_str("{\"$dict\":[");
                    for (index, (key, item)) in dict.iter().enumerate() {
                        if index > 0 {
                            self.out.push(',');
                        }
                        self.out.push('[');
                        self.value(key)?;
                        self.out.push(',');
                        self.value(item)?;
                        self.out.push(']');
                    }
                    self.out.push_str("]}");
                }
            }
            PyValue::Dataclass(instance) => {
                self.out.push_str("{\"$dataclass\":");
                write_string(&mut self.out, &instance.class);
                self.out.push_str(",\"fields\":{");
                for (index, (name, item)) in instance.fields.iter().enumerate() {
                    if index > 0 {
                        self.out.push(',');
                    }
                    write_string(&mut self.out, name);
                    self.out.push(':');
                    self.value(item)?;
                }
                self.out.push_str("}}");
            }
            PyValue::Table => self.out.push_str("{\"$table\":1}"),
            PyValue::TableChannels => self.out.push_str("{\"$table_channels\":1}"),
            PyValue::TableColumn(name) => {
                self.out.push_str("{\"$table_column\":");
                write_string(&mut self.out, name);
                self.out.push('}');
            }
            PyValue::Handle(n) => {
                self.out.push_str("{\"$handle\":");
                self.out.push_str(&n.to_string());
                self.out.push('}');
            }
        }
        Ok(())
    }

    fn sequence(&mut self, items: &[PyValue]) -> Result<(), KernelError> {
        self.out.push('[');
        for (index, item) in items.iter().enumerate() {
            if index > 0 {
                self.out.push(',');
            }
            self.value(item)?;
        }
        self.out.push(']');
        Ok(())
    }
}

/// A JSON string as CPython's `c_encode_basestring` writes it (`ensure_ascii=False`): `"` and
/// `\` escaped, `\b \f \n \r \t` short forms, other characters below U+0020 as `\u00xx`,
/// everything else verbatim.
pub fn write_string(out: &mut String, text: &str) {
    out.push('"');
    for c in text.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\u{8}' => out.push_str("\\b"),
            '\u{c}' => out.push_str("\\f"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 => {
                out.push_str(&format!("\\u{:04x}", c as u32));
            }
            c => out.push(c),
        }
    }
    out.push('"');
}

// ---------------------------------------------------------------------------------------------
// Vector store
// ---------------------------------------------------------------------------------------------

/// The element a vector-store column holds.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ColumnElement {
    F64,
    I64,
    Bool,
    Utf8,
}

impl ColumnElement {
    pub fn name(self) -> &'static str {
        match self {
            ColumnElement::F64 => "f64",
            ColumnElement::I64 => "i64",
            ColumnElement::Bool => "bool",
            ColumnElement::Utf8 => "utf8",
        }
    }

    fn from_name(name: &str) -> Option<ColumnElement> {
        Some(match name {
            "f64" => ColumnElement::F64,
            "i64" => ColumnElement::I64,
            "bool" => ColumnElement::Bool,
            "utf8" => ColumnElement::Utf8,
            _ => return None,
        })
    }
}

/// `kernel_codec._column_element`: the element and whether `None` occurs, for a list whose
/// non-`None` items are all `float`, all `int` (within `i64`), all `bool` or all `str`.
fn column_shape(values: &[PyValue]) -> Option<(ColumnElement, bool)> {
    let mut element = None;
    let mut missing = false;
    let mut out_of_range = false;
    for value in values {
        let kind = match value {
            PyValue::None => {
                missing = true;
                continue;
            }
            PyValue::Float(_) => ColumnElement::F64,
            PyValue::Int(_) => ColumnElement::I64,
            PyValue::BigInt(_) => {
                out_of_range = true;
                ColumnElement::I64
            }
            PyValue::Bool(_) => ColumnElement::Bool,
            PyValue::Str(_) => ColumnElement::Utf8,
            _ => return None,
        };
        match element {
            None => element = Some(kind),
            Some(seen) if seen != kind => return None,
            Some(_) => {}
        }
    }
    let element = element?;
    (!out_of_range).then_some((element, missing))
}

struct SinkColumn {
    id: String,
    element: ColumnElement,
    present: Option<Vec<u8>>,
    data: Vec<u8>,
    offsets: Option<Vec<u8>>,
    length: usize,
}

/// The column-valued lists of one call, written as one `stage: "vector"` store
/// (`kernel_codec.ColumnSink`).
#[derive(Default)]
pub struct ColumnSink {
    columns: Vec<SinkColumn>,
}

impl ColumnSink {
    pub fn new() -> ColumnSink {
        ColumnSink::default()
    }

    pub fn is_empty(&self) -> bool {
        self.columns.is_empty()
    }

    /// The column id for `values`, or `None` when the list stays inline JSON.
    pub fn add(&mut self, values: &[PyValue]) -> Result<Option<String>, KernelError> {
        if values.len() < COLUMN_MIN {
            return Ok(None);
        }
        let Some((element, missing)) = column_shape(values) else {
            return Ok(None);
        };
        let id = format!("c{}", self.columns.len());
        let present = missing.then(|| values.iter().map(|v| u8::from(!v.is_none())).collect());
        let mut data = Vec::new();
        let mut offsets = None;
        match element {
            ColumnElement::F64 => {
                for value in values {
                    let x = value.as_float().unwrap_or(0.0);
                    data.extend_from_slice(&x.to_le_bytes());
                }
            }
            ColumnElement::I64 => {
                for value in values {
                    data.extend_from_slice(&value.as_int().unwrap_or(0).to_le_bytes());
                }
            }
            ColumnElement::Bool => {
                data.extend(
                    values
                        .iter()
                        .map(|v| u8::from(v.as_bool().unwrap_or(false))),
                );
            }
            ColumnElement::Utf8 => {
                let mut words = Vec::with_capacity((values.len() + 1) * 8);
                words.extend_from_slice(&0u64.to_le_bytes());
                for value in values {
                    if let Some(text) = value.as_str() {
                        data.extend_from_slice(text.as_bytes());
                    }
                    words.extend_from_slice(&(data.len() as u64).to_le_bytes());
                }
                offsets = Some(words);
            }
        }
        self.columns.push(SinkColumn {
            id: id.clone(),
            element,
            present,
            data,
            offsets,
            length: values.len(),
        });
        Ok(Some(id))
    }

    /// The store's bytes, or `None` when no list became a column (no file is written).
    pub fn to_bytes<'a>(&'a self, producer: &str) -> Result<Option<Vec<u8>>, KernelError> {
        if self.columns.is_empty() {
            return Ok(None);
        }
        // Blocks in placement order, each at an 8-byte aligned offset (`_Blocks.place`).
        let mut cursor = 0usize;
        let mut parts: Vec<(usize, &[u8])> = Vec::new();
        let mut place = |bytes: &'a [u8]| {
            let block = Dict::new()
                .with("offset", cursor as i64)
                .with("length", bytes.len() as i64);
            parts.push((cursor, bytes));
            cursor = align(cursor + bytes.len());
            PyValue::Dict(block)
        };
        let mut entries = Vec::with_capacity(self.columns.len());
        for column in &self.columns {
            let mut entry = Dict::new()
                .with("name", column.id.as_str())
                .with("element", column.element.name())
                .with("length", column.length as i64);
            if let Some(present) = &column.present {
                entry.insert("present", place(present));
            }
            entry.insert("data", place(&column.data));
            if let Some(offsets) = &column.offsets {
                entry.insert("text_offsets", place(offsets));
            }
            entries.push(PyValue::Dict(entry));
        }
        let header = Dict::new()
            .with("format", crate::store::FORMAT_NAME)
            .with("version", i64::from(crate::store::FORMAT_VERSION))
            .with("stage", "vector")
            .with("producer", producer)
            .with("codec", CODEC_VERSION)
            .with("columns", PyValue::List(entries));
        let header = to_json(&PyValue::Dict(header))?;
        let header_len = u32::try_from(header.len())
            .map_err(|_| KernelError::codec("vector store header longer than 4 GiB"))?;
        let data_start = align(16 + header.len());
        let mut out = Vec::with_capacity(data_start + cursor);
        out.extend_from_slice(&crate::store::MAGIC);
        out.extend_from_slice(&crate::store::FORMAT_VERSION.to_le_bytes());
        out.extend_from_slice(&header_len.to_le_bytes());
        out.extend_from_slice(header.as_bytes());
        out.resize(data_start, 0);
        for (offset, bytes) in parts {
            out.resize(data_start + offset, 0);
            out.extend_from_slice(bytes);
        }
        out.resize(align(out.len()), 0);
        Ok(Some(out))
    }
}

struct StoredColumn {
    element: ColumnElement,
    length: usize,
    present: Option<Vec<u8>>,
    data: Vec<u8>,
    offsets: Vec<usize>,
}

/// A per-call vector store, validated as `ColumnStore::parse` validates a decode store: magic,
/// version, format, stage `vector`, codec 1; every block 8-byte aligned, inside the data
/// section and exactly the length its column implies (`present` one byte per item, 0 or 1;
/// `bool` bytes 0 or 1; `utf8` offsets rising from 0 to the text length over valid UTF-8).
pub struct VectorStore {
    columns: HashMap<String, StoredColumn>,
}

impl VectorStore {
    pub fn read(path: &std::path::Path) -> Result<VectorStore, KernelError> {
        let bytes = std::fs::read(path)
            .map_err(|error| KernelError::codec(format!("{}: {error}", path.display())))?;
        VectorStore::parse(&bytes)
            .map_err(|error| KernelError::codec(format!("{}: {error}", path.display())))
    }

    pub fn parse(bytes: &[u8]) -> Result<VectorStore, KernelError> {
        let fail = |message: String| Err(KernelError::codec(message));
        if bytes.len() < 16 || bytes[..8] != crate::store::MAGIC {
            return fail("not a coach column store (magic)".to_owned());
        }
        let version = u32::from_le_bytes([bytes[8], bytes[9], bytes[10], bytes[11]]);
        if version != crate::store::FORMAT_VERSION {
            return fail(format!("column store version {version} is not supported"));
        }
        let header_len = u32::from_le_bytes([bytes[12], bytes[13], bytes[14], bytes[15]]) as usize;
        let header = bytes
            .get(16..16 + header_len)
            .and_then(|h| std::str::from_utf8(h).ok())
            .ok_or_else(|| KernelError::codec("header runs past the end or is not UTF-8"))?;
        let header = decode(header, None)?;
        let field = |name: &str| {
            header
                .get(name)
                .ok_or_else(|| KernelError::codec(format!("header field {name:?} missing")))
        };
        if field("format")?.as_str() != Some(crate::store::FORMAT_NAME)
            || field("version")?.as_int() != Some(i64::from(crate::store::FORMAT_VERSION))
        {
            return fail("header format or version does not match the file".to_owned());
        }
        if field("stage")?.as_str() != Some("vector") {
            return fail("stage is not vector".to_owned());
        }
        if field("codec")?.as_int() != Some(CODEC_VERSION) {
            return fail("codec version is not 1".to_owned());
        }
        let data = bytes.get(align(16 + header_len)..).unwrap_or_default();
        let Some(entries) = field("columns")?.as_list() else {
            return fail("columns is not a list".to_owned());
        };
        let mut columns = HashMap::with_capacity(entries.len());
        for entry in entries {
            let name = entry
                .get("name")
                .and_then(PyValue::as_str)
                .filter(|name| !name.is_empty())
                .ok_or_else(|| KernelError::codec("column without a name"))?;
            let column = Self::column(entry, data)
                .map_err(|error| KernelError::codec(format!("column {name:?}: {error}")))?;
            if columns.insert(name.to_owned(), column).is_some() {
                return fail(format!("column {name:?} repeated"));
            }
        }
        Ok(VectorStore { columns })
    }

    fn column(entry: &PyValue, data: &[u8]) -> Result<StoredColumn, String> {
        let element = entry
            .get("element")
            .and_then(PyValue::as_str)
            .and_then(ColumnElement::from_name)
            .ok_or("unknown element")?;
        let length = entry
            .get("length")
            .and_then(PyValue::as_int)
            .and_then(|n| usize::try_from(n).ok())
            .ok_or("length is not a non-negative int")?;
        let block = |key: &str, expected: Option<usize>| -> Result<Option<Vec<u8>>, String> {
            let Some(spec) = entry.get(key) else {
                return Ok(None);
            };
            let number = |name: &str| {
                spec.get(name)
                    .and_then(PyValue::as_int)
                    .and_then(|n| usize::try_from(n).ok())
                    .ok_or(format!("{key} block {name} is not a non-negative int"))
            };
            let (offset, size) = (number("offset")?, number("length")?);
            if offset % ALIGN != 0 {
                return Err(format!(
                    "{key} block offset {offset} is not a multiple of 8"
                ));
            }
            if let Some(expected) = expected
                && size != expected
            {
                return Err(format!("{key} block of {size} bytes, expected {expected}"));
            }
            let bytes = offset
                .checked_add(size)
                .and_then(|end| data.get(offset..end))
                .ok_or(format!("{key} block runs past the end of the file"))?;
            Ok(Some(bytes.to_vec()))
        };
        let present = block("present", Some(length))?;
        if present.as_ref().is_some_and(|p| p.iter().any(|&b| b > 1)) {
            return Err("present bytes must be 0 or 1".to_owned());
        }
        let width = match element {
            ColumnElement::F64 | ColumnElement::I64 => Some(8),
            ColumnElement::Bool => Some(1),
            ColumnElement::Utf8 => None,
        };
        let expected = width.map(|w| length.checked_mul(w).ok_or("length overflows"));
        let expected = expected.transpose()?;
        let column_data = block("data", expected)?.ok_or("data block missing")?;
        let mut offsets = Vec::new();
        if element == ColumnElement::Utf8 {
            let words = (length + 1).checked_mul(8).ok_or("length overflows")?;
            let raw =
                block("text_offsets", Some(words))?.ok_or("utf8 column without text_offsets")?;
            offsets = raw
                .as_chunks::<8>()
                .0
                .iter()
                .map(|word| usize::try_from(u64::from_le_bytes(*word)).unwrap_or(usize::MAX))
                .collect();
            if offsets.first() != Some(&0)
                || offsets.last() != Some(&column_data.len())
                || offsets.windows(2).any(|pair| pair[0] > pair[1])
            {
                return Err("text offsets must rise from 0 to the text length".to_owned());
            }
            if offsets
                .windows(2)
                .any(|pair| std::str::from_utf8(&column_data[pair[0]..pair[1]]).is_err())
            {
                return Err("an item is not UTF-8".to_owned());
            }
        } else if entry.get("text_offsets").is_some() {
            return Err(format!("text_offsets on a {} column", element.name()));
        }
        if element == ColumnElement::Bool && column_data.iter().any(|&b| b > 1) {
            return Err("bool bytes must be 0 or 1".to_owned());
        }
        Ok(StoredColumn {
            element,
            length,
            present,
            data: column_data,
            offsets,
        })
    }

    pub fn len(&self) -> usize {
        self.columns.len()
    }

    pub fn is_empty(&self) -> bool {
        self.columns.is_empty()
    }

    /// Column `id` as the list it encodes.
    pub fn values(&self, id: &str) -> Result<PyValue, KernelError> {
        let column = self.columns.get(id).ok_or_else(|| {
            KernelError::codec(format!("column {id:?} is not in the vector store"))
        })?;
        let mut items = Vec::with_capacity(column.length);
        for index in 0..column.length {
            if column.present.as_ref().is_some_and(|p| p[index] == 0) {
                items.push(PyValue::None);
                continue;
            }
            let word = |i: usize| {
                let mut bytes = [0u8; 8];
                bytes.copy_from_slice(&column.data[i * 8..i * 8 + 8]);
                bytes
            };
            items.push(match column.element {
                ColumnElement::F64 => PyValue::Float(f64::from_le_bytes(word(index))),
                ColumnElement::I64 => PyValue::Int(i64::from_le_bytes(word(index))),
                ColumnElement::Bool => PyValue::Bool(column.data[index] == 1),
                ColumnElement::Utf8 => PyValue::Str(
                    // Checked UTF-8 in parse.
                    String::from_utf8_lossy(
                        &column.data[column.offsets[index]..column.offsets[index + 1]],
                    )
                    .into_owned(),
                ),
            });
        }
        Ok(PyValue::List(items))
    }
}

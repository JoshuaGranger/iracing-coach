//! `json.dumps(value, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=...,
//! allow_nan=...)` of CPython 3.12's C encoder (`Modules/_json.c`), byte for byte, over the
//! [`Graph`] of the worker's object.
//!
//! What the encoder does, in its order, and what this reproduces:
//!
//! - `None`, `True`, `False`: `null`, `true`, `false`. An `int`: its `repr` (beyond `i64` under
//!   `sys.get_int_max_str_digits()`, else `ValueError`). A `float`: `float.__repr__`
//!   (`coach_numerics::float_repr`); a non-finite one is `NaN`, `Infinity`, `-Infinity`, or with
//!   `allow_nan=False` `ValueError: Out of range float values are not JSON compliant: <repr>`.
//! - A `str`: `"..."` with `\"`, `\\`, `\b`, `\f`, `\n`, `\r`, `\t` and `\u00XX` for the other
//!   controls; with `ensure_ascii` also `\uXXXX` (lowercase hex) for everything outside
//!   `' '..='~'`, astral characters as a surrogate pair, lone surrogates as themselves.
//! - A list or tuple: `[]` when empty, else (after the circular-reference check) the items
//!   joined by `,`. A dict: `{}` when empty, else the check, then the items sorted by key
//!   (`list.sort` of `(key, value)` tuples: keys are never equal, so the key decides; a key
//!   pair Python cannot order is `TypeError: '<' not supported between instances of 'A' and
//!   'B'` from the first such comparison CPython's timsort makes), each key converted
//!   (`str` as is, `float` as above, `True`/`False`/`None` as `true`/`false`/`null`, `int` its
//!   `repr`) and written as a string, `:`, the value.
//! - `bytes` (`default=str`): the string `repr(bytes)`.
//! - Anything else the seam's pickler meets is a [`Node::Fragment`] (Python's own text for it),
//!   a [`Node::Raised`] (the exception Python raised encoding it) or a [`Node::Unsupported`].
//!
//! Cycles raise `ValueError: Circular reference detected` where the encoder's markers do. A
//! value nested more than [`MAX_DEPTH`] containers deep, a set, a frozenset, a bytearray, or a
//! dict key that is not exactly `str`, `int` (within `i64`), `float`, `bool` or `None` is not
//! encoded here: the call is written by the oracle instead ([`Stop::Fallback`]), so the result
//! is Python's by construction (recursion limits, `repr` of set members and key types with
//! their own comparisons stay Python's). The seam counts every fallback and the parity gate
//! requires none on the corpus.

use std::cmp::Ordering;

use coach_numerics::{Num, float_repr_into, int_repr_from_le_bytes, py_lt, sort_permutation};

use super::pickle::{Graph, Node, NodeId};

/// Containers nested deeper than this are written by the oracle (its recursion limit decides).
pub const MAX_DEPTH: usize = 200;

#[derive(Clone, Copy, Debug)]
pub struct Options {
    pub ensure_ascii: bool,
    pub allow_nan: bool,
    /// `sys.get_int_max_str_digits()`; `None` when it is 0 (no limit).
    pub max_str_digits: Option<u64>,
}

/// Why encoding stopped before the end.
#[derive(Debug, PartialEq)]
pub enum Stop {
    /// A builtin exception CPython raises for the same object: class and message.
    Raise {
        class: &'static str,
        message: String,
    },
    /// The exception the seam recorded for a value at this index (it re-raises that object).
    Python(u64),
    /// The call is written by the oracle; the reason.
    Fallback(String),
}

fn raise(class: &'static str, message: impl Into<String>) -> Stop {
    Stop::Raise {
        class,
        message: message.into(),
    }
}

/// Appends `json.dumps` of the graph's root to `out`.
pub fn encode(graph: &Graph, options: &Options, out: &mut Vec<u8>) -> Result<(), Stop> {
    encode_with(graph, options, out, true)
}

/// [`encode`]; `split` allows the large lists near the root to be encoded by several threads
/// (the result is the same either way; the tests compare both).
pub fn encode_with(
    graph: &Graph,
    options: &Options,
    out: &mut Vec<u8>,
    split: bool,
) -> Result<(), Stop> {
    let mut encoder = Encoder {
        graph,
        options: *options,
        active: vec![false; graph.nodes.len()],
        orders: Vec::new(),
        split: split && graph.nodes.len() >= SPLIT_MIN_NODES,
    };
    encoder.value(graph.root, 0, out)
}

/// A graph this large has its lists of at least [`SPLIT_MIN_ITEMS`] items within
/// [`SPLIT_MAX_DEPTH`] of the root encoded by up to [`SPLIT_THREADS`] threads, each a
/// contiguous run of items into its own buffer; the buffers are joined in order and the first
/// stop in order is the result. That is the sequential result: the sequential encoder stops at
/// the first stop in traversal order, and each run meets its stops in traversal order. Each
/// thread starts with the markers of the path above the list. On the race this is the list of
/// laps in `lap_traces`.
const SPLIT_MIN_NODES: usize = 200_000;
const SPLIT_MIN_ITEMS: usize = 8;
const SPLIT_MAX_DEPTH: usize = 3;
const SPLIT_THREADS: usize = 4;

struct Encoder<'a> {
    graph: &'a Graph,
    options: Options,
    /// The encoder's markers: containers on the current path.
    active: Vec<bool>,
    /// Recent sorted orders by the dict's key objects (the pickle memo makes every use of a key
    /// string the same node): the trace bins repeat a few key sets tens of thousands of times,
    /// and the same key values always sort the same way.
    orders: Vec<(Vec<NodeId>, Vec<usize>)>,
    /// This encoder may split a list across threads (the root encoder of a large graph).
    split: bool,
}

/// How many key sets [`Encoder::orders`] remembers.
const ORDER_CACHE: usize = 16;

/// A dict key Python can compare and convert.
#[derive(Clone, Copy)]
enum Key<'a> {
    Str(&'a [u8]),
    Int(i64),
    Float(f64),
    Bool(bool),
    None,
}

impl Key<'_> {
    fn type_name(self) -> &'static str {
        match self {
            Key::Str(_) => "str",
            Key::Int(_) => "int",
            Key::Float(_) => "float",
            Key::Bool(_) => "bool",
            Key::None => "NoneType",
        }
    }

    fn number(self) -> Option<Num> {
        match self {
            Key::Int(value) => Some(Num::Int(value)),
            Key::Float(value) => Some(Num::Float(value)),
            Key::Bool(value) => Some(Num::Int(i64::from(value))),
            Key::Str(_) | Key::None => None,
        }
    }
}

/// Python `a < b` for two keys: `Err` with the `TypeError` message when the types do not order.
fn key_lt(a: Key<'_>, b: Key<'_>) -> Result<bool, String> {
    match (a, b) {
        (Key::Str(x), Key::Str(y)) => Ok(x < y),
        _ => match (a.number(), b.number()) {
            (Some(x), Some(y)) => Ok(py_lt(x, y)),
            _ => Err(format!(
                "'<' not supported between instances of '{}' and '{}'",
                a.type_name(),
                b.type_name()
            )),
        },
    }
}

impl<'a> Encoder<'a> {
    fn value(&mut self, id: NodeId, depth: usize, out: &mut Vec<u8>) -> Result<(), Stop> {
        let graph: &'a Graph = self.graph;
        match &graph.nodes[id as usize] {
            Node::None => out.extend_from_slice(b"null"),
            Node::Bool(true) => out.extend_from_slice(b"true"),
            Node::Bool(false) => out.extend_from_slice(b"false"),
            Node::Int(value) => push_int(*value, out),
            Node::BigInt(bytes) => {
                let text = int_repr_from_le_bytes(bytes, self.options.max_str_digits)
                    .ok_or_else(|| digits_error(self.options.max_str_digits))?;
                out.extend_from_slice(text.as_bytes());
            }
            Node::Float(value) => self.float(*value, out)?,
            Node::Str(text) => self.string(text, out),
            Node::Bytes(bytes) => {
                let text = bytes_repr(bytes);
                self.string(text.as_bytes(), out);
            }
            Node::List(items) | Node::Tuple(items) => {
                if items.is_empty() {
                    out.extend_from_slice(b"[]");
                    return Ok(());
                }
                self.enter(id, depth)?;
                out.push(b'[');
                if self.split && depth <= SPLIT_MAX_DEPTH && items.len() >= SPLIT_MIN_ITEMS {
                    self.items_in_threads(items, depth + 1, out)?;
                } else {
                    for (index, &item) in items.iter().enumerate() {
                        if index > 0 {
                            out.push(b',');
                        }
                        self.value(item, depth + 1, out)?;
                    }
                }
                out.push(b']');
                self.active[id as usize] = false;
            }
            Node::Dict(items) => {
                if items.is_empty() {
                    out.extend_from_slice(b"{}");
                    return Ok(());
                }
                self.enter(id, depth)?;
                out.push(b'{');
                let keys = items
                    .iter()
                    .map(|&(key, _)| dict_key(graph, key))
                    .collect::<Result<Vec<Key<'_>>, Stop>>()?;
                let order = self.order(items, &keys)?;
                for (position, &index) in order.iter().enumerate() {
                    if position > 0 {
                        out.push(b',');
                    }
                    self.key_text(keys[index], out)?;
                    out.push(b':');
                    self.value(items[index].1, depth + 1, out)?;
                }
                out.push(b'}');
                self.active[id as usize] = false;
            }
            Node::Fragment(text) => out.extend_from_slice(text),
            Node::Raised(index) => return Err(Stop::Python(*index)),
            Node::Unsupported(reason) => return Err(Stop::Fallback(reason.clone())),
            Node::Marker => {
                return Err(Stop::Fallback(
                    "the writer marker function as a value".into(),
                ));
            }
        }
        Ok(())
    }

    /// A list's items, comma separated, encoded in contiguous runs by several threads.
    fn items_in_threads(
        &mut self,
        items: &[NodeId],
        depth: usize,
        out: &mut Vec<u8>,
    ) -> Result<(), Stop> {
        let threads = std::thread::available_parallelism()
            .map_or(1, |n| n.get())
            .clamp(1, SPLIT_THREADS)
            .min(items.len());
        let run = items.len().div_ceil(threads);
        let results: Vec<Result<Vec<u8>, Stop>> = std::thread::scope(|scope| {
            let handles: Vec<_> = items
                .chunks(run)
                .map(|part| {
                    let mut encoder = Encoder {
                        graph: self.graph,
                        options: self.options,
                        active: self.active.clone(),
                        orders: Vec::new(),
                        split: false,
                    };
                    scope.spawn(move || {
                        let mut buffer = Vec::new();
                        for (index, &item) in part.iter().enumerate() {
                            if index > 0 {
                                buffer.push(b',');
                            }
                            encoder.value(item, depth, &mut buffer)?;
                        }
                        Ok(buffer)
                    })
                })
                .collect();
            handles
                .into_iter()
                .map(|handle| handle.join().expect("a writer thread panicked"))
                .collect()
        });
        for (index, result) in results.into_iter().enumerate() {
            let buffer = result?;
            if index > 0 {
                out.push(b',');
            }
            out.extend_from_slice(&buffer);
        }
        Ok(())
    }

    /// The dict's item order under `sort_keys=True`, from [`Encoder::orders`] when the same key
    /// objects were sorted before (a sort that raised never enters the cache).
    fn order(&mut self, items: &[(NodeId, NodeId)], keys: &[Key<'_>]) -> Result<Vec<usize>, Stop> {
        let same = |ids: &[NodeId]| {
            ids.len() == items.len() && ids.iter().zip(items).all(|(&id, &(key, _))| id == key)
        };
        if let Some(position) = self.orders.iter().position(|(ids, _)| same(ids)) {
            let entry = self.orders.remove(position);
            let order = entry.1.clone();
            self.orders.push(entry);
            return Ok(order);
        }
        let order = sort_keys(keys)?;
        if self.orders.len() == ORDER_CACHE {
            self.orders.remove(0);
        }
        self.orders
            .push((items.iter().map(|&(key, _)| key).collect(), order.clone()));
        Ok(order)
    }

    /// The markers check of a non-empty container, and the depth limit.
    fn enter(&mut self, id: NodeId, depth: usize) -> Result<(), Stop> {
        if depth >= MAX_DEPTH {
            return Err(Stop::Fallback(format!(
                "containers nested more than {MAX_DEPTH} deep"
            )));
        }
        let slot = &mut self.active[id as usize];
        if *slot {
            return Err(raise("ValueError", "Circular reference detected"));
        }
        *slot = true;
        Ok(())
    }

    fn key_text(&self, key: Key<'_>, out: &mut Vec<u8>) -> Result<(), Stop> {
        match key {
            Key::Str(text) => self.string(text, out),
            Key::Int(value) => {
                out.push(b'"');
                push_int(value, out);
                out.push(b'"');
            }
            Key::Float(value) => {
                // Digits, '.', 'e', signs, "NaN" or "Infinity": nothing to escape.
                out.push(b'"');
                self.float(value, out)?;
                out.push(b'"');
            }
            Key::Bool(true) => out.extend_from_slice(b"\"true\""),
            Key::Bool(false) => out.extend_from_slice(b"\"false\""),
            Key::None => out.extend_from_slice(b"\"null\""),
        }
        Ok(())
    }

    /// `float.__repr__` (`coach_numerics::float_repr_into`, no allocation), or the non-finite
    /// spelling, or the `allow_nan=False` error.
    fn float(&self, value: f64, out: &mut Vec<u8>) -> Result<(), Stop> {
        if value.is_finite() {
            float_repr_into(value, out);
            return Ok(());
        }
        if !self.options.allow_nan {
            let shown = if value.is_nan() {
                "nan"
            } else if value > 0.0 {
                "inf"
            } else {
                "-inf"
            };
            return Err(raise(
                "ValueError",
                format!("Out of range float values are not JSON compliant: {shown}"),
            ));
        }
        out.extend_from_slice(if value.is_nan() {
            b"NaN"
        } else if value > 0.0 {
            b"Infinity"
        } else {
            b"-Infinity"
        });
        Ok(())
    }

    fn string(&self, text: &[u8], out: &mut Vec<u8>) {
        if self.options.ensure_ascii {
            escape_ascii(text, out);
        } else {
            escape_unicode(text, out);
        }
    }
}

/// The key as Python compares it; anything other than an exact `str`, `int` within `i64`,
/// `float`, `bool` or `None` is written by the oracle.
fn dict_key(graph: &Graph, id: NodeId) -> Result<Key<'_>, Stop> {
    Ok(match &graph.nodes[id as usize] {
        Node::Str(text) => Key::Str(text),
        Node::Int(value) => Key::Int(*value),
        Node::Float(value) => Key::Float(*value),
        Node::Bool(value) => Key::Bool(*value),
        Node::None => Key::None,
        Node::BigInt(_) => {
            return Err(Stop::Fallback("a dict key int beyond i64".into()));
        }
        _ => return Err(Stop::Fallback("a dict key of another type".into())),
    })
}

/// `repr(int)` of an `i64`.
fn push_int(value: i64, out: &mut Vec<u8>) {
    let mut digits = [0u8; 20];
    let mut at = digits.len();
    let mut rest = value.unsigned_abs();
    loop {
        at -= 1;
        digits[at] = b'0' + (rest % 10) as u8;
        rest /= 10;
        if rest == 0 {
            break;
        }
    }
    if value < 0 {
        out.push(b'-');
    }
    out.extend_from_slice(&digits[at..]);
}

fn digits_error(limit: Option<u64>) -> Stop {
    raise(
        "ValueError",
        format!(
            "Exceeds the limit ({} digits) for integer string conversion; use sys.set_int_max_str_digits() to increase the limit",
            limit.unwrap_or(0)
        ),
    )
}

/// The dict's item order under `sort_keys=True`.
fn sort_keys(keys: &[Key<'_>]) -> Result<Vec<usize>, Stop> {
    if keys.iter().all(|key| matches!(key, Key::Str(_))) {
        // Distinct strings: any correct sort is timsort's order (code point order is the byte
        // order of UTF-8, surrogates included).
        let mut order: Vec<usize> = (0..keys.len()).collect();
        order.sort_unstable_by(|&a, &b| match (keys[a], keys[b]) {
            (Key::Str(x), Key::Str(y)) => x.cmp(y),
            _ => Ordering::Equal,
        });
        return Ok(order);
    }
    // Mixed keys: CPython's comparison sequence decides both the order (NaN keys) and which
    // TypeError is raised first.
    let mut error: Option<String> = None;
    let order = sort_permutation(
        keys.len(),
        &mut |a: usize, b: usize| {
            if error.is_some() {
                return false;
            }
            match key_lt(keys[a], keys[b]) {
                Ok(less) => less,
                Err(message) => {
                    error = Some(message);
                    false
                }
            }
        },
        false,
    );
    match error {
        Some(message) => Err(raise("TypeError", message)),
        None => Ok(order),
    }
}

const HEX: &[u8; 16] = b"0123456789abcdef";

fn push_u_escape(code: u32, out: &mut Vec<u8>) {
    out.extend_from_slice(b"\\u");
    for shift in [12, 8, 4, 0] {
        out.push(HEX[((code >> shift) & 0xF) as usize]);
    }
}

fn short_escape(byte: u8) -> Option<&'static [u8]> {
    Some(match byte {
        b'"' => b"\\\"",
        b'\\' => b"\\\\",
        b'\n' => b"\\n",
        b'\r' => b"\\r",
        b'\t' => b"\\t",
        0x08 => b"\\b",
        0x0C => b"\\f",
        _ => return None,
    })
}

/// `ensure_ascii=False` (`escape_unicode`): only quotes, backslashes and controls are escaped.
fn escape_unicode(text: &[u8], out: &mut Vec<u8>) {
    out.reserve(text.len() + 2);
    out.push(b'"');
    let mut start = 0;
    for (index, &byte) in text.iter().enumerate() {
        if byte >= 0x20 && byte != b'"' && byte != b'\\' {
            continue;
        }
        out.extend_from_slice(&text[start..index]);
        match short_escape(byte) {
            Some(escape) => out.extend_from_slice(escape),
            None => push_u_escape(u32::from(byte), out),
        }
        start = index + 1;
    }
    out.extend_from_slice(&text[start..]);
    out.push(b'"');
}

/// `ensure_ascii=True` (`ascii_escape_unicode`).
fn escape_ascii(text: &[u8], out: &mut Vec<u8>) {
    out.reserve(text.len() + 2);
    out.push(b'"');
    let mut index = 0;
    while index < text.len() {
        let byte = text[index];
        if byte < 0x80 {
            index += 1;
            if (b' '..=b'~').contains(&byte) && byte != b'"' && byte != b'\\' {
                out.push(byte);
            } else if let Some(escape) = short_escape(byte) {
                out.extend_from_slice(escape);
            } else {
                push_u_escape(u32::from(byte), out);
            }
            continue;
        }
        // Surrogatepass UTF-8 (validated when read): a 2, 3 or 4 byte sequence.
        let len = match byte {
            0xC0..=0xDF => 2,
            0xE0..=0xEF => 3,
            _ => 4,
        };
        let mut code = u32::from(byte) & (0x7F >> len);
        for &next in &text[index + 1..(index + len).min(text.len())] {
            code = (code << 6) | u32::from(next & 0x3F);
        }
        index += len;
        if code >= 0x10000 {
            let value = code - 0x10000;
            push_u_escape(0xD800 | (value >> 10), out);
            push_u_escape(0xDC00 | (value & 0x3FF), out);
        } else {
            push_u_escape(code, out);
        }
    }
    out.push(b'"');
}

/// `repr(bytes)` (what `default=str` returns for a `bytes` value).
pub fn bytes_repr(bytes: &[u8]) -> String {
    let quote = if bytes.contains(&b'\'') && !bytes.contains(&b'"') {
        '"'
    } else {
        '\''
    };
    let mut out = String::with_capacity(bytes.len() + 3);
    out.push('b');
    out.push(quote);
    for &byte in bytes {
        match byte {
            b'\\' => out.push_str("\\\\"),
            b'\t' => out.push_str("\\t"),
            b'\n' => out.push_str("\\n"),
            b'\r' => out.push_str("\\r"),
            _ if char::from(byte) == quote => {
                out.push('\\');
                out.push(quote);
            }
            0x20..=0x7E => out.push(char::from(byte)),
            _ => {
                out.push_str("\\x");
                out.push(char::from(HEX[usize::from(byte >> 4)]));
                out.push(char::from(HEX[usize::from(byte & 0xF)]));
            }
        }
    }
    out.push(quote);
    out
}

/// The first run of surrogate code points in UTF-8 output (what `str.encode("utf-8")` refuses):
/// `(start, end, code points)` with positions counted in code points, or None.
pub fn surrogate_run(text: &[u8]) -> Option<(u64, u64, Vec<u32>)> {
    let is_surrogate_at =
        |at: usize| text.get(at) == Some(&0xED) && text.get(at + 1).is_some_and(|b| *b >= 0xA0);
    let first = (0..text.len()).find(|&at| is_surrogate_at(at))?;
    let start = text[..first].iter().filter(|&&b| b & 0xC0 != 0x80).count() as u64;
    let mut codes = Vec::new();
    let mut at = first;
    while is_surrogate_at(at) && at + 2 < text.len() + 1 {
        let code = (u32::from(text[at] & 0x0F) << 12)
            | (u32::from(text[at + 1] & 0x3F) << 6)
            | u32::from(text.get(at + 2).copied().unwrap_or(0x80) & 0x3F);
        codes.push(code);
        at += 3;
    }
    let end = start + codes.len() as u64;
    Some((start, end, codes))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bytes_repr_quotes() {
        assert_eq!(bytes_repr(b"x'\"\x00\x80"), r#"b'x\'"\x00\x80'"#);
        assert_eq!(bytes_repr(b"it's"), r#"b"it's""#);
        assert_eq!(bytes_repr(b""), "b''");
    }

    /// A graph large enough to split: a list of float lists, with stops planted in its runs.
    fn big_graph(stops: Vec<(usize, Node)>) -> Graph {
        let mut nodes = Vec::new();
        let mut laps = Vec::new();
        for lap in 0..40u32 {
            let mut values = Vec::new();
            for sample in 0..6000u32 {
                values.push(nodes.len() as NodeId);
                nodes.push(Node::Float(f64::from(lap) * 0.37 + f64::from(sample) / 7.0));
            }
            laps.push(nodes.len() as NodeId);
            nodes.push(Node::List(values));
        }
        let root = nodes.len() as NodeId;
        nodes.push(Node::List(laps.clone()));
        for (lap, node) in stops {
            let Node::List(values) = &nodes[laps[lap] as usize] else {
                unreachable!("a lap is a list")
            };
            let target = values[17] as usize;
            nodes[target] = node;
        }
        Graph { nodes, root }
    }

    fn both(graph: &Graph) -> (Result<Vec<u8>, Stop>, Result<Vec<u8>, Stop>) {
        let options = Options {
            ensure_ascii: false,
            allow_nan: false,
            max_str_digits: Some(4300),
        };
        let run = |split| {
            let mut out = Vec::new();
            encode_with(graph, &options, &mut out, split).map(|()| out)
        };
        (run(true), run(false))
    }

    #[test]
    fn threads_give_the_sequential_result() {
        let (split, sequential) = both(&big_graph(Vec::new()));
        assert_eq!(split.unwrap(), sequential.unwrap());
        // The first stop in traversal order wins, whichever thread meets which stop first.
        let graph = big_graph(vec![
            (31, Node::Float(f64::NAN)),
            (12, Node::Raised(4)),
            (35, Node::Raised(9)),
        ]);
        let (split, sequential) = both(&graph);
        assert_eq!(split.unwrap_err(), Stop::Python(4));
        assert_eq!(sequential.unwrap_err(), Stop::Python(4));
        // A cycle through the root, met inside a run, after a later run's NaN.
        let root = big_graph(Vec::new()).root;
        let graph = big_graph(vec![
            (25, Node::List(vec![root])),
            (38, Node::Float(f64::NAN)),
        ]);
        let (split, sequential) = both(&graph);
        let expected = Stop::Raise {
            class: "ValueError",
            message: "Circular reference detected".into(),
        };
        assert_eq!(split.unwrap_err(), expected);
        assert_eq!(sequential.unwrap_err(), expected);
    }

    #[test]
    fn surrogate_runs() {
        let mut text = b"[\"a".to_vec();
        text.extend([0xED, 0xA0, 0x80, 0xED, 0xB0, 0x80]);
        text.extend(b"b\"]");
        assert_eq!(surrogate_run(&text), Some((3, 5, vec![0xD800, 0xDC00])));
        assert_eq!(surrogate_run("é€😀".as_bytes()), None);
    }
}

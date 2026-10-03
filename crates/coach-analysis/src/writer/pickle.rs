//! The value graph the worker hands to the writer: a pickle (protocol 5) written by CPython
//! 3.12's C pickler from the worker's final analysis object (`native_stages._WriterPickler`).
//!
//! Pickle is the hand-off because CPython writes it in C from the live objects without
//! formatting a single float (8 bytes per float, every shared object once): 0.07 s for the
//! 203 MB race's analysis against 0.53 s for the `json.dumps` it replaces
//! (`docs/08-rewrite/port/phase3-writer.md` section 2). Only data is read: no opcode can name a
//! class or call anything except the one marker function the request names, and the reader
//! builds nothing but the nodes below. The seam pickles no marker since the Codex phase-3 review
//! (finding 1: any object outside the exact types falls back before the writer runs); the
//! reader still accepts one, as the request format allows.
//!
//! The graph keeps what `json.dumps` can observe: `int`, `bool` and `float` apart, float bits,
//! dict insertion order, tuples beside lists, `bytes`, strings as CPython's `surrogatepass`
//! UTF-8 (a lone surrogate is one code point), and object identity (the pickle memo), so a
//! shared object is written twice and a cycle is found exactly where Python finds it.

use std::fmt;

/// An index into [`Graph::nodes`].
pub type NodeId = u32;

/// One Python object.
#[derive(Debug)]
pub enum Node {
    None,
    Bool(bool),
    Int(i64),
    /// An `int` outside `i64`: little-endian two's complement bytes (pickle `LONG1`/`LONG4`).
    BigInt(Box<[u8]>),
    Float(f64),
    /// A `str` as UTF-8 that may hold surrogate code points (CPython `surrogatepass`).
    Str(Box<[u8]>),
    Bytes(Box<[u8]>),
    List(Vec<NodeId>),
    Tuple(Vec<NodeId>),
    Dict(Vec<(NodeId, NodeId)>),
    /// The marker function (`STACK_GLOBAL` of the request's module and name).
    Marker,
    /// A value outside the native model, written by Python: the exact JSON text the encoder
    /// emits for it at any position (`json.dumps` of the object with the call's options).
    Fragment(Box<[u8]>),
    /// A value whose encoding raised in Python: the index of that exception in the seam's list.
    Raised(u64),
    /// A value the native writer does not encode (a set, a frozenset, a bytearray, or a value
    /// the seam marked); the call is written by the oracle instead (`fallback`).
    Unsupported(String),
}

/// The decoded graph: every node and the root.
#[derive(Debug)]
pub struct Graph {
    pub nodes: Vec<Node>,
    pub root: NodeId,
}

/// A pickle the reader refuses: not one the seam writes. Always a protocol failure, never a
/// Python outcome.
#[derive(Debug)]
pub struct PickleError(pub String);

impl fmt::Display for PickleError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "pickle: {}", self.0)
    }
}

impl std::error::Error for PickleError {}

fn fail<T>(message: impl Into<String>) -> Result<T, PickleError> {
    Err(PickleError(message.into()))
}

#[derive(Clone, Copy, PartialEq)]
enum Item {
    Mark,
    Node(NodeId),
}

struct Reader<'a> {
    data: &'a [u8],
    at: usize,
}

impl<'a> Reader<'a> {
    fn take(&mut self, n: usize) -> Result<&'a [u8], PickleError> {
        let end = self
            .at
            .checked_add(n)
            .filter(|&end| end <= self.data.len())
            .ok_or_else(|| PickleError(format!("truncated at byte {}", self.at)))?;
        let bytes = &self.data[self.at..end];
        self.at = end;
        Ok(bytes)
    }

    fn byte(&mut self) -> Result<u8, PickleError> {
        Ok(self.take(1)?[0])
    }

    fn u16(&mut self) -> Result<u16, PickleError> {
        Ok(u16::from_le_bytes(
            self.take(2)?.try_into().unwrap_or_default(),
        ))
    }

    fn u32(&mut self) -> Result<u32, PickleError> {
        Ok(u32::from_le_bytes(
            self.take(4)?.try_into().unwrap_or_default(),
        ))
    }

    fn u64(&mut self) -> Result<u64, PickleError> {
        Ok(u64::from_le_bytes(
            self.take(8)?.try_into().unwrap_or_default(),
        ))
    }

    fn sized(&mut self, len: u64) -> Result<&'a [u8], PickleError> {
        let len = usize::try_from(len).map_err(|_| PickleError("length too large".into()))?;
        self.take(len)
    }
}

/// True when `bytes` is UTF-8 in which surrogate code points (U+D800 to U+DFFF, three bytes
/// `ED A0..BF xx`) are allowed: what CPython's `surrogatepass` writes for any `str`.
pub fn is_surrogatepass_utf8(bytes: &[u8]) -> bool {
    if std::str::from_utf8(bytes).is_ok() {
        return true;
    }
    let mut i = 0;
    while i < bytes.len() {
        let b = bytes[i];
        let (len, min) = match b {
            0x00..=0x7F => {
                i += 1;
                continue;
            }
            0xC2..=0xDF => (2, 0x80),
            0xE0..=0xEF => (3, 0x800),
            0xF0..=0xF4 => (4, 0x10000),
            _ => return false,
        };
        if i + len > bytes.len() {
            return false;
        }
        let mut code = u32::from(b) & (0x7F >> len);
        for &next in &bytes[i + 1..i + len] {
            if next & 0xC0 != 0x80 {
                return false;
            }
            code = (code << 6) | u32::from(next & 0x3F);
        }
        if code < min || code > 0x10FFFF {
            return false;
        }
        i += len;
    }
    true
}

/// Reads one pickle. `marker` is the `(module, qualname)` of the seam's marker function, the
/// only global the pickle may name.
pub fn read(data: &[u8], marker: (&str, &str)) -> Result<Graph, PickleError> {
    let mut reader = Reader { data, at: 0 };
    let mut nodes: Vec<Node> = Vec::new();
    let mut stack: Vec<Item> = Vec::new();
    let mut memo: Vec<Option<NodeId>> = Vec::new();

    fn push(nodes: &mut Vec<Node>, stack: &mut Vec<Item>, node: Node) -> Result<(), PickleError> {
        let id =
            NodeId::try_from(nodes.len()).map_err(|_| PickleError("too many objects".into()))?;
        nodes.push(node);
        stack.push(Item::Node(id));
        Ok(())
    }
    fn pop(stack: &mut Vec<Item>) -> Result<NodeId, PickleError> {
        match stack.pop() {
            Some(Item::Node(id)) => Ok(id),
            Some(Item::Mark) => fail("an object expected, a mark found"),
            None => fail("stack underflow"),
        }
    }
    fn top(stack: &[Item]) -> Result<NodeId, PickleError> {
        match stack.last() {
            Some(Item::Node(id)) => Ok(*id),
            _ => fail("no object on the stack"),
        }
    }
    fn pop_mark(stack: &mut Vec<Item>) -> Result<Vec<NodeId>, PickleError> {
        let Some(position) = stack.iter().rposition(|item| *item == Item::Mark) else {
            return fail("no mark on the stack");
        };
        let items = stack
            .drain(position + 1..)
            .map(|item| match item {
                Item::Node(id) => id,
                Item::Mark => unreachable!("rposition found the last mark"),
            })
            .collect();
        stack.pop();
        Ok(items)
    }
    fn string(bytes: &[u8]) -> Result<Node, PickleError> {
        if !is_surrogatepass_utf8(bytes) {
            return fail("a str that is not surrogatepass UTF-8");
        }
        Ok(Node::Str(bytes.into()))
    }
    fn long(bytes: &[u8]) -> Node {
        if bytes.len() <= 8 {
            let negative = bytes.last().is_some_and(|b| b & 0x80 != 0);
            let mut word = if negative { [0xFF; 8] } else { [0; 8] };
            word[..bytes.len()].copy_from_slice(bytes);
            Node::Int(i64::from_le_bytes(word))
        } else {
            // Python writes the shortest two's complement; a longer one may still fit i64.
            let negative = bytes.last().is_some_and(|b| b & 0x80 != 0);
            let fill = if negative { 0xFF } else { 0x00 };
            let fits =
                bytes[8..].iter().all(|&b| b == fill) && ((bytes[7] & 0x80 != 0) == negative);
            if fits {
                Node::Int(i64::from_le_bytes(
                    bytes[..8].try_into().unwrap_or_default(),
                ))
            } else {
                Node::BigInt(bytes.into())
            }
        }
    }

    loop {
        let opcode = reader.byte()?;
        match opcode {
            0x80 => {
                let protocol = reader.byte()?;
                if protocol > 5 {
                    return fail(format!("protocol {protocol}"));
                }
            }
            0x95 => {
                reader.u64()?; // FRAME: a length hint, the frame's bytes follow inline
            }
            b'.' => break, // STOP
            b'(' => stack.push(Item::Mark),
            b'0' => {
                // POP: the top object, or the mark at the top
                if stack.pop().is_none() {
                    return fail("POP on an empty stack");
                }
            }
            b'1' => {
                pop_mark(&mut stack)?; // POP_MARK
            }
            b'N' => push(&mut nodes, &mut stack, Node::None)?,
            0x88 => push(&mut nodes, &mut stack, Node::Bool(true))?,
            0x89 => push(&mut nodes, &mut stack, Node::Bool(false))?,
            b'J' => {
                let value = reader.u32()? as i32;
                push(&mut nodes, &mut stack, Node::Int(i64::from(value)))?;
            }
            b'K' => {
                let value = reader.byte()?;
                push(&mut nodes, &mut stack, Node::Int(i64::from(value)))?;
            }
            b'M' => {
                let value = reader.u16()?;
                push(&mut nodes, &mut stack, Node::Int(i64::from(value)))?;
            }
            0x8A => {
                let len = reader.byte()?;
                let bytes = reader.sized(u64::from(len))?;
                push(&mut nodes, &mut stack, long(bytes))?;
            }
            0x8B => {
                let len = reader.u32()? as i32;
                if len < 0 {
                    return fail("LONG4 with a negative length");
                }
                let bytes = reader.sized(len as u64)?;
                push(&mut nodes, &mut stack, long(bytes))?;
            }
            b'G' => {
                let bytes = reader.take(8)?;
                let value = f64::from_be_bytes(bytes.try_into().unwrap_or_default());
                push(&mut nodes, &mut stack, Node::Float(value))?;
            }
            0x8C => {
                let len = reader.byte()?;
                let node = string(reader.sized(u64::from(len))?)?;
                push(&mut nodes, &mut stack, node)?;
            }
            b'X' => {
                let len = reader.u32()?;
                let node = string(reader.sized(u64::from(len))?)?;
                push(&mut nodes, &mut stack, node)?;
            }
            0x8D => {
                let len = reader.u64()?;
                let node = string(reader.sized(len)?)?;
                push(&mut nodes, &mut stack, node)?;
            }
            b'C' => {
                let len = reader.byte()?;
                let bytes = reader.sized(u64::from(len))?;
                push(&mut nodes, &mut stack, Node::Bytes(bytes.into()))?;
            }
            b'B' => {
                let len = reader.u32()?;
                let bytes = reader.sized(u64::from(len))?;
                push(&mut nodes, &mut stack, Node::Bytes(bytes.into()))?;
            }
            0x8E => {
                let len = reader.u64()?;
                let bytes = reader.sized(len)?;
                push(&mut nodes, &mut stack, Node::Bytes(bytes.into()))?;
            }
            0x96 => {
                // BYTEARRAY8: str(bytearray) is not in the native model
                let len = reader.u64()?;
                reader.sized(len)?;
                push(
                    &mut nodes,
                    &mut stack,
                    Node::Unsupported("a bytearray".into()),
                )?;
            }
            b']' => push(&mut nodes, &mut stack, Node::List(Vec::new()))?,
            b'}' => push(&mut nodes, &mut stack, Node::Dict(Vec::new()))?,
            b')' => push(&mut nodes, &mut stack, Node::Tuple(Vec::new()))?,
            0x8F => push(&mut nodes, &mut stack, Node::Unsupported("a set".into()))?,
            0x85..=0x87 => {
                let n = usize::from(opcode - 0x84);
                if stack.len() < n {
                    return fail("TUPLE on a short stack");
                }
                let mut items = Vec::with_capacity(n);
                for _ in 0..n {
                    items.push(pop(&mut stack)?);
                }
                items.reverse();
                push(&mut nodes, &mut stack, Node::Tuple(items))?;
            }
            b't' => {
                let items = pop_mark(&mut stack)?;
                push(&mut nodes, &mut stack, Node::Tuple(items))?;
            }
            0x91 => {
                pop_mark(&mut stack)?; // FROZENSET
                push(
                    &mut nodes,
                    &mut stack,
                    Node::Unsupported("a frozenset".into()),
                )?;
            }
            0x90 => {
                // ADDITEMS to a set
                pop_mark(&mut stack)?;
                let target = top(&stack)?;
                if !matches!(nodes[target as usize], Node::Unsupported(_)) {
                    return fail("ADDITEMS to an object that is not a set");
                }
            }
            b'a' => {
                let value = pop(&mut stack)?;
                let target = top(&stack)?;
                match &mut nodes[target as usize] {
                    Node::List(items) => items.push(value),
                    _ => return fail("APPEND to an object that is not a list"),
                }
            }
            b'e' => {
                let values = pop_mark(&mut stack)?;
                let target = top(&stack)?;
                match &mut nodes[target as usize] {
                    Node::List(items) => items.extend(values),
                    _ => return fail("APPENDS to an object that is not a list"),
                }
            }
            b's' => {
                let value = pop(&mut stack)?;
                let key = pop(&mut stack)?;
                let target = top(&stack)?;
                set_items(&mut nodes, target, &[key, value])?;
            }
            b'u' => {
                let pairs = pop_mark(&mut stack)?;
                if pairs.len() % 2 != 0 {
                    return fail("SETITEMS with an odd number of objects");
                }
                let target = top(&stack)?;
                set_items(&mut nodes, target, &pairs)?;
            }
            0x94 => {
                let id = top(&stack)?; // MEMOIZE
                memo.push(Some(id));
            }
            b'q' | b'r' => {
                let index = if opcode == b'q' {
                    u64::from(reader.byte()?)
                } else {
                    u64::from(reader.u32()?)
                };
                let index = usize::try_from(index).map_err(|_| PickleError("memo index".into()))?;
                let id = top(&stack)?;
                if memo.len() <= index {
                    memo.resize(index + 1, None);
                }
                memo[index] = Some(id);
            }
            b'h' | b'j' => {
                let index = if opcode == b'h' {
                    u64::from(reader.byte()?)
                } else {
                    u64::from(reader.u32()?)
                };
                let id = usize::try_from(index)
                    .ok()
                    .and_then(|index| memo.get(index).copied().flatten())
                    .ok_or_else(|| PickleError(format!("memo {index} is not set")))?;
                stack.push(Item::Node(id));
            }
            0x93 => {
                // STACK_GLOBAL: only the seam's marker function
                let name = pop(&mut stack)?;
                let module = pop(&mut stack)?;
                let text = |id: NodeId| match &nodes[id as usize] {
                    Node::Str(bytes) => Some(bytes.as_ref()),
                    _ => None,
                };
                if text(module) != Some(marker.0.as_bytes())
                    || text(name) != Some(marker.1.as_bytes())
                {
                    return fail("a global other than the writer marker");
                }
                push(&mut nodes, &mut stack, Node::Marker)?;
            }
            b'R' => {
                let args = pop(&mut stack)?;
                let callable = pop(&mut stack)?;
                if !matches!(nodes[callable as usize], Node::Marker) {
                    return fail("REDUCE of something other than the writer marker");
                }
                let node = marker_value(&nodes, args)?;
                push(&mut nodes, &mut stack, node)?;
            }
            other => return fail(format!("opcode 0x{other:02x} at byte {}", reader.at - 1)),
        }
    }
    if reader.at != data.len() {
        return fail("bytes after STOP");
    }
    let root = pop(&mut stack)?;
    if !stack.is_empty() {
        return fail("objects left on the stack at STOP");
    }
    Ok(Graph { nodes, root })
}

fn set_items(nodes: &mut [Node], target: NodeId, pairs: &[NodeId]) -> Result<(), PickleError> {
    let Node::Dict(items) = &mut nodes[target as usize] else {
        return fail("SETITEM(S) on an object that is not a dict");
    };
    // The pickler writes a dict's items once each, in insertion order (a dict holds no two equal
    // keys), so the items are appended as read.
    items.extend(
        pairs
            .as_chunks::<2>()
            .0
            .iter()
            .map(|&[key, value]| (key, value)),
    );
    Ok(())
}

/// The marker's arguments: `("v", text)` a fragment, `("e", index)` a raised exception,
/// `("f", reason)` a value for the oracle.
fn marker_value(nodes: &[Node], args: NodeId) -> Result<Node, PickleError> {
    let Node::Tuple(items) = &nodes[args as usize] else {
        return fail("marker arguments are not a tuple");
    };
    let get = |index: usize| items.get(index).map(|&id| &nodes[id as usize]);
    match (get(0), get(1), items.len()) {
        (Some(Node::Str(kind)), Some(Node::Str(text)), 2) if kind.as_ref() == b"v" => {
            if std::str::from_utf8(text).is_err() && !is_surrogatepass_utf8(text) {
                return fail("marker fragment is not text");
            }
            Ok(Node::Fragment(text.clone()))
        }
        (Some(Node::Str(kind)), Some(Node::Int(index)), 2)
            if kind.as_ref() == b"e" && *index >= 0 =>
        {
            Ok(Node::Raised(*index as u64))
        }
        (Some(Node::Str(kind)), Some(Node::Str(reason)), 2) if kind.as_ref() == b"f" => Ok(
            Node::Unsupported(String::from_utf8_lossy(reason).into_owned()),
        ),
        _ => fail("marker arguments are not (kind, value)"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn surrogatepass_utf8() {
        assert!(is_surrogatepass_utf8("aé€😀".as_bytes()));
        assert!(is_surrogatepass_utf8(&[0xED, 0xA0, 0x80])); // U+D800
        assert!(is_surrogatepass_utf8(&[0xED, 0xBF, 0xBF])); // U+DFFF
        assert!(!is_surrogatepass_utf8(&[0xC0, 0x80])); // overlong
        assert!(!is_surrogatepass_utf8(&[0xE0, 0x80, 0x80])); // overlong
        assert!(!is_surrogatepass_utf8(&[0xF4, 0x90, 0x80, 0x80])); // beyond U+10FFFF
        assert!(!is_surrogatepass_utf8(&[0xED, 0xA0])); // truncated
        assert!(!is_surrogatepass_utf8(&[0xFF]));
    }

    #[test]
    fn long_fits_or_not() {
        let graph = read(
            &[0x80, 5, 0x8A, 9, 0, 0, 0, 0, 0, 0, 0, 0x80, 0, b'.'],
            ("m", "f"),
        )
        .unwrap();
        assert!(matches!(graph.nodes[graph.root as usize], Node::BigInt(_)));
        let graph = read(
            &[0x80, 5, 0x8A, 9, 1, 0, 0, 0, 0, 0, 0, 0, 0, b'.'],
            ("m", "f"),
        )
        .unwrap();
        assert!(matches!(graph.nodes[graph.root as usize], Node::Int(1)));
        let graph = read(&[0x80, 5, 0x8A, 1, 0xFF, b'.'], ("m", "f")).unwrap();
        assert!(matches!(graph.nodes[graph.root as usize], Node::Int(-1)));
    }

    #[test]
    fn refuses_globals_and_trailing_bytes() {
        assert!(read(&[0x80, 5, b'N', b'.', 0], ("m", "f")).is_err());
        assert!(read(&[0x80, 5, b'c', b'.'], ("m", "f")).is_err());
        assert!(read(&[0x80, 5], ("m", "f")).is_err());
    }
}

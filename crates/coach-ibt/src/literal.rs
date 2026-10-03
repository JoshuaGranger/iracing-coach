//! Python 3.12 `ast.literal_eval` over the literals a session-info value can carry
//! (`_yaml_scalar`, ibt_reader.py:349-358): string literals with adjacent concatenation and
//! Python escapes, integers, floats, `True`, `False`, `None`, unary sign on numbers, and tuples,
//! lists, dicts and sets of these. Evaluation follows `literal_eval`'s order, so an unhashable
//! dict key or set element is an error where Python raises `TypeError`, while any syntax or
//! malformed-node failure is `Fail` (Python's `SyntaxError`/`ValueError`, which `_yaml_scalar`
//! catches and turns into the unevaluated text).

use std::sync::LazyLock;

use regex::Regex;

use crate::yaml::{YamlNode, YamlScalar};

/// Python's tokenizer limit on nested brackets (`MAXLEVEL`); deeper input is a `SyntaxError`.
const MAX_NESTING: usize = 200;

/// Outcome of evaluating a quoted or flow value.
#[derive(Debug, Clone, PartialEq)]
pub(crate) enum Evaluated {
    /// A single string, number, bool or None.
    Scalar(YamlScalar),
    /// A list, tuple or dict, converted to `YamlNode::List` / `YamlNode::Map` with literal leaves.
    Node(YamlNode),
}

/// `Ok(None)`: `literal_eval` fails and `_yaml_scalar` falls back to text.
/// `Err(reason)`: Python would succeed with a value this port does not represent (set, bytes,
/// complex, Ellipsis, non-string dict key, lone surrogate, `\N{...}` escape) or would raise a
/// `TypeError` (unhashable key or element).
pub(crate) fn evaluate(text: &str) -> Result<Option<Evaluated>, &'static str> {
    let value = match literal_eval(text) {
        Ok(value) => value,
        Err(EvalError::Fail) => return Ok(None),
        Err(EvalError::Unsupported(reason)) => return Err(reason),
    };
    match to_node(value)? {
        YamlNode::Literal(scalar) => Ok(Some(Evaluated::Scalar(scalar))),
        node => Ok(Some(Evaluated::Node(node))),
    }
}

#[derive(Debug)]
enum EvalError {
    Fail,
    Unsupported(&'static str),
}

type Eval<T> = Result<T, EvalError>;

/// Arbitrary-precision integer: sign and base-1e9 limbs, least significant first.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
struct BigInt {
    negative: bool,
    limbs: Vec<u32>,
}

const LIMB: u64 = 1_000_000_000;

impl BigInt {
    fn from_digits(digits: &str, radix: u32) -> Option<BigInt> {
        let mut limbs: Vec<u32> = Vec::new();
        for c in digits.chars() {
            let mut carry = u64::from(c.to_digit(radix)?);
            for limb in &mut limbs {
                let value = u64::from(*limb) * u64::from(radix) + carry;
                *limb = (value % LIMB) as u32;
                carry = value / LIMB;
            }
            while carry > 0 {
                limbs.push((carry % LIMB) as u32);
                carry /= LIMB;
            }
        }
        Some(BigInt {
            negative: false,
            limbs,
        })
    }

    fn negated(mut self) -> BigInt {
        self.negative = !self.negative && !self.limbs.is_empty();
        self
    }

    fn to_i64(&self) -> Option<i64> {
        if self.limbs.len() > 3 {
            return None;
        }
        let magnitude = self.limbs.iter().rev().fold(0u128, |acc, &limb| {
            acc * u128::from(LIMB) + u128::from(limb)
        });
        let signed = if self.negative {
            -i128::try_from(magnitude).ok()?
        } else {
            i128::try_from(magnitude).ok()?
        };
        i64::try_from(signed).ok()
    }

    fn to_decimal(&self) -> String {
        let Some((last, rest)) = self.limbs.split_last() else {
            return "0".to_owned();
        };
        let mut out = String::new();
        if self.negative {
            out.push('-');
        }
        out.push_str(&last.to_string());
        for limb in rest.iter().rev() {
            out.push_str(&format!("{limb:09}"));
        }
        out
    }
}

#[derive(Debug, Clone)]
struct PyStr {
    text: String,
    /// The literal holds a lone surrogate or a `\N{...}` escape: Python yields a string this
    /// port does not reproduce.
    unrepresentable: Option<&'static str>,
}

/// A Python object produced by `literal_eval`.
#[derive(Debug, Clone)]
enum Lit {
    None,
    Bool(bool),
    Int(BigInt),
    Float(f64),
    Complex,
    Str(PyStr),
    Bytes,
    Ellipsis,
    Tuple(Vec<Lit>),
    List(Vec<Lit>),
    Dict(Vec<(Lit, Lit)>),
    Set,
}

impl Lit {
    fn hashable(&self) -> bool {
        match self {
            Lit::List(_) | Lit::Dict(_) => false,
            Lit::Tuple(items) => items.iter().all(Lit::hashable),
            _ => true,
        }
    }
}

// ---- tokens ----

#[derive(Debug)]
enum Tok {
    Str { bytes: bool, value: PyStr },
    Num(Lit),
    Name(String),
    Ellipsis,
    Op(char),
}

fn literal_eval(text: &str) -> Eval<Lit> {
    let source = text.trim_start_matches([' ', '\t']);
    let tokens = tokenize(source)?;
    let mut parser = Parser { tokens, pos: 0 };
    let node = parser.top()?;
    convert(node)
}

static INT_TOKEN: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"^(?:[1-9](?:_?[0-9])*|0+(?:_?0)*|0[xX](?:_?[0-9a-fA-F])+|0[oO](?:_?[0-7])+|0[bB](?:_?[01])+)$")
        .expect("valid integer token pattern")
});

const DIGITPART: &str = "[0-9](?:_?[0-9])*";

fn float_body() -> String {
    let point = format!("(?:(?:{d})?\\.{d}|{d}\\.)", d = DIGITPART);
    format!("(?:{point}|(?:{d}|{point})[eE][+-]?{d})", d = DIGITPART)
}

static FLOAT_TOKEN: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(&format!("^{}$", float_body())).expect("valid float token pattern")
});

static IMAG_TOKEN: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(&format!("^(?:{}|{})[jJ]$", float_body(), DIGITPART))
        .expect("valid imaginary token pattern")
});

fn number(token: &str) -> Eval<Lit> {
    let plain: String = token.chars().filter(|&c| c != '_').collect();
    if INT_TOKEN.is_match(token) {
        let (digits, radix) = match plain.get(..2) {
            Some("0x" | "0X") => (&plain[2..], 16),
            Some("0o" | "0O") => (&plain[2..], 8),
            Some("0b" | "0B") => (&plain[2..], 2),
            _ => (plain.as_str(), 10),
        };
        return BigInt::from_digits(digits, radix)
            .map(Lit::Int)
            .ok_or(EvalError::Fail);
    }
    if FLOAT_TOKEN.is_match(token) {
        return plain
            .parse::<f64>()
            .map(Lit::Float)
            .map_err(|_| EvalError::Fail);
    }
    if IMAG_TOKEN.is_match(token) {
        return Ok(Lit::Complex);
    }
    Err(EvalError::Fail)
}

fn is_name_start(c: char) -> bool {
    c == '_' || c.is_alphabetic()
}

fn is_name_char(c: char) -> bool {
    c == '_' || c.is_alphanumeric()
}

fn tokenize(source: &str) -> Eval<Vec<Tok>> {
    let chars: Vec<char> = source.chars().collect();
    let mut tokens = Vec::new();
    let mut brackets: Vec<char> = Vec::new();
    let mut i = 0;
    while i < chars.len() {
        let c = chars[i];
        match c {
            ' ' | '\t' | '\u{0c}' => i += 1,
            '#' => break,
            '(' | '[' | '{' => {
                if brackets.len() >= MAX_NESTING {
                    return Err(EvalError::Fail);
                }
                brackets.push(c);
                tokens.push(Tok::Op(c));
                i += 1;
            }
            ')' | ']' | '}' => {
                let open = match c {
                    ')' => '(',
                    ']' => '[',
                    _ => '{',
                };
                if brackets.pop() != Some(open) {
                    return Err(EvalError::Fail);
                }
                tokens.push(Tok::Op(c));
                i += 1;
            }
            ',' | ':' | '+' | '-' => {
                tokens.push(Tok::Op(c));
                i += 1;
            }
            '.' if chars.get(i + 1).is_some_and(char::is_ascii_digit) => {
                i = scan_number(&chars, i, &mut tokens)?;
            }
            '.' if chars.get(i + 1) == Some(&'.') && chars.get(i + 2) == Some(&'.') => {
                tokens.push(Tok::Ellipsis);
                i += 3;
            }
            '0'..='9' => i = scan_number(&chars, i, &mut tokens)?,
            '\'' | '"' => i = scan_string(&chars, i, "", &mut tokens)?,
            _ if is_name_start(c) => {
                let start = i;
                while i < chars.len() && is_name_char(chars[i]) {
                    i += 1;
                }
                let name: String = chars[start..i].iter().collect();
                if matches!(chars.get(i), Some('\'' | '"'))
                    && matches!(
                        name.to_ascii_lowercase().as_str(),
                        "r" | "u" | "b" | "br" | "rb" | "f" | "fr" | "rf"
                    )
                {
                    i = scan_string(&chars, i, &name, &mut tokens)?;
                } else {
                    tokens.push(Tok::Name(name));
                }
            }
            _ => return Err(EvalError::Fail),
        }
    }
    if !brackets.is_empty() {
        return Err(EvalError::Fail);
    }
    Ok(tokens)
}

fn scan_number(chars: &[char], start: usize, tokens: &mut Vec<Tok>) -> Eval<usize> {
    let prefixed = chars[start] == '0'
        && matches!(
            chars.get(start + 1),
            Some('x' | 'X' | 'o' | 'O' | 'b' | 'B')
        );
    let mut i = start;
    while i < chars.len() {
        let c = chars[i];
        let exponent_sign =
            matches!(c, '+' | '-') && !prefixed && i > start && matches!(chars[i - 1], 'e' | 'E');
        if c.is_ascii_alphanumeric() || c == '_' || c == '.' || exponent_sign {
            i += 1;
        } else {
            break;
        }
    }
    let token: String = chars[start..i].iter().collect();
    tokens.push(Tok::Num(number(&token)?));
    Ok(i)
}

fn scan_string(
    chars: &[char],
    quote_at: usize,
    prefix: &str,
    tokens: &mut Vec<Tok>,
) -> Eval<usize> {
    let prefix = prefix.to_ascii_lowercase();
    if prefix.contains('f') {
        // f-strings parse to JoinedStr, which literal_eval rejects.
        return Err(EvalError::Fail);
    }
    let raw = prefix.contains('r');
    let bytes = prefix.contains('b');
    let quote = chars[quote_at];
    let triple = chars.get(quote_at + 1) == Some(&quote) && chars.get(quote_at + 2) == Some(&quote);
    let body_start = quote_at + if triple { 3 } else { 1 };
    let mut j = body_start;
    let body_end = loop {
        match chars.get(j) {
            None => return Err(EvalError::Fail),
            Some('\\') => j += 2,
            Some(&c) if c == quote => {
                if !triple {
                    break j;
                }
                if chars.get(j + 1) == Some(&quote) && chars.get(j + 2) == Some(&quote) {
                    break j;
                }
                j += 1;
            }
            Some(_) => j += 1,
        }
    };
    let body = &chars[body_start..body_end];
    let value = if bytes {
        if body.iter().any(|c| !c.is_ascii()) {
            return Err(EvalError::Fail);
        }
        decode_body(body, raw, true)?
    } else {
        decode_body(body, raw, false)?
    };
    tokens.push(Tok::Str { bytes, value });
    Ok(body_end + if triple { 3 } else { 1 })
}

fn hex_value(digits: &[char]) -> Option<u32> {
    digits
        .iter()
        .try_fold(0u32, |acc, c| Some(acc * 16 + c.to_digit(16)?))
}

/// Python string-literal escapes (`Parser/string_parser.c`). Invalid `\x`, `\u`, `\U` escapes
/// and code points above U+10FFFF are syntax errors; unknown escapes keep the backslash.
fn decode_body(body: &[char], raw: bool, bytes: bool) -> Eval<PyStr> {
    let mut out = PyStr {
        text: String::with_capacity(body.len()),
        unrepresentable: None,
    };
    if raw {
        out.text.extend(body);
        return Ok(out);
    }
    let mut i = 0;
    while i < body.len() {
        let c = body[i];
        if c != '\\' {
            out.text.push(c);
            i += 1;
            continue;
        }
        let Some(&next) = body.get(i + 1) else {
            return Err(EvalError::Fail);
        };
        i += 2;
        let simple = match next {
            '\\' => Some('\\'),
            '\'' => Some('\''),
            '"' => Some('"'),
            'a' => Some('\u{07}'),
            'b' => Some('\u{08}'),
            'f' => Some('\u{0c}'),
            'n' => Some('\n'),
            'r' => Some('\r'),
            't' => Some('\t'),
            'v' => Some('\u{0b}'),
            _ => None,
        };
        if let Some(value) = simple {
            out.text.push(value);
            continue;
        }
        match next {
            '0'..='7' => {
                let mut value = next.to_digit(8).unwrap_or(0);
                for _ in 0..2 {
                    match body.get(i).and_then(|c| c.to_digit(8)) {
                        Some(digit) => {
                            value = value * 8 + digit;
                            i += 1;
                        }
                        None => break,
                    }
                }
                let value = if bytes { value & 0xFF } else { value };
                out.text.push(char::from_u32(value).unwrap_or('\u{FFFD}'));
            }
            'x' => {
                let value = body
                    .get(i..i + 2)
                    .and_then(hex_value)
                    .ok_or(EvalError::Fail)?;
                i += 2;
                out.text.push(char::from_u32(value).unwrap_or('\u{FFFD}'));
            }
            'u' | 'U' if !bytes => {
                let width = if next == 'u' { 4 } else { 8 };
                let value = body
                    .get(i..i + width)
                    .and_then(hex_value)
                    .ok_or(EvalError::Fail)?;
                i += width;
                if value > 0x10FFFF {
                    return Err(EvalError::Fail);
                }
                match char::from_u32(value) {
                    Some(decoded) => out.text.push(decoded),
                    None => {
                        out.text.push('\u{FFFD}');
                        out.unrepresentable = Some("lone surrogate in a string literal");
                    }
                }
            }
            'N' if !bytes => {
                // Resolving a character name needs the Unicode name table; Python either
                // succeeds or raises SyntaxError, so neither outcome can be reproduced.
                let close = body[i..].iter().position(|&c| c == '}');
                if body.get(i) != Some(&'{') || close.is_none() {
                    return Err(EvalError::Fail);
                }
                i += close.unwrap_or(0) + 1;
                out.unrepresentable = Some("\\N{...} escape in a string literal");
            }
            _ => {
                out.text.push('\\');
                out.text.push(next);
            }
        }
    }
    Ok(out)
}

// ---- parser ----

#[derive(Debug)]
enum Node {
    Const(Lit),
    Name,
    SetCall,
    Tuple(Vec<Node>),
    List(Vec<Node>),
    Set(Vec<Node>),
    Dict(Vec<(Node, Node)>),
    Unary { negative: bool, operand: Box<Node> },
    Binary { left: Box<Node>, right: Box<Node> },
}

struct Parser {
    tokens: Vec<Tok>,
    pos: usize,
}

impl Parser {
    fn peek_op(&self) -> Option<char> {
        match self.tokens.get(self.pos) {
            Some(Tok::Op(c)) => Some(*c),
            _ => None,
        }
    }

    fn eat(&mut self, op: char) -> bool {
        if self.peek_op() == Some(op) {
            self.pos += 1;
            true
        } else {
            false
        }
    }

    fn expect(&mut self, op: char) -> Eval<()> {
        if self.eat(op) {
            Ok(())
        } else {
            Err(EvalError::Fail)
        }
    }

    fn top(&mut self) -> Eval<Node> {
        if self.tokens.is_empty() {
            return Err(EvalError::Fail);
        }
        let first = self.expr()?;
        let node = if self.peek_op() == Some(',') {
            let mut items = vec![first];
            while self.eat(',') {
                if self.pos == self.tokens.len() {
                    break;
                }
                items.push(self.expr()?);
            }
            Node::Tuple(items)
        } else {
            first
        };
        if self.pos != self.tokens.len() {
            return Err(EvalError::Fail);
        }
        Ok(node)
    }

    /// Comma-separated expressions up to `close` (consumed), trailing comma allowed.
    fn items(&mut self, close: char) -> Eval<Vec<Node>> {
        let mut items = Vec::new();
        loop {
            if self.eat(close) {
                return Ok(items);
            }
            items.push(self.expr()?);
            if !self.eat(',') {
                self.expect(close)?;
                return Ok(items);
            }
        }
    }

    fn expr(&mut self) -> Eval<Node> {
        let mut left = self.factor()?;
        // literal_eval treats `+` and `-` alike: only number +/- complex is a literal.
        while matches!(self.peek_op(), Some('+' | '-')) {
            self.pos += 1;
            let right = self.factor()?;
            left = Node::Binary {
                left: Box::new(left),
                right: Box::new(right),
            };
        }
        Ok(left)
    }

    fn factor(&mut self) -> Eval<Node> {
        if let Some(op @ ('+' | '-')) = self.peek_op() {
            self.pos += 1;
            let operand = self.factor()?;
            return Ok(Node::Unary {
                negative: op == '-',
                operand: Box::new(operand),
            });
        }
        let atom = self.atom()?;
        // Calls and subscripts are never literals.
        if matches!(self.peek_op(), Some('(' | '[')) {
            return Err(EvalError::Fail);
        }
        Ok(atom)
    }

    fn atom(&mut self) -> Eval<Node> {
        let Some(token) = self.tokens.get(self.pos) else {
            return Err(EvalError::Fail);
        };
        match token {
            Tok::Str { .. } => self.strings(),
            Tok::Num(value) => {
                let value = value.clone();
                self.pos += 1;
                Ok(Node::Const(value))
            }
            Tok::Ellipsis => {
                self.pos += 1;
                Ok(Node::Const(Lit::Ellipsis))
            }
            Tok::Name(name) => {
                let node = match name.as_str() {
                    "True" => Node::Const(Lit::Bool(true)),
                    "False" => Node::Const(Lit::Bool(false)),
                    "None" => Node::Const(Lit::None),
                    "set" => {
                        let call = matches!(self.tokens.get(self.pos + 1), Some(Tok::Op('(')))
                            && matches!(self.tokens.get(self.pos + 2), Some(Tok::Op(')')));
                        if call {
                            self.pos += 2;
                            Node::SetCall
                        } else {
                            Node::Name
                        }
                    }
                    _ => Node::Name,
                };
                self.pos += 1;
                Ok(node)
            }
            Tok::Op('(') => {
                self.pos += 1;
                if self.eat(')') {
                    return Ok(Node::Tuple(Vec::new()));
                }
                let first = self.expr()?;
                if self.eat(')') {
                    return Ok(first);
                }
                self.expect(',')?;
                let mut items = vec![first];
                items.extend(self.items(')')?);
                Ok(Node::Tuple(items))
            }
            Tok::Op('[') => {
                self.pos += 1;
                Ok(Node::List(self.items(']')?))
            }
            Tok::Op('{') => {
                self.pos += 1;
                if self.eat('}') {
                    return Ok(Node::Dict(Vec::new()));
                }
                let first = self.expr()?;
                if self.eat(':') {
                    let mut pairs = vec![(first, self.expr()?)];
                    while self.eat(',') {
                        if self.eat('}') {
                            return Ok(Node::Dict(pairs));
                        }
                        let key = self.expr()?;
                        self.expect(':')?;
                        pairs.push((key, self.expr()?));
                    }
                    self.expect('}')?;
                    Ok(Node::Dict(pairs))
                } else {
                    let mut items = vec![first];
                    if self.eat(',') {
                        items.extend(self.items('}')?);
                    } else {
                        self.expect('}')?;
                    }
                    Ok(Node::Set(items))
                }
            }
            Tok::Op(_) => Err(EvalError::Fail),
        }
    }

    /// Adjacent string literals concatenate; mixing bytes and text is a syntax error.
    fn strings(&mut self) -> Eval<Node> {
        let mut text = String::new();
        let mut kind: Option<bool> = None;
        let mut unrepresentable = None;
        while let Some(Tok::Str { bytes, value }) = self.tokens.get(self.pos) {
            if kind.is_some_and(|previous| previous != *bytes) {
                return Err(EvalError::Fail);
            }
            kind = Some(*bytes);
            text.push_str(&value.text);
            unrepresentable = unrepresentable.or(value.unrepresentable);
            self.pos += 1;
        }
        if kind == Some(true) {
            return Ok(Node::Const(Lit::Bytes));
        }
        Ok(Node::Const(Lit::Str(PyStr {
            text,
            unrepresentable,
        })))
    }
}

// ---- evaluation (ast.literal_eval._convert) ----

fn convert(node: Node) -> Eval<Lit> {
    match node {
        Node::Const(value) => Ok(value),
        Node::Tuple(items) => Ok(Lit::Tuple(convert_all(items)?)),
        Node::List(items) => Ok(Lit::List(convert_all(items)?)),
        Node::Set(items) => {
            for item in items {
                if !convert(item)?.hashable() {
                    return Err(EvalError::Unsupported("unhashable set element"));
                }
            }
            Ok(Lit::Set)
        }
        Node::SetCall => Ok(Lit::Set),
        Node::Dict(pairs) => {
            let mut entries = Vec::with_capacity(pairs.len());
            for (key, value) in pairs {
                let key = convert(key)?;
                let value = convert(value)?;
                if !key.hashable() {
                    return Err(EvalError::Unsupported("unhashable dict key"));
                }
                entries.push((key, value));
            }
            Ok(Lit::Dict(entries))
        }
        Node::Binary { left, right } => {
            let left = convert_signed_num(*left)?;
            let right = convert_num(*right)?;
            if matches!(left, Lit::Int(_) | Lit::Float(_)) && matches!(right, Lit::Complex) {
                Ok(Lit::Complex)
            } else {
                Err(EvalError::Fail)
            }
        }
        other => convert_signed_num(other),
    }
}

fn convert_all(items: Vec<Node>) -> Eval<Vec<Lit>> {
    items.into_iter().map(convert).collect()
}

fn convert_signed_num(node: Node) -> Eval<Lit> {
    match node {
        Node::Unary { negative, operand } => {
            let value = convert_num(*operand)?;
            Ok(match (negative, value) {
                (false, value) => value,
                (true, Lit::Int(value)) => Lit::Int(value.negated()),
                (true, Lit::Float(value)) => Lit::Float(-value),
                (true, other) => other,
            })
        }
        other => convert_num(other),
    }
}

fn convert_num(node: Node) -> Eval<Lit> {
    match node {
        Node::Const(value @ (Lit::Int(_) | Lit::Float(_) | Lit::Complex)) => Ok(value),
        _ => Err(EvalError::Fail),
    }
}

// ---- conversion to the session-info tree (_normalise_yaml_value) ----

fn to_scalar(value: Lit) -> Result<YamlScalar, &'static str> {
    match value {
        Lit::None => Ok(YamlScalar::Null),
        Lit::Bool(value) => Ok(YamlScalar::Bool(value)),
        Lit::Int(value) => Ok(match value.to_i64() {
            Some(value) => YamlScalar::Int(value),
            None => YamlScalar::Str(value.to_decimal()),
        }),
        Lit::Float(value) => Ok(YamlScalar::Float(value)),
        Lit::Str(PyStr {
            unrepresentable: Some(reason),
            ..
        }) => Err(reason),
        Lit::Str(PyStr { text, .. }) => Ok(YamlScalar::Str(text)),
        Lit::Complex => Err("complex number in a flow literal"),
        Lit::Bytes => Err("bytes in a flow literal"),
        Lit::Ellipsis => Err("Ellipsis in a flow literal"),
        Lit::Set => Err("set in a flow literal"),
        Lit::Tuple(_) | Lit::List(_) | Lit::Dict(_) => Err("container where a scalar was expected"),
    }
}

fn to_node(value: Lit) -> Result<YamlNode, &'static str> {
    match value {
        Lit::Tuple(items) | Lit::List(items) => Ok(YamlNode::List(
            items.into_iter().map(to_node).collect::<Result<_, _>>()?,
        )),
        Lit::Dict(entries) => {
            let mut map: Vec<(String, YamlNode)> = Vec::with_capacity(entries.len());
            for (key, value) in entries {
                let key = match key {
                    Lit::Str(PyStr {
                        text,
                        unrepresentable: None,
                    }) => text,
                    _ => return Err("non-string dict key in a flow literal"),
                };
                let value = to_node(value)?;
                match map.iter_mut().find(|(existing, _)| *existing == key) {
                    Some(slot) => slot.1 = value,
                    None => map.push((key, value)),
                }
            }
            Ok(YamlNode::Map(map))
        }
        scalar => Ok(YamlNode::Literal(to_scalar(scalar)?)),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scalar(text: &str) -> Option<YamlScalar> {
        match evaluate(text) {
            Ok(Some(Evaluated::Scalar(value))) => Some(value),
            _ => None,
        }
    }

    fn fails(text: &str) -> bool {
        matches!(evaluate(text), Ok(None))
    }

    fn errors(text: &str) -> bool {
        evaluate(text).is_err()
    }

    #[test]
    fn strings() {
        assert_eq!(scalar("'abc'"), Some(YamlScalar::Str("abc".into())));
        assert_eq!(scalar("\"a\" 'b'"), Some(YamlScalar::Str("ab".into())));
        assert_eq!(
            scalar(r"'a\tb\x41\101é\U0001F600'"),
            Some(YamlScalar::Str("a\tbAAé😀".into()))
        );
        assert_eq!(scalar(r"'a\d'"), Some(YamlScalar::Str("a\\d".into())));
        assert_eq!(scalar(r"'a' r'\d'"), Some(YamlScalar::Str("a\\d".into())));
        assert_eq!(scalar(r"'it\'s'"), Some(YamlScalar::Str("it's".into())));
        assert_eq!(scalar("'''a'b'''"), Some(YamlScalar::Str("a'b".into())));
        assert_eq!(scalar(r"'\777'"), Some(YamlScalar::Str("\u{1ff}".into())));
        assert!(fails("'a"));
        assert!(fails("'''"));
        assert!(fails("'a' b'c'"));
        assert!(fails("'a' f'c'"));
        assert!(fails(r"'\x4'"));
        assert!(fails(r"'\U00110000'"));
        assert!(fails("'a' if 1 else 'b'"));
        assert!(fails("'"));
        assert!(errors(r"'\ud800'"));
        assert!(errors(r"'\N{BULLET}'"));
    }

    #[test]
    fn containers() {
        let Ok(Some(Evaluated::Node(YamlNode::List(items)))) =
            evaluate("[1, -2.5, 'x', None, True, (3,), []]")
        else {
            panic!("list expected");
        };
        assert_eq!(items.len(), 7);
        assert_eq!(items[0], YamlNode::Literal(YamlScalar::Int(1)));
        assert_eq!(items[1], YamlNode::Literal(YamlScalar::Float(-2.5)));
        assert_eq!(
            items[5],
            YamlNode::List(vec![YamlNode::Literal(YamlScalar::Int(3))])
        );
        let Ok(Some(Evaluated::Node(YamlNode::Map(map)))) =
            evaluate("{'a': 1, 'b': {'c': False}, 'a': 2,}")
        else {
            panic!("map expected");
        };
        assert_eq!(
            map[0],
            ("a".to_owned(), YamlNode::Literal(YamlScalar::Int(2)))
        );
        assert_eq!(map.len(), 2);
        assert!(matches!(
            evaluate("'a', 'b'"),
            Ok(Some(Evaluated::Node(YamlNode::List(_))))
        ));
        assert!(matches!(
            evaluate("[1], [2]"),
            Ok(Some(Evaluated::Node(YamlNode::List(_))))
        ));
        assert!(
            matches!(evaluate("{}"), Ok(Some(Evaluated::Node(YamlNode::Map(m)))) if m.is_empty())
        );
        assert!(fails("[a, b]"));
        assert!(fails("[1 2]"));
        assert!(fails("[1,,2]"));
        assert!(fails("[1][0]"));
        assert!(
            matches!(evaluate("[1, 2] # c]"), Ok(Some(Evaluated::Node(YamlNode::List(items)))) if items.len() == 2)
        );
        assert!(fails("[1, # c]"));
        assert!(fails("{'a': x}"));
        assert!(fails("[*a]"));
        assert!(fails("[1+2]"));
        assert!(fails("[--1]"));
        assert!(fails("[-True]"));
    }

    #[test]
    fn unsupported_results_and_type_errors() {
        assert!(errors("{1, 2}"));
        assert!(errors("[set()]"));
        assert!(errors("[b'x']"));
        assert!(errors("[1j]"));
        assert!(errors("[1+2j]"));
        assert!(errors("[...]"));
        assert!(errors("{1: 'a'}"));
        assert!(errors("{[1]: 2, 'a': x}"));
        assert!(fails("{[1]: x}"));
        assert!(errors("{[1]}"));
        assert!(fails("[b'é']"));
    }

    #[test]
    fn numbers() {
        let list = |text: &str| match evaluate(text) {
            Ok(Some(Evaluated::Node(YamlNode::List(items)))) => items,
            other => panic!("{text}: {other:?}"),
        };
        assert_eq!(
            list("[0x1F, 0o17, 0b101, 1_000, -0, 007.5, 1e999, .5, 1., 1.e1]"),
            vec![
                YamlNode::Literal(YamlScalar::Int(31)),
                YamlNode::Literal(YamlScalar::Int(15)),
                YamlNode::Literal(YamlScalar::Int(5)),
                YamlNode::Literal(YamlScalar::Int(1000)),
                YamlNode::Literal(YamlScalar::Int(0)),
                YamlNode::Literal(YamlScalar::Float(7.5)),
                YamlNode::Literal(YamlScalar::Float(f64::INFINITY)),
                YamlNode::Literal(YamlScalar::Float(0.5)),
                YamlNode::Literal(YamlScalar::Float(1.0)),
                YamlNode::Literal(YamlScalar::Float(10.0)),
            ]
        );
        assert_eq!(
            list("[0x10000000000000000, -9223372036854775808]"),
            vec![
                YamlNode::Literal(YamlScalar::Str("18446744073709551616".into())),
                YamlNode::Literal(YamlScalar::Int(i64::MIN)),
            ]
        );
        assert!(fails("[0777]"));
        assert!(fails("[1__0]"));
        assert!(fails("[1_]"));
        assert!(fails("[0x]"));
    }

    #[test]
    fn nesting_limit_is_python_maxlevel() {
        let deep = |depth: usize| format!("{}{}", "[".repeat(depth), "]".repeat(depth));
        assert!(evaluate(&deep(200)).is_ok_and(|v| v.is_some()));
        assert!(fails(&deep(201)));
    }
}

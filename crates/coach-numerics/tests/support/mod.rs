//! Vector loading: a small JSON reader for the generator's output and the value encoding
//! described in `tools/gen_vectors.py`.

use std::path::PathBuf;

use coach_numerics::{Num, PyResult};

#[derive(Debug, Clone)]
pub enum Json {
    Null,
    Bool(bool),
    Int(i64),
    Str(String),
    Arr(Vec<Json>),
    Obj(Vec<(String, Json)>),
}

impl Json {
    pub fn get(&self, key: &str) -> &Json {
        match self {
            Json::Obj(fields) => fields
                .iter()
                .find(|(k, _)| k == key)
                .map(|(_, v)| v)
                .unwrap_or_else(|| panic!("missing key {key}")),
            _ => panic!("not an object"),
        }
    }

    pub fn has(&self, key: &str) -> bool {
        matches!(self, Json::Obj(fields) if fields.iter().any(|(k, _)| k == key))
    }

    pub fn str(&self) -> &str {
        match self {
            Json::Str(s) => s,
            other => panic!("not a string: {other:?}"),
        }
    }

    pub fn arr(&self) -> &[Json] {
        match self {
            Json::Arr(items) => items,
            other => panic!("not an array: {other:?}"),
        }
    }

    pub fn int(&self) -> i64 {
        match self {
            Json::Int(i) => *i,
            other => panic!("not an int: {other:?}"),
        }
    }

    pub fn bool(&self) -> bool {
        match self {
            Json::Bool(b) => *b,
            other => panic!("not a bool: {other:?}"),
        }
    }

    pub fn is_null(&self) -> bool {
        matches!(self, Json::Null)
    }
}

struct Parser<'a> {
    bytes: &'a [u8],
    pos: usize,
}

impl Parser<'_> {
    fn ws(&mut self) {
        while self.pos < self.bytes.len() && self.bytes[self.pos].is_ascii_whitespace() {
            self.pos += 1;
        }
    }

    fn expect(&mut self, b: u8) {
        self.ws();
        assert_eq!(self.bytes[self.pos], b, "at byte {}", self.pos);
        self.pos += 1;
    }

    fn value(&mut self) -> Json {
        self.ws();
        match self.bytes[self.pos] {
            b'{' => {
                self.pos += 1;
                let mut fields = Vec::new();
                self.ws();
                if self.bytes[self.pos] == b'}' {
                    self.pos += 1;
                    return Json::Obj(fields);
                }
                loop {
                    self.ws();
                    let key = self.string();
                    self.expect(b':');
                    fields.push((key, self.value()));
                    self.ws();
                    let b = self.bytes[self.pos];
                    self.pos += 1;
                    if b == b'}' {
                        return Json::Obj(fields);
                    }
                    assert_eq!(b, b',');
                }
            }
            b'[' => {
                self.pos += 1;
                let mut items = Vec::new();
                self.ws();
                if self.bytes[self.pos] == b']' {
                    self.pos += 1;
                    return Json::Arr(items);
                }
                loop {
                    items.push(self.value());
                    self.ws();
                    let b = self.bytes[self.pos];
                    self.pos += 1;
                    if b == b']' {
                        return Json::Arr(items);
                    }
                    assert_eq!(b, b',');
                }
            }
            b'"' => Json::Str(self.string()),
            b't' => {
                self.pos += 4;
                Json::Bool(true)
            }
            b'f' => {
                self.pos += 5;
                Json::Bool(false)
            }
            b'n' => {
                self.pos += 4;
                Json::Null
            }
            _ => {
                let start = self.pos;
                while self.pos < self.bytes.len()
                    && (self.bytes[self.pos] == b'-' || self.bytes[self.pos].is_ascii_digit())
                {
                    self.pos += 1;
                }
                let text = std::str::from_utf8(&self.bytes[start..self.pos]).unwrap();
                Json::Int(text.parse().unwrap_or_else(|_| panic!("bad number {text}")))
            }
        }
    }

    fn hex4(&mut self) -> u32 {
        let text = std::str::from_utf8(&self.bytes[self.pos..self.pos + 4]).unwrap();
        self.pos += 4;
        u32::from_str_radix(text, 16).unwrap()
    }

    fn string(&mut self) -> String {
        assert_eq!(self.bytes[self.pos], b'"');
        self.pos += 1;
        let mut out = String::new();
        loop {
            let b = self.bytes[self.pos];
            self.pos += 1;
            match b {
                b'"' => return out,
                b'\\' => {
                    let e = self.bytes[self.pos];
                    self.pos += 1;
                    match e {
                        b'"' => out.push('"'),
                        b'\\' => out.push('\\'),
                        b'/' => out.push('/'),
                        b'b' => out.push('\u{8}'),
                        b'f' => out.push('\u{c}'),
                        b'n' => out.push('\n'),
                        b'r' => out.push('\r'),
                        b't' => out.push('\t'),
                        b'u' => {
                            let mut code = self.hex4();
                            if (0xD800..0xDC00).contains(&code) {
                                assert_eq!(&self.bytes[self.pos..self.pos + 2], b"\\u");
                                self.pos += 2;
                                let low = self.hex4();
                                code = 0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00);
                            }
                            out.push(char::from_u32(code).expect("scalar value"));
                        }
                        other => panic!("bad escape {other}"),
                    }
                }
                _ => out.push(b as char),
            }
        }
    }
}

pub fn vectors_root() -> PathBuf {
    match std::env::var_os("COACH_NUMERICS_VECTORS") {
        Some(dir) => PathBuf::from(dir),
        None => PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("vectors"),
    }
}

#[cfg(windows)]
pub const PLATFORM: &str = "windows";
#[cfg(not(windows))]
pub const PLATFORM: &str = "linux";

/// Cases of `<root>/<dir>/<family>.json`; `dir` is `common` or the platform.
pub fn load(dir: &str, family: &str) -> Vec<Json> {
    let path = vectors_root().join(dir).join(format!("{family}.json"));
    let text = std::fs::read(&path).unwrap_or_else(|e| panic!("{}: {e}", path.display()));
    let mut parser = Parser {
        bytes: &text,
        pos: 0,
    };
    let root = parser.value();
    assert_eq!(root.get("family").str(), family);
    root.get("cases").arr().to_vec()
}

pub fn num(encoded: &Json) -> Num {
    let text = encoded.str();
    if let Some(hex) = text.strip_prefix("f:") {
        Num::Float(f64::from_bits(
            u64::from_str_radix(hex, 16).expect("float bits"),
        ))
    } else if let Some(dec) = text.strip_prefix("i:") {
        Num::Int(dec.parse().expect("i64"))
    } else {
        panic!("not a numeric input: {text}")
    }
}

pub fn float(encoded: &Json) -> f64 {
    num(encoded).to_f64()
}

/// Encode a result the way the generator does.
pub fn encode<T>(result: PyResult<T>, as_text: impl Fn(T) -> String) -> String {
    match result {
        Ok(value) => as_text(value),
        Err(error) => format!("e:{}", error.kind.name()),
    }
}

pub fn enc_num(value: Num) -> String {
    match value {
        Num::Int(i) => format!("i:{i}"),
        Num::Float(x) => enc_float(x),
    }
}

pub fn enc_float(x: f64) -> String {
    if x.is_nan() {
        "f:nan".to_owned()
    } else {
        format!("f:{:016x}", x.to_bits())
    }
}

pub fn enc_bool(b: bool) -> String {
    if b { "b:1" } else { "b:0" }.to_owned()
}

/// Mismatch collector: records each failing case and panics with a summary at the end.
#[derive(Default)]
pub struct Report {
    pub cases: usize,
    pub failures: Vec<String>,
}

impl Report {
    pub fn check(&mut self, what: impl FnOnce() -> String, got: &str, want: &str) {
        self.cases += 1;
        if got != want {
            self.failures
                .push(format!("{}: got {got}, want {want}", what()));
        }
    }

    pub fn finish(self, family: &str) {
        assert!(self.cases > 0, "{family}: no cases");
        if !self.failures.is_empty() {
            let shown: Vec<&String> = self.failures.iter().take(40).collect();
            panic!(
                "{family}: {} of {} cases differ\n{}",
                self.failures.len(),
                self.cases,
                shown
                    .iter()
                    .map(|s| s.as_str())
                    .collect::<Vec<_>>()
                    .join("\n")
            );
        }
        eprintln!("{family}: {} cases identical", self.cases);
    }
}

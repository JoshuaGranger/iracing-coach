//! Python 3.12 text and number semantics that the session-info port reproduces: whitespace,
//! `str.splitlines`, cp1252 decoding, `repr(float)`, `repr(str)`, `int(str)`.

use std::fmt::Write as _;

/// `str.isspace()`: Unicode White_Space plus the four separators U+001C..U+001F.
pub(crate) fn is_space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

/// `str.strip()` with no arguments.
pub(crate) fn strip(s: &str) -> &str {
    s.trim_matches(is_space)
}

/// `str.lstrip()` with no arguments.
pub(crate) fn lstrip(s: &str) -> &str {
    s.trim_start_matches(is_space)
}

/// `str.rstrip()` with no arguments.
pub(crate) fn rstrip(s: &str) -> &str {
    s.trim_end_matches(is_space)
}

/// `str.splitlines()`: boundaries `\n`, `\r`, `\r\n`, `\v`, `\f`, U+001C..U+001E, U+0085, U+2028,
/// U+2029; a final boundary does not start an empty line.
pub(crate) fn splitlines(s: &str) -> Vec<&str> {
    let mut lines = Vec::new();
    let mut start = 0;
    let mut chars = s.char_indices().peekable();
    while let Some((index, c)) = chars.next() {
        let boundary = matches!(
            c,
            '\n' | '\r'
                | '\u{0b}'
                | '\u{0c}'
                | '\u{1c}'
                | '\u{1d}'
                | '\u{1e}'
                | '\u{85}'
                | '\u{2028}'
                | '\u{2029}'
        );
        if !boundary {
            continue;
        }
        lines.push(&s[start..index]);
        start = index + c.len_utf8();
        if c == '\r' && matches!(chars.peek(), Some((_, '\n'))) {
            chars.next();
            start += 1;
        }
    }
    if start < s.len() {
        lines.push(&s[start..]);
    }
    lines
}

/// cp1252 code points for bytes 0x80..=0x9F; `None` for the five undefined bytes.
const CP1252_HIGH: [Option<char>; 32] = [
    Some('\u{20AC}'),
    None,
    Some('\u{201A}'),
    Some('\u{0192}'),
    Some('\u{201E}'),
    Some('\u{2026}'),
    Some('\u{2020}'),
    Some('\u{2021}'),
    Some('\u{02C6}'),
    Some('\u{2030}'),
    Some('\u{0160}'),
    Some('\u{2039}'),
    Some('\u{0152}'),
    None,
    Some('\u{017D}'),
    None,
    None,
    Some('\u{2018}'),
    Some('\u{2019}'),
    Some('\u{201C}'),
    Some('\u{201D}'),
    Some('\u{2022}'),
    Some('\u{2013}'),
    Some('\u{2014}'),
    Some('\u{02DC}'),
    Some('\u{2122}'),
    Some('\u{0161}'),
    Some('\u{203A}'),
    Some('\u{0153}'),
    None,
    Some('\u{017E}'),
    Some('\u{0178}'),
];

/// `bytes.decode("cp1252", errors="replace")`.
pub(crate) fn decode_cp1252(bytes: &[u8]) -> String {
    bytes
        .iter()
        .map(|&b| match b {
            0x80..=0x9F => CP1252_HIGH[usize::from(b - 0x80)].unwrap_or('\u{FFFD}'),
            _ => char::from(b),
        })
        .collect()
}

/// Python `_decode_c_string`: bytes before the first NUL, UTF-8, else cp1252 with replacement.
pub(crate) fn decode_c_string(bytes: &[u8]) -> String {
    let cut = bytes.split(|&b| b == 0).next().unwrap_or_default();
    match std::str::from_utf8(cut) {
        Ok(text) => text.to_owned(),
        Err(_) => decode_cp1252(cut),
    }
}

/// `repr(float)` (and `str(float)`): shortest round-trip digits, exponent form when the decimal
/// exponent is below -4 or at least 16, `inf`, `-inf`, `nan`.
pub(crate) fn float_repr(x: f64) -> String {
    if x.is_nan() {
        return "nan".to_owned();
    }
    if x.is_infinite() {
        return if x > 0.0 { "inf" } else { "-inf" }.to_owned();
    }
    // Rust's LowerExp prints the shortest digits that round-trip, as Python's repr does.
    let formatted = format!("{:e}", x.abs());
    let (mantissa, exponent) = formatted.split_once('e').unwrap_or((&formatted, "0"));
    let exponent: i32 = exponent.parse().unwrap_or(0);
    let digits: String = mantissa.chars().filter(|c| c.is_ascii_digit()).collect();
    let decpt = exponent + 1;
    let mut out = String::new();
    if x.is_sign_negative() {
        out.push('-');
    }
    if !(-4 < decpt && decpt <= 16) {
        out.push_str(&digits[..1]);
        if digits.len() > 1 {
            out.push('.');
            out.push_str(&digits[1..]);
        }
        let sign = if exponent < 0 { '-' } else { '+' };
        let _ = write!(out, "e{sign}{:02}", exponent.unsigned_abs());
    } else if decpt <= 0 {
        out.push_str("0.");
        out.extend(std::iter::repeat_n('0', decpt.unsigned_abs() as usize));
        out.push_str(&digits);
    } else {
        let point = decpt as usize;
        if point >= digits.len() {
            out.push_str(&digits);
            out.extend(std::iter::repeat_n('0', point - digits.len()));
            out.push_str(".0");
        } else {
            out.push_str(&digits[..point]);
            out.push('.');
            out.push_str(&digits[point..]);
        }
    }
    out
}

/// `str.isprintable()` for one character. Exact for ASCII and Latin-1; beyond that the
/// separators, format characters and private-use ranges Python treats as non-printable are
/// listed, and unassigned code points other than noncharacters count as printable.
fn is_printable(c: char) -> bool {
    let code = u32::from(c);
    if code < 0x20 || (0x7F..=0xA0).contains(&code) || code == 0xAD {
        return false;
    }
    !matches!(
        code,
        0x0600..=0x0605
            | 0x061C
            | 0x06DD
            | 0x070F
            | 0x0890..=0x0891
            | 0x08E2
            | 0x1680
            | 0x180E
            | 0x2000..=0x200F
            | 0x2028..=0x202F
            | 0x205F..=0x2064
            | 0x2066..=0x206F
            | 0x3000
            | 0xD800..=0xF8FF
            | 0xFDD0..=0xFDEF
            | 0xFEFF
            | 0xFFF0..=0xFFFB
            | 0xFFFE..=0xFFFF
            | 0x110BD
            | 0x110CD
            | 0x13430..=0x1343F
            | 0x1BCA0..=0x1BCA3
            | 0x1D173..=0x1D17A
            | 0xE0001
            | 0xE0020..=0xE007F
            | 0xF0000..=0x10FFFF
    ) && (code & 0xFFFE) != 0xFFFE
}

/// `repr(str)`.
pub(crate) fn str_repr(s: &str) -> String {
    let quote = if s.contains('\'') && !s.contains('"') {
        '"'
    } else {
        '\''
    };
    let mut out = String::with_capacity(s.len() + 2);
    out.push(quote);
    for c in s.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '\t' => out.push_str("\\t"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            _ if c == quote => {
                out.push('\\');
                out.push(c);
            }
            _ if is_printable(c) => out.push(c),
            _ => {
                let code = u32::from(c);
                let _ = if code <= 0xFF {
                    write!(out, "\\x{code:02x}")
                } else if code <= 0xFFFF {
                    write!(out, "\\u{code:04x}")
                } else {
                    write!(out, "\\U{code:08x}")
                };
            }
        }
    }
    out.push(quote);
    out
}

/// An integral float as i64 when it lies in i64's range (exact there); `None` otherwise.
pub(crate) fn integral_to_i64(value: f64) -> Option<i64> {
    const BOUND: f64 = -(i64::MIN as f64);
    (-BOUND..BOUND).contains(&value).then_some(value as i64)
}

/// `int(text)` for base 10 within i64: surrounding whitespace, an optional sign, ASCII digits
/// with single underscores between them. Python also accepts other Unicode decimal digits and
/// returns integers beyond i64; both give `None` here.
pub(crate) fn parse_int(text: &str) -> Option<i64> {
    let text = strip(text);
    let (negative, digits) = match text.as_bytes().first() {
        Some(b'-') => (true, &text[1..]),
        Some(b'+') => (false, &text[1..]),
        _ => (false, text),
    };
    let bytes = digits.as_bytes();
    if bytes.is_empty() || !bytes[0].is_ascii_digit() || !bytes[bytes.len() - 1].is_ascii_digit() {
        return None;
    }
    let mut value: i128 = 0;
    let mut previous_underscore = false;
    for &b in bytes {
        if b == b'_' {
            if previous_underscore {
                return None;
            }
            previous_underscore = true;
            continue;
        }
        if !b.is_ascii_digit() {
            return None;
        }
        previous_underscore = false;
        value = value.checked_mul(10)?.checked_add(i128::from(b - b'0'))?;
    }
    let value = if negative { -value } else { value };
    i64::try_from(value).ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn float_repr_matches_python() {
        let cases: &[(f64, &str)] = &[
            (0.0, "0.0"),
            (-0.0, "-0.0"),
            (1.0, "1.0"),
            (1.5, "1.5"),
            (0.1, "0.1"),
            (1e16, "1e+16"),
            (1e15, "1000000000000000.0"),
            (123456789012345680.0, "1.2345678901234568e+17"),
            (0.0001, "0.0001"),
            (0.00001, "1e-05"),
            (1.5e-7, "1.5e-07"),
            (1780000001.983333, "1780000001.983333"),
            (f64::INFINITY, "inf"),
            (f64::NEG_INFINITY, "-inf"),
            (f64::NAN, "nan"),
            (5e-324, "5e-324"),
            (1.7976931348623157e308, "1.7976931348623157e+308"),
            (-2.8, "-2.8"),
            (100.0, "100.0"),
        ];
        for &(value, expected) in cases {
            assert_eq!(float_repr(value), expected, "{value:?}");
        }
    }

    #[test]
    fn splitlines_matches_python() {
        assert_eq!(splitlines("a\nb\r\nc\rd"), ["a", "b", "c", "d"]);
        assert_eq!(splitlines("a\n"), ["a"]);
        assert_eq!(splitlines("\n"), [""]);
        assert!(splitlines("").is_empty());
        assert_eq!(
            splitlines("a\u{1c}b\u{85}c\u{2028}d\u{0b}e"),
            ["a", "b", "c", "d", "e"]
        );
        assert_eq!(splitlines("a\r\r\nb"), ["a", "", "b"]);
    }

    #[test]
    fn whitespace_includes_python_separators() {
        assert_eq!(strip("\u{1f} x \u{a0}"), "x");
        assert_eq!(lstrip("\u{3000}x "), "x ");
        assert_eq!(rstrip(" x\u{85}"), " x");
    }

    #[test]
    fn cp1252_replaces_undefined_bytes() {
        assert_eq!(
            decode_cp1252(b"\x80\x81\x9f\xe9"),
            "\u{20AC}\u{FFFD}\u{0178}\u{e9}"
        );
        assert_eq!(decode_c_string(b"Caf\xe9\0junk"), "Caf\u{e9}");
        assert_eq!(decode_c_string("Café".as_bytes()), "Café");
    }

    #[test]
    fn str_repr_matches_python() {
        assert_eq!(str_repr("abc"), "'abc'");
        assert_eq!(str_repr("it's"), "\"it's\"");
        assert_eq!(str_repr("a'b\"c"), "'a\\'b\"c'");
        assert_eq!(str_repr("t\tn\n\\"), "'t\\tn\\n\\\\'");
        assert_eq!(str_repr("\u{1}\u{7f}\u{a0}é"), "'\\x01\\x7f\\xa0é'");
        assert_eq!(str_repr("\u{200b}"), "'\\u200b'");
    }

    #[test]
    fn parse_int_matches_python_int() {
        assert_eq!(parse_int(" 007 "), Some(7));
        assert_eq!(parse_int("-1_000"), Some(-1000));
        assert_eq!(parse_int("+5"), Some(5));
        assert_eq!(parse_int("1__0"), None);
        assert_eq!(parse_int("_1"), None);
        assert_eq!(parse_int("1.0"), None);
        assert_eq!(parse_int(""), None);
        assert_eq!(parse_int("9223372036854775808"), None);
        assert_eq!(parse_int("-9223372036854775808"), Some(i64::MIN));
    }
}

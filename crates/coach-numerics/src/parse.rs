//! `float(str)` and `int(str)` (base 10) with Python 3.12's accepted syntax: surrounding
//! whitespace, a sign, single underscores between digits, `inf`, `infinity` and `nan` in
//! any case.
//!
//! Whitespace follows `_PyUnicode_TransformDecimalAndSpaceToASCII`: an all-ASCII string is
//! stripped of ASCII whitespace only (`\x1c`-`\x1f` stay and fail), while a string with any
//! non-ASCII character first has every `str.isspace` character turned into a space. A
//! non-ASCII digit (which Python accepts, for example `'١'`) is `Unsupported` here.

use crate::error::{INT_BEYOND_I64, PyError, PyErrorKind, PyResult};

/// `Py_ISSPACE`: the C locale whitespace set.
fn is_ascii_space(c: char) -> bool {
    matches!(c, ' ' | '\t' | '\n' | '\x0b' | '\x0c' | '\r')
}

/// `str.isspace()` for one character.
fn is_unicode_space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

/// The ASCII text CPython parses, already stripped; `Err` for a non-ASCII character that is
/// neither whitespace nor (possibly) a decimal digit, `Unsupported` for a non-ASCII digit.
fn ascii_text(text: &str, error: PyError) -> PyResult<String> {
    let mapped: String = if text.is_ascii() {
        text.to_owned()
    } else {
        let mut out = String::with_capacity(text.len());
        for c in text.chars() {
            if is_unicode_space(c) {
                out.push(' ');
            } else if c.is_ascii() {
                out.push(c);
            } else if c.is_numeric() {
                return Err(PyError::new(PyErrorKind::Unsupported, "non-ASCII digit"));
            } else {
                return Err(error);
            }
        }
        out
    };
    Ok(mapped.trim_matches(is_ascii_space).to_owned())
}

/// Remove underscores that sit between two digits; `None` if any other underscore exists.
fn strip_underscores(text: &str) -> Option<String> {
    let bytes = text.as_bytes();
    let mut out = String::with_capacity(text.len());
    for (index, &b) in bytes.iter().enumerate() {
        if b == b'_' {
            let before = index > 0 && bytes[index - 1].is_ascii_digit();
            let after = bytes.get(index + 1).is_some_and(u8::is_ascii_digit);
            if !(before && after) {
                return None;
            }
        } else {
            out.push(b as char);
        }
    }
    Some(out)
}

fn float_error() -> PyError {
    PyError::new(PyErrorKind::ValueError, "could not convert string to float")
}

/// `float(text)`.
pub fn parse_float(text: &str) -> PyResult<f64> {
    let trimmed = ascii_text(text, float_error())?;
    let trimmed = trimmed.as_str();
    if trimmed.is_empty() {
        return Err(float_error());
    }
    let cleaned = if trimmed.contains('_') {
        strip_underscores(trimmed).ok_or_else(float_error)?
    } else {
        trimmed.to_owned()
    };
    let (negative, body) = match cleaned.as_bytes()[0] {
        b'-' => (true, &cleaned[1..]),
        b'+' => (false, &cleaned[1..]),
        _ => (false, cleaned.as_str()),
    };
    let lower = body.to_ascii_lowercase();
    let magnitude = match lower.as_str() {
        "inf" | "infinity" => f64::INFINITY,
        "nan" => f64::NAN,
        _ => {
            let b = body.as_bytes();
            let mut i = 0;
            let int_start = i;
            while i < b.len() && b[i].is_ascii_digit() {
                i += 1;
            }
            let mut digits = i - int_start;
            if i < b.len() && b[i] == b'.' {
                i += 1;
                let frac_start = i;
                while i < b.len() && b[i].is_ascii_digit() {
                    i += 1;
                }
                digits += i - frac_start;
            }
            if digits == 0 {
                return Err(float_error());
            }
            if i < b.len() && (b[i] == b'e' || b[i] == b'E') {
                i += 1;
                if i < b.len() && (b[i] == b'+' || b[i] == b'-') {
                    i += 1;
                }
                let exp_start = i;
                while i < b.len() && b[i].is_ascii_digit() {
                    i += 1;
                }
                if i == exp_start {
                    return Err(float_error());
                }
            }
            if i != b.len() {
                return Err(float_error());
            }
            body.parse::<f64>().map_err(|_| float_error())?
        }
    };
    Ok(if negative { -magnitude } else { magnitude })
}

/// `int(text)` in base 10.
pub fn parse_int(text: &str) -> PyResult<i64> {
    let error = || {
        PyError::new(
            PyErrorKind::ValueError,
            "invalid literal for int() with base 10",
        )
    };
    let trimmed = ascii_text(text, error())?;
    let trimmed = trimmed.as_str();
    let (negative, body) = match trimmed.as_bytes().first() {
        Some(b'-') => (true, &trimmed[1..]),
        Some(b'+') => (false, &trimmed[1..]),
        _ => (false, trimmed),
    };
    if body.is_empty() || !body.is_ascii() || !body.as_bytes()[0].is_ascii_digit() {
        return Err(error());
    }
    let digits = strip_underscores(body).ok_or_else(error)?;
    if !digits.bytes().all(|b| b.is_ascii_digit()) {
        return Err(error());
    }
    let magnitude = crate::bigint::BigUint::from_decimal(&digits).ok_or_else(error)?;
    crate::bigint::BigInt::new(negative, magnitude)
        .to_i64()
        .ok_or(INT_BEYOND_I64)
}

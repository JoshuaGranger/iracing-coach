//! `format(value, spec)` and f-string `{value:spec}` for floats and ints (CPython 3.12
//! `Python/formatter_unicode.c`): fill, align, sign, `z`, `#`, `0`, width, `,`/`_` grouping,
//! precision and the types `e E f F g G %` (floats and ints) and `d b o x X` (ints).

use crate::error::{PyError, PyErrorKind, PyResult};
use crate::num::Num;
use crate::repr::{Code, Flags, double_to_string};

#[derive(Clone, Debug)]
struct Spec {
    fill: char,
    align: char,
    sign: Option<char>,
    no_neg_0: bool,
    alt: bool,
    width: i64,
    grouping: Option<char>,
    precision: i64,
    ty: Option<char>,
}

fn value_error(message: &'static str) -> PyError {
    PyError::new(PyErrorKind::ValueError, message)
}

fn is_align(c: char) -> bool {
    matches!(c, '<' | '>' | '=' | '^')
}

/// `parse_internal_render_format_spec` with the numeric defaults (align `>`).
fn parse_spec(text: &str) -> PyResult<Spec> {
    let chars: Vec<char> = text.chars().collect();
    let end = chars.len();
    let mut pos = 0;
    let mut spec = Spec {
        fill: ' ',
        align: '>',
        sign: None,
        no_neg_0: false,
        alt: false,
        width: -1,
        grouping: None,
        precision: -1,
        ty: None,
    };
    let mut fill_specified = false;
    let mut align_specified = false;
    if end - pos >= 2 && is_align(chars[pos + 1]) {
        spec.align = chars[pos + 1];
        spec.fill = chars[pos];
        fill_specified = true;
        align_specified = true;
        pos += 2;
    } else if end - pos >= 1 && is_align(chars[pos]) {
        spec.align = chars[pos];
        align_specified = true;
        pos += 1;
    }
    if pos < end && matches!(chars[pos], '+' | '-' | ' ') {
        spec.sign = Some(chars[pos]);
        pos += 1;
    }
    if pos < end && chars[pos] == 'z' {
        spec.no_neg_0 = true;
        pos += 1;
    }
    if pos < end && chars[pos] == '#' {
        spec.alt = true;
        pos += 1;
    }
    if !fill_specified && pos < end && chars[pos] == '0' {
        spec.fill = '0';
        if !align_specified {
            spec.align = '=';
        }
        pos += 1;
    }
    let read_integer = |pos: &mut usize| -> PyResult<Option<i64>> {
        let start = *pos;
        let mut value: i64 = 0;
        while *pos < end && chars[*pos].is_ascii_digit() {
            value = value
                .checked_mul(10)
                .and_then(|v| v.checked_add(i64::from(chars[*pos] as u8 - b'0')))
                .ok_or(value_error("Too many decimal digits in format string"))?;
            *pos += 1;
        }
        Ok((*pos > start).then_some(value))
    };
    if let Some(width) = read_integer(&mut pos)? {
        spec.width = width;
    }
    if pos < end && chars[pos] == ',' {
        spec.grouping = Some(',');
        pos += 1;
    }
    if pos < end && chars[pos] == '_' {
        if spec.grouping.is_some() {
            return Err(value_error("Cannot specify both ',' and '_'."));
        }
        spec.grouping = Some('_');
        pos += 1;
    }
    if pos < end && chars[pos] == ',' && spec.grouping == Some('_') {
        return Err(value_error("Cannot specify both ',' and '_'."));
    }
    if pos < end && chars[pos] == '.' {
        pos += 1;
        spec.precision =
            read_integer(&mut pos)?.ok_or(value_error("Format specifier missing precision"))?;
    }
    if end - pos > 1 {
        return Err(value_error("Invalid format specifier"));
    }
    if end - pos == 1 {
        spec.ty = Some(chars[pos]);
    }
    if spec.grouping.is_some() {
        match spec.ty {
            None | Some('d' | 'e' | 'f' | 'g' | 'E' | 'G' | '%' | 'F') => {}
            Some('b' | 'o' | 'x' | 'X') if spec.grouping == Some('_') => {}
            _ => return Err(value_error("Cannot specify ',' with this format type")),
        }
    }
    Ok(spec)
}

/// `_PyUnicode_InsertThousandsGrouping`: digits split into groups from the right, padded
/// with zeros up to `min_width` (the `0` flag's width) without starting on a separator.
fn group_digits(digits: &str, min_width: i64, group: i64, separator: Option<char>) -> String {
    let bytes = digits.as_bytes();
    let mut min_width = min_width.max(0);
    let mut remaining = bytes.len() as i64;
    let mut parts: Vec<String> = Vec::new();
    let mut emit = |remaining: &mut i64, l: i64| {
        let zeros = (l - *remaining).max(0);
        let count = (*remaining).min(l).max(0);
        let end = *remaining as usize;
        let start = end - count as usize;
        let mut part = "0".repeat(zeros as usize);
        part.push_str(&digits[start..end]);
        parts.push(part);
        *remaining -= count;
    };
    let mut broken = false;
    if separator.is_some() && group > 0 {
        loop {
            let l = group.min(remaining.max(min_width).max(1));
            emit(&mut remaining, l);
            min_width -= l;
            if remaining <= 0 && min_width <= 0 {
                broken = true;
                break;
            }
            min_width -= 1;
        }
    }
    if !broken {
        let l = remaining.max(min_width).max(1);
        emit(&mut remaining, l);
    }
    parts.reverse();
    match separator {
        Some(sep) => parts.join(&sep.to_string()),
        None => parts.concat(),
    }
}

/// `calc_number_widths` + `fill_number`: sign, prefix, padding, grouped digits, remainder.
fn assemble(
    spec: &Spec,
    negative: bool,
    prefix: &str,
    digits: &str,
    remainder: &str,
    group: i64,
) -> String {
    let sign = match spec.sign {
        Some('+') => Some(if negative { '-' } else { '+' }),
        Some(' ') => Some(if negative { '-' } else { ' ' }),
        _ => negative.then_some('-'),
    };
    let n_sign = i64::from(sign.is_some());
    let n_prefix = prefix.chars().count() as i64;
    let n_remainder = remainder.chars().count() as i64;
    let non_digit = n_sign + n_prefix + n_remainder;
    let min_width = if spec.fill == '0' && spec.align == '=' {
        spec.width - non_digit
    } else {
        0
    };
    let grouped = if digits.is_empty() {
        String::new()
    } else {
        group_digits(digits, min_width, group, spec.grouping)
    };
    let padding = spec.width - (non_digit + grouped.chars().count() as i64);
    let (mut left, mut middle, mut right) = (0, 0, 0);
    if padding > 0 {
        match spec.align {
            '<' => right = padding,
            '^' => {
                left = padding / 2;
                right = padding - left;
            }
            '=' => middle = padding,
            _ => left = padding,
        }
    }
    let fill = |count: i64| spec.fill.to_string().repeat(count as usize);
    let mut out = fill(left);
    if let Some(sign) = sign {
        out.push(sign);
    }
    out.push_str(prefix);
    out.push_str(&fill(middle));
    out.push_str(&grouped);
    out.push_str(remainder);
    out.push_str(&fill(right));
    out
}

fn format_float_spec(mut x: f64, spec: &Spec) -> PyResult<String> {
    let mut flags = Flags {
        alt: spec.alt,
        no_negative_zero: spec.no_neg_0,
        ..Flags::default()
    };
    let mut default_precision = 6;
    let mut add_pct = false;
    let mut code = match spec.ty {
        None => {
            flags.add_dot_0_if_integer = true;
            default_precision = 0;
            Code::Repr
        }
        Some('e') => Code::Exp,
        Some('E') => {
            flags.upper = true;
            Code::Exp
        }
        Some('f') => Code::Fixed,
        Some('F') => {
            flags.upper = true;
            Code::Fixed
        }
        Some('g') => Code::General,
        Some('G') => {
            flags.upper = true;
            Code::General
        }
        Some('%') => {
            x *= 100.0;
            add_pct = true;
            Code::Fixed
        }
        Some('n') => {
            return Err(PyError::new(PyErrorKind::Unsupported, "locale format 'n'"));
        }
        Some(_) => {
            return Err(value_error(
                "Unknown format code for object of type 'float'",
            ));
        }
    };
    let precision = if spec.precision < 0 {
        default_precision
    } else {
        if code == Code::Repr {
            code = Code::General;
        }
        i32::try_from(spec.precision).map_err(|_| value_error("precision too big"))?
    };
    let mut buf = double_to_string(x, code, precision, flags);
    if add_pct {
        buf.push('%');
    }
    let (negative, body) = match buf.strip_prefix('-') {
        Some(rest) => (true, rest),
        None => (false, buf.as_str()),
    };
    let split = body
        .find(|c: char| !c.is_ascii_digit())
        .unwrap_or(body.len());
    let (digits, remainder) = body.split_at(split);
    Ok(assemble(spec, negative, "", digits, remainder, 3))
}

/// `format(x, spec)` for a float.
pub fn format_float(x: f64, spec: &str) -> PyResult<String> {
    format_float_spec(x, &parse_spec(spec)?)
}

/// `format(i, spec)` for an int; float types format `float(i)`.
pub fn format_int(i: i64, spec: &str) -> PyResult<String> {
    let spec = parse_spec(spec)?;
    let (radix, prefix, upper) = match spec.ty {
        Some('e' | 'E' | 'f' | 'F' | 'g' | 'G' | '%') => {
            return format_float_spec(i as f64, &spec);
        }
        None | Some('d') => (10, "", false),
        Some('x') => (16, "0x", false),
        Some('X') => (16, "0X", true),
        Some('o') => (8, "0o", false),
        Some('b') => (2, "0b", false),
        Some('c' | 'n') => {
            return Err(PyError::new(
                PyErrorKind::Unsupported,
                "int format type 'c' or 'n'",
            ));
        }
        Some(_) => return Err(value_error("Unknown format code for object of type 'int'")),
    };
    if spec.precision >= 0 {
        return Err(value_error(
            "Precision not allowed in integer format specifier",
        ));
    }
    if spec.no_neg_0 {
        return Err(value_error(
            "Negative zero coercion (z) not allowed in integer format specifier",
        ));
    }
    let magnitude = i.unsigned_abs();
    let digits = match radix {
        16 if upper => format!("{magnitude:X}"),
        16 => format!("{magnitude:x}"),
        8 => format!("{magnitude:o}"),
        2 => format!("{magnitude:b}"),
        _ => magnitude.to_string(),
    };
    let group = if radix == 10 { 3 } else { 4 };
    Ok(assemble(
        &spec,
        i < 0,
        if spec.alt { prefix } else { "" },
        &digits,
        "",
        group,
    ))
}

/// `format(value, spec)` for an int or a float.
pub fn format_num(value: Num, spec: &str) -> PyResult<String> {
    match value {
        Num::Int(i) => format_int(i, spec),
        Num::Float(x) => format_float(x, spec),
    }
}

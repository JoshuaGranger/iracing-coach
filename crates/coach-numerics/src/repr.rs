//! Float to text as CPython 3.12 `PyOS_double_to_string` produces it (`Python/pystrtod.c`,
//! `format_float_short`): `repr`, `str`, the JSON float token and the digit layout behind
//! `format()`.

use crate::decimal::{self, Digits};
use crate::error::{PyError, PyErrorKind, PyResult};
use crate::num::Num;

/// The digit-layout code `format_float_short` receives.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Code {
    /// `repr`: shortest digits, exponent below 1e-4 or from 1e16.
    Repr,
    /// `e`: `precision` digits after the point, always an exponent.
    Exp,
    /// `f`: `precision` digits after the point.
    Fixed,
    /// `g`: `precision` significant digits, exponent outside `1e-4 <= |x| < 10**precision`.
    General,
}

#[derive(Clone, Copy, Debug, Default)]
pub(crate) struct Flags {
    pub always_add_sign: bool,
    pub add_dot_0_if_integer: bool,
    pub alt: bool,
    pub no_negative_zero: bool,
    pub upper: bool,
}

/// `PyOS_double_to_string(x, code, precision, flags)`.
pub(crate) fn double_to_string(x: f64, code: Code, precision: i32, flags: Flags) -> String {
    let negative = x.is_sign_negative();
    if x.is_nan() {
        return if flags.upper { "NAN" } else { "nan" }.to_owned();
    }
    if x.is_infinite() {
        let body = if flags.upper { "INF" } else { "inf" };
        return if negative {
            format!("-{body}")
        } else if flags.always_add_sign {
            format!("+{body}")
        } else {
            body.to_owned()
        };
    }
    let (digits, precision) = match code {
        Code::Repr => (
            if x == 0.0 {
                Digits {
                    digits: b"0".to_vec(),
                    decpt: 1,
                }
            } else {
                decimal::shortest(x)
            },
            0,
        ),
        Code::Exp => (decimal::mode2(x, precision + 1), precision + 1),
        Code::Fixed => (decimal::mode3(x, precision), precision),
        Code::General => {
            let precision = precision.max(1);
            (decimal::mode2(x, precision), precision)
        }
    };
    layout(negative, &digits, code, precision, flags)
}

fn layout(mut negative: bool, d: &Digits, code: Code, precision: i32, flags: Flags) -> String {
    let digits = &d.digits;
    let digits_len = digits.len() as i64;
    let mut decpt = i64::from(d.decpt);
    let precision = i64::from(precision);
    let mut use_exp = false;
    let mut vdigits_end = digits_len;
    match code {
        Code::Exp => {
            use_exp = true;
            vdigits_end = precision;
        }
        Code::Fixed => vdigits_end = decpt + precision,
        Code::General => {
            let limit = if flags.add_dot_0_if_integer {
                precision - 1
            } else {
                precision
            };
            if decpt <= -4 || decpt > limit {
                use_exp = true;
            }
            if flags.alt {
                vdigits_end = precision;
            }
        }
        Code::Repr => {
            if decpt <= -4 || decpt > 16 {
                use_exp = true;
            }
        }
    }
    let mut exp = 0i64;
    if use_exp {
        exp = decpt - 1;
        decpt = 1;
    }
    let vdigits_start = if decpt <= 0 { decpt - 1 } else { 0 };
    if !use_exp && flags.add_dot_0_if_integer {
        vdigits_end = vdigits_end.max(decpt + 1);
    } else {
        vdigits_end = vdigits_end.max(decpt);
    }
    if flags.no_negative_zero
        && negative
        && (digits.is_empty() || (digits.len() == 1 && digits[0] == b'0'))
    {
        negative = false;
    }
    let mut out = String::new();
    if negative {
        out.push('-');
    } else if flags.always_add_sign {
        out.push('+');
    }
    let zeros = |out: &mut String, count: i64| {
        for _ in 0..count.max(0) {
            out.push('0');
        }
    };
    let text = std::str::from_utf8(digits).expect("ASCII digits");
    if decpt <= 0 {
        zeros(&mut out, decpt - vdigits_start);
        out.push('.');
        zeros(&mut out, -decpt);
    } else {
        zeros(&mut out, -vdigits_start);
    }
    if 0 < decpt && decpt <= digits_len {
        out.push_str(&text[..decpt as usize]);
        out.push('.');
        out.push_str(&text[decpt as usize..]);
    } else {
        out.push_str(text);
    }
    if digits_len < decpt {
        zeros(&mut out, decpt - digits_len);
        out.push('.');
        zeros(&mut out, vdigits_end - decpt);
    } else {
        zeros(&mut out, vdigits_end - digits_len);
    }
    if out.ends_with('.') && !flags.alt {
        out.pop();
    }
    if use_exp {
        out.push(if flags.upper { 'E' } else { 'e' });
        out.push(if exp < 0 { '-' } else { '+' });
        let magnitude = exp.unsigned_abs();
        if magnitude < 10 {
            out.push('0');
        }
        out.push_str(&magnitude.to_string());
    }
    out
}

/// `repr(x)` and `str(x)` for a float: `1.0`, `0.1`, `1e-05`, `1e+16`, `-0.0`, `inf`, `nan`.
pub fn float_repr(x: f64) -> String {
    double_to_string(
        x,
        Code::Repr,
        0,
        Flags {
            add_dot_0_if_integer: true,
            ..Flags::default()
        },
    )
}

/// `repr(value)` for an int or a float.
pub fn num_repr(value: Num) -> String {
    match value {
        Num::Int(i) => i.to_string(),
        Num::Float(x) => float_repr(x),
    }
}

/// The token `json.dumps` writes for a float: `float.__repr__` for finite values, `NaN`,
/// `Infinity`, `-Infinity` when `allow_nan` (the default), else `ValueError`.
pub fn json_float(x: f64, allow_nan: bool) -> PyResult<String> {
    if x.is_finite() {
        return Ok(float_repr(x));
    }
    if !allow_nan {
        return Err(PyError::new(
            PyErrorKind::ValueError,
            "Out of range float values are not JSON compliant",
        ));
    }
    Ok(if x.is_nan() {
        "NaN"
    } else if x > 0.0 {
        "Infinity"
    } else {
        "-Infinity"
    }
    .to_owned())
}

/// The token `json.dumps` writes for an int or a float.
pub fn json_num(value: Num, allow_nan: bool) -> PyResult<String> {
    match value {
        Num::Int(i) => Ok(i.to_string()),
        Num::Float(x) => json_float(x, allow_nan),
    }
}

//! Python numbers (`int` within `i64`, `float`) and their operators: mixed-type arithmetic,
//! exact int/float comparison, floor division and modulo sign rules, `int(float)`.

use std::cmp::Ordering;

use crate::error::{INT_BEYOND_I64, PyError, PyErrorKind, PyResult, ZERO_DIVISION};
use crate::exact::int_true_div;

/// A Python `int` (held in `i64`) or `float`. The type survives arithmetic exactly as in
/// Python: `int op int` stays `int` except under `/`.
#[derive(Clone, Copy, Debug)]
pub enum Num {
    Int(i64),
    Float(f64),
}

impl Num {
    /// `float(x)`: an int is rounded half to even (`PyLong_AsDouble`); `i64` never overflows.
    pub fn to_f64(self) -> f64 {
        match self {
            Self::Int(i) => i as f64,
            Self::Float(f) => f,
        }
    }

    pub fn is_float(self) -> bool {
        matches!(self, Self::Float(_))
    }

    /// Same type and same bits (floats) or value (ints); NaN equals NaN.
    pub fn identical(self, other: Self) -> bool {
        match (self, other) {
            (Self::Int(a), Self::Int(b)) => a == b,
            (Self::Float(a), Self::Float(b)) => {
                a.to_bits() == b.to_bits() || (a.is_nan() && b.is_nan())
            }
            _ => false,
        }
    }
}

/// Exact comparison of an int with a float, as `float_richcompare` does; `None` for NaN.
pub fn cmp_int_float(i: i64, f: f64) -> Option<Ordering> {
    if f.is_nan() {
        return None;
    }
    if f >= 9_223_372_036_854_775_808.0 {
        return Some(Ordering::Less);
    }
    if f < -9_223_372_036_854_775_808.0 {
        return Some(Ordering::Greater);
    }
    let whole = f.trunc();
    match i.cmp(&(whole as i64)) {
        Ordering::Equal => 0.0f64.partial_cmp(&(f - whole)),
        other => Some(other),
    }
}

/// Python's `a <=> b` for numbers; `None` when either side is NaN.
pub fn py_cmp(a: Num, b: Num) -> Option<Ordering> {
    match (a, b) {
        (Num::Int(x), Num::Int(y)) => Some(x.cmp(&y)),
        (Num::Float(x), Num::Float(y)) => x.partial_cmp(&y),
        (Num::Int(x), Num::Float(y)) => cmp_int_float(x, y),
        (Num::Float(x), Num::Int(y)) => cmp_int_float(y, x).map(Ordering::reverse),
    }
}

/// `a < b`.
pub fn py_lt(a: Num, b: Num) -> bool {
    py_cmp(a, b) == Some(Ordering::Less)
}

/// `a <= b`.
pub fn py_le(a: Num, b: Num) -> bool {
    matches!(py_cmp(a, b), Some(Ordering::Less | Ordering::Equal))
}

/// `a > b`.
pub fn py_gt(a: Num, b: Num) -> bool {
    py_cmp(a, b) == Some(Ordering::Greater)
}

/// `a >= b`.
pub fn py_ge(a: Num, b: Num) -> bool {
    matches!(py_cmp(a, b), Some(Ordering::Greater | Ordering::Equal))
}

/// `a == b`.
pub fn py_eq(a: Num, b: Num) -> bool {
    py_cmp(a, b) == Some(Ordering::Equal)
}

fn int_op(value: Option<i64>) -> PyResult<Num> {
    value.map(Num::Int).ok_or(INT_BEYOND_I64)
}

/// `a + b`.
pub fn add(a: Num, b: Num) -> PyResult<Num> {
    match (a, b) {
        (Num::Int(x), Num::Int(y)) => int_op(x.checked_add(y)),
        _ => Ok(Num::Float(a.to_f64() + b.to_f64())),
    }
}

/// `a - b`.
pub fn sub(a: Num, b: Num) -> PyResult<Num> {
    match (a, b) {
        (Num::Int(x), Num::Int(y)) => int_op(x.checked_sub(y)),
        _ => Ok(Num::Float(a.to_f64() - b.to_f64())),
    }
}

/// `a * b`.
pub fn mul(a: Num, b: Num) -> PyResult<Num> {
    match (a, b) {
        (Num::Int(x), Num::Int(y)) => int_op(x.checked_mul(y)),
        _ => Ok(Num::Float(a.to_f64() * b.to_f64())),
    }
}

/// `a / b` (always a float; int/int correctly rounded).
pub fn truediv(a: Num, b: Num) -> PyResult<f64> {
    match (a, b) {
        (Num::Int(x), Num::Int(y)) => int_true_div(x, y),
        _ => {
            let divisor = b.to_f64();
            if divisor == 0.0 {
                return Err(PyError::new(
                    PyErrorKind::ZeroDivisionError,
                    "float division by zero",
                ));
            }
            Ok(a.to_f64() / divisor)
        }
    }
}

/// CPython `_float_div_mod`: floor quotient snapped to an integer and remainder with the
/// divisor's sign.
pub fn float_divmod(vx: f64, wx: f64) -> PyResult<(f64, f64)> {
    if wx == 0.0 {
        return Err(PyError::new(
            PyErrorKind::ZeroDivisionError,
            "float divmod()",
        ));
    }
    let mut modulo = vx % wx;
    let mut div = (vx - modulo) / wx;
    if modulo != 0.0 {
        if (wx < 0.0) != (modulo < 0.0) {
            modulo += wx;
            div -= 1.0;
        }
    } else {
        modulo = 0.0f64.copysign(wx);
    }
    let floordiv = if div != 0.0 {
        let mut f = div.floor();
        if div - f > 0.5 {
            f += 1.0;
        }
        f
    } else {
        0.0f64.copysign(vx / wx)
    };
    Ok((floordiv, modulo))
}

/// CPython `float_rem` (the `%` operator on floats).
pub fn float_mod(vx: f64, wx: f64) -> PyResult<f64> {
    if wx == 0.0 {
        return Err(PyError::new(PyErrorKind::ZeroDivisionError, "float modulo"));
    }
    let modulo = vx % wx;
    if modulo != 0.0 {
        Ok(if (wx < 0.0) != (modulo < 0.0) {
            modulo + wx
        } else {
            modulo
        })
    } else {
        Ok(0.0f64.copysign(wx))
    }
}

fn int_floordiv(x: i64, y: i64) -> PyResult<i64> {
    if y == 0 {
        return Err(ZERO_DIVISION);
    }
    let q = x.checked_div(y).ok_or(INT_BEYOND_I64)?;
    Ok(if x % y != 0 && ((x < 0) != (y < 0)) {
        q - 1
    } else {
        q
    })
}

fn int_mod(x: i64, y: i64) -> PyResult<i64> {
    if y == 0 {
        return Err(ZERO_DIVISION);
    }
    let r = x.checked_rem(y).unwrap_or(0);
    Ok(if r != 0 && ((r < 0) != (y < 0)) {
        r + y
    } else {
        r
    })
}

/// `a // b`.
pub fn floordiv(a: Num, b: Num) -> PyResult<Num> {
    match (a, b) {
        (Num::Int(x), Num::Int(y)) => int_floordiv(x, y).map(Num::Int),
        _ => float_divmod(a.to_f64(), b.to_f64()).map(|(q, _)| Num::Float(q)),
    }
}

/// `a % b`.
pub fn modulo(a: Num, b: Num) -> PyResult<Num> {
    match (a, b) {
        (Num::Int(x), Num::Int(y)) => int_mod(x, y).map(Num::Int),
        _ => float_mod(a.to_f64(), b.to_f64()).map(Num::Float),
    }
}

/// `divmod(a, b)`.
pub fn divmod(a: Num, b: Num) -> PyResult<(Num, Num)> {
    match (a, b) {
        (Num::Int(x), Num::Int(y)) => Ok((Num::Int(int_floordiv(x, y)?), Num::Int(int_mod(x, y)?))),
        _ => float_divmod(a.to_f64(), b.to_f64()).map(|(q, r)| (Num::Float(q), Num::Float(r))),
    }
}

/// `-a`.
pub fn neg(a: Num) -> PyResult<Num> {
    match a {
        Num::Int(x) => int_op(x.checked_neg()),
        Num::Float(f) => Ok(Num::Float(-f)),
    }
}

/// `abs(a)`.
pub fn abs(a: Num) -> PyResult<Num> {
    match a {
        Num::Int(x) => int_op(x.checked_abs()),
        Num::Float(f) => Ok(Num::Float(f.abs())),
    }
}

/// `int(x)` for a float: truncation toward zero.
pub fn int_from_float(x: f64) -> PyResult<i64> {
    if x.is_nan() {
        return Err(PyError::new(
            PyErrorKind::ValueError,
            "cannot convert float NaN to integer",
        ));
    }
    if x.is_infinite() {
        return Err(PyError::new(
            PyErrorKind::OverflowError,
            "cannot convert float infinity to integer",
        ));
    }
    let whole = x.trunc();
    if (-9_223_372_036_854_775_808.0..9_223_372_036_854_775_808.0).contains(&whole) {
        Ok(whole as i64)
    } else {
        Err(INT_BEYOND_I64)
    }
}

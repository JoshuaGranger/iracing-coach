//! Builtin `round()` (CPython 3.12 `float___round___impl`, `double_round`).

use crate::decimal;
use crate::error::{INT_BEYOND_I64, PyError, PyErrorKind, PyResult};
use crate::exact::decompose;
use crate::num::{Num, int_from_float};

/// `(int)((DBL_MANT_DIG - DBL_MIN_EXP) * 0.30103)`: above this every float rounds to itself.
const NDIGITS_MAX: i64 = 323;
/// `-(int)((DBL_MAX_EXP + 1) * 0.30103)`: below this every float rounds to a signed zero.
const NDIGITS_MIN: i64 = -308;

const POW5: [u128; 23] = {
    let mut table = [1u128; 23];
    let mut i = 1;
    while i < 23 {
        table[i] = table[i - 1] * 5;
        i += 1;
    }
    table
};

const POW10: [f64; 23] = [
    1e0, 1e1, 1e2, 1e3, 1e4, 1e5, 1e6, 1e7, 1e8, 1e9, 1e10, 1e11, 1e12, 1e13, 1e14, 1e15, 1e16,
    1e17, 1e18, 1e19, 1e20, 1e21, 1e22,
];

/// `round(x, ndigits)` for a float: the decimal value of `x` rounded half to even at
/// `ndigits` places (on the exact binary value), read back correctly rounded. NaN and the
/// infinities return themselves; `OverflowError` when the rounded value leaves the float
/// range.
pub fn round_ndigits(x: f64, ndigits: i64) -> PyResult<f64> {
    if !x.is_finite() {
        return Ok(x);
    }
    if ndigits > NDIGITS_MAX {
        return Ok(x);
    }
    if ndigits < NDIGITS_MIN {
        return Ok(0.0 * x);
    }
    let ndigits = ndigits as i32;
    if let Some(value) = round_fast(x, ndigits) {
        return Ok(value);
    }
    round_exact(x, ndigits)
}

/// `double_round` itself: dtoa mode 3, then `strtod` of `"[-]0<digits>e<decpt - len>"`.
pub(crate) fn round_exact(x: f64, ndigits: i32) -> PyResult<f64> {
    let d = decimal::mode3(x, ndigits);
    let mut text = String::with_capacity(d.digits.len() + 16);
    if x.is_sign_negative() {
        text.push('-');
    }
    text.push('0');
    text.push_str(std::str::from_utf8(&d.digits).expect("ASCII digits"));
    text.push('e');
    text.push_str(&(d.decpt - d.digits.len() as i32).to_string());
    let value: f64 = text.parse().expect("well-formed decimal");
    if value.is_infinite() {
        return Err(PyError::new(
            PyErrorKind::OverflowError,
            "rounded value too large to represent",
        ));
    }
    Ok(value)
}

/// Exact integer arithmetic for `0 <= ndigits <= 22` when `x * 10**ndigits` fits 128 bits
/// and the rounded integer fits 53 bits: then the result is one correctly rounded division
/// of two exactly represented floats, which is what `strtod` returns.
fn round_fast(x: f64, ndigits: i32) -> Option<f64> {
    if !(0..=22).contains(&ndigits) {
        return None;
    }
    let (negative, mantissa, exponent) = decompose(x);
    if exponent >= 0 {
        // an integer: rounding at or right of the decimal point leaves it unchanged
        return Some(x);
    }
    let product = u128::from(mantissa).checked_mul(POW5[ndigits as usize])?;
    // x * 10**ndigits = product * 2**(ndigits + exponent)
    let shift = -(ndigits + exponent);
    if shift <= 0 {
        return Some(x);
    }
    let rounded = if shift >= 128 {
        0
    } else {
        let quotient = product >> shift;
        let remainder = product & ((1u128 << shift) - 1);
        let half = 1u128 << (shift - 1);
        let up = remainder > half || (remainder == half && quotient & 1 == 1);
        quotient + u128::from(up)
    };
    if rounded >= 1 << 53 {
        return None;
    }
    let magnitude = rounded as f64 / POW10[ndigits as usize];
    Some(if negative { -magnitude } else { magnitude })
}

/// `round(x)` with no `ndigits` for a float, as the integral float CPython builds before
/// converting to int: C `round` (half away from zero) corrected to half to even.
pub fn round_half_even(x: f64) -> f64 {
    let mut rounded = x.round();
    if (x - rounded).abs() == 0.5 {
        rounded = 2.0 * (x / 2.0).round();
    }
    rounded
}

/// `round(x)` for a float: an int (`ValueError` for NaN, `OverflowError` for infinities).
pub fn round_to_int(x: f64) -> PyResult<i64> {
    if x.is_nan() {
        return Err(PyError::new(
            PyErrorKind::ValueError,
            "cannot convert float NaN to integer",
        ));
    }
    int_from_float(round_half_even(x))
}

/// `round(i, ndigits)` for an int: unchanged for `ndigits >= 0`, else rounded half to even
/// to a multiple of `10**-ndigits`.
pub fn round_int_ndigits(i: i64, ndigits: i64) -> PyResult<i64> {
    if ndigits >= 0 {
        return Ok(i);
    }
    if ndigits <= -20 {
        // |i| <= 2**63 < 10**20 / 2: every i64 rounds to 0 (and 2 * remainder would overflow
        // i128 at 10**38)
        return Ok(0);
    }
    let unit = 10i128.pow((-ndigits) as u32);
    let value = i128::from(i);
    let quotient = value.div_euclid(unit);
    let remainder = value.rem_euclid(unit);
    let twice = 2 * remainder;
    let up = twice > unit || (twice == unit && quotient & 1 == 1);
    let rounded = (quotient + i128::from(up)) * unit;
    i64::try_from(rounded).map_err(|_| INT_BEYOND_I64)
}

/// `round(value, ndigits)` keeping Python's result type.
pub fn round_num(value: Num, ndigits: Option<i64>) -> PyResult<Num> {
    match (value, ndigits) {
        (Num::Float(x), Some(n)) => round_ndigits(x, n).map(Num::Float),
        (Num::Float(x), None) => round_to_int(x).map(Num::Int),
        (Num::Int(i), Some(n)) => round_int_ndigits(i, n).map(Num::Int),
        (Num::Int(i), None) => Ok(Num::Int(i)),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fast_path_agrees_with_exact_path() {
        let mut state = 0x853C_49E6_748F_EA9Bu64;
        for _ in 0..200_000 {
            state ^= state << 13;
            state ^= state >> 7;
            state ^= state << 17;
            let scale = [1.0, 1e-3, 1e3, 1e-7, 1e6, 0.1][(state % 6) as usize];
            let x = ((state >> 11) as f64 / (1u64 << 53) as f64 - 0.5) * 2000.0 * scale;
            let digits = ((state >> 3) % 12) as i32;
            if let Some(fast) = round_fast(x, digits) {
                let exact = round_exact(x, digits).unwrap();
                assert_eq!(fast.to_bits(), exact.to_bits(), "round({x:?}, {digits})");
            }
        }
    }
}

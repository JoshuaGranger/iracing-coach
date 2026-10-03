//! Exact conversions between floats, integers and rationals.

use std::cmp::Ordering;

use crate::bigint::{BigInt, BigUint};
use crate::error::{PyError, PyErrorKind, PyResult};

/// A finite float as `(negative, mantissa, exponent)` with `|x| = mantissa * 2**exponent`.
pub(crate) fn decompose(x: f64) -> (bool, u64, i32) {
    let bits = x.to_bits();
    let negative = bits >> 63 == 1;
    let exponent = ((bits >> 52) & 0x7ff) as i32;
    let fraction = bits & ((1 << 52) - 1);
    if exponent == 0 {
        (negative, fraction, -1074)
    } else {
        (negative, fraction | (1 << 52), exponent - 1075)
    }
}

/// `2.0 ** k` for `-1074 <= k <= 1023`, exactly.
pub(crate) fn pow2(k: i32) -> f64 {
    debug_assert!((-1074..=1023).contains(&k));
    if k >= -1022 {
        f64::from_bits(((k + 1023) as u64) << 52)
    } else {
        f64::from_bits(1u64 << (k + 1074))
    }
}

/// `float.as_integer_ratio()`: the reduced `(numerator, denominator)`.
pub fn as_integer_ratio(x: f64) -> PyResult<(BigInt, BigUint)> {
    if x.is_nan() {
        return Err(PyError::new(
            PyErrorKind::ValueError,
            "cannot convert NaN to integer ratio",
        ));
    }
    if x.is_infinite() {
        return Err(PyError::new(
            PyErrorKind::OverflowError,
            "cannot convert Infinity to integer ratio",
        ));
    }
    let (negative, mut mantissa, mut exponent) = decompose(x);
    if mantissa == 0 {
        return Ok((BigInt::zero(), BigUint::one()));
    }
    let zeros = mantissa.trailing_zeros();
    mantissa >>= zeros;
    exponent += zeros as i32;
    let m = BigUint::from_u64(mantissa);
    if exponent >= 0 {
        Ok((
            BigInt::new(negative, m.shl(exponent as u64)),
            BigUint::one(),
        ))
    } else {
        Ok((
            BigInt::new(negative, m),
            BigUint::one().shl(u64::from(exponent.unsigned_abs())),
        ))
    }
}

/// `numerator / denominator` correctly rounded (half to even), with gradual underflow; an
/// infinity when the rounded value overflows. `denominator` must be nonzero.
pub(crate) fn ratio_to_f64(negative: bool, numerator: &BigUint, denominator: &BigUint) -> f64 {
    let sign = if negative { -1.0 } else { 1.0 };
    if numerator.is_zero() {
        return sign * 0.0;
    }
    // e = floor(log2(numerator / denominator))
    let mut e = numerator.bit_length() as i64 - denominator.bit_length() as i64;
    let at_least = if e >= 0 {
        numerator.cmp(&denominator.shl(e as u64)) != Ordering::Less
    } else {
        numerator.shl(e.unsigned_abs()).cmp(denominator) != Ordering::Less
    };
    if !at_least {
        e -= 1;
    }
    if e > 1023 {
        return sign * f64::INFINITY;
    }
    // Round to a multiple of the unit in the last place, 2**k.
    let k = (e - 52).max(-1074);
    let (n, d) = if k <= 0 {
        (numerator.shl(k.unsigned_abs()), denominator.clone())
    } else {
        (numerator.clone(), denominator.shl(k as u64))
    };
    let (q, r) = n.divrem(&d);
    let mut q = q.to_u64().expect("quotient below 2**54");
    match r.shl(1).cmp(&d) {
        Ordering::Greater => q += 1,
        Ordering::Equal if q & 1 == 1 => q += 1,
        _ => {}
    }
    sign * (q as f64 * pow2(k as i32))
}

/// Python `int / int` (`long_true_divide`), correctly rounded.
pub fn int_true_div(a: i64, b: i64) -> PyResult<f64> {
    if b == 0 {
        return Err(PyError::new(
            PyErrorKind::ZeroDivisionError,
            "division by zero",
        ));
    }
    const EXACT: u64 = 1 << 53;
    if a.unsigned_abs() <= EXACT && b.unsigned_abs() <= EXACT {
        return Ok(a as f64 / b as f64);
    }
    Ok(ratio_to_f64(
        (a < 0) != (b < 0),
        &BigUint::from_u64(a.unsigned_abs()),
        &BigUint::from_u64(b.unsigned_abs()),
    ))
}

/// `float(n / d)` for a signed big rational (`Fraction.__float__`, int true division).
pub(crate) fn bigint_true_div(numerator: &BigInt, denominator: &BigInt) -> PyResult<f64> {
    if denominator.is_zero() {
        return Err(PyError::new(
            PyErrorKind::ZeroDivisionError,
            "division by zero",
        ));
    }
    let value = ratio_to_f64(
        numerator.negative != denominator.negative,
        &numerator.magnitude,
        &denominator.magnitude,
    );
    if value.is_infinite() {
        return Err(PyError::new(
            PyErrorKind::OverflowError,
            "integer division result too large for a float",
        ));
    }
    Ok(value)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ratio_matches_hardware_division_for_exact_operands() {
        let mut state = 0x2545_F491_4F6C_DD1Du64;
        for _ in 0..50_000 {
            state ^= state << 13;
            state ^= state >> 7;
            state ^= state << 17;
            let a = (state >> 11) as i64 - (1 << 52);
            let b =
                ((state.rotate_left(29) >> 11) as i64 | 1) * if state & 1 == 1 { -1 } else { 1 };
            let expected = a as f64 / b as f64;
            let got = ratio_to_f64(
                (a < 0) != (b < 0),
                &BigUint::from_u64(a.unsigned_abs()),
                &BigUint::from_u64(b.unsigned_abs()),
            );
            assert_eq!(got.to_bits(), expected.to_bits(), "{a} / {b}");
        }
    }

    #[test]
    fn ratio_handles_subnormals_and_overflow() {
        let one = BigUint::one();
        assert_eq!(ratio_to_f64(false, &one, &one.shl(1074)), f64::from_bits(1));
        assert_eq!(ratio_to_f64(false, &one, &one.shl(1075)), 0.0);
        assert_eq!(
            ratio_to_f64(false, &BigUint::from_u64(3), &one.shl(1076)),
            f64::from_bits(1)
        );
        assert_eq!(ratio_to_f64(false, &one.shl(1024), &one), f64::INFINITY);
        let max = BigUint::from_u64((1 << 53) - 1).shl(971);
        assert_eq!(ratio_to_f64(false, &max, &one), f64::MAX);
    }
}

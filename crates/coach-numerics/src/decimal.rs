//! Decimal digit strings of floats, as David Gay's `dtoa` (CPython `_Py_dg_dtoa`) returns
//! them: a digit string without trailing zeros and the decimal point position `decpt`, so
//! that the value is `0.d1d2d3... * 10**decpt`.
//!
//! Mode 0 is the shortest string that round-trips; modes 2 and 3 are correctly rounded
//! (half to even on the exact binary value) to a number of significant digits or of digits
//! after the decimal point. Modes 2 and 3 work from the exact decimal expansion.

use crate::bigint::BigUint;
use crate::exact::decompose;

/// Digits (ASCII `0`-`9`, no trailing zeros) and the decimal point position.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Digits {
    pub digits: Vec<u8>,
    pub decpt: i32,
}

fn strip_trailing_zeros(digits: &mut Vec<u8>) {
    while digits.last() == Some(&b'0') {
        digits.pop();
    }
}

/// The exact decimal expansion of a finite nonzero `|x|`.
pub fn exact(x: f64) -> Digits {
    let (_, mantissa, exponent) = decompose(x);
    debug_assert!(mantissa != 0);
    let (text, decpt) = if exponent >= 0 {
        let text = BigUint::from_u64(mantissa)
            .shl(exponent as u64)
            .to_decimal();
        let len = text.len() as i32;
        (text, len)
    } else {
        // m * 2**-e = m * 5**e / 10**e
        let e = exponent.unsigned_abs();
        let text = BigUint::from_u64(mantissa)
            .mul(&BigUint::pow(5, e))
            .to_decimal();
        let len = text.len() as i32;
        (text, len - e as i32)
    };
    let mut digits = text.into_bytes();
    strip_trailing_zeros(&mut digits);
    Digits { digits, decpt }
}

/// Round an exact expansion to its first `keep` digits, half to even. `keep` may be zero or
/// negative (everything rounds away). Returns `None` when the result is zero.
fn round_to(exact: &Digits, keep: i64) -> Option<Digits> {
    let len = exact.digits.len() as i64;
    if keep >= len {
        return Some(exact.clone());
    }
    if keep < 0 {
        return None;
    }
    let keep = keep as usize;
    let next = exact.digits[keep];
    let rest_nonzero = exact.digits.len() > keep + 1;
    let last_odd = keep > 0 && (exact.digits[keep - 1] - b'0') % 2 == 1;
    let up = next > b'5' || (next == b'5' && (rest_nonzero || last_odd));
    let mut digits = exact.digits[..keep].to_vec();
    let mut decpt = exact.decpt;
    if up {
        let mut index = digits.len();
        loop {
            if index == 0 {
                digits.insert(0, b'1');
                decpt += 1;
                break;
            }
            index -= 1;
            if digits[index] == b'9' {
                digits[index] = b'0';
            } else {
                digits[index] += 1;
                break;
            }
        }
    }
    strip_trailing_zeros(&mut digits);
    if digits.is_empty() {
        None
    } else {
        Some(Digits { digits, decpt })
    }
}

/// `_Py_dg_dtoa(x, 2, ndigits)`: `ndigits >= 1` significant digits. `x` finite.
pub fn mode2(x: f64, ndigits: i32) -> Digits {
    if x == 0.0 {
        return Digits {
            digits: b"0".to_vec(),
            decpt: 1,
        };
    }
    round_to(&exact(x), i64::from(ndigits.max(1))).expect("nonzero value keeps a digit")
}

/// `_Py_dg_dtoa(x, 3, ndigits)`: `ndigits` digits after the decimal point (negative: to the
/// left of it). A result that rounds to zero is the empty string with `decpt = -ndigits`.
pub fn mode3(x: f64, ndigits: i32) -> Digits {
    if x == 0.0 {
        return Digits {
            digits: b"0".to_vec(),
            decpt: 1,
        };
    }
    let exact = exact(x);
    let keep = i64::from(exact.decpt) + i64::from(ndigits);
    round_to(&exact, keep).unwrap_or(Digits {
        digits: Vec::new(),
        decpt: -ndigits,
    })
}

/// `_Py_dg_dtoa(x, 0, 0)`: the shortest digits that read back as `x`, nearest to `x`, and
/// of two equally near the even one. `x` finite and nonzero.
pub fn shortest(x: f64) -> Digits {
    // Rust's `LowerExp` prints the shortest round-trip digits, choosing the closest; on an
    // exact tie it rounds up where dtoa rounds to even.
    let text = format!("{:e}", x.abs());
    let (mantissa, exponent) = text.split_once('e').expect("LowerExp has an exponent");
    let exponent: i32 = exponent.parse().expect("LowerExp exponent is an integer");
    let mut digits: Vec<u8> = mantissa.bytes().filter(u8::is_ascii_digit).collect();
    strip_trailing_zeros(&mut digits);
    let rust = Digits {
        digits,
        decpt: exponent + 1,
    };
    tie_to_even(x.abs(), &rust).unwrap_or(rust)
}

/// [`shortest`] without a heap allocation (port phase 3: the `analysis.json` writer formats
/// about a million floats per race). The digits go to `digits`; returns `(length, decpt)`.
/// The same digits as [`shortest`] by construction: Rust's digits are taken unchanged unless
/// [`may_tie`] cannot rule out a tie, and then [`tie_to_even`] itself decides.
pub(crate) fn shortest_into(x: f64, digits: &mut [u8; 24]) -> (usize, i32) {
    use std::fmt::Write as _;

    struct Stack {
        bytes: [u8; 40],
        len: usize,
    }
    impl std::fmt::Write for Stack {
        fn write_str(&mut self, text: &str) -> std::fmt::Result {
            let end = self.len + text.len();
            if end > self.bytes.len() {
                return Err(std::fmt::Error);
            }
            self.bytes[self.len..end].copy_from_slice(text.as_bytes());
            self.len = end;
            Ok(())
        }
    }
    let x = x.abs();
    let mut text = Stack {
        bytes: [0; 40],
        len: 0,
    };
    // At most 17 digits, a point, `e` and a four-character exponent.
    write!(text, "{x:e}").expect("LowerExp of a finite float fits 40 bytes");
    let text = &text.bytes[..text.len];
    let e = text
        .iter()
        .position(|&b| b == b'e')
        .expect("LowerExp has an exponent");
    let mut len = 0;
    for &byte in &text[..e] {
        if byte.is_ascii_digit() {
            digits[len] = byte;
            len += 1;
        }
    }
    while len > 1 && digits[len - 1] == b'0' {
        len -= 1;
    }
    let (negative, magnitude) = match text[e + 1] {
        b'-' => (true, &text[e + 2..]),
        _ => (false, &text[e + 1..]),
    };
    let exponent = magnitude
        .iter()
        .fold(0i32, |value, &b| value * 10 + i32::from(b - b'0'));
    let decpt = if negative { -exponent } else { exponent } + 1;
    if may_tie(x, len) {
        let rust = Digits {
            digits: digits[..len].to_vec(),
            decpt,
        };
        if let Some(even) = tie_to_even(x, &rust) {
            digits[..even.digits.len()].copy_from_slice(&even.digits);
            return (even.digits.len(), even.decpt);
        }
    }
    (len, decpt)
}

/// False only where [`tie_to_even`] returns `None` for a `k`-digit shortest string: the exact
/// expansion `n` (without trailing zeros) is not `k + 1` digits ending in 5. Decided in `u64`
/// whenever `n` fits; `n >= 10**18` has more than `k + 1 <= 18` digits. True means "ask
/// `tie_to_even`", never "a tie".
fn may_tie(x: f64, k: usize) -> bool {
    let (_, mut mantissa, mut exponent) = decompose(x);
    let zeros = mantissa.trailing_zeros();
    mantissa >>= zeros;
    exponent += zeros as i32;
    let mut n = if exponent >= 0 {
        if exponent >= 64 || mantissa.leading_zeros() < exponent as u32 {
            return true;
        }
        mantissa << exponent
    } else {
        // n = mantissa * 5**p with an odd mantissa: no trailing zero, so its length is final.
        let p = exponent.unsigned_abs();
        if p > 26 {
            return false;
        }
        let wide = u128::from(mantissa) * 5u128.pow(p);
        if wide >= 1_000_000_000_000_000_000 {
            return false;
        }
        wide as u64
    };
    while n != 0 && n % 10 == 0 {
        n /= 10;
    }
    let mut length = 1;
    let mut rest = n / 10;
    while rest != 0 {
        length += 1;
        rest /= 10;
    }
    n % 10 == 5 && length == k + 1
}

/// When `x` is exactly halfway between two `k`-digit decimals (`k` the length Rust chose),
/// the even one if it reads back as `x`. A tie needs an exact expansion of `k + 1 <= 18`
/// significant digits ending in 5, so only small exact values are examined.
fn tie_to_even(x: f64, rust: &Digits) -> Option<Digits> {
    let k = rust.digits.len();
    let (_, mut mantissa, mut exponent) = decompose(x);
    let zeros = mantissa.trailing_zeros();
    mantissa >>= zeros;
    exponent += zeros as i32;
    // x = n * 10**-scale exactly
    let (mut n, mut scale): (u128, i32) = if exponent >= 0 {
        if exponent > 74 {
            return None;
        }
        (u128::from(mantissa) << exponent, 0)
    } else {
        let p = exponent.unsigned_abs();
        if p > 26 {
            return None;
        }
        (u128::from(mantissa) * 5u128.pow(p), p as i32)
    };
    while n % 10 == 0 {
        n /= 10;
        scale -= 1;
    }
    let exact_len = n.to_string().len();
    if exact_len != k + 1 || n % 10 != 5 {
        return None;
    }
    let lower = n / 10;
    let even = if lower % 2 == 0 { lower } else { lower + 1 };
    let text = even.to_string();
    let decpt = exact_len as i32 - scale + (text.len() as i32 - k as i32);
    let mut digits = text.into_bytes();
    strip_trailing_zeros(&mut digits);
    let candidate = Digits { digits, decpt };
    let literal = format!(
        "0.{}e{}",
        std::str::from_utf8(&candidate.digits).expect("ASCII digits"),
        candidate.decpt
    );
    (literal.parse::<f64>().ok() == Some(x)).then_some(candidate)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn text(d: &Digits) -> (String, i32) {
        (String::from_utf8(d.digits.clone()).unwrap(), d.decpt)
    }

    #[test]
    fn exact_expansions() {
        assert_eq!(text(&exact(0.5)), ("5".into(), 0));
        assert_eq!(text(&exact(1024.0)), ("1024".into(), 4));
        assert_eq!(text(&exact(0.1)).0.len(), 55);
        assert_eq!(text(&exact(f64::from_bits(1))).0.len(), 751);
    }

    #[test]
    fn ties_round_to_even() {
        assert_eq!(text(&mode3(2.5, 0)), ("2".into(), 1));
        assert_eq!(text(&mode3(3.5, 0)), ("4".into(), 1));
        assert_eq!(text(&mode3(0.125, 2)), ("12".into(), 0));
        assert_eq!(text(&mode3(0.375, 2)), ("38".into(), 0));
        assert_eq!(text(&mode3(0.4, 0)), (String::new(), 0));
        assert_eq!(text(&mode3(0.5, 0)), (String::new(), 0));
        assert_eq!(text(&mode3(0.51, 0)), ("1".into(), 1));
        assert_eq!(text(&mode3(9.96, 1)), ("1".into(), 2));
        assert_eq!(text(&mode2(123456.0, 3)), ("123".into(), 6));
    }
}

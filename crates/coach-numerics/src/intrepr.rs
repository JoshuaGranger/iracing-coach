//! `repr(int)` of an int of any size (port phase 3: the `analysis.json` writer receives ints
//! beyond `i64` as pickle `LONG1`/`LONG4` bytes, `docs/08-rewrite/port/phase3-writer.md`).

use crate::bigint::BigUint;

/// `repr(int.from_bytes(bytes, "little", signed=True))`: the decimal digits, with `-` for a
/// negative value, `"0"` for no bytes. `None` when the value has more decimal digits (sign not
/// counted) than `max_digits`: CPython 3.12 then raises `ValueError` ("Exceeds the limit (N
/// digits) for integer string conversion; ...", `sys.get_int_max_str_digits()`, where 0 means no
/// limit and is passed here as `None`).
pub fn int_repr_from_le_bytes(bytes: &[u8], max_digits: Option<u64>) -> Option<String> {
    let negative = bytes.last().is_some_and(|byte| byte & 0x80 != 0);
    let magnitude = if negative {
        // Two's complement: invert and add one.
        let mut inverted: Vec<u8> = bytes.iter().map(|byte| !byte).collect();
        for byte in &mut inverted {
            let (sum, carry) = byte.overflowing_add(1);
            *byte = sum;
            if !carry {
                break;
            }
        }
        BigUint::from_le_bytes(&inverted)
    } else {
        BigUint::from_le_bytes(bytes)
    };
    if let Some(limit) = max_digits {
        // A value of bit length b >= 1 has at least floor((b - 1) * log10(2)) + 1 digits; the
        // bound is lowered by a margin so float rounding can only make it weaker, then the exact
        // count decides.
        let bits = magnitude.bit_length();
        if bits > 0 {
            let lower = ((bits - 1) as f64 * std::f64::consts::LOG10_2 - 1e-6).floor() as u64 + 1;
            if lower > limit {
                return None;
            }
        }
    }
    let digits = magnitude.to_decimal();
    if max_digits.is_some_and(|limit| digits.len() as u64 > limit) {
        return None;
    }
    Some(if negative && digits != "0" {
        format!("-{digits}")
    } else {
        digits
    })
}

#[cfg(test)]
mod tests {
    use super::int_repr_from_le_bytes;

    fn le(value: i128) -> Vec<u8> {
        value.to_le_bytes().to_vec()
    }

    #[test]
    fn small_and_wide_values() {
        for value in [
            0i128,
            1,
            -1,
            255,
            -256,
            i64::MAX as i128 + 1,
            i64::MIN as i128 - 1,
            i128::MAX,
            i128::MIN,
        ] {
            assert_eq!(
                int_repr_from_le_bytes(&le(value), None).unwrap(),
                value.to_string()
            );
        }
        assert_eq!(int_repr_from_le_bytes(&[], None).unwrap(), "0");
    }

    #[test]
    fn digit_limit_counts_digits_not_the_sign() {
        // 10**20 has 21 digits; -(10**20) too.
        let big = le(100_000_000_000_000_000_000);
        let negative = le(-100_000_000_000_000_000_000);
        assert!(int_repr_from_le_bytes(&big, Some(21)).is_some());
        assert!(int_repr_from_le_bytes(&negative, Some(21)).is_some());
        assert!(int_repr_from_le_bytes(&big, Some(20)).is_none());
        assert!(int_repr_from_le_bytes(&negative, Some(20)).is_none());
    }
}

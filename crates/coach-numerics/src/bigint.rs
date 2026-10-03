//! Arbitrary-precision integers for the exact paths: `float.as_integer_ratio`, `Fraction`
//! sums in `statistics`, correctly rounded int/int division, exact decimal expansion and
//! `math.isqrt`. Little-endian `u32` limbs, always normalized (no high zero limb).

use std::cmp::Ordering;

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct BigUint {
    limbs: Vec<u32>,
}

impl BigUint {
    pub fn zero() -> Self {
        Self { limbs: Vec::new() }
    }

    pub fn one() -> Self {
        Self::from_u64(1)
    }

    pub fn from_u64(value: u64) -> Self {
        let mut out = Self {
            limbs: vec![value as u32, (value >> 32) as u32],
        };
        out.normalize();
        out
    }

    #[cfg(test)]
    pub fn from_u128(value: u128) -> Self {
        let mut out = Self {
            limbs: vec![
                value as u32,
                (value >> 32) as u32,
                (value >> 64) as u32,
                (value >> 96) as u32,
            ],
        };
        out.normalize();
        out
    }

    fn normalize(&mut self) {
        while self.limbs.last() == Some(&0) {
            self.limbs.pop();
        }
    }

    pub fn is_zero(&self) -> bool {
        self.limbs.is_empty()
    }

    pub fn is_odd(&self) -> bool {
        self.limbs.first().is_some_and(|limb| limb & 1 == 1)
    }

    /// `int.bit_length()`.
    pub fn bit_length(&self) -> u64 {
        match self.limbs.last() {
            None => 0,
            Some(&top) => (self.limbs.len() as u64 - 1) * 32 + u64::from(32 - top.leading_zeros()),
        }
    }

    /// Number of trailing zero bits; 0 for zero.
    pub fn trailing_zeros(&self) -> u64 {
        for (index, &limb) in self.limbs.iter().enumerate() {
            if limb != 0 {
                return index as u64 * 32 + u64::from(limb.trailing_zeros());
            }
        }
        0
    }

    pub fn to_u64(&self) -> Option<u64> {
        match self.limbs.len() {
            0 => Some(0),
            1 => Some(u64::from(self.limbs[0])),
            2 => Some(u64::from(self.limbs[0]) | (u64::from(self.limbs[1]) << 32)),
            _ => None,
        }
    }

    pub fn add(&self, other: &Self) -> Self {
        let (long, short) = if self.limbs.len() >= other.limbs.len() {
            (self, other)
        } else {
            (other, self)
        };
        let mut limbs = Vec::with_capacity(long.limbs.len() + 1);
        let mut carry = 0u64;
        for (index, &limb) in long.limbs.iter().enumerate() {
            let sum = u64::from(limb) + u64::from(*short.limbs.get(index).unwrap_or(&0)) + carry;
            limbs.push(sum as u32);
            carry = sum >> 32;
        }
        if carry != 0 {
            limbs.push(carry as u32);
        }
        Self { limbs }
    }

    /// `self - other`; `other` must not exceed `self`.
    pub fn sub(&self, other: &Self) -> Self {
        debug_assert!(self.cmp(other) != Ordering::Less);
        let mut limbs = Vec::with_capacity(self.limbs.len());
        let mut borrow = 0i64;
        for (index, &limb) in self.limbs.iter().enumerate() {
            let mut diff =
                i64::from(limb) - i64::from(*other.limbs.get(index).unwrap_or(&0)) - borrow;
            borrow = 0;
            if diff < 0 {
                diff += 1 << 32;
                borrow = 1;
            }
            limbs.push(diff as u32);
        }
        let mut out = Self { limbs };
        out.normalize();
        out
    }

    pub fn mul(&self, other: &Self) -> Self {
        if self.is_zero() || other.is_zero() {
            return Self::zero();
        }
        let mut limbs = vec![0u32; self.limbs.len() + other.limbs.len()];
        for (i, &a) in self.limbs.iter().enumerate() {
            let mut carry = 0u64;
            for (j, &b) in other.limbs.iter().enumerate() {
                let t = u64::from(a) * u64::from(b) + u64::from(limbs[i + j]) + carry;
                limbs[i + j] = t as u32;
                carry = t >> 32;
            }
            let mut k = i + other.limbs.len();
            while carry != 0 {
                let t = u64::from(limbs[k]) + carry;
                limbs[k] = t as u32;
                carry = t >> 32;
                k += 1;
            }
        }
        let mut out = Self { limbs };
        out.normalize();
        out
    }

    pub fn mul_small(&self, factor: u32) -> Self {
        let mut limbs = Vec::with_capacity(self.limbs.len() + 1);
        let mut carry = 0u64;
        for &limb in &self.limbs {
            let t = u64::from(limb) * u64::from(factor) + carry;
            limbs.push(t as u32);
            carry = t >> 32;
        }
        if carry != 0 {
            limbs.push(carry as u32);
        }
        let mut out = Self { limbs };
        out.normalize();
        out
    }

    /// Quotient and remainder by a nonzero `u32`.
    pub fn divrem_small(&self, divisor: u32) -> (Self, u32) {
        assert!(divisor != 0, "division by zero");
        let mut limbs = vec![0u32; self.limbs.len()];
        let mut rem = 0u64;
        for index in (0..self.limbs.len()).rev() {
            let cur = (rem << 32) | u64::from(self.limbs[index]);
            limbs[index] = (cur / u64::from(divisor)) as u32;
            rem = cur % u64::from(divisor);
        }
        let mut out = Self { limbs };
        out.normalize();
        (out, rem as u32)
    }

    /// Quotient and remainder by a nonzero divisor (Knuth, TAOCP 4.3.1, algorithm D).
    pub fn divrem(&self, divisor: &Self) -> (Self, Self) {
        assert!(!divisor.is_zero(), "division by zero");
        if self.cmp(divisor) == Ordering::Less {
            return (Self::zero(), self.clone());
        }
        if divisor.limbs.len() == 1 {
            let (q, r) = self.divrem_small(divisor.limbs[0]);
            return (q, Self::from_u64(u64::from(r)));
        }
        let shift = divisor.limbs[divisor.limbs.len() - 1].leading_zeros();
        let v = divisor.shl(u64::from(shift)).limbs;
        let mut u = self.shl(u64::from(shift)).limbs;
        if u.len() == self.limbs.len() {
            u.push(0);
        }
        let n = v.len();
        let m = u.len() - n;
        let mut q = vec![0u32; m];
        let v_top = u64::from(v[n - 1]);
        let v_next = u64::from(v[n - 2]);
        for j in (0..m).rev() {
            let numerator = (u64::from(u[j + n]) << 32) | u64::from(u[j + n - 1]);
            let mut qhat = numerator / v_top;
            let mut rhat = numerator % v_top;
            while qhat >= (1 << 32) || qhat * v_next > ((rhat << 32) | u64::from(u[j + n - 2])) {
                qhat -= 1;
                rhat += v_top;
                if rhat >= (1 << 32) {
                    break;
                }
            }
            let mut borrow = 0i64;
            let mut carry = 0u64;
            for i in 0..n {
                let product = qhat * u64::from(v[i]) + carry;
                carry = product >> 32;
                let t = i64::from(u[i + j]) - borrow - (product & 0xffff_ffff) as i64;
                u[i + j] = t as u32;
                borrow = i64::from(t < 0);
            }
            let t = i64::from(u[j + n]) - borrow - carry as i64;
            u[j + n] = t as u32;
            if t < 0 {
                qhat -= 1;
                let mut c = 0u64;
                for i in 0..n {
                    let s = u64::from(u[i + j]) + u64::from(v[i]) + c;
                    u[i + j] = s as u32;
                    c = s >> 32;
                }
                u[j + n] = u[j + n].wrapping_add(c as u32);
            }
            q[j] = qhat as u32;
        }
        let mut quotient = Self { limbs: q };
        quotient.normalize();
        u.truncate(n);
        let mut remainder = Self { limbs: u };
        remainder.normalize();
        (quotient, remainder.shr(u64::from(shift)))
    }

    pub fn shl(&self, bits: u64) -> Self {
        if self.is_zero() {
            return Self::zero();
        }
        let whole = (bits / 32) as usize;
        let part = (bits % 32) as u32;
        let mut limbs = vec![0u32; whole];
        limbs.reserve(self.limbs.len() + 1);
        if part == 0 {
            limbs.extend_from_slice(&self.limbs);
        } else {
            let mut carry = 0u32;
            for &limb in &self.limbs {
                limbs.push((limb << part) | carry);
                carry = limb >> (32 - part);
            }
            if carry != 0 {
                limbs.push(carry);
            }
        }
        Self { limbs }
    }

    pub fn shr(&self, bits: u64) -> Self {
        let whole = (bits / 32) as usize;
        if whole >= self.limbs.len() {
            return Self::zero();
        }
        let part = (bits % 32) as u32;
        let src = &self.limbs[whole..];
        let mut limbs = Vec::with_capacity(src.len());
        if part == 0 {
            limbs.extend_from_slice(src);
        } else {
            for index in 0..src.len() {
                let high = src.get(index + 1).map_or(0, |&next| next << (32 - part));
                limbs.push((src[index] >> part) | high);
            }
        }
        let mut out = Self { limbs };
        out.normalize();
        out
    }

    /// `base ** exponent`.
    pub fn pow(base: u32, exponent: u32) -> Self {
        let mut result = Self::one();
        let mut square = Self::from_u64(u64::from(base));
        let mut e = exponent;
        while e > 0 {
            if e & 1 == 1 {
                result = result.mul(&square);
            }
            e >>= 1;
            if e > 0 {
                square = square.mul(&square);
            }
        }
        result
    }

    /// `math.gcd` (binary algorithm).
    pub fn gcd(a: &Self, b: &Self) -> Self {
        if a.is_zero() {
            return b.clone();
        }
        if b.is_zero() {
            return a.clone();
        }
        let za = a.trailing_zeros();
        let zb = b.trailing_zeros();
        let common = za.min(zb);
        let mut x = a.shr(za);
        let mut y = b.shr(zb);
        loop {
            if x.cmp(&y) == Ordering::Greater {
                std::mem::swap(&mut x, &mut y);
            }
            y = y.sub(&x);
            if y.is_zero() {
                return x.shl(common);
            }
            y = y.shr(y.trailing_zeros());
        }
    }

    /// `math.isqrt`: the floor of the square root.
    pub fn isqrt(&self) -> Self {
        if self.is_zero() {
            return Self::zero();
        }
        let mut x = Self::one().shl(self.bit_length().div_ceil(2));
        loop {
            let (q, _) = self.divrem(&x);
            let y = x.add(&q).shr(1);
            if y.cmp(&x) != Ordering::Less {
                return x;
            }
            x = y;
        }
    }

    /// The magnitude of little-endian bytes (unsigned).
    pub fn from_le_bytes(bytes: &[u8]) -> Self {
        let mut out = Self {
            limbs: bytes
                .chunks(4)
                .map(|chunk| {
                    let mut word = [0u8; 4];
                    word[..chunk.len()].copy_from_slice(chunk);
                    u32::from_le_bytes(word)
                })
                .collect(),
        };
        out.normalize();
        out
    }

    /// Decimal digits, most significant first; `"0"` for zero.
    pub fn to_decimal(&self) -> String {
        if self.is_zero() {
            return "0".to_owned();
        }
        let mut chunks = Vec::new();
        let mut rest = self.clone();
        while !rest.is_zero() {
            let (q, r) = rest.divrem_small(1_000_000_000);
            chunks.push(r);
            rest = q;
        }
        let mut out = chunks.pop().map_or_else(String::new, |top| top.to_string());
        for chunk in chunks.iter().rev() {
            out.push_str(&format!("{chunk:09}"));
        }
        out
    }

    /// Parse ASCII decimal digits (no sign, no separators).
    pub fn from_decimal(digits: &str) -> Option<Self> {
        if digits.is_empty() || !digits.bytes().all(|b| b.is_ascii_digit()) {
            return None;
        }
        let mut out = Self::zero();
        for chunk in digits.as_bytes().chunks(9) {
            let mut value = 0u32;
            for &b in chunk {
                value = value * 10 + u32::from(b - b'0');
            }
            out = out
                .mul_small(10u32.pow(chunk.len() as u32))
                .add(&Self::from_u64(u64::from(value)));
        }
        Some(out)
    }
}

impl PartialOrd for BigUint {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

impl Ord for BigUint {
    fn cmp(&self, other: &Self) -> Ordering {
        self.limbs
            .len()
            .cmp(&other.limbs.len())
            .then_with(|| self.limbs.iter().rev().cmp(other.limbs.iter().rev()))
    }
}

/// Signed integer: sign and magnitude, zero is never negative.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct BigInt {
    pub negative: bool,
    pub magnitude: BigUint,
}

impl BigInt {
    pub fn zero() -> Self {
        Self::default()
    }

    pub fn new(negative: bool, magnitude: BigUint) -> Self {
        let negative = negative && !magnitude.is_zero();
        Self {
            negative,
            magnitude,
        }
    }

    pub fn from_i64(value: i64) -> Self {
        Self::new(value < 0, BigUint::from_u64(value.unsigned_abs()))
    }

    pub fn is_zero(&self) -> bool {
        self.magnitude.is_zero()
    }

    pub fn to_i64(&self) -> Option<i64> {
        let magnitude = self.magnitude.to_u64()?;
        if self.negative {
            if magnitude <= 1u64 << 63 {
                Some((magnitude as i64).wrapping_neg())
            } else {
                None
            }
        } else {
            i64::try_from(magnitude).ok()
        }
    }

    pub fn neg(&self) -> Self {
        Self::new(!self.negative, self.magnitude.clone())
    }

    pub fn add(&self, other: &Self) -> Self {
        if self.negative == other.negative {
            return Self::new(self.negative, self.magnitude.add(&other.magnitude));
        }
        match self.magnitude.cmp(&other.magnitude) {
            Ordering::Less => Self::new(other.negative, other.magnitude.sub(&self.magnitude)),
            _ => Self::new(self.negative, self.magnitude.sub(&other.magnitude)),
        }
    }

    pub fn sub(&self, other: &Self) -> Self {
        self.add(&other.neg())
    }

    pub fn mul(&self, other: &Self) -> Self {
        Self::new(
            self.negative != other.negative,
            self.magnitude.mul(&other.magnitude),
        )
    }

    pub fn shl(&self, bits: u64) -> Self {
        Self::new(self.negative, self.magnitude.shl(bits))
    }

    /// Python `divmod` on integers: floor quotient, remainder with the divisor's sign.
    pub fn floor_divmod(&self, divisor: &Self) -> (Self, Self) {
        let (q, r) = self.magnitude.divrem(&divisor.magnitude);
        let mut quotient = Self::new(self.negative != divisor.negative, q);
        let mut remainder = Self::new(self.negative, r);
        if !remainder.is_zero() && remainder.negative != divisor.negative {
            quotient = quotient.sub(&Self::from_i64(1));
            remainder = remainder.add(divisor);
        }
        (quotient, remainder)
    }
}

impl PartialOrd for BigInt {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

impl Ord for BigInt {
    fn cmp(&self, other: &Self) -> Ordering {
        match (self.negative, other.negative) {
            (false, true) => Ordering::Greater,
            (true, false) => Ordering::Less,
            (false, false) => self.magnitude.cmp(&other.magnitude),
            (true, true) => other.magnitude.cmp(&self.magnitude),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    struct Rng(u64);
    impl Rng {
        fn next(&mut self) -> u64 {
            self.0 ^= self.0 << 13;
            self.0 ^= self.0 >> 7;
            self.0 ^= self.0 << 17;
            self.0
        }
        fn big(&mut self, max_limbs: u64) -> BigUint {
            let count = (self.next() % max_limbs + 1) as usize;
            let mut limbs: Vec<u32> = (0..count).map(|_| self.next() as u32).collect();
            if self.next().is_multiple_of(4) {
                limbs[count - 1] = u32::MAX;
            }
            if self.next().is_multiple_of(5) && count > 1 {
                limbs[0] = 0;
            }
            let mut out = BigUint { limbs };
            out.normalize();
            out
        }
    }

    #[test]
    fn arithmetic_agrees_with_u128() {
        let mut rng = Rng(0x9E37_79B9_7F4A_7C15);
        for _ in 0..20_000 {
            let a = u128::from(rng.next()) << (rng.next() % 64) | u128::from(rng.next() >> 3);
            let b = u128::from(rng.next() >> (rng.next() % 64)).max(1);
            let (ba, bb) = (BigUint::from_u128(a), BigUint::from_u128(b));
            if let Some(sum) = a.checked_add(b) {
                assert_eq!(ba.add(&bb), BigUint::from_u128(sum));
            }
            if let Some(product) = a.checked_mul(b) {
                assert_eq!(ba.mul(&bb), BigUint::from_u128(product));
            }
            if a >= b {
                assert_eq!(ba.sub(&bb), BigUint::from_u128(a - b));
            }
            let (q, r) = ba.divrem(&bb);
            assert_eq!(
                (q, r),
                (BigUint::from_u128(a / b), BigUint::from_u128(a % b))
            );
            assert_eq!(BigUint::from_u128(a).to_decimal(), a.to_string());
            assert_eq!(ba.bit_length(), u64::from(128 - a.leading_zeros()));
        }
    }

    #[test]
    fn division_identity_holds_for_large_operands() {
        let mut rng = Rng(0xD1B5_4A32_D192_ED03);
        for _ in 0..3_000 {
            let n = rng.big(40);
            let d = rng.big(20);
            if d.is_zero() {
                continue;
            }
            let (q, r) = n.divrem(&d);
            assert!(r < d);
            assert_eq!(q.mul(&d).add(&r), n);
            let s = rng.next() % 100;
            assert_eq!(n.shl(s).shr(s), n);
            let root = n.isqrt();
            assert!(root.mul(&root) <= n);
            let next = root.add(&BigUint::one());
            assert!(next.mul(&next) > n);
            assert_eq!(BigUint::from_decimal(&n.to_decimal()), Some(n.clone()));
            let g = BigUint::gcd(&n, &d);
            if !g.is_zero() {
                assert!(n.divrem(&g).1.is_zero() && d.divrem(&g).1.is_zero());
            }
        }
    }
}

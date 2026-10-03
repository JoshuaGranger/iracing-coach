//! `statistics` (CPython 3.12 `Lib/statistics.py`): `fmean`, `mean`, `median`, `variance`,
//! `pvariance`, `stdev`, `pstdev`. The exact paths use the same rationals `Fraction` holds.

use crate::bigint::{BigInt, BigUint};
use crate::error::{PyError, PyErrorKind, PyResult};
use crate::exact::{as_integer_ratio, bigint_true_div};
use crate::num::{self, Num};
use crate::order;
use crate::sum::{fsum, fsum_nums};

fn statistics_error(message: &'static str) -> PyError {
    PyError::new(PyErrorKind::StatisticsError, message)
}

/// `statistics.fmean(data)` for floats: `fsum(data) / len(data)`.
pub fn fmean(data: &[f64]) -> PyResult<f64> {
    let total = fsum(data)?;
    if data.is_empty() {
        return Err(statistics_error("fmean requires at least one data point"));
    }
    Ok(total / data.len() as f64)
}

/// `statistics.fmean(data)` for ints and floats.
pub fn fmean_nums(data: &[Num]) -> PyResult<f64> {
    let total = fsum_nums(data)?;
    if data.is_empty() {
        return Err(statistics_error("fmean requires at least one data point"));
    }
    Ok(total / data.len() as f64)
}

/// `_exact_ratio` per item, or the non-finite float itself.
enum Ratio {
    /// numerator, log2 of the (power-of-two) denominator
    Exact(BigInt, u64),
    NonFinite(f64),
}

fn exact_ratio(value: Num) -> Ratio {
    match value {
        Num::Int(i) => Ratio::Exact(BigInt::from_i64(i), 0),
        Num::Float(f) => match as_integer_ratio(f) {
            Ok((numerator, denominator)) => Ratio::Exact(numerator, denominator.bit_length() - 1),
            Err(_) => Ratio::NonFinite(f),
        },
    }
}

/// The result type `_coerce` picks: `float` when any item is a float, else `int`.
fn any_float(data: &[Num]) -> bool {
    data.iter().any(|value| value.is_float())
}

/// Sums shared by `_sum` and `_ss`: `sum(x)` and `sum(x*x)` over a common power-of-two
/// denominator (`2**k` and `2**(2k)`), or the running sum of the non-finite items
/// (`partials[None]`, which starts from the int 0).
struct Sums {
    count: u64,
    shift: u64,
    sx: BigInt,
    sxx: BigInt,
    special: Option<f64>,
}

/// One pass, retaining only the two running sums: when an item's denominator `2**k` exceeds
/// the common one, the sums are rescaled to it (at most 1,074 times, the float range), so
/// memory does not grow with the data (CPython keeps one partial per distinct denominator).
fn sums(data: &[Num], squares: bool) -> Sums {
    let mut special: Option<f64> = None;
    let mut shift = 0u64;
    let mut sx = BigInt::zero();
    let mut sxx = BigInt::zero();
    for &value in data {
        match exact_ratio(value) {
            Ratio::Exact(numerator, k) => {
                if special.is_some() {
                    continue;
                }
                if k > shift {
                    sx = sx.shl(k - shift);
                    if squares {
                        sxx = sxx.shl(2 * (k - shift));
                    }
                    shift = k;
                }
                sx = sx.add(&numerator.shl(shift - k));
                if squares {
                    sxx = sxx.add(&numerator.mul(&numerator).shl(2 * (shift - k)));
                }
            }
            Ratio::NonFinite(f) => special = Some(special.map_or(0.0 + f, |s| s + f)),
        }
    }
    Sums {
        count: data.len() as u64,
        shift,
        sx,
        sxx,
        special,
    }
}

/// `_convert(Fraction(numerator, denominator), T)`.
fn convert(numerator: &BigInt, denominator: &BigInt, as_float: bool) -> PyResult<Num> {
    if !as_float {
        let (q, r) = numerator.floor_divmod(denominator);
        if r.is_zero() {
            return q.to_i64().map(Num::Int).ok_or(crate::error::INT_BEYOND_I64);
        }
    }
    bigint_true_div(numerator, denominator).map(Num::Float)
}

fn count_int(count: u64) -> BigInt {
    BigInt::from_i64(count as i64)
}

/// `statistics.mean(data)`: the exact rational mean converted to the data's type (an int
/// mean of ints stays an int).
pub fn mean(data: &[Num]) -> PyResult<Num> {
    let s = sums(data, false);
    if s.count < 1 {
        return Err(statistics_error("mean requires at least one data point"));
    }
    if let Some(total) = s.special {
        return Ok(Num::Float(total / s.count as f64));
    }
    let denominator = BigInt::new(false, BigUint::one().shl(s.shift)).mul(&count_int(s.count));
    convert(&s.sx, &denominator, any_float(data))
}

/// `_ss(data)`: `count * sxx - sx * sx` over `2**(2k) * count`, or the non-finite sum.
enum SumOfSquares {
    Exact {
        numerator: BigInt,
        denominator: BigInt,
    },
    NonFinite(f64),
}

fn sum_of_squares(data: &[Num]) -> (SumOfSquares, u64) {
    let s = sums(data, true);
    if let Some(total) = s.special {
        return (SumOfSquares::NonFinite(total), s.count);
    }
    let n = count_int(s.count);
    let numerator = n.mul(&s.sxx).sub(&s.sx.mul(&s.sx));
    let denominator = BigInt::new(false, BigUint::one().shl(2 * s.shift)).mul(&n);
    (
        SumOfSquares::Exact {
            numerator,
            denominator,
        },
        s.count,
    )
}

fn variance_with(data: &[Num], sample: bool) -> PyResult<Num> {
    let (ss, n) = sum_of_squares(data);
    let divisor = if sample { n.saturating_sub(1) } else { n };
    match ss {
        SumOfSquares::NonFinite(total) => Ok(Num::Float(total / divisor as f64)),
        SumOfSquares::Exact {
            numerator,
            denominator,
        } => convert(
            &numerator,
            &denominator.mul(&count_int(divisor)),
            any_float(data),
        ),
    }
}

/// `statistics.variance(data)` (sample variance, `n - 1`).
pub fn variance(data: &[Num]) -> PyResult<Num> {
    if data.len() < 2 {
        return Err(statistics_error(
            "variance requires at least two data points",
        ));
    }
    variance_with(data, true)
}

/// `statistics.pvariance(data)` (population variance, `n`).
pub fn pvariance(data: &[Num]) -> PyResult<Num> {
    if data.is_empty() {
        return Err(statistics_error(
            "pvariance requires at least one data point",
        ));
    }
    variance_with(data, false)
}

/// `_float_sqrt_of_frac(n, m)`: correctly rounded square root of `n / m` (`n >= 0`).
fn float_sqrt_of_frac(n: &BigUint, m: &BigUint) -> PyResult<f64> {
    const SQRT_BIT_WIDTH: i64 = 2 * 53 + 3;
    let q = (n.bit_length() as i64 - m.bit_length() as i64 - SQRT_BIT_WIDTH).div_euclid(2);
    let rto = |n: &BigUint, m: &BigUint| {
        let (quotient, _) = n.divrem(m);
        let a = quotient.isqrt();
        let odd = a.mul(&a).mul(m) != *n;
        if odd && !a.is_odd() {
            a.add(&BigUint::one())
        } else {
            a
        }
    };
    let (numerator, denominator) = if q >= 0 {
        (rto(n, &m.shl(2 * q as u64)).shl(q as u64), BigUint::one())
    } else {
        (
            rto(&n.shl(2 * q.unsigned_abs()), m),
            BigUint::one().shl(q.unsigned_abs()),
        )
    };
    bigint_true_div(
        &BigInt::new(false, numerator),
        &BigInt::new(false, denominator),
    )
}

fn stdev_with(data: &[Num], sample: bool) -> PyResult<f64> {
    let (ss, n) = sum_of_squares(data);
    let divisor = if sample { n - 1 } else { n };
    match ss {
        SumOfSquares::NonFinite(_) => Err(PyError::new(
            PyErrorKind::AttributeError,
            "'float' object has no attribute 'numerator'",
        )),
        SumOfSquares::Exact {
            numerator,
            denominator,
        } => {
            // mss = ss / divisor as a reduced Fraction; ss is never negative.
            let denominator = denominator.mul(&count_int(divisor));
            let g = BigUint::gcd(&numerator.magnitude, &denominator.magnitude);
            let (n, _) = numerator.magnitude.divrem(&g);
            let (m, _) = denominator.magnitude.divrem(&g);
            float_sqrt_of_frac(&n, &m)
        }
    }
}

/// `statistics.stdev(data)`.
pub fn stdev(data: &[Num]) -> PyResult<f64> {
    if data.len() < 2 {
        return Err(statistics_error("stdev requires at least two data points"));
    }
    stdev_with(data, true)
}

/// `statistics.pstdev(data)`.
pub fn pstdev(data: &[Num]) -> PyResult<f64> {
    if data.is_empty() {
        return Err(statistics_error("pstdev requires at least one data point"));
    }
    stdev_with(data, false)
}

/// `statistics.median(data)`: CPython's `sorted` (NaN placement included), then the middle
/// item or `(a + b) / 2` of the two middle items with Python's mixed-type addition.
pub fn median(data: &[Num]) -> PyResult<Num> {
    if data.is_empty() {
        return Err(statistics_error("no median for empty data"));
    }
    let sorted = order::sorted_nums(data, false);
    let n = sorted.len();
    if n % 2 == 1 {
        return Ok(sorted[n / 2]);
    }
    let sum = num::add(sorted[n / 2 - 1], sorted[n / 2])?;
    num::truediv(sum, Num::Int(2)).map(Num::Float)
}

/// `statistics.median` of floats.
pub fn median_floats(data: &[f64]) -> PyResult<f64> {
    let nums: Vec<Num> = data.iter().map(|&x| Num::Float(x)).collect();
    median(&nums).map(Num::to_f64)
}

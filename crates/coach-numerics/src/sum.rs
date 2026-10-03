//! Builtin `sum()` (CPython 3.12 `builtin_sum_impl`) and `math.fsum` (Shewchuk).

use std::os::raw::c_long;

use crate::error::{PyError, PyErrorKind, PyResult};
use crate::num::{self, Num};

/// Whether CPython's `PyLong_AsLongAndOverflow` accepts the value: C `long` is 32 bits on
/// Windows and 64 bits on Linux, and `sum()` changes path on that boundary.
fn fits_c_long(value: i64) -> bool {
    c_long::try_from(value).is_ok()
}

/// Neumaier step of the float loop.
#[inline]
fn neumaier(total: &mut f64, compensation: &mut f64, x: f64) {
    let t = *total + x;
    if total.abs() >= x.abs() {
        *compensation += (*total - t) + x;
    } else {
        *compensation += (x - t) + *total;
    }
    *total = t;
}

/// The float loop's exit: the compensation joins only when nonzero and finite.
#[inline]
fn finish(total: f64, compensation: f64) -> f64 {
    if compensation != 0.0 && compensation.is_finite() {
        total + compensation
    } else {
        total
    }
}

/// `sum(items, start)` exactly as CPython 3.12 evaluates it:
///
/// - while the running value is an int, ints add exactly; the first float turns the running
///   value into `float(int) + x`;
/// - while the running value is a float, floats add with Neumaier compensation and ints that
///   fit a C `long` add as `(double)value` without compensation;
/// - an int outside C `long` (beyond 2**31 on Windows, 2**63 on Linux) leaves the fast loops
///   for good: the remaining items add with plain `+`, uncompensated.
pub fn builtin_sum(items: &[Num], start: Num) -> PyResult<Num> {
    let mut index = 0;
    let mut result = start;
    if let Num::Int(mut i_result) = start
        && fits_c_long(i_result)
    {
        loop {
            let Some(&item) = items.get(index) else {
                return Ok(Num::Int(i_result));
            };
            index += 1;
            if let Num::Int(b) = item
                && fits_c_long(b)
                && let Some(sum) = i_result.checked_add(b)
                && fits_c_long(sum)
            {
                i_result = sum;
                continue;
            }
            result = num::add(Num::Int(i_result), item)?;
            break;
        }
    }
    if let Num::Float(mut f_result) = result {
        let mut c = 0.0f64;
        loop {
            let Some(&item) = items.get(index) else {
                return Ok(Num::Float(finish(f_result, c)));
            };
            index += 1;
            match item {
                Num::Float(x) => neumaier(&mut f_result, &mut c, x),
                Num::Int(value) if fits_c_long(value) => f_result += value as f64,
                Num::Int(_) => {
                    result = num::add(Num::Float(finish(f_result, c)), item)?;
                    break;
                }
            }
        }
    }
    for &item in &items[index..] {
        result = num::add(result, item)?;
    }
    Ok(result)
}

/// `sum(xs, start)` for a float start and float items: the Neumaier loop alone.
pub fn sum_floats(items: &[f64], start: f64) -> f64 {
    let mut total = start;
    let mut c = 0.0;
    for &x in items {
        neumaier(&mut total, &mut c, x);
    }
    finish(total, c)
}

/// `sum(xs)` (int start 0) for float items: `Int(0)` when empty, else `0 + xs[0]` (so a
/// leading `-0.0` becomes `0.0`) followed by the Neumaier loop.
pub fn sum_floats_from_int_zero(items: &[f64]) -> Num {
    match items.split_first() {
        None => Num::Int(0),
        Some((&first, rest)) => Num::Float(sum_floats(rest, 0.0 + first)),
    }
}

/// `functools.reduce(operator.add, xs, start)`: a plain left fold, no compensation.
pub fn fold_add(items: &[f64], start: f64) -> f64 {
    items.iter().fold(start, |acc, &x| acc + x)
}

/// `math.fsum(xs)`: exactly rounded sum of the floats (`msum` with the half-even fix-up).
/// Errors as Python: `OverflowError` on intermediate overflow of finite inputs, `ValueError`
/// for `inf + -inf`. The sum of nothing, or of zeros, is `0.0` (never `-0.0`).
pub fn fsum(items: &[f64]) -> PyResult<f64> {
    let mut partials: Vec<f64> = Vec::with_capacity(32);
    let mut special_sum = 0.0f64;
    let mut inf_sum = 0.0f64;
    let mut lo = 0.0f64;
    for &item in items {
        let mut x = item;
        let xsave = x;
        let mut i = 0;
        for j in 0..partials.len() {
            let mut y = partials[j];
            if x.abs() < y.abs() {
                std::mem::swap(&mut x, &mut y);
            }
            let hi = x + y;
            let yr = hi - x;
            lo = y - yr;
            if lo != 0.0 {
                partials[i] = lo;
                i += 1;
            }
            x = hi;
        }
        partials.truncate(i);
        if x != 0.0 {
            if !x.is_finite() {
                if xsave.is_finite() {
                    return Err(PyError::new(
                        PyErrorKind::OverflowError,
                        "intermediate overflow in fsum",
                    ));
                }
                if xsave.is_infinite() {
                    inf_sum += xsave;
                }
                special_sum += xsave;
                partials.clear();
            } else {
                partials.push(x);
            }
        }
    }
    if special_sum != 0.0 {
        if inf_sum.is_nan() {
            return Err(PyError::new(PyErrorKind::ValueError, "-inf + inf in fsum"));
        }
        return Ok(special_sum);
    }
    let mut hi = 0.0f64;
    let mut n = partials.len();
    if n > 0 {
        n -= 1;
        hi = partials[n];
        while n > 0 {
            let x = hi;
            n -= 1;
            let y = partials[n];
            hi = x + y;
            let yr = hi - x;
            lo = y - yr;
            if lo != 0.0 {
                break;
            }
        }
        if n > 0 && ((lo < 0.0 && partials[n - 1] < 0.0) || (lo > 0.0 && partials[n - 1] > 0.0)) {
            let y = lo * 2.0;
            let x = hi + y;
            let yr = x - hi;
            if y == yr {
                hi = x;
            }
        }
    }
    Ok(hi)
}

/// `math.fsum` over Python numbers: each int converts with `float(int)` first.
pub fn fsum_nums(items: &[Num]) -> PyResult<f64> {
    let floats: Vec<f64> = items.iter().map(|item| item.to_f64()).collect();
    fsum(&floats)
}

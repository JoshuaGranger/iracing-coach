//! The `math` module functions the analysis calls (CPython 3.12 `Modules/mathmodule.c`) and
//! the float `**` operator (`float_pow`).
//!
//! `hypot`, `sqrt`, `degrees`, `radians`, `floor`, `ceil`, `isclose` and `gcd` are exact
//! ports and platform independent. `atan2`, `sin`, `cos` and `pow` call the C library
//! exactly as CPython does, after CPython's own special-case handling: they match the oracle
//! only when linked against the same libm (UCRT on Windows, glibc on Linux).

use crate::error::{INT_BEYOND_I64, PyError, PyErrorKind, PyResult};
use crate::num::{Num, int_from_float};

/// The C library calls CPython makes. Arguments pass through `black_box` so the optimizer
/// cannot fold a call with constant arguments into its own (differently rounded) value.
mod libm {
    use std::hint::black_box;
    use std::os::raw::c_int;

    mod c {
        use std::os::raw::c_int;
        unsafe extern "C" {
            pub safe fn atan2(y: f64, x: f64) -> f64;
            pub safe fn sin(x: f64) -> f64;
            pub safe fn cos(x: f64) -> f64;
            pub safe fn pow(x: f64, y: f64) -> f64;
            #[cfg(windows)]
            pub fn _errno() -> *mut c_int;
            #[cfg(not(windows))]
            pub fn __errno_location() -> *mut c_int;
        }
    }

    pub const ERANGE: c_int = 34;

    fn errno_location() -> *mut c_int {
        // SAFETY: both functions return the calling thread's errno slot and take no input.
        #[cfg(windows)]
        unsafe {
            c::_errno()
        }
        #[cfg(not(windows))]
        unsafe {
            c::__errno_location()
        }
    }

    pub fn atan2(y: f64, x: f64) -> f64 {
        c::atan2(black_box(y), black_box(x))
    }

    pub fn sin(x: f64) -> f64 {
        c::sin(black_box(x))
    }

    pub fn cos(x: f64) -> f64 {
        c::cos(black_box(x))
    }

    /// `pow` with the errno it leaves, as `float_pow` reads it.
    pub fn pow(x: f64, y: f64) -> (f64, c_int) {
        let slot = errno_location();
        // SAFETY: `slot` is this thread's errno, valid for the life of the thread.
        unsafe { slot.write_volatile(0) };
        let result = c::pow(black_box(x), black_box(y));
        // SAFETY: as above.
        let errno = unsafe { slot.read_volatile() };
        (black_box(result), errno)
    }
}

fn domain_error() -> PyError {
    PyError::new(PyErrorKind::ValueError, "math domain error")
}

/// `math.pi`.
pub const PI: f64 = std::f64::consts::PI;

/// `math.floor(x)` for a float.
pub fn floor(x: f64) -> PyResult<i64> {
    int_from_float(x.floor())
}

/// `math.ceil(x)` for a float.
pub fn ceil(x: f64) -> PyResult<i64> {
    int_from_float(x.ceil())
}

/// `math.floor` / `math.ceil` keeping Python's types (an int returns itself).
pub fn floor_num(value: Num) -> PyResult<i64> {
    match value {
        Num::Int(i) => Ok(i),
        Num::Float(x) => floor(x),
    }
}

/// `math.ceil` for an int or a float.
pub fn ceil_num(value: Num) -> PyResult<i64> {
    match value {
        Num::Int(i) => Ok(i),
        Num::Float(x) => ceil(x),
    }
}

/// `math.sqrt(x)`: IEEE square root; `ValueError` below zero (`-0.0` returns `-0.0`).
pub fn sqrt(x: f64) -> PyResult<f64> {
    let r = x.sqrt();
    if r.is_nan() && !x.is_nan() {
        return Err(domain_error());
    }
    Ok(r)
}

/// `math.degrees(x)`: `x * (180.0 / pi)`.
pub fn degrees(x: f64) -> f64 {
    x * (180.0 / PI)
}

/// `math.radians(x)`: `x * (pi / 180.0)`.
pub fn radians(x: f64) -> f64 {
    x * (PI / 180.0)
}

/// Dekker/Shewchuk exact product: `hi + lo == x * y`.
fn dl_mul(x: f64, y: f64) -> (f64, f64) {
    let hi = x * y;
    (hi, x.mul_add(y, -hi))
}

/// Exact sum for `|a| >= |b|`.
fn dl_fast_sum(a: f64, b: f64) -> (f64, f64) {
    let hi = a + b;
    (hi, (a - hi) + b)
}

/// `frexp(x).1` for finite positive `x`.
fn frexp_exponent(x: f64) -> i32 {
    let bits = x.to_bits();
    let exponent = ((bits >> 52) & 0x7ff) as i32;
    if exponent == 0 {
        let fraction = bits & ((1 << 52) - 1);
        -1010 - fraction.leading_zeros() as i32
    } else {
        exponent - 1022
    }
}

/// CPython `vector_norm`: correctly rounded in nearly all cases, by exact squares and a
/// differential correction of the square root.
fn vector_norm(vec: &mut [f64], max: f64, found_nan: bool) -> f64 {
    if max.is_infinite() {
        return max;
    }
    if found_nan {
        return f64::NAN;
    }
    if max == 0.0 || vec.len() <= 1 {
        return max;
    }
    let max_e = frexp_exponent(max);
    if max_e < -1023 {
        let dbl_min = f64::MIN_POSITIVE;
        for value in vec.iter_mut() {
            *value /= dbl_min;
        }
        return dbl_min * vector_norm(vec, max / dbl_min, found_nan);
    }
    let scale = crate::exact::pow2(-max_e);
    let mut csum = 1.0f64;
    let mut frac1 = 0.0f64;
    let mut frac2 = 0.0f64;
    for &value in vec.iter() {
        let x = value * scale;
        let (hi, lo) = dl_mul(x, x);
        let (sum_hi, sum_lo) = dl_fast_sum(csum, hi);
        csum = sum_hi;
        frac1 += lo;
        frac2 += sum_lo;
    }
    let mut h = (csum - 1.0 + (frac1 + frac2)).sqrt();
    let (hi, lo) = dl_mul(-h, h);
    let (sum_hi, sum_lo) = dl_fast_sum(csum, hi);
    csum = sum_hi;
    frac1 += lo;
    frac2 += sum_lo;
    let x = csum - 1.0 + (frac1 + frac2);
    h += x / (2.0 * h);
    h / scale
}

/// `math.hypot(*coordinates)`.
pub fn hypot_n(coordinates: &[f64]) -> f64 {
    let mut vec: Vec<f64> = Vec::with_capacity(coordinates.len());
    let mut max = 0.0f64;
    let mut found_nan = false;
    for &c in coordinates {
        let x = c.abs();
        found_nan |= x.is_nan();
        if x > max {
            max = x;
        }
        vec.push(x);
    }
    vector_norm(&mut vec, max, found_nan)
}

/// `math.hypot(x, y)`.
pub fn hypot(x: f64, y: f64) -> f64 {
    hypot_n(&[x, y])
}

/// `math.atan2(y, x)`: CPython `m_atan2` special cases, then the C library.
pub fn atan2(y: f64, x: f64) -> f64 {
    if x.is_nan() || y.is_nan() {
        return f64::NAN;
    }
    if y.is_infinite() {
        if x.is_infinite() {
            return if x.is_sign_positive() {
                (0.25 * PI).copysign(y)
            } else {
                (0.75 * PI).copysign(y)
            };
        }
        return (0.5 * PI).copysign(y);
    }
    if x.is_infinite() || y == 0.0 {
        return if x.is_sign_positive() {
            0.0f64.copysign(y)
        } else {
            PI.copysign(y)
        };
    }
    libm::atan2(y, x)
}

/// `math.sin(x)`: `ValueError` for infinities.
pub fn sin(x: f64) -> PyResult<f64> {
    let r = libm::sin(x);
    if r.is_nan() && !x.is_nan() {
        return Err(domain_error());
    }
    Ok(r)
}

/// `math.cos(x)`: `ValueError` for infinities.
pub fn cos(x: f64) -> PyResult<f64> {
    let r = libm::cos(x);
    if r.is_nan() && !x.is_nan() {
        return Err(domain_error());
    }
    Ok(r)
}

/// `math.isclose(a, b, rel_tol=rel_tol, abs_tol=abs_tol)`.
pub fn isclose(a: f64, b: f64, rel_tol: f64, abs_tol: f64) -> PyResult<bool> {
    if rel_tol < 0.0 || abs_tol < 0.0 {
        return Err(PyError::new(
            PyErrorKind::ValueError,
            "tolerances must be non-negative",
        ));
    }
    if a == b {
        return Ok(true);
    }
    if a.is_infinite() || b.is_infinite() {
        return Ok(false);
    }
    let diff = (b - a).abs();
    Ok(diff <= (rel_tol * b).abs() || diff <= (rel_tol * a).abs() || diff <= abs_tol)
}

/// `math.gcd(a, b)`.
pub fn gcd(a: i64, b: i64) -> PyResult<i64> {
    let (mut x, mut y) = (a.unsigned_abs(), b.unsigned_abs());
    while y != 0 {
        (x, y) = (y, x % y);
    }
    i64::try_from(x).map_err(|_| INT_BEYOND_I64)
}

fn is_odd_integer(x: f64) -> bool {
    x.abs() % 2.0 == 1.0
}

/// `x ** y` for floats (`float_pow`): CPython's special cases, then the C library `pow`.
/// `ZeroDivisionError` for `0.0 ** negative`, `OverflowError` when the result overflows,
/// `ComplexResult` for a negative base with a fractional exponent.
pub fn float_pow(x: f64, y: f64) -> PyResult<f64> {
    let (mut iv, iw) = (x, y);
    if iw == 0.0 {
        return Ok(1.0);
    }
    if iv.is_nan() {
        return Ok(iv);
    }
    if iw.is_nan() {
        return Ok(if iv == 1.0 { 1.0 } else { iw });
    }
    if iw.is_infinite() {
        iv = iv.abs();
        return Ok(if iv == 1.0 {
            1.0
        } else if (iw > 0.0) == (iv > 1.0) {
            iw.abs()
        } else {
            0.0
        });
    }
    if iv.is_infinite() {
        let odd = is_odd_integer(iw);
        return Ok(if iw > 0.0 {
            if odd { iv } else { iv.abs() }
        } else if odd {
            0.0f64.copysign(iv)
        } else {
            0.0
        });
    }
    if iv == 0.0 {
        let odd = is_odd_integer(iw);
        if iw < 0.0 {
            return Err(PyError::new(
                PyErrorKind::ZeroDivisionError,
                "0.0 cannot be raised to a negative power",
            ));
        }
        return Ok(if odd { iv } else { 0.0 });
    }
    let mut negate = false;
    if iv < 0.0 {
        if iw != iw.floor() {
            return Err(PyError::new(
                PyErrorKind::ComplexResult,
                "negative base, fractional power",
            ));
        }
        iv = -iv;
        negate = is_odd_integer(iw);
    }
    if iv == 1.0 {
        return Ok(if negate { -1.0 } else { 1.0 });
    }
    let (mut ix, mut errno) = libm::pow(iv, iw);
    // _Py_ADJUST_ERANGE1
    if errno == 0 {
        if ix.is_infinite() {
            errno = libm::ERANGE;
        }
    } else if errno == libm::ERANGE && ix == 0.0 {
        errno = 0;
    }
    if negate {
        ix = -ix;
    }
    if errno != 0 {
        return Err(if errno == libm::ERANGE {
            PyError::new(PyErrorKind::OverflowError, "Numerical result out of range")
        } else {
            PyError::new(PyErrorKind::ValueError, "math domain error")
        });
    }
    Ok(ix)
}

/// `a ** b` for Python numbers: int to a non-negative int power stays an int, everything
/// else is `float(a) ** float(b)`.
pub fn pow_num(a: Num, b: Num) -> PyResult<Num> {
    match (a, b) {
        (Num::Int(base), Num::Int(exponent)) if exponent >= 0 => match base {
            0 => Ok(Num::Int(i64::from(exponent == 0))),
            1 => Ok(Num::Int(1)),
            -1 => Ok(Num::Int(if exponent % 2 == 0 { 1 } else { -1 })),
            _ => u32::try_from(exponent)
                .ok()
                .and_then(|e| base.checked_pow(e))
                .map(Num::Int)
                .ok_or(INT_BEYOND_I64),
        },
        _ => float_pow(a.to_f64(), b.to_f64()).map(Num::Float),
    }
}

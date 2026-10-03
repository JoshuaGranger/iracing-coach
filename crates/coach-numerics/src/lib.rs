//! Python 3.12 numeric semantics, bit for bit: the foundation the Rust port of the analysis
//! kernels builds on (docs/08-rewrite/port/numerics.md).
//!
//! Every function names the Python expression it reproduces. Results equal CPython 3.12's
//! bit for bit (and type for type: [`Num`] keeps `int` and `float` apart), which the
//! vectors under `vectors/` prove against the bundled interpreter. NaN payloads and the
//! sign of a NaN are not part of the contract: Python prints every NaN as `nan`/`NaN`.

mod bigint;
mod decimal;
mod error;
mod exact;
mod format;
mod num;
mod order;
mod parse;
mod pymath;
mod repr;
mod round;
mod stats;
mod sum;

pub use error::{PyError, PyErrorKind, PyResult};
pub use exact::int_true_div;
pub use format::{format_float, format_int, format_num};
pub use num::{
    Num, abs, add, cmp_int_float, divmod, float_divmod, float_mod, floordiv, int_from_float,
    modulo, mul, neg, py_cmp, py_eq, py_ge, py_gt, py_le, py_lt, sub, truediv,
};
pub use order::{
    bisect_left, bisect_left_by, bisect_right, bisect_right_by, heap_pop, heap_push, heap_replace,
    max_index_by, max_num, min_index_by, min_num, sort_by, sort_permutation, sorted_floats,
    sorted_nums,
};
pub use parse::{parse_float, parse_int};
pub use pymath::{
    PI, atan2, ceil, ceil_num, cos, degrees, float_pow, floor, floor_num, gcd, hypot, hypot_n,
    isclose, pow_num, radians, sin, sqrt,
};
pub use repr::{float_repr, json_float, json_num, num_repr};
pub use round::{round_half_even, round_int_ndigits, round_ndigits, round_num, round_to_int};
pub use stats::{
    fmean, fmean_nums, mean, median, median_floats, pstdev, pvariance, stdev, variance,
};
pub use sum::{builtin_sum, fold_add, fsum, fsum_nums, sum_floats, sum_floats_from_int_zero};

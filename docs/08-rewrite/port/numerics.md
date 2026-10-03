# Port foundation: Python-exact numerics (`crates/coach-numerics`)

Date 2026-10-03. Authority: owner decision D2 (staged Rust port, byte-identical output against the Python oracle) and `architecture.md` section 12 (determinism rules every Rust stage must meet). Phase 2 (the per-sample kernels) builds on this crate; phase 3 (the JSON writer) uses its float text.

## 1. Result

- `crates/coach-numerics` reproduces every numeric primitive the Python analysis uses with CPython 3.12 semantics, bit for bit and type for type: builtin `sum()`, `math.fsum`, `statistics` (`fmean`, `mean`, `median`, `variance`, `pvariance`, `stdev`, `pstdev`), `round()`, `repr(float)`, the `json.dumps` float token, `format()`/f-string specs, the `math` functions in use, the float `**` operator, int/float arithmetic and comparison, `divmod`/`//`/`%`, `int()`/`float()` of numbers and strings, `sorted`/`list.sort` (CPython's timsort, so NaN placement matches), `min`/`max`, `bisect` and `heapq`.
- No dependencies. Exact paths use the crate's own big integers (`src/bigint.rs`).
- 30,533 vector cases (37,856 checks) from CPython match bit for bit on Windows (CPython 3.12.14, the bundled oracle) and on Linux (WSL Ubuntu, CPython 3.12.3). The nine platform-independent vector files come out byte-identical from both interpreters. Local stress runs (not committed) added 1.2 million `repr`/JSON checks, 301,375 `round` cases, 300,638 `math` cases (mostly `hypot`), 370,276 `format` cases, 20,454 ordering cases and the other families at 6,000 random cases each: all identical.

Acceptance, run from `crates/coach-numerics` with `CARGO_TARGET_DIR=<repo>/target/port-numerics`:

| Command | Result |
|---|---|
| `python tools/gen_vectors.py --check` (bundled 3.12.14), run twice | 11 of 11 files identical both times |
| `python3.12 tools/gen_vectors.py --check` (WSL, 3.12.3) | 11 of 11 identical |
| `cargo test -p coach-numerics` (Windows) | 7 unit tests, 1 memory test (`tests/stats_memory.rs`), 11 vector families: pass |
| `cargo test` in WSL (Linux target) | same, pass |
| `cargo clippy -p coach-numerics --all-targets -- -D warnings` (Windows and WSL) | clean |
| `cargo fmt -p coach-numerics --check` | clean |

### Workspace

The crate is a member of the repository workspace (root `Cargo.toml` `members`) and has no dependencies; `cargo test -p coach-numerics` runs from the repository root. Its package fields are written out rather than inherited with `.workspace = true`; the values are the workspace's.

### Unsafe code

`src/pymath.rs` declares the C library's `sin`, `cos`, `atan2`, `pow` and the errno accessor (`_errno` on Windows, `__errno_location` on Linux). The math functions are declared `safe`; the errno slot is read and written in two `unsafe` blocks. Nothing else is unsafe.

## 2. API

All functions are free functions re-exported at the crate root. `PyResult<T> = Result<T, PyError>`; `PyError.kind` is the Python exception class (`ZeroDivisionError`, `OverflowError`, `ValueError`, `StatisticsError`, `AttributeError`, `TypeError`) or one of three port limits (`IntBeyondI64`, `ComplexResult`, `Unsupported`, section 5).

`Num` is a Python number: `Num::Int(i64)` or `Num::Float(f64)`. Arithmetic keeps Python's result type (`int + int` is an int, `/` is always a float, an int mean of ints is an int).

| Python | Rust |
|---|---|
| `sum(items, start)` | `builtin_sum(&[Num], Num) -> PyResult<Num>` |
| `sum(floats, 0.0)` | `sum_floats(&[f64], f64) -> f64` (the Neumaier loop alone) |
| `sum(floats)` | `sum_floats_from_int_zero(&[f64]) -> Num` (`Int(0)` when empty) |
| `reduce(add, floats, start)` | `fold_add(&[f64], f64) -> f64` (plain left fold) |
| `math.fsum(xs)` | `fsum(&[f64]) -> PyResult<f64>`, `fsum_nums(&[Num])` |
| `statistics.fmean(xs)` | `fmean(&[f64]) -> PyResult<f64>`, `fmean_nums(&[Num])` |
| `statistics.mean` / `median` / `variance` / `pvariance` | `mean`, `median`, `variance`, `pvariance`: `(&[Num]) -> PyResult<Num>`; `median_floats(&[f64])` |
| `statistics.stdev` / `pstdev` | `stdev`, `pstdev`: `(&[Num]) -> PyResult<f64>` |
| `round(x, n)` for a float | `round_ndigits(f64, i64) -> PyResult<f64>` |
| `round(x)` for a float | `round_to_int(f64) -> PyResult<i64>`; `round_half_even(f64) -> f64` (the integral float) |
| `round(i, n)` for an int; `round(v, n)` any | `round_int_ndigits(i64, i64)`, `round_num(Num, Option<i64>) -> PyResult<Num>` |
| `repr(x)`, `str(x)`, `f"{x}"` | `float_repr(f64) -> String`, `num_repr(Num)` |
| `json.dumps` token of a number | `json_float(f64, allow_nan) -> PyResult<String>`, `json_num(Num, allow_nan)` |
| `format(x, spec)`, `f"{x:spec}"` | `format_float(f64, &str)`, `format_int(i64, &str)`, `format_num(Num, &str)`: `PyResult<String>` |
| `a + b`, `-`, `*`, `/`, `//`, `%`, `divmod`, `-a`, `abs` | `add`, `sub`, `mul`, `truediv` (`-> PyResult<f64>`), `floordiv`, `modulo`, `divmod`, `neg`, `abs`; float-only `float_divmod`, `float_mod`; `int_true_div(i64, i64)` |
| `a < b` etc. | `py_lt`, `py_le`, `py_gt`, `py_ge`, `py_eq`, `py_cmp -> Option<Ordering>`, `cmp_int_float` |
| `int(x)` for a float; `float(i)` | `int_from_float(f64) -> PyResult<i64>`; `Num::to_f64` |
| `float(text)`, `int(text)` | `parse_float(&str) -> PyResult<f64>`, `parse_int(&str) -> PyResult<i64>` |
| `math.floor` / `ceil` | `floor`, `ceil` `(f64) -> PyResult<i64>`; `floor_num`, `ceil_num` |
| `math.sqrt`, `hypot`, `atan2`, `sin`, `cos` | `sqrt -> PyResult<f64>`, `hypot(x, y)`, `hypot_n(&[f64])`, `atan2(y, x)`, `sin`, `cos -> PyResult<f64>` |
| `math.degrees`, `radians`, `isclose`, `gcd`, `pi` | `degrees`, `radians`, `isclose(a, b, rel_tol, abs_tol) -> PyResult<bool>`, `gcd(i64, i64)`, `PI` |
| `x ** y` | `float_pow(f64, f64) -> PyResult<f64>`, `pow_num(Num, Num) -> PyResult<Num>` |
| `sorted(xs, key=..., reverse=...)`, `list.sort` | `sort_permutation(n, &mut lt(i, j), reverse) -> Vec<usize>`, `sort_by(&mut [T], lt, reverse)`, `sorted_nums(&[Num], reverse)`, `sorted_floats(&[f64], reverse)` |
| `min(...)`, `max(...)` (with or without `key`) | `min_index_by(len, lt)`, `max_index_by(len, gt)`, `min_num`, `max_num` |
| `bisect.bisect_left` / `bisect_right` | `bisect_left_by(len, item_lt_x)`, `bisect_right_by(len, x_lt_item)`, `bisect_left(&[Num], Num)`, `bisect_right` |
| `heapq.heappush` / `heappop` / `heapreplace` | `heap_push(&mut Vec<T>, T, lt)`, `heap_pop`, `heap_replace` |

Plain `f64` `+ - * /`, comparisons, `f64::is_finite`/`is_nan`, `f64::abs` and `mul_add`-free expressions are already what CPython computes; the crate is needed where Python does more than one IEEE operation.

## 3. Semantics reproduced, and what the vectors found

Sources are CPython 3.12 (`Python/bltinmodule.c`, `Modules/mathmodule.c`, `Lib/statistics.py`, `Objects/floatobject.c`, `Objects/longobject.c`, `Python/pystrtod.c`, `Python/formatter_unicode.c`, `Objects/listobject.c`, `Modules/_bisectmodule.c`, `Modules/_heapqmodule.c`). `Lib/statistics.py` was read from the bundled runtime.

- **`sum()`** runs three loops. While the running value is an int, ints add exactly; the first float makes it `float(int) + x` (so `sum([-0.0])` is `0.0`). While it is a float, floats add with Neumaier compensation, ints that fit a C `long` add as `(double)value` with no compensation, and the compensation joins at the end only when nonzero and finite (`sum([-0.0, -0.0], -0.0)` stays `-0.0`, `sum([inf, 1.0], 0.0)` is `inf`). An int outside C `long` sends every later item through plain `+`, uncompensated, to the end. C `long` is 32 bits on Windows and 64 on Linux, so the same list can sum differently: `sum([0.1, 2**40, 0.1, 0.1, 1e16, -1e16], 0.0)` is `1099511627776.0` on Windows and `1099511627776.3` on Linux. `builtin_sum` uses the target's `c_long`.
- **`math.fsum`** is Shewchuk's `msum` with the half-even fix-up across partials; the sum of nothing or of zeros is `0.0`, never `-0.0`; `OverflowError` on intermediate overflow of finite inputs, `ValueError` for `inf + -inf`.
- **`statistics`**: `fmean` is `fsum(data) / len(data)`. `mean`, `variance` and `pvariance` are exact rationals: every float's `as_integer_ratio` has a power-of-two denominator, so sums are integers over `2**k`, and the result converts with one correctly rounded division (an int when the data are ints and the value is whole: `variance([1, 2, 3])` is `1`). The sums are accumulated in one pass as two running big integers, rescaled when a larger denominator `2**k` appears (at most 1,074 times), so memory does not grow with the data (Codex Sol review finding 11: `mean` of 1,000,000 copies of `0.1` grew the heap 48 MB before; `tests/stats_memory.rs` bounds the growth under 64 KiB). `stdev`/`pstdev` reduce the fraction and run `_float_sqrt_of_frac` (`isqrt` with round-to-odd, then one correctly rounded division). Non-finite data take the `partials[None]` path: `mean`/`variance` return the float sum of the non-finite items divided by the count; `stdev`/`pstdev` raise `AttributeError` (CPython 3.12 asks a float for `.numerator`). `median` sorts with CPython's timsort, so NaN placement matches.
- **`round(x, n)`** is dtoa mode 3 (correct rounding of the exact binary value, ties to even) read back with `strtod`; `n > 323` returns `x`, `n < -308` returns `0.0 * x`, NaN and infinities return themselves, and an overflow raises `OverflowError`. The exact path expands the float in decimal with big integers; a fast path (`0 <= n <= 22`, product within 128 bits, rounded integer below `2**53`) does one exactly rounded division and is checked against the exact path on 200,000 random cases. `round(x)` is C `round` corrected to half even (`round(2.5) == 2`).
- **`repr(float)`**: shortest round-trip digits, exponent form when the decimal point position is `<= -4` or `> 16`, exponent with sign and at least two digits (`1e-05`, `1e+16`). Rust's shortest formatter rounds an exact tie in the last kept digit up; dtoa rounds it to even. The first vector run found 45 of 9,660 checks differing, all such ties, mostly float32 values widened to double (`-125.661651611328125` prints `-125.66165161132812` in Python, `...813` from Rust's formatter). `decimal::shortest` detects the tie exactly and takes the even neighbour. `json.dumps` writes `float.__repr__`, or `NaN`/`Infinity`/`-Infinity` (`ValueError` with `allow_nan=False`).
- **`format()`** ports `parse_internal_render_format_spec`, `format_float_internal`/`format_long_internal` and `calc_number_widths`/`fill_number`, including zero padding through thousands groups (`format(1234, '08,d')` is `0,001,234`), `z`, `#`, `%` (multiplies by 100 in floating point first), the no-type-with-precision rule (`format(100.0, '.3')` is `1e+02`) and signs on `inf`/`nan`.
- **`math.hypot`** is CPython's own `vector_norm` (exact squares, differential correction), not the C library's `hypot`. `sqrt`, `degrees` (`x * (180.0 / pi)`), `radians`, `floor`, `ceil`, `isclose` and `gcd` are exact ports. `atan2` applies CPython's `m_atan2` special cases, then the C library; `sin`, `cos` and `pow` call the C library. `float_pow` follows CPython's special cases and reads errno after `pow` as `_Py_ADJUST_ERANGE1` does. Arguments to the C library pass through `black_box`, so the compiler cannot fold a constant call with its own rounding.
- **Arithmetic and comparison**: int/float comparison is exact (`2**53 + 1 > 9007199254740992.0`); `int / int` is correctly rounded; float `//`, `%` and `divmod` follow `_float_div_mod` (remainder takes the divisor's sign, the quotient snaps to an integer, signed zeros as CPython).
- **Ordering**: `sort_permutation` is CPython 3.12's `list.sort` step for step: `count_run`, `binarysort`, `gallop_left`/`gallop_right`, `merge_lo`/`merge_hi` with the adaptive `min_gallop`, and the powersort merge policy. For a consistent order any stable sort agrees; with a NaN present only the same comparison sequence gives the same result, and the vectors include NaN-laden lists, runs, descending runs and reverse sorts. `min` keeps the first of equal items, `max` replaces only on `>`.
- **`float(str)` / `int(str)`**: an all-ASCII string is stripped of ASCII whitespace only, so `float("\x1c2\x1f")` is a `ValueError`, while in a string holding any non-ASCII character every `str.isspace` character counts (CPython's `_PyUnicode_TransformDecimalAndSpaceToASCII` returns ASCII strings unchanged). Underscores only between digits.

## 4. Platform dependence

| Family | Windows (UCRT, C `long` 32 bits) vs Linux (glibc, 64 bits) |
|---|---|
| `sum` | 55 of 429 cases differ: every list or start with an int beyond `2**31` in a position where the Windows loop gives up compensation. |
| `sin`, `cos`, `atan2`, `pow` | 28 of 3,431 cases differ in the last bit (`cos` 13, `sin` 12, `atan2` 2, `pow` 1), for example `sin(2.5)` and `(1.7976931348623157e308) ** 0.5` (UCRT's `pow(x, 0.5)` is not `sqrt`). |
| everything else | byte-identical vector files from both interpreters |

`vectors/windows/` and `vectors/linux/` hold the two platform families; the test picks the target's directory. Consequences for the container: the oracle is Linux CPython 3.12 with glibc, and a Rust stage that calls `sin`, `cos`, `atan2` or `pow` matches it only when linked against the same glibc libm (a dynamically linked build in the same image, not musl). `hypot` does not depend on the C library. The common files also show that CPython 3.12.3 and 3.12.14 agree on everything here; the container's exact 3.12 patch level should still be pinned. The oracle's C code is assumed built without FMA contraction (generic x86-64 distribution builds are); a `-march=native` CPython build could fuse `a * b + c` in `sum`, `fsum` or `vector_norm` and would need its own vectors.

## 5. Limits (each is an explicit error, never a silently different value)

- Ints are `i64`. A result Python would hold as a larger int is `IntBeyondI64` (`2**63`, `-(-2**63)`, `(-2**63) // -1`). The vectors encode such Python results the same way.
- NaN payloads and NaN signs are not compared: Python prints every NaN as `nan`/`NaN`, and the compiler may commute IEEE additions, which changes which payload survives.
- No complex numbers: a finite negative base to a fractional float power is `ComplexResult` (Python returns a complex, or raises `OverflowError` from complex power).
- Format types `n` (locale) and `c` (character) are `Unsupported`.
- `parse_float`/`parse_int` reject non-ASCII digits as `Unsupported` (Python accepts, for example, `'١'`).
- Not implemented because the analysis does not call them: `statistics` `mu`/`xbar` arguments, `fmean` weights, `bool` as a distinct type, `round` and `format` of `Fraction`/`Decimal`.

## 6. Vectors

`crates/coach-numerics/tools/gen_vectors.py` runs under the oracle interpreter (it refuses anything but 3.12) and writes `vectors/common/*.json` and `vectors/<platform>/*.json`. Fixed seeds per family, sorted keys, one case per line, LF endings, ASCII. Values are strings: `f:<16 hex digits>` (float bits; results write `f:nan`), `i:<decimal>`, `e:<exception class>`, `s:<text>`, `b:0`/`b:1`. Random inputs mix arbitrary bit patterns, subnormals, values across the whole exponent range, telemetry-like values (decimals of 0 to 6 places, float32 widened to double, dyadic fractions that make exact ties), the special values and long cancellation sequences.

| File | Cases | Covers |
|---|---|---|
| `common/arith.json` | 7,575 | `+ - * / // %`, `divmod`, comparisons, `-`, `abs`, `int(float)`, `float(int)` on int/float pairs including `i64` edges, `2**53 + 1`, signed zeros, infinities, NaN |
| `common/format.json` | 6,716 | 61 float specs over 92 distinct values; 41 int specs over 18 values; error specs included |
| `common/fsum.json` | 318 | edge lists (overflow, `inf - inf`, `[1e-16, 1, 1e16]`), random lists to 300 items, cancellation |
| `common/math.json` | 2,638 | `floor`, `ceil`, `sqrt`, `degrees`, `radians`, `hypot` (2 and 3 arguments, subnormal and huge), `isclose`, `gcd` |
| `common/order.json` | 587 | `sorted` (permutations, reverse), `min`, `max`, `bisect_left`/`right`, `heapq` push/pop/replace sequences, with NaN, signed zeros, equal int/float pairs |
| `common/parse.json` | 358 | `float(str)` and `int(str)`: whitespace, underscores, specials, overflow, 30-digit decimals |
| `common/repr.json` | 3,220 | `repr` and both `json.dumps` modes; every power of ten from `1e-30` to `1e29` and its neighbours |
| `common/round.json` | 4,600 | 55 floats x 23 `ndigits` (to `+-330`), ints, near-decimal ties, exact binary ties; 225 int cases at `ndigits` -17 to -41 (9 ints including both `i64` edges; Codex Sol review finding 1: `ndigits <= -20` is 0 for every `i64`) |
| `common/stats.json` | 661 | the seven `statistics` functions on edge data and random int, float and mixed data |
| `<platform>/sum.json` | 429 | `sum` with int and float starts, mixed ints, C `long` boundary, long lists |
| `<platform>/libm.json` | 3,431 | `sin`, `cos`, `atan2`, `**` (special cases, overflow, underflow, int powers) |

Regenerate: `python crates/coach-numerics/tools/gen_vectors.py` (Windows, bundled runtime) and `python3.12 crates/coach-numerics/tools/gen_vectors.py` (WSL) for the Linux files; `--check` compares without writing. Stress: `--stress N --out DIR` writes `DIR/stress/...` with `N` random cases per family; `COACH_NUMERICS_VECTORS=DIR/stress cargo test --test vectors` runs the same tests against it. Stress output is never committed.

## 7. Coverage of the Python analysis

Call sites in `iracing-coach/skills/analyze-iracing-race/scripts` (45 modules), counted by pattern on 2026-10-03; `int()`, `float()`, `min`, `max`, `sorted`, `abs` include non-numeric uses (strings, keys).

| Primitive | Sites | Main modules | Crate |
|---|---|---|---|
| `sum()` | 175 | analysis_engine 91, race_foundations 13, groove_analysis 8, storage 8, test_day 7, tire_energy 6, 20 more | `builtin_sum`, `sum_floats`, `sum_floats_from_int_zero` |
| `math.fsum` / `_fsum` | 3 | analysis_engine (`_row_means`, `_mean` path) | `fsum` |
| `reduce(add, ..., 0.0)` | 16 | analysis_engine (lap time and energy totals) | `fold_add` |
| `statistics.fmean` | 13 | analysis_engine 5, investigation_study, race_inspection, reporting, test_day | `fmean` |
| `statistics.mean` | 1 | race_foundations | `mean` |
| `statistics.median` / `median` | 79 | analysis_engine 25, test_day 11, groove_analysis 8, driving_trials, race_investigation, race_inspection, 11 more | `median`, `median_floats` (and `sorted_floats` for `_median_of_sorted`) |
| `statistics.pstdev` | 2 | analysis_engine | `pstdev` |
| `round()` | 234 | analysis_engine 65, race_foundations 46, storage 21, lap_reference 15, test_day 14, strategy_model 10, 17 more | `round_ndigits`, `round_to_int`, `round_num` |
| `int()` | 409 | analysis_engine 124, workflow 43, race_investigation 36, storage 26, native_events 24, ibt_reader 22 | `int_from_float`, `round_to_int`, `parse_int` |
| `float()` | 253 | race_foundations 34, storage 28, ibt_reader 23, setup_catalog 23, analysis_engine 20 | `Num::to_f64`, `parse_float` |
| `abs()` | 125 | analysis_engine 36, race_investigation 14, test_day 13, reporting 11 | `abs`, `f64::abs` |
| `min()` / `max()` | 205 / 323 | analysis_engine 71 / 103, race_foundations, storage, test_day, reporting | `min_index_by`, `max_index_by`, `min_num`, `max_num` |
| `sorted()` / `.sort()` | 237 | analysis_engine 57, workflow 22, storage 20, setup_catalog 15 | `sort_permutation`, `sort_by`, `sorted_nums`, `sorted_floats` |
| `math.floor` / `ceil` | 20 | groove_analysis 5, analysis_engine (`_percentile_of_sorted`), race_foundations, reporting, storage | `floor`, `ceil` |
| `math.sqrt` | 13 | test_day 8, storage, native_events, race_foundations, tire_energy | `sqrt` |
| `math.hypot` | 14 | race_foundations 7, groove_analysis 4, analysis_engine (`_sideslip_column`), tire_energy | `hypot`, `hypot_n` |
| `math.atan2` | 5 | analysis_engine (sideslip), groove_analysis, tire_energy | `atan2` |
| `math.sin` / `cos` | 4 | groove_analysis, tire_energy | `sin`, `cos` |
| `math.degrees` / `radians` | 16 | analysis_engine 8, groove_analysis 6 | `degrees`, `radians` |
| `math.isclose` | 8 | race_plan_decision, setup_catalog, race_card, strategy_model | `isclose` |
| `math.gcd` | 1 | ibt_reader (sampling phases) | `gcd` |
| `**` | 40 | identity_contract, evidence_records, analysis_engine (`** 2`, `** 0.5`, `10 ** -digits`), time_loss, strategy_model | `float_pow`, `pow_num` |
| `//`, `%`, `divmod` | 31 `//` | analysis_engine 7, ibt_reader 4, tuning_engine 4 | `floordiv`, `modulo`, `divmod` |
| `bisect_left` | 2 | investigation_study, race_inspection | `bisect_left_by`, `bisect_left` |
| `heapq` | 5 | storage, native_events | `heap_push`, `heap_pop`, `heap_replace` |
| `json.dumps` (float tokens) | 36 | storage 9, analysis_engine 5, mcp_server 4, tuning_engine 4 | `json_float`, `json_num` (the per-site flags stay phase 3's) |
| f-string float specs | 277 | reporting 98, analysis_engine 52, test_day 37, race_investigation 21 | `format_float`, `format_int` |
| `repr`/`str` of floats | via f-strings and JSON | | `float_repr` |
| `math.isfinite` / `isnan` | 80 | everywhere (`_finite`) | `f64::is_finite`, `f64::is_nan` |

Specs in use: `.1f` (77), `.2f` (52), `.3f` (50), `.0f` (48), `g` (23), `.0%` (10), `+.3f`, `03d`, `+.1f`, `d`, `04.1f`, `+.2f`, `06.3f`, `04d`, `02x`, `02d`, `.9f`, `.1%`, `,.0f`, `+d`. All are in the vectors.

## 8. Notes for phase 2

- `analysis_engine.py` already documents its exactness rules (lines 221-248): running totals are `reduce(add, ...)` left folds (`fold_add`, or a plain loop), `sum` stays `sum` (`sum_floats` / `sum_floats_from_int_zero` / `builtin_sum` according to the start and item types), `statistics.fmean(list)` is `fsum(list) / len`, `statistics.median` is a stable sort plus a middle pick (`sorted_floats`; finite inputs sort the same under any stable sort, NaN does not).
- `_normalize_column` tests `isfinite(sum(converted, 0.0))`: that is `sum_floats(&col, 0.0).is_finite()`, compensated, and it can overflow where the values are finite. The test only chooses between two paths that return the same values and the same `complete` flag (a finite sum means every value is finite), so a port may compute the definition directly (`phase2-plan.md`, unit U1).
- `_round(value, digits)` is `round_ndigits`; `int(round(x))` is `round_to_int`; `_variance**0.5` and `(value - mean_x) ** 2` are `float_pow`, which is a C library call and therefore platform-specific (section 4).
- `max(key=abs)` and signed-zero `max`/`min` keep the first of equals: `max_index_by` / `min_index_by`.
- Performance was not measured. `fsum` allocates a partials vector per call; `round_ndigits` takes the integer fast path for the digit counts the analysis uses; `float_repr` is Rust's shortest formatter plus an integer tie check.

"""Emit coach-numerics test vectors from the running CPython 3.12.

Usage (from the repository root):

    python crates/coach-numerics/tools/gen_vectors.py            # write vectors
    python crates/coach-numerics/tools/gen_vectors.py --check    # compare, write nothing
    python crates/coach-numerics/tools/gen_vectors.py --out DIR  # write under DIR instead
    python crates/coach-numerics/tools/gen_vectors.py --stress N --out DIR
        # N random cases per stress family into DIR/stress (not committed)

Families under vectors/common/ must come out byte-identical from Windows and Linux
CPython; families under vectors/<platform>/ depend on the C library (sin, cos, atan2, pow)
or on the width of C long (sum). Output is deterministic: fixed seeds, sorted keys, one case
per line, LF line ends.

Value encoding (every numeric value is a string):
    "f:<16 hex digits>"  float bits (inputs keep NaN payloads; results write "f:nan")
    "i:<decimal>"        int
    "e:<Name>"           exception class; "e:IntBeyondI64" for an int result outside i64,
                         "e:ComplexResult" for a complex result
    "s:<text>"           text result
    "b:0" / "b:1"        bool result
"""

from __future__ import annotations

import argparse
import bisect
import heapq
import json
import math
import random
import statistics
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CRATE = HERE.parent
I64_MIN, I64_MAX = -(2**63), 2**63 - 1

if sys.version_info[:2] != (3, 12):
    sys.exit(f"coach-numerics vectors need CPython 3.12, not {sys.version.split()[0]}")

PLATFORM = {"win32": "windows", "linux": "linux"}.get(sys.platform)
if PLATFORM is None:
    sys.exit(f"unsupported platform {sys.platform}")


# --------------------------------------------------------------------------- encoding


def bits(x: float) -> str:
    return struct.pack(">d", x).hex()


def enc_in(v):
    if isinstance(v, bool):
        raise TypeError("bools are not vector values")
    if isinstance(v, int):
        return f"i:{v}"
    if isinstance(v, float):
        return f"f:{bits(v)}"
    raise TypeError(type(v))


def enc_out(v):
    if isinstance(v, bool):
        return "b:1" if v else "b:0"
    if isinstance(v, int):
        if not I64_MIN <= v <= I64_MAX:
            return "e:IntBeyondI64"
        return f"i:{v}"
    if isinstance(v, float):
        return "f:nan" if math.isnan(v) else f"f:{bits(v)}"
    if isinstance(v, complex):
        return "e:ComplexResult"
    if isinstance(v, str):
        return "s:" + v
    raise TypeError(type(v))


def run(fn, *args):
    try:
        return enc_out(fn(*args))
    except Exception as exc:  # noqa: BLE001 - the exception class is the expected result
        return "e:" + type(exc).__name__


def from_bits(value: int) -> float:
    return struct.unpack(">d", value.to_bytes(8, "big"))[0]


# --------------------------------------------------------------------------- value sources

SPECIAL = [
    0.0, -0.0, 1.0, -1.0, 0.5, -0.5, 1.5, 2.5, -2.5, 0.1, 0.2, 0.3, 1e-300, 1e300, 1e308,
    -1e308, 1.7976931348623157e308, -1.7976931348623157e308, 2.2250738585072014e-308,
    2.225073858507201e-308, 5e-324, -5e-324, 1e16, 1e-5, 1e-4, 9007199254740992.0,
    9007199254740993.0, 4503599627370496.5, 123456.789, math.pi, math.e,
    float("inf"), float("-inf"), float("nan"), -float("nan"),
]


def random_bits_float(rng: random.Random) -> float:
    return from_bits(rng.getrandbits(64))


def telemetry_float(rng: random.Random) -> float:
    kind = rng.randrange(6)
    if kind == 0:
        return rng.uniform(-400.0, 400.0)
    if kind == 1:
        return round(rng.uniform(-100.0, 100.0), rng.randrange(0, 7))
    if kind == 2:
        return rng.uniform(0.0, 1.0)
    if kind == 3:
        return float(rng.randrange(-1000, 1000)) / (2 ** rng.randrange(0, 12))
    if kind == 4:
        return rng.uniform(-1e6, 1e6) * 10.0 ** rng.randrange(-12, 12)
    return float(struct.unpack(">f", struct.pack(">f", rng.uniform(-300.0, 300.0)))[0])


def any_float(rng: random.Random) -> float:
    pick = rng.randrange(10)
    if pick == 0:
        return rng.choice(SPECIAL)
    if pick == 1:
        return random_bits_float(rng)
    if pick == 2:
        return from_bits(rng.getrandbits(52))  # subnormal or zero
    if pick == 3:
        return rng.choice([-1, 1]) * rng.uniform(1.0, 2.0) * 2.0 ** rng.randrange(-1074, 1024)
    return telemetry_float(rng)


def finite_float(rng: random.Random) -> float:
    while True:
        x = any_float(rng)
        if math.isfinite(x):
            return x


def small_int(rng: random.Random) -> int:
    return rng.choice([rng.randrange(-10, 11), rng.randrange(-1000, 1001), rng.randrange(-(2**40), 2**40)])


# --------------------------------------------------------------------------- families


def fam_sum(rng: random.Random, count: int) -> list[dict]:
    cases = []
    inf, nan = float("inf"), float("nan")
    hand = [
        ([], 0), ([], 0.0), ([-0.0], 0), ([-0.0], -0.0), ([-0.0, -0.0], -0.0), ([-0.0, 0.0], -0.0),
        ([inf, 1e-300, 1.0], 0.0), ([inf, -inf], 0.0), ([nan, 1.0], 0.0), ([1e308, 1e308], 0.0),
        ([1e308, 1e308, -1e308], 0.0), ([1e16, 1.0, -1e16], 0.0), ([0.1] * 10, 0),
        ([1e100, 1.0, -1e100, 1e-100], 0.0), ([1.0, 2**53, -9007199254740992.0], 0.0),
        ([0.1, 2**40, 0.1, 0.1, 1e16, -1e16], 0.0), ([0.1, 2**31, 0.1, 0.1, 1e16, -1e16], 0.0),
        ([0.1, 2**31 - 1, 0.1, 0.1, 1e16, -1e16], 0.0), ([2**31 - 1, 1, 0.1, 0.2, 0.3], 0),
        ([2**31, 0.1, 0.2, 0.3], 0), ([2**40, 0.1, 0.2, 0.3], 0), ([3, 0.1, 0.2], 0),
        ([0.1, 3, 0.2], 0), ([1, 2, 3], 0), ([1, 2, 3], 2**40), ([1, 2.5], 2**40),
        ([0.5, 2**62, 1e-3, -2**62], 0.0), ([2**62, 0.5, 0.25], 0), ([5, 7], -3.5),
    ]
    for items, start in hand:
        cases.append({"items": [enc_in(v) for v in items], "start": enc_in(start), "out": run(sum, items, start)})
    for index in range(count):
        n = rng.randrange(0, 40) if index % 10 else rng.randrange(100, 300)
        mode = rng.randrange(4)
        items = []
        for _ in range(n):
            if mode == 0:
                items.append(telemetry_float(rng))
            elif mode == 1:
                items.append(any_float(rng))
            elif mode == 2:
                items.append(small_int(rng) if rng.random() < 0.3 else telemetry_float(rng))
            else:
                x = rng.uniform(-1, 1) * 10.0 ** rng.randrange(-20, 20)
                items.extend([x, -x * rng.choice([1.0, 1.0, 1.0000000000000002])])
        if mode == 3:
            rng.shuffle(items)
        start = rng.choice([0, 0.0, -0.0, small_int(rng), telemetry_float(rng)])
        cases.append({"items": [enc_in(v) for v in items], "start": enc_in(start), "out": run(sum, items, start)})
    return cases


def fam_fsum(rng: random.Random, count: int) -> list[dict]:
    inf, nan = float("inf"), float("nan")
    hand = [
        [], [-0.0], [0.0, -0.0], [1e308, 1e308], [1e308, 1e308, -1e308], [inf, -inf], [inf, inf],
        [nan, 1.0], [inf, nan], [-inf, 1e308, 1e308], [1e-16, 1.0, 1e16], [1e16, 1.0, 1e-16],
        [0.1] * 10, [1e100, 1.0, -1e100, 1e-100, 1e50, -1.0, -1e50], [5e-324, 5e-324, -5e-324],
        [2.0**53, 1.0, 1.0], [-(2.0**53), -1.0, -1.0], [1.0, 1e-17, 1e-17, -1.0],
    ]
    cases = [{"items": [enc_in(v) for v in items], "out": run(math.fsum, items)} for items in hand]
    for index in range(count):
        n = rng.randrange(0, 50) if index % 10 else rng.randrange(100, 300)
        mode = rng.randrange(3)
        items = []
        for _ in range(n):
            if mode == 0:
                items.append(telemetry_float(rng))
            elif mode == 1:
                items.append(any_float(rng) if rng.random() < 0.97 else rng.choice([inf, -inf, nan]))
            else:
                x = finite_float(rng)
                items.extend([x, -x, x * 1e-17])
        if mode == 2:
            rng.shuffle(items)
        cases.append({"items": [enc_in(v) for v in items], "out": run(math.fsum, items)})
    return cases


STAT_FNS = {
    "fmean": statistics.fmean,
    "mean": statistics.mean,
    "median": statistics.median,
    "variance": statistics.variance,
    "pvariance": statistics.pvariance,
    "stdev": statistics.stdev,
    "pstdev": statistics.pstdev,
}


def fam_stats(rng: random.Random, count: int) -> list[dict]:
    inf, nan = float("inf"), float("nan")
    hand = [
        [], [1], [1.5], [1, 2], [1, 3], [1, 2, 3, 4], [1, 2, 4], [2.5, 2.5], [-0.0], [0.0, -0.0],
        [1e308, 1e308], [1e308, -1e308, 1.0], [5e-324, 1e-320], [inf, 1.0], [nan, 1.0, 2.0],
        [inf, -inf], [1, 2.5, 3], [1e16, 1.0, -1e16], [0.1, 0.2, 0.3], [3, 1, 2], [2**40, 2**40 + 1],
        [1.5, 2.5, 2.5, 2.75, 3.25, 4.75], [2.75, 1.75, 1.25, 0.25, 0.5, 1.25, 3.5],
    ]
    cases = []
    for data in hand:
        for name, fn in STAT_FNS.items():
            cases.append({"fn": name, "data": [enc_in(v) for v in data], "out": run(fn, data)})
    for index in range(count):
        n = rng.randrange(0, 30) if index % 10 else rng.randrange(100, 300)
        mode = rng.randrange(5)
        if mode == 0:
            data = [telemetry_float(rng) for _ in range(n)]
        elif mode == 1:
            data = [finite_float(rng) for _ in range(n)]
        elif mode == 2:
            data = [rng.randrange(-50, 50) for _ in range(n)]
        elif mode == 3:
            data = [rng.choice([rng.randrange(-9, 10), float(rng.randrange(-9, 10)) / 4]) for _ in range(n)]
        else:
            data = [any_float(rng) for _ in range(n)]
        name = rng.choice(list(STAT_FNS))
        cases.append({"fn": name, "data": [enc_in(v) for v in data], "out": run(STAT_FNS[name], data)})
    return cases


def fam_round(rng: random.Random, count: int) -> list[dict]:
    values = list(SPECIAL) + [
        2.675, 1.005, 0.125, 0.375, 2.5, 3.5, -2.5, 0.5, 1.5, 4.5, 1234.5, 0.045, 1.0000000000000002,
        2.0**52 + 0.5, 2.0**53, 1e22, 1e23, 123456789.123456789, 0.000123456, 5e-310,
    ]
    ndigits = [None, 0, 1, 2, 3, 4, 6, 9, 15, 17, 22, 23, 30, -1, -2, -5, -22, -307, -308, -309, 300, 323, 324]
    cases = []
    for x in values:
        for nd in ndigits:
            cases.append({"x": enc_in(x), "nd": nd, "out": run(round, x, *(() if nd is None else (nd,)))})
    for i in [0, 5, 15, 25, 35, -5, -15, -25, 2**40 + 5, -(2**40) - 5, 2**62]:
        for nd in [None, 0, 2, -1, -2, -3, -12, -19, -20, -40]:
            cases.append({"x": enc_in(i), "nd": nd, "out": run(round, i, *(() if nd is None else (nd,)))})
    # every int ndigits from -17 to -41: 10**38 is the largest power of ten in i128, and
    # 2 * (10**38 - 1) overflowed it before the port returned 0 for ndigits <= -20
    for i in [0, 1, -1, 9, -9, 5 * 10**18, -5 * 10**18, I64_MAX, I64_MIN]:
        for nd in range(-17, -42, -1):
            cases.append({"x": enc_in(i), "nd": nd, "out": run(round, i, nd)})
    for _ in range(count):
        kind = rng.randrange(4)
        if kind == 0:
            nd = rng.randrange(0, 7)
            x = (rng.randrange(-10**7, 10**7) + 0.5) / 10**nd  # near decimal ties
        elif kind == 1:
            nd = rng.choice([None, 0, 1, 2, 3, 4, 5, 6])
            x = telemetry_float(rng)
        elif kind == 2:
            nd = rng.randrange(-330, 330)
            x = any_float(rng)
        else:
            nd = rng.choice([None, 0, 1, 2, 3])
            x = float(rng.randrange(-10**6, 10**6)) / 2 ** rng.randrange(1, 6)  # exact binary ties
        cases.append({"x": enc_in(x), "nd": nd, "out": run(round, x, *(() if nd is None else (nd,)))})
    return cases


def json_token(x, allow_nan):
    return json.dumps(x, allow_nan=allow_nan)


def fam_repr(rng: random.Random, count: int) -> list[dict]:
    values = list(SPECIAL)
    for e in range(-30, 30):
        values += [10.0**e, 10.0**e * 0.9999999999999999, 10.0**e * 1.0000000000000002]
    values += [9999999999999998.0, 99999999999999990.0, 0.00009999999999999999, 2.0**63, 2.0**-1022]
    for _ in range(count):
        values.append(rng.choice([random_bits_float, telemetry_float, any_float])(rng))
    cases = []
    for x in values:
        cases.append({
            "x": enc_in(x),
            "repr": run(repr, x),
            "json": run(json_token, x, True),
            "json_strict": run(json_token, x, False),
        })
    return cases


FLOAT_SPECS = [
    "", ".0f", ".1f", ".2f", ".3f", ".9f", ".20f", "f", "F", "g", "G", ".3g", ".17g", ".0g", "#g", "#.3g",
    "e", "E", ".0e", ".3e", "#.0e", ".0%", ".1%", "%", "+.1f", "+.2f", "+.3f", " .2f", "04.1f", "06.3f",
    ",.0f", ",.2f", "_.3f", ",", ",g", "010,.1f", "012,.2f", "08,.0f", ">10.2f", "<10.2f", "^10.2f",
    "=+10.2f", "*^12.3f", "z.1f", "z.0f", "+z.2f", "#.0f", ".3", ".10", ".0", ".1", "+", " ", "010",
    "=10", "x>8", ".2d", "d", "s", ".f", "1.2.3",
]
INT_SPECS = [
    "", "d", "03d", "04d", "02d", "+d", " d", "02x", "x", "#x", "X", "#X", "o", "#o", "b", "#b", ",d", "_d",
    "_x", "_b", "_o", "08,d", "09,d", "010_d", ",", ">5", "<5", "^6", "=+8", "*>9,", ".2f", ".0%", "g", "e",
    ".3g", ",.2f", ".2", "z", ",x", "s", "#010x",
]


def fam_format(rng: random.Random, count: int) -> list[dict]:
    floats = list(SPECIAL) + [
        0.125, 0.375, 2.675, 1234567.891, -1234567.891, 0.0001, 0.00001, 123456789.0, 1e20, 100.0,
        10.0, 0.05, -0.04, 0.995, 9.995, 99999.95, 1e-7, 12.5, 0.5, -0.5, 1.0e15, 1e16, 0.123456789,
    ]
    floats += [telemetry_float(rng) for _ in range(count)]
    ints = [0, 1, -1, 7, -7, 42, 255, -255, 1000, 1234, -1234, 1234567, -1234567, 2**31, 2**53 + 1, I64_MAX, I64_MIN, 10**15]
    cases = []
    for x in floats:
        for spec in FLOAT_SPECS:
            cases.append({"x": enc_in(x), "spec": spec, "out": run(format, x, spec)})
    for i in ints:
        for spec in INT_SPECS:
            cases.append({"x": enc_in(i), "spec": spec, "out": run(format, i, spec)})
    return cases


def py_pow(a, b):
    if math.isfinite(a) and a < 0 and isinstance(b, float) and math.isfinite(b) and b != math.floor(b):
        # CPython hands this to complex power (a complex result, or OverflowError from it);
        # the port stops at ComplexResult for the whole class.
        return complex(0.0, 0.0)
    return a**b


MATH_FNS = {
    "floor": math.floor,
    "ceil": math.ceil,
    "sqrt": math.sqrt,
    "hypot": math.hypot,
    "degrees": math.degrees,
    "radians": math.radians,
    "isclose": lambda a, b, r, t: math.isclose(a, b, rel_tol=r, abs_tol=t),
    "gcd": math.gcd,
}
LIBM_FNS = {"atan2": math.atan2, "sin": math.sin, "cos": math.cos, "pow": py_pow}


def math_case(name, fn, args):
    return {"fn": name, "args": [enc_in(a) for a in args], "out": run(fn, *args)}


def fam_math(rng: random.Random, count: int) -> list[dict]:
    cases = []
    for x in SPECIAL:
        for name in ["floor", "ceil", "sqrt", "degrees", "radians"]:
            cases.append(math_case(name, MATH_FNS[name], [x]))
    for i in [0, 5, -5, 2**40]:
        for name in ["floor", "ceil", "sqrt", "degrees", "radians"]:
            cases.append(math_case(name, MATH_FNS[name], [i]))
    for x in SPECIAL:
        for y in SPECIAL[::3]:
            cases.append(math_case("hypot", math.hypot, [x, y]))
    cases.append(math_case("hypot", math.hypot, [3, 4]))
    cases.append(math_case("hypot", math.hypot, [3.0, 4.0, 12.0]))
    cases.append(math_case("hypot", math.hypot, [1e-320, 3e-321]))
    cases.append(math_case("hypot", math.hypot, [5e-324, 5e-324]))
    cases.append(math_case("hypot", math.hypot, [1e308, 1e308]))
    for a, b, r, t in [
        (1.0, 1.0 + 1e-10, 1e-9, 0.0), (1.0, 1.1, 1e-9, 0.0), (0.0, 1e-12, 1e-9, 0.0), (0.0, 1e-12, 1e-9, 1e-9),
        (float("inf"), float("inf"), 1e-9, 0.0), (float("inf"), 1e308, 1e-9, 0.0), (float("nan"), 1.0, 1e-9, 0.0),
        (1.0, 1.0, -1.0, 0.0), (100.0, 100.0000001, 1e-9, 0.0), (100.0, 100.0000001, 1e-8, 0.0),
    ]:
        cases.append(math_case("isclose", MATH_FNS["isclose"], [a, b, r, t]))
    for a, b in [(0, 0), (12, 18), (-12, 18), (7, 0), (0, -7), (2**40, 2**20), (I64_MIN, 2), (I64_MIN, 0)]:
        cases.append(math_case("gcd", math.gcd, [a, b]))
    for _ in range(count):
        name = rng.choice(["hypot", "hypot", "hypot", "sqrt", "floor", "ceil", "degrees", "radians", "hypot3"])
        if name == "hypot":
            x = telemetry_float(rng) if rng.random() < 0.6 else any_float(rng)
            y = rng.choice([x * rng.uniform(0.5, 2.0), telemetry_float(rng), any_float(rng), x])
            cases.append(math_case("hypot", math.hypot, [x, y]))
        elif name == "hypot3":
            cases.append(math_case("hypot", math.hypot, [telemetry_float(rng) for _ in range(3)]))
        else:
            cases.append(math_case(name, MATH_FNS[name], [any_float(rng) if rng.random() < 0.5 else telemetry_float(rng)]))
    return cases


def fam_libm(rng: random.Random, count: int) -> list[dict]:
    cases = []
    for x in SPECIAL:
        for name in ["sin", "cos"]:
            cases.append(math_case(name, LIBM_FNS[name], [x]))
        for y in SPECIAL[::2]:
            cases.append(math_case("atan2", math.atan2, [x, y]))
            cases.append(math_case("pow", py_pow, [x, y]))
    for a, b in [(2, 10), (2, 64), (2, 62), (-3, 3), (10, -3), (0, -1), (0, 0), (-1, 2**40), (7, 0), (-2, 63)]:
        cases.append(math_case("pow", py_pow, [a, b]))
    for x in [2.0, 3.0, -2.0, 0.5, 1.0000001, 10.0, 1.5]:
        for y in [2, 3, 0.5, -1, -0.5, 1074, -1074, -1075, 1023, 1024, 1e10, -1800.0, 1.5]:
            cases.append(math_case("pow", py_pow, [x, y]))
    for _ in range(count):
        name = rng.choice(["sin", "cos", "atan2", "atan2", "pow", "pow"])
        if name in ("sin", "cos"):
            x = rng.choice([rng.uniform(-7.0, 7.0), telemetry_float(rng), any_float(rng)])
            cases.append(math_case(name, LIBM_FNS[name], [x]))
        elif name == "atan2":
            cases.append(math_case(name, math.atan2, [telemetry_float(rng), telemetry_float(rng)]))
        else:
            x = rng.choice([telemetry_float(rng), abs(telemetry_float(rng)), any_float(rng)])
            y = rng.choice([2, 2.0, 0.5, 3, -1.0, rng.uniform(-3.0, 3.0), float(rng.randrange(-5, 6)), 10, -2])
            cases.append(math_case(name, py_pow, [x, y]))
    return cases


ARITH = {
    "add": lambda a, b: a + b,
    "sub": lambda a, b: a - b,
    "mul": lambda a, b: a * b,
    "truediv": lambda a, b: a / b,
    "floordiv": lambda a, b: a // b,
    "mod": lambda a, b: a % b,
    "lt": lambda a, b: a < b,
    "le": lambda a, b: a <= b,
    "gt": lambda a, b: a > b,
    "ge": lambda a, b: a >= b,
    "eq": lambda a, b: a == b,
}


def arith_value(rng: random.Random):
    pick = rng.randrange(6)
    if pick == 0:
        return small_int(rng)
    if pick == 1:
        return rng.choice([2**53, 2**53 + 1, -(2**53) - 1, 2**62, I64_MAX, I64_MIN, 2**63 - 2**10, rng.randrange(I64_MIN, I64_MAX)])
    if pick == 2:
        return rng.choice(SPECIAL)
    return finite_float(rng) if rng.random() < 0.7 else telemetry_float(rng)


def fam_arith(rng: random.Random, count: int) -> list[dict]:
    cases = []
    values = [0, 1, -1, 7, -7, 2**53 + 1, I64_MAX, I64_MIN, 0.0, -0.0, 7.5, -7.5, 1e308, 5e-324,
              float("inf"), float("-inf"), float("nan"), 9223372036854775807.0, 9223372036854774784.0]
    pairs = [(a, b) for a in values for b in values]
    pairs += [(arith_value(rng), arith_value(rng)) for _ in range(count)]
    for a, b in pairs:
        for name, fn in ARITH.items():
            cases.append({"op": name, "a": enc_in(a), "b": enc_in(b), "out": run(fn, a, b)})
        try:
            q, r = divmod(a, b)
            out = [enc_out(q), enc_out(r)]
            if any(part.startswith("e:") for part in out):
                out = next(part for part in out if part.startswith("e:"))
        except Exception as exc:  # noqa: BLE001
            out = "e:" + type(exc).__name__
        cases.append({"op": "divmod", "a": enc_in(a), "b": enc_in(b), "out": out})
    for v in values + [arith_value(rng) for _ in range(count // 4)]:
        cases.append({"op": "neg", "a": enc_in(v), "out": run(lambda x: -x, v)})
        cases.append({"op": "abs", "a": enc_in(v), "out": run(abs, v)})
        if isinstance(v, float):
            cases.append({"op": "int", "a": enc_in(v), "out": run(int, v)})
        else:
            cases.append({"op": "float", "a": enc_in(v), "out": run(float, v)})
    return cases


class Item:
    """Heap item compared by value only, carrying its index to show identity."""

    __slots__ = ("value", "index")

    def __init__(self, value, index):
        self.value = value
        self.index = index

    def __lt__(self, other):
        return self.value < other.value


def order_value(rng: random.Random, pool):
    return rng.choice(pool)


def make_order_data(rng: random.Random, n: int):
    nan, inf = float("nan"), float("inf")
    pool = [0.0, -0.0, 1.0, 1, 2, 2.0, -1, -1.0, 0.5, 3.25, inf, -inf, nan, -nan, from_bits(0x7FF8000000000001), 2**53 + 1, 9007199254740992.0]
    shape = rng.randrange(6)
    if shape == 0:
        data = [rng.choice(pool) for _ in range(n)]
    elif shape == 1:
        data = [telemetry_float(rng) for _ in range(n)]
        for _ in range(rng.randrange(0, 4)):
            if data:
                data[rng.randrange(len(data))] = rng.choice([nan, -nan])
    elif shape == 2:
        data = sorted(telemetry_float(rng) for _ in range(n))
        for _ in range(rng.randrange(0, 1 + n // 10)):
            if data:
                data[rng.randrange(len(data))] = rng.choice([nan, telemetry_float(rng)])
    elif shape == 3:
        data = sorted((float(rng.randrange(0, 20)) for _ in range(n)), reverse=rng.random() < 0.5)
        for _ in range(rng.randrange(0, 3)):
            if data:
                data.insert(rng.randrange(len(data)), nan)
    elif shape == 4:
        runs = []
        while len(runs) < n:
            run_len = rng.randrange(1, 80)
            base = sorted(rng.randrange(0, 1000) / 4 for _ in range(run_len))
            runs.extend(base if rng.random() < 0.7 else base[::-1])
        data = runs[:n]
        if data and rng.random() < 0.6:
            data[rng.randrange(len(data))] = nan
    else:
        data = [rng.choice([rng.randrange(-5, 6), float(rng.randrange(-5, 6)), nan]) for _ in range(n)]
    return data


def fam_order(rng: random.Random, count: int) -> list[dict]:
    cases = []
    for index in range(count):
        n = rng.randrange(0, 70) if index % 8 else rng.randrange(100, 400)
        data = make_order_data(rng, n)
        n = len(data)
        encoded = [enc_in(v) for v in data]
        reverse = rng.random() < 0.25
        perm = sorted(range(n), key=data.__getitem__, reverse=reverse)
        cases.append({"fn": "sorted", "data": encoded, "reverse": reverse, "out": perm})
        if n and index % 3 == 0:
            cases.append({"fn": "min", "data": encoded, "out": min(range(n), key=data.__getitem__)})
            cases.append({"fn": "max", "data": encoded, "out": max(range(n), key=data.__getitem__)})
        if index % 3 == 1:
            x = rng.choice(data) if data and rng.random() < 0.5 else order_value(rng, [0.0, 1, 2.5, float("nan"), -1.0, 300.0])
            cases.append({"fn": "bisect_left", "data": encoded, "x": enc_in(x), "out": bisect.bisect_left(data, x)})
            cases.append({"fn": "bisect_right", "data": encoded, "x": enc_in(x), "out": bisect.bisect_right(data, x)})
        if index % 3 == 2 and n:
            heap: list[Item] = []
            ops = []
            popped = []
            for i, value in enumerate(data):
                if heap and rng.random() < 0.3:
                    ops.append(["pop", None])
                    popped.append(heapq.heappop(heap).index)
                if heap and rng.random() < 0.15:
                    ops.append(["replace", i])
                    popped.append(heapq.heapreplace(heap, Item(value, i)).index)
                else:
                    ops.append(["push", i])
                    heapq.heappush(heap, Item(value, i))
            while heap:
                ops.append(["pop", None])
                popped.append(heapq.heappop(heap).index)
            cases.append({"fn": "heap", "data": encoded, "ops": ops, "out": popped})
    return cases


def fam_parse(rng: random.Random, count: int) -> list[dict]:
    texts = [
        "1", " 1.5 ", "1_000", "1__0", "_1", "1_", "1e5", "1E+05", "1e1_0", "1_e5", ".5", "5.", ".", "", " ",
        "inf", "-Infinity", "nAn", "+nan", "-nan", "infinit", "infinityy", "0x10", "1.2.3", "1e", "e5", "-",
        "+1", "-0", "00012", "1_000.000_1", "+-1", "--1", "1 2", " 1 ", "\x1c2\x1f", "\t\n 3 \r",
        "9" * 400, "1e400", "-1e400", "1e-400", "4.9406564584124654e-324", "2.4703282292062327e-324",
        "2.4703282292062328e-324", "1.7976931348623158e308", "1.7976931348623159e308",
        "9223372036854775807", "9223372036854775808", "-9223372036854775808", "-9223372036854775809",
        "0_0", "0.1e-0_1", "1.e5", "INF", "Infinity ", "1.0", "1e+", "1.5e-3_0",
    ]
    for _ in range(count):
        digits = "".join(rng.choice("0123456789") for _ in range(rng.randrange(1, 30)))
        point = rng.randrange(0, len(digits) + 1)
        text = digits[:point] + "." + digits[point:] if rng.random() < 0.7 else digits
        if rng.random() < 0.5:
            text += f"e{rng.randrange(-340, 320)}"
        texts.append(rng.choice(["", "-", "+"]) + text)
    return [{"text": t, "float": run(float, t), "int": run(int, t)} for t in texts]


def fam_parse_unicode(rng: random.Random, count: int) -> list[dict]:
    """``float``/``int`` of non-ASCII text: every decimal digit of every script (category Nd),
    digits that are not decimal, Unicode spaces and characters near them."""

    import unicodedata

    texts = [
        "٠", "١٢", "-٣.٥e١", " ١　", " १_२ ",
        "²", "½", "Ⅷ", "١²", "​1", "1﻿", "᠎1", "\x1c1٠", "1٠\x1f",
        "\x85١", "١ ٢", "１０", "\U0001d7ce\U0001d7ff", "\U0001fbf9", "๑.๕",
        "١e٢٢٢", "inf ", " nan", "١_٢", "١__٢", "İ",
        "é", "1é", "٣٣٣٣٣٣٣٣٣٣٣٣٣٣٣٣٣٣٣٣",
    ]
    for code in range(0x110000):
        if unicodedata.decimal(chr(code), -1) >= 0 and code > 0x7F:
            texts.append(chr(code))
    pool = [chr(code) for code in range(0x80, 0x3100) if not 0xD800 <= code < 0xE000]
    for _ in range(count):
        texts.append("".join(rng.choice(pool) if rng.random() < 0.3 else rng.choice("0123456789._e- ")
                             for _ in range(rng.randrange(1, 8))))
    return [{"text": t, "float": run(float, t), "int": run(int, t)} for t in texts]


def fam_int_low64(rng: random.Random, count: int) -> list[dict]:
    """``int(x) & (2**64 - 1)`` as a signed 64-bit value, for floats of every magnitude."""

    def low64(x: float) -> int:
        value = int(x) & (2**64 - 1)
        return value - 2**64 if value >= 2**63 else value

    xs = [2.0**63, -(2.0**63), 2.0**64, -(2.0**64), 2.0**63 + 2048.0, -(2.0**63) - 2048.0, 2.0**70 + 2.0**18,
          1.7976931348623157e308, -1.7976931348623157e308, 9223372036854774784.0, -9223372036854775808.0,
          0.0, -0.0, 1.5, -1.5, 4.2e18, 1.8446744073709552e19, 1.8446744073709550e19, float("nan"),
          float("inf"), float("-inf")]
    for _ in range(count):
        xs.append(any_float(rng))
        exponent = rng.randrange(60, 130)
        xs.append(rng.choice((-1.0, 1.0)) * rng.random() * 2.0**exponent)
    return [{"x": enc_in(x), "out": run(low64, x)} for x in xs]


COMMON = {
    "fsum": (fam_fsum, 300),
    "stats": (fam_stats, 500),
    "round": (fam_round, 3000),
    "repr": (fam_repr, 3000),
    "format": (fam_format, 40),
    "math": (fam_math, 2000),
    "arith": (fam_arith, 250),
    "order": (fam_order, 220),
    "parse": (fam_parse, 300),
    "parse_unicode": (fam_parse_unicode, 300),
    "int_low64": (fam_int_low64, 300),
}
PLATFORM_FAMILIES = {
    "sum": (fam_sum, 400),
    "libm": (fam_libm, 2000),
}
# Families added later take seeds after the first set, so the first set's files never change.
LATER_FAMILIES = ("parse_unicode", "int_low64")
SEEDS = {
    name: 20261003 + index
    for index, name in enumerate(sorted((set(COMMON) | set(PLATFORM_FAMILIES)) - set(LATER_FAMILIES)))
}
SEEDS.update({name: 20261003 + len(SEEDS) + index for index, name in enumerate(LATER_FAMILIES)})


def render(family: str, cases: list[dict], platform: str | None) -> str:
    header = {"family": family, "generator": "crates/coach-numerics/tools/gen_vectors.py"}
    if platform is not None:
        header["platform"] = platform
        header["python"] = sys.version.split()[0]
    head = json.dumps(header, sort_keys=True, separators=(",", ":"))[:-1]
    lines = [json.dumps(case, sort_keys=True, separators=(",", ":"), ensure_ascii=True) for case in cases]
    return head + ',"cases":[\n' + ",\n".join(lines) + "\n]}\n"


def build(stress: int | None = None) -> dict[str, str]:
    out = {}
    for name, (fn, count) in COMMON.items():
        n = stress if stress is not None else count
        out[f"common/{name}.json"] = render(name, fn(random.Random(SEEDS[name] + (stress or 0)), n), None)
    for name, (fn, count) in PLATFORM_FAMILIES.items():
        n = stress if stress is not None else count
        out[f"{PLATFORM}/{name}.json"] = render(name, fn(random.Random(SEEDS[name] + (stress or 0)), n), PLATFORM)
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="compare with the vectors on disk")
    parser.add_argument("--out", type=Path, default=CRATE / "vectors")
    parser.add_argument("--stress", type=int, default=None, help="random cases per family")
    args = parser.parse_args()
    files = build(args.stress)
    root = args.out / "stress" if args.stress is not None else args.out
    if args.check:
        stale = []
        for rel, text in files.items():
            path = root / rel
            if not path.exists() or path.read_bytes() != text.encode("ascii"):
                stale.append(rel)
        for rel in stale:
            print(f"differs: {rel}")
        print(f"{len(files) - len(stale)} of {len(files)} vector files identical ({PLATFORM}, Python {sys.version.split()[0]})")
        return 1 if stale else 0
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("ascii"))
        print(f"wrote {path} ({len(text)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

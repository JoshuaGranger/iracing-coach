#!/usr/bin/env python3
"""Writer vectors (port phase 3): ``json.dumps`` outcomes from the oracle for the native writer.

Each case is one value, pickled exactly as the seam pickles it
(``native_stages._WriterPickler``), with the request the seam sends for one of
its modes and the oracle's outcome for the same value and options:

* ``text``: ``json.dumps(value, sort_keys=True, separators=(",", ":"),
  default=str, ensure_ascii=..., allow_nan=...)`` plus the terminator
  (``bytes`` mode; ``sha256`` mode also records the hex digest);
* ``raises``: the exception class and message, for ``UnicodeEncodeError``
  (``str.encode("utf-8")`` of the text) its start, end and characters;
* ``recorded``: the class and message of each exception the pickler recorded
  for a value outside the native model, so the replay can check that a
  ``python_exception`` answer names one the oracle raised;
* ``fallback``: hand-written cases outside the writer's model (sets, other
  key types, deep nesting) whose answer must be ``fallback``.

Since the Codex phase-3 review (finding 1) the seam's pickler stops at any
object outside the exact types and the call falls back before the writer
runs. The hand-written values that do so (subclasses, objects ``default=str``
stringifies, classes and functions, an object whose ``__str__`` raises) are kept as reader vectors of
the request's marker (``seam_fallback: true``): :class:`MarkerPickler` writes
them as the seam did before the review, a marker carrying the JSON text or the
recorded exception, which the reader still accepts. The seam-level behaviour
(one oracle run, side effects in Python's order, a counted fallback) is tested
in ``iracing-coach/tests/test_native_stages.py``.

``crates/coach-analysis/tests/writer.rs`` runs every case through
``coach_analysis::writer::run`` and compares the answer exactly. Values are
deterministic (fixed seeds); pickles and outcomes do not depend on the
platform (no C library call is involved), so one file serves both, and
``--check`` must report it identical under the Windows and the Linux
interpreter.

Usage::

    python -X utf8 crates/coach-analysis/tools/emit_writer_vectors.py [--check]
    python -X utf8 crates/coach-analysis/tools/emit_writer_vectors.py --stress 20000 --seed 7 --out STRESS.json

``--stress`` writes random cases only (never committed); ``COACH_WRITER_VECTORS=STRESS.json
cargo test -p coach-analysis --test writer`` replays them too.
"""

from __future__ import annotations

import argparse
import decimal
import enum
import hashlib
import io
import json
import math
import pathlib
import random
import struct
import sys
from pathlib import Path
from typing import Any

REPOSITORY = Path(__file__).resolve().parents[3]
SCRIPTS = REPOSITORY / "iracing-coach" / "skills" / "analyze-iracing-race" / "scripts"
OUT = REPOSITORY / "crates" / "coach-analysis" / "tests" / "vectors" / "writer" / "cases.json"
sys.path.insert(0, str(SCRIPTS))

import native_stages  # noqa: E402

if sys.version_info[:2] != (3, 12):
    raise SystemExit("writer vectors come from the CPython 3.12 oracle")

MODES = {
    # name: (seam mode, terminator, output)
    "bytes": ("bytes", "\n", "bytes"),
    "bytes-crlf": ("file", "\r\n", "bytes"),  # the file mode's text, returned as bytes
    "sha256": ("sha256", "", "sha256"),
}


class Color(enum.IntEnum):
    RED = 3
    BLUE = -7


class Label(str):
    pass


class Ratio(float):
    pass


class Shouty:
    def __str__(self) -> str:
        return "SHOUT é \x01"


class Broken:
    def __str__(self) -> str:
        raise RuntimeError("str() refused")


class Items(dict):
    def items(self):  # type: ignore[override]
        return [("z", 1), ("a", 2)]


class Seq(list):
    pass


class MarkerPickler(native_stages._WriterPickler):
    """The marker form of the wire format (the seam's pickler before review finding 1).

    An object outside the exact types becomes ``_writer_marker`` with the text
    ``json.dumps`` writes for it, or the index of the exception that raised.
    Vectors only: evaluating ``default=str`` here, out of ``json.dumps``'s
    order, is exactly why the seam no longer does it.
    """

    def __init__(self, file: Any, options: dict[str, Any]) -> None:
        super().__init__(file, options)
        self.raised: list[BaseException] = []

    def reducer_override(self, obj: Any) -> Any:
        if obj is native_stages._writer_marker:
            return NotImplemented
        try:
            text = json.dumps(obj, **self.options)
        except RecursionError:
            return native_stages._writer_marker, ("f", "a value nested beyond the recursion limit")
        except Exception as exc:  # noqa: BLE001 - recorded, named by python_exception
            self.raised.append(exc)
            return native_stages._writer_marker, ("e", len(self.raised) - 1)
        return native_stages._writer_marker, ("v", text)


def f32(value: float) -> float:
    return struct.unpack("<f", struct.pack("<f", value))[0]


def bits(value: int) -> float:
    return struct.unpack("<d", struct.pack("<Q", value))[0]


def hand_cases() -> list[tuple[str, Any, bool]]:
    """(name, value, expect fallback)."""

    nan, inf = float("nan"), float("inf")
    cyclic_list: list[Any] = [1]
    cyclic_list.append(cyclic_list)
    cyclic_dict: dict[str, Any] = {"a": 1}
    cyclic_dict["self"] = cyclic_dict
    inner: list[Any] = []
    cyclic_tuple = (inner,)
    inner.append(cyclic_tuple)
    shared = [1.5, "x"]
    deep: Any = 0
    for _ in range(300):
        deep = [deep]
    shallow: Any = 0
    for _ in range(150):
        shallow = {"n": shallow}
    floats = [
        0.0, -0.0, 1.0, -1.0, 0.1, 0.2, 0.30000000000000004, 1e16, 1e15, 9007199254740993.0, 1e-4, 1e-5,
        123456789.123456789, 5e-324, 2.2250738585072014e-308, 1.7976931348623157e308, -1.7976931348623157e308,
        f32(-125.66165161132812), f32(0.1), f32(3.4028234663852886e38), f32(1e-45), 1 / 3, 2 / 3, 100.0, 1e22, 1e23,
    ]
    cases: list[tuple[str, Any, bool]] = [
        ("scalars", [None, True, False, 0, -1, 1, 255, 65535, -(2**31), 2**31 - 1, 2**31, 2**63 - 1, -(2**63)], False),
        ("floats", floats, False),
        ("float-specials", [nan, inf, -inf, -nan], False),
        ("float-special-in-dict", {"b": 1.0, "a": inf}, False),
        ("big-ints", [2**63, -(2**63) - 1, 2**64, -(2**64), 10**30, -(10**30), 10**640, 3**2000], False),
        ("int-limit-ok", [10**4300 - 1, -(10**4300 - 1)], False),
        ("int-limit-over", [1, 10**4300], False),
        ("int-limit-over-negative", -(10**4300), False),
        ("strings-controls", "".join(chr(c) for c in range(0, 0x30)) + "\x7f\x80\xa0", False),
        ("strings-escapes", ['"', "\\", "/", "\b\f\n\r\t", "  ", "é", "€", "😀", "\U0010ffff", "﻿"], False),
        ("string-lone-surrogate", ["ok", "a" + chr(0xD800) + "b"], False),
        ("string-surrogate-run", "xy" + chr(0xD800) + chr(0xDC00) + chr(0xDFFF) + "z", False),
        ("string-surrogate-later", {"a": "é😀", "b": ["x", chr(0xDBFF)]}, False),
        ("bytes", [b"", b"x'\"\x00\x80\t\n\r\\", b"it's", b'say "hi"', bytes(range(256))], False),
        ("containers-empty", [[], {}, (), [[]], {"": {}}], False),
        ("tuples", (1, (2, (3,)), [4, (5, 6, 7, 8)]), False),
        ("shared", {"a": shared, "b": shared, "c": [shared, shared]}, False),
        ("cyclic-list", cyclic_list, False),
        ("cyclic-dict", cyclic_dict, False),
        ("cyclic-tuple", cyclic_tuple, False),
        ("cycle-after-nan", [nan, cyclic_list], False),
        ("keys-sorted", {"b": 1, "a": 2, "": 3, "B": 4, "é": 5, "😀": 6, "￿": 7, "a\x00": 8, "aa": 9}, False),
        ("keys-numeric", {2: "two", 1.5: "x", True: "t", -3: "m", 1e100: "big", 0.0: "z"}, False),
        ("keys-none-alone", {None: 1}, False),
        ("keys-none-mixed", {None: 1, 2: 3}, False),
        ("keys-str-int", {1: 2, "a": 3}, False),
        ("keys-str-int-later", {"a": 1, "b": 2, "c": 3, 4: 5, "d": 6}, False),
        ("keys-nan", {nan: 1, 2.0: 0, -inf: 3, 1: 4}, False),
        ("keys-inf", {inf: 1}, False),
        ("keys-bool", {False: "f", True: "t", 0.5: "h"}, False),
        ("exotic-values", {"e": Color.RED, "f": Color.BLUE, "l": Label("sub"), "r": Ratio(2.5), "p": pathlib.PurePosixPath("/x/y"),
                           "d": decimal.Decimal("1.10"), "s": Shouty(), "i": Items(q=1), "q": Seq([1, 2])}, False),
        ("exotic-raises", [1, Broken()], False),
        ("exotic-after-error", [float("nan"), Broken()], False),
        ("exotic-cycle", Seq(), False),
        ("nested-150", shallow, False),
        ("fallback-set", [1, {1, 2}], True),
        ("fallback-frozenset", {"a": frozenset({"x"})}, True),
        ("fallback-bytearray", [bytearray(b"q")], True),
        ("fallback-tuple-key", {(1, 2): 3}, True),
        ("fallback-bigint-key", {2**70: 1}, True),
        ("fallback-subclass-key", {Label("k"): 1}, True),
        ("fallback-deep", deep, True),
        # Classes and functions also reach the seam's reducer_override (before the C pickler's
        # own save_type/save_global): seam fallbacks, here marker vectors like the others.
        ("exotic-class", {"c": int}, False),
        ("exotic-none-type", [1, type(None)], False),
        ("exotic-function", [len, math.floor], False),  # str() without an address
        ("exotic-class-after-nan", [nan, int], False),
    ]
    exotic_cycle = next(value for name, value, _ in cases if name == "exotic-cycle")
    exotic_cycle.append(exotic_cycle)
    return cases


def random_float(rng: random.Random) -> float:
    kind = rng.randrange(6)
    if kind == 0:
        return bits(rng.getrandbits(64))
    if kind == 1:
        return f32(rng.uniform(-500.0, 500.0))
    if kind == 2:
        return round(rng.uniform(-1e4, 1e4), rng.randrange(0, 7))
    if kind == 3:
        return rng.choice([0.0, -0.0, 0.5, 1.5, 2.5, -2.5]) * 2.0 ** rng.randrange(-60, 60)
    if kind == 4:
        return rng.uniform(-1.0, 1.0) * 10.0 ** rng.randrange(-320, 308)
    return rng.choice([0.1, 1e16, 1e-5, 123.456, 5e-324])


def random_text(rng: random.Random) -> str:
    pieces = []
    for _ in range(rng.randrange(0, 12)):
        kind = rng.randrange(10)
        if kind < 5:
            pieces.append(chr(rng.randrange(0x20, 0x7F)))
        elif kind == 5:
            pieces.append(chr(rng.randrange(0, 0x20)))
        elif kind == 6:
            pieces.append(chr(rng.randrange(0x80, 0x800)))
        elif kind == 7:
            code = rng.randrange(0x800, 0x10000)
            pieces.append(chr(code if not 0xD800 <= code <= 0xDFFF else 0xE000))
        elif kind == 8:
            pieces.append(chr(rng.randrange(0x10000, 0x110000)))
        else:
            pieces.append(rng.choice(['"', "\\", "\x7f", " "]))
    return "".join(pieces)


def random_value(rng: random.Random, depth: int) -> Any:
    kind = rng.randrange(12 if depth < 5 else 7)
    if kind == 0:
        return None
    if kind == 1:
        return rng.random() < 0.5
    if kind == 2:
        return rng.choice([rng.randrange(-1000, 1000), rng.getrandbits(64) - 2**63, rng.getrandbits(90) - 2**89])
    if kind in (3, 4):
        return random_float(rng)
    if kind == 5:
        return random_text(rng)
    if kind == 6:
        return bytes(rng.getrandbits(8) for _ in range(rng.randrange(0, 6)))
    if kind in (7, 8):
        items = [random_value(rng, depth + 1) for _ in range(rng.randrange(0, 6))]
        return items if kind == 7 or rng.random() < 0.7 else tuple(items)
    if kind == 9 and rng.random() < 0.15:
        keys: list[Any] = [rng.choice([rng.randrange(-5, 5), random_float(rng), True, False]) for _ in range(rng.randrange(1, 5))]
        return {key: random_value(rng, depth + 1) for key in keys}
    return {random_text(rng): random_value(rng, depth + 1) for _ in range(rng.randrange(0, 7))}


def oracle(value: Any, mode: str) -> dict[str, Any]:
    seam_mode, terminator, output = MODES[mode]
    ensure_ascii, allow_nan, _ = native_stages.WRITER_MODES[seam_mode]
    try:
        text = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=ensure_ascii, allow_nan=allow_nan)
        data = (text + terminator).encode("utf-8")
    except UnicodeEncodeError as exc:
        return {"raises": {"class": "UnicodeEncodeError", "start": exc.start, "end": exc.end,
                           "characters": [ord(c) for c in exc.object[exc.start:exc.end]]}}
    except (ValueError, TypeError, RuntimeError) as exc:
        return {"raises": {"class": type(exc).__name__, "message": str(exc)}}
    if output == "sha256":
        return {"length": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    return {"length": len(data), "text": data.decode("utf-8")}


def case(name: str, value: Any, mode: str, fallback: bool) -> dict[str, Any]:
    seam_mode, terminator, output = MODES[mode]
    ensure_ascii, allow_nan, _ = native_stages.WRITER_MODES[seam_mode]
    options = native_stages._writer_options(ensure_ascii, allow_nan)
    buffer = io.BytesIO()
    seam_fallback = False
    try:
        native_stages._WriterPickler(buffer, options).dump(value)
    except native_stages._WriterUnsupported:
        seam_fallback = True
    buffer = io.BytesIO()
    pickler = MarkerPickler(buffer, options)
    pickler.dump(value)
    if not seam_fallback:
        seam = io.BytesIO()
        native_stages._WriterPickler(seam, options).dump(value)
        assert seam.getvalue() == buffer.getvalue(), f"{name}: the marker pickler differs from the seam's"
    request = {
        "version": native_stages.WRITER_VERSION, "ensure_ascii": ensure_ascii, "allow_nan": allow_nan,
        "sort_keys": True, "separators": [",", ":"], "indent": None, "terminator": terminator, "output": output,
        "marker": [native_stages._writer_marker.__module__, native_stages._writer_marker.__qualname__],
        "max_str_digits": sys.get_int_max_str_digits(),
    }
    entry: dict[str, Any] = {"name": f"{name}/{mode}", "request": request, "pickle": buffer.getvalue().hex()}
    if seam_fallback:
        entry["seam_fallback"] = True
    if fallback:
        entry["expect"] = {"fallback": True}
    else:
        entry["expect"] = oracle(value, mode)
        entry["recorded"] = [[type(exc).__name__, str(exc)] for exc in pickler.raised]
    return entry


def build(stress: int = 0, seed: int = 20261003) -> dict[str, Any]:
    entries = []
    if not stress:
        for name, value, fallback in hand_cases():
            for mode in MODES:
                entries.append(case(name, value, mode, fallback))
    rng = random.Random(seed)
    for number in range(stress or 400):
        value = random_value(rng, 0)
        for mode in ("bytes", "sha256"):
            entries.append(case(f"random-{number:03d}", value, mode, False))
    return {
        "generator": "crates/coach-analysis/tools/emit_writer_vectors.py",
        "python": "3.12",
        "cases": entries,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="compare with the committed file instead of writing")
    parser.add_argument("--stress", type=int, default=0, help="random cases only, to --out (not committed)")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    if args.stress:
        if args.out is None:
            raise SystemExit("--stress needs --out")
        args.out.write_text(json.dumps(build(args.stress, args.seed), sort_keys=True, ensure_ascii=True) + "\n", encoding="utf-8", newline="\n")
        print(f"wrote {2 * args.stress} stress cases to {args.out}")
        return 0
    text = json.dumps(build(), indent=0, sort_keys=True, ensure_ascii=True) + "\n"
    if args.check:
        same = OUT.is_file() and OUT.read_text(encoding="utf-8") == text
        print(f"{OUT.relative_to(REPOSITORY).as_posix()}: {'identical' if same else 'DIFFERENT'}")
        return 0 if same else 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {len(json.loads(text)['cases'])} cases to {OUT.relative_to(REPOSITORY).as_posix()} ({len(text)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

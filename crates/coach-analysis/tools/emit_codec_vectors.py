#!/usr/bin/env python3
"""Write the codec conformance vectors for the port phase 2 skeleton (unit I0).

One document exercises every rule of ``kernel_codec`` codec version 1: scalars at
their edges (i64 bounds, ints beyond them, signed zeros, subnormals, shortest-repr
ties, non-finite floats), strings with every JSON escape class, tuples, sets of
numbers, strings and tuples, dicts with non-string and ``$``-prefixed keys, a
dataclass, the table references, and lists on both sides of the column rule
(every element type, with and without ``None``, and the lists that must stay
inline). The Rust test (``tests/kernels_codec.rs``) decodes it, compares the
column form with the inline form, and re-encodes both byte for byte.

Output under ``--out`` (default ``crates/coach-analysis/tests/vectors/codec``)::

    <platform>/codec.json         document, column-valued lists in codec.ccs
    <platform>/codec.ccs          its vector store
    <platform>/codec.inline.json  the same document with every list inline

Run with CPython 3.12 (the bundled runtime on Windows, ``python3.12`` in WSL)::

    python -X utf8 crates/coach-analysis/tools/emit_codec_vectors.py [--check]
"""

from __future__ import annotations

import argparse
import json
import math
import random
import struct
import sys
import types
from pathlib import Path
from typing import Any

TOOLS = Path(__file__).resolve().parent
CRATE = TOOLS.parent
SCRIPTS = CRATE.parents[1] / "iracing-coach" / "skills" / "analyze-iracing-race" / "scripts"
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(SCRIPTS))

import kernel_codec as codec  # noqa: E402
from steering_basis import SteeringBasis  # noqa: E402

PRODUCER = "emit_codec_vectors.py"
SEED = 20261003


def _float_bits(rng: random.Random) -> float:
    """A float from arbitrary bits, telemetry-like values or edges (never NaN)."""

    choice = rng.randrange(6)
    if choice == 0:
        value = struct.unpack("<d", struct.pack("<Q", rng.getrandbits(64)))[0]
        return 0.0 if math.isnan(value) else value
    if choice == 1:
        return struct.unpack("<f", struct.pack("<f", rng.uniform(-400.0, 400.0)))[0]
    if choice == 2:
        return round(rng.uniform(-1000.0, 1000.0), rng.randrange(7))
    if choice == 3:
        return rng.choice([0.0, -0.0, 5e-324, -5e-324, 2.2250738585072014e-308, 1.7976931348623157e308, 1e16, 1e-5])
    if choice == 4:
        return rng.randrange(-(2**53), 2**53) * 2.0 ** rng.randrange(-60, 60)
    return rng.random()


# Stands in for the analysis TelemetryTable: the codec writes it, its channel map and its
# channel lists by reference.
TABLE = types.SimpleNamespace(channels={"Speed": [float(index) for index in range(300)], "Lap": [1, 2]})


def document() -> dict[str, Any]:
    rng = random.Random(SEED)
    floats = [_float_bits(rng) for _ in range(300)]
    floats_special = floats[:250] + [math.nan, math.inf, -math.inf, -0.0, 0.0, 5e-324]
    with_none = [None if rng.random() < 0.2 else value for value in floats]
    ints = [rng.randrange(-(2**63), 2**63) for _ in range(256)] + [2**63 - 1, -(2**63), 0, -1]
    ints_none = [None if index % 7 == 3 else value for index, value in enumerate(ints)]
    bools = [rng.random() < 0.5 for _ in range(257)]
    bools_none = [None if index % 5 == 0 else value for index, value in enumerate(bools)]
    words = ["", "a", "é", "日本", "🏁", "\x00", "\x1f", '"', "\\", "\n", "\x7f", " ", "$x", "<recording>/x.ibt"]
    strs = [rng.choice(words) + str(index) * rng.randrange(3) for index in range(256)]
    strs_none = [None if index % 9 == 0 else value for index, value in enumerate(strs)]
    columns = [
        floats,
        floats_special,
        with_none,
        ints,
        ints_none,
        bools,
        bools_none,
        strs,
        strs_none,
        [None] * 256,  # no element type: inline
        [None] * 255 + [1.0],  # one float among None: a column
        ints[:255] + [2**63],  # an int beyond i64: inline
        [1] * 128 + [True] * 128,  # int and bool are two types: inline
        [1.0] * 128 + [1] * 128,  # float and int: inline
        floats[:255],  # below the column threshold: inline
        [{"i": index} for index in range(256)],  # not scalars: inline
        [(index,) for index in range(256)],
    ]
    basis = SteeringBasis(kind="road_wheel", status="stated", ratio=14.5, ratio_text="14.5:1", ratio_source="setup")
    return {
        "codec": codec.CODEC_VERSION,
        "scalars": {
            "none": None,
            "true": True,
            "false": False,
            "ints": [0, 1, -1, 2**31, 2**63 - 1, -(2**63)],
            "big_ints": [2**63, -(2**63) - 1, 10**30, -(10**40)],
            "floats": [
                0.0, -0.0, 1.0, -1.5, 0.1, 1 / 3, 5e-324, 2.2250738585072014e-308, 2.225073858507201e-308,
                1.7976931348623157e308, -1.7976931348623157e308, 1e16, 1e15, 1e-5, 1e-4, 1e22, 1e23,
                9007199254740993.0, 0.30000000000000004, 123456789.0, struct.unpack("<f", struct.pack("<f", 0.1))[0],
            ],
            "nonfinite": [math.nan, math.inf, -math.inf],
            "strings": ["", "a", "é", "日本", "🏁", "\x00\x01\x1f", "\"\\/", "\b\f\n\r\t", "\x7f\x80", "  ", "$table", "<recording>/x.ibt"],
        },
        "containers": {
            "list": [1, 2.0, "3", None, [True, []], {}],
            "tuple": (1, 2.0, ("x",), ()),
            "set_ints": {3, 1, 2, -(2**63), 2**63},
            "set_numbers": {2, 0.5, -1, 1e300},
            "set_strs": {"b", "a", "é", "B", "", "🏁"},
            "set_tuples": {(2, "a"), (1, "b"), (1, "a"), (1,)},
            "single_set": {"x"},
            "empty_set": set(),
            "dict_int_keys": {3: "c", 1: "a", 2.5: "f", -(2**70): "big"},
            "dict_dollar_key": {"x": 1, "$f": "not a marker"},
            "dict_none_key": {None: 1},
            "dict_tuple_keys": {(1, 2): [3], ("a",): None},
            "dict_bool_key": {True: "t", False: "f"},
            "empty_dict": {},
            "empty_list": [],
            "empty_tuple": (),
            "dataclass": basis,
            "dataclasses": [SteeringBasis(kind="steering_wheel", status="not_recorded", wheel_angle_recorded=False), basis],
            "nested": {"a": [{"b": (floats[:3], {"c": with_none})}]},
        },
        "references": [TABLE, TABLE.channels, TABLE.channels["Speed"], TABLE.channels["Lap"], list(TABLE.channels["Lap"])],
        "columns": columns,
    }


def write(out: Path) -> list[Path]:
    platform = "windows" if sys.platform == "win32" else "linux"
    folder = out / platform
    folder.mkdir(parents=True, exist_ok=True)
    live = document()
    context = codec.Context(TABLE)
    sink = codec.ColumnSink()
    stored = codec.dumps(codec.encode(live, context, sink))
    inline = codec.dumps(codec.encode(live, context, None))
    (folder / "codec.json").write_text(stored + "\n", encoding="utf-8", newline="\n")
    (folder / "codec.inline.json").write_text(inline + "\n", encoding="utf-8", newline="\n")
    sink.write(folder / "codec.ccs", PRODUCER)
    columns = codec.read_vector_store(folder / "codec.ccs")
    for name, text in (("codec.json", stored), ("codec.inline.json", inline)):
        decoded = codec.decode(json.loads(text), context, columns)
        if not codec.same(decoded, live):
            raise SystemExit(f"self-test: {name} does not decode to the live document")
    return [folder / "codec.json", folder / "codec.inline.json", folder / "codec.ccs"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=CRATE / "tests" / "vectors" / "codec")
    parser.add_argument("--check", action="store_true", help="regenerate into a temporary folder and compare")
    arguments = parser.parse_args(argv)
    if sys.version_info[:2] != (3, 12):
        raise SystemExit(f"the oracle is CPython 3.12; this is {sys.version.split()[0]}")
    if not arguments.check:
        for path in write(arguments.out):
            print(path)
        return 0
    import tempfile

    with tempfile.TemporaryDirectory(prefix="codec-vectors-") as temporary:
        differences = []
        for path in write(Path(temporary)):
            expected = arguments.out / path.relative_to(temporary)
            if not expected.is_file() or expected.read_bytes() != path.read_bytes():
                differences.append(str(expected))
        for line in differences:
            print(f"differs: {line}")
        print(f"check: {'identical' if not differences else f'{len(differences)} difference(s)'}")
        return 1 if differences else 0


if __name__ == "__main__":
    raise SystemExit(main())

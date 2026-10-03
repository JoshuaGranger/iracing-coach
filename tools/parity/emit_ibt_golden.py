#!/usr/bin/env python3
"""Emit a byte-exact golden of the Python .ibt decode for decode parity testing.

A native decoder (the Rust decode stage, port phase 1) must reproduce this file
exactly. Python is the authoritative oracle: this captures the variable
catalogue, the sampling plan, and a content hash of the decoded value matrix
so a native reader can be proven byte-identical, not merely close.
``coach-analysis golden <ibt> [--target-hz N]`` prints the same fields (no
``spot_values``) from the native decode; ``analysis_parity.py corpus``
compares the two on every recording.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "iracing-coach" / "skills" / "analyze-iracing-race" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import ibt_reader  # noqa: E402

# Native little-endian packers matching the SDK element types, used to
# re-serialise each decoded value to the exact bytes it was read from so the
# matrix hash is a true byte-identity check independent of JSON float encoding.
_PACK = {
    1: lambda v: struct.pack("<?", v),
    2: lambda v: struct.pack("<i", v),
    3: lambda v: struct.pack("<I", v),
    4: lambda v: struct.pack("<f", v),
    5: lambda v: struct.pack("<d", v),
}


def _hash_columns(digest, result: dict) -> None:
    samples = result["samples"]
    for var in result["variables"]:
        name = var["name"]
        type_code = var["type_code"]
        count = var["count"]
        column = samples[name]
        digest.update(name.encode("utf-8"))
        digest.update(struct.pack("<iii", type_code, count, len(column)))
        if type_code == 0:  # char array -> decoded string
            for value in column:
                encoded = value.encode("utf-8")
                digest.update(struct.pack("<i", len(encoded)))
                digest.update(encoded)
            continue
        packer = _PACK[type_code]
        for value in column:
            elements = value if isinstance(value, list) else [value]
            for element in elements:
                digest.update(packer(element))


def _matrix_hash(result: dict) -> str:
    digest = hashlib.sha256()
    _hash_columns(digest, result)
    return digest.hexdigest()


# Elements per sample decoded at once. The digest runs over the variables in
# order, so hashing consecutive batches gives the digest of the whole matrix
# while a large recording's array channels (64 cars each) never sit in memory
# together as Python lists.
BATCH_ELEMENTS = 256


def _batches(variables: list) -> list:
    batches, current, size = [], [], 0
    for var in variables:
        if current and size + var["count"] > BATCH_ELEMENTS:
            batches.append(current)
            current, size = [], 0
        current.append(var["name"])
        size += var["count"]
    if current:
        batches.append(current)
    return batches


def _spot(column: list, type_code: int) -> list:
    if not column:
        return []
    picks = sorted({0, len(column) // 2, len(column) - 1})
    out = []
    for i in picks:
        value = column[i]
        if type_code == 4:  # float32 widened: round-trippable repr
            out.append([i, repr(value) if not isinstance(value, list) else [repr(x) for x in value]])
        else:
            out.append([i, value])
    return out


def emit(path: str, target_hz) -> dict:
    catalogue = ibt_reader.scan_ibt(path)["variables"]
    digest = hashlib.sha256()
    first = None
    variables: list = []
    spot: dict = {}
    for names in _batches(catalogue) or [[]]:
        # load_telemetry(channels=None) in batches: the same columns, in catalogue order.
        result = ibt_reader.load_telemetry(path, channels=names if names else None, target_hz=target_hz)
        first = first or result
        _hash_columns(digest, result)
        for v in result["variables"]:
            if len(variables) < 12:
                spot[v["name"]] = _spot(result["samples"][v["name"]], v["type_code"])
            variables.append(v)
        del result
    return {
        "target_hz": target_hz,
        "native_tick_rate_hz": first["native_tick_rate_hz"],
        "sample_rate_hz": first["sample_rate_hz"],
        "source_record_count": first["source_record_count"],
        "sample_count": first["sample_count"],
        "sample_indices": first["sample_indices"],
        "variables": [
            {"name": v["name"], "type_code": v["type_code"], "count": v["count"], "offset": v["offset"], "unit": v.get("unit", "")}
            for v in variables
        ],
        "matrix_sha256": digest.hexdigest(),
        "spot_values": spot,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ibt")
    ap.add_argument("out")
    args = ap.parse_args()
    doc = {
        "source": Path(args.ibt).name,
        "full_rate": emit(args.ibt, None),
        "downsampled_20hz": emit(args.ibt, 20),
    }
    Path(args.out).write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {args.out}: full={doc['full_rate']['sample_count']} samples, 20hz={doc['downsampled_20hz']['sample_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

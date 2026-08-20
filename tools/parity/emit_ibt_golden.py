#!/usr/bin/env python3
"""Emit a byte-exact golden of the Python .ibt decode for C# parity testing.

The C# port under companion-app/src/iRacingCoach.Telemetry must reproduce this
file exactly. Python is the authoritative oracle: this captures the variable
catalogue, the sampling plan, and a content hash of the decoded value matrix
so a C# reader can be proven byte-identical, not merely close.
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


def _matrix_hash(result: dict) -> str:
    digest = hashlib.sha256()
    variables = result["variables"]
    samples = result["samples"]
    for var in variables:
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
    return digest.hexdigest()


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
    result = ibt_reader.load_telemetry(path, channels=None, target_hz=target_hz)
    variables = result["variables"]
    samples = result["samples"]
    spot = {v["name"]: _spot(samples[v["name"]], v["type_code"]) for v in variables[:12]}
    return {
        "target_hz": target_hz,
        "native_tick_rate_hz": result["native_tick_rate_hz"],
        "sample_rate_hz": result["sample_rate_hz"],
        "source_record_count": result["source_record_count"],
        "sample_count": result["sample_count"],
        "sample_indices": result["sample_indices"],
        "variables": [
            {"name": v["name"], "type_code": v["type_code"], "count": v["count"], "offset": v["offset"], "unit": v.get("unit", "")}
            for v in variables
        ],
        "matrix_sha256": _matrix_hash(result),
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
    Path(args.out).write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.out}: full={doc['full_rate']['sample_count']} samples, 20hz={doc['downsampled_20hz']['sample_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Test-only JSON codec for exercising Rust storage and HTTP behavior.

This deliberately does not parse STO. Production encryption, native validation,
and malformed-file tests live with the authoritative Python codec.
"""
import base64
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import sys


def main():
    request = json.load(sys.stdin)
    source = base64.b64decode(request["data_base64"], validate=True)
    setup = json.loads(source)
    if setup.get("test_behavior") == "oversized_stdout":
        Path(request["profile_path"]).with_name("oversized-codec.pid").write_text(str(os.getpid()))
        for _ in range(49):
            sys.stdout.buffer.write(b"x" * (1 << 20))
            sys.stdout.buffer.flush()
        raise SystemExit(0)
    if request["operation"] == "encode":
        changes = request.get("changes", {})
        for name, value in changes.items():
            try:
                valid = isinstance(value, str) and Decimal(value).is_finite()
            except InvalidOperation:
                valid = False
            if name not in setup["settings"] or not valid:
                return {"schema": 1, "ok": False, "error": {
                    "code": "invalid_edit", "message": "Unknown field or invalid native value"
                }}
        changed = bool(changes) or "notes" in request
        setup["settings"].update(changes)
        if "notes" in request:
            setup["notes"] = request["notes"]
        if changed:
            source = json.dumps(setup, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    document = {
        "sto_version": 3,
        "sha256": hashlib.sha256(source).hexdigest(),
        "internal_car_path": "synthetic\\car",
        "notes": setup["notes"],
        "settings": [{
            "name": name, "raw_value": value, "label": name,
            "unit": "kg" if name == "fuelFillToKg" else None,
            "editable": True, "group": "Native"
        } for name, value in setup["settings"].items()],
        "warnings": ["Synthetic storage test; raw native values"],
        "future_optional": setup.get("unknown", {}),
    }
    result = {"schema": 1, "ok": True, "document": document}
    if request["operation"] == "encode":
        result["data_base64"] = base64.b64encode(source).decode("ascii")
    return result


if __name__ == "__main__":
    try:
        result = main()
    except (ValueError, KeyError, TypeError):
        result = {"schema": 1, "ok": False, "error": {
            "code": "invalid_sto", "message": "Synthetic codec could not read these bytes"
        }}
    json.dump(result, sys.stdout, ensure_ascii=False)
    sys.exit(0 if result["ok"] else 2)

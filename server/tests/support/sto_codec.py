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

GARAGE_PROFILE = "synthetic-garage-v1"


def failure(code, message, name=None):
    error = {"code": code, "message": message}
    if name is not None:
        error["details"] = {"name": name}
    return {"schema": 1, "ok": False, "error": error}


def native_number(name, value, garage=False):
    try:
        if not isinstance(value, str):
            raise InvalidOperation
        number = Decimal(value)
        if not number.is_finite():
            raise InvalidOperation
    except InvalidOperation:
        return None, failure("invalid_garage_value" if garage else "invalid_edit", "Use a finite decimal value", name)
    if name == "fBrakeBias":
        if not 0 <= number <= 100:
            return None, failure("garage_out_of_range", "Outside the synthetic garage range", name)
        if number % Decimal("0.5"):
            return None, failure("garage_off_grid", "Off the synthetic adjustment grid", name)
    return number, None


def main():
    request = json.load(sys.stdin)
    if Path(request["profile_path"]).with_name("codec-unavailable").exists():
        return failure("material_unavailable", "Synthetic codec material is unavailable")
    source = base64.b64decode(request["data_base64"], validate=True)
    setup = json.loads(source)
    if setup.get("test_behavior") == "oversized_stdout":
        Path(request["profile_path"]).with_name("oversized-codec.pid").write_text(str(os.getpid()))
        for _ in range(49):
            sys.stdout.buffer.write(b"x" * (1 << 20))
            sys.stdout.buffer.flush()
        raise SystemExit(0)
    if request["operation"] == "encode":
        changes = dict(request.get("changes", {}))
        if "garage_changes" in request:
            if request.get("garage_profile_id") != GARAGE_PROFILE:
                return failure("garage_profile_mismatch", "The synthetic garage profile changed")
            for name, raw in request["garage_changes"].items():
                if name in changes:
                    return failure("ambiguous_change", "A field cannot use both representations", name)
                if name != "fBrakeBias" or name not in setup["settings"]:
                    return failure("garage_unmapped_field", "This native field has no synthetic garage mapping", name)
                number, error = native_number(name, raw, garage=True)
                if error:
                    return error
                if number != Decimal(setup["settings"][name]):
                    changes[name] = raw
        for name, value in changes.items():
            if name not in setup["settings"]:
                return failure("invalid_edit", "Unknown native field", name)
            _, error = native_number(name, value)
            if error:
                return error
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
        "descriptor_version": 2,
        "garage_profile_id": GARAGE_PROFILE,
        "garage_profile_label": "Synthetic storage-test garage",
    }
    for field in document["settings"]:
        if field["name"] == "fBrakeBias":
            field["garage"] = {"label": "Front brake bias", "value": field["raw_value"],
                               "unit": "%", "editable": True, "min": "0", "max": "100",
                               "step": "0.5", "step_base": "0"}
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

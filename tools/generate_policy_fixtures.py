#!/usr/bin/env python3
"""Generate the conformance fixtures a second implementation must reproduce.

These are test vectors, not contracts, so they live under `test-data/` and are
generated here rather than by `export_contracts.py`. Keeping them separate is
deliberate: the registry gate sandboxes `contracts/`, `tools/` and the backend
sources and checks that the committed contracts regenerate byte-identically
inside that sandbox. Emitting a `test-data/` artifact from the same entry point
would make that gate depend on a tree it does not copy.

Drift is still impossible: the backend suite compares each committed fixture
against a freshly generated one, so a policy change with a stale fixture fails
there. This tool is how you make it non-stale.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = WORKSPACE_ROOT / "iracing-coach" / "skills" / "analyze-iracing-race" / "scripts"
FIXTURE_ROOT = WORKSPACE_ROOT / "test-data"

sys.path.insert(0, str(SCRIPT_ROOT))

import live_truth  # noqa: E402
import starting_tune  # noqa: E402


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def generated_values() -> dict[Path, Any]:
    return {
        FIXTURE_ROOT / "live-truth-conformance-v1.json": live_truth.conformance_vectors(),
        FIXTURE_ROOT / "starting-tune-matrix-v1.json": {
            "contract_version": starting_tune.STARTING_TUNE_CONTRACT_VERSION,
            "purposes": list(starting_tune.PURPOSES),
            "source_shapes": list(starting_tune.SOURCE_SHAPES),
            "rows": starting_tune.capability_matrix(),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail when committed fixtures differ instead of writing them.",
    )
    args = parser.parse_args()
    failures: list[str] = []
    for path, value in generated_values().items():
        expected = _json_text(value)
        if args.check:
            actual = path.read_text(encoding="utf-8") if path.is_file() else None
            if actual != expected:
                failures.append(str(path.relative_to(WORKSPACE_ROOT)))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(expected, encoding="utf-8")
    if failures:
        print(json.dumps({"ok": False, "out_of_date_fixtures": failures}, indent=2))
        return 1
    print(json.dumps({"ok": True, "checked": bool(args.check)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

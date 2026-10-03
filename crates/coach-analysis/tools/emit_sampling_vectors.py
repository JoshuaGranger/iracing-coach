#!/usr/bin/env python3
"""Write tests/vectors/sampling.json from the Python oracle's sampling plan.

Each case runs ``ibt_reader._sampling_plan`` and ``ibt_reader._iter_sample_indices``
(the functions ``load_telemetry`` uses) and records the step and output rate as
``repr`` text (exact) and the indices as a count and a SHA-256 over u32 LE.
A target the plan rejects has ``step`` null; a walk that raises (an infinite
step: ``int(inf)`` is ``OverflowError``) records ``error`` as
``"<type>: <message>"`` instead of the indices.
Regenerate with the bundled Python 3.12:

    python -X utf8 crates/coach-analysis/tools/emit_sampling_vectors.py
"""

from __future__ import annotations

import hashlib
import json
import struct
import sys
from pathlib import Path

CRATE = Path(__file__).resolve().parents[1]
SCRIPTS = CRATE.parents[1] / "iracing-coach" / "skills" / "analyze-iracing-race" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import ibt_reader  # noqa: E402

TICK_RATES = (1, 7, 60, 360, 10_000)
TARGETS = (
    None, "1.0", "3.0", "7.0", "13.3", "20.0", "29.97", "30.000000000000004", "45.5",
    "59.94", "59.99999999999999", "60.0", "0.1", "1e-05", "123.456", "0.0", "-5.0",
    # Huge finite step (index 0 only) and steps that overflow to inf (review finding 2).
    "1e-300", "1e-307", "5e-324",
)
RECORD_COUNTS = (0, 1, 2, 100, 12_345, 216_001)


def main() -> int:
    cases = []
    for tick in TICK_RATES:
        for target in TARGETS:
            for records in RECORD_COUNTS:
                case = {"tick_rate": tick, "target_hz": target, "record_count": records}
                try:
                    step, rate = ibt_reader._sampling_plan(tick, None if target is None else float(target))
                except ValueError:
                    case["step"] = None
                    cases.append(case)
                    continue
                case.update(step=repr(step), output_rate=repr(rate))
                try:
                    indices = list(ibt_reader._iter_sample_indices(records, step))
                except OverflowError as exc:
                    case["error"] = f"{type(exc).__name__}: {exc}"
                    cases.append(case)
                    continue
                case.update(
                    sample_count=len(indices),
                    indices_sha256=hashlib.sha256(struct.pack(f"<{len(indices)}I", *indices)).hexdigest(),
                )
                cases.append(case)
    out = CRATE / "tests" / "vectors" / "sampling.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    document = {"python": sys.version.split()[0], "source": "ibt_reader._sampling_plan, _iter_sample_indices", "cases": cases}
    out.write_text(json.dumps(document, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {out}: {len(cases)} cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

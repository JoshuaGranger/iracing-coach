"""WS-10A durable read-modify-write reproduction. NOT a discovered test.

Reproduces and reports the current cross-process lost-update behavior at
N=8 and N=16, forward and reverse release order, against today's unmodified
primitive shape.

This probe is OPEN through WS-10A and remains open after atomic containment
lands. Bounded retry around os.replace cannot prevent two writers that read
the same prior value from replacing each other, so the loss reported here is
NOT closed by WS-10A. It is prerequisite evidence for the separately
reviewed F2 union-semantics work.

Exit code reports whether the REPRODUCTION EXECUTED, never whether the union
survived. Today's data loss is a measurement, not a desired product contract.

    exit 0  - every cell ran with clean stages and produced a stable result
    exit 1  - harness failure: a stage did not hold, a worker hung, or a
              worker exited non-zero

Run:
    python -X utf8 iracing-coach/tests/probe_ws10a_durable_rmw.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ws10a_support as support

SIZES = (8, 16)
ORDERS = ("forward", "reverse")


def run() -> dict:
    cells = []
    harness_ok = True

    with support.sandbox("ws10a-durable-rmw") as root:
        for n in SIZES:
            for order in ORDERS:
                outcome = support.run_staged_rmw(n, order, root / f"u-{n}-{order}.json")

                # One shared predicate, also used by the discovered
                # regression, so weakening it fails a test rather than
                # silently widening what counts as clean.
                stages_clean = support.stages_clean(outcome)
                harness_ok = harness_ok and stages_clean

                cells.append(
                    {
                        "n": n,
                        "order": order,
                        "start_method": outcome["start_method"],
                        "stages_clean": stages_clean,
                        "all_read_initial": outcome["all_read_initial"],
                        "acknowledged_in_order": outcome["acknowledged_in_order"],
                        "acknowledged_clean": outcome["acknowledged_clean"],
                        "hung": outcome["hung"],
                        "bad_exits": outcome["bad_exits"],
                        "expected_union_size": len(outcome["expected_union"]),
                        "survived": outcome["survived"],
                        "observed_loss": outcome["lost"],
                        "predicted_survivor": outcome["predicted_survivor"],
                        "sole_survivor_is_last_released": outcome[
                            "sole_survivor_is_last_released"
                        ],
                    }
                )

    return {
        "schema": "ws10a-durable-rmw-v1",
        "probe": "durable-rmw",
        "status": "OPEN",
        "closes": None,
        "note": (
            "Reproduction only. WS-10A atomic containment does not and cannot "
            "close this loss; union semantics remain Open until F2."
        ),
        "harness_ok": harness_ok,
        "cells": cells,
    }


def main() -> int:
    report = run()
    print(json.dumps(report, indent=2, sort_keys=True))

    print("\n--- reproduction summary (measurement, not a desired contract) ---")
    for cell in report["cells"]:
        print(
            f"  N={cell['n']:>2} {cell['order']:>7}: "
            f"stages_clean={cell['stages_clean']} "
            f"observed_loss={cell['observed_loss']:>2}/"
            f"{cell['expected_union_size']} "
            f"survivor={cell['survived']} "
            f"predicted={cell['predicted_survivor']} "
            f"match={cell['sole_survivor_is_last_released']}"
        )

    if not report["harness_ok"]:
        print("\nHARNESS FAILURE: a stage did not hold; the reproduction is not valid.")
        return 1

    print("\nReproduction executed. Union loss remains OPEN until F2.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

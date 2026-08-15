#!/usr/bin/env python3
"""Synthetic target used only by the tools/dev tests.

Writes the file named by ``G0_DEV_MARKER`` as its first action. A negative test
asserts the marker is absent, which proves the target was never entered. An exit
code alone cannot prove that, because a refusal and a failing target can share
one.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> int:
    marker = os.environ.get("G0_DEV_MARKER")
    if not marker:
        print("G0_DEV_MARKER is not set", file=sys.stderr)
        return 2
    path = Path(marker)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("entered\n", encoding="utf-8")
    print("marker target entered", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Run ``mcp_server.py`` as the worker runs it and record every native stage call.

Usage: ``python -X utf8 -u worker_trace.py <trace.jsonl>`` with the worker's own
environment and standard input. The server runs as ``__main__`` with the
scripts folder as ``sys.path[0]``, exactly as ``python mcp_server.py`` would;
the only additions are a wrapper on ``native_stages.load_telemetry`` that
appends one JSON line per call to the trace (the recording's file name and the
exception type, null on success) and the kernel-call counters
(``native_stages.install_call_counters``), written as one
``{"kernel_calls": ...}`` line when a process that counted a call ends. The seam has no Python
fallback, so a successful call is a native decode and a counted kernel call
ran in ``coach-analysis kernels``.

``analysis_parity.py capture-worker`` starts every worker call through this
shim on both sides; the corpus gate then requires no native call on the oracle
side, one successful decode per recording on the native side and, with
``kernels``, one kernel call per oracle entry-point call, count for count, so a
worker launch that loses ``IRACING_COACH_NATIVE_STAGES`` cannot compare Python
with Python.
"""

from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parents[2] / "iracing-coach" / "skills" / "analyze-iracing-race" / "scripts"


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: worker_trace.py <trace.jsonl>")
    trace = Path(sys.argv[1])
    server = SCRIPTS / "mcp_server.py"
    sys.argv = [str(server)]
    sys.path[0] = str(SCRIPTS)
    import native_stages  # noqa: PLC0415 - after the path is the server's

    original = native_stages.load_telemetry

    def record(path: Any, error: str | None) -> None:
        with trace.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"source": Path(path).name, "error": error}) + "\n")

    def traced(path: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            result = original(path, *args, **kwargs)
        except BaseException as exc:
            record(path, type(exc).__name__)
            raise
        record(path, None)
        return result

    native_stages.load_telemetry = traced
    counts, _uninstall = native_stages.install_call_counters()
    try:
        runpy.run_path(str(server), run_name="__main__")
    finally:
        if counts["native"] or counts["oracle"]:  # a process that analysed nothing adds nothing
            with trace.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"kernel_calls": counts}) + "\n")


if __name__ == "__main__":
    main()

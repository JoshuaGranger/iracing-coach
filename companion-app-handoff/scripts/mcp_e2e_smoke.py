#!/usr/bin/env python3
"""Run a real stdio MCP dashboard + analysis against the sanitized synthetic IBT."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


HANDOFF_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = HANDOFF_ROOT.parent
SERVER_PATH = (
    WORKSPACE_ROOT
    / "iracing-coach"
    / "skills"
    / "analyze-iracing-race"
    / "scripts"
    / "mcp_server.py"
)
FIXTURE_ROOT = HANDOFF_ROOT / "fixtures" / "ibt"


def _domain(response: dict[str, Any], request_id: int) -> dict[str, Any]:
    if response.get("id") != request_id:
        raise RuntimeError(f"Expected MCP response id {request_id}, got {response.get('id')}")
    if response.get("error"):
        raise RuntimeError(f"MCP protocol error: {response['error']}")
    result = response.get("result")
    if not isinstance(result, dict) or result.get("isError") is True:
        raise RuntimeError(f"MCP tool call failed: {result}")
    content = result.get("content")
    if not isinstance(content, list) or not content or not isinstance(content[0], dict):
        raise RuntimeError("MCP tool result has no content[0]")
    text = content[0].get("text")
    if not isinstance(text, str):
        raise RuntimeError("MCP tool result content[0].text is not JSON text")
    value = json.loads(text)
    if not isinstance(value, dict):
        raise RuntimeError("MCP domain result is not an object")
    return value


def run() -> dict[str, Any]:
    if not SERVER_PATH.is_file():
        raise FileNotFoundError(SERVER_PATH)
    fixture = FIXTURE_ROOT / "synthetic-race.ibt"
    if not fixture.is_file():
        raise FileNotFoundError(fixture)
    requests = (
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18"},
        },
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "iracing_companion_dashboard",
                "arguments": {"limit": 20},
            },
        },
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "analyze_iracing_race",
                "arguments": {"selector": "latest", "target_hz": 20},
            },
        },
    )
    payload = "".join(json.dumps(item, separators=(",", ":")) + "\n" for item in requests)
    with tempfile.TemporaryDirectory(prefix="iracing-coach-e2e-") as archive_root:
        environment = os.environ.copy()
        environment.update(
            {
                "IRACING_COACH_IRACING_ROOT": str(FIXTURE_ROOT.resolve()),
                "IRACING_COACH_DATA": str(Path(archive_root).resolve()),
                "IRACING_COACH_PYTHON": sys.executable,
                "PYTHONUTF8": "1",
            }
        )
        completed = subprocess.run(
            [sys.executable, "-X", "utf8", "-u", str(SERVER_PATH)],
            cwd=WORKSPACE_ROOT,
            env=environment,
            input=payload,
            text=True,
            encoding="utf-8",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=60,
        )
    if completed.returncode != 0:
        raise RuntimeError(
            f"MCP process exited {completed.returncode}: {completed.stderr.strip()}"
        )
    responses = [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]
    if len(responses) != 3:
        raise RuntimeError(
            f"Expected 3 MCP responses, got {len(responses)}; stderr={completed.stderr.strip()}"
        )
    initialized = responses[0].get("result") or {}
    dashboard = _domain(responses[1], 2)
    analysis = _domain(responses[2], 3)
    if initialized.get("protocolVersion") != "2025-06-18":
        raise RuntimeError("MCP initialize negotiated an unexpected protocol")
    if dashboard.get("ok") is not True or dashboard.get("race_count") != 1:
        raise RuntimeError(f"Synthetic dashboard result is unexpected: {dashboard}")
    if analysis.get("ok") is not True or not isinstance(analysis.get("race_card"), dict):
        raise RuntimeError(f"Synthetic analysis result is unexpected: {analysis}")
    selection = analysis.get("selection") or {}
    if selection.get("subsession_id") != 8001:
        raise RuntimeError(f"Synthetic analysis selected the wrong session: {selection}")
    timing = analysis.get("timing") or {}
    return {
        "ok": True,
        "protocol_version": initialized.get("protocolVersion"),
        "dashboard_race_count": dashboard.get("race_count"),
        "analysis_id": analysis.get("analysis_id"),
        "subsession_id": selection.get("subsession_id"),
        "data_quality": (analysis.get("data_quality") or {}).get("confidence"),
        "backend_elapsed_ms": timing.get("total_ms"),
    }


def main() -> int:
    try:
        print(json.dumps(run(), ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {"ok": False, "error": type(exc).__name__, "message": str(exc)},
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

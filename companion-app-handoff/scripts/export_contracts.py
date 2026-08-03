#!/usr/bin/env python3
"""Export deterministic companion compatibility and MCP input contracts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


HANDOFF_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = HANDOFF_ROOT.parent
PLUGIN_ROOT = WORKSPACE_ROOT / "iracing-coach"
SCRIPT_ROOT = PLUGIN_ROOT / "skills" / "analyze-iracing-race" / "scripts"
CONTRACT_ROOT = HANDOFF_ROOT / "contracts"

sys.path.insert(0, str(SCRIPT_ROOT))

import analysis_engine  # noqa: E402
import groove_analysis  # noqa: E402
import mcp_server  # noqa: E402
import race_card  # noqa: E402
import setup_catalog  # noqa: E402
import storage  # noqa: E402
import tuning_engine  # noqa: E402


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _plugin_manifest() -> dict[str, Any]:
    path = PLUGIN_ROOT / ".codex-plugin" / "plugin.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Plugin manifest is not an object: {path}")
    return value


def exported_values() -> dict[Path, Any]:
    plugin = _plugin_manifest()
    compatibility = {
        "handoff_contract_version": 1,
        "plugin": {
            "name": plugin.get("name"),
            "version": plugin.get("version"),
        },
        "backend": {
            "mcp_server_name": mcp_server.SERVER_NAME,
            "mcp_server_version": mcp_server.SERVER_VERSION,
            "supported_mcp_protocol_versions": list(
                mcp_server.SUPPORTED_PROTOCOL_VERSIONS
            ),
            "transport": "stdio-jsonl-utf8",
            "tool_count": len(mcp_server.TOOLS),
            "dashboard_contract_version": 1,
            "analyze_result_contract_version": 1,
            "race_card_contract_version": race_card.CONTRACT_VERSION,
            "analysis_schema_version": analysis_engine.ANALYSIS_SCHEMA_VERSION,
            "analysis_profile_version": analysis_engine.ANALYSIS_PROFILE_VERSION,
            "archive_schema_version": storage.SCHEMA_VERSION,
            "groove_schema_version": groove_analysis.SCHEMA_VERSION,
            "setup_catalog_schema_version": setup_catalog.SCHEMA_VERSION,
            "tuning_schema_version": tuning_engine.TUNING_SCHEMA_VERSION,
        },
        "runtime": {
            "operating_system": "Windows 10/11 x64",
            "python_minimum": "3.10",
            "python_runtime_dependencies": [],
            "powershell_required_for": [
                "MCP launcher",
                "Garage61 DPAPI credential configuration",
            ],
        },
        "environment": {
            "IRACING_COACH_PYTHON": "packaged Python executable",
            "IRACING_COACH_IRACING_ROOT": "trusted local iRacing Documents root",
            "IRACING_COACH_INSTALL_ROOT": "optional read-only iRacing installation root",
            "IRACING_COACH_DATA": "trusted backend-owned archive root",
            "PYTHONUTF8": "1",
        },
        "ai": {
            "required_for_deterministic_workflows": False,
            "preferred_adapter": "codex-app-server-stdio",
            "app_server_schema_policy": "generate and pin from packaged Codex version",
        },
        "ui": {
            "design_system_version": 1,
            "default_theme": "gentle-charcoal-dark",
            "application_language": "C#",
            "application_runtime": ".NET 10",
            "native_host": "WPF",
            "view_layer": "Blazor Hybrid",
            "webview_runtime_policy_required": True,
        },
    }
    tools = {
        "snapshot_version": 1,
        "server_name": mcp_server.SERVER_NAME,
        "server_version": mcp_server.SERVER_VERSION,
        "transport": "newline-delimited UTF-8 JSON-RPC 2.0",
        "tools_call_payload": "Parse result.content[0].text as JSON and inspect isError",
        "tools": mcp_server.TOOLS,
    }
    return {
        CONTRACT_ROOT / "compatibility.json": compatibility,
        CONTRACT_ROOT / "mcp-tools.v1.json": tools,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail when checked-in exports differ instead of writing them.",
    )
    args = parser.parse_args()
    failures: list[str] = []
    for path, value in exported_values().items():
        expected = _json_text(value)
        if args.check:
            actual = path.read_text(encoding="utf-8") if path.is_file() else None
            if actual != expected:
                failures.append(str(path.relative_to(WORKSPACE_ROOT)))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(expected, encoding="utf-8")
    if failures:
        print(
            json.dumps(
                {"ok": False, "out_of_date_contracts": failures},
                indent=2,
            )
        )
        return 1
    print(json.dumps({"ok": True, "checked": bool(args.check)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

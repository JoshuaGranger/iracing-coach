#!/usr/bin/env python3
"""Export deterministic companion compatibility and MCP input contracts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = WORKSPACE_ROOT / "iracing-coach"
SCRIPT_ROOT = PLUGIN_ROOT / "skills" / "analyze-iracing-race" / "scripts"
CONTRACT_ROOT = WORKSPACE_ROOT / "contracts"

sys.path.insert(0, str(SCRIPT_ROOT))

import analysis_engine  # noqa: E402
import groove_analysis  # noqa: E402
import mcp_server  # noqa: E402
import race_card  # noqa: E402
import setup_catalog  # noqa: E402
import storage  # noqa: E402
import tuning_engine  # noqa: E402
import workflow  # noqa: E402


COMPATIBILITY_SOURCES_PATH = CONTRACT_ROOT / "compatibility-sources.json"

_JSON_TYPE_BY_FIELD_KIND = {
    "object": {"type": "object"},
    "array": {"type": "array"},
    "string-or-null": {"type": ["string", "null"]},
}


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _compatibility_sources() -> dict[str, Any]:
    value = json.loads(COMPATIBILITY_SOURCES_PATH.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Compatibility sources is not an object: {COMPATIBILITY_SOURCES_PATH}")
    return value


def _companion(field: str) -> int:
    """Read one declared companion-archive value, refusing a malformed record.

    The companion store's authority is C# source rather than a Python constant,
    so the value is declared here and bound to that source by test. Failing
    loudly beats generating a plausible number from an incomplete record.
    """
    record = _compatibility_sources().get("companion_durable_archive")
    if not isinstance(record, dict) or field not in record:
        raise ValueError(f"Compatibility sources lacks companion_durable_archive.{field}")
    entry = record[field]
    if not isinstance(entry, dict) or "value" not in entry or "authority" not in entry:
        raise ValueError(f"companion_durable_archive.{field} lacks value/authority")
    value = entry["value"]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"companion_durable_archive.{field}.value must be an integer")
    return value


def _analysis_view_schema() -> dict[str, Any]:
    """Generate the envelope contract from the producer's sole field authority."""
    properties: dict[str, Any] = {
        "schema_version": {"const": workflow.ANALYSIS_VIEW_SCHEMA_VERSION, "type": "integer"}
    }
    required = ["schema_version"]
    for name, kind, _default in workflow.ANALYSIS_VIEW_FIELDS:
        if kind not in _JSON_TYPE_BY_FIELD_KIND:
            raise ValueError(f"Unknown analysis_view field kind for {name!r}: {kind!r}")
        properties[name] = dict(_JSON_TYPE_BY_FIELD_KIND[kind])
        required.append(name)
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "analysis_view envelope",
        "description": (
            "The analysis_view envelope emitted by workflow.py. Every field is always "
            "present; an empty object or array means the section has no content, not "
            "that it was omitted. Unknown optional fields are accepted so a newer "
            "producer remains readable."
        ),
        "type": "object",
        "additionalProperties": True,
        "required": required,
        "properties": properties,
    }


def _plugin_manifest() -> dict[str, Any]:
    path = PLUGIN_ROOT / ".codex-plugin" / "plugin.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Plugin manifest is not an object: {path}")
    return value


def exported_values() -> dict[Path, Any]:
    plugin = _plugin_manifest()
    compatibility = {
        "repository_contract_version": 1,
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
            "analysis_view_envelope_version": workflow.ANALYSIS_VIEW_SCHEMA_VERSION,
            # Two independent stores previously shared the single ambiguous key
            # `archive_schema_version`. The backend range is derived here; the
            # companion range is declared in compatibility-sources.json because
            # its authority is C# source, and a test binds it there.
            "backend_archive_writer_version": storage.SCHEMA_VERSION,
            "backend_archive_min_readable_version": storage.SCHEMA_VERSION,
            "backend_archive_max_readable_version": storage.SCHEMA_VERSION,
            "companion_durable_archive_writer_version": _companion("writer_version"),
            "companion_durable_archive_min_readable_version": _companion("min_readable_version"),
            "companion_durable_archive_max_readable_version": _companion("max_readable_version"),
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
            "default_theme": "mineral-glass-dark",
            "application_language": "C#",
            "application_runtime": ".NET 10",
            "native_host": "WPF",
            "view_layer": "Blazor Hybrid",
            "webview_runtime_policy_required": True,
        },
        # JSON carries no comments, so the alias records which store the retained
        # ambiguous key means, and when it goes away, in a form tests can check.
        "legacy_aliases": _compatibility_sources()["legacy_aliases"],
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
        CONTRACT_ROOT / "analysis-view-v1.schema.json": _analysis_view_schema(),
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

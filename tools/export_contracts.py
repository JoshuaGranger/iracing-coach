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
import artifact_identity  # noqa: E402
import evidence_records  # noqa: E402
import groove_analysis  # noqa: E402
import live_truth  # noqa: E402
import mcp_server  # noqa: E402
import race_card  # noqa: E402
import race_plan_decision  # noqa: E402
import setup_catalog  # noqa: E402
import starting_tune  # noqa: E402
import storage  # noqa: E402
import tuning_engine  # noqa: E402
import workflow  # noqa: E402


COMPATIBILITY_SOURCES_PATH = CONTRACT_ROOT / "compatibility-sources.json"

# The interpreter the server runs the worker with and the oracle runs on
# (owner decisions 2026-10-02, D2 ruling: 3.12 for compensated `sum()`).
# `python_minimum` stays the source floor the suite still supports.
WORKER_PYTHON = "3.12"

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


DURABLE_ARCHIVE_SOURCE = "companion-app/src/iRacingCoach.Coordinator/DurableArchive.cs"
DURABLE_ARCHIVE_SYMBOL = "DurableArchiveService.CurrentSchemaVersion"
RETIRED_SYMBOL_AUTHORITY = "retired-csharp-symbol"

# Exact expectations, not shapes. Checking that a symbol string is non-empty
# accepts `Bogus.CurrentVersion`; checking that a value is in range accepts a
# policy floor of 1 where 0 is the accepted value. Each field is pinned to the
# literal metadata and to its exact value. The writer and maximum were bound to
# the C# constant while the desktop app existed; it was retired on 2026-10-02
# (owner decision D13), so they are frozen at the constant's last value and the
# C# source is no longer read.
_COMPANION_AUTHORITY_CONTRACT = {
    "writer_version": {
        "authority": RETIRED_SYMBOL_AUTHORITY,
        "symbol": DURABLE_ARCHIVE_SYMBOL,
        "source": DURABLE_ARCHIVE_SOURCE,
        "value": 1,
    },
    "max_readable_version": {
        "authority": RETIRED_SYMBOL_AUTHORITY,
        "symbol": DURABLE_ARCHIVE_SYMBOL,
        "source": DURABLE_ARCHIVE_SOURCE,
        "value": 1,
    },
    "min_readable_version": {
        "authority": "declared-policy",
        "symbol": None,
        "source": None,
        "value": 0,
    },
}


def _companion_entry(record: dict[str, Any], field: str) -> int:
    """Validate one declared companion field and return its value.

    Checking only that a value is an integer is not enough. A declaration whose
    minimum exceeds its maximum is a self-contradictory compatibility range, and
    generating it would publish a claim no reader could satisfy. Every part of
    the accepted per-field authority contract is enforced here so a malformed
    record cannot become a generated artifact.
    """
    if field not in record:
        raise ValueError(f"Compatibility sources lacks companion_durable_archive.{field}")
    entry = record[field]
    if not isinstance(entry, dict):
        raise ValueError(f"companion_durable_archive.{field} is not an object")
    for required in ("value", "authority", "symbol", "source", "derivation"):
        if required not in entry:
            raise ValueError(f"companion_durable_archive.{field} lacks {required!r}")

    value = entry["value"]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"companion_durable_archive.{field}.value must be a JSON integer")
    if value < 0:
        raise ValueError(f"companion_durable_archive.{field}.value must not be negative")

    contract = _COMPANION_AUTHORITY_CONTRACT[field]
    for key in ("authority", "symbol", "source"):
        if entry[key] != contract[key]:
            raise ValueError(
                f"companion_durable_archive.{field}.{key} must be exactly {contract[key]!r}, "
                f"got {entry[key]!r}"
            )

    if value != contract["value"]:
        origin = (
            f" ({contract['symbol']} when the desktop app was retired)"
            if contract["authority"] == RETIRED_SYMBOL_AUTHORITY
            else ""
        )
        raise ValueError(
            f"companion_durable_archive.{field}.value must be exactly {contract['value']}{origin}, "
            f"got {value}"
        )
    if contract["authority"] == RETIRED_SYMBOL_AUTHORITY:
        if not entry.get("retired"):
            raise ValueError(f"companion_durable_archive.{field} lacks 'retired'")
    else:
        for required in ("enforced_by", "current_behavior"):
            if not entry.get(required):
                raise ValueError(f"companion_durable_archive.{field} lacks {required!r}")
        if entry["enforced_by"] != "codex-consumer-phase":
            raise ValueError(
                f"companion_durable_archive.{field}.enforced_by must name the consumer phase"
            )
    return value


def _companion_range() -> dict[str, int]:
    """Return the companion range, refusing any self-contradictory declaration."""
    record = _compatibility_sources().get("companion_durable_archive")
    if not isinstance(record, dict):
        raise ValueError("Compatibility sources lacks companion_durable_archive")
    values = {field: _companion_entry(record, field) for field in _COMPANION_AUTHORITY_CONTRACT}
    minimum = values["min_readable_version"]
    writer = values["writer_version"]
    maximum = values["max_readable_version"]
    if not minimum <= writer <= maximum:
        raise ValueError(
            "companion_durable_archive range is self-contradictory: "
            f"min {minimum} <= writer {writer} <= max {maximum} does not hold"
        )
    return values


def _companion(field: str) -> int:
    return _companion_range()[field]


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


def _race_plan_decision_schema() -> dict[str, Any]:
    """Generate the fuel decision contract from the producer's own constants.

    The status list and the version come from `race_plan_decision`, so a status
    added there cannot be missing here, and a consumer generated from this file
    cannot enumerate a set the producer no longer emits.
    """
    number_or_null = {"type": ["number", "null"]}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "race plan decision",
        "description": (
            "The single authoritative fuel plan. `all_green_range_laps` is exact "
            "and unrounded: a consumer displays it, and never re-derives "
            "`minimum_stops` from it or from any rounded projection of it. "
            "`no_stop_language_permitted` is the only permission to state that a "
            "race needs no stop. When `status` is not `usable` the decided "
            "fields are null and no plan may be stated. "
            "Presence of this record is decided by key membership: a reader that "
            "finds the key, including with a null value, must never fall back to "
            "a legacy rounded projection. "
            "Every fuel scalar has a domain a reader must enforce: "
            "`green_burn_l_per_lap`, `maximum_start_fuel_l`, `usable_fuel_l`, "
            "`scheduled_laps` and `all_green_range_laps` are positive when "
            "present, and `reserve_fuel_l` is never negative. "
            "Three identities also hold whenever their inputs are all present, "
            "and a record that breaks one is refused rather than normalized: "
            "`reserve_fuel_l` equals `green_burn_l_per_lap` times "
            "`reserve_green_laps`; `usable_fuel_l` equals `maximum_start_fuel_l` "
            "minus `reserve_fuel_l`; and `all_green_range_laps` equals "
            "`usable_fuel_l` divided by `green_burn_l_per_lap`. "
            "The domains and the identities are stated here rather than encoded "
            "because this repository's bounded validator supports no numeric or "
            "cross-field keywords; only `reserve_green_laps` is machine-pinned, "
            "by `const`."
        ),
        "type": "object",
        "additionalProperties": True,
        "required": [
            "all_green_range_laps",
            "caution_scenario",
            "decision_version",
            "equal_stint_pit_targets",
            "final_stint_margin_laps",
            "minimum_stops",
            "no_stop_language_permitted",
            "re_decidable",
            "reserve_green_laps",
            "scheduled_laps",
            "status",
            "stints",
        ],
        "properties": {
            "all_green_range_laps": number_or_null,
            "assumptions": {"type": "array", "items": {"type": "string"}},
            "caution_scenario": {"type": ["object", "null"]},
            "classification": {"type": "string"},
            "decision_version": {
                "const": race_plan_decision.RACE_PLAN_DECISION_VERSION,
                "type": "integer",
            },
            "equal_stint_pit_targets": {"type": "array", "items": {"type": "number"}},
            "final_stint_margin_laps": number_or_null,
            "green_burn_l_per_lap": number_or_null,
            "limitations": {"type": "array", "items": {"type": "string"}},
            "maximum_start_fuel_l": number_or_null,
            "minimum_stops": {"type": ["integer", "null"]},
            "no_stop_language_permitted": {"type": "boolean"},
            "re_decidable": {"type": "boolean"},
            "reserve_fuel_l": number_or_null,
            # The operational reserve is a definitional floor, not a tunable, so
            # the contract pins it rather than describing its type.
            "reserve_green_laps": {
                "const": race_plan_decision.RESERVE_GREEN_LAPS,
                "type": "number",
            },
            "scheduled_laps": number_or_null,
            "status": {"enum": list(race_plan_decision.PLAN_STATUSES), "type": "string"},
            "stints": {"type": ["integer", "null"]},
            # Additive: all-green fuel window per decided stop. Empty when no
            # stop is decided. A fuel window, never a tire or caution strategy.
            "stop_windows": {"type": "array", "items": {"type": "object"}},
            "usable_fuel_l": number_or_null,
        },
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
            "race_plan_decision_version": race_plan_decision.RACE_PLAN_DECISION_VERSION,
            "live_truth_policy_version": live_truth.LIVE_TRUTH_POLICY_VERSION,
            "starting_tune_contract_version": starting_tune.STARTING_TUNE_CONTRACT_VERSION,
            "evidence_record_version": evidence_records.EVIDENCE_RECORD_VERSION,
            "artifact_identity_version": artifact_identity.ARTIFACT_IDENTITY_VERSION,
            # Two independent stores previously shared the single ambiguous key
            # `archive_schema_version`. The backend range is derived here; the
            # companion range is declared in compatibility-sources.json because
            # its authority was C# source (retired, frozen at its last value).
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
        # The rewrite (owner decisions 2026-10-02, architecture.md sections 2
        # and 8): a Rust collector on the sim PC, a Rust server in a Linux
        # container on the mini PC that runs this backend as its Python 3.12
        # worker, and a TypeScript single-page app. Windows runs the same
        # backend as the parity oracle and as the Codex plugin.
        "runtime": {
            "python_minimum": "3.10",
            "python_runtime_dependencies": [],
            "worker": {
                "python": WORKER_PYTHON,
                "operating_system": "Linux x64 (python:3.12-slim-bookworm image, mini PC)",
                "launched_by": "coach-server, one process per job (worker-interface.md section 1)",
            },
            "oracle": {
                "python": WORKER_PYTHON,
                "operating_system": "Windows 10/11 x64",
            },
            "powershell_required_for": [
                "Codex plugin MCP launcher on Windows",
                "Garage61 DPAPI credential configuration on Windows",
            ],
        },
        "application": {
            "collector": "coach-collector: Rust, Windows x64 sim PC, no .NET and no WebView",
            "server": "coach-server: Rust, Linux x64 container on the mini PC",
            "worker": "this backend: Python 3.12, standard library only, inside the server container",
            "web": "TypeScript single-page app served by coach-server",
        },
        "environment": {
            "IRACING_COACH_PYTHON": "packaged Python executable",
            "IRACING_COACH_IRACING_ROOT": "trusted local iRacing Documents root",
            "IRACING_COACH_INSTALL_ROOT": "optional read-only iRacing installation root",
            "IRACING_COACH_DATA": "trusted backend-owned archive root",
            "IRACING_COACH_RAW_ARCHIVE": "unset (copy raw IBTs into the archive) or 'external' (raw recordings are kept by the service; worker-interface.md section 5)",
            "IRACING_COACH_GARAGE61_TOKEN_FILE": "optional path of an owner-only file holding the Garage61 token; checked before the Windows DPAPI store",
            "IRACING_COACH_NATIVE_STAGES": "empty in v1; comma list of Rust stages (decode, kernels, json_writer) in port phases",
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
            "application_language": "TypeScript",
            "view_layer": "Preact single-page app built with Vite",
            "hosts": ["desktop browser", "rig screen browser", "tablet browser", "phone browser"],
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
        CONTRACT_ROOT / "race-plan-decision-v1.schema.json": _race_plan_decision_schema(),
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
            path.write_text(expected, encoding="utf-8", newline="\n")
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

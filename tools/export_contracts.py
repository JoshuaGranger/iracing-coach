#!/usr/bin/env python3
"""Export deterministic companion compatibility and MCP input contracts."""

from __future__ import annotations

import argparse
import json
import re
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

# Exact expectations, not shapes. Checking that a symbol string is non-empty
# accepts `Bogus.CurrentVersion`; checking that a value is in range accepts a
# policy floor of 1 where 0 is the accepted value. Each field is pinned to the
# literal metadata and, where its authority is C# source, to the integer
# actually read from that source.
_COMPANION_AUTHORITY_CONTRACT = {
    "writer_version": {
        "authority": "csharp-symbol",
        "symbol": DURABLE_ARCHIVE_SYMBOL,
        "source": DURABLE_ARCHIVE_SOURCE,
    },
    "max_readable_version": {
        "authority": "csharp-symbol",
        "symbol": DURABLE_ARCHIVE_SYMBOL,
        "source": DURABLE_ARCHIVE_SOURCE,
    },
    "min_readable_version": {
        "authority": "declared-policy",
        "symbol": None,
        "source": None,
        "value": 0,
    },
}


def _strip_csharp_comments_and_strings(text: str) -> str:
    """Blank out comments, strings, and char literals, preserving offsets.

    A regular expression over raw C# happily matches a declaration inside a
    comment, so generation would bind compatibility values to dead text. Only
    executable source may carry authority, so non-code spans are replaced with
    spaces of equal length before any matching. Newlines are preserved so line
    numbers stay usable in errors.

    Unterminated comments and strings raise rather than being tolerated: an
    ambiguous parse must not resolve to a plausible number.
    """
    out: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        pair = text[index : index + 2]
        if pair == "//":
            end = text.find("\n", index)
            end = length if end == -1 else end
            out.append(" " * (end - index))
            index = end
        elif pair == "/*":
            end = text.find("*/", index + 2)
            if end == -1:
                raise ValueError(
                    f"Unterminated block comment in {DURABLE_ARCHIVE_SOURCE}; refusing to parse"
                )
            span = text[index : end + 2]
            out.append("".join("\n" if c == "\n" else " " for c in span))
            index = end + 2
        elif pair == '@"':
            cursor = index + 2
            while True:
                if cursor >= length:
                    raise ValueError(
                        f"Unterminated verbatim string in {DURABLE_ARCHIVE_SOURCE}; refusing to parse"
                    )
                if text[cursor] == '"':
                    if text[cursor : cursor + 2] == '""':
                        cursor += 2
                        continue
                    cursor += 1
                    break
                cursor += 1
            span = text[index:cursor]
            out.append("".join("\n" if c == "\n" else " " for c in span))
            index = cursor
        elif char in {'"', "'"}:
            quote = char
            cursor = index + 1
            while True:
                if cursor >= length or text[cursor] == "\n":
                    raise ValueError(
                        f"Unterminated {quote!r} literal in {DURABLE_ARCHIVE_SOURCE}; refusing to parse"
                    )
                if text[cursor] == "\\":
                    cursor += 2
                    continue
                if text[cursor] == quote:
                    cursor += 1
                    break
                cursor += 1
            out.append(" " * (cursor - index))
            index = cursor
        else:
            out.append(char)
            index += 1
    return "".join(out)


_CURRENT_SCHEMA_VERSION_DECLARATION = re.compile(
    r"(?:^|[;{}\s])public\s+const\s+int\s+CurrentSchemaVersion\s*=\s*(-?\d+)\s*;"
)


def _csharp_current_schema_version() -> int:
    """Read `CurrentSchemaVersion` from live C# source, refusing anything ambiguous.

    Generation binds the companion writer and maximum to this integer, so a
    parser that guesses, or that reads commented-out text, would defeat the
    binding. Missing, duplicated, commented-out, and non-integer declarations
    all raise rather than resolving to a plausible number.
    """
    path = WORKSPACE_ROOT / DURABLE_ARCHIVE_SOURCE
    if not path.is_file():
        raise ValueError(f"Companion archive source is missing: {DURABLE_ARCHIVE_SOURCE}")
    code = _strip_csharp_comments_and_strings(path.read_text(encoding="utf-8"))
    matches = _CURRENT_SCHEMA_VERSION_DECLARATION.findall(code)
    if not matches:
        raise ValueError(
            f"{DURABLE_ARCHIVE_SYMBOL} was not found as a live integer constant in "
            f"{DURABLE_ARCHIVE_SOURCE}"
        )
    if len(matches) > 1:
        raise ValueError(
            f"{DURABLE_ARCHIVE_SYMBOL} is declared {len(matches)} times in "
            f"{DURABLE_ARCHIVE_SOURCE}; refusing to guess which one binds"
        )
    return int(matches[0])


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

    if contract["authority"] == "csharp-symbol":
        # Bind the value to the constant actually in C# source, not merely to a
        # field that names a symbol. A self-consistent but substituted number
        # fails here even though it satisfies the range invariant.
        current = _csharp_current_schema_version()
        if value != current:
            raise ValueError(
                f"companion_durable_archive.{field}.value is {value}, but "
                f"{DURABLE_ARCHIVE_SYMBOL} is {current}"
            )
    else:
        if value != contract["value"]:
            raise ValueError(
                f"companion_durable_archive.{field}.value must be exactly "
                f"{contract['value']}, got {value}"
            )
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
            "fields are null and no plan may be stated."
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
            "reserve_green_laps": {"type": "number"},
            "scheduled_laps": number_or_null,
            "status": {"enum": list(race_plan_decision.PLAN_STATUSES), "type": "string"},
            "stints": {"type": ["integer", "null"]},
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
        CONTRACT_ROOT / "race-plan-decision-v1.schema.json": _race_plan_decision_schema(),
        # A fixture rather than a schema: these are the cases a second decoder
        # must reproduce, generated so they cannot drift from the policy.
        WORKSPACE_ROOT
        / "test-data"
        / "live-truth-conformance-v1.json": live_truth.conformance_vectors(),
        WORKSPACE_ROOT
        / "test-data"
        / "starting-tune-matrix-v1.json": {
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

"""Small stdio MCP server for deterministic iRacing Coach operations.

The server intentionally does not expose a generic filesystem or shell tool.
It offers bounded racing-data operations while Codex's normal web/browser
tools handle authenticated research and visual sources.
"""

from __future__ import annotations

import json
import math
import os
import sys
import traceback
from pathlib import Path
from typing import Any, Callable, Mapping


SCRIPT_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = SCRIPT_DIR.parents[2]
VENDOR_DIR = PLUGIN_ROOT / "vendor"
for import_path in (str(SCRIPT_DIR), str(VENDOR_DIR)):
    if import_path not in sys.path:
        sys.path.insert(0, import_path)

from storage import ArchiveStore, default_archive_root  # noqa: E402
from path_security import local_path  # noqa: E402
from native_events import (  # noqa: E402
    SUPPORTED_EVENT_SELECTION_MODES,
    SUPPORTED_EVENT_TYPES,
)


SERVER_NAME = "iracing-coach-local"
SERVER_VERSION = "0.3.0"
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")


def _load_defaults() -> dict[str, Any]:
    path = PLUGIN_ROOT / "config" / "defaults.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return dict(value) if isinstance(value, Mapping) else {}


_DEFAULTS = _load_defaults()
DEFAULT_IRACING_ROOT = local_path(
    str(
        os.environ.get("IRACING_COACH_IRACING_ROOT")
        or _DEFAULTS.get("iracing_root")
        or (Path.home() / "Documents" / "iRacing")
    ),
    "IRACING_COACH_IRACING_ROOT",
)
DEFAULT_ARCHIVE_ROOT = local_path(default_archive_root(), "IRACING_COACH_DATA")


def _reject_network_or_device_path(value: Any, label: str) -> str:
    raw = os.fspath(value).strip()
    normalized = raw.replace("/", "\\")
    if (
        normalized.startswith("\\\\")
        or normalized.startswith("\\?\\")
        or normalized.startswith("\\.\\")
    ):
        raise ValueError(f"{label} must be a local path; UNC and device paths are not allowed.")
    return raw


def _bounded_path(value: Any, base: Path, label: str) -> Path:
    raw = _reject_network_or_device_path(value, label)
    candidate = Path(raw).expanduser().resolve()
    resolved_base = base.resolve()
    try:
        candidate.relative_to(resolved_base)
    except ValueError as exc:
        raise ValueError(f"{label} must stay within {resolved_base}.") from exc
    return candidate


def _archive_root(value: Any = None) -> Path:
    if value is None or not str(value).strip():
        return DEFAULT_ARCHIVE_ROOT
    return _bounded_path(value, DEFAULT_ARCHIVE_ROOT, "archive_root")


def _iracing_root(value: Any = None, *, label: str = "iracing_root") -> Path:
    if value is None or not str(value).strip():
        return DEFAULT_IRACING_ROOT
    return _bounded_path(value, DEFAULT_IRACING_ROOT, label)


def _analysis_path(value: Any, archive_root: Path) -> Path:
    if value is None or not str(value).strip():
        raise ValueError("analysis_path is required.")
    path = _bounded_path(value, archive_root, "analysis_path")
    reports_root = archive_root / "reports"
    try:
        path.relative_to(reports_root)
    except ValueError as exc:
        raise ValueError(f"analysis_path must stay within {reports_root}.") from exc
    if path.name.lower() != "analysis.json":
        raise ValueError("analysis_path must name an archived analysis.json file.")
    return path


def _bounded_selector(value: Any) -> str:
    selector = str(value or "latest").strip()
    looks_like_path = (
        selector.lower().endswith(".ibt")
        or "\\" in selector
        or "/" in selector
        or (len(selector) >= 2 and selector[1] == ":")
    )
    return str(_bounded_path(selector, DEFAULT_IRACING_ROOT, "selector")) if looks_like_path else selector


def _bounded_identifier(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 128 or not all(
        character.isalnum() or character in "._-" for character in text
    ):
        raise ValueError(f"{label} must be 1-128 letters, numbers, dots, dashes, or underscores.")
    return text


def _bounded_text(value: Any, label: str, maximum: int) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} must be non-empty.")
    if len(text) > maximum:
        raise ValueError(f"{label} must be {maximum} characters or fewer.")
    return text


def _bounded_integer(value: Any, label: str, minimum: int, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        suffix = f" and {maximum}" if maximum is not None else ""
        raise ValueError(f"{label} must be an integer between {minimum}{suffix}.")
    if value < minimum or (maximum is not None and value > maximum):
        suffix = f" and {maximum}" if maximum is not None else " or greater"
        raise ValueError(f"{label} must be between {minimum}{suffix}.")
    return value


def _telemetry_query_arguments(arguments: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {
        "selector",
        "iracing_root",
        "archive_root",
        "mode",
        "channels",
        "search",
        "target_hz",
        "start_record",
        "end_record",
        "max_samples",
    }
    unexpected = sorted(str(name) for name in set(arguments).difference(allowed))
    if unexpected:
        raise ValueError(
            "query_iracing_telemetry received unsupported argument(s): "
            + ", ".join(unexpected)
        )

    mode = arguments.get("mode", "catalog")
    if not isinstance(mode, str) or mode not in {"catalog", "profile", "slice"}:
        raise ValueError("mode must be one of: catalog, profile, slice.")

    raw_channels = arguments.get("channels")
    channels: list[str] | None
    if raw_channels is None:
        channels = None
    elif not isinstance(raw_channels, list):
        raise ValueError("channels must be an array containing at most 12 channel names.")
    else:
        if len(raw_channels) > 12:
            raise ValueError("channels must contain at most 12 channel names.")
        channels = []
        seen: set[str] = set()
        for raw_channel in raw_channels:
            if not isinstance(raw_channel, str):
                raise ValueError("each channel name must be a string.")
            channel = raw_channel.strip()
            if not channel or len(channel) > 64:
                raise ValueError("each channel name must contain 1-64 characters.")
            if channel in seen:
                raise ValueError(f"channels contains a duplicate name: {channel}")
            seen.add(channel)
            channels.append(channel)

    search: str | None = None
    if arguments.get("search") is not None:
        raw_search = arguments["search"]
        if not isinstance(raw_search, str):
            raise ValueError("search must be a string containing at most 100 characters.")
        if len(raw_search) > 100:
            raise ValueError("search must be 100 characters or fewer.")
        search = raw_search

    target_hz: float | None = None
    if arguments.get("target_hz") is not None:
        raw_target = arguments["target_hz"]
        if isinstance(raw_target, bool) or not isinstance(raw_target, (int, float)):
            raise ValueError("target_hz must be a number between 1 and 60, or null for native rate.")
        target_hz = float(raw_target)
        if not math.isfinite(target_hz) or target_hz < 1 or target_hz > 60:
            raise ValueError("target_hz must be a number between 1 and 60, or null for native rate.")

    start_record = 0
    if arguments.get("start_record") is not None:
        start_record = _bounded_integer(arguments["start_record"], "start_record", 0)
    end_record = None
    if arguments.get("end_record") is not None:
        end_record = _bounded_integer(arguments["end_record"], "end_record", 1)
    if start_record is not None and end_record is not None and end_record <= start_record:
        raise ValueError("end_record must be greater than start_record.")

    return {
        "mode": mode,
        "channels": channels,
        "search": search,
        "target_hz": target_hz,
        "start_record": start_record,
        "end_record": end_record,
        "max_samples": _bounded_integer(
            arguments.get("max_samples", 1000), "max_samples", 1, 2000
        ),
    }


def _optional_bounded_number(
    value: Any,
    label: str,
    minimum: float,
    maximum: float | None = None,
) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        suffix = f" and {maximum:g}" if maximum is not None else ""
        raise ValueError(f"{label} must be a number between {minimum:g}{suffix}.")
    number = float(value)
    if not math.isfinite(number) or number < minimum or (
        maximum is not None and number > maximum
    ):
        suffix = f" and {maximum:g}" if maximum is not None else " or greater"
        raise ValueError(f"{label} must be between {minimum:g}{suffix}.")
    return number


def _native_event_arguments(arguments: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {
        "selector",
        "iracing_root",
        "archive_root",
        "event_types",
        "selection_mode",
        "start_record",
        "end_record",
        "max_events",
        "lap",
        "session_time_start",
        "session_time_end",
        "lap_distance_start",
        "lap_distance_end",
    }
    unexpected = sorted(str(name) for name in set(arguments).difference(allowed))
    if unexpected:
        raise ValueError(
            "find_iracing_telemetry_events received unsupported argument(s): "
            + ", ".join(unexpected)
        )

    raw_types = arguments.get("event_types")
    event_types: list[str] | None = None
    if raw_types is not None:
        if not isinstance(raw_types, list) or not 1 <= len(raw_types) <= len(
            SUPPORTED_EVENT_TYPES
        ):
            raise ValueError(
                f"event_types must be an array containing 1-{len(SUPPORTED_EVENT_TYPES)} event types."
            )
        event_types = []
        for raw_name in raw_types:
            if not isinstance(raw_name, str) or raw_name not in SUPPORTED_EVENT_TYPES:
                raise ValueError(
                    "event_types may contain only: "
                    + ", ".join(SUPPORTED_EVENT_TYPES)
                    + "."
                )
            if raw_name in event_types:
                raise ValueError(f"event_types contains a duplicate name: {raw_name}")
            event_types.append(raw_name)

    selection_mode = arguments.get("selection_mode", "chronological")
    if (
        not isinstance(selection_mode, str)
        or selection_mode not in SUPPORTED_EVENT_SELECTION_MODES
    ):
        raise ValueError(
            "selection_mode must be one of: "
            + ", ".join(SUPPORTED_EVENT_SELECTION_MODES)
            + "."
        )

    start_record = _bounded_integer(
        arguments.get("start_record", 0), "start_record", 0
    )
    end_record = None
    if arguments.get("end_record") is not None:
        end_record = _bounded_integer(arguments["end_record"], "end_record", 1)
        if end_record <= start_record:
            raise ValueError("end_record must be greater than start_record.")

    session_time_start = _optional_bounded_number(
        arguments.get("session_time_start"), "session_time_start", 0
    )
    session_time_end = _optional_bounded_number(
        arguments.get("session_time_end"), "session_time_end", 0
    )
    if (
        session_time_start is not None
        and session_time_end is not None
        and session_time_end <= session_time_start
    ):
        raise ValueError("session_time_end must be greater than session_time_start.")

    return {
        "event_types": event_types,
        "selection_mode": selection_mode,
        "start_record": start_record,
        "end_record": end_record,
        "max_events": _bounded_integer(
            arguments.get("max_events", 200), "max_events", 1, 500
        ),
        "lap": (
            _bounded_integer(arguments["lap"], "lap", 0)
            if arguments.get("lap") is not None
            else None
        ),
        "session_time_start": session_time_start,
        "session_time_end": session_time_end,
        "lap_distance_start": _optional_bounded_number(
            arguments.get("lap_distance_start"), "lap_distance_start", 0, 1
        ),
        "lap_distance_end": _optional_bounded_number(
            arguments.get("lap_distance_end"), "lap_distance_end", 0, 1
        ),
    }


TOOLS: list[dict[str, Any]] = [
    {
        "name": "inventory_iracing_data",
        "description": "Read-only inventory of local iRacing telemetry, replays, setups, lapfiles, safe logging settings, and known install roots.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "root": {"type": "string", "description": "Optional iRacing Documents root or a descendant."},
                "recent_limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 20},
                "include_known_roots": {"type": "boolean", "default": True},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "iracing_companion_dashboard",
        "description": "Return a compact read-only snapshot of recent races, archived analyses, tuning packages, capabilities, and local Garage61 readiness for a companion UI.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "root": {"type": "string", "description": "Optional iRacing Documents root or a descendant."},
                "archive_root": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "discover_iracing_sessions",
        "description": "Discover recorded iRacing sessions and identify the latest Race session from IBT metadata.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "root": {"type": "string", "description": "Optional iRacing root or telemetry directory."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 20},
                "races_only": {"type": "boolean", "default": True},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "query_iracing_telemetry",
        "description": "Inspect the complete channel catalog, profile selected channels, or load a bounded telemetry slice from the latest or specified iRacing IBT session.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "'latest', an IBT path within the configured iRacing root, a SubSessionID, or a discovery group_id such as 'subsession:<id>:<sim_session_num>' to pin the exact event phase.",
                    "default": "latest",
                },
                "iracing_root": {"type": "string"},
                "archive_root": {"type": "string"},
                "mode": {
                    "type": "string",
                    "enum": ["catalog", "profile", "slice"],
                    "default": "catalog",
                },
                "channels": {
                    "type": "array",
                    "maxItems": 12,
                    "uniqueItems": True,
                    "items": {"type": "string", "minLength": 1, "maxLength": 64},
                },
                "search": {"type": "string", "maxLength": 100},
                "target_hz": {
                    "oneOf": [
                        {"type": "number", "minimum": 1, "maximum": 60},
                        {"type": "null"},
                    ],
                    "default": None,
                    "description": "Output sample rate; omit or pass null to retain native rate.",
                },
                "start_record": {"type": "integer", "minimum": 0},
                "end_record": {"type": "integer", "minimum": 1},
                "max_samples": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 2000,
                    "default": 1000,
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "find_iracing_telemetry_events",
        "description": "Find bounded native-rate brake, pit, steering-torque, shock-velocity, and calibrated wheel-speed-divergence events with exact source-record, lap, time, and track-position context.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "'latest', an IBT path within the configured iRacing root, a SubSessionID, or a discovery group_id such as 'subsession:<id>:<sim_session_num>' to pin the exact event phase.",
                    "default": "latest",
                },
                "iracing_root": {"type": "string"},
                "archive_root": {"type": "string"},
                "event_types": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": len(SUPPORTED_EVENT_TYPES),
                    "uniqueItems": True,
                    "items": {"type": "string", "enum": list(SUPPORTED_EVENT_TYPES)},
                    "description": "Omit to run every supported detector; select only the needed types for compact results.",
                },
                "selection_mode": {
                    "type": "string",
                    "enum": list(SUPPORTED_EVENT_SELECTION_MODES),
                    "default": "chronological",
                    "description": "Return the earliest matches, or scan the full window and retain a balanced strongest subset.",
                },
                "start_record": {"type": "integer", "minimum": 0, "default": 0},
                "end_record": {"type": "integer", "minimum": 1},
                "max_events": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 500,
                    "default": 200,
                },
                "lap": {"type": "integer", "minimum": 0},
                "session_time_start": {"type": "number", "minimum": 0},
                "session_time_end": {"type": "number", "minimum": 0},
                "lap_distance_start": {"type": "number", "minimum": 0, "maximum": 1},
                "lap_distance_end": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "analyze_iracing_race",
        "description": "Analyze the latest Race session, or a specified IBT/session; return a concise deterministic Race Card plus damage, tow, repair, pit, tire, fuel, strategy, and coaching context with stage timings, then archive the complete evidence report.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "'latest', an IBT path, a SubSessionID, or a discovery group_id such as 'subsession:<id>:<sim_session_num>' to pin the exact event phase.", "default": "latest"},
                "iracing_root": {"type": "string"},
                "archive_root": {"type": "string"},
                "target_hz": {"type": "number", "minimum": 1, "maximum": 60, "default": 20},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "iracing_knowledge_cache_status",
        "description": "Check whether the season/car/track/setup knowledge bundle is fresh, stale, or missing.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "analysis_path": {"type": "string"},
                "context": {"type": "object"},
                "archive_root": {"type": "string"},
                "sim_physics_fingerprint": {
                    "oneOf": [{"type": "string"}, {"type": "object"}],
                    "description": "Optional current sim/build/physics fingerprint used for early invalidation.",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "archive_iracing_knowledge",
        "description": "Persist researched car, track, manual, image-source, and Garage61 facts for the current iRacing season.",
        "inputSchema": {
            "type": "object",
            "required": ["context", "sources", "facts"],
            "properties": {
                "context": {"type": "object"},
                "sources": {"type": "array", "items": {"type": "object"}},
                "facts": {"type": "object"},
                "garage61": {"type": "object"},
                "track_shape": {"type": "object"},
                "notes_markdown": {"type": "string"},
                "sim_physics_fingerprint": {
                    "oneOf": [{"type": "string"}, {"type": "object"}]
                },
                "archive_root": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "garage61_auth_status",
        "description": "Verify whether a securely stored Garage61 personal token exists and can access the official API.",
        "inputSchema": {
            "type": "object",
            "properties": {"archive_root": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "sync_garage61_references",
        "description": "Find, rank, and archive comparable Garage61 laps for an analyzed car/track/session using the official API.",
        "inputSchema": {
            "type": "object",
            "required": ["analysis_path"],
            "properties": {
                "analysis_path": {"type": "string"},
                "archive_root": {"type": "string"},
                "maximum_laps": {"type": "integer", "minimum": 1, "maximum": 50, "default": 8},
                "download_telemetry": {"type": "boolean", "default": True},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "iracing_strategy_history",
        "description": "Return comparable historical runs for the same season, car, exact track layout, setup type, and race length.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "analysis_path": {"type": "string"},
                "context": {"type": "object"},
                "archive_root": {"type": "string"},
                "include_other_seasons": {"type": "boolean", "default": False},
                "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 200},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "catalog_iracing_setups",
        "description": "Read-only catalog of local iRacing STO and HTML setup exports with pairing, hashes, parsed values, and identity warnings.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "root": {"type": "string", "description": "Optional iRacing Documents root or its setups directory."},
                "archive_root": {"type": "string"},
                "car": {"type": "string", "maxLength": 160},
                "track": {"type": "string", "maxLength": 160},
                "season": {"type": "string", "maxLength": 32},
                "role": {"type": "string", "enum": ["race", "qualifying", "endurance", "R", "Q", "E"]},
                "maximum_entries": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 200},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "build_open_setup_package",
        "description": "Create a season-scoped open-setup race/Q baseline for an exact car/track or a researched donor family; source setups remain read-only.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "analysis_path": {"type": "string"},
                "iracing_root": {"type": "string"},
                "archive_root": {"type": "string"},
                "season": {"type": "string", "maxLength": 32},
                "car": {"type": "string", "maxLength": 160},
                "track": {"type": "string", "maxLength": 160},
                "track_characteristics": {
                    "oneOf": [{"type": "object"}, {"type": "string", "maxLength": 4000}]
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "recommend_open_setup_tuning",
        "description": "Combine driver symptoms, the authoritative embedded setup, telemetry, a baseline package, and prior experiments into a conservative open-setup test plan.",
        "inputSchema": {
            "type": "object",
            "required": ["analysis_path", "symptoms"],
            "properties": {
                "analysis_path": {"type": "string"},
                "archive_root": {"type": "string"},
                "package_id": {"type": "string", "maxLength": 128},
                "symptoms": {
                    "oneOf": [
                        {"type": "string", "minLength": 1, "maxLength": 8000},
                        {"type": "array", "minItems": 1, "maxItems": 20, "items": {"type": "string", "maxLength": 1000}}
                    ]
                },
                "maximum_changes": {"type": "integer", "minimum": 1, "maximum": 5, "default": 3},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "record_open_setup_feedback",
        "description": "Record whether a planned setup experiment improved, worsened, or did not change the car, optionally validating a result analysis.",
        "inputSchema": {
            "type": "object",
            "required": ["experiment_id", "outcome", "notes"],
            "properties": {
                "experiment_id": {"type": "string", "maxLength": 128},
                "outcome": {"type": "string", "enum": ["improved", "worse", "no-change", "inconclusive"]},
                "notes": {"type": "string", "maxLength": 8000},
                "result_analysis_path": {"type": "string"},
                "archive_root": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "iracing_setup_history",
        "description": "Return season/car/track-scoped open-setup experiments and their recorded outcomes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "analysis_path": {"type": "string"},
                "package_id": {"type": "string", "maxLength": 128},
                "context": {"type": "object"},
                "archive_root": {"type": "string"},
                "include_other_seasons": {"type": "boolean", "default": False},
                "include_other_tracks": {"type": "boolean", "default": False},
                "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100},
            },
            "additionalProperties": False,
        },
    },
]


def _read_analysis(path: str) -> dict[str, Any]:
    target = Path(path)
    return json.loads(target.read_text(encoding="utf-8"))


def _context(arguments: Mapping[str, Any], store: ArchiveStore) -> dict[str, str]:
    context = arguments.get("context")
    if isinstance(context, Mapping):
        required = {"season_key", "car_key", "track_key", "setup_type", "race_length_key"}
        missing = required.difference(context)
        if missing:
            raise ValueError(f"Context is missing: {', '.join(sorted(missing))}")
        return {name: str(context[name]) for name in required}
    analysis_path = arguments.get("analysis_path")
    if not analysis_path:
        raise ValueError("Provide analysis_path or context.")
    return store.context_from_analysis(_read_analysis(str(analysis_path)))


def _workflow_function(name: str) -> Callable[..., Any]:
    try:
        import workflow
    except ImportError as exc:
        raise RuntimeError(f"iRacing workflow module could not load: {exc}") from exc
    function = getattr(workflow, name, None)
    if function is None:
        raise RuntimeError(f"iRacing workflow does not implement {name}.")
    return function


def call_tool(name: str, arguments: Mapping[str, Any]) -> Any:
    if not isinstance(arguments, Mapping):
        raise ValueError("Tool arguments must be an object.")
    archive_root = _archive_root(arguments.get("archive_root"))
    store = ArchiveStore(archive_root)
    if name == "inventory_iracing_data":
        return _workflow_function("inventory_iracing_data_workflow")(
            root=_iracing_root(arguments.get("root"), label="root"),
            recent_limit=int(arguments.get("recent_limit", 20)),
            include_known_roots=bool(arguments.get("include_known_roots", True)),
        )
    if name == "iracing_companion_dashboard":
        return _workflow_function("companion_dashboard_workflow")(
            root=_iracing_root(arguments.get("root"), label="root"),
            archive_root=str(archive_root),
            limit=_bounded_integer(arguments.get("limit", 20), "limit", 1, 100),
        )
    if name == "discover_iracing_sessions":
        return _workflow_function("discover_sessions_workflow")(
            root=_iracing_root(arguments.get("root"), label="root"),
            limit=int(arguments.get("limit", 20)),
            races_only=bool(arguments.get("races_only", True)),
        )
    if name == "query_iracing_telemetry":
        query = _telemetry_query_arguments(arguments)
        return _workflow_function("telemetry_query_workflow")(
            selector=_bounded_selector(arguments.get("selector", "latest")),
            iracing_root=_iracing_root(arguments.get("iracing_root")),
            archive_root=str(archive_root),
            **query,
        )
    if name == "find_iracing_telemetry_events":
        query = _native_event_arguments(arguments)
        return _workflow_function("native_event_search_workflow")(
            selector=_bounded_selector(arguments.get("selector", "latest")),
            iracing_root=_iracing_root(arguments.get("iracing_root")),
            archive_root=str(archive_root),
            **query,
        )
    if name == "analyze_iracing_race":
        return _workflow_function("analyze_race_workflow")(
            selector=_bounded_selector(arguments.get("selector", "latest")),
            iracing_root=_iracing_root(arguments.get("iracing_root")),
            archive_root=str(archive_root),
            target_hz=float(arguments.get("target_hz", 20)),
        )
    if name == "iracing_knowledge_cache_status":
        if arguments.get("analysis_path"):
            arguments = {**arguments, "analysis_path": str(_analysis_path(arguments["analysis_path"], archive_root))}
        return store.cache_status(
            _context(arguments, store),
            sim_physics_fingerprint=arguments.get("sim_physics_fingerprint"),
        )
    if name == "archive_iracing_knowledge":
        context = _context(arguments, store)
        return store.write_knowledge_bundle(
            context,
            sources=arguments.get("sources") or [],
            facts=arguments.get("facts") or {},
            garage61=arguments.get("garage61"),
            track_shape=arguments.get("track_shape"),
            notes_markdown=arguments.get("notes_markdown"),
            sim_physics_fingerprint=arguments.get("sim_physics_fingerprint"),
        )
    if name == "garage61_auth_status":
        return _workflow_function("garage61_status_workflow")(archive_root=str(archive_root))
    if name == "sync_garage61_references":
        analysis_path = _analysis_path(arguments["analysis_path"], archive_root)
        return _workflow_function("garage61_sync_workflow")(
            analysis_path=str(analysis_path),
            archive_root=str(archive_root),
            maximum_laps=int(arguments.get("maximum_laps", 8)),
            download_telemetry=bool(arguments.get("download_telemetry", True)),
        )
    if name == "iracing_strategy_history":
        if arguments.get("analysis_path"):
            arguments = {**arguments, "analysis_path": str(_analysis_path(arguments["analysis_path"], archive_root))}
        context = _context(arguments, store)
        return store.historical_runs(
            context,
            limit=int(arguments.get("limit", 200)),
            include_other_seasons=bool(arguments.get("include_other_seasons", False)),
        )
    if name == "catalog_iracing_setups":
        return _workflow_function("catalog_iracing_setups_workflow")(
            root=_iracing_root(arguments.get("root"), label="root"),
            archive_root=str(archive_root),
            car=arguments.get("car"),
            track=arguments.get("track"),
            season=arguments.get("season"),
            role=arguments.get("role"),
            maximum_entries=int(arguments.get("maximum_entries", 200)),
        )
    if name == "build_open_setup_package":
        analysis_path = (
            str(_analysis_path(arguments["analysis_path"], archive_root))
            if arguments.get("analysis_path") else None
        )
        return _workflow_function("build_open_setup_package_workflow")(
            analysis_path=analysis_path,
            iracing_root=_iracing_root(arguments.get("iracing_root")),
            archive_root=str(archive_root),
            season=arguments.get("season"),
            car=arguments.get("car"),
            track=arguments.get("track"),
            track_characteristics=arguments.get("track_characteristics"),
        )
    if name == "recommend_open_setup_tuning":
        package_id = (
            _bounded_identifier(arguments["package_id"], "package_id")
            if arguments.get("package_id") else None
        )
        symptoms = arguments.get("symptoms")
        if isinstance(symptoms, str):
            _bounded_text(symptoms, "symptoms", 8000)
        elif isinstance(symptoms, list):
            if not symptoms or len(symptoms) > 20:
                raise ValueError("symptoms must contain 1-20 items.")
            symptoms = [_bounded_text(item, "symptom", 1000) for item in symptoms]
        else:
            raise ValueError("symptoms must be a string or array of strings.")
        return _workflow_function("recommend_open_setup_tuning_workflow")(
            analysis_path=str(_analysis_path(arguments["analysis_path"], archive_root)),
            symptoms=symptoms,
            archive_root=str(archive_root),
            package_id=package_id,
            maximum_changes=int(arguments.get("maximum_changes", 3)),
        )
    if name == "record_open_setup_feedback":
        result_analysis = (
            str(_analysis_path(arguments["result_analysis_path"], archive_root))
            if arguments.get("result_analysis_path") else None
        )
        return _workflow_function("record_open_setup_feedback_workflow")(
            experiment_id=_bounded_identifier(arguments.get("experiment_id"), "experiment_id"),
            outcome=str(arguments.get("outcome") or ""),
            notes=str(arguments.get("notes") or ""),
            result_analysis_path=result_analysis,
            archive_root=str(archive_root),
        )
    if name == "iracing_setup_history":
        analysis_path = (
            str(_analysis_path(arguments["analysis_path"], archive_root))
            if arguments.get("analysis_path") else None
        )
        package_id = (
            _bounded_identifier(arguments["package_id"], "package_id")
            if arguments.get("package_id") else None
        )
        return _workflow_function("iracing_setup_history_workflow")(
            archive_root=str(archive_root),
            analysis_path=analysis_path,
            package_id=package_id,
            context=arguments.get("context"),
            include_other_seasons=bool(arguments.get("include_other_seasons", False)),
            include_other_tracks=bool(arguments.get("include_other_tracks", False)),
            limit=int(arguments.get("limit", 100)),
        )
    raise ValueError(f"Unknown tool: {name}")


def _response(request_id: Any, result: Any = None, error: Mapping[str, Any] | None = None) -> dict[str, Any]:
    response: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id}
    if error is not None:
        response["error"] = dict(error)
    else:
        response["result"] = result
    return response


def _write(message: Mapping[str, Any]) -> None:
    sys.stdout.write(json.dumps(message, separators=(",", ":"), ensure_ascii=False, default=str) + "\n")
    sys.stdout.flush()


def handle(message: Mapping[str, Any]) -> dict[str, Any] | None:
    if not isinstance(message, Mapping) or message.get("jsonrpc") != "2.0":
        request_id = message.get("id") if isinstance(message, Mapping) else None
        return _response(request_id, error={"code": -32600, "message": "Invalid JSON-RPC 2.0 request."})
    method = message.get("method")
    request_id = message.get("id")
    if request_id is None:
        return None
    if not isinstance(method, str) or not method:
        return _response(request_id, error={"code": -32600, "message": "Request method must be a non-empty string."})
    params = message.get("params") or {}
    if not isinstance(params, Mapping):
        return _response(request_id, error={"code": -32602, "message": "Request params must be an object."})
    if method == "initialize":
        requested = params.get("protocolVersion")
        negotiated = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else SUPPORTED_PROTOCOL_VERSIONS[0]
        return _response(
            request_id,
            {
                "protocolVersion": negotiated,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        )
    if method == "ping":
        return _response(request_id, {})
    if method == "tools/list":
        return _response(request_id, {"tools": TOOLS})
    if method == "tools/call":
        name = str(params.get("name") or "")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, Mapping):
            return _response(request_id, error={"code": -32602, "message": "Tool arguments must be an object."})
        try:
            result = call_tool(name, arguments)
            text = json.dumps(result, indent=2, ensure_ascii=False, default=str)
            return _response(request_id, {"content": [{"type": "text", "text": text}], "isError": False})
        except Exception as exc:  # MCP tools must return actionable diagnostics.
            traceback.print_exc(file=sys.stderr)
            text = json.dumps(
                {"error": type(exc).__name__, "message": str(exc), "tool": name},
                indent=2,
            )
            return _response(request_id, {"content": [{"type": "text", "text": text}], "isError": True})
    return _response(request_id, error={"code": -32601, "message": f"Method not found: {method}"})


def main() -> int:
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
            response = handle(message)
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            response = _response(None, error={"code": -32700, "message": str(exc)})
        if response is not None:
            _write(response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

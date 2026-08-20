"""End-to-end local analysis and optional Garage61 synchronization.

The functions in this module are the stable integration surface shared by the
stdio MCP server and ``coach_cli.py``.  Local race analysis deliberately has no
network dependency: Garage61 authentication and downloads happen only through
the explicit status and sync workflows.
"""

from __future__ import annotations

from collections import Counter
import datetime as _datetime
import hashlib
import json
import math
import os
import re
import statistics
import time
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

try:  # Package import and direct script execution are both supported.
    from . import backend_roots
    from .path_security import local_path
except ImportError:  # pragma: no cover - normal CLI/MCP script-loading path.
    import backend_roots
    from path_security import local_path

try:  # Support package imports and direct execution from the scripts folder.
    from .analysis_engine import (
        ANALYSIS_PROFILE_VERSION,
        ANALYSIS_SCHEMA_VERSION,
        analyze_telemetry,
        build_technical_insights,
    )
    from .garage61_client import (
        Garage61AuthError,
        Garage61Client,
        Garage61Error,
        Garage61PermissionError,
        RankedLap,
        parse_telemetry_csv,
    )
    from .ibt_reader import (
        discover_sessions,
        iter_telemetry_chunks,
        load_telemetry,
        profile_telemetry,
        scan_ibt,
    )
    from .native_events import (
        SUPPORTED_EVENT_SELECTION_MODES,
        SUPPORTED_EVENT_TYPES,
        detect_native_telemetry_events,
        select_native_events_by_severity,
    )
    from .race_card import build_race_card, render_race_card
    from .reporting import render_report, render_visuals
    from .secure_store import SecureStoreError, credential_exists
    from .storage import ArchiveStore, safe_slug, session_phase, stable_hash, utc_now
except ImportError:  # pragma: no cover - normal path for CLI/MCP script loading.
    from analysis_engine import (
        ANALYSIS_PROFILE_VERSION,
        ANALYSIS_SCHEMA_VERSION,
        analyze_telemetry,
        build_technical_insights,
    )
    from garage61_client import (
        Garage61AuthError,
        Garage61Client,
        Garage61Error,
        Garage61PermissionError,
        RankedLap,
        parse_telemetry_csv,
    )
    from ibt_reader import (
        discover_sessions,
        iter_telemetry_chunks,
        load_telemetry,
        profile_telemetry,
        scan_ibt,
    )
    from native_events import (
        SUPPORTED_EVENT_SELECTION_MODES,
        SUPPORTED_EVENT_TYPES,
        detect_native_telemetry_events,
        select_native_events_by_severity,
    )
    from race_card import build_race_card, render_race_card
    from reporting import render_report, render_visuals
    from secure_store import SecureStoreError, credential_exists
    from storage import ArchiveStore, safe_slug, session_phase, stable_hash, utc_now


SCRIPT_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = SCRIPT_DIR.parents[2]
DEFAULTS_PATH = PLUGIN_ROOT / "config" / "defaults.json"
_GARAGE61_TARGET_DERIVATION_VERSION = "explicit-analysis-paths-v1"

# The `analysis_view` envelope is this module's product, and this is its only
# version authority. It is deliberately separate from
# `analysis_engine.ANALYSIS_SCHEMA_VERSION`, which versions the larger analysis
# document; conflating the two would publish a version no producer emits.
ANALYSIS_VIEW_SCHEMA_VERSION = 1

# One declaration of the envelope's shape, consumed by three callers: the
# producer below builds from it, `tools/export_contracts.py` generates the
# contract from it, and the backend contract test asserts the emitted key set
# equals it. Duplicating the field list would create exactly the drift surface
# this workstream exists to remove.
#
# `default` is the factory used when the analysis document omits the key, which
# is why every field is always present and why an empty container means "no
# content", never "not emitted".
ANALYSIS_VIEW_FIELDS: tuple[tuple[str, str, Any], ...] = (
    ("analysis_profile_version", "string-or-null", None),
    ("identity", "object", dict),
    ("race_summary", "object", dict),
    ("race_grades", "object", dict),
    ("runs", "array", list),
    ("laps", "array", list),
    ("lap_traces", "object", dict),
    ("track_profile", "object", dict),
    ("track_geometry", "object", dict),
    ("race_replay", "object", dict),
    ("tire_learning", "object", dict),
    ("garage61_representative_laps", "object", dict),
    ("technical_insights", "array", list),
    ("corner_tire_age", "object", dict),
    ("groove_evolution", "object", dict),
    ("strategy", "object", dict),
    ("strategy_planning", "object", dict),
    ("field_pace", "object", dict),
    ("pace_attribution", "object", dict),
    ("damage_repair", "object", dict),
    ("setup_telemetry", "object", dict),
    ("conditions", "object", dict),
    ("data_quality", "object", dict),
)


def build_analysis_view(analysis: Mapping[str, Any]) -> dict[str, Any]:
    """Build the `analysis_view` envelope from the single field declaration.

    The falsy-substitution below reproduces the previous inline construction
    exactly. It used `analysis.get(key) or {}`, which replaces any falsy value
    and not merely `None`, so an `is None` check here would quietly change the
    emitted bytes for an empty-but-present section. Only the version literal
    changes, and it changes to an equal constant.
    """
    view: dict[str, Any] = {"schema_version": ANALYSIS_VIEW_SCHEMA_VERSION}
    for name, _kind, default in ANALYSIS_VIEW_FIELDS:
        value = analysis.get(name)
        if default is not None and not value:
            value = default()
        view[name] = value
    return view


class WorkflowError(RuntimeError):
    """Base class for actionable workflow integration failures."""


class SessionSelectionError(WorkflowError):
    """Raised when a requested recording cannot be selected unambiguously."""


class Garage61MappingError(WorkflowError):
    """Raised when local content cannot be mapped to an exact Garage61 item."""


_BASE_ANALYSIS_CHANNELS = (
    "SessionTime",
    "SessionTimeOfDay",
    "SessionNum",
    "SessionUniqueID",
    "Lap",
    "LapCompleted",
    "LapDistPct",
    "LapLastLapTime",
    "LapBestLapTime",
    "LapCurrentLapTime",
    "Speed",
    "RPM",
    "Gear",
    "Clutch",
    "Throttle",
    "ThrottleRaw",
    "Brake",
    "BrakeRaw",
    "SteeringWheelAngle",
    "SteeringWheelAngleMax",
    "LatAccel",
    "LongAccel",
    "VertAccel",
    "YawRate",
    "FuelLevel",
    "SessionFlags",
    "OnPitRoad",
    "PlayerCarInPitStall",
    "PitstopActive",
    "PitSvFlags",
    "PlayerTrackSurface",
    "PlayerCarPosition",
    "PlayerCarClassPosition",
    "RaceLaps",
    "TrackTempCrew",
    "AirTemp",
    "TrackWetness",
    "TrackUsage",
    "Lat",
    "Lon",
    "Alt",
)


_EXTENDED_ANALYSIS_CHANNELS = (
    # Session/run state and race context.
    "SessionTick",
    "SessionState",
    "SessionTimeRemain",
    "SessionLapsRemain",
    "SessionLapsRemainEx",
    "SessionTimeTotal",
    "SessionLapsTotal",
    "IsOnTrack",
    "IsOnTrackCar",
    "LapDist",
    "CarDistAhead",
    "CarDistBehind",
    "PlayerTrackSurfaceMaterial",
    "PlayerCarPitSvStatus",
    "PlayerTireCompound",
    "PitsOpen",
    "PaceMode",
    "PlayerCarMyIncidentCount",
    "PlayerCarDriverIncidentCount",
    "PlayerCarTeamIncidentCount",
    "PlayerIncidents",
    # Recorded disruption/repair evidence. Incident counts are context only;
    # the deterministic analyzer never treats points or pace loss as damage.
    "PlayerCarTowTime",
    "PitRepairLeft",
    "PitOptRepairLeft",
    "PlayerFastRepairsUsed",
    "FastRepairUsed",
    "FastRepairAvailable",
    # Pit-service confirmation and fuel cross-checks.
    "FuelLevelPct",
    "FuelUsePerHour",
    "PitSvFuel",
    "LFTiresUsed",
    "RFTiresUsed",
    "LRTiresUsed",
    "RRTiresUsed",
    "TireSetsUsed",
    # Braking, steering, orientation, and body motion.
    "BrakeABSactive",
    "BrakeABScutPct",
    "SteeringWheelTorque",
    "SteeringWheelPctTorque",
    "Yaw",
    "Pitch",
    "Roll",
    "PitchRate",
    "RollRate",
    "VelocityX",
    "VelocityY",
    "VelocityZ",
    # In-car and requested pit adjustments useful for setup provenance.
    "dcBrakeBias",
    "dpQTape",
    "dpWeightJackerLeft",
    "dpWeightJackerRight",
    "dpFuelAddKg",
    "dpFastRepair",
    # Conditions that materially affect pace, tire use, and comparability.
    "TrackTemp",
    "AirDensity",
    "AirPressure",
    "WindVel",
    "WindDir",
    "Skies",
    "FogLevel",
    "RelativeHumidity",
    "Precipitation",
    "WeatherDeclaredWet",
    # Full-field channels. Historical IBTs frequently omit these arrays; the
    # replay contract reports that absence rather than synthesizing rivals.
    "CarIdxLap",
    "CarIdxLapCompleted",
    "CarIdxLapDistPct",
    "CarIdxPosition",
    "CarIdxClassPosition",
    "CarIdxOnPitRoad",
    "CarIdxTrackSurface",
    "CarIdxPaceFlags",
    "CarIdxLastLapTime",
    "CarIdxBestLapTime",
)


def _wheel_analysis_channels() -> tuple[str, ...]:
    result: list[str] = []
    for tire in ("LF", "RF", "LR", "RR"):
        result.extend((f"{tire}speed", f"{tire}odometer"))
    return tuple(result)


def _tire_analysis_channels() -> tuple[str, ...]:
    result: list[str] = []
    for tire in ("LF", "RF", "LR", "RR"):
        for position in ("L", "M", "R"):
            result.extend(
                (
                    f"{tire}wear{position}",
                    f"{tire}Wear{position}",
                    f"{tire}tireWear{position}",
                    f"{tire}TireWear{position}",
                )
            )
        for position in ("CL", "CM", "CR"):
            result.extend(
                (
                    f"{tire}temp{position}",
                    f"{tire}Temp{position}",
                    f"{tire}tireTemp{position}",
                    f"{tire}TireTemp{position}",
                )
            )
        for position in ("L", "M", "R"):
            result.extend(
                (
                    f"{tire}temp{position}",
                    f"{tire}Temp{position}",
                    f"{tire}tireTemp{position}",
                    f"{tire}TireTemp{position}",
                )
            )
        result.extend(
            (
                f"{tire}pressure",
                f"{tire}Pressure",
                f"{tire}tirePressure",
                f"{tire}TirePressure",
                f"{tire}hotPressure",
                f"{tire}HotPressure",
                f"{tire}coldPressure",
                f"{tire}ColdPressure",
                f"{tire}tireColdPressure",
                f"{tire}TireColdPressure",
            )
        )
    return tuple(result)


def _setup_analysis_channels() -> tuple[str, ...]:
    result = [
        "CFSRrideHeight",
        "CFSRRideHeight",
        "CenterFrontSplitterRideHeight",
        "CenterFrontRideHeight",
        "SplitterRideHeight",
    ]
    for tire in ("LF", "RF", "LR", "RR"):
        result.extend((f"{tire}rideHeight", f"{tire}RideHeight"))
        for suffix in ("shockDefl", "ShockDefl", "shockDeflection", "ShockDeflection"):
            result.extend((f"{tire}SH{suffix}", f"{tire}{suffix}"))
        for suffix in ("damperDefl", "DamperDefl", "damperDeflection", "DamperDeflection"):
            result.append(f"{tire}{suffix}")
        for suffix in ("shockVel", "ShockVel", "shockVelocity", "ShockVelocity"):
            result.extend((f"{tire}SH{suffix}", f"{tire}{suffix}"))
        for suffix in ("damperVel", "DamperVel", "damperVelocity", "DamperVelocity"):
            result.append(f"{tire}{suffix}")
    return tuple(result)


ANALYSIS_CHANNELS = tuple(
    dict.fromkeys(
        _BASE_ANALYSIS_CHANNELS
        + _EXTENDED_ANALYSIS_CHANNELS
        + _wheel_analysis_channels()
        + _tire_analysis_channels()
        + _setup_analysis_channels()
    )
)


def _load_defaults() -> dict[str, Any]:
    try:
        payload = json.loads(DEFAULTS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return dict(payload) if isinstance(payload, Mapping) else {}


def _default_iracing_root() -> Path:
    # `backend_roots` is the single authority for this precedence chain; see
    # IDENTITY-PATH-001. Three modules previously carried their own copy.
    return backend_roots.iracing_root_path(defaults=_load_defaults())


def _garage61_base_url() -> str:
    garage = _load_defaults().get("garage61")
    if isinstance(garage, Mapping) and garage.get("base_url"):
        return str(garage["base_url"])
    return "https://garage61.net/api/v1"


def _garage61_global_visible_laps_approved() -> bool:
    garage = _load_defaults().get("garage61")
    return bool(
        isinstance(garage, Mapping)
        and garage.get("global_visible_laps_approved") is True
    )


def _positive_bounded_int(value: Any, name: str, maximum: int) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer between 1 and {maximum}")
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{name} must be an integer between 1 and {maximum}"
        ) from exc
    if normalized < 1 or normalized > maximum:
        raise ValueError(f"{name} must be between 1 and {maximum}")
    return normalized


def _target_rate(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("target_hz must be between 1 and 60")
    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("target_hz must be between 1 and 60") from exc
    if not math.isfinite(normalized) or not 1 <= normalized <= 60:
        raise ValueError("target_hz must be between 1 and 60")
    return normalized


def _resolved_root(root: str | os.PathLike[str] | None) -> Path:
    selected = local_path(root, "iRacing root") if root is not None else _default_iracing_root()
    try:
        return selected.resolve(strict=True)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"iRacing telemetry root does not exist: {selected}") from exc


def discover_sessions_workflow(
    *,
    root: str | os.PathLike[str] | None = None,
    limit: int = 20,
    races_only: bool = True,
) -> dict[str, Any]:
    """Discover grouped recordings, with the newest metadata-identified Race.

    ``limit`` applies to valid session groups. Malformed IBTs are returned in a
    separate bounded diagnostic list and cannot hide valid recordings.
    """

    bounded_limit = _positive_bounded_int(limit, "limit", 200)
    selected_root = _resolved_root(root)
    discovered = discover_sessions(selected_root, latest_only=False)
    errors = [item for item in discovered if item.get("kind") == "error"]
    all_sessions = [item for item in discovered if item.get("kind") == "session"]
    latest_race = next((item for item in all_sessions if item.get("is_race")), None)
    sessions = (
        [item for item in all_sessions if item.get("is_race")]
        if races_only
        else all_sessions
    )
    return {
        "root": str(selected_root),
        "selection_policy": "latest-race-by-session-metadata",
        "latest_race": latest_race,
        "session_count": len(sessions),
        "returned_session_count": min(len(sessions), bounded_limit),
        "error_count": len(errors),
        "sessions": sessions[:bounded_limit],
        "errors": errors[:bounded_limit],
    }


def companion_dashboard_workflow(
    *,
    root: str | os.PathLike[str] | None = None,
    archive_root: str | os.PathLike[str] | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Return a compact read-only backend snapshot for the companion UI."""

    bounded_limit = _positive_bounded_int(limit, "limit", 100)
    discovery = discover_sessions_workflow(
        root=root,
        limit=bounded_limit,
        races_only=True,
    )
    store = ArchiveStore(archive_root)
    recent_analyses = store.recent_analyses(limit=bounded_limit, phase="race")
    by_group: dict[str, dict[str, Any]] = {}
    by_subsession_phase: dict[tuple[str, str], dict[str, Any]] = {}
    by_source_phase: dict[tuple[str, str], dict[str, Any]] = {}
    legacy_by_source: dict[str, dict[str, Any]] = {}
    legacy_by_subsession: dict[str, dict[str, Any]] = {}
    for item in recent_analyses:
        group_key = str(item.get("session_group_id") or "").strip().casefold()
        if group_key and group_key not in by_group:
            by_group[group_key] = item
        subsession_key = str(item.get("subsession_id") or "").strip()
        phase_key = session_phase(item.get("session_phase") or item.get("sim_session_type"))
        if subsession_key and phase_key != "unknown" and (subsession_key, phase_key) not in by_subsession_phase:
            by_subsession_phase[(subsession_key, phase_key)] = item
        elif subsession_key and subsession_key not in legacy_by_subsession:
            legacy_by_subsession[subsession_key] = item
        source_key = str(item.get("source_path") or "").strip()
        if source_key:
            try:
                source_key = str(Path(source_key).resolve()).casefold()
            except (OSError, ValueError):
                source_key = source_key.casefold()
            if phase_key != "unknown":
                if (source_key, phase_key) not in by_source_phase:
                    by_source_phase[(source_key, phase_key)] = item
            elif source_key not in legacy_by_source:
                legacy_by_source[source_key] = item
    races: list[dict[str, Any]] = []
    for raw_session in discovery.get("sessions", ()) or ():
        if not isinstance(raw_session, Mapping):
            continue
        session = dict(raw_session)
        key = str(session.get("subsession_id") or "").strip()
        group_key = str(session.get("group_id") or "").strip().casefold()
        phase_key = session_phase(session.get("sim_session_type"))
        archived = by_group.get(group_key) if group_key else None
        if archived is None and key and phase_key != "unknown":
            archived = by_subsession_phase.get((key, phase_key))
        if archived is None:
            for source in session.get("files", ()) or ():
                try:
                    source_key = str(Path(str(source)).resolve()).casefold()
                except (OSError, ValueError):
                    source_key = str(source).casefold()
                archived = (
                    by_source_phase.get((source_key, phase_key))
                    if phase_key != "unknown"
                    else legacy_by_source.get(source_key)
                )
                if archived is not None:
                    break
        if archived is None and key and not group_key and phase_key == "unknown":
            archived = legacy_by_subsession.get(key)
        session["analysis_status"] = "analyzed" if archived else "not_analyzed"
        session["analysis"] = (
            {
                "analysis_id": archived.get("analysis_id"),
                "analyzed_at": archived.get("analyzed_at"),
                "analysis_path": archived.get("analysis_path"),
                "report_path": archived.get("report_path"),
                "race_card_path": archived.get("race_card_path"),
                "analysis_available": archived.get("analysis_available"),
                "report_available": archived.get("report_available"),
                "race_card_available": archived.get("race_card_available"),
                "source_available": archived.get("source_available"),
                "summary": archived.get("summary") or {},
            }
            if archived
            else None
        )
        races.append(session)

    request = _read_json_file(
        store.auth_dir / "garage61-api-request.json", dict, {}
    )
    try:
        credential_configured = credential_exists()
        credential_error = None
    except OSError as exc:
        credential_configured = False
        credential_error = str(exc)
    garage61 = {
        "credential_configured": credential_configured,
        "api_request_status": request.get("status") or (
            "configured" if credential_configured else "not_requested"
        ),
        "requested_permissions": request.get("requested_permissions") or [],
        "local_status_only": True,
        "credential_store_error": credential_error,
    }
    return {
        "ok": True,
        "contract_version": 1,
        "generated_at": utc_now(),
        "read_only": True,
        "latest_race": races[0] if races else None,
        "race_count": len(races),
        "races": races,
        "recent_analyses": recent_analyses,
        "tuning_packages": store.list_tuning_packages(limit=bounded_limit),
        "garage61": garage61,
        "capabilities": {
            "race_planning": True,
            "race_analysis": True,
            "race_card": True,
            "corner_phase_coaching": True,
            "groove_migration": True,
            "damage_repair_awareness": True,
            "starting_setup_package": True,
            "progressive_tuning": True,
            "native_event_search": True,
            "garage61_sync": credential_configured,
        },
    }


_INVENTORY_SUFFIXES: dict[str, frozenset[str]] = {
    "ibt": frozenset({".ibt"}),
    "replays": frozenset({".rpy"}),
    "setups": frozenset({".sto", ".htm", ".html"}),
    "lapfiles": frozenset({".olap", ".blap", ".lap"}),
    "configs": frozenset({".ini", ".cfg"}),
}


def _inventory_kind(path: Path) -> str | None:
    suffix = path.suffix.lower()
    for kind, suffixes in _INVENTORY_SUFFIXES.items():
        if suffix in suffixes:
            return kind
    # Future iRacing lap-file extensions still belong to the explicit
    # ``lapfiles`` tree; inventory them without attempting to interpret bytes.
    if any(part.lower() == "lapfiles" for part in path.parts):
        return "lapfiles"
    return None


def _inventory_record(path: Path, root: Path) -> dict[str, Any]:
    stat = path.stat()
    modified = _datetime.datetime.fromtimestamp(
        stat.st_mtime, tz=_datetime.timezone.utc
    ).isoformat()
    try:
        relative = path.relative_to(root)
    except ValueError:
        relative = path
    return {
        "path": str(path),
        "relative_path": relative.as_posix(),
        "name": path.name,
        "extension": path.suffix.lower(),
        "size_bytes": stat.st_size,
        "modified_unix": stat.st_mtime,
        "modified_utc": modified,
    }


_SAFE_APP_INI_KEYS = frozenset(
    {
        "irsdkenablemem",
        "irsdkautolog",
        "irsdkautologdisk",
        "irsdkenabledisk",
        "irsdklimitfilesize",
        "irsdklog360hz",
        "irsdklogallcars",
        "irsdklogdisk",
        "irsdkdisklogging",
        "irsdklogsetup",
        "irsdkutf8sessionstr",
        "telemetrydiskfile",
        "spoolreplay",
        "spoolonlyifdisk",
        "replaypatchremoteclients",
    }
)


def _app_ini_inventory(path: Path | None, root: Path) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {
            "exists": False,
            "path": str(root / "app.ini"),
            "settings": {},
        }
    settings: dict[str, Any] = {}
    normalized_settings: dict[str, str] = {}
    section = ""
    try:
        lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
        for raw_line in lines:
            line = raw_line.strip()
            if not line or line.startswith((";", "#")):
                continue
            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1].strip()
                continue
            if "=" not in line:
                continue
            key, value = (piece.strip() for piece in line.split("=", 1))
            normalized = re.sub(r"[^a-z0-9]", "", key.lower())
            if normalized in _SAFE_APP_INI_KEYS:
                clean_value = value.split(";", 1)[0].strip()
                settings[f"{section}.{key}" if section else key] = clean_value
                normalized_settings[normalized] = clean_value
        error = None
    except OSError as exc:
        error = f"{type(exc).__name__}: {exc}"
    result = {
        "exists": True,
        **_inventory_record(path, root),
        "settings": settings,
        "telemetry_logging": {
            "auto_disk": normalized_settings.get("irsdkautologdisk") == "1",
            "disk_enabled": normalized_settings.get("irsdkenabledisk") == "1",
            "memory_enabled": normalized_settings.get("irsdkenablemem") == "1",
            "all_cars": normalized_settings.get("irsdklogallcars") == "1",
            "setup_in_telemetry": normalized_settings.get("irsdklogsetup") == "1",
            "disk_sample_rate_hz": (
                360 if normalized_settings.get("irsdklog360hz") == "1" else 60
                if "irsdklog360hz" in normalized_settings
                else None
            ),
            "file_size_limit_enabled": normalized_settings.get("irsdklimitfilesize") == "1",
            "utf8_session_string": normalized_settings.get("irsdkutf8sessionstr") == "1",
        },
        "settings_note": (
            "Only telemetry/replay-related non-secret settings are read; "
            "the inventory never changes app.ini."
        ),
    }
    if error:
        result["read_error"] = error
    return result


def _reference_record(item: Mapping[str, Any], root: Path, marker: str) -> dict[str, Any]:
    result = dict(item)
    relative = Path(str(item.get("relative_path") or ""))
    parts = list(relative.parts)
    lowered = [part.lower() for part in parts]
    if marker in lowered:
        index = lowered.index(marker)
        if index + 1 < len(parts) - 1:
            result["content_folder"] = parts[index + 1]
    result["reference"] = str(root / relative)
    return result


_SENSITIVE_LOCAL_STATE_NAMES = frozenset(
    {
        "auth",
        "browser",
        "cache",
        "code cache",
        "cookies",
        "iracing-electron",
        "local storage",
        "session storage",
        "user data",
        "webcache",
    }
)


def _root_metadata_summary(
    label: str,
    path: Path,
    *,
    exclude_local_state: bool = False,
) -> dict[str, Any]:
    resolved = path.expanduser().resolve(strict=False)
    if not resolved.is_dir():
        return {
            "label": label,
            "path": str(resolved),
            "exists": False,
            "file_count": 0,
            "size_bytes": 0,
        }
    count = 0
    total_size = 0
    extensions: dict[str, int] = {}
    skipped_directories = 0
    errors = 0
    for directory, directory_names, file_names in os.walk(
        resolved, followlinks=False
    ):
        if exclude_local_state:
            retained = []
            for name in directory_names:
                if name.strip().lower() in _SENSITIVE_LOCAL_STATE_NAMES:
                    skipped_directories += 1
                else:
                    retained.append(name)
            directory_names[:] = retained
        for file_name in file_names:
            candidate = Path(directory) / file_name
            try:
                stat = candidate.stat()
            except OSError:
                errors += 1
                continue
            count += 1
            total_size += stat.st_size
            extension = candidate.suffix.lower() or "[none]"
            extensions[extension] = extensions.get(extension, 0) + 1
    return {
        "label": label,
        "path": str(resolved),
        "exists": True,
        "read_only_metadata_only": True,
        "file_count": count,
        "size_bytes": total_size,
        "extension_counts": dict(
            sorted(extensions.items(), key=lambda item: (-item[1], item[0]))[:25]
        ),
        "stat_errors": errors,
        "skipped_sensitive_state_directories": skipped_directories,
        "content_note": (
            "Files, including game .dat content, were only stat-counted; no content was parsed."
        ),
    }


def _known_iracing_roots(selected_root: Path) -> list[tuple[str, Path, bool]]:
    candidates: list[tuple[str, Path, bool]] = [
        ("documents", selected_root, False),
    ]
    # The installation root resolves through `backend_roots`, which reports
    # `None` rather than guessing `C:\Program Files (x86)` on a machine whose
    # system drive differs (IDENTITY-PATH-001). An unknown location is omitted
    # from the candidate list instead of being asserted. The explicitly
    # configured root and the machine defaults are both still probed, exactly
    # as before; only the invented literals are gone.
    install = backend_roots.resolve_install_root(defaults=_load_defaults())
    if install is not None and not install.is_default:
        candidates.append(("install_configured", install.path, False))
    program_x86 = os.environ.get("PROGRAMFILES(X86)")
    if program_x86:
        candidates.append(("install_x86", Path(program_x86) / "iRacing", False))
    program = os.environ.get("PROGRAMFILES")
    if program:
        candidates.append(("install", Path(program) / "iRacing", False))
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates.append(
            ("local_app_data", Path(local_app_data) / "iRacing", True)
        )
    result: list[tuple[str, Path, bool]] = []
    seen: set[str] = set()
    for label, path, sensitive in candidates:
        key = os.path.normcase(str(path.expanduser().resolve(strict=False)))
        if key in seen:
            continue
        seen.add(key)
        result.append((label, path, sensitive))
    return result


def iracing_local_inventory_workflow(
    *,
    root: str | os.PathLike[str] | None = None,
    recent_limit: int = 20,
    include_known_roots: bool = True,
) -> dict[str, Any]:
    """Inventory useful local iRacing artifacts without opening binary data.

    The operation is read-only: it records path/stat metadata for IBT, replay,
    setup, lap, and configuration files, and reads only a small safe whitelist
    of telemetry/replay settings from ``app.ini``.
    """

    selected_root = _resolved_root(root)
    limit = _positive_bounded_int(recent_limit, "recent_limit", 200)
    records: dict[str, list[dict[str, Any]]] = {
        kind: [] for kind in _INVENTORY_SUFFIXES
    }
    errors: list[dict[str, str]] = []
    all_file_count = 0
    all_size_bytes = 0

    def on_walk_error(exc: OSError) -> None:
        errors.append(
            {
                "path": str(getattr(exc, "filename", "") or selected_root),
                "error_type": type(exc).__name__,
                "message": str(exc),
            }
        )

    for directory, directory_names, file_names in os.walk(
        selected_root, followlinks=False, onerror=on_walk_error
    ):
        directory_names.sort(key=str.lower)
        file_names.sort(key=str.lower)
        base = Path(directory)
        for file_name in file_names:
            path = base / file_name
            try:
                stat = path.stat()
            except OSError as exc:
                errors.append(
                    {
                        "path": str(path),
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                    }
                )
                continue
            all_file_count += 1
            all_size_bytes += stat.st_size
            kind = _inventory_kind(path)
            if kind is None:
                continue
            try:
                records[kind].append(_inventory_record(path, selected_root))
            except OSError as exc:
                errors.append(
                    {
                        "path": str(path),
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                    }
                )

    for values in records.values():
        values.sort(
            key=lambda item: (
                float(item.get("modified_unix") or 0.0),
                str(item.get("path") or ""),
            ),
            reverse=True,
        )
    app_ini = next(
        (
            Path(item["path"])
            for item in records["configs"]
            if str(item.get("name", "")).lower() == "app.ini"
            and Path(item["path"]).parent == selected_root
        ),
        None,
    )
    recent = {kind: values[:limit] for kind, values in records.items()}
    setup_references = [
        _reference_record(item, selected_root, "setups")
        for item in records["setups"][:limit]
    ]
    replay_references = [
        _reference_record(item, selected_root, "replay")
        for item in records["replays"][:limit]
    ]
    if include_known_roots:
        known_roots = []
        for label, path, exclude_local_state in _known_iracing_roots(selected_root):
            if label == "documents":
                known_roots.append(
                    {
                        "label": label,
                        "path": str(selected_root),
                        "exists": True,
                        "read_only_metadata_only": True,
                        "file_count": all_file_count,
                        "size_bytes": all_size_bytes,
                    }
                )
            else:
                known_roots.append(
                    _root_metadata_summary(
                        label,
                        path,
                        exclude_local_state=exclude_local_state,
                    )
                )
    else:
        known_roots = [
            {
                "label": "documents",
                "path": str(selected_root),
                "exists": True,
                "read_only_metadata_only": True,
                "file_count": all_file_count,
                "size_bytes": all_size_bytes,
            }
        ]
    return {
        "root": str(selected_root),
        "read_only": True,
        "generated_at": utc_now(),
        "counts": {kind: len(values) for kind, values in records.items()},
        "total_recognized_files": sum(len(values) for values in records.values()),
        "all_files_below_selected_root": all_file_count,
        "all_bytes_below_selected_root": all_size_bytes,
        "known_roots": known_roots,
        "recent_limit": limit,
        "recent": recent,
        "references": {
            "telemetry": records["ibt"][:limit],
            "setups": setup_references,
            "replays": replay_references,
            "lapfiles": records["lapfiles"][:limit],
        },
        "app_ini": _app_ini_inventory(app_ini, selected_root),
        "errors": errors[:limit],
        "error_count": len(errors),
    }


# Descriptive aliases keep this helper easy to call from future CLI/MCP
# surfaces without changing its read-only contract.
iracing_data_inventory_workflow = iracing_local_inventory_workflow
inventory_iracing_data_workflow = iracing_local_inventory_workflow


def _looks_like_path(selector: str) -> bool:
    return (
        selector.lower().endswith(".ibt")
        or any(separator in selector for separator in ("/", "\\"))
        or bool(re.match(r"^[A-Za-z]:", selector))
    )


def _file_selection(path: Path) -> dict[str, Any]:
    try:
        resolved = path.expanduser().resolve(strict=True)
    except FileNotFoundError as exc:
        raise SessionSelectionError(f"IBT file does not exist: {path}") from exc
    if not resolved.is_file() or resolved.suffix.lower() != ".ibt":
        raise SessionSelectionError(f"Session selector is not an IBT file: {resolved}")
    metadata = scan_ibt(resolved)
    return {
        "selector_type": "file",
        "selector": str(resolved),
        "group_id": None,
        "subsession_id": metadata.get("subsession_id"),
        "session_id": metadata.get("session_id"),
        "sim_session_num": metadata.get("sim_session_num"),
        "sim_session_type": metadata.get("sim_session_type"),
        "is_race": bool(metadata.get("is_race")),
        "track_name": metadata.get("track_name"),
        "track_config_name": metadata.get("track_config_name"),
        "car_path": metadata.get("car_path"),
        "files": [str(resolved)],
    }


def _subsession_value(selector: str) -> str | None:
    normalized = selector.strip()
    prefixed = re.fullmatch(r"(?i)(?:subsession|sub)\s*[:=#]?\s*(\d+)", normalized)
    if prefixed:
        return str(int(prefixed.group(1)))
    if normalized.isdigit():
        return str(int(normalized))
    return None


def _selection_archive_identity(selection: Mapping[str, Any]) -> dict[str, str]:
    """Return the stable event-phase identity that qualifies durable analysis data."""

    subsession_value = selection.get("subsession_id")
    sim_session_value = selection.get("sim_session_num")
    subsession = (
        str(subsession_value).strip()
        if subsession_value not in (None, "")
        else ""
    )
    sim_session = (
        str(sim_session_value).strip()
        if sim_session_value not in (None, "")
        else ""
    )
    raw_group = str(selection.get("group_id") or "").strip()
    group = (
        f"subsession:{subsession}:{sim_session}"
        if subsession and sim_session
        else raw_group
    )
    return {
        "group_id": group.casefold(),
        "subsession_id": subsession,
        "sim_session_num": sim_session,
        "session_phase": session_phase(selection.get("sim_session_type")),
    }


def _analysis_cache_identity(
    source_fingerprints: Sequence[Mapping[str, Any]],
    rate: float,
    pipeline_sha256: str,
    selection: Mapping[str, Any],
) -> str:
    return stable_hash(
        {
            "cache_schema": 4,
            "source_sha256": [item["sha256"] for item in source_fingerprints],
            "target_hz": rate,
            "pipeline_sha256": pipeline_sha256,
            "session": _selection_archive_identity(selection),
        },
        64,
    )


def _phase_qualified_analysis_id(
    analysis_id: Any,
    selection: Mapping[str, Any],
) -> str:
    base = str(analysis_id or "").strip()
    identity = _selection_archive_identity(selection)
    if not identity["group_id"] and identity["session_phase"] == "unknown":
        return base
    return stable_hash(
        {"base_analysis_id": base, "session": identity},
        24,
    )


def _resolve_session_selection(
    selector: str,
    iracing_root: str | os.PathLike[str] | None,
) -> dict[str, Any]:
    normalized = str(selector or "latest").strip()
    if not normalized:
        normalized = "latest"

    path_candidate = Path(normalized).expanduser()
    if _looks_like_path(normalized) or (
        path_candidate.is_file() and path_candidate.suffix.lower() == ".ibt"
    ):
        return _file_selection(path_candidate)

    selected_root = _resolved_root(iracing_root)
    discovered = discover_sessions(selected_root, latest_only=False)
    sessions = [item for item in discovered if item.get("kind") == "session"]
    errors = [item for item in discovered if item.get("kind") == "error"]

    if normalized.lower() == "latest":
        match = next((item for item in sessions if item.get("is_race")), None)
        if match is None:
            detail = f" ({len(errors)} malformed IBT file(s) were skipped)" if errors else ""
            raise SessionSelectionError(
                f"No recorded Race session was found below {selected_root}{detail}. "
                "Confirm iRacing disk telemetry logging is enabled."
            )
        return {**match, "selector_type": "latest", "selector": "latest"}

    group_match = next(
        (
            item
            for item in sessions
            if str(item.get("group_id") or "").casefold() == normalized.casefold()
        ),
        None,
    )
    if group_match is not None:
        return {
            **group_match,
            "selector_type": "group_id",
            "selector": str(group_match.get("group_id") or normalized),
        }

    subsession = _subsession_value(normalized)
    if subsession is None:
        raise SessionSelectionError(
            "Session selector must be 'latest', an existing .ibt path, a discovery group ID, or a SubSessionID."
        )
    matches = [
        item
        for item in sessions
        if str(item.get("subsession_id")) == subsession
    ]
    if not matches:
        raise SessionSelectionError(
            f"No telemetry session with SubSessionID {subsession} was found below {selected_root}."
        )
    # A SubSessionID can contain practice, qualifying, and Race sim sessions.
    # Prefer the Race group, but allow an explicit ID to select the newest
    # available group when the recording metadata has no Race classification.
    match = next((item for item in matches if item.get("is_race")), matches[0])
    return {
        **match,
        "selector_type": "subsession_id",
        "selector": subsession,
    }


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _available_channel_names(metadata: Mapping[str, Any]) -> set[str]:
    result: set[str] = set()
    for variable in metadata.get("variables", ()) or ():
        if isinstance(variable, Mapping) and variable.get("name"):
            result.add(str(variable["name"]))
    return result


def _file_signature(path: str | os.PathLike[str]) -> tuple[int, int]:
    stat = Path(path).stat()
    return stat.st_size, stat.st_mtime_ns


_IBT_QUIET_SECONDS = 2.0


def _assert_finalized_ibt(
    raw_path: str | os.PathLike[str], metadata: Mapping[str, Any]
) -> None:
    """Reject incomplete/growing disk recordings before analysis.

    Completed IBTs end exactly after the declared telemetry records. A larger
    file normally means iRacing has appended part of the next record without
    updating the disk subheader yet. The brief quiet-age gate avoids racing the
    final header update.
    """

    header = metadata.get("header") if isinstance(metadata.get("header"), Mapping) else {}
    disk = metadata.get("disk") if isinstance(metadata.get("disk"), Mapping) else {}
    file_size = metadata.get("file_size")
    buffer_offset = metadata.get("buffer_offset")
    buffer_length = header.get("buffer_length")
    record_count = metadata.get("record_count", disk.get("session_record_count"))
    numbers = (file_size, buffer_offset, buffer_length, record_count)
    if not all(isinstance(value, int) and not isinstance(value, bool) for value in numbers):
        # Test doubles and legacy callers may not supply structural metadata;
        # the low-level reader still performs its normal bounds checks.
        return
    if int(record_count) <= 0:
        raise WorkflowError(
            f"The selected IBT has no finalized records: {Path(raw_path).resolve()}."
        )
    declared_end = int(buffer_offset) + int(record_count) * int(buffer_length)
    if int(file_size) != declared_end:
        raise WorkflowError(
            "The selected IBT is not finalized (declared telemetry ends at byte "
            f"{declared_end:,}, file size is {int(file_size):,}): "
            f"{Path(raw_path).resolve()}. Wait for the recording to finish and retry."
        )
    modified = _finite_number(metadata.get("mtime_unix"))
    if modified is not None and time.time() - modified < _IBT_QUIET_SECONDS:
        raise WorkflowError(
            "The selected IBT was modified too recently and may still be recording: "
            f"{Path(raw_path).resolve()}. Wait a few seconds and retry."
        )


def _catalog_signature(variable: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: variable.get(key)
        for key in ("type", "type_code", "count", "count_as_time", "unit", "byte_size")
    }


def _catalog_summary(source_catalogs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    definitions: dict[str, list[dict[str, Any]]] = {}
    name_sets: list[set[str]] = []
    for source in source_catalogs:
        names: set[str] = set()
        for raw in source.get("recorded_channel_catalog", ()) or ():
            if not isinstance(raw, Mapping) or not raw.get("name"):
                continue
            name = str(raw["name"])
            names.add(name)
            signature = _catalog_signature(raw)
            signature["source"] = source.get("path")
            definitions.setdefault(name, []).append(signature)
        name_sets.append(names)
    union_names = sorted(set().union(*name_sets)) if name_sets else []
    intersection_names = sorted(set.intersection(*name_sets)) if name_sets else []
    conflicts: list[dict[str, Any]] = []
    for name in union_names:
        variants: dict[str, dict[str, Any]] = {}
        sources: dict[str, list[str]] = {}
        for definition in definitions.get(name, ()):  # source is provenance, not signature
            comparable = {key: value for key, value in definition.items() if key != "source"}
            key = json.dumps(comparable, sort_keys=True, default=str)
            variants[key] = comparable
            sources.setdefault(key, []).append(str(definition.get("source") or ""))
        if len(variants) > 1:
            conflicts.append(
                {
                    "name": name,
                    "definitions": [
                        {**variants[key], "sources": sorted(sources[key])}
                        for key in sorted(variants)
                    ],
                }
            )
    return {
        "source_count": len(source_catalogs),
        "union_count": len(union_names),
        "intersection_count": len(intersection_names),
        "union_channels": union_names,
        "intersection_channels": intersection_names,
        "conflict_count": len(conflicts),
        "conflicts": conflicts,
    }


def _source_catalog_record(
    raw_path: str | os.PathLike[str], table: Mapping[str, Any]
) -> dict[str, Any]:
    catalog = [
        dict(item)
        for item in (table.get("available_variables") or ())
        if isinstance(item, Mapping) and item.get("name")
    ]
    loaded = sorted(str(name) for name in (table.get("samples") or {}))
    recorded = sorted(str(item["name"]) for item in catalog)
    metadata = table.get("metadata") if isinstance(table.get("metadata"), Mapping) else {}
    return {
        "path": str(Path(raw_path).resolve()),
        "file_size": metadata.get("file_size"),
        "modified_ns": Path(raw_path).stat().st_mtime_ns,
        "record_count": table.get("source_record_count"),
        "native_tick_rate_hz": table.get("native_tick_rate_hz"),
        "analysis_sample_rate_hz": table.get("sample_rate_hz"),
        "recorded_channel_catalog": catalog,
        "loaded_channels": loaded,
        "unloaded_channels": sorted(set(recorded) - set(loaded)),
        "catalog_sha256": stable_hash(catalog, 64),
    }


def _load_analysis_telemetry(
    source_paths: Sequence[str], target_hz: float
) -> dict[str, Any]:
    """Load and time-merge relevant channels from one grouped sim session."""

    if not source_paths:
        raise SessionSelectionError("The selected session contains no IBT files.")
    loaded: list[dict[str, Any]] = []
    for raw_path in source_paths:
        before = _file_signature(raw_path)
        metadata = scan_ibt(raw_path)
        _assert_finalized_ibt(raw_path, metadata)
        available = _available_channel_names(metadata)
        requested: Sequence[str] | None = (
            [name for name in ANALYSIS_CHANNELS if name in available]
            if available
            else None
        )
        table = load_telemetry(raw_path, channels=requested, target_hz=target_hz)
        after = _file_signature(raw_path)
        if after != before:
            raise WorkflowError(
                "The selected IBT changed while it was being read and is likely still "
                f"recording: {Path(raw_path).resolve()}. Wait for the session to finish and retry."
            )
        if int(table.get("sample_count") or 0) > 0:
            source_catalog = _source_catalog_record(raw_path, table)
            table["source_catalogs"] = [source_catalog]
            table["catalog_summary"] = _catalog_summary([source_catalog])
            table["source_signatures"] = [
                {"path": source_catalog["path"], "size": after[0], "modified_ns": after[1]}
            ]
            table["sample_provenance"] = [
                {
                    "source_file_index": 0,
                    "source_path": source_catalog["path"],
                    "native_record_index": int(native_index),
                }
                for native_index in (
                    table.get("sample_indices")
                    or range(int(table.get("sample_count") or 0))
                )
            ]
            loaded.append(table)
    if not loaded:
        raise WorkflowError("The selected IBT recording contains no telemetry samples.")
    if len(loaded) == 1:
        return loaded[0]

    channel_names: list[str] = []
    for preferred in ANALYSIS_CHANNELS:
        if any(preferred in (table.get("samples") or {}) for table in loaded):
            channel_names.append(preferred)
    for table in loaded:
        for name in (table.get("samples") or {}):
            if name not in channel_names:
                channel_names.append(str(name))

    references: list[tuple[int, float, int, int]] = []
    for file_index, table in enumerate(loaded):
        samples = table.get("samples") or {}
        count = int(table.get("sample_count") or 0)
        time_values = samples.get("SessionTime") or samples.get("SessionTimeOfDay") or ()
        for sample_index in range(count):
            time_value = (
                _finite_number(time_values[sample_index])
                if sample_index < len(time_values)
                else None
            )
            references.append(
                (
                    0 if time_value is not None else 1,
                    time_value if time_value is not None else float(file_index),
                    file_index,
                    sample_index,
                )
            )
    references.sort()

    merged: dict[str, list[Any]] = {name: [] for name in channel_names}
    merged_provenance: list[dict[str, Any]] = []
    previous_time: float | None = None
    previous_output_index: int | None = None
    for has_no_time, time_value, file_index, sample_index in references:
        duplicate = (
            has_no_time == 0
            and previous_time is not None
            and abs(time_value - previous_time) <= 1e-9
        )
        table_samples = loaded[file_index].get("samples") or {}
        if not duplicate:
            for name in channel_names:
                values = table_samples.get(name) or ()
                merged[name].append(values[sample_index] if sample_index < len(values) else None)
            previous_output_index = len(next(iter(merged.values()))) - 1 if merged else None
            source_provenance = loaded[file_index].get("sample_provenance") or ()
            provenance = (
                dict(source_provenance[sample_index])
                if sample_index < len(source_provenance)
                and isinstance(source_provenance[sample_index], Mapping)
                else {
                    "source_file_index": file_index,
                    "source_path": str(source_paths[file_index]),
                    "native_record_index": sample_index,
                }
            )
            provenance["source_file_index"] = file_index
            merged_provenance.append(provenance)
            previous_time = time_value if has_no_time == 0 else None
            continue
        # Overlapping disk recordings sometimes contain the same SessionTime.
        # Retain the first non-null value per channel instead of double-counting
        # the sample in lap, fuel, or control integrations.
        assert previous_output_index is not None
        for name in channel_names:
            values = table_samples.get(name) or ()
            candidate = values[sample_index] if sample_index < len(values) else None
            if merged[name][previous_output_index] is None and candidate is not None:
                merged[name][previous_output_index] = candidate
        source_provenance = loaded[file_index].get("sample_provenance") or ()
        contributor = (
            dict(source_provenance[sample_index])
            if sample_index < len(source_provenance)
            and isinstance(source_provenance[sample_index], Mapping)
            else {
                "source_file_index": file_index,
                "source_path": str(source_paths[file_index]),
                "native_record_index": sample_index,
            }
        )
        contributor["source_file_index"] = file_index
        merged_provenance[previous_output_index].setdefault("contributors", []).append(contributor)

    def info_size(table: Mapping[str, Any]) -> int:
        return len(json.dumps(table.get("session_info") or {}, default=str))

    representative = max(loaded, key=info_size)
    metadata = dict(representative.get("metadata") or {})
    metadata["merged_file_count"] = len(loaded)
    metadata["merged_sample_count"] = len(next(iter(merged.values()))) if merged else 0
    variables_by_name: dict[str, Mapping[str, Any]] = {}
    available_variables_by_name: dict[str, Mapping[str, Any]] = {}
    source_catalogs: list[dict[str, Any]] = []
    source_signatures: list[dict[str, Any]] = []
    for table in loaded:
        for variable in table.get("variables", ()) or ():
            if isinstance(variable, Mapping) and variable.get("name"):
                variables_by_name.setdefault(str(variable["name"]), variable)
        for variable in table.get("available_variables", ()) or ():
            if isinstance(variable, Mapping) and variable.get("name"):
                available_variables_by_name.setdefault(str(variable["name"]), variable)
        source_catalogs.extend(
            dict(item)
            for item in (table.get("source_catalogs") or ())
            if isinstance(item, Mapping)
        )
        source_signatures.extend(
            dict(item)
            for item in (table.get("source_signatures") or ())
            if isinstance(item, Mapping)
        )
    sample_rate = max(
        (_finite_number(table.get("sample_rate_hz")) or 0.0 for table in loaded),
        default=target_hz,
    )
    native_rate = max(
        (_finite_number(table.get("native_tick_rate_hz")) or 0.0 for table in loaded),
        default=sample_rate,
    )
    return {
        "metadata": metadata,
        "session_info": representative.get("session_info") or {},
        "variables": list(variables_by_name.values()),
        "available_variables": list(available_variables_by_name.values()),
        "source_catalogs": source_catalogs,
        "catalog_summary": _catalog_summary(source_catalogs),
        "source_signatures": source_signatures,
        "samples": merged,
        "sample_provenance": merged_provenance,
        "sample_rate_hz": sample_rate or target_hz,
        "native_tick_rate_hz": native_rate or sample_rate or target_hz,
        "sample_count": len(next(iter(merged.values()))) if merged else 0,
        "source_record_count": sum(
            int(table.get("source_record_count") or 0) for table in loaded
        ),
        "channel_selection": {
            "mode": "selected",
            "available_count": len(available_variables_by_name),
            "decoded_count": len(variables_by_name),
        },
    }


def _analysis_pipeline_sha256() -> str:
    """Fingerprint only the decode/merge contract that can change analysis JSON.

    Race Card rendering, dashboard fields, and optional network workflows must
    not invalidate an otherwise identical multi-megabyte telemetry analysis.
    """

    # This used to hash SOURCE TEXT - the analyzer bundle's raw bytes plus
    # inspect.getsource of six helpers - which meant a reformat, a renamed local, or
    # a corrected comment rotated the fingerprint and orphaned every cached
    # analysis. On a real archive that produced 19 live generations holding 2.42 GB,
    # none of them reachable. The docstring above already stated the correct intent;
    # the implementation contradicted it.
    #
    # Fingerprint the declared CONTRACT instead. A change to analysis math must be
    # accompanied by a bump to ANALYSIS_SCHEMA_VERSION or ANALYSIS_PROFILE_VERSION,
    # and test_analysis_pipeline_identity enforces that: it fails when the analyzer
    # bundle changes without a deliberate decision, so the maintainer must either
    # bump a version (math changed, caches must fall) or re-record the digest
    # (formatting only, caches stay valid).
    return stable_hash(
        {
            "schema": 2,
            "analysis_channels": ANALYSIS_CHANNELS,
            "analysis_schema_version": ANALYSIS_SCHEMA_VERSION,
            "analysis_profile_version": ANALYSIS_PROFILE_VERSION,
        },
        64,
    )


# Recorded digest of the analyzer bundle as of the last deliberate review of what
# the pipeline fingerprint above covers. This module is not itself part of
# ANALYZER_SOURCE_FILES, so updating this line cannot change the value it records.
#
# When the guard test fails, exactly one of these is true, and you must choose:
#   - analysis math or output changed  -> bump ANALYSIS_SCHEMA_VERSION or
#     ANALYSIS_PROFILE_VERSION in analysis_engine.py, then re-record below.
#     Cached analyses become unreachable, which is correct: they are wrong.
#   - only formatting, comments, typing or naming changed -> re-record below
#     alone. Cached analyses stay valid, which is the whole point.
ANALYZER_BUNDLE_REVIEWED_SHA256 = (
    "b7bc8e0aa9eaa6af524e20f40bcda409754d01e196572696cc869759c483346c"
)


def _read_json_file(path: Path, expected: type, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkflowError(f"Cached JSON is unreadable: {path}: {exc}") from exc
    if not isinstance(value, expected):
        raise WorkflowError(
            f"Cached JSON has the wrong shape: {path} (expected {expected.__name__})"
        )
    return value


def _bundle_components(store: ArchiveStore, context: Mapping[str, str]) -> dict[str, Any]:
    path = store.cache_path(context)
    notes_path = path / "knowledge.md"
    notes = notes_path.read_text(encoding="utf-8") if notes_path.exists() else None
    return {
        "facts": _read_json_file(path / "facts.json", dict, {}),
        "sources": _read_json_file(path / "sources.json", list, []),
        "garage61": _read_json_file(path / "garage61" / "index.json", dict, {}),
        "track_shape": _read_json_file(path / "track" / "shape.json", dict, {}),
        "notes_markdown": notes,
    }


def _knowledge_for_report(
    store: ArchiveStore,
    context: Mapping[str, str],
    cache_status: Mapping[str, Any],
) -> dict[str, Any] | None:
    # ``incomplete`` is still a fully validated bundle under the storage
    # contract; it may contain useful Garage61 comparisons while primary web
    # research remains outstanding. Invalid/stale/missing bundles are never
    # loaded into a report.
    if cache_status.get("state") not in {"fresh", "incomplete"}:
        return None
    components = _bundle_components(store, context)
    garage61 = components["garage61"]
    if (
        garage61.get("target_derivation_version")
        != _GARAGE61_TARGET_DERIVATION_VERSION
    ):
        # Older bundles may contain targets selected by the former recursive
        # metadata scan. Preserve independent facts/track research, but never
        # republish those Garage61 comparisons as current evidence.
        garage61 = {}
    result: dict[str, Any] = {
        "facts": components["facts"],
        "garage61": garage61,
        "track_shape": components["track_shape"],
    }
    if components["notes_markdown"]:
        result["notes"] = components["notes_markdown"]
    return result


def _local_race_file_references(
    iracing_root: Path,
    analysis: Mapping[str, Any],
    store: ArchiveStore,
) -> dict[str, Any]:
    """Join safe saved-setup/replay references to the embedded race identity."""

    documents_root = (
        iracing_root.parent
        if iracing_root.name.lower() in {"telemetry", "replay", "setups", "lapfiles"}
        else iracing_root
    )
    identity = analysis.get("identity") or {}
    setup_name = Path(str(identity.get("setup_name") or "").replace("\\", "/")).name
    identity_car_path = str(identity.get("car_path") or "").replace("\\", "/").strip("/")
    setup_paths: list[Path] = []
    setup_root = documents_root / "setups"
    if setup_name and setup_root.is_dir():
        try:
            resolved_setup_root = setup_root.resolve()
            if identity_car_path:
                exact_candidate = (
                    resolved_setup_root / identity_car_path / setup_name
                ).resolve()
                if (
                    resolved_setup_root in exact_candidate.parents
                    and exact_candidate.is_file()
                    and exact_candidate.suffix.casefold() == ".sto"
                ):
                    setup_paths.append(exact_candidate)
            for path in setup_root.rglob("*.sto"):
                if len(setup_paths) >= 20:
                    break
                if (
                    path.name.casefold() == setup_name.casefold()
                    and path.is_file()
                    and path.resolve() not in setup_paths
                ):
                    setup_paths.append(path.resolve())
        except OSError:
            setup_paths = []
    def setup_car_directory(path: Path) -> str:
        try:
            return path.parent.resolve().relative_to(setup_root.resolve()).as_posix().strip("/")
        except (OSError, ValueError):
            return path.parent.name

    setup_paths.sort(
        key=lambda path: (
            0
            if identity_car_path
            and setup_car_directory(path).casefold() == identity_car_path.casefold()
            else 1,
            str(path).casefold(),
        )
    )
    try:
        setup_fingerprints = store.source_fingerprints(setup_paths)
    except OSError:
        setup_fingerprints = []
    for fingerprint in setup_fingerprints:
        candidate = Path(str(fingerprint.get("path") or ""))
        car_directory = setup_car_directory(candidate)
        exact_car_path = bool(
            identity_car_path and car_directory.casefold() == identity_car_path.casefold()
        )
        fingerprint["car_directory"] = car_directory or None
        fingerprint["identity_car_path_match"] = exact_car_path
        fingerprint["match"] = (
            "exact-filename-and-car-path"
            if exact_car_path
            else "exact-filename-other-car-directory"
        )
    preferred_setup = next(
        (
            dict(item)
            for item in setup_fingerprints
            if item.get("identity_car_path_match") is True
        ),
        None,
    )

    replay_matches: list[dict[str, Any]] = []
    subsession = str(identity.get("subsession_id") or "").strip()
    replay_root = documents_root / "replay"
    expected_replay_stem = f"subses{subsession}".casefold() if subsession else ""
    if expected_replay_stem and replay_root.is_dir():
        try:
            for path in replay_root.glob("*.rpy"):
                if path.stem.casefold() != expected_replay_stem:
                    continue
                stat = path.stat()
                replay_matches.append(
                    {
                        "path": str(path.resolve()),
                        "size_bytes": stat.st_size,
                        "modified_utc": _datetime.datetime.fromtimestamp(
                            stat.st_mtime, tz=_datetime.timezone.utc
                        ).isoformat(),
                        "match": "exact-subsession-id",
                    }
                )
        except OSError:
            replay_matches = []
    return {
        "iracing_documents_root": str(documents_root),
        "embedded_setup_is_authority": True,
        "saved_setup_name": setup_name or None,
        "saved_setup_matches": setup_fingerprints,
        "preferred_saved_setup": preferred_setup,
        "saved_setup_status": (
            "matched_by_exact_filename_and_car_path"
            if preferred_setup
            else "matched_by_filename_only_other_car_directory"
            if setup_fingerprints
            else "not_saved_locally_or_built_in"
        ),
        "replay_matches": replay_matches,
        "replay_status": "matched_by_subsession_id" if replay_matches else "not_found",
    }


def _write_json_cache(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, default=str)
        + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _elapsed_ms(start: float, end: float) -> float:
    """Return bounded monotonic stage timing for companion diagnostics."""

    return round(min(86_400_000.0, max(0.0, (end - start) * 1000.0)), 3)


def analyze_race_workflow(
    *,
    selector: str = "latest",
    iracing_root: str | os.PathLike[str] | None = None,
    archive_root: str | os.PathLike[str] | None = None,
    target_hz: float = 20,
) -> dict[str, Any]:
    """Resolve, analyze, report, and archive one local recording offline."""

    workflow_started = time.perf_counter()
    rate = _target_rate(target_hz)
    selection = _resolve_session_selection(selector, iracing_root)
    source_paths = [str(Path(path).resolve()) for path in selection.get("files", ())]
    if not source_paths:
        raise SessionSelectionError("The selected session contains no IBT files.")
    store = ArchiveStore(archive_root)
    initial_signatures: dict[str, tuple[int, int]] = {}
    for path in source_paths:
        initial_signatures[path] = _file_signature(path)
        _assert_finalized_ibt(path, scan_ibt(path))
    source_fingerprints = store.source_fingerprints(source_paths)
    fingerprints_by_path = {
        str(Path(item["path"]).resolve()): item for item in source_fingerprints
    }
    for path, before in initial_signatures.items():
        fingerprint_record = fingerprints_by_path.get(path)
        if fingerprint_record is None or before != (
            int(fingerprint_record.get("size") or -1),
            int(fingerprint_record.get("modified_ns") or -1),
        ):
            raise WorkflowError(
                "The selected IBT changed during SHA-256 verification: "
                f"{path}. No analysis artifact was written."
            )
    raw_archive = store.archive_raw_telemetry(source_fingerprints)
    selection_verified = time.perf_counter()
    pipeline_sha256 = _analysis_pipeline_sha256()
    analysis_cache_key = _analysis_cache_identity(
        source_fingerprints,
        rate,
        pipeline_sha256,
        selection,
    )
    analysis_cache_path = store.root / "analysis-cache" / f"{analysis_cache_key}.json"
    cached_payload: Any = None
    if analysis_cache_path.is_file():
        try:
            cached_payload = json.loads(analysis_cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cached_payload = None
    cached_analysis = (
        cached_payload.get("analysis")
        if isinstance(cached_payload, Mapping)
        and cached_payload.get("cache_key") == analysis_cache_key
        and isinstance(cached_payload.get("analysis"), Mapping)
        else None
    )
    cache_valid = bool(
        cached_analysis is not None
        and cached_payload.get("analysis_sha256") == stable_hash(cached_analysis, 64)
    )
    if cache_valid:
        analysis = dict(cached_analysis)
        analysis["analyzed_at"] = utc_now()
        analysis_cache_hit = True
    else:
        telemetry = _load_analysis_telemetry(source_paths, rate)
        for signature in telemetry.get("source_signatures", ()) or ():
            if not isinstance(signature, Mapping) or not signature.get("path"):
                continue
            path = str(Path(str(signature["path"])).resolve())
            fingerprint_record = fingerprints_by_path.get(path)
            if fingerprint_record is None or (
                int(fingerprint_record.get("size") or -1)
                != int(signature.get("size") or -2)
                or int(fingerprint_record.get("modified_ns") or -1)
                != int(signature.get("modified_ns") or -2)
            ):
                raise WorkflowError(
                    "The selected IBT changed between SHA-256 verification and "
                    f"telemetry decoding: {path}. No analysis artifact was written."
                )
        analysis = analyze_telemetry(
            telemetry,
            source_paths=source_paths,
            source_fingerprints=source_fingerprints,
            analysis_profile={
                "target_hz": rate,
                "pipeline_sha256": pipeline_sha256,
            },
        )
        if not isinstance(analysis, Mapping):
            raise WorkflowError("The telemetry analysis engine returned an invalid result.")
        analysis = dict(analysis)
        analysis["analysis_id"] = _phase_qualified_analysis_id(
            analysis.get("analysis_id"),
            selection,
        )
        _write_json_cache(
            analysis_cache_path,
            {
                "cache_schema": 4,
                "cache_key": analysis_cache_key,
                "created_at": utc_now(),
                "analysis_sha256": stable_hash(analysis, 64),
                "analysis": analysis,
            },
        )
        analysis_cache_hit = False
    source = dict(analysis.get("source") or {})
    source["selection"] = dict(selection)
    source["analysis_cache"] = {
        "hit": analysis_cache_hit,
        "key": analysis_cache_key,
        "path": str(analysis_cache_path),
    }

    source["fingerprints"] = source_fingerprints
    source["raw_source_policy"] = {
        "mode": "content-addressed-portable-copy",
        "durably_copied": raw_archive.get("durably_copied") is True,
        "archive": raw_archive,
        "note": "Verified raw IBTs are copied into portable append-only storage; originals remain untouched.",
    }
    if iracing_root is not None:
        selected_iracing_root = _resolved_root(iracing_root)
    else:
        source_parent = Path(source_paths[0]).parent
        selected_iracing_root = (
            source_parent.parent
            if source_parent.name.lower() == "telemetry"
            else source_parent
        )
    source["related_local_files"] = _local_race_file_references(
        selected_iracing_root,
        analysis,
        store,
    )
    for path, fingerprint_record in fingerprints_by_path.items():
        if _file_signature(path) != (
            int(fingerprint_record.get("size") or -1),
            int(fingerprint_record.get("modified_ns") or -1),
        ):
            raise WorkflowError(
                f"The selected IBT changed before artifact persistence: {path}. No report was written."
            )
    analysis["source"] = source
    live_replay = store.live_replay_for_analysis(analysis)
    recorded_replay = analysis.get("race_replay") or {}
    if isinstance(live_replay, Mapping) and (
        not isinstance(recorded_replay, Mapping)
        or recorded_replay.get("status") == "unavailable"
        or int(live_replay.get("frame_count") or 0) > int(recorded_replay.get("frame_count") or 0)
    ):
        analysis["race_replay"] = dict(live_replay)
    geometry = analysis.get("track_geometry")
    if isinstance(geometry, Mapping):
        analysis["track_geometry"] = store.cache_track_geometry(geometry)
    tire_learning = analysis.get("tire_learning")
    if isinstance(tire_learning, Mapping):
        tire_learning = dict(tire_learning)
        tire_model = store.update_tire_learning(tire_learning)
        tire_learning["prediction"] = tire_model.get("prediction") or {
            "status": "unavailable",
            "reason": "No matching local tire observations are available.",
        }
        tire_learning["persistent_model"] = {
            "path": tire_model.get("model_path"),
            "observation_count": tire_model.get("observation_count"),
            "model_version": tire_model.get("model_version"),
            "observation_set_fingerprint": tire_model.get(
                "observation_set_fingerprint"
            ),
        }
        analysis["tire_learning"] = tire_learning
        analysis["technical_insights"] = build_technical_insights(
            analysis.get("laps") or [],
            analysis.get("runs") or [],
            analysis.get("race_summary") or {},
            analysis.get("strategy") or {},
            analysis.get("damage_repair") or {},
            tire_learning,
        )
    analysis_finished = time.perf_counter()
    context = store.context_from_analysis(analysis)
    cache_status = store.cache_status(context)
    current_subsession = analysis.get("identity", {}).get("subsession_id")
    current_sources = {str(Path(path).resolve()).casefold() for path in source_paths}

    def is_current_recording(row: Mapping[str, Any]) -> bool:
        if (
            current_subsession not in (None, "")
            and row.get("subsession_id") not in (None, "")
            and str(row.get("subsession_id")) == str(current_subsession)
        ):
            return True
        historical_source = row.get("source_path")
        if historical_source:
            try:
                return str(Path(str(historical_source)).resolve()).casefold() in current_sources
            except (OSError, ValueError):
                return False
        return False

    history = [
        row
        for row in store.historical_runs(context, include_other_seasons=True)
        if str(row.get("analysis_id")) != str(analysis.get("analysis_id"))
        and not is_current_recording(row)
    ]
    knowledge = _knowledge_for_report(store, context, cache_status)
    garage61_index = (
        (knowledge or {}).get("garage61")
        if isinstance((knowledge or {}).get("garage61"), Mapping)
        else {}
    )
    garage61_representatives = {
        "schema_version": 1,
        "target_derivation_version": _GARAGE61_TARGET_DERIVATION_VERSION,
        "status": "available" if garage61_index.get("representative_laps") else "unavailable",
        "reason": None if garage61_index.get("representative_laps") else "No cached Garage61 representative laps are available for this exact car/track/setup context.",
        "comparison_scope": garage61_index.get("comparison_scope"),
        "representative_laps": list(garage61_index.get("representative_laps") or ()),
        "reference_comparisons": list(garage61_index.get("reference_comparisons") or ()),
        "comparison_quality": dict(garage61_index.get("comparison_quality") or {}),
    }
    analysis["garage61_representative_laps"] = garage61_representatives
    race_card = build_race_card(
        analysis,
        historical_runs=history,
        knowledge=knowledge,
    )
    race_card_markdown = render_race_card(race_card)
    report = render_report(analysis, historical_runs=history, knowledge=knowledge)
    extra_files = dict(render_visuals(analysis) or {})
    extra_files["race-card.md"] = race_card_markdown
    artifacts = store.save_report_artifacts(
        analysis,
        report,
        extra_files=extra_files,
    )
    store.record_analysis(analysis, artifacts["report"])
    persisted = time.perf_counter()
    timing = {
        "contract_version": 1,
        "clock": "monotonic_perf_counter",
        "selection_verification_ms": _elapsed_ms(workflow_started, selection_verified),
        "decode_analysis_ms": _elapsed_ms(selection_verified, analysis_finished),
        "report_persist_ms": _elapsed_ms(analysis_finished, persisted),
        "total_ms": _elapsed_ms(workflow_started, persisted),
        "analysis_cache_hit": analysis_cache_hit,
    }
    inline_race_card = dict(race_card)
    inline_race_card["markdown"] = race_card_markdown
    inline_race_card["path"] = artifacts["race-card.md"]
    inline_race_card["timing"] = timing
    analysis_view = build_analysis_view(analysis)
    return {
        "ok": True,
        "analysis_id": analysis.get("analysis_id"),
        "selector": str(selector),
        "selection": selection,
        "context": context,
        "analysis_cache": source["analysis_cache"],
        "knowledge_cache": cache_status,
        "historical_runs_considered": len(history),
        "race_summary": analysis.get("race_summary") or {},
        "race_timeline": analysis.get("race_timeline") or {},
        "damage_repair": analysis.get("damage_repair") or {},
        "strategy_forecast": (
            analysis.get("strategy", {}).get("forecast")
            if isinstance(analysis.get("strategy"), Mapping)
            else {}
        ) or {},
        "data_quality": analysis.get("data_quality") or {},
        "source_files": list(source_paths),
        "source_channel_coverage": source.get("channel_coverage") or {},
        "analysis_path": artifacts["analysis"],
        "report_path": artifacts["report"],
        "race_card_path": artifacts["race-card.md"],
        "race_card": inline_race_card,
        "analysis_view": analysis_view,
        "timing": timing,
        "artifacts": artifacts,
    }


def _query_channels(value: Sequence[str] | str | None) -> list[str]:
    if value is None:
        return []
    raw_values = [value] if isinstance(value, str) else list(value)
    if len(raw_values) > 12:
        raise ValueError("channels may contain at most 12 names")
    result: list[str] = []
    for raw in raw_values:
        name = str(raw).strip()
        if not name or len(name) > 128:
            raise ValueError("Each channel name must contain 1-128 characters")
        if name not in result:
            result.append(name)
    return result


def _json_safe_telemetry_value(value: Any) -> Any:
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (list, tuple)):
        return [_json_safe_telemetry_value(item) for item in value]
    return value


def _query_source_catalog(path: str, metadata: Mapping[str, Any]) -> dict[str, Any]:
    catalog = [
        dict(item)
        for item in (metadata.get("variables") or ())
        if isinstance(item, Mapping) and item.get("name")
    ]
    return {
        "path": str(Path(path).resolve()),
        "file_size": metadata.get("file_size"),
        "modified_ns": Path(path).stat().st_mtime_ns,
        "record_count": metadata.get("record_count"),
        "native_tick_rate_hz": _path_value(metadata, "header", "tick_rate"),
        "recorded_channel_catalog": catalog,
        "loaded_channels": [],
        "unloaded_channels": sorted(str(item["name"]) for item in catalog),
        "catalog_sha256": stable_hash(catalog, 64),
    }


def _path_value(value: Mapping[str, Any], *keys: str) -> Any:
    current: Any = value
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _profile_cache_path(
    store: ArchiveStore,
    source_sha256: str,
    *,
    channels: Sequence[str] | None,
    target_hz: float | None,
    start_record: int,
    end_record: int | None,
) -> Path:
    key = stable_hash(
        {
            "profile_schema": 1,
            "channels": list(channels) if channels is not None else None,
            "target_hz": target_hz,
            "start_record": start_record,
            "end_record": end_record,
        },
        32,
    )
    return store.root / "telemetry-profiles" / source_sha256 / f"{key}.json"


def _write_profile_cache(path: Path, payload: Mapping[str, Any]) -> None:
    _write_json_cache(path, payload)


def telemetry_query_workflow(
    *,
    selector: str = "latest",
    iracing_root: str | os.PathLike[str] | None = None,
    archive_root: str | os.PathLike[str] | None = None,
    mode: str = "catalog",
    channels: Sequence[str] | str | None = None,
    search: str | None = None,
    target_hz: float | None = None,
    start_record: int = 0,
    end_record: int | None = None,
    max_samples: int = 1_000,
) -> dict[str, Any]:
    """Catalog, profile, or extract a bounded slice from any recorded channel."""

    normalized_mode = str(mode or "catalog").strip().lower()
    if normalized_mode not in {"catalog", "profile", "slice"}:
        raise ValueError("mode must be catalog, profile, or slice")
    requested = _query_channels(channels)
    if normalized_mode == "slice" and not requested:
        raise ValueError("slice mode requires at least one channel")
    if isinstance(start_record, bool) or int(start_record) != start_record or start_record < 0:
        raise ValueError("start_record must be a non-negative integer")
    record_start = int(start_record)
    if end_record is not None:
        if isinstance(end_record, bool) or int(end_record) != end_record:
            raise ValueError("end_record must be an integer or null")
        end_record = int(end_record)
        if end_record <= record_start:
            raise ValueError("end_record must be greater than start_record")
    if isinstance(max_samples, bool) or not 1 <= int(max_samples) <= 2_000:
        raise ValueError("max_samples must be between 1 and 2000")
    sample_limit = int(max_samples)
    rate = None if target_hz is None else _target_rate(target_hz)
    search_text = str(search or "").strip().casefold()
    if len(search_text) > 100:
        raise ValueError("search must be 100 characters or fewer")

    selection = _resolve_session_selection(selector, iracing_root)
    source_paths = [str(Path(path).resolve()) for path in selection.get("files", ())]
    if not source_paths:
        raise SessionSelectionError("The selected session contains no IBT files.")
    metadata_by_path: dict[str, dict[str, Any]] = {}
    signatures: dict[str, tuple[int, int]] = {}
    source_catalogs: list[dict[str, Any]] = []
    all_names: set[str] = set()
    for path in source_paths:
        signatures[path] = _file_signature(path)
        metadata = scan_ibt(path)
        _assert_finalized_ibt(path, metadata)
        metadata_by_path[path] = metadata
        source_catalog = _query_source_catalog(path, metadata)
        source_catalogs.append(source_catalog)
        all_names.update(
            str(item["name"])
            for item in source_catalog["recorded_channel_catalog"]
        )

    unknown = sorted(set(requested) - all_names)
    if unknown:
        raise ValueError("Unknown telemetry channel(s): " + ", ".join(unknown))
    catalog_summary = _catalog_summary(source_catalogs)
    if normalized_mode == "catalog":
        sources = []
        for source in source_catalogs:
            filtered = []
            for item in source["recorded_channel_catalog"]:
                haystack = " ".join(
                    str(item.get(key) or "")
                    for key in ("name", "description", "unit", "type")
                ).casefold()
                if not search_text or search_text in haystack:
                    filtered.append(item)
            sources.append(
                {
                    **{key: value for key, value in source.items() if key != "recorded_channel_catalog"},
                    "matching_channel_count": len(filtered),
                    "recorded_channel_catalog": filtered,
                }
            )
        return {
            "ok": True,
            "mode": normalized_mode,
            "selection": selection,
            "search": search or None,
            "catalog_summary": catalog_summary,
            "sources": sources,
        }

    store = ArchiveStore(archive_root)
    fingerprints = store.source_fingerprints(source_paths)
    fingerprints_by_path = {str(Path(item["path"]).resolve()): item for item in fingerprints}
    outputs: list[dict[str, Any]] = []
    remaining = sample_limit
    truncated = False
    for path in source_paths:
        metadata = metadata_by_path[path]
        catalog = {
            str(item["name"]): item
            for item in metadata.get("variables", ()) or ()
            if isinstance(item, Mapping) and item.get("name")
        }
        selected = [name for name in requested if name in catalog]
        missing = [name for name in requested if name not in catalog]
        record_count = int(metadata.get("record_count") or 0)
        if record_start >= record_count:
            outputs.append(
                {
                    "source_path": path,
                    "missing_channels": missing,
                    "status": "record_window_empty",
                }
            )
            continue
        record_end = min(end_record, record_count) if end_record is not None else record_count
        before = signatures[path]
        fingerprint = fingerprints_by_path[path]
        if normalized_mode == "profile":
            profile_channels: Sequence[str] | None = selected if requested else None
            cache_path = _profile_cache_path(
                store,
                str(fingerprint["sha256"]),
                channels=profile_channels,
                target_hz=rate,
                start_record=record_start,
                end_record=record_end,
            )
            if cache_path.is_file():
                try:
                    profile = json.loads(cache_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    profile = None
            else:
                profile = None
            cache_hit = isinstance(profile, Mapping)
            if not cache_hit:
                profile = profile_telemetry(
                    path,
                    channels=profile_channels,
                    target_hz=rate,
                    chunk_size=2_048,
                    start_record=record_start,
                    end_record=record_end,
                )
                profile = {
                    **dict(profile),
                    "source_sha256": fingerprint["sha256"],
                }
                if _file_signature(path) != before:
                    raise WorkflowError(
                        f"The selected IBT changed during profiling: {path}. No profile cache was written."
                    )
                _write_profile_cache(cache_path, profile)
            outputs.append(
                {
                    "source_path": path,
                    "missing_channels": missing,
                    "cache_hit": cache_hit,
                    "cache_path": str(cache_path),
                    "profile": profile,
                }
            )
        else:
            estimated_cells = sum(
                max(1, int(catalog[name].get("count") or 1)) for name in selected
            ) * sample_limit
            if estimated_cells > 100_000:
                raise ValueError(
                    "Requested telemetry slice is too large; reduce channels or max_samples"
                )
            source_indices: list[int] = []
            samples: dict[str, list[Any]] = {name: [] for name in selected}
            if selected and remaining > 0:
                for chunk in iter_telemetry_chunks(
                    path,
                    selected,
                    target_hz=rate,
                    chunk_size=min(2_048, remaining + 1),
                    start_record=record_start,
                    end_record=record_end,
                ):
                    for local_index, native_index in enumerate(chunk["sample_indices"]):
                        if remaining <= 0:
                            truncated = True
                            break
                        source_indices.append(int(native_index))
                        for name in selected:
                            samples[name].append(
                                _json_safe_telemetry_value(chunk["samples"][name][local_index])
                            )
                        remaining -= 1
                    if remaining <= 0:
                        if int(chunk["sample_count"]) > len(source_indices):
                            truncated = True
                        break
            outputs.append(
                {
                    "source_path": path,
                    "source_sha256": fingerprint["sha256"],
                    "missing_channels": missing,
                    "sample_indices": source_indices,
                    "samples": samples,
                    "sample_count": len(source_indices),
                    "native_tick_rate_hz": _path_value(metadata, "header", "tick_rate"),
                    "sample_rate_hz": rate
                    or _finite_number(_path_value(metadata, "header", "tick_rate")),
                    "record_start": record_start,
                    "record_end": record_end,
                }
            )
        if _file_signature(path) != before:
            raise WorkflowError(
                f"The selected IBT changed during the telemetry query: {path}."
            )

    return {
        "ok": True,
        "mode": normalized_mode,
        "selection": selection,
        "requested_channels": requested,
        "target_hz": rate,
        "record_bounds_semantics": "per source; start inclusive, end exclusive",
        "max_samples": sample_limit if normalized_mode == "slice" else None,
        "truncated": truncated if normalized_mode == "slice" else False,
        "source_fingerprints": fingerprints,
        "catalog_summary": catalog_summary,
        "sources": outputs,
    }


def native_event_search_workflow(
    *,
    selector: str = "latest",
    iracing_root: str | os.PathLike[str] | None = None,
    archive_root: str | os.PathLike[str] | None = None,
    event_types: Sequence[str] | str | None = None,
    selection_mode: str = "chronological",
    start_record: int = 0,
    end_record: int | None = None,
    max_events: int = 200,
    lap: int | None = None,
    session_time_start: float | None = None,
    session_time_end: float | None = None,
    lap_distance_start: float | None = None,
    lap_distance_end: float | None = None,
) -> dict[str, Any]:
    """Find bounded native-rate driving, dynamics, and pit events."""

    limit = _positive_bounded_int(max_events, "max_events", 500)
    normalized_selection_mode = str(selection_mode or "chronological").strip().casefold()
    if normalized_selection_mode not in SUPPORTED_EVENT_SELECTION_MODES:
        raise ValueError(
            "selection_mode must be one of: "
            + ", ".join(SUPPORTED_EVENT_SELECTION_MODES)
        )
    if isinstance(start_record, bool) or not isinstance(start_record, int) or start_record < 0:
        raise ValueError("start_record must be a non-negative integer")
    if end_record is not None and (
        isinstance(end_record, bool)
        or not isinstance(end_record, int)
        or end_record <= start_record
    ):
        raise ValueError("end_record must be an integer greater than start_record")
    if event_types is None:
        requested: Sequence[str] | str | None = None
        requested_names = list(SUPPORTED_EVENT_TYPES)
    elif isinstance(event_types, str):
        requested = event_types
        requested_names = [event_types]
    else:
        requested_names = list(dict.fromkeys(str(item) for item in event_types))
        requested = requested_names
    if not requested_names:
        raise ValueError("event_types must contain at least one supported event type")
    unknown = sorted(set(requested_names) - set(SUPPORTED_EVENT_TYPES))
    if unknown:
        raise ValueError(
            "Unknown native event type(s): "
            + ", ".join(unknown)
            + ". Supported: "
            + ", ".join(SUPPORTED_EVENT_TYPES)
        )

    selection = _resolve_session_selection(selector, iracing_root)
    source_paths = [str(Path(path).resolve()) for path in selection.get("files", ())]
    if not source_paths:
        raise SessionSelectionError("The selected session contains no IBT files.")
    metadata_by_path: dict[str, dict[str, Any]] = {}
    signatures: dict[str, tuple[int, int]] = {}
    for path in source_paths:
        signatures[path] = _file_signature(path)
        metadata = scan_ibt(path)
        _assert_finalized_ibt(path, metadata)
        metadata_by_path[path] = metadata

    store = ArchiveStore(archive_root)
    fingerprints = store.source_fingerprints(source_paths)
    fingerprints_by_path = {
        str(Path(item["path"]).resolve()): item for item in fingerprints
    }
    detector_sha256 = hashlib.sha256(
        (SCRIPT_DIR / "native_events.py").read_bytes()
    ).hexdigest()
    filters = {
        "lap": lap,
        "session_time_start": session_time_start,
        "session_time_end": session_time_end,
        "lap_distance_start": lap_distance_start,
        "lap_distance_end": lap_distance_end,
    }
    outputs: list[dict[str, Any]] = []
    flat_events: list[dict[str, Any]] = []
    remaining = limit
    global_truncated = False
    global_scan_complete = True
    global_candidate_count = 0
    global_candidate_counts: Counter[str] = Counter()
    for path in source_paths:
        fingerprint = fingerprints_by_path[path]
        if _file_signature(path) != signatures[path]:
            raise WorkflowError(
                f"The selected IBT changed during native-event SHA-256 verification: {path}."
            )
        if normalized_selection_mode == "chronological" and remaining <= 0:
            outputs.append(
                {
                    "source_path": path,
                    "source_sha256": fingerprint["sha256"],
                    "status": "skipped_global_event_limit",
                }
            )
            global_truncated = True
            global_scan_complete = False
            continue
        record_count = int(metadata_by_path[path].get("record_count") or 0)
        if start_record >= record_count:
            outputs.append(
                {
                    "source_path": path,
                    "source_sha256": fingerprint["sha256"],
                    "status": "record_window_empty",
                    "events": [],
                }
            )
            continue
        record_end = min(end_record, record_count) if end_record is not None else record_count
        per_source_limit = (
            remaining if normalized_selection_mode == "chronological" else limit
        )
        cache_key = stable_hash(
            {
                "cache_schema": 1,
                "source_sha256": fingerprint["sha256"],
                "detector_sha256": detector_sha256,
                "event_types": requested_names,
                "start_record": start_record,
                "end_record": record_end,
                "max_events": per_source_limit,
                "selection_mode": normalized_selection_mode,
                "filters": filters,
            },
            40,
        )
        cache_path = (
            store.root
            / "telemetry-events"
            / str(fingerprint["sha256"])
            / f"{cache_key}.json"
        )
        cached: Any = None
        if cache_path.is_file():
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                cached = None
        result = (
            cached.get("result")
            if isinstance(cached, Mapping)
            and cached.get("cache_key") == cache_key
            and cached.get("result_sha256") == stable_hash(cached.get("result"), 64)
            and isinstance(cached.get("result"), Mapping)
            else None
        )
        cache_hit = result is not None
        if result is None:
            result = detect_native_telemetry_events(
                path,
                requested,
                selection_mode=normalized_selection_mode,
                start_record=start_record,
                end_record=record_end,
                max_events=per_source_limit,
                lap=lap,
                session_time_start=session_time_start,
                session_time_end=session_time_end,
                lap_distance_start=lap_distance_start,
                lap_distance_end=lap_distance_end,
            )
            result = dict(result)
            result["source"]["sha256"] = fingerprint["sha256"]
            if _file_signature(path) != signatures[path]:
                raise WorkflowError(
                    f"The selected IBT changed during native-event detection: {path}. No event cache was written."
                )
            _write_json_cache(
                cache_path,
                {
                    "cache_schema": 1,
                    "cache_key": cache_key,
                    "created_at": utc_now(),
                    "result_sha256": stable_hash(result, 64),
                    "result": result,
                },
            )
        source_events = [
            {"source_path": path, "source_sha256": fingerprint["sha256"], **dict(event)}
            for event in (result.get("events") or ())
            if isinstance(event, Mapping)
        ]
        source_summary = result.get("summary") if isinstance(result.get("summary"), Mapping) else {}
        source_candidate_count = int(
            source_summary.get("candidate_event_count", len(source_events)) or 0
        )
        global_candidate_count += source_candidate_count
        for event_type, count in (source_summary.get("candidate_counts_by_type") or {}).items():
            if isinstance(count, int) and not isinstance(count, bool):
                global_candidate_counts[str(event_type)] += count
        source_scan_complete = source_summary.get("scan_complete") is not False
        global_scan_complete = global_scan_complete and source_scan_complete
        source_truncated = bool(source_summary.get("truncated"))
        if normalized_selection_mode == "severity":
            flat_events = select_native_events_by_severity(
                [*flat_events, *source_events], requested_names, limit
            )
        else:
            flat_events.extend(source_events)
            remaining -= len(source_events)
        global_truncated = global_truncated or source_truncated
        outputs.append(
            {
                "source_path": path,
                "source_sha256": fingerprint["sha256"],
                "cache_hit": cache_hit,
                "cache_path": str(cache_path),
                "coverage": result.get("coverage") or {},
                "summary": source_summary,
                "events": (
                    [] if normalized_selection_mode == "severity" else source_events
                ),
            }
        )

    if normalized_selection_mode == "severity":
        selected_by_source: dict[str, list[dict[str, Any]]] = {}
        for event in flat_events:
            selected_by_source.setdefault(str(event.get("source_path") or ""), []).append(event)
        for output in outputs:
            path = str(output.get("source_path") or "")
            output["events"] = selected_by_source.get(path, [])
            output["globally_returned_event_count"] = len(output["events"])
        global_truncated = global_candidate_count > len(flat_events)

    counts: dict[str, int] = {name: 0 for name in requested_names}
    for event in flat_events:
        name = str(event.get("event_type") or "")
        counts[name] = counts.get(name, 0) + 1
    return {
        "ok": True,
        "selection": selection,
        "event_types": requested_names,
        "selection_mode": normalized_selection_mode,
        "filters": filters,
        "record_bounds_semantics": "per source; start inclusive, end exclusive",
        "max_events": limit,
        "source_fingerprints": fingerprints,
        "sources": outputs,
        "events": flat_events,
        "summary": {
            "source_count": len(source_paths),
            "selection_mode": normalized_selection_mode,
            "scan_complete": global_scan_complete,
            "candidate_event_count": global_candidate_count,
            "candidate_event_count_complete": global_scan_complete,
            "returned_event_count": len(flat_events),
            "counts_by_type": counts,
            "candidate_counts_by_type": {
                name: global_candidate_counts.get(name, 0) for name in requested_names
            },
            "omitted_event_count": (
                max(0, global_candidate_count - len(flat_events))
                if global_scan_complete
                else None
            ),
            "truncated": global_truncated,
        },
        "evidence_rule": (
            "Brake, pit, torque, and shock events are derived from measured SDK channels; "
            "wheel-speed divergence remains a calibrated diagnostic proxy, not proof of lock, spin, wear, or setup cause."
        ),
    }


def garage61_status_workflow(
    *, archive_root: str | os.PathLike[str] | None = None
) -> dict[str, Any]:
    """Report secure credential and Garage61 API capability status safely."""

    store = ArchiveStore(archive_root)
    request_path = store.auth_dir / "garage61-api-request.json"
    request_status = _read_json_file(request_path, dict, {})
    try:
        configured = credential_exists()
    except OSError as exc:
        return {
            "ok": False,
            "configured": False,
            "status": "credential_store_unavailable",
            "credential_storage": "windows-user-dpapi",
            "archive_root": str(store.root),
            "api_request": request_status or None,
            "error_type": type(exc).__name__,
            "message": str(exc),
        }
    if not configured:
        return {
            "ok": False,
            "configured": False,
            "status": "not_configured",
            "credential_storage": "windows-user-dpapi",
            "archive_root": str(store.root),
            "api_request": request_status or None,
            "message": (
                "Garage61 API access is awaiting approval; use the signed-in browser fallback meanwhile."
                if request_status.get("status") == "pending"
                else "Garage61 is not configured. Run configure-garage61.ps1 once."
            ),
        }
    try:
        health = Garage61Client(
            base_url=_garage61_base_url(),
            global_visible_laps_approved=_garage61_global_visible_laps_approved(),
        ).health_check()
    except (SecureStoreError, Garage61Error, OSError) as exc:
        return {
            "ok": False,
            "configured": True,
            "status": "unavailable",
            "credential_storage": "windows-user-dpapi",
            "archive_root": str(store.root),
            "api_request": request_status or None,
            "error_type": type(exc).__name__,
            "message": str(exc),
        }
    effective_request = dict(request_status)
    if effective_request:
        effective_request["status"] = "approved_and_configured"
    return {
        **health,
        "configured": True,
        "status": "available",
        "credential_storage": "windows-user-dpapi",
        "archive_root": str(store.root),
        "api_request": effective_request or None,
    }


def _normalized_label(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _mapping_id(value: Any) -> int | None:
    if isinstance(value, Mapping):
        value = value.get("id")
    number = _finite_number(value)
    return int(number) if number is not None and number.is_integer() else None


def _resolve_garage_car(catalog: Any, identity: Mapping[str, Any]) -> Mapping[str, Any]:
    car_id = identity.get("car_id")
    candidates: list[Mapping[str, Any]] = []
    if car_id not in (None, ""):
        candidate = catalog.cars_by_platform_id.get(str(car_id))
        if candidate is not None:
            candidates.append(candidate)
    if not candidates:
        desired = {
            _normalized_label(identity.get("car_name")),
            _normalized_label(identity.get("car_path")),
        } - {""}
        for candidate in catalog.cars:
            labels = {
                _normalized_label(candidate.get(key))
                for key in ("name", "shortName", "slug", "path")
            } - {""}
            if desired.intersection(labels):
                candidates.append(candidate)
    unique = {str(item.get("id")): item for item in candidates if item.get("id") is not None}
    if len(unique) != 1:
        raise Garage61MappingError(
            "Could not map the local car to one exact Garage61 car using its iRacing CarID."
        )
    return next(iter(unique.values()))


def _resolve_garage_track(catalog: Any, identity: Mapping[str, Any]) -> Mapping[str, Any]:
    track_id = identity.get("track_id")
    candidates = list(catalog.tracks_by_platform_id.get(str(track_id), ()))
    if not candidates:
        raise Garage61MappingError(
            "Could not map the local track to Garage61 using its iRacing TrackID."
        )
    if len(candidates) == 1:
        return candidates[0]
    desired = _normalized_label(identity.get("track_config"))
    if desired:
        exact: list[Mapping[str, Any]] = []
        for candidate in candidates:
            labels = {
                _normalized_label(candidate.get(key))
                for key in (
                    "variant",
                    "layout",
                    "configuration",
                    "config",
                    "name",
                    "slug",
                )
            } - {""}
            if desired in labels:
                exact.append(candidate)
        if len(exact) == 1:
            return exact[0]
    raise Garage61MappingError(
        "The iRacing TrackID maps to multiple Garage61 layouts and the exact "
        f"layout {identity.get('track_config')!r} could not be resolved uniquely."
    )


def _datetime_value(value: Any) -> _datetime.datetime | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return _datetime.datetime.fromtimestamp(float(value), tz=_datetime.timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = _datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.replace(tzinfo=parsed.tzinfo or _datetime.timezone.utc)


def _season_year_quarter(season: Mapping[str, Any]) -> tuple[int | None, int | None]:
    year = _mapping_id(season.get("year") or season.get("seasonYear"))
    quarter = _mapping_id(season.get("quarter") or season.get("seasonQuarter"))
    if year is not None and quarter is not None:
        return year, quarter
    label = " ".join(
        str(season.get(key) or "") for key in ("name", "title", "slug")
    )
    match = re.search(r"\b(20\d{2})\D{0,20}(?:season\s*|s)([1-4])\b", label, re.I)
    return (int(match.group(1)), int(match.group(2))) if match else (year, quarter)


def _season_contains(season: Mapping[str, Any], moment: _datetime.datetime) -> bool:
    start = next(
        (
            _datetime_value(season.get(key))
            for key in ("start", "startDate", "start_date", "startsAt")
            if season.get(key) is not None
        ),
        None,
    )
    end = next(
        (
            _datetime_value(season.get(key))
            for key in ("end", "endDate", "end_date", "endsAt")
            if season.get(key) is not None
        ),
        None,
    )
    if start is None or end is None:
        return False
    comparable = moment.astimezone(_datetime.timezone.utc)
    return start.astimezone(_datetime.timezone.utc) <= comparable <= end.astimezone(_datetime.timezone.utc)


def _resolve_garage_season(catalog: Any, identity: Mapping[str, Any]) -> Mapping[str, Any]:
    seasons = [
        item
        for item in catalog.seasons
        if str(item.get("platform", "iracing")).lower() == "iracing"
    ]
    year = _mapping_id(identity.get("season_year"))
    quarter = _mapping_id(identity.get("season_quarter"))
    local_id = _mapping_id(identity.get("season_id"))
    if local_id is not None:
        direct = [item for item in seasons if _mapping_id(item.get("id")) == local_id]
        if len(direct) == 1:
            direct_period = _season_year_quarter(direct[0])
            if year is None or quarter is None or direct_period == (year, quarter):
                return direct[0]
    if year is not None and quarter is not None:
        matching = [item for item in seasons if _season_year_quarter(item) == (year, quarter)]
        if len(matching) == 1:
            return matching[0]
        seasons = matching or seasons
    moment = _datetime_value(identity.get("session_start"))
    if moment is not None:
        dated = [item for item in seasons if _season_contains(item, moment)]
        if len(dated) == 1:
            return dated[0]
    raise Garage61MappingError(
        "Could not map the local iRacing season to one exact Garage61 season."
    )


def _representative_local_lap(analysis: Mapping[str, Any]) -> float | None:
    values = []
    for lap in analysis.get("laps", ()) or ():
        if not isinstance(lap, Mapping):
            continue
        lap_time = _finite_number(lap.get("lap_time_s"))
        damage_context = lap.get("damage_repair_context")
        damage_eligible = not (
            isinstance(damage_context, Mapping)
            and damage_context.get("automatic_coaching_reference_eligible") is False
        )
        if (
            lap_time is not None
            and lap.get("complete", True)
            and str(lap.get("flag_state", "")).lower() == "green"
            and (_finite_number(lap.get("pit_time_s")) or 0.0) < 1.0
            and damage_eligible
        ):
            values.append(lap_time)
    return statistics.median(values) if values else None


def _metadata_value(
    analysis: Mapping[str, Any],
    aliases: Sequence[str],
    *,
    canonical_path: tuple[str, ...] | None = None,
    legacy_container: str | None = None,
    canonical_before_legacy: bool = False,
    numeric: bool = True,
) -> tuple[Any, str] | None:
    """Read Garage61 metadata from allowlisted shallow and canonical paths."""

    def accepted(raw: Any, source: str) -> tuple[Any, str] | None:
        if raw is None or raw == "":
            return None
        if not numeric:
            if isinstance(raw, bool) or not isinstance(raw, (str, int, float)):
                return None
            if isinstance(raw, float) and not math.isfinite(raw):
                return None
            return raw, source
        value = _finite_number(raw)
        return (value, source) if value is not None else None

    def shallow(
        container: Mapping[str, Any], container_path: str
    ) -> tuple[Any, str] | None:
        for alias in aliases:
            wanted = _normalized_label(alias).replace(" ", "")
            for key, raw in container.items():
                if _normalized_label(key).replace(" ", "") != wanted:
                    continue
                found = accepted(raw, f"{container_path}.{key}")
                if found is not None:
                    return found
        return None

    found = shallow(analysis, "analysis")
    if found is not None:
        return found

    def canonical() -> tuple[Any, str] | None:
        if canonical_path is None:
            return None
        return accepted(
            _path_value(analysis, *canonical_path),
            f"analysis.{'.'.join(canonical_path)}",
        )

    if canonical_before_legacy and (found := canonical()) is not None:
        return found

    legacy = analysis.get(legacy_container) if legacy_container else None
    if legacy_container and isinstance(legacy, Mapping):
        found = shallow(legacy, f"analysis.{legacy_container}")
        if found is not None:
            return found

    return None if canonical_before_legacy else canonical()


def _garage_target(
    analysis: Mapping[str, Any],
    car: Mapping[str, Any],
    track: Mapping[str, Any],
    season: Mapping[str, Any],
) -> dict[str, Any]:
    identity = analysis.get("identity") or {}
    target: dict[str, Any] = {
        "car": dict(car),
        "track": dict(track),
        "season": dict(season),
        "lapTime": _representative_local_lap(analysis),
        "_localSetupType": (
            "fixed"
            if identity.get("is_fixed_setup") is True
            else "open" if identity.get("is_fixed_setup") is False else None
        ),
    }
    metadata_sources: dict[str, str] = {}
    enriched_fields: tuple[
        tuple[str, tuple[str, ...], tuple[str, ...], str | None, bool, bool], ...
    ] = (
        (
            "trackTemp",
            ("trackTemp", "track_temp", "track_temp_c", "TrackTempCrew"),
            ("identity", "conditions", "track_temp_c"),
            "conditions",
            True,
            False,
        ),
        (
            "airTemp",
            ("airTemp", "air_temp", "air_temp_c", "AirTemp"),
            ("identity", "conditions", "air_temp_c"),
            "conditions",
            True,
            False,
        ),
        (
            "trackUsage",
            ("trackUsage", "track_usage", "TrackUsage"),
            ("identity", "conditions", "track_usage"),
            "conditions",
            True,
            False,
        ),
        (
            "trackWetness",
            ("trackWetness", "track_wetness", "TrackWetness"),
            ("identity", "conditions", "track_wetness"),
            "conditions",
            True,
            False,
        ),
        (
            "tireCompound",
            ("tireCompound", "tire_compound", "TireCompound"),
            ("identity", "tire_compound"),
            "conditions",
            False,
            True,
        ),
        (
            "weightPenalty",
            ("weightPenalty", "weight_penalty", "weight_penalty_kg", "WeightPenalty"),
            ("identity", "weight_penalty_kg"),
            None,
            True,
            False,
        ),
        (
            "powerAdjust",
            ("powerAdjust", "power_adjust", "power_adjust_percent", "PowerAdjust"),
            ("identity", "power_adjust_percent"),
            None,
            True,
            False,
        ),
        (
            "driverRating",
            ("driverRating", "driver_rating", "driver_irating", "iRating", "IRating"),
            ("identity", "driver_irating"),
            None,
            True,
            False,
        ),
        (
            "maxFuelPercent",
            ("maxFuelPercent", "max_fuel_percent", "MaxFuelPercent"),
            ("identity", "max_fuel_percent"),
            None,
            True,
            False,
        ),
    )
    for (
        target_name,
        aliases,
        canonical_path,
        legacy_container,
        numeric,
        canonical_before_legacy,
    ) in enriched_fields:
        found = _metadata_value(
            analysis,
            aliases,
            canonical_path=canonical_path,
            legacy_container=legacy_container,
            canonical_before_legacy=canonical_before_legacy,
            numeric=numeric,
        )
        if found is not None:
            target[target_name], metadata_sources[target_name] = found

    explicit_fuel = _metadata_value(
        analysis,
        ("fuelLevel", "fuel_level", "starting_fuel_l"),
        numeric=True,
    )
    first_run = next(
        (
            (index, item)
            for index, item in enumerate(analysis.get("runs", ()) or ())
            if isinstance(item, Mapping)
        ),
        None,
    )
    if explicit_fuel is None and first_run is not None:
        run_index, run = first_run
        fuel = run.get("fuel")
        start_fuel = (
            _finite_number(fuel.get("start_l"))
            if isinstance(fuel, Mapping)
            else None
        )
        if start_fuel is not None:
            explicit_fuel = (
                start_fuel,
                f"analysis.runs[{run_index}].fuel.start_l",
            )
    if explicit_fuel is not None:
        target["fuelLevel"], metadata_sources["fuelLevel"] = explicit_fuel

    event_type = _mapping_id(identity.get("event_type"))
    if event_type is not None:
        target["eventType"] = event_type
        metadata_sources["eventType"] = "analysis.identity.event_type"
    session_type = _mapping_id(identity.get("session_type"))
    if session_type is not None:
        target["sessionType"] = session_type
        metadata_sources["sessionType"] = "analysis.identity.session_type"
    target["_metadataSources"] = metadata_sources
    return target


def _ranked_dict(item: RankedLap) -> dict[str, Any]:
    result = item.as_dict()
    result["reasons"] = list(result.get("reasons") or ())
    return result


def _annotate_comparison_roles(items: list[dict[str, Any]]) -> None:
    for item in items:
        item["comparison_role"] = "representative"
    clean = [
        item
        for item in items
        if (item.get("lap") or {}).get("clean") is True
        and (item.get("lap") or {}).get("canViewTelemetry") is True
        and _finite_number((item.get("lap") or {}).get("lapTime")) is not None
    ]
    if len(clean) < 4:
        return
    elite = min(
        clean,
        key=lambda item: _finite_number((item.get("lap") or {}).get("lapTime"))
        or math.inf,
    )
    elite["comparison_role"] = "elite"


def _driver_key(reference: Mapping[str, Any]) -> str:
    lap = reference.get("lap") or {}
    for key in ("driver", "user", "driverId", "userId", "customerId"):
        value = lap.get(key) if isinstance(lap, Mapping) else None
        if isinstance(value, Mapping):
            value = value.get("id") or value.get("slug") or value.get("name")
        if value not in (None, ""):
            return f"driver:{value}"
    return f"lap:{lap.get('id') if isinstance(lap, Mapping) else stable_hash(reference)}"


def _select_representatives(
    cohorts: Mapping[str, Sequence[Mapping[str, Any]]],
    local_setup: str | None,
    maximum_laps: int,
) -> list[dict[str, Any]]:
    preferred_order = [local_setup] if local_setup in {"fixed", "open"} else []
    preferred_order.extend(name for name in ("fixed", "open") if name not in preferred_order)
    output: list[dict[str, Any]] = []
    used_ids: set[str] = set()

    # When a cohort is large enough to contain a meaningful fast tail, seed its
    # clean fastest candidate. Otherwise seed its highest-ranked representative.
    # Fixed/open cohorts remain explicit and are never silently mixed.
    for name in preferred_order:
        candidates = cohorts.get(name) or ()
        if candidates and len(output) < maximum_laps:
            candidate = next(
                (
                    dict(item)
                    for item in candidates
                    if item.get("comparison_role") == "elite"
                ),
                dict(candidates[0]),
            )
            output.append(candidate)
            used_ids.add(str((candidate.get("lap") or {}).get("id")))

    ordered = [item for name in preferred_order for item in (cohorts.get(name) or ())]
    seen_drivers = {_driver_key(item) for item in output}
    for unique_only in (True, False):
        for raw in ordered:
            if len(output) >= maximum_laps:
                break
            lap_id = str((raw.get("lap") or {}).get("id"))
            if lap_id in used_ids:
                continue
            driver = _driver_key(raw)
            if unique_only and driver in seen_drivers:
                continue
            output.append(dict(raw))
            used_ids.add(lap_id)
            seen_drivers.add(driver)
        if len(output) >= maximum_laps:
            break
    return output


def _csv_destination(bundle_path: Path, setup_type: str, lap_id: str) -> Path:
    filename = f"{safe_slug(lap_id, 'lap')}-{stable_hash(lap_id, 8)}.csv"
    return bundle_path / "garage61" / "csv" / safe_slug(setup_type) / filename


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _cache_reference_csv(
    client: Garage61Client,
    reference: dict[str, Any],
    bundle_path: Path,
    download_telemetry: bool,
    expected_sha256: str | None = None,
) -> None:
    lap = reference.get("lap") or {}
    lap_id = str(lap.get("id") or "").strip()
    setup_type = str(reference.get("setup_type") or lap.get("_comparisonSetupType") or "unknown")
    if not download_telemetry:
        reference["telemetry"] = {"status": "not_requested", "path": None}
        return
    if not lap_id:
        reference["telemetry"] = {"status": "unavailable_missing_lap_id", "path": None}
        return
    destination = _csv_destination(bundle_path, setup_type, lap_id)
    relative = destination.relative_to(bundle_path).as_posix()
    try:
        overwrite = False
        if destination.exists():
            try:
                parse_telemetry_csv(destination.read_bytes())
                actual_sha256 = _file_sha256(destination)
            except (Garage61Error, OSError):
                overwrite = True
            else:
                if expected_sha256 and actual_sha256 != expected_sha256:
                    overwrite = True
                else:
                    reference["telemetry"] = {
                        "status": "cached",
                        "path": relative,
                        "sha256": actual_sha256,
                    }
                    return
        client.download_lap_csv(lap_id, destination, overwrite=overwrite)
        reference["telemetry"] = {
            "status": "downloaded",
            "path": relative,
            "sha256": _file_sha256(destination),
            "replaced_invalid_cache": overwrite,
        }
    except (Garage61Error, OSError, ValueError) as exc:
        reference["telemetry"] = {
            "status": "failed",
            "path": relative,
            "error_type": type(exc).__name__,
            "message": str(exc),
        }


_CSV_SIGNAL_ALIASES: dict[str, tuple[str, ...]] = {
    "lap_pct": (
        "lapdistpct",
        "lapdistancepct",
        "lapdistancepercent",
        "lapdistance",
        "lapdist",
    ),
    "speed_mph": ("speedmph", "velocitymph", "speed", "velocity"),
    "brake": ("brake", "brakepct", "brakepercent", "brakeposition"),
    "throttle": (
        "throttle",
        "throttlepct",
        "throttlepercent",
        "throttleposition",
    ),
    "steering_abs_rad": (
        "steeringwheelangle",
        "steeringangle",
        "steerangle",
        "steering",
    ),
    "lateral_g": ("lataccel", "lateralaccel", "lateralg", "latg"),
}


def _csv_header_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _csv_column_indices(headers: Sequence[str]) -> dict[str, int]:
    by_key = {_csv_header_key(header): index for index, header in enumerate(headers)}
    result: dict[str, int] = {}
    for signal, aliases in _CSV_SIGNAL_ALIASES.items():
        for alias in aliases:
            if alias in by_key:
                result[signal] = by_key[alias]
                break
        if signal not in result:
            for alias in aliases:
                matches = [index for key, index in by_key.items() if key.startswith(alias)]
                if len(matches) == 1:
                    result[signal] = matches[0]
                    break
    return result


def _csv_number(row: Sequence[str], index: int | None) -> float | None:
    if index is None or index >= len(row):
        return None
    return _finite_number(row[index])


def _upper_observed(values: Iterable[float]) -> float | None:
    ordered = sorted(value for value in values if math.isfinite(value))
    if not ordered:
        return None
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * 0.95))]


def _median_value(values: Iterable[Any]) -> float | None:
    finite = [value for raw in values if (value := _finite_number(raw)) is not None]
    return statistics.median(finite) if finite else None


def _reference_csv_profile(path: Path, bins: int) -> dict[str, Any]:
    telemetry = parse_telemetry_csv(path.read_bytes())
    headers = list(telemetry.headers)
    indices = _csv_column_indices(headers)
    distance_index = indices.get("lap_pct")
    if distance_index is None:
        return {
            "bins": {},
            "quality": {
                "status": "missing_lap_distance",
                "row_count": len(telemetry.rows),
                "headers": headers,
                "mapped_headers": {},
                "assumptions": [],
            },
        }

    raw_by_signal: dict[str, list[float]] = {name: [] for name in indices}
    for row in telemetry.rows:
        for signal, index in indices.items():
            value = _csv_number(row, index)
            if value is not None:
                raw_by_signal[signal].append(value)

    distance_upper = _upper_observed(raw_by_signal.get("lap_pct", ())) or 0.0
    distance_scale = 0.01 if distance_upper > 1.5 else 1.0
    assumptions = [
        (
            "Lap-distance values exceeded 1.5 and were normalized from percent to 0-1."
            if distance_scale == 0.01
            else "Lap-distance values were used as the native 0-1 fraction."
        )
    ]

    mapped_headers = {
        signal: headers[index] for signal, index in indices.items()
    }
    speed_header = _csv_header_key(mapped_headers.get("speed_mph"))
    if "mph" in speed_header:
        speed_scale = 1.0
        speed_note = "Speed header declares mph."
    elif "kmh" in speed_header or "kph" in speed_header:
        speed_scale = 0.621371192
        speed_note = "Speed header declares km/h; converted to mph."
    else:
        speed_scale = 2.236936292
        speed_note = "Garage61/iRacing Speed was treated as m/s and converted to mph."
    if "speed_mph" in indices:
        assumptions.append(speed_note)

    fraction_scales: dict[str, float] = {}
    for signal in ("brake", "throttle"):
        header = _csv_header_key(mapped_headers.get(signal))
        upper = _upper_observed(raw_by_signal.get(signal, ())) or 0.0
        scale = 0.01 if "percent" in header or "pct" in header or upper > 1.5 else 1.0
        fraction_scales[signal] = scale
        if signal in indices and scale == 0.01:
            assumptions.append(f"{signal.title()} was normalized from percent to 0-1.")

    steering_header = _csv_header_key(mapped_headers.get("steering_abs_rad"))
    steering_scale = math.pi / 180.0 if "deg" in steering_header else 1.0
    if "steering_abs_rad" in indices:
        assumptions.append(
            "Steering was converted from degrees to radians."
            if steering_scale != 1.0
            else "Steering angle was treated as radians."
        )
    lateral_header = _csv_header_key(mapped_headers.get("lateral_g"))
    lateral_scale = 1.0 if lateral_header.endswith("g") else 1.0 / 9.80665

    grouped: dict[int, dict[str, list[float]]] = {}
    valid_rows = 0
    for row in telemetry.rows:
        raw_pct = _csv_number(row, distance_index)
        if raw_pct is None:
            continue
        lap_pct = raw_pct * distance_scale
        if lap_pct < -0.001 or lap_pct > 1.001:
            continue
        lap_pct %= 1.0
        bin_index = min(bins - 1, max(0, int(lap_pct * bins)))
        bucket = grouped.setdefault(
            bin_index,
            {signal: [] for signal in indices if signal != "lap_pct"},
        )
        valid_rows += 1
        for signal, index in indices.items():
            if signal == "lap_pct":
                continue
            value = _csv_number(row, index)
            if value is None:
                continue
            if signal == "speed_mph":
                value *= speed_scale
            elif signal in fraction_scales:
                value *= fraction_scales[signal]
                value = max(0.0, min(1.0, value))
            elif signal == "steering_abs_rad":
                value = abs(value * steering_scale)
            elif signal == "lateral_g":
                value = abs(value * lateral_scale)
            bucket[signal].append(value)

    profile: dict[int, dict[str, Any]] = {}
    for bin_index, signals in grouped.items():
        profile[bin_index] = {
            "bin": bin_index,
            "lap_pct": round((bin_index + 0.5) / bins, 6),
            **{
                signal: round(value, 6) if value is not None else None
                for signal, values in signals.items()
                if (value := _median_value(values)) is not None
            },
        }
    return {
        "bins": profile,
        "quality": {
            "status": "parsed" if profile else "no_usable_rows",
            "row_count": len(telemetry.rows),
            "valid_distance_rows": valid_rows,
            "occupied_bins": len(profile),
            "headers": headers,
            "mapped_headers": mapped_headers,
            "signals": sorted(signal for signal in indices if signal != "lap_pct"),
            "assumptions": assumptions,
        },
    }


def _local_track_bins(analysis: Mapping[str, Any]) -> tuple[int, dict[int, dict[str, Any]]]:
    track = analysis.get("track_profile") or {}
    bins = int(_finite_number(track.get("bins")) or 200)
    result: dict[int, dict[str, Any]] = {}
    for raw in track.get("profile", ()) or ():
        if not isinstance(raw, Mapping):
            continue
        bin_number = _mapping_id(raw.get("bin"))
        lap_pct = _finite_number(raw.get("lap_pct"))
        if bin_number is None and lap_pct is not None:
            bin_number = min(bins - 1, max(0, int((lap_pct % 1.0) * bins)))
        if bin_number is None:
            continue
        result[bin_number] = {
            "bin": bin_number,
            "lap_pct": lap_pct if lap_pct is not None else (bin_number + 0.5) / bins,
            "speed_mph": _finite_number(raw.get("speed_mph")),
            "brake": _finite_number(raw.get("brake")),
            "throttle": _finite_number(raw.get("throttle")),
            "steering_abs_rad": _finite_number(raw.get("steering_abs_rad")),
            "lateral_g": _finite_number(raw.get("lateral_g")),
        }
    return bins, result


def _aligned_reference_comparison(
    analysis: Mapping[str, Any],
    reference: Mapping[str, Any],
    bundle_path: Path,
) -> dict[str, Any]:
    bins, local = _local_track_bins(analysis)
    telemetry = reference.get("telemetry") or {}
    relative = telemetry.get("path")
    lap = reference.get("lap") or {}
    summary: dict[str, Any] = {
        "lap_id": lap.get("id"),
        "setup_type": reference.get("setup_type") or lap.get("_comparisonSetupType"),
        "comparison_role": reference.get("comparison_role") or "representative",
        "score": reference.get("score"),
        "lap_time_s": lap.get("lapTime"),
        "telemetry_path": relative,
        "quality": {},
        "aligned_bins": [],
    }
    if not relative or telemetry.get("status") not in {"downloaded", "cached"}:
        summary["quality"] = {
            "status": "telemetry_unavailable",
            "usable": False,
            "coverage_fraction": 0.0,
        }
        return summary
    path = (bundle_path / str(relative)).resolve()
    resolved_bundle = bundle_path.resolve()
    if resolved_bundle != path and resolved_bundle not in path.parents:
        summary["quality"] = {
            "status": "unsafe_cache_path",
            "usable": False,
            "coverage_fraction": 0.0,
        }
        return summary
    expected_sha256 = str(telemetry.get("sha256") or "").strip().lower()
    if expected_sha256:
        try:
            actual_sha256 = _file_sha256(path)
        except OSError as exc:
            summary["quality"] = {
                "status": "csv_read_failed",
                "usable": False,
                "coverage_fraction": 0.0,
                "message": str(exc),
            }
            return summary
        if actual_sha256 != expected_sha256:
            summary["quality"] = {
                "status": "csv_integrity_mismatch",
                "usable": False,
                "coverage_fraction": 0.0,
            }
            return summary
    try:
        parsed = _reference_csv_profile(path, bins)
    except (Garage61Error, OSError, ValueError) as exc:
        summary["quality"] = {
            "status": "csv_parse_failed",
            "usable": False,
            "coverage_fraction": 0.0,
            "error_type": type(exc).__name__,
            "message": str(exc),
        }
        return summary
    reference_bins = parsed["bins"]
    aligned = []
    for bin_index in sorted(set(local).intersection(reference_bins)):
        local_bin = local[bin_index]
        reference_bin = reference_bins[bin_index]
        deltas = {}
        for signal in (
            "speed_mph",
            "brake",
            "throttle",
            "steering_abs_rad",
            "lateral_g",
        ):
            local_value = _finite_number(local_bin.get(signal))
            reference_value = _finite_number(reference_bin.get(signal))
            if local_value is not None and reference_value is not None:
                deltas[signal] = round(local_value - reference_value, 6)
        aligned.append(
            {
                "bin": bin_index,
                "lap_pct": round(float(local_bin["lap_pct"]), 6),
                "local": {
                    key: value
                    for key, value in local_bin.items()
                    if key not in {"bin", "lap_pct"} and value is not None
                },
                "reference": {
                    key: value
                    for key, value in reference_bin.items()
                    if key not in {"bin", "lap_pct"} and value is not None
                },
                "delta_local_minus_reference": deltas,
            }
        )
    coverage = len(aligned) / max(1, len(local))
    minimum_bins = max(4, math.ceil(len(local) * 0.25))
    usable = len(aligned) >= minimum_bins and "speed_mph" in parsed["quality"].get("signals", ())
    summary["quality"] = {
        **parsed["quality"],
        "status": "usable" if usable else "insufficient_alignment",
        "usable": usable,
        "local_bins": len(local),
        "aligned_bins": len(aligned),
        "minimum_aligned_bins": minimum_bins,
        "coverage_fraction": round(coverage, 4),
        "delta_definition": "local minus reference; positive speed means local is faster",
    }
    summary["aligned_bins"] = aligned
    summary["_reference_profile"] = reference_bins
    return summary


def _zone_contains(lap_pct: float, start: float, end: float, wraps: bool) -> bool:
    if wraps or start > end:
        return lap_pct >= start or lap_pct <= end
    return start <= lap_pct <= end


def _ordered_zone_bins(
    values: Sequence[Mapping[str, Any]], start: float, end: float, wraps: bool
) -> list[Mapping[str, Any]]:
    selected = [
        item
        for item in values
        if (pct := _finite_number(item.get("lap_pct"))) is not None
        and _zone_contains(pct, start, end, wraps)
    ]
    return sorted(
        selected,
        key=lambda item: (
            (_finite_number(item.get("lap_pct")) or 0.0) + 1.0
            if wraps and (_finite_number(item.get("lap_pct")) or 0.0) <= end
            else (_finite_number(item.get("lap_pct")) or 0.0)
        ),
    )


def _event_pct(
    values: Sequence[Mapping[str, Any]], signal: str, threshold: float, after: str
) -> float | None:
    usable = [
        item
        for item in values
        if _finite_number(item.get(signal)) is not None
        and _finite_number(item.get("speed_mph")) is not None
    ]
    if not usable:
        return None
    if after == "minimum_speed":
        pivot = min(
            range(len(usable)),
            key=lambda index: _finite_number(usable[index].get("speed_mph")) or math.inf,
        )
    elif after == "peak_brake":
        pivot = max(
            range(len(usable)),
            key=lambda index: _finite_number(usable[index].get("brake")) or 0.0,
        )
    else:
        pivot = 0
    for item in usable[pivot:]:
        value = _finite_number(item.get(signal))
        if value is None:
            continue
        if (after in {"minimum_speed", "zone_start"} and value >= threshold) or (
            after == "peak_brake" and value <= threshold
        ):
            return _finite_number(item.get("lap_pct"))
    return None


def _round_delta(value: float | None, digits: int = 4) -> float | None:
    return round(value, digits) if value is not None and math.isfinite(value) else None


def _zone_coaching_target(
    zone: Mapping[str, Any],
    local_values: Sequence[Mapping[str, Any]],
    reference_values: Sequence[Mapping[str, Any]],
    reference_count: int,
) -> dict[str, Any] | None:
    start = _finite_number(zone.get("start_pct"))
    end = _finite_number(zone.get("end_pct"))
    if start is None or end is None:
        return None
    wraps = bool(zone.get("wraps_start_finish")) or start > end
    local = _ordered_zone_bins(local_values, start, end, wraps)
    reference = _ordered_zone_bins(reference_values, start, end, wraps)
    local_by_bin = {item.get("bin"): item for item in local}
    reference_by_bin = {item.get("bin"): item for item in reference}
    shared_bins = set(local_by_bin).intersection(reference_by_bin)
    local = [item for item in local if item.get("bin") in shared_bins]
    reference = [item for item in reference if item.get("bin") in shared_bins]
    if len(local) < 3 or len(reference) < 3:
        return None

    local_entry = _finite_number(local[0].get("speed_mph"))
    reference_entry = _finite_number(reference[0].get("speed_mph"))
    local_minimum = min(
        (value for item in local if (value := _finite_number(item.get("speed_mph"))) is not None),
        default=None,
    )
    reference_minimum = min(
        (value for item in reference if (value := _finite_number(item.get("speed_mph"))) is not None),
        default=None,
    )
    local_peak_brake = max(
        (value for item in local if (value := _finite_number(item.get("brake"))) is not None),
        default=None,
    )
    reference_peak_brake = max(
        (value for item in reference if (value := _finite_number(item.get("brake"))) is not None),
        default=None,
    )
    local_steering = _median_value(item.get("steering_abs_rad") for item in local)
    reference_steering = _median_value(item.get("steering_abs_rad") for item in reference)
    local_throttle = _event_pct(local, "throttle", 0.75, "minimum_speed")
    reference_throttle = _event_pct(reference, "throttle", 0.75, "minimum_speed")
    local_onset = _event_pct(local, "brake", 0.05, "zone_start")
    reference_onset = _event_pct(reference, "brake", 0.05, "zone_start")
    local_release = _event_pct(local, "brake", 0.05, "peak_brake")
    reference_release = _event_pct(reference, "brake", 0.05, "peak_brake")

    entry_delta = (
        local_entry - reference_entry
        if local_entry is not None and reference_entry is not None
        else None
    )
    minimum_delta = (
        local_minimum - reference_minimum
        if local_minimum is not None and reference_minimum is not None
        else None
    )
    brake_delta = (
        local_peak_brake - reference_peak_brake
        if local_peak_brake is not None and reference_peak_brake is not None
        else None
    )
    steering_delta = (
        local_steering - reference_steering
        if local_steering is not None and reference_steering is not None
        else None
    )
    def timing_delta(local_value: float | None, reference_value: float | None) -> float | None:
        if local_value is None or reference_value is None:
            return None
        delta = local_value - reference_value
        if wraps and delta > 0.5:
            delta -= 1.0
        elif wraps and delta < -0.5:
            delta += 1.0
        return delta

    throttle_delta = (
        timing_delta(local_throttle, reference_throttle)
        if local_throttle is not None and reference_throttle is not None
        else None
    )
    onset_delta = (
        timing_delta(local_onset, reference_onset)
        if local_onset is not None and reference_onset is not None
        else None
    )
    release_delta = (
        timing_delta(local_release, reference_release)
        if local_release is not None and reference_release is not None
        else None
    )

    actions: list[str] = []
    if entry_delta is not None and entry_delta > 1.0:
        actions.append("match the reference entry speed before adding steering load")
    elif entry_delta is not None and entry_delta < -1.0 and minimum_delta is not None and minimum_delta < -1.0:
        actions.append("carry the reference entry speed without sacrificing minimum speed")
    if onset_delta is not None and onset_delta > 0.01:
        actions.append("begin braking at the earlier reference onset")
    elif onset_delta is not None and onset_delta < -0.01:
        actions.append("delay initial brake application toward the reference onset")
    if release_delta is not None and release_delta > 0.01:
        actions.append("finish brake release earlier")
    if steering_delta is not None and steering_delta > 0.02:
        actions.append("use less steering angle through the loaded phase")
    if throttle_delta is not None and throttle_delta > 0.01:
        actions.append("begin the 75% throttle pickup earlier")
    if not actions:
        actions.append("repeat the reference control timing and validate the delta on the next run")

    return {
        "name": f"Load zone {zone.get('segment') or '?'}",
        "start_pct": round(start, 5),
        "end_pct": round(end, 5),
        "wraps_start_finish": wraps,
        "entry_speed_mph": _round_delta(reference_entry, 2),
        "minimum_speed_mph": _round_delta(reference_minimum, 2),
        "local_entry_speed_mph": _round_delta(local_entry, 2),
        "local_minimum_speed_mph": _round_delta(local_minimum, 2),
        "entry_speed_delta_mph": _round_delta(entry_delta, 2),
        "minimum_speed_delta_mph": _round_delta(minimum_delta, 2),
        "peak_brake_delta": _round_delta(brake_delta, 4),
        "steering_delta_rad": _round_delta(steering_delta, 4),
        "throttle_pickup_delta_lap_pct": _round_delta(throttle_delta, 5),
        "brake_onset_delta_lap_pct": _round_delta(onset_delta, 5),
        "brake_release_delta_lap_pct": _round_delta(release_delta, 5),
        "reference_throttle_pickup_pct": _round_delta(reference_throttle, 5),
        "reference_brake_onset_pct": _round_delta(reference_onset, 5),
        "reference_brake_release_pct": _round_delta(reference_release, 5),
        "coaching": "; ".join(actions).capitalize() + ".",
        "quality": {
            "reference_laps": reference_count,
            "local_bins": len(local),
            "reference_bins": len(reference),
            "alignment": "lap-distance-bin median",
            "delta_definition": "local minus reference",
            "brake_onset_definition": "first aligned zone bin with brake >= 0.05",
            "local_brake_onset_boundary_censored": (
                (_finite_number(local[0].get("brake")) or 0.0) >= 0.05
            ),
            "reference_brake_onset_boundary_censored": (
                (_finite_number(reference[0].get("brake")) or 0.0) >= 0.05
            ),
            "corner_name_status": "telemetry_load_zone_not_official_corner_name",
        },
    }


def _comparison_analysis(
    analysis: Mapping[str, Any],
    references: Sequence[Mapping[str, Any]],
    bundle_path: Path,
    local_setup: str | None,
) -> dict[str, Any]:
    bins, local_bins = _local_track_bins(analysis)
    comparisons = [
        _aligned_reference_comparison(analysis, reference, bundle_path)
        for reference in references
    ]
    usable = [item for item in comparisons if (item.get("quality") or {}).get("usable")]
    same_setup = [item for item in usable if item.get("setup_type") == local_setup]
    benchmark_sources = same_setup or usable
    benchmark: dict[int, dict[str, Any]] = {}
    for bin_index in sorted(local_bins):
        profiles = [
            item.get("_reference_profile", {}).get(bin_index)
            for item in benchmark_sources
        ]
        profiles = [item for item in profiles if isinstance(item, Mapping)]
        if not profiles:
            continue
        benchmark[bin_index] = {
            "bin": bin_index,
            "lap_pct": (bin_index + 0.5) / bins,
        }
        for signal in (
            "speed_mph",
            "brake",
            "throttle",
            "steering_abs_rad",
            "lateral_g",
        ):
            value = _median_value(profile.get(signal) for profile in profiles)
            if value is not None:
                benchmark[bin_index][signal] = value

    zones = (
        (analysis.get("track_profile") or {}).get("detected_corner_segments", ())
        or ()
    )
    targets = []
    for zone in zones:
        if not isinstance(zone, Mapping):
            continue
        target = _zone_coaching_target(
            zone,
            list(local_bins.values()),
            list(benchmark.values()),
            len(benchmark_sources),
        )
        if target is not None:
            targets.append(target)

    coverages = [
        _finite_number((item.get("quality") or {}).get("coverage_fraction"))
        for item in usable
    ]
    coverage = _median_value(value for value in coverages if value is not None)
    for item in comparisons:
        item.pop("_reference_profile", None)
    status = "usable"
    reason = None
    if not local_bins:
        status, reason = "unavailable", "Local telemetry has no lap-distance track profile."
    elif not usable:
        status, reason = "unavailable", "No downloaded reference CSV aligned with enough local bins."
    elif not benchmark:
        status, reason = "unavailable", "Reference CSVs did not expose aligned control/speed bins."
    elif not targets:
        status, reason = "partial", "Aligned bins exist, but no local load zone had enough shared samples for an exact target."
    quality = {
        "status": status,
        "usable": status in {"usable", "partial"},
        "reason": reason,
        "local_setup_type": local_setup,
        "local_profile_bins": len(local_bins),
        "downloaded_reference_laps": len(references),
        "usable_reference_laps": len(usable),
        "same_setup_reference_laps": len(same_setup),
        "benchmark_reference_laps": len(benchmark_sources),
        "median_coverage_fraction": _round_delta(coverage, 4),
        "alignment": "local and Garage61 telemetry median values in identical lap-distance bins",
        "target_rule": "Targets are emitted only for telemetry-derived load zones with at least three aligned bins.",
        "setup_scope": (
            "same_setup_only"
            if same_setup
            else "cross_setup_fallback" if usable else "unavailable"
        ),
    }
    return {
        "quality": quality,
        "references": comparisons,
        "benchmark_profile": [benchmark[index] for index in sorted(benchmark)],
        "coaching_targets": targets,
    }


def _merge_garage_source(
    sources: Iterable[Mapping[str, Any]], base_url: str, scope: str
) -> list[dict[str, Any]]:
    result = [
        dict(item)
        for item in sources
        if not (
            str(item.get("kind", "")).lower() == "garage61-api"
            or str(item.get("url", "")).rstrip("/") == base_url.rstrip("/")
        )
    ]
    result.append(
        {
            "kind": "garage61-api",
            "url": base_url,
            "title": "Garage61 official API",
            "retrieved_at": utc_now(),
            "comparison_scope": scope,
        }
    )
    return result


def _read_analysis(path: str | os.PathLike[str]) -> tuple[Path, dict[str, Any]]:
    target = Path(path).expanduser().resolve(strict=True)
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkflowError(f"Analysis artifact is unreadable: {target}: {exc}") from exc
    if not isinstance(value, dict) or not isinstance(value.get("identity"), Mapping):
        raise WorkflowError(f"Analysis artifact has an invalid shape: {target}")
    return target, value


def garage61_sync_workflow(
    *,
    analysis_path: str | os.PathLike[str],
    archive_root: str | os.PathLike[str] | None = None,
    maximum_laps: int = 8,
    download_telemetry: bool = True,
) -> dict[str, Any]:
    """Rank and cache comparable fixed/open Garage61 reference laps."""

    maximum = _positive_bounded_int(maximum_laps, "maximum_laps", 50)
    resolved_analysis_path, analysis = _read_analysis(analysis_path)
    store = ArchiveStore(archive_root)
    context = store.context_from_analysis(analysis)
    prior_cache_status = store.cache_status(context)
    prior_manifest = (
        prior_cache_status.get("manifest")
        if prior_cache_status.get("state") in {"fresh", "incomplete", "stale"}
        and isinstance(prior_cache_status.get("manifest"), Mapping)
        else {}
    )
    prior_sim_physics_fingerprint = prior_manifest.get("sim_physics_fingerprint")
    components = (
        _bundle_components(store, context)
        if prior_cache_status.get("state") in {"fresh", "incomplete", "stale"}
        else {
            "facts": {},
            "sources": [],
            "garage61": {},
            "track_shape": {},
            "notes_markdown": None,
        }
    )
    base_url = _garage61_base_url()
    try:
        client = Garage61Client(
            base_url=base_url,
            global_visible_laps_approved=_garage61_global_visible_laps_approved(),
        )
    except SecureStoreError as exc:
        raise Garage61AuthError(str(exc)) from exc
    health = client.health_check()
    personal_scope = (
        (health.get("capabilities") or {})
        .get("personal_and_team_laps", {})
        .get("available")
    )
    if personal_scope is not True:
        raise Garage61PermissionError(
            "Garage61 has not granted the driving_data permission required for lap comparisons."
        )

    catalog = client.content_catalog()
    identity = analysis.get("identity") or {}
    car = _resolve_garage_car(catalog, identity)
    track = _resolve_garage_track(catalog, identity)
    season = _resolve_garage_season(catalog, identity)
    target = _garage_target(analysis, car, track, season)
    local_setup = target.get("_localSetupType")

    cohorts: dict[str, list[dict[str, Any]]] = {}
    cohort_errors: dict[str, dict[str, str]] = {}
    search_limit = min(1000, max(100, maximum * 25))
    for setup_type in ("fixed", "open"):
        try:
            ranked = client.find_comparable_laps(
                target,
                setup_type=setup_type,
                top_n=maximum,
                search_limit=search_limit,
            )
            cohorts[setup_type] = [_ranked_dict(item) for item in ranked]
            _annotate_comparison_roles(cohorts[setup_type])
        except Garage61Error as exc:
            cohorts[setup_type] = []
            cohort_errors[setup_type] = {
                "error_type": type(exc).__name__,
                "message": str(exc),
            }
    if len(cohort_errors) == 2:
        diagnostics = "; ".join(
            f"{name}: {value['message']}" for name, value in cohort_errors.items()
        )
        raise WorkflowError(f"Garage61 comparable-lap searches failed: {diagnostics}")

    representatives = _select_representatives(cohorts, local_setup, maximum)
    bundle_path = store.cache_path(context)
    prior_csv_hashes = {
        str((item.get("lap") or {}).get("id")): str(
            (item.get("telemetry") or {}).get("sha256") or ""
        )
        for item in (components.get("garage61") or {}).get("representative_laps", ())
        if isinstance(item, Mapping) and (item.get("lap") or {}).get("id") is not None
    }
    for reference in representatives:
        lap_id = str((reference.get("lap") or {}).get("id") or "")
        _cache_reference_csv(
            client,
            reference,
            bundle_path,
            bool(download_telemetry),
            prior_csv_hashes.get(lap_id) or None,
        )
    comparison = _comparison_analysis(
        analysis, representatives, bundle_path, local_setup
    )

    global_available = (
        (health.get("capabilities") or {})
        .get("global_visible_laps", {})
        .get("available")
        is True
    )
    scope = "approved-global-visible" if global_available else "own/team"
    index = {
        "schema_version": 1,
        "target_derivation_version": _GARAGE61_TARGET_DERIVATION_VERSION,
        "synced_at": utc_now(),
        "authentication": {
            "method": "bearer-pat-from-windows-user-dpapi",
            "healthy": health.get("ok") is True,
            "identity": health.get("identity") or {},
            "api_permissions": list(health.get("api_permissions") or ()),
        },
        "comparison_scope": scope,
        "scope_diagnostic": (
            (health.get("capabilities") or {})
            .get("global_visible_laps", {})
            .get("diagnostic")
        ),
        "api_permissions": list(health.get("api_permissions") or ()),
        "content_mapping": {
            "car": dict(car),
            "track": dict(track),
            "season": dict(season),
        },
        "target": target,
        "queries": {
            setup_type: {
                "setup_type": setup_type,
                "car_id": _mapping_id(car),
                "track_id": _mapping_id(track),
                "season_id": _mapping_id(season),
                "clean_only": True,
                "telemetry_required": True,
                "result_count": len(cohorts.get(setup_type, ())),
                "error": cohort_errors.get(setup_type),
            }
            for setup_type in ("fixed", "open")
        },
        "cohorts": cohorts,
        "representative_laps": representatives,
        "reference_comparisons": comparison["references"],
        "benchmark_profile": comparison["benchmark_profile"],
        "coaching_targets": comparison["coaching_targets"],
        "comparison_quality": comparison["quality"],
    }
    representative_contract = {
        "schema_version": 1,
        "target_derivation_version": _GARAGE61_TARGET_DERIVATION_VERSION,
        "status": "available" if representatives else "unavailable",
        "reason": None if representatives else "No comparable Garage61 laps were returned for this exact context.",
        "comparison_scope": scope,
        "representative_laps": representatives,
        "reference_comparisons": comparison["references"],
        "comparison_quality": comparison["quality"],
    }
    analysis["garage61_representative_laps"] = representative_contract
    target_lap_cache = store.cache_garage61_target_laps(context, index)

    sources = _merge_garage_source(components["sources"], base_url, scope)
    manifest = store.write_knowledge_bundle(
        context,
        sources=sources,
        facts=components["facts"],
        garage61=index,
        track_shape=components["track_shape"] or None,
        notes_markdown=components["notes_markdown"],
        sim_physics_fingerprint=prior_sim_physics_fingerprint,
    )

    # Refresh the deterministic report so a later explicit sync is visible in
    # the same reporting contract, without changing the local analysis facts.
    cache_status = store.cache_status(context)
    history = store.historical_runs(context, include_other_seasons=True)
    knowledge = _knowledge_for_report(store, context, cache_status)
    report = render_report(analysis, historical_runs=history, knowledge=knowledge)
    artifacts = store.save_report_artifacts(
        analysis,
        report,
        extra_files=render_visuals(analysis),
    )
    store.record_analysis(analysis, artifacts["report"])

    downloaded = sum(
        1
        for item in representatives
        if (item.get("telemetry") or {}).get("status") == "downloaded"
    )
    cached = sum(
        1
        for item in representatives
        if (item.get("telemetry") or {}).get("status") == "cached"
    )
    failed = sum(
        1
        for item in representatives
        if (item.get("telemetry") or {}).get("status") == "failed"
    )
    status = "complete"
    if cohort_errors or failed:
        status = "partial"
    elif not representatives:
        status = "no_matches"
    return {
        "ok": status == "complete",
        "status": status,
        "analysis_path": str(resolved_analysis_path),
        "context": context,
        "comparison_scope": scope,
        "representative_lap_count": len(representatives),
        "telemetry": {
            "requested": bool(download_telemetry),
            "downloaded": downloaded,
            "already_cached": cached,
            "failed": failed,
        },
        "cohort_counts": {
            name: len(cohorts.get(name, ())) for name in ("fixed", "open")
        },
        "cohort_errors": cohort_errors,
        "representative_laps": representatives,
        "garage61_representative_laps": representative_contract,
        "comparison_quality": comparison["quality"],
        "coaching_targets": comparison["coaching_targets"],
        "cache": {
            "path": str(bundle_path),
            "manifest": manifest,
            "garage61_index": str(bundle_path / "garage61" / "index.json"),
            "portable_target_laps": target_lap_cache,
        },
        "report_artifacts": artifacts,
    }


# Keep tuning logic isolated from the large race/Garage61 pipeline while
# re-exporting one stable workflow surface for the CLI and MCP server.
try:  # Support both package imports and direct script-folder execution.
    from .tuning_workflow import (  # noqa: E402
        build_open_setup_package_workflow,
        catalog_iracing_setups_workflow,
        iracing_setup_history_workflow,
        recommend_open_setup_tuning_workflow,
        recommend_structured_open_setup_tuning_workflow,
        record_open_setup_feedback_workflow,
    )
except ImportError:  # pragma: no cover - normal CLI/MCP loading path.
    from tuning_workflow import (  # type: ignore[no-redef]  # noqa: E402
        build_open_setup_package_workflow,
        catalog_iracing_setups_workflow,
        iracing_setup_history_workflow,
        recommend_open_setup_tuning_workflow,
        recommend_structured_open_setup_tuning_workflow,
        record_open_setup_feedback_workflow,
    )


__all__ = [
    "Garage61MappingError",
    "SessionSelectionError",
    "WorkflowError",
    "analyze_race_workflow",
    "build_open_setup_package_workflow",
    "catalog_iracing_setups_workflow",
    "companion_dashboard_workflow",
    "discover_sessions_workflow",
    "garage61_status_workflow",
    "garage61_sync_workflow",
    "inventory_iracing_data_workflow",
    "iracing_data_inventory_workflow",
    "iracing_local_inventory_workflow",
    "iracing_setup_history_workflow",
    "native_event_search_workflow",
    "recommend_open_setup_tuning_workflow",
    "recommend_structured_open_setup_tuning_workflow",
    "record_open_setup_feedback_workflow",
    "telemetry_query_workflow",
]

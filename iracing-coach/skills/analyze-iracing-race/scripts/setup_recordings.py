"""Link local setup files to the analysed recordings that loaded them.

An iRacing ``.sto`` file is an encrypted binary, so its garage values cannot be
decoded. A recording's session info names what was driven instead:
``DriverInfo.DriverSetupName`` is the loaded file name,
``DriverSetupLoadTypeName`` says whether it came from the user's library
(``user``), iRacing's baseline setups (``iracing``) or a fixed series
(``fixed``), ``DriverSetupIsModified`` says whether the garage was changed
after loading, and ``CarSetup`` holds the garage values that were driven.

A library file therefore has known values only through its exported setup
sheet, or through an analysed recording that loaded exactly that file from the
user's library without changes. Nothing here guesses a link: the car folder and
file name must both match what the recording names, and a name that exists in
more than one folder for the same car is reported as ambiguous.
"""

from __future__ import annotations

import copy
import os
import re
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any, Callable, Iterable, Mapping, Sequence

SCHEMA_VERSION = 1
MAX_RECORDINGS = 200

# Tire readings iRacing stores inside CarSetup describe the last run, not the
# garage setup; UpdateCount is a session-info revision counter.
_READING_PREFIXES = ("lasthotpressure", "lasttemps", "treadremaining")
_NON_SETUP_KEYS = {"updatecount"}

NO_VALUES_REASON = (
    "Values unknown: .sto files are encrypted. Analyze a recording that loaded "
    "this file unchanged, or export its setup sheet from iRacing."
)
MODIFIED_REASON = (
    "Only recorded with unsaved garage changes, so no recording shows this "
    "file's own values."
)
UNVERIFIED_REASON = (
    "Its recordings don't say whether the garage was changed after loading, "
    "so their values aren't attributed to this file."
)
AMBIGUOUS_REASON = (
    "Another file with this name exists for this car; recordings name only the "
    "file, so they can't be matched to one of them."
)

SessionInfoReader = Callable[[str], Mapping[str, Any]]
_identity_cache: dict[tuple[str, int, int, int], dict[str, Any]] = {}


def _key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _flag(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value or "").strip().casefold()
    if text in {"1", "true", "yes"}:
        return True
    if text in {"0", "false", "no"}:
        return False
    return None


def is_setup_reading(name: Any) -> bool:
    """True for a CarSetup leaf that is a run reading or counter, not a setting."""

    key = _key(name)
    return key in _NON_SETUP_KEYS or key.startswith(_READING_PREFIXES)


def garage_values(car_setup: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return the CarSetup tree without tire readings and revision counters."""

    def strip(value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        result: dict[str, Any] = {}
        for name, child in value.items():
            if is_setup_reading(name):
                continue
            cleaned = strip(child)
            if isinstance(cleaned, dict) and not cleaned:
                continue
            result[str(name)] = cleaned
        return result

    cleaned = strip(car_setup if isinstance(car_setup, Mapping) else {})
    return cleaned if isinstance(cleaned, dict) else {}


def _leaf_count(value: Any) -> int:
    if isinstance(value, Mapping):
        return sum(_leaf_count(child) for child in value.values())
    return 1


def _driver(driver_info: Mapping[str, Any]) -> Mapping[str, Any]:
    car_idx = driver_info.get("DriverCarIdx")
    for candidate in driver_info.get("Drivers") or ():
        if isinstance(candidate, Mapping) and str(candidate.get("CarIdx")) == str(car_idx):
            return candidate
    return {}


def _garage_fingerprint(car_setup: Mapping[str, Any] | None) -> str | None:
    try:  # Package import and direct script execution are both supported.
        from .setup_catalog import garage_setup_fingerprint
    except ImportError:  # pragma: no cover - normal CLI/MCP script-loading path.
        from setup_catalog import garage_setup_fingerprint  # type: ignore[no-redef]
    fingerprint = garage_setup_fingerprint(car_setup)
    # Sixteen characters, exactly as an analysis identity stores it, so a file's
    # recorded values and the recording's own setup name the same adjustments.
    return fingerprint[:16] if fingerprint else None


def recorded_setup_identity(session_info: Mapping[str, Any]) -> dict[str, Any]:
    """Read exactly what one recording says about the setup that was driven.

    The recording also names where it was driven (``WeekendInfo``), which is the
    only recorded track a setup file has: its name is free text.
    """

    driver_info = session_info.get("DriverInfo")
    driver_info = driver_info if isinstance(driver_info, Mapping) else {}
    weekend = session_info.get("WeekendInfo")
    weekend = weekend if isinstance(weekend, Mapping) else {}
    modified = driver_info.get("DriverSetupIsModified")
    if modified is None:  # Older synthetic fixtures used this spelling.
        modified = driver_info.get("DriverSetupModified")
    car_setup = session_info.get("CarSetup")
    car_setup = car_setup if isinstance(car_setup, Mapping) else None
    return {
        "setup_name": _text(driver_info.get("DriverSetupName")),
        "load_type": (_text(driver_info.get("DriverSetupLoadTypeName")) or "").casefold() or None,
        "modified": _flag(modified),
        "car_path": _text(_driver(driver_info).get("CarPath")),
        # Named as the analysis identity names them, so a file and its recording
        # read the same track ("Lucas Oil Indianapolis Raceway Park · IRP").
        "track_name": _text(weekend.get("TrackDisplayName")) or _text(weekend.get("TrackName")),
        "track_config": _text(weekend.get("TrackConfigName")) or _text(weekend.get("TrackDisplayShortName")),
        "track_path": _text(weekend.get("TrackName")),
        "garage": garage_values(car_setup),
        # The adjustments alone (no tire readings, no computed readouts), so two
        # loads of one file whose corner weights read a newton apart still agree.
        "garage_fingerprint": _garage_fingerprint(car_setup),
    }


def _scan_session_info(path: str) -> Mapping[str, Any]:
    try:  # Package import and direct script execution are both supported.
        from .ibt_reader import scan_ibt
    except ImportError:  # pragma: no cover - normal CLI/MCP script-loading path.
        from ibt_reader import scan_ibt  # type: ignore[no-redef]
    info = scan_ibt(path).get("session_info")
    return info if isinstance(info, Mapping) else {}


def _cached_identity(path: str, reader: SessionInfoReader) -> dict[str, Any]:
    # Only the compact identity is kept; an unchanged recording is not re-read.
    stat = os.stat(path)
    key = (os.path.normcase(os.path.abspath(path)), stat.st_size, stat.st_mtime_ns, id(reader))
    cached = _identity_cache.get(key)
    if cached is None:
        cached = recorded_setup_identity(reader(path))
        if len(_identity_cache) >= 2 * MAX_RECORDINGS:
            _identity_cache.clear()
        _identity_cache[key] = cached
    return copy.deepcopy(cached)


def read_recording_setup_uses(
    analyses: Iterable[Mapping[str, Any]],
    *,
    reader: SessionInfoReader | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Read the setup each analysed recording loaded from its source IBT.

    ``analyses`` are archive session rows (``ArchiveStore.recent_analyses``).
    Rows without an available report or source recording are counted, not
    guessed. The source IBT is read for session info only.
    """

    read = reader or _scan_session_info
    try:
        from .ibt_reader import IbtError
    except ImportError:  # pragma: no cover - normal CLI/MCP script-loading path.
        from ibt_reader import IbtError  # type: ignore[no-redef]
    uses: list[dict[str, Any]] = []
    counts = {"considered": 0, "unavailable": 0, "unreadable": 0, "read": 0}
    for row in list(analyses)[:MAX_RECORDINGS]:
        counts["considered"] += 1
        analysis_path = _text(row.get("analysis_path"))
        source_path = _text(row.get("source_path"))
        if not (row.get("analysis_available") and analysis_path and source_path and row.get("source_available")):
            counts["unavailable"] += 1
            continue
        try:
            identity = _cached_identity(source_path, read)
        except FileNotFoundError:
            counts["unavailable"] += 1
            continue
        except (IbtError, OSError, ValueError):
            counts["unreadable"] += 1
            continue
        counts["read"] += 1
        uses.append(
            {
                **identity,
                "analysis_path": analysis_path,
                "analysis_id": _text(row.get("analysis_id")),
                "session_start": _text(row.get("session_start")),
                "session_type": _text(row.get("sim_session_type")) or _text(row.get("session_phase")),
                "subsession_id": _text(row.get("subsession_id")),
                "source_path": source_path,
            }
        )
    return uses, counts


def _parse_time(value: Any) -> datetime | None:
    text = _text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _use_summary(use: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "analysis_path": use.get("analysis_path"),
        "analysis_id": use.get("analysis_id"),
        "session_start": use.get("session_start"),
        "session_type": use.get("session_type"),
        "subsession_id": use.get("subsession_id"),
        "source_path": use.get("source_path"),
        "track_name": use.get("track_name"),
        "track_config": use.get("track_config"),
    }


def _linked_use_summary(use: Mapping[str, Any], values: Mapping[str, Any] | None) -> dict[str, Any]:
    """A linked recording, and whether it drove exactly the values shown for the file.

    ``values_match`` compares adjustment fingerprints (no run readings, no
    computed readouts), so the Setups library can list a file and the
    recordings that loaded it unchanged with identical adjustments as one
    setup. It is ``None`` when either side has no fingerprint: unknown is
    never reported as equal or as different.
    """

    fingerprint = _text(use.get("garage_fingerprint"))
    shown = _text((values or {}).get("garage_fingerprint"))
    return {
        **_use_summary(use),
        "garage_fingerprint": fingerprint,
        "values_match": (fingerprint == shown) if fingerprint and shown else None,
    }


def _recordings_agree(uses: Sequence[Mapping[str, Any]], garage: Mapping[str, Any]) -> bool:
    """Whether every unmodified recording of a file drove the same adjustments.

    Computed readouts (corner and cross weight, ride heights, ...) drift by a
    unit between loads of one file, so recordings are compared by their
    adjustment fingerprint when both have one, and by the whole garage tree
    otherwise.
    """

    newest = uses[0].get("garage_fingerprint")
    for item in uses:
        fingerprint = item.get("garage_fingerprint")
        if newest and fingerprint:
            if fingerprint != newest:
                return False
        elif (item.get("garage") or {}) != garage:
            return False
    return True


def _source_names(entry: Mapping[str, Any]) -> list[tuple[Mapping[str, Any], str, str]]:
    """Return (source, car folder key, path-within-car-folder key) per .sto source."""

    result: list[tuple[Mapping[str, Any], str, str]] = []
    car = str(entry.get("car_folder") or "").casefold()
    for source in (entry.get("sources") or {}).get("sto") or ():
        if not isinstance(source, Mapping):
            continue
        relative = PurePosixPath(str(source.get("relative_path") or source.get("name") or ""))
        parts = relative.parts
        # relative_path is below the setups root, whose first part is the car folder.
        within = PurePosixPath(*parts[1:]) if len(parts) > 1 and parts[0].casefold() == car else relative
        result.append((source, car, str(within).casefold()))
    return result


def _named_file(setup_name: str) -> tuple[str, bool]:
    normalized = setup_name.replace("\\", "/").strip("/")
    return normalized.casefold(), "/" in normalized


def link_recorded_setups(
    entries: Sequence[Mapping[str, Any]],
    uses: Sequence[Mapping[str, Any]],
    *,
    catalog: Sequence[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Annotate catalog entries with the analysed recordings that loaded them.

    Returns deep copies; each carries ``recorded_setup`` and ``values_status``.
    ``catalog`` is the whole catalog ``entries`` were taken from (it defaults to
    ``entries``). A file name is ambiguous when it occurs more than once for a
    car anywhere in it, so one page of the catalog never links a name whose
    duplicate is on another page.
    """

    annotated = [copy.deepcopy(dict(entry)) for entry in entries]
    by_name: dict[tuple[str, str], list[tuple[int, Mapping[str, Any]]]] = {}
    by_path: dict[tuple[str, str], list[tuple[int, Mapping[str, Any]]]] = {}
    for index, entry in enumerate(annotated):
        for source, car, within in _source_names(entry):
            by_name.setdefault((car, PurePosixPath(within).name), []).append((index, source))
            by_path.setdefault((car, within), []).append((index, source))
    name_counts: dict[tuple[str, str], int] = {}
    path_counts: dict[tuple[str, str], int] = {}
    for entry in annotated if catalog is None else catalog:
        for _source, car, within in _source_names(entry):
            name_key, path_key = (car, PurePosixPath(within).name), (car, within)
            name_counts[name_key] = name_counts.get(name_key, 0) + 1
            path_counts[path_key] = path_counts.get(path_key, 0) + 1

    buckets = ("uses", "modified", "unverified", "ambiguous")
    linked: dict[int, dict[str, list[Mapping[str, Any]]]] = {}
    for use in uses:
        setup_name, car_path = _text(use.get("setup_name")), _text(use.get("car_path"))
        if use.get("load_type") != "user" or not setup_name or not car_path:
            continue  # iRacing baseline and fixed setups are not library files.
        name, has_folder = _named_file(setup_name)
        key = (car_path.casefold(), name)
        candidates = (by_path if has_folder else by_name).get(key, [])
        if not candidates:
            continue
        occurrences = max(len(candidates), (path_counts if has_folder else name_counts).get(key, 0))
        if occurrences > 1:
            bucket = "ambiguous"
        elif use.get("modified") is False:
            bucket = "uses"
        elif use.get("modified") is True:
            bucket = "modified"
        else:
            bucket = "unverified"
        for index, source in candidates:
            state = linked.setdefault(index, {name: [] for name in buckets})
            state[bucket].append({**use, "_source": source})

    for index, entry in enumerate(annotated):
        state = linked.get(index, {name: [] for name in buckets})
        ordered = {
            name: sorted(
                items,
                key=lambda item: (_parse_time(item.get("session_start")) or datetime.min.replace(tzinfo=timezone.utc)),
                reverse=True,
            )
            for name, items in state.items()
        }
        values: dict[str, Any] | None = None
        if ordered["uses"]:
            newest = ordered["uses"][0]
            garage = newest.get("garage") or {}
            recorded_at = _parse_time(newest.get("session_start"))
            saved_at = _parse_time((newest.get("_source") or {}).get("modified_utc"))
            if garage:
                values = {
                    "source": "recording",
                    "analysis_path": newest.get("analysis_path"),
                    "analysis_id": newest.get("analysis_id"),
                    "session_start": newest.get("session_start"),
                    "session_type": newest.get("session_type"),
                    "track_name": newest.get("track_name"),
                    "track_config": newest.get("track_config"),
                    "setup": garage,
                    "field_count": _leaf_count(garage),
                    "garage_fingerprint": newest.get("garage_fingerprint"),
                    "file_saved_after_recording": bool(recorded_at and saved_at and saved_at > recorded_at),
                    "recordings_agree": _recordings_agree(ordered["uses"], garage),
                }
        if ordered["uses"]:
            status, reason_code, reason = "linked", None, None
        elif ordered["ambiguous"]:
            status, reason_code, reason = "ambiguous", "ambiguous_file_name", AMBIGUOUS_REASON
        elif ordered["modified"]:
            status, reason_code, reason = "modified_only", "recorded_with_changes", MODIFIED_REASON
        elif ordered["unverified"]:
            status, reason_code, reason = "unverified", "change_state_not_recorded", UNVERIFIED_REASON
        else:
            status, reason_code, reason = "none", None, None
        # Most library files were never recorded; keep their annotation compact.
        entry["recorded_setup"] = {"schema_version": SCHEMA_VERSION, "status": status} if status == "none" else {
            "schema_version": SCHEMA_VERSION,
            "status": status,
            "uses": [_linked_use_summary(item, values) for item in ordered["uses"]],
            "modified_uses": [_use_summary(item) for item in ordered["modified"]],
            "ambiguous_use_count": len(ordered["ambiguous"]),
            "values": values,
            "reason_code": reason_code,
            "reason": reason,
        }
        parsed = entry.get("parsed_html")
        export_fields = parsed.get("field_count") if isinstance(parsed, Mapping) else 0
        if export_fields:
            entry["values_status"] = {"available": True, "source": "setup_export"}
        elif values is not None:
            entry["values_status"] = {"available": True, "source": "recording"}
        else:
            entry["values_status"] = {
                "available": False,
                "reason_code": reason_code or "sto_not_readable",
                "reason": reason or NO_VALUES_REASON,
            }
    return annotated


__all__ = [
    "AMBIGUOUS_REASON",
    "MODIFIED_REASON",
    "NO_VALUES_REASON",
    "UNVERIFIED_REASON",
    "garage_values",
    "is_setup_reading",
    "link_recorded_setups",
    "read_recording_setup_uses",
    "recorded_setup_identity",
]

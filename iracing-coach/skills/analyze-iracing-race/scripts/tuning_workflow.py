"""Durable setup-catalog, baseline-package, and tuning-experiment workflows."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

try:  # Package import and direct script execution are both supported.
    from . import backend_roots
    from .path_security import local_path
    from .setup_catalog import catalog_setups, compare_setups, normalize_embedded_setup
    from .storage import ArchiveStore, safe_slug, stable_hash, utc_now
    from .tuning_engine import (
        STRUCTURED_TUNING_RULESET_ID,
        StructuredTuningError,
        build_bounded_tuning_ai_request,
        build_structured_tuning_evidence,
        choose_oreilly_donor,
        select_representative_runs,
        select_structured_recommendation,
        stable_evidence_hash,
        recommend_tuning,
    )
except ImportError:  # pragma: no cover - normal CLI/MCP script-loading path.
    import backend_roots
    from path_security import local_path
    from setup_catalog import catalog_setups, compare_setups, normalize_embedded_setup
    from storage import ArchiveStore, safe_slug, stable_hash, utc_now
    from tuning_engine import (
        STRUCTURED_TUNING_RULESET_ID,
        StructuredTuningError,
        build_bounded_tuning_ai_request,
        build_structured_tuning_evidence,
        choose_oreilly_donor,
        select_representative_runs,
        select_structured_recommendation,
        stable_evidence_hash,
        recommend_tuning,
    )


SCRIPT_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = SCRIPT_DIR.parents[2]
DEFAULTS_PATH = PLUGIN_ROOT / "config" / "defaults.json"


class TuningWorkflowError(ValueError):
    """Actionable open-setup workflow error."""


def _defaults() -> dict[str, Any]:
    try:
        value = json.loads(DEFAULTS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return dict(value) if isinstance(value, Mapping) else {}


def _default_iracing_root() -> Path:
    # Shared authority; see `backend_roots` and IDENTITY-PATH-001.
    return backend_roots.iracing_root_path(defaults=_defaults())


def _setup_root(root: str | os.PathLike[str] | None) -> Path:
    candidate = local_path(root, "iRacing setup root") if root else _default_iracing_root()
    if candidate.name.casefold() != "setups":
        candidate = candidate / "setups"
    if not candidate.is_dir():
        raise TuningWorkflowError(f"iRacing setup directory does not exist: {candidate}")
    return candidate.resolve()


def _read_analysis(path: str | os.PathLike[str]) -> tuple[Path, dict[str, Any]]:
    resolved = Path(path).expanduser().resolve(strict=True)
    try:
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TuningWorkflowError(f"Analysis is unreadable: {exc}") from exc
    if not isinstance(value, dict) or not isinstance(value.get("identity"), Mapping):
        raise TuningWorkflowError("Analysis must be an archived analysis.json object.")
    return resolved, value


def _compact(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _canonical_car_query(value: Any) -> str:
    text = str(value or "").strip()
    lowered = text.casefold()
    if any(term in lowered for term in ("o'reilly", "oreilly", "xfinity", "supra")):
        return "stockcars2 supra2019"
    return text


_TRACK_ALIASES = {
    "newhampshire": ("newhampshire", "nhms"),
    "indianapolis": ("indianapolis", "indy", "ims"),
    "atlanta": ("atlanta", "atlantass", "echopark"),
    "coronado": ("coronado", "qualcomm", "sandiego"),
}


def _track_terms(value: Any) -> set[str]:
    compact = _compact(value)
    terms = {compact}
    for canonical, aliases in _TRACK_ALIASES.items():
        if any(alias in compact for alias in aliases):
            terms.update(aliases)
            terms.add(canonical)
    for noise in ("motorspeedway", "internationalspeedway", "speedway", "raceway", "circuit", "oval", "roadcourse", "course"):
        compact = compact.replace(noise, "")
    if compact:
        terms.add(compact)
    return {term for term in terms if term}


def _loose_match(query: Any, candidate: Any) -> bool:
    left, right = _compact(query), _compact(candidate)
    return bool(left and right and (left == right or left in right or right in left))


def _track_match(query: Any, candidate: Any) -> bool:
    return any(
        left == right or (len(left) >= 4 and left in right) or (len(right) >= 4 and right in left)
        for left in _track_terms(query)
        for right in _track_terms(candidate)
    )


def _entry_identity(entry: Mapping[str, Any]) -> Mapping[str, Any]:
    value = entry.get("filename_identity")
    return value if isinstance(value, Mapping) else {}


def _filter_entries(
    entries: Sequence[Mapping[str, Any]],
    *,
    car: str | None = None,
    track: str | None = None,
    season: str | None = None,
    role: str | None = None,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    car_query = _canonical_car_query(car) if car else None
    role_query = str(role or "").strip().casefold()
    role_alias = {"q": "qualifying", "r": "race", "e": "endurance"}.get(role_query, role_query)
    for raw in entries:
        entry = dict(raw)
        identity = _entry_identity(entry)
        if car_query and not _loose_match(car_query, entry.get("car_folder")):
            continue
        if track and not _track_match(track, identity.get("track_hint")):
            continue
        if season and _compact(identity.get("season_key")) != _compact(season):
            continue
        if role_query and str(identity.get("role") or "").casefold() != role_alias:
            continue
        result.append(entry)
    return result


def _source_fingerprint(entry: Mapping[str, Any]) -> str | None:
    sources = entry.get("sources") if isinstance(entry.get("sources"), Mapping) else {}
    for kind in ("html", "sto"):
        values = sources.get(kind) if isinstance(sources, Mapping) else None
        if isinstance(values, Sequence) and values and isinstance(values[0], Mapping):
            digest = values[0].get("sha256")
            if digest:
                return str(digest)
    return None


def _entry_notes(entry: Mapping[str, Any]) -> str:
    parsed = entry.get("parsed_html")
    return str(parsed.get("notes") or "") if isinstance(parsed, Mapping) else ""


def _entry_warnings(entry: Mapping[str, Any]) -> list[str]:
    result: list[str] = []
    parsed = entry.get("parsed_html")
    if isinstance(parsed, Mapping):
        mismatches = (
            parsed.get("identity", {}).get("mismatches", {})
            if isinstance(parsed.get("identity"), Mapping) else {}
        )
        for name, value in mismatches.items() if isinstance(mismatches, Mapping) else ():
            if value is True:
                result.append(str(name))
        result.extend(str(item) for item in parsed.get("warnings") or [])
    if entry.get("parse_error"):
        result.append("html_parse_error")
    return result


def catalog_iracing_setups_workflow(
    *,
    root: str | os.PathLike[str] | None = None,
    archive_root: str | os.PathLike[str] | None = None,
    car: str | None = None,
    track: str | None = None,
    season: str | None = None,
    role: str | None = None,
    maximum_entries: int = 200,
) -> dict[str, Any]:
    """Scan source setups read-only and archive a normalized local index."""

    maximum = max(1, min(int(maximum_entries), 1_000))
    setups = _setup_root(root)
    catalog = catalog_setups(setups, max_entries=1_000)
    catalog["cataloged_at"] = utc_now()
    store = ArchiveStore(archive_root)
    index_path = store.save_tuning_catalog(catalog)
    filtered = _filter_entries(
        catalog.get("entries") or [], car=car, track=track, season=season, role=role
    )
    return {
        "ok": True,
        "read_only_sources": True,
        "setup_root": str(setups),
        "archive_index": str(index_path),
        "counts": {
            "source_files": catalog.get("source_file_count"),
            "groups": catalog.get("group_count"),
            "catalog_entries": catalog.get("returned_entry_count"),
            "matching_entries": len(filtered),
            "parse_errors": sum(1 for item in catalog.get("entries") or [] if item.get("parse_error")),
        },
        "filters": {"car": car, "track": track, "season": season, "role": role},
        "entries": filtered[:maximum],
        "output_truncated": len(filtered) > maximum,
        "scan_truncated": bool(catalog.get("scan_truncated") or catalog.get("entries_truncated")),
        "errors": catalog.get("errors") or [],
    }


def _analysis_context(
    store: ArchiveStore, analysis: Mapping[str, Any], season: str | None = None
) -> dict[str, str]:
    context = store.context_from_analysis(analysis)
    context["setup_type"] = "open"
    if season:
        context["season_key"] = safe_slug(season)
    return context


def _identity_values(
    analysis: Mapping[str, Any] | None,
    car: str | None,
    track: str | None,
) -> tuple[str, str, dict[str, Any]]:
    identity = (
        dict(analysis.get("identity") or {})
        if isinstance(analysis, Mapping) and isinstance(analysis.get("identity"), Mapping)
        else {}
    )
    resolved_car = _canonical_car_query(
        car or identity.get("car_path") or identity.get("car_name")
    )
    resolved_track = str(
        track or identity.get("track_name") or identity.get("track_config") or ""
    ).strip()
    if not resolved_car or not resolved_track:
        raise TuningWorkflowError("Provide car and exact track, or an analysis_path containing both.")
    return resolved_car, resolved_track, identity


def _candidate_summary(entry: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if entry is None:
        return None
    return {
        "stem": entry.get("stem"),
        "pair_status": entry.get("pair_status"),
        "car_folder": entry.get("car_folder"),
        "filename_identity": dict(_entry_identity(entry)),
        "sources": entry.get("sources") or {},
        "fingerprint": _source_fingerprint(entry),
        "builder_notes": _entry_notes(entry),
        "identity_warnings": _entry_warnings(entry),
        "parsed_html": entry.get("parsed_html"),
        "source_files_read_only": True,
    }


def _artifact_car_directory_match(
    artifact: Mapping[str, Any] | None,
    identity: Mapping[str, Any],
    expected_car: str | None = None,
) -> dict[str, Any]:
    actual = str((artifact or {}).get("car_folder") or "").strip()
    identity_path = str(identity.get("car_path") or "").strip()
    if identity_path:
        expected = identity_path
        source = "identity.car_path"
    else:
        expected = _canonical_car_query(expected_car or identity.get("car_name"))
        source = "resolved-car-fallback"
    confirmed = bool(actual and expected and _compact(actual) == _compact(expected))
    return {
        "confirmed": confirmed,
        "expected_car_directory": expected or None,
        "artifact_car_directory": actual or None,
        "expected_source": source,
        "reason": (
            "The setup artifact is stored under the exact analyzed car directory."
            if confirmed
            else "The artifact directory does not exactly match the analyzed car path; it cannot supply builder-note provenance."
            if actual and expected
            else "Exact car-directory identity is unavailable; the artifact cannot supply builder-note provenance."
        ),
    }


def _preferred(entries: Sequence[Mapping[str, Any]], role: str) -> dict[str, Any] | None:
    candidates = _filter_entries(entries, role=role)
    if not candidates:
        return None
    candidates.sort(
        key=lambda item: (
            item.get("pair_status") == "paired",
            item.get("parsed_html") is not None,
            str(item.get("stem") or ""),
        ),
        reverse=True,
    )
    return dict(candidates[0])


def _baseline_confirmation(
    baseline: Mapping[str, Any] | None,
    identity: Mapping[str, Any],
    resolved_track: str,
    resolved_car: str | None = None,
) -> dict[str, Any]:
    """State how strongly a filename-selected baseline's track identity is known."""

    if not baseline:
        return {
            "status": "missing",
            "confirmed": False,
            "reason": "No race baseline artifact was selected.",
        }
    car_directory_match = _artifact_car_directory_match(
        baseline, identity, resolved_car
    )
    if not car_directory_match["confirmed"]:
        return {
            "status": "artifact-car-directory-mismatch",
            "confirmed": False,
            "reason": car_directory_match["reason"],
            "car_directory_match": car_directory_match,
        }
    warnings = {str(item) for item in baseline.get("identity_warnings") or []}
    if "track_header_mismatch" not in warnings:
        return {
            "status": "artifact-identities-agree",
            "confirmed": True,
            "reason": "The filename track hint and exported HTML track header do not conflict.",
            "car_directory_match": car_directory_match,
        }

    current_match = _current_analysis_baseline_match(baseline, identity, resolved_track)
    if current_match["confirmed"]:
        return {
            "status": "confirmed-by-ibt-session-and-setup-name",
            "confirmed": True,
            "reason": (
                "The HTML header conflicts, but the authoritative IBT session track and driven "
                "setup name identify this exact artifact."
            ),
            "analysis_track": current_match["analysis_track"],
            "analysis_setup_name": current_match["analysis_setup_name"],
            "car_directory_match": car_directory_match,
        }
    return {
        "status": "provisional-conflicting-export-header",
        "confirmed": False,
        "reason": (
            "The filename suggests this track but the exported HTML header conflicts; run and "
            "analyze this named setup at the target track before treating it as confirmed."
        ),
        "analysis_track": current_match["analysis_track"],
        "analysis_setup_name": current_match["analysis_setup_name"],
        "car_directory_match": car_directory_match,
    }


def _current_analysis_baseline_match(
    baseline: Mapping[str, Any] | None,
    identity: Mapping[str, Any],
    resolved_track: str,
) -> dict[str, Any]:
    """Require the current IBT to name the target track and baseline artifact."""

    setup_name = str(identity.get("setup_name") or "").strip()
    setup_stem = Path(setup_name).stem if setup_name else ""
    baseline_stem = str((baseline or {}).get("stem") or "").strip()
    analysis_track = identity.get("track_name") or identity.get("track_config")
    track_matches = bool(analysis_track and _track_match(resolved_track, analysis_track))
    car_directory_match = _artifact_car_directory_match(baseline, identity)
    setup_name_matches = bool(
        setup_stem and baseline_stem and _compact(setup_stem) == _compact(baseline_stem)
    )
    return {
        "status": (
            "current-ibt-matches-baseline"
            if track_matches and setup_name_matches and car_directory_match["confirmed"]
            else "current-ibt-does-not-match-baseline"
        ),
        "confirmed": bool(
            track_matches and setup_name_matches and car_directory_match["confirmed"]
        ),
        "track_matches": track_matches,
        "setup_name_matches": setup_name_matches,
        "car_directory_matches": car_directory_match["confirmed"],
        "car_directory_match": car_directory_match,
        "analysis_track": str(analysis_track or "") or None,
        "analysis_setup_name": setup_name or None,
    }


def build_open_setup_package_workflow(
    *,
    analysis_path: str | os.PathLike[str] | None = None,
    iracing_root: str | os.PathLike[str] | None = None,
    archive_root: str | os.PathLike[str] | None = None,
    season: str | None = None,
    car: str | None = None,
    track: str | None = None,
    track_characteristics: Mapping[str, Any] | str | None = None,
) -> dict[str, Any]:
    """Create a season-scoped new-week race/Q baseline package."""

    analysis: dict[str, Any] | None = None
    resolved_analysis: Path | None = None
    if analysis_path:
        resolved_analysis, analysis = _read_analysis(analysis_path)
    store = ArchiveStore(archive_root)
    resolved_car, resolved_track, identity = _identity_values(analysis, car, track)
    if analysis is not None:
        context = _analysis_context(store, analysis, season)
    else:
        if not season:
            raise TuningWorkflowError("A new-week package without analysis_path requires season, such as 2026S3.")
        context = {
            "season_key": safe_slug(season),
            "car_key": safe_slug(resolved_car, "car-unknown"),
            "track_key": safe_slug(resolved_track, "track-unknown"),
            "setup_type": "open",
            "race_length_key": "length-unknown",
        }

    setups = _setup_root(iracing_root)
    catalog = catalog_setups(setups, max_entries=1_000)
    catalog["cataloged_at"] = utc_now()
    catalog_path = store.save_tuning_catalog(catalog)
    exact = _filter_entries(
        catalog.get("entries") or [],
        car=resolved_car,
        track=resolved_track,
        season=season or context["season_key"],
    )
    race_entry = _preferred(exact, "race")
    qualifying_entry = _preferred(exact, "qualifying")
    donor = None
    donor_candidates: list[dict[str, Any]] = []
    if race_entry is None:
        donor = choose_oreilly_donor(track_characteristics or {})
        if donor.get("donor"):
            donor_candidates = _filter_entries(
                catalog.get("entries") or [],
                car=resolved_car,
                track=str(donor["donor"]),
                season=season or context["season_key"],
            )
            race_entry = _preferred(donor_candidates, "race")
            qualifying_entry = qualifying_entry or _preferred(donor_candidates, "qualifying")

    baseline = _candidate_summary(race_entry)
    qualifying = _candidate_summary(qualifying_entry)
    baseline_confirmation = _baseline_confirmation(
        baseline, identity, resolved_track, resolved_car
    )
    current_setup: dict[str, Any] | None = None
    if identity.get("setup"):
        current_setup = {
            "authority": "embedded-ibt-car-setup",
            "analysis_id": analysis.get("analysis_id") if analysis else None,
            "analysis_path": str(resolved_analysis) if resolved_analysis else None,
            "name": identity.get("setup_name"),
            "fingerprint": identity.get("setup_fingerprint"),
            "modified": identity.get("setup_modified"),
            "parameters": normalize_embedded_setup(identity["setup"]),
            "open_session": identity.get("is_fixed_setup") is False,
        }
    status = (
        "exact-track-baseline"
        if exact and baseline and baseline_confirmation["confirmed"]
        else "provisional-exact-track-baseline"
        if exact and baseline
        else (
        "donor-baseline" if baseline else "needs-baseline-export-or-research"
        )
    )
    package_id = "setup-" + stable_hash(
        {
            "season": context["season_key"],
            "car": context["car_key"],
            "track": context["track_key"],
            "baseline": (baseline or {}).get("fingerprint"),
            "donor": donor,
        },
        20,
    )
    package = {
        "schema_version": 1,
        "package_id": package_id,
        "created_at": utc_now(),
        "status": status,
        "context": context,
        "identity": {
            "car_path": identity.get("car_path") or resolved_car,
            "car_name": identity.get("car_name"),
            "track_name": identity.get("track_name") or resolved_track,
            "track_config": identity.get("track_config"),
        },
        "source_analysis": current_setup,
        "baseline": baseline,
        "baseline_confirmation": baseline_confirmation,
        "qualifying": qualifying,
        "exact_track_setup_available": bool(exact and baseline and baseline_confirmation["confirmed"]),
        "donor": donor,
        "track_characteristics": dict(track_characteristics) if isinstance(track_characteristics, Mapping) else track_characteristics,
        "catalog_path": str(catalog_path),
        "catalog_candidate_counts": {
            "exact": len(exact),
            "donor": len(donor_candidates),
        },
        "tuning_order": [
            "dynamic aero platform and tech legality",
            "mechanical grip",
            "entry, then center, then exit balance",
            "rear geometry",
            "tires, alignment, brakes, and damping finish work",
        ],
        "test_protocol": {
            "baseline_laps": 5,
            "race_stint_laps": "10-15",
            "one_logical_change_per_test": True,
            "match_conditions": ["fuel", "tire age", "weather", "track state", "line"],
            "always_repass_tech": True,
            "rollback_required": True,
        },
        "limitations": [
            "This package is a coaching plan and fingerprinted baseline record, not a simulator-loadable setup file.",
            "The source .sto files are opaque and remain read-only.",
            "An HTML/filename match is a candidate; the embedded IBT CarSetup is authoritative for what was driven.",
            "A donor transfers setup logic, not guaranteed body-package compatibility or tech legality.",
        ],
    }
    saved = store.save_tuning_package(package)
    return {
        "ok": status != "needs-baseline-export-or-research",
        "status": status,
        "package_id": package_id,
        "package_path": saved["path"],
        "context": context,
        "baseline": baseline,
        "baseline_confirmation": baseline_confirmation,
        "qualifying": qualifying,
        "donor": donor,
        "simulator_loadable_setup_produced": False,
        "source_setup_files_modified": False,
    }


_TRANSIENT_FIELD_TERMS = (
    "last_hot_pressure",
    "last_temps",
    "tread_remaining",
)


def _comparable_snapshot(value: Mapping[str, Any]) -> dict[str, Any]:
    fields = value.get("fields") if isinstance(value.get("fields"), Mapping) else {}
    return {
        "fields": {
            key: item for key, item in fields.items()
            if not any(term in str(key) for term in _TRANSIENT_FIELD_TERMS)
        }
    }


def _comparison_is_exact(comparison: Mapping[str, Any]) -> bool:
    summary = comparison.get("summary") if isinstance(comparison.get("summary"), Mapping) else {}
    return bool(
        (summary.get("common_fields") or 0) >= 20
        and summary.get("different_fields") == 0
        and summary.get("only_left_fields") == 0
        and summary.get("only_right_fields") == 0
    )


_NATIVE_CACHE_SCAN_LIMIT = 200
_NATIVE_CACHE_FILE_LIMIT = 12
_NATIVE_CACHE_MAX_BYTES = 8 * 1024 * 1024
_NATIVE_EVENT_SAMPLE_LIMIT = 40
_NATIVE_MEASUREMENT_KEYS = {
    "channel",
    "value",
    "absolute_value",
    "threshold",
    "unit",
    "direction",
    "vehicle_speed_mps",
    "brake",
    "on_pit_state",
    "lap_distance_bin",
    "peak_threshold_score",
}


def _analysis_source_sha256(analysis: Mapping[str, Any]) -> list[str]:
    source = analysis.get("source") if isinstance(analysis.get("source"), Mapping) else {}
    raw = source.get("fingerprints") if isinstance(source, Mapping) else None
    values: Sequence[Any]
    if isinstance(raw, Mapping):
        values = (raw,)
    elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
        values = raw
    else:
        values = ()
    result: list[str] = []
    for item in values:
        if not isinstance(item, Mapping):
            continue
        digest = str(item.get("sha256") or "").strip().casefold()
        if re.fullmatch(r"[0-9a-f]{64}", digest) and digest not in result:
            result.append(digest)
    return result[:8]


def _compact_native_event(event: Mapping[str, Any], source_sha256: str) -> dict[str, Any]:
    evidence = event.get("evidence") if isinstance(event.get("evidence"), Mapping) else {}
    measurements = event.get("measurements") if isinstance(event.get("measurements"), Mapping) else {}
    compact_measurements = {
        str(key): value for key, value in measurements.items()
        if str(key) in _NATIVE_MEASUREMENT_KEYS
    }
    per_wheel = measurements.get("per_wheel") if isinstance(measurements, Mapping) else None
    if isinstance(per_wheel, Mapping):
        compact_measurements["per_wheel"] = {
            str(wheel): {
                str(key): value for key, value in values.items()
                if str(key) in {
                    "delta", "threshold", "baseline_lap_count", "ratio_vs_vehicle_speed"
                }
            }
            for wheel, values in list(per_wheel.items())[:4]
            if isinstance(values, Mapping)
        }
    sub_tick = event.get("sub_tick") if isinstance(event.get("sub_tick"), Mapping) else None
    return {
        "event_type": event.get("event_type"),
        "source_sha256": source_sha256,
        "source_record_index": event.get("source_record_index"),
        "session_time_s": event.get("session_time_s"),
        "lap": event.get("lap"),
        "lap_distance_fraction": event.get("lap_distance_fraction"),
        "sub_tick": dict(sub_tick) if sub_tick else None,
        "evidence": {
            "label": evidence.get("label"),
            "measured_channels": list(evidence.get("measured_channels") or ())[:12],
            "method": evidence.get("method"),
            "limitation": evidence.get("limitation"),
        },
        "measurements": compact_measurements,
    }


def _cached_native_event_evidence(
    store: ArchiveStore,
    analysis: Mapping[str, Any],
) -> dict[str, Any]:
    """Reuse exact-source event-search caches without running telemetry detection."""

    source_sha256 = _analysis_source_sha256(analysis)
    limitations = [
        "Tuning reads exact-IBT native event caches only and never runs the detector implicitly.",
        "Derived threshold events and calibrated proxies locate trace regions; they do not prove a setup cause.",
    ]
    if not source_sha256:
        return {
            "status": "analysis-source-fingerprint-missing",
            "cache_only": True,
            "source_sha256": [],
            "cache_files_used": [],
            "queries": [],
            "event_count": 0,
            "counts_by_type": {},
            "event_samples": [],
            "event_samples_truncated": False,
            "limitations": limitations,
        }

    candidates: list[tuple[int, Path, str]] = []
    scanned = 0
    for digest in source_sha256:
        directory = store.root / "telemetry-events" / digest
        if not directory.is_dir():
            continue
        try:
            entries = directory.iterdir()
            for path in entries:
                if scanned >= _NATIVE_CACHE_SCAN_LIMIT:
                    break
                scanned += 1
                try:
                    if path.is_file() and path.suffix.casefold() == ".json":
                        candidates.append((path.stat().st_mtime_ns, path, digest))
                except OSError:
                    continue
        except OSError:
            continue
        if scanned >= _NATIVE_CACHE_SCAN_LIMIT:
            break
    candidates.sort(key=lambda item: item[0], reverse=True)

    cache_files: list[str] = []
    queries: list[dict[str, Any]] = []
    events_by_key: dict[tuple[Any, ...], dict[str, Any]] = {}
    ignored = 0
    for _, path, digest in candidates[:_NATIVE_CACHE_FILE_LIMIT]:
        try:
            if path.stat().st_size > _NATIVE_CACHE_MAX_BYTES:
                ignored += 1
                continue
            cached = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            ignored += 1
            continue
        result = cached.get("result") if isinstance(cached, Mapping) else None
        result_source = result.get("source") if isinstance(result, Mapping) and isinstance(result.get("source"), Mapping) else {}
        if not (
            cached.get("cache_schema") == 1
            and isinstance(result, Mapping)
            and cached.get("result_sha256") == stable_hash(result, 64)
            and str(result_source.get("sha256") or "").casefold() == digest
        ):
            ignored += 1
            continue
        cache_files.append(str(path.resolve()))
        query = result.get("query") if isinstance(result.get("query"), Mapping) else {}
        queries.append(
            {
                "source_sha256": digest,
                "event_types": list(query.get("event_types") or ())[:6],
                "start_record": query.get("start_record"),
                "end_record": query.get("end_record"),
                "context_filters": dict(query.get("context_filters") or {}),
            }
        )
        for raw in result.get("events") or ():
            if not isinstance(raw, Mapping):
                continue
            compact = _compact_native_event(raw, digest)
            measurement = compact.get("measurements") or {}
            sub_tick = compact.get("sub_tick") or {}
            key = (
                digest,
                compact.get("event_type"),
                compact.get("source_record_index"),
                measurement.get("channel"),
                sub_tick.get("index"),
            )
            events_by_key[key] = compact

    events = sorted(
        events_by_key.values(),
        key=lambda item: (
            str(item.get("source_sha256") or ""),
            int(item.get("source_record_index") or 0),
            str(item.get("event_type") or ""),
            str((item.get("measurements") or {}).get("channel") or ""),
        ),
    )
    counts: dict[str, int] = {}
    for event in events:
        event_type = str(event.get("event_type") or "unknown")
        counts[event_type] = counts.get(event_type, 0) + 1
    status = "available" if events else "searched-no-events" if cache_files else "not-searched"
    if len(events) > _NATIVE_EVENT_SAMPLE_LIMIT:
        limitations.append(
            f"Only {_NATIVE_EVENT_SAMPLE_LIMIT} exact-record event samples are embedded in the tuning plan."
        )
    if ignored:
        limitations.append(f"Ignored {ignored} unreadable, oversized, mismatched, or invalid event cache file(s).")
    return {
        "status": status,
        "cache_only": True,
        "source_sha256": source_sha256,
        "cache_files_used": cache_files,
        "cache_files_scanned": scanned,
        "queries": queries,
        "event_count": len(events),
        "counts_by_type": counts,
        "event_samples": events[:_NATIVE_EVENT_SAMPLE_LIMIT],
        "event_samples_truncated": len(events) > _NATIVE_EVENT_SAMPLE_LIMIT,
        "limitations": limitations,
    }


def _find_package_for_analysis(
    store: ArchiveStore, analysis: Mapping[str, Any]
) -> dict[str, Any] | None:
    identity = analysis.get("identity") if isinstance(analysis.get("identity"), Mapping) else {}
    analysis_context = _analysis_context(store, analysis)
    for row in store.list_tuning_packages(limit=200):
        if safe_slug(row.get("season_key")) != analysis_context["season_key"]:
            continue
        if str(row.get("setup_type") or "").casefold() != "open":
            continue
        try:
            package = store.load_tuning_package(str(row["package_id"]))
        except (KeyError, ValueError):
            continue
        package_identity = package.get("identity") if isinstance(package.get("identity"), Mapping) else {}
        if _loose_match(identity.get("car_path") or identity.get("car_name"), package_identity.get("car_path") or package_identity.get("car_name")) and _track_match(identity.get("track_name"), package_identity.get("track_name")):
            return package
    return None


def recommend_open_setup_tuning_workflow(
    *,
    analysis_path: str | os.PathLike[str],
    symptoms: Any,
    archive_root: str | os.PathLike[str] | None = None,
    package_id: str | None = None,
    maximum_changes: int = 3,
) -> dict[str, Any]:
    """Create and persist a one-hypothesis tuning experiment after an open race."""

    resolved_analysis, analysis = _read_analysis(analysis_path)
    identity = analysis["identity"]
    store = ArchiveStore(archive_root)
    package = store.load_tuning_package(package_id) if package_id else _find_package_for_analysis(store, analysis)
    analysis_context = _analysis_context(store, analysis)
    if package:
        package_identity = package.get("identity") if isinstance(package.get("identity"), Mapping) else {}
        package_context = package.get("context") if isinstance(package.get("context"), Mapping) else {}
        car_matches = _loose_match(
            identity.get("car_path") or identity.get("car_name"),
            package_identity.get("car_path") or package_identity.get("car_name"),
        )
        track_matches = _track_match(
            identity.get("track_name") or identity.get("track_config"),
            package_identity.get("track_name") or package_identity.get("track_config"),
        )
        season_matches = safe_slug(package_context.get("season_key")) == analysis_context["season_key"]
        if not (car_matches and track_matches and season_matches):
            raise TuningWorkflowError(
                "The selected setup package does not match the analyzed car, exact track, and season."
            )
    comparison: dict[str, Any] = {}
    builder_notes = ""
    builder_note_provenance: dict[str, Any] = {
        "available": False,
        "used": False,
        "reason": "No matching setup package was available.",
    }
    if package:
        baseline = package.get("baseline") if isinstance(package.get("baseline"), Mapping) else {}
        notes = str(baseline.get("builder_notes") or "")
        package_identity = package.get("identity") if isinstance(package.get("identity"), Mapping) else {}
        target_track = str(
            package_identity.get("track_name")
            or package.get("context", {}).get("track_key")
            or identity.get("track_name")
            or ""
        )
        live_confirmation = _current_analysis_baseline_match(baseline, identity, target_track)
        stored_confirmation = (
            dict(package.get("baseline_confirmation") or {})
            if isinstance(package.get("baseline_confirmation"), Mapping)
            else {}
        )
        if live_confirmation.get("confirmed") and not stored_confirmation.get("confirmed"):
            package = dict(package)
            package["baseline_confirmation"] = {
                **live_confirmation,
                "status": "confirmed-by-ibt-session-and-setup-name",
                "reason": "The current target-track IBT names this exact setup artifact.",
            }
            package["exact_track_setup_available"] = True
            if not package.get("donor"):
                package["status"] = "exact-track-baseline"
            package = store.save_tuning_package(package)["package"]
            stored_confirmation = package["baseline_confirmation"]
        artifact_car_matches = bool(live_confirmation.get("car_directory_matches"))
        parsed = baseline.get("parsed_html") if isinstance(baseline.get("parsed_html"), Mapping) else None
        if parsed and identity.get("setup") and artifact_car_matches:
            current = normalize_embedded_setup(identity["setup"])
            comparison = compare_setups(
                _comparable_snapshot(current),
                _comparable_snapshot(parsed),
                max_output=150,
            )
            summary = comparison.get("summary") or {}
            comparison["link_basis"] = (
                "exact_parameter_match"
                if _comparison_is_exact(comparison)
                else "canonical_parameter_comparison"
            )
            comparison["warning"] = "IBT and HTML semantic fingerprints are not assumed equal; compare canonical parameters."
        semantic_match = _comparison_is_exact(comparison)
        current_compatible = bool(
            artifact_car_matches
            and (
                live_confirmation.get("confirmed")
                or (live_confirmation.get("track_matches") and semantic_match)
            )
        )
        notes_allowed = bool(notes and current_compatible and not package.get("donor"))
        if notes_allowed:
            builder_notes = notes
        builder_note_provenance = {
            "available": bool(notes),
            "used": notes_allowed,
            "reason": (
                "The current IBT matches the exact-track baseline by setup name or exact normalized parameters."
                if notes_allowed
                else "Builder notes were suppressed because the artifact car directory does not exactly match identity.car_path."
                if notes and not artifact_car_matches
                else "Builder notes were suppressed because the current setup is different, the baseline is provisional, or the package is donor-derived."
                if notes
                else "The selected baseline has no builder notes."
            ),
            "stored_baseline_confirmation": stored_confirmation,
            "current_analysis_match": live_confirmation,
            "artifact_car_directory_match": live_confirmation.get("car_directory_match"),
            "semantic_parameter_match": semantic_match,
        }
    context = (
        dict(package["context"])
        if package and isinstance(package.get("context"), Mapping)
        else analysis_context
    )
    history = store.tuning_history(context, include_other_seasons=True, limit=100)
    native_event_evidence = _cached_native_event_evidence(store, analysis)
    recommendation = recommend_tuning(
        analysis,
        symptoms,
        builder_notes=builder_notes,
        setup_comparison=comparison,
        previous_experiments=history,
        maximum_changes=maximum_changes,
        native_event_evidence=native_event_evidence,
    )
    recommendation["builder_note_provenance"] = builder_note_provenance
    if identity.get("is_fixed_setup") is True:
        return {
            "ok": False,
            "status": "not-applicable-fixed-session",
            "persisted": False,
            "analysis_path": str(resolved_analysis),
            "recommendation": recommendation,
        }
    if recommendation.get("status") != "ready" or not recommendation.get("recommendations"):
        return {
            "ok": False,
            "status": recommendation.get("status"),
            "persisted": False,
            "analysis_path": str(resolved_analysis),
            "recommendation": recommendation,
        }
    experiment = {
        "analysis_id": analysis.get("analysis_id"),
        "analysis_path": str(resolved_analysis),
        "package_id": package.get("package_id") if package else None,
        "context": context,
        "source_identity": {
            "car_id": identity.get("car_id"),
            "car_path": identity.get("car_path"),
            "track_id": identity.get("track_id"),
            "track_name": identity.get("track_name"),
            "track_config": identity.get("track_config"),
        },
        "setup": {
            "name": identity.get("setup_name"),
            "fingerprint": identity.get("setup_fingerprint"),
            "modified": identity.get("setup_modified"),
        },
        "symptoms": recommendation.get("symptoms") or [],
        "builder_note_provenance": builder_note_provenance,
        "recommendation": recommendation,
        "primary_recommendation": recommendation["recommendations"][0],
        "status": "planned",
    }
    saved = store.record_tuning_experiment(experiment)
    return {
        "ok": True,
        "status": "planned",
        "persisted": True,
        "experiment_id": saved["experiment"]["experiment_id"],
        "experiment_path": saved["path"],
        "package_id": experiment.get("package_id"),
        "primary_recommendation": experiment["primary_recommendation"],
        "recommendation": recommendation,
    }


def recommend_structured_open_setup_tuning_workflow(
    *,
    analysis_path: str | os.PathLike[str],
    feedback: Sequence[Mapping[str, Any]],
    map_identity: Mapping[str, Any],
    archive_root: str | os.PathLike[str] | None = None,
    open_target_analysis_path: str | os.PathLike[str] | None = None,
    representative_run_ids: Sequence[Any] = (),
    generic_note: str = "",
    goal: str = "long-run-pace",
    ruleset_id: str = STRUCTURED_TUNING_RULESET_ID,
    package_id: str | None = None,
    draft_id: str | None = None,
    ai_response: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build and idempotently persist the evidence-bounded v2 tuning plan.

    A fixed race may be the driving-evidence source, but it can never be the
    garage target.  In that case ``open_target_analysis_path`` must identify a
    distinct, exact-car/exact-configuration open analysis with an authoritative
    embedded setup.  Source setup files remain read-only.
    """

    resolved_driving, driving_analysis = _read_analysis(analysis_path)
    if open_target_analysis_path:
        resolved_target, target_analysis = _read_analysis(open_target_analysis_path)
    else:
        resolved_target, target_analysis = resolved_driving, driving_analysis
    store = ArchiveStore(archive_root)
    if package_id:
        # Existence is verified for referential integrity. The v2 rules engine
        # never substitutes package or HTML values for the embedded target setup.
        store.load_tuning_package(package_id)
    context = _analysis_context(store, target_analysis)
    history = store.tuning_history(
        context,
        include_other_seasons=False,
        include_other_tracks=False,
        limit=200,
    )
    try:
        evidence = build_structured_tuning_evidence(
            driving_analysis,
            feedback,
            map_identity,
            open_target_analysis=target_analysis,
            representative_run_ids=representative_run_ids,
            previous_experiments=history,
            generic_note=generic_note,
            goal=goal,
            ruleset_id=ruleset_id,
        )
        recommendation = select_structured_recommendation(evidence, ai_response)
    except StructuredTuningError as exc:
        raise TuningWorkflowError(str(exc)) from exc
    ai_request = None
    ai_request_error = None
    if evidence.get("candidate_whitelist"):
        try:
            ai_request = build_bounded_tuning_ai_request(evidence)
        except StructuredTuningError as exc:
            ai_request_error = str(exc)

    draft_key = {
        "driving_analysis_id": driving_analysis.get("analysis_id"),
        "open_target_analysis_id": target_analysis.get("analysis_id"),
        "map_identity": evidence.get("map_ref", {}).get("map_identity"),
        "ruleset_id": ruleset_id,
    }
    supplied_draft_id = str(draft_id or "").strip()
    if supplied_draft_id and (
        len(supplied_draft_id) > 128
        or any(not (character.isalnum() or character in "._-") for character in supplied_draft_id)
    ):
        raise TuningWorkflowError(
            "draft_id must be 1-128 letters, numbers, dots, dashes, or underscores."
        )
    resolved_draft_id = supplied_draft_id or (
        "tuning-draft-" + stable_evidence_hash(draft_key, 20)
    )
    saved_draft = store.save_tuning_draft(
        {
            "schema_version": 2,
            "draft_id": resolved_draft_id,
            "driving_analysis_path": str(resolved_driving),
            "open_target_analysis_path": str(resolved_target),
            "representative_run_ids": [str(item) for item in representative_run_ids],
            "map_identity": dict(map_identity),
            "ruleset_id": ruleset_id,
            "generic_note": str(generic_note).strip(),
            "goal": str(goal or "long-run-pace"),
            "feedback": [dict(item) for item in feedback],
            "latest_evidence_hash": evidence["evidence_hash"],
            "latest_status": recommendation["status"],
        }
    )

    persisted = False
    experiment_id = None
    experiment_path = None
    selected_candidate = next(
        (
            item
            for item in evidence.get("candidate_whitelist") or ()
            if item.get("candidate_id") == recommendation.get("selected_candidate_id")
        ),
        None,
    )
    if recommendation.get("status") == "ready" and selected_candidate:
        # The evidence hash excludes AI synthesis, so deterministic first call
        # and validated-AI second call UPSERT the same experiment rather than
        # producing duplicate history rows.
        experiment_id = "tune-v2-" + str(evidence["evidence_hash"])[0:24]
        target_identity = target_analysis.get("identity") if isinstance(target_analysis.get("identity"), Mapping) else {}
        driving_identity = driving_analysis.get("identity") if isinstance(driving_analysis.get("identity"), Mapping) else {}
        experiment = {
            "schema_version": 2,
            "experiment_id": experiment_id,
            "analysis_id": driving_analysis.get("analysis_id"),
            "analysis_path": str(resolved_driving),
            "open_target_analysis_id": target_analysis.get("analysis_id"),
            "open_target_analysis_path": str(resolved_target),
            "package_id": package_id,
            "context": context,
            "source_identity": {
                "car_id": target_identity.get("car_id"),
                "car_path": target_identity.get("car_path"),
                "track_id": target_identity.get("track_id"),
                "track_name": target_identity.get("track_name"),
                "track_config": target_identity.get("track_config"),
                "track_configuration_key": evidence.get("open_target_ref", {}).get("track_configuration_key"),
            },
            "driving_evidence_identity": {
                "car_id": driving_identity.get("car_id"),
                "car_path": driving_identity.get("car_path"),
                "track_id": driving_identity.get("track_id"),
                "track_name": driving_identity.get("track_name"),
                "track_config": driving_identity.get("track_config"),
                "is_fixed_setup": driving_identity.get("is_fixed_setup"),
            },
            "setup": {
                "name": target_identity.get("setup_name"),
                "fingerprint": target_identity.get("setup_fingerprint"),
                "modified": target_identity.get("setup_modified"),
                "manual_sto_boundary": True,
            },
            "symptoms": evidence.get("feedback") or [],
            "structured_evidence": evidence,
            "recommendation": {
                "contract": "structured_tuning_recommendation_v2",
                "evidence_hash": evidence["evidence_hash"],
                "selection": recommendation,
                "selected_candidate": selected_candidate,
            },
            "primary_recommendation": selected_candidate,
            "status": "planned",
        }
        saved = store.record_tuning_experiment(experiment)
        persisted = True
        experiment_path = saved["path"]

    return {
        "ok": recommendation.get("status") == "ready",
        "status": recommendation.get("status"),
        "contract": "tuning_evidence_v2",
        "draft_id": resolved_draft_id,
        "draft_path": saved_draft["path"],
        "persisted": persisted,
        "experiment_id": experiment_id,
        "experiment_path": experiment_path,
        "evidence_hash": evidence["evidence_hash"],
        "eligibility": evidence["eligibility"],
        "evidence": evidence.get("observations") or [],
        "candidate_whitelist": evidence.get("candidate_whitelist") or [],
        "suppressed_candidates": evidence.get("suppressed_candidates") or [],
        "recommendation": recommendation,
        "limitations": evidence.get("limitations") or [],
        "missing_required": evidence.get("missing_required") or [],
        "history": history,
        "ai_request": ai_request,
        "ai_request_error": ai_request_error,
        "tuning_evidence_v2": evidence,
    }


_OUTCOMES = {"improved", "worse", "no-change", "inconclusive"}


def _temperature_delta(left: Any, right: Any) -> float | None:
    try:
        if left is None or right is None:
            return None
        return abs(float(left) - float(right))
    except (TypeError, ValueError):
        return None


def _exact_track_configuration_key(analysis: Mapping[str, Any]) -> str:
    geometry = analysis.get("track_geometry")
    if isinstance(geometry, Mapping) and geometry.get("track_configuration_key"):
        return str(geometry["track_configuration_key"])
    identity = analysis.get("identity") if isinstance(analysis.get("identity"), Mapping) else {}
    return safe_slug(
        f"{identity.get('track_id') or 'track'}-{identity.get('track_config') or identity.get('track_name')}"
    )


def _selected_run_metrics(
    analysis: Mapping[str, Any], selected: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    if not selected:
        return {}
    run_number = selected[0].get("run_number")
    run = next(
        (
            item
            for item in analysis.get("runs") or ()
            if isinstance(item, Mapping) and item.get("run_number") == run_number
        ),
        {},
    )
    pace = run.get("pace") if isinstance(run.get("pace"), Mapping) else {}
    fuel = run.get("fuel") if isinstance(run.get("fuel"), Mapping) else {}
    return {
        "run_id": selected[0].get("run_id"),
        "run_number": run_number,
        "eligible_lap_count": selected[0].get("eligible_lap_count"),
        "early_average_lap_s": _number_or_none(pace.get("early_average_lap_s")),
        "late_average_lap_s": _number_or_none(pace.get("late_average_lap_s")),
        "early_to_late_delta_s": _number_or_none(pace.get("early_to_late_delta_s")),
        "green_lap_time_slope_s_per_lap": _number_or_none(pace.get("green_lap_time_slope_s_per_lap")),
        "start_fuel_l": _number_or_none(fuel.get("start_l")),
    }


def _number_or_none(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _structured_result_comparison(
    experiment: Mapping[str, Any], result_analysis: Mapping[str, Any]
) -> dict[str, Any]:
    """Compare known A/B fields without converting association into causality."""

    result_identity = result_analysis.get("identity") if isinstance(result_analysis.get("identity"), Mapping) else {}
    source_identity = experiment.get("source_identity") if isinstance(experiment.get("source_identity"), Mapping) else {}
    exact_car = _compact(source_identity.get("car_path")) == _compact(result_identity.get("car_path"))
    exact_track = str(source_identity.get("track_configuration_key") or "") == _exact_track_configuration_key(result_analysis)
    open_setup = result_identity.get("is_fixed_setup") is False
    baseline_setup = experiment.get("setup") if isinstance(experiment.get("setup"), Mapping) else {}
    baseline_fingerprint = str(baseline_setup.get("fingerprint") or "")
    result_fingerprint = str(result_identity.get("setup_fingerprint") or "")
    setup_changed = bool(
        baseline_fingerprint and result_fingerprint and baseline_fingerprint != result_fingerprint
    )
    result_runs, rejected_result_runs, result_run_limits = select_representative_runs(result_analysis)

    baseline_analysis: dict[str, Any] = {}
    baseline_path = experiment.get("open_target_analysis_path") or experiment.get("analysis_path")
    if baseline_path and Path(str(baseline_path)).is_file():
        try:
            _, baseline_analysis = _read_analysis(str(baseline_path))
        except TuningWorkflowError:
            baseline_analysis = {}
    baseline_runs, _, baseline_run_limits = select_representative_runs(baseline_analysis) if baseline_analysis else ([], [], ["Baseline analysis is unavailable."])
    baseline_metrics = _selected_run_metrics(baseline_analysis, baseline_runs)
    result_metrics = _selected_run_metrics(result_analysis, result_runs)
    metric_deltas: dict[str, float] = {}
    for field in (
        "early_average_lap_s",
        "late_average_lap_s",
        "early_to_late_delta_s",
        "green_lap_time_slope_s_per_lap",
        "start_fuel_l",
    ):
        before = _number_or_none(baseline_metrics.get(field))
        after = _number_or_none(result_metrics.get(field))
        if before is not None and after is not None:
            metric_deltas[field] = round(after - before, 6)

    baseline_identity = baseline_analysis.get("identity") if isinstance(baseline_analysis.get("identity"), Mapping) else {}
    baseline_conditions = baseline_identity.get("conditions") if isinstance(baseline_identity.get("conditions"), Mapping) else {}
    result_conditions = result_identity.get("conditions") if isinstance(result_identity.get("conditions"), Mapping) else {}
    track_temp_delta = _temperature_delta(
        baseline_conditions.get("track_temp_c"), result_conditions.get("track_temp_c")
    )
    air_temp_delta = _temperature_delta(
        baseline_conditions.get("air_temp_c"), result_conditions.get("air_temp_c")
    )
    limitations: list[str] = []
    if not exact_car:
        limitations.append("Result car_path does not exactly match the open target.")
    if not exact_track:
        limitations.append("Result track configuration key does not exactly match the open target.")
    if not open_setup:
        limitations.append("Result is not confirmed as an open-setup session.")
    if not setup_changed:
        limitations.append("A different setup fingerprint was not recorded.")
    if not result_runs:
        limitations.extend(result_run_limits or ["No strict clean result run is available."])
    if not baseline_runs:
        limitations.extend(baseline_run_limits or ["No strict clean baseline run is available."])
    if track_temp_delta is None:
        limitations.append("Track-temperature comparability is unknown.")
    elif track_temp_delta > 5.0:
        limitations.append("Track temperature differs by more than 5 C.")
    if air_temp_delta is None:
        limitations.append("Air-temperature comparability is unknown.")
    elif air_temp_delta > 3.0:
        limitations.append("Air temperature differs by more than 3 C.")
    baseline_compound = baseline_identity.get("tire_compound")
    result_compound = result_identity.get("tire_compound")
    if baseline_compound is None or result_compound is None:
        limitations.append("Tire-compound comparability is unknown.")
    elif baseline_compound != result_compound:
        limitations.append("Tire compounds differ.")
    return {
        "contract": "tuning_result_comparison_v2",
        "status": "controlled-comparison-candidate" if not limitations else "partial",
        "same_exact_car_path": exact_car,
        "same_exact_track_configuration": exact_track,
        "open_setup": open_setup,
        "setup_fingerprint_changed": setup_changed,
        "baseline_setup_fingerprint": baseline_fingerprint or None,
        "result_setup_fingerprint": result_fingerprint or None,
        "baseline_representative_run": baseline_metrics or None,
        "result_representative_run": result_metrics or None,
        "measured_deltas_result_minus_baseline": metric_deltas,
        "track_temp_absolute_delta_c": track_temp_delta,
        "air_temp_absolute_delta_c": air_temp_delta,
        "limitations": list(dict.fromkeys(limitations)),
        "causality": "Measured differences are an A/B association and do not prove the setup change caused them.",
    }


def record_open_setup_feedback_workflow(
    *,
    experiment_id: str,
    outcome: str,
    notes: str,
    archive_root: str | os.PathLike[str] | None = None,
    result_analysis_path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Attach observed driver/result evidence to a prior tuning experiment."""

    normalized_outcome = str(outcome).strip().casefold().replace("_", "-")
    if normalized_outcome not in _OUTCOMES:
        raise TuningWorkflowError(
            "outcome must be improved, worse, no-change, or inconclusive."
        )
    if len(str(notes)) > 8_000:
        raise TuningWorkflowError("notes must be 8,000 characters or fewer.")
    store = ArchiveStore(archive_root)
    experiment = store.load_tuning_experiment(experiment_id)
    result_analysis: dict[str, Any] | None = None
    resolved_result: Path | None = None
    comparison = {
        "status": "driver-feedback-only",
        "same_car": None,
        "same_track": None,
        "open_setup": None,
        "setup_fingerprint_changed": None,
        "limitations": ["No result analysis was supplied for telemetry comparability."],
    }
    if int(experiment.get("schema_version") or 1) >= 2:
        comparison = {
            "contract": "tuning_result_comparison_v2",
            "status": "driver-feedback-only",
            "measured_deltas_result_minus_baseline": {},
            "limitations": [
                "No result analysis was supplied, so only the driver's stated outcome is recorded."
            ],
            "causality": "Driver feedback alone does not prove the setup change caused the reported outcome.",
        }
    if result_analysis_path:
        resolved_result, result_analysis = _read_analysis(result_analysis_path)
        if int(experiment.get("schema_version") or 1) >= 2:
            comparison = _structured_result_comparison(experiment, result_analysis)
            if not comparison["same_exact_car_path"]:
                raise TuningWorkflowError(
                    "Result analysis must use the exact open-target car_path."
                )
            if not comparison["same_exact_track_configuration"]:
                raise TuningWorkflowError(
                    "Result analysis must use the exact open-target track configuration."
                )
            if not comparison["open_setup"]:
                raise TuningWorkflowError("Result analysis must be an open-setup session.")
            feedback = {
                "recorded_at": utc_now(),
                "driver_outcome": normalized_outcome,
                "notes": str(notes).strip(),
                "result_analysis_id": result_analysis.get("analysis_id"),
                "result_analysis_path": str(resolved_result),
                "comparison": comparison,
            }
            saved = store.record_tuning_feedback(
                experiment_id,
                feedback,
                outcome=normalized_outcome,
            )
            return {
                "ok": True,
                "status": "recorded",
                "experiment_id": experiment_id,
                "experiment_path": saved["path"],
                "outcome": normalized_outcome,
                "comparison": comparison,
            }
        identity = result_analysis["identity"]
        source_identity = experiment.get("source_identity") if isinstance(experiment.get("source_identity"), Mapping) else {}
        same_car = _loose_match(
            source_identity.get("car_path") or source_identity.get("car_id"),
            identity.get("car_path") or identity.get("car_id"),
        )
        same_track = _track_match(
            source_identity.get("track_name") or source_identity.get("track_id"),
            identity.get("track_name") or identity.get("track_id"),
        )
        open_setup = identity.get("is_fixed_setup") is False
        if not same_car or not same_track:
            raise TuningWorkflowError("Result analysis must use the same car and exact track as the experiment.")
        if not open_setup:
            raise TuningWorkflowError("Result analysis must be an open-setup session.")
        old_setup = experiment.get("setup") if isinstance(experiment.get("setup"), Mapping) else {}
        changed = bool(
            old_setup.get("fingerprint")
            and identity.get("setup_fingerprint")
            and old_setup.get("fingerprint") != identity.get("setup_fingerprint")
        )
        baseline_analysis: dict[str, Any] = {}
        baseline_path = experiment.get("analysis_path")
        if baseline_path and Path(str(baseline_path)).is_file():
            try:
                _, baseline_analysis = _read_analysis(str(baseline_path))
            except TuningWorkflowError:
                baseline_analysis = {}
        prior_conditions = (
            baseline_analysis.get("identity", {}).get("conditions", {})
            if isinstance(baseline_analysis.get("identity"), Mapping) else {}
        )
        result_conditions = identity.get("conditions") if isinstance(identity.get("conditions"), Mapping) else {}
        track_temp_delta = _temperature_delta(
            prior_conditions.get("track_temp_c"), result_conditions.get("track_temp_c")
        )
        air_temp_delta = _temperature_delta(
            prior_conditions.get("air_temp_c"), result_conditions.get("air_temp_c")
        )
        limitations = [
            "Fuel state, tire age, track state, line, and traffic must still be confirmed by the driver."
        ]
        if not changed:
            limitations.append("No setup fingerprint change was detected.")
        if track_temp_delta is not None and track_temp_delta > 5.0:
            limitations.append("Track temperature differed by more than 5 C.")
        comparison = {
            "status": "comparable-candidate" if changed and len(limitations) == 1 else "partial",
            "same_car": same_car,
            "same_track": same_track,
            "open_setup": open_setup,
            "setup_fingerprint_changed": changed,
            "baseline_setup_fingerprint": old_setup.get("fingerprint"),
            "result_setup_fingerprint": identity.get("setup_fingerprint"),
            "track_temp_delta_c": track_temp_delta,
            "air_temp_delta_c": air_temp_delta,
            "limitations": limitations,
        }
    feedback = {
        "recorded_at": utc_now(),
        "driver_outcome": normalized_outcome,
        "notes": str(notes).strip(),
        "result_analysis_id": result_analysis.get("analysis_id") if result_analysis else None,
        "result_analysis_path": str(resolved_result) if resolved_result else None,
        "comparison": comparison,
    }
    saved = store.record_tuning_feedback(
        experiment_id,
        feedback,
        outcome=normalized_outcome,
    )
    return {
        "ok": True,
        "status": "recorded",
        "experiment_id": experiment_id,
        "experiment_path": saved["path"],
        "outcome": normalized_outcome,
        "comparison": comparison,
    }


def iracing_setup_history_workflow(
    *,
    archive_root: str | os.PathLike[str] | None = None,
    analysis_path: str | os.PathLike[str] | None = None,
    package_id: str | None = None,
    context: Mapping[str, Any] | None = None,
    include_other_seasons: bool = False,
    include_other_tracks: bool = False,
    limit: int = 100,
) -> dict[str, Any]:
    store = ArchiveStore(archive_root)
    resolved_context: dict[str, Any]
    if context is not None:
        resolved_context = dict(context)
    elif package_id:
        package = store.load_tuning_package(package_id)
        resolved_context = dict(package["context"])
    elif analysis_path:
        _, analysis = _read_analysis(analysis_path)
        resolved_context = _analysis_context(store, analysis)
    else:
        raise TuningWorkflowError("Provide analysis_path, package_id, or context.")
    history = store.tuning_history(
        resolved_context,
        limit=max(1, min(int(limit), 1_000)),
        include_other_seasons=include_other_seasons,
        include_other_tracks=include_other_tracks,
    )
    return {
        "ok": True,
        "context": resolved_context,
        "experiment_count": len(history),
        "experiments": history,
    }


__all__ = [
    "TuningWorkflowError",
    "build_open_setup_package_workflow",
    "catalog_iracing_setups_workflow",
    "iracing_setup_history_workflow",
    "recommend_open_setup_tuning_workflow",
    "recommend_structured_open_setup_tuning_workflow",
    "record_open_setup_feedback_workflow",
]

"""Verbatim pre-optimisation reference for ``race_foundations`` (generated; do not edit).

These are the race_foundations.py function bodies exactly as they were at commit
9077917, before the column-at-a-time rewrite. The equivalence tests run them
beside the current functions on the same synthetic telemetry and require
identical output. Every other name they use is imported from the current
module; the generator verified each of those is unchanged (identical AST).

Regenerate only from the pre-change source (``git show 9077917:<path>``).
"""
# ruff: noqa
# fmt: off
from __future__ import annotations

import hashlib
import json
import math
import re
import statistics
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, MutableMapping, Sequence

import sys as _sys

_SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "analyze-iracing-race" / "scripts"
if str(_SCRIPTS) not in _sys.path:
    _sys.path.insert(0, str(_SCRIPTS))

from race_foundations import (  # noqa: E402
    GPS_CLUSTER_RADIUS_DEGREES,
    GPS_PLACEHOLDER_EPSILON,
    _average_paths,
    _cross_line,
    _finite,
    _main_loop_quality,
    _normalize_geometry,
    _sanitize_geometry_layers,
    track_configuration_key,
    track_geometry_sha256,
)


def _median(values: Iterable[Any]) -> float | None:
    numbers = [number for value in values if (number := _finite(value)) is not None]
    return statistics.median(numbers) if numbers else None

def _percentile(values: Iterable[Any], fraction: float) -> float | None:
    numbers = sorted(number for value in values if (number := _finite(value)) is not None)
    if not numbers:
        return None
    if len(numbers) == 1:
        return numbers[0]
    position = max(0.0, min(1.0, fraction)) * (len(numbers) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return numbers[lower]
    weight = position - lower
    return numbers[lower] * (1.0 - weight) + numbers[upper] * weight

def build_track_geometry(
    channels: Mapping[str, Sequence[Any]],
    metadata: Mapping[str, Any],
    identity: Mapping[str, Any],
    source_fingerprints: Sequence[Mapping[str, Any]] = (),
    *,
    main_bins: int = 500,
) -> dict[str, Any]:
    required = {
        "LapDistPct": "LapDistPct was not recorded.",
        "Lat": "Latitude was not recorded.",
        "Lon": "Longitude was not recorded.",
    }
    missing = [reason for channel, reason in required.items() if channel not in channels]
    source_sha256 = sorted(
        {
            str(item.get("sha256")).lower()
            for item in source_fingerprints
            if len(str(item.get("sha256") or "")) == 64
        }
    )
    result: dict[str, Any] = {
        "schema_version": 1,
        "track_configuration_key": track_configuration_key(identity),
        "track_id": identity.get("track_id"),
        "track_name": identity.get("track_name"),
        "track_config": identity.get("track_config"),
        # source_sha256 remains for contract-v1 readers.  The explicit fields
        # below distinguish the bytes that actually produced this geometry
        # from other exact-configuration sources a cache may later observe.
        "source_sha256": source_sha256,
        "contributing_source_sha256": source_sha256,
        "observed_source_sha256": source_sha256,
        "coordinate_system": "normalized_local_vector",
        "geometry_hash": None,
        "status": "unavailable" if missing else "usable",
        "unavailable_reasons": missing,
        "main_path": [],
        "pit_lane": [],
        "pit_entry_path": [],
        "pit_exit_path": [],
        "start_finish_line": None,
        "pit_commitment_line": None,
        "pit_merge_line": None,
        "quality": {
            "main_loop_complete": False,
            "lap_percent_coverage": 0.0,
            "maximum_lap_percent_gap": 1.0,
            "closure_distance": 1.0,
            "geometry_plausible": False,
            "main_path_span": 0.0,
            "maximum_segment_distance": None,
            "maximum_relative_segment_distance": None,
            "main_path_points": 0,
            "observed_main_path_points": 0,
            "pit_lane_points": 0,
            "pit_entry_observations": 0,
            "pit_exit_observations": 0,
            "main_source_samples": 0,
            "pit_source_samples": 0,
        },
    }
    if missing:
        return result

    pct = channels["LapDistPct"]
    lat = channels["Lat"]
    lon = channels["Lon"]
    pit_recorded = "OnPitRoad" in channels
    pit = channels.get("OnPitRoad") or [False] * min(len(pct), len(lat), len(lon))
    count = min(len(pct), len(lat), len(lon), len(pit))
    bins: list[list[dict[str, Any]]] = [[] for _ in range(main_bins)]
    valid: list[dict[str, Any] | None] = [None] * count
    candidates: list[tuple[int, float, float, float]] = []
    rejected_gps_samples = 0
    for index in range(count):
        lap_pct, y, x = _finite(pct[index]), _finite(lat[index]), _finite(lon[index])
        if (
            lap_pct is None
            or x is None
            or y is None
            or lap_pct < -0.01
            or lap_pct > 1.01
            or abs(y) > 90
            or abs(x) > 180
            or (abs(x) <= GPS_PLACEHOLDER_EPSILON and abs(y) <= GPS_PLACEHOLDER_EPSILON)
        ):
            if x is not None and y is not None:
                rejected_gps_samples += 1
            continue
        candidates.append((index, lap_pct, y, x))

    # iRacing can emit isolated finite GPS sentinels during session-state or
    # pit transitions.  Keep the dominant local cluster and leave rejected
    # samples as gaps; never bridge an entire continent into track geometry.
    center_x = _median(item[3] for item in candidates)
    center_y = _median(item[2] for item in candidates)
    distances = (
        [math.hypot(item[3] - center_x, item[2] - center_y) for item in candidates]
        if center_x is not None and center_y is not None
        else []
    )
    median_distance = _median(distances) or 0.0
    distance_mad = _median(abs(distance - median_distance) for distance in distances) or 0.0
    cluster_radius = max(
        GPS_CLUSTER_RADIUS_DEGREES,
        median_distance + max(0.0005, distance_mad) * 12.0,
    )
    for index, lap_pct, y, x in candidates:
        if center_x is None or center_y is None or math.hypot(x - center_x, y - center_y) > cluster_radius:
            rejected_gps_samples += 1
            continue
        point = {"x": x, "y": y, "lap_pct": lap_pct % 1.0, "sample_index": index}
        valid[index] = point
        if not bool(pit[index]):
            bins[min(main_bins - 1, max(0, int((lap_pct % 1.0) * main_bins)))].append(point)

    main_raw = [
        {
            "sequence": sequence,
            "lap_pct": round((bin_index + 0.5) / main_bins, 6),
            "x": round(float(_median(point["x"] for point in points) or 0.0), 8),
            "y": round(float(_median(point["y"] for point in points) or 0.0), 8),
            "observations": len(points),
        }
        for sequence, (bin_index, points) in enumerate(
            (item for item in enumerate(bins) if item[1])
        )
    ]

    sample_rate = _finite(metadata.get("sample_rate") or metadata.get("tick_rate")) or 20.0
    window = max(3, int(sample_rate * 4.0))
    pit_episodes: list[list[dict[str, Any]]] = []
    entry_paths: list[list[dict[str, Any]]] = []
    exit_paths: list[list[dict[str, Any]]] = []
    entry_points: list[dict[str, Any]] = []
    exit_points: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    for index in range(count):
        is_pit = bool(pit[index])
        prior_pit = bool(pit[index - 1]) if index > 0 else False
        point = valid[index]
        if is_pit and point is not None:
            current.append(point)
        if is_pit and not prior_pit:
            segment = [item for item in valid[max(0, index - window): min(count, index + max(2, window // 4))] if item]
            if len(segment) >= 2:
                entry_paths.append(segment)
            if point:
                entry_points.append(point)
        if not is_pit and prior_pit:
            if len(current) >= 2:
                pit_episodes.append(current)
            current = []
            segment = [item for item in valid[max(0, index - max(2, window // 4)): min(count, index + window)] if item]
            if len(segment) >= 2:
                exit_paths.append(segment)
            if point:
                exit_points.append(point)
    if len(current) >= 2:
        pit_episodes.append(current)

    raw_paths = {
        "main_path": main_raw,
        "pit_lane": _average_paths(pit_episodes, 180),
        "pit_entry_path": _average_paths(entry_paths, 70),
        "pit_exit_path": _average_paths(exit_paths, 70),
    }
    paths, transform = _normalize_geometry(raw_paths)
    sanitized_paths, rejected_layers = _sanitize_geometry_layers(paths)
    paths = {name: list(sanitized_paths.get(name) or ()) for name in raw_paths}
    result.update(paths)
    result["transform"] = transform
    loop_quality = _main_loop_quality(paths["main_path"])
    main_loop_complete = bool(loop_quality["main_loop_complete"])
    if main_loop_complete:
        result["start_finish_line"] = _cross_line(paths["main_path"], 0)
    if entry_points and paths["pit_lane"]:
        result["pit_commitment_line"] = _cross_line(paths["pit_lane"], 0)
    if exit_points and paths["pit_lane"]:
        result["pit_merge_line"] = _cross_line(paths["pit_lane"], len(paths["pit_lane"]) - 1)
    result["quality"] = {
        **loop_quality,
        "main_path_points": len(paths["main_path"]) if main_loop_complete else 0,
        "observed_main_path_points": len(paths["main_path"]),
        "pit_lane_points": len(paths["pit_lane"]),
        "pit_entry_observations": len(entry_paths),
        "pit_exit_observations": len(exit_paths),
        "main_source_samples": sum(len(items) for items in bins),
        "pit_source_samples": sum(len(items) for items in pit_episodes),
        "rejected_gps_samples": rejected_gps_samples,
        "rejected_geometry_layers": rejected_layers,
    }
    if not main_loop_complete:
        result["status"] = "unavailable"
        result["main_path"] = []
        result["start_finish_line"] = None
        result["unavailable_reasons"].append(
            "A complete closed main circuit loop could not be verified "
            f"({loop_quality['lap_percent_coverage']:.1%} lap coverage, "
            f"{loop_quality['maximum_lap_percent_gap']:.1%} largest gap, "
            f"{loop_quality['closure_distance']:.4f} normalized closure distance)."
        )
    if not pit_recorded:
        result["status"] = "partial" if main_loop_complete else "unavailable"
        result["unavailable_reasons"].append(
            "Pit-road state was not recorded, so pit lane, entry, exit, commitment, and merge layers are unavailable."
        )
    elif not paths["pit_lane"]:
        result["status"] = "partial" if main_loop_complete else "unavailable"
        result["unavailable_reasons"].append("No complete recorded pit-road traversal was available.")
    if rejected_layers:
        result["status"] = "partial" if main_loop_complete else "unavailable"
        result["unavailable_reasons"].append(
            "Implausible recorded track overlays were omitted: " + ", ".join(sorted(rejected_layers)) + "."
        )
    if main_loop_complete:
        # This analysis-owned hash is the cross-language geometry identity.
        # Consumers carry it verbatim; they must not derive a different hash
        # from rendered/scaled points or a subset of the layers.
        result["geometry_hash"] = track_geometry_sha256(result)
    return result

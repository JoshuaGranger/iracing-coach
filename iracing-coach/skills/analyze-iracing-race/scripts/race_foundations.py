"""Portable track, replay, and tire-learning data contracts.

The helpers in this module are deliberately evidence bounded.  They consume
only channels present in a finalized recording, return explicit coverage
gaps, and never infer competitor fuel, tires, or private flag state.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import statistics
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


TIRES = ("LF", "RF", "LR", "RR")
CHECKERED_FLAG = 0x0001
WHITE_FLAG = 0x0002
GREEN_FLAG = 0x0004
YELLOW_FLAGS = 0x0008 | 0x0100 | 0x4000 | 0x8000
BLACK_FLAG = 0x00010000
SESSION_STATES = {
    0: "invalid",
    1: "get_in_car",
    2: "warmup",
    3: "parade_laps",
    4: "racing",
    5: "checkered",
    6: "cooldown",
}
TRACK_SURFACES = {
    -1: "not_in_world",
    0: "off_track",
    1: "in_pit_stall",
    2: "approaching_pits",
    3: "on_track",
}
TIRE_MODEL_VERSION = "nascar-tire-condition-load-match-v1"
MIN_MAIN_LOOP_LAP_PERCENT_COVERAGE = 0.95
MAX_MAIN_LOOP_LAP_PERCENT_GAP = 0.05
MAX_MAIN_LOOP_CLOSURE_DISTANCE = 0.15
MIN_MAIN_LOOP_NORMALIZED_SPAN = 0.20
MAX_MAIN_LOOP_RELATIVE_SEGMENT_DISTANCE = 0.35
MAX_AUXILIARY_BOUNDS_MARGIN = 0.45
MAX_AUXILIARY_RELATIVE_SEGMENT_DISTANCE = 0.55
GPS_PLACEHOLDER_EPSILON = 1e-9
GPS_CLUSTER_RADIUS_DEGREES = 0.25


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _integer(value: Any) -> int | None:
    number = _finite(value)
    return int(round(number)) if number is not None else None


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


def _weighted_mean(values: Sequence[tuple[float, float]]) -> float | None:
    usable = [(value, weight) for value, weight in values if math.isfinite(value) and math.isfinite(weight) and weight > 0]
    total = sum(weight for _, weight in usable)
    return sum(value * weight for value, weight in usable) / total if total > 0 else None


def _weighted_percentile(values: Sequence[tuple[float, float]], fraction: float) -> float | None:
    usable = sorted((value, weight) for value, weight in values if math.isfinite(value) and math.isfinite(weight) and weight > 0)
    total = sum(weight for _, weight in usable)
    if total <= 0:
        return None
    target = max(0.0, min(1.0, fraction)) * total
    cumulative = 0.0
    for value, weight in usable:
        cumulative += weight
        if cumulative >= target:
            return value
    return usable[-1][0]


def _stable_hash(value: Any, length: int = 24) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:length]


def _safe_slug(value: Any, fallback: str = "unknown") -> str:
    result = re.sub(r"[^a-z0-9]+", "-", str(value or "").casefold()).strip("-")
    return result[:100] or fallback


def _path_get(value: Any, *path: str) -> Any:
    current = value
    for key in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _array_value(series: Sequence[Any], sample_index: int, car_index: int) -> Any:
    if sample_index < 0 or sample_index >= len(series):
        return None
    row = series[sample_index]
    if not isinstance(row, Sequence) or isinstance(row, (str, bytes, bytearray)):
        return None
    return row[car_index] if 0 <= car_index < len(row) else None


def _resample_path(points: Sequence[Mapping[str, Any]], count: int) -> list[dict[str, Any]]:
    if not points:
        return []
    if len(points) <= count:
        return [dict(point) for point in points]
    result: list[dict[str, Any]] = []
    for output_index in range(count):
        source_index = round(output_index * (len(points) - 1) / max(1, count - 1))
        result.append(dict(points[source_index]))
    return result


def _average_paths(paths: Sequence[Sequence[Mapping[str, Any]]], count: int) -> list[dict[str, Any]]:
    usable = [path for path in paths if len(path) >= 2]
    if not usable:
        return []
    normalized = [_resample_path(path, count) for path in usable]
    length = min(len(path) for path in normalized)
    return [
        {
            "sequence": index,
            "x": round(float(_median(path[index].get("x") for path in normalized) or 0.0), 8),
            "y": round(float(_median(path[index].get("y") for path in normalized) or 0.0), 8),
            "lap_pct": (
                round(value, 6)
                if (value := _median(path[index].get("lap_pct") for path in normalized))
                is not None
                else None
            ),
            "observations": len(normalized),
        }
        for index in range(length)
    ]


def _normalize_geometry(
    paths: Mapping[str, Sequence[Mapping[str, Any]]],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    # The verified racing surface is the projection anchor.  A single iRacing
    # GPS placeholder in a pit-entry window used to make the 0/0 coordinate an
    # extrema for every layer, compressing an Iowa-sized oval to ~0.00005 of the
    # normalized canvas and producing kilometre-long synthetic lines.
    anchor_points = list(paths.get("main_path") or ())
    if not anchor_points:
        anchor_points = [point for path in paths.values() for point in path]
    xs = [number for point in anchor_points if (number := _finite(point.get("x"))) is not None]
    ys = [number for point in anchor_points if (number := _finite(point.get("y"))) is not None]
    if not xs or not ys:
        return {name: [] for name in paths}, {}
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    scale = max(max_x - min_x, max_y - min_y)
    if scale <= 1e-12:
        return {name: [] for name in paths}, {}
    normalized: dict[str, list[dict[str, Any]]] = {}
    for name, path in paths.items():
        normalized[name] = [
            {
                **dict(point),
                "x": round((float(point["x"]) - min_x) / scale, 8),
                "y": round((max_y - float(point["y"])) / scale, 8),
            }
            for point in path
            if _finite(point.get("x")) is not None and _finite(point.get("y")) is not None
        ]
    return normalized, {
        "source_bounds": {
            "minimum_x": min_x,
            "maximum_x": max_x,
            "minimum_y": min_y,
            "maximum_y": max_y,
        },
        "normalization_scale": scale,
    }


def _geometry_extents(path: Sequence[Mapping[str, Any]]) -> tuple[float, float, float, float] | None:
    points = [
        (x, y)
        for point in path
        if (x := _finite(point.get("x"))) is not None
        and (y := _finite(point.get("y"))) is not None
    ]
    if not points:
        return None
    return (
        min(point[0] for point in points),
        max(point[0] for point in points),
        min(point[1] for point in points),
        max(point[1] for point in points),
    )


def _path_is_plausible_relative_to_main(
    path: Sequence[Mapping[str, Any]],
    main_path: Sequence[Mapping[str, Any]],
) -> bool:
    if len(path) < 2:
        return False
    main_bounds = _geometry_extents(main_path)
    path_bounds = _geometry_extents(path)
    if main_bounds is None or path_bounds is None:
        return False
    main_span = max(main_bounds[1] - main_bounds[0], main_bounds[3] - main_bounds[2])
    if not math.isfinite(main_span) or main_span <= 1e-9:
        return False
    margin = main_span * MAX_AUXILIARY_BOUNDS_MARGIN
    if (
        path_bounds[0] < main_bounds[0] - margin
        or path_bounds[1] > main_bounds[1] + margin
        or path_bounds[2] < main_bounds[2] - margin
        or path_bounds[3] > main_bounds[3] + margin
    ):
        return False
    points = [
        (float(point["x"]), float(point["y"]))
        for point in path
        if _finite(point.get("x")) is not None and _finite(point.get("y")) is not None
    ]
    if len(points) != len(path):
        return False
    maximum_segment = max(
        (math.hypot(after[0] - before[0], after[1] - before[1]) for before, after in zip(points, points[1:])),
        default=0.0,
    )
    return maximum_segment <= main_span * MAX_AUXILIARY_RELATIVE_SEGMENT_DISTANCE


def _line_is_plausible_relative_to_main(
    line: Mapping[str, Any] | None,
    main_path: Sequence[Mapping[str, Any]],
) -> bool:
    if not isinstance(line, Mapping):
        return False
    a, b = line.get("a"), line.get("b")
    if not isinstance(a, Mapping) or not isinstance(b, Mapping):
        return False
    if not _path_is_plausible_relative_to_main([a, b], main_path):
        return False
    bounds = _geometry_extents(main_path)
    if bounds is None:
        return False
    span = max(bounds[1] - bounds[0], bounds[3] - bounds[2])
    length = math.hypot(float(b["x"]) - float(a["x"]), float(b["y"]) - float(a["y"]))
    return length <= span * 0.20


def _sanitize_geometry_layers(value: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Drop impossible overlays rather than publishing off-canvas SVG work."""

    sanitized = dict(value)
    main_path = list(sanitized.get("main_path") or ())
    rejected: list[str] = []
    for field in ("pit_lane", "pit_entry_path", "pit_exit_path"):
        path = list(sanitized.get(field) or ())
        if path and not _path_is_plausible_relative_to_main(path, main_path):
            sanitized[field] = []
            rejected.append(field)
    line_fields = {
        "start_finish_line": "start_finish_line",
        "pit_commitment_line": "pit_commitment_line",
        "pit_merge_line": "pit_merge_line",
    }
    for field in line_fields:
        line = sanitized.get(field)
        if line is not None and not _line_is_plausible_relative_to_main(line, main_path):
            sanitized[field] = None
            rejected.append(field)
    return sanitized, rejected


def _main_loop_quality(path: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Measure whether recorded points describe one complete, closed circuit loop.

    Point count is intentionally not a completeness proxy.  A low-rate recording
    can still cover a whole lap, while thousands of samples from half a lap must
    remain unavailable.  Circular lap-percent coverage detects the latter and
    normalized endpoint distance independently verifies geometric closure.
    """

    usable_path = [
        point
        for point in path
        if isinstance(point, Mapping)
        and _finite(point.get("x")) is not None
        and _finite(point.get("y")) is not None
    ]
    lap_percentages = sorted(
        {
            value % 1.0
            for point in usable_path
            if (value := _finite(point.get("lap_pct"))) is not None
        }
    )
    if len(lap_percentages) >= 2:
        gaps = [
            lap_percentages[index + 1] - lap_percentages[index]
            for index in range(len(lap_percentages) - 1)
        ]
        gaps.append(1.0 - lap_percentages[-1] + lap_percentages[0])
        maximum_gap = max(gaps)
        coverage = max(0.0, min(1.0, 1.0 - maximum_gap))
    else:
        maximum_gap = 1.0
        coverage = 0.0

    closure_distance = 1.0
    main_path_span = 0.0
    maximum_segment_distance = math.inf
    if len(usable_path) >= 2:
        first_x = _finite(usable_path[0].get("x"))
        first_y = _finite(usable_path[0].get("y"))
        last_x = _finite(usable_path[-1].get("x"))
        last_y = _finite(usable_path[-1].get("y"))
        if None not in (first_x, first_y, last_x, last_y):
            closure_distance = math.hypot(last_x - first_x, last_y - first_y)
        bounds = _geometry_extents(usable_path)
        if bounds is not None:
            main_path_span = max(bounds[1] - bounds[0], bounds[3] - bounds[2])
        maximum_segment_distance = max(
            (
                math.hypot(
                    float(after["x"]) - float(before["x"]),
                    float(after["y"]) - float(before["y"]),
                )
                for before, after in zip(usable_path, usable_path[1:])
            ),
            default=0.0,
        )

    relative_segment_distance = (
        maximum_segment_distance / main_path_span
        if main_path_span > 1e-12 and math.isfinite(maximum_segment_distance)
        else math.inf
    )
    geometry_plausible = bool(
        main_path_span >= MIN_MAIN_LOOP_NORMALIZED_SPAN
        and relative_segment_distance <= MAX_MAIN_LOOP_RELATIVE_SEGMENT_DISTANCE
    )

    complete = bool(
        len(usable_path) >= 3
        and coverage >= MIN_MAIN_LOOP_LAP_PERCENT_COVERAGE
        and maximum_gap <= MAX_MAIN_LOOP_LAP_PERCENT_GAP
        and closure_distance <= MAX_MAIN_LOOP_CLOSURE_DISTANCE
        and geometry_plausible
    )
    return {
        "main_loop_complete": complete,
        "lap_percent_coverage": round(coverage, 6),
        "maximum_lap_percent_gap": round(maximum_gap, 6),
        "closure_distance": round(closure_distance, 8),
        "geometry_plausible": geometry_plausible,
        "main_path_span": round(main_path_span, 8),
        "maximum_segment_distance": (
            round(maximum_segment_distance, 8) if math.isfinite(maximum_segment_distance) else None
        ),
        "maximum_relative_segment_distance": (
            round(relative_segment_distance, 8) if math.isfinite(relative_segment_distance) else None
        ),
    }


def _cross_line(path: Sequence[Mapping[str, Any]], index: int, width: float = 0.018) -> dict[str, Any] | None:
    if not path:
        return None
    center_index = max(0, min(len(path) - 1, index))
    before = path[max(0, center_index - 1)]
    after = path[min(len(path) - 1, center_index + 1)]
    center = path[center_index]
    dx = float(after["x"]) - float(before["x"])
    dy = float(after["y"]) - float(before["y"])
    magnitude = math.hypot(dx, dy)
    if magnitude <= 1e-12:
        return None
    perpendicular_x, perpendicular_y = -dy / magnitude, dx / magnitude
    return {
        "a": {
            "x": round(float(center["x"]) - perpendicular_x * width, 8),
            "y": round(float(center["y"]) - perpendicular_y * width, 8),
        },
        "b": {
            "x": round(float(center["x"]) + perpendicular_x * width, 8),
            "y": round(float(center["y"]) + perpendicular_y * width, 8),
        },
    }


def track_configuration_key(identity: Mapping[str, Any]) -> str:
    track_id = identity.get("track_id")
    config = identity.get("track_config") or identity.get("track_path")
    prefix = str(track_id) if track_id not in (None, "") else "track"
    return _safe_slug(f"{prefix}-{config or identity.get('track_name')}")


def track_geometry_sha256(value: Mapping[str, Any]) -> str:
    """Canonical cross-language identity for the published geometry layers."""

    canonical_geometry = {
        "track_configuration_key": value.get("track_configuration_key"),
        "coordinate_system": value.get("coordinate_system"),
        "main_path": value.get("main_path") or [],
        "pit_lane": value.get("pit_lane") or [],
        "pit_entry_path": value.get("pit_entry_path") or [],
        "pit_exit_path": value.get("pit_exit_path") or [],
        "start_finish_line": value.get("start_finish_line"),
        "pit_commitment_line": value.get("pit_commitment_line"),
        "pit_merge_line": value.get("pit_merge_line"),
    }
    return hashlib.sha256(
        json.dumps(
            canonical_geometry,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        ).encode("utf-8")
    ).hexdigest()


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


def _flag_labels(raw: int) -> list[str]:
    labels: list[str] = []
    if raw & GREEN_FLAG:
        labels.append("green")
    if raw & YELLOW_FLAGS:
        labels.append("yellow")
    if raw & WHITE_FLAG:
        labels.append("white")
    if raw & CHECKERED_FLAG:
        labels.append("checkered")
    if raw & BLACK_FLAG:
        labels.append("black")
    return labels


def _participants(session_info: Mapping[str, Any]) -> tuple[list[dict[str, Any]], int | None]:
    drivers = _path_get(session_info, "DriverInfo", "Drivers")
    player = _integer(_path_get(session_info, "DriverInfo", "DriverCarIdx"))
    result: list[dict[str, Any]] = []
    if not isinstance(drivers, Sequence) or isinstance(drivers, (str, bytes, bytearray)):
        return result, player
    for raw in drivers:
        if not isinstance(raw, Mapping):
            continue
        car_index = _integer(raw.get("CarIdx"))
        if car_index is None or car_index < 0:
            continue
        result.append(
            {
                "car_index": car_index,
                "car_number": str(raw.get("CarNumber") or raw.get("CarNumberRaw") or "").strip() or None,
                "class_id": _integer(raw.get("CarClassID")),
                "class_name": str(raw.get("CarClassShortName") or raw.get("CarClassRelSpeed") or "").strip() or None,
                "car_name": str(raw.get("CarScreenName") or raw.get("CarPath") or "").strip() or None,
                "driver_name": str(raw.get("UserName") or raw.get("AbbrevName") or "").strip() or None,
                "team_name": str(raw.get("TeamName") or "").strip() or None,
                "is_player": car_index == player,
                "is_spectator": bool(raw.get("IsSpectator")),
            }
        )
    return sorted(result, key=lambda item: item["car_index"]), player


def build_race_replay(
    channels: Mapping[str, Sequence[Any]],
    session_info: Mapping[str, Any],
    metadata: Mapping[str, Any],
    *,
    output_hz: float = 2.0,
) -> dict[str, Any]:
    required = {
        "SessionTime": "SessionTime is required to order replay frames.",
        "SessionState": "SessionState is required to identify grid, racing, and post-checker scope.",
        "CarIdxLapDistPct": "CarIdxLapDistPct is required to place competitors on the track.",
    }
    optional = {
        "CarIdxLap": "Per-car lap number was not recorded.",
        "CarIdxLapCompleted": "Per-car completed-lap count was not recorded.",
        "CarIdxPosition": "Per-car overall position was not recorded.",
        "CarIdxClassPosition": "Per-car class position was not recorded.",
        "CarIdxOnPitRoad": "Per-car pit-road state was not recorded.",
        "CarIdxTrackSurface": "Per-car track-surface state was not recorded.",
        "CarIdxPaceFlags": "Per-car pace flags were not recorded; private black/penalty flags cannot be reconstructed.",
        "CarIdxLastLapTime": "Per-car last-lap timing was not recorded.",
        "CarIdxBestLapTime": "Per-car best-lap timing was not recorded.",
        "SessionFlags": "Global session flags were not recorded.",
    }
    participants, player_index = _participants(session_info)
    coverage = [
        {
            "channel": name,
            "status": "recorded" if name in channels else "unavailable",
            "reason": None if name in channels else reason,
        }
        for name, reason in {**required, **optional}.items()
    ]
    missing = [reason for name, reason in required.items() if name not in channels]
    if not participants:
        missing.append("DriverInfo.Drivers was unavailable, so car numbers and classes cannot be identified.")
    result: dict[str, Any] = {
        "schema_version": 1,
        "status": "unavailable" if missing else "usable",
        "unavailable_reasons": missing,
        "coverage": coverage,
        "sample_rate_hz": output_hz,
        "interpolation": "linear lap-distance interpolation between recorded replay frames",
        "participant_count": len(participants),
        "player_car_index": player_index,
        "participants": participants,
        "frames": [],
        "limitations": [
            "SessionFlags is a global/player-visible flag channel, not a private flag feed for every competitor.",
            "Competitor fuel, tire wear, tire temperature, and setup are not present in this replay contract.",
        ],
    }
    if missing:
        return result

    times = channels["SessionTime"]
    states = channels["SessionState"]
    count = min(len(times), len(states), len(channels["CarIdxLapDistPct"]))
    scoped = [
        index
        for index in range(count)
        if (state := _integer(states[index])) is not None and 3 <= state <= 6
    ]
    if not scoped:
        result["status"] = "unavailable"
        result["unavailable_reasons"].append(
            "No grid/parade, racing, checkered, or cooldown SessionState samples were recorded."
        )
        return result

    native_rate = _finite(metadata.get("sample_rate") or metadata.get("tick_rate")) or 20.0
    step = max(1, int(round(native_rate / max(0.5, min(10.0, output_hz)))))
    first, last = scoped[0], scoped[-1]
    selected: list[int] = []
    prior_state: int | None = None
    prior_flags: int | None = None
    session_flags = channels.get("SessionFlags") or ()

    def flags_at(index: int) -> int:
        return (_integer(session_flags[index]) or 0) if index < len(session_flags) else 0

    for index in range(first, last + 1):
        state = _integer(states[index])
        flags = flags_at(index)
        if index == first or index == last or (index - first) % step == 0 or state != prior_state or flags != prior_flags:
            selected.append(index)
        prior_state, prior_flags = state, flags

    series_names = {
        "lap": "CarIdxLap",
        "completed_laps": "CarIdxLapCompleted",
        "lap_pct": "CarIdxLapDistPct",
        "overall_position": "CarIdxPosition",
        "class_position": "CarIdxClassPosition",
        "on_pit_road": "CarIdxOnPitRoad",
        "track_surface": "CarIdxTrackSurface",
        "pace_flags": "CarIdxPaceFlags",
    }
    frames: list[dict[str, Any]] = []
    for index in selected:
        raw_flags = flags_at(index)
        cars: list[dict[str, Any]] = []
        for participant in participants:
            car_index = int(participant["car_index"])
            raw_pct = _finite(_array_value(channels["CarIdxLapDistPct"], index, car_index))
            if raw_pct is None or raw_pct < -0.01:
                continue
            car: dict[str, Any] = {"car_index": car_index, "lap_pct": round(raw_pct % 1.0, 6)}
            for field, channel in series_names.items():
                if field == "lap_pct" or channel not in channels:
                    continue
                value = _array_value(channels[channel], index, car_index)
                if field == "on_pit_road":
                    car[field] = bool(value) if value is not None else None
                else:
                    number = _integer(value)
                    if number is not None:
                        car[field] = number
            if "track_surface" in car:
                car["track_surface_label"] = TRACK_SURFACES.get(car["track_surface"])
            for field, channel in (("last_lap_time_s", "CarIdxLastLapTime"), ("best_lap_time_s", "CarIdxBestLapTime")):
                if channel in channels and (timing := _finite(_array_value(channels[channel], index, car_index))) is not None and timing > 0:
                    car[field] = round(timing, 4)
            cars.append(car)
        frames.append(
            {
                "session_time_s": round(float(_finite(times[index]) or 0.0), 3),
                "session_state": SESSION_STATES.get(_integer(states[index]) or -1, "unknown"),
                "global_flags": raw_flags,
                "global_flag_labels": _flag_labels(raw_flags),
                "cars": cars,
            }
        )
    result["frames"] = frames
    result["frame_count"] = len(frames)
    result["start_session_time_s"] = frames[0]["session_time_s"] if frames else None
    result["end_session_time_s"] = frames[-1]["session_time_s"] if frames else None
    if any(item["status"] == "unavailable" for item in coverage if item["channel"] in optional):
        result["status"] = "partial"
    return result


def nascar_family(identity: Mapping[str, Any]) -> str | None:
    value = " ".join(
        str(identity.get(key) or "")
        for key in ("car_name", "car_path")
    ).casefold()
    if "xfinity" in value or "stockcars2" in value:
        return "nascar_xfinity"
    if "truck" in value or re.search(r"\btrucks?\b", value):
        return "nascar_truck"
    if "next gen" in value or "nextgen" in value or "stockcars3" in value:
        return "nascar_next_gen"
    if "arca" in value or "stockcars arca" in value:
        return "arca"
    return None


def _remaining_omi(corner: str, remaining: Mapping[str, Any]) -> dict[str, float | None]:
    left_side = corner.startswith("L")
    outer = _finite(remaining.get("L" if left_side else "R"))
    middle = _finite(remaining.get("M"))
    inner = _finite(remaining.get("R" if left_side else "L"))
    return {
        "outer": round(outer, 3) if outer is not None else None,
        "middle": round(middle, 3) if middle is not None else None,
        "inner": round(inner, 3) if inner is not None else None,
    }


def build_tire_learning(
    identity: Mapping[str, Any],
    runs: Sequence[Mapping[str, Any]],
    laps: Sequence[Mapping[str, Any]],
    analysis_id: str,
) -> dict[str, Any]:
    family = nascar_family(identity)
    tire_compound = identity.get("tire_compound")
    recorded_conditions = identity.get("conditions") if isinstance(identity.get("conditions"), Mapping) else {}
    context = {
        "family": family,
        "car_id": identity.get("car_id"),
        "car_path": identity.get("car_path"),
        "track_id": identity.get("track_id"),
        "track_config": identity.get("track_config"),
        "setup_type": "fixed" if identity.get("is_fixed_setup") else "open",
        "tire_compound": tire_compound,
    }
    context_key = _safe_slug(
        f"{family or 'unsupported'}-{identity.get('car_id') or identity.get('car_path')}-"
        f"{identity.get('track_id') or identity.get('track_name')}-{identity.get('track_config')}-"
        f"{context['setup_type']}-{tire_compound if tire_compound not in (None, '') else 'compound-unknown'}"
    )
    result: dict[str, Any] = {
        "schema_version": 1,
        "status": "collecting" if family else "unsupported_car_family",
        "family": family,
        "supported_families": ["nascar_xfinity", "nascar_truck", "nascar_next_gen", "arca"],
        "context_key": context_key,
        "context": context,
        "observations": [],
        "prediction": {
            "status": "unavailable",
            "reason": "Persistent matching observations have not been evaluated yet.",
        },
    }
    if not family:
        result["prediction"]["reason"] = "Persistent tire learning is currently NASCAR/ARCA-first."
        return result

    lap_by_number = {
        _integer(lap.get("lap")): lap for lap in laps if _integer(lap.get("lap")) is not None
    }
    prior_changed: set[str] = set()
    for run_index, run in enumerate(runs):
        observation = run.get("tire_observation")
        observed_tires = observation.get("tires") if isinstance(observation, Mapping) else None
        lifecycle = run.get("tire_set_lifecycle") or {}
        lifecycle_corners = lifecycle.get("corners") or {}
        fresh_corners = set(TIRES) if run_index > 0 and prior_changed.issuperset(TIRES) else set()
        if isinstance(observed_tires, Mapping) and observed_tires:
            valid_numbers = [
                _integer(value)
                for value in (run.get("valid_green_lap_numbers") or ())
                if _integer(value) is not None
            ]
            valid_lap_times = [
                (number, value)
                for number in valid_numbers
                if (lap := lap_by_number.get(number)) is not None
                and (value := _finite(lap.get("lap_time_s"))) is not None
            ]
            capability_lap = min(valid_lap_times, key=lambda item: item[1]) if valid_lap_times else None
            tires: dict[str, Any] = {}
            eligible = True
            for corner in TIRES:
                details = observed_tires.get(corner)
                if not isinstance(details, Mapping):
                    eligible = False
                    continue
                remaining = details.get("remaining_percent")
                if not isinstance(remaining, Mapping):
                    eligible = False
                    continue
                age = lifecycle_corners.get(corner) or {}
                green_age = _finite(age.get("green_laps_on_set"))
                caution_age = _finite(age.get("caution_laps_on_set"))
                bands = _remaining_omi(corner, remaining)
                if corner not in fresh_corners or green_age is None or green_age <= 0 or any(value is None for value in bands.values()):
                    eligible = False
                tires[corner] = {
                    "remaining_percent_omi": bands,
                    "green_laps_on_set": green_age,
                    "caution_laps_on_set": caution_age,
                    "fresh_start_confirmed": corner in fresh_corners,
                }
            item = {
                "observation_id": _stable_hash(
                    {"analysis_id": analysis_id, "run_number": run.get("run_number"), "context": context},
                    32,
                ),
                "analysis_id": analysis_id,
                "run_number": run.get("run_number"),
                "context": context,
                "tires": tires,
                "pace": {
                    "capability_lap_s": capability_lap[1] if capability_lap else None,
                    "capability_tire_age_green_laps": None,
                    "early_average_lap_s": _finite(_path_get(run, "pace", "early_average_lap_s")),
                    "late_average_lap_s": _finite(_path_get(run, "pace", "late_average_lap_s")),
                    "pace_cost_s": _finite(_path_get(run, "pace", "early_to_late_delta_s")),
                    "slope_s_per_green_lap": _finite(_path_get(run, "pace", "green_lap_time_slope_s_per_lap")),
                },
                "load": dict(run.get("driving_load") or {}),
                "vehicle_dynamics": dict(run.get("vehicle_dynamics") or {}),
                "conditions": {
                    "track_temp_c": _finite(recorded_conditions.get("track_temp_c")),
                    "air_temp_c": _finite(recorded_conditions.get("air_temp_c")),
                    "track_usage": _finite(recorded_conditions.get("track_usage")),
                    "track_wetness": _finite(recorded_conditions.get("track_wetness")),
                    "caution_fraction": (
                        (_finite(run.get("caution_lap_equivalents")) or 0.0)
                        / max(1e-9, (_finite(run.get("green_lap_equivalents")) or 0.0) + (_finite(run.get("caution_lap_equivalents")) or 0.0))
                    ),
                    "tire_compound": tire_compound,
                },
                "eligible_for_rate_model": eligible,
                "eligibility_reason": (
                    "all four corners have a measured O/M/I endpoint after a confirmed fresh start"
                    if eligible
                    else "a confirmed fresh start plus all four measured O/M/I endpoints is required"
                ),
            }
            if capability_lap is not None:
                endpoint_ages = [
                    value for details in tires.values()
                    if (value := _finite(details.get("green_laps_on_set"))) is not None
                ]
                endpoint_age = min(endpoint_ages) if endpoint_ages else None
                run_green = _finite(run.get("green_laps")) or 0.0
                ordinal = valid_numbers.index(capability_lap[0]) + 1 if capability_lap[0] in valid_numbers else 1
                if endpoint_age is not None:
                    item["pace"]["capability_tire_age_green_laps"] = max(0.0, endpoint_age - run_green) + ordinal
            result["observations"].append(item)
        service = run.get("pit_service") or {}
        prior_changed = {
            str(value).upper()
            for value in (service.get("tires_changed_observed") or ())
            if str(value).upper() in TIRES
        }

    current = runs[-1] if runs else {}
    current_lifecycle = (current.get("tire_set_lifecycle") or {}).get("corners") or {}
    result["current_tire_age"] = {
        corner: {
            "green_laps": _finite((current_lifecycle.get(corner) or {}).get("green_laps_on_set")),
            "caution_laps": _finite((current_lifecycle.get(corner) or {}).get("caution_laps_on_set")),
        }
        for corner in TIRES
    }
    current_run = runs[-1] if runs else {}
    current_green = _finite(current_run.get("green_lap_equivalents")) or 0.0
    current_caution = _finite(current_run.get("caution_lap_equivalents")) or 0.0
    result["prediction_context"] = {
        "conditions": {
            "track_temp_c": _finite(recorded_conditions.get("track_temp_c")),
            "air_temp_c": _finite(recorded_conditions.get("air_temp_c")),
            "track_usage": _finite(recorded_conditions.get("track_usage")),
            "track_wetness": _finite(recorded_conditions.get("track_wetness")),
            "caution_fraction": current_caution / max(1e-9, current_green + current_caution),
            "tire_compound": tire_compound,
        },
        "load": dict(current_run.get("driving_load") or {}),
        "vehicle_dynamics": dict(current_run.get("vehicle_dynamics") or {}),
    }
    result["status"] = "observed" if result["observations"] else "collecting"
    return result


def build_tire_prediction(
    observations: Sequence[Mapping[str, Any]],
    current_tire_age: Mapping[str, Any],
    prediction_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    prediction_context = prediction_context or {}
    canonical_observations = sorted(
        [dict(item) for item in observations if isinstance(item, Mapping)],
        key=lambda item: str(item.get("observation_id") or ""),
    )
    observation_set_fingerprint = _stable_hash(canonical_observations, 64)
    target_compound = _path_get(prediction_context, "conditions", "tire_compound")
    ineligible_count = sum(
        item.get("eligible_for_rate_model") is not True
        for item in canonical_observations
    )
    compound_mismatch_count = sum(
        item.get("eligible_for_rate_model") is True
        and target_compound not in (None, "")
        and _path_get(item, "conditions", "tire_compound") != target_compound
        for item in canonical_observations
    )
    eligible = [
        item for item in canonical_observations
        if item.get("eligible_for_rate_model") is True
        and (
            target_compound in (None, "")
            or _path_get(item, "conditions", "tire_compound") == target_compound
        )
    ]
    sessions = {str(item.get("analysis_id")) for item in eligible if item.get("analysis_id")}
    exclusion_reasons = [
        text
        for count, text in (
            (ineligible_count, f"{ineligible_count} observation(s) lacked a confirmed fresh start or complete measured O/M/I endpoint"),
            (compound_mismatch_count, f"{compound_mismatch_count} observation(s) used a different tire compound"),
        )
        if count > 0
    ]
    base_contract = {
        "model_version": TIRE_MODEL_VERSION,
        "observation_set_fingerprint": observation_set_fingerprint,
        "total_observations": len(canonical_observations),
        "excluded_observations": len(canonical_observations) - len(eligible),
        "exclusion_reasons": exclusion_reasons,
        "matching_scope": "exact car/track-configuration/setup model and tire compound; distance-weighted recorded conditions and controls/load",
    }
    if len(eligible) < 3 or len(sessions) < 3:
        return {
            **base_contract,
            "status": "unavailable",
            "confidence": "low",
            "reason": "At least three matching sessions with the same car, track configuration, setup type, tire compound, confirmed fresh starts, and measured O/M/I endpoints are required.",
            "eligible_observations": len(eligible),
            "matching_sessions": len(sessions),
            "effective_matched_observations": 0.0,
            "median_feature_distance": None,
            "comparable_feature_count": 0,
            "matched_features": [],
        }

    def feature_values(value: Mapping[str, Any]) -> dict[str, float]:
        conditions = value.get("conditions") or {}
        load = value.get("load") or {}
        dynamics = value.get("vehicle_dynamics") or {}
        result: dict[str, float] = {}
        direct = {
            "track_temp_c": conditions.get("track_temp_c"),
            "air_temp_c": conditions.get("air_temp_c"),
            "track_usage": conditions.get("track_usage"),
            "track_wetness": conditions.get("track_wetness"),
            "caution_fraction": conditions.get("caution_fraction"),
            "wheel_lock_s": dynamics.get("braking_wheel_lock_proxy_s"),
            "wheelspin_s": dynamics.get("rear_wheelspin_proxy_s"),
            "abs_active_s": dynamics.get("abs_active_s"),
            "yaw_p95_deg_s": dynamics.get("yaw_rate_abs_p95_deg_s_mean"),
        }
        for name, raw in direct.items():
            if (number := _finite(raw)) is not None:
                result[name] = number
        for name, early_key, late_key in (
            ("brake_energy", "early_brake_energy_proxy", "late_brake_energy_proxy"),
            ("steering_work", "early_steering_work_proxy", "late_steering_work_proxy"),
        ):
            values = [_finite(load.get(early_key)), _finite(load.get(late_key))]
            finite = [number for number in values if number is not None]
            if finite:
                result[name] = float(statistics.mean(finite))
        return result

    target_features = feature_values(prediction_context)
    observation_features = [feature_values(item) for item in eligible]
    scales: dict[str, float] = {}
    for name in sorted(target_features):
        values = [features[name] for features in observation_features if name in features] + [target_features[name]]
        if len(values) < 3:
            continue
        low = _percentile(values, 0.10)
        high = _percentile(values, 0.90)
        span = (high - low) if low is not None and high is not None else 0.0
        if span <= 1e-9:
            span = max(values) - min(values)
        scales[name] = max(span, 1e-6)

    weighted: list[tuple[Mapping[str, Any], float, float]] = []
    for item, features in zip(eligible, observation_features):
        deltas = [
            (features[name] - target_features[name]) / scale
            for name, scale in scales.items()
            if name in features
        ]
        distance = math.sqrt(sum(value * value for value in deltas) / len(deltas)) if deltas else 1.5
        weight = 1.0 / (0.25 + distance) ** 2
        weighted.append((item, weight, distance))
    weight_sum = sum(weight for _, weight, _ in weighted)
    effective_count = (
        weight_sum * weight_sum / sum(weight * weight for _, weight, _ in weighted)
        if weighted and sum(weight * weight for _, weight, _ in weighted) > 0 else 0.0
    )
    median_distance = statistics.median(distance for _, _, distance in weighted)

    corner_predictions: dict[str, Any] = {}
    laps_remaining_values: list[float] = []
    for corner in TIRES:
        age = _finite((current_tire_age.get(corner) or {}).get("green_laps"))
        band_predictions: dict[str, Any] = {}
        for band in ("outer", "middle", "inner"):
            rates: list[tuple[float, float]] = []
            for item, weight, _ in weighted:
                tire = (item.get("tires") or {}).get(corner) or {}
                green_laps = _finite(tire.get("green_laps_on_set"))
                remaining = _finite((tire.get("remaining_percent_omi") or {}).get(band))
                if green_laps is not None and green_laps > 0 and remaining is not None:
                    rates.append((max(0.0, 100.0 - remaining) / green_laps, weight))
            if not rates or age is None:
                continue
            predicted_rate = _weighted_mean(rates)
            if predicted_rate is None:
                continue
            low_rate = float(_weighted_percentile(rates, 0.10) or predicted_rate)
            high_rate = float(_weighted_percentile(rates, 0.90) or predicted_rate)
            remaining = max(0.0, 100.0 - predicted_rate * age)
            low_remaining = max(0.0, 100.0 - high_rate * age)
            high_remaining = max(0.0, 100.0 - low_rate * age)
            laps_remaining = remaining / predicted_rate if predicted_rate > 1e-9 else None
            if laps_remaining is not None:
                laps_remaining_values.append(laps_remaining)
            band_predictions[band] = {
                "remaining_percent": round(remaining, 2),
                "low_percent": round(low_remaining, 2),
                "high_percent": round(high_remaining, 2),
                "wear_rate_percent_per_green_lap": round(predicted_rate, 4),
                "laps_remaining_to_zero": round(laps_remaining, 1) if laps_remaining is not None else None,
            }
        if band_predictions:
            corner_predictions[corner] = band_predictions

    current_ages = [
        value for corner in TIRES
        if (value := _finite((current_tire_age.get(corner) or {}).get("green_laps"))) is not None
    ]
    target_age = min(current_ages) if current_ages else None
    capability: list[tuple[float, float]] = []
    pace_cost: list[tuple[float, float]] = []
    slopes: list[tuple[float, float]] = []
    for item, weight, _ in weighted:
        pace = item.get("pace") or {}
        slope = _finite(pace.get("slope_s_per_green_lap"))
        if slope is not None:
            slopes.append((slope, weight))
        cost = _finite(pace.get("pace_cost_s"))
        if cost is not None:
            pace_cost.append((cost, weight))
        raw_capability = _finite(pace.get("capability_lap_s"))
        observed_age = _finite(pace.get("capability_tire_age_green_laps"))
        if raw_capability is not None and slope is not None and observed_age is not None and target_age is not None:
            capability.append((raw_capability + slope * (target_age - observed_age), weight))
    comparable_feature_count = len(scales)
    confidence = (
        "high"
        if len(sessions) >= 10
        and effective_count >= 6
        and median_distance <= 1.0
        and comparable_feature_count >= 4
        else "medium"
        if len(sessions) >= 5
        and effective_count >= 3
        and median_distance <= 1.5
        and comparable_feature_count >= 2
        else "low"
    )
    feature_match: dict[str, Any] = {}
    for name in scales:
        historical = _weighted_mean([
            (features[name], weight)
            for features, (_, weight, _) in zip(observation_features, weighted)
            if name in features
        ])
        if historical is not None:
            feature_match[name] = {
                "current": round(target_features[name], 4),
                "matched_average": round(historical, 4),
                "delta": round(target_features[name] - historical, 4),
            }
    capability_value = _weighted_mean(capability)
    pace_cost_value = _weighted_mean(pace_cost)
    slope_value = _weighted_mean(slopes)
    return {
        **base_contract,
        "status": "predicted" if corner_predictions else "unavailable",
        "evidence_class": "historical_local_prediction",
        "method": "distance_weighted_matching across recorded conditions, caution mix, and controls/load; capability pace is adjusted to current tire age using each observation's measured pace slope",
        "confidence": confidence,
        "eligible_observations": len(eligible),
        "matching_sessions": len(sessions),
        "effective_matched_observations": round(effective_count, 2),
        "median_feature_distance": round(median_distance, 3),
        "comparable_feature_count": comparable_feature_count,
        "matched_features": sorted(scales),
        "feature_match": feature_match,
        "tires": corner_predictions,
        "laps_remaining": round(min(laps_remaining_values), 1) if laps_remaining_values else None,
        "laps_remaining_definition": "minimum predicted green laps until any modeled O/M/I band reaches zero; not a safety limit",
        "pace_cost_s": round(pace_cost_value, 3) if pace_cost_value is not None else None,
        "pace_cost_low_s": round(float(_weighted_percentile(pace_cost, 0.10)), 3) if pace_cost else None,
        "pace_cost_high_s": round(float(_weighted_percentile(pace_cost, 0.90)), 3) if pace_cost else None,
        "pace_slope_s_per_green_lap": round(slope_value, 4) if slope_value is not None else None,
        "capability_pace_s": round(capability_value, 3) if capability_value is not None else None,
        "capability_pace_low_s": round(float(_weighted_percentile(capability, 0.10)), 3) if capability else None,
        "capability_pace_high_s": round(float(_weighted_percentile(capability, 0.90)), 3) if capability else None,
        "capability_target_tire_age_green_laps": round(target_age, 2) if target_age is not None else None,
        "capability_status": "predicted" if capability else "unavailable",
        "capability_unavailable_reason": None if capability else "Capability pace requires recorded fastest-lap tire age and clean-run pace slope in at least one matched observation.",
    }


def model_file_name(context_key: str) -> str:
    return f"{_safe_slug(context_key)}.json"


__all__ = [
    "TIRE_MODEL_VERSION",
    "build_race_replay",
    "build_tire_learning",
    "build_tire_prediction",
    "build_track_geometry",
    "model_file_name",
    "nascar_family",
    "track_configuration_key",
]

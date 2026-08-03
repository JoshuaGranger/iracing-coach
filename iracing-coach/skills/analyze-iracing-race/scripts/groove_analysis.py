"""Deterministic player-path and groove-evolution evidence from iRacing IBT data.

The module reports a signed lateral offset from the driver's own session-median
path.  Stable signed reference-path curvature can calibrate whether an observed
movement is toward the local corner inside or outside.  It still does not claim
an absolute low/middle/high lane or a best groove because the IBT channels used
here do not contain track edges or controlled comparative performance evidence.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = 2
EARTH_RADIUS_M = 6_371_008.8
DEFAULT_BINS = 120
MAX_INPUT_SAMPLES = 200_000
MAX_ZONES = 12
MAX_RUNS = 20
MAX_SAMPLES_PER_LAP_ZONE = 2_000
MIN_GEOMETRY_BIN_COUNT = 5
MIN_ZONE_REFERENCE_COVERAGE = 0.85
MIN_ZONE_HEADING_CHANGE_DEG = 12.0
MIN_CURVATURE_SIGN_AGREEMENT = 0.85
CAUTION_FLAGS = 0x0008 | 0x0100 | 0x0200 | 0x4000 | 0x8000
TIRE_ODOMETER_CHANNELS = ("LFodometer", "RFodometer", "LRodometer", "RRodometer")


def _finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return float(value)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _median(values: Sequence[float]) -> float | None:
    return statistics.median(values) if values else None


def _percentile(values: Sequence[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = max(0.0, min(1.0, fraction)) * (len(ordered) - 1)
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _round(value: Any, digits: int = 3) -> float | None:
    number = _finite(value)
    return round(number, digits) if number is not None else None


def _series(channels: Mapping[str, Any], name: str) -> Sequence[Any] | None:
    value = channels.get(name)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return value
    return None


def _at(values: Sequence[Any] | None, index: int, default: Any = None) -> Any:
    return values[index] if values is not None and index < len(values) else default


def _lap_fraction(value: Any) -> float | None:
    number = _finite(value)
    if number is None or number < 0:
        return None
    if number > 1.5:
        number /= 100.0
    if not 0.0 <= number <= 1.05:
        return None
    return number % 1.0


def _required_channels() -> dict[str, Any]:
    return {
        "position": ["Lat (decimal degrees)", "Lon (decimal degrees)"],
        "track_progress": ["LapDistPct", "Lap"],
        "tire_age": {
            "minimum": 2,
            "any_of": list(TIRE_ODOMETER_CHANNELS),
            "unit": "m",
        },
        "recommended_filters": ["SessionFlags", "OnPitRoad", "Speed"],
    }


def _limitations() -> list[str]:
    return [
        "Lateral offset is relative to the driver's session-median path, not an official centerline or measured track edge.",
        "Positive offset means left of travel relative to that reference; it maps toward inside/outside only where signed-curvature quality gates pass.",
        "Inside/outside and oval low/high fields describe observed movement direction, not absolute lane position or a recommended groove.",
        "LapDistPct aligns samples around the lap but cannot establish a groove without recorded Lat/Lon position.",
        "Tire odometers measure distance on the current tire set, not continuous wear; wear remains known only at a discrete service reading.",
        "A detected migration is a repeatable path change, not proof that tire wear or setup caused it; traffic, cautions, line choice, and driver intent may contribute.",
    ]


def _unavailable(reason: str, *, available_channels: Sequence[str] = ()) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "unavailable",
        "reason": reason,
        "required_channels": _required_channels(),
        "available_required_channels": list(available_channels),
        "zones": [],
        "performance_claim": {
            "status": "unavailable",
            "best_groove_claimed": False,
            "reason": "No path-performance claim is possible without valid path evidence and controlled pace comparisons.",
        },
        "inside_outside_calibration": {
            "status": "unavailable",
            "calibrated_zone_count": 0,
            "reason": "No valid zone-specific signed-curvature calibration is available.",
        },
        "limitations": _limitations(),
    }


def _unit_ok(unit: Any, accepted: set[str]) -> bool:
    normalized = str(unit or "").strip().casefold().replace(" ", "")
    return not normalized or normalized in accepted


def _project(lat_deg: float, lon_deg: float, lat0: float, lon0: float) -> tuple[float, float]:
    lat_rad = math.radians(lat_deg)
    x = EARTH_RADIUS_M * math.radians(lon_deg - lon0) * math.cos(math.radians(lat0))
    y = EARTH_RADIUS_M * (lat_rad - math.radians(lat0))
    return x, y


def _zone_contains(position: float, start: float, end: float) -> bool:
    if start <= end:
        return start <= position < end
    return position >= start or position < end


def _wrapped_angle_delta(after: float, before: float) -> float:
    """Return the signed shortest heading change in radians."""

    return math.atan2(math.sin(after - before), math.cos(after - before))


def _zone_bin_indices(zone: Mapping[str, Any], bins: int) -> list[int]:
    start = _finite(zone.get("start_pct"))
    end = _finite(zone.get("end_pct"))
    if start is None or end is None or bins < 1:
        return []
    start %= 1.0
    end %= 1.0
    selected = [
        index
        for index in range(bins)
        if _zone_contains((index + 0.5) / bins, start, end)
    ]
    return sorted(
        selected,
        key=lambda index: (((index + 0.5) / bins) - start) % 1.0,
    )


def _authoritative_oval(track_type: Any) -> bool:
    normalized = str(track_type or "").strip().casefold()
    return bool(normalized and "oval" in normalized)


def _zone_curvature_calibration(
    points: Sequence[tuple[float, float]],
    zone: Mapping[str, Any],
    *,
    observed_reference_bins: set[int],
    track_type: Any = None,
) -> dict[str, Any]:
    """Calibrate the local inside normal from stable signed path curvature.

    Track edges are unnecessary for a directional inside/outside statement:
    the center of curvature is left of travel in a left turn and right of
    travel in a right turn.  Strict geometry gates prevent that relationship
    from being applied to straights, chicanes, or poorly observed reference
    segments.
    """

    bins = len(points)
    indices = _zone_bin_indices(zone, bins)
    coverage = (
        sum(index in observed_reference_bins for index in indices) / len(indices)
        if indices
        else 0.0
    )
    headings: list[float] = []
    # A two-bin central chord suppresses sub-bin geographic jitter while
    # retaining more than enough resolution for a 12-degree acceptance gate.
    tangent_radius = 2
    for index in indices:
        before = points[(index - tangent_radius) % bins]
        after = points[(index + tangent_radius) % bins]
        dx, dy = after[0] - before[0], after[1] - before[1]
        if math.hypot(dx, dy) < 0.1:
            continue
        headings.append(math.atan2(dy, dx))
    changes = [
        _wrapped_angle_delta(after, before)
        for before, after in zip(headings, headings[1:])
    ]
    positive_weight = sum(change for change in changes if change > 0.0)
    negative_weight = sum(-change for change in changes if change < 0.0)
    absolute_weight = positive_weight + negative_weight
    signed_change = sum(changes)
    agreement = (
        max(positive_weight, negative_weight) / absolute_weight
        if absolute_weight > 1e-12
        else 0.0
    )
    signed_change_deg = math.degrees(signed_change)
    absolute_change_deg = math.degrees(absolute_weight)

    reasons: list[str] = []
    if len(indices) < MIN_GEOMETRY_BIN_COUNT:
        reasons.append(
            f"fewer than {MIN_GEOMETRY_BIN_COUNT} reference bins in the zone"
        )
    if coverage < MIN_ZONE_REFERENCE_COVERAGE:
        reasons.append(
            f"reference coverage {coverage:.0%} is below {MIN_ZONE_REFERENCE_COVERAGE:.0%}"
        )
    if len(changes) < MIN_GEOMETRY_BIN_COUNT - 1:
        reasons.append("too few stable tangent changes")
    if abs(signed_change_deg) < MIN_ZONE_HEADING_CHANGE_DEG:
        reasons.append(
            f"net heading change {abs(signed_change_deg):.1f} deg is below {MIN_ZONE_HEADING_CHANGE_DEG:.0f} deg"
        )
    if agreement < MIN_CURVATURE_SIGN_AGREEMENT:
        reasons.append(
            f"curvature-sign agreement {agreement:.0%} is below {MIN_CURVATURE_SIGN_AGREEMENT:.0%}"
        )

    calibrated = not reasons
    turn_direction = (
        "left" if calibrated and signed_change > 0.0 else
        "right" if calibrated and signed_change < 0.0 else
        None
    )
    inside_sign = (
        1 if turn_direction == "left" else -1 if turn_direction == "right" else None
    )
    oval = _authoritative_oval(track_type)
    confidence = (
        "high"
        if calibrated
        and coverage >= 0.95
        and agreement >= 0.95
        and abs(signed_change_deg) >= 25.0
        else "medium"
        if calibrated
        else "unavailable"
    )
    return {
        "status": "calibrated" if calibrated else "unavailable",
        "confidence": confidence,
        "method": (
            "signed heading change of two-bin central tangents on the projected "
            "session-reference path"
        ),
        "reference_bin_count": len(indices),
        "observed_reference_bin_count": sum(
            index in observed_reference_bins for index in indices
        ),
        "reference_bin_coverage_fraction": _round(coverage, 4),
        "net_heading_change_deg": _round(signed_change_deg, 2),
        "absolute_heading_change_deg": _round(absolute_change_deg, 2),
        "curvature_sign_agreement_fraction": _round(agreement, 4),
        "travel_turn_direction": turn_direction,
        "inside_lateral_offset_sign": (
            "positive-left-of-travel"
            if inside_sign == 1
            else "negative-right-of-travel"
            if inside_sign == -1
            else None
        ),
        "inside_lateral_offset_sign_value": inside_sign,
        "track_edges_used": False,
        "absolute_lane_position_available": False,
        "track_type": _ascii_track_type(track_type),
        "oval_track_type_confirmed": oval,
        "oval_direction_mapping": (
            {"toward_inside": "toward-low-side", "toward_outside": "toward-high-side"}
            if oval and calibrated
            else None
        ),
        "thresholds": {
            "minimum_reference_bins": MIN_GEOMETRY_BIN_COUNT,
            "minimum_reference_coverage_fraction": MIN_ZONE_REFERENCE_COVERAGE,
            "minimum_net_heading_change_deg": MIN_ZONE_HEADING_CHANGE_DEG,
            "minimum_curvature_sign_agreement_fraction": MIN_CURVATURE_SIGN_AGREEMENT,
        },
        "unavailable_reason": "; ".join(reasons) if reasons else None,
        "claim_scope": (
            "directional movement relative to the session reference path only; "
            "not absolute lane position or groove effectiveness"
        ),
    }


def _ascii_track_type(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    return "".join(character for character in text if character.isascii())[:80] or None


def _path_label(offset_m: float | None, threshold_m: float = 0.35) -> str | None:
    if offset_m is None:
        return None
    if offset_m > threshold_m:
        return "left-of-session-reference"
    if offset_m < -threshold_m:
        return "right-of-session-reference"
    return "near-session-reference"


def _window_summary(items: Sequence[Mapping[str, Any]], track_length_m: float) -> dict[str, Any]:
    offsets = [float(item["offset_m"]) for item in items]
    ages = [float(item["tire_distance_m"]) for item in items]
    median_offset = _median(offsets)
    return {
        "laps": [int(item["lap"]) for item in items],
        "median_lateral_offset_m": _round(median_offset),
        "path": _path_label(median_offset),
        "median_tire_distance_m": _round(_median(ages), 1),
        "median_tire_age_lap_equivalents": _round(
            (_median(ages) or 0.0) / track_length_m, 2
        ),
        "lap_to_lap_offset_iqr_m": _round(
            (_percentile(offsets, 0.75) or 0.0) - (_percentile(offsets, 0.25) or 0.0)
        ),
    }


def _wear_context(run: Mapping[str, Any]) -> dict[str, Any]:
    observation = run.get("tire_observation")
    if not isinstance(observation, Mapping):
        return {
            "status": "unmeasured",
            "note": "No discrete post-run tire reading is available for this run.",
        }
    lowest = _finite(observation.get("lowest_remaining_percent"))
    if lowest is None:
        tire_values: list[float] = []
        for tire in (observation.get("tires") or {}).values():
            if not isinstance(tire, Mapping):
                continue
            value = _finite(tire.get("minimum_remaining_percent"))
            if value is not None:
                tire_values.append(value)
        lowest = min(tire_values) if tire_values else None
    return {
        "status": "measured-at-service" if lowest is not None else "unmeasured",
        "lowest_remaining_percent": _round(lowest, 2),
        "lowest_remaining_tire": observation.get("lowest_remaining_tire"),
        "measurement_scope": (
            "discrete reading assigned to the run end; it does not timestamp when wear occurred"
        ),
    }


def _previous_run_changed_tires(runs: Sequence[Mapping[str, Any]], run_index: int) -> bool:
    if run_index <= 0:
        return False
    service = runs[run_index - 1].get("pit_service")
    if not isinstance(service, Mapping):
        return False
    changed = service.get("tires_changed_observed") or ()
    return len({str(item).upper() for item in changed}) >= 2


def _valid_green_lap_numbers(
    runs: Sequence[Mapping[str, Any]], laps: Sequence[Mapping[str, Any]]
) -> set[int]:
    """Mirror the race analysis' complete racing-state/restart exclusion."""

    lap_by_number = {
        int(number): item
        for item in laps
        if isinstance(item, Mapping)
        and (number := _finite(item.get("lap"))) is not None
    }
    valid: set[int] = set()
    for run in runs:
        if not isinstance(run, Mapping):
            continue
        damage_context = run.get("damage_repair_context")
        if (
            isinstance(damage_context, Mapping)
            and damage_context.get("automatic_coaching_reference_eligible") is False
        ):
            continue
        screened_numbers = {
            int(number)
            for value in run.get("coaching_reference_lap_numbers") or ()
            if (number := _finite(value)) is not None
        }
        previous_flag: str | None = None
        for raw_number in run.get("lap_numbers") or ():
            number = _finite(raw_number)
            if number is None:
                continue
            lap = lap_by_number.get(int(number))
            if lap is None:
                continue
            flag_state = str(lap.get("flag_state") or "")
            racing_fraction = _finite(lap.get("racing_state_fraction"))
            is_restart_lap = previous_flag == "caution" and flag_state == "green"
            if (
                flag_state == "green"
                and lap.get("complete") is True
                and (_finite(lap.get("pit_time_s")) or 0.0) < 1.0
                and (racing_fraction is None or racing_fraction >= 0.98)
                and not is_restart_lap
                and (not screened_numbers or int(number) in screened_numbers)
            ):
                valid.add(int(number))
            previous_flag = flag_state
    return valid


def analyze_groove_evolution(
    channels: Mapping[str, Any],
    *,
    runs: Sequence[Mapping[str, Any]],
    laps: Sequence[Mapping[str, Any]],
    load_zones: Sequence[Mapping[str, Any]],
    channel_units: Mapping[str, str] | None = None,
    track_type: Any = None,
    bins: int = DEFAULT_BINS,
) -> dict[str, Any]:
    """Return bounded, track-relative player-path evolution by load zone."""

    if isinstance(bins, bool) or not isinstance(bins, int) or not 60 <= bins <= 240:
        raise ValueError("bins must be an integer between 60 and 240")
    required_names = ("Lat", "Lon", "LapDistPct", "Lap")
    missing = [name for name in required_names if _series(channels, name) is None]
    if missing:
        return _unavailable(
            "Missing measured position/progress channels: " + ", ".join(missing),
            available_channels=[name for name in required_names if name not in missing],
        )
    units = dict(channel_units or {})
    if not _unit_ok(units.get("Lat"), {"deg", "degree", "degrees"}) or not _unit_ok(
        units.get("Lon"), {"deg", "degree", "degrees"}
    ):
        return _unavailable("Lat/Lon units are not decimal degrees; geographic projection is unsafe.")
    odometer_names = [
        name
        for name in TIRE_ODOMETER_CHANNELS
        if _series(channels, name) is not None
        and _unit_ok(units.get(name), {"m", "meter", "meters"})
    ]
    if len(odometer_names) < 2:
        return _unavailable(
            "At least two meter-valued tire odometer channels are required to compare path against tire distance.",
            available_channels=[*required_names, *odometer_names],
        )
    zones: list[dict[str, Any]] = []
    for raw in list(load_zones)[:MAX_ZONES]:
        if not isinstance(raw, Mapping):
            continue
        start, end = _lap_fraction(raw.get("start_pct")), _lap_fraction(raw.get("end_pct"))
        if start is None or end is None or start == end:
            continue
        zones.append(
            {
                "segment": int(_finite(raw.get("segment")) or len(zones) + 1),
                "start_pct": start,
                "end_pct": end,
                "wraps_start_finish": start > end,
            }
        )
    if not zones:
        return _unavailable("No telemetry-derived corner/load zones were available.")

    valid_green_laps = _valid_green_lap_numbers(runs, laps)
    if not valid_green_laps:
        return _unavailable(
            "No complete racing-state green laps remained after pit, partial-lap, and first-restart-lap exclusions."
        )

    lat_values = _series(channels, "Lat")
    lon_values = _series(channels, "Lon")
    pct_values = _series(channels, "LapDistPct")
    lap_values = _series(channels, "Lap")
    flags = _series(channels, "SessionFlags")
    pit = _series(channels, "OnPitRoad")
    speed = _series(channels, "Speed")
    session_time = _series(channels, "SessionTime")
    tire_series = {name: _series(channels, name) for name in odometer_names}
    length = max(len(lat_values or ()), len(lon_values or ()), len(pct_values or ()))
    stride = max(1, math.ceil(length / MAX_INPUT_SAMPLES))
    raw_rows: list[dict[str, Any]] = []
    for index in range(0, length, stride):
        lat = _finite(_at(lat_values, index))
        lon = _finite(_at(lon_values, index))
        position = _lap_fraction(_at(pct_values, index))
        lap_number = _finite(_at(lap_values, index))
        if (
            lat is None
            or lon is None
            or not -90.0 <= lat <= 90.0
            or not -180.0 <= lon <= 180.0
            or position is None
            or lap_number is None
            or lap_number < 1
            or int(lap_number) not in valid_green_laps
        ):
            continue
        if bool(_at(pit, index, False)):
            continue
        flag_value = int(_finite(_at(flags, index, 0)) or 0)
        if flag_value & CAUTION_FLAGS:
            continue
        speed_value = _finite(_at(speed, index))
        if speed_value is not None and speed_value < 5.0:
            continue
        ages = [
            value
            for name in odometer_names
            if (value := _finite(_at(tire_series[name], index))) is not None and value >= 0
        ]
        if len(ages) < 2:
            continue
        raw_rows.append(
            {
                "index": index,
                "lat": lat,
                "lon": lon,
                "pct": position,
                "lap": int(lap_number),
                "tire_distance_m": statistics.median(ages),
                "session_time_s": _finite(_at(session_time, index)),
            }
        )
    if len(raw_rows) < bins * 2:
        return _unavailable("Too few valid green, non-pit geographic samples to reconstruct a path.")
    reference_laps = sorted({int(row["lap"]) for row in raw_rows})
    if len(reference_laps) < 3:
        return _unavailable("At least three valid green laps are required for a session reference path.")
    lat0 = statistics.median(row["lat"] for row in raw_rows)
    lon0 = statistics.median(row["lon"] for row in raw_rows)
    for row in raw_rows:
        row["x"], row["y"] = _project(row["lat"], row["lon"], lat0, lon0)
    x_values = [row["x"] for row in raw_rows]
    y_values = [row["y"] for row in raw_rows]
    span_m = math.hypot(max(x_values) - min(x_values), max(y_values) - min(y_values))
    if not 80.0 <= span_m <= 30_000.0:
        return _unavailable(
            f"Projected Lat/Lon span ({span_m:.1f} m) is not plausible for one circuit layout."
        )

    by_bin: list[list[tuple[float, float]]] = [[] for _ in range(bins)]
    bin_laps: list[set[int]] = [set() for _ in range(bins)]
    for row in raw_rows:
        bin_index = min(bins - 1, int(row["pct"] * bins))
        by_bin[bin_index].append((row["x"], row["y"]))
        bin_laps[bin_index].add(row["lap"])
    reference: list[tuple[float, float] | None] = []
    for index in range(bins):
        points = by_bin[index]
        reference.append(
            (
                statistics.median(point[0] for point in points),
                statistics.median(point[1] for point in points),
            )
            if len(points) >= 3 and len(bin_laps[index]) >= 2
            else None
        )
    observed_reference_bins = {
        index for index, point in enumerate(reference) if point is not None
    }
    coverage = sum(point is not None for point in reference) / bins
    if coverage < 0.90:
        return _unavailable(
            f"Only {coverage:.1%} of lap-distance bins had a stable multi-lap geographic reference."
        )
    for index, point in enumerate(reference):
        if point is not None:
            continue
        previous = next(
            ((index - distance) % bins for distance in range(1, 5) if reference[(index - distance) % bins] is not None),
            None,
        )
        following = next(
            ((index + distance) % bins for distance in range(1, 5) if reference[(index + distance) % bins] is not None),
            None,
        )
        if previous is None or following is None:
            return _unavailable("A geographic reference-path gap exceeded four lap-distance bins.")
        previous_distance = (index - previous) % bins
        following_distance = (following - index) % bins
        fraction = previous_distance / (previous_distance + following_distance)
        left, right = reference[previous], reference[following]
        assert left is not None and right is not None
        reference[index] = (
            left[0] + (right[0] - left[0]) * fraction,
            left[1] + (right[1] - left[1]) * fraction,
        )
    points = [point for point in reference if point is not None]
    if len(points) != bins:
        return _unavailable("Unable to complete a bounded circular reference path.")
    track_length_m = sum(
        math.hypot(
            points[(index + 1) % bins][0] - points[index][0],
            points[(index + 1) % bins][1] - points[index][1],
        )
        for index in range(bins)
    )
    if not 100.0 <= track_length_m <= 30_000.0:
        return _unavailable(
            f"Reconstructed path length ({track_length_m:.1f} m) is not plausible for one circuit layout."
        )
    zone_calibrations = [
        _zone_curvature_calibration(
            points,
            zone,
            observed_reference_bins=observed_reference_bins,
            track_type=track_type,
        )
        for zone in zones
    ]

    run_by_lap: dict[int, tuple[int, int]] = {}
    bounded_runs = [dict(run) for run in list(runs)[:MAX_RUNS] if isinstance(run, Mapping)]
    for run_index, run in enumerate(bounded_runs):
        run_number = int(_finite(run.get("run_number")) or run_index + 1)
        for value in run.get("lap_numbers") or ():
            lap_number = _finite(value)
            if lap_number is not None:
                run_by_lap[int(lap_number)] = (run_index, run_number)

    aggregates: dict[tuple[int, int, int], dict[str, list[float]]] = defaultdict(
        lambda: {"offsets": [], "ages": [], "times": []}
    )
    rejected_offset_samples = 0
    for row in raw_rows:
        lap_number = int(row["lap"])
        if lap_number not in valid_green_laps or lap_number not in run_by_lap:
            continue
        q = row["pct"] * bins - 0.5
        left_index = math.floor(q) % bins
        fraction = q - math.floor(q)
        right_index = (left_index + 1) % bins
        previous_index = (left_index - 1) % bins
        next_index = (right_index + 1) % bins
        left_point, right_point = points[left_index], points[right_index]
        ref_x = left_point[0] + (right_point[0] - left_point[0]) * fraction
        ref_y = left_point[1] + (right_point[1] - left_point[1]) * fraction
        tangent_x = points[next_index][0] - points[previous_index][0]
        tangent_y = points[next_index][1] - points[previous_index][1]
        magnitude = math.hypot(tangent_x, tangent_y)
        if magnitude < 0.1:
            continue
        left_normal_x, left_normal_y = -tangent_y / magnitude, tangent_x / magnitude
        lateral_offset = (
            (row["x"] - ref_x) * left_normal_x
            + (row["y"] - ref_y) * left_normal_y
        )
        if abs(lateral_offset) > 30.0:
            rejected_offset_samples += 1
            continue
        run_index, _run_number = run_by_lap[lap_number]
        for zone_index, zone in enumerate(zones):
            if not _zone_contains(row["pct"], zone["start_pct"], zone["end_pct"]):
                continue
            bucket = aggregates[(zone_index, run_index, lap_number)]
            if len(bucket["offsets"]) >= MAX_SAMPLES_PER_LAP_ZONE:
                continue
            bucket["offsets"].append(lateral_offset)
            bucket["ages"].append(row["tire_distance_m"])
            if row["session_time_s"] is not None:
                bucket["times"].append(row["session_time_s"])

    output_zones: list[dict[str, Any]] = []
    comparison_count = 0
    migration_count = 0
    for zone_index, zone in enumerate(zones):
        zone_calibration = zone_calibrations[zone_index]
        zone_runs: list[dict[str, Any]] = []
        for run_index, run in enumerate(bounded_runs):
            summaries: list[dict[str, Any]] = []
            for lap_number in sorted(
                lap for (candidate_zone, candidate_run, lap) in aggregates
                if candidate_zone == zone_index and candidate_run == run_index
            ):
                bucket = aggregates[(zone_index, run_index, lap_number)]
                if len(bucket["offsets"]) < 5 or len(bucket["ages"]) < 5:
                    continue
                summaries.append(
                    {
                        "lap": lap_number,
                        "offset_m": statistics.median(bucket["offsets"]),
                        "tire_distance_m": statistics.median(bucket["ages"]),
                        "session_time_s": _median(bucket["times"]),
                        "sample_count": len(bucket["offsets"]),
                    }
                )
            if len(summaries) < 4:
                continue
            early = summaries[:2]
            late = summaries[-2:]
            early_window = _window_summary(early, track_length_m)
            late_window = _window_summary(late, track_length_m)
            minimum_age_laps = min(item["tire_distance_m"] for item in summaries) / track_length_m
            fresh_available = minimum_age_laps <= 1.5
            fresh_status = (
                "confirmed-after-observed-tire-change"
                if fresh_available and _previous_run_changed_tires(bounded_runs, run_index)
                else "low-recorded-distance-initial-history-unknown"
                if fresh_available and run_index == 0
                else "low-recorded-distance-without-prior-change-confirmation"
                if fresh_available
                else "not-observed"
            )
            age_separation = (
                float(late_window["median_tire_age_lap_equivalents"])
                - float(early_window["median_tire_age_lap_equivalents"])
            )
            offsets_early = [float(item["offset_m"]) for item in early]
            offsets_late = [float(item["offset_m"]) for item in late]
            early_median = float(_median(offsets_early) or 0.0)
            late_median = float(_median(offsets_late) or 0.0)
            delta = late_median - early_median
            within_window_deviations = [
                abs(value - median)
                for values, median in (
                    (offsets_early, early_median),
                    (offsets_late, late_median),
                )
                for value in values
            ]
            robust_noise = 1.4826 * float(_median(within_window_deviations) or 0.0)
            threshold = max(0.35, 2.5 * robust_noise)
            enough_age_change = age_separation >= 2.0
            detected = bool(enough_age_change and abs(delta) >= threshold)
            first_migration: dict[str, Any] | None = None
            if detected:
                direction_sign = 1.0 if delta > 0 else -1.0
                candidates = [item for item in summaries if item["lap"] > early[-1]["lap"]]
                for index, item in enumerate(candidates):
                    shifted = (item["offset_m"] - early_median) * direction_sign >= threshold
                    next_shifted = (
                        index + 1 < len(candidates)
                        and (candidates[index + 1]["offset_m"] - early_median) * direction_sign >= threshold
                    )
                    if shifted and next_shifted:
                        first_migration = item
                        break
                if first_migration is None:
                    first_migration = late[0]
                migration_count += 1
            inside_outside_direction: str | None = None
            oval_low_high_direction: str | None = None
            display_direction: str | None = None
            inside_sign = _finite(
                zone_calibration.get("inside_lateral_offset_sign_value")
            )
            if (
                detected
                and zone_calibration.get("status") == "calibrated"
                and inside_sign in (-1.0, 1.0)
            ):
                moved_toward_inside = delta * inside_sign > 0.0
                inside_outside_direction = (
                    "toward-inside" if moved_toward_inside else "toward-outside"
                )
                if zone_calibration.get("oval_track_type_confirmed"):
                    oval_low_high_direction = (
                        "toward-low-side"
                        if moved_toward_inside
                        else "toward-high-side"
                    )
                    display_direction = (
                        f"{oval_low_high_direction} "
                        f"({'inside' if moved_toward_inside else 'outside'})"
                    )
                else:
                    display_direction = inside_outside_direction
            comparison_count += 1
            wear = _wear_context(run)
            zone_runs.append(
                {
                    "run_number": int(_finite(run.get("run_number")) or run_index + 1),
                    "green_laps_analyzed": len(summaries),
                    "early_tire_window": early_window,
                    "fresh_tire_window": early_window if fresh_available else None,
                    "fresh_tire_status": fresh_status,
                    "late_tire_window": late_window,
                    "late_tire_wear_context": wear,
                    "fresh_vs_late_available": bool(fresh_available and enough_age_change),
                    "comparison_basis": (
                        "fresh-recorded-distance-vs-late-on-set"
                        if fresh_available
                        else "early-vs-late-on-used-set; no fresh window observed"
                    ),
                    "lateral_delta_late_minus_early_m": _round(delta),
                    "tire_age_separation_lap_equivalents": _round(age_separation, 2),
                    "migration": {
                        "status": "detected" if detected else "not-detected",
                        "claim_scope": "observed relative-line movement only",
                        "traffic_and_clean_air_screened": False,
                        "direction": (
                            "left-relative-to-session-reference"
                            if detected and delta > 0
                            else "right-relative-to-session-reference"
                            if detected
                            else None
                        ),
                        "inside_outside_calibration_status": zone_calibration.get(
                            "status"
                        ),
                        "inside_outside_direction": inside_outside_direction,
                        "oval_low_high_direction": oval_low_high_direction,
                        "display_direction": display_direction,
                        "effectiveness_status": "unavailable",
                        "recommendation_emitted": False,
                        "threshold_m": _round(threshold),
                        "first_sustained_lap": first_migration.get("lap") if first_migration else None,
                        "first_sustained_session_time_s": _round(
                            first_migration.get("session_time_s") if first_migration else None
                        ),
                        "first_sustained_tire_age_lap_equivalents": _round(
                            first_migration["tire_distance_m"] / track_length_m
                            if first_migration else None,
                            2,
                        ),
                        "method": "two-lap sustained displacement from the early-window median; fallback to late-window boundary when persistence is unavailable",
                    },
                    "lap_path_samples": [
                        {
                            "lap": item["lap"],
                            "lateral_offset_m": _round(item["offset_m"]),
                            "path": _path_label(item["offset_m"]),
                            "tire_distance_m": _round(item["tire_distance_m"], 1),
                            "tire_age_lap_equivalents": _round(
                                item["tire_distance_m"] / track_length_m, 2
                            ),
                        }
                        for item in summaries[:60]
                    ],
                }
            )
        output_zones.append(
            {
                **zone,
                "inside_outside_calibration": zone_calibration,
                "runs": zone_runs,
            }
        )

    calibrated_zone_count = sum(
        item.get("status") == "calibrated" for item in zone_calibrations
    )
    calibration_status = (
        "calibrated"
        if zone_calibrations and calibrated_zone_count == len(zone_calibrations)
        else "partially-calibrated"
        if calibrated_zone_count
        else "unavailable"
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "available" if comparison_count else "insufficient-comparable-laps",
        "coordinate_evidence": {
            "channels": ["Lat", "Lon", "LapDistPct", "Lap", *odometer_names],
            "lat_lon_units": "decimal degrees",
            "projection": "local equirectangular projection about session median Lat/Lon",
            "origin_lat_deg": round(lat0, 7),
            "origin_lon_deg": round(lon0, 7),
            "reference": "driver session-median x/y path by lap-distance bin",
            "lateral_sign": "positive is left of travel relative to the reference path",
            "inside_outside_sign_calibrated": calibrated_zone_count > 0,
            "inside_outside_calibration_scope": "zone-specific signed reference-path curvature",
            "absolute_groove_claimed": False,
            "absolute_low_middle_high_lane_claimed": False,
        },
        "inside_outside_calibration": {
            "status": calibration_status,
            "calibrated_zone_count": calibrated_zone_count,
            "zone_count": len(zone_calibrations),
            "track_type": _ascii_track_type(track_type),
            "oval_track_type_confirmed": _authoritative_oval(track_type),
            "method": "zone-specific signed reference-path curvature with strict heading, sign-agreement, and coverage gates",
            "absolute_lane_position_available": False,
        },
        "reference_quality": {
            "bins": bins,
            "bin_coverage_fraction": round(coverage, 4),
            "reference_lap_count": len(reference_laps),
            "valid_green_lap_count": len(valid_green_laps),
            "lap_exclusion_rule": (
                "complete green laps only; pit-time >=1 s, racing-state fraction <0.98, partial laps, and the first green lap after a caution are excluded"
            ),
            "projected_span_m": round(span_m, 1),
            "reconstructed_path_length_m": round(track_length_m, 1),
            "input_sample_count": length,
            "processed_sample_count": len(raw_rows),
            "sample_stride": stride,
            "rejected_offset_samples": rejected_offset_samples,
            "bounded": True,
        },
        "summary": {
            "zone_count": len(output_zones),
            "run_zone_comparison_count": comparison_count,
            "detected_migration_count": migration_count,
            "inside_outside_calibrated_zone_count": calibrated_zone_count,
        },
        "performance_claim": {
            "status": "unavailable",
            "best_groove_claimed": False,
            "reason": (
                "This pass measures the driver's path evolution only. It does not establish which groove works best because traffic, clean-air state, fuel, tire age, and driver intent are not controlled here."
            ),
        },
        "required_channels": _required_channels(),
        "zones": output_zones,
        "limitations": _limitations(),
    }


__all__ = ["analyze_groove_evolution"]

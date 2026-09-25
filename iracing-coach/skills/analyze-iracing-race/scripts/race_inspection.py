"""Compact race-investigation projections consumed by the application.

Generalized from the September 2026 Bristol design study. Exposure, observed
trends, screened fits, review sections and synchronized pass context are
deterministic backend evidence; the application only presents and selects it.

Nothing here infers tire wear, grip, damage or a cause. Traffic-affected laps
stay visible as observations; they never become clean references.
"""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from bisect import bisect_left
from pathlib import Path
from typing import Any, Mapping, Sequence

try:  # Package import and direct script loading are both supported.
    from .race_investigation import _corner_lap, _crossing, _metric, _number, _path, _round
    from .steering_basis import SteeringBasis, pair_descriptor
except ImportError:  # pragma: no cover - normal CLI/MCP script-loading path.
    from race_investigation import _corner_lap, _crossing, _metric, _number, _path, _round
    from steering_basis import SteeringBasis, pair_descriptor


SECTION_CATALOG = Path(__file__).resolve().parent / "review_sections.v1.json"
TREND_WINDOW = 5
MINIMUM_TREND_OBSERVATIONS = 3
# A pool whose extremes are compared must be at similar run exposure. Wider
# pools use each cohort's median pass instead of calling its extremes comparable.
MATCHED_POOL_SPAN = 3.0
MAX_SECTIONS = 12
# Trace bins are about 0.6% lap; a larger step is missing coverage, not a straight.
MAX_POINT_STEP = 0.03
# A pass pair's approach is the lead-in to its section, not the rest of the lap:
# as long as the section itself, clamped to 3-8% of a lap, and never reaching
# back past the previous section's start. IRP's detected section F (64.5-72.5%)
# was once timed from 30.5%, so "Entry" covered a third of the lap and the
# section filled only the right fifth of every chart.
APPROACH_MIN_FRACTION = 0.03
APPROACH_MAX_FRACTION = 0.08
MAX_POINT_SECONDS = 2.0
CHANNELS = (
    ("speed", ("speed_mph",), 1.0, 1),
    ("throttle", ("throttle",), 100.0, 1),
    ("brake", ("brake_mean", "brake"), 100.0, 1),
    # Degrees in the recording's steering basis; a pair declares it under
    # "steering". Road-wheel angles are about a twelfth of wheel angles.
    ("steering", ("steering_rad",), 180.0 / math.pi, 2),
    ("slip", ("slip_angle_deg",), 1.0, 2),
)
SECTION_METRICS = (
    ("elapsed", "Section time", "s", 1.0),
    ("entry_speed", "Entry speed", "mph", 1.0),
    ("minimum_speed", "Minimum speed", "mph", 1.0),
    ("exit_speed", "Exit speed", "mph", 1.0),
    ("brake", "Brake demand", "%", 100.0),
    ("throttle", "Throttle demand", "%", 100.0),
    # The label is the recording's steering basis (see comparison_sections).
    ("steering", "Steering", "deg", 180.0 / math.pi),
    ("brake_release", "Brake release", "% lap", 1.0),
    ("throttle_pickup", "Throttle pickup", "% lap", 1.0),
)


def configuration_key(analysis: Mapping[str, Any]) -> str | None:
    """Exact track configuration identity; a display string is never a key."""
    key = _path(analysis, "track_geometry", "track_configuration_key")
    if isinstance(key, str) and key.strip():
        return key.strip()
    identity = analysis.get("identity") or {}
    track_id = _number(identity.get("track_id"))
    config = identity.get("track_config")
    if track_id is None or not isinstance(config, str) or not config.strip():
        return None
    slug = "-".join("".join(ch.lower() if ch.isalnum() else " " for ch in config).split())
    return f"{int(track_id)}-{slug}" if slug else None


def _valid_bounds(start: Any, end: Any) -> tuple[float, float] | None:
    low, high = _number(start), _number(end)
    if low is None or high is None or not 0 <= low < high <= 1:
        return None
    return low, high


def _catalog_record(key: str | None, catalog_path: Path = SECTION_CATALOG) -> Mapping[str, Any] | None:
    if key is None:
        return None
    try:
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if catalog.get("schema_version") != 1:
        return None
    return next((record for record in catalog.get("records") or ()
                 if isinstance(record, Mapping) and record.get("track_configuration_key") == key), None)


def review_sections(analysis: Mapping[str, Any], *, catalog_path: Path = SECTION_CATALOG) -> dict[str, Any]:
    """Sections with concrete bounds, provenance and a version for archiving.

    A catalog record for the exact configuration wins so separate recordings
    are measured over identical distances. Otherwise this recording's detected
    regions are used with neutral letters. Wrapping or overlapping bounds are
    rejected rather than repaired.
    """
    key = configuration_key(analysis)
    record = _catalog_record(key, catalog_path)
    sections: list[dict[str, Any]] = []
    if record is not None:
        for item in record.get("sections") or ():
            bounds = _valid_bounds(item.get("start_pct"), item.get("end_pct"))
            if bounds is None or not item.get("id"):
                continue
            section = {"id": str(item["id"]), "label": str(item.get("label") or item["id"]),
                       "start_pct": bounds[0], "end_pct": bounds[1],
                       "boundary_status": str(item.get("boundary_status") or "detected_section"),
                       "provenance": str(item.get("provenance") or "")}
            # A driver-readable name ("Turns 1-2 entry") travels beside the
            # short label; it is descriptive, never an official turn number.
            if isinstance(item.get("name"), str) and item["name"].strip():
                section["name"] = item["name"].strip()
            sections.append(section)
        source, version = "configuration_catalog", str(record.get("version") or "unversioned")
        provenance = str(record.get("provenance") or "")
    else:
        detected = []
        for segment in _path(analysis, "track_profile", "detected_corner_segments") or ():
            if not isinstance(segment, Mapping) or segment.get("wraps_start_finish"):
                continue
            bounds = _valid_bounds(segment.get("start_pct"), segment.get("end_pct"))
            if bounds is not None:
                detected.append(bounds)
        detected.sort()
        for index, (start, end) in enumerate(detected[:MAX_SECTIONS]):
            label = chr(ord("A") + index)
            sections.append({"id": label, "label": label, "start_pct": start, "end_pct": end,
                             "boundary_status": "detected_section",
                             "provenance": f"Detected region {index + 1} in this recording; not a verified turn boundary."})
        digest = hashlib.sha256(json.dumps([[s["start_pct"], s["end_pct"]] for s in sections]).encode("utf-8")).hexdigest()[:12]
        source, version = "detected_in_recording", f"detected/{digest}"
        provenance = "Regions detected from steering and lateral load in this recording. Letters are analysis labels, not turn numbers."
    sections.sort(key=lambda item: item["start_pct"])
    for left, right in zip(sections, sections[1:]):
        if right["start_pct"] < left["end_pct"]:
            return {"configuration_key": key, "source": source, "version": version, "provenance": provenance,
                    "sections": [], "unavailable_reason": "Review sections overlap; no section comparison is shown."}
    return {"configuration_key": key, "source": source, "version": version, "provenance": provenance,
            "sections": sections[:MAX_SECTIONS],
            "unavailable_reason": None if sections else "No usable track sections were detected in this recording."}


def run_exposure(laps: Sequence[Mapping[str, Any]], runs: Sequence[Mapping[str, Any]]) -> dict[int, float | None]:
    """Run-relative sampled green-lap equivalents for each lap, never tire age."""
    try:
        from .race_investigation import _run_exposure
    except ImportError:  # pragma: no cover
        from race_investigation import _run_exposure
    result: dict[int, float | None] = {}
    for run in runs:
        start, end = _number(run.get("start_lap")), _number(run.get("end_lap"))
        if start is None or end is None:
            continue
        result.update(_run_exposure([lap for lap in laps if start <= lap["lap"] <= end]))
    return result


def _gap_reason(between: Sequence[Mapping[str, Any]]) -> str:
    if any(lap.get("flag_state") == "caution" for lap in between):
        return "caution"
    if any((_number(lap.get("pit_time_s")) or 0) >= 1 for lap in between):
        return "service"
    if between:
        return "not_plotted"
    return "exposure_discontinuity"


def run_views(points: Sequence[Mapping[str, Any]], laps: Sequence[Mapping[str, Any]] = ()) -> list[dict[str, Any]]:
    """Observed trends and screened fits per run on the green-exposure axis.

    `points` are plotted laps with `lap`, `run_number`, `lap_time_s`, `eligible`
    and `green_exposure`. The observed trend is a centered five-observation
    median (three or four at sequence edges) that includes reference
    exclusions: it describes what happened, not clean-air potential. It never
    invents a lap-zero time and never bridges missing laps or exposure breaks.
    The screened least-squares fit uses eligible laps only and is confined to
    the exposure domain of those laps.
    """
    try:
        from .analysis_engine import _linear_slope
    except ImportError:  # pragma: no cover
        from analysis_engine import _linear_slope
    by_lap = {int(lap["lap"]): lap for lap in laps if _number(lap.get("lap")) is not None}
    result = []
    for run in sorted({p["run_number"] for p in points if p.get("run_number") is not None}):
        selected = sorted((p for p in points if p.get("run_number") == run
                           and _number(p.get("green_exposure")) is not None
                           and (_number(p.get("lap_time_s")) or 0) > 0), key=lambda p: p["lap"])
        chunks: list[list[Mapping[str, Any]]] = []
        gaps = []
        for point in selected:
            previous = chunks[-1][-1] if chunks else None
            if (previous is None or point["lap"] != previous["lap"] + 1
                    or not 0 < point["green_exposure"] - previous["green_exposure"] <= 1.5):
                if previous is not None:
                    between = [by_lap[n] for n in range(previous["lap"] + 1, point["lap"]) if n in by_lap]
                    gaps.append({"after_lap": previous["lap"], "before_lap": point["lap"], "reason": _gap_reason(between)})
                chunks.append([])
            chunks[-1].append(point)
        segments = []
        for chunk in chunks:
            if len(chunk) < MINIMUM_TREND_OBSERVATIONS:
                continue
            half = TREND_WINDOW // 2
            segments.append([{
                "lap": point["lap"], "x": _round(point["green_exposure"], 3),
                "y": _round(statistics.median(p["lap_time_s"] for p in chunk[max(0, i - half):i + half + 1]), 3),
                "sample_laps": [p["lap"] for p in chunk[max(0, i - half):i + half + 1]],
            } for i, point in enumerate(chunk)])
        eligible = [p for p in selected if p.get("eligible") is True]
        pairs = [(float(p["green_exposure"]), float(p["lap_time_s"])) for p in eligible]
        slope = _linear_slope(pairs) if len(pairs) >= MINIMUM_TREND_OBSERVATIONS else None
        fit = None
        if slope is not None:
            intercept = statistics.fmean(y for _, y in pairs) - slope * statistics.fmean(x for x, _ in pairs)
            low, high = min(x for x, _ in pairs), max(x for x, _ in pairs)
            fit = {"method": "ordinary_least_squares", "eligible_count": len(pairs),
                   "laps": [p["lap"] for p in eligible], "slope_s_per_green_lap": _round(slope, 5),
                   "domain": [_round(low, 3), _round(high, 3)],
                   "points": [{"x": _round(x, 3), "y": _round(intercept + slope * x, 3)} for x in (low, high)]}
        plotted = [p["lap"] for p in selected]
        result.append({
            "run_number": run, "plotted_lap_count": len(plotted),
            "first_lap": plotted[0] if plotted else None, "last_lap": plotted[-1] if plotted else None,
            "trend": {"method": "centered_five_observation_median", "includes_reference_exclusions": True,
                      "covered_lap_count": sum(len(segment) for segment in segments),
                      "segments": segments, "gaps": gaps},
            "fit": fit,
            "fit_unavailable_reason": None if fit else "Fewer than three screened laps with known exposure.",
        })
    return result


def _points(trace: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    points = [p for p in (trace or {}).get("points") or () if isinstance(p, Mapping) and _number(p.get("lap_pct")) is not None]
    # Never sort corrupt or reversed telemetry into a plausible drive.
    if any(a["lap_pct"] >= b["lap_pct"] for a, b in zip(points, points[1:])):
        return []
    return points


def _covered(points: Sequence[Mapping[str, Any]], start: float, end: float) -> bool:
    if _crossing(points, start) is None or _crossing(points, end) is None:
        return False
    for left, right in zip(points, points[1:]):
        if right["lap_pct"] <= start or left["lap_pct"] >= end:
            continue
        t0, t1 = _number(left.get("session_time_s")), _number(right.get("session_time_s"))
        if t0 is None or t1 is None or not 0 < t1 - t0 <= MAX_POINT_SECONDS or right["lap_pct"] - left["lap_pct"] > MAX_POINT_STEP:
            return False
    return True


def _value(point: Mapping[str, Any], keys: Sequence[str]) -> float | None:
    for key in keys:
        value = _number(point.get(key))
        if value is not None:
            return value
    return None


def _sample(points: Sequence[Mapping[str, Any]], x: float, keys: Sequence[str]) -> float | None:
    positions = [p["lap_pct"] for p in points]
    i = bisect_left(positions, x)
    if i < len(points) and abs(positions[i] - x) < 1e-9:
        return _value(points[i], keys)
    if i == 0 or i >= len(points):
        return None
    left, right = points[i - 1], points[i]
    if not _covered([left, right], left["lap_pct"], right["lap_pct"]):
        return None
    a, b = _value(left, keys), _value(right, keys)
    if a is None or b is None:
        return None
    return a + (b - a) * (x - left["lap_pct"]) / (right["lap_pct"] - left["lap_pct"])


def section_elapsed(trace: Mapping[str, Any] | None, start: float, end: float) -> float | None:
    """Recorded time across a section, withheld when coverage has a gap."""
    points = _points(trace)
    if not _covered(points, start, end):
        return None
    entered, exited = _crossing(points, start), _crossing(points, end)
    return exited - entered if entered is not None and exited is not None and exited > entered else None


def comparison_sections(baseline: Sequence[int], compared: Sequence[int], traces: Mapping[int, Mapping[str, Any]],
                        sections: Sequence[Mapping[str, Any]], *, steering: SteeringBasis | None = None) -> list[dict[str, Any]]:
    """Group-median section metrics over explicit bounds.

    Merged sections are recomputed across their full interval, including the
    distance between the former regions; medians are never added together.
    Each cohort needs two covered laps; timing gaps disqualify a lap.
    Steering is the traces' basis, named by ``steering``.
    """
    steering_label = (steering or SteeringBasis.from_descriptor(None)).short_label
    result = []
    for section in sections:
        start, end = section["start_pct"], section["end_pct"]
        def measured(laps: Sequence[int]) -> list[dict[str, Any]]:
            values = []
            for lap in laps:
                trace = traces.get(lap)
                if not _covered(_points(trace), start, end):
                    continue
                metric = _corner_lap(trace, start, end)
                if metric is not None:
                    values.append(metric)
            return values
        first, second = measured(baseline), measured(compared)
        if len(first) < 2 or len(second) < 2:
            continue
        metrics = []
        for key, label, unit, multiplier in SECTION_METRICS:
            one = [m[key] for m in first if m[key] is not None]
            two = [m[key] for m in second if m[key] is not None]
            if len(one) < 2 or len(two) < 2:
                continue
            if key == "steering":
                label = steering_label
            metrics.append(_metric(key, label, unit, statistics.median(one) * multiplier, statistics.median(two) * multiplier,
                                   f"{len(one)} earlier and {len(two)} later laps; distance-binned traces."))
        elapsed = next((metric for metric in metrics if metric["key"] == "elapsed"), {})
        name = section.get("name")
        title = f"Section {section['label']} · {name}" if name else f"Section {section['label']}"
        item = {
            "id": section["id"],
            "label": f"{title} ({start * 100:.1f}–{end * 100:.1f}% lap)",
            "short_label": section["label"], "start_pct": start, "end_pct": end,
            "boundary_status": section["boundary_status"], "delta_s": elapsed.get("delta"),
            "baseline_count": len(first), "compared_count": len(second), "metrics": metrics,
        }
        if name:
            item["name"] = name
        result.append(item)
    return sorted(result, key=lambda item: item["delta_s"] if item["delta_s"] is not None else -math.inf, reverse=True)


def _pass(lap: int, seconds: float, role: str, lap_record: Mapping[str, Any] | None, exposure: float | None,
          exclusions: Sequence[str]) -> dict[str, Any]:
    fuel = _number(_path(lap_record or {}, "fuel", "start_l"))
    return {
        "lap": lap, "role": role, "section_s": _round(seconds, 3),
        "run_number": None, "green_exposure": _round(exposure, 2),
        "eligible": not exclusions, "exclusion_reasons": list(exclusions),
        "fuel_start_gal": _round(fuel / 3.785411784, 2) if fuel is not None else None,
        "track_temp_f": _round(_path(lap_record or {}, "conditions", "track_temperature_f"), 1),
    }


def _pair_rows(first: Sequence[Mapping[str, Any]], second: Sequence[Mapping[str, Any]], context_start: float,
               start: float, end: float, timing_valid: bool) -> list[dict[str, Any]]:
    grid = sorted({round(context_start, 6), round(start, 6), round(end, 6)} | {round(p["lap_pct"], 6) for points in (first, second)
                  for p in points if context_start <= p["lap_pct"] <= end})
    origins = [_crossing(first, context_start), _crossing(second, context_start)]
    rows = []
    for x in grid:
        row: dict[str, Any] = {"x": round(x, 6)}
        for name, points in (("a", first), ("b", second)):
            row[name] = {}
            for key, sources, multiplier, digits in CHANNELS:
                value = _sample(points, x, sources)
                row[name][key] = round(value * multiplier, digits) if value is not None else None
        times = [_crossing(first, x), _crossing(second, x)]
        row["gap"] = (round((times[1] - origins[1]) - (times[0] - origins[0]), 4)
                      if timing_valid and None not in times + origins else None)
        rows.append(row)
    return rows


def _slip_summary(points: Sequence[Mapping[str, Any]], start: float, end: float) -> dict[str, Any]:
    core = [p for p in points if start <= p["lap_pct"] <= end]
    values = [abs(v) for p in core if (v := _number(p.get("slip_angle_deg"))) is not None]
    coverage = len(values) / len(core) if core else 0.0
    summarized = len(values) >= 2 and coverage >= 0.8
    return {"coverage": round(coverage, 3),
            "median_abs": _round(statistics.median(values), 3) if summarized else None,
            "peak_abs": _round(max(values), 3) if summarized else None}


def approach_start(ordered: Sequence[Mapping[str, Any]], index: int, recorded_start: float) -> tuple[float, str]:
    """Where a section's approach begins, and how the application names it.

    The lead-in is the section's own length clamped to 3-8% of a lap. It never
    reaches back past the previous section's start, or before the first
    recorded sample of either pass for the first section.
    """
    section = ordered[index]
    start = float(section["start_pct"])
    lead = min(APPROACH_MAX_FRACTION, max(APPROACH_MIN_FRACTION, float(section["end_pct"]) - start))
    floor = float(ordered[index - 1]["start_pct"]) if index else recorded_start
    context = round(max(floor, start - lead), 6)
    if context <= floor:
        label = f"From section {ordered[index - 1]['label']}" if index else "From the recorded lap start"
    else:
        label = f"Lead-in: {(start - context) * 100:.1f}% lap before the section"
    return context, label


def comparison_inspection(comparison: Mapping[str, Any], laps: Mapping[int, Mapping[str, Any]],
                          exposure: Mapping[int, float | None], run_for_lap: Mapping[int, int],
                          exclusions: Mapping[int, Sequence[str]], traces: Mapping[int, Mapping[str, Any]],
                          sections: Sequence[Mapping[str, Any]], *,
                          steering: SteeringBasis | None = None) -> dict[str, Any]:
    """Two actual passes per section, with their bounded approach (approach_start).

    A matched pool (all laps screened and within three green-lap equivalents)
    compares its fastest and slowest actual section passes; both may come from
    one run. Otherwise each cohort contributes its median actual pass so traffic-
    affected laps are inspected as what happened, not as references. The time
    gap starts at zero at `context_start`; it locates where separation
    accumulated and does not apportion the physical cause.
    """
    baseline = list(comparison["baseline"]["lap_numbers"])
    compared = list(comparison["compared"]["lap_numbers"])
    pool = baseline + [lap for lap in compared if lap not in baseline]
    ages = [exposure.get(lap) for lap in pool]
    matched = (bool(pool) and all(not exclusions.get(lap, ["eligibility_unknown"]) for lap in pool)
               and all(age is not None for age in ages) and max(ages) - min(ages) <= MATCHED_POOL_SPAN)
    ordered = sorted(sections, key=lambda item: item["start_pct"])
    # Both passes come from this recording, so their steering shares its basis.
    steering_pair = pair_descriptor(steering or SteeringBasis.from_descriptor(None))
    result: dict[str, Any] = {
        "mode": "matched_pool_extremes" if matched else "cohort_median_passes",
        "pool_laps": pool,
        "pool_exposure": [_round(min(ages), 2), _round(max(ages), 2)] if ages and all(a is not None for a in ages) else None,
        "sections": {},
    }
    for index, section in enumerate(ordered):
        start, end = section["start_pct"], section["end_pct"]
        def ranked(numbers: Sequence[int]) -> list[tuple[float, int]]:
            values = []
            for lap in numbers:
                seconds = section_elapsed(traces.get(lap), start, end)
                if seconds is not None:
                    values.append((seconds, lap))
            return sorted(values)
        if matched:
            pool_ranked = ranked(pool)
            if len(pool_ranked) < 2 or pool_ranked[-1][0] - pool_ranked[0][0] < 1e-9:
                continue
            (first_s, first_lap), (second_s, second_lap) = pool_ranked[0], pool_ranked[-1]
            roles = ("fastest", "slowest")
            candidate_count = len(pool_ranked)
        else:
            one, two = ranked(baseline), ranked(compared)
            if not one or not two:
                continue
            first_s, first_lap = one[(len(one) - 1) // 2]
            second_s, second_lap = two[(len(two) - 1) // 2]
            if first_lap == second_lap:
                continue
            roles = ("baseline_median", "compared_median")
            candidate_count = len(one) + len(two)
        first_points, second_points = _points(traces.get(first_lap)), _points(traces.get(second_lap))
        context_start, context_label = approach_start(ordered, index, max(first_points[0]["lap_pct"], second_points[0]["lap_pct"]))
        if context_start >= start:
            continue
        timing_valid = _covered(first_points, context_start, end) and _covered(second_points, context_start, end)
        rows = _pair_rows(first_points, second_points, context_start, start, end, timing_valid)
        entry = next(row for row in rows if abs(row["x"] - start) < 1e-9)
        first_pass = _pass(first_lap, first_s, roles[0], laps.get(first_lap), exposure.get(first_lap), exclusions.get(first_lap, ["eligibility_unknown"]))
        second_pass = _pass(second_lap, second_s, roles[1], laps.get(second_lap), exposure.get(second_lap), exclusions.get(second_lap, ["eligibility_unknown"]))
        first_pass["run_number"], second_pass["run_number"] = run_for_lap.get(first_lap), run_for_lap.get(second_lap)
        entry_speeds = (entry["a"]["speed"], entry["b"]["speed"])
        result["sections"][section["id"]] = {
            "section_id": section["id"], "start_pct": start, "end_pct": end,
            "context_start_pct": context_start,
            "context_label": context_label,
            "timing_complete": timing_valid, "candidate_count": candidate_count,
            "a": first_pass, "b": second_pass,
            "section_delta_s": _round(second_s - first_s, 3),
            "entry_gap_s": _round(entry["gap"], 3), "exit_gap_s": _round(rows[-1]["gap"], 3),
            "entry_speed_difference_mph": _round(entry_speeds[1] - entry_speeds[0], 1) if None not in entry_speeds else None,
            "slip": {"basis": "chassis_velocity_angle", "unit": "deg",
                     "a": _slip_summary(first_points, start, end), "b": _slip_summary(second_points, start, end)},
            "steering": dict(steering_pair),
            "rows": rows,
        }
    return result

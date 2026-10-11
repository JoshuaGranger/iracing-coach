"""Deterministic race investigations assembled from existing analysis evidence.

This module does not infer hidden tire condition, grip, damage, or attention.
Observed late laps remain visible even when they cannot support clean coaching.
"""

from __future__ import annotations

import math
import statistics
from typing import Any, Mapping, Sequence

try:  # Package import and direct script loading are both supported.
    from .steering_basis import SteeringBasis, basis_of, pair_descriptor
except ImportError:  # pragma: no cover - normal CLI/MCP script-loading path.
    from steering_basis import SteeringBasis, basis_of, pair_descriptor


SCHEMA_VERSION = 2
# Bump when any projection below changes; cached analyses rebuild it without decoding.
# v7: steering is road-wheel angle when the recording states its steering ratio
# (labelled steering-wheel angle otherwise), with steering thresholds to match.
PROJECTION_VERSION = "sections-trials-v9"
# A steering change worth naming, stated on steering-wheel angle (degrees) and
# converted to the recording's basis (road wheel: 2/12 deg).
STEERING_CHANGE_DEG = 2.0
MAX_COMPARISONS = 24
# A saved test is checked on screened laps within this many green laps of its
# exposure window, and needs this many of them (driving_trials.evaluate_trial).
TRIAL_EXPOSURE_TOLERANCE = 2.0
TRIAL_MINIMUM_LAPS = 3
# The longest run a test day is assumed to give between pit visits. Joshua's
# test days run about 20 laps per run; a test whose window needs a longer run
# can only be checked by a race or a long practice run.
TEST_DAY_RUN_LAPS = 20
TIRES = ("LF", "RF", "LR", "RR")
RUBBER_REFERENCE = {
    "title": "iRacing 2026 Season 1: oval track and tire interaction",
    "url": "https://support.iracing.com/support/solutions/articles/31000177717-2026-season-1-initial-release-notes-2025-12-08-03-",
}


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _round(value: Any, digits: int = 3) -> float | None:
    number = _number(value)
    return round(number, digits) if number is not None else None


def _median(values: Sequence[Any]) -> float | None:
    numbers = [number for value in values if (number := _number(value)) is not None]
    return statistics.median(numbers) if numbers else None


def _path(value: Mapping[str, Any], *keys: str) -> Any:
    current: Any = value
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _lap_numbers(laps: Sequence[Mapping[str, Any]]) -> list[int]:
    return [int(lap["lap"]) for lap in laps]


def _plot_pace(lap: Mapping[str, Any]) -> bool:
    racing = _number(lap.get("racing_state_fraction"))
    # SessionState can become checkered when the leader finishes, while this
    # driver is still completing a scored racing lap. Retain that observed lap.
    scored_finish = (lap.get("lap_time_source") == "sdk_scored"
                     and isinstance(lap.get("flag_states"), (list, tuple))
                     and "checkered" in lap["flag_states"]
                     and racing is not None and racing > 0)
    return bool(lap.get("complete") and lap.get("flag_state") == "green"
                and (_number(lap.get("pit_time_s")) or 0) < 1
                and (_number(lap.get("lap_time_s")) or 0) > 0
                and (racing is None or racing >= 0.98 or scored_finish))


OBSERVED_SECTION_CONTEXT = frozenset({"close_traffic", "damage_repair_context"})


def _observed_section_exclusions(lap: Mapping[str, Any], strict_reasons: Sequence[str]) -> list[str]:
    """A recorded comparison may retain traffic/ongoing repair; coaching stays strict."""
    reasons = [reason for reason in strict_reasons if reason not in OBSERVED_SECTION_CONTEXT]
    if not lap.get("complete"):
        reasons.append("partial")
    if lap.get("flag_state") != "green":
        reasons.append("caution_or_mixed")
    if (_number(lap.get("lap_time_s")) or 0) <= 0:
        reasons.append("lap_timing_unavailable")
    if any(lap.get(key) is True for key in ("pit_entry", "pit_exit", "pit_in", "pit_out")):
        reasons.append("pit_entry_or_exit")
    return list(dict.fromkeys(reasons))


def _observed_section_laps(rows: Sequence[Mapping[str, Any]], traces: Mapping[int, Mapping[str, Any]],
                           sections: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    try:
        from .race_inspection import section_elapsed
    except ImportError:  # pragma: no cover - normal CLI/MCP script loading.
        from race_inspection import section_elapsed
    candidates = {row["lap"]: row for row in rows if row.get("observed_section_eligible", row["eligible"])}
    result = []
    for section in sections:
        start, end = _number(section.get("start_pct")), _number(section.get("end_pct"))
        if start is None or end is None or not 0 <= start < end <= 1:
            continue
        timed = sorted((round(seconds, 3), number) for number in candidates
                       if (seconds := section_elapsed(traces.get(number), start, end)) is not None)
        middle = round(statistics.median(seconds for seconds, _ in timed), 3) if timed else None
        shown = min(3, len(timed) // 2)
        def entry(item):
            seconds, number = item
            row = candidates[number]
            return {"lap": number, "section_s": seconds, "delta_s": round(seconds - middle, 3),
                    "eligible": row["eligible"],
                    "context_reasons": [reason for reason in row["exclusion_reasons"] if reason in OBSERVED_SECTION_CONTEXT]}
        result.append({"section_id": section["id"], "observed_lap_count": len(timed),
                       "clean_lap_count": sum(candidates[number]["eligible"] for _, number in timed),
                       "lap_numbers": sorted(number for _, number in timed), "median_s": middle,
                       "median_lap": timed[(len(timed) - 1) // 2][1] if timed else None,
                       "fastest": [entry(item) for item in timed[:shown]],
                       "slowest": [entry(item) for item in reversed(timed[len(timed) - shown:])] if shown else []})
    return {"population": "observed_green_racing_laps", "sections": result}


def _window_text(numbers: Sequence[int]) -> str:
    if not numbers:
        return "No laps"
    if len(numbers) == 1:
        return f"Lap {numbers[0]}"
    return f"Laps {numbers[0]}–{numbers[-1]}"


def _joined(parts: Sequence[str]) -> str:
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def _lap_ranges(numbers: Sequence[int]) -> str:
    spans: list[list[int]] = []
    for number in sorted(numbers):
        if spans and number == spans[-1][1] + 1:
            spans[-1][1] = number
        else:
            spans.append([number, number])
    return _joined([f"{start}–{end}" if end > start else str(start) for start, end in spans])


def _caution_laps_within(numbers: Sequence[int], flags: Mapping[int, str]) -> list[int]:
    """Recorded caution laps from a window's first lap to its last."""
    if not numbers:
        return []
    return [lap for lap in range(min(numbers), max(numbers) + 1) if flags.get(lap) == "caution"]


def _lap_flags(analysis: Mapping[str, Any]) -> dict[int, str]:
    return {int(number): str(lap.get("flag_state") or "") for lap in analysis.get("laps", ())
            if isinstance(lap, Mapping) and (number := _number(lap.get("lap"))) is not None}


def _eligibility(before: Sequence[int], after: Sequence[int], exclusions: Mapping[int, Sequence[str]]) -> str:
    numbers = [*before, *after]
    eligible = sum(1 for number in numbers if not exclusions.get(number, ["eligibility_unknown"]))
    return "eligible" if eligible == len(numbers) else "mixed" if eligible else "observed"


def _caution_laps_between(before: Sequence[int], after: Sequence[int], flags: Mapping[int, str]) -> list[int]:
    """Recorded caution laps after the earlier window ends and before the later one starts."""
    if not before or not after or max(before) >= min(after):
        return []
    return [lap for lap in range(max(before) + 1, min(after)) if flags.get(lap) == "caution"]


def _pace_usability(kind: str, before: Sequence[int], after: Sequence[int], eligibility: str,
                    flags: Mapping[int, str]) -> tuple[list[str], list[str]]:
    """Reason codes and text for a comparison whose median delta is not a pace result.

    A pace change is claimed within one green run. A window that spans a
    caution, or earlier and later windows that a caution separates, mix laps
    from separate green runs; a comparison whose every lap is excluded has no
    screened lap to stand on. Cross-run comparisons pair laps at matched green
    exposure across a stop by design, so only cautions inside their windows
    disqualify them; their summary discloses that cautions are not controlled.
    """
    codes: list[str] = []
    reasons: list[str] = []
    for numbers in (before, after):
        cautions = _caution_laps_within(numbers, flags)
        if cautions:
            laps = _joined([str(number) for number in numbers])
            reasons.append(f"laps {laps} span caution {'lap' if len(cautions) == 1 else 'laps'} {_lap_ranges(cautions)}")
    if reasons:
        codes.append("spans_caution")
    between = _caution_laps_between(before, after, flags) if kind != "cross_run" else []
    if between:
        codes.append("separated_by_caution")
        reasons.append(f"caution {'lap' if len(between) == 1 else 'laps'} {_lap_ranges(between)} "
                       f"{'separates' if len(between) == 1 else 'separate'} laps {_lap_ranges(before)} from laps {_lap_ranges(after)}")
    if eligibility == "observed":
        codes.append("all_laps_excluded")
        reasons.append(f"all {len(before) + len(after)} selected laps are excluded from clean coaching")
    return codes, reasons


def _metric(key: str, label: str, unit: str, before: Any, after: Any, detail: str = "") -> dict[str, Any]:
    baseline, compared = _number(before), _number(after)
    return {
        "key": key, "label": label, "unit": unit,
        "baseline": _round(baseline), "compared": _round(compared),
        "delta": _round(compared - baseline) if baseline is not None and compared is not None else None,
        "evidence": "derived", "detail": detail,
    }


def _run_exposure(laps: Sequence[Mapping[str, Any]]) -> dict[int, float | None]:
    """Run-relative sampled green-time equivalents, never confirmed tire age."""
    result: dict[int, float | None] = {}
    age = 0.0
    known = True
    previous: int | None = None
    for lap in laps:
        number = int(lap["lap"])
        fraction = _number(lap.get("green_fraction"))
        if previous is not None and number != previous + 1:
            known = False
        if not lap.get("complete") or fraction is None or not 0 <= fraction <= 1:
            known = False
        if known:
            age += fraction or 0.0
        result[number] = age if known else None
        previous = number
    return result


def _cohort(laps: Sequence[Mapping[str, Any]], run: Mapping[str, Any], exclusions: Mapping[int, Sequence[str]], ages: Mapping[int, float | None]) -> dict[str, Any]:
    numbers = _lap_numbers(laps)
    pace = _median([lap.get("lap_time_s") for lap in laps])
    timed = [lap for lap in laps if _number(lap.get("lap_time_s")) is not None]
    representative = min(timed, key=lambda lap: abs(float(lap["lap_time_s"]) - float(pace))) if timed and pace is not None else None
    observed_ages = [ages.get(number) for number in numbers]
    age_available = bool(observed_ages) and all(value is not None for value in observed_ages)
    return {
        "label": _window_text(numbers), "run_number": run.get("run_number"),
        "lap_numbers": numbers, "lap_count": len(numbers),
        "eligible_lap_numbers": [number for number in numbers if not exclusions.get(number, ["eligibility_unknown"])],
        "excluded_laps": [{"lap": number, "reasons": list(exclusions.get(number, ["eligibility_unknown"]))} for number in numbers if exclusions.get(number, ["eligibility_unknown"])],
        "median_lap_s": _round(pace),
        "green_age_start": _round(min(observed_ages), 2) if age_available else None,
        "green_age_end": _round(max(observed_ages), 2) if age_available else None,
        "fuel_start_gal": _round(_median([_path(lap, "fuel", "start_l") for lap in laps]) / 3.785411784) if _median([_path(lap, "fuel", "start_l") for lap in laps]) is not None else None,
        "track_temp_f": _round(_median([_path(lap, "conditions", "track_temperature_f") for lap in laps]), 1),
        "representative_lap": int(representative["lap"]) if representative else None,
    }


def _cohort_metrics(before: Sequence[Mapping[str, Any]], after: Sequence[Mapping[str, Any]],
                    steering: SteeringBasis | None = None) -> list[dict[str, Any]]:
    basis = steering or SteeringBasis.from_descriptor(None)
    definitions = (
        ("pace", "Lap time", "s", ("lap_time_s",), 1.0),
        ("brake", "Average brake", "%", ("controls", "brake_mean"), 100.0),
        ("throttle", "Average throttle", "%", ("controls", "throttle_mean"), 100.0),
        ("steering", f"Average {basis.short_label.lower() if basis.road_wheel else 'steering-wheel angle'}", "deg",
         ("controls", "steering_abs_mean_rad"), 180.0 / math.pi),
        ("corrections", "Steering corrections", "per lap", ("controls", "steering_corrections"), 1.0),
        ("brake_work", "Braking work", "proxy", ("controls", "brake_energy_proxy"), 1.0),
        ("steering_work", "Steering work", "proxy", ("controls", "steering_work_proxy"), 1.0),
        ("traffic", "Close-traffic exposure", "%", ("clean_context", "traffic_proximity_fraction"), 100.0),
        ("fuel", "Starting fuel", "gal", ("fuel", "start_l"), 1.0 / 3.785411784),
        ("track_temperature", "Track temperature", "°F", ("conditions", "track_temperature_f"), 1.0),
        ("track_usage", "Recorded track usage", "%", ("conditions", "track_usage_percent"), 1.0),
        ("brake_bias", "Brake-bias setting", "setting", ("vehicle_dynamics", "brake_bias_setting", "median"), 1.0),
    )
    result = []
    for key, label, unit, path, multiplier in definitions:
        baseline = _median([_path(lap, *path) for lap in before])
        compared = _median([_path(lap, *path) for lap in after])
        if baseline is None and compared is None:
            continue
        result.append(_metric(key, label, unit, baseline * multiplier if baseline is not None else None, compared * multiplier if compared is not None else None,
                              "Median of per-lap observations; later window minus earlier window."))
    return result


def _crossing(points: Sequence[Mapping[str, Any]], position: float) -> float | None:
    for left, right in zip(points, points[1:]):
        x0, x1 = _number(left.get("lap_pct")), _number(right.get("lap_pct"))
        t0, t1 = _number(left.get("session_time_s")), _number(right.get("session_time_s"))
        if None in (x0, x1, t0, t1) or not 0 < x1 - x0 <= 0.03 or not 0 < t1 - t0 <= 2.0:
            continue
        if x0 <= position <= x1:
            return t0 + (position - x0) / (x1 - x0) * (t1 - t0)
    return None


def _corner_lap(trace: Mapping[str, Any], start: float, end: float) -> dict[str, Any] | None:
    try:
        from .race_inspection import _points
    except ImportError:  # pragma: no cover
        from race_inspection import _points
    points = _points(trace)
    core = [point for point in points if start <= point["lap_pct"] <= end]
    if len(core) < 2:
        return None
    entered, exited = _crossing(points, start), _crossing(points, end)
    speeds = [value for point in core if (value := _number(point.get("speed_mph"))) is not None]
    if not speeds:
        return None
    minimum = min((point for point in core if _number(point.get("speed_mph")) is not None), key=lambda point: point["speed_mph"])
    pickup = None
    after_minimum = [point for point in core if point["lap_pct"] >= minimum["lap_pct"]]
    for left, right in zip(after_minimum, after_minimum[1:]):
        v0, v1 = _number(left.get("throttle")), _number(right.get("throttle"))
        if v0 is not None and v1 is not None and v0 < 0.75 <= v1:
            pickup = float(right["lap_pct"]) * 100.0
            break
    brake_release = None
    for left, right in zip(core, core[1:]):
        v0, v1 = _number(left.get("brake")), _number(right.get("brake"))
        if v0 is not None and v1 is not None and v0 >= 0.05 > v1:
            brake_release = float(right["lap_pct"]) * 100.0
    return {
        "elapsed": exited - entered if entered is not None and exited is not None and exited > entered else None,
        "entry_speed": _number(core[0].get("speed_mph")),
        "minimum_speed": min(speeds), "exit_speed": _number(core[-1].get("speed_mph")),
        "brake": _median([point.get("brake_mean", point.get("brake")) for point in core]),
        "throttle": _median([point.get("throttle") for point in core]),
        "steering": _median([abs(value) for point in core if (value := _number(point.get("steering_rad"))) is not None]),
        "brake_release": brake_release, "throttle_pickup": pickup,
    }


def _corners(before: Sequence[int], after: Sequence[int], traces: Mapping[int, Mapping[str, Any]], profile: Mapping[str, Any],
             steering: SteeringBasis | None = None) -> list[dict[str, Any]]:
    steering_label = (steering or SteeringBasis.from_descriptor(None)).short_label
    result = []
    for index, segment in enumerate(profile.get("detected_corner_segments") or (), 1):
        start, end = _number(segment.get("start_pct")), _number(segment.get("end_pct"))
        if start is None or end is None or not 0 <= start < end <= 1 or segment.get("wraps_start_finish"):
            continue
        baseline = [metric for lap in before if lap in traces and (metric := _corner_lap(traces[lap], start, end)) is not None]
        compared = [metric for lap in after if lap in traces and (metric := _corner_lap(traces[lap], start, end)) is not None]
        if len(baseline) < 2 or len(compared) < 2:
            continue
        metrics = []
        for key, label, unit, multiplier in (
            ("elapsed", "Area time", "s", 1.0),
            ("entry_speed", "Entry speed", "mph", 1.0),
            ("minimum_speed", "Minimum speed", "mph", 1.0),
            ("exit_speed", "Exit speed", "mph", 1.0),
            ("brake", "Brake demand", "%", 100.0),
            ("throttle", "Throttle demand", "%", 100.0),
            ("steering", steering_label, "deg", 180.0 / math.pi),
            ("brake_release", "Brake release", "% lap", 1.0),
            ("throttle_pickup", "Throttle pickup", "% lap", 1.0),
        ):
            one = [metric[key] for metric in baseline if metric[key] is not None]
            two = [metric[key] for metric in compared if metric[key] is not None]
            # Timing transitions and all other metrics require two observations per cohort.
            if len(one) < 2 or len(two) < 2:
                continue
            metrics.append(_metric(key, label, unit, statistics.median(one) * multiplier, statistics.median(two) * multiplier,
                                   f"{len(one)} earlier and {len(two)} later laps; distance-binned traces."))
        elapsed = next((metric for metric in metrics if metric["key"] == "elapsed"), {})
        result.append({
            "id": f"track-area-{index}", "label": f"Track area {index} ({start * 100:.0f}–{end * 100:.0f}% lap)",
            "start_pct": start, "end_pct": end, "delta_s": elapsed.get("delta"), "metrics": metrics,
        })
    return sorted(result, key=lambda item: item["delta_s"] if item["delta_s"] is not None else -math.inf, reverse=True)


def _track_evolution(laps: Sequence[Mapping[str, Any]], context: Mapping[str, Any]) -> dict[str, Any]:
    starting = next((str(_path(lap, "conditions", "track_usage")) for lap in laps if _path(lap, "conditions", "track_usage")), None)
    points = [{"lap": int(lap["lap"]), "track_temp_f": _round(_path(lap, "conditions", "track_temperature_f"), 1),
               "usage_value": _round(_path(lap, "conditions", "track_usage_percent"), 1)} for lap in laps]
    for point in points:
        if point["usage_value"] is not None and not 0 <= point["usage_value"] <= 100:
            point["usage_value"] = None
    values = [point["usage_value"] for point in points if point["usage_value"] is not None]
    if len(values) >= 2:
        summary = f"Recorded usage changed from {values[0]:.1f}% to {values[-1]:.1f}%." if max(values) - min(values) > 0.1 else f"Recorded usage stayed near {values[0]:.1f}% across the available samples."
        status, provenance = "measured_series", "Per-lap medians of recorded TrackUsage samples."
    elif starting:
        status, provenance = "starting_state_only", "SessionTrackRubberState session metadata; not a measured race-long series."
        summary = f"Inherited session rubber label: {starting}. Rubber progression during this race was not measured."
    else:
        status, provenance = "unavailable", "No usable track-usage series or starting rubber state."
        summary = "Track rubber evolution was not recorded."
    states = context.get("session_states") or []
    if states:
        chain = " → ".join(f"{item.get('session_type', 'Session')}: {item.get('state', 'unknown')}" for item in states)
        provenance += f" Session initialization: {chain}. The metadata labels are not sampled evolution."
    if context.get("dynamic_track_enabled") is True:
        provenance += " Dynamic track was enabled; that setting does not measure the direction or amount of grip change."
    return {"status": status, "provenance": provenance, "starting_usage": starting, "summary": summary,
            "points": points, "usage_unit": "%", "usage_source": "TrackUsage" if values else None,
            "limitations": ["Track temperature is a separate measurement; stable temperature does not establish stable rubber, marbles or grip.",
                            "A starting or carry-over rubber label does not measure subsequent rubber build-up or groove grip."]}


def _hypothesis(key: str, label: str, status: str, supporting: list[str], contradicting: list[str], unknown: list[str]) -> dict[str, Any]:
    return {"key": key, "label": label, "status": status, "supporting": supporting, "contradicting": contradicting, "unknown": unknown, "general_context": "", "references": []}


def _tire_service_evidence(comparison: Mapping[str, Any], analysis: Mapping[str, Any]) -> tuple[list[str], bool]:
    """Recorded tire service and readings that bear on the selected windows.

    A run's own pit_service is the stop that ENDED it, so the tires a window ran
    on were fitted at the stop that ended the previous run. Kansas 9/21 run 2
    (laps 19-42) started on four tires changed at the lap-17 stop, yet its
    comparisons read "Tires and service: Unresolved" because only run 2's own,
    absent, end-of-run service was consulted. Returns the supporting lines and
    whether every selected run started on a confirmed four-tire change.
    """
    runs = sorted((run for run in analysis.get("runs", ()) if isinstance(run, Mapping)),
                  key=lambda run: _number(run.get("run_number")) or 0)
    selected = {comparison["baseline"]["run_number"], comparison["compared"]["run_number"]}
    support: list[str] = []
    fresh = []
    for index, run in enumerate(runs):
        number = run.get("run_number")
        if number not in selected:
            continue
        previous = runs[index - 1] if index else None
        fitted = [str(tire) for tire in (_path(previous, "pit_service", "tires_changed_observed") or ()) if tire] if previous else []
        if fitted:
            stop_lap = _number(previous.get("end_lap"))
            at = f" at the lap {int(stop_lap)} stop" if stop_lap is not None else " at the previous stop"
            count = "four tires" if len(set(fitted)) == 4 else f"{len(fitted)} changed tire{'s' if len(fitted) != 1 else ''}"
            support.append(f"Run {number} started on {count} fitted{at} (confirmed: {', '.join(fitted)}).")
        fresh.append(len(set(fitted)) == 4)
        measured = _number(_path(run, "tire_observation", "lowest_remaining_percent"))
        if measured is not None:
            support.append(f"Run {number} ended with a lowest measured tire reading of {measured:.1f}% remaining.")
    return support, bool(fresh) and all(fresh)


def _hypotheses(comparison: Mapping[str, Any], analysis: Mapping[str, Any], track: Mapping[str, Any]) -> list[dict[str, Any]]:
    metrics = {item["key"]: item for item in comparison["metrics"]}
    def changed(key: str, threshold: float) -> bool:
        return key in metrics and metrics[key]["delta"] is not None and abs(metrics[key]["delta"]) >= threshold
    def text(key: str) -> str:
        item = metrics[key]
        return f"{item['label']}: {item['baseline']:.2f} → {item['compared']:.2f} {item['unit']}."
    steering_change = basis_of(analysis).threshold(STEERING_CHANGE_DEG)
    controls = [text(key) for key, threshold in (("brake", 1.0), ("throttle", 2.0), ("steering", steering_change), ("corrections", 1.0), ("brake_bias", 0.1)) if changed(key, threshold)]
    comparable_controls = any(metrics.get(key, {}).get("delta") is not None for key in ("brake", "throttle", "steering", "corrections", "brake_bias"))
    result = [_hypothesis("driving", "Driving inputs", "supported" if controls else "unresolved", controls,
                          ["No large change was found in the available whole-lap control summaries."] if comparable_controls and not controls else [],
                          (["Comparable control measurements were not available in both windows."] if not comparable_controls else []) + ["Changed inputs describe what happened; they do not establish whether the driver, grip, traffic or setup caused the change. Mental focus is not measured."])]
    traffic = metrics.get("traffic")
    excluded = comparison["compared"]["excluded_laps"]
    traffic_laps = [item["lap"] for item in excluded if "close_traffic" in item["reasons"]]
    baseline_traffic = [item["lap"] for item in comparison["baseline"]["excluded_laps"] if "close_traffic" in item["reasons"]]
    selected_numbers = set(comparison["baseline"]["lap_numbers"] + comparison["compared"]["lap_numbers"])
    selected_laps = [lap for lap in analysis.get("laps", ()) if lap.get("lap") in selected_numbers]
    traffic_complete = len(selected_laps) == len(selected_numbers) and all(_number(_path(lap, "clean_context", "traffic_proximity_fraction")) is not None for lap in selected_laps)
    result.append(_hypothesis("traffic", "Traffic", "possible" if traffic_laps or baseline_traffic else "not_supported" if traffic_complete else "unresolved",
                             ([f"{len(baseline_traffic)} earlier and {len(traffic_laps)} later laps triggered the proximity screen."] if traffic_laps or baseline_traffic else []) + ([text("traffic")] if changed("traffic", 1.0) else []),
                             ["Both selected windows pass the recorded traffic screen."] if traffic_complete and not traffic_laps and not baseline_traffic else [],
                             (["Proximity measurements are missing for part of the selected windows."] if not traffic_complete else []) + ["Proximity does not prove a blocker, contact, or the amount of time lost; inspect the selected laps in replay."]))
    condition_support = [text(key) for key, threshold in (("track_temperature", 1.0), ("track_usage", 0.1)) if changed(key, threshold)]
    surface = _hypothesis("track", "Track evolution", "possible" if condition_support else "unresolved", condition_support,
                          [], [track["summary"], "Temperature alone cannot rule out changing rubber, marbles or usable grip."])
    surface["general_context"] = "iRacing's oval track model allows a more rubbered surface to shift wear toward the tire and reduce the long-run advantage of staying in the groove. That is general behavior, not proof of this race's cause."
    surface["references"] = [RUBBER_REFERENCE]
    result.append(surface)
    result.append(_hypothesis("fuel", "Fuel load", "possible" if changed("fuel", 0.5) else "unresolved", [text("fuel")] if changed("fuel", 0.5) else [], [],
                             ["The comparison is not fuel-corrected; a measured fuel difference does not quantify its lap-time effect."]))
    tire_support, fresh_sets = _tire_service_evidence(comparison, analysis)
    result.append(_hypothesis("tires", "Tires and service", "possible" if tire_support else "unresolved", tire_support, [],
                             ["Run-relative green exposure is not exact tire wear." + ("" if fresh_sets else " Initial tire age and mixed sets can prevent a like-for-like comparison."),
                              "A stop reading belongs to the preceding run; absent final-run readings cannot establish final tire wear."]))
    damage = analysis.get("damage_repair") or {}
    repair_count = _number(_path(damage, "summary", "recorded_repair_episodes"))
    result.append(_hypothesis("damage", "Damage and repairs", "possible" if repair_count and repair_count > 0 else "unresolved",
                             [f"{int(repair_count)} repair episode(s) were recorded."] if repair_count and repair_count > 0 else [],
                             ["No repair workload was recorded."] if repair_count == 0 and damage.get("status") != "unavailable" else [],
                             ["No recorded repair workload does not establish an undamaged car. Incident points do not identify physical damage or fault."]))
    return result


def _cohort_section_seconds(numbers: Sequence[int], traces: Mapping[int, Mapping[str, Any]], start: float, end: float) -> list[float]:
    try:
        from .race_inspection import section_elapsed
    except ImportError:  # pragma: no cover - normal CLI/MCP script-loading path.
        from race_inspection import section_elapsed
    return [seconds for lap in numbers if (seconds := section_elapsed(traces.get(lap), start, end)) is not None]


def trial_reachability(window: Sequence[Any] | None, minimum_laps: Any = TRIAL_MINIMUM_LAPS) -> dict[str, Any]:
    """How long one run must be before a later recording can check a test.

    Exposure counts sampled green laps since the run started, and a run starts
    at every pit visit, so this is a run length without pitting, not tire age.
    On a clear green run lap n sits near n green laps. A check needs
    `minimum_laps` screened laps no earlier than the window start less the
    tolerance, so the run must reach that lap and continue for the rest. A
    test-day run is assumed to last up to TEST_DAY_RUN_LAPS laps.
    """
    low = _number(window[0]) if isinstance(window, (list, tuple)) and len(window) == 2 else None
    first = max(1, math.ceil(round(low - TRIAL_EXPOSURE_TOLERANCE, 6))) if low is not None else 1
    count = int(_number(minimum_laps) or TRIAL_MINIMUM_LAPS)
    needed = first + max(1, count) - 1
    return {"run_laps_needed": needed, "test_day_run_laps": TEST_DAY_RUN_LAPS,
            "test_day_reachable": needed <= TEST_DAY_RUN_LAPS}


def _trial(comparison: Mapping[str, Any], corner: Mapping[str, Any], section_set: Mapping[str, Any], traces: Mapping[int, Mapping[str, Any]], approach_start: float | None) -> dict[str, Any] | None:
    """A saveable test: exact bounds, the slow condition to beat and the rule.

    The baseline is the compared (slower) cohort because the test asks whether
    a later recording repeats it. Success needs three screened laps inside the
    same exposure window to beat the baseline median by the stated threshold;
    anything else is reported as not comparable or not established.
    """
    # A baseline with traffic, incident or other excluded laps could be "beaten"
    # by clear running alone, so only fully screened comparisons become tests.
    if comparison.get("eligibility") != "eligible":
        return None
    start = approach_start if approach_start is not None else corner["start_pct"]
    end = corner["end_pct"]
    slow = _cohort_section_seconds(comparison["compared"]["lap_numbers"], traces, start, end)
    fast = _cohort_section_seconds(comparison["baseline"]["lap_numbers"], traces, start, end)
    if len(slow) < 2 or len(fast) < 2:
        return None
    slow_median, fast_median = statistics.median(slow), statistics.median(fast)
    loss = slow_median - fast_median
    if loss <= 0.02:
        return None
    compared = comparison["compared"]
    window = [compared.get("green_age_start"), compared.get("green_age_end")]
    window = window if None not in window else None
    return {
        "metric": {"key": "approach_and_section_s" if approach_start is not None else "section_s",
                   "label": "Approach and section time" if approach_start is not None else "Section time",
                   "unit": "s", "direction": "decrease", "start_pct": start, "end_pct": end},
        "section": {"id": corner["id"], "label": corner.get("short_label") or corner["id"],
                    "start_pct": corner["start_pct"], "end_pct": end,
                    "boundary_status": corner.get("boundary_status"),
                    **({"name": corner["name"]} if corner.get("name") else {})},
        "annotation": {"configuration_key": section_set.get("configuration_key"), "source": section_set.get("source"),
                       "version": section_set.get("version")},
        "baseline": {"laps": list(compared["lap_numbers"]), "run_number": compared.get("run_number"),
                     "median_s": _round(slow_median), "count": len(slow)},
        "reference": {"laps": list(comparison["baseline"]["lap_numbers"]), "run_number": comparison["baseline"].get("run_number"),
                      "median_s": _round(fast_median), "count": len(fast)},
        "exposure_window": window,
        "reachability": trial_reachability(window, TRIAL_MINIMUM_LAPS),
        "conditions": {"fuel_start_gal": compared.get("fuel_start_gal"), "track_temp_f": compared.get("track_temp_f")},
        "success": {"threshold_s": _round(max(0.02, loss / 2)), "minimum_laps": TRIAL_MINIMUM_LAPS,
                    "rule": "Improved when at least three screened laps inside the exposure window beat the baseline median by the threshold. Worse when they are slower by the threshold. Otherwise not established."},
    }


def _actions(comparison: Mapping[str, Any], section_set: Mapping[str, Any] | None = None, traces: Mapping[int, Mapping[str, Any]] | None = None,
             steering: SteeringBasis | None = None) -> list[dict[str, Any]]:
    steering_basis = steering or SteeringBasis.from_descriptor(None)
    steering_change = steering_basis.threshold(STEERING_CHANGE_DEG)
    result = []
    section_set = section_set or {}
    traces = traces or {}
    baseline, compared = comparison["baseline"], comparison["compared"]
    lap_numbers = sorted(set(baseline["lap_numbers"] + compared["lap_numbers"]))
    if comparison.get("pace_comparable") is False:
        # Section losses between these windows inherit the same caution or
        # exclusion confound, so they do not become driving corrections.
        return [{"priority": 1, "title": "Compare laps from one green run",
                 "instruction": "These windows cannot show a pace change. Compare complete, clear green laps that no caution separates, at similar fuel and run age.",
                 "evidence": [comparison["summary"]],
                 "limitation": "Section and control differences in these windows are observations, not driving corrections.",
                 "lap_numbers": lap_numbers, "start_pct": None, "end_pct": None,
                 "signal_keys": ["delta", "speed", "brake", "throttle", "steering"], "section_id": None, "trial": None}]
    caveat = "Change one input at a time on clear, complete green laps at similar run age and fuel. The observed association is not a proven cause."
    if comparison["eligibility"] != "eligible":
        caveat = "The later window includes excluded laps. Confirm the pattern on a clear lap before treating this as a driving correction. " + caveat
    inspection = comparison.get("inspection") or {}
    ordered = sorted(section_set.get("sections") or (), key=lambda item: item["start_pct"])
    labels = {item["id"]: item["label"] for item in ordered}
    for corner in comparison["corners"]:
        if len(result) >= 2:
            break
        metrics = {metric["key"]: metric for metric in corner["metrics"]}
        loss = corner.get("delta_s")
        if loss is None or loss < 0.03:
            continue
        name = f"section {corner.get('short_label') or corner['id']}" + (f" ({corner['name']})" if corner.get("name") else "")
        pair = (inspection.get("sections") or {}).get(corner["id"]) or {}
        brake = metrics.get("brake", {}).get("delta")
        steer = metrics.get("steering", {}).get("delta")
        throttle = metrics.get("throttle", {}).get("delta")
        minimum = metrics.get("minimum_speed", {}).get("delta")
        entry = metrics.get("entry_speed", {}).get("delta")
        evidence = [f"{corner['label']} took {loss:.3f} s longer by group median."]
        approach = None
        upstream_gap, upstream_speed = pair.get("entry_gap_s"), pair.get("entry_speed_difference_mph")
        index = next((i for i, item in enumerate(ordered) if item["id"] == corner["id"]), None)
        previous = ordered[index - 1] if index else None
        if (previous is not None and inspection.get("mode") == "matched_pool_extremes" and pair.get("timing_complete")
                and upstream_gap is not None and upstream_gap >= 0.05 and upstream_speed is not None and upstream_speed <= -2.0):
            a, b = pair["a"], pair["b"]
            before = labels.get(previous["id"], previous["id"])
            title = f"Carry more speed into {name}"
            instruction = (f"The slower pass (lap {b['lap']}) reached {name} {abs(upstream_speed):.1f} mph slower and already {upstream_gap:.3f} s behind lap {a['lap']}, measured from {pair['context_start_pct'] * 100:.1f}% lap. "
                           f"Test one change at the exit of section {before}: a tighter exit line with throttle added as the wheel unwinds. "
                           "If entry speed recovers, the loss was built on that exit. If entry speed stays low with matching inputs, treat it as a grip or setup question rather than technique.")
            evidence.append(f"Lap {b['lap']} entered {abs(upstream_speed):.1f} mph slower and {upstream_gap:.3f} s behind; it lost {pair.get('section_delta_s') or 0:.3f} s more inside.")
            signals = ["speed", "delta", "throttle", "steering"]
            approach = pair.get("context_start_pct")
        elif brake is not None and brake > 1.0 and minimum is not None and minimum < -0.5:
            title = "Test a smoother brake release"
            instruction = f"At {name}, keep the entry controlled and release brake pressure progressively. Check whether minimum speed recovers without adding steering correction or sacrificing exit speed."
            evidence.append(f"Brake demand rose {brake:.1f} percentage points while minimum speed fell {abs(minimum):.1f} mph.")
            signals = ["speed", "brake", "steering", "delta"]
        elif steer is not None and steer > steering_change:
            title = "Test a smoother steering arc"
            instruction = f"At {name}, test a small entry-speed margin and one smoother steering arc. Compare section time and exit speed; retain the change only if it reduces correction without worsening pace."
            applied = "road-wheel steering" if steering_basis.road_wheel else "steering-wheel angle"
            evidence.append(f"Median {applied} increased {steer:.{steering_basis.degree_digits}f}° in the same section.")
            signals = ["speed", "steering", "throttle", "delta"]
        elif throttle is not None and throttle < -2.0:
            title = "Test the exit-throttle transition"
            instruction = f"At {name}, work throttle back in progressively as steering unwinds. Compare exit speed and section time against the earlier laps; do not add throttle solely to match a trace."
            evidence.append(f"Throttle demand fell {abs(throttle):.1f} percentage points in this section.")
            signals = ["speed", "throttle", "steering", "delta"]
        elif entry is not None and entry < -1.0:
            title = f"Find where {name} entry speed is lost"
            instruction = (f"Entry speed at {name} fell {abs(entry):.1f} mph with similar inputs inside it. Repeat three clear laps at the same run age and fuel. "
                           "If the entry deficit repeats with matching throttle and line on the approach, treat it as a grip or setup question rather than technique.")
            evidence.append(f"Entry speed fell {abs(entry):.1f} mph by group median.")
            signals = ["speed", "delta", "throttle", "steering"]
            approach = pair.get("context_start_pct") if pair.get("timing_complete") else None
        else:
            continue
        action = {"priority": len(result) + 1, "title": title, "instruction": instruction,
                  "evidence": evidence, "limitation": caveat, "lap_numbers": lap_numbers,
                  "start_pct": corner["start_pct"], "end_pct": corner["end_pct"], "signal_keys": signals,
                  "section_id": corner["id"]}
        action["trial"] = _trial(comparison, corner, section_set, traces, approach)
        result.append(action)
    if not result:
        largest = next((corner for corner in comparison["corners"] if (corner.get("delta_s") or 0) >= 0.03), None)
        if largest is not None:
            label = (largest.get("short_label") or largest["id"]) + (f" ({largest['name']})" if largest.get("name") else "")
            result.append({"priority": 1, "title": f"Repeat section {label} at the same run age",
                           "instruction": f"Section {label} lost {largest['delta_s']:.3f} s by group median without a supported input change. Drive three clear laps at the same run age and fuel with unchanged inputs. If the loss repeats, treat it as grip, tires or setup rather than technique; if it disappears, it was lap-to-lap variation.",
                           "evidence": [comparison["summary"]], "limitation": caveat,
                           "lap_numbers": lap_numbers, "start_pct": largest["start_pct"], "end_pct": largest["end_pct"],
                           "signal_keys": ["speed", "delta", "throttle", "steering"], "section_id": largest["id"],
                           "trial": _trial(comparison, largest, section_set, traces, None)})
        else:
            result.append({"priority": 1, "title": "Locate the repeatable pace loss", "instruction": "No section shows a repeatable loss in these laps. Compare complete, clear laps at similar fuel and run age before changing technique.",
                           "evidence": [comparison["summary"]], "limitation": caveat,
                           "lap_numbers": lap_numbers, "start_pct": None, "end_pct": None,
                           "signal_keys": ["delta", "speed", "brake", "throttle", "steering"], "section_id": None, "trial": None})
    return result


def _comparison(identifier: str, kind: str, before: Sequence[Mapping[str, Any]], after: Sequence[Mapping[str, Any]], before_run: Mapping[str, Any], after_run: Mapping[str, Any], exclusions: Mapping[int, Sequence[str]], ages: Mapping[int, float | None], traces: Mapping[int, Mapping[str, Any]], analysis: Mapping[str, Any], track: Mapping[str, Any], sections: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
    baseline = _cohort(before, before_run, exclusions, ages)
    compared = _cohort(after, after_run, exclusions, ages)
    eligible_count = len(baseline["eligible_lap_numbers"]) + len(compared["eligible_lap_numbers"])
    count = baseline["lap_count"] + compared["lap_count"]
    eligibility = _eligibility(baseline["lap_numbers"], compared["lap_numbers"], exclusions)
    delta = compared["median_lap_s"] - baseline["median_lap_s"] if baseline["median_lap_s"] is not None and compared["median_lap_s"] is not None else None
    label = "slower" if delta is not None and delta > 0 else "faster" if delta is not None and delta < 0 else "unchanged"
    # A lap-time gap between two windows, in seconds; "s/lap" is kept for a slope (fall-off).
    summary = ((f"{compared['label']}: median lap {abs(delta):.3f} s {label} than {baseline['label'].lower()}." if label != "unchanged"
                else f"{compared['label']}: median lap equal to {baseline['label'].lower()}.")
               if delta is not None else "Both windows need usable recorded lap times.")
    if eligibility != "eligible":
        summary += f" {count - eligible_count} of {count} selected laps are excluded from clean coaching."
    unusable, reasons = _pace_usability(kind, baseline["lap_numbers"], compared["lap_numbers"], eligibility, _lap_flags(analysis))
    if unusable:
        # Cohort medians stay visible as observations; no pace change is claimed.
        delta = None
        summary = "Not a usable pace comparison: " + "; ".join(reasons) + "."
    title = {"observed_finish": f"Run {after_run.get('run_number')}: earlier to final green laps",
             "within_run": f"Run {after_run.get('run_number')}: clean early versus late",
             "early_run": f"Run {after_run.get('run_number')}: first {TEST_DAY_RUN_LAPS} green laps",
             }.get(kind, f"Runs {before_run.get('run_number')} and {after_run.get('run_number')}: similar green exposure")
    steering = basis_of(analysis)
    result = {"id": identifier, "kind": kind, "title": title, "summary": summary,
              "pace_comparable": not unusable, "pace_unusable_reasons": unusable,
              "baseline": baseline, "compared": compared, "delta_s": _round(delta), "eligibility": eligibility,
              "metrics": _cohort_metrics(before, after, steering),
              "corners": _section_metrics(baseline["lap_numbers"], compared["lap_numbers"], traces, sections, steering),
              "hypotheses": [], "actions": []}
    # Explanations describe a pace change; an unusable comparison claims none,
    # so it carries no explanation statuses to read as evidence.
    result["hypotheses"] = [] if unusable else _hypotheses(result, analysis, track)
    return result


def _section_metrics(before: Sequence[int], after: Sequence[int], traces: Mapping[int, Mapping[str, Any]], sections: Sequence[Mapping[str, Any]],
                     steering: SteeringBasis | None = None) -> list[dict[str, Any]]:
    try:
        from .race_inspection import comparison_sections
    except ImportError:  # pragma: no cover - normal CLI/MCP script-loading path.
        from race_inspection import comparison_sections
    return comparison_sections(before, after, traces, sections, steering=steering)


def _timeline(analysis: Mapping[str, Any], laps: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result = []
    labels = {"race_start": "Recording starts", "race_end": "Recording ends", "caution_period": "Caution", "pit_service": "Pit service", "restart": "Restart"}
    for event in _path(analysis, "race_timeline", "events") or ():
        lap = _number(event.get("start_lap"))
        if lap is None:
            continue
        kind = str(event.get("event_type") or "recorded_event")
        end = _number(event.get("end_lap"))
        detail = f"Laps {int(lap)}–{int(end)}" if end is not None and end != lap else f"Lap {int(lap)}"
        if kind == "pit_service":
            tires = event.get("confirmed_tires") or []
            fuel = _number(event.get("confirmed_fuel_added_l"))
            if tires:
                detail += f"; confirmed tires: {', '.join(tires)}"
            if fuel is not None:
                detail += f"; {fuel / 3.785411784:.2f} gal added"
        result.append({"lap": int(lap), "kind": kind, "label": labels.get(kind, kind.replace("_", " ").capitalize()), "detail": detail, "value": None, "unit": None})
    for incident in _path(analysis, "damage_repair", "incident_points", "events") or ():
        lap = _number(incident.get("candidate_lap"))
        points = _number(incident.get("points_added"))
        if lap is not None and points is not None:
            result.append({"lap": int(lap), "kind": "incident", "label": f"Incident +{points:g}x", "detail": "Recorded point change; event type, damage and fault are not established.", "value": points, "unit": "points"})
    previous = None
    for lap in laps:
        bias = _number(_path(lap, "vehicle_dynamics", "brake_bias_setting", "median"))
        if bias is not None and previous is not None and abs(bias - previous) >= 0.01:
            result.append({"lap": int(lap["lap"]), "kind": "brake_bias", "label": "Brake-bias setting changed", "detail": f"{previous:g} → {bias:g}; per-lap median control setting.", "value": bias, "unit": "setting"})
        if bias is not None:
            previous = bias
    return sorted(result, key=lambda item: (item["lap"], item["kind"]))


def _early_run_window(clean: Sequence[Mapping[str, Any]], size: int, ages: Mapping[int, float | None]) -> list[Mapping[str, Any]] | None:
    """The latest clean laps of a run that a test-day run reaches.

    Only for a run whose clean early-versus-late window needs a longer run than
    a test day gives (IRP 9/23: laps at 77.5-81.5 green laps into an 80-lap
    run). The earlier window stays the run's first clean laps; the later one is
    the last `size` clean laps after them within TEST_DAY_RUN_LAPS green laps,
    so a loss already present by then can be checked on a test day. The run's
    later laps are not used and nothing is extrapolated to them.
    """
    late = [ages.get(int(lap["lap"])) for lap in clean[-size:]]
    if None in late or trial_reachability([min(late), max(late)])["test_day_reachable"]:
        return None
    reachable = [lap for lap in clean[size:]
                 if (age := ages.get(int(lap["lap"]))) is not None and age <= TEST_DAY_RUN_LAPS]
    return reachable[-size:] if len(reachable) >= size else None


def _early_run_version(action: Mapping[str, Any], early: Mapping[str, Any]) -> dict[str, Any] | None:
    """The early-run comparison's test for the same section, when it should be offered instead.

    Offered only when this action's own test is absent or needs a longer run
    than a test day gives, and the early-run comparison passed every screen and
    produced its own test for the same section that a test-day run reaches. The
    early test keeps its own evidence, title and success rule; it never
    inherits this window's loss.
    """
    own = action.get("trial")
    if action.get("section_id") is None or (own is not None and own["reachability"]["test_day_reachable"]):
        return None
    match = next((item for item in early.get("actions") or ()
                  if item.get("section_id") == action["section_id"] and isinstance(item.get("trial"), Mapping)
                  and item["trial"]["reachability"]["test_day_reachable"]), None)
    return {"comparison_id": early["id"], "action_priority": match["priority"]} if match is not None else None


def _default_rank(comparison: Mapping[str, Any]) -> tuple[bool, bool, int, bool, bool]:
    """Order comparisons by usefulness as the one the investigation opens on.

    A usable pace result comes first; an unusable one leads only when nothing
    else exists, and then its summary states why. Among usable comparisons one
    that offers a saveable test wins, because only it turns the finding into
    something the driver can check in a later recording. Then the latest
    compared lap, then a fully screened window over one that keeps excluded
    laps. IRP 9/23 opened on "earlier to final green laps" (laps 106-110,
    mixed, nothing to save) one row below "clean early versus late" (laps
    105-109, the same +0.730 s/lap, two saveable tests).
    """
    return (bool(comparison["pace_comparable"]),
            any(action.get("trial") for action in comparison["actions"]),
            max(comparison["compared"]["lap_numbers"]),
            comparison["eligibility"] == "eligible",
            comparison["kind"] == "observed_finish")


def build_race_investigation(analysis: Mapping[str, Any], *, lap_exclusions: Mapping[int, Sequence[str]]) -> dict[str, Any]:
    """Build compact, auditable findings; caller supplies authoritative eligibility."""
    laps = sorted((lap for lap in analysis.get("laps", ()) if isinstance(lap, Mapping) and (_number(lap.get("lap")) or 0) > 0), key=lambda lap: lap["lap"])
    runs = sorted((run for run in analysis.get("runs", ()) if isinstance(run, Mapping)), key=lambda run: run.get("run_number", 0))
    traces = {int(trace["lap"]): trace for trace in _path(analysis, "lap_traces", "traces") or () if isinstance(trace, Mapping) and _number(trace.get("lap")) is not None}
    track = _track_evolution(laps, analysis.get("track_usage_context") or {})
    by_run: dict[int, list[Mapping[str, Any]]] = {}
    ages: dict[int, float | None] = {}
    run_for_lap: dict[int, int] = {}
    candidates = []
    for run in runs:
        number = int(run.get("run_number") or 0)
        start, end = _number(run.get("start_lap")), _number(run.get("end_lap"))
        current = [lap for lap in laps if start is not None and end is not None and start <= lap["lap"] <= end]
        by_run[number] = current
        ages.update(_run_exposure(current))
        run_for_lap.update({int(lap["lap"]): number for lap in current})
        clean = [lap for lap in current if not lap_exclusions.get(int(lap["lap"]), ["eligibility_unknown"]) and (_number(lap.get("lap_time_s")) or 0) > 0]
        observed = [lap for lap in current if _plot_pace(lap)]
        early_candidate = None
        if len(clean) >= 4:
            size = min(5, max(2, len(clean) // 3))
            candidates.append((f"run-{number}-clean", "within_run", clean[:size], clean[-size:], run, run))
            early = _early_run_window(clean, size, ages)
            if early is not None:
                early_candidate = (f"run-{number}-early", "early_run", clean[:size], early, run, run)
        if len(observed) >= 4:
            size = min(5, max(2, len(observed) // 3))
            before = clean[:size] if len(clean) >= size else observed[:size]
            after = observed[-size:]
            if max(lap["lap"] for lap in before) < min(lap["lap"] for lap in after) and (not candidates or _lap_numbers(after) != _lap_numbers(candidates[-1][3])):
                candidates.append((f"run-{number}-finish", "observed_finish", before, after, run, run))
        # After the run's late windows, so the finish window's duplicate check is unchanged.
        if early_candidate is not None:
            candidates.append(early_candidate)
    for before_run, after_run in zip(runs, runs[1:]):
        before = [lap for lap in by_run[int(before_run.get("run_number") or 0)] if not lap_exclusions.get(int(lap["lap"]), ["eligibility_unknown"]) and ages.get(int(lap["lap"])) is not None and (_number(lap.get("lap_time_s")) or 0) > 0]
        after = [lap for lap in by_run[int(after_run.get("run_number") or 0)] if not lap_exclusions.get(int(lap["lap"]), ["eligibility_unknown"]) and ages.get(int(lap["lap"])) is not None and (_number(lap.get("lap_time_s")) or 0) > 0]
        pairs = []
        used = set()
        for latter in after:
            matches = [lap for lap in before if lap["lap"] not in used and abs(ages[int(lap["lap"])] - ages[int(latter["lap"])]) <= 2.0]
            if matches:
                former = min(matches, key=lambda lap: abs(ages[int(lap["lap"])] - ages[int(latter["lap"])]))
                used.add(former["lap"])
                pairs.append((former, latter))
        if len(pairs) >= 3:
            pairs = pairs[:5]
            baseline = sorted((pair[0] for pair in pairs), key=lambda lap: lap["lap"])
            compared = sorted((pair[1] for pair in pairs), key=lambda lap: lap["lap"])
            candidates.append((f"runs-{before_run['run_number']}-{after_run['run_number']}-age", "cross_run", baseline, compared, before_run, after_run))
    comparison_coverage = {"candidate_count": len(candidates), "generated_count": min(len(candidates), MAX_COMPARISONS),
                           "selection_policy": "At most 24 comparisons: retain the latest usable window and the latest fully screened usable window with their runs' early-run windows, the first and last four candidates, then usable comparisons by largest absolute median pace change before unusable ones. All recorded laps remain in the timeline."}
    if len(candidates) > MAX_COMPARISONS:
        flags = _lap_flags(analysis)
        usable, screened = [], []
        for _identifier, kind, before, after, _before_run, _after_run in candidates:
            numbers = (_lap_numbers(before), _lap_numbers(after))
            eligibility = _eligibility(*numbers, lap_exclusions)
            usable.append(not _pace_usability(kind, *numbers, eligibility, flags)[0])
            screened.append(usable[-1] and eligibility == "eligible")
        # Only a fully screened usable window can offer a saveable test, so the
        # latest one is retained beside the latest usable window; the default
        # is then chosen from what is shown (see _default_rank).
        def latest(indices: Sequence[int]) -> int | None:
            return max(indices, key=lambda i: (max(lap["lap"] for lap in candidates[i][3]), candidates[i][1] == "observed_finish"), default=None)
        pool = [i for i in range(len(candidates)) if usable[i]] or list(range(len(candidates)))
        retained = set(range(4)) | set(range(len(candidates) - 4, len(candidates)))
        leads = {index for index in (latest(pool), latest([i for i in range(len(candidates)) if screened[i]])) if index is not None}
        # A retained late window keeps its run's early-run window, whose test it may offer instead.
        early_by_run = {candidates[i][5].get("run_number"): i for i in range(len(candidates)) if candidates[i][1] == "early_run"}
        retained |= leads | {early_by_run[run] for i in leads
                             if candidates[i][1] in ("within_run", "observed_finish")
                             and (run := candidates[i][5].get("run_number")) in early_by_run}
        ranked = sorted(range(len(candidates)), key=lambda i: (usable[i], abs(_median([lap["lap_time_s"] for lap in candidates[i][3]]) - _median([lap["lap_time_s"] for lap in candidates[i][2]]))), reverse=True)
        for index in ranked:
            if len(retained) >= MAX_COMPARISONS:
                break
            retained.add(index)
        candidates = [candidate for i, candidate in enumerate(candidates) if i in retained]
    try:
        from .race_inspection import comparison_inspection, review_sections, run_views
    except ImportError:  # pragma: no cover - normal CLI/MCP script-loading path.
        from race_inspection import comparison_inspection, review_sections, run_views
    section_set = review_sections(analysis)
    sections = section_set["sections"]
    by_number = {int(lap["lap"]): lap for lap in laps}
    steering = basis_of(analysis)
    comparisons = []
    for identifier, kind, before, after, before_run, after_run in candidates:
        comparison = _comparison(identifier, kind, before, after, before_run, after_run, lap_exclusions, ages, traces, analysis, track, sections)
        if kind == "cross_run" and comparison["pace_comparable"]:
            comparison["summary"] += " Pairs are within two sampled green-lap equivalents since their run starts; tire age, fuel, cautions and conditions are not controlled."
        comparison["inspection"] = comparison_inspection(comparison, by_number, ages, run_for_lap, lap_exclusions, traces, sections,
                                                         steering=steering)
        comparison["actions"] = _actions(comparison, section_set, traces, steering=steering)
        comparisons.append(comparison)
    early_runs = {item["compared"]["run_number"]: item for item in comparisons if item["kind"] == "early_run"}
    for comparison in comparisons:
        early = early_runs.get(comparison["compared"]["run_number"]) if comparison["kind"] in ("within_run", "observed_finish") else None
        for action in comparison["actions"]:
            action["early_run_version"] = _early_run_version(action, early) if early is not None else None
    default = max(comparisons, key=_default_rank, default=None)
    lap_rows = [{"lap": int(lap["lap"]), "lap_time_s": _round(lap.get("lap_time_s")), "position": _number(_path(lap, "position", "end")),
                 "flag_state": str(lap.get("flag_state") or "unknown"), "eligible": not bool(lap_exclusions.get(int(lap["lap"]), ["eligibility_unknown"])),
                 "plot_pace": _plot_pace(lap),
                 "exclusion_reasons": list(lap_exclusions.get(int(lap["lap"]), ["eligibility_unknown"])), "run_number": run_for_lap.get(int(lap["lap"])),
                 "green_exposure": _round(ages.get(int(lap["lap"])), 3)} for lap in laps]
    for row, lap in zip(lap_rows, laps):
        observed_reasons = _observed_section_exclusions(lap, row["exclusion_reasons"])
        # Strictly eligible laps normally need no duplicate declaration. Emit the separate
        # observed screen whenever it differs or excludes a lap, keeping large races bounded.
        if not row["eligible"] or observed_reasons:
            row["observed_section_eligible"] = not observed_reasons
            if observed_reasons:
                row["observed_section_exclusion_reasons"] = observed_reasons
    return {
        "schema_version": SCHEMA_VERSION, "projection_version": PROJECTION_VERSION,
        "steering": steering.describe(),
        "status": "available" if comparisons else "insufficient_evidence",
        "summary": default["summary"] if default else "No two usable multi-lap windows are available yet; the recorded lap timeline remains inspectable.",
        "default_comparison_id": default["id"] if default else None,
        "comparisons": comparisons, "comparison_coverage": comparison_coverage,
        "laps": lap_rows,
        "section_laps": _observed_section_laps(lap_rows, traces, sections),
        "runs": run_views([row for row in lap_rows if row["plot_pace"]], laps),
        "sections": {key: value for key, value in section_set.items()},
        "timeline": _timeline(analysis, laps), "track_evolution": track,
        "limitations": ([f"Showing {comparison_coverage['generated_count']} of {comparison_coverage['candidate_count']} candidate comparisons. " + comparison_coverage["selection_policy"]] if comparison_coverage["candidate_count"] > MAX_COMPARISONS else []) + ["Window medians are observed comparisons, not estimates of a single cause.",
                        "Clean windows use the existing recorded eligibility screen; observed finish windows retain excluded laps and their reasons.",
                        "A comparison whose selected laps are all excluded, whose window spans a caution, or whose earlier and later laps a caution separates carries no pace delta or explanations and states why. Cross-run windows are matched by green exposure across a stop.",
                        "Cross-run exposure uses sampled green-time fractions from complete laps, not measured tire wear or exact physical green distance.",
                        f"A saved test needs one run long enough to reach its exposure window; a test day is assumed to give runs of up to {TEST_DAY_RUN_LAPS} laps. A run whose late laps need a longer run adds a first-{TEST_DAY_RUN_LAPS}-green-lap window of its own clean laps, which offers a test only when it passes the same screens.",
                        "Corner timing uses recorded distance-binned samples; absent boundaries, gaps or fewer than two observations withhold that metric. Local traffic is not controlled.",
                        "Complete SDK-scored green laps crossing the checkered transition stay in observed pace windows; their clean-screen exclusions remain visible."]
                       + ([f"Steering is steering-wheel angle, not road-wheel steering: {steering.reason or 'the steering ratio was not recorded.'}"]
                          if not steering.road_wheel else []),
    }

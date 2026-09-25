"""One review of a test day: every stint in time order, compared at equal tire age.

A test day is 2-12 archived stints of one car at one track configuration. Each
stint is one recording with one garage setup. The review measures, for every
stint, the median of clean laps in the same run-age windows (laps 2-5, and the
last five laps up to the shortest stint of at least eight laps), a least-squares
fall-off over laps 2..N with its residual scatter, the garage-setup change from
the previous stint (tire readings excluded), whether the two stints can be
compared at all, and the tire readings at the stint's stop with their sources.

Nothing here infers a setup direction from tire temperatures, and nothing is
called "unchanged": a difference inside the scatter threshold is Within
scatter. A missing measurement is Not measured (``None``), never zero.

The garage sheet's tire readings (``CarSetup`` ``LastTemps*``, ``TreadRemaining``,
``LastHotPressure``) are shown with their timing marked unverified. In the local
recordings checked (Iowa and New Hampshire, August 2026) the sheet matched the
recording's first telemetry sample in two races, showed an older uniform
garage state in five others, and never matched the stint's own final stall
reading, so it is never attributed to this stint's stop.
"""

from __future__ import annotations

import datetime as _datetime
import json
import math
import statistics
from pathlib import Path
from typing import Any, Mapping, Sequence

try:  # Package import and direct script loading are both supported.
    from .driving_trials import MATERIAL_FUEL_GAL, MATERIAL_TEMPERATURE_F
    from .path_security import path_is_within
    from .race_inspection import configuration_key, run_views
    from .race_investigation import _number, _path, _plot_pace, _round, _run_exposure
    from .setup_catalog import (
        _normalize_embedded_path, compare_setups, garage_setup_fields, garage_setup_fingerprint,
        tire_readings_from_setup,
    )
except ImportError:  # pragma: no cover - normal CLI/MCP script-loading path.
    from driving_trials import MATERIAL_FUEL_GAL, MATERIAL_TEMPERATURE_F
    from path_security import path_is_within
    from race_inspection import configuration_key, run_views
    from race_investigation import _number, _path, _plot_pace, _round, _run_exposure
    from setup_catalog import (
        _normalize_embedded_path, compare_setups, garage_setup_fields, garage_setup_fingerprint,
        tire_readings_from_setup,
    )


SCHEMA_VERSION = 1
CONTRACT = "test-day-review-v1"
MINIMUM_RUNS = 2
MAXIMUM_RUNS = 12
EARLY_WINDOW = (2, 5)
LATE_WINDOW_LAPS = 5
MINIMUM_LATE_RUN_LAPS = 8
MINIMUM_WINDOW_LAPS = 3
MINIMUM_THRESHOLD_S = 0.03
STANDARD_ERROR_MULTIPLIER = 2.0
# Standard error of a median of n normal observations is sqrt(pi/2) * sd / sqrt(n).
MEDIAN_STANDARD_ERROR_FACTOR = math.sqrt(math.pi / 2.0)
USED_TIRE_ODOMETER_M = 250.0
STOPPED_ON_TRACK_MPH = 15.0
# The sheet lists whole degrees Celsius (0.9 °F rounding) and whole percent.
SHEET_AGREEMENT_F = 2.0
SHEET_AGREEMENT_PERCENT = 1.0
LITRES_PER_GALLON = 3.785411784
TIRES = ("LF", "RF", "LR", "RR")
LEFT_TIRES = ("LF", "LR")
# Telemetry bands are numbered across the car from its left side. The garage
# sheet labels the same three numbers O-M-I on left tires and I-M-O on right
# tires (checked value-for-value against recorded New Hampshire pit readings).
_LEFT_BANDS = {"L": "outside", "M": "middle", "R": "inside"}
_RIGHT_BANDS = {"L": "inside", "M": "middle", "R": "outside"}
_OFFLINE_FLAG_REASONS = frozenset({"caution_or_mixed", "restart"})
_DIRECTION_SIGNS = {"forward": 1, "rearward": -1, "raise": 1, "lower": -1, "lower-equal": -1, "add-equal": 1}
_STOP_REASONS = {
    "unmeasured_final_run": "The run ended on track; there is no stall reading.",
    "unavailable_at_stop": "No tire reading was recorded at this stop.",
    "stale_or_unconfirmed_at_stop": "The stall reading did not change at this stop, so it is not a fresh measurement.",
}
SHEET_TIMING_NOTE = (
    "Garage-sheet readings carry no timestamp. In checked recordings they matched the recording's start "
    "or an older garage state and never this stint's own stall reading, so they are not attributed to this stop."
)
OFFLINE_FLAG_NOTE = (
    "Offline Testing holds iRacing's one-lap-to-green flag for the whole session, which the recording "
    "analysis reads as a caution. For Offline Testing stints the review ignores caution and restart "
    "classifications; every other exclusion still applies."
)


class TestDayError(ValueError):
    """A review request that cannot be satisfied; the message is shown to the driver."""

    __test__ = False  # Not a unittest case, despite the name.


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def _text(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _window_label(first: int | None, last: int | None) -> str | None:
    if first is None or last is None:
        return None
    return f"Lap {first}" if first == last else f"Laps {first}–{last}"


def _epoch(value: Any) -> float | None:
    text = _text(value)
    if text is None:
        return None
    try:
        moment = _datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=_datetime.timezone.utc)
    return moment.timestamp()


def _gallons(litres: Any) -> float | None:
    value = _number(litres)
    return _round(value / LITRES_PER_GALLON, 2) if value is not None else None


def _field_label(field: str) -> str:
    parts = [part for part in field.split(".") if part not in ("chassis", "tires", "setup")]
    if len(parts) >= 2 and parts[-1].startswith(parts[-2] + "_"):
        del parts[-2]  # chassis.front.front_brake_bias -> "Front brake bias"
    return " ".join(part.replace("_", " ") for part in parts).capitalize() or field


def _display(record: Mapping[str, Any] | None) -> str | None:
    if not isinstance(record, Mapping):
        return None
    raw = record.get("raw")
    if isinstance(raw, (list, tuple)):
        return ", ".join(str(item) for item in raw)
    return _text(raw)


# --------------------------------------------------------------------------
# One stint
# --------------------------------------------------------------------------


def _is_offline_testing(analysis: Mapping[str, Any]) -> bool:
    kind = _text(_path(analysis, "source", "selection", "sim_session_type"))
    if kind is not None:
        return kind.casefold() == "offline testing"
    event = _text(_path(analysis, "identity", "event_type"))
    return event is not None and event.casefold() == "test"


def _screen(analysis: Mapping[str, Any], offline: bool) -> dict[int, list[str]]:
    try:
        from .analysis_engine import investigation_lap_exclusions
    except ImportError:  # pragma: no cover
        from analysis_engine import investigation_lap_exclusions
    exclusions = investigation_lap_exclusions(analysis)
    by_lap = {int(lap["lap"]): lap for lap in analysis.get("laps") or ()
              if isinstance(lap, Mapping) and _number(lap.get("lap")) is not None}
    result: dict[int, list[str]] = {}
    for number, reasons in exclusions.items():
        kept = [reason for reason in reasons if not (offline and reason in _OFFLINE_FLAG_REASONS)]
        minimum = _number(_path(by_lap.get(number, {}), "speed", "minimum_mph"))
        # A car that stood still on track is not a pace sample, whatever else the lap shows.
        if minimum is not None and minimum < STOPPED_ON_TRACK_MPH and "pit" not in kept:
            kept.append("stopped_on_track")
        result[number] = kept
    return result


def _plotted(lap: Mapping[str, Any], offline: bool) -> bool:
    return _plot_pace({**lap, "flag_state": "green"} if offline else lap)


def _oriented(tire: str, values: Any) -> dict[str, float] | None:
    if not isinstance(values, Mapping):
        return None
    bands = _LEFT_BANDS if tire in LEFT_TIRES else _RIGHT_BANDS
    result: dict[str, float] = {}
    for key, band in bands.items():
        value = _number(values.get(key) if key in values else values.get("C" + key))
        if value is None:
            return None
        result[band] = round(value, 1)
    return {band: result[band] for band in ("inside", "middle", "outside")}


def _corner_summary(temperatures: Mapping[str, float] | None, tread: Mapping[str, float] | None) -> dict[str, Any]:
    return {
        "spread_f": _round(temperatures["inside"] - temperatures["outside"], 1) if temperatures else None,
        "minimum_tread_percent": _round(min(tread.values()), 1) if tread else None,
    }


def _stop_reading(run: Mapping[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any]]:
    status = _text((run or {}).get("tire_measurement_status"))
    observation = (run or {}).get("tire_observation")
    measured = status == "measured_at_stop" and isinstance(observation, Mapping) and isinstance(observation.get("tires"), Mapping)
    header = {
        "status": "measured" if measured else "not_measured",
        "source": "telemetry_pit_stall",
        "measurement_status": status,
        "reason": None if measured else _STOP_REASONS.get(status or "", "No stall reading is available for this run."),
    }
    corners: dict[str, Any] = {}
    for tire in TIRES:
        details = observation["tires"].get(tire) if measured else None
        if not isinstance(details, Mapping):
            corners[tire] = None
            continue
        temperatures = _oriented(tire, details.get("carcass_temperature_f") or details.get("temperature_f"))
        tread = _oriented(tire, details.get("remaining_percent"))
        pressure = details.get("pressure") if isinstance(details.get("pressure"), Mapping) else {}
        corners[tire] = {
            "temperature_f": temperatures, "tread_percent": tread,
            "pressure_psi": _round(pressure.get("psi"), 2), "pressure_kind": _text(pressure.get("kind")),
            **_corner_summary(temperatures, tread),
        }
    return header, corners


def _sheet_reading(setup: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    readings = tire_readings_from_setup(setup) if isinstance(setup, Mapping) and setup else {}
    corners: dict[str, Any] = {}
    for tire in TIRES:
        reading = readings.get(tire)
        if not isinstance(reading, Mapping):
            corners[tire] = None
            continue
        corners[tire] = {
            "temperature_f": reading.get("temperature_f"), "tread_percent": reading.get("tread_percent"),
            "hot_pressure_psi": reading.get("hot_pressure_psi"), "cold_pressure_psi": reading.get("cold_pressure_psi"),
            **_corner_summary(reading.get("temperature_f"), reading.get("tread_percent")),
        }
    recorded = any(corner is not None for corner in corners.values())
    return {"status": "recorded" if recorded else "not_recorded", "source": "garage_sheet",
            "timing": "unverified", "timing_note": SHEET_TIMING_NOTE}, corners


def _agreement(first: Mapping[str, Any] | None, second: Mapping[str, Any] | None) -> dict[str, Any]:
    temperature_deltas = [abs(first["temperature_f"][band] - second["temperature_f"][band])
                          for band in ("inside", "middle", "outside")
                          if first and second and first.get("temperature_f") and second.get("temperature_f")]
    tread_deltas = [abs(first["tread_percent"][band] - second["tread_percent"][band])
                    for band in ("inside", "middle", "outside")
                    if first and second and first.get("tread_percent") and second.get("tread_percent")]
    if not temperature_deltas and not tread_deltas:
        return {"status": "not_compared", "max_temperature_delta_f": None, "max_tread_delta_percent": None}
    agree = all(delta <= SHEET_AGREEMENT_F for delta in temperature_deltas) and all(
        delta <= SHEET_AGREEMENT_PERCENT for delta in tread_deltas)
    return {"status": "agree" if agree else "disagree",
            "max_temperature_delta_f": _round(max(temperature_deltas), 1) if temperature_deltas else None,
            "max_tread_delta_percent": _round(max(tread_deltas), 1) if tread_deltas else None}


def _change(current: Mapping[str, Any] | None, previous: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not current or not previous:
        return None
    def delta(key: str) -> dict[str, float] | None:
        if not current.get(key) or not previous.get(key):
            return None
        return {band: _round(current[key][band] - previous[key][band], 1) for band in ("inside", "middle", "outside")}
    return {"temperature_f": delta("temperature_f"), "tread_percent": delta("tread_percent")}


def _measured_run(analysis: Mapping[str, Any], screen: Mapping[int, Sequence[str]]) -> tuple[Mapping[str, Any] | None, list[dict[str, Any]], int]:
    """The run with the most screened laps of known run age, and its lap rows."""

    laps = sorted((lap for lap in analysis.get("laps") or () if isinstance(lap, Mapping) and (_number(lap.get("lap")) or 0) > 0),
                  key=lambda lap: lap["lap"])
    runs = sorted((run for run in analysis.get("runs") or () if isinstance(run, Mapping)
                   and _number(run.get("start_lap")) is not None and _number(run.get("end_lap")) is not None),
                  key=lambda run: (_number(run.get("start_lap")) or 0))
    best: tuple[tuple[int, int, int], Mapping[str, Any], list[dict[str, Any]]] | None = None
    for position, run in enumerate(runs):
        start, end = int(run["start_lap"]), int(run["end_lap"])
        run_laps = [lap for lap in laps if start <= int(lap["lap"]) <= end]
        exposure = _run_exposure(run_laps)
        rows = []
        for lap in run_laps:
            number = int(lap["lap"])
            known = exposure.get(number) is not None
            run_lap = number - start + 1 if known else None
            reasons = list(screen.get(number, ["eligibility_unknown"]))
            if run_lap == 1:
                reasons.append("first_lap_of_run")
            if run_lap is None:
                reasons.append("run_age_unknown")
            rows.append({"lap": number, "run_lap": run_lap, "lap_time_s": _round(lap.get("lap_time_s")),
                         "eligible": not reasons and (_number(lap.get("lap_time_s")) or 0) > 0,
                         "exclusion_reasons": reasons})
        clean = sum(1 for row in rows if row["eligible"])
        known_laps = max((row["run_lap"] for row in rows if row["run_lap"] is not None), default=0)
        rank = (clean, known_laps, -position)
        if best is None or rank > best[0]:
            best = (rank, run, rows)
    if best is None:
        return None, [], len(runs)
    return best[1], best[2], len(runs)


def _tire_start(analysis: Mapping[str, Any], run: Mapping[str, Any] | None) -> dict[str, Any]:
    runs = sorted((item for item in analysis.get("runs") or () if isinstance(item, Mapping)),
                  key=lambda item: (_number(item.get("start_lap")) or 0))
    if run is None:
        return {"status": "not_measured", "initial_odometer_m": None, "detail": None}
    earlier = [item for item in runs if (_number(item.get("start_lap")) or 0) < (_number(run.get("start_lap")) or 0)]
    if earlier:
        changed = {str(tire).upper() for tire in _path(earlier[-1], "tire_set_lifecycle", "confirmed_changed_at_run_end") or ()}
        if set(TIRES) <= changed:
            return {"status": "changed_before_run", "initial_odometer_m": None,
                    "detail": "All four tires were confirmed changed before this run."}
        return {"status": "carried_over", "initial_odometer_m": None,
                "detail": "This run followed an earlier run in the same recording without a confirmed four-tire change."}
    evidence = None
    for item in _path(analysis, "corner_tire_age", "runs") or ():
        candidate = _path(item, "tire_age_basis", "initial_zero_evidence", "initial_odometer_m")
        if isinstance(candidate, Mapping) and candidate:
            evidence = candidate
            break
    values = {tire: _round(evidence.get(tire), 1) for tire in TIRES if _number((evidence or {}).get(tire)) is not None} if evidence else {}
    if len(values) != len(TIRES):
        return {"status": "not_measured", "initial_odometer_m": values or None, "detail": None}
    highest = max(values.values())
    if highest > USED_TIRE_ODOMETER_M:
        return {"status": "possibly_used", "initial_odometer_m": values,
                "detail": f"Tire odometers read up to {highest:,.0f} m when lap 1 began (over {USED_TIRE_ODOMETER_M:.0f} m)."}
    return {"status": "no_prior_distance", "initial_odometer_m": values, "detail": None}


def _stint(analysis: Mapping[str, Any], index: int, analysis_path: str | None) -> dict[str, Any]:
    identity = analysis.get("identity")
    if not isinstance(identity, Mapping):
        raise TestDayError(f"Stint {index + 1} has no identity; refresh its analysis.")
    for field in ("laps", "runs"):
        if not isinstance(analysis.get(field), list):
            raise TestDayError(f"Stint {index + 1} has no {field} list; refresh its analysis.")
    recorded = _epoch(identity.get("session_start"))
    if recorded is None:
        raise TestDayError(f"Stint {index + 1} has no recording time (identity.session_start); refresh its analysis.")
    offline = _is_offline_testing(analysis)
    screen = _screen(analysis, offline)
    run, rows, run_count = _measured_run(analysis, screen)
    by_lap = {int(lap["lap"]): lap for lap in analysis["laps"] if isinstance(lap, Mapping) and _number(lap.get("lap")) is not None}
    for row in rows:
        # A lap where the car stood still is neither a pace sample nor a pace observation to draw.
        row["plotted"] = (_plotted(by_lap.get(row["lap"], {}), offline) and row["run_lap"] is not None
                          and "stopped_on_track" not in row["exclusion_reasons"])
    setup = identity.get("setup")
    has_setup = isinstance(setup, Mapping) and bool(setup)
    temperatures = [value for row in rows if (value := _number(_path(by_lap.get(row["lap"], {}), "conditions", "track_temperature_f"))) is not None]
    start_fuel = _gallons((run or {}).get("fuel", {}).get("start_l") if isinstance((run or {}).get("fuel"), Mapping) else None)
    if start_fuel is None and rows:
        start_fuel = _gallons(_path(by_lap.get(rows[0]["lap"], {}), "fuel", "start_l"))
    stop_header, stop_corners = _stop_reading(run)
    sheet_header, sheet_corners = _sheet_reading(setup)
    selection = _path(analysis, "source", "selection") or {}
    run_laps = max((row["run_lap"] for row in rows if row["run_lap"] is not None), default=0)
    return {
        "analysis": analysis, "index": index, "recorded": recorded, "offline": offline,
        "analysis_id": _text(analysis.get("analysis_id")), "analysis_path": analysis_path,
        "recorded_at": _text(identity.get("session_start")),
        "session_type": _text(selection.get("sim_session_type")) or _text(identity.get("event_type")),
        "test_day_id": _text(selection.get("test_day_id")),
        "test_day_run": selection.get("test_day_run") if isinstance(selection.get("test_day_run"), int) else None,
        "car_path": _text(identity.get("car_path")), "car_name": _text(identity.get("car_name")),
        "car_id": identity.get("car_id"),
        "track_name": _text(identity.get("track_name")), "track_config": _text(identity.get("track_config")),
        "configuration_key": configuration_key(analysis),
        "fixed": identity.get("is_fixed_setup") if isinstance(identity.get("is_fixed_setup"), bool) else None,
        "setup_name": _text(identity.get("setup_name")),
        "garage_fields": garage_setup_fields(setup)["fields"] if has_setup else None,
        "garage_fingerprint": (garage_setup_fingerprint(setup) or "")[:16] or None if has_setup else None,
        "compound": _text(identity.get("tire_compound")),
        "track_temp_f": _round(statistics.median(temperatures), 1) if temperatures else None,
        "start_fuel_gal": start_fuel,
        "tire_start": _tire_start(analysis, run),
        "run": run, "run_count": run_count, "rows": rows, "run_laps": run_laps,
        "stop": (stop_header, stop_corners), "sheet": (sheet_header, sheet_corners),
    }


# --------------------------------------------------------------------------
# Windows, fall-off and verdicts
# --------------------------------------------------------------------------


def _window(stint: Mapping[str, Any], first: int, last: int) -> dict[str, Any]:
    clean = [row for row in stint["rows"] if row["eligible"] and row["run_lap"] is not None and first <= row["run_lap"] <= last]
    result = {"first_run_lap": first, "last_run_lap": last, "label": _window_label(first, last),
              "clean_lap_count": len(clean), "laps": [row["lap"] for row in clean],
              "median_s": None, "status": "not_measured", "reason": None}
    if stint["run_laps"] < last:
        result["reason"] = (f"The run ended at lap {stint['run_laps']}, before lap {last}." if stint["run_laps"]
                            else "No run with known lap ages was recorded.")
    elif len(clean) < MINIMUM_WINDOW_LAPS:
        result["reason"] = f"{len(clean)} clean lap{'s' if len(clean) != 1 else ''} in {result['label'].lower()}; {MINIMUM_WINDOW_LAPS} are needed."
    else:
        result.update(status="measured", median_s=_round(statistics.median(row["lap_time_s"] for row in clean)))
    return result


def _fall_off(stint: Mapping[str, Any], last: int) -> dict[str, Any]:
    try:
        from .analysis_engine import _linear_slope
    except ImportError:  # pragma: no cover
        from analysis_engine import _linear_slope
    first = EARLY_WINDOW[0]
    end = min(last, stint["run_laps"])
    clean = [row for row in stint["rows"] if row["eligible"] and row["run_lap"] is not None and first <= row["run_lap"] <= end]
    points = [(float(row["run_lap"]), float(row["lap_time_s"])) for row in clean]
    slope = _linear_slope(points) if len(points) >= MINIMUM_WINDOW_LAPS else None
    result = {"first_run_lap": first, "last_run_lap": end if end >= first else None, "fit_lap_count": len(points),
              "laps": [row["lap"] for row in clean], "equal_age": stint["run_laps"] >= last,
              "slope_s_per_lap": None, "scatter_s": None, "status": "not_measured", "reason": None,
              "method": "ordinary_least_squares_on_run_lap"}
    if slope is None:
        span = _window_label(first, max(end, first)).lower()
        result["reason"] = f"{len(points)} clean lap{'s' if len(points) != 1 else ''} in {span}; {MINIMUM_WINDOW_LAPS} are needed."
        return result
    mean_x = statistics.fmean(x for x, _ in points)
    mean_y = statistics.fmean(y for _, y in points)
    intercept = mean_y - slope * mean_x
    residuals = [y - (intercept + slope * x) for x, y in points]
    scatter = math.sqrt(sum(value * value for value in residuals) / (len(points) - 2)) if len(points) > 2 else None
    result.update(status="measured", slope_s_per_lap=_round(slope, 4), scatter_s=_round(scatter, 4))
    return result


def _standard_error(fall_off: Mapping[str, Any], window: Mapping[str, Any]) -> float | None:
    scatter = _number(fall_off.get("scatter_s"))
    count = int(window.get("clean_lap_count") or 0)
    if scatter is None or count <= 0:
        return None
    return MEDIAN_STANDARD_ERROR_FACTOR * scatter / math.sqrt(count)


def _comparability(previous: Mapping[str, Any], current: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    """Blocking reasons, then non-blocking flags, for comparing two stints."""

    before, after = previous["number"], current["number"]
    reasons: list[str] = []
    flags: list[str] = []
    if previous["car_path"] != current["car_path"] or not current["car_path"]:
        reasons.append(f"Run {after} used a different car ({current['car_path'] or 'not recorded'}) than run {before} ({previous['car_path'] or 'not recorded'}).")
    if not previous["configuration_key"] or not current["configuration_key"]:
        reasons.append("The track configuration was not recorded for both runs.")
    elif previous["configuration_key"] != current["configuration_key"]:
        reasons.append(f"Run {after} was recorded on another track configuration than run {before}.")
    for item in (previous, current):
        if item["fixed"] is not False:
            reasons.append(f"Run {item['number']} was not recorded on an open setup." if item["fixed"] else
                           f"Run {item['number']} does not record whether its setup was open.")
    if previous["track_temp_f"] is None or current["track_temp_f"] is None:
        missing = before if previous["track_temp_f"] is None else after
        reasons.append(f"Track temperature was not recorded for run {missing}.")
    elif abs(current["track_temp_f"] - previous["track_temp_f"]) >= MATERIAL_TEMPERATURE_F:
        reasons.append(f"Track temperature {previous['track_temp_f']:.0f} → {current['track_temp_f']:.0f} °F "
                       f"({MATERIAL_TEMPERATURE_F:.0f} °F or more apart).")
    if previous["start_fuel_gal"] is None or current["start_fuel_gal"] is None:
        missing = before if previous["start_fuel_gal"] is None else after
        reasons.append(f"Starting fuel was not recorded for run {missing}.")
    elif abs(current["start_fuel_gal"] - previous["start_fuel_gal"]) >= MATERIAL_FUEL_GAL:
        reasons.append(f"Starting fuel {previous['start_fuel_gal']:.1f} → {current['start_fuel_gal']:.1f} gal "
                       f"({MATERIAL_FUEL_GAL:.0f} gal or more apart).")
    if previous["compound"] and current["compound"] and previous["compound"] != current["compound"]:
        reasons.append(f"Tire compound {previous['compound']} → {current['compound']}.")
    elif not previous["compound"] or not current["compound"]:
        flags.append(f"Tire compound is not recorded for run {before if not previous['compound'] else after}"
                     + (f" or run {after}." if not previous["compound"] and not current["compound"] else "."))
    for item in (previous, current):
        status = item["tire_start"]["status"]
        if status == "possibly_used":
            flags.append(f"Run {item['number']} may have started on used tires: {item['tire_start']['detail']}")
        elif status == "carried_over":
            flags.append(f"Run {item['number']}: {item['tire_start']['detail']}")
    return reasons, flags


def _compare(previous: Mapping[str, Any], current: Mapping[str, Any]) -> dict[str, Any]:
    reasons, flags = _comparability(previous, current)
    result: dict[str, Any] = {"compared_with_run": previous["number"], "verdict": "not_comparable", "blocked_by": None, "basis": None,
                              "window_label": None, "first_run_lap": None, "last_run_lap": None,
                              "previous_median_s": None, "current_median_s": None, "delta_s": None,
                              "threshold_s": None, "standard_error_s": None, "reasons": reasons, "flags": flags,
                              "summary": None}
    if reasons:
        result.update(blocked_by="conditions", summary=f"Not comparable with run {previous['number']}: " + " ".join(reasons))
        return result
    basis = next((key for key in ("late", "early")
                  if previous[key]["status"] == "measured" and current[key]["status"] == "measured"), None)
    if basis is None:
        reason = f"Neither pace window has {MINIMUM_WINDOW_LAPS}+ clean laps in both runs."
        result.update(blocked_by="measurement", reasons=[reason], summary=f"Not comparable with run {previous['number']}: {reason}")
        return result
    errors = [_standard_error(item["fall_off"], item[basis]) for item in (previous, current)]
    combined = math.sqrt(sum(value * value for value in errors)) if all(value is not None for value in errors) else None
    if combined is None:
        reason = "The lap-time scatter needed for a threshold is not measured for both runs."
        result.update(blocked_by="measurement", reasons=[reason], summary=f"Not comparable with run {previous['number']}: {reason}")
        return result
    threshold = round(max(MINIMUM_THRESHOLD_S, STANDARD_ERROR_MULTIPLIER * combined), 3)
    before, after = previous[basis]["median_s"], current[basis]["median_s"]
    delta = round(after - before, 3)
    verdict = "faster" if delta < -threshold else "slower" if delta > threshold else "within_scatter"
    window = current[basis]
    words = {"faster": f"{-delta:.3f} s faster than", "slower": f"{delta:.3f} s slower than",
             "within_scatter": f"within the {threshold:.3f} s scatter threshold of"}[verdict]
    result.update(verdict=verdict, basis=f"{basis}_window", window_label=window["label"],
                  first_run_lap=window["first_run_lap"], last_run_lap=window["last_run_lap"],
                  previous_median_s=before, current_median_s=after, delta_s=delta, threshold_s=threshold,
                  standard_error_s=_round(combined, 4), reasons=[],
                  summary=(f"{window['label']}: median {after:.3f} s, {words} run {previous['number']} ({before:.3f} s)"
                           + ("." if verdict == "within_scatter" else f"; threshold {threshold:.3f} s.")))
    return result


# --------------------------------------------------------------------------
# Setup changes and the next-change card
# --------------------------------------------------------------------------


def _setup_change(previous: Mapping[str, Any] | None, current: Mapping[str, Any]) -> dict[str, Any]:
    if previous is None:
        return {"status": "first_run", "compared_with_run": None, "change_count": 0, "changes": [],
                "unknown_count": 0, "unknown_fields": [], "complete": True, "reason": None}
    result = {"status": "unknown", "compared_with_run": previous["number"], "change_count": 0, "changes": [],
              "unknown_count": 0, "unknown_fields": [], "complete": False, "reason": None}
    missing = [item["number"] for item in (previous, current) if item["garage_fields"] is None]
    if missing:
        result["reason"] = "No embedded garage setup was recorded for run " + " or ".join(str(number) for number in missing) + "."
        return result
    comparison = compare_setups({"fields": previous["garage_fields"]}, {"fields": current["garage_fields"]}, max_output=2000)
    changes = []
    for item in comparison["differences"]:
        delta = item.get("delta_left_minus_right")
        changes.append({"field": item["field"], "label": _field_label(item["field"]),
                        "previous": _display(item["left"]), "current": _display(item["right"]),
                        "delta": _round(-delta, 4) if _number(delta) is not None else None,
                        "unit": item["right"].get("unit") if isinstance(item.get("right"), Mapping) else None})
    unknown = sorted(set(comparison["only_left"]) | set(comparison["only_right"]))
    unknown_count = comparison["summary"]["only_left_fields"] + comparison["summary"]["only_right_fields"]
    complete = not comparison["output_truncated"]
    status = "changed" if changes else "unknown" if unknown_count else "unchanged"
    result.update(status=status, change_count=comparison["summary"]["different_fields"], changes=changes,
                  unknown_count=unknown_count, unknown_fields=unknown, complete=complete,
                  reason=(f"{unknown_count} setting{'s' if unknown_count != 1 else ''} recorded in only one of the two runs."
                          if unknown_count else None))
    return result


def _ruleset_settings(rules: Mapping[str, Any] | None, car_path: str | None) -> list[dict[str, Any]]:
    if not isinstance(rules, Mapping):
        return []
    try:
        from .tuning_engine import _ruleset_supports_car
    except ImportError:  # pragma: no cover
        from tuning_engine import _ruleset_supports_car
    if not _ruleset_supports_car(rules, car_path):
        return []
    settings = []
    for setting in rules.get("settings") or ():
        if not isinstance(setting, Mapping):
            continue
        paths = [_normalize_embedded_path(str(path).split(".")) for path in setting.get("paths") or ()]
        if paths and all(paths):
            settings.append({**setting, "normalized_paths": paths})
    return settings


def _step_suggestion(rules: Mapping[str, Any] | None, car_path: str | None, changes: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    """One more garage step in the same direction, only when the ruleset names it."""

    fields = {change["field"] for change in changes}
    deltas = [_number(change.get("delta")) for change in changes]
    if not changes or len(fields) != len(changes) or any(delta in (None, 0.0) for delta in deltas):
        return None
    signs = {1 if delta > 0 else -1 for delta in deltas}
    if len(signs) != 1:
        return None
    sign = signs.pop()
    for setting in _ruleset_settings(rules, car_path):
        if set(setting["normalized_paths"]) != fields:
            continue
        for direction, text in (setting.get("directions") or {}).items():
            if _DIRECTION_SIGNS.get(direction) == sign and isinstance(text, Mapping):
                return {"ruleset_id": rules.get("ruleset_id"), "setting_id": setting.get("setting_id"),
                        "direction": direction, "instruction": text.get("instruction"),
                        "expected_effect": text.get("expected_effect"), "risk": text.get("risk"),
                        "step_verified": setting.get("verified_step") is not None}
    return None


def _logical_change_count(rules: Mapping[str, Any] | None, car_path: str | None, changes: Sequence[Mapping[str, Any]]) -> int:
    """Changed settings, counting a ruleset pair changed together (both ends) as one."""

    fields = {change["field"] for change in changes}
    for setting in _ruleset_settings(rules, car_path):
        paths = set(setting["normalized_paths"])
        if len(paths) > 1 and paths == fields:
            return 1
    return len(fields)


def _isolation_order(rules: Mapping[str, Any] | None, car_path: str | None, changes: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    ranked = {path: index for index, setting in enumerate(_ruleset_settings(rules, car_path))
              for path in setting["normalized_paths"]}
    return sorted(changes, key=lambda change: (ranked.get(change["field"], len(ranked)), change["field"]))


def _success(reference: Mapping[str, Any], window_key: str | None, threshold: float | None, direction: str) -> dict[str, Any]:
    window = reference.get(window_key) if window_key else None
    if not isinstance(window, Mapping):
        window = reference["late"] if reference["late"]["status"] == "measured" else reference["early"]
    median = window.get("median_s")
    limit = round(threshold if threshold is not None else MINIMUM_THRESHOLD_S, 3)
    label = window.get("label") or _window_label(*EARLY_WINDOW)
    if median is None or direction == "measure":
        text = (f"Record {MINIMUM_WINDOW_LAPS}+ clean laps on {label.lower()} with starting fuel within "
                f"{MATERIAL_FUEL_GAL:.0f} gal and track temperature within {MATERIAL_TEMPERATURE_F:.0f} °F of run {reference['number']}"
                + (f" ({median:.3f} s median)." if median is not None else "."))
        direction = "measure"
    elif direction == "within":
        text = f"Repeat {median:.3f} s median on {label.lower()} within ±{limit:.3f} s with {MINIMUM_WINDOW_LAPS}+ clean laps."
    else:
        text = f"Beat {median:.3f} s median on {label.lower()} by at least {limit:.3f} s with {MINIMUM_WINDOW_LAPS}+ clean laps."
    return {"direction": direction, "reference_run": reference["number"], "reference_median_s": median,
            "window_label": label, "first_run_lap": window.get("first_run_lap"), "last_run_lap": window.get("last_run_lap"),
            "threshold_s": limit, "minimum_clean_laps": MINIMUM_WINDOW_LAPS, "text": text}


def _card(kind: str, title: str, instruction: str, runs: Sequence[int], success: Mapping[str, Any], evidence: Sequence[str],
          changes: Sequence[Mapping[str, Any]] = (), ruleset: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {"kind": kind, "title": title, "instruction": instruction, "based_on_runs": list(runs),
            "changes": [dict(item) for item in changes], "ruleset_step": dict(ruleset) if ruleset else None,
            "success_rule": dict(success), "evidence": [item for item in evidence if item],
            "note": "No setup direction is inferred from tire temperatures or wear."}


def _next_change(stints: Sequence[Mapping[str, Any]], rules: Mapping[str, Any] | None) -> dict[str, Any]:
    latest, previous = stints[-1], stints[-2]
    comparison = latest["comparison"]
    change = latest["setup_change"]
    known = change["status"] == "changed" and change["complete"] and not change["unknown_count"]
    basis = (comparison.get("basis") or "").replace("_window", "") or None
    threshold = comparison.get("threshold_s")
    number, before = latest["number"], previous["number"]

    if comparison["verdict"] == "not_comparable":
        if comparison["blocked_by"] == "measurement":
            reference = previous if any(previous[key]["status"] == "measured" for key in ("late", "early")) else latest
            instruction = (f"Drive run {number}'s setup again, unchanged, for at least {EARLY_WINDOW[1]} laps so it can be "
                           f"compared with run {before}." if reference is previous else
                           f"Run {before} has too few clean laps to compare. Drive run {number}'s setup again, unchanged, "
                           f"for at least {EARLY_WINDOW[1]} laps to confirm its pace.")
            return _card("repeat_comparable", f"Repeat run {number} with more clean laps", instruction,
                         [before, number], _success(reference, None, None, "measure"), comparison["reasons"])
        return _card("repeat_comparable", f"Repeat run {number} under comparable conditions",
                     f"Drive run {number}'s setup again with conditions matching run {before}, so the change can be measured.",
                     [before, number], _success(previous, None, None, "measure"), comparison["reasons"])

    if comparison["verdict"] == "slower" and change["status"] == "changed" and change["changes"]:
        reverts = [{"field": item["field"], "label": item["label"], "from": item["current"], "to": item["previous"]}
                   for item in change["changes"]]
        title = (f"Revert {reverts[0]['label'].lower()} to {reverts[0]['to']}" if len(reverts) == 1
                 else f"Revert the {len(reverts)} changes to run {before}'s values")
        return _card("revert", title, f"Return exactly to run {before}'s values; run {number} was slower.",
                     [before, number], _success(latest, basis, threshold, "faster"), [comparison["summary"]], reverts)

    best = None
    if latest["late"]["status"] == "measured":
        for earlier in stints[:-1]:
            if earlier["late"]["status"] != "measured":
                continue
            pair = _compare(earlier, latest)
            if pair["verdict"] == "slower" and pair["basis"] == "late_window" and (
                    best is None or earlier["late"]["median_s"] < best[0]["late"]["median_s"]):
                best = (earlier, pair)
    if best is not None:
        earlier, pair = best
        target = _setup_change(earlier, latest)
        if target["status"] != "unchanged":
            returns = [{"field": item["field"], "label": item["label"], "from": item["current"], "to": item["previous"]}
                       for item in target["changes"]]
            detail = (f"Load run {earlier['number']}'s setup ({earlier['setup_name']})." if earlier["setup_name"]
                      else f"Load run {earlier['number']}'s setup.")
            if target["status"] == "unknown":
                detail += " Its differences from run " + str(number) + " are not fully recorded."
            return _card("return_to_setup", f"Return to run {earlier['number']}'s setup", detail,
                         [earlier["number"], number], _success(latest, "late", pair["threshold_s"], "faster"),
                         [f"Run {earlier['number']} still has the best late pace. " + (pair["summary"] or "")], returns)

    logical = _logical_change_count(rules, latest["car_path"], change["changes"]) if known else None
    if comparison["verdict"] == "faster" and logical == 1:
        kept = change["changes"]
        step = _step_suggestion(rules, latest["car_path"], kept)
        label = kept[0]["label"].lower() if len(kept) == 1 else "the " + " and ".join(item["label"].lower() for item in kept)
        values = ", ".join(item["current"] or "?" for item in kept)
        if step is not None:
            return _card("keep_and_step", f"Keep {label}; try one more step",
                         f"{step['instruction']} Expected: {step['expected_effect']} Risk: {step['risk']}",
                         [before, number], _success(latest, basis, threshold, "faster"), [comparison["summary"]],
                         [{"field": item["field"], "label": item["label"], "from": item["current"], "to": None} for item in kept],
                         step)
        return _card("keep", f"Keep {label} at {values}", f"Keep run {number}'s setup and repeat it to confirm the gain.",
                     [before, number], _success(latest, basis, threshold, "within"), [comparison["summary"]])

    if known and (logical or 0) >= 2:
        ordered = _isolation_order(rules, latest["car_path"], change["changes"])
        keep = ordered[0]
        reverts = [{"field": item["field"], "label": item["label"], "from": item["current"], "to": item["previous"]}
                   for item in ordered[1:]]
        return _card("isolate", f"Test {keep['label'].lower()} alone",
                     f"Return to run {before}'s setup with only {keep['label'].lower()} at {keep['current']}; "
                     f"run {number} changed {len(ordered)} settings at once.",
                     [before, number], _success(previous, basis, threshold, "faster"), [comparison["summary"]], reverts)

    evidence = [comparison["summary"]]
    if change["status"] == "unchanged":
        evidence.append(f"No garage setting changed between runs {before} and {number}; the difference is not attributed to setup.")
    elif change["status"] == "unknown":
        evidence.append(change["reason"] or "The setup comparison is incomplete.")
    return _card("repeat", f"Repeat run {number} unchanged", f"Drive run {number}'s setup again, unchanged, to measure its repeatability.",
                 [before, number], _success(latest, basis, threshold, "within"), evidence)


# --------------------------------------------------------------------------
# The review
# --------------------------------------------------------------------------


def _chart_trend(stint: Mapping[str, Any]) -> dict[str, Any] | None:
    points = [{"lap": row["lap"], "run_number": stint["number"], "lap_time_s": row["lap_time_s"],
               "eligible": row["eligible"], "green_exposure": float(row["run_lap"])}
              for row in stint["rows"] if row["plotted"] and row["run_lap"] is not None and row["lap_time_s"]]
    laps = [lap for lap in stint["analysis"]["laps"] if isinstance(lap, Mapping)]
    views = run_views(points, laps)
    if not views:
        return None
    view = dict(views[0])
    view["x_axis"] = "run_lap"
    return view


def _tires(stint: Mapping[str, Any], previous: Mapping[str, Any] | None) -> dict[str, Any]:
    stop_header, stop_corners = stint["stop"]
    sheet_header, sheet_corners = stint["sheet"]
    previous_corners = previous["stop"][1] if previous else {}
    corners = {}
    for tire in TIRES:
        corners[tire] = {
            "stop": stop_corners.get(tire), "sheet": sheet_corners.get(tire),
            "sheet_vs_stop": _agreement(sheet_corners.get(tire), stop_corners.get(tire)),
            "sheet_vs_previous_stop": _agreement(sheet_corners.get(tire), previous_corners.get(tire)),
            "change_from_previous_stop": _change(stop_corners.get(tire), previous_corners.get(tire)),
        }
    return {"stop_reading": stop_header, "sheet_reading": sheet_header, "corners": corners}


def _row(stint: Mapping[str, Any], previous: Mapping[str, Any] | None) -> dict[str, Any]:
    run = stint["run"]
    measured = None
    if run is not None:
        measured = {"analysis_run": run.get("run_number"), "first_lap": int(run["start_lap"]), "last_lap": int(run["end_lap"]),
                    "run_laps": stint["run_laps"], "other_run_count": max(0, stint["run_count"] - 1)}
    return {
        "run": stint["number"], "analysis_id": stint["analysis_id"], "analysis_path": stint["analysis_path"],
        "recorded_at": stint["recorded_at"], "session_type": stint["session_type"],
        "test_day_id": stint["test_day_id"], "test_day_run": stint["test_day_run"],
        "offline_flag_rule_applied": stint["offline"],
        "setup": {"name": stint["setup_name"], "fixed": stint["fixed"], "embedded": stint["garage_fields"] is not None,
                  "garage_fingerprint": stint["garage_fingerprint"]},
        "measured_run": measured,
        "conditions": {"track_temp_f": stint["track_temp_f"], "start_fuel_gal": stint["start_fuel_gal"],
                       "tire_compound": stint["compound"], "tire_start": stint["tire_start"]},
        "early": stint["early"], "late": stint["late"], "fall_off": stint["fall_off"],
        "setup_change": stint["setup_change"], "comparison": stint["comparison"],
        "tires": _tires(stint, previous),
        "laps": [{key: row[key] for key in ("lap", "run_lap", "lap_time_s", "eligible", "plotted", "exclusion_reasons")}
                 | {"label": f"Run {stint['number']} · lap {row['run_lap']}" if row["run_lap"] is not None else f"Run {stint['number']} · recording lap {row['lap']}"}
                 for row in stint["rows"]],
        "trend": _chart_trend(stint),
    }


def build_test_day_review(analyses: Sequence[Mapping[str, Any]], rules: Mapping[str, Any] | None = None, *,
                          analysis_paths: Sequence[str | None] | None = None) -> dict[str, Any]:
    """Review 2-12 stints of one test day. Pure: reads the analyses, writes nothing."""

    if isinstance(analyses, (str, bytes, Mapping)) or not isinstance(analyses, Sequence):
        raise TestDayError("A test-day review needs a list of stint analyses.")
    if not MINIMUM_RUNS <= len(analyses) <= MAXIMUM_RUNS:
        raise TestDayError(f"A test-day review needs {MINIMUM_RUNS}–{MAXIMUM_RUNS} stints; {len(analyses)} were supplied.")
    paths = list(analysis_paths) if analysis_paths is not None else [None] * len(analyses)
    if len(paths) != len(analyses):
        raise TestDayError("Each stint needs exactly one analysis path.")
    stints = []
    for index, analysis in enumerate(analyses):
        if not isinstance(analysis, Mapping):
            raise TestDayError(f"Stint {index + 1} is not an analysis object.")
        stints.append(_stint(analysis, index, paths[index]))
    identifiers = [item["analysis_id"] for item in stints if item["analysis_id"]]
    if len(identifiers) != len(set(identifiers)):
        raise TestDayError("The same recording was supplied more than once.")
    stints.sort(key=lambda item: (item["recorded"], item["analysis_id"] or "", item["index"]))
    for number, stint in enumerate(stints, start=1):
        stint["number"] = number

    first = stints[0]
    members = [item for item in stints if item["car_path"] == first["car_path"]
               and item["configuration_key"] == first["configuration_key"] and item["run"] is not None]
    long_runs = [item for item in members if item["run_laps"] >= MINIMUM_LATE_RUN_LAPS]
    if long_runs:
        shortest = min(long_runs, key=lambda item: (item["run_laps"], item["number"]))
        late_last = shortest["run_laps"]
        late = {"status": "available", "first_run_lap": late_last - LATE_WINDOW_LAPS + 1, "last_run_lap": late_last,
                "label": _window_label(late_last - LATE_WINDOW_LAPS + 1, late_last), "reason": None,
                "basis": f"Shortest run of at least {MINIMUM_LATE_RUN_LAPS} laps: run {shortest['number']} ({late_last} laps)."}
        fall_last = late_last
    else:
        late = {"status": "withheld", "first_run_lap": None, "last_run_lap": None, "label": None,
                "reason": f"No run reached {MINIMUM_LATE_RUN_LAPS} laps, so a late window would overlap laps 2–5.", "basis": None}
        fall_last = EARLY_WINDOW[1]
    for stint in stints:
        stint["early"] = _window(stint, *EARLY_WINDOW)
        if late["status"] == "available":
            stint["late"] = _window(stint, late["first_run_lap"], late["last_run_lap"])
        else:
            stint["late"] = {"first_run_lap": None, "last_run_lap": None, "label": None, "clean_lap_count": 0, "laps": [],
                             "median_s": None, "status": "not_measured", "reason": late["reason"]}
        stint["fall_off"] = _fall_off(stint, fall_last)
    previous = None
    for stint in stints:
        stint["setup_change"] = _setup_change(previous, stint)
        stint["comparison"] = _compare(previous, stint) if previous is not None else None
        previous = stint

    rows = [_row(stint, stints[index - 1] if index else None) for index, stint in enumerate(stints)]
    offline_runs = [stint["number"] for stint in stints if stint["offline"]]
    limitations = [
        "Pace windows count laps from the start of each stint's measured run; they are equal run age, not measured tire wear.",
        "Verdicts compare medians against a threshold of max(0.03 s, 2 × combined standard error); a difference inside it is within scatter, not unchanged.",
        "Track evolution, driver adaptation and traffic are not controlled between stints.",
        SHEET_TIMING_NOTE,
    ]
    if offline_runs:
        limitations.append(OFFLINE_FLAG_NOTE)
    if not isinstance(rules, Mapping):
        limitations.append("The tuning ruleset is unavailable, so no additional garage step is suggested.")
    test_days = {stint["test_day_id"] for stint in stints if stint["test_day_id"]}
    return {
        "ok": True, "schema_version": SCHEMA_VERSION, "contract": CONTRACT, "read_only": True,
        "run_count": len(stints),
        "test_day_id": test_days.pop() if len(test_days) == 1 and all(stint["test_day_id"] for stint in stints) else None,
        "car": {"car_path": first["car_path"], "car_name": first["car_name"], "car_id": first["car_id"]},
        "track": {"track_name": first["track_name"], "track_config": first["track_config"],
                  "configuration_key": first["configuration_key"]},
        "windows": {
            "early": {"first_run_lap": EARLY_WINDOW[0], "last_run_lap": EARLY_WINDOW[1], "label": _window_label(*EARLY_WINDOW)},
            "late": late,
            "fall_off": {"first_run_lap": EARLY_WINDOW[0], "last_run_lap": fall_last, "label": _window_label(EARLY_WINDOW[0], fall_last)},
            "minimum_clean_laps": MINIMUM_WINDOW_LAPS,
        },
        "screening": {
            "rule": ("Clean laps use the recording's investigation screen (partial, pit, caution, restart, off-track, "
                     "close-traffic, incident, damage and timing exclusions). Lap 1 of each run and laps where the car "
                     f"stopped on track (below {STOPPED_ON_TRACK_MPH:.0f} mph off pit road) are also excluded; stopped laps are not plotted."),
            "offline_flag_rule_runs": offline_runs,
            "offline_flag_rule": OFFLINE_FLAG_NOTE if offline_runs else None,
        },
        "verdict_rule": {"minimum_threshold_s": MINIMUM_THRESHOLD_S, "standard_error_multiplier": STANDARD_ERROR_MULTIPLIER,
                         "median_standard_error_factor": round(MEDIAN_STANDARD_ERROR_FACTOR, 4),
                         "scatter": "Residual standard deviation of the fall-off fit over laps 2..N.",
                         "comparison": "Late window when both runs have it, otherwise laps 2–5."},
        "comparability_rule": {"track_temperature_f": MATERIAL_TEMPERATURE_F, "start_fuel_gal": MATERIAL_FUEL_GAL,
                               "used_tire_odometer_m": USED_TIRE_ODOMETER_M,
                               "requires": ["exact car path", "exact track configuration", "open setup",
                                            "track temperature within the limit", "starting fuel within the limit",
                                            "same tire compound when recorded"]},
        "runs": rows,
        "next_change": _next_change(stints, rules),
        "limitations": limitations,
    }


# --------------------------------------------------------------------------
# Workflow entry point
# --------------------------------------------------------------------------


def _read_analysis(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise TestDayError(f"The analysis could not be read: {exc}") from exc
    if not isinstance(value, dict) or not isinstance(value.get("identity"), Mapping):
        raise TestDayError(f"The analysis has an invalid shape: {path}")
    return value


def review_test_day_workflow(*, analysis_paths: Sequence[str], archive_root: str) -> dict[str, Any]:
    """Read-only test-day review of archived stint analyses under ``archive_root``."""

    if isinstance(analysis_paths, (str, bytes)) or not isinstance(analysis_paths, Sequence):
        raise TestDayError("analysis_paths must be a list of archived analysis.json paths.")
    if not MINIMUM_RUNS <= len(analysis_paths) <= MAXIMUM_RUNS:
        raise TestDayError(f"A test-day review needs {MINIMUM_RUNS}–{MAXIMUM_RUNS} stints; {len(analysis_paths)} were supplied.")
    reports = (Path(archive_root) / "reports").resolve()
    resolved: list[Path] = []
    for raw in analysis_paths:
        path = Path(str(raw)).expanduser().resolve()
        if not path_is_within(path, reports) or path.name.lower() != "analysis.json":
            raise TestDayError(f"Each analysis path must name an archived analysis.json within {reports}.")
        if path in resolved:
            raise TestDayError("The same analysis was supplied more than once.")
        resolved.append(path)
    analyses = [_read_analysis(path) for path in resolved]
    try:
        try:
            from .tuning_engine import load_structured_tuning_rules
        except ImportError:  # pragma: no cover
            from tuning_engine import load_structured_tuning_rules
        rules: Mapping[str, Any] | None = load_structured_tuning_rules()
    except ValueError:
        rules = None
    return build_test_day_review(analyses, rules, analysis_paths=[str(path) for path in resolved])


__all__ = [
    "CONTRACT",
    "SCHEMA_VERSION",
    "TestDayError",
    "build_test_day_review",
    "review_test_day_workflow",
]

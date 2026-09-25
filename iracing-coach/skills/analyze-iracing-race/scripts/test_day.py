"""One review of a test day: every run in time order, compared at equal tire age.

A test day is 1-12 archived recordings of one car at one track configuration
holding two or more timed runs. A run is the laps between two pit visits, so
one recording can hold several (an official practice split by garage and pit
visits) and every timed run with three or more clean laps is a row; a
recording without a timed lap, and a shorter run (the one- or two-lap stubs
between resets), are listed once, apart from the rows. Each recording keeps
one garage sheet. The review measures, for every run, the median of clean
laps 2-5 and of its own last five clean laps (from lap 6 on), a least-squares
fall-off from lap 6 (after the warm-up of laps 2-5) to its last clean lap,
and its lap noise after lap 5: the larger of the lap-to-lap spread and the
fall-off fit's residual. Two runs are compared over their common clean length
N: laps 2-5, the last five laps up to N (withheld below eight laps so it never
overlaps laps 2-5), and the fall-off over laps 6..N (withheld below ten), each
against a threshold built from the noise, widened when laps wander together
and never below what two other runs of the day on one setup differed by.
It also states the garage-setup change from the previous run (tire readings
and computed readouts excluded; not recorded between two runs of one
recording), whether the two runs can be compared at all, and the tire
readings at the run's stop with their sources.

Nothing here infers a setup direction from tire temperatures, and nothing is
called "unchanged": a difference inside the noise threshold is Within
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
        READOUT_ADJUSTER_LEAVES, _normalize_embedded_path, compare_setups, garage_setup_fields,
        garage_setup_fingerprint, is_garage_readout_field, tire_readings_from_setup,
    )
except ImportError:  # pragma: no cover - normal CLI/MCP script-loading path.
    from driving_trials import MATERIAL_FUEL_GAL, MATERIAL_TEMPERATURE_F
    from path_security import path_is_within
    from race_inspection import configuration_key, run_views
    from race_investigation import _number, _path, _plot_pace, _round, _run_exposure
    from setup_catalog import (
        READOUT_ADJUSTER_LEAVES, _normalize_embedded_path, compare_setups, garage_setup_fields,
        garage_setup_fingerprint, is_garage_readout_field, tire_readings_from_setup,
    )


SCHEMA_VERSION = 1
CONTRACT = "test-day-review-v1"
MINIMUM_RECORDINGS = 1
MAXIMUM_RECORDINGS = 12
MINIMUM_RUNS = 2
# A long practice split by many pit visits keeps its latest runs; the review says how many it left out.
MAXIMUM_ROWS = 24
EARLY_WINDOW = (2, 5)
LATE_WINDOW_LAPS = 5
MINIMUM_WINDOW_LAPS = 3
# A late window needs MINIMUM_WINDOW_LAPS laps after laps 2-5, so it never overlaps them.
MINIMUM_LATE_RUN_LAPS = EARLY_WINDOW[1] + MINIMUM_WINDOW_LAPS
# A timed run with fewer clean laps than one window needs is listed apart, never a row or a plot.
MINIMUM_COMPARED_CLEAN_LAPS = MINIMUM_WINDOW_LAPS
# Laps 2-5 are the tires coming in. Lap noise, fit residuals and fall-off start at lap 6.
NOISE_FIRST_RUN_LAP = EARLY_WINDOW[1] + 1
FALL_OFF_FIRST_RUN_LAP = NOISE_FIRST_RUN_LAP
# A fall-off fit (and the residual spread it measures) needs five clean laps from lap 6 on.
MINIMUM_FIT_LAPS = LATE_WINDOW_LAPS
MINIMUM_FALL_OFF_RUN_LAPS = FALL_OFF_FIRST_RUN_LAP + MINIMUM_FIT_LAPS - 1
MINIMUM_THRESHOLD_S = 0.03
MINIMUM_SLOPE_THRESHOLD_S = 0.002
STANDARD_ERROR_MULTIPLIER = 2.0
# Standard error of a median of n normal observations is sqrt(pi/2) * sd / sqrt(n).
MEDIAN_STANDARD_ERROR_FACTOR = math.sqrt(math.pi / 2.0)
# Lap noise is the larger of two estimates from lap 6 on. Lap-to-lap: 1.4826 *
# MAD of consecutive clean-lap differences / sqrt(2) (a difference of two
# independent laps has sqrt(2) times one lap's spread); it sees only short-term
# jitter. Fit residual: the residual standard deviation of each run's own
# fall-off fit; it sees pace that wanders for several laps. The IRP 9/24 bot
# repeated laps to a few thousandths yet moved between 23.2 s and 23.5 s for
# stretches of 3-5 laps after lap 6: lap-to-lap 0.03-0.06 s, fit residual
# 0.10-0.17 s, and two runs of one garage setup 0.247 s apart on laps 15-19.
MINIMUM_NOISE_DIFFERENCES = 4
MAD_TO_STANDARD_DEVIATION = 1.4826
# Laps that wander together are fewer independent samples: n laps with lag-1
# autocorrelation rho count as n (1 - rho) / (1 + rho), at least one. rho is
# the day's pooled autocorrelation of fit residuals, measured from 8+ pairs of
# consecutive clean laps, and capped so a window keeps some weight.
MINIMUM_AUTOCORRELATION_PAIRS = 8
MAXIMUM_AUTOCORRELATION = 0.9
# A stall pressure this close to the set cold pressure is that setting, not a hot reading.
COLD_SETTING_MATCH_PSI = 0.25
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


def _stop_reading(run: Mapping[str, Any] | None,
                  sheet_corners: Mapping[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """The run's pit-stall reading, with the set cold pressure beside it.

    In the IRP Supra test of September 2026 the stall pressure channel read the
    set cold pressure at every stop (14.0/28.0 psi against a 96/193 kPa sheet)
    while the next recording's sheet held 16.7/34.2/17.7/33.8 psi hot, so a
    stall pressure equal to the cold setting is labelled ``cold_setting`` and
    is never a hot reading. ``hot_pressure_psi`` is filled only from a later
    garage sheet whose readings match this stop (see ``_attach_hot_pressures``).
    """

    status = _text((run or {}).get("tire_measurement_status"))
    observation = (run or {}).get("tire_observation")
    measured = status == "measured_at_stop" and isinstance(observation, Mapping) and isinstance(observation.get("tires"), Mapping)
    header = {
        "status": "measured" if measured else "not_measured",
        "source": "telemetry_pit_stall",
        "temperature": "carcass",
        "measurement_status": status,
        "reason": None if measured else _STOP_REASONS.get(status or "", "No stall reading is available for this run."),
        "hot_pressure": {"status": "not_measured", "source": None, "matched_run": None,
                         "reason": "No stall reading is available for this run." if not measured else None},
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
        cold = _number(((sheet_corners or {}).get(tire) or {}).get("cold_pressure_psi"))
        psi = _round(pressure.get("psi"), 2)
        kind = _text(pressure.get("kind"))
        if psi is not None and cold is not None and abs(psi - cold) <= COLD_SETTING_MATCH_PSI:
            kind = "cold_setting"
        corners[tire] = {
            "temperature_f": temperatures, "tread_percent": tread,
            "pressure_psi": psi, "pressure_kind": kind,
            "cold_pressure_psi": _round(cold, 2), "hot_pressure_psi": None,
            **_corner_summary(temperatures, tread),
        }
    return header, corners


def _attach_hot_pressures(stint: Mapping[str, Any], later_sheet: tuple[Mapping[str, Any], Mapping[str, Any]] | None,
                          later_run: int | None) -> None:
    """Hot pressures for this stop from the next recording's garage sheet, when it is this stop's reading.

    iRacing writes the stall reading into the garage sheet the next recording
    loads. The sheet is attributed to this stop only when every corner's
    temperatures and tread match the stall telemetry (within 2 °F and 1 %).
    """

    header, corners = stint["stop"]
    if header["status"] != "measured":
        return
    hot = header["hot_pressure"]
    if later_sheet is None:
        hot["reason"] = ("The run ended inside its recording; the next run's sheet is the same one, so no hot pressure was recorded."
                         if not stint["last_in_recording"] else
                         "No later recording's garage sheet was reviewed, so the hot pressures at this stop are not recorded.")
        return
    sheet_header, sheet_corners = later_sheet
    agreements = [_agreement(sheet_corners.get(tire), corners.get(tire)) for tire in TIRES]
    pressures = {tire: _number((sheet_corners.get(tire) or {}).get("hot_pressure_psi")) for tire in TIRES}
    if sheet_header.get("status") != "recorded" or any(item["status"] != "agree" for item in agreements) \
            or any(value is None for value in pressures.values()) or any(corners.get(tire) is None for tire in TIRES):
        hot["reason"] = "The next recording's garage sheet does not match this stop's temperatures and tread, so its hot pressures are not attributed here."
        return
    for tire in TIRES:
        corners[tire]["hot_pressure_psi"] = _round(pressures[tire], 2)
    hot.update(status="measured", source="garage_sheet_next_recording", matched_run=later_run, reason=None,
               note=(f"Hot pressures from the garage sheet loaded with run {later_run}; its temperatures and tread match "
                     "this stop's stall reading." if later_run is not None else
                     "Hot pressures from the next recording's garage sheet; its temperatures and tread match this stop's stall reading."))


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
    temperatures = [corner["temperature_f"] for corner in corners.values() if corner and corner.get("temperature_f")]
    # Every band of every recorded corner equal: a garage state no tire has driven on.
    uniform = bool(temperatures) and all(len(set(bands.values())) == 1 for bands in temperatures)
    return {"status": "recorded" if recorded else "not_recorded", "source": "garage_sheet",
            "timing": "unverified", "timing_note": SHEET_TIMING_NOTE, "uniform": uniform}, corners


def _agreement(first: Mapping[str, Any] | None, second: Mapping[str, Any] | None) -> dict[str, Any]:
    temperature_deltas = [abs(first["temperature_f"][band] - second["temperature_f"][band])
                          for band in ("inside", "middle", "outside")
                          if first and second and first.get("temperature_f") and second.get("temperature_f")]
    tread_deltas = [abs(first["tread_percent"][band] - second["tread_percent"][band])
                    for band in ("inside", "middle", "outside")
                    if first and second and first.get("tread_percent") and second.get("tread_percent")]
    if not temperature_deltas and not tread_deltas:
        return {"status": "not_compared", "disagrees_on": [], "max_temperature_delta_f": None, "max_tread_delta_percent": None}
    # Which reading differs, so a tread-only difference is never shown as a temperature one.
    disagrees_on = [name for name, deltas, limit in (("temperature", temperature_deltas, SHEET_AGREEMENT_F),
                                                     ("tread", tread_deltas, SHEET_AGREEMENT_PERCENT))
                    if any(delta > limit for delta in deltas)]
    return {"status": "disagree" if disagrees_on else "agree", "disagrees_on": disagrees_on,
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


def _run_rows(analysis: Mapping[str, Any], screen: Mapping[int, Sequence[str]]) -> list[tuple[Mapping[str, Any], list[dict[str, Any]]]]:
    """Every run of the recording that has laps, in order, with its lap rows."""

    laps = sorted((lap for lap in analysis.get("laps") or () if isinstance(lap, Mapping) and (_number(lap.get("lap")) or 0) > 0),
                  key=lambda lap: lap["lap"])
    runs = sorted((run for run in analysis.get("runs") or () if isinstance(run, Mapping)
                   and _number(run.get("start_lap")) is not None and _number(run.get("end_lap")) is not None),
                  key=lambda run: (_number(run.get("start_lap")) or 0))
    result: list[tuple[Mapping[str, Any], list[dict[str, Any]]]] = []
    for run in runs:
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
        if rows:
            result.append((run, rows))
    return result


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


def _recording_runs(analysis: Mapping[str, Any], index: int, analysis_path: str | None) -> list[dict[str, Any]]:
    """One entry per run of one recording; a recording without a timed run is one entry without laps."""

    identity = analysis.get("identity")
    if not isinstance(identity, Mapping):
        raise TestDayError(f"Recording {index + 1} has no identity; refresh its analysis.")
    for field in ("laps", "runs"):
        if not isinstance(analysis.get(field), list):
            raise TestDayError(f"Recording {index + 1} has no {field} list; refresh its analysis.")
    recorded = _epoch(identity.get("session_start"))
    if recorded is None:
        raise TestDayError(f"Recording {index + 1} has no recording time (identity.session_start); refresh its analysis.")
    offline = _is_offline_testing(analysis)
    screen = _screen(analysis, offline)
    runs = _run_rows(analysis, screen) or [(None, [])]
    by_lap = {int(lap["lap"]): lap for lap in analysis["laps"] if isinstance(lap, Mapping) and _number(lap.get("lap")) is not None}
    setup = identity.get("setup")
    has_setup = isinstance(setup, Mapping) and bool(setup)
    sheet = _sheet_reading(setup)
    selection = _path(analysis, "source", "selection") or {}
    recording = {
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
        "run_count": len([run for run, _ in runs if run is not None]),
        "sheet": sheet,
    }
    entries = []
    for position, (run, rows) in enumerate(runs, start=1):
        for row in rows:
            # A lap where the car stood still is neither a pace sample nor a pace observation to draw.
            row["plotted"] = (_plotted(by_lap.get(row["lap"], {}), offline) and row["run_lap"] is not None
                              and "stopped_on_track" not in row["exclusion_reasons"])
        temperatures = [value for row in rows if (value := _number(_path(by_lap.get(row["lap"], {}), "conditions", "track_temperature_f"))) is not None]
        start_fuel = _gallons((run or {}).get("fuel", {}).get("start_l") if isinstance((run or {}).get("fuel"), Mapping) else None)
        if start_fuel is None and rows:
            start_fuel = _gallons(_path(by_lap.get(rows[0]["lap"], {}), "fuel", "start_l"))
        entries.append({
            **recording,
            "run_in_recording": position if run is not None else None,
            "last_in_recording": position == len(runs),
            "track_temp_f": _round(statistics.median(temperatures), 1) if temperatures else None,
            "start_fuel_gal": start_fuel,
            "tire_start": _tire_start(analysis, run),
            "run": run, "rows": rows,
            "run_laps": max((row["run_lap"] for row in rows if row["run_lap"] is not None), default=0),
            "stop": _stop_reading(run, sheet[1]),
        })
    return entries


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


def _clean_rows(stint: Mapping[str, Any], first: int | None = None, last: int | None = None) -> list[dict[str, Any]]:
    return [row for row in stint["rows"] if row["eligible"] and row["run_lap"] is not None
            and (first is None or row["run_lap"] >= first) and (last is None or row["run_lap"] <= last)]


def _clean_length(stint: Mapping[str, Any]) -> int:
    """The run's clean length: the last lap of the run that is a clean pace sample."""

    return max((row["run_lap"] for row in _clean_rows(stint)), default=0)


def _late_span(length: int) -> tuple[int, int] | None:
    """The last five laps up to ``length``, from lap 6 on so they never share a lap with laps 2-5."""

    if length < MINIMUM_LATE_RUN_LAPS:
        return None
    return max(length - LATE_WINDOW_LAPS + 1, EARLY_WINDOW[1] + 1), length


def _too_short(stint: Mapping[str, Any], length: int, what: str) -> str:
    if not stint["run_laps"]:
        return "No run with known lap ages was recorded."
    if not length:
        return "No clean lap was recorded."
    if what == "fall-off":
        return (f"Too short for fall-off: the last clean lap is lap {length}; {MINIMUM_FALL_OFF_RUN_LAPS} are needed "
                f"so the fit has {MINIMUM_FIT_LAPS} laps after laps {EARLY_WINDOW[0]}–{EARLY_WINDOW[1]}, where the tires come in.")
    return (f"Too short for a late window: the last clean lap is lap {length}; {MINIMUM_LATE_RUN_LAPS} are needed "
            f"so it does not overlap laps {EARLY_WINDOW[0]}–{EARLY_WINDOW[1]}.")


def _own_late(stint: Mapping[str, Any]) -> dict[str, Any]:
    """This run's own late window: its last five clean-length laps from lap 6 on."""

    length = _clean_length(stint)
    span = _late_span(length)
    if span is None:
        return {"first_run_lap": None, "last_run_lap": None, "label": None, "clean_lap_count": 0, "laps": [],
                "median_s": None, "status": "not_measured", "reason": _too_short(stint, length, "a late window")}
    return _window(stint, *span)


def _fit(rows: Sequence[Mapping[str, Any]], minimum: int = MINIMUM_WINDOW_LAPS) -> dict[str, Any] | None:
    try:
        from .analysis_engine import _linear_slope
    except ImportError:  # pragma: no cover
        from analysis_engine import _linear_slope
    points = [(float(row["run_lap"]), float(row["lap_time_s"])) for row in rows]
    slope = _linear_slope(points) if len(points) >= max(minimum, MINIMUM_WINDOW_LAPS) else None
    if slope is None:
        return None
    mean_x = statistics.fmean(x for x, _ in points)
    mean_y = statistics.fmean(y for _, y in points)
    intercept = mean_y - slope * mean_x
    residuals = {int(x): y - (intercept + slope * x) for x, y in points}
    scatter = math.sqrt(sum(value * value for value in residuals.values()) / (len(points) - 2)) if len(points) > 2 else None
    return {"slope": slope, "intercept": intercept, "scatter": scatter, "residuals": residuals,
            "sxx": sum((x - mean_x) ** 2 for x, _ in points), "count": len(points)}


def _post_warm_up_fit(stint: Mapping[str, Any], length: int | None = None) -> dict[str, Any] | None:
    """Least-squares fit of the clean laps from lap 6 to ``length`` (the run's clean length by default)."""

    last = _clean_length(stint) if length is None else length
    if last < MINIMUM_FALL_OFF_RUN_LAPS:
        return None
    return _fit(_clean_rows(stint, FALL_OFF_FIRST_RUN_LAP, last), MINIMUM_FIT_LAPS)


def _fall_off(stint: Mapping[str, Any]) -> dict[str, Any]:
    """Least-squares fall-off from lap 6, the first lap after the warm-up of laps 2-5, to the run's last clean lap."""

    first = FALL_OFF_FIRST_RUN_LAP
    length = _clean_length(stint)
    clean = _clean_rows(stint, first, length) if length >= first else []
    result = {"first_run_lap": first, "last_run_lap": length if length >= first else None, "fit_lap_count": len(clean),
              "laps": [row["lap"] for row in clean], "equal_age": False,
              "slope_s_per_lap": None, "intercept_s": None, "scatter_s": None, "status": "not_measured", "reason": None,
              "method": "ordinary_least_squares_on_run_lap"}
    if length < MINIMUM_FALL_OFF_RUN_LAPS:
        result["reason"] = _too_short(stint, length, "fall-off")
        return result
    fit = _post_warm_up_fit(stint, length)
    if fit is None:
        span = _window_label(first, length).lower()
        result["reason"] = f"{len(clean)} clean lap{'s' if len(clean) != 1 else ''} in {span}; {MINIMUM_FIT_LAPS} are needed."
        return result
    result.update(status="measured", equal_age=True, slope_s_per_lap=_round(fit["slope"], 4), intercept_s=_round(fit["intercept"], 4),
                  scatter_s=_round(fit["scatter"], 4))
    return result


def _differences(stint: Mapping[str, Any], first: int = NOISE_FIRST_RUN_LAP) -> list[float]:
    """Differences between consecutive clean laps from ``first`` on (after the early window by default)."""

    ages = {row["run_lap"]: float(row["lap_time_s"]) for row in _clean_rows(stint, first)}
    return [ages[age + 1] - ages[age] for age in sorted(ages) if age + 1 in ages]


def _noise_from(differences: Sequence[float]) -> float | None:
    if len(differences) < MINIMUM_NOISE_DIFFERENCES:
        return None
    center = statistics.median(differences)
    spread = statistics.median(abs(value - center) for value in differences)
    return MAD_TO_STANDARD_DEVIATION * spread / math.sqrt(2.0)


def _residual_spread(stints: Sequence[Mapping[str, Any]]) -> tuple[float | None, int]:
    """Pooled residual standard deviation of the runs' own fall-off fits, and the laps behind it."""

    fits = [fit for stint in stints if (fit := _post_warm_up_fit(stint)) is not None]
    freedom = sum(fit["count"] - 2 for fit in fits)
    if freedom <= 0:
        return None, 0
    total = sum(value * value for fit in fits for value in fit["residuals"].values())
    return math.sqrt(total / freedom), sum(fit["count"] for fit in fits)


def _autocorrelation(stints: Sequence[Mapping[str, Any]]) -> tuple[float | None, int]:
    """Pooled lag-1 autocorrelation of the runs' fit residuals at consecutive clean laps, clamped to [0, 0.9]."""

    fits = [fit for stint in stints if (fit := _post_warm_up_fit(stint)) is not None]
    products = [fit["residuals"][age] * fit["residuals"][age + 1] for fit in fits for age in fit["residuals"] if age + 1 in fit["residuals"]]
    total = sum(value * value for fit in fits for value in fit["residuals"].values())
    if len(products) < MINIMUM_AUTOCORRELATION_PAIRS or total <= 0:
        return None, len(products)
    return min(MAXIMUM_AUTOCORRELATION, max(0.0, sum(products) / total)), len(products)


def _run_noise(stint: Mapping[str, Any]) -> dict[str, Any]:
    """This run's lap noise after the warm-up: the larger of its lap-to-lap and fit-residual estimates."""

    differences = _differences(stint)
    lap_to_lap = _noise_from(differences)
    fit = _post_warm_up_fit(stint)
    residual = fit["scatter"] if fit is not None else None
    values = [value for value in (lap_to_lap, residual) if value is not None]
    noise = max(values) if values else None
    return {"status": "measured" if noise is not None else "not_measured", "noise_s": _round(noise, 4),
            "difference_noise_s": _round(lap_to_lap, 4), "residual_noise_s": _round(residual, 4),
            "difference_count": len(differences), "residual_lap_count": fit["count"] if fit is not None else 0,
            "first_run_lap": NOISE_FIRST_RUN_LAP, "method": "larger_of_lap_to_lap_and_fit_residual",
            "reason": None if noise is not None else
            (f"{len(differences)} pair{'s' if len(differences) != 1 else ''} of consecutive clean laps from lap "
             f"{NOISE_FIRST_RUN_LAP} on ({MINIMUM_NOISE_DIFFERENCES} are needed) and no fall-off fit "
             f"({MINIMUM_FALL_OFF_RUN_LAPS}+ clean laps).")}


def _day_context(members: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """What the whole day measured: its lap noise, the autocorrelation of its fit residuals and its same-setup runs.

    Two runs are a same-setup pair when their recordings' garage setups match
    (no adjustment changed; computed readouts aside) and nothing blocks their
    comparison. Their difference at the same laps of the run is what the day
    shows when no setup changed, so a setup verdict must exceed it.
    """

    residual, residual_count = _residual_spread(members)
    autocorrelation, pairs = _autocorrelation(members)
    same_setup = []
    for index, first in enumerate(members):
        for second in members[index + 1:]:
            if _setup_change(first, second)["status"] == "unchanged" and not _comparability(first, second)[0]:
                same_setup.append((first, second))
    return {"differences": [value for item in members for value in _differences(item)],
            "residual_s": residual, "residual_lap_count": residual_count,
            "autocorrelation": autocorrelation, "autocorrelation_pairs": pairs, "same_setup": same_setup}


def _pair_noise(previous: Mapping[str, Any], current: Mapping[str, Any]) -> dict[str, Any]:
    """Lap noise for one comparison: the larger of the lap-to-lap and fit-residual estimates.

    Each comes from both runs, else from the whole day; with no run of the day
    past lap 5, the lap-to-lap estimate uses both runs from lap 2. The day's
    autocorrelation of fit residuals travels with it (``_threshold``).
    """

    day = current.get("day") or {}
    differences = _differences(previous) + _differences(current)
    lap_to_lap = _noise_from(differences)
    basis = "these_runs"
    if lap_to_lap is None:
        differences = list(day.get("differences") or ())
        lap_to_lap = _noise_from(differences)
        basis = "day"
    if lap_to_lap is None:
        # Short runs only: their laps from lap 2 on. The median difference removes a
        # steady warm-up slope; a bend in it can only widen the threshold.
        differences = _differences(previous, EARLY_WINDOW[0]) + _differences(current, EARLY_WINDOW[0])
        lap_to_lap = _noise_from(differences)
        basis = "these_runs_from_lap_2" if lap_to_lap is not None else None
    residual, residual_count = _residual_spread([previous, current])
    residual_basis = "these_runs" if residual is not None else None
    if residual is None and day.get("residual_s") is not None:
        residual, residual_count, residual_basis = day["residual_s"], day["residual_lap_count"], "day"
    autocorrelation = day.get("autocorrelation")
    values = [value for value in (lap_to_lap, residual) if value is not None]
    result = {"status": "not_measured", "noise_s": None, "basis": basis, "difference_count": len(differences),
              "difference_noise_s": _round(lap_to_lap, 4), "residual_noise_s": _round(residual, 4),
              "residual_basis": residual_basis, "residual_lap_count": residual_count,
              "autocorrelation": _round(autocorrelation, 3), "autocorrelation_pairs": day.get("autocorrelation_pairs", 0),
              "reason": None}
    if not values:
        result["reason"] = (f"Lap noise is not measured: fewer than {MINIMUM_NOISE_DIFFERENCES} pairs of consecutive clean laps "
                            f"and no run of {MINIMUM_FALL_OFF_RUN_LAPS}+ clean laps in these runs or the rest of the day.")
        return result
    result.update(status="measured", noise_s=_round(max(values), 4))
    return result


def _threshold(noise: Mapping[str, Any], count: int) -> tuple[float, float, float]:
    """Combined standard error of two medians of ``count`` laps each, the verdict threshold and the laps' effective count.

    The medians' standard error uses the lap noise; laps that wander together
    count as fewer independent laps (``MINIMUM_AUTOCORRELATION_PAIRS``).
    """

    autocorrelation = _number(noise.get("autocorrelation")) or 0.0
    effective = max(1.0, count * (1.0 - autocorrelation) / (1.0 + autocorrelation))
    combined = math.sqrt(2.0) * MEDIAN_STANDARD_ERROR_FACTOR * float(noise["noise_s"]) / math.sqrt(effective)
    return combined, round(max(MINIMUM_THRESHOLD_S, STANDARD_ERROR_MULTIPLIER * combined), 3), effective


def _clean_by_age(stint: Mapping[str, Any], first: int, last: int) -> dict[int, Mapping[str, Any]]:
    return {row["run_lap"]: row for row in _clean_rows(stint, first, last)}


def _same_pair(first: Mapping[str, Any], second: Mapping[str, Any], pair: tuple[Mapping[str, Any], Mapping[str, Any]]) -> bool:
    return {first["number"], second["number"]} == {pair[0]["number"], pair[1]["number"]}


def _same_setup_spread(day: Mapping[str, Any], first: int, last: int,
                       exclude: tuple[Mapping[str, Any], Mapping[str, Any]] | None = None) -> dict[str, Any] | None:
    """The largest median difference between two other same-setup runs of the day on laps ``first``-``last``."""

    best: tuple[float, Mapping[str, Any], Mapping[str, Any], list[int]] | None = None
    count = 0
    for earlier, later in day.get("same_setup") or ():
        if exclude is not None and _same_pair(earlier, later, exclude):
            continue
        before, after = _clean_by_age(earlier, first, last), _clean_by_age(later, first, last)
        shared = sorted(set(before) & set(after))
        if len(shared) < MINIMUM_WINDOW_LAPS:
            continue
        count += 1
        spread = abs(statistics.median(after[age]["lap_time_s"] for age in shared)
                     - statistics.median(before[age]["lap_time_s"] for age in shared))
        if best is None or spread > best[0]:
            best = (spread, earlier, later, shared)
    if best is None:
        return None
    return {"spread_s": round(best[0], 3), "runs": [best[1]["number"], best[2]["number"]], "run_laps": best[3], "pair_count": count}


def _same_setup_slope_spread(day: Mapping[str, Any], length: int,
                             exclude: tuple[Mapping[str, Any], Mapping[str, Any]]) -> dict[str, Any] | None:
    """The largest fall-off difference between two other same-setup runs of the day over laps 6..``length``."""

    best: tuple[float, Mapping[str, Any], Mapping[str, Any]] | None = None
    count = 0
    for earlier, later in day.get("same_setup") or ():
        if _same_pair(earlier, later, exclude):
            continue
        fits = [_fit(_clean_rows(item, FALL_OFF_FIRST_RUN_LAP, length), MINIMUM_FIT_LAPS) for item in (earlier, later)]
        if length < MINIMUM_FALL_OFF_RUN_LAPS or any(fit is None for fit in fits):
            continue
        count += 1
        spread = abs(fits[1]["slope"] - fits[0]["slope"])
        if best is None or spread > best[0]:
            best = (spread, earlier, later)
    if best is None:
        return None
    return {"spread_s_per_lap": round(best[0], 4), "runs": [best[1]["number"], best[2]["number"]], "pair_count": count}


def _window_threshold(noise: Mapping[str, Any], count: int, first: int, last: int, day: Mapping[str, Any],
                      exclude: tuple[Mapping[str, Any], Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """The verdict threshold for ``count`` laps of laps ``first``-``last``: the noise's, or the same-setup spread when larger."""

    combined, statistical, effective = _threshold(noise, count)
    spread = _same_setup_spread(day, first, last, exclude)
    threshold = max(statistical, spread["spread_s"] if spread else 0.0)
    return {"standard_error_s": _round(combined, 4), "statistical_threshold_s": statistical, "threshold_s": round(threshold, 3),
            "effective_lap_count": round(effective, 1), "same_setup_spread": spread}


_PAIR_WINDOW_EMPTY = {"previous_median_s": None, "current_median_s": None, "delta_s": None, "threshold_s": None,
                      "statistical_threshold_s": None, "standard_error_s": None, "effective_lap_count": None,
                      "same_setup_spread": None}


def _pair_window(previous: Mapping[str, Any], current: Mapping[str, Any], key: str, first: int, last: int,
                 noise: Mapping[str, Any]) -> dict[str, Any]:
    """Both runs' medians over the laps of this window that are clean in both, and their verdict."""

    label = _window_label(first, last)
    before_rows, after_rows = _clean_by_age(previous, first, last), _clean_by_age(current, first, last)
    shared = sorted(set(before_rows) & set(after_rows))
    result: dict[str, Any] = {
        "key": key, "label": label, "first_run_lap": first, "last_run_lap": last, "status": "not_measured", "verdict": None,
        "run_laps": shared, "previous_laps": [before_rows[age]["lap"] for age in shared],
        "current_laps": [after_rows[age]["lap"] for age in shared], **_PAIR_WINDOW_EMPTY, "reason": None}
    if len(shared) < MINIMUM_WINDOW_LAPS:
        result["reason"] = (f"Only {len(shared)} lap{'s' if len(shared) != 1 else ''} of {label.lower()} "
                            f"{'is' if len(shared) == 1 else 'are'} clean in both runs; {MINIMUM_WINDOW_LAPS} are needed at the same laps of the run.")
        return result
    before = _round(statistics.median(before_rows[age]["lap_time_s"] for age in shared))
    after = _round(statistics.median(after_rows[age]["lap_time_s"] for age in shared))
    result.update(previous_median_s=before, current_median_s=after, delta_s=round(after - before, 3) + 0.0)  # + 0.0: never -0.0
    if noise.get("status") != "measured":
        result["reason"] = "Lap noise is not measured, so there is no threshold."
        return result
    result.update(_window_threshold(noise, len(shared), first, last, current.get("day") or {}, (previous, current)))
    delta, threshold = result["delta_s"], result["threshold_s"]
    result.update(status="measured", verdict="faster" if delta < -threshold else "slower" if delta > threshold else "within_scatter")
    return result


def _withheld_late(length: int) -> dict[str, Any]:
    return {"key": "late", "label": None, "first_run_lap": None, "last_run_lap": None, "status": "withheld", "verdict": None,
            "run_laps": [], "previous_laps": [], "current_laps": [], **_PAIR_WINDOW_EMPTY,
            "reason": ("One of the runs has no clean lap." if not length else
                       f"The shorter run's last clean lap is lap {length}; a late window needs {MINIMUM_LATE_RUN_LAPS} laps "
                       f"so it does not overlap laps {EARLY_WINDOW[0]}–{EARLY_WINDOW[1]}.")}


def _pair_fall_off(previous: Mapping[str, Any], current: Mapping[str, Any], length: int, noise: Mapping[str, Any]) -> dict[str, Any]:
    """Both runs' fall-off over the same laps 6..N and whether they differ beyond the noise.

    The slopes' standard error uses the lap noise (the runs' fit residuals when
    larger), widened by the autocorrelation of laps that wander together; a
    difference must also exceed the fall-off difference between two other
    same-setup runs of the day over the same laps.
    """

    first = FALL_OFF_FIRST_RUN_LAP
    result: dict[str, Any] = {"first_run_lap": first, "last_run_lap": length if length >= first else None,
                              "label": _window_label(first, length) if length >= first else None, "status": "not_measured",
                              "previous_slope_s_per_lap": None, "current_slope_s_per_lap": None, "delta_s_per_lap": None,
                              "threshold_s_per_lap": None, "statistical_threshold_s_per_lap": None,
                              "standard_error_s_per_lap": None, "same_setup_spread": None, "verdict": None, "reason": None}
    if length < MINIMUM_FALL_OFF_RUN_LAPS:
        # No label: "laps 6–8" would read as a fall-off measured from too few laps.
        result.update(status="withheld", label=None, reason="One of the runs has no clean lap." if not length else (
            f"Too short for fall-off: the shorter run's last clean lap is lap {length}; {MINIMUM_FALL_OFF_RUN_LAPS} are needed "
            f"so the fit has {MINIMUM_FIT_LAPS} laps after laps {EARLY_WINDOW[0]}–{EARLY_WINDOW[1]}, where the tires come in."))
        return result
    fits = [_fit(_clean_rows(item, first, length), MINIMUM_FIT_LAPS) for item in (previous, current)]
    if any(fit is None for fit in fits):
        result["reason"] = f"Fewer than {MINIMUM_FIT_LAPS} clean laps in {result['label'].lower()} in one of the runs."
        return result
    before, after = fits
    delta = _round(after["slope"] - before["slope"], 4) + 0.0  # never -0.0
    result.update(previous_slope_s_per_lap=_round(before["slope"], 4), current_slope_s_per_lap=_round(after["slope"], 4),
                  delta_s_per_lap=delta)
    if noise.get("status") != "measured" or before["sxx"] <= 0 or after["sxx"] <= 0:
        result["reason"] = "Lap noise is not measured, so there is no threshold."
        return result
    autocorrelation = _number(noise.get("autocorrelation")) or 0.0
    inflation = math.sqrt((1.0 + autocorrelation) / (1.0 - autocorrelation))
    error = float(noise["noise_s"]) * inflation * math.sqrt(1.0 / before["sxx"] + 1.0 / after["sxx"])
    statistical = round(max(MINIMUM_SLOPE_THRESHOLD_S, STANDARD_ERROR_MULTIPLIER * error), 4)
    spread = _same_setup_slope_spread(current.get("day") or {}, length, (previous, current))
    threshold = round(max(statistical, spread["spread_s_per_lap"] if spread else 0.0), 4)
    result.update(status="measured", threshold_s_per_lap=threshold, statistical_threshold_s_per_lap=statistical,
                  standard_error_s_per_lap=_round(error, 5), same_setup_spread=spread,
                  verdict="more_fall_off" if delta > threshold else "less_fall_off" if delta < -threshold else "within_scatter")
    return result


def _comparability(previous: Mapping[str, Any], current: Mapping[str, Any]) -> tuple[list[tuple[str, str]], list[str]]:
    """Blocking (code, reason) pairs, then non-blocking flags, for comparing two stints."""

    before, after = previous["number"], current["number"]
    reasons: list[tuple[str, str]] = []
    flags: list[str] = []
    if previous["car_path"] != current["car_path"] or not current["car_path"]:
        reasons.append(("car", f"Run {after} used a different car ({current['car_path'] or 'not recorded'}) than run {before} ({previous['car_path'] or 'not recorded'})."))
    if not previous["configuration_key"] or not current["configuration_key"]:
        reasons.append(("configuration_missing", "The track configuration was not recorded for both runs."))
    elif previous["configuration_key"] != current["configuration_key"]:
        reasons.append(("configuration", f"Run {after} was recorded on another track configuration than run {before}."))
    for item in (previous, current):
        if item["fixed"] is not False:
            reasons.append(("fixed_setup", f"Run {item['number']} was not recorded on an open setup.") if item["fixed"] else
                           ("setup_type_missing", f"Run {item['number']} does not record whether its setup was open."))
    if previous["track_temp_f"] is None or current["track_temp_f"] is None:
        missing = before if previous["track_temp_f"] is None else after
        reasons.append(("track_temperature_missing", f"Track temperature was not recorded for run {missing}."))
    elif abs(current["track_temp_f"] - previous["track_temp_f"]) >= MATERIAL_TEMPERATURE_F:
        reasons.append(("track_temperature", f"Track temperature {previous['track_temp_f']:.0f} → {current['track_temp_f']:.0f} °F "
                                             f"({MATERIAL_TEMPERATURE_F:.0f} °F or more apart)."))
    if previous["start_fuel_gal"] is None or current["start_fuel_gal"] is None:
        missing = before if previous["start_fuel_gal"] is None else after
        reasons.append(("fuel_missing", f"Starting fuel was not recorded for run {missing}."))
    elif abs(current["start_fuel_gal"] - previous["start_fuel_gal"]) >= MATERIAL_FUEL_GAL:
        reasons.append(("fuel", f"Starting fuel {previous['start_fuel_gal']:.1f} → {current['start_fuel_gal']:.1f} gal "
                                f"({MATERIAL_FUEL_GAL:.0f} gal or more apart)."))
    if previous["compound"] and current["compound"] and previous["compound"] != current["compound"]:
        reasons.append(("compound", f"Tire compound {previous['compound']} → {current['compound']}."))
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


def _window_sentence(window: Mapping[str, Any]) -> str:
    """'Laps 2–5: 0.007 s faster (threshold 0.054 s).' for a window that is not the verdict's basis."""

    if window["status"] == "measured":
        delta, threshold = window["delta_s"], window["threshold_s"]
        text = {"faster": f"{-delta:.3f} s faster (threshold {threshold:.3f} s)",
                "slower": f"{delta:.3f} s slower (threshold {threshold:.3f} s)",
                "within_scatter": f"{delta:+.3f} s, within the {threshold:.3f} s threshold"}[window["verdict"]]
        return f"{window['label']}: {text}."
    if window["status"] == "withheld":
        return f"No late window: {window['reason'][0].lower()}{window['reason'][1:]}"
    return window["reason"] or ""


def _threshold_sentence(window: Mapping[str, Any]) -> str:
    """' Runs 1 and 5 used the same setup and differed by 0.247 s here.' when that set the threshold."""

    spread = window.get("same_setup_spread")
    if not spread or spread["spread_s"] < (window.get("statistical_threshold_s") or 0.0):
        return ""
    first, second = spread["runs"]
    return f" Runs {first} and {second} used one garage setup and differed by {spread['spread_s']:.3f} s on these laps."


def _compare(previous: Mapping[str, Any], current: Mapping[str, Any]) -> dict[str, Any]:
    """This run against an earlier one over their common clean length N.

    Laps 2-5, the last five laps up to N (from lap 6 on, withheld below eight
    laps) and the fall-off over laps 6..N (withheld below ten) are each compared
    on the laps clean in both runs. The verdict is the late window's when it is
    measured, otherwise laps 2-5's; both windows are reported.
    """

    blocking, flags = _comparability(previous, current)
    reasons = [text for _, text in blocking]
    length = min(_clean_length(previous), _clean_length(current))
    result: dict[str, Any] = {"compared_with_run": previous["number"], "verdict": "not_comparable", "blocked_by": None, "basis": None,
                              "window_label": None, "first_run_lap": None, "last_run_lap": None,
                              "run_laps": [], "previous_laps": [], "current_laps": [], "previous_median_s": None, "current_median_s": None, "delta_s": None,
                              "threshold_s": None, "standard_error_s": None, "reasons": reasons,
                              "reason_codes": [code for code, _ in blocking], "flags": flags, "summary": None,
                              "common_run_laps": length, "noise": None, "windows": None, "fall_off": None}
    if reasons:
        result.update(blocked_by="conditions", summary=f"Not comparable with run {previous['number']}: " + " ".join(reasons))
        return result
    noise = _pair_noise(previous, current)
    span = _late_span(length)
    windows = {"early": _pair_window(previous, current, "early", EARLY_WINDOW[0], EARLY_WINDOW[1], noise),
               "late": _pair_window(previous, current, "late", span[0], span[1], noise) if span else _withheld_late(length)}
    result.update(noise=noise, windows=windows, fall_off=_pair_fall_off(previous, current, length, noise))
    candidates = [window for window in (windows["late"], windows["early"]) if window["status"] != "withheld"]
    usable = [window for window in candidates if len(window["run_laps"]) >= MINIMUM_WINDOW_LAPS]
    if not usable:
        reason = (candidates[0]["reason"] if candidates else
                  f"Neither pace window has {MINIMUM_WINDOW_LAPS}+ clean laps in both runs.")
        result.update(blocked_by="measurement", reasons=[reason], reason_codes=["measurement"],
                      summary=f"Not comparable with run {previous['number']}: {reason}")
        return result
    if noise["status"] != "measured":
        reason = noise["reason"]
        result.update(blocked_by="measurement", reasons=[reason], reason_codes=["scatter"],
                      summary=f"Not comparable with run {previous['number']}: {reason}")
        return result
    window = usable[0]
    others = [item for item in (windows["early"], windows["late"]) if item is not window]
    verdict, delta, threshold = window["verdict"], window["delta_s"], window["threshold_s"]
    before, after, ages = window["previous_median_s"], window["current_median_s"], window["run_laps"]
    words = {"faster": f"{-delta:.3f} s faster than", "slower": f"{delta:.3f} s slower than",
             "within_scatter": f"{delta:+.3f} s, within the {threshold:.3f} s threshold of"}[verdict]
    result.update(verdict=verdict, basis=f"{window['key']}_window", window_label=window["label"],
                  first_run_lap=window["first_run_lap"], last_run_lap=window["last_run_lap"],
                  run_laps=ages, previous_laps=window["previous_laps"], current_laps=window["current_laps"],
                  previous_median_s=before, current_median_s=after, delta_s=delta, threshold_s=threshold,
                  standard_error_s=window["standard_error_s"], reasons=[],
                  summary=(f"{window['label']}, {len(ages)} laps clean in both runs: median {after:.3f} s, {words} "
                           f"run {previous['number']} ({before:.3f} s)"
                           + ("." if verdict == "within_scatter" else f"; threshold {threshold:.3f} s.")
                           + _threshold_sentence(window)
                           + "".join(f" {_window_sentence(item)}" for item in others if _window_sentence(item))))
    return result


# --------------------------------------------------------------------------
# Setup changes and the next-change card
# --------------------------------------------------------------------------


def _setting_entry(item: Mapping[str, Any]) -> dict[str, Any]:
    delta = item.get("delta_left_minus_right")
    return {"field": item["field"], "label": _field_label(item["field"]),
            "previous": _display(item["left"]), "current": _display(item["right"]),
            "delta": _round(-delta, 4) if _number(delta) is not None else None,
            "unit": item["right"].get("unit") if isinstance(item.get("right"), Mapping) else None}


def _setup_change(previous: Mapping[str, Any] | None, current: Mapping[str, Any]) -> dict[str, Any]:
    """Garage adjustments changed since the previous stint, and the readouts they moved.

    Computed readouts (corner, cross and nose weight, ride heights, ...) are
    listed separately and never counted: the driver sets them only through an
    adjustment, and they drift by a unit between loads of one setup.
    """

    empty = {"change_count": 0, "changes": [], "readout_count": 0, "readouts": [],
             "unknown_count": 0, "unknown_fields": []}
    if previous is None:
        return {"status": "first_run", "compared_with_run": None, **empty, "complete": True, "reason": None}
    result = {"status": "unknown", "compared_with_run": previous["number"], **empty, "complete": False, "reason": None}
    if previous["index"] == current["index"]:
        # A recording keeps one garage sheet, so a change between two of its runs is not recorded.
        result.update(status="not_recorded",
                      reason=f"Runs {previous['number']} and {current['number']} are in one recording, which keeps one garage "
                             "sheet; a setup change between them is not recorded.")
        return result
    missing = [item["number"] for item in (previous, current) if item["garage_fields"] is None]
    if missing:
        result["reason"] = "No embedded garage setup was recorded for run " + " or ".join(str(number) for number in missing) + "."
        return result
    comparison = compare_setups({"fields": previous["garage_fields"]}, {"fields": current["garage_fields"]}, max_output=2000)
    changes: list[dict[str, Any]] = []
    readouts: list[dict[str, Any]] = []
    for item in comparison["differences"]:
        (readouts if is_garage_readout_field(item["field"]) else changes).append(_setting_entry(item))
    one_sided = set(comparison["only_left"]) | set(comparison["only_right"])
    unknown = sorted(field for field in one_sided if not is_garage_readout_field(field))
    unknown_count = (comparison["summary"]["only_left_fields"] + comparison["summary"]["only_right_fields"]
                     - (len(one_sided) - len(unknown)))
    status = "changed" if changes else "unknown" if unknown_count else "unchanged"
    reason = None
    if unknown_count:
        reason = f"{unknown_count} setting{'s' if unknown_count != 1 else ''} recorded in only one of the two runs."
    elif not changes and readouts:
        shown = ", ".join(f"{item['label'].lower()} {item['previous']} → {item['current']}" for item in readouts[:3])
        reason = (f"No adjustment changed; only computed garage readouts differ ({shown}"
                  + (", …" if len(readouts) > 3 else "") + ").")
    if reason is None and previous["run_count"] > 1:
        reason = (f"Compared with the one garage sheet of run {previous['number']}'s recording, which holds "
                  f"{previous['run_count']} runs; a change made between those runs is not recorded.")
    result.update(status=status, change_count=len(changes), changes=changes, readout_count=len(readouts),
                  readouts=readouts, unknown_count=unknown_count, unknown_fields=unknown,
                  complete=not comparison["output_truncated"], reason=reason)
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


def _adjusts(field: str, readout: str) -> bool:
    return field.rsplit(".", 1)[-1] in READOUT_ADJUSTER_LEAVES.get(readout.rsplit(".", 1)[-1], ())


def _values(changes: Sequence[Mapping[str, Any]], key: str) -> str:
    return ", ".join(dict.fromkeys(str(item.get(key) or "?") for item in changes))


def _setting_units(rules: Mapping[str, Any] | None, car_path: str | None, change: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The settings the driver changed, in the ruleset's order.

    A ruleset pair changed at both ends (the rear track bars, the front
    packers) is one setting and is never split. The adjustments that set a
    readout the ruleset names (spring perch offsets that moved cross weight)
    are one setting too, carrying that readout: when two perches moved cross
    weight together, the review cannot say how much each one moved it.
    """

    settings = _ruleset_settings(rules, car_path)
    changes = [item for item in change.get("changes") or () if isinstance(item, Mapping)]
    readouts = {item["field"]: item for item in change.get("readouts") or () if isinstance(item, Mapping)}
    by_field = {item["field"]: item for item in changes}
    units: list[dict[str, Any]] = []
    used: set[str] = set()
    for rank, setting in enumerate(settings):
        paths = setting["normalized_paths"]
        if len(paths) > 1 and all(path in by_field for path in paths):
            members = [by_field[path] for path in paths]
            units.append({"rank": rank, "setting": setting, "readout": None, "changes": members,
                          "label": str(setting.get("setting_id") or members[0]["label"]).replace("-", " ").capitalize()})
            used.update(paths)
    for rank, setting in enumerate(settings):
        paths = setting["normalized_paths"]
        if len(paths) != 1 or paths[0] not in readouts:
            continue
        adjusters = [item for item in changes if item["field"] not in used and _adjusts(item["field"], paths[0])]
        if adjusters:
            readout = readouts[paths[0]]
            units.append({"rank": rank, "setting": setting, "readout": readout, "changes": adjusters,
                          "label": adjusters[0]["label"] if len(adjusters) == 1 else readout["label"]})
            used.update(item["field"] for item in adjusters)
    for item in changes:
        if item["field"] in used:
            continue
        unit = {"rank": len(settings), "setting": None, "readout": None, "changes": [item], "label": item["label"]}
        for rank, setting in enumerate(settings):
            if setting["normalized_paths"] == [item["field"]]:
                unit.update(rank=rank, setting=setting)
                break
        units.append(unit)
    units.sort(key=lambda unit: (unit["rank"], unit["changes"][0]["field"]))
    return units


def _unit_value(unit: Mapping[str, Any], key: str) -> str:
    """A setting's value; several adjusters of one readout are named by the readout and each value."""

    if unit.get("readout") is not None and len(unit["changes"]) > 1:
        parts = ", ".join(f"{item['label'].lower()} {item.get(key) or '?'}" for item in unit["changes"])
        return f"{unit['readout'].get(key) or '?'} ({parts})"
    return _values(unit["changes"], key)


def _step_suggestion(rules: Mapping[str, Any] | None, unit: Mapping[str, Any]) -> dict[str, Any] | None:
    """One more garage step in the same direction, only when the ruleset names it."""

    setting = unit.get("setting")
    if not isinstance(setting, Mapping):
        return None
    if unit.get("readout") is not None:
        deltas = [_number(unit["readout"].get("delta"))]
    elif {item["field"] for item in unit["changes"]} == set(setting["normalized_paths"]):
        deltas = [_number(item.get("delta")) for item in unit["changes"]]
    else:
        return None
    if any(delta in (None, 0.0) for delta in deltas):
        return None
    signs = {1 if delta > 0 else -1 for delta in deltas}
    if len(signs) != 1:
        return None
    sign = signs.pop()
    for direction, text in (setting.get("directions") or {}).items():
        if _DIRECTION_SIGNS.get(direction) == sign and isinstance(text, Mapping):
            return {"ruleset_id": (rules or {}).get("ruleset_id"), "setting_id": setting.get("setting_id"),
                    "direction": direction, "instruction": text.get("instruction"),
                    "expected_effect": text.get("expected_effect"), "risk": text.get("risk"),
                    "step_verified": setting.get("verified_step") is not None}
    return None


def _joined(parts: Sequence[str]) -> str:
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def _moved(unit: Mapping[str, Any]) -> str | None:
    """'Left rear spring perch offset -72 mm → -70 mm moved front cross weight 52.1% → 52.4%.'

    Arrows always read in time order, earlier run → later run. Several
    adjustments of one readout are credited with its movement together.
    """

    readout = unit.get("readout")
    if readout is None:
        return None
    moved = f"{readout['label'].lower()} {readout['previous']} → {readout['current']}"
    items = unit["changes"]
    if len(items) == 1:
        item = items[0]
        return f"{item['label']} {item['previous']} → {item['current']} moved {moved}."
    parts = [f"{item['label'].lower()} {item['previous']} → {item['current']}" for item in items]
    text = _joined(parts)
    return (f"{text[0].upper()}{text[1:]} together moved {moved}; the review does not split that between them.")


def _revert(item: Mapping[str, Any]) -> dict[str, Any]:
    return {"field": item["field"], "label": item["label"], "from": item["current"], "to": item["previous"]}


def _pair_reference(comparison: Mapping[str, Any], side: str) -> dict[str, Any]:
    """The compared window, with one run's median there, as a success rule's reference."""

    return {"label": comparison.get("window_label"), "first_run_lap": comparison.get("first_run_lap"),
            "last_run_lap": comparison.get("last_run_lap"), "median_s": comparison.get(f"{side}_median_s")}


def _rule_window(comparison: Mapping[str, Any], reference: Mapping[str, Any], side: str) -> tuple[dict[str, Any], float | None]:
    """The window a success rule checks, and its threshold.

    The compared late window when the verdict used one. When a short run left
    only laps 2-5 to compare, the rule checks the reference run's own late
    window instead (a later run must reach it), with a threshold from the same
    lap-to-lap noise: asking to beat laps 2-5, where the tires come in, would
    not measure the change.
    """

    if comparison.get("basis") == "late_window":
        return _pair_reference(comparison, side), comparison.get("threshold_s")
    late = reference["late"]
    noise = comparison.get("noise") or {}
    if late["status"] == "measured" and noise.get("status") == "measured" and late["clean_lap_count"]:
        rule = _window_threshold(noise, late["clean_lap_count"], late["first_run_lap"], late["last_run_lap"], reference.get("day") or {})
        return ({"label": late["label"], "first_run_lap": late["first_run_lap"], "last_run_lap": late["last_run_lap"],
                 "median_s": late["median_s"]}, rule["threshold_s"])
    return _pair_reference(comparison, side), comparison.get("threshold_s")


def _reach(window: Mapping[str, Any]) -> str:
    """' in a run of 14+ laps' for a window past laps 2-5, so the run length it needs is stated."""

    last = window.get("last_run_lap")
    return f" in a run of {last}+ laps" if isinstance(last, int) and last > EARLY_WINDOW[1] else ""


def _success(reference: Mapping[str, Any], window: Mapping[str, Any] | str | None, threshold: float | None,
             direction: str) -> dict[str, Any]:
    """The rule a later run must meet, on the window the verdict used.

    ``window`` is a compared window (``_pair_reference``), a key of the run's
    own windows, or None for the run's own late window, else laps 2-5. The
    threshold is the comparison's noise threshold, so the rule asks for a
    difference the day's lap-to-lap noise can resolve.
    """

    if isinstance(window, str):
        window = reference.get(window)
    if not isinstance(window, Mapping) or not window.get("label"):
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
        text = f"Repeat {median:.3f} s median on {label.lower()} within ±{limit:.3f} s with {MINIMUM_WINDOW_LAPS}+ clean laps{_reach(window)}."
    else:
        text = f"Beat {median:.3f} s median on {label.lower()} by at least {limit:.3f} s with {MINIMUM_WINDOW_LAPS}+ clean laps{_reach(window)}."
    return {"direction": direction, "reference_run": reference["number"], "reference_median_s": median,
            "window_label": label, "first_run_lap": window.get("first_run_lap"), "last_run_lap": window.get("last_run_lap"),
            "threshold_s": limit, "minimum_clean_laps": MINIMUM_WINDOW_LAPS, "text": text}


def _card(kind: str, title: str, instruction: str, runs: Sequence[int], success: Mapping[str, Any], evidence: Sequence[str],
          changes: Sequence[Mapping[str, Any]] = (), ruleset: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {"kind": kind, "title": title, "instruction": instruction, "based_on_runs": list(runs),
            "changes": [dict(item) for item in changes], "ruleset_step": dict(ruleset) if ruleset else None,
            "success_rule": dict(success), "evidence": [item for item in evidence if item],
            "note": "No setup direction is inferred from tire temperatures or wear."}


def _measured_windows(stint: Mapping[str, Any]) -> list[str]:
    return [key for key in ("early", "late") if stint[key]["status"] == "measured"]


def _measurement_card(previous: Mapping[str, Any], latest: Mapping[str, Any], comparison: Mapping[str, Any]) -> dict[str, Any]:
    """Too few clean laps: ask for the run that can meet the card's own success rule."""

    number, before = latest["number"], previous["number"]
    earlier_windows, latest_windows = _measured_windows(previous), _measured_windows(latest)
    if earlier_windows:
        # The shortest window the earlier run measured is the quickest way to a verdict.
        success = _success(previous, earlier_windows[0], None, "measure")
        title = f"Repeat run {number} with more clean laps"
        instruction = (f"Drive run {number}'s setup again, unchanged, for at least {success['last_run_lap']} laps "
                       f"so {success['window_label'].lower()} can be compared with run {before}.")
    elif latest_windows:
        success = _success(latest, latest_windows[0], None, "measure")
        title = f"Run {before} has too few clean laps; make run {number} the baseline"
        instruction = (f"Run {before} has no pace window with {MINIMUM_WINDOW_LAPS}+ clean laps, so run {number}'s change "
                       f"cannot be measured. Drive run {number}'s setup again, unchanged, for at least "
                       f"{success['last_run_lap']} laps; it becomes the baseline for the next change.")
    else:
        success = _success(latest, "early", None, "measure")
        title = f"Runs {before} and {number} have too few clean laps"
        instruction = (f"Neither run has a pace window with {MINIMUM_WINDOW_LAPS}+ clean laps. Drive run {number}'s setup "
                       f"again, unchanged, for at least {success['last_run_lap']} laps.")
    return _card("repeat_comparable", title, instruction, [before, number], success, comparison["reasons"])


def _conditions_card(previous: Mapping[str, Any], latest: Mapping[str, Any], comparison: Mapping[str, Any]) -> dict[str, Any]:
    """Conditions differ: ask for the repeat the driver can actually make."""

    number, before = latest["number"], previous["number"]
    codes = set(comparison.get("reason_codes") or ())
    if "track_temperature" in codes and codes <= {"track_temperature", "fuel"}:
        # The track does not return to an earlier temperature; re-run the earlier setup under today's conditions.
        fuel = latest["start_fuel_gal"]
        back = latest["setup_change"]  # run {before} -> run {number}; reverting it loads run {before}'s setup
        moved = f"{previous['track_temp_f']:.0f} → {latest['track_temp_f']:.0f} °F"
        if back["status"] == "unchanged":
            # Same garage setup: there is no change to measure, only a new baseline.
            return _card("rebaseline", f"Make run {number} the baseline for today's conditions",
                         f"Runs {before} and {number} used the same garage setup and track temperature moved {moved}, so their "
                         f"difference is not a setup result. Make the next change from run {number}'s setup and compare it with run {number}.",
                         [before, number], _success(latest, None, None, "measure"), comparison["reasons"])
        instruction = (f"Track temperature moved {previous['track_temp_f']:.0f} → {latest['track_temp_f']:.0f} °F since run "
                       f"{before} and will not come back. Drive run {before}'s setup again now"
                       + (f", starting with {fuel:.1f} gal," if fuel is not None else "")
                       + f" so it can be compared with run {number} under the same conditions.")
        return _card("rebaseline", f"Re-run run {before}'s setup now", instruction, [before, number],
                     _success(latest, None, None, "measure"), comparison["reasons"],
                     [_revert(item) for item in back["changes"]] if back["status"] == "changed" else ())
    if codes == {"fuel"} and previous["start_fuel_gal"] is not None:
        return _card("repeat_comparable", f"Repeat run {number} with run {before}'s fuel",
                     f"Drive run {number}'s setup again, unchanged, starting with {previous['start_fuel_gal']:.1f} gal "
                     f"(within {MATERIAL_FUEL_GAL:.0f} gal of run {before}) so the change can be measured.",
                     [before, number], _success(previous, None, None, "measure"), comparison["reasons"])
    return _card("repeat_comparable", f"Repeat run {number} under comparable conditions",
                 f"Drive run {number}'s setup again with conditions matching run {before}, so the change can be measured.",
                 [before, number], _success(previous, None, None, "measure"), comparison["reasons"])


def _return_card(stints: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    """Return to the earlier comparable run the latest run lost to by the most in the late window."""

    latest = stints[-1]
    number = latest["number"]
    pairs = [(earlier, _compare(earlier, latest)) for earlier in stints[:-1]]
    slower = [(earlier, pair) for earlier, pair in pairs if pair["verdict"] == "slower" and pair["basis"] == "late_window"]
    if not slower:
        return None
    earlier, pair = max(slower, key=lambda item: (item[1]["delta_s"], -item[0]["number"]))
    target = _setup_change(earlier, latest)
    if target["status"] in ("unchanged", "not_recorded"):
        return None
    detail = (f"Load run {earlier['number']}'s setup ({earlier['setup_name']})." if earlier["setup_name"]
              else f"Load run {earlier['number']}'s setup.")
    if target["status"] == "unknown":
        detail += " Its differences from run " + str(number) + " are not fully recorded."
    evidence = [f"Run {number} lost the most late pace to run {earlier['number']} of the runs comparable with it. " + (pair["summary"] or "")]
    own = latest["late"]
    for other, other_pair in pairs:
        # A faster run that cannot be compared is named, never silently skipped.
        late = other["late"]
        if (other_pair["verdict"] == "not_comparable" and late["status"] == "measured" and own["status"] == "measured"
                and late["median_s"] < own["median_s"]):
            evidence.append(f"Run {other['number']} was faster late ({late['median_s']:.3f} s on its {late['label'].lower()}) "
                            f"but is not comparable with run {number}: " + " ".join(other_pair["reasons"]))
    return _card("return_to_setup", f"Return to run {earlier['number']}'s setup", detail,
                 [earlier["number"], number], _success(latest, _pair_reference(pair, "current"), pair["threshold_s"], "faster"),
                 evidence, [_revert(item) for item in target["changes"]])


def _next_change(stints: Sequence[Mapping[str, Any]], rules: Mapping[str, Any] | None) -> dict[str, Any]:
    latest, previous = stints[-1], stints[-2]
    comparison = latest["comparison"]
    change = latest["setup_change"]
    known = change["status"] == "changed" and change["complete"] and not change["unknown_count"]
    number, before = latest["number"], previous["number"]

    if comparison["verdict"] == "not_comparable":
        if comparison["blocked_by"] == "measurement":
            return _measurement_card(previous, latest, comparison)
        return _conditions_card(previous, latest, comparison)

    units = _setting_units(rules, latest["car_path"], change) if change["status"] == "changed" else []
    if comparison["verdict"] == "slower" and units:
        reverts = [_revert(item) for unit in units for item in unit["changes"]]
        title = (f"Revert {units[0]['label'].lower()} to {_unit_value(units[0], 'previous')}" if len(units) == 1
                 else f"Revert the {len(units)} changes to run {before}'s values")
        return _card("revert", title, f"Return exactly to run {before}'s values; run {number} was slower.",
                     [before, number], _success(latest, *_rule_window(comparison, latest, "current"), "faster"),
                     [comparison["summary"], *filter(None, (_moved(unit) for unit in units))], reverts)

    returned = _return_card(stints)
    if returned is not None:
        return returned

    logical = len(units) if known else None
    if comparison["verdict"] == "faster" and logical == 1:
        unit = units[0]
        kept = unit["changes"]
        step = _step_suggestion(rules, unit)
        moved = _moved(unit)
        # A kept wedge adjustment is named by the ruleset setting it moved (front cross weight).
        label = (unit["readout"]["label"] if unit["readout"] is not None and step is not None else unit["label"]).lower()
        values = unit["readout"]["current"] if unit["readout"] is not None and step is not None else _unit_value(unit, "current")
        evidence = [comparison["summary"], *([moved] if moved else [])]
        if step is not None:
            return _card("keep_and_step", f"Keep {label} at {values}; try one more step",
                         f"{step['instruction']} Expected: {step['expected_effect']} Risk: {step['risk']}",
                         [before, number], _success(latest, *_rule_window(comparison, latest, "current"), "faster"), evidence,
                         [{"field": item["field"], "label": item["label"], "from": item["current"], "to": None} for item in kept],
                         step)
        return _card("keep", f"Keep {label} at {values}", f"Keep run {number}'s setup and repeat it to confirm the gain.",
                     [before, number], _success(latest, *_rule_window(comparison, latest, "current"), "within"), evidence)

    if known and (logical or 0) >= 2:
        keep, rest = units[0], units[1:]
        reverts = [_revert(item) for unit in rest for item in unit["changes"]]
        return _card("isolate", f"Test {keep['label'].lower()} alone",
                     f"Return to run {before}'s setup with only {keep['label'].lower()} at {_unit_value(keep, 'current')}; "
                     f"run {number} changed {len(units)} settings at once.",
                     [before, number], _success(previous, *_rule_window(comparison, previous, "previous"), "faster"),
                     [comparison["summary"], *filter(None, (_moved(unit) for unit in units))], reverts)

    evidence = [comparison["summary"]]
    if change["status"] == "unchanged":
        evidence.append(f"No garage setting changed between runs {before} and {number}; the difference is not attributed to setup.")
        if change.get("reason"):
            evidence.append(change["reason"])
    elif change["status"] == "unknown":
        evidence.append(change["reason"] or "The setup comparison is incomplete.")
    return _card("repeat", f"Repeat run {number} unchanged", f"Drive run {number}'s setup again, unchanged, to measure its repeatability.",
                 [before, number], _success(latest, *_rule_window(comparison, latest, "current"), "within"), evidence)


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
    # The chart's fitted line is the table's fall-off: the same clean laps 2..N and the
    # same slope, never a second fit over every plotted lap of a longer run.
    fall_off = stint["fall_off"]
    ages = sorted(row["run_lap"] for row in stint["rows"] if row["lap"] in set(fall_off["laps"]) and row["run_lap"] is not None)
    if fall_off["status"] == "measured" and ages:
        slope, intercept = fall_off["slope_s_per_lap"], fall_off["intercept_s"]
        view["fit"] = {"method": fall_off["method"], "eligible_count": fall_off["fit_lap_count"], "laps": list(fall_off["laps"]),
                       "slope_s_per_green_lap": slope, "domain": [float(ages[0]), float(ages[-1])],
                       "points": [{"x": float(age), "y": _round(intercept + slope * age, 3)} for age in (ages[0], ages[-1])]}
        view["fit_unavailable_reason"] = None
    else:
        view["fit"] = None
        view["fit_unavailable_reason"] = fall_off["reason"]
    view["fit_basis"] = "fall_off"
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
                    "run_laps": stint["run_laps"], "other_run_count": max(0, stint["run_count"] - 1),
                    "clean_run_laps": _clean_length(stint)}
    return {
        "run": stint["number"], "analysis_id": stint["analysis_id"], "analysis_path": stint["analysis_path"],
        "recording": {"number": stint["recording_number"], "analysis_id": stint["analysis_id"],
                      "analysis_path": stint["analysis_path"], "recorded_at": stint["recorded_at"],
                      "run_in_recording": stint["run_in_recording"], "runs_in_recording": stint["run_count"]},
        "recorded_at": stint["recorded_at"], "session_type": stint["session_type"],
        "test_day_id": stint["test_day_id"], "test_day_run": stint["test_day_run"],
        "offline_flag_rule_applied": stint["offline"],
        "setup": {"name": stint["setup_name"], "fixed": stint["fixed"], "embedded": stint["garage_fields"] is not None,
                  "garage_fingerprint": stint["garage_fingerprint"]},
        "measured_run": measured,
        "conditions": {"track_temp_f": stint["track_temp_f"], "start_fuel_gal": stint["start_fuel_gal"],
                       "tire_compound": stint["compound"], "tire_start": stint["tire_start"]},
        "early": stint["early"], "late": stint["late"], "fall_off": stint["fall_off"], "noise": stint["noise"],
        "setup_change": stint["setup_change"], "comparison": stint["comparison"],
        "tires": _tires(stint, previous),
        "laps": [{key: row[key] for key in ("lap", "run_lap", "lap_time_s", "eligible", "plotted", "exclusion_reasons")}
                 | {"label": f"Run {stint['number']} · lap {row['run_lap']}" if row["run_lap"] is not None else f"Run {stint['number']} · recording lap {row['lap']}"}
                 for row in stint["rows"]],
        "trend": _chart_trend(stint),
    }


def _untimed(entry: Mapping[str, Any]) -> dict[str, Any]:
    return {"recording": entry["recording_number"], "analysis_id": entry["analysis_id"], "analysis_path": entry["analysis_path"],
            "recorded_at": entry["recorded_at"], "session_type": entry["session_type"], "setup_name": entry["setup_name"],
            "reason": "No timed lap was recorded."}


def _short_run(entry: Mapping[str, Any]) -> dict[str, Any]:
    """A timed run with fewer clean laps than one window needs: listed once, never a row, never plotted."""

    clean = len(_clean_rows(entry))
    run = entry["run"]
    return {"recording": entry["recording_number"], "analysis_id": entry["analysis_id"], "analysis_path": entry["analysis_path"],
            "recorded_at": entry["recorded_at"], "session_type": entry["session_type"], "setup_name": entry["setup_name"],
            "run_in_recording": entry["run_in_recording"], "runs_in_recording": entry["run_count"],
            "first_lap": int(run["start_lap"]), "last_lap": int(run["end_lap"]), "run_laps": entry["run_laps"],
            "clean_lap_count": clean,
            "reason": (f"{'No' if not clean else clean} clean lap{'s' if clean != 1 else ''} after lap 1; "
                       f"{MINIMUM_COMPARED_CLEAN_LAPS} are needed to compare a run.")}


def build_test_day_review(analyses: Sequence[Mapping[str, Any]], rules: Mapping[str, Any] | None = None, *,
                          analysis_paths: Sequence[str | None] | None = None) -> dict[str, Any]:
    """Review the runs of 1-12 recordings of one test day. Pure: reads the analyses, writes nothing."""

    if isinstance(analyses, (str, bytes, Mapping)) or not isinstance(analyses, Sequence):
        raise TestDayError("A test-day review needs a list of recording analyses.")
    if not MINIMUM_RECORDINGS <= len(analyses) <= MAXIMUM_RECORDINGS:
        raise TestDayError(f"A test-day review needs {MINIMUM_RECORDINGS}–{MAXIMUM_RECORDINGS} recordings; {len(analyses)} were supplied.")
    paths = list(analysis_paths) if analysis_paths is not None else [None] * len(analyses)
    if len(paths) != len(analyses):
        raise TestDayError("Each recording needs exactly one analysis path.")
    recordings = []
    for index, analysis in enumerate(analyses):
        if not isinstance(analysis, Mapping):
            raise TestDayError(f"Recording {index + 1} is not an analysis object.")
        recordings.append(_recording_runs(analysis, index, paths[index]))
    identifiers = [entries[0]["analysis_id"] for entries in recordings if entries[0]["analysis_id"]]
    if len(identifiers) != len(set(identifiers)):
        raise TestDayError("The same recording was supplied more than once.")
    recordings.sort(key=lambda entries: (entries[0]["recorded"], entries[0]["analysis_id"] or "", entries[0]["index"]))
    stints = []
    short = []
    untimed = []
    for recording_number, entries in enumerate(recordings, start=1):
        for entry in entries:
            entry["recording_number"] = recording_number
            # A recording without a timed lap, or a run too short to hold one window (the stubs
            # between resets), is listed once apart from the runs: it has nothing to compare.
            if entry["run"] is None:
                untimed.append(entry)
            elif len(_clean_rows(entry)) < MINIMUM_COMPARED_CLEAN_LAPS:
                short.append(entry)
            else:
                stints.append(entry)
    if len(stints) < MINIMUM_RUNS:
        apart = [*([f"{len(short)} run{'s have' if len(short) != 1 else ' has'} fewer than {MINIMUM_COMPARED_CLEAN_LAPS} clean laps"] if short else []),
                 *([f"{len(untimed)} recording{'s have' if len(untimed) != 1 else ' has'} no timed lap"] if untimed else [])]
        raise TestDayError(f"A test day needs at least {MINIMUM_RUNS} runs; "
                           f"{'this recording holds' if len(recordings) == 1 else 'these recordings hold'} {len(stints)}"
                           + (f" ({'; '.join(apart)})." if apart else "."))
    omitted = max(0, len(stints) - MAXIMUM_ROWS)
    stints = stints[omitted:]
    for number, stint in enumerate(stints, start=1):
        stint["number"] = number
    # The next recording's garage sheet is where iRacing writes a stop's reading.
    first_run_of = {}
    for stint in stints:
        first_run_of.setdefault(stint["recording_number"], stint["number"])
    for stint in stints:
        later = recordings[stint["recording_number"]] if stint["last_in_recording"] and stint["recording_number"] < len(recordings) else None
        _attach_hot_pressures(stint, later[0]["sheet"] if later else None,
                              first_run_of.get(stint["recording_number"] + 1) if later else None)

    first = stints[0]
    members = [item for item in stints if item["car_path"] == first["car_path"]
               and item["configuration_key"] == first["configuration_key"]]
    day = _day_context(members)
    lengths = [_clean_length(item) for item in members]
    for stint in stints:
        stint["early"] = _window(stint, *EARLY_WINDOW)
        stint["late"] = _own_late(stint)
        stint["fall_off"] = _fall_off(stint)
        stint["noise"] = _run_noise(stint)
        stint["day"] = day if any(item is stint for item in members) else {}
    if any(length >= MINIMUM_LATE_RUN_LAPS for length in lengths):
        late = {"status": "available", "first_run_lap": None, "last_run_lap": None, "label": "Last 5 laps", "reason": None,
                "basis": (f"Each run's late window is its last {LATE_WINDOW_LAPS} clean-length laps from lap "
                          f"{EARLY_WINDOW[1] + 1} on; a comparison uses the last {LATE_WINDOW_LAPS} laps both runs reached.")}
    else:
        late = {"status": "withheld", "first_run_lap": None, "last_run_lap": None, "label": None,
                "reason": (f"No run reached {MINIMUM_LATE_RUN_LAPS} clean laps, so there are not {MINIMUM_WINDOW_LAPS} laps "
                           f"after {_window_label(*EARLY_WINDOW).lower()} for a late window."), "basis": None}
    previous = None
    for stint in stints:
        stint["setup_change"] = _setup_change(previous, stint)
        stint["comparison"] = _compare(previous, stint) if previous is not None else None
        previous = stint

    rows = [_row(stint, stints[index - 1] if index else None) for index, stint in enumerate(stints)]
    offline_runs = [stint["number"] for stint in stints if stint["offline"]]
    limitations = [
        *([f"Showing the latest {MAXIMUM_ROWS} runs; {omitted} earlier run{'s are' if omitted != 1 else ' is'} left out."] if omitted else []),
        "Pace windows count laps from the start of each stint's measured run. A verdict compares two runs over their common "
        "clean length on the laps of the run that are clean in both, so its medians are at equal run age; run age is not measured tire wear.",
        (f"Verdicts compare medians against max({MINIMUM_THRESHOLD_S:.2f} s, {STANDARD_ERROR_MULTIPLIER:.0f} × the combined standard "
         "error from the runs' lap noise, the largest difference between two other runs of the day on one garage setup at the "
         "same laps); a difference inside it is within scatter, not unchanged. The lap noise is the larger of the lap-to-lap "
         f"spread and each run's fall-off fit residual, from lap {NOISE_FIRST_RUN_LAP} on so a warm-up ramp does not widen it, and "
         "laps whose pace wanders together count as fewer independent laps."),
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
        "recording_count": len(recordings),
        "untimed_recordings": [_untimed(entry) for entry in untimed],
        "short_runs": [_short_run(entry) for entry in short],
        "test_day_id": test_days.pop() if len(test_days) == 1 and all(stint["test_day_id"] for stint in stints) else None,
        "car": {"car_path": first["car_path"], "car_name": first["car_name"], "car_id": first["car_id"]},
        "track": {"track_name": first["track_name"], "track_config": first["track_config"],
                  "configuration_key": first["configuration_key"]},
        "windows": {
            "early": {"first_run_lap": EARLY_WINDOW[0], "last_run_lap": EARLY_WINDOW[1], "label": _window_label(*EARLY_WINDOW)},
            "late": late,
            "fall_off": {"first_run_lap": FALL_OFF_FIRST_RUN_LAP, "last_run_lap": max([FALL_OFF_FIRST_RUN_LAP, *lengths]),
                         "label": f"Laps {FALL_OFF_FIRST_RUN_LAP} to the last clean lap"},
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
                         "noise": (f"The larger of two estimates from lap {NOISE_FIRST_RUN_LAP} on: lap-to-lap, {MAD_TO_STANDARD_DEVIATION} × "
                                   "median absolute deviation of consecutive clean-lap differences ÷ √2 (both runs' differences pooled, "
                                   f"else the whole day's, else both runs' from lap {EARLY_WINDOW[0]} on; {MINIMUM_NOISE_DIFFERENCES}+ needed); "
                                   f"and fit residual, the pooled residual standard deviation of both runs' fall-off fits (laps "
                                   f"{FALL_OFF_FIRST_RUN_LAP} to the last clean lap; else the whole day's)."),
                         "autocorrelation": (f"The day's pooled lag-1 autocorrelation ρ of fall-off fit residuals at consecutive clean laps "
                                             f"({MINIMUM_AUTOCORRELATION_PAIRS}+ pairs, clamped to 0–{MAXIMUM_AUTOCORRELATION}); n laps count "
                                             "as n_eff = max(1, n × (1 − ρ) ÷ (1 + ρ)). Not measured: n_eff = n."),
                         "threshold": ("max(minimum, multiplier × √2 × median factor × noise ÷ √n_eff, same-setup spread) for n laps "
                                       "clean in both runs. Same-setup spread: the largest median difference at the same laps between two "
                                       "other runs of the day whose garage setups match and whose conditions are comparable."),
                         "fall_off_threshold": (f"max({MINIMUM_SLOPE_THRESHOLD_S} s/lap, multiplier × noise × √((1 + ρ) ÷ (1 − ρ)) × "
                                                "√(1/Sxx₁ + 1/Sxx₂), the largest fall-off difference between two other same-setup runs over "
                                                "the same laps)."),
                         "scatter": "Residual standard deviation of each run's fall-off fit; the fit-residual noise estimate.",
                         "comparison": (f"Over the two runs' common clean length N: laps 2–5 and the last {LATE_WINDOW_LAPS} laps up to N "
                                        f"from lap {EARLY_WINDOW[1] + 1} on (withheld when N < {MINIMUM_LATE_RUN_LAPS}), each on the laps "
                                        f"clean in both runs; the verdict is the late window's when it has {MINIMUM_WINDOW_LAPS}+ such laps, "
                                        f"otherwise laps 2–5's. Fall-off is compared over laps {FALL_OFF_FIRST_RUN_LAP}..N when "
                                        f"N ≥ {MINIMUM_FALL_OFF_RUN_LAPS}; a run with fewer than {MINIMUM_COMPARED_CLEAN_LAPS} clean laps "
                                        "is listed in short_runs and not compared.")},
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
    """Read-only test-day review of archived recording analyses under ``archive_root``."""

    if isinstance(analysis_paths, (str, bytes)) or not isinstance(analysis_paths, Sequence):
        raise TestDayError("analysis_paths must be a list of archived analysis.json paths.")
    if not MINIMUM_RECORDINGS <= len(analysis_paths) <= MAXIMUM_RECORDINGS:
        raise TestDayError(f"A test-day review needs {MINIMUM_RECORDINGS}–{MAXIMUM_RECORDINGS} recordings; {len(analysis_paths)} were supplied.")
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

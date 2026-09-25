"""Durable one-change trials and their later-recording checks.

A trial is saved from a backend investigation action, never from numbers the
application supplies. It fixes the section bounds and annotation version, the
slow condition to beat, the exposure window and the success rule, plus the
baseline pass needed to draw a before/after comparison later.

A check never reports "unchanged". It is Improved or Worse only when at least
the stated number of screened laps inside the exposure window move past the
threshold; otherwise the result is Not established. A recording that cannot be
compared (another car or configuration, the trial's own recording, no laps at
the required run age, missing section timing) is Not comparable. Every check is
kept, including unsuccessful ones.

A trial also stores how it can be checked (race_investigation.trial_reachability):
the run length without pitting its window needs, and whether a test-day run
reaches it. A later recording whose every screened lap sits before the window
says it could not reach the window, with its longest clean run, rather than
only that no laps matched.
"""

from __future__ import annotations

import json
import re
import statistics
from pathlib import Path
from typing import Any, Mapping, Sequence

try:  # Package import and direct script loading are both supported.
    from .race_inspection import (
        _covered, _pair_rows, _points, _slip_summary, configuration_key, run_exposure, section_elapsed,
    )
    from .race_investigation import TRIAL_EXPOSURE_TOLERANCE, _number, _path, _round, trial_reachability
    from .steering_basis import SteeringBasis, basis_of, comparable_across_recordings, pair_descriptor
    from .storage import _atomic_write_text, stable_hash, utc_now
except ImportError:  # pragma: no cover - normal CLI/MCP script-loading path.
    from race_inspection import (
        _covered, _pair_rows, _points, _slip_summary, configuration_key, run_exposure, section_elapsed,
    )
    from race_investigation import TRIAL_EXPOSURE_TOLERANCE, _number, _path, _round, trial_reachability
    from steering_basis import SteeringBasis, basis_of, comparable_across_recordings, pair_descriptor
    from storage import _atomic_write_text, stable_hash, utc_now


SCHEMA_VERSION = 1
EXPOSURE_TOLERANCE = TRIAL_EXPOSURE_TOLERANCE
MATERIAL_FUEL_GAL = 2.0
MATERIAL_TEMPERATURE_F = 5.0
MAX_EVALUATIONS = 50
TRIAL_ID = re.compile(r"^trial-[0-9a-f]{20}$")
POINT_FIELDS = ("lap_pct", "session_time_s", "speed_mph", "throttle", "brake", "brake_mean", "steering_rad", "slip_angle_deg")


class TrialError(ValueError):
    """A trial request that cannot be satisfied; the message is shown to the driver."""


def _trials_dir(archive_root: Path) -> Path:
    return Path(archive_root) / "trials"


def _trial_path(archive_root: Path, trial_id: str) -> Path:
    if not TRIAL_ID.match(str(trial_id)):
        raise TrialError("Unknown trial identifier.")
    return _trials_dir(archive_root) / f"{trial_id}.json"


def _identity(analysis: Mapping[str, Any]) -> dict[str, Any]:
    identity = analysis.get("identity") or {}
    selection = _path(analysis, "source", "selection") or {}
    return {
        "analysis_id": analysis.get("analysis_id"),
        "recorded_at": identity.get("session_start"),
        "track": identity.get("track_name"), "layout": identity.get("track_config"),
        "configuration_key": configuration_key(analysis),
        "car_id": identity.get("car_id"), "car": identity.get("car_name"), "car_path": identity.get("car_path"),
        "setup_name": identity.get("setup_name"), "setup_fingerprint": identity.get("setup_fingerprint"),
        "fixed_setup": identity.get("is_fixed_setup"),
        "subsession_id": identity.get("subsession_id"), "session_id": identity.get("session_id"),
        "session_type": selection.get("sim_session_type") or identity.get("event_type"),
    }


def _investigation(analysis: Mapping[str, Any]) -> Mapping[str, Any]:
    investigation = analysis.get("race_investigation")
    if not isinstance(investigation, Mapping) or investigation.get("schema_version") != 2:
        raise TrialError("This recording's investigation predates saved tests. Refresh its analysis first.")
    return investigation


def _reachability(spec: Mapping[str, Any]) -> dict[str, Any]:
    """The run length a test's window needs, from its own window and lap count."""
    return trial_reachability(spec.get("exposure_window"), (spec.get("success") or {}).get("minimum_laps"))


def _pass_points(trace: Mapping[str, Any] | None, start: float, end: float) -> list[dict[str, Any]]:
    return [{key: point.get(key) for key in POINT_FIELDS if key in point}
            for point in _points(trace) if start - 0.01 <= point["lap_pct"] <= end + 0.01]


def create_trial(analysis: Mapping[str, Any], *, analysis_path: str | None, comparison_id: str,
                 action_priority: int, created_at: str | None = None) -> dict[str, Any]:
    investigation = _investigation(analysis)
    comparison = next((item for item in investigation.get("comparisons") or () if item.get("id") == comparison_id), None)
    if comparison is None:
        raise TrialError("That comparison is not in this recording's investigation.")
    action = next((item for item in comparison.get("actions") or () if item.get("priority") == action_priority), None)
    if action is None or not isinstance(action.get("trial"), Mapping):
        raise TrialError("This suggestion has no measurable section to test.")
    spec = action["trial"]
    metric = spec["metric"]
    traces = {int(trace["lap"]): trace for trace in _path(analysis, "lap_traces", "traces") or ()
              if isinstance(trace, Mapping) and _number(trace.get("lap")) is not None}
    timed = sorted((seconds, lap) for lap in spec["baseline"]["laps"]
                   if (seconds := section_elapsed(traces.get(lap), metric["start_pct"], metric["end_pct"])) is not None)
    if not timed:
        raise TrialError("The baseline laps have no complete timing over this section.")
    seconds, lap = timed[(len(timed) - 1) // 2]
    exposure = {row["lap"]: row.get("green_exposure") for row in investigation.get("laps") or ()}
    source = _identity(analysis)
    trial_id = "trial-" + stable_hash({"analysis_id": source["analysis_id"], "comparison": comparison_id,
                                       "title": action.get("title"), "metric": metric,
                                       "annotation": spec.get("annotation")}, 20)
    return {
        "schema_version": SCHEMA_VERSION, "trial_id": trial_id, "created_at": created_at or utc_now(),
        "kind": "driving", "title": action.get("title"), "instruction": action.get("instruction"),
        "evidence": list(action.get("evidence") or ()), "limitation": action.get("limitation"),
        "source": dict(source, analysis_path=analysis_path),
        "comparison": {"id": comparison_id, "title": comparison.get("title"), "delta_s": comparison.get("delta_s"),
                       "baseline_laps": list(comparison["baseline"]["lap_numbers"]),
                       "compared_laps": list(comparison["compared"]["lap_numbers"])},
        "section": dict(spec["section"]), "annotation": dict(spec.get("annotation") or {}), "metric": dict(metric),
        "baseline": dict(spec["baseline"]), "reference": dict(spec["reference"]),
        "exposure_window": spec.get("exposure_window"),
        "reachability": _reachability(spec),
        "conditions": dict(spec.get("conditions") or {}),
        "success": dict(spec["success"]),
        "baseline_pass": {"lap": lap, "section_s": _round(seconds), "green_exposure": exposure.get(lap),
                          # What the stored steering_rad values mean; a later
                          # recording's steering is drawn beside them only
                          # when both are road-wheel angle.
                          "steering": basis_of(analysis).describe(),
                          "points": _pass_points(traces.get(lap), metric["start_pct"], metric["end_pct"])},
        "evaluations": [],
    }


def _not(verdict: str, reason: str, **extra: Any) -> dict[str, Any]:
    return {"verdict": verdict, "reasons": [reason], **extra}


def evaluate_trial(trial: Mapping[str, Any], analysis: Mapping[str, Any], *, analysis_path: str | None = None,
                   evaluated_at: str | None = None) -> dict[str, Any]:
    """Measure the trial metric on a later recording. Never mutates the trial."""
    later = _identity(analysis)
    source = trial["source"]
    result: dict[str, Any] = {"evaluated_at": evaluated_at or utc_now(), "analysis_id": later["analysis_id"],
                              "analysis_path": analysis_path, "recording": later, "confounds": []}
    metric, success = trial["metric"], trial["success"]
    def finish(outcome: Mapping[str, Any]) -> dict[str, Any]:
        result.update(outcome)
        return result
    if later["configuration_key"] != trial["annotation"].get("configuration_key"):
        return finish(_not("not_comparable", f"Recorded at {later['track'] or 'another track'} {later['layout'] or ''}".strip() + "; the test needs the same track configuration."))
    if (later["car_id"], later["car_path"]) != (source.get("car_id"), source.get("car_path")):
        return finish(_not("not_comparable", f"Recorded in a different car ({later['car'] or later['car_path'] or 'unknown'}); the test needs the same car as its source ({source.get('car') or source.get('car_path') or 'unknown'})."))
    if later["analysis_id"] == source.get("analysis_id") or (
            later["subsession_id"] not in (None, "") and later["subsession_id"] == source.get("subsession_id")
            and later["session_type"] == source.get("session_type")):
        return finish(_not("not_comparable", "This is the recording the test came from."))
    if later["recorded_at"] and source.get("recorded_at") and str(later["recorded_at"]) <= str(source["recorded_at"]):
        return finish(_not("not_comparable", "Recorded before the test's source recording."))
    laps = sorted((lap for lap in analysis.get("laps") or () if isinstance(lap, Mapping) and (_number(lap.get("lap")) or 0) > 0),
                  key=lambda lap: lap["lap"])
    runs = [run for run in analysis.get("runs") or () if isinstance(run, Mapping)]
    exposure = run_exposure(laps, runs)
    investigation = analysis.get("race_investigation") if isinstance(analysis.get("race_investigation"), Mapping) else {}
    eligible = {row["lap"] for row in investigation.get("laps") or () if row.get("eligible") is True}
    if not investigation.get("laps"):
        try:
            from .analysis_engine import investigation_lap_exclusions
        except ImportError:  # pragma: no cover
            from analysis_engine import investigation_lap_exclusions
        exclusions = investigation_lap_exclusions(analysis)
        eligible = {int(lap["lap"]) for lap in laps if not exclusions.get(int(lap["lap"]), ["eligibility_unknown"])}
    window = trial.get("exposure_window")
    low = high = None
    if window and None not in window:
        low, high = window[0] - EXPOSURE_TOLERANCE, window[1] + EXPOSURE_TOLERANCE
    candidates = [int(lap["lap"]) for lap in laps if int(lap["lap"]) in eligible
                  and (low is None or ((age := exposure.get(int(lap["lap"]))) is not None and low <= age <= high))]
    by_lap = {int(lap["lap"]): lap for lap in laps}
    result["window"] = [_round(low, 2), _round(high, 2)] if low is not None else None
    # No lap of this recording reached the window: it never ran long enough.
    # Claimed only when each screened lap's run age is known, since an unknown
    # age could hide a lap inside the window. A recording that reached the
    # window without a screened lap there keeps the message below.
    screened_ages = [exposure.get(int(lap["lap"])) for lap in laps if int(lap["lap"]) in eligible]
    reached = max((age for age in exposure.values() if age is not None), default=None)
    if (not candidates and low is not None and screened_ages and None not in screened_ages
            and reached is not None and reached < low):
        needed = _reachability(trial)["run_laps_needed"]
        longest = int(round(max(screened_ages)))
        result["reachability"] = {"reachable": False, "longest_clean_run_laps": longest, "run_laps_needed": needed}
        return finish(_not("not_comparable", f"Not reachable in this recording (longest clean run {longest} laps; needs ~{needed})."))
    if not candidates:
        where = f" between {low:.1f} and {high:.1f} green laps into a run" if low is not None else ""
        return finish(_not("not_comparable", f"No screened laps{where}. Drive at least {success['minimum_laps']} clear laps at that run age."))
    traces = {int(trace["lap"]): trace for trace in _path(analysis, "lap_traces", "traces") or ()
              if isinstance(trace, Mapping) and _number(trace.get("lap")) is not None}
    measured = sorted((seconds, lap) for lap in candidates
                      if (seconds := section_elapsed(traces.get(lap), metric["start_pct"], metric["end_pct"])) is not None)
    def fuel_gal(lap: int) -> float | None:
        litres = _number(_path(by_lap.get(lap, {}), "fuel", "start_l"))
        return _round(litres / 3.785411784, 2) if litres is not None else None
    result["laps"] = [{"lap": lap, "seconds": _round(seconds), "green_exposure": _round(exposure.get(lap), 2),
                       "fuel_start_gal": fuel_gal(lap)} for seconds, lap in sorted(measured, key=lambda item: item[1])]
    if not measured:
        return finish(_not("not_comparable", "Section timing is incomplete on every matching lap."))
    later_median = statistics.median(seconds for seconds, _ in measured)
    baseline_median = float(trial["baseline"]["median_s"])
    improvement = baseline_median - later_median
    result.update({"median_s": _round(later_median), "count": len(measured), "improvement_s": _round(improvement)})
    fuels = [row["fuel_start_gal"] for row in result["laps"] if row["fuel_start_gal"] is not None]
    baseline_fuel = _number(trial["conditions"].get("fuel_start_gal"))
    if fuels and baseline_fuel is not None and abs(statistics.median(fuels) - baseline_fuel) >= MATERIAL_FUEL_GAL:
        result["confounds"].append(f"Starting fuel {baseline_fuel:.1f} → {statistics.median(fuels):.1f} gal.")
    temperatures = [value for lap in measured if (value := _number(_path(by_lap.get(lap[1], {}), "conditions", "track_temperature_f"))) is not None]
    baseline_temperature = _number(trial["conditions"].get("track_temp_f"))
    if temperatures and baseline_temperature is not None and abs(statistics.median(temperatures) - baseline_temperature) >= MATERIAL_TEMPERATURE_F:
        result["confounds"].append(f"Track temperature {baseline_temperature:.0f} → {statistics.median(temperatures):.0f} °F.")
    if later["setup_fingerprint"] and source.get("setup_fingerprint") and later["setup_fingerprint"] != source["setup_fingerprint"]:
        result["confounds"].append("A different setup was loaded.")
    seconds, lap = measured[(len(measured) - 1) // 2]
    base_points = [dict(point) for point in trial["baseline_pass"]["points"]]
    later_points = _points(traces.get(lap))
    section = trial["section"]
    context_start = metric["start_pct"]
    timing = _covered(base_points, context_start, section["end_pct"]) and _covered(later_points, context_start, section["end_pct"])
    rows = _pair_rows(base_points, later_points, context_start, section["start_pct"], section["end_pct"], timing)
    # The two passes come from different recordings. Their steering is drawn
    # together only as road-wheel angle; otherwise it is withheld, not mixed.
    saved_steering = SteeringBasis.from_descriptor(trial["baseline_pass"].get("steering"))
    later_steering = basis_of(analysis)
    if comparable_across_recordings(saved_steering, later_steering):
        steering_pair = pair_descriptor(later_steering)
    else:
        for row in rows:
            row["a"]["steering"] = row["b"]["steering"] = None
        steering_pair = pair_descriptor(
            later_steering, comparable=False,
            reason=("Steering is not compared: "
                    + ("the saved test's steering is steering-wheel angle without a recorded steering ratio."
                       if not saved_steering.road_wheel
                       else "this recording's steering ratio is not recorded, so its steering-wheel angle cannot be matched to road-wheel steering.")),
        )
    result["pair"] = {
        "section_id": section["id"], "start_pct": section["start_pct"], "end_pct": section["end_pct"],
        "context_start_pct": context_start, "timing_complete": timing,
        "a": {"lap": trial["baseline_pass"]["lap"], "role": "before", "section_s": trial["baseline_pass"]["section_s"],
              "green_exposure": trial["baseline_pass"].get("green_exposure")},
        "b": {"lap": lap, "role": "after", "section_s": _round(seconds), "green_exposure": _round(exposure.get(lap), 2)},
        "slip": {"basis": "chassis_velocity_angle", "unit": "deg",
                 "a": _slip_summary(base_points, section["start_pct"], section["end_pct"]),
                 "b": _slip_summary(later_points, section["start_pct"], section["end_pct"])},
        "steering": steering_pair,
        "rows": rows,
    }
    if len(measured) < int(success["minimum_laps"]):
        return finish(_not("not_established", f"{len(measured)} comparable lap{'s' if len(measured) != 1 else ''}; {success['minimum_laps']} are needed."))
    threshold = float(success["threshold_s"])
    if improvement >= threshold:
        return finish({"verdict": "improved", "reasons": [f"{metric['label']} median {later_median:.3f} s against {baseline_median:.3f} s: {improvement:.3f} s faster, past the {threshold:.3f} s threshold."]})
    if improvement <= -threshold:
        return finish({"verdict": "worse", "reasons": [f"{metric['label']} median {later_median:.3f} s against {baseline_median:.3f} s: {-improvement:.3f} s slower, past the {threshold:.3f} s threshold."]})
    return finish({"verdict": "not_established", "reasons": [f"{metric['label']} moved {improvement:+.3f} s; the threshold is {threshold:.3f} s either way."]})


def load_trial(archive_root: Path, trial_id: str) -> dict[str, Any]:
    path = _trial_path(archive_root, trial_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise TrialError("That test is no longer in the archive.") from exc
    except (OSError, ValueError) as exc:
        raise TrialError(f"That test could not be read: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION or payload.get("trial_id") != trial_id:
        raise TrialError("That test uses an unsupported format.")
    # Tests saved before reachability was stored get it from their own window;
    # the next check writes it back with the record.
    if not isinstance(payload.get("reachability"), Mapping):
        payload["reachability"] = _reachability(payload)
    return payload


def _write(archive_root: Path, trial: Mapping[str, Any]) -> None:
    _atomic_write_text(_trial_path(archive_root, trial["trial_id"]), json.dumps(trial, indent=1, ensure_ascii=False, allow_nan=False) + "\n")


def _summary(trial: Mapping[str, Any]) -> dict[str, Any]:
    summary = {key: value for key, value in trial.items() if key not in ("baseline_pass", "evaluations")}
    evaluations = trial.get("evaluations") or []
    summary["evaluation_count"] = len(evaluations)
    summary["latest_evaluation"] = ({key: value for key, value in evaluations[-1].items() if key != "pair"}
                                    if evaluations else None)
    return summary


def _read_analysis(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise TrialError(f"The analysis could not be read: {exc}") from exc
    if not isinstance(value, dict) or not isinstance(value.get("identity"), Mapping):
        raise TrialError("The analysis has an invalid shape.")
    return value


def save_driving_trial_workflow(*, analysis_path: str, comparison_id: str, action_priority: int, archive_root: str) -> dict[str, Any]:
    analysis = _read_analysis(Path(analysis_path))
    try:
        from .workflow import refresh_race_investigation
    except ImportError:  # pragma: no cover
        from workflow import refresh_race_investigation
    refresh_race_investigation(analysis)
    trial = create_trial(analysis, analysis_path=str(analysis_path), comparison_id=comparison_id, action_priority=int(action_priority))
    root = Path(archive_root)
    path = _trial_path(root, trial["trial_id"])
    if path.is_file():
        return {"ok": True, "created": False, "trial": _summary(load_trial(root, trial["trial_id"]))}
    _write(root, trial)
    return {"ok": True, "created": True, "trial": _summary(trial)}


def list_driving_trials_workflow(*, archive_root: str, limit: int = 100) -> dict[str, Any]:
    directory = _trials_dir(Path(archive_root))
    trials, unreadable = [], 0
    for path in sorted(directory.glob("trial-*.json")) if directory.is_dir() else ():
        try:
            trials.append(_summary(load_trial(Path(archive_root), path.stem)))
        except TrialError:
            unreadable += 1
    trials.sort(key=lambda trial: str(trial.get("created_at") or ""), reverse=True)
    return {"ok": True, "schema_version": SCHEMA_VERSION, "trials": trials[:max(1, min(int(limit), 500))],
            "unreadable_count": unreadable}


def evaluate_driving_trial_workflow(*, trial_id: str, analysis_path: str, archive_root: str) -> dict[str, Any]:
    root = Path(archive_root)
    trial = load_trial(root, trial_id)
    analysis = _read_analysis(Path(analysis_path))
    evaluation = evaluate_trial(trial, analysis, analysis_path=str(analysis_path))
    # A repeated check of the same recording replaces its earlier entry; every
    # other check is retained, including unsuccessful ones.
    kept = [item for item in trial.get("evaluations") or () if item.get("analysis_id") != evaluation["analysis_id"]]
    trial["evaluations"] = (kept + [evaluation])[-MAX_EVALUATIONS:]
    _write(root, trial)
    return {"ok": True, "trial": _summary(trial), "evaluation": evaluation}


def load_driving_trial_workflow(*, trial_id: str, archive_root: str) -> dict[str, Any]:
    trial = load_trial(Path(archive_root), trial_id)
    return {"ok": True, "trial": _summary(trial), "evaluations": list(trial.get("evaluations") or ()),
            "baseline_pass": {key: value for key, value in trial["baseline_pass"].items() if key != "points"}}

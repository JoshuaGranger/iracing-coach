"""Offline next-event planning. Fuel decisions remain owned by the deterministic engine."""
from __future__ import annotations

import math
from typing import Any, Mapping

from race_plan_decision import LITRES_PER_GALLON, decide, from_payload


def _positive(value: Any, label: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{label} must be a positive finite number.")
    return float(value)


def build_event_plan(*, distance: float, distance_mode: str = "Laps",
                     analysis: Mapping[str, Any] | None = None,
                     green_burn_gallons: float | None = None,
                     start_fuel_gallons: float | None = None,
                     lap_seconds: float | None = None) -> dict[str, Any]:
    distance = _positive(distance, "Distance")
    if distance is None or distance > 1000:
        raise ValueError("Distance must be greater than zero and at most 1000.")
    if distance_mode not in ("Laps", "Minutes"):
        raise ValueError("Distance mode must be Laps or Minutes.")
    if distance_mode == "Laps" and not distance.is_integer():
        raise ValueError("Lap distance must be a whole number.")
    burn = _positive(green_burn_gallons, "Green fuel per lap")
    fuel = _positive(start_fuel_gallons, "Starting fuel")
    pace = _positive(lap_seconds, "Lap time")
    assumptions: list[str] = []
    source = None
    if analysis:
        forecast = (analysis.get("strategy") or {}).get("forecast") or {}
        payload = forecast.get("race_plan_decision")
        if payload is not None:
            source = from_payload(payload)  # Never fall back through an unreadable authority.
        if pace is None:
            valid = {number for run in analysis.get("runs", []) for number in run.get("valid_green_lap_numbers", [])}
            clean = [float(row["lap_time_s"]) for row in analysis.get("laps", [])
                     if row.get("lap") in valid and isinstance(row.get("lap_time_s"), (int, float))
                     and math.isfinite(row["lap_time_s"]) and row["lap_time_s"] > 0]
            if clean:
                import statistics
                pace = statistics.median(clean)
                assumptions.append(f"Timed distance uses the median clean recorded lap: {pace:g} seconds.")
    manual = any(value is not None for value in (green_burn_gallons, start_fuel_gallons, lap_seconds))
    if burn is None and source is not None and source.green_burn_l_per_lap is not None:
        burn = source.green_burn_l_per_lap / LITRES_PER_GALLON
    if fuel is None and source is not None and source.maximum_start_fuel_l is not None:
        fuel = source.maximum_start_fuel_l / LITRES_PER_GALLON
    laps = distance if distance_mode == "Laps" else math.ceil(distance * 60 / pace) if pace else None
    estimated = manual or distance_mode == "Minutes"
    assumptions.append("Reserves exactly one green lap of fuel.")
    if manual:
        assumptions.append("User-entered values are assumptions; this is an estimate to verify in practice.")
    if analysis:
        assumptions.append("Recorded fuel and pace describe the reference race; verify weather, setup and fuel limits for this event.")
    if distance_mode == "Minutes":
        assumptions.append("Timed distance assumes uninterrupted green running at the supplied or recorded pace; the finish lap and cautions can change it.")
    missing = []
    if burn is None: missing.append("Add green fuel use per lap or select a matching analyzed race.")
    if fuel is None: missing.append("Add available starting fuel or select a matching analyzed race.")
    if laps is None: missing.append("Add a representative lap time to estimate a timed event.")
    decision = decide(scheduled_laps=laps,
                      green_burn_l_per_lap=burn * LITRES_PER_GALLON if burn else None,
                      maximum_start_fuel_l=fuel * LITRES_PER_GALLON if fuel else None,
                      limitations=tuple(missing))
    payload = decision.to_payload()
    payload["assumptions"] = assumptions
    payload["classification"] = "user-assumption estimate" if manual else "recorded-evidence projection" if analysis else "practice preparation"
    return {"contract_version": 1, "race_plan_decision": payload,
            "scheduled_laps": laps, "distance_label": f"{distance:g} laps" if distance_mode == "Laps" else f"{distance:g} minutes" + (f" (~{laps:g} laps)" if laps else " (pace needed)"),
            "estimated": estimated, "evidence": "Estimate" if estimated else "Recorded reference" if analysis else "Preparation",
            "green_burn_gallons": burn, "start_fuel_gallons": fuel,
            "assumptions": assumptions, "missing": missing}

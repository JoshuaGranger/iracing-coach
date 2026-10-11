"""Observed pace, distinct from eligibility for a coaching reference."""
from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from typing import Any


def finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def annotate_observed_trends(
    laps: Sequence[Mapping[str, Any]],
    runs: Sequence[dict[str, Any]],
    damage_repair: Mapping[str, Any],
    exclusion_reasons: Callable[[Mapping[str, Any], str | None], list[str]],
) -> None:
    """Allow traffic and ongoing repair context, while retaining every other screen."""
    incidents = {finite(event.get("candidate_lap"))
                 for event in damage_repair.get("incident_points", {}).get("events", ())
                 if isinstance(event, Mapping)}
    eligible: dict[int, float] = {}
    previous: str | None = None
    for lap in sorted(laps, key=lambda item: finite(item.get("lap")) or -1):
        number = finite(lap.get("lap"))
        time = finite(lap.get("lap_time_s"))
        reasons = exclusion_reasons(lap, previous)
        previous = str(lap.get("flag_state") or "").strip().lower()
        if (number is None or number <= 0 or not number.is_integer()
                or number in incidents or time is None or time <= 0
                or lap.get("pit_entry") is True or lap.get("pit_exit") is True
                or lap.get("pit_in") is True or lap.get("pit_out") is True
                or any(reason not in {"close_traffic", "damage_repair_context"} for reason in reasons)):
            continue
        eligible[int(number)] = time
    for run in runs:
        numbers = sorted({int(number) for raw in run.get("lap_numbers", ())
                          if (number := finite(raw)) is not None and number.is_integer()
                          and int(number) in eligible})
        run["observed_trend"] = None
        if len(numbers) < 3:
            continue
        mean_x = sum(numbers) / len(numbers)
        mean_y = sum(eligible[number] for number in numbers) / len(numbers)
        denominator = sum((number - mean_x) ** 2 for number in numbers)
        if denominator <= 0:
            continue
        slope = sum((number - mean_x) * (eligible[number] - mean_y) for number in numbers) / denominator
        run["observed_trend"] = {
            "slope_s_per_lap": round(slope, 4),
            "intercept_s": round(mean_y - slope * mean_x, 4),
            "first_lap": numbers[0], "last_lap": numbers[-1], "laps_used": len(numbers),
        }

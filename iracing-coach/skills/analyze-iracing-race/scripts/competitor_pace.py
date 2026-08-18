"""Observed competitor pace and gaps, from the recording only.

`COMPETITOR-PACE-001`, prerequisite of ranking a strategy by finishing position.

Every coaching and strategy number in this engine has been computed about a
driver racing alone. Recoverable time says how much quicker he could be than
himself; the strategy comparison ranks plans by his own race time. Neither can
answer the question he actually cares about, which is whether a plan *wins*,
because winning is relative and nothing in the codebase has ever measured
anybody else.

This module measures the two things that are legitimately observable about the
rest of the field: how quickly each car completes laps, and where each car is
relative to the player. Both come from the recorded ``CarIdx`` arrays, which
describe positions on track that were visible to anyone watching.

The privacy boundary is the one :mod:`race_foundations` already draws and this
module does not move it. Competitor fuel, tires, setup, damage and private flag
state are not recorded per car and are never inferred. Neither is intent: a car
circulating three seconds off its earlier pace may be saving fuel, managing
tires, damaged or stuck in traffic, and this module reports the pace without
choosing between those.

Gaps are reported in **time**, not distance, because a strategist reasons in
time - whether a stop can be made without losing a place, whether a car is
within undercut range. Converting requires a pace to divide by, so a gap is
only reported where the relevant car has a measured lap time; a distance gap
dressed up as seconds using an assumed pace would be the sort of invented
number this project rejects elsewhere.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import median
from typing import Any, Mapping, Sequence


#: Version of the competitor-pace contract.
COMPETITOR_PACE_VERSION = 1

#: Completed laps required before a car's pace is reported at all.
MINIMUM_LAPS_FOR_PACE = 3

#: Lap times beyond this multiple of the field median are pit, damage or
#: caution laps rather than green pace, and are excluded from a pace estimate.
OUTLIER_MULTIPLE = 1.35

STATUS_USABLE = "usable"
STATUS_LIMITED = "limited"
STATUS_UNAVAILABLE = "unavailable"


def _finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _array_value(series: Sequence[Any], sample_index: int, car_index: int) -> Any:
    if sample_index < 0 or sample_index >= len(series):
        return None
    row = series[sample_index]
    if not isinstance(row, Sequence) or isinstance(row, (str, bytes, bytearray)):
        return None
    return row[car_index] if 0 <= car_index < len(row) else None


@dataclass(frozen=True)
class CarPace:
    """One car's observed lap pace."""

    car_index: int
    completed_laps: int
    counted_laps: int
    median_lap_s: float | None
    best_lap_s: float | None
    status: str
    reason: str | None

    def to_payload(self) -> dict[str, Any]:
        def rounded(value: float | None) -> float | None:
            return round(value, 3) if value is not None else None

        return {
            "car_index": self.car_index,
            "completed_laps": self.completed_laps,
            "counted_laps": self.counted_laps,
            "median_lap_s": rounded(self.median_lap_s),
            "best_lap_s": rounded(self.best_lap_s),
            "status": self.status,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class RelativePace:
    """A competitor measured against the player."""

    car_index: int
    lap_delta_s: float | None
    faster_than_player: bool | None
    final_gap_s: float | None
    final_gap_laps: float | None

    def to_payload(self) -> dict[str, Any]:
        def rounded(value: float | None, digits: int = 3) -> float | None:
            return round(value, digits) if value is not None else None

        return {
            "car_index": self.car_index,
            "lap_delta_s": rounded(self.lap_delta_s),
            "faster_than_player": self.faster_than_player,
            "final_gap_s": rounded(self.final_gap_s),
            "final_gap_laps": rounded(self.final_gap_laps, 4),
        }


@dataclass(frozen=True)
class FieldReport:
    """What the recording establishes about the rest of the field."""

    cars: tuple[CarPace, ...]
    relative: tuple[RelativePace, ...]
    status: str
    reason: str | None
    field_median_lap_s: float | None
    player_median_lap_s: float | None
    measured_car_count: int

    def to_payload(self) -> dict[str, Any]:
        def rounded(value: float | None) -> float | None:
            return round(value, 3) if value is not None else None

        return {
            "contract_version": COMPETITOR_PACE_VERSION,
            "status": self.status,
            "reason": self.reason,
            "field_median_lap_s": rounded(self.field_median_lap_s),
            "player_median_lap_s": rounded(self.player_median_lap_s),
            "measured_car_count": self.measured_car_count,
            "cars": [car.to_payload() for car in self.cars],
            "relative": [item.to_payload() for item in self.relative],
            "minimum_laps_for_pace": MINIMUM_LAPS_FOR_PACE,
            "excluded_by_boundary": [
                "competitor fuel, tires, setup and damage, which are not recorded",
                "competitor intent: saving, managing, traffic and damage are not "
                "distinguished from chosen pace",
                "private per-car flag state",
            ],
            "gap_definition": (
                "time gap at the last common sample, from the distance between "
                "the cars divided by the trailing car's own measured pace"
            ),
        }


def _lap_completions(
    lap_pct_series: Sequence[Any],
    session_time_s: Sequence[Any],
    car_index: int,
) -> list[float]:
    """Session times at which this car crossed the start/finish line.

    A crossing is a drop in lap distance, which is what the recorded position
    actually shows. Using a lap counter instead would depend on a channel that
    is not always present for every car.
    """

    crossings: list[float] = []
    previous: float | None = None
    for sample_index in range(len(lap_pct_series)):
        raw = _finite(_array_value(lap_pct_series, sample_index, car_index))
        if raw is None or raw < 0.0:
            continue
        position = raw % 1.0
        time_s = _finite(session_time_s[sample_index]) if sample_index < len(session_time_s) else None
        if time_s is None:
            previous = position
            continue
        if previous is not None and position + 0.5 < previous:
            crossings.append(time_s)
        previous = position
    return crossings


def _pace_from_crossings(crossings: Sequence[float]) -> tuple[list[float], int]:
    laps = [
        later - earlier
        for earlier, later in zip(crossings, crossings[1:])
        if later > earlier
    ]
    return laps, len(laps)


def analyze_field(
    *,
    lap_dist_pct_by_car: Sequence[Any],
    session_time_s: Sequence[Any],
    player_car_index: Any,
    car_count: int | None = None,
) -> FieldReport:
    """Measure every recorded car's pace and its standing against the player."""

    if not lap_dist_pct_by_car or not session_time_s:
        return FieldReport(
            cars=(),
            relative=(),
            status=STATUS_UNAVAILABLE,
            reason="no_recorded_competitor_positions",
            field_median_lap_s=None,
            player_median_lap_s=None,
            measured_car_count=0,
        )

    width = 0
    for row in lap_dist_pct_by_car:
        if isinstance(row, Sequence) and not isinstance(row, (str, bytes, bytearray)):
            width = max(width, len(row))
    if car_count is not None:
        width = min(width, car_count)
    if width == 0:
        return FieldReport(
            cars=(),
            relative=(),
            status=STATUS_UNAVAILABLE,
            reason="competitor_position_rows_are_not_arrays",
            field_median_lap_s=None,
            player_median_lap_s=None,
            measured_car_count=0,
        )

    raw_laps: dict[int, list[float]] = {}
    completed: dict[int, int] = {}
    for car_index in range(width):
        crossings = _lap_completions(lap_dist_pct_by_car, session_time_s, car_index)
        laps, count = _pace_from_crossings(crossings)
        raw_laps[car_index] = laps
        completed[car_index] = count

    everything = [value for laps in raw_laps.values() for value in laps]
    field_median = median(everything) if everything else None
    cutoff = field_median * OUTLIER_MULTIPLE if field_median else None

    cars: list[CarPace] = []
    for car_index in range(width):
        laps = raw_laps[car_index]
        counted = [value for value in laps if cutoff is None or value <= cutoff]
        if completed[car_index] < MINIMUM_LAPS_FOR_PACE or len(counted) < MINIMUM_LAPS_FOR_PACE:
            cars.append(
                CarPace(
                    car_index=car_index,
                    completed_laps=completed[car_index],
                    counted_laps=len(counted),
                    median_lap_s=None,
                    best_lap_s=None,
                    status=STATUS_UNAVAILABLE,
                    reason="insufficient_green_laps",
                )
            )
            continue
        cars.append(
            CarPace(
                car_index=car_index,
                completed_laps=completed[car_index],
                counted_laps=len(counted),
                median_lap_s=median(counted),
                best_lap_s=min(counted),
                status=STATUS_USABLE,
                reason=None,
            )
        )

    player_index = _finite(player_car_index)
    player = None
    if player_index is not None and 0 <= int(player_index) < width:
        candidate = cars[int(player_index)]
        player = candidate if candidate.status == STATUS_USABLE else None

    relative: list[RelativePace] = []
    if player is not None and player.median_lap_s:
        last_index = len(lap_dist_pct_by_car) - 1
        player_position = _finite(
            _array_value(lap_dist_pct_by_car, last_index, player.car_index)
        )
        for car in cars:
            if car.car_index == player.car_index or car.status != STATUS_USABLE:
                continue
            delta = (
                car.median_lap_s - player.median_lap_s
                if car.median_lap_s is not None
                else None
            )
            gap_laps: float | None = None
            gap_seconds: float | None = None
            other_position = _finite(
                _array_value(lap_dist_pct_by_car, last_index, car.car_index)
            )
            if player_position is not None and other_position is not None:
                separation = (other_position % 1.0) - (player_position % 1.0)
                # Wrap to the shorter way round; a car 0.9 of a lap "ahead" is
                # a tenth of a lap behind.
                if separation > 0.5:
                    separation -= 1.0
                elif separation < -0.5:
                    separation += 1.0
                gap_laps = separation
                trailing_pace = (
                    car.median_lap_s if separation < 0 else player.median_lap_s
                )
                if trailing_pace:
                    gap_seconds = separation * trailing_pace
            relative.append(
                RelativePace(
                    car_index=car.car_index,
                    lap_delta_s=delta,
                    faster_than_player=(delta < 0.0) if delta is not None else None,
                    final_gap_s=gap_seconds,
                    final_gap_laps=gap_laps,
                )
            )

    measured = sum(1 for car in cars if car.status == STATUS_USABLE)
    if measured == 0:
        status = STATUS_UNAVAILABLE
        reason: str | None = "no_car_completed_enough_green_laps"
    elif player is None:
        status = STATUS_LIMITED
        reason = "player_pace_unavailable"
    elif measured < len(cars):
        status = STATUS_LIMITED
        reason = "some_cars_lack_green_laps"
    else:
        status = STATUS_USABLE
        reason = None

    return FieldReport(
        cars=tuple(cars),
        relative=tuple(sorted(relative, key=lambda item: item.car_index)),
        status=status,
        reason=reason,
        field_median_lap_s=field_median,
        player_median_lap_s=player.median_lap_s if player else None,
        measured_car_count=measured,
    )

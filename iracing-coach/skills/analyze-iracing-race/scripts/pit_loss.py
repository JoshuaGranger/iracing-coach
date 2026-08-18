"""What a pit stop actually costs, measured from the driver's own recordings.

`PIT-LOSS-001`, prerequisite of `STRATEGY-SIM-001`.

No strategy question can be answered without this number. Whether an undercut
works, whether a splash-and-dash beats running long, whether the two-stop is
even in contention - every one of them is a comparison between time spent in
the pit lane and time gained on track, and the engine has never measured the
first half. A search of the analysis engine for ``pit_loss`` before this module
returned nothing.

The measurement is a difference, not a duration. A stop does not cost the time
between entering and leaving the pit lane; it costs that time *minus what the
same stretch of track would have taken at racing speed*, because the car would
have had to cover that ground either way. Reporting the raw pit-lane duration
would overstate every stop by the better part of a green-flag sector, and would
make every strategy look worse than it is by the same amount.

The reference for "what that stretch would have taken" is the driver's own
green lap, resampled by :mod:`lap_reference`, so the comparison is against
himself in the same car on the same day. Where a pit lane spans the
start/finish line the reference span is stitched across it, which is safe here
in a way it is not for corner attribution: both halves come from one continuous
green lap rather than from two consecutive laps.

The stationary portion is reported separately. Service time is set by the crew
and the tire/fuel choice; travel loss is set by the pit lane and the speed
limit. They respond to entirely different decisions, and a single blended
number lets neither be reasoned about.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import median
from typing import Any, Mapping, Sequence

import lap_reference


#: Version of the pit-loss contract.
PIT_LOSS_VERSION = 1

#: At or below this speed the car is treated as stopped in the box.
STATIONARY_SPEED_M_S = 0.5

#: Shortest pit-road presence that counts as a stop rather than a brush of the
#: timing line or a drive-through of the apron.
MINIMUM_EPISODE_S = 3.0

STATUS_USABLE = "usable"
STATUS_LIMITED = "limited"
STATUS_UNAVAILABLE = "unavailable"


def _finite(value: Any) -> float | None:
    return lap_reference._finite(value)


def _truthy(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    number = _finite(value)
    return bool(number) if number is not None else bool(value)


@dataclass(frozen=True)
class PitEpisode:
    """One visit to pit road, and what it cost against staying out."""

    entry_lap: int | None
    entry_pct: float | None
    exit_pct: float | None
    duration_s: float
    stationary_s: float
    green_equivalent_s: float | None
    covered_span_pct: float | None
    loss_s: float | None
    travel_loss_s: float | None
    wrapped_start_finish: bool
    status: str
    reason: str | None

    def to_payload(self) -> dict[str, Any]:
        def rounded(value: float | None, digits: int = 3) -> float | None:
            return round(value, digits) if value is not None else None

        return {
            "entry_lap": self.entry_lap,
            "entry_pct": rounded(self.entry_pct, 6),
            "exit_pct": rounded(self.exit_pct, 6),
            "duration_s": rounded(self.duration_s),
            "stationary_s": rounded(self.stationary_s),
            "green_equivalent_s": rounded(self.green_equivalent_s),
            "covered_span_pct": rounded(self.covered_span_pct, 6),
            "loss_s": rounded(self.loss_s),
            "travel_loss_s": rounded(self.travel_loss_s),
            "wrapped_start_finish": self.wrapped_start_finish,
            "status": self.status,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class PitLossReport:
    """Every measured stop, and the figure a strategy model should use."""

    episodes: tuple[PitEpisode, ...]
    status: str
    median_loss_s: float | None
    median_stationary_s: float | None
    median_travel_loss_s: float | None
    measured_stop_count: int
    reference_lap: int | None

    def to_payload(self) -> dict[str, Any]:
        def rounded(value: float | None) -> float | None:
            return round(value, 3) if value is not None else None

        return {
            "contract_version": PIT_LOSS_VERSION,
            "status": self.status,
            "median_loss_s": rounded(self.median_loss_s),
            "median_stationary_s": rounded(self.median_stationary_s),
            "median_travel_loss_s": rounded(self.median_travel_loss_s),
            "measured_stop_count": self.measured_stop_count,
            "reference_lap": self.reference_lap,
            "episodes": [episode.to_payload() for episode in self.episodes],
            "definition": (
                "loss is pit-road duration minus the time the same stretch of "
                "track would have taken on the reference green lap"
            ),
            "limitations": [
                "Measured against this driver's own green reference lap, not a "
                "field-wide or official pit-lane time.",
                "Service time reflects the fuel and tire work actually requested "
                "on these stops, not the work a future strategy would request.",
                "A stop taken under caution is measured the same way, but the "
                "track-position consequence of a caution stop is not in this number.",
            ],
        }


#: How far a span boundary may be moved to the nearest recorded point. A lap's
#: samples never land exactly on the start/finish line - the lap counter
#: increments and the distance resets - so requiring an exact boundary would
#: refuse every pit lane that spans the line, which is most of them. The
#: allowance matches the distance gap :mod:`lap_reference` already tolerates,
#: and the span actually measured is reported rather than assumed.
BOUNDARY_TOLERANCE_PCT = lap_reference.DEFAULT_MAX_GAP_PCT


def _green_span_seconds(
    reference: lap_reference.LapTrace, start_pct: float, end_pct: float
) -> tuple[float | None, bool, float | None]:
    """Time the reference lap took across a distance span, wrap included.

    Returns the seconds, whether the span crossed the start/finish line, and
    the lap fraction actually covered by recorded samples.
    """

    bins = reference.bins
    wrapped = end_pct < start_pct

    def nearest_covered(target: float, *, upward: bool) -> int | None:
        requested = lap_reference._boundary_index(bins, target)
        limit = int(math.ceil(BOUNDARY_TOLERANCE_PCT * bins))
        for offset in range(limit + 1):
            index = requested + offset if upward else requested - offset
            if 0 <= index <= bins and reference.covers(index):
                return index
        return None

    def span(low: float, high: float) -> tuple[float | None, float]:
        low_index = nearest_covered(low, upward=True)
        high_index = nearest_covered(high, upward=False)
        if low_index is None or high_index is None or high_index <= low_index:
            return None, 0.0
        elapsed = lap_reference._segment_elapsed(reference, low_index, high_index)
        return elapsed, (high_index - low_index) / bins

    if not wrapped:
        seconds, covered = span(start_pct, end_pct)
        return seconds, False, covered if seconds is not None else None
    # Both halves come from one continuous green lap, so stitching them is a
    # single uninterrupted stretch of driving rather than two laps joined.
    first, first_covered = span(start_pct, 1.0)
    second, second_covered = span(0.0, end_pct)
    if first is None or second is None:
        return None, True, None
    return first + second, True, first_covered + second_covered


def measure_pit_loss(
    *,
    session_time_s: Sequence[Any],
    on_pit_road: Sequence[Any],
    lap_dist_pct: Sequence[Any],
    speed_m_s: Sequence[Any] | None = None,
    lap_numbers: Sequence[Any] | None = None,
    reference: lap_reference.LapTrace | None = None,
    minimum_episode_s: float = MINIMUM_EPISODE_S,
) -> PitLossReport:
    """Measure every pit-road visit and what it cost against staying out."""

    episodes: list[PitEpisode] = []
    length = min(len(session_time_s), len(on_pit_road), len(lap_dist_pct))
    index = 0
    while index < length:
        if not _truthy(on_pit_road[index]):
            index += 1
            continue
        start = index
        while index < length and _truthy(on_pit_road[index]):
            index += 1
        end = index - 1

        entry_time = _finite(session_time_s[start])
        exit_time = _finite(session_time_s[end])
        if entry_time is None or exit_time is None or exit_time <= entry_time:
            continue
        duration = exit_time - entry_time
        if duration < minimum_episode_s:
            continue

        entry_pct = _finite(lap_dist_pct[start])
        exit_pct = _finite(lap_dist_pct[end])
        entry_lap = None
        if lap_numbers is not None and start < len(lap_numbers):
            lap_value = _finite(lap_numbers[start])
            entry_lap = int(lap_value) if lap_value is not None else None

        stationary = 0.0
        if speed_m_s is not None:
            for position in range(start, min(end + 1, len(speed_m_s))):
                speed = _finite(speed_m_s[position])
                if speed is None or speed > STATIONARY_SPEED_M_S:
                    continue
                previous = _finite(session_time_s[position - 1]) if position > start else None
                current = _finite(session_time_s[position])
                if previous is not None and current is not None and current > previous:
                    stationary += current - previous

        green_equivalent: float | None = None
        covered_span: float | None = None
        wrapped = False
        reason: str | None = None
        if reference is None:
            reason = "no_green_reference_lap"
        elif entry_pct is None or exit_pct is None:
            reason = "pit_road_distance_unavailable"
        else:
            green_equivalent, wrapped, covered_span = _green_span_seconds(
                reference, entry_pct, exit_pct
            )
            if green_equivalent is None:
                reason = "reference_lap_does_not_cover_pit_span"

        loss = duration - green_equivalent if green_equivalent is not None else None
        episodes.append(
            PitEpisode(
                entry_lap=entry_lap,
                entry_pct=entry_pct,
                exit_pct=exit_pct,
                duration_s=duration,
                stationary_s=stationary,
                green_equivalent_s=green_equivalent,
                covered_span_pct=covered_span,
                loss_s=loss,
                travel_loss_s=(loss - stationary) if loss is not None else None,
                wrapped_start_finish=wrapped,
                status=STATUS_USABLE if loss is not None else STATUS_UNAVAILABLE,
                reason=reason,
            )
        )

    measured = [episode for episode in episodes if episode.loss_s is not None]
    if not episodes:
        status = STATUS_UNAVAILABLE
    elif not measured:
        status = STATUS_UNAVAILABLE
    elif len(measured) < len(episodes):
        status = STATUS_LIMITED
    else:
        status = STATUS_USABLE
    return PitLossReport(
        episodes=tuple(episodes),
        status=status,
        median_loss_s=median([e.loss_s for e in measured]) if measured else None,
        median_stationary_s=(
            median([e.stationary_s for e in measured]) if measured else None
        ),
        median_travel_loss_s=(
            median([e.travel_loss_s for e in measured if e.travel_loss_s is not None])
            if measured
            else None
        ),
        measured_stop_count=len(measured),
        reference_lap=reference.lap_number if reference is not None else None,
    )

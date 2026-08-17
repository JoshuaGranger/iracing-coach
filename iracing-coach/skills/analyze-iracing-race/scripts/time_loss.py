"""Rank where a driver is losing time, by how much time it is worth.

`REFERENCE-DELTA-001`, `TIME-LOSS-RANK-001`.

The engine's existing coaching emits every observation that crosses a fixed
threshold, in the order the checks happen to run, with no notion of what any of
them costs. A crew chief does the opposite: he tells you the one corner worth
fixing first, because he knows what it is worth.

This module supplies the missing quantity. For every segment of track it
compares the driver's *typical* time through it against his *own best* time
through it, and calls the difference recoverable. Ranking by that number
produces an ordered list whose first entry is, by construction, the largest
single improvement available.

Recoverable time is deliberately measured against the driver's own best rather
than against any external or modelled target:

* it is **proven achievable** - he has already driven the segment that quickly,
  in these conditions, in this car, on this set of tires;
* it needs **no external data**, so it works on the first race at a new track
  and does not wait on any provider; and
* it **cannot flatter or fabricate**, because both numbers are measured times
  from the same session.

What it is not is a prediction that the sum is available on one lap. Segment
bests come from different laps whose entry states may be incompatible, and the
total is reported as a ceiling with that stated. The honest claim is "you have
driven each of these faster than you typically do", never "you can lap this
much faster".

A consistency count travels with every segment for the same reason. One
exceptional lap through a corner and eleven ordinary ones is a different
coaching situation from a corner the driver nails half the time, and a bare
delta cannot distinguish them.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Any, Mapping, Sequence

import lap_reference


#: Version of the time-loss contract.
TIME_LOSS_VERSION = 1

#: Laps required in a segment before a median is trusted.
MINIMUM_LAPS_FOR_RANKING = 3

#: A lap within this margin of the segment best counts as a repeat of it.
NEAR_BEST_MARGIN_S = 0.05

STATUS_USABLE = lap_reference.STATUS_USABLE
STATUS_LIMITED = lap_reference.STATUS_LIMITED
STATUS_UNAVAILABLE = lap_reference.STATUS_UNAVAILABLE

#: A corner spanning the start/finish line cannot be attributed from
#: within-lap traces: its two halves belong to consecutive laps, and joining
#: them inside one lap would compare the end of a lap against its own start.
WRAP_REASON = "segment_wraps_start_finish"


@dataclass(frozen=True)
class SegmentLoss:
    """Recoverable time in one segment, with the evidence behind it."""

    name: str
    start_pct: float
    end_pct: float
    status: str
    reason: str | None
    lap_count: int
    best_s: float | None
    median_s: float | None
    worst_s: float | None
    recoverable_s: float | None
    best_lap: int | None
    near_best_lap_count: int
    minimum_speed_on_best_mps: float | None
    median_minimum_speed_mps: float | None

    @property
    def sort_key(self) -> float:
        return self.recoverable_s if self.recoverable_s is not None else -1.0

    def to_payload(self) -> dict[str, Any]:
        def rounded(value: float | None, digits: int = 4) -> float | None:
            return round(value, digits) if value is not None else None

        return {
            "name": self.name,
            "start_pct": round(self.start_pct, 6),
            "end_pct": round(self.end_pct, 6),
            "status": self.status,
            "reason": self.reason,
            "lap_count": self.lap_count,
            "best_s": rounded(self.best_s),
            "median_s": rounded(self.median_s),
            "worst_s": rounded(self.worst_s),
            "recoverable_s": rounded(self.recoverable_s),
            "best_lap": self.best_lap,
            "near_best_lap_count": self.near_best_lap_count,
            "minimum_speed_on_best_mps": rounded(self.minimum_speed_on_best_mps, 3),
            "median_minimum_speed_mps": rounded(self.median_minimum_speed_mps, 3),
        }


@dataclass(frozen=True)
class TimeLossReport:
    """Every segment, ranked by the time it is costing."""

    segments: tuple[SegmentLoss, ...]
    status: str
    total_recoverable_s: float | None
    ranked_names: tuple[str, ...]
    usable_lap_count: int
    excluded_segment_count: int

    def top(self, count: int = 3) -> list[SegmentLoss]:
        return [
            segment
            for segment in self.segments
            if segment.status == STATUS_USABLE
        ][:count]

    def to_payload(self) -> dict[str, Any]:
        return {
            "contract_version": TIME_LOSS_VERSION,
            "status": self.status,
            "usable_lap_count": self.usable_lap_count,
            "excluded_segment_count": self.excluded_segment_count,
            "total_recoverable_s": (
                round(self.total_recoverable_s, 4)
                if self.total_recoverable_s is not None
                else None
            ),
            "ranked_names": list(self.ranked_names),
            "segments": [segment.to_payload() for segment in self.segments],
            "classification": (
                "recoverable time is this driver's own median minus his own best in "
                "each segment; the total is a ceiling composed from different laps, "
                "not an achievable single-lap time"
            ),
            "minimum_laps_for_ranking": MINIMUM_LAPS_FOR_RANKING,
        }


def _segment_name(segment: Mapping[str, Any], ordinal: int) -> str:
    for key in ("name", "label"):
        value = segment.get(key)
        if value:
            return str(value)
    number = segment.get("segment")
    if number is not None:
        return f"Corner {number}"
    return f"Segment {ordinal}"


def _finite(value: Any) -> float | None:
    return lap_reference._finite(value)


def _collect(
    traces: Sequence[lap_reference.LapTrace],
    start_pct: float,
    end_pct: float,
) -> list[tuple[float, int | None, float | None]]:
    """Return (elapsed, lap number, minimum speed) for every covering lap."""

    collected: list[tuple[float, int | None, float | None]] = []
    for trace in traces:
        if trace.status == STATUS_UNAVAILABLE:
            continue
        start_index = lap_reference._boundary_index(trace.bins, start_pct)
        end_index = lap_reference._boundary_index(trace.bins, end_pct)
        elapsed = lap_reference._segment_elapsed(trace, start_index, end_index)
        if elapsed is None or elapsed <= 0.0:
            continue
        collected.append(
            (
                elapsed,
                trace.lap_number,
                lap_reference._segment_extreme_speed(trace, start_index, end_index),
            )
        )
    return collected


def analyze_time_loss(
    traces: Sequence[lap_reference.LapTrace],
    segments: Sequence[Mapping[str, Any]],
    *,
    minimum_laps: int = MINIMUM_LAPS_FOR_RANKING,
) -> TimeLossReport:
    """Rank segments by the time this driver is leaving in each of them."""

    usable_traces = [
        trace for trace in traces if trace.status != STATUS_UNAVAILABLE
    ]
    results: list[SegmentLoss] = []
    excluded = 0

    for ordinal, segment in enumerate(segments, start=1):
        name = _segment_name(segment, ordinal)
        start_pct = _finite(segment.get("start_pct"))
        end_pct = _finite(segment.get("end_pct"))

        if start_pct is None or end_pct is None:
            excluded += 1
            results.append(
                _unavailable(name, start_pct or 0.0, end_pct or 0.0, "segment_bounds_missing")
            )
            continue
        if bool(segment.get("wraps_start_finish")) or end_pct <= start_pct:
            excluded += 1
            results.append(_unavailable(name, start_pct, end_pct, WRAP_REASON))
            continue

        collected = _collect(usable_traces, start_pct, end_pct)
        if len(collected) < minimum_laps:
            excluded += 1
            results.append(
                _unavailable(
                    name,
                    start_pct,
                    end_pct,
                    "insufficient_covering_laps",
                    lap_count=len(collected),
                )
            )
            continue

        times = [item[0] for item in collected]
        best_value = min(times)
        best_entry = min(collected, key=lambda item: item[0])
        median_value = median(times)
        speeds = [item[2] for item in collected if item[2] is not None]
        results.append(
            SegmentLoss(
                name=name,
                start_pct=start_pct,
                end_pct=end_pct,
                status=STATUS_USABLE,
                reason=None,
                lap_count=len(collected),
                best_s=best_value,
                median_s=median_value,
                worst_s=max(times),
                recoverable_s=max(0.0, median_value - best_value),
                best_lap=best_entry[1],
                near_best_lap_count=sum(
                    1 for value in times if value - best_value <= NEAR_BEST_MARGIN_S
                ),
                minimum_speed_on_best_mps=best_entry[2],
                median_minimum_speed_mps=median(speeds) if speeds else None,
            )
        )

    ranked = sorted(
        results,
        key=lambda item: (item.status != STATUS_USABLE, -item.sort_key, item.name),
    )
    usable = [item for item in ranked if item.status == STATUS_USABLE]
    total = sum(item.recoverable_s or 0.0 for item in usable) if usable else None
    if not usable:
        status = STATUS_UNAVAILABLE
    elif excluded:
        status = STATUS_LIMITED
    else:
        status = STATUS_USABLE
    return TimeLossReport(
        segments=tuple(ranked),
        status=status,
        total_recoverable_s=total,
        ranked_names=tuple(item.name for item in usable),
        usable_lap_count=len(usable_traces),
        excluded_segment_count=excluded,
    )


def _unavailable(
    name: str,
    start_pct: float,
    end_pct: float,
    reason: str,
    *,
    lap_count: int = 0,
) -> SegmentLoss:
    return SegmentLoss(
        name=name,
        start_pct=start_pct,
        end_pct=end_pct,
        status=STATUS_UNAVAILABLE,
        reason=reason,
        lap_count=lap_count,
        best_s=None,
        median_s=None,
        worst_s=None,
        recoverable_s=None,
        best_lap=None,
        near_best_lap_count=0,
        minimum_speed_on_best_mps=None,
        median_minimum_speed_mps=None,
    )

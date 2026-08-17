"""Distance-domain lap alignment and accumulated time delta.

`REFERENCE-DELTA-001`.

Every coaching signal in this repository before this module compared the driver
against *himself over time*: early-run laps against late-run laps, one phase
against another. That answers "did my driving drift?" and it never answers the
question a driver actually asks, which is "where am I losing time?". Answering
that needs one thing the codebase did not have - a way to put two laps on the
same spatial grid and say how much time separates them at every point around
the track.

That is what this module is. It is deliberately free of any opinion about
*which* lap is the reference: the driver's own best lap, a composite of his
best segments, or an external lap all enter through the same door. The module
that has an opinion is the caller.

Three properties are load-bearing, and the tests pin all three.

* **Anchoring is by distance, not by sample index.** A lap's first recorded
  sample is not the start/finish line; it is wherever the logger happened to
  land, which differs lap to lap. Elapsed time is therefore always measured
  from an interpolated time at a *shared distance*, never from the first
  sample. Getting this wrong biases every delta by the sampling phase, which is
  a quiet error that looks like real time.

* **Segment deltas are anchor-independent.** Because a segment delta is the
  difference of the accumulated delta at its two boundaries, the anchor cancels
  exactly. This is the invariant that makes per-corner attribution trustworthy
  even on laps with partial coverage, and :func:`segment_deltas` relies on it.

* **A gap is never interpolated across.** If the recording skips a stretch of
  track, the grid points inside that stretch are unavailable rather than
  linearly filled. Filling them would manufacture a time for distance that was
  never observed - exactly the failure `CAP-009` prohibits elsewhere in the
  product. Coverage is reported so a consumer can refuse a comparison rather
  than quietly present a partial one as whole.

The interpolation between two bracketing samples is linear in time against
distance. At ordinary logging rates a sample spans well under a tenth of a
percent of a lap, so the residual is far below the resolution any coaching
claim is made at; the alternative - snapping to the nearest sample - would
introduce a quantization error that is *larger*, and phase-dependent.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence


#: Version of the lap-reference contract.
LAP_REFERENCE_VERSION = 1

#: Default number of evenly spaced grid points around one lap.
DEFAULT_BINS = 1000

#: Largest distance gap, as a lap fraction, that may be interpolated across.
#: Ordinary logging places consecutive samples three orders of magnitude closer
#: than this, so it triggers only on a genuine recording dropout.
DEFAULT_MAX_GAP_PCT = 0.02

#: Grid coverage required before a trace is called fully usable.
USABLE_COVERAGE = 0.95

#: Grid coverage below which a trace carries no comparison at all.
MINIMUM_COVERAGE = 0.50

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


def _classify(coverage: float) -> str:
    if coverage >= USABLE_COVERAGE:
        return STATUS_USABLE
    if coverage >= MINIMUM_COVERAGE:
        return STATUS_LIMITED
    return STATUS_UNAVAILABLE


@dataclass(frozen=True)
class LapTrace:
    """One lap resampled onto the shared lap-distance grid.

    ``session_time_s`` holds the interpolated absolute session time at each grid
    point, or ``None`` where the recording does not support one. Absolute time
    is retained rather than elapsed time precisely because the anchor is a
    comparison-time decision: two laps must be anchored at a distance they both
    cover, which is not knowable while a single lap is being built.
    """

    lap_number: int | None
    bins: int
    session_time_s: tuple[float | None, ...]
    speed_mps: tuple[float | None, ...]
    coverage: float
    status: str
    covered_time_s: float | None
    sample_count: int
    regressed_sample_count: int
    gap_count: int

    @property
    def grid(self) -> tuple[float, ...]:
        return tuple(index / self.bins for index in range(self.bins + 1))

    def covers(self, index: int) -> bool:
        return (
            0 <= index < len(self.session_time_s)
            and self.session_time_s[index] is not None
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "contract_version": LAP_REFERENCE_VERSION,
            "lap_number": self.lap_number,
            "bins": self.bins,
            "status": self.status,
            "coverage": round(self.coverage, 4),
            "covered_time_s": (
                round(self.covered_time_s, 4)
                if self.covered_time_s is not None
                else None
            ),
            "sample_count": self.sample_count,
            "regressed_sample_count": self.regressed_sample_count,
            "gap_count": self.gap_count,
        }


@dataclass(frozen=True)
class SegmentDelta:
    """Time gained or lost across one named stretch of track.

    ``delta_s`` is positive when the comparison lap is *slower* through the
    segment, matching the sign convention of the accumulated delta.
    """

    name: str
    start_pct: float
    end_pct: float
    delta_s: float | None
    status: str
    reference_time_s: float | None
    comparison_time_s: float | None
    reference_minimum_speed_mps: float | None
    comparison_minimum_speed_mps: float | None

    def to_payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "start_pct": round(self.start_pct, 6),
            "end_pct": round(self.end_pct, 6),
            "delta_s": round(self.delta_s, 4) if self.delta_s is not None else None,
            "status": self.status,
            "reference_time_s": (
                round(self.reference_time_s, 4)
                if self.reference_time_s is not None
                else None
            ),
            "comparison_time_s": (
                round(self.comparison_time_s, 4)
                if self.comparison_time_s is not None
                else None
            ),
            "reference_minimum_speed_mps": (
                round(self.reference_minimum_speed_mps, 3)
                if self.reference_minimum_speed_mps is not None
                else None
            ),
            "comparison_minimum_speed_mps": (
                round(self.comparison_minimum_speed_mps, 3)
                if self.comparison_minimum_speed_mps is not None
                else None
            ),
        }


@dataclass(frozen=True)
class DeltaTrace:
    """Accumulated time delta between a comparison lap and a reference lap."""

    reference_lap: int | None
    comparison_lap: int | None
    bins: int
    anchor_index: int | None
    delta_s: tuple[float | None, ...]
    status: str
    coverage: float
    total_delta_s: float | None

    def to_payload(self) -> dict[str, Any]:
        return {
            "contract_version": LAP_REFERENCE_VERSION,
            "reference_lap": self.reference_lap,
            "comparison_lap": self.comparison_lap,
            "bins": self.bins,
            "status": self.status,
            "coverage": round(self.coverage, 4),
            "anchor_pct": (
                round(self.anchor_index / self.bins, 6)
                if self.anchor_index is not None
                else None
            ),
            "total_delta_s": (
                round(self.total_delta_s, 4)
                if self.total_delta_s is not None
                else None
            ),
            "delta_definition": "comparison lap minus reference lap; positive is slower",
        }


def _forward_progress_pairs(
    distance_pct: Sequence[Any],
    session_time_s: Sequence[Any],
    speed_mps: Sequence[Any] | None,
    indices: Iterable[int],
) -> tuple[list[tuple[float, float, float | None]], int]:
    """Keep only samples that advance around the lap.

    A spin, a reversal, or an off-track excursion can send ``LapDistPct``
    backwards inside a single lap. Those samples are dropped rather than
    reordered: the resampler requires a monotone distance axis, and inventing
    an ordering for a car that was travelling backwards would put time against
    distance it was not making. The count of dropped samples is returned so the
    caller can distinguish a clean lap from a salvaged one.
    """

    pairs: list[tuple[float, float, float | None]] = []
    regressed = 0
    last_pct: float | None = None
    last_time: float | None = None
    for index in indices:
        pct = _finite(distance_pct[index]) if index < len(distance_pct) else None
        time_s = _finite(session_time_s[index]) if index < len(session_time_s) else None
        if pct is None or time_s is None:
            continue
        if not 0.0 <= pct <= 1.0:
            continue
        if last_pct is not None and pct <= last_pct:
            regressed += 1
            continue
        if last_time is not None and time_s <= last_time:
            regressed += 1
            continue
        speed = None
        if speed_mps is not None and index < len(speed_mps):
            speed = _finite(speed_mps[index])
        pairs.append((pct, time_s, speed))
        last_pct = pct
        last_time = time_s
    return pairs, regressed


def build_lap_trace(
    distance_pct: Sequence[Any],
    session_time_s: Sequence[Any],
    indices: Sequence[int],
    *,
    speed_mps: Sequence[Any] | None = None,
    lap_number: int | None = None,
    bins: int = DEFAULT_BINS,
    max_gap_pct: float = DEFAULT_MAX_GAP_PCT,
) -> LapTrace:
    """Resample one lap onto the shared distance grid.

    ``indices`` selects the samples belonging to the lap, in recording order.
    """

    if bins < 1:
        raise ValueError("bins must be at least 1")
    if not 0.0 < max_gap_pct <= 1.0:
        raise ValueError("max_gap_pct must fall in (0, 1]")

    pairs, regressed = _forward_progress_pairs(
        distance_pct, session_time_s, speed_mps, indices
    )
    times: list[float | None] = [None] * (bins + 1)
    speeds: list[float | None] = [None] * (bins + 1)
    gap_count = 0

    if len(pairs) >= 2:
        cursor = 0
        for grid_index in range(bins + 1):
            target = grid_index / bins
            while cursor + 1 < len(pairs) and pairs[cursor + 1][0] < target:
                cursor += 1
            if cursor + 1 >= len(pairs):
                break
            left_pct, left_time, left_speed = pairs[cursor]
            right_pct, right_time, right_speed = pairs[cursor + 1]
            if target < left_pct or target > right_pct:
                continue
            span = right_pct - left_pct
            if span > max_gap_pct:
                # A recording dropout. Filling it would manufacture a time for
                # distance that was never observed.
                continue
            weight = 0.0 if span <= 0.0 else (target - left_pct) / span
            times[grid_index] = left_time + (right_time - left_time) * weight
            if left_speed is not None and right_speed is not None:
                speeds[grid_index] = left_speed + (right_speed - left_speed) * weight
            elif left_speed is not None and weight <= 0.0:
                speeds[grid_index] = left_speed
        gap_count = sum(
            1
            for left, right in zip(pairs, pairs[1:])
            if right[0] - left[0] > max_gap_pct
        )

    covered = [value for value in times if value is not None]
    coverage = len(covered) / (bins + 1)
    covered_time = max(covered) - min(covered) if len(covered) >= 2 else None
    return LapTrace(
        lap_number=lap_number,
        bins=bins,
        session_time_s=tuple(times),
        speed_mps=tuple(speeds),
        coverage=coverage,
        status=_classify(coverage),
        covered_time_s=covered_time,
        sample_count=len(pairs),
        regressed_sample_count=regressed,
        gap_count=gap_count,
    )


def compare_laps(reference: LapTrace, comparison: LapTrace) -> DeltaTrace:
    """Accumulate the time delta between two aligned laps.

    The result is anchored at the first grid point both laps cover, so the
    delta there is exactly zero and every later value is the time the
    comparison lap has gained or lost *since that point*. Segment attribution
    differences the accumulated delta and is therefore independent of which
    anchor was chosen.
    """

    if reference.bins != comparison.bins:
        raise ValueError("laps must share a grid to be compared")
    bins = reference.bins
    anchor_index: int | None = None
    for index in range(bins + 1):
        if reference.covers(index) and comparison.covers(index):
            anchor_index = index
            break

    deltas: list[float | None] = [None] * (bins + 1)
    if anchor_index is not None:
        reference_anchor = reference.session_time_s[anchor_index]
        comparison_anchor = comparison.session_time_s[anchor_index]
        assert reference_anchor is not None and comparison_anchor is not None
        for index in range(bins + 1):
            reference_time = reference.session_time_s[index]
            comparison_time = comparison.session_time_s[index]
            if reference_time is None or comparison_time is None:
                continue
            deltas[index] = (comparison_time - comparison_anchor) - (
                reference_time - reference_anchor
            )

    covered = [value for value in deltas if value is not None]
    coverage = len(covered) / (bins + 1)
    total = None
    for value in reversed(deltas):
        if value is not None:
            total = value
            break
    return DeltaTrace(
        reference_lap=reference.lap_number,
        comparison_lap=comparison.lap_number,
        bins=bins,
        anchor_index=anchor_index,
        delta_s=tuple(deltas),
        status=_classify(coverage),
        coverage=coverage,
        total_delta_s=total,
    )


def _boundary_index(bins: int, pct: float) -> int:
    return max(0, min(bins, int(round(pct * bins))))


def _segment_extreme_speed(trace: LapTrace, start: int, end: int) -> float | None:
    values = [
        value
        for value in trace.speed_mps[start : end + 1]
        if value is not None
    ]
    return min(values) if values else None


def _segment_elapsed(trace: LapTrace, start: int, end: int) -> float | None:
    first = trace.session_time_s[start]
    last = trace.session_time_s[end]
    if first is None or last is None:
        return None
    return last - first


def segment_deltas(
    reference: LapTrace,
    comparison: LapTrace,
    segments: Sequence[Mapping[str, Any]],
    *,
    delta: DeltaTrace | None = None,
) -> list[SegmentDelta]:
    """Attribute the accumulated delta to named stretches of track.

    Each segment is a mapping with ``name``, ``start_pct`` and ``end_pct``. A
    segment is reported only when both laps cover both of its boundaries;
    otherwise it carries an explicit unavailable status rather than a number
    derived from one covered end.
    """

    delta = delta or compare_laps(reference, comparison)
    bins = reference.bins
    results: list[SegmentDelta] = []
    for segment in segments:
        name = str(segment.get("name") or "segment")
        start_pct = _finite(segment.get("start_pct"))
        end_pct = _finite(segment.get("end_pct"))
        if start_pct is None or end_pct is None or end_pct <= start_pct:
            results.append(
                SegmentDelta(
                    name=name,
                    start_pct=start_pct or 0.0,
                    end_pct=end_pct or 0.0,
                    delta_s=None,
                    status=STATUS_UNAVAILABLE,
                    reference_time_s=None,
                    comparison_time_s=None,
                    reference_minimum_speed_mps=None,
                    comparison_minimum_speed_mps=None,
                )
            )
            continue
        start_index = _boundary_index(bins, start_pct)
        end_index = _boundary_index(bins, end_pct)
        start_delta = delta.delta_s[start_index]
        end_delta = delta.delta_s[end_index]
        if start_delta is None or end_delta is None:
            status = STATUS_UNAVAILABLE
            value = None
        else:
            status = STATUS_USABLE
            value = end_delta - start_delta
        results.append(
            SegmentDelta(
                name=name,
                start_pct=start_pct,
                end_pct=end_pct,
                delta_s=value,
                status=status,
                reference_time_s=_segment_elapsed(reference, start_index, end_index),
                comparison_time_s=_segment_elapsed(comparison, start_index, end_index),
                reference_minimum_speed_mps=_segment_extreme_speed(
                    reference, start_index, end_index
                ),
                comparison_minimum_speed_mps=_segment_extreme_speed(
                    comparison, start_index, end_index
                ),
            )
        )
    return results


def uniform_segments(count: int, *, prefix: str = "S") -> list[dict[str, Any]]:
    """Evenly spaced fallback segments for a track with no corner profile."""

    if count < 1:
        raise ValueError("count must be at least 1")
    return [
        {
            "name": f"{prefix}{index + 1}",
            "start_pct": index / count,
            "end_pct": (index + 1) / count,
        }
        for index in range(count)
    ]


@dataclass(frozen=True)
class TheoreticalBest:
    """The best observed time in each segment, and what it sums to.

    This is the honest ceiling on a driver's current pace: every segment was
    actually driven at this speed, just not on the same lap. It is not a
    prediction and it is not a target lap time - joining two segments driven on
    different laps ignores whether their entry states are compatible, which is
    why the record keeps the contributing lap for each segment rather than
    presenting one synthetic lap.
    """

    segments: tuple[str, ...]
    segment_time_s: tuple[float | None, ...]
    contributing_lap: tuple[int | None, ...]
    total_s: float | None
    status: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "contract_version": LAP_REFERENCE_VERSION,
            "status": self.status,
            "total_s": round(self.total_s, 4) if self.total_s is not None else None,
            "segments": [
                {
                    "name": name,
                    "time_s": round(time_s, 4) if time_s is not None else None,
                    "lap_number": lap,
                }
                for name, time_s, lap in zip(
                    self.segments, self.segment_time_s, self.contributing_lap
                )
            ],
            "classification": (
                "best observed segment times from different laps; not a predicted lap"
            ),
        }


def theoretical_best(
    traces: Sequence[LapTrace], segments: Sequence[Mapping[str, Any]]
) -> TheoreticalBest:
    """Compose the best observed time for every segment across usable laps."""

    names: list[str] = []
    best_times: list[float | None] = []
    best_laps: list[int | None] = []
    for segment in segments:
        name = str(segment.get("name") or "segment")
        start_pct = _finite(segment.get("start_pct"))
        end_pct = _finite(segment.get("end_pct"))
        names.append(name)
        if start_pct is None or end_pct is None or end_pct <= start_pct:
            best_times.append(None)
            best_laps.append(None)
            continue
        best_value: float | None = None
        best_lap: int | None = None
        for trace in traces:
            if trace.status == STATUS_UNAVAILABLE:
                continue
            start_index = _boundary_index(trace.bins, start_pct)
            end_index = _boundary_index(trace.bins, end_pct)
            elapsed = _segment_elapsed(trace, start_index, end_index)
            if elapsed is None or elapsed <= 0.0:
                continue
            if best_value is None or elapsed < best_value:
                best_value = elapsed
                best_lap = trace.lap_number
        best_times.append(best_value)
        best_laps.append(best_lap)

    complete = all(value is not None for value in best_times) and bool(best_times)
    total = sum(value for value in best_times if value is not None) if complete else None
    return TheoreticalBest(
        segments=tuple(names),
        segment_time_s=tuple(best_times),
        contributing_lap=tuple(best_laps),
        total_s=total,
        status=STATUS_USABLE if complete else STATUS_LIMITED,
    )

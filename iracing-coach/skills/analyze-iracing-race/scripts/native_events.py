"""Bounded native-rate event detection for one finalized iRacing IBT file.

The detector keeps the source file read-only and streams selected SDK channels
through :func:`ibt_reader.iter_telemetry_chunks`.  It deliberately separates
measured inputs, derived threshold events, and diagnostic proxies.  In
particular, a wheel-speed divergence is never reported as proof that a tire
locked or spun.
"""

from __future__ import annotations

from collections import Counter
import heapq
import math
import os
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence

try:  # Support package imports and direct loading from the scripts folder.
    from .ibt_reader import iter_telemetry_chunks, scan_ibt
except ImportError:  # pragma: no cover - normal for direct CLI/test loading.
    from ibt_reader import iter_telemetry_chunks, scan_ibt


__all__ = [
    "NativeEventError",
    "SUPPORTED_EVENT_SELECTION_MODES",
    "SUPPORTED_EVENT_TYPES",
    "detect_native_telemetry_events",
    "select_native_events_by_severity",
]


class NativeEventError(Exception):
    """A native event query cannot be completed safely."""


SUPPORTED_EVENT_TYPES = (
    "brake_onset",
    "brake_release",
    "pit_transition",
    "steering_torque_peak",
    "shock_velocity_peak",
    "wheel_speed_divergence",
)
SUPPORTED_EVENT_SELECTION_MODES = ("chronological", "severity")

_CONTEXT_CHANNELS = ("SessionTime", "Lap", "LapDistPct")
_BRAKE_CHANNELS = ("Brake", "BrakeRaw")
_PIT_CHANNELS = ("OnPitRoad", "PitstopActive", "PlayerCarInPitStall")
_TORQUE_CHANNELS = ("SteeringWheelTorque_ST", "SteeringWheelTorque")
_WHEEL_CHANNELS = tuple(f"{corner}speed" for corner in ("LF", "RF", "LR", "RR"))
_MAX_EVENTS = 1_000
_CHUNK_SIZE = 2_048
_BRAKE_ON_THRESHOLD = 0.05
_BRAKE_OFF_THRESHOLD = 0.02
_WHEEL_DISTANCE_BINS = 120
_WHEEL_MIN_BASELINE_LAPS = 2
_WHEEL_MIN_SPEED_MPS = 5.0
_WHEEL_MIN_RATIO_DELTA = 0.06
_WHEEL_SIGMA_MULTIPLIER = 4.0
_SHOCK_PATTERN = re.compile(
    r"^(?:LF|RF|LR|RR)(?:SH)?(?:shock|damper)(?:Vel|Velocity)$",
    re.IGNORECASE,
)


def _finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return float(value)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _json_number(value: Any) -> int | float | None:
    number = _finite(value)
    if number is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        return int(value)
    return number


def _first_available(available: set[str], aliases: Sequence[str]) -> str | None:
    return next((name for name in aliases if name in available), None)


def _normalise_event_types(event_types: Iterable[str] | str | None) -> tuple[str, ...]:
    if event_types is None:
        return SUPPORTED_EVENT_TYPES
    requested = [event_types] if isinstance(event_types, str) else list(event_types)
    if not requested:
        return ()
    if any(not isinstance(item, str) or not item for item in requested):
        raise ValueError("event_types must contain non-empty strings")
    unknown = sorted(set(requested) - set(SUPPORTED_EVENT_TYPES))
    if unknown:
        raise ValueError(
            "unsupported event type(s): "
            + ", ".join(unknown)
            + "; supported types: "
            + ", ".join(SUPPORTED_EVENT_TYPES)
        )
    return tuple(dict.fromkeys(requested))


def _normalise_max_events(max_events: int) -> int:
    if isinstance(max_events, bool) or not isinstance(max_events, int):
        raise ValueError(f"max_events must be an integer between 1 and {_MAX_EVENTS}")
    if not 1 <= max_events <= _MAX_EVENTS:
        raise ValueError(f"max_events must be between 1 and {_MAX_EVENTS}")
    return max_events


def _normalise_selection_mode(selection_mode: str) -> str:
    if not isinstance(selection_mode, str):
        raise ValueError(
            "selection_mode must be one of: "
            + ", ".join(SUPPORTED_EVENT_SELECTION_MODES)
        )
    normalized = selection_mode.strip().casefold()
    if normalized not in SUPPORTED_EVENT_SELECTION_MODES:
        raise ValueError(
            "selection_mode must be one of: "
            + ", ".join(SUPPORTED_EVENT_SELECTION_MODES)
        )
    return normalized


def _event_severity_score(event: Mapping[str, Any]) -> float:
    """Return a deterministic within-detector selection score.

    Scores rank events within and, for leftover capacity, across event types.
    They do not change the measured/derived/proxy evidence classification.
    """

    event_type = str(event.get("event_type") or "")
    measurements = (
        event.get("measurements")
        if isinstance(event.get("measurements"), Mapping)
        else {}
    )
    if event_type in {"steering_torque_peak", "shock_velocity_peak"}:
        magnitude = _finite(measurements.get("absolute_value"))
        threshold = _finite(measurements.get("threshold"))
        if magnitude is not None and threshold is not None and threshold > 0:
            return magnitude / threshold
    if event_type == "wheel_speed_divergence":
        score = _finite(measurements.get("peak_threshold_score"))
        if score is not None:
            return max(0.0, score)
    if event_type == "brake_onset":
        value = _finite(measurements.get("value"))
        threshold = _finite(measurements.get("threshold"))
        if value is not None and threshold is not None and threshold > 0:
            return max(0.0, value / threshold)
    if event_type == "brake_release":
        value = _finite(measurements.get("value"))
        threshold = _finite(measurements.get("threshold"))
        if value is not None and threshold is not None and threshold > 0:
            return min(1_000_000.0, threshold / max(abs(value), 1e-9))
    # A pit transition is important chronology but has no physical magnitude.
    # Unknown additive event types receive the same neutral deterministic score.
    return 1.0


def _event_record_index(event: Mapping[str, Any]) -> int:
    value = event.get("source_record_index")
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else 0


def _event_sub_tick_index(event: Mapping[str, Any]) -> int:
    sub_tick = event.get("sub_tick")
    value = sub_tick.get("index") if isinstance(sub_tick, Mapping) else None
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else 0


def _event_channel(event: Mapping[str, Any]) -> str:
    measurements = event.get("measurements")
    return (
        str(measurements.get("channel") or "")
        if isinstance(measurements, Mapping)
        else ""
    )


def _strongest_sort_key(
    event: Mapping[str, Any], type_order: Mapping[str, int]
) -> tuple[Any, ...]:
    """Sort strongest first with stable provenance-based tie breakers."""

    return (
        -_event_severity_score(event),
        int(type_order.get(str(event.get("event_type") or ""), len(type_order))),
        str(event.get("source_path") or ""),
        _event_record_index(event),
        _event_sub_tick_index(event),
        _event_channel(event),
    )


def select_native_events_by_severity(
    events: Iterable[Mapping[str, Any]],
    event_types: Sequence[str],
    max_events: int,
) -> list[dict[str, Any]]:
    """Select a balanced deterministic strongest subset from bounded candidates.

    When capacity permits, every event type with a match receives the same base
    quota. Remaining slots go to the strongest leftover candidates. If capacity
    is smaller than the number of matched types, only each type's strongest
    candidate participates, preventing one noisy detector from taking every slot.
    """

    limit = _normalise_max_events(max_events)
    requested = tuple(dict.fromkeys(str(item) for item in event_types))
    type_order = {name: index for index, name in enumerate(requested)}
    buckets: dict[str, list[dict[str, Any]]] = {name: [] for name in requested}
    for raw_event in events:
        event = dict(raw_event)
        event_type = str(event.get("event_type") or "")
        if event_type in buckets:
            buckets[event_type].append(event)
    for event_type in requested:
        buckets[event_type].sort(key=lambda item: _strongest_sort_key(item, type_order))

    matched = [name for name in requested if buckets[name]]
    if not matched:
        return []
    if limit < len(matched):
        representatives = [buckets[name][0] for name in matched]
        return sorted(
            representatives, key=lambda item: _strongest_sort_key(item, type_order)
        )[:limit]

    base_quota = limit // len(matched)
    selected: list[dict[str, Any]] = []
    for event_type in matched:
        for event in buckets[event_type][:base_quota]:
            selected.append(event)

    remaining = [
        event
        for event_type in matched
        for event in buckets[event_type][base_quota:]
    ]
    remaining.sort(key=lambda item: _strongest_sort_key(item, type_order))
    selected.extend(remaining[: max(0, limit - len(selected))])
    return sorted(selected, key=lambda item: _strongest_sort_key(item, type_order))


class _BoundedSeveritySelector:
    """Keep at most ``event_type_count * max_events`` candidate objects."""

    def __init__(self, event_types: Sequence[str], max_events: int) -> None:
        self.event_types = tuple(dict.fromkeys(event_types))
        self.max_events = max_events
        self.type_order = {name: index for index, name in enumerate(self.event_types)}
        self.heaps: dict[str, list[tuple[float, int, int, int, dict[str, Any]]]] = {
            name: [] for name in self.event_types
        }
        self.counts: Counter[str] = Counter()
        self.serial = 0

    def add(self, item: Mapping[str, Any]) -> None:
        event = dict(item)
        event_type = str(event.get("event_type") or "")
        if event_type not in self.heaps:
            return
        self.counts[event_type] += 1
        self.serial += 1
        rank = (
            _event_severity_score(event),
            -_event_record_index(event),
            -_event_sub_tick_index(event),
            -self.serial,
            event,
        )
        heap = self.heaps[event_type]
        if len(heap) < self.max_events:
            heapq.heappush(heap, rank)
        elif rank[:4] > heap[0][:4]:
            heapq.heapreplace(heap, rank)

    @property
    def candidate_count(self) -> int:
        return sum(self.counts.values())

    def selected(self) -> list[dict[str, Any]]:
        candidates = [entry[4] for heap in self.heaps.values() for entry in heap]
        return select_native_events_by_severity(
            candidates, self.event_types, self.max_events
        )


def _normalise_context_filters(
    *,
    lap: int | None,
    session_time_start: float | None,
    session_time_end: float | None,
    lap_distance_start: float | None,
    lap_distance_end: float | None,
) -> dict[str, Any]:
    if lap is not None and (
        isinstance(lap, bool) or not isinstance(lap, int) or lap < 0
    ):
        raise ValueError("lap must be a non-negative integer or None")

    def optional_finite(value: Any, label: str) -> float | None:
        if value is None:
            return None
        if isinstance(value, bool):
            raise ValueError(f"{label} must be a finite number or None")
        number = _finite(value)
        if number is None:
            raise ValueError(f"{label} must be a finite number or None")
        return number

    time_start = optional_finite(session_time_start, "session_time_start")
    time_end = optional_finite(session_time_end, "session_time_end")
    if time_start is not None and time_start < 0:
        raise ValueError("session_time_start must be non-negative")
    if time_end is not None and time_end < 0:
        raise ValueError("session_time_end must be non-negative")
    if time_start is not None and time_end is not None and time_end <= time_start:
        raise ValueError("session_time_end must be greater than session_time_start")

    distance_start = optional_finite(lap_distance_start, "lap_distance_start")
    distance_end = optional_finite(lap_distance_end, "lap_distance_end")
    for label, value in (
        ("lap_distance_start", distance_start),
        ("lap_distance_end", distance_end),
    ):
        if value is not None and not 0.0 <= value <= 1.0:
            raise ValueError(f"{label} must be between 0 and 1")
    return {
        "lap": lap,
        "session_time_start": time_start,
        "session_time_end": time_end,
        "lap_distance_start": distance_start,
        "lap_distance_end": distance_end,
    }


def _context_matches(context: Mapping[str, Any], filters: Mapping[str, Any]) -> bool:
    lap = filters.get("lap")
    if lap is not None and context.get("lap") != lap:
        return False
    session_time = _finite(context.get("session_time_s"))
    time_start = filters.get("session_time_start")
    time_end = filters.get("session_time_end")
    if time_start is not None and (session_time is None or session_time < time_start):
        return False
    if time_end is not None and (session_time is None or session_time >= time_end):
        return False
    distance = _finite(context.get("lap_distance_fraction"))
    distance_start = filters.get("lap_distance_start")
    distance_end = filters.get("lap_distance_end")
    if distance_start is None and distance_end is None:
        return True
    if distance is None:
        return False
    start = 0.0 if distance_start is None else float(distance_start)
    end = 1.0 if distance_end is None else float(distance_end)
    if start <= end:
        return start <= distance <= end
    return distance >= start or distance <= end


def _assert_structurally_finalized(path: Path, metadata: Mapping[str, Any]) -> None:
    record_count = metadata.get("record_count")
    buffer_offset = metadata.get("buffer_offset")
    header = metadata.get("header")
    buffer_length = header.get("buffer_length") if isinstance(header, Mapping) else None
    if not isinstance(record_count, int) or record_count <= 0:
        raise NativeEventError(f"IBT has no finalized telemetry records: {path}")
    if not isinstance(buffer_offset, int) or not isinstance(buffer_length, int):
        raise NativeEventError(f"IBT layout is missing finalized extent data: {path}")
    declared_extent = buffer_offset + record_count * buffer_length
    file_size = metadata.get("file_size")
    if declared_extent != file_size:
        raise NativeEventError(
            f"IBT declared telemetry extent {declared_extent} does not equal "
            f"file size {file_size}; recording may still be active: {path}"
        )


def _context(
    samples: Mapping[str, Sequence[Any]],
    row: int,
    record_index: int,
) -> dict[str, Any]:
    session_time = (
        _json_number(samples["SessionTime"][row])
        if "SessionTime" in samples
        else None
    )
    lap_value = (
        _json_number(samples["Lap"][row]) if "Lap" in samples else None
    )
    lap = int(lap_value) if lap_value is not None else None
    lap_fraction = (
        _finite(samples["LapDistPct"][row])
        if "LapDistPct" in samples
        else None
    )
    return {
        "source_record_index": int(record_index),
        "session_time_s": session_time,
        "lap": lap,
        "lap_distance_fraction": lap_fraction,
        "lap_distance_pct": lap_fraction * 100.0 if lap_fraction is not None else None,
    }


def _event(
    event_type: str,
    evidence_label: str,
    context: Mapping[str, Any],
    *,
    measured_channels: Sequence[str],
    method: str,
    measurements: Mapping[str, Any],
    limitation: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    evidence = {
        "label": evidence_label,
        "measured_channels": list(dict.fromkeys(measured_channels)),
        "method": method,
    }
    if limitation:
        evidence["limitation"] = limitation
    result = {
        "event_type": event_type,
        **dict(context),
        "evidence": evidence,
        "measurements": dict(measurements),
    }
    if extra:
        result.update(extra)
    return result


def _new_running_stats() -> dict[str, float | int]:
    return {"count": 0, "mean": 0.0, "m2": 0.0}


def _update_stats(state: dict[str, float | int], value: float) -> None:
    count = int(state["count"]) + 1
    delta = value - float(state["mean"])
    mean = float(state["mean"]) + delta / count
    state["count"] = count
    state["mean"] = mean
    state["m2"] = float(state["m2"]) + delta * (value - mean)


def _stats_std(state: Mapping[str, float | int]) -> float:
    count = int(state["count"])
    if count < 2:
        return 0.0
    return math.sqrt(max(0.0, float(state["m2"]) / (count - 1)))


def _peak_threshold(
    state: Mapping[str, Any],
    *,
    floor: float,
) -> float:
    stats = state["stats"]
    if int(stats["count"]) < 8:
        return math.inf
    return max(
        floor,
        float(stats["mean"])
        + max(4.0 * _stats_std(stats), floor * 0.25),
    )


def _peak_position(
    context: Mapping[str, Any],
    *,
    sub_tick_index: int | None,
    sub_tick_count: int,
    native_rate: float,
    count_as_time: bool,
) -> dict[str, Any]:
    position = dict(context)
    if sub_tick_index is None or sub_tick_count <= 1 or not count_as_time:
        return position
    effective_rate = native_rate * sub_tick_count
    offset = sub_tick_index / effective_rate
    host_time = _finite(context.get("session_time_s"))
    position["sub_tick"] = {
        "index": sub_tick_index,
        "count": sub_tick_count,
        "effective_sample_rate_hz": effective_rate,
        "offset_from_record_s": offset,
        "derived_session_time_s": host_time + offset if host_time is not None else None,
    }
    return position


def _process_peak_value(
    state: dict[str, Any],
    *,
    value: Any,
    context: Mapping[str, Any],
    sub_tick_index: int | None,
    sub_tick_count: int,
    native_rate: float,
    count_as_time: bool,
    floor: float,
) -> dict[str, Any] | None:
    number = _finite(value)
    if number is None:
        return None
    magnitude = abs(number)
    threshold = _peak_threshold(state, floor=floor)
    active = state.get("active")
    if magnitude >= threshold:
        position = _peak_position(
            context,
            sub_tick_index=sub_tick_index,
            sub_tick_count=sub_tick_count,
            native_rate=native_rate,
            count_as_time=count_as_time,
        )
        candidate = {
            "value": number,
            "magnitude": magnitude,
            "threshold": threshold,
            "position": position,
        }
        if active is None or magnitude > float(active["magnitude"]):
            state["active"] = candidate
        return None
    _update_stats(state["stats"], magnitude)
    if active is not None and magnitude <= max(floor * 0.65, float(active["threshold"]) * 0.65):
        state["active"] = None
        return active
    return None


def _flush_peak(state: Mapping[str, Any]) -> dict[str, Any] | None:
    active = state.get("active")
    return dict(active) if isinstance(active, Mapping) else None


def _new_transition_state() -> dict[str, Any]:
    return {"initialized": False, "value": None, "candidate": None, "count": 0}


def _confirmed_bool_transition(
    state: dict[str, Any],
    value: bool,
    context: Mapping[str, Any],
) -> tuple[bool, bool, Mapping[str, Any]] | None:
    if not state["initialized"]:
        state.update({"initialized": True, "value": value})
        return None
    if value == state["value"]:
        state.update({"candidate": None, "count": 0})
        return None
    if state["candidate"] != value:
        state.update({"candidate": value, "count": 1, "context": dict(context)})
        return None
    state["count"] += 1
    if state["count"] < 2:
        return None
    previous = bool(state["value"])
    first_context = dict(state["context"])
    state.update({"value": value, "candidate": None, "count": 0})
    return previous, value, first_context


def _new_brake_state() -> dict[str, Any]:
    return {
        "initialized": False,
        "engaged": False,
        "candidate": None,
        "count": 0,
    }


def _confirmed_brake_transition(
    state: dict[str, Any],
    value: float,
    context: Mapping[str, Any],
) -> tuple[str, Mapping[str, Any], float] | None:
    if not state["initialized"]:
        state.update({"initialized": True, "engaged": value >= _BRAKE_ON_THRESHOLD})
        return None
    target: str | None = None
    if not state["engaged"] and value >= _BRAKE_ON_THRESHOLD:
        target = "brake_onset"
    elif state["engaged"] and value <= _BRAKE_OFF_THRESHOLD:
        target = "brake_release"
    if target is None:
        state.update({"candidate": None, "count": 0})
        return None
    if state["candidate"] != target:
        state.update(
            {
                "candidate": target,
                "count": 1,
                "context": dict(context),
                "candidate_value": value,
            }
        )
        return None
    state["count"] += 1
    if state["count"] < 2:
        return None
    first_context = dict(state["context"])
    first_value = float(state["candidate_value"])
    state.update(
        {
            "engaged": target == "brake_onset",
            "candidate": None,
            "count": 0,
        }
    )
    return target, first_context, first_value


def _wheel_bin(lap_fraction: float) -> int:
    wrapped = lap_fraction % 1.0
    return min(_WHEEL_DISTANCE_BINS - 1, int(wrapped * _WHEEL_DISTANCE_BINS))


def _new_wheel_state() -> dict[str, Any]:
    return {
        "lap": None,
        "lap_bins": {},
        "baseline": {},
        "baseline_laps": set(),
        "active": None,
    }


def _commit_wheel_lap(state: dict[str, Any]) -> None:
    lap = state.get("lap")
    bins = state["lap_bins"]
    if lap is None or not bins:
        state["lap_bins"] = {}
        return
    contributed = False
    for bin_index, by_wheel in bins.items():
        for wheel, sample_stats in by_wheel.items():
            if int(sample_stats["count"]) <= 0:
                continue
            target = state["baseline"].setdefault(
                (bin_index, wheel), _new_running_stats()
            )
            _update_stats(target, float(sample_stats["mean"]))
            contributed = True
    if contributed:
        state["baseline_laps"].add(int(lap))
    state["lap_bins"] = {}


def _wheel_observation(
    state: dict[str, Any],
    *,
    context: Mapping[str, Any],
    brake: float | None,
    pit: bool,
    speed: float | None,
    wheel_values: Mapping[str, float],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Return ``(completed_peak, immediate_peak_at_eof_candidate)``."""

    lap = context.get("lap")
    lap_fraction = _finite(context.get("lap_distance_fraction"))
    if (
        lap is None
        or lap_fraction is None
        or speed is None
        or speed < _WHEEL_MIN_SPEED_MPS
        or len(wheel_values) < 2
    ):
        active = state.get("active")
        state["active"] = None
        return active, None
    if state["lap"] is None:
        state["lap"] = int(lap)
    elif int(lap) != int(state["lap"]):
        previous_lap = int(state["lap"])
        _commit_wheel_lap(state)
        # A simulator/session reset must not mix unrelated location baselines.
        if int(lap) < previous_lap:
            state["baseline"] = {}
            state["baseline_laps"] = set()
        state["lap"] = int(lap)

    bin_index = _wheel_bin(lap_fraction)
    ratios = {wheel: value / speed for wheel, value in wheel_values.items()}
    deviations: dict[str, Any] = {}
    exceeds = False
    max_score = 0.0
    for wheel, ratio in ratios.items():
        baseline = state["baseline"].get((bin_index, wheel))
        if baseline is None or int(baseline["count"]) < _WHEEL_MIN_BASELINE_LAPS:
            continue
        mean = float(baseline["mean"])
        std = _stats_std(baseline)
        threshold = max(_WHEEL_MIN_RATIO_DELTA, _WHEEL_SIGMA_MULTIPLIER * std)
        delta = ratio - mean
        score = abs(delta) / threshold if threshold > 0 else 0.0
        deviations[wheel] = {
            "ratio_vs_vehicle_speed": ratio,
            "clean_baseline_mean": mean,
            "clean_baseline_std": std,
            "delta": delta,
            "threshold": threshold,
            "baseline_lap_count": int(baseline["count"]),
        }
        if abs(delta) >= threshold:
            exceeds = True
            max_score = max(max_score, score)

    completed = None
    if exceeds:
        candidate = {
            "score": max_score,
            "position": dict(context),
            "deviations": deviations,
            "speed_mps": speed,
            "brake": brake,
            "pit": pit,
            "distance_bin": bin_index,
        }
        active = state.get("active")
        if active is None or max_score > float(active["score"]):
            state["active"] = candidate
    elif state.get("active") is not None:
        completed = state["active"]
        state["active"] = None

    clean_unbraked = brake is not None and brake <= _BRAKE_OFF_THRESHOLD and not pit
    if clean_unbraked and not exceeds:
        by_wheel = state["lap_bins"].setdefault(bin_index, {})
        for wheel, ratio in ratios.items():
            sample_stats = by_wheel.setdefault(wheel, _new_running_stats())
            _update_stats(sample_stats, ratio)
    return completed, state.get("active")


def _coverage_entry(
    requested: bool,
    channels: Sequence[str],
    *,
    available: bool,
    reason: str | None = None,
) -> dict[str, Any]:
    status = "not_requested"
    if requested:
        status = "enabled" if available else "unavailable"
    result = {"status": status, "channels_used": list(channels)}
    if reason and requested and not available:
        result["reason"] = reason
    return result


def detect_native_telemetry_events(
    path: os.PathLike[str] | str,
    event_types: Iterable[str] | str | None = None,
    *,
    selection_mode: str = "chronological",
    start_record: int = 0,
    end_record: int | None = None,
    max_events: int = 200,
    lap: int | None = None,
    session_time_start: float | None = None,
    session_time_end: float | None = None,
    lap_distance_start: float | None = None,
    lap_distance_end: float | None = None,
) -> dict[str, Any]:
    """Detect compact native-rate events in one finalized IBT recording.

    ``start_record`` is inclusive and ``end_record`` exclusive.  Chronological
    selection preserves the first matching events and may stop scanning at the
    cap. Severity selection scans the complete requested source window and
    retains a balanced strongest subset in bounded memory. Missing optional
    channels disable only the affected detector and are reported in ``coverage``.
    """

    requested = _normalise_event_types(event_types)
    limit = _normalise_max_events(max_events)
    normalized_selection_mode = _normalise_selection_mode(selection_mode)
    context_filters = _normalise_context_filters(
        lap=lap,
        session_time_start=session_time_start,
        session_time_end=session_time_end,
        lap_distance_start=lap_distance_start,
        lap_distance_end=lap_distance_end,
    )
    source = Path(path).expanduser().resolve(strict=True)
    if not source.is_file() or source.suffix.lower() != ".ibt":
        raise NativeEventError(f"native event source must be one .ibt file: {source}")

    before = source.stat()
    metadata = scan_ibt(source)
    _assert_structurally_finalized(source, metadata)
    record_count = int(metadata["record_count"])
    if isinstance(start_record, bool) or not isinstance(start_record, int):
        raise ValueError("start_record must be an integer")
    if start_record < 0 or start_record > record_count:
        raise ValueError(f"start_record must be between 0 and {record_count}")
    normalized_end = record_count if end_record is None else end_record
    if isinstance(normalized_end, bool) or not isinstance(normalized_end, int):
        raise ValueError("end_record must be an integer or None")
    if normalized_end < start_record or normalized_end > record_count:
        raise ValueError(f"end_record must be between start_record and {record_count}")

    variables = {
        item["name"]: item
        for item in metadata.get("variables", [])
        if isinstance(item, Mapping) and isinstance(item.get("name"), str)
    }
    available = set(variables)
    brake_channel = _first_available(available, _BRAKE_CHANNELS)
    pit_channel = _first_available(available, _PIT_CHANNELS)
    torque_channel = _first_available(available, _TORQUE_CHANNELS)
    shock_channels = sorted(name for name in available if _SHOCK_PATTERN.match(name))
    wheel_channels = [name for name in _WHEEL_CHANNELS if name in available]

    wants_brake = bool({"brake_onset", "brake_release"} & set(requested))
    wants_pit = "pit_transition" in requested
    wants_torque = "steering_torque_peak" in requested
    wants_shock = "shock_velocity_peak" in requested
    wants_wheel = "wheel_speed_divergence" in requested
    wheel_ready = bool(
        brake_channel
        and "Lap" in available
        and "LapDistPct" in available
        and "Speed" in available
        and len(wheel_channels) >= 2
    )

    selected: list[str] = [name for name in _CONTEXT_CHANNELS if name in available]
    if (wants_brake or wants_wheel) and brake_channel:
        selected.append(brake_channel)
    if (wants_pit or wants_wheel) and pit_channel:
        selected.append(pit_channel)
    if wants_torque and torque_channel:
        selected.append(torque_channel)
    if wants_shock:
        selected.extend(shock_channels)
    if wants_wheel:
        selected.extend(name for name in ("Speed", "Throttle") if name in available)
        selected.extend(wheel_channels)
    selected = list(dict.fromkeys(selected))

    coverage = {
        "brake_onset": _coverage_entry(
            "brake_onset" in requested,
            [brake_channel] if brake_channel else [],
            available=brake_channel is not None,
            reason="Brake or BrakeRaw was not recorded",
        ),
        "brake_release": _coverage_entry(
            "brake_release" in requested,
            [brake_channel] if brake_channel else [],
            available=brake_channel is not None,
            reason="Brake or BrakeRaw was not recorded",
        ),
        "pit_transition": _coverage_entry(
            wants_pit,
            [pit_channel] if pit_channel else [],
            available=pit_channel is not None,
            reason="no supported pit-state channel was recorded",
        ),
        "steering_torque_peak": _coverage_entry(
            wants_torque,
            [torque_channel] if torque_channel else [],
            available=torque_channel is not None,
            reason="SteeringWheelTorque_ST or SteeringWheelTorque was not recorded",
        ),
        "shock_velocity_peak": _coverage_entry(
            wants_shock,
            shock_channels,
            available=bool(shock_channels),
            reason="no per-corner shock/damper velocity channel was recorded",
        ),
        "wheel_speed_divergence": _coverage_entry(
            wants_wheel,
            [
                *([brake_channel] if brake_channel else []),
                *(["Lap", "LapDistPct", "Speed"] if wheel_ready else []),
                *wheel_channels,
            ],
            available=wheel_ready,
            reason=(
                "requires Brake/BrakeRaw, Lap, LapDistPct, Speed, and at least "
                "two wheel-speed channels to build clean unbraked "
                "same-lap-distance baselines"
            ),
        ),
    }

    events: list[dict[str, Any]] = []
    severity_selector = (
        _BoundedSeveritySelector(requested, limit)
        if normalized_selection_mode == "severity"
        else None
    )
    chronological_candidate_counts: Counter[str] = Counter()
    truncated = False
    scanned_records = 0
    native_rate = float(metadata["header"]["tick_rate"])
    brake_state = _new_brake_state()
    pit_state = _new_transition_state()
    peak_states: dict[str, dict[str, Any]] = {}
    peak_event_slots: dict[str, tuple[int, int]] = {}
    severity_pending_peaks: dict[str, dict[str, Any]] = {}
    wheel_state = _new_wheel_state()

    def append_event(item: dict[str, Any]) -> bool:
        nonlocal truncated
        if not _context_matches(item, context_filters):
            return True
        if severity_selector is not None:
            severity_selector.add(item)
            return True
        chronological_candidate_counts[str(item.get("event_type") or "")] += 1
        if len(events) >= limit:
            truncated = True
            return False
        events.append(item)
        return True

    def emit_peak(channel: str, peak: Mapping[str, Any], event_type: str) -> bool:
        variable = variables[channel]
        position = peak["position"]
        is_torque = event_type == "steering_torque_peak"
        item = _event(
            event_type,
            "derived",
            position,
            measured_channels=[channel],
            method=(
                "debounced absolute steering-torque peak above a bounded "
                "adaptive threshold"
                if is_torque
                else "debounced absolute shock/damper-velocity peak above "
                "a bounded adaptive threshold"
            ),
            measurements={
                "channel": channel,
                "value": _json_number(peak["value"]),
                "absolute_value": _json_number(peak["magnitude"]),
                "threshold": _json_number(peak["threshold"]),
                "unit": variable.get("unit") or None,
            },
        )
        if not _context_matches(item, context_filters):
            return True
        record_index = int(position["source_record_index"])
        debounce_records = max(1, int(math.ceil(native_rate * 0.20)))
        if severity_selector is not None:
            previous_pending = severity_pending_peaks.get(channel)
            if previous_pending is not None and (
                record_index - int(previous_pending["last_record_index"])
                <= debounce_records
            ):
                previous_item = previous_pending["item"]
                prior_magnitude = _finite(
                    previous_item["measurements"]["absolute_value"]
                )
                if prior_magnitude is None or float(peak["magnitude"]) > prior_magnitude:
                    previous_pending["item"] = item
                previous_pending["last_record_index"] = record_index
                return True
            if previous_pending is not None:
                append_event(previous_pending["item"])
            severity_pending_peaks[channel] = {
                "item": item,
                "last_record_index": record_index,
            }
            return True

        previous = peak_event_slots.get(channel)
        if previous is not None and record_index - previous[1] <= debounce_records:
            slot, _ = previous
            prior_magnitude = _finite(events[slot]["measurements"]["absolute_value"])
            if prior_magnitude is None or float(peak["magnitude"]) > prior_magnitude:
                events[slot] = item
                peak_event_slots[channel] = (slot, record_index)
            return True
        if not append_event(item):
            return False
        peak_event_slots[channel] = (len(events) - 1, record_index)
        return True

    stop = False
    if selected and start_record < normalized_end:
        for chunk in iter_telemetry_chunks(
            source,
            channels=selected,
            target_hz=None,
            chunk_size=_CHUNK_SIZE,
            start_record=start_record,
            end_record=normalized_end,
        ):
            samples = chunk["samples"]
            for row, record_index in enumerate(chunk["sample_indices"]):
                scanned_records += 1
                context = _context(samples, row, record_index)
                brake = (
                    _finite(samples[brake_channel][row])
                    if brake_channel and brake_channel in samples
                    else None
                )
                pit = (
                    bool(samples[pit_channel][row])
                    if pit_channel and pit_channel in samples
                    else False
                )

                if wants_brake and brake_channel and brake is not None:
                    transition = _confirmed_brake_transition(brake_state, brake, context)
                    if transition is not None:
                        kind, first_context, first_value = transition
                        if kind in requested:
                            threshold = (
                                _BRAKE_ON_THRESHOLD
                                if kind == "brake_onset"
                                else _BRAKE_OFF_THRESHOLD
                            )
                            if not append_event(
                                _event(
                                    kind,
                                    "derived",
                                    first_context,
                                    measured_channels=[brake_channel],
                                    method="two-native-record hysteresis transition",
                                    measurements={
                                        "channel": brake_channel,
                                        "value": first_value,
                                        "threshold": threshold,
                                    },
                                )
                            ):
                                stop = True
                                break

                if wants_pit and pit_channel:
                    transition = _confirmed_bool_transition(pit_state, pit, context)
                    if transition is not None:
                        previous, current, first_context = transition
                        if not append_event(
                            _event(
                                "pit_transition",
                                "derived",
                                first_context,
                                measured_channels=[pit_channel],
                                method="two-native-record pit-state transition",
                                measurements={
                                    "channel": pit_channel,
                                    "from": previous,
                                    "to": current,
                                    "direction": "entry" if current else "exit",
                                },
                            )
                        ):
                            stop = True
                            break

                peak_channels: list[tuple[str, str, float]] = []
                if wants_torque and torque_channel:
                    peak_channels.append((torque_channel, "steering_torque_peak", 6.0))
                if wants_shock:
                    peak_channels.extend(
                        (channel, "shock_velocity_peak", 0.25)
                        for channel in shock_channels
                    )
                for channel, event_type, floor in peak_channels:
                    variable = variables[channel]
                    raw_value = samples[channel][row]
                    count_as_time = bool(variable.get("count_as_time"))
                    values = (
                        list(raw_value)
                        if isinstance(raw_value, Sequence)
                        and not isinstance(raw_value, (str, bytes, bytearray))
                        else [raw_value]
                    )
                    state = peak_states.setdefault(
                        channel, {"stats": _new_running_stats(), "active": None}
                    )
                    for sub_index, value in enumerate(values):
                        completed = _process_peak_value(
                            state,
                            value=value,
                            context=context,
                            sub_tick_index=sub_index if len(values) > 1 else None,
                            sub_tick_count=len(values),
                            native_rate=native_rate,
                            count_as_time=count_as_time,
                            floor=floor,
                        )
                        if completed is not None and not emit_peak(channel, completed, event_type):
                            stop = True
                            break
                    if stop:
                        break
                if stop:
                    break

                if wants_wheel and wheel_ready:
                    speed = _finite(samples["Speed"][row])
                    wheels = {
                        name[:2]: number
                        for name in wheel_channels
                        if (number := _finite(samples[name][row])) is not None
                    }
                    completed, _ = _wheel_observation(
                        wheel_state,
                        context=context,
                        brake=brake,
                        pit=pit,
                        speed=speed,
                        wheel_values=wheels,
                    )
                    if completed is not None:
                        wheel_measured_channels = [
                            "Speed",
                            brake_channel,
                            *wheel_channels,
                            "Lap",
                            "LapDistPct",
                        ]
                        if pit_channel:
                            wheel_measured_channels.append(pit_channel)
                        if not append_event(
                            _event(
                                "wheel_speed_divergence",
                                "proxy",
                                completed["position"],
                                measured_channels=[
                                    channel for channel in wheel_measured_channels if channel
                                ],
                                method=(
                                    "wheel/vehicle-speed ratio deviation from prior "
                                    "clean unbraked laps in the same 1/120-lap-distance bin"
                                ),
                                measurements={
                                    "vehicle_speed_mps": completed["speed_mps"],
                                    "brake": completed["brake"],
                                    "on_pit_state": completed["pit"],
                                    "lap_distance_bin": completed["distance_bin"],
                                    "peak_threshold_score": completed["score"],
                                    "per_wheel": completed["deviations"],
                                },
                                limitation=(
                                    "This calibrated divergence is diagnostic context only; "
                                    "it is not causal proof of wheel lock or wheelspin."
                                ),
                            )
                        ):
                            stop = True
                            break
            if stop:
                break

    if not stop:
        for channel, state in peak_states.items():
            completed = _flush_peak(state)
            if completed is None:
                continue
            event_type = (
                "steering_torque_peak"
                if channel == torque_channel
                else "shock_velocity_peak"
            )
            if not emit_peak(channel, completed, event_type):
                stop = True
                break
    if severity_selector is not None:
        for pending in severity_pending_peaks.values():
            append_event(pending["item"])
    if not stop and wants_wheel and wheel_state.get("active") is not None:
        completed = wheel_state["active"]
        append_event(
            _event(
                "wheel_speed_divergence",
                "proxy",
                completed["position"],
                measured_channels=[
                    channel
                    for channel in (
                        "Speed",
                        brake_channel,
                        *wheel_channels,
                        "Lap",
                        "LapDistPct",
                        pit_channel,
                    )
                    if channel
                ],
                method=(
                    "wheel/vehicle-speed ratio deviation from prior clean unbraked "
                    "laps in the same 1/120-lap-distance bin"
                ),
                measurements={
                    "vehicle_speed_mps": completed["speed_mps"],
                    "brake": completed["brake"],
                    "on_pit_state": completed["pit"],
                    "lap_distance_bin": completed["distance_bin"],
                    "peak_threshold_score": completed["score"],
                    "per_wheel": completed["deviations"],
                },
                limitation=(
                    "This calibrated divergence is diagnostic context only; it is "
                    "not causal proof of wheel lock or wheelspin."
                ),
            )
        )

    after = source.stat()
    if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
        raise NativeEventError(
            f"IBT changed while native events were being read; no result is valid: {source}"
        )

    if severity_selector is not None:
        events = severity_selector.selected()
        candidate_counts = Counter(severity_selector.counts)
        candidate_count = severity_selector.candidate_count
        truncated = candidate_count > len(events)
    else:
        candidate_counts = Counter(chronological_candidate_counts)
        candidate_count = sum(candidate_counts.values())
    counts = Counter(item["event_type"] for item in events)
    requested_record_count = normalized_end - start_record
    scan_complete = (
        not selected
        or requested_record_count == 0
        or scanned_records == requested_record_count
    )
    return {
        "schema_version": 1,
        "source": {
            "path": str(source),
            "file_size": before.st_size,
            "mtime_ns": before.st_mtime_ns,
            "record_count": record_count,
            "native_tick_rate_hz": native_rate,
            "read_only": True,
            "structurally_finalized": True,
        },
        "query": {
            "event_types": list(requested),
            "selection_mode": normalized_selection_mode,
            "start_record": start_record,
            "end_record": normalized_end,
            "max_events": limit,
            "context_filters": context_filters,
        },
        "coverage": coverage,
        "events": events,
        "summary": {
            "scanned_record_count": scanned_records,
            "requested_record_count": requested_record_count,
            "scan_complete": scan_complete,
            "candidate_event_count": candidate_count,
            "candidate_event_count_complete": scan_complete,
            "returned_event_count": len(events),
            "counts_by_type": {
                event_type: counts.get(event_type, 0) for event_type in requested
            },
            "candidate_counts_by_type": {
                event_type: candidate_counts.get(event_type, 0)
                for event_type in requested
            },
            "omitted_event_count": (
                max(0, candidate_count - len(events)) if scan_complete else None
            ),
            "selection_mode": normalized_selection_mode,
            "returned_order": (
                "strongest-first with deterministic provenance tie-breaks"
                if normalized_selection_mode == "severity"
                else "chronological detector order"
            ),
            "truncated": truncated,
            "wheel_baseline_completed_laps": len(wheel_state["baseline_laps"]),
        },
        "evidence_legend": {
            "measured": "direct SDK channel value",
            "derived": "event located by a stated rule over measured SDK values",
            "proxy": (
                "calibrated diagnostic indicator that does not establish a causal "
                "vehicle or tire state"
            ),
        },
    }

"""Recorded field comparison. Never infer another car's fuel or tire service."""
from __future__ import annotations

import math
from statistics import median
from typing import Any, Mapping, Sequence

CAUTION_FLAGS = 0x0008 | 0x0100 | 0x0200 | 0x4000 | 0x8000


def number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return value if math.isfinite(value) else None


def integer(value: Any) -> int | None:
    value = number(value)
    return int(value) if value is not None and value.is_integer() else None


def build_field_results(channels: Mapping[str, Sequence[Any]], session_info: Mapping[str, Any],
                        metadata: Mapping[str, Any], player_laps: Sequence[Mapping[str, Any]],
                        runs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """One row per participant and observed per-lap progression, scoped to one phase.

    Pit visits count observed off->on pit-road transitions during racing, not
    service stops. A recording that starts in the pits cannot establish entry.
    Lap timing uses observed consecutive counter crossings, never a possibly
    delayed last-lap scoreboard value. Gaps/reset counters break coverage.
    """
    info = session_info.get("DriverInfo") or {}
    player = integer(info.get("DriverCarIdx"))
    sessions = (session_info.get("SessionInfo") or {}).get("Sessions") or []
    phase = integer(metadata.get("sim_session_num"))
    session_nums = channels.get("SessionNum", ())
    if phase is None:
        phase = next((integer(v) for v in reversed(session_nums) if integer(v) is not None), None)
    session = next((s for s in sessions if isinstance(s, Mapping) and integer(s.get("SessionNum")) == phase), {})
    phase_type = str(metadata.get("sim_session_type") or session.get("SessionType") or "").strip()
    is_race = phase_type.casefold() == "race"
    snapshot = {integer(r.get("CarIdx")): r for r in (session.get("ResultsPositions") or ()) if isinstance(r, Mapping)}
    snapshot_positions = {car: integer(row.get("Position")) for car, row in snapshot.items()
                          if car is not None and integer(row.get("Position")) is not None}
    # SDK ResultsPositions is normally one-based. Only a snapshot explicitly
    # containing zero establishes the legacy zero-based representation.
    snapshot_offset = 1 if min(snapshot_positions.values(), default=1) == 0 else 0
    times = channels.get("SessionTime", ())
    states = channels.get("SessionState", ())
    flags = channels.get("SessionFlags", ())

    def scalar(series: Sequence[Any], index: int) -> Any:
        return series[index] if index < len(series) else None

    def array(name: str, index: int, car: int) -> Any:
        row = scalar(channels.get(name, ()), index)
        return row[car] if isinstance(row, (list, tuple)) and car < len(row) else None

    indices = [i for i in range(len(times))
               if (phase is None or not session_nums or integer(scalar(session_nums, i)) == phase)
               and (not is_race or integer(scalar(states, i)) in (4, 5))]
    scoped = set(indices)

    def span(item: Mapping[str, Any]) -> list[int]:
        start, end = integer(item.get("start_index")), integer(item.get("end_index"))
        if start is None and end is None:
            return indices if len(indices) == len(times) and times else []
        if start is None or end is None or not 0 <= start <= end < len(times):
            return []
        return list(range(start, end + 1)) if all(i in scoped for i in range(start, end + 1)) else []

    scoped_player_laps = [lap for lap in player_laps if span(lap)]

    def known_green_no_pit(lap: Mapping[str, Any]) -> bool:
        samples = span(lap)
        return bool(samples) and all(
            integer(scalar(flags, i)) is not None
            and not integer(scalar(flags, i)) & CAUTION_FLAGS
            and scalar(channels.get("OnPitRoad", ()), i) in (False, 0)
            for i in samples
        ) and all(number(scalar(times, i)) is not None for i in samples) and all(
            0 < number(scalar(times, later)) - number(scalar(times, earlier)) <= 2
            for earlier, later in zip(samples, samples[1:])
        )

    rows = []
    seen = set()
    for driver in info.get("Drivers") or ():
        if not isinstance(driver, Mapping):
            continue
        car = integer(driver.get("CarIdx"))
        if car is None or car < 0 or car in seen or driver.get("IsSpectator") in (True, 1, "1") or driver.get("CarIsPaceCar") in (True, 1, "1"):
            continue
        seen.add(car)
        laps: list[dict[str, Any]] = []
        pits: list[dict[str, Any]] = []
        previous_count = None
        previous_pit = None
        previous_time = None
        previous_index = None
        boundary_time = None
        boundary_source = None
        lap_caution = False
        lap_pit = False
        lap_flags_known = True
        lap_pit_known = True
        position = None
        completed = None
        pit_samples = 0
        for i in indices:
            time = number(scalar(times, i))
            count = integer(array("CarIdxLapCompleted", i, car))
            pos = integer(array("CarIdxPosition", i, car))
            pit_raw = array("CarIdxOnPitRoad", i, car)
            if pit_raw is None and car == player:
                pit_raw = scalar(channels.get("OnPitRoad", ()), i)
            pit = bool(pit_raw) if pit_raw in (False, True, 0, 1) else None
            raw_flags = integer(scalar(flags, i))
            gap = (time is None or previous_time is None or time <= previous_time
                   or time - previous_time > 2.0 or previous_index != i - 1)
            if gap:
                previous_count = None
                previous_pit = None
                lap_flags_known = False
                lap_pit_known = False
                boundary_time = None
                boundary_source = None
            if pos is not None and pos > 0:
                position = pos
            if count is not None and count >= 0:
                completed = count
            if pit is not None:
                pit_samples += 1
            if pit is True and previous_pit is False:
                pits.append({"lap": count + 1 if count is not None and count >= 0 else None,
                             "session_time_s": time, "evidence_class": "observed_pit_road_entry"})
            lap_caution = lap_caution or (raw_flags is not None and bool(raw_flags & CAUTION_FLAGS))
            lap_pit = lap_pit or pit is True
            lap_flags_known = lap_flags_known and raw_flags is not None
            lap_pit_known = lap_pit_known and pit is not None
            if count is not None and previous_count is not None and count != previous_count:
                consecutive = count == previous_count + 1 and count > 0
                crossing_time, crossing_source = time, "recorded_lap_counter_intervals"
                if consecutive:
                    x0 = number(array("CarIdxLapDistPct", i - 1, car))
                    x1 = number(array("CarIdxLapDistPct", i, car))
                    if x0 is not None and x1 is not None and 0.97 < x0 <= 1 and 0 <= x1 < 0.03:
                        step = 1 - x0 + x1
                        if 0 < step <= 0.03:
                            crossing_time = previous_time + (1 - x0) / step * (time - previous_time)
                            crossing_source = "recorded_interpolated_distance_crossings"
                lap_time = crossing_time - boundary_time if consecutive and boundary_time is not None else None
                if lap_time is not None and lap_time > 0:
                    eligible = lap_flags_known and lap_pit_known and not lap_caution and not lap_pit
                    laps.append({"lap": count, "lap_time_s": round(lap_time, 4), "position": pos if pos and pos > 0 else None,
                                 "lap_time_source": crossing_source if crossing_source == boundary_source else "recorded_lap_counter_intervals",
                                 "flag_state": "caution" if lap_caution else "green" if lap_flags_known else "unknown",
                                 "on_pit_road": lap_pit, "pace_eligible": eligible})
                boundary_time = crossing_time if consecutive else None
                boundary_source = crossing_source if consecutive else None
                lap_caution = raw_flags is not None and bool(raw_flags & CAUTION_FLAGS)
                lap_pit = pit is True
                lap_flags_known = raw_flags is not None
                lap_pit_known = pit is not None
            if count is None:
                boundary_time = None
                boundary_source = None
            previous_count, previous_pit, previous_time, previous_index = count, pit, time, i
        recorded = snapshot.get(car, {})
        raw_position = snapshot_positions.get(car)
        result_pos = raw_position + snapshot_offset if raw_position is not None and raw_position >= 0 else None
        fastest = number(recorded.get("FastestTime"))
        fastest = fastest if fastest is not None and fastest > 0 else None
        if car == player and scoped_player_laps:
            # The player's IBT channels are commonly present without CarIdx arrays.
            laps = [{"lap": lap["lap"], "lap_time_s": number(lap.get("lap_time_s")),
                     "position": (lap.get("position") or {}).get("end"),
                     "flag_state": lap.get("flag_state") or "unknown",
                     "lap_time_source": lap.get("lap_time_source") or "backend_player_lap_summary",
                     "on_pit_road": (number(lap.get("pit_time_s")) or 0) > 0 or bool(lap.get("pit_entry") or lap.get("pit_exit")),
                     "pace_eligible": known_green_no_pit(lap) and not (lap.get("pit_entry") or lap.get("pit_exit"))}
                    for lap in scoped_player_laps if lap.get("complete") and (number(lap.get("lap_time_s")) or 0) > 0]
            position = next((lap["position"] for lap in reversed(laps) if (number(lap["position"]) or 0) > 0), position)
            complete_summaries = [lap["lap"] for lap in laps]
            if completed is not None:
                complete_summaries.append(completed)
            completed = max(complete_summaries, default=None)
        lap_times = [lap["lap_time_s"] for lap in laps if lap["pace_eligible"]]
        observed_best = min((lap["lap_time_s"] for lap in laps), default=None)
        used = [number((lap.get("fuel") or {}).get("used_l")) for lap in scoped_player_laps if lap.get("complete")]
        used = [value for value in used if value is not None and value >= 0]
        tires = []
        known_services = 0
        if car == player:
            for run in runs:
                service = run.get("pit_service") or {}
                if service.get("post_run_service") or run.get("ended_with_post_run_service"):
                    continue
                if not span(service):
                    continue
                service_start = integer(service.get("start_index"))
                if service_start is not None and service_start > 0 and service_start - 1 not in scoped:
                    continue
                service_end = integer(service.get("end_index"))
                if service_end is not None:
                    end_time = number(scalar(times, service_end))
                    if end_time is None:
                        continue
                    safe_lookahead = True
                    for j in range(service_end + 1, len(times)):
                        later = number(scalar(times, j))
                        if j not in scoped or later is None or later < end_time:
                            safe_lookahead = False
                            break
                        if later > end_time + 5:
                            break
                    if not safe_lookahead:
                        continue
                changed = service.get("tires_changed_observed")
                if isinstance(changed, list) and any(tire in ("LF", "RF", "LR", "RR") for tire in changed):
                    known_services += 1
                    tires.extend(tire for tire in changed if tire in ("LF", "RF", "LR", "RR"))
        rows.append({
            "car_index": car, "driver_name": driver.get("UserName") or driver.get("AbbrevName") or f"Car {car}",
            "car_number": str(driver.get("CarNumber") or ""), "is_player": car == player,
            "position": position or result_pos, "position_source": "last_recorded_position" if position else "session_results_snapshot",
            "laps_completed": completed if completed is not None else integer(recorded.get("LapsComplete")),
            "fastest_lap_s": observed_best or fastest, "fastest_source": next((lap.get("lap_time_source") for lap in laps if lap["lap_time_s"] == observed_best), "session_results_snapshot"),
            "pace_median_s": round(median(lap_times), 4) if len(lap_times) >= 3 else None,
            "pace_lap_count": len(lap_times), "pit_visits": len(pits) if pit_samples else None,
            "tires_taken": tires if car == player and known_services else None,
            "fuel_used_l": round(sum(used), 3) if car == player and used else None,
            "laps": laps, "pit_events": pits,
        })
    rows.sort(key=lambda row: (row["position"] is None, row["position"] or 9999, row["car_index"]))
    return {"schema_version": 1, "player_car_index": player, "order_status": "recorded",
            "cars": rows, "status": "available" if rows else "unavailable",
            "pace_definition": "Median of at least three recorded whole green laps with no pit-road samples; traffic is included.",
            "limitations": ["Order is the recorded results snapshot or last observed position; final classification may be unavailable.",
                            "Pit visits are observed pit-road entries, including drive-throughs; entries before recording are unknown.",
                            "Competitor tire changes and fuel consumption are not recorded.",
                            "Competitor lap times use observed counter transitions, interpolating distance wraps when available; sampled intervals are approximate, not scored lap times."]}

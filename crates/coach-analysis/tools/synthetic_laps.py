"""Unit "laps" synthetic tables for ``emit_kernel_vectors.py`` (phase2-plan.md section 2, U2).

The fixture is one 120-sample lap with eight channels; these tables reach the
lap, service and disruption branches no committed recording does: lap 0,
one-sample and negative laps, scored lap times (accepted, rejected by more
than 1 s, zero), caution and black/white/checkered flags, an Offline Testing
session with one-lap-to-green, pit stops with fuel, tyre counters and odometer
resets, repair timers and fast repairs, wheel-speed slip and lock proxies,
traffic distances (close ahead, behind, both and under the screen; one side
recorded alone), positions, weather, NaN and infinity in float channels,
text channels, a float Lap channel, missing SessionTime, and both steering
bases. Values are exactly what the decoder yields for each SDK type (an f32
channel holds widened f32 values), so ``table.ccs`` reproduces them bit for bit.
"""

from __future__ import annotations

import copy
import math
import random
import struct
from typing import Any

_LAPS_F32 = struct.Struct("<f")
_LAPS_TYPES = {0: ("char", 1), 1: ("bool", 1), 2: ("int", 4), 3: ("bitfield", 4), 4: ("float", 4), 5: ("double", 8)}
NAN, INF = float("nan"), float("inf")


def _sin(x: float) -> float:
    """A sine-like wave from correctly rounded arithmetic only (period 2 pi, peak 1).

    ``math.sin`` and ``math.cos`` are the C library's and differ between Windows and Linux in
    the last bit, which would make the builders' own inputs (and so ``table.ccs``) differ.
    """

    t = (x / 6.283185307179586) % 1.0
    return 16.0 * t * (0.5 - t) if t < 0.5 else -16.0 * (t - 0.5) * (1.0 - t)


def _cos(x: float) -> float:
    return _sin(x + 1.5707963267948966)


def _laps_value(code: int, value: Any) -> Any:
    if code == 4:
        return _LAPS_F32.unpack(_LAPS_F32.pack(value))[0]
    if code == 5:
        return float(value)
    if code == 1:
        return bool(value)
    if code in (2, 3):
        return int(value)
    return str(value)


def _laps_table(
    base: dict[str, Any],
    columns: dict[str, tuple[int, str, list[Any]]],
    *,
    session_info: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Replace the fixture's channels with ``columns`` (name -> type code, unit, values)."""

    import workflow

    lengths = {len(values) for _code, _unit, values in columns.values()}
    if len(lengths) != 1:
        raise ValueError(f"synthetic columns differ in length: {sorted(lengths)}")
    (count,) = lengths
    variables, samples, offset = [], {}, 0
    for name, (code, unit, values) in columns.items():
        kind, size = _LAPS_TYPES[code]
        variables.append({
            "name": name, "description": name, "unit": unit, "type": kind, "type_code": code,
            "offset": offset, "count": 1, "count_as_time": False, "byte_size": size,
        })
        offset += size
        samples[name] = [_laps_value(code, value) for value in values]
    path = base["metadata"].get("path")
    base["samples"] = samples
    base["variables"] = variables
    base["available_variables"] = [dict(variable) for variable in variables]
    base["sample_indices"] = list(range(count))
    base["sample_count"] = count
    base["source_record_count"] = count
    base["channel_selection"] = {"mode": "selected", "available_count": len(variables), "decoded_count": len(variables)}
    base["metadata"].update({"record_count": count, "end_time_unix": base["metadata"]["start_time_unix"] + count / 60.0})
    base["metadata"]["header"].update({"num_variables": len(variables), "buffer_length": offset})
    base["metadata"].update(metadata or {})
    if session_info is not None:
        base["session_info"] = session_info
    catalog = dict(base["source_catalogs"][0])
    catalog.update({
        "record_count": count, "recorded_channel_catalog": [dict(variable) for variable in variables],
        "loaded_channels": sorted(columns), "unloaded_channels": [],
    })
    base["source_catalogs"] = [catalog]
    base["catalog_summary"] = workflow._catalog_summary([catalog])
    base["sample_provenance"] = [
        {"source_file_index": 0, "source_path": path, "native_record_index": index} for index in range(count)
    ]
    return base


def _laps_session_info(base: dict[str, Any], sessions: list[dict[str, Any]], car_setup: dict[str, Any],
                       weekend: dict[str, Any] | None = None) -> dict[str, Any]:
    info = copy.deepcopy(base["session_info"])
    info["SessionInfo"] = {"Sessions": sessions}
    info["CarSetup"] = car_setup
    info["WeekendInfo"].update(weekend or {})
    return info


def _laps_race(base: dict[str, Any]) -> dict[str, Any]:
    """Seven laps of a race with a pit stop, cautions, traffic, slip and repairs; road-wheel steering."""

    rng = random.Random(20261003)
    # (lap number, samples, start pct, end pct): an out-lap 0, five full laps, a partial lap 6.
    plan = [(0, 100, 0.80, 0.999), (1, 250, 0.0, 0.999), (2, 250, 0.0, 0.999), (3, 260, 0.0, 0.999),
            (4, 250, 0.0, 0.999), (5, 250, 0.0, 0.999), (6, 140, 0.0, 0.62)]
    lap, pct = [], []
    for number, size, first, last in plan:
        lap += [number] * size
        pct += [first + (last - first) * k / (size - 1) for k in range(size)]
    n = len(lap)
    lap[1200] = -1  # a negative lap value is no lap
    lap[1400] = 9   # a lap of one sample is skipped
    completed = [max(0, value - 1) if value >= 0 else 0 for value in lap]
    completed = [min(value, 4) for value in completed]  # lap 5 never completes
    time = [100.0 + index / 60.0 + rng.uniform(-0.002, 0.002) for index in range(n)]
    for index in range(500, n):
        time[index] += 3.0  # a gap: one fallback dt
    time[900] = time[899] - 0.01  # a backward step: one fallback dt
    pit = [False] * n
    pit[0] = pit[1] = True
    for index in range(700, 790):
        pit[index] = True
    for index in range(1488, n):
        pit[index] = True
    # Two stops: one mid-race, one into the stall at the end (the lookahead clamps to the last sample).
    stall = [700 <= index < 760 or index >= 1492 for index in range(n)]
    active = [725 <= index < 755 for index in range(n)]
    speed = []
    for index in range(n):
        value = 0.0 if stall[index] else (22.0 if pit[index] else 45.0 + 18.0 * _sin(index / 23.0))
        speed.append(value + rng.uniform(-0.3, 0.3) if value else value)
    speed[37], speed[410], speed[1111] = NAN, INF, -0.5
    throttle = [max(0.0, min(1.0, 0.6 + 0.5 * _sin(index / 19.0))) for index in range(n)]
    brake = [max(0.0, min(1.0, -0.6 * _sin(index / 19.0) - 0.05)) for index in range(n)]
    for index in range(300, 330):  # percent-scaled samples (divided by 100 above 1.5)
        throttle[index] *= 100.0
        brake[index] *= 100.0
    throttle[55], brake[56], brake[57] = NAN, -0.0, 1.5
    steering = [1.1 * _sin(index / 31.0) + rng.uniform(-0.05, 0.05) for index in range(n)]
    steering[200], steering[201] = NAN, -0.0
    lat = [9.0 * _sin(index / 31.0) for index in range(n)]
    lon = [4.0 * _cos(index / 19.0) for index in range(n)]
    lat[88], lon[89] = NAN, -INF
    fuel, level = [], 40.0
    for index in range(n):
        if 730 <= index < 750:
            level += 0.5  # refuelling in the stall
        elif not pit[index]:
            level -= 0.004
        fuel.append(level)
    fuel[1010] = NAN
    fuel[1300] = fuel[1299] + 0.2  # a rise off pit road is no service
    fuel[1] = fuel[0] + 0.3  # a rise on pit road at the second sample is a service hit
    fuel[2] = fuel[1] + 0.1  # exactly 0.1 is not a rise over 0.10 (as f32 widened)
    # SessionFlags: green, a caution on lap 2, black, white on lap 5, checkered on lap 6,
    # repair required during the stop, one-lap-to-green before the restart.
    flags = []
    for index in range(n):
        value = 0x0004
        if 380 <= index < 560:
            value = 0x4000 | 0x0100
        if 560 <= index < 590:
            value = 0x0200
        if 640 <= index < 650:
            value |= 0x00010000
        if 700 <= index < 760:
            value |= 0x00100000
        if lap[index] == 5 and index > 1300:
            value |= 0x0002
        if lap[index] == 6:
            value |= 0x0001
        flags.append(value)
    # LapLastLapTime: lap N's time settles in lap N+1; lap 2's reading is off by 1.5 s and lap 5's is 0.
    bounds: dict[int, tuple[int, int]] = {}
    for index, value in enumerate(lap):
        if value >= 0:
            first, _last = bounds.get(value, (index, index))
            bounds[value] = (first, index)
    last_lap = [0.0] * n
    held = 0.0
    for index in range(n):
        current = lap[index]
        previous = current - 1
        if previous in bounds and current in bounds and index - bounds[current][0] >= 90:
            first, last = bounds[previous]
            held = time[last] - time[first] + 0.0137
            if previous == 2:
                held += 1.5
            if previous == 5:
                held = 0.0
        last_lap[index] = held
    state = [3 if index < 60 else (5 if index > 1450 else 4) for index in range(n)]
    surface = []
    for index in range(n):
        value = 1 if stall[index] else (2 if pit[index] else 3)
        surface.append(value)
    surface[10], surface[11], surface[1250] = -1, 0, 0
    ahead = [max(-1.0, 60.0 + 50.0 * _sin(index / 41.0)) for index in range(n)]
    behind = [max(-1.0, 40.0 + 35.0 * _cos(index / 37.0)) for index in range(n)]
    ahead[123], behind[124] = NAN, NAN
    position = [5 if index < 400 else (4 if index < 900 else 3) for index in range(n)]
    position[33], position[34] = 0, -1
    class_position = [value - 1 if value > 1 else value for value in position]

    def wheel(slip_lock: float, slip_spin: float, phase: int) -> list[float]:
        values = []
        for index in range(n):
            car = speed[index] if math.isfinite(speed[index]) else 30.0
            ratio = rng.uniform(-0.02, 0.02)
            if brake[index] >= 0.15 and (index + phase) % 7 == 0:
                ratio = slip_lock
            if throttle[index] >= 0.35 and (index + phase) % 5 == 0:
                ratio = slip_spin
            values.append(car * (1.0 + ratio))
        return values

    lf, rf, lr, rr = wheel(-0.12, 0.0, 0), wheel(-0.09, 0.0, 3), wheel(-0.01, 0.11, 1), wheel(-0.03, 0.15, 2)
    lf[66], rr[67] = NAN, INF
    odometers = {}
    for tire, reset in (("LF", True), ("RF", True), ("LR", False), ("RR", False)):
        distance, values = 1000.0, []
        for index in range(n):
            if reset and index == 745:
                distance = 0.0
            distance += (speed[index] if math.isfinite(speed[index]) and speed[index] > 0 else 0.0) / 60.0
            values.append(distance)
        odometers[tire] = values
    odometers["LR"][500] = NAN
    odometers["RR"][501] = odometers["RR"][500] - 3.0  # a backwards step is not a positive increase
    tires_used = {
        "LF": [1 if index < 750 else 2 for index in range(n)],
        "RF": [1 if index < 750 else 2 for index in range(n)],
        "LR": [1] * n,
        "RR": [1] * n,
    }
    service_flags = [0x13 if 690 <= index < 770 else 0 for index in range(n)]
    service_flags[695] = 0x60
    requested_fuel = [20.0 if 690 <= index < 770 else 0.0 for index in range(n)]
    requested_fuel[700] = NAN
    tow = [0.0] * n
    for index in range(1040, 1260):
        tow[index] = 12.0 - (index - 1040) * 0.05
    repair = [max(0.0, 20.0 - (index - 725) * 0.4) if 725 <= index < 760 else 0.0 for index in range(n)]
    optional = [5.0 if 735 <= index < 742 else 0.0 for index in range(n)]
    optional[741] = 0.004  # below timer_epsilon_s
    fast_used = [0 if index < 1150 else 1 for index in range(n)]
    columns: dict[str, tuple[int, str, list[Any]]] = {
        "SessionTime": (5, "s", time),
        "SessionNum": (2, "", [1] * n),
        "SessionState": (2, "irsdk_SessionState", state),
        "SessionFlags": (3, "irsdk_Flags", flags),
        "Lap": (2, "", lap),
        "LapCompleted": (2, "", completed),
        "LapDistPct": (4, "%", pct),
        "LapLastLapTime": (4, "s", last_lap),
        "Speed": (4, "m/s", speed),
        "Throttle": (4, "%", throttle),
        "Brake": (4, "%", brake),
        "SteeringWheelAngle": (4, "rad", steering),
        "SteeringWheelTorque": (4, "N*m", [12.0 * _sin(index / 13.0) for index in range(n)]),
        "LatAccel": (4, "m/s^2", lat),
        "LongAccel": (4, "m/s^2", lon),
        "YawRate": (4, "rad/s", [0.4 * _sin(index / 17.0) for index in range(n)]),
        "PitchRate": (4, "rad/s", [0.02 * _cos(index / 7.0) for index in range(n)]),
        "RollRate": (4, "rad/s", [-0.03 * _sin(index / 9.0) for index in range(n)]),
        "FuelLevel": (4, "l", fuel),
        "OnPitRoad": (1, "", pit),
        "PlayerCarInPitStall": (1, "", stall),
        "PitstopActive": (1, "", active),
        "PlayerTrackSurface": (2, "irsdk_TrkLoc", surface),
        "CarDistAhead": (4, "m", ahead),
        "CarDistBehind": (4, "m", behind),
        "PlayerCarPosition": (2, "", position),
        "PlayerCarClassPosition": (2, "", class_position),
        "LFspeed": (4, "m/s", lf),
        "RFspeed": (4, "m/s", rf),
        "LRspeed": (4, "m/s", lr),
        "RRspeed": (4, "m/s", rr),
        **{f"{tire}odometer": (4, "m", values) for tire, values in odometers.items()},
        **{f"{tire}TiresUsed": (2, "", values) for tire, values in tires_used.items()},
        "BrakeABSactive": (1, "", [brake[index] >= 0.5 and index % 3 == 0 for index in range(n)]),
        "BrakeABScutPct": (4, "%", [(brake[index] * 40.0 if index % 4 else 0.0) for index in range(n)]),
        "dcBrakeBias": (4, "", [0.56 if index < 600 else 0.575 for index in range(n)]),
        # Lap 6 records sky 9, no label: the session's "Dynamic" TrackSkies gives none either.
        "Skies": (2, "", [1 if index < 700 else (2 if index < 1350 else 9) for index in range(n)]),
        "TrackTempCrew": (4, "C", [35.2 + index * 0.001 for index in range(n)]),
        "AirTemp": (4, "C", [22.1 + index * 0.0005 for index in range(n)]),
        "WindVel": (4, "m/s", [3.4 + 0.5 * _sin(index / 50.0) for index in range(n)]),
        "WindDir": (4, "rad", [-0.4 + 0.01 * _sin(index / 50.0) for index in range(n)]),
        "RelativeHumidity": (4, "%", [0.55] * n),
        "FogLevel": (4, "%", [0.0] * n),
        "Precipitation": (4, "%", [12.0 if index > 1000 else 0.0 for index in range(n)]),
        "AirPressure": (4, "Pa", [101325.0 - index * 0.1 for index in range(n)]),
        "AirDensity": (4, "kg/m^3", [1.2 - index * 1e-6 for index in range(n)]),
        "TrackUsage": (4, "%", [0.4 + index * 0.0001 for index in range(n)]),
        "TrackWetness": (2, "irsdk_TrackWetness", [1 if index < 1000 else 2 for index in range(n)]),
        "WeatherDeclaredWet": (1, "", [index > 1350 for index in range(n)]),
        "PitSvFlags": (3, "irsdk_PitSvFlags", service_flags),
        "PitSvFuel": (4, "l", requested_fuel),
        "PlayerCarTowTime": (4, "s", tow),
        "PitRepairLeft": (4, "s", repair),
        "PitOptRepairLeft": (4, "s", optional),
        "PlayerFastRepairsUsed": (2, "", fast_used),
    }
    sessions = [
        {"SessionNum": 0, "SessionType": "Practice", "SessionTrackRubberState": "moderate usage"},
        {"SessionNum": 1, "SessionType": "Race", "SessionTrackRubberState": "carry over"},
    ]
    setup = {"Chassis": {"Front": {"SteeringRatio": "12:1", "SteeringOffset": "+6 deg"}}}
    info = _laps_session_info(base, sessions, setup, {"TrackSkies": "Dynamic"})
    return _laps_table(base, columns, session_info=info)


def _laps_offline_text(base: dict[str, Any]) -> dict[str, Any]:
    """Offline Testing running on into a Race: text channels, a float Lap, no SessionTime, wheel angle."""

    n = 900
    lap: list[float] = []
    pct: list[float] = []
    for number, size in ((0, 150), (1, 300), (2, 300), (3, 150)):
        lap += [float(number)] * size
        pct += [k / size for k in range(size)]
    lap[5], lap[6], lap[7] = NAN, -0.5, -0.0  # -0.0 is lap 0; -0.5 is no lap
    lap[460] = 2.7  # int(2.7) is lap 2
    lap[461] = INF
    lap[880] = lap[881] = 1e19  # a lap number beyond i64: Python's int is unbounded
    pct[300] = NAN
    time_of_day = [36000.0 + index * 0.25 for index in range(n)]  # f32: coarse steps
    time_of_day[400] = NAN
    session_num = [0 if index < 600 else 1 for index in range(n)]
    flags = [0x0200 if index % 50 < 30 else 0x0004 for index in range(n)]
    flags[100] = 0x0200 | 0x4000  # a real caution keeps its bits
    speed = [30.0 + 10.0 * _sin(index / 20.0) for index in range(n)]
    speed[0], speed[1] = -INF, NAN
    skies = ["2"] * n
    skies[10], skies[11], skies[12] = " 3 ", "abc", "1_0"
    for index in range(750, n):
        skies[index] = "nan"  # lap 3 has no sky sample: the label falls back to TrackSkies
    wind = [str(0.25 * _sin(index / 80.0)) for index in range(n)]
    wind[20] = "nan"
    position = [" 7 " if index < 450 else "6" for index in range(n)]
    position[30], position[31] = "", "x"
    pit_text = ["true" if 500 <= index < 540 else "no" for index in range(n)]
    pit_text[0], pit_text[520] = " ON ", "0"
    flag_text = [str(value) for value in flags]
    flag_text[700] = "1e3"
    columns: dict[str, tuple[int, str, list[Any]]] = {
        "SessionTimeOfDay": (4, "s", time_of_day),
        "SessionNum": (2, "", session_num),
        "SessionFlags": (0, "irsdk_Flags", flag_text),
        "Lap": (4, "", lap),
        "LapDistPct": (4, "%", pct),
        "Speed": (4, "m/s", speed),
        "ThrottleRaw": (4, "%", [0.5 + 0.5 * _sin(index / 9.0) for index in range(n)]),
        "BrakeRaw": (4, "%", [max(0.0, -_sin(index / 9.0)) for index in range(n)]),
        "SteeringWheelAngle": (4, "rad", [0.9 * _sin(index / 15.0) for index in range(n)]),
        "SteeringWheelAngleMax": (4, "rad", [7.85 if index < 400 else 9.42 for index in range(n)]),
        "FuelLevel": (5, "l", [30.0 - index * 0.01 for index in range(n)]),
        "OnPitRoad": (0, "", pit_text),
        "PlayerCarInPitStall": (1, "", [510 <= index < 530 for index in range(n)]),
        "PlayerCarPosition": (0, "", position),
        "LFspeed": (4, "m/s", [value * 0.85 for value in speed]),
        "RRspeed": (4, "m/s", [value * 1.12 for value in speed]),
        "LFTiresUsed": (2, "", [3] * n),
        "RRodometer": (5, "m", [5000.0 + index * 0.5 for index in range(n)]),
        "Skies": (0, "", skies),
        "WindDir": (0, "rad", wind),
        "TrackTemp": (5, "C", [41.0] * n),
        "TrackWetness": (2, "irsdk_TrackWetness", [0] * n),
        "PlayerFastRepairsUsed": (2, "", [0] * 300 + [1] * 300 + [3] * 300),
    }
    sessions = [
        {"SessionNum": "0", "SessionType": "Offline Testing", "SessionTrackRubberState": "high usage"},
        {"SessionNum": " 1 extra", "SessionType": "Race"},
    ]
    setup = {"Chassis": {"Front": {"SteeringRatio": "14:1"}}}
    info = _laps_session_info(base, sessions, setup, {"TrackSkies": "Partly Cloudy"})
    return _laps_table(base, columns, session_info=info,
                       metadata={"sim_session_type": "Offline Testing", "sim_session_num": 0, "is_race": False})


def _laps_no_laps(base: dict[str, Any]) -> dict[str, Any]:
    """No lap with two samples: _lap_summaries returns [] and no run exists."""

    n = 300
    lap = [index if index % 2 == 0 else -1 for index in range(n)]
    columns: dict[str, tuple[int, str, list[Any]]] = {
        "SessionTime": (5, "s", [index / 60.0 for index in range(n)]),
        "Lap": (2, "", lap),
        "LapDistPct": (4, "%", [(index % 100) / 100.0 for index in range(n)]),
        "Speed": (4, "m/s", [NAN] * n),
        "OnPitRoad": (1, "", [index < 3 for index in range(n)]),
    }
    return _laps_table(base, columns)


def _laps_traffic_sides(base: dict[str, Any]) -> dict[str, Any]:
    """Close traffic ahead, behind, on both sides and under the screen: every branch of the side.

    200 samples a lap at 40 m/s (one sample is 0.5 % of a lap; close is within 30 m ahead and
    16 m behind), laps 0 to 10 one case of ``_traffic_sides`` each; lap 11 repeats lap 10's
    distances at 10 m/s (floors of 25 m and 15 m), lap 12 holds 30 m ahead and 20 m behind over
    a 20 to 60 m/s ramp, and a partial lap 13 has a car close behind.
    """

    far = 500.0

    def close(first: int, last: int, distance: float) -> dict[int, float]:
        return {offset: distance for offset in range(first, last)}

    limits_ahead = {**close(0, 20, 30.0), **close(20, 40, 30.5), **close(40, 60, 0.0), **close(60, 80, -1.0),
                    **close(80, 100, NAN), **close(100, 104, INF)}
    limits_behind = {**close(100, 120, 16.0), **close(120, 140, 16.5), **close(140, 160, 0.0), **close(160, 162, -INF)}
    plan: list[tuple[dict[int, float], dict[int, float]]] = [
        ({}, {}),  # open air: no side
        (close(0, 60, 10.0), {}),  # 30 % ahead
        ({}, close(100, 150, 8.0)),  # 25 % behind
        (close(0, 40, 10.0), close(100, 130, 8.0)),  # 20 % and 15 %, apart: both
        (close(50, 74, 10.0), close(50, 74, 8.0)),  # the same 12 % on each side: both, counted once
        (close(0, 20, 10.0), close(100, 118, 8.0)),  # exactly 10 % ahead, 9 % behind: ahead
        (close(0, 12, 10.0), close(100, 110, 8.0)),  # 6 % + 5 %: only the union screens; ahead
        (close(0, 8, 10.0), close(100, 114, 8.0)),  # 4 % + 7 %: behind
        (close(0, 6, 10.0), {}),  # 3 %, under the screen: ahead
        (close(0, 8, 10.0), close(100, 108, 8.0)),  # 4 % each, equal: both
        (limits_ahead, limits_behind),  # the limits themselves are close; beyond, zero, negative, NaN not
        (limits_ahead, limits_behind),  # the same at 10 m/s: nothing close
        ({offset: 30.0 for offset in range(200)}, {offset: 20.0 for offset in range(200)}),  # speed ramp
    ]
    n = 200 * len(plan) + 30
    lap, pct, speed, ahead, behind = [], [], [], [], []
    for number, (near_ahead, near_behind) in enumerate(plan):
        for offset in range(200):
            lap.append(number)
            pct.append(offset / 200.0)
            if number == len(plan) - 2:
                speed.append(10.0)
            elif number == len(plan) - 1:
                speed.append(20.0 + 40.0 * offset / 199.0)  # 30 m ahead closes at 40 m/s, 20 m behind at 50 m/s
            else:
                speed.append(40.0)
            ahead.append(near_ahead.get(offset, far))
            behind.append(near_behind.get(offset, far))
    for offset in range(30):  # a partial last lap with a car close behind
        lap.append(len(plan))
        pct.append(offset / 200.0)
        speed.append(40.0)
        ahead.append(far)
        behind.append(8.0)
    columns: dict[str, tuple[int, str, list[Any]]] = {
        "SessionTime": (5, "s", [index / 60.0 for index in range(n)]),
        "Lap": (2, "", lap),
        "LapDistPct": (4, "%", pct),
        "Speed": (4, "m/s", speed),
        "CarDistAhead": (4, "m", ahead),
        "CarDistBehind": (4, "m", behind),
    }
    return _laps_table(base, columns)


def _laps_traffic_behind_only(base: dict[str, Any]) -> dict[str, Any]:
    """CarDistBehind recorded without CarDistAhead: the traffic screen runs, nothing is ever ahead."""

    n = 600
    behind = [8.0 if index % 200 < 30 else 500.0 for index in range(n)]
    behind[205:225] = [NAN] * 20  # lap 2: a missing distance is not close
    columns: dict[str, tuple[int, str, list[Any]]] = {
        "SessionTime": (5, "s", [index / 60.0 for index in range(n)]),
        "Lap": (2, "", [1 + index // 200 for index in range(n)]),
        "LapDistPct": (4, "%", [(index % 200) / 200.0 for index in range(n)]),
        "Speed": (4, "m/s", [35.0 + 10.0 * _sin(index / 25.0) for index in range(n)]),
        "CarDistBehind": (4, "m", behind),
    }
    return _laps_table(base, columns)


BUILDERS = (
    ("laps-race", _laps_race),
    ("laps-offline-text", _laps_offline_text),
    ("laps-no-laps", _laps_no_laps),
    ("laps-traffic-sides", _laps_traffic_sides),
    ("laps-traffic-behind-only", _laps_traffic_behind_only),
)

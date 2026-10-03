"""Unit "sections" synthetic tables for ``emit_kernel_vectors.py`` (phase2-plan.md section 2, U4).

The fixture is one 120-sample lap with no load zone; the corpus is oval
racing with few clean laps per run. These tables drive a 60 Hz practice
session around a synthetic road course with three corners (one across the
start/finish line) through every branch of the unit's entries: clean and
excluded laps (caution, restart, pit, off-track, traffic), two runs split by a
pit stop across the line with a confirmed four-tyre change, usable early,
middle and late phases whose drift crosses each coaching threshold, slip
energy from velocity components, short pit-road brushes, and the steering,
channel-type and missing-channel variants: road-wheel steering, percent
brake, text and float channels, NaN and infinity samples, no load zones,
missing energy channels, Offline Testing one-lap-to-green and a recording
without SessionTime. Float channels hold widened f32 values, as the decoder
yields them, so ``table.ccs`` reproduces them bit for bit.
"""

from __future__ import annotations

import copy
import math
import random
import struct
from typing import Any, Callable

_SECTIONS_F32 = struct.Struct("<f")
_SECTIONS_TYPES = {0: ("char", 1), 1: ("bool", 1), 2: ("int", 4), 3: ("bitfield", 4), 4: ("float", 4), 5: ("double", 8)}
_SECTIONS_RATE = 60.0
_SECTIONS_TRACK_M = 420.0
NAN, INF = float("nan"), float("inf")

# Corners as (start pct, end pct, direction); the last crosses the start/finish line.
_CORNERS = ((0.20, 0.32, 1.0), (0.55, 0.70, -1.0), (0.92, 0.04, 1.0))


def _sections_value(code: int, value: Any) -> Any:
    if value is None:
        raise ValueError("a decoded column has no missing samples")
    if code == 4:
        return _SECTIONS_F32.unpack(_SECTIONS_F32.pack(value))[0]
    if code == 5:
        return float(value)
    if code == 1:
        return bool(value)
    if code in (2, 3):
        return int(value)
    return str(value)


def _sections_table(
    base: dict[str, Any],
    columns: dict[str, tuple[int, str, list[Any]]],
    *,
    session_info: dict[str, Any],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    """Replace the fixture's channels with ``columns`` (name -> type code, unit, values)."""

    import workflow

    lengths = {len(values) for _code, _unit, values in columns.values()}
    if len(lengths) != 1:
        raise ValueError(f"synthetic columns differ in length: {sorted(lengths)}")
    (count,) = lengths
    variables, samples, offset = [], {}, 0
    for name, (code, unit, values) in columns.items():
        kind, size = _SECTIONS_TYPES[code]
        variables.append({
            "name": name, "description": name, "unit": unit, "type": kind, "type_code": code,
            "offset": offset, "count": 1, "count_as_time": False, "byte_size": size,
        })
        offset += size
        samples[name] = [_sections_value(code, value) for value in values]
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
    base["metadata"].update(metadata)
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


def _sections_session_info(base: dict[str, Any], session_type: str, car_setup: dict[str, Any]) -> dict[str, Any]:
    info = copy.deepcopy(base["session_info"])
    info["SessionInfo"] = {"Sessions": [{"SessionNum": 0, "SessionType": session_type}]}
    info["CarSetup"] = car_setup
    info["WeekendInfo"].update({"EventType": session_type, "Category": "Road"})
    return info


def _corner_weight(pct: float, corner: tuple[float, float, float]) -> float:
    """0 outside the corner, rising to 1 at its apex (a sine over the corner's span)."""

    start, end, _direction = corner
    span = (end - start) % 1.0
    relative = (pct - start) % 1.0
    if relative >= span:
        return 0.0
    return math.sin(math.pi * relative / span)


def _approach(pct: float, corner: tuple[float, float, float], lead: float) -> float:
    """1 in the ``lead`` lap fraction before the corner, falling to 0 at its start."""

    start = corner[0]
    before = (start - pct) % 1.0
    if before > lead:
        return 0.0
    return 1.0 - before / lead


def _drive(*, seed: int, laps: int, pit_lap: int | None, caution_lap: int | None,
           off_track_lap: int | None, traffic_lap: int | None, flat: bool = False, comb: bool = False,
           late_from: int = 15, drive_throughs: tuple[tuple[int, float, float], ...] = (),
           odometer_reset: bool = True, odometer_start: float = 30.0,
           not_racing: tuple[int, float, float] | None = None) -> dict[str, list[Any]]:
    """Per-sample channels of one session: laps 0 (from pct 0.85) to ``laps`` and a partial lap."""

    rng = random.Random(seed)
    dt = 1.0 / _SECTIONS_RATE
    columns: dict[str, list[Any]] = {name: [] for name in (
        "SessionTime", "Lap", "LapDistPct", "Speed", "Brake", "Throttle", "SteeringWheelAngle",
        "LatAccel", "LongAccel", "VelocityX", "VelocityY", "OnPitRoad", "PlayerCarInPitStall",
        "SessionFlags", "SessionState", "PlayerTrackSurface", "CarDistAhead", "CarDistBehind",
        "LFodometer", "RFodometer", "LRodometer", "RRodometer", "FuelLevel",
    )}
    lap, pct, time = 0, 0.85, 0.0
    odometer = odometer_start
    fuel = 40.0
    previous_speed = 30.0
    stall_left = 0
    pitted = False
    while lap <= laps or pct < 0.35:
        k = lap
        in_pit = pit_lap is not None and ((lap == pit_lap and pct >= 0.93) or (lap == pit_lap + 1 and pct < 0.06))
        in_pit = in_pit or any(lap == number and low <= pct < high for number, low, high in drive_throughs)
        speed = 42.0
        brake = 0.0
        throttle = 1.0
        steer = 0.0
        lateral = 0.0
        late = 1.0 if k >= late_from else 0.0
        for number, corner in enumerate(_CORNERS):
            weight = 0.0 if flat else _corner_weight(pct, corner)
            lead = 0.06 + (0.03 * late if number == 1 else 0.0)
            approach = 0.0 if flat else _approach(pct, corner, lead)
            # Late laps drift corner by corner, so each coaching threshold is crossed once:
            # corner 1 more entry speed, steering and an earlier turn-in; corner 2 longer and
            # harder braking, a slower apex, a later brake release and throttle pickup;
            # corner 3 extra steering corrections.
            if number == 0:
                speed -= (13.0 - 2.5 * late) * weight - 2.0 * late * _approach(pct, corner, 0.12)
                steer_amplitude = 1.15 * (1.0 + 0.3 * late)
                early_turn = _approach(pct, corner, 0.02) if late and not flat else 0.0
                steer += steer_amplitude * corner[2] * max(weight, 0.5 * early_turn)
            elif number == 1:
                speed -= (13.0 + 2.5 * late) * weight
                steer += 1.15 * corner[2] * weight
            else:
                speed -= 13.0 * weight
                steer += 1.15 * corner[2] * weight
                if late and weight > 0.0:
                    steer += 0.12 * math.sin(time * 14.0)
            release = 0.03 + (0.04 * late if number == 1 else 0.0)
            in_release = (pct - corner[0]) % 1.0 < release
            trail = 0.45 if in_release and weight > 0.0 else 0.0
            brake = max(brake, (0.85 + 0.15 * late * (number == 1)) * approach, trail)
            pickup = max(weight, approach)
            if number == 1 and late and 0.0 < (pct - corner[0]) % 1.0 < 0.16:
                pickup = max(pickup, 0.9)
            if number == 1 and late and 0.0 < (pct - corner[0]) % 1.0 < 0.20:
                speed -= 2.0
            throttle = min(throttle, 1.0 - 0.9 * pickup)
            lateral += corner[2] * weight * (speed * speed) / 160.0
        if comb:
            steer = 0.5 if int(pct * 200) % 2 == 0 else 0.0
        steer += rng.uniform(-0.004, 0.004)
        speed += rng.uniform(-0.2, 0.2)
        if in_pit:
            speed = 20.0
            throttle, brake = 0.2, 0.0
            if not pitted and lap == pit_lap and pct >= 0.98:
                stall_left = int(3.5 * _SECTIONS_RATE)
                pitted = True
            if stall_left > 0:
                speed = 0.0
                stall_left -= 1
                if stall_left == 0 and odometer_reset:
                    odometer = 0.0
                    fuel = 40.0
        brush = lap == 9 and 0.45 <= pct < 0.46  # a pit-road brush shorter than 3 s
        off = off_track_lap is not None and lap == off_track_lap and 0.40 <= pct < 0.46
        traffic = traffic_lap is not None and lap == traffic_lap and 0.21 <= pct < 0.25
        caution = caution_lap is not None and lap == caution_lap
        columns["SessionTime"].append(time)
        columns["Lap"].append(lap)
        columns["LapDistPct"].append(pct)
        columns["Speed"].append(speed)
        columns["Brake"].append(max(0.0, min(1.0, brake)))
        columns["Throttle"].append(max(0.0, min(1.0, throttle)))
        columns["SteeringWheelAngle"].append(steer)
        columns["LatAccel"].append(lateral)
        columns["LongAccel"].append((speed - previous_speed) * _SECTIONS_RATE)
        beta = 0.06 * steer / 1.5
        columns["VelocityX"].append(speed * math.cos(beta))
        columns["VelocityY"].append(speed * math.sin(beta))
        columns["OnPitRoad"].append(in_pit or brush)
        columns["PlayerCarInPitStall"].append(in_pit and speed == 0.0)
        columns["SessionFlags"].append(0x8000 | 0x4 if caution else 0x4)
        quiet = not_racing is not None and lap == not_racing[0] and not_racing[1] <= pct < not_racing[2]
        columns["SessionState"].append(2 if quiet else 4)
        columns["PlayerTrackSurface"].append(0 if off else (1 if in_pit and speed == 0.0 else 3))
        columns["CarDistAhead"].append(9.0 if traffic else 0.0)
        columns["CarDistBehind"].append(0.0)
        for tire in ("LF", "RF", "LR", "RR"):
            columns[f"{tire}odometer"].append(odometer)
        columns["FuelLevel"].append(fuel)
        previous_speed = speed
        odometer += speed * dt
        fuel -= 0.0004
        time += dt
        pct += speed * dt / _SECTIONS_TRACK_M
        if pct >= 1.0:
            pct -= 1.0
            lap += 1
    return columns


_CHANNELS = {
    "SessionTime": (5, "s"), "SessionNum": (2, ""), "Lap": (2, ""), "LapDistPct": (4, "%"),
    "Speed": (4, "m/s"), "Brake": (4, "%"), "Throttle": (4, "%"), "SteeringWheelAngle": (4, "rad"),
    "LatAccel": (4, "m/s^2"), "LongAccel": (4, "m/s^2"), "VelocityX": (4, "m/s"), "VelocityY": (4, "m/s"),
    "OnPitRoad": (1, ""), "PlayerCarInPitStall": (1, ""), "SessionFlags": (3, "irsdk_Flags"),
    "SessionState": (2, "irsdk_SessionState"), "PlayerTrackSurface": (2, "irsdk_TrkLoc"),
    "CarDistAhead": (4, "m"), "CarDistBehind": (4, "m"), "LFodometer": (4, "m"), "RFodometer": (4, "m"),
    "LRodometer": (4, "m"), "RRodometer": (4, "m"), "FuelLevel": (4, "l"),
}


def _typed(columns: dict[str, list[Any]], overrides: dict[str, tuple[int, str]] | None = None,
           drop: tuple[str, ...] = ()) -> dict[str, tuple[int, str, list[Any]]]:
    count = len(columns["SessionTime"])
    columns = {"SessionNum": [0] * count, **columns}
    typed = {}
    for name, values in columns.items():
        if name in drop:
            continue
        code, unit = (overrides or {}).get(name, _CHANNELS[name])
        typed[name] = (code, unit, values)
    return typed


def _sections_practice(base: dict[str, Any]) -> dict[str, Any]:
    """Two runs of clean laps split by a four-tyre stop across the line; steering-wheel basis."""

    columns = _drive(seed=20261003, laps=16, pit_lap=6, caution_lap=2, off_track_lap=11, traffic_lap=10,
                     not_racing=(13, 0.56, 0.565))
    # Inside clean laps, in a corner: a pit-road sample, an off-track sample, a car close behind.
    laps, pct = columns["Lap"], columns["LapDistPct"]
    for index in range(len(laps)):
        if laps[index] == 12 and 0.215 <= pct[index] < 0.217:
            columns["OnPitRoad"][index] = True
        if laps[index] == 13 and 0.60 <= pct[index] < 0.603:
            columns["PlayerTrackSurface"][index] = 0
        if laps[index] == 14 and 0.62 <= pct[index] < 0.623:
            columns["CarDistBehind"][index] = 5.0
    info = _sections_session_info(base, "Practice", {"Tires": {"LeftFront": {"ColdPressure": "25.0 psi"}}})
    return _sections_table(base, _typed(columns), session_info=info,
                           metadata={"sim_session_type": "Practice", "sim_session_num": 0, "event_type": "Practice",
                                     "is_race": False})


def _sections_road_wheel(base: dict[str, Any]) -> dict[str, Any]:
    """Road-wheel steering (12:1), percent brake, text OnPitRoad, float Lap, NaN and infinity,
    lap-distance gaps and a regression, a pit entry without lap distance and a drive-through."""

    columns = _drive(seed=7, laps=14, pit_lap=5, caution_lap=None, off_track_lap=None, traffic_lap=None,
                     late_from=13, drive_throughs=((10, 0.40, 0.56), (12, 0.60, 0.75), (13, 0.20, 0.36)),
                     odometer_reset=False)
    count = len(columns["SessionTime"])
    laps, pct, time = columns["Lap"], columns["LapDistPct"], columns["SessionTime"]
    # Lap 3 three percent quicker (time compressed, later samples shifted): the reference lap,
    # whose gap at 0.60-0.64 leaves the lap-12 drive-through uncovered.
    lap3 = [i for i in range(count) if laps[i] == 3]
    start, saved = time[lap3[0]], (time[lap3[-1]] - time[lap3[0]]) * 0.03
    for index in range(lap3[0], count):
        time[index] = start + (time[index] - start) * 0.97 if laps[index] == 3 else time[index] - saved
    # The lap-13 drive-through ends on a sample without SessionTime: not measured.
    last = max(i for i in range(count) if laps[i] == 13 and columns["OnPitRoad"][i])
    time[last] = NAN
    entry = next(i for i in range(count) if columns["OnPitRoad"][i] and laps[i] == 5)
    pct[entry] = NAN
    for index in range(count):
        if laps[index] == 3 and 0.60 <= pct[index] < 0.64:
            pct[index] = NAN  # one gap wider than 0.02: still usable
        elif laps[index] == 4 and 0.30 <= pct[index] < 0.42:
            pct[index] = NAN  # limited coverage
        elif laps[index] == 2 and 0.10 <= pct[index] < 0.70:
            pct[index] = NAN  # unavailable coverage
    regress = next(i for i in range(count) if laps[i] == 7 and pct[i] > 0.5)
    pct[regress] = pct[regress - 1] - 0.004
    stalled = next(i for i in range(count) if laps[i] == 1 and pct[i] > 0.5)
    time[stalled] = time[stalled - 1] - 0.001  # time steps back while distance advances
    # Sideslip guards (slow planar speed, backwards), a sample with neither acceleration, and
    # two laps excluded by session state and by traffic.
    for index in range(count):
        if laps[index] == 7 and 0.30 <= pct[index] < 0.303:
            columns["VelocityX"][index], columns["VelocityY"][index] = 1.0, 1.0
        if laps[index] == 7 and 0.31 <= pct[index] < 0.313:
            columns["VelocityX"][index], columns["VelocityY"][index] = 0.3, 6.0
        if laps[index] == 8 and 0.10 <= pct[index] < 0.16:
            columns["SessionState"][index] = 2  # not racing for 6 % of the lap: excluded
        if laps[index] == 11 and 0.30 <= pct[index] < 0.45:
            columns["CarDistAhead"][index] = 9.0  # close traffic for 15 % of the lap: excluded
    both = next(i for i in range(count) if laps[i] == 7 and pct[i] > 0.4)
    columns["LatAccel"][both], columns["LongAccel"][both] = NAN, NAN
    columns["Brake"] = [value * 100.0 for value in columns["Brake"]]
    columns["OnPitRoad"] = [" 1 " if value else "0" for value in columns["OnPitRoad"]]
    columns["Lap"] = [float(value) for value in laps]
    rng = random.Random(11)
    for name, value in (("Speed", NAN), ("LapDistPct", NAN), ("LatAccel", INF), ("SessionTime", NAN),
                        ("SteeringWheelAngle", NAN), ("Throttle", -INF), ("VelocityY", NAN)):
        for _ in range(6):
            columns[name][rng.randrange(count)] = value
    pct[rng.randrange(count)] = -0.25
    pct[rng.randrange(count)] = 1.5
    info = _sections_session_info(base, "Practice", {"Chassis": {"Front": {"SteeringRatio": "12:1",
                                                                           "SteeringOffset": "+0 deg"}}})
    overrides = {"OnPitRoad": (0, ""), "Lap": (4, "")}
    return _sections_table(base, _typed(columns, overrides), session_info=info,
                           metadata={"sim_session_type": "Practice", "sim_session_num": 0, "event_type": "Practice",
                                     "is_race": False})


def _sections_flat(base: dict[str, Any]) -> dict[str, Any]:
    """Steering alternating bin by bin, no lateral load and no velocity components: no load zone
    of two or more bins (uniform fallback segments) and load-only tyre energy."""

    columns = _drive(seed=5, laps=6, pit_lap=None, caution_lap=None, off_track_lap=None, traffic_lap=None,
                     flat=True, comb=True)
    info = _sections_session_info(base, "Practice", {})
    return _sections_table(base, _typed(columns, drop=("VelocityX", "VelocityY", "CarDistAhead", "CarDistBehind",
                                                       "PlayerTrackSurface")),
                           session_info=info,
                           metadata={"sim_session_type": "Practice", "sim_session_num": 0, "event_type": "Practice",
                                     "is_race": False})


def _sections_offline(base: dict[str, Any]) -> dict[str, Any]:
    """Offline Testing with one-lap-to-green on every sample and SessionTimeOfDay only."""

    columns = _drive(seed=3, laps=8, pit_lap=3, caution_lap=None, off_track_lap=None, traffic_lap=None,
                     odometer_start=600.0)
    columns["SessionFlags"] = [value | 0x0200 for value in columns["SessionFlags"]]
    columns["SessionTimeOfDay"] = [43200.0 + value for value in columns.pop("SessionTime")]
    # Lap 6 held at one steering angle: no turn-in crossing, censored by the baseline fallback.
    columns["SteeringWheelAngle"] = [0.4 if lap == 6 else value
                                     for lap, value in zip(columns["Lap"], columns["SteeringWheelAngle"])]
    info = _sections_session_info(base, "Offline Testing", {})
    overrides = {"SessionTimeOfDay": (4, "s")}
    typed = {}
    count = len(columns["Lap"])
    for name, values in {"SessionNum": [0] * count, **columns}.items():
        code, unit = overrides.get(name) or _CHANNELS[name]
        typed[name] = (code, unit, values)
    return _sections_table(base, typed, session_info=info,
                           metadata={"sim_session_type": "Offline Testing", "sim_session_num": 0,
                                     "event_type": "Offline Testing", "is_race": False})


def _sections_caution(base: dict[str, Any]) -> dict[str, Any]:
    """A short race under caution throughout: no clean lap and no profile bin."""

    columns = _drive(seed=9, laps=3, pit_lap=None, caution_lap=None, off_track_lap=None, traffic_lap=None)
    columns["SessionFlags"] = [0x8000 | 0x4] * len(columns["SessionFlags"])
    info = _sections_session_info(base, "Race", {})
    return _sections_table(base, _typed(columns), session_info=info,
                           metadata={"sim_session_type": "Race", "sim_session_num": 0, "event_type": "Race",
                                     "is_race": True})


BUILDERS: tuple[tuple[str, Callable[[dict[str, Any]], dict[str, Any]]], ...] = (
    ("sections-practice", _sections_practice),
    ("sections-road-wheel", _sections_road_wheel),
    ("sections-flat", _sections_flat),
    ("sections-offline", _sections_offline),
    ("sections-caution", _sections_caution),
)

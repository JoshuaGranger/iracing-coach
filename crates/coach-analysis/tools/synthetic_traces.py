"""Synthetic tables for unit U3 (trace payload, downsampling, track profile).

Each builder receives a deep copy of the fixture's decoded table and returns a
new one built from scratch: a few laps at 60 Hz with the channels the trace
payload and the track profile read, shaped to reach the oracle branches the
fixture (eight channels, one lap, one sample a bin) and the corpus do not:

* ``traces-roadwheel``: a stated 12:1 steering ratio (road-wheel basis and the
  steering-wheel-angle signal), every optional signal with unit strings the
  corpus lacks (km/h, mph, kph, bar, Pa, K, degC with the degree sign, mm,
  cm, ft, in, l, gal, g, rad/s, deg/s), two or three samples a bin (even
  medians, signed-peak ties between x and -x and between -0.0 and 0.0, gear
  medians at .5), lap-distance wrap so one bin's samples are not one run,
  distances just outside [0, 1], the full setup series, sectors with
  duplicates, text and out-of-range starts, and load zones that merge across
  start/finish.
* ``traces-gaps``: a ratio change mid-recording (steering-wheel basis), NaN
  and infinity in float channels, text channels (float() and _bool parsing),
  an all-NaN channel (not registered), values whose unit conversion
  overflows, a non-race session with one-lap-to-green and caution flags,
  pit-road samples, and a lap-distance gap.
* ``traces-no-distance``: no LapDistPct (no track profile, no trace points)
  and an unreadable steering ratio.

Values are float64 channels (type code 5) unless the case needs another SDK
type, so the decode store keeps every bit the builder wrote.
"""

from __future__ import annotations

import math
from typing import Any, Callable

RATE = 60
F64, I32, BOOL, TEXT, U32 = 5, 2, 1, 0, 3
_TYPE_NAMES = {F64: "double", I32: "int", BOOL: "bool", TEXT: "char", U32: "bitfield"}
_BYTE_SIZE = {F64: 8, I32: 4, BOOL: 1, TEXT: 1, U32: 4}


def _table(base: dict[str, Any], channels: dict[str, tuple[int, str, list[Any]]],
           *, session_type: str = "Race", car_setup: dict[str, Any] | None = None,
           sectors: list[Any] | None = None) -> dict[str, Any]:
    """The fixture's decoded mapping with its samples replaced by ``channels``."""

    count = len(next(iter(channels.values()))[2])
    variables = []
    offset = 0
    for name, (type_code, unit, values) in channels.items():
        if len(values) != count:
            raise ValueError(f"{name}: {len(values)} values for {count} samples")
        variables.append({
            "name": name, "description": f"Synthetic {name}", "unit": unit,
            "type": _TYPE_NAMES[type_code], "type_code": type_code, "offset": offset,
            "count": 1, "count_as_time": False, "byte_size": _BYTE_SIZE[type_code],
        })
        offset += _BYTE_SIZE[type_code]
    telemetry = base
    telemetry["samples"] = {name: list(values) for name, (_t, _u, values) in channels.items()}
    telemetry["variables"] = variables
    telemetry["available_variables"] = [dict(variable) for variable in variables]
    telemetry["sample_indices"] = list(range(count))
    telemetry["sample_count"] = count
    telemetry["source_record_count"] = count
    telemetry["channel_selection"] = {"mode": "selected", "available_count": len(variables),
                                      "decoded_count": len(variables)}
    provenance = telemetry["sample_provenance"][0]
    telemetry["sample_provenance"] = [dict(provenance, native_record_index=index) for index in range(count)]
    metadata = telemetry["metadata"]
    metadata["record_count"] = count
    metadata["sim_session_type"] = session_type
    metadata["is_race"] = session_type == "Race"
    session_info = telemetry["session_info"]
    session_info["SessionInfo"] = {"Sessions": [{"SessionNum": 1, "SessionType": session_type}]}
    if car_setup is not None:
        session_info["CarSetup"] = car_setup
    if sectors is not None:
        session_info["SplitTimeInfo"] = {"Sectors": sectors}
    return telemetry


def _laps(fractions: list[float], start_lap: int = 1, lead: int = 0) -> tuple[list[int], list[float]]:
    """Lap numbers and lap distance for consecutive laps of ``len(fractions)`` steps.

    ``lead`` samples of each new lap still read the previous lap's distance
    (iRacing increments Lap a moment before LapDistPct wraps), so the first
    and last bins of a lap hold samples that are not one run.
    """

    laps: list[int] = []
    distance: list[float] = []
    for lap_offset, steps in enumerate(fractions):
        for step in range(int(steps)):
            laps.append(start_lap + lap_offset)
            distance.append(step / steps)
    for index in range(1, len(laps)):
        if laps[index] != laps[index - 1]:
            for back in range(lead):
                if index + back < len(laps) and laps[index + back] == laps[index]:
                    distance[index + back] = 0.996 + 0.001 * back
    return laps, distance


def _wave(x: float) -> float:
    """A triangle wave in [-1, 1] of period 1 from IEEE arithmetic alone, so both platforms
    build the same table (math.sin and math.cos come from the platform's C library)."""

    return 4.0 * abs(x - math.floor(x + 0.5)) - 1.0


def _corner(pct: float) -> float:
    """0..1 load: corners at 0.88..0.12 (across start/finish) and 0.38..0.62 with a flat
    apex (equal minimum speeds), and a one-bin spike at 0.75 (a segment too short to keep)."""

    def bump(center: float, half: float) -> float:
        distance = min(abs(pct - center), 1.0 - abs(pct - center))
        return min(1.0, 1.5 * max(0.0, 1.0 - distance / half))

    return max(bump(0.0, 0.12), bump(0.5, 0.12), 0.9 if abs(pct - 0.75) < 0.003 else 0.0)


def roadwheel(base: dict[str, Any]) -> dict[str, Any]:
    lap, pct = _laps([360, 260], lead=3)
    count = len(lap)
    pct[5] = -0.005  # inside [-0.01, 0): bin 159 of the payload, skipped by the profile
    pct[6] = -0.02  # outside: skipped by both
    pct[count - 1] = 1.005  # inside (0.005 after % 1.0)
    pct[count - 2] = 1.02  # outside
    load = [_corner(value % 1.0) for value in pct]
    time = [index / RATE for index in range(count)]
    speed = [55.0 - 25.0 * c for c in load]
    speed[40:44] = [1.5, 2.0, 2.5, 3.0]  # sideslip undefined below 5 m/s
    sign = [(-1.0) ** (index // 2) for index in range(count)]
    # Pairs x, -x inside a bin: the signed peak keeps the first.
    steer = [0.42 * c * (1.0 if index % 2 == 0 else -1.0) for index, c in enumerate(load)]
    steer[100] = -0.0
    steer[101] = 0.0
    pitch = [0.0 if index % 3 else -0.0 for index in range(count)]
    gear = [2 + (index // 2) % 2 for index in range(count)]  # medians of 2.0 and 3.0 are 2.5
    vx = [s * (1.0 - 0.00125 * c) for s, c in zip(speed, load)]
    vy = [s * 0.05 * c * sg for s, c, sg in zip(speed, load, sign)]
    vx[60] = 0.4  # forward at or below 0.5 m/s
    vy[60] = 6.0
    vx[61] = -12.0  # backwards
    lat_g = [9.80665 * 1.8 * c * sg for c, sg in zip(load, sign)]
    brake = [0.0] * count
    for index, value in enumerate(pct):
        position = value % 1.0
        if 0.40 <= position < 0.45 or 0.90 <= position < 0.95:
            brake[index] = 0.6 if index % 2 else 0.6000000000000001
    throttle = [max(0.0, 1.0 - c - b) for c, b in zip(load, brake)]
    lat0, lon0 = 42.3369, -71.5136
    latitude = [lat0 + 0.004 * _wave(value) for value in pct]
    longitude = [lon0 + 0.006 * _wave(value + 0.25) for value in pct]

    def series(scale: float, phase: float, shift: float = 0.0) -> list[float]:
        return [shift + scale * _wave(phase + 0.059 * index) for index in range(count)]

    channels: dict[str, tuple[int, str, list[Any]]] = {
        "SessionTime": (F64, "s", time),
        "SessionNum": (I32, "", [1] * count),
        "Lap": (I32, "", lap),
        "LapDistPct": (F64, "%", pct),
        "Speed": (F64, "m/s", speed),
        "Throttle": (F64, "%", throttle),
        "Brake": (F64, "%", brake),
        "OnPitRoad": (BOOL, "", [index < 8 for index in range(count)]),
        "SessionFlags": (U32, "irsdk_Flags", [0x0004] * count),
        "SteeringWheelAngle": (F64, "rad", steer),
        "SteeringWheelAngleMax": (F64, "rad", [8.37758] * count),
        "SteeringWheelTorque": (F64, "N*m", series(4.0, 0.1)),
        "Gear": (I32, "", gear),
        "RPM": (F64, "revs/min", series(900.0, 0.2, 6500.0)),
        "YawRate": (F64, "rad/s", series(0.4, 0.3)),
        "VelocityX": (F64, "m/s", vx),
        "VelocityY": (F64, "m/s", vy),
        "LatAccel": (F64, "m/s^2", lat_g),
        "LongAccel": (F64, "m/s^2", series(6.0, 0.5)),
        "Lat": (F64, "deg", latitude),
        "Lon": (F64, "deg", longitude),
        "Clutch": (F64, "%", [1.0 if index % 50 == 0 else 0.0 for index in range(count)]),
        "BrakeABSactive": (BOOL, "", [bool(index % 7 == 0) for index in range(count)]),
        "BrakeABScutPct": (F64, "%", [((index * 13) % 100) / 100.0 for index in range(count)]),
        "dcBrakeBias": (F64, "%", [54.2] * count),
        "VertAccel": (F64, "G", series(0.3, 0.6, 1.0)),
        "Pitch": (F64, "deg", pitch),
        "Roll": (F64, "rad", series(0.02, 0.7)),
        "PitchRate": (F64, "°/s", series(0.1, 0.8)),
        "RollRate": (F64, "deg/s", series(0.1, 0.9)),
        "LFspeed": (F64, "km/h", [s * 3.6 * 1.01 for s in speed]),
        "RFspeed": (F64, "mph", [s * 2.236936 * 0.99 for s in speed]),
        "LRspeed": (F64, "kph", [s * 3.6 * 1.3 for s in speed]),
        "RRspeed": (F64, "m/s", [s * 3.5 for s in speed]),  # beyond +/-200 %: gaps
        "LFpressure": (F64, "kPa", series(5.0, 1.0, 180.0)),
        "RFpressure": (F64, "bar", series(0.05, 1.1, 1.8)),
        "LRpressure": (F64, "Pa", series(5000.0, 1.2, 175000.0)),
        "RRpressure": (F64, "psi", series(0.7, 1.3, 26.0)),
        "LFcoldPressure": (F64, "kPa", [172.0] * count),
        "LFtempCL": (F64, "C", series(3.0, 1.4, 80.0)),
        "LFtempCM": (F64, "°C", series(3.0, 1.5, 82.0)),
        "LFtempCR": (F64, "C", series(3.0, 1.6, 84.0)),
        "RFtempCL": (F64, "K", series(3.0, 1.7, 355.0)),
        "LRtempCM": (F64, "F", series(5.0, 1.8, 180.0)),
        "RRtempL": (F64, "C", series(2.0, 1.9, 70.0)),
        "RRtempM": (F64, "C", series(2.0, 2.0, 72.0)),
        "RRtempR": (F64, "deg C", series(2.0, 2.1, 74.0)),
        "FuelLevel": (F64, "l", [60.0 - index * 0.002 for index in range(count)]),
        "FuelUsePerHour": (F64, "kg/h", series(3.0, 2.2, 40.0)),
        "CFSRrideHeight": (F64, "cm", series(0.4, 2.3, 4.0)),
        "LFrideHeight": (F64, "m", series(0.004, 2.4, 0.06)),
        "RFrideHeight": (F64, "mm", series(4.0, 2.5, 60.0)),
        "LRrideHeight": (F64, "in", series(0.2, 2.6, 2.5)),
        "RRrideHeight": (F64, "ft", series(0.02, 2.7, 0.2)),
        "LFshockDefl": (F64, "m", series(0.01, 2.8, 0.05)),
        "LFshockVel": (F64, "m/s", series(0.1, 2.9)),
        "RFshockVel": (F64, "mm/s", series(100.0, 3.0)),
        "LRshockDefl": (F64, "in", series(0.3, 3.1, 1.5)),
        "RRshockVel": (F64, "in/s", series(3.0, 3.2)),
        "TrackTempCrew": (F64, "C", series(0.5, 3.3, 41.0)),
        "AirTemp": (F64, "K", series(0.2, 3.4, 300.0)),
        "WindVel": (F64, "km/h", series(2.0, 3.5, 12.0)),
        "RelativeHumidity": (F64, "%", [0.55] * count),
        "FogLevel": (F64, "%", [0.0] * count),
        "Precipitation": (F64, "%", [0.0] * count),
        "AirPressure": (F64, "Pa", series(50.0, 3.6, 101300.0)),
        "AirDensity": (F64, "kg/m^3", series(0.01, 3.7, 1.2)),
        "TrackWetness": (I32, "irsdk_TrackWetness", [1 + (index // 300) for index in range(count)]),
        "TrackUsage": (F64, "%", [0.3] * count),
        "WeatherDeclaredWet": (BOOL, "", [False] * count),
        "PlayerCarPosition": (I32, "", [3 - (index // 400) for index in range(count)]),
        "PlayerCarClassPosition": (I32, "", [2] * count),
        "CarDistAhead": (F64, "m", [(-0.0 if index % 5 == 0 else 12.5 + index % 3) for index in range(count)]),
        "CarDistBehind": (F64, "m", [7.25] * count),
        "PlayerTrackSurface": (I32, "irsdk_TrkLoc", [3] * count),
    }
    setup = {"Chassis": {"Front": {"SteeringRatio": "12:1", "SteeringOffset": "+6 deg"}}}
    sectors = [
        {"SectorNum": 0, "SectorStartPct": 0.0},
        {"SectorNum": 1, "SectorStartPct": 0.333333},
        {"SectorNum": 2, "SectorStartPct": "0.666667"},
        {"SectorNum": 3, "SectorStartPct": 0.333333},
        {"SectorNum": 4, "SectorStartPct": 1.0},
        {"SectorNum": 5, "SectorStartPct": False},
        "not a sector",
    ]
    return _table(base, channels, car_setup=setup, sectors=sectors)


def gaps(base: dict[str, Any]) -> dict[str, Any]:
    lap, pct = _laps([320, 300], start_lap=0)
    count = len(lap)
    nan, inf = math.nan, math.inf
    pct = list(pct)
    pct[50] = nan
    pct[51:54] = [1.5, -0.5, inf]
    load = [_corner(value % 1.0) if math.isfinite(value) else 0.0 for value in pct]
    speed = [50.0 - 20.0 * c for c in load]
    speed[70] = nan
    speed[71] = inf
    speed[72] = -inf
    steer = [0.5 * c for c in load]
    steer[200] = nan
    lat_g = [12.0 * c for c in load]
    lat_g[300] = nan
    flags = [0x0004] * count
    flags[100:110] = [0x0200] * 10  # one-lap-to-green outside a race: cleared
    flags[110:120] = [0x4000] * 10  # caution: excluded from the profile
    flags[120:125] = [0x0208] * 5  # yellow plus one-lap-to-green: kept as caution
    clutch_text = ["0.5", " 12 ", "nan", "abc", "1_0", "inf", "", "-0.0", "1e400", "0x10"]
    wet_text = ["yes", "no", " ON ", "0", "true", "", "False", "1"]
    channels: dict[str, tuple[int, str, list[Any]]] = {
        "SessionTime": (F64, "s", [index / RATE for index in range(count)]),
        "SessionNum": (I32, "", [1] * count),
        "Lap": (I32, "", lap),
        "LapDistPct": (F64, "%", pct),
        "Speed": (F64, "m/s", speed),
        "Throttle": (TEXT, "%", [f"{0.25 + 0.5 * (index % 3)}" if index % 97 else "n/a" for index in range(count)]),
        "Brake": (F64, "%", [nan if index % 41 == 0 else 0.2 * c for index, c in enumerate(load)]),
        "OnPitRoad": (BOOL, "", [560 <= index < 600 for index in range(count)]),
        "SessionFlags": (U32, "irsdk_Flags", flags),
        "SteeringWheelAngle": (F64, "rad", steer),
        "SteeringWheelAngleMax": (F64, "rad", [8.0 if index < 300 else 9.0 for index in range(count)]),
        "Gear": (TEXT, "", [str(3 + index % 2) if index % 13 else "N" for index in range(count)]),
        "YawRate": (F64, "rad/s", [inf if index == 33 else 0.2 * c for index, c in enumerate(load)]),
        "VelocityX": (F64, "m/s", [nan if index == 34 else s for index, s in enumerate(speed)]),
        "VelocityY": (F64, "m/s", [0.8 * c for c in load]),
        "LatAccel": (F64, "m/s^2", lat_g),
        "Lat": (F64, "deg", [nan if index % 9 == 0 else 40.0 + 0.001 * index for index in range(count)]),
        "Lon": (F64, "deg", [-80.0 - 0.001 * index for index in range(count)]),
        "Clutch": (TEXT, "%", [clutch_text[index % len(clutch_text)] for index in range(count)]),
        "BrakeABSactive": (I32, "", [index % 3 for index in range(count)]),
        "FuelLevel": (F64, "gal", [nan] * count),
        "AirTemp": (F64, "C", [1e308 if index % 4 == 0 else 20.0 for index in range(count)]),
        "TrackTemp": (F64, "C", [inf if index % 4 == 0 else 30.0 for index in range(count)]),
        "AirPressure": (F64, "Pa", [-0.0 if index % 2 else nan for index in range(count)]),
        "WeatherDeclaredWet": (TEXT, "", [wet_text[index % len(wet_text)] for index in range(count)]),
        "CarDistAhead": (F64, "m", [nan if index % 2 else -0.0 for index in range(count)]),
        "LFtempCL": (F64, "C", [nan if index % 3 == 0 else 80.0 for index in range(count)]),
        "LFtempCM": (F64, "C", [inf if index % 5 == 0 else 81.0 for index in range(count)]),
        "LFrideHeight": (F64, "m", [nan if index % 6 == 0 else 0.05 for index in range(count)]),
        "LFshockVel": (F64, "m/s", [-inf if index % 8 == 0 else 0.1 for index in range(count)]),
        "LFpressure": (F64, "kPa", [1e308 if index % 10 == 0 else 180.0 for index in range(count)]),
        "PlayerTrackSurface": (I32, "irsdk_TrkLoc", [3] * count),
    }
    # A stated ratio with a lock change mid-recording: steering-wheel basis, "changed".
    setup = {"Chassis": {"Front": {"SteeringRatio": "14:1"}}}
    return _table(base, channels, session_type="Practice", car_setup=setup)


def no_distance(base: dict[str, Any]) -> dict[str, Any]:
    lap, _pct = _laps([300])
    count = len(lap)
    channels: dict[str, tuple[int, str, list[Any]]] = {
        "SessionTime": (F64, "s", [index / RATE for index in range(count)]),
        "Lap": (I32, "", lap),
        "Speed": (F64, "m/s", [40.0] * count),
        "Throttle": (F64, "%", [1.0] * count),
        "Brake": (F64, "%", [0.0] * count),
    }
    # An unreadable ratio: steering-wheel basis, "unreadable".
    return _table(base, channels, car_setup={"Chassis": {"Front": {"SteeringRatio": "12:2"}}})


BUILDERS: tuple[tuple[str, Callable[[dict[str, Any]], dict[str, Any]]], ...] = (
    ("traces-roadwheel", roadwheel),
    ("traces-gaps", gaps),
    ("traces-no-distance", no_distance),
)

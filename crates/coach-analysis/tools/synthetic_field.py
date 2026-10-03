"""Synthetic tables for unit U5 (field geometry and groove).

The fixture has no Lat/Lon, so both entry points stop at their first check
there. Each builder here replaces the fixture's samples with a simulated
session at 10 Hz on a closed track, shaped to reach the branches of
``groove_analysis.analyze_groove_evolution`` and
``race_foundations.build_track_geometry``:

* ``field-oval``: a short oval driven counter-clockwise for two runs split by a
  four-tyre stop (pit entry, stall, odometer reset, a left-front wear reading
  measured at the stop, exit), a caution lap and its restart lap, a last lap
  whose final sample reads 1.005, a line that drifts outward in run 1 and inward
  in run 2 (migration detected both ways, oval low/high mapping), a reference
  gap of two bins filled by interpolation, a zone-lap with under five samples
  and a run-zone with under four laps (slow samples), NaN, placeholder (0, 0)
  and beyond-the-pole GPS samples, a far GPS sentinel, positions outside the
  lap-distance range and a "short oval" track type.
* ``field-road``: the same plan driven clockwise with the line mid-corner (a
  zone wrapping start/finish) and a chicane on the back straight (right-hand
  calibration, a chicane that fails the gates), no OnPitRoad channel, two
  odometers in km (filtered), "degrees" and " M " unit spellings, a road-course
  track type, one run and an alternating line (the first-sustained-lap
  fallback).
* ``field-gap``: a lap-distance band with no GPS on any lap (groove: a reference
  gap over four bins; geometry: an open main loop) and pit road recorded on the
  far side of the track (entry and exit paths rejected for their jump).
* ``field-tiny``: a 10 m circle with distant outlier samples (groove: span
  plausible, reconstructed length not) and a pit episode 2 km away (every pit
  layer outside the main bounds).
* ``field-span``: a 12 m circle (groove: implausible span).
* ``field-pit-only``: the circle on pit road from the first sample to the last
  (no profile, so no zones; geometry: no main path, pit layers rejected).
* ``field-units``: Lat in radians (groove: unsafe projection); constant GPS
  (geometry: zero normalisation scale).
* ``field-km``: every odometer in km (groove: fewer than two odometers), Lon as
  text with unparsable samples, pit episodes with a single valid point and one
  stopped (identical points: no commitment or merge line).
* ``field-straight``: no steering, lateral acceleration or brake at a speed that
  fills every profile bin, so one segment covers the lap and no zone survives.
* ``field-caution``: caution over 60 % of every lap (groove: zones but no valid
  green laps).
* ``field-two-laps``: two green laps (groove: fewer than three reference laps).
* ``field-coverage``: a band with no GPS too wide for the reference (groove:
  bin coverage under 90 %), SessionTime as text, and samples each row filter
  drops on valid laps (pit road under 1 s, a caution flag, under 5 m/s, fewer
  than two usable odometers).
* ``field-few``: four short laps at 2 Hz with no finite latitude (groove: too few
  samples; geometry: no point to normalise).

Trigonometry here uses only IEEE arithmetic (a Taylor series), never the C
library, so the decoded tables are bit-identical on Windows and Linux and any
platform difference in the vectors comes from the oracle's own primitives.
"""

from __future__ import annotations

import random
from typing import Any, Callable

RATE = 10
TEXT, BOOL, I32, U32, F32, F64 = 0, 1, 2, 3, 4, 5
_TYPE_NAMES = {TEXT: "char", F32: "float", F64: "double", I32: "int", BOOL: "bool", U32: "bitfield"}
_BYTE_SIZE = {TEXT: 1, F32: 4, F64: 8, I32: 4, BOOL: 1, U32: 4}
EARTH_RADIUS_M = 6_371_008.8
PI = 3.141592653589793
GREEN, YELLOW, CAUTION_WAVING = 0x0004, 0x0100, 0x4000
LAT0, LON0 = 43.3628, -71.4606
TIRES = ("LF", "RF", "LR", "RR")


def _sin_cos(angle: float) -> tuple[float, float]:
    """sin and cos by quadrant reduction and Taylor series: IEEE arithmetic only."""

    quarter = PI / 2.0
    k = round(angle / quarter)
    r = angle - k * quarter
    r2 = r * r
    s, c, term_s, term_c = r, 1.0, r, 1.0
    for n in range(1, 12):
        term_s = -term_s * r2 / ((2 * n) * (2 * n + 1))
        term_c = -term_c * r2 / ((2 * n - 1) * (2 * n))
        s += term_s
        c += term_c
    return ((s, c), (c, -s), (-s, -c), (-c, s))[k % 4]


def _f32(value: float) -> float:
    import struct

    return struct.unpack("<f", struct.pack("<f", value))[0]


class Track:
    """A closed path: straights of ``straight`` m joined by arcs, optional chicane."""

    def __init__(self, straight: float, radius: float, *, clockwise: bool = False, chicane: float = 0.0,
                 zones: tuple[tuple[float, float], ...] | None = None, origin: float = 0.0):
        self.origin = origin
        self.straight = straight
        self.radius = radius
        self.clockwise = clockwise
        self.chicane = chicane
        self.zones = zones
        self.turn = PI * radius
        self.length = 2.0 * straight + 2.0 * self.turn

    def _ccw(self, d: float) -> tuple[float, float, float, float, float]:
        """Point, left normal and corner load at ``d`` m counter-clockwise from mid front straight."""

        s, r = self.straight, self.radius
        d = (d + s / 2.0) % self.length
        if d < s:
            return d - s / 2.0, -r, 0.0, 1.0, 0.0
        d -= s
        if d < self.turn:
            sin_t, cos_t = _sin_cos(-PI / 2.0 + d / r)
            return s / 2.0 + r * cos_t, r * sin_t, -cos_t, -sin_t, self._bump(d / self.turn)
        d -= self.turn
        if d < s:
            x, y = s / 2.0 - d, r
            if self.chicane:
                # An S bend on the back straight: out and back over its middle third.
                u = (d - s / 3.0) / (s / 3.0)
                if 0.0 <= u <= 1.0:
                    sin_u, _ = _sin_cos(2.0 * PI * u)
                    return x, y + self.chicane * sin_u, 0.0, -1.0, 0.8 * self._bump(2.0 * u % 1.0)
            return x, y, 0.0, -1.0, 0.0
        d -= s
        sin_t, cos_t = _sin_cos(PI / 2.0 + d / r)
        return -s / 2.0 + r * cos_t, r * sin_t, -cos_t, -sin_t, self._bump(d / self.turn)

    @staticmethod
    def _bump(u: float) -> float:
        """Corner load over a corner's fraction ``u``: 0.2 at the ends, 1 at the apex."""

        sin_u, _ = _sin_cos(PI * u)
        return 0.2 + 0.8 * sin_u

    def at(self, d: float) -> tuple[float, float, float, float, float]:
        d = d + self.origin * self.length
        if not self.clockwise:
            return self._ccw(d)
        x, y, nx, ny, k = self._ccw(self.length - (d % self.length))
        return x, y, -nx, -ny, k

    def load(self, pct: float) -> float:
        if self.zones is not None:
            return 1.0 if any(start <= pct < end for start, end in self.zones) else 0.0
        return self.at(pct * self.length)[4]

    def corner(self, pct: float) -> bool:
        return self.load(pct) > 0.0


def _lat_lon(x: float, y: float) -> tuple[float, float]:
    _, cos_lat = _sin_cos(LAT0 * PI / 180.0)
    lat = LAT0 + (y / EARTH_RADIUS_M) * (180.0 / PI)
    lon = LON0 + (x / (EARTH_RADIUS_M * cos_lat)) * (180.0 / PI)
    return lat, lon


def _table(base: dict[str, Any], channels: dict[str, tuple[int, str, list[Any]]], *,
           track_type: str | None, session_type: str = "Race", rate: int = RATE) -> dict[str, Any]:
    """The fixture's decoded mapping with its samples replaced by ``channels``."""

    count = len(next(iter(channels.values()))[2])
    variables, samples, offset = [], {}, 0
    for name, (type_code, unit, values) in channels.items():
        if len(values) != count:
            raise ValueError(f"{name}: {len(values)} values for {count} samples")
        variables.append({
            "name": name, "description": f"Synthetic {name}", "unit": unit,
            "type": _TYPE_NAMES[type_code], "type_code": type_code, "offset": offset,
            "count": 1, "count_as_time": False, "byte_size": _BYTE_SIZE[type_code],
        })
        offset += _BYTE_SIZE[type_code]
        if type_code == F32:
            values = [_f32(value) for value in values]
        elif type_code == F64:
            values = [float(value) for value in values]
        elif type_code == BOOL:
            values = [bool(value) for value in values]
        elif type_code == TEXT:
            values = [str(value) for value in values]
        else:
            values = [int(value) for value in values]
        samples[name] = values
    telemetry = base
    telemetry["samples"] = samples
    telemetry["variables"] = variables
    telemetry["available_variables"] = [dict(variable) for variable in variables]
    telemetry["sample_indices"] = list(range(count))
    telemetry["sample_count"] = count
    telemetry["source_record_count"] = count
    telemetry["native_tick_rate_hz"] = rate
    telemetry["sample_rate_hz"] = float(rate)
    telemetry["channel_selection"] = {"mode": "selected", "available_count": len(variables),
                                      "decoded_count": len(variables)}
    provenance = telemetry["sample_provenance"][0]
    telemetry["sample_provenance"] = [dict(provenance, native_record_index=index) for index in range(count)]
    metadata = telemetry["metadata"]
    metadata["record_count"] = count
    metadata["sample_rate"] = float(rate)
    metadata["tick_rate"] = rate
    metadata["header"]["tick_rate"] = rate
    metadata["header"]["num_variables"] = len(variables)
    metadata["header"]["buffer_length"] = offset
    metadata["sim_session_type"] = session_type
    metadata["is_race"] = session_type == "Race"
    session_info = telemetry["session_info"]
    weekend = session_info.setdefault("WeekendInfo", {})
    if track_type is None:
        weekend.pop("TrackType", None)
    else:
        weekend["TrackType"] = track_type
    session_info["SessionInfo"] = {"Sessions": [{"SessionNum": 1, "SessionType": session_type}]}
    return telemetry


def _session(
    base: dict[str, Any],
    track: Track,
    *,
    laps: int,
    seed: int,
    rate: int = RATE,
    track_type: str | None = "short oval",
    pit_after: tuple[int, ...] = (),
    caution: Callable[[int, float], bool] = lambda lap, pct: False,
    drift: Callable[[int, int, float], float] = lambda run, age, pct: 0.0,
    partial_last: float = 0.0,
    units: dict[str, str] | None = None,
    include: tuple[str, ...] | None = None,
    exclude: tuple[str, ...] = (),
    edit: Callable[[dict[str, list[Any]], list[int], list[float]], None] | None = None,
    speed_scale: float = 1.0,
    extra: dict[str, tuple[int, str]] | None = None,
) -> dict[str, Any]:
    """Simulate ``laps`` laps (and a partial one) and return the edited fixture table.

    ``drift(run, laps_on_tyres, pct)`` is the lateral offset (m, positive left of
    travel) of the driven line; a pit stop after lap N puts the car through pit
    road from 0.90 of lap N to 0.10 of lap N + 1 with a 25 s stall at the line.
    """

    rng = random.Random(seed)
    dt = 1.0 / rate
    length = track.length
    columns: dict[str, list[Any]] = {name: [] for name in (
        "SessionTime", "SessionNum", "Lap", "LapDistPct", "Lat", "Lon", "Speed", "SessionFlags",
        "OnPitRoad", "PlayerCarInPitStall", "FuelLevel", "SteeringWheelAngle", "LatAccel",
        "Brake", "Throttle", *(f"{tire}odometer" for tire in TIRES),
    )}
    odometer = [0.0, 0.0, 0.0, 0.0]
    scale = (0.995, 1.005, 0.997, 1.003)
    fuel = 60.0
    distance = 0.0  # along the lap, from the line
    lap = 1
    run = 0
    tyre_start_lap = 1
    stall_left = 0
    pit_lap = None
    time_index = 0
    total = laps + partial_last
    while lap + distance / length <= total + 1.0 and lap <= laps + 1:
        if lap > laps and distance / length >= partial_last:
            break
        pct = distance / length
        in_pit = pit_lap is not None and (
            (lap == pit_lap and pct >= 0.90) or (lap == pit_lap + 1 and pct < 0.10)
        )
        under_caution = caution(lap, pct)
        corner = track.corner(pct)
        speed = (30.0 if under_caution else (40.0 if corner else 50.0)) * speed_scale
        if in_pit:
            speed = 20.0
        stalled = False
        if pit_lap is not None and lap == pit_lap + 1 and stall_left > 0 and pct >= 0.0:
            stalled = True
            speed = 0.0
            stall_left -= 1
            if stall_left == 120:
                odometer = [0.0, 0.0, 0.0, 0.0]
                tyre_start_lap = lap
            fuel += 0.2
        x, y, nx, ny, _k = track.at(distance)
        age = lap - tyre_start_lap
        offset = drift(run, age, pct) + (rng.random() - 0.5) * 0.12
        if in_pit:
            # Pit lane 18 m inside the front straight, entered and left over 0.04 of a lap.
            ramp = min(1.0, (pct - 0.90) / 0.04) if pct >= 0.90 else min(1.0, (0.10 - pct) / 0.04)
            offset = 18.0 * ramp
        lat, lon = _lat_lon(x + nx * offset, y + ny * offset)
        load = track.load(pct)
        steer = 0.11 * load * (-1.0 if track.clockwise else 1.0) + (rng.random() - 0.5) * 0.004
        lateral = speed * speed / track.radius * load + (rng.random() - 0.5) * 0.2
        brake = 0.35 if (not corner and track.corner((pct + 0.03) % 1.0)) else 0.0
        columns["SessionTime"].append(time_index * dt)
        columns["SessionNum"].append(1)
        columns["Lap"].append(lap)
        columns["LapDistPct"].append(pct)
        columns["Lat"].append(lat)
        columns["Lon"].append(lon)
        columns["Speed"].append(speed)
        columns["SessionFlags"].append((YELLOW | CAUTION_WAVING) if under_caution else GREEN)
        columns["OnPitRoad"].append(in_pit)
        columns["PlayerCarInPitStall"].append(stalled)
        columns["FuelLevel"].append(fuel)
        columns["SteeringWheelAngle"].append(steer)
        columns["LatAccel"].append(lateral)
        columns["Brake"].append(brake)
        columns["Throttle"].append(0.0 if brake or stalled else (0.6 if corner else 1.0))
        for tire in range(4):
            columns[f"{TIRES[tire]}odometer"].append(odometer[tire])
        step = speed * dt
        for tire in range(4):
            odometer[tire] += step * scale[tire]
        fuel -= step * 0.00025
        time_index += 1
        distance += step
        if distance >= length:
            distance -= length
            lap += 1
            if lap in [after + 1 for after in pit_after]:
                run += 1
        if lap in pit_after and pit_lap != lap and distance / length >= 0.85:
            pit_lap = lap
            stall_left = 250
    count = len(columns["Lap"])
    if edit is not None:
        edit(columns, columns["Lap"], columns["LapDistPct"])
    unit_of = {
        "SessionTime": "s", "SessionNum": "", "Lap": "", "LapDistPct": "%", "Lat": "deg", "Lon": "deg",
        "Speed": "m/s", "SessionFlags": "irsdk_Flags", "OnPitRoad": "", "PlayerCarInPitStall": "",
        "FuelLevel": "l", "SteeringWheelAngle": "rad", "LatAccel": "m/s^2", "Brake": "%", "Throttle": "%",
        **{f"{tire}odometer": "m" for tire in TIRES},
    }
    unit_of.update(units or {})
    types = {
        "SessionTime": F64, "SessionNum": I32, "Lap": I32, "LapDistPct": F32, "Lat": F64, "Lon": F64,
        "Speed": F32, "SessionFlags": U32, "OnPitRoad": BOOL, "PlayerCarInPitStall": BOOL, "FuelLevel": F32,
        "SteeringWheelAngle": F32, "LatAccel": F32, "Brake": F32, "Throttle": F32,
        **{f"{tire}odometer": F32 for tire in TIRES},
    }
    for name, (type_code, unit) in (extra or {}).items():
        types[name] = type_code
        unit_of[name] = unit
    names = [name for name in columns if (include is None or name in include) and name not in exclude]
    assert all(len(columns[name]) == count for name in names)
    return _table(base, {name: (types[name], unit_of[name], columns[name]) for name in names},
                  track_type=track_type, rate=rate)


def _oval_drift(run: int, age: int, pct: float) -> float:
    corner = (0.21 <= pct < 0.48) or (0.71 <= pct < 0.98)
    if not corner:
        return 0.0
    # Run 1 climbs (right of travel), run 2 settles down (left); lap 3 of run 1 spikes back.
    if run == 0:
        return -0.0 if age == 2 else -0.22 * age
    return 0.18 * age


def field_oval(base: dict[str, Any]) -> dict[str, Any]:
    track = Track(400.0, 143.0)

    def edit(columns: dict[str, list[Any]], laps: list[int], pct: list[float]) -> None:
        count = len(laps)
        lat, lon = columns["Lat"], columns["Lon"]
        nan = float("nan")
        for index in range(count):
            position = pct[index]
            # Two bins (of 120) with no GPS on any lap: the reference fills them.
            if 0.600 <= position < 0.6166:
                lat[index] = nan
            if index % 997 == 13:
                lon[index] = nan
        stalls = [index for index, stalled in enumerate(columns["PlayerCarInPitStall"]) if stalled]
        for index in stalls[40:42]:
            lat[index], lon[index] = 0.0, 0.0  # GPS placeholder: rejected
        lat[stalls[60]] += 2.0  # a finite sentinel 220 km away: outside the cluster
        lat[stalls[61]] = 95.0  # beyond the pole
        pct[5] = -0.02  # outside [-0.01, 1.01]: no candidate
        pct[6] = -0.005  # inside: wraps to 0.995
        pct[count - 1] = 1.005
        # Too slow to count: most of zone 1 on lap 2 (a bucket under five samples) and all of
        # zone 2 on lap 14 (run 2 keeps three laps there, under the four a comparison needs).
        slow = [index for index in range(count) if laps[index] == 2 and 0.10 <= pct[index] < 0.40]
        keep = [index for index in slow if 0.2 <= pct[index]][:3]
        for index in slow:
            if index not in keep:
                columns["Speed"][index] = 3.0
        for index in range(count):
            if laps[index] == 14 and 0.55 <= pct[index] < 0.95:
                columns["Speed"][index] = 3.0
        # Left-front wear: a fresh reading appears with the tyre change (measured at the stop).
        reset = stalls[130]
        for band, worn in (("L", 0.93), ("M", 0.91), ("R", 0.95)):
            columns[f"LFwear{band}"] = [1.0 if index < reset else worn for index in range(count)]

    wear = {f"LFwear{band}": (F32, "%") for band in "LMR"}
    return _session(base, track, laps=14, seed=5, pit_after=(7,),
                    caution=lambda lap, pct: lap == 11,
                    drift=_oval_drift, partial_last=0.4, edit=edit, extra=wear)


def field_road(base: dict[str, Any]) -> dict[str, Any]:
    # The line sits in the middle of a corner, so a load zone wraps start/finish.
    track = Track(420.0, 120.0, clockwise=True, chicane=14.0, origin=0.33)

    # Laps 3 to 8 alternate on and off the early line, ending 1.0 then 0.4 m left: the
    # late median moves past the threshold with no two consecutive shifted laps, so the
    # first sustained lap falls back to the first late lap.
    pattern = (0.0, 0.0, 1.0, 0.0, 1.0, 0.0, 1.0, 0.4)

    def drift(run: int, age: int, pct: float) -> float:
        return pattern[min(age, 7)] if track.corner(pct) else 0.0

    return _session(
        base, track, laps=8, seed=11, track_type="Road Course", drift=drift,
        exclude=("OnPitRoad", "PlayerCarInPitStall"),
        units={"Lat": "degrees", "Lon": " Degree ", "LFodometer": " M ", "LRodometer": "km", "RRodometer": "km"},
    )


def field_gap(base: dict[str, Any]) -> dict[str, Any]:
    def edit(columns: dict[str, list[Any]], laps: list[int], pct: list[float]) -> None:
        lat, lon = columns["Lat"], columns["Lon"]
        for index, position in enumerate(pct):
            if 0.40 <= position < 0.45:
                lat[index] = float("nan")
            if columns["OnPitRoad"][index]:
                # Pit road recorded on the far side of the track: inside the main bounds,
                # but the entry and exit paths jump across the infield.
                lat[index], lon[index] = 2.0 * LAT0 - lat[index], 2.0 * LON0 - lon[index]

    return _session(base, Track(300.0, 100.0), laps=5, seed=17, edit=edit, pit_after=(3,))


def _circle(base: dict[str, Any], radius: float, *, seed: int, outliers: bool = False,
            pit_everywhere: bool = False) -> dict[str, Any]:
    track = Track(0.0, radius, zones=((0.2, 0.45), (0.7, 0.95)))

    def edit(columns: dict[str, list[Any]], laps: list[int], pct: list[float]) -> None:
        if outliers:
            for index in range(7, len(pct), 251):
                columns["Lat"][index] += 90.0 / EARTH_RADIUS_M * (180.0 / PI)
            # A pit-road episode 2 km away: outside the main path's bounds.
            for index in range(300, 331):
                columns["OnPitRoad"][index] = True
                columns["Lat"][index] += 2000.0 / EARTH_RADIUS_M * (180.0 / PI)
        if pit_everywhere:
            columns["OnPitRoad"] = [True] * len(pct)

    return _session(base, track, laps=8, seed=seed, edit=edit, speed_scale=0.13)


def field_tiny(base: dict[str, Any]) -> dict[str, Any]:
    return _circle(base, 10.0, seed=23, outliers=True)


def field_span(base: dict[str, Any]) -> dict[str, Any]:
    return _circle(base, 12.0, seed=29)


def field_pit_only(base: dict[str, Any]) -> dict[str, Any]:
    return _circle(base, 12.0, seed=31, pit_everywhere=True)


def field_units(base: dict[str, Any]) -> dict[str, Any]:
    def edit(columns: dict[str, list[Any]], laps: list[int], pct: list[float]) -> None:
        columns["Lat"] = [LAT0] * len(pct)
        columns["Lon"] = [LON0] * len(pct)
        columns["OnPitRoad"] = [index < 40 or 300 <= index < 340 for index in range(len(pct))]

    return _session(base, Track(100.0, 40.0), laps=3, seed=31, edit=edit,
                    units={"Lat": "rad", "Lon": "rad"})


def field_km(base: dict[str, Any]) -> dict[str, Any]:
    def edit(columns: dict[str, list[Any]], laps: list[int], pct: list[float]) -> None:
        nan = float("nan")
        pit = columns["OnPitRoad"]
        lat, lon = columns["Lat"], columns["Lon"]
        for index in range(len(pct)):
            pit[index] = 100 <= index < 104 or 150 <= index < 170 or 220 <= index < 240
            if 101 <= index < 104 or 151 <= index < 170:
                lat[index] = nan
            if 220 <= index < 240:
                # Stopped on pit road: identical points, so no commitment or merge line.
                lat[index], lon[index] = lat[220], lon[220]
        # Longitude as text, as a char channel decodes: float() per sample, a few unparsable.
        text = [repr(value) for value in lon]
        for index, bad in ((10, "abc"), (11, " nan"), (12, "1e999"), (13, ""), (14, " 1_0 ")):
            text[index] = bad
        text[20] = f" {lon[20]!r}\t"
        columns["Lon"] = text

    return _session(base, Track(100.0, 40.0), laps=3, seed=37, edit=edit,
                    units={f"{tire}odometer": "km" for tire in TIRES}, extra={"Lon": (TEXT, "deg")})


def field_straight(base: dict[str, Any]) -> dict[str, Any]:
    return _session(base, Track(100.0, 40.0), laps=4, seed=41, speed_scale=0.3,
                    exclude=("SteeringWheelAngle", "LatAccel", "Brake"))


def field_caution(base: dict[str, Any]) -> dict[str, Any]:
    return _session(base, Track(100.0, 40.0), laps=4, seed=43,
                    caution=lambda lap, pct: pct < 0.6)


def field_two_laps(base: dict[str, Any]) -> dict[str, Any]:
    return _session(base, Track(150.0, 60.0), laps=2, seed=47, partial_last=0.0)


def field_coverage(base: dict[str, Any]) -> dict[str, Any]:
    def edit(columns: dict[str, list[Any]], laps: list[int], pct: list[float]) -> None:
        nan = float("nan")
        for index, position in enumerate(pct):
            if 0.30 <= position < 0.45:
                columns["Lat"][index] = nan
        # Samples the row filters drop on valid laps.
        for index in range(140, 145):
            columns["OnPitRoad"][index] = True  # under 1 s on pit road: the lap stays valid
        for index in range(160, 165):
            columns["SessionFlags"][index] = YELLOW
        for index in range(180, 184):
            columns["Speed"][index] = 3.0
        for index in range(200, 204):
            for tire in TIRES[1:]:
                columns[f"{tire}odometer"][index] = nan
        columns["LFodometer"][205] = -1.0
        # SessionTime as text: float() per sample, with surrounding whitespace on some.
        text = [repr(value) for value in columns["SessionTime"]]
        for index in range(0, len(text), 7):
            text[index] = f" {text[index]}\n"
        columns["SessionTime"] = text

    return _session(base, Track(100.0, 40.0), laps=4, seed=59, edit=edit,
                    extra={"SessionTime": (TEXT, "s")})


def field_few(base: dict[str, Any]) -> dict[str, Any]:
    def edit(columns: dict[str, list[Any]], laps: list[int], pct: list[float]) -> None:
        columns["Lat"] = [float("nan")] * len(pct)

    return _session(base, Track(100.0, 40.0), laps=4, seed=53, rate=2, edit=edit)


BUILDERS: tuple[tuple[str, Callable[[dict[str, Any]], dict[str, Any]]], ...] = (
    ("field-oval", field_oval),
    ("field-road", field_road),
    ("field-gap", field_gap),
    ("field-tiny", field_tiny),
    ("field-span", field_span),
    ("field-pit-only", field_pit_only),
    ("field-units", field_units),
    ("field-km", field_km),
    ("field-straight", field_straight),
    ("field-caution", field_caution),
    ("field-two-laps", field_two_laps),
    ("field-coverage", field_coverage),
    ("field-few", field_few),
)

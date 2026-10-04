"""Synthetic Offline Testing session recorded as two stints (two IBT files).

iRacing starts a new file each time the driver gets back in the car. The two
files of one Test Drive share the session clock: each file's disk header holds
its own wall-clock start (``sessionStartDate``, whole seconds) and the
``SessionTime`` then (``sessionStartTime``), so date minus time is the same
session start in both, to the second. The lap counter carries on across the
stints (real Test Drives and hosted practices show it: laps 93-99, then 111-114
of one IRP Test Drive; laps 0-19, then 19-51 of one practice).

Stint 1 leaves the stall on lap 0, runs laps 1-4 and parks on lap 5. The
driver is out of the car for :data:`GARAGE_GAP_S` of session time; stint 2
leaves the stall still on lap 5, runs laps 6-9 and parks on lap 10. Laps 2-4
and 7-9 are full green laps; the out-laps, in-laps and lap 5 (which spans the
garage visit) are not. Nothing here is private race data: the track, car and
driver are made up, and the builder is deterministic, so every platform writes
the same bytes.
"""

from __future__ import annotations

import math
import os
import struct
from pathlib import Path

HEADER = struct.Struct("<28i")
DISK_SUBHEADER = struct.Struct("<qddii")
VAR_HEADER = struct.Struct("<iiiB3x32s64s32s")

TICK_RATE = 60
TRACK_LENGTH_M = 600.0
#: Wall-clock time (Unix seconds) at which the sim session's clock started.
SESSION_ZERO = 1_791_100_000.0
#: SessionTime when the driver first gets in the car.
STINT_1_CLOCK = 120.25
#: Session time the driver spends in the garage between the stints.
GARAGE_GAP_S = 95.5
STALL_PCT = 0.97
PIT_ENTRY_PCT = 0.92
PIT_EXIT_PCT = 0.06
PIT_LIMIT_MPS = 20.0
#: Start/finish crossings after leaving the stall before the in-lap begins.
FLYING_LAPS = 4
#: A fixed modification time well in the past, so the 2 s quiet rule passes everywhere.
FIXED_MTIME = 1_780_000_000

SESSION_INFO = """WeekendInfo:
  TrackName: synthetic test oval
  TrackID: 9101
  TrackLength: 0.60 km
  TrackDisplayName: Synthetic Test Oval
  TrackDisplayShortName: Test Oval
  TrackConfigName: Oval
  TrackCity: Synthetic
  TrackCountry: USA
  TrackNumTurns: 2
  TrackType: short oval
  SeriesID: 0
  SeasonID: 0
  SessionID: 0
  SubSessionID: 0
  LeagueID: 0
  Official: 0
  RaceWeek: 0
  EventType: Test
  Category: Oval
  SimMode: full
  WeekendOptions:
    NumStarters: 1
    IsFixedSetup: 0
SessionInfo:
  Sessions:
    - SessionNum: 0
      SessionLaps: unlimited
      SessionTime: unlimited
      SessionType: Offline Testing
      SessionName: TESTING
DriverInfo:
  DriverCarIdx: 0
  DriverUserID: 4242
  DriverSetupName: synthetic test.sto
  DriverSetupIsModified: 0
  DriverCarFuelMaxLtr: 68.000
  Drivers:
    - CarIdx: 0
      UserName: Synthetic Tester
      UserID: 4242
      CarID: 9102
      CarPath: synthetic test car
      CarScreenName: Synthetic Test Car
      CarClassID: 0
CarSetup:
  UpdateCount: 1
  Tires:
    LeftFront:
      ColdPressure: 165.0 kPa
    RightFront:
      ColdPressure: 190.0 kPa
"""

# (name, type code, unit); the record holds them in this order.
VARIABLES = (
    ("SessionTime", 5, "s"),
    ("SessionNum", 2, ""),
    ("SessionUniqueID", 2, ""),
    ("SessionState", 2, "irsdk_SessionState"),
    ("SessionFlags", 3, "irsdk_Flags"),
    ("Lap", 2, ""),
    ("LapCompleted", 2, ""),
    ("LapDistPct", 4, "%"),
    ("LapDist", 4, "m"),
    ("LapLastLapTime", 4, "s"),
    ("LapCurrentLapTime", 4, "s"),
    ("Speed", 4, "m/s"),
    ("Throttle", 4, "%"),
    ("Brake", 4, "%"),
    ("SteeringWheelAngle", 4, "rad"),
    ("LatAccel", 4, "m/s^2"),
    ("LongAccel", 4, "m/s^2"),
    ("RPM", 4, "revs/min"),
    ("Gear", 2, ""),
    ("FuelLevel", 4, "l"),
    ("OnPitRoad", 1, ""),
    ("PlayerCarInPitStall", 1, ""),
    ("PlayerTrackSurface", 2, "irsdk_TrkLoc"),
    ("IsOnTrack", 1, ""),
)
_FORMATS = {1: "?", 2: "i", 3: "I", 4: "f", 5: "d"}
_RECORD = struct.Struct("<" + "".join(_FORMATS[code] for _name, code, _unit in VARIABLES))
#: iRacing holds "one lap to green" through a whole test session.
_TEST_FLAGS = 0x10000200


def _align(value: int, alignment: int = 16) -> int:
    return (value + alignment - 1) // alignment * alignment


def _fixed(value: str, length: int) -> bytes:
    encoded = value.encode("utf-8")
    return encoded + b"\x00" * (length - len(encoded))


def _track_speed(pct: float, lap: int) -> float:
    # Two corners per lap; each lap a little different, deterministically.
    return 46.0 - 9.0 * abs(math.sin(2.0 * math.pi * pct)) + 0.15 * math.sin(1.7 * lap)


def _drive_stint(clock: float, lap: int, fuel: float) -> tuple[list[tuple], float, int, float]:
    """Samples of one stint from the stall back to the stall: (records, end clock, end lap, fuel)."""

    dt = 1.0 / TICK_RATE
    first_clock = clock
    pct = STALL_PCT
    pit_lane = True
    speed = 0.0
    completed = lap - 1
    lap_start: float | None = None
    last_lap = 0.0
    flying_left = FLYING_LAPS
    phase = "stall-out"
    hold = 1.5
    records: list[tuple] = []
    while len(records) < 60 * TICK_RATE * 10:
        clock = first_clock + len(records) / TICK_RATE
        in_stall = phase in {"stall-out", "stall-in"}
        on_pit = pit_lane
        if in_stall:
            target = 0.0
        elif phase == "in-lap" and pct >= 0.5:
            # Brake for the pit limit at the entry, then for the stall.
            to_entry = (PIT_ENTRY_PCT - pct) * TRACK_LENGTH_M
            to_stall = (STALL_PCT - pct) * TRACK_LENGTH_M
            target = min(
                _track_speed(pct, lap),
                math.sqrt(PIT_LIMIT_MPS ** 2 + 2.0 * 8.0 * max(0.0, to_entry)),
                math.sqrt(max(0.0, 2.0 * 5.0 * to_stall)),
            )
            if pct >= PIT_ENTRY_PCT:
                target = min(target, PIT_LIMIT_MPS)
        elif on_pit:
            target = PIT_LIMIT_MPS
        else:
            target = _track_speed(pct, lap)
        accel = max(-11.0, min(4.5, (target - speed) * TICK_RATE))
        new_speed = max(0.0, speed + accel * dt)
        throttle = 0.0 if in_stall else min(1.0, max(0.0, 0.55 + accel / 9.0))
        brake = 0.4 if in_stall else min(1.0, max(0.0, -accel / 11.0))
        curvature = abs(math.sin(2.0 * math.pi * pct)) / 60.0
        records.append(
            (
                clock, 0, 1, 4, _TEST_FLAGS, lap, completed, pct, pct * TRACK_LENGTH_M,
                last_lap, 0.0 if lap_start is None else clock - lap_start,
                speed, throttle, brake, 0.9 * curvature * 60.0 / 8.0,
                speed * speed * curvature, accel, 1200.0 + 110.0 * speed,
                0 if speed < 0.5 else 1 if speed < 15.0 else 2 if speed < 30.0 else 3,
                fuel, on_pit or in_stall, in_stall, 1 if (on_pit or in_stall) else 3, True,
            )
        )
        if in_stall:
            hold -= dt
            if hold <= 0.0:
                if phase == "stall-in":
                    return records, first_clock + len(records) / TICK_RATE, lap, fuel
                phase = "out-lap"
            continue
        speed = new_speed
        fuel -= speed * dt * 0.00045
        pct += speed * dt / TRACK_LENGTH_M
        clock = first_clock + len(records) / TICK_RATE
        if pct >= 1.0:
            pct -= 1.0
            if lap_start is not None:
                last_lap = clock - lap_start
            lap += 1
            completed += 1
            lap_start = clock
            if phase == "out-lap":
                phase = "flying"
            elif phase == "flying":
                flying_left -= 1
                if flying_left == 0:
                    phase = "in-lap"
        # Pit road runs from the entry, past the stall and the line, to the exit.
        if pit_lane and phase != "in-lap" and PIT_EXIT_PCT <= pct < 0.5:
            pit_lane = False
        if phase == "in-lap" and pct >= PIT_ENTRY_PCT:
            pit_lane = True
        if phase == "in-lap" and pct >= STALL_PCT - 0.0005 and speed < 0.6:
            pct = STALL_PCT
            speed = 0.0
            phase = "stall-in"
            hold = 1.5
    raise RuntimeError("the synthetic stint never returned to its stall")


def _write_ibt(path: Path, records: list[tuple], session_info: str = SESSION_INFO) -> None:
    yaml_blob = session_info.encode("utf-8") + b"\x00"
    session_info_offset = HEADER.size + DISK_SUBHEADER.size
    variable_header_offset = _align(session_info_offset + len(yaml_blob))
    sample_offset = _align(variable_header_offset + len(VARIABLES) * VAR_HEADER.size)
    buffer_length = _align(_RECORD.size, 4)
    output = bytearray(sample_offset + len(records) * buffer_length)
    HEADER.pack_into(
        output, 0,
        2, 1, TICK_RATE, 1, len(yaml_blob), session_info_offset,
        len(VARIABLES), variable_header_offset, 1, buffer_length, 0, 0,
        len(records) - 1, sample_offset, 0, 0, *([0] * 12),
    )
    first_clock, last_clock = records[0][0], records[-1][0]
    # sessionStartDate is the file's wall-clock start in whole seconds.
    DISK_SUBHEADER.pack_into(
        output, HEADER.size,
        int(SESSION_ZERO + first_clock), first_clock, last_clock, 1, len(records),
    )
    output[session_info_offset:session_info_offset + len(yaml_blob)] = yaml_blob
    offset = 0
    for index, (name, code, unit) in enumerate(VARIABLES):
        VAR_HEADER.pack_into(
            output, variable_header_offset + index * VAR_HEADER.size,
            code, offset, 1, 0, _fixed(name, 32), _fixed(name, 64), _fixed(unit, 32),
        )
        offset += struct.calcsize("<" + _FORMATS[code])
    for index, record in enumerate(records):
        _RECORD.pack_into(output, sample_offset + index * buffer_length, *record)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(output))
    os.utime(path, (FIXED_MTIME, FIXED_MTIME))


def stint_names() -> tuple[str, str]:
    """iRacing's file names for the two stints (car, track and wall-clock start)."""

    import datetime

    def name(clock: float) -> str:
        moment = datetime.datetime.fromtimestamp(int(SESSION_ZERO + clock), tz=datetime.timezone.utc)
        return f"synthetic test car_synthetic test oval {moment:%Y-%m-%d %H-%M-%S}.ibt"

    second_clock = _stint_clocks()[1]
    return name(STINT_1_CLOCK), name(second_clock)


def _stint_clocks() -> tuple[float, float]:
    _records, end_clock, _lap, _fuel = _drive_stint(STINT_1_CLOCK, 0, 40.0)
    return STINT_1_CLOCK, end_clock + GARAGE_GAP_S


#: The left-front cold pressure of :data:`SESSION_INFO`'s setup.
BASELINE_LF_COLD = "165.0 kPa"


def session_info_with(lf_cold: str = BASELINE_LF_COLD, update_count: int = 1, corner_weight: str | None = None) -> str:
    """:data:`SESSION_INFO` with another left-front cold pressure (a setup change), another
    ``UpdateCount`` or a ``CornerWeight`` leaf (derived values that are no setup change)."""

    text = SESSION_INFO.replace(f"ColdPressure: {BASELINE_LF_COLD}", f"ColdPressure: {lf_cold}", 1)
    text = text.replace("  UpdateCount: 1\n", f"  UpdateCount: {update_count}\n", 1)
    if corner_weight is not None:
        text = text.replace(
            f"ColdPressure: {lf_cold}\n", f"ColdPressure: {lf_cold}\n      CornerWeight: {corner_weight}\n", 1
        )
    return text


def build_offline_stints(
    directory: Path,
    *,
    second_session_info: str = SESSION_INFO,
    first_lap: int = 0,
) -> list[Path]:
    """Write the two stints into ``directory``; return them in recording order.

    ``second_session_info`` replaces the second stint's session info (another
    setup); ``first_lap`` is the sim's ``Lap`` when the first stint leaves the
    stall (1024 and above: the counter has stopped there; see
    ``workflow.OFFLINE_LAP_COUNTER_LIMIT``). The defaults write the bytes the
    module docstring describes.
    """

    first, end_clock, lap, fuel = _drive_stint(STINT_1_CLOCK, first_lap, 40.0)
    second, _end, _lap, _fuel = _drive_stint(end_clock + GARAGE_GAP_S, lap, fuel)
    paths = [directory / name for name in stint_names()]
    for path, records, info in zip(paths, (first, second), (SESSION_INFO, second_session_info)):
        _write_ibt(path, records, info)
    return paths

"""Writes a synthetic SDK replay fixture (`.sdkfix`) for the dev-PC end to end.

The file format and map layout are the collector's (`collector/src/fixture.rs`): magic `IRCSDKF1`,
then `u8 kind, u32 length, payload` records (1 static, 2 tick, 3 status). The map carries the
variables of `MapBuilder::engine_vars` with the same natural alignment, three buffers, and a 16 KiB
session-info region.

Timeline at 60 Hz (seconds of replay time):
  0 to 1     in session, in the pit stall, off track
  1 to 91    on track, three laps of about 30 s (live tiles, on-track upload gate)
  91 to 94   pit road, then off track
  94 to 110  garage visit: CarSetup.UpdateCount bumps every 3 s
  110        garage exit; 0.5 s later DriverSetupName changes (P5 two-moment rule)
  110 to END off track in the stall (uploads allowed)
  END        the sim quits (status 0)

The driver's customer id and name are arguments so no identity is stored in the repository.

Usage: python make_sdkfix.py OUT.sdkfix --customer-id N --name "First Last"
       [--start-setup NAME.sto] [--exit-setup NAME.sto] [--end-s 170]
"""

from __future__ import annotations

import argparse
import math
import struct

HEADER_LEN = 112
VAR_HEADER_LEN = 144
SESSION_CAPACITY = 16 * 1024
NUM_BUF = 3
TICK_RATE = 60

CHAR, BOOL, INT, BITFIELD, FLOAT, DOUBLE = 0, 1, 2, 3, 4, 5
WIDTH = {CHAR: 1, BOOL: 1, INT: 4, BITFIELD: 4, FLOAT: 4, DOUBLE: 8}

ENGINE_VARS = [
    ("SessionTime", DOUBLE, 1, "s"),
    ("SessionNum", INT, 1, ""),
    ("SessionUniqueID", INT, 1, ""),
    ("SessionFlags", BITFIELD, 1, "irsdk_Flags"),
    ("Lap", INT, 1, ""),
    ("LapDistPct", FLOAT, 1, "%"),
    ("LapLastLapTime", FLOAT, 1, "s"),
    ("SessionLapsRemainEx", INT, 1, ""),
    ("SessionLapsRemain", INT, 1, ""),
    ("Speed", FLOAT, 1, "m/s"),
    ("Throttle", FLOAT, 1, "%"),
    ("Brake", FLOAT, 1, "%"),
    ("SteeringWheelAngle", FLOAT, 1, "rad"),
    ("Gear", INT, 1, ""),
    ("RPM", FLOAT, 1, "revs/min"),
    ("YawRate", FLOAT, 1, "rad/s"),
    ("LatAccel", FLOAT, 1, "m/s^2"),
    ("LongAccel", FLOAT, 1, "m/s^2"),
    ("Lat", DOUBLE, 1, "deg"),
    ("Lon", DOUBLE, 1, "deg"),
    ("FuelLevel", FLOAT, 1, "l"),
    ("FuelLevelPct", FLOAT, 1, "%"),
    ("TrackTemp", FLOAT, 1, "C"),
    ("AirTemp", FLOAT, 1, "C"),
    ("dcBrakeBias", FLOAT, 1, ""),
    ("OnPitRoad", BOOL, 1, ""),
    ("IsOnTrack", BOOL, 1, ""),
    ("IsInGarage", BOOL, 1, ""),
    ("PlayerCarIdx", INT, 1, ""),
    ("PlayerCarPosition", INT, 1, ""),
    ("PlayerCarClassPosition", INT, 1, ""),
    ("PlayerCarMyIncidentCount", INT, 1, "x"),
    ("PlayerCarTowTime", FLOAT, 1, "s"),
    ("CarIdxPosition", INT, 64, ""),
    ("CarIdxClassPosition", INT, 64, ""),
    ("CarIdxLap", INT, 64, ""),
    ("CarIdxF2Time", FLOAT, 64, "s"),
    ("CarIdxLastLapTime", FLOAT, 64, "s"),
]

CAR_PATH = "stockcars2 supra2019"
LAP_S = 30.4
OPPONENTS = [(1, "Car Two", 0.0, 30.25), (2, "Car Three", 0.0, 30.31), (3, "Car Four", 0.0, 30.52)]


class Map:
    def __init__(self) -> None:
        self.vars = []
        row = 0
        for name, ty, count, unit in ENGINE_VARS:
            w = WIDTH[ty]
            offset = -(-row // w) * w
            row = offset + w * count
            self.vars.append((name, ty, count, unit, offset))
        self.row_len = -(-row // 16) * 16
        self.index = {v[0]: v for v in self.vars}
        self.var_off = HEADER_LEN
        self.session_off = self.var_off + len(self.vars) * VAR_HEADER_LEN
        self.buf0 = -(-(self.session_off + SESSION_CAPACITY) // 16) * 16
        self.map_len = self.buf0 + NUM_BUF * self.row_len

    def static_region(self, status: int, update: int, session_info: str) -> bytes:
        b = bytearray(self.session_off + SESSION_CAPACITY)
        text = session_info.encode("utf-8")
        if len(text) + 1 > SESSION_CAPACITY:
            raise ValueError("session info exceeds the map's capacity")
        struct.pack_into("<10i", b, 0, 2, status, TICK_RATE, update, len(text) + 1, self.session_off,
                         len(self.vars), self.var_off, NUM_BUF, self.row_len)
        for k in range(NUM_BUF):
            struct.pack_into("<ii", b, 48 + 16 * k, 0, self.buf0 + k * self.row_len)
        for i, (name, ty, count, unit, offset) in enumerate(self.vars):
            at = self.var_off + i * VAR_HEADER_LEN
            struct.pack_into("<iii", b, at, ty, offset, count)
            b[at + 16:at + 16 + len(name)] = name.encode("ascii")
            b[at + 112:at + 112 + len(unit)] = unit.encode("ascii")
        b[self.session_off:self.session_off + len(text)] = text
        return bytes(b)

    def row(self, values: dict) -> bytes:
        b = bytearray(self.row_len)
        for name, value in values.items():
            _, ty, count, _, offset = self.index[name]
            if count > 1:
                fmt = "<i" if ty in (INT, BITFIELD) else "<f"
                for idx, v in value.items():
                    struct.pack_into(fmt, b, offset + 4 * idx, v)
            elif ty == DOUBLE:
                struct.pack_into("<d", b, offset, float(value))
            elif ty == FLOAT:
                struct.pack_into("<f", b, offset, float(value))
            elif ty in (INT, BITFIELD):
                struct.pack_into("<i" if ty == INT else "<I", b, offset, int(value))
            else:
                b[offset] = 1 if value else 0
        return bytes(b)


def session_yaml(customer_id: int, name: str, setup_name: str, modified: bool, update_count: int,
                 pressure_kpa: int) -> str:
    drivers = [f" - CarIdx: 0\n   UserName: {name}\n   UserID: {customer_id}\n   CarPath: {CAR_PATH}\n"
               f"   CarScreenName: Toyota Supra\n   IRating: 1650\n"]
    for idx, opponent, _, _ in OPPONENTS:
        drivers.append(f" - CarIdx: {idx}\n   UserName: {opponent}\n   UserID: {900000 + idx}\n"
                       f"   CarPath: {CAR_PATH}\n   CarScreenName: Toyota Supra\n   IRating: 1500\n")
    return (
        "---\n"
        "WeekendInfo:\n TrackName: newhampshire oval\n TrackDisplayName: New Hampshire Motor Speedway\n"
        " TrackConfigName: Oval\n TrackID: 30\n SubSessionID: 0\n WeekendOptions:\n  IsFixedSetup: 0\n"
        "SessionInfo:\n Sessions:\n - SessionNum: 0\n   SessionType: Offline Testing\n   SessionName: TESTING\n"
        f"DriverInfo:\n DriverCarIdx: 0\n DriverSetupName: {setup_name}\n"
        f" DriverSetupIsModified: {1 if modified else 0}\n DriverSetupLoadTypeName: user\n Drivers:\n"
        + "".join(drivers)
        + f"CarSetup:\n UpdateCount: {update_count}\n Chassis:\n  Front:\n   SteeringRatio: 16.5:1\n"
        f" Tires:\n  LeftFront:\n   ColdPressure: {pressure_kpa} kPa\n  RightFront:\n   ColdPressure: {pressure_kpa + 20} kPa\n"
        "...\n"
    )


class Writer:
    def __init__(self, path: str) -> None:
        self.f = open(path, "wb")
        self.f.write(b"IRCSDKF1")

    def record(self, kind: int, payload: bytes) -> None:
        self.f.write(struct.pack("<BI", kind, len(payload)))
        self.f.write(payload)

    def static(self, m: Map, status: int, update: int, info: str) -> None:
        self.record(1, struct.pack("<I", m.map_len) + m.static_region(status, update, info))

    def tick(self, t_ms: int, status: int, update: int, tick: int, row: bytes) -> None:
        self.record(2, struct.pack("<Iiiii", t_ms, status, TICK_RATE, update, tick) + row)

    def status(self, t_ms: int, status: int) -> None:
        self.record(3, struct.pack("<Ii", t_ms, status))

    def close(self) -> None:
        self.f.close()


def driving(t: float, start: float) -> dict:
    """Car state at replay time t (seconds); the car leaves the stall at `start`."""
    run = max(0.0, t - start)
    laps_done = int(run // LAP_S)
    pct = (run % LAP_S) / LAP_S
    corner = 0.5 + 0.5 * math.cos(4 * math.pi * pct)  # two corners per lap
    speed = 72.0 - 14.0 * corner
    values = {
        "Lap": 1 + laps_done,
        "LapDistPct": pct,
        "Speed": speed,
        "Throttle": 1.0 - 0.55 * corner,
        "Brake": 0.25 if 0.12 < (pct * 2) % 1 < 0.16 else 0.0,
        "SteeringWheelAngle": 0.18 * corner,
        "Gear": 4,
        "RPM": 6200.0 + 900.0 * (1 - corner),
        "YawRate": 0.25 * corner,
        "LatAccel": 18.0 * corner,
        "LongAccel": -2.0 * corner,
        "Lat": 43.3627 + 0.002 * math.sin(2 * math.pi * pct),
        "Lon": -71.4606 + 0.003 * math.cos(2 * math.pi * pct),
        "FuelLevel": 70.0 - 0.105 * run,
        "FuelLevelPct": (70.0 - 0.105 * run) / 70.0,
        "IsOnTrack": True,
    }
    if laps_done >= 1:
        values["LapLastLapTime"] = LAP_S + 0.03 * (laps_done % 3)
    positions = {0: 3}
    for i, (idx, _, _, lap_s) in enumerate(OPPONENTS):
        positions[idx] = [1, 2, 4][i]
    values["CarIdxPosition"] = positions
    values["CarIdxClassPosition"] = dict(positions)
    values["CarIdxLap"] = {0: 1 + laps_done, 1: 1 + laps_done, 2: 1 + laps_done, 3: 1 + laps_done}
    values["CarIdxF2Time"] = {0: 0.62 + 0.01 * laps_done, 1: 0.0, 2: 0.31, 3: 1.4}
    if laps_done >= 1:
        values["CarIdxLastLapTime"] = {0: values["LapLastLapTime"], 1: 30.25, 2: 30.31, 3: 30.52}
    values["PlayerCarPosition"] = 3
    values["PlayerCarClassPosition"] = 3
    return values


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("out")
    ap.add_argument("--customer-id", type=int, required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--start-setup", default="e2e baseline.sto")
    ap.add_argument("--exit-setup", default="e2e garage capture.sto")
    ap.add_argument("--end-s", type=float, default=170.0)
    a = ap.parse_args()

    m = Map()
    w = Writer(a.out)
    update = 1
    info = session_yaml(a.customer_id, a.name, a.start_setup, False, 2, 152)
    w.static(m, 1, update, info)

    track_start, pit_in, off_track, garage_in, garage_out = 1.0, 91.0, 94.0, 96.0, 110.0
    bumps = [99.0, 102.0, 105.0, 108.0]  # UpdateCount 3..6 while in the garage
    exit_static = garage_out + 0.5         # count 7 and the new name arrive after the exit
    count = 2
    pressure = 152
    frames = int(a.end_s * TICK_RATE)
    session_time0 = 500.0
    for i in range(frames):
        t = i / TICK_RATE
        if bumps and t >= bumps[0]:
            bumps.pop(0)
            count += 1
            pressure += 2
            update += 1
            w.static(m, 1, update, session_yaml(a.customer_id, a.name, a.start_setup, False, count, pressure))
        if exit_static is not None and t >= exit_static:
            exit_static = None
            count += 1
            update += 1
            w.static(m, 1, update, session_yaml(a.customer_id, a.name, a.exit_setup, False, count, pressure))
        base = {
            "SessionTime": session_time0 + t,
            "SessionNum": 0,
            "SessionUniqueID": 1,
            "SessionFlags": 0x4,
            "SessionLapsRemainEx": 32767,
            "SessionLapsRemain": 32767,
            "TrackTemp": 31.5,
            "AirTemp": 22.0,
            "dcBrakeBias": 0.56,
            "PlayerCarIdx": 0,
            "PlayerCarMyIncidentCount": 0,
            "Gear": 0,
            "FuelLevel": 70.0,
            "FuelLevelPct": 1.0,
        }
        if track_start <= t < pit_in:
            base.update(driving(t, track_start))
        elif pit_in <= t < off_track:
            base.update(driving(pit_in - 0.01, track_start))
            base.update({"OnPitRoad": True, "Speed": max(0.0, 20.0 * (off_track - t)), "Throttle": 0.1,
                         "IsOnTrack": True})
        else:
            base.update({"IsInGarage": garage_in <= t < garage_out, "IsOnTrack": False, "Speed": 0.0})
        w.tick(round(t * 1000), 1, update, 1000 + i, m.row(base))
    w.status(round(a.end_s * 1000), 0)
    w.close()
    print(f"{a.out}: {frames} ticks, map {m.map_len} bytes, row {m.row_len} bytes, session updates {update}")


if __name__ == "__main__":
    main()

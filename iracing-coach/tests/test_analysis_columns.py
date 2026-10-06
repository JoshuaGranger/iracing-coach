"""Column-at-a-time analysis must equal the per-sample formulation it replaced.

The cold-analysis rewrite computes each recorded column once and does the
per-element work in C where it can. These tests run the rewritten functions
beside verbatim copies of the pre-change functions (``*_reference.py``,
generated from commit 9077917) on adversarial synthetic telemetry - gaps, NaN,
infinities, signed zeros, integers, booleans and text in numeric channels,
lap wrap, partial and non-contiguous laps, pit, caution, tow and repair
episodes, unit variants and missing channels - and require identical results:
same types, same float bits (so -0.0 differs from 0.0), same dict key order,
and the same set of channels recorded as analysed.
"""

from __future__ import annotations

import functools
import gc
import itertools
import math
import pickle
import random
import statistics
import struct
import sys
import unittest
from operator import add, mul, sub, truediv
from pathlib import Path
from typing import Any

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN_ROOT / "skills" / "analyze-iracing-race" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import analysis_engine as engine  # noqa: E402
import analysis_engine_reference as reference  # noqa: E402
import ibt_reader  # noqa: E402
import race_foundations  # noqa: E402
import race_foundations_reference as foundations_reference  # noqa: E402
import workflow  # noqa: E402

NAN = float("nan")
INF = float("inf")


def assert_identical(test: unittest.TestCase, expected: Any, actual: Any, where: str = "$") -> None:
    """Equal in type, float bits, container shape and dict key order.

    Pickles record exactly those properties, so equal pickles settle it
    quickly; otherwise (a real difference, or merely different object sharing)
    the structural walk decides and names the first differing path.
    """

    if where == "$" and pickle.dumps(expected, protocol=5) == pickle.dumps(actual, protocol=5):
        return
    _assert_identical_walk(test, expected, actual, where)


def _assert_identical_walk(test: unittest.TestCase, expected: Any, actual: Any, where: str) -> None:
    assert_identical = _assert_identical_walk  # recurse without the pickle shortcut
    test.assertIs(type(actual), type(expected), f"{where}: type")
    if isinstance(expected, float):
        if math.isnan(expected):
            test.assertTrue(math.isnan(actual), f"{where}: {expected!r} != {actual!r}")
        else:
            test.assertEqual(struct.pack("<d", expected), struct.pack("<d", actual), f"{where}: {expected!r} != {actual!r}")
    elif isinstance(expected, dict):
        test.assertEqual(list(expected), list(actual), f"{where}: keys or key order")
        for key in expected:
            assert_identical(test, expected[key], actual[key], f"{where}.{key}")
    elif isinstance(expected, (list, tuple)):
        test.assertEqual(len(expected), len(actual), f"{where}: length")
        for index, (left, right) in enumerate(zip(expected, actual)):
            assert_identical(test, left, right, f"{where}[{index}]")
    elif isinstance(expected, set):
        test.assertEqual(expected, actual, where)
    else:
        test.assertEqual(expected, actual, where)


def outcome(function: Any, *args: Any) -> Any:
    """The result, or the exception type: a helper must fail exactly as the original did."""

    try:
        return function(*args)
    except Exception as error:  # noqa: BLE001 - compared, not handled
        return type(error)


SPECIAL_VALUES = (None, NAN, INF, -INF, -0.0, 0.0, "junk", "12.5", " 3 ", True, False, 7)


def noisy(rng: random.Random, values: list[Any], rate: float = 0.03) -> list[Any]:
    return [rng.choice(SPECIAL_VALUES) if rng.random() < rate else value for value in values]


def f32(value: float) -> float:
    """Round like a recorded float32 sample."""

    return struct.unpack("<f", struct.pack("<f", value))[0]


def synthetic_race(seed: int, *, variant: str = "full") -> dict[str, Any]:
    """A deliberately messy recording: every edge the rewrite must preserve."""

    rng = random.Random(seed)
    per_lap = 100
    count = per_lap * 7 + per_lap * 97 // 180  # a partial final lap

    def at(sample: int) -> int:
        # Event positions are written for a 1,357-sample race and scaled.
        return sample * count // 1357

    start_pct = 0.4  # a partial first lap
    channels: dict[str, list[Any]] = {}
    units: dict[str, str] = {}

    def channel(name: str, values: list[Any], unit: str = "", *, noise: float = 0.03) -> None:
        channels[name] = noisy(rng, values, noise) if noise else values
        if unit:
            units[name] = unit

    lap_numbers: list[Any] = []
    pct: list[Any] = []
    for index in range(count):
        position = start_pct + index / per_lap
        lap_numbers.append(int(position) - (1 if index < at(25) else 0))  # a pre-race lap -1
        pct.append(f32(position % 1.0))
    # Lap wrap: the lap counter moves a sample before/after LapDistPct wraps.
    for boundary in range(per_lap, count, per_lap):
        cut = boundary - int(start_pct * per_lap)
        if 2 < cut < count - 2:
            pct[cut] = 0.998
            pct[cut + 1] = 0.0015
            pct[cut - 1] = -0.004  # slightly negative is still a valid wrap
    # A non-contiguous lap: a stray sample repeats an earlier lap number.
    lap_numbers[3 * per_lap + 50] = lap_numbers[3 * per_lap + 50] - 1
    for index, value in ((at(40), 1.004), (at(40) + 1, 1.5), (at(40) + 2, -0.5), (at(40) + 3, NAN), (at(40) + 4, None)):
        pct[index] = value
    channel("Lap", lap_numbers, noise=0.002)
    channel("LapDistPct", pct, "%", noise=0.01)
    channel("LapCompleted", [max(0, value - 1) for value in lap_numbers], noise=0.01)

    time_value = 1000.0
    times: list[Any] = []
    for index in range(count):
        step = 1.0 / 60.0
        if index == at(600):
            step = 3.0  # a gap beyond the dt window
        if index == at(700):
            step = -0.5  # a backwards step
        time_value += step
        times.append(time_value)
    channel("SessionTime", times, "s", noise=0.005)

    pit = [at(900) <= index < at(1010) for index in range(count)]
    caution = [at(300) <= index < at(420) for index in range(count)]
    speed = [
        f32((12.0 if pit[index] else 48.0 + 9.0 * math.sin(index / 13.0)) * (0.3 if index < at(30) else 1.0))
        for index in range(count)
    ]
    speed[at(100)] = -0.0
    speed[at(100) + 1] = -2.5
    speed[at(100) + 2] = 10.0  # exactly the slip threshold
    channel("Speed", [int(value) if variant == "int-speed" else value for value in speed], "km/h" if variant == "units" else "m/s")

    def controls(offset: float) -> list[float]:
        values = [f32(max(0.0, math.sin(index / 7.0 + offset))) for index in range(count)]
        values[at(200) : at(200) + 5] = [1.0, 1.5, 55.0, -0.0, 0.08]
        return values

    channel("Throttle", controls(0.0))
    channel("Brake", controls(1.7))
    channel("SteeringWheelAngle", [f32(0.3 * math.sin(index / 5.0)) if index % 97 else -0.0 for index in range(count)], "rad")
    channel("LatAccel", [f32(12.0 * math.sin(index / 9.0)) for index in range(count)], "m/s^2")
    channel("LongAccel", [f32(3.0 * math.cos(index / 11.0)) for index in range(count)], "m/s^2")
    channel("YawRate", [f32(0.4 * math.sin(index / 6.0)) for index in range(count)], "rad/s")
    channel("PitchRate", [f32(0.05 * math.sin(index / 4.0)) for index in range(count)], "rad/s")
    channel("RollRate", [f32(0.07 * math.cos(index / 4.0)) for index in range(count)], "rad/s")
    channel("VelocityX", [value if index % 211 else 0.4 for index, value in enumerate(speed)], "m/s")
    channel("VelocityY", [f32(1.2 * math.sin(index / 8.0)) for index in range(count)], "m/s")
    channel("Gear", [3 + (index // 50) % 2 for index in range(count)])
    channel("RPM", [f32(6000.0 + 900.0 * math.sin(index / 10.0)) for index in range(count)], "revs/min")
    channel("SteeringWheelTorque", [f32(8.0 * math.sin(index / 3.0)) for index in range(count)], "N*m")
    if variant != "no-gps":
        # A GPS placeholder, a far-away sentinel and the dominant local track.
        lat = [f32(43.36 + 0.004 * math.sin(2 * math.pi * (value if isinstance(value, float) and math.isfinite(value) else 0.0))) for value in pct]
        lon = [f32(-71.46 + 0.006 * math.cos(2 * math.pi * (value if isinstance(value, float) and math.isfinite(value) else 0.0))) for value in pct]
        lat[at(500)], lon[at(500)] = 0.0, 0.0
        lat[at(500) + 1], lon[at(500) + 1] = 10.0, 60.0
        channel("Lat", lat, "deg", noise=0.005)
        channel("Lon", lon, "deg", noise=0.005)

    flags: list[Any] = []
    for index in range(count):
        bits = 0x0004 if index % 400 < 3 else 0
        if caution[index]:
            bits |= 0x4000 if index % 2 else 0x0008
        if index > count - at(60):
            bits |= 0x0001
        if count - at(250) < index <= count - at(60):
            bits |= 0x0002
        if at(450) <= index < at(450) + 6:
            bits |= 0x00010000 | 0x00100000
        flags.append(bits)
    channel("SessionFlags", flags, noise=0.01)
    channel("SessionState", [4 if at(20) <= index < count - at(40) else (5 if index >= count - at(40) else 3) for index in range(count)], noise=0.01)
    pit_values = [("true" if value else "false") if variant == "text-pit" else value for value in pit]
    channel("OnPitRoad", pit_values, noise=0.0)
    channel("PlayerCarInPitStall", [at(930) <= index < at(960) for index in range(count)], noise=0.0)
    channel("PitstopActive", [at(932) <= index < at(958) for index in range(count)], noise=0.0)
    channel("PlayerTrackSurface", [1 if at(930) <= index < at(960) else (2 if pit[index] else (0 if index % 311 == 0 else 3)) for index in range(count)], noise=0.01)
    channel("CarDistAhead", [f32(10.0 + 60.0 * abs(math.sin(index / 30.0))) for index in range(count)], "m")
    channel("CarDistBehind", [f32(5.0 + 40.0 * abs(math.cos(index / 25.0))) for index in range(count)], "m")
    channel("PlayerCarPosition", [5 - (index // at(300)) for index in range(count)], noise=0.01)
    channel("PlayerCarClassPosition", [3 - (index // at(500)) for index in range(count)], noise=0.01)
    fuel = [f32(60.0 - index * 0.03 + (40.0 if index >= at(940) else 0.0)) for index in range(count)]
    channel("FuelLevel", fuel, "gal" if variant == "units" else "l", noise=0.01)
    channel("FuelUsePerHour", [f32(30.0 + math.sin(index)) for index in range(count)], "kg/h")
    lap_times = [0.0] * count
    for index in range(count):
        lap_times[index] = 29.9 + 0.01 * (index // per_lap) if index > per_lap else 0.0
    channel("LapLastLapTime", lap_times, "s", noise=0.01)
    if variant != "no-wheels":
        for offset, tire in enumerate(engine.TIRES):
            ratio = [0.0] * count
            for index in range(count):
                phase = math.sin(index / (5.0 + offset))
                ratio[index] = -0.3 if index % 157 == offset else (2.5 if index % 263 == offset else 0.12 * phase)
            wheel = [f32(value * (1.0 + factor)) if isinstance(value, float) else value for value, factor in zip(speed, ratio)]
            channel(f"{tire}speed", wheel, "m/s")
            odometer = [f32(index * 1.1 + offset) for index in range(count)]
            odometer[at(950):] = [f32((index - at(950)) * 1.1) for index in range(at(950), count)]
            channel(f"{tire}odometer", odometer, "m")
            channel(f"{tire}TiresUsed", [0 if index < at(960) else 1 for index in range(count)], noise=0.0)
    channel("BrakeABSactive", [index % 17 == 0 for index in range(count)], noise=0.0)
    channel("BrakeABScutPct", [f32((index % 17) / 17.0) for index in range(count)], "%")
    channel("dcBrakeBias", [52.5 if index < at(700) else 53.0 for index in range(count)], "%")
    channel("Clutch", [1.0 if index % 100 else 0.0 for index in range(count)], "%")
    channel("VertAccel", [f32(9.8 + math.sin(index / 2.0)) for index in range(count)], "g" if variant == "units" else "m/s^2")
    channel("Pitch", [f32(0.01 * math.sin(index / 20.0)) for index in range(count)], "deg" if variant == "units" else "rad")
    channel("Roll", [f32(0.02 * math.cos(index / 20.0)) for index in range(count)], "rad")

    temperature_unit = {"units": "K", "fahrenheit": "F"}.get(variant, "C")
    temperature_offset = 273.15 if temperature_unit == "K" else 0.0
    channel("AirTemp", [f32(22.0 + temperature_offset + index * 0.001) for index in range(count)], temperature_unit)
    channel("TrackTempCrew", [f32(35.0 + temperature_offset + index * 0.002) for index in range(count)], temperature_unit)
    channel("WindVel", [f32(3.0 + math.sin(index / 50.0)) for index in range(count)], "m/s")
    channel("WindDir", [f32(1.0 + 0.1 * math.sin(index / 60.0)) for index in range(count)], "rad")
    channel("RelativeHumidity", [f32(0.55 if index < at(800) else 55.0) for index in range(count)], "%")
    channel("FogLevel", [0.0] * count, "%")
    channel("Precipitation", [f32(0.1 if index > at(1100) else 0.0) for index in range(count)], "%")
    channel("AirPressure", [f32(101325.0 - index) if index % 300 else -0.0 for index in range(count)], "Pa")
    channel("AirDensity", [f32(1.2) if index % 400 else 0.0 for index in range(count)], "kg/m^3")
    channel("TrackUsage", [f32(min(1.0, index / count)) for index in range(count)], "%")
    channel("TrackWetness", [1 if index < at(1000) else 2 for index in range(count)])
    channel("WeatherDeclaredWet", [index > at(1150) for index in range(count)], noise=0.0)
    channel("Skies", [1] * count)
    channel("PlayerTireCompound", [0] * count)
    for name in ("dpQTape", "dpWeightJackerLeft", "dpFuelAddKg"):
        channel(name, [f32(40.0 + (index // at(400))) for index in range(count)])

    distance_unit = {"units": "mm", "feet": "ft"}.get(variant, "m")
    scale = {"mm": 1000.0, "ft": 3.28084}.get(distance_unit, 1.0)
    channel("CFSRrideHeight", [f32((0.05 + 0.01 * math.sin(index / 9.0)) * scale) for index in range(count)], distance_unit)
    pressure_unit = {"units": "bar", "psi": "psi"}.get(variant, "kPa")
    pressure_scale = {"bar": 0.01, "psi": 0.145}.get(pressure_unit, 1.0)
    for offset, tire in enumerate(engine.TIRES):
        channel(f"{tire}rideHeight", [f32((0.08 + 0.01 * math.sin(index / (7.0 + offset))) * scale) for index in range(count)], distance_unit)
        channel(f"{tire}shockDefl", [f32((0.02 * math.sin(index / 4.0)) * scale) for index in range(count)], distance_unit)
        channel(f"{tire}shockVel", [f32(0.3 * math.cos(index / 3.0 + offset)) for index in range(count)], "m/s")
        channel(f"{tire}pressure", [f32((170.0 + index * 0.01) * pressure_scale) for index in range(count)], pressure_unit)
        channel(f"{tire}coldPressure", [f32(165.0 * pressure_scale)] * count, pressure_unit)
        for position in ("CL", "CM", "CR", "L", "M", "R"):
            if variant == "sparse-temps" and position in ("CM", "M"):
                continue
            channel(f"{tire}temp{position}", [f32(70.0 + temperature_offset + offset + index * 0.003) for index in range(count)], temperature_unit)

    # Damage/repair state.
    channel("PlayerCarTowTime", [f32(max(0.0, 12.0 - (index - at(1050)) / 60.0)) if at(1050) <= index < at(1050) + 6 else 0.0 for index in range(count)], "s")
    channel("PitRepairLeft", [f32(8.0 - (index - at(930)) * 0.2) if at(930) <= index < at(970) else 0.0 for index in range(count)], "s")
    channel("PitOptRepairLeft", [f32(4.0) if at(930) <= index < at(945) else 0.0 for index in range(count)], "s")
    channel("PlayerFastRepairsUsed", [0 if index < at(940) else 1 for index in range(count)], noise=0.005)
    channel("FastRepairAvailable", [1 if index < at(940) else 0 for index in range(count)])
    channel("PlayerCarMyIncidentCount", [index // at(200) for index in range(count)], noise=0.005)
    channel("PlayerCarPitSvStatus", [2 if at(950) <= index < at(960) else 0 for index in range(count)])
    channel("PitSvFlags", [0x1F if at(900) <= index < at(960) else 0 for index in range(count)])
    channel("PitSvFuel", [f32(40.0) if at(900) <= index < at(960) else 0.0 for index in range(count)], "l")
    channel("dpFastRepair", [0] * count)

    for missing in {
        "no-speed": ("Speed",),
        "no-state": ("SessionState", "PlayerTrackSurface", "CarDistAhead", "CarDistBehind"),
        "no-flags": ("SessionFlags", "OnPitRoad"),
    }.get(variant, ()):
        channels.pop(missing, None)
    if variant == "gappy":
        channels["Brake"] = [None] * count
        channels["RPM"] = channels["RPM"][: count // 2]  # padded with gaps by the table
    return {
        "samples": channels,
        "variables": [{"name": name, "unit": unit} for name, unit in units.items()],
        "sample_rate_hz": 60,
        "session_info": {
            "SplitTimeInfo": {"Sectors": [{"SectorNum": 0, "SectorStartPct": 0.0}, {"SectorNum": 1, "SectorStartPct": 0.5}]},
            "WeekendInfo": {"TrackSkies": "Partly Cloudy", "TrackID": 1, "TrackDisplayName": "Synthetic", "TrackConfigName": "Oval"},
            "SessionInfo": {"Sessions": [{"SessionNum": 0, "SessionType": "Race", "SessionTrackRubberState": "moderate usage"}]},
        },
    }


# The per-side traffic keys are newer than the pre-change reference: each lap's
# clean_context ends with them. They are split off before the stage comparison
# and checked against a per-sample loop in the reference's own terms.
TRAFFIC_SIDE_KEYS = ("traffic_ahead_fraction", "traffic_behind_fraction", "traffic_side")


def split_traffic_sides(laps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove the per-side traffic keys from each lap's clean_context and return them."""

    return [{key: lap["clean_context"].pop(key) for key in TRAFFIC_SIDE_KEYS} for lap in laps]


def per_sample_traffic_sides(table: Any) -> list[dict[str, Any]]:
    """The per-side keys of every summarised lap, folded sample by sample as the reference folds."""

    times = table.get("SessionTime", "SessionTimeOfDay", default=None)
    laps = table.get("Lap", default=None)
    speed = table.get("Speed", default=None)
    ahead_values = table.get("CarDistAhead", default=None)
    behind_values = table.get("CarDistBehind", default=None)
    screened = table.has("CarDistAhead", "CarDistBehind")
    rate = engine._finite(table.metadata.get("sample_rate") or table.metadata.get("tick_rate")) or 20.0
    dts = reference._dt_series(times, rate)
    groups: dict[int, list[int]] = {}
    for index, raw_lap in enumerate(laps):
        lap_value = engine._finite(raw_lap)
        if lap_value is not None and lap_value >= 0:
            groups.setdefault(int(lap_value), []).append(index)
    expected = []
    for lap_number in sorted(groups):
        indices = groups[lap_number]
        if len(indices) < 2:
            continue
        near = ahead_time = behind_time = observed = 0.0
        for index in indices if screened else ():
            ahead = engine._finite(ahead_values[index])
            behind = engine._finite(behind_values[index])
            observed += dts[index]
            speed_mps = max(0.0, engine._finite(speed[index]) or 0.0)
            ahead_near = ahead is not None and ahead > 0.0 and ahead <= max(25.0, speed_mps * 0.75)
            behind_near = behind is not None and behind > 0.0 and behind <= max(15.0, speed_mps * 0.40)
            if ahead_near or behind_near:
                near += dts[index]
            if ahead_near:
                ahead_time += dts[index]
            if behind_near:
                behind_time += dts[index]
        expected.append({
            "traffic_proximity_fraction": engine._round(near / observed if observed > 0.0 else None, 4),
            **engine._traffic_sides(near, ahead_time, behind_time, observed),
        })
    return expected


def traffic_sides_of(reference_laps: list[dict[str, Any]], sides: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The reference's combined fraction beside the engine's per-side keys, lap by lap."""

    return [
        {"traffic_proximity_fraction": lap["clean_context"]["traffic_proximity_fraction"], **side}
        for lap, side in zip(reference_laps, sides, strict=True)
    ]


VARIANTS = (
    (11, "full"),
    (12, "units"),
    (13, "fahrenheit"),
    (14, "feet"),
    (15, "psi"),
    (16, "int-speed"),
    (17, "text-pit"),
    (18, "no-wheels"),
    (19, "no-gps"),
    (20, "no-speed"),
    (21, "no-state"),
    (22, "no-flags"),
    (23, "gappy"),
    (24, "sparse-temps"),
)


class ColumnPrimitiveTests(unittest.TestCase):
    """Each primitive equals its per-sample formulation on hostile values."""

    def setUp(self) -> None:
        rng = random.Random(7)
        base = [f32(rng.uniform(-200.0, 200.0)) for _ in range(400)]
        edges = [0.0, -0.0, 1.0, 1.5, 1.5000001, -1.5, 100.0, 150.0, 0.08, 0.15, 0.35, 10.0, 2.0, -2.0,
                 5e-324, -5e-324, 1e308, -1e308, 3.4e38, 1e-300]
        self.columns = {
            "finite": base + edges,
            "gappy": noisy(random.Random(8), base + edges, 0.2),
            "ints": [rng.randint(-5, 5) for _ in range(100)],
            "bools": [rng.random() < 0.5 for _ in range(100)],
            "text": ["1", "x", " 2.5 ", "nan", "inf", "-0.0", ""] * 5,
            "none": [None] * 50,
            "empty": [],
            "overflowing-sum": [1e308, 1e308, -1e308, 5.0],
        }

    def test_normalize_and_finite_values(self) -> None:
        for name, values in self.columns.items():
            with self.subTest(column=name):
                expected = [engine._finite(value) for value in values]
                normalized, complete = engine._normalize_column(values)
                assert_identical(self, expected, normalized)
                self.assertEqual(None not in expected, complete)
                assert_identical(self, [number for number in expected if number is not None], engine._finite_values(values))
                assert_identical(self, [number for number in expected if number is not None], engine._finite_values(iter(values)))

    def test_mean_median_percentile_match_the_original(self) -> None:
        for name, values in self.columns.items():
            with self.subTest(column=name):
                for function in ("_mean", "_median"):
                    assert_identical(
                        self,
                        outcome(getattr(reference, function), values),
                        outcome(getattr(engine, function), values),
                    )
                for percentile in (-1.0, 0.0, 0.05, 0.5, 0.55, 0.9, 0.95, 0.99, 1.0, 2.0):
                    assert_identical(
                        self,
                        outcome(reference._percentile, values, percentile),
                        outcome(engine._percentile, values, percentile),
                    )
                numbers = sorted(engine._finite_values(values))
                if numbers:
                    assert_identical(self, statistics.median(numbers), engine._median_of_sorted(numbers))
                for percentile in (0.05, 0.9, 0.99):
                    assert_identical(self, reference._percentile(numbers, percentile), engine._percentile_of_sorted(numbers, percentile))
        # A stable sort keeps the first of equal signed zeros; the original did too.
        for values in ([0.0, -0.0], [-0.0, 0.0], [-0.0, 0.0, -0.0, 0.0]):
            assert_identical(self, reference._median(values), engine._median(values))
            assert_identical(self, reference._percentile(values, 0.05), engine._percentile(values, 0.05))

    def test_fraction_bool_flag_and_binary_columns(self) -> None:
        for name, values in self.columns.items():
            with self.subTest(column=name):
                normalized, _complete = engine._normalize_column(values)
                assert_identical(self, [engine._fraction(value) for value in values], engine._fraction_column(normalized))
                assert_identical(self, [engine._bool(value) for value in values], engine._bool_column(values))
                assert_identical(self, [int(engine._finite(value) or 0) for value in values], engine._distinct_map(engine._flag_bits, normalized))
                binary = [None if value is None else (1.0 if engine._bool(value) else 0.0) for value in values]
                assert_identical(self, (binary, None not in binary), engine._binary_column(values))

    def test_unit_steps_match_the_original_conversions(self) -> None:
        units = {
            "distance": ("m", "mm", "cm", "ft", "in", " IN ", None, ""),
            "velocity": ("m/s", "mm/s", "cm/s", "ft/s", "ips", None),
            "pressure": ("kPa", "Pa", "bar", "psi", None),
            "temperature": ("C", "°C", "F", "K", None),
            "angle": ("rad", None),
        }
        for name, values in self.columns.items():
            normalized, complete = engine._normalize_column(values)
            for quantity, options in units.items():
                for unit in options:
                    with self.subTest(column=name, quantity=quantity, unit=unit):
                        expected = [reference._convert_setup_value(value, quantity, unit) for value in values]
                        steps = engine._unit_steps(quantity, engine._normalized_unit(unit))
                        assert_identical(self, expected, engine._apply_steps(normalized, complete, steps))
                        assert_identical(self, expected, [engine._convert_setup_value(value, quantity, unit) for value in values])
        # Every column shape _apply_steps spells out, against its infix form.
        numbers = engine._finite_values(self.columns["finite"])
        for steps, infix in (
            (((mul, 2.5),), lambda n: n * 2.5),
            (((truediv, 2.54),), lambda n: n / 2.54),
            (((mul, 3.0), (truediv, 7.0)), lambda n: n * 3.0 / 7.0),
            (((mul, 100.0), (mul, 0.145)), lambda n: n * 100.0 * 0.145),
            (((mul, 9.0), (truediv, 5.0), (add, 32.0)), lambda n: n * 9.0 / 5.0 + 32.0),
            (((sub, 273.15), (mul, 9.0), (truediv, 5.0), (add, 32.0)), lambda n: (n - 273.15) * 9.0 / 5.0 + 32.0),
            (((add, 1.0), (mul, 2.0), (sub, 3.0)), lambda n: (n + 1.0) * 2.0 - 3.0),
        ):
            with self.subTest(steps=steps):
                assert_identical(self, [infix(n) for n in numbers], engine._apply_steps(numbers, True, steps))
                gappy = [None, *numbers, None]
                assert_identical(self, [None, *[infix(n) for n in numbers], None], engine._apply_steps(gappy, False, steps))

    def test_row_means_equal_per_sample_fmean(self) -> None:
        rng = random.Random(9)
        for trial in range(20):
            width = rng.randint(1, 3)
            columns = []
            for _ in range(width):
                values = [f32(rng.uniform(-50, 150)) for _ in range(60)]
                if trial % 3:
                    values = noisy(rng, values, 0.2)
                if trial % 5 == 0:
                    values = values[: rng.randint(0, 60)]
                columns.append(engine._normalize_column(values))
            length = max(len(values) for values, _ in columns)
            expected = []
            for index in range(length):
                sample = [values[index] for values, _ in columns if index < len(values) and values[index] is not None]
                expected.append(reference._mean(sample))
            means, complete = engine._row_means(columns)
            assert_identical(self, expected, means)
            self.assertEqual(None not in expected, complete)
        with self.assertRaises(OverflowError):  # fsum's own overflow, as statistics.fmean raised
            engine._row_means([([1e308], True), ([1e308], True)])

    def test_sequence_helpers(self) -> None:
        rng = random.Random(10)
        for name, values in self.columns.items():
            with self.subTest(column=name):
                indices = sorted(rng.sample(range(len(values)), min(len(values), 30))) if values else []
                assert_identical(self, reference._positive_distance_delta(values, indices), engine._positive_distance_delta(values, indices))
                run = list(range(3, min(len(values), 40)))
                assert_identical(self, [values[i] for i in run], engine._ascending_taker(run)(values))
                assert_identical(self, [values[i] for i in indices], engine._ascending_taker(indices)(values))
                assert_identical(self, reference._dt_series(values, 60.0), engine._dt_series(values, 60.0))
        # Running totals must stay left folds (the original's +=): these
        # increases are 1e16 then 1.0s, which a compensated sum() adds up
        # differently, so a sum() in place of the fold would be caught.
        wobble = [0.0, 1e16, 0.0, 1.0, 0.0, 1.0, 0.0, 1.0, 0.5, 2.0]
        increases = [c - p for p, c in zip(wobble, wobble[1:]) if c >= p]
        self.assertNotEqual(sum(increases), functools.reduce(add, increases, 0.0))
        everything = list(range(len(wobble)))
        assert_identical(self, reference._positive_distance_delta(wobble, everything), engine._positive_distance_delta(wobble, everything))
        # Exactly +/-200 % slip is kept; the original tested abs(ratio) <= 2.0.
        speeds = noisy(rng, [f32(rng.uniform(-5, 60)) for _ in range(300)], 0.1) + [10.0, 9.999, -0.0, 10.0, 10.0, 20.0]
        wheels = noisy(rng, [f32(rng.uniform(-5, 190)) for _ in range(300)], 0.1) + [30.0, 40.0, 30.0, -10.0, 30.0000001, 60.0]
        expected_ratio = []
        for car, wheel in zip(speeds, wheels):
            car, wheel = engine._finite(car), engine._finite(wheel)
            if car is None or wheel is None or car < 10.0:
                expected_ratio.append(None)
                continue
            ratio = (wheel - car) / max(car, 1e-9)
            expected_ratio.append(ratio if abs(ratio) <= 2.0 else None)
        assert_identical(self, expected_ratio, engine._wheel_ratio_column(engine._normalize_column(speeds)[0], engine._normalize_column(wheels)[0]))
        assert_identical(
            self,
            [engine._vehicle_sideslip_degrees(x, y) for x, y in zip(speeds, wheels)],
            engine._sideslip_column(engine._normalize_column(speeds)[0], engine._normalize_column(wheels)[0]),
        )
        for values in (speeds, wheels, [3, 3, 2, None, 4, 4.5, NAN, 5]):
            normalized = engine._normalize_column(values)[0]
            expected = []
            for index in range(1, len(values)):
                previous = engine._finite(values[index - 1])
                current = engine._finite(values[index])
                if previous is None or current is None or current <= previous:
                    continue
                expected.append(index)
            self.assertEqual(expected, engine._increase_indices(normalized))


class StageEquivalenceTests(unittest.TestCase):
    """Rewritten stages equal the pre-change stages on synthetic races."""

    def tables(self, seed: int, variant: str) -> tuple[engine.TelemetryTable, engine.TelemetryTable]:
        telemetry = synthetic_race(seed, variant=variant)
        return engine.TelemetryTable(telemetry), engine.TelemetryTable(telemetry)

    def assert_stage(self, name: str, old: Any, new: Any, old_table: Any, new_table: Any) -> None:
        assert_identical(self, old, new, name)
        self.assertEqual(old_table.accessed_channels, new_table.accessed_channels, f"{name}: accessed channels")

    def test_lap_summaries_and_traces(self) -> None:
        for seed, variant in VARIANTS:
            with self.subTest(variant=variant):
                old_table, new_table = self.tables(seed, variant)
                old_laps = reference._lap_summaries(old_table)
                new_laps = engine._lap_summaries(new_table)
                sides = split_traffic_sides(new_laps)
                self.assertGreater(len(old_laps), 3)
                self.assert_stage("laps", old_laps, new_laps, old_table, new_table)
                assert_identical(
                    self,
                    per_sample_traffic_sides(engine.TelemetryTable(synthetic_race(seed, variant=variant))),
                    traffic_sides_of(old_laps, sides),
                    "traffic sides",
                )
                # 160 bins (the default) leaves ~1 sample per bin here; coarser
                # bins hold many samples, like real laps (~11 per bin at 60 Hz).
                for bins in (160, 16, 7):
                    old_traces = reference._lap_trace_payload(old_table, old_laps, maximum_bins=bins)
                    new_traces = engine._lap_trace_payload(new_table, new_laps, maximum_bins=bins)
                    # The steering declaration is new. These races state no
                    # steering ratio, so steering stays steering-wheel angle
                    # and every other byte of the payload is unchanged.
                    steering = new_traces.pop("steering")
                    self.assertEqual((steering["basis"], steering["status"]), ("steering_wheel", "not_recorded"))
                    self.assertGreater(old_traces["trace_count"], 3)
                    self.assert_stage(f"lap_traces[{bins}]", old_traces, new_traces, old_table, new_table)
                self.assert_stage(
                    "additional_trace_sources",
                    reference._additional_trace_sources(old_table),
                    engine._additional_trace_sources(new_table),
                    old_table,
                    new_table,
                )

    def test_lap_summaries_with_zero_or_single_sample_laps(self) -> None:
        for lap in ([0] * 5, [None] * 5, [-1, -1, 0, 1, 2], [1, 1, 2, 2, 1]):
            with self.subTest(lap=lap):
                telemetry = {"samples": {"Lap": lap, "LapDistPct": [0.0, 0.2, 0.5, 0.9, 1.0], "Speed": [20.0] * 5}}
                old_table, new_table = engine.TelemetryTable(telemetry), engine.TelemetryTable(telemetry)
                old_laps, new_laps = reference._lap_summaries(old_table), engine._lap_summaries(new_table)
                sides = split_traffic_sides(new_laps)
                self.assert_stage("laps", old_laps, new_laps, old_table, new_table)
                assert_identical(
                    self,
                    per_sample_traffic_sides(engine.TelemetryTable(telemetry)),
                    traffic_sides_of(old_laps, sides),
                    "traffic sides",
                )

    def test_setup_series_telemetry_and_track_profile(self) -> None:
        for seed, variant in VARIANTS:
            with self.subTest(variant=variant):
                old_table, new_table = self.tables(seed, variant)
                old_series = reference._setup_channel_series(old_table)
                new_series = engine._setup_channel_series(new_table)
                self.assert_stage("setup_series", old_series, new_series, old_table, new_table)
                self.assert_stage(
                    "setup_telemetry",
                    reference._setup_telemetry(old_table, old_series),
                    engine._setup_telemetry(new_table, new_series),
                    old_table,
                    new_table,
                )
                old_profile = reference._track_profile(old_table, setup_series=old_series)
                new_profile = engine._track_profile(new_table, setup_series=new_series)
                self.assert_stage("track_profile", old_profile, new_profile, old_table, new_table)

    def test_conditions_adjustments_and_service_events(self) -> None:
        for seed, variant in VARIANTS:
            with self.subTest(variant=variant):
                old_table, new_table = self.tables(seed, variant)
                for name in ("_conditions_summary", "_driver_adjustments_summary", "_service_events"):
                    self.assert_stage(
                        name,
                        getattr(reference, name)(old_table),
                        getattr(engine, name)(new_table),
                        old_table,
                        new_table,
                    )

    def test_disrupted_samples_match_the_per_sample_state_loop(self) -> None:
        for seed, variant in VARIANTS:
            with self.subTest(variant=variant):
                table = engine.TelemetryTable(synthetic_race(seed, variant=variant))
                aliases = engine.DAMAGE_REPAIR_CHANNEL_ALIASES
                for epsilon in (0.01, 0.25, 5.0):
                    tow_channel, tow = table.resolve(*aliases["tow_timer"], default=None)
                    mandatory_channel, mandatory = table.resolve(*aliases["mandatory_repair_timer"], default=None)
                    optional_channel, optional = table.resolve(*aliases["optional_repair_timer"], default=None)
                    fast_channel, fast_used = table.resolve(*aliases["fast_repairs_used"], default=None)
                    flags_channel, session_flags = table.resolve("SessionFlags", default=0)
                    _, on_pit = table.resolve("OnPitRoad", default=False)
                    _, in_stall = table.resolve("PlayerCarInPitStall", default=False)
                    _, service_active = table.resolve("PitstopActive", default=False)
                    # The original loops, verbatim apart from local names.
                    fast_increment_indices: set[int] = set()
                    if fast_channel:
                        for index in range(1, table.length):
                            before = engine._finite(fast_used[index - 1])
                            after = engine._finite(fast_used[index])
                            if before is not None and after is not None and after > before:
                                fast_increment_indices.add(index)
                    state = []
                    for index in range(table.length):
                        tow_active = (engine._finite(tow[index]) or 0.0) > epsilon
                        mandatory_active = (engine._finite(mandatory[index]) or 0.0) > epsilon if mandatory_channel else False
                        optional_active = (engine._finite(optional[index]) or 0.0) > epsilon if optional_channel else False
                        repair_required_active = bool(
                            flags_channel and int(engine._finite(session_flags[index]) or 0) & engine.REPAIR_REQUIRED_FLAG
                        )
                        state.append(
                            engine._bool(on_pit[index]) or engine._bool(in_stall[index]) or engine._bool(service_active[index])
                            or tow_active or mandatory_active or optional_active or repair_required_active
                            or index in fast_increment_indices
                        )
                    expected = [index for index, active in enumerate(state) if active]
                    fast = set(engine._increase_indices(table._finite_for(fast_channel)[0])) if fast_channel else set()
                    self.assertEqual(fast_increment_indices, fast)
                    self.assertEqual(
                        expected,
                        engine._disrupted_sample_indices(
                            table,
                            epsilon,
                            mandatory_recorded=bool(mandatory_channel),
                            optional_recorded=bool(optional_channel),
                            session_flags_channel=flags_channel,
                            fast_increment_indices=fast,
                        ),
                    )

    def test_foundation_statistics(self) -> None:
        # build_track_geometry left this equivalence with profile v24: the
        # centreline rule (clean racing laps, cos-latitude scale, smoothing)
        # deliberately changes its output. test_race_foundations pins the rule.
        for values in (
            noisy(random.Random(3), [f32(value / 7.0) for value in range(200)], 0.2),
            [10**400, 1.0, None],  # float() overflow is a gap in race_foundations
            [], [None], ["x", "2"], [0.0, -0.0], [-0.0, 0.0],
        ):
            assert_identical(self, foundations_reference._median(values), race_foundations._median(values))
            assert_identical(self, foundations_reference._percentile(values, 0.9), race_foundations._percentile(values, 0.9))
            assert_identical(self, [race_foundations._finite(value) for value in values], race_foundations._finite_column(values))

    def test_cached_column_views_never_alter_recorded_columns(self) -> None:
        telemetry = synthetic_race(31)
        snapshot = {name: list(values) for name, values in telemetry["samples"].items()}
        table = engine.TelemetryTable(telemetry)
        before = {name: list(values) for name, values in table.channels.items()}
        laps = engine._lap_summaries(table)
        engine._lap_trace_payload(table, laps)
        series = engine._setup_channel_series(table)
        engine._setup_telemetry(table, series)
        engine._track_profile(table, setup_series=series)
        engine._conditions_summary(table)
        engine._damage_repair_summary(table, laps, engine._runs(table, laps))
        for name, values in before.items():
            assert_identical(self, values, table.channels[name], name)
        engine.analyze_telemetry(telemetry)
        assert_identical(self, snapshot, telemetry["samples"])


class TypedViewDecoderTests(unittest.TestCase):
    """The IBT column decoder equals struct decoding for any record layout."""

    def test_typed_views_match_struct_rows(self) -> None:
        rng = random.Random(5)
        specials = {
            "f": [NAN, INF, -INF, -0.0, 0.0, 1e-45, 3.4e38, struct.unpack("<f", bytes.fromhex("0000a07f"))[0]],
            "d": [NAN, INF, -INF, -0.0, 5e-324, 1e308],
        }
        for record_length in (13, 37, 64, 1070, 1071, 1072):
            for format_code, size in (("f", 4), ("d", 8), ("i", 4), ("I", 4), ("?", 1)):
                for offset in {0, 1, 3, record_length - size}:
                    if offset < 0 or offset + size > record_length:
                        continue
                    for with_specials in (False, True):
                        record_count = rng.randint(0, 40)
                        buffer = bytearray(rng.randbytes(record_count * record_length))
                        if format_code in specials and with_specials and record_count:
                            for value in specials[format_code]:
                                where = rng.randrange(record_count) * record_length + offset
                                struct.pack_into(f"<{format_code}", buffer, where, value)
                        if format_code == "?":
                            for index in range(record_count):
                                buffer[index * record_length + offset] = rng.choice((0, 1, 2, 255))
                        decoder = struct.Struct(f"<{offset}x{format_code}{record_length - offset - size}x")
                        for step in (1, 2, 3, 4):
                            with self.subTest(length=record_length, fmt=format_code, offset=offset, specials=with_specials, step=step):
                                expected = [row[0] for row in itertools.islice(decoder.iter_unpack(buffer), 0, None, step)]
                                actual = ibt_reader._strided_scalar_column(memoryview(buffer), record_length, offset, format_code, step)
                                if actual is None:  # left to struct: only for non-finite floats
                                    self.assertIn(format_code, "fd")
                                    self.assertFalse(all(math.isfinite(value) for value in expected))
                                    continue
                                assert_identical(self, expected, actual)


class CollectorPauseTests(unittest.TestCase):
    def test_pause_restores_the_previous_collector_state(self) -> None:
        was_enabled = gc.isenabled()
        try:
            gc.enable()
            with workflow._automatic_gc_paused():
                self.assertFalse(gc.isenabled())
            self.assertTrue(gc.isenabled())
            with self.assertRaises(RuntimeError):
                with workflow._automatic_gc_paused():
                    raise RuntimeError("analysis failed")
            self.assertTrue(gc.isenabled())
            gc.disable()
            with workflow._automatic_gc_paused():
                self.assertFalse(gc.isenabled())
            self.assertFalse(gc.isenabled())
        finally:
            (gc.enable if was_enabled else gc.disable)()


if __name__ == "__main__":  # pragma: no cover - convenience runner
    unittest.main()

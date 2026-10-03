"""Unit U1 (table) vector sources for ``emit_kernel_vectors.py``.

``docs/08-rewrite/port/phase2-plan.md`` section 2 (U1) and section 3. This
module holds what the generator registers for the table unit and nothing
else:

* ``ENTRIES``: the oracle entry points U1 ports.
* ``probe_views``: per recording, every channel's finite, fraction, boolean
  and flag-bit view and the views of the alias lists the engine resolves, as
  SHA-256 digests (``view_digest`` below), with each channel's count of
  non-finite and missing samples, so the corpus's blind spots are visible.
* ``SYNTHETIC``: tables for oracle paths no recording reaches (unit strings,
  NaN and infinity, text channels, Offline Testing one-lap-to-green, a merged
  group with missing cells).
* ``write_table_store``: the store ``table.ccs`` is written as. A merged table
  (``None`` cells, several source files) cannot be a decode-stage store; it
  is written as a ``merge``-stage store (decode layout plus a ``present``
  block per column with a missing cell and a ``provenance`` block), the layout
  ``coach_analysis::store`` reads and ``coach-analysis merge`` writes.
* ``main``: the native-merge vectors (``python table_vectors.py merge``):
  per-file decode stores and the oracle's merged columns, under
  ``crates/coach-analysis/tests/vectors/table/merge/<platform>/<case>/``.

Digest of a view (``view_digest``), SHA-256 in sample order. Finite and
fraction views: every sample's presence byte (0 or 1), then every sample's
IEEE-754 bits as 8 little-endian bytes (all zero when absent). Boolean views:
one byte per sample, 0 or 1. Flag-bit views: the ints as 8 little-endian
two's-complement bytes; a flag outside i64 makes the digest ``null``, sets
``flags_beyond_i64`` and adds ``flags_low64``, the same digest of every int's
low 64 bits (``int & (2**64 - 1)``), which the native view holds.
"""

from __future__ import annotations

import argparse
import array
import copy
import hashlib
import json
import math
import os
import shutil
import struct
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

TOOLS = Path(__file__).resolve().parent
CRATE = TOOLS.parent
REPO = CRATE.parents[1]
SCRIPTS = REPO / "iracing-coach" / "skills" / "analyze-iracing-race" / "scripts"
FIXTURE = REPO / "test-data" / "ibt" / "synthetic-race.ibt"
MERGE_VECTORS = CRATE / "tests" / "vectors" / "table" / "merge"
for _path in (TOOLS, SCRIPTS):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import kernel_codec as codec  # noqa: E402

PRODUCER = "table_vectors.py"

ENTRY_NAMES = (
    "_setup_channel_series",
    "_setup_telemetry",
    "_conditions_summary",
    "_driver_adjustments_summary",
    "_caution_flag_series",
    "_dt_series",
)

# ---------------------------------------------------------------------------
# Synthetic tables
# ---------------------------------------------------------------------------

_TYPE_NAMES = {0: "char", 1: "bool", 2: "int", 3: "bitfield", 4: "float", 5: "double"}
_TYPE_SIZES = {0: 1, 1: 1, 2: 4, 3: 4, 4: 4, 5: 8}


def _f32(value: float) -> float:
    """``value`` rounded to float32 and widened back, as a decoded f32 sample is."""

    return struct.unpack("<f", struct.pack("<f", value))[0]


def _resize(table: dict[str, Any], count: int) -> dict[str, Any]:
    """The fixture race stretched to ``count`` samples: three laps over 300 samples at 60 Hz."""

    samples = table["samples"]
    samples["SessionTime"] = [index / 60.0 for index in range(count)]
    samples["SessionNum"] = [1] * count
    samples["Lap"] = [index * 3 // count for index in range(count)]
    samples["LapDistPct"] = [_f32((index * 3 % count) / count) for index in range(count)]
    samples["Speed"] = [_f32(62.0 - 12.0 * abs(_triangle(index * 3 % count, count))) for index in range(count)]
    samples["Throttle"] = [_f32(0.85)] * count
    samples["Brake"] = [0.0] * count
    samples["OnPitRoad"] = [index < 6 for index in range(count)]
    table["sample_indices"] = list(range(count))
    table["sample_count"] = count
    table["source_record_count"] = count
    metadata = table["metadata"]
    metadata["record_count"] = count
    metadata["disk"] = dict(metadata["disk"], session_record_count=count)
    return table


def _add(table: dict[str, Any], name: str, type_code: int, values: list[Any], unit: str = "", count: int = 1) -> None:
    """Add (or replace) channel ``name`` with its variable entry."""

    if type_code == 4:
        values = [value if value is None or isinstance(value, bool) else _f32(value) for value in values]
    table["samples"][name] = values
    offset = 1000 + 64 * len(table["samples"])
    variable = {
        "name": name,
        "description": name,
        "unit": unit,
        "type": _TYPE_NAMES[type_code],
        "type_code": type_code,
        "offset": offset,
        "count": count,
        "count_as_time": False,
        "byte_size": _TYPE_SIZES[type_code] * count,
    }
    for key in ("variables", "available_variables"):
        entries = [entry for entry in table[key] if entry["name"] != name]
        entries.append(dict(variable))
        table[key] = entries
    selection = table.get("channel_selection")
    if isinstance(selection, dict):
        selection["available_count"] = len(table["available_variables"])
        selection["decoded_count"] = len(table["variables"])


def _triangle(index: int, period: int) -> float:
    """A triangle wave in [-1, 1] from IEEE arithmetic only.

    The builders avoid ``math.sin`` and the other C library functions, whose last bit
    differs between the Windows and Linux interpreters, so both platforms build the same
    tables and any vector difference comes from the oracle code under test.
    """

    return 4.0 * abs((index % period) / period - 0.5) - 1.0


def _wave(count: int, base: float, amplitude: float, period: int = 37) -> list[float]:
    return [base + amplitude * _triangle(index, period) for index in range(count)]


def synthetic_setup_units(table: dict[str, Any]) -> dict[str, Any]:
    """Platform, shock, pressure and temperature channels in every unit `_unit_steps` knows,
    with NaN, infinity, an overflowing conversion, ints and the default units."""

    count = 300
    _resize(table, count)
    nan, inf = math.nan, math.inf
    splitter = _wave(count, 0.05, 0.01)
    splitter[10] = nan
    _add(table, "CFSRrideHeight", 4, splitter, "m")
    _add(table, "LFrideHeight", 4, _wave(count, 0.06, 0.004, 23), "m")
    _add(table, "RFrideHeight", 5, _wave(count, 61.0, 3.0, 29), "mm")
    left_rear = _wave(count, 2.4, 0.2, 31)
    left_rear[50] = inf
    _add(table, "LRrideHeight", 5, left_rear, "in")
    _add(table, "RRrideHeight", 2, [6 + index % 3 for index in range(count)], "cm")
    _add(table, "LFshockDefl", 4, _wave(count, 0.1, 0.02, 41), "ft")
    _add(table, "RFshockDefl", 5, _wave(count, 0.03, 0.01, 43), " M ")
    _add(table, "LRshockDefl", 4, _wave(count, 0.04, 0.01, 47), "")
    _add(table, "RRdamperDefl", 5, _wave(count, 40.0, 9.0, 53), "mm")
    _add(table, "LFshockVel", 4, _wave(count, 0.0, 0.3, 11), "m/s")
    _add(table, "RFshockVel", 5, _wave(count, 0.0, 5.0, 13), "ips")
    _add(table, "LRshockVel", 5, _wave(count, 0.0, 90.0, 17), "mm/s")
    velocity = _wave(count, 0.0, 1.1, 19)
    velocity[7] = nan
    velocity[8] = -inf
    _add(table, "RRshockVel", 4, velocity, "ft/s")
    _add(table, "RRSHshockVel", 5, _wave(count, 0.0, 9.0, 21), "cm/s")
    _add(table, "LFpressure", 4, _wave(count, 180.0, 4.0, 59), "kPa")
    _add(table, "RFpressure", 5, _wave(count, 181000.0, 3000.0, 61), "Pa")
    _add(table, "LRpressure", 5, _wave(count, 1.8, 0.05, 67), "bar")
    _add(table, "RRpressure", 4, _wave(count, 26.0, 0.5, 71), "psi")
    _add(table, "LFcoldPressure", 4, [172.0] * count, "kPa")
    _add(table, "RRtireColdPressure", 5, [1.7e5] * count, "PA")
    _add(table, "LFtempCL", 4, _wave(count, 80.0, 5.0, 73), "C")
    _add(table, "LFtempCM", 5, _wave(count, 82.0, 5.0, 79), "C")
    hot = _wave(count, 84.0, 5.0, 83)
    hot[3] = 1e308  # * 9.0 overflows to inf inside the conversion
    _add(table, "LFtempCR", 5, hot, "C")
    _add(table, "RFtempCL", 5, _wave(count, 355.0, 4.0, 89), "K")
    _add(table, "RFTempCM", 4, _wave(count, 180.0, 6.0, 97), "° F")
    _add(table, "LRtireTempCM", 5, [math.nan] * count, "C")
    _add(table, "RRTireTempCR", 2, [70 + index % 5 for index in range(count)], "kelvin")
    return table


def synthetic_conditions(table: dict[str, Any]) -> dict[str, Any]:
    """Every `_conditions_summary` and `_driver_adjustments_summary` channel, with signed zeros,
    exact 1.0, percent-scaled fractions, NaN, infinity and changes either side of 10**-digits."""

    count = 300
    _resize(table, count)
    nan, inf = math.nan, math.inf
    _add(table, "TrackTemp", 4, _wave(count, 30.0, 1.0), "C")
    _add(table, "TrackTempCrew", 5, _wave(count, 31.25, 0.75), "C")
    air = _wave(count, 21.5, 0.4, 101)
    air[0] = nan
    air[-1] = inf
    _add(table, "AirTemp", 5, air, "C")
    _add(table, "TrackWetness", 2, [1 if index < 200 else 2 for index in range(count)], "")
    humidity = [0.45 + 0.001 * (index % 50) for index in range(count)]
    humidity[5] = 55.0
    humidity[6] = -0.0
    humidity[7] = 1.0
    humidity[8] = 1.5
    humidity[9] = -3.0
    humidity[10] = 250.0
    _add(table, "RelativeHumidity", 4, humidity, "%")
    wind = _wave(count, 3.0, 1.0, 7)
    wind[100] = inf
    _add(table, "WindVel", 4, wind, "m/s")
    _add(table, "AirDensity", 4, _wave(count, 1.2, 0.0001, 9), "kg/m^3")
    _add(table, "AirPressure", 4, [101325.0 + (index % 3) for index in range(count)], "Pa")
    _add(table, "Precipitation", 4, [0.0] * count, "%")
    _add(table, "WeatherDeclaredWet", 1, [index % 97 == 5 for index in range(count)], "")
    compound = [1] * count
    _add(table, "PlayerTireCompound", 2, compound, "")
    _add(table, "dcBrakeBias", 4, [0.56 if index < 150 else 0.565 for index in range(count)], "%")
    _add(table, "dpQTape", 5, [3.0] * count, "")
    # 0.0001 apart: (max - min) > 10 ** -4 decides `changed` at the boundary.
    jacker = [1.0 if index < 120 else 1.0001 for index in range(count)]
    _add(table, "dpWeightJackerLeft", 5, jacker, "")
    _add(table, "dpWeightJackerRight", 5, [-0.0] * count, "")
    _add(table, "dpFuelAddKg", 5, [nan] * count, "kg")
    return table


def synthetic_conditions_last(table: dict[str, Any]) -> dict[str, Any]:
    """Conditions with gaps at the ends: a last compound behind trailing NaN, a negative
    compound, Fahrenheit and Kelvin unit strings the summary ignores, an all-NaN column."""

    count = 300
    _resize(table, count)
    nan = math.nan
    compound = [float(index % 4) for index in range(count)]
    compound[-1] = nan
    compound[-2] = nan
    compound[-3] = -2.75
    _add(table, "PlayerTireCompound", 5, compound, "")
    _add(table, "TrackTemp", 5, [nan] * 10 + _wave(count - 10, 120.0, 2.0), "F")
    _add(table, "AirTemp", 5, _wave(count, 295.0, 1.0), "K")
    _add(table, "RelativeHumidity", 5, [nan] * count, "%")
    _add(table, "Precipitation", 5, [0.0] * 100 + [12.5] * 200, "%")
    _add(table, "WeatherDeclaredWet", 2, [0] * count, "")
    _add(table, "dcBrakeBias", 5, [0.5 + 1e-5 * index for index in range(count)], "")
    return table


def _offline_flags(count: int) -> list[int]:
    flags = []
    for index in range(count):
        if index % 50 == 0:
            flags.append(0x0200 | 0x4000)  # one-lap-to-green with a real caution bit
        elif index % 7 == 0:
            flags.append(0x0004)
        elif index % 11 == 0:
            flags.append(0x0200 | 0x0004 | 0x10000)
        else:
            flags.append(0x0200)
    return flags


def synthetic_offline_testing(table: dict[str, Any]) -> dict[str, Any]:
    """An Offline Testing recording that runs on into a Race session: the one-lap-to-green
    bit cleared per sample by SessionNum through the session-info map."""

    count = 300
    _resize(table, count)
    table["metadata"]["sim_session_type"] = "Offline Testing"
    table["session_info"]["SessionInfo"]["Sessions"] = [
        {"SessionNum": 0, "SessionType": "Offline Testing"},
        {"SessionNum": " 1 laps ", "SessionType": "Race"},
        {"SessionNum": 2.5, "SessionType": "Race"},
        {"SessionNum": "3", "SessionType": ""},
        {"SessionNum": "x", "SessionType": "Race"},
        "not a mapping",
    ]
    _add(table, "SessionFlags", 3, _offline_flags(count), "irsdk_Flags")
    numbers = [0.0 if index < 120 else (1.0 if index < 200 else 3.0) for index in range(count)]
    numbers[130] = math.nan
    numbers[131] = 2.5
    numbers[132] = 2.0
    _add(table, "SessionNum", 5, numbers, "")
    return table


def synthetic_lone_qualify(table: dict[str, Any]) -> dict[str, Any]:
    """A Lone Qualify recording with no SessionNum channel and no session list."""

    count = 300
    _resize(table, count)
    table["metadata"]["sim_session_type"] = " Lone Qualify "
    table["session_info"]["SessionInfo"] = {"Sessions": None}
    flags = _offline_flags(count)
    flags[20] = -0x0200  # a negative bit pattern from a float channel
    _add(table, "SessionFlags", 5, [float(flag) for flag in flags], "")
    del table["samples"]["SessionNum"]
    table["variables"] = [entry for entry in table["variables"] if entry["name"] != "SessionNum"]
    table["available_variables"] = [entry for entry in table["available_variables"] if entry["name"] != "SessionNum"]
    return table


def synthetic_text_channels(table: dict[str, Any]) -> dict[str, Any]:
    """Char channels where the engine reads numbers and booleans: Python ``float()`` text
    (whitespace, underscores, ``nan``, ``inf``, garbage) and ``_bool`` text."""

    count = 300
    _resize(table, count)
    texts = ["21.5", " 1_0.5 ", "nan", "inf", "abc", "", "-0.0", "1e400", "\t22\n", "2_2"]
    _add(table, "AirTemp", 0, [texts[index % len(texts)] for index in range(count)], "C", count=32)
    words = ["Yes", " no", "TRUE", "on ", "1", "0", "", "off", " on ", "y"]
    _add(table, "WeatherDeclaredWet", 0, [words[index % len(words)] for index in range(count)], "", count=8)
    _add(table, "PlayerCarInPitStall", 0, [words[(index + 3) % len(words)] for index in range(count)], "", count=8)
    _add(table, "dcBrakeBias", 0, [f"{0.5 + index / 1000:.3f}" for index in range(count)], "", count=16)
    _add(table, "LFpressure", 0, [f" {170 + index % 9}.25 " for index in range(count)], "kPa", count=16)
    _add(table, "PlayerTireCompound", 0, ["2"] * (count - 1) + ["x"], "", count=4)
    _add(table, "SessionFlags", 0, ["512" if index % 3 else "0x4000" for index in range(count)], "", count=8)
    return table


# --- merged groups --------------------------------------------------------


def _file_table(base: dict[str, Any], first: int, count: int, *, drop: Iterable[str] = (),
                extra: Mapping[str, tuple[int, list[Any], str]] | None = None,
                time_edits: Mapping[int, float] | None = None) -> dict[str, Any]:
    """One file of a merged group: records ``first .. first + count`` of the fixture race."""

    table = copy.deepcopy(base)
    _resize(table, 300)
    samples = table["samples"]
    for name in list(samples):
        samples[name] = samples[name][first:first + count]
    for name in drop:
        del samples[name]
        table["variables"] = [entry for entry in table["variables"] if entry["name"] != name]
        table["available_variables"] = [entry for entry in table["available_variables"] if entry["name"] != name]
    for name, (type_code, values, unit) in (extra or {}).items():
        _add(table, name, type_code, values, unit)
    for index, value in (time_edits or {}).items():
        samples["SessionTime"][index] = value
    table["sample_indices"] = list(range(first, first + count))
    table["sample_count"] = count
    table["source_record_count"] = count
    table["metadata"]["record_count"] = count
    return table


def merge_cases(base: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Per-file decoded tables for each merged case (the oracle merges them)."""

    nan = math.nan
    first = _file_table(
        base, 0, 180, drop=("Brake",),
        extra={"AirTemp": (5, _wave(180, 21.0, 0.5), "C"),
               "WeatherDeclaredWet": (1, [index % 5 == 0 for index in range(180)], "")},
        time_edits={20: 20 / 60.0 + 1e-10},
    )
    second = _file_table(
        base, 150, 150, drop=("Throttle",),
        extra={"dcBrakeBias": (4, [0.55 + 0.0001 * index for index in range(150)], "%"),
               "AirTemp": (5, [nan] * 10 + _wave(140, 21.5, 0.5), "C"),
               "LFpressure": (5, _wave(150, 180.0, 4.0), "kPa")},
        # A sample 2e-9 s after a kept one is not a duplicate; NaN time sorts after every timed sample.
        time_edits={40: 189 / 60.0 + 2e-9, 41: nan},
    )
    third = _file_table(
        base, 280, 20,
        extra={"SessionTimeOfDay": (5, [5000.0 + index for index in range(20)], "s")},
    )
    # Without SessionTime the file's samples are ordered by SessionTimeOfDay.
    del third["samples"]["SessionTime"]
    third["variables"] = [entry for entry in third["variables"] if entry["name"] != "SessionTime"]
    # Codex phase-2 review finding 2: one file stores Speed as float, the next as double
    # with values a float cannot hold; Python merges the floats as they are.
    narrow = _file_table(base, 0, 150)
    wide = _file_table(base, 150, 150)
    wide_speed = [value + 1e-7 * (index % 7) for index, value in enumerate(wide["samples"]["Speed"])]
    _add(wide, "Speed", 5, wide_speed, "m/s")
    wide_first = _file_table(base, 0, 150, extra={"Throttle": (5, [0.85 + 1e-9 * index for index in range(150)], "%")})
    return {"reconnect-gaps": [first, second], "three-files": [first, second, third],
            "mixed-width": [narrow, wide], "mixed-width-double-first": [wide_first, _file_table(base, 150, 150)]}


def oracle_merge(tables: list[dict[str, Any]]) -> dict[str, Any]:
    """``workflow._load_analysis_telemetry`` over ``tables`` as if each were a recording.

    The loader runs unchanged; only its per-file reader is replaced, so the merge, the
    duplicate rule and the metadata merge are the oracle's own code.
    """

    import workflow

    folder = Path(tempfile.mkdtemp(prefix="u1-merge-"))
    try:
        paths = []
        for index, _table in enumerate(tables):
            path = folder / f"part{index}.ibt"
            shutil.copyfile(FIXTURE, path)
            os.utime(path, (1_780_000_000, 1_780_000_000))  # past the loader's 2 s quiet rule
            paths.append(str(path))
        by_path = {path: table for path, table in zip(paths, tables)}

        def load(path: str, channels: Any = None, target_hz: Any = None) -> dict[str, Any]:
            return copy.deepcopy(by_path[str(path)])

        saved = workflow.load_telemetry
        workflow.load_telemetry = load
        try:
            merged = workflow._load_analysis_telemetry(paths, None)
        finally:
            workflow.load_telemetry = saved
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    for item in merged.get("sample_provenance") or ():
        item.pop("source_path", None)
        for contributor in item.get("contributors") or ():
            contributor.pop("source_path", None)
    merged.pop("source_catalogs", None)
    merged.pop("catalog_summary", None)
    merged.pop("source_signatures", None)
    merged["metadata"]["path"] = "<synthetic>/merged"
    return merged


def synthetic_merged(table: dict[str, Any]) -> dict[str, Any]:
    """A two-file group with missing cells, merged by the oracle (the ``reconnect`` case)."""

    return oracle_merge(merge_cases(table)["reconnect-gaps"])


SYNTHETIC: tuple[tuple[str, Callable[[dict[str, Any]], dict[str, Any]]], ...] = (
    ("table-setup-units", synthetic_setup_units),
    ("table-conditions", synthetic_conditions),
    ("table-conditions-last", synthetic_conditions_last),
    ("table-offline-testing", synthetic_offline_testing),
    ("table-lone-qualify", synthetic_lone_qualify),
    ("table-text-channels", synthetic_text_channels),
    ("table-merged", synthetic_merged),
)

# ---------------------------------------------------------------------------
# Probe: every view of every channel
# ---------------------------------------------------------------------------


def _little_endian(numbers: array.array) -> bytes:
    if sys.byteorder != "little":
        numbers.byteswap()
    return numbers.tobytes()


def _digest_float_view(values: list[Any]) -> str:
    digest = hashlib.sha256(bytes(0 if value is None else 1 for value in values))
    digest.update(_little_endian(array.array("d", [0.0 if value is None else value for value in values])))
    return digest.hexdigest()


def _digest_bool_view(values: list[bool]) -> str:
    return hashlib.sha256(bytes(1 if value else 0 for value in values)).hexdigest()


def _digest_flag_view(values: list[int]) -> str | None:
    try:
        numbers = array.array("q", values)
    except OverflowError:
        return None
    return hashlib.sha256(_little_endian(numbers)).hexdigest()


def _digest_flag_low64(values: list[int]) -> str:
    low = [value & (2**64 - 1) for value in values]
    return hashlib.sha256(_little_endian(array.array("q", [v - 2**64 if v >= 2**63 else v for v in low]))).hexdigest()


def view_digest(table: Any, key: Any) -> dict[str, Any]:
    """Digests of the four cached views of ``key`` (a ``_column_key`` result)."""

    finite, complete = table._finite_for(key)
    fraction, fraction_complete = table._fractions_for(key)
    flags = table._flag_bits_for(key)
    flag_digest = _digest_flag_view(flags)
    digests = {
        "finite": _digest_float_view(finite),
        "complete": complete,
        "fraction": _digest_float_view(fraction),
        "fraction_complete": fraction_complete,
        "boolean": _digest_bool_view(table._booleans_for(key)),
        "flags": flag_digest,
        "flags_beyond_i64": flag_digest is None,
    }
    if flag_digest is None:
        digests["flags_low64"] = _digest_flag_low64(flags)
    return digests


def _alias_lists() -> list[tuple[tuple[str, ...], Any]]:
    import analysis_engine as engine

    lists: list[tuple[tuple[str, ...], Any]] = [(aliases, None) for aliases in engine.PLATFORM_CHANNEL_ALIASES.values()]
    for tire in engine.TIRES:
        lists.append((engine._shock_channel_aliases(tire, "deflection"), None))
        lists.append((engine._shock_channel_aliases(tire, "velocity"), None))
        lists.append((engine._tire_pressure_aliases(tire), None))
        lists.append((engine._tire_pressure_aliases(tire, cold=True), None))
        for position in ("CL", "CM", "CR"):
            lists.append((engine._tire_temperature_aliases(tire, position), None))
    lists += [
        (("OnPitRoad", "PlayerCarInPitStall", "PitstopActive"), False),
        (("OnPitRoad",), False),
        (("PlayerCarInPitStall",), False),
        (("PitstopActive",), False),
        (("SessionFlags",), 0),
        (("SessionTime", "SessionTimeOfDay"), None),
        (("TrackTempCrew", "TrackTemp"), None),
        (("Brake", "BrakeRaw"), None),
        (("Throttle", "ThrottleRaw"), None),
        (("SteeringWheelAngle",), None),
        (("SessionLapsTotal",), 0.0),
        (("NoSuchChannel",), 1),
        (("NoSuchChannel",), True),
        (("NoSuchChannel",), -0.0),
        (("NoSuchChannel",), math.nan),
        (("NoSuchChannel",), "yes"),
        (("NoSuchChannel",), " 2.5 "),
    ]
    lists += [(aliases, None) for aliases in engine.DAMAGE_REPAIR_CHANNEL_ALIASES.values()]
    return lists


def probe_views(context: Any) -> Iterable[tuple[str, Any]]:
    """``views``: per channel and per alias list, the digests of its cached views."""

    import analysis_engine as engine

    table = context.table
    channels = {}
    for name, values in table.channels.items():
        missing = sum(1 for value in values if value is None)
        finite, _complete = table._finite_for(name)
        entry = {
            "missing": missing,
            "nonfinite": sum(1 for value in finite if value is None) - missing,
        }
        entry.update(view_digest(table, name))
        channels[name] = entry
    aliases = []
    for names, default in _alias_lists():
        key = table._column_key(names, default)
        aliases.append({"aliases": list(names), "default": default, "key": key if isinstance(key, str) else None,
                        **view_digest(table, key)})
    ratios = []
    for speed in ("Speed",):
        for tire in engine.TIRES:
            wheel = f"{tire}speed"
            if speed in table.channels and wheel in table.channels:
                ratios.append({"speed": speed, "wheel": wheel,
                               "digest": _digest_float_view(table._wheel_ratios(speed, wheel))})
    steering = engine._steering_column(table, engine._steering_basis(table))
    yield "views", {
        "length": table.length,
        "channels": channels,
        "aliases": aliases,
        "wheel_ratios": ratios,
        "steering": {"digest": _digest_float_view(steering[0]), "complete": steering[1]},
    }


PROBES = (probe_views,)

# ---------------------------------------------------------------------------
# table.ccs: decode store, or merge store for a merged table
# ---------------------------------------------------------------------------

_DECODE_STORE = codec.write_decode_store


def _merged(telemetry: Mapping[str, Any]) -> bool:
    if "sample_indices" not in telemetry:
        return True
    return any(value is None for values in telemetry["samples"].values() for value in values)


def write_table_store(path: Path, *, telemetry: Mapping[str, Any], sampling_plan: Any, target_hz: Any,
                      producer: str) -> int:
    """``codec.write_decode_store``, or a ``merge``-stage store when ``telemetry`` is merged."""

    if not _merged(telemetry):
        return _DECODE_STORE(path, telemetry=telemetry, sampling_plan=sampling_plan, target_hz=target_hz,
                             producer=producer)
    return write_merge_store(path, telemetry=telemetry, sampling_plan=sampling_plan, target_hz=target_hz,
                             producer=producer)


def write_merge_store(path: Path, *, telemetry: Mapping[str, Any], sampling_plan: Any, target_hz: Any,
                      producer: str) -> int:
    """A merged table as a format-1 ``merge``-stage store.

    The decode layout, plus per column a ``present`` block (one byte per sample, 1 when
    the cell holds a value) whenever a cell is missing, and ``provenance.file_index``
    (u16 per sample); the sample-index block holds each sample's native record index.
    Missing numeric cells are stored as zero and missing text as an empty string.
    """

    metadata = telemetry["metadata"]
    header_info = metadata["header"]
    samples: Mapping[str, list[Any]] = telemetry["samples"]
    variables = {variable["name"]: variable for variable in telemetry["variables"]}
    provenance = telemetry["sample_provenance"]
    count = len(provenance)
    step, rate = sampling_plan(header_info["tick_rate"], target_hz)
    blocks = codec._Blocks()
    sample_block = blocks.place(struct.pack(f"<{count}I", *(item["native_record_index"] for item in provenance)))
    file_block = blocks.place(struct.pack(f"<{count}H", *(item["source_file_index"] for item in provenance)))
    columns = []
    for name, values in samples.items():
        variable = variables[name]
        element = codec.DECODE_ELEMENTS[variable["type_code"]]
        if len(values) != count:
            raise codec.CodecError(f"column {name!r} has {len(values)} values for {count} samples")
        entry: dict[str, Any] = {
            "name": name,
            "type_code": variable["type_code"],
            "count": variable["count"],
            "var_offset": variable["offset"],
            "element": element,
        }
        if element == "utf8":
            data, offsets = codec._text_block(values)
            entry["data"] = blocks.place(data)
            entry["text_offsets"] = blocks.place(offsets)
        else:
            width = variable["count"]
            if element == "f32" and any(
                value == value and _f32(value) != value
                for row in values if row is not None
                for value in (row if width > 1 else [row])
            ):
                # A group that stores the channel as float and as double (review finding 2):
                # a double column, as `coach-analysis merge` writes it.
                element = "f64"
                entry["element"], entry["type_code"] = "f64", 5
            filler = [False if element == "bool" else 0] * width
            rows = [filler if value is None else (value if width > 1 else [value]) for value in values]
            flat = [item for row in rows for item in row]
            if element == "bool":
                flat = [1 if item else 0 for item in flat]
            entry["data"] = blocks.place(struct.pack(f"<{len(flat)}{codec._PACK[element]}", *flat))
        if any(value is None for value in values):
            entry["present"] = blocks.place(bytes(0 if value is None else 1 for value in values))
        columns.append(entry)
    header = {
        "format": codec.STORE_FORMAT,
        "version": codec.STORE_VERSION,
        "stage": "merge",
        "producer": producer,
        "source": {
            "file_size": metadata["file_size"],
            "record_count": metadata["record_count"],
            "tick_rate": header_info["tick_rate"],
            "buffer_offset": metadata["buffer_offset"],
            "buffer_length": header_info["buffer_length"],
            "num_variables": len(telemetry["available_variables"]),
        },
        "sampling": {
            "target_hz": None if target_hz is None else float(target_hz),
            "step": step,
            "integral_step": step.is_integer(),
            "sample_rate_hz": float(telemetry.get("sample_rate_hz") or rate),
            "sample_count": count,
        },
        "channel_mode": (telemetry.get("channel_selection") or {}).get("mode") or "selected",
        "sample_indices": sample_block,
        "provenance": {"file_index": file_block},
        "columns": columns,
    }
    return codec._write_store(path, header, blocks)


def install() -> None:
    """Route ``table.ccs`` through :func:`write_table_store` (merged tables included)."""

    codec.write_decode_store = write_table_store


# ---------------------------------------------------------------------------
# Native-merge vectors
# ---------------------------------------------------------------------------


def _platform() -> str:
    return "windows" if sys.platform == "win32" else "linux"


def emit_merge_vectors(out: Path) -> list[str]:
    """Write each merge case's input decode stores and the oracle's merged columns."""

    import ibt_reader
    import workflow

    base = workflow._load_analysis_telemetry([str(FIXTURE)], None)
    written = []
    for case, tables in merge_cases(base).items():
        folder = out / _platform() / case
        folder.mkdir(parents=True, exist_ok=True)
        for index, table in enumerate(tables):
            _DECODE_STORE(folder / f"file{index}.ccs", telemetry=table, sampling_plan=ibt_reader._sampling_plan,
                          target_hz=None, producer=PRODUCER)
        merged = oracle_merge(tables)
        write_merge_store(folder / "merged.ccs", telemetry=merged, sampling_plan=ibt_reader._sampling_plan,
                          target_hz=None, producer=PRODUCER)
        sink = codec.ColumnSink()
        document = {
            "codec": codec.CODEC_VERSION,
            "case": case,
            "python": sys.version.split()[0],
            "preferred": list(workflow.ANALYSIS_CHANNELS),
            "inputs": [f"file{index}.ccs" for index in range(len(tables))],
            "channels": list(merged["samples"]),
            "sample_count": merged["sample_count"],
            "samples": codec.encode(merged["samples"], codec.Context(), sink),
            "file_index": [item["source_file_index"] for item in merged["sample_provenance"]],
            "record_index": [item["native_record_index"] for item in merged["sample_provenance"]],
        }
        (folder / "merged.json").write_text(codec.dumps(document) + "\n", encoding="utf-8", newline="\n")
        sink.write(folder / "merged.vectors.ccs", PRODUCER)
        written.append(case)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="U1 native-merge vectors")
    parser.add_argument("command", choices=("merge",))
    parser.add_argument("--out", type=Path, default=MERGE_VECTORS)
    parser.add_argument("--check", action="store_true", help="regenerate into a temporary folder and compare")
    arguments = parser.parse_args(argv)
    if sys.version_info[:2] != (3, 12):
        raise SystemExit(f"the oracle is CPython 3.12; this is {sys.version.split()[0]}")
    if not arguments.check:
        print(json.dumps({"merge_cases": emit_merge_vectors(arguments.out)}))
        return 0
    work = Path(tempfile.mkdtemp(prefix="u1-merge-check-"))
    try:
        emit_merge_vectors(work)
        differences = []
        root = work / _platform()
        expected_root = arguments.out / _platform()
        names = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
        names |= {p.relative_to(expected_root).as_posix() for p in expected_root.rglob("*") if p.is_file()}
        for name in sorted(names):
            a, b = expected_root / name, root / name
            if not a.is_file() or not b.is_file() or a.read_bytes() != b.read_bytes():
                differences.append(name)
        for name in differences:
            print(f"differs: {name}")
        print(f"check: {'identical' if not differences else f'{len(differences)} difference(s)'}")
        return 1 if differences else 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())

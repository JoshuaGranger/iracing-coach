"""Regression tables for the Codex Sol 6.1 phase-2 review (``docs/08-rewrite/reviews/
port-phase2-review-codex-sol.md``), registered with ``emit_kernel_vectors.py``.

Each builder takes the fixture's decoded table and returns the table for one
finding; ``EXTRA_CALLS`` adds direct oracle calls on a recorded table where no
analysis path makes them (the recorder is still installed, so each is recorded
under its unit like any other call). Every input here was first run through
the Linux CPython oracle; the Disposition section of the review lists them.

* ``review-big-ints`` (finding 4): ``SessionFlags``, ``PlayerTireCompound`` and
  ``PitSvFlags`` stored as doubles of ``2**63`` and beyond, in an Offline
  Testing session (the one-lap-to-green clearing runs beside them) with a
  ``SessionNum`` beyond ``i64`` in the session list.
* ``review-unicode-text`` (finding 3): text channels written in Arabic-Indic,
  Devanagari, fullwidth and other decimal digits, Unicode spaces and digits that
  are not decimal; ``_dt_series`` called on Unicode digit text directly.
* ``review-huge-rate`` (finding 8): a sample rate of ``4e307`` with no service
  hit, so ``_service_events`` returns before its windows overflow.
* ``review-field-merged`` (finding 1): the ``field-oval`` session split into two
  overlapping files, merged by the oracle, with missing cells in ``Lat``,
  ``Lon``, ``Speed``, ``LapDistPct`` and ``OnPitRoad`` (a merge-stage store).
* ``review-track-id`` (finding 7): a track id list of characters ``repr``
  escapes and characters it keeps; plus (finding 6) ``_lap_trace_payload`` with
  zero and negative bins, on no laps and on the laps.

Finding 2 (mixed float widths) is a native-merge case in ``table_vectors.py``.
"""

from __future__ import annotations

import copy
from typing import Any, Callable

import synthetic_field
import table_vectors

F64, TEXT, BOOL = 5, 0, 1

# --------------------------------------------------------------------------- finding 4


def review_big_ints(table: dict[str, Any]) -> dict[str, Any]:
    count = 300
    table_vectors._resize(table, count)
    table["metadata"]["sim_session_type"] = "Offline Testing"
    table["session_info"]["SessionInfo"]["Sessions"] = [
        {"SessionNum": 0, "SessionType": "Offline Testing"},
        {"SessionNum": 1, "SessionType": "Race"},
        {"SessionNum": 2.0**64, "SessionType": "Race"},
    ]
    big = [
        2.0**63,  # int 9223372036854775808
        -(2.0**63) - 2048.0,
        2.0**70 + 2.0**14,  # a caution bit (0x4000) in the low 64 bits
        -(2.0**70),
        2.0**64 + 2.0**16,  # the black-flag bit (0x10000)
        1.7976931348623157e308,
        -(2.0**63),  # exactly i64::MIN: within i64
        2.0**84 + 2.0**31,
    ]
    flags = table_vectors._offline_flags(count)
    values: list[float] = []
    for index in range(count):
        if index % 9 == 4:
            values.append(big[(index // 9) % len(big)])
        else:
            values.append(float(flags[index]))
    table_vectors._add(table, "SessionFlags", F64, values, "irsdk_Flags")
    numbers = [0.0 if index < 150 else (2.0**64 if index < 220 else 1.0) for index in range(count)]
    table_vectors._add(table, "SessionNum", F64, numbers, "")
    table_vectors._add(table, "PlayerTireCompound", F64, [1.0] * (count - 1) + [2.0**63], "")
    stall = [100 <= index < 112 or 250 <= index < 256 for index in range(count)]
    table_vectors._add(table, "PlayerCarInPitStall", BOOL, stall, "")
    service = [0.0] * count
    service[101] = 2.0**70
    service[104] = float(0x11)
    service[106] = -(2.0**66)
    service[252] = 2.0**63 + 2048.0
    service[253] = float(0x40)
    table_vectors._add(table, "PitSvFlags", F64, service, "irsdk_PitSvFlags")
    return table


# --------------------------------------------------------------------------- finding 3


_SCRIPTS = (0x0660, 0x0966, 0xFF10, 0x0E50, 0x1D7CE, 0x06F0)


def _digits(text: str, script: int) -> str:
    return "".join(chr(script + ord(c) - 48) if c.isdigit() else c for c in text)


def review_unicode_text(table: dict[str, Any]) -> dict[str, Any]:
    count = 300
    table_vectors._resize(table, count)
    times = [f"{index / 60.0:.6f}" for index in range(count)]
    texts = [_digits(time, _SCRIPTS[index % len(_SCRIPTS)]) for index, time in enumerate(times)]
    texts[17] = " " + texts[17] + "　"
    texts[40] = texts[40].replace(".", "·")  # not a decimal point: ValueError, a gap
    table_vectors._add(table, "SessionTime", TEXT, texts, "s", count=32)
    air = ["٢١.٥", "२२", " ٢٣ ", "²", "½", "1_٠",
           "２０.５", "​21", "\x1c2١", "٢١e٠"]
    table_vectors._add(table, "AirTemp", TEXT, [air[index % len(air)] for index in range(count)], "C", count=32)
    bias = [_digits(f"{0.5 + index / 1000:.3f}", _SCRIPTS[index % 3]) for index in range(count)]
    table_vectors._add(table, "dcBrakeBias", TEXT, bias, "", count=16)
    table_vectors._add(table, "PlayerTireCompound", TEXT, ["٢"] * count, "", count=4)
    table_vectors._add(table, "SessionFlags", TEXT,
                       ["٥١٢" if index % 3 else "²" for index in range(count)], "", count=8)
    return table


def _unicode_calls(table: Any, analysis: dict[str, Any]) -> None:
    import analysis_engine

    for times in (["٠", "١"], ["²", "١"], ["१.॥", "२", "３"]):
        analysis_engine._dt_series(times, 1.0)


# --------------------------------------------------------------------------- finding 8


def review_huge_rate(table: dict[str, Any]) -> dict[str, Any]:
    table["sample_rate_hz"] = 4e307
    table["metadata"]["sample_rate"] = 4e307
    return table


# --------------------------------------------------------------------------- finding 1


def _slice(table: dict[str, Any], first: int, count: int, drop: tuple[str, ...] = ()) -> dict[str, Any]:
    part = copy.deepcopy(table)
    samples = part["samples"]
    for name in list(samples):
        samples[name] = samples[name][first:first + count]
    for name in drop:
        del samples[name]
        for key in ("variables", "available_variables"):
            part[key] = [entry for entry in part[key] if entry["name"] != name]
    part["sample_indices"] = list(range(count))
    part["sample_provenance"] = [dict(item, native_record_index=index)
                                 for index, item in enumerate(part["sample_provenance"][first:first + count])]
    part["sample_count"] = count
    part["source_record_count"] = count
    part["metadata"]["record_count"] = count
    return part


def review_field_merged(table: dict[str, Any]) -> dict[str, Any]:
    whole = synthetic_field.field_oval(table)
    count = whole["sample_count"]
    half = count // 2
    merged = table_vectors.oracle_merge([
        _slice(whole, 0, half + 40),
        _slice(whole, half, count - half, drop=("RRodometer",)),
    ])
    samples = merged["samples"]
    for index in range(len(samples["Lat"])):
        if index % 211 == 7:
            samples["Lat"][index] = None
        if index % 173 == 11:
            samples["Lon"][index] = None
        if index % 157 == 3:
            samples["Speed"][index] = None
        if index % 401 == 19:
            samples["LapDistPct"][index] = None
        if index % 263 == 5:
            samples["OnPitRoad"][index] = None
    return merged


# --------------------------------------------------------------------------- findings 7 and 6


def review_track_id(table: dict[str, Any]) -> dict[str, Any]:
    weekend = table["session_info"].setdefault("WeekendInfo", {})
    weekend["TrackID"] = ["⁦", "­", "͸", "é", "\U0001f600", "‎", "\U000e0001", "\x7f",
                          "a　b", "١", "﻿", "؜", " ", "Z"]
    weekend["TrackName"] = "École ⁧X⁩"
    return table


def _bins_calls(table: Any, analysis: dict[str, Any]) -> None:
    import analysis_engine

    laps = [lap for lap in analysis.get("laps") or () if isinstance(lap, dict)]
    for laps_arg, bins in (([], 0), ([], -3), (laps, -3), (laps[:1], 1), (laps, 0)):
        try:
            analysis_engine._lap_trace_payload(table, laps_arg, maximum_bins=bins)
        except ZeroDivisionError:
            pass  # recorded as the oracle's exception


BUILDERS: tuple[tuple[str, Callable[[dict[str, Any]], dict[str, Any]]], ...] = (
    ("review-big-ints", review_big_ints),
    ("review-unicode-text", review_unicode_text),
    ("review-huge-rate", review_huge_rate),
    ("review-field-merged", review_field_merged),
    ("review-track-id", review_track_id),
)

EXTRA_CALLS: dict[str, Callable[[Any, dict[str, Any]], None]] = {
    "review-unicode-text": _unicode_calls,
    "review-track-id": _bins_calls,
}

"""Read archived SDK field samples without changing player IBT measurements."""
from __future__ import annotations

import heapq
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from ibt_reader import _parse_session_info
from field_results import build_field_results, integer

MAX_SEGMENTS = 256  # Leave headroom for the worker and its IBT files on Linux.
FIELD_CHANNELS = frozenset(("SessionTime", "SessionNum", "SessionUniqueID", "SessionState", "SessionFlags",
                           "PlayerCarIdx", "CarIdxLap", "CarIdxLapCompleted", "CarIdxLapDistPct",
                           "CarIdxPosition", "CarIdxClassPosition", "CarIdxOnPitRoad", "CarIdxTrackSurface",
                           "CarIdxLastLapTime", "CarIdxBestLapTime"))


def identity(info: Mapping[str, Any], channels: Mapping[str, Any]) -> tuple[int | None, int | None, int | None]:
    drivers = info.get("DriverInfo") or {}
    player = integer(drivers.get("DriverCarIdx"))
    user = next((integer(d.get("UserID")) for d in drivers.get("Drivers") or ()
                 if isinstance(d, Mapping) and integer(d.get("CarIdx")) == player), None)
    return user, integer((info.get("WeekendInfo") or {}).get("SubSessionID")), integer(channels.get("SessionNum"))


def _rows(path: Path, expected: tuple[int | None, int | None, int | None], fingerprint=None):
    info = None
    previous_capture = -1
    before = path.stat()
    if fingerprint is not None and (before.st_size, before.st_mtime_ns) != (fingerprint["size"], fingerprint["modified_ns"]):
        raise ValueError("Field recording changed after SHA-256 verification")
    digest = hashlib.sha256()
    complete = False
    with path.open("rb") as stream:
        for index in range(1_000_000):
            raw = stream.readline(1024 * 1024 + 1)
            if not raw:
                complete = True
                break
            digest.update(raw)
            if len(raw) > 1024 * 1024 or not raw.endswith(b"\n"):
                raise ValueError("Invalid or incomplete field recording line")
            row = json.loads(raw, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
            if row.get("schema_version") != 1 or not isinstance(row.get("channels"), dict):
                raise ValueError("Incompatible field recording schema")
            if "session_info" in row:
                info = _parse_session_info(row["session_info"].encode("utf-8"), path)
            channels = row["channels"]
            if info is None or identity(info, channels) != expected:
                raise ValueError("Field recording session identity mismatch")
            capture = integer(row.get("captured_unix_ms"))
            if capture is None or capture < previous_capture or integer(row.get("tick")) is None:
                raise ValueError("Invalid field capture ordering")
            previous_capture = capture
            time = channels.get("SessionTime")
            if isinstance(time, bool) or not isinstance(time, (int, float)) or not math.isfinite(time) or time < 0:
                raise ValueError("Invalid field session time")
            yield (capture, path.name, index, row, info)
    if not complete:
        raise ValueError("Field recording row limit exceeded")
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or (
            fingerprint is not None and digest.hexdigest() != fingerprint["sha256"]):
        raise ValueError("Field recording changed during analysis")


def apply_field_recordings(analysis: dict[str, Any], paths: Sequence[Path], session_info: Mapping[str, Any],
                           metadata: Mapping[str, Any], *, field_fingerprints=()) -> None:
    if not paths:
        return
    if len(paths) > MAX_SEGMENTS:
        raise ValueError(f"Field recording segment limit exceeded ({MAX_SEGMENTS}); no partial analysis was produced")
    fingerprints = {str(Path(item["path"]).resolve()): item for item in field_fingerprints}
    if field_fingerprints and any(str(path.resolve()) not in fingerprints for path in paths):
        raise ValueError("Field recording is missing its verified fingerprint")
    expected = identity(session_info, {"SessionNum": metadata.get("sim_session_num")})
    if expected[0] is None or not expected[1] or expected[1] <= 0 or expected[2] is None:
        raise ValueError("Field recordings require an identified online session")
    channels: dict[str, list] = {}
    infos = session_info
    count = 0
    previous_capture = None
    # Merge individually capture-ordered segments without materializing all JSON rows.
    pending = None
    discontinuity = False
    roster = {}
    identities = {}
    ambiguous = set()
    observed_infos = {}

    def observe_roster(info, source):
        if observed_infos.get(source) is info:
            return
        observed_infos[source] = info
        for driver in (info.get("DriverInfo") or {}).get("Drivers") or ():
            if not isinstance(driver, Mapping):
                continue
            car = integer(driver.get("CarIdx"))
            if car is None or not 0 <= car < 64:
                continue
            user = integer(driver.get("UserID"))
            if car in identities and identities[car] != user:
                ambiguous.add(car)
            identities.setdefault(car, user)
            roster[car] = driver

    observe_roster(session_info, "ibt")

    def append(row, info, gap=False):
        nonlocal count, infos
        values = {key: value for key, value in row["channels"].items() if key in FIELD_CHANNELS}
        if gap:
            values = {"SessionTime": None, "SessionNum": expected[2]}
        for key in values:
            if key not in channels:
                channels[key] = [None] * count
        for key, series in channels.items():
            series.append(values.get(key))
        count += 1
        infos = info

    for capture, source_name, _, row, info in heapq.merge(*(_rows(path, expected, fingerprints.get(str(path.resolve()))) for path in sorted(paths))):
        observe_roster(info, source_name)
        if pending is not None and capture == previous_capture:
            # Exact duplicate is harmless; conflicting simultaneous samples break continuity.
            if row["tick"] != pending[0]["tick"] or row["channels"] != pending[0]["channels"]:
                discontinuity = True
            continue
        if pending is not None:
            append(*pending, gap=discontinuity)
        if previous_capture is not None and capture - previous_capture > 2000:
            append(row, info, gap=True)
        pending = row, info
        previous_capture = capture
        discontinuity = False
    if pending is not None:
        append(*pending, gap=discontinuity)
    infos = dict(infos)
    infos["DriverInfo"] = {**(infos.get("DriverInfo") or {}), "Drivers": [driver for car, driver in sorted(roster.items()) if car not in ambiguous]}
    result = build_field_results(channels, infos, metadata, (), ())
    old = (analysis.get("field_pace") or {}).get("results") or {}
    own = next((r for r in old.get("cars", ()) if r.get("is_player")), None)
    if own is not None:
        def with_player_evidence(row):
            if not row.get("is_player"):
                return row
            merged = dict(row)
            # IBT lap summaries win when available. Missing IBT progression must
            # not erase independently recorded SDK history for the same player.
            if own.get("laps"):
                return own
            else:
                merged["fuel_used_l"] = own.get("fuel_used_l")
                merged["tires_taken"] = own.get("tires_taken")
            return merged
        result["cars"] = [with_player_evidence(r) for r in result["cars"]]
        result["cars"].sort(key=lambda r: (r["position"] is None, r["position"] or 9999, r["car_index"]))
    # Preserve the IBT's scored snapshot for this exact phase. A field recording can finish
    # before the final results are published; its last observed position must not erase them.
    sessions = (session_info.get("SessionInfo") or {}).get("Sessions") or ()
    phase = next((s for s in sessions if isinstance(s, Mapping) and integer(s.get("SessionNum")) == expected[2]), {})
    snapshots = {integer(item.get("CarIdx")): item for item in phase.get("ResultsPositions") or () if isinstance(item, Mapping)}
    positions = [integer(item.get("Position")) for item in snapshots.values()]
    offset = 1 if 0 in positions else 0
    for car in result["cars"]:
        snapshot = snapshots.get(car["car_index"], {})
        position = integer(snapshot.get("Position"))
        if position is not None and position >= 0 and (offset or position > 0):
            car["position"] = position + offset
            car["position_source"] = "session_results_snapshot"
    result["cars"].sort(key=lambda r: (r["position"] is None, r["position"] or 9999, r["car_index"]))
    if ambiguous:
        result["limitations"].append("Cars whose SDK slot changed driver were omitted; earlier samples cannot be safely attributed.")
    result["recording_source"] = "companion_field_recording"
    result["recorded_sample_count"] = count
    analysis.setdefault("field_pace", {})["results"] = result

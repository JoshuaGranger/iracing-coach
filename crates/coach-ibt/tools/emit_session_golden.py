"""Emit the Python oracle's view of IBT headers and session info for coach-ibt parity tests.

Usage::

    python emit_session_golden.py SOURCE [SOURCE ...] OUT_DIR

Each SOURCE is an ``.ibt`` recording, a ``.cases`` file of session-info texts, or a directory
searched recursively for both. One ``<name>.golden.json`` is written per input into OUT_DIR.

Everything recorded comes from ``ibt_reader`` itself (``_read_layout``, ``_read_edge_scalar``,
``_parse_session_info``, ``_extract_summary`` and its ``_as_*`` conversions), with the in-house
YAML subset parser forced on as in the worker container (no PyYAML). Two hooks observe the
parser without changing its results: every quoted or flow value handed to ``_yaml_scalar`` is
listed, and values Python accepts but the Rust port deliberately rejects (sets, bytes, complex
numbers, Ellipsis, non-string dict keys, lone surrogates, non-string quoted keys) are marked
``unsupported``. Absolute paths never reach the output.

A ``.cases`` file holds session-info texts separated by marker lines
``%%% <name> [start_date=<int>] [start_time=<float>] [end_time=<float>]``; the options form the
disk subheader handed to ``_extract_summary`` (defaults 0, so the summary falls back to
``SessionStartTime`` or the fixed mtime ``CASE_MTIME``). Bodies are raw bytes.
"""

from __future__ import annotations

import hashlib
import json
import platform
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

REPOSITORY = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPOSITORY / "iracing-coach" / "skills" / "analyze-iracing-race" / "scripts"))

import ibt_reader  # noqa: E402

GOLDEN_VERSION = 1
CASE_MTIME = 1_500_000_000.25
CASE_MARKER = b"%%% "

# The worker image has no PyYAML; parse with the in-house subset exactly as it does.
ibt_reader._YAML_IMPORT_ATTEMPTED = True
ibt_reader._YAML_MODULE = None

_observed: dict[str, Any] = {}
_original_yaml_scalar = ibt_reader._yaml_scalar
_original_subset = ibt_reader._parse_iracing_yaml_subset


def _reset_observations() -> None:
    _observed.clear()
    _observed.update({"literals": [], "unsupported": None, "text": None})


def _mark_unsupported(reason: str) -> None:
    if _observed.get("unsupported") is None:
        _observed["unsupported"] = reason


def _is_literal_text(value: str) -> bool:
    quoted = value[0:1] in {"'", '"'} and value[-1:] == value[0:1]
    flow = value[0:1] in {"[", "{"} and value[-1:] in {"]", "}"}
    return quoted or flow


def _observing_yaml_scalar(text: str) -> Any:
    value = text.strip()
    result = _original_yaml_scalar(text)
    if _is_literal_text(value):
        _observed["literals"].append(value)
        if sys._getframe(1).f_code.co_name == "_split_yaml_pair" and not isinstance(result, str):
            _mark_unsupported("quoted key")
    return result


def _unsupported_reason(value: Any) -> str | None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                return "non-string dict key"
            reason = _unsupported_reason(key) or _unsupported_reason(item)
            if reason:
                return reason
        return None
    if isinstance(value, (list, tuple)):
        for item in value:
            reason = _unsupported_reason(item)
            if reason:
                return reason
        return None
    if isinstance(value, str):
        return "lone surrogate" if any(0xD800 <= ord(c) <= 0xDFFF for c in value) else None
    if value is None or isinstance(value, (bool, int, float)):
        return None
    return type(value).__name__


def _observing_subset(text: str, path: Path) -> dict[str, Any]:
    _observed["text"] = text
    parsed = _original_subset(text, path)
    reason = _unsupported_reason(parsed)
    if reason:
        _mark_unsupported(reason)
    return parsed


ibt_reader._yaml_scalar = _observing_yaml_scalar
ibt_reader._parse_iracing_yaml_subset = _observing_subset


def tag(value: Any) -> Any:
    """JSON with exact numbers: ints as decimal text, floats as Python repr."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return {"int": str(value)}
    if isinstance(value, float):
        return {"float": repr(value)}
    if isinstance(value, str):
        if any(0xD800 <= ord(c) <= 0xDFFF for c in value):
            return {"unsupported": "lone surrogate"}
        return value
    if isinstance(value, (list, tuple)):
        return [tag(item) for item in value]
    if isinstance(value, Mapping):
        return {"map": [[str(key), tag(item)] for key, item in value.items()]}
    return {"unsupported": type(value).__name__}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _error_record(exc: BaseException, path: Path) -> dict[str, Any]:
    message = str(exc)
    # Longest first: the relative spelling is a suffix of the resolved one.
    for spelling in sorted({str(path), str(path.resolve())}, key=len, reverse=True):
        message = message.replace(spelling, "<file>")
    record: dict[str, Any] = {"class": type(exc).__name__, "message": message}
    line = re.search(r"line (\d+)", message)
    record["line"] = int(line.group(1)) if line else None
    truncated = re.search(r"requires bytes \[(\d+), (\d+)\), but the file contains only (\d+) bytes", message)
    if truncated:
        record["start"], record["end"], record["available"] = (int(group) for group in truncated.groups())
    return record


def _leaves(prefix: str, value: Any, out: list[list[Any]]) -> None:
    def join(segment: str) -> str:
        return f"{prefix}.{segment}" if prefix else segment

    if isinstance(value, Mapping):
        for key, item in value.items():
            _leaves(join(str(key)), item, out)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _leaves(join(str(index)), item, out)
    elif value is not None:
        out.append([prefix, str(value), tag(value)])


def extracts(info: Mapping[str, Any]) -> dict[str, Any]:
    """The crate's typed extracts, computed with ibt_reader's own conversions."""
    as_mapping, as_list = ibt_reader._as_mapping, ibt_reader._as_list
    as_int, as_bool, as_text = ibt_reader._as_int, ibt_reader._as_bool, ibt_reader._as_text

    weekend = as_mapping(info.get("WeekendInfo"))
    options = as_mapping(weekend.get("WeekendOptions"))
    weekend_info = {
        "track_name": as_text(weekend.get("TrackName")),
        "track_display_name": as_text(weekend.get("TrackDisplayName")),
        "track_config_name": as_text(weekend.get("TrackConfigName")),
        "track_id": as_int(weekend.get("TrackID")),
        "track_length": as_text(weekend.get("TrackLength")),
        "series_id": as_int(weekend.get("SeriesID")),
        "season_id": as_int(weekend.get("SeasonID")),
        "session_id": as_int(weekend.get("SessionID")),
        "subsession_id": as_int(weekend.get("SubSessionID")),
        "race_week": as_int(weekend.get("RaceWeek")),
        "event_type": as_text(weekend.get("EventType")),
        "category": as_text(weekend.get("Category")),
        "official": as_bool(weekend.get("Official")),
        "is_fixed_setup": as_bool(options.get("IsFixedSetup")),
    }
    sessions = []
    for item in as_list(as_mapping(info.get("SessionInfo")).get("Sessions")):
        entry = as_mapping(item)
        sessions.append(
            {
                "session_num": as_int(entry.get("SessionNum")),
                "session_type": as_text(entry.get("SessionType")),
                "session_name": as_text(entry.get("SessionName")),
            }
        )
    driver_info = as_mapping(info.get("DriverInfo"))
    car_idx = as_int(driver_info.get("DriverCarIdx"))
    driver = None
    if car_idx is not None:
        for candidate in as_list(driver_info.get("Drivers")):
            entry = as_mapping(candidate)
            if as_int(entry.get("CarIdx")) == car_idx:
                driver = {
                    "car_idx": car_idx,
                    "user_id": as_int(entry.get("UserID")),
                    "user_name": as_text(entry.get("UserName")),
                    "irating": as_int(entry.get("IRating")),
                    "car_id": as_int(entry.get("CarID")),
                    "car_path": as_text(entry.get("CarPath")),
                    "car_screen_name": as_text(entry.get("CarScreenName")),
                }
                break
    modified = driver_info.get("DriverSetupIsModified")
    if modified is None:
        modified = driver_info.get("DriverSetupModified")
    driver_setup = {
        "name": as_text(driver_info.get("DriverSetupName")),
        "is_modified": as_bool(modified),
        "load_type_name": as_text(driver_info.get("DriverSetupLoadTypeName")),
        "load_type": as_int(driver_info.get("DriverSetupLoadType")),
    }
    car_setup = None
    raw_setup = info.get("CarSetup")
    if isinstance(raw_setup, Mapping):
        leaves: list[list[Any]] = []
        _leaves("", raw_setup, leaves)
        car_setup = {"update_count": tag(as_int(raw_setup.get("UpdateCount"))), "leaves": leaves}
    return {
        "weekend": tag(weekend_info),
        "sessions": [tag(entry) for entry in sessions],
        "driver": tag(driver) if driver is not None else None,
        "driver_setup": tag(driver_setup),
        "car_setup": car_setup,
    }


def _session_record(info: Mapping[str, Any]) -> dict[str, Any]:
    text = _observed["text"]
    return {
        "session_info_text_sha256": None if text is None else _sha256(text.encode("utf-8")),
        "unsupported": _observed["unsupported"],
        "literals": list(_observed["literals"]),
        "session_info": tag(info),
        "extracts": extracts(info),
    }


def emit_ibt(path: Path, label: str) -> dict[str, Any]:
    _reset_observations()
    record: dict[str, Any] = {
        "golden": GOLDEN_VERSION,
        "kind": "ibt",
        "source": label,
        "python": platform.python_version(),
        "platform": sys.platform,
    }
    try:
        layout = ibt_reader._read_layout(path)
    except Exception as exc:  # every failure class is part of the golden
        record.update({"outcome": "error", "error": _error_record(exc, path)})
        return record
    header = layout.header
    with path.open("rb") as handle:
        handle.seek(header["session_info_offset"])
        session_bytes = handle.read(header["session_info_length"])
        by_name = {variable.name: variable for variable in layout.variables}
        edges = {
            variable.name: tag(ibt_reader._read_edge_scalar(handle, layout, by_name, variable.name))
            for variable in layout.variables
            if variable.count == 1 and variable.type_code != 0
        }
    record.update(
        {
            "outcome": "ok",
            "file_size": layout.file_size,
            "header": {key: value for key, value in header.items() if key != "buffers"},
            "buffers": [[item["tick_count"], item["buffer_offset"]] for item in header["buffers"]],
            "disk": tag(layout.disk),
            "buffer_offset": layout.buffer_offset,
            "record_count": layout.record_count,
            "variables": [
                [v.name, v.description, v.unit, v.type_code, v.offset, v.count, v.count_as_time]
                for v in layout.variables
            ],
            "session_info_sha256": _sha256(session_bytes),
            **_session_record(layout.session_info),
            "summary": tag(layout.summary),
            "edges": edges,
        }
    )
    return record


def split_cases(data: bytes) -> list[tuple[str, dict[str, str], bytes]]:
    cases: list[tuple[str, dict[str, str], bytes]] = []
    starts = [0] if data.startswith(CASE_MARKER) else []
    starts += [index + 1 for index in range(len(data)) if data.startswith(b"\n" + CASE_MARKER, index)]
    for position, start in enumerate(starts):
        line_end = data.find(b"\n", start)
        line_end = len(data) if line_end < 0 else line_end
        words = data[start + len(CASE_MARKER) : line_end].decode("ascii").split()
        options = dict(word.split("=", 1) for word in words[1:])
        end = starts[position + 1] if position + 1 < len(starts) else len(data)
        cases.append((words[0], options, data[line_end + 1 : end]))
    return cases


def emit_cases(path: Path, label: str) -> dict[str, Any]:
    data = path.read_bytes()
    record: dict[str, Any] = {
        "golden": GOLDEN_VERSION,
        "kind": "cases",
        "source": label,
        "source_sha256": _sha256(data),
        "python": platform.python_version(),
        "platform": sys.platform,
        "case_mtime": tag(CASE_MTIME),
        "cases": [],
    }
    for name, options, body in split_cases(data):
        _reset_observations()
        case: dict[str, Any] = {"name": name, "options": options}
        try:
            info = ibt_reader._parse_session_info(body, path)
        except Exception as exc:  # every failure class is part of the golden
            case.update({"outcome": "error", "error": _error_record(exc, path)})
            record["cases"].append(case)
            continue
        disk = {
            "session_start_date": int(options.get("start_date", "0")),
            "session_start_time": float(options.get("start_time", "0")),
            "session_end_time": float(options.get("end_time", "0")),
            "session_lap_count": 0,
            "session_record_count": 0,
        }
        summary = ibt_reader._extract_summary(info, disk, CASE_MTIME, None, None)
        case.update({"outcome": "ok", **_session_record(info), "summary": tag(summary)})
        record["cases"].append(case)
    return record


def _inputs(source: Path) -> list[tuple[Path, Path]]:
    if source.is_dir():
        found = [p for p in source.rglob("*") if p.is_file() and p.suffix.lower() in {".ibt", ".cases"}]
        return [(path, path.relative_to(source)) for path in sorted(found)]
    return [(source, Path(source.name))]


def _label(path: Path, relative: Path) -> str:
    try:
        return path.resolve().relative_to(REPOSITORY).as_posix()
    except ValueError:
        return relative.as_posix()


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__, file=sys.stderr)
        return 2
    out_dir = Path(argv[-1])
    out_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for source in map(Path, argv[1:-1]):
        if not source.exists():
            print(f"missing source: {source}", file=sys.stderr)
            return 2
        for path, relative in _inputs(source):
            label = _label(path, relative)
            if path.suffix.lower() == ".cases":
                record = emit_cases(path, label)
                name = f"{path.stem}.cases.golden.json"
            else:
                record = emit_ibt(path, label)
                name = f"{path.stem}.golden.json"
            # Same file name in different folders: keep both.
            if len(relative.parts) > 1:
                name = "__".join(relative.parent.parts) + "__" + name
            text = json.dumps(record, indent=1, ensure_ascii=True) + "\n"
            (out_dir / name).write_text(text, encoding="utf-8", newline="\n")
            written += 1
    print(f"wrote {written} golden file(s) to {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

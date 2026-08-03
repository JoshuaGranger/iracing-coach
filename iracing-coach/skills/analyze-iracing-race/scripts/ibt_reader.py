"""Read iRacing disk telemetry (``.ibt``) files without modifying them.

The public API is intentionally small:

``scan_ibt(path)``
    Parse the file layout, session YAML, variable catalogue, and a few scalar
    identity channels.  Sample buffers are not loaded.

``discover_sessions(root, latest_only=False)``
    Group files by SubSessionID and simulator session number.  With
    ``latest_only=True``, return only the newest Race group.

``load_telemetry(path, channels=None, target_hz=20)``
    Return selected channels as a column-oriented ``samples`` mapping.  Array
    variables remain lists inside each channel's sample list.

Only read-only file handles and read-only memory maps are used. PyYAML is used
when available; a strict parser for iRacing's mapping/list/scalar YAML subset
keeps the reader self-contained when it is not.
"""

from __future__ import annotations

import ast
import datetime as _datetime
import importlib
import math
import mmap
import os
from pathlib import Path
import re
import struct
import sys
from dataclasses import dataclass
from typing import Any, Iterable, Iterator, Mapping, Sequence


__all__ = [
    "IbtError",
    "IbtBoundsError",
    "IbtChannelError",
    "IbtDependencyError",
    "IbtFormatError",
    "IbtTruncatedError",
    "IbtTypeError",
    "IbtYamlError",
    "discover_sessions",
    "iter_telemetry_chunks",
    "load_telemetry",
    "profile_telemetry",
    "scan_ibt",
]


class IbtError(Exception):
    """Base class for telemetry-reader failures."""


class IbtFormatError(IbtError):
    """The file is not a structurally valid iRacing telemetry file."""


class IbtTruncatedError(IbtFormatError):
    """A declared file region extends past the available bytes."""


class IbtBoundsError(IbtFormatError):
    """A header or variable offset is outside its containing structure."""


class IbtTypeError(IbtFormatError):
    """A telemetry variable uses an unsupported or invalid SDK type."""


class IbtYamlError(IbtFormatError):
    """The embedded iRacing session YAML could not be decoded or parsed."""


class IbtDependencyError(IbtYamlError):
    """The optional safe YAML parser is unavailable."""


class IbtChannelError(IbtError):
    """A requested telemetry channel does not exist in the file."""


# irsdk_header: 12 int32 values followed by four irsdk_varBuf structures,
# each containing four int32 values.  iRacing currently writes this 112-byte
# layout to disk.
_HEADER = struct.Struct("<28i")
_HEADER_SIZE = _HEADER.size
_DISK_SUBHEADER = struct.Struct("<qddii")
_DISK_SUBHEADER_OFFSET = _HEADER_SIZE
_VAR_HEADER = struct.Struct("<iiiB3x32s64s32s")

_MAX_VARIABLES = 16_384
_MAX_SESSION_INFO_BYTES = 64 * 1024 * 1024
_MAX_TICK_RATE = 10_000
_MAX_TELEMETRY_CHUNK_SIZE = 8_192
_MIN_REASONABLE_EPOCH = 946_684_800.0  # 2000-01-01 UTC
_MAX_REASONABLE_EPOCH = 4_102_444_800.0  # 2100-01-01 UTC

_TYPE_INFO = {
    0: ("char", 1, None),
    1: ("bool", 1, "?"),
    2: ("int", 4, "i"),
    3: ("bitfield", 4, "I"),
    4: ("float", 4, "f"),
    5: ("double", 8, "d"),
}


@dataclass(frozen=True)
class _Variable:
    type_code: int
    offset: int
    count: int
    count_as_time: bool
    name: str
    description: str
    unit: str

    @property
    def type_name(self) -> str:
        return _TYPE_INFO[self.type_code][0]

    @property
    def byte_size(self) -> int:
        return _TYPE_INFO[self.type_code][1] * self.count

    def public(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "unit": self.unit,
            "type": self.type_name,
            "type_code": self.type_code,
            "offset": self.offset,
            "count": self.count,
            "count_as_time": self.count_as_time,
            "byte_size": self.byte_size,
        }


@dataclass
class _Layout:
    path: Path
    file_size: int
    mtime: float
    header: dict[str, Any]
    disk: dict[str, Any]
    session_info: dict[str, Any]
    variables: list[_Variable]
    buffer_offset: int
    buffer_length: int
    record_count: int
    summary: dict[str, Any]


def _path_label(path: Path) -> str:
    return str(path)


def _checked_region(
    offset: int,
    length: int,
    file_size: int,
    label: str,
    path: Path,
) -> tuple[int, int]:
    if offset < 0 or length < 0:
        raise IbtBoundsError(
            f"{_path_label(path)}: {label} has a negative offset or length "
            f"(offset={offset}, length={length})"
        )
    end = offset + length
    if end > file_size:
        raise IbtTruncatedError(
            f"{_path_label(path)}: {label} requires bytes [{offset}, {end}), "
            f"but the file contains only {file_size} bytes"
        )
    return offset, end


def _regions_overlap(
    first_offset: int,
    first_length: int,
    second_offset: int,
    second_length: int,
) -> bool:
    if first_length == 0 or second_length == 0:
        return False
    return (
        first_offset < second_offset + second_length
        and second_offset < first_offset + first_length
    )


def _read_exact_at(
    handle: Any,
    offset: int,
    length: int,
    file_size: int,
    label: str,
    path: Path,
) -> bytes:
    _checked_region(offset, length, file_size, label, path)
    handle.seek(offset)
    data = handle.read(length)
    if len(data) != length:
        raise IbtTruncatedError(
            f"{_path_label(path)}: short read for {label}; expected {length} "
            f"bytes at offset {offset}, received {len(data)}"
        )
    return data


def _decode_c_string(value: bytes) -> str:
    value = value.split(b"\x00", 1)[0]
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        # Old content can contain Windows-1252 driver/setup names.
        return value.decode("cp1252", errors="replace")


_YAML_MODULE: Any = None
_YAML_IMPORT_ATTEMPTED = False


def _load_yaml_module() -> Any:
    global _YAML_IMPORT_ATTEMPTED, _YAML_MODULE
    if _YAML_IMPORT_ATTEMPTED:
        return _YAML_MODULE

    _YAML_IMPORT_ATTEMPTED = True
    errors: list[str] = []

    def usable(module: Any) -> bool:
        return callable(getattr(module, "safe_load", None))

    # This file is <plugin>/skills/analyze-iracing-race/scripts/ibt_reader.py.
    vendor = Path(__file__).resolve().parents[3] / "vendor"
    if vendor.is_dir():
        vendor_text = str(vendor)
        if vendor_text not in sys.path:
            sys.path.insert(0, vendor_text)
        try:
            candidate = importlib.import_module("yaml")
            if usable(candidate):
                _YAML_MODULE = candidate
                return _YAML_MODULE
            errors.append("bundled yaml module does not provide safe_load")
        except Exception as exc:  # dependency/ACL/import failures need context
            errors.append(f"bundled PyYAML: {exc}")

        # An empty/incomplete vendor directory can be imported as a namespace
        # package.  Remove that unusable module and path before trying the
        # runtime installation.
        sys.modules.pop("yaml", None)
        try:
            sys.path.remove(vendor_text)
        except ValueError:
            pass
        importlib.invalidate_caches()

    try:
        candidate = importlib.import_module("yaml")
        if usable(candidate):
            _YAML_MODULE = candidate
            return _YAML_MODULE
        errors.append("runtime yaml module does not provide safe_load")
    except Exception as exc:
        errors.append(f"runtime PyYAML: {exc}")

    return None


_INTEGER_PATTERN = re.compile(r"^[+-]?(?:0|[1-9][0-9]*)$")
_FLOAT_PATTERN = re.compile(
    r"^[+-]?(?:(?:[0-9]+\.[0-9]*)|(?:[0-9]*\.[0-9]+)|(?:[0-9]+[eE][+-]?[0-9]+)|(?:[0-9]+\.[0-9]*[eE][+-]?[0-9]+))$"
)


def _yaml_scalar(text: str) -> Any:
    value = text.strip()
    if value in {"", "~", "null", "Null", "NULL"}:
        return None
    lowered = value.lower()
    if lowered in {"true", "yes", "on"}:
        return True
    if lowered in {"false", "no", "off"}:
        return False
    if value[0:1] in {"'", '"'} and value[-1:] == value[0:1]:
        try:
            return ast.literal_eval(value)
        except (SyntaxError, ValueError):
            return value[1:-1]
    if value[0:1] in {"[", "{"} and value[-1:] in {"]", "}"}:
        try:
            return ast.literal_eval(value)
        except (SyntaxError, ValueError):
            return value
    if _INTEGER_PATTERN.fullmatch(value):
        try:
            return int(value)
        except ValueError:
            return value
    if _FLOAT_PATTERN.fullmatch(value):
        try:
            number = float(value)
            return number if math.isfinite(number) else value
        except ValueError:
            return value
    return value


def _split_yaml_pair(content: str, path: Path, line_number: int) -> tuple[str, str]:
    quote: str | None = None
    escaped = False
    for index, character in enumerate(content):
        if escaped:
            escaped = False
            continue
        if character == "\\" and quote == '"':
            escaped = True
            continue
        if character in {"'", '"'}:
            quote = None if quote == character else (character if quote is None else quote)
            continue
        if character == ":" and quote is None:
            key = content[:index].strip()
            if not key:
                break
            if key[0:1] in {"'", '"'} and key[-1:] == key[0:1]:
                key = str(_yaml_scalar(key))
            return key, content[index + 1 :].strip()
    raise IbtYamlError(
        f"{_path_label(path)}: unsupported session YAML at line {line_number}: {content!r}"
    )


def _parse_iracing_yaml_subset(text: str, path: Path) -> dict[str, Any]:
    """Parse the deterministic YAML subset emitted by the iRacing SDK.

    The SDK session block uses indentation, mapping keys, sequence items, and
    plain/quoted scalars. Deliberately reject tags, anchors, aliases, and other
    general YAML features rather than interpreting them.
    """

    tokens: list[tuple[int, str, int]] = []
    for line_number, raw_line in enumerate(text.splitlines(), 1):
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        stripped = raw_line.strip()
        if stripped in {"---", "..."}:
            continue
        leading = raw_line[: len(raw_line) - len(raw_line.lstrip(" \t"))]
        if "\t" in leading:
            raise IbtYamlError(
                f"{_path_label(path)}: tabs are not allowed for session YAML indentation (line {line_number})"
            )
        if stripped.startswith(("!", "&", "*")):
            raise IbtYamlError(
                f"{_path_label(path)}: YAML tags, anchors, and aliases are not supported (line {line_number})"
            )
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        tokens.append((indent, raw_line[indent:].rstrip(), line_number))

    if not tokens:
        return {}

    def parse_block(index: int, indent: int) -> tuple[Any, int]:
        if index >= len(tokens):
            return {}, index
        is_list = tokens[index][1].startswith("-")
        container: Any = [] if is_list else {}
        while index < len(tokens):
            current_indent, content, line_number = tokens[index]
            if current_indent < indent:
                break
            if current_indent > indent:
                raise IbtYamlError(
                    f"{_path_label(path)}: unexpected indentation at line {line_number}"
                )
            if is_list:
                if not content.startswith("-"):
                    break
                item_text = content[1:].strip()
                index += 1
                if not item_text:
                    if index < len(tokens) and tokens[index][0] > indent:
                        item, index = parse_block(index, tokens[index][0])
                    else:
                        item = None
                    container.append(item)
                    continue
                if ":" not in item_text:
                    item = _yaml_scalar(item_text)
                    if index < len(tokens) and tokens[index][0] > indent:
                        raise IbtYamlError(
                            f"{_path_label(path)}: scalar list item has an unsupported nested block at line {tokens[index][2]}"
                        )
                    container.append(item)
                    continue
                key, rest = _split_yaml_pair(item_text, path, line_number)
                item_map: dict[str, Any] = {}
                if rest:
                    item_map[key] = _yaml_scalar(rest)
                elif index < len(tokens) and tokens[index][0] > indent:
                    item_map[key], index = parse_block(index, tokens[index][0])
                else:
                    item_map[key] = {}
                if index < len(tokens) and tokens[index][0] > indent:
                    extra, index = parse_block(index, tokens[index][0])
                    if not isinstance(extra, Mapping):
                        raise IbtYamlError(
                            f"{_path_label(path)}: list mapping has a non-mapping continuation"
                        )
                    item_map.update(extra)
                container.append(item_map)
            else:
                if content.startswith("-"):
                    break
                key, rest = _split_yaml_pair(content, path, line_number)
                index += 1
                if rest:
                    container[key] = _yaml_scalar(rest)
                elif index < len(tokens) and (
                    tokens[index][0] > indent
                    or (tokens[index][0] == indent and tokens[index][1].startswith("-"))
                ):
                    container[key], index = parse_block(index, tokens[index][0])
                else:
                    container[key] = {}
        return container, index

    root_indent = tokens[0][0]
    parsed, final_index = parse_block(0, root_indent)
    if final_index != len(tokens):
        _, _, line_number = tokens[final_index]
        raise IbtYamlError(
            f"{_path_label(path)}: could not consume session YAML near line {line_number}"
        )
    if not isinstance(parsed, Mapping):
        raise IbtYamlError(
            f"{_path_label(path)}: embedded session YAML root must be a mapping"
        )
    return dict(parsed)


def _normalise_yaml_value(value: Any) -> Any:
    """Keep safe_load output predictable and JSON-friendly."""
    if isinstance(value, Mapping):
        return {str(key): _normalise_yaml_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalise_yaml_value(item) for item in value]
    if isinstance(value, tuple):
        return [_normalise_yaml_value(item) for item in value]
    if isinstance(value, (_datetime.datetime, _datetime.date)):
        return value.isoformat()
    return value


def _parse_session_info(data: bytes, path: Path) -> dict[str, Any]:
    data = data.split(b"\x00", 1)[0]
    if not data.strip():
        return {}
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1252", errors="replace")

    yaml = _load_yaml_module()
    if yaml is None:
        parsed = _parse_iracing_yaml_subset(text, path)
    else:
        try:
            parsed = yaml.safe_load(text)
        except Exception as exc:
            raise IbtYamlError(
                f"{_path_label(path)}: invalid embedded session YAML: {exc}"
            ) from exc
    if parsed is None:
        return {}
    if not isinstance(parsed, Mapping):
        raise IbtYamlError(
            f"{_path_label(path)}: embedded session YAML root is "
            f"{type(parsed).__name__}, expected a mapping"
        )
    return _normalise_yaml_value(parsed)


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return None


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _safe_iso(epoch: float | None) -> str | None:
    if epoch is None or not math.isfinite(epoch):
        return None
    try:
        return _datetime.datetime.fromtimestamp(
            epoch, tz=_datetime.timezone.utc
        ).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _parse_iso_epoch(value: Any) -> float | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = _datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_datetime.timezone.utc)
    return parsed.timestamp()


def _decode_value(buffer: Any, offset: int, variable: _Variable) -> Any:
    type_name, element_size, format_code = _TYPE_INFO[variable.type_code]
    if type_name == "char":
        raw = bytes(buffer[offset : offset + variable.count])
        return _decode_c_string(raw)

    try:
        values = struct.unpack_from(
            f"<{variable.count}{format_code}", buffer, offset
        )
    except (struct.error, TypeError) as exc:
        raise IbtBoundsError(
            f"cannot decode channel {variable.name!r} at byte offset {offset}: {exc}"
        ) from exc
    if variable.count == 1:
        return values[0]
    return list(values)


def _read_edge_scalar(
    handle: Any,
    layout: _Layout,
    by_name: Mapping[str, _Variable],
    channel: str,
) -> Any:
    variable = by_name.get(channel)
    if variable is None or variable.count != 1 or layout.record_count <= 0:
        return None
    if variable.type_code not in {1, 2, 3, 4, 5}:
        return None

    # The final value identifies the active sim session in a completed disk
    # telemetry file.  Fall back to the first value if the last is unusable.
    values: list[Any] = []
    record_indices = [layout.record_count - 1]
    if layout.record_count > 1:
        record_indices.append(0)
    for record_index in record_indices:
        absolute = (
            layout.buffer_offset
            + record_index * layout.buffer_length
            + variable.offset
        )
        raw = _read_exact_at(
            handle,
            absolute,
            variable.byte_size,
            layout.file_size,
            f"{channel} sample {record_index}",
            layout.path,
        )
        values.append(_decode_value(raw, 0, variable))
    for value in values:
        if value is not None:
            return value
    return None


def _extract_summary(
    session_info: Mapping[str, Any],
    disk: Mapping[str, Any],
    mtime: float,
    session_num_sample: Any,
    session_unique_id_sample: Any,
) -> dict[str, Any]:
    weekend = _as_mapping(session_info.get("WeekendInfo"))
    options = _as_mapping(weekend.get("WeekendOptions"))
    driver_info = _as_mapping(session_info.get("DriverInfo"))
    session_root = _as_mapping(session_info.get("SessionInfo"))

    sim_session_num = _as_int(session_num_sample)
    sessions = [
        _as_mapping(item) for item in _as_list(session_root.get("Sessions"))
    ]
    matched_session: Mapping[str, Any] = {}
    if sim_session_num is not None:
        for item in sessions:
            if _as_int(item.get("SessionNum")) == sim_session_num:
                matched_session = item
                break

    if not matched_session:
        race_sessions = [
            item
            for item in sessions
            if (_as_text(item.get("SessionType")) or "").strip().lower()
            == "race"
        ]
        if len(race_sessions) == 1:
            matched_session = race_sessions[0]
            if sim_session_num is None:
                sim_session_num = _as_int(matched_session.get("SessionNum"))

    sim_session_type = _as_text(matched_session.get("SessionType"))
    event_type = _as_text(weekend.get("EventType"))
    if sim_session_type:
        is_race = sim_session_type.strip().lower() == "race"
    else:
        is_race = (event_type or "").strip().lower() == "race"

    driver_car_idx = _as_int(driver_info.get("DriverCarIdx"))
    driver: Mapping[str, Any] = {}
    for candidate in _as_list(driver_info.get("Drivers")):
        candidate_map = _as_mapping(candidate)
        if _as_int(candidate_map.get("CarIdx")) == driver_car_idx:
            driver = candidate_map
            break

    base_epoch = disk.get("session_start_date")
    if not isinstance(base_epoch, (int, float)):
        base_epoch = None
    if base_epoch is not None and not (
        _MIN_REASONABLE_EPOCH <= float(base_epoch) <= _MAX_REASONABLE_EPOCH
    ):
        base_epoch = None

    start_epoch: float | None = None
    end_epoch: float | None = None
    time_source = "mtime"
    if base_epoch is not None:
        start_offset = disk.get("session_start_time")
        end_offset = disk.get("session_end_time")
        # sessionStartDate is the recording's wall-clock start.  The two
        # doubles are simulator SessionTime values, so only their difference
        # belongs on the wall-clock duration.
        start_epoch = float(base_epoch)
        if (
            isinstance(start_offset, (int, float))
            and isinstance(end_offset, (int, float))
            and math.isfinite(start_offset)
            and math.isfinite(end_offset)
        ):
            end_epoch = start_epoch + max(0.0, float(end_offset) - float(start_offset))
        else:
            end_epoch = start_epoch
        time_source = "disk_header"
    else:
        yaml_start = _parse_iso_epoch(weekend.get("SessionStartTime"))
        if yaml_start is not None:
            start_epoch = yaml_start
            end_epoch = yaml_start
            time_source = "session_yaml"

    if start_epoch is None:
        start_epoch = mtime
        end_epoch = mtime
    if end_epoch is None or end_epoch < start_epoch:
        end_epoch = start_epoch

    return {
        "series_id": _as_int(weekend.get("SeriesID")),
        "season_id": _as_int(weekend.get("SeasonID")),
        "session_id": _as_int(weekend.get("SessionID")),
        "subsession_id": _as_int(weekend.get("SubSessionID")),
        "race_week": _as_int(weekend.get("RaceWeek")),
        "event_type": event_type,
        "category": _as_text(weekend.get("Category")),
        "official": _as_bool(weekend.get("Official")),
        "track_id": _as_int(weekend.get("TrackID")),
        "track_name": _as_text(weekend.get("TrackName")),
        "track_display_name": _as_text(weekend.get("TrackDisplayName")),
        "track_config_name": _as_text(weekend.get("TrackConfigName")),
        "track_length": _as_text(weekend.get("TrackLength")),
        "is_fixed_setup": _as_bool(options.get("IsFixedSetup")),
        "sim_session_num": sim_session_num,
        "sim_session_type": sim_session_type,
        "session_unique_id": _as_int(session_unique_id_sample),
        "is_race": is_race,
        "car_id": _as_int(driver.get("CarID")),
        "car_path": _as_text(driver.get("CarPath")),
        "car_screen_name": _as_text(driver.get("CarScreenName")),
        "driver_user_id": _as_int(driver.get("UserID")),
        "driver_setup_name": _as_text(driver_info.get("DriverSetupName")),
        "driver_setup_modified": _as_bool(driver_info.get("DriverSetupModified")),
        "start_time_unix": start_epoch,
        "end_time_unix": end_epoch,
        "start_time_utc": _safe_iso(start_epoch),
        "end_time_utc": _safe_iso(end_epoch),
        "time_source": time_source,
    }


def _read_layout(path: os.PathLike[str] | str) -> _Layout:
    source = Path(path).expanduser()
    try:
        source = source.resolve(strict=True)
    except FileNotFoundError:
        raise
    if not source.is_file():
        raise IbtFormatError(f"{_path_label(source)} is not a regular file")

    try:
        handle = source.open("rb")
    except OSError as exc:
        raise IbtError(f"cannot open {_path_label(source)} for reading: {exc}") from exc

    with handle:
        stat = os.fstat(handle.fileno())
        file_size = stat.st_size
        minimum = _HEADER_SIZE + _DISK_SUBHEADER.size
        _checked_region(0, minimum, file_size, "IBT header", source)

        header_values = _HEADER.unpack(
            _read_exact_at(
                handle, 0, _HEADER_SIZE, file_size, "SDK header", source
            )
        )
        version = header_values[0]
        tick_rate = header_values[2]
        session_info_len = header_values[4]
        session_info_offset = header_values[5]
        num_vars = header_values[6]
        var_header_offset = header_values[7]
        num_buf = header_values[8]
        buffer_length = header_values[9]

        if version <= 0 or version > 1_000:
            raise IbtFormatError(
                f"{_path_label(source)}: implausible SDK header version {version}"
            )
        if tick_rate <= 0 or tick_rate > _MAX_TICK_RATE:
            raise IbtFormatError(
                f"{_path_label(source)}: invalid tick rate {tick_rate} Hz"
            )
        if session_info_len < 0 or session_info_len > _MAX_SESSION_INFO_BYTES:
            raise IbtBoundsError(
                f"{_path_label(source)}: invalid session info length "
                f"{session_info_len}"
            )
        if num_vars < 0 or num_vars > _MAX_VARIABLES:
            raise IbtBoundsError(
                f"{_path_label(source)}: invalid variable count {num_vars}"
            )
        if num_buf < 1 or num_buf > 4:
            raise IbtBoundsError(
                f"{_path_label(source)}: invalid telemetry buffer count {num_buf}"
            )
        if buffer_length <= 0:
            raise IbtBoundsError(
                f"{_path_label(source)}: invalid sample buffer length "
                f"{buffer_length}"
            )
        if session_info_len and session_info_offset < minimum:
            raise IbtBoundsError(
                f"{_path_label(source)}: session info offset "
                f"{session_info_offset} overlaps the fixed IBT headers"
            )
        if num_vars and var_header_offset < minimum:
            raise IbtBoundsError(
                f"{_path_label(source)}: variable header offset "
                f"{var_header_offset} overlaps the fixed IBT headers"
            )

        buffers: list[dict[str, int]] = []
        for index in range(4):
            start = 12 + index * 4
            buffers.append(
                {
                    "index": index,
                    "tick_count": header_values[start],
                    "buffer_offset": header_values[start + 1],
                }
            )
        buffer_offset = buffers[0]["buffer_offset"]
        if buffer_offset < 0:
            raise IbtBoundsError(
                f"{_path_label(source)}: negative telemetry buffer offset "
                f"{buffer_offset}"
            )

        header = {
            "version": version,
            "status": header_values[1],
            "tick_rate": tick_rate,
            "session_info_update": header_values[3],
            "session_info_length": session_info_len,
            "session_info_offset": session_info_offset,
            "num_variables": num_vars,
            "variable_header_offset": var_header_offset,
            "num_buffers": num_buf,
            "buffer_length": buffer_length,
            "buffers": buffers[:num_buf],
        }

        disk_values = _DISK_SUBHEADER.unpack(
            _read_exact_at(
                handle,
                _DISK_SUBHEADER_OFFSET,
                _DISK_SUBHEADER.size,
                file_size,
                "disk subheader",
                source,
            )
        )
        record_count = disk_values[4]
        if record_count < 0:
            raise IbtBoundsError(
                f"{_path_label(source)}: negative disk record count {record_count}"
            )
        disk = {
            "session_start_date": disk_values[0],
            "session_start_time": disk_values[1],
            "session_end_time": disk_values[2],
            "session_lap_count": disk_values[3],
            "session_record_count": record_count,
        }

        session_bytes = _read_exact_at(
            handle,
            session_info_offset,
            session_info_len,
            file_size,
            "session info",
            source,
        )
        session_info = _parse_session_info(session_bytes, source)

        variable_region_len = num_vars * _VAR_HEADER.size
        if _regions_overlap(
            session_info_offset,
            session_info_len,
            var_header_offset,
            variable_region_len,
        ):
            raise IbtBoundsError(
                f"{_path_label(source)}: session info and variable header "
                "regions overlap"
            )
        variable_bytes = _read_exact_at(
            handle,
            var_header_offset,
            variable_region_len,
            file_size,
            "variable headers",
            source,
        )
        variables: list[_Variable] = []
        names: set[str] = set()
        for index in range(num_vars):
            fields = _VAR_HEADER.unpack_from(variable_bytes, index * _VAR_HEADER.size)
            type_code, offset, count, count_as_time = fields[:4]
            name = _decode_c_string(fields[4])
            if type_code not in _TYPE_INFO:
                raise IbtTypeError(
                    f"{_path_label(source)}: variable #{index} {name!r} uses "
                    f"unsupported SDK type code {type_code}"
                )
            if not name:
                raise IbtFormatError(
                    f"{_path_label(source)}: variable header #{index} has no name"
                )
            if name in names:
                raise IbtFormatError(
                    f"{_path_label(source)}: duplicate variable name {name!r}"
                )
            names.add(name)
            if count <= 0:
                raise IbtBoundsError(
                    f"{_path_label(source)}: variable {name!r} has invalid "
                    f"element count {count}"
                )
            variable = _Variable(
                type_code=type_code,
                offset=offset,
                count=count,
                count_as_time=bool(count_as_time),
                name=name,
                description=_decode_c_string(fields[5]),
                unit=_decode_c_string(fields[6]),
            )
            if offset < 0 or offset + variable.byte_size > buffer_length:
                raise IbtBoundsError(
                    f"{_path_label(source)}: variable {name!r} occupies bytes "
                    f"[{offset}, {offset + variable.byte_size}) outside the "
                    f"{buffer_length}-byte sample buffer"
                )
            variables.append(variable)

        sample_bytes = record_count * buffer_length
        if sample_bytes and buffer_offset < minimum:
            raise IbtBoundsError(
                f"{_path_label(source)}: telemetry buffer offset "
                f"{buffer_offset} overlaps the fixed IBT headers"
            )
        if _regions_overlap(
            buffer_offset, sample_bytes, session_info_offset, session_info_len
        ) or _regions_overlap(
            buffer_offset, sample_bytes, var_header_offset, variable_region_len
        ):
            raise IbtBoundsError(
                f"{_path_label(source)}: telemetry samples overlap session "
                "information or variable headers"
            )
        _checked_region(
            buffer_offset,
            sample_bytes,
            file_size,
            "telemetry sample buffers",
            source,
        )

        layout = _Layout(
            path=source,
            file_size=file_size,
            mtime=stat.st_mtime,
            header=header,
            disk=disk,
            session_info=session_info,
            variables=variables,
            buffer_offset=buffer_offset,
            buffer_length=buffer_length,
            record_count=record_count,
            summary={},
        )
        by_name = {variable.name: variable for variable in variables}
        session_num = _read_edge_scalar(handle, layout, by_name, "SessionNum")
        session_unique_id = _read_edge_scalar(
            handle, layout, by_name, "SessionUniqueID"
        )
        layout.summary = _extract_summary(
            session_info, disk, stat.st_mtime, session_num, session_unique_id
        )
        return layout


def _layout_public(layout: _Layout) -> dict[str, Any]:
    metadata = {
        "path": str(layout.path),
        "file_size": layout.file_size,
        "mtime_unix": layout.mtime,
        "mtime_utc": _safe_iso(layout.mtime),
        "header": layout.header,
        "disk": layout.disk,
        "record_count": layout.record_count,
        "buffer_offset": layout.buffer_offset,
        **layout.summary,
        "session_info": layout.session_info,
        "variables": [variable.public() for variable in layout.variables],
    }
    return metadata


def scan_ibt(path: os.PathLike[str] | str) -> dict[str, Any]:
    """Scan one IBT without loading its telemetry sample buffers.

    Two edge values from ``SessionNum`` and ``SessionUniqueID`` may be read to
    identify the simulator session.  No source bytes are written.
    """

    return _layout_public(_read_layout(path))


def _normalise_channels(
    channels: Iterable[str] | str | None,
    variables: Sequence[_Variable],
) -> list[_Variable]:
    by_name = {variable.name: variable for variable in variables}
    if channels is None:
        return list(variables)
    if isinstance(channels, str):
        requested = [channels]
    else:
        requested = list(channels)
    if any(not isinstance(name, str) or not name for name in requested):
        raise IbtChannelError("channel names must be non-empty strings")
    missing = [name for name in requested if name not in by_name]
    if missing:
        available_preview = ", ".join(sorted(by_name)[:20])
        raise IbtChannelError(
            f"requested channel(s) not present: {', '.join(missing)}; "
            f"available channels begin: {available_preview}"
        )
    # Preserve caller order while avoiding accidental duplicate decoding.
    seen: set[str] = set()
    result: list[_Variable] = []
    for name in requested:
        if name not in seen:
            result.append(by_name[name])
            seen.add(name)
    return result


def _sampling_plan(
    tick_rate: int, target_hz: float | int | None
) -> tuple[float, float]:
    if target_hz is None:
        return 1.0, float(tick_rate)
    if isinstance(target_hz, bool):
        raise ValueError("target_hz must be a positive finite number or None")
    try:
        target = float(target_hz)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "target_hz must be a positive finite number or None"
        ) from exc
    if not math.isfinite(target) or target <= 0:
        raise ValueError("target_hz must be a positive finite number or None")
    if target >= tick_rate:
        return 1.0, float(tick_rate)

    return tick_rate / target, target


def _iter_sample_indices(record_count: int, step: float) -> Iterator[int]:
    if step <= 1.0:
        yield from range(record_count)
        return

    position = 0.0
    previous = -1
    while True:
        index = int(position)
        if index >= record_count:
            break
        if index != previous:
            yield index
            previous = index
        position += step


def _sample_indices(
    record_count: int, tick_rate: int, target_hz: float | int | None
) -> tuple[list[int], float]:
    step, output_rate = _sampling_plan(tick_rate, target_hz)
    return list(_iter_sample_indices(record_count, step)), output_rate


def _normalise_chunk_size(chunk_size: int) -> int:
    if isinstance(chunk_size, bool) or not isinstance(chunk_size, int):
        raise ValueError(
            f"chunk_size must be an integer between 1 and "
            f"{_MAX_TELEMETRY_CHUNK_SIZE}"
        )
    if not 1 <= chunk_size <= _MAX_TELEMETRY_CHUNK_SIZE:
        raise ValueError(
            f"chunk_size must be between 1 and {_MAX_TELEMETRY_CHUNK_SIZE}"
        )
    return chunk_size


def _normalise_record_bounds(
    record_count: int,
    start_record: int,
    end_record: int | None,
) -> tuple[int, int]:
    if isinstance(start_record, bool) or not isinstance(start_record, int):
        raise ValueError("start_record must be an integer")
    if start_record < 0 or start_record > record_count:
        raise ValueError(
            f"start_record must be between 0 and {record_count}"
        )
    if end_record is None:
        normalized_end = record_count
    else:
        if isinstance(end_record, bool) or not isinstance(end_record, int):
            raise ValueError("end_record must be an integer or None")
        normalized_end = end_record
    if normalized_end < start_record or normalized_end > record_count:
        raise ValueError(
            f"end_record must be between start_record and {record_count}"
        )
    return start_record, normalized_end


def load_telemetry(
    path: os.PathLike[str] | str,
    channels: Iterable[str] | str | None = None,
    target_hz: float | int | None = 20,
) -> dict[str, Any]:
    """Load selected IBT telemetry channels at or below ``target_hz``.

    ``samples`` is column-oriented: ``samples[channel][sample_index]``.  A
    scalar channel produces scalar values; an SDK array variable produces a
    list for each retained sample.  Character arrays are decoded as strings.
    Use ``target_hz=None`` to retain every native record.
    """

    layout = _read_layout(path)
    selected = _normalise_channels(channels, layout.variables)
    indices, output_rate = _sample_indices(
        layout.record_count, layout.header["tick_rate"], target_hz
    )
    samples: dict[str, list[Any]] = {variable.name: [] for variable in selected}

    if indices and selected:
        try:
            handle = layout.path.open("rb")
        except OSError as exc:
            raise IbtError(
                f"cannot reopen {_path_label(layout.path)} for reading: {exc}"
            ) from exc
        with handle:
            try:
                mapped = mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ)
            except (OSError, ValueError) as exc:
                raise IbtError(
                    f"cannot memory-map {_path_label(layout.path)} read-only: {exc}"
                ) from exc
            with mapped:
                for record_index in indices:
                    record_offset = (
                        layout.buffer_offset + record_index * layout.buffer_length
                    )
                    for variable in selected:
                        absolute = record_offset + variable.offset
                        samples[variable.name].append(
                            _decode_value(mapped, absolute, variable)
                        )

    public = _layout_public(layout)
    session_info = public.pop("session_info")
    public.pop("variables")
    return {
        "metadata": public,
        "session_info": session_info,
        # Keep the complete SDK catalogue even when callers decode only a
        # focused subset.  The raw IBT remains the authoritative full-fidelity
        # source; this catalogue makes every available channel discoverable
        # for later on-demand analysis without materialising every column on
        # every routine race review.
        "available_variables": [variable.public() for variable in layout.variables],
        "variables": [variable.public() for variable in selected],
        "samples": samples,
        "sample_indices": indices,
        "native_tick_rate_hz": layout.header["tick_rate"],
        "sample_rate_hz": output_rate,
        "source_record_count": layout.record_count,
        "sample_count": len(indices),
        "channel_selection": {
            "mode": "all" if channels is None else "selected",
            "available_count": len(layout.variables),
            "decoded_count": len(selected),
        },
    }


def _iter_decoded_chunks(
    layout: _Layout,
    selected: Sequence[_Variable],
    *,
    target_hz: float | int | None,
    chunk_size: int,
    start_record: int,
    end_record: int | None,
) -> Iterator[dict[str, Any]]:
    size = _normalise_chunk_size(chunk_size)
    record_start, record_end = _normalise_record_bounds(
        layout.record_count, start_record, end_record
    )
    step, output_rate = _sampling_plan(layout.header["tick_rate"], target_hz)
    indices = _iter_sample_indices(layout.record_count, step)
    public_variables = [variable.public() for variable in selected]

    if layout.record_count <= 0 or record_start >= record_end:
        return

    try:
        handle = layout.path.open("rb")
    except OSError as exc:
        raise IbtError(
            f"cannot reopen {_path_label(layout.path)} for reading: {exc}"
        ) from exc

    with handle:
        try:
            mapped = mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ)
        except (OSError, ValueError) as exc:
            raise IbtError(
                f"cannot memory-map {_path_label(layout.path)} read-only: {exc}"
            ) from exc

        with mapped:
            chunk_number = 0
            chunk_indices: list[int] = []
            samples: dict[str, list[Any]] = {
                variable.name: [] for variable in selected
            }
            for record_index in indices:
                if record_index < record_start:
                    continue
                if record_index >= record_end:
                    break
                record_offset = (
                    layout.buffer_offset + record_index * layout.buffer_length
                )
                chunk_indices.append(record_index)
                for variable in selected:
                    samples[variable.name].append(
                        _decode_value(mapped, record_offset + variable.offset, variable)
                    )

                if len(chunk_indices) < size:
                    continue

                yield {
                    "source_path": str(layout.path),
                    "chunk_index": chunk_number,
                    "sample_indices": chunk_indices,
                    "first_native_index": chunk_indices[0],
                    "last_native_index": chunk_indices[-1],
                    "samples": samples,
                    "variables": public_variables,
                    "native_tick_rate_hz": layout.header["tick_rate"],
                    "sample_rate_hz": output_rate,
                    "source_record_count": layout.record_count,
                    "record_start": record_start,
                    "record_end": record_end,
                    "sample_count": len(chunk_indices),
                }
                chunk_number += 1
                chunk_indices = []
                samples = {variable.name: [] for variable in selected}

            if chunk_indices:
                yield {
                    "source_path": str(layout.path),
                    "chunk_index": chunk_number,
                    "sample_indices": chunk_indices,
                    "first_native_index": chunk_indices[0],
                    "last_native_index": chunk_indices[-1],
                    "samples": samples,
                    "variables": public_variables,
                    "native_tick_rate_hz": layout.header["tick_rate"],
                    "sample_rate_hz": output_rate,
                    "source_record_count": layout.record_count,
                    "record_start": record_start,
                    "record_end": record_end,
                    "sample_count": len(chunk_indices),
                }


def iter_telemetry_chunks(
    path: os.PathLike[str] | str,
    channels: Iterable[str] | str | None = None,
    *,
    target_hz: float | int | None = None,
    chunk_size: int = 2_048,
    start_record: int = 0,
    end_record: int | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield selected telemetry in bounded, read-only chunks.

    Each yielded mapping contains column-oriented ``samples`` and the exact
    native ``sample_indices`` from which those values were decoded. Passing
    ``channels=None`` selects the complete recorded catalogue, while
    ``target_hz=None`` retains every native record.

    ``chunk_size`` limits retained decoded records, not source bytes. It must
    be between 1 and 8192 so an all-channel stream remains bounded.
    ``start_record`` is inclusive and ``end_record`` is exclusive; both refer
    to native source-record indices and are applied before decoding.
    """

    layout = _read_layout(path)
    selected = _normalise_channels(channels, layout.variables)
    yield from _iter_decoded_chunks(
        layout,
        selected,
        target_hz=target_hz,
        chunk_size=chunk_size,
        start_record=start_record,
        end_record=end_record,
    )


_PROFILE_DISTINCT_STRING_LIMIT = 64


def _profile_values_equal(first: Any, second: Any) -> bool:
    if isinstance(first, float) and isinstance(second, float):
        if math.isnan(first) and math.isnan(second):
            return True
    if (
        isinstance(first, Sequence)
        and not isinstance(first, (str, bytes, bytearray))
        and isinstance(second, Sequence)
        and not isinstance(second, (str, bytes, bytearray))
    ):
        return len(first) == len(second) and all(
            _profile_values_equal(left, right)
            for left, right in zip(first, second)
        )
    return first == second


def _new_value_profile(type_name: str) -> dict[str, Any]:
    state: dict[str, Any] = {
        "type": type_name,
        "value_count": 0,
        "change_count": 0,
        "has_previous": False,
        "previous": None,
    }
    if type_name in {"int", "float", "double"}:
        state.update(
            {
                "finite_count": 0,
                "non_finite_count": 0,
                "minimum": None,
                "maximum": None,
                "mean": 0.0,
            }
        )
    elif type_name == "bool":
        state["true_count"] = 0
    elif type_name == "bitfield":
        state.update({"observed_or": 0, "observed_and": None})
    elif type_name == "char":
        state.update(
            {
                "non_empty_count": 0,
                "minimum_length": None,
                "maximum_length": None,
                "distinct_values": set(),
                "distinct_truncated": False,
            }
        )
    return state


def _update_value_profile(state: dict[str, Any], value: Any) -> None:
    if state["has_previous"] and not _profile_values_equal(
        state["previous"], value
    ):
        state["change_count"] += 1
    state["has_previous"] = True
    state["previous"] = value
    state["value_count"] += 1

    type_name = state["type"]
    if type_name in {"int", "float", "double"}:
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            state["non_finite_count"] += 1
            return
        if not math.isfinite(number):
            state["non_finite_count"] += 1
            return
        state["finite_count"] += 1
        state["minimum"] = (
            value if state["minimum"] is None else min(state["minimum"], value)
        )
        state["maximum"] = (
            value if state["maximum"] is None else max(state["maximum"], value)
        )
        count = state["finite_count"]
        state["mean"] += (number - state["mean"]) / count
        return

    if type_name == "bool":
        if bool(value):
            state["true_count"] += 1
        return

    if type_name == "bitfield":
        integer = int(value)
        state["observed_or"] |= integer
        state["observed_and"] = (
            integer
            if state["observed_and"] is None
            else state["observed_and"] & integer
        )
        return

    if type_name == "char":
        text = str(value)
        length = len(text)
        if text:
            state["non_empty_count"] += 1
        state["minimum_length"] = (
            length
            if state["minimum_length"] is None
            else min(state["minimum_length"], length)
        )
        state["maximum_length"] = (
            length
            if state["maximum_length"] is None
            else max(state["maximum_length"], length)
        )
        distinct = state["distinct_values"]
        if text not in distinct:
            if len(distinct) < _PROFILE_DISTINCT_STRING_LIMIT:
                distinct.add(text)
            else:
                state["distinct_truncated"] = True


def _finish_value_profile(state: Mapping[str, Any]) -> dict[str, Any]:
    type_name = str(state["type"])
    result: dict[str, Any] = {
        "value_count": int(state["value_count"]),
        "change_count": int(state["change_count"]),
    }
    if type_name in {"int", "float", "double"}:
        finite_count = int(state["finite_count"])
        result.update(
            {
                "finite_count": finite_count,
                "non_finite_count": int(state["non_finite_count"]),
                "min": state["minimum"],
                "max": state["maximum"],
                "mean": state["mean"] if finite_count else None,
            }
        )
    elif type_name == "bool":
        true_count = int(state["true_count"])
        result.update(
            {
                "true_count": true_count,
                "false_count": int(state["value_count"]) - true_count,
                "transitions": int(state["change_count"]),
            }
        )
    elif type_name == "bitfield":
        result.update(
            {
                "observed_or": int(state["observed_or"]),
                "observed_and": (
                    int(state["observed_and"])
                    if state["observed_and"] is not None
                    else None
                ),
                "transitions": int(state["change_count"]),
            }
        )
    elif type_name == "char":
        distinct = state["distinct_values"]
        result.update(
            {
                "non_empty_count": int(state["non_empty_count"]),
                "min_length": state["minimum_length"],
                "max_length": state["maximum_length"],
                "observed_distinct_count": len(distinct),
                "distinct_count_is_lower_bound": bool(
                    state["distinct_truncated"]
                ),
            }
        )
    return result


def _new_channel_profile(variable: _Variable) -> dict[str, Any]:
    type_name = variable.type_name
    is_array = type_name != "char" and variable.count > 1
    return {
        "variable": variable,
        "sample_count": 0,
        "change_count": 0,
        "has_previous": False,
        "previous": None,
        "value": None if is_array else _new_value_profile(type_name),
        "elements": (
            [_new_value_profile(type_name) for _ in range(variable.count)]
            if is_array
            else None
        ),
    }


def _update_channel_profile(state: dict[str, Any], value: Any) -> None:
    variable: _Variable = state["variable"]
    is_array = state["elements"] is not None
    if state["has_previous"] and not _profile_values_equal(
        state["previous"], value
    ):
        state["change_count"] += 1
    state["has_previous"] = True
    state["previous"] = list(value) if is_array else value
    state["sample_count"] += 1

    if not is_array:
        _update_value_profile(state["value"], value)
        return

    if not isinstance(value, Sequence) or isinstance(
        value, (str, bytes, bytearray)
    ):
        raise IbtTypeError(
            f"channel {variable.name!r} declared an array of "
            f"{variable.count} values but decoded {type(value).__name__}"
        )
    if len(value) != variable.count:
        raise IbtTypeError(
            f"channel {variable.name!r} declared {variable.count} array values "
            f"but decoded {len(value)}"
        )
    for element_state, element in zip(state["elements"], value):
        _update_value_profile(element_state, element)


def _finish_channel_profile(
    state: Mapping[str, Any],
    *,
    native_tick_rate: float,
    profile_rate: float,
) -> dict[str, Any]:
    variable: _Variable = state["variable"]
    result: dict[str, Any] = {
        "name": variable.name,
        "type": variable.type_name,
        "type_code": variable.type_code,
        "unit": variable.unit,
        "count": variable.count,
        "count_as_time": variable.count_as_time,
        "sample_count": int(state["sample_count"]),
    }
    if variable.type_name == "char":
        result["value_shape"] = "string"
        result["string_capacity"] = variable.count
        result.update(_finish_value_profile(state["value"]))
        return result

    if variable.count == 1:
        result["value_shape"] = "scalar"
        result.update(_finish_value_profile(state["value"]))
        return result

    result.update(
        {
            "value_shape": "array",
            "change_count": int(state["change_count"]),
            "effective_sample_rate_hz": (
                native_tick_rate * variable.count
                if variable.count_as_time
                else native_tick_rate
            ),
            "profiled_effective_sample_rate_hz": (
                profile_rate * variable.count
                if variable.count_as_time
                else profile_rate
            ),
            "elements": [
                {"index": index, **_finish_value_profile(element_state)}
                for index, element_state in enumerate(state["elements"])
            ],
        }
    )
    return result


def profile_telemetry(
    path: os.PathLike[str] | str,
    channels: Iterable[str] | str | None = None,
    *,
    target_hz: float | int | None = None,
    chunk_size: int = 2_048,
    start_record: int = 0,
    end_record: int | None = None,
) -> dict[str, Any]:
    """Stream and compactly profile every channel recorded in an IBT.

    Profiles are type-aware and never retain whole-file sample columns. The
    default profiles every channel at every native record. ``channels`` and
    the inclusive/exclusive source-record bounds support compact on-demand
    queries. Numeric channels report finite counts, extrema, a streaming mean,
    and changes; booleans and bitfields report state counts/transitions;
    arrays report the same statistics per element.
    """

    layout = _read_layout(path)
    size = _normalise_chunk_size(chunk_size)
    record_start, record_end = _normalise_record_bounds(
        layout.record_count, start_record, end_record
    )
    selected = _normalise_channels(channels, layout.variables)
    _, profile_rate = _sampling_plan(layout.header["tick_rate"], target_hz)
    states = {
        variable.name: _new_channel_profile(variable)
        for variable in selected
    }
    sample_count = 0
    chunks_processed = 0
    for chunk in _iter_decoded_chunks(
        layout,
        selected,
        target_hz=target_hz,
        chunk_size=size,
        start_record=record_start,
        end_record=record_end,
    ):
        chunks_processed += 1
        sample_count += int(chunk["sample_count"])
        samples = chunk["samples"]
        for variable in selected:
            state = states[variable.name]
            for value in samples[variable.name]:
                _update_channel_profile(state, value)

    native_rate = float(layout.header["tick_rate"])
    return {
        "schema_version": 1,
        "source_path": str(layout.path),
        "file_size": layout.file_size,
        "mtime_unix": layout.mtime,
        "native_tick_rate_hz": layout.header["tick_rate"],
        "profile_rate_hz": profile_rate,
        "source_record_count": layout.record_count,
        "record_start": record_start,
        "record_end": record_end,
        "sample_count": sample_count,
        "chunk_size": size,
        "chunks_processed": chunks_processed,
        "available_channel_count": len(layout.variables),
        "channel_count": len(selected),
        "channel_selection": "all" if channels is None else "selected",
        "channel_catalog": [variable.public() for variable in selected],
        "channels": {
            variable.name: _finish_channel_profile(
                states[variable.name],
                native_tick_rate=native_rate,
                profile_rate=profile_rate,
            )
            for variable in selected
        },
    }


def _iter_ibt_paths(root: Path) -> Iterator[Path]:
    if root.is_file():
        if root.suffix.lower() == ".ibt":
            yield root
        return
    for directory, directory_names, file_names in os.walk(root, followlinks=False):
        # Stable traversal makes grouping/tests reproducible.
        directory_names.sort(key=str.lower)
        file_names.sort(key=str.lower)
        for file_name in file_names:
            if file_name.lower().endswith(".ibt"):
                yield Path(directory) / file_name


def _fallback_group_key(metadata: Mapping[str, Any]) -> tuple[Any, ...]:
    session_num = metadata.get("sim_session_num")
    subsession_id = _as_int(metadata.get("subsession_id"))
    if subsession_id not in {None, 0}:
        return ("subsession", subsession_id, session_num)

    session_id = _as_int(metadata.get("session_id"))
    if session_id not in {None, 0}:
        return ("session", session_id, session_num)

    unique_id = _as_int(metadata.get("session_unique_id"))
    if unique_id not in {None, 0}:
        return ("sim", unique_id, session_num)

    # Offline/test sessions may expose no server IDs.  Prefer session metadata
    # time; it is more reliable than filesystem mtime.  Remaining fields avoid
    # merging different local events that happened to start together.
    disk = _as_mapping(metadata.get("disk"))
    disk_start = disk.get("session_start_date")
    if isinstance(disk_start, (int, float)) and (
        _MIN_REASONABLE_EPOCH <= float(disk_start) <= _MAX_REASONABLE_EPOCH
    ):
        time_value: Any = float(disk_start)
    else:
        time_value = metadata.get("start_time_unix")
    if metadata.get("time_source") == "mtime":
        time_value = None
    return (
        "local",
        time_value,
        session_num,
        metadata.get("track_id"),
        metadata.get("track_config_name"),
        metadata.get("car_id"),
        metadata.get("car_path"),
    )


def _group_id(key: tuple[Any, ...]) -> str:
    return ":".join("unknown" if value is None else str(value) for value in key)


def discover_sessions(
    root: os.PathLike[str] | str,
    latest_only: bool = False,
) -> list[dict[str, Any]]:
    """Discover and group IBTs below ``root``.

    Groups use ``SubSessionID + sim_session_num`` when available.  Server
    session IDs, simulator IDs, and metadata timestamps are progressively used
    for offline sessions.  Disk/session metadata determines recency; mtime is
    used only when those timestamps are unavailable.

    With ``latest_only=True`` only the newest group identified as a Race is
    returned.  A full discovery appends malformed-file records with
    ``kind='error'`` after valid groups so one partial recording does not hide
    the rest of the season.
    """

    base = Path(root).expanduser()
    try:
        base = base.resolve(strict=True)
    except FileNotFoundError:
        raise

    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    errors: list[dict[str, Any]] = []
    for source in _iter_ibt_paths(base):
        try:
            metadata = scan_ibt(source)
        except (IbtError, OSError) as exc:
            errors.append(
                {
                    "kind": "error",
                    "valid": False,
                    "path": str(source.resolve()),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )
            continue
        grouped.setdefault(_fallback_group_key(metadata), []).append(metadata)

    groups: list[dict[str, Any]] = []
    for key, members in grouped.items():
        members.sort(
            key=lambda item: (
                float(item.get("start_time_unix") or 0.0),
                str(item.get("path") or ""),
            )
        )
        representative = max(
            members,
            key=lambda item: (
                float(item.get("end_time_unix") or 0.0),
                float(item.get("mtime_unix") or 0.0),
            ),
        )
        non_mtime = [
            item for item in members if item.get("time_source") != "mtime"
        ]
        timing_members = non_mtime or members
        start_time = min(
            float(item.get("start_time_unix") or item.get("mtime_unix") or 0.0)
            for item in timing_members
        )
        end_time = max(
            float(item.get("end_time_unix") or item.get("mtime_unix") or 0.0)
            for item in timing_members
        )
        time_source = (
            representative.get("time_source") if non_mtime else "mtime"
        )
        groups.append(
            {
                "kind": "session",
                "valid": True,
                "group_id": _group_id(key),
                "subsession_id": representative.get("subsession_id"),
                "session_id": representative.get("session_id"),
                "session_unique_id": representative.get("session_unique_id"),
                "sim_session_num": representative.get("sim_session_num"),
                "sim_session_type": representative.get("sim_session_type"),
                "event_type": representative.get("event_type"),
                "is_race": bool(representative.get("is_race")),
                "season_id": representative.get("season_id"),
                "series_id": representative.get("series_id"),
                "track_id": representative.get("track_id"),
                "track_name": representative.get("track_name"),
                "track_config_name": representative.get("track_config_name"),
                "car_id": representative.get("car_id"),
                "car_path": representative.get("car_path"),
                "is_fixed_setup": representative.get("is_fixed_setup"),
                "start_time_unix": start_time,
                "end_time_unix": end_time,
                "start_time_utc": _safe_iso(start_time),
                "end_time_utc": _safe_iso(end_time),
                "time_source": time_source,
                "latest_mtime_unix": max(
                    float(item.get("mtime_unix") or 0.0) for item in members
                ),
                "file_count": len(members),
                "files": [item["path"] for item in members],
            }
        )

    groups.sort(
        key=lambda item: (
            float(item.get("end_time_unix") or 0.0),
            float(item.get("latest_mtime_unix") or 0.0),
            str(item.get("group_id") or ""),
        ),
        reverse=True,
    )
    if latest_only:
        for group in groups:
            if group["is_race"]:
                return [group]
        return []
    return groups + errors

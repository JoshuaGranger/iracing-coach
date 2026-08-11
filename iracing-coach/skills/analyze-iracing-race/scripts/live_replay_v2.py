"""Reader for iRacing Coach's compact live replay v2 chunks.

The companion app owns the append-only writer. This module deliberately only
decodes recorded fields; it never interpolates gaps or invents incident types.
"""

from __future__ import annotations

import datetime as _datetime
import gzip
import io
import math
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping


MAGIC = b"IRCRPLY2"
SCHEMA_VERSION = 2
# The writer closes chunks after at most 1,200 frames.  Keep enough headroom
# for a full 64-car field while rejecting corrupt headers before they can
# drive an unbounded allocation or decompression bomb in the analysis worker.
MAX_COMPRESSED_BYTES = 64 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 128 * 1024 * 1024
HEADER_BYTES = 56
MAX_CHUNK_BYTES = MAX_COMPRESSED_BYTES + HEADER_BYTES
MAX_FRAMES = 1_200
MAX_CARS = 512
MAX_EVENTS = 1_024


class LiveReplayV2Error(ValueError):
    """The replay chunk is corrupt, unsupported, or unsafe to decode."""


def _gzip_decompress(payload: bytes, expected_bytes: int) -> bytes:
    if expected_bytes <= 0 or expected_bytes > MAX_UNCOMPRESSED_BYTES:
        raise LiveReplayV2Error("Replay decompressed length is invalid.")
    try:
        # gzip.decompress() expands before the caller can validate its size.
        # Reading one byte beyond the authenticated header length makes the
        # allocation bounded and rejects both bombs and dishonest headers.
        with gzip.GzipFile(fileobj=io.BytesIO(payload), mode="rb") as stream:
            result = stream.read(expected_bytes + 1)
    except (OSError, EOFError) as exc:
        raise LiveReplayV2Error("Replay gzip payload is corrupt.") from exc
    if len(result) != expected_bytes:
        raise LiveReplayV2Error("Replay decompressed length is invalid.")
    return result


@dataclass(frozen=True)
class ReplayV2Header:
    frame_count: int
    uncompressed_bytes: int
    compressed_bytes: int
    start_captured_at: str
    end_captured_at: str
    start_session_time_seconds: float | None
    end_session_time_seconds: float | None


class _Reader:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.offset = 0

    @property
    def remaining(self) -> int:
        return len(self.payload) - self.offset

    def _unpack(self, fmt: str) -> Any:
        size = struct.calcsize(fmt)
        if self.remaining < size:
            raise LiveReplayV2Error("Replay chunk ended unexpectedly.")
        value = struct.unpack_from(fmt, self.payload, self.offset)[0]
        self.offset += size
        return value

    def byte(self) -> int:
        return self._unpack("<B")

    def boolean(self) -> bool:
        value = self.byte()
        if value not in (0, 1):
            raise LiveReplayV2Error("Replay boolean marker is invalid.")
        return value == 1

    def int32(self) -> int:
        return self._unpack("<i")

    def uint32(self) -> int:
        return self._unpack("<I")

    def int64(self) -> int:
        return self._unpack("<q")

    def float32(self) -> float:
        return self._unpack("<f")

    def float64(self) -> float:
        return self._unpack("<d")

    def seven_bit_int(self) -> int:
        value = 0
        for shift in range(0, 35, 7):
            current = self.byte()
            value |= (current & 0x7F) << shift
            if current & 0x80 == 0:
                return value
        raise LiveReplayV2Error("Replay string length is invalid.")

    def text(self) -> str:
        length = self.seven_bit_int()
        if length < 0 or length > self.remaining:
            raise LiveReplayV2Error("Replay string is truncated.")
        raw = self.payload[self.offset : self.offset + length]
        self.offset += length
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise LiveReplayV2Error("Replay text is not valid UTF-8.") from exc

    def optional(self, read: Callable[[], Any]) -> Any | None:
        return read() if self.boolean() else None

    def bounded_count(self, maximum: int, label: str) -> int:
        value = self.int32()
        if value < 0 or value > maximum:
            raise LiveReplayV2Error(f"Replay {label} count is invalid.")
        return value


def _iso_from_unix_milliseconds(value: int) -> str:
    try:
        return (
            _datetime.datetime.fromtimestamp(value / 1_000, tz=_datetime.timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
    except (OverflowError, OSError, ValueError) as exc:
        raise LiveReplayV2Error("Replay timestamp is invalid.") from exc


def _finite_or_none(value: float) -> float | None:
    return value if math.isfinite(value) else None


def read_live_replay_v2_header(source: str | Path | bytes) -> ReplayV2Header:
    payload = _source_bytes(source)
    if len(payload) < HEADER_BYTES + 1 or len(payload) > MAX_CHUNK_BYTES:
        raise LiveReplayV2Error("Replay chunk size is invalid.")
    reader = _Reader(payload)
    if payload[: len(MAGIC)] != MAGIC:
        raise LiveReplayV2Error("This is not an iRacing Coach replay v2 chunk.")
    reader.offset = len(MAGIC)
    version = reader.int32()
    if version != SCHEMA_VERSION:
        raise LiveReplayV2Error(f"Unsupported replay chunk version {version}.")
    frame_count = reader.int32()
    uncompressed = reader.int32()
    compressed = reader.int32()
    start_at = reader.int64()
    end_at = reader.int64()
    start_session = _finite_or_none(reader.float64())
    end_session = _finite_or_none(reader.float64())
    if (
        frame_count <= 0
        or frame_count > MAX_FRAMES
        or uncompressed <= 0
        or uncompressed > MAX_UNCOMPRESSED_BYTES
        or compressed <= 0
        or compressed > MAX_COMPRESSED_BYTES
        or compressed != reader.remaining
    ):
        raise LiveReplayV2Error("Replay chunk header lengths are invalid.")
    return ReplayV2Header(
        frame_count,
        uncompressed,
        compressed,
        _iso_from_unix_milliseconds(start_at),
        _iso_from_unix_milliseconds(end_at),
        start_session,
        end_session,
    )


def decode_live_replay_v2(source: str | Path | bytes) -> dict[str, Any]:
    payload = _source_bytes(source)
    header = read_live_replay_v2_header(payload)
    compressed = payload[-header.compressed_bytes :]
    raw = _gzip_decompress(compressed, header.uncompressed_bytes)
    reader = _Reader(raw)
    common = _read_common(reader)
    frame_count = reader.bounded_count(MAX_FRAMES, "frames")
    if frame_count != header.frame_count:
        raise LiveReplayV2Error("Replay frame counts disagree.")
    frames: list[dict[str, Any]] = []
    prior_cars: dict[int, dict[str, Any]] = {}
    prior_player: dict[str, Any] | None = None
    for _ in range(frame_count):
        captured_at = _iso_from_unix_milliseconds(reader.int64())
        session_time = reader.optional(reader.float64)
        session_state = reader.optional(reader.int32)
        session_flags = reader.optional(reader.int64)
        source_tick = reader.int32()
        source_tick_rate = reader.int32()
        player = _read_player(reader, prior_player)
        prior_player = player
        events = _read_events(reader)
        cars = _read_cars(reader, prior_cars)
        frames.append(
            {
                "capturedAt": captured_at,
                "sessionTimeSeconds": session_time,
                "sessionState": session_state,
                "sessionFlags": session_flags,
                "sourceTick": source_tick,
                "sourceTickRate": source_tick_rate,
                "playerTelemetry": player,
                "events": events,
                "cars": cars,
            }
        )
    if reader.remaining:
        raise LiveReplayV2Error("Replay payload has trailing data.")
    return {
        "schemaVersion": SCHEMA_VERSION,
        "frameCount": frame_count,
        "startSessionTimeSeconds": header.start_session_time_seconds,
        "endSessionTimeSeconds": header.end_session_time_seconds,
        **common,
        "frames": frames,
    }


def _source_bytes(source: str | Path | bytes) -> bytes:
    if isinstance(source, bytes):
        if len(source) > MAX_CHUNK_BYTES:
            raise LiveReplayV2Error("Replay chunk size is invalid.")
        return source
    path = Path(source)
    try:
        if path.stat().st_size > MAX_CHUNK_BYTES:
            raise LiveReplayV2Error("Replay chunk size is invalid.")
        with path.open("rb") as stream:
            payload = stream.read(MAX_CHUNK_BYTES + 1)
    except OSError as exc:
        raise LiveReplayV2Error(f"Replay chunk could not be read: {exc}") from exc
    if len(payload) > MAX_CHUNK_BYTES:
        raise LiveReplayV2Error("Replay chunk size is invalid.")
    return payload


def _read_common(reader: _Reader) -> dict[str, Any]:
    session_key = reader.text()
    session_unique_id = reader.optional(reader.int64)
    subsession_id = reader.optional(reader.int64)
    session_number = reader.optional(reader.int32)
    session_type = reader.optional(reader.text)
    player_car_index = reader.optional(reader.int32)
    coverage = []
    for _ in range(reader.bounded_count(1_024, "coverage")):
        coverage.append(
            {
                "channel": reader.text(),
                "recorded": reader.boolean(),
                "unavailableReason": reader.optional(reader.text),
            }
        )
    participants = []
    for _ in range(reader.bounded_count(MAX_CARS, "participants")):
        participants.append(
            {
                "carIndex": reader.int32(),
                "carNumber": reader.optional(reader.text),
                "classId": reader.optional(reader.int32),
                "className": reader.optional(reader.text),
                "carName": reader.optional(reader.text),
                "driverName": reader.optional(reader.text),
                "teamName": reader.optional(reader.text),
                "isSpectator": reader.optional(reader.boolean),
            }
        )
    return {
        "sessionKey": session_key,
        "sessionUniqueId": session_unique_id,
        "subsessionId": subsession_id,
        "sessionNumber": session_number,
        "sessionType": session_type,
        "playerCarIndex": player_car_index,
        "coverage": coverage,
        "participants": participants,
    }


_CAR_FIELDS: tuple[tuple[str, Callable[[_Reader], Any]], ...] = (
    ("lapDistancePercent", lambda r: r.optional(r.float32)),
    ("lap", lambda r: r.optional(r.int32)),
    ("completedLaps", lambda r: r.optional(r.int32)),
    ("overallPosition", lambda r: r.optional(r.int32)),
    ("classPosition", lambda r: r.optional(r.int32)),
    ("onPitRoad", lambda r: r.optional(r.boolean)),
    ("trackSurface", lambda r: r.optional(r.int32)),
    ("paceFlags", lambda r: r.optional(r.int32)),
    ("lastLapSeconds", lambda r: r.optional(r.float32)),
    ("bestLapSeconds", lambda r: r.optional(r.float32)),
)
_ALL_CAR_FIELDS = (1 << len(_CAR_FIELDS)) - 1


def _read_cars(reader: _Reader, prior: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    count = reader.bounded_count(MAX_CARS, "cars")
    result = []
    present: set[int] = set()
    for _ in range(count):
        car_index = reader.int32()
        mask = reader.uint32()
        previous = prior.get(car_index)
        if previous is None and mask != _ALL_CAR_FIELDS:
            raise LiveReplayV2Error("A replay car delta is missing its base state.")
        car = {"carIndex": car_index}
        for bit, (name, read) in enumerate(_CAR_FIELDS):
            car[name] = read(reader) if mask & (1 << bit) else previous[name]  # type: ignore[index]
        result.append(car)
        prior[car_index] = car
        present.add(car_index)
    for missing in set(prior) - present:
        del prior[missing]
    return result


_PLAYER_FIELDS: tuple[tuple[str, Callable[[_Reader], Any]], ...] = (
    ("incidentPoints", lambda r: r.optional(r.int32)),
    ("driverIncidentPoints", lambda r: r.optional(r.int32)),
    ("teamIncidentPoints", lambda r: r.optional(r.int32)),
    ("trackSurface", lambda r: r.optional(r.int32)),
    ("onPitRoad", lambda r: r.boolean()),
    ("towing", lambda r: r.boolean()),
    ("repairRequired", lambda r: r.boolean()),
    ("mandatoryRepairSeconds", lambda r: r.optional(r.float32)),
    ("optionalRepairSeconds", lambda r: r.optional(r.float32)),
    ("speedMetersPerSecond", lambda r: r.optional(r.float32)),
    ("throttle", lambda r: r.optional(r.float32)),
    ("brake", lambda r: r.optional(r.float32)),
    ("steeringWheelAngleRadians", lambda r: r.optional(r.float32)),
    ("gear", lambda r: r.optional(r.int32)),
    ("rpm", lambda r: r.optional(r.float32)),
    ("yawRateRadiansPerSecond", lambda r: r.optional(r.float32)),
    ("lateralAccelerationG", lambda r: r.optional(r.float32)),
    ("longitudinalAccelerationG", lambda r: r.optional(r.float32)),
)
_ALL_PLAYER_FIELDS = (1 << len(_PLAYER_FIELDS)) - 1


def _read_player(reader: _Reader, previous: Mapping[str, Any] | None) -> dict[str, Any] | None:
    marker = reader.byte()
    if marker == 0:
        return None
    if marker != 1:
        raise LiveReplayV2Error("Replay player state marker is invalid.")
    mask = reader.uint32()
    if previous is None and mask != _ALL_PLAYER_FIELDS:
        raise LiveReplayV2Error("A replay player delta is missing its base state.")
    result: dict[str, Any] = {}
    for bit, (name, read) in enumerate(_PLAYER_FIELDS):
        result[name] = read(reader) if mask & (1 << bit) else previous[name]  # type: ignore[index]
    return result


def _read_events(reader: _Reader) -> list[dict[str, Any]]:
    result = []
    for _ in range(reader.bounded_count(MAX_EVENTS, "events")):
        result.append(
            {
                "kind": reader.text(),
                "label": reader.text(),
                "sourceChannel": reader.text(),
                "delta": reader.optional(reader.float64),
            }
        )
    return result

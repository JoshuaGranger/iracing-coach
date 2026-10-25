"""Build-pinned, read-callback decoder for native track geometry snapshots.

This module never opens a process or performs I/O. Its layout is statically
qualified for one executable; runtime acquisition and driving remain unqualified.
"""
from __future__ import annotations

import math
import struct
from typing import Callable, Iterable

PINNED_EXECUTABLE_SHA256 = "6581dd68f7fa4367ae874fd41b459597029058ddcba9829c90d8b710170e6362"
IMAGE_MEMSPAN = 0x24A63000
ROUTE_SLOTS = {0: "race", 43: "pit0", 50: "pitb0"}
SECTION_STRIDE = 0x110
HARD_MAX_SECTIONS = 200_000
HARD_MAX_TOTAL_BYTES = 64 * 1024 * 1024
# Broad input plausibility guard (native units), not a claim about the format's
# physical-unit semantics. This admits route extents up to roughly 1,000 km.
MAX_GEOMETRY_MAGNITUDE = 1_000_000.0
_CANON_LO_MAX = (1 << 47) - 1
_USER_PTR_MIN = 0x10000


class SnapshotError(ValueError):
    """Invalid, truncated, or unsupported snapshot data."""


def _read(read: Callable[[int, int], bytes], address: int, size: int) -> bytes:
    if (type(address) is not int or not _USER_PTR_MIN <= address <= _CANON_LO_MAX or
            type(size) is not int or size < 0 or (size and address + size - 1 > _CANON_LO_MAX)):
        raise SnapshotError("address range is outside user-mode pointer bounds")
    data = read(address, size)
    if not isinstance(data, (bytes, bytearray, memoryview)) or len(data) != size:
        raise SnapshotError(f"short read at 0x{address:x}: expected {size} bytes")
    return bytes(data)


def _unpack(read, address: int, fmt: str):
    return struct.unpack(fmt, _read(read, address, struct.calcsize(fmt)))


def _ptr(read, address: int, *, optional: bool = False) -> int | None:
    (value,) = _unpack(read, address, "<Q")
    if value == 0 and optional:
        return None
    if not _USER_PTR_MIN <= value < (1 << 47):
        raise SnapshotError(f"invalid pointer at 0x{address:x}")
    return value


def _finite(values: Iterable[float], label: str) -> tuple[float, ...]:
    result = tuple(float(x) for x in values)
    if not all(math.isfinite(x) for x in result):
        raise SnapshotError(f"non-finite {label}")
    return result


def world_to_en(x: float, y: float, theta_rad: float) -> tuple[float, float]:
    """Convert native XY metres to east/north using the qualified world yaw."""
    x, y, theta_rad = _finite((x, y, theta_rad), "world coordinates")
    s, c = math.sin(theta_rad), math.cos(theta_rad)
    return s * x - c * y, c * x + s * y


def wrap_angle(angle: float) -> float:
    angle, = _finite((angle,), "angle")
    return (angle + math.pi) % (2.0 * math.pi) - math.pi


def _decode_route(read, route_ptr: int, role: str, max_sections: int) -> dict:
    (length,) = _unpack(read, route_ptr + 0x10, "<f")
    (count,) = _unpack(read, route_ptr + 0x18, "<i")
    (route_type,) = _unpack(read, route_ptr + 0x1C, "<i")
    (sections_ptr,) = _unpack(read, route_ptr + 0x38, "<Q")
    length, = _finite((length,), "route length")
    if length <= 0 or length > MAX_GEOMETRY_MAGNITUDE:
        raise SnapshotError("route length outside broad plausibility bounds")
    if count < 1 or count > max_sections:
        raise SnapshotError("route section count exceeds configured budget")
    if route_type != 1:
        raise SnapshotError(f"unsupported route type {route_type}")
    if not _USER_PTR_MIN <= sections_ptr < (1 << 47):
        raise SnapshotError("invalid route section array pointer")
    sections = []
    for i in range(count):
        p = sections_ptr + i * SECTION_STRIDE
        raw_station, = _unpack(read, p + 0x50, "<d")
        xy = _unpack(read, p + 0x58, "<2f")
        (checkpoint_station,) = _unpack(read, p + 0x7C, "<f")
        (inv_span,) = _unpack(read, p + 0x84, "<f")
        xy_delta = _unpack(read, p + 0x88, "<2f")
        n0 = _unpack(read, p + 0x98, "<2f")
        dn = _unpack(read, p + 0xA8, "<2f")
        raw_station, = _finite((raw_station,), "raw route station")
        xy = _finite(xy, "route XY")
        checkpoint_station, = _finite((checkpoint_station,), "checkpoint station")
        inv_span, = _finite((inv_span,), "inverse span")
        xy_delta = _finite(xy_delta, "XY delta")
        n0 = _finite(n0, "N0 coefficient")
        dn = _finite(dn, "delta-N coefficient")
        if any(abs(v) > MAX_GEOMETRY_MAGNITUDE for v in
               (raw_station, *xy, checkpoint_station, inv_span, *xy_delta, *n0, *dn)):
            raise SnapshotError("route section value outside broad plausibility bounds")
        if inv_span <= 0:
            raise SnapshotError("inverse span must be positive")
        sections.append({"raw_rl_station": raw_station, "xy": xy,
                         "checkpoint_station": checkpoint_station,
                         "inverse_span": inv_span, "xy_delta": xy_delta,
                         "n0": n0, "delta_n": dn})
    return {"role": role, "length": length, "section_count": count,
            "type": route_type, "sections": sections}


def decode_snapshot(read: Callable[[int, int], bytes], module_base: int,
                    executable_sha256: str, *, route_slots=(0, 43, 50),
                    max_sections: int = 50_000,
                    max_total_bytes: int = 32 * 1024 * 1024) -> dict:
    """Decode selected routes using an injected exact-read callback.

    Hash validation precedes every callback invocation. Optional missing route
    pointers are represented as absent entries; they are never synthesized.
    """
    if executable_sha256 != PINNED_EXECUTABLE_SHA256:
        raise SnapshotError("executable SHA-256 is not supported")
    if type(module_base) is not int or not _USER_PTR_MIN <= module_base <= _CANON_LO_MAX:
        raise SnapshotError("module base is outside user-mode pointer bounds")
    if type(max_sections) is not int or not 1 <= max_sections <= HARD_MAX_SECTIONS:
        raise SnapshotError("section budget outside hard limits")
    if type(max_total_bytes) is not int or not 1024 <= max_total_bytes <= HARD_MAX_TOTAL_BYTES:
        raise SnapshotError("byte budget outside hard limits")
    slots = tuple(route_slots)
    if not slots or any(type(slot) is not int or slot not in ROUTE_SLOTS for slot in slots):
        raise SnapshotError("route slots must be selected from race/pit0/pitb0")
    if len(set(slots)) != len(slots):
        raise SnapshotError("duplicate route slot")
    budget = [0]

    def bounded_read(address, size):
        budget[0] += size
        if budget[0] > max_total_bytes:
            raise SnapshotError("snapshot read byte budget exceeded")
        return _read(read, address, size)

    world_ptr = _ptr(bounded_read, module_base + 0x1258D6E0)
    (theta,) = _unpack(bounded_read, world_ptr + 0x44, "<f")
    (altitude,) = _unpack(bounded_read, world_ptr + 0x48, "<f")
    (latitude,) = _unpack(bounded_read, world_ptr + 0x50, "<d")
    (longitude,) = _unpack(bounded_read, world_ptr + 0x58, "<d")
    theta, altitude, latitude, longitude = _finite((theta, altitude, latitude, longitude), "world metadata")
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise SnapshotError("world origin outside geographic bounds")
    (sdk_length,) = _unpack(bounded_read, world_ptr + 0x38, "<f")
    checkpoint_00 = _unpack(bounded_read, world_ptr + 0xB4C4, "<4f")
    cp_start = _unpack(bounded_read, world_ptr + 0xBCD8, "<4f")
    (layout_branch_mode,) = _unpack(bounded_read, world_ptr + 0x3F4, "<i")
    sdk_length, = _finite((sdk_length,), "ordinary SDK lap length")
    checkpoint_00 = _finite(checkpoint_00, "checkpoint_00")
    cp_start = _finite(cp_start, "cp_start")
    if sdk_length <= 0:
        raise SnapshotError("ordinary SDK lap length must be positive")
    (route_count,) = _unpack(bounded_read, world_ptr + 0x703D0, "<i")
    if route_count < 0 or route_count > 4096:
        raise SnapshotError("world route slot count outside bounds")
    route_array = _ptr(bounded_read, world_ptr + 0x703E8)
    routes = {}
    for slot in slots:
        if slot >= route_count:
            continue
        (route_ptr,) = _unpack(bounded_read, route_array + slot * 8, "<Q")
        if route_ptr == 0:
            continue
        if not _USER_PTR_MIN <= route_ptr < (1 << 47):
            raise SnapshotError("invalid route object pointer")
        routes[ROUTE_SLOTS[slot]] = _decode_route(bounded_read, route_ptr, ROUTE_SLOTS[slot], max_sections)
    return {"executable_sha256": executable_sha256,
            "layout_status": "STATIC_LAYOUT_VERIFIED",
            "runtime_acquisition": "UNQUALIFIED",
            "metadata_coherence": "UNQUALIFIED",
            "driving_admitted": False,
            "world": {"theta_rad": theta, "altitude_m": altitude,
                      "origin_lat_deg": latitude, "origin_lon_deg": longitude},
            "sdk_phase_inputs": {
                "ordinary_sdk_length": sdk_length,
                "checkpoint_00_segment": checkpoint_00,
                "cp_start_segment": cp_start,
                "layout_branch_mode": layout_branch_mode,
                "alternate_layout_detected": layout_branch_mode >= 1,
                "sdk_phase_admitted": False,
            },
            "routes": routes, "bytes_read": budget[0]}


def decode_player_pose(read: Callable[[int, int], bytes], module_base: int,
                       executable_sha256: str,
                       *, max_player_index: int = 71) -> dict | None:
    """Read pose separately from route geometry; return None for inactive player."""
    if executable_sha256 != PINNED_EXECUTABLE_SHA256:
        raise SnapshotError("executable SHA-256 is not supported")
    if type(module_base) is not int or not _USER_PTR_MIN <= module_base <= _CANON_LO_MAX:
        raise SnapshotError("module base is outside user-mode pointer bounds")
    if type(max_player_index) is not int or not 0 <= max_player_index <= 71:
        raise SnapshotError("player index limit outside hard bounds")
    (tick_before,) = _unpack(read, module_base + 0x11916C48, "<i")
    (player_idx,) = _unpack(read, module_base + 0x1258CD34, "<i")
    (mode,) = _unpack(read, module_base + 0x1258CD28, "<i")
    if not 0 <= player_idx <= max_player_index:
        return None
    manager_offset = 0x1258D6F0 if mode == 1 else 0x1258D6E8
    manager = _ptr(read, module_base + manager_offset)
    entity_base = _ptr(read, manager + 8)
    entity = entity_base + player_idx * 0xE68
    valid_ptr = _ptr(read, entity + 0x490, optional=True)
    (active,) = _unpack(read, entity + 0xC0, "<B")
    if valid_ptr is None or not active:
        return None
    xyz = _finite(_unpack(read, entity + 0xC4, "<3f"), "player XYZ")
    matrix = _finite(_unpack(read, entity + 0x15C, "<9f"), "player rotation matrix")
    (station,) = _unpack(read, entity + 0x19C, "<f")
    station, = _finite((station,), "player station")
    world = _ptr(read, module_base + 0x1258D6E0)
    (theta,) = _unpack(read, world + 0x44, "<f")
    (tick_after,) = _unpack(read, module_base + 0x11916C48, "<i")
    theta, = _finite((theta,), "world yaw")
    compass_yaw = wrap_angle(theta - math.atan2(matrix[3], matrix[0]))
    east, north = world_to_en(xyz[0], xyz[1], theta)
    return {"player_index": player_idx, "mode": mode, "xyz": xyz,
            "matrix": matrix, "station": station, "east_m": east, "north_m": north,
            "compass_yaw_rad": compass_yaw,
            "native_session_tick_before": tick_before,
            "native_session_tick_after": tick_after,
            "metadata_coherence": "UNQUALIFIED",
            "driving_admitted": False}

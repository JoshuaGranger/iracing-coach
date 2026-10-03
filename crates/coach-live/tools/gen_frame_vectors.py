"""Writes the live frame golden vectors from `live-frame.md` and `live-channels.json`.

The layout is implemented here from the written contract, independently of the Rust codec, so a
Rust encoder, the server's header check and the TypeScript decoder all test against bytes that
none of them produced.

    python crates/coach-live/tools/gen_frame_vectors.py [out_dir]

Default output: docs/08-rewrite/contracts/vectors/live-frame/.
"""

import json
import math
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CONTRACTS = ROOT / "docs" / "08-rewrite" / "contracts"
TABLE = json.loads((CONTRACTS / "live-channels.json").read_text(encoding="utf-8"))
CHANNELS = TABLE["channels"]
FORMATS = {"f32": "<f", "f64": "<d", "u8": "<B", "i8": "<b", "u16": "<H", "u32": "<I", "bool8": "<B", "enum8": "<B"}
ENUMS = TABLE["enums"]
FLAG_BITS = {
    "connected": 0,
    "on_track": 1,
    "in_garage": 2,
    "on_pit_road": 3,
    "first_of_epoch": 4,
    "coaching_paused": 5,
    "backlog": 6,
}
NAN_BITS = struct.pack("<d", float("nan"))


def enum_value(name: str, text: str) -> int:
    table = ENUMS[name]
    if isinstance(table, dict):
        return table[text]
    return table.index(text)


def encode_value(channel: dict, value) -> bytes:
    if channel["type"] == "enum8" and isinstance(value, str):
        value = enum_value(channel["enum"], value)
    if channel["type"] == "bool8":
        value = 1 if value else 0
    return struct.pack(FORMATS[channel["type"]], value)


def encode(header: dict, values: dict, cue, channel_count: int | None = None) -> bytes:
    count = len(CHANNELS) if channel_count is None else channel_count
    flags = 0
    for name, bit in FLAG_BITS.items():
        if header.get("flags", {}).get(name):
            flags |= 1 << bit
    out = bytearray()
    out += b"LF"
    out += bytes([1, flags])
    out += struct.pack("<I", header["seq"])
    out += struct.pack("<I", header["session_epoch"])
    out += struct.pack("<Q", header["captured_unix_ms"])
    session_time = header.get("session_time")
    out += NAN_BITS if session_time is None else struct.pack("<d", session_time)
    out += struct.pack("<I", header["tick"])
    out += struct.pack("<H", header["tick_rate"])
    out += struct.pack("<H", header.get("table_version", TABLE["table_version"]))
    out += struct.pack("<H", count)
    presence = bytearray((count + 7) // 8)
    body = bytearray()
    for index, channel in enumerate(CHANNELS[:count]):
        if channel["id"] in values and values[channel["id"]] is not None:
            presence[index // 8] |= 1 << (index % 8)
            body += encode_value(channel, values[channel["id"]])
    out += presence
    out += body
    if cue is None:
        out += bytes([0, 0, 0, 0])
    else:
        args = cue.get("args", [])
        out += bytes([cue["id"], cue["priority"], cue["evidence"], len(args)])
        for arg in args:
            out += struct.pack("<f", arg)
    return bytes(out)


def decoded(header: dict, values: dict, cue, channel_count: int | None = None) -> dict:
    count = len(CHANNELS) if channel_count is None else channel_count
    flags = 0
    for name, bit in FLAG_BITS.items():
        if header.get("flags", {}).get(name):
            flags |= 1 << bit
    return {
        "header": {
            "version": 1,
            "flags": flags,
            "seq": header["seq"],
            "session_epoch": header["session_epoch"],
            "captured_unix_ms": header["captured_unix_ms"],
            "session_time": header.get("session_time"),
            "tick": header["tick"],
            "tick_rate": header["tick_rate"],
            "table_version": header.get("table_version", TABLE["table_version"]),
            "channel_count": count,
        },
        "values": {c["id"]: values.get(c["id"]) for c in CHANNELS[:count]},
        "cue": cue,
    }


ALL_VALUES = {
    "speed": 41.5, "throttle": 0.75, "brake": 0.25, "steering_wheel_angle": -1.5,
    "steering_road_wheel": -0.125, "steering_ratio": 12.0, "gear": -1, "rpm": 6543.0,
    "yaw_rate": 0.0625, "lateral_accel": 12.5, "longitudinal_accel": -3.25, "lap_dist_pct": 0.5,
    "latitude": 39.7812345, "longitude": -86.2412345, "lap": 65535, "laps_remaining": 12,
    "position": 255, "class_position": 3, "flag_state": "caution", "session_flags": 0x80100008,
    "abs_active": True, "brake_bias": 54.5, "fuel": 45.5, "fuel_pct": 0.75,
    "fuel_laps_remaining": 12.25, "fuel_hard_limit_laps": 11, "last_lap": 92.375,
    "leader_last_lap": 91.875, "ahead_gap": 1.25, "ahead_gap_trend": "closing", "behind_gap": 0.5,
    "behind_gap_trend": "growing", "leader_gap": 33.5, "pace_band_min": 91.5, "pace_band_max": 92.5,
    "run_phase": "middle", "green_run_laps": 7, "mandatory_repair": 0.0, "optional_repair": 4.0,
    "on_pit_road": True, "tow_active": False, "track_temperature": 36.5, "air_temperature": 24.0,
    "incidents": 4, "pace_delta_to_leader": 0.5,
}

BASE = {"seq": 1, "session_epoch": 1, "captured_unix_ms": 1_700_000_000_123, "session_time": 1234.5, "tick": 74_000, "tick_rate": 60}


def numeric_values(values: dict) -> dict:
    """Enum names as their wire numbers, for the `decoded` side of a vector."""
    out = {}
    for channel in CHANNELS:
        value = values.get(channel["id"])
        if channel["type"] == "enum8" and isinstance(value, str):
            value = enum_value(channel["enum"], value)
        out[channel["id"]] = value
    return out


def case(name: str, header: dict, values: dict, cue, note: str, channel_count: int | None = None) -> dict:
    data = encode(header, values, cue, channel_count)
    numeric = numeric_values(values)
    entry = {
        "name": name,
        "note": note,
        "hex": data.hex(),
        "decoded": decoded(header, numeric, cue, channel_count),
    }
    entry["decoded"]["header"]["session_time"] = header.get("session_time")
    return entry


def reject(name: str, data: bytes, error: str, detail: str | None, note: str) -> dict:
    entry = {"name": name, "note": note, "hex": data.hex(), "error": error}
    if detail is not None:
        entry["detail"] = detail
    return entry


def build() -> dict:
    cue11 = {"id": 11, "priority": 20, "evidence": 1, "args": [25.0, 0.5, 3.0]}
    cue13 = {"id": 13, "priority": 0, "evidence": 1, "args": []}
    cue14 = {"id": 14, "priority": 0, "evidence": 2, "args": []}
    cue3 = {"id": 3, "priority": 100, "evidence": 0, "args": [12.0]}
    cue9 = {"id": 9, "priority": 40, "evidence": 1, "args": [0.75]}

    encode_cases = [
        case("every_channel_present", {**BASE, "flags": {"connected": True, "on_track": True, "on_pit_road": True}}, ALL_VALUES, cue11,
             "All 45 channels, extreme integer values (lap 65535, position 255, session_flags above i32 max), a zero repair timer that is present, cue 11 with three arguments."),
        case("disconnected", {**BASE, "session_time": None, "tick": 0, "tick_rate": 0}, {}, cue14,
             "Sim not connected: no channels, flags all clear, session_time NaN, cue 14 (waiting)."),
        case("typical_race_lap", {**BASE, "seq": 77, "session_epoch": 3, "flags": {"connected": True, "on_track": True}},
             {k: ALL_VALUES[k] for k in ("speed", "throttle", "brake", "steering_wheel_angle", "gear", "rpm", "lap_dist_pct", "latitude", "longitude",
                                          "lap", "position", "class_position", "flag_state", "session_flags", "on_pit_road", "tow_active",
                                          "ahead_gap_trend", "behind_gap_trend", "run_phase", "green_run_laps")} | {"on_pit_road": False},
             cue13, "Sparse presence: a few channels, enums as bytes, on_pit_road false (flag bit 3 clear)."),
        case("no_cue", {**BASE, "flags": {"connected": True}}, {"speed": 10.0}, None,
             "Cue id 0: the block is four zero bytes."),
        case("cue_with_one_argument", {**BASE, "flags": {"connected": True, "on_track": True}}, {"mandatory_repair": 11.2}, cue3, "Critical cue, one f32 argument."),
        case("cue_gap_closing", {**BASE, "flags": {"connected": True, "on_track": True}}, {"ahead_gap": 0.75, "ahead_gap_trend": "closing"}, cue9, "Traffic cue, one f32 argument."),
        case("first_of_epoch_and_paused", {**BASE, "flags": {"connected": True, "first_of_epoch": True, "coaching_paused": True}}, {"speed": 1.0},
             {"id": 15, "priority": 0, "evidence": 0, "args": []}, "Flag bits 4 and 5."),
        case("in_garage", {**BASE, "flags": {"connected": True, "in_garage": True}}, {}, cue13, "Flag bit 2."),
        case("counters_at_their_limits", {"seq": 0xFFFFFFFF, "session_epoch": 0xFFFFFFFF, "captured_unix_ms": 0xFFFFFFFFFFFFFFFF, "session_time": 0.0, "tick": 0xFFFFFFFF, "tick_rate": 240, "flags": {"connected": True}},
             {"speed": 0.0}, cue13, "seq, epoch, clock and tick at their maxima; session_time 0.0 is present, not absent."),
    ]

    older = case("older_sender_40_channels", {**BASE, "flags": {"connected": True}},
                 {k: v for k, v in ALL_VALUES.items() if CHANNELS[[c["id"] for c in CHANNELS].index(k)]["index"] < 40}, cue13,
                 "A sender whose table has 40 channels (table_version 1 is allowed to know fewer): channels 40 to 44 decode as absent.",
                 channel_count=40)

    good = encode({**BASE, "flags": {"connected": True}}, {"speed": 10.0, "lap": 3}, cue13)
    header_len = 38
    presence_len = 6

    def with_byte(data: bytes, index: int, value: int) -> bytes:
        buffer = bytearray(data)
        buffer[index] = value
        return bytes(buffer)

    def with_u16(data: bytes, index: int, value: int) -> bytes:
        buffer = bytearray(data)
        buffer[index:index + 2] = struct.pack("<H", value)
        return bytes(buffer)

    cue_at = len(good) - 4
    rejects = [
        reject("bad_magic", with_byte(good, 0, 0x58), "BadMagic", None, "First byte is not 'L'."),
        reject("unsupported_version", with_byte(good, 2, 2), "UnsupportedVersion", "2", "Version byte 2."),
        reject("table_version_newer", with_u16(good, 34, TABLE["table_version"] + 1), "TableNewer", None, "table_version above the reader's."),
        reject("too_many_channels", with_u16(good, 36, len(CHANNELS) + 1), "TooManyChannels", None, "channel_count above the reader's table: the widths of the extra channels are unknown."),
        reject("header_truncated", good[:header_len - 1], "TooShort", None, "37 bytes."),
        reject("presence_truncated", good[:header_len + presence_len - 1], "TooShort", None, "Presence bytes cut short."),
        reject("value_truncated", good[:header_len + presence_len + 5], "TooShort", None, "speed (4 bytes) present, lap (2 bytes) cut in half."),
        reject("cue_block_missing", good[:cue_at], "BadCue", "truncated", "No cue block at all."),
        reject("cue_block_truncated", good[:cue_at + 3], "BadCue", "truncated", "Three of four cue header bytes."),
        reject("cue_argument_count_above_four", with_byte(good, cue_at + 3, 5), "BadCue", "argument count above 4", "k = 5."),
        reject("cue_arguments_truncated", with_byte(good, cue_at + 3, 2), "BadCue", "truncated", "k = 2 with no argument bytes."),
        reject("cue_unknown_id", with_byte(good, cue_at, 99), "BadCue", "unknown cue id", "Cue id 99."),
        reject("cue_unknown_priority", with_byte(good, cue_at + 1, 7), "BadCue", "unknown priority", "Priority byte 7 is not in the enum."),
        reject("cue_unknown_evidence", with_byte(good, cue_at + 2, 3), "BadCue", "unknown evidence", "Evidence byte 3."),
    ]

    # Cue id 0 skips the id, priority and evidence checks (but not k and the argument bytes).
    lenient = bytearray(good)
    lenient[cue_at:cue_at + 3] = bytes([0, 0xEE, 0xEE])
    decode_only = [
        older,
        {
            "name": "cue_id_zero_skips_priority_and_evidence_checks",
            "note": "Cue id 0 with garbage priority and evidence bytes is accepted as no cue.",
            "hex": bytes(lenient).hex(),
            "decoded": decoded({**BASE, "flags": {"connected": True}}, numeric_values({"speed": 10.0, "lap": 3}), None),
        },
    ]

    return {
        "format": 1,
        "table_version": TABLE["table_version"],
        "channel_count": len(CHANNELS),
        "note": "Layout: live-frame.md. Presence bit i is bit (i % 8), least significant first, of presence byte (i // 8). `hex` is the whole frame. `decoded.values` maps every channel id the frame's channel_count covers to its value (enums as their wire byte, bool as true/false) or null when absent; `session_time` null means NaN on the wire. Flags bits: 0 connected, 1 on track, 2 in garage, 3 on pit road, 4 first frame of an epoch, 5 coaching paused, 6 backlog.",
        "encode": encode_cases,
        "decode_only": decode_only,
        "reject": rejects,
    }


def main() -> None:
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else CONTRACTS / "vectors" / "live-frame"
    out_dir.mkdir(parents=True, exist_ok=True)
    document = build()
    (out_dir / "frames.json").write_text(json.dumps(document, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(document['encode'])} encode, {len(document['decode_only'])} decode-only, {len(document['reject'])} reject cases -> {out_dir}")


if __name__ == "__main__":
    main()

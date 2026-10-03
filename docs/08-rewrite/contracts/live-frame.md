# Live frame (version 1)

One binary WebSocket message per engine tick, collector to server, relayed unchanged (except the backlog flag) to browsers. Every frame is self-contained (full state, no deltas), so any frame may be dropped. Channel order, types, enums, cue templates and tiles: `live-channels.json` (table version 1).

## Layout (little-endian, no padding)

| Offset | Size | Field | Notes |
|---|---|---|---|
| 0 | 2 | magic | bytes `4C 46` ("LF") |
| 2 | 1 | version | 1 |
| 3 | 1 | flags | bit0 sim connected, bit1 on track, bit2 in garage, bit3 on pit road, bit4 first frame the collector sent in this session epoch (`crate-api.md` 4.8; a hint only, receivers detect a boundary by a change of `session_epoch`), bit5 coaching paused, bit6 backlog (set by the server on replayed frames), bit7 reserved 0 |
| 4 | 4 | seq | u32 per collector process, wraps |
| 8 | 4 | session_epoch | u32, increments on every session boundary (live-stack section 1 rules) |
| 12 | 8 | captured_unix_ms | u64, collector clock |
| 20 | 8 | session_time | f64 seconds, SDK `SessionTime`; absent is written as the quiet NaN `0x7FF8000000000000`, and any NaN reads as absent |
| 28 | 4 | tick | u32, SDK tick |
| 32 | 2 | tick_rate | u16, SDK tick rate (Hz) |
| 34 | 2 | table_version | u16 |
| 36 | 2 | channel_count | u16, N = channels known to the sender |
| 38 | ceil(N/8) | presence | channel i is bit `i % 8` (least significant first) of presence byte `i / 8`; set = present; absent = not measured (never zero); unused high bits of the last byte are 0 |
| ... | varies | values | present channels in index order, each at its type width (`types` in the table); `bool8` and `enum8` are one byte |
| ... | 4 + 4k | cue | u8 cue id (0 = none), u8 priority (`enums.priority` value), u8 evidence (`enums.evidence` index), u8 k = argument count (<= 4), then k x f32 arguments |

Size at table version 1 with every channel present: 38 + 6 + 148 + 4 + up to 12 = about 210 bytes; 60 Hz is about 13 KB/s.

## Rules

- Rate (normative; `collector-protocol.md` section 6 defers to it): one frame per engine tick at 60 Hz or less; when iRacing publishes faster (up to 240 Hz), the collector sends every `ceil(tick_rate/60)`-th tick so the wire never exceeds 60 frames/s.
- Units on the wire are the SDK's (m/s, rad, m/s2, L, C). Display conversion and formatting use `unit_conversions`, `unit_labels` and `tiles`, in the overlay (Rust) and the browser (TS) from the same generated table; the text rules are `crate-api.md` 4.7, and `crates/coach-live/src/format.rs` with `crates/coach-live/tests/vectors/engine.json` is the reference the TS twin tests against.
- Cue text is rendered from `cues[id].template` with the arguments formatted as listed (`ceil`, `int`, `fixed2`, `percent0`; rules in `crate-api.md` 4.7). The tile's secondary text is the cue's `basis` when present.
- Decoders accept `channel_count` smaller than their own table (missing tail = absent) and reject a larger one when `table_version` is not newer than their own (the extra widths are unknown, so values and cue cannot be located). A decoder rejects a cue block with k > 4, missing argument bytes, or (cue id not 0) an id, priority or evidence byte outside its table. The server does not relay frames whose `table_version` is greater than its own and reports `collector_newer_than_server` in status.
- The server validates the whole fixed header (bytes 0 to 37: magic, version, `table_version`, `channel_count` not above its own table's, and that the message length is at least 38 + ceil(`channel_count`/8)) and drops an invalid frame with one logged error per collector connection; it decodes no values. It sets bit6 on backlog frames.
- Golden vectors: W2 commits encoded frames with their decoded values under `docs/08-rewrite/contracts/vectors/live-frame/` (by contract change); the Rust encoder, the server's header check and the TS decoder all test against them.

## Browser socket

`GET /ws/live/{driverId}?rate=60|30|10|1`. The server first sends one JSON text message, `LiveHello` exactly as defined in `api.openapi.yaml` (that schema is normative), then up to 10 min of backlog at 10 Hz (bit6 set), then live frames decimated to `rate`. The browser may send `{"type": "rate", "rate": 10}` to change rate. Opening this socket counts as a viewer (`collector-protocol.md` section 6).

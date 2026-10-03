# Live frame vectors

Golden frames for `live-frame.md` (table version 1), written by `crates/coach-live/tools/gen_frame_vectors.py` from the written contract and `live-channels.json`. The script implements the layout on its own, so the Rust encoder (`coach_live::LiveState::encode_frame`), the server's header check (`parse_header`) and the TypeScript decoder all test against bytes that none of them produced.

Regenerate: `python crates/coach-live/tools/gen_frame_vectors.py` (the interpreter is `C:\Users\joshu\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe`; the `python` on PATH is a Store alias).

`frames.json`:

| Key | Content |
|---|---|
| `encode[]` | `name`, `note`, `hex` (the whole frame), `decoded` (header fields, every channel id with its value or null, cue). A conforming encoder given `decoded` writes exactly `hex`; a conforming decoder reads `hex` as `decoded`. |
| `decode_only[]` | Frames an encoder of this table would not write (an older sender with 40 channels, a cue id 0 with garbage priority and evidence bytes), with their decoded values. |
| `reject[]` | `name`, `note`, `hex`, `error` (`TooShort`, `BadMagic`, `UnsupportedVersion`, `TableNewer`, `TooManyChannels`, `BadCue`) and, for `BadCue`, `detail`. A full decoder rejects each; the server's header check (bytes 0 to 37 plus the presence bytes) rejects exactly the header-level ones. |

Conventions the vectors fix beyond the prose of `live-frame.md`:

- **Presence bit order.** Channel `i` is bit `i % 8`, least significant bit first, of presence byte `i / 8`. With 45 channels all present the six presence bytes are `ff ff ff ff ff 1f`.
- **Absent session time** is the quiet NaN `0x7FF8000000000000` (`00 00 00 00 00 00 f8 7f`); a present `0.0` is eight zero bytes.
- `decoded.header.session_time` is `null` for NaN. `decoded.values` keys cover only the channels the frame's `channel_count` includes; enums are their wire byte, `bool8` is `true`/`false`.
- Flag bits: 0 connected, 1 on track, 2 in garage, 3 on pit road, 4 first frame of an epoch, 5 coaching paused, 6 backlog (set by the server), 7 reserved zero.
- Cue block: id, priority byte (`enums.priority` value), evidence index, `k`, then `k` little-endian f32. A cue of id 0 is four zero bytes; checks on id, priority and evidence are skipped for it, `k` and the argument bytes are not.

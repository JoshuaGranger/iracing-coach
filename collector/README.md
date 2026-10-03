# coach-collector

The sim-PC half of iRacing Coach: a native Rust program with no WebView and no .NET. It reads iRacing's shared memory, runs the live engine (`crates/coach-live`), draws the always-on-top monitor, captures garage setups, and uploads finalized recordings to the server. Contracts: `docs/08-rewrite/contracts/collector-protocol.md`, `live-frame.md`, the `Collector*` and `Upload*` parts of `api.openapi.yaml`, `nas-layout.md` section 7.

```
coach-collector [run] [--server URL] [--replay FILE [--speed N]] [--dry-run] [--no-ui]
                      [--exit-at-end [--drain-secs N]] [--iracing-root DIR] [--state-dir DIR]
coach-collector install [--server URL]     config + HKCU Run value + start; no admin, no firewall rule
coach-collector uninstall                  stop, remove the Run value and its own files only
coach-collector record-sdk <seconds> <out.sdkfix>
```

## Modules

| Module | Role |
|---|---|
| `sdk`, `win::map` | Event-driven reader over an `SdkMap` (real map or replay): torn-read guard, session info only when the header counter changes, map reopened after 3 s dead |
| `pipeline`, `hub` | Reader thread: engine tick, frame decimation to at most 60 per second, epoch marker carried to the next sent frame, status, overlay state; nothing here touches disk or network |
| `gate` | The on-track gate: fresh off-track evidence or no file work |
| `uploader`, `http`, `scan`, `win::watcher` | Finalization gate, SHA-256, create, 4 MiB chunks, resume; the gate is re-checked per 32 KiB piece read, per directory entry scanned, per journal entry matched, and every 2 ms while a full socket is waited on (the socket is non-blocking), so an abort completes within 100 ms |
| `garage`, `garage_io` | P5 two-moment capture state machine; journal thread; `.sto` search and hash under the gate |
| `journal` | `journal.jsonl`: pending recordings, garage snapshots and commits, hash memo, `seen` memo (restart does not hash or post a held file again). A torn tail is cut off at start; a failed append is not applied and the caller retries it; a journal that cannot be opened holds entries in memory, sends no garage event, and opens the file again |
| `link`, `ws`, `stream` | One WebSocket (own RFC 6455 client), presence, stream latch, layout sync, garage resend after every welcome |
| `win::ui`, `win::render`, `layout`, `hotkey` | Layered click-through overlay, tray, hotkey, server prompt; GDI renderer usable on a memory bitmap |
| `fixture`, `record` | SDK byte fixtures (`.sdkfix`): format, replay map, synthetic map builder, recorder |
| `app`, `cli`, `logging`, `config` | Wiring, command line, rotating log, `collector.json` |

Threads: reader (engine, frames), net (link), uploader and garage-io (background I/O priority), watcher (directory events), UI (main). Channels are bounded or short-lived locks; the reader never waits on another thread.

## State (`%LOCALAPPDATA%\iRacingCoach`)

`collector.json` (collector id, server, placement, cached layout), `journal.jsonl`, `logs\collector.log` (+ `.1`, 5 MB each), `dry-run.jsonl` in `--dry-run`. The folder name is shared with the retired desktop app: `uninstall` removes only these files. The collector never writes under `Documents\iRacing`.

## Replay, dry run, recording

`--replay FILE` reads a fixture through the same reader code as the real map. Speed 0 runs on a virtual clock (tests); `--exit-at-end` treats the end of the replay as the sim quitting and exits when the journal and upload queue have drained. `--dry-run` has no server and no uploads and writes garage snapshots, commits (with the `.sto` hash when they name a file; values-only commits are logged too) and a per-second frame summary to `dry-run.jsonl` (gate G3). `IsInGarage` dips of up to 1.5 s with an unchanged name stay in the same visit (the rig drops it for 40 to 83 ms at every load and apply). `test-data/sdk/g3-garage-pit-2026-10-03.sdkfix` is a sanitized real recording of that session; `tests/rig_g3.rs` replays it. `record-sdk` copies the map while a session runs; the fixture holds the driver's name and customer id, so treat it as private data.

Fixture format (little-endian): magic `IRCSDKF1`, then records `u8 kind, u32 length, payload`: `1` static (`u32 map_len`, bytes `[0, static_end)`), `2` tick (`u32 t_ms`, `i32 status, tick_rate, session_info_update, tick`, one row), `3` status (`u32 t_ms`, `i32 status`). The first static region of a recording is the whole map. On replay the first static record leaves the header "not connected" until the first tick, so no phantom sample appears.

## Tests

```
cargo test -p coach-collector                    # unit, replay pipeline, uploader, link, garage, end to end
cargo test -p coach-collector --test win_ui_smoke -- --ignored   # shows the overlay for a few seconds
cargo test -p coach-collector --test win_install  -- --ignored   # install/uninstall against a throwaway key
COACH_RENDER_OUT=<dir> cargo test -p coach-collector --test win_render   # keep the rendered BMPs
```

The in-test server mock (`tests/support`) implements `POST` and `PUT /api/v1/uploads` and `/ws/collector` (including 4001, 4002, acks, pings) and validates every message the collector sends against the generated wire schema.

## Size

`cargo build --release -p coach-collector --target x86_64-pc-windows-msvc` is held to 3 MB (1,578,496 bytes after `coach-ibt` dropped `regex`). The crate carries its own small HTTP client, WebSocket client and logger instead of `ureq`, `tungstenite` and `tracing` to stay there.

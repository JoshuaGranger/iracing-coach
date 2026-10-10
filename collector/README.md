# coach-collector

The sim-PC half of iRacing Coach: a native Rust program with no WebView and no .NET. It reads iRacing's shared memory, runs the live engine (`crates/coach-live`), draws the always-on-top monitor, captures garage setups, and uploads finalized recordings to the server. Contracts: `docs/08-rewrite/contracts/collector-protocol.md`, `live-frame.md`, the `Collector*` and `Upload*` parts of `api.openapi.yaml`, `nas-layout.md` section 7.

```
coach-collector [run] [--server URL] [--replay FILE [--speed N]] [--dry-run] [--no-ui]
                      [--exit-at-end [--drain-secs N]] [--cpu-report] [--iracing-root DIR]
                      [--state-dir DIR]
coach-collector install [--server URL]     config + HKCU Run value + start; no admin, no firewall rule
coach-collector stop                       graceful stop; keep startup, state and executable
coach-collector uninstall --installer      graceful stop + remove Run; installer removes app files
coach-collector uninstall                  stop, remove the Run value and its own files only
coach-collector record-sdk <seconds> <out.sdkfix>
coach-collector backfill [--dry-run] [--archive DIR | --no-archive] [--server URL] [FOLDER...]
```

## Modules

| Module | Role |
|---|---|
| `sdk`, `win::map` | Event-driven reader over an `SdkMap` (real map or replay): torn-read guard, session info only when the header counter changes, map reopened after 3 s dead |
| `pipeline`, `hub` | Reader thread: engine tick, frame decimation to at most 60 per second, epoch marker carried to the next sent frame, status, overlay state; nothing here touches disk or network |
| `gate` | The on-track gate: fresh off-track evidence or no file work |
| `uploader`, `http`, `scan`, `win::watcher` | Finalization gate, SHA-256, create, 4 MiB chunks, resume; the gate is re-checked per 32 KiB piece read, per directory entry scanned, per journal entry matched, and every 2 ms while a full socket is waited on (the socket is non-blocking), so an abort completes within 100 ms |
| `garage`, `garage_io` | P5 two-moment capture state machine; journal thread; `.sto` search and hash under the gate |
| `backfill` | `coach-collector backfill`, D12 step 3: the uploader pointed at the telemetry folder, the legacy raw archive and any FOLDER given, at any depth; its own journal, its own shared-memory evidence of the car being off track; `--dry-run` lists and writes nothing (see Backfill) |
| `journal` | `journal.jsonl`: pending recordings, garage snapshots and commits, hash memo, `seen` memo (restart does not hash or post a held file again), and the terminal state of recordings given up on (see Refused recordings). A torn tail is cut off at start; a failed append is not applied and the caller retries it; a journal that cannot be opened holds entries in memory, sends no garage event, and opens the file again |
| `link`, `ws`, `stream` | One WebSocket (own RFC 6455 client), presence, stream latch, layout sync, garage resend after every welcome |
| `win::ui`, `win::render`, `layout`, `hotkey` | Layered click-through overlay, tray, hotkey, server prompt; GDI renderer usable on a memory bitmap. The overlay looks for new figures 10 times a second and redraws only the tiles whose text, accent or bar position changed, into a back buffer it keeps (`Renderer::dirty`, `paint_tiles`); a trend tile with a value scrolls, so it redraws in every step |
| `cpu` | `run --cpu-report`: the collector's own CPU accounting, process and per thread (see CPU budget) |
| `fixture`, `record` | SDK byte fixtures (`.sdkfix`): format, replay map, synthetic map builder, recorder |
| `app`, `cli`, `logging`, `config` | Wiring, command line, rotating log, `collector.json` |

Threads: reader (engine, frames), net (link), uploader and garage-io (background I/O priority), watcher (directory events), UI (main). Channels are bounded or short-lived locks; the reader never waits on another thread. While frames stream, the link sleeps on the frame queue until the next frame is queued (it does not time out on the socket every few milliseconds), reads the socket every 50 ms and runs its status, garage and stats housekeeping at the same rate.

## CPU budget

Gate G4-3: at most 1.5 % of one core while the car moves, streaming at 60 Hz with the overlay drawn. `run --cpu-report` prints `cpu-report label=window ...` every 10 s and a `label=total` line at exit: `process_pct` is `GetProcessTimes` (what the rig recorder reads; scheduler-tick sampled, so coarse over one window), `process_cycles_pct` and the per-thread figures (`main` is the overlay, `reader`, `link`, `uploader`, `garage-io`) come from the CPU cycle counters, all as percent of one core.

`tests/perf_cpu.rs` (ignored; release binary, about a minute, shows the overlay) replays `fixture::synthetic_laps` (a lapping car, every value moving, 60 Hz) in real time against the mock server, which announces two viewers, and asserts the mean `process_pct` of the steady windows is at most `COACH_PERF_LIMIT` (1.5) and that about 60 frames/s arrived. `tests/perf_reader.rs` (ignored; release) times the reader's tick (SDK read, engine, frame encode) on the same run without sleeping.

## State (`%LOCALAPPDATA%\iRacingCoach`)

`collector.json` (collector id, server, placement, cached layout), `journal.jsonl`, `logs\collector.log` (+ `.1`, 5 MB each), `dry-run.jsonl` in `--dry-run`, and while a backfill runs `backfill-journal.jsonl` and `backfill.lock`. The folder name is shared with the retired desktop app: `uninstall` removes only these files. The collector never writes under `Documents\iRacing`.

The Windows installer installs the executable separately under `%LOCALAPPDATA%\Programs\iRacingCoach`. It runs `install --server URL` to retain the collector id and settings, enable startup at Windows sign-in, and start the companion immediately. A Start menu launch of `run` hides its own private console; an existing PowerShell/cmd console and headless diagnostics remain visible. Upgrades stop the earlier process before loading its settings; unknown optional top-level configuration fields survive a save.

Installer removal uses `uninstall --installer`: stop and wait up to eight seconds, then remove only the sign-in Run value. The state folder, pending-upload journal, logs and executable remain; the installer owns removal of the installed files. `stop` keeps the Run value too. Both return success when already stopped; installer uninstall also succeeds when the Run value is absent. A stop timeout or registry error returns exit code 1 before file removal; invalid CLI arguments return 2. Bare `uninstall` retains its earlier file-cleanup behavior for manual installs.

Lifecycle integration tests inherit `COACH_TEST_RUN_KEY` to redirect startup to a throwaway HKCU key and `COACH_TEST_INSTANCE_ID` to isolate the named mutex/event. With an id, the names are `Local\iRacingCoach.Collector.<id>` and `Local\iRacingCoach.Collector.Quit.<id>`; absent/empty uses the names without a suffix. Detached children inherit both variables. The normal installer checks that `iRacingSim64DX11` is closed before changing the installation.

## Refused recordings

A recording the collector cannot read as one (the header says 0 records, the size does not match the header, the header does not parse) or that the server refuses for good (`400`, `413`, `422` on create) does not stay pending. Once it has not been written for 10 minutes (before that it may be a recording in progress, and an I/O error such as a sharing violation is never final) the journal gets a terminal `rejected` entry with the reason. From then on it is not pending, its bytes are not in `bytes_pending`, and neither a rescan nor a restart queues it; it is looked at again only when its size or mtime changes. The count and bytes are in `UploadStats.rejected` and `rejected_bytes`, the tray's second status line says "N recordings not uploaded", the log has one `giving up on <path>: <reason>` line, and `journal.jsonl` keeps the entries (newest 1,024) with their reasons. The status message to the server is unchanged (`queue` has only `uploads_pending` and `bytes_pending`).

## Backfill

`coach-collector backfill` is the D12 step 3 command: it uploads the recordings under the legacy raw archive (`Documents\iRacing Coach\data\telemetry-traces\raw`, one folder per hash; `--archive DIR` changes it), the telemetry folder, and each FOLDER given (all searched at any depth for `.ibt`; `--no-archive` searches only the FOLDERs). It is the normal uploader: finalization gate, SHA-256 at background priority, `POST /api/v1/uploads`, 4 MiB chunks, resume, hash-verified, only while the collector's own read of the shared memory says the car is off track or iRacing is closed (it waits otherwise), files opened read-only with sharing and never written. An archived recording is uploaded under the name its `manifest.json` records (`original_path`, else a `.ibt` `source_discoveries[].source_path`), as `tools/migrate/Backfill-RawArchive.ps1` did. It is idempotent: the server answers `verified` for bytes it holds and the file is dropped; what the running collector's `journal.jsonl` (read only) or the backfill's own journal already knows the server holds is not even hashed. Progress is in `backfill-journal.jsonl`, so an interrupted backfill resumes, and `backfill.lock` keeps two from running at once. Refused recordings end in the terminal state above and are listed at the end. A run ends when every file is uploaded, held, or refused; it prints a progress line every 15 s.

`--dry-run` waits for the off-track evidence like any scan, then lists each recording (`queue`, `held`, `refused`, size, mtime, path, and `as <name>` when the archive's name differs) and a total line. It sends nothing, hashes nothing, and creates no file or folder.

## Replay, dry run, recording

`--replay FILE` reads a fixture through the same reader code as the real map. Speed 0 runs on a virtual clock (tests); `--exit-at-end` treats the end of the replay as the sim quitting and exits when the journal and upload queue have drained. `--dry-run` has no server and no uploads and writes garage snapshots, commits (with the `.sto` hash when they name a file; values-only commits are logged too) and a per-second frame summary to `dry-run.jsonl` (gate G3). `IsInGarage` dips of up to 1.5 s with an unchanged name stay in the same visit (the rig drops it for 40 to 83 ms at every load and apply). A visit whose name did not change but whose exit state has `DriverSetupIsModified` 0 commits that exit name and the hash of that file (reload of the file already named, or Revert: unmodified means the values are the file's); a modified or unknown exit commits values only (`collector-protocol.md` section 8). `test-data/sdk/g3-garage-pit-2026-10-03.sdkfix` is a sanitized real recording of that session; `tests/rig_g3.rs` replays it. `record-sdk` copies the map while a session runs; the fixture holds the driver's name and customer id, so treat it as private data.

Fixture format (little-endian): magic `IRCSDKF1`, then records `u8 kind, u32 length, payload`: `1` static (`u32 map_len`, bytes `[0, static_end)`), `2` tick (`u32 t_ms`, `i32 status, tick_rate, session_info_update, tick`, one row), `3` status (`u32 t_ms`, `i32 status`). The first static region of a recording is the whole map. On replay the first static record leaves the header "not connected" until the first tick, so no phantom sample appears.

## Tests

```
cargo test -p coach-collector                    # unit, replay pipeline, uploader, backfill, link, garage, end to end
cargo test -p coach-collector --test win_lifecycle # isolated fake listener, safe installer removal
cargo test -p coach-collector --test win_ui_smoke -- --ignored   # shows the overlay for a few seconds
cargo test -p coach-collector --test win_install  -- --ignored   # install/uninstall against a throwaway key
cargo build --release -p coach-collector && cargo test -p coach-collector --test perf_cpu -- --ignored --nocapture   # G4-3 budget on a moving car (COACH_PERF_LAYOUT=busy: every tile changes)
cargo test --release -p coach-collector --test perf_reader -- --ignored --nocapture   # reader cost per tick
COACH_RENDER_OUT=<dir> cargo test -p coach-collector --test win_render   # keep the rendered BMPs
```

The in-test server mock (`tests/support`) implements `POST` and `PUT /api/v1/uploads` and `/ws/collector` (including 4001, 4002, acks, pings) and validates every message the collector sends against the generated wire schema.

## Size

`cargo build --release -p coach-collector --target x86_64-pc-windows-msvc` is held to 3 MB (1,715,200 bytes with `backfill` and the CPU accounting; 1,578,496 before the G3 fixes, after `coach-ibt` dropped `regex`). The crate carries its own small HTTP client, WebSocket client and logger instead of `ureq`, `tungstenite` and `tracing` to stay there.

# Wave 3: the released service on the mini PC, as a user (integration lead)

Run 2026-10-03 05:19Z to 05:24Z from the dev PC against `http://192.168.1.81:8210`, the release of `15a53f4` that passed G1 (`server-release.md`, `g1-analysis.md`). Only HTTP, as a browser and a collector use it: no container, host or release change, no SSH. The run's own writes: one recording upload, and the preference writes the SPA makes when an investigation opens (`last_investigation`).

## Before the upload

`GET /api/v1/status`: `nas.state ok`, queues and spool empty, Python 3.12.15, profile `post-race-foundations-v23`, no collector. One driver with three sessions: the G1 race (session 1, `subsession:87641773:2`, `analysis_id 31419c583d3959b352c3a86c`), the overlap race (session 2, 87648500) and the reconnect-group practice (session 3, two recordings).

`node tools/e2e/spa_check.mjs --base http://192.168.1.81:8210 --out <scratch>\shots\mini-before --session 1` (Playwright Chromium, emulated viewports):

| Check | Desktop 1280x720 | Tablet 1024x768 | Phone 390x844 |
|---|---|---|---|
| `/` opens the single driver's Sessions | pass | pass | pass |
| Library: 3 rows equal to the API, all Analyzed | pass | pass | pass |
| G1 race: Race / Runs / Sections / Traces render (ms) | 207 / 36 / 43 / 199 | 119 / 8 / 19 / 189 | 110 / 8 / 35 / 234 |
| Sections cursor follows the pointer (63.5 % to 79.7 % lap) | pass | pass | pass |
| Traces: map readout follows the chart cursor | 29.8 % to 70.7 % | 29.8 % to 71.0 % | 29.5 % to 72.3 % |
| Traces: requests during a 5 s, 300-move pointer sweep | 0 | 0 | 0 |
| Setups: 2 listed, the race setup shows 62 values | pass | pass | pass |
| No horizontal page scroll | pass | pass | pass |
| No console error | fail: `GET /api/v1/drivers/0/prefs` 404 (W7 minor, `e2e-dev-pc.md`) | same | same |

The first desktop load took 7.5 s to show the library; three repeated cold loads in new browser contexts then took 195 to 238 ms (document 45 to 78 ms, assets 7 to 9 ms each, API calls 2 to 4 ms), so the first figure is attributed to the first connection from a newly launched browser, not to the service. The same loads show `/drivers` requested 3 times and `/drivers/<id>/prefs` twice (W7 minor). The 1280x720 Traces view of the G1 race is 3,710 px high, as on the dev PC (W8 major).

## Upload of a different session type

Recording: the Iowa Offline Testing session of 2026-08-03 (`22a20e50dcff6f548c5afd6d5cd93440d7d818a4af60469e4dbcd183ca52ab8d`, 13,289,951 B, SubSessionID 0), a scratch copy of the dev PC's file, read-only. The service held Race and Practice sessions only; `HEAD /api/v1/uploads/22a20e50...` answered 404 before the run.

`python tools/e2e/time_to_visible.py --server http://192.168.1.81:8210 --file <copy>` (runs `tools/rig/g1_upload.py`, the collector-protocol section 4 client), started 05:22:01.956Z:

| Measure | Value |
|---|---|
| Hash, upload | 0.02 s; 4 chunks in 1.58 s (8.4 MB/s), create `201`, 0 conflicts, 0 retries, final state `verified` |
| Jobs | `discover` 05:22:02 (succeeded), `archive` 05:22:02 (succeeded), `analyze` 05:22:02 to 05:22:04, `project` 05:22:04 |
| Session | new row 4, `Offline Testing`, Iowa Speedway, `analysis_state ready`, `analysis_id cef488c5c7e7563c98ad2d82` (the dev PC's Windows worker produced the same id for the same file) |
| Views after ready | race, runs, investigation, trace catalog, track: all `200`, 0 to 18 ms |
| NAS (status endpoint) | `nas.state ok`, `last_ok_at 05:22:02Z`, `pending_archive 0`; spool empty again (the copy was deleted after archiving) |
| **Upload complete (verified) to analysed and visible** (session `ready` and its race view served) | **2.57 s**; first byte to visible 4.3 s |

Then `spa_check.mjs` again (`shots\mini`): the library lists 4 sessions at all three viewports, the new one labelled Test with "best 24.457 · 2 laps"; the G1 race's investigation results are as before (race 107 to 117 ms, traces 183 to 219 ms, 0 requests during the sweeps); Setups lists 3 setups.

Side effect for the owner: after this upload `/api/v1/drivers` reports `irating 1` (iRacing writes 1 into offline sessions); the next official race restores it. Defect W4 minor in `e2e-dev-pc.md`.

## Not measured here

- A race-sized recording end to end: G1 measured it on this release (first upload byte to projected views 44.9 s for 203 MB).
- Tailnet access and a real phone or tablet: the viewports are emulated in Chromium on the dev PC.
- Collector presence and live tiles on the mini PC: no collector runs against it until the sim-PC install, which is blocked on the DESKTOP-SIM remote agent (stopped; only the owner can restart it).

Screenshots and JSON reports: `<scratchpad>\e2e\shots\mini-before\`, `shots\mini\`; upload report `<scratchpad>\e2e\logs\mini-upload.json`.

## 2026-10-03, wave 3b: replay-driven collector against the released v1 candidate (e2e2)

Run from the dev PC against `http://192.168.1.81:8210` with a collector built from the current tree, driven by a replayed SDK stream instead of iRacing. HTTP and the collector WebSocket only: no SSH, no container or release change, no server data deleted. The sim PC was not used (its remote agent is stopped).

### Release under test

- W9's v1 candidate: polled `GET /` until the SPA's entry chunk changed from `index-Cgab1wYl.js` (the `15a53f4` release) to `index-JIRUEp8w.js`, seen 05:51:43Z. `pnpm -C web build` of the tree at `2e3b962` produces the same `index-JIRUEp8w.js`, `kit-BAFvG6bt.js` and `index-UNQ69cYh.css`, so the served SPA is the wave-3 fix commit's. The image tag itself was not read (no SSH). `/health` 200; `/api/v1/status` `server_version 0.19.0`, `nas.state ok`, queues and spool empty, Python 3.12.15, profile `post-race-foundations-v23`.
- Another agent's upload arrived meanwhile (session 5, a Race of subsession 87697310, analysed 05:53:27Z); it restored the driver's `irating` from 1 to 1636 (the wave-3 iRating fix applies to new ingests only).

### Setup

| Item | Value |
|---|---|
| Collector | `cargo build --release -p coach-collector --target x86_64-pc-windows-msvc` (`CARGO_TARGET_DIR=target/e2e2`, 4 jobs), 47.4 s, exit 0; 1,578,496 B, SHA-256 `3ba1f4a9...6740`, `coach-collector 0.19.0` |
| Command | `coach-collector run --server http://192.168.1.81:8210 --iracing-root %TEMP%\e2e2\iracing --state-dir %TEMP%\e2e2\cstate --no-ui --replay %TEMP%\e2e2\live.sdkfix --speed 1` (scratch state and config; the user's `%LOCALAPPDATA%\iRacingCoach` untouched) |
| Fixture | `make_sdkfix.py live.sdkfix --customer-id <driver> --name <driver> --track-s 150` (new option; the default output is byte-identical to before): 13,800 ticks at 60 Hz; on track T0+1 to T0+151 s, pit road to T0+154, garage T0+156 to T0+170 (`UpdateCount` 3 to 6), name change to `e2e garage capture.sto` at T0+170.5, sim exit at T0+230 |
| Watched folder | `setups\stockcars2 supra2019\` with two synthetic `.sto` files (58 and 64 B of text, not iRacing setups); a copy (timestamps kept) of the New Hampshire practice of 2026-08-01 (`49374699...7304`, 7,896,460 B, subsession 87641773 session 0), new to the service (`HEAD /api/v1/uploads/<sha>` 404 before the run), moved into `telemetry\` at T0+5.5 s while the car was on track |
| Instruments | `tools/e2e/watch_upload.py --interval 0.25` (HEAD timeline), `tools/e2e/live_soak.mjs` (new: presence every 250 ms, two viewers, frame headers, Setups), `tools/e2e/live_check.mjs` (three viewports), the collector's Win32 I/O counters once a second |

A first attempt (05:55:49Z to 05:59:39Z) is not counted: the launcher's PowerShell `Start-Process` held the script, so no viewer and no recording reached that replay. It still gives two facts: with no viewer for the whole session the collector reported `streaming: false` throughout and `sim not_running` at the end; and its garage visit created the four garage setups listed below (05:58:28Z to 05:58:39Z). The counted run is T0 = 12:00:16.04Z (the host was idle between the two).

### Before the collector

`node tools/e2e/live_soak.mjs --phase pre`: driver `live: false`, no collector online, and at desktop 1280x720, tablet 1024x768 and phone 390x844 the Sessions page shows neither the nav's live dot nor the "Live now" button. Pass.

### Results (T0 = 12:00:16.04Z)

| # | Check | Result |
|---|---|---|
| 1 | Presence | `sim in_session`, driver attributed, `on_track true` at T0+1.2 s; `driver.live true`; `viewers 0`, `streaming false` before any viewer (T0+6.7 s) |
| 2 | Live indicator | desktop Sessions shows the nav dot and "Live now" (T0+14 s); `live_check.mjs`: "Live now" at all three viewports |
| 3 | Opening Live starts the stream | viewer A (desktop) reached Live 209 ms after opening; status `viewers 1` at T0+14.4 s and `streaming true` at T0+14.7 s (false before) |
| 4 | Rate | viewer A, 10 s window, live frames only (bit6 clear): **600 frames, 60.0 frames/s**, seq 144 to 743 with no gap, one `session_epoch`, largest gap 65 ms; collector capture to browser receipt through the mini PC p50 40 ms, p95 65 ms, max 91 ms (one clock) |
| 5 | Tiles | desktop at T0+27 s: P3, gap to leader 0.62 s, fuel 17.8 US gal, Laps to pit "Insufficient evidence", Last lap "Not measured" (first lap not yet complete), Coach cue "Building your pace range"; `live_check.mjs` at T0+61 to T0+80 s, every viewport: 6 tiles, 0 waiting, last lap 30.459 (+0.210 s vs leader), gap 0.64 s, laps to pit 19; Live reached 94 / 84 / 83 ms after opening; 835 / 715 / 659 binary frames in 6 s (backlog plus 60 / 30 / 10 Hz); no horizontal scroll, no console error |
| 6 | Second viewer | viewer B (phone, 10 Hz) joined at T0+27 s: `viewers 2`; viewer A across the join (T0+24 to T0+35 s): 684 frames, seq continuous (570 to 1253), one epoch, largest gap 133 ms; B's frames are A's stream decimated (seq 874 to 1252, 10.0 frames/s, same epoch); after B left, `viewers 1` and A continuous (256 frames, 60 frames/s, largest gap 61 ms). Nothing restarted |
| 7 | Leaving keeps the stream | A left at T0+39.6 s: `viewers 0`, and `streaming` stayed `true` in every status while the session continued: on track to T0+151, pit road, garage, stall (14 status changes, the last at T0+170.2 s; `live_check.mjs` viewers came and went at T0+61 to T0+80). At the sim exit (status at T0+230.3 s): `sim not_running`, `streaming false`, `driver.live false` |
| 8 | No upload on track | the practice copy sat in the watched folder from T0+5.5 s; `HEAD` answered 404 until T0+154.3 s; spool `files 2, bytes 122` (the two `.sto` blobs) throughout; collector `ReadTransferCount` 0 and `WriteTransferCount` 0 from T0 to T0+153.8 s (no hash, no scan read, no journal write while on track). The replay's `IsOnTrack` became 0 at T0+154.0 s |
| 9 | Upload after leaving the car | create seen at T0+154.3 s (0.3 s after the car left), 4 MiB at T0+154.6, `parsed` at T0+155.1 s (7,896,460 B); the collector read 15,875,170 B (hash plus body, 2.01 x the file) between T0+153.8 and T0+156.0 s. Status showed `uploads_pending 1, bytes_pending 7896460` at T0+154.4 s and 0 at T0+155.4 s |
| 10 | Ingest | jobs 26 `discover`, 27 `archive`, 28 `analyze` (12:02:50 to 12:02:51, server clock about 1.5 s behind the dev PC), 29 `project`, all succeeded; new session 6, Practice, `subsession:87641773:0`, `analysis_state ready`, `analysis_id d652bb75e2fcfbec6aea8d8a`; spool back to the two `.sto` blobs; `nas.pending_archive 0` |
| 11 | Garage capture in Setups | API: setup 8 named `e2e garage capture.sto`, sources `garage_commit`, `garage_snapshot`, `sto_files[0].sha256 38ac7cf9...242c` (the file's hash), `linked_setup_id 8`, 6 sightings (both visits); setups 5, 6, 7 are name-unknown snapshots (2 sightings each), as P5 requires. Setups page at desktop, tablet and phone: 9 rows equal to the API, the capture listed as "e2e garage capture.sto · Garage · Toyota Supra Class B · 6 sightings"; no horizontal scroll, no console error |
| 12 | Collector | working set 32.1 MiB during the replay (the 20 MB fixture is held in memory; not a G4 figure), 12.5 MiB after the sim exit; stopped with `taskkill` at 12:04:20Z; status then `online false` |

`node tools/e2e/live_soak.mjs --phase run`: all checks passed (exit 0). `node tools/e2e/live_check.mjs`: all live checks passed (exit 0).

### State left on the service

As a user's session would leave it: session 6 (the practice), the four garage setups 5 to 8 and the two synthetic `.sto` files (`e2e baseline.sto` values unknown, `e2e garage capture.sto` linked), the offline collector `bcd322db-...` (machine DESKTOP-JOSHUA) in `status.collectors`, and the prefs the SPA writes. Nothing was deleted on the server. The dev PC's scratch copy of the recording was deleted; the original was only read.

### Findings

| Owner | Severity | Finding | Reproduction | Observed | Expected | Clause |
|---|---|---|---|---|---|---|
| W4 (contract wording) | minor | Setup blobs stay in the spool, which the deletion rule does not say | upload any `.sto` and let it archive | `status.spool` stays `files 2, bytes 122` hours after both `.sto` blobs were archived; `server/src/handlers.rs` `release_spool` keeps non-`ibt` blobs on purpose ("job roots link them") | the deletion table names the exception (setup blobs stay while job roots may link them), or setup blobs are released like recordings | `nas-layout.md` deletion rules (`ready/<sha>` once archived and unreferenced); `worker-interface.md` section 3 (setup links) |

Nothing else failed: every requested behaviour passed.

Logs and screenshots (scratch, not in the repository): `<scratchpad>\e2e2\soak\` (`soak-pre.json`, `soak.json`, `presence.jsonl`, `desktop-sessions-live.png`, `desktop-viewer-a.png`, `phone-viewer-b.png`, `{desktop,tablet,phone}-setups.png`, `*-pre-sessions.png`), `<scratchpad>\e2e2\live\` (`live-check.json`, `{desktop,tablet,phone}-{live,sessions-live}.png`); `%TEMP%\e2e2\logs\` (`run.txt`, `upload-watch.jsonl`, `io.tsv`, `soak.txt`, `live_check.txt`, collector output) and `logs-aborted\`.

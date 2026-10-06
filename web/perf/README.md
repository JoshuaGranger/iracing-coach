# G2 harness

`pnpm -C web perf:g2` runs gate G2 (`docs/08-rewrite/workstreams.md`, Gates): Chromium via Playwright, CDP `Emulation.setCPUThrottlingRate` 6, phone 390x844 and tablet 1024x768, the committed 82-lap current-profile fixture, median of 3. Options: `--runs`, `--duration` (ms of pointer movement, default 10000), `--rate`, `--viewports phone,tablet,desktop`, `--select all` (every lap selected), `--fixture g2|synthetic`.

`PERF_OUT=<dir>` moves every harness output (builds, results, screenshots) out of the shared `target/W8`, so two agents running harnesses at once do not rebuild each other's dist.

Pass: Traces first interactive <= 3 s, cursor >= 55 fps under continuous pointer movement, zero requests during pointer movement. Exit code 1 on a miss; `target/W8/g2-result.json` holds every run (`g2-result-synthetic.json` for the fallback fixture, `-all-laps` appended for `--select all`).

## What is measured

- **Fixture.** `test-data/g2/` (W6, `tools/generate_fixtures.py --g2`): 83 catalog laps (82 race laps), 77 signals, 640 bins (the analysis's `lap_trace_columns`, #77), a 27.2 MB `traces.ccol`, profile `post-race-foundations-v25`, projection 4. Traces opens on the eight default channels for every lap, 3.4 MB of the body; six laps of them are 246 KB. The harness checks every file against the hashes in `fixture.json` and serves the bodies unchanged through the SPA's endpoints (`/api/v1/analyses/{id}/...`); `traces` with `channels` and `laps` queries is answered by re-encoding the subset (`src/investigation/fixtures/static-api.ts`). `--fixture synthetic` serves the generated fixture from `src/investigation/fixtures/synthetic.ts` instead (no race data). The fixture is a synthetic race, not a private recording.
- **First interactive.** Navigation start until the cursor draws markers and value cards for a real pointer move (`performance.mark("ri-cursor-first-draw")`). It includes fetching, decoding, building the chart and the first cursor frame.
- **Cursor fps, `driven`.** One synthetic `pointermove` per animation frame dispatched from inside the page for the whole sweep, so the cursor loop is the only variable. fps = animation frames / seconds.
- **Cursor fps, `real`.** A CDP mouse sweep from the test process while the page counts frames. It adds Playwright's input delivery and the browser's own hit testing and input handling. The gate uses the lower of the two medians.
- **Requests.** Every request made while either sweep runs. Pointer movement must make none.
- **Load.** The dev PC must be under 50 % CPU before each run (the P2 rule); the harness waits up to 90 s and records the load it measured. Concurrent builds on the dev PC move these numbers by several fps: record `cpuBeforePercent`.

Throttling is emulation on desktop Chromium, not a phone.

`node perf/views.mjs` renders Race, Runs, Sections and Traces at phone, tablet and desktop sizes (1280x720, 1536x864, 1920x1080), checks structure, console errors, horizontal scroll and 44 px phone targets, and writes screenshots to `target/W8/shots`. It then builds the real app and drives it at 1280x720, 1536x864, 1920x1080 and 1024x768 with the shell around the workspace (`shell-api.mjs` answers the shell's own reads from the fixture), on the one type scale (16 px root): no page scroll, the track map, the six trace rows and the six section channels inside the window, a 600 px window re-fits. It is not part of the gate.

`node perf/type-scale.mjs [--check]` audits the computed type of the real app, page by page, at 1920x1080 and 1536x864 (`--viewports`, `--pages`): the smallest and median size of body text, secondary text, numbers and tab and button labels, the shortest control, any canvas font, and screenshots (`--shots`). Sessions, Setups and Live come from `web/mock`; the investigation views from the g2 fixture served through the real app. `--check` exits 1 under the owner's minimums (body 16 px, secondary 14, numbers 15, tab and button labels 15, controls 36 px tall, 44 px on a phone).

## Cursor loop

`src/investigation/cursor/telemetry-cursor.ts`. Per pointer frame: one lookup of the two bins around the cursor on the shared bin grid (values are read linearly between them, #77), one canvas dirty-rect redraw (cursor line, one marker per visible lap and signal, the per-row value cards), a compositor-only transform for the map marker. The chart rect is cached, the trace SVG takes no pointer events, readouts under the map update at most every 100 ms. Work is bounded by the visible laps (the tooltip capacity of a row), not by the selected lap count.

`node perf/final-screens.mjs [--out dir] [--prefix final] [--size 1536x864]` screenshots every page of the real app (Sessions, Setups, a setup detail and Live from web/mock; Race, Runs, Sections and Traces from the g2 fixture) and fails on a console error. Not a gate.

# G2 harness

`pnpm -C web perf:g2` runs gate G2 (`docs/08-rewrite/workstreams.md`, Gates): Chromium via Playwright, CDP `Emulation.setCPUThrottlingRate` 6, phone 390x844 and tablet 1024x768, the committed 82-lap current-profile fixture, median of 3. Options: `--runs`, `--duration` (ms of pointer movement, default 10000), `--rate`, `--viewports phone,tablet,desktop`, `--select all` (every lap selected), `--fixture g2|synthetic`.

Pass: Traces first interactive <= 3 s, cursor >= 55 fps under continuous pointer movement, zero requests during pointer movement. Exit code 1 on a miss; `target/W8/g2-result.json` holds every run (`g2-result-synthetic.json` for the fallback fixture, `-all-laps` appended for `--select all`).

## What is measured

- **Fixture.** `test-data/g2/` (W6, `tools/generate_fixtures.py --g2`): 83 catalog laps (82 race laps), 77 signals, 160 bins, a 6.8 MB `traces.ccol`, current profile `post-race-foundations-v23`. The harness checks every file against the hashes in `fixture.json` and serves the bodies unchanged through the SPA's endpoints (`/api/v1/analyses/{id}/...`); `traces` with `channels` and `laps` queries is answered by re-encoding the subset (`src/investigation/fixtures/static-api.ts`). `--fixture synthetic` serves the generated fixture from `src/investigation/fixtures/synthetic.ts` instead (no race data). The fixture is a synthetic race, not a private recording.
- **First interactive.** Navigation start until the cursor draws markers and value cards for a real pointer move (`performance.mark("ri-cursor-first-draw")`). It includes fetching, decoding, building the chart and the first cursor frame.
- **Cursor fps, `driven`.** One synthetic `pointermove` per animation frame dispatched from inside the page for the whole sweep, so the cursor loop is the only variable. fps = animation frames / seconds.
- **Cursor fps, `real`.** A CDP mouse sweep from the test process while the page counts frames. It adds Playwright's input delivery and the browser's own hit testing and input handling. The gate uses the lower of the two medians.
- **Requests.** Every request made while either sweep runs. Pointer movement must make none.
- **Load.** The dev PC must be under 50 % CPU before each run (the P2 rule); the harness waits up to 90 s and records the load it measured. Concurrent builds on the dev PC move these numbers by several fps: record `cpuBeforePercent`.

Throttling is emulation on desktop Chromium, not a phone.

`node perf/views.mjs` renders Race, Runs, Sections and Traces at desktop, tablet and phone sizes, checks structure, console errors, horizontal scroll and 44 px phone targets, and writes screenshots to `target/W8/shots`. It is not part of the gate.

## Cursor loop

`src/investigation/cursor/telemetry-cursor.ts`. Per pointer frame: one bin lookup on the shared bin grid, one canvas dirty-rect redraw (cursor line, one marker per visible lap and signal, the per-row value cards), a compositor-only transform for the map marker. The chart rect is cached, the trace SVG takes no pointer events, readouts under the map update at most every 100 ms. Work is bounded by the visible laps (the tooltip capacity of a row), not by the selected lap count.

# G2 harness

`pnpm -C web perf:g2` runs gate G2 (`docs/08-rewrite/workstreams.md`, Gates): Chromium via Playwright, CDP `Emulation.setCPUThrottlingRate` 6, phone 390x844 and tablet 1024x768, the synthetic 82-lap fixture, median of 3. Options: `--runs`, `--duration` (ms of pointer movement, default 10000), `--rate`, `--viewports phone,tablet,desktop`.

Pass: Traces first interactive <= 3 s, cursor >= 55 fps under continuous pointer movement, zero requests during pointer movement. Exit code 1 on a miss; `target/W8/g2-result.json` holds every run.

## What is measured

- **Fixture.** `src/investigation/fixtures/synthetic.ts` generates 82 laps x 160 bins, 15 CCOL channels and a closed circuit with seeded noise. No race data. It stands in for a current-profile analysis until W5 provides a sanitized one; the harness serves it through the same endpoints the SPA uses (`/api/v1/analyses/{id}/...`).
- **First interactive.** Navigation start until the cursor draws markers and value cards for a real pointer move (`performance.mark("ri-cursor-first-draw")`). It includes fetching, decoding, building the chart and the first cursor frame.
- **Cursor fps, `driven`.** One synthetic `pointermove` per animation frame dispatched from inside the page for the whole sweep, so the cursor loop is the only variable. fps = animation frames / seconds.
- **Cursor fps, `real`.** A CDP mouse sweep from the test process while the page counts frames. It adds Playwright's input delivery and the browser's own hit testing and input handling. The gate uses the lower of the two medians.
- **Requests.** Every request made while either sweep runs. Pointer movement must make none.
- **Load.** The dev PC must be under 50 % CPU before each run (the P2 rule); the harness waits up to 90 s and records the load it measured. Concurrent builds on the dev PC move these numbers by several fps: record `cpuBeforePercent`.

Throttling is emulation on desktop Chromium, not a phone.

`node perf/views.mjs` renders Race, Runs, Sections and Traces at desktop, tablet and phone sizes, checks structure, console errors, horizontal scroll and 44 px phone targets, and writes screenshots to `target/W8/shots`. It is not part of the gate.

## Cursor loop

`src/investigation/cursor/telemetry-cursor.ts`. Per pointer frame: one bin lookup on the shared bin grid, one canvas dirty-rect redraw (cursor line, one marker per visible lap and signal, the per-row value cards), a compositor-only transform for the map marker. The chart rect is cached, the trace SVG takes no pointer events, readouts under the map update at most every 100 ms. Work is bounded by the visible laps (the tooltip capacity of a row), not by the selected lap count.

// G2 gate: the investigation SPA at 6x CPU throttle (workstreams.md, Gates).
//
//   pnpm -C web perf:g2 [-- --runs 3 --duration 10000 --rate 6 --viewports phone,tablet --fixture g2|synthetic]
//
// Chromium via Playwright, CDP Emulation.setCPUThrottlingRate, phone 390x844 and tablet 1024x768, the
// committed current-profile 82-lap fixture (test-data/g2, or the synthetic fallback), median of 3. Pass: Traces first interactive <= 3 s, cursor
// >= 55 fps under continuous pointer movement, zero requests during pointer movement.
//
// Two cursor measurements are taken per run. `driven` dispatches one pointermove per animation frame from
// inside the page (the cursor loop is the only variable); `real` moves a CDP mouse from the test process
// while the page counts frames (adds Playwright's input delivery). Both are reported; the gate uses the
// lower median. Nothing outside <repo>/target/W8 is written.
import { chromium } from "@playwright/test";
import { writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { buildHarness, outRoot, pageUrl, serve, VIEWPORTS } from "./harness-lib.mjs";

const args = process.argv.slice(2);
function option(name, fallback) {
  const index = args.indexOf(`--${name}`);
  return index >= 0 && args[index + 1] !== undefined ? args[index + 1] : fallback;
}
const RUNS = Number(option("runs", 3));
const DURATION_MS = Number(option("duration", 10_000));
const RATE = Number(option("rate", 6));
const FIXTURE = option("fixture", "g2");
const WANTED = option("viewports", "phone,tablet").split(",");
// `--select all` selects every lap before the sweeps: the worst case for per-frame work (82 logical traces, 24 detailed).
const SELECT_ALL = option("select", "best") === "all";
const RESULT_FILE = `g2-result${FIXTURE === "g2" ? "" : "-" + FIXTURE}${SELECT_ALL ? "-all-laps" : ""}.json`;
const GATE = { firstInteractiveMs: 3000, fps: 55, requests: 0 };

const median = (values) => {
  const sorted = [...values].sort((a, b) => a - b);
  const middle = sorted.length >> 1;
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
};

function cpuSample() {
  return os.cpus().reduce((sum, cpu) => ({ idle: sum.idle + cpu.times.idle, total: sum.total + Object.values(cpu.times).reduce((a, b) => a + b, 0) }), { idle: 0, total: 0 });
}

/** The proof's rule: the dev PC must be below 50 % CPU before a run (waits up to 90 s, then records the load). */
async function waitForQuietCpu() {
  let load = 100;
  for (let attempt = 0; attempt < 30; attempt++) {
    const a = cpuSample();
    await new Promise((resolve) => setTimeout(resolve, 3000));
    const b = cpuSample();
    load = 100 * (1 - (b.idle - a.idle) / Math.max(1, b.total - a.total));
    if (load < 50) return load;
  }
  return load;
}

async function measureOnce(browser, origin, fixture, name, viewport, rate, view = "traces") {
  const cpuBefore = await waitForQuietCpu();
  const context = await browser.newContext({ viewport, deviceScaleFactor: 1 });
  const page = await context.newPage();
  const cdp = await context.newCDPSession(page);
  await cdp.send("Emulation.setCPUThrottlingRate", { rate });
  const requests = [];
  page.on("request", (request) => requests.push({ url: request.url(), at: Date.now() }));
  const consoleErrors = [];
  page.on("pageerror", (error) => consoleErrors.push(String(error)));
  page.on("console", (message) => { if (message.type() === "error") consoleErrors.push(message.text()); });

  await page.goto(pageUrl(origin, fixture, view), { waitUntil: "commit" });
  await page.waitForFunction(() => window.__g2 && window.__g2.tracesPaintedMs !== null, null, { timeout: 120_000, polling: 100 });
  const surface = page.locator(".ri-chart-surface");
  await surface.scrollIntoViewIfNeeded();
  const box = await surface.boundingBox();
  if (!box) throw new Error("chart surface has no box");
  const y = Math.min(viewport.height - 4, Math.max(4, box.y + box.height / 2));

  // First interactive: the first tooltip a real pointer move makes visible, in page time since navigation start.
  const interactiveDeadline = Date.now() + 60_000;
  let step = 0;
  while (Date.now() < interactiveDeadline) {
    const done = await page.evaluate(() => window.__g2.firstInteractiveMs !== null);
    if (done) break;
    step += 1;
    await page.mouse.move(box.x + 40 + ((step * 37) % Math.max(10, box.width - 80)), y);
    await page.waitForTimeout(30);
  }
  const timing = await page.evaluate(() => ({ painted: window.__g2.tracesPaintedMs, interactive: window.__g2.firstInteractiveMs }));
  if (timing.interactive === null) throw new Error("the cursor never responded to the pointer");
  if (SELECT_ALL) {
    await page.evaluate(() => document.querySelectorAll(".ri-lap-row:not(.selected)").forEach((row) => row.click()));
    await page.waitForFunction(() => document.querySelectorAll(".ri-lap-row.selected").length >= 80, null, { timeout: 60_000 });
    await page.waitForTimeout(1500);
  }

  // Functional check: the cursor must really be drawing (markers on the canvas layer, text in the fixed tooltip DOM).
  const functional = await page.evaluate(() => {
    const diagnostics = window.__g2.diagnostics();
    return { cards: diagnostics.cursor?.cardsDrawn ?? 0, text: diagnostics.cursor?.lastCardText ?? "", diagnostics };
  });

  const requestsBefore = requests.length;
  const driven = await page.evaluate((ms) => window.__g2.drivenSweep(ms), DURATION_MS);
  const drivenDiagnostics = await page.evaluate(() => window.__g2.diagnostics());

  let moves = 0;
  const probe = page.evaluate((ms) => window.__g2.probe(ms), DURATION_MS);
  let finished = false;
  probe.then(() => { finished = true; });
  const sweepStart = Date.now();
  while (!finished) {
    const phase = ((Date.now() - sweepStart) / 1800) % 2;
    const t = phase < 1 ? phase : 2 - phase;
    await page.mouse.move(box.x + 8 + t * (box.width - 16), y);
    moves += 1;
  }
  const real = await probe;
  const requestsDuringPointer = requests.slice(requestsBefore).map((request) => request.url);

  const selectedLaps = await page.evaluate(() => document.querySelectorAll(".ri-lap-row.selected").length);
  await context.close();
  return {
    viewport: name,
    rate,
    cpuBeforePercent: Math.round(cpuBefore),
    tracesPaintedMs: Math.round(timing.painted),
    firstInteractiveMs: Math.round(timing.interactive),
    drivenFps: Number(driven.fps.toFixed(1)),
    drivenFramesOver25ms: driven.framesOver25ms,
    drivenMaxGapMs: Math.round(driven.maximumFrameGap),
    realFps: Number(real.fps.toFixed(1)),
    realFramesOver25ms: real.framesOver25ms,
    realMaxGapMs: Math.round(real.maximumFrameGap),
    realMouseMoves: moves,
    requestsDuringPointer,
    totalRequests: requests.length,
    selectedLaps,
    detailedTraces: drivenDiagnostics.cursor?.detailedTraceCount ?? 0,
    logicalTraces: drivenDiagnostics.cursor?.logicalTraceCount ?? 0,
    domNodes: functional.diagnostics.domNodes,
    pathNodes: functional.diagnostics.pathNodes,
    markersDrawn: drivenDiagnostics.cursor?.markersDrawn ?? 0,
    cardsDrawn: drivenDiagnostics.cursor?.cardsDrawn ?? 0,
    visibleTooltipCards: functional.cards,
    cardText: functional.text.slice(0, 80),
    errors: consoleErrors,
  };
}

async function main() {
  console.log("building harness ...");
  const { distDir, fixture } = await buildHarness(FIXTURE);
  const { server, origin } = await serve(distDir, fixture);
  const browser = await chromium.launch({ args: ["--disable-features=CalculateNativeWinOcclusion", "--disable-backgrounding-occluded-windows", "--disable-renderer-backgrounding"] });
  const results = { generatedAt: new Date().toISOString(), select: SELECT_ALL ? "all" : "best three", rate: RATE, fixture: fixture.name, analysisId: fixture.analysisId, fixtureLaps: fixture.laps, runs: RUNS, durationMs: DURATION_MS, browser: browser.version(), node: process.version, fixtureTraceBytes: fixture.ccolBytes, gate: GATE, viewports: {} };
  let failed = false;
  try {
    for (const name of WANTED) {
      const viewport = VIEWPORTS[name];
      if (!viewport) throw new Error(`unknown viewport ${name}`);
      const reference = await measureOnce(browser, origin, fixture, name, viewport, 1);
      const runs = [];
      for (let run = 0; run < RUNS; run++) {
        const result = await measureOnce(browser, origin, fixture, name, viewport, RATE);
        runs.push(result);
        console.log(`${name} run ${run + 1}: interactive ${result.firstInteractiveMs} ms, driven ${result.drivenFps} fps, real ${result.realFps} fps (${result.realMouseMoves} moves), requests ${result.requestsDuringPointer.length}`);
      }
      const summary = {
        firstInteractiveMs: median(runs.map((r) => r.firstInteractiveMs)),
        drivenFps: median(runs.map((r) => r.drivenFps)),
        realFps: median(runs.map((r) => r.realFps)),
        requestsDuringPointer: Math.max(...runs.map((r) => r.requestsDuringPointer.length)),
      };
      summary.cursorFps = Math.min(summary.drivenFps, summary.realFps);
      summary.pass = summary.firstInteractiveMs <= GATE.firstInteractiveMs && summary.cursorFps >= GATE.fps && summary.requestsDuringPointer === GATE.requests;
      const functionalOk = runs.every((r) => r.visibleTooltipCards > 0 && r.markersDrawn > 0 && r.errors.length === 0);
      summary.functionalOk = functionalOk;
      if (!summary.pass || !functionalOk) failed = true;
      results.viewports[name] = { reference, runs, summary };
    }
  } finally {
    await browser.close();
    server.close();
  }
  await writeFile(path.join(outRoot, RESULT_FILE), JSON.stringify(results, null, 2));
  console.log("\nG2 at 6x CPU throttle (median of runs)");
  console.log("viewport  first interactive   driven fps   real-input fps   requests   gate");
  for (const [name, entry] of Object.entries(results.viewports)) {
    const s = entry.summary;
    console.log(`${name.padEnd(9)} ${String(s.firstInteractiveMs).padStart(8)} ms      ${String(s.drivenFps).padStart(8)}      ${String(s.realFps).padStart(10)}       ${String(s.requestsDuringPointer).padStart(5)}      ${s.pass && s.functionalOk ? "PASS" : "FAIL"}`);
  }
  console.log(`result: ${path.join(outRoot, RESULT_FILE)}`);
  process.exitCode = failed ? 1 : 0;
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});

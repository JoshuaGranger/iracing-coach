// Renders Race, Runs, Sections and Traces from the synthetic fixture at desktop, tablet and phone sizes
// and checks structure, console errors, horizontal scroll and the 44 px phone targets. Screenshots go to
// <repo>/target/W8/shots. Not part of the G2 gate: `node perf/views.mjs`.
import { chromium } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import path from "node:path";
import { buildHarness, outRoot, serve, VIEWPORTS } from "./harness-lib.mjs";

const { distDir, fixture } = await buildHarness();
const { server, origin } = await serve(distDir, fixture);
const browser = await chromium.launch();
const shots = path.join(outRoot, "shots");
await mkdir(shots, { recursive: true });
let failures = 0;
const check = (name, ok, detail = "") => {
  if (!ok) failures += 1;
  console.log(`${ok ? "ok  " : "FAIL"} ${name}${detail ? ` (${detail})` : ""}`);
};

for (const [sizeName, viewport] of Object.entries(VIEWPORTS)) {
  for (const view of ["race", "runs", "sections", "traces"]) {
    const context = await browser.newContext({ viewport });
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", (error) => errors.push(String(error)));
    page.on("console", (message) => { if (message.type() === "error") errors.push(message.text()); });
    await page.goto(`${origin}/?view=${view}`);
    const ready = { race: ".ri-dot", runs: ".ri-dot", sections: "[data-ri-chart]", traces: "[data-analysis-trace-path]" }[view];
    await page.waitForSelector(ready, { timeout: 30_000 });
    await page.waitForTimeout(400);
    const label = `${sizeName}/${view}`;
    const metrics = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      innerWidth: window.innerWidth,
      scrollHeight: document.documentElement.scrollHeight,
      innerHeight: window.innerHeight,
      small: [...document.querySelectorAll(".ri-tab, .ri-button, .ri-text-button, .ri-lap-row, .ri-section, .ri-comparison")]
        .map((element) => ({ name: element.className, box: element.getBoundingClientRect() }))
        .filter((item) => item.box.width > 0 && item.box.height > 0 && item.box.height < 44)
        .map((item) => `${item.name}:${Math.round(item.box.height)}`),
    }));
    check(`${label} renders without console errors`, errors.length === 0, errors.join(" | ").slice(0, 200));
    check(`${label} has no horizontal page scroll`, metrics.scrollWidth <= metrics.innerWidth + 1, `${metrics.scrollWidth} vs ${metrics.innerWidth}`);
    if (sizeName === "desktop" || sizeName === "tablet") {
      // The viewport-bound workspace fits 1280x720 and 1024x768 without page scroll (stacked layouts start at 900 px).
      if (sizeName === "desktop") check(`${label} fits the viewport height`, metrics.scrollHeight <= metrics.innerHeight + 1, `${metrics.scrollHeight} vs ${metrics.innerHeight}`);
    }
    if (sizeName === "phone") check(`${label} targets are 44 px`, metrics.small.length === 0, [...new Set(metrics.small)].slice(0, 4).join(", "));

    if (view === "sections") {
      const before = await page.locator("[data-ri-position]").textContent();
      const chart = page.locator("[data-ri-chart]").first();
      const box = await chart.boundingBox();
      await page.mouse.move(box.x + box.width * 0.8, box.y + box.height / 2);
      await page.waitForTimeout(200);
      const after = await page.locator("[data-ri-position]").textContent();
      check(`${label} pointer moves the inspection cursor`, before !== after, `${before} -> ${after}`);
      check(`${label} shows six channels`, (await page.locator("[data-ri-chart]").count()) === 6);
    }
    if (view === "traces") {
      const surface = page.locator(".ri-chart-surface");
      await surface.scrollIntoViewIfNeeded();
      const box = await surface.boundingBox();
      const y = Math.min(viewport.height - 4, Math.max(4, box.y + box.height / 2));
      await page.mouse.move(box.x + box.width * 0.5, y);
      await page.mouse.move(box.x + box.width * 0.55, y);
      await page.waitForTimeout(200);
      const diag = await page.evaluate(() => window.__g2.diagnostics());
      check(`${label} cursor draws markers and cards`, diag.cursor.markersDrawn > 0 && diag.cursor.cardsDrawn > 0, JSON.stringify({ markers: diag.cursor.markersDrawn, cards: diag.cursor.cardsDrawn }));
      check(`${label} shows the six critical rows`, (await page.locator("[data-analysis-render-row]").count()) === 6);
      const readout = await page.locator(".ri-cursor-readout strong").textContent();
      check(`${label} map readout follows the cursor`, readout !== "25.0%", readout ?? "");
      await page.screenshot({ path: path.join(shots, `${sizeName}-traces-hover.png`) });
    } else {
      await page.screenshot({ path: path.join(shots, `${sizeName}-${view}.png`) });
    }
    await context.close();
  }
}
await browser.close();
server.close();
console.log(failures === 0 ? "all view checks passed" : `${failures} view checks failed`);
process.exitCode = failures === 0 ? 0 : 1;

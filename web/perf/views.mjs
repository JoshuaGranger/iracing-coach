// Renders Race, Runs, Sections and Traces from the committed g2 fixture (or `--fixture synthetic`) at phone, tablet and desktop sizes
// (1280x720, 1536x864, 1920x1080) and checks structure, console errors, horizontal scroll and the 44 px phone targets. Screenshots go to
// <repo>/target/W8/shots. It then drives the real app (shell included) at 1280x720, 1536x864, 1920x1080 and 1024x768, at each text size
// step (Default, Larger, Largest; `--steps default` for one), and checks that each view is bound to the window with the map, the
// context and the critical rows inside it. Not part of the G2 gate: `node perf/views.mjs`.
import { chromium } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import path from "node:path";
import { buildHarness, buildShell, outRoot, pageUrl, serve, serveShell, shellUrl, VIEWPORTS } from "./harness-lib.mjs";

const fixtureName = process.argv.includes("--fixture") ? process.argv[process.argv.indexOf("--fixture") + 1] : "g2";
const STEPS = (process.argv.includes("--steps") ? process.argv[process.argv.indexOf("--steps") + 1] : "default,larger,largest").split(",");
const { distDir, fixture } = await buildHarness(fixtureName);
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
    await page.goto(pageUrl(origin, fixture, view));
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
    // The viewport-bound workspace fits the desktop sizes without page scroll (stacked layouts start at 900 px).
    if (["desktop", "laptop", "large"].includes(sizeName)) check(`${label} fits the viewport height`, metrics.scrollHeight <= metrics.innerHeight + 1, `${metrics.scrollHeight} vs ${metrics.innerHeight}`);
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

// The workspace inside the real shell (top bar, main padding, page wrapper): no page scroll, and the panels a view is
// for are inside the window, not below the fold.
const shellServer = await serveShell(await buildShell(), fixture);
const inside = (box, viewport) => box !== null && box.x >= -1 && box.y >= -1 && box.x + box.width <= viewport.width + 1 && box.y + box.height <= viewport.height + 1;
for (const sizeName of ["desktop", "laptop", "large", "tablet"]) {
  const viewport = VIEWPORTS[sizeName];
  for (const step of STEPS) {
  for (const view of ["race", "runs", "sections", "traces"]) {
    const context = await browser.newContext({ viewport });
    // The text size this device last showed is a hint in localStorage that the app applies before its first render.
    await context.addInitScript((value) => { try { localStorage.setItem("coach.text-size", value); } catch { /* blocked */ } }, step);
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", (error) => errors.push(String(error)));
    page.on("console", (message) => { if (message.type() === "error") errors.push(message.text()); });
    await page.goto(shellUrl(shellServer.origin, view));
    const ready = { race: ".ri-dot", runs: ".ri-dot", sections: "[data-ri-chart]", traces: "[data-analysis-trace-path]" }[view];
    await page.waitForSelector(ready, { timeout: 30_000 });
    await page.waitForTimeout(400);
    const label = `shell ${sizeName}/${step}/${view}`;
    check(`${label} is at the ${step} text size`, (await page.evaluate(() => document.documentElement.dataset.textSize)) === step);
    const height = () => page.evaluate(() => ({ document: document.documentElement.scrollHeight, window: window.innerHeight }));
    const measured = await height();
    check(`${label} renders without console errors`, errors.length === 0, errors.join(" | ").slice(0, 200));
    check(`${label} fits the viewport height`, measured.document <= measured.window + 1, `${measured.document} vs ${measured.window}`);
    const box = async (selector) => page.locator(selector).first().boundingBox();
    check(`${label} workspace is inside the window`, inside(await box(".ri-workspace"), viewport));
    if (view === "sections") {
      const channelBoxes = await Promise.all([0, 1, 2, 3, 4, 5].map((index) => page.locator("[data-ri-chart]").nth(index).boundingBox()));
      const allInside = channelBoxes.every((item) => inside(item, viewport));
      const region = await page.evaluate(() => {
        const element = document.querySelector(".ri-channels");
        const box = element.getBoundingClientRect();
        return { bottom: box.bottom, scrolls: element.scrollHeight > element.clientHeight + 1 };
      });
      // Default and Larger keep all six channels in the window. At Largest, where the text leaves a short window too little height for
      // six readable rows, the channels scroll inside their panel while the map, the section list and the header stay in view.
      check(`${label} shows the six channels inside the window${allInside ? "" : " (scrolling in the panel)"}`, allInside || (step === "largest" && region.scrolls && region.bottom <= viewport.height + 1));
      const squeezed = await page.evaluate(() => [...document.querySelectorAll(".ri-channel")].filter((channel) => channel.querySelector(".ri-channel-label").scrollHeight > channel.clientHeight + 1).length);
      check(`${label} channel labels are not squeezed`, squeezed === 0, `${squeezed} squeezed`);
    }
    if (view === "traces") {
      check(`${label} track map is inside the window`, inside(await box(".ri-track-map"), viewport));
      const rows = await Promise.all([0, 1, 2, 3, 4, 5].map((index) => page.locator("[data-analysis-render-row]").nth(index).boundingBox()));
      check(`${label} six critical rows are inside the window`, rows.every((item) => inside(item, viewport)), rows.map((item) => item && Math.round(item.height)).join(","));
      const crowded = await page.evaluate(() => [...document.querySelectorAll(".trace-row-label-shell")].filter((shell) => shell.firstElementChild.scrollHeight > shell.clientHeight + 1).length);
      check(`${label} row labels are not squeezed`, crowded === 0, `${crowded} squeezed`);
    }
    // A shorter window re-fits without a reload.
    await page.setViewportSize({ width: viewport.width, height: 600 });
    await page.waitForTimeout(250);
    const shorter = await height();
    check(`${label} re-fits a 600 px window`, shorter.document <= 601, `${shorter.document}`);
    await page.screenshot({ path: path.join(shots, `shell-${sizeName}-${step}-${view}.png`) });
    await context.close();
  }
  }
}
await browser.close();
server.close();
shellServer.server.close();
console.log(failures === 0 ? "all view checks passed" : `${failures} view checks failed`);
process.exitCode = failures === 0 ? 0 : 1;

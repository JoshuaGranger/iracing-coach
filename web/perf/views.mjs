// Renders Race, Runs, Sections and Traces from the committed g2 fixture (or `--fixture synthetic`) at phone, tablet and desktop sizes
// (1280x720, 1536x864, 1920x1080) and checks structure, console errors, horizontal scroll and the 44 px phone targets. Screenshots go to
// <repo>/target/W8/shots. It then drives the real app (shell included) at 1280x720, 1536x864, 1920x1080 and 1024x768 on the one type
// scale, and checks that each view is bound to the window with the map, the context and the critical rows inside it. Not part of
// the G2 gate: `node perf/views.mjs`.
import { chromium } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import path from "node:path";
import { buildHarness, buildShell, outRoot, pageUrl, serve, serveShell, shellUrl, VIEWPORTS } from "./harness-lib.mjs";

const fixtureName = process.argv.includes("--fixture") ? process.argv[process.argv.indexOf("--fixture") + 1] : "g2";
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
      small: [...document.querySelectorAll(".ri-tab, .ri-button, .ri-pill-button, .ri-pill-toggle, .ri-icon-pill, .ri-pqr-seg, .ri-text-button, .ri-lap-row, .ri-section, .ri-comparison")]
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
      const readout = await page.locator(".ri-readout-head strong").textContent();
      check(`${label} map readout follows the cursor`, readout !== "25.0%", readout ?? "");
      await page.screenshot({ path: path.join(shots, `${sizeName}-traces-hover.png`) });
      if (sizeName !== "phone") {
        // The rail is one grid: every visible cell starts where its header column starts.
        const misaligned = await page.evaluate(() => {
          const head = [...document.querySelectorAll(".ri-rail-cols > span")].map((cell) => cell.getBoundingClientRect());
          let bad = 0;
          for (const row of [...document.querySelectorAll(".ri-lap-row")].slice(0, 30)) {
            [...row.children].forEach((cell, index) => {
              const box = cell.getBoundingClientRect();
              if (box.width > 0 && head[index] && head[index].width > 0 && Math.abs(box.left - head[index].left) > 0.5) bad += 1;
            });
          }
          return bad;
        });
        check(`${label} rail cells sit in their header columns`, misaligned === 0, `${misaligned} off`);
        const zero = await page.locator("[data-zero-lap]").textContent();
        check(`${label} names the zero lap`, /Δ vs L\d+/.test(zero ?? ""), zero ?? "");
        // A click selects without leaving a focus box; the keyboard still shows one.
        const row = page.locator(".ri-lap-row[data-lap='9']");
        await row.click();
        const ring = await row.evaluate((element) => getComputedStyle(element).outlineStyle);
        check(`${label} a clicked lap shows no focus box`, ring === "none", ring);
        await page.keyboard.press("ArrowDown");
        const keyed = await page.evaluate(() => getComputedStyle(document.activeElement).outlineStyle);
        check(`${label} the keyboard shows the focus ring`, keyed !== "none", keyed);
        await page.getByRole("button", { name: "All", exact: true }).click();
        const selectedCount = await page.locator(".ri-lap-row.selected").count();
        check(`${label} All selects every lap the filters allow`, selectedCount === fixture.laps, `${selectedCount} of ${fixture.laps}`);
        await page.getByRole("button", { name: "Clean", exact: true }).click();
        const cleanRows = await page.locator(".ri-lap-row").count();
        check(`${label} Clean keeps the clean laps`, cleanRows > 0 && cleanRows < fixture.laps, `${cleanRows}`);
      }
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
  for (const view of ["race", "runs", "sections", "traces"]) {
    const context = await browser.newContext({ viewport });
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", (error) => errors.push(String(error)));
    page.on("console", (message) => { if (message.type() === "error") errors.push(message.text()); });
    await page.goto(shellUrl(shellServer.origin, view));
    const ready = { race: ".ri-dot", runs: ".ri-dot", sections: "[data-ri-chart]", traces: "[data-analysis-trace-path]" }[view];
    await page.waitForSelector(ready, { timeout: 30_000 });
    await page.waitForTimeout(400);
    const label = `shell ${sizeName}/${view}`;
    const rootPx = await page.evaluate(() => parseFloat(getComputedStyle(document.documentElement).fontSize));
    check(`${label} is on the one type scale (16 px root)`, rootPx === 16, `${rootPx} px`);
    const height = () => page.evaluate(() => ({ document: document.documentElement.scrollHeight, window: window.innerHeight }));
    const measured = await height();
    check(`${label} renders without console errors`, errors.length === 0, errors.join(" | ").slice(0, 200));
    check(`${label} fits the viewport height`, measured.document <= measured.window + 1, `${measured.document} vs ${measured.window}`);
    const box = async (selector) => page.locator(selector).first().boundingBox();
    check(`${label} workspace is inside the window`, inside(await box(".ri-workspace"), viewport));
    if (view === "sections") {
      const channelBoxes = await Promise.all([0, 1, 2, 3, 4, 5].map((index) => page.locator("[data-ri-chart]").nth(index).boundingBox()));
      check(`${label} shows the six channels inside the window`, channelBoxes.every((item) => inside(item, viewport)));
      const squeezed = await page.evaluate(() => [...document.querySelectorAll(".ri-channel")].filter((channel) => channel.querySelector(".ri-channel-label").scrollHeight > channel.clientHeight + 1).length);
      check(`${label} channel labels are not squeezed`, squeezed === 0, `${squeezed} squeezed`);
    }
    if (view === "traces") {
      check(`${label} track map is inside the window`, inside(await box(".ri-track-map"), viewport));
      check(`${label} lap rail is inside the window`, inside(await box(".ri-lap-rail"), viewport));
      const railScrolls = await page.evaluate(() => { const element = document.querySelector(".ri-lap-scroll"); return element.scrollHeight > element.clientHeight; });
      check(`${label} the rail scrolls inside its card`, railScrolls);
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
    await page.screenshot({ path: path.join(shots, `shell-${sizeName}-${view}.png`) });
    await context.close();
  }
}
await browser.close();
server.close();
shellServer.server.close();
console.log(failures === 0 ? "all view checks passed" : `${failures} view checks failed`);
process.exitCode = failures === 0 ? 0 : 1;

// Quick fit probe for one shell size: `node perf/probe-fit.mjs 1280x720 [traces]`. Prints the boxes the viewport rule cares
// about and writes a screenshot to <repo>/target/W8/probe.png. A development aid, not a gate.
import { chromium } from "@playwright/test";
import path from "node:path";
import { buildHarness, buildShell, outRoot, serveShell, shellUrl } from "./harness-lib.mjs";

const [size = "1280x720", view = "traces"] = process.argv.slice(2);
const [width, height] = size.split("x").map(Number);
const { fixture } = await buildHarness("g2");
const served = await serveShell(await buildShell(), fixture);
const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width, height } });
const page = await context.newPage();
await page.goto(shellUrl(served.origin, view));
await page.waitForSelector(view === "traces" ? "[data-analysis-trace-path]" : ".ri-dot", { timeout: 30_000 });
await page.waitForTimeout(500);
const boxes = await page.evaluate(() => {
  const pick = (selector) => { const element = document.querySelector(selector); if (!element) return null; const box = element.getBoundingClientRect(); return [Math.round(box.top), Math.round(box.bottom), Math.round(box.height)]; };
  return {
    workspace: pick(".ri-workspace"), bar: pick(".ri-bar"), strip: pick(".ri-track-panel"), head: pick(".ri-trace-head"), surface: pick(".ri-chart-surface"),
    rows: [...document.querySelectorAll("[data-analysis-render-row]")].map((row) => Math.round(row.getBoundingClientRect().bottom)),
    document: document.documentElement.scrollHeight,
  };
});
console.log(JSON.stringify(boxes));
await page.screenshot({ path: path.join(outRoot, "probe.png") });
await browser.close();
served.server.close();

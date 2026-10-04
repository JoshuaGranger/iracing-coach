// Screenshots of the investigation views at 1536x864 from the committed g2 fixture, inside the real app shell (`--harness` for the
// bare workspace). Writes investigation-*.png to `--out` (default <repo>/target/W8/screens). Not a gate: `node perf/screens.mjs`.
import { chromium } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import path from "node:path";
import { buildHarness, buildShell, outRoot, pageUrl, serve, serveShell, shellUrl } from "./harness-lib.mjs";

const args = process.argv.slice(2);
const option = (name, fallback) => (args.includes(`--${name}`) ? args[args.indexOf(`--${name}`) + 1] : fallback);
const out = path.resolve(option("out", path.join(outRoot, "screens")));
const harnessOnly = args.includes("--harness");
const viewport = { width: Number(option("width", 1536)), height: Number(option("height", 864)) };
await mkdir(out, { recursive: true });

const { distDir, fixture } = await buildHarness("g2");
const served = harnessOnly ? await serve(distDir, fixture) : await serveShell(await buildShell(), fixture);
const url = (view) => (harnessOnly ? pageUrl(served.origin, fixture, view) : shellUrl(served.origin, view));
const browser = await chromium.launch();
const ready = { race: ".ri-dot", runs: ".ri-dot", sections: "[data-ri-chart]", traces: "[data-analysis-trace-path]" };
const shots = [];

async function open(view) {
  const context = await browser.newContext({ viewport, deviceScaleFactor: 1 });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(String(error)));
  page.on("console", (message) => { if (message.type() === "error") errors.push(message.text()); });
  await page.goto(url(view));
  await page.waitForSelector(ready[view], { timeout: 30_000 });
  await page.waitForTimeout(500);
  return { context, page, errors };
}

async function shot(page, name) {
  const file = path.join(out, `investigation-${name}.png`);
  await page.screenshot({ path: file });
  shots.push(file);
}

{
  const { context, page, errors } = await open("traces");
  const surface = await page.locator(".ri-chart-surface").boundingBox();
  await page.mouse.move(surface.x + surface.width * 0.38, surface.y + surface.height * 0.4);
  await page.waitForTimeout(250);
  await shot(page, "traces");
  // Spotlight the fastest lap, then hover the pit stop in the rail.
  await page.locator("select[aria-label='Spotlight lap']").selectOption("6");
  const pit = page.locator("[data-pit-stop]").first();
  await pit.scrollIntoViewIfNeeded();
  await pit.hover();
  await page.waitForTimeout(250);
  await shot(page, "traces-pit-hover");
  await page.mouse.move(5, 5);
  // Clicking a lap leaves no focus box behind.
  await page.locator(".ri-lap-row[data-lap='7']").click();
  await page.locator(".ri-lap-row[data-lap='9']").click();
  await page.mouse.move(surface.x + surface.width * 0.6, surface.y + surface.height * 0.5);
  await page.waitForTimeout(250);
  await shot(page, "traces-selection");
  await page.getByRole("button", { name: "Customize" }).click();
  await page.waitForTimeout(250);
  await shot(page, "traces-customize");
  await page.getByRole("button", { name: "Customize" }).click();
  await page.getByRole("button", { name: "Full screen" }).click();
  await page.waitForTimeout(400);
  await shot(page, "traces-full-screen");
  if (errors.length) console.log(`traces console errors: ${errors.join(" | ")}`);
  await context.close();
}
{
  const { context, page, errors } = await open("race");
  await shot(page, "race");
  const dot = page.locator("[data-position-lap='20']");
  const box = await dot.boundingBox();
  if (box) {
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.waitForTimeout(200);
    await shot(page, "race-position-hover");
  }
  const marker = page.locator("[data-pit-marker]").first();
  if (await marker.count()) {
    const tag = await marker.locator(".ri-pit-tag").boundingBox();
    await page.mouse.move(tag.x + tag.width / 2, tag.y + tag.height / 2);
    await page.waitForTimeout(200);
    await shot(page, "race-pit-hover");
  }
  if (errors.length) console.log(`race console errors: ${errors.join(" | ")}`);
  await context.close();
}
for (const view of ["runs", "sections"]) {
  const { context, page, errors } = await open(view);
  await shot(page, view);
  if (errors.length) console.log(`${view} console errors: ${errors.join(" | ")}`);
  await context.close();
}
await browser.close();
served.server.close();
console.log(shots.join("\n"));

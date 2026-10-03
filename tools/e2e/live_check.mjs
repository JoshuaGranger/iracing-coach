// Live page check while a collector streams (dev PC: the collector's --replay of make_sdkfix.py output).
// For each viewport it waits for the collector's presence, opens the Live page, waits for live frames,
// reads every tile's text and takes a screenshot. Writes live-check.json to --out.
//
//   node tools/e2e/live_check.mjs --base http://127.0.0.1:8291 --out <scratch>/shots/live [--hold-ms 6000]
import { createRequire } from "node:module";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const { chromium } = createRequire(path.join(repo, "web", "package.json"))("@playwright/test");

const arg = (name, fallback = null) => {
  const i = process.argv.indexOf(`--${name}`);
  return i > 0 ? process.argv[i + 1] : fallback;
};
const base = arg("base");
const out = arg("out");
const holdMs = Number(arg("hold-ms", "6000"));
await mkdir(out, { recursive: true });

const VIEWPORTS = {
  desktop: { width: 1280, height: 720, touch: false },
  tablet: { width: 1024, height: 768, touch: true },
  phone: { width: 390, height: 844, touch: true },
};
const api = async (p) => (await fetch(base + p)).json();
const results = [];
let failures = 0;
const check = (viewport, name, ok, detail = "") => {
  if (!ok) failures += 1;
  results.push({ viewport, name, ok, detail });
  console.log(`${new Date().toISOString().slice(11, 23)} ${ok ? "ok  " : "FAIL"} ${viewport} ${name}${detail ? ` (${detail})` : ""}`);
};

// Presence: the collector reports the driver in session (collector-protocol section 5).
let driver = null;
for (let i = 0; i < 60 && !driver?.live; i++) {
  driver = (await api("/api/v1/drivers")).drivers.find((d) => d.live) ?? (await api("/api/v1/drivers")).drivers[0];
  if (!driver?.live) await new Promise((r) => setTimeout(r, 500));
}
const status = await api("/api/v1/status");
check("api", "driver shows live", driver?.live === true, JSON.stringify(driver));
check("api", "status lists the collector", status.collectors.length > 0, JSON.stringify(status.collectors).slice(0, 400));

const browser = await chromium.launch();
for (const [vp, size] of Object.entries(VIEWPORTS)) {
  const context = await browser.newContext({ viewport: { width: size.width, height: size.height }, hasTouch: size.touch });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  let frames = 0;
  page.on("websocket", (ws) => ws.on("framereceived", (f) => { if (typeof f.payload !== "string") frames += 1; }));

  await page.goto(`${base}/#/d/${driver.customer_id}/sessions`);
  await page.waitForSelector(".session-row", { timeout: 15_000 }).catch(() => {});
  const liveLink = await page.locator("a[href$='/live']").allTextContents();
  check(vp, "sessions page offers Live", liveLink.length > 0, liveLink.join(" | "));
  await page.screenshot({ path: path.join(out, `${vp}-sessions-live.png`) });

  const opened = Date.now();
  await page.goto(`${base}/#/d/${driver.customer_id}/live`);
  const live = await page.waitForSelector("[data-testid=live-status][data-live=true]", { timeout: 20_000 }).then(() => true, () => false);
  check(vp, "live page reaches Live", live, `${Date.now() - opened} ms after opening`);
  await page.waitForTimeout(holdMs);
  const tiles = await page.$$eval("[data-metric]", (els) => els.map((e) => ({ metric: e.getAttribute("data-metric"), text: e.textContent.replace(/\s+/g, " ").trim() })));
  const waiting = tiles.filter((t) => /^.{0,40}Waiting/.test(t.text));
  check(vp, "tiles show values", tiles.length > 0 && waiting.length < tiles.length, `${tiles.length} tiles, ${waiting.length} waiting`);
  check(vp, "binary frames received", frames > 0, `${frames} frames in ${Math.round((Date.now() - opened) / 1000)} s`);
  const m = await page.evaluate(() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]);
  check(vp, "no horizontal page scroll", m[0] <= m[1], `${m[0]} vs ${m[1]}`);
  check(vp, "no console errors", errors.length === 0, errors.slice(0, 3).join(" | ").slice(0, 300));
  results.push({ viewport: vp, name: "tile texts", ok: true, detail: tiles });
  await page.screenshot({ path: path.join(out, `${vp}-live.png`), fullPage: true });
  await context.close();
}
await browser.close();
await writeFile(path.join(out, "live-check.json"), JSON.stringify({ base, failures, results }, null, 1));
console.log(failures === 0 ? "all live checks passed" : `${failures} live checks failed`);
process.exitCode = failures === 0 ? 0 : 1;

// Screenshots of every v1 page against the released service at one size (default 1536x864), for a
// release record: Sessions, Race, Runs, Sections, Traces (cursor at 38 %), Setups, a setup detail and
// Live. The live-server counterpart of web/perf/final-screens.mjs (which shoots the mock and the g2
// fixture). Writes `<prefix>-<page>.png` to --out. Read-only except the preference writes the SPA
// itself makes (last investigation). Exit 1 on a console error or a page that does not render.
//
//   node tools/rig/live_screens.mjs --base http://192.168.1.81:8210 --out <dir> [--prefix live] [--size 1536x864] [--session ID] [--setup ID]
import { createRequire } from "node:module";
import { mkdir } from "node:fs/promises";
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
if (!base || !out) throw new Error("usage: live_screens.mjs --base URL --out DIR [--prefix live] [--size WxH] [--session ID] [--setup ID]");
const prefix = arg("prefix", "live");
const [width, height] = arg("size", "1536x864").split("x").map(Number);
await mkdir(out, { recursive: true });

const api = async (p) => (await fetch(base + p)).json();
const driver = (await api("/api/v1/drivers")).drivers[0];
const c = driver.customer_id;
const sessions = (await api(`/api/v1/drivers/${c}/sessions?limit=200`)).sessions;
const sessionId = Number(arg("session", 0)) || sessions.find((s) => s.analysis_state === "ready" && s.session_type === "Race")?.session_id;
const setups = (await api(`/api/v1/drivers/${c}/setups?limit=200`)).setups;
const setupId = Number(arg("setup", 0)) || setups.find((s) => s.names.length > 0)?.setup_id || setups[0]?.setup_id;

const PAGES = {
  sessions: { hash: `/d/${c}/sessions`, ready: "li.session-row" },
  race: { hash: `/d/${c}/s/${sessionId}/race`, ready: ".ri-dot" },
  runs: { hash: `/d/${c}/s/${sessionId}/runs`, ready: ".ri-dot" },
  sections: { hash: `/d/${c}/s/${sessionId}/sections`, ready: "[data-ri-chart]" },
  traces: { hash: `/d/${c}/s/${sessionId}/traces`, ready: "[data-analysis-trace-path]", cursor: 0.38 },
  setups: { hash: `/d/${c}/setups`, ready: "section.s-car a.s-name" },
  "setup-detail": { hash: `/d/${c}/setups/${setupId}`, ready: "[aria-label='Setup values']" },
  live: { hash: `/d/${c}/live`, ready: "[data-testid='live-status']" },
};

const browser = await chromium.launch();
const problems = [];
try {
  for (const [name, page] of Object.entries(PAGES)) {
    const context = await browser.newContext({ viewport: { width, height }, deviceScaleFactor: 1 });
    const tab = await context.newPage();
    tab.on("pageerror", (e) => problems.push(`${name}: ${e}`));
    tab.on("console", (m) => { if (m.type() === "error") problems.push(`${name}: ${m.text()}`); });
    await tab.goto(`${base}/#${page.hash}`);
    const ok = await tab.waitForSelector(page.ready, { timeout: 30_000 }).then(() => true, () => false);
    if (!ok) problems.push(`${name}: ${page.ready} did not appear`);
    await tab.waitForTimeout(800);
    if (page.cursor) {
      const surface = await tab.locator(".ri-chart-surface").first().boundingBox();
      if (surface) await tab.mouse.move(surface.x + surface.width * page.cursor, surface.y + surface.height * 0.4);
      await tab.waitForTimeout(250);
    }
    const file = path.join(out, `${prefix}-${name}.png`);
    await tab.screenshot({ path: file });
    console.log(`${ok ? "ok  " : "FAIL"} ${file}`);
    await context.close();
  }
} finally {
  await browser.close();
}
console.log(JSON.stringify({ base, sessionId, setupId, width, height }));
if (problems.length) {
  console.log(`problems:\n${problems.join("\n")}`);
  process.exitCode = 1;
}

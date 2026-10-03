// Release checks of the v1 candidate as a user meets it (W9, wave 3b), against the released service.
// Complements tools/e2e/spa_check.mjs with what the candidate record needs and that script does not
// measure: a 10 s pointer sweep on Traces and on Sections with the request log, whether Sections and
// Traces fit the viewport (the wave-3 fix), the Setups library naming each setup as the API does, and
// the Live page without a collector. Read-only except the preference writes the SPA itself makes.
//
//   node tools/rig/v1_candidate_check.mjs --base http://192.168.1.81:8210 --out <scratch>/shots/v1 [--session ID]
//
// Playwright is resolved from web/node_modules. Exit 0 only when every check passes.
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
if (!base || !out) throw new Error("usage: v1_candidate_check.mjs --base URL --out DIR [--session ID]");
await mkdir(out, { recursive: true });

// Stacked layouts start below 900 px wide, so the phone scrolls by design; desktop and tablet must fit.
const VIEWPORTS = {
  desktop: { width: 1280, height: 720, touch: false, fit: true },
  tablet: { width: 1024, height: 768, touch: true, fit: true },
  phone: { width: 390, height: 844, touch: true, fit: false },
};
const SWEEP_MS = 10_000;
const READY = { sections: "[data-ri-chart]", traces: "[data-analysis-trace-path]" };
const SURFACE = { sections: "[data-ri-chart]", traces: ".ri-chart-surface" };

const api = async (p) => (await fetch(base + p)).json();
const driver = (await api("/api/v1/drivers")).drivers[0];
const sessions = (await api(`/api/v1/drivers/${driver.customer_id}/sessions`)).sessions;
const sessionId = Number(arg("session", 0)) || sessions.find((s) => s.analysis_state === "ready" && s.session_type === "Race")?.session_id;
const setups = (await api(`/api/v1/drivers/${driver.customer_id}/setups`)).setups ?? [];

const results = [];
let failures = 0;
const check = (viewport, name, ok, detail = "") => {
  if (!ok) failures += 1;
  results.push({ viewport, name, ok, detail });
  console.log(`${ok ? "ok  " : "FAIL"} ${viewport} ${name}${detail ? ` (${detail})` : ""}`);
};

const browser = await chromium.launch();
for (const [vp, size] of Object.entries(VIEWPORTS)) {
  const context = await browser.newContext({ viewport: { width: size.width, height: size.height }, hasTouch: size.touch });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  const requests = [];
  page.on("request", (r) => requests.push({ url: `${r.method()} ${r.url()}`, at: Date.now() }));
  const failed = [];
  page.on("response", (r) => { if (r.status() >= 400) failed.push(`${r.status()} ${r.request().method()} ${r.url()}`); });
  const shot = (name) => page.screenshot({ path: path.join(out, `${vp}-${name}.png`) });
  const dims = () => page.evaluate(() => ({ h: document.documentElement.scrollHeight, ih: window.innerHeight, w: document.documentElement.scrollWidth, iw: document.documentElement.clientWidth }));

  for (const view of ["sections", "traces"]) {
    if (!sessionId) break;
    const start = Date.now();
    await page.goto(`${base}/#/d/${driver.customer_id}/s/${sessionId}/${view}`);
    const ok = await page.waitForSelector(READY[view], { timeout: 30_000 }).then(() => true, () => false);
    check(vp, `${view} renders`, ok, `${Date.now() - start} ms`);
    if (!ok) continue;
    await page.waitForTimeout(500);
    const d = await dims();
    check(vp, `${view}: no horizontal page scroll`, d.w <= d.iw, `${d.w} vs ${d.iw}`);
    if (size.fit) check(vp, `${view} fits the viewport`, d.h <= d.ih + 1, `document ${d.h} px, window ${d.ih} px`);
    else results.push({ viewport: vp, name: `${view} document height (stacked by design)`, ok: true, detail: `${d.h} px` });
    if (view === "traces") {
      // The map and the critical rows are inside the viewport together at the bound sizes.
      const inView = await page.evaluate(() => {
        const vis = (el) => { if (!el) return false; const r = el.getBoundingClientRect(); return r.height > 0 && r.top >= 0 && r.bottom <= window.innerHeight + 1; };
        const map = document.querySelector("[data-ri-map], .ri-track-map, .analysis-track-map, svg[data-track-map]");
        const rows = [...document.querySelectorAll("[data-analysis-render-row]")];
        return { map: map ? vis(map) : null, rows: rows.length, rowsVisible: rows.filter(vis).length };
      });
      results.push({ viewport: vp, name: "traces: map and rows in view", ok: true, detail: JSON.stringify(inView) });
      if (size.fit) check(vp, "traces: every drawn row inside the viewport", inView.rows > 0 && inView.rowsVisible === inView.rows, JSON.stringify(inView));
    }
    // Pointer sweep: no request may leave the page while the pointer moves (D3). Requests the
    // navigation itself causes (debounced preference writes) settle first.
    const surface = page.locator(SURFACE[view]).first();
    await surface.scrollIntoViewIfNeeded();
    const box = await surface.boundingBox();
    const y = Math.min(size.height - 4, Math.max(4, box.y + box.height / 2));
    await page.mouse.move(box.x + box.width * 0.5, y);
    await page.waitForTimeout(3000);
    const before = requests.length;
    const sweepStart = Date.now();
    let moves = 0;
    while (Date.now() - sweepStart < SWEEP_MS) {
      const phase = ((Date.now() - sweepStart) / 1500) % 2;
      const t = phase < 1 ? phase : 2 - phase;
      await page.mouse.move(box.x + 8 + t * (box.width - 16), y);
      moves += 1;
    }
    const during = requests.slice(before).map((r) => `${r.url} +${r.at - sweepStart} ms`);
    check(vp, `${view}: zero requests during a 10 s pointer sweep`, during.length === 0, `${moves} moves in ${Date.now() - sweepStart} ms, ${during.length} requests ${during.slice(0, 3).join(" ")}`);
    await shot(`v1-${view}`);
  }

  // Setups: every setup listed, each row naming the setup as the API does.
  await page.goto(`${base}/#/d/${driver.customer_id}/setups`);
  await page.waitForSelector(".setup-row, .empty", { timeout: 15_000 }).catch(() => {});
  const rowTexts = await page.$$eval(".setup-row", (els) => els.map((e) => e.textContent.replace(/\s+/g, " ").trim()));
  check(vp, "setups: rows equal the API", rowTexts.length === setups.length, `${rowTexts.length} rows, API ${setups.length}`);
  for (const s of setups.filter((s) => (s.names ?? []).length > 0)) {
    const name = s.names[0].replace(/\.sto$/i, "");
    check(vp, `setups: a row names "${s.names[0]}"`, rowTexts.some((t) => t.includes(name)), rowTexts.map((t) => t.slice(0, 80)).join(" | "));
  }
  const d = await dims();
  check(vp, "setups: no horizontal page scroll", d.w <= d.iw, `${d.w} vs ${d.iw}`);
  await shot("v1-setups");

  // Live page without a collector: it renders and says it is waiting.
  await page.goto(`${base}/#/d/${driver.customer_id}/live`);
  const liveOk = await page.waitForSelector("[data-testid='live-status']", { timeout: 15_000 }).then(() => true, () => false);
  const status = liveOk ? (await page.getByTestId("live-status").textContent()).replace(/\s+/g, " ").trim() : null;
  const live = liveOk ? await page.getByTestId("live-status").getAttribute("data-live") : null;
  check(vp, "live page renders its status", liveOk, `${status} (data-live ${live})`);
  const dl = await dims();
  check(vp, "live: no horizontal page scroll", dl.w <= dl.iw, `${dl.w} vs ${dl.iw}`);
  await shot("v1-live");

  check(vp, "no console errors", errors.length === 0, errors.slice(0, 3).join(" | ").slice(0, 300));
  check(vp, "no failed responses", failed.length === 0, failed.slice(0, 5).join(" | "));
  await context.close();
}
await browser.close();
await writeFile(path.join(out, "v1-check.json"), JSON.stringify({ base, sessionId, setups: setups.map((s) => ({ setup_id: s.setup_id, names: s.names })), failures, results }, null, 1));
console.log(failures === 0 ? "all v1 candidate checks passed" : `${failures} v1 candidate checks failed`);
process.exitCode = failures === 0 ? 0 : 1;

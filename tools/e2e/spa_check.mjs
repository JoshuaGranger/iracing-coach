// Browser checks of the SPA as a user meets it, against a running coach-server (dev PC or the mini PC),
// at desktop 1280x720, tablet 1024x768 and phone 390x844 (D11). Read-only except the preference writes
// the SPA itself makes (last investigation, trace layout). Screenshots and a JSON report go to --out.
//
//   node tools/e2e/spa_check.mjs --base http://127.0.0.1:8291 --out <scratch>/shots/dev [--session ID]
//
// --session is the investigation to open; without it, the first session with analysis_state "ready"
// and type Race. Playwright is resolved from web/node_modules.
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
if (!base || !out) throw new Error("usage: spa_check.mjs --base URL --out DIR [--session ID]");
await mkdir(out, { recursive: true });

const VIEWPORTS = {
  desktop: { width: 1280, height: 720, touch: false },
  tablet: { width: 1024, height: 768, touch: true },
  phone: { width: 390, height: 844, touch: true },
};
const READY = { race: ".ri-dot", runs: ".ri-dot", sections: "[data-ri-chart]", traces: "[data-analysis-trace-path]" };

const api = async (p) => (await fetch(base + p)).json();
const drivers = (await api("/api/v1/drivers")).drivers;
const driver = drivers[0];
const sessions = (await api(`/api/v1/drivers/${driver.customer_id}/sessions`)).sessions;
const sessionId = Number(arg("session", 0)) || sessions.find((s) => s.analysis_state === "ready" && s.session_type === "Race")?.session_id;
const setups = (await api(`/api/v1/drivers/${driver.customer_id}/setups`));

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
  const notFound = [];
  page.on("response", (r) => { if (r.status() >= 400) notFound.push(`${r.status()} ${r.request().method()} ${r.url()}`); });
  const shot = (name) => page.screenshot({ path: path.join(out, `${vp}-${name}.png`) });
  const noHScroll = async (label) => {
    const m = await page.evaluate(() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]);
    check(vp, `${label}: no horizontal page scroll`, m[0] <= m[1], `${m[0]} vs ${m[1]}`);
  };

  // Drivers landing: one driver opens straight to that driver's sessions (D7).
  const t0 = Date.now();
  await page.goto(`${base}/`);
  await page.waitForURL(new RegExp(`#/d/${driver.customer_id}/sessions`), { timeout: 15_000 }).catch(() => {});
  check(vp, "landing opens the single driver's sessions", page.url().includes(`#/d/${driver.customer_id}/sessions`), page.url());

  // Sessions library: every session listed with its analysis state.
  await page.waitForSelector(".session-row", { timeout: 15_000 }).catch(() => {});
  const rows = await page.$$eval(".session-row", (els) => els.map((e) => ({ state: e.getAttribute("data-state"), text: e.textContent.replace(/\s+/g, " ").trim() })));
  check(vp, "library lists every session", rows.length === sessions.length, `${rows.length} rows, API ${sessions.length}`);
  const apiStates = sessions.map((s) => s.analysis_state).sort().join(",");
  check(vp, "library states equal the API", rows.map((r) => r.state).sort().join(",") === apiStates, rows.map((r) => r.state).join(","));
  // The library labels iRacing's session types: Lone Qualify as Qualifying, Offline Testing as Test.
  const label = (t) => (/qualify/i.test(t) ? "Qualifying" : /test/i.test(t) ? "Test" : /practice/i.test(t) ? "Practice" : /race/i.test(t) ? "Race" : t);
  for (const t of new Set(sessions.map((s) => s.session_type))) {
    check(vp, `library shows a ${t} session as ${label(t)}`, rows.some((r) => r.text.startsWith(label(t))), rows.map((r) => r.text.slice(0, 60)).join(" | "));
  }
  check(vp, "library loaded in under 5 s", Date.now() - t0 < 5000, `${Date.now() - t0} ms`);
  await noHScroll("library");
  await shot("library");

  // Investigation: Race, Runs, Sections, Traces of the analysed race.
  if (sessionId) {
    for (const view of ["race", "runs", "sections", "traces"]) {
      const start = Date.now();
      await page.goto(`${base}/#/d/${driver.customer_id}/s/${sessionId}/${view}`);
      const ok = await page.waitForSelector(READY[view], { timeout: 30_000 }).then(() => true, () => false);
      check(vp, `${view} renders`, ok, `${Date.now() - start} ms`);
      await page.waitForTimeout(400);
      await noHScroll(view);
      if (view === "sections" && ok) {
        const before = await page.locator("[data-ri-position]").first().textContent();
        const chart = page.locator("[data-ri-chart]").first();
        await chart.scrollIntoViewIfNeeded();
        const box = await chart.boundingBox();
        await page.mouse.move(box.x + box.width * 0.8, box.y + box.height / 2);
        await page.waitForTimeout(250);
        const after = await page.locator("[data-ri-position]").first().textContent();
        check(vp, "sections: pointer moves the inspection cursor", before !== after, `${before} -> ${after}`);
      }
      if (view === "traces" && ok) {
        const surface = page.locator(".ri-chart-surface").first();
        await surface.scrollIntoViewIfNeeded();
        const box = await surface.boundingBox();
        const y = Math.min(size.height - 4, Math.max(4, box.y + box.height / 2));
        await page.mouse.move(box.x + box.width * 0.3, y);
        await page.waitForTimeout(300);
        const readoutA = await page.locator(".ri-cursor-readout strong").first().textContent().catch(() => null);
        const markerA = await page.evaluate(() => {
          const m = document.querySelector("[data-ri-map-marker], .ri-map-marker, .ri-track-marker");
          return m ? `${m.getAttribute("transform") ?? ""}|${m.getAttribute("style") ?? ""}|${m.getAttribute("cx") ?? ""}` : null;
        });
        // Pointer sweep: no request may leave the page while the pointer moves (D3). Requests the
        // navigation itself causes (debounced preference writes) settle first.
        await page.waitForTimeout(3000);
        const before = requests.length;
        const sweepStart = Date.now();
        let moves = 0;
        while (Date.now() - sweepStart < 5000) {
          const phase = ((Date.now() - sweepStart) / 1500) % 2;
          const t = phase < 1 ? phase : 2 - phase;
          await page.mouse.move(box.x + 8 + t * (box.width - 16), y);
          moves += 1;
        }
        const during = requests.slice(before).map((r) => `${r.url} +${r.at - sweepStart} ms`);
        check(vp, "traces: zero requests during a 5 s pointer sweep", during.length === 0, `${moves} moves, ${during.length} requests ${during.slice(0, 3).join(" ")}`);
        await page.mouse.move(box.x + box.width * 0.7, y);
        await page.waitForTimeout(300);
        const readoutB = await page.locator(".ri-cursor-readout strong").first().textContent().catch(() => null);
        const markerB = await page.evaluate(() => {
          const m = document.querySelector("[data-ri-map-marker], .ri-map-marker, .ri-track-marker");
          return m ? `${m.getAttribute("transform") ?? ""}|${m.getAttribute("style") ?? ""}|${m.getAttribute("cx") ?? ""}` : null;
        });
        check(vp, "traces: map readout follows the chart cursor", readoutA !== null && readoutA !== readoutB, `${readoutA} -> ${readoutB}`);
        results.push({ viewport: vp, name: "traces map marker", ok: true, detail: `${markerA} -> ${markerB}` });
        const rowsDrawn = await page.locator("[data-analysis-render-row]").count();
        check(vp, "traces: critical rows drawn", rowsDrawn >= 1, `${rowsDrawn} rows`);
      }
      await shot(view);
    }
  }

  // Setups: the library lists the recordings' setups, and a setup shows its values.
  await page.goto(`${base}/#/d/${driver.customer_id}/setups`);
  await page.waitForSelector(".setup-row, .unknown-files, .empty", { timeout: 15_000 }).catch(() => {});
  const setupRows = await page.locator(".setup-row").count();
  check(vp, "setups library lists setups", setupRows > 0 && setupRows === (setups.setups?.length ?? setupRows), `${setupRows} rows, API ${setups.setups?.length}`);
  await noHScroll("setups");
  await shot("setups");
  if (setupRows > 0) {
    await page.locator(".setup-row").first().click();
    await page.waitForSelector("[aria-label='Setup values']", { timeout: 15_000 }).catch(() => {});
    const count = await page.locator("[aria-label='Setup values'] .count").first().textContent().catch(() => "0");
    check(vp, "setup detail shows values", Number(count) > 0, `${count} values`);
    await noHScroll("setup detail");
    await shot("setup-detail");
  }
  check(vp, "no console errors", errors.length === 0, errors.slice(0, 3).join(" | ").slice(0, 300));
  check(vp, "no failed responses", notFound.length === 0, notFound.slice(0, 5).join(" | "));
  await context.close();
}
await browser.close();
await writeFile(path.join(out, "spa-check.json"), JSON.stringify({ base, sessionId, failures, results }, null, 1));
console.log(failures === 0 ? "all SPA checks passed" : `${failures} SPA checks failed`);
process.exitCode = failures === 0 ? 0 : 1;

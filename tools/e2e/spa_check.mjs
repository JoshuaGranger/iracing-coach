// Browser checks of the SPA as a user meets it, against a running coach-server (dev PC or the mini PC),
// at desktop 1280x720, tablet 1024x768 and phone 390x844 (D11). Read-only except the preference writes
// the SPA itself makes (last investigation, trace layout). Screenshots and a JSON report go to --out.
//
//   node tools/e2e/spa_check.mjs --base http://127.0.0.1:8291 --out <scratch>/shots/dev [--session ID]
//
// --session is the investigation to open; without it, the first session with analysis_state "ready"
// and type Race. Playwright is resolved from web/node_modules.
//
// Web round 1 (2026-10-04) structure: Sessions is a list of event cards (`section.sx-event`) holding one
// tile per session (`li.session-row[data-session][data-state][data-phase]`), drawn 24 events at a time
// ("Older sessions"); Setups is car cards (`section.s-car`) whose named entries fold their versions and
// whose unnamed garage captures fold into one stack; Traces names its time-delta zero lap in the header
// (`.ri-trace-count[data-zero-lap]`, "Δ vs L6").
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
const SWEEP_MS = 10_000;

const api = async (p) => (await fetch(base + p)).json();
/** Every page of a cursor listing, as the SPA reads it. */
const all = async (p, key) => {
  const rows = [];
  let cursor = null;
  for (let i = 0; i < 50; i++) {
    const page = await api(`${p}${p.includes("?") ? "&" : "?"}limit=200${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`);
    rows.push(...page[key]);
    cursor = page.next_cursor ?? null;
    if (!cursor) return { rows, page };
  }
  throw new Error(`${p}: runaway cursor`);
};
const drivers = (await api("/api/v1/drivers")).drivers;
const driver = drivers[0];
const sessions = (await all(`/api/v1/drivers/${driver.customer_id}/sessions`, "sessions")).rows;
const sessionId = Number(arg("session", 0)) || sessions.find((s) => s.analysis_state === "ready" && s.session_type === "Race")?.session_id;
const setupList = await all(`/api/v1/drivers/${driver.customer_id}/setups`, "setups");
const setups = setupList.rows;
const unlinkedFiles = (await api(`/api/v1/drivers/${driver.customer_id}/setups?limit=200`)).unlinked_files ?? [];
const prefs = await api(`/api/v1/drivers/${driver.customer_id}/prefs`);
const sessionFilters = prefs.session_filters ?? null;
const filtered = sessionFilters && Object.values(sessionFilters).some((v) => (Array.isArray(v) ? v.length > 0 : v != null));

const results = [];
let failures = 0;
const check = (viewport, name, ok, detail = "") => {
  if (!ok) failures += 1;
  results.push({ viewport, name, ok, detail });
  console.log(`${ok ? "ok  " : "FAIL"} ${viewport} ${name}${detail ? ` (${detail})` : ""}`);
};
const typeLabel = (t) => {
  const s = (t ?? "").toLowerCase();
  return s.includes("race") ? "Race" : s.includes("qual") ? "Qualifying" : s.includes("practice") ? "Practice" : s.includes("test") || s.includes("offline") ? "Test" : null;
};

const browser = await chromium.launch();
// Warm-up: the first page load in a freshly launched browser costs several seconds of browser start-up
// (about 7.5 s measured against a service that answers in 0.2 s). Load the app once, unmeasured, so the
// library time limit below measures the service and the SPA, not the harness.
{
  const warm = await browser.newContext();
  const warmPage = await warm.newPage();
  const w0 = Date.now();
  await warmPage.goto(`${base}/`, { waitUntil: "load", timeout: 60_000 }).catch(() => {});
  console.log(`warm-up load ${Date.now() - w0} ms (not checked)`);
  await warm.close();
}
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

  // Sessions: event cards, one tile per session with its analysis state; "Older sessions" draws the rest.
  const firstRows = await page.waitForSelector("li.session-row", { timeout: 15_000 }).then(() => true, () => false);
  check(vp, "sessions: first event cards drawn in under 5 s", firstRows && Date.now() - t0 < 5000, `${Date.now() - t0} ms`);
  const events0 = await page.locator("section.sx-event").count();
  await noHScroll("sessions");
  await shot("library");
  for (let i = 0; i < 100 && (await page.locator("button.load-more").count()) > 0; i++) {
    await page.locator("button.load-more").click();
    await page.waitForTimeout(60);
  }
  const rows = await page.$$eval("li.session-row", (els) =>
    els.map((e) => ({ id: Number(e.getAttribute("data-session")), state: e.getAttribute("data-state"), band: e.querySelector(".sx-band b")?.textContent?.trim() ?? "" })),
  );
  const events = await page.locator("section.sx-event").count();
  const ids = new Set(rows.map((r) => r.id));
  const missing = sessions.filter((s) => !ids.has(s.session_id)).map((s) => s.session_id);
  if (filtered) {
    results.push({ viewport: vp, name: "sessions: saved filters in effect, every-session check skipped", ok: true, detail: JSON.stringify(sessionFilters) });
  } else {
    check(vp, "sessions: every session has a tile", missing.length === 0 && rows.length === sessions.length, `${rows.length} tiles in ${events} events (${events0} drawn first), API ${sessions.length}; missing ${missing.slice(0, 10).join(",")}`);
    const byId = new Map(sessions.map((s) => [s.session_id, s]));
    const wrongState = rows.filter((r) => byId.get(r.id) && byId.get(r.id).analysis_state !== r.state);
    check(vp, "sessions: tile states equal the API", wrongState.length === 0, `${wrongState.length} differ ${wrongState.slice(0, 5).map((r) => `${r.id}:${r.state}`).join(",")}`);
    for (const t of new Set(sessions.map((s) => s.session_type))) {
      const want = typeLabel(t);
      const tiles = rows.filter((r) => byId.get(r.id)?.session_type === t);
      check(vp, `sessions: a ${t} session reads ${want ?? "its own label"}`, tiles.length > 0 && (want === null || tiles.every((r) => r.band === want)), `${tiles.length} tiles, bands ${[...new Set(tiles.map((r) => r.band))].join("/")}`);
    }
  }
  await noHScroll("sessions, all drawn");

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
        const head = page.locator(".ri-trace-count").first();
        const zero = await head.getAttribute("data-zero-lap").catch(() => null);
        const headText = (await head.textContent().catch(() => "")).replace(/\s+/g, " ").trim();
        check(vp, "traces: the header names the zero lap", !!zero && headText.includes(`Δ vs L${zero}`), `"${headText}" (data-zero-lap ${zero})`);
        const surface = page.locator(".ri-chart-surface").first();
        await surface.scrollIntoViewIfNeeded();
        const box = await surface.boundingBox();
        const y = Math.min(size.height - 4, Math.max(4, box.y + box.height / 2));
        await page.mouse.move(box.x + box.width * 0.3, y);
        await page.waitForTimeout(300);
        const readoutA = await page.locator(".ri-cursor-readout strong").first().textContent().catch(() => null);
        // Pointer sweep: no request may leave the page while the pointer moves (D3). Requests the
        // navigation itself causes (debounced preference writes) settle first.
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
        check(vp, "traces: zero requests during a 10 s pointer sweep", during.length === 0, `${moves} moves, ${during.length} requests ${during.slice(0, 3).join(" ")}`);
        await page.mouse.move(box.x + box.width * 0.7, y);
        await page.waitForTimeout(300);
        const readoutB = await page.locator(".ri-cursor-readout strong").first().textContent().catch(() => null);
        check(vp, "traces: map readout follows the chart cursor", readoutA !== null && readoutA !== readoutB, `${readoutA} -> ${readoutB}`);
        const rowsDrawn = await page.locator("[data-analysis-render-row]").count();
        check(vp, "traces: critical rows drawn", rowsDrawn >= 1, `${rowsDrawn} rows`);
      }
      await shot(view);
    }
  }

  // Setups: car cards; with every version list and capture stack open, each API setup has one link.
  await page.goto(`${base}/#/d/${driver.customer_id}/setups`);
  await page.waitForSelector("section.s-car, .empty", { timeout: 15_000 }).catch(() => {});
  await noHScroll("setups");
  await shot("setups");
  for (let i = 0; i < 20 && (await page.locator("button.pill.load-more").count()) > 0; i++) {
    await page.locator("button.pill.load-more").click();
    await page.waitForTimeout(300);
  }
  for (const toggle of await page.locator("button.versions[aria-expanded=false], button.s-toggle[aria-expanded=false]").all()) await toggle.click();
  const linked = await page.$$eval("section.s-car a.s-name", (els) => els.map((a) => Number((a.getAttribute("href") ?? "").split("/setups/")[1])).filter(Number.isFinite));
  const linkedIds = new Set(linked);
  const unlisted = setups.filter((s) => !linkedIds.has(s.setup_id)).map((s) => s.setup_id);
  check(vp, "setups: every API setup reachable from a car card", unlisted.length === 0 && linkedIds.size === setups.length, `${linkedIds.size} setups linked, API ${setups.length}; unlisted ${unlisted.join(",")}`);
  const fileRows = await page.locator("li[data-entry=file]").count();
  check(vp, "setups: files with values unknown listed", fileRows === unlinkedFiles.length, `${fileRows} rows, API ${unlinkedFiles.length}`);
  if (setups.length > 0) {
    await page.goto(`${base}/#/d/${driver.customer_id}/setups/${setups[0].setup_id}`);
    await page.waitForSelector("[aria-label='Setup values']", { timeout: 15_000 }).catch(() => {});
    const count = await page.locator("[aria-label='Setup values'] .count").first().textContent().catch(() => "0");
    check(vp, `setup ${setups[0].setup_id} detail shows values`, Number(count) > 0, `${count} values`);
    await noHScroll("setup detail");
    await shot("setup-detail");
  }
  check(vp, "no console errors", errors.length === 0, errors.slice(0, 3).join(" | ").slice(0, 300));
  check(vp, "no failed responses", notFound.length === 0, notFound.slice(0, 5).join(" | "));
  await context.close();
}
await browser.close();
await writeFile(path.join(out, "spa-check.json"), JSON.stringify({ base, sessionId, sessions: sessions.length, setups: setups.length, failures, results }, null, 1));
console.log(failures === 0 ? "all SPA checks passed" : `${failures} SPA checks failed`);
process.exitCode = failures === 0 ? 0 : 1;

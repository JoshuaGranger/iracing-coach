// Live stream behaviour across one replayed session (collector-protocol section 6, D5, D6), as a browser sees it.
// Run while a collector replays a make_sdkfix.py fixture with a long on-track stretch (--track-s 150).
//
//   node tools/e2e/live_soak.mjs --base http://192.168.1.81:8210 --out <scratch>/soak --phase pre
//   node tools/e2e/live_soak.mjs --base http://192.168.1.81:8210 --out <scratch>/soak --phase run [--setup-name "e2e garage capture.sto"]
//
// pre: with no collector, the driver is not live and no viewport offers a Live link.
// run: (1) presence timeline from /api/v1/status every 250 ms for the whole run (presence.jsonl);
//      (2) viewer A (desktop) opens the Live page: frames/s from the frame headers over 10 s, seq continuity,
//          latency (browser receive time minus the collector's captured_unix_ms, same clock on the dev PC);
//      (3) viewer B (phone) joins while A watches: A's seq continuity and largest gap across the join,
//          session_epoch unchanged, viewers 2; then B leaves;
//      (4) A leaves: streaming must stay true while the replay session continues (on track, garage, stall)
//          and turn false only when the sim exits;
//      (5) after the garage commit, Setups at desktop, tablet and phone lists the captured setup by name.
// Writes soak.json and screenshots to --out. Read-only apart from opening live sockets (viewer counts).
import { createRequire } from "node:module";
import { appendFile, mkdir, writeFile } from "node:fs/promises";
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
const phase = arg("phase", "run");
const setupName = arg("setup-name", "e2e garage capture.sto");
const endTimeoutS = Number(arg("end-timeout-s", "400"));
await mkdir(out, { recursive: true });

const VIEWPORTS = {
  desktop: { width: 1280, height: 720, touch: false },
  tablet: { width: 1024, height: 768, touch: true },
  phone: { width: 390, height: 844, touch: true },
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const iso = () => new Date().toISOString();
const api = async (p) => (await fetch(base + p)).json();
const results = [];
let failures = 0;
const check = (scope, name, ok, detail = "") => {
  if (!ok) failures += 1;
  results.push({ at: iso(), scope, name, ok, detail });
  console.log(`${iso().slice(11, 23)} ${ok ? "ok  " : "FAIL"} ${scope} ${name}${detail ? ` (${typeof detail === "string" ? detail : JSON.stringify(detail)})` : ""}`);
};
const note = (scope, name, detail) => {
  results.push({ at: iso(), scope, name, ok: true, detail });
  console.log(`${iso().slice(11, 23)} note ${scope} ${name} (${typeof detail === "string" ? detail : JSON.stringify(detail)})`);
};

const browser = await chromium.launch();
const viewer = async (vp) => {
  const size = VIEWPORTS[vp];
  const context = await browser.newContext({ viewport: { width: size.width, height: size.height }, hasTouch: size.touch });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  return { vp, context, page, errors };
};

// Frame header (live-frame.md): flags @3, seq u32 @4, session_epoch u32 @8, captured_unix_ms u64 @12.
const decodeHeader = (payload) => {
  const b = Buffer.isBuffer(payload) ? payload : Buffer.from(payload);
  if (b.length < 38 || b[0] !== 0x4c || b[1] !== 0x46) return null;
  return { flags: b[3], seq: b.readUInt32LE(4), epoch: b.readUInt32LE(8), captured: Number(b.readBigUInt64LE(12)) };
};
const recordFrames = (page) => {
  const frames = [];
  page.on("websocket", (ws) => {
    if (!ws.url().includes("/ws/live/")) return;
    ws.on("framereceived", (f) => {
      if (typeof f.payload === "string") return;
      const h = decodeHeader(f.payload);
      if (h) frames.push({ ...h, recv: Date.now(), backlog: (h.flags & 0x40) !== 0 });
    });
  });
  return frames;
};
const liveFrames = (frames, from, to) => frames.filter((f) => !f.backlog && f.recv >= from && f.recv < to);
const stats = (fs) => {
  if (fs.length < 2) return { frames: fs.length };
  const span = (fs[fs.length - 1].captured - fs[0].captured) / 1000;
  let gaps = 0, maxGap = 0, seqJumps = 0;
  const epochs = new Set();
  const lat = fs.map((f) => f.recv - f.captured).sort((a, b) => a - b);
  for (let i = 1; i < fs.length; i++) {
    const g = fs[i].recv - fs[i - 1].recv;
    maxGap = Math.max(maxGap, g);
    if (fs[i].seq !== fs[i - 1].seq + 1) seqJumps += 1;
    gaps += fs[i].seq - fs[i - 1].seq - 1;
  }
  for (const f of fs) epochs.add(f.epoch);
  return {
    frames: fs.length,
    fps: Math.round(((fs.length - 1) / span) * 10) / 10,
    seq_first: fs[0].seq, seq_last: fs[fs.length - 1].seq, seq_skipped: gaps, seq_jumps: seqJumps,
    max_gap_ms: maxGap, epochs: [...epochs],
    latency_ms: { p50: lat[Math.floor(lat.length * 0.5)], p95: lat[Math.floor(lat.length * 0.95)], max: lat[lat.length - 1] },
  };
};

// The Live indicator: the nav's dot (app.tsx, aria-label "Live now") and the Sessions page's "Live now" button.
const indicator = async (page) => ({
  dots: await page.locator(".live-dot").count(),
  liveNow: await page.locator("a.btn", { hasText: "Live now" }).count(),
});

const drivers = (await api("/api/v1/drivers")).drivers;
const driver = drivers[0];

if (phase === "pre") {
  check("api", "driver not live without a collector", drivers.every((d) => d.live === false), JSON.stringify(drivers));
  const status = await api("/api/v1/status");
  check("api", "no collector online", status.collectors.every((c) => !c.online), JSON.stringify(status.collectors).slice(0, 300));
  for (const vp of Object.keys(VIEWPORTS)) {
    const v = await viewer(vp);
    await v.page.goto(`${base}/#/d/${driver.customer_id}/sessions`);
    await v.page.waitForSelector(".session-row", { timeout: 15_000 }).catch(() => {});
    const ind = await indicator(v.page);
    check(vp, "no Live indicator without a collector (nav dot, Live now)", ind.dots === 0 && ind.liveNow === 0, ind);
    await v.page.screenshot({ path: path.join(out, `${vp}-pre-sessions.png`) });
    await v.context.close();
  }
  await browser.close();
  await writeFile(path.join(out, "soak-pre.json"), JSON.stringify({ base, failures, results }, null, 1));
  process.exitCode = failures === 0 ? 0 : 1;
  process.exit();
}

// (1) presence timeline
const timeline = [];
let polling = true;
let lastKey = "";
const latest = { c: null };
const poller = (async () => {
  while (polling) {
    try {
      const s = await api("/api/v1/status");
      const d = (await api("/api/v1/drivers")).drivers.find((x) => x.customer_id === driver.customer_id);
      const c = s.collectors.find((x) => x.online) ?? s.collectors[0] ?? null;
      latest.c = c;
      const row = {
        online: c?.online ?? false, viewers: c?.viewers ?? null, sim: c?.status?.sim ?? null, on_track: c?.status?.on_track ?? null,
        in_garage: c?.status?.in_garage ?? null, on_pit_road: c?.status?.on_pit_road ?? null, streaming: c?.status?.streaming ?? null,
        uploads_pending: c?.status?.queue?.uploads_pending ?? null, bytes_pending: c?.status?.queue?.bytes_pending ?? null,
        driver_live: d?.live ?? null, spool_bytes: s.spool.bytes, spool_files: s.spool.files, queue: s.queue,
      };
      const key = JSON.stringify(row);
      if (key !== lastKey) {
        lastKey = key;
        const entry = { at: iso(), ...row };
        timeline.push(entry);
        await appendFile(path.join(out, "presence.jsonl"), JSON.stringify(entry) + "\n");
      }
    } catch (e) {
      timeline.push({ at: iso(), error: String(e) });
    }
    await sleep(250);
  }
})();

const waitFor = async (pred, timeoutMs) => {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    if (pred(latest.c)) return true;
    await sleep(100);
  }
  return false;
};
check("api", "collector on track", await waitFor((c) => c?.status?.on_track === true, 60_000));
await sleep(5_500); // at least one 5 s status since the collector connected
check("api", "not streaming before any viewer", latest.c?.status?.streaming === false && latest.c?.viewers === 0,
  JSON.stringify({ viewers: latest.c?.viewers, streaming: latest.c?.status?.streaming }));

// (2) viewer A
const a = await viewer("desktop");
const aFrames = recordFrames(a.page);
await a.page.goto(`${base}/#/d/${driver.customer_id}/sessions`);
await a.page.waitForSelector(".session-row", { timeout: 15_000 }).catch(() => {});
await a.page.waitForSelector(".live-dot", { timeout: 10_000 }).catch(() => {});
const ind = await indicator(a.page);
check("desktop", "Live indicator on Sessions (nav dot and Live now)", ind.dots > 0 && ind.liveNow > 0, ind);
await a.page.screenshot({ path: path.join(out, "desktop-sessions-live.png") });
const aOpen = Date.now();
await a.page.goto(`${base}/#/d/${driver.customer_id}/live`);
const aLive = await a.page.waitForSelector("[data-testid=live-status][data-live=true]", { timeout: 20_000 }).then(() => true, () => false);
check("desktop", "viewer A reaches Live", aLive, `${Date.now() - aOpen} ms`);
check("api", "viewers 1 and streaming", await waitFor((c) => c?.viewers === 1 && c?.status?.streaming === true, 10_000),
  JSON.stringify({ viewers: latest.c?.viewers, streaming: latest.c?.status?.streaming }));
await sleep(2_000);
const w1 = Date.now();
await sleep(10_000);
const sA = stats(liveFrames(aFrames, w1, Date.now()));
check("desktop", "viewer A ~60 frames/s over 10 s", sA.fps >= 57 && sA.fps <= 61, sA);
const tiles = await a.page.$$eval("[data-metric]", (els) => els.map((e) => ({ metric: e.getAttribute("data-metric"), text: e.textContent.replace(/\s+/g, " ").trim() })));
check("desktop", "tiles show values", tiles.length > 0 && tiles.some((t) => !/Waiting/.test(t.text)), tiles);
await a.page.screenshot({ path: path.join(out, "desktop-viewer-a.png") });

// (3) viewer B joins while A watches
const b = await viewer("phone");
const bFrames = recordFrames(b.page);
const bOpen = Date.now();
await b.page.goto(`${base}/#/d/${driver.customer_id}/live`);
const bLive = await b.page.waitForSelector("[data-testid=live-status][data-live=true]", { timeout: 20_000 }).then(() => true, () => false);
check("phone", "viewer B reaches Live", bLive, `${Date.now() - bOpen} ms`);
check("api", "viewers 2", await waitFor((c) => c?.viewers === 2, 10_000), `viewers ${latest.c?.viewers}`);
await sleep(8_000);
const bClose = Date.now();
const sAjoin = stats(liveFrames(aFrames, bOpen - 3_000, bClose));
check("desktop", "viewer A uninterrupted while B joins (seq continuous, one epoch, gap < 250 ms)",
  sAjoin.seq_jumps === 0 && sAjoin.epochs.length === 1 && sAjoin.max_gap_ms < 250, sAjoin);
const sB = stats(liveFrames(bFrames, bOpen + 2_000, bClose));
note("phone", "viewer B frames (default phone rate)", sB);
check("phone", "viewer B sees the same stream (same epoch, seq inside A's range)",
  sB.frames > 0 && sB.epochs.every((e) => sAjoin.epochs.includes(e)) && sB.seq_first >= sAjoin.seq_first, { b: [sB.seq_first, sB.seq_last], a: [sAjoin.seq_first, sAjoin.seq_last] });
await b.page.screenshot({ path: path.join(out, "phone-viewer-b.png") });
check("phone", "no console errors", b.errors.length === 0, b.errors.slice(0, 3).join(" | "));
await b.context.close();
check("api", "viewers back to 1 after B leaves", await waitFor((c) => c?.viewers === 1, 10_000), `viewers ${latest.c?.viewers}`);
await sleep(4_000);
const sAafter = stats(liveFrames(aFrames, bClose, Date.now()));
check("desktop", "viewer A uninterrupted after B leaves", sAafter.seq_jumps === 0 && sAafter.max_gap_ms < 250, sAafter);
check("desktop", "no console errors", a.errors.length === 0, a.errors.slice(0, 3).join(" | "));

// (4) A leaves while the session continues
const aClose = iso();
await a.context.close();
check("api", "viewers 0 after A leaves", await waitFor((c) => c?.viewers === 0, 10_000), `viewers ${latest.c?.viewers}`);
note("api", "viewer A left at", aClose);
const endSeen = await waitFor((c) => c?.status?.sim === "not_running" || c?.status?.connected === false, endTimeoutS * 1000);
await sleep(7_000); // one more 5 s status after the end
const after = timeline.filter((r) => r.at >= aClose);
const beforeEnd = after.filter((r) => r.sim === "in_session");
const endRow = after.find((r) => r.sim !== "in_session" && r.sim !== null);
check("api", "streaming stayed true while the session continued without viewers",
  endSeen && beforeEnd.length > 0 && beforeEnd.every((r) => r.streaming === true),
  { rows: beforeEnd.length, first: beforeEnd[0]?.at, last: beforeEnd[beforeEnd.length - 1]?.at, states: [...new Set(beforeEnd.map((r) => `${r.on_track ? "track" : r.in_garage ? "garage" : "stall"}`))] });
const finalRow = timeline[timeline.length - 1];
check("api", "streaming false once the sim exits", endRow !== undefined && finalRow.streaming === false && finalRow.driver_live === false,
  { end: endRow, final: finalRow });

// (5) garage capture in Setups
const setups = await api(`/api/v1/drivers/${driver.customer_id}/setups`);
const captured = setups.setups.filter((s) => (s.sources ?? []).some((x) => x.startsWith("garage")));
const named = captured.filter((s) => (s.names ?? []).includes(setupName));
check("api", "garage setups in the library", captured.length > 0, captured.map((s) => ({ id: s.setup_id, names: s.names, sto: s.sto_files, obs: s.observation_count })));
check("api", `a garage setup is named ${setupName} and linked to its .sto`, named.length > 0 && named.some((s) => (s.sto_files ?? []).length > 0), named.map((s) => s.setup_id));
for (const vp of Object.keys(VIEWPORTS)) {
  const v = await viewer(vp);
  await v.page.goto(`${base}/#/d/${driver.customer_id}/setups`);
  await v.page.waitForSelector(".setup-row", { timeout: 15_000 }).catch(() => {});
  const rows = await v.page.locator(".setup-row").allTextContents();
  const hit = rows.filter((t) => t.includes(setupName.replace(/\.sto$/, "")));
  check(vp, "Setups shows the garage capture", hit.length > 0, { rows: rows.length, api: setups.setups.length, hit: hit.slice(0, 2) });
  const m = await v.page.evaluate(() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]);
  check(vp, "Setups: no horizontal page scroll", m[0] <= m[1], `${m[0]} vs ${m[1]}`);
  check(vp, "Setups: no console errors", v.errors.length === 0, v.errors.slice(0, 3).join(" | "));
  await v.page.screenshot({ path: path.join(out, `${vp}-setups.png`), fullPage: true });
  await v.context.close();
}

polling = false;
await poller;
await browser.close();
await writeFile(path.join(out, "soak.json"), JSON.stringify({ base, failures, results, timeline }, null, 1));
console.log(failures === 0 ? "all soak checks passed" : `${failures} soak checks failed`);
process.exitCode = failures === 0 ? 0 : 1;

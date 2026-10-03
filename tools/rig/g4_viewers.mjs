// Gate G4 viewers: two separate Chromium browsers on the dev PC hold the Live page open at desktop size
// (1280x720, which the SPA streams at 60 Hz) and count the live frames the server relays to each, per
// second. Also polls GET /api/v1/status once a second (spool bytes, collector presence, viewers) and,
// with --watch-upload <sha256>, HEAD /api/v1/uploads/<sha256> (Upload-Offset).
//
//   node tools/rig/g4_viewers.mjs --base http://192.168.1.81:8210 --out <scratch>/g4 --seconds 1500
//        [--watch-upload <sha256>] [--stop-file <path>]
//
// Writes <out>/viewers.jsonl (one line per second), <out>/viewers-summary.json, screenshots at start and
// end. Read-only apart from opening two live sockets (each counts as a viewer).
import { createRequire } from "node:module";
import { appendFile, mkdir, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const { chromium } = createRequire(path.join(repo, "web", "package.json"))("@playwright/test");

const arg = (name, fallback = null) => {
  const i = process.argv.indexOf(`--${name}`);
  return i > 0 ? process.argv[i + 1] : fallback;
};
const base = arg("base", "http://192.168.1.81:8210");
const out = arg("out");
const seconds = Number(arg("seconds", "1500"));
const watch = arg("watch-upload");
const stopFile = arg("stop-file");
await mkdir(out, { recursive: true });
const log = path.join(out, "viewers.jsonl");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const drivers = (await (await fetch(`${base}/api/v1/drivers`)).json()).drivers;
const customer = drivers[0].customer_id;

// Frame header (live-frame.md): magic "LF" @0, flags @3, seq u32 @4, session_epoch u32 @8, captured_unix_ms u64 @12.
const header = (payload) => {
  const b = Buffer.isBuffer(payload) ? payload : Buffer.from(payload);
  if (b.length < 38 || b[0] !== 0x4c || b[1] !== 0x46) return null;
  return { flags: b[3], seq: b.readUInt32LE(4), epoch: b.readUInt32LE(8), captured: Number(b.readBigUInt64LE(12)) };
};

const viewers = [];
for (const name of ["A", "B"]) {
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: 1280, height: 720 } });
  const page = await context.newPage();
  const v = { name, browser, page, count: 0, backlog: 0, lastSeq: null, skipped: 0, sockets: 0, errors: [] };
  page.on("pageerror", (e) => v.errors.push(String(e)));
  page.on("websocket", (ws) => {
    if (!ws.url().includes("/ws/live/")) return;
    v.sockets += 1;
    v.url = ws.url();
    ws.on("framereceived", (f) => {
      if (typeof f.payload === "string") return;
      const h = header(f.payload);
      if (!h) return;
      if (h.flags & 0x40) { v.backlog += 1; return; }
      v.count += 1;
      if (v.lastSeq !== null && h.seq > v.lastSeq + 1) v.skipped += h.seq - v.lastSeq - 1;
      v.lastSeq = h.seq;
    });
  });
  await page.goto(`${base}/#/d/${customer}/live`);
  viewers.push(v);
}
await sleep(3000);
for (const v of viewers) await v.page.screenshot({ path: path.join(out, `viewer-${v.name}-start.png`) });

const t0 = Date.now();
const rows = [];
for (let i = 0; i < seconds; i++) {
  if (stopFile && existsSync(stopFile)) break;
  const tick = t0 + (i + 1) * 1000;
  await sleep(Math.max(0, tick - Date.now()));
  const row = { utc: new Date().toISOString() };
  for (const v of viewers) {
    row[v.name] = v.count; v.count = 0;
    row[`${v.name}_seq`] = v.lastSeq;
    row[`${v.name}_skipped`] = v.skipped;
    row[`${v.name}_sockets`] = v.sockets;
  }
  try {
    const s = await (await fetch(`${base}/api/v1/status`, { signal: AbortSignal.timeout(900) })).json();
    row.spool_bytes = s.spool.bytes; row.spool_files = s.spool.files;
    row.queue = s.queue;
    row.collectors = s.collectors.map((c) => ({ id: c.collector_id.slice(0, 8), online: c.online, viewers: c.viewers, version: c.collector_version }));
  } catch (e) { row.status_error = String(e).slice(0, 80); }
  if (watch) {
    try {
      const r = await fetch(`${base}/api/v1/uploads/${watch}`, { method: "HEAD", signal: AbortSignal.timeout(900) });
      row.watch = { http: r.status, offset: r.headers.get("upload-offset"), state: r.headers.get("upload-state") };
    } catch (e) { row.watch = { error: String(e).slice(0, 80) }; }
  }
  rows.push(row);
  await appendFile(log, JSON.stringify(row) + "\n");
}
for (const v of viewers) await v.page.screenshot({ path: path.join(out, `viewer-${v.name}-end.png`) });
const summary = { base, customer, seconds: rows.length, viewers: viewers.map((v) => ({ name: v.name, url: v.url, sockets: v.sockets, backlog_frames: v.backlog, skipped: v.skipped, errors: v.errors.slice(0, 5) })) };
await writeFile(path.join(out, "viewers-summary.json"), JSON.stringify(summary, null, 1));
for (const v of viewers) await v.browser.close();
console.log(JSON.stringify(summary));

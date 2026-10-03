// Fixture server for web development and the Playwright app project: node mock/server.mjs [--port 4173] [--dist dist]
// Serves the built SPA and the /api/v1 surface of docs/08-rewrite/contracts/api.openapi.yaml from fixture data,
// plus the live WebSocket (binary frames from the generated channel table) and SSE.
// Test control: POST /__mock/state {drivers, live, conflictNext, failWrites, delayMs, reset} and POST /__mock/event {...ServerEvent}.
import crypto from "node:crypto";
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { CHANNEL_INDEX } from "../src/gen/live-channels.ts";
import { encodeFrame } from "../src/live/encode.ts";
import {
  BASELINE_RESULT,
  DRIVER_ID,
  EXCLUDED_KEYS,
  RECOMMENDATION_RESULT,
  TRACE_SIGNALS,
  builtinLayouts,
  createState,
  drivers,
  traceBinary,
  tuningContext,
} from "./data.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const args = process.argv.slice(2);
const opt = (name, fallback) => {
  const i = args.indexOf(`--${name}`);
  return i >= 0 ? args[i + 1] : fallback;
};
const PORT = Number(opt("port", process.env.PORT ?? 4173));
const DIST = path.resolve(here, "..", opt("dist", "dist"));

let state = createState();
const sseClients = new Set();
const sockets = new Set();

const json = (res, status, body, headers = {}) => {
  const text = JSON.stringify(body);
  res.writeHead(status, { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(text), "Cache-Control": "no-cache", ...headers });
  res.end(text);
};
const err = (res, status, error, message) => json(res, status, { error, message });
const iso = () => new Date().toISOString();

const readBody = (req) =>
  new Promise((resolve) => {
    const chunks = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => {
      const text = Buffer.concat(chunks).toString("utf8");
      try {
        resolve(text ? JSON.parse(text) : undefined);
      } catch {
        resolve(undefined);
      }
    });
  });

function sendEvent(event) {
  const frame = `event: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`;
  for (const c of sseClients) c.write(frame);
}

// ---- jobs ---------------------------------------------------------------------

function startJob(kind, customerId, result, { fail = null, ms = 250 } = {}) {
  const id = state.nextJob++;
  const job = { job_id: id, kind, state: "queued", lane: "light", priority: 0, customer_id: customerId, attempts: 0, created_at: iso(), started_at: null, finished_at: null, error_code: null, error_message: null, result: null };
  state.jobs.set(id, job);
  setTimeout(() => {
    job.state = fail ? "failed" : "succeeded";
    job.finished_at = iso();
    job.result = fail ? null : result;
    if (fail) {
      job.error_code = fail.code;
      job.error_message = fail.message;
    }
    sendEvent({ type: "job_state", job: { job_id: id, kind, state: job.state }, customer_id: customerId, error_code: job.error_code });
  }, ms);
  return { job_id: id, kind, state: "queued" };
}

// ---- helpers ------------------------------------------------------------------

const findSession = (id) => Object.values(state.sessions).flat().find((s) => s.session_id === id);

function sessionDetail(s) {
  const analyses = s.analysis_id ? [{ analysis_id: s.analysis_id, created_at: s.start_time_utc, profile_version: "post-race-foundations-v23", lap_count: s.overview.recorded_laps ?? null, projection_version: 1, projection_state: "ready", current: true }] : [];
  return {
    session: s,
    recordings: Array.from({ length: s.recording_count }, (_, i) => ({ sha256: `${s.session_id}${i}`.padStart(64, "e"), size: 146_000_000, file_name: `rec-${s.session_id}-${i}.ibt`, start_time_utc: s.start_time_utc, ingest_state: "discovered", nas_state: "archived" })),
    analyses,
    setup_ids: [1],
    notes_count: state.notes.filter((n) => n.subject_kind === "session" && n.subject_id === String(s.session_id)).length,
    event_sessions: Object.values(state.sessions).flat().filter((x) => x.customer_id === s.customer_id && x.event_key === s.event_key && x.session_id !== s.session_id).map((x) => ({ session_id: x.session_id, session_type: x.session_type })),
  };
}

function envelope(analysisId) {
  return { analysis_id: analysisId, projection_version: 1, schema_version: 1, profile_version: "post-race-foundations-v23" };
}

/** Versioned user resources: enforces If-Match like the real server (an existing resource needs the current version). */
function versioned(req, res, current, apply) {
  if (state.config.conflictNext) {
    // Someone else saved first: the stored resource really moves on to the next version.
    state.config.conflictNext = false;
    if (current) current.version = (current.version ?? 1) + 1;
    return json(res, 412, { error: "version_conflict", current: current ?? { version: 2 } });
  }
  const header = req.headers["if-match"];
  if (current && header === undefined) return json(res, 412, { error: "version_conflict", current });
  if (current && String(header).replaceAll('"', "") !== String(current.version)) return json(res, 412, { error: "version_conflict", current });
  return apply();
}

// ---- routes -------------------------------------------------------------------------

async function route(req, res, url) {
  const p = url.pathname;
  const m = req.method;
  let r;

  if (p === "/health") return json(res, 200, { status: "ok", server_version: "0.19.0-mock", schema_version: 1 });
  if (p === "/api/v1/status")
    return json(res, 200, { server_version: "0.19.0-mock", api_version: 1, collector_protocol: 1, live_table_version: 1, projection_version: 1, nas: { state: "ok", pending_archive: 0 }, spool: { bytes: 0, files: 0, limit_bytes: 21474836480 }, queue: { analysis: 0, light: 0, deferred: 0 }, collectors: [], disk_free_bytes: 361e9 });

  if (p === "/api/v1/events") {
    res.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-cache", Connection: "keep-alive" });
    res.write(": open\n\n");
    sseClients.add(res);
    req.on("close", () => sseClients.delete(res));
    return;
  }

  if (p === "/api/v1/drivers") return json(res, 200, { drivers: drivers(state) });
  if ((r = p.match(/^\/api\/v1\/drivers\/(\d+)$/))) {
    const d = drivers(state).find((x) => x.customer_id === Number(r[1]));
    return d ? json(res, 200, d) : err(res, 404, "not_found", "No such driver");
  }

  if ((r = p.match(/^\/api\/v1\/drivers\/(\d+)\/sessions$/)) && m === "GET") {
    const c = Number(r[1]);
    const type = url.searchParams.get("type");
    const q = (url.searchParams.get("q") ?? "").toLowerCase();
    const limit = Number(url.searchParams.get("limit") ?? 50);
    const offset = Number(url.searchParams.get("cursor") ?? 0);
    let list = (state.sessions[c] ?? []).filter((s) => !type || type === "all" || (s.session_type ?? "").toLowerCase().startsWith(type));
    if (q) list = list.filter((s) => [s.track, s.track_config, s.car_name, s.series_name].join(" ").toLowerCase().includes(q));
    const page = list.slice(offset, offset + limit);
    return json(res, 200, { sessions: page, next_cursor: offset + limit < list.length ? String(offset + limit) : null });
  }
  if ((r = p.match(/^\/api\/v1\/drivers\/(\d+)\/cars-and-tracks$/)))
    return json(res, 200, { cars: [{ car_path: "nascar-xfinity", car_name: "NASCAR Xfinity Toyota Supra" }, { car_path: "mx5-cup", car_name: "Global Mazda MX-5 Cup" }], tracks: [{ track: "Bristol Motor Speedway", track_config: "Oval" }, { track: "Lime Rock Park", track_config: "Grand Prix" }, { track: "New Hampshire Motor Speedway", track_config: "Oval" }] });
  if ((r = p.match(/^\/api\/v1\/sessions\/(\d+)$/))) {
    const s = findSession(Number(r[1]));
    return s ? json(res, 200, sessionDetail(s)) : err(res, 404, "not_found", "No such session");
  }
  if ((r = p.match(/^\/api\/v1\/sessions\/(\d+)\/analysis$/)) && m === "POST") {
    const s = findSession(Number(r[1]));
    if (!s) return err(res, 404, "not_found", "No such session");
    s.analysis_state = "queued";
    s.analysis_reason = null;
    const ref = startJob("analyze", s.customer_id, {}, { ms: 400 });
    setTimeout(() => {
      s.analysis_state = "ready";
      s.analysis_id = `an-${s.session_id}`;
      sendEvent({ type: "analysis_ready", customer_id: s.customer_id, session_id: s.session_id, analysis_id: s.analysis_id, projection_version: 1 });
      sendEvent({ type: "library_changed", customer_id: s.customer_id, scope: "sessions" });
    }, 450);
    return json(res, 202, ref);
  }

  // views
  if ((r = p.match(/^\/api\/v1\/analyses\/([^/]+)\/(race|runs|investigation|track|tuning)$/)) && m === "GET") {
    const [, a, what] = r;
    if (what === "tuning") return json(res, 200, tuningContext(a, Object.values(state.sessions).flat()));
    if (what === "race") return json(res, 200, { ...envelope(a), identity: { track_name: "Bristol Motor Speedway", car_name: "NASCAR Xfinity Toyota Supra" }, race_summary: { recorded_laps: 12 }, laps: Array.from({ length: 12 }, (_, i) => ({ lap: i + 1, lap_time_s: 15.2 + i * 0.01, comparison_eligible: true, exclusion_reasons: [] })) });
    if (what === "runs") return json(res, 200, { ...envelope(a), runs: [] });
    if (what === "investigation") return json(res, 200, { ...envelope(a), race_investigation: { schema_version: 2, status: "available", comparisons: [], laps: [], runs: [], timeline: [] } });
    return json(res, 200, { ...envelope(a), coordinate_space: "normalized_local_vector", status: "usable", main_path: { pct: [0, 0.5], x: [0.1, 0.9], y: [0.5, 0.5] }, provenance: {} });
  }
  if ((r = p.match(/^\/api\/v1\/analyses\/([^/]+)\/traces\/catalog$/)))
    return json(res, 200, { ...envelope(r[1]), bins_per_lap: 160, laps: Array.from({ length: 12 }, (_, i) => ({ lap: i + 1, lap_time_s: 15.2, comparison_eligible: true, exclusion_reasons: [] })), signals: TRACE_SIGNALS, omitted_signals: [], steering_basis: { basis: "steering_wheel" }, budgets: { vertex_budget_per_signal: 48000, min_points_per_trace: 48, max_color_groups: 20, cursor_detail_traces: 24, max_trace_rows: 10 } });
  if ((r = p.match(/^\/api\/v1\/analyses\/([^/]+)\/traces$/))) {
    const body = traceBinary(r[1]);
    res.writeHead(200, { "Content-Type": "application/vnd.coach.columns", "Content-Length": body.length, "X-Coach-Trace-Laps": "12" });
    return res.end(body);
  }
  if ((r = p.match(/^\/api\/v1\/analyses\/([^/]+)\/tuning\/recommendations$/)) && m === "POST") {
    const body = await readBody(req);
    if (!body?.feedback?.length) return err(res, 422, "invalid_request", "feedback is required");
    state.writes.push({ path: p, body });
    return json(res, 202, startJob("tuning_recommend", DRIVER_ID, RECOMMENDATION_RESULT, { ms: 350 }));
  }

  // setups
  if ((r = p.match(/^\/api\/v1\/drivers\/(\d+)\/setups$/)) && m === "GET") {
    const c = Number(r[1]);
    const car = url.searchParams.get("car_path");
    const q = (url.searchParams.get("q") ?? "").toLowerCase();
    const raced = url.searchParams.get("raced_only") === "true";
    let list = (state.setups[c] ?? []).map((s) => s.summary).filter((s) => (!car || s.car_path === car) && (!raced || s.raced) && (!q || [...s.names, s.car_name].join(" ").toLowerCase().includes(q)));
    return json(res, 200, { setups: list, unlinked_files: state.unlinked[c] ?? [], next_cursor: null });
  }
  if ((r = p.match(/^\/api\/v1\/setups\/(\d+)$/))) {
    const s = Object.values(state.setups).flat().find((x) => x.summary.setup_id === Number(r[1]));
    return s ? json(res, 200, { setup: s.summary, values: s.values, identity_excluded_keys: EXCLUDED_KEYS, observations: s.observations.map((o) => ({ ...o, analysis_id: o.session_id != null ? (findSession(o.session_id)?.analysis_id ?? null) : null })) }) : err(res, 404, "not_found", "No such setup");
  }
  if (p === "/api/v1/setups/compare" && m === "POST") {
    const body = await readBody(req);
    const all = Object.values(state.setups).flat();
    const a = all.find((x) => x.summary.setup_id === body?.left_setup_id);
    const b = all.find((x) => x.summary.setup_id === body?.right_setup_id);
    if (!a || !b) return err(res, 422, "values_unknown", "Values unknown for one of these setups.");
    const keys = [...new Set([...Object.keys(a.values), ...Object.keys(b.values)])].sort();
    const rows = keys.map((key) => {
      const left = a.values[key] ?? null;
      const right = b.values[key] ?? null;
      const status = left === null || right === null ? "unknown" : left === right ? "identical" : "changed";
      return { key, left, right, status, readout: EXCLUDED_KEYS.includes(key) };
    });
    return json(res, 200, { rows, identical_count: rows.filter((x) => x.status === "identical").length, backend: { schema_version: 1 } });
  }
  if ((r = p.match(/^\/api\/v1\/drivers\/(\d+)\/baseline$/)) && m === "POST") {
    const body = await readBody(req);
    if (!body?.car || !body?.track || !body?.season) return err(res, 422, "invalid_request", "season, car and track are required");
    state.writes.push({ path: p, body });
    return json(res, 202, startJob("baseline", Number(r[1]), BASELINE_RESULT, { ms: 300 }));
  }
  if ((r = p.match(/^\/api\/v1\/drivers\/(\d+)\/tuning\/experiments\/([^/]+)\/outcome$/)) && m === "POST") {
    state.writes.push({ path: p, body: await readBody(req) });
    return json(res, 202, startJob("tuning_outcome", Number(r[1]), { ok: true }, { ms: 150 }));
  }
  if ((r = p.match(/^\/api\/v1\/jobs\/(\d+)$/))) {
    const j = state.jobs.get(Number(r[1]));
    return j ? json(res, 200, j) : err(res, 404, "not_found", "No such job");
  }

  // user data
  if ((r = p.match(/^\/api\/v1\/drivers\/(\d+)\/(tuning-drafts|turn-annotations)\/(.+)$/))) {
    const [, c, kind, rest] = r;
    const store = kind === "tuning-drafts" ? state.drafts : state.annotations;
    const key = `${c}/${decodeURIComponent(rest)}`;
    const cur = store.get(key);
    if (m === "GET") return cur ? json(res, 200, cur) : err(res, 404, "not_found", "No saved data");
    if (m === "PUT") {
      const body = await readBody(req);
      return versioned(req, res, cur, () => {
        const parts = decodeURIComponent(rest).split("/");
        const next = { ...(kind === "tuning-drafts" ? { identity_key: parts[0] } : { track_config_key: parts[0], map_identity: parts[1] }), body: body?.body ?? {}, version: (cur?.version ?? 0) + 1, updated_at: iso() };
        store.set(key, next);
        state.writes.push({ path: p, body: body?.body });
        return json(res, 200, next);
      });
    }
  }
  if ((r = p.match(/^\/api\/v1\/drivers\/(\d+)\/notes$/))) {
    const c = Number(r[1]);
    if (m === "GET") return json(res, 200, { notes: state.notes.filter((n) => n.customer_id === c && n.subject_kind === url.searchParams.get("subject_kind") && n.subject_id === url.searchParams.get("subject_id")) });
    if (m === "POST") {
      const body = await readBody(req);
      const note = { ...body, note_id: state.nextNote++, customer_id: c, version: 1, created_at: iso(), updated_at: iso() };
      state.notes.push(note);
      state.writes.push({ path: p, body });
      return json(res, 201, note);
    }
  }
  if ((r = p.match(/^\/api\/v1\/notes\/(\d+)$/))) {
    const note = state.notes.find((n) => n.note_id === Number(r[1]));
    if (!note) return err(res, 404, "not_found", "No such note");
    if (m === "PUT") {
      const body = await readBody(req);
      return versioned(req, res, note, () => {
        note.body = body.body;
        note.version += 1;
        note.updated_at = iso();
        return json(res, 200, note);
      });
    }
    if (m === "DELETE")
      return versioned(req, res, note, () => {
        state.notes = state.notes.filter((n) => n !== note);
        res.writeHead(204);
        res.end();
      });
  }
  if ((r = p.match(/^\/api\/v1\/drivers\/(\d+)\/trace-layouts$/))) {
    const c = Number(r[1]);
    const builtin = { layout_id: "default", name: "Default", version: 1, builtin: true, body: { rows: [{ row_id: "speed", signals: ["speed"] }, { row_id: "inputs", signals: ["throttle", "brake"] }] } };
    return json(res, 200, { layouts: [builtin, ...(state.traceLayouts[c] ?? [])] });
  }
  if ((r = p.match(/^\/api\/v1\/drivers\/(\d+)\/trace-layouts\/([a-z0-9-]+)$/)) && m === "PUT") {
    const c = Number(r[1]);
    const list = (state.traceLayouts[c] ??= []);
    const cur = list.find((l) => l.layout_id === r[2]);
    const body = await readBody(req);
    return versioned(req, res, cur, () => {
      const next = { layout_id: r[2], name: body.name, body: body.body, version: (cur?.version ?? 0) + 1, builtin: false };
      state.traceLayouts[c] = [...list.filter((l) => l !== cur), next];
      state.writes.push({ path: p, body });
      return json(res, 200, next);
    });
  }
  if ((r = p.match(/^\/api\/v1\/drivers\/(\d+)\/live-layouts$/))) {
    const c = Number(r[1]);
    return json(res, 200, { layouts: [...builtinLayouts(), ...(state.liveLayouts[c] ?? [])], active_layout_id: state.activeLayout[c] ?? "factory-default" });
  }
  if ((r = p.match(/^\/api\/v1\/drivers\/(\d+)\/live-layouts\/([a-z0-9-]+)$/))) {
    const c = Number(r[1]);
    const id = r[2];
    const list = (state.liveLayouts[c] ??= []);
    const cur = list.find((l) => l.layout_id === id);
    if (builtinLayouts().some((l) => l.layout_id === id)) return err(res, 403, "builtin_immutable", "Built-in layouts cannot change");
    if (m === "PUT") {
      const body = await readBody(req);
      return versioned(req, res, cur, () => {
        const next = { layout_id: id, name: body.name, body: body.body, version: (cur?.version ?? 0) + 1, builtin: false };
        state.liveLayouts[c] = [...list.filter((l) => l !== cur), next];
        state.writes.push({ path: p, body });
        sendEvent({ type: "library_changed", customer_id: c, scope: "layouts" });
        return json(res, 200, next);
      });
    }
    if (m === "DELETE" && cur)
      return versioned(req, res, cur, () => {
        state.liveLayouts[c] = list.filter((l) => l !== cur);
        res.writeHead(204);
        res.end();
      });
  }
  if ((r = p.match(/^\/api\/v1\/drivers\/(\d+)\/prefs$/))) {
    const c = Number(r[1]);
    const cur = state.prefs[c] ?? { customer_id: c, version: 1 };
    if (m === "GET") return json(res, 200, { ...cur, active_live_layout_id: state.activeLayout[c] });
    if (m === "PUT") {
      const body = await readBody(req);
      return versioned(req, res, cur, () => {
        const next = { ...cur, ...body, customer_id: c, version: cur.version + 1 };
        state.prefs[c] = next;
        if (body.active_live_layout_id) state.activeLayout[c] = body.active_live_layout_id;
        state.writes.push({ path: p, body });
        return json(res, 200, next);
      });
    }
  }

  return err(res, 404, "not_found", `No route for ${m} ${p}`);
}

// ---- static ------------------------------------------------------------------------------

const TYPES = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".json": "application/json", ".map": "application/json", ".png": "image/png", ".ico": "image/x-icon" };

function serveStatic(req, res, url) {
  let file = path.join(DIST, decodeURIComponent(url.pathname));
  if (!file.startsWith(DIST)) return err(res, 403, "forbidden", "Outside the site");
  if (!fs.existsSync(file) || fs.statSync(file).isDirectory()) file = path.join(DIST, "index.html");
  if (!fs.existsSync(file)) return err(res, 404, "not_found", `Build the site first (${DIST})`);
  const ext = path.extname(file);
  res.writeHead(200, { "Content-Type": TYPES[ext] ?? "application/octet-stream", "Cache-Control": ext === ".html" ? "no-cache" : "max-age=3600" });
  fs.createReadStream(file).pipe(res);
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url ?? "/", "http://localhost");
  try {
    if (url.pathname === "/__mock/state" && req.method === "POST") {
      const body = (await readBody(req)) ?? {};
      if (body.reset) {
        for (const s of sockets) s.destroy();
        state = createState();
      }
      const { reset, ...cfg } = body;
      Object.assign(state.config, cfg);
      if ("live" in cfg) sendEvent({ type: "library_changed", customer_id: DRIVER_ID, scope: "sessions" });
      return json(res, 200, { config: state.config });
    }
    if (url.pathname === "/__mock/state" && req.method === "GET") return json(res, 200, { config: state.config, writes: state.writes, notes: state.notes });
    if (url.pathname === "/__mock/event" && req.method === "POST") {
      sendEvent((await readBody(req)) ?? {});
      return json(res, 200, { ok: true });
    }
    if (url.pathname.startsWith("/api/") || url.pathname === "/health") {
      if (state.config.delayMs) await new Promise((r) => setTimeout(r, state.config.delayMs));
      if (state.config.failWrites && req.method !== "GET") return err(res, 503, "unavailable", "Mock outage");
      return await route(req, res, url);
    }
    return serveStatic(req, res, url);
  } catch (e) {
    console.error(e);
    if (!res.headersSent) err(res, 500, "internal", String(e?.message ?? e));
  }
});

// ---- live WebSocket (RFC 6455, server side only) ----------------------------------------------

const GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11";

function wsFrame(opcode, payload) {
  const len = payload.length;
  const head = len < 126 ? Buffer.from([0x80 | opcode, len]) : len < 65536 ? Buffer.from([0x80 | opcode, 126, len >> 8, len & 255]) : null;
  return Buffer.concat([head, payload]);
}

function readClientFrames(buf, onText, onClose) {
  let at = 0;
  while (at + 2 <= buf.length) {
    const opcode = buf[at] & 0x0f;
    let len = buf[at + 1] & 0x7f;
    let hdr = 2;
    if (len === 126) {
      len = buf.readUInt16BE(at + 2);
      hdr = 4;
    }
    const mask = buf.subarray(at + hdr, at + hdr + 4);
    const start = at + hdr + 4;
    if (start + len > buf.length) break;
    const data = Buffer.from(buf.subarray(start, start + len));
    for (let i = 0; i < data.length; i++) data[i] ^= mask[i % 4];
    if (opcode === 1) onText(data.toString("utf8"));
    if (opcode === 8) onClose();
    at = start + len;
  }
}

const IDX = CHANNEL_INDEX;
function liveValues(t, lapBase) {
  const lapTime = 15.2;
  const tt = t + 600;
  const lap = Math.floor(tt / lapTime) + lapBase;
  const frac = (tt % lapTime) / lapTime;
  const v = Array(45).fill(null);
  const speed = 62 + 18 * Math.sin(frac * Math.PI * 4);
  v[IDX.speed] = speed;
  v[IDX.throttle] = Math.max(0, Math.min(1, 0.55 + 0.45 * Math.sin(frac * Math.PI * 4 + 1)));
  v[IDX.brake] = Math.max(0, 0.7 * Math.sin(frac * Math.PI * 4 + 3.6));
  v[IDX.steering_wheel_angle] = 1.2 * Math.sin(frac * Math.PI * 4 + 2);
  v[IDX.steering_road_wheel] = v[IDX.steering_wheel_angle] / 12;
  v[IDX.steering_ratio] = 12;
  v[IDX.gear] = 3 + Math.round(1.5 * Math.sin(frac * Math.PI * 4));
  v[IDX.rpm] = 6000 + 1500 * Math.sin(frac * Math.PI * 4);
  v[IDX.yaw_rate] = 0.4 * Math.sin(frac * Math.PI * 4 + 2);
  v[IDX.lateral_accel] = 14 * Math.sin(frac * Math.PI * 4 + 2);
  v[IDX.longitudinal_accel] = 6 * Math.cos(frac * Math.PI * 4 + 1);
  v[IDX.lap_dist_pct] = frac;
  v[IDX.lap] = lap;
  v[IDX.laps_remaining] = Math.max(0, 80 - lap);
  v[IDX.position] = 8;
  v[IDX.class_position] = 8;
  v[IDX.flag_state] = 2;
  v[IDX.session_flags] = 4;
  v[IDX.abs_active] = v[IDX.brake] > 0.5 ? 1 : 0;
  v[IDX.brake_bias] = 54.5;
  v[IDX.fuel] = 68 - t * 0.02;
  v[IDX.fuel_pct] = (68 - t * 0.02) / 75;
  v[IDX.fuel_laps_remaining] = 31.4;
  v[IDX.fuel_hard_limit_laps] = 30;
  v[IDX.last_lap] = 15.234;
  v[IDX.leader_last_lap] = 15.1;
  v[IDX.ahead_gap] = 1.23;
  v[IDX.ahead_gap_trend] = 3;
  v[IDX.behind_gap] = 0.84;
  v[IDX.behind_gap_trend] = 3;
  v[IDX.leader_gap] = 12.34;
  v[IDX.pace_band_min] = 15.1;
  v[IDX.pace_band_max] = 15.5;
  v[IDX.run_phase] = 2;
  v[IDX.green_run_laps] = 12;
  v[IDX.optional_repair] = 0;
  v[IDX.on_pit_road] = 0;
  v[IDX.tow_active] = 0;
  v[IDX.track_temperature] = 38;
  v[IDX.air_temperature] = 27;
  v[IDX.incidents] = 2;
  v[IDX.pace_delta_to_leader] = 0.134;
  return v;
}

server.on("upgrade", (req, socket) => {
  const url = new URL(req.url ?? "/", "http://localhost");
  const m = url.pathname.match(/^\/ws\/live\/(\d+)$/);
  const key = req.headers["sec-websocket-key"];
  if (!m || !key) return socket.destroy();
  const accept = crypto.createHash("sha1").update(key + GUID).digest("base64");
  socket.write(`HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: ${accept}\r\n\r\n`);
  sockets.add(socket);
  let rate = Number(url.searchParams.get("rate") ?? 30);
  let seq = 0;
  let t = 0;
  const live = state.config.live;
  const backlogFrames = live ? 120 : 0;
  socket.write(wsFrame(1, Buffer.from(JSON.stringify({ type: "live.hello", table_version: 1, live, backlog_frames: backlogFrames, rate }))));
  const frame = (time, flags, cue) =>
    wsFrame(2, Buffer.from(encodeFrame({ flags, seq: seq++, sessionEpoch: 1, capturedUnixMs: Date.now(), sessionTime: time, tick: Math.round(time * 60), tickRate: 60, values: liveValues(time, 1), cue })));
  if (live) {
    for (let i = 0; i < backlogFrames; i++) socket.write(frame(-12 + i / 10, 1 | 64, null));
  }
  let timer = null;
  const arm = () => {
    clearInterval(timer);
    if (!live) return;
    timer = setInterval(() => {
      t += 1 / rate;
      socket.write(frame(t, 1 | 2, { id: 13, priority: 0, evidence: 1, args: [] }));
    }, Math.round(1000 / rate));
  };
  arm();
  socket.on("data", (buf) =>
    readClientFrames(
      buf,
      (text) => {
        try {
          const msg = JSON.parse(text);
          if (msg.type === "rate" && [1, 10, 30, 60].includes(msg.rate)) {
            rate = msg.rate;
            arm();
          }
        } catch {
          /* ignore */
        }
      },
      () => {
        socket.write(wsFrame(8, Buffer.alloc(0)));
        socket.end();
      },
    ),
  );
  const done = () => {
    clearInterval(timer);
    sockets.delete(socket);
  };
  socket.on("close", done);
  socket.on("error", done);
});

server.listen(PORT, "127.0.0.1", () => console.log(`mock server on http://127.0.0.1:${PORT} (site: ${DIST})`));

import type { Schemas } from "../core/api";
import { humanize, sessionTypeLabel } from "../core/format";
import { CAR_COLORS, carIndex } from "../core/identity";

/**
 * Sessions library rules (backlog #1f-#1k, #3; audit S1-S5): events, phases in running order, filters, figures and their colour
 * meaning. Pure functions; the components only draw what these return.
 */
export type Session = Schemas["SessionSummary"];

/** The em dash the Sessions design uses for a missing figure. */
export const NONE = "—";

/* ---- phases ---------------------------------------------------------------------- */

export type PhaseCode = "P" | "Q" | "R" | "W" | "T";
export interface Phase {
  code: PhaseCode;
  label: string;
  /** Running order inside an event: practice, qualifying, warmup, race. */
  rank: number;
}

export function phaseOf(sessionType: string | null | undefined): Phase {
  const label = sessionTypeLabel(sessionType);
  switch (label) {
    case "Practice":
      return { code: "P", label, rank: 0 };
    case "Qualifying":
      return { code: "Q", label, rank: 1 };
    case "Race":
      return { code: "R", label, rank: 3 };
    case "Test":
      return { code: "T", label, rank: 0 };
    case "Warmup":
      return { code: "W", label, rank: 2 };
    default:
      return { code: "T", label, rank: 4 };
  }
}

/** The session types the Session filter offers. Tests and warmups are never a filter option. */
export const TYPE_OPTIONS: readonly Phase[] = [phaseOf("Practice"), phaseOf("Qualify"), phaseOf("Race")];

/* ---- setup type ------------------------------------------------------------------ */

export type SetupType = "Fixed" | "Open";

/**
 * Fixed or open setup from the recording's `is_fixed_setup` (true fixed, false open). Null or absent means the recording does not
 * say: no badge and no filter match, never read as open. A `setup_type` string is read only when `is_fixed_setup` is absent.
 */
export function setupTypeOf(s: Session): SetupType | null {
  if (s.is_fixed_setup === true) return "Fixed";
  if (s.is_fixed_setup === false) return "Open";
  if (s.is_fixed_setup === null) return null;
  const raw = (s as Record<string, unknown>).setup_type;
  if (typeof raw !== "string") return null;
  const v = raw.trim().toLowerCase();
  return v === "fixed" ? "Fixed" : v === "open" ? "Open" : null;
}

/* ---- identity -------------------------------------------------------------------- */

export function carKey(s: Session): string {
  return s.car_path ?? s.car_name ?? "";
}

export function carName(s: Session): string {
  return s.car_name ?? s.car_path ?? "Car unknown";
}

/**
 * Colour index (0 to 2) per car. The device keeps one first-seen order for the whole app (`core/identity.ts`), so a car has the same
 * colour here, on Setups and on the rail. A first visit sees the library oldest first, so older cars take the first colours.
 */
export function carColorIndex(sessions: readonly Session[]): Map<string, number> {
  const oldestFirst = sessions
    .slice()
    .sort((x, y) => (x.start_time_utc ?? "").localeCompare(y.start_time_utc ?? "") || x.session_id - y.session_id);
  const out = new Map<string, number>();
  for (const s of oldestFirst) {
    const key = carKey(s);
    if (!out.has(key)) out.set(key, carIndex(key) % CAR_COLORS);
  }
  return out;
}

const norm = (v: string) => v.toLowerCase().replace(/\bseries\b/g, "").replace(/[^a-z0-9]+/g, "");

/** The series name, unless it only repeats the car name. */
export function seriesLabel(s: Session): string | null {
  const series = s.series_name?.trim();
  if (!series) return null;
  const car = s.car_name ?? s.car_path ?? "";
  return norm(series) === norm(car) ? null : series;
}

/* ---- events ---------------------------------------------------------------------- */

export interface EventGroup {
  key: string;
  /** The race when the event has one, else its last phase: the session that names the event's car, track and series. */
  head: Session;
  /** Phases in running order (practice, qualifying, race). */
  sessions: Session[];
  /** Earliest session start, UTC ISO. */
  start: string | null;
  setupType: SetupType | null;
  /** Fastest best lap among the event's phases. */
  bestLapS: number | null;
}

export function bestLap(s: Session): number | null {
  const v = s.overview.best_clean_lap_s;
  return typeof v === "number" && Number.isFinite(v) && v > 0 ? v : null;
}

export function comparePhases(a: Session, b: Session): number {
  return phaseOf(a.session_type).rank - phaseOf(b.session_type).rank || (a.sim_session_num ?? 0) - (b.sim_session_num ?? 0) || (a.start_time_utc ?? "").localeCompare(b.start_time_utc ?? "") || a.session_id - b.session_id;
}

/**
 * Events in the order the server sent them (newest first); phases inside an event in running order. With a selection, each event
 * keeps the phases that match it and events with none are dropped; the event best, head and setup type still come from every phase
 * of the event, so filtering never turns a slower lap into the event best (review finding 13).
 */
export function groupEvents(sessions: readonly Session[], sel?: Selection): EventGroup[] {
  const order: string[] = [];
  const byKey = new Map<string, Session[]>();
  for (const s of sessions) {
    let list = byKey.get(s.event_key);
    if (!list) {
      byKey.set(s.event_key, (list = []));
      order.push(s.event_key);
    }
    list.push(s);
  }
  return order.flatMap((key) => {
    const list = byKey.get(key)!.slice().sort(comparePhases);
    const shown = sel ? filterSessions(list, sel) : list;
    if (shown.length === 0) return [];
    const head = list.find((s) => phaseOf(s.session_type).code === "R") ?? list[list.length - 1]!;
    const starts = list.map((s) => s.start_time_utc).filter((v): v is string => !!v).sort();
    const bests = list.map(bestLap).filter((v): v is number => v !== null);
    const setupType = setupTypeOf(head) ?? list.map(setupTypeOf).find((v) => v !== null) ?? null;
    return [{ key, head, sessions: shown, start: starts[0] ?? null, setupType, bestLapS: bests.length ? Math.min(...bests) : null }];
  });
}

/* ---- filters --------------------------------------------------------------------- */

export type FilterId = "car" | "track" | "series" | "type" | "setup";
export const FILTER_IDS: readonly FilterId[] = ["car", "track", "series", "type", "setup"];
export const FILTER_LABEL: Record<FilterId, string> = { car: "Car", track: "Track", series: "Series", type: "Session", setup: "Setup" };

export type Selection = Record<FilterId, string[]>;

export function emptySelection(): Selection {
  return { car: [], track: [], series: [], type: [], setup: [] };
}

export function isEmptySelection(sel: Selection): boolean {
  return FILTER_IDS.every((id) => sel[id].length === 0);
}

export function selectionCount(sel: Selection): number {
  return FILTER_IDS.reduce((n, id) => n + sel[id].length, 0);
}

/** A stored selection (driver prefs, device hint); anything malformed is dropped, unknown filters are ignored. */
export function parseSelection(raw: unknown): Selection | null {
  if (!raw || typeof raw !== "object") return null;
  const out = emptySelection();
  let any = false;
  for (const id of FILTER_IDS) {
    const v = (raw as Record<string, unknown>)[id];
    if (Array.isArray(v)) {
      out[id] = [...new Set(v.filter((x): x is string => typeof x === "string" && x !== ""))];
      any = true;
    }
  }
  return any ? out : null;
}

type StoredFilters = Schemas["SessionFilters"];
const TYPE_QUERY: Record<"P" | "Q" | "R", "practice" | "qualify" | "race"> = { P: "practice", Q: "qualify", R: "race" };

/**
 * The selection as the driver's prefs store it (`session_filters`): the query values of `GET /sessions` (car_path, track name,
 * series name, `race`/`qualify`/`practice`, `fixed`/`open`). Both setup types chosen is no setup filter.
 */
export function toSessionFilters(sel: Selection): StoredFilters {
  const setup = sel.setup.length === 1 ? (sel.setup[0] === "Fixed" ? "fixed" : "open") : null;
  const types = sel.type.filter((t): t is "P" | "Q" | "R" => t in TYPE_QUERY).map((t) => TYPE_QUERY[t]);
  return { cars: sel.car, tracks: sel.track, series: sel.series, types, setup };
}

/** A stored `session_filters` back to the page's selection; anything malformed is dropped. */
export function fromSessionFilters(raw: unknown): Selection | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  const list = (v: unknown) => (Array.isArray(v) ? [...new Set(v.filter((x): x is string => typeof x === "string" && x !== ""))] : []);
  const code: Record<string, string> = { practice: "P", qualify: "Q", race: "R" };
  const setup = r.setup === "fixed" ? ["Fixed"] : r.setup === "open" ? ["Open"] : [];
  return { car: list(r.cars), track: list(r.tracks), series: list(r.series), type: list(r.types).map((t) => code[t]).filter((t): t is string => !!t), setup };
}

/** The value a session has for one filter; null = the session has none (it matches no option of that filter). */
export function facetKey(id: FilterId, s: Session): string | null {
  switch (id) {
    case "car":
      return carKey(s) || null;
    case "track":
      return s.track?.trim() || null;
    case "series":
      return s.series_name?.trim() || null;
    case "type": {
      const code = phaseOf(s.session_type).code;
      return code === "P" || code === "Q" || code === "R" ? code : null;
    }
    case "setup":
      return setupTypeOf(s);
  }
}

export interface FacetOption {
  key: string;
  label: string;
  count: number;
  /** Car colour index (Car filter) or the phase (Session filter). */
  car?: number;
  phase?: PhaseCode;
}

export function facetOptions(id: FilterId, sessions: readonly Session[], colors: ReadonlyMap<string, number>): FacetOption[] {
  const map = new Map<string, FacetOption>();
  for (const s of sessions) {
    const key = facetKey(id, s);
    if (key === null) continue;
    let o = map.get(key);
    if (!o) {
      o = { key, label: key, count: 0 };
      if (id === "car") {
        o.label = carName(s);
        o.car = colors.get(key) ?? 0;
      }
      if (id === "type") {
        const p = phaseOf(s.session_type);
        o.label = p.label;
        o.phase = p.code;
      }
      map.set(key, o);
    }
    o.count += 1;
  }
  const list = [...map.values()];
  if (id === "type") return list.sort((a, b) => TYPE_OPTIONS.findIndex((p) => p.code === a.key) - TYPE_OPTIONS.findIndex((p) => p.code === b.key));
  if (id === "setup") return list.sort((a, b) => (a.key === "Fixed" ? -1 : 1) - (b.key === "Fixed" ? -1 : 1));
  return list.sort((a, b) => a.label.localeCompare(b.label, "en", { sensitivity: "base" }));
}

/**
 * Filters that narrow the list. Both setup types chosen is no setup filter, the rule `session_filters` stores (`setup: null`), so
 * the same sessions show before and after a reload (review finding 12).
 */
export function activeFilters(sel: Selection): FilterId[] {
  return FILTER_IDS.filter((id) => sel[id].length > 0 && !(id === "setup" && sel.setup.includes("Fixed") && sel.setup.includes("Open")));
}

/** Within one filter any chosen option matches; across filters all must. */
export function filterSessions(sessions: readonly Session[], sel: Selection): Session[] {
  const active = activeFilters(sel);
  if (active.length === 0) return sessions.slice();
  return sessions.filter((s) =>
    active.every((id) => {
      const key = facetKey(id, s);
      return key !== null && sel[id].includes(key);
    }),
  );
}

export function toggleOption(sel: Selection, id: FilterId, key: string): Selection {
  const has = sel[id].includes(key);
  return { ...sel, [id]: has ? sel[id].filter((k) => k !== key) : [...sel[id], key] };
}

/* ---- figures --------------------------------------------------------------------- */

/** Pace trend (s per lap over the green laps): above this it is a fall-off, below the negative one an improvement. */
export const PACE_LOSS = 0.02;
export const PACE_GAIN = -0.005;
export type PaceTone = "loss" | "gain" | "flat";

export function paceTone(slope: number | null | undefined): PaceTone | null {
  if (slope == null || !Number.isFinite(slope)) return null;
  return slope > PACE_LOSS ? "loss" : slope < PACE_GAIN ? "gain" : "flat";
}

/** Signed seconds per lap to 3 decimals with a true minus; a trend that rounds to nothing is `0.000`. */
export function formatPace(slope: number | null | undefined): string {
  if (slope == null || !Number.isFinite(slope)) return NONE;
  const r = Math.round(slope * 1000) / 1000;
  if (r === 0) return "0.000";
  const body = Math.abs(r).toFixed(3);
  return r > 0 ? `+${body}` : `−${body}`;
}

export function formatPosition(p: number | null | undefined): string {
  return p == null ? NONE : `P${p}`;
}

/** `P12 → P4`, or `P4` when only one end is known. */
export function resultText(s: Session): string {
  const o = s.overview;
  const from = o.starting_position;
  const to = o.final_position;
  if (from != null && to != null) return `P${from} → P${to}`;
  return to != null ? `P${to}` : from != null ? `P${from} → ${NONE}` : NONE;
}

export interface PositionChange {
  from: number;
  to: number;
  /** Positive = places gained. */
  gained: number;
}

/** Race only: qualifying and practice show their classification and no change. */
export function positionChange(s: Session): PositionChange | null {
  if (phaseOf(s.session_type).code !== "R") return null;
  const { starting_position: from, final_position: to } = s.overview;
  if (from == null || to == null) return null;
  return { from, to, gained: from - to };
}

export function isWin(s: Session): boolean {
  return phaseOf(s.session_type).code === "R" && s.overview.final_position === 1;
}

/** Race result edge: win, gain or loss; nothing when the position did not change or is unknown. */
export type ResultBand = "win" | "gain" | "loss" | null;

export function resultBand(s: Session): ResultBand {
  if (isWin(s)) return "win";
  const c = positionChange(s);
  return c === null || c.gained === 0 ? null : c.gained > 0 ? "gain" : "loss";
}

const FIGURE_FIELDS = ["green_laps_estimated", "caution_laps_estimated", "best_clean_lap_s", "pace_slope_s_per_lap", "pit_stops_detected", "longest_green_run", "fuel_used_gal", "final_position"] as const;

/** Whether the library row has measured figures to show (a session that was never analyzed shows its lap count only). */
export function hasFigures(s: Session): boolean {
  return FIGURE_FIELDS.some((f) => s.overview[f] != null);
}

/* ---- analysis state: exceptions only --------------------------------------------- */

export type ExceptionKind = "analyzing" | "queued" | "deferred" | "failed" | "none";
export interface AnalysisException {
  kind: ExceptionKind;
  text: string;
  action: "Analyze" | "Retry" | null;
}

/** A ready session shows nothing; every other state is named. `Failed · worker crash`, `Deferred · exceeds memory budget`. */
export function analysisException(s: Session): AnalysisException | null {
  const reason = s.analysis_reason ? humanize(s.analysis_reason).toLowerCase() : null;
  switch (s.analysis_state) {
    case "ready":
      return null;
    case "running":
      return { kind: "analyzing", text: "Analyzing", action: null };
    case "queued":
      return { kind: "queued", text: "Queued", action: null };
    case "failed":
      return { kind: "failed", text: reason ? `Failed · ${reason}` : "Failed", action: "Retry" };
    case "deferred":
      return { kind: "deferred", text: reason ? `Deferred · ${reason}` : "Deferred", action: null };
    default:
      return { kind: "none", text: "Not analyzed", action: "Analyze" };
  }
}

export function isOpenable(s: Session): boolean {
  return s.analysis_state === "ready";
}

/* ---- phases of one event, for the investigation header ---------------------------------- */

/** One phase of an event as the header's P · Q · R selector takes it: its type and the analysis state that decides whether it can open. */
export interface PhaseOption {
  sessionId: number;
  sessionType: string | null;
  /** The library's analysis state: `ready` opens, every other state is named on hover (audit S2). */
  state: Session["analysis_state"];
  reason: string | null;
}

/** The phases of the event that holds `sessionId`, in running order, with their real analysis state. */
export function eventPhaseOptions(sessionId: number, sessions: readonly Session[]): PhaseOption[] {
  const me = sessions.find((s) => s.session_id === sessionId);
  if (!me) return [];
  return sessions
    .filter((s) => s.event_key === me.event_key)
    .sort(comparePhases)
    .map((s) => ({ sessionId: s.session_id, sessionType: s.session_type ?? null, state: s.analysis_state, reason: s.analysis_reason ?? null }));
}

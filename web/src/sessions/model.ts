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
  /** Phases as the card lists them: race, qualifying, practice (`compareLines`, backlog #86). */
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

/**
 * A race's fastest green lap, traffic included (`SessionOverview.best_green_lap_s`, backlog #85): the clean best leaves out close
 * traffic, which is nearly every race lap, so the race line reads this one. Null reads as absent.
 */
export function raceBestLap(s: Session): number | null {
  const v: unknown = s.overview.best_green_lap_s;
  return typeof v === "number" && Number.isFinite(v) && v > 0 ? v : null;
}

/** The best lap a phase line shows: the race its green best (#85), practice and qualifying their clean best. */
export function phaseBestLap(s: Session): number | null {
  return phaseOf(s.session_type).code === "R" ? raceBestLap(s) : bestLap(s);
}

export function comparePhases(a: Session, b: Session): number {
  return phaseOf(a.session_type).rank - phaseOf(b.session_type).rank || (a.sim_session_num ?? 0) - (b.sim_session_num ?? 0) || (a.start_time_utc ?? "").localeCompare(b.start_time_utc ?? "") || a.session_id - b.session_id;
}

/** Order of the lines in an event card (backlog #86): race, warmup, qualifying, practice; then running order within a type. */
const LINE_RANK: Record<PhaseCode, number> = { R: 0, W: 1, Q: 2, P: 3, T: 3 };

export function compareLines(a: Session, b: Session): number {
  return LINE_RANK[phaseOf(a.session_type).code] - LINE_RANK[phaseOf(b.session_type).code] || comparePhases(a, b);
}

/**
 * Events in the order the server sent them (newest first); phases inside an event race first, then qualifying, then practice (#86). With a selection, each event
 * keeps the phases that match it and events with none are dropped; the event best, head and setup type still come from every phase
 * of the event, so filtering never turns a slower lap into the event best (review finding 13).
 */
export function groupEvents(sessions: readonly Session[], sel?: Selection, phases?: PhaseToggles): EventGroup[] {
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
    const filtered = sel ? filterSessions(list, sel) : list;
    const shown = (phases ? filtered.filter((s) => phaseShown(phases, s)) : filtered).sort(compareLines);
    if (shown.length === 0) return [];
    const head = list.find((s) => phaseOf(s.session_type).code === "R") ?? list[list.length - 1]!;
    const starts = list.map((s) => s.start_time_utc).filter((v): v is string => !!v).sort();
    const bests = list.map(phaseBestLap).filter((v): v is number => v !== null);
    const setupType = setupTypeOf(head) ?? list.map(setupTypeOf).find((v) => v !== null) ?? null;
    return [{ key, head, sessions: shown, start: starts[0] ?? null, setupType, bestLapS: bests.length ? Math.min(...bests) : null }];
  });
}

/* ---- phase toggles (backlog #88) --------------------------------------------------- */

/** The three toggle chips in the heading, in the card's line order. A warmup or test line follows the practice chip. */
export const TOGGLE_CODES = ["R", "Q", "P"] as const;
export type ToggleCode = (typeof TOGGLE_CODES)[number];
export type PhaseToggles = Record<ToggleCode, boolean>;

export function allPhases(): PhaseToggles {
  return { R: true, Q: true, P: true };
}

/** The chip a session's line answers to. */
export function toggleOf(s: Session): ToggleCode {
  const code = phaseOf(s.session_type).code;
  return code === "R" ? "R" : code === "Q" ? "Q" : "P";
}

export function phaseShown(t: PhaseToggles, s: Session): boolean {
  return t[toggleOf(s)];
}

/** Flip one chip; the last chip on stays on, so the list never goes empty by toggling. */
export function togglePhase(t: PhaseToggles, code: ToggleCode): PhaseToggles {
  const next = { ...t, [code]: !t[code] };
  return TOGGLE_CODES.some((c) => next[c]) ? next : t;
}

/** A stored value read back: anything unreadable, or with every chip off, is all on. */
export function parsePhaseToggles(raw: unknown): PhaseToggles {
  if (!raw || typeof raw !== "object") return allPhases();
  const r = raw as Record<string, unknown>;
  const out = allPhases();
  for (const c of TOGGLE_CODES) if (typeof r[c] === "boolean") out[c] = r[c] as boolean;
  return TOGGLE_CODES.some((c) => out[c]) ? out : allPhases();
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

/**
 * The official starting position a race finish is measured against (`SessionOverview.start_position`: the grid, else the
 * backend's position at the green flag). Never the first recorded position (backlog #56). Null outside a race.
 */
export function startPosition(s: Session): number | null {
  if (phaseOf(s.session_type).code !== "R") return null;
  const v: unknown = s.overview.start_position;
  return finite(v) && v > 0 ? v : null;
}

/** A race left before the flag (backlog #97): the lap he was on and where he was running when the recording ended. */
export interface Retirement {
  lap: number | null;
  position: number | null;
}

/**
 * The race's recording stopped while the race was still running (`SessionOverview.finished` false, the backend's
 * `race_summary.finish`): its last recorded place is a running place, not a finish. Null for a finished or unknown race, outside a
 * race, and when a later recording of the event lists the official results (`results_position`), which stand as before.
 */
export function retirement(s: Session): Retirement | null {
  if (phaseOf(s.session_type).code !== "R") return null;
  const o = s.overview;
  if (o.finished !== false) return null;
  if (finite(o.results_position) && o.results_position > 0) return null;
  const lap: unknown = o.retired_lap;
  const position: unknown = o.retired_position;
  return { lap: finite(lap) && lap >= 0 ? lap : null, position: finite(position) && position > 0 ? position : null };
}

/** `DNF · L14`, or `DNF` when the lap is unknown. */
export function retirementText(r: Retirement): string {
  return r.lap === null ? "DNF" : `DNF · L${r.lap}`;
}

/** `P12 → P4`, or `P4` when only one end is known; `P2 → DNF · L35` for a race left before the flag. */
export function resultText(s: Session): string {
  const from = startPosition(s);
  const retired = retirement(s);
  if (retired) return from != null ? `P${from} → ${retirementText(retired)}` : retirementText(retired);
  const to = s.overview.final_position;
  if (from != null && to != null) return `P${from} → P${to}`;
  return to != null ? `P${to}` : from != null ? `P${from} → ${NONE}` : NONE;
}

export interface PositionChange {
  from: number;
  to: number;
  /** Positive = places gained. */
  gained: number;
}

/** Race only: qualifying and practice show their classification and no change; a race left before the flag has none (#97). */
export function positionChange(s: Session): PositionChange | null {
  if (retirement(s)) return null;
  const from = startPosition(s);
  const to = s.overview.final_position;
  if (from == null || to == null) return null;
  return { from, to, gained: from - to };
}

/**
 * Practice and qualifying place: the official one with the field size when a later recording of the event lists the results
 * (`results_position`, `field_size`, sent together), else the last recorded position alone. Never a recorded place over an
 * official field.
 */
export function classification(s: Session): { position: number; field: number | null } | null {
  const o = s.overview;
  const official: unknown = o.results_position;
  const field: unknown = o.field_size;
  if (finite(official) && official > 0) return { position: official, field: finite(field) && field > 0 ? field : null };
  const recorded = o.final_position;
  return recorded == null ? null : { position: recorded, field: null };
}

/** Burn per green lap, gallons (`fuel_per_lap_gal`, the backend's strategy figure); null when it measured none. */
export function burnPerLap(s: Session): number | null {
  const v: unknown = s.overview.fuel_per_lap_gal;
  return finite(v) && v > 0 ? v : null;
}

export function isWin(s: Session): boolean {
  return phaseOf(s.session_type).code === "R" && s.overview.final_position === 1 && retirement(s) === null;
}

/** Race result edge: win, gain or loss; nothing when the position did not change or is unknown. */
export type ResultBand = "win" | "gain" | "loss" | null;

export function resultBand(s: Session): ResultBand {
  if (isWin(s)) return "win";
  const c = positionChange(s);
  return c === null || c.gained === 0 ? null : c.gained > 0 ? "gain" : "loss";
}

const FIGURE_FIELDS = [
  "green_laps_estimated",
  "caution_laps_estimated",
  "best_clean_lap_s",
  "best_green_lap_s",
  "race_pace_median_s",
  "pace_slope_s_per_lap",
  "pit_stops_detected",
  "longest_green_run",
  "fuel_used_gal",
  "fuel_per_lap_gal",
  "final_position",
  "results_position",
  "longest_run_laps",
  "longest_run_pace_loss_s",
  "timed_lap_1_s",
  "timed_lap_2_s",
] as const;

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

/* ---- the compact phase cell (web round 2, Sessions) -------------------------------- */

/**
 * The labelled slots of a phase cell, in reading order. A race has seven (`pos` to `run`); practice has position, best, its longest
 * run and the pace lost over it (`loss`); qualifying has position and its first two timed laps (`lap1`, `lap2`).
 */
export type SlotKey = "pos" | "laps" | "best" | "pace" | "stops" | "burn" | "run" | "loss" | "lap1" | "lap2";

/**
 * What a phase cell is (backlog #33): the race is the row's subject, practice and qualifying its preamble. A test or a warmup reads
 * as practice.
 */
export type CellKind = "race" | "practice" | "qualify";

export function cellKind(s: Session): CellKind {
  const code = phaseOf(s.session_type).code;
  return code === "R" ? "race" : code === "Q" ? "qualify" : "practice";
}

/**
 * Colour budget of the cell: gain or loss on a supported position change, the green-flag colour on the green-lap count, caution on a
 * caution count above zero, nothing else. `unit` and `sep` are muted text, not colour carriers.
 */
export type PartTone = "gain" | "loss" | "green" | "caution" | "unit" | "sep";
export interface SlotPart {
  text: string;
  tone?: PartTone;
  /** The part's own hover and accessible name: the finish arrow says where the place was gained or lost from (`up 8 from P12`). */
  title?: string;
}
export interface Slot {
  key: SlotKey;
  label: string;
  /** Full words for the tooltip and the accessible name. */
  title: string;
  parts: SlotPart[];
  /** The backend reported nothing: the slot shows `—`. */
  missing: boolean;
  /** The event's fastest lap among its phases: marked by weight, never by colour. */
  eventBest?: boolean;
}

const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const count = (v: number | null | undefined): number | null => (finite(v) ? v : null);

/**
 * The race's median green lap, traffic included (`SessionOverview.race_pace_median_s`, backlog #85), over the same laps as its best.
 * Null reads as absent: the slot shows `—`.
 */
export function racePace(s: Session): number | null {
  const v: unknown = s.overview.race_pace_median_s;
  return finite(v) && v > 0 ? v : null;
}

function missingSlot(key: SlotKey, label: string, title: string): Slot {
  return { key, label, title, parts: [{ text: NONE }], missing: true };
}

/** `Pos` for practice and qualifying, `Finish` for a race. */
export function positionLabel(s: Session): string {
  return phaseOf(s.session_type).code === "R" ? "Finish" : "Pos";
}

/** Lap time as the cell prints it: `15.234`, `1:16.204`. */
function lapText(seconds: number): string {
  if (seconds >= 60) {
    const m = Math.floor(seconds / 60);
    return `${m}:${(seconds - m * 60).toFixed(3).padStart(6, "0")}`;
  }
  return seconds.toFixed(3);
}

/**
 * The seven slots of one phase, every one present so a missing value reads `—` in its place. The finish is a plain figure (no win
 * flag); a position change (race only, against the official start, which the arrow's hover names: `up 8 from P12`) is the one gain or loss colour; the
 * green-lap count is in the green-flag colour, cautions above zero in the caution colour; best, pace, stops, burn and the long run
 * carry no colour.
 */
export function phaseSlots(s: Session, eventBest: number | null): Slot[] {
  const o = s.overview;
  const label = positionLabel(s);
  const out: Slot[] = [];

  const final = o.final_position;
  const retired = retirement(s);
  if (retired) {
    // Left before the flag (#97): no finish and no change; where he was running is the hover.
    const from = startPosition(s);
    const running = retired.position !== null ? `Running P${retired.position} when the recording ended` : "The recording ended before the race did";
    const title = from !== null ? `${running}, started P${from}` : running;
    out.push({ key: "pos", label, title, parts: [{ text: retirementText(retired) }], missing: false });
  } else if (final == null) out.push(missingSlot("pos", label, label === "Finish" ? "Finish position" : "Classification"));
  else {
    const parts: SlotPart[] = [{ text: formatPosition(final) }];
    const change = positionChange(s);
    if (change && change.gained !== 0) {
      const places = Math.abs(change.gained);
      const gain = change.gained > 0;
      // The grid position is not printed (#68): it is the arrow's hover and accessible name.
      parts.push({ text: `${gain ? "▲" : "▼"}${places}`, tone: gain ? "gain" : "loss", title: `${gain ? "up" : "down"} ${places} from P${change.from}` });
    }
    out.push({ key: "pos", label, title: change ? `Finished P${final}, started P${change.from}` : `Finished P${final}`, parts, missing: false });
  }

  const green = count(o.green_laps_estimated);
  const caution = count(o.caution_laps_estimated);
  if (green === null && caution === null) out.push(missingSlot("laps", "Grn · Cau", "Green · caution laps"));
  else
    out.push({
      key: "laps",
      label: "Grn · Cau",
      title: "Green · caution laps",
      parts: [
        green === null ? { text: NONE } : { text: String(green), tone: green > 0 ? "green" : undefined },
        { text: "·", tone: "sep" },
        caution === null ? { text: NONE } : { text: String(caution), tone: caution > 0 ? "caution" : undefined },
      ],
      missing: false,
    });

  out.push(lapSlot("best", "Best", "Fastest green lap, traffic included", raceBestLap(s), eventBest));

  const median = racePace(s);
  const slope = o.pace_slope_s_per_lap;
  const trend = finite(slope);
  const paceTitle = "Median green lap, traffic included, and the trend, s per lap";
  if (median === null && !trend) out.push(missingSlot("pace", "Pace", paceTitle));
  else {
    const parts: SlotPart[] = [];
    if (median !== null) parts.push({ text: lapText(median) });
    if (trend) parts.push({ text: formatPace(slope), tone: median !== null ? "unit" : undefined });
    out.push({ key: "pace", label: "Pace", title: paceTitle, parts, missing: false });
  }

  const stops = count(o.pit_stops_detected);
  out.push(stops === null ? missingSlot("stops", "Stops", "Pit stops") : { key: "stops", label: "Stops", title: "Pit stops", parts: [{ text: String(stops) }], missing: false });

  const burn = burnPerLap(s);
  const burnTitle = "Fuel burn per green lap, gallons";
  out.push(burn === null ? missingSlot("burn", "Burn", burnTitle) : { key: "burn", label: "Burn", title: burnTitle, parts: [{ text: burn.toFixed(2) }, { text: "gal/lap", tone: "unit" }], missing: false });

  const run = count(o.longest_green_run);
  out.push(run === null ? missingSlot("run", "Long run", "Longest green run, laps") : { key: "run", label: "Long run", title: "Longest green run, laps", parts: [{ text: String(run) }], missing: false });
  return out;
}

/** Signed seconds to 2 decimals with a true minus: `+0.21`, `−0.08`, `0.00`. */
export function formatDelta(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return NONE;
  const r = Math.round(seconds * 100) / 100;
  if (r === 0) return "0.00";
  const body = Math.abs(r).toFixed(2);
  return r > 0 ? `+${body}` : `−${body}`;
}

/** Practice and qualifying place, `P3 / 24` with the field size when the official results give it (backlog #55). */
function positionSlot(s: Session): Slot {
  const c = classification(s);
  if (c === null) return missingSlot("pos", "Pos", "Classification");
  const parts: SlotPart[] = [{ text: formatPosition(c.position) }];
  if (c.field !== null) parts.push({ text: `/ ${c.field}`, tone: "unit" });
  return { key: "pos", label: "Pos", title: c.field !== null ? `Classified P${c.position} of ${c.field}` : `Last recorded P${c.position}`, parts, missing: false };
}

/** A lap time slot; `eventBest` marks it by weight when it is the event's fastest lap. */
function lapSlot(key: SlotKey, label: string, title: string, seconds: unknown, eventBest: number | null): Slot {
  if (!finite(seconds) || seconds <= 0) return missingSlot(key, label, title);
  const isEventBest = eventBest !== null && seconds === eventBest;
  return { key, label, title: isEventBest ? `${title}, fastest of the event` : title, parts: [{ text: lapText(seconds) }], missing: false, eventBest: isEventBest || undefined };
}

/**
 * Practice (backlog #33): position, fastest lap, the longest run without a reset to the pits or garage (laps) and the pace lost over
 * it (the backend's last minus first clean lap of that run, `+0.21 s`). No colour: a pace loss is a plain signed figure.
 */
export function practiceSlots(s: Session, eventBest: number | null): Slot[] {
  const o = s.overview;
  const runLaps = count(o.longest_run_laps);
  const loss = o.longest_run_pace_loss_s;
  const lossTitle = "Pace lost over the longest run: its last clean lap minus its first, s";
  return [
    positionSlot(s),
    lapSlot("best", "Best", "Fastest clean lap", bestLap(s), eventBest),
    runLaps === null ? missingSlot("run", "Long run", "Longest run without a reset, laps") : { key: "run", label: "Long run", title: "Longest run without a reset, laps", parts: [{ text: String(runLaps) }, { text: "laps", tone: "unit" }], missing: false },
    finite(loss) ? { key: "loss", label: "Fall-off", title: lossTitle, parts: [{ text: formatDelta(loss) }, { text: "s", tone: "unit" }], missing: false } : missingSlot("loss", "Fall-off", lossTitle),
  ];
}

/** Qualifying (backlog #33): position and the first two timed laps. */
export function qualifySlots(s: Session, eventBest: number | null): Slot[] {
  const o = s.overview;
  return [positionSlot(s), lapSlot("lap1", "Lap 1", "First timed lap", o.timed_lap_1_s, eventBest), lapSlot("lap2", "Lap 2", "Second timed lap", o.timed_lap_2_s, eventBest)];
}

/** The slots of one phase cell by its kind. */
export function cellSlots(s: Session, eventBest: number | null): Slot[] {
  const kind = cellKind(s);
  return kind === "race" ? phaseSlots(s, eventBest) : kind === "qualify" ? qualifySlots(s, eventBest) : practiceSlots(s, eventBest);
}

/** Coloured figures in one cell: the budget is at most three (one position change, the green count, one caution count). */
export function colouredFigures(slots: readonly Slot[]): number {
  return slots.reduce((n, s) => n + s.parts.filter((p) => p.tone === "gain" || p.tone === "loss" || p.tone === "green" || p.tone === "caution").length, 0);
}

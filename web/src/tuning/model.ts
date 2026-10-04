import type { Schemas } from "../core/api";
import { type Obj, arr, at, num, obj, str, strings } from "../core/json";
import { formatDate, humanize, sessionTypeLabel } from "../core/format";

export type Turn = Schemas["TuningTurn"];
export type RunPhase = "early" | "middle" | "late";
export type Stage = "entry" | "center" | "exit";
export type Balance = "tight" | "good" | "loose";
export type Goal = NonNullable<Schemas["TuningRecommendRequest"]["goal"]>;

export const RUN_PHASES: readonly [RunPhase, string][] = [
  ["early", "Early"],
  ["middle", "Mid"],
  ["late", "Late"],
];
export const STAGES: readonly [Stage, string][] = [
  ["entry", "Entry"],
  ["center", "Center"],
  ["exit", "Exit"],
];
export const BALANCES: readonly [Balance, string][] = [
  ["tight", "Tight"],
  ["good", "Comfortable"],
  ["loose", "Loose"],
];
export const SYMPTOMS: readonly { id: string; label: string; help: string }[] = [
  { id: "unstable-braking", label: "Unstable braking", help: "Car moves or rotates unpredictably under braking" },
  { id: "wheel-hop-lock", label: "Wheel hop / lock", help: "A tire hops or locks under braking" },
  { id: "cant-take-throttle", label: "Can't take throttle", help: "Throttle application makes the car push or step out" },
  { id: "wheelspin", label: "Wheelspin", help: "Lacks drive or spins the rear tires" },
  { id: "bottoming", label: "Bottoming", help: "Splitter or chassis contact" },
  { id: "harsh-skating", label: "Harsh / skating", help: "Skates or loses compliance over bumps and seams" },
  { id: "low-grip", label: "Low grip", help: "Both ends lack grip" },
  { id: "other", label: "Other", help: "Describe it in the note" },
];
export const GOALS: readonly [Goal, string][] = [
  ["long-run-pace", "Long-run pace"],
  ["tire-life", "Tire life"],
  ["restart-pace", "Restart pace"],
  ["stability", "Stability"],
];
export const EXTRA_STAGES: readonly [Stage | "whole", string][] = [
  ["whole", "Whole turn"],
  ["entry", "Entry"],
  ["center", "Center"],
  ["exit", "Exit"],
];

/**
 * One saved observation. Field names match the worker's closed input schema (`TuningFeedbackItem`) plus `priority`.
 * Bounds are absent when the saved item had none (never zero-filled); `unknown` keeps every field this page does not know.
 */
export type FeedbackItem = {
  feedback_id: string;
  corner_id: string;
  corner_label: string;
  start_pct?: number;
  apex_pct?: number;
  end_pct?: number;
  run_phase: RunPhase;
  corner_phases: (Stage | "whole")[];
  symptom_id: string;
  severity: number;
  driver_confidence: number;
  priority: number;
  note: string;
  unknown: Obj;
};

const FEEDBACK_KEYS = new Set(["feedback_id", "corner_id", "corner_label", "start_pct", "apex_pct", "end_pct", "run_phase", "corner_phases", "symptom_id", "severity", "driver_confidence", "priority", "note"]);

/** The draft body stored per exact session identity (`TuningDraft.body`): the wire `TuningDraftBody` with its fields narrowed to what this page writes. Unknown fields are kept when saving. */
export type DraftBody = Omit<Schemas["TuningDraftBody"], "goal" | "general_note" | "representative_run_ids" | "open_target_analysis_id" | "feedback"> & {
  goal: Goal;
  general_note: string;
  representative_run_ids: string[];
  open_target_analysis_id: string | null;
  feedback: FeedbackItem[];
  /** Saved items this page cannot edit (a field it needs is missing): kept as they were and written back untouched. */
  incomplete: Obj[];
};

export const EMPTY_DRAFT: DraftBody = { goal: "long-run-pace", general_note: "", representative_run_ids: [], open_target_analysis_id: null, feedback: [], incomplete: [] };

export function readDraft(body: unknown): DraftBody {
  const b = obj(body);
  const goal = str(b.goal);
  const items = arr(b.feedback);
  const parsed = items.map(readFeedback);
  return {
    ...b,
    goal: GOALS.some(([g]) => g === goal) ? (goal as Goal) : "long-run-pace",
    general_note: str(b.general_note),
    representative_run_ids: strings(b.representative_run_ids).slice(0, 3),
    open_target_analysis_id: str(b.open_target_analysis_id) || null,
    feedback: parsed.filter((f): f is FeedbackItem => f !== null),
    incomplete: items.filter((_, i) => parsed[i] === null).map(obj),
  };
}

function readFeedback(v: unknown): FeedbackItem | null {
  const f = obj(v);
  const corner = str(f.corner_id);
  const symptom = str(f.symptom_id);
  const phase = str(f.run_phase) as RunPhase;
  if (!corner || !symptom || !RUN_PHASES.some(([p]) => p === phase)) return null;
  const phases = strings(f.corner_phases).filter((p): p is Stage | "whole" => ["entry", "center", "exit", "whole"].includes(p));
  const bound = (k: string): { [k: string]: number } => {
    const n = num(f[k]);
    return n === null ? {} : { [k]: n };
  };
  return {
    feedback_id: str(f.feedback_id) || feedbackId(corner, phase, phases[0] ?? "whole", symptom),
    corner_id: corner,
    corner_label: str(f.corner_label) || corner,
    ...bound("start_pct"),
    ...bound("apex_pct"),
    ...bound("end_pct"),
    run_phase: phase,
    corner_phases: phases.length ? phases : ["whole"],
    symptom_id: symptom,
    severity: clamp(num(f.severity) ?? 3, 1, 5),
    driver_confidence: clamp(num(f.driver_confidence) ?? 3, 1, 5),
    priority: clamp(num(f.priority) ?? 3, 1, 5),
    note: str(f.note),
    unknown: Object.fromEntries(Object.entries(f).filter(([k]) => !FEEDBACK_KEYS.has(k))),
  };
}

/** One item as saved: the fields this page does not know first, then the ones it edits; a missing bound stays missing. */
function wireItem(f: FeedbackItem): Obj {
  return {
    ...f.unknown,
    feedback_id: f.feedback_id,
    corner_id: f.corner_id,
    corner_label: f.corner_label,
    ...(f.start_pct !== undefined ? { start_pct: f.start_pct } : {}),
    ...(f.apex_pct !== undefined ? { apex_pct: f.apex_pct } : {}),
    ...(f.end_pct !== undefined ? { end_pct: f.end_pct } : {}),
    run_phase: f.run_phase,
    corner_phases: f.corner_phases,
    symptom_id: f.symptom_id,
    severity: f.severity,
    driver_confidence: f.driver_confidence,
    priority: f.priority,
    note: f.note,
  };
}

/** The body to save: every field read comes back, including unknown ones and items this page cannot edit. */
export function serializeDraft(draft: DraftBody): Schemas["TuningDraftBody"] {
  const { incomplete, ...rest } = draft;
  return { ...rest, feedback: [...draft.feedback.map(wireItem), ...incomplete] };
}

const clamp = (n: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, Math.round(n)));

/** Deterministic id, so saving the same observation twice replaces it. (No crypto: the site is plain HTTP.) */
export function feedbackId(corner: string, phase: string, stage: string, symptom: string): string {
  const slug = `${corner}-${phase}-${stage}-${symptom}`.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
  return `fb-${slug}`.slice(0, 160);
}

export interface EditorState {
  balance: Partial<Record<Stage, Balance>>;
  /** `stage|symptom` keys of the extra symptoms. */
  extras: string[];
  note: string;
  severity: number;
  confidence: number;
}

export const EMPTY_EDITOR: EditorState = { balance: {}, extras: [], note: "", severity: 3, confidence: 3 };

const isBalance = (s: string): s is Balance => s === "tight" || s === "good" || s === "loose";

/** The editor's view of what is saved for one turn and run phase (a whole-turn balance applies to all three stages). */
export function readEditorState(feedback: readonly FeedbackItem[], cornerId: string, phase: RunPhase): EditorState {
  const rel = feedback.filter((f) => f.corner_id === cornerId && f.run_phase === phase).sort((a, b) => (a.feedback_id < b.feedback_id ? -1 : 1));
  const state: EditorState = { balance: {}, extras: [], note: "", severity: rel[0]?.severity ?? 3, confidence: rel[0]?.driver_confidence ?? 3 };
  state.note = rel.map((f) => f.note).find((n) => n.trim()) ?? "";
  for (const f of rel) {
    const stages = f.corner_phases.length ? f.corner_phases : (["whole"] as const);
    if (isBalance(f.symptom_id)) {
      const target: Stage[] = stages.includes("whole") ? ["entry", "center", "exit"] : (stages.filter((s) => s !== "whole") as Stage[]);
      for (const s of target) state.balance[s] ??= f.symptom_id;
    } else {
      for (const s of stages) state.extras.push(`${s}|${f.symptom_id}`);
    }
  }
  state.extras = [...new Set(state.extras)].sort();
  return state;
}

/** Items for one turn and run phase: stage balances, extra symptoms (ordinal order), else a lone note as "other". */
export function buildFeedback(turn: Turn, phase: RunPhase, state: EditorState, priority: number, previous: readonly FeedbackItem[] = []): FeedbackItem[] {
  const note = state.note.trim();
  const make = (stage: Stage | "whole", symptom: string): FeedbackItem => ({
    feedback_id: feedbackId(turn.corner_id, phase, stage, symptom),
    corner_id: turn.corner_id,
    corner_label: turn.label,
    start_pct: pct(turn.start_pct),
    apex_pct: pct(turn.apex_pct),
    end_pct: pct(turn.end_pct),
    run_phase: phase,
    corner_phases: [stage],
    symptom_id: symptom,
    severity: state.severity,
    driver_confidence: state.confidence,
    priority,
    note,
    unknown: previous.find((p) => p.feedback_id === feedbackId(turn.corner_id, phase, stage, symptom))?.unknown ?? {},
  });
  const out: FeedbackItem[] = [];
  for (const [stage] of STAGES) {
    const b = state.balance[stage];
    if (b) out.push(make(stage, b));
  }
  for (const key of [...state.extras].sort()) {
    const i = key.indexOf("|");
    if (i < 1 || i >= key.length - 1) continue;
    out.push(make(key.slice(0, i) as Stage | "whole", key.slice(i + 1)));
  }
  if (out.length === 0 && note) out.push(make("whole", "other"));
  return out;
}

/** The schema wants fractions in [0, 1). */
export const pct = (v: number): number => Math.min(0.999999, Math.max(0, v));

export function replaceFeedback(draft: DraftBody, cornerId: string, phase: RunPhase, items: FeedbackItem[]): DraftBody {
  return { ...draft, feedback: [...draft.feedback.filter((f) => !(f.corner_id === cornerId && f.run_phase === phase)), ...items] };
}

/** Priority 5 marks the one highest-priority turn; every other item is at most 4. */
export function applyPriority(draft: DraftBody, cornerId: string | null): DraftBody {
  return { ...draft, feedback: draft.feedback.map((f) => ({ ...f, priority: f.corner_id === cornerId ? 5 : Math.min(f.priority, 4) })) };
}

export function priorityCorner(draft: DraftBody): string | null {
  return draft.feedback.find((f) => f.priority >= 5)?.corner_id ?? null;
}

export function phaseCount(draft: DraftBody, phase: RunPhase): number {
  return draft.feedback.filter((f) => f.run_phase === phase).length;
}

export function toggleRun(draft: DraftBody, run: string): DraftBody {
  const sel = draft.representative_run_ids;
  if (sel.includes(run)) return sel.length === 1 ? draft : { ...draft, representative_run_ids: sel.filter((r) => r !== run) };
  return sel.length >= 3 ? draft : { ...draft, representative_run_ids: [...sel, run] };
}

// ---- turns and the submitted map

export interface MapTurn extends Turn {
  user_verified: boolean;
}

/**
 * Driver corrections override the projected turns of the same id and mark them verified. A correction stored with a
 * geometry hash applies only while the map's geometry is the one it was made on. The label may change; the turn
 * stays official only while it does not.
 */
export function mergeTurns(turns: readonly Turn[], annotations: unknown, geometryHash: string | null = null): MapTurn[] {
  const fixes = new Map<string, Obj>();
  for (const c of arr(obj(annotations).corners)) {
    const o = obj(c);
    if (str(o.corner_id) && !isStale(o, geometryHash)) fixes.set(str(o.corner_id), o);
  }
  return turns.map((t) => {
    const f = fixes.get(t.corner_id);
    if (!f) return { ...t, user_verified: false };
    const label = str(f.label) || t.label;
    return {
      ...t,
      label,
      start_pct: num(f.start_pct) ?? t.start_pct,
      apex_pct: num(f.apex_pct) ?? t.apex_pct,
      end_pct: num(f.end_pct) ?? t.end_pct,
      is_official: t.is_official && label === t.label,
      user_verified: true,
      verification_source: "Driver correction",
    };
  });
}

function isStale(correction: Obj, geometryHash: string | null): boolean {
  const stored = str(correction.geometry_hash);
  return stored !== "" && geometryHash !== null && stored !== geometryHash;
}

/** Corrections saved on other geometry, which `mergeTurns` skips. */
export function staleCorrections(annotations: unknown, geometryHash: string | null): number {
  return arr(obj(annotations).corners).filter((c) => isStale(obj(c), geometryHash)).length;
}

/**
 * Body of `PUT turn-annotations`: every driver-corrected turn, replacing the one with the same id. Fields this page
 * does not know are kept. `original` is the projected turn: a renamed turn is no longer official.
 */
export function annotationBody(existing: unknown, corrected: MapTurn, original: Pick<Turn, "label" | "is_official"> = corrected, geometryHash: string | null = null): { corners: Obj[] } & Obj {
  const body = obj(existing);
  const all = arr(body.corners).map(obj);
  const before = all.find((c) => str(c.corner_id) === corrected.corner_id) ?? {};
  const keep = all.filter((c) => str(c.corner_id) !== corrected.corner_id);
  return {
    ...body,
    corners: [
      ...keep,
      {
        ...before,
        corner_id: corrected.corner_id,
        label: corrected.label,
        start_pct: pct(corrected.start_pct),
        apex_pct: pct(corrected.apex_pct),
        end_pct: pct(corrected.end_pct),
        is_official: original.is_official && corrected.label === original.label,
        user_verified: true,
        ...(geometryHash ? { geometry_hash: geometryHash } : {}),
      },
    ],
  };
}

/** Entry, apex and exit must go forward around the lap, each inside [0, 1). */
export function validBounds(start: number, apex: number, end: number): string | null {
  if (![start, apex, end].every((v) => Number.isFinite(v) && v >= 0 && v < 1)) return "Bounds must be inside the lap";
  const fwd = (a: number, b: number) => (b - a + 1) % 1;
  if (fwd(start, apex) === 0 || fwd(apex, end) === 0) return "Entry, apex and exit must be different points";
  if (fwd(start, apex) + fwd(apex, end) > 1 - 1e-9) return "Entry, apex and exit must follow the direction of travel";
  return null;
}

/** Items follow the turn they belong to: its current label and bounds, wherever the correction was made. */
export function alignFeedback(feedback: readonly FeedbackItem[], turns: readonly MapTurn[]): FeedbackItem[] {
  const byId = new Map(turns.map((t) => [t.corner_id, t]));
  return feedback.map((f) => {
    const t = byId.get(f.corner_id);
    return t ? { ...f, corner_label: t.label, start_pct: pct(t.start_pct), apex_pct: pct(t.apex_pct), end_pct: pct(t.end_pct) } : f;
  });
}

export type MapSubmission = Schemas["TuningMapSubmission"];

export interface Verification {
  verified: number;
  total: number;
}

/** How many turns of the map the driver verified; the projected map counts as verified throughout. */
export function verification(map: Pick<Schemas["TuningMapView"], "is_verified">, turns: readonly MapTurn[]): Verification {
  return { verified: map.is_verified ? turns.length : turns.filter((t) => t.user_verified).length, total: turns.length };
}

/**
 * The map the analysis may use: every turn, as the old app submitted it. It is verified when the projected map is, or
 * when the driver verified every turn; otherwise the page asks for verification first.
 */
export function buildMapSubmission(map: Schemas["TuningMapView"], turns: readonly MapTurn[], trackKey: string | null): { submission: MapSubmission | null; verified: boolean } {
  if (!trackKey || turns.length === 0) return { submission: null, verified: false };
  const verified = map.is_verified || turns.every((t) => t.user_verified);
  const manual = !map.is_verified && verified;
  return {
    verified,
    submission: {
      map_identity: map.map_identity,
      track_configuration_key: trackKey,
      source_type: (manual ? "verified-manual" : map.source_type) as MapSubmission["source_type"],
      source_label: map.source_label.slice(0, 200),
      ...(map.source_url ? { source_url: map.source_url } : {}),
      verified,
      corners: turns.slice(0, 100).map((t) => ({
        corner_id: t.corner_id,
        label: t.label,
        start_pct: pct(t.start_pct),
        apex_pct: pct(t.apex_pct),
        end_pct: pct(t.end_pct),
        is_official: t.is_official,
        user_verified: t.user_verified,
      })),
    },
  };
}

/** The schema's closed item: a bound the item never had is left out, not sent as null or zero. */
export function toRequestFeedback(items: readonly FeedbackItem[]): Schemas["TuningFeedbackItem"][] {
  return items.map((f) => ({
    feedback_id: f.feedback_id.slice(0, 160),
    corner_id: f.corner_id,
    corner_label: f.corner_label.slice(0, 160),
    ...(f.start_pct !== undefined ? { start_pct: pct(f.start_pct) } : {}),
    ...(f.apex_pct !== undefined ? { apex_pct: pct(f.apex_pct) } : {}),
    ...(f.end_pct !== undefined ? { end_pct: pct(f.end_pct) } : {}),
    run_phase: f.run_phase,
    corner_phases: f.corner_phases.length ? [...new Set(f.corner_phases)] : ["whole"],
    symptom_id: f.symptom_id.slice(0, 80),
    severity: f.severity,
    driver_confidence: f.driver_confidence,
    priority: f.priority,
    ...(f.note ? { note: f.note.slice(0, 2000) } : {}),
  }));
}

// ---- setup type, eligibility and targets

/** An Open setup can take a change; anything else (Fixed, Unknown) supplies driving evidence for an open target. */
export function needsTarget(ctx: Pick<Schemas["TuningContext"], "identity">): boolean {
  return ctx.identity.setup_type.toLowerCase() !== "open";
}

export function setupTypeLabel(ctx: Pick<Schemas["TuningContext"], "identity">): string {
  const t = ctx.identity.setup_type.toLowerCase();
  return t === "open" ? "Open setup" : t === "fixed" ? "Fixed setup" : "Setup type unknown";
}

/** Why this recording cannot take a recommendation: the backend's reasons, whenever it says not eligible. */
export function eligibilityReasons(ctx: Pick<Schemas["TuningContext"], "eligible" | "reasons">): string[] {
  if (ctx.eligible) return [];
  return ctx.reasons.length ? ctx.reasons : ["Not eligible for setup changes"];
}

/** The saved open target, only while the server still offers it. */
export function chosenTarget(ctx: Pick<Schemas["TuningContext"], "open_targets">, draft: Pick<DraftBody, "open_target_analysis_id">): string | null {
  const id = draft.open_target_analysis_id;
  return id && ctx.open_targets.some((t) => t.analysis_id === id) ? id : null;
}

export interface TargetChoice {
  analysisId: string;
  /** ISO start time, for ordering. */
  startedAt: string | null;
  label: string;
}

type SessionRow = Pick<Schemas["SessionSummary"], "analysis_id" | "analysis_state" | "car_path" | "car_name" | "track" | "track_config" | "start_time_utc" | "session_type">;

const same = (a: string | null | undefined, b: string | null | undefined) => (a ?? "").trim().toLowerCase() === (b ?? "").trim().toLowerCase();

/**
 * Recordings that can answer a test: the server's open targets plus every other analyzed recording of the same car and
 * track layout, newest first. The worker accepts any of them, so a Fixed or Open setup can link its result.
 */
export function outcomeChoices(ctx: Schemas["TuningContext"], analysisId: string, sessions: readonly SessionRow[]): TargetChoice[] {
  const id = ctx.identity;
  const out = new Map<string, TargetChoice>();
  for (const t of ctx.open_targets) out.set(t.analysis_id, { analysisId: t.analysis_id, startedAt: t.start_time_utc ?? null, label: `${formatDate(t.start_time_utc)} · ${t.identity.car ?? t.identity.car_path ?? "Car"}` });
  for (const s of sessions) {
    if (s.analysis_state !== "ready" || !s.analysis_id || s.analysis_id === analysisId || out.has(s.analysis_id)) continue;
    const car = id.car_path ? same(s.car_path, id.car_path) : same(s.car_name, id.car);
    if (!car || !same(s.track, id.track) || !same(s.track_config, id.layout)) continue;
    out.set(s.analysis_id, { analysisId: s.analysis_id, startedAt: s.start_time_utc ?? null, label: `${formatDate(s.start_time_utc)} · ${sessionTypeLabel(s.session_type)}` });
  }
  return [...out.values()].sort((a, b) => (Date.parse(b.startedAt ?? "") || 0) - (Date.parse(a.startedAt ?? "") || 0));
}

export interface AnalyzeInputs {
  ctx: Schemas["TuningContext"];
  draft: DraftBody;
  verified: boolean;
  hasSubmission: boolean;
  /** Turns the driver has verified; shown while the map is not. */
  progress?: Verification;
}

/** Why Begin analysis is unavailable, in the order the driver can fix it; empty when it is available. */
export function analyzeBlocker(i: AnalyzeInputs): string {
  if (!i.ctx.identity.track_configuration_key) return "Track configuration unavailable";
  if (i.draft.feedback.length === 0 || !i.hasSubmission) return "Select a turn and describe the balance.";
  if (!i.verified) return i.progress && i.progress.total > 0 ? `Verify all turns (${i.progress.verified} of ${i.progress.total})` : "Verify the turn bounds before analysis.";
  if (i.draft.representative_run_ids.length === 0) return "Choose a representative run.";
  if (needsTarget(i.ctx) && !chosenTarget(i.ctx, i.draft)) return "Choose a matching open setup.";
  return "";
}

export function recommendRequest(i: AnalyzeInputs, submission: MapSubmission): Schemas["TuningRecommendRequest"] {
  return {
    feedback: toRequestFeedback(i.draft.feedback).slice(0, 100),
    representative_run_ids: i.draft.representative_run_ids.slice(0, 3),
    map_identity: submission,
    goal: i.draft.goal,
    generic_note: i.draft.general_note.trim() ? i.draft.general_note.trim().slice(0, 4000) : null,
    maximum_changes: 1,
    ...(needsTarget(i.ctx) ? { open_target_analysis_id: chosenTarget(i.ctx, i.draft) } : {}),
  };
}

// ---- runs and results

export interface RunFacts {
  number: string;
  greenLaps: number | null;
  delta: number | null;
  slope: number | null;
  fuel: number | null;
  lowestTire: number | null;
}

export function runFacts(run: Schemas["BackendRun"]): RunFacts {
  const r = run as Record<string, unknown>;
  const green = r.green_laps;
  return {
    number: String(r.run_number ?? ""),
    greenLaps: typeof green === "number" ? green : Array.isArray(green) ? green.length : null,
    delta: num(at(r, "pace.early_to_late_delta_s")),
    slope: num(at(r, "pace.green_lap_time_slope_s_per_lap")),
    fuel: num(at(r, "fuel.used_gal")),
    lowestTire: num(at(r, "tire_observation.lowest_remaining_percent")),
  };
}

export interface Candidate {
  id: string;
  system: string;
  change: string;
  effect: string;
  risk: string;
  verify: string[];
  /** This candidate's own protocol (`candidate_whitelist[].test_protocol`). */
  protocol: { control: string; minimumRun: string; rollback: string } | null;
  /** What to restore before another test or when the named risk appears. */
  rollback: string;
}

export interface TuningResult {
  experimentId: string;
  status: string;
  summary: string;
  selected: Candidate | null;
  missing: string[];
  limitations: string[];
  /** A top-level protocol, when the worker sends one; the selected candidate's own protocol is `selected.protocol`. */
  protocol: { control: string; sequence: string[]; comparison: string[] } | null;
}

/** A worker reason as words: codes (`feedback-1-corner-bounds-mismatch`) are humanized, sentences stay. */
export function reasonText(reason: string): string {
  return /\s/.test(reason.trim()) ? reason.trim() : humanize(reason);
}

function readCandidate(v: unknown, i: number): Candidate {
  const c = obj(v);
  const p = obj(c.test_protocol);
  return {
    id: str(c.candidate_id) || str(c.id) || `candidate-${i + 1}`,
    system: str(c.system) || "Setup",
    change: str(c.change),
    effect: str(c.predicted_effect),
    risk: str(c.risk),
    verify: [...new Set([...strings(c.verify), ...strings(c.verification)])],
    protocol: Object.keys(p).length ? { control: str(p.control), minimumRun: str(p.minimum_run), rollback: str(p.rollback) } : null,
    rollback: str(obj(c.rollback).instruction),
  };
}

/** The recommendation job's result (tuning-recommendation-v1) reduced to what the page shows. Anything else is ignored. */
export function readResult(raw: unknown): TuningResult {
  const wrapped = obj(obj(raw).result);
  const root = Object.keys(wrapped).length ? wrapped : obj(raw);
  const rec = obj(root.recommendation);
  const items = arr(root.candidate_whitelist).length ? arr(root.candidate_whitelist) : arr(rec.candidate_whitelist);
  const candidates = items.map(readCandidate);
  const selectedId = str(rec.selected_candidate_id);
  const protocol = obj(root.test_protocol);
  const elig = obj(root.eligibility);
  return {
    experimentId: str(root.experiment_id),
    status: str(rec.status) || str(root.status) || "unavailable",
    summary: str(rec.summary) || str(rec.explanation),
    selected: candidates.find((c) => c.id === selectedId) ?? candidates[0] ?? null,
    missing: [...new Set([...strings(root.missing_required), ...strings(elig.missing_required)])],
    limitations: strings(root.limitations),
    protocol: Object.keys(protocol).length ? { control: str(protocol.control), sequence: strings(protocol.sequence), comparison: strings(protocol.comparison_requirements) } : null,
  };
}

/** The last test the driver started from this recording, kept in the draft so its result can be recorded after the next run. */
export interface LastExperiment {
  experimentId: string;
  system: string;
  change: string;
}

export function readLastExperiment(draft: Obj): LastExperiment | null {
  const e = obj(draft.last_experiment);
  const id = str(e.experiment_id);
  return id ? { experimentId: id, system: str(e.system), change: str(e.change) } : null;
}

export function withExperiment(draft: DraftBody, result: TuningResult): DraftBody {
  if (!result.experimentId) return draft;
  return { ...draft, last_experiment: { experiment_id: result.experimentId, system: result.selected?.system ?? "", change: result.selected?.change ?? "" } };
}

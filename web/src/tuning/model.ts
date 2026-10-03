import type { Schemas } from "../core/api";
import { arr, at, num, obj, str, strings } from "../core/json";

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

/** One saved observation. Field names match the worker's closed input schema (`TuningFeedbackItem`) plus `priority`. */
export interface FeedbackItem {
  feedback_id: string;
  corner_id: string;
  corner_label: string;
  start_pct: number;
  apex_pct: number;
  end_pct: number;
  run_phase: RunPhase;
  corner_phases: (Stage | "whole")[];
  symptom_id: string;
  severity: number;
  driver_confidence: number;
  priority: number;
  note: string;
}

/** The draft body stored per exact session identity (`TuningDraft.body`). Unknown fields are kept when saving. */
export interface DraftBody {
  goal: Goal;
  general_note: string;
  representative_run_ids: string[];
  open_target_analysis_id: string | null;
  feedback: FeedbackItem[];
  [extra: string]: unknown;
}

export const EMPTY_DRAFT: DraftBody = { goal: "long-run-pace", general_note: "", representative_run_ids: [], open_target_analysis_id: null, feedback: [] };

export function readDraft(body: unknown): DraftBody {
  const b = obj(body);
  const goal = str(b.goal);
  return {
    ...b,
    goal: GOALS.some(([g]) => g === goal) ? (goal as Goal) : "long-run-pace",
    general_note: str(b.general_note),
    representative_run_ids: strings(b.representative_run_ids).slice(0, 3),
    open_target_analysis_id: str(b.open_target_analysis_id) || null,
    feedback: arr(b.feedback).map(readFeedback).filter((f): f is FeedbackItem => f !== null),
  };
}

function readFeedback(v: unknown): FeedbackItem | null {
  const f = obj(v);
  const corner = str(f.corner_id);
  const symptom = str(f.symptom_id);
  const phase = str(f.run_phase) as RunPhase;
  if (!corner || !symptom || !RUN_PHASES.some(([p]) => p === phase)) return null;
  const phases = strings(f.corner_phases).filter((p): p is Stage | "whole" => ["entry", "center", "exit", "whole"].includes(p));
  return {
    feedback_id: str(f.feedback_id) || feedbackId(corner, phase, phases[0] ?? "whole", symptom),
    corner_id: corner,
    corner_label: str(f.corner_label) || corner,
    start_pct: num(f.start_pct) ?? 0,
    apex_pct: num(f.apex_pct) ?? 0,
    end_pct: num(f.end_pct) ?? 0,
    run_phase: phase,
    corner_phases: phases.length ? phases : ["whole"],
    symptom_id: symptom,
    severity: clamp(num(f.severity) ?? 3, 1, 5),
    driver_confidence: clamp(num(f.driver_confidence) ?? 3, 1, 5),
    priority: clamp(num(f.priority) ?? 3, 1, 5),
    note: str(f.note),
  };
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
export function buildFeedback(turn: Turn, phase: RunPhase, state: EditorState, priority: number): FeedbackItem[] {
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

/** Driver corrections override the projected turns of the same id and mark them verified. */
export function mergeTurns(turns: readonly Turn[], annotations: unknown): MapTurn[] {
  const fixes = new Map<string, Obj2>();
  for (const c of arr(obj(annotations).corners)) {
    const o = obj(c);
    if (str(o.corner_id)) fixes.set(str(o.corner_id), o);
  }
  return turns.map((t) => {
    const f = fixes.get(t.corner_id);
    if (!f) return { ...t, user_verified: false };
    return {
      ...t,
      label: str(f.label) || t.label,
      start_pct: num(f.start_pct) ?? t.start_pct,
      apex_pct: num(f.apex_pct) ?? t.apex_pct,
      end_pct: num(f.end_pct) ?? t.end_pct,
      is_official: f.is_official === true ? true : t.is_official,
      user_verified: true,
      verification_source: "Driver correction",
    };
  });
}
type Obj2 = Record<string, unknown>;

/** Body of `PUT turn-annotations`: every driver-corrected turn, replacing the one with the same id. */
export function annotationBody(existing: unknown, corrected: MapTurn): { corners: Obj2[] } {
  const keep = arr(obj(existing).corners).filter((c) => str(obj(c).corner_id) !== corrected.corner_id);
  return {
    corners: [
      ...(keep as Obj2[]),
      {
        corner_id: corrected.corner_id,
        label: corrected.label,
        start_pct: pct(corrected.start_pct),
        apex_pct: pct(corrected.apex_pct),
        end_pct: pct(corrected.end_pct),
        is_official: corrected.is_official,
        user_verified: true,
      },
    ],
  };
}

/** Entry, apex and exit must go forward around the lap, each inside [0, 1). */
export function validBounds(start: number, apex: number, end: number): string | null {
  if (![start, apex, end].every((v) => Number.isFinite(v) && v >= 0 && v < 1)) return "Bounds must be inside the lap.";
  const fwd = (a: number, b: number) => (b - a + 1) % 1;
  if (fwd(start, apex) === 0 || fwd(apex, end) === 0) return "Entry, apex and exit must be different points.";
  if (fwd(start, apex) + fwd(apex, end) > 1 - 1e-9) return "Entry, apex and exit must follow the direction of travel.";
  return null;
}

export type MapSubmission = Schemas["TuningMapSubmission"];

/**
 * The turns the analysis may use. The map is verified when the projected map is, or when every submitted turn (the
 * ones carrying feedback) was verified by the driver; otherwise the page asks for verification first.
 */
export function buildMapSubmission(map: Schemas["TuningMapView"], turns: readonly MapTurn[], feedback: readonly FeedbackItem[], trackKey: string | null): { submission: MapSubmission | null; verified: boolean } {
  if (!trackKey) return { submission: null, verified: false };
  const used = new Set(feedback.map((f) => f.corner_id));
  const chosen = map.is_verified ? turns : turns.filter((t) => used.has(t.corner_id));
  if (chosen.length === 0) return { submission: null, verified: false };
  const verified = map.is_verified || chosen.every((t) => t.user_verified);
  const manual = !map.is_verified && chosen.some((t) => t.user_verified);
  return {
    verified,
    submission: {
      map_identity: map.map_identity,
      track_configuration_key: trackKey,
      source_type: (manual ? "verified-manual" : map.source_type) as MapSubmission["source_type"],
      source_label: map.source_label.slice(0, 200),
      ...(map.source_url ? { source_url: map.source_url } : {}),
      verified,
      corners: chosen.slice(0, 100).map((t) => ({
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

export function toRequestFeedback(items: readonly FeedbackItem[]): Schemas["TuningFeedbackItem"][] {
  return items.map((f) => ({
    feedback_id: f.feedback_id.slice(0, 160),
    corner_id: f.corner_id,
    corner_label: f.corner_label.slice(0, 160),
    start_pct: pct(f.start_pct),
    apex_pct: pct(f.apex_pct),
    end_pct: pct(f.end_pct),
    run_phase: f.run_phase,
    corner_phases: f.corner_phases.length ? [...new Set(f.corner_phases)] : ["whole"],
    symptom_id: f.symptom_id.slice(0, 80),
    severity: f.severity,
    driver_confidence: f.driver_confidence,
    priority: f.priority,
    ...(f.note ? { note: f.note.slice(0, 2000) } : {}),
  }));
}

export interface AnalyzeInputs {
  ctx: Schemas["TuningContext"];
  draft: DraftBody;
  verified: boolean;
  hasSubmission: boolean;
}

/** Why Begin analysis is unavailable, in the order the driver can fix it; empty when it is available. */
export function analyzeBlocker(i: AnalyzeInputs): string {
  const needsTarget = i.ctx.identity.setup_type.toLowerCase() !== "open";
  if (!i.hasSubmission || i.draft.feedback.length === 0) return "Select a turn and describe the balance.";
  if (!i.verified) return "Verify the turn bounds before analysis.";
  if (i.draft.representative_run_ids.length === 0) return "Choose a representative run.";
  if (needsTarget && !i.draft.open_target_analysis_id) return "Choose a matching open setup.";
  return "";
}

export function recommendRequest(i: AnalyzeInputs, submission: MapSubmission): Schemas["TuningRecommendRequest"] {
  const needsTarget = i.ctx.identity.setup_type.toLowerCase() !== "open";
  return {
    feedback: toRequestFeedback(i.draft.feedback).slice(0, 100),
    representative_run_ids: i.draft.representative_run_ids.slice(0, 3),
    map_identity: submission,
    goal: i.draft.goal,
    generic_note: i.draft.general_note.trim() ? i.draft.general_note.trim().slice(0, 4000) : null,
    maximum_changes: 1,
    ...(needsTarget ? { open_target_analysis_id: i.draft.open_target_analysis_id } : {}),
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
}

export interface TuningResult {
  experimentId: string;
  status: string;
  summary: string;
  selected: Candidate | null;
  missing: string[];
  limitations: string[];
  protocol: { control: string; sequence: string[]; comparison: string[] } | null;
}

/** The recommendation job's result (tuning-recommendation-v1) reduced to what the page shows. Anything else is ignored. */
export function readResult(raw: unknown): TuningResult {
  const wrapped = obj(obj(raw).result);
  const root = Object.keys(wrapped).length ? wrapped : obj(raw);
  const rec = obj(root.recommendation);
  const items = arr(root.candidate_whitelist).length ? arr(root.candidate_whitelist) : arr(rec.candidate_whitelist);
  const candidates: Candidate[] = items.map((v, i) => {
    const c = obj(v);
    return {
      id: str(c.candidate_id) || str(c.id) || `candidate-${i + 1}`,
      system: str(c.system) || "Setup",
      change: str(c.change),
      effect: str(c.predicted_effect),
      risk: str(c.risk),
      verify: [...new Set([...strings(c.verify), ...strings(c.verification)])],
    };
  });
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

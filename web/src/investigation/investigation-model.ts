import { asArray, asRecord, num, str, type Json, type SteeringBasisData } from "./types";

/**
 * Typed read of `analysis_view.race_investigation` (schema 2, projection sections-trials-v7). Only the
 * fields listed in `x-consumed-fields` are read; the backend subtree is otherwise left alone. Missing
 * required fields and an incompatible schema fail clearly (AGENTS.md); optional fields become null.
 */

export const SUPPORTED_INVESTIGATION_SCHEMA = 2;

export class InvestigationFormatError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "InvestigationFormatError";
  }
}

export interface Cohort {
  label: string;
  runNumber: number | null;
  lapNumbers: number[];
  lapCount: number;
  eligibleLapNumbers: number[];
  excludedLaps: { lap: number; reasons: string[] }[];
  medianLapS: number | null;
  greenAgeStart: number | null;
  greenAgeEnd: number | null;
  fuelStartGal: number | null;
  trackTempF: number | null;
  representativeLap: number | null;
}

export interface Metric {
  key: string;
  label: string;
  unit: string;
  baseline: number | null;
  compared: number | null;
  delta: number | null;
  evidence: string | null;
  detail: string | null;
}

export interface Hypothesis {
  key: string;
  label: string;
  status: string;
  supporting: string[];
  contradicting: string[];
  unknown: string[];
}

export interface Action {
  priority: number;
  title: string;
  instruction: string;
  limitation: string | null;
  lapNumbers: number[];
  startPct: number | null;
  endPct: number | null;
  signalKeys: string[];
  sectionId: string | null;
}

export interface Corner {
  id: string;
  label: string;
  shortLabel: string | null;
  startPct: number;
  endPct: number;
  deltaS: number | null;
  boundaryStatus: string | null;
  baselineCount: number;
  comparedCount: number;
}

export interface Pass {
  lap: number;
  role: string;
  sectionS: number | null;
  runNumber: number | null;
  greenExposure: number | null;
  eligible: boolean;
  exclusionReasons: string[];
  fuelStartGal: number | null;
}

export interface PassRow {
  x: number;
  gap: number | null;
  a: Record<string, number | null>;
  b: Record<string, number | null>;
}

export interface PairSteering {
  comparable: boolean;
  degreeDigits: number | null;
  reason: string | null;
  basis: string | null;
  label: string | null;
}

export interface PassPair {
  sectionId: string;
  startPct: number;
  endPct: number;
  contextStartPct: number;
  contextLabel: string;
  timingComplete: boolean;
  candidateCount: number;
  sectionDeltaS: number | null;
  entryGapS: number | null;
  exitGapS: number | null;
  entrySpeedDifferenceMph: number | null;
  a: Pass;
  b: Pass;
  rows: PassRow[];
  steering: PairSteering;
}

export interface Comparison {
  id: string;
  kind: string;
  title: string;
  summary: string;
  deltaS: number | null;
  eligibility: string;
  paceComparable: boolean;
  paceUnusableReasons: string[];
  baseline: Cohort;
  compared: Cohort;
  metrics: Metric[];
  corners: Corner[];
  hypotheses: Hypothesis[];
  actions: Action[];
  inspection: { mode: string; poolLaps: number[]; pairs: Map<string, PassPair> } | null;
}

export interface InvestigationLap {
  lap: number;
  lapTimeS: number | null;
  position: number | null;
  flagState: string;
  eligible: boolean;
  exclusionReasons: string[];
  runNumber: number | null;
  plotPace: boolean;
  greenExposure: number | null;
}

export interface TimelineEvent {
  lap: number;
  kind: string;
  label: string;
  detail: string;
  value: number | null;
  unit: string | null;
}

export interface TrendPoint {
  lap: number;
  x: number;
  y: number;
}

export interface RunView {
  runNumber: number;
  plottedLapCount: number;
  firstLap: number;
  lastLap: number;
  fitUnavailableReason: string | null;
  trend: { method: string | null; coveredLapCount: number; segments: TrendPoint[][]; gaps: { afterLap: number; beforeLap: number; reason: string }[] };
  /** The screened fit; `laps` are the eligible laps the backend fitted (empty when it did not list them). */
  fit: { method: string | null; eligibleCount: number; slopeSPerGreenLap: number | null; domain: [number, number] | null; points: { x: number; y: number }[]; laps: number[] } | null;
}

export interface Section {
  id: string;
  label: string;
  startPct: number;
  endPct: number;
  boundaryStatus: string | null;
  provenance: string;
}

export interface Investigation {
  status: string | null;
  summary: string;
  defaultComparisonId: string | null;
  limitations: string[];
  comparisons: Comparison[];
  laps: InvestigationLap[];
  timeline: TimelineEvent[];
  runs: RunView[];
  sections: { sections: Section[]; unavailableReason: string | null; configurationKey: string | null };
  trackEvolution: { status: string | null; summary: string | null } | null;
  /** The backend steering declaration (SteeringBasis), read for the change thresholds. */
  steeringBasis: SteeringBasisData | null;
}

function required<T>(record: Json, key: string, what: string): T {
  if (!(key in record) || record[key] === null || record[key] === undefined) throw new InvestigationFormatError(`${what} is missing "${key}"`);
  return record[key] as T;
}

function requiredBoolean(record: Json, key: string, what: string): boolean {
  const value = record[key];
  if (typeof value !== "boolean") throw new InvestigationFormatError(`${what} is missing "${key}"`);
  return value;
}

const numbers = (value: unknown): number[] => asArray(value).filter((item): item is number => typeof item === "number" && Number.isFinite(item));
const strings = (value: unknown): string[] => asArray(value).filter((item): item is string => typeof item === "string");

function cohort(raw: unknown): Cohort {
  const r = asRecord(raw);
  return {
    label: str(r.label) ?? "",
    runNumber: num(r.run_number),
    lapNumbers: numbers(r.lap_numbers),
    lapCount: num(r.lap_count) ?? numbers(r.lap_numbers).length,
    eligibleLapNumbers: numbers(r.eligible_lap_numbers),
    excludedLaps: asArray(r.excluded_laps).map((item) => ({ lap: num(asRecord(item).lap) ?? 0, reasons: strings(asRecord(item).reasons) })),
    medianLapS: num(r.median_lap_s),
    greenAgeStart: num(r.green_age_start),
    greenAgeEnd: num(r.green_age_end),
    fuelStartGal: num(r.fuel_start_gal),
    trackTempF: num(r.track_temp_f),
    representativeLap: num(r.representative_lap),
  };
}

function metric(raw: unknown): Metric {
  const r = asRecord(raw);
  return { key: str(r.key) ?? "", label: str(r.label) ?? "", unit: str(r.unit) ?? "", baseline: num(r.baseline), compared: num(r.compared), delta: num(r.delta), evidence: str(r.evidence), detail: str(r.detail) };
}

function pass(raw: unknown, what: string): Pass {
  const r = asRecord(raw);
  return {
    lap: num(r.lap) ?? 0,
    role: str(r.role) ?? "",
    sectionS: num(r.section_s),
    runNumber: num(r.run_number),
    greenExposure: num(r.green_exposure),
    eligible: requiredBoolean(r, "eligible", what),
    exclusionReasons: strings(r.exclusion_reasons),
    fuelStartGal: num(r.fuel_start_gal),
  };
}

function channelValues(raw: unknown): Record<string, number | null> {
  const r = asRecord(raw);
  const out: Record<string, number | null> = {};
  for (const key of ["speed", "throttle", "brake", "steering", "slip"]) out[key] = num(r[key]);
  return out;
}

function pair(raw: unknown, id: string): PassPair {
  const r = asRecord(raw);
  const steering = asRecord(r.steering);
  return {
    sectionId: str(r.section_id) ?? id,
    startPct: required<number>(r, "start_pct", `section ${id}`),
    endPct: required<number>(r, "end_pct", `section ${id}`),
    contextStartPct: num(r.context_start_pct) ?? (num(r.start_pct) ?? 0),
    contextLabel: str(r.context_label) ?? "",
    timingComplete: r.timing_complete === true,
    candidateCount: num(r.candidate_count) ?? 0,
    sectionDeltaS: num(r.section_delta_s),
    entryGapS: num(r.entry_gap_s),
    exitGapS: num(r.exit_gap_s),
    entrySpeedDifferenceMph: num(r.entry_speed_difference_mph),
    a: pass(r.a, `section ${id} pass a`),
    b: pass(r.b, `section ${id} pass b`),
    rows: asArray(r.rows).map((row) => {
      const rr = asRecord(row);
      return { x: num(rr.x) ?? 0, gap: num(rr.gap), a: channelValues(rr.a), b: channelValues(rr.b) };
    }),
    steering: { comparable: requiredBoolean(steering, "comparable", `section ${id} steering`), degreeDigits: num(steering.degree_digits), reason: str(steering.reason), basis: str(steering.basis), label: str(steering.label) },
  };
}

function comparison(raw: unknown): Comparison {
  const r = asRecord(raw);
  const id = required<string>(r, "id", "a comparison");
  const inspection = asRecord(r.inspection);
  const sections = asRecord(inspection.sections);
  const pairs = new Map<string, PassPair>();
  for (const [key, value] of Object.entries(sections)) pairs.set(key, pair(value, key));
  return {
    id,
    kind: str(r.kind) ?? "",
    title: str(r.title) ?? id,
    summary: str(r.summary) ?? "",
    deltaS: num(r.delta_s),
    eligibility: required<string>(r, "eligibility", `comparison ${id}`),
    paceComparable: requiredBoolean(r, "pace_comparable", `comparison ${id}`),
    paceUnusableReasons: strings(r.pace_unusable_reasons),
    baseline: cohort(r.baseline),
    compared: cohort(r.compared),
    metrics: asArray(r.metrics).map(metric),
    corners: asArray(r.corners).map((item) => {
      const c = asRecord(item);
      return {
        id: str(c.id) ?? "",
        label: str(c.label) ?? "",
        shortLabel: str(c.short_label),
        startPct: num(c.start_pct) ?? 0,
        endPct: num(c.end_pct) ?? 0,
        deltaS: num(c.delta_s),
        boundaryStatus: str(c.boundary_status),
        baselineCount: num(c.baseline_count) ?? 0,
        comparedCount: num(c.compared_count) ?? 0,
      };
    }),
    hypotheses: asArray(r.hypotheses).map((item) => {
      const h = asRecord(item);
      return { key: str(h.key) ?? "", label: str(h.label) ?? "", status: str(h.status) ?? "", supporting: strings(h.supporting), contradicting: strings(h.contradicting), unknown: strings(h.unknown) };
    }),
    actions: asArray(r.actions).map((item) => {
      const a = asRecord(item);
      return {
        priority: num(a.priority) ?? 99,
        title: str(a.title) ?? "",
        instruction: str(a.instruction) ?? "",
        limitation: str(a.limitation),
        lapNumbers: numbers(a.lap_numbers),
        startPct: num(a.start_pct),
        endPct: num(a.end_pct),
        signalKeys: strings(a.signal_keys),
        sectionId: str(a.section_id),
      };
    }),
    inspection: inspection.mode !== undefined ? { mode: str(inspection.mode) ?? "", poolLaps: numbers(inspection.pool_laps), pairs } : null,
  };
}

function run(raw: unknown): RunView {
  const r = asRecord(raw);
  const trend = asRecord(r.trend);
  const fit = asRecord(r.fit);
  const domain = numbers(fit.domain);
  return {
    runNumber: required<number>(r, "run_number", "a run"),
    plottedLapCount: num(r.plotted_lap_count) ?? 0,
    firstLap: num(r.first_lap) ?? 0,
    lastLap: num(r.last_lap) ?? 0,
    fitUnavailableReason: str(r.fit_unavailable_reason),
    trend: {
      method: str(trend.method),
      coveredLapCount: num(trend.covered_lap_count) ?? 0,
      segments: asArray(trend.segments).map((segment) => asArray(segment).map((p) => ({ lap: num(asRecord(p).lap) ?? 0, x: num(asRecord(p).x) ?? 0, y: num(asRecord(p).y) ?? 0 }))),
      gaps: asArray(trend.gaps).map((g) => ({ afterLap: num(asRecord(g).after_lap) ?? 0, beforeLap: num(asRecord(g).before_lap) ?? 0, reason: str(asRecord(g).reason) ?? "" })),
    },
    fit: Object.keys(fit).length
      ? {
          method: str(fit.method),
          eligibleCount: num(fit.eligible_count) ?? 0,
          slopeSPerGreenLap: required<number>(fit, "slope_s_per_green_lap", `run ${num(r.run_number) ?? "?"} fit`),
          domain: domain.length === 2 ? [domain[0]!, domain[1]!] : null,
          points: asArray(fit.points).map((p) => ({ x: num(asRecord(p).x) ?? 0, y: num(asRecord(p).y) ?? 0 })),
          laps: numbers(fit.laps),
        }
      : null,
  };
}

export function parseInvestigation(raw: unknown): Investigation {
  const r = asRecord(raw);
  if (Object.keys(r).length === 0) throw new InvestigationFormatError("race_investigation is empty");
  const schema = num(r.schema_version);
  if (schema === null) throw new InvestigationFormatError(`race_investigation is missing "schema_version"`);
  if (schema !== SUPPORTED_INVESTIGATION_SCHEMA) {
    throw new InvestigationFormatError(`race_investigation schema ${schema} is not supported (supported: ${SUPPORTED_INVESTIGATION_SCHEMA})`);
  }
  const comparisons = asArray(required<unknown>(r, "comparisons", "race_investigation")).map(comparison);
  const sections = asRecord(r.sections);
  const evolution = asRecord(r.track_evolution);
  return {
    status: str(r.status),
    summary: str(r.summary) ?? "",
    defaultComparisonId: str(r.default_comparison_id),
    limitations: strings(r.limitations),
    comparisons,
    laps: asArray(r.laps).map((item) => {
      const l = asRecord(item);
      return {
        lap: num(l.lap) ?? 0,
        lapTimeS: num(l.lap_time_s),
        position: num(l.position),
        flagState: str(l.flag_state) ?? "",
        eligible: requiredBoolean(l, "eligible", `lap ${num(l.lap) ?? "?"}`),
        exclusionReasons: strings(l.exclusion_reasons),
        runNumber: num(l.run_number),
        plotPace: requiredBoolean(l, "plot_pace", `lap ${num(l.lap) ?? "?"}`),
        greenExposure: num(l.green_exposure),
      };
    }),
    timeline: asArray(r.timeline).map((item) => {
      const t = asRecord(item);
      return { lap: num(t.lap) ?? 0, kind: str(t.kind) ?? "", label: str(t.label) ?? "", detail: str(t.detail) ?? "", value: num(t.value), unit: str(t.unit) };
    }),
    runs: asArray(r.runs).map(run),
    sections: {
      configurationKey: str(sections.configuration_key),
      unavailableReason: str(sections.unavailable_reason),
      sections: asArray(sections.sections).map((item) => {
        const s = asRecord(item);
        return { id: str(s.id) ?? "", label: str(s.label) ?? "", startPct: num(s.start_pct) ?? 0, endPct: num(s.end_pct) ?? 0, boundaryStatus: str(s.boundary_status), provenance: str(s.provenance) ?? "" };
      }),
    },
    trackEvolution: Object.keys(evolution).length ? { status: str(evolution.status), summary: str(evolution.summary) } : null,
    steeringBasis: typeof asRecord(r.steering).basis === "string" ? (asRecord(r.steering) as unknown as SteeringBasisData) : null,
  };
}

/** True only when the backend reports a usable, finite median pace change. */
export function hasPaceResult(c: Comparison): boolean {
  return c.paceComparable && c.deltaS !== null && Number.isFinite(c.deltaS);
}

/** Slower is a loss, faster a gain; a withheld result is neutral, never a gain. */
export function paceTone(c: Comparison): "neutral" | "loss" | "gain" {
  return !hasPaceResult(c) ? "neutral" : (c.deltaS as number) > 0 ? "loss" : "gain";
}

export function selectedComparison(inv: Investigation, id: string | null | undefined): Comparison | null {
  return inv.comparisons.find((c) => c.id === id) ?? inv.comparisons.find((c) => c.id === inv.defaultComparisonId) ?? inv.comparisons[0] ?? null;
}

/** Section to show: the requested one, else the lead action's, else the first corner, else one with a pass pair. */
export function selectedSection(inv: Investigation, comparisonValue: Comparison | null, id: string | null | undefined): Section | null {
  const sections = inv.sections.sections;
  const lead = comparisonValue?.actions.slice().sort((a, b) => a.priority - b.priority).find((action) => action.sectionId !== null)?.sectionId;
  return (
    sections.find((s) => s.id === id) ??
    sections.find((s) => s.id === lead) ??
    sections.find((s) => s.id === comparisonValue?.corners[0]?.id) ??
    sections.find((s) => comparisonValue?.inspection?.pairs.has(s.id)) ??
    sections[0] ??
    null
  );
}

import { channelAsFloat, type CcolChannel, type TraceTable } from "../ccol";
import { resolveSignal, steeringDeclaration, type ResolvedSignal, type SteeringDeclaration } from "../signals";
import { DEFAULT_BUDGETS, type RenderBudgets, type TraceCatalogData, type TraceLapData, type TraceSignalData } from "../types";
import { raceProgressColour } from "../../core/palette";
import { pointBudgetPerTrace, representativeIndices } from "./budget";

/**
 * Chart data model over one analysis's CCOL traces: signal columns in display units, per-lap colours,
 * value ranges, smoothing and path building. Ported from TelemetryWorkspace.razor:2263-2425 as pure
 * functions; every value is copied from the projection, the only derived series is the time delta.
 */

const RAD_TO_DEG = 180 / Math.PI;

export interface TraceData {
  analysisId: string;
  bins: number;
  laps: number[];
  lapIndex: ReadonlyMap<number, number>;
  lapMeta: ReadonlyMap<number, TraceLapData>;
  /** Lap fraction of each bin, the backend's bucket-centre `lap_pct` (bin i at (i + 0.5) / bins); shared by every lap. */
  x: Float64Array;
  catalog: TraceCatalogData;
  signals: ReadonlyMap<string, TraceSignalData>;
  steering: SteeringDeclaration;
  budgets: RenderBudgets;
  table: TraceTable;
  columns: Map<string, Float64Array | null>;
  /** The fastest clean lap of the race (the clean rule below), null when no complete lap is timed; magenta. */
  fastestLap: number | null;
  fastestLapTime: number;
  colors: ReadonlyMap<number, string>;
  /** Incident points per lap from the race view (setLapContext); empty until set. */
  incidentPoints: ReadonlyMap<number, number>;
  /** Runs for the calculated tire-wear row (setLapContext). */
  runs: readonly RunWearInput[];
  /** Sector cells, computed on first use. */
  sectors: ReadonlyMap<number, SectorCell[]> | null;
  /** Auxiliary channels found by catalog source key. */
  aux: { lapPct: CcolChannel | null; sessionTime: CcolChannel | null; latitude: CcolChannel | null; longitude: CcolChannel | null };
}

function findBySourceKey(catalog: TraceCatalogData, table: TraceTable, keys: string[]): CcolChannel | null {
  for (const signal of catalog.signals) {
    if (keys.includes(signal.source_key)) {
      const channel = table.channels.get(signal.id);
      if (channel) return channel;
    }
  }
  return null;
}

export function createTraceData(catalog: TraceCatalogData, table: TraceTable): TraceData {
  const bins = table.bins;
  const lapIndex = new Map(table.laps.map((lap, index) => [lap, index] as const));
  const lapMeta = new Map<number, TraceLapData>();
  for (const lap of catalog.laps) if (!lapMeta.has(lap.lap)) lapMeta.set(lap.lap, lap);
  const aux = {
    lapPct: findBySourceKey(catalog, table, ["lap_pct"]),
    sessionTime: findBySourceKey(catalog, table, ["session_time_s", "session_time"]),
    latitude: findBySourceKey(catalog, table, ["latitude"]),
    longitude: findBySourceKey(catalog, table, ["longitude"]),
  };
  const x = new Float64Array(bins);
  const pct = aux.lapPct ? channelAsFloat(aux.lapPct) : null;
  for (let bin = 0; bin < bins; bin++) {
    let value = Number.NaN;
    if (pct) for (let lap = 0; lap < table.laps.length && !Number.isFinite(value); lap++) value = pct[lap * bins + bin]!;
    x[bin] = Number.isFinite(value) ? value : (bin + 0.5) / bins;
  }
  const data: TraceData = {
    analysisId: table.analysisId,
    bins,
    laps: table.laps,
    lapIndex,
    lapMeta,
    x,
    catalog,
    signals: new Map(catalog.signals.map((signal) => [signal.id, signal] as const)),
    steering: steeringDeclaration(catalog.steering_basis),
    budgets: { ...DEFAULT_BUDGETS, ...catalog.budgets },
    table,
    columns: new Map(),
    fastestLap: null,
    fastestLapTime: Number.POSITIVE_INFINITY,
    colors: new Map(),
    incidentPoints: new Map(),
    runs: [],
    sectors: null,
    aux,
  };
  applyFastest(data);
  return data;
}

export function lapColor(data: TraceData, lap: number): string {
  return data.colors.get(lap) ?? raceProgressColour(0, 1);
}

export function resolve(data: TraceData, id: string): ResolvedSignal | null {
  return resolveSignal(id, data.signals, data.steering);
}

/** Display values of a signal for every lap and bin (`laps x bins`, NaN missing); null when not recorded. */
export function signalColumn(data: TraceData, id: string): Float64Array | null {
  const cached = data.columns.get(id);
  if (cached !== undefined) return cached;
  let out: Float64Array | null = null;
  const signal = resolve(data, id);
  const channel = data.table.channels.get(id);
  if (id === "tire-wear") {
    out = tireWearColumn(data);
  } else if (signal && channel) {
    const raw = channelAsFloat(channel);
    if (signal.radians) {
      out = new Float64Array(raw.length);
      for (let index = 0; index < raw.length; index++) out[index] = raw[index]! * RAD_TO_DEG;
    } else {
      out = raw;
    }
  }
  data.columns.set(id, out);
  return out;
}

/**
 * Time delta against the zero lap, `laps x bins` (NaN where either lap lacks session time): this lap's time since the first bin
 * both laps cover minus the zero lap's time since that bin (audit T10, `lap_reference.py`). Positive is slower.
 */
export function deltaColumn(data: TraceData, referenceLap: number | null): Float64Array | null {
  const key = `delta:${referenceLap ?? "none"}`;
  const cached = data.columns.get(key);
  if (cached !== undefined) return cached;
  let out: Float64Array | null = null;
  const channel = data.aux.sessionTime;
  const ref = referenceLap !== null ? data.lapIndex.get(referenceLap) : undefined;
  if (channel && ref !== undefined) {
    const time = channelAsFloat(channel);
    const bins = data.bins;
    out = new Float64Array(time.length).fill(Number.NaN);
    for (let lap = 0; lap < data.laps.length; lap++) {
      let anchor = -1;
      for (let bin = 0; bin < bins && anchor < 0; bin++) {
        if (Number.isFinite(time[lap * bins + bin]!) && Number.isFinite(time[ref * bins + bin]!)) anchor = bin;
      }
      if (anchor < 0) continue;
      const lapStart = time[lap * bins + anchor]!;
      const referenceStart = time[ref * bins + anchor]!;
      for (let bin = anchor; bin < bins; bin++) {
        out[lap * bins + bin] = (time[lap * bins + bin]! - lapStart) - (time[ref * bins + bin]! - referenceStart);
      }
    }
  }
  data.columns.set(key, out);
  return out;
}

/** The column the chart plots for `id` under the given delta reference. */
export function plotColumn(data: TraceData, id: string, referenceLap: number | null): Float64Array | null {
  return id === "delta" ? deltaColumn(data, referenceLap) : signalColumn(data, id);
}

export function hasSignalData(data: TraceData, id: string, lapIndices: readonly number[], referenceLap: number | null): boolean {
  const column = plotColumn(data, id, referenceLap);
  if (!column) return false;
  const bins = data.bins;
  for (const lap of lapIndices) {
    for (let bin = 0; bin < bins; bin++) if (Number.isFinite(column[lap * bins + bin]!)) return true;
  }
  return false;
}

export function signalRecordedInRace(data: TraceData, id: string): boolean {
  const column = id === "delta" ? deltaColumn(data, data.laps[0] ?? null) : signalColumn(data, id);
  if (!column) return false;
  for (let index = 0; index < column.length; index++) if (Number.isFinite(column[index]!)) return true;
  return false;
}

/** SmoothedValue: step signals pass through; others blend each bin with its neighbours (0.10 pedals, 0.16 otherwise). */
export function smoothedValue(column: Float64Array, lapIndex: number, bin: number, bins: number, id: string, step: boolean): number {
  const base = lapIndex * bins;
  const current = column[base + bin]!;
  if (!Number.isFinite(current) || step) return current;
  const neighbor = id === "throttle" || id === "brake" ? 0.1 : 0.16;
  const centerWeight = 1 - neighbor * 2;
  let sum = current * centerWeight;
  let weight = centerWeight;
  if (bin > 0) {
    const previous = column[base + bin - 1]!;
    if (Number.isFinite(previous)) { sum += previous * neighbor; weight += neighbor; }
  }
  if (bin + 1 < bins) {
    const next = column[base + bin + 1]!;
    if (Number.isFinite(next)) { sum += next * neighbor; weight += neighbor; }
  }
  return sum / weight;
}

export interface Range {
  min: number;
  max: number;
}

/** SignalRange: pedals 0-1, on/off 0-1, symmetric for delta, steering, slip and yaw, tight otherwise. */
export function signalRange(data: TraceData, id: string, lapIndices: readonly number[], referenceLap: number | null): Range {
  const column = plotColumn(data, id, referenceLap);
  const signal = resolve(data, id);
  const bins = data.bins;
  let min = Number.POSITIVE_INFINITY;
  let max = Number.NEGATIVE_INFINITY;
  let magnitude = 0;
  if (column) {
    for (const lap of lapIndices) {
      for (let bin = 0; bin < bins; bin++) {
        const value = column[lap * bins + bin]!;
        if (!Number.isFinite(value)) continue;
        if (value < min) min = value;
        if (value > max) max = value;
        magnitude = Math.max(magnitude, Math.abs(value));
      }
    }
  }
  if (!Number.isFinite(min)) return { min: -0.5, max: 0.5 };
  if (id === "throttle" || id === "brake") return { min: 0, max: 1 };
  if (signal?.unit === "on / off") return { min: 0, max: 1 };
  if (id === "tire-wear") return { min: 0, max: Math.max(0.01, max * 1.05) };
  if (signal?.unit === "position") return { min: Math.max(0, min - 0.5), max: max + 0.5 };
  if (id === "delta" || id === "steering" || id === "steering-wheel-angle" || id === "slip" || id === "yaw") {
    // Road-wheel steering is about a twelfth of the wheel angle: the floor is 1/12 degree whatever the car's ratio (audit T13).
    const floor = id === "delta" ? 0.05 : id === "steering" ? (data.steering.roadWheel ? 1 / 12 : 1) : 1;
    const m = Math.max(floor, magnitude);
    return { min: -m, max: m };
  }
  if (Math.abs(max - min) < 0.0001) return { min: min - 0.5, max: max + 0.5 };
  return { min, max };
}

export interface ChartPathGroup {
  key: string;
  laps: number[];
  color: string;
  d: string;
  spotlight: boolean;
  strokeWidth: number;
  opacity: number;
}

export function traceStrokeWidth(spotlightLap: number | null, lap: number, signalIndex: number): number {
  if (spotlightLap === null) return signalIndex === 0 ? 1.35 : 1.05;
  if (spotlightLap === lap) return signalIndex === 0 ? 2.35 : 1.8;
  return signalIndex === 0 ? 1.15 : 0.95;
}

export function traceOpacity(spotlightLap: number | null, lap: number, signalIndex: number): number {
  if (spotlightLap === null) return signalIndex === 0 ? 1 : 0.72;
  if (spotlightLap === lap) return signalIndex === 0 ? 1 : 0.82;
  return signalIndex === 0 ? 0.42 : 0.3;
}

export interface RowFrame {
  plotLeft: number;
  plotWidth: number;
  top: number;
  plotHeight: number;
}

/** One lap's path for a signal inside a row frame (TracePath). Empty string when no bin has a value. */
export function lapPath(
  data: TraceData,
  column: Float64Array,
  lap: number,
  signalId: string,
  step: boolean,
  range: Range,
  frame: RowFrame,
  pointBudget: number,
): string {
  const lapIndex = data.lapIndex.get(lap);
  if (lapIndex === undefined) return "";
  const bins = data.bins;
  const indices = representativeIndices(bins, pointBudget);
  const span = range.max - range.min || 1;
  const parts: string[] = [];
  let started = false;
  for (const bin of indices) {
    const value = smoothedValue(column, lapIndex, bin, bins, signalId, step);
    if (!Number.isFinite(value)) {
      started = false;
      continue;
    }
    const px = frame.plotLeft + data.x[bin]! * frame.plotWidth;
    const py = frame.top + frame.plotHeight - 4 - ((value - range.min) / span) * (frame.plotHeight - 8);
    parts.push(`${started ? "L" : "M"}${px.toFixed(1)},${py.toFixed(1)}`);
    started = true;
  }
  return parts.join(" ");
}

/**
 * Path groups for one signal of one row: laps sharing a colour share one `<path>` so the DOM stays
 * bounded by the colour budget however many laps are selected (ChartTracePaths).
 */
export function chartPathGroups(
  data: TraceData,
  signalId: string,
  signalIndex: number,
  selectedLaps: readonly number[],
  spotlightLap: number | null,
  referenceLap: number | null,
  frame: RowFrame,
  range: Range,
): ChartPathGroup[] {
  const column = plotColumn(data, signalId, referenceLap);
  const signal = resolve(data, signalId);
  if (!column || !signal) return [];
  const ordered = selectedLaps.slice().sort((a, b) => (spotlightLap === a ? 1 : 0) - (spotlightLap === b ? 1 : 0) || a - b);
  const groups = new Map<string, ChartPathGroup>();
  for (const lap of ordered) {
    const index = data.lapIndex.get(lap);
    if (index === undefined) continue;
    const focused = spotlightLap === lap;
    const budget = pointBudgetPerTrace(ordered.length, frame.plotWidth, focused, data.budgets);
    const d = lapPath(data, column, lap, signalId, signal.step, range, frame, budget);
    if (!d) continue;
    const color = lapColor(data, lap);
    const key = `${color}|${focused ? 1 : 0}`;
    let group = groups.get(key);
    if (!group) {
      group = {
        key,
        laps: [],
        color,
        d: "",
        spotlight: focused,
        strokeWidth: traceStrokeWidth(spotlightLap, lap, signalIndex),
        opacity: traceOpacity(spotlightLap, lap, signalIndex),
      };
      groups.set(key, group);
    }
    group.laps.push(lap);
    group.d = group.d ? `${group.d} ${d}` : d;
  }
  return [...groups.values()].sort((a, b) => Number(a.spotlight) - Number(b.spotlight) || (a.color < b.color ? -1 : a.color > b.color ? 1 : 0));
}

export interface AggregateSeries {
  speed: Float64Array;
  throttle: Float64Array;
  brake: Float64Array;
  delta: Float64Array;
}

/** Mean over every selected lap at each bin, for the map readout (all selected laps, not only the detailed ones). */
export function aggregateSeries(data: TraceData, lapIndices: readonly number[], referenceLap: number | null): AggregateSeries {
  const bins = data.bins;
  const make = (column: Float64Array | null): Float64Array => {
    const out = new Float64Array(bins).fill(Number.NaN);
    if (!column) return out;
    for (let bin = 0; bin < bins; bin++) {
      let sum = 0;
      let count = 0;
      for (const lap of lapIndices) {
        const value = column[lap * bins + bin]!;
        if (Number.isFinite(value)) { sum += value; count += 1; }
      }
      if (count) out[bin] = sum / count;
    }
    return out;
  };
  return {
    speed: make(signalColumn(data, "speed")),
    throttle: make(signalColumn(data, "throttle")),
    brake: make(signalColumn(data, "brake")),
    delta: make(deltaColumn(data, referenceLap)),
  };
}


/*
 * Lap rules. One definition of "clean" (the documented 0.14.1 rule, orchestrator ruling on audit T7): complete, green, not a
 * pit-in or pit-out lap, no incident points and not off track, whatever the backend's comparison screening says. The zero lap,
 * the fastest lap, Best three, the Clean filter and the sector colours all use it. The backend's screening only feeds the
 * Excl chip and its reasons. A partial lap is never a baseline.
 */

const CAUTION_WORDS = ["yellow", "caution"];

function flagText(value: unknown): string {
  return typeof value === "string" ? value.toLowerCase() : "";
}

export function lapFlags(meta: TraceLapData): string[] {
  const states = Array.isArray(meta.flag_states) ? meta.flag_states.map(flagText) : [];
  return [flagText(meta.flag_state), ...states].filter((flag) => flag.length > 0);
}

/** Caution on the lap from its flag states or caution time; completeness plays no part (audit T9). */
export function isCautionLap(meta: TraceLapData): boolean {
  const caution = typeof meta.caution_fraction === "number" ? meta.caution_fraction : 0;
  return caution > 0.001 || lapFlags(meta).some((flag) => CAUTION_WORDS.some((word) => flag.includes(word)));
}

/**
 * A complete lap: not marked incomplete and without the backend's `partial` exclusion. The projector writes `partial` whenever
 * `complete` is not true, so a lap that omits `complete` but carries `partial` is partial (review finding 1).
 */
export function isCompleteLap(meta: TraceLapData): boolean {
  return meta.complete !== false && !(Array.isArray(meta.exclusion_reasons) && meta.exclusion_reasons.includes("partial"));
}

/** Green lap (IsGreenLap): complete and without caution time or yellow flags. */
export function isGreenLap(meta: TraceLapData): boolean {
  return isCompleteLap(meta) && !isCautionLap(meta);
}

/** A pit-in or pit-out lap: time on pit road or a pit entry or exit on the lap. */
export function isPitLap(meta: TraceLapData): boolean {
  const pit = typeof meta.pit_time_s === "number" ? meta.pit_time_s : 0;
  const record = meta as Record<string, unknown>;
  return pit > 0 || record.pit_entry === true || record.pit_exit === true;
}

export function isOffTrackLap(meta: TraceLapData): boolean {
  return Array.isArray(meta.exclusion_reasons) && meta.exclusion_reasons.includes("off_track");
}

/** Clean lap: green, not a pit lap, no incident points, not off track. The backend's comparison screening plays no part. */
export function isCleanLap(meta: TraceLapData, incidentPoints: number): boolean {
  return isGreenLap(meta) && !isPitLap(meta) && incidentPoints === 0 && !isOffTrackLap(meta);
}

export function hasUsableLapTime(meta: TraceLapData | undefined): boolean {
  return typeof meta?.lap_time_s === "number" && Number.isFinite(meta.lap_time_s) && meta.lap_time_s > 0;
}

/** The backend's screening: the lap is offered for coaching comparison (the Excl chip is its inverse). */
export function isComparable(meta: TraceLapData): boolean {
  return meta.comparison_eligible !== false && isCompleteLap(meta) && !(Array.isArray(meta.exclusion_reasons) && meta.exclusion_reasons.length > 0);
}

export interface SelectionContext {
  incidentPoints: ReadonlyMap<number, number>;
}

function byTime(a: TraceLapData, b: TraceLapData): number {
  return (a.lap_time_s as number) - (b.lap_time_s as number) || a.lap - b.lap;
}

/** Complete timed laps among `metas`, fastest first: clean ones when any is clean, else every complete timed lap. */
function cleanPool(metas: readonly (TraceLapData | undefined)[], incidents: ReadonlyMap<number, number>): TraceLapData[] {
  const timed = metas.filter((meta): meta is TraceLapData => meta !== undefined && isCompleteLap(meta) && hasUsableLapTime(meta));
  const clean = timed.filter((meta) => isCleanLap(meta, incidents.get(meta.lap) ?? 0));
  return (clean.length ? clean : timed).sort(byTime);
}

function baselinePool(data: TraceData, laps: readonly number[], incidents: ReadonlyMap<number, number>): TraceLapData[] {
  return cleanPool(laps.map((lap) => data.lapMeta.get(lap)), incidents);
}

/** The fastest lap of any lap list (trace catalog or race view laps) under the same rule; null without a complete timed lap. */
export function fastestCleanLap(laps: readonly TraceLapData[], incidents: ReadonlyMap<number, number>): number | null {
  return cleanPool(laps, incidents)[0]?.lap ?? null;
}

/**
 * The time-delta zero line: the fastest clean lap of the selection; when none is clean, the fastest complete timed lap; null when
 * the selection holds no complete timed lap. Never chosen by lap number, never a partial lap.
 */
export function zeroLapFor(data: TraceData, selectedLaps: readonly number[], incidents: ReadonlyMap<number, number> = data.incidentPoints): number | null {
  return baselinePool(data, selectedLaps, incidents)[0]?.lap ?? null;
}

/** The zero lap under the same rule (the name the cursor and the first tests use). */
export const referenceLapFor = zeroLapFor;

/** "Best three": the three fastest clean laps; when no lap is clean, the three fastest complete timed laps. */
export function bestThree(data: TraceData, context: SelectionContext = { incidentPoints: data.incidentPoints }): number[] {
  return baselinePool(data, data.laps, context.incidentPoints).slice(0, 3).map((meta) => meta.lap);
}

export function fastestLap(data: TraceData, context: SelectionContext = { incidentPoints: data.incidentPoints }): number | null {
  return baselinePool(data, data.laps, context.incidentPoints)[0]?.lap ?? null;
}

/**
 * Sets the race context the lap rules need (incident points per lap, the runs for the tire-wear row) and recomputes the fastest
 * lap and its colour. Idempotent for the same inputs.
 */
export function setLapContext(data: TraceData, incidents: ReadonlyMap<number, number>, runs: readonly RunWearInput[] = data.runs): void {
  const sameIncidents = data.incidentPoints.size === incidents.size && [...incidents].every(([lap, points]) => data.incidentPoints.get(lap) === points);
  if (!sameIncidents) {
    data.incidentPoints = new Map(incidents);
    applyFastest(data);
    data.sectors = null;
  }
  if (runs !== data.runs) {
    data.runs = runs;
    data.columns.delete("tire-wear");
  }
}

export function applyFastest(data: TraceData): void {
  const fastest = fastestLap(data);
  const time = fastest !== null ? data.lapMeta.get(fastest)?.lap_time_s : undefined;
  data.fastestLap = fastest;
  data.fastestLapTime = typeof time === "number" ? time : Number.POSITIVE_INFINITY;
  data.colors = progressColors(data.laps, data.budgets.max_color_groups);
}

/**
 * Lap colour is race progress (web round 2): a start-to-finish gradient through the theme's `identity.progress` stops over every lap
 * of the phase, so no filter or selection ever recolours a lap. The gradient is cut into at most `bands` steps (the catalog's
 * `max_color_groups`) so the chart's paths stay bounded by colour groups, not by lap count; neighbouring laps may share a step.
 */
export function progressColors(laps: readonly number[], bands = Number.POSITIVE_INFINITY): Map<number, string> {
  const sorted = [...new Set(laps)].sort((a, b) => a - b);
  const steps = Math.max(1, Math.min(sorted.length, Math.floor(bands)));
  return new Map(sorted.map((lap, index) => {
    const step = sorted.length <= 1 ? 0 : Math.round((index * (steps - 1)) / (sorted.length - 1));
    return [lap, raceProgressColour(step, steps)] as const;
  }));
}

/** The first and last lap of the phase with their progress colours, for the rail's key. Null without laps. */
export function lapProgressEnds(data: TraceData): { first: { lap: number; color: string }; last: { lap: number; color: string } } | null {
  const sorted = data.laps.slice().sort((a, b) => a - b);
  if (sorted.length === 0) return null;
  const first = sorted[0]!;
  const last = sorted[sorted.length - 1]!;
  return { first: { lap: first, color: lapColor(data, first) }, last: { lap: last, color: lapColor(data, last) } };
}

/** Sector boundaries: the catalog's start fractions inside [0, 1), distinct and ascending (SectorStarts). */
export function sectorStarts(data: TraceData): number[] {
  const raw = Array.isArray(data.catalog.sector_start_pcts) ? data.catalog.sector_start_pcts : [];
  return [...new Set(raw.filter((value) => typeof value === "number" && value >= 0 && value < 1))].sort((a, b) => a - b);
}

/** How far past the lap's first or last recorded bin a sector boundary may sit, in bins (the contract's `sector_times_s` rule). */
const BOUNDARY_COVERAGE_BINS = 2;

/**
 * Session time at a lap fraction (TimeAtPercent): linear between recorded bins, held at the first and last recorded bin only within
 * two bins of it; null beyond (a lap that starts mid-lap or stops short), so missing coverage never reads as a 0.000 s sector.
 */
export function timeAtFraction(data: TraceData, time: Float64Array, lapIndex: number, target: number): number | null {
  const bins = data.bins;
  const reach = BOUNDARY_COVERAGE_BINS / Math.max(1, bins) + 1e-9;
  let previous = -1;
  for (let bin = 0; bin < bins; bin++) {
    const value = time[lapIndex * bins + bin]!;
    if (!Number.isFinite(value)) continue;
    const x = data.x[bin]!;
    if (previous < 0 && target <= x) return x - target <= reach ? value : null;
    if (previous >= 0 && x >= target) {
      const px = data.x[previous]!;
      const pv = time[lapIndex * bins + previous]!;
      const span = x - px;
      return span <= 1e-6 ? value : pv + ((value - pv) * (target - px)) / span;
    }
    previous = bin;
  }
  if (previous < 0) return null;
  return target - data.x[previous]! <= reach ? time[lapIndex * bins + previous]! : null;
}

export type SectorState = "sb" | "pb" | "n" | "nc" | "na";
export interface SectorCell {
  state: SectorState;
  seconds: number | null;
}

/**
 * Sector durations (the server's `sector_times_s`, else the recorded session clock at the sector boundaries,
 * TelemetryWorkspace.SectorDuration), and their
 * colours (SectorState): session best among clean laps, personal best (faster than every earlier clean lap), slower, lap not
 * clean, not recorded. Computed once per analysis and incident context.
 */
export function sectorCells(data: TraceData): ReadonlyMap<number, SectorCell[]> {
  if (data.sectors) return data.sectors;
  const starts = sectorStarts(data);
  const out = new Map<number, SectorCell[]>();
  if (starts.length < 2) {
    data.sectors = out;
    return out;
  }
  const channel = data.aux.sessionTime;
  const time = channel ? channelAsFloat(channel) : null;
  const durations = new Map<number, (number | null)[]>();
  for (const lap of data.laps) {
    // The projector's per-lap sector times (`TraceLap.sector_times_s`) when it sends one per boundary; else the session clock.
    const served = (data.lapMeta.get(lap) as Record<string, unknown> | undefined)?.sector_times_s;
    if (Array.isArray(served) && served.length === starts.length) {
      durations.set(lap, served.map((value) => (typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null)));
      continue;
    }
    const index = data.lapIndex.get(lap)!;
    durations.set(lap, starts.map((start, sector) => {
      if (!time) return null;
      const end = sector + 1 < starts.length ? starts[sector + 1]! : 1;
      const a = timeAtFraction(data, time, index, start);
      const b = timeAtFraction(data, time, index, end);
      return a !== null && b !== null && b >= a ? b - a : null;
    }));
  }
  const clean = (lap: number): boolean => {
    const meta = data.lapMeta.get(lap);
    return meta !== undefined && isCleanLap(meta, data.incidentPoints.get(lap) ?? 0);
  };
  const ordered = data.laps.slice().sort((a, b) => a - b);
  const cleanLaps = ordered.filter(clean);
  const sessionBest = starts.map((_, sector) => Math.min(Number.POSITIVE_INFINITY, ...cleanLaps.map((lap) => durations.get(lap)![sector] ?? Number.POSITIVE_INFINITY)));
  const priorBest = starts.map(() => Number.POSITIVE_INFINITY);
  for (const lap of ordered) {
    const values = durations.get(lap)!;
    const isClean = clean(lap);
    out.set(lap, values.map((seconds, sector): SectorCell => {
      if (seconds === null) return { state: "na", seconds };
      if (!isClean) return { state: "nc", seconds };
      if (Math.abs(seconds - sessionBest[sector]!) < 0.0005) return { state: "sb", seconds };
      return { state: seconds < priorBest[sector]! ? "pb" : "n", seconds };
    }));
    if (isClean) values.forEach((seconds, sector) => { if (seconds !== null && seconds < priorBest[sector]!) priorBest[sector] = seconds; });
  }
  data.sectors = out;
  return out;
}

/** What the tire-wear row needs from one backend run (BackendRun). */
export interface RunWearInput {
  runNumber: number;
  laps: readonly number[];
  /** Worst observed wear at the end of the run, percent (100 - tire_observation.lowest_remaining_percent). */
  observedWearPercent: number | null;
}

/**
 * Calculated tire wear (CalculatedTireWear.cs): each lap's accumulated tire-stress proxy, scaled so a run's laps add up to the
 * worst wear observed at its end (the session factor for runs without an observation). Null without the stress channel or any
 * observed wear.
 */
export function tireWearColumn(data: TraceData): Float64Array | null {
  const stressChannel = data.table.channels.get("tire-stress");
  if (!stressChannel) return null;
  const stress = channelAsFloat(stressChannel);
  const bins = data.bins;
  const cumulative = new Float64Array(stress.length).fill(Number.NaN);
  const totals = new Map<number, number>();
  for (const lap of data.laps) {
    const index = data.lapIndex.get(lap)!;
    let sum = 0;
    let has = false;
    let last = 0;
    for (let bin = 0; bin < bins; bin++) {
      const current = stress[index * bins + bin]!;
      const value = Number.isFinite(current) ? Math.max(0, current) : null;
      if (bin === 0) {
        if (value !== null) { sum = value * Math.min(1, Math.max(0, data.x[0]!)); has = true; }
      } else {
        const before = stress[index * bins + bin - 1]!;
        const previous = Number.isFinite(before) ? Math.max(0, before) : null;
        if (value !== null || previous !== null) {
          const segment = value !== null && previous !== null ? (value + previous) / 2 : (value ?? previous ?? 0);
          sum += segment * Math.max(0, data.x[bin]! - data.x[bin - 1]!);
          has = true;
        }
      }
      if (has) { cumulative[index * bins + bin] = sum; last = sum; }
    }
    totals.set(lap, has ? last : 0);
  }
  const factors = new Map<number, number>();
  let wearTotal = 0;
  let loadTotal = 0;
  for (const run of data.runs) {
    const load = run.laps.reduce((acc, lap) => acc + (totals.get(lap) ?? 0), 0);
    if (run.observedWearPercent === null || run.observedWearPercent <= 0 || load <= 0) continue;
    factors.set(run.runNumber, run.observedWearPercent / load);
    wearTotal += run.observedWearPercent;
    loadTotal += load;
  }
  if (loadTotal <= 0) return null;
  const sessionFactor = wearTotal / loadTotal;
  const runOf = new Map<number, number>();
  for (const run of data.runs) for (const lap of run.laps) if (!runOf.has(lap)) runOf.set(lap, run.runNumber);
  const out = new Float64Array(stress.length).fill(Number.NaN);
  for (const lap of data.laps) {
    if ((totals.get(lap) ?? 0) <= 0) continue;
    const index = data.lapIndex.get(lap)!;
    const run = runOf.get(lap);
    const factor = run !== undefined && factors.has(run) ? factors.get(run)! : sessionFactor;
    for (let bin = 0; bin < bins; bin++) out[index * bins + bin] = cumulative[index * bins + bin]! * factor;
  }
  return out;
}

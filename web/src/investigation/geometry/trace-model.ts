import { channelAsFloat, type CcolChannel, type TraceTable } from "../ccol";
import { resolveSignal, steeringDeclaration, type ResolvedSignal, type SteeringDeclaration } from "../signals";
import { DEFAULT_BUDGETS, type RenderBudgets, type TraceCatalogData, type TraceLapData, type TraceSignalData } from "../types";
import { FASTEST_LAP_COLOR, pointBudgetPerTrace, representativeIndices, TRACE_PALETTE } from "./budget";

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
  fastestLapTime: number;
  colors: ReadonlyMap<number, string>;
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
  const times = table.laps
    .map((lap) => lapMeta.get(lap))
    .filter((lap) => lap && lap.comparison_eligible && typeof lap.lap_time_s === "number" && lap.lap_time_s > 0)
    .map((lap) => lap!.lap_time_s as number);
  const fastest = times.length ? Math.min(...times) : Number.POSITIVE_INFINITY;
  const sortedLaps = table.laps.slice().sort((a, b) => a - b);
  const colors = new Map<number, string>();
  sortedLaps.forEach((lap, index) => {
    const time = lapMeta.get(lap)?.lap_time_s;
    colors.set(lap, typeof time === "number" && Math.abs(time - fastest) < 0.0001 ? FASTEST_LAP_COLOR : TRACE_PALETTE[index % TRACE_PALETTE.length]!);
  });
  return {
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
    fastestLapTime: fastest,
    colors,
    aux,
  };
}

export function lapColor(data: TraceData, lap: number): string {
  return data.colors.get(lap) ?? TRACE_PALETTE[0]!;
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
  if (signal && channel) {
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

/** Time delta against the reference lap, `laps x bins` (NaN where either lap lacks session time). */
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
    const start = (lap: number): number => {
      for (let bin = 0; bin < bins; bin++) {
        const value = time[lap * bins + bin]!;
        if (Number.isFinite(value)) return value;
      }
      return Number.NaN;
    };
    const referenceStart = start(ref);
    out = new Float64Array(time.length).fill(Number.NaN);
    for (let lap = 0; lap < data.laps.length; lap++) {
      const lapStart = start(lap);
      for (let bin = 0; bin < bins; bin++) {
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
  if (id === "delta" || id === "steering" || id === "steering-wheel-angle" || id === "slip" || id === "yaw") {
    // Road-wheel steering is about a twelfth of the wheel angle: a 1 degree floor would flatten it.
    const floor = id === "delta" ? 0.05 : id === "steering" ? (data.steering.roadWheel && data.steering.ratio ? 1 / data.steering.ratio : 1) : 1;
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

/** Reference lap for the delta: the fastest clean selected lap, else the first selected (ReferenceTrace). */
export function referenceLapFor(data: TraceData, selectedLaps: readonly number[]): number | null {
  const ordered = selectedLaps.slice().sort((a, b) => a - b);
  let best: number | null = null;
  let bestTime = Number.POSITIVE_INFINITY;
  for (const lap of ordered) {
    const meta = data.lapMeta.get(lap);
    if (!meta || !isCleanLap(meta, 0)) continue;
    const time = meta.lap_time_s;
    if (typeof time === "number" && time < bestTime) { best = lap; bestTime = time; }
  }
  return best ?? ordered[0] ?? null;
}

const CAUTION_WORDS = ["yellow", "caution"];

function flagText(value: unknown): string {
  return typeof value === "string" ? value.toLowerCase() : "";
}

function lapFlags(meta: TraceLapData): string[] {
  const states = Array.isArray(meta.flag_states) ? meta.flag_states.map(flagText) : [];
  return [flagText(meta.flag_state), ...states];
}

/** Green lap (IsGreenLap): complete and without caution time or yellow flags. */
export function isGreenLap(meta: TraceLapData): boolean {
  const caution = typeof meta.caution_fraction === "number" ? meta.caution_fraction : 0;
  return meta.complete !== false && caution <= 0.001 && !lapFlags(meta).some((flag) => CAUTION_WORDS.some((word) => flag.includes(word)));
}

/** Clean lap (IsClean): green, no pit time and no incident points. */
export function isCleanLap(meta: TraceLapData, incidentPoints: number): boolean {
  const pit = typeof meta.pit_time_s === "number" ? meta.pit_time_s : 0;
  return isGreenLap(meta) && pit <= 0 && incidentPoints === 0 && meta.comparison_eligible !== false;
}

export function hasUsableLapTime(meta: TraceLapData | undefined): boolean {
  return typeof meta?.lap_time_s === "number" && Number.isFinite(meta.lap_time_s) && meta.lap_time_s > 0;
}

export interface SelectionContext {
  incidentPoints: ReadonlyMap<number, number>;
}

function byTime(a: TraceLapData, b: TraceLapData): number {
  return (a.lap_time_s as number) - (b.lap_time_s as number) || a.lap - b.lap;
}

/** "Best three": the three fastest clean laps, else the three fastest laps with a time. */
export function bestThree(data: TraceData, context: SelectionContext): number[] {
  const timed = data.laps.map((lap) => data.lapMeta.get(lap)).filter((meta): meta is TraceLapData => hasUsableLapTime(meta));
  const clean = timed.filter((meta) => isCleanLap(meta, context.incidentPoints.get(meta.lap) ?? 0)).sort(byTime).slice(0, 3);
  return (clean.length ? clean : timed.sort(byTime).slice(0, 3)).map((meta) => meta.lap);
}

export function fastestLap(data: TraceData, context: SelectionContext): number | null {
  const timed = data.laps.map((lap) => data.lapMeta.get(lap)).filter((meta): meta is TraceLapData => hasUsableLapTime(meta));
  const clean = timed.filter((meta) => isCleanLap(meta, context.incidentPoints.get(meta.lap) ?? 0)).sort(byTime);
  return (clean[0] ?? timed.sort(byTime)[0])?.lap ?? null;
}

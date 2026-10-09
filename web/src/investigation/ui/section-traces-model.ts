import { channelAsFloat } from "../ccol";
import { cardMetrics, type ChartMetrics, type CursorConfig, type CursorRow, type CursorSignal, type CursorTrace } from "../cursor/cursor-config";
import { tooltipCapacityFor } from "../cursor/cursor-core";
import { mapPointAt, type MapPoint } from "../geometry/map-model";
import { resolve, signalColumn, smoothedValue, type Range, type TraceData } from "../geometry/trace-model";

/**
 * Sections telemetry (backlog #64, gap ledger G6): the chosen section's laps over its approach and its distance. Every value is a
 * trace bin the backend reported; this module only picks the bins of the window, places them across the plot and builds the
 * cursor's configuration over them. The one derived series is the time gap to the median lap, the Traces delta row's arithmetic
 * (`deltaColumn`): each lap's session time since the first window bin both laps cover, less the median lap's.
 *
 * The window is the section with its approach in front: the backend's lead-in (`race_inspection.approach_start`), the section's own
 * length clamped to 3-8 % of a lap, never before the previous section's start or the recorded lap start. The SectionTraces cursor
 * runs across the window; the view's fraction (`fractionRef`, the map marker) stays the position across the section, negative in
 * the approach.
 */

/** The channels the chart loads: the five drawn as recorded and the session time the gap row is derived from. */
export const SECTION_SIGNALS = ["speed", "session-time", "throttle", "brake", "steering", "slip"] as const;

/** The six rows drawn, in this order: speed, time gap to the median lap, throttle, brake, steering and body slip. */
export const SECTION_ROWS = ["speed", "gap", "throttle", "brake", "steering", "slip"] as const;
export type SectionRowId = (typeof SECTION_ROWS)[number];

/** `race_inspection.APPROACH_MIN_FRACTION` and `APPROACH_MAX_FRACTION`: the lead-in's bounds, in lap fraction. */
export const APPROACH_MIN_FRACTION = 0.03;
export const APPROACH_MAX_FRACTION = 0.08;

export interface SectionSpan {
  startPct: number;
  endPct: number;
  /** The previous section's start (the approach never reaches back past it); the recorded lap start bounds it when absent. */
  previousStartPct?: number | null;
}

/** Lap distance of the section (wraps past start/finish when the end is before the start). */
export function spanOf(section: SectionSpan): number {
  const span = section.endPct - section.startPct;
  return span > 0 ? span : span + 1;
}

/** Position across the section of a lap fraction: 0 at its start, 1 at its end, above 1 outside it. */
export function acrossSection(pct: number, section: SectionSpan): number {
  const span = spanOf(section);
  const offset = (((pct - section.startPct) % 1) + 1) % 1;
  return span > 0 ? offset / span : Number.POSITIVE_INFINITY;
}

/** Lap fraction at a position across the section. */
export function lapPctAt(local: number, section: SectionSpan): number {
  const pct = section.startPct + Math.min(1, Math.max(0, local)) * spanOf(section);
  return pct >= 1 ? pct - 1 : pct;
}

/** The drawn window: the approach start, the section boundaries, and the boundary's position across the window. */
export interface SectionWindow {
  /** Lap fraction where the approach begins. */
  approachPct: number;
  startPct: number;
  endPct: number;
  /** Position of the section start across the window (0 at the approach start, 1 at the section end). */
  boundary: number;
}

/** The section with its approach (`approach_start`): lead-in = section length clamped to 3-8 % lap, bounded by the previous start or the lap start. */
export function sectionWindow(section: SectionSpan): SectionWindow {
  const span = spanOf(section);
  const lead = Math.min(APPROACH_MAX_FRACTION, Math.max(APPROACH_MIN_FRACTION, span));
  const previous = section.previousStartPct;
  const floor = previous != null && Number.isFinite(previous) && previous < section.startPct ? previous : 0;
  const approachPct = Math.max(floor, section.startPct - lead);
  const total = spanOf({ startPct: approachPct, endPct: section.endPct });
  return { approachPct, startPct: section.startPct, endPct: section.endPct, boundary: total > 0 && approachPct < section.startPct ? (section.startPct - approachPct) / total : 0 };
}

/** The window as a span (approach start to section end). */
export function windowSpan(window: SectionWindow): SectionSpan {
  return { startPct: window.approachPct, endPct: window.endPct };
}

/** Position across the section (negative in the approach) of a position across the window. */
export function sectionLocal(position: number, window: SectionWindow): number {
  const boundary = window.boundary;
  return boundary < 1 ? (position - boundary) / (1 - boundary) : 0;
}

/** Position across the window of a position across the section (clamped to the window). */
export function windowPosition(local: number, window: SectionWindow): number {
  const boundary = window.boundary;
  return Math.min(1, Math.max(0, boundary + local * (1 - boundary)));
}

/** Lap fraction at a position across the window. */
export function windowPctAt(position: number, window: SectionWindow): number {
  const pct = window.approachPct + Math.min(1, Math.max(0, position)) * spanOf(windowSpan(window));
  return pct >= 1 ? pct - 1 : pct;
}

export interface SectionBins {
  /** Trace bins inside the span, in driving order. */
  bins: number[];
  /** Their positions across the span (ascending). */
  x: Float64Array;
}

/** The trace bins whose lap fraction lies inside the span (a section, or a window with its approach). */
export function sectionBins(lapX: ArrayLike<number>, section: SectionSpan): SectionBins {
  const picked: { bin: number; local: number }[] = [];
  for (let bin = 0; bin < lapX.length; bin++) {
    const local = acrossSection(lapX[bin]!, section);
    if (local <= 1 + 1e-9) picked.push({ bin, local: Math.min(1, local) });
  }
  picked.sort((a, b) => a.local - b.local || a.bin - b.bin);
  return { bins: picked.map((p) => p.bin), x: Float64Array.from(picked.map((p) => p.local)) };
}

/** The map's section segment as an open polyline from its start to its end, each point at its position across the section. */
export function sectionTrackPoints(points: readonly MapPoint[], section: SectionSpan): MapPoint[] {
  if (points.length < 2 || spanOf(section) <= 0) return [];
  const start = mapPointAt(points, section.startPct);
  const end = mapPointAt(points, section.endPct);
  if (!start || !end) return [];
  const inner = points
    .map((point) => ({ x: point.x, y: point.y, percent: acrossSection(point.percent, section) }))
    .filter((point) => point.percent > 1e-9 && point.percent < 1 - 1e-9)
    .sort((a, b) => a.percent - b.percent);
  return [{ x: start.x, y: start.y, percent: 0 }, ...inner, { x: end.x, y: end.y, percent: 1 }];
}

/** The point at a position across the section on the open polyline (never wraps back to the start). */
export function sectionPointAt(points: readonly MapPoint[], local: number): MapPoint | null {
  if (points.length === 0) return null;
  const target = Math.min(1, Math.max(0, local));
  for (let i = 0; i + 1 < points.length; i++) {
    const a = points[i]!;
    const b = points[i + 1]!;
    if (target > b.percent && i + 2 < points.length) continue;
    const t = b.percent - a.percent > 1e-9 ? Math.min(1, Math.max(0, (target - a.percent) / (b.percent - a.percent))) : 0;
    return { percent: target, x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t };
  }
  return points[0]!;
}

/** Position across the section of the segment point nearest to (px, py), on the open polyline. */
export function projectOnSection(points: readonly MapPoint[], px: number, py: number): number {
  let best = Number.POSITIVE_INFINITY;
  let local = 0;
  for (let i = 0; i + 1 < points.length; i++) {
    const a = points[i]!;
    const b = points[i + 1]!;
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const lengthSquared = dx * dx + dy * dy;
    const amount = lengthSquared <= 1e-12 ? 0 : Math.min(1, Math.max(0, ((px - a.x) * dx + (py - a.y) * dy) / lengthSquared));
    const d = (px - (a.x + dx * amount)) ** 2 + (py - (a.y + dy * amount)) ** 2;
    if (d < best) {
      best = d;
      local = a.percent + (b.percent - a.percent) * amount;
    }
  }
  return Math.min(1, Math.max(0, local));
}

/** A drawn lap: its number and colour (a CSS colour the canvas accepts). */
export interface DrawnLap {
  lap: number;
  color: string;
}

/** The drawing scale from a channel's extremes: pedals 0-1, steering, slip and gap symmetric about zero, the rest padded. */
function rangeOf(data: TraceData, id: string, min: number, max: number): Range {
  if (id === "throttle" || id === "brake") return { min: 0, max: 1 };
  if (!Number.isFinite(min)) return id === "gap" ? { min: -0.05, max: 0.05 } : { min: -0.5, max: 0.5 };
  if (id === "steering" || id === "slip" || id === "gap") {
    // Road-wheel steering is about a twelfth of the wheel angle (audit T13); slip's floor is one degree, the gap's 0.05 s.
    const floor = id === "steering" ? (data.steering.roadWheel ? 1 / 12 : 1) : id === "slip" ? 1 : 0.05;
    const m = Math.max(floor, Math.abs(min), Math.abs(max));
    return { min: -m, max: m };
  }
  if (max - min < 0.0001) return { min: min - 0.5, max: max + 0.5 };
  const pad = (max - min) * 0.04;
  return { min: min - pad, max: max + pad };
}

/** Drawing scale of a channel over the given bins of the drawn laps (same rules as the Traces rows: pedals 0–1, steering and slip symmetric). */
export function sectionRange(data: TraceData, id: string, lapIndices: readonly number[], bins: readonly number[]): Range {
  const column = signalColumn(data, id);
  let min = Number.POSITIVE_INFINITY;
  let max = Number.NEGATIVE_INFINITY;
  if (column) {
    for (const lap of lapIndices) {
      for (const bin of bins) {
        const value = column[lap * data.bins + bin]!;
        if (!Number.isFinite(value)) continue;
        if (value < min) min = value;
        if (value > max) max = value;
      }
    }
  }
  return rangeOf(data, id, min, max);
}

/** A lap's smoothed values at the given bins (NaN where the bin has none), as the Traces cursor smooths them. */
export function sectionValues(data: TraceData, id: string, lapIndex: number, bins: readonly number[]): Float64Array {
  const out = new Float64Array(bins.length).fill(Number.NaN);
  const column = signalColumn(data, id);
  const signal = resolve(data, id);
  if (!column || !signal) return out;
  bins.forEach((bin, i) => {
    out[i] = smoothedValue(column, lapIndex, bin, data.bins, id, signal.step);
  });
  return out;
}

/** Session time, `laps x bins`: the catalog's channel, or one fetched after the store was built (found by source key). */
export function sessionTimeColumn(data: TraceData): Float64Array | null {
  const column = signalColumn(data, "session-time");
  if (column) return column;
  let channel = data.aux.sessionTime;
  for (const signal of data.catalog.signals) {
    if (channel) break;
    if (signal.source_key === "session_time_s") channel = data.table.channels.get(signal.id) ?? null;
  }
  return channel ? channelAsFloat(channel) : null;
}

/**
 * Time gap to the reference lap at the given bins (the Traces delta row, `deltaColumn`, anchored at the window's first bin both laps
 * cover): this lap's session time since that bin, less the reference lap's. Positive is behind. NaN where either lap has no time.
 */
export function gapValues(time: Float64Array | null, lapIndex: number, referenceIndex: number | null, bins: readonly number[], binCount: number): Float64Array {
  const out = new Float64Array(bins.length).fill(Number.NaN);
  if (!time || referenceIndex === null) return out;
  const at = (lap: number, i: number): number => time[lap * binCount + bins[i]!]!;
  let anchor = -1;
  for (let i = 0; i < bins.length && anchor < 0; i++) if (Number.isFinite(at(lapIndex, i)) && Number.isFinite(at(referenceIndex, i))) anchor = i;
  if (anchor < 0) return out;
  const lapStart = at(lapIndex, anchor);
  const referenceStart = at(referenceIndex, anchor);
  for (let i = anchor; i < bins.length; i++) out[i] = (at(lapIndex, i) - lapStart) - (at(referenceIndex, i) - referenceStart);
  return out;
}

/** A recorded value at a position across the span, read linearly between the two bins around it; NaN when either is missing. */
export function valueAtPosition(column: Float64Array | null, lapIndex: number, bins: SectionBins, position: number, binCount: number): number {
  if (!column) return Number.NaN;
  return seriesAt(bins.bins.map((bin) => column[lapIndex * binCount + bin]!), bins.x, position);
}

/** A series' value at a position across the span (`x`, its bins' positions), read linearly between the two around it; NaN when either is missing. */
export function seriesAt(values: ArrayLike<number>, x: ArrayLike<number>, position: number): number {
  let right = 0;
  while (right < x.length && x[right]! < position - 1e-12) right++;
  if (right >= x.length) return Number.NaN;
  const b = values[right]!;
  if (Math.abs(x[right]! - position) <= 1e-12) return b;
  if (right === 0) return Number.NaN;
  const a = values[right - 1]!;
  if (!Number.isFinite(a) || !Number.isFinite(b)) return Number.NaN;
  return a + ((b - a) * (position - x[right - 1]!)) / (x[right]! - x[right - 1]!);
}

/**
 * Entry speed difference to the reference lap at the section start, mph: each lap's recorded speed at the boundary (the bins either
 * side, read linearly) less the reference lap's. Positive is faster in. null without a reference lap or where either lap has no speed.
 */
export function entrySpeedDeltas(data: TraceData, window: SectionWindow, laps: readonly number[], referenceLap: number | null): Map<number, number | null> {
  const out = new Map<number, number | null>();
  const column = signalColumn(data, "speed");
  const bins = sectionBins(data.x, windowSpan(window));
  const reference = referenceLap !== null ? data.lapIndex.get(referenceLap) : undefined;
  const referenceSpeed = reference !== undefined ? valueAtPosition(column, reference, bins, window.boundary, data.bins) : Number.NaN;
  for (const lap of laps) {
    const index = data.lapIndex.get(lap);
    const speed = index !== undefined ? valueAtPosition(column, index, bins, window.boundary, data.bins) : Number.NaN;
    out.set(lap, Number.isFinite(speed) && Number.isFinite(referenceSpeed) ? speed - referenceSpeed : null);
  }
  return out;
}

/** One drawn row: its values per drawn lap (in the order given), its drawing scale and the cursor's signal (none when not recorded). */
export interface SectionRow {
  id: SectionRowId;
  name: string;
  unit: string;
  values: Float64Array[];
  range: Range;
  signal: CursorSignal | null;
}

/**
 * The six rows over the bins for the drawn laps (laps the store lacks get all-NaN values): recorded channels smoothed as the Traces
 * cursor smooths them, the gap row against `referenceLap` (the section's median lap; an empty row without it).
 */
export function sectionRows(data: TraceData, bins: SectionBins, laps: readonly number[], referenceLap: number | null): SectionRow[] {
  const time = sessionTimeColumn(data);
  const reference = referenceLap !== null ? (data.lapIndex.get(referenceLap) ?? null) : null;
  const missing = (): Float64Array => new Float64Array(bins.bins.length).fill(Number.NaN);
  return SECTION_ROWS.map((id) => {
    let values: Float64Array[];
    let name: string;
    let unit: string;
    let signal: CursorSignal | null = null;
    if (id === "gap") {
      values = laps.map((lap) => {
        const index = data.lapIndex.get(lap);
        return index === undefined ? missing() : gapValues(time, index, reference, bins.bins, data.bins);
      });
      name = "Gap to median";
      unit = "s";
      if (time && reference !== null) signal = { id: "delta", shortLabel: "Gap", unit: "s", digits: 3, min: 0, max: 0 };
    } else {
      const resolved = resolve(data, id);
      values = laps.map((lap) => {
        const index = data.lapIndex.get(lap);
        return index === undefined ? missing() : sectionValues(data, id, index, bins.bins);
      });
      // Body slip: the chassis velocity angle (`slip_angle_deg`, sideslip), not tyre slip.
      name = id === "slip" ? "Body slip" : (resolved?.name ?? id);
      unit = resolved?.unit ?? "";
      if (resolved && signalColumn(data, id)) signal = { id, shortLabel: resolved.shortLabel, unit: resolved.unit, digits: resolved.digits, min: 0, max: 0 };
    }
    let min = Number.POSITIVE_INFINITY;
    let max = Number.NEGATIVE_INFINITY;
    for (const series of values) {
      for (const value of series) {
        if (!Number.isFinite(value)) continue;
        if (value < min) min = value;
        if (value > max) max = value;
      }
    }
    const range = rangeOf(data, id, min, max);
    return { id, name, unit, values, range, signal: signal ? { ...signal, min: range.min, max: range.max } : null };
  });
}

export interface PlotFrame {
  plotLeft: number;
  plotWidth: number;
  top: number;
  plotHeight: number;
}

/** Pixel y of a value in a row frame (the placement `sectionPath` uses). */
export function rowY(value: number, range: Range, frame: PlotFrame): number {
  const span = range.max - range.min || 1;
  return frame.top + frame.plotHeight - 4 - ((value - range.min) / span) * (frame.plotHeight - 8);
}

/** One lap's polyline for a row (`M`/`L` in CSS pixels); a missing value breaks the line. Empty when nothing is drawn. */
export function sectionPath(values: Float64Array, x: Float64Array, range: Range, frame: PlotFrame): string {
  const parts: string[] = [];
  let started = false;
  for (let i = 0; i < values.length; i++) {
    const value = values[i]!;
    if (!Number.isFinite(value)) {
      started = false;
      continue;
    }
    const px = frame.plotLeft + x[i]! * frame.plotWidth;
    const py = rowY(value, range, frame);
    parts.push(`${started ? "L" : "M"}${px.toFixed(1)},${py.toFixed(1)}`);
    started = true;
  }
  return parts.join(" ");
}

/**
 * One lap's polyline split at the section start: the approach (the bins before the boundary and the first at or after it, so the
 * line reaches the boundary) and the section (from the first bin at or after the boundary). Either is empty when nothing is drawn.
 */
export function splitSectionPath(values: Float64Array, x: Float64Array, boundary: number, range: Range, frame: PlotFrame): { approach: string; section: string } {
  let first = 0;
  while (first < x.length && x[first]! < boundary - 1e-9) first++;
  const end = Math.min(x.length, first + 1);
  return {
    approach: first > 0 ? sectionPath(values.subarray(0, end), x.subarray(0, end), range, frame) : "",
    section: sectionPath(values.subarray(first), x.subarray(first), range, frame),
  };
}

/** The pass pair: the fastest and the slowest of the drawn laps (first and last in list order), drawn with the thick stroke. */
export function passPair(laps: readonly { lap: number; median: boolean }[]): Set<number> {
  const listed = laps.filter((lap) => !lap.median);
  return new Set(listed.length ? [listed[0]!.lap, listed[listed.length - 1]!.lap] : []);
}

/** Which laps the traces draw: the selected subset when any is selected, else every listed lap, then the median lap when reported. */
export function tracedLaps(listed: readonly number[], selected: ReadonlySet<number>, medianLap: number | null): { laps: number[]; median: number | null } {
  const chosen = listed.filter((lap) => selected.has(lap));
  const laps = chosen.length ? chosen : [...listed];
  const median = medianLap !== null && !laps.includes(medianLap) ? medianLap : null;
  return { laps, median };
}

export interface SectionCursorInput {
  /** Drawn laps in card order (the six first, the median last); `rows` holds their values in the same order. */
  laps: readonly DrawnLap[];
  bins: SectionBins;
  rows: readonly SectionRow[];
  metrics: ChartMetrics;
  /** Where the cursor starts, across the window. */
  initialFraction: number;
  key: string;
}

/**
 * The shared cursor over the window: rows are the six channels, traces the drawn laps, x the bins' positions across the window. The
 * map side is driven through `setFraction` and `onFraction` (the section segment is an open line, not the lap loop), so the cursor
 * gets no track points.
 */
export function buildSectionCursorConfig(input: SectionCursorInput): CursorConfig {
  const { metrics, bins } = input;
  const card = cardMetrics(metrics.unit);
  const rows: CursorRow[] = input.rows.map((row, rowIndex) => ({
    rowId: row.id,
    top: metrics.chartTop + rowIndex * metrics.rowHeight,
    plotHeight: metrics.plotHeight,
    signals: row.signal ? [row.signal] : [],
    widestText: row.id === "steering" ? 13 : row.id === "gap" ? 10 : 11,
  }));
  const traces: CursorTrace[] = input.laps.map((lap, lapIndex) => ({
    lap: lap.lap,
    color: lap.color,
    rowValues: input.rows.map((row) => (row.signal ? [row.values[lapIndex]!] : [])),
  }));
  const empty = (): Float64Array => new Float64Array(bins.bins.length).fill(Number.NaN);
  return {
    key: input.key,
    bins: bins.bins.length,
    x: bins.x,
    metrics,
    card,
    rows,
    traces,
    logicalTraceCount: traces.length,
    tooltipCapacity: tooltipCapacityFor(metrics.rowHeight, card.slotHeight),
    aggregate: { speed: empty(), throttle: empty(), brake: empty(), delta: empty() },
    trackPoints: [],
    distanceStrip: false,
    initialFraction: input.initialFraction,
  };
}

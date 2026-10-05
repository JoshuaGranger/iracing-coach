import { cardMetrics, type ChartMetrics, type CursorConfig, type CursorRow, type CursorTrace } from "../cursor/cursor-config";
import { tooltipCapacityFor } from "../cursor/cursor-core";
import { mapPointAt, type MapPoint } from "../geometry/map-model";
import { resolve, signalColumn, smoothedValue, type Range, type TraceData } from "../geometry/trace-model";

/**
 * Sections telemetry (backlog #64): the chosen section's laps over the section's distance. Every value is a trace bin the backend
 * reported; this module only picks the bins inside the section, places them across the plot and builds the cursor's configuration
 * over them. The cursor's fraction is the position across the section (0 at its start, 1 at its end).
 */

/** The four channels drawn, one row each, in this order. */
export const SECTION_SIGNALS = ["speed", "throttle", "brake", "steering"] as const;

export interface SectionSpan {
  startPct: number;
  endPct: number;
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

export interface SectionBins {
  /** Trace bins inside the section, in driving order. */
  bins: number[];
  /** Their positions across the section (ascending). */
  x: Float64Array;
}

/** The trace bins whose lap fraction lies inside the section. */
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

/** Drawing scale of a channel over the section's bins of the drawn laps (same rules as the Traces rows: pedals 0–1, steering symmetric). */
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
  if (id === "throttle" || id === "brake") return { min: 0, max: 1 };
  if (!Number.isFinite(min)) return { min: -0.5, max: 0.5 };
  if (id === "steering") {
    const floor = data.steering.roadWheel ? 1 / 12 : 1;
    const m = Math.max(floor, Math.abs(min), Math.abs(max));
    return { min: -m, max: m };
  }
  if (max - min < 0.0001) return { min: min - 0.5, max: max + 0.5 };
  const pad = (max - min) * 0.04;
  return { min: min - pad, max: max + pad };
}

/** A lap's smoothed values at the section's bins (NaN where the bin has none), as the Traces cursor smooths them. */
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

export interface PlotFrame {
  plotLeft: number;
  plotWidth: number;
  top: number;
  plotHeight: number;
}

/** One lap's polyline for a row (`M`/`L` in CSS pixels); a missing value breaks the line. Empty when nothing is drawn. */
export function sectionPath(values: Float64Array, x: Float64Array, range: Range, frame: PlotFrame): string {
  const span = range.max - range.min || 1;
  const parts: string[] = [];
  let started = false;
  for (let i = 0; i < values.length; i++) {
    const value = values[i]!;
    if (!Number.isFinite(value)) {
      started = false;
      continue;
    }
    const px = frame.plotLeft + x[i]! * frame.plotWidth;
    const py = frame.top + frame.plotHeight - 4 - ((value - range.min) / span) * (frame.plotHeight - 8);
    parts.push(`${started ? "L" : "M"}${px.toFixed(1)},${py.toFixed(1)}`);
    started = true;
  }
  return parts.join(" ");
}

/** Which laps the traces draw: the selected subset when any is selected, else every listed lap, then the median lap when reported. */
export function tracedLaps(listed: readonly number[], selected: ReadonlySet<number>, medianLap: number | null): { laps: number[]; median: number | null } {
  const chosen = listed.filter((lap) => selected.has(lap));
  const laps = chosen.length ? chosen : [...listed];
  const median = medianLap !== null && !laps.includes(medianLap) ? medianLap : null;
  return { laps, median };
}

export interface SectionCursorInput {
  data: TraceData;
  /** Drawn laps in card order (the six first, the median last). */
  laps: readonly DrawnLap[];
  bins: SectionBins;
  ranges: readonly Range[];
  metrics: ChartMetrics;
  initialFraction: number;
  key: string;
}

/**
 * The shared cursor over the section: rows are the four channels, traces the drawn laps, x the bins' positions across the section.
 * The map side is driven through `setFraction` and `onFraction` (the section segment is an open line, not the lap loop), so the
 * cursor gets no track points.
 */
export function buildSectionCursorConfig(input: SectionCursorInput): CursorConfig {
  const { data, metrics, bins } = input;
  const card = cardMetrics(metrics.unit);
  const present = input.laps.filter((lap) => data.lapIndex.has(lap.lap));
  const rows: CursorRow[] = SECTION_SIGNALS.map((id, rowIndex) => {
    const signal = resolve(data, id);
    const range = input.ranges[rowIndex]!;
    return {
      rowId: id,
      top: metrics.chartTop + rowIndex * metrics.rowHeight,
      plotHeight: metrics.plotHeight,
      signals: signal && signalColumn(data, id) ? [{ id, shortLabel: signal.shortLabel, unit: signal.unit, digits: signal.digits, min: range.min, max: range.max }] : [],
      widestText: id === "steering" ? 13 : 11,
    };
  });
  const traces: CursorTrace[] = present.map((lap) => {
    const lapIndex = data.lapIndex.get(lap.lap)!;
    return { lap: lap.lap, color: lap.color, rowValues: rows.map((row) => row.signals.map((signal) => sectionValues(data, signal.id, lapIndex, bins.bins))) };
  });
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

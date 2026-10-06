import { pointBudgetPerTrace, representativeIndices } from "../geometry/budget";
import {
  lapColor,
  plotColumn,
  resolve,
  traceOpacity,
  traceStrokeWidth,
  type ChartPathGroup,
  type Range,
  type RowFrame,
  type TraceData,
} from "../geometry/trace-model";

/**
 * Trace lines through the measured bin points (backlog #77). A continuous channel is a monotone cubic (Fritsch-Carlson, as
 * d3's curveMonotoneX): the curve passes through every drawn point and never rises above or falls below its two neighbours,
 * so no peak or dip is invented between bins. A step channel (gear, on/off, position) is drawn as steps between bin centres.
 * A missing bin breaks the line. Pure functions; the cursor reads the same points (cursor-core `valueAt`).
 */

const sign = (value: number): number => (value < 0 ? -1 : 1);
const f = (value: number): string => value.toFixed(1);

/** Interior tangent at point 1 of three (d3 slope3): zero at a turning point, limited so the curve stays monotone. */
function interiorSlope(x0: number, y0: number, x1: number, y1: number, x2: number, y2: number): number {
  const h0 = x1 - x0;
  const h1 = x2 - x1;
  const s0 = (y1 - y0) / h0;
  const s1 = (y2 - y1) / h1;
  const p = (s0 * h1 + s1 * h0) / (h0 + h1);
  return (sign(s0) + sign(s1)) * Math.min(Math.abs(s0), Math.abs(s1), 0.5 * Math.abs(p)) || 0;
}

/** End tangent from the neighbouring tangent (d3 slope2). */
function endSlope(x0: number, y0: number, x1: number, y1: number, tangent: number): number {
  const h = x1 - x0;
  return h ? (3 * (y1 - y0) / h - tangent) / 2 : tangent;
}

/**
 * SVG path data for one run of points with strictly increasing `x` (pixels): `M` then one `C` per segment; two points are a line,
 * one point a lone `M`.
 */
export function monotoneRun(xs: ArrayLike<number>, ys: ArrayLike<number>, start = 0, end = xs.length): string {
  const count = end - start;
  if (count <= 0) return "";
  const parts: string[] = [`M${f(xs[start]!)},${f(ys[start]!)}`];
  if (count === 1) return parts[0]!;
  if (count === 2) return `${parts[0]} L${f(xs[start + 1]!)},${f(ys[start + 1]!)}`;
  const tangents = new Float64Array(count);
  for (let i = 1; i < count - 1; i++) {
    const k = start + i;
    tangents[i] = interiorSlope(xs[k - 1]!, ys[k - 1]!, xs[k]!, ys[k]!, xs[k + 1]!, ys[k + 1]!);
  }
  tangents[0] = endSlope(xs[start]!, ys[start]!, xs[start + 1]!, ys[start + 1]!, tangents[1]!);
  tangents[count - 1] = endSlope(xs[end - 2]!, ys[end - 2]!, xs[end - 1]!, ys[end - 1]!, tangents[count - 2]!);
  for (let i = 0; i < count - 1; i++) {
    const k = start + i;
    const x0 = xs[k]!;
    const y0 = ys[k]!;
    const x1 = xs[k + 1]!;
    const y1 = ys[k + 1]!;
    const dx = (x1 - x0) / 3;
    parts.push(`C${f(x0 + dx)},${f(y0 + dx * tangents[i]!)} ${f(x1 - dx)},${f(y1 - dx * tangents[i + 1]!)} ${f(x1)},${f(y1)}`);
  }
  return parts.join(" ");
}

/** Steps through one run: each value held from half way after the previous point to half way before the next. */
export function stepRun(xs: ArrayLike<number>, ys: ArrayLike<number>, start = 0, end = xs.length): string {
  if (end - start <= 0) return "";
  const parts: string[] = [`M${f(xs[start]!)},${f(ys[start]!)}`];
  for (let k = start + 1; k < end; k++) {
    const middle = (xs[k - 1]! + xs[k]!) / 2;
    parts.push(`H${f(middle)} V${f(ys[k]!)}`);
  }
  if (end - start > 1) parts.push(`H${f(xs[end - 1]!)}`);
  return parts.join(" ");
}

/** One lap's line for a signal in a row frame through its measured bins (at most `pointBudget` of them); empty without a value. */
export function lapTracePath(
  data: TraceData,
  column: Float64Array,
  lap: number,
  step: boolean,
  range: Range,
  frame: RowFrame,
  pointBudget: number,
): string {
  const lapIndex = data.lapIndex.get(lap);
  if (lapIndex === undefined) return "";
  const bins = data.bins;
  const base = lapIndex * bins;
  const indices = representativeIndices(bins, pointBudget);
  const span = range.max - range.min || 1;
  const xs = new Float64Array(indices.length);
  const ys = new Float64Array(indices.length);
  const runs: string[] = [];
  let count = 0;
  const flush = (): void => {
    if (count > 0) runs.push(step ? stepRun(xs, ys, 0, count) : monotoneRun(xs, ys, 0, count));
    count = 0;
  };
  for (const bin of indices) {
    const value = column[base + bin]!;
    if (!Number.isFinite(value)) {
      flush();
      continue;
    }
    xs[count] = frame.plotLeft + data.x[bin]! * frame.plotWidth;
    ys[count] = frame.top + frame.plotHeight - 4 - ((value - range.min) / span) * (frame.plotHeight - 8);
    count += 1;
  }
  flush();
  return runs.join(" ");
}

/**
 * Path groups for one signal of one row: laps sharing a colour share one `<path>`, so the DOM stays bounded by the colour budget
 * however many laps are selected (as `chartPathGroups`, with lines through the measured bins).
 */
export function tracePathGroups(
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
    if (!data.lapIndex.has(lap)) continue;
    const focused = spotlightLap === lap;
    const budget = pointBudgetPerTrace(ordered.length, frame.plotWidth, focused, data.budgets);
    const d = lapTracePath(data, column, lap, signal.step, range, frame, budget);
    if (!d) continue;
    const color = lapColor(data, lap);
    const key = `${color}|${focused ? 1 : 0}`;
    let group = groups.get(key);
    if (!group) {
      group = { key, laps: [], color, d: "", spotlight: focused, strokeWidth: traceStrokeWidth(spotlightLap, lap, signalIndex), opacity: traceOpacity(spotlightLap, lap, signalIndex) };
      groups.set(key, group);
    }
    group.laps.push(lap);
    group.d = group.d ? `${group.d} ${d}` : d;
  }
  return [...groups.values()].sort((a, b) => Number(a.spotlight) - Number(b.spotlight) || (a.color < b.color ? -1 : a.color > b.color ? 1 : 0));
}

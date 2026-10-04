import { asRecord, num } from "../types";
import type { Investigation, RunView, TrendPoint } from "../investigation-model";

const LITERS_PER_GALLON = 3.785411784;

/**
 * The screened fit confined to its supported span: the backend's fit points clipped to its `domain` (green laps into the run). Never
 * extended past the observations the backend fitted. Null without a two-point fit.
 */
export function fitSegment(run: RunView): { x1: number; y1: number; x2: number; y2: number } | null {
  const fit = run.fit;
  if (!fit || fit.points.length < 2) return null;
  const [p, q] = [fit.points[0]!, fit.points[fit.points.length - 1]!];
  if (q.x === p.x) return null;
  const slope = (q.y - p.y) / (q.x - p.x);
  const lo = fit.domain ? Math.max(Math.min(p.x, q.x), fit.domain[0]) : Math.min(p.x, q.x);
  const hi = fit.domain ? Math.min(Math.max(p.x, q.x), fit.domain[1]) : Math.max(p.x, q.x);
  if (!(hi > lo)) return null;
  const at = (x: number): number => p.y + slope * (x - p.x);
  return { x1: lo, y1: at(lo), x2: hi, y2: at(hi) };
}

/** `+0.041 s/lap · 18 laps`: the backend's slope per green lap and its eligible count. Null without a slope. */
export function fitLabel(run: RunView): string | null {
  const slope = run.fit?.slopeSPerGreenLap;
  if (slope === null || slope === undefined || !Number.isFinite(slope)) return null;
  const sign = slope > 0 ? "+" : slope < 0 ? "−" : "";
  return `${sign}${Math.abs(slope).toFixed(3)} s/lap · ${run.fit!.eligibleCount} laps`;
}

export interface RunCard {
  runNumber: number;
  lapsText: string;
  /** The observed trend (the backend's 5-observation median), every segment, for the sparkline. */
  trend: TrendPoint[][];
  slope: string | null;
  noFitReason: string | null;
  used: number;
  screened: number;
  /** `15.9 → 3.8 gal` from the runs view's fuel at start and end; the range stays a range. Null when not reported. */
  fuelBand: string | null;
  /** Green laps on the tires at the run's end, per corner: one figure, or a range when the corners differ. Null when not reported. */
  tireLaps: string | null;
}

function backendRun(runs: readonly unknown[], runNumber: number): Record<string, unknown> | null {
  for (const raw of runs) {
    const run = asRecord(raw);
    if (num(run.run_number) === runNumber) return run;
  }
  return null;
}

function fuelBand(run: Record<string, unknown> | null): string | null {
  const fuel = asRecord(run?.fuel);
  const start = num(fuel.start_l);
  const end = num(fuel.end_l);
  if (start === null && end === null) return null;
  const gal = (liters: number | null): string => (liters === null ? "—" : (liters / LITERS_PER_GALLON).toFixed(1));
  return `${gal(start)} → ${gal(end)} gal`;
}

function tireLaps(run: Record<string, unknown> | null): string | null {
  const corners = asRecord(asRecord(run?.tire_set_lifecycle).corners);
  const values = Object.values(corners).map((corner) => num(asRecord(corner).green_laps_on_set)).filter((value): value is number => value !== null);
  if (values.length === 0) return null;
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const text = (value: number): string => (Number.isInteger(value) ? String(value) : value.toFixed(1));
  return lo === hi ? text(lo) : `${text(lo)}–${text(hi)}`;
}

/** One card per run: sparkline, slope, laps used and screened, fuel band, tire laps. Counts are of the backend's lap flags. */
export function runCards(inv: Investigation, runs: readonly unknown[]): RunCard[] {
  return inv.runs.map((run) => {
    const laps = inv.laps.filter((lap) => lap.runNumber === run.runNumber && lap.plotPace);
    const raw = backendRun(runs, run.runNumber);
    return {
      runNumber: run.runNumber,
      lapsText: `L${run.firstLap}–${run.lastLap}`,
      trend: run.trend.segments,
      slope: fitLabel(run),
      noFitReason: fitLabel(run) === null ? run.fitUnavailableReason ?? "No fit" : null,
      used: laps.filter((lap) => lap.eligible).length,
      screened: laps.filter((lap) => !lap.eligible).length,
      fuelBand: fuelBand(raw),
      tireLaps: tireLaps(raw),
    };
  });
}

/** Sparkline path of the trend in a `width` × `height` box (y down, slower up). */
export function sparkPath(segments: readonly TrendPoint[][], width: number, height: number): string {
  const points = segments.flat();
  if (points.length < 2) return "";
  const xs = points.map((p) => p.x);
  const ys = points.map((p) => p.y);
  const [x0, x1] = [Math.min(...xs), Math.max(...xs)];
  const [y0, y1] = [Math.min(...ys), Math.max(...ys)];
  const sx = (x: number): number => ((x - x0) / Math.max(1e-9, x1 - x0)) * (width - 2) + 1;
  const sy = (y: number): number => height - 1 - ((y - y0) / Math.max(1e-9, y1 - y0)) * (height - 2);
  return segments
    .filter((segment) => segment.length > 0)
    .map((segment) => segment.map((p, i) => `${i === 0 ? "M" : "L"}${sx(p.x).toFixed(1)},${sy(p.y).toFixed(1)}`).join(" "))
    .join(" ");
}

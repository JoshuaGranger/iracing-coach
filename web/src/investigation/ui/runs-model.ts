import { runColour, runDash } from "../../core/palette";
import type { PitStop } from "../geometry/rail-model";
import type { Investigation, InvestigationLap, RunView } from "../investigation-model";
import { asRecord, num } from "../types";

const LITERS_PER_GALLON = 3.785411784;

/**
 * Runs as the Race and Runs charts draw them (round 3, #40 #42 #43 #44 #47): the backend's screened fit as the only line, run
 * identity by tire set, one line of facts per run, and the run toggles. Every figure is the backend's; the browser only evaluates
 * the backend's fitted line at the exposures the backend reported, to draw it.
 */

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

/** `+0.041 s/lap`: the backend's slope per green lap. Null without a slope. */
export function slopeText(run: RunView): string | null {
  const slope = run.fit?.slopeSPerGreenLap;
  if (slope === null || slope === undefined || !Number.isFinite(slope)) return null;
  const sign = slope > 0 ? "+" : slope < 0 ? "−" : "";
  return `${sign}${Math.abs(slope).toFixed(3)} s/lap`;
}

/** `+0.041 s/lap · 18 laps`: the backend's slope per green lap and its eligible count. Null without a slope. */
export function fitLabel(run: RunView): string | null {
  const slope = slopeText(run);
  return slope === null ? null : `${slope} · ${run.fit!.eligibleCount} laps`;
}

/**
 * The fit on the Race chart's lap axis: the backend's line evaluated at the green exposure the backend reported for each lap of the
 * run, kept only inside the fitted span. Laps without a reported exposure (caution, pit) carry no point; the curve passes them.
 */
export function fitOnLaps(run: RunView, laps: readonly InvestigationLap[]): { lap: number; y: number }[] {
  const segment = fitSegment(run);
  if (!segment) return [];
  const slope = (segment.y2 - segment.y1) / (segment.x2 - segment.x1);
  const eps = 1e-9;
  return laps
    .filter((lap) => lap.runNumber === run.runNumber && lap.greenExposure !== null && lap.greenExposure >= segment.x1 - eps && lap.greenExposure <= segment.x2 + eps)
    .sort((a, b) => a.lap - b.lap)
    .map((lap) => ({ lap: lap.lap, y: segment.y1 + slope * ((lap.greenExposure as number) - segment.x1) }));
}

/**
 * A smooth path through pixel points in x order (monotone cubic, Fritsch–Carlson): it never overshoots between points, so the curve
 * stays inside the values it joins. One point draws nothing.
 */
export function smoothPath(points: readonly (readonly [number, number])[]): string {
  const p = points.filter(([x, y]) => Number.isFinite(x) && Number.isFinite(y));
  if (p.length < 2) return "";
  const f = (value: number): string => (Math.round(value * 100) / 100).toString();
  if (p.length === 2) return `M${f(p[0]![0])},${f(p[0]![1])}L${f(p[1]![0])},${f(p[1]![1])}`;
  const count = p.length;
  const dx: number[] = [];
  const slope: number[] = [];
  for (let i = 0; i < count - 1; i++) {
    dx.push(p[i + 1]![0] - p[i]![0]);
    slope.push(dx[i]! === 0 ? 0 : (p[i + 1]![1] - p[i]![1]) / dx[i]!);
  }
  const tangent: number[] = [slope[0]!];
  for (let i = 1; i < count - 1; i++) tangent.push(slope[i - 1]! * slope[i]! <= 0 ? 0 : (slope[i - 1]! + slope[i]!) / 2);
  tangent.push(slope[count - 2]!);
  for (let i = 0; i < count - 1; i++) {
    if (slope[i] === 0) { tangent[i] = 0; tangent[i + 1] = 0; continue; }
    const a = tangent[i]! / slope[i]!;
    const b = tangent[i + 1]! / slope[i]!;
    const h = a * a + b * b;
    if (h > 9) { const t = 3 / Math.sqrt(h); tangent[i] = t * a * slope[i]!; tangent[i + 1] = t * b * slope[i]!; }
  }
  let d = `M${f(p[0]![0])},${f(p[0]![1])}`;
  for (let i = 0; i < count - 1; i++) {
    const [x0, y0] = p[i]!;
    const [x1, y1] = p[i + 1]!;
    const h = dx[i]! / 3;
    d += `C${f(x0 + h)},${f(y0 + tangent[i]! * h)} ${f(x1 - h)},${f(y1 - tangent[i + 1]! * h)} ${f(x1)},${f(y1)}`;
  }
  return d;
}

/** A run's identity: hue by tire set, a lighter shade for each run after a stop that changed no tires (#42). */
export interface RunIdentity {
  runNumber: number;
  /** Tire set, 1-based in race order: the hue (`--run-<set>`). */
  set: number;
  /** Runs on this set before this one: 0 is the set's own hue, each further run lighter. */
  shade: number;
  sameTires: boolean;
  color: string;
  dash: string;
  /** `Run 2` or `Run 3 · same tires`. */
  name: string;
}

/** The hue of a tire set, lightened `shade` steps toward white (a CSS value that follows the run tokens). */
export function shadeColour(set: number, shade: number): string {
  const base = runColour(set);
  if (shade <= 0) return base;
  return `color-mix(in srgb, ${base} ${Math.max(10, Math.round(100 * 0.45 ** shade))}%, white)`;
}

/**
 * Run identity by tire set from the backend's stops: a run follows the run before it on the same tires when the stop that ended that
 * run observed no tire changed (`tires_changed` reported and empty). A stop whose tires were not reported starts a new identity, so
 * no shared set is claimed without the record.
 */
export function runIdentities(runNumbers: readonly number[], stops: readonly Pick<PitStop, "runNumber" | "tires" | "postRun">[]): Map<number, RunIdentity> {
  const ordered = [...new Set(runNumbers)].filter((run) => Number.isInteger(run) && run >= 1).sort((a, b) => a - b);
  const out = new Map<number, RunIdentity>();
  let set = 0;
  let shade = 0;
  let previous: number | null = null;
  for (const run of ordered) {
    const stop = previous !== null ? stops.find((item) => item.runNumber === previous && !item.postRun) : undefined;
    const same = previous !== null && run === previous + 1 && !!stop && stop.tires !== null && stop.tires.size === 0;
    if (same) shade += 1;
    else { set += 1; shade = 0; }
    out.set(run, { runNumber: run, set, shade, sameTires: same, color: shadeColour(set, shade), dash: runDash(run), name: same ? `Run ${run} · same tires` : `Run ${run}` });
    previous = run;
  }
  return out;
}

/** The identity of `run`, or the muted colour for a lap outside every run. */
export function identityColour(identities: ReadonlyMap<number, RunIdentity>, run: number | null | undefined): string {
  return (run !== null && run !== undefined ? identities.get(run)?.color : undefined) ?? runColour(run ?? null);
}

function backendRun(runs: readonly unknown[], runNumber: number): Record<string, unknown> | null {
  for (const raw of runs) {
    const run = asRecord(raw);
    if (num(run.run_number) === runNumber) return run;
  }
  return null;
}

/** Fuel in the car at the run's start, gal, from the runs view (`fuel.start_l`); null when not reported. */
export function fuelStartGal(runs: readonly unknown[], runNumber: number): number | null {
  const liters = num(asRecord(backendRun(runs, runNumber)?.fuel).start_l);
  return liters === null ? null : liters / LITERS_PER_GALLON;
}

/** `L3–19`: the laps the backend's fit used, else the run's own laps. */
export function runSpan(run: RunView): string {
  const laps = run.fit?.laps ?? [];
  const [a, b] = laps.length ? [Math.min(...laps), Math.max(...laps)] : [run.firstLap, run.lastLap];
  return a === b ? `L${a}` : `L${a}–${b}`;
}

/** The line under the Race chart: `Run 1 · L3–19 · +0.041 s/lap · 17.4 gal`; a figure the backend did not report is left out. */
export function runLine(run: RunView, identity: RunIdentity | undefined, runs: readonly unknown[]): string {
  const fuel = fuelStartGal(runs, run.runNumber);
  return [identity?.name ?? `Run ${run.runNumber}`, runSpan(run), slopeText(run), fuel !== null ? `${fuel.toFixed(1)} gal` : null].filter(Boolean).join(" · ");
}

/** `Pace fit: 31 of 41 laps`: laps the backend's fits used over the laps drawn as pace dots. Null when nothing is drawn. */
export function paceFitText(inv: Investigation, plotted: number): string | null {
  if (plotted <= 0) return null;
  const used = inv.runs.reduce((sum, run) => sum + (run.fit && fitSegment(run) ? run.fit.eligibleCount : 0), 0);
  return `Pace fit: ${used} of ${plotted} laps`;
}

export interface SparkPoint {
  x: number;
  y: number;
  used: boolean;
}

export interface RunCard {
  runNumber: number;
  lapsText: string;
  /** The run's laps on the Runs chart's axes (green laps into run, lap time) for the card's dot sparkline. */
  points: SparkPoint[];
  fit: { x1: number; y1: number; x2: number; y2: number } | null;
  slope: string | null;
  noFitReason: string | null;
  used: number;
  screened: number;
  /** `15.9 → 3.8 gal` from the runs view's fuel at start and end; the range stays a range. Null when not reported. */
  fuelBand: string | null;
  /** Green laps on the tires at the run's end, per corner: one figure, or a range when the corners differ. Null when not reported. */
  tireLaps: string | null;
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

/** Laps the Runs chart draws: pace laps with a reported exposure and a lap time. */
export function runsChartLaps(laps: readonly InvestigationLap[]): InvestigationLap[] {
  return laps.filter((lap) => lap.plotPace && lap.greenExposure !== null && lap.lapTimeS !== null && lap.lapTimeS > 0);
}

/** One card per run: dot sparkline with the fit, slope, laps used and screened, fuel band, tire laps. Counts are of the backend's lap flags. */
export function runCards(inv: Investigation, runs: readonly unknown[]): RunCard[] {
  const drawn = runsChartLaps(inv.laps);
  return inv.runs.map((run) => {
    const laps = inv.laps.filter((lap) => lap.runNumber === run.runNumber && lap.plotPace);
    const raw = backendRun(runs, run.runNumber);
    return {
      runNumber: run.runNumber,
      lapsText: `L${run.firstLap}–${run.lastLap}`,
      points: drawn.filter((lap) => lap.runNumber === run.runNumber).map((lap) => ({ x: lap.greenExposure as number, y: lap.lapTimeS as number, used: lap.eligible })),
      fit: fitSegment(run),
      slope: fitLabel(run),
      noFitReason: fitLabel(run) === null ? run.fitUnavailableReason ?? "No fit" : null,
      used: laps.filter((lap) => lap.eligible).length,
      screened: laps.filter((lap) => !lap.eligible).length,
      fuelBand: fuelBand(raw),
      tireLaps: tireLaps(raw),
    };
  });
}

/** The card sparkline in a `width` × `height` box (y down, slower up): dot centres and the fit's two ends, on the run's own scale. */
export function sparkGeometry(card: Pick<RunCard, "points" | "fit">, width: number, height: number): { dots: { cx: number; cy: number; used: boolean }[]; fit: [number, number, number, number] | null } {
  const xs = [...card.points.map((p) => p.x), ...(card.fit ? [card.fit.x1, card.fit.x2] : [])];
  const ys = [...card.points.map((p) => p.y), ...(card.fit ? [card.fit.y1, card.fit.y2] : [])];
  if (xs.length === 0) return { dots: [], fit: null };
  const [x0, x1] = [Math.min(...xs), Math.max(...xs)];
  const [y0, y1] = [Math.min(...ys), Math.max(...ys)];
  const pad = 2.5;
  const sx = (x: number): number => pad + ((x - x0) / Math.max(1e-9, x1 - x0)) * (width - 2 * pad);
  const sy = (y: number): number => height - pad - ((y - y0) / Math.max(1e-9, y1 - y0)) * (height - 2 * pad);
  const r = (value: number): number => Math.round(value * 10) / 10;
  return {
    dots: card.points.map((p) => ({ cx: r(sx(p.x)), cy: r(sy(p.y)), used: p.used })),
    fit: card.fit ? [r(sx(card.fit.x1)), r(sy(card.fit.y1)), r(sx(card.fit.x2)), r(sy(card.fit.y2))] : null,
  };
}

/**
 * The Runs chart's run toggle: hides or shows `run`; the last visible run stays visible. Returns the hidden set unchanged when the
 * click would hide every run.
 */
export function toggleRun(hidden: ReadonlySet<number>, run: number, all: readonly number[]): Set<number> {
  const next = new Set(hidden);
  if (next.has(run)) next.delete(run);
  else next.add(run);
  const visible = all.filter((item) => !next.has(item));
  return all.length > 0 && visible.length === 0 ? new Set(hidden) : next;
}

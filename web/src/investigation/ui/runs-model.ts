import { runColour, runDash } from "../../core/palette";
import type { PitStop } from "../geometry/rail-model";
import type { Investigation, InvestigationLap, RunView } from "../investigation-model";
import { asRecord, num } from "../types";
import { TIRE_IDS, type TireId } from "../../ui/pictograms";
import { lapKind, type LapKind } from "./traffic";

const LITERS_PER_GALLON = 3.785411784;

/**
 * Runs as the Race and Runs charts draw them (round 3, #40 #42 #47; round 6, #90 #91 #93): one straight line per run, the server's
 * observed trend (`BackendRun.observed_trend`: least squares of lap time on lap over the run's green, complete laps, traffic
 * included, no pit in or out, caution, restart, off-track, tow or incident lap), drawn only between its first and last counted lap;
 * run identity by tire set; one line of facts per run; the run toggles. The browser reads the server's line and draws it; it never
 * fits one. The backend's screened pace fit is not drawn.
 */

export interface ObservedTrend {
  /** Seconds per lap; positive is slowing. */
  slopeSPerLap: number;
  /** The line's time at lap 0: the time at lap `x` is `interceptS + slopeSPerLap * x`. */
  interceptS: number;
  firstLap: number;
  lastLap: number;
  lapsUsed: number;
}

/** The server's observed trend of `runNumber` from the runs view; null when it sent none or a malformed one. */
export function observedTrend(runs: readonly unknown[], runNumber: number): ObservedTrend | null {
  const raw = backendRun(runs, runNumber)?.observed_trend;
  if (raw === null || raw === undefined) return null;
  const t = asRecord(raw);
  const slope = num(t.slope_s_per_lap);
  const intercept = num(t.intercept_s);
  const first = num(t.first_lap);
  const last = num(t.last_lap);
  const used = num(t.laps_used);
  if (slope === null || intercept === null || first === null || last === null || used === null || !(last > first)) return null;
  return { slopeSPerLap: slope, interceptS: intercept, firstLap: first, lastLap: last, lapsUsed: used };
}

/** The trend's lap time at lap `lap`. */
export function trendAt(trend: ObservedTrend, lap: number): number {
  return trend.interceptS + trend.slopeSPerLap * lap;
}

/** `+0.06 s/lap`: the trend's slope, the label at the line's end. */
export function trendText(trend: ObservedTrend): string {
  return perLapText(trend.slopeSPerLap);
}

/** `+0.06 s/lap · 16 laps`: the slope and the laps it was fitted over (the Runs chart label and the card's Fit). */
export function trendLabel(trend: ObservedTrend): string {
  return `${trendText(trend)} · ${trend.lapsUsed} laps`;
}

/** The hover of a trend: `observed trend · L11–27 · 16 laps, traffic included`. */
export function trendHover(trend: ObservedTrend): string {
  return `observed trend · L${trend.firstLap}–${trend.lastLap} · ${trend.lapsUsed} laps, traffic included`;
}

/**
 * The trend on the Runs chart's axis (green laps into run): its two ends placed at the green exposure the backend reported for its
 * first and last lap. Null when either end has no reported exposure.
 */
export function trendOnExposure(trend: ObservedTrend, laps: readonly InvestigationLap[]): { x1: number; y1: number; x2: number; y2: number } | null {
  const at = (lap: number): number | null => laps.find((item) => item.lap === lap)?.greenExposure ?? null;
  const x1 = at(trend.firstLap);
  const x2 = at(trend.lastLap);
  if (x1 === null || x2 === null || !(x2 > x1)) return null;
  return { x1, y1: trendAt(trend, trend.firstLap), x2, y2: trendAt(trend, trend.lastLap) };
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

/**
 * The run's green laps as the Race chart draws them (the backend's `plot_pace`: complete, green, no pit, timed), traffic included,
 * in lap order. Observations, not the screened set.
 */
export function greenLaps(run: Pick<RunView, "runNumber">, laps: readonly InvestigationLap[]): InvestigationLap[] {
  return laps.filter((lap) => lap.runNumber === run.runNumber && lap.plotPace && lap.lapTimeS !== null && lap.lapTimeS > 0).sort((a, b) => a.lap - b.lap);
}

/** `+0.07 s/lap`, `−0.02 s/lap`. */
export function perLapText(value: number, digits = 2): string {
  const text = Math.abs(value).toFixed(digits);
  const sign = Number(text) === 0 ? "" : value > 0 ? "+" : "−";
  return `${sign}${text} s/lap`;
}

/** Green-flag burn per lap from the runs view (`fuel.green_gal_per_lap`, else `green_l_per_lap` in gallons); null when not reported. */
export function burnGalPerLap(runs: readonly unknown[], runNumber: number): number | null {
  const fuel = asRecord(backendRun(runs, runNumber)?.fuel);
  const gal = num(fuel.green_gal_per_lap);
  if (gal !== null && gal > 0) return gal;
  const liters = num(fuel.green_l_per_lap);
  return liters !== null && liters > 0 ? liters / LITERS_PER_GALLON : null;
}

/** `L3–24`: the run's green laps, first to last; the run's own laps when it has none. */
export function runSpan(run: RunView, laps: readonly InvestigationLap[] = []): string {
  const green = greenLaps(run, laps);
  const [a, b] = green.length ? [green[0]!.lap, green[green.length - 1]!.lap] : [run.firstLap, run.lastLap];
  return a === b ? `L${a}` : `L${a}–${b}`;
}

export interface RunLinePart {
  key: "name" | "span" | "falloff" | "burn";
  text: string;
  /** The hover of the part (the fall-off's observed-trend span and lap count). */
  hover?: string;
}

/**
 * The line under the Race chart (#59a, #90): `Run 1 · L3–24 · fall-off +0.07 s/lap · 0.33 gal/lap`, the fall-off being the slope of the
 * server's observed trend, the same figure as the line's label. A figure the server did not send is left out.
 */
export function runLineParts(run: RunView, identity: RunIdentity | undefined, runs: readonly unknown[], laps: readonly InvestigationLap[]): RunLinePart[] {
  const parts: RunLinePart[] = [{ key: "name", text: identity?.name ?? `Run ${run.runNumber}` }, { key: "span", text: runSpan(run, laps) }];
  const trend = observedTrend(runs, run.runNumber);
  if (trend) parts.push({ key: "falloff", text: `fall-off ${trendText(trend)}`, hover: trendHover(trend) });
  const burn = burnGalPerLap(runs, run.runNumber);
  if (burn !== null) parts.push({ key: "burn", text: `${burn.toFixed(2)} gal/lap` });
  return parts;
}

export function runLine(run: RunView, identity: RunIdentity | undefined, runs: readonly unknown[], laps: readonly InvestigationLap[]): string {
  return runLineParts(run, identity, runs, laps).map((part) => part.text).join(" · ");
}

/** The readout of a run line on hover or tap: `Run 1 · fall-off +0.07 s/lap · observed trend · L3–24 · 20 laps, traffic included · 0.33 gal/lap`. */
export function runLineReadout(parts: readonly RunLinePart[]): string {
  return parts.filter((part) => part.key !== "span").flatMap((part) => (part.hover ? [part.text, part.hover] : [part.text])).join(" · ");
}

export interface SparkPoint {
  x: number;
  y: number;
  /** The lap's mark on the chart: open air (in the fit), screened for traffic alone, screened for anything else (#67). */
  kind: LapKind;
}

export interface RunCard {
  runNumber: number;
  lapsText: string;
  /** The run's laps on the Runs chart's axes (green laps into run, lap time) for the card's dot sparkline. */
  points: SparkPoint[];
  /** The observed trend on the card's axes (green laps into run): null when the server sent none. */
  fit: { x1: number; y1: number; x2: number; y2: number } | null;
  /** `+0.06 s/lap · 18 laps`: the observed trend's slope and laps (#93); null without one. */
  slope: string | null;
  /** `observed trend · L11–27 · 16 laps, traffic included`; null without one. */
  slopeHover: string | null;
  used: number;
  screened: number;
  /** `15.9 → 3.8 gal` from the runs view's fuel at start and end; the range stays a range. Null when not reported. */
  fuelBand: string | null;
  /** Green laps on the tires at the run's end, per corner: one figure, or a range when the corners differ. Null when not reported. */
  tireLaps: string | null;
  /** The four-tire picture at the run's end; null when the run has no measured tire reading. */
  tires: RunTires | null;
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

/** One card per run: dot sparkline with the observed trend, its slope, laps used and screened, fuel band, tire laps. Counts are of the backend's lap flags. */
export function runCards(inv: Investigation, runs: readonly unknown[]): RunCard[] {
  const drawn = runsChartLaps(inv.laps);
  return inv.runs.map((run) => {
    const laps = inv.laps.filter((lap) => lap.runNumber === run.runNumber && lap.plotPace);
    const raw = backendRun(runs, run.runNumber);
    const trend = observedTrend(runs, run.runNumber);
    return {
      runNumber: run.runNumber,
      lapsText: `L${run.firstLap}–${run.lastLap}`,
      points: drawn.filter((lap) => lap.runNumber === run.runNumber).map((lap) => ({ x: lap.greenExposure as number, y: lap.lapTimeS as number, kind: lapKind(lap) })),
      fit: trend ? trendOnExposure(trend, inv.laps) : null,
      slope: trend ? trendLabel(trend) : null,
      slopeHover: trend ? trendHover(trend) : null,
      used: laps.filter((lap) => lap.eligible).length,
      screened: laps.filter((lap) => !lap.eligible).length,
      fuelBand: fuelBand(raw),
      tireLaps: tireLaps(raw),
      tires: runTires(raw, run.lastLap),
    };
  });
}

/** The card sparkline in a `width` × `height` box (y down, slower up): dot centres and the fit's two ends, on the run's own scale. */
export function sparkGeometry(card: Pick<RunCard, "points" | "fit">, width: number, height: number): { dots: { cx: number; cy: number; kind: LapKind }[]; fit: [number, number, number, number] | null } {
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
    dots: card.points.map((p) => ({ cx: r(sx(p.x)), cy: r(sy(p.y)), kind: p.kind })),
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

/**
 * The run's four-tire picture (G8): the tires as the stop that ended the run read them. iRacing exposes tire readings only during
 * pit service, so `tire_observation` is the run's END state and says nothing about the laps before it; the picture is labelled with
 * the run's last lap (`at L47`). Absent (`stale_or_unconfirmed_at_stop`, `unmeasured_final_run`) the run has no picture, never a
 * guess. Every figure is the server's: remaining percent per band, surface temperature per band, the lowest tire, laps on the set.
 * Bands keep the sim's L/M/R order, which is also the picture's left to right: a left tire reads outside, middle, inside and a
 * right tire inside, middle, outside (Q7).
 */
export interface TireBand {
  /** Tread left, 0–100; null when the band was not read. */
  remaining: number | null;
  /** Surface temperature, °F; null when not read. */
  surfaceF: number | null;
}

export interface TireCorner {
  id: TireId;
  /** Left, middle, right of the tire as the sim names the bands. */
  bands: [TireBand, TireBand, TireBand];
  /** Pressure after the stop, psi, with the sim's kind (`live` or `cold`); null when not read. */
  pressure: { psi: number; kind: string | null } | null;
}

export interface RunTires {
  /** Always the four corners in diagram order (LF, RF, LR, RR); a corner not read has empty bands. */
  corners: TireCorner[];
  /** The lowest tire and its tread left, from the server; null when it sent none. */
  lowest: { tire: TireId; percent: number } | null;
  /** Green laps on the lowest tire's set; null when the server sent none. */
  lapsOnSet: number | null;
  /** The lap the reading is read at: the run's last lap. */
  atLap: number;
  /** Coolest and warmest surface band read, °F; null when none. */
  surfaceRange: [number, number] | null;
  /** `RF 61 % · 23 laps`; just what is known. */
  caption: string;
  /** `at L47 · 171–204 °F`. */
  atText: string;
  /** One line per tire: `RF · I 61 M 63 O 58 % · I 171 M 180 O 204 °F · 32.1 psi live`. */
  hover: string;
}

const BAND_KEYS = ["L", "M", "R"] as const;

/** The band names left to right for a tire: a left tire is outside, middle, inside; a right tire inside, middle, outside. */
export function bandNames(id: TireId): [string, string, string] {
  return id === "LF" || id === "LR" ? ["O", "M", "I"] : ["I", "M", "O"];
}

function percentOf(value: unknown): number | null {
  const v = num(value);
  return v === null ? null : Math.min(100, Math.max(0, v));
}

function lapsText(value: number): string {
  const text = Number.isInteger(value) ? String(value) : value.toFixed(1);
  return `${text} ${value === 1 ? "lap" : "laps"}`;
}

/** The run's tire picture from the runs view's run; null when the run has no measured tire reading. */
export function runTires(run: Record<string, unknown> | null, atLap: number): RunTires | null {
  const observation = asRecord(run?.tire_observation);
  const tires = asRecord(observation.tires);
  const corners: TireCorner[] = TIRE_IDS.map((id) => {
    const tire = asRecord(tires[id]);
    const wear = asRecord(tire.remaining_percent);
    const surface = asRecord(tire.surface_temperature_f);
    const pressure = asRecord(tire.pressure);
    const psi = num(pressure.psi);
    return {
      id,
      bands: BAND_KEYS.map((key) => ({ remaining: percentOf(wear[key]), surfaceF: num(surface[key]) })) as [TireBand, TireBand, TireBand],
      pressure: psi === null ? null : { psi, kind: typeof pressure.kind === "string" ? pressure.kind : null },
    };
  });
  if (!corners.some((corner) => corner.bands.some((band) => band.remaining !== null))) return null;
  const lowestId = TIRE_IDS.find((id) => id === observation.lowest_remaining_tire);
  const lowestPercent = percentOf(observation.lowest_remaining_percent);
  const lowest = lowestId && lowestPercent !== null ? { tire: lowestId, percent: lowestPercent } : null;
  const setLaps = lowest ? num(asRecord(asRecord(asRecord(run?.tire_set_lifecycle).corners)[lowest.tire]).green_laps_on_set) : null;
  const lapsOnSet = setLaps !== null && setLaps >= 0 ? setLaps : null;
  const temps = corners.flatMap((corner) => corner.bands.map((band) => band.surfaceF)).filter((value): value is number => value !== null);
  const surfaceRange: [number, number] | null = temps.length ? [Math.min(...temps), Math.max(...temps)] : null;
  const caption = [lowest ? `${lowest.tire} ${Math.round(lowest.percent)} %` : null, lapsOnSet !== null ? lapsText(lapsOnSet) : null].filter(Boolean).join(" · ");
  const temp = surfaceRange ? (Math.round(surfaceRange[0]) === Math.round(surfaceRange[1]) ? `${Math.round(surfaceRange[0])} °F` : `${Math.round(surfaceRange[0])}–${Math.round(surfaceRange[1])} °F`) : null;
  const atText = [`at L${atLap}`, temp].filter(Boolean).join(" · ");
  const join = (corner: TireCorner, pick: (band: TireBand) => number | null): string => {
    const names = bandNames(corner.id);
    return corner.bands.flatMap((band, index) => { const v = pick(band); return v === null ? [] : [`${names[index]} ${Math.round(v)}`]; }).join(" ");
  };
  const hover = corners.map((corner) => {
    const wear = join(corner, (band) => band.remaining);
    const heat = join(corner, (band) => band.surfaceF);
    const psi = corner.pressure ? `${corner.pressure.psi.toFixed(1)} psi${corner.pressure.kind ? ` ${corner.pressure.kind}` : ""}` : "";
    return [corner.id, wear ? `${wear} %` : "", heat ? `${heat} °F` : "", psi].filter(Boolean).join(" · ");
  }).join("\n");
  return { corners, lowest, lapsOnSet, atLap, surfaceRange, caption, atText: `${atText}`, hover: `Read at the stop after L${atLap} (I inside, M middle, O outside)\n${hover}` };
}

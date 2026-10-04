import { reasonsText, signedText } from "../format";
import { hasPaceResult, type Cohort, type Comparison, type Corner, type Investigation, type InvestigationLap } from "../investigation-model";

/**
 * The comparison as a picture (web round 2, Investigation): names from the groups, brackets split around omitted laps, the lap
 * dots of each group, the paired input bars and the one-line no-result. Everything is read from the backend's comparison and laps;
 * nothing here recomputes a median, a delta or a screening.
 */

export type GroupRole = "earlier" | "later";

/** Contiguous lap ranges in ascending order: `[61, 62, 64, 65]` → `[[61, 62], [64, 65]]`. */
export function lapRanges(laps: readonly number[]): [number, number][] {
  const ordered = [...new Set(laps)].sort((a, b) => a - b);
  const out: [number, number][] = [];
  for (const lap of ordered) {
    const last = out[out.length - 1];
    if (last && lap === last[1] + 1) last[1] = lap;
    else out.push([lap, lap]);
  }
  return out;
}

/** `L61–65`, `L61–63, 65`, `L7`; empty for no laps. */
export function rangesText(laps: readonly number[]): string {
  const ranges = lapRanges(laps);
  if (ranges.length === 0) return "";
  return `L${ranges.map(([a, b]) => (a === b ? `${a}` : `${a}–${b}`)).join(", ")}`;
}

/** A group named by its run (`Run 3`), else by its laps. */
export function groupName(cohort: Cohort): string {
  return cohort.runNumber !== null ? `Run ${cohort.runNumber}` : rangesText(cohort.lapNumbers);
}

/**
 * The comparison named by its groups: one run → `Run 3: L61–65 → L85–89`; two runs → `Run 2 → Run 3`; no run → `L1–5 → L36–40`.
 */
export function comparisonName(c: Comparison): string {
  const a = c.baseline;
  const b = c.compared;
  if (a.runNumber !== null && a.runNumber === b.runNumber) return `Run ${a.runNumber}: ${rangesText(a.lapNumbers)} → ${rangesText(b.lapNumbers)}`;
  if (a.runNumber !== null && b.runNumber !== null) return `Run ${a.runNumber} → Run ${b.runNumber}`;
  return `${rangesText(a.lapNumbers)} → ${rangesText(b.lapNumbers)}`;
}

/** The laps line under a name that does not already carry them (two runs): `L3–5 → L31–33`; null otherwise. */
export function comparisonLaps(c: Comparison): string | null {
  const a = c.baseline;
  const b = c.compared;
  if (a.runNumber !== null && b.runNumber !== null && a.runNumber !== b.runNumber) return `${rangesText(a.lapNumbers)} → ${rangesText(b.lapNumbers)}`;
  return null;
}

/** The group a lap belongs to in the selected comparison, or null. */
export function groupOf(c: Comparison | null, lap: number): GroupRole | null {
  if (!c) return null;
  if (c.baseline.lapNumbers.includes(lap)) return "earlier";
  if (c.compared.lapNumbers.includes(lap)) return "later";
  return null;
}

/** Laps of both groups, `earlier` or `later` by lap number. */
export function groupMap(c: Comparison | null): Map<number, GroupRole> {
  const out = new Map<number, GroupRole>();
  if (!c) return out;
  for (const lap of c.baseline.lapNumbers) out.set(lap, "earlier");
  for (const lap of c.compared.lapNumbers) out.set(lap, "later");
  return out;
}

export interface Bracket {
  role: GroupRole;
  label: string;
  run: number | null;
  /** One piece per contiguous range: the bracket is split around omitted laps. */
  pieces: [number, number][];
}

/** The two labelled brackets under the lap groups: `Earlier L61–65`, `Later L85–89`, each split around omitted laps. */
export function brackets(c: Comparison | null): Bracket[] {
  if (!c) return [];
  return ([["earlier", c.baseline], ["later", c.compared]] as const)
    .filter(([, cohort]) => cohort.lapNumbers.length > 0)
    .map(([role, cohort]) => ({ role, label: `${role === "earlier" ? "Earlier" : "Later"} ${rangesText(cohort.lapNumbers)}`, run: cohort.runNumber, pieces: lapRanges(cohort.lapNumbers) }));
}

/**
 * Places bracket labels under a chart: each centred on its group, kept inside [left, right], and moved to a second line when it would
 * overlap the label before it (a narrow chart). `widths` are the labels' estimated widths in the same units.
 */
export function placeLabels(centres: readonly number[], widths: readonly number[], left: number, right: number, gap = 8): { x: number; line: 0 | 1 }[] {
  const out: { x: number; line: 0 | 1 }[] = [];
  centres.forEach((centre, index) => {
    const half = (widths[index] ?? 0) / 2;
    const x = Math.min(Math.max(centre, left + half), Math.max(left + half, right - half));
    const previous = out[index - 1];
    const overlaps = previous !== undefined && previous.line === 0 && x - half < previous.x + (widths[index - 1] ?? 0) / 2 + gap;
    out.push({ x, line: overlaps ? 1 : 0 });
  });
  return out;
}

/** `slower`, `faster` or `equal` for the later group, from the backend's signed difference; null without a pace result. */
export function direction(c: Comparison): "slower" | "faster" | "equal" | null {
  if (!hasPaceResult(c)) return null;
  const delta = c.deltaS as number;
  return delta > 0 ? "slower" : delta < 0 ? "faster" : "equal";
}

/** The one line shown instead of the picture when the backend has no pace result: `No pace result · every lap screened · close traffic`. */
export function noResultLine(c: Comparison): string {
  const reasons = [reasonsText(c.paceUnusableReasons), reasonsText([...c.baseline.excludedLaps, ...c.compared.excludedLaps].flatMap((lap) => lap.reasons))].filter(Boolean);
  return ["No pace result", ...new Set(reasons)].join(" · ");
}

/** The signed difference as the picture prints it: `+0.104`. */
export function deltaText(c: Comparison): string {
  return hasPaceResult(c) ? signedText(c.deltaS) : "—";
}

export interface GroupDot {
  lap: number;
  lapTimeS: number;
  /** Used in the pace comparison (the backend's eligible laps of the group); a hollow dot otherwise. */
  used: boolean;
  reasons: string[];
}

export interface GroupRow {
  role: GroupRole;
  cohort: Cohort;
  dots: GroupDot[];
  green: number;
  total: number;
}

/** Each group's laps as dots on the lap-time scale, with green over total counts; lap times are the backend's. */
export function groupRows(c: Comparison, laps: readonly InvestigationLap[]): GroupRow[] {
  const byLap = new Map(laps.map((lap) => [lap.lap, lap] as const));
  return ([["earlier", c.baseline], ["later", c.compared]] as const).map(([role, cohort]) => {
    const excluded = new Map(cohort.excludedLaps.map((lap) => [lap.lap, lap.reasons] as const));
    const eligible = new Set(cohort.eligibleLapNumbers);
    const dots: GroupDot[] = [];
    let green = 0;
    for (const number of cohort.lapNumbers) {
      const lap = byLap.get(number);
      if (lap?.flagState === "green") green += 1;
      if (!lap || lap.lapTimeS === null || !(lap.lapTimeS > 0)) continue;
      dots.push({ lap: number, lapTimeS: lap.lapTimeS, used: eligible.has(number), reasons: excluded.get(number) ?? (eligible.has(number) ? [] : lap.exclusionReasons) });
    }
    return { role, cohort, dots, green, total: cohort.lapCount };
  });
}

/** One shared lap-time domain for both rows: every dot and both medians, padded. */
export function dotDomain(rows: readonly GroupRow[]): [number, number] | null {
  const values = rows.flatMap((row) => [...row.dots.map((dot) => dot.lapTimeS), ...(row.cohort.medianLapS !== null ? [row.cohort.medianLapS] : [])]);
  if (values.length === 0) return null;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const pad = Math.max(0.02, (max - min) * 0.08);
  return [min - pad, max + pad];
}

export interface PairedMetric {
  key: "fuel" | "throttle" | "traffic";
  label: string;
  /** The label where space is short (the Traces strip). */
  short: string;
  unit: string;
  earlier: number | null;
  later: number | null;
  /** The bar scale runs from zero to this. */
  max: number;
  digits: number;
}

/** Rounds a scale end up to 1, 2, 2.5 or 5 times a power of ten. */
export function niceMax(value: number): number {
  if (!(value > 0)) return 1;
  const magnitude = Math.pow(10, Math.floor(Math.log10(value)));
  return ([1, 2, 2.5, 5, 10].map((factor) => factor * magnitude).find((candidate) => candidate >= value - 1e-9) ?? value);
}

/**
 * The paired bars beside the dots: the backend's starting fuel (the groups' `fuel_start_gal`, else the `fuel` metric), average
 * throttle (`throttle`) and close-traffic exposure (`traffic`), each on its own scale from zero. A metric the backend did not
 * report is left out.
 */
export function pairedMetrics(c: Comparison): PairedMetric[] {
  const metric = (key: string) => c.metrics.find((item) => item.key === key) ?? null;
  const out: PairedMetric[] = [];
  const fuel = metric("fuel");
  const fuelA = c.baseline.fuelStartGal ?? fuel?.baseline ?? null;
  const fuelB = c.compared.fuelStartGal ?? fuel?.compared ?? null;
  if (fuelA !== null || fuelB !== null) out.push({ key: "fuel", label: "Fuel at start", short: "Fuel", unit: "gal", earlier: fuelA, later: fuelB, max: niceMax(Math.max(fuelA ?? 0, fuelB ?? 0)), digits: 1 });
  const throttle = metric("throttle");
  if (throttle && (throttle.baseline !== null || throttle.compared !== null)) out.push({ key: "throttle", label: "Throttle", short: "Throttle", unit: "%", earlier: throttle.baseline, later: throttle.compared, max: 100, digits: 0 });
  const traffic = metric("traffic");
  if (traffic && (traffic.baseline !== null || traffic.compared !== null)) out.push({ key: "traffic", label: "Close traffic", short: "Traffic", unit: "%", earlier: traffic.baseline, later: traffic.compared, max: 100, digits: 0 });
  return out;
}

/**
 * Why a lap is not in its run's pace fit, naming the fit: `Not used in Run 1 pace fit · close traffic`. Null for a lap the fit used.
 */
export function screenedText(lap: Pick<InvestigationLap, "eligible" | "exclusionReasons" | "runNumber">): string | null {
  if (lap.eligible) return null;
  const fit = lap.runNumber !== null ? `Run ${lap.runNumber} pace fit` : "pace fit";
  const why = reasonsText(lap.exclusionReasons.filter((code) => code !== "partial"));
  return `Not used in ${fit}${why ? ` · ${why}` : ""}`;
}

/** Gain or loss for a section's time against the selected comparison; neutral when the comparison has no pace result or the section too few passes. */
export function sectionTone(c: Comparison | null, corner: Corner | undefined): "gain" | "loss" | "neutral" {
  if (!c || !corner || !hasPaceResult(c) || corner.deltaS === null || corner.baselineCount < 1 || corner.comparedCount < 1) return "neutral";
  return corner.deltaS < 0 ? "gain" : corner.deltaS > 0 ? "loss" : "neutral";
}

/** The investigation's laps by number. */
export function lapsByNumber(inv: Investigation): Map<number, InvestigationLap> {
  return new Map(inv.laps.map((lap) => [lap.lap, lap] as const));
}

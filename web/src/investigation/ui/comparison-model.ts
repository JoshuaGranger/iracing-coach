import { reasonsText, signedText } from "../format";
import { hasPaceResult, type Action, type Cohort, type Comparison, type Corner, type Investigation, type InvestigationLap } from "../investigation-model";

/**
 * The backend's comparisons as Traces and Sections show them (round 3: the comparison machinery left the Race tab, #43 #45): names
 * from the groups, the rail's group marks, the paired input bars and the one-line no-result. Everything is read from the backend's
 * comparison and laps; nothing here recomputes a median, a delta or a screening.
 */

/**
 * The "Inspect section" entry that left the Race tab (#45), for the Sections tab to offer: the selected comparison's lead action
 * (lowest priority number), labelled `Inspect section B` when it names a section, else `Open in Traces`; null without an action.
 * The Race tab used to run it as: a section id selects that section on Sections; otherwise Traces opens the action's laps over
 * `startPct`–`endPct` with its `signalKeys`.
 */
export function inspectSectionEntry(c: Comparison | null): { label: string; sectionId: string | null; action: Action } | null {
  const lead = c?.actions.slice().sort((a, b) => a.priority - b.priority)[0] ?? null;
  if (!lead) return null;
  return { label: lead.sectionId ? `Inspect section ${lead.sectionId}` : "Open in Traces", sectionId: lead.sectionId, action: lead };
}

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

/** Gain or loss for a section's time against the selected comparison; neutral when the comparison has no pace result or the section too few passes. */
export function sectionTone(c: Comparison | null, corner: Corner | undefined): "gain" | "loss" | "neutral" {
  if (!c || !corner || !hasPaceResult(c) || corner.deltaS === null || corner.baselineCount < 1 || corner.comparedCount < 1) return "neutral";
  return corner.deltaS < 0 ? "gain" : corner.deltaS > 0 ? "loss" : "neutral";
}

/** The investigation's laps by number. */
export function lapsByNumber(inv: Investigation): Map<number, InvestigationLap> {
  return new Map(inv.laps.map((lap) => [lap.lap, lap] as const));
}

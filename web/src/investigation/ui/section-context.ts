import { asArray, asRecord, num, type RaceViewData } from "../types";
import { lapTraffic, sideText, trafficText, type LapTraffic } from "./traffic";

/**
 * Context columns on the Sections lap rows (backlog #75): how old the tires were and how much traffic the lap had, so a fast lap on
 * fresh tires in open air reads differently from a fast lap on old tires in traffic. Both are read-only facts the backend reports:
 *
 * - Tires: the runs view's `tire_set_lifecycle.corners` gives, per corner, the green and caution laps on the set at the run's end.
 *   The oldest corner is the set's age; a lap earlier in the run is that age less the run's laps after it (the run's own lap list).
 *   No lifecycle, or a count the run's laps contradict, gives no figure.
 * - Traffic: the race view's `clean_context.traffic_proximity_fraction` and, when the backend names it, the side (`./traffic`): in
 *   words as every traffic readout says them (`ahead`, `behind`, `both sides`), drawn as the Race chart's mark where the column is
 *   narrow (sections.css).
 * - Green exposure (gap ledger G6): the race view's `laps[].run_age_laps`, in the row's hover; `greenText` and `entryText` format
 *   the green-lap and entry-speed cells for the rows.
 */

export interface LapContext {
  /** Laps on the current tire set at the end of this lap (oldest corner); null when the backend has no lifecycle for the run. */
  tires: number | null;
  traffic: LapTraffic | null;
  /**
   * Green exposure (gap ledger G6): the race view's `laps[].run_age_laps`, the backend's green-lap equivalents into the run at this
   * lap; null when not reported. Optional so contexts built before it read as unknown.
   */
  green?: number | null;
}

/** Green exposure by lap number, from the race view's `laps[].run_age_laps` (a negative figure is not a figure). */
export function lapGreenExposure(race: RaceViewData | null | undefined): Map<number, number> {
  const out = new Map<number, number>();
  for (const item of asArray(asRecord(race).laps)) {
    const lap = asRecord(item);
    const number = num(lap.lap);
    const age = num(lap.run_age_laps);
    if (number !== null && age !== null && age >= 0 && !out.has(number)) out.set(number, age);
  }
  return out;
}

const CORNERS = ["LF", "RF", "LR", "RR"] as const;

/** Laps on the set at the run's end, oldest corner: green plus caution laps; null when no corner reports green laps. */
export function runTireAge(run: unknown): number | null {
  const corners = asRecord(asRecord(asRecord(run).tire_set_lifecycle).corners);
  let oldest: number | null = null;
  for (const corner of CORNERS) {
    const c = asRecord(corners[corner]);
    const green = num(c.green_laps_on_set);
    if (green === null || green < 0) continue;
    const caution = num(c.caution_laps_on_set);
    const age = green + (caution !== null && caution > 0 ? caution : 0);
    if (oldest === null || age > oldest) oldest = age;
  }
  return oldest;
}

/** Laps on the set by lap number, from the runs view's runs. */
export function lapTireAges(runs: readonly unknown[] | null | undefined): Map<number, number> {
  const out = new Map<number, number>();
  for (const run of runs ?? []) {
    const atEnd = runTireAge(run);
    if (atEnd === null) continue;
    const laps = asArray(asRecord(run).lap_numbers).map(num).filter((lap): lap is number => lap !== null);
    const ordered = [...new Set(laps)].sort((a, b) => a - b);
    ordered.forEach((lap, index) => {
      const age = atEnd - (ordered.length - 1 - index);
      // A set younger than one lap at a lap that was driven on it contradicts the run: no figure.
      if (age >= 1 - 1e-9 && !out.has(lap)) out.set(lap, age);
    });
  }
  return out;
}

/** Both columns and the green exposure by lap number. */
export function lapContexts(race: RaceViewData | null | undefined, runs: readonly unknown[] | null | undefined): Map<number, LapContext> {
  const tires = lapTireAges(runs);
  const traffic = lapTraffic(race);
  const green = lapGreenExposure(race);
  const out = new Map<number, LapContext>();
  for (const lap of new Set([...tires.keys(), ...traffic.keys(), ...green.keys()])) out.set(lap, { tires: tires.get(lap) ?? null, traffic: traffic.get(lap) ?? null, green: green.get(lap) ?? null });
  return out;
}

/** Green exposure as a cell: whole green laps into the run (`12`), `—` without a figure. */
export function greenText(green: number | null | undefined): string {
  return green == null ? "—" : `${Math.round(green)}`;
}

/** Entry speed difference to the median lap at the section start as a cell: `+1.4`, `−0.6` (mph), `—` without a figure. */
export function entryText(delta: number | null | undefined): string {
  if (delta == null || !Number.isFinite(delta)) return "—";
  const rounded = Math.round(delta * 10) / 10;
  return (rounded > 0 ? "+" : rounded < 0 ? "−" : "") + Math.abs(rounded).toFixed(1);
}

/** The Tires cell: whole laps, `—` without a figure. */
export function tiresText(tires: number | null | undefined): string {
  return tires == null ? "—" : `${Math.round(tires)}`;
}

/** The Traffic cell's two parts: the share (`75 %`, `—` without a figure) and the side in words, null when not named or no figure. */
export function trafficCell(traffic: LapTraffic | null | undefined): { share: string; side: string | null } {
  if (traffic?.fraction == null) return { share: "—", side: null };
  return { share: `${Math.round(traffic.fraction * 100)} %`, side: traffic.side ? sideText(traffic.side) : null };
}

/** The Traffic cell as text: `75 %`, `75 % · behind` or `75 % · both sides` when the side is known, `—` without a figure. */
export function trafficCellText(traffic: LapTraffic | null | undefined): string {
  const cell = trafficCell(traffic);
  return cell.side ? `${cell.share} · ${cell.side}` : cell.share;
}

/** The row's hover tag for the two columns. */
export function contextTitle(context: LapContext | null | undefined): string {
  const parts: string[] = [];
  if (context?.tires != null) parts.push(`${Math.round(context.tires)} ${Math.round(context.tires) === 1 ? "lap" : "laps"} on tires`);
  if (context?.traffic?.fraction != null) parts.push(trafficText(context.traffic));
  if (context?.green != null) parts.push(`green lap ${Math.round(context.green)} of the run`);
  return parts.join(" · ");
}

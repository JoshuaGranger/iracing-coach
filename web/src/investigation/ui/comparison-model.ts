import { hasPaceResult, type Action, type Cohort, type Comparison, type Corner, type Investigation, type InvestigationLap } from "../investigation-model";

/**
 * The backend's comparisons, read (round 3: the machinery left the Race tab, #43 #45; round 4: it left Traces too, #66, and nothing
 * in the investigation draws an earlier/later group). Names from the groups, the Sections entry and the section tone stay for the
 * model's consumers. Everything is read from the backend's comparison and laps; nothing here recomputes a median, a delta or a
 * screening.
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

/** Gain or loss for a section's time against the selected comparison; neutral when the comparison has no pace result or the section too few passes. */
export function sectionTone(c: Comparison | null, corner: Corner | undefined): "gain" | "loss" | "neutral" {
  if (!c || !corner || !hasPaceResult(c) || corner.deltaS === null || corner.baselineCount < 1 || corner.comparedCount < 1) return "neutral";
  return corner.deltaS < 0 ? "gain" : corner.deltaS > 0 ? "loss" : "neutral";
}

/** The investigation's laps by number. */
export function lapsByNumber(inv: Investigation): Map<number, InvestigationLap> {
  return new Map(inv.laps.map((lap) => [lap.lap, lap] as const));
}

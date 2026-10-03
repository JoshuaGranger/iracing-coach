import type { Comparison } from "../investigation-model";

/** Exposure brackets of the compared cohorts drawn under the runs chart's x axis. */
export function cohortRibbon(comparison: Comparison): { key: string; start: number; end: number; run: number | null }[] {
  const out: { key: string; start: number; end: number; run: number | null }[] = [];
  for (const [name, cohort] of [["baseline", comparison.baseline], ["compared", comparison.compared]] as const) {
    if (cohort.greenAgeStart !== null && cohort.greenAgeEnd !== null) out.push({ key: name, start: cohort.greenAgeStart, end: cohort.greenAgeEnd, run: cohort.runNumber });
  }
  return out;
}

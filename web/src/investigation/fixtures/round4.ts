import { buildRound3Analysis } from "./round3";
import type { SyntheticAnalysis } from "./synthetic";

/**
 * The round-3 synthetic race dressed for the round-4 Race, Runs and Traces checks (#58–#63, #66). Every value is invented; nothing
 * here is race data.
 *
 * - The shape of the 2026-10-02 Las Vegas win around its stop (backlog #63, laps 25–28 there): the service lap belongs to no run
 *   (the backend numbers runs between service events, so the lap that spans the stop is in neither), and the next lap's reading is
 *   the refilled tank. Here that is lap 28. The fuel line runs through it and steps up; it does not break.
 * - One lap whose fuel reading is truly absent (`ROUND4_NO_FUEL_LAP`): the only break in the fuel line, read out as `no reading`.
 * - Run 2's screened fit covers 9 laps, under half of its green laps, as the Vegas race's traffic screen left it: no line is drawn.
 * - The runs view reports the green-flag burn per lap (`fuel.green_gal_per_lap`).
 */

type Json = Record<string, unknown>;

export const ROUND4_SERVICE_LAP = 28;
export const ROUND4_NO_FUEL_LAP = 45;
export const ROUND4_BURN_GAL: Record<number, number> = { 1: 0.4227, 2: 0.4227, 3: 0.4311 };

export function buildRound4Analysis(analysisId = "round4-82-lap"): SyntheticAnalysis {
  const analysis = buildRound3Analysis(analysisId);
  for (const lap of analysis.race.laps as Json[]) {
    const number = lap.lap as number;
    if (number === ROUND4_SERVICE_LAP) lap.run_number = null;
    if (number === ROUND4_NO_FUEL_LAP) lap.fuel = { start_l: null, end_l: null, used_l: null, used_gal: null };
  }
  const ri = (analysis.investigation as Json).race_investigation as Json;
  for (const lap of ri.laps as Json[]) if (lap.lap === ROUND4_SERVICE_LAP) lap.run_number = null;
  for (const run of ri.runs as Json[]) {
    const fit = run.fit as Json | null;
    if (run.run_number === 2 && fit) {
      fit.eligible_count = 9;
      fit.laps = (fit.laps as number[]).slice(0, 9);
    }
  }
  for (const run of analysis.runs.runs as Json[]) {
    const burn = ROUND4_BURN_GAL[run.run_number as number];
    if (burn !== undefined) run.fuel = { ...(run.fuel as Json), green_gal_per_lap: burn, green_l_per_lap: burn * 3.785411784 };
  }
  return analysis;
}

import { buildRound2Analysis } from "./round2";
import type { SyntheticAnalysis } from "./synthetic";

/**
 * The round-2 synthetic race dressed for the round-3 Race and Runs checks (#40–#47): the per-lap context the backend's race view
 * carries (track temperature, close-traffic share, wheel-speed slip proxies, fuel at the lap's start; no track-usage series, so that
 * layer is not offered), a second stop that changed no tires (run 3 runs on run 2's set), and the laps each fit used. Every value is
 * invented; nothing here is race data.
 */

type Json = Record<string, unknown>;

export const ROUND3_SLIP_LAPS = [15, 33, 70] as const;
export const ROUND3_TRAFFIC_LAPS = [10, 11, 79] as const;

export function buildRound3Analysis(analysisId = "round3-82-lap"): SyntheticAnalysis {
  const analysis = buildRound2Analysis(analysisId);
  const runStartFuel: Record<number, number> = { 1: 60, 2: 59.2, 3: 52.1 };
  const runStartLap: Record<number, number> = { 1: 1, 2: 29, 3: 57 };
  for (const lap of analysis.race.laps as Json[]) {
    const number = lap.lap as number;
    const run = lap.run_number as number;
    lap.conditions = { track_temperature_f: Math.round((100.4 - number * 0.06) * 10) / 10, track_usage_percent: null, track_usage: null };
    lap.clean_context = { on_track_fraction: 1, traffic_proximity_fraction: (ROUND3_TRAFFIC_LAPS as readonly number[]).includes(number) ? 0.62 : Math.round(((number * 7) % 9) * 0.01 * 1000) / 1000 };
    const slip = (ROUND3_SLIP_LAPS as readonly number[]).includes(number);
    lap.vehicle_dynamics = { wheel_speed: { rear_wheelspin_proxy_s: slip ? 0.4 : 0, front_wheel_lock_proxy_s: slip ? 0.1 : 0, braking_wheel_lock_proxy_s: 0 } };
    lap.fuel = { start_l: Math.round((runStartFuel[run]! - (number - runStartLap[run]!) * 1.6) * 1000) / 1000 };
  }
  // The stop at lap 56 serviced fuel only: run 3 is on run 2's tires.
  const stops = (analysis.race as Json).pit_stops as Json[];
  stops[1] = { ...stops[1]!, tires_changed: [], service_s: 6.1, end_time_s: 1706.1 };
  // Each fit lists the eligible laps it used (the backend's `fit.laps`).
  const ri = (analysis.investigation as Json).race_investigation as Json;
  const laps = ri.laps as Json[];
  for (const run of ri.runs as Json[]) {
    const fit = run.fit as Json | null;
    if (!fit) continue;
    const [lo, hi] = fit.domain as [number, number];
    fit.laps = laps
      .filter((lap) => lap.run_number === run.run_number && lap.eligible === true && typeof lap.green_exposure === "number" && (lap.green_exposure as number) >= lo && (lap.green_exposure as number) <= hi)
      .map((lap) => lap.lap as number);
  }
  return analysis;
}

import { buildRound4Analysis } from "./round4";
import type { SyntheticAnalysis } from "./synthetic";

/**
 * The round-4 synthetic race dressed for the traffic marker (#67): the lap kinds the Race and Runs charts tell apart, each with the
 * `clean_context` the backend writes (`on_track_fraction`, `traffic_proximity_fraction`, `traffic_screened`, `traffic_rule`; no side).
 * Every value is invented; nothing here is race data.
 *
 * - Screened for close traffic alone, the double ring: laps 10, 11 and 79 (62 % of the lap, round 3's) and lap 20 (75 %).
 * - Screened for close traffic and something else, the hollow dot: lap 22 (off track as well).
 * - Screened for something else, the hollow dot: lap 30 (off track) and the restart laps 1, 29 and 57 of the base race.
 * - Open air, the plain dot: every other lap. `traffic_screened` is true on all of them (the recording has the car-distance channels)
 *   and the share is under a tenth, so none is screened.
 */

type Json = Record<string, unknown>;

export const TRAFFIC_RULE = "car ahead within max(25 m, 0.75 s) or behind within max(15 m, 0.40 s) for at least 10% of observed lap time";
/** Screened for close traffic alone. */
export const TRAFFIC_ONLY_LAPS = [10, 11, 20, 79] as const;
/** The traffic-only lap the readout example reads: `traffic 75 % of lap`. */
export const TRAFFIC_LAP = 20;
/** Screened for close traffic and off track. */
export const TRAFFIC_AND_OFF_TRACK_LAP = 22;
/** Screened for off track alone. */
export const OFF_TRACK_LAP = 30;
/** Screened for a restart alone (the base race's). */
export const RESTART_LAP = 29;
/** Open air in a recording with the distance channels: a small share of traffic, not screened. */
export const OPEN_AIR_LAP = 12;

/** The laps this fixture adds a screen to: the backend's reasons in its order, the share of the lap on track and in traffic. */
const SCREENED: Readonly<Record<number, { reasons: string[]; onTrack: number; share: number }>> = {
  [TRAFFIC_LAP]: { reasons: ["close_traffic"], onTrack: 1, share: 0.75 },
  [TRAFFIC_AND_OFF_TRACK_LAP]: { reasons: ["off_track", "close_traffic"], onTrack: 0.9, share: 0.4 },
  [OFF_TRACK_LAP]: { reasons: ["off_track"], onTrack: 0.9, share: 0.05 },
};

export function buildTrafficAnalysis(analysisId = "traffic-82-lap"): SyntheticAnalysis {
  const analysis = buildRound4Analysis(analysisId);
  const screen = (lap: Json, reasons: readonly string[]): void => {
    Object.assign(lap, { comparison_eligible: false, exclusion_reasons: [...reasons] });
  };
  for (const lap of analysis.catalog.laps as Json[]) {
    const dressing = SCREENED[lap.lap as number];
    if (dressing) screen(lap, dressing.reasons);
  }
  for (const lap of analysis.race.laps as Json[]) {
    const dressing = SCREENED[lap.lap as number];
    // The backend's whole clean_context: the distance channels are on every lap, so every lap is `traffic_screened`.
    lap.clean_context = { ...(lap.clean_context as Json), traffic_screened: true, traffic_rule: TRAFFIC_RULE };
    if (!dressing) continue;
    screen(lap, dressing.reasons);
    Object.assign(lap.clean_context as Json, { on_track_fraction: dressing.onTrack, traffic_proximity_fraction: dressing.share });
  }
  const ri = (analysis.investigation as Json).race_investigation as Json;
  for (const lap of ri.laps as Json[]) {
    const dressing = SCREENED[lap.lap as number];
    if (dressing) Object.assign(lap, { eligible: false, exclusion_reasons: [...dressing.reasons] });
  }
  // The fits list the laps they used: the newly screened laps are not among them.
  for (const run of ri.runs as Json[]) {
    const fit = run.fit as Json | null;
    if (fit) fit.laps = (fit.laps as number[]).filter((lap) => !(lap in SCREENED));
  }
  return analysis;
}

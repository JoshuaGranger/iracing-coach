import type { SyntheticAnalysis } from "./synthetic";

/**
 * The server's observed trend (`BackendRun.observed_trend`, `crates/coach-views/src/trend.rs`) written onto a synthetic analysis's
 * runs view, so the fixtures carry what the server would send. Test data only: the browser never fits a line. The rule is the
 * server's: a run's race lap rows that are complete, green, under a second on pit road, neither pit-in nor pit-out, timed, with no
 * exclusion reason but `close_traffic`, and not an incident's lap; least squares of lap time on lap, 4 decimals; none under three.
 * The synthetic race rows leave out `complete` and `pit_time_s`, read here as complete and 0.
 */

type Json = Record<string, unknown>;

const round4 = (value: number): number => Math.round(value * 1e4) / 1e4;

export function fixtureTrend(rows: readonly Json[], run: number, incidents: ReadonlySet<number> = new Set()): Json | null {
  const points = rows
    .filter((row) => row.run_number === run && typeof row.lap === "number" && (row.lap as number) > 0 && !incidents.has(row.lap as number))
    .filter((row) => row.complete !== false && String(row.flag_state ?? "").trim().toLowerCase() === "green")
    .filter((row) => ((row.pit_time_s as number | undefined) ?? 0) < 1 && row.pit_in !== true && row.pit_out !== true)
    .filter((row) => typeof row.lap_time_s === "number" && (row.lap_time_s as number) > 0)
    .filter((row) => ((row.exclusion_reasons as string[] | undefined) ?? []).every((reason) => reason === "close_traffic"))
    .map((row) => [row.lap as number, row.lap_time_s as number] as const);
  if (points.length < 3) return null;
  const mx = points.reduce((sum, [x]) => sum + x, 0) / points.length;
  const my = points.reduce((sum, [, y]) => sum + y, 0) / points.length;
  const sxx = points.reduce((sum, [x]) => sum + (x - mx) ** 2, 0);
  if (sxx <= 0) return null;
  const slope = points.reduce((sum, [x, y]) => sum + (x - mx) * (y - my), 0) / sxx;
  return {
    slope_s_per_lap: round4(slope),
    intercept_s: round4(my - slope * mx),
    first_lap: Math.min(...points.map(([x]) => x)),
    last_lap: Math.max(...points.map(([x]) => x)),
    laps_used: points.length,
  };
}

/** Sets every run's `observed_trend` from the analysis's race lap rows and incident events; returns the analysis. */
export function withObservedTrends(analysis: SyntheticAnalysis): SyntheticAnalysis {
  const rows = (analysis.race.laps ?? []) as unknown as Json[];
  const events = (((analysis.race as Json).damage_repair as Json | undefined)?.incident_points as Json | undefined)?.events as Json[] | undefined;
  const incidents = new Set((events ?? []).map((event) => event.candidate_lap).filter((lap): lap is number => typeof lap === "number"));
  for (const run of analysis.runs.runs as Json[]) run.observed_trend = fixtureTrend(rows, run.run_number as number, incidents);
  return analysis;
}

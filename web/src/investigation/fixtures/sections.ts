import { decodeCcol } from "../ccol";
import { buildRound2Analysis } from "./round2";
import type { SyntheticAnalysis } from "./synthetic";

/**
 * The round-2 synthetic race with the projector's `section_laps` (projection 3) for its three sections, standing in for the server
 * in the Sections checks (web round 3, R4). A section's time is the lap's share of its traced session time between the boundaries
 * (interpolated at the bin centres) applied to the lap's `lap_time_s`, so the figures read like seconds on a 30 s lap; they are ranked
 * over the laps the investigation's screen kept, as `crates/coach-views/src/section_laps.rs` ranks them. Every value is invented;
 * nothing here is race data.
 */

type Json = Record<string, unknown>;

const round3 = (value: number): number => Number(value.toFixed(3));

function timeAt(times: ArrayLike<number>, offset: number, bins: number, pct: number): number | null {
  const position = pct * bins - 0.5;
  const left = Math.max(0, Math.min(bins - 2, Math.floor(position)));
  const t0 = times[offset + left]!;
  const t1 = times[offset + left + 1]!;
  if (!Number.isFinite(t0) || !Number.isFinite(t1)) return null;
  return t0 + (t1 - t0) * (position - left);
}

export function buildSectionsAnalysis(analysisId = "sections-82-lap"): SyntheticAnalysis {
  const analysis = buildRound2Analysis(analysisId);
  const table = decodeCcol(analysis.ccol);
  const time = table.channels.get("session-time")!.raw;
  const ri = (analysis.investigation as Json).race_investigation as Json;
  const clean = new Set(((ri.laps as Json[]) ?? []).filter((lap) => lap.eligible === true).map((lap) => lap.lap as number));
  const lapTime = new Map(((ri.laps as Json[]) ?? []).map((lap) => [lap.lap as number, lap.lap_time_s as number]));
  const sections = ((ri.sections as Json).sections as Json[]).map((s) => ({ id: s.id as string, start: s.start_pct as number, end: s.end_pct as number }));
  const entries = sections.map((section) => {
    const timed: [number, number][] = [];
    table.laps.forEach((lap, index) => {
      if (!clean.has(lap)) return;
      const offset = index * table.bins;
      const a = timeAt(time, offset, table.bins, section.start);
      const b = timeAt(time, offset, table.bins, section.end);
      const whole = time[offset + table.bins - 1]! - time[offset]!;
      const seconds = lapTime.get(lap);
      if (a !== null && b !== null && b > a && whole > 0 && typeof seconds === "number") timed.push([round3(((b - a) / whole) * seconds), lap]);
    });
    timed.sort((x, y) => x[0] - y[0] || x[1] - y[1]);
    const middle = timed.length >> 1;
    const median = timed.length === 0 ? null : round3(timed.length % 2 ? timed[middle]![0] : (timed[middle - 1]![0] + timed[middle]![0]) / 2);
    const shown = Math.min(3, timed.length >> 1);
    const entry = ([seconds, lap]: [number, number]) => ({ lap, section_s: seconds, delta_s: round3(seconds - (median ?? 0)) });
    return {
      section_id: section.id,
      clean_lap_count: timed.length,
      median_s: median,
      fastest: timed.slice(0, shown).map(entry),
      slowest: timed.slice(timed.length - shown).reverse().map(entry),
    };
  });
  (analysis.investigation as Json).section_laps = { sections: entries };
  return analysis;
}

import { channelAsFloat, decodeCcol, encodeCcol } from "../ccol";
import { buildRound2Analysis } from "./round2";
import { circuitPoint, type SyntheticAnalysis } from "./synthetic";

/**
 * The round-2 synthetic race with the projector's `section_laps` (projection 3) for its three sections, standing in for the server
 * in the Sections checks (web round 3, R4). A section's time is the lap's share of its traced session time between the boundaries
 * (interpolated at the bin centres) applied to the lap's `lap_time_s`, so the figures read like seconds on a 30 s lap; they are ranked
 * over the laps the investigation's screen kept, as `crates/coach-views/src/section_laps.rs` ranks them.
 *
 * Round 5 (U3, #75, #76): each lap drives its own line, so its recorded latitude and longitude sit a few metres either side of the
 * centreline, varying along the lap and from lap to lap; the runs view's tire lifecycle agrees with the runs' laps (a four-tire stop
 * after run 1, right side only after run 2); and every race lap carries a traffic share, some with the side. Every value is
 * invented; nothing here is race data.
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

/** Sideways offset (normalized track units, about 10 m per 0.01) of a lap's line at lap fraction `t`. */
export function lineOffset(lap: number, t: number): number {
  const phase = ((lap * 0.618) % 1) * Math.PI * 2;
  return 0.006 * Math.sin(Math.PI * 2 * 3 * t + phase) + 0.002 * ((lap % 5) - 2);
}

/** Moves each lap's recorded positions off the centreline by `lineOffset`, along the circuit's normal. */
function driveOwnLines(analysis: SyntheticAnalysis): void {
  const table = decodeCcol(analysis.ccol);
  const lat = channelAsFloat(table.channels.get("latitude")!).slice();
  const lon = channelAsFloat(table.channels.get("longitude")!).slice();
  const pct = channelAsFloat(table.channels.get("lap-pct")!);
  // The synthetic transform (synthetic.ts): latitude = maximum_y - y * scale, longitude = minimum_x + x * scale.
  const scale = 0.12;
  table.laps.forEach((lap, index) => {
    for (let bin = 0; bin < table.bins; bin++) {
      const at = index * table.bins + bin;
      const t = pct[at]!;
      if (!Number.isFinite(lat[at]!) || !Number.isFinite(t)) continue;
      const a = circuitPoint(t - 0.001);
      const b = circuitPoint(t + 0.001);
      const length = Math.hypot(b.x - a.x, b.y - a.y) || 1;
      const offset = lineOffset(lap, t);
      const nx = -(b.y - a.y) / length;
      const ny = (b.x - a.x) / length;
      lon[at] = lon[at]! + nx * offset * scale;
      lat[at] = lat[at]! - ny * offset * scale;
    }
  });
  const channels = [...table.channels.values()].map((channel) => ({
    id: channel.id,
    dtype: channel.dtype,
    values: channel.id === "latitude" ? lat : channel.id === "longitude" ? lon : channelAsFloat(channel),
    missing: channel.missing,
  }));
  analysis.ccol = encodeCcol(table.analysisId, table.projectionVersion, table.bins, table.laps, channels);
}

export function buildSectionsAnalysis(analysisId = "sections-82-lap"): SyntheticAnalysis {
  const analysis = buildRound2Analysis(analysisId);
  driveOwnLines(analysis);
  // Tires (#75): laps on the set at each run's end, green plus caution, by corner.
  const sets: Record<number, Record<string, [number, number]>> = {
    1: { LF: [28, 0], RF: [28, 0], LR: [28, 0], RR: [28, 0] },
    2: { LF: [25, 3], RF: [25, 3], LR: [25, 3], RR: [25, 3] },
    3: { LF: [51, 3], RF: [26, 0], LR: [51, 3], RR: [26, 0] },
  };
  for (const run of analysis.runs.runs as Json[]) {
    const set = sets[run.run_number as number];
    if (!set) continue;
    run.tire_set_lifecycle = { corners: Object.fromEntries(Object.entries(set).map(([corner, [green, caution]]) => [corner, { green_laps_on_set: green, caution_laps_on_set: caution }])) };
  }
  // Traffic (#75): a share for every lap; the screened traffic laps well over a tenth, a side named on most (ahead, behind, both).
  for (const lap of (analysis.race.laps ?? []) as Json[]) {
    const number = lap.lap as number;
    const screened = Array.isArray(lap.exclusion_reasons) && (lap.exclusion_reasons as string[]).includes("close_traffic");
    const fraction = screened ? 0.42 + (number % 3) * 0.11 : ((number * 37) % 10) / 100;
    const side = number % 4 === 0 ? "behind" : number % 4 === 1 ? "ahead" : number % 4 === 2 ? "both" : null;
    lap.clean_context = { ...((lap.clean_context as Json) ?? {}), traffic_proximity_fraction: fraction, traffic_screened: true, ...(side ? { traffic_side: side } : {}) };
  }
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

/**
 * The same analysis with its whole circuit turned by `tilt` radians about the middle of the normalized box: the centreline, the
 * start/finish line and every lap's recorded latitude and longitude, so the laps still drive on the track (round 5, #81: the map
 * must turn it back so its long axis is horizontal). Invented, like everything here.
 */
export function tiltAnalysis(analysis: SyntheticAnalysis, tilt: number): SyntheticAnalysis {
  const cos = Math.cos(tilt);
  const sin = Math.sin(tilt);
  const turn = (x: number, y: number): { x: number; y: number } => ({ x: 0.5 + (x - 0.5) * cos - (y - 0.5) * sin, y: 0.5 + (x - 0.5) * sin + (y - 0.5) * cos });
  const track = analysis.track;
  const main = track.main_path!;
  const turned = main.x.map((x, i) => turn(x, main.y[i]!));
  const sf = track.start_finish;
  const t = track.transform!;
  const scale = t.normalization_scale;
  const table = decodeCcol(analysis.ccol);
  const lat = channelAsFloat(table.channels.get("latitude")!).slice();
  const lon = channelAsFloat(table.channels.get("longitude")!).slice();
  for (let i = 0; i < lat.length; i++) {
    if (!Number.isFinite(lat[i]!) || !Number.isFinite(lon[i]!)) continue;
    const p = turn((lon[i]! - t.source_bounds.minimum_x) / scale, (t.source_bounds.maximum_y - lat[i]!) / scale);
    lon[i] = t.source_bounds.minimum_x + p.x * scale;
    lat[i] = t.source_bounds.maximum_y - p.y * scale;
  }
  const channels = [...table.channels.values()].map((channel) => ({
    id: channel.id,
    dtype: channel.dtype,
    values: channel.id === "latitude" ? lat : channel.id === "longitude" ? lon : channelAsFloat(channel),
    missing: channel.missing,
  }));
  return {
    ...analysis,
    track: {
      ...track,
      main_path: { ...main, x: turned.map((p) => p.x), y: turned.map((p) => p.y) },
      start_finish: sf ? { a: turn(sf.a.x, sf.a.y), b: turn(sf.b.x, sf.b.y) } : null,
    },
    ccol: encodeCcol(table.analysisId, table.projectionVersion, table.bins, table.laps, channels),
  };
}

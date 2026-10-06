import { channelAsFloat, type CcolChannel } from "../ccol";
import type { MapGeometry } from "../geometry/map-model";
import type { TraceData } from "../geometry/trace-model";
import type { Point2 } from "../types";
import { sectionBins, type SectionSpan } from "./section-traces-model";

/**
 * Racing lines through a section (backlog #76): each lap's own path, from the latitude and longitude it recorded in the section's
 * trace bins, placed with the track geometry's GPS transform and longitude scale (`MapGeometry.projectGps`, the same transform and
 * rotation as the centreline). Nothing is offset, smoothed or invented: a bin without a position breaks the line, and a lap with
 * fewer than two placed samples in the section gets no line.
 */

/** The trace channels the lines need, fetched with the section's telemetry. */
export const POSITION_SIGNALS = ["latitude", "longitude"] as const;

function bySourceKey(data: TraceData, key: string): CcolChannel | null {
  for (const signal of data.catalog.signals) {
    if (signal.source_key !== key) continue;
    const channel = data.table.channels.get(signal.id);
    if (channel) return channel;
  }
  return null;
}

/** Latitude and longitude columns (`laps x bins`), including channels fetched after the store was first built; null when absent. */
export function positionColumns(data: TraceData): { lat: Float64Array; lon: Float64Array } | null {
  const lat = data.aux.latitude ?? bySourceKey(data, "latitude");
  const lon = data.aux.longitude ?? bySourceKey(data, "longitude");
  return lat && lon ? { lat: channelAsFloat(lat), lon: channelAsFloat(lon) } : null;
}

export interface SectionLine {
  lap: number;
  /** Runs of consecutive placed samples, in driving order. */
  runs: Point2[][];
  /** SVG path data in map units (`M`/`L`, one `M` per run). */
  d: string;
}

const fixed = (value: number): string => (Math.round(value * 100) / 100).toString();

export function linePath(runs: readonly (readonly Point2[])[]): string {
  return runs
    .filter((run) => run.length > 1)
    .map((run) => run.map((p, i) => `${i === 0 ? "M" : "L"}${fixed(p.x)},${fixed(p.y)}`).join(" "))
    .join(" ");
}

/** The laps' lines through the section, in the order given; laps without two placed samples are left out. */
export function sectionRacingLines(data: TraceData | null, map: MapGeometry | null, section: SectionSpan | null, laps: readonly number[]): SectionLine[] {
  if (!data || !map || !section || !map.hasTransform) return [];
  const columns = positionColumns(data);
  if (!columns) return [];
  const { bins } = sectionBins(data.x, section);
  const out: SectionLine[] = [];
  for (const lap of laps) {
    const index = data.lapIndex.get(lap);
    if (index === undefined) continue;
    const runs: Point2[][] = [];
    let current: Point2[] = [];
    for (const bin of bins) {
      const la = columns.lat[index * data.bins + bin]!;
      const lo = columns.lon[index * data.bins + bin]!;
      const placed = Number.isFinite(la) && Number.isFinite(lo) && !(Math.abs(la) <= 1e-9 && Math.abs(lo) <= 1e-9) ? map.projectGps(la, lo) : null;
      if (placed) {
        current.push(placed);
      } else if (current.length) {
        runs.push(current);
        current = [];
      }
    }
    if (current.length) runs.push(current);
    const drawn = runs.filter((run) => run.length > 1);
    if (drawn.length === 0) continue;
    out.push({ lap, runs: drawn, d: linePath(drawn) });
  }
  return out;
}

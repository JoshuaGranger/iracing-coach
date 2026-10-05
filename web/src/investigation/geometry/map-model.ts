import { channelAsFloat } from "../ccol";
import type { PathSeriesData, Point2, TrackGeometryData } from "../types";
import { pointBudgetPerTrace, representativeIndices } from "./budget";
import { lapColor, signalColumn, type TraceData } from "./trace-model";

/**
 * Track map geometry (TelemetryWorkspace.razor:1750-2240, AnalysisTrackMapGeometry.cs). The backend's
 * normalized_local_vector geometry is drawn as delivered: this module only scales it into the 420x280
 * map box, checks that overlays are plausible and places recorded laps with the geometry's own
 * GPS-to-geometry transform. No coordinate is reprojected.
 */

export interface VectorPoint {
  x: number;
  y: number;
  pct: number | null;
}

export interface MapPoint {
  percent: number;
  x: number;
  y: number;
}

export interface MapLine {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

const MINIMUM_NORMALIZED_MAIN_SPAN = 0.2;
const MAXIMUM_RELATIVE_MAIN_SEGMENT = 0.35;
const MAXIMUM_OVERLAY_MARGIN = 0.45;
const MAXIMUM_RELATIVE_OVERLAY_SEGMENT = 0.55;

export function wrap(value: number): number {
  const wrapped = value % 1;
  return wrapped < 0 ? wrapped + 1 : wrapped;
}

export function seriesPoints(series: PathSeriesData | null | undefined): VectorPoint[] {
  if (!series) return [];
  const count = Math.min(series.x.length, series.y.length);
  const out: VectorPoint[] = [];
  for (let index = 0; index < count; index++) {
    const pct = series.pct[index];
    out.push({ x: series.x[index]!, y: series.y[index]!, pct: typeof pct === "number" && Number.isFinite(pct) ? pct : null });
  }
  return out;
}

const distance = (a: Point2, b: Point2): number => Math.hypot(b.x - a.x, b.y - a.y);

interface Extents {
  minX: number;
  maxX: number;
  minY: number;
  maxY: number;
  count: number;
}

function extents(points: readonly Point2[]): Extents | null {
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity, count = 0;
  for (const point of points) {
    if (!Number.isFinite(point.x) || !Number.isFinite(point.y)) continue;
    count += 1;
    minX = Math.min(minX, point.x); maxX = Math.max(maxX, point.x);
    minY = Math.min(minY, point.y); maxY = Math.max(maxY, point.y);
  }
  return count ? { minX, maxX, minY, maxY, count } : null;
}

/** IsCanonicalMainLoop: a closed loop of plausible size with no long jumps and no lap-percent gap over 5 %. */
export function isCanonicalMainLoop(points: readonly VectorPoint[]): boolean {
  const finite = points.filter((point) => Number.isFinite(point.x) && Number.isFinite(point.y));
  if (finite.length < 3) return false;
  const e = extents(finite)!;
  const span = Math.max(e.maxX - e.minX, e.maxY - e.minY);
  if (!Number.isFinite(span) || span < MINIMUM_NORMALIZED_MAIN_SPAN) return false;
  let maximumSegment = 0;
  for (let index = 1; index < finite.length; index++) maximumSegment = Math.max(maximumSegment, distance(finite[index - 1]!, finite[index]!));
  if (!Number.isFinite(maximumSegment) || maximumSegment > span * MAXIMUM_RELATIVE_MAIN_SEGMENT) return false;
  const closure = distance(finite[0]!, finite[finite.length - 1]!);
  if (!Number.isFinite(closure) || closure > span * 0.15) return false;
  const percentages = finite.filter((point) => point.pct !== null).map((point) => wrap(point.pct!)).sort((a, b) => a - b);
  if (percentages.length < 24) return true;
  let maximumGap = percentages[0]! + 1 - percentages[percentages.length - 1]!;
  for (let index = 1; index < percentages.length; index++) maximumGap = Math.max(maximumGap, percentages[index]! - percentages[index - 1]!);
  return maximumGap <= 0.05;
}

export function isPlausibleOverlayPath(main: readonly VectorPoint[], overlay: readonly Point2[]): boolean {
  if (!isCanonicalMainLoop(main) || overlay.length < 2) return false;
  const m = extents(main);
  const c = extents(overlay);
  if (!m || !c || c.count !== overlay.length) return false;
  const span = Math.max(m.maxX - m.minX, m.maxY - m.minY);
  const margin = span * MAXIMUM_OVERLAY_MARGIN;
  if (c.minX < m.minX - margin || c.maxX > m.maxX + margin || c.minY < m.minY - margin || c.maxY > m.maxY + margin) return false;
  let maximumSegment = 0;
  for (let index = 1; index < overlay.length; index++) maximumSegment = Math.max(maximumSegment, distance(overlay[index - 1]!, overlay[index]!));
  return Number.isFinite(maximumSegment) && maximumSegment <= span * MAXIMUM_RELATIVE_OVERLAY_SEGMENT;
}

export function isPlausibleOverlayLine(main: readonly VectorPoint[], line: { a: Point2; b: Point2 } | null | undefined): boolean {
  if (!line || !isPlausibleOverlayPath(main, [line.a, line.b])) return false;
  const m = extents(main);
  if (!m) return false;
  const span = Math.max(m.maxX - m.minX, m.maxY - m.minY);
  return distance(line.a, line.b) <= span * 0.2;
}

export function isPlausibleNormalizedPoint(main: readonly VectorPoint[], x: number, y: number): boolean {
  if (!Number.isFinite(x) || !Number.isFinite(y) || !isCanonicalMainLoop(main)) return false;
  const m = extents(main);
  if (!m) return false;
  const span = Math.max(m.maxX - m.minX, m.maxY - m.minY);
  const margin = span * MAXIMUM_OVERLAY_MARGIN;
  return x >= m.minX - margin && x <= m.maxX + margin && y >= m.minY - margin && y <= m.maxY + margin;
}

export interface MapGeometry {
  /** Scaled main loop in the 420x280 map box, ascending lap percent. */
  points: MapPoint[];
  viewBox: string;
  /** Padded bounds `[x, y, w, h]` of the map. */
  bounds: [number, number, number, number];
  ribbonPath: string;
  startFinish: MapLine | null;
  pit: { road: string; entry: string; exit: string; commitment: MapLine | null; merge: MapLine | null } | null;
  /** Maps a normalized-geometry point into the map box. */
  project(x: number, y: number): Point2;
  /** Recorded GPS to map box, null when the geometry has no transform or the point is implausible. */
  projectGps(latitude: number, longitude: number): Point2 | null;
  hasTransform: boolean;
  /** The Sections highlight may use this map: status usable and every main-loop point carries its lap fraction (audit R8). */
  sectionsReady: boolean;
}

export function mapPointAt(points: readonly MapPoint[], percent: number): MapPoint | null {
  if (points.length === 0) return null;
  const target = wrap(percent);
  for (let index = 0; index < points.length; index++) {
    const a = points[index]!;
    const b = points[(index + 1) % points.length]!;
    const start = a.percent;
    const end = b.percent <= start ? b.percent + 1 : b.percent;
    const adjusted = target < start ? target + 1 : target;
    if (adjusted > end + 0.000001) continue;
    const t = Math.abs(end - start) < 0.000001 ? 0 : (adjusted - start) / (end - start);
    return { percent: target, x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t };
  }
  return points[0]!;
}

const fixed = (value: number): string => (Math.round(value * 100) / 100).toString();

export function svgPath(points: readonly Point2[], close: boolean): string {
  if (points.length === 0) return "";
  let path = `M ${fixed(points[0]!.x)} ${fixed(points[0]!.y)}`;
  for (let index = 1; index < points.length; index++) path += ` L ${fixed(points[index]!.x)} ${fixed(points[index]!.y)}`;
  return close ? `${path} Z` : path;
}

/** Builds the map from backend geometry; null when it has no complete main loop (the browser then uses the distance strip). */
export function buildMapGeometry(track: TrackGeometryData | null): MapGeometry | null {
  if (!track || track.status.toLowerCase() === "unavailable") return null;
  const main = seriesPoints(track.main_path);
  if (main.length < 3 || !isCanonicalMainLoop(main)) return null;
  const quality = track.quality as { main_loop_complete?: unknown } | null | undefined;
  if (quality && quality.main_loop_complete === false) return null;
  const e = extents(main)!;
  const centerX = (e.minX + e.maxX) / 2;
  const centerY = (e.minY + e.maxY) / 2;
  const scale = Math.min(350 / Math.max(0.0001, e.maxX - e.minX), 218 / Math.max(0.0001, e.maxY - e.minY));
  const project = (x: number, y: number): Point2 => ({ x: 210 + (x - centerX) * scale, y: 140 + (y - centerY) * scale });
  const points: MapPoint[] = main.map((point, index) => {
    const p = project(point.x, point.y);
    return { percent: wrap(point.pct ?? index / main.length), x: p.x, y: p.y };
  });
  const padding = 18;
  const xs = points.map((point) => point.x);
  const ys = points.map((point) => point.y);
  const minX = Math.min(...xs) - padding;
  const minY = Math.min(...ys) - padding;
  const width = Math.max(...xs) - Math.min(...xs) + padding * 2;
  const height = Math.max(...ys) - Math.min(...ys) + padding * 2;

  const lineOf = (line: { a: Point2; b: Point2 }): MapLine => {
    const a = project(line.a.x, line.a.y);
    const b = project(line.b.x, line.b.y);
    return { x1: a.x, y1: a.y, x2: b.x, y2: b.y };
  };
  const pathOf = (series: PathSeriesData | null | undefined): string => {
    const pts = seriesPoints(series);
    return isPlausibleOverlayPath(main, pts) ? svgPath(pts.map((point) => project(point.x, point.y)), false) : "";
  };
  let startFinish: MapLine | null = null;
  if (track.start_finish && isPlausibleOverlayLine(main, track.start_finish)) startFinish = lineOf(track.start_finish);
  if (!startFinish) startFinish = deriveStartFinish(points);
  const road = pathOf(track.pit_lane);
  const entry = pathOf(track.pit_entry);
  const exit = pathOf(track.pit_exit);
  const commitment = track.pit_commitment_line && isPlausibleOverlayLine(main, track.pit_commitment_line) ? lineOf(track.pit_commitment_line) : null;
  const merge = track.pit_merge_line && isPlausibleOverlayLine(main, track.pit_merge_line) ? lineOf(track.pit_merge_line) : null;
  const transform = track.transform ?? null;
  // The backend's x is longitude times this factor (profile v24); older geometry has none and x is longitude.
  const longitudeScale = transform?.longitude_scale !== undefined && transform.longitude_scale > 0 ? transform.longitude_scale : 1;
  return {
    points,
    viewBox: `${fixed(minX)} ${fixed(minY)} ${fixed(width)} ${fixed(height)}`,
    bounds: [minX, minY, width, height],
    ribbonPath: svgPath(points, true),
    startFinish,
    pit: road || entry || exit || commitment || merge ? { road, entry, exit, commitment, merge } : null,
    project,
    hasTransform: transform !== null,
    sectionsReady: track.status.toLowerCase() === "usable" && main.every((point) => typeof point.pct === "number" && Number.isFinite(point.pct)),
    projectGps(latitude, longitude) {
      if (!transform) return null;
      const nx = (longitude * longitudeScale - transform.source_bounds.minimum_x) / transform.normalization_scale;
      const ny = (transform.source_bounds.maximum_y - latitude) / transform.normalization_scale;
      return isPlausibleNormalizedPoint(main, nx, ny) ? project(nx, ny) : null;
    },
  };
}

function deriveStartFinish(points: readonly MapPoint[]): MapLine {
  const center = mapPointAt(points, 0)!;
  const before = mapPointAt(points, 0.995)!;
  const after = mapPointAt(points, 0.005)!;
  const tx = after.x - before.x;
  const ty = after.y - before.y;
  const length = Math.hypot(tx, ty);
  if (length < 0.0001) return { x1: center.x, y1: center.y - 13, x2: center.x, y2: center.y + 13 };
  const nx = (-ty / length) * 13;
  const ny = (tx / length) * 13;
  return { x1: center.x - nx, y1: center.y - ny, x2: center.x + nx, y2: center.y + ny };
}

export interface LapMapPaths {
  laps: number[];
  color: string;
  d: string;
}

/** Recorded laps on the map, one `<path>` per colour group, each lap thinned to the screen budget. */
export function lapMapPaths(data: TraceData, map: MapGeometry, selectedLaps: readonly number[]): LapMapPaths[] {
  const lat = data.aux.latitude ? channelAsFloat(data.aux.latitude) : null;
  const lon = data.aux.longitude ? channelAsFloat(data.aux.longitude) : null;
  if (!lat || !lon || !map.hasTransform) return [];
  const bins = data.bins;
  const groups = new Map<string, LapMapPaths>();
  const budget = pointBudgetPerTrace(selectedLaps.length, 420, false, data.budgets);
  for (const lap of selectedLaps.slice().sort((a, b) => a - b)) {
    const index = data.lapIndex.get(lap);
    if (index === undefined) continue;
    const pts: Point2[] = [];
    for (let bin = 0; bin < bins; bin++) {
      const la = lat[index * bins + bin]!;
      const lo = lon[index * bins + bin]!;
      if (!Number.isFinite(la) || !Number.isFinite(lo) || (Math.abs(la) <= 1e-9 && Math.abs(lo) <= 1e-9)) continue;
      const p = map.projectGps(la, lo);
      if (p) pts.push(p);
    }
    if (pts.length < 3) continue;
    const thinned = representativeIndices(pts.length, budget).map((i) => pts[i]!);
    const color = lapColor(data, lap);
    let group = groups.get(color);
    if (!group) {
      group = { laps: [], color, d: "" };
      groups.set(color, group);
    }
    group.laps.push(lap);
    const d = svgPath(thinned, false);
    group.d = group.d ? `${group.d} ${d}` : d;
  }
  return [...groups.values()];
}

export type MapMode = "traces" | "speed" | "throttle" | "brake" | "stress";
export const MAP_MODES: readonly { key: MapMode; label: string }[] = [
  { key: "traces", label: "Traces" },
  { key: "speed", label: "Speed" },
  { key: "throttle", label: "Throttle" },
  { key: "brake", label: "Brake" },
  { key: "stress", label: "Tire load" },
];

const SPEED_STOPS: [number, number, number, number][] = [
  [0.0, 232, 66, 78],
  [0.34, 248, 132, 64],
  [0.58, 242, 203, 76],
  [0.78, 116, 222, 108],
  [1.0, 34, 220, 116],
];

function mix(amount: number, low: [number, number, number], high: [number, number, number]): string {
  const b = Math.min(1, Math.max(0, amount));
  return `rgb(${Math.round(low[0] + (high[0] - low[0]) * b)} ${Math.round(low[1] + (high[1] - low[1]) * b)} ${Math.round(low[2] + (high[2] - low[2]) * b)})`;
}

function heat(normalized: number): string {
  const amount = Math.min(1, Math.max(0, normalized));
  let upper = 1;
  while (upper < SPEED_STOPS.length - 1 && amount > SPEED_STOPS[upper]![0]) upper += 1;
  const lo = SPEED_STOPS[Math.max(0, upper - 1)]!;
  const hi = SPEED_STOPS[upper]!;
  const blend = Math.abs(hi[0] - lo[0]) < 0.000001 ? 0 : (amount - lo[0]) / (hi[0] - lo[0]);
  return `rgb(${Math.round(lo[1] + (hi[1] - lo[1]) * blend)} ${Math.round(lo[2] + (hi[2] - lo[2]) * blend)} ${Math.round(lo[3] + (hi[3] - lo[3]) * blend)})`;
}

export function metricColor(mode: MapMode, value: number | null, range: { min: number; max: number }): string {
  if (value === null || !Number.isFinite(value)) return "#56616D";
  const normalized = Math.pow(Math.min(1, Math.max(0, (value - range.min) / Math.max(0.000001, range.max - range.min))), 0.72);
  if (mode !== "speed" && normalized < 0.015) return "#48515B";
  switch (mode) {
    case "speed": return heat(normalized);
    case "brake": return mix(normalized, [72, 79, 88], [255, 69, 86]);
    case "throttle": return mix(normalized, [72, 79, 88], [42, 226, 126]);
    case "stress": return mix(normalized, [247, 210, 74], [247, 65, 76]);
    default: return "#75AFFF";
  }
}

function median(values: number[]): number | null {
  const finite = values.filter((value) => Number.isFinite(value)).sort((a, b) => a - b);
  if (finite.length === 0) return null;
  const middle = finite.length >> 1;
  return finite.length % 2 === 0 ? (finite[middle - 1]! + finite[middle]!) / 2 : finite[middle]!;
}

export function metricColumnId(mode: MapMode): string {
  return mode === "stress" ? "tire-stress" : mode;
}

export function metricRange(data: TraceData, mode: MapMode, lapIndices: readonly number[]): { min: number; max: number } {
  if (mode === "throttle" || mode === "brake") return { min: 0, max: 1 };
  const column = signalColumn(data, metricColumnId(mode));
  let min = Infinity, max = -Infinity;
  if (column) {
    for (const lap of lapIndices) {
      for (let bin = 0; bin < data.bins; bin++) {
        const value = column[lap * data.bins + bin]!;
        if (!Number.isFinite(value)) continue;
        min = Math.min(min, value); max = Math.max(max, value);
      }
    }
  }
  if (!Number.isFinite(min)) return mode === "speed" ? { min: 0, max: 180 } : { min: 0, max: 1 };
  if (mode === "stress") {
    if (max - min < 0.001) { min = Math.max(0, min - 0.01); max = Math.min(1, max + 0.01); }
    return { min, max: Math.max(min + 0.001, max) };
  }
  if (max - min < 5) { min -= 2.5; max += 2.5; }
  return { min, max };
}

export interface RibbonSegment {
  d: string;
  color: string;
}

/**
 * Metric ribbon: per bin, the median position of the selected laps' recorded points sets the centre and
 * their spread the width; the colour is the median metric (BuildMetricRibbonSamples).
 */
export function metricRibbon(data: TraceData, map: MapGeometry, mode: MapMode, selectedLaps: readonly number[]): RibbonSegment[] {
  const bins = data.bins;
  const column = signalColumn(data, metricColumnId(mode));
  if (!column) return [];
  const lapIndices = selectedLaps.map((lap) => data.lapIndex.get(lap)).filter((index): index is number => index !== undefined);
  const range = metricRange(data, mode, lapIndices);
  const lat = data.aux.latitude ? channelAsFloat(data.aux.latitude) : null;
  const lon = data.aux.longitude ? channelAsFloat(data.aux.longitude) : null;
  const seeds: { x: number; y: number; observed: Point2[]; metric: number | null }[] = [];
  for (let bin = 0; bin < bins; bin++) {
    const percent = data.x[bin]!;
    const observed: Point2[] = [];
    const metrics: number[] = [];
    for (const lap of lapIndices) {
      metrics.push(column[lap * bins + bin]!);
      if (lat && lon && map.hasTransform) {
        const p = map.projectGps(lat[lap * bins + bin]!, lon[lap * bins + bin]!);
        if (p) observed.push(p);
      }
    }
    const fallback = mapPointAt(map.points, percent)!;
    const cx = median(observed.map((p) => p.x)) ?? fallback.x;
    const cy = median(observed.map((p) => p.y)) ?? fallback.y;
    seeds.push({ x: cx, y: cy, observed, metric: median(metrics) });
  }
  const samples = seeds.map((seed, index) => {
    const before = seeds[(index - 1 + bins) % bins]!;
    const after = seeds[(index + 1) % bins]!;
    const tx = after.x - before.x;
    const ty = after.y - before.y;
    const length = Math.hypot(tx, ty);
    const nx = length < 0.000001 ? 0 : -ty / length;
    const ny = length < 0.000001 ? 1 : tx / length;
    const offsets = seed.observed.map((p) => (p.x - seed.x) * nx + (p.y - seed.y) * ny);
    const lo = offsets.length ? Math.min(...offsets) : 0;
    const hi = offsets.length ? Math.max(...offsets) : 0;
    const mid = (lo + hi) / 2;
    return { x: seed.x + nx * mid, y: seed.y + ny * mid, nx, ny, width: Math.max(1, hi - lo), metric: seed.metric };
  });
  const segments: RibbonSegment[] = [];
  for (let index = 0; index < samples.length; index++) {
    const a = samples[index]!;
    const b = samples[(index + 1) % samples.length]!;
    const pa = (s: typeof a, sign: number): string => `${fixed(s.x + sign * s.nx * s.width / 2)} ${fixed(s.y + sign * s.ny * s.width / 2)}`;
    const d = `M ${pa(a, 1)} L ${pa(b, 1)} L ${pa(b, -1)} L ${pa(a, -1)} Z`;
    const m = median([a.metric ?? Number.NaN, b.metric ?? Number.NaN]);
    segments.push({ d, color: metricColor(mode, m, range) });
  }
  return segments;
}

/** Legend text for the colour scale. */
export function metricLegend(mode: MapMode, range: { min: number; max: number }): { low: string; high: string } {
  if (mode === "speed") return { low: `${range.min.toFixed(0)} mph`, high: `${range.max.toFixed(0)} mph` };
  if (mode === "throttle" || mode === "brake" || mode === "stress") return { low: "0%", high: "100%" };
  return { low: "", high: "" };
}

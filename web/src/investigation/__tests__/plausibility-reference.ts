import { isCanonicalMainLoop, seriesPoints, type MapGeometry, type VectorPoint } from "../geometry/map-model";
import type { TrackGeometryData } from "../types";

/**
 * The map's GPS plausibility rule as it ran before `buildMapGeometry` computed the overlay bounds once: the loop check, the
 * extents and the margin recomputed for every point. The reference the tests hold the faster rule to, answer for answer.
 */
export function perPointPlausible(main: readonly VectorPoint[], x: number, y: number): boolean {
  if (!Number.isFinite(x) || !Number.isFinite(y) || !isCanonicalMainLoop(main)) return false;
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity, count = 0;
  for (const point of main) {
    if (!Number.isFinite(point.x) || !Number.isFinite(point.y)) continue;
    count += 1;
    minX = Math.min(minX, point.x); maxX = Math.max(maxX, point.x);
    minY = Math.min(minY, point.y); maxY = Math.max(maxY, point.y);
  }
  if (count === 0) return false;
  const margin = Math.max(maxX - minX, maxY - minY) * 0.45;
  return x >= minX - margin && x <= maxX + margin && y >= minY - margin && y <= maxY + margin;
}

/** `map` with a GPS projection that applies `perPointPlausible` to every fix, as `buildMapGeometry` did before. */
export function perPointMap(track: TrackGeometryData, map: MapGeometry): MapGeometry {
  const main = seriesPoints(track.main_path);
  const t = track.transform!;
  const k = t.longitude_scale !== undefined && t.longitude_scale > 0 ? t.longitude_scale : 1;
  return {
    ...map,
    projectGps(latitude, longitude) {
      const nx = (longitude * k - t.source_bounds.minimum_x) / t.normalization_scale;
      const ny = (t.source_bounds.maximum_y - latitude) / t.normalization_scale;
      return perPointPlausible(main, nx, ny) ? map.project(nx, ny) : null;
    },
  };
}

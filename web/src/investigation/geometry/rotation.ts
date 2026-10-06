/**
 * Map rotation (backlog #81): every track map and silhouette is turned so the centreline's long axis lies horizontal, then fitted.
 * The angle is the principal axis of the centreline's points (the direction of their largest spread), so it is derived from the
 * geometry, never hand-picked, and the same points always give the same angle. Of the two ways to lay that axis flat, the one that
 * puts the start/finish point in the lower half is chosen (an oval's front stretch along the bottom); a start on the axis itself
 * keeps the smaller turn. A rotation only turns the picture: no reflection, so the direction of travel is kept, and north is not
 * implied. Every consumer (the centreline, pit road, the start/finish mark, section segments, racing lines, markers and the
 * cursor) goes through the same `rotatePoint`, so nothing drifts against the outline.
 */

export interface PlanePoint {
  x: number;
  y: number;
}

export interface PlaneRotation {
  /** The turn applied, in radians, in the drawing's own axes (y down): positive turns +x towards +y. */
  angle: number;
  cos: number;
  sin: number;
  /** The pivot: the centroid of the points the rotation was derived from. */
  cx: number;
  cy: number;
}

export const NO_ROTATION: PlaneRotation = { angle: 0, cos: 1, sin: 0, cx: 0, cy: 0 };

/** Below this relative difference of the two spreads the outline has no long axis (a near-circle) and is not turned. */
const MIN_ANISOTROPY = 0.02;
/** A start/finish point closer to the long axis than this share of the outline's spread does not choose a side. */
const SIDE_TOLERANCE = 0.02;

function rotationOf(angle: number, cx: number, cy: number): PlaneRotation {
  // Exact zeros keep an unturned outline's coordinates bit-for-bit.
  if (angle === 0) return { angle: 0, cos: 1, sin: 0, cx, cy };
  return { angle, cos: Math.cos(angle), sin: Math.sin(angle), cx, cy };
}

export function rotatePoint(rotation: PlaneRotation, x: number, y: number): PlanePoint {
  const dx = x - rotation.cx;
  const dy = y - rotation.cy;
  return { x: rotation.cx + dx * rotation.cos - dy * rotation.sin, y: rotation.cy + dx * rotation.sin + dy * rotation.cos };
}

/**
 * The rotation that lays the long axis of `points` horizontal. `start` is the start/finish point (default: the first finite point,
 * the path's lap fraction 0). Fewer than three finite points, or no long axis, give the identity about the centroid.
 */
export function principalAxisRotation(points: Iterable<PlanePoint>, start?: PlanePoint | null): PlaneRotation {
  let count = 0;
  let sx = 0;
  let sy = 0;
  let first: PlanePoint | null = null;
  const finite: PlanePoint[] = [];
  for (const p of points) {
    if (!Number.isFinite(p.x) || !Number.isFinite(p.y)) continue;
    first ??= p;
    finite.push(p);
    count += 1;
    sx += p.x;
    sy += p.y;
  }
  if (count < 3) return count ? rotationOf(0, sx / count, sy / count) : NO_ROTATION;
  const cx = sx / count;
  const cy = sy / count;
  let sxx = 0;
  let syy = 0;
  let sxy = 0;
  for (const p of finite) {
    const dx = p.x - cx;
    const dy = p.y - cy;
    sxx += dx * dx;
    syy += dy * dy;
    sxy += dx * dy;
  }
  const trace = sxx + syy;
  if (!(trace > 0)) return rotationOf(0, cx, cy);
  // Eigenvalues of the 2x2 covariance; their gap says how much longer the long axis is.
  const gap = Math.hypot(sxx - syy, 2 * sxy);
  if (gap / trace < MIN_ANISOTROPY) return rotationOf(0, cx, cy);
  // Direction of the long axis, in (-pi/2, pi/2]; turning by its negative lays it on +x.
  const axis = 0.5 * Math.atan2(2 * sxy, sxx - syy);
  let angle = -axis;
  const anchor = start && Number.isFinite(start.x) && Number.isFinite(start.y) ? start : first!;
  const turned = rotatePoint(rotationOf(angle, cx, cy), anchor.x, anchor.y);
  // The spread across the axis (the short one) measures "near the axis".
  const across = Math.sqrt(Math.max(0, (trace - gap) / 2 / count));
  const along = Math.sqrt(Math.max(0, (trace + gap) / 2 / count));
  if (turned.y - cy < -SIDE_TOLERANCE * Math.max(across, along)) angle += Math.PI;
  if (angle > Math.PI) angle -= 2 * Math.PI;
  return rotationOf(angle, cx, cy);
}

/** The turn in degrees, rounded to a tenth (for the map's data attribute and tests). */
export function rotationDegrees(rotation: PlaneRotation): number {
  return Math.round((rotation.angle * 180) / Math.PI * 10) / 10 || 0;
}

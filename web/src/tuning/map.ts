export interface MapPoint {
  lap_pct: number;
  x: number;
  y: number;
}

export interface ViewBox {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** Bounds of the path with a margin, so markers and halos are not clipped. */
export function viewBox(points: readonly MapPoint[], margin = 0.06): ViewBox {
  if (points.length === 0) return { x: 0, y: 0, w: 1, h: 1 };
  let x0 = Infinity;
  let y0 = Infinity;
  let x1 = -Infinity;
  let y1 = -Infinity;
  for (const p of points) {
    x0 = Math.min(x0, p.x);
    x1 = Math.max(x1, p.x);
    y0 = Math.min(y0, p.y);
    y1 = Math.max(y1, p.y);
  }
  const w = Math.max(1e-6, x1 - x0);
  const h = Math.max(1e-6, y1 - y0);
  const pad = Math.max(w, h) * margin;
  return { x: x0 - pad, y: y0 - pad, w: w + 2 * pad, h: h + 2 * pad };
}

const f = (n: number) => (Math.round(n * 1e4) / 1e4).toString();

export function pathData(points: readonly MapPoint[], close = false): string {
  if (points.length < 2) return "";
  return `M${points.map((p) => `${f(p.x)} ${f(p.y)}`).join(" L")}${close ? " Z" : ""}`;
}

/** Points whose lap fraction lies in [start, end], going forward and wrapping past the start line when end < start. */
export function segment(points: readonly MapPoint[], start: number, end: number): MapPoint[] {
  const wraps = end < start;
  return points.filter((p) => (wraps ? p.lap_pct >= start || p.lap_pct <= end : p.lap_pct >= start && p.lap_pct <= end));
}

/** Position at a lap fraction, interpolated between the two points around it (nearest point when the path has gaps). */
export function pointAt(points: readonly MapPoint[], pct: number): { x: number; y: number } | null {
  if (points.length === 0) return null;
  let best = 0;
  let bestD = Infinity;
  for (let i = 0; i < points.length; i++) {
    const d = Math.abs(points[i]!.lap_pct - pct);
    const wrapped = Math.min(d, 1 - d);
    if (wrapped < bestD) {
      bestD = wrapped;
      best = i;
    }
  }
  const a = points[best]!;
  const b = points[(best + 1) % points.length]!;
  const span = (b.lap_pct - a.lap_pct + 1) % 1;
  const into = (pct - a.lap_pct + 1) % 1;
  if (span > 0 && into <= span && span < 0.05) {
    const t = into / span;
    return { x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t };
  }
  return { x: a.x, y: a.y };
}

/** Lap fraction of the path point nearest to a position (for clicking the track to set a turn bound). */
export function nearestPct(points: readonly MapPoint[], x: number, y: number): number | null {
  let best: MapPoint | null = null;
  let bestD = Infinity;
  for (const p of points) {
    const d = (p.x - x) ** 2 + (p.y - y) ** 2;
    if (d < bestD) {
      bestD = d;
      best = p;
    }
  }
  return best ? best.lap_pct : null;
}

/**
 * Marker text. An official "Turn 4" shows "4". A detected zone is not an official turn, so it shows "Z4" whatever the
 * label's wording; "Load zone 2" is "Z2"; anything else is its first four characters.
 */
export function shortLabel(label: string, official = true): string {
  const zone = /^(?:turn|load zone|zone) (\S+)$/i.exec(label.trim());
  if (zone) return official && /^turn /i.test(label.trim()) ? zone[1]! : `Z${zone[1]}`;
  return label.length <= 4 ? label : label.slice(0, 4);
}

import { principalAxisRotation, rotatePoint } from "../investigation/geometry/rotation";

/**
 * The track silhouette on an event card: the analysis geometry's main path, turned so its long axis is horizontal by the same rule
 * as the investigation map (backlog #81, `investigation/geometry/rotation.ts`), fitted to a small box, with a tick at the line.
 * No geometry (or too few points to be a circuit) gives no silhouette.
 */
export interface Silhouette {
  /** SVG path data inside a `width` x `height` box. */
  d: string;
  /** Start/finish tick: x1, y1, x2, y2. */
  tick: [number, number, number, number];
  width: number;
  height: number;
}

export const SIL_WIDTH = 118;
export const SIL_HEIGHT = 78;
const MIN_POINTS = 8;
const MAX_POINTS = 72;
const PAD = 7;

const r1 = (v: number) => Math.round(v * 10) / 10;

export function silhouetteOf(xs: readonly number[] | undefined, ys: readonly number[] | undefined, width = SIL_WIDTH, height = SIL_HEIGHT): Silhouette | null {
  if (!xs || !ys) return null;
  const n = Math.min(xs.length, ys.length);
  const pts: { x: number; y: number }[] = [];
  for (let i = 0; i < n; i++) {
    const x = xs[i]!;
    const y = ys[i]!;
    if (Number.isFinite(x) && Number.isFinite(y)) pts.push({ x, y });
  }
  if (pts.length < MIN_POINTS) return null;
  const stride = Math.max(1, Math.ceil(pts.length / MAX_POINTS));
  const thinned = pts.filter((_, i) => i % stride === 0);
  const last = pts[pts.length - 1]!;
  if (thinned[thinned.length - 1] !== last) thinned.push(last);
  // The turn comes from every point (the path starts at the line), as the map's does, so card and map agree.
  const rotation = principalAxisRotation(pts, pts[0]);
  const picked = thinned.map((p) => rotatePoint(rotation, p.x, p.y));

  let minX = Infinity;
  let maxX = -Infinity;
  let minY = Infinity;
  let maxY = -Infinity;
  for (const p of picked) {
    minX = Math.min(minX, p.x);
    maxX = Math.max(maxX, p.x);
    minY = Math.min(minY, p.y);
    maxY = Math.max(maxY, p.y);
  }
  const bw = maxX - minX;
  const bh = maxY - minY;
  if (!(bw > 0) || !(bh > 0) || Math.min(bw, bh) < Math.max(bw, bh) * 0.02) return null;
  const scale = Math.min((width - 2 * PAD) / bw, (height - 2 * PAD) / bh);
  const ox = (width - bw * scale) / 2;
  const oy = (height - bh * scale) / 2;
  const at = (p: { x: number; y: number }) => ({ x: ox + (p.x - minX) * scale, y: oy + (p.y - minY) * scale });
  const mapped = picked.map(at);
  const first = mapped[0]!;
  const end = mapped[mapped.length - 1]!;
  const closed = Math.hypot(end.x - first.x, end.y - first.y) < Math.hypot(bw, bh) * scale * 0.08;
  const d = mapped.map((p, i) => `${i === 0 ? "M" : "L"}${r1(p.x)},${r1(p.y)}`).join("") + (closed ? "Z" : "");

  const next = mapped.find((p) => Math.hypot(p.x - first.x, p.y - first.y) > 1.5) ?? mapped[1]!;
  let tx = next.x - first.x;
  let ty = next.y - first.y;
  const len = Math.hypot(tx, ty) || 1;
  tx /= len;
  ty /= len;
  const half = 5.5;
  return { d, tick: [r1(first.x - ty * half), r1(first.y + tx * half), r1(first.x + ty * half), r1(first.y - tx * half)], width, height };
}

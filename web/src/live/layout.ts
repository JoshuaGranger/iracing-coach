import type { Schemas } from "../core/api";
import { TILES, type TileSpec } from "../gen/live-channels";
import { TILE_BY_ID } from "./tiles";

export type LayoutBody = Schemas["LiveLayoutBody"];
export type LiveTileCfg = Schemas["LiveTile"];
export type TileStyle = Schemas["LiveTileStyle"];

export const MAX_GRID = 8;
export const STYLES: readonly TileStyle[] = ["number", "gauge", "bar", "trend", "status"];
export const ACCENTS = ["default", "blue", "green", "amber", "coral", "violet"] as const;
export const TREND_WINDOWS = [
  ["s15", "15 s"],
  ["s30", "30 s"],
  ["s60", "60 s"],
  ["one_lap", "1 lap"],
  ["three_laps", "3 laps"],
] as const;

/** Every tile allows Number, Gauge, Bar and Trend; the four status tiles also allow Status (live-stack 2.4). */
export function allowedStyles(spec: TileSpec): readonly TileStyle[] {
  return spec.style === "status" ? ["status", "number", "gauge", "bar", "trend"] : ["number", "gauge", "bar", "trend"];
}

export function defaultStyle(spec: TileSpec): TileStyle {
  return (STYLES as readonly string[]).includes(spec.style) ? (spec.style as TileStyle) : "number";
}

export interface LayoutIssue {
  tileId: string | null;
  message: string;
}

const inRange = (n: number, lo: number, hi: number) => Number.isInteger(n) && n >= lo && n <= hi;

/** Structural checks the server and the rig also apply: grid 1-8, tiles inside it, none overlapping, known metrics and styles. */
export function validateLayout(body: LayoutBody): LayoutIssue[] {
  const issues: LayoutIssue[] = [];
  if (!inRange(body.rows, 1, MAX_GRID) || !inRange(body.columns, 1, MAX_GRID)) issues.push({ tileId: null, message: "Rows and columns must be 1 to 8." });
  const seen = new Set<string>();
  const taken = new Map<string, string>();
  for (const t of body.tiles) {
    const bad = (message: string) => issues.push({ tileId: t.tile_id, message });
    if (seen.has(t.tile_id)) bad("Duplicate tile id.");
    seen.add(t.tile_id);
    const spec = TILE_BY_ID.get(t.metric);
    if (!spec) bad(`Unknown metric ${t.metric}.`);
    else {
      if (!allowedStyles(spec).includes(t.style)) bad(`${spec.label} cannot show as ${t.style}.`);
      if (t.unit && spec.units.length > 0 && !spec.units.includes(t.unit)) bad(`${spec.label} has no unit ${t.unit}.`);
    }
    if (t.precision != null && !inRange(t.precision, 0, 3)) bad("Precision must be 0 to 3.");
    if (!inRange(t.row, 0, 7) || !inRange(t.column, 0, 7) || !inRange(t.row_span, 1, 8) || !inRange(t.column_span, 1, 8)) {
      bad("Tile position is out of range.");
      continue;
    }
    if (t.row + t.row_span > body.rows || t.column + t.column_span > body.columns) {
      bad("Tile does not fit the grid.");
      continue;
    }
    for (let r = t.row; r < t.row + t.row_span; r++)
      for (let c = t.column; c < t.column + t.column_span; c++) {
        const k = `${r},${c}`;
        const other = taken.get(k);
        if (other && other !== t.tile_id) bad(`Overlaps ${other}.`);
        taken.set(k, t.tile_id);
      }
  }
  return issues;
}

type Rect = Pick<LiveTileCfg, "row" | "column" | "row_span" | "column_span">;

export function fits(body: LayoutBody, rect: Rect, ignoreId?: string): boolean {
  if (rect.row < 0 || rect.column < 0 || rect.row_span < 1 || rect.column_span < 1) return false;
  if (rect.row + rect.row_span > body.rows || rect.column + rect.column_span > body.columns) return false;
  return !body.tiles.some(
    (t) =>
      t.tile_id !== ignoreId &&
      rect.row < t.row + t.row_span &&
      t.row < rect.row + rect.row_span &&
      rect.column < t.column + t.column_span &&
      t.column < rect.column + rect.column_span,
  );
}

/** First free spot, row-major, for a tile of the given span (the C# deterministic first-fit order). */
export function findFreeSpot(body: LayoutBody, rowSpan = 1, columnSpan = 1): { row: number; column: number } | null {
  for (let r = 0; r + rowSpan <= body.rows; r++)
    for (let c = 0; c + columnSpan <= body.columns; c++) if (fits(body, { row: r, column: c, row_span: rowSpan, column_span: columnSpan })) return { row: r, column: c };
  return null;
}

export function newTileId(body: LayoutBody, metric: string): string {
  const ids = new Set(body.tiles.map((t) => t.tile_id));
  for (let n = 1; ; n++) {
    const id = `${metric}-${n}`;
    if (!ids.has(id)) return id;
  }
}

export function addTile(body: LayoutBody, metric: string): LayoutBody | null {
  const spec = TILE_BY_ID.get(metric);
  if (!spec) return null;
  const span = spec.style === "status" ? { row_span: 1, column_span: Math.min(2, body.columns) } : { row_span: 1, column_span: 1 };
  const spot = findFreeSpot(body, span.row_span, span.column_span) ?? findFreeSpot(body, 1, 1);
  if (!spot) return null;
  const fitted = fits(body, { ...spot, ...span }) ? span : { row_span: 1, column_span: 1 };
  const tile: LiveTileCfg = { tile_id: newTileId(body, metric), metric, ...spot, ...fitted, style: defaultStyle(spec), highlight_abs: false };
  return { ...body, tiles: [...body.tiles, tile] };
}

export function removeTile(body: LayoutBody, tileId: string): LayoutBody {
  return { ...body, tiles: body.tiles.filter((t) => t.tile_id !== tileId) };
}

export function updateTile(body: LayoutBody, tileId: string, patch: Partial<LiveTileCfg>): LayoutBody {
  return { ...body, tiles: body.tiles.map((t) => (t.tile_id === tileId ? { ...t, ...patch } : t)) };
}

/** Move or resize; refused (null) when the result would leave the grid or overlap another tile. */
export function placeTile(body: LayoutBody, tileId: string, rect: Rect): LayoutBody | null {
  return fits(body, rect, tileId) ? updateTile(body, tileId, rect) : null;
}

/** Change the grid size; refused when a tile would no longer fit. */
export function resizeGrid(body: LayoutBody, rows: number, columns: number): LayoutBody | null {
  if (!inRange(rows, 1, MAX_GRID) || !inRange(columns, 1, MAX_GRID)) return null;
  const next = { ...body, rows, columns };
  return body.tiles.every((t) => t.row + t.row_span <= rows && t.column + t.column_span <= columns) ? next : null;
}

/** Replace a tile's metric, resetting unit, precision and style to the new metric's defaults. */
export function swapMetric(body: LayoutBody, tileId: string, metric: string): LayoutBody | null {
  const spec = TILE_BY_ID.get(metric);
  if (!spec) return null;
  return {
    ...body,
    tiles: body.tiles.map((t) => (t.tile_id === tileId ? { tile_id: t.tile_id, metric, row: t.row, column: t.column, row_span: t.row_span, column_span: t.column_span, style: defaultStyle(spec), highlight_abs: false } : t)),
  };
}

export { newLayoutId, slugify } from "../core/ids";

export const METRICS: readonly TileSpec[] = TILES;

import type { TileSpec } from "../gen/live-channels";
import { type LayoutBody, type LiveTileCfg, MAX_GRID, findFreeSpot, fits, newTileId } from "./layout";
import { defaultTileStyle } from "./representation";
import { TILE_BY_ID } from "./tiles";

/**
 * The direct-manipulation model of the Live editor: grid snapping, move or swap, resize with a placement preview, drag-in and
 * select-then-place. Pure functions over the layout body; the grid and the page hold no geometry rules of their own.
 */
export type Rect = Pick<LiveTileCfg, "row" | "column" | "row_span" | "column_span">;
export type Verdict = "valid" | "swap" | "invalid";
export type GestureMode = "move" | "resize" | "add";

export interface Preview {
  mode: GestureMode;
  rect: Rect;
  verdict: Verdict;
  /** The tile being moved or resized. */
  tileId?: string;
  /** The equal-sized tile a drop would swap with. */
  swapWith?: string;
  /** The metric being dragged in from the palette. */
  metric?: string;
}

export interface Cell {
  row: number;
  col: number;
}

export interface Box {
  left: number;
  top: number;
  width: number;
  height: number;
}

/**
 * The grid cell under a point. `clamp` keeps a point outside the grid on its nearest edge cell (a tile drag); without it a point
 * outside is no cell (a palette drag that has not reached the grid). Track sizes are equal, so the gap is shared evenly.
 */
export function cellFromPoint(box: Box, rows: number, columns: number, x: number, y: number, clamp: boolean): Cell | null {
  const fx = (x - box.left) / box.width;
  const fy = (y - box.top) / box.height;
  if (!clamp && (fx < 0 || fx >= 1 || fy < 0 || fy >= 1 || !Number.isFinite(fx) || !Number.isFinite(fy))) return null;
  const pin = (f: number, n: number) => Math.min(n - 1, Math.max(0, Math.floor((Number.isFinite(f) ? f : 0) * n)));
  return { row: pin(fy, rows), col: pin(fx, columns) };
}

export const sameRect = (a: Rect, b: Rect): boolean => a.row === b.row && a.column === b.column && a.row_span === b.row_span && a.column_span === b.column_span;
const rectOf = (t: LiveTileCfg): Rect => ({ row: t.row, column: t.column, row_span: t.row_span, column_span: t.column_span });
const overlaps = (a: Rect, b: Rect) => a.row < b.row + b.row_span && b.row < a.row + a.row_span && a.column < b.column + b.column_span && b.column < a.column + a.column_span;

/** The rect moved so it lies inside the grid (snapping at the edges), its size unchanged. */
export function clampRect(body: LayoutBody, rect: Rect): Rect {
  const row_span = Math.min(rect.row_span, body.rows);
  const column_span = Math.min(rect.column_span, body.columns);
  return {
    row: Math.min(Math.max(0, rect.row), body.rows - row_span),
    column: Math.min(Math.max(0, rect.column), body.columns - column_span),
    row_span,
    column_span,
  };
}

/** A tile dropped on `rect`: free space moves it; exactly one equal-sized tile filling `rect` swaps with it; anything else is refused. */
export function moveVerdict(body: LayoutBody, tileId: string, rect: Rect): { verdict: Verdict; swapWith?: string } {
  const tile = body.tiles.find((t) => t.tile_id === tileId);
  if (!tile) return { verdict: "invalid" };
  if (fits(body, rect, tileId)) return { verdict: "valid" };
  const hit = body.tiles.filter((t) => t.tile_id !== tileId && overlaps(rect, t));
  if (hit.length === 1 && sameRect(rectOf(hit[0]!), rect) && hit[0]!.row_span === tile.row_span && hit[0]!.column_span === tile.column_span) {
    return { verdict: "swap", swapWith: hit[0]!.tile_id };
  }
  return { verdict: "invalid" };
}

/** Two tiles of the same size trade places. */
export function swapTiles(body: LayoutBody, a: string, b: string): LayoutBody | null {
  const ta = body.tiles.find((t) => t.tile_id === a);
  const tb = body.tiles.find((t) => t.tile_id === b);
  if (!ta || !tb || ta === tb || ta.row_span !== tb.row_span || ta.column_span !== tb.column_span) return null;
  return {
    ...body,
    tiles: body.tiles.map((t) => (t === ta ? { ...t, row: tb.row, column: tb.column } : t === tb ? { ...t, row: ta.row, column: ta.column } : t)),
  };
}

/** Apply a move: the moved layout, the swapped layout, or null when refused or unchanged. */
export function applyMove(body: LayoutBody, tileId: string, rect: Rect): LayoutBody | null {
  const tile = body.tiles.find((t) => t.tile_id === tileId);
  if (!tile || sameRect(rectOf(tile), rect)) return null;
  const v = moveVerdict(body, tileId, rect);
  if (v.verdict === "valid") return { ...body, tiles: body.tiles.map((t) => (t.tile_id === tileId ? { ...t, ...rect } : t)) };
  if (v.verdict === "swap") return swapTiles(body, tileId, v.swapWith!);
  return null;
}

/** The rect a move drag targets: the grabbed cell keeps its offset inside the tile; the result snaps inside the grid. */
export function moveRect(body: LayoutBody, tile: Rect, grab: Cell, cell: Cell): Rect {
  return clampRect(body, { row: tile.row + cell.row - grab.row, column: tile.column + cell.col - grab.col, row_span: tile.row_span, column_span: tile.column_span });
}

/** The rect a corner drag targets: the origin stays, the far corner follows the pointer's cell (at least one cell). */
export function resizeRect(tile: Rect, cell: Cell): Rect {
  return { row: tile.row, column: tile.column, row_span: Math.max(1, cell.row - tile.row + 1), column_span: Math.max(1, cell.col - tile.column + 1) };
}

export function resizeVerdict(body: LayoutBody, tileId: string, rect: Rect): Verdict {
  return fits(body, rect, tileId) ? "valid" : "invalid";
}

export function applyResize(body: LayoutBody, tileId: string, rect: Rect): LayoutBody | null {
  const tile = body.tiles.find((t) => t.tile_id === tileId);
  if (!tile || sameRect(rectOf(tile), rect) || !fits(body, rect, tileId)) return null;
  return { ...body, tiles: body.tiles.map((t) => (t.tile_id === tileId ? { ...t, ...rect } : t)) };
}

/** A new tile's size: two columns for the status metrics where the grid allows, else one cell. */
export function defaultSpan(spec: TileSpec, body: LayoutBody): { row_span: number; column_span: number } {
  return { row_span: 1, column_span: spec.style === "status" ? Math.min(2, body.columns) : 1 };
}

/** The rect a palette drag targets at a cell. */
export function addRect(body: LayoutBody, metric: string, cell: Cell): Rect | null {
  const spec = TILE_BY_ID.get(metric);
  if (!spec) return null;
  return clampRect(body, { row: cell.row, column: cell.col, ...defaultSpan(spec, body) });
}

function newTile(body: LayoutBody, spec: TileSpec, rect: Rect): LiveTileCfg {
  return { tile_id: newTileId(body, spec.id), metric: spec.id, ...rect, style: defaultTileStyle(spec), highlight_abs: false };
}

/** Drop a metric from the palette on `rect`; refused where it would overlap or leave the grid. */
export function addAt(body: LayoutBody, metric: string, rect: Rect): LayoutBody | null {
  const spec = TILE_BY_ID.get(metric);
  if (!spec || !fits(body, rect)) return null;
  return { ...body, tiles: [...body.tiles, newTile(body, spec, rect)] };
}

/** Tap-to-add: the first free spot, row-major, at the default size, else one cell; null when the grid is full. */
export function addFirstFree(body: LayoutBody, metric: string): LayoutBody | null {
  const spec = TILE_BY_ID.get(metric);
  if (!spec) return null;
  const span = defaultSpan(spec, body);
  const at = findFreeSpot(body, span.row_span, span.column_span);
  if (at) return addAt(body, metric, { ...at, ...span });
  const one = findFreeSpot(body, 1, 1);
  return one ? addAt(body, metric, { ...one, row_span: 1, column_span: 1 }) : null;
}

/** Tap-to-add on a full grid adds a row (then a column) up to the 8 x 8 limit, so the palette is never a dead end. */
export function addGrowing(body: LayoutBody, metric: string): LayoutBody | null {
  const direct = addFirstFree(body, metric);
  if (direct) return direct;
  if (body.rows < MAX_GRID) return addFirstFree({ ...body, rows: body.rows + 1 }, metric);
  if (body.columns < MAX_GRID) return addFirstFree({ ...body, columns: body.columns + 1 }, metric);
  return null;
}

export function gridFull(body: LayoutBody): boolean {
  return findFreeSpot(body, 1, 1) === null;
}

export interface PlaceTargets {
  /** Origins where the tile fits as it is. */
  cells: Cell[];
  /** Equal-sized tiles it can trade places with. */
  swaps: string[];
}

/** Select-then-place: every origin the selected tile can move to, and every tile it can swap with. */
export function placeTargets(body: LayoutBody, tileId: string): PlaceTargets {
  const tile = body.tiles.find((t) => t.tile_id === tileId);
  if (!tile) return { cells: [], swaps: [] };
  const cells: Cell[] = [];
  for (let row = 0; row + tile.row_span <= body.rows; row++)
    for (let col = 0; col + tile.column_span <= body.columns; col++) {
      if (row === tile.row && col === tile.column) continue;
      if (fits(body, { row, column: col, row_span: tile.row_span, column_span: tile.column_span }, tileId)) cells.push({ row, col });
    }
  const swaps = body.tiles.filter((t) => t.tile_id !== tileId && t.row_span === tile.row_span && t.column_span === tile.column_span).map((t) => t.tile_id);
  return { cells, swaps };
}

/** Width or height one step larger or smaller, from the same origin; null when it would overlap, leave the grid or vanish. */
export function stepSize(body: LayoutBody, tileId: string, axis: "width" | "height", delta: 1 | -1): LayoutBody | null {
  const tile = body.tiles.find((t) => t.tile_id === tileId);
  if (!tile) return null;
  const rect = rectOf(tile);
  if (axis === "width") rect.column_span += delta;
  else rect.row_span += delta;
  if (rect.row_span < 1 || rect.column_span < 1) return null;
  return applyResize(body, tileId, rect);
}

/** One-tap Undo: the bodies before each completed gesture, newest last, bounded. */
export const UNDO_LIMIT = 50;
export function pushUndo(stack: readonly LayoutBody[], body: LayoutBody): LayoutBody[] {
  const next = [...stack, body];
  return next.length > UNDO_LIMIT ? next.slice(next.length - UNDO_LIMIT) : next;
}

import type { RefObject } from "preact";
import { useMemo, useRef } from "preact/hooks";
import { type Store, useStore } from "../core/store";
import { type Cell, type Preview, type Rect, applyMove, applyResize, cellFromPoint, moveRect, moveVerdict, placeTargets, resizeRect, resizeVerdict, sameRect, swapTiles } from "./edit-model";
import type { LiveFrame } from "./frame";
import type { LiveHistory } from "./history";
import { type LayoutBody, type LiveTileCfg, placeTile } from "./layout";
import { TileView } from "./tile-view";
import { TILE_BY_ID } from "./tiles";

/** What the grid needs while the layout is edited. Absent, the grid is the plain board and draws nothing else. */
export interface GridEdit {
  selectedId: string | null;
  /** Select-then-place: the tile waiting for a target. */
  placingId: string | null;
  /** The placement preview, shared with the palette's drag-in. */
  preview: Store<Preview | null>;
  /** The grid element, for the palette to measure. */
  gridRef: RefObject<HTMLDivElement>;
  onSelect(id: string | null): void;
  /** A completed gesture: the new layout. */
  onCommit(body: LayoutBody, select?: string | null): void;
}

interface GridProps {
  body: LayoutBody;
  frame: LiveFrame | null;
  history: LiveHistory;
  tick: number;
  edit?: GridEdit | null;
  /** Phone flow: two columns in row-major order instead of the layout's own geometry (tiles stay readable at 390 px). */
  flow?: boolean;
}

interface Drag {
  id: string;
  mode: "move" | "resize";
  pointerId: number;
  startX: number;
  startY: number;
  grab: Cell;
  tile: Rect;
  /** A mouse press on the tile body becomes a move after a few pixels; a handle starts at once. */
  armed: boolean;
}

const DRAG_SLOP_PX = 4;
const area = (r: Rect) => ({ gridRow: `${r.row + 1} / span ${r.row_span}`, gridColumn: `${r.column + 1} / span ${r.column_span}` });

/**
 * The dashboard grid, shared by the board and the editor. Editing: drag the grip (or, with a mouse, the tile) to move; drop on
 * an equal-sized tile to swap; drag the corner to resize, with a preview that says whether the placement is valid; arrow keys
 * move and Shift+arrows resize. The board's per-frame work is the tiles; the edit layer is memoised and redraws only when the
 * layout, the selection or the preview changes.
 */
export function LiveGrid({ body, frame, history, tick, edit = null, flow = false }: GridProps) {
  const ownRef = useRef<HTMLDivElement>(null);
  const ref = edit?.gridRef ?? ownRef;
  const drag = useRef<Drag | null>(null);
  const editing = edit !== null;
  const preview = useStore(edit?.preview ?? NO_PREVIEW);

  const cellAt = (e: PointerEvent): Cell => {
    const r = ref.current!.getBoundingClientRect();
    return cellFromPoint(r, body.rows, body.columns, e.clientX, e.clientY, true)!;
  };

  const begin = (e: PointerEvent, t: LiveTileCfg, mode: Drag["mode"], armed: boolean) => {
    if (!edit || e.button > 0) return;
    e.stopPropagation();
    if (armed) e.preventDefault();
    const target = e.currentTarget as HTMLElement;
    try {
      target.setPointerCapture(e.pointerId);
    } catch {
      /* synthetic events have no active pointer */
    }
    drag.current = { id: t.tile_id, mode, pointerId: e.pointerId, startX: e.clientX, startY: e.clientY, grab: cellAt(e), tile: { row: t.row, column: t.column, row_span: t.row_span, column_span: t.column_span }, armed };
  };

  const move = (e: PointerEvent) => {
    const d = drag.current;
    if (!d || !edit || e.pointerId !== d.pointerId) return;
    if (!d.armed) {
      if (Math.hypot(e.clientX - d.startX, e.clientY - d.startY) < DRAG_SLOP_PX) return;
      d.armed = true;
    }
    const c = cellAt(e);
    let next: Preview;
    if (d.mode === "move") {
      const rect = moveRect(body, d.tile, d.grab, c);
      next = { mode: "move", tileId: d.id, rect, ...moveVerdict(body, d.id, rect) };
    } else {
      const rect = resizeRect(d.tile, c);
      next = { mode: "resize", tileId: d.id, rect, verdict: resizeVerdict(body, d.id, rect) };
    }
    const prev = edit.preview.get();
    if (sameRect(next.rect, d.tile)) {
      if (prev) edit.preview.set(null);
    } else if (!prev || !sameRect(prev.rect, next.rect) || prev.verdict !== next.verdict) edit.preview.set(next);
  };

  const end = (e: PointerEvent, cancelled: boolean) => {
    const d = drag.current;
    if (!d || !edit || e.pointerId !== d.pointerId) return;
    drag.current = null;
    const p = edit.preview.get();
    edit.preview.set(null);
    if (cancelled) return;
    if (!d.armed || !p || p.tileId !== d.id) {
      edit.onSelect(d.id);
      return;
    }
    const next = p.mode === "move" ? applyMove(body, d.id, p.rect) : applyResize(body, d.id, p.rect);
    if (next) edit.onCommit(next, d.id);
    else edit.onSelect(d.id);
  };

  const key = (e: KeyboardEvent, t: LiveTileCfg) => {
    if (!edit) return;
    const delta: Record<string, [number, number]> = { ArrowUp: [-1, 0], ArrowDown: [1, 0], ArrowLeft: [0, -1], ArrowRight: [0, 1] };
    const d = delta[e.key];
    if (!d) return;
    e.preventDefault();
    const rect = e.shiftKey
      ? { row: t.row, column: t.column, row_span: t.row_span + d[0], column_span: t.column_span + d[1] }
      : { row: t.row + d[0], column: t.column + d[1], row_span: t.row_span, column_span: t.column_span };
    const next = placeTile(body, t.tile_id, rect);
    if (next) edit.onCommit(next, t.tile_id);
  };

  const selectedId = edit?.selectedId ?? null;
  const placingId = edit?.placingId ?? null;
  const layer = useMemo(() => (edit ? <EditLayer body={body} edit={edit} preview={preview} /> : null), [body, edit?.selectedId, edit?.placingId, preview, edit?.onCommit, edit?.onSelect]);

  return (
    <div
      ref={ref}
      class={`live-grid${editing ? " le-grid" : ""}${placingId ? " le-placing" : ""}${flow ? " flow" : ""}`}
      style={
        flow
          ? { gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gridAutoRows: "minmax(var(--tile-min-h), auto)" }
          : { gridTemplateRows: `repeat(${body.rows}, minmax(var(--tile-min-h), 1fr))`, gridTemplateColumns: `repeat(${body.columns}, minmax(0, 1fr))` }
      }
      onPointerMove={editing ? move : undefined}
      onPointerUp={editing ? (e) => end(e, false) : undefined}
      onPointerCancel={editing ? (e) => end(e, true) : undefined}
      onClick={editing ? () => edit!.onSelect(null) : undefined}
    >
      {layer}
      {(flow ? [...body.tiles].sort((a, b) => a.row - b.row || a.column - b.column) : body.tiles).map((t) => {
        const spec = TILE_BY_ID.get(t.metric);
        if (!spec) return null;
        if (!editing)
          return (
            <div
              key={t.tile_id}
              class="tile-cell"
              style={flow ? { gridColumn: `span ${t.column_span > 1 || t.style === "status" || t.style === "trend" ? 2 : 1}` } : area(t)}
            >
              <TileView cfg={t} spec={spec} frame={frame} history={history} tick={tick} />
            </div>
          );
        const selected = selectedId === t.tile_id;
        const lifted = preview?.tileId === t.tile_id;
        return (
          <div
            key={t.tile_id}
            class={`tile-cell le-cell${selected ? " selected" : ""}${lifted ? " le-lifted" : ""}`}
            style={area(t)}
            role="button"
            tabIndex={0}
            aria-pressed={selected}
            aria-label={`${spec.label} tile`}
            data-tile={t.tile_id}
            onPointerDown={(e) => {
              if (e.pointerType === "mouse") begin(e, t, "move", false);
            }}
            onClick={(e) => {
              e.stopPropagation();
              edit!.onSelect(t.tile_id);
            }}
            onKeyDown={(e) => key(e, t)}
          >
            <TileView cfg={t} spec={spec} frame={frame} history={history} tick={tick} />
            <span class="le-grip" role="presentation" title="Move" onPointerDown={(e) => begin(e, t, "move", true)} onClick={(e) => e.stopPropagation()}>
              <GripGlyph />
            </span>
            <span class="le-corner" role="presentation" title="Resize" onPointerDown={(e) => begin(e, t, "resize", true)} onClick={(e) => e.stopPropagation()} />
          </div>
        );
      })}
    </div>
  );
}

const NO_PREVIEW: Store<Preview | null> = { get: () => null, set: () => undefined, update: () => undefined, subscribe: () => () => undefined };

function GripGlyph() {
  return (
    <svg viewBox="0 0 12 18" width="12" height="18" aria-hidden="true">
      {[3, 9, 15].map((y) => (
        <g key={y}>
          <circle cx="3" cy={y} r="1.6" />
          <circle cx="9" cy={y} r="1.6" />
        </g>
      ))}
    </svg>
  );
}

/** Empty cells, the placement preview and, while placing, the targets. Drawn under and over the tiles by z-order. */
function EditLayer({ body, edit, preview }: { body: LayoutBody; edit: GridEdit; preview: Preview | null }) {
  const taken = new Set<string>();
  for (const t of body.tiles)
    for (let r = t.row; r < t.row + t.row_span; r++) for (let c = t.column; c < t.column + t.column_span; c++) taken.add(`${r},${c}`);
  const slots: Cell[] = [];
  for (let row = 0; row < body.rows; row++) for (let col = 0; col < body.columns; col++) if (!taken.has(`${row},${col}`)) slots.push({ row, col });

  const placing = edit.placingId ? body.tiles.find((t) => t.tile_id === edit.placingId) ?? null : null;
  const targets = placing ? placeTargets(body, placing.tile_id) : null;
  const label = (id: string) => TILE_BY_ID.get(body.tiles.find((t) => t.tile_id === id)?.metric ?? "")?.label ?? id;

  return (
    <>
      {slots.map((s) => (
        <div key={`slot-${s.row}-${s.col}`} class="le-slot" style={area({ row: s.row, column: s.col, row_span: 1, column_span: 1 })} aria-hidden="true" />
      ))}
      {preview ? (
        <div
          class={`le-preview ${preview.verdict}`}
          style={area(preview.rect)}
          data-verdict={preview.verdict}
          data-testid="le-preview"
          aria-hidden="true"
        >
          <span>{preview.verdict === "swap" ? "Swap" : preview.verdict === "invalid" ? "No room" : `${preview.rect.column_span}×${preview.rect.row_span}`}</span>
        </div>
      ) : null}
      {placing && targets
        ? [
            ...targets.cells.map((c) => (
              <button
                key={`to-${c.row}-${c.col}`}
                type="button"
                class="le-target"
                style={area({ row: c.row, column: c.col, row_span: 1, column_span: 1 })}
                aria-label={`Place at row ${c.row + 1}, column ${c.col + 1}`}
                onClick={(e) => {
                  e.stopPropagation();
                  const next = applyMove(body, placing.tile_id, { row: c.row, column: c.col, row_span: placing.row_span, column_span: placing.column_span });
                  if (next) edit.onCommit(next, placing.tile_id);
                }}
              >
                <span>Here</span>
              </button>
            )),
            ...targets.swaps.map((id) => {
              const other = body.tiles.find((t) => t.tile_id === id)!;
              return (
                <button
                  key={`swap-${id}`}
                  type="button"
                  class="le-target swap"
                  style={area(other)}
                  aria-label={`Swap with ${label(id)}`}
                  onClick={(e) => {
                    e.stopPropagation();
                    const next = swapTiles(body, placing.tile_id, id);
                    if (next) edit.onCommit(next, placing.tile_id);
                  }}
                >
                  <span>Swap</span>
                </button>
              );
            }),
          ]
        : null}
    </>
  );
}

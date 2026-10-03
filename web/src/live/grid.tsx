import { useRef } from "preact/hooks";
import type { LiveFrame } from "./frame";
import type { LiveHistory } from "./history";
import { type LayoutBody, type LiveTileCfg, placeTile } from "./layout";
import { TileView } from "./tile-view";
import { TILE_BY_ID } from "./tiles";

interface GridProps {
  body: LayoutBody;
  frame: LiveFrame | null;
  history: LiveHistory;
  tick: number;
  observed: Map<string, { min: number; max: number }>;
  editing?: boolean;
  selectedId?: string | null;
  onSelect?: (id: string | null) => void;
  onChange?: (body: LayoutBody) => void;
  /** Phone flow: two columns in row-major order instead of the layout's own geometry (tiles stay readable at 390 px). */
  flow?: boolean;
}

interface Drag {
  id: string;
  mode: "move" | "resize";
  offRow: number;
  offCol: number;
}

/** The dashboard grid, shared by the page and the layout editor. Editing: drag a tile to move it, its corner to resize, arrow keys move, Shift+arrows resize. */
export function LiveGrid({ body, frame, history, tick, observed, editing = false, selectedId = null, onSelect, onChange, flow = false }: GridProps) {
  const ref = useRef<HTMLDivElement>(null);
  const drag = useRef<Drag | null>(null);

  const cellAt = (e: PointerEvent) => {
    const r = ref.current!.getBoundingClientRect();
    return {
      row: Math.min(body.rows - 1, Math.max(0, Math.floor(((e.clientY - r.top) / r.height) * body.rows))),
      col: Math.min(body.columns - 1, Math.max(0, Math.floor(((e.clientX - r.left) / r.width) * body.columns))),
    };
  };

  const down = (e: PointerEvent, t: LiveTileCfg, mode: Drag["mode"]) => {
    if (!editing) return;
    e.preventDefault();
    e.stopPropagation();
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    const c = cellAt(e);
    drag.current = { id: t.tile_id, mode, offRow: c.row - t.row, offCol: c.col - t.column };
    onSelect?.(t.tile_id);
  };
  const move = (e: PointerEvent) => {
    const d = drag.current;
    if (!d || !onChange) return;
    const t = body.tiles.find((x) => x.tile_id === d.id);
    if (!t) return;
    const c = cellAt(e);
    const rect =
      d.mode === "move"
        ? { row: c.row - d.offRow, column: c.col - d.offCol, row_span: t.row_span, column_span: t.column_span }
        : { row: t.row, column: t.column, row_span: Math.max(1, c.row - t.row + 1), column_span: Math.max(1, c.col - t.column + 1) };
    const next = placeTile(body, d.id, rect);
    if (next) onChange(next);
  };
  const up = () => {
    drag.current = null;
  };

  const key = (e: KeyboardEvent, t: LiveTileCfg) => {
    if (!editing || !onChange) return;
    const delta: Record<string, [number, number]> = { ArrowUp: [-1, 0], ArrowDown: [1, 0], ArrowLeft: [0, -1], ArrowRight: [0, 1] };
    const d = delta[e.key];
    if (!d) return;
    e.preventDefault();
    const rect = e.shiftKey
      ? { row: t.row, column: t.column, row_span: t.row_span + d[0], column_span: t.column_span + d[1] }
      : { row: t.row + d[0], column: t.column + d[1], row_span: t.row_span, column_span: t.column_span };
    const next = placeTile(body, t.tile_id, rect);
    if (next) onChange(next);
  };

  return (
    <div
      ref={ref}
      class={`live-grid${editing ? " editing" : ""}${flow ? " flow" : ""}`}
      style={
        flow
          ? { gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gridAutoRows: "minmax(var(--tile-min-h), auto)" }
          : { gridTemplateRows: `repeat(${body.rows}, minmax(var(--tile-min-h), 1fr))`, gridTemplateColumns: `repeat(${body.columns}, minmax(0, 1fr))` }
      }
      onPointerMove={move}
      onPointerUp={up}
      onPointerCancel={up}
      onClick={() => editing && onSelect?.(null)}
    >
      {(flow ? [...body.tiles].sort((a, b) => a.row - b.row || a.column - b.column) : body.tiles).map((t) => {
        const spec = TILE_BY_ID.get(t.metric);
        if (!spec) return null;
        const selected = selectedId === t.tile_id;
        return (
          <div
            key={t.tile_id}
            class={`tile-cell${selected ? " selected" : ""}`}
            style={flow ? { gridColumn: `span ${t.column_span > 1 || t.style === "status" || t.style === "trend" ? 2 : 1}` } : { gridRow: `${t.row + 1} / span ${t.row_span}`, gridColumn: `${t.column + 1} / span ${t.column_span}` }}
            role={editing ? "button" : undefined}
            tabIndex={editing ? 0 : undefined}
            aria-pressed={editing ? selected : undefined}
            aria-label={editing ? `${spec.label} tile` : undefined}
            onPointerDown={(e) => down(e, t, "move")}
            onClick={(e) => {
              if (!editing) return;
              e.stopPropagation();
              onSelect?.(t.tile_id);
            }}
            onKeyDown={(e) => key(e, t)}
          >
            <TileView cfg={t} spec={spec} frame={frame} history={history} tick={tick} observed={observed} />
            {editing && selected ? <span class="resize-handle" aria-hidden="true" onPointerDown={(e) => down(e, t, "resize")} /> : null}
          </div>
        );
      })}
    </div>
  );
}

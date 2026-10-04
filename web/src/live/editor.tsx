import type { RefObject } from "preact";
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import type { Store } from "../core/store";
import { unitLabel } from "../gen/live-channels";
import { Segmented } from "../ui/segmented";
import { type Preview, addAt, addGrowing, addRect, cellFromPoint, stepSize } from "./edit-model";
import { Chip } from "../ui/kit";
import { type LayoutBody, type LiveTileCfg, MAX_GRID, METRICS, TREND_WINDOWS, fits, removeTile, resizeGrid, updateTile } from "./layout";
import { ACCENT_LABEL, type Accent, REPRESENTATION_LABEL, type RepresentationContext, colourChoices, representationOf, representationPatch, representations } from "./representation";
import { tileColor } from "./tile-view";
import { TILE_BY_ID, resolveUnit, usesPrecision } from "./tiles";

function Stepper({ label, value, min, max, canLess, canMore, onChange }: { label: string; value: number; min: number; max: number; canLess?: boolean; canMore?: boolean; onChange: (v: number) => void }) {
  return (
    <div class="stepper" role="group" aria-label={label}>
      <span class="stepper-label">{label}</span>
      <button type="button" aria-label={`${label} less`} disabled={value <= min || canLess === false} onClick={() => onChange(value - 1)}>
        −
      </button>
      <output>{value}</output>
      <button type="button" aria-label={`${label} more`} disabled={value >= max || canMore === false} onClick={() => onChange(value + 1)}>
        +
      </button>
    </div>
  );
}

// ---------------------------------------------------------------- edit bar and the layout surface

export interface EditBarProps {
  /** Still the built-in layout: the first change saves a copy. */
  builtin: boolean;
  canUndo: boolean;
  onUndo(): void;
  onDone(): void;
  layout: LayoutSheetProps;
}

/** Undo, the layout surface (name, rows, columns, Save as new layout, Delete) and Done. */
export function EditBar(p: EditBarProps) {
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    const away = (e: PointerEvent) => {
      if (wrap.current && !wrap.current.contains(e.target as Node)) setOpen(false);
    };
    const esc = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        button.current?.focus();
      }
    };
    document.addEventListener("pointerdown", away);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("pointerdown", away);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);
  return (
    <div class="le-bar" role="toolbar" aria-label="Layout editor">
      {p.builtin ? <Chip variant="constraint">Built in · edits save a copy</Chip> : null}
      <button type="button" class="btn" disabled={!p.canUndo} onClick={p.onUndo}>
        Undo
      </button>
      <div class="le-menu" ref={wrap}>
        <button ref={button} type="button" class="btn" aria-expanded={open} aria-haspopup="dialog" onClick={() => setOpen(!open)}>
          Layout
        </button>
        {open ? (
          <LayoutSheet
            {...p.layout}
            onClose={() => {
              setOpen(false);
              button.current?.focus();
            }}
          />
        ) : null}
      </div>
      <button type="button" class="btn btn-accent" onClick={p.onDone}>
        Done
      </button>
    </div>
  );
}

export interface LayoutSheetProps {
  name: string;
  body: LayoutBody;
  canDelete: boolean;
  onName(name: string): void;
  onBody(body: LayoutBody): void;
  onSaveAsNew(): void;
  onDelete(): void;
}

function LayoutSheet(p: LayoutSheetProps & { onClose(): void }) {
  const [name, setName] = useState(p.name);
  // Follow a name saved elsewhere (Save as new layout), never overwrite typing on mount.
  const seen = useRef(p.name);
  useEffect(() => {
    if (seen.current === p.name) return;
    seen.current = p.name;
    setName(p.name);
  }, [p.name]);
  // The input's own value: a commit can follow the last keystroke before the re-render.
  const commitName = (value: string) => {
    const n = value.trim();
    if (n && n !== p.name) p.onName(n);
    else if (!n) setName(p.name);
  };
  return (
    <div class="le-sheet" role="dialog" aria-label="Layout">
      <label>
        <span>Name</span>
        <input value={name} maxLength={80} onInput={(e) => setName((e.currentTarget as HTMLInputElement).value)} onChange={(e) => commitName((e.currentTarget as HTMLInputElement).value)} />
      </label>
      <div class="le-sheet-row">
        <Stepper label="Rows" value={p.body.rows} min={1} max={MAX_GRID} canLess={resizeGrid(p.body, p.body.rows - 1, p.body.columns) !== null} onChange={(v) => { const b = resizeGrid(p.body, v, p.body.columns); if (b) p.onBody(b); }} />
        <Stepper label="Columns" value={p.body.columns} min={1} max={MAX_GRID} canLess={resizeGrid(p.body, p.body.rows, p.body.columns - 1) !== null} onChange={(v) => { const b = resizeGrid(p.body, p.body.rows, v); if (b) p.onBody(b); }} />
      </div>
      <div class="le-sheet-row">
        <button
          type="button"
          class="btn"
          onClick={() => {
            p.onSaveAsNew();
            p.onClose();
          }}
        >
          Save as new layout
        </button>
        {p.canDelete ? (
          <button
            type="button"
            class="btn danger"
            onClick={() => {
              p.onDelete();
              p.onClose();
            }}
          >
            Delete layout
          </button>
        ) : null}
        <button type="button" class="btn" onClick={p.onClose}>
          Close
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- style strip

function resolveColour(value: string): string {
  const m = /^var\((--[\w-]+)\)$/.exec(value);
  if (!m || typeof getComputedStyle !== "function") return value;
  return getComputedStyle(document.documentElement).getPropertyValue(m[1]!).trim().toUpperCase() || value;
}

export interface StripProps {
  body: LayoutBody;
  tile: LiveTileCfg;
  ctx: RepresentationContext;
  /** False once frames have flowed and none carried ABS activation: Mark ABS is then unavailable. */
  absRecorded: boolean;
  placing: boolean;
  onCommit(body: LayoutBody, select?: string | null): void;
  onPlacing(on: boolean): void;
}

/** One strip for the selected tile: Show, Colour, Size, then the metric's own settings, Move and Remove. Every change previews and saves at once. */
export function StyleStrip({ body, tile, ctx, absRecorded, placing, onCommit, onPlacing }: StripProps) {
  const spec = TILE_BY_ID.get(tile.metric);
  // A colour identical to the metric's own (fuel's amber) is offered once, as Default.
  const colours = useMemo(() => {
    if (!spec) return [];
    const own = resolveColour(tileColor({ ...tile, accent: "default" }));
    return colourChoices(spec).filter((a) => a === "default" || resolveColour(tileColor({ ...tile, accent: a })) !== own);
  }, [tile.metric]);
  if (!spec) return null;
  const set = (patch: Partial<LiveTileCfg>) => onCommit(updateTile(body, tile.tile_id, patch), tile.tile_id);
  const reps = representations(spec, ctx);
  const current = representationOf(tile.style);
  // A representation the metric no longer offers (set elsewhere) stays visible as the current one.
  const shown = reps.includes(current) ? reps : [...reps, current];
  const size = (axis: "width" | "height", delta: 1 | -1) => stepSize(body, tile.tile_id, axis, delta);

  return (
    <section class="le-strip" aria-label={`${spec.label} style`} data-testid="le-strip">
      <h2 class="le-strip-title">{spec.label}</h2>
      <div class="le-group">
        <span class="le-label">Show</span>
        <Segmented
          label="Show"
          options={shown.map((r) => ({ value: r, label: REPRESENTATION_LABEL[r] }))}
          value={current}
          onChange={(r) => r !== current && set(representationPatch(tile, r, spec))}
        />
      </div>
      {colours.length > 0 ? (
        <div class="le-group">
          <span class="le-label">Colour</span>
          <div class="le-swatches" role="group" aria-label="Colour">
            {colours.map((a: Accent) => {
              const on = (tile.accent ?? "default") === a;
              return (
                <button
                  key={a}
                  type="button"
                  class={`le-swatch${on ? " on" : ""}`}
                  aria-pressed={on}
                  aria-label={ACCENT_LABEL[a]}
                  title={ACCENT_LABEL[a]}
                  style={{ "--swatch": tileColor({ ...tile, accent: a }) }}
                  onClick={() => !on && set({ accent: a })}
                >
                  <i aria-hidden="true" />
                </button>
              );
            })}
          </div>
        </div>
      ) : null}
      <div class="le-group">
        <span class="le-label">Size</span>
        <div class="le-size">
          <Stepper label="Width" value={tile.column_span} min={1} max={body.columns} canLess={size("width", -1) !== null} canMore={size("width", 1) !== null} onChange={(v) => { const b = size("width", v > tile.column_span ? 1 : -1); if (b) onCommit(b, tile.tile_id); }} />
          <Stepper label="Height" value={tile.row_span} min={1} max={body.rows} canLess={size("height", -1) !== null} canMore={size("height", 1) !== null} onChange={(v) => { const b = size("height", v > tile.row_span ? 1 : -1); if (b) onCommit(b, tile.tile_id); }} />
        </div>
      </div>
      {spec.units.length > 1 ? (
        <div class="le-group">
          <span class="le-label">Unit</span>
          <Segmented label="Unit" options={spec.units.map((u) => ({ value: u, label: unitLabel(u) }))} value={resolveUnit(spec, tile.unit)} onChange={(u) => set({ unit: u })} />
        </div>
      ) : null}
      {usesPrecision(spec) ? (
        <div class="le-group">
          <span class="le-label">Decimals</span>
          <Segmented label="Decimals" options={[0, 1, 2, 3].map((n) => ({ value: n, label: String(n) }))} value={tile.precision ?? spec.precision} onChange={(n) => set({ precision: n })} />
        </div>
      ) : null}
      {tile.style === "trend" ? (
        <div class="le-group">
          <span class="le-label">Window</span>
          <Segmented label="Window" options={TREND_WINDOWS.map(([id, label]) => ({ value: id, label }))} value={tile.trend_window ?? "s30"} onChange={(w) => set({ trend_window: w })} />
        </div>
      ) : null}
      {tile.metric === "brake" && tile.style === "trend" ? (
        <label class="check le-group">
          <input type="checkbox" checked={tile.highlight_abs ?? false} disabled={!absRecorded} onChange={(e) => set({ highlight_abs: (e.currentTarget as HTMLInputElement).checked })} />
          <span>{absRecorded ? "Mark ABS" : "Mark ABS · no ABS data"}</span>
        </label>
      ) : null}
      <div class="le-group le-actions">
        <button type="button" class="btn" aria-pressed={placing} onClick={() => onPlacing(!placing)}>
          {placing ? "Cancel move" : "Move"}
        </button>
        <button type="button" class="btn danger" onClick={() => onCommit(removeTile(body, tile.tile_id), null)}>
          Remove
        </button>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------- palette

export interface PaletteProps {
  body: LayoutBody;
  preview: Store<Preview | null>;
  gridRef: RefObject<HTMLDivElement>;
  onCommit(body: LayoutBody, select?: string | null): void;
}

interface PaletteDrag {
  metric: string;
  pointerId: number;
  x: number;
  y: number;
  armed: boolean;
  ghost: HTMLElement | null;
  /** Last pointer position, for the edge scroll. */
  lastX: number;
  lastY: number;
  scroll: number;
}

const SLOP_PX = 4;
/** Within this distance of the top or bottom edge a palette drag scrolls the page (the palette sits under the grid on a phone). */
const EDGE_PX = 56;

/** Metrics not on the board. Tap adds one at the first free spot; drag drops it on a cell, with the same placement preview as a move. */
export function Palette({ body, preview, gridRef, onCommit }: PaletteProps) {
  const free = useMemo(() => METRICS.filter((m) => !body.tiles.some((t) => t.metric === m.id)), [body]);
  const full = useMemo(() => free.length === 0 || addGrowing(body, free[0]!.id) === null, [body, free]);
  const drag = useRef<PaletteDrag | null>(null);
  const suppressClick = useRef(false);
  const newId = (next: LayoutBody) => next.tiles[next.tiles.length - 1]!.tile_id;

  const down = (e: PointerEvent, metric: string) => {
    if (full || e.button > 0) return;
    try {
      (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    } catch {
      /* synthetic */
    }
    drag.current = { metric, pointerId: e.pointerId, x: e.clientX, y: e.clientY, armed: false, ghost: null, lastX: e.clientX, lastY: e.clientY, scroll: 0 };
  };
  const aim = (d: PaletteDrag) => {
    const grid = gridRef.current;
    const cell = grid ? cellFromPoint(grid.getBoundingClientRect(), body.rows, body.columns, d.lastX, d.lastY, false) : null;
    const rect = cell ? addRect(body, d.metric, cell) : null;
    const prev = preview.get();
    if (!rect) {
      if (prev) preview.set(null);
      return;
    }
    const verdict = fits(body, rect) ? "valid" : "invalid";
    if (!prev || prev.rect.row !== rect.row || prev.rect.column !== rect.column || prev.verdict !== verdict) preview.set({ mode: "add", metric: d.metric, rect, verdict });
  };
  const edgeScroll = (d: PaletteDrag) => {
    const step = d.lastY < EDGE_PX ? -14 : d.lastY > innerHeight - EDGE_PX ? 14 : 0;
    if (step === 0 || d !== drag.current) {
      d.scroll = 0;
      return;
    }
    scrollBy(0, step);
    aim(d);
    d.scroll = requestAnimationFrame(() => edgeScroll(d));
  };
  const move = (e: PointerEvent) => {
    const d = drag.current;
    if (!d || e.pointerId !== d.pointerId) return;
    d.lastX = e.clientX;
    d.lastY = e.clientY;
    if (!d.armed) {
      if (Math.hypot(e.clientX - d.x, e.clientY - d.y) < SLOP_PX) return;
      d.armed = true;
      const ghost = document.createElement("div");
      ghost.className = "le-ghost";
      ghost.textContent = TILE_BY_ID.get(d.metric)?.label ?? d.metric;
      document.body.appendChild(ghost);
      d.ghost = ghost;
    }
    if (d.ghost) d.ghost.style.transform = `translate(${e.clientX + 12}px, ${e.clientY + 12}px)`;
    aim(d);
    if (!d.scroll) edgeScroll(d);
  };
  const up = (e: PointerEvent, cancelled: boolean) => {
    const d = drag.current;
    if (!d || e.pointerId !== d.pointerId) return;
    drag.current = null;
    d.ghost?.remove();
    if (d.scroll) cancelAnimationFrame(d.scroll);
    if (!d.armed) return; // a tap: the click adds it
    suppressClick.current = true;
    const p = preview.get();
    preview.set(null);
    if (cancelled || !p || p.verdict !== "valid" || p.metric !== d.metric) return;
    const next = addAt(body, d.metric, p.rect);
    if (next) onCommit(next, newId(next));
  };
  useEffect(() => () => drag.current?.ghost?.remove(), []);

  return (
    <section class="le-palette" aria-label="Add a tile">
      <h2 class="le-strip-title">Add{full && free.length > 0 ? " · grid full" : ""}</h2>
      <div class="le-chips">
        {free.map((m) => (
          <button
            key={m.id}
            type="button"
            class="le-chip"
            data-metric={m.id}
            disabled={full}
            onPointerDown={(e) => down(e, m.id)}
            onPointerMove={move}
            onPointerUp={(e) => up(e, false)}
            onPointerCancel={(e) => up(e, true)}
            onClick={() => {
              if (suppressClick.current) {
                suppressClick.current = false;
                return;
              }
              const next = addGrowing(body, m.id);
              if (next) onCommit(next, newId(next));
            }}
          >
            {m.label}
          </button>
        ))}
      </div>
    </section>
  );
}

import { useMemo } from "preact/hooks";
import { unitLabel } from "../gen/live-channels";
import { TILE_BY_ID, resolveUnit, usesPrecision } from "./tiles";
import {
  ACCENTS,
  type LayoutBody,
  type LayoutIssue,
  type LiveTileCfg,
  MAX_GRID,
  METRICS,
  TREND_WINDOWS,
  addTile,
  allowedStyles,
  fits,
  removeTile,
  resizeGrid,
  swapMetric,
  updateTile,
} from "./layout";

function Stepper({ label, value, min, max, onChange }: { label: string; value: number; min: number; max: number; onChange: (v: number) => void }) {
  return (
    <div class="stepper" role="group" aria-label={label}>
      <span class="stepper-label">{label}</span>
      <button type="button" aria-label={`${label} less`} disabled={value <= min} onClick={() => onChange(value - 1)}>
        -
      </button>
      <output>{value}</output>
      <button type="button" aria-label={`${label} more`} disabled={value >= max} onClick={() => onChange(value + 1)}>
        +
      </button>
    </div>
  );
}

export interface EditorProps {
  name: string;
  builtin: boolean;
  isNew: boolean;
  body: LayoutBody;
  selectedId: string | null;
  issues: LayoutIssue[];
  saving: boolean;
  /** False once frames have flowed and none carried ABS activation: Mark ABS is then unavailable. */
  absRecorded: boolean;
  dirty: boolean;
  onName(name: string): void;
  onBody(body: LayoutBody): void;
  onSelect(id: string | null): void;
  onSave(): void;
  onSaveCopy(): void;
  onDelete(): void;
  onCancel(): void;
}

/**
 * Dashboard editor for the rig and this page. Tile geometry can also be changed by dragging on the grid; the
 * steppers here give the same control with 44 px targets for touch screens.
 */
export function LayoutEditor(p: EditorProps) {
  const tile = p.body.tiles.find((t) => t.tile_id === p.selectedId) ?? null;
  const spec = tile ? TILE_BY_ID.get(tile.metric) : undefined;
  const free = useMemo(() => METRICS.filter((m) => !p.body.tiles.some((t) => t.metric === m.id)), [p.body]);
  const set = (patch: Partial<LiveTileCfg>) => tile && p.onBody(updateTile(p.body, tile.tile_id, patch));
  const geom = (patch: Partial<Pick<LiveTileCfg, "row" | "column" | "row_span" | "column_span">>) => {
    if (!tile) return;
    const rect = { row: tile.row, column: tile.column, row_span: tile.row_span, column_span: tile.column_span, ...patch };
    if (fits(p.body, rect, tile.tile_id)) set(patch);
  };
  const canAdd = addTile(p.body, free[0]?.id ?? METRICS[0]!.id) !== null;
  const blocking = p.issues.length > 0;

  return (
    <section class="editor panel" aria-label="Layout editor">
      <div class="editor-row">
        <label class="grow">
          <span>Name</span>
          <input value={p.name} maxLength={80} onInput={(e) => p.onName((e.currentTarget as HTMLInputElement).value)} />
        </label>
        <Stepper label="Rows" value={p.body.rows} min={1} max={MAX_GRID} onChange={(v) => { const b = resizeGrid(p.body, v, p.body.columns); if (b) p.onBody(b); }} />
        <Stepper label="Columns" value={p.body.columns} min={1} max={MAX_GRID} onChange={(v) => { const b = resizeGrid(p.body, p.body.rows, v); if (b) p.onBody(b); }} />
      </div>

      <div class="editor-row">
        <label class="grow">
          <span>Add tile</span>
          <select
            value=""
            onChange={(e) => {
              const id = (e.currentTarget as HTMLSelectElement).value;
              if (!id) return;
              const next = addTile(p.body, id);
              if (next) {
                p.onBody(next);
                p.onSelect(next.tiles[next.tiles.length - 1]!.tile_id);
              }
            }}
            disabled={!canAdd}
          >
            <option value="">{canAdd ? "Choose a metric" : "Grid is full"}</option>
            {METRICS.map((m) => (
              <option key={m.id} value={m.id}>
                {m.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      {tile && spec ? (
        <fieldset class="tile-settings" aria-label={`${spec.label} settings`}>
          <legend>{spec.label}</legend>
          <label>
            <span>Metric</span>
            <select
              value={tile.metric}
              onChange={(e) => {
                const next = swapMetric(p.body, tile.tile_id, (e.currentTarget as HTMLSelectElement).value);
                if (next) p.onBody(next);
              }}
            >
              {METRICS.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>Style</span>
            <select value={tile.style} onChange={(e) => set({ style: (e.currentTarget as HTMLSelectElement).value as LiveTileCfg["style"] })}>
              {allowedStyles(spec).map((s) => (
                <option key={s} value={s}>
                  {s.charAt(0).toUpperCase() + s.slice(1)}
                </option>
              ))}
            </select>
          </label>
          {spec.units.length > 1 ? (
            <label>
              <span>Unit</span>
              <select value={resolveUnit(spec, tile.unit)} onChange={(e) => set({ unit: (e.currentTarget as HTMLSelectElement).value })}>
                {spec.units.map((u) => (
                  <option key={u} value={u}>
                    {unitLabel(u)}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          {usesPrecision(spec) ? (
            <label>
              <span>Decimals</span>
              <select value={tile.precision ?? spec.precision} onChange={(e) => set({ precision: Number((e.currentTarget as HTMLSelectElement).value) })}>
                {[0, 1, 2, 3].map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          {tile.style === "trend" ? (
            <label>
              <span>Window</span>
              <select value={tile.trend_window ?? "s30"} onChange={(e) => set({ trend_window: (e.currentTarget as HTMLSelectElement).value as LiveTileCfg["trend_window"] })}>
                {TREND_WINDOWS.map(([id, label]) => (
                  <option key={id} value={id}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          <label>
            <span>Accent</span>
            <select value={tile.accent ?? "default"} onChange={(e) => set({ accent: (e.currentTarget as HTMLSelectElement).value as LiveTileCfg["accent"] })}>
              {ACCENTS.map((a) => (
                <option key={a} value={a}>
                  {a.charAt(0).toUpperCase() + a.slice(1)}
                </option>
              ))}
            </select>
          </label>
          {tile.metric === "brake" && tile.style === "trend" ? (
            <label class="check">
              <input type="checkbox" checked={tile.highlight_abs ?? false} disabled={!p.absRecorded} onChange={(e) => set({ highlight_abs: (e.currentTarget as HTMLInputElement).checked })} />
              <span title={p.absRecorded ? undefined : "No ABS data"}>{p.absRecorded ? "Mark ABS" : "Mark ABS (no ABS data)"}</span>
            </label>
          ) : null}
          <div class="editor-row geometry">
            <Stepper label="Row" value={tile.row + 1} min={1} max={p.body.rows} onChange={(v) => geom({ row: v - 1 })} />
            <Stepper label="Column" value={tile.column + 1} min={1} max={p.body.columns} onChange={(v) => geom({ column: v - 1 })} />
            <Stepper label="Height" value={tile.row_span} min={1} max={p.body.rows} onChange={(v) => geom({ row_span: v })} />
            <Stepper label="Width" value={tile.column_span} min={1} max={p.body.columns} onChange={(v) => geom({ column_span: v })} />
          </div>
          <button
            type="button"
            class="btn danger"
            onClick={() => {
              p.onBody(removeTile(p.body, tile.tile_id));
              p.onSelect(null);
            }}
          >
            Remove tile
          </button>
        </fieldset>
      ) : (
        <p class="sub">Select a tile.</p>
      )}

      {blocking ? (
        <ul class="cautions" role="alert">
          {p.issues.map((i, n) => (
            <li key={n}>{i.message}</li>
          ))}
        </ul>
      ) : null}

      <div class="editor-actions">
        {p.builtin ? (
          <button type="button" class="btn btn-accent" disabled={blocking || p.saving || !p.name.trim()} onClick={p.onSaveCopy}>
            Save as new layout
          </button>
        ) : (
          <>
            <button type="button" class="btn btn-accent" disabled={blocking || p.saving || !p.dirty || !p.name.trim()} onClick={p.isNew ? p.onSaveCopy : p.onSave}>
              Save
            </button>
            {!p.isNew ? (
              <button type="button" class="btn" disabled={blocking || p.saving || !p.name.trim()} onClick={p.onSaveCopy}>
                Save as copy
              </button>
            ) : null}
          </>
        )}
        {!p.builtin && !p.isNew ? (
          <button type="button" class="btn danger" onClick={p.onDelete}>
            Delete layout
          </button>
        ) : null}
        <button type="button" class="btn" onClick={p.onCancel}>
          Done
        </button>
      </div>
    </section>
  );
}

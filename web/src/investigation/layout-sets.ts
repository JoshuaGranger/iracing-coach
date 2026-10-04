import { criticalLayoutBody, newRowId, validateAndRepair } from "./layout-model";
import type { TraceLayoutBody } from "./types";

/**
 * Saved chart layouts (AnalysisTraceLayoutSets.cs): the built-in Default plus the driver's layouts, one active. New starts from
 * Default, Copy duplicates the active layout with fresh row ids, Delete removes the active layout (never Default) and falls back to
 * Default. Every operation returns a new set; persistence is the shell's (`trace-layouts`, `active_trace_layout_id`).
 */

export const DEFAULT_LAYOUT_ID = "default";

export interface NamedLayout {
  id: string;
  name: string;
  builtin: boolean;
  body: TraceLayoutBody;
}

export interface LayoutSet {
  layouts: NamedLayout[];
  activeId: string;
}

export function defaultLayout(body: TraceLayoutBody = criticalLayoutBody()): NamedLayout {
  return { id: DEFAULT_LAYOUT_ID, name: "Default", builtin: true, body };
}

export function initialLayoutSet(body?: TraceLayoutBody): LayoutSet {
  return { layouts: [defaultLayout(body)], activeId: DEFAULT_LAYOUT_ID };
}

export function activeLayout(set: LayoutSet): NamedLayout {
  return set.layouts.find((layout) => layout.id === set.activeId) ?? set.layouts.find((layout) => layout.builtin) ?? set.layouts[0] ?? defaultLayout();
}

/** "Custom", "Custom 2", ... the first name no layout uses (UniqueName, case-insensitive). */
export function uniqueName(set: LayoutSet, base: string): string {
  const used = new Set(set.layouts.map((layout) => layout.name.toLowerCase()));
  if (!used.has(base.toLowerCase())) return base;
  for (let index = 2; ; index++) if (!used.has(`${base} ${index}`.toLowerCase())) return `${base} ${index}`;
}

let counter = 0;
export function newLayoutId(): string {
  counter += 1;
  return `analysis-layout-${Date.now().toString(36)}${counter.toString(36)}`;
}

function withFreshRows(body: TraceLayoutBody): TraceLayoutBody {
  return { rows: validateAndRepair(body).body.rows.map((row) => ({ ...row, row_id: newRowId(), signals: row.signals.slice() })) };
}

export function selectLayout(set: LayoutSet, id: string): LayoutSet {
  return set.layouts.some((layout) => layout.id === id) ? { ...set, activeId: id } : set;
}

/** New: a copy of Default named "Custom n", made active. */
export function createLayout(set: LayoutSet, id: string = newLayoutId()): { set: LayoutSet; layout: NamedLayout } {
  const base = set.layouts.find((layout) => layout.builtin) ?? defaultLayout();
  const layout: NamedLayout = { id, name: uniqueName(set, "Custom"), builtin: false, body: withFreshRows(base.body) };
  return { set: { layouts: [...set.layouts, layout], activeId: id }, layout };
}

/** Copy: the active layout as "<name> Copy", fresh row ids, made active. */
export function duplicateLayout(set: LayoutSet, id: string = newLayoutId()): { set: LayoutSet; layout: NamedLayout } {
  const source = activeLayout(set);
  const layout: NamedLayout = { id, name: uniqueName(set, `${source.name} Copy`), builtin: false, body: withFreshRows(source.body) };
  return { set: { layouts: [...set.layouts, layout], activeId: id }, layout };
}

/** Delete: the active layout unless it is built in; Default becomes active. Null when nothing was deleted. */
export function deleteActiveLayout(set: LayoutSet): { set: LayoutSet; deletedId: string } | null {
  const active = activeLayout(set);
  if (active.builtin) return null;
  const fallback = set.layouts.find((layout) => layout.builtin)?.id ?? DEFAULT_LAYOUT_ID;
  return { set: { layouts: set.layouts.filter((layout) => layout.id !== active.id), activeId: fallback }, deletedId: active.id };
}

/** An edit to the active layout. Editing Default makes a "Default Copy" first (EnsureEditable), so Default never changes. */
export function editActiveLayout(set: LayoutSet, body: TraceLayoutBody, id: string = newLayoutId()): { set: LayoutSet; layout: NamedLayout; created: boolean } {
  const active = activeLayout(set);
  if (active.builtin) {
    const layout: NamedLayout = { id, name: uniqueName(set, "Default Copy"), builtin: false, body };
    return { set: { layouts: [...set.layouts, layout], activeId: id }, layout, created: true };
  }
  const layout = { ...active, body };
  return { set: { ...set, layouts: set.layouts.map((entry) => (entry.id === active.id ? layout : entry)) }, layout, created: false };
}

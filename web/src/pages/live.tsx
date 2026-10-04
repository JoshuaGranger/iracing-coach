import { useCallback, useEffect, useMemo, useRef, useState } from "preact/hooks";
import { type Schemas, api, paths } from "../core/api";
import { outbox } from "../core/outbox";
import { savePrefs } from "../core/prefs";
import { invalidate, useResource } from "../core/resource";
import { createStore, safeStorage, useStore } from "../core/store";
import { LiveBoard } from "../live/board";
import { liveHeadline, useLiveConnection, useLiveHealth } from "../live/connection";
import { type Preview, pushUndo } from "../live/edit-model";
import { EditBar, Palette, StyleStrip } from "../live/editor";
import type { GridEdit } from "../live/grid";
import { type LayoutBody, newLayoutId } from "../live/layout";
import "../live/live.css";
import { Chip, ErrorLine, Loading, Notice, PageHead } from "../ui/kit";

type LiveLayout = Schemas["LiveLayout"];
type LayoutList = Schemas["LiveLayoutList"];
const clone = (b: LayoutBody): LayoutBody => JSON.parse(JSON.stringify(b)) as LayoutBody;
const same = (a: LayoutBody, b: LayoutBody) => JSON.stringify(a) === JSON.stringify(b);

/** The layout being edited. Every completed gesture saves it; `copyOf` is set while it is still the built-in layout, whose first change saves a copy. */
interface Editing {
  id: string;
  name: string;
  body: LayoutBody;
  copyOf: string | null;
}

/** The layout this device shows, remembered per device (the rig keeps its own active layout). */
const viewKey = (customerId: number) => `coach.live.view.${customerId}`;
function readView(customerId: number): string | null {
  try {
    return safeStorage?.getItem(viewKey(customerId)) ?? null;
  } catch {
    return null;
  }
}
function writeView(customerId: number, id: string | null) {
  try {
    if (id) safeStorage?.setItem(viewKey(customerId), id);
    else safeStorage?.removeItem(viewKey(customerId));
  } catch {
    /* per-device convenience only */
  }
}

function useNarrow(): boolean {
  const q = typeof matchMedia === "function" ? matchMedia("(max-width: 700px)") : null;
  const [narrow, setNarrow] = useState(q?.matches ?? false);
  useEffect(() => {
    if (!q) return;
    const on = () => setNarrow(q.matches);
    q.addEventListener("change", on);
    return () => q.removeEventListener("change", on);
  }, [q]);
  return narrow;
}

export default function LivePage({ customerId }: { customerId: number }) {
  const conn = useLiveConnection(customerId);
  const status = useStore(conn.status);
  const health = useLiveHealth(conn);
  const narrow = useNarrow();

  const layoutsKey = paths.liveLayouts(customerId);
  const layouts = useResource(layoutsKey, () => api.liveLayouts(customerId));
  const list: LiveLayout[] = layouts.data?.layouts ?? [];
  const activeId = layouts.data?.active_layout_id ?? null;
  const [viewId, setViewIdState] = useState<string | null>(() => readView(customerId));
  const setViewId = (id: string | null) => {
    setViewIdState(id);
    writeView(customerId, id);
  };
  const view = list.find((l) => l.layout_id === (viewId ?? activeId)) ?? list.find((l) => l.layout_id === activeId) ?? list[0] ?? null;

  const [editing, setEditing] = useState<Editing | null>(null);
  const [undo, setUndo] = useState<LayoutBody[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [placing, setPlacing] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const preview = useMemo(() => createStore<Preview | null>(null), []);
  const gridRef = useRef<HTMLDivElement>(null);

  // Latest values for the stable gesture callbacks (the grid's edit layer is memoised on them).
  const editRef = useRef(editing);
  editRef.current = editing;
  const dataRef = useRef<LayoutList | undefined>(layouts.data);
  dataRef.current = layouts.data;

  useEffect(() => {
    // The outbox carries the version a write must match. A refetch never lowers it below what a save already returned,
    // and a layout this page inserted before the server answered (version 0) is not observed.
    for (const l of list) {
      const path = paths.liveLayout(customerId, l.layout_id);
      const known = Number(outbox.versionOf(path) ?? 0);
      if (l.version > 0 && l.version > known) outbox.observe(path, l.version);
    }
  }, [list, customerId]);

  /** Save the edited layout (creating the copy of a built-in on its first change) and show it in the list at once. */
  const persist = useCallback(
    (e: Editing): Editing => {
      const data = dataRef.current;
      const all = data?.layouts ?? [];
      let cur = e;
      if (cur.copyOf !== null) {
        cur = { ...cur, id: newLayoutId(cur.name, all.map((l) => l.layout_id)), copyOf: null };
        setViewId(cur.id);
      }
      const name = cur.name.trim() || "Layout";
      const known = all.some((l) => l.layout_id === cur.id);
      if (data) {
        const layoutsNext = known
          ? all.map((l) => (l.layout_id === cur.id ? { ...l, name, body: cur.body } : l))
          : [...all, { layout_id: cur.id, name, body: cur.body, version: 0, builtin: false }];
        const next = { ...data, layouts: layoutsNext };
        dataRef.current = next;
        layouts.mutate(next);
      }
      outbox
        .enqueue({ method: "PUT", path: paths.liveLayout(customerId, cur.id), body: { name, body: cur.body }, label: `Layout ${name}`, coalesceKey: `live-layout:${customerId}:${cur.id}` })
        .then(
          () => invalidate(layoutsKey),
          () => setMessage("Layout not saved."),
        );
      return cur;
    },
    [customerId, layoutsKey],
  );

  const apply = useCallback((next: Editing | null) => {
    editRef.current = next;
    setEditing(next);
  }, []);

  const commit = useCallback(
    (body: LayoutBody, select?: string | null) => {
      const e = editRef.current;
      if (!e) return;
      setPlacing(null);
      if (select !== undefined) setSelected(select);
      if (same(body, e.body)) return;
      setUndo((u) => pushUndo(u, e.body));
      apply(persist({ ...e, body }));
    },
    [apply, persist],
  );

  const select = useCallback((id: string | null) => {
    setSelected(id);
    setPlacing((p) => (p && p !== id ? null : p));
  }, []);

  const gridEdit: GridEdit | null = useMemo(
    () => (editing ? { selectedId: selected, placingId: placing, preview, gridRef, onSelect: select, onCommit: commit } : null),
    [editing !== null, selected, placing, preview, select, commit],
  );

  useEffect(() => {
    if (!editing) return;
    const esc = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || (e.target as HTMLElement | null)?.closest?.(".le-sheet")) return;
      if (placing) setPlacing(null);
      else setSelected(null);
    };
    document.addEventListener("keydown", esc);
    return () => document.removeEventListener("keydown", esc);
  }, [editing !== null, placing]);

  function startEdit() {
    if (!view) return;
    apply({ id: view.layout_id, name: view.builtin ? `${view.name} copy` : view.name, body: clone(view.body), copyOf: view.builtin ? view.layout_id : null });
    setUndo([]);
    setSelected(null);
    setPlacing(null);
    setMessage(null);
  }

  function done() {
    apply(null);
    setUndo([]);
    setSelected(null);
    setPlacing(null);
    preview.set(null);
  }

  function undoOnce() {
    const e = editRef.current;
    const prev = undo[undo.length - 1];
    if (!e || !prev) return;
    setUndo(undo.slice(0, -1));
    apply(persist({ ...e, body: prev }));
    if (selected && !prev.tiles.some((t) => t.tile_id === selected)) setSelected(null);
    setPlacing(null);
  }

  async function deleteLayout() {
    const e = editRef.current;
    if (!e || e.copyOf !== null) return;
    const path = paths.liveLayout(customerId, e.id);
    const data = dataRef.current;
    if (data) layouts.mutate({ ...data, layouts: data.layouts.filter((l) => l.layout_id !== e.id) });
    done();
    setViewId(null);
    try {
      await outbox.enqueue({ method: "DELETE", path, ifMatch: outbox.versionOf(path), label: `Delete layout ${e.name}` });
    } catch {
      setMessage("Delete not applied.");
    }
    invalidate(layoutsKey);
  }

  /** The rig's active layout: shown at once, saved through the driver's prefs, then read back. */
  async function showOnRig(id: string) {
    const data = dataRef.current;
    if (data) layouts.mutate({ ...data, active_layout_id: id });
    await savePrefs(customerId, { active_live_layout_id: id });
    invalidate(layoutsKey);
  }

  const headline = liveHeadline(status.link, status.hello, health);
  const body = editing ? editing.body : (view?.body ?? null);
  const selectedTile = editing && selected ? (editing.body.tiles.find((t) => t.tile_id === selected) ?? null) : null;
  const shownId = editing ? (editing.copyOf ?? editing.id) : (view?.layout_id ?? "");

  return (
    <>
      <PageHead title="Live">
        <span class="live-status" data-testid="live-status" data-live={headline.live}>
          <span class={`dot${headline.live ? " on" : ""}`} aria-hidden="true" />
          {headline.text}
        </span>
      </PageHead>

      <div class="live-controls">
        <label class="select-field">
          <span>Layout</span>
          <select value={shownId} disabled={editing !== null} onChange={(e) => setViewId((e.currentTarget as HTMLSelectElement).value)}>
            {list.map((l) => (
              <option key={l.layout_id} value={l.layout_id}>
                {l.name}
                {l.layout_id === activeId ? " (on rig)" : ""}
              </option>
            ))}
          </select>
        </label>
        {editing ? (
          <EditBar
            builtin={editing.copyOf !== null}
            canUndo={undo.length > 0}
            onUndo={undoOnce}
            onDone={done}
            layout={{
              name: editing.name,
              body: editing.body,
              canDelete: editing.copyOf === null,
              onName: (name) => {
                const e = editRef.current;
                if (e && name !== e.name) apply(persist({ ...e, name }));
              },
              onBody: (b) => commit(b),
              onSaveAsNew: () => {
                const e = editRef.current;
                if (e) apply(persist({ ...e, name: `${e.name} copy`, copyOf: e.id }));
              },
              onDelete: () => void deleteLayout(),
            }}
          />
        ) : (
          <div class="live-buttons">
            {view?.builtin ? (
              <Chip variant="constraint" title="Edit saves a copy">
                Built in
              </Chip>
            ) : null}
            {view && view.layout_id !== activeId ? (
              <button type="button" class="btn" onClick={() => void showOnRig(view.layout_id)}>
                Show on rig
              </button>
            ) : null}
            <button type="button" class="btn" disabled={!view} onClick={startEdit}>
              Edit layout
            </button>
          </div>
        )}
      </div>

      {layouts.error && !layouts.data ? <ErrorLine error={layouts.error} /> : null}
      {status.lastError ? <Notice tone="warn">{status.lastError}</Notice> : null}
      {message ? <Notice tone="warn">{message}</Notice> : null}
      {!body && !layouts.error ? <Loading what="layout" /> : null}

      <div class={editing ? "live-work le-on" : "live-work"}>
        {body ? <LiveBoard conn={conn} body={body} edit={gridEdit} flow={narrow && !editing} /> : null}
        {editing ? (
          <aside class="le-side" aria-label="Tile tools">
            {selectedTile ? (
              <StyleStrip
                body={editing.body}
                tile={selectedTile}
                ctx={{ fuelCapacityKnown: health.fuelCapacityKnown }}
                absRecorded={!health.hadFrames || health.absRecorded}
                placing={placing === selectedTile.tile_id}
                onCommit={commit}
                onPlacing={(on) => {
                  setPlacing(on ? selectedTile.tile_id : null);
                  // On a phone the strip sits under the grid: bring the targets into view.
                  if (on) requestAnimationFrame(() => gridRef.current?.scrollIntoView({ block: "nearest" }));
                }}
              />
            ) : null}
            <Palette body={editing.body} preview={preview} gridRef={gridRef} onCommit={commit} />
          </aside>
        ) : null}
      </div>
    </>
  );
}

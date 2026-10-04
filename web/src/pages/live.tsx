import { useEffect, useMemo, useState } from "preact/hooks";
import { type Schemas, api, paths } from "../core/api";
import { outbox } from "../core/outbox";
import { savePrefs } from "../core/prefs";
import { invalidate, useResource } from "../core/resource";
import { useStore } from "../core/store";
import { LiveBoard } from "../live/board";
import { LayoutEditor } from "../live/editor";
import { liveHeadline, useLiveConnection, useLiveHealth } from "../live/connection";
import { type LayoutBody, newLayoutId, validateLayout } from "../live/layout";
import { Chip, ErrorLine, Loading, Notice, PageHead } from "../ui/kit";

type LiveLayout = Schemas["LiveLayout"];
const clone = (b: LayoutBody): LayoutBody => JSON.parse(JSON.stringify(b)) as LayoutBody;

interface Draft {
  name: string;
  body: LayoutBody;
  /** True when saving creates a new layout (built-in, or a copy). */
  isNew: boolean;
  baseId: string;
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
  const [viewId, setViewId] = useState<string | null>(null);
  const view = list.find((l) => l.layout_id === (viewId ?? activeId)) ?? list[0] ?? null;
  const [draft, setDraft] = useState<Draft | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    for (const l of list) outbox.observe(paths.liveLayout(customerId, l.layout_id), l.version);
  }, [list, customerId]);

  const body = draft ? draft.body : view?.body ?? null;
  const issues = useMemo(() => (draft ? validateLayout(draft.body) : []), [draft]);
  const dirty = draft && view ? JSON.stringify(draft.body) !== JSON.stringify(view.body) || draft.name !== view.name : true;

  function startEdit() {
    if (!view) return;
    setDraft({ name: view.builtin ? `${view.name} copy` : view.name, body: clone(view.body), isNew: view.builtin, baseId: view.layout_id });
    setSelected(null);
    setMessage(null);
  }

  async function write(id: string, name: string, b: LayoutBody, existing: LiveLayout | null) {
    setSaving(true);
    setMessage(null);
    try {
      await outbox.enqueue({
        method: "PUT",
        path: paths.liveLayout(customerId, id),
        body: { name: name.trim(), body: b },
        ifMatch: existing ? String(existing.version) : undefined,
        label: `Layout ${name.trim()}`,
      });
      invalidate(layoutsKey);
      setViewId(id);
      setDraft(null);
      setSelected(null);
    } catch {
      setMessage("Layout queued.");
      setDraft(null);
    } finally {
      setSaving(false);
    }
  }

  const headline = liveHeadline(status.link, status.hello, health);

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
          <select
            value={view?.layout_id ?? ""}
            disabled={draft !== null}
            onChange={(e) => {
              setViewId((e.currentTarget as HTMLSelectElement).value);
            }}
          >
            {list.map((l) => (
              <option key={l.layout_id} value={l.layout_id}>
                {l.name}
                {l.layout_id === activeId ? " (on rig)" : ""}
              </option>
            ))}
          </select>
        </label>
        <div class="live-buttons">
          {view?.builtin && !draft ? (
            <Chip title="Edit saves a copy">Built in</Chip>
          ) : null}
          {view && view.layout_id !== activeId && !draft ? (
            <button type="button" class="btn" onClick={() => void savePrefs(customerId, { active_live_layout_id: view.layout_id })}>
              Show on rig
            </button>
          ) : null}
          {!draft ? (
            <button type="button" class="btn" disabled={!view} onClick={startEdit}>
              Edit layout
            </button>
          ) : null}
        </div>
      </div>

      {layouts.error && !layouts.data ? <ErrorLine error={layouts.error} /> : null}
      {status.lastError ? <Notice tone="warn">{status.lastError}</Notice> : null}
      {message ? <Notice tone="warn">{message}</Notice> : null}
      {!body && !layouts.error ? <Loading what="layout" /> : null}

      <div class={draft ? "live-work editing" : "live-work"}>
        {body ? <LiveBoard conn={conn} body={body} editing={draft !== null} selectedId={selected} onSelect={setSelected} onChange={(b) => draft && setDraft({ ...draft, body: b })} flow={narrow && !draft} /> : null}
        {draft ? (
          <LayoutEditor
            name={draft.name}
            builtin={!!view?.builtin}
            isNew={draft.isNew}
            body={draft.body}
            selectedId={selected}
            issues={issues}
            saving={saving}
            absRecorded={!health.hadFrames || health.absRecorded}
            dirty={!!dirty}
            onName={(name) => setDraft({ ...draft, name })}
            onBody={(b) => setDraft({ ...draft, body: b })}
            onSelect={setSelected}
            onSave={() => view && write(view.layout_id, draft.name, draft.body, view)}
            onSaveCopy={() => write(newLayoutId(draft.name, list.map((l) => l.layout_id)), draft.name, draft.body, null)}
            onDelete={async () => {
              if (!view) return;
              try {
                await outbox.enqueue({ method: "DELETE", path: paths.liveLayout(customerId, view.layout_id), ifMatch: String(view.version), label: `Delete layout ${view.name}` });
                invalidate(layoutsKey);
                setViewId(null);
              } catch {
                setMessage("Delete queued.");
              }
              setDraft(null);
            }}
            onCancel={() => {
              setDraft(null);
              setSelected(null);
            }}
          />
        ) : null}
      </div>
    </>
  );
}

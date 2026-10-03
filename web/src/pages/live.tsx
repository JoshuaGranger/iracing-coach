import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { type Schemas, api, paths } from "../core/api";
import { outbox } from "../core/outbox";
import { savePrefs } from "../core/prefs";
import { invalidate, useResource } from "../core/resource";
import { readJson, useStore, writeJson } from "../core/store";
import { LayoutEditor } from "../live/editor";
import { type LiveRate, LIVE_RATES, defaultRate, useFrameSample, useLiveConnection } from "../live/connection";
import { LiveGrid } from "../live/grid";
import { type LayoutBody, newLayoutId, validateLayout } from "../live/layout";
import { Chip, ErrorLine, Loading, Notice, PageHead } from "../ui/kit";

type LiveLayout = Schemas["LiveLayout"];
const RATE_KEY = "coach.live.rate";
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
  const [rate, setRate] = useState<LiveRate>(() => {
    const stored = readJson<number | null>(RATE_KEY, null);
    return (LIVE_RATES as readonly number[]).includes(stored ?? -1) ? (stored as LiveRate) : defaultRate(globalThis.innerWidth ?? 1280);
  });
  useEffect(() => writeJson(RATE_KEY, rate), [rate]);
  const conn = useLiveConnection(customerId, rate);
  const { frame, tick } = useFrameSample(conn, 15);
  const status = useStore(conn.status);
  const observed = useRef(new Map<string, { min: number; max: number }>());
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
      setMessage("The layout is waiting to save; see pending changes above.");
      setDraft(null);
    } finally {
      setSaving(false);
    }
  }

  const link = status.link;
  const waitingSim = link === "open" && status.hello && !status.hello.live;
  const liveNow = link === "open" && status.hello?.live === true && frame !== null && (frame.flags & 1) !== 0;
  const headline = liveNow ? "Live" : waitingSim ? "Waiting for iRacing" : link === "connecting" ? "Connecting" : link === "retrying" ? "Reconnecting" : link === "open" ? "Waiting for data" : "Closed";

  return (
    <>
      <PageHead title="Live">
        <span class="live-status" data-testid="live-status" data-live={liveNow}>
          <span class={`dot${liveNow ? " on" : ""}`} aria-hidden="true" />
          {headline}
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
              observed.current.clear();
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
        <label class="select-field">
          <span>Update rate</span>
          <select value={rate} onChange={(e) => setRate(Number((e.currentTarget as HTMLSelectElement).value) as LiveRate)}>
            {LIVE_RATES.map((r) => (
              <option key={r} value={r}>
                {r} Hz
              </option>
            ))}
          </select>
        </label>
        <div class="live-buttons">
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
      {link === "retrying" ? (
        <p class="sub" role="status">
          Showing the last values. Reconnecting.
        </p>
      ) : null}

      <div class={draft ? "live-work editing" : "live-work"}>
        {body ? (
          <LiveGrid
            body={body}
            frame={frame}
            history={conn.history}
            tick={tick}
            observed={observed.current}
            editing={draft !== null}
            selectedId={selected}
            onSelect={setSelected}
            onChange={(b) => draft && setDraft({ ...draft, body: b })}
            flow={narrow && !draft}
          />
        ) : null}
        {draft ? (
          <LayoutEditor
            name={draft.name}
            builtin={!!view?.builtin}
            isNew={draft.isNew}
            body={draft.body}
            selectedId={selected}
            issues={issues}
            saving={saving}
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
                setMessage("Deleting is waiting; see pending changes above.");
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
      {view?.builtin && !draft ? (
        <p class="sub">
          <Chip>Built in</Chip> Built-in layouts cannot be changed; edit makes a copy.
        </p>
      ) : null}
    </>
  );
}

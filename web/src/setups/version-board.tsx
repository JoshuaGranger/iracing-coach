import { useMemo, useState } from "preact/hooks";
import { type Schemas, api, paths } from "../core/api";
import { formatDate } from "../core/format";
import { useResource } from "../core/resource";
import { href } from "../core/router";
import { Chevron } from "./glyphs";
import type { Setup } from "./model";
import { type Change, type VersionDiff, type VersionRef, useVersionChanges, versionsOf } from "./versions";

/**
 * The changed-parameter count of a version, as the row says it. The oldest version has nothing to compare with. The backend's count
 * (`SetupVersion.changed_count`, from its own comparison) is the label until the pair's changes are loaded; a null count is `—`.
 */
export function changedLabel(diff: VersionDiff | undefined, server?: number | null): string {
  if (!diff) return typeof server === "number" ? `${server} changed` : server === null ? "—" : "…";
  switch (diff.state) {
    case "done":
      return `${diff.changes.length} changed`;
    case "loading":
      return "…";
    default:
      return "—";
  }
}

function Changes({ changes }: { changes: readonly Change[] }) {
  if (changes.length === 0) return <p class="v-none">No settings changed</p>;
  return (
    <table class="v-changes" aria-label="Changed settings">
      <thead>
        <tr>
          <th scope="col">Setting</th>
          <th scope="col">Old</th>
          <th scope="col">New</th>
          <th scope="col">Unit</th>
        </tr>
      </thead>
      <tbody>
        {changes.map((c) => (
          <tr key={c.key} data-key={c.key}>
            <th scope="row">{c.label}</th>
            <td data-label="Old">{c.old}</td>
            <td data-label="New">{c.new}</td>
            <td data-label="Unit">{c.unit}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** The sessions recorded on this version: from its sightings, the recordings that carry a session. */
function RecordedRuns({ setupId, customerId }: { setupId: number; customerId: number }) {
  const res = useResource(paths.setup(setupId), (signal) => api.setup(setupId, { signal }));
  const runs = useMemo(() => {
    const seen = new Map<number, string>();
    for (const o of res.data?.observations ?? []) if (o.session_id != null && !seen.has(o.session_id)) seen.set(o.session_id, o.observed_at);
    return [...seen].map(([sessionId, at]) => ({ sessionId, at }));
  }, [res.data]);
  return (
    <section class="v-runs" aria-label="Recorded runs">
      <h3>Recorded runs</h3>
      {!res.data ? (
        <p class="v-none">{res.error ? "—" : "…"}</p>
      ) : runs.length === 0 ? (
        <p class="v-none">None</p>
      ) : (
        <ul>
          {runs.map((r) => (
            <li key={r.sessionId}>
              <a href={href({ page: "investigation", customerId, sessionId: r.sessionId, view: "race" })}>Session {r.sessionId}</a>
              <span class="date">{formatDate(r.at)}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function VersionItem({ v, here, diff, customerId, serverCount }: { v: VersionRef; here: boolean; diff: VersionDiff | undefined; customerId: number; serverCount?: number | null }) {
  const [open, setOpen] = useState(false);
  const id = `ver-${v.setup.setup_id}`;
  const first = v.number === 1;
  const state = first ? ({ state: "first" } as const) : diff;
  return (
    <li class={`v-item${here ? " here" : ""}`} data-version={v.number} aria-current={here ? "true" : undefined}>
      <button type="button" class="v-head" aria-expanded={open} aria-controls={id} onClick={() => setOpen(!open)}>
        <Chevron down={open} />
        <span class="s-title">Version {v.number}</span>
        <span class="date">{formatDate(v.setup.first_seen_at)}</span>
        <span class="num v-count" title={state && state.state === "blocked" ? state.reason : state && state.state === "error" ? state.message : undefined}>
          {changedLabel(state, serverCount)}
        </span>
      </button>
      {open ? (
        <div class="v-body" id={id}>
          {state && state.state === "done" ? <Changes changes={state.changes} /> : null}
          {state && (state.state === "blocked" || state.state === "error") ? <p class="v-none">{state.state === "blocked" ? state.reason : state.message}</p> : null}
          <RecordedRuns setupId={v.setup.setup_id} customerId={customerId} />
          {here ? null : (
            <a class="v-open" href={href({ page: "setup", customerId, setupId: v.setup.setup_id })}>
              Open version {v.number}
            </a>
          )}
        </div>
      ) : null}
    </li>
  );
}

/**
 * Versions of one setup name: a collapsed section. Open, it lists the versions by date with the number of parameters each changed
 * against the one before it; a version opens to its changed settings (old value, new value, unit) and the runs recorded on it.
 * The backend's comparison supplies the changes. No pace or tire effect is claimed.
 */
export function VersionBoard({ current, twins, customerId, serverVersions }: { current: Setup; twins: Setup[]; customerId: number; serverVersions?: readonly Schemas["SetupVersion"][] }) {
  const [open, setOpen] = useState(false);
  const counts = useMemo(() => new Map((serverVersions ?? []).map((x) => [x.setup_id, x.changed_count] as const)), [serverVersions]);
  const versions = useMemo(() => versionsOf(current, twins), [current, twins]);
  const diffs = useVersionChanges(versions, open);
  return (
    <section class="d-card versions-board" aria-label="Versions" data-testid="versions">
      <h2>
        <button type="button" class="v-toggle" aria-expanded={open} aria-controls="versions-list" onClick={() => setOpen(!open)}>
          <Chevron down={open} />
          Versions <span class="count">{versions.length}</span>
        </button>
      </h2>
      <ul class="v-list" id="versions-list" hidden={!open}>
        {versions.map((v) => (
          <VersionItem key={v.setup.setup_id} v={v} here={v.setup.setup_id === current.setup_id} diff={diffs.get(v.setup.setup_id)} customerId={customerId} serverCount={counts.get(v.setup.setup_id)} />
        ))}
      </ul>
    </section>
  );
}

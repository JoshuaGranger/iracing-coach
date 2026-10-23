import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { api, apiUrl, paths } from "../core/api";
import { formatDateTime, plural } from "../core/format";
import { cachedValue, invalidateKey, isFresh, setCached, useResource } from "../core/resource";
import { href, navigate, setLeaveGuard } from "../core/router";
import { uuid4 } from "../core/uuid";
import { ErrorLine, Loading, Notice } from "../ui/kit";
import {
  MESSAGE_MAX,
  NAME_MAX,
  NOTES_MAX,
  type PendingWrite,
  type StoDetail,
  type StoField,
  type StoFile,
  type StoWrite,
  type WriteFailure,
  type WriteKind,
  baseName,
  changesOf,
  clearPending,
  conflictingEdits,
  describeFailure,
  fieldDomId,
  folderOf,
  groupDomId,
  groupFields,
  historyNewestFirst,
  isAmbiguous,
  nativeHint,
  readPending,
  rejectedField,
  revisionLabel,
  sameRequest,
  suggestName,
  viewOf,
  withNewerMetadata,
  writePending,
} from "../setups/sto";
import "../setups/setups.css";

/**
 * One `.sto` file: its native parameters, notes and revisions on one screen. Imported originals are read-only; Save as makes an
 * editable Coach setup; Save appends a revision; an old revision is read-only and can be restored as a new one.
 */
export default function SetupFilePage({ customerId, fileId, version }: { customerId: number; fileId: string; version: number | null }) {
  // A new driver, file or revision is a new editor: nothing typed for one ever lands on another.
  return <FileView key={`${customerId}|${fileId}|${version ?? "current"}`} customerId={customerId} fileId={fileId} version={version} />;
}

/**
 * Old revisions already read keep their own document, SHA and download but take the file's current revision and history from a
 * newer answer. The cache only reaches mounted readers through invalidation, so without this an old revision opened next would
 * still call itself current and offer editing instead of Restore.
 */
function refreshOlder(customerId: number, d: StoDetail): void {
  for (const h of d.history) {
    if (h.version === d.version) continue;
    const key = paths.stoFile(customerId, d.file.id, h.version);
    const old = cachedValue<StoDetail>(key);
    const next = old ? withNewerMetadata(old, d) : null;
    if (next) setCached(key, next);
  }
}

/** A detail the server has just answered as current (a save, or the current of a 412): the current view, its revision, and the old ones. */
function rememberCurrent(customerId: number, d: StoDetail): void {
  const latest = paths.stoFile(customerId, d.file.id);
  const cached = cachedValue<StoDetail>(latest);
  if (!cached || cached.file.current_version <= d.file.current_version) setCached(latest, d);
  setCached(paths.stoFile(customerId, d.file.id, d.version), d);
  refreshOlder(customerId, d);
}

/**
 * Sends a write and settles its persisted record: any answer from the server clears it, an unanswered request keeps it for the
 * identical retry. Runs to the end even when the page has gone, so a late answer still clears the record and fills the cache.
 */
async function sendWrite(p: PendingWrite): Promise<StoDetail> {
  writePending(p);
  try {
    const r = p.kind === "save" ? await api.saveSto(p.customerId, p.fileId, p.body, p.ifMatch ?? "", p.key) : await api.saveStoAs(p.customerId, p.fileId, p.body, p.key);
    clearPending(p.customerId, p.fileId);
    const d = r.body;
    rememberCurrent(p.customerId, d);
    invalidateKey(paths.stoFiles(p.customerId));
    return d;
  } catch (e) {
    if (!isAmbiguous(e)) clearPending(p.customerId, p.fileId);
    throw e;
  }
}

function FileView({ customerId, fileId, version }: { customerId: number; fileId: string; version: number | null }) {
  const key = paths.stoFile(customerId, fileId, version);
  const res = useResource(key, (signal) => api.stoFile(customerId, fileId, version, { signal }));
  const lib = useResource(paths.stoFiles(customerId), (signal) => api.stoFiles(customerId, { signal }));
  // An explicit revision is read again from the server on every open, and edited only once that answer is in: a cached copy may
  // still call it current after a later save.
  const [confirmed, setConfirmed] = useState(version === null);
  const asked = useRef(false);
  useEffect(() => {
    if (version !== null && isFresh(key)) res.reload();
  }, []);
  useEffect(() => {
    if (version === null) return;
    if (res.loading) asked.current = true;
    else if (asked.current && !res.error) setConfirmed(true);
  }, [version, res.loading, res.error]);
  const restored = useMemo(() => readPending(customerId, fileId), [customerId, fileId]);
  const [base, setBase] = useState<StoDetail | null>(null);
  const [edits, setEdits] = useState<Record<string, string>>(() => (restored ? { ...restored.body.changes } : {}));
  const [notes, setNotes] = useState<string | null>(() => restored?.body.notes ?? null);
  const [message, setMessage] = useState(() => (restored?.kind === "save" ? restored.body.message ?? "" : ""));
  const [saveAs, setSaveAs] = useState<{ name: string; message: string } | null>(() => (restored?.kind === "save-as" ? { name: restored.body.name ?? "", message: restored.body.message ?? "" } : null));
  const [pending, setPending] = useState<PendingWrite | null>(restored);
  const [busy, setBusy] = useState<WriteKind | null>(null);
  const [failure, setFailure] = useState<WriteFailure | null>(() =>
    restored ? { kind: "unconfirmed", message: "An earlier save was not confirmed by the server. Retry sends the same request; it never saves twice." } : null,
  );
  const [conflicts, setConflicts] = useState<string[]>([]);
  const live = useRef(true);
  useEffect(() => () => void (live.current = false), []);

  const data = res.data;
  const document_ = base?.document ?? null;
  const settings = document_?.settings ?? [];
  const changes = useMemo(() => changesOf(settings, edits), [settings, edits]);
  const notesChanged = notes !== null && document_ !== null && notes !== document_.notes;
  const changeCount = Object.keys(changes).length + (notesChanged ? 1 : 0);
  const dirty = changeCount > 0;
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;
  const newer = !!data && !!base && data.file.current_version > base.file.current_version;

  // The loaded revision becomes the editor's base; a refresh that brings a newer revision waits while there are unsaved edits.
  useEffect(() => {
    if (!data) return;
    refreshOlder(customerId, data);
    setBase((b) => (!b || !dirtyRef.current || (b.version === data.version && b.file.current_version === data.file.current_version) ? data : b));
  }, [data]);

  const title = base ? baseName(base.file.name) : "setup file";
  useEffect(
    () =>
      setLeaveGuard((next) => {
        if (!dirtyRef.current) return null;
        const same = next.page === "setupFile" && next.customerId === customerId && next.fileId === fileId && (next.query.version ?? null) === (version === null ? null : String(version));
        return same ? null : `Discard unsaved changes to ${title}?`;
      }),
    [customerId, fileId, version, title],
  );
  useEffect(() => {
    if (!dirty) return undefined;
    const stop = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", stop);
    return () => window.removeEventListener("beforeunload", stop);
  }, [dirty]);

  if (!base) return res.error ? <ErrorLine error={res.error} /> : <Loading what="setup file" />;
  const d = base;
  const view = viewOf(d);
  const file = d.file;
  const files = lib.data?.files ?? [];
  const parent = file.parent_file_id ? files.find((f) => f.id === file.parent_file_id) ?? null : null;
  const next = file.current_version + 1;
  const locked = pending !== null || busy !== null;

  function discard() {
    setEdits({});
    setNotes(null);
    setMessage("");
    setConflicts([]);
    if (failure?.kind !== "unconfirmed") setFailure(null);
  }

  /** Builds (or, for the identical request, reuses) the persisted write and sends it. */
  async function run(kind: WriteKind, body: StoWrite, ifMatch: string | null) {
    const p: PendingWrite = sameRequest(pending, kind, customerId, fileId, ifMatch, body)
      ? pending
      : { v: 1, kind, customerId, fileId, key: uuid4(), ifMatch, body, at: new Date().toISOString() };
    await send(p);
  }

  async function send(p: PendingWrite) {
    setBusy(p.kind);
    setFailure(null);
    setPending(p);
    try {
      const saved = await sendWrite(p);
      if (!live.current) return;
      dirtyRef.current = false;
      setPending(null);
      setBusy(null);
      setConflicts([]);
      if (p.kind === "save-as" || saved.file.id !== fileId || version !== null) {
        navigate({ page: "setupFile", customerId, fileId: saved.file.id });
        return;
      }
      setBase(saved);
      setEdits({});
      setNotes(null);
      setMessage("");
    } catch (e) {
      if (!live.current) return;
      setBusy(null);
      const f = describeFailure(e, p.kind);
      setPending(f.kind === "unconfirmed" ? p : null);
      setFailure(f);
      if (f.kind === "conflict" && f.current) {
        const cur = f.current;
        rememberCurrent(customerId, cur);
        if (view === "editable") {
          setConflicts(conflictingEdits(d.document?.settings ?? [], cur.document?.settings ?? [], changes));
          setBase(cur);
        } else setBase({ ...d, file: cur.file, history: cur.history });
      }
    }
  }

  function save() {
    const body: StoWrite = { base_version: d.version, changes, ...(notesChanged ? { notes: notes! } : {}), ...(message.trim() ? { message: message.trim() } : {}) };
    void run("save", body, String(file.current_version));
  }

  function restore() {
    void run("save", { base_version: d.version, changes: {}, message: `Restored ${revisionLabel(d.version).toLowerCase()}` }, String(file.current_version));
  }

  function openSaveAs() {
    setFailure(null);
    setSaveAs({ name: suggestName(file.name, file.car_folder, files), message: "" });
  }

  function submitSaveAs(e: Event) {
    e.preventDefault();
    if (!saveAs) return;
    const body: StoWrite = {
      base_version: d.version,
      changes: view === "editable" ? changes : {},
      ...(view === "editable" && notesChanged ? { notes: notes! } : {}),
      name: saveAs.name,
      ...(saveAs.message.trim() ? { message: saveAs.message.trim() } : {}),
    };
    void run("save-as", body, null);
  }

  function dropPending() {
    clearPending(customerId, fileId);
    setPending(null);
    setFailure(null);
  }

  /** A long native file: the group's first row at the top, focus on the group for the keyboard. */
  function jumpTo(group: string) {
    const el = document.getElementById(groupDomId(group));
    el?.scrollIntoView({ block: "start" });
    el?.focus({ preventScroll: true });
  }

  const editable = view === "editable" && !locked && confirmed;
  const rejected = rejectedField(failure, changes);
  const barShown = view === "editable" && (dirty || busy === "save");
  const labelOf = (name: string) => settings.find((s) => s.name === name)?.label || name;
  const crumbsCar = file.car_folder;
  const groups = groupFields(settings);

  return (
    <div class="setup-file ds" data-view={view}>
      <p class="crumbs">
        <a href={href({ page: "setups", customerId })}>Setups</a>
        <span aria-hidden="true"> / </span>
        <span>{crumbsCar}</span>
      </p>
      <header class="d-head sf-head">
        <div class="sf-title">
          <h1>{baseName(file.name)}</h1>
          <p class="meta">
            {file.locked ? <span class="tag solid" data-testid="sto-locked">Locked original</span> : <span class="tag">Coach setup</span>}
            {!file.locked ? (
              <span data-testid="sto-revision">
                {revisionLabel(d.version)} of {file.current_version}
              </span>
            ) : null}
            {!file.locked && d.history.length > 1 ? <RevisionPicker d={d} customerId={customerId} /> : null}
            {folderOf(file) ? <span class="sf-path">{folderOf(file)}/</span> : null}
            {!file.present ? <span class="tag dashed" title="The latest scan did not find this file in the car's setups folder; Coach keeps its copy.">Not in folder</span> : null}
            {parent ? (
              <span>
                from <a href={href({ page: "setupFile", customerId, fileId: parent.id }, { version: file.parent_version ? String(file.parent_version) : null })}>{baseName(parent.name)}</a>
                {file.parent_version && !parent.locked ? ` ${revisionLabel(file.parent_version).toLowerCase()}` : ""}
              </span>
            ) : null}
          </p>
        </div>
        <div class="sf-actions">
          {view === "history" ? (
            <button type="button" class="pill primary" disabled={locked} onClick={restore}>
              Restore as {revisionLabel(next).toLowerCase()}
            </button>
          ) : null}
          {view !== "unavailable" ? (
            <button type="button" class={`pill${view === "locked" ? " primary" : ""}`} disabled={locked} aria-expanded={saveAs !== null} onClick={openSaveAs}>
              Save as…
            </button>
          ) : null}
          <a class="pill" href={apiUrl(d.download_url)} download>
            {file.locked ? "Download original" : `Download ${revisionLabel(d.version).toLowerCase()}`}
          </a>
        </div>
      </header>

      {view === "locked" ? <p class="sf-line">Read-only original. Save as makes an editable Coach copy.</p> : null}
      {view === "history" ? (
        <p class="sf-line sf-history">
          {revisionLabel(d.version)} is read-only history. Restore copies it to {revisionLabel(next).toLowerCase()}.{" "}
          <a href={href({ page: "setupFile", customerId, fileId })}>Open {revisionLabel(file.current_version).toLowerCase()}</a>
        </p>
      ) : null}
      {view === "unavailable" ? (
        <Notice tone="warn">
          <span data-testid="sto-unavailable">Cannot open this file: {d.unavailable_reason ?? "the setup codec could not read it."}</span>
        </Notice>
      ) : null}
      {newer && view === "editable" ? <Notice tone="warn">{revisionLabel(data!.file.current_version)} was saved elsewhere. Save will show what changed there first.</Notice> : null}

      {pending ? (
        <div class="notice notice-warn sf-pending" role="status" data-testid="sto-pending">
          <span>
            {busy ? (pending.kind === "save" ? "Saving…" : "Creating…") : failure?.kind === "unconfirmed" ? failure.message : "Saving…"}
          </span>
          {!busy ? (
            <span class="sf-pending-actions">
              <button type="button" class="pill primary" onClick={() => void send(pending)}>
                Retry
              </button>
              <button type="button" class="pill" onClick={dropPending}>
                Discard request
              </button>
            </span>
          ) : null}
        </div>
      ) : null}
      {failure && failure.kind !== "unconfirmed" && failure.kind !== "name" && !(failure.kind === "rejected" && barShown) ? (
        <Notice tone={failure.kind === "conflict" ? "warn" : "bad"} role="alert">
          <span data-testid="sto-error">
            {failure.message}
            {failure.kind === "conflict" && view === "editable"
              ? ` Your ${plural(changeCount, "change")} ${changeCount === 1 ? "is" : "are"} kept on top of ${revisionLabel(file.current_version).toLowerCase()}${conflicts.length ? `; ${conflicts.map(labelOf).join(", ")} also changed there` : ""}. Review, then Save to create ${revisionLabel(next).toLowerCase()}.`
              : failure.kind === "conflict"
                ? ` Restore now adds ${revisionLabel(next).toLowerCase()}.`
                : ""}
          </span>
        </Notice>
      ) : null}

      {saveAs ? (
        <form class="d-card sf-saveas" aria-label="Save as" onSubmit={submitSaveAs}>
          <h2>Save as</h2>
          <p class="sf-line">
            A new editable Coach setup from {file.locked ? "this original" : revisionLabel(d.version).toLowerCase()}
            {view === "editable" && changeCount ? ` with your ${plural(changeCount, "change")}` : ""}.
          </p>
          <label class="sf-field">
            <span>Name</span>
            <input
              type="text"
              value={saveAs.name}
              maxLength={NAME_MAX}
              spellcheck={false}
              autoComplete="off"
              disabled={locked}
              aria-invalid={failure?.kind === "name"}
              aria-describedby={failure?.kind === "name" ? "sf-name-error" : undefined}
              onInput={(e) => setSaveAs({ ...saveAs, name: e.currentTarget.value })}
            />
          </label>
          {failure?.kind === "name" ? (
            <p id="sf-name-error" class="sf-error" role="alert">
              {failure.message}
            </p>
          ) : null}
          <label class="sf-field">
            <span>Note (optional)</span>
            <input type="text" value={saveAs.message} maxLength={MESSAGE_MAX} disabled={locked} onInput={(e) => setSaveAs({ ...saveAs, message: e.currentTarget.value })} />
          </label>
          <div class="sf-buttons">
            <button type="submit" class="pill primary" disabled={locked || !saveAs.name.trim()}>
              {busy === "save-as" ? "Creating…" : "Create setup"}
            </button>
            <button type="button" class="pill" disabled={busy !== null} onClick={() => setSaveAs(null)}>
              Cancel
            </button>
          </div>
        </form>
      ) : null}

      {document_ ? (
        <section class="d-card sf-params" aria-label="Parameters">
          <h2>
            Parameters <span class="count">{settings.length}</span>
          </h2>
          {document_.warnings.length ? (
            document_.warnings.map((w) => (
              <p key={w} class="sf-line sf-check" data-testid="sto-check">
                {w}
              </p>
            ))
          ) : (
            <p class="sf-line">Native file values and units.</p>
          )}
          {groups.length > 1 ? (
            <nav class="sf-jump" aria-label="Parameter groups">
              {groups.map((g) => (
                <button key={g.group} type="button" class="pill" onClick={() => jumpTo(g.group)}>
                  {g.group} <span class="count">{g.fields.length}</span>
                </button>
              ))}
            </nav>
          ) : null}
          <div class="sf-groups">
            {groups.map((g) => (
              <section key={g.group} id={groupDomId(g.group)} tabIndex={-1} class="sf-group" aria-label={g.group}>
                <h3>{g.group}</h3>
                <ul>
                  {g.fields.map((f) => (
                    <FieldRow
                      key={f.name}
                      f={f}
                      value={edits[f.name] ?? f.raw_value}
                      editable={editable && f.editable}
                      showEditable={view === "editable"}
                      invalid={rejected === f.name ? (failure as Extract<WriteFailure, { kind: "rejected" }>).message : null}
                      conflict={conflicts.includes(f.name)}
                      onInput={(v) =>
                        setEdits((prev) => {
                          const nextEdits = { ...prev };
                          if (v === f.raw_value) delete nextEdits[f.name];
                          else nextEdits[f.name] = v;
                          return nextEdits;
                        })
                      }
                    />
                  ))}
                </ul>
              </section>
            ))}
          </div>
        </section>
      ) : null}

      {document_ ? (
        <section class="d-card sf-notes" aria-label="Notes">
          <h2>Notes</h2>
          {view === "editable" ? (
            <label class="sf-field">
              <span class="visually-hidden">Notes</span>
              <textarea rows={4} maxLength={NOTES_MAX} disabled={locked} value={notes ?? document_.notes} onInput={(e) => setNotes(e.currentTarget.value)} />
              <small class="sf-count">
                {(notes ?? document_.notes).length} / {NOTES_MAX}
              </small>
            </label>
          ) : document_.notes ? (
            <p class="sf-notes-text">{document_.notes}</p>
          ) : (
            <p class="sf-line">No notes.</p>
          )}
        </section>
      ) : null}

      {!file.locked ? <Revisions d={d} customerId={customerId} /> : null}

      <section class="d-card d-file" aria-label="File">
        <h2>File</h2>
        <dl>
          {document_ ? (
            <div>
              <dt>Car</dt>
              <dd>{document_.internal_car_path}</dd>
            </div>
          ) : null}
          <div>
            <dt>Folder</dt>
            <dd>{[file.car_folder, folderOf(file)].filter(Boolean).join("/")}</dd>
          </div>
          <div>
            <dt>SHA-256</dt>
            <dd class="num" title={d.sha256}>
              {d.sha256.slice(0, 12)}…
            </dd>
          </div>
          {document_ ? (
            <div>
              <dt>Format</dt>
              <dd>STO version {document_.sto_version}</dd>
            </div>
          ) : null}
          <div>
            <dt>{file.locked ? "Imported" : "Saved"}</dt>
            <dd class="date">{formatDateTime(d.history.find((h) => h.version === d.version)?.created_at ?? file.updated_at)}</dd>
          </div>
        </dl>
      </section>

      {barShown ? (
        <div class="sf-bar" role="region" aria-label="Unsaved changes">
          <span class="sf-bar-count" data-testid="sto-change-count">
            {plural(changeCount, "change")}
          </span>
          <label class="sf-bar-message">
            <span class="visually-hidden">Revision note</span>
            <input type="text" placeholder="Revision note (optional)" value={message} maxLength={MESSAGE_MAX} disabled={locked} onInput={(e) => setMessage(e.currentTarget.value)} />
          </label>
          {failure?.kind === "rejected" ? (
            <span class="sf-error" role="alert" data-testid="sto-error">
              {failure.message}
            </span>
          ) : null}
          <button type="button" class="pill" disabled={locked} onClick={discard}>
            Discard
          </button>
          <button type="button" class="pill primary" disabled={locked} onClick={save}>
            {busy === "save" ? "Saving…" : `Save ${revisionLabel(next).toLowerCase()}`}
          </button>
        </div>
      ) : null}
    </div>
  );
}

function FieldRow({
  f,
  value,
  editable,
  showEditable,
  invalid,
  conflict,
  onInput,
}: {
  f: StoField;
  value: string;
  editable: boolean;
  showEditable: boolean;
  invalid: string | null;
  conflict: boolean;
  onInput: (v: string) => void;
}) {
  const hint = nativeHint(f);
  const changed = value !== f.raw_value;
  const id = fieldDomId(f.name);
  return (
    <li class="sf-row" data-name={f.name} data-changed={changed ? "true" : undefined} data-conflict={conflict ? "true" : undefined}>
      <label class="sf-label" for={showEditable && f.editable ? id : undefined}>
        <span>{f.label || f.name}</span>
        {hint ? <small>{hint}</small> : null}
      </label>
      <span class="sf-value">
        {showEditable && f.editable ? (
          <input
            id={id}
            type="text"
            class="num"
            value={value}
            spellcheck={false}
            autoComplete="off"
            disabled={!editable}
            aria-invalid={invalid ? true : undefined}
            aria-describedby={changed || invalid ? `${id}-was` : undefined}
            onInput={(e) => onInput(e.currentTarget.value)}
          />
        ) : (
          <span class="num" title={showEditable && !f.editable ? "Not editable in this file" : undefined}>
            {f.raw_value}
          </span>
        )}
        {changed || invalid ? (
          <small id={`${id}-was`} class={invalid ? "sf-error" : "sf-was"}>
            {invalid ?? `was ${f.raw_value}`}
          </small>
        ) : null}
      </span>
      <span class="sf-unit">{f.unit ?? ""}</span>
    </li>
  );
}

/** Every revision one choice away from the header, above a long parameter list. The current one opens at the file's own address. */
function RevisionPicker({ d, customerId }: { d: StoDetail; customerId: number }) {
  const file = d.file;
  return (
    <label class="sf-rev-pick">
      <span class="visually-hidden">Open revision</span>
      <select
        value={String(d.version)}
        onChange={(e) => {
          const el = e.currentTarget;
          const v = Number(el.value);
          // The page follows the address; a declined leave question keeps this revision, so the choice shows it until then.
          el.value = String(d.version);
          navigate(href({ page: "setupFile", customerId, fileId: file.id }, { version: v === file.current_version ? null : String(v) }));
        }}
      >
        {historyNewestFirst(d.history).map((r) => (
          <option key={r.version} value={String(r.version)}>
            {revisionLabel(r.version)}
            {r.version === file.current_version ? " · current" : ""}
          </option>
        ))}
      </select>
    </label>
  );
}

function Revisions({ d, customerId }: { d: StoDetail; customerId: number }) {
  const rows = historyNewestFirst(d.history);
  const file: StoFile = d.file;
  return (
    <section class="d-card sf-revisions" aria-label="Revisions">
      <h2>
        Revisions <span class="count">{rows.length}</span>
      </h2>
      <ol class="sf-revs">
        {rows.map((r) => {
          const here = r.version === d.version;
          const current = r.version === file.current_version;
          return (
            <li key={r.version} class={here ? "sf-rev here" : "sf-rev"} aria-current={here ? "true" : undefined}>
              <a class="sf-rev-name" href={href({ page: "setupFile", customerId, fileId: file.id }, { version: current ? null : String(r.version) })}>
                {revisionLabel(r.version)}
              </a>
              <span class="sf-rev-tags">
                {current ? <span class="tag solid">Current</span> : null}
                {r.based_on_version != null && r.based_on_version !== r.version - 1 ? <span class="tag">from {revisionLabel(r.based_on_version).toLowerCase()}</span> : null}
              </span>
              <span class="sf-rev-msg">{r.message}</span>
              <span class="date">{formatDateTime(r.created_at)}</span>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

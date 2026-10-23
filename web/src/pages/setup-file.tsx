import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { api, apiUrl, paths } from "../core/api";
import { formatDateTime, plural } from "../core/format";
import { cachedValue, invalidateKey, isFresh, setCached, useResource } from "../core/resource";
import { href, navigate, setLeaveGuard } from "../core/router";
import { uuid4 } from "../core/uuid";
import { ErrorLine, Loading, Notice } from "../ui/kit";
import {
  type Edit,
  type FieldView,
  MESSAGE_MAX,
  NAME_MAX,
  NOTES_MAX,
  type PendingWrite,
  type Representation,
  type StoDetail,
  type StoField,
  type StoFile,
  type StoGarageWrite,
  type StoWrite,
  type WriteFailure,
  type WriteKind,
  baseName,
  blockedEdits,
  changesOf,
  choiceLabel,
  clearPending,
  conflictingEdits,
  describeFailure,
  editFor,
  editsOfPending,
  fieldDomId,
  fieldView,
  folderOf,
  garageWritable,
  garageWrite,
  groupDomId,
  groupFields,
  hasGarage,
  historyNewestFirst,
  isAmbiguous,
  profileChanged,
  profileKey,
  readPending,
  rejectedField,
  revisionLabel,
  rowHint,
  sameRequest,
  suggestName,
  viewOf,
  withNewerMetadata,
  writePath,
  writePending,
} from "../setups/sto";
import "../setups/setups.css";

/**
 * One `.sto` file: its parameters, notes and revisions on one screen. Imported originals are read-only; Save as makes an editable
 * Coach setup; Save appends a revision; an old revision is read-only and can be restored as a new one. A row Python maps to the
 * garage shows and edits garage values; the rest show native file values.
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
function post(p: PendingWrite) {
  if (p.representation === "garage") {
    const body = p.body as StoGarageWrite;
    return p.kind === "save" ? api.saveStoGarage(p.customerId, p.fileId, body, p.ifMatch ?? "", p.key) : api.saveStoGarageAs(p.customerId, p.fileId, body, p.key);
  }
  const body = p.body as StoWrite;
  return p.kind === "save" ? api.saveSto(p.customerId, p.fileId, body, p.ifMatch ?? "", p.key) : api.saveStoAs(p.customerId, p.fileId, body, p.key);
}

async function sendWrite(p: PendingWrite): Promise<StoDetail> {
  writePending(p);
  try {
    const r = await post(p);
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
  const [edits, setEdits] = useState<Record<string, Edit>>(() => (restored ? editsOfPending(restored) : {}));
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
  const writable = garageWritable(document_);
  const profile = profileKey(document_);
  const split = useMemo(() => changesOf(settings, edits, writable, profile), [settings, edits, writable, profile]);
  const blocked = useMemo(() => blockedEdits(settings, edits, writable, profile), [settings, edits, writable, profile]);
  // Every name the server may quote back, in either representation.
  const changes = useMemo(() => ({ ...split.native, ...split.garage }), [split]);
  const garageCount = Object.keys(split.garage).length;
  const notesChanged = notes !== null && document_ !== null && notes !== document_.notes;
  const changeCount = Object.keys(changes).length + blocked.length + (notesChanged ? 1 : 0);
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
    // A reload held back while editing (a newer revision, a changed garage profile) becomes the base now there is nothing to keep.
    if (data && data !== d && data.file.id === d.file.id && data.file.current_version >= d.file.current_version) setBase(data);
  }

  /** Builds (or, for the identical request, reuses) the persisted write and sends it. */
  async function run(kind: WriteKind, representation: Representation, body: StoWrite | StoGarageWrite, ifMatch: string | null) {
    const garage = representation === "garage";
    const request = {
      kind,
      representation,
      path: writePath(kind, representation, customerId, fileId),
      customerId,
      fileId,
      ifMatch,
      profile: garage ? (document_?.garage_profile_id ?? null) : null,
      descriptorVersion: garage ? (document_?.descriptor_version ?? null) : null,
      body,
    };
    const p: PendingWrite = sameRequest(pending, request) ? pending : { v: 2, ...request, key: uuid4(), at: new Date().toISOString() };
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
      // A confirmed Save as settles these edits before navigation. A render may
      // run before hashchange and must not re-arm the unsaved-work guard.
      setEdits({});
      setNotes(null);
      setMessage("");
      if (p.kind === "save-as" || saved.file.id !== fileId || version !== null) {
        navigate({ page: "setupFile", customerId, fileId: saved.file.id });
        return;
      }
      setBase(saved);
    } catch (e) {
      if (!live.current) return;
      setBusy(null);
      const f = describeFailure(e, p.kind, p.representation);
      setPending(f.kind === "unconfirmed" ? p : null);
      setFailure(f);
      // The current profile is read now; it becomes the base only after Discard, so nothing typed is reinterpreted.
      if (f.kind === "profile") res.reload();
      if (f.kind === "conflict" && f.current) {
        const cur = f.current;
        rememberCurrent(customerId, cur);
        if (view === "editable") {
          setConflicts(conflictingEdits(d.document?.settings ?? [], cur.document?.settings ?? [], edits));
          setBase(cur);
        } else setBase({ ...d, file: cur.file, history: cur.history });
      }
    }
  }

  const saveBlocked = blocked.length > 0 || failure?.kind === "profile";

  function save() {
    if (saveBlocked) return;
    const extra = { ...(notesChanged ? { notes: notes! } : {}), ...(message.trim() ? { message: message.trim() } : {}) };
    // Garage routes only when a garage value changed; a native-only Save keeps its route and body.
    if (garageCount && document_) void run("save", "garage", garageWrite(d.version, document_, split, extra), String(file.current_version));
    else void run("save", "native", { base_version: d.version, changes: split.native, ...extra }, String(file.current_version));
  }

  function restore() {
    void run("save", "native", { base_version: d.version, changes: {}, message: `Restored ${revisionLabel(d.version).toLowerCase()}` }, String(file.current_version));
  }

  function openSaveAs() {
    setFailure(null);
    setSaveAs({ name: suggestName(file.name, file.car_folder, files), message: "" });
  }

  function submitSaveAs(e: Event) {
    e.preventDefault();
    if (!saveAs) return;
    const editing = view === "editable";
    if (editing && saveBlocked) return;
    const extra = { ...(editing && notesChanged ? { notes: notes! } : {}), name: saveAs.name, ...(saveAs.message.trim() ? { message: saveAs.message.trim() } : {}) };
    if (editing && garageCount && document_) void run("save-as", "garage", garageWrite(d.version, document_, split, extra), null);
    else void run("save-as", "native", { base_version: d.version, changes: editing ? split.native : {}, ...extra }, null);
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
  const labelOf = (name: string) => {
    const s = settings.find((x) => x.name === name);
    return s ? fieldView(s, writable).label : name;
  };
  const crumbsCar = file.car_folder;
  const groups = groupFields(settings);
  const mixed = hasGarage(settings);
  const garageRows = settings.filter((s) => s.garage).length;
  const profileLabel = document_?.garage_profile_label || document_?.garage_profile_id || null;
  const blockedLine =
    view === "editable" && blocked.length && failure?.kind !== "profile"
      ? profileChanged(edits, profile)
        ? "The garage profile changed after these values were typed. They are kept for review; Discard to continue."
        : `${blocked.map(labelOf).join(", ")} changed form after you typed. Kept for review; Discard to continue.`
      : null;

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
        <Notice tone={failure.kind === "conflict" || failure.kind === "profile" || failure.kind === "unavailable" ? "warn" : "bad"} role="alert">
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
            <button type="submit" class="pill primary" disabled={locked || !saveAs.name.trim() || (view === "editable" && saveBlocked)}>
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
            <p class="sf-line">{mixed ? "Garage values where verified; other rows are native file values." : "Native file values and units."}</p>
          )}
          {mixed ? (
            <p class="sf-line" data-testid="sto-garage-scope">
              {plural(garageRows, "row")} in garage units{profileLabel ? ` for ${profileLabel}` : ""}; rows marked native show the file's own value.
            </p>
          ) : null}
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
                  {g.fields.map((f) => {
                    const fv = fieldView(f, writable);
                    const stuck = view === "editable" && blocked.includes(f.name);
                    return (
                      <FieldRow
                        key={f.name}
                        f={f}
                        fv={fv}
                        hint={rowHint(f, mixed)}
                        edit={edits[f.name] ?? null}
                        editable={editable && fv.editable && !stuck && failure?.kind !== "profile"}
                        showEditable={view === "editable"}
                        blocked={stuck}
                        invalid={rejected === f.name ? (failure as Extract<WriteFailure, { kind: "rejected" }>).message : null}
                        conflict={conflicts.includes(f.name)}
                        onInput={(v) => setEdits((prev) => editFor(prev, f.name, fv, v, profile))}
                      />
                    );
                  })}
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
          {profileLabel ? (
            <div>
              <dt>Garage profile</dt>
              <dd data-testid="sto-garage-profile">{profileLabel}</dd>
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
          {blockedLine ? (
            <span class="sf-error" role="alert" data-testid="sto-blocked">
              {blockedLine}
            </span>
          ) : null}
          <button type="button" class="pill" disabled={locked} onClick={discard}>
            Discard
          </button>
          <button type="button" class="pill primary" disabled={locked || saveBlocked} onClick={save}>
            {busy === "save" ? "Saving…" : `Save ${revisionLabel(next).toLowerCase()}`}
          </button>
        </div>
      ) : null}
    </div>
  );
}

/**
 * One parameter in the representation the backend chose: garage label, value, unit and verified limits when supplied, else the
 * native file value. Values are shown and sent as exact strings; a choice list offers only the backend's choices. A value typed
 * in a representation the row no longer has stays visible and read-only until Discard.
 */
function FieldRow({
  f,
  fv,
  hint,
  edit,
  editable,
  showEditable,
  blocked,
  invalid,
  conflict,
  onInput,
}: {
  f: StoField;
  fv: FieldView;
  hint: string | null;
  edit: Edit | null;
  editable: boolean;
  showEditable: boolean;
  blocked: boolean;
  invalid: string | null;
  conflict: boolean;
  onInput: (v: string) => void;
}) {
  const value = edit ? edit.value : fv.value;
  const changed = !!edit && (blocked || edit.rep !== fv.rep || edit.value !== fv.value);
  const id = fieldDomId(f.name);
  const control = showEditable && (fv.editable || !!edit);
  // A retained draft belongs to the old representation/profile. Never put a
  // new unit or constraint beside that old number.
  const limits = !blocked && (fv.limits || fv.note) ? [fv.limits, fv.note].filter(Boolean).join(" · ") : null;
  const describedBy = [changed || invalid || blocked ? `${id}-was` : null, limits ? `${id}-limits` : null].filter(Boolean).join(" ") || undefined;
  const shown = (v: string) => (fv.choices ? choiceLabel(fv, v) : v);
  const note = invalid ?? (blocked && edit ? `${edit.rep === "garage" ? "Garage" : "Native"} value kept; Discard to edit` : `was ${shown(fv.value)}`);
  const choices = fv.choices
    ? [...fv.choices, ...[value, fv.value].filter((v, i, all) => all.indexOf(v) === i && !fv.choices!.some((c) => c.value === v)).map((v) => ({ value: v, label: v }))]
    : null;
  return (
    <li
      class="sf-row"
      data-name={f.name}
      data-rep={fv.rep}
      data-changed={changed ? "true" : undefined}
      data-conflict={conflict ? "true" : undefined}
      data-blocked={blocked ? "true" : undefined}
    >
      <label class="sf-label" for={control ? id : undefined}>
        <span>{fv.label}</span>
        {hint ? <small title={fv.rep === "garage" ? "Native key and file value" : undefined}>{hint}</small> : null}
      </label>
      <span class="sf-value">
        {control && choices && !blocked ? (
          <select id={id} value={value} disabled={!editable} aria-invalid={invalid ? true : undefined} aria-describedby={describedBy} onChange={(e) => onInput(e.currentTarget.value)}>
            {choices.map((c) => (
              <option key={c.value} value={c.value}>
                {c.label}
              </option>
            ))}
          </select>
        ) : control ? (
          <input
            id={id}
            type="text"
            class="num"
            inputMode={fv.inputMode}
            value={value}
            spellcheck={false}
            autoComplete="off"
            disabled={!editable}
            aria-invalid={invalid || blocked ? true : undefined}
            aria-describedby={describedBy}
            onInput={(e) => onInput(e.currentTarget.value)}
          />
        ) : (
          <span class="num" title={showEditable && !fv.editable ? "Not editable in this file" : undefined}>
            {shown(fv.value)}
          </span>
        )}
        {changed || invalid ? (
          <small id={`${id}-was`} class={invalid || blocked ? "sf-error" : "sf-was"}>
            {note}
          </small>
        ) : null}
      </span>
      <span class="sf-unit">{blocked ? "" : fv.unit ?? ""}</span>
      {limits ? (
        <small id={`${id}-limits`} class="sf-limit">
          {limits}
        </small>
      ) : null}
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

import { ApiError, NetworkError, type Schemas, VersionConflictError, paths } from "../core/api";
import { safeStorage } from "../core/store";

/**
 * The `.sto` file library and editor (docs/08-rewrite/contracts/sto-editor.md). Imported files are locked originals; a Coach
 * setup is an editable lineage whose every Save appends an immutable revision. The Python codec owns names, units, groups and
 * what is editable; the UI never converts a native value or invents a limit. Where Python supplies a verified garage
 * representation the row shows and edits that instead, as exact strings; every other row stays the native file value.
 */
export type StoFile = Schemas["StoFile"];
export type StoDetail = Schemas["StoDetail"];
export type StoDocument = Schemas["StoDocument"];
export type StoField = Schemas["StoField"];
export type StoGarageField = Schemas["StoGarageField"];
export type StoGarageChoice = Schemas["StoGarageChoice"];
export type StoWrite = Schemas["StoWrite"];
export type StoGarageWrite = Schemas["StoGarageWrite"];
export type StoRevision = Schemas["StoRevision"];

/** Notes obey the native 0x4000-byte UTF-16LE bound including its terminator (`StoWrite.notes` maxLength). */
export const NOTES_MAX = 8191;
export const MESSAGE_MAX = 500;
export const NAME_MAX = 120;

const lower = (s: string) => s.toLowerCase();
const time = (iso: string) => {
  const t = Date.parse(iso);
  return Number.isFinite(t) ? t : 0;
};

/** The last path segment: a source's name is its full relative path, a Coach setup's name is already a basename. */
export const baseName = (name: string): string => name.split(/[\\/]/).pop() ?? name;

/** The folders above the file inside the car folder (`Week 3/` of `Week 3/race.sto`), or null for a file at the top. */
export function folderOf(f: Pick<StoFile, "name" | "car_folder">): string | null {
  const parts = stripCarFolder(f.name, f.car_folder).split(/[\\/]/);
  return parts.length > 1 ? parts.slice(0, -1).join("/") : null;
}

function stripCarFolder(path: string, carFolder: string): string {
  const p = path.replace(/\\/g, "/");
  return lower(p).startsWith(`${lower(carFolder)}/`) ? p.slice(carFolder.length + 1) : p;
}

const stem = (name: string) => baseName(name).replace(/\.sto$/i, "");

/** Coach setups first (newest first), then the originals A to Z by path. */
export function sortFiles(files: readonly StoFile[]): StoFile[] {
  return [...files].sort((a, b) => {
    if (a.locked !== b.locked) return a.locked ? 1 : -1;
    if (!a.locked) return time(b.updated_at) - time(a.updated_at) || a.name.localeCompare(b.name);
    return a.name.localeCompare(b.name, undefined, { sensitivity: "base" }) || a.id.localeCompare(b.id);
  });
}

export function matchesText(f: StoFile, q: string): boolean {
  const t = q.trim().toLowerCase();
  return !t || lower(f.name).includes(t);
}

/**
 * The STO files by car folder. `folderToCar` maps a lowercased folder to the car path the setup library knows it as (a folder is
 * not always the path); a folder it does not know stays its own group.
 */
export function filesByCar(files: readonly StoFile[], folderToCar: ReadonlyMap<string, string>): Map<string, StoFile[]> {
  const out = new Map<string, StoFile[]>();
  for (const f of files) {
    const k = lower(folderToCar.get(lower(f.car_folder)) ?? f.car_folder);
    const list = out.get(k);
    if (list) list.push(f);
    else out.set(k, [f]);
  }
  for (const [k, list] of out) out.set(k, sortFiles(list));
  return out;
}

/** The library entry an observed setup's file occurrence is: same blob, same folder, same path below it. */
export function stoFileFor(entry: Schemas["SetupFileEntry"], files: readonly StoFile[]): StoFile | null {
  const direct = (entry as Record<string, unknown>).file_id;
  if (typeof direct === "string") return files.find((f) => f.id === direct) ?? null;
  const path = lower(stripCarFolder(entry.relative_path, entry.car_folder));
  return files.find((f) => f.locked && f.sha256 === entry.sha256 && lower(f.car_folder) === lower(entry.car_folder) && lower(stripCarFolder(f.name, f.car_folder)) === path) ?? null;
}

/** A free name for a Save As in this car: `<stem> copy.sto`, then `<stem> copy 2.sto`, compared case-insensitively. */
export function suggestName(from: string, carFolder: string, files: readonly StoFile[]): string {
  const taken = new Set(files.filter((f) => !f.locked && lower(f.car_folder) === lower(carFolder)).map((f) => lower(baseName(f.name))));
  const base = `${stem(from)} copy`;
  for (let i = 1; ; i++) {
    const name = `${base}${i === 1 ? "" : ` ${i}`}.sto`;
    if (!taken.has(lower(name))) return name;
  }
}

/** What the editor shows for a field: the native name only when the label does not already say it. */
export const nativeHint = (f: StoField): string | null => (f.label && f.label !== f.name ? f.name : null);

/** DOM ids from native names and groups: percent-encoding keeps `future.slot` and `future-slot` apart. */
export const fieldDomId = (name: string): string => `sf-${encodeURIComponent(name)}`;
export const groupDomId = (group: string): string => `sf-g-${encodeURIComponent(group)}`;

/** Fields by backend group, groups in the order the document first names them. */
export function groupFields(settings: readonly StoField[]): { group: string; fields: StoField[] }[] {
  const groups = new Map<string, StoField[]>();
  for (const s of settings) {
    const g = s.group || "Other";
    const list = groups.get(g);
    if (list) list.push(s);
    else groups.set(g, [s]);
  }
  return [...groups].map(([group, fields]) => ({ group, fields }));
}

// ---- representation: garage where Python supplies it, else the native file value ------------------------------

export type Representation = "native" | "garage";

/** The garage profile and descriptor version a document's garage values belong to. Values typed under one are never sent under another. */
export const profileKey = (doc: Pick<StoDocument, "garage_profile_id" | "descriptor_version"> | null | undefined): string =>
  JSON.stringify([doc?.garage_profile_id ?? null, doc?.descriptor_version ?? null]);

/** Garage values are written only under a named profile of the descriptor version this page knows (2). */
export const garageWritable = (doc: StoDocument | null | undefined): boolean =>
  !!doc?.garage_profile_id && (doc.descriptor_version == null || doc.descriptor_version === 2);

/** Whether any row of the document carries a garage representation. */
export const hasGarage = (settings: readonly StoField[]): boolean => settings.some((s) => !!s.garage);

/** One row as shown: every string comes from the backend unchanged. */
export interface FieldView {
  rep: Representation;
  label: string;
  value: string;
  unit: string | null;
  /** Backend-decided only: `garage.editable` when a garage view exists (and the profile is writable), else the native flag. */
  editable: boolean;
  choices: readonly StoGarageChoice[] | null;
  /** The verified constraints exactly as supplied; null when none is supplied (missing means unverified, not unlimited). */
  limits: string | null;
  note: string | null;
  /** A decimal keypad only where the backend's own minimum rules out a sign; never a parsed or inferred bound. */
  inputMode: "decimal" | "text";
}

/** Supplied constraints as text, `0 to 100, step 0.5`. A step without its grid origin is incomplete and is not shown. */
export function limitsOf(g: StoGarageField): string | null {
  const parts: string[] = [];
  if (g.min !== undefined && g.max !== undefined) parts.push(`${g.min} to ${g.max}`);
  else if (g.min !== undefined) parts.push(`min ${g.min}`);
  else if (g.max !== undefined) parts.push(`max ${g.max}`);
  if (g.step !== undefined && g.step_base !== undefined) parts.push(g.step_base === g.min ? `step ${g.step}` : `step ${g.step} from ${g.step_base}`);
  return parts.length ? parts.join(", ") : null;
}

export function fieldView(f: StoField, writable: boolean): FieldView {
  const g = f.garage;
  if (g) {
    const choices = g.choices?.length ? g.choices : null;
    return {
      rep: "garage",
      label: g.label || f.label || f.name,
      value: g.value,
      unit: g.unit ?? null,
      editable: g.editable === true && writable,
      choices,
      limits: choices ? null : limitsOf(g),
      note: g.constraint_note ?? null,
      inputMode: !choices && g.min !== undefined && !g.min.trim().startsWith("-") ? "decimal" : "text",
    };
  }
  return { rep: "native", label: f.label || f.name, value: f.raw_value, unit: f.unit, editable: f.editable, choices: null, limits: null, note: null, inputMode: "text" };
}

/** A choice's label for its exact value, or the value itself when the backend lists no such choice. */
export const choiceLabel = (v: FieldView, value: string): string => v.choices?.find((c) => c.value === value)?.label ?? value;

/**
 * The secondary line under a row's label. A garage row names its native key and the file's exact token; a native row in a
 * document that also has garage rows says so; otherwise the native name only when the label does not already say it.
 */
export function rowHint(f: StoField, mixed: boolean): string | null {
  if (f.garage) return `${f.name} ${f.raw_value}`;
  const hint = nativeHint(f);
  if (!mixed) return hint;
  return hint ? `native · ${hint}` : "native";
}

/** A typed value with the representation and profile it was typed in, so it is never sent or read as another. */
export interface Edit {
  rep: Representation;
  value: string;
  /** `profileKey` of the document when typed, for garage values; null for native ones (their meaning does not depend on a profile). */
  profile: string | null;
}
export type Edits = Readonly<Record<string, Edit>>;

/** The edit a row's input makes: none when the text is the base spelling again. */
export function editFor(prev: Edits, name: string, view: FieldView, value: string, profile: string): Record<string, Edit> {
  const next = { ...prev };
  if (value === view.value) delete next[name];
  else next[name] = { rep: view.rep, value, profile: view.rep === "garage" ? profile : null };
  return next;
}

export interface SplitChanges {
  garage: Record<string, string>;
  native: Record<string, string>;
}

/**
 * Edits as the two write maps: name to the exact text typed, only for rows still shown and editable in the representation the
 * value was typed in, and only where it differs from the base spelling. Blocked edits (`blockedEdits`) are never sent.
 */
export function changesOf(settings: readonly StoField[], edits: Edits, writable: boolean, profile: string): SplitChanges {
  const out: SplitChanges = { garage: {}, native: {} };
  for (const s of settings) {
    const e = edits[s.name];
    if (!e) continue;
    const v = fieldView(s, writable);
    if (e.rep !== v.rep || !v.editable || e.value === v.value || (e.rep === "garage" && e.profile !== profile)) continue;
    out[e.rep][s.name] = e.value;
  }
  return out;
}

/**
 * Edits that cannot be sent as typed: the row is gone, now shows another representation, is no longer editable, or a garage value
 * was typed under another profile. They stay on screen for review; Save waits for Discard. Nothing is converted.
 */
export function blockedEdits(settings: readonly StoField[], edits: Edits, writable: boolean, profile: string): string[] {
  const by = new Map(settings.map((s) => [s.name, s]));
  return Object.entries(edits)
    .filter(([name, e]) => {
      const s = by.get(name);
      if (!s) return true;
      const v = fieldView(s, writable);
      return e.rep !== v.rep || !v.editable || (e.rep === "garage" && e.profile !== profile);
    })
    .map(([name]) => name);
}

/** Garage values typed under a profile other than the document's: the whole save waits for Discard. */
export const profileChanged = (edits: Edits, profile: string): boolean => Object.values(edits).some((e) => e.rep === "garage" && e.profile !== profile);

/** A field the user changed whose shown value or representation in the newer revision differs from the one the user started from. */
export function conflictingEdits(before: readonly StoField[], after: readonly StoField[], edits: Readonly<Record<string, unknown>>): string[] {
  const shown = (s: StoField) => (s.garage ? `g:${s.garage.value}` : `n:${s.raw_value}`);
  const was = new Map(before.map((s) => [s.name, shown(s)]));
  return after.filter((s) => s.name in edits && was.get(s.name) !== shown(s)).map((s) => s.name);
}

/** The garage write body: Python converts and checks; the UI passes the exact strings and the profile they were typed under. */
export function garageWrite(baseVersion: number, doc: StoDocument, changes: SplitChanges, extra: { notes?: string; name?: string; message?: string }): StoGarageWrite {
  return {
    base_version: baseVersion,
    garage_profile_id: doc.garage_profile_id!,
    ...(doc.descriptor_version === 2 ? { descriptor_version: 2 as const } : {}),
    garage_changes: changes.garage,
    ...(Object.keys(changes.native).length ? { native_changes: changes.native } : {}),
    ...extra,
  };
}

export type StoView = "locked" | "editable" | "history" | "unavailable";

/** Locked originals and unreadable files are read-only; an old revision is history; only the current revision of a Coach setup is edited. */
export function viewOf(d: StoDetail): StoView {
  if (!d.document) return "unavailable";
  if (d.file.locked) return "locked";
  return d.version === d.file.current_version ? "editable" : "history";
}

export function revisionLabel(n: number): string {
  return `Revision ${n}`;
}

/** Newest first. */
export function historyNewestFirst(h: readonly StoRevision[]): StoRevision[] {
  return [...h].sort((a, b) => b.version - a.version);
}

/**
 * A read revision brought up to a newer answer for the same file: its own document, version, SHA and download stay, the file's
 * current revision and history come from `newer`. Null when `newer` is no newer, so an old revision is never taken for the current one.
 */
export function withNewerMetadata(old: StoDetail, newer: StoDetail): StoDetail | null {
  if (old.file.id !== newer.file.id || old.version === newer.version || old.file.current_version >= newer.file.current_version) return null;
  return { ...old, file: newer.file, history: newer.history };
}

// ---- writes: one persisted request per file until the server answers ------------------------------

export type WriteKind = "save" | "save-as";

/** The exact endpoint of a write: native routes unchanged, garage values only on their own routes. */
export function writePath(kind: WriteKind, rep: Representation, customerId: number, fileId: string): string {
  if (rep === "garage") return kind === "save" ? paths.stoGarage(customerId, fileId) : paths.stoGarageSaveAs(customerId, fileId);
  return kind === "save" ? paths.stoFile(customerId, fileId) : paths.stoSaveAs(customerId, fileId);
}

/**
 * A write the server may or may not have committed. It keeps its representation, endpoint, garage profile, Idempotency-Key, body
 * and If-Match until a definite answer, so a retry after a dropped connection (or a reload) sends the identical request and the
 * server replays the first result. Version 1 records (native only, before garage writes) are read as native version 2 records.
 */
export interface PendingWrite {
  v: 2;
  kind: WriteKind;
  representation: Representation;
  path: string;
  customerId: number;
  fileId: string;
  key: string;
  ifMatch: string | null;
  /** The garage profile and descriptor version the body's garage values were typed under; null for a native write. */
  profile: string | null;
  descriptorVersion: number | null;
  body: StoWrite | StoGarageWrite;
  at: string;
}

/** Everything that makes two writes the same request; the key and time are not part of it. */
export type WriteRequest = Omit<PendingWrite, "v" | "key" | "at">;

const pendingKey = (customerId: number, fileId: string) => `coach.sto.pending.${customerId}.${fileId}`;
const isMap = (v: unknown): v is Record<string, string> => !!v && typeof v === "object" && !Array.isArray(v) && Object.values(v).every((x) => typeof x === "string");

export function readPending(customerId: number, fileId: string, storage = safeStorage): PendingWrite | null {
  try {
    const raw = storage?.getItem(pendingKey(customerId, fileId));
    if (!raw) return null;
    const p = JSON.parse(raw) as Record<string, unknown> | null;
    if (!p || typeof p !== "object" || p.customerId !== customerId || p.fileId !== fileId) return null;
    const { kind, key, ifMatch, representation } = p;
    if ((kind !== "save" && kind !== "save-as") || typeof key !== "string" || (ifMatch !== null && typeof ifMatch !== "string")) return null;
    const body = p.body as Record<string, unknown> | null;
    if (!body || typeof body !== "object") return null;
    if (p.v === 1) {
      if (!isMap(body.changes)) return null;
      return { v: 2, kind, representation: "native", path: writePath(kind, "native", customerId, fileId), customerId, fileId, key, ifMatch, profile: null, descriptorVersion: null, body: body as StoWrite, at: String(p.at ?? "") };
    }
    if (p.v !== 2 || (representation !== "native" && representation !== "garage") || p.path !== writePath(kind, representation, customerId, fileId)) return null;
    if (representation === "native" ? !isMap(body.changes) : !isMap(body.garage_changes) || typeof p.profile !== "string" || body.garage_profile_id !== p.profile) return null;
    return p as unknown as PendingWrite;
  } catch {
    return null;
  }
}

/** The values a pending write carries, in the representation and profile they were typed in, for review after a reload. */
export function editsOfPending(p: PendingWrite): Record<string, Edit> {
  const out: Record<string, Edit> = {};
  if (p.representation === "garage") {
    const b = p.body as StoGarageWrite;
    const profile = profileKey({ garage_profile_id: p.profile, descriptor_version: p.descriptorVersion ?? undefined });
    for (const [name, value] of Object.entries(b.native_changes ?? {})) out[name] = { rep: "native", value, profile: null };
    for (const [name, value] of Object.entries(b.garage_changes)) out[name] = { rep: "garage", value, profile };
  } else for (const [name, value] of Object.entries((p.body as StoWrite).changes)) out[name] = { rep: "native", value, profile: null };
  return out;
}

export function writePending(p: PendingWrite, storage = safeStorage): void {
  try {
    storage?.setItem(pendingKey(p.customerId, p.fileId), JSON.stringify(p));
  } catch {
    /* blocked storage: the request still carries its key for this page's lifetime */
  }
}

export function clearPending(customerId: number, fileId: string, storage = safeStorage): void {
  try {
    storage?.removeItem(pendingKey(customerId, fileId));
  } catch {
    /* nothing stored */
  }
}

const canonical = (v: unknown): string =>
  JSON.stringify(v, (_k, x) => (x && typeof x === "object" && !Array.isArray(x) ? Object.fromEntries(Object.entries(x).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))) : x));

/** The same request as a stored one: same action, representation, endpoint, file, profile, If-Match and body. Only then may its key be reused. */
export function sameRequest(p: PendingWrite | null, r: WriteRequest): p is PendingWrite {
  return (
    !!p &&
    p.kind === r.kind &&
    p.representation === r.representation &&
    p.path === r.path &&
    p.customerId === r.customerId &&
    p.fileId === r.fileId &&
    p.ifMatch === r.ifMatch &&
    p.profile === r.profile &&
    p.descriptorVersion === r.descriptorVersion &&
    canonical(p.body) === canonical(r.body)
  );
}

/** Network failures leave the outcome unknown; any answer from the server settles it. */
export const isAmbiguous = (e: unknown): boolean => e instanceof NetworkError || (e instanceof ApiError && (e.status >= 500 || e.status === 408 || e.status === 429));

export type WriteFailure =
  | { kind: "unconfirmed"; message: string }
  | { kind: "conflict"; current: StoDetail | null; message: string }
  | { kind: "name"; message: string }
  | { kind: "rejected"; message: string; field: string | null; code?: string | null }
  /** The garage profile changed on the server: values stay for review, Save waits for Discard. */
  | { kind: "profile"; message: string }
  /** A server without the garage routes: values stay, the request is cleared. */
  | { kind: "unavailable"; message: string }
  | { kind: "error"; message: string };

const fieldOf = (details: Record<string, unknown> | null): string | null => {
  for (const k of ["name", "key", "field", "parameter"]) {
    const v = details?.[k];
    if (typeof v === "string" && v) return v;
  }
  return null;
};

/**
 * The parameter a rejected edit names: the server's `details` when it gives one, else the changed native name its message quotes.
 * The codec's `invalid_edit` text names no parameter, so with one changed value and no word of notes it is that value. Null otherwise.
 */
export function rejectedField(failure: WriteFailure | null, changes: Readonly<Record<string, string>>): string | null {
  if (failure?.kind !== "rejected") return null;
  if (failure.field) return failure.field;
  const names = Object.keys(changes).sort((a, b) => b.length - a.length);
  const quoted = names.find((n) => new RegExp(`(^|[^A-Za-z0-9_])${n.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}([^A-Za-z0-9_]|$)`).test(failure.message));
  if (quoted) return quoted;
  return failure.code === "invalid_edit" && names.length === 1 && !/\bnotes?\b/i.test(failure.message) ? names[0]! : null;
}

function asDetail(v: unknown): StoDetail | null {
  const d = v as Partial<StoDetail> | null;
  return d && typeof d === "object" && d.file && typeof d.version === "number" && Array.isArray(d.history) ? (d as StoDetail) : null;
}

/** What a failed write means to the person saving. */
export function describeFailure(e: unknown, kind: WriteKind, rep: Representation = "native"): WriteFailure {
  if (e instanceof VersionConflictError) {
    const current = asDetail(e.current);
    return { kind: "conflict", current, message: current ? `${revisionLabel(current.file.current_version)} was saved elsewhere while you edited.` : "This setup changed elsewhere while you edited." };
  }
  if (isAmbiguous(e)) return { kind: "unconfirmed", message: "The server did not confirm the save. Retry sends the same request; it never saves twice." };
  if (e instanceof ApiError) {
    if (e.status === 409 && e.code === "duplicate_name") return { kind: "name", message: "A Coach setup with this name already exists for this car. Choose another name." };
    if (e.status === 409 && e.code === "locked_source") return { kind: "error", message: "Imported originals are locked. Use Save as." };
    if (kind === "save-as" && e.status === 400) return { kind: "name", message: e.message };
    if (e.status === 422 && (e.code === "idempotency_key_reused" || e.code === "idempotency_mismatch")) return { kind: "error", message: "This request was already used for different content. Discard it and save again." };
    if (e.status === 422 && e.code === "garage_profile_mismatch") return { kind: "profile", message: "The garage profile for this file changed. Your values are kept for review; Discard to edit in the current profile." };
    if (e.status === 404 && rep === "garage") return { kind: "unavailable", message: "This server cannot save garage values. Your values are kept; refresh after the server is updated, or Discard." };
    if (e.status === 422 || e.status === 400) return { kind: "rejected", message: e.message, field: fieldOf(e.details), code: e.code };
    if (e.status === 404) return { kind: "error", message: "This setup file or revision no longer exists." };
    return { kind: "error", message: e.message };
  }
  return { kind: "error", message: e instanceof Error ? e.message : String(e) };
}

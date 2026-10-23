import { ApiError, NetworkError, type Schemas, VersionConflictError } from "../core/api";
import { safeStorage } from "../core/store";

/**
 * The `.sto` file library and editor (docs/08-rewrite/contracts/sto-editor.md). Imported files are locked originals; a Coach
 * setup is an editable lineage whose every Save appends an immutable revision. The Python codec owns names, units, groups and
 * what is editable; the UI never converts a native value or invents a limit.
 */
export type StoFile = Schemas["StoFile"];
export type StoDetail = Schemas["StoDetail"];
export type StoField = Schemas["StoField"];
export type StoWrite = Schemas["StoWrite"];
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

/** Edits as the contract's `changes`: native name to the exact text typed, only where it differs from the base spelling. */
export function changesOf(settings: readonly StoField[], edits: Readonly<Record<string, string>>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const s of settings) {
    const v = edits[s.name];
    if (v !== undefined && s.editable && v !== s.raw_value) out[s.name] = v;
  }
  return out;
}

/** A field the user changed whose value in the newer revision differs from the one the user started from. */
export function conflictingEdits(before: readonly StoField[], after: readonly StoField[], changes: Readonly<Record<string, string>>): string[] {
  const was = new Map(before.map((s) => [s.name, s.raw_value]));
  return after.filter((s) => s.name in changes && was.get(s.name) !== s.raw_value).map((s) => s.name);
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

/**
 * A write the server may or may not have committed. It keeps its Idempotency-Key, body and If-Match until a definite answer, so a
 * retry after a dropped connection (or a reload) sends the identical request and the server replays the first result.
 */
export interface PendingWrite {
  v: 1;
  kind: WriteKind;
  customerId: number;
  fileId: string;
  key: string;
  ifMatch: string | null;
  body: StoWrite;
  at: string;
}

const pendingKey = (customerId: number, fileId: string) => `coach.sto.pending.${customerId}.${fileId}`;

export function readPending(customerId: number, fileId: string, storage = safeStorage): PendingWrite | null {
  try {
    const raw = storage?.getItem(pendingKey(customerId, fileId));
    if (!raw) return null;
    const p = JSON.parse(raw) as PendingWrite;
    return p && p.v === 1 && p.customerId === customerId && p.fileId === fileId && typeof p.key === "string" && p.body && typeof p.body === "object" ? p : null;
  } catch {
    return null;
  }
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

/** The same request as a stored one: same action, file, If-Match and body. Only then may its key be reused. */
export function sameRequest(p: PendingWrite | null, kind: WriteKind, customerId: number, fileId: string, ifMatch: string | null, body: StoWrite): p is PendingWrite {
  return !!p && p.kind === kind && p.customerId === customerId && p.fileId === fileId && p.ifMatch === ifMatch && canonical(p.body) === canonical(body);
}

/** Network failures leave the outcome unknown; any answer from the server settles it. */
export const isAmbiguous = (e: unknown): boolean => e instanceof NetworkError || (e instanceof ApiError && (e.status >= 500 || e.status === 408 || e.status === 429));

export type WriteFailure =
  | { kind: "unconfirmed"; message: string }
  | { kind: "conflict"; current: StoDetail | null; message: string }
  | { kind: "name"; message: string }
  | { kind: "rejected"; message: string; field: string | null; code?: string | null }
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
export function describeFailure(e: unknown, kind: WriteKind): WriteFailure {
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
    if (e.status === 422 || e.status === 400) return { kind: "rejected", message: e.message, field: fieldOf(e.details), code: e.code };
    if (e.status === 404) return { kind: "error", message: "This setup file or revision no longer exists." };
    return { kind: "error", message: e.message };
  }
  return { kind: "error", message: e instanceof Error ? e.message : String(e) };
}

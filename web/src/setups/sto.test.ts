import { describe, expect, it } from "vitest";
import { ApiError, NetworkError, VersionConflictError } from "../core/api";
import {
  type PendingWrite,
  type StoDetail,
  type StoField,
  type StoFile,
  changesOf,
  clearPending,
  conflictingEdits,
  describeFailure,
  fieldDomId,
  filesByCar,
  folderOf,
  groupDomId,
  groupFields,
  readPending,
  rejectedField,
  sameRequest,
  sortFiles,
  stoFileFor,
  suggestName,
  viewOf,
  withNewerMetadata,
  writePending,
} from "./sto";

const file = (over: Partial<StoFile>): StoFile => ({
  id: "source:1",
  name: "nascar-xfinity/Bristol.sto",
  car_folder: "nascar-xfinity",
  locked: true,
  current_version: 1,
  version_count: 1,
  sha256: "a".repeat(64),
  updated_at: "2026-10-01T00:00:00Z",
  present: true,
  ...over,
});
const field = (name: string, raw_value: string, editable = true, group = "Chassis"): StoField => ({ name, raw_value, label: name, unit: "mm", editable, group });

function memory() {
  const m = new Map<string, string>();
  return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v), removeItem: (k: string) => void m.delete(k), m };
}

describe("sto library", () => {
  it("names the folder below the car folder and sorts Coach setups before locked originals", () => {
    expect(folderOf(file({ name: "nascar-xfinity/Week 3/race.sto" }))).toBe("Week 3");
    expect(folderOf(file({ name: "nascar-xfinity/race.sto" }))).toBeNull();
    expect(folderOf(file({ name: "race.sto" }))).toBeNull();
    const a = file({ id: "source:a", name: "nascar-xfinity/b.sto" });
    const b = file({ id: "source:b", name: "nascar-xfinity/A.sto" });
    const old = file({ id: "coach:1", locked: false, name: "old.sto", updated_at: "2026-10-01T00:00:00Z" });
    const fresh = file({ id: "coach:2", locked: false, name: "new.sto", updated_at: "2026-10-05T00:00:00Z" });
    expect(sortFiles([a, old, b, fresh]).map((f) => f.id)).toEqual(["coach:2", "coach:1", "source:b", "source:a"]);
  });

  it("keeps every occurrence, including identical bytes at another path or the same path with other bytes", () => {
    const one = file({ id: "source:1" });
    const sameBytes = file({ id: "source:2", name: "nascar-xfinity/Copy/Bristol.sto" });
    const otherBytes = file({ id: "source:3", sha256: "b".repeat(64) });
    const copy = file({ id: "coach:1", locked: false, name: "Bristol copy.sto", sha256: "a".repeat(64) });
    const by = filesByCar([one, sameBytes, otherBytes, copy, file({ id: "source:9", car_folder: "dallara" })], new Map([["nascar-xfinity", "stockcars/xfinity"]]));
    expect(by.get("stockcars/xfinity")?.map((f) => f.id).sort()).toEqual(["coach:1", "source:1", "source:2", "source:3"]);
    expect(by.get("dallara")?.map((f) => f.id)).toEqual(["source:9"]);
  });

  it("finds the library entry an observed setup's file is: same blob, folder and path", () => {
    const files = [file({ id: "source:1" }), file({ id: "source:2", sha256: "b".repeat(64) })];
    const entry = { sha256: "b".repeat(64), car_folder: "NASCAR-Xfinity", relative_path: "nascar-xfinity/bristol.sto", format: "sto", present: true, linked_setup_id: 1 } as never;
    expect(stoFileFor(entry, files)?.id).toBe("source:2");
    expect(stoFileFor({ ...(entry as object), sha256: "c".repeat(64) } as never, files)).toBeNull();
  });

  it("suggests a free Save As name among this car's Coach setups, case-insensitively", () => {
    const files = [file({ id: "coach:1", locked: false, name: "Bristol copy.sto" }), file({ id: "coach:2", locked: false, name: "BRISTOL COPY 2.sto" }), file({ id: "coach:3", locked: false, name: "Bristol copy 3.sto", car_folder: "other" })];
    expect(suggestName("nascar-xfinity/Week 1/Bristol.sto", "nascar-xfinity", files)).toBe("Bristol copy 3.sto");
    expect(suggestName("Road.STO", "nascar-xfinity", files)).toBe("Road copy.sto");
  });
});

describe("sto editing", () => {
  const settings = [field("A", "1.50"), field("B", "2"), field("Locked", "7", false), field("C", "3", true, "Rear")];

  it("sends only real changes of editable fields, spelled exactly as typed", () => {
    expect(changesOf(settings, { A: "1.50", B: "2.0", Locked: "8", C: "3", Missing: "1" })).toEqual({ B: "2.0" });
  });

  it("groups fields in the order the document names them", () => {
    expect(groupFields(settings).map((g) => [g.group, g.fields.map((f) => f.name)])).toEqual([
      ["Chassis", ["A", "B", "Locked"]],
      ["Rear", ["C"]],
    ]);
  });

  it("names the changed fields a newer revision also changed", () => {
    const after = [field("A", "1.60"), field("B", "2"), field("Locked", "7", false), field("C", "4")];
    expect(conflictingEdits(settings, after, { A: "1.55", B: "3" })).toEqual(["A"]);
  });

  it("decides the view from the document, lock and revision", () => {
    const d = (over: Partial<StoDetail>, f: Partial<StoFile> = {}): StoDetail => ({ file: file(f), version: 1, sha256: "", document: { sto_version: 3, sha256: "", internal_car_path: "x", notes: "", settings, warnings: [] }, history: [], unavailable_reason: null, download_url: "", ...over });
    expect(viewOf(d({ document: null, unavailable_reason: "unsupported" }))).toBe("unavailable");
    expect(viewOf(d({}))).toBe("locked");
    expect(viewOf(d({ version: 2 }, { locked: false, current_version: 2 }))).toBe("editable");
    expect(viewOf(d({ version: 1 }, { locked: false, current_version: 2 }))).toBe("history");
  });

  it("finds the rejected parameter in details or in the message, whole names only", () => {
    expect(rejectedField({ kind: "rejected", message: "x", field: "B" }, {})).toBe("B");
    expect(rejectedField({ kind: "rejected", message: "LFColdPressure 400.0 is outside the native range", field: null }, { ColdPressure: "1", LFColdPressure: "400.0" })).toBe("LFColdPressure");
    expect(rejectedField({ kind: "rejected", message: "unsupported build", field: null }, { A: "1" })).toBeNull();
    expect(rejectedField({ kind: "error", message: "A" }, { A: "1" })).toBeNull();
  });

  it("puts the codec's nameless invalid_edit on the one changed value, never on notes or one of several", () => {
    const nameless = { kind: "rejected" as const, message: "Use a finite decimal string for each changed native value.", field: null, code: "invalid_edit" };
    expect(rejectedField(nameless, { lfTireColdPressPa: "165 kPa" })).toBe("lfTireColdPressPa");
    expect(rejectedField(nameless, { A: "x", B: "y" })).toBeNull();
    expect(rejectedField({ ...nameless, message: "Notes exceed the native 16 KiB UTF-16 limit." }, { A: "1" })).toBeNull();
    expect(rejectedField({ ...nameless, code: "unsupported_version" }, { A: "1" })).toBeNull();
  });

  it("gives every native name its own DOM id", () => {
    expect(fieldDomId("future.slot")).not.toBe(fieldDomId("future-slot"));
    expect(fieldDomId("a b")).not.toMatch(/\s/);
    expect(groupDomId("Left front")).toBe("sf-g-Left%20front");
  });

  it("brings a read revision up to a newer answer without taking its document, SHA or download", () => {
    const d = (version: number, current: number, sha: string): StoDetail => ({
      file: file({ id: "coach:2", locked: false, current_version: current, version_count: current }),
      version,
      sha256: sha,
      document: { sto_version: 3, sha256: sha, internal_car_path: "x", notes: "", settings: [field("A", sha)], warnings: [] },
      history: Array.from({ length: current }, (_, i) => ({ version: i + 1, sha256: `s${i + 1}`, created_at: "2026-10-23T00:00:00Z", message: "" })),
      unavailable_reason: null,
      download_url: `/download?version=${version}`,
    });
    const r1 = d(1, 1, "one");
    const saved = d(2, 2, "two");
    const next = withNewerMetadata(r1, saved)!;
    expect(viewOf(r1)).toBe("editable");
    expect(viewOf(next)).toBe("history");
    expect(next).toMatchObject({ version: 1, sha256: "one", download_url: "/download?version=1", document: r1.document });
    expect(next.file.current_version).toBe(2);
    expect(next.history.map((h) => h.version)).toEqual([1, 2]);
    expect(withNewerMetadata(next, saved)).toBeNull();
    expect(withNewerMetadata(saved, r1)).toBeNull();
    expect(withNewerMetadata(r1, { ...saved, file: { ...saved.file, id: "coach:3" } })).toBeNull();
  });
});

describe("sto writes", () => {
  const body = { base_version: 2, changes: { B: "2.0", A: "1.6" }, message: "x" };
  const p: PendingWrite = { v: 1, kind: "save", customerId: 7, fileId: "coach:1", key: "k-1", ifMatch: "2", body, at: "2026-10-23T00:00:00Z" };

  it("persists one pending request per driver and file and reads it back intact", () => {
    const s = memory();
    writePending(p, s);
    expect(readPending(7, "coach:1", s)).toEqual(p);
    expect(readPending(8, "coach:1", s)).toBeNull();
    expect(readPending(7, "coach:2", s)).toBeNull();
    clearPending(7, "coach:1", s);
    expect(readPending(7, "coach:1", s)).toBeNull();
    s.setItem("coach.sto.pending.7.coach:1", "{not json");
    expect(readPending(7, "coach:1", s)).toBeNull();
  });

  it("reuses a key only for the identical request (key order ignored)", () => {
    expect(sameRequest(p, "save", 7, "coach:1", "2", { message: "x", changes: { A: "1.6", B: "2.0" }, base_version: 2 })).toBe(true);
    expect(sameRequest(p, "save", 7, "coach:1", "3", body)).toBe(false);
    expect(sameRequest(p, "save", 7, "coach:1", "2", { ...body, changes: { B: "2.0" } })).toBe(false);
    expect(sameRequest(p, "save-as", 7, "coach:1", "2", body)).toBe(false);
    expect(sameRequest(null, "save", 7, "coach:1", "2", body)).toBe(false);
  });

  it("explains each failure", () => {
    expect(describeFailure(new NetworkError(), "save").kind).toBe("unconfirmed");
    expect(describeFailure(new ApiError(503, "unavailable", "x"), "save").kind).toBe("unconfirmed");
    expect(describeFailure(new ApiError(409, "duplicate_name", "x"), "save-as")).toMatchObject({ kind: "name" });
    expect(describeFailure(new ApiError(400, "invalid_request", "Use one .sto extension"), "save-as")).toMatchObject({ kind: "name", message: "Use one .sto extension" });
    expect(describeFailure(new ApiError(409, "locked_source", "x"), "save").message).toMatch(/Save as/);
    expect(describeFailure(new ApiError(422, "idempotency_key_reused", "x"), "save").message).toMatch(/Discard it/);
    expect(describeFailure(new ApiError(422, "invalid_edit", "A is outside", { name: "A" }), "save")).toEqual({ kind: "rejected", message: "A is outside", field: "A", code: "invalid_edit" });
    const current = { file: file({ id: "coach:1", locked: false, current_version: 4 }), version: 4, history: [], document: null };
    const c = describeFailure(new VersionConflictError(current), "save");
    expect(c).toMatchObject({ kind: "conflict", message: "Revision 4 was saved elsewhere while you edited." });
    expect(describeFailure(new VersionConflictError({}), "save")).toMatchObject({ kind: "conflict", current: null });
  });
});

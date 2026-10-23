import { describe, expect, it } from "vitest";
import { ApiError, NetworkError, VersionConflictError } from "../core/api";
import {
  type Edit,
  type PendingWrite,
  type StoDetail,
  type StoDocument,
  type StoField,
  type StoFile,
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
  filesByCar,
  folderOf,
  garageWritable,
  garageWrite,
  groupDomId,
  groupFields,
  limitsOf,
  profileChanged,
  profileKey,
  readPending,
  rejectedField,
  rowHint,
  sameRequest,
  sortFiles,
  stoFileFor,
  suggestName,
  viewOf,
  withNewerMetadata,
  writePath,
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
const native = (value: string): Edit => ({ rep: "native", value, profile: null });
// Synthetic garage descriptors: exact strings as Python would author them; nothing here is product evidence.
const PROFILE_DOC = { garage_profile_id: "synthetic.test-1", descriptor_version: 2 };
const P1 = profileKey(PROFILE_DOC);
const bias = (raw_value: string, value: string, over: Partial<NonNullable<StoField["garage"]>> = {}): StoField => ({
  name: "fBrakeBias",
  raw_value,
  label: "fBrakeBias",
  unit: "%",
  editable: true,
  group: "Front",
  garage: { label: "Brake bias", value, unit: "%", editable: true, min: "0", max: "100", step: "0.5", step_base: "0", ...over },
});
const pressure: StoField = { name: "lfTireColdPressPa", raw_value: "165474.000000", label: "lfTireColdPressPa", unit: "Pa", editable: true, group: "Left front", garage: { label: "Left front cold pressure", value: "24.0", unit: "psi", editable: false } };
const gears: StoField = {
  name: "gearStackId",
  raw_value: "1.000000",
  label: "gearStackId",
  unit: null,
  editable: true,
  group: "General",
  garage: { label: "Gear stack", value: "1", unit: null, editable: true, choices: [{ value: "1", label: "Short" }, { value: "2", label: "Tall" }] },
};
const garage = (value: string, profile = P1): Edit => ({ rep: "garage", value, profile });

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
    expect(changesOf(settings, { A: native("1.50"), B: native("2.0"), Locked: native("8"), C: native("3"), Missing: native("1") }, false, profileKey(null))).toEqual({ garage: {}, native: { B: "2.0" } });
  });

  it("groups fields in the order the document names them", () => {
    expect(groupFields(settings).map((g) => [g.group, g.fields.map((f) => f.name)])).toEqual([
      ["Chassis", ["A", "B", "Locked"]],
      ["Rear", ["C"]],
    ]);
  });

  it("names the changed fields a newer revision also changed", () => {
    const after = [field("A", "1.60"), field("B", "2"), field("Locked", "7", false), field("C", "4")];
    expect(conflictingEdits(settings, after, { A: native("1.55"), B: native("3") })).toEqual(["A"]);
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

describe("sto garage representation", () => {
  it("shows the backend's garage label, value, unit and limits exactly, and the native key and token as the hint", () => {
    const v = fieldView(bias("54.500000", "54.50"), true);
    expect(v).toMatchObject({ rep: "garage", label: "Brake bias", value: "54.50", unit: "%", editable: true, limits: "0 to 100, step 0.5", inputMode: "decimal" });
    expect(rowHint(bias("54.500000", "54.50"), true)).toBe("fBrakeBias 54.500000");
    // Native rows beside garage rows say so; a native-only document keeps its plain layout.
    expect(rowHint(field("lfCamber", "-0.061087"), true)).toBe("native");
    expect(rowHint(field("lfCamber", "-0.061087"), false)).toBeNull();
    expect(fieldView(field("lfCamber", "-0.061087"), true)).toMatchObject({ rep: "native", value: "-0.061087", unit: "mm", limits: null });
  });

  it("never invents a limit: only supplied constraints, a step only with its origin, a sign-capable keypad unless the minimum rules it out", () => {
    expect(limitsOf({ label: "x", value: "1", unit: null, editable: true })).toBeNull();
    expect(limitsOf({ label: "x", value: "1", unit: null, editable: true, max: "5" })).toBe("max 5");
    expect(limitsOf({ label: "x", value: "1", unit: null, editable: true, step: "0.5" })).toBeNull();
    expect(limitsOf({ label: "x", value: "1", unit: null, editable: true, min: "-4.0", max: "0.0", step: "0.1", step_base: "-0.05" })).toBe("-4.0 to 0.0, step 0.1 from -0.05");
    expect(fieldView(bias("54.500000", "54.50", { min: "-4.0" }), true).inputMode).toBe("text");
    expect(fieldView(bias("54.500000", "54.50", { min: undefined, max: undefined, step: undefined, step_base: undefined }), true)).toMatchObject({ limits: null, inputMode: "text" });
  });

  it("edits only where the backend says the garage view is editable, even when the native value is", () => {
    expect(fieldView(pressure, true)).toMatchObject({ rep: "garage", value: "24.0", unit: "psi", editable: false });
    // No writable profile (none named, or a descriptor version this page does not know): garage rows are read-only.
    expect(fieldView(bias("54.500000", "54.50"), false).editable).toBe(false);
    expect(garageWritable({ sto_version: 3, sha256: "", internal_car_path: "x", notes: "", settings: [], warnings: [], ...PROFILE_DOC })).toBe(true);
    expect(garageWritable({ sto_version: 3, sha256: "", internal_car_path: "x", notes: "", settings: [], warnings: [], garage_profile_id: "p", descriptor_version: 3 })).toBe(false);
    expect(garageWritable({ sto_version: 3, sha256: "", internal_car_path: "x", notes: "", settings: [], warnings: [] })).toBe(false);
  });

  it("offers a choice by its exact value and label", () => {
    const v = fieldView(gears, true);
    expect(v.choices?.map((c) => c.value)).toEqual(["1", "2"]);
    expect(choiceLabel(v, "2")).toBe("Tall");
    expect(choiceLabel(v, "9")).toBe("9");
  });

  it("splits garage and native changes; the base spelling again is no change; nothing is converted", () => {
    const settings = [bias("54.500000", "54.50"), field("rArbDiameter", "0.028600"), pressure, gears];
    let edits = editFor({}, "fBrakeBias", fieldView(settings[0]!, true), "55.0", P1);
    edits = editFor(edits, "rArbDiameter", fieldView(settings[1]!, true), "0.027000", P1);
    edits = editFor(edits, "gearStackId", fieldView(gears, true), "2", P1);
    expect(edits.fBrakeBias).toEqual({ rep: "garage", value: "55.0", profile: P1 });
    expect(changesOf(settings, edits, true, P1)).toEqual({ garage: { fBrakeBias: "55.0", gearStackId: "2" }, native: { rArbDiameter: "0.027000" } });
    expect(editFor(edits, "fBrakeBias", fieldView(settings[0]!, true), "54.50", P1).fBrakeBias).toBeUndefined();
    // "54.5" is not the backend's spelling "54.50": it is sent as typed and Python decides.
    expect(editFor({}, "fBrakeBias", fieldView(settings[0]!, true), "54.5", P1).fBrakeBias?.value).toBe("54.5");
  });

  it("blocks values whose row changed form or profile, keeps them, and never sends them", () => {
    const before = [bias("54.500000", "54.50"), field("rArbDiameter", "0.028600")];
    const edits = { fBrakeBias: garage("55.0"), rArbDiameter: native("0.027000") };
    expect(blockedEdits(before, edits, true, P1)).toEqual([]);
    // The newer revision has no garage view for bias (an unlisted token): the garage value cannot be sent as native.
    const unmapped = [field("fBrakeBias", "54.700000"), field("rArbDiameter", "0.028600")];
    expect(blockedEdits(unmapped, edits, true, P1)).toEqual(["fBrakeBias"]);
    expect(changesOf(unmapped, edits, true, P1)).toEqual({ garage: {}, native: { rArbDiameter: "0.027000" } });
    // Another profile: garage values are blocked, native ones keep their meaning.
    const P2 = profileKey({ garage_profile_id: "synthetic.test-2", descriptor_version: 2 });
    expect(profileChanged(edits, P2)).toBe(true);
    expect(blockedEdits(before, edits, true, P2)).toEqual(["fBrakeBias"]);
    expect(changesOf(before, edits, true, P2).garage).toEqual({});
    // A native value typed before the row gained a garage view is not reinterpreted either.
    expect(blockedEdits([bias("54.500000", "54.50")], { fBrakeBias: native("55.000000") }, true, P1)).toEqual(["fBrakeBias"]);
    expect(blockedEdits([], { gone: native("1") }, true, P1)).toEqual(["gone"]);
  });

  it("marks a conflict when the shown value or its representation changed", () => {
    const edits = { fBrakeBias: garage("56.0") };
    expect(conflictingEdits([bias("54.500000", "54.50")], [bias("55.000000", "55.00")], edits)).toEqual(["fBrakeBias"]);
    expect(conflictingEdits([bias("54.500000", "54.50")], [bias("54.500000", "54.50")], edits)).toEqual([]);
    expect(conflictingEdits([bias("54.500000", "54.50")], [field("fBrakeBias", "54.500000")], edits)).toEqual(["fBrakeBias"]);
  });

  it("builds the garage body with the profile, both maps and no legacy changes", () => {
    const doc = { sto_version: 3, sha256: "", internal_car_path: "x", notes: "", settings: [], warnings: [], ...PROFILE_DOC } as StoDocument;
    const body = garageWrite(2, doc, { garage: { fBrakeBias: "55.0" }, native: { rArbDiameter: "0.027000" } }, { message: "m" });
    expect(body).toEqual({ base_version: 2, garage_profile_id: "synthetic.test-1", descriptor_version: 2, garage_changes: { fBrakeBias: "55.0" }, native_changes: { rArbDiameter: "0.027000" }, message: "m" });
    expect("changes" in body).toBe(false);
    expect(garageWrite(1, { ...doc, descriptor_version: undefined }, { garage: { a: "1" }, native: {} }, {})).toEqual({ base_version: 1, garage_profile_id: "synthetic.test-1", garage_changes: { a: "1" } });
  });
});

describe("sto writes", () => {
  const body = { base_version: 2, changes: { B: "2.0", A: "1.6" }, message: "x" };
  const p: PendingWrite = { v: 2, kind: "save", representation: "native", path: "/api/v1/drivers/7/sto-files/coach%3A1", customerId: 7, fileId: "coach:1", key: "k-1", ifMatch: "2", profile: null, descriptorVersion: null, body, at: "2026-10-23T00:00:00Z" };
  const gBody = { base_version: 2, garage_profile_id: "synthetic.test-1", descriptor_version: 2 as const, garage_changes: { fBrakeBias: "55.0" }, native_changes: { B: "2.0" } };
  const g: PendingWrite = { ...p, representation: "garage", path: "/api/v1/drivers/7/sto-files/coach%3A1/garage", profile: "synthetic.test-1", descriptorVersion: 2, body: gBody };
  const req = (x: PendingWrite) => {
    const { v: _v, key: _k, at: _a, ...rest } = x;
    return rest;
  };

  it("names the exact endpoint for each action and representation", () => {
    expect(writePath("save", "native", 7, "coach:1")).toBe("/api/v1/drivers/7/sto-files/coach%3A1");
    expect(writePath("save-as", "native", 7, "source:a")).toBe("/api/v1/drivers/7/sto-files/source%3Aa/save-as");
    expect(writePath("save", "garage", 7, "coach:1")).toBe("/api/v1/drivers/7/sto-files/coach%3A1/garage");
    expect(writePath("save-as", "garage", 7, "source:a")).toBe("/api/v1/drivers/7/sto-files/source%3Aa/garage/save-as");
  });

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
    writePending(g, s);
    expect(readPending(7, "coach:1", s)).toEqual(g);
  });

  it("reads a version 1 record as the identical native request: same key, body, If-Match and route", () => {
    const s = memory();
    s.setItem("coach.sto.pending.7.coach:1", JSON.stringify({ v: 1, kind: "save", customerId: 7, fileId: "coach:1", key: "k-old", ifMatch: "2", body, at: "2026-10-22T00:00:00Z" }));
    const r = readPending(7, "coach:1", s)!;
    expect(r).toMatchObject({ v: 2, kind: "save", representation: "native", path: "/api/v1/drivers/7/sto-files/coach%3A1", key: "k-old", ifMatch: "2", body, profile: null });
    s.setItem("coach.sto.pending.7.coach:1", JSON.stringify({ v: 1, kind: "save-as", customerId: 7, fileId: "coach:1", key: "k-as", ifMatch: null, body: { ...body, name: "x.sto" }, at: "" }));
    expect(readPending(7, "coach:1", s)?.path).toBe("/api/v1/drivers/7/sto-files/coach%3A1/save-as");
  });

  it("refuses a stored record whose route, representation or profile does not match its body", () => {
    const s = memory();
    const bad = (x: object) => {
      s.setItem("coach.sto.pending.7.coach:1", JSON.stringify(x));
      return readPending(7, "coach:1", s);
    };
    expect(bad({ ...g, path: "/api/v1/drivers/7/sto-files/coach%3A1" })).toBeNull();
    expect(bad({ ...g, representation: "native" })).toBeNull();
    expect(bad({ ...g, profile: "other" })).toBeNull();
    expect(bad({ ...p, path: "/api/v1/drivers/8/sto-files/coach%3A1" })).toBeNull();
    expect(bad({ ...p, v: 3 })).toBeNull();
  });

  it("restores a pending write's values in the representation and profile they were typed in", () => {
    expect(editsOfPending(g)).toEqual({ fBrakeBias: { rep: "garage", value: "55.0", profile: P1 }, B: { rep: "native", value: "2.0", profile: null } });
    expect(editsOfPending(p)).toEqual({ B: native("2.0"), A: native("1.6") });
  });

  it("reuses a key only for the identical request (key order ignored)", () => {
    expect(sameRequest(p, { ...req(p), body: { message: "x", changes: { A: "1.6", B: "2.0" }, base_version: 2 } })).toBe(true);
    expect(sameRequest(p, { ...req(p), ifMatch: "3" })).toBe(false);
    expect(sameRequest(p, { ...req(p), body: { ...body, changes: { B: "2.0" } } })).toBe(false);
    expect(sameRequest(p, { ...req(p), kind: "save-as" })).toBe(false);
    expect(sameRequest(null, req(p))).toBe(false);
    expect(sameRequest(g, req(g))).toBe(true);
    expect(sameRequest(g, { ...req(g), profile: "synthetic.test-2" })).toBe(false);
    expect(sameRequest(g, { ...req(g), representation: "native", path: p.path })).toBe(false);
  });

  it("explains garage failures: a server without the routes, a changed profile, and a named row", () => {
    expect(describeFailure(new ApiError(404, "not_found", "x"), "save", "garage")).toMatchObject({ kind: "unavailable" });
    expect(describeFailure(new ApiError(404, "not_found", "x"), "save", "native")).toMatchObject({ kind: "error" });
    expect(describeFailure(new ApiError(422, "garage_profile_mismatch", "x"), "save", "garage")).toMatchObject({ kind: "profile" });
    expect(describeFailure(new ApiError(422, "garage_off_grid", "Not on the 0.5 step.", { name: "fBrakeBias", representation: "garage" }), "save", "garage")).toEqual({
      kind: "rejected",
      message: "Not on the 0.5 step.",
      field: "fBrakeBias",
      code: "garage_off_grid",
    });
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

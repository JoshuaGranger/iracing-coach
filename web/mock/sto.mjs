// The `.sto` file library of the fixture server (docs/08-rewrite/contracts/sto-editor.md): locked imported originals, Coach
// working setups with immutable revisions, Save (If-Match), Save As, downloads and permanent idempotency receipts.
// Test control through POST /__mock/state: `stoConflictNext` (another device saves a revision first) and `stoDropResponse`
// (the next write commits, then the connection drops before the answer).
import crypto from "node:crypto";

const DRIVER_ID = 1507810;
const SECOND_DRIVER_ID = 2000001;
const BASE = Date.parse("2026-10-20T18:00:00Z");
const MIN = 60000;
const iso = (ms) => new Date(ms).toISOString();
const sha = (text) => crypto.createHash("sha256").update(text).digest("hex");
const baseName = (name) => name.split(/[\\/]/).pop() ?? name;

/** The codec's safety bound on a native number (sto_codec.MAX_NATIVE_MAGNITUDE); not a car adjustment limit. */
const MAX_NATIVE = 1e12;
const NUMBER = /^[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?$/;
const CORNERS = [["lf", "Left front"], ["rf", "Right front"], ["lr", "Left rear"], ["rr", "Right rear"]];

/**
 * What `sto_codec._descriptor` reports for a native record: the label is the native name, the group comes from an lf/rf/lr/rr or
 * f/r prefix, the unit only from a Pa, Npm or ToKg suffix (and rad, % for the two named records). Everything else has no unit:
 * the codec never invents a garage unit or range.
 */
function descriptor(name, raw_value) {
  let group = CORNERS.find(([p]) => name.startsWith(p) && name.length > 2 && /[A-Z]/.test(name[2]))?.[1] ?? "General";
  if (group === "General" && /^[fr][A-Z]/.test(name)) group = name[0] === "f" ? "Front" : "Rear";
  let unit = name.endsWith("Pa") ? "Pa" : name.endsWith("Npm") ? "N/m" : name.endsWith("ToKg") ? "kg" : null;
  if (name === "steeringWheelOffset") unit = "rad";
  else if (name === "fBrakeBias") unit = "%";
  return { name, raw_value, label: name, unit, editable: Math.abs(Number(raw_value)) <= MAX_NATIVE, group };
}

/** 88 synthetic native records in file order, spelled the way the native writer spells numbers (no source file's values). */
const RECORDS = [
  ["fuelFillToKg", "32.000000"],
  ["steeringWheelOffset", "0.000000"],
  ["crossWeightPct", "52.000000"],
  ["steeringRatio", "12.000000"],
  ...CORNERS.flatMap(([p], i) => [
    [`${p}TireColdPressPa`, ["165474.000000", "172369.000000", "158579.000000", "165474.000000"][i]],
    [`${p}SpringRateNpm`, ["105076.000000", "122588.000000", "78807.000000", "87563.000000"][i]],
    [`${p}SpringPerchOffset`, "0.050800"],
    [`${p}RideHeight`, ["0.104140", "0.106680", "0.114300", "0.116840"][i]],
    [`${p}Camber`, ["-0.061087", "0.047997", "-0.008727", "0.008727"][i]],
    [`${p}ToeIn`, "0.000000"],
    [`${p}ShockCompSlow`, "8.000000"],
    [`${p}ShockCompFast`, "4.000000"],
    [`${p}ShockRbdSlow`, "10.000000"],
    [`${p}ShockRbdFast`, "6.000000"],
    [`${p}BumpStopGap`, "0.012700"],
    [`${p}BrakeDuctOpen`, "1.000000"],
    ...(i < 2 ? [[`${p}Caster`, i === 0 ? "0.122173" : "0.157080"]] : []),
  ]),
  ["fBrakeBias", "54.500000"],
  ["fArbDiameter", "0.025400"],
  ["fArbPreload", "0.000000"],
  ["fToeIn", "-0.000873"],
  ["fSplitterHeight", "0.050800"],
  ["fBumpRubberGap", "0.019050"],
  ["fTapeBlanking", "0.000000"],
  ["fBrakeDuctBlanking", "0.000000"],
  ["rArbDiameter", "0.028600"],
  ["rArbArmIndex", "2.000000"],
  ["rTrackBarLeft", "0.254000"],
  ["rTrackBarRight", "0.266700"],
  ["rSpoilerAngle", "0.122173"],
  ["rToeIn", "0.000000"],
  ["rGearRatio", "4.110000"],
  ["rPinionAngle", "-0.034907"],
  ["diffPreloadNm", "75.000000"],
  ["diffRampCoast", "45.000000"],
  ["diffRampDrive", "60.000000"],
  ["diffClutchPlates", "4.000000"],
  ["gearStackId", "1.000000"],
  ["tapeConfiguration", "0.000000"],
  ["radiatorBlanking", "0.000000"],
  ["engineMapId", "1.000000"],
  ["throttleShapeId", "1.000000"],
  ["tcSetting", "0.000000"],
  ["absSetting", "0.000000"],
  ["noseWeightPct", "51.000000"],
  ["ballastFwdOffset", "0.711000"],
  ["brakeMasterCylFront", "0.020637"],
  ["brakeMasterCylRear", "0.022225"],
  ["pitFuelFillToKg", "0.000000"],
  ["unknownFutureParameter", "1e+2"],
  ["opaqueBuildToken", "9007199254740993"],
];

function settings(overrides = {}) {
  return RECORDS.map(([name, value]) => descriptor(name, overrides[name] ?? value));
}

/** The codec's one actionable garage check (sto_codec.WARNINGS). */
const WARNINGS = ["Native file values may differ from garage values. After loading, check the garage and run Tech in iRacing."];

function document(overrides = {}, notes = "") {
  const doc = {
    sto_version: 3,
    sha256: "",
    internal_car_path: "synthetic\\stockcar",
    notes,
    settings: settings(overrides),
    warnings: [...WARNINGS],
    trailer_sha256: sha("synthetic trailer"),
    notes_limit_bytes: 16384,
    native_values: true,
  };
  doc.sha256 = sha(JSON.stringify(doc.settings) + notes);
  return doc;
}

/** An imported occurrence: `name` is its full relative path (car folder included); the id digests driver, folder, path and blob. */
function source(customerId, carFolder, file, blob, at, extra = {}) {
  const path = `${carFolder}/${file}`;
  const id = `source:${sha(JSON.stringify([customerId, carFolder, path, blob]))}`;
  return { id, name: path, car_folder: carFolder, locked: true, present: true, parent_file_id: null, parent_version: null, revisions: [{ version: 1, sha256: blob, created_at: iso(at), message: "Imported original · locked", based_on_version: null, document: extra.document === undefined ? document() : extra.document }], unavailable_reason: extra.unavailable_reason ?? null, ...(extra.present === false ? { present: false } : {}) };
}

export function createSto() {
  const c = DRIVER_ID;
  const files = [
    // Setups 1, 2 and 4 of the fixture library came from these files (same folder, path and blob). Setup 4 is a later copy of
    // "Bristol race v3.sto": the same path with other bytes is its own locked occurrence, and both stay listed.
    source(c, "nascar-xfinity", "Bristol race v3.sto", "1".padStart(64, "a"), BASE - 5 * 86400000, { document: document({}, "Bristol race baseline.\nShort-run balance.") }),
    source(c, "nascar-xfinity", "Bristol race v2.sto", "2".padStart(64, "a"), BASE - 9 * 86400000, { document: document({ crossWeightPct: "51.500000" }) }),
    source(c, "nascar-xfinity", "Bristol race v3.sto", "4".padStart(64, "a"), BASE - 3 * 86400000, { document: document({ lfCamber: "-0.059341" }, "Bristol race baseline.\nShort-run balance.") }),
    ...[1, 2, 3].map((i) => source(c, "nascar-xfinity", `Imported ${i}.sto`, `${i + 39}`.padStart(64, "b"), BASE - (20 + i) * 86400000)),
    source(c, "nascar-xfinity", "Archive/Old build.sto", "9".padStart(64, "c"), BASE - 200 * 86400000, {
      document: null,
      unavailable_reason: "Unsupported STO build: this file was written by a simulator build whose setup encoding is not verified.",
    }),
    source(c, "dallara-ir18", "Road base.sto", "7".padStart(64, "d"), BASE - 30 * 86400000, { present: false }),
  ];
  const parent = files[0];
  const r1 = document({ lfTireColdPressPa: "162000.000000" }, "Bristol race baseline.\nShort-run balance.");
  const r2 = document({ lfTireColdPressPa: "162000.000000", rArbDiameter: "0.027000" }, "Bristol race baseline.\nShort-run balance.");
  files.push({
    id: "coach:1",
    name: "Bristol short-run.sto",
    car_folder: "nascar-xfinity",
    locked: false,
    present: true,
    parent_file_id: parent.id,
    parent_version: 1,
    unavailable_reason: null,
    revisions: [
      { version: 1, sha256: sha(`coach:1/1${JSON.stringify(r1)}`), created_at: iso(BASE - 2 * 86400000), message: "Saved as from Bristol race v3.sto", based_on_version: null, document: r1 },
      { version: 2, sha256: sha(`coach:1/2${JSON.stringify(r2)}`), created_at: iso(BASE - 86400000), message: "Softer rear bar", based_on_version: 1, document: r2 },
    ],
  });
  return { [DRIVER_ID]: { files, nextOwned: 2 }, [SECOND_DRIVER_ID]: { files: [], nextOwned: 1 }, receipts: new Map() };
}

function fileView(f) {
  const last = f.revisions[f.revisions.length - 1];
  return { id: f.id, name: f.name, car_folder: f.car_folder, locked: f.locked, current_version: last.version, version_count: f.revisions.length, sha256: last.sha256, updated_at: last.created_at, present: f.present, parent_file_id: f.parent_file_id, parent_version: f.parent_version };
}

function detail(customerId, f, version) {
  const r = f.revisions[version - 1];
  return {
    file: fileView(f),
    version,
    sha256: r.sha256,
    document: r.document ? structuredClone(r.document) : null,
    history: f.revisions.map(({ document: _d, ...h }) => h),
    unavailable_reason: r.document ? null : f.unavailable_reason,
    download_url: `/api/v1/drivers/${customerId}/sto-files/${f.id}/download?version=${version}`,
  };
}

const RESERVED = /^(con|prn|aux|nul|com[1-9]|lpt[1-9])$/i;
function nameProblem(name) {
  if (typeof name !== "string" || !name) return "Save As requires a filename";
  if (name.length > 120) return "The filename is too long";
  if (/[\\/:*?"<>|]/.test(name) || /[\u0000-\u001f\u007f]/.test(name)) return "The filename cannot contain a folder separator, control character or any of \\ / : * ? \" < > |";
  if (!/\.sto$/i.test(name)) return "The filename must end in .sto";
  const stem = name.slice(0, -4);
  if (/\.sto$/i.test(stem)) return "The filename has .sto twice";
  if (!stem.trim() || /[. ]$/.test(stem) || /^\s/.test(stem)) return "The filename cannot start with a space or end with a dot or space";
  if (RESERVED.test(stem.split(".")[0])) return "That filename is reserved by Windows";
  return null;
}

/**
 * What `sto_codec._validated_changes` rejects, with its messages: they name no parameter, and the server adds no details. Only the
 * codec's safety bound is checked; there is no per-parameter range.
 */
function changeProblem(doc, changes, notes) {
  if (!changes || typeof changes !== "object" || Array.isArray(changes)) return "Changes must be an object of native names and decimal strings.";
  for (const [name, value] of Object.entries(changes)) {
    const s = doc.settings.find((x) => x.name === name);
    if (!s) return "A changed native parameter is absent from this file.";
    if (typeof value !== "string" || value.length > 64 || !NUMBER.test(value)) return "Use a finite decimal string for each changed native value.";
    if (Math.abs(Number(value)) > MAX_NATIVE) return "Changed native number exceeds the codec safety bound.";
    if (Math.abs(Number(s.raw_value)) > MAX_NATIVE) return "This native value is outside the supported editing bound.";
  }
  if (notes !== undefined && (typeof notes !== "string" || notes.length > 8191)) return "Notes exceed the native 16 KiB UTF-16 limit.";
  return null;
}

function applied(doc, changes, notes) {
  const next = structuredClone(doc);
  for (const s of next.settings) if (changes[s.name] !== undefined) s.raw_value = changes[s.name];
  if (notes !== undefined) next.notes = notes;
  next.sha256 = sha(JSON.stringify(next.settings) + next.notes);
  return next;
}

/** Handles `/api/v1/drivers/{c}/sto-files…`; returns false for any other path. */
export async function handleSto(req, res, url, { state, json, err, readBody, sendEvent }) {
  const m = url.pathname.match(/^\/api\/v1\/drivers\/(\d+)\/sto-files(?:\/([^/]+)(?:\/(save-as|download))?)?$/);
  if (!m) return false;
  const c = Number(m[1]);
  const lib = state.sto[c];
  if (!lib) return err(res, 404, "not_found", "No such driver"), true;
  const id = m[2] === undefined ? null : decodeURIComponent(m[2]);
  const action = m[3] ?? null;
  const method = req.method;
  if (id === null) {
    if (method !== "GET") return err(res, 405, "method_not_allowed", "GET only"), true;
    return json(res, 200, { files: lib.files.map(fileView) }), true;
  }
  const file = lib.files.find((f) => f.id === id);
  const version = (f) => {
    const v = url.searchParams.get("version");
    if (v === null) return f.revisions.length;
    return /^[1-9]\d*$/.test(v) && Number(v) <= f.revisions.length ? Number(v) : null;
  };

  if (method === "GET") {
    if (!file) return err(res, 404, "not_found", "no such setup file"), true;
    const v = version(file);
    if (v === null) return err(res, 404, "not_found", "no such revision"), true;
    if (action === "download") {
      const r = file.revisions[v - 1];
      const bytes = Buffer.from(`MOCK-STO\n${JSON.stringify(r.document)}`);
      const stem = baseName(file.name).replace(/\.sto$/i, "");
      const filename = file.locked ? baseName(file.name) : `${stem}.${file.id.replace(":", "-")}.r${v}.sto`;
      res.writeHead(200, { "Content-Type": "application/octet-stream", "Content-Length": bytes.length, "Content-Disposition": `attachment; filename="${filename}"` });
      res.end(bytes);
      return true;
    }
    if (action) return err(res, 405, "method_not_allowed", "GET only"), true;
    return json(res, 200, detail(c, file, v)), true;
  }
  if (method !== "POST" || action === "download") return err(res, 405, "method_not_allowed", "Method not allowed"), true;

  const key = req.headers["idempotency-key"];
  if (!key) return err(res, 400, "invalid_request", "Idempotency-Key is required for Save"), true;
  const body = await readBody(req);
  const ifMatch = req.headers["if-match"] ?? null;
  const kind = action === "save-as" ? "save-as" : "save";
  const requestSha = sha(JSON.stringify([c, id, kind, body ?? null, ifMatch]));
  const receipt = state.sto.receipts.get(key);
  state.writes.push({ path: url.pathname, body, key, ifMatch });
  if (receipt) {
    if (receipt.requestSha !== requestSha) return err(res, 422, "idempotency_key_reused", "This save key was used for different content"), true;
    return json(res, receipt.status, receipt.body), true;
  }
  if (!file) return err(res, 404, "not_found", "no such setup file"), true;
  if (!body || typeof body !== "object" || !Number.isInteger(body.base_version) || body.base_version < 1) return err(res, 400, "invalid_request", "base_version required"), true;

  const settle = (status, result) => {
    state.sto.receipts.set(key, { requestSha, status, body: result });
    sendEvent({ type: "library_changed", customer_id: c, scope: "setups" });
    if (state.config.stoDropResponse) {
      // Committed, but the answer breaks off: the client cannot tell whether the save happened. Part of the response is sent
      // first, because the browser silently resends a request whose reused connection closes before any byte arrives.
      state.config.stoDropResponse = false;
      res.socket.write("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 4096\r\n\r\n{\"file\":");
      setTimeout(() => res.socket?.destroy(), 20);
      return true;
    }
    return json(res, status, result), true;
  };

  if (kind === "save") {
    if (file.locked) return err(res, 409, "locked_source", "Use Save As"), true;
    if (state.config.stoConflictNext) {
      state.config.stoConflictNext = false;
      const last = file.revisions[file.revisions.length - 1];
      const doc = applied(last.document, { fBrakeBias: "55.000000", crossWeightPct: "52.400000" });
      file.revisions.push({ version: last.version + 1, sha256: sha(`${file.id}/${last.version + 1}${JSON.stringify(doc)}`), created_at: new Date().toISOString(), message: "Saved on the rig screen", based_on_version: last.version, document: doc });
    }
    const current = file.revisions.length;
    const expected = ifMatch === null ? null : Number(String(ifMatch).trim().replaceAll('"', ""));
    if (expected !== current) return json(res, 412, { error: "version_conflict", message: "the resource changed; reload and retry", current: detail(c, file, current) }), true;
    const base = file.revisions[body.base_version - 1];
    if (!base) return err(res, 404, "not_found", "no such revision"), true;
    const problem = changeProblem(base.document, body.changes, body.notes);
    if (problem) return err(res, 422, "invalid_edit", problem), true;
    const doc = applied(base.document, body.changes, body.notes);
    const restoring = Object.keys(body.changes).length === 0 && body.notes === undefined && body.base_version !== current;
    file.revisions.push({ version: current + 1, sha256: sha(`${file.id}/${current + 1}${JSON.stringify(doc)}`), created_at: new Date().toISOString(), message: body.message ?? (restoring ? `Restored revision ${body.base_version}` : "Saved"), based_on_version: body.base_version, document: doc });
    return settle(200, detail(c, file, current + 1));
  }

  const base = file.revisions[body.base_version - 1];
  if (!base) return err(res, 404, "not_found", "no such revision"), true;
  if (!base.document) return err(res, 422, "material_unavailable", file.unavailable_reason ?? "The setup codec cannot read this file"), true;
  const problemName = nameProblem(body.name);
  if (problemName) return err(res, 400, "invalid_name", problemName), true;
  const name = `${body.name.slice(0, -4)}.sto`;
  if (lib.files.some((f) => !f.locked && f.car_folder.toLowerCase() === file.car_folder.toLowerCase() && f.name.toLowerCase() === name.toLowerCase())) {
    return err(res, 409, "duplicate_name", "A working setup already has that filename for this car"), true;
  }
  const problem = changeProblem(base.document, body.changes, body.notes);
  if (problem) return err(res, 422, "invalid_edit", problem), true;
  const doc = applied(base.document, body.changes, body.notes);
  const newId = `coach:${lib.nextOwned++}`;
  const created = {
    id: newId,
    name,
    car_folder: file.car_folder,
    locked: false,
    present: true,
    parent_file_id: file.id,
    parent_version: body.base_version,
    unavailable_reason: null,
    revisions: [{ version: 1, sha256: sha(`${newId}/1${JSON.stringify(doc)}`), created_at: new Date().toISOString(), message: body.message ?? `Saved as from ${baseName(file.name)}`, based_on_version: null, document: doc }],
  };
  lib.files.push(created);
  return settle(201, detail(c, created, 1));
}

import type { components, paths as paths_ } from "../gen/api";
import { createStore } from "./store";
import { uuid4 } from "./uuid";

export type Schemas = components["schemas"];

/** The server answered with a non-2xx status. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details: Record<string, unknown> | null = null,
    readonly retryAfter: number | null = null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** 412: the resource changed since `If-Match`; `current` is the server's copy. */
export class VersionConflictError extends Error {
  constructor(readonly current: Record<string, unknown>) {
    super("version_conflict");
    this.name = "VersionConflictError";
  }
}

/** No answer from the server (offline, refused, reset). Writes queued in the outbox wait through it. */
export class NetworkError extends Error {
  constructor(message = "Server unreachable") {
    super(message);
    this.name = "NetworkError";
  }
}

/** True while the last request reached the server. Drives the unreachable banner. */
export const reachable = createStore(true);

let baseUrl = "";
export function setBaseUrl(url: string): void {
  baseUrl = url.replace(/\/$/, "");
}

/** A server path as a link target (downloads the browser fetches itself). */
export const apiUrl = (path: string): string => (path.startsWith("/") ? baseUrl + path : path);

export interface RequestOptions {
  signal?: AbortSignal;
  headers?: Record<string, string>;
  /** `Idempotency-Key` for POST, PUT and DELETE. Generated per call unless the caller persists one (outbox). */
  idempotencyKey?: string;
  /** `If-Match` version of the resource being updated or deleted. */
  ifMatch?: string | null;
}

export interface RawResponse<T> {
  status: number;
  body: T;
  headers: Headers;
}

const isAbort = (e: unknown) => e instanceof DOMException && e.name === "AbortError";

export async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  opts: RequestOptions = {},
): Promise<RawResponse<T>> {
  const headers: Record<string, string> = { Accept: "application/json", ...opts.headers };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (opts.idempotencyKey) headers["Idempotency-Key"] = opts.idempotencyKey;
  if (opts.ifMatch != null) headers["If-Match"] = opts.ifMatch;
  let res: Response;
  try {
    res = await fetch(baseUrl + path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: opts.signal,
    });
  } catch (e) {
    if (isAbort(e)) throw e;
    reachable.set(false);
    throw new NetworkError();
  }
  reachable.set(true);
  let text = "";
  try {
    text = res.status === 204 ? "" : await res.text();
  } catch (e) {
    // The connection broke inside the answer: the request may have been applied, so this is as unknown as no answer at all.
    if (isAbort(e)) throw e;
    throw new NetworkError("The connection broke before the server's answer arrived");
  }
  let parsed: unknown = undefined;
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = undefined;
    }
  }
  if (res.ok) return { status: res.status, body: parsed as T, headers: res.headers };
  const obj = (parsed && typeof parsed === "object" ? parsed : {}) as Record<string, unknown>;
  if (res.status === 412 && obj.error === "version_conflict") {
    throw new VersionConflictError((obj.current as Record<string, unknown>) ?? {});
  }
  const retry = Number(res.headers.get("Retry-After"));
  throw new ApiError(
    res.status,
    typeof obj.error === "string" ? obj.error : `http_${res.status}`,
    typeof obj.message === "string" ? obj.message : res.statusText || `HTTP ${res.status}`,
    (obj.details as Record<string, unknown> | null | undefined) ?? null,
    Number.isFinite(retry) && retry > 0 ? retry : null,
  );
}

export async function getJson<T>(path: string, opts?: RequestOptions): Promise<T> {
  return (await request<T>("GET", path, undefined, opts)).body;
}

type QueryValue = string | number | boolean | null | undefined;

/** Query string; an array repeats its key (`car=a&car=b`), the contract's form for filters that take several values. */
export const qs = (params: Record<string, QueryValue | readonly QueryValue[]>): string => {
  const p = new URLSearchParams();
  const add = (k: string, v: QueryValue) => {
    if (v !== undefined && v !== null && v !== "") p.append(k, String(v));
  };
  for (const [k, v] of Object.entries(params)) {
    if (Array.isArray(v)) for (const item of v) add(k, item);
    else add(k, v as QueryValue);
  }
  const s = p.toString();
  return s ? `?${s}` : "";
};

const enc = encodeURIComponent;

/**
 * A recording belongs to a library: `main` is the driver's own (the default everywhere), `lab` holds the bot-driven and
 * harvest recordings. Lab never enters the driver's counts; asking for it is explicit.
 */
export type Library = "main" | "lab";

/** The query value for a library: only `lab` is sent, so `main` keeps the plain URL (and its cache key) it always had. */
export const libraryQuery = (library: Library | null | undefined): "lab" | undefined => (library === "lab" ? "lab" : undefined);

type SessionsQuery = NonNullable<paths_["/api/v1/drivers/{customer_id}/sessions"]["get"]["parameters"]["query"]>;

/** Paths, so the outbox and the cache agree on keys. */
export const paths = {
  drivers: "/api/v1/drivers",
  status: "/api/v1/status",
  sessions: (c: number, { library, ...q }: Omit<SessionsQuery, "cursor" | "library"> & { cursor?: string | null; library?: Library } = {}) =>
    `/api/v1/drivers/${c}/sessions${qs({ ...q, library: libraryQuery(library) })}`,
  session: (id: number) => `/api/v1/sessions/${id}`,
  sessionAnalysis: (id: number) => `/api/v1/sessions/${id}/analysis`,
  carsAndTracks: (c: number) => `/api/v1/drivers/${c}/cars-and-tracks`,
  setups: (c: number, { library, ...q }: { car_path?: string; q?: string; raced?: boolean; cursor?: string | null; limit?: number; library?: Library } = {}) =>
    `/api/v1/drivers/${c}/setups${qs({ ...q, library: libraryQuery(library) })}`,
  setup: (id: number) => `/api/v1/setups/${id}`,
  setupCompare: "/api/v1/setups/compare",
  stoFiles: (c: number) => `/api/v1/drivers/${c}/sto-files`,
  /** No `version` reads the current revision. */
  stoFile: (c: number, fileId: string, version?: number | null) => `/api/v1/drivers/${c}/sto-files/${enc(fileId)}${qs({ version })}`,
  stoSaveAs: (c: number, fileId: string) => `/api/v1/drivers/${c}/sto-files/${enc(fileId)}/save-as`,
  /** Garage-unit writes: their own routes, so a server without them answers 404 rather than ignoring converted values. */
  stoGarage: (c: number, fileId: string) => `/api/v1/drivers/${c}/sto-files/${enc(fileId)}/garage`,
  stoGarageSaveAs: (c: number, fileId: string) => `/api/v1/drivers/${c}/sto-files/${enc(fileId)}/garage/save-as`,
  baseline: (c: number) => `/api/v1/drivers/${c}/baseline`,
  tuning: (analysisId: string) => `/api/v1/analyses/${enc(analysisId)}/tuning`,
  tuningRecommend: (analysisId: string) => `/api/v1/analyses/${enc(analysisId)}/tuning/recommendations`,
  tuningOutcome: (c: number, experimentId: string) => `/api/v1/drivers/${c}/tuning/experiments/${enc(experimentId)}/outcome`,
  tuningDraft: (c: number, key: string) => `/api/v1/drivers/${c}/tuning-drafts/${enc(key)}`,
  turnAnnotations: (c: number, trackKey: string, mapIdentity: string) =>
    `/api/v1/drivers/${c}/turn-annotations/${enc(trackKey)}/${enc(mapIdentity)}`,
  notes: (c: number, kind: string, subject: string) => `/api/v1/drivers/${c}/notes${qs({ subject_kind: kind, subject_id: subject })}`,
  notesCreate: (c: number) => `/api/v1/drivers/${c}/notes`,
  note: (id: number) => `/api/v1/notes/${id}`,
  traceLayouts: (c: number) => `/api/v1/drivers/${c}/trace-layouts`,
  traceLayout: (c: number, id: string) => `/api/v1/drivers/${c}/trace-layouts/${enc(id)}`,
  liveLayouts: (c: number) => `/api/v1/drivers/${c}/live-layouts`,
  liveLayout: (c: number, id: string) => `/api/v1/drivers/${c}/live-layouts/${enc(id)}`,
  prefs: (c: number) => `/api/v1/drivers/${c}/prefs`,
  job: (id: number) => `/api/v1/jobs/${id}`,
  jobCancel: (id: number) => `/api/v1/jobs/${id}/cancel`,
  liveChannels: "/api/v1/live/channels",
  // View payloads (W8 reads these; ETag "<analysis_id>-p<projection>", Cache-Control: no-cache).
  race: (a: string) => `/api/v1/analyses/${enc(a)}/race`,
  runs: (a: string) => `/api/v1/analyses/${enc(a)}/runs`,
  investigation: (a: string) => `/api/v1/analyses/${enc(a)}/investigation`,
  traceCatalog: (a: string) => `/api/v1/analyses/${enc(a)}/traces/catalog`,
  traces: (a: string, q: { laps?: string; channels?: string } = {}) => `/api/v1/analyses/${enc(a)}/traces${qs(q)}`,
  track: (a: string) => `/api/v1/analyses/${enc(a)}/track`,
};

/** Typed reads. Writes that are user-authored go through the outbox instead (`outbox.enqueue`). */
export const api = {
  drivers: (o?: RequestOptions) => getJson<Schemas["DriverList"]>(paths.drivers, o),
  status: (o?: RequestOptions) => getJson<Schemas["ServerStatus"]>(paths.status, o),
  sessions: (c: number, q?: Parameters<typeof paths.sessions>[1], o?: RequestOptions) =>
    getJson<Schemas["SessionPage"]>(paths.sessions(c, q), o),
  session: (id: number, o?: RequestOptions) => getJson<Schemas["SessionDetail"]>(paths.session(id), o),
  carsAndTracks: (c: number, o?: RequestOptions) => getJson<Schemas["CarTrackOptions"]>(paths.carsAndTracks(c), o),
  setups: (c: number, q?: Parameters<typeof paths.setups>[1], o?: RequestOptions) =>
    getJson<Schemas["SetupPage"]>(paths.setups(c, q), o),
  setup: (id: number, o?: RequestOptions) => getJson<Schemas["SetupDetail"]>(paths.setup(id), o),
  stoFiles: (c: number, o?: RequestOptions) => getJson<Schemas["StoLibrary"]>(paths.stoFiles(c), o),
  stoFile: (c: number, fileId: string, version?: number | null, o?: RequestOptions) => getJson<Schemas["StoDetail"]>(paths.stoFile(c, fileId, version), o),
  /**
   * STO writes are direct requests, never outbox entries: a stale If-Match is never replayed unseen. The caller persists the
   * Idempotency-Key with the body and reuses both on a retry, so an unanswered save is never applied twice.
   */
  saveSto: (c: number, fileId: string, body: Schemas["StoWrite"], ifMatch: string, idempotencyKey: string) =>
    request<Schemas["StoDetail"]>("POST", paths.stoFile(c, fileId), body, { ifMatch, idempotencyKey }),
  saveStoAs: (c: number, fileId: string, body: Schemas["StoWrite"], idempotencyKey: string) =>
    request<Schemas["StoDetail"]>("POST", paths.stoSaveAs(c, fileId), body, { idempotencyKey }),
  saveStoGarage: (c: number, fileId: string, body: Schemas["StoGarageWrite"], ifMatch: string, idempotencyKey: string) =>
    request<Schemas["StoDetail"]>("POST", paths.stoGarage(c, fileId), body, { ifMatch, idempotencyKey }),
  saveStoGarageAs: (c: number, fileId: string, body: Schemas["StoGarageWrite"], idempotencyKey: string) =>
    request<Schemas["StoDetail"]>("POST", paths.stoGarageSaveAs(c, fileId), body, { idempotencyKey }),
  tuning: (analysisId: string, o?: RequestOptions) => getJson<Schemas["TuningContext"]>(paths.tuning(analysisId), o),
  notes: (c: number, kind: Schemas["NoteSubjectKind"], subject: string, o?: RequestOptions) =>
    getJson<{ notes: Schemas["Note"][] }>(paths.notes(c, kind, subject), o),
  liveLayouts: (c: number, o?: RequestOptions) => getJson<Schemas["LiveLayoutList"]>(paths.liveLayouts(c), o),
  prefs: (c: number, o?: RequestOptions) => getJson<Schemas["DriverPrefs"]>(paths.prefs(c), o),
  job: (id: number, o?: RequestOptions) => getJson<Schemas["Job"]>(paths.job(id), o),

  /** Job-starting posts: not queued offline (the answer is the job), but idempotent on retry. */
  queueAnalysis: (sessionId: number) => post<Schemas["JobRef"]>(paths.sessionAnalysis(sessionId)),
  compareSetups: (body: Schemas["SetupCompareRequest"]) =>
    post<Schemas["SetupCompareResult"] | Schemas["JobRef"]>(paths.setupCompare, body),
  findBaseline: (c: number, body: Schemas["BaselineRequest"]) => post<Schemas["JobRef"]>(paths.baseline(c), body),
  recommend: (analysisId: string, body: Schemas["TuningRecommendRequest"]) =>
    post<Schemas["JobRef"]>(paths.tuningRecommend(analysisId), body),
  recordOutcome: (c: number, experimentId: string, body: Schemas["TuningOutcomeRequest"]) =>
    post<Schemas["JobRef"]>(paths.tuningOutcome(c, experimentId), body),
  cancelJob: (id: number) => post<Schemas["JobRef"]>(paths.jobCancel(id)),
};

async function post<T>(path: string, body?: unknown): Promise<RawResponse<T>> {
  return request<T>("POST", path, body, { idempotencyKey: uuid4() });
}

export function isJobRef(v: unknown): v is Schemas["JobRef"] {
  return !!v && typeof v === "object" && "job_id" in v && "kind" in v && "state" in v;
}

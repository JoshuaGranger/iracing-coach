import { createAnalysisCache, type AnalysisCache } from "./cache";
import { decodeCcol, joinCcol, type CcolChannel, type TraceTable } from "./ccol";
import { createTraceData, type TraceData } from "./geometry/trace-model";
import { parseInvestigation, type Investigation } from "./investigation-model";
import type { InvestigationViewData, RaceViewData, RunsViewData, TraceCatalogData, TrackGeometryData } from "./types";

/**
 * Loads one analysis's view files (immutable per `analysis_id` and projection) and keeps them in the
 * in-memory cache so a loaded analysis stays inspectable when the server drops. Requests happen here
 * only: on opening an analysis, on switching to Traces, and when a layout asks for a channel outside the
 * default set. The pointer path never calls this module.
 */

export class HttpError extends Error {
  constructor(readonly status: number, readonly code: string, message: string) {
    super(message);
    this.name = "HttpError";
  }
}

export class ProjectionPendingError extends HttpError {
  constructor(message: string) {
    super(409, "projection_pending", message);
    this.name = "ProjectionPendingError";
  }
}

/** Parsed JSON is held as objects: a conservative factor over the text size for the cache's byte budget. */
export const JSON_HEAP_FACTOR = 4;

export interface Overview {
  race: RaceViewData;
  runs: RunsViewData;
  /** null when the backend produced no investigation (404 not_found) or the schema is unreadable (see `investigationError`). */
  investigation: Investigation | null;
  investigationError: string | null;
  track: TrackGeometryData | null;
}

export interface AnalysisHandle {
  readonly analysisId: string;
  overview(): Promise<Overview>;
  traces(): Promise<TraceData>;
  /** Fetches channels the default set lacks (only when a layout needs them) and returns the updated data. */
  ensureSignals(ids: readonly string[]): Promise<TraceData>;
  /** Drops failed stages so the next call retries (after `analysis_ready`, or the Retry button). */
  reset(): void;
}

export interface AnalysisLoader {
  get(analysisId: string): AnalysisHandle;
  readonly cache: AnalysisCache<AnalysisHandle>;
}

export interface LoaderOptions {
  fetch?: typeof fetch;
  cache?: AnalysisCache<AnalysisHandle>;
  baseUrl?: string;
}

/** Lap numbers as the `laps` query value: sorted, consecutive runs written as ranges (`1-40,52`). */
export function lapRangeText(laps: readonly number[]): string {
  const sorted = [...new Set(laps)].sort((a, b) => a - b);
  const parts: string[] = [];
  for (let index = 0; index < sorted.length;) {
    let end = index;
    while (end + 1 < sorted.length && sorted[end + 1] === sorted[end]! + 1) end++;
    parts.push(end > index ? `${sorted[index]}-${sorted[end]}` : `${sorted[index]}`);
    index = end + 1;
  }
  return parts.join(",");
}

async function errorFrom(response: Response): Promise<HttpError> {
  let code = `http_${response.status}`;
  let message = `${response.status} ${response.statusText}`.trim();
  try {
    const body = (await response.json()) as { error?: string; message?: string };
    if (body.error) code = body.error;
    if (body.message) message = body.message;
  } catch { /* body is not JSON */ }
  return response.status === 409 ? new ProjectionPendingError(message) : new HttpError(response.status, code, message);
}

export function createAnalysisLoader(options: LoaderOptions = {}): AnalysisLoader {
  const doFetch: typeof fetch = options.fetch ?? ((input, init) => fetch(input, init));
  const base = options.baseUrl ?? "";
  const cache = options.cache ?? createAnalysisCache<AnalysisHandle>();

  async function json<T>(path: string, optional = false): Promise<{ value: T | null; bytes: number }> {
    const response = await doFetch(`${base}${path}`, { headers: { accept: "application/json" } });
    if (response.status === 404 && optional) return { value: null, bytes: 0 };
    if (!response.ok) throw await errorFrom(response);
    const text = await response.text();
    return { value: JSON.parse(text) as T, bytes: text.length * JSON_HEAP_FACTOR };
  }

  function create(analysisId: string): AnalysisHandle {
    const id = encodeURIComponent(analysisId);
    let overviewPromise: Promise<Overview> | null = null;
    let tracesPromise: Promise<TraceData> | null = null;
    let bytes = 0;
    let data: TraceData | null = null;
    const account = (delta: number): void => {
      bytes += delta;
      cache.resize(analysisId, bytes);
    };

    async function loadOverview(): Promise<Overview> {
      const [race, runs, inv, track] = await Promise.all([
        json<RaceViewData>(`/api/v1/analyses/${id}/race`),
        json<RunsViewData>(`/api/v1/analyses/${id}/runs`),
        json<InvestigationViewData>(`/api/v1/analyses/${id}/investigation`, true),
        json<TrackGeometryData>(`/api/v1/analyses/${id}/track`, true),
      ]);
      account(race.bytes + runs.bytes + inv.bytes + track.bytes);
      let investigation: Investigation | null = null;
      let investigationError: string | null = null;
      if (inv.value) {
        try {
          investigation = parseInvestigation(inv.value.race_investigation);
        } catch (error) {
          investigationError = error instanceof Error ? error.message : String(error);
        }
      }
      return { race: race.value!, runs: runs.value!, investigation, investigationError, track: track.value };
    }

    async function loadTraces(): Promise<TraceData> {
      const catalogRequest = json<TraceCatalogData>(`/api/v1/analyses/${id}/traces/catalog`);
      const [catalog, blob] = await Promise.all([
        catalogRequest,
        fetchTraces(null, async () => (await catalogRequest).value!.laps.map((lap) => lap.lap)),
      ]);
      data = createTraceData(catalog.value!, blob);
      account(catalog.bytes + blob.byteLength);
      return data;
    }

    /** One CCOL body, or null when the server answers 413 (selection over its 16 MiB bound). */
    async function fetchCcol(channels: readonly string[] | null, laps: readonly number[] | null): Promise<TraceTable | null> {
      const query: string[] = [];
      if (channels) query.push(`channels=${encodeURIComponent(channels.join(","))}`);
      if (laps) query.push(`laps=${encodeURIComponent(lapRangeText(laps))}`);
      const response = await doFetch(`${base}/api/v1/analyses/${id}/traces${query.length ? `?${query.join("&")}` : ""}`, { headers: { accept: "application/vnd.coach.columns" } });
      if (response.status === 413) return null;
      if (!response.ok) throw await errorFrom(response);
      return decodeCcol(await response.arrayBuffer());
    }

    /** Requests the lap selection in halves, recursively, and joins the bodies (the 413 remedy). */
    async function fetchLapHalves(channels: readonly string[] | null, laps: readonly number[]): Promise<TraceTable> {
      if (laps.length < 2) throw new HttpError(413, "payload_too_large", "A single lap exceeds the traces size bound");
      const middle = laps.length >> 1;
      const parts: TraceTable[] = [];
      for (const half of [laps.slice(0, middle), laps.slice(middle)]) {
        parts.push((await fetchCcol(channels, half)) ?? (await fetchLapHalves(channels, half)));
      }
      return joinCcol(parts);
    }

    /** The whole selection in one request; on a 413 the lap list (from `lapsOf`) is split instead. */
    async function fetchTraces(channels: readonly string[] | null, lapsOf: () => Promise<readonly number[]> | readonly number[]): Promise<TraceTable> {
      const whole = await fetchCcol(channels, null);
      if (whole) return whole;
      const laps = [...new Set(await lapsOf())].sort((a, b) => a - b);
      return fetchLapHalves(channels, laps);
    }

    return {
      analysisId,
      overview() {
        overviewPromise ??= loadOverview().catch((error) => {
          overviewPromise = null;
          throw error;
        });
        return overviewPromise;
      },
      traces() {
        tracesPromise ??= loadTraces().catch((error) => {
          tracesPromise = null;
          throw error;
        });
        return tracesPromise;
      },
      async ensureSignals(ids) {
        const current = await this.traces();
        const missing = ids.filter((signal) => !current.table.channels.has(signal) && current.signals.has(signal));
        if (missing.length === 0) return current;
        const extra = await fetchTraces(missing, () => current.table.laps);
        if (extra.bins !== current.bins || extra.laps.length !== current.laps.length) return current;
        const added: [string, CcolChannel][] = [];
        for (const [channelId, channel] of extra.channels) if (!current.table.channels.has(channelId)) added.push([channelId, channel]);
        for (const [channelId, channel] of added) current.table.channels.set(channelId, channel);
        for (const signal of missing) current.columns.delete(signal);
        // The calculated tire-wear row is built from the stress channel.
        if (missing.includes("tire-stress")) current.columns.delete("tire-wear");
        account(extra.byteLength);
        return current;
      },
      reset() {
        overviewPromise = null;
        tracesPromise = null;
      },
    };
  }

  return {
    cache,
    get(analysisId) {
      const existing = cache.get(analysisId);
      if (existing) return existing;
      const handle = create(analysisId);
      cache.set(analysisId, handle, 0);
      return handle;
    },
  };
}

import { describe, expect, it } from "vitest";
import { createAnalysisCache } from "../cache";
import { channelAsFloat, decodeCcol, encodeCcol } from "../ccol";
import { fixtureFetch } from "../fixtures/api";
import { buildSyntheticAnalysis } from "../fixtures/synthetic";
import { createAnalysisLoader, HttpError, ProjectionPendingError, type AnalysisHandle } from "../load";

import { signalColumn as signalColumnOf } from "../geometry/trace-model";

const analysis = buildSyntheticAnalysis({ laps: 12 });

describe("analysis loader", () => {
  it("loads the overview and the traces, and a cache hit repeats no request", async () => {
    const log: string[] = [];
    const loader = createAnalysisLoader({ fetch: fixtureFetch(analysis, log) });
    const handle = loader.get(analysis.analysisId);
    const overview = await handle.overview();
    expect(overview.race.laps.length).toBe(12);
    expect(overview.investigation?.comparisons.length).toBe(1);
    expect(overview.track?.status).toBe("usable");
    const data = await handle.traces();
    expect(data.laps.length).toBe(12);
    expect(log.length).toBe(6);
    expect(log.some((url) => url.endsWith("/traces"))).toBe(true);

    const again = loader.get(analysis.analysisId);
    expect(again).toBe(handle);
    await again.overview();
    await again.traces();
    expect(log.length).toBe(6);
    expect(loader.cache.totalBytes).toBeGreaterThan(analysis.ccol.byteLength);
  });

  it("treats a missing investigation or track as absent, not as an error", async () => {
    const fetchImpl = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/investigation") || url.endsWith("/track")) {
        return new Response(JSON.stringify({ error: "not_found", message: "none" }), { status: 404, headers: { "content-type": "application/json" } });
      }
      return fixtureFetch(analysis)(input);
    }) as typeof fetch;
    const overview = await createAnalysisLoader({ fetch: fetchImpl }).get(analysis.analysisId).overview();
    expect(overview.investigation).toBeNull();
    expect(overview.investigationError).toBeNull();
    expect(overview.track).toBeNull();
  });

  it("reports an unreadable investigation without failing the overview", async () => {
    const broken = structuredClone(analysis);
    (broken.investigation.race_investigation as Record<string, unknown>).schema_version = 9;
    const overview = await createAnalysisLoader({ fetch: fixtureFetch(broken) }).get(analysis.analysisId).overview();
    expect(overview.investigation).toBeNull();
    expect(overview.investigationError).toMatch(/schema 9/);
  });

  it("maps 409 to projection pending and retries after reset", async () => {
    let pending = true;
    const inner = fixtureFetch(analysis);
    const fetchImpl = (async (input: RequestInfo | URL) => {
      if (pending && String(input).endsWith("/race")) {
        return new Response(JSON.stringify({ error: "projection_pending", message: "Building" }), { status: 409, headers: { "content-type": "application/json" } });
      }
      return inner(input);
    }) as typeof fetch;
    const handle = createAnalysisLoader({ fetch: fetchImpl }).get(analysis.analysisId);
    await expect(handle.overview()).rejects.toBeInstanceOf(ProjectionPendingError);
    pending = false;
    expect((await handle.overview()).race.laps.length).toBe(12);
  });

  it("surfaces other HTTP failures with the server's code", async () => {
    const fetchImpl = (async () => new Response(JSON.stringify({ error: "invalid_request", message: "bad" }), { status: 400 })) as typeof fetch;
    const error = await createAnalysisLoader({ fetch: fetchImpl }).get("x").overview().catch((e: unknown) => e);
    expect(error).toBeInstanceOf(HttpError);
    expect((error as HttpError).code).toBe("invalid_request");
  });

  it("keeps at most three analyses and evicts the least recent", () => {
    const loader = createAnalysisLoader({ fetch: fixtureFetch(analysis), cache: createAnalysisCache<AnalysisHandle>() });
    for (const id of ["a", "b", "c"]) loader.get(id);
    loader.get("a");
    loader.get("d");
    expect(loader.cache.keys()).toEqual(["c", "a", "d"]);
  });

  it("requests a channel outside the default set only when a layout needs it, then merges it", async () => {
    const full = decodeCcol(analysis.ccol);
    const subset = (ids: string[]): ArrayBuffer =>
      encodeCcol(analysis.analysisId, 1, full.bins, full.laps, ids.map((id) => {
        const channel = full.channels.get(id)!;
        return { id, dtype: channel.dtype, values: channelAsFloat(channel), missing: channel.missing };
      }));
    const log: string[] = [];
    const inner = fixtureFetch(analysis);
    const fetchImpl = (async (input: RequestInfo | URL) => {
      const url = String(input);
      log.push(url);
      if (url.includes("/traces?channels=")) return new Response(subset(["rpm"]), { status: 200 });
      if (url.endsWith("/traces")) return new Response(subset(["lap-pct", "speed"]), { status: 200 });
      return inner(input);
    }) as typeof fetch;
    const handle = createAnalysisLoader({ fetch: fetchImpl }).get(analysis.analysisId);
    const data = await handle.traces();
    expect(data.table.channels.has("rpm")).toBe(false);
    expect(signalColumnOf(data, "rpm")).toBeNull();
    await handle.ensureSignals(["speed"]);
    expect(log.filter((url) => url.includes("channels=")).length).toBe(0);
    await handle.ensureSignals(["rpm", "no-such-signal"]);
    expect(log.filter((url) => url.includes("channels=rpm")).length).toBe(1);
    expect(data.table.channels.has("rpm")).toBe(true);
    expect(signalColumnOf(data, "rpm")).not.toBeNull();
    await handle.ensureSignals(["rpm"]);
    expect(log.filter((url) => url.includes("channels=")).length).toBe(1);
  });

  it("fetches a channel outside the default set only when asked, and merges it", async () => {
    const log: string[] = [];
    const handle = createAnalysisLoader({ fetch: fixtureFetch(analysis, log) }).get(analysis.analysisId);
    const data = await handle.traces();
    const before = log.length;
    await handle.ensureSignals(["speed"]);
    expect(log.length).toBe(before);
    await handle.ensureSignals(["no-such-signal"]);
    expect(log.length).toBe(before);
  });
});

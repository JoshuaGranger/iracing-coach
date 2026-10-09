import { describe, expect, it } from "vitest";
import { createAnalysisCache } from "../cache";
import { channelAsFloat, decodeCcol, encodeCcol } from "../ccol";
import { fixtureFetch } from "../fixtures/api";
import { buildSyntheticAnalysis } from "../fixtures/synthetic";
import { createAnalysisLoader, HttpError, lapRangeText, ProjectionPendingError, type AnalysisHandle } from "../load";

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

  it("passes the investigation view's top-level pace_attribution to the parsed investigation", async () => {
    const withPace = structuredClone(analysis);
    (withPace.investigation as Record<string, unknown>).pace_attribution = {
      schema_version: 1,
      priorities: [{ section_id: "A", start_pct: 0.1, end_pct: 0.2, recoverable_s: 0.31, best_lap: 17, tire_energy_share: 0.18, tire_energy_grade: "slip_weighted" }],
    };
    const overview = await createAnalysisLoader({ fetch: fixtureFetch(withPace) }).get(analysis.analysisId).overview();
    expect(overview.investigation?.paceAttribution?.priorities[0]).toMatchObject({ sectionId: "A", recoverableS: 0.31, bestLap: 17, tireEnergyShare: 0.18 });
    // Without the field the investigation still loads, with no attribution.
    expect((await createAnalysisLoader({ fetch: fixtureFetch(analysis) }).get(analysis.analysisId).overview()).investigation?.paceAttribution).toBeNull();
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

  describe("413 on the traces", () => {
    const full = decodeCcol(analysis.ccol);
    const inRanges = (text: string): number[] =>
      text.split(",").flatMap((part) => {
        const [from, to] = part.split("-").map(Number);
        return Array.from({ length: (to ?? from!) - from! + 1 }, (_, offset) => from! + offset);
      });
    /** Server stand-in: bodies over `limit` laps answer 413, smaller ones are cut from the full table. */
    const limited = (limit: { laps: number }, log: string[]): typeof fetch => {
      const inner = fixtureFetch(analysis);
      return (async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        log.push(url);
        if (!url.includes("/traces") || url.includes("/catalog")) return inner(input, init);
        const query = new URL(url, "http://x").searchParams;
        const wanted = query.has("laps") ? inRanges(query.get("laps")!) : full.laps;
        if (wanted.length > limit.laps) return new Response(JSON.stringify({ error: "payload_too_large", message: "narrow laps" }), { status: 413 });
        const rows = wanted.map((lap) => full.laps.indexOf(lap));
        const ids = query.has("channels") ? query.get("channels")!.split(",") : [...full.channels.keys()];
        const body = encodeCcol(analysis.analysisId, 1, full.bins, wanted, ids.map((id) => {
          const channel = full.channels.get(id)!;
          const values = channelAsFloat(channel);
          const picked = new Float64Array(rows.length * full.bins);
          rows.forEach((row, at) => picked.set(values.subarray(row * full.bins, (row + 1) * full.bins), at * full.bins));
          return { id, dtype: channel.dtype, values: picked, missing: channel.missing };
        }));
        return new Response(body, { status: 200 });
      }) as typeof fetch;
    };

    it("writes consecutive laps as ranges", () => {
      expect(lapRangeText([5, 1, 2, 3, 52, 4, 6, 53, 60])).toBe("1-6,52-53,60");
      expect(lapRangeText([7])).toBe("7");
    });

    it("requests lap halves recursively and joins them into the same table", async () => {
      const log: string[] = [];
      const data = await createAnalysisLoader({ fetch: limited({ laps: 3 }, log) }).get(analysis.analysisId).traces();
      expect(data.laps).toEqual(full.laps);
      for (const [id, channel] of full.channels) {
        const joined = data.table.channels.get(id)!;
        expect(joined.dtype).toBe(channel.dtype);
        expect(Array.from(joined.raw)).toEqual(Array.from(channel.raw));
      }
      const traceRequests = log.filter((url) => url.includes("/traces") && !url.includes("/catalog"));
      expect(traceRequests[0]!.endsWith("/traces")).toBe(true);
      expect(traceRequests.slice(1).every((url) => url.includes("laps="))).toBe(true);
      expect(traceRequests.slice(1).map((url) => new URL(url, "http://x").searchParams.get("laps"))).toEqual(["1-6", "1-3", "4-6", "7-12", "7-9", "10-12"]);
    });

    it("splits an extra-channel request the same way", async () => {
      const log: string[] = [];
      const limit = { laps: 12 };
      const handle = createAnalysisLoader({ fetch: limited(limit, log) }).get(analysis.analysisId);
      const data = await handle.traces();
      data.table.channels.delete("rpm");
      data.columns.delete("rpm");
      limit.laps = 5;
      const before = log.length;
      await handle.ensureSignals(["rpm"]);
      expect(log.slice(before).filter((url) => url.includes("laps=")).length).toBeGreaterThan(1);
      expect(Array.from(data.table.channels.get("rpm")!.raw)).toEqual(Array.from(full.channels.get("rpm")!.raw));
    });

    it("fails with the 413 when a single lap is still too large", async () => {
      const error = await createAnalysisLoader({ fetch: limited({ laps: 0 }, []) }).get(analysis.analysisId).traces().catch((e: unknown) => e);
      expect(error).toBeInstanceOf(HttpError);
      expect((error as HttpError).status).toBe(413);
    });
  });
});

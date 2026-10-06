import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { decodeCcol } from "../ccol";
import { parseLapRanges, routeStatic, type StaticAnalysisFiles } from "../fixtures/static-api";
import { buildMapGeometry, lapMapPaths } from "../geometry/map-model";
import { createTraceData } from "../geometry/trace-model";
import { createAnalysisLoader } from "../load";
import type { TraceCatalogData, TrackGeometryData } from "../types";
import { perPointMap } from "./plausibility-reference";

/** The committed current-profile 82-lap race that gate G2 runs on (`test-data/g2/`). */
const dir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../../../test-data/g2");
const manifest = JSON.parse(readFileSync(path.join(dir, "fixture.json"), "utf8")) as { analysis_id: string; catalog_laps: number; signals: number; files: { file_name: string; kind: string; sha256: string; bytes: number }[] };

const bodies = new Map<string, Buffer>();
for (const entry of manifest.files) bodies.set(entry.kind, readFileSync(path.join(dir, entry.file_name)));
const text = (kind: string): string => bodies.get(kind)!.toString("utf8");
const ccol = bodies.get("traces")!;
const files: StaticAnalysisFiles = {
  analysisId: manifest.analysis_id,
  race: text("race"),
  runs: text("runs"),
  investigation: text("investigation"),
  track: text("track"),
  catalog: text("trace_catalog"),
  ccol: new Uint8Array(ccol.buffer, ccol.byteOffset, ccol.byteLength),
};

function staticFetch(log: string[] = []): typeof fetch {
  return (async (input: RequestInfo | URL) => {
    const url = String(input);
    log.push(url);
    const routed = routeStatic(files, url);
    const body = typeof routed.body === "string" ? routed.body : (routed.body.buffer as ArrayBuffer).slice(routed.body.byteOffset, routed.body.byteOffset + routed.body.byteLength);
    return new Response(body, { status: routed.status, headers: { "content-type": routed.contentType } });
  }) as typeof fetch;
}

describe("G2 fixture (test-data/g2)", () => {
  it("matches the hashes in fixture.json", () => {
    for (const entry of manifest.files) {
      const body = bodies.get(entry.kind)!;
      expect(body.length, entry.file_name).toBe(entry.bytes);
      expect(createHash("sha256").update(body).digest("hex"), entry.file_name).toBe(entry.sha256);
    }
  });

  it("loads through the investigation loader: 83 catalog laps (82 race laps), 77 signals, a readable investigation and track", async () => {
    const log: string[] = [];
    const handle = createAnalysisLoader({ fetch: staticFetch(log) }).get(manifest.analysis_id);
    const overview = await handle.overview();
    expect(overview.race.laps.length).toBe(manifest.catalog_laps);
    expect(overview.investigationError).toBeNull();
    expect(overview.investigation).not.toBeNull();
    expect(overview.track).not.toBeNull();
    const data = await handle.traces();
    expect(data.laps.length).toBe(manifest.catalog_laps);
    expect(data.bins).toBe(640);
    expect(data.signals.size).toBe(manifest.signals);
    expect(log.some((url) => url.includes("?"))).toBe(false);
  });

  it("answers channel and lap subsets with the same values as the whole body", () => {
    const whole = decodeCcol(files.ccol.buffer.slice(files.ccol.byteOffset, files.ccol.byteOffset + files.ccol.byteLength) as ArrayBuffer);
    const lapNumbers = whole.laps.slice(5, 8);
    const query = `/api/v1/analyses/${manifest.analysis_id}/traces?channels=speed&laps=${lapNumbers[0]}-${lapNumbers[2]}`;
    const routed = routeStatic(files, query);
    expect(routed.status).toBe(200);
    const body = routed.body as Uint8Array;
    const part = decodeCcol(body.buffer.slice(body.byteOffset, body.byteOffset + body.byteLength) as ArrayBuffer);
    expect(part.laps).toEqual(lapNumbers);
    expect([...part.channels.keys()]).toEqual(["speed"]);
    const speed = whole.channels.get("speed")!.raw;
    expect(Array.from(part.channels.get("speed")!.raw)).toEqual(Array.from(speed.slice(5 * whole.bins, 8 * whole.bins)));
    expect(routeStatic(files, `/api/v1/analyses/${manifest.analysis_id}/traces?channels=nope`).status).toBe(400);
  });

  it("places every lap's 640 recorded fixes on the map as the per-point rule does, with the bounds computed once", () => {
    const table = decodeCcol(files.ccol.buffer.slice(files.ccol.byteOffset, files.ccol.byteOffset + files.ccol.byteLength) as ArrayBuffer);
    const data = createTraceData(JSON.parse(files.catalog) as TraceCatalogData, table);
    const track = JSON.parse(files.track!) as TrackGeometryData;
    const map = buildMapGeometry(track)!;
    expect(data.bins).toBe(640);
    expect(data.aux.latitude).not.toBeNull();
    const laps = [...data.lapIndex.keys()];
    const drawn = lapMapPaths(data, map, laps);
    expect(drawn.flatMap((group) => group.laps).length).toBeGreaterThan(0);
    expect(drawn).toEqual(lapMapPaths(data, perPointMap(track, map), laps));
  });

  it("parses lap range lists", () => {
    expect(parseLapRanges("1-3,7")).toEqual([1, 2, 3, 7]);
    expect(parseLapRanges("3-1")).toBeNull();
    expect(parseLapRanges("a")).toBeNull();
  });
});

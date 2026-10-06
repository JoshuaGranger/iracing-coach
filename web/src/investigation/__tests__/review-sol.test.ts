import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { decodeCcol } from "../ccol";
import { chartContentHeight, chartMetrics } from "../cursor/cursor-config";
import { buildSyntheticAnalysis } from "../fixtures/synthetic";
import { buildMapGeometry } from "../geometry/map-model";
import { inRaceStops, passesFilters, pitStops, railDelta, statusChip } from "../geometry/rail-model";
import { createTraceData, fastestLap, isCleanLap, sectorCells, zeroLapFor, type TraceData } from "../geometry/trace-model";
import { InvestigationFormatError, parseInvestigation } from "../investigation-model";
import type { RaceViewData, TraceCatalogData, TrackGeometryData } from "../types";
import { orderedPhases } from "../ui/PhaseSelector";
import { raceFastestLap } from "../ui/RaceView";
import { sectionMapPoints } from "../ui/SectionsView";
import { emptyRowText } from "../ui/TraceChart";
import { spotlightLaps } from "../ui/TracesView";

/** Regression tests for docs/08-rewrite/reviews/web-round1-review-codex-sol.md (finding numbers in the test names). */

const dir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../../../test-data/g2");
const manifest = JSON.parse(readFileSync(path.join(dir, "fixture.json"), "utf8")) as { files: { file_name: string; kind: string }[] };
const body = (kind: string): Buffer => readFileSync(path.join(dir, manifest.files.find((entry) => entry.kind === kind)!.file_name));
const ccol = body("traces");
const catalogText = body("trace_catalog").toString("utf8");

function g2(edit?: (catalog: TraceCatalogData) => void): TraceData {
  const catalog = JSON.parse(catalogText) as TraceCatalogData;
  edit?.(catalog);
  const buffer = ccol.buffer.slice(ccol.byteOffset, ccol.byteOffset + ccol.byteLength) as ArrayBuffer;
  return createTraceData(catalog, decodeCcol(buffer));
}

describe("finding 1: a lap carrying the partial exclusion is partial even without `complete`", () => {
  const data = g2((catalog) => {
    const lap = catalog.laps.find((entry) => entry.lap === 83)! as Record<string, unknown>;
    delete lap.complete;
    expect(lap.exclusion_reasons).toContain("partial");
  });
  const meta = data.lapMeta.get(83)!;

  it("is never the zero lap, never clean, and the rail keeps its Partial chip and no Δ", () => {
    expect(zeroLapFor(data, [6, 83])).toBe(6);
    expect(zeroLapFor(data, [83])).toBeNull();
    expect(isCleanLap(meta, 0)).toBe(false);
    expect(passesFilters(meta, false, true, 0)).toBe(false);
    expect(statusChip(meta)?.kind).toBe("partial");
    expect(railDelta(meta, data.lapMeta.get(6))).toBe("—");
  });
});

describe("finding 2: fallback sectors need session time within two bins of each boundary", () => {
  it("leaves a sector empty when the lap's first 48 bins are missing, instead of a 0.000 s session best", () => {
    const data = g2((catalog) => catalog.laps.forEach((lap) => delete (lap as Record<string, unknown>).sector_times_s));
    const raw = data.aux.sessionTime!.raw as Float64Array;
    const index = data.lapIndex.get(10)!;
    for (let bin = 0; bin < 48; bin++) raw[index * data.bins + bin] = Number.NaN;
    const cell = sectorCells(data).get(10)![0]!;
    expect(cell.seconds).toBeNull();
    expect(cell.state).toBe("na");
    // A complete lap still sums to its lap time within a bin or so.
    const sum = sectorCells(data).get(6)!.reduce((acc, value) => acc + (value.seconds ?? 0), 0);
    expect(Math.abs(sum - (data.lapMeta.get(6)!.lap_time_s as number))).toBeLessThan(0.5);
  });
});

describe("finding 3: rows that cannot fit keep their floor and the chart scrolls", () => {
  it("states the content height of ten rows in a 300 px surface and six rows at a 20 px root", () => {
    for (const [rows, height, unit] of [[10, 300, 16], [6, 260, 20]] as const) {
      const metrics = chartMetrics(800, height, rows, unit);
      const needed = chartContentHeight(rows, unit);
      expect(needed).toBeGreaterThan(height);
      // At the content height every row sits at its floor and ends inside the scrolled area.
      const fitted = chartMetrics(800, needed, rows, unit);
      expect(fitted.rowHeight).toBe(metrics.rowHeight);
      expect(fitted.chartTop + rows * fitted.rowHeight).toBeLessThanOrEqual(needed);
    }
  });
});

describe("finding 5 (round 5, #79): Spotlight offers only the laps drawn, in rail order", () => {
  it("lists the selected laps and no others", () => {
    const data = g2();
    expect(spotlightLaps(data, [], [41, 6, 3]).map((meta) => meta.lap)).toEqual([3, 6, 41]);
    expect(spotlightLaps(data, [], [6]).map((meta) => meta.lap)).toEqual([6]);
    expect(spotlightLaps(data, [], [])).toEqual([]);
  });

  it("drops a lap the moment it leaves the selection and offers none that is not on the chart", () => {
    const data = g2();
    const drawn = [3, 6, 41];
    const offered = new Set(spotlightLaps(data, [], drawn).map((meta) => meta.lap));
    for (const lap of data.laps) expect(offered.has(lap), `L${lap}`).toBe(drawn.includes(lap));
    expect(spotlightLaps(data, [], drawn.filter((lap) => lap !== 41)).map((meta) => meta.lap)).toEqual([3, 6]);
  });
});

describe("finding 6: the Sections map places labels and its cursor only from lap fractions", () => {
  it("offers no placement points when the main path carries no fractions", () => {
    const track = JSON.parse(body("track").toString("utf8")) as TrackGeometryData;
    const ready = buildMapGeometry(track)!;
    expect(sectionMapPoints(ready).length).toBe(ready.points.length);
    const stripped = JSON.parse(body("track").toString("utf8")) as TrackGeometryData & { main_path: { pct?: unknown[] } };
    stripped.main_path.pct = (stripped.main_path.pct ?? []).map(() => null);
    const guessed = buildMapGeometry(stripped)!;
    expect(guessed.sectionsReady).toBe(false);
    expect(sectionMapPoints(guessed)).toEqual([]);
  });
});

describe("finding 8: a failed channel request says Failed, not Loading", () => {
  it("names the row Failed for a failed channel and Loading while it is only missing", () => {
    const data = g2();
    data.table.channels.delete("gear");
    expect(emptyRowText(data, { row_id: "g", signals: ["gear"] }, 6)).toBe("Loading");
    expect(emptyRowText(data, { row_id: "g", signals: ["gear"] }, 6, ["gear"])).toBe("Failed");
  });
});

describe("finding 9: Race and Traces mark the same fastest lap", () => {
  it("takes the clean rule on the unmodified synthetic race", () => {
    const synthetic = buildSyntheticAnalysis();
    const traces = createTraceData(synthetic.catalog, decodeCcol(synthetic.ccol));
    const inv = parseInvestigation(synthetic.investigation.race_investigation);
    expect(raceFastestLap(inv.laps, synthetic.race)).toBe(fastestLap(traces));
    // Without the race view the investigation's laps give the same answer.
    expect(raceFastestLap(inv.laps, null)).toBe(fastestLap(traces));
  });
});

describe("finding 10: post-run service is not an in-race stop", () => {
  it("keeps the flag and leaves the stop out of the rail and chart markers", () => {
    const race = {
      laps: [],
      strategy: null,
      pit_stops: [
        { run_number: 1, lap: 41, service_s: 13, tires_changed: ["LF", "LR", "RF", "RR"], fuel_added_gal: 12, post_run_service: false },
        { run_number: 2, lap: 82, service_s: 20, tires_changed: [], fuel_added_gal: 0, post_run_service: true },
      ],
    } as unknown as RaceViewData;
    const stops = pitStops(race, { runs: [] }, null);
    expect(stops.map((stop) => [stop.lap, stop.postRun])).toEqual([[41, false], [82, true]]);
    expect(inRaceStops(stops).map((stop) => stop.lap)).toEqual([41]);
    const fallback = pitStops({ laps: [], strategy: null } as unknown as RaceViewData, { runs: [{ run_number: 2, lap_numbers: [80, 81, 82], end_lap: 82, ended_with_post_run_service: true, pit_service: { start_time: 1, end_time: 9 } }] }, null);
    expect(fallback[0]!.postRun).toBe(true);
  });
});

describe("finding 11: missing screening fields fail clearly", () => {
  const raw = (): Record<string, unknown> => JSON.parse(body("investigation").toString("utf8")).race_investigation as Record<string, unknown>;
  const firstPair = (inv: Record<string, unknown>): Record<string, unknown> | null => {
    for (const comparison of inv.comparisons as Record<string, unknown>[]) {
      const sections = ((comparison.inspection as Record<string, unknown> | undefined)?.sections ?? {}) as Record<string, Record<string, unknown>>;
      const first = Object.values(sections)[0];
      if (first) return first;
    }
    return null;
  };

  it("throws without schema_version or a comparison's pace_comparable", () => {
    const noSchema = raw();
    delete noSchema.schema_version;
    expect(() => parseInvestigation(noSchema)).toThrow(InvestigationFormatError);
    const noPace = raw();
    delete (noPace.comparisons as Record<string, unknown>[])[0]!.pace_comparable;
    expect(() => parseInvestigation(noPace)).toThrow(/pace_comparable/);
  });

  it("throws without a pass's eligible or the pair's steering comparable", () => {
    const noEligible = raw();
    const pair = firstPair(noEligible);
    expect(pair).not.toBeNull();
    delete (pair!.a as Record<string, unknown>).eligible;
    expect(() => parseInvestigation(noEligible)).toThrow(/eligible/);
    const noSteering = raw();
    delete (firstPair(noSteering)!.steering as Record<string, unknown>).comparable;
    expect(() => parseInvestigation(noSteering)).toThrow(/comparable/);
  });
});

describe("finding 14: a failed duplicate phase never hides an openable one", () => {
  const phases = [
    { sessionId: 1, sessionType: "Practice", state: "failed" },
    { sessionId: 2, sessionType: "Practice", state: "ready" },
    { sessionId: 3, sessionType: "Race", state: "ready" },
  ];

  it("takes the openable practice, and the current session when it is one of the pair", () => {
    expect(orderedPhases(phases, 3).map((phase) => phase.sessionId)).toEqual([2, 3]);
    expect(orderedPhases(phases, 1).map((phase) => phase.sessionId)).toEqual([1, 3]);
    expect(orderedPhases([phases[0]!, { sessionId: 4, sessionType: "Practice", state: "none" }, phases[2]!], 3).map((phase) => phase.sessionId)).toEqual([1, 3]);
  });
});

import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { fixtureFetch } from "../fixtures/api";
import { buildSectionsAnalysis } from "../fixtures/sections";
import { routeStatic, type StaticAnalysisFiles } from "../fixtures/static-api";
import { createAnalysisLoader } from "../load";
import type { RaceViewData } from "../types";
import { contextTitle, greenText, lapContexts, lapGreenExposure } from "../ui/section-context";
import {
  APPROACH_MAX_FRACTION,
  SECTION_ROWS,
  SECTION_SIGNALS,
  gapReference,
  gapValues,
  passPair,
  sectionBins,
  sectionLocal,
  sectionRows,
  sectionWindow,
  sessionTimeColumn,
  splitSectionPath,
  windowPctAt,
  windowPosition,
  windowSpan,
} from "../ui/section-traces-model";

// Gap ledger G6: the Sections chart gains the time gap to the median lap and body slip, starts at the backend's approach (dashed),
// and draws the fastest and slowest laps (the pass pair) thick. Lap rows' green exposure comes from the race view's `run_age_laps`.
// Every figure is a trace bin or view field as reported; the gap is the Traces delta row's arithmetic. No entry-speed figure is
// computed in the browser (Phase 1 review: backend authority); the dashed approach shows entry speed.

async function loadSynthetic() {
  const analysis = buildSectionsAnalysis("an-g6");
  const loader = createAnalysisLoader({ fetch: fixtureFetch(analysis) });
  return { analysis, data: await loader.get("an-g6").ensureSignals(SECTION_SIGNALS) };
}

/** The committed current-profile race (`test-data/g2/`), real recorded channels. */
async function loadG2() {
  const dir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../../../test-data/g2");
  const manifest = JSON.parse(readFileSync(path.join(dir, "fixture.json"), "utf8")) as { analysis_id: string; files: { file_name: string; kind: string }[] };
  const body = (kind: string) => readFileSync(path.join(dir, manifest.files.find((file) => file.kind === kind)!.file_name));
  const ccol = body("traces");
  const files: StaticAnalysisFiles = {
    analysisId: manifest.analysis_id,
    race: body("race").toString("utf8"),
    runs: body("runs").toString("utf8"),
    investigation: body("investigation").toString("utf8"),
    track: body("track").toString("utf8"),
    catalog: body("trace_catalog").toString("utf8"),
    ccol: new Uint8Array(ccol.buffer, ccol.byteOffset, ccol.byteLength),
  };
  const fetcher = (async (input: RequestInfo | URL) => {
    const routed = routeStatic(files, String(input));
    const out = typeof routed.body === "string" ? routed.body : (routed.body.buffer as ArrayBuffer).slice(routed.body.byteOffset, routed.body.byteOffset + routed.body.byteLength);
    return new Response(out, { status: routed.status, headers: { "content-type": routed.contentType } });
  }) as typeof fetch;
  const loader = createAnalysisLoader({ fetch: fetcher });
  return { files, data: await loader.get(files.analysisId).ensureSignals(SECTION_SIGNALS) };
}

describe("the approach window", () => {
  it("is the backend's lead-in: the section's length clamped to 3-8 % lap, before the section start", () => {
    // A 9.5 % section: 8 % lead-in.
    const b = sectionWindow({ startPct: 0.27, endPct: 0.365 });
    expect(b.approachPct).toBeCloseTo(0.19, 9);
    expect(b.boundary).toBeCloseTo(0.08 / 0.175, 9);
    // A 2 % section: 3 % lead-in.
    expect(sectionWindow({ startPct: 0.5, endPct: 0.52 }).approachPct).toBeCloseTo(0.47, 9);
    // A 5 % section: its own length.
    expect(sectionWindow({ startPct: 0.5, endPct: 0.55 }).approachPct).toBeCloseTo(0.45, 9);
  });

  it("never reaches back past the previous section's start or the recorded lap start", () => {
    expect(sectionWindow({ startPct: 0.3, endPct: 0.4, previousStartPct: 0.25 }).approachPct).toBeCloseTo(0.25, 9);
    expect(sectionWindow({ startPct: 0.04, endPct: 0.2 }).approachPct).toBe(0);
    expect(sectionWindow({ startPct: 0, endPct: 0.2 }).boundary).toBe(0);
  });

  it("maps the view's position across the section to the window and back; the approach is negative", () => {
    const w = sectionWindow({ startPct: 0.27, endPct: 0.365 });
    expect(windowPosition(0, w)).toBeCloseTo(w.boundary, 9);
    expect(windowPosition(1, w)).toBeCloseTo(1, 9);
    expect(sectionLocal(windowPosition(0.4, w), w)).toBeCloseTo(0.4, 9);
    expect(sectionLocal(0, w)).toBeLessThan(0);
    expect(windowPctAt(0, w)).toBeCloseTo(0.19, 9);
    expect(windowPctAt(w.boundary, w)).toBeCloseTo(0.27, 9);
    expect(windowPctAt(1, w)).toBeCloseTo(0.365, 9);
    expect(APPROACH_MAX_FRACTION).toBe(0.08);
  });

  it("splits a lap's line exactly at the boundary, on the drawn segment between the two bins around it", () => {
    // Bins at 0, 0.2, 0.4, 0.6, 0.8, 1 across the window; the boundary at 0.5 falls between 0.4 and 0.6.
    const x = Float64Array.from([0, 0.2, 0.4, 0.6, 0.8, 1]);
    const values = Float64Array.from([1, 2, 3, 5, 5, 6]);
    const frame = { plotLeft: 0, plotWidth: 100, top: 0, plotHeight: 58 };
    const range = { min: 0, max: 10 };
    const { approach, section } = splitSectionPath(values, x, 0.5, range, frame);
    const points = (d: string) => d.split(" ").map((part) => part.slice(1).split(",").map(Number));
    const a = points(approach);
    const b = points(section);
    // The approach ends and the section starts at x = 50 px (the boundary), at the value 4 halfway between 3 and 5.
    expect(a[a.length - 1]![0]).toBe(50);
    expect(b[0]![0]).toBe(50);
    expect(a[a.length - 1]).toEqual(b[0]);
    expect(b[0]![1]).toBeCloseTo(58 - 4 - (4 / 10) * 50, 1);
    expect(a).toHaveLength(4);
    expect(b).toHaveLength(4);
    // Nothing of the approach lies past the boundary, nothing of the section before it.
    expect(a.every(([px]) => px! <= 50)).toBe(true);
    expect(b.every(([px]) => px! >= 50)).toBe(true);
    // A boundary on a bin cuts there; no approach before a window start; a missing bin leaves each side at its own bins.
    expect(points(splitSectionPath(values, x, 0.4, range, frame).section)[0]![0]).toBe(40);
    expect(splitSectionPath(values, x, 0, range, frame).approach).toBe("");
    const gap = Float64Array.from([1, 2, Number.NaN, 5, 5, 6]);
    const split = splitSectionPath(gap, x, 0.5, range, frame);
    expect(points(split.approach).every(([px]) => px! <= 20)).toBe(true);
    expect(points(split.section)[0]![0]).toBe(60);
  });
});

describe("six rows", () => {
  it("speed, gap to the median lap, throttle, brake, steering and body slip, over the window's bins", async () => {
    const { data } = await loadSynthetic();
    const w = sectionWindow({ startPct: 0.42, endPct: 0.58 });
    const bins = sectionBins(data.x, windowSpan(w));
    const laps = [data.laps[3]!, data.laps[8]!, data.laps[20]!];
    const rows = sectionRows(data, bins, laps, laps[2]!);
    expect(rows.map((row) => row.id)).toEqual([...SECTION_ROWS]);
    expect(rows.map((row) => row.name)).toEqual(["Speed", "Gap to median", "Throttle", "Brake", expect.any(String), "Body slip"]);
    for (const row of rows) expect(row.values).toHaveLength(3);
    // The median lap's own gap is zero everywhere it is timed; the others start at zero at the window's first bin.
    const gap = rows[1]!;
    expect(gap.values[2]!.every((value) => value === 0)).toBe(true);
    expect(gap.values[0]![0]).toBe(0);
    expect(gap.range.min).toBe(-gap.range.max);
    const slip = rows[5]!;
    expect(slip.range.min).toBe(-slip.range.max);
    expect(slip.signal?.id).toBe("slip");
  });

  it("the gap is the Traces delta arithmetic, anchored at the window: session time since the first common bin, less the median's", async () => {
    const { data } = await loadSynthetic();
    const time = sessionTimeColumn(data)!;
    const w = sectionWindow({ startPct: 0.42, endPct: 0.58 });
    const { bins } = sectionBins(data.x, windowSpan(w));
    const lap = data.lapIndex.get(data.laps[5]!)!;
    const ref = data.lapIndex.get(data.laps[11]!)!;
    const gap = gapValues(time, lap, ref, bins, data.bins);
    const t = (l: number, i: number) => time[l * data.bins + bins[i]!]!;
    const last = bins.length - 1;
    expect(gap[last]!).toBeCloseTo((t(lap, last) - t(lap, 0)) - (t(ref, last) - t(ref, 0)), 9);
    // No median lap: an empty row, never another reference.
    expect(gapValues(time, lap, null, bins, data.bins).every(Number.isNaN)).toBe(true);
    const rows = sectionRows(data, sectionBins(data.x, windowSpan(w)), [data.laps[5]!], null);
    expect(rows[1]!.signal).toBeNull();
    expect(rows[1]!.values[0]!.every(Number.isNaN)).toBe(true);
  });

  it("missing session time breaks the gap where either lap lacks it, and re-anchors at the first common bin", () => {
    const time = Float64Array.from([Number.NaN, 10, 11, 12, 100, 101.5, 103, 104]);
    const gap = gapValues(time, 0, 1, [0, 1, 2, 3], 4);
    expect(Number.isNaN(gap[0]!)).toBe(true);
    expect([gap[1], gap[2], gap[3]].map((v) => Number(v!.toFixed(6)))).toEqual([0, -0.5, -0.5]);
  });
});

describe("the gap reference", () => {
  it("is the reported median lap also when it is one of the listed laps; else the drawn median line", () => {
    const listed = [58, 4, 12, 27, 55, 53].map((lap) => ({ lap, median: false }));
    const has = (lap: number) => lap !== 99;
    // Median L12 among the six: no grey line is drawn, but it stays the reference.
    expect(gapReference(12, listed, has)).toBe(12);
    expect(gapReference(40, [...listed, { lap: 40, median: true }], has)).toBe(40);
    // Not in the store: the drawn median line, or nothing.
    expect(gapReference(99, [...listed, { lap: 40, median: true }], has)).toBe(40);
    expect(gapReference(99, listed, has)).toBeNull();
    expect(gapReference(null, listed, has)).toBeNull();
  });
});

describe("the pass pair", () => {
  it("is the fastest and the slowest drawn lap (first and last in list order), never the median", () => {
    const laps = [58, 4, 3, 27, 55, 53].map((lap) => ({ lap, median: false }));
    expect([...passPair([...laps, { lap: 40, median: true }])]).toEqual([58, 53]);
    expect([...passPair([{ lap: 4, median: false }, { lap: 40, median: true }])]).toEqual([4]);
    expect(passPair([{ lap: 40, median: true }]).size).toBe(0);
  });
});

describe("green exposure on the lap rows", () => {
  it("is the race view's run_age_laps, in the row's hover after the tires and the traffic", () => {
    const race = { laps: [{ lap: 4, run_age_laps: 3.5, clean_context: { traffic_proximity_fraction: 0.75, traffic_side: "behind" } }, { lap: 5, run_age_laps: -1 }, { lap: 6 }] } as unknown as RaceViewData;
    expect([...lapGreenExposure(race)]).toEqual([[4, 3.5]]);
    const contexts = lapContexts(race, [{ lap_numbers: [4, 5, 6], tire_set_lifecycle: { corners: { RR: { green_laps_on_set: 12 } } } }]);
    expect(contexts.get(4)!.green).toBe(3.5);
    expect(contexts.get(5)!.green).toBeNull();
    expect(contextTitle(contexts.get(4))).toBe("10 laps on tires · traffic 75 % of lap · behind · green lap 4 of the run");
    expect(greenText(3.5)).toBe("4");
    expect(greenText(null)).toBe("—");
  });
});

describe("real recorded channels (test-data/g2)", () => {
  it("carries session time and body slip in the default channels; slip is recorded and signed", async () => {
    const { data } = await loadG2();
    expect(sessionTimeColumn(data)).not.toBeNull();
    const w = sectionWindow({ startPct: 0.27, endPct: 0.365 });
    const bins = sectionBins(data.x, windowSpan(w));
    const rows = sectionRows(data, bins, [10, 11, 12, 30], 20);
    const slip = rows.find((row) => row.id === "slip")!;
    const values = slip.values.flatMap((series) => [...series]).filter(Number.isFinite);
    expect(values.length).toBeGreaterThan(bins.bins.length * 3);
    expect(Math.max(...values.map(Math.abs))).toBeGreaterThan(0.1);
    expect(rows.every((row) => row.signal !== null)).toBe(true);
  });

  it("the gap over nearly a whole lap matches the two laps' recorded lap times", async () => {
    const { data, files } = await loadG2();
    const race = JSON.parse(files.race) as { laps: { lap: number; lap_time_s?: number }[] };
    const lapTime = new Map(race.laps.map((lap) => [lap.lap, lap.lap_time_s]));
    const time = sessionTimeColumn(data)!;
    const { bins } = sectionBins(data.x, { startPct: 0, endPct: 0.9999 });
    for (const [a, b] of [[12, 20], [30, 55], [60, 70]] as const) {
      const gap = gapValues(time, data.lapIndex.get(a)!, data.lapIndex.get(b)!, bins, data.bins);
      const end = gap[gap.length - 1]!;
      // First to last bin centre of each lap: the bins miss half a bin at each end, so the gap agrees within a few hundredths.
      expect(Math.abs(end - (lapTime.get(a)! - lapTime.get(b)!))).toBeLessThan(0.05);
    }
  });
});

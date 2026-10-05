import { describe, expect, it } from "vitest";
import { cardMetrics, chartMetrics } from "../cursor/cursor-config";
import { fixtureFetch } from "../fixtures/api";
import { buildSectionsAnalysis } from "../fixtures/sections";
import type { MapPoint } from "../geometry/map-model";
import { createAnalysisLoader } from "../load";
import { parseSectionLaps } from "../ui/section-laps";
import {
  SECTION_SIGNALS,
  acrossSection,
  buildSectionCursorConfig,
  lapPctAt,
  projectOnSection,
  sectionBins,
  sectionPath,
  sectionPointAt,
  sectionRange,
  sectionTrackPoints,
  sectionValues,
  tracedLaps,
} from "../ui/section-traces-model";
import { mapPixel, mapUnits } from "../ui/SectionsView";

// Round 4, S3 (backlog #64): the Sections view draws the chosen laps' speed, throttle, brake and steering over the section with the
// Traces cursor. The model only picks the section's bins and places them; every value is a trace bin as reported.

async function loadFixture() {
  const analysis = buildSectionsAnalysis("an-s3");
  const log: string[] = [];
  const loader = createAnalysisLoader({ fetch: fixtureFetch(analysis, log) });
  const data = await loader.get("an-s3").ensureSignals(SECTION_SIGNALS);
  return { analysis, log, loader, data };
}

const SECTION_B = { startPct: 0.42, endPct: 0.58 };

/** A quarter circle from (0, 0) around to (10, 10), lap fractions 0..1 around a loop of eight points. */
const LOOP: MapPoint[] = Array.from({ length: 8 }, (_, i) => {
  const angle = (i / 8) * Math.PI * 2;
  return { percent: i / 8, x: 10 * Math.cos(angle), y: 10 * Math.sin(angle) };
});

describe("section bins", () => {
  it("keeps the bins whose lap fraction lies in the section, in driving order, placed across it", async () => {
    const { data } = await loadFixture();
    const { bins, x } = sectionBins(data.x, SECTION_B);
    expect(bins.length).toBeGreaterThan(10);
    for (const bin of bins) expect(data.x[bin]!).toBeGreaterThanOrEqual(0.42 - 1e-9);
    for (const bin of bins) expect(data.x[bin]!).toBeLessThanOrEqual(0.58 + 1e-9);
    expect([...bins]).toEqual([...bins].sort((a, b) => a - b));
    expect(x[0]!).toBeGreaterThanOrEqual(0);
    expect(x[x.length - 1]!).toBeLessThanOrEqual(1);
    for (let i = 1; i < x.length; i++) expect(x[i]!).toBeGreaterThan(x[i - 1]!);
    expect(x[0]!).toBeCloseTo((data.x[bins[0]!]! - 0.42) / 0.16, 9);
  });

  it("follows a section across start/finish in driving order", () => {
    const lapX = Float64Array.from({ length: 20 }, (_, i) => (i + 0.5) / 20);
    const { bins, x } = sectionBins(lapX, { startPct: 0.9, endPct: 0.1 });
    expect(bins).toEqual([18, 19, 0, 1]);
    expect([...x].map((v) => Number(v.toFixed(3)))).toEqual([0.125, 0.375, 0.625, 0.875]);
    expect(acrossSection(0.95, { startPct: 0.9, endPct: 0.1 })).toBeCloseTo(0.25, 9);
    expect(lapPctAt(0.75, { startPct: 0.9, endPct: 0.1 })).toBeCloseTo(0.05, 9);
  });
});

describe("the section segment on the map", () => {
  const points = sectionTrackPoints(LOOP, { startPct: 0.1, endPct: 0.4 });

  it("is an open line from the section start (0) to its end (1)", () => {
    expect(points[0]!.percent).toBe(0);
    expect(points[points.length - 1]!.percent).toBe(1);
    expect(points.map((p) => p.percent)).toEqual([...points.map((p) => p.percent)].sort((a, b) => a - b));
    // The ends are the loop's points at the section boundaries.
    const start = points[0]!;
    expect(Math.hypot(start.x - 10 * Math.cos(0.1 * Math.PI * 2), start.y - 10 * Math.sin(0.1 * Math.PI * 2))).toBeLessThan(1.5);
  });

  it("places the marker at the end at 1, never back at the start", () => {
    const end = sectionPointAt(points, 1)!;
    const last = points[points.length - 1]!;
    expect(end.x).toBeCloseTo(last.x, 9);
    expect(end.y).toBeCloseTo(last.y, 9);
    const start = sectionPointAt(points, 0)!;
    expect(start.x).toBeCloseTo(points[0]!.x, 9);
  });

  it("projects a pointer to the nearest point of the segment; the chord between its ends is not part of it", () => {
    const middle = sectionPointAt(points, 0.5)!;
    expect(projectOnSection(points, middle.x, middle.y)).toBeCloseTo(0.5, 6);
    const first = points[0]!;
    const last = points[points.length - 1]!;
    // Just inside the arc from the chord's middle: the nearest segment point, well away from both ends.
    const chord = { x: (first.x + last.x) / 2, y: (first.y + last.y) / 2 };
    const local = projectOnSection(points, chord.x, chord.y);
    expect(local).toBeGreaterThan(0.2);
    expect(local).toBeLessThan(0.8);
  });

  it("converts map units to element pixels and back under preserveAspectRatio meet", () => {
    const box = [-10, -5, 40, 20] as const;
    const { px, py } = mapPixel(box, 400, 400, 10, 5);
    expect(px).toBeCloseTo(200, 9);
    expect(py).toBeCloseTo(200, 9);
    const back = mapUnits(box, 400, 400, px, py);
    expect(back.x).toBeCloseTo(10, 9);
    expect(back.y).toBeCloseTo(5, 9);
  });
});

describe("drawn laps", () => {
  it("are the selected subset when any is selected, else the six; the median lap joins when the view reports it", () => {
    const listed = [58, 4, 3, 27, 55, 53];
    expect(tracedLaps(listed, new Set(), 40)).toEqual({ laps: listed, median: 40 });
    expect(tracedLaps(listed, new Set([53, 4]), 40)).toEqual({ laps: [4, 53], median: 40 });
    expect(tracedLaps(listed, new Set(), null)).toEqual({ laps: listed, median: null });
    expect(tracedLaps(listed, new Set([9]), null).laps).toEqual(listed);
  });

  it("reads the optional median lap and keeps it null when absent", () => {
    const raw = { sections: [{ section_id: "A", clean_lap_count: 5, median_s: 6.1, median_lap: 12, fastest: [], slowest: [] }, { section_id: "B", clean_lap_count: 0, median_s: null, fastest: [], slowest: [] }] };
    const laps = parseSectionLaps(raw)!;
    expect(laps.get("A")!.medianLap).toBe(12);
    expect(laps.get("B")!.medianLap).toBeNull();
  });
});

describe("telemetry over the section", () => {
  it("loads the trace store once; later sections and selections request nothing", async () => {
    const { log, loader } = await loadFixture();
    const first = log.length;
    expect(log.some((url) => url.includes("/traces/catalog"))).toBe(true);
    await loader.get("an-s3").ensureSignals(SECTION_SIGNALS);
    await loader.get("an-s3").ensureSignals(SECTION_SIGNALS);
    expect(log.length).toBe(first);
  });

  it("draws each lap's reported values at the section's bins, pedals on 0–1 and steering about zero", async () => {
    const { data } = await loadFixture();
    const bins = sectionBins(data.x, SECTION_B);
    const lapIndices = data.laps.slice(0, 6).map((lap) => data.lapIndex.get(lap)!);
    expect(sectionRange(data, "throttle", lapIndices, bins.bins)).toEqual({ min: 0, max: 1 });
    expect(sectionRange(data, "brake", lapIndices, bins.bins)).toEqual({ min: 0, max: 1 });
    const steer = sectionRange(data, "steering", lapIndices, bins.bins);
    expect(steer.min).toBe(-steer.max);
    const speed = sectionRange(data, "speed", lapIndices, bins.bins);
    expect(speed.max).toBeGreaterThan(speed.min);
    const values = sectionValues(data, "speed", lapIndices[0]!, bins.bins);
    expect(values.length).toBe(bins.bins.length);
    expect(values.every(Number.isFinite)).toBe(true);
    const d = sectionPath(values, bins.x, speed, { plotLeft: 6, plotWidth: 400, top: 0, plotHeight: 100 });
    expect(d.startsWith("M")).toBe(true);
    expect(d.split(/[ML]/).filter(Boolean)).toHaveLength(bins.bins.length);
    // A missing value breaks the line.
    const gap = Float64Array.from(values);
    gap[3] = Number.NaN;
    expect(sectionPath(gap, bins.x, speed, { plotLeft: 6, plotWidth: 400, top: 0, plotHeight: 100 }).match(/M/g)).toHaveLength(2);
  });

  it("builds the shared cursor over the section: four rows, one trace per drawn lap in card order, x across the section", async () => {
    const { data } = await loadFixture();
    const bins = sectionBins(data.x, SECTION_B);
    const laps = [data.laps[4]!, data.laps[2]!, data.laps[9]!];
    const lapIndices = laps.map((lap) => data.lapIndex.get(lap)!);
    const metrics = chartMetrics(600, 560, SECTION_SIGNALS.length);
    const config = buildSectionCursorConfig({
      data, bins, metrics, initialFraction: 0.5, key: "k",
      ranges: SECTION_SIGNALS.map((id) => sectionRange(data, id, lapIndices, bins.bins)),
      laps: [...laps.map((lap) => ({ lap, color: "rgb(61, 220, 132)" })), { lap: 99999, color: "grey" }],
    });
    expect(config.rows.map((row) => row.rowId)).toEqual(["speed", "throttle", "brake", "steering"]);
    expect(config.rows.every((row) => row.signals.length === 1)).toBe(true);
    expect(config.traces.map((trace) => trace.lap)).toEqual(laps);
    expect(config.bins).toBe(bins.bins.length);
    expect(config.x).toBe(bins.x);
    expect(config.trackPoints).toEqual([]);
    expect(config.card).toEqual(cardMetrics(metrics.unit));
    const speedRow = config.traces[0]!.rowValues[0]![0]!;
    expect(speedRow.length).toBe(bins.bins.length);
    expect(Array.from(speedRow)).toEqual(Array.from(sectionValues(data, "speed", lapIndices[0]!, bins.bins)));
  });
});

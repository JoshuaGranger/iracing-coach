import { describe, expect, it } from "vitest";
import { decodeCcol } from "../ccol";
import { buildCursorConfig, chartMetrics } from "../cursor/cursor-config";
import {
  buildMapGeometry,
  isCanonicalMainLoop,
  lapMapPaths,
  metricRibbon,
  seriesPoints,
  type MapGeometry,
} from "../geometry/map-model";
import {
  aggregateSeries,
  bestThree,
  chartPathGroups,
  createTraceData,
  deltaColumn,
  fastestLap,
  hasSignalData,
  hasUsableLapTime,
  isComparable,
  isCleanLap,
  isGreenLap,
  referenceLapFor,
  signalColumn,
  signalRange,
  smoothedValue,
  type TraceData,
} from "../geometry/trace-model";
import { buildSyntheticAnalysis } from "../fixtures/synthetic";

const analysis = buildSyntheticAnalysis();
function load(): TraceData {
  return createTraceData(analysis.catalog, decodeCcol(analysis.ccol));
}

describe("trace model over the synthetic 82-lap analysis", () => {
  const data = load();

  it("decodes 82 laps x 160 bins and a bucket-centre lap-fraction grid", () => {
    expect(data.laps.length).toBe(82);
    expect(data.bins).toBe(160);
    expect(data.x[0]).toBeCloseTo(0.5 / 160, 12);
    expect(data.x[159]).toBeCloseTo(159.5 / 160, 12);
  });

  it("places bin i at (i + 0.5) / bins when the table has no lap fraction channel", () => {
    const catalog = { ...analysis.catalog, signals: analysis.catalog.signals.filter((signal) => signal.source_key !== "lap_pct") };
    const bare = createTraceData(catalog, decodeCcol(analysis.ccol));
    expect(bare.x[0]).toBeCloseTo(0.5 / 160, 12);
    expect(bare.x[159]).toBeCloseTo(159.5 / 160, 12);
    expect(Array.from(bare.x)).toEqual(Array.from(data.x));
  });

  it("converts steering radians to degrees and leaves other units alone", () => {
    const steering = signalColumn(data, "steering")!;
    const raw = data.table.channels.get("steering")!.raw as Float64Array;
    expect(steering[10]).toBeCloseTo((raw[10]! * 180) / Math.PI, 12);
    expect(signalColumn(data, "speed")).toBe(data.table.channels.get("speed")!.raw);
    expect(signalColumn(data, "no-such-signal")).toBeNull();
  });

  it("smooths neighbours with the 0.16 / 0.10 weights and passes step signals through", () => {
    const column = Float64Array.from([0, 1, 0, 0, 0, 0]);
    expect(smoothedValue(column, 0, 1, 6, "speed", false)).toBeCloseTo(0.68 / 1, 10);
    expect(smoothedValue(column, 0, 1, 6, "throttle", false)).toBeCloseTo(0.8, 10);
    expect(smoothedValue(column, 0, 1, 6, "gear", true)).toBe(1);
    expect(smoothedValue(Float64Array.from([2, Number.NaN, 4]), 0, 0, 3, "speed", false)).toBe(2);
    const edge = smoothedValue(Float64Array.from([1, 3]), 0, 0, 2, "speed", false);
    expect(edge).toBeCloseTo((1 * 0.68 + 3 * 0.16) / 0.84, 10);
  });

  it("ranges follow the C# rules per signal", () => {
    const all = data.laps.map((_, i) => i);
    expect(signalRange(data, "throttle", all, null)).toEqual({ min: 0, max: 1 });
    const steering = signalRange(data, "steering", all, null);
    expect(steering.min).toBe(-steering.max);
    expect(steering.max).toBeGreaterThanOrEqual(1 / 12);
    const speed = signalRange(data, "speed", all, null);
    expect(speed.min).toBeLessThan(speed.max);
    expect(signalRange(data, "delta", all, 1).max).toBeGreaterThanOrEqual(0.05);
  });

  it("time delta is zero on the reference lap and uses the fastest clean selected lap", () => {
    const context = { incidentPoints: new Map<number, number>() };
    const picked = bestThree(data, context);
    expect(picked.length).toBe(3);
    const reference = referenceLapFor(data, picked)!;
    expect(picked).toContain(reference);
    const delta = deltaColumn(data, reference)!;
    const index = data.lapIndex.get(reference)!;
    for (let bin = 0; bin < 160; bin += 20) expect(delta[index * 160 + bin]).toBeCloseTo(0, 12);
    const other = data.lapIndex.get(picked.find((lap) => lap !== reference)!)!;
    expect(Number.isFinite(delta[other * 160 + 80]!)).toBe(true);
    expect(fastestLap(data, context)).toBe(picked[0]);
  });

  it("default selection skips laps the backend marks not comparison-eligible or incomplete", () => {
    const context = { incidentPoints: new Map<number, number>() };
    // Work on a copy of the lap records so the shared fixture stays intact.
    const copy = (): TraceData => ({ ...data, lapMeta: new Map([...data.lapMeta].map(([lap, meta]) => [lap, { ...meta }] as const)) });
    const local = copy();
    const eligible = data.laps.filter((lap) => isComparable(local.lapMeta.get(lap)!) && hasUsableLapTime(local.lapMeta.get(lap)));
    expect(eligible.length).toBeGreaterThan(5);
    const [partialLap, pitLap] = [eligible[0]!, eligible[1]!];
    // Both would win on time alone: a 12.8 s partial final lap and a quick lap with backend exclusion reasons.
    Object.assign(local.lapMeta.get(partialLap)!, { lap_time_s: 12.833, complete: false, comparison_eligible: false, exclusion_reasons: ["partial"] });
    Object.assign(local.lapMeta.get(pitLap)!, { lap_time_s: 13.5, comparison_eligible: false, exclusion_reasons: ["pit"] });
    expect(isComparable(local.lapMeta.get(partialLap)!)).toBe(false);
    expect(isComparable(local.lapMeta.get(pitLap)!)).toBe(false);
    const picked = bestThree(local, context);
    expect(picked.length).toBe(3);
    for (const lap of picked) expect(isComparable(local.lapMeta.get(lap)!)).toBe(true);
    expect(picked).not.toContain(partialLap);
    expect(picked).not.toContain(pitLap);
    expect([partialLap, pitLap]).not.toContain(fastestLap(local, context));
    // Still selectable by hand: the lap stays in the data with its trace row.
    expect(local.lapIndex.has(partialLap)).toBe(true);
    expect(data.lapMeta.get(partialLap)!.complete).not.toBe(false);

    // An incomplete lap alone is enough to leave the default selection, even if flagged eligible.
    const incompleteOnly = copy();
    Object.assign(incompleteOnly.lapMeta.get(partialLap)!, { lap_time_s: 12.833, complete: false });
    expect(bestThree(incompleteOnly, context)).not.toContain(partialLap);

    // When the backend marks no lap comparable the complete laps stand in (Traces still opens); a partial lap never does.
    const none = copy();
    for (const meta of none.lapMeta.values()) Object.assign(meta, { comparison_eligible: false, exclusion_reasons: ["caution_or_mixed"] });
    Object.assign(none.lapMeta.get(partialLap)!, { lap_time_s: 12.833, complete: false, exclusion_reasons: ["partial"] });
    const fallback = bestThree(none, context);
    expect(fallback.length).toBe(3);
    expect(fallback).not.toContain(partialLap);
    expect(fastestLap(none, context)).not.toBe(partialLap);
    for (const lap of fallback) expect(none.lapMeta.get(lap)!.complete).not.toBe(false);
  });

  it("screens clean and green laps from the lap record", () => {
    const caution = data.lapMeta.get(42)!;
    expect(isGreenLap(caution)).toBe(false);
    expect(isCleanLap(data.lapMeta.get(10)!, 0)).toBe(true);
    expect(isCleanLap(data.lapMeta.get(10)!, 2)).toBe(false);
    expect(isCleanLap(data.lapMeta.get(28)!, 0)).toBe(false);
  });

  it("bounds the chart DOM by colour groups, not by lap count", () => {
    const all = data.laps;
    const frame = { plotLeft: 6, plotWidth: 400, top: 6, plotHeight: 80 };
    const range = signalRange(data, "speed", all.map((_, i) => i), null);
    const groups = chartPathGroups(data, "speed", 0, all, null, null, frame, range);
    expect(groups.length).toBeLessThanOrEqual(21);
    expect(groups.reduce((sum, group) => sum + group.laps.length, 0)).toBe(82);
    const focused = chartPathGroups(data, "speed", 0, all, 40, null, frame, range);
    expect(focused.some((group) => group.spotlight && group.laps.join() === "40")).toBe(true);
    expect(focused[focused.length - 1]!.spotlight).toBe(true);
    // 82 laps at a 400 px plot: 500 points per background lap but only 160 bins exist.
    const points = groups[0]!.d.split(/[ML]/).filter(Boolean).length;
    expect(points).toBeLessThanOrEqual(160 * groups[0]!.laps.length);
  });

  it("builds a cursor config bounded by the detailed-trace budget", () => {
    const metrics = chartMetrics(900, 600, 6);
    const rows = [{ row_id: "a", signals: ["speed"] }, { row_id: "b", signals: ["delta"] }, { row_id: "c", signals: ["slip", "yaw"] }, { row_id: "d", signals: ["not-recorded"] }];
    const config = buildCursorConfig({ data, rows, selectedLaps: data.laps, spotlightLap: 12, metrics, trackPoints: [], initialFraction: 0.25, key: "k" });
    expect(config.logicalTraceCount).toBe(82);
    expect(config.traces.length).toBe(24);
    expect(config.traces[0]!.lap).toBe(12);
    expect(config.rows[3]!.signals.length).toBe(0);
    expect(config.rows[2]!.signals.map((signal) => signal.id)).toEqual(["slip", "yaw"]);
    expect(config.aggregate.speed.length).toBe(160);
    expect(config.tooltipCapacity).toBe(Math.floor((metrics.rowHeight - 18) / 20));
    expect(config.distanceStrip).toBe(true);
    expect(hasSignalData(data, "speed", [0], null)).toBe(true);
    expect(hasSignalData(data, "tire-wear", [0], null)).toBe(false);
  });

  it("aggregates every selected lap at each bin", () => {
    const series = aggregateSeries(data, [0, 1], null);
    const speed = signalColumn(data, "speed")!;
    expect(series.speed[5]).toBeCloseTo((speed[5]! + speed[160 + 5]!) / 2, 12);
  });
});

describe("map geometry", () => {
  const map = buildMapGeometry(analysis.track) as MapGeometry;

  it("accepts the synthetic loop and scales it into the 420x280 box", () => {
    expect(isCanonicalMainLoop(seriesPoints(analysis.track.main_path))).toBe(true);
    expect(map).not.toBeNull();
    const xs = map.points.map((p) => p.x);
    const ys = map.points.map((p) => p.y);
    expect(Math.min(...xs)).toBeGreaterThanOrEqual(35 - 1e-6);
    expect(Math.max(...xs)).toBeLessThanOrEqual(385 + 1e-6);
    expect(Math.min(...ys)).toBeGreaterThanOrEqual(31 - 1e-6);
    expect(Math.max(...ys)).toBeLessThanOrEqual(249 + 1e-6);
    expect(map.startFinish).not.toBeNull();
    expect(map.ribbonPath.endsWith("Z")).toBe(true);
  });

  it("rejects an open loop, a too-small span and unavailable geometry", () => {
    const open = seriesPoints({ pct: [0, 0.25, 0.5, 0.75], x: [0, 1, 1, 0.9], y: [0, 0, 1, 1] });
    expect(isCanonicalMainLoop(open)).toBe(false);
    expect(isCanonicalMainLoop(seriesPoints({ pct: [0, 0.3, 0.6], x: [0, 0.05, 0.1], y: [0, 0.05, 0] }))).toBe(false);
    expect(buildMapGeometry({ ...analysis.track, status: "unavailable" })).toBeNull();
    expect(buildMapGeometry(null)).toBeNull();
  });

  it("places recorded laps on the loop through the geometry transform", () => {
    const data = load();
    const paths = lapMapPaths(data, map, [1, 2, 3]);
    expect(paths.length).toBeGreaterThan(0);
    const projected = map.projectGps(analysis.track.transform!.source_bounds.maximum_y - 0.5 * analysis.track.transform!.normalization_scale, analysis.track.transform!.source_bounds.minimum_x + 0.5 * analysis.track.transform!.normalization_scale);
    expect(projected).not.toBeNull();
    expect(map.projectGps(0, 0)).toBeNull();
    const ribbon = metricRibbon(data, map, "speed", [1, 2, 3]);
    expect(ribbon.length).toBe(160);
    expect(ribbon[0]!.color).toMatch(/^rgb\(/);
    expect(metricRibbon(data, map, "stress", [1]).length).toBe(160);
  });
});

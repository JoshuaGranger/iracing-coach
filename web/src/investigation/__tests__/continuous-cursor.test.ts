import { describe, expect, it } from "vitest";
import { decodeCcol } from "../ccol";
import { buildCursorConfig, chartMetrics } from "../cursor/cursor-config";
import { binPosition, nearerBin, valueAt } from "../cursor/cursor-core";
import { trackFieldTexts, trackFieldTextsAt } from "../cursor/telemetry-cursor";
import { lapTracePath, monotoneRun, stepRun, tracePathGroups } from "../cursor/trace-path";
import { buildSyntheticAnalysis } from "../fixtures/synthetic";
import { createTraceData, plotColumn, signalColumn, signalRange } from "../geometry/trace-model";

/** Backlog #77: a continuous cursor over 640 bins, read between measured bins, and lines through those bins. */

const dense = buildSyntheticAnalysis({ laps: 8, bins: 640, analysisId: "dense-8" });
const data = createTraceData(dense.catalog, decodeCcol(dense.ccol));

describe("cursor position on the bin grid", () => {
  const grid = Float64Array.from({ length: 640 }, (_, bin) => (bin + 0.5) / 640);

  it("finds the two measured bins around a fraction and how far between them", () => {
    const position = binPosition(grid, (100.5 + 0.25) / 640);
    expect(position.lower).toBe(100);
    expect(position.upper).toBe(101);
    expect(position.t).toBeCloseTo(0.25, 9);
    expect(nearerBin(position)).toBe(100);
    expect(nearerBin(binPosition(grid, (100.5 + 0.75) / 640))).toBe(101);
  });

  it("sits exactly on a bin centre with t 0", () => {
    const position = binPosition(grid, grid[200]!);
    expect(position).toEqual({ lower: 200, upper: 201, t: 0 });
  });

  it("holds the end bins outside the grid: never beyond a measured point", () => {
    expect(binPosition(grid, 0)).toEqual({ lower: 0, upper: 0, t: 0 });
    expect(binPosition(grid, 1)).toEqual({ lower: 639, upper: 639, t: 0 });
    expect(binPosition(grid, Number.NaN)).toEqual({ lower: 0, upper: 0, t: 0 });
    expect(binPosition(new Float64Array(0), 0.5).lower).toBe(-1);
  });

  it("moves continuously: 40 positions inside one bin give 40 distinct values", () => {
    const speed = signalColumn(data, "speed")!;
    const seen = new Set<string>();
    for (let step = 0; step < 40; step++) {
      const fraction = (300.5 + step / 40) / 640;
      seen.add(valueAt(speed, binPosition(grid, fraction), false, 2 * 640).toFixed(9));
    }
    expect(seen.size).toBe(40);
  });
});

describe("values between measured bins", () => {
  const values = Float64Array.from([10, 20, Number.NaN, 40, 3, 4]);

  it("interpolates linearly between two measured neighbours", () => {
    expect(valueAt(values, { lower: 0, upper: 1, t: 0.25 })).toBeCloseTo(12.5, 12);
    expect(valueAt(values, { lower: 3, upper: 4, t: 0.5 })).toBeCloseTo(21.5, 12);
  });

  it("never leaves the range of its two neighbours", () => {
    for (let t = 0; t <= 1; t += 0.05) {
      const value = valueAt(values, { lower: 3, upper: 4, t });
      expect(value).toBeGreaterThanOrEqual(3);
      expect(value).toBeLessThanOrEqual(40);
    }
  });

  it("keeps a gap a gap: the nearer bin's value, or missing", () => {
    expect(valueAt(values, { lower: 1, upper: 2, t: 0.3 })).toBe(20);
    expect(valueAt(values, { lower: 1, upper: 2, t: 0.7 })).toBeNaN();
    expect(valueAt(values, { lower: 2, upper: 3, t: 0.7 })).toBe(40);
  });

  it("reads a step channel at the nearer bin, never between two gears", () => {
    const gears = Float64Array.from([3, 4]);
    expect(valueAt(gears, { lower: 0, upper: 1, t: 0.49 }, true)).toBe(3);
    expect(valueAt(gears, { lower: 0, upper: 1, t: 0.51 }, true)).toBe(4);
  });

  it("reads a lap's bins at its offset in a lap-major column", () => {
    const column = Float64Array.from([1, 2, 3, 100, 200, 300]);
    expect(valueAt(column, { lower: 1, upper: 2, t: 0.5 }, false, 3)).toBe(250);
  });

  it("interpolates the map readouts too", () => {
    const aggregate = {
      speed: Float64Array.from([100, 110]),
      throttle: Float64Array.from([0.5, 0.7]),
      brake: Float64Array.from([0, 0]),
      delta: Float64Array.from([0.1, 0.2]),
    };
    expect(trackFieldTextsAt(aggregate, { lower: 0, upper: 1, t: 0.5 })).toEqual({ speed: "105.0", throttle: "60", brake: "0", delta: "+0.150" });
    expect(trackFieldTexts(aggregate, 1)).toEqual({ speed: "110.0", throttle: "70", brake: "0", delta: "+0.200" });
  });
});

/** Points of a cubic Bezier from (x0, y0) through controls to (x3, y3). */
function bezier(p: number[], t: number): [number, number] {
  const [x0, y0, x1, y1, x2, y2, x3, y3] = p as [number, number, number, number, number, number, number, number];
  const u = 1 - t;
  return [
    u * u * u * x0 + 3 * u * u * t * x1 + 3 * u * t * t * x2 + t * t * t * x3,
    u * u * u * y0 + 3 * u * u * t * y1 + 3 * u * t * t * y2 + t * t * t * y3,
  ];
}

function segments(path: string): number[][] {
  const tokens = path.match(/[MCLHV]|-?\d+(\.\d+)?/g)!;
  const out: number[][] = [];
  let x = 0;
  let y = 0;
  for (let i = 0; i < tokens.length;) {
    const command = tokens[i++]!;
    if (command === "M") {
      x = Number(tokens[i++]);
      y = Number(tokens[i++]);
    } else if (command === "C") {
      const numbers = tokens.slice(i, i + 6).map(Number);
      i += 6;
      out.push([x, y, ...numbers]);
      x = numbers[4]!;
      y = numbers[5]!;
    } else if (command === "L") {
      const nx = Number(tokens[i++]);
      const ny = Number(tokens[i++]);
      out.push([x, y, x, y, nx, ny, nx, ny]);
      x = nx;
      y = ny;
    }
  }
  return out;
}

describe("monotone cubic lines through the measured points", () => {
  it("passes through every point and never overshoots between two of them", () => {
    const xs = Float64Array.from({ length: 60 }, (_, i) => 10 + i * 2.5);
    const ys = Float64Array.from({ length: 60 }, (_, i) => 50 + 30 * Math.sin(i * 0.7) + (i % 7 === 0 ? 25 : 0));
    const path = monotoneRun(xs, ys);
    const parts = segments(path);
    expect(parts.length).toBe(59);
    parts.forEach((segment, index) => {
      expect(segment[6]).toBeCloseTo(xs[index + 1]!, 1);
      expect(segment[7]).toBeCloseTo(ys[index + 1]!, 1);
      const low = Math.min(ys[index]!, ys[index + 1]!) - 0.06;
      const high = Math.max(ys[index]!, ys[index + 1]!) + 0.06;
      for (let t = 0; t <= 1; t += 0.05) {
        const [, y] = bezier(segment, t);
        expect(y).toBeGreaterThanOrEqual(low);
        expect(y).toBeLessThanOrEqual(high);
      }
    });
  });

  it("keeps a flat stretch flat and a step a clean rise", () => {
    const xs = [0, 3, 6, 9, 12, 15];
    const ys = [40, 40, 40, 10, 10, 10];
    for (const segment of segments(monotoneRun(xs, ys))) {
      for (let t = 0; t <= 1; t += 0.1) {
        const [, y] = bezier(segment, t);
        expect(y).toBeGreaterThanOrEqual(9.95);
        expect(y).toBeLessThanOrEqual(40.05);
      }
    }
    expect(monotoneRun([0], [5])).toBe("M0.0,5.0");
    expect(monotoneRun([0, 4], [5, 9])).toBe("M0.0,5.0 L4.0,9.0");
  });

  it("draws a step channel as steps between bin centres", () => {
    expect(stepRun([0, 10, 20], [5, 5, 8])).toBe("M0.0,5.0 H5.0 V5.0 H15.0 V8.0 H20.0");
  });

  it("breaks the line at a missing bin", () => {
    const column = Float64Array.from(signalColumn(data, "speed")!);
    column[data.lapIndex.get(3)! * data.bins + 100] = Number.NaN;
    const range = signalRange(data, "speed", [2], null);
    const path = lapTracePath(data, column, 3, false, range, { plotLeft: 6, plotWidth: 1200, top: 6, plotHeight: 80 }, 1200);
    expect(path.match(/M/g)!.length).toBe(2);
    expect(path.match(/C/g)!.length).toBe(640 - 1 - 2);
  });

  it("groups the laps by colour and draws every measured bin when the budget allows", () => {
    const frame = { plotLeft: 6, plotWidth: 1200, top: 6, plotHeight: 80 };
    const range = signalRange(data, "speed", [0, 1, 2], null);
    const groups = tracePathGroups(data, "speed", 0, [1, 2, 3], 2, null, frame, range);
    expect(groups.reduce((sum, group) => sum + group.laps.length, 0)).toBe(3);
    expect(groups[groups.length - 1]!.spotlight).toBe(true);
    const spotlight = groups[groups.length - 1]!;
    expect(spotlight.d.match(/C/g)!.length).toBe(639);
    // Gear is a step channel: no curves.
    const gear = tracePathGroups(data, "gear", 0, [1], null, null, frame, signalRange(data, "gear", [0], null));
    expect(gear[0]!.d).not.toContain("C");
    expect(gear[0]!.d).toContain("H");
  });
});

describe("the cursor reads the measured bins", () => {
  it("keeps the measured values, not a blend of neighbours", () => {
    const metrics = chartMetrics(1200, 600, 3);
    const rows = [{ row_id: "s", signals: ["speed"] }, { row_id: "g", signals: ["gear"] }];
    const config = buildCursorConfig({ data, rows, selectedLaps: [1, 2, 3], spotlightLap: null, metrics, trackPoints: [], initialFraction: 0.3, key: "k" });
    expect(config.bins).toBe(640);
    const speed = plotColumn(data, "speed", null)!;
    const lap = config.traces[0]!.lap;
    const offset = data.lapIndex.get(lap)! * 640;
    expect(Array.from(config.traces[0]!.rowValues[0]![0]!)).toEqual(Array.from(speed.subarray(offset, offset + 640)));
    expect(config.rows[1]!.signals[0]!.step).toBe(true);
    expect(config.rows[0]!.signals[0]!.step).toBe(false);
  });
});

import { describe, expect, it } from "vitest";
import { fixtureFetch } from "../fixtures/api";
import { buildSectionsAnalysis, lineOffset } from "../fixtures/sections";
import { buildSyntheticAnalysis } from "../fixtures/synthetic";
import { buildMapGeometry, mapPointAt } from "../geometry/map-model";
import { NO_ROTATION, principalAxisRotation, rotatePoint, rotationDegrees } from "../geometry/rotation";
import { createAnalysisLoader } from "../load";
import type { TrackGeometryData } from "../types";
import { contextTitle, lapContexts, lapTireAges, runTireAge, tiresText, trafficCell, trafficCellText } from "../ui/section-context";
import { POSITION_SIGNALS, linePath, positionColumns, sectionRacingLines } from "../ui/section-lines";
import { SECTION_SIGNALS, sectionBins } from "../ui/section-traces-model";

// Round 5, U3 (backlog #75, #76, #81): Sections lap rows carry the laps on the tires and the lap's traffic; the map draws each lap's
// recorded racing line through the section; every map is turned so the centreline's long axis is horizontal.

/** An ellipse `a` x `b` turned by `tilt` radians about (cx, cy), lap fraction 0 at angle `start`. */
function ellipse(a: number, b: number, tilt: number, count = 200, start = 0, cx = 0.5, cy = 0.5): { x: number; y: number }[] {
  return Array.from({ length: count }, (_, i) => {
    const t = start + (i / count) * Math.PI * 2;
    const ex = a * Math.cos(t);
    const ey = b * Math.sin(t);
    return { x: cx + ex * Math.cos(tilt) - ey * Math.sin(tilt), y: cy + ex * Math.sin(tilt) + ey * Math.cos(tilt) };
  });
}

function box(points: readonly { x: number; y: number }[]): { w: number; h: number } {
  const xs = points.map((p) => p.x);
  const ys = points.map((p) => p.y);
  return { w: Math.max(...xs) - Math.min(...xs), h: Math.max(...ys) - Math.min(...ys) };
}

/** Twice the signed area (shoelace): its sign is the direction of travel. */
function signedArea(points: readonly { x: number; y: number }[]): number {
  let sum = 0;
  for (let i = 0; i < points.length; i++) {
    const a = points[i]!;
    const b = points[(i + 1) % points.length]!;
    sum += a.x * b.y - b.x * a.y;
  }
  return sum;
}

describe("principal-axis rotation (#81)", () => {
  it("lays a tilted outline's long axis horizontal, so it is wider than tall", () => {
    for (const tilt of [0.3, 0.9, -1.1, 1.45, Math.PI / 2]) {
      const points = ellipse(0.4, 0.15, tilt);
      const rotation = principalAxisRotation(points);
      const turned = points.map((p) => rotatePoint(rotation, p.x, p.y));
      const { w, h } = box(turned);
      expect(w / h, `tilt ${tilt}`).toBeCloseTo(0.4 / 0.15, 1);
    }
  });

  it("is derived from the points alone: the same points give the same turn, in any order of evaluation", () => {
    const points = ellipse(0.4, 0.2, 0.7);
    const a = principalAxisRotation(points);
    const b = principalAxisRotation(points.map((p) => ({ ...p })));
    expect(a).toEqual(b);
  });

  it("puts the start/finish point in the lower half and keeps the direction of travel (a turn, never a mirror)", () => {
    for (const start of [Math.PI / 2, -Math.PI / 2, 0.3, 2.6]) {
      const points = ellipse(0.4, 0.15, 0.8, 200, start);
      const rotation = principalAxisRotation(points);
      const turned = points.map((p) => rotatePoint(rotation, p.x, p.y));
      expect(turned[0]!.y, `start ${start}`).toBeGreaterThan(rotation.cy);
      expect(Math.sign(signedArea(turned))).toBe(Math.sign(signedArea(points)));
    }
  });

  it("leaves a near-circle and too few points unturned", () => {
    expect(principalAxisRotation(ellipse(0.3, 0.299, 0.6)).angle).toBe(0);
    expect(principalAxisRotation([{ x: 0, y: 0 }, { x: 1, y: 1 }]).angle).toBe(0);
    expect(principalAxisRotation([])).toEqual(NO_ROTATION);
    expect(principalAxisRotation([{ x: Number.NaN, y: 0 }, { x: 0, y: 1 }, { x: 1, y: 0 }, { x: 2, y: 2 }]).angle).not.toBeNaN();
    expect(rotationDegrees({ ...NO_ROTATION, angle: Math.PI / 2 })).toBe(90);
  });
});

describe("the map turns with its outline (#81)", () => {
  const base = buildSyntheticAnalysis({ laps: 3, analysisId: "u3-map" }).track;
  /** The synthetic geometry with its main path turned by `tilt` about (0.5, 0.5); the transform is unchanged. */
  const tilted = (tilt: number): TrackGeometryData => {
    const main = base.main_path!;
    const x = main.x.map((value, i) => 0.5 + (value - 0.5) * Math.cos(tilt) - (main.y[i]! - 0.5) * Math.sin(tilt));
    const y = main.y.map((value, i) => 0.5 + (main.x[i]! - 0.5) * Math.sin(tilt) + (value - 0.5) * Math.cos(tilt));
    return {
      ...base,
      main_path: { ...main, x, y },
      start_finish: null,
      pit_lane: { pct: [0.1, 0.12, 0.14], x: [x[50]!, x[60]!, x[70]!], y: [y[50]!, y[60]!, y[70]!] },
    };
  };

  it("draws a tilted outline wider than tall and records the turn", () => {
    const flat = buildMapGeometry(base)!;
    const steep = buildMapGeometry(tilted(1.2))!;
    expect(Math.abs(steep.rotationDeg)).toBeGreaterThan(30);
    const a = box(flat.points);
    const b = box(steep.points);
    expect(b.w / b.h).toBeGreaterThan(1.2);
    expect(b.w / b.h).toBeCloseTo(a.w / a.h, 1);
  });

  it("places every overlay with the same turn: GPS fixes, the start/finish mark and pit road", () => {
    const track = tilted(0.9);
    const map = buildMapGeometry(track)!;
    const t = track.transform!;
    // A GPS fix at normalized (nx, ny) lands where the centreline's projection of (nx, ny) does.
    const nx = track.main_path!.x[30]!;
    const ny = track.main_path!.y[30]!;
    const fix = map.projectGps(t.source_bounds.maximum_y - ny * t.normalization_scale, t.source_bounds.minimum_x + nx * t.normalization_scale)!;
    expect(fix.x).toBeCloseTo(map.points[30]!.x, 6);
    expect(fix.y).toBeCloseTo(map.points[30]!.y, 6);
    // The start/finish mark crosses the line at lap fraction 0.
    const s = map.startFinish!;
    const at0 = mapPointAt(map.points, 0)!;
    expect((s.x1 + s.x2) / 2).toBeCloseTo(at0.x, 6);
    expect((s.y1 + s.y2) / 2).toBeCloseTo(at0.y, 6);
    // Pit road (here three main-path points) is drawn through the turned points.
    const first = map.pit!.road.match(/^M ([\d.-]+) ([\d.-]+)/)!;
    expect(Number(first[1])).toBeCloseTo(map.points[50]!.x, 1);
    expect(Number(first[2])).toBeCloseTo(map.points[50]!.y, 1);
  });

  it("keeps the 420 x 280 fit", () => {
    const map = buildMapGeometry(tilted(-0.7))!;
    for (const p of map.points) {
      expect(p.x).toBeGreaterThanOrEqual(35 - 1e-6);
      expect(p.x).toBeLessThanOrEqual(385 + 1e-6);
      expect(p.y).toBeGreaterThanOrEqual(31 - 1e-6);
      expect(p.y).toBeLessThanOrEqual(249 + 1e-6);
    }
  });
});

async function loadSections() {
  const analysis = buildSectionsAnalysis("an-u3");
  const loader = createAnalysisLoader({ fetch: fixtureFetch(analysis) });
  const handle = loader.get("an-u3");
  const data = await handle.ensureSignals([...SECTION_SIGNALS, ...POSITION_SIGNALS]);
  const overview = await handle.overview();
  return { analysis, data, overview, map: buildMapGeometry(overview.track)! };
}

const SECTION_B = { startPct: 0.42, endPct: 0.58 };

describe("racing lines through a section (#76)", () => {
  it("draws each lap's own recorded path: the samples in the section, through the map's GPS transform, no offset", async () => {
    const { data, map } = await loadSections();
    const lines = sectionRacingLines(data, map, SECTION_B, [5, 12, 30]);
    expect(lines.map((line) => line.lap)).toEqual([5, 12, 30]);
    const { bins } = sectionBins(data.x, SECTION_B);
    const { lat, lon } = positionColumns(data)!;
    for (const line of lines) {
      const index = data.lapIndex.get(line.lap)!;
      const expected = bins.map((bin) => map.projectGps(lat[index * data.bins + bin]!, lon[index * data.bins + bin]!)!);
      expect(line.runs).toHaveLength(1);
      expect(line.runs[0]!.length).toBe(bins.length);
      line.runs[0]!.forEach((p, i) => {
        expect(p.x).toBeCloseTo(expected[i]!.x, 9);
        expect(p.y).toBeCloseTo(expected[i]!.y, 9);
      });
    }
    // Different laps drove different lines (the fixture's offsets differ), so the lines are not copies of one another.
    expect(lines[0]!.d).not.toBe(lines[1]!.d);
  });

  it("sits exactly as far from the centreline as the lap drove", async () => {
    const { data, map } = await loadSections();
    const lap = 7;
    const { bins } = sectionBins(data.x, SECTION_B);
    const index = data.lapIndex.get(lap)!;
    const { lat, lon } = positionColumns(data)!;
    // The fixture moved this lap's positions off the centreline by its own offset: the line keeps that distance, nothing is added.
    const bin = bins[Math.floor(bins.length / 2)]!;
    const t = data.x[bin]!;
    const placed = map.projectGps(lat[index * data.bins + bin]!, lon[index * data.bins + bin]!)!;
    const centre = mapPointAt(map.points, t)!;
    const gap = Math.hypot(placed.x - centre.x, placed.y - centre.y);
    const scale = Math.hypot(map.project(1, 0).x - map.project(0, 0).x, map.project(1, 0).y - map.project(0, 0).y);
    expect(gap).toBeCloseTo(Math.abs(lineOffset(lap, t)) * scale, 0);
  });

  it("breaks the line where a bin has no position and draws no line for a lap without positions", async () => {
    const { data, map } = await loadSections();
    const { bins } = sectionBins(data.x, SECTION_B);
    const { lat } = positionColumns(data)!;
    const holed = data.lapIndex.get(20)!;
    lat[holed * data.bins + bins[10]!] = Number.NaN;
    const empty = data.lapIndex.get(21)!;
    for (const bin of bins) lat[empty * data.bins + bin] = Number.NaN;
    const lines = sectionRacingLines(data, map, SECTION_B, [20, 21]);
    expect(lines.map((line) => line.lap)).toEqual([20]);
    expect(lines[0]!.runs.map((run) => run.length)).toEqual([10, bins.length - 11]);
    expect(lines[0]!.d.match(/M/g)!.length).toBe(2);
  });

  it("draws nothing without the trace store, a map transform or position channels", async () => {
    const { data, map } = await loadSections();
    expect(sectionRacingLines(null, map, SECTION_B, [5])).toEqual([]);
    expect(sectionRacingLines(data, { ...map, hasTransform: false }, SECTION_B, [5])).toEqual([]);
    expect(sectionRacingLines(data, map, null, [5])).toEqual([]);
    expect(sectionRacingLines(data, map, SECTION_B, [999])).toEqual([]);
    expect(linePath([[{ x: 1, y: 2 }]])).toBe("");
  });
});

describe("tires and traffic on the lap rows (#75)", () => {
  it("counts the set's laps at each lap from the oldest corner at the run's end, green plus caution", () => {
    const run = { lap_numbers: [10, 11, 12, 13], tire_set_lifecycle: { corners: { LF: { green_laps_on_set: 20, caution_laps_on_set: 2 }, RF: { green_laps_on_set: 4 } } } };
    expect(runTireAge(run)).toBe(22);
    const ages = lapTireAges([run]);
    expect([...ages.entries()]).toEqual([[10, 19], [11, 20], [12, 21], [13, 22]]);
  });

  it("gives no figure without a lifecycle or where the run's laps contradict it", () => {
    expect(lapTireAges([{ lap_numbers: [1, 2, 3] }]).size).toBe(0);
    const short = lapTireAges([{ lap_numbers: [1, 2, 3, 4], tire_set_lifecycle: { corners: { LF: { green_laps_on_set: 2 } } } }]);
    expect([...short.keys()]).toEqual([3, 4]);
    expect(lapTireAges(null).size).toBe(0);
  });

  it("reads the traffic share and side from the race view's clean_context", () => {
    const race = { laps: [
      { lap: 4, clean_context: { traffic_proximity_fraction: 0.75, traffic_side: "behind" } },
      { lap: 5, clean_context: { traffic_proximity_fraction: 0.04 } },
      { lap: 6, clean_context: {} },
    ] } as never;
    const contexts = lapContexts(race, [{ lap_numbers: [4, 5, 6], tire_set_lifecycle: { corners: { RR: { green_laps_on_set: 12 } } } }]);
    expect(trafficCellText(contexts.get(4)!.traffic)).toBe("75 % · behind");
    expect(trafficCellText(contexts.get(5)!.traffic)).toBe("4 %");
    expect(trafficCellText(contexts.get(6)!.traffic)).toBe("—");
    expect(tiresText(contexts.get(4)!.tires)).toBe("10");
    expect(tiresText(null)).toBe("—");
    expect(contextTitle(contexts.get(4))).toBe("10 laps on tires · traffic 75 % of lap · behind");
  });

  it("says both sides the way the Race readout does, and keeps the share and the side apart for the cell", () => {
    const race = { laps: [
      { lap: 7, clean_context: { traffic_proximity_fraction: 1, traffic_side: "both" } },
      { lap: 8, clean_context: { traffic_proximity_fraction: 0.25, traffic_side: "ahead" } },
      { lap: 9, clean_context: { traffic_proximity_fraction: null, traffic_side: "both" } },
    ] } as never;
    const contexts = lapContexts(race, null);
    expect(trafficCellText(contexts.get(7)!.traffic)).toBe("100 % · both sides");
    expect(trafficCell(contexts.get(7)!.traffic)).toEqual({ share: "100 %", side: "both sides" });
    expect(contextTitle(contexts.get(7))).toBe("traffic 100 % of lap · both sides");
    expect(trafficCell(contexts.get(8)!.traffic)).toEqual({ share: "25 %", side: "ahead" });
    // No share, no cell: the side alone is not shown.
    expect(trafficCell(contexts.get(9)!.traffic)).toEqual({ share: "—", side: null });
    expect(trafficCellText(contexts.get(9)!.traffic)).toBe("—");
  });

  it("the sections fixture agrees with its runs: a four-tire stop after run 1, right side only after run 2", async () => {
    const { overview } = await loadSections();
    const ages = lapTireAges(overview.runs.runs as unknown[]);
    expect(ages.get(1)).toBe(1);
    expect(ages.get(28)).toBe(28);
    expect(ages.get(29)).toBe(1);
    expect(ages.get(57)).toBe(29);
    expect(ages.get(82)).toBe(54);
  });
});

describe("Sessions silhouettes turn by the same rule (#81)", () => {
  it("draws a tilted outline wider than tall, as the investigation map does", async () => {
    const { silhouetteOf } = await import("../../sessions/silhouette");
    const points = ellipse(0.4, 0.12, 1.2);
    const s = silhouetteOf(points.map((p) => p.x), points.map((p) => p.y))!;
    const nums = [...s.d.matchAll(/(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)/g)].map((m) => ({ x: Number(m[1]), y: Number(m[2]) }));
    const { w, h } = box(nums);
    expect(w / h).toBeGreaterThan(2.5);
    // The start tick sits at the turned start point, in the lower half of the card.
    const [x1, y1, x2, y2] = s.tick;
    expect((y1 + y2) / 2).toBeGreaterThan(s.height / 2);
    expect(Number.isFinite(x1 + x2)).toBe(true);
  });
});

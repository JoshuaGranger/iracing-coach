import { describe, expect, it } from "vitest";
import type { VNode } from "preact";
import { theme } from "../../gen/theme";
import { buildRound3Analysis } from "../fixtures/round3";
import { inRaceStops, pitStops, TIRE_CORNERS, type PitStop } from "../geometry/rail-model";
import { parseInvestigation } from "../investigation-model";
import { inspectSectionEntry } from "../ui/comparison-model";
import { PitCard } from "../ui/PitPictogram";
import { availableLayers, lapContexts, layerReadout, layersReadout, parseLayers, readLayers, writeLayers } from "../ui/race-layers";
import { paceTopMargin } from "../ui/RaceView";
import { fitOnLaps, fitSegment, runIdentities, shadeColour, smoothPath, toggleRun } from "../ui/runs-model";
import type { RaceViewData } from "../types";

const analysis = buildRound3Analysis();
const inv = parseInvestigation((analysis.investigation as { race_investigation: unknown }).race_investigation);
const runs = analysis.runs.runs as unknown[];
const stops = pitStops(analysis.race, { runs }, null);

function hue(hex: string): { h: number; s: number; l: number } {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255) as [number, number, number];
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const l = (max + min) / 2;
  const d = max - min;
  if (d === 0) return { h: 0, s: 0, l };
  const s = d / (1 - Math.abs(2 * l - 1));
  const h = max === r ? ((g - b) / d) % 6 : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
  return { h: (h * 60 + 360) % 360, s, l };
}

describe("run palette (#40): no amber and no red", () => {
  it("keeps every run colour out of the caution and loss hues", () => {
    expect(theme.identity.runs).toHaveLength(8);
    for (const hex of theme.identity.runs) {
      const { h, s } = hue(hex);
      if (s < 0.15) continue; // a neutral grey carries no hue
      expect(h < 20 || h > 70, `${hex} hue ${h.toFixed(0)} is amber or orange`).toBe(true);
      expect(h > 20 && h < 340, `${hex} hue ${h.toFixed(0)} is red`).toBe(true);
    }
    expect(theme.identity.runs).not.toContain(theme.race.caution);
    expect(theme.identity.runs).not.toContain(theme.race.loss);
  });
});

describe("run identity by tire set (#42)", () => {
  it("gives a run after a no-tire stop the same hue, lighter, named `same tires`", () => {
    const ids = runIdentities([1, 2, 3], stops);
    expect(ids.get(1)).toMatchObject({ set: 1, shade: 0, sameTires: false, color: "var(--run-1)", name: "Run 1" });
    expect(ids.get(2)).toMatchObject({ set: 2, shade: 0, sameTires: false, color: "var(--run-2)", name: "Run 2" });
    expect(ids.get(3)).toMatchObject({ set: 2, shade: 1, sameTires: true, name: "Run 3 · same tires" });
    expect(ids.get(3)!.color).toBe(shadeColour(2, 1));
    expect(ids.get(3)!.color).toMatch(/^color-mix\(in srgb, var\(--run-2\) \d+%, white\)$/);
    expect(ids.get(3)!.dash).not.toBe(ids.get(2)!.dash);
  });

  it("claims no shared set when the stop's tires were not reported, and ignores a post-race service", () => {
    const unknown: PitStop[] = stops.map((stop) => ({ ...stop, tires: null }));
    expect([...runIdentities([1, 2, 3], unknown).values()].map((id) => id.set)).toEqual([1, 2, 3]);
    const postRun: PitStop[] = stops.map((stop) => ({ ...stop, tires: new Set(), postRun: true }));
    expect([...runIdentities([1, 2, 3], postRun).values()].map((id) => id.sameTires)).toEqual([false, false, false]);
    const fourTires: PitStop[] = stops.map((stop) => ({ ...stop, tires: new Set(TIRE_CORNERS) }));
    expect([...runIdentities([1, 2, 3], fourTires).values()].map((id) => id.color)).toEqual(["var(--run-1)", "var(--run-2)", "var(--run-3)"]);
  });

  it("chains several no-tire stops on one set, each lighter", () => {
    const chain = [1, 2, 3].map((run) => ({ runNumber: run, tires: new Set<never>(), postRun: false }));
    const ids = runIdentities([1, 2, 3, 4], chain);
    expect([...ids.values()].map((id) => [id.set, id.shade])).toEqual([[1, 0], [1, 1], [1, 2], [1, 3]]);
    expect(shadeColour(1, 3)).not.toBe(shadeColour(1, 2));
  });
});

describe("the fit is the only line (#44)", () => {
  it("evaluates the backend's line at each lap's reported exposure inside the fitted span only", () => {
    const run = inv.runs[0]!;
    const points = fitOnLaps(run, inv.laps);
    const segment = fitSegment(run)!;
    expect(points[0]!.lap).toBe(3);
    expect(points[points.length - 1]!.lap).toBe(24);
    expect(points[0]!.y).toBeCloseTo(segment.y1, 9);
    expect(points[points.length - 1]!.y).toBeCloseTo(segment.y2, 9);
    for (const point of points) {
      const lap = inv.laps.find((item) => item.lap === point.lap)!;
      expect(lap.greenExposure).toBeGreaterThanOrEqual(3);
      expect(lap.greenExposure).toBeLessThanOrEqual(24);
    }
    expect(fitOnLaps(inv.runs[2]!, inv.laps)).toEqual([]);
  });

  it("draws a smooth monotone curve that never leaves the values it joins", () => {
    expect(smoothPath([[0, 0]])).toBe("");
    expect(smoothPath([[0, 0], [10, 5]])).toBe("M0,0L10,5");
    const pts: [number, number][] = [[0, 50], [10, 40], [20, 38], [30, 20], [40, 20]];
    const d = smoothPath(pts);
    expect(d.startsWith("M0,50C")).toBe(true);
    const ys = [...d.matchAll(/C[\d.-]+,([\d.-]+) [\d.-]+,([\d.-]+) [\d.-]+,([\d.-]+)/g)].flatMap((m) => [Number(m[1]), Number(m[2])]);
    for (const y of ys) {
      expect(y).toBeGreaterThanOrEqual(20 - 1e-6);
      expect(y).toBeLessThanOrEqual(50 + 1e-6);
    }
  });
});

describe("run toggles on Runs (#47)", () => {
  it("hides and shows a run and never hides the last one", () => {
    const all = [1, 2, 3];
    let hidden = toggleRun(new Set(), 2, all);
    expect([...hidden]).toEqual([2]);
    hidden = toggleRun(hidden, 1, all);
    expect([...hidden].sort()).toEqual([1, 2]);
    expect([...toggleRun(hidden, 3, all)].sort()).toEqual([1, 2]);
    expect([...toggleRun(hidden, 2, all)]).toEqual([1]);
  });
});

describe("context layers (#46, round 4 #60–#62): what the race view reports per lap", () => {
  const contexts = lapContexts(analysis.race);

  it("reads the backend's per-lap fields and offers only layers with figures", () => {
    expect(contexts.get(10)).toEqual({ lap: 10, trackTempF: 99.8, usagePct: null, fuelGal: expect.any(Number) });
    expect(contexts.get(1)!.fuelGal).toBeCloseTo(60 / 3.785411784, 9);
    // No traffic and no slip layer, though the race view carries both fields (#61, #62).
    expect(availableLayers(contexts).map((layer) => layer.key)).toEqual(["temp", "fuel"]);
    const withUsage = { ...analysis.race, laps: (analysis.race.laps as Record<string, unknown>[]).map((lap) => ({ ...lap, conditions: { track_usage_percent: 34 + (lap.lap as number) * 0.1 } })) } as unknown as RaceViewData;
    expect(availableLayers(lapContexts(withUsage)).map((layer) => layer.key)).toEqual(["usage", "fuel"]);
    expect(availableLayers(lapContexts({ ...analysis.race, laps: [] } as RaceViewData))).toEqual([]);
    // Out-of-range figures are not figures.
    const bad = { ...analysis.race, laps: [{ lap: 1, conditions: { track_usage_percent: 140 }, clean_context: { traffic_proximity_fraction: 3 } }] } as unknown as RaceViewData;
    expect(lapContexts(bad).get(1)).toMatchObject({ usagePct: null });
  });

  it("reads out each layer with a short evidence tag, and a missing figure as `no reading`", () => {
    const lap = contexts.get(15)!;
    expect(layerReadout("temp", lap)).toBe(`Track ${lap.trackTempF!.toFixed(1)} °F`);
    expect(layerReadout("usage", lap)).toBe("Usage no reading");
    expect(layersReadout(["fuel", "temp"], contexts.get(10))).toEqual([`Track ${contexts.get(10)!.trackTempF!.toFixed(1)} °F · measured`, `Fuel ${contexts.get(10)!.fuelGal!.toFixed(1)} gal · measured`]);
    expect(layersReadout(["temp"], undefined)).toEqual(["Track no reading"]);
    for (const text of layersReadout(["temp", "usage", "fuel"], lap)) expect(text).not.toMatch(/derived|proxy|not |unavailable/i);
  });

  it("remembers the layers on the device, off by default", () => {
    const store = new Map<string, string>();
    const storage = { getItem: (key: string) => store.get(key) ?? null, setItem: (key: string, value: string) => void store.set(key, value) };
    expect(readLayers(storage)).toEqual([]);
    writeLayers(storage, ["fuel", "temp"]);
    expect(readLayers(storage)).toEqual(["fuel", "temp"]);
    // A device that remembered the dropped layers keeps only what is left.
    expect(parseLayers('["traffic","slip","temp"]')).toEqual(["temp"]);
    expect(parseLayers("not json")).toEqual([]);
    expect(parseLayers('["temp","bogus","temp"]')).toEqual(["temp"]);
    expect(readLayers({ getItem: () => { throw new Error("blocked"); } })).toEqual([]);
  });
});

function texts(node: unknown): string[] {
  if (node === null || node === undefined || typeof node === "boolean") return [];
  if (typeof node === "string" || typeof node === "number") return [String(node)];
  if (Array.isArray(node)) return node.flatMap(texts);
  const vnode = node as VNode<Record<string, unknown>>;
  if (typeof vnode.type === "function") return texts((vnode.type as (props: unknown) => unknown)(vnode.props));
  return texts(vnode.props?.children);
}

describe("pit detail (#39, #41)", () => {
  it("shows tires, fuel and the stop time and no sentence about missing fields", () => {
    const stop = inRaceStops(stops).find((item) => item.lap === 56)!;
    const words = texts(PitCard({ stop })).join(" ");
    expect(words).toContain("6.1");
    expect(words).toContain("No tires");
    expect(words).toMatch(/\+ ?8\.0 gal/);
    expect(words).not.toMatch(/extract|repair|penalt/i);
    const bare = texts(PitCard({ stop: { ...stop, durationS: null, tires: null, fuelAddedGal: null } })).join(" ");
    expect(bare).not.toMatch(/—|extract/);
  });

  it("keeps the top margin above the pit tag's glow", () => {
    expect(paceTopMargin([400], 52, 1)).toBeGreaterThanOrEqual(25 + 12);
  });
});

describe("the Inspect section entry for the Sections tab (#45)", () => {
  it("names the lead action's section, else opens Traces, else nothing", () => {
    const [runsCmp, windowCmp, none] = inv.comparisons;
    expect(inspectSectionEntry(runsCmp!)).toMatchObject({ label: "Inspect section A", sectionId: "A" });
    expect(inspectSectionEntry(windowCmp!)).toMatchObject({ label: "Open in Traces", sectionId: null });
    expect(inspectSectionEntry(none!)).toBeNull();
    expect(inspectSectionEntry(null)).toBeNull();
  });
});

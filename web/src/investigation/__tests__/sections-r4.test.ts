import { describe, expect, it } from "vitest";
import { createAnalysisLoader } from "../load";
import { fixtureFetch } from "../fixtures/api";
import { buildSectionsAnalysis } from "../fixtures/sections";
import { deltaBar, lapsToOpen, parseSectionLaps, shadedLaps } from "../ui/section-laps";
import { offsetPath } from "../ui/SectionsView";

// Web round 3, R4 (backlog #48, #49): the Sections view reads the projector's section_laps and only selects and draws.

const RAW = {
  sections: [
    {
      section_id: "A", clean_lap_count: 9, median_s: 6.412,
      fastest: [{ lap: 8, section_s: 6.3, delta_s: -0.112 }, { lap: 2, section_s: 6.33, delta_s: -0.082 }, { lap: 6, section_s: 6.36, delta_s: -0.052 }],
      slowest: [{ lap: 3, section_s: 6.7, delta_s: 0.288 }, { lap: 9, section_s: 6.6, delta_s: 0.188 }, { lap: 5, section_s: 6.5, delta_s: 0.088 }],
    },
    { section_id: "B", clean_lap_count: 1, median_s: 7.1, fastest: [], slowest: [] },
    { section_id: "C", clean_lap_count: 0, median_s: null, fastest: [], slowest: [], future_field: { kept: true } },
    { clean_lap_count: 4 },
  ],
};

describe("parseSectionLaps", () => {
  it("is null for a view projected before section_laps existed", () => {
    expect(parseSectionLaps(undefined)).toBeNull();
    expect(parseSectionLaps({})).toBeNull();
    expect(parseSectionLaps({ sections: "x" })).toBeNull();
  });

  it("reads every section by id, skips an entry without one and keeps the projector's order", () => {
    const laps = parseSectionLaps(RAW)!;
    expect([...laps.keys()]).toEqual(["A", "B", "C"]);
    const a = laps.get("A")!;
    expect(a.cleanLapCount).toBe(9);
    expect(a.medianS).toBe(6.412);
    expect(a.fastest.map((lap) => lap.lap)).toEqual([8, 2, 6]);
    expect(a.slowest.map((lap) => lap.lap)).toEqual([3, 9, 5]);
    expect(laps.get("C")!.medianS).toBeNull();
  });

  it("drops a lap entry with a missing figure and never lists more than three a side", () => {
    const laps = parseSectionLaps({ sections: [{ section_id: "A", clean_lap_count: 12, median_s: 5, fastest: [{ lap: 1, section_s: 4.9 }, ...[2, 3, 4, 5].map((lap) => ({ lap, section_s: 4.9, delta_s: -0.1 }))], slowest: [] }] })!;
    expect(laps.get("A")!.fastest.map((lap) => lap.lap)).toEqual([2, 3, 4]);
  });
});

describe("shadedLaps: time order, green shades faster, red shades slower", () => {
  it("lists fastest to slowest with the strongest shades at the extremes", () => {
    const { fastest, slowest } = shadedLaps(parseSectionLaps(RAW)!.get("A"));
    expect(fastest.map((lap) => [lap.lap, lap.shade])).toEqual([[8, "g1"], [2, "g2"], [6, "g3"]]);
    expect(slowest.map((lap) => [lap.lap, lap.shade])).toEqual([[5, "r3"], [9, "r2"], [3, "r1"]]);
  });

  it("draws nothing without an entry or with too few laps", () => {
    expect(shadedLaps(null)).toEqual({ fastest: [], slowest: [] });
    expect(shadedLaps(parseSectionLaps(RAW)!.get("B"))).toEqual({ fastest: [], slowest: [] });
  });
});

describe("lapsToOpen: exactly the listed laps, or the selected subset", () => {
  it("opens all six when none is selected and only the selected otherwise, in list order", () => {
    expect(lapsToOpen([8, 2, 6, 5, 9, 3], new Set())).toEqual([8, 2, 6, 5, 9, 3]);
    expect(lapsToOpen([8, 2, 6, 5, 9, 3], new Set([3, 8]))).toEqual([8, 3]);
    expect(lapsToOpen([8, 2, 6, 5, 9, 3], new Set([40]))).toEqual([8, 2, 6, 5, 9, 3]);
  });
});

describe("deltaBar: a drawing scale against the largest delta shown", () => {
  it("puts gains left and losses right of the median, the largest at half the cell", () => {
    const laps = parseSectionLaps(RAW)!.get("A")!;
    const all = [...laps.fastest, ...laps.slowest];
    expect(deltaBar(0.288, all)).toEqual({ side: "loss", widthPct: 50 });
    expect(deltaBar(-0.112, all).side).toBe("gain");
    expect(deltaBar(-0.112, all).widthPct).toBeCloseTo((0.112 / 0.288) * 50, 9);
    expect(deltaBar(0, all)).toEqual({ side: "zero", widthPct: 0 });
  });
});

describe("offsetPath: a strand beside the section line", () => {
  it("moves a straight segment sideways by the offset", () => {
    expect(offsetPath([{ x: 0, y: 0 }, { x: 10, y: 0 }, { x: 20, y: 0 }], 2)).toBe("M0,2 L10,2 L20,2");
    expect(offsetPath([{ x: 0, y: 0 }, { x: 0, y: 10 }], 1)).toBe("M-1,0 L-1,10");
    expect(offsetPath([{ x: 0, y: 0 }], 1)).toBe("");
  });
});

describe("the loader carries section_laps beside the investigation", () => {
  it("reads it from the investigation view of the sections fixture", async () => {
    const analysis = buildSectionsAnalysis("sections-unit");
    const overview = await createAnalysisLoader({ fetch: fixtureFetch(analysis) }).get("sections-unit").overview();
    const laps = overview.sectionLaps!;
    expect([...laps.keys()]).toEqual(["A", "B", "C"]);
    for (const entry of laps.values()) {
      expect(entry.fastest).toHaveLength(3);
      expect(entry.slowest).toHaveLength(3);
      expect(entry.fastest.every((lap) => lap.deltaS < 0)).toBe(true);
      expect(entry.slowest.every((lap) => lap.deltaS > 0)).toBe(true);
      // Screened laps (close traffic 10, 11, 79; caution 41–43) never appear.
      expect([...entry.fastest, ...entry.slowest].some((lap) => [10, 11, 41, 42, 43, 79].includes(lap.lap))).toBe(false);
    }
  });

  it("is null when the view has none", async () => {
    const analysis = buildSectionsAnalysis("sections-old");
    delete (analysis.investigation as { section_laps?: unknown }).section_laps;
    const overview = await createAnalysisLoader({ fetch: fixtureFetch(analysis) }).get("sections-old").overview();
    expect(overview.sectionLaps).toBeNull();
    expect(overview.investigation).not.toBeNull();
  });
});

import { describe, expect, it } from "vitest";
import { raceProgressColour, runColour } from "../../core/palette";
import { buildRound2Analysis, ROUND2_COMPARISONS } from "../fixtures/round2";
import { createTraceData, lapProgressEnds, progressColors } from "../geometry/trace-model";
import { decodeCcol } from "../ccol";
import { parseInvestigation, type Comparison } from "../investigation-model";
import { runColor } from "../format";
import {
  brackets,
  comparisonLaps,
  comparisonName,
  direction,
  dotDomain,
  groupMap,
  groupRows,
  lapRanges,
  niceMax,
  noResultLine,
  pairedMetrics,
  placeLabels,
  rangesText,
  screenedText,
  sectionTone,
} from "../ui/comparison-model";
import { lapChipStatus } from "../ui/LapRail";
import { chartLaps, lapLabel } from "../ui/RaceView";
import { fitLabel, fitSegment, runCards, sparkPath } from "../ui/runs-model";
import { PIT_UNEXTRACTED } from "../ui/PitPictogram";
import type { TraceLapData } from "../types";

const analysis = buildRound2Analysis();
const inv = parseInvestigation((analysis.investigation as { race_investigation: unknown }).race_investigation);
const byId = (id: string): Comparison => inv.comparisons.find((c) => c.id === id)!;

describe("comparison names and brackets (round 2, #28d, #28e)", () => {
  it("splits lap groups around omitted laps", () => {
    expect(lapRanges([65, 61, 62, 64])).toEqual([[61, 62], [64, 65]]);
    expect(rangesText([61, 62, 63, 64, 65])).toBe("L61–65");
    expect(rangesText([61, 62, 64, 65])).toBe("L61–62, 64–65");
    expect(rangesText([7])).toBe("L7");
  });

  it("names a comparison by its groups", () => {
    expect(comparisonName(byId(ROUND2_COMPARISONS.runs))).toBe("Run 2 → Run 3");
    expect(comparisonLaps(byId(ROUND2_COMPARISONS.runs))).toBe("L31–35 → L61–65");
    expect(comparisonName(byId(ROUND2_COMPARISONS.window))).toBe("Run 3: L61–62, 64–65 → L77–81");
    expect(comparisonLaps(byId(ROUND2_COMPARISONS.window))).toBeNull();
    const free = { ...byId(ROUND2_COMPARISONS.runs), baseline: { ...byId(ROUND2_COMPARISONS.runs).baseline, runNumber: null }, compared: { ...byId(ROUND2_COMPARISONS.runs).compared, runNumber: null } };
    expect(comparisonName(free)).toBe("L31–35 → L61–65");
  });

  it("draws two labelled brackets, one piece per contiguous range, in the group's run colour", () => {
    const [earlier, later] = brackets(byId(ROUND2_COMPARISONS.window));
    expect(earlier).toMatchObject({ role: "earlier", label: "Earlier L61–62, 64–65", run: 3, pieces: [[61, 62], [64, 65]] });
    expect(later).toMatchObject({ role: "later", label: "Later L77–81", pieces: [[77, 81]] });
    expect(brackets(null)).toEqual([]);
    expect(groupMap(byId(ROUND2_COMPARISONS.runs)).get(33)).toBe("earlier");
    expect(groupMap(byId(ROUND2_COMPARISONS.runs)).get(63)).toBe("later");
    expect(groupMap(byId(ROUND2_COMPARISONS.runs)).has(40)).toBe(false);
  });
});

describe("bracket labels on a narrow chart", () => {
  it("stay inside the plot and drop to a second line instead of overlapping", () => {
    expect(placeLabels([100, 400], [80, 80], 0, 500)).toEqual([{ x: 100, line: 0 }, { x: 400, line: 0 }]);
    expect(placeLabels([300, 340], [150, 90], 0, 380)).toEqual([{ x: 300, line: 0 }, { x: 335, line: 1 }]);
    expect(placeLabels([10], [100], 0, 300)).toEqual([{ x: 50, line: 0 }]);
  });
});

describe("the comparison picture reads the backend, draws, and computes nothing", () => {
  it("gives the backend's signed difference its direction; no pace result is one line", () => {
    expect(direction(byId(ROUND2_COMPARISONS.runs))).toBe("slower");
    expect(direction(byId(ROUND2_COMPARISONS.window))).toBe("faster");
    expect(direction(byId(ROUND2_COMPARISONS.none))).toBeNull();
    expect(noResultLine(byId(ROUND2_COMPARISONS.none))).toBe("No pace result · every lap screened · close traffic");
    expect(noResultLine(byId(ROUND2_COMPARISONS.none))).not.toMatch(/\./);
  });

  it("puts each group's laps on one lap-time scale with the backend's median and green/total counts", () => {
    const rows = groupRows(byId(ROUND2_COMPARISONS.window), inv.laps);
    expect(rows.map((row) => row.role)).toEqual(["earlier", "later"]);
    expect(rows[0]!.dots.map((dot) => dot.lap)).toEqual([61, 62, 64, 65]);
    expect(rows[1]!.dots.find((dot) => dot.lap === 79)).toMatchObject({ used: false, reasons: ["close_traffic"] });
    expect(rows[1]!.dots.filter((dot) => dot.used).length).toBe(4);
    expect(rows[1]!.green).toBe(5);
    expect(rows[1]!.total).toBe(5);
    const [lo, hi] = dotDomain(rows)!;
    for (const row of rows) {
      for (const dot of row.dots) expect(dot.lapTimeS).toBeGreaterThan(lo);
      expect(row.cohort.medianLapS!).toBeLessThan(hi);
    }
  });

  it("pairs starting fuel, throttle and close traffic, each on its own scale from zero", () => {
    const metrics = pairedMetrics(byId(ROUND2_COMPARISONS.runs));
    expect(metrics.map((m) => m.key)).toEqual(["fuel", "throttle", "traffic"]);
    expect(metrics[0]).toMatchObject({ unit: "gal", earlier: 14.6, later: 12.9, max: 20 });
    expect(metrics[1]).toMatchObject({ unit: "%", earlier: 71, later: 68, max: 100 });
    expect(metrics[2]).toMatchObject({ unit: "%", earlier: 4, later: 22, max: 100 });
    expect(niceMax(14.6)).toBe(20);
    expect(niceMax(2.3)).toBe(2.5);
    expect(niceMax(0)).toBe(1);
    expect(pairedMetrics({ ...byId(ROUND2_COMPARISONS.runs), metrics: [], baseline: { ...byId(ROUND2_COMPARISONS.runs).baseline, fuelStartGal: null }, compared: { ...byId(ROUND2_COMPARISONS.runs).compared, fuelStartGal: null } })).toEqual([]);
  });

  it("colours a section by sign only against a comparison with a pace result", () => {
    const runs = byId(ROUND2_COMPARISONS.runs);
    expect(sectionTone(runs, runs.corners[0])).toBe("loss");
    expect(sectionTone(runs, runs.corners[1])).toBe("gain");
    expect(sectionTone(runs, runs.corners[2])).toBe("neutral");
    const none = byId(ROUND2_COMPARISONS.none);
    expect(sectionTone(none, none.corners[0])).toBe("neutral");
  });
});

describe("Not used in pace fit (round 2, #28a)", () => {
  it("names the fit and the backend's reason; never Excl", () => {
    const lap = inv.laps.find((item) => item.lap === 10)!;
    expect(screenedText(lap)).toBe("Not used in Run 1 pace fit · close traffic");
    expect(lapLabel(lap)).toContain("Not used in Run 1 pace fit · close traffic");
    expect(screenedText(inv.laps.find((item) => item.lap === 12)!)).toBeNull();
    const meta = (analysis.catalog.laps as TraceLapData[]).find((item) => item.lap === 10)!;
    expect(lapChipStatus(meta)).toEqual({ kind: "screened", text: "Not used in Run 1 pace fit · close traffic" });
    expect(lapChipStatus({ ...meta, complete: false })?.kind).toBe("partial");
    expect(lapChipStatus({ ...meta, comparison_eligible: true, exclusion_reasons: [] })).toBeNull();
    for (const text of [screenedText(lap), lapLabel(lap), lapChipStatus(meta)!.text]) expect(text).not.toMatch(/excl/i);
  });

  it("plots caution laps as their own dots and keeps them off the pace dots", () => {
    const { pace, caution } = chartLaps(inv.laps);
    expect(caution.map((lap) => lap.lap)).toEqual([41, 42, 43]);
    expect(caution[0]!.lapTimeS).toBe(38.2);
    expect(pace.some((lap) => lap.flagState === "caution")).toBe(false);
  });
});

describe("runs: the fit confined to its span, run cards (round 2, #28f)", () => {
  it("clips the fit to the backend's domain and labels it with the backend's slope and eligible count", () => {
    const run = inv.runs.find((item) => item.runNumber === 1)!;
    const segment = fitSegment(run)!;
    expect(segment.x1).toBe(3);
    expect(segment.x2).toBe(24);
    expect(segment.y1).toBeCloseTo(30.04 + 0.033 * 2, 9);
    expect(segment.y2).toBeCloseTo(30.04 + 0.033 * 23, 9);
    expect(fitLabel(run)).toBe("+0.033 s/lap · 18 laps");
    expect(fitLabel(inv.runs.find((item) => item.runNumber === 2)!)).toBe("+0.029 s/lap · 21 laps");
    const none = inv.runs.find((item) => item.runNumber === 3)!;
    expect(fitSegment(none)).toBeNull();
    expect(fitLabel(none)).toBeNull();
  });

  it("builds one card per run with used and screened counts, the fuel band as a range, and tire laps", () => {
    const cards = runCards(inv, analysis.runs.runs as unknown[]);
    expect(cards.map((card) => card.runNumber)).toEqual([1, 2, 3]);
    expect(cards[0]!.slope).toBe("+0.033 s/lap · 18 laps");
    expect(cards[2]!.slope).toBeNull();
    expect(cards[2]!.noFitReason).toBe("insufficient_timed_clean_laps");
    expect(cards[0]!.fuelBand).toBe(`${(60 / 3.785411784).toFixed(1)} → ${(14.3 / 3.785411784).toFixed(1)} gal`);
    expect(cards[1]!.tireLaps).toBe("25–52");
    expect(cards[0]!.tireLaps).toBe("27");
    expect(cards[0]!.screened).toBeGreaterThanOrEqual(2);
    expect(runCards(inv, [])[0]).toMatchObject({ fuelBand: null, tireLaps: null });
    expect(sparkPath(cards[0]!.trend, 96, 24)).toMatch(/^M/);
  });

  it("colours runs from the ordered palette", () => {
    expect(runColor(1)).toBe(runColour(1));
    expect(runColor(2)).toBe("var(--run-2)");
    expect(runColor(null)).toBe("var(--color-text-muted)");
  });
});

describe("Traces: lap colour is race progress over the full phase (round 2, #28h)", () => {
  it("runs start to finish and never depends on a selection or filter", () => {
    const colors = progressColors([1, 2, 3, 4, 5]);
    expect(colors.get(1)).toBe(raceProgressColour(0, 5));
    expect(colors.get(5)).toBe(raceProgressColour(4, 5));
    expect(progressColors([5, 4, 3, 2, 1])).toEqual(colors);
  });

  it("bands the gradient by the colour-group budget so paths stay bounded", () => {
    const laps = Array.from({ length: 82 }, (_, i) => i + 1);
    const colors = progressColors(laps, 20);
    expect(new Set(colors.values()).size).toBeLessThanOrEqual(20);
    expect(colors.get(1)).toBe(raceProgressColour(0, 2));
    expect(colors.get(82)).toBe(raceProgressColour(1, 2));
  });

  it("shows the endpoints of the full phase", () => {
    const data = createTraceData(analysis.catalog, decodeCcol(analysis.ccol));
    expect(lapProgressEnds(data)).toEqual({ first: { lap: 1, color: data.colors.get(1) }, last: { lap: 82, color: data.colors.get(82) } });
  });
});

describe("pit stops: unreported repair and penalty fields", () => {
  it("say so once, in words", () => {
    expect(PIT_UNEXTRACTED).toBe("Repairs and penalties: not extracted");
  });
});

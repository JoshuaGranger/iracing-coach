import { describe, expect, it } from "vitest";
import { raceProgressColour, runColour } from "../../core/palette";
import { buildRound2Analysis, ROUND2_COMPARISONS } from "../fixtures/round2";
import { createTraceData, lapProgressEnds, progressColors } from "../geometry/trace-model";
import { decodeCcol } from "../ccol";
import { parseInvestigation, type Comparison } from "../investigation-model";
import { runColor } from "../format";
import * as comparisonModel from "../ui/comparison-model";
import { comparisonLaps, comparisonName, lapRanges, rangesText, sectionTone } from "../ui/comparison-model";
import { lapChipStatus } from "../ui/LapRail";
import { chartLaps, fitStatus, lapLabel } from "../ui/RaceView";
import { observedTrend, runCards, sparkGeometry, trendLabel } from "../ui/runs-model";
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
});

describe("no earlier/later drawing is left in the investigation (round 4, #66)", () => {
  it("keeps no group marks, paired bars or direction helpers", () => {
    for (const name of ["groupMap", "groupOf", "pairedMetrics", "direction", "noResultLine", "deltaText", "niceMax"]) expect(name in comparisonModel, name).toBe(false);
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
    // A lap screened for close traffic alone is drawn as a traffic lap and reads out its traffic (#67, traffic-marker.test.ts); the
    // fit status still names the reason. A lap screened for anything else keeps the hollow dot and this readout.
    expect(fitStatus(lap)).toBe("Not in pace fit · close traffic");
    const restart = inv.laps.find((item) => item.lap === 29)!;
    expect(fitStatus(restart)).toBe("Not in pace fit · restart");
    expect(lapLabel(restart)).toBe(`Lap 29 · ${restart.lapTimeS!.toFixed(3)} s · P4 · Run 2 · 1.0 green · Not in pace fit · restart`);
    expect(fitStatus(inv.laps.find((item) => item.lap === 12)!)).toBeNull();
    const meta = (analysis.catalog.laps as TraceLapData[]).find((item) => item.lap === 10)!;
    expect(lapChipStatus(meta)).toEqual({ kind: "screened", text: "Not used in Run 1 pace fit · close traffic" });
    expect(lapChipStatus({ ...meta, complete: false })?.kind).toBe("partial");
    expect(lapChipStatus({ ...meta, comparison_eligible: true, exclusion_reasons: [] })).toBeNull();
    for (const text of [fitStatus(lap)!, lapLabel(lap), lapLabel(restart), lapChipStatus(meta)!.text]) expect(text).not.toMatch(/excl/i);
  });

  it("plots caution laps as their own dots and keeps them off the pace dots", () => {
    const { pace, caution } = chartLaps(inv.laps);
    expect(caution.map((lap) => lap.lap)).toEqual([41, 42, 43]);
    expect(caution[0]!.lapTimeS).toBe(38.2);
    expect(pace.some((lap) => lap.flagState === "caution")).toBe(false);
  });
});

describe("runs: the observed trend, run cards (round 2, #28f; round 6, #90 #93)", () => {
  it("reads the server's observed trend per run and labels it with its slope and laps", () => {
    const runs = analysis.runs.runs as unknown[];
    const trend = observedTrend(runs, 1)!;
    expect(trend.firstLap).toBeLessThan(trend.lastLap);
    expect(trendLabel(trend)).toMatch(/^[+−]?\d\.\d\d s\/lap · \d+ laps$/);
    expect(observedTrend(runs, 9)).toBeNull();
    expect(observedTrend([{ run_number: 1, observed_trend: null }], 1)).toBeNull();
    expect(observedTrend([{ run_number: 1, observed_trend: { slope_s_per_lap: 0.1, intercept_s: 30, first_lap: 5, last_lap: 5, laps_used: 3 } }], 1)).toBeNull();
  });

  it("builds one card per run with used and screened counts, the fuel band as a range, and tire laps", () => {
    const cards = runCards(inv, analysis.runs.runs as unknown[]);
    expect(cards.map((card) => card.runNumber)).toEqual([1, 2, 3]);
    const runs = analysis.runs.runs as unknown[];
    expect(cards.map((card) => card.slope)).toEqual([1, 2, 3].map((run) => trendLabel(observedTrend(runs, run)!)));
    expect(cards[0]!.fuelBand).toBe(`${(60 / 3.785411784).toFixed(1)} → ${(14.3 / 3.785411784).toFixed(1)} gal`);
    expect(cards[1]!.tireLaps).toBe("25–52");
    expect(cards[0]!.tireLaps).toBe("27");
    expect(cards[0]!.screened).toBeGreaterThanOrEqual(2);
    expect(runCards(inv, [])[0]).toMatchObject({ fuelBand: null, tireLaps: null });
    const spark = sparkGeometry(cards[0]!, 96, 24);
    expect(spark.dots.length).toBe(cards[0]!.points.length);
    expect(spark.fit).not.toBeNull();
    for (const dot of spark.dots) {
      expect(dot.cx).toBeGreaterThanOrEqual(0);
      expect(dot.cx).toBeLessThanOrEqual(96);
      expect(dot.cy).toBeGreaterThanOrEqual(0);
      expect(dot.cy).toBeLessThanOrEqual(24);
    }
    expect(sparkGeometry({ ...cards[2]!, fit: null }, 96, 24).fit).toBeNull();
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

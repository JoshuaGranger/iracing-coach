import { describe, expect, it } from "vitest";
import { fixtureTrend } from "../fixtures/trend";
import type { InvestigationLap, RunView } from "../investigation-model";
import { observedTrend, runCards, runLineParts, trendAt, trendHover, trendLabel, trendOnExposure, trendText } from "../ui/runs-model";

/** Round 6, #90 #91 #93: the server's observed trend per run, read and drawn, never fitted in the browser. */

const runView = (runNumber: number, firstLap: number, lastLap: number): RunView => ({
  runNumber, plottedLapCount: 0, firstLap, lastLap, fitUnavailableReason: null,
  trend: { method: null, coveredLapCount: 0, segments: [], gaps: [] }, fit: null,
});
const lap = (n: number, time: number, over: Partial<InvestigationLap> = {}): InvestigationLap => ({
  lap: n, lapTimeS: time, position: null, flagState: "green", eligible: false, exclusionReasons: ["close_traffic"], runNumber: 1, plotPace: true, greenExposure: n - 9, ...over,
});

/**
 * Backlog #91: the 2026-10-02 Las Vegas win, run 1, laps 10–27 (the recorded times). Lap 10 is the restart after the opening caution,
 * the run's slowest lap; then the laps slow from about 30.9 to 32.0 s. Lap 14 carried a 2x incident.
 */
const VEGAS: [number, number][] = [
  [10, 33.686], [11, 30.906], [12, 30.867], [13, 30.961], [14, 31.056], [15, 31.091], [16, 30.932], [17, 31.322], [18, 31.218],
  [19, 31.383], [20, 31.463], [21, 31.682], [22, 31.691], [23, 31.638], [24, 31.977], [25, 31.803], [26, 31.768], [27, 32.068],
];
const vegasRows = VEGAS.map(([n, t]) => ({
  lap: n, run_number: 1, lap_time_s: t, complete: true, flag_state: "green", pit_time_s: 0, pit_in: false, pit_out: false,
  exclusion_reasons: n === 10 ? ["close_traffic", "restart"] : ["close_traffic"],
}));

describe("#91: a slowing run reads as slowing", () => {
  it("the old first-to-last fall-off was negative because of the restart lap", () => {
    const [first, last] = [VEGAS[0]!, VEGAS[VEGAS.length - 1]!];
    expect(((last[1] - first[1]) / (last[0] - first[0])).toFixed(2)).toBe("-0.10");
  });

  it("the server rule (as the fixture mirrors it) leaves the restart and incident laps out and gives the Rust test's vector", () => {
    // crates/coach-views/src/trend.rs `the_restart_lap_does_not_turn_a_slowing_run_negative`: the same numbers.
    const trend = fixtureTrend(vegasRows, 1, new Set([14]))!;
    expect(trend).toEqual({ slope_s_per_lap: 0.0756, intercept_s: 29.9622, first_lap: 11, last_lap: 27, laps_used: 16 });
  });

  it("the run line, the chart label and the card read the server's slope: +0.08 s/lap", () => {
    const runs = [{ run_number: 1, observed_trend: fixtureTrend(vegasRows, 1, new Set([14])) }];
    const trend = observedTrend(runs, 1)!;
    expect(trendText(trend)).toBe("+0.08 s/lap");
    expect(trendLabel(trend)).toBe("+0.08 s/lap · 16 laps");
    expect(trendHover(trend)).toBe("observed trend · L11–27 · 16 laps, traffic included");
    const laps = VEGAS.map(([n, t]) => lap(n, t));
    const parts = runLineParts(runView(1, 10, 27), undefined, runs, laps);
    expect(parts.find((part) => part.key === "falloff")!.text).toBe("fall-off +0.08 s/lap");
    expect(parts.map((part) => part.text).join(" · ")).toBe("Run 1 · L10–27 · fall-off +0.08 s/lap");
  });
});

describe("#90: the line is the server's, confined to its laps", () => {
  const runs = [{ run_number: 1, observed_trend: { slope_s_per_lap: 0.05, intercept_s: 30, first_lap: 11, last_lap: 27, laps_used: 16 } }];
  const trend = observedTrend(runs, 1)!;

  it("evaluates the line at its first and last counted laps", () => {
    expect(trendAt(trend, 11)).toBeCloseTo(30.55, 9);
    expect(trendAt(trend, 27)).toBeCloseTo(31.35, 9);
  });

  it("places the Runs chart's ends at the green exposure of those laps; no line without them", () => {
    const laps = VEGAS.map(([n, t]) => lap(n, t));
    expect(trendOnExposure(trend, laps)).toEqual({ x1: 2, y1: trendAt(trend, 11), x2: 18, y2: trendAt(trend, 27) });
    expect(trendOnExposure(trend, laps.map((item) => (item.lap === 27 ? { ...item, greenExposure: null } : item)))).toBeNull();
  });

  it("signs a gain with a true minus and a flat run without a sign", () => {
    const at = (slope: number) => trendText({ ...trend, slopeSPerLap: slope });
    expect(at(-0.031)).toBe("−0.03 s/lap");
    expect(at(0.0004)).toBe("0.00 s/lap");
  });

  it("a card's Fit is the trend's slope and laps; the fall-off figure is gone (#93)", () => {
    const laps = VEGAS.map(([n, t]) => lap(n, t));
    const inv = { runs: [runView(1, 10, 27)], laps } as unknown as Parameters<typeof runCards>[0];
    const [card] = runCards(inv, runs);
    expect(card!.slope).toBe("+0.05 s/lap · 16 laps");
    expect(card!.slopeHover).toBe("observed trend · L11–27 · 16 laps, traffic included");
    expect(card).not.toHaveProperty("fallOff");
  });
});

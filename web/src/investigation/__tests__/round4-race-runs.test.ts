import { describe, expect, it } from "vitest";
import { buildRound4Analysis, ROUND4_NO_FUEL_LAP, ROUND4_SERVICE_LAP } from "../fixtures/round4";
import { parseInvestigation, type InvestigationLap, type RunView } from "../investigation-model";
import { fuelPath, lapContexts, layersReadout, refills, seriesSegments, type SeriesPoint } from "../ui/race-layers";
import { fallOff, fallOffHover, fallOffText, fitDrawn, greenLaps, perLapText, runCards, runIdentities, runLine, runLineParts, runLineReadout, runSpan } from "../ui/runs-model";

/** Round 4, Race and Runs (#59a, #60–#63): observed fall-off, the fit's coverage rule, the fuel line. */

const lap = (n: number, time: number | null, over: Partial<InvestigationLap> = {}): InvestigationLap => ({
  lap: n, lapTimeS: time, position: null, flagState: "green", eligible: true, exclusionReasons: [], runNumber: 1, plotPace: true, greenExposure: n, ...over,
});
const run = (fit: RunView["fit"]): RunView => ({
  runNumber: 1, plottedLapCount: 0, firstLap: 1, lastLap: 30, fitUnavailableReason: null,
  trend: { method: null, coveredLapCount: 0, segments: [], gaps: [] }, fit,
});
const fit = (eligibleCount: number): RunView["fit"] => ({ method: "ols", eligibleCount, slopeSPerGreenLap: 0.05, domain: [1, 20], points: [{ x: 1, y: 30 }, { x: 20, y: 31 }], laps: [] });

describe("observed fall-off per run (#59a)", () => {
  // Laps 3–24: green from 3, a close-traffic lap screened out of the fit (still observed), a caution lap and a pit lap that are not
  // green-pace laps, a lap of another run.
  const laps = [
    lap(1, 31.9, { plotPace: false, flagState: "caution" }),
    lap(2, 30.6, { plotPace: false }),
    lap(3, 29.80),
    lap(4, 29.85, { eligible: false, exclusionReasons: ["close_traffic"] }),
    lap(12, 30.10, { eligible: false, exclusionReasons: ["close_traffic"] }),
    lap(13, 38.2, { plotPace: false, flagState: "caution" }),
    lap(24, 31.27, { eligible: false, exclusionReasons: ["close_traffic"] }),
    lap(25, 44.0, { plotPace: false }),
    lap(29, 29.5, { runNumber: 2 }),
  ];

  it("is the last green lap less the first, over the laps between, traffic laps included", () => {
    const f = fallOff({ runNumber: 1 }, laps)!;
    expect(f.first.lap).toBe(3);
    expect(f.last.lap).toBe(24);
    expect(f.greenLaps).toBe(4);
    expect(f.sPerLap).toBeCloseTo((31.27 - 29.8) / 21, 12);
    expect(fallOffText(f)).toBe("fall-off +0.07 s/lap");
    expect(fallOffHover(f)).toBe("observed · L3 29.800 s → L24 31.270 s");
    expect(runSpan(run(null), laps)).toBe("L3–24");
  });

  it("is null under three green laps and signs a gain with a minus", () => {
    expect(fallOff({ runNumber: 1 }, laps.slice(0, 4))).toBeNull();
    expect(fallOff({ runNumber: 2 }, laps)).toBeNull();
    expect(perLapText(-0.024)).toBe("−0.02 s/lap");
    expect(perLapText(0.0004)).toBe("0.00 s/lap");
    expect(greenLaps({ runNumber: 1 }, laps).map((item) => item.lap)).toEqual([3, 4, 12, 24]);
  });
});

describe("the screened fit is drawn only at half the run's green laps (#59a)", () => {
  const twenty = Array.from({ length: 20 }, (_, i) => lap(i + 1, 30 + i * 0.01));
  it("draws at 10 of 20 and not at 9 of 20; never without a fit", () => {
    expect(fitDrawn(run(fit(10)), twenty)).toBe(true);
    expect(fitDrawn(run(fit(9)), twenty)).toBe(false);
    expect(fitDrawn(run(null), twenty)).toBe(false);
    expect(fitDrawn(run(fit(5)), [])).toBe(false);
  });
});

describe("the fuel line (#63): the 2026-10-02 Las Vegas shape around the stop", () => {
  // Fuel at each lap's start, gal. Laps 25–28 under caution; lap 26 is the service lap (in no run), so lap 27 starts on the refilled
  // tank; lap 30 has no reading. Before round 4 the line broke at every run change and refill: 22–25 | 26 | 27–29 | 31.
  const reading: [number, number | null][] = [[22, 6.1], [23, 5.7], [24, 5.3], [25, 5.0], [26, 4.8], [27, 17.9], [28, 17.7], [29, 17.4], [30, null], [31, 16.6]];
  const points: SeriesPoint[] = reading.filter((r): r is [number, number] => r[1] !== null).map(([n, value]) => ({ lap: n, value }));

  it("runs through every lap with a reading and breaks only where the reading is absent", () => {
    expect(seriesSegments(points).map((segment) => segment.map((p) => p.lap))).toEqual([[22, 23, 24, 25, 26, 27, 28, 29], [31]]);
    expect(refills(seriesSegments(points)[0]!).map((p) => p.lap)).toEqual([27]);
  });

  it("draws the refill as a step up at the lap boundary, never a gap", () => {
    const x = (n: number): number => n * 10;
    const y = (value: number): number => 100 - value;
    const d = fuelPath(seriesSegments(points)[0]!, x, y);
    expect(d.match(/M/g)).toHaveLength(1);
    expect(d).toBe("M220,93.9L230,94.3L240,94.7L250,95L260,95.2L265,95.2L265,82.1L270,82.1L280,82.3L290,82.6");
  });

  it("reads a lap without a reading as `no reading`", () => {
    expect(layersReadout(["fuel"], { lap: 30, trackTempF: null, usagePct: null, fuelGal: null })).toEqual(["Fuel no reading"]);
  });
});

describe("the round-4 fixture end to end through the models", () => {
  const analysis = buildRound4Analysis();
  const inv = parseInvestigation((analysis.investigation as { race_investigation: unknown }).race_investigation);
  const runs = analysis.runs.runs as unknown[];
  const contexts = lapContexts(analysis.race);

  it("draws one fuel line from the start to the absent reading and one after, through the service lap", () => {
    expect(contexts.get(ROUND4_NO_FUEL_LAP)!.fuelGal).toBeNull();
    const fuels = inv.laps.map((item) => ({ lap: item.lap, value: contexts.get(item.lap)?.fuelGal ?? null })).filter((p): p is SeriesPoint => p.value !== null);
    const segments = seriesSegments(fuels);
    expect(segments.map((segment) => [segment[0]!.lap, segment[segment.length - 1]!.lap])).toEqual([[1, ROUND4_NO_FUEL_LAP - 1], [ROUND4_NO_FUEL_LAP + 1, 82]]);
    expect(segments[0]!.some((p) => p.lap === ROUND4_SERVICE_LAP)).toBe(true);
    expect(refills(segments[0]!).map((p) => p.lap)).toEqual([29]);
    expect(refills(segments[1]!).map((p) => p.lap)).toEqual([57]);
  });

  it("writes `Run · green span · fall-off · burn` and draws only the fit that covers half the green laps", () => {
    const ids = runIdentities([1, 2, 3], []);
    for (const item of inv.runs) {
      const green = inv.laps.filter((l) => l.runNumber === item.runNumber && l.plotPace && (l.lapTimeS ?? 0) > 0).sort((a, b) => a.lap - b.lap);
      const [first, last] = [green[0]!, green[green.length - 1]!];
      const rate = ((last.lapTimeS as number) - (first.lapTimeS as number)) / (last.lap - first.lap);
      const burn = item.runNumber === 3 ? "0.43" : "0.42";
      expect(runLine(item, ids.get(item.runNumber), runs, inv.laps)).toBe(`Run ${item.runNumber} · L${first.lap}–${last.lap} · fall-off ${perLapText(rate)} · ${burn} gal/lap`);
    }
    const parts = runLineParts(inv.runs[0]!, ids.get(1), runs, inv.laps);
    expect(parts.find((part) => part.key === "falloff")!.hover).toMatch(/^observed · L\d+ \d+\.\d{3} s → L\d+ \d+\.\d{3} s$/);
    expect(runLineReadout(parts)).toMatch(/^Run 1 · fall-off [+−]?\d\.\d\d s\/lap · observed · L\d+ .* · 0\.42 gal\/lap$/);
    expect(inv.runs.map((item) => fitDrawn(item, inv.laps))).toEqual([true, false, false]);
    const cards = runCards(inv, runs);
    expect(cards.map((card) => card.slope)).toEqual(["+0.033 s/lap · 18 laps", null, null]);
    expect(cards.map((card) => card.fit !== null)).toEqual([true, false, false]);
    expect(cards.every((card) => card.fallOff !== null)).toBe(true);
    // No word about why a fit is not drawn.
    for (const card of cards) expect(JSON.stringify(card)).not.toMatch(/insufficient|no fit|coverage|screened fit/i);
  });

  it("leaves no `Pace fit` header text in the model", async () => {
    const model = await import("../ui/runs-model");
    expect("paceFitText" in model).toBe(false);
  });
});

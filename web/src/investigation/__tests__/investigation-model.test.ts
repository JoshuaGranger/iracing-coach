import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { hasPaceResult, InvestigationFormatError, paceTone, parseInvestigation, selectedComparison, selectedSection } from "../investigation-model";
import { hypothesisStatus, lapsText, lapTime, passRole, reasonsText, signedText, ticks } from "../format";

const fixture = JSON.parse(readFileSync(new URL("../../../../test-data/race-investigation.json", import.meta.url), "utf8")) as Record<string, unknown>;

describe("race_investigation fixture (schema 2, sections-trials-v7)", () => {
  const inv = parseInvestigation(fixture);

  it("reads comparisons, cohorts, metrics, corners and actions", () => {
    expect(inv.comparisons.length).toBe(2);
    expect(inv.defaultComparisonId).toBe("run-1-clean");
    const first = inv.comparisons[0]!;
    expect(first.baseline.lapNumbers).toEqual([1, 2, 3]);
    expect(first.compared.greenAgeStart).toBe(7);
    expect(first.metrics.find((metric) => metric.key === "pace")?.delta).toBeCloseTo(0.12, 3);
    expect(first.corners[0]!.id).toBe("A");
    expect(first.actions[0]!.sectionId).toBe("A");
    expect(first.actions[0]!.priority).toBe(1);
  });

  it("reads pass pairs with rows and the steering declaration", () => {
    const pair = inv.comparisons[0]!.inspection!.pairs.get("A")!;
    expect(pair.rows.length).toBeGreaterThan(5);
    expect(pair.rows[0]!.a.speed).toBe(100);
    expect(pair.steering.comparable).toBe(true);
    expect(pair.steering.degreeDigits).toBe(2);
    expect(pair.a.role).toBe("baseline_median");
    expect(pair.timingComplete).toBeTypeOf("boolean");
  });

  it("keeps observed trend segments and the screened fit separate", () => {
    const run = inv.runs[0]!;
    expect(run.trend.method).toBe("centered_five_observation_median");
    expect(run.trend.segments[0]!.length).toBeGreaterThan(5);
    expect(run.fit!.method).toBe("ordinary_least_squares");
    expect(run.fit!.points.length).toBe(2);
    expect(run.fit!.domain).toEqual([1, 9]);
  });

  it("reads laps, sections and track evolution", () => {
    expect(inv.laps[0]).toMatchObject({ lap: 1, plotPace: true, eligible: true, runNumber: 1 });
    expect(inv.sections.sections[0]).toMatchObject({ id: "A", startPct: 0.1, endPct: 0.4, boundaryStatus: "detected_section" });
    expect(inv.trackEvolution).not.toBeNull();
    expect(inv.steeringBasis?.basis).toBe("road_wheel");
  });

  it("selects the default comparison and the lead action's section", () => {
    const c = selectedComparison(inv, null)!;
    expect(c.id).toBe("run-1-clean");
    expect(selectedComparison(inv, "missing")!.id).toBe("run-1-clean");
    expect(selectedSection(inv, c, null)!.id).toBe("A");
    expect(selectedSection(inv, c, "nope")!.id).toBe("A");
  });

  it("withholds an unusable pace result instead of showing a gain", () => {
    const unusable = { ...inv.comparisons[0]!, paceComparable: false };
    expect(hasPaceResult(unusable)).toBe(false);
    expect(paceTone(unusable)).toBe("neutral");
    expect(paceTone({ ...inv.comparisons[0]!, deltaS: 0.1 })).toBe("loss");
    expect(paceTone({ ...inv.comparisons[0]!, deltaS: -0.1 })).toBe("gain");
    expect(paceTone({ ...inv.comparisons[0]!, deltaS: null })).toBe("neutral");
  });
});

describe("race_investigation failures are clear", () => {
  it("rejects an incompatible schema version", () => {
    expect(() => parseInvestigation({ ...fixture, schema_version: 3 })).toThrow(InvestigationFormatError);
    expect(() => parseInvestigation({ ...fixture, schema_version: 3 })).toThrow(/schema 3 is not supported/);
  });
  it("rejects missing required fields", () => {
    const { comparisons: _omit, ...rest } = fixture;
    void _omit;
    expect(() => parseInvestigation(rest)).toThrow(/comparisons/);
    expect(() => parseInvestigation({})).toThrow(/empty/);
    expect(() => parseInvestigation({ ...fixture, comparisons: [{ title: "x" }] })).toThrow(/"id"/);
  });
  it("preserves what it does not read (unknown fields do not break parsing)", () => {
    expect(parseInvestigation({ ...fixture, future_field: { a: 1 } }).comparisons.length).toBe(2);
  });
});

describe("presentation helpers", () => {
  it("formats lap times, signed deltas, lap lists and reasons", () => {
    expect(lapTime(95.1234)).toBe("1:35.123");
    expect(lapTime(30.04)).toBe("30.040");
    expect(lapTime(null)).toBe("—");
    expect(signedText(0.036)).toBe("+0.036");
    expect(signedText(-0.2)).toBe("−0.200");
    expect(signedText(0)).toBe("0.000");
    expect(lapsText([37, 35, 36])).toBe("35, 36, 37");
    expect(lapsText([116, 117, 118, 119, 120])).toBe("116–120");
    expect(lapsText([])).toBe("no laps");
    expect(reasonsText(["close_traffic", "pit", "close_traffic"])).toBe("close traffic, pit");
    expect(passRole("baseline_median")).toBe("Earlier group median");
    expect(hypothesisStatus("supported")).toBe("Observed change");
    expect(hypothesisStatus("???")).toBe("Unresolved");
    expect(ticks(30, 31, 4)).toEqual([30, 30.25, 30.5, 30.75, 31]);
  });
});

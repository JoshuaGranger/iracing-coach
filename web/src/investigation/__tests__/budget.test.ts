import { describe, expect, it } from "vitest";
import { boundedCursorLaps, colorGroupCount, pointBudgetPerTrace, representativeIndices, roundHalfEven } from "../geometry/budget";

describe("render budgets (race-analysis-geometry-and-performance.md reference table)", () => {
  const laps = [1, 3, 20, 82, 500];
  it("colour groups: 1/3/20/20/20", () => {
    expect(laps.map((count) => colorGroupCount(count))).toEqual([1, 3, 20, 20, 20]);
  });
  it("detailed cursor traces: 1/3/20/24/24", () => {
    expect(laps.map((count) => boundedCursorLaps(Array.from({ length: count }, (_, i) => i + 1), null, null, 24).length)).toEqual([1, 3, 20, 24, 24]);
  });
  it("points per background lap at a 400 px plot: 500/500/500/500/96", () => {
    expect(laps.map((count) => pointBudgetPerTrace(count, 400))).toEqual([500, 500, 500, 500, 96]);
  });
  it("a focused lap gets the screen budget, clamped to 64..1200", () => {
    expect(pointBudgetPerTrace(500, 400, true)).toBe(500);
    expect(pointBudgetPerTrace(500, 10, true)).toBe(64);
    expect(pointBudgetPerTrace(1, 5000, true)).toBe(1200);
    expect(pointBudgetPerTrace(100000, 400)).toBe(48);
  });
  it("representative indices span both ends, ascend and never repeat", () => {
    const picked = representativeIndices(160, 20);
    expect(picked[0]).toBe(0);
    expect(picked[picked.length - 1]).toBe(159);
    expect(new Set(picked).size).toBe(picked.length);
    expect([...picked].sort((a, b) => a - b)).toEqual(picked);
    expect(representativeIndices(5, 10)).toEqual([0, 1, 2, 3, 4]);
    expect(representativeIndices(10, 1)).toEqual([0]);
    expect(representativeIndices(0, 4)).toEqual([]);
  });
  it("rounds half to even like Math.Round", () => {
    expect([0.5, 1.5, 2.5, 3.5, 2.4, 2.6].map(roundHalfEven)).toEqual([0, 2, 2, 4, 2, 3]);
  });
  it("keeps the spotlight, reference, first and last laps in the bounded cursor set", () => {
    const selected = Array.from({ length: 82 }, (_, i) => i + 1);
    const picked = boundedCursorLaps(selected, 37, 50, 24);
    expect(picked.length).toBe(24);
    for (const lap of [37, 50, 1, 82]) expect(picked).toContain(lap);
    expect(picked[0]).toBe(37);
  });
});

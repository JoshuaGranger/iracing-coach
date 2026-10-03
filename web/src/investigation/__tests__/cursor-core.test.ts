import { describe, expect, it } from "vitest";
import { clamp, directionalDegrees, formatSignalValue, nearestIndex, projectedTrackFraction, signed, tooltipCapacityFor, tooltipX } from "../cursor/cursor-core";
import { mapPointAt, type MapPoint } from "../geometry/map-model";

describe("cursor core", () => {
  it("finds the nearest bin on an ascending grid", () => {
    const grid = Float64Array.from({ length: 5 }, (_, i) => i / 4);
    expect([-1, 0, 0.1, 0.13, 0.37, 0.38, 0.99, 2].map((f) => nearestIndex(grid, f))).toEqual([0, 0, 0, 1, 1, 2, 4, 4]);
    expect(nearestIndex([], 0.5)).toBe(-1);
  });

  it("formats the catalogue signals like the C# cursor", () => {
    const f = (id: string, value: number, label = false, digits: number | null = null, unit = ""): string =>
      formatSignalValue({ id, shortLabel: id.slice(0, 1).toUpperCase(), unit, digits }, value, label);
    expect(f("speed", 123.456)).toBe("123.5 mph");
    expect(f("throttle", 0.456)).toBe("46%");
    expect(f("brake", 1, true)).toBe("B 100%");
    expect(f("delta", 0.0123)).toBe("+0.012 s");
    expect(f("delta", -0.0004)).toBe("0.000 s");
    expect(f("gear", 3.2)).toBe("3");
    expect(f("rpm", 7123.4)).toBe("7123 rpm");
    expect(f("slip", -2.34)).toBe("-2.3°");
    expect(f("lateral-g", 1.234)).toBe("+1.23 g");
    expect(f("steering", 12.345, false, 2)).toBe("Left 12.35°");
    expect(f("steering", -3.2, true, 1)).toBe("S R 3.2°");
    expect(f("steering", 0, false, 1)).toBe("Center 0°");
    expect(f("x", 1, false, null, "on / off")).toBe("On");
    expect(f("x", 12.34, false, null, "deg F")).toBe("12.3 °F");
    expect(signed(0.0001, 3)).toBe("0.000");
    expect(directionalDegrees(-1, false, null)).toBe("Right 1.0°");
  });

  it("places the tooltip left of the cursor, then right, then clamped", () => {
    const base = { plotLeft: 6, plotWidth: 500, tooltipWidth: 100 };
    expect(tooltipX({ ...base, cursorX: 300 })).toBe(188);
    expect(tooltipX({ ...base, cursorX: 60 })).toBe(72);
    const clamped = tooltipX({ ...base, cursorX: 110, plotWidth: 130 });
    expect(clamped + 100).toBeLessThanOrEqual(6 + 130 - 4);
    expect(clamped).toBeGreaterThanOrEqual(10);
  });

  it("bounds visible tooltip slots by row height", () => {
    expect(tooltipCapacityFor(82, 20)).toBe(3);
    expect(tooltipCapacityFor(137, 20)).toBe(5);
    expect(tooltipCapacityFor(20, 20)).toBe(1);
    expect(clamp(5, 0, 3)).toBe(3);
  });

  it("maps a pointer on the loop to its lap fraction and wraps across the start line", () => {
    const loop: MapPoint[] = Array.from({ length: 100 }, (_, i) => ({ percent: i / 100, x: 100 + 80 * Math.cos((i / 100) * Math.PI * 2), y: 100 + 80 * Math.sin((i / 100) * Math.PI * 2) }));
    const p = mapPointAt(loop, 0.25)!;
    expect(p.x).toBeCloseTo(100, 6);
    expect(p.y).toBeCloseTo(180, 6);
    expect(projectedTrackFraction(loop, p.x, p.y + 3, 0)).toBeCloseTo(0.25, 2);
    expect(projectedTrackFraction(loop, 180.5, 100, 0.9)).toBeCloseTo(0, 2);
    expect(mapPointAt(loop, 1.25)!.x).toBeCloseTo(p.x, 6);
    expect(mapPointAt(loop, 0.995)!.percent).toBeCloseTo(0.995, 9);
    expect(projectedTrackFraction([], 0, 0, 0.4)).toBe(0.4);
  });
});

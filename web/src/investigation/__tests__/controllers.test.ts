import { readdirSync, readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { clampRatio, DEFAULT_RATIO, MAXIMUM_RATIO, MINIMUM_RATIO, ratioAtPointer, ratioForKey } from "../cursor/context-splitter";
import { fractionAtMapPosition, formatReading, nearestMapPoint, nearestRow, type InspectionRow } from "../cursor/race-inspection";
import { constrainView, createTrackMap, parseViewBox, zoomView } from "../cursor/track-map";

describe("context splitter rules", () => {
  it("clamps the track share to 1/3..2/3 with a 0.43 default", () => {
    expect(DEFAULT_RATIO).toBe(0.43);
    expect(clampRatio(0.1)).toBe(MINIMUM_RATIO);
    expect(clampRatio(0.9)).toBe(MAXIMUM_RATIO);
    expect(clampRatio(0.5)).toBe(0.5);
  });
  it("maps pointer position and keys to a ratio", () => {
    expect(ratioAtPointer(100, 500, 10, 350)).toBeCloseTo((350 - 100 - 5) / 490, 12);
    expect(ratioForKey(0.5, "ArrowUp")).toBeCloseTo(0.48, 12);
    expect(ratioForKey(0.5, "ArrowDown")).toBeCloseTo(0.52, 12);
    expect(ratioForKey(0.5, "Home")).toBe(MINIMUM_RATIO);
    expect(ratioForKey(0.5, "End")).toBe(MAXIMUM_RATIO);
    expect(ratioForKey(0.34, "ArrowUp")).toBe(MINIMUM_RATIO);
    expect(ratioForKey(0.5, "x")).toBeNull();
  });
});

describe("track map zoom", () => {
  const base = { x: 0, y: 0, width: 400, height: 200 };
  it("parses view boxes strictly", () => {
    expect(parseViewBox("0 0 420 280")).toEqual({ x: 0, y: 0, width: 420, height: 280 });
    expect(parseViewBox("1,2,3,4")).toEqual({ x: 1, y: 2, width: 3, height: 4 });
    expect(parseViewBox("0 0 0 10")).toBeNull();
    expect(parseViewBox("a b c d")).toBeNull();
    expect(parseViewBox(null)).toBeNull();
  });
  it("never zooms out past the base or in past 1/8, and keeps the aspect", () => {
    expect(constrainView(base, { x: -50, y: -50, width: 900, height: 450 })).toEqual(base);
    const tight = constrainView(base, { x: 0, y: 0, width: 1, height: 1 });
    expect(tight.width).toBe(50);
    expect(tight.height).toBe(25);
  });
  it("keeps the anchor fixed on screen when zooming", () => {
    const anchor = { x: 300, y: 100 };
    const next = zoomView(base, base, anchor, 0.5);
    expect(next.width).toBe(200);
    expect((anchor.x - next.x) / next.width).toBeCloseTo((anchor.x - base.x) / base.width, 12);
    expect((anchor.y - next.y) / next.height).toBeCloseTo((anchor.y - base.y) / base.height, 12);
  });
  it("keeps a panned view inside the base box", () => {
    const view = { x: 0, y: 0, width: 100, height: 50 };
    expect(constrainView(base, { ...view, x: 500, y: -10 })).toEqual({ x: 300, y: 0, width: 100, height: 50 });
  });
  it("wheel zoom synchronizes layered maps, Fit restores them, and disposal removes wheel handling", () => {
    const target = new EventTarget();
    const classes = new Set<string>();
    let box = { ...base };
    const updates: typeof base[] = [];
    const element = Object.assign(target, {
      dataset: {} as Record<string, string>,
      classList: { add: (key: string) => classes.add(key), remove: (key: string) => classes.delete(key) },
      viewBox: { get baseVal() { return box; } },
      getBoundingClientRect: () => ({ left: 0, top: 0, width: 400, height: 200 }),
      querySelectorAll: () => [],
      setAttribute: (_name: string, value: string) => { box = parseViewBox(value)!; },
    }) as unknown as SVGSVGElement;
    const controller = createTrackMap(element, "0 0 400 200", "race", (view) => updates.push(view));
    const wheel = () => {
      const event = new Event("wheel", { cancelable: true });
      Object.assign(event, { deltaY: -120, clientX: 200, clientY: 100 });
      target.dispatchEvent(event);
      return event;
    };
    expect(wheel().defaultPrevented).toBe(true);
    expect(controller.zoom).toBeGreaterThan(1);
    expect(updates.at(-1)?.width).toBeLessThan(400);
    controller.fit();
    expect(updates.at(-1)).toEqual(base);
    controller.dispose();
    const count = updates.length;
    expect(wheel().defaultPrevented).toBe(false);
    expect(updates).toHaveLength(count);
  });
});

describe("section inspection lookups", () => {
  const rows: InspectionRow[] = [0.1, 0.2, 0.3, 0.4].map((x) => ({ x, gap: x, a: { speed: x * 100 }, b: { speed: x * 90 } }));
  it("finds the nearest row, preferring the earlier on a tie", () => {
    expect(nearestRow(rows, 0.0)).toBe(0);
    expect(nearestRow(rows, 0.26)).toBe(2);
    expect(nearestRow(rows, 0.25)).toBe(1);
    expect(nearestRow(rows, 5)).toBe(3);
    expect(nearestRow([], 0.5)).toBe(-1);
  });
  it("only inspects the highlighted span from the map", () => {
    const points = [[0, 0, 0.1], [10, 0, 0.3], [20, 0, 0.9]] as const;
    expect(nearestMapPoint(points, 0.28)).toEqual([10, 0, 0.3]);
    expect(fractionAtMapPosition(points, 9, 1, 0.1, 0.5)).toBe(0.3);
    expect(fractionAtMapPosition(points, 19, 0, 0.1, 0.5)).toBeNull();
  });
  it("formats readings with a minus sign and signed gaps", () => {
    expect(formatReading(-3.456, 1, false)).toBe("−3.5");
    expect(formatReading(0.0361, 3, true)).toBe("+0.036");
    expect(formatReading(-0.0361, 3, true)).toBe("−0.036");
    expect(formatReading(0, 3, true)).toBe("0.000");
    expect(formatReading(null, 3, true)).toBe("—");
    expect(formatReading(Number.NaN, 1, false)).toBe("—");
  });
});

describe("pointer movement never reaches the network", () => {
  const dir = new URL("../cursor/", import.meta.url);
  const forbidden = /\b(fetch|XMLHttpRequest|WebSocket|EventSource|sendBeacon)\b\s*\(|new\s+(WebSocket|EventSource|XMLHttpRequest)|from\s+["']\.\.\/load["']|navigator\.sendBeacon/;
  for (const file of readdirSync(dir).filter((name) => name.endsWith(".ts"))) {
    it(`${file} has no request path`, () => {
      const source = readFileSync(new URL(file, dir), "utf8").replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");
      expect(source).not.toMatch(forbidden);
    });
  }
});

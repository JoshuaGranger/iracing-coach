import { describe, expect, it, vi } from "vitest";
import { silhouetteOf } from "./silhouette";
import { isCurrentCentreline } from "./track-silhouette";
import { lastTab, rememberTab } from "./tab-memory";
import { matches } from "../ui/multi-select";

function oval(n = 200): { x: number[]; y: number[] } {
  const x: number[] = [];
  const y: number[] = [];
  for (let i = 0; i < n; i++) {
    const t = (i / n) * Math.PI * 2;
    x.push(0.5 + 0.42 * Math.cos(t));
    y.push(0.5 + 0.28 * Math.sin(t));
  }
  return { x, y };
}

describe("track silhouette", () => {
  it("fits the main path to the box, thins it, closes a loop and puts a tick at the line", () => {
    const { x, y } = oval();
    const s = silhouetteOf(x, y)!;
    expect(s.width).toBe(118);
    expect(s.d.startsWith("M")).toBe(true);
    expect(s.d.endsWith("Z")).toBe(true);
    expect(s.d.split("L").length).toBeLessThanOrEqual(75);
    const nums = [...s.d.matchAll(/(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)/g)].map((m) => [Number(m[1]), Number(m[2])] as const);
    for (const [px, py] of nums) {
      expect(px).toBeGreaterThanOrEqual(0);
      expect(px).toBeLessThanOrEqual(118);
      expect(py).toBeGreaterThanOrEqual(0);
      expect(py).toBeLessThanOrEqual(78);
    }
    // the tick is perpendicular to the path at its start: the oval starts at its right end heading down, so the tick is horizontal
    const [x1, y1, x2, y2] = s.tick;
    expect(Math.abs(y1 - y2)).toBeLessThan(1);
    expect(Math.abs(x1 - x2)).toBeGreaterThan(8);
  });

  it("leaves an open path open", () => {
    const { x, y } = oval();
    const half = silhouetteOf(x.slice(0, 100), y.slice(0, 100))!;
    expect(half.d.endsWith("Z")).toBe(false);
  });

  it("keeps the track's proportions: a long narrow oval stays long and narrow", () => {
    const n = 200;
    const x = Array.from({ length: n }, (_, i) => 0.5 + 0.5 * Math.cos((i / n) * Math.PI * 2));
    const y = Array.from({ length: n }, (_, i) => 0.5 + 0.1 * Math.sin((i / n) * Math.PI * 2));
    const s = silhouetteOf(x, y)!;
    const nums = [...s.d.matchAll(/(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)/g)].map((m) => [Number(m[1]), Number(m[2])] as const);
    const w = Math.max(...nums.map((p) => p[0])) - Math.min(...nums.map((p) => p[0]));
    const h = Math.max(...nums.map((p) => p[1])) - Math.min(...nums.map((p) => p[1]));
    expect(w / h).toBeCloseTo(5, 0);
  });

  it("stores only the current centreline's outline", () => {
    expect(isCurrentCentreline({ transform: { source_bounds: { minimum_x: 0, maximum_x: 1, minimum_y: 0, maximum_y: 1 }, normalization_scale: 1, longitude_scale: 0.81 } })).toBe(true);
    expect(isCurrentCentreline({ transform: { source_bounds: { minimum_x: 0, maximum_x: 1, minimum_y: 0, maximum_y: 1 }, normalization_scale: 1 } })).toBe(false);
    expect(isCurrentCentreline({ transform: null })).toBe(false);
  });

  it("keeps outlines under key version 3, so an outline stored before the turn (#81) is fetched and drawn again", async () => {
    // A version-2 outline (stored before the outline was turned) and a fresh page load: the key is not read, the layout's geometry is
    // fetched once, and the turned outline is kept under version 3, which the next page load reads back without asking.
    const items = new Map<string, string>([["coach.silhouette.2.iowa", JSON.stringify({ d: "M0,0L1,1", tick: [0, 0, 1, 1], width: 118, height: 78 })]]);
    vi.stubGlobal("localStorage", { getItem: (key: string) => items.get(key) ?? null, setItem: (key: string, value: string) => void items.set(key, String(value)), removeItem: (key: string) => void items.delete(key) });
    const { x, y } = oval();
    const geometry = { status: "usable", main_path: { pct: x.map((_, i) => i / x.length), x, y }, transform: { source_bounds: { minimum_x: 0, maximum_x: 1, minimum_y: 0, maximum_y: 1 }, normalization_scale: 1, longitude_scale: 0.8 } };
    const fetched: string[] = [];
    vi.stubGlobal("fetch", async (url: string) => {
      fetched.push(String(url));
      return new Response(JSON.stringify(geometry), { status: 200, headers: { "content-type": "application/json" } });
    });
    try {
      vi.resetModules();
      const first = await import("./track-silhouette");
      expect(first.SILHOUETTE_STORE).toBe("coach.silhouette.3.");
      const drawn = await first.silhouetteFor("iowa", "an-1");
      expect(fetched).toEqual(["/api/v1/analyses/an-1/track"]);
      expect(drawn).toEqual(silhouetteOf(x, y));
      expect(JSON.parse(items.get("coach.silhouette.3.iowa")!)).toEqual(drawn);
      vi.resetModules();
      const next = await import("./track-silhouette");
      expect(await next.silhouetteFor("iowa", "an-1")).toEqual(drawn);
      expect(fetched).toHaveLength(1);
    } finally {
      vi.unstubAllGlobals();
      vi.resetModules();
    }
  });

  it("draws nothing without geometry that can be a circuit", () => {
    expect(silhouetteOf(undefined, undefined)).toBeNull();
    expect(silhouetteOf([0, 1], [0, 1])).toBeNull();
    expect(silhouetteOf(Array(20).fill(1), Array.from({ length: 20 }, (_, i) => i))).toBeNull();
    expect(silhouetteOf(Array.from({ length: 20 }, (_, i) => i), Array(20).fill(3))).toBeNull();
    const withGaps = oval(40);
    withGaps.x[3] = Number.NaN;
    expect(silhouetteOf(withGaps.x, withGaps.y)).not.toBeNull();
  });
});

describe("the tab a session was left on", () => {
  it("remembers per session and keeps the latest of each", () => {
    let m = rememberTab({}, 5, "traces");
    m = rememberTab(m, 6, "runs");
    m = rememberTab(m, 5, "race");
    expect(lastTab(m, 5)).toBe("race");
    expect(lastTab(m, 6)).toBe("runs");
    expect(lastTab(m, 7)).toBeNull();
  });
  it("forgets the oldest sessions past its limit", () => {
    let m = {};
    for (let i = 1; i <= 450; i++) m = rememberTab(m, i, "race");
    expect(Object.keys(m)).toHaveLength(400);
    expect(lastTab(m, 1)).toBeNull();
    expect(lastTab(m, 450)).toBe("race");
  });
  it("ignores a stored value that is not a tab", () => {
    expect(lastTab({ "3": "bogus" } as never, 3)).toBeNull();
  });
});

describe("search in a filter dropdown", () => {
  const options = [
    { key: "a", label: "Toyota Supra Class B" },
    { key: "b", label: "Global Mazda MX-5 Cup" },
    { key: "c", label: "NASCAR Xfinity Toyota Supra" },
  ];
  it("matches anywhere in the label, ignoring case", () => {
    expect(matches(options, "supra").map((o) => o.key)).toEqual(["a", "c"]);
    expect(matches(options, "  MX-5 ").map((o) => o.key)).toEqual(["b"]);
    expect(matches(options, "").map((o) => o.key)).toEqual(["a", "b", "c"]);
    expect(matches(options, "zzz")).toEqual([]);
  });
});

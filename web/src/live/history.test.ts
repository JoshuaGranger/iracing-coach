import { describe, expect, it } from "vitest";
import { CHANNELS, CHANNEL_INDEX } from "../gen/live-channels";
import type { LiveFrame } from "./frame";
import { HISTORY_CAPACITY, LiveHistory, TREND_WINDOW_SPECS, downsample } from "./history";

function f(time: number, speed: number | null, lap = 1, epoch = 1): LiveFrame {
  const values: (number | null)[] = Array(CHANNELS.length).fill(null);
  values[CHANNEL_INDEX.speed!] = speed;
  values[CHANNEL_INDEX.lap!] = lap;
  return { flags: 1, seq: 0, sessionEpoch: epoch, capturedUnixMs: 0, sessionTime: time, tick: 0, tickRate: 60, tableVersion: 1, channelCount: 45, values, cue: null };
}

describe("live history", () => {
  it("returns the time window ending at the newest frame", () => {
    const h = new LiveHistory();
    for (let i = 0; i <= 100; i++) h.push(f(i, i));
    const s = h.series("speed", TREND_WINDOW_SPECS.s15!);
    expect(s.t[0]).toBe(85);
    expect(s.t.at(-1)).toBe(100);
    expect(s.v.at(-1)).toBe(100);
  });

  it("windows by laps: the current lap and the ones before it", () => {
    const h = new LiveHistory();
    for (let i = 0; i < 90; i++) h.push(f(i, i, 1 + Math.floor(i / 30)));
    expect(h.series("speed", { laps: 1 }).t[0]).toBe(60);
    expect(h.series("speed", { laps: 2 }).t[0]).toBe(30);
    expect(h.series("speed", { laps: 3 }).t[0]).toBe(0);
  });

  it("keeps missing values as gaps and ignores channels never seen", () => {
    const h = new LiveHistory();
    h.push(f(0, 10));
    h.push(f(1, null));
    h.push(f(2, 12));
    const s = h.series("speed", { seconds: 30 });
    expect(Array.from(s.v).map((x) => (Number.isNaN(x) ? "gap" : x))).toEqual([10, "gap", 12]);
    expect(h.series("rpm", { seconds: 30 }).t).toHaveLength(0);
  });

  it("clears on a session epoch change and when time runs backwards", () => {
    const h = new LiveHistory();
    h.push(f(50, 1));
    h.push(f(51, 2));
    h.push(f(1, 3, 1, 2));
    expect(h.length).toBe(1);
    h.push(f(0.5, 4, 1, 2));
    expect(h.length).toBe(1);
  });

  it("is a ring: the oldest frames fall off at capacity", () => {
    const h = new LiveHistory();
    for (let i = 0; i < HISTORY_CAPACITY + 10; i++) h.push(f(i, i));
    expect(h.length).toBe(HISTORY_CAPACITY);
    const s = h.series("speed", { seconds: 1e9 });
    expect(s.t[0]).toBe(10);
    expect(s.t.at(-1)).toBe(HISTORY_CAPACITY + 9);
  });
});

describe("downsample", () => {
  it("keeps a one-sample spike that a stride would drop", () => {
    const n = 1000;
    const t = Float64Array.from({ length: n }, (_, i) => i);
    const v = Float32Array.from({ length: n }, (_, i) => (i === 503 ? 999 : 1));
    const cols = downsample({ t, v }, 0, n - 1, 50);
    expect(Math.max(...cols.map((c) => c.max))).toBe(999);
    expect(cols.length).toBeLessThanOrEqual(50);
  });

  it("skips NaN and returns nothing for an empty or inverted range", () => {
    const s = { t: Float64Array.of(0, 1, 2), v: Float32Array.of(Number.NaN, Number.NaN, 5) };
    expect(downsample(s, 0, 2, 10)).toEqual([{ x: 9, min: 5, max: 5 }]);
    expect(downsample(s, 2, 2, 10)).toEqual([]);
  });
});

import { describe, expect, it } from "vitest";
import { CHANNELS, CHANNEL_INDEX } from "../gen/live-channels";
import type { LiveFrame } from "./frame";
import { CUE_PRIORITY, HISTORY_CAPACITY, LiveHistory, type Series, TREND_WINDOW_SPECS, downsample } from "./history";

function f(time: number, speed: number | null, lap = 1, epoch = 1, dist: number | null = null): LiveFrame {
  const values: (number | null)[] = Array(CHANNELS.length).fill(null);
  values[CHANNEL_INDEX.speed!] = speed;
  values[CHANNEL_INDEX.lap!] = lap;
  values[CHANNEL_INDEX.lap_dist_pct!] = dist;
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

  it("windows by laps on a rolling lap distance, not whole lap numbers", () => {
    const h = new LiveHistory();
    // 30 s laps: lap 1 from t 0, lap 2 from t 30, lap 3 from t 60; position = lap + fraction.
    for (let i = 0; i < 90; i++) h.push(f(i, i, 1 + Math.floor(i / 30), 1, (i % 30) / 30));
    // At t 89 (lap 3, 0.967 through) one lap back is lap position 2.967: the samples from t 59 on, not all of lap 2.
    const one = h.series("speed", { laps: 1 });
    expect(one.axis).toBe("distance");
    expect(one.t[0]).toBe(59);
    expect(one.x[0]).toBeCloseTo(2 + 29 / 30, 6);
    expect(h.series("speed", { laps: 2 }).t[0]).toBe(29);
    expect(h.series("speed", { laps: 3 }).t[0]).toBe(0);
    // A new lap starts: the window still spans one lap of distance, so it holds the end of the lap before.
    h.push(f(90, 90, 4, 1, 0));
    expect(h.series("speed", { laps: 1 }).t[0]).toBe(60);
  });

  it("falls back to a time window when the lap distance is not reported", () => {
    const h = new LiveHistory();
    for (let i = 0; i < 90; i++) h.push(f(i, i, 1 + Math.floor(i / 30)));
    const s = h.series("speed", { laps: 1 });
    expect(s.axis).toBe("time");
    expect(s.t[0]).toBe(0);
  });

  it("maps several channels of one row, and the cue priority column", () => {
    const h = new LiveHistory();
    const frame = f(0, 10);
    frame.values[CHANNEL_INDEX.pace_band_min!] = 15;
    frame.values[CHANNEL_INDEX.pace_band_max!] = 16;
    frame.cue = { id: 1, priority: 50, evidence: 0, args: [] };
    h.push(frame);
    expect(Array.from(h.map(["pace_band_min", "pace_band_max"], { seconds: 30 }, ([a, b]) => (a! + b!) / 2).v)).toEqual([15.5]);
    expect(Array.from(h.series(CUE_PRIORITY, { seconds: 30 }).v)).toEqual([50]);
    expect(h.hasChannel("speed")).toBe(true);
    expect(h.hasChannel("abs_active")).toBe(false);
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

const timeSeries = (t: number[], v: number[]): Series => ({ t: Float64Array.from(t), v: Float32Array.from(v), x: Float64Array.from(t), axis: "time" });

describe("downsample", () => {
  it("keeps a one-sample spike that a stride would drop", () => {
    const n = 1000;
    const cols = downsample(timeSeries(Array.from({ length: n }, (_, i) => i / 100), Array.from({ length: n }, (_, i) => (i === 503 ? 999 : 1))), 0, (n - 1) / 100, 50);
    expect(Math.max(...cols.map((c) => c.max))).toBe(999);
    expect(cols.length).toBeLessThanOrEqual(50);
  });

  it("skips NaN and returns nothing for an empty or inverted range", () => {
    const s = timeSeries([0, 1, 2], [Number.NaN, Number.NaN, 5]);
    expect(downsample(s, 0, 2, 10)).toEqual([{ x: 9, min: 5, max: 5, gap: false }]);
    expect(downsample(s, 2, 2, 10)).toEqual([]);
  });

  it("breaks the line where samples are missing or time jumps, never joining across the gap", () => {
    // Ahead gap 1.20 s for t 0-10, absent for t 11-19, 1.50 s for t 20-30, sampled at 10 Hz.
    const t: number[] = [];
    const v: number[] = [];
    for (let i = 0; i <= 300; i++) {
      t.push(i / 10);
      v.push(i / 10 >= 11 && i / 10 < 20 ? Number.NaN : i / 10 < 11 ? 1.2 : 1.5);
    }
    const cols = downsample(timeSeries(t, v), 0, 30, 300);
    const breaks = cols.filter((c) => c.gap);
    expect(breaks).toHaveLength(1);
    expect(breaks[0]!.x).toBe(200);
    // The same samples with the NaN rows left out entirely are a time gap of 9 s: still one break.
    const kept = t.map((x, i) => [x, v[i]!] as const).filter(([, y]) => !Number.isNaN(y));
    const cut = downsample(timeSeries(kept.map(([x]) => x), kept.map(([, y]) => y)), 0, 30, 300);
    expect(cut.filter((c) => c.gap)).toHaveLength(1);
  });

  it("does not break a window that mixes the 60 Hz backlog, 10 Hz decimated frames and 60 Hz again", () => {
    const t: number[] = [];
    let at = 0;
    for (const step of [1 / 60, 1 / 10, 1 / 60]) for (let i = 0; i < 120; i++) t.push((at += step));
    const cols = downsample(timeSeries(t, t.map(() => 5)), 0, at, 400);
    expect(cols.some((c) => c.gap)).toBe(false);
  });

  it("does not break an ordinary run of samples", () => {
    const t = Array.from({ length: 600 }, (_, i) => i / 60);
    const cols = downsample(timeSeries(t, t.map(() => 5)), 0, 10, 200);
    expect(cols.some((c) => c.gap)).toBe(false);
  });
});

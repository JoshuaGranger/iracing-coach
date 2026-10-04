import { describe, expect, it } from "vitest";
import { FRAME_BUDGET_MS, RATE_STEPS, RateGovernor } from "./rate";

/** Feed `count` passes of `ms` each; returns every rate change with the pass it happened on. */
function run(g: RateGovernor, count: number, ms: number, start = 0): { rate: number; at: number }[] {
  const changes: { rate: number; at: number }[] = [];
  let now = start;
  for (let i = 0; i < count; i++) {
    now += ms;
    const next = g.observe(ms, now);
    if (next !== null) changes.push({ rate: next, at: now });
  }
  return changes;
}

describe("rate governor (decimation by measured frame time, never by a setting)", () => {
  it("starts at 60 Hz and stays there while frames keep to the budget", () => {
    const g = new RateGovernor();
    expect(g.rate).toBe(60);
    expect(run(g, 600, 16.7)).toEqual([]);
    expect(g.rate).toBe(60);
  });

  it("steps down 60 to 30 to 10 when the mean frame time stays over the budget, and no further", () => {
    const g = new RateGovernor();
    const changes = run(g, 600, FRAME_BUDGET_MS + 15);
    expect(changes.map((c) => c.rate)).toEqual([30, 10]);
    expect(g.rate).toBe(RATE_STEPS[RATE_STEPS.length - 1]);
  });

  it("ignores a hidden tab or a suspended page, which is not a slow device", () => {
    const g = new RateGovernor();
    expect(run(g, 300, 2000)).toEqual([]);
    expect(g.rate).toBe(60);
  });

  it("does not drop on a single hitch inside an otherwise smooth window", () => {
    const g = new RateGovernor();
    run(g, 80, 16.7);
    expect(g.observe(200, 10_000)).toBeNull();
    expect(run(g, 200, 16.7, 10_000)).toEqual([]);
    expect(g.rate).toBe(60);
  });

  it("tries a step up after a calm minute, and waits twice as long when the try fails at once", () => {
    const g = new RateGovernor();
    let now = 0;
    /** Feed passes of `ms` until the governor changes rate (or `max` passes); returns the new rate and the seconds it took. */
    const until = (ms: number, max: number): { rate: number | null; seconds: number } => {
      const from = now;
      for (let i = 0; i < max; i++) {
        now += ms;
        const next = g.observe(ms, now);
        if (next !== null) return { rate: next, seconds: (now - from) / 1000 };
      }
      return { rate: null, seconds: (now - from) / 1000 };
    };
    expect(until(40, 200).rate).toBe(30);
    // Smooth again at 30 Hz: the try comes after a minute, not before.
    const first = until(16.7, 10_000);
    expect(first.rate).toBe(60);
    expect(first.seconds).toBeGreaterThanOrEqual(55);
    // The try fails at once: back to 30, and the next try waits two minutes.
    expect(until(40, 200).rate).toBe(30);
    const second = until(16.7, 20_000);
    expect(second.rate).toBe(60);
    expect(second.seconds).toBeGreaterThanOrEqual(115);
  });
});

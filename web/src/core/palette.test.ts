import { describe, expect, it } from "vitest";
import { cssVariables, theme } from "../gen/theme";
import { RUN_COLORS, raceProgressColour, runColour, runDash, runHex } from "./palette";

const rgb = (h: string) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16)) as [number, number, number];
const lum = (h: string) => {
  const [r, g, b] = rgb(h).map((v) => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  }) as [number, number, number];
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};
const contrast = (a: string, b: string) => {
  const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
};
const dist = (a: string, b: string) => Math.hypot(...rgb(a).map((v, i) => v - rgb(b)[i]!));

describe("run palette", () => {
  it("has eight tokens, emitted as --run-1 to --run-8", () => {
    expect(RUN_COLORS).toBe(8);
    for (let n = 1; n <= 8; n++) expect(cssVariables[`--run-${n}`]).toBe(theme.identity.runs[n - 1]);
  });

  it("is ordered by run number and cycles after the eighth", () => {
    expect(runColour(1)).toBe("var(--run-1)");
    expect(runColour(8)).toBe("var(--run-8)");
    expect(runColour(9)).toBe("var(--run-1)");
    expect(runHex(2)).toBe(theme.identity.runs[1]);
    expect(runHex(10)).toBe(theme.identity.runs[1]);
  });

  it("answers muted text for a missing or invalid run", () => {
    for (const bad of [null, undefined, 0, -1, 1.5, Number.NaN]) expect(runColour(bad as number)).toBe("var(--color-text-muted)");
  });

  it("gives the first three runs the three line styles and keeps every colour distinct and legible", () => {
    expect(runDash(1)).toBe("");
    expect(runDash(2)).not.toBe("");
    expect(runDash(3)).not.toBe(runDash(2));
    const runs = theme.identity.runs;
    expect(new Set(runs).size).toBe(runs.length);
    for (const c of runs) expect(contrast(c, theme.colors.app), c).toBeGreaterThanOrEqual(4.5);
    for (let a = 0; a < runs.length; a++) for (let b = a + 1; b < runs.length; b++) expect(dist(runs[a]!, runs[b]!), `${runs[a]} ${runs[b]}`).toBeGreaterThan(40);
  });

  it("keeps clear of the race hues that mean green flag, caution, fastest lap and loss", () => {
    for (const run of theme.identity.runs) for (const race of [theme.race.green, theme.race.caution, theme.race.best, theme.race.loss]) expect(dist(run, race), `${run} vs ${race}`).toBeGreaterThan(70);
  });
});

describe("raceProgressColour", () => {
  const stops = theme.identity.progress;
  it("starts and ends on the first and last stop", () => {
    expect(raceProgressColour(0, 50)).toBe(stops[0]!.toLowerCase());
    expect(raceProgressColour(49, 50)).toBe(stops[stops.length - 1]!.toLowerCase());
  });

  it("hits an interior stop exactly and blends between stops", () => {
    // Six stops, eleven laps: lap 2 of 11 sits at t = 0.2, exactly on stop 2.
    expect(raceProgressColour(2, 11)).toBe(stops[1]!.toLowerCase());
    const mid = rgb(raceProgressColour(1, 11));
    const a = rgb(stops[0]!);
    const b = rgb(stops[1]!);
    for (let k = 0; k < 3; k++) expect(mid[k]).toBeCloseTo((a[k]! + b[k]!) / 2, -0.5);
  });

  it("is a function of the lap and the phase length only, so a filter never recolours a lap", () => {
    expect(raceProgressColour(7, 40)).toBe(raceProgressColour(7, 40));
    expect(raceProgressColour(7, 40)).not.toBe(raceProgressColour(7, 41));
  });

  it("is the start colour for one lap or an unknown count, and clamps out-of-range laps", () => {
    expect(raceProgressColour(0, 1)).toBe(stops[0]!.toLowerCase());
    expect(raceProgressColour(3, 0)).toBe(stops[0]!.toLowerCase());
    expect(raceProgressColour(-5, 10)).toBe(raceProgressColour(0, 10));
    expect(raceProgressColour(99, 10)).toBe(raceProgressColour(9, 10));
  });

  it("changes colour from lap to lap along a race", () => {
    const seen = new Set(Array.from({ length: 60 }, (_, i) => raceProgressColour(i, 60)));
    expect(seen.size).toBe(60);
  });
});

import { describe, expect, it } from "vitest";
import { cssVariables, theme } from "../gen/theme";
import { RUN_COLORS, carAccent, raceProgressColour, runColour, runDash, runHex } from "./palette";

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

describe("carAccent (#73)", () => {
  // The car folders of the iRacing library the owner drives from, the cars of the mock and of the fixtures.
  const FIXTURE_CARS = ["nascar-xfinity", "mx5-cup", "stockcars chevyss2020", "supra", "other car", "acuraarx06gtp", "mx5"];
  const LIBRARY_CARS = [
    ...FIXTURE_CARS,
    ..."audibmwm4gt3 bmwm4gt3 bmwlmdh cadillacvseriesrgtp dallaradw12 dallarap217 dallarair18 ferrari296gt3 ferrari499p fordgt fordmustanggt3 fordmustanggt4 formularenaultv40 hpdarx01c hyundaielantracn7 indyoval ligierjsp320 mclaren570sgt4 mclaren720sgt3 mercedesamgevogt3 porsche911cup porsche911rsrgt3 porsche963 porschegt3r radical sr10 toyotagr86 vwbeetlegrc v8supercars lamborghinihuracangt3evo2 astonmartinvantagegt3 astonmartinvantagegt4 bmwm2csr".split(" "),
  ];

  const lab = (hex: string): [number, number, number] => {
    const lin = rgb(hex).map((v) => {
      const s = v / 255;
      return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
    }) as [number, number, number];
    const [r, g, b] = lin;
    const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
    const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
    const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
    return [0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s, 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s, 0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s];
  };
  const deltaE = (a: string, b: string) => Math.hypot(...lab(a).map((v, i) => v - lab(b)[i]!));

  it("is a function of the car identity alone: the same on every call, whatever was asked before", () => {
    const first = LIBRARY_CARS.map(carAccent);
    expect([...LIBRARY_CARS].reverse().map(carAccent).reverse()).toEqual(first);
    expect(carAccent("supra")).toBe(carAccent("supra"));
    // pinned values: a change here recolours every car on every device
    expect([carAccent("supra"), carAccent("mx5-cup"), carAccent("acuraarx06gtp")]).toEqual(["#f7a597", "#ceadf1", "#00a99d"]);
    expect(first.every((c) => /^#[0-9a-f]{6}$/.test(c))).toBe(true);
  });

  it("ignores case and punctuation, so a folder and its path are one car", () => {
    expect(carAccent("MX5-Cup")).toBe(carAccent("mx5 cup"));
    expect(carAccent("  Stockcars ChevySS2020 ")).toBe(carAccent("stockcars chevyss2020"));
    expect(carAccent("mx5cup")).not.toBe(carAccent("mx5"));
  });

  it("answers the muted colour for no car", () => {
    for (const none of [null, undefined, "", "  ", "--"]) expect(carAccent(none)).toBe("var(--color-text-muted)");
  });

  it("separates the cars the mock and the fixtures use", () => {
    for (let a = 0; a < FIXTURE_CARS.length; a++)
      for (let b = a + 1; b < FIXTURE_CARS.length; b++) expect(deltaE(carAccent(FIXTURE_CARS[a]!), carAccent(FIXTURE_CARS[b]!)), `${FIXTURE_CARS[a]} vs ${FIXTURE_CARS[b]}`).toBeGreaterThan(0.1);
  });

  it("tells the two cars of the mock apart at a glance", () => {
    expect(deltaE(carAccent("nascar-xfinity"), carAccent("mx5-cup"))).toBeGreaterThan(0.2);
  });

  it("spreads a whole library over the hue circle, far past the three colours it replaces", () => {
    const accents = LIBRARY_CARS.map(carAccent);
    expect(new Set(accents).size).toBeGreaterThanOrEqual(LIBRARY_CARS.length * 0.6);
    const hues = new Set(accents.map((c) => Math.round(((Math.atan2(lab(c)[2], lab(c)[1]) * 180) / Math.PI + 360) % 360 / 12)));
    expect(hues.size).toBeGreaterThanOrEqual(20);
    // no two names of one make look alike: neighbours in an alphabetical list differ
    const sorted = [...LIBRARY_CARS].sort();
    const near = sorted.slice(1).filter((c, i) => deltaE(carAccent(c), carAccent(sorted[i]!)) < 0.04);
    expect(near).toEqual([]);
  });

  it("stays legible on the app background and clear of the race hues", () => {
    for (const c of LIBRARY_CARS.map(carAccent)) {
      expect(contrast(c, theme.colors.app), c).toBeGreaterThanOrEqual(4.5);
      for (const race of [theme.race.green, theme.race.caution, theme.race.best, theme.race.loss, theme.race.pit, theme.race.fuel]) expect(deltaE(c, race), `${c} vs ${race}`).toBeGreaterThan(0.05);
    }
  });
});

import { describe, expect, it } from "vitest";
import type { VNode } from "preact";
import { buildRound2Analysis } from "../fixtures/round2";
import { parseInvestigation } from "../investigation-model";
import { runCards, runTires, stopsBetweenRuns } from "../ui/runs-model";
import { RunTireDetails } from "../ui/RunTireDetails";
import type { PitStop } from "../geometry/rail-model";
import { surfaceTempColour, TireDiagram, wearBarHeight } from "../../ui/pictograms";

const analysis = buildRound2Analysis();
const inv = parseInvestigation((analysis.investigation as { race_investigation: unknown }).race_investigation);

function tire(l: [number, number], m: [number, number], r: [number, number], psi = 31.5): Record<string, unknown> {
  return {
    remaining_percent: { L: l[0], M: m[0], R: r[0] },
    surface_temperature_f: { L: l[1], M: m[1], R: r[1] },
    pressure: { psi, kind: "live" },
  };
}
const measured = (): Record<string, unknown> => ({
  run_number: 1,
  tire_measurement_status: "measured_at_stop",
  tire_observation: {
    lowest_remaining_tire: "RF",
    lowest_remaining_percent: 61.2,
    tires: {
      LF: tire([72, 150], [74, 160], [70, 171]),
      RF: tire([58, 190], [63, 200], [62, 204]),
      LR: tire([80, 140], [81, 145], [79, 150]),
      RR: tire([77, 150], [78, 158], [76, 170]),
    },
  },
  tire_set_lifecycle: { corners: { RF: { green_laps_on_set: 23 }, LF: { green_laps_on_set: 23 } } },
});

type V = VNode<Record<string, any>>;
function walk(node: unknown, visit: (v: V) => void): void {
  if (Array.isArray(node)) return node.forEach((child) => walk(child, visit));
  if (node && typeof node === "object" && "props" in node) {
    const v = node as V;
    if (typeof v.type === "function") return walk((v.type as (p: unknown) => unknown)(v.props), visit);
    visit(v);
    walk(v.props.children, visit);
  }
}

describe("run tire picture (G8)", () => {
  it("shows twelve physical tread bands with four corner labels and unknown readings", () => {
    const readings = runTires({ tire_observation: { tires: { LF: { remaining_percent: { L: 50 } } } } }, 9)!;
    const corners: unknown[] = [];
    const bands: V[] = [];
    walk(RunTireDetails({ tires: readings }), (v) => {
      if (v.props["data-detail-tire"]) corners.push(v.props["data-detail-tire"]);
      if (v.props.class === "ri-tire-detail-band") bands.push(v);
    });
    expect(corners).toEqual(["LF", "RF", "LR", "RR"]);
    expect(bands).toHaveLength(12);
    expect(JSON.stringify(bands[1]!.props)).toContain("—");
  });

  it("places only recorded service between runs, excluding post-race and unmatched stops", () => {
    const current = inv.runs[0]!;
    const next = inv.runs[1]!;
    const stop = (runNumber: number | null, lap = current.lastLap, postRun = false): PitStop => ({ lap, runNumber, postRun, tires: null, fuelAddedGal: null, durationS: null, capacityGal: null });
    const recorded = stop(current.runNumber);
    const noRunId = stop(null);
    expect(stopsBetweenRuns(current, next, [recorded, noRunId, stop(current.runNumber, current.lastLap, true), stop(next.runNumber), stop(null, next.firstLap)])).toEqual([recorded, noRunId]);
    expect(stopsBetweenRuns(current, undefined, [recorded])).toEqual([]);
  });
  it("reads the run's end state: caption, lap, temperature range, one line per tire", () => {
    const t = runTires(measured(), 47)!;
    expect(t.caption).toBe("RF 61 % · 23 laps");
    expect(t.atText).toBe("at L47 · 140–204 °F");
    expect(t.lowest).toEqual({ tire: "RF", percent: 61.2 });
    expect(t.corners.map((c) => c.id)).toEqual(["LF", "RF", "LR", "RR"]);
    const lines = t.hover.split("\n");
    expect(lines[0]).toContain("L47");
    expect(lines[1]).toBe("LF · O 72 M 74 I 70 % · O 150 M 160 I 171 °F · 31.5 psi live");
    // A right tire reads inside, middle, outside.
    expect(lines[2]).toBe("RF · I 58 M 63 O 62 % · I 190 M 200 O 204 °F · 31.5 psi live");
  });

  it("has no picture when the server measured nothing", () => {
    expect(runTires(null, 3)).toBeNull();
    expect(runTires({ tire_observation: null, tire_measurement_status: "stale_or_unconfirmed_at_stop" }, 3)).toBeNull();
    expect(runTires({ tire_observation: { tires: { LF: { remaining_percent: null } } } }, 3)).toBeNull();
    expect(runTires({ tire_observation: { lowest_remaining_percent: 60 } }, 3)).toBeNull();
  });

  it("leaves out what the server did not send", () => {
    const run = { tire_observation: { tires: { LF: { remaining_percent: { L: 50, M: 55 } } } } };
    const t = runTires(run, 9)!;
    expect(t.caption).toBe("");
    expect(t.atText).toBe("at L9");
    expect(t.corners[0]!.bands.map((b) => b.remaining)).toEqual([50, 55, null]);
    expect(t.corners[1]!.bands.every((b) => b.remaining === null && b.surfaceF === null)).toBe(true);
    expect(t.hover.split("\n")[1]).toBe("LF · O 50 M 55 %");
    expect(runTires({ ...run, tire_observation: { ...run.tire_observation, lowest_remaining_tire: "LF", lowest_remaining_percent: 52 }, tire_set_lifecycle: { corners: { LF: { green_laps_on_set: 1 } } } }, 9)!.caption).toBe("LF 52 % · 1 lap");
  });

  it("rides on the run card and is null without a reading", () => {
    const runs = (analysis.runs.runs as Record<string, unknown>[]).map((run, index) => (index === 0 ? { ...run, ...measured(), run_number: run.run_number } : run));
    const cards = runCards(inv, runs);
    expect(cards[0]!.tires?.atLap).toBe(inv.runs[0]!.lastLap);
    expect(cards[0]!.tires?.atText.startsWith(`at L${inv.runs[0]!.lastLap}`)).toBe(true);
    expect(cards[1]!.tires).toBeNull();
    expect(runCards(inv, [])[0]!.tires).toBeNull();
  });

  it("draws tread as fill height and surface temperature as hue", () => {
    expect(wearBarHeight(100)).toBe(22);
    expect(wearBarHeight(50)).toBe(11);
    expect(wearBarHeight(null)).toBe(0);
    expect(wearBarHeight(140)).toBe(22);
    expect(surfaceTempColour(null)).toBe("var(--color-text-muted)");
    expect(surfaceTempColour(120)).toBe("color-mix(in srgb, var(--progress-4) 0%, var(--progress-2))");
    expect(surfaceTempColour(170)).toBe("color-mix(in srgb, var(--progress-4) 100%, var(--progress-2))");
    expect(surfaceTempColour(220)).toBe("color-mix(in srgb, var(--progress-6) 100%, var(--progress-4))");
    expect(surfaceTempColour(500)).toBe(surfaceTempColour(220));
    expect(surfaceTempColour(70)).toBe(surfaceTempColour(120));
    const t = runTires(measured(), 47)!;
    const fills: { remaining: unknown; height: number }[] = [];
    walk(TireDiagram({ wear: t.corners, width: 60, height: 80 }), (v) => {
      if (v.type === "rect" && v.props["data-remaining"] !== undefined) fills.push({ remaining: v.props["data-remaining"], height: v.props.height as number });
    });
    expect(fills).toHaveLength(12);
    const rf = fills.find((f) => f.remaining === 58)!;
    expect(rf.height).toBeCloseTo(22 * 0.58);
  });

  it("keeps the stop pictogram unchanged", () => {
    const lit: unknown[] = [];
    walk(TireDiagram({ changed: ["RF"] }), (v) => { if (v.type === "rect") lit.push(v.props["data-lit"]); });
    expect(lit).toEqual([false, true, false, false]);
  });
});

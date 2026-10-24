import { describe, expect, it } from "vitest";
import { incidentTagDrop, paceTopMargin } from "../ui/RaceView";
import { rowNameWraps } from "../ui/TraceChart";

describe("rowNameWraps: a trace row wraps a long name only when two lines and the unit fit", () => {
  it("wraps at 1440x900 (57 px rows) and keeps one line at 1024x768 (40 px rows)", () => {
    expect(rowNameWraps(57.4, 16)).toBe(true);
    expect(rowNameWraps(40.2, 16)).toBe(false);
    expect(rowNameWraps(56, 16)).toBe(true);
    expect(rowNameWraps(57.4, 20)).toBe(false);
  });
});

// C3 (web round 2 verification): labels that overprinted at 390 px.

describe("paceTopMargin: the Lap time title clears the pit and incident tags", () => {
  // Round 3 (#41): 38 px, so the pit tag (25 px above the plot) keeps its 6 px glow inside the chart's top edge.
  it("keeps the 38 px band when every tag sits right of the title", () => {
    expect(paceTopMargin([300, 640], 52, 1)).toBe(38);
    expect(paceTopMargin([], 52, 1)).toBe(38);
  });

  it("adds a line when a tag would sit under the title (a phone chart with an incident at lap 15)", () => {
    // Title from 52 to 52 + 87.6 + 6; a tag at x 130 reaches back to 116.
    expect(paceTopMargin([130, 260], 52, 1)).toBe(56);
  });

  it("scales with the root font size", () => {
    expect(paceTopMargin([400], 65, 1.25)).toBe(48);
    expect(paceTopMargin([150], 65, 1.25)).toBe(70);
  });

  it("leaves room above the pit tag for its glow", () => {
    for (const k of [0.875, 1, 1.25]) expect(paceTopMargin([400], 52, k) - 25 * k).toBeGreaterThanOrEqual(12 * k);
  });
});

describe("incidentTagDrop: an incident on a pit lap sits under the pit tag", () => {
  it("stays in the tag row on a lap without a stop", () => {
    expect(incidentTagDrop(false, 1)).toBe(0);
  });

  it("drops below the pit tag (25 to 5 px above the plot) so neither covers the other", () => {
    for (const k of [0.875, 1, 1.25]) {
      const pitBottom = -5 * k;
      const incidentTop = -26 * k + incidentTagDrop(true, k);
      expect(incidentTop).toBeGreaterThan(pitBottom);
    }
  });
});

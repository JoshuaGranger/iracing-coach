import { describe, expect, it } from "vitest";
import { paceTopMargin } from "../ui/RaceView";
import { declutterLabels } from "../ui/SectionsView";
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
  it("keeps the 30 px band when every tag sits right of the title", () => {
    expect(paceTopMargin([300, 640], 52, 1)).toBe(30);
    expect(paceTopMargin([], 52, 1)).toBe(30);
  });

  it("adds a line when a tag would sit under the title (a phone chart with an incident at lap 15)", () => {
    // Title from 52 to 52 + 87.6 + 6; a tag at x 130 reaches back to 116.
    expect(paceTopMargin([130, 260], 52, 1)).toBe(48);
  });

  it("scales with the root font size", () => {
    expect(paceTopMargin([400], 65, 1.25)).toBe(38);
    expect(paceTopMargin([150], 65, 1.25)).toBe(60);
  });
});

class Label {
  attrs = new Set<string>();
  constructor(public left: number, public right: number) {}
  getBoundingClientRect() {
    return { left: this.left, right: this.right };
  }
  setAttribute(name: string) {
    this.attrs.add(name);
  }
  removeAttribute(name: string) {
    this.attrs.delete(name);
  }
}

function host(labels: Record<string, Label>) {
  return { querySelector: (selector: string) => labels[selector] ?? null };
}

describe("declutterLabels: the approach and axis-start labels give way to the section boundary", () => {
  it("hides the approach label and the start value when the approach is narrow (phone)", () => {
    const labels = {
      ".ri-zone.approach": new Label(188, 250),
      ".ri-zone.section": new Label(232, 300),
      ".ri-x-axis .start": new Label(186, 216),
      ".ri-x-axis .middle": new Label(212, 250),
      ".ri-x-axis .end": new Label(320, 352),
    };
    declutterLabels(host(labels));
    expect(labels[".ri-zone.approach"].attrs.has("data-crowded")).toBe(true);
    expect(labels[".ri-x-axis .start"].attrs.has("data-crowded")).toBe(true);
    expect(labels[".ri-x-axis .end"].attrs.has("data-crowded")).toBe(false);
    expect(labels[".ri-zone.section"].attrs.size).toBe(0);
    expect(labels[".ri-x-axis .middle"].attrs.size).toBe(0);
  });

  it("shows every label again once the plot is wide enough (desktop)", () => {
    const labels = {
      ".ri-zone.approach": new Label(628, 690),
      ".ri-zone.section": new Label(850, 912),
      ".ri-x-axis .start": new Label(625, 655),
      ".ri-x-axis .middle": new Label(830, 866),
      ".ri-x-axis .end": new Label(1370, 1406),
    };
    labels[".ri-zone.approach"].attrs.add("data-crowded");
    labels[".ri-x-axis .start"].attrs.add("data-crowded");
    declutterLabels(host(labels));
    for (const label of Object.values(labels)) expect(label.attrs.size).toBe(0);
  });

  it("hides the end value when a long approach pushes the boundary against it", () => {
    const labels = { ".ri-x-axis .start": new Label(0, 30), ".ri-x-axis .middle": new Label(300, 336), ".ri-x-axis .end": new Label(332, 364) };
    declutterLabels(host(labels));
    expect(labels[".ri-x-axis .end"].attrs.has("data-crowded")).toBe(true);
    expect(labels[".ri-x-axis .start"].attrs.size).toBe(0);
  });

  it("does nothing without a pass (no labels)", () => {
    expect(() => declutterLabels(host({}))).not.toThrow();
  });
});

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cssVariables, theme } from "../gen/theme";

vi.mock("./prefs", () => ({ savePrefs: vi.fn(() => Promise.resolve()), usePrefs: vi.fn(() => ({ prefs: undefined })) }));

import { DEFAULT_TEXT_SIZE, TEXT_SIZES, chooseTextSize, initTextSize, parseTextSize, savedTextSize, textSize, textSizeHint } from "./text-size";
import { savePrefs } from "./prefs";

/** A token in rem as pixels at the default 16 px root. */
function px(name: string): number {
  const value = cssVariables[name];
  const match = /^([\d.]+)rem$/.exec(value ?? "");
  if (!match) throw new Error(`${name} is ${value}, not rem`);
  return Number(match[1]) * 16;
}

describe("type scale (owner minimums, 2026-10-03)", () => {
  it("meets the minimums at the default root and is rem, so the browser default and the text-size step scale it", () => {
    expect(px("--text-body")).toBeGreaterThanOrEqual(16);
    expect(px("--text-compact")).toBeGreaterThanOrEqual(14);
    expect(px("--text-data")).toBeGreaterThanOrEqual(15);
    expect(px("--text-control")).toBeGreaterThanOrEqual(15);
    // The hierarchy holds: titles above body above secondary.
    expect(px("--text-page-title")).toBeGreaterThan(px("--text-section-title"));
    expect(px("--text-section-title")).toBeGreaterThan(px("--text-body"));
    expect(px("--text-body")).toBeGreaterThan(px("--text-compact"));
    for (const space of ["--space-1", "--space-4", "--space-8"]) expect(cssVariables[space]).toMatch(/rem$/);
  });

  it("keeps controls 36 px tall (44 px for touch) even when the browser default font is small", () => {
    expect(cssVariables["--control-height"]).toBe("max(36px, 2.25rem)");
    expect(cssVariables["--touch-target"]).toBe("max(44px, 2.75rem)");
  });

  it("offers Default, Larger and Largest as growing percentages of the root, Default being the browser default", () => {
    expect(TEXT_SIZES.map((step) => step.id)).toEqual(["default", "larger", "largest"]);
    expect(TEXT_SIZES.map((step) => step.label)).toEqual(["Default", "Larger", "Largest"]);
    const percents = TEXT_SIZES.map((step) => step.percent);
    expect(percents[0]).toBe(100);
    expect(percents).toEqual([...percents].sort((a, b) => a - b));
    expect(new Set(percents).size).toBe(3);
    expect(theme.typography.textSizes).toBe(TEXT_SIZES);
  });
});

describe("text size preference", () => {
  const dataset: Record<string, string> = {};
  beforeEach(() => {
    for (const key of Object.keys(dataset)) delete dataset[key];
    vi.stubGlobal("document", { documentElement: { dataset } });
    vi.mocked(savePrefs).mockClear();
    textSize.set(DEFAULT_TEXT_SIZE);
  });
  afterEach(() => vi.unstubAllGlobals());

  it("reads only the three known steps", () => {
    expect(parseTextSize("larger")).toBe("larger");
    expect(parseTextSize("Largest")).toBeNull();
    expect(parseTextSize("huge")).toBeNull();
    expect(parseTextSize(1)).toBeNull();
    expect(parseTextSize(null)).toBeNull();
  });

  it("takes the saved step from the driver's prefs, which the contract does not declare, and ignores one it does not know", () => {
    expect(savedTextSize({ customer_id: 1, version: 3, text_size: "larger" })).toBe("larger");
    expect(savedTextSize({ customer_id: 1, version: 3, text_size: "enormous" })).toBeNull();
    expect(savedTextSize({ customer_id: 1, version: 3 })).toBeNull();
    expect(savedTextSize(undefined)).toBeNull();
  });

  it("has no hint without storage and starts at Default", () => {
    expect(textSizeHint()).toBe("default");
    expect(textSize.get()).toBe("default");
  });

  it("applies a chosen step to the root element and saves it with the driver's prefs", () => {
    chooseTextSize("larger", 1507810);
    expect(dataset.textSize).toBe("larger");
    expect(textSize.get()).toBe("larger");
    expect(savePrefs).toHaveBeenCalledWith(1507810, { text_size: "larger" });
    chooseTextSize("default", 1507810);
    expect(dataset.textSize).toBe("default");
  });

  it("keeps the choice on this device when no driver is chosen yet", () => {
    chooseTextSize("largest", null);
    expect(dataset.textSize).toBe("largest");
    expect(savePrefs).not.toHaveBeenCalled();
  });

  it("applies the starting step before the first render", () => {
    textSize.set("largest");
    for (const key of Object.keys(dataset)) delete dataset[key];
    initTextSize();
    expect(dataset.textSize).toBe("largest");
  });
});

import { describe, expect, it } from "vitest";
import { cssVariables } from "../gen/theme";

/** A token in rem as pixels at the default 16 px root. */
function px(name: string): number {
  const value = cssVariables[name];
  const match = /^([\d.]+)rem$/.exec(value ?? "");
  if (!match) throw new Error(`${name} is ${value}, not rem`);
  return Number(match[1]) * 16;
}

describe("type scale (owner minimums, 2026-10-03)", () => {
  it("meets the minimums at the default root and is rem, so the browser's default font size scales it", () => {
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
});

import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { PHASE_HUE } from "../investigation/ui/PhaseSelector";
import { cssVariables as THEME_VARS } from "../gen/theme";

/** Backlog #52: practice, qualifying and race each their own hue token, shared by the Sessions cells and the P · Q · R selector. */
describe("phase hues", () => {
  const css = readFileSync(new URL("./sessions.css", import.meta.url), "utf8");
  const tokens = ["--phase-practice-hue", "--phase-qualify-hue", "--phase-race-hue"] as const;
  const value = (t: string) => (THEME_VARS as Record<string, string>)[t] ?? "";

  it("are three distinct theme tokens", () => {
    const values = tokens.map(value);
    expect(values.every((v) => /^#[0-9A-F]{6}$/i.test(v))).toBe(true);
    expect(new Set(values).size).toBe(3);
  });

  it("the race is the strongest: the highest luminance on the dark surface", () => {
    const lum = (hex: string) => {
      const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
      return 0.2126 * r! + 0.7152 * g! + 0.0722 * b!;
    };
    const [p, q, r] = tokens.map((t) => lum(value(t)));
    expect(r!).toBeGreaterThan(q!);
    expect(r!).toBeGreaterThan(p!);
  });

  it("the selector and the Sessions cells use the same tokens", () => {
    expect(PHASE_HUE.P).toBe("var(--phase-practice-hue)");
    expect(PHASE_HUE.Q).toBe("var(--phase-qualify-hue)");
    expect(PHASE_HUE.R).toBe("var(--phase-race-hue)");
    for (const t of tokens) expect(css).toContain(`var(${t})`);
  });
});

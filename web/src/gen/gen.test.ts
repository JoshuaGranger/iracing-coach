import { describe, expect, it } from "vitest";

import { CHANNELS, CUES, FlagState, Priority, TABLE_VERSION, TILES, conversion } from "./live-channels";
import { EXCLUDED_LEAF_PATTERNS } from "./setup-keys";
import { cssVariables, theme } from "./theme";

describe("generated live channel table", () => {
  it("matches the contract", () => {
    expect(TABLE_VERSION).toBe(1);
    expect(CHANNELS).toHaveLength(45);
    CHANNELS.forEach((c, i) => expect(c.index).toBe(i));
    expect(CUES.map((c) => c.id)).toEqual(Array.from({ length: 15 }, (_, i) => i + 1));
    expect(TILES).toHaveLength(32);
  });

  it("carries wire bytes and conversions", () => {
    expect(FlagState.Disqualified).toBe(8);
    expect(Priority.Critical).toBe(100);
    expect(conversion("m/s", "m/s")).toEqual({ scale: 1, offset: 0 });
    expect(conversion("C", "F")).toEqual({ scale: 1.8, offset: 32 });
    expect(conversion("L", "mph")).toBeNull();
  });
});

describe("generated setup keys and theme", () => {
  it("lists the excluded setup key patterns", () => {
    expect(EXCLUDED_LEAF_PATTERNS).toHaveLength(7);
  });

  it("emits the canonical tokens", () => {
    expect(theme.name).toBe("mineral-glass-dark");
    expect(cssVariables["--color-accent"]).toBe(theme.colors.accent);
    expect(cssVariables["--space-1"]).toBe("4px");
  });
});

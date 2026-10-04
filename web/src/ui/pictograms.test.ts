import { describe, expect, it } from "vitest";
import { DEFAULT_FUEL_CAPACITY_GAL, formatStopSeconds, fuelFill, tireSummary } from "./pictograms";

describe("pit stop pictogram data", () => {
  it("lists the changed tires in car order, and says 4 tires for all of them", () => {
    expect(tireSummary(["RR", "RF"])).toBe("RF RR");
    expect(tireSummary(["LF", "RF", "LR", "RR"])).toBe("4 tires");
    expect(tireSummary(["LR"])).toBe("LR");
    expect(tireSummary([])).toBe("");
    expect(tireSummary(null)).toBe("");
  });

  it("sizes the fuel gauge by the gallons added over the tank, and draws none for no fuel", () => {
    expect(fuelFill(9.25, 18.5)).toBeCloseTo(0.5, 6);
    expect(fuelFill(30, 18.5)).toBe(1);
    expect(fuelFill(5, null)).toBeCloseTo(5 / DEFAULT_FUEL_CAPACITY_GAL, 6);
    for (const none of [0, -1, null, undefined, Number.NaN]) expect(fuelFill(none, 18.5)).toBeNull();
  });

  it("shows the stop duration to one decimal when it is known", () => {
    expect(formatStopSeconds(13.84)).toBe("13.8");
    expect(formatStopSeconds(0)).toBeNull();
    expect(formatStopSeconds(null)).toBeNull();
  });
});

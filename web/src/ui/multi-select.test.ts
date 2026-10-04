import { describe, expect, it } from "vitest";
import { type Option, pillText } from "./multi-select";

const cars: Option[] = [
  { key: "supra", label: "Supra" },
  { key: "mx5", label: "MX-5 Cup" },
  { key: "dallara", label: "Dallara IR-18" },
];

describe("pillText", () => {
  it("is the label alone while nothing is chosen", () => {
    expect(pillText("Car", cars, [])).toBe("Car");
  });

  it("names a single value, and counts several", () => {
    expect(pillText("Car", cars, ["supra"])).toBe("Car · Supra");
    expect(pillText("Car", cars, ["supra", "mx5"])).toBe("Car · 2");
    expect(pillText("Track", cars, ["supra", "mx5", "dallara"])).toBe("Track · 3");
  });

  it("counts only the values the options still offer", () => {
    expect(pillText("Car", cars, ["gone"])).toBe("Car");
    expect(pillText("Car", cars, ["gone", "mx5"])).toBe("Car · MX-5 Cup");
  });
});

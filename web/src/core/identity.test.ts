import { beforeEach, describe, expect, it } from "vitest";
import { CAR_COLORS, carColor, carIndex, resetCarOrder } from "./identity";

describe("car identity colours", () => {
  beforeEach(() => resetCarOrder());

  it("gives a car the next colour the first time it is seen and the same colour after that", () => {
    const first = carColor("supra");
    const second = carColor("mx5");
    expect(first).toBe("var(--car-1)");
    expect(second).toBe("var(--car-2)");
    expect(carColor("supra")).toBe(first);
    expect(carIndex("mx5")).toBe(1);
  });

  it("cycles through the three identity colours and is neutral for no car", () => {
    const colors = ["a", "b", "c", "d"].map(carColor);
    expect(colors).toEqual(["var(--car-1)", "var(--car-2)", "var(--car-3)", "var(--car-1)"]);
    expect(CAR_COLORS).toBe(3);
    expect(carColor(null)).toBe("var(--color-text-muted)");
  });
});

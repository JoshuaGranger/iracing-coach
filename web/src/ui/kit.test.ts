import { describe, expect, it } from "vitest";
import { chipClass } from "./kit";
import { libraryOptions } from "./library-control";

describe("chip rule", () => {
  it("has exactly three variants and no status-normal one", () => {
    expect(chipClass("exception")).toBe("chip chip-exception chip-fail");
    expect(chipClass("exception", "wait")).toBe("chip chip-exception chip-wait");
    expect(chipClass("constraint")).toBe("chip chip-constraint");
    expect(chipClass("action")).toBe("chip chip-action");
    // Level belongs to exceptions only.
    expect(chipClass("constraint", "fail")).toBe("chip chip-constraint");
  });
});

describe("library control", () => {
  it("offers the driver's library first and Lab second", () => {
    expect(libraryOptions()).toEqual([
      { value: "main", label: "Joshua" },
      { value: "lab", label: "Lab" },
    ]);
    expect(libraryOptions("Sam")[0]!.label).toBe("Sam");
  });
});

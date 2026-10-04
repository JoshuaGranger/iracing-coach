import { describe, expect, it } from "vitest";
import {
  FIRST_ROWS,
  MORE_ROWS,
  activeCount,
  emptySelection,
  fromSetupFilters,
  isEmptySelection,
  parseSelection,
  revealMore,
  revealed,
  shownRows,
  toSetupFilters,
  toggleCollapsed,
} from "./sections";

describe("rows revealed per car section", () => {
  it("shows ten rows, then ten more at a time, never past the total", () => {
    expect([FIRST_ROWS, MORE_ROWS]).toEqual([10, 10]);
    expect(shownRows(25, undefined)).toBe(10);
    expect(shownRows(7, undefined)).toBe(7);
    expect(shownRows(25, revealMore(undefined))).toBe(20);
    expect(shownRows(25, revealMore(revealMore(undefined)))).toBe(25);
    expect(shownRows(0, 20)).toBe(0);
  });

  it("remembers the revealed count per driver and car, whatever the case of the car path", () => {
    revealed.reset();
    revealed.set(1, "Supra", 20);
    expect(revealed.get(1, "supra")).toBe(20);
    expect(revealed.get(2, "supra")).toBeUndefined();
    expect(revealed.get(1, "mx5")).toBeUndefined();
    revealed.reset();
  });

  it("a revealed count below the first ten does not shrink the section", () => {
    expect(shownRows(30, 3)).toBe(10);
    expect(shownRows(30, Number.NaN)).toBe(10);
  });
});

describe("collapsed car sections", () => {
  it("toggles a car in a new set and treats a car path case-insensitively", () => {
    const a = new Set<string>();
    const b = toggleCollapsed(a, "Supra");
    expect([...a]).toEqual([]);
    expect(b.has("supra")).toBe(true);
    expect(toggleCollapsed(b, "SUPRA").size).toBe(0);
  });
});

describe("setup filters as the prefs store them", () => {
  it("stores one car in car_path and the list in car_paths, raced as a boolean, text trimmed", () => {
    expect(toSetupFilters({ cars: ["supra"], text: " v3 ", raced: "raced" })).toEqual({ car_path: "supra", car_paths: ["supra"], raced: true, text: "v3" });
    expect(toSetupFilters({ cars: ["a", "b"], text: "", raced: "not-raced" })).toEqual({ car_path: null, car_paths: ["a", "b"], raced: false, text: null });
    expect(toSetupFilters(emptySelection())).toEqual({ car_path: null, car_paths: null, raced: null, text: null });
  });

  it("reads them back, falling back to car_path for an older write, and drops anything malformed", () => {
    expect(fromSetupFilters({ car_paths: ["a", 3, "b"], raced: false, text: "x" } as unknown as Parameters<typeof fromSetupFilters>[0])).toEqual({ cars: ["a", "b"], text: "x", raced: "not-raced" });
    expect(fromSetupFilters({ car_path: "supra", raced: true })).toEqual({ cars: ["supra"], text: "", raced: "raced" });
    expect(fromSetupFilters({ car_path: null, raced: null, text: null })).toEqual({ cars: [], text: "", raced: "all" });
    expect(fromSetupFilters(null)).toBeNull();
    expect(parseSelection({ cars: ["a"], raced: "bogus", text: 4 })).toEqual({ cars: ["a"], text: "", raced: "all" });
    expect(parseSelection("x")).toBeNull();
  });

  it("counts each car, a name and a raced choice as one choice in force", () => {
    expect(activeCount(emptySelection())).toBe(0);
    expect(activeCount({ cars: ["a", "b"], text: " ", raced: "all" })).toBe(2);
    expect(activeCount({ cars: ["a"], text: "v3", raced: "raced" })).toBe(3);
    expect(isEmptySelection({ cars: [], text: "  ", raced: "all" })).toBe(true);
  });
});

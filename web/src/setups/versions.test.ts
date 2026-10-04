import { describe, expect, it } from "vitest";
import type { Schemas } from "../core/api";
import { changedLabel } from "./version-board";
import { changeOf, changesFrom, comparable, pairs, splitUnit, versionsOf } from "./versions";

type Setup = Schemas["SetupSummary"];
const setup = (id: number, day: number, extra: Partial<Setup> = {}): Setup => ({
  setup_id: id,
  customer_id: 1,
  car_path: "supra",
  car_name: "Supra",
  names: ["Bristol race v3.sto"],
  sources: ["recording"],
  observation_count: 1,
  raced: false,
  first_seen_at: `2026-09-${String(day).padStart(2, "0")}T10:00:00Z`,
  last_seen_at: `2026-09-${String(day).padStart(2, "0")}T11:00:00Z`,
  ...extra,
});

describe("versions of a setup name", () => {
  it("numbers them oldest first and lists them newest first, whichever one is open", () => {
    const list = versionsOf(setup(2, 20), [setup(1, 10), setup(3, 25)]);
    expect(list.map((v) => [v.setup.setup_id, v.number, v.latest])).toEqual([
      [3, 3, true],
      [2, 2, false],
      [1, 1, false],
    ]);
  });

  it("pairs each version with the one before it; the oldest has no pair", () => {
    const list = versionsOf(setup(2, 20), [setup(1, 10), setup(3, 25)]);
    expect(pairs(list).map((p) => [p.older.number, p.newer.number])).toEqual([
      [2, 3],
      [1, 2],
    ]);
    expect(pairs(versionsOf(setup(1, 1), []))).toEqual([]);
  });

  it("does not compare an exported sheet with a recorded setup", () => {
    expect(comparable(setup(1, 1), setup(2, 2))).toBeNull();
    expect(comparable(setup(1, 1, { sources: ["htm_export"] }), setup(2, 2))).toBe("Not comparable");
  });
});

describe("a changed setting as a row", () => {
  it("splits a trailing unit and keeps it only when both values share it", () => {
    expect(splitUnit("-3.2 deg")).toEqual({ value: "-3.2", unit: "deg" });
    expect(splitUnit("12 psi")).toEqual({ value: "12", unit: "psi" });
    expect(splitUnit("Soft")).toEqual({ value: "Soft", unit: "" });
    expect(splitUnit("Light blue")).toEqual({ value: "Light blue", unit: "" });
    expect(changeOf({ key: "k", label: "Camber", left: "-3.4 deg", right: "-3.2 deg" })).toEqual({ key: "k", label: "Camber", old: "-3.4", new: "-3.2", unit: "deg" });
    expect(changeOf({ key: "k", label: "Spring", left: "5 in", right: "130 N/mm" })).toMatchObject({ old: "5 in", new: "130 N/mm", unit: "" });
  });

  it("counts settings only: readouts, run readings and unknowns are not changes", () => {
    const rows: Schemas["SetupCompareRow"][] = [
      { key: "Chassis.LeftFront.Camber", status: "changed", left: "-3.4 deg", right: "-3.2 deg" },
      { key: "Chassis.LeftFront.CornerWeight", status: "changed", left: "1 lb", right: "2 lb", readout: true },
      { key: "TiresAero.LeftFrontTire.LastHotPressure", status: "changed", left: "30 psi", right: "31 psi" },
      { key: "Chassis.LeftRear.TrackBarHeight", status: "unknown", left: "5 in", right: null },
      { key: "Chassis.Rear.Spring", status: "identical", left: "1", right: "1" },
    ];
    const changes = changesFrom({ rows });
    expect(changes.map((c) => c.key)).toEqual(["Chassis.LeftFront.Camber"]);
    expect(changes[0]).toMatchObject({ old: "-3.4", new: "-3.2", unit: "deg" });
  });

  it("says the changed count, a dash where there is nothing to compare, and an ellipsis while it loads", () => {
    expect(changedLabel({ state: "done", changes: [] })).toBe("0 changed");
    expect(changedLabel({ state: "done", changes: [changeOf({ key: "k", label: "l", left: "1", right: "2" })] })).toBe("1 changed");
    expect(changedLabel({ state: "first" })).toBe("—");
    expect(changedLabel({ state: "blocked", reason: "x" })).toBe("—");
    expect(changedLabel({ state: "loading" })).toBe("…");
    expect(changedLabel(undefined)).toBe("…");
    // Before a pair's changes load, the backend's own count labels the row; a null count is a dash, never zero.
    expect(changedLabel(undefined, 3)).toBe("3 changed");
    expect(changedLabel(undefined, 0)).toBe("0 changed");
    expect(changedLabel(undefined, null)).toBe("—");
    expect(changedLabel({ state: "done", changes: [] }, 5)).toBe("0 changed");
  });
});

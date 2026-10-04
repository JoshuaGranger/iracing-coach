import { describe, expect, it } from "vitest";
import type { Schemas } from "../core/api";
import { resetCarOrder } from "../core/identity";
import { CAR_COLORS, carColors, carsWithSetups, groupLibrary, racedCountOf, racedCountOver } from "./library";

type Setup = Schemas["SetupSummary"];
type File = Schemas["SetupFileEntry"];

const setup = (id: number, names: string[], extra: Partial<Setup> = {}): Setup => ({
  setup_id: id,
  customer_id: 1,
  car_path: "supra",
  car_name: "Toyota Supra",
  names,
  sources: ["recording"],
  observation_count: 2,
  raced: false,
  first_seen_at: `2026-09-${String(10 + id).padStart(2, "0")}T10:00:00Z`,
  last_seen_at: `2026-09-${String(10 + id).padStart(2, "0")}T12:00:00Z`,
  ...extra,
});
const file = (name: string, folder = "supra"): File => ({ sha256: name.padEnd(64, "0"), car_folder: folder, relative_path: `${folder}/${name}`, format: "sto", present: true, linked_setup_id: null });
const colors = carColors(["supra", "mx5"], []);

describe("car colours", () => {
  it("assigns one colour per car in first-seen order and wraps after the third", () => {
    resetCarOrder();
    const c = carColors(["a", "b", "c", "d"], []);
    expect([...c.values()]).toEqual([CAR_COLORS[0], CAR_COLORS[1], CAR_COLORS[2], CAR_COLORS[0]]);
    // A car already seen (on Sessions, say) keeps its colour whatever order this page lists it in.
    expect(carColors(["c", "a"], []).get("c")).toBe(CAR_COLORS[2]);
    resetCarOrder();
    expect(carColors(["B"], ["b", "a"]).get("a")).toBe(CAR_COLORS[1]);
  });
});

describe("the Car dropdown (#38)", () => {
  it("offers the cars that have setups, with their setup counts, and not a car that only has unlinked files", () => {
    const g = groupLibrary(
      [setup(1, ["a.sto"]), setup(2, [], { car_path: "mx5", car_name: "Global Mazda MX-5 Cup" }), setup(3, [], { car_path: "mx5", car_name: "Global Mazda MX-5 Cup" })],
      [file("loose.sto", "dallara"), file("other.sto", "supra")],
      colors,
    );
    expect(g).toHaveLength(3);
    const offered = carsWithSetups(g);
    expect(offered.map((c) => [c.carPath, c.setups])).toEqual(expect.arrayContaining([["supra", 1], ["mx5", 2]]));
    expect(offered.map((c) => c.carPath)).not.toContain("dallara");
    expect(offered).toHaveLength(2);
  });

  it("offers nothing for an empty library", () => {
    expect(carsWithSetups(groupLibrary([], [], colors))).toEqual([]);
  });
});

describe("library by car", () => {
  it("shows a file name with other values as one entry with version rows, never two look-alike rows", () => {
    const g = groupLibrary([setup(1, ["Bristol race v3.sto"]), setup(2, ["Bristol race v3.sto"], { raced: true }), setup(3, ["Vegas.sto"])], [], colors);
    expect(g).toHaveLength(1);
    const names = g[0]!.entries.map((e) => (e.kind === "setup" ? e.name : e.kind));
    expect(names).toEqual(["Vegas.sto", "Bristol race v3.sto"]);
    const bristol = g[0]!.entries[1]!;
    expect(bristol.kind === "setup" && bristol.versions.map((v) => [v.setup.setup_id, v.number, v.latest])).toEqual([
      [2, 2, true],
      [1, 1, false],
    ]);
    expect(bristol.kind === "setup" && [bristol.seen, bristol.raced]).toEqual([4, true]);
  });

  it("joins names case-insensitively and through a setup that carries both", () => {
    const g = groupLibrary([setup(1, ["a.sto"]), setup(2, ["A.STO", "b.sto"]), setup(3, ["B.sto"])], [], colors);
    expect(g[0]!.entries).toHaveLength(1);
    expect(g[0]!.entries[0]!.kind === "setup" && g[0]!.entries[0]!.versions).toHaveLength(3);
  });

  it("collapses unnamed garage captures into one stacked entry with a count", () => {
    const g = groupLibrary([setup(1, []), setup(2, []), setup(3, []), setup(4, ["Named.sto"])], [], colors);
    const captures = g[0]!.entries.find((e) => e.kind === "captures")!;
    expect(captures.kind === "captures" && captures.setups.map((s) => s.setup_id)).toEqual([3, 2, 1]);
    expect(g[0]!.entries.filter((e) => e.kind === "captures")).toHaveLength(1);
    expect(g[0]!.setups).toBe(4);
  });

  it("puts files with unknown values under their car, after the setups, whatever the folder is called", () => {
    const withFolder = setup(1, ["A.sto"], { sto_files: [file("A.sto", "mx5cup")], car_path: "globalmazdamx5cup", car_name: "Global Mazda MX-5 Cup" });
    const g = groupLibrary([withFolder], [file("Imported 1.sto", "mx5cup"), file("Other.sto", "unknowncar")], carColors(["globalmazdamx5cup"], []));
    const mx5 = g.find((x) => x.carPath === "globalmazdamx5cup")!;
    expect(mx5.entries.map((e) => (e.kind === "file" ? `file ${e.name}` : e.kind))).toEqual(["setup", "file Imported 1.sto"]);
    expect(g.find((x) => x.carPath === "unknowncar")!.entries.map((e) => e.kind)).toEqual(["file"]);
  });

  it("filters by car and by raced, hiding a car with nothing left", () => {
    const all = [setup(1, ["A.sto"], { raced: true }), setup(2, ["B.sto"]), setup(3, ["C.sto"], { car_path: "mx5", car_name: "Mazda", raced: false })];
    expect(groupLibrary(all, [], colors, { cars: new Set(["MX5"]) }).map((g) => g.carPath)).toEqual(["mx5"]);
    const raced = groupLibrary(all, [file("Imported.sto")], colors, { raced: "raced" });
    expect(raced.map((g) => [g.carPath, g.setups, g.raced])).toEqual([["supra", 1, 1]]);
    expect(raced[0]!.entries.some((e) => e.kind === "file")).toBe(false);
    const not = groupLibrary(all, [file("Imported.sto")], colors, { raced: "not-raced" });
    expect(not.flatMap((g) => g.entries.map((e) => e.kind)).sort()).toEqual(["file", "setup", "setup"]);
  });

  it("orders cars by their newest sighting and carries each car's colour", () => {
    const g = groupLibrary([setup(1, ["A.sto"]), setup(9, ["B.sto"], { car_path: "mx5", car_name: "Mazda" })], [], colors);
    expect(g.map((x) => [x.carPath, x.color])).toEqual([
      ["mx5", CAR_COLORS[1]],
      ["supra", CAR_COLORS[0]],
    ]);
  });
});

describe("raced count", () => {
  it("is the server count for a raced setup, and unknown (null, not 0) when the server gave none", () => {
    expect(racedCountOf(setup(1, ["A.sto"], { raced: true, raced_count: 3 } as Partial<Setup>))).toBe(3);
    expect(racedCountOf(setup(1, ["A.sto"], { raced: true }))).toBeNull();
    expect(racedCountOf(setup(1, ["A.sto"], { raced: false, raced_count: 3 } as Partial<Setup>))).toBeNull();
    expect(racedCountOf(setup(1, ["A.sto"], { raced: true, raced_count: 0 } as Partial<Setup>))).toBeNull();
    expect(racedCountOf(setup(1, ["A.sto"], { raced: true, raced_count: "3" } as unknown as Partial<Setup>))).toBeNull();
  });

  it("adds over versions only when every raced version reports one", () => {
    const r = (id: number, n?: number) => setup(id, ["A.sto"], { raced: true, ...(n ? { raced_count: n } : {}) } as Partial<Setup>);
    expect(racedCountOver([r(1, 2), r(2, 3), setup(3, ["A.sto"])])).toBe(5);
    expect(racedCountOver([r(1, 2), r(2)])).toBeNull();
    expect(racedCountOver([setup(1, ["A.sto"])])).toBeNull();
  });

  it("reaches the library entry: a count for a named setup, none for a raced setup without one", () => {
    const g = groupLibrary([setup(1, ["A.sto"], { raced: true, raced_count: 4 } as Partial<Setup>), setup(2, ["B.sto"], { raced: true })], [], colors);
    const entry = (n: string) => g[0]!.entries.find((e) => e.kind === "setup" && e.name === n)!;
    const a = entry("A.sto");
    const b = entry("B.sto");
    expect(a.kind === "setup" && a.racedCount).toBe(4);
    expect(b.kind === "setup" && [b.raced, b.racedCount]).toEqual([true, null]);
  });
});

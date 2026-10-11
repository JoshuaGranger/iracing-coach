import { describe, expect, it } from "vitest";
import type { Schemas } from "../core/api";
import { carAccent } from "../core/palette";
import { carsFilesOnly, carsWithSetups, carsWithValues, fileNames, groupLibrary, racedCountOf, racedCountOver, groupTrackLibrary, entryUsage } from "./library";

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
describe("car accent (#73)", () => {
  it("is the car's own accent on its section and in the Car dropdown, the same one every page uses", () => {
    const g = groupLibrary([setup(1, ["A.sto"]), setup(9, ["B.sto"], { car_path: "mx5", car_name: "Mazda" })], []);
    expect(g.map((x) => [x.carPath, x.color])).toEqual([
      ["mx5", carAccent("mx5")],
      ["supra", carAccent("supra")],
    ]);
    expect(carsWithSetups(g).map((c) => c.color)).toEqual([carAccent("mx5"), carAccent("supra")]);
  });
});

describe("the Car dropdown (#38)", () => {
  it("offers the cars that have setups, with their setup counts, and not a car that only has unlinked files", () => {
    const g = groupLibrary(
      [setup(1, ["a.sto"]), setup(2, [], { car_path: "mx5", car_name: "Global Mazda MX-5 Cup" }), setup(3, [], { car_path: "mx5", car_name: "Global Mazda MX-5 Cup" })],
      [file("loose.sto", "dallara"), file("other.sto", "supra")],
    );
    expect(g).toHaveLength(3);
    const offered = carsWithSetups(g);
    expect(offered.map((c) => [c.carPath, c.setups])).toEqual(expect.arrayContaining([["supra", 1], ["mx5", 2]]));
    expect(offered.map((c) => c.carPath)).not.toContain("dallara");
    expect(offered).toHaveLength(2);
  });

  it("offers nothing for an empty library", () => {
    expect(carsWithSetups(groupLibrary([], []))).toEqual([]);
  });
});

describe("library by car", () => {
  it("shows a file name with other values as one entry with version rows, never two look-alike rows", () => {
    const g = groupLibrary([setup(1, ["Bristol race v3.sto"]), setup(2, ["Bristol race v3.sto"], { raced: true }), setup(3, ["Vegas.sto"])], []);
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
    const g = groupLibrary([setup(1, ["a.sto"]), setup(2, ["A.STO", "b.sto"]), setup(3, ["B.sto"])], []);
    expect(g[0]!.entries).toHaveLength(1);
    expect(g[0]!.entries[0]!.kind === "setup" && g[0]!.entries[0]!.versions).toHaveLength(3);
  });

  it("collapses unnamed garage captures into one stacked entry with a count", () => {
    const g = groupLibrary([setup(1, []), setup(2, []), setup(3, []), setup(4, ["Named.sto"])], []);
    const captures = g[0]!.entries.find((e) => e.kind === "captures")!;
    expect(captures.kind === "captures" && captures.setups.map((s) => s.setup_id)).toEqual([3, 2, 1]);
    expect(g[0]!.entries.filter((e) => e.kind === "captures")).toHaveLength(1);
    expect(g[0]!.setups).toBe(4);
  });

  it("puts files with unknown values under their car, after the setups, whatever the folder is called", () => {
    const withFolder = setup(1, ["A.sto"], { sto_files: [file("A.sto", "mx5cup")], car_path: "globalmazdamx5cup", car_name: "Global Mazda MX-5 Cup" });
    const g = groupLibrary([withFolder], [file("Imported 1.sto", "mx5cup"), file("Other.sto", "unknowncar")]);
    const mx5 = g.find((x) => x.carPath === "globalmazdamx5cup")!;
    expect(mx5.entries.map((e) => (e.kind === "file" ? `file ${e.name}` : e.kind))).toEqual(["setup", "file Imported 1.sto"]);
    expect(g.find((x) => x.carPath === "unknowncar")!.entries.map((e) => e.kind)).toEqual(["file"]);
  });

  it("filters by car and by raced, hiding a car with nothing left", () => {
    const all = [setup(1, ["A.sto"], { raced: true }), setup(2, ["B.sto"]), setup(3, ["C.sto"], { car_path: "mx5", car_name: "Mazda", raced: false })];
    expect(groupLibrary(all, [], { cars: new Set(["MX5"]) }).map((g) => g.carPath)).toEqual(["mx5"]);
    const raced = groupLibrary(all, [file("Imported.sto")], { raced: "raced" });
    expect(raced.map((g) => [g.carPath, g.setups, g.raced])).toEqual([["supra", 1, 1]]);
    expect(raced[0]!.entries.some((e) => e.kind === "file")).toBe(false);
    const not = groupLibrary(all, [file("Imported.sto")], { raced: "not-raced" });
    expect(not.flatMap((g) => g.entries.map((e) => e.kind)).sort()).toEqual(["file", "setup", "setup"]);
  });

  it("orders cars by their newest sighting", () => {
    const g = groupLibrary([setup(1, ["A.sto"]), setup(9, ["B.sto"], { car_path: "mx5", car_name: "Mazda" })], []);
    expect(g.map((x) => x.carPath)).toEqual(["mx5", "supra"]);
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
    const g = groupLibrary([setup(1, ["A.sto"], { raced: true, raced_count: 4 } as Partial<Setup>), setup(2, ["B.sto"], { raced: true })], []);
    const entry = (n: string) => g[0]!.entries.find((e) => e.kind === "setup" && e.name === n)!;
    const a = entry("A.sto");
    const b = entry("B.sto");
    expect(a.kind === "setup" && a.racedCount).toBe(4);
    expect(b.kind === "setup" && [b.raced, b.racedCount]).toEqual([true, null]);
  });
});

describe("files without values (#74)", () => {
  const named = (name: string, folder: string, car_name: string | null): File => ({ ...file(name, folder), car_name });

  it("sets cars that only have files apart from the cars with setups, and never counts their files as setups", () => {
    const g = groupLibrary([setup(1, ["A.sto"])], [named("a.sto", "acuraarx06gtp", "Acura ARX-06 GTP"), named("b.sto", "acuraarx06gtp", "Acura ARX-06 GTP"), file("c.sto", "supra")]);
    expect(carsWithValues(g).map((x) => x.carPath)).toEqual(["supra"]);
    const only = carsFilesOnly(g);
    expect(only.map((x) => [x.carPath, x.setups, fileNames(x)])).toEqual([["acuraarx06gtp", 0, ["a.sto", "b.sto"]]]);
    // a car with setups keeps its own files in its section
    expect(g.find((x) => x.carPath === "supra")!.entries.map((e) => e.kind)).toEqual(["setup", "file"]);
  });

  it("names the car by the sim's screen name, the folder only when no recording gave one", () => {
    const g = groupLibrary([], [named("a.sto", "acuraarx06gtp", "Acura ARX-06 GTP"), named("b.sto", "lonely", null), { ...file("c.sto", "blank"), car_name: "  " }]);
    expect(carsFilesOnly(g).map((x) => x.carName)).toEqual(["Acura ARX-06 GTP", "blank", "lonely"]);
  });

  it("prefers a setup's car name over a file's, and lists the cars A to Z by name", () => {
    const g = groupLibrary([setup(1, ["A.sto"], { car_path: "mx5cup", car_name: "Mazda MX-5 Cup" })], [named("a.sto", "mx5cup", "Other Name"), named("b.sto", "zeta", "Zeta GT"), named("c.sto", "alpha", "alpha gt")]);
    expect(carsWithValues(g)[0]!.carName).toBe("Mazda MX-5 Cup");
    expect(carsFilesOnly(g).map((x) => x.carName)).toEqual(["alpha gt", "Zeta GT"]);
  });

  it("gives every file-only car its accent and keeps them out of the Car dropdown", () => {
    const g = groupLibrary([setup(1, ["A.sto"])], [named("a.sto", "acuraarx06gtp", "Acura ARX-06 GTP")]);
    expect(carsFilesOnly(g)[0]!.color).toBe(carAccent("acuraarx06gtp"));
    expect(carsWithSetups(g).map((c) => c.carPath)).toEqual(["supra"]);
  });

  it("drops with the Raced filter and with a car filter that does not name it", () => {
    const files = [named("a.sto", "acuraarx06gtp", "Acura ARX-06 GTP")];
    expect(carsFilesOnly(groupLibrary([], files, { raced: "raced" }))).toEqual([]);
    expect(carsFilesOnly(groupLibrary([], files, { cars: new Set(["supra"]) }))).toEqual([]);
    expect(carsFilesOnly(groupLibrary([], files, { cars: new Set(["acuraarx06gtp"]) }))).toHaveLength(1);
  });
});

describe("recorded track membership", () => {
  it("uses session tracks and keeps unrecorded track membership separate", () => {
    const base = setup(1, ["Daytona.sto"]);
    const g = groupTrackLibrary([{ ...base, tracks: [{ track: "Lime Rock", session_count: 2, last_used_at: "2026-10-01T12:00:00Z" }] }, setup(2, ["unknown.sto"])], []);
    expect(g.map((x) => x.track)).toEqual(["Lime Rock", null]);
    const e = g[0]!.cars[0]!.entries[0]!;
    if (e.kind === "file") throw new Error("Expected setup");
    expect(entryUsage(e)).toEqual({ count: 2, last: "2026-10-01T12:00:00Z" });
  });
  it("does not add potentially overlapping session counts across versions", () => {
    const values = [setup(1, ["base.sto"]), setup(2, ["base.sto"])].map((s) => ({ ...s, session_count: 1, last_used_at: s.last_seen_at }));
    const e = groupLibrary(values, [])[0]!.entries[0]!;
    if (e.kind === "file") throw new Error("Expected setup");
    expect(entryUsage(e)).toEqual({ count: 1, last: values[1]!.last_used_at });
  });
});

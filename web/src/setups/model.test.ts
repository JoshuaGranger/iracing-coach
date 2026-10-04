import { describe, expect, it } from "vitest";
import type { Schemas } from "../core/api";
import { classifyCompare, compareLabel, comparePicks, compareScheme, currentSeason, groupSetupValues, isReadout, keyKind, normalizeSeason, sameNameAs, seenSpan, setupTitle, tuneTarget, words } from "./model";

type Setup = Schemas["SetupSummary"];
const setup = (id: number, names: string[], extra: Partial<Setup> = {}): Setup => ({
  setup_id: id,
  customer_id: 1,
  car_path: "supra",
  car_name: "Supra",
  names,
  sources: ["recording"],
  observation_count: 1,
  raced: false,
  first_seen_at: "2026-09-20T10:00:00Z",
  last_seen_at: "2026-09-20T12:00:00Z",
  ...extra,
});

describe("setup values", () => {
  it("splits camel case and snake_case into words", () => {
    expect(words("BumpStiffness")).toBe("Bump stiffness");
    expect(words("LeftFront")).toBe("Left front");
    expect(words("ARBPreload")).toBe("ARB preload");
    expect(words("left_front")).toBe("Left front");
    expect(words("brake_pressure_bias")).toBe("Brake pressure bias");
  });

  it("groups by key path and keeps garage readouts and run readings apart from the settings", () => {
    const values = {
      "Chassis.LeftFront.Camber": "-3.2 deg",
      "Chassis.LeftFront.SpringRate": "1100 lb/in",
      "Chassis.LeftFront.CornerWeight": "1480 lb",
      "TiresAero.LeftFrontTire.LastHotPressure": "29.4 psi",
      "TiresAero.LeftFrontTire.StartingPressure": "26 psi",
      "Chassis.Rear.ArbPreload": "5 Nm",
      "Chassis.Rear.TrackBar": "5 in",
      Other: "x",
    };
    const g = groupSetupValues(values);
    expect(g.settingCount).toBe(5);
    expect(g.settings.map((s) => s.group).sort()).toEqual(["Chassis · Left front", "Chassis · Rear", "General", "Tires aero · Left front tire"]);
    expect(g.readouts.flatMap((r) => r.items.map((i) => i.key)).sort()).toEqual(["Chassis.LeftFront.CornerWeight", "Chassis.Rear.ArbPreload"]);
    expect(g.readings.flatMap((r) => r.items.map((i) => i.key))).toEqual(["TiresAero.LeftFrontTire.LastHotPressure"]);
  });

  it("classifies keys as the backend does: readings, computed readouts, settings", () => {
    expect(keyKind("TiresAero.LeftFrontTire.LastTemps")).toBe("reading");
    expect(keyKind("Chassis.Misc.UpdateCount")).toBe("reading");
    expect(keyKind("Chassis.Front.CrossWeight")).toBe("readout");
    expect(keyKind("Chassis.Front.NoseWeight")).toBe("readout");
    expect(keyKind("Chassis.LeftFront.TravelToCoilBind")).toBe("readout");
    // The stock-car front ARB lists LinkSlack as the adjustment and Preload as the computed torque; a differential's preload is a setting.
    expect(keyKind("Chassis.FrontArb.Preload")).toBe("readout");
    expect(keyKind("Chassis.RearArb.Preload")).toBe("readout");
    expect(keyKind("Drivetrain.Differential.Preload")).toBe("setting");
    expect(keyKind("Chassis.LeftFront.Camber")).toBe("setting");
  });

  it("reads exported-sheet keys: snake_case leaves are readouts and readings too", () => {
    expect(keyKind("chassis.left_front.corner_weight")).toBe("readout");
    expect(keyKind("chassis.left_front.ride_height")).toBe("readout");
    expect(keyKind("tires.left_front.last_hot_pressure")).toBe("reading");
    expect(keyKind("chassis.left_front.camber")).toBe("setting");
    const g = groupSetupValues({ "chassis.left_front.camber": "-3.2", "chassis.left_front.corner_weight": "1480" });
    expect(g.settings[0]).toEqual({ group: "Chassis · Left front", items: [{ key: "chassis.left_front.camber", label: "Camber", value: "-3.2" }] });
    expect(g.readouts).toHaveLength(1);
  });

  it("follows Compare's rule, not the identity list", () => {
    expect(isReadout("Chassis.X.Camber")).toBe(false);
    expect(isReadout("A.TreadRemaining")).toBe(true);
    expect(isReadout("A.UpdateCount")).toBe(true);
  });

  it("names a setup by its file, else as a garage capture", () => {
    expect(setupTitle({ setup_id: 1, names: ["Race v3.sto"], car_name: "Supra", car_path: "x" })).toBe("Race v3.sto");
    expect(setupTitle({ setup_id: 1, names: [], car_name: "Supra", car_path: "x" })).toBe("Garage capture");
  });
});

describe("setup compare", () => {
  const rows = [
    { key: "Chassis.LeftFront.Camber", status: "changed" as const, left: "-3.2", right: "-3.6" },
    { key: "Chassis.LeftRear.TrackBarHeight", status: "unknown" as const, left: "5 in", right: null },
    { key: "Chassis.LeftFront.CornerWeight", status: "changed" as const, left: "1480", right: "1475", readout: true },
    { key: "Chassis.RightFront.CornerWeight", status: "identical" as const, left: "1600", right: "1600", readout: true },
    { key: "Chassis.Rear.Spring", status: "identical" as const, left: "9", right: "9" },
  ];

  it("puts changed and unknown first, then identical settings; readouts apart; missing is a dash and never equal", () => {
    const c = classifyCompare(rows);
    expect(c.changed.map((r) => r.key)).toEqual(["Chassis.LeftFront.Camber"]);
    expect(c.unknown.map((r) => [r.key, r.right])).toEqual([["Chassis.LeftRear.TrackBarHeight", "-"]]);
    expect(c.identical.map((r) => r.key)).toEqual(["Chassis.Rear.Spring"]);
    expect(c.readouts.map((r) => [r.key, r.kind])).toEqual([
      ["Chassis.LeftFront.CornerWeight", "changed"],
      ["Chassis.RightFront.CornerWeight", "identical"],
    ]);
    expect(c.changed[0]!.label).toBe("Chassis · Left front · Camber");
  });

  it("leaves run readings out of a comparison", () => {
    const c = classifyCompare([...rows, { key: "TiresAero.LeftFrontTire.LastHotPressure", status: "changed", left: "29", right: "30" }]);
    expect(c.changed.map((r) => r.key)).toEqual(["Chassis.LeftFront.Camber"]);
  });

  it("counts a readout as moved only when its values differ", () => {
    expect(classifyCompare(rows).readoutsMoved).toBe(1);
    expect(classifyCompare([{ key: "A.CornerWeight", status: "unknown", left: "1", right: null, readout: true }]).readoutsMoved).toBe(0);
  });

  it("labels rows with our words, not the backend's field name", () => {
    expect(classifyCompare([{ key: "chassis.left_front.camber", status: "changed", left: "1", right: "2", label: "chassis.left_front.camber" }]).changed[0]!.label).toBe("Chassis · Left front · Camber");
    expect(compareLabel("Solo")).toBe("Solo");
  });
});

describe("compare pairs", () => {
  const recorded = setup(1, ["A.sto"]);
  const exported = setup(2, ["B.sto"], { sources: ["htm_export"] });
  const mixed = setup(3, ["C.sto"], { sources: ["htm_export", "recording"] });

  it("treats a setup known only from an exported sheet as the export scheme", () => {
    expect(compareScheme(recorded)).toBe("recording");
    expect(compareScheme(exported)).toBe("export");
    expect(compareScheme(mixed)).toBe("recording");
    expect(compareScheme(setup(4, [], { sources: [] }))).toBe("recording");
  });

  it("offers same-scheme setups of the same car and gives the others a reason", () => {
    const other = setup(5, ["D.sto"], { car_path: "SUPRA" });
    const elsewhere = setup(6, ["E.sto"], { car_path: "mx5" });
    const picks = comparePicks(recorded, [recorded, exported, mixed, other, elsewhere]);
    expect(picks.map((p) => [p.setup.setup_id, p.blocked])).toEqual([
      [2, "Export only"],
      [3, null],
      [5, null],
    ]);
    expect(comparePicks(exported, [recorded, exported]).map((p) => p.blocked)).toEqual(["Recorded"]);
  });
});

describe("season", () => {
  it("follows the iRacing quarters", () => {
    expect(currentSeason(new Date("2026-10-02T00:00:00Z"))).toBe("2026S4");
    expect(currentSeason(new Date("2026-07-15T00:00:00Z"))).toBe("2026S3");
    expect(currentSeason(new Date("2026-04-01T00:00:00Z"))).toBe("2026S2");
    expect(currentSeason(new Date("2026-01-20T00:00:00Z"))).toBe("2026S1");
    expect(currentSeason(new Date("2026-12-20T00:00:00Z"))).toBe("2027S1");
  });

  it("reads 26S4 as 2026S4, the form the backend matches", () => {
    expect(normalizeSeason("26S4")).toBe("2026S4");
    expect(normalizeSeason(" 2026 s4 ")).toBe("2026S4");
    expect(normalizeSeason("2026S4")).toBe("2026S4");
    expect(normalizeSeason("Fall 2026")).toBe("Fall 2026");
  });
});

describe("Tune this setup", () => {
  const obs = (extra: Partial<Schemas["SetupObservation"]>): Schemas["SetupObservation"] => ({ source: "recording", observed_at: "2026-09-20T12:00:00Z", ...extra });

  it("opens the newest recording observation that carries an analysis", () => {
    const target = tuneTarget([
      obs({ session_id: 1, analysis_id: "an-1", observed_at: "2026-09-01T12:00:00Z" }),
      obs({ session_id: 3, analysis_id: "an-3", observed_at: "2026-09-20T12:00:00Z" }),
      obs({ session_id: 2, analysis_id: null, observed_at: "2026-09-25T12:00:00Z" }),
      obs({ source: "garage_commit", visit_id: "v1", observed_at: "2026-09-30T12:00:00Z" }),
    ]);
    expect(target).toEqual({ analysisId: "an-3" });
  });

  it("says why there is nothing to open", () => {
    expect(tuneTarget([obs({ source: "garage_commit", visit_id: "v1" })])).toEqual({ blocked: "No recording yet" });
    expect(tuneTarget([obs({ session_id: 2 })])).toEqual({ blocked: "Recording not analyzed" });
    expect(tuneTarget([])).toEqual({ blocked: "No recording yet" });
  });
});

describe("setups that share a file name", () => {
  const mk = (id: number, names: string[], extra: Partial<Setup> = {}) => setup(id, names, extra);

  it("lists the other setups with a shared name, and none for unnamed or unique ones", () => {
    const all = [mk(1, ["R.sto"]), mk(2, ["R.sto", "R copy.sto"]), mk(3, ["Other.sto"]), mk(4, [])];
    expect(sameNameAs(all[0]!, all).map((s) => s.setup_id)).toEqual([2]);
    expect(sameNameAs(all[1]!, all).map((s) => s.setup_id)).toEqual([1]);
    expect(sameNameAs(all[2]!, all)).toEqual([]);
    expect(sameNameAs(all[3]!, all)).toEqual([]);
  });

  it("matches the file name without regard to case, and only within one car", () => {
    const all = [mk(1, ["Bristol race v3.sto"]), mk(2, ["bristol RACE v3.sto"]), mk(3, ["Bristol race v3.sto"], { car_path: "mx5" })];
    expect(sameNameAs(all[0]!, all).map((s) => s.setup_id)).toEqual([2]);
  });

  it("shows one date when first and last sighting share a day, else the span", () => {
    expect(seenSpan(mk(1, []))).toBe("Sep 20, 2026");
    expect(seenSpan(mk(1, [], { first_seen_at: "2026-09-20T10:00:00Z", last_seen_at: "2026-09-27T09:00:00Z" }))).toBe("Sep 20, 2026 to Sep 27, 2026");
  });
});

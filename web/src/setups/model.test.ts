import { describe, expect, it } from "vitest";
import type { Schemas } from "../core/api";
import { classifyCompare, compareLabel, currentSeason, groupSetupValues, isReadout, setupTitle, tuneTarget, words } from "./model";

describe("setup values", () => {
  it("splits camel case into words", () => {
    expect(words("BumpStiffness")).toBe("Bump stiffness");
    expect(words("LeftFront")).toBe("Left front");
    expect(words("ARBPreload")).toBe("ARB preload");
  });

  it("groups by key path and keeps derived values and run readings apart", () => {
    const values = {
      "Chassis.LeftFront.Camber": "-3.2 deg",
      "Chassis.LeftFront.SpringRate": "1100 lb/in",
      "Chassis.LeftFront.CornerWeight": "1480 lb",
      "TiresAero.LeftFrontTire.LastHotPressure": "29.4 psi",
      "TiresAero.LeftFrontTire.StartingPressure": "26 psi",
      "Chassis.Rear.ArbPreload": "5 Nm",
      "Chassis.Rear.TrackBar": "5 in",
      "Other": "x",
    };
    const g = groupSetupValues(values, []);
    expect(g.settingCount).toBe(5);
    expect(g.settings.map((s) => s.group).sort()).toEqual(["Chassis · Left front", "Chassis · Rear", "General", "Tires aero · Left front tire"]);
    expect(g.readouts.flatMap((r) => r.items.map((i) => i.key)).sort()).toEqual([
      "Chassis.LeftFront.CornerWeight",
      "Chassis.Rear.ArbPreload",
      "TiresAero.LeftFrontTire.LastHotPressure",
    ]);
  });

  it("honours the server's excluded key list as well as the generated patterns", () => {
    expect(isReadout("Chassis.X.Camber", new Set(["Chassis.X.Camber"]))).toBe(true);
    expect(isReadout("Chassis.X.Camber", new Set())).toBe(false);
    expect(isReadout("A.TreadRemaining", new Set())).toBe(true);
    expect(isReadout("A.UpdateCount", new Set())).toBe(true);
  });

  it("names a setup by its file, else by car", () => {
    expect(setupTitle({ setup_id: 1, names: ["Race v3.sto"], car_name: "Supra", car_path: "x" })).toBe("Race v3.sto");
    expect(setupTitle({ setup_id: 1, names: [], car_name: "Supra", car_path: "x" })).toBe("Unnamed Supra setup");
  });
});

describe("setup compare", () => {
  const rows = [
    { key: "Chassis.LeftFront.Camber", status: "changed" as const, left: "-3.2", right: "-3.6" },
    { key: "Chassis.LeftRear.TrackBarHeight", status: "unknown" as const, left: "5 in", right: null },
    { key: "Chassis.LeftFront.CornerWeight", status: "changed" as const, left: "1480", right: "1475", readout: true },
    { key: "Chassis.Rear.Spring", status: "identical" as const, left: "9", right: "9" },
  ];

  it("puts changed and unknown first, readouts apart, identical last; missing is shown as a dash and never equal", () => {
    const c = classifyCompare(rows);
    expect(c.changed.map((r) => r.key)).toEqual(["Chassis.LeftFront.Camber"]);
    expect(c.unknown.map((r) => [r.key, r.right])).toEqual([["Chassis.LeftRear.TrackBarHeight", "-"]]);
    expect(c.readout.map((r) => r.key)).toEqual(["Chassis.LeftFront.CornerWeight"]);
    expect(c.identical).toHaveLength(1);
    expect(c.changed[0]!.label).toBe("Chassis · Left front · Camber");
  });

  it("uses the backend label when it has one", () => {
    expect(classifyCompare([{ key: "A.B", status: "changed", left: "1", right: "2", label: "Front camber" }]).changed[0]!.label).toBe("Front camber");
    expect(compareLabel("Solo")).toBe("Solo");
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
    expect(tuneTarget([obs({ source: "garage_commit", visit_id: "v1" })])).toEqual({ blocked: "No recording used this setup yet." });
    expect(tuneTarget([obs({ session_id: 2 })])).toEqual({ blocked: "The recording that used this setup is not analyzed yet." });
    expect(tuneTarget([])).toEqual({ blocked: "No recording used this setup yet." });
  });
});

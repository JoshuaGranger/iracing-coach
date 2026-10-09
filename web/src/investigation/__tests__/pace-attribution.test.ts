import { describe, expect, it } from "vitest";
import { buildSectionsAnalysis } from "../fixtures/sections";
import { parseInvestigation, parsePaceAttribution } from "../investigation-model";
import { orderedSections, paceFigures, sectionPriorities } from "../ui/SectionsView";

const priority = (id: string | null, loss = 0.31, grade: "slip_weighted" | "load_only" | null = "slip_weighted") => ({
  section_id: id, start_pct: 0.1, end_pct: 0.2, recoverable_s: loss, best_lap: 17,
  tire_energy_share: 0.18, tire_energy_grade: grade, future_optional: { keep: true },
});
const payload = (priorities = [priority("A")]) => ({ schema_version: 1, priorities, future_optional: { keep: true } });
const raw = () => buildSectionsAnalysis("pace-unit").investigation.race_investigation;

describe("optional derived section priorities", () => {
  it("leaves older projections unchanged and preserves optional producer fields", () => {
    expect(parseInvestigation(raw()).paceAttribution).toBeNull();
    expect(parsePaceAttribution(null)).toBeNull();
    const value = parsePaceAttribution(payload())!;
    expect(value.future_optional).toEqual({ keep: true });
    expect(value.priorities[0]!.future_optional).toEqual({ keep: true });
    expect(value.priorities[0]!.recoverableS).toBe(0.31);
  });

  it("fails clearly on incompatible versions, bounds, missing or malformed required fields", () => {
    expect(() => parsePaceAttribution({ ...payload(), schema_version: 2 })).toThrow(/schema_version/);
    expect(() => parsePaceAttribution(payload(Array.from({ length: 33 }, () => priority("A"))))).toThrow(/at most 32/);
    for (const key of Object.keys(priority("A")).filter((key) => key !== "future_optional")) {
      const item: Record<string, unknown> = priority("A");
      delete item[key];
      expect(() => parsePaceAttribution({ schema_version: 1, priorities: [item] })).toThrow(new RegExp(key));
    }
    expect(() => parsePaceAttribution(payload([{ ...priority("A"), best_lap: 1.5 }]))).toThrow(/timing/);
    expect(() => parsePaceAttribution(payload([{ ...priority("A"), tire_energy_share: 1.2 }]))).toThrow(/share/);
    expect(() => parsePaceAttribution({ schema_version: 1, priorities: [{ ...priority("A"), tire_energy_grade: "future" }] })).toThrow(/grade/);
  });

  it("orders worst first, leaves ties stable and hides unattached or unknown sections", () => {
    const reordered = parseInvestigation(raw(), payload([priority("A", 0.1), priority("B", 0.8), priority("C", 0.31)]));
    expect(orderedSections(reordered).map((s) => s.id)).toEqual(["B", "C", "A"]);
    const inv = parseInvestigation(raw(), payload([priority("C", 0.1), priority(null, 2), priority("unknown", 3), priority("B", 0.5), priority("A", 0.31), priority("A", 0.8)]));
    expect(orderedSections(inv).map((s) => s.id)).toEqual(["A", "B", "C"]);
    expect(sectionPriorities(inv).size).toBe(3);
    expect(sectionPriorities(inv).get("A")!.recoverableS).toBe(0.8);
    expect(inv.sections.sections.map((s) => s.id)).toEqual(["A", "B", "C"]);
    const tied = parseInvestigation(raw(), payload([priority("B"), priority("A")]));
    expect(orderedSections(tied).map((s) => s.id)).toEqual(["A", "B", "C"]);
    expect(orderedSections(parseInvestigation(raw())).map((s) => s.id)).toEqual(["A", "B", "C"]);
  });

  it("names the best section pass, formats the supplied energy and weakens only load-only energy", () => {
    const p = parsePaceAttribution(payload())!.priorities[0]!;
    expect(paceFigures(p)).toEqual({ time: "-0.31 s · best L17", energy: "18 % tire energy", energyEvidence: "derived" });
    expect(paceFigures({ ...p, tireEnergyGrade: "load_only" })).toEqual({ time: "-0.31 s · best L17", energy: "18 % tire energy proxy", energyEvidence: "proxy" });
    expect(paceFigures({ ...p, recoverableS: 0, tireEnergyShare: 0 }).time).toBe("0.00 s · best L17");
    expect(paceFigures({ ...p, tireEnergyShare: 0 }).energy).toBe("0 % tire energy");
    expect(paceFigures({ ...p, tireEnergyShare: null }).energy).toBeNull();
    expect(paceFigures({ ...p, tireEnergyGrade: null }).energy).toBeNull();
  });
});

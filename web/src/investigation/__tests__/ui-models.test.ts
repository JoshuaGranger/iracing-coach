import { describe, expect, it } from "vitest";
import type { InvestigationLap } from "../investigation-model";
import { criticalLayoutBody } from "../layout-model";
import { activeLayout, createLayout, deleteActiveLayout, duplicateLayout, editActiveLayout, initialLayoutSet, selectLayout, uniqueName } from "../layout-sets";
import { orderedPhases, phaseLetter } from "../ui/PhaseSelector";
import { changeDigits, changes, exposureWord, positionSegments, positionTicks, steeringLimit } from "../ui/RaceView";

describe("saved chart layouts: New, Copy, Delete (#4)", () => {
  it("starts from Default, names new layouts uniquely and makes them active", () => {
    const set = initialLayoutSet();
    expect(activeLayout(set).name).toBe("Default");
    const first = createLayout(set, "a");
    expect(first.layout.name).toBe("Custom");
    expect(first.set.activeId).toBe("a");
    const second = createLayout(first.set, "b");
    expect(second.layout.name).toBe("Custom 2");
    expect(uniqueName(second.set, "custom")).toBe("custom 3");
    // New starts from Default with fresh row ids.
    expect(second.layout.body.rows.map((row) => row.signals)).toEqual(criticalLayoutBody().rows.map((row) => row.signals));
    expect(second.layout.body.rows[0]!.row_id).not.toBe(criticalLayoutBody().rows[0]!.row_id);
  });

  it("copies the active layout as '<name> Copy' and deletes only a driver layout", () => {
    const set = createLayout(initialLayoutSet(), "a").set;
    const edited = editActiveLayout(set, { rows: [{ row_id: "r1", signals: ["speed"] }] }).set;
    const copy = duplicateLayout(edited, "c");
    expect(copy.layout.name).toBe("Custom Copy");
    expect(copy.layout.body.rows.map((row) => row.signals)).toEqual([["speed"]]);
    const deleted = deleteActiveLayout(copy.set)!;
    expect(deleted.deletedId).toBe("c");
    expect(activeLayout(deleted.set).builtin).toBe(true);
    expect(deleteActiveLayout(deleted.set)).toBeNull();
    expect(selectLayout(deleted.set, "a").activeId).toBe("a");
    expect(selectLayout(deleted.set, "missing").activeId).toBe(deleted.set.activeId);
  });

  it("never edits Default in place: the first edit makes 'Default Copy'", () => {
    const set = initialLayoutSet();
    const result = editActiveLayout(set, { rows: [{ row_id: "r1", signals: ["rpm"] }] }, "x");
    expect(result.created).toBe(true);
    expect(result.layout.name).toBe("Default Copy");
    expect(result.set.layouts.find((layout) => layout.builtin)!.body).toEqual(set.layouts[0]!.body);
  });
});

describe("P · Q · R (#1l)", () => {
  it("orders the event's phases P, Q, R with one segment per phase", () => {
    const phases = orderedPhases([
      { sessionId: 3, sessionType: "Race" },
      { sessionId: 1, sessionType: "Open Practice" },
      { sessionId: 2, sessionType: "Lone Qualify" },
      { sessionId: 4, sessionType: "Race" },
    ]);
    expect(phases.map((phase) => phaseLetter(phase.sessionType))).toEqual(["P", "Q", "R"]);
    expect(phases.map((phase) => phase.sessionId)).toEqual([1, 2, 3]);
  });
});

const lap = (number: number, position: number | null): InvestigationLap => ({ lap: number, lapTimeS: 30, position, flagState: "green", eligible: true, exclusionReasons: [], runNumber: 1, plotPace: true, greenExposure: number });

describe("position chart (#1n)", () => {
  it("labels a readable P axis, not only the ends", () => {
    expect(positionTicks(1, 18)).toEqual([1, 5, 10, 15, 18]);
    expect(positionTicks(3, 9)).toEqual([3, 6, 9]);
    expect(positionTicks(7, 14)).toEqual([7, 10, 12, 14]);
    expect(positionTicks(7, 8)).toEqual([7, 8]);
  });

  it("colours each lap's segment by gain, loss or hold and breaks over a missing lap", () => {
    const segments = positionSegments([lap(1, 12), lap(2, 10), lap(3, 10), lap(4, 11), lap(6, 9)]);
    expect(segments.map((segment) => segment.tone)).toEqual(["gain", "hold", "loss"]);
  });
});

describe("Race change chips (audit R1, R3, R7)", () => {
  it("converts the steering threshold with the backend's reference ratio, not the car's", () => {
    expect(steeringLimit(2, { basis: "road_wheel", steering_ratio: 16 } as never)).toBeCloseTo(2 / 12, 9);
    expect(steeringLimit(2, { basis: "road_wheel", steering_ratio: 16, threshold_reference_ratio: 10 } as never)).toBeCloseTo(0.2, 9);
    expect(steeringLimit(2, { basis: "steering_wheel" } as never)).toBe(2);
    const comparison = { metrics: [{ key: "steering", label: "Steering", unit: "deg", baseline: 1, compared: 1.14, delta: 0.14, evidence: null, detail: null }] } as never;
    expect(changes(comparison, { basis: "road_wheel", steering_ratio: 16 } as never)).toHaveLength(0);
  });

  it("uses per-unit decimals and the clean wording for Offline Testing", () => {
    expect(changeDigits({ key: "track_temperature", unit: "°F" }, false)).toBe(0);
    expect(changeDigits({ key: "traffic", unit: "%" }, false)).toBe(0);
    expect(changeDigits({ key: "steering", unit: "deg" }, true)).toBe(2);
    expect(exposureWord("Offline Testing")).toBe("clean");
    expect(exposureWord("Race")).toBe("green");
  });
});

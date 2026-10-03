import { describe, expect, it } from "vitest";
import {
  MAXIMUM_ROWS,
  addRow,
  applyLayoutAction,
  criticalLayoutBody,
  defaultLayoutBody,
  insertSignalRow,
  moveRow,
  moveRowToIndex,
  placeSignal,
  removeRow,
  removeSignal,
  secondarySignalOptions,
  validateAndRepair,
} from "../layout-model";

describe("trace layout rules (AnalysisTraceLayouts.cs)", () => {
  it("the factory layout is the C# default: ten rows, slip+yaw and lateral+longitudinal paired", () => {
    const body = defaultLayoutBody();
    expect(body.rows.map((row) => row.signals.join("+"))).toEqual(["speed", "delta", "throttle", "brake", "tire-wear", "gear", "rpm", "steering", "slip+yaw", "lateral-g+longitudinal-g"]);
    expect(validateAndRepair(body).changed).toBe(false);
  });

  it("the critical layout shows the six critical channels", () => {
    expect(criticalLayoutBody().rows.map((row) => row.signals[0])).toEqual(["speed", "delta", "throttle", "brake", "steering", "slip"]);
  });

  it("repairs unknown signals, duplicate ids, steering pairs and over-long layouts", () => {
    const repaired = validateAndRepair({
      rows: [
        { row_id: "x", signals: ["nonsense"] },
        { row_id: "x", signals: ["steering", "steering-wheel-angle"] },
        { row_id: "z", signals: ["speed", "speed"] },
        ...Array.from({ length: 12 }, (_, i) => ({ row_id: `r${i}`, signals: ["gear"] })),
      ],
    });
    expect(repaired.changed).toBe(true);
    expect(repaired.body.rows.length).toBe(MAXIMUM_ROWS);
    expect(repaired.body.rows[0]!.signals).toEqual(["speed"]);
    expect(repaired.body.rows[1]!.row_id).not.toBe("x");
    expect(repaired.body.rows[1]!.signals).toEqual(["steering"]);
    expect(repaired.body.rows[2]!.signals).toEqual(["speed"]);
    expect(validateAndRepair({ rows: [] }).body.rows.length).toBe(10);
  });

  it("inserts rows within bounds and refuses the eleventh", () => {
    const body = { rows: [{ row_id: "a", signals: ["speed"] }, { row_id: "b", signals: ["brake"] }] };
    const inserted = insertSignalRow(body, "rpm", 1)!;
    expect(inserted.rows.map((row) => row.signals[0])).toEqual(["speed", "rpm", "brake"]);
    expect(insertSignalRow(body, "rpm", 3)).toBeNull();
    expect(insertSignalRow(body, "nonsense", 0)).toBeNull();
    expect(insertSignalRow(defaultLayoutBody(), "rpm", 0)).toBeNull();
    expect(addRow(body)!.rows.length).toBe(3);
    expect(addRow(defaultLayoutBody())).toBeNull();
  });

  it("moves and removes rows, keeping at least one", () => {
    const body = defaultLayoutBody();
    const moved = moveRowToIndex(body, "default-row-1", 3)!;
    expect(moved.rows[3]!.row_id).toBe("default-row-1");
    expect(moveRowToIndex(body, "default-row-1", 0)).toBeNull();
    expect(moveRowToIndex(body, "default-row-1", 10)).toBeNull();
    expect(moveRow(body, "default-row-2", -1)!.rows[0]!.row_id).toBe("default-row-2");
    expect(removeRow(body, "default-row-1")!.rows.length).toBe(9);
    expect(removeRow({ rows: [{ row_id: "a", signals: ["speed"] }] }, "a")).toBeNull();
    expect(removeRow(body, "nope")).toBeNull();
  });

  it("pairs a dropped signal as the second trace but never road-wheel with wheel angle", () => {
    const body = { rows: [{ row_id: "a", signals: ["speed"] }, { row_id: "s", signals: ["steering"] }] };
    expect(placeSignal(body, "a", "rpm")!.rows[0]!.signals).toEqual(["speed", "rpm"]);
    expect(placeSignal(body, "a", "speed")).toBeNull();
    expect(placeSignal(body, "s", "steering-wheel-angle")).toBeNull();
    expect(placeSignal({ rows: [{ row_id: "a", signals: ["speed", "rpm"] }] }, "a", "gear")!.rows[0]!.signals).toEqual(["speed", "gear"]);
    expect(secondarySignalOptions("steering")).not.toContain("steering-wheel-angle");
    expect(secondarySignalOptions("steering")).not.toContain("steering");
  });

  it("removing the primary promotes the secondary; removing a lone signal removes the row", () => {
    const body = { rows: [{ row_id: "a", signals: ["speed", "rpm"] }, { row_id: "b", signals: ["gear"] }] };
    expect(removeSignal(body, "a", "speed")!.rows[0]!.signals).toEqual(["rpm"]);
    expect(removeSignal(body, "a", "rpm")!.rows[0]!.signals).toEqual(["speed"]);
    expect(removeSignal(body, "b", "gear")!.rows.length).toBe(1);
    expect(removeSignal(body, "b", "speed")).toBeNull();
  });

  it("applies the drag editor's actions", () => {
    const body = { rows: [{ row_id: "a", signals: ["speed"] }, { row_id: "b", signals: ["brake"] }] };
    expect(applyLayoutAction(body, { type: "move-row", rowId: "a", targetIndex: 1 })!.rows[1]!.row_id).toBe("a");
    expect(applyLayoutAction(body, { type: "insert-signal", signalId: "rpm", targetIndex: 2 })!.rows.length).toBe(3);
    expect(applyLayoutAction(body, { type: "place-signal", rowId: "b", signalId: "rpm" })!.rows[1]!.signals).toEqual(["brake", "rpm"]);
  });
});

import { describe, expect, it } from "vitest";
import {
  UNDO_LIMIT,
  addAt,
  addFirstFree,
  addGrowing,
  addRect,
  applyMove,
  applyResize,
  cellFromPoint,
  clampRect,
  gridFull,
  moveRect,
  moveVerdict,
  placeTargets,
  pushUndo,
  resizeRect,
  resizeVerdict,
  stepSize,
  swapTiles,
} from "./edit-model";
import { type LayoutBody, validateLayout } from "./layout";

const t = (id: string, metric: string, row: number, column: number, extra: object = {}) => ({ tile_id: id, metric, row, column, row_span: 1, column_span: 1, style: "number" as const, highlight_abs: false, ...extra });
// 2 x 3: position | last lap | gap ; fuel (2 wide) | free
const base = (): LayoutBody => ({ rows: 2, columns: 3, tiles: [t("a", "position", 0, 0), t("b", "last-lap", 0, 1), t("c", "leader-gap", 0, 2), t("d", "fuel", 1, 0, { column_span: 2, style: "bar" })] });
const at = (b: LayoutBody, id: string) => b.tiles.find((x) => x.tile_id === id)!;

describe("grid snapping", () => {
  const box = { left: 100, top: 50, width: 300, height: 200 };
  it("maps a point to its cell; outside the grid it clamps for a tile drag and is no cell for a palette drag", () => {
    expect(cellFromPoint(box, 2, 3, 101, 51, true)).toEqual({ row: 0, col: 0 });
    expect(cellFromPoint(box, 2, 3, 399, 249, true)).toEqual({ row: 1, col: 2 });
    expect(cellFromPoint(box, 2, 3, 250, 160, true)).toEqual({ row: 1, col: 1 });
    expect(cellFromPoint(box, 2, 3, 50, 400, true)).toEqual({ row: 1, col: 0 });
    expect(cellFromPoint(box, 2, 3, 50, 100, false)).toBeNull();
    expect(cellFromPoint(box, 2, 3, 400, 100, false)).toBeNull();
  });
  it("a moved rect snaps inside the grid, keeping the grabbed offset", () => {
    const b = base();
    expect(moveRect(b, at(b, "d"), { row: 1, col: 1 }, { row: 1, col: 2 })).toEqual({ row: 1, column: 1, row_span: 1, column_span: 2 });
    expect(moveRect(b, at(b, "d"), { row: 1, col: 0 }, { row: 1, col: 2 })).toEqual({ row: 1, column: 1, row_span: 1, column_span: 2 });
    expect(clampRect(b, { row: -3, column: 9, row_span: 1, column_span: 2 })).toEqual({ row: 0, column: 1, row_span: 1, column_span: 2 });
  });
});

describe("move and swap", () => {
  it("moves into free space", () => {
    const b = base();
    expect(moveVerdict(b, "d", { row: 1, column: 1, row_span: 1, column_span: 2 }).verdict).toBe("valid");
    const next = applyMove(b, "d", { row: 1, column: 1, row_span: 1, column_span: 2 })!;
    expect(at(next, "d")).toMatchObject({ row: 1, column: 1 });
    expect(validateLayout(next)).toEqual([]);
  });
  it("swaps when dropped exactly on an equal-sized occupied slot", () => {
    const b = base();
    expect(moveVerdict(b, "a", { row: 0, column: 2, row_span: 1, column_span: 1 })).toEqual({ verdict: "swap", swapWith: "c" });
    const next = applyMove(b, "a", { row: 0, column: 2, row_span: 1, column_span: 1 })!;
    expect(at(next, "a")).toMatchObject({ row: 0, column: 2 });
    expect(at(next, "c")).toMatchObject({ row: 0, column: 0 });
    expect(validateLayout(next)).toEqual([]);
  });
  it("refuses a drop over a tile of another size or over two tiles", () => {
    const b = base();
    expect(moveVerdict(b, "a", { row: 1, column: 0, row_span: 1, column_span: 1 }).verdict).toBe("invalid");
    expect(moveVerdict(b, "d", { row: 0, column: 0, row_span: 1, column_span: 2 }).verdict).toBe("invalid");
    expect(applyMove(b, "a", { row: 1, column: 0, row_span: 1, column_span: 1 })).toBeNull();
    expect(applyMove(b, "a", { row: 0, column: 0, row_span: 1, column_span: 1 })).toBeNull();
  });
  it("swapTiles refuses unequal sizes", () => {
    expect(swapTiles(base(), "a", "d")).toBeNull();
    expect(swapTiles(base(), "a", "a")).toBeNull();
  });
});

describe("resize", () => {
  it("previews valid and refused placements and applies only a valid one", () => {
    const b = base();
    const c = at(b, "c");
    expect(resizeRect(c, { row: 1, col: 2 })).toEqual({ row: 0, column: 2, row_span: 2, column_span: 1 });
    expect(resizeRect(c, { row: 0, col: 0 })).toEqual({ row: 0, column: 2, row_span: 1, column_span: 1 });
    expect(resizeVerdict(b, "c", { row: 0, column: 2, row_span: 2, column_span: 1 })).toBe("valid");
    expect(resizeVerdict(b, "b", { row: 0, column: 1, row_span: 2, column_span: 1 })).toBe("invalid");
    expect(applyResize(b, "b", { row: 0, column: 1, row_span: 2, column_span: 1 })).toBeNull();
    expect(at(applyResize(b, "c", { row: 0, column: 2, row_span: 2, column_span: 1 })!, "c").row_span).toBe(2);
  });
  it("steps width and height from the same origin", () => {
    const b = base();
    expect(stepSize(b, "c", "height", 1)).not.toBeNull();
    expect(stepSize(b, "c", "width", 1)).toBeNull();
    expect(stepSize(b, "c", "height", -1)).toBeNull();
    expect(at(stepSize(b, "d", "width", -1)!, "d").column_span).toBe(1);
  });
});

describe("palette", () => {
  it("drag-in previews at the cell with the metric's size and adds only where it fits", () => {
    const b = base();
    expect(addRect(b, "speed", { row: 1, col: 2 })).toEqual({ row: 1, column: 2, row_span: 1, column_span: 1 });
    expect(addRect(b, "flag", { row: 1, col: 2 })).toEqual({ row: 1, column: 1, row_span: 1, column_span: 2 });
    expect(addAt(b, "speed", { row: 1, column: 1, row_span: 1, column_span: 1 })).toBeNull();
    const next = addAt(b, "speed", { row: 1, column: 2, row_span: 1, column_span: 1 })!;
    expect(next.tiles.at(-1)).toMatchObject({ tile_id: "speed-1", metric: "speed", row: 1, column: 2, style: "trend" });
    expect(validateLayout(next)).toEqual([]);
  });
  it("tap-to-add takes the first free spot and a full grid refuses", () => {
    const b = base();
    const next = addFirstFree(b, "fuel-laps")!;
    expect(next.tiles.at(-1)).toMatchObject({ metric: "fuel-laps", row: 1, column: 2, style: "number" });
    expect(gridFull(next)).toBe(true);
    expect(addFirstFree(next, "speed")).toBeNull();
    // A status tile falls back to one cell when two do not fit.
    expect(addFirstFree(b, "flag")!.tiles.at(-1)).toMatchObject({ column_span: 1, style: "status" });
  });
});

describe("tap-to-add on a full grid", () => {
  it("adds a row, then a column, up to 8 x 8", () => {
    const full = addFirstFree(base(), "fuel-laps")!;
    const grown = addGrowing(full, "speed")!;
    expect(grown.rows).toBe(3);
    expect(grown.tiles.at(-1)).toMatchObject({ metric: "speed", row: 2, column: 0 });
    expect(validateLayout(grown)).toEqual([]);
    const tall: LayoutBody = { rows: 8, columns: 1, tiles: Array.from({ length: 8 }, (_, i) => t(`x${i}`, "speed", i, 0)) };
    expect(addGrowing(tall, "rpm")!.columns).toBe(2);
    const max: LayoutBody = { rows: 8, columns: 8, tiles: [t("all", "speed", 0, 0, { row_span: 8, column_span: 8 })] };
    expect(addGrowing(max, "rpm")).toBeNull();
  });
});

describe("select-then-place", () => {
  it("lists free origins and equal-sized swap partners", () => {
    const b = base();
    expect(placeTargets(b, "a")).toEqual({ cells: [{ row: 1, col: 2 }], swaps: ["b", "c"] });
    expect(placeTargets(b, "d")).toEqual({ cells: [{ row: 1, col: 1 }], swaps: [] });
  });
});

describe("undo", () => {
  it("keeps the newest bodies, bounded", () => {
    let stack: LayoutBody[] = [];
    for (let i = 0; i < UNDO_LIMIT + 5; i++) stack = pushUndo(stack, { rows: 1, columns: 1 + (i % 8), tiles: [] });
    expect(stack.length).toBe(UNDO_LIMIT);
    expect(stack.at(-1)!.columns).toBe(1 + ((UNDO_LIMIT + 4) % 8));
  });
});

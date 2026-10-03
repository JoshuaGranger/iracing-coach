import { describe, expect, it } from "vitest";
import { TILES } from "../gen/live-channels";
import { type LayoutBody, addTile, fits, findFreeSpot, newLayoutId, placeTile, removeTile, resizeGrid, slugify, swapMetric, validateLayout } from "./layout";

const t = (id: string, metric: string, row: number, column: number, extra: object = {}) => ({ tile_id: id, metric, row, column, row_span: 1, column_span: 1, style: "number" as const, highlight_abs: false, ...extra });
const base = (): LayoutBody => ({ rows: 2, columns: 3, tiles: [t("a", "position", 0, 0), t("b", "last-lap", 0, 1), t("c", "coach-cue", 1, 2, { style: "status", column_span: 1 })] });

describe("live layout model", () => {
  it("accepts a valid layout and reports structural problems", () => {
    expect(validateLayout(base())).toEqual([]);
    const bad: LayoutBody = { rows: 2, columns: 2, tiles: [t("a", "position", 0, 0, { column_span: 2 }), t("b", "last-lap", 0, 1), t("a", "nope", 1, 1), t("c", "speed", 1, 1, { row_span: 2 }), t("d", "brake", 0, 0, { style: "status" })] };
    const messages = validateLayout(bad).map((i) => i.message);
    expect(messages).toContain("Duplicate tile id.");
    expect(messages).toContain("Unknown metric nope.");
    expect(messages).toContain("Tile does not fit the grid.");
    expect(messages.some((m) => m.startsWith("Overlaps"))).toBe(true);
    expect(messages.some((m) => m.includes("cannot show as status"))).toBe(true);
    expect(validateLayout({ rows: 9, columns: 3, tiles: [] })[0]!.message).toBe("Rows and columns must be 1 to 8.");
  });

  it("only the four status metrics allow Status; units must come from the table", () => {
    const body = { rows: 1, columns: 2, tiles: [t("a", "flag", 0, 0, { style: "status" }), t("b", "speed", 0, 1, { unit: "furlongs" })] };
    expect(validateLayout(body).map((i) => i.message)).toEqual(["Speed has no unit furlongs."]);
  });

  it("finds the first free spot row-major and refuses overlaps and out-of-grid placement", () => {
    const b = base();
    expect(findFreeSpot(b)).toEqual({ row: 0, column: 2 });
    expect(fits(b, { row: 0, column: 0, row_span: 1, column_span: 2 })).toBe(false);
    expect(fits(b, { row: 0, column: 0, row_span: 1, column_span: 2 }, "b")).toBe(false);
    expect(fits(b, { row: 1, column: 0, row_span: 1, column_span: 2 })).toBe(true);
    expect(fits(b, { row: 1, column: 2, row_span: 1, column_span: 2 })).toBe(false);
    expect(placeTile(b, "a", { row: 1, column: 0, row_span: 1, column_span: 1 })!.tiles[0]).toMatchObject({ row: 1, column: 0 });
    expect(placeTile(b, "a", { row: 0, column: 1, row_span: 1, column_span: 1 })).toBeNull();
  });

  it("adds a tile at its default style until the grid is full", () => {
    let b = base();
    b = addTile(b, "fuel")!;
    expect(b.tiles.at(-1)).toMatchObject({ metric: "fuel", style: "bar", row: 0, column: 2 });
    expect(b.tiles.at(-1)!.tile_id).toBe("fuel-1");
    b = addTile(b, "fuel")!;
    expect(b.tiles.at(-1)!.tile_id).toBe("fuel-2");
    b = addTile(b, "gear")!;
    expect(b.tiles).toHaveLength(6);
    expect(addTile(b, "rpm")).toBeNull();
    expect(validateLayout(b)).toEqual([]);
  });

  it("resizes the grid only when every tile still fits, and swaps metrics to the new defaults", () => {
    const b = base();
    expect(resizeGrid(b, 2, 2)).toBeNull();
    expect(resizeGrid(b, 3, 4)).toMatchObject({ rows: 3, columns: 4 });
    expect(resizeGrid(b, 0, 3)).toBeNull();
    const swapped = swapMetric(base(), "a", "throttle")!;
    expect(swapped.tiles[0]).toMatchObject({ metric: "throttle", style: "gauge", tile_id: "a" });
    expect(removeTile(b, "a").tiles.map((x) => x.tile_id)).toEqual(["b", "c"]);
  });

  it("makes layout ids that satisfy the server pattern and stay unique", () => {
    expect(slugify("My Race Layout!")).toBe("my-race-layout");
    expect(slugify("***")).toBe("layout");
    expect(newLayoutId("Race", ["race", "race-2"])).toBe("race-3");
    expect(newLayoutId("Qualifying Setup", [])).toMatch(/^[a-z0-9-]+$/);
  });

  it("covers every catalog metric", () => {
    expect(TILES).toHaveLength(32);
    for (const spec of TILES) expect(addTile({ rows: 1, columns: 1, tiles: [] }, spec.id)).not.toBeNull();
  });
});

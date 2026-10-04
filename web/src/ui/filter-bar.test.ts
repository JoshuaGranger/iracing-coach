import { describe, expect, it } from "vitest";
import { CONTROL_GAP_REM, DEFAULT_CONTROL_REM, TITLE_GAP_REM, filtersLabel, fitsInline } from "./filter-bar";

const root = 16;
const controls = (n: number, rem = DEFAULT_CONTROL_REM) => Array.from({ length: n }, () => rem);
const need = (title: number, rems: number[]) => title + (TITLE_GAP_REM + rems.reduce((a, b) => a + b, 0) + (rems.length - 1) * CONTROL_GAP_REM) * root;

describe("fitsInline", () => {
  it("fits when the title and every control at its width fit in the row, and not one pixel less", () => {
    const rems = controls(5);
    const width = need(160, rems);
    expect(fitsInline({ rowWidth: width, titleWidth: 160, controlRems: rems, rootPx: root })).toBe(true);
    expect(fitsInline({ rowWidth: width - 1, titleWidth: 160, controlRems: rems, rootPx: root })).toBe(false);
  });

  it("is a phone-width collapse and a desktop-width inline layout for the Sessions set", () => {
    const rems = controls(5);
    expect(fitsInline({ rowWidth: 390 - 32, titleWidth: 130, controlRems: rems, rootPx: root })).toBe(false);
    expect(fitsInline({ rowWidth: 1440 - 64, titleWidth: 130, controlRems: rems, rootPx: root })).toBe(true);
  });

  it("scales with the root font size, so a larger browser default collapses sooner", () => {
    const rems = controls(4);
    const width = need(160, rems);
    expect(fitsInline({ rowWidth: width, titleWidth: 160, controlRems: rems, rootPx: 16 })).toBe(true);
    expect(fitsInline({ rowWidth: width, titleWidth: 160, controlRems: rems, rootPx: 20 })).toBe(false);
  });

  it("never fits an unmeasured row, and always fits no controls", () => {
    expect(fitsInline({ rowWidth: 0, titleWidth: 0, controlRems: controls(2), rootPx: root })).toBe(false);
    expect(fitsInline({ rowWidth: 0, titleWidth: 0, controlRems: [], rootPx: root })).toBe(true);
  });
});

describe("filtersLabel", () => {
  it("reads Filters, or Filters with the number in force", () => {
    expect(filtersLabel(0)).toBe("Filters");
    expect(filtersLabel(3)).toBe("Filters · 3");
  });
});

import { describe, expect, it } from "vitest";
import { CLEAR_REM, CONTROL_GAP_REM, DEFAULT_CONTROL_REM, TITLE_GAP_REM, filtersLabel, fitsInline } from "./filter-bar";

const root = 16;
// Title 160 px, then the gap, then five controls with four gaps.
const controls = (n: number, rem = DEFAULT_CONTROL_REM) => Array.from({ length: n }, () => rem);
const need = (title: number, rems: number[], clear = false) => title + (TITLE_GAP_REM + rems.reduce((a, b) => a + b, 0) + (rems.length - 1 + (clear ? 1 : 0)) * CONTROL_GAP_REM + (clear ? CLEAR_REM : 0)) * root;

describe("fitsInline", () => {
  it("fits when the title and every control at its minimum fit in the row, and not one pixel less", () => {
    const rems = controls(5);
    const width = need(160, rems);
    expect(fitsInline({ rowWidth: width, titleWidth: 160, controlRems: rems, clear: false, rootPx: root })).toBe(true);
    expect(fitsInline({ rowWidth: width - 1, titleWidth: 160, controlRems: rems, clear: false, rootPx: root })).toBe(false);
  });

  it("is a phone-width collapse and a desktop-width inline layout for the Sessions set", () => {
    const rems = [7.5, 7.5, 7.5, 7.5, 7.5, 15];
    expect(fitsInline({ rowWidth: 390 - 32, titleWidth: 130, controlRems: rems, clear: false, rootPx: root })).toBe(false);
    expect(fitsInline({ rowWidth: 1440 - 64, titleWidth: 130, controlRems: rems, clear: false, rootPx: root })).toBe(true);
  });

  it("counts the inline Clear button only while something is chosen", () => {
    const rems = controls(3);
    const width = need(160, rems);
    expect(fitsInline({ rowWidth: width, titleWidth: 160, controlRems: rems, clear: false, rootPx: root })).toBe(true);
    expect(fitsInline({ rowWidth: width, titleWidth: 160, controlRems: rems, clear: true, rootPx: root })).toBe(false);
    expect(fitsInline({ rowWidth: need(160, rems, true), titleWidth: 160, controlRems: rems, clear: true, rootPx: root })).toBe(true);
  });

  it("scales with the root font size, so a larger browser default collapses sooner", () => {
    const rems = controls(4);
    const width = need(160, rems);
    expect(fitsInline({ rowWidth: width, titleWidth: 160, controlRems: rems, clear: false, rootPx: 16 })).toBe(true);
    expect(fitsInline({ rowWidth: width, titleWidth: 160, controlRems: rems, clear: false, rootPx: 20 })).toBe(false);
  });

  it("never fits an unmeasured row, and always fits no controls", () => {
    expect(fitsInline({ rowWidth: 0, titleWidth: 0, controlRems: controls(2), clear: false, rootPx: root })).toBe(false);
    expect(fitsInline({ rowWidth: 0, titleWidth: 0, controlRems: [], clear: false, rootPx: root })).toBe(true);
  });
});

describe("filtersLabel", () => {
  it("reads Filters, or Filters with the number in force", () => {
    expect(filtersLabel(0)).toBe("Filters");
    expect(filtersLabel(3)).toBe("Filters · 3");
  });
});

import { describe, expect, it } from "vitest";
import { CONTROL_GAP_REM, DEFAULT_CONTROL_REM, TITLE_GAP_REM, TRIGGER_REM, filtersLabel, fitsInline, stacksAside } from "./filter-bar";

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

describe("stacksAside (#88)", () => {
  const room = (title: number, aside: number) => title + aside + (2 * TITLE_GAP_REM + TRIGGER_REM) * root;
  it("keeps the aside beside the Filters button while the three fit, and gives it its own row one pixel less", () => {
    expect(stacksAside({ rowWidth: room(130, 150), titleWidth: 130, asideWidth: 150, rootPx: root, inline: false })).toBe(false);
    expect(stacksAside({ rowWidth: room(130, 150) - 1, titleWidth: 130, asideWidth: 150, rootPx: root, inline: false })).toBe(true);
  });
  it("never stacks with the controls inline or without an aside; a phone stacks the Sessions chips", () => {
    expect(stacksAside({ rowWidth: 100, titleWidth: 130, asideWidth: 150, rootPx: root, inline: true })).toBe(false);
    expect(stacksAside({ rowWidth: 100, titleWidth: 130, asideWidth: 0, rootPx: root, inline: false })).toBe(false);
    expect(stacksAside({ rowWidth: 390 - 32, titleWidth: 120, asideWidth: 150, rootPx: root, inline: false })).toBe(true);
  });
});

describe("filtersLabel", () => {
  it("reads Filters, or Filters with the number in force", () => {
    expect(filtersLabel(0)).toBe("Filters");
    expect(filtersLabel(3)).toBe("Filters · 3");
  });
});

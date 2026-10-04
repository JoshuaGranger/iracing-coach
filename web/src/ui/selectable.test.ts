import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { segmentClass } from "./segmented";
import { SELECTED_MARKERS, selectableClass } from "./selectable";

describe("selection has one state", () => {
  it("marks the selected item `on` and nothing else; the class never depends on hover", () => {
    expect(selectableClass(true, "tab")).toBe("on tab");
    expect(selectableClass(false, "tab")).toBe("tab");
    expect(selectableClass(false)).toBeUndefined();
    expect(selectableClass(true)).toBe("on");
    expect(selectableClass(false, null, false, undefined)).toBeUndefined();
  });

  it("gives a segment `on` exactly when it is the value, plus its band", () => {
    expect(segmentClass(true)).toBe("on");
    expect(segmentClass(false)).toBeUndefined();
    expect(segmentClass(true, "r")).toBe("on seg-r");
    expect(segmentClass(false, "q")).toBe("seg-q");
  });

  it("is honoured by the global hover rules: they skip every selected marker", () => {
    const css = readFileSync(new URL("../styles.css", import.meta.url), "utf8");
    const rule = (selector: string) => {
      const at = css.indexOf(selector);
      expect(at, selector).toBeGreaterThanOrEqual(0);
      return css.slice(at, css.indexOf("{", at));
    };
    const hover = rule("button:hover:not(:disabled)");
    for (const marker of [".on", ".selected", '[aria-pressed="true"]', '[aria-selected="true"]', "[aria-current]"]) expect(hover, marker).toContain(marker);
    expect(SELECTED_MARKERS).toContain(".on");
    const seg = rule(".seg button:hover");
    for (const marker of [".on", '[aria-pressed="true"]', '[aria-selected="true"]']) expect(seg, marker).toContain(marker);
    // A selected option of a multi-select keeps its look under the pointer: the hover fill is for unselected options.
    expect(css).toContain('.ms-opt.hl:not([aria-selected="true"])');
  });
});

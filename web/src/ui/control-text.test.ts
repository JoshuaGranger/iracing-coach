import { h, type VNode } from "preact";
import { describe, expect, it } from "vitest";
import { installControlText, isControl, wrapControlText } from "./control-text";

const span = (node: unknown): VNode<{ class?: string; children?: unknown }> => node as VNode<{ class?: string; children?: unknown }>;

describe("which elements are controls", () => {
  it("takes buttons, summaries, tab and button roles, and the control class tokens", () => {
    expect(isControl("button", {})).toBe(true);
    expect(isControl("summary", {})).toBe(true);
    expect(isControl("div", { role: "tab" })).toBe(true);
    expect(isControl("a", { class: "btn" })).toBe(true);
    expect(isControl("span", { class: "ri-count-chip" })).toBe(true);
    expect(isControl("span", { class: "badge badge-fixed" })).toBe(true);
    expect(isControl("span", { class: "s-car-toggle" })).toBe(true);
    expect(isControl("span", { class: "ri-sl-tab" })).toBe(true);
  });

  it("leaves the text carriers of a control, containers and unrelated names alone", () => {
    expect(isControl("span", { class: "ms-pill-text" })).toBe(false);
    expect(isControl("span", { class: "ri-btn-text" })).toBe(false);
    expect(isControl("span", { class: "ri-pill-label" })).toBe(false);
    expect(isControl("div", { class: "chips" })).toBe(false);
    expect(isControl("table", { class: "setups-table" })).toBe(false);
    expect(isControl("span", { class: "stripe" })).toBe(false);
    expect(isControl(() => null, { class: "btn" })).toBe(false);
  });
});

describe("wrapping a control's bare text", () => {
  it("puts a lone string or number in one ctl-t span", () => {
    expect(span(wrapControlText("Race")).type).toBe("span");
    expect(span(wrapControlText("Race")).props.class).toBe("ctl-t");
    expect(span(wrapControlText("Race")).props.children).toBe("Race");
    expect(span(wrapControlText(12)).props.class).toBe("ctl-t");
  });

  it("keeps adjacent text pieces together: x{n} is one run, not three flex items", () => {
    const icon = h("svg", null);
    const out = wrapControlText([icon, "x", 3, " ", "laps"]) as unknown[];
    expect(out).toHaveLength(2);
    expect(out[0]).toBe(icon);
    expect(span(out[1]).props.class).toBe("ctl-t");
    expect(span(out[1]).props.children).toEqual(["x", 3, " ", "laps"]);
  });

  it("wraps each text run between elements separately and keeps the elements in order", () => {
    const a = h("i", null);
    const b = h("b", null);
    const out = wrapControlText(["Left", a, "Middle", b, "Right"]) as unknown[];
    expect(out).toHaveLength(5);
    expect(out.map((child) => (typeof child === "object" ? span(child).type : "?"))).toEqual(["span", "i", "span", "b", "span"]);
  });

  it("does not wrap whitespace on its own and leaves element-only children as they were", () => {
    const only = [h("svg", null), h("span", null, "label")];
    expect(wrapControlText(only)).toBe(only);
    expect(wrapControlText(" ")).toBe(" ");
    const spaced = wrapControlText([h("i", null), " ", h("b", null)]) as unknown[];
    expect(spaced[1]).toBe(" ");
  });

  it("leaves nothing, a component and a nested list as they are", () => {
    expect(wrapControlText(null)).toBeNull();
    const nested = [["a", "b"], h("i", null)];
    expect(wrapControlText(nested)).toBe(nested);
  });
});

describe("the vnode hook", () => {
  it("wraps the text of a created button, and not that of a plain div", () => {
    installControlText();
    installControlText();
    const button = h("button", null, "Race");
    expect(span(button.props.children).props.class).toBe("ctl-t");
    const div = h("div", null, "Race");
    expect(div.props.children).toBe("Race");
    const html = h("button", { dangerouslySetInnerHTML: { __html: "<b>x</b>" } });
    expect(html.props.children).toBeUndefined();
  });
});

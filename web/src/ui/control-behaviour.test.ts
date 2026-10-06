import { describe, expect, it } from "vitest";
import { blurAfterPointer, inputModeForKey, POINTER_BLUR_SELECTOR } from "./control-behaviour";

interface FakeOptions {
  tag?: string;
  matches?: boolean;
  tabindex?: string | null;
  role?: string | null;
  scrolls?: boolean;
  inDialog?: boolean;
  body?: boolean;
}

/** A stand-in for the few Element members `blurAfterPointer` reads. */
function fake(options: FakeOptions = {}): Element {
  const body = {} as Element;
  const element = {
    tagName: (options.tag ?? "BUTTON").toUpperCase(),
    matches: () => options.matches ?? true,
    getAttribute: (name: string) => (name === "tabindex" ? options.tabindex ?? null : name === "role" ? options.role ?? null : null),
    scrollHeight: options.scrolls ? 500 : 100,
    clientHeight: 100,
    closest: () => (options.inDialog ? ({} as Element) : null),
    ownerDocument: { body: options.body ? (undefined as unknown as Element) : body },
  } as unknown as Element;
  if (options.body) (element as unknown as { ownerDocument: { body: Element } }).ownerDocument.body = element;
  return element;
}

describe("which key counts as using the keyboard", () => {
  it("is any key but a lone modifier", () => {
    expect(inputModeForKey("Tab")).toBe("keyboard");
    expect(inputModeForKey("ArrowDown")).toBe("keyboard");
    expect(inputModeForKey("Enter")).toBe("keyboard");
    expect(inputModeForKey("Shift")).toBeNull();
    expect(inputModeForKey("Control")).toBeNull();
  });
});

describe("what a pointer click lets go of", () => {
  it("blurs the control that holds focus", () => {
    const button = fake();
    expect(blurAfterPointer(button)).toBe(button);
  });

  it("does nothing when nothing, the page body or a text field holds focus", () => {
    expect(blurAfterPointer(null)).toBeNull();
    expect(blurAfterPointer(fake({ body: true }))).toBeNull();
    expect(blurAfterPointer(fake({ tag: "input", matches: false }))).toBeNull();
  });

  it("leaves a native select, a programmatic focus target and anything in an open dialog", () => {
    expect(blurAfterPointer(fake({ tag: "select", matches: true }))).toBeNull();
    expect(blurAfterPointer(fake({ tabindex: "-1" }))).toBeNull();
    expect(blurAfterPointer(fake({ inDialog: true }))).toBeNull();
  });

  it("leaves a keyboard-driven widget and a scrolling region, and lets go of a card", () => {
    expect(blurAfterPointer(fake({ tag: "div", tabindex: "0", role: "application" }))).toBeNull();
    expect(blurAfterPointer(fake({ tag: "div", tabindex: "0", role: "group", scrolls: true }))).toBeNull();
    const card = fake({ tag: "article", tabindex: "0", role: "listitem" });
    expect(blurAfterPointer(card)).toBe(card);
  });

  it("leaves a chart mark: its focus is what shows its readout on a tap", () => {
    // The selector does not name a bare tabindex, so a focusable mark is not matched.
    expect(POINTER_BLUR_SELECTOR.split(", ")).not.toContain('[tabindex="0"]');
    expect(blurAfterPointer(fake({ tag: "circle", tabindex: "0", matches: false }))).toBeNull();
  });
});

import { describe, expect, it } from "vitest";
import { LOOKS, barInitiallyOpen, initialLook, isLookId, stepLook } from "./looks";

describe("looks: which look a page opens in", () => {
  it("takes ?look= first, then the stored choice, else the shipped look", () => {
    expect(initialLook("?look=glass", "hud")).toBe("glass");
    expect(initialLook("", "hud")).toBe("hud");
    expect(initialLook("?look=nope", "programme")).toBe("programme");
    expect(initialLook("", "nope")).toBe("current");
    expect(initialLook("", null)).toBe("current");
  });

  it("knows only the listed looks", () => {
    expect(LOOKS.map((look) => look.id)).toEqual(["current", "pitwall", "console", "glass", "programme", "hud"]);
    expect(isLookId("console")).toBe(true);
    expect(isLookId("CONSOLE")).toBe(false);
    expect(isLookId(undefined)).toBe(false);
  });

  it("steps through the list both ways, wrapping at the ends", () => {
    expect(stepLook("current", 1)).toBe("pitwall");
    expect(stepLook("hud", 1)).toBe("current");
    expect(stepLook("current", -1)).toBe("hud");
  });
});

describe("looks: the chooser strip leaves the shipped layout alone until asked for", () => {
  it("stays hidden on the shipped look with nothing chosen", () => {
    expect(barInitiallyOpen("", "current", false)).toBe(false);
    expect(barInitiallyOpen("", "current", null)).toBe(false);
  });

  it("shows for another look, after a choice here, or with ?look= in the URL", () => {
    expect(barInitiallyOpen("", "glass", false)).toBe(true);
    expect(barInitiallyOpen("", "current", true)).toBe(true);
    expect(barInitiallyOpen("?look=current", "current", false)).toBe(true);
  });
});

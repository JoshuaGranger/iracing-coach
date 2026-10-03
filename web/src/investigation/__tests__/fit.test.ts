import { describe, expect, it } from "vitest";
import { MIN_FIT_HEIGHT, fitHeight } from "../ui/fit";

describe("fitHeight", () => {
  it("ends the workspace at the bottom of the window", () => {
    // 1280x720 in the shell: 48 px top bar, 8 px main padding above and below.
    expect(fitHeight(720, 56, 8)).toBe(656);
    expect(fitHeight(768, 56, 8)).toBe(704);
  });

  it("follows a banner above it", () => {
    expect(fitHeight(720, 56 + 34, 8)).toBe(622);
  });

  it("never drops below the minimum and rounds down to whole pixels", () => {
    expect(fitHeight(400, 56, 8)).toBe(MIN_FIT_HEIGHT);
    expect(fitHeight(720.7, 56.2, 8)).toBe(656);
  });
});

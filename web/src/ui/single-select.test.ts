import { describe, expect, it } from "vitest";
import { type SelectOption, selectedOption, stepIndex } from "./single-select";

const options: SelectOption[] = [
  { key: "", label: "None" },
  { key: "12", label: "L12 · 1:23.456", hint: "fastest" },
  { key: "20", label: "L20 · 1:24.001" },
];

describe("the single-choice dropdown's model", () => {
  it("steps through the list and stops at its ends", () => {
    expect(stepIndex(0, 1, 3)).toBe(1);
    expect(stepIndex(2, 1, 3)).toBe(2);
    expect(stepIndex(0, -1, 3)).toBe(0);
    expect(stepIndex(1, -1, 3)).toBe(0);
    expect(stepIndex(0, 1, 0)).toBe(-1);
  });

  it("reads the chosen option, and the first (the empty choice) for an unknown key", () => {
    expect(selectedOption(options, "12")?.label).toBe("L12 · 1:23.456");
    expect(selectedOption(options, "")?.label).toBe("None");
    expect(selectedOption(options, "99")?.label).toBe("None");
    expect(selectedOption([], "1")).toBeUndefined();
  });
});

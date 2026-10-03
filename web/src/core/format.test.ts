import { describe, expect, it } from "vitest";
import { formatLapTime, formatSigned, humanize, sessionTypeLabel, trackLabel } from "./format";

describe("format", () => {
  it("formats lap times", () => {
    expect(formatLapTime(15.234)).toBe("15.234");
    expect(formatLapTime(92.5)).toBe("1:32.500");
    expect(formatLapTime(60)).toBe("1:00.000");
    expect(formatLapTime(null)).toBe("-");
    expect(formatLapTime(0)).toBe("-");
  });
  it("signs deltas and never invents zero", () => {
    expect(formatSigned(0.1234)).toBe("+0.123");
    expect(formatSigned(-0.5, 1, "s")).toBe("-0.5 s");
    expect(formatSigned(undefined)).toBe("-");
  });
  it("labels", () => {
    expect(humanize("worker_crash")).toBe("Worker crash");
    expect(sessionTypeLabel("Lone Qualify")).toBe("Qualifying");
    expect(sessionTypeLabel("Offline Testing")).toBe("Test");
    expect(trackLabel("Bristol", "Oval")).toBe("Bristol - Oval");
    expect(trackLabel(null, null)).toBe("Track unknown");
  });
});

import { describe, expect, it } from "vitest";
import { eventPhases, formatLapTime, formatSigned, humanize, sessionTypeLabel, trackLabel } from "./format";

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

describe("eventPhases", () => {
  it("adds the open session to the other phases in running order", () => {
    const others = [
      { session_id: 2, session_type: "Practice" },
      { session_id: 3, session_type: "Lone Qualify" },
    ];
    expect(eventPhases({ session_id: 1, session_type: "Race" }, others).map((p) => p.session_id)).toEqual([2, 3, 1]);
    expect(eventPhases({ session_id: 3, session_type: "Lone Qualify" }, [others[0]!, { session_id: 1, session_type: "Race" }]).map((p) => p.session_id)).toEqual([2, 3, 1]);
  });

  it("lists a lone session once and does not repeat the open one", () => {
    const only = { session_id: 5, session_type: null };
    expect(eventPhases(only, [])).toEqual([only]);
    expect(eventPhases(only, [only, { session_id: 6, session_type: "Race" }]).map((p) => p.session_id)).toEqual([6, 5]);
  });
});

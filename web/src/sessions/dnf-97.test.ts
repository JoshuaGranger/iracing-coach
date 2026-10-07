// Backlog #97, Joshua: "On a race where I wreck out and leave, it doesn't have the rest of the race and where I really finished. I
// did not finish 6th at the 9:09 race. I got wrecked out." The overviews below are session 670's (2026-10-06 21:09 Charlotte, the
// recording stopped in the stall on lap 14, running P6, started P7) and the 10/02 Vegas win's.
import { describe, expect, it } from "vitest";
import { type Session, cellSlots, isWin, phaseSlots, positionChange, resultBand, resultText, retirement } from "./model";

function race(overview: Record<string, unknown>, sessionType = "Race"): Session {
  return {
    session_id: 670,
    customer_id: 1,
    group_key: "subsession:89181419:2",
    event_key: "subsession:89181419",
    session_type: sessionType,
    track: "Charlotte Motor Speedway",
    track_config: "Oval",
    car_path: "stockcars2 supra2019",
    car_name: "Toyota Supra Class B",
    recording_count: 2,
    analysis_state: "ready",
    start_time_utc: "2026-10-07T02:09:00Z",
    overview,
  } as Session;
}

const wreckedOut = race({ start_position: 7, final_position: 6, finished: false, retired_lap: 14, retired_position: 6 });

describe("a race left before the flag (#97)", () => {
  it("reads DNF and the lap in the Finish slot, with no change arrow and the running place in the hover", () => {
    const slot = phaseSlots(wreckedOut, null)[0]!;
    expect(slot.key).toBe("pos");
    expect(slot.label).toBe("Finish");
    expect(slot.parts.map((part) => part.text)).toEqual(["DNF · L14"]);
    expect(slot.parts.some((part) => part.tone === "gain" || part.tone === "loss")).toBe(false);
    expect(slot.title).toBe("Running P6 when the recording ended, started P7");
    expect(slot.parts[0]!.title, "the slot carries the hover; the text stays the accessible name").toBeUndefined();
    expect(cellSlots(wreckedOut, null)[0]).toEqual(slot);
  });

  it("is not a finish: no position change, no result band, never a win", () => {
    expect(retirement(wreckedOut)).toEqual({ lap: 14, position: 6 });
    expect(positionChange(wreckedOut)).toBeNull();
    expect(resultBand(wreckedOut)).toBeNull();
    expect(resultText(wreckedOut)).toBe("P7 → DNF · L14");
    const leading = race({ start_position: 2, final_position: 1, finished: false, retired_lap: 30, retired_position: 1 });
    expect(isWin(leading)).toBe(false);
    expect(resultBand(leading)).toBeNull();
  });

  it("without the lap or the place it still reads DNF", () => {
    const slot = phaseSlots(race({ final_position: 9, finished: false, retired_lap: null, retired_position: null }), null)[0]!;
    expect(slot.parts.map((part) => part.text)).toEqual(["DNF"]);
    expect(slot.title).toBe("The recording ended before the race did");
  });

  it("a finished race, an unknown one and an analysis from before the field read as before", () => {
    for (const finished of [true, null, undefined]) {
      const s = race({ start_position: 2, final_position: 1, finished, retired_lap: null, retired_position: null });
      expect(retirement(s)).toBeNull();
      expect(phaseSlots(s, null)[0]!.parts.map((part) => part.text)).toEqual(["P1", "▲1"]);
      expect(isWin(s)).toBe(true);
    }
  });

  it("official results from a later recording of the event stand as before", () => {
    const s = race({ start_position: 7, final_position: 6, results_position: 22, field_size: 30, finished: false, retired_lap: 14, retired_position: 6 });
    expect(retirement(s)).toBeNull();
    expect(phaseSlots(s, null)[0]!.parts[0]!.text).toBe("P6");
  });

  it("practice and qualifying never read DNF", () => {
    const q = race({ final_position: 3, finished: false, retired_lap: 4, retired_position: 3 }, "Lone Qualify");
    expect(retirement(q)).toBeNull();
  });
});

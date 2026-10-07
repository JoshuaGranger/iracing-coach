// Backlog #96, Joshua: "I started in 2nd but the position chart doesn't show that. I got bumped down to like 18 on the first lap so it
// shows me starting in the back. Lap 0 should show my starting position." And #97: a race left before the flag reads DNF.
// The laps are session 674's opening (2026-10-06 22:09 Charlotte: started P2, P22 after lap 1) and session 670's end.
import { describe, expect, it } from "vitest";
import type { InvestigationLap } from "../investigation-model";
import { positionSegments, positionTrack, raceResult, retiredText, retiredTitle } from "../ui/RaceView";

const lap = (number: number, position: number | null): InvestigationLap => ({ lap: number, lapTimeS: 33, position, flagState: "green", eligible: true, exclusionReasons: [], runNumber: 1, plotPace: true, greenExposure: number });

describe("the position line starts at the start (#96)", () => {
  const opening = [lap(1, 22), lap(2, 22), lap(3, 21), lap(4, 20)];

  it("draws lap 0 at the start, joined to lap 1's recorded place", () => {
    const track = positionTrack(opening, 2);
    expect(track.map((point) => [point.lap, point.position, point.start])).toEqual([[0, 2, true], [1, 22, false], [2, 22, false], [3, 21, false], [4, 20, false]]);
    const segments = positionSegments(track);
    expect(segments[0]).toMatchObject({ from: { lap: 0, position: 2 }, to: { lap: 1, position: 22 }, tone: "loss" });
    expect(segments.map((segment) => segment.tone)).toEqual(["loss", "hold", "gain", "gain"]);
  });

  it("a recorded lap 0 gives way to the start", () => {
    expect(positionTrack([lap(0, 17), ...opening], 2).filter((point) => point.lap === 0)).toEqual([{ lap: 0, position: 2, start: true }]);
  });

  it("without a start the recorded laps alone, as before", () => {
    expect(positionTrack(opening, null).map((point) => point.lap)).toEqual([1, 2, 3, 4]);
    expect(positionTrack([lap(1, null), lap(2, 5)], null).map((point) => point.lap)).toEqual([2]);
  });
});

describe("the race result the chart reads (#96, #97)", () => {
  const view = (summary: Record<string, unknown>) => ({ race_summary: summary }) as never;

  it("prefers the session's start and retirement (the Sessions row's rule)", () => {
    const given = { start: 2, retired: null };
    expect(raceResult(view({ starting_position: 5 }), given)).toEqual({ start: 2, retired: null });
    expect(raceResult(view({ starting_position: 5, finish: { finished: true } }), null), "the shell says: not a race").toBeNull();
  });

  it("else reads the race view: the backend's start and its finish", () => {
    const left = view({ starting_position: 7, finish: { status: "recording_ended", finished: false, last_lap: 14, position_at_end: 6 } });
    expect(raceResult(left)).toEqual({ start: 7, retired: { lap: 14, position: 6 } });
    const flagged = view({ starting_position: 2, finish: { status: "checkered", finished: true, last_lap: 47, position_at_end: 1 } });
    expect(raceResult(flagged)).toEqual({ start: 2, retired: null });
  });

  it("practice, qualifying and an analysis from before the finish field have no start on the line", () => {
    // The backend writes `finish` for a Race alone (null otherwise); its `starting_position` exists for every phase.
    expect(raceResult(view({ starting_position: 4, finish: null }))).toBeNull();
    expect(raceResult(view({ starting_position: 4 }))).toBeNull();
    expect(raceResult(undefined)).toBeNull();
  });

  it("names the retirement as the Sessions row does", () => {
    expect(retiredText({ lap: 14, position: 6 })).toBe("DNF · L14");
    expect(retiredTitle({ lap: 14, position: 6 })).toBe("Running P6 when the recording ended");
    expect(retiredText({ lap: null, position: null })).toBe("DNF");
  });
});

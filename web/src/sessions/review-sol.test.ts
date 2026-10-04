import { describe, expect, it } from "vitest";
import { createState } from "../../mock/data.mjs";
import { emptySelection, filterSessions, fromSessionFilters, groupEvents, toSessionFilters, type Session } from "./model";

/** Review findings 12 and 13 (docs/08-rewrite/reviews/web-round1-review-codex-sol.md) on the unmodified mock library. */
const library = (): Session[] => (createState() as { sessions: Record<number, Session[]> }).sessions[1507810]!;

describe("finding 12: both setup types chosen shows the same sessions before and after a reload", () => {
  it("treats Fixed and Open together as no setup filter, as session_filters stores it", () => {
    const sessions = library();
    const both = { ...emptySelection(), setup: ["Fixed", "Open"] };
    const restored = fromSessionFilters(toSessionFilters(both))!;
    expect(toSessionFilters(both).setup).toBeNull();
    expect(filterSessions(sessions, both).length).toBe(filterSessions(sessions, restored).length);
    expect(filterSessions(sessions, both).length).toBe(sessions.length);
    // One type alone still filters, and a session without a recorded type never matches it.
    const fixed = filterSessions(sessions, { ...emptySelection(), setup: ["Fixed"] });
    expect(fixed.length).toBeGreaterThan(0);
    expect(fixed.every((s) => s.is_fixed_setup === true)).toBe(true);
  });
});

describe("finding 13: the event best comes from the whole event, whatever the filter shows", () => {
  it("keeps qualifying's 15.310 as the best of the event holding race 110 when only the race is shown", () => {
    const sessions = library();
    const raceOnly = groupEvents(sessions, { ...emptySelection(), type: ["R"] });
    const event = raceOnly.find((ev) => ev.sessions.some((s) => s.session_id === 110))!;
    expect(event.sessions.map((s) => s.session_id)).toEqual([110]);
    expect(event.bestLapS).toBe(15.31);
    expect(event.sessions[0]!.overview.best_clean_lap_s).toBe(15.534);
    // Events without a matching phase are dropped; no filter keeps every event and phase.
    expect(raceOnly.every((ev) => ev.sessions.length > 0)).toBe(true);
    expect(groupEvents(sessions).flatMap((ev) => ev.sessions).length).toBe(sessions.length);
    expect(groupEvents(sessions, emptySelection())).toEqual(groupEvents(sessions));
  });
});

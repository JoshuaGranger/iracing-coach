import { beforeEach, describe, expect, it } from "vitest";
import { resetCarOrder } from "../core/identity";
import { localDay, localDayTime, localTime } from "./dates";
import {
  type Session,
  analysisException,
  carColorIndex,
  eventPhaseOptions,
  facetOptions,
  filterSessions,
  formatPace,
  fromSessionFilters,
  groupEvents,
  emptySelection,
  paceTone,
  parseSelection,
  phaseOf,
  toSessionFilters,
  positionChange,
  resultBand,
  resultText,
  seriesLabel,
  setupTypeOf,
  toggleOption,
  hasFigures,
  colouredFigures,
  phaseColumns,
  phaseLayout,
  phaseSlots,
} from "./model";

beforeEach(() => resetCarOrder());

let next = 1;
function session(over: Partial<Session> & Record<string, unknown> = {}): Session {
  const id = next++;
  return {
    session_id: id,
    customer_id: 1,
    group_key: `g${id}`,
    event_key: "subsession:1",
    session_type: "Race",
    track: "Bristol Motor Speedway",
    track_config: "Oval",
    car_path: "nascar-xfinity",
    car_name: "NASCAR Xfinity Toyota Supra",
    series_name: "NASCAR Xfinity Series",
    recording_count: 1,
    analysis_state: "ready",
    overview: {},
    start_time_utc: "2026-09-28T18:00:00Z",
    ...over,
  } as Session;
}

describe("phases and events", () => {
  it("names phases and orders them in running order", () => {
    expect(phaseOf("Lone Qualify")).toMatchObject({ code: "Q", label: "Qualifying" });
    expect(phaseOf("Offline Testing").code).toBe("T");
    expect(phaseOf("Warmup").code).toBe("W");
    const race = session({ session_type: "Race", sim_session_num: 2 });
    const prac = session({ session_type: "Practice", sim_session_num: 0 });
    const qual = session({ session_type: "Qualify", sim_session_num: 1 });
    const [ev] = groupEvents([race, qual, prac]);
    expect(ev!.sessions.map((s) => phaseOf(s.session_type).code)).toEqual(["P", "Q", "R"]);
    expect(ev!.head).toBe(race);
  });

  it("keeps events in the server's order and takes the earliest start and the fastest best lap of one event", () => {
    const a1 = session({ event_key: "a", session_type: "Practice", start_time_utc: "2026-09-28T18:00:00Z", overview: { best_clean_lap_s: 15.5 } });
    const a2 = session({ event_key: "a", session_type: "Race", start_time_utc: "2026-09-28T19:00:00Z", overview: { best_clean_lap_s: 15.2 } });
    const b = session({ event_key: "b", overview: { best_clean_lap_s: 14 } });
    const events = groupEvents([b, a1, a2]);
    expect(events.map((e) => e.key)).toEqual(["b", "a"]);
    expect(events[1]).toMatchObject({ start: "2026-09-28T18:00:00Z", bestLapS: 15.2 });
  });

  it("gives each car a colour by first sighting, oldest first, so a new car never recolours an old one", () => {
    const old = session({ car_path: "mx5", start_time_utc: "2026-01-01T00:00:00Z" });
    const mid = session({ car_path: "supra", start_time_utc: "2026-05-01T00:00:00Z" });
    const fresh = session({ car_path: "gr86", start_time_utc: "2026-09-01T00:00:00Z" });
    const before = carColorIndex([mid, old]);
    const after = carColorIndex([fresh, mid, old]);
    expect(before.get("mx5")).toBe(0);
    expect(before.get("supra")).toBe(1);
    expect(after.get("mx5")).toBe(0);
    expect(after.get("supra")).toBe(1);
    expect(after.get("gr86")).toBe(2);
  });

  it("omits a series that only repeats the car name", () => {
    expect(seriesLabel(session({ car_name: "Global Mazda MX-5 Cup", series_name: "Global Mazda MX-5 Cup" }))).toBeNull();
    expect(seriesLabel(session({ car_name: "Global Mazda MX-5 Cup", series_name: "Global Mazda MX-5 Cup Series" }))).toBeNull();
    expect(seriesLabel(session({ series_name: "NASCAR Xfinity Series" }))).toBe("NASCAR Xfinity Series");
    expect(seriesLabel(session({ series_name: null }))).toBeNull();
  });
});

describe("fixed and open", () => {
  it("reads is_fixed_setup: true fixed, false open, null or absent unknown (never open)", () => {
    expect(setupTypeOf(session({ is_fixed_setup: true }))).toBe("Fixed");
    expect(setupTypeOf(session({ is_fixed_setup: false }))).toBe("Open");
    expect(setupTypeOf(session({ is_fixed_setup: null, setup_type: "Open" }))).toBeNull();
  });
  it("falls back to a setup_type string only when is_fixed_setup is absent", () => {
    expect(setupTypeOf(session({ setup_type: "Fixed" }))).toBe("Fixed");
    expect(setupTypeOf(session({ setup_type: "open" }))).toBe("Open");
    expect(setupTypeOf(session({ setup_type: "Unknown" }))).toBeNull();
    expect(setupTypeOf(session())).toBeNull();
  });
  it("takes the event's type from its race, else from any phase that has one", () => {
    const prac = session({ event_key: "x", session_type: "Practice", setup_type: "Open" });
    const race = session({ event_key: "x", session_type: "Race", setup_type: "Fixed" });
    expect(groupEvents([prac, race])[0]!.setupType).toBe("Fixed");
    expect(groupEvents([prac])[0]!.setupType).toBe("Open");
    expect(groupEvents([session({ event_key: "y" })])[0]!.setupType).toBeNull();
  });
});

describe("filters", () => {
  const supraRace = session({ car_path: "supra", car_name: "Supra", track: "Bristol", track_config: "Oval", series_name: "Xfinity", session_type: "Race", setup_type: "Fixed" });
  const supraPrac = session({ car_path: "supra", car_name: "Supra", track: "Bristol", track_config: "Oval", series_name: "Xfinity", session_type: "Practice", setup_type: "Fixed" });
  const mx5Qual = session({ car_path: "mx5", car_name: "MX-5", track: "Lime Rock Park", track_config: "Grand Prix", series_name: "MX-5 Cup", session_type: "Lone Qualify", setup_type: "Open" });
  const test = session({ car_path: "supra", car_name: "Supra", track: "Bristol", track_config: "Oval", series_name: null, session_type: "Offline Testing" });
  const all = [supraRace, supraPrac, mx5Qual, test];
  const colors = carColorIndex(all);

  it("matches any chosen value within a filter and every filter together", () => {
    const sel = toggleOption(toggleOption(emptySelection(), "car", "supra"), "car", "mx5");
    expect(filterSessions(all, sel)).toHaveLength(4);
    const both = toggleOption(sel, "type", "R");
    expect(filterSessions(all, both)).toEqual([supraRace]);
    expect(filterSessions(all, toggleOption(emptySelection(), "setup", "Open"))).toEqual([mx5Qual]);
    expect(filterSessions(all, emptySelection())).toHaveLength(4);
  });

  it("offers only practice, qualifying and race as session types; a test is never an option", () => {
    const types = facetOptions("type", all, colors);
    expect(types.map((o) => o.label)).toEqual(["Practice", "Qualifying", "Race"]);
    expect(filterSessions(all, toggleOption(emptySelection(), "type", "P"))).toEqual([supraPrac]);
  });

  it("counts sessions per option and sorts by name", () => {
    const cars = facetOptions("car", all, colors);
    expect(cars.map((o) => [o.label, o.count])).toEqual([
      ["MX-5", 1],
      ["Supra", 3],
    ]);
    expect(facetOptions("track", all, colors).map((o) => o.label)).toEqual(["Bristol", "Lime Rock Park"]);
    expect(facetOptions("series", all, colors).map((o) => o.label)).toEqual(["MX-5 Cup", "Xfinity"]);
    expect(facetOptions("setup", all, colors).map((o) => o.key)).toEqual(["Fixed", "Open"]);
  });

  it("offers no setup option while no session carries its setup type", () => {
    expect(facetOptions("setup", [session(), session()], new Map())).toEqual([]);
  });

  it("restores a stored selection and drops what is malformed", () => {
    expect(parseSelection({ car: ["a", "a", 3, ""], track: "x", type: ["R"] })).toEqual({ car: ["a"], track: [], series: [], type: ["R"], setup: [] });
    expect(parseSelection(null)).toBeNull();
    expect(parseSelection({ nonsense: [1] })).toBeNull();
  });

  it("stores the selection as session_filters with the sessions query values and reads it back", () => {
    const sel = { car: ["supra"], track: ["Bristol"], series: ["Xfinity"], type: ["Q", "R"], setup: ["Fixed"] };
    const stored = toSessionFilters(sel);
    expect(stored).toEqual({ cars: ["supra"], tracks: ["Bristol"], series: ["Xfinity"], types: ["qualify", "race"], setup: "fixed" });
    expect(fromSessionFilters(stored)).toEqual(sel);
    expect(toSessionFilters({ ...sel, setup: ["Fixed", "Open"] }).setup).toBeNull();
    expect(fromSessionFilters({ cars: "x", types: ["race", "bogus"] })).toEqual({ car: [], track: [], series: [], type: ["R"], setup: [] });
    expect(fromSessionFilters(null)).toBeNull();
  });
});

describe("figures", () => {
  it("colours pace by its trend: above +0.020 a fall-off, below -0.005 an improvement", () => {
    expect(paceTone(0.021)).toBe("loss");
    expect(paceTone(0.02)).toBe("flat");
    expect(paceTone(-0.006)).toBe("gain");
    expect(paceTone(-0.005)).toBe("flat");
    expect(paceTone(null)).toBeNull();
    expect(formatPace(0.018)).toBe("+0.018");
    expect(formatPace(-0.041)).toBe("−0.041");
    expect(formatPace(0.0004)).toBe("0.000");
    expect(formatPace(undefined)).toBe("—");
  });

  it("shows a place change for a race only, with a win and a result band", () => {
    const gain = session({ overview: { starting_position: 12, final_position: 4 } });
    expect(positionChange(gain)).toEqual({ from: 12, to: 4, gained: 8 });
    expect(resultBand(gain)).toBe("gain");
    expect(resultText(gain)).toBe("P12 → P4");
    const loss = session({ overview: { starting_position: 7, final_position: 9 } });
    expect(positionChange(loss)!.gained).toBe(-2);
    expect(resultBand(loss)).toBe("loss");
    expect(resultBand(session({ overview: { starting_position: 2, final_position: 1 } }))).toBe("win");
    expect(resultBand(session({ overview: { starting_position: 5, final_position: 5 } }))).toBeNull();
    const qual = session({ session_type: "Qualify", overview: { starting_position: 7, final_position: 2 } });
    expect(positionChange(qual)).toBeNull();
    expect(resultBand(qual)).toBeNull();
    expect(resultText(session({ overview: { final_position: 4 } }))).toBe("P4");
  });

  it("tells a measured row from one that has only discovery", () => {
    expect(hasFigures(session({ overview: { recorded_laps: 38 } }))).toBe(false);
    expect(hasFigures(session({ overview: { recorded_laps: 38, best_clean_lap_s: 15.2 } }))).toBe(true);
  });
});

describe("analysis state shows exceptions only", () => {
  it("says nothing for a ready session and names every other state", () => {
    expect(analysisException(session())).toBeNull();
    expect(analysisException(session({ analysis_state: "running" }))).toEqual({ kind: "analyzing", text: "Analyzing", action: null });
    expect(analysisException(session({ analysis_state: "queued" }))).toMatchObject({ text: "Queued", action: null });
    expect(analysisException(session({ analysis_state: "failed", analysis_reason: "worker_crash" }))).toEqual({ kind: "failed", text: "Failed · worker crash", action: "Retry" });
    expect(analysisException(session({ analysis_state: "deferred", analysis_reason: "exceeds_memory_budget" }))).toEqual({ kind: "deferred", text: "Deferred · exceeds memory budget", action: null });
    expect(analysisException(session({ analysis_state: "none" }))).toEqual({ kind: "none", text: "Not analyzed", action: "Analyze" });
  });
});

describe("phases of an event (audit S2)", () => {
  it("lists the phases in running order with their real analysis state", () => {
    const p = session({ event_key: "e", session_type: "Practice", sim_session_num: 0 });
    const q = session({ event_key: "e", session_type: "Qualify", sim_session_num: 1, analysis_state: "failed", analysis_reason: "worker_crash" });
    const r = session({ event_key: "e", session_type: "Race", sim_session_num: 2, analysis_state: "deferred", analysis_reason: "exceeds_memory_budget" });
    const other = session({ event_key: "other" });
    const phases = eventPhaseOptions(p.session_id, [r, other, q, p]);
    expect(phases.map((x) => x.sessionType)).toEqual(["Practice", "Qualify", "Race"]);
    expect(phases.map((x) => x.state)).toEqual(["ready", "failed", "deferred"]);
    expect(phases[1]!.reason).toBe("worker_crash");
    expect(phases[2]!.reason).toBe("exceeds_memory_budget");
    expect(phases[0]!.reason).toBeNull();
  });
  it("returns nothing for a session the library does not hold", () => {
    expect(eventPhaseOptions(999999, [session()])).toEqual([]);
  });
});

describe("local dates (audit S1)", () => {
  // 2026-09-29T00:30:00Z is Sep 28, 7:30 pm in Chicago.
  const iso = "2026-09-29T00:30:00Z";
  const now = new Date("2026-10-03T12:00:00Z");
  it("reads the clock on the driver's wall, not UTC", () => {
    expect(localDay(iso, now, "America/Chicago")).toBe("Mon Sep 28");
    expect(localDay(iso, now, "UTC")).toBe("Tue Sep 29");
    expect(localTime(iso, "America/Chicago")).toBe("7:30 pm");
    expect(localDayTime(iso, now, "America/Chicago")).toBe("Mon Sep 28, 7:30 pm");
  });
  it("adds the year once it is not the current one and shows nothing for a missing time", () => {
    expect(localDay("2025-09-27T18:00:00Z", now, "UTC")).toBe("Sat Sep 27 2025");
    expect(localDay(null, now)).toBeNull();
    expect(localTime("not a date")).toBeNull();
  });
});

describe("phase cell slots (web round 2)", () => {
  const race = session({
    session_type: "Race",
    overview: { starting_position: 12, final_position: 4, recorded_laps: 82, green_laps_estimated: 60, caution_laps_estimated: 20, pit_stops_detected: 3, longest_green_run: 41, pace_slope_s_per_lap: 0.021, best_clean_lap_s: 15.234, fuel_used_gal: 21.6 },
  });
  const text = (s: ReturnType<typeof phaseSlots>[number]) => s.parts.map((p) => p.text).join(" ");

  it("has seven slots in the plan's order with the figures printed as the cell shows them", () => {
    const slots = phaseSlots(race, 15.234);
    expect(slots.map((s) => s.key)).toEqual(["pos", "laps", "best", "pace", "stops", "fuel", "run"]);
    expect(slots.map(text)).toEqual(["P4 ▲8", "60 · 20", "15.234", "+0.021", "3", "21.6 gal", "41"]);
    expect(slots[0]!.label).toBe("Finish");
    expect(slots.every((s) => !s.missing)).toBe(true);
  });

  it("labels a practice position `Pos` and shows no change for it", () => {
    const practice = session({ session_type: "Practice", overview: { starting_position: 9, final_position: 5 } });
    const [pos] = phaseSlots(practice, null);
    expect(pos!.label).toBe("Pos");
    expect(text(pos!)).toBe("P5");
  });

  it("shows a dash in every slot the backend did not report, never a guess", () => {
    const slots = phaseSlots(session({ overview: { recorded_laps: 20 } }), null);
    expect(slots).toHaveLength(7);
    expect(slots.every((s) => s.missing && text(s) === "—")).toBe(true);
    // A zero is a value, not a gap.
    const zero = phaseSlots(session({ overview: { pit_stops_detected: 0, caution_laps_estimated: 0, green_laps_estimated: 0 } }), null);
    expect(text(zero.find((s) => s.key === "stops")!)).toBe("0");
    expect(zero.find((s) => s.key === "stops")!.missing).toBe(false);
    expect(text(zero.find((s) => s.key === "laps")!)).toBe("0 · 0");
  });

  it("spends the colour budget on a supported position change and a caution count above zero, and nothing else", () => {
    const tones = (s: ReturnType<typeof phaseSlots>) => s.flatMap((x) => x.parts).filter((p) => p.tone === "gain" || p.tone === "loss" || p.tone === "caution").map((p) => `${p.tone}:${p.text}`);
    expect(tones(phaseSlots(race, 15.234))).toEqual(["gain:▲8", "caution:20"]);
    const lost = session({ overview: { ...race.overview, starting_position: 7, final_position: 9, caution_laps_estimated: 0 } });
    expect(tones(phaseSlots(lost, null))).toEqual(["loss:▼2"]);
    // Unchanged position, no cautions: no colour at all. A win is a plain number.
    const win = session({ overview: { ...race.overview, starting_position: 1, final_position: 1, caution_laps_estimated: 0 } });
    expect(tones(phaseSlots(win, null))).toEqual([]);
    expect(text(phaseSlots(win, null)[0]!)).toBe("P1");
    expect(colouredFigures(phaseSlots(race, 15.234))).toBe(2);
    // Pace trend, best and fuel never carry a colour whatever their value.
    const hot = session({ overview: { ...race.overview, pace_slope_s_per_lap: 0.2, best_clean_lap_s: 14.9 } });
    expect(phaseSlots(hot, 14.9).filter((s) => ["best", "pace", "stops", "fuel", "run"].includes(s.key)).flatMap((s) => s.parts).every((p) => !p.tone || p.tone === "unit")).toBe(true);
  });

  it("marks the event's fastest lap without colour, and reads a green pace the server may add", () => {
    expect(phaseSlots(race, 15.234).find((s) => s.key === "best")!.eventBest).toBe(true);
    expect(phaseSlots(race, 15.0).find((s) => s.key === "best")!.eventBest).toBeUndefined();
    const withPace = session({ overview: { ...race.overview, green_pace_median_s: 76.204 } });
    expect(text(phaseSlots(withPace, null).find((s) => s.key === "pace")!)).toBe("1:16.204 +0.021");
  });
});

describe("phase columns", () => {
  const p = session({ session_type: "Practice" });
  const q = session({ session_type: "Qualify" });
  const r = session({ session_type: "Race" });
  it("keeps practice, qualifying and race in the same column in every event", () => {
    const layout = phaseLayout([p, q, r]);
    expect(layout).toEqual(["P", "Q", "R"]);
    expect(phaseColumns([p, r], layout)).toEqual({ columns: [0, 2], count: 3 });
    expect(phaseColumns([r], layout)).toEqual({ columns: [2], count: 3 });
  });
  it("leaves one column when a filter shows only races", () => {
    const layout = phaseLayout([r]);
    expect(layout).toEqual(["R"]);
    expect(phaseColumns([r], layout)).toEqual({ columns: [0], count: 1 });
  });
  it("puts a test in practice's column and follows running order when an event repeats a phase", () => {
    expect(phaseLayout([session({ session_type: "Offline Testing" }), r])).toEqual(["P", "R"]);
    const p2 = session({ session_type: "Practice" });
    expect(phaseColumns([p, p2, r], ["P", "R"])).toEqual({ columns: [0, 1, 2], count: 3 });
  });
});

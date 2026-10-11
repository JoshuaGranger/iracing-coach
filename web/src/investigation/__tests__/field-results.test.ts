import { describe, expect, it } from "vitest";
import { fieldResults, fieldTime, sampledTime, timeSourceLabel } from "../ui/field-results";
import { FieldRaceView } from "../ui/FieldRaceView";
import { ResultsSummaryView } from "../ui/ResultsSummaryView";

const raw = { cars: [
  { car_index: 4, driver_name: "Leader", car_number: "7", position: 1, is_player: false, pace_median_s: null, pace_lap_count: 2, tires_taken: null, fuel_used_l: null, pit_visits: 1,
    laps: [{ lap: 3, lap_time_s: 60.1, position: 1, flag_state: "green", pace_eligible: true }, { lap: 4, lap_time_s: 79.2, position: 2, flag_state: "caution", pace_eligible: false }], pit_events: [{ lap: 4, session_time_s: 242 }] },
  { car_index: 0, driver_name: "Player", position: 2, is_player: true, tires_taken: [], fuel_used_l: 0, pit_visits: 0 },
] };

describe("recorded field comparison", () => {
  it("retains backend order, missing opponent service, explicit zero values and eligibility", () => {
    const result = fieldResults(raw);
    expect(result.cars.map((car) => car.index)).toEqual([4, 0]);
    expect(result.cars[0]).toMatchObject({ tires: null, fuel: null, pace: null, paceCount: 2 });
    expect(result.cars[1]).toMatchObject({ tires: [], fuel: 0, pits: 0 });
    expect(result.cars[0]!.laps.map((lap) => lap.eligible)).toEqual([true, false]);
    expect(result.cars[0]!.pitEvents).toEqual([{ lap: 4, time: 242 }]);
  });
  it("does not invent field rows when optional results are missing or malformed", () => {
    expect(fieldResults(null).cars).toEqual([]);
    expect(fieldResults({ cars: [{ car_index: -1 }, {}, { car_index: 2.3 }] }).cars).toEqual([]);
  });
  it("renders observed lap dots and pit marks and no unsupported service figures", () => {
    const car = fieldResults(raw).cars[0]!;
    const view = FieldRaceView({ car });
    const json = JSON.stringify(view);
    expect(json).toContain('"data-field-lap":3');
    expect(json).toContain('"data-field-pit":4');
    expect(json).toContain("screened");
    expect(json).toContain("Tire service and fuel are unavailable");
    expect(json).not.toContain("NaN");
    expect(JSON.stringify(FieldRaceView({ car: { ...car, laps: [{ ...car.laps[0]!, time: null, position: null }] } }))).not.toContain("NaN");
  });
  it("labels order and unavailable values in the comparison table", () => {
    const json = JSON.stringify(ResultsSummaryView({ results: fieldResults(raw) }));
    expect(json).toContain("recorded order");
    expect(json).toContain("Recorded fuel");
    expect(json).toContain("None");
    expect(json).toContain("—");
  });
  it("formats time without turning absent data into zero", () => {
    expect(fieldTime(null)).toBe("—");
    expect(fieldTime(65.25)).toBe("65.250");
    expect(fieldTime(28.42)).toBe("28.420");
    expect(fieldTime(65.2511, true)).toBe("≈65.3");
    expect(sampledTime("interpolated_lap_crossings")).toBe(true);
    expect(sampledTime("recorded_interpolated_distance_crossings")).toBe(true);
    expect(timeSourceLabel("recorded_interpolated_distance_crossings")).toBe("Approximate sampled interval; not a scored lap time");
    expect(sampledTime("recorded_lap_counter_intervals")).toBe(true);
    expect(sampledTime("session_results_snapshot")).toBe(false);
    expect(timeSourceLabel("session_results_snapshot")).toBe("iRacing results snapshot");
  });
  it("preserves sampled timing evidence and explains omitted oversized progression", () => {
    const results = fieldResults({ cars: [{ ...raw.cars[0], fastest_source: "recorded_lap_counter_intervals", progression_unavailable_reason: "payload_budget", laps: [] }] });
    expect(results.cars[0]!.fastestSource).toBe("recorded_lap_counter_intervals");
    expect(JSON.stringify(ResultsSummaryView({ results }))).toContain("Approximate sampled interval");
    expect(JSON.stringify(FieldRaceView({ car: results.cars[0]! }))).toContain("oversized recording");
    const laps = fieldResults({ cars: [{ ...raw.cars[0], laps: [{ ...raw.cars[0]!.laps![0], lap_time_source: "recorded_interpolated_distance_crossings" }] }] });
    expect(laps.cars[0]!.laps[0]!.timeSource).toBe("recorded_interpolated_distance_crossings");
    expect(JSON.stringify(FieldRaceView({ car: laps.cars[0]! }))).toContain("≈60.1");
  });
});

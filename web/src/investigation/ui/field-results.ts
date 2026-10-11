import { asArray, asRecord, num } from "../types";

export interface FieldLap { lap: number; time: number | null; timeSource: string | null; position: number | null; flag: string; pit: boolean; eligible: boolean }
export interface FieldCar {
  index: number; name: string; number: string; player: boolean; position: number | null;
  completed: number | null; fastest: number | null; pace: number | null; paceCount: number | null;
  pits: number | null; tires: string[] | null; fuel: number | null;
  fastestSource: string | null; progressionUnavailableReason: string | null;
  laps: FieldLap[]; pitEvents: { lap: number | null; time: number | null }[];
}
export interface FieldResults { cars: FieldCar[]; definition: string; limitations: string[]; unavailableReason: string | null }
/** Optional projection fields retain backend order and evidence; no metrics are fitted in the browser. */
export function fieldResults(raw: unknown): FieldResults {
  const results = asRecord(raw);
  if (Object.keys(results).length > 0 && results.schema_version !== 1) {
    return { cars: [], definition: "", limitations: [], unavailableReason: results.schema_version === undefined
      ? "Field results unavailable: required schema version is missing"
      : "Field results unavailable: incompatible schema version" };
  }
  return {
    unavailableReason: null,
    definition: typeof results.pace_definition === "string" ? results.pace_definition : "Recorded green-lap median; traffic included.",
    limitations: asArray(results.limitations).filter((item): item is string => typeof item === "string"),
    cars: asArray(results.cars).flatMap((rawCar) => {
      const car = asRecord(rawCar);
      const index = num(car.car_index);
      if (index === null || !Number.isInteger(index) || index < 0) return [];
      return [{ index, name: typeof car.driver_name === "string" ? car.driver_name : `Car ${index}`,
        number: typeof car.car_number === "string" ? car.car_number : "", player: car.is_player === true,
        position: num(car.position), completed: num(car.laps_completed), fastest: num(car.fastest_lap_s),
        pace: num(car.pace_median_s), paceCount: num(car.pace_lap_count),
        pits: num(car.pit_visits), tires: Array.isArray(car.tires_taken) ? car.tires_taken.filter((item): item is string => ["LF", "RF", "LR", "RR"].includes(String(item))) : null,
        fuel: num(car.fuel_used_l),
        fastestSource: typeof car.fastest_source === "string" ? car.fastest_source : null,
        progressionUnavailableReason: typeof car.progression_unavailable_reason === "string" ? car.progression_unavailable_reason : null,
        laps: asArray(car.laps).flatMap((rawLap) => {
          const lap = asRecord(rawLap); const number = num(lap.lap);
          return number === null ? [] : [{ lap: number, time: num(lap.lap_time_s), timeSource: typeof lap.lap_time_source === "string" ? lap.lap_time_source : null, position: num(lap.position), flag: typeof lap.flag_state === "string" ? lap.flag_state : "unknown", pit: lap.on_pit_road === true, eligible: lap.pace_eligible === true }];
        }),
        pitEvents: asArray(car.pit_events).map((rawPit) => { const pit = asRecord(rawPit); return { lap: num(pit.lap), time: num(pit.session_time_s) }; }),
      }];
    }),
  };
}
export function sampledTime(source: string | null, player = false): boolean {
  return source === "recorded_lap_counter_intervals" || source === "recorded_interpolated_distance_crossings" || source === "interpolated_lap_crossings" || (source === null && !player);
}
export function timeSourceLabel(source: string | null, player = false): string {
  if (source === "session_results_snapshot") return "iRacing results snapshot";
  if (sampledTime(source, player)) return "Approximate sampled interval; not a scored lap time";
  return "Recorded player lap summary";
}
export function fieldTime(time: number | null, approximate = false): string {
  return time === null ? "—" : `${approximate ? "≈" : ""}${time.toFixed(approximate ? 1 : 3)}`;
}

export function hasFieldProgression(car: FieldCar): boolean {
  return car.laps.some((lap) => (lap.time !== null && lap.time > 0) || (lap.position !== null && lap.position > 0));
}

/** A roster name and absent/zero lap counts alone do not establish recorded history. */
export function hasFieldSummary(car: FieldCar): boolean {
  return hasFieldProgression(car) || [car.position, car.completed, car.fastest, car.pace, car.pits, car.fuel].some((value) => value !== null && value > 0) || (car.tires?.length ?? 0) > 0 || car.pitEvents.length > 0;
}

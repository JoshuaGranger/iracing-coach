import type { RaceViewData, RunsViewData, TraceLapData } from "../types";
import { asArray, asRecord, num } from "../types";
import { isCautionLap, isCleanLap, isCompleteLap, isGreenLap, isPitLap, lapFlags, type RunWearInput, type TraceData } from "./trace-model";

/**
 * Lap rail and pit-stop model: pure functions over the trace catalog, the race view and the runs view. The rail renders what these
 * return; nothing here is drawn or fetched.
 */

export interface RailRun {
  runNumber: number | null;
  firstLap: number;
  lastLap: number;
  green: number | null;
  caution: number | null;
  laps: TraceLapData[];
}

interface RunInfo {
  runNumber: number;
  laps: number[];
  green: number | null;
  caution: number | null;
}

function runInfos(runs: readonly unknown[]): RunInfo[] {
  const count = (value: unknown): number | null => (Array.isArray(value) ? value.length : num(value));
  return runs
    .map((raw) => {
      const r = asRecord(raw);
      const laps = asArray(r.lap_numbers).filter((lap): lap is number => typeof lap === "number");
      return { runNumber: num(r.run_number) ?? 0, laps, green: count(r.green_laps), caution: count(r.caution_laps) };
    })
    .filter((run) => run.laps.length > 0)
    .sort((a, b) => Math.min(...a.laps) - Math.min(...b.laps));
}

/**
 * Laps grouped by run in lap order (TelemetryWorkspace.LapGroups, audit T4): each lap joins the run that last started at or before
 * it; laps before the first run join the first run. Without runs every lap is one group. `keep` filters laps (Green, Clean).
 */
export function railGroups(data: TraceData, runs: readonly unknown[], keep: (meta: TraceLapData) => boolean): RailRun[] {
  const laps = data.laps.slice().sort((a, b) => a - b).map((lap) => data.lapMeta.get(lap)).filter((meta): meta is TraceLapData => meta !== undefined);
  const infos = runInfos(runs);
  if (infos.length === 0) {
    const kept = laps.filter(keep);
    return kept.length ? [{ runNumber: null, firstLap: laps[0]?.lap ?? 0, lastLap: laps[laps.length - 1]?.lap ?? 0, green: null, caution: null, laps: kept }] : [];
  }
  const out: RailRun[] = [];
  infos.forEach((run, index) => {
    const start = index === 0 ? Number.NEGATIVE_INFINITY : Math.min(...run.laps);
    const end = index + 1 < infos.length ? Math.min(...infos[index + 1]!.laps) : Number.POSITIVE_INFINITY;
    const members = laps.filter((meta) => meta.lap >= start && meta.lap < end);
    const kept = members.filter(keep);
    if (kept.length === 0) return;
    out.push({
      runNumber: run.runNumber,
      firstLap: members[0]!.lap,
      lastLap: members[members.length - 1]!.lap,
      green: run.green,
      caution: run.caution,
      laps: kept,
    });
  });
  return out;
}

export type LapFlag = "green" | "caution" | "white" | "checkered" | null;

/** One mark per lap for the rail's flag column: checkered, white, caution, green; a partial lap shows none (design, audit T9). */
export function railFlag(meta: TraceLapData): LapFlag {
  const flags = lapFlags(meta);
  const checkered = flags.some((flag) => flag === "checkered" || flag === "checker");
  const complete = isCompleteLap(meta);
  if (checkered && complete) return "checkered";
  if (!complete) return null;
  if (flags.includes("white")) return "white";
  if (isCautionLap(meta)) return "caution";
  return isGreenLap(meta) ? "green" : null;
}

/** Δ to the zero lap as the rail shows it: 3 decimals under 10 s, 1 decimal from 10 s, `—` for partial or untimed laps. */
export function railDelta(meta: TraceLapData, zero: TraceLapData | undefined): string {
  if (!zero || !isCompleteLap(meta) || typeof meta.lap_time_s !== "number" || typeof zero.lap_time_s !== "number") return "—";
  if (meta.lap === zero.lap) return "0.000";
  const delta = meta.lap_time_s - zero.lap_time_s;
  const digits = Math.abs(delta) >= 10 ? 1 : 3;
  return `${delta > 0 ? "+" : delta < 0 ? "−" : ""}${Math.abs(delta).toFixed(digits)}`;
}

/** The backend's screening as a chip: Partial for an incomplete lap, Excl with its reasons otherwise; null when offered. */
export function statusChip(meta: TraceLapData): { kind: "partial" | "excl"; reasons: string[] } | null {
  const reasons = Array.isArray(meta.exclusion_reasons) ? meta.exclusion_reasons.filter((reason) => reason !== "partial") : [];
  if (!isCompleteLap(meta)) return { kind: "partial", reasons };
  if (meta.comparison_eligible === false || reasons.length > 0) return { kind: "excl", reasons };
  return null;
}

export function passesFilters(meta: TraceLapData, greenOnly: boolean, cleanOnly: boolean, incidents: number): boolean {
  return (!cleanOnly || isCleanLap(meta, incidents)) && (!greenOnly || isGreenLap(meta));
}

/** Incident events per lap (any recorded event, `x0` included, audit T11) and their points. */
export interface LapIncidents {
  points: number;
  events: number;
}

export function incidentsByLap(race: RaceViewData): Map<number, LapIncidents> {
  const out = new Map<number, LapIncidents>();
  const events = asArray(asRecord(asRecord(race.damage_repair).incident_points).events);
  for (const event of events) {
    const record = asRecord(event);
    const lap = num(record.candidate_lap);
    if (lap === null) continue;
    const points = num(record.points_added) ?? 0;
    const current = out.get(lap) ?? { points: 0, events: 0 };
    out.set(lap, { points: current.points + points, events: current.events + 1 });
  }
  return out;
}

export function incidentPointsByLap(race: RaceViewData): Map<number, number> {
  return new Map([...incidentsByLap(race)].map(([lap, value]) => [lap, value.points] as const));
}

const LITERS_PER_GALLON = 3.785411784;
export type TireCorner = "LF" | "RF" | "LR" | "RR";
export const TIRE_CORNERS: readonly TireCorner[] = ["LF", "RF", "LR", "RR"];

export interface PitStop {
  lap: number;
  runNumber: number | null;
  /** Service time from the stop's start to its end, seconds; null when not recorded. */
  durationS: number | null;
  /** Tires the recording confirms changed; null when the stop has no tire record. */
  tires: ReadonlySet<TireCorner> | null;
  fuelAddedGal: number | null;
  /** The fuel gauge's full scale: the largest fuel load recorded at a run start (the backend forecast's capacity), gal. */
  capacityGal: number | null;
  /** Service after the recorded race ended (`post_run_service`): not an in-race stop, never a rail or chart pit marker. */
  postRun: boolean;
}

/** The fuel the car is known to hold: the backend's maximum recorded run-start fuel, else the largest run-start fuel. */
function capacityGallons(race: RaceViewData, runs: readonly unknown[]): number | null {
  const forecast = asRecord(asRecord(race.strategy).forecast);
  const liters = num(forecast.maximum_recorded_run_start_fuel_l);
  if (liters !== null && liters > 0) return liters / LITERS_PER_GALLON;
  const starts = runs.map((run) => num(asRecord(asRecord(run).fuel).start_l)).filter((value): value is number => value !== null && value > 0);
  return starts.length ? Math.max(...starts) / LITERS_PER_GALLON : null;
}

/** The projector's stops (`RaceView.pit_stops`): lap, service window, tires observed changed, fuel added. Null when absent. */
function projectedStops(race: RaceViewData, capacity: number | null): PitStop[] | null {
  const raw = (race as Record<string, unknown>).pit_stops;
  if (!Array.isArray(raw)) return null;
  const out: PitStop[] = [];
  for (const entry of raw) {
    const stop = asRecord(entry);
    const lap = num(stop.lap);
    if (lap === null) continue;
    const tiresRaw = stop.tires_changed;
    const gallons = num(stop.fuel_added_gal);
    const liters = num(stop.fuel_added_l);
    const service = num(stop.service_s);
    out.push({
      lap,
      runNumber: num(stop.run_number),
      durationS: service !== null && service > 0 ? service : null,
      tires: Array.isArray(tiresRaw) ? new Set(tiresRaw.filter((tire): tire is TireCorner => TIRE_CORNERS.includes(tire as TireCorner))) : null,
      fuelAddedGal: gallons ?? (liters !== null ? liters / LITERS_PER_GALLON : null),
      capacityGal: capacity,
      postRun: stop.post_run_service === true,
    });
  }
  return out.sort((a, b) => a.lap - b.lap);
}

/**
 * Pit stops: the race view's `pit_stops` when the server sends them; otherwise from the runs view (`runs[].pit_service`), where
 * the stop's lap is the lap of the run carrying a pit entry, else the run's last lap. Tires come from `tires_changed_observed`,
 * fuel from `fuel_added_l`, duration from the service start and end.
 */
export function pitStops(race: RaceViewData, runsView: RunsViewData | { runs: readonly unknown[] }, data: TraceData | null): PitStop[] {
  const runs = asArray(runsView.runs);
  const capacity = capacityGallons(race, runs);
  const projected = projectedStops(race, capacity);
  if (projected) return projected;
  const raceLaps = new Map(asArray(race.laps).map((lap) => [num(asRecord(lap).lap) ?? -1, asRecord(lap)] as const));
  const out: PitStop[] = [];
  for (const raw of runs) {
    const run = asRecord(raw);
    const service = asRecord(run.pit_service);
    if (Object.keys(service).length === 0) continue;
    const laps = asArray(run.lap_numbers).filter((lap): lap is number => typeof lap === "number").sort((a, b) => a - b);
    const entry = laps.find((lap) => {
      const meta = data?.lapMeta.get(lap) as Record<string, unknown> | undefined;
      return meta?.pit_entry === true || raceLaps.get(lap)?.pit_entry === true || raceLaps.get(lap)?.pit_in === true;
    });
    const lap = entry ?? num(run.end_lap) ?? laps[laps.length - 1];
    if (lap === undefined) continue;
    const start = num(service.start_time);
    const end = num(service.end_time);
    const tiresRaw = service.tires_changed_observed;
    const tires = Array.isArray(tiresRaw) ? new Set(tiresRaw.filter((tire): tire is TireCorner => TIRE_CORNERS.includes(tire as TireCorner))) : null;
    const liters = num(service.fuel_added_l);
    out.push({
      lap,
      runNumber: num(run.run_number),
      durationS: start !== null && end !== null && end > start ? end - start : null,
      tires,
      fuelAddedGal: liters !== null ? liters / LITERS_PER_GALLON : null,
      capacityGal: capacity,
      postRun: run.ended_with_post_run_service === true || service.post_run_service === true,
    });
  }
  return out.sort((a, b) => a.lap - b.lap);
}

/** The stops made during the race: post-run service is left out of the rail and the progression chart (review finding 10). */
export function inRaceStops(stops: readonly PitStop[]): PitStop[] {
  return stops.filter((stop) => !stop.postRun);
}

/** `RF RR`, `4 tires`, `No tires`, or `—` when the stop has no tire record. */
export function tiresText(stop: PitStop): string {
  if (!stop.tires) return "—";
  if (stop.tires.size === 4) return "4 tires";
  if (stop.tires.size === 0) return "No tires";
  return TIRE_CORNERS.filter((tire) => stop.tires!.has(tire)).join(" ");
}

/** Pit road time of a lap that is not a recorded service stop (a drive-through, the pit-out lap), for the rail's pit column. */
export function pitRoadSeconds(meta: TraceLapData): number | null {
  if (!isPitLap(meta)) return null;
  return typeof meta.pit_time_s === "number" && meta.pit_time_s > 0 ? meta.pit_time_s : 0;
}

/** Runs as the tire-wear row needs them: worst observed wear = 100 − `tire_observation.lowest_remaining_percent`. */
export function runWearInputs(runsView: RunsViewData | { runs: readonly unknown[] }): RunWearInput[] {
  return asArray(runsView.runs).map((raw) => {
    const run = asRecord(raw);
    const remaining = num(asRecord(run.tire_observation).lowest_remaining_percent);
    return {
      runNumber: num(run.run_number) ?? 0,
      laps: asArray(run.lap_numbers).filter((lap): lap is number => typeof lap === "number"),
      observedWearPercent: remaining !== null ? Math.min(100, Math.max(0, 100 - remaining)) : null,
    };
  });
}

/** Lap conditions as the lap-time tooltip shows them: `98 °F track · 76 °F air · 7 mph wind · 48% humidity`. */
export function conditionsText(meta: TraceLapData): string {
  const c = meta.conditions as Record<string, unknown> | null | undefined;
  if (!c) return "";
  const parts: string[] = [];
  if (typeof c.track_temperature_f === "number") parts.push(`${c.track_temperature_f.toFixed(0)} °F track`);
  if (typeof c.air_temperature_f === "number") parts.push(`${c.air_temperature_f.toFixed(0)} °F air`);
  if (typeof c.wind_speed_mph === "number") parts.push(`${c.wind_speed_mph.toFixed(0)} mph wind`);
  if (typeof c.relative_humidity_percent === "number") parts.push(`${c.relative_humidity_percent.toFixed(0)}% humidity`);
  return parts.join(" · ");
}

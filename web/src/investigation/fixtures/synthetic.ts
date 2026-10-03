import { encodeCcol, type CcolEncodeChannel } from "../ccol";
import type { InvestigationViewData, RaceViewData, RunsViewData, TraceCatalogData, TraceSignalData, TrackGeometryData } from "../types";

/**
 * Deterministic synthetic analysis for tests and the G2 harness: an 82-lap race on an invented closed
 * circuit, with the current-profile shapes (CCOL traces, catalog, track geometry, race, runs and
 * investigation views). Nothing here is race data; every value comes from a seeded generator.
 */

export interface SyntheticOptions {
  laps?: number;
  bins?: number;
  seed?: number;
  analysisId?: string;
}

export interface SyntheticAnalysis {
  analysisId: string;
  laps: number[];
  bins: number;
  race: RaceViewData;
  runs: RunsViewData;
  investigation: InvestigationViewData;
  catalog: TraceCatalogData;
  track: TrackGeometryData;
  ccol: ArrayBuffer;
}

function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const SIGNALS: [string, string, string, string, "f64" | "i16" | "u8", string, boolean][] = [
  // id, source_key, label, unit, dtype, category, default
  ["lap-pct", "lap_pct", "Lap fraction", "fraction", "f64", "time", true],
  ["session-time", "session_time_s", "Session time", "s", "f64", "time", true],
  ["speed", "speed_mph", "Speed", "mph", "f64", "speed", true],
  ["throttle", "throttle", "Throttle", "fraction", "f64", "pedals", true],
  ["brake", "brake", "Brake", "fraction", "f64", "pedals", true],
  ["steering", "steering_rad", "Road-wheel steering", "rad", "f64", "rotation", true],
  ["slip", "slip_angle_deg", "Slip angle", "deg", "f64", "rotation", true],
  ["yaw", "yaw_rate_deg_s", "Yaw rate", "deg/s", "f64", "rotation", true],
  ["lateral-g", "lateral_g", "Lateral G", "g", "f64", "acceleration", true],
  ["longitudinal-g", "longitudinal_g", "Longitudinal G", "g", "f64", "acceleration", true],
  ["gear", "gear", "Gear", "selected gear", "u8", "drivetrain", true],
  ["rpm", "rpm", "RPM", "rpm", "i16", "drivetrain", true],
  ["latitude", "latitude", "Latitude", "deg", "f64", "position", true],
  ["longitude", "longitude", "Longitude", "deg", "f64", "position", true],
  ["tire-stress", "tire_stress_proxy", "Tire load proxy", "fraction", "f64", "tire", true],
];

const TRACK_POINTS = 500;
const SOURCE_BOUNDS = { minimum_x: -88.5, maximum_x: -88.38, minimum_y: 42.1, maximum_y: 42.2 };
const NORMALIZATION_SCALE = 0.12;

/** Invented circuit in the unit square, y down, with a chicane and a hairpin so curvature varies. */
export function circuitPoint(t: number): { x: number; y: number } {
  const angle = t * Math.PI * 2;
  return {
    x: 0.5 + 0.42 * Math.cos(angle) + 0.07 * Math.cos(3 * angle),
    y: 0.5 + 0.28 * Math.sin(angle) + 0.05 * Math.sin(2 * angle),
  };
}

function curvatureAt(t: number): number {
  const h = 0.004;
  const a = circuitPoint(t - h);
  const b = circuitPoint(t);
  const c = circuitPoint(t + h);
  const d1x = (b.x - a.x) / h, d1y = (b.y - a.y) / h;
  const d2x = (c.x - 2 * b.x + a.x) / (h * h), d2y = (c.y - 2 * b.y + a.y) / (h * h);
  const speed = Math.hypot(d1x, d1y);
  return (d1x * d2y - d1y * d2x) / Math.pow(speed, 3);
}

export function buildSyntheticAnalysis(options: SyntheticOptions = {}): SyntheticAnalysis {
  const lapCount = options.laps ?? 82;
  const bins = options.bins ?? 160;
  const analysisId = options.analysisId ?? "synthetic-82-lap";
  const random = mulberry32(options.seed ?? 20261002);
  const laps = Array.from({ length: lapCount }, (_, index) => index + 1);
  const kappa = Float64Array.from({ length: bins }, (_, bin) => Math.abs(curvatureAt(bin / (bins - 1))));
  const kmax = Math.max(...kappa);
  const values = new Map<string, Float64Array>();
  for (const [id] of SIGNALS) values.set(id, new Float64Array(lapCount * bins).fill(Number.NaN));
  const lapTimes: number[] = [];
  const runOf = (lap: number): number => (lap <= 28 ? 1 : lap <= 56 ? 2 : 3);
  const runStart = [1, 29, 57];
  let clock = 0;
  for (let lapIndex = 0; lapIndex < lapCount; lapIndex++) {
    const lap = lapIndex + 1;
    const run = runOf(lap);
    const age = lap - runStart[run - 1]!;
    const pace = 1 + age * 0.0011 + (random() - 0.5) * 0.004;
    const started = clock;
    let time = clock;
    for (let bin = 0; bin < bins; bin++) {
      const t = bin / (bins - 1);
      const k = kappa[bin]! / kmax;
      const speed = (155 - 82 * Math.pow(k, 0.6)) / pace + (random() - 0.5) * 1.2;
      const nextK = kappa[Math.min(bins - 1, bin + 1)]! / kmax;
      const braking = nextK > k + 0.015 ? Math.min(1, (nextK - k) * 9) : 0;
      const throttle = braking > 0 ? 0 : Math.min(1, 0.35 + (1 - k) * 0.9 + (random() - 0.5) * 0.03);
      const steer = (curvatureAt(t) > 0 ? -1 : 1) * 0.5 * k * (0.9 + random() * 0.2);
      const mph = Math.max(35, speed);
      time += (1 / bins) * 3.0e3 / (mph * 17.6) * (1 + 0.5 * 0);
      const p = circuitPoint(t);
      const set = (id: string, value: number): void => { values.get(id)![lapIndex * bins + bin] = value; };
      set("lap-pct", t);
      set("session-time", time);
      set("speed", mph);
      set("throttle", throttle);
      set("brake", braking * (0.9 + random() * 0.1));
      set("steering", steer);
      set("slip", steer * 6 + (random() - 0.5) * 0.6);
      set("yaw", steer * 40 + (random() - 0.5) * 2);
      set("lateral-g", k * 1.6 * (steer >= 0 ? 1 : -1));
      set("longitudinal-g", throttle * 0.5 - braking * 1.4);
      set("gear", Math.max(1, Math.min(6, Math.round(mph / 28))));
      set("rpm", 3200 + mph * 38);
      set("latitude", SOURCE_BOUNDS.maximum_y - p.y * NORMALIZATION_SCALE);
      set("longitude", SOURCE_BOUNDS.minimum_x + p.x * NORMALIZATION_SCALE);
      set("tire-stress", Math.min(1, k * 0.8 + 0.1));
    }
    lapTimes.push(time - started);
    clock = time + 0.02;
  }
  const scale = lapTimes[0]! / 30;
  const lapTimesScaled = lapTimes.map((value) => Math.round((value / scale) * 1000) / 1000);
  const fastest = Math.min(...lapTimesScaled);

  const signals: TraceSignalData[] = SIGNALS.map(([id, source_key, label, unit, dtype, category, def]) => ({ id, source_key, label, unit, dtype, category, default: def }));
  const flagOf = (lap: number): string => (lap >= 41 && lap <= 43 ? "caution" : "green");
  const catalogLaps = laps.map((lap, index) => {
    const pit = lap === 28 || lap === 56 ? 24 : 0;
    const flag = flagOf(lap);
    const eligible = flag === "green" && pit === 0 && lap !== 29 && lap !== 57 && lap !== 1;
    return {
      lap,
      lap_time_s: lapTimesScaled[index]!,
      comparison_eligible: eligible,
      exclusion_reasons: eligible ? [] : [pit ? "pit" : flag !== "green" ? "caution_or_mixed" : "restart"],
      run_number: runOf(lap),
      run_age_laps: lap - runStart[runOf(lap) - 1]! + 1,
      fastest: Math.abs(lapTimesScaled[index]! - fastest) < 0.0001,
      complete: true,
      flag_state: flag,
      caution_fraction: flag === "caution" ? 1 : 0,
      pit_time_s: pit,
      fuel_used_gal: 0.3 + (index % 5) * 0.001,
    };
  });
  const catalog: TraceCatalogData = {
    analysis_id: analysisId,
    projection_version: 1,
    schema_version: 2,
    profile_version: "synthetic",
    bins_per_lap: bins,
    laps: catalogLaps,
    signals,
    sector_start_pcts: [0, 0.33, 0.66],
    steering_basis: { basis: "road_wheel", steering_ratio: 12, digits: 2 },
    budgets: { vertex_budget_per_signal: 48000, min_points_per_trace: 48, max_color_groups: 20, cursor_detail_traces: 24, max_trace_rows: 10 },
  };
  const channels: CcolEncodeChannel[] = SIGNALS.map(([id, , , , dtype]) => ({
    id,
    dtype,
    values: values.get(id)!,
    missing: dtype === "i16" ? -32768 : dtype === "u8" ? 255 : null,
  }));
  const ccol = encodeCcol(analysisId, 1, bins, laps, channels);

  const main = Array.from({ length: TRACK_POINTS }, (_, i) => ({ t: i / TRACK_POINTS, ...circuitPoint(i / TRACK_POINTS) }));
  const track: TrackGeometryData = {
    analysis_id: analysisId,
    projection_version: 1,
    schema_version: 2,
    profile_version: "synthetic",
    coordinate_space: "normalized_local_vector",
    status: "usable",
    track_configuration_key: "synthetic-circuit",
    geometry_hash: "synthetic",
    main_path: { pct: main.map((p) => p.t), x: main.map((p) => p.x), y: main.map((p) => p.y) },
    pit_lane: null,
    pit_entry: null,
    pit_exit: null,
    start_finish: { a: { x: main[0]!.x - 0.01, y: main[0]!.y }, b: { x: main[0]!.x + 0.01, y: main[0]!.y } },
    pit_commitment_line: null,
    pit_merge_line: null,
    transform: { source_bounds: SOURCE_BOUNDS, normalization_scale: NORMALIZATION_SCALE },
    quality: { main_loop_complete: true, lap_percent_coverage: 1, maximum_lap_percent_gap: 0.002, closure_distance: 0.004 },
    provenance: {},
  };

  const raceLaps = catalogLaps.map((lap) => ({
    lap: lap.lap,
    lap_time_s: lap.lap_time_s,
    comparison_eligible: lap.comparison_eligible,
    exclusion_reasons: lap.exclusion_reasons,
    run_number: lap.run_number,
    run_age_laps: lap.run_age_laps,
    flag_state: lap.flag_state,
    pit_in: lap.pit_time_s > 0,
    pit_out: lap.lap === 29 || lap.lap === 57,
    incident_count: lap.lap === 17 ? 2 : 0,
  }));
  const race: RaceViewData = {
    analysis_id: analysisId,
    projection_version: 1,
    schema_version: 2,
    profile_version: "synthetic",
    identity: { track_name: "Synthetic Circuit", car_name: "Synthetic GT", driver_name: "Test Driver" },
    race_summary: { recorded_laps: lapCount, pit_stops_detected: 2, starting_position: 4, final_recorded_position: 2 },
    laps: raceLaps,
  };
  const runs: RunsViewData = {
    analysis_id: analysisId,
    projection_version: 1,
    schema_version: 2,
    profile_version: "synthetic",
    runs: [1, 2, 3].map((run) => ({ run_number: run, lap_numbers: laps.filter((lap) => runOf(lap) === run), green_laps: 25, caution_laps: run === 2 ? 3 : 0, ended_with_pit_stop: run < 3 })),
  };

  const eligibleLaps = catalogLaps.filter((lap) => lap.comparison_eligible);
  const trendSegment = (run: number): { lap: number; x: number; y: number; sample_laps: number[] }[] =>
    eligibleLaps.filter((lap) => lap.run_number === run).map((lap) => ({ lap: lap.lap, x: lap.run_age_laps, y: lap.lap_time_s, sample_laps: [lap.lap] }));
  const cohortOf = (lapsIn: number[], run: number): Record<string, unknown> => ({
    label: `Laps ${lapsIn[0]}–${lapsIn[lapsIn.length - 1]}`,
    run_number: run,
    lap_numbers: lapsIn,
    lap_count: lapsIn.length,
    eligible_lap_numbers: lapsIn,
    excluded_laps: [],
    median_lap_s: lapTimesScaled[lapsIn[1]! - 1]!,
    green_age_start: lapsIn[0]! - runStart[run - 1]! + 1,
    green_age_end: lapsIn[lapsIn.length - 1]! - runStart[run - 1]! + 1,
    fuel_start_gal: 12.1,
    track_temp_f: 82,
    representative_lap: lapsIn[1],
  });
  const rows = Array.from({ length: 60 }, (_, i) => {
    const x = 0.1 + (i / 59) * 0.4;
    const base = 100 - 50 * Math.sin(((x - 0.1) / 0.4) * Math.PI);
    return { x, gap: (i / 59) * 0.036, a: { speed: base, throttle: 40, brake: i < 20 ? 60 : 0, steering: 0.9, slip: 0.4 }, b: { speed: base - 0.6, throttle: 38, brake: i < 20 ? 62 : 0, steering: 0.95, slip: 0.5 } };
  });
  const investigation: InvestigationViewData = {
    analysis_id: analysisId,
    projection_version: 1,
    schema_version: 2,
    profile_version: "synthetic",
    race_investigation: {
      schema_version: 2,
      projection_version: "sections-trials-v7",
      status: "available",
      summary: "Synthetic race.",
      default_comparison_id: "run-1-vs-run-2",
      limitations: [],
      comparisons: [
        {
          id: "run-1-vs-run-2",
          kind: "run_window",
          title: "Run 1 vs run 2",
          summary: "",
          delta_s: 0.12,
          eligibility: "eligible",
          pace_comparable: true,
          pace_unusable_reasons: [],
          baseline: cohortOf([3, 4, 5], 1),
          compared: cohortOf([31, 32, 33], 2),
          metrics: [{ key: "brake", label: "Brake work", unit: "%", baseline: 40, compared: 43, delta: 3, evidence: "derived", detail: "3 earlier and 3 later laps." }],
          corners: [{ id: "A", label: "Section A (10.0–40.0% lap)", short_label: "A", start_pct: 0.1, end_pct: 0.4, delta_s: 0.036, boundary_status: "detected_section", baseline_count: 3, compared_count: 3, metrics: [] }],
          hypotheses: [{ key: "tires", label: "Tire fall-off", status: "possible", supporting: ["Later laps slower at equal fuel."], contradicting: [], unknown: [] }],
          actions: [{ priority: 1, title: "Repeat section A at the same run age", instruction: "Drive three clear laps with unchanged inputs.", limitation: null, lap_numbers: [3, 4, 5, 31, 32, 33], start_pct: 0.1, end_pct: 0.4, signal_keys: ["speed", "throttle"], section_id: "A" }],
          inspection: {
            mode: "baseline_median_vs_compared_median",
            pool_laps: [3, 4, 5, 31, 32, 33],
            sections: {
              A: {
                section_id: "A",
                start_pct: 0.2,
                end_pct: 0.5,
                context_start_pct: 0.1,
                context_label: "approach start",
                timing_complete: true,
                candidate_count: 3,
                section_delta_s: 0.036,
                entry_gap_s: 0,
                exit_gap_s: 0.036,
                entry_speed_difference_mph: -0.6,
                a: { lap: 4, role: "baseline_median", section_s: 9.012, run_number: 1, green_exposure: 4, eligible: true, exclusion_reasons: [], fuel_start_gal: 12.1 },
                b: { lap: 32, role: "compared_median", section_s: 9.048, run_number: 2, green_exposure: 4, eligible: true, exclusion_reasons: [], fuel_start_gal: 12.0 },
                slip: { a: { coverage: 1, median_abs: 0.4, peak_abs: 1 }, b: { coverage: 1, median_abs: 0.5, peak_abs: 1.2 } },
                steering: { comparable: true, basis: "road_wheel", degree_digits: 2, label: "Road-wheel steering", reason: null },
                rows,
              },
            },
          },
        },
      ],
      laps: catalogLaps.map((lap) => ({
        lap: lap.lap,
        lap_time_s: lap.lap_time_s,
        position: 4 - Math.floor(lap.lap / 30),
        flag_state: lap.flag_state,
        eligible: lap.comparison_eligible,
        exclusion_reasons: lap.exclusion_reasons,
        run_number: lap.run_number,
        plot_pace: lap.pit_time_s === 0,
        green_exposure: lap.flag_state === "green" ? lap.run_age_laps : null,
      })),
      timeline: [
        { lap: 28, kind: "pit_service", label: "Pit", detail: "Stop", value: 24, unit: "s" },
        { lap: 17, kind: "incident", label: "Incident", detail: "Contact", value: 2, unit: "x" },
      ],
      runs: [1, 2, 3].map((run) => ({
        run_number: run,
        plotted_lap_count: 25,
        first_lap: runStart[run - 1],
        last_lap: run === 3 ? lapCount : runStart[run]! - 1,
        fit_unavailable_reason: null,
        trend: { method: "centered_five_observation_median", covered_lap_count: 25, segments: [trendSegment(run)], gaps: [] },
        fit: { method: "ordinary_least_squares", eligible_count: 20, laps: [], slope_s_per_green_lap: 0.02, domain: [1, 25], points: [{ x: 1, y: 30.0 }, { x: 25, y: 30.5 }] },
      })),
      sections: {
        configuration_key: null,
        source: "detected_in_recording",
        version: "detected/synthetic",
        provenance: "Regions detected in this recording; not turn numbers.",
        unavailable_reason: null,
        sections: [{ id: "A", label: "A", start_pct: 0.1, end_pct: 0.4, boundary_status: "detected_section", provenance: "Detected region 1." }],
      },
      track_evolution: { status: "available", summary: "Track temperature stable.", provenance: "", points: [] },
      steering: { basis: "road_wheel", steering_ratio: 12 },
    },
  };
  return { analysisId, laps, bins, race, runs, investigation, catalog, track, ccol };
}

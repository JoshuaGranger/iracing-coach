import { buildSyntheticAnalysis, type SyntheticAnalysis } from "./synthetic";
import { withObservedTrends } from "./trend";

/**
 * The synthetic race dressed for the round-2 investigation checks (e2e and the A3 screenshots): three comparisons (two runs; one run
 * with an omitted lap and a screened lap; one with no pace result), laps screened for close traffic, slow caution laps, three
 * sections with pass pairs, run fits with a confined domain and one run without a fit, pit stops, and the runs view's fuel and tire
 * laps. Every value is invented; nothing here is race data.
 */

type Json = Record<string, unknown>;

function passRows(start: number, end: number, gapEnd: number, seed: number): Json[] {
  const context = Math.max(0, start - 0.08);
  return Array.from({ length: 60 }, (_, i) => {
    const x = context + (i / 59) * (end - context);
    const shape = Math.sin(((x - context) / Math.max(1e-9, end - context)) * Math.PI);
    const base = 140 - 55 * shape - seed;
    return {
      x,
      gap: (i / 59) * gapEnd,
      a: { speed: base, throttle: 30 + 60 * (1 - shape), brake: shape > 0.6 ? 0 : 40 * (1 - shape), steering: 0.8 * shape, slip: 0.4 * shape },
      b: { speed: base - 0.8, throttle: 28 + 60 * (1 - shape), brake: shape > 0.6 ? 0 : 44 * (1 - shape), steering: 0.85 * shape, slip: 0.5 * shape },
    };
  });
}

function pair(id: string, start: number, end: number, delta: number, laps: [number, number], runs: [number, number], seed: number): Json {
  return {
    section_id: id,
    start_pct: start,
    end_pct: end,
    context_start_pct: Math.max(0, start - 0.08),
    context_label: "approach start",
    timing_complete: true,
    candidate_count: 5,
    section_delta_s: delta,
    entry_gap_s: 0,
    exit_gap_s: delta,
    entry_speed_difference_mph: -0.8,
    a: { lap: laps[0], role: "baseline_median", section_s: 6.4, run_number: runs[0], green_exposure: 4, eligible: true, exclusion_reasons: [], fuel_start_gal: 14.1 },
    b: { lap: laps[1], role: "compared_median", section_s: 6.4 + delta, run_number: runs[1], green_exposure: 4, eligible: true, exclusion_reasons: [], fuel_start_gal: 6.3 },
    steering: { comparable: true, basis: "road_wheel", degree_digits: 2, label: "Road-wheel steering", reason: null },
    rows: passRows(start, end, delta, seed),
  };
}

const SECTIONS = [
  { id: "A", label: "A", start_pct: 0.1, end_pct: 0.3 },
  { id: "B", label: "B", start_pct: 0.42, end_pct: 0.58 },
  { id: "C", label: "C", start_pct: 0.7, end_pct: 0.86 },
];

export const ROUND2_COMPARISONS = { runs: "run-2-vs-run-3", window: "run-3-window", none: "run-1-window" } as const;

export function buildRound2Analysis(analysisId = "round2-82-lap"): SyntheticAnalysis {
  const analysis = buildSyntheticAnalysis({ laps: 82, analysisId });
  const traffic = new Set([10, 11, 79]);
  const cautionTimes = new Map([[41, 38.2], [42, 39.1], [43, 36.4]]);
  const lapTime = new Map<number, number>();

  // Catalog and race laps: close-traffic screening and slow caution laps, the same on every view.
  for (const lap of analysis.catalog.laps as Json[]) {
    const number = lap.lap as number;
    if (cautionTimes.has(number)) lap.lap_time_s = cautionTimes.get(number);
    if (traffic.has(number)) Object.assign(lap, { comparison_eligible: false, exclusion_reasons: ["close_traffic"] });
    lapTime.set(number, lap.lap_time_s as number);
  }
  for (const lap of analysis.race.laps as Json[]) {
    const number = lap.lap as number;
    lap.lap_time_s = lapTime.get(number);
    if (traffic.has(number)) Object.assign(lap, { comparison_eligible: false, exclusion_reasons: ["close_traffic"] });
  }
  (analysis.race as Json).pit_stops = [
    { run_number: 1, lap: 28, start_time_s: 840, end_time_s: 853.8, service_s: 13.8, tires_changed: ["LF", "LR", "RF", "RR"], fuel_added_l: 45.4, fuel_added_gal: 12.0, post_run_service: false },
    { run_number: 2, lap: 56, start_time_s: 1700, end_time_s: 1708.2, service_s: 8.2, tires_changed: ["RF", "RR"], fuel_added_l: 30.3, fuel_added_gal: 8.0, post_run_service: false },
  ];

  // Runs view: fuel at start and end, green laps on the tires at the run's end.
  const runFuel: Record<number, [number, number]> = { 1: [60, 14.3], 2: [59.2, 13.9], 3: [52.1, 18.4] };
  const tireLaps: Record<number, [number, number]> = { 1: [27, 27], 2: [25, 52], 3: [26, 26] };
  for (const run of analysis.runs.runs as Json[]) {
    const number = run.run_number as number;
    run.fuel = { start_l: runFuel[number]![0], end_l: runFuel[number]![1] };
    const [front, rear] = tireLaps[number]!;
    run.tire_set_lifecycle = { corners: { LF: { green_laps_on_set: front }, LR: { green_laps_on_set: front }, RF: { green_laps_on_set: rear }, RR: { green_laps_on_set: rear } } };
  }

  const ri = (analysis.investigation as Json).race_investigation as Json;
  for (const lap of ri.laps as Json[]) {
    const number = lap.lap as number;
    lap.lap_time_s = lapTime.get(number);
    if (cautionTimes.has(number)) Object.assign(lap, { plot_pace: false, eligible: false });
    if (traffic.has(number)) Object.assign(lap, { eligible: false, exclusion_reasons: ["close_traffic"] });
  }

  const cohort = (laps: number[], run: number, start: number, fuelGal: number, excluded: { lap: number; reasons: string[] }[] = [], median?: number): Json => {
    const eligible = laps.filter((lap) => !excluded.some((item) => item.lap === lap));
    const times = eligible.map((lap) => lapTime.get(lap)!).sort((a, b) => a - b);
    return {
      label: `Laps ${laps[0]}–${laps[laps.length - 1]}`,
      run_number: run,
      lap_numbers: laps,
      lap_count: laps.length,
      eligible_lap_numbers: eligible,
      excluded_laps: excluded,
      median_lap_s: median ?? times[Math.floor(times.length / 2)] ?? null,
      green_age_start: laps[0]! - start + 1,
      green_age_end: laps[laps.length - 1]! - start + 1,
      fuel_start_gal: fuelGal,
      track_temp_f: 84,
      representative_lap: laps[1],
    };
  };
  const metrics = (throttle: [number, number], trafficPct: [number, number], fuel: [number, number]): Json[] => [
    { key: "throttle", label: "Average throttle", unit: "%", baseline: throttle[0], compared: throttle[1], delta: throttle[1] - throttle[0], evidence: "derived", detail: null },
    { key: "traffic", label: "Close traffic", unit: "%", baseline: trafficPct[0], compared: trafficPct[1], delta: trafficPct[1] - trafficPct[0], evidence: "derived", detail: null },
    { key: "fuel", label: "Fuel", unit: "gal", baseline: fuel[0], compared: fuel[1], delta: fuel[1] - fuel[0], evidence: "derived", detail: null },
  ];
  const corners = (deltas: (number | null)[]): Json[] => SECTIONS.map((s, i) => ({ id: s.id, label: `Section ${s.id}`, short_label: s.id, start_pct: s.start_pct, end_pct: s.end_pct, delta_s: deltas[i], boundary_status: "detected_section", baseline_count: deltas[i] === null ? 0 : 5, compared_count: deltas[i] === null ? 0 : 5, metrics: [] }));
  const inspection = (laps: [number, number], runs: [number, number], deltas: number[]): Json => ({
    mode: "baseline_median_vs_compared_median",
    pool_laps: [],
    sections: Object.fromEntries(SECTIONS.map((s, i) => [s.id, pair(s.id, s.start_pct, s.end_pct, deltas[i]!, laps, runs, i * 3)])),
  });
  const action = (laps: number[], section: string | null): Json[] => [{ priority: 1, title: "Inspect the later laps", instruction: "", limitation: null, lap_numbers: laps, start_pct: null, end_pct: null, signal_keys: ["speed", "throttle", "brake"], section_id: section }];

  ri.default_comparison_id = ROUND2_COMPARISONS.runs;
  ri.comparisons = [
    {
      id: ROUND2_COMPARISONS.runs, kind: "run_window", title: "Run 2 vs run 3", summary: "", delta_s: 0.118, eligibility: "eligible", pace_comparable: true, pace_unusable_reasons: [],
      baseline: cohort([31, 32, 33, 34, 35], 2, 29, 14.6), compared: cohort([61, 62, 63, 64, 65], 3, 57, 12.9),
      metrics: metrics([71, 68], [4, 22], [14.6, 12.9]), corners: corners([0.036, -0.021, null]), hypotheses: [], actions: action([31, 61], "A"),
      inspection: inspection([33, 63], [2, 3], [0.036, -0.021, 0.004]),
    },
    {
      id: ROUND2_COMPARISONS.window, kind: "run_window", title: "Run 3 earlier to later", summary: "", delta_s: -0.052, eligibility: "partial", pace_comparable: true, pace_unusable_reasons: [],
      baseline: cohort([61, 62, 64, 65], 3, 57, 12.9), compared: cohort([77, 78, 79, 80, 81], 3, 57, 6.1, [{ lap: 79, reasons: ["close_traffic"] }]),
      metrics: metrics([68, 70], [9, 31], [12.9, 6.1]), corners: corners([-0.012, 0.018, -0.006]), hypotheses: [], actions: action([62, 78], null),
      inspection: inspection([62, 78], [3, 3], [-0.012, 0.018, -0.006]),
    },
    {
      id: ROUND2_COMPARISONS.none, kind: "run_window", title: "Run 1 earlier to later", summary: "Not a usable pace comparison.", delta_s: null, eligibility: "observed", pace_comparable: false, pace_unusable_reasons: ["all_laps_excluded"],
      baseline: cohort([9, 10, 11], 1, 1, 15.2, [{ lap: 9, reasons: ["close_traffic"] }, { lap: 10, reasons: ["close_traffic"] }, { lap: 11, reasons: ["close_traffic"] }]),
      compared: cohort([22, 23, 24], 1, 1, 8.8, [{ lap: 22, reasons: ["close_traffic"] }, { lap: 23, reasons: ["close_traffic"] }, { lap: 24, reasons: ["close_traffic"] }]),
      metrics: metrics([70, 70], [95, 100], [15.2, 8.8]), corners: corners([0.01, 0.01, 0.01]), hypotheses: [], actions: [],
      inspection: inspection([10, 23], [1, 1], [0.01, 0.01, 0.01]),
    },
  ];
  ri.sections = { ...(ri.sections as Json), sections: SECTIONS.map((s, i) => ({ ...s, boundary_status: "detected_section", provenance: `Detected region ${i + 1}.` })) };
  ri.timeline = [
    { lap: 28, kind: "pit_service", label: "Pit", detail: "Stop", value: null, unit: null },
    { lap: 56, kind: "pit_service", label: "Pit", detail: "Stop", value: null, unit: null },
    { lap: 17, kind: "incident", label: "Incident", detail: "Contact", value: 2, unit: "x" },
  ];
  // Fits: run 1 and 2 confined to green laps 3–24; run 3 has none.
  for (const run of ri.runs as Json[]) {
    if (run.run_number === 3) {
      run.fit = null;
      run.fit_unavailable_reason = "insufficient_timed_clean_laps";
    } else {
      const [slope, y0] = run.run_number === 1 ? [0.033, 30.04] : [0.029, 30.09];
      run.fit = { method: "ordinary_least_squares", eligible_count: run.run_number === 1 ? 18 : 21, laps: [], slope_s_per_green_lap: slope, domain: [3, 24], points: [{ x: 1, y: y0 }, { x: 25, y: y0 + slope * 24 }] };
    }
  }
  return withObservedTrends(analysis);
}


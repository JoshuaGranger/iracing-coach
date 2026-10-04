import { DEFAULT_BUDGETS, type RenderBudgets } from "../types";

/** Port of AnalysisRenderBudget (AnalysisTrackMapGeometry.cs:132-162) under the catalog's budgets. */

/** Detailed-cursor sample bins: the cursor reads the traces at the catalog's own bin grid. */
export const CURSOR_SAMPLE_BINS = 160;

export function pointBudgetPerTrace(
  logicalTraceCount: number,
  screenWidth: number,
  focused = false,
  budgets: RenderBudgets = DEFAULT_BUDGETS,
): number {
  const screenBudget = Math.min(1200, Math.max(64, Math.ceil(Math.max(1, screenWidth) * 1.25)));
  if (focused) return screenBudget;
  const shared = Math.max(budgets.min_points_per_trace, Math.floor(budgets.vertex_budget_per_signal / Math.max(1, logicalTraceCount)));
  return Math.min(screenBudget, shared);
}

/** Evenly spaced indices over [0, itemCount - 1], at most `budget`, always including both ends. */
export function representativeIndices(itemCount: number, budget: number): number[] {
  if (itemCount <= 0 || budget <= 0) return [];
  if (itemCount <= budget) return Array.from({ length: itemCount }, (_, index) => index);
  if (budget === 1) return [0];
  const out: number[] = [];
  let previous = -1;
  for (let slot = 0; slot < budget; slot++) {
    // Math.round on a positive value rounds half up; C# Math.Round is half-to-even, which differs only at exact .5.
    const raw = (slot * (itemCount - 1)) / (budget - 1);
    const index = roundHalfEven(raw);
    if (index !== previous) out.push(index);
    previous = index;
  }
  return out;
}

export function roundHalfEven(value: number): number {
  const floor = Math.floor(value);
  const diff = value - floor;
  if (diff < 0.5) return floor;
  if (diff > 0.5) return floor + 1;
  return floor % 2 === 0 ? floor : floor + 1;
}

export function colorGroupCount(logicalTraceCount: number, budgets: RenderBudgets = DEFAULT_BUDGETS): number {
  return Math.min(Math.max(0, logicalTraceCount), budgets.max_color_groups);
}

/**
 * Laps that get a detailed cursor trace: the spotlight lap, the reference lap, the first and last
 * selected lap, then evenly spaced laps up to `budget` (TelemetryWorkspace.BoundedCursorTraces).
 * `selected` is ordered as the cursor orders it: spotlight first, then ascending lap number.
 */
export function boundedCursorLaps(selected: number[], spotlight: number | null, reference: number | null, budget: number): number[] {
  if (selected.length <= budget) return selected;
  const chosen = new Set<number>();
  const add = (lap: number | undefined | null): void => {
    if (lap !== undefined && lap !== null && selected.includes(lap) && chosen.size < budget) chosen.add(lap);
  };
  // The four named laps are always kept; evenly spaced laps fill the rest (the C# version cut the
  // tail after ordering, which could drop the last selected lap).
  add(spotlight);
  add(reference);
  add(selected[0]);
  add(selected[selected.length - 1]);
  for (const index of representativeIndices(selected.length, budget)) add(selected[index]);
  return [...chosen]
    .sort((a, b) => (spotlight === a ? 0 : 1) - (spotlight === b ? 0 : 1) || a - b)
    .slice(0, budget);
}

/**
 * Lap identity palette (TelemetryWorkspace.TracePalette less the colours that imitate the caution yellow or the fastest-lap
 * magenta, design README "Theme changes"; the pink and the lilac read as the fastest lap next to it); the fastest lap is always
 * magenta.
 */
export const TRACE_PALETTE: readonly string[] = [
  "#63F5D2", "#69A7FF", "#A879FF", "#FF7B72", "#66F06A",
  "#54D6FF", "#FF9D4D", "#8EF0FF", "#4FE0A4", "#7F8CFF",
  "#B8F25D", "#FFB56B", "#54ECE5", "#F07B67",
];
export const FASTEST_LAP_COLOR = "#F05CDB";

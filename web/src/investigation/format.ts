/** Presentation helpers for the investigation (InvestigationPresentation.cs). Values are formatted, never derived. */

import { runColour, runDash as paletteDash } from "../core/palette";

/**
 * Runs are identity: the ordered run palette (`--run-1` … `--run-8`, `identity.runs` in the theme) on run-level marks, with line
 * style as the second cue (run 1 solid, 2 dashed, 3 dotted).
 */
export function runColor(run: number | null | undefined): string {
  return runColour(run ?? null);
}

export function runDash(run: number | null | undefined): string {
  return paletteDash(run ?? null);
}

const isNumber = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);

export function seconds(value: number | null | undefined, digits = 3): string {
  return isNumber(value) ? value.toFixed(digits) : "—";
}

export function signedText(value: number | null | undefined, digits = 3): string {
  if (!isNumber(value)) return "—";
  return (value > 0 ? "+" : value < 0 ? "−" : "") + Math.abs(value).toFixed(digits);
}

export function numberText(value: number | null | undefined, digits = 1): string {
  if (!isNumber(value)) return "—";
  return (value < 0 ? "−" : "") + Math.abs(value).toFixed(digits);
}

export function percentText(fraction: number): string {
  return (fraction * 100).toFixed(1);
}

export function exposureText(value: number | null | undefined, word = "green"): string {
  return isNumber(value) ? `${value.toFixed(1)} ${word}` : "exposure unknown";
}

export function lapTime(value: number | null | undefined): string {
  if (!isNumber(value)) return "—";
  if (value >= 60) {
    const minutes = Math.floor(value / 60);
    return `${minutes}:${(value - minutes * 60).toFixed(3).padStart(6, "0")}`;
  }
  return value.toFixed(3);
}

/** "35, 36, 37" for short windows and "116–120" for contiguous long ones; never ambiguous. */
export function lapsText(laps: readonly number[]): string {
  if (laps.length === 0) return "no laps";
  const ordered = laps.slice().sort((a, b) => a - b);
  const contiguous = ordered.every((lap, index) => index === 0 || lap === ordered[index - 1]! + 1);
  return contiguous && ordered.length > 3 ? `${ordered[0]}–${ordered[ordered.length - 1]}` : ordered.join(", ");
}

const REASONS: Record<string, string> = {
  close_traffic: "close traffic",
  incident_points: "incident",
  eligibility_unknown: "screening unknown",
  eligibility_not_recorded: "screening not recorded",
  lap_timing_unavailable: "timing unavailable",
  not_racing_state: "not racing",
  recorded_repair_disruption: "repair",
  damage_repair_context: "tow/repair review",
  first_lap_of_run: "first lap of run",
  stopped_on_track: "stopped on track",
  caution_or_mixed: "caution",
  off_track: "off track",
  pit: "pit",
  partial: "partial",
  restart: "restart",
  pit_tow_or_repair_episode_overlap: "tow/repair",
  all_laps_excluded: "every lap screened",
  insufficient_timed_clean_laps: "too few clean laps",
};

export function reason(code: string): string {
  return REASONS[code] ?? code.replace(/_/g, " ");
}

export function reasonsText(codes: readonly string[]): string {
  return [...new Set(codes.map(reason))].join(", ");
}

export function passRole(role: string): string {
  switch (role) {
    case "fastest": return "Fastest";
    case "slowest": return "Slowest";
    case "baseline_median": return "Earlier group median";
    case "compared_median": return "Later group median";
    case "before": return "Before";
    case "after": return "After";
    default: return role;
  }
}

export function hypothesisStatus(status: string): string {
  switch (status) {
    case "supported": return "Observed change";
    case "possible": return "Possible";
    case "not_supported": return "Not supported";
    default: return "Unresolved";
  }
}

/** Evenly spaced round ticks inside [min, max]. */
export function ticks(min: number, max: number, count: number): number[] {
  if (!Number.isFinite(min) || !Number.isFinite(max) || max <= min) return [min];
  const raw = (max - min) / Math.max(1, count);
  const magnitude = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map((factor) => factor * magnitude).find((candidate) => candidate >= raw) ?? raw;
  const first = Math.ceil(min / step) * step;
  const out: number[] = [];
  for (let value = first; value <= max + step * 1e-6; value += step) out.push(Math.round(value * 1e6) / 1e6);
  return out;
}

/** Linear pixel mapping for one chart plot area (PlotScale). */
export class PlotScale {
  constructor(
    readonly left: number,
    readonly top: number,
    readonly width: number,
    readonly height: number,
    readonly xMin: number,
    readonly xMax: number,
    readonly yMin: number,
    readonly yMax: number,
  ) {}

  x(value: number): number {
    return this.left + ((value - this.xMin) / Math.max(1e-9, this.xMax - this.xMin)) * this.width;
  }

  y(value: number): number {
    return this.top + ((this.yMax - value) / Math.max(1e-9, this.yMax - this.yMin)) * this.height;
  }

  get right(): number {
    return this.left + this.width;
  }

  get bottom(): number {
    return this.top + this.height;
  }
}

export const n = (value: number): string => (Math.round(value * 100) / 100).toString();

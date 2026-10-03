import type { MapPoint } from "../geometry/map-model";

/**
 * Pure parts of the synchronized chart/map cursor (analysis-telemetry-cursor.js): bin lookup, value
 * formatting, map interpolation, track projection and tooltip placement. No DOM, no network.
 */

export const clamp = (value: number, minimum: number, maximum: number): number => Math.max(minimum, Math.min(maximum, value));
export const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);

/** Index of the bin nearest to `fraction` in an ascending array. */
export function nearestIndex(points: ArrayLike<number>, fraction: number): number {
  const count = points.length;
  if (count === 0) return -1;
  let low = 0;
  let high = count - 1;
  while (low <= high) {
    const middle = low + ((high - low) >> 1);
    if (points[middle]! < fraction) low = middle + 1;
    else high = middle - 1;
  }
  if (low <= 0) return 0;
  if (low >= count) return count - 1;
  return Math.abs(points[low]! - fraction) < Math.abs(points[low - 1]! - fraction) ? low : low - 1;
}

export function signed(value: number, digits: number): string {
  if (Math.abs(value) < Math.pow(10, -digits) / 2) return (0).toFixed(digits);
  return `${value > 0 ? "+" : ""}${value.toFixed(digits)}`;
}

/** iRacing steering is positive to the left; the backend's basis sets the decimals. */
export function directionalDegrees(value: number, compact: boolean, digits: number | null): string {
  const places = digits !== null && Number.isInteger(digits) ? digits : 1;
  if (Math.abs(value) < Math.pow(10, -places) / 2) return compact ? "C 0°" : "Center 0°";
  const direction = value > 0 ? (compact ? "L" : "Left") : compact ? "R" : "Right";
  return `${direction} ${Math.abs(value).toFixed(places)}°`;
}

export interface FormatSignal {
  id: string;
  shortLabel: string;
  unit: string;
  digits: number | null;
}

export function formatSignalValue(signal: FormatSignal, value: number, includeLabel: boolean): string {
  const prefix = includeLabel ? `${signal.shortLabel} ` : "";
  switch (signal.id) {
    case "delta": return `${prefix}${signed(value, 3)} s`;
    case "gear": return `${prefix}${value.toFixed(0)}`;
    case "rpm": return `${prefix}${value.toFixed(0)} rpm`;
    case "speed": return `${prefix}${value.toFixed(1)} mph`;
    case "throttle":
    case "brake": return `${prefix}${(value * 100).toFixed(0)}%`;
    case "tire-wear": return `${prefix}${value.toFixed(3)}% est.`;
    case "steering":
    case "steering-wheel-angle": return `${prefix}${directionalDegrees(value, includeLabel, signal.digits)}`;
    case "slip": return `${prefix}${signed(value, 1)}°`;
    case "yaw": return `${prefix}${signed(value, 1)}°/s`;
    case "lateral-g":
    case "longitudinal-g": return `${prefix}${signed(value, 2)} g`;
    default: {
      const unit = signal.unit || "";
      if (unit === "on / off") return `${prefix}${value >= 0.5 ? "On" : "Off"}`;
      if (unit === "state" || unit === "position") return `${prefix}${value.toFixed(0)}`;
      const digits = unit === "%" || unit === "mph" || unit === "deg F" || unit === "psi" ? 1 : unit === "g" ? 2 : 3;
      const renderedUnit = unit === "deg F" ? "°F" : unit;
      return `${prefix}${value.toFixed(digits)}${renderedUnit ? ` ${renderedUnit}` : ""}`;
    }
  }
}

/** Fraction along the main loop of the point of the polyline nearest to the pointer (projectedTrackFraction). */
export function projectedTrackFraction(points: readonly MapPoint[], px: number, py: number, fallback: number): number {
  if (points.length === 0) return fallback;
  let bestDistance = Number.POSITIVE_INFINITY;
  let bestFraction = fallback;
  for (let index = 0; index < points.length; index++) {
    const a = points[index]!;
    const b = points[(index + 1) % points.length]!;
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const lengthSquared = dx * dx + dy * dy;
    const amount = lengthSquared <= 0.000001 ? 0 : clamp(((px - a.x) * dx + (py - a.y) * dy) / lengthSquared, 0, 1);
    const qx = a.x + dx * amount;
    const qy = a.y + dy * amount;
    const d = (px - qx) ** 2 + (py - qy) ** 2;
    if (d >= bestDistance) continue;
    bestDistance = d;
    const end = b.percent <= a.percent ? b.percent + 1 : b.percent;
    const value = a.percent + (end - a.percent) * amount;
    bestFraction = ((value % 1) + 1) % 1;
  }
  return bestFraction;
}

export interface TooltipPlacementInput {
  cursorX: number;
  plotLeft: number;
  plotWidth: number;
  tooltipWidth: number;
}

/** Left of the cursor when it fits, else right, else clamped inside the plot (CSS pixels). */
export function tooltipX(input: TooltipPlacementInput): number {
  const inset = 4;
  const start = input.plotLeft + inset;
  const end = input.plotLeft + input.plotWidth - inset;
  const width = Math.min(input.tooltipWidth, Math.max(40, end - start));
  const left = input.cursorX - width - 12;
  const right = input.cursorX + 12;
  if (left >= start) return left;
  if (right + width <= end) return right;
  return clamp(left, start, Math.max(start, end - width));
}

/** How many lap slots a row's tooltip card can show (rendered row height minus its padding, over the slot height). */
export function tooltipCapacityFor(rowHeight: number, slotHeight: number): number {
  return Math.max(1, Math.floor((rowHeight - 18) / slotHeight));
}

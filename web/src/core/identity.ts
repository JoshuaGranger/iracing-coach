import { readJson, writeJson } from "./store";

/**
 * Car identity: which car, stable per `car_path`. A car takes the next identity colour the first time this device sees it,
 * and the order is kept, so the car keeps its colour across pages and visits. Three colours cycle (`--car-1` to `--car-3`,
 * none of them a race hue).
 */
export const CAR_COLORS = 3;
const KEY = "coach.car-order";
let order: string[] | null = null;

function load(): string[] {
  if (order === null) {
    const stored = readJson<unknown>(KEY, []);
    order = Array.isArray(stored) ? stored.filter((x): x is string => typeof x === "string") : [];
  }
  return order;
}

/** Position of the car in first-seen order, 0-based. */
export function carIndex(carPath: string): number {
  const list = load();
  let i = list.indexOf(carPath);
  if (i < 0) {
    i = list.push(carPath) - 1;
    writeJson(KEY, list);
  }
  return i;
}

/** The identity colour of a car as a CSS value, `var(--car-1)` to `var(--car-3)`. */
export function carColor(carPath: string | null | undefined): string {
  if (!carPath) return "var(--color-text-muted)";
  return `var(--car-${(carIndex(carPath) % CAR_COLORS) + 1})`;
}

/** Test hook: forget the order held in memory. */
export function resetCarOrder(): void {
  order = null;
}

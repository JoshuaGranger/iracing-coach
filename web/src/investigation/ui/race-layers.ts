import { asArray, asRecord, num, type RaceViewData } from "../types";

/**
 * Context layers on the Race chart (round 3, #46; round 4, #60–#62): one mark per lap for what the backend already reports per lap
 * in the race view's laps. A toggle is its name only; the evidence is a short tag in the hover readout (`Track 87.0 °F · measured`).
 * A layer whose figure no lap carries is not offered. Nothing here derives a value: the readers pick the backend's per-lap fields and
 * convert units for display. The traffic tick and the slip layer are gone (#61, #62): the backend reports no per-lap gap ahead or
 * behind and no per-lap slip figure to read.
 */

export type LayerKey = "temp" | "usage" | "fuel";

export interface LayerSpec {
  key: LayerKey;
  label: string;
  evidence: "measured";
}

/** The order of the toggles in the chart header. */
export const LAYERS: readonly LayerSpec[] = [
  { key: "temp", label: "Track temp", evidence: "measured" },
  { key: "usage", label: "Track usage", evidence: "measured" },
  { key: "fuel", label: "Fuel", evidence: "measured" },
];

export interface LapContext {
  lap: number;
  /** `conditions.track_temperature_f`. */
  trackTempF: number | null;
  /** `conditions.track_usage_percent`, kept only inside 0–100 as the backend's track evolution keeps it. */
  usagePct: number | null;
  /** `fuel.start_l` in gallons: fuel in the car at the lap's start. */
  fuelGal: number | null;
}

const LITERS_PER_GALLON = 3.785411784;

/** The backend's per-lap context, by lap number, from the race view's laps. */
export function lapContexts(race: RaceViewData | null | undefined): Map<number, LapContext> {
  const out = new Map<number, LapContext>();
  for (const raw of asArray(race?.laps)) {
    const lap = asRecord(raw);
    const number = num(lap.lap);
    if (number === null || out.has(number)) continue;
    const conditions = asRecord(lap.conditions);
    const usage = num(conditions.track_usage_percent);
    const fuel = num(asRecord(lap.fuel).start_l);
    out.set(number, {
      lap: number,
      trackTempF: num(conditions.track_temperature_f),
      usagePct: usage !== null && usage >= 0 && usage <= 100 ? usage : null,
      fuelGal: fuel !== null && fuel >= 0 ? fuel / LITERS_PER_GALLON : null,
    });
  }
  return out;
}

function value(context: LapContext | undefined, key: LayerKey): number | null {
  if (!context) return null;
  switch (key) {
    case "temp": return context.trackTempF;
    case "usage": return context.usagePct;
    case "fuel": return context.fuelGal;
  }
}

/** Layers with a figure on at least two laps (one lap is not a race context), in toggle order. */
export function availableLayers(contexts: ReadonlyMap<number, LapContext>): LayerSpec[] {
  return LAYERS.filter((layer) => [...contexts.values()].filter((context) => value(context, layer.key) !== null).length >= 2);
}

const NAME: Record<LayerKey, string> = { temp: "Track", usage: "Usage", fuel: "Fuel" };

/** A lap's readout for one layer: `Track 98.4 °F`, `Usage 34 %`, `Fuel 12.3 gal`; `Fuel no reading` when the lap has none. */
export function layerReadout(key: LayerKey, context: LapContext | undefined): string {
  const figure = value(context, key);
  if (figure === null) return `${NAME[key]} no reading`;
  switch (key) {
    case "temp": return `Track ${figure.toFixed(1)} °F`;
    case "usage": return `Usage ${figure.toFixed(0)} %`;
    case "fuel": return `Fuel ${figure.toFixed(1)} gal`;
  }
}

/** Every active layer's readout for a lap, a figure tagged with its evidence: `Track 98.4 °F · measured`, `Fuel no reading`. */
export function layersReadout(active: readonly LayerKey[], context: LapContext | undefined): string[] {
  return LAYERS.filter((layer) => active.includes(layer.key)).map((layer) => {
    const text = layerReadout(layer.key, context);
    return value(context, layer.key) === null ? text : `${text} · ${layer.evidence}`;
  });
}

/** A per-lap series point: the lap and its reading. */
export interface SeriesPoint {
  lap: number;
  value: number;
}

/**
 * The lap series as polylines (#63): one line through every lap with a reading. A line breaks only where a reading is truly absent
 * (a lap with no reading, or a lap missing from the recording); a run change or a pit stop never breaks it.
 */
export function seriesSegments<T extends SeriesPoint>(points: readonly T[]): T[][] {
  const out: T[][] = [];
  for (const point of [...points].sort((a, b) => a.lap - b.lap)) {
    const segment = out[out.length - 1];
    const last = segment?.[segment.length - 1];
    if (segment && last && point.lap === last.lap + 1) segment.push(point);
    else out.push([point]);
  }
  return out;
}

/**
 * A fuel segment's path in pixels (#63): straight between laps while the tank drains; a refill is a step up at the lap boundary
 * (along the previous level, up, along the new level), so a pit stop reads as a step and never as a gap.
 */
export function fuelPath(segment: readonly SeriesPoint[], x: (lap: number) => number, y: (value: number) => number, f: (value: number) => string = String): string {
  let d = "";
  segment.forEach((point, index) => {
    const previous = segment[index - 1];
    if (!previous) {
      d += `M${f(x(point.lap))},${f(y(point.value))}`;
      return;
    }
    if (point.value > previous.value + 1e-9) {
      const edge = x(previous.lap + 0.5);
      d += `L${f(edge)},${f(y(previous.value))}L${f(edge)},${f(y(point.value))}`;
    }
    d += `L${f(x(point.lap))},${f(y(point.value))}`;
  });
  return d;
}

/** The laps of a segment where the tank was refilled (a rise over the lap before): each gets the new level's label. */
export function refills(segment: readonly SeriesPoint[]): SeriesPoint[] {
  return segment.filter((point, index) => index > 0 && point.value > segment[index - 1]!.value + 1e-9);
}

/** The device's remembered layers (off by default). */
export const LAYERS_STORAGE_KEY = "coach.race-layers";

export function parseLayers(text: string | null): LayerKey[] {
  if (!text) return [];
  try {
    const value: unknown = JSON.parse(text);
    const keys = new Set(LAYERS.map((layer) => layer.key));
    return Array.isArray(value) ? [...new Set(value.filter((item): item is LayerKey => typeof item === "string" && keys.has(item as LayerKey)))] : [];
  } catch {
    return [];
  }
}

export function readLayers(storage: Pick<Storage, "getItem"> | null): LayerKey[] {
  try {
    return parseLayers(storage?.getItem(LAYERS_STORAGE_KEY) ?? null);
  } catch {
    return [];
  }
}

export function writeLayers(storage: Pick<Storage, "setItem"> | null, layers: readonly LayerKey[]): void {
  try {
    storage?.setItem(LAYERS_STORAGE_KEY, JSON.stringify(layers));
  } catch {
    // Storage unavailable (private window): the layers last for the visit.
  }
}

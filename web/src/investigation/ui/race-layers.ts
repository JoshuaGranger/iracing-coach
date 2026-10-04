import { asArray, asRecord, num, type RaceViewData } from "../types";

/**
 * Context layers on the Race chart (round 3, #46): one mark per lap for what the backend already reports per lap in the race view's
 * laps, each with its evidence label. A layer whose figure no lap carries is not offered. Nothing here derives a value: the
 * readers pick the backend's per-lap fields and convert units for display.
 */

export type LayerKey = "temp" | "usage" | "traffic" | "slip" | "fuel";

export interface LayerSpec {
  key: LayerKey;
  label: string;
  evidence: "measured" | "derived" | "proxy";
}

/** The order of the toggles in the chart header. */
export const LAYERS: readonly LayerSpec[] = [
  { key: "temp", label: "Track temp", evidence: "measured" },
  { key: "usage", label: "Track usage", evidence: "measured" },
  { key: "traffic", label: "Traffic", evidence: "derived" },
  { key: "slip", label: "Wheel slip", evidence: "proxy" },
  { key: "fuel", label: "Fuel", evidence: "measured" },
];

export interface LapContext {
  lap: number;
  /** `conditions.track_temperature_f`. */
  trackTempF: number | null;
  /** `conditions.track_usage_percent`, kept only inside 0–100 as the backend's track evolution keeps it. */
  usagePct: number | null;
  /** `clean_context.traffic_proximity_fraction`: the share of the lap with a car close ahead or behind (the backend's traffic rule). */
  traffic: number | null;
  /** `vehicle_dynamics.wheel_speed`: rear wheelspin and front lock proxy seconds. */
  slip: { spinS: number; lockS: number } | null;
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
    const traffic = num(asRecord(lap.clean_context).traffic_proximity_fraction);
    const wheel = asRecord(asRecord(lap.vehicle_dynamics).wheel_speed);
    const spin = num(wheel.rear_wheelspin_proxy_s);
    const lock = num(wheel.front_wheel_lock_proxy_s);
    const fuel = num(asRecord(lap.fuel).start_l);
    out.set(number, {
      lap: number,
      trackTempF: num(conditions.track_temperature_f),
      usagePct: usage !== null && usage >= 0 && usage <= 100 ? usage : null,
      traffic: traffic !== null && traffic >= 0 && traffic <= 1 ? traffic : null,
      slip: spin === null && lock === null ? null : { spinS: Math.max(0, spin ?? 0), lockS: Math.max(0, lock ?? 0) },
      fuelGal: fuel !== null && fuel >= 0 ? fuel / LITERS_PER_GALLON : null,
    });
  }
  return out;
}

function value(context: LapContext, key: LayerKey): unknown {
  switch (key) {
    case "temp": return context.trackTempF;
    case "usage": return context.usagePct;
    case "traffic": return context.traffic;
    case "slip": return context.slip;
    case "fuel": return context.fuelGal;
  }
}

/** Layers with a figure on at least two laps (one lap is not a race context), in toggle order. */
export function availableLayers(contexts: ReadonlyMap<number, LapContext>): LayerSpec[] {
  return LAYERS.filter((layer) => [...contexts.values()].filter((context) => value(context, layer.key) !== null).length >= 2);
}

/** A lap's readout for one layer: `Track 98.4 °F`, `Usage 34 %`, `Traffic 51 %`, `Spin 0.4 s · lock 0.1 s`, `Fuel 12.3 gal`. */
export function layerReadout(key: LayerKey, context: LapContext | undefined): string | null {
  if (!context) return null;
  switch (key) {
    case "temp": return context.trackTempF === null ? null : `Track ${context.trackTempF.toFixed(1)} °F`;
    case "usage": return context.usagePct === null ? null : `Usage ${context.usagePct.toFixed(0)} %`;
    case "traffic": return context.traffic === null ? null : `Traffic ${(context.traffic * 100).toFixed(0)} %`;
    case "slip": return context.slip === null ? null : `Spin ${context.slip.spinS.toFixed(1)} s · lock ${context.slip.lockS.toFixed(1)} s`;
    case "fuel": return context.fuelGal === null ? null : `Fuel ${context.fuelGal.toFixed(1)} gal`;
  }
}

/** Every active layer's readout for a lap, each followed by its evidence label: `Traffic 51 % derived`. */
export function layersReadout(active: readonly LayerKey[], context: LapContext | undefined): string[] {
  return LAYERS.filter((layer) => active.includes(layer.key))
    .map((layer) => {
      const text = layerReadout(layer.key, context);
      return text === null ? null : `${text} ${layer.evidence}`;
    })
    .filter((text): text is string => text !== null);
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

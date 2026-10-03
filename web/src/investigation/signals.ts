import type { SteeringBasisData, TraceSignalData } from "./types";

/**
 * Trace signal definitions (AnalysisTraceLayouts.Signals). The catalog's `signals` say which signals a
 * projection carries and their units; this table supplies the friendly names, short labels and groups.
 */
export interface SignalDefinition {
  id: string;
  name: string;
  unit: string;
  group: string;
  shortLabel: string;
}

const defs: [string, string, string, string, string][] = [
  ["speed", "Speed", "mph", "speed", "Speed"],
  ["delta", "Time delta", "s", "time", "Delta"],
  ["throttle", "Throttle", "%", "pedals", "T"],
  ["brake", "Brake", "%", "pedals", "B"],
  ["tire-wear", "Calculated tire wear", "% this lap", "tire", "Wear"],
  ["gear", "Gear", "selected gear", "drivetrain", "Gear"],
  ["rpm", "RPM", "rpm", "drivetrain", "RPM"],
  ["steering", "Road-wheel steering", "deg left / right", "rotation", "Steer"],
  ["steering-wheel-angle", "Steering wheel angle", "deg left / right", "rotation", "Wheel"],
  ["slip", "Slip angle", "deg", "rotation", "Slip"],
  ["yaw", "Yaw rate", "deg/s", "rotation", "Yaw"],
  ["lateral-g", "Lateral G", "g", "acceleration", "Lat"],
  ["longitudinal-g", "Longitudinal G", "g", "acceleration", "Long"],
  ["vertical-g", "Vertical G", "g", "acceleration", "Vert"],
  ["clutch", "Clutch", "%", "pedals", "C"],
  ["abs-active", "ABS active", "on / off", "pedals", "ABS"],
  ["abs-cut", "ABS intervention", "%", "pedals", "ABS cut"],
  ["brake-bias", "Brake bias", "%", "pedals", "Bias"],
  ["steering-torque", "Steering torque", "Nm", "rotation", "Torque"],
  ["pitch", "Pitch", "deg", "rotation", "Pitch"],
  ["roll", "Roll", "deg", "rotation", "Roll"],
  ["pitch-rate", "Pitch rate", "deg/s", "rotation", "Pitch"],
  ["roll-rate", "Roll rate", "deg/s", "rotation", "Roll"],
  ["lf-wheel-speed", "LF wheel speed", "mph", "tire", "LF"],
  ["rf-wheel-speed", "RF wheel speed", "mph", "tire", "RF"],
  ["lr-wheel-speed", "LR wheel speed", "mph", "tire", "LR"],
  ["rr-wheel-speed", "RR wheel speed", "mph", "tire", "RR"],
  ["lf-wheel-slip", "LF wheel slip", "%", "tire", "LF slip"],
  ["rf-wheel-slip", "RF wheel slip", "%", "tire", "RF slip"],
  ["lr-wheel-slip", "LR wheel slip", "%", "tire", "LR slip"],
  ["rr-wheel-slip", "RR wheel slip", "%", "tire", "RR slip"],
  ["lf-pressure", "LF pressure", "psi", "tire", "LF psi"],
  ["rf-pressure", "RF pressure", "psi", "tire", "RF psi"],
  ["lr-pressure", "LR pressure", "psi", "tire", "LR psi"],
  ["rr-pressure", "RR pressure", "psi", "tire", "RR psi"],
  ["lf-carcass-temp", "LF carcass temp", "deg F", "tire", "LF temp"],
  ["rf-carcass-temp", "RF carcass temp", "deg F", "tire", "RF temp"],
  ["lr-carcass-temp", "LR carcass temp", "deg F", "tire", "LR temp"],
  ["rr-carcass-temp", "RR carcass temp", "deg F", "tire", "RR temp"],
  ["lf-surface-temp", "LF surface temp", "deg F", "tire", "LF surf"],
  ["rf-surface-temp", "RF surface temp", "deg F", "tire", "RF surf"],
  ["lr-surface-temp", "LR surface temp", "deg F", "tire", "LR surf"],
  ["rr-surface-temp", "RR surface temp", "deg F", "tire", "RR surf"],
  ["fuel-level", "Fuel level", "gal", "fuel", "Fuel"],
  ["fuel-use-rate", "Fuel use rate", "kg/h", "fuel", "Fuel rate"],
  ["center-front-ride-height", "Front ride height", "in", "chassis", "Front RH"],
  ["lf-ride-height", "LF ride height", "in", "chassis", "LF RH"],
  ["rf-ride-height", "RF ride height", "in", "chassis", "RF RH"],
  ["lr-ride-height", "LR ride height", "in", "chassis", "LR RH"],
  ["rr-ride-height", "RR ride height", "in", "chassis", "RR RH"],
  ["lf-shock-deflection", "LF shock deflection", "in", "chassis", "LF shock"],
  ["rf-shock-deflection", "RF shock deflection", "in", "chassis", "RF shock"],
  ["lr-shock-deflection", "LR shock deflection", "in", "chassis", "LR shock"],
  ["rr-shock-deflection", "RR shock deflection", "in", "chassis", "RR shock"],
  ["lf-shock-velocity", "LF shock velocity", "in/s", "chassis", "LF vel"],
  ["rf-shock-velocity", "RF shock velocity", "in/s", "chassis", "RF vel"],
  ["lr-shock-velocity", "LR shock velocity", "in/s", "chassis", "LR vel"],
  ["rr-shock-velocity", "RR shock velocity", "in/s", "chassis", "RR vel"],
  ["track-temperature", "Track temperature", "deg F", "conditions", "Track"],
  ["air-temperature", "Air temperature", "deg F", "conditions", "Air"],
  ["wind-speed", "Wind speed", "mph", "conditions", "Wind"],
  ["humidity", "Humidity", "%", "conditions", "Humidity"],
  ["fog", "Fog", "%", "conditions", "Fog"],
  ["precipitation", "Precipitation", "%", "conditions", "Rain"],
  ["air-pressure", "Air pressure", "inHg", "conditions", "Pressure"],
  ["air-density", "Air density", "lb/ft3", "conditions", "Density"],
  ["track-wetness", "Track wetness", "state", "conditions", "Wetness"],
  ["track-usage", "Track usage", "%", "conditions", "Usage"],
  ["weather-wet", "Wet weather", "on / off", "conditions", "Wet"],
  ["overall-position", "Overall position", "position", "race", "Pos"],
  ["class-position", "Class position", "position", "race", "Class"],
  ["distance-ahead", "Distance ahead", "ft", "race", "Ahead"],
  ["distance-behind", "Distance behind", "ft", "race", "Behind"],
  ["on-pit-road", "On pit road", "on / off", "race", "Pit"],
  ["track-surface", "Track surface", "state", "race", "Surface"],
];

export const SIGNAL_DEFINITIONS: readonly SignalDefinition[] = defs.map(([id, name, unit, group, shortLabel]) => ({ id, name, unit, group, shortLabel }));
const byId = new Map(SIGNAL_DEFINITIONS.map((definition) => [definition.id, definition] as const));

export function signalDefinition(id: string): SignalDefinition | undefined {
  return byId.get(id);
}

/** Steering is named for the analysis's basis (C# AnalysisSteeringBasis). */
export interface SteeringDeclaration {
  roadWheel: boolean;
  label: string;
  /** Decimals for steering degrees: road-wheel angles are a twelfth of the wheel angle, so they read finer. */
  degreeDigits: number;
  ratio: number | null;
}

export const ROAD_WHEEL_LABEL = "Road-wheel steering";
export const STEERING_WHEEL_LABEL = "Steering wheel angle";

export function steeringDeclaration(basis: SteeringBasisData | null | undefined): SteeringDeclaration {
  const ratio = typeof basis?.steering_ratio === "number" && Number.isFinite(basis.steering_ratio) ? basis.steering_ratio : null;
  const roadWheel = basis?.basis === "road_wheel" && ratio !== null && ratio > 0;
  const label = typeof (basis as Record<string, unknown> | null | undefined)?.label === "string" && !roadWheel
    ? String((basis as Record<string, unknown>).label)
    : roadWheel ? ROAD_WHEEL_LABEL : STEERING_WHEEL_LABEL;
  const digits = typeof basis?.digits === "number" && Number.isInteger(basis.digits) ? basis.digits : roadWheel ? 2 : 1;
  return { roadWheel, label, degreeDigits: digits, ratio };
}

/** A steering-wheel threshold in degrees converted to the declared basis (2 deg -> 2/ratio deg road wheel). */
export function steeringThreshold(declaration: SteeringDeclaration, wheelDegrees: number): number {
  return declaration.roadWheel && declaration.ratio ? wheelDegrees / declaration.ratio : wheelDegrees;
}

/** A resolved signal: the static definition, overridden by the projection's catalog row when it has one. */
export interface ResolvedSignal {
  id: string;
  name: string;
  shortLabel: string;
  unit: string;
  group: string;
  /** Catalog source key (backend point key), when the projection carries the signal. */
  sourceKey: string | null;
  digits: number | null;
  /** The value is a physical quantity in radians that the display converts to degrees. */
  radians: boolean;
  step: boolean;
  available: boolean;
}

const STEP_IDS = new Set(["gear", "tire-wear", "overall-position", "class-position", "track-wetness", "track-surface"]);
const STEP_UNITS = new Set(["on / off", "state", "position"]);

export function isStepSignal(id: string, unit: string): boolean {
  return STEP_IDS.has(id) || STEP_UNITS.has(unit);
}

export function resolveSignal(id: string, catalog: ReadonlyMap<string, TraceSignalData>, steering: SteeringDeclaration): ResolvedSignal | null {
  const definition = signalDefinition(id);
  const row = catalog.get(id);
  if (!definition && !row) return null;
  const isSteering = id === "steering" || id === "steering-wheel-angle";
  const wheelLabel = id === "steering" && !steering.roadWheel;
  const name = wheelLabel ? steering.label : (definition?.name ?? row?.label ?? id);
  const unit = definition?.unit ?? row?.unit ?? "";
  const sourceUnit = row?.unit ?? "";
  return {
    id,
    name,
    shortLabel: wheelLabel ? "Wheel" : (definition?.shortLabel ?? row?.short_label ?? name),
    unit: row ? (isSteering ? unit : unit || sourceUnit) : unit,
    group: definition?.group ?? row?.category ?? "other",
    sourceKey: row?.source_key ?? null,
    digits: id === "steering" ? steering.degreeDigits : id === "steering-wheel-angle" ? 1 : (row?.digits ?? null),
    radians: isSteering && /rad/i.test(sourceUnit) && !/deg/i.test(sourceUnit),
    step: isStepSignal(id, unit),
    available: row !== undefined || id === "delta",
  };
}

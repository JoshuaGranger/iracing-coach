import { CHANNELS, CHANNEL_INDEX, CUES, FlagState, RunPhase, TILES, type TileSpec, conversion, unitLabel } from "../gen/live-channels";
import { FrameFlag, type LiveFrame } from "./frame";
import { ceiling, custom, customOptional, fixed, lapTime, percent0, signed, whole } from "./text";

/**
 * What a tile shows for one frame: the browser twin of the overlay's `format_tile` (crate-api.md 4.7).
 * Tables come from the generated live-channels.json; text rules live in `text.ts`.
 */
export type TileState = "ok" | "waiting" | "missing" | "insufficient";
export type TileTone = "neutral" | "good" | "warn" | "bad" | "info";

export interface TileText {
  /** The formatted value, then a space and the unit's display label when the tile has units ("12.4 US gal", "20 laps"). */
  primary: string;
  /** `primary` without the unit label, for a layout that sets the unit apart. */
  amount: string;
  /** Display label of the unit (`unitLabel`), "" when the tile shows none. */
  unit: string;
  secondary: string | null;
  /** Numeric value in the display unit, for gauges, bars and trends; null when none. */
  value: number | null;
  state: TileState;
  tone: TileTone;
}

export interface TileDisplay {
  /** A unit id from the tile's `units`; anything else selects the default (first). */
  unit?: string | null;
  precision?: number | null;
}

export const TILE_BY_ID: ReadonlyMap<string, TileSpec> = new Map(TILES.map((t) => [t.id, t]));
const CHANNEL_UNIT: Map<string, string | null> = new Map();
for (const c of CHANNELS) CHANNEL_UNIT.set(c.id, c.unit);

/** Tiles whose decimals the overlay fixes at 0 (`LiveTelemetryCatalog.Read`); a layout's precision does not apply to them. */
const WHOLE_NUMBER_TILES: ReadonlySet<string> = new Set(["gear", "lap", "laps-remaining", "mandatory-repair", "optional-repair", "rpm"]);
const NO_PRECISION_TILES: ReadonlySet<string> = new Set([
  ...WHOLE_NUMBER_TILES,
  "class-position",
  "coach-cue",
  "flag",
  "last-lap",
  "leader-last-lap",
  "on-pit-road",
  "pace-range",
  "pit-window",
  "position",
  "tire-phase",
]);

/** Whether a layout's Decimals setting changes this tile. */
export function usesPrecision(spec: TileSpec): boolean {
  return !NO_PRECISION_TILES.has(spec.id);
}

const FLAG_TEXT: Record<number, string> = {
  [FlagState.Unknown]: "UNKNOWN",
  [FlagState.Racing]: "RACING",
  [FlagState.Green]: "GREEN",
  [FlagState.White]: "WHITE",
  [FlagState.Checkered]: "CHECKERED",
  [FlagState.Caution]: "CAUTION",
  [FlagState.Red]: "RED",
  [FlagState.Black]: "BLACK FLAG",
  [FlagState.Disqualified]: "DISQUALIFIED",
};

/** C# flag step ordinal: Green 0, Blue 1, White 2, Checkered 3, Yellow 4, Black 5, Red 6, Other 7. */
const FLAG_STEP: Record<number, number> = {
  [FlagState.Racing]: 0,
  [FlagState.Green]: 0,
  [FlagState.White]: 2,
  [FlagState.Checkered]: 3,
  [FlagState.Caution]: 4,
  [FlagState.Black]: 5,
  [FlagState.Red]: 6,
};

const FLAG_TONE: Record<number, TileTone> = {
  [FlagState.Green]: "good",
  [FlagState.Racing]: "good",
  [FlagState.Caution]: "warn",
  [FlagState.Red]: "bad",
  [FlagState.Black]: "bad",
  [FlagState.Disqualified]: "bad",
};

const PHASE_TEXT: Record<number, string> = {
  [RunPhase.Early]: "Early run",
  [RunPhase.Middle]: "Middle run",
  [RunPhase.Late]: "Late run",
};

export function channelValue(frame: LiveFrame, id: string): number | null {
  const i = CHANNEL_INDEX[id];
  return i === undefined ? null : frame.values[i] ?? null;
}

/** Units a tile may show, default first (empty when the tile has none). */
export function tileUnits(spec: TileSpec): readonly string[] {
  return spec.units;
}

/** The unit a tile shows: the requested unit id when the tile lists it, else the default (first), else none. */
export function resolveUnit(spec: TileSpec, requested: string | null | undefined): string {
  return requested && spec.units.includes(requested) ? requested : (spec.units[0] ?? "");
}

const clampPrecision = (p: number) => Math.min(3, Math.max(0, Math.trunc(p)));

/** Display-unit converter for a channel (identity when the table has no conversion). */
export function unitConverter(channel: string, unit: string | null | undefined): { fn: (v: number) => number; unit: string } {
  const from = CHANNEL_UNIT.get(channel) ?? "";
  const target = unit ?? from;
  const c = from ? conversion(from, target) : null;
  return c ? { fn: (v) => v * c.scale + c.offset, unit: target } : { fn: (v) => v, unit: from };
}

function convert(value: number, from: string, to: string): number {
  const c = from && to ? conversion(from, to) : null;
  return c ? value * c.scale + c.offset : value;
}

const make = (primary: string, extra: Partial<TileText> = {}): TileText => ({
  primary,
  amount: primary,
  unit: "",
  secondary: null,
  value: null,
  state: "ok",
  tone: "neutral",
  ...extra,
});

/** A formatted number, then its unit label after one space. */
function withUnit(amount: string, unit: string, extra: Partial<TileText> = {}): TileText {
  const label = unit ? unitLabel(unit) : "";
  return make(label ? `${amount} ${label}` : amount, { amount, unit: label, ...extra });
}

const waiting = () => make("Waiting", { state: "waiting" });
const notMeasured = () => make("Not measured", { state: "missing" });
const insufficient = () => make("Insufficient evidence", { state: "insufficient" });

export function cueText(id: number, args: readonly number[]): { text: string; basis: string | null } | null {
  const cue = CUES.find((c) => c.id === id);
  if (!cue) return null;
  const rendered = cue.template.replace(/\{(\d)\}/g, (_m, d: string) => {
    const k = Number(d);
    const v = args[k];
    if (v === undefined) return "";
    switch (cue.args[k]) {
      case "ceil":
        return ceiling(v);
      case "fixed2":
        return custom(v, 2);
      case "percent0":
        return percent0(v);
      default:
        return whole(v);
    }
  });
  return { text: rendered, basis: cue.basis };
}

export function formatTile(spec: TileSpec, display: TileDisplay, frame: LiveFrame | null): TileText {
  if (!frame || (frame.flags & FrameFlag.SimConnected) === 0) return waiting();
  const ch = (id: string) => channelValue(frame, id);
  const unit = resolveUnit(spec, display.unit);
  const precision = clampPrecision(display.precision ?? spec.precision);
  const first = spec.channels[0]!;

  /** `Number<T>`: the channel converted to the display unit and formatted `F{decimals}`. */
  const number = (channel: string, decimals: number, from = CHANNEL_UNIT.get(channel) ?? "", to = unit): TileText => {
    const raw = ch(channel);
    if (raw === null) return notMeasured();
    const converted = convert(raw, from, to);
    return withUnit(fixed(converted, decimals), to, { value: converted });
  };
  const position = (channel: string): TileText => {
    const v = ch(channel);
    return v === null || v <= 0 ? notMeasured() : make(`P${v}`, { value: v });
  };

  switch (spec.id) {
    case "air-temperature":
    case "track-temperature":
    case "speed":
    case "lateral-acceleration":
    case "longitudinal-acceleration":
    case "yaw-rate":
    case "steering-wheel-angle":
    case "fuel-laps":
    case "ahead-gap":
    case "behind-gap":
    case "brake-bias":
      return number(first, precision);
    case "brake":
    case "throttle":
      return number(first, precision, "ratio", "%");
    case "mandatory-repair": {
      const t = number(first, 0);
      return t.value !== null && t.value > 0 ? { ...t, tone: "bad" } : t;
    }
    case "gear":
    case "lap":
    case "laps-remaining":
    case "optional-repair":
    case "rpm":
      return number(first, 0);
    case "class-position":
    case "position":
      return position(first);
    case "leader-last-lap": {
      const v = ch(first);
      return v === null || v <= 0 ? notMeasured() : make(lapTime(v), { value: v });
    }
    case "leader-gap":
      return ch("position") === 1 ? make("Leader", { value: 0 }) : number(first, precision);
    case "last-lap": {
      const v = ch(first);
      if (v === null) return notMeasured();
      const delta = ch("pace_delta_to_leader");
      return make(lapTime(v), { value: v, secondary: delta !== null ? `${signed(delta, 3)} s vs leader last lap` : "Leader comparison not measured" });
    }
    case "coach-cue": {
      if (!frame.cue) return notMeasured();
      const c = cueText(frame.cue.id, frame.cue.args);
      return make(c?.text ?? "", { secondary: c?.basis ?? null, value: frame.cue.priority, tone: frame.cue.priority >= 100 ? "bad" : frame.cue.priority >= 50 ? "warn" : "neutral" });
    }
    case "flag": {
      const f = ch(first);
      if (f === null) return notMeasured();
      return make(FLAG_TEXT[f] ?? "UNKNOWN", {
        value: FLAG_STEP[f] ?? 7,
        tone: FLAG_TONE[f] ?? "neutral",
        secondary: f === FlagState.Racing ? "Recorded session state" : "iRacing race status",
      });
    }
    case "on-pit-road": {
      const v = ch(first);
      return v === null ? notMeasured() : make(v ? "On pit road" : "On track", { value: v ? 1 : 0, tone: v ? "warn" : "neutral", secondary: "Recorded OnPitRoad state" });
    }
    case "tire-phase": {
      const v = ch(first);
      const label = v === null ? undefined : PHASE_TEXT[v];
      return v === null || !label ? insufficient() : make(label, { value: v - 1, secondary: "Observed clean laps since pit road" });
    }
    case "fuel": {
      const liters = ch(first);
      if (liters === null) return notMeasured();
      const value = convert(liters, "L", unit);
      const laps = ch("fuel_laps_remaining");
      return withUnit(fixed(value, precision), unit, { value, secondary: laps !== null ? `${custom(laps, 1)} estimated laps` : "Lap estimate needs clean fuel-use evidence" });
    }
    case "steering": {
      const road = ch(first);
      if (road === null) return ch("steering_wheel_angle") === null ? notMeasured() : make("Ratio not recorded", { state: "missing" });
      const converted = convert(road, "rad", unit);
      const ratio = ch("steering_ratio");
      return withUnit(fixed(converted, precision), unit, { value: converted, secondary: ratio !== null ? `${customOptional(ratio, 2)}:1 steering ratio` : null });
    }
    case "pace-range": {
      const lo = ch("pace_band_min");
      const hi = ch("pace_band_max");
      if (lo === null || hi === null) return insufficient();
      const phase = ch("run_phase");
      return make(`${lapTime(lo)}–${lapTime(hi)}`, { value: (lo + hi) / 2, secondary: phase === null ? null : (PHASE_TEXT[phase] ?? null) });
    }
    case "pit-window": {
      const laps = ch(first);
      return laps === null ? insufficient() : withUnit(String(laps), "lap", { value: laps, secondary: "Fuel hard limit; strategic window not established" });
    }
    default:
      return notMeasured();
  }
}

/** Semantic range of a tile for gauges and bars: the table's min/max, widened to the fuel capacity for the fuel bar. */
export function tileRange(spec: TileSpec, frame: LiveFrame | null, display: TileDisplay): { min: number | null; max: number | null } {
  if (spec.id === "fuel" && frame) {
    const fuel = channelValue(frame, "fuel");
    const pct = channelValue(frame, "fuel_pct");
    if (fuel !== null && pct !== null && pct > 0.0001) return { min: 0, max: convert(fuel, "L", resolveUnit(spec, display.unit)) / pct };
  }
  return { min: spec.min, max: spec.max };
}

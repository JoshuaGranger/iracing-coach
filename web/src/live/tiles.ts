import { formatLapTime } from "../core/format";
import { CHANNELS, CHANNEL_INDEX, CUES, FlagState, RunPhase, TILES, type TileSpec, conversion } from "../gen/live-channels";
import { FrameFlag, type LiveFrame } from "./frame";

/** What a tile shows for one frame. Display formatting comes from the generated tile table (live-channels.json). */
export type TileState = "ok" | "waiting" | "missing" | "insufficient";
export type TileTone = "neutral" | "good" | "warn" | "bad" | "info";

export interface TileText {
  primary: string;
  secondary: string | null;
  /** Numeric value in the display unit, for gauges, bars and trends; null when none. */
  value: number | null;
  unit: string;
  state: TileState;
  tone: TileTone;
}

export interface TileDisplay {
  unit?: string | null;
  precision?: number | null;
}

export const TILE_BY_ID: ReadonlyMap<string, TileSpec> = new Map(TILES.map((t) => [t.id, t]));
const CHANNEL_UNIT: Map<string, string | null> = new Map();
for (const c of CHANNELS) CHANNEL_UNIT.set(c.id, c.unit);

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

/** Units a tile may show, default first (the channel's own unit when the tile lists none). */
export function tileUnits(spec: TileSpec): readonly string[] {
  return spec.units;
}

const clampPrecision = (p: number) => Math.min(3, Math.max(0, Math.trunc(p)));

/** Display-unit converter for a channel (identity when the table has no conversion). */
export function unitConverter(channel: string, unit: string | null | undefined): { fn: (v: number) => number; unit: string } {
  const from = CHANNEL_UNIT.get(channel) ?? "";
  const target = unit ?? from;
  const c = from ? conversion(from, target) : null;
  return c ? { fn: (v) => v * c.scale + c.offset, unit: target } : { fn: (v) => v, unit: from };
}

function convert(value: number, channel: string, unit: string): { value: number; unit: string } {
  const from = CHANNEL_UNIT.get(channel) ?? "";
  const c = from ? conversion(from, unit) : null;
  return c ? { value: value * c.scale + c.offset, unit } : { value, unit: from || unit };
}

const text = (primary: string, extra: Partial<TileText> = {}): TileText => ({
  primary,
  secondary: null,
  value: null,
  unit: "",
  state: "ok",
  tone: "neutral",
  ...extra,
});

export function cueText(id: number, args: readonly number[]): { text: string; basis: string | null } | null {
  const cue = CUES.find((c) => c.id === id);
  if (!cue) return null;
  const rendered = cue.template.replace(/\{(\d)\}/g, (_m, d: string) => {
    const k = Number(d);
    const v = args[k];
    if (v === undefined) return "";
    switch (cue.args[k]) {
      case "ceil":
        return String(Math.ceil(v));
      case "fixed2":
        return v.toFixed(2);
      case "percent0":
        return `${Math.round(v * 100)}%`;
      default:
        return String(Math.round(v));
    }
  });
  return { text: rendered, basis: cue.basis };
}

function gearText(g: number): string {
  return g < 0 ? "R" : g === 0 ? "N" : String(g);
}

export function formatTile(spec: TileSpec, display: TileDisplay, frame: LiveFrame | null): TileText {
  if (!frame || (frame.flags & FrameFlag.SimConnected) === 0) return text("Waiting", { state: "waiting" });
  const missing = (s = "Not measured") => text(s, { state: "missing" });
  const ch = (id: string) => channelValue(frame, id);

  switch (spec.id) {
    case "coach-cue": {
      if (!frame.cue) return missing("No cue");
      const c = cueText(frame.cue.id, frame.cue.args);
      return c ? text(c.text, { secondary: c.basis, value: frame.cue.priority, tone: frame.cue.priority >= 100 ? "bad" : frame.cue.priority >= 50 ? "warn" : "neutral" }) : missing("No cue");
    }
    case "flag": {
      const f = ch("flag_state");
      if (f === null) return missing();
      return text(FLAG_TEXT[f] ?? "UNKNOWN", { value: FLAG_STEP[f] ?? 7, tone: FLAG_TONE[f] ?? "neutral" });
    }
    case "on-pit-road": {
      const v = ch("on_pit_road");
      return v === null ? missing() : text(v ? "On pit road" : "On track", { value: v ? 1 : 0, tone: v ? "warn" : "neutral" });
    }
    case "tire-phase": {
      const v = ch("run_phase");
      return v === null || v === RunPhase.Unknown ? missing() : text(PHASE_TEXT[v] ?? "Not measured", { value: v - 1 });
    }
    case "steering": {
      const v = ch("steering_road_wheel");
      if (v === null) return text("Ratio not recorded", { state: "missing" });
      const unit = display.unit ?? spec.units[0] ?? "deg";
      const out = convert(v, "steering_road_wheel", unit);
      const ratio = ch("steering_ratio");
      return text(out.value.toFixed(clampPrecision(display.precision ?? spec.precision)), {
        unit: out.unit,
        value: out.value,
        secondary: ratio !== null ? `${Number(ratio.toFixed(2))}:1 steering ratio` : null,
      });
    }
    case "pace-range": {
      const lo = ch("pace_band_min");
      const hi = ch("pace_band_max");
      if (lo === null || hi === null) return text("Insufficient evidence", { state: "insufficient" });
      return text(`${formatLapTime(lo)}-${formatLapTime(hi)}`, { value: (lo + hi) / 2, unit: "s" });
    }
    case "leader-gap": {
      const v = ch("leader_gap");
      if (v === null) return missing();
      return v === 0 ? text("Leader", { value: 0, unit: "s" }) : text(v.toFixed(clampPrecision(display.precision ?? spec.precision)), { value: v, unit: "s" });
    }
    default:
  }

  const channel = spec.channels[0];
  if (!channel) return missing();
  const raw = ch(channel);
  if (raw === null) return missing();
  const precision = clampPrecision(display.precision ?? spec.precision);

  if (spec.format === "laptime") {
    const delta = spec.id === "last-lap" ? ch("pace_delta_to_leader") : null;
    return text(formatLapTime(raw), { value: raw, unit: "s", secondary: delta !== null ? `${delta > 0 ? "+" : ""}${delta.toFixed(3)} s vs leader last lap` : null });
  }
  if (spec.format === "P{n}") return text(`P${Math.round(raw)}`, { value: raw });
  if (spec.id === "gear") return text(gearText(raw), { value: raw });

  const unit = display.unit ?? spec.units[0] ?? CHANNEL_UNIT.get(channel) ?? "";
  const out = convert(raw, channel, unit);
  const secondary =
    spec.id === "fuel"
      ? (() => {
          const laps = ch("fuel_laps_remaining");
          return laps !== null ? `${laps.toFixed(1)} estimated laps` : "Lap estimate needs clean fuel-use evidence";
        })()
      : null;
  let tone: TileTone = "neutral";
  if (spec.id === "mandatory-repair" && raw > 0) tone = "bad";
  return text(out.value.toFixed(precision), { value: out.value, unit: out.unit === "lap" ? "" : out.unit, secondary, tone });
}

/** Semantic range of a tile for gauges and bars: the table's min/max, widened to the fuel capacity for the fuel bar. */
export function tileRange(spec: TileSpec, frame: LiveFrame | null, display: TileDisplay): { min: number | null; max: number | null } {
  if (spec.id === "fuel" && frame) {
    const fuel = channelValue(frame, "fuel");
    const pct = channelValue(frame, "fuel_pct");
    if (fuel !== null && pct !== null && pct > 0.0001) {
      const unit = display.unit ?? spec.units[0] ?? "L";
      return { min: 0, max: convert(fuel / pct, "fuel", unit).value };
    }
  }
  return { min: spec.min, max: spec.max };
}

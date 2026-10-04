import type { TileSpec } from "../gen/live-channels";
import type { LiveFrame } from "./frame";
import type { LiveTileCfg, TileStyle } from "./layout";
import { ACCENTS } from "./layout";
import { channelValue } from "./tiles";

/**
 * What the style strip offers for one metric (web-round-2-plan.md, Live editor): Text; Trend; Bar for bounded metrics; Gauge for
 * fuel where the capacity is known; Position is Text only; fuel laps defaults to Text. The stored `style` keeps the contract enum:
 * Text is `number`, or `status` for the four status metrics. Validation (`validateLayout`) stays the server's wider rule, so a layout
 * written elsewhere still loads and saves.
 */
export type Representation = "text" | "trend" | "bar" | "gauge";

export const REPRESENTATION_LABEL: Record<Representation, string> = { text: "Text", trend: "Trend", bar: "Bar", gauge: "Gauge" };

/** Metrics that only read as text: positions, lap times, counts, states and the cue. */
const TEXT_ONLY: ReadonlySet<string> = new Set([
  "position",
  "class-position",
  "lap",
  "laps-remaining",
  "last-lap",
  "leader-last-lap",
  "pace-range",
  "pit-window",
  "mandatory-repair",
  "optional-repair",
]);

/** Tiles whose colour is a state (flag, pit road, cue priority, a mandatory repair): the colour choice is not offered. */
const STATE_COLOURED: ReadonlySet<string> = new Set(["flag", "on-pit-road", "coach-cue", "mandatory-repair", "tire-phase"]);

export interface RepresentationContext {
  /** The fuel capacity is known (the stream carried fuel and a fuel fraction). */
  fuelCapacityKnown: boolean;
}

export function representationOf(style: TileStyle): Representation {
  return style === "number" || style === "status" ? "text" : style;
}

export function styleFor(rep: Representation, spec: TileSpec): TileStyle {
  if (rep === "text") return spec.style === "status" ? "status" : "number";
  return rep;
}

const textOnly = (spec: TileSpec) => spec.style === "status" || TEXT_ONLY.has(spec.id);

/** A full scale the sim defines: both bounds known, continuous. */
export function isBounded(spec: TileSpec, ctx: RepresentationContext): boolean {
  if (spec.id === "fuel") return ctx.fuelCapacityKnown;
  return !textOnly(spec) && !spec.step && spec.min !== null && spec.max !== null;
}

/** The representations offered for a metric, in strip order. */
export function representations(spec: TileSpec, ctx: RepresentationContext): Representation[] {
  if (textOnly(spec)) return ["text"];
  const out: Representation[] = ["text", "trend"];
  if (isBounded(spec, ctx)) out.push("bar");
  if (spec.id === "fuel" && ctx.fuelCapacityKnown) out.push("gauge");
  return out;
}

/** The style a new tile starts with: the table's default where the strip offers it (a bar for fuel), else Text. */
export function defaultTileStyle(spec: TileSpec): TileStyle {
  if (spec.id === "fuel-laps" || textOnly(spec)) return styleFor("text", spec);
  if (spec.id === "fuel") return "bar";
  const table = spec.style as TileStyle;
  if (table === "trend") return "trend";
  if (table === "bar" || table === "gauge") return isBounded(spec, { fuelCapacityKnown: false }) ? "bar" : "number";
  return "number";
}

export type Accent = (typeof ACCENTS)[number];
export const ACCENT_LABEL: Record<Accent, string> = { default: "Default", blue: "Blue", green: "Green", amber: "Amber", coral: "Coral", violet: "Violet" };

/** Colour choices for a metric; none where the colour already carries a state, so a choice can never hide caution or a warning. */
export function colourChoices(spec: TileSpec): readonly Accent[] {
  return STATE_COLOURED.has(spec.id) ? [] : ACCENTS;
}

/** The patch that switches a tile to a representation: the window stays; Mark ABS belongs to a trend and goes without one. */
export function representationPatch(cfg: LiveTileCfg, rep: Representation, spec: TileSpec): Partial<LiveTileCfg> {
  return { style: styleFor(rep, spec), highlight_abs: rep === "trend" ? (cfg.highlight_abs ?? false) : false };
}

/** The fuel capacity follows from fuel and its fraction of a full tank; known once both arrived. */
export function fuelCapacityKnown(frame: LiveFrame | null): boolean {
  if (!frame) return false;
  const fuel = channelValue(frame, "fuel");
  const pct = channelValue(frame, "fuel_pct");
  return fuel !== null && pct !== null && pct > 0.0001;
}

/**
 * The tile's shape while it waits: its format with dashes in place of digits, so each tile shows what it will hold
 * ("P–", "–:––.–––", "–.–– s"). The unit is the tile's display label.
 */
export function waitingAmount(spec: TileSpec, precision: number | null | undefined): string {
  switch (spec.format) {
    case "P{n}":
      return "P–";
    case "laptime":
      return "–:––.–––";
    case "laptime-range":
      return "–:––.–––";
  }
  if (spec.style === "status") return "—";
  const p = Math.min(3, Math.max(0, Math.trunc(precision ?? spec.precision)));
  return p > 0 ? `–.${"–".repeat(p)}` : "–";
}

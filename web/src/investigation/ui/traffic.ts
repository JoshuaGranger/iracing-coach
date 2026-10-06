import type { InvestigationLap } from "../investigation-model";
import { asArray, asRecord, num, str, type RaceViewData } from "../types";

/**
 * Traffic laps on the Race and Runs charts (backlog #67). The backend screens a lap for close traffic when a car was ahead within
 * max(25 m, 0.75 s) or behind within max(15 m, 0.40 s) for at least a tenth of the lap, and says so with the reason `close_traffic`.
 * A lap with that reason alone is a traffic lap and is drawn as one; a lap screened for anything else (caution, pit, incident, off
 * track, restart, partial, with traffic or without) keeps the hollow dot; every other lap is open air. The browser reads these facts
 * and draws them; it never re-derives the rule.
 *
 * `clean_context.traffic_screened` only says the recording carried the car-distance channels, so it is true on open-air laps too and
 * cannot pick a traffic lap out: the reason list does.
 */

/** The backend's code for a lap with a car close ahead or behind for at least a tenth of it. */
export const TRAFFIC_CODE = "close_traffic";

/** open: in the pace fit; traffic: screened for close traffic alone; screened: screened for anything else. */
export type LapKind = "open" | "traffic" | "screened";

export function lapKind(lap: Pick<InvestigationLap, "eligible" | "exclusionReasons">): LapKind {
  if (lap.eligible) return "open";
  const codes = new Set(lap.exclusionReasons);
  return codes.size === 1 && codes.has(TRAFFIC_CODE) ? "traffic" : "screened";
}

export type TrafficSide = "ahead" | "behind" | "both";

export interface LapTraffic {
  /** `clean_context.traffic_proximity_fraction`, 0 to 1: the share of the lap's observed time a car was inside the rule's distance. */
  fraction: number | null;
  /**
   * The side the car was on: `clean_context.traffic_side` (`ahead`, `behind` or `both`, from the backend's per-side fractions);
   * null when the backend does not say.
   */
  side: TrafficSide | null;
}

/** The backend's per-lap traffic facts by lap number, from the race view's laps. A figure outside 0 to 1 is not a figure. */
export function lapTraffic(race: RaceViewData | null | undefined): Map<number, LapTraffic> {
  const out = new Map<number, LapTraffic>();
  for (const raw of asArray(race?.laps)) {
    const lap = asRecord(raw);
    const number = num(lap.lap);
    if (number === null || out.has(number)) continue;
    const clean = asRecord(lap.clean_context);
    const fraction = num(clean.traffic_proximity_fraction);
    const side = str(clean.traffic_side);
    out.set(number, {
      fraction: fraction !== null && fraction >= 0 && fraction <= 1 ? fraction : null,
      side: side === "ahead" || side === "behind" || side === "both" ? side : null,
    });
  }
  return out;
}

/** The side in words, as every traffic readout says it: `ahead`, `behind`, `both sides`. */
export function sideText(side: TrafficSide): string {
  return side === "both" ? "both sides" : side;
}

/** A traffic lap's readout: `traffic 75 % of lap`, with ` · ahead`, ` · behind` or ` · both sides` when the backend names the side. */
export function trafficText(traffic: LapTraffic | null | undefined): string {
  const share = traffic?.fraction != null ? `traffic ${Math.round(traffic.fraction * 100)} % of lap` : "traffic";
  if (!traffic?.side) return share;
  return `${share} · ${sideText(traffic.side)}`;
}

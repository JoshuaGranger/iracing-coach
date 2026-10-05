import { asArray, asRecord, num, str } from "../types";

/**
 * `InvestigationView.section_laps` (projection 3): per detected section, the clean laps' median section time and the three
 * fastest and three slowest of them, as the projector ranked them. The browser only selects and draws; it ranks nothing.
 */

export interface SectionLap {
  lap: number;
  sectionS: number;
  deltaS: number;
}

export interface SectionLapEntry {
  sectionId: string;
  cleanLapCount: number;
  medianS: number | null;
  /** The clean lap whose section time is the median (`median_lap`, optional); null when the view does not report it. */
  medianLap: number | null;
  /** Fastest first. */
  fastest: SectionLap[];
  /** Slowest first. */
  slowest: SectionLap[];
}

export type SectionLaps = ReadonlyMap<string, SectionLapEntry>;

/** Shade of a listed lap: `g1` the fastest … `g3`, `r3` … `r1` the slowest. */
export type Shade = "g1" | "g2" | "g3" | "r3" | "r2" | "r1";

export interface ShadedLap extends SectionLap {
  shade: Shade;
}

function laps(value: unknown): SectionLap[] {
  return asArray(value).flatMap((item) => {
    const r = asRecord(item);
    const lap = num(r.lap);
    const sectionS = num(r.section_s);
    const deltaS = num(r.delta_s);
    return lap !== null && sectionS !== null && deltaS !== null ? [{ lap, sectionS, deltaS }] : [];
  });
}

/** null when the view has no `section_laps` (projected before version 3). */
export function parseSectionLaps(raw: unknown): SectionLaps | null {
  const r = asRecord(raw);
  if (!Array.isArray(r.sections)) return null;
  const out = new Map<string, SectionLapEntry>();
  for (const item of r.sections) {
    const s = asRecord(item);
    const sectionId = str(s.section_id);
    if (sectionId === null) continue;
    out.set(sectionId, { sectionId, cleanLapCount: num(s.clean_lap_count) ?? 0, medianS: num(s.median_s), medianLap: num(s.median_lap), fastest: laps(s.fastest).slice(0, 3), slowest: laps(s.slowest).slice(0, 3) });
  }
  return out;
}

const FAST: readonly Shade[] = ["g1", "g2", "g3"];
const SLOW: readonly Shade[] = ["r1", "r2", "r3"];

/** The six laps in time order (fastest … slowest), each with its shade; the median falls between `fastest` and `slowest`. */
export function shadedLaps(entry: SectionLapEntry | null | undefined): { fastest: ShadedLap[]; slowest: ShadedLap[] } {
  if (!entry) return { fastest: [], slowest: [] };
  return {
    fastest: entry.fastest.map((lap, i) => ({ ...lap, shade: FAST[i]! })),
    // Listed slowest-last, so the column reads in time order away from the median.
    slowest: entry.slowest.map((lap, i) => ({ ...lap, shade: SLOW[i]! })).reverse(),
  };
}

/** What Open in Traces opens: the selected laps when any are selected, else all listed laps, in list order. */
export function lapsToOpen(listed: readonly number[], selected: ReadonlySet<number>): number[] {
  const chosen = listed.filter((lap) => selected.has(lap));
  return chosen.length ? chosen : [...listed];
}

/** Bar geometry for a delta: percent of half the cell, against the largest |delta| shown (drawing scale only). */
export function deltaBar(deltaS: number, laps: readonly SectionLap[]): { side: "gain" | "loss" | "zero"; widthPct: number } {
  const scale = Math.max(0, ...laps.map((lap) => Math.abs(lap.deltaS)));
  if (scale <= 0 || deltaS === 0) return { side: "zero", widthPct: 0 };
  return { side: deltaS < 0 ? "gain" : "loss", widthPct: (Math.abs(deltaS) / scale) * 50 };
}

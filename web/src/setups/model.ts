import type { Schemas } from "../core/api";
import { EXCLUDED_LEAF_PATTERNS } from "../gen/setup-keys";

export interface ValueItem {
  key: string;
  label: string;
  value: string;
}
export interface ValueGroup {
  group: string;
  items: ValueItem[];
}

const patterns = EXCLUDED_LEAF_PATTERNS.map(([source]) => new RegExp(source, "i"));

const ABBREVIATIONS = new Set(["lf", "rf", "lr", "rr", "abs", "arb"]);

/** Camel-case leaf or segment to words: `BumpStiffness` -> `Bump stiffness`. */
export function words(segment: string): string {
  const spaced = segment.replace(/([a-z0-9])([A-Z])/g, "$1 $2").replace(/([A-Z]+)([A-Z][a-z])/g, "$1 $2");
  const s = spaced
    .trim()
    .toLowerCase()
    .split(" ")
    .map((w) => (ABBREVIATIONS.has(w) ? w.toUpperCase() : w))
    .join(" ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export function isReadout(key: string, excluded: ReadonlySet<string>): boolean {
  if (excluded.has(key)) return true;
  const leaf = key.split(".").pop() ?? key;
  return patterns.some((p) => p.test(leaf));
}

function groupItems(items: [string, string][]): ValueGroup[] {
  const groups = new Map<string, ValueItem[]>();
  for (const [key, value] of items) {
    const parts = key.split(".");
    const leaf = parts.pop() ?? key;
    const group = parts.length ? parts.map(words).join(" · ") : "General";
    let list = groups.get(group);
    if (!list) groups.set(group, (list = []));
    list.push({ key, label: words(leaf), value });
  }
  return [...groups].map(([group, list]) => ({ group, items: list }));
}

/**
 * CarSetup leaves grouped by their path. Derived values and run readings (setup-keys.json) are returned apart:
 * they come from the garage or the last run, are not settings, and never count toward a comparison.
 */
export function groupSetupValues(
  values: Readonly<Record<string, string>>,
  excludedKeys: readonly string[] = [],
): { settings: ValueGroup[]; readouts: ValueGroup[]; settingCount: number } {
  const excluded = new Set(excludedKeys);
  const settings: [string, string][] = [];
  const readouts: [string, string][] = [];
  for (const [key, value] of Object.entries(values)) (isReadout(key, excluded) ? readouts : settings).push([key, value]);
  return { settings: groupItems(settings), readouts: groupItems(readouts), settingCount: settings.length };
}

export type CompareKind = "changed" | "unknown" | "readout" | "identical";
export interface CompareRow {
  key: string;
  label: string;
  left: string;
  right: string;
  kind: CompareKind;
}

export function compareLabel(key: string): string {
  const parts = key.split(".");
  const leaf = parts.pop() ?? key;
  return parts.length ? `${parts.map(words).join(" · ")} · ${words(leaf)}` : words(leaf);
}

/** Changed and unknown settings first, then readouts that moved, then identical. Missing is unknown, never equal. */
export function classifyCompare(rows: readonly Schemas["SetupCompareRow"][]): Record<CompareKind, CompareRow[]> {
  const out: Record<CompareKind, CompareRow[]> = { changed: [], unknown: [], readout: [], identical: [] };
  for (const r of rows) {
    const kind: CompareKind = r.status === "identical" ? "identical" : r.readout ? "readout" : r.status === "unknown" ? "unknown" : "changed";
    out[kind].push({
      key: r.key,
      label: r.label?.trim() ? r.label : compareLabel(r.key),
      left: r.left?.length ? r.left : "-",
      right: r.right?.length ? r.right : "-",
      kind,
    });
  }
  return out;
}

export const SOURCE_LABEL: Record<Schemas["SetupSource"], string> = {
  recording: "Recording",
  garage_snapshot: "Garage",
  garage_commit: "Garage",
  htm_export: "Export",
};

export function setupTitle(s: Pick<Schemas["SetupSummary"], "names" | "car_name" | "car_path" | "setup_id">): string {
  if (s.names.length > 0) return s.names[0]!;
  return `Unnamed ${s.car_name ?? s.car_path} setup`;
}

export function uniqueSources(sources: readonly Schemas["SetupSource"][]): string[] {
  return [...new Set(sources.map((s) => SOURCE_LABEL[s]))];
}

/** iRacing season label for a date: seasons start in March, June, September and December (2026S1 starts Dec 2025). */
export function currentSeason(d: Date): string {
  const m = d.getUTCMonth() + 1;
  const y = d.getUTCFullYear();
  if (m === 12) return `${y + 1}S1`;
  if (m <= 2) return `${y}S1`;
  if (m <= 5) return `${y}S2`;
  if (m <= 8) return `${y}S3`;
  return `${y}S4`;
}

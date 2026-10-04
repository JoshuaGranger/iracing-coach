import type { Schemas } from "../core/api";
import { formatDate } from "../core/format";

export interface ValueItem {
  key: string;
  label: string;
  value: string;
}
export interface ValueGroup {
  group: string;
  items: ValueItem[];
}

const ABBREVIATIONS = new Set(["lf", "rf", "lr", "rr", "abs", "arb"]);

/** Camel-case or snake_case leaf or segment to words: `BumpStiffness` and `bump_stiffness` -> `Bump stiffness`. */
export function words(segment: string): string {
  const spaced = segment
    .replace(/[_-]+/g, " ")
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/([A-Z]+)([A-Z][a-z])/g, "$1 $2");
  const s = spaced
    .trim()
    .toLowerCase()
    .split(/\s+/)
    .map((w) => (ABBREVIATIONS.has(w) ? w.toUpperCase() : w))
    .join(" ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** What a CarSetup key is: a setting the driver chose, a computed garage readout, or a reading from the last run. */
export type KeyKind = "setting" | "readout" | "reading";

/** Camel, Pascal and snake_case spellings of one leaf compare equal: `CornerWeight`, `corner_weight`. */
const flat = (s: string): string => s.replace(/[^A-Za-z0-9]+/g, "").toLowerCase();

/** The tire readings and the sheet's refresh counter (`setup_catalog.TRANSIENT_FIELD_TERMS`). */
const READING_TERMS = ["lasthotpressure", "lasttemps", "treadremaining", "updatecount"];
/** Computed by the garage (`setup_catalog.GARAGE_READOUT_LEAVES`). */
const READOUT_LEAVES = new Set(["cornerweight", "crossweight", "noseweight", "rideheight", "traveltocoilbind", "truckarmpreload", "arbpreload"]);
/** Sections whose `Preload` leaf is the computed torque (`GARAGE_READOUT_SECTION_LEAVES`). */
const READOUT_SECTIONS = new Set(["frontarb", "reararb"]);

/**
 * The rule Compare applies (`setup_catalog.is_garage_readout_field` and `is_transient_setup_field`), so a key sits under the
 * same heading on the setup page and in a comparison. The identity list of `setup-keys.json` is a different question
 * (what makes a new setup) and is not used here.
 */
export function keyKind(key: string): KeyKind {
  const parts = key.split(".");
  const leaf = parts[parts.length - 1] ?? key;
  const section = parts.length > 1 ? flat(parts[parts.length - 2]!) : "";
  const f = flat(leaf);
  if (READING_TERMS.some((t) => f.includes(t))) return "reading";
  if (READOUT_LEAVES.has(f) || (f === "preload" && READOUT_SECTIONS.has(section))) return "readout";
  return "setting";
}

export function isReadout(key: string): boolean {
  return keyKind(key) !== "setting";
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

export interface SetupValues {
  settings: ValueGroup[];
  /** Computed by the garage; Compare lists them apart and never counts them. */
  readouts: ValueGroup[];
  /** Tire readings from the last run; never compared. */
  readings: ValueGroup[];
  settingCount: number;
}

/** CarSetup leaves grouped by their path, with garage readouts and run readings apart from the settings. */
export function groupSetupValues(values: Readonly<Record<string, string>>): SetupValues {
  const bucket: Record<KeyKind, [string, string][]> = { setting: [], readout: [], reading: [] };
  for (const [key, value] of Object.entries(values)) bucket[keyKind(key)].push([key, value]);
  return { settings: groupItems(bucket.setting), readouts: groupItems(bucket.readout), readings: groupItems(bucket.reading), settingCount: bucket.setting.length };
}

export type CompareKind = "changed" | "unknown" | "identical";
export interface CompareRow {
  key: string;
  label: string;
  left: string;
  right: string;
  kind: CompareKind;
  readout: boolean;
}
export interface CompareRows {
  /** Settings that differ, then settings missing on a side. */
  changed: CompareRow[];
  unknown: CompareRow[];
  identical: CompareRow[];
  /** Computed garage readouts of any status, changed first. They are never counted as changes. */
  readouts: CompareRow[];
  /** Readouts whose values differ. */
  readoutsMoved: number;
}

export function compareLabel(key: string): string {
  const parts = key.split(".");
  const leaf = parts.pop() ?? key;
  return parts.length ? `${parts.map(words).join(" · ")} · ${words(leaf)}` : words(leaf);
}

const READOUT_ORDER: Record<CompareKind, number> = { changed: 0, unknown: 1, identical: 2 };

/**
 * Settings first (changed, unknown, identical), readouts after them. Missing is unknown, never equal. The label is
 * always ours: the backend's `label` is its field name.
 */
export function classifyCompare(rows: readonly Schemas["SetupCompareRow"][]): CompareRows {
  const out: CompareRows = { changed: [], unknown: [], identical: [], readouts: [], readoutsMoved: 0 };
  for (const r of rows) {
    // Run readings are not setup values: the backend leaves them out and so does the page.
    if (keyKind(r.key) === "reading") continue;
    const kind: CompareKind = r.status;
    const row: CompareRow = { key: r.key, label: compareLabel(r.key), left: r.left?.length ? r.left : "-", right: r.right?.length ? r.right : "-", kind, readout: r.readout === true };
    if (row.readout) out.readouts.push(row);
    else out[kind].push(row);
  }
  out.readouts.sort((a, b) => READOUT_ORDER[a.kind] - READOUT_ORDER[b.kind]);
  out.readoutsMoved = out.readouts.filter((r) => r.kind === "changed").length;
  return out;
}

export const SOURCE_LABEL: Record<Schemas["SetupSource"], string> = {
  recording: "Recording",
  garage_snapshot: "Garage",
  garage_commit: "Garage",
  htm_export: "Export",
};

export type Setup = Schemas["SetupSummary"];

export function setupTitle(s: Pick<Setup, "names" | "car_name" | "car_path" | "setup_id">): string {
  if (s.names.length > 0) return s.names[0]!;
  return "Garage capture";
}

type NameKey = Pick<Setup, "setup_id" | "names"> & Partial<Pick<Setup, "car_path">>;

const same = (a: string, b: string) => a.toLowerCase() === b.toLowerCase();

/** The other setups of the same car that carry one of this setup's file names (case-insensitive). */
export function sameNameAs<T extends NameKey>(s: NameKey, all: readonly T[]): T[] {
  if (s.names.length === 0) return [];
  const names = new Set(s.names.map((n) => n.toLowerCase()));
  return all.filter((o) => o.setup_id !== s.setup_id && (s.car_path == null || o.car_path == null || same(o.car_path, s.car_path)) && o.names.some((n) => names.has(n.toLowerCase())));
}

/** When this set of values was seen: one date, or first to last. */
export function seenSpan(s: Pick<Setup, "first_seen_at" | "last_seen_at">): string {
  const a = formatDate(s.first_seen_at);
  const b = formatDate(s.last_seen_at);
  return a === b ? a : `${a} to ${b}`;
}

export function uniqueSources(sources: readonly Schemas["SetupSource"][]): string[] {
  return [...new Set(sources.map((s) => SOURCE_LABEL[s]))];
}

/** Values known only from an exported sheet: the server compares such a setup in the export scheme, which a recorded setup does not share. */
export type CompareScheme = "export" | "recording";
export function compareScheme(s: Pick<Setup, "sources">): CompareScheme {
  return s.sources.length > 0 && s.sources.every((x) => x === "htm_export") ? "export" : "recording";
}

export interface ComparePick {
  setup: Setup;
  /** Why this setup does not compare with the open one; null when it does. */
  blocked: string | null;
}

/** Siblings of one car, each with the reason it does not compare (a setup of another scheme compares as all unknown). */
export function comparePicks(current: Setup, others: readonly Setup[]): ComparePick[] {
  const mine = compareScheme(current);
  return others
    .filter((o) => o.setup_id !== current.setup_id && same(o.car_path, current.car_path))
    .map((o) => ({ setup: o, blocked: compareScheme(o) === mine ? null : compareScheme(o) === "export" ? "Export only" : "Recorded" }));
}

export type TuneTarget = { analysisId: string } | { blocked: string };

/**
 * Where Tune this setup goes: the newest recording observation that carries its session's current analysis
 * (`SetupObservation.analysis_id`), so the page needs no second request.
 */
export function tuneTarget(observations: readonly Schemas["SetupObservation"][]): TuneTarget {
  const recorded = observations.filter((o) => o.session_id != null);
  if (recorded.length === 0) return { blocked: "No recording yet" };
  const analyzed = recorded.filter((o) => o.analysis_id).sort((a, b) => Date.parse(b.observed_at) - Date.parse(a.observed_at));
  const newest = analyzed[0];
  return newest ? { analysisId: newest.analysis_id! } : { blocked: "Recording not analyzed" };
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

/** `26S4` and `2026 S4` mean `2026S4`; the backend matches the long form only. Anything else is the driver's own text. */
export function normalizeSeason(text: string): string {
  const m = /^(\d{2}|\d{4})[sS](\d)$/.exec(text.replace(/\s+/g, ""));
  if (!m) return text.trim();
  return `${m[1]!.length === 2 ? `20${m[1]}` : m[1]}S${m[2]}`;
}

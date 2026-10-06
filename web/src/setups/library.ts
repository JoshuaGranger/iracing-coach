import type { Schemas } from "../core/api";
import { carAccent } from "../core/palette";
import type { Setup } from "./model";

export type SetupFile = Schemas["SetupFileEntry"];

/**
 * How many races used this setup (`SetupSummary.raced_count`). A null, missing or inconsistent count is unknown (null), never 0.
 */
export function racedCountOf(s: Setup): number | null {
  const n: unknown = s.raced_count;
  return s.raced && typeof n === "number" && Number.isInteger(n) && n > 0 ? n : null;
}

/** The count over several setups: known only when every raced setup reports one; null when nothing raced or any count is unknown. */
export function racedCountOver(setups: readonly Setup[]): number | null {
  const raced = setups.filter((s) => s.raced);
  if (raced.length === 0) return null;
  let total = 0;
  for (const s of raced) {
    const n = racedCountOf(s);
    if (n === null) return null;
    total += n;
  }
  return total;
}

export interface Version {
  setup: Setup;
  /** 1 for the oldest version of a name. */
  number: number;
  latest: boolean;
}

export type Entry =
  | {
      kind: "setup";
      key: string;
      name: string;
      /** Newest first. One element for a setup that has no other version. */
      versions: Version[];
      seen: number;
      raced: boolean;
      /** Races over all versions; null when unknown (a raced setup the server gave no count for). */
      racedCount: number | null;
      last: string;
    }
  | {
      kind: "captures";
      key: string;
      /** Unnamed garage captures, newest first. */
      setups: Setup[];
      seen: number;
      raced: boolean;
      racedCount: number | null;
      last: string;
    }
  | { kind: "file"; key: string; name: string; file: SetupFile };

export interface CarGroup {
  carPath: string;
  carName: string;
  color: string;
  entries: Entry[];
  /** Setups of this car, versions and captures counted one each, and how many of them raced. */
  setups: number;
  raced: number;
}

const lower = (s: string) => s.toLowerCase();
const time = (iso: string) => {
  const t = Date.parse(iso);
  return Number.isFinite(t) ? t : 0;
};

/** Clusters of setups that share a file name (directly or through another setup that does). */
function cluster(setups: readonly Setup[]): Setup[][] {
  const parent = new Map<number, number>();
  const find = (x: number): number => {
    let r = x;
    while (parent.get(r) !== r) r = parent.get(r)!;
    parent.set(x, r);
    return r;
  };
  for (const s of setups) parent.set(s.setup_id, s.setup_id);
  const byName = new Map<string, number>();
  for (const s of setups) {
    for (const n of s.names) {
      const k = lower(n);
      const seen = byName.get(k);
      if (seen === undefined) byName.set(k, s.setup_id);
      else parent.set(find(s.setup_id), find(seen));
    }
  }
  const groups = new Map<number, Setup[]>();
  for (const s of setups) {
    const r = find(s.setup_id);
    const list = groups.get(r);
    if (list) list.push(s);
    else groups.set(r, [s]);
  }
  return [...groups.values()];
}

/** The name most of the cluster carries; the newest version's first name breaks a tie. */
function clusterName(versions: readonly Setup[]): string {
  const counts = new Map<string, { name: string; n: number }>();
  for (const s of versions) {
    for (const n of s.names) {
      const k = lower(n);
      const c = counts.get(k);
      if (c) c.n += 1;
      else counts.set(k, { name: n, n: 1 });
    }
  }
  const newest = [...versions].sort((a, b) => time(b.last_seen_at) - time(a.last_seen_at))[0]!;
  const tie = lower(newest.names[0] ?? "");
  return [...counts.values()].sort((a, b) => b.n - a.n || (lower(b.name) === tie ? 1 : 0) - (lower(a.name) === tie ? 1 : 0))[0]!.name;
}

function entryFor(group: Setup[]): Extract<Entry, { kind: "setup" }> {
  const oldestFirst = [...group].sort((a, b) => time(a.first_seen_at) - time(b.first_seen_at) || a.setup_id - b.setup_id);
  const versions: Version[] = oldestFirst.map((setup, i) => ({ setup, number: i + 1, latest: i === oldestFirst.length - 1 })).reverse();
  return {
    kind: "setup",
    key: `setup-${versions[0]!.setup.setup_id}`,
    name: clusterName(group),
    versions,
    seen: group.reduce((n, s) => n + s.observation_count, 0),
    raced: group.some((s) => s.raced),
    racedCount: racedCountOver(group),
    last: group.map((s) => s.last_seen_at).sort((a, b) => time(b) - time(a))[0]!,
  };
}

const stem = (path: string) => path.split(/[\\/]/).pop() ?? path;

/**
 * The library by car: named setups with their versions, one stack of unnamed garage captures, and the files whose
 * values are unknown. `fileCar` maps a file's car folder to a car path (the folder is not always the path).
 */
export function groupLibrary(setups: readonly Setup[], files: readonly SetupFile[], filter: LibraryFilter = {}): CarGroup[] {
  const cars = filter.cars && filter.cars.size > 0 ? new Set([...filter.cars].map(lower)) : null;
  const raced = filter.raced ?? "all";
  const folderToCar = new Map<string, string>();
  for (const s of setups) {
    folderToCar.set(lower(s.car_path), s.car_path);
    for (const f of s.sto_files ?? []) folderToCar.set(lower(f.car_folder), s.car_path);
  }
  const groups = new Map<string, { carPath: string; carName: string; setups: Setup[]; files: SetupFile[] }>();
  const get = (carPath: string, carName: string) => {
    const k = lower(carPath);
    let g = groups.get(k);
    if (!g) groups.set(k, (g = { carPath, carName, setups: [], files: [] }));
    else if (!g.carName || g.carName === g.carPath) g.carName = carName;
    return g;
  };
  for (const s of setups) {
    if (cars && !cars.has(lower(s.car_path))) continue;
    if (racedMatches(raced, s.raced)) get(s.car_path, s.car_name ?? s.car_path).setups.push(s);
  }
  for (const f of files) {
    const path = folderToCar.get(lower(f.car_folder)) ?? f.car_folder;
    if (cars && !cars.has(lower(path)) && !cars.has(lower(f.car_folder))) continue;
    if (raced !== "raced") get(path, carNameOf(setups, path) ?? screenName(f.car_name) ?? path).files.push(f);
  }
  const out: CarGroup[] = [];
  for (const g of groups.values()) {
    const named = g.setups.filter((s) => s.names.length > 0);
    const unnamed = g.setups.filter((s) => s.names.length === 0).sort((a, b) => time(b.last_seen_at) - time(a.last_seen_at));
    const entries: Entry[] = cluster(named).map(entryFor);
    if (unnamed.length > 0) {
      entries.push({
        kind: "captures",
        key: `captures-${g.carPath}`,
        setups: unnamed,
        seen: unnamed.reduce((n, s) => n + s.observation_count, 0),
        raced: unnamed.some((s) => s.raced),
        racedCount: racedCountOver(unnamed),
        last: unnamed[0]!.last_seen_at,
      });
    }
    entries.sort((a, b) => time(lastOf(b)) - time(lastOf(a)));
    const files = [...g.files].sort((a, b) => stem(a.relative_path).localeCompare(stem(b.relative_path)));
    for (const f of files) entries.push({ kind: "file", key: `file-${f.sha256}-${f.relative_path}`, name: stem(f.relative_path), file: f });
    out.push({
      carPath: g.carPath,
      carName: g.carName,
      color: carAccent(g.carPath),
      entries,
      setups: named.length + unnamed.length,
      raced: g.setups.filter((s) => s.raced).length,
    });
  }
  return out.sort((a, b) => newest(b) - newest(a) || a.carName.localeCompare(b.carName));
}

/** A car the library holds setups for, as the Car dropdown offers it. */
export interface CarChoice {
  carPath: string;
  carName: string;
  color: string;
  /** Setups of the car, versions and captures counted one each. */
  setups: number;
}

/**
 * The cars that have setups, in the library's order. A car with only unlinked files (no values, no setup) is not offered: the
 * filter lists values that exist among the setups it filters (#38).
 */
export function carsWithSetups(groups: readonly CarGroup[]): CarChoice[] {
  return groups.filter((g) => g.setups > 0).map((g) => ({ carPath: g.carPath, carName: g.carName, color: g.color, setups: g.setups }));
}

const screenName = (name: string | null | undefined): string | null => name?.trim() || null;

function carNameOf(setups: readonly Setup[], carPath: string): string | null {
  return setups.find((s) => lower(s.car_path) === lower(carPath))?.car_name ?? null;
}

/** The cars with decoded setups, in the library's order: the sections of the page. */
export function carsWithValues(groups: readonly CarGroup[]): CarGroup[] {
  return groups.filter((g) => g.setups > 0);
}

/**
 * The cars that have only files with unknown values (#74): they sit below the sections under one quiet divider, named by the
 * sim's screen name when any recording of the car gave one (the folder name only as a fallback), A to Z. Never counted as setups.
 */
export function carsFilesOnly(groups: readonly CarGroup[]): CarGroup[] {
  return groups.filter((g) => g.setups === 0 && g.entries.length > 0).sort((a, b) => a.carName.localeCompare(b.carName, undefined, { sensitivity: "base" }) || a.carPath.localeCompare(b.carPath));
}

/** The file stems of a car group, for the rows under a files-only car. */
export function fileNames(g: CarGroup): string[] {
  return g.entries.flatMap((e) => (e.kind === "file" ? [e.name] : []));
}

function lastOf(e: Entry): string {
  return e.kind === "file" ? "" : e.last;
}
function newest(g: CarGroup): number {
  return Math.max(0, ...g.entries.map((e) => time(lastOf(e))));
}

export type RacedFilter = "all" | "raced" | "not-raced";

export interface LibraryFilter {
  /** Car paths to keep; empty or absent keeps every car. */
  cars?: ReadonlySet<string>;
  raced?: RacedFilter;
}

/** The raced filter applies to setups; a file with unknown values was never driven as far as the library knows, so it shows under Not raced and All. */
export function racedMatches(filter: RacedFilter, raced: boolean): boolean {
  return filter === "all" || (filter === "raced" ? raced : !raced);
}

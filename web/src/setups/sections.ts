import type { Schemas } from "../core/api";
import { readJson, writeJson } from "../core/store";
import type { RacedFilter } from "./library";

/** Rows a car section shows at first, and how many `Show 10 more` appends. */
export const FIRST_ROWS = 10;
export const MORE_ROWS = 10;

/** The rows to draw: the revealed count (default `FIRST_ROWS`), never past the total. */
export function shownRows(total: number, revealed: number | undefined): number {
  const want = revealed !== undefined && Number.isFinite(revealed) ? Math.max(FIRST_ROWS, Math.floor(revealed)) : FIRST_ROWS;
  return Math.min(Math.max(0, total), want);
}

/** The revealed count after one `Show 10 more`. */
export function revealMore(revealed: number | undefined): number {
  return Math.max(FIRST_ROWS, revealed ?? FIRST_ROWS) + MORE_ROWS;
}

/**
 * The revealed count of each car section. A module-level map, so it survives a collapse (the section stays mounted but hidden)
 * and a visit to a setup and Back; a reload starts at ten again.
 */
const revealedRows = new Map<string, number>();
const revealKey = (customerId: number, carPath: string) => `${customerId}|${carPath.toLowerCase()}`;
export const revealed = {
  get: (customerId: number, carPath: string): number | undefined => revealedRows.get(revealKey(customerId, carPath)),
  set: (customerId: number, carPath: string, n: number): void => void revealedRows.set(revealKey(customerId, carPath), n),
  reset: (): void => revealedRows.clear(),
};

const COLLAPSED_KEY = "coach.setups.collapsed";

/** The car sections this device has collapsed (lower-case car paths). A malformed store reads as none collapsed. */
export function readCollapsed(): Set<string> {
  const raw = readJson<unknown>(COLLAPSED_KEY, []);
  return new Set(Array.isArray(raw) ? raw.filter((x): x is string => typeof x === "string").map((x) => x.toLowerCase()) : []);
}

export function writeCollapsed(collapsed: ReadonlySet<string>): void {
  writeJson(COLLAPSED_KEY, [...collapsed].sort());
}

/** Toggle one car section; returns a new set. */
export function toggleCollapsed(collapsed: ReadonlySet<string>, carPath: string): Set<string> {
  const next = new Set(collapsed);
  const key = carPath.toLowerCase();
  if (next.has(key)) next.delete(key);
  else next.add(key);
  return next;
}

/* ---- the page's filters, as the driver's prefs store them (`setup_filters`) ------------------------------------- */

export interface SetupSelection {
  cars: string[];
  text: string;
  raced: RacedFilter;
}

export const emptySelection = (): SetupSelection => ({ cars: [], text: "", raced: "all" });

export function isEmptySelection(s: SetupSelection): boolean {
  return s.cars.length === 0 && s.text.trim() === "" && s.raced === "all";
}

/** How many choices are in force: each car, a name, a raced choice. */
export function activeCount(s: SetupSelection): number {
  return s.cars.length + (s.text.trim() ? 1 : 0) + (s.raced === "all" ? 0 : 1);
}

/**
 * `SetupFilters` holds one `car_path`; the Car filter is a multi-select, so one car is stored there and the whole list in the
 * extra `car_paths` field (the contract preserves unknown fields). `raced: null` is both.
 */
export function toSetupFilters(s: SetupSelection): Schemas["SetupFilters"] {
  return {
    car_path: s.cars.length === 1 ? s.cars[0]! : null,
    car_paths: s.cars.length > 0 ? s.cars : null,
    raced: s.raced === "raced" ? true : s.raced === "not-raced" ? false : null,
    text: s.text.trim() ? s.text.trim() : null,
  };
}

/** A stored `setup_filters` back to a selection; anything malformed is dropped. Null when nothing is stored. */
export function fromSetupFilters(stored: Schemas["SetupFilters"] | null | undefined): SetupSelection | null {
  if (!stored || typeof stored !== "object") return null;
  const list = (stored as { car_paths?: unknown }).car_paths;
  const cars = Array.isArray(list) ? list.filter((x): x is string => typeof x === "string") : typeof stored.car_path === "string" && stored.car_path ? [stored.car_path] : [];
  const raced: RacedFilter = stored.raced === true ? "raced" : stored.raced === false ? "not-raced" : "all";
  return { cars, text: typeof stored.text === "string" ? stored.text : "", raced };
}

/** The selection kept on this device so the first paint is already filtered while the prefs load. */
export function parseSelection(raw: unknown): SetupSelection | null {
  if (!raw || typeof raw !== "object") return null;
  const o = raw as Record<string, unknown>;
  const cars = Array.isArray(o.cars) ? o.cars.filter((x): x is string => typeof x === "string") : [];
  const raced: RacedFilter = o.raced === "raced" || o.raced === "not-raced" ? o.raced : "all";
  return { cars, text: typeof o.text === "string" ? o.text : "", raced };
}

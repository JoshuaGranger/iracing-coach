import { useEffect, useMemo, useState } from "preact/hooks";
import { ApiError, type Schemas, isJobRef, paths, request } from "../core/api";
import { jobFailure, waitForJob } from "../core/jobs";
import { uuid4 } from "../core/uuid";
import { type CompareRow, type Setup, classifyCompare, compareScheme } from "./model";

type Result = Schemas["SetupCompareResult"];

export interface VersionRef {
  setup: Setup;
  /** 1 for the oldest version of a name. */
  number: number;
  latest: boolean;
}

const time = (iso: string) => {
  const t = Date.parse(iso);
  return Number.isFinite(t) ? t : 0;
};

/** The versions of one name, newest first: the open setup and the other setups that carry its file name, oldest numbered 1. */
export function versionsOf(current: Setup, twins: readonly Setup[]): VersionRef[] {
  const all = [current, ...twins].sort((a, b) => time(a.first_seen_at) - time(b.first_seen_at) || a.setup_id - b.setup_id);
  return all.map((setup, i) => ({ setup, number: i + 1, latest: i === all.length - 1 })).reverse();
}

/** A value split into its figure and unit: `-3.2 deg` is `-3.2` and `deg`. A value with no trailing unit has an empty unit. */
export function splitUnit(value: string): { value: string; unit: string } {
  const m = /^([+-]?\d[\d.,/]*)\s*([A-Za-z%°"'][\w%°/.\-"']*)$/.exec(value.trim());
  return m ? { value: m[1]!, unit: m[2]! } : { value: value.trim(), unit: "" };
}

export interface Change {
  key: string;
  /** The parameter as the setup page names it. */
  label: string;
  old: string;
  new: string;
  /** The shared unit, when both values carry the same one; empty when the values keep their own text. */
  unit: string;
}

/** One changed setting as a row: old value, new value and their shared unit. */
export function changeOf(row: Pick<CompareRow, "key" | "label" | "left" | "right">): Change {
  const a = splitUnit(row.left);
  const b = splitUnit(row.right);
  const shared = a.unit !== "" && a.unit === b.unit;
  return { key: row.key, label: row.label, old: shared ? a.value : row.left, new: shared ? b.value : row.right, unit: shared ? a.unit : "" };
}

/** The settings that changed between two versions, from the backend's comparison. Readouts, run readings and unknowns are not changes. */
export function changesFrom(result: Pick<Result, "rows">): Change[] {
  return classifyCompare(result.rows).changed.map(changeOf);
}

export type VersionDiff =
  | { state: "loading" }
  | { state: "first" }
  | { state: "blocked"; reason: string }
  | { state: "error"; message: string }
  | { state: "done"; changes: Change[] };

/** The version each one is compared with: its predecessor. The oldest has none. Pure, so it is testable. */
export function pairs(versions: readonly VersionRef[]): { older: VersionRef; newer: VersionRef }[] {
  const out: { older: VersionRef; newer: VersionRef }[] = [];
  for (let i = 0; i < versions.length - 1; i++) out.push({ older: versions[i + 1]!, newer: versions[i]! });
  return out;
}

/** A pair the backend compares: both setups in one scheme (an exported sheet does not compare with a recorded setup). */
export function comparable(older: Setup, newer: Setup): string | null {
  return compareScheme(older) === compareScheme(newer) ? null : "Not comparable";
}

async function compare(left: number, right: number, signal: AbortSignal): Promise<Result> {
  const res = await request<unknown>("POST", paths.setupCompare, { left_setup_id: left, right_setup_id: right }, { idempotencyKey: uuid4(), signal });
  if (res.status === 202 || isJobRef(res.body)) {
    const job = await waitForJob((res.body as Schemas["JobRef"]).job_id, { signal });
    if (job.state !== "succeeded") throw new Error(jobFailure(job));
    return (job.result ?? { rows: [] }) as Result;
  }
  return res.body as Result;
}

/**
 * The changed parameters of each version against the one before it, from the backend's comparison of the two setups
 * (`POST /setups/compare`, one per pair, one at a time). Nothing runs until `enabled`: the Versions section loads them when it opens.
 * Keyed by the newer setup's id.
 */
export function useVersionChanges(versions: readonly VersionRef[], enabled: boolean): ReadonlyMap<number, VersionDiff> {
  const key = versions.map((v) => v.setup.setup_id).join(",");
  const [diffs, setDiffs] = useState<ReadonlyMap<number, VersionDiff>>(new Map());
  const list = useMemo(() => pairs(versions), [key]);
  useEffect(() => {
    if (!enabled) return undefined;
    const ctl = new AbortController();
    let live = true;
    const put = (id: number, diff: VersionDiff) => live && setDiffs((p) => new Map(p).set(id, diff));
    void (async () => {
      for (const { older, newer } of list) {
        const id = newer.setup.setup_id;
        const blocked = comparable(older.setup, newer.setup);
        if (blocked) {
          put(id, { state: "blocked", reason: blocked });
          continue;
        }
        put(id, { state: "loading" });
        try {
          put(id, { state: "done", changes: changesFrom(await compare(older.setup.setup_id, id, ctl.signal)) });
        } catch (e) {
          if (e instanceof DOMException && e.name === "AbortError") return;
          put(id, { state: "error", message: e instanceof ApiError || e instanceof Error ? e.message : "Request failed" });
        }
      }
    })();
    return () => {
      live = false;
      ctl.abort();
    };
  }, [enabled, key]);
  return diffs;
}

import { useEffect, useState } from "preact/hooks";
import { type Schemas, api, paths } from "./api";
import { outbox } from "./outbox";
import { cachedValue, loadShared, setCached, useResource } from "./resource";
import { readJson, writeJson } from "./store";

export type DriverPrefs = Schemas["DriverPrefs"];
export type DriverPrefsWrite = Schemas["DriverPrefsWrite"];
/** What this app writes: the contract's fields (`text_size`, `session_filters` and `setup_filters` included). */
export type DriverPrefsPatch = DriverPrefsWrite;
export type LastInvestigation = Schemas["LastInvestigation"];
export type TextSize = Schemas["TextSize"];
export type SessionFilters = Schemas["SessionFilters"];
export type SetupFilters = Schemas["SetupFilters"];

/**
 * Driver preferences (active layouts, last investigation, motion, safe glance, units, text size, page filters). Writes go through
 * the outbox with the version last read.
 */
export function usePrefs(customerId: number | null) {
  const key = customerId === null ? null : paths.prefs(customerId);
  const res = useResource<DriverPrefs>(key);
  useEffect(() => {
    if (key && res.data) outbox.observe(key, res.data.version);
  }, [key, res.data]);
  return {
    prefs: res.data,
    loading: res.loading,
    error: res.error,
    save: (patch: DriverPrefsPatch) => (customerId === null ? Promise.resolve() : savePrefs(customerId, patch)),
  };
}

/**
 * A write to a stored prefs row must carry its version (`If-Match`) or the server answers 412 and the shell opens a conflict choice.
 * Prefs the page has not read yet (a choice made in the first moments after load) are read first; unreachable, the write queues.
 */
async function knowVersion(customerId: number): Promise<void> {
  const key = paths.prefs(customerId);
  if (cachedValue<DriverPrefs>(key) !== undefined || outbox.versionOf(key) !== undefined) return;
  try {
    const stored = await loadShared(key, (signal) => api.prefs(customerId, { signal }));
    setCached(key, stored);
    outbox.observe(key, stored.version);
  } catch {
    /* the server is unreachable: the write queues and goes out with the version seen then */
  }
}

export async function savePrefs(customerId: number, patch: DriverPrefsPatch): Promise<unknown> {
  await knowVersion(customerId);
  const key = paths.prefs(customerId);
  const current = cachedValue<DriverPrefs>(key);
  const { customer_id: _c, version: _v, ...rest } = current ?? ({} as Partial<DriverPrefs>);
  const body: DriverPrefsPatch = { ...rest, ...patch };
  if (current) setCached(key, { ...current, ...patch });
  return outbox
    .enqueue({ method: "PUT", path: key, body, label: "Preferences", coalesceKey: `prefs:${customerId}` })
    .catch(() => undefined);
}

const lastKey = (c: number) => `coach.last-investigation.${c}`;

export function getLastInvestigation(customerId: number, serverValue?: LastInvestigation | null): LastInvestigation | null {
  return serverValue ?? readJson<LastInvestigation | null>(lastKey(customerId), null);
}

/** Called by the investigation workspace whenever its session, view, comparison or section changes. */
export function rememberInvestigation(customerId: number, last: LastInvestigation): void {
  writeJson(lastKey(customerId), last);
  void savePrefs(customerId, { last_investigation: last });
}

/** Operating-system reduced-motion preference combined with the driver's stored one. */
export function useReducedMotion(prefs?: { reduced_motion?: boolean | null } | null): boolean {
  const query = typeof matchMedia === "function" ? matchMedia("(prefers-reduced-motion: reduce)") : null;
  const [os, setOs] = useState(query?.matches ?? false);
  useEffect(() => {
    if (!query) return;
    const on = () => setOs(query.matches);
    query.addEventListener("change", on);
    return () => query.removeEventListener("change", on);
  }, [query]);
  return os || prefs?.reduced_motion === true;
}

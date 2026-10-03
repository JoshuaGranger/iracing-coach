import { useEffect, useState } from "preact/hooks";
import { type Schemas, paths } from "./api";
import { outbox } from "./outbox";
import { cachedValue, setCached, useResource } from "./resource";
import { readJson, writeJson } from "./store";

export type DriverPrefs = Schemas["DriverPrefs"];
export type DriverPrefsWrite = Schemas["DriverPrefsWrite"];
export type LastInvestigation = Schemas["LastInvestigation"];

/**
 * Driver preferences (active live layout, last investigation, motion, safe glance, units). Writes go through the
 * outbox with the version last read. The last investigation is also kept in localStorage so "resume" works
 * before the server answers and when it is down.
 */
export function usePrefs(customerId: number) {
  const key = paths.prefs(customerId);
  const res = useResource<DriverPrefs>(key);
  useEffect(() => {
    if (res.data) outbox.observe(key, res.data.version);
  }, [key, res.data]);
  return {
    prefs: res.data,
    loading: res.loading,
    error: res.error,
    save: (patch: DriverPrefsWrite) => savePrefs(customerId, patch),
  };
}

export function savePrefs(customerId: number, patch: DriverPrefsWrite): Promise<unknown> {
  const key = paths.prefs(customerId);
  const current = cachedValue<DriverPrefs>(key);
  const { customer_id: _c, version: _v, ...rest } = current ?? ({} as Partial<DriverPrefs>);
  const body: DriverPrefsWrite = { ...rest, ...patch };
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

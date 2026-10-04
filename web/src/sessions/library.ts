import { type Schemas, api, paths } from "../core/api";
import { useResource } from "../core/resource";
import type { Session } from "./model";

/** Rows per request: the contract's largest page. */
const PAGE = 200;
/** Stops a runaway cursor; 20 pages is 4000 sessions. */
const MAX_PAGES = 20;

/**
 * The whole library, newest first. The filters (Car, Track, Series, Session, Setup) take several values each and their options
 * list what the library holds, so the page reads every row once and filters here. `invalidate(".../sessions")` reaches this key,
 * so server events refresh it like any other read.
 */
export const libraryKey = (customerId: number) => paths.sessions(customerId, { limit: PAGE });

export async function loadLibrary(customerId: number, signal?: AbortSignal): Promise<Session[]> {
  const out: Session[] = [];
  let cursor: string | null = null;
  for (let page = 0; page < MAX_PAGES; page++) {
    const res: Schemas["SessionPage"] = await api.sessions(customerId, { limit: PAGE, cursor }, { signal });
    out.push(...res.sessions);
    cursor = res.next_cursor ?? null;
    if (!cursor) break;
  }
  return out;
}

export function useLibrary(customerId: number | null) {
  return useResource<Session[]>(customerId === null ? null : libraryKey(customerId), (signal) => loadLibrary(customerId!, signal));
}

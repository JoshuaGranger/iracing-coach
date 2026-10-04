import type { Library } from "./api";
import { currentRoute, useRoute } from "./router";

/**
 * The library the page is looking at: the driver's own (`main`, the default) or the `lab` library. There is no control for it:
 * `lab` is reached only by `?library=lab` in the page's URL (`#/d/1507810/sessions?library=lab`), read from the hash query on
 * every render, never stored. Pages pass the result to the sessions and setups calls.
 */
export function libraryOfQuery(query: Readonly<Record<string, string>>): Library {
  return query.library === "lab" ? "lab" : "main";
}

/** The library named by the current page's URL. Re-reads on every route change. */
export function useLibraryChoice(): Library {
  return libraryOfQuery(useRoute().query);
}

/** Non-hook read, for code outside a component. */
export const libraryChoice = (): Library => libraryOfQuery(currentRoute().query);

import { useEffect, useState } from "preact/hooks";

/**
 * Hash router (`#/d/1507810/sessions`). The server serves static files only, so no path fallback is needed.
 * Query parameters live inside the hash (`#/d/1/s/42/traces?comparison=c1&section=s3`).
 */
export type InvestigationView = "race" | "runs" | "sections" | "traces" | "results";
export const INVESTIGATION_VIEWS: readonly InvestigationView[] = ["race", "runs", "sections", "traces", "results"];

export type Route =
  | { page: "drivers" }
  | { page: "sessions"; customerId: number }
  | { page: "investigation"; customerId: number; sessionId: number; view: InvestigationView }
  | { page: "setups"; customerId: number }
  | { page: "setup"; customerId: number; setupId: number }
  | { page: "baseline"; customerId: number }
  | { page: "tuning"; customerId: number; analysisId: string | null }
  | { page: "live"; customerId: number }
  /** The component gallery (`#/__kit`): every shared mark, selector and pictogram on one page. Not linked from the app. */
  | { page: "kit" }
  | { page: "notfound" };

export type ParsedRoute = Route & { query: Record<string, string> };

const int = (s: string | undefined): number | null => {
  if (!s || !/^\d+$/.test(s)) return null;
  const n = Number(s);
  return Number.isSafeInteger(n) ? n : null;
};

export function parseHash(hash: string): ParsedRoute {
  const raw = hash.startsWith("#") ? hash.slice(1) : hash;
  const [pathPart = "", queryPart = ""] = raw.split("?", 2);
  const query: Record<string, string> = {};
  for (const [k, v] of new URLSearchParams(queryPart)) query[k] = v;
  const seg = pathPart.split("/").filter(Boolean).map(decodeURIComponent);
  const done = (r: Route): ParsedRoute => ({ ...r, query });
  if (seg.length === 0) return done({ page: "drivers" });
  if (seg[0] === "__kit" && seg.length === 1) return done({ page: "kit" });
  if (seg[0] !== "d") return done({ page: "notfound" });
  const customerId = int(seg[1]);
  if (customerId === null) return done({ page: "notfound" });
  switch (seg[2]) {
    case "sessions":
      return seg.length === 3 ? done({ page: "sessions", customerId }) : done({ page: "notfound" });
    case "s": {
      const sessionId = int(seg[3]);
      const view = (seg[4] ?? "race") as InvestigationView;
      if (sessionId === null || !INVESTIGATION_VIEWS.includes(view) || seg.length > 5) return done({ page: "notfound" });
      return done({ page: "investigation", customerId, sessionId, view });
    }
    case "setups": {
      if (seg.length === 3) return done({ page: "setups", customerId });
      if (seg[3] === "baseline" && seg.length === 4) return done({ page: "baseline", customerId });
      const setupId = int(seg[3]);
      return setupId !== null && seg.length === 4 ? done({ page: "setup", customerId, setupId }) : done({ page: "notfound" });
    }
    case "tuning":
      if (seg.length === 3) return done({ page: "tuning", customerId, analysisId: null });
      return seg.length === 4 ? done({ page: "tuning", customerId, analysisId: seg[3]! }) : done({ page: "notfound" });
    case "live":
      return seg.length === 3 ? done({ page: "live", customerId }) : done({ page: "notfound" });
    default:
      return done({ page: "notfound" });
  }
}

const e = encodeURIComponent;

export function formatRoute(route: Route, query?: Record<string, string | null | undefined>): string {
  let path: string;
  switch (route.page) {
    case "drivers":
      path = "/";
      break;
    case "sessions":
      path = `/d/${route.customerId}/sessions`;
      break;
    case "investigation":
      path = `/d/${route.customerId}/s/${route.sessionId}/${route.view}`;
      break;
    case "setups":
      path = `/d/${route.customerId}/setups`;
      break;
    case "setup":
      path = `/d/${route.customerId}/setups/${route.setupId}`;
      break;
    case "baseline":
      path = `/d/${route.customerId}/setups/baseline`;
      break;
    case "tuning":
      path = `/d/${route.customerId}/tuning${route.analysisId ? `/${e(route.analysisId)}` : ""}`;
      break;
    case "live":
      path = `/d/${route.customerId}/live`;
      break;
    case "kit":
      path = "/__kit";
      break;
    default:
      path = "/";
  }
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(query ?? {})) if (v) p.set(k, v);
  const q = p.toString();
  return `#${path}${q ? `?${q}` : ""}`;
}

export function href(route: Route, query?: Record<string, string | null | undefined>): string {
  return formatRoute(route, query);
}

export interface NavigateOptions {
  replace?: boolean;
}

export function navigate(to: Route | string, opts: NavigateOptions = {}): void {
  const hash = typeof to === "string" ? to : formatRoute(to);
  if (typeof location === "undefined") return;
  if (opts.replace) location.replace(hash);
  else location.hash = hash;
}

/** Merge `patch` into the current query string without adding a history entry (`null` removes a key). */
export function setQuery(patch: Record<string, string | null>, opts: NavigateOptions = { replace: true }): void {
  const cur = parseHash(location.hash);
  const next: Record<string, string> = { ...cur.query };
  for (const [k, v] of Object.entries(patch)) {
    if (v === null) delete next[k];
    else next[k] = v;
  }
  const { query: _drop, ...route } = cur;
  navigate(formatRoute(route as Route, next), opts);
}

export function currentRoute(): ParsedRoute {
  return parseHash(typeof location === "undefined" ? "" : location.hash);
}

export function useRoute(): ParsedRoute {
  const [route, setRoute] = useState<ParsedRoute>(currentRoute);
  useEffect(() => {
    const on = () => setRoute(currentRoute());
    window.addEventListener("hashchange", on);
    on();
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
}

import { INVESTIGATION_VIEWS, type InvestigationView, parseHash } from "../core/router";
import { getLastInvestigation } from "../core/prefs";
import { readJson, writeJson } from "../core/store";

/**
 * The last tab (Race, Runs, Sections, Traces) each session was open on, remembered silently: a session opens where it was left.
 * No banner, no setting. The investigation workspace is not told about this; the hash is watched instead, so the memory also
 * follows a link or a reload straight into a tab.
 */
type TabMap = Record<string, InvestigationView>;

const key = (customerId: number) => `coach.session-tabs.${customerId}`;
/** Sessions kept per driver; the oldest entries go first. */
const KEEP = 400;

export function rememberTab(map: TabMap, sessionId: number, view: InvestigationView): TabMap {
  const id = String(sessionId);
  if (map[id] === view) return map;
  const next: TabMap = { ...map };
  delete next[id];
  next[id] = view;
  const ids = Object.keys(next);
  for (const old of ids.slice(0, Math.max(0, ids.length - KEEP))) delete next[old];
  return next;
}

export function lastTab(map: TabMap, sessionId: number): InvestigationView | null {
  const v = map[String(sessionId)];
  return v && INVESTIGATION_VIEWS.includes(v) ? v : null;
}

export function readTabs(customerId: number): TabMap {
  const raw = readJson<unknown>(key(customerId), {});
  return raw && typeof raw === "object" ? (raw as TabMap) : {};
}

/** The tab and query a row opens with: the last one used in that session. */
export function openingTab(customerId: number, sessionId: number): { view: InvestigationView; query: Record<string, string> } {
  const last = getLastInvestigation(customerId);
  const view = lastTab(readTabs(customerId), sessionId) ?? (last?.session_id === sessionId ? last.view : null) ?? "race";
  const query: Record<string, string> = {};
  if (last?.session_id === sessionId && last.view === view) {
    if (last.comparison_id) query.comparison = last.comparison_id;
    if (last.section_id) query.section = last.section_id;
  }
  return { view, query };
}

let watching = false;
/** Start remembering tabs; once per page load. */
export function watchTabs(): void {
  if (watching || typeof window === "undefined") return;
  watching = true;
  // Each event carries its own hash: reading the location when the event is handled would miss a change that was followed by another.
  const note = (e?: HashChangeEvent) => {
    const r = parseHash(e ? new URL(e.newURL).hash : location.hash);
    if (r.page !== "investigation") return;
    writeJson(key(r.customerId), rememberTab(readTabs(r.customerId), r.sessionId, r.view));
  };
  window.addEventListener("hashchange", note);
  note();
}

import type { ComponentType } from "preact";
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { type Schemas, api, paths } from "../core/api";
import { outbox } from "../core/outbox";
import { type LastInvestigation, rememberInvestigation, savePrefs, usePrefs, useReducedMotion } from "../core/prefs";
import { invalidate, useResource } from "../core/resource";
import { type InvestigationView, formatRoute, navigate } from "../core/router";
import { writeJson } from "../core/store";
import { Empty, ErrorLine, Loading, Notice } from "./kit";

/** The device copy `getLastInvestigation` reads (core/prefs.ts, `coach.last-investigation.<driver>`). */
const lastKey = (customerId: number) => `coach.last-investigation.${customerId}`;

/**
 * The last investigation, remembered silently (web round 2, Sections). A selection is written to this device at once; the driver's
 * prefs are written once, when the investigation is left (unmount, page hide), because every prefs write shows the outbox bar above
 * the workspace while it is pending, and that shift re-fitted the workspace and redrew every chart on each section or comparison
 * click (the "glitch" of backlog #28g).
 */
export function useRememberedInvestigation(customerId: number) {
  const pending = useRef<LastInvestigation | null>(null);
  useEffect(() => {
    const flush = (): void => {
      const last = pending.current;
      pending.current = null;
      if (last) rememberInvestigation(customerId, last);
    };
    const onHide = (): void => { if (document.visibilityState === "hidden") flush(); };
    window.addEventListener("pagehide", flush);
    document.addEventListener("visibilitychange", onHide);
    return () => {
      window.removeEventListener("pagehide", flush);
      document.removeEventListener("visibilitychange", onHide);
      flush();
    };
  }, [customerId]);
  return (last: LastInvestigation): void => {
    writeJson(lastKey(customerId), last);
    pending.current = last;
  };
}

/**
 * Mounts the investigation workspace (W8, `web/src/investigation/`). The shell resolves the session to its current
 * analysis, keeps the route, the last-investigation preference and the saved trace layouts in step with the workspace, and
 * supplies the event's phases for the P Q R selector. The workspace makes the view requests itself (`createAnalysisLoader`).
 */
export interface InvestigationProps {
  customerId: number;
  sessionId: number;
  view: InvestigationView;
  /** Current query parameters: `comparison`, `section`, and `analysis` to open a specific analysis of the session. */
  query: Record<string, string>;
}

interface TraceLayoutBody {
  rows: { row_id: string; label?: string | null; signals: string[] }[];
}

interface Selection {
  view: InvestigationView;
  comparisonId: string | null;
  sectionId: string | null;
}

/** A saved chart layout as the workspace takes it (`investigation/layout-sets.ts`). */
interface NamedLayout {
  id: string;
  name: string;
  builtin: boolean;
  body: TraceLayoutBody;
}

/** A saved-layout change from the workspace (New, Copy, Delete, select, or an edit). */
type TraceLayoutCommand =
  | { type: "select"; id: string }
  | { type: "create"; layout: NamedLayout }
  | { type: "edit"; layout: NamedLayout; created: boolean }
  | { type: "delete"; id: string };

/** The part of W8's contract the shell uses (`investigation/README.md`). */
interface WorkspaceProps {
  loader: unknown;
  analysisId: string;
  view?: InvestigationView;
  comparisonId?: string | null;
  sectionId?: string | null;
  onSelectionChange?(selection: Selection): void;
  reducedMotion?: boolean;
  /** The event's phases (P, Q, R) with the state of each analysis, the open session, and the switch. */
  phases?: { sessionId: number; sessionType: string | null; state?: string | null; reason?: string | null }[];
  currentSessionId?: number;
  onPhase?(sessionId: number): void;
  /** The driver's saved layouts (the built-in Default included), the active one, and the changes to persist. */
  traceLayouts?: NamedLayout[];
  activeTraceLayoutId?: string;
  onTraceLayoutCommand?(command: TraceLayoutCommand): void;
}

interface InvestigationModule {
  InvestigationWorkspace: ComponentType<WorkspaceProps>;
  createAnalysisLoader(): unknown;
}

// Absent until W8 lands the workspace; the glob is empty then and the slot shows a notice.
const modules = import.meta.glob<InvestigationModule>("../investigation/index.{ts,tsx}");
const loadModule = Object.values(modules)[0] ?? null;
let loaderInstance: unknown = null;

type LayoutList = { layouts: Schemas["TraceLayout"][] };

/** The event's phases for the P Q R selector: the open session and the other phases, each with the state of its analysis. */
function phasesOf(detail: Schemas["SessionDetail"]): NonNullable<WorkspaceProps["phases"]> {
  const others = (detail.event_sessions ?? []).filter((o) => o.session_id !== detail.session.session_id);
  const me = detail.session;
  return [
    { sessionId: me.session_id, sessionType: me.session_type ?? null, state: me.analysis_state, reason: me.analysis_reason ?? null },
    ...others.map((o) => ({ sessionId: o.session_id, sessionType: o.session_type, state: o.analysis_state ?? null, reason: o.analysis_reason ?? null })),
  ];
}

function namedLayouts(list: readonly Schemas["TraceLayout"][]): NamedLayout[] {
  return list.map((l) => ({ id: l.layout_id, name: l.name || (l.builtin ? "Default" : "Layout"), builtin: l.builtin, body: l.body as TraceLayoutBody }));
}

const sameIds = (a: readonly NamedLayout[], b: readonly NamedLayout[]) => a.length === b.length && a.every((l, i) => l.id === b[i]?.id);

/**
 * Saved chart layouts: the server's list (`trace-layouts`, Default built in) and the active one (`active_trace_layout_id`). A change
 * shows at once and is written through the outbox: New and Copy PUT a new layout, an edit PUTs the layout (an edit of Default
 * arrives as a new layout), Delete removes it; the active id follows in the driver's prefs.
 */
function useTraceLayouts(customerId: number, activeId: string | null | undefined) {
  const layoutsKey = paths.traceLayouts(customerId);
  const layouts = useResource<LayoutList>(layoutsKey);
  const server = useMemo(() => namedLayouts(layouts.data?.layouts ?? []), [layouts.data]);
  (layouts.data?.layouts ?? []).forEach((l) => outbox.observe(paths.traceLayout(customerId, l.layout_id), l.version));
  const [pending, setPending] = useState<{ layouts: NamedLayout[]; activeId: string } | null>(null);
  // The server's list takes over once it holds the same layouts as the change shown.
  useEffect(() => {
    if (pending && sameIds(pending.layouts, server)) setPending(null);
  }, [server]);
  const list = pending?.layouts ?? server;
  const builtinId = list.find((l) => l.builtin)?.id ?? list[0]?.id;
  const active = pending?.activeId ?? (activeId && list.some((l) => l.id === activeId) ? activeId : builtinId);

  const put = (layout: NamedLayout) =>
    void outbox
      .enqueue({ method: "PUT", path: paths.traceLayout(customerId, layout.id), body: { name: layout.name, body: layout.body }, label: "Trace layout", coalesceKey: `trace:${layout.id}` })
      .then(() => invalidate(layoutsKey), () => undefined);
  const activate = (id: string) => void savePrefs(customerId, { active_trace_layout_id: id });

  const command = (c: TraceLayoutCommand) => {
    if (c.type === "select") {
      setPending({ layouts: list, activeId: c.id });
      activate(c.id);
    } else if (c.type === "create" || (c.type === "edit" && c.created)) {
      setPending({ layouts: [...list, c.layout], activeId: c.layout.id });
      put(c.layout);
      activate(c.layout.id);
    } else if (c.type === "edit") {
      setPending({ layouts: list.map((l) => (l.id === c.layout.id ? c.layout : l)), activeId: c.layout.id });
      put(c.layout);
    } else {
      const fallback = list.find((l) => l.builtin)?.id ?? "default";
      setPending({ layouts: list.filter((l) => l.id !== c.id), activeId: fallback });
      void outbox.enqueue({ method: "DELETE", path: paths.traceLayout(customerId, c.id), label: "Trace layout" }).then(() => invalidate(layoutsKey), () => undefined);
      activate(fallback);
    }
  };
  return { layouts: list.length ? list : undefined, activeId: active, command };
}

function Host({ mod, customerId, sessionId, view, query }: InvestigationProps & { mod: InvestigationModule }) {
  const detail = useResource(paths.session(sessionId), () => api.session(sessionId));
  const { prefs } = usePrefs(customerId);
  const reduced = useReducedMotion(prefs);
  const loader = useMemo(() => (loaderInstance ??= mod.createAnalysisLoader()), [mod]);
  const traceLayouts = useTraceLayouts(customerId, prefs?.active_trace_layout_id);
  const remember = useRememberedInvestigation(customerId);

  if (!detail.data) return detail.error ? <ErrorLine error={detail.error} /> : <Loading what="session" />;
  const analyses = detail.data.analyses;
  const analysis = (query.analysis ? analyses.find((a) => a.analysis_id === query.analysis) : undefined) ?? analyses.find((a) => a.current) ?? analyses[0];
  if (!analysis) return <Empty title="Not analyzed" />;

  const onSelectionChange = (s: Selection) => {
    const q = { comparison: s.comparisonId, section: s.sectionId, analysis: query.analysis ?? null };
    navigate(formatRoute({ page: "investigation", customerId, sessionId, view: s.view }, q), { replace: s.view === view });
    remember({ session_id: sessionId, analysis_id: analysis.analysis_id, view: s.view, comparison_id: s.comparisonId, section_id: s.sectionId });
  };

  const Workspace = mod.InvestigationWorkspace;
  return (
    <Workspace
      loader={loader}
      analysisId={analysis.analysis_id}
      view={view}
      comparisonId={query.comparison ?? null}
      sectionId={query.section ?? null}
      onSelectionChange={onSelectionChange}
      traceLayouts={traceLayouts.layouts}
      activeTraceLayoutId={traceLayouts.activeId}
      onTraceLayoutCommand={traceLayouts.command}
      reducedMotion={reduced}
      phases={phasesOf(detail.data)}
      currentSessionId={sessionId}
      onPhase={(id) => navigate({ page: "investigation", customerId, sessionId: id, view })}
    />
  );
}

let loadedModule: InvestigationModule | null = null;

export function InvestigationSlot(props: InvestigationProps) {
  const [mod, setMod] = useState<InvestigationModule | null>(loadedModule);
  const [error, setError] = useState<Error | null>(null);
  useEffect(() => {
    if (mod || !loadModule) return;
    loadModule().then(
      (m) => {
        loadedModule = m;
        setMod(m);
      },
      (e: unknown) => setError(e instanceof Error ? e : new Error(String(e))),
    );
  }, [mod]);
  if (!loadModule) return <Notice>Investigation is not in this build.</Notice>;
  if (error) return <ErrorLine error={error} />;
  if (!mod) return <Loading what="investigation" />;
  return <Host {...props} mod={mod} />;
}

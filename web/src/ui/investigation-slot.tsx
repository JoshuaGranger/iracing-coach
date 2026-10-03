import type { ComponentChildren, ComponentType } from "preact";
import { useEffect, useMemo, useState } from "preact/hooks";
import { type Schemas, api, paths } from "../core/api";
import { sessionTypeLabel } from "../core/format";
import { outbox } from "../core/outbox";
import { rememberInvestigation, savePrefs, usePrefs, useReducedMotion } from "../core/prefs";
import { invalidate, useResource } from "../core/resource";
import { type InvestigationView, formatRoute, navigate } from "../core/router";
import { newLayoutId } from "../core/ids";
import { Empty, ErrorLine, Loading, Notice } from "./kit";

/**
 * Mounts the investigation workspace (W8, `web/src/investigation/`). The shell resolves the session to its current
 * analysis, keeps the route, the last-investigation preference and the trace layouts in step with the workspace's
 * selection, and supplies the single session picker. The workspace makes the view requests itself (`createAnalysisLoader`).
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

/** The part of W8's contract the shell uses (`investigation/README.md`). */
interface WorkspaceProps {
  loader: unknown;
  analysisId: string;
  view?: InvestigationView;
  comparisonId?: string | null;
  sectionId?: string | null;
  onSelectionChange?(selection: Selection): void;
  traceLayout?: TraceLayoutBody;
  traceLayoutName?: string;
  onTraceLayoutChange?(body: TraceLayoutBody): void;
  reducedMotion?: boolean;
  sessionPicker?: ComponentChildren;
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

function SessionPicker({ customerId, sessionId, view, sessions }: { customerId: number; sessionId: number; view: InvestigationView; sessions: { session_id: number; session_type?: string | null }[] }) {
  if (sessions.length < 2) return null;
  return (
    <label class="select-field inline">
      <span class="visually-hidden">Session</span>
      <select value={sessionId} aria-label="Session" onChange={(e) => navigate({ page: "investigation", customerId, sessionId: Number((e.currentTarget as HTMLSelectElement).value), view })}>
        {sessions.map((s) => (
          <option key={s.session_id} value={s.session_id}>
            {sessionTypeLabel(s.session_type)}
          </option>
        ))}
      </select>
    </label>
  );
}

function Host({ mod, customerId, sessionId, view, query }: InvestigationProps & { mod: InvestigationModule }) {
  const detail = useResource(paths.session(sessionId), () => api.session(sessionId));
  const { prefs } = usePrefs(customerId);
  const layoutsKey = paths.traceLayouts(customerId);
  const layouts = useResource<LayoutList>(layoutsKey);
  const reduced = useReducedMotion(prefs);
  const loader = useMemo(() => (loaderInstance ??= mod.createAnalysisLoader()), [mod]);

  const list = layouts.data?.layouts ?? [];
  list.forEach((l) => outbox.observe(paths.traceLayout(customerId, l.layout_id), l.version));
  const active = list.find((l) => l.layout_id === prefs?.active_trace_layout_id) ?? list.find((l) => l.builtin) ?? list[0];

  if (!detail.data) return detail.error ? <ErrorLine error={detail.error} /> : <Loading what="session" />;
  const analyses = detail.data.analyses;
  const analysis = (query.analysis ? analyses.find((a) => a.analysis_id === query.analysis) : undefined) ?? analyses.find((a) => a.current) ?? analyses[0];
  if (!analysis) return <Empty title="Not analyzed yet">Analyze this session from the library to open it here.</Empty>;

  const onSelectionChange = (s: Selection) => {
    const q = { comparison: s.comparisonId, section: s.sectionId, analysis: query.analysis ?? null };
    navigate(formatRoute({ page: "investigation", customerId, sessionId, view: s.view }, q), { replace: s.view === view });
    rememberInvestigation(customerId, { session_id: sessionId, analysis_id: analysis.analysis_id, view: s.view, comparison_id: s.comparisonId, section_id: s.sectionId });
  };

  const onTraceLayoutChange = (body: TraceLayoutBody) => {
    if (!active) return;
    if (active.builtin) {
      const id = newLayoutId("my-layout", list.map((l) => l.layout_id));
      void outbox
        .enqueue({ method: "PUT", path: paths.traceLayout(customerId, id), body: { name: "My layout", body }, label: "Trace layout" })
        .then(() => {
          invalidate(layoutsKey);
          return savePrefs(customerId, { active_trace_layout_id: id });
        }, () => undefined);
    } else {
      void outbox
        .enqueue({ method: "PUT", path: paths.traceLayout(customerId, active.layout_id), body: { name: active.name, body }, ifMatch: String(active.version), label: "Trace layout", coalesceKey: `trace:${active.layout_id}` })
        .then(() => invalidate(layoutsKey), () => undefined);
    }
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
      traceLayout={active ? (active.body as TraceLayoutBody) : undefined}
      traceLayoutName={active?.name}
      onTraceLayoutChange={onTraceLayoutChange}
      reducedMotion={reduced}
      sessionPicker={<SessionPicker customerId={customerId} sessionId={sessionId} view={view} sessions={detail.data.event_sessions ?? []} />}
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
  if (!loadModule) return <Notice>The investigation workspace is not installed in this build.</Notice>;
  if (error) return <ErrorLine error={error} />;
  if (!mod) return <Loading what="investigation" />;
  return <Host {...props} mod={mod} />;
}

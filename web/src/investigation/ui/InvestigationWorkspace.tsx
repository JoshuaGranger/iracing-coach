import type { ComponentChildren } from "preact";
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import type { TelemetryCursor } from "../cursor/telemetry-cursor";
import { buildMapGeometry } from "../geometry/map-model";
import { selectedComparison, selectedSection, type Comparison } from "../investigation-model";
import { criticalLayoutBody } from "../layout-model";
import { HttpError, ProjectionPendingError, type AnalysisLoader } from "../load";
import { INVESTIGATION_VIEWS, type InvestigationView, type TraceLayoutBody } from "../types";
import "./investigation.css";
import { useAsync } from "./hooks";
import { RaceView } from "./RaceView";
import { RunsView } from "./RunsView";
import { SectionsView } from "./SectionsView";
import { TracesView, type TraceFocus } from "./TracesView";

export interface InvestigationSelection {
  view: InvestigationView;
  comparisonId: string | null;
  sectionId: string | null;
}

export interface InvestigationWorkspaceProps {
  loader: AnalysisLoader;
  analysisId: string;
  view?: InvestigationView;
  comparisonId?: string | null;
  sectionId?: string | null;
  /** Called when the view, comparison or section changes (the shell stores it as `last_investigation`). */
  onSelectionChange?(selection: InvestigationSelection): void;
  traceLayout?: TraceLayoutBody;
  traceLayoutName?: string;
  onTraceLayoutChange?(body: TraceLayoutBody): void;
  reducedMotion?: boolean;
  /** The shell's single session picker, shown in the bar. */
  sessionPicker?: ComponentChildren;
  /** Called with the live cursor (diagnostics, tests). */
  onCursor?(cursor: TelemetryCursor | null): void;
}

const VIEW_LABELS: Record<InvestigationView, string> = { race: "Race", runs: "Runs", sections: "Sections", traces: "Traces" };

function describe(error: Error): { title: string; detail: string; pending: boolean } {
  if (error instanceof ProjectionPendingError) return { title: "Preparing this analysis", detail: "Its views are being built. They open when ready.", pending: true };
  if (error instanceof HttpError && error.status === 404) return { title: "Analysis not found", detail: error.message, pending: false };
  return { title: "This analysis could not be read", detail: error.message, pending: false };
}

function Notice(props: { title: string; detail: string; retry?: () => void }) {
  return (
    <div class="ri-panel ri-empty" role="status">
      <strong>{props.title}</strong>
      <span>{props.detail}</span>
      {props.retry && <button type="button" class="ri-button" onClick={props.retry}>Try again</button>}
    </div>
  );
}

export function InvestigationWorkspace(props: InvestigationWorkspaceProps) {
  const handle = useMemo(() => props.loader.get(props.analysisId), [props.loader, props.analysisId]);
  const [view, setView] = useState<InvestigationView>(props.view ?? "race");
  const [comparisonId, setComparisonId] = useState<string | null>(props.comparisonId ?? null);
  const [sectionId, setSectionId] = useState<string | null>(props.sectionId ?? null);
  const [focus, setFocus] = useState<TraceFocus | null>(null);
  const [localLayout, setLocalLayout] = useState<TraceLayoutBody>(() => props.traceLayout ?? criticalLayoutBody());
  const layout = props.traceLayout ?? localLayout;
  const layoutRef = useRef(layout);
  layoutRef.current = layout;

  useEffect(() => { if (props.view) setView(props.view); }, [props.view]);
  useEffect(() => { if (props.comparisonId !== undefined) setComparisonId(props.comparisonId); }, [props.comparisonId]);
  useEffect(() => { if (props.sectionId !== undefined) setSectionId(props.sectionId); }, [props.sectionId]);
  useEffect(() => { setFocus(null); }, [props.analysisId]);

  const select = (next: Partial<InvestigationSelection>): void => {
    const merged: InvestigationSelection = { view, comparisonId, sectionId, ...next };
    if (next.view !== undefined) setView(next.view);
    if (next.comparisonId !== undefined) setComparisonId(next.comparisonId);
    if (next.sectionId !== undefined) setSectionId(next.sectionId);
    props.onSelectionChange?.(merged);
  };

  const overview = useAsync(() => handle.overview(), handle);
  const traces = useAsync(() => (view === "traces" ? handle.traces() : Promise.resolve(null)), `${props.analysisId}|${view === "traces"}`);
  const overviewValue = overview.state.status === "ready" ? overview.state.value : null;
  const inv = overviewValue?.investigation ?? null;
  const comparison: Comparison | null = inv ? selectedComparison(inv, comparisonId) : null;
  const section = inv ? selectedSection(inv, comparison, sectionId) : null;
  const map = useMemo(() => buildMapGeometry(overviewValue?.track ?? null), [overviewValue?.track]);

  const openTraces = (request: TraceFocus): void => {
    setFocus(request);
    select({ view: "traces" });
  };

  let body: ComponentChildren;
  if (overview.state.status === "loading") {
    body = <Notice title="Loading analysis" detail="Reading the race, runs and sections." />;
  } else if (overview.state.status === "error") {
    const info = describe(overview.state.error);
    body = <Notice title={info.title} detail={info.detail} retry={() => { handle.reset(); overview.retry(); }} />;
  } else if (view === "traces") {
    if (traces.state.status === "loading") body = <Notice title="Loading traces" detail="Reading the lap traces." />;
    else if (traces.state.status === "error") {
      const info = describe(traces.state.error);
      body = <Notice title={info.title === "This analysis could not be read" ? "Detailed traces could not be read" : info.title} detail={info.detail} retry={() => { handle.reset(); traces.retry(); }} />;
    } else if (traces.state.value) {
      const data = traces.state.value;
      const ov = overview.state.value;
      body = (
        <TracesView
          key={props.analysisId} data={data} map={map} race={ov.race} runs={ov.runs.runs as { run_number?: unknown; green_laps?: unknown; caution_laps?: unknown }[]}
          layout={layout} layoutName={props.traceLayoutName ?? "My traces"}
          onLayoutChange={(next) => { setLocalLayout(next); props.onTraceLayoutChange?.(next); }}
          ensureSignals={(ids) => handle.ensureSignals(ids)} focus={focus} onClearFocus={() => setFocus(null)}
          analysisKey={props.analysisId} reducedMotion={props.reducedMotion ?? false} onCursor={props.onCursor}
        />
      );
    }
  } else if (!inv) {
    body = (
      <Notice
        title={overview.state.value.investigationError ? "This investigation could not be read" : "No investigation for this analysis"}
        detail={overview.state.value.investigationError ?? "The backend produced no race investigation. Traces are still available."}
      />
    );
  } else if (view === "race") {
    body = (
      <RaceView
        inv={inv} selected={comparison}
        onSelectComparison={(id) => select({ comparisonId: id })}
        onInspect={(action) => {
          if (action.sectionId) select({ view: "sections", comparisonId: comparison?.id ?? null, sectionId: action.sectionId });
          else openTraces({ laps: action.lapNumbers, startPct: action.startPct, endPct: action.endPct, signalKeys: action.signalKeys });
        }}
      />
    );
  } else if (view === "runs") {
    body = <RunsView inv={inv} selected={comparison} />;
  } else {
    body = (
      <SectionsView
        inv={inv} comparison={comparison} section={section} map={map} onSelectSection={(id) => select({ view: "sections", sectionId: id })}
        onOpenTraces={(request) => openTraces({ laps: request.laps, startPct: request.startPct, endPct: request.endPct, signalKeys: request.signalKeys })}
      />
    );
  }

  const showComparisons = inv !== null && inv.comparisons.length > 1 && view !== "traces";
  return (
    <section class="ri-workspace" aria-label="Race investigation" data-view={view}>
      <header class="ri-bar">
        <div class="ri-tabs" role="tablist" aria-label="Investigation views">
          {INVESTIGATION_VIEWS.map((name) => (
            <button type="button" role="tab" key={name} class="ri-tab" aria-selected={view === name} data-view-tab={name} onClick={() => select({ view: name })}>{VIEW_LABELS[name]}</button>
          ))}
        </div>
        {showComparisons && (
          <label class="ri-select"><span>Compare</span>
            <select value={comparison?.id ?? ""} onChange={(event) => select({ comparisonId: (event.currentTarget as HTMLSelectElement).value })}>
              {inv!.comparisons.map((choice) => <option key={choice.id} value={choice.id}>{choice.title}</option>)}
            </select>
          </label>
        )}
        {props.sessionPicker && <div class="ri-session-picker">{props.sessionPicker}</div>}
      </header>
      <div class="ri-body">{body}</div>
    </section>
  );
}

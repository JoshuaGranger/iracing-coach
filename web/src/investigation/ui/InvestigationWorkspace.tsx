import type { ComponentChildren } from "preact";
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import type { TelemetryCursor } from "../cursor/telemetry-cursor";
import { buildMapGeometry } from "../geometry/map-model";
import { selectedComparison, selectedSection, type Comparison } from "../investigation-model";
import { criticalLayoutBody } from "../layout-model";
import { activeLayout, createLayout, defaultLayout, deleteActiveLayout, duplicateLayout, editActiveLayout, selectLayout, type LayoutSet, type NamedLayout } from "../layout-sets";
import { HttpError, ProjectionPendingError, type AnalysisLoader } from "../load";
import { asRecord, INVESTIGATION_VIEWS, type InvestigationView, type TraceLayoutBody } from "../types";
import "./investigation.css";
import { useViewportFit } from "./fit";
import { useAsync } from "./hooks";
import { RaceView, type RaceResult } from "./RaceView";
import { ResultsSummaryView } from "./ResultsSummaryView";
import { FieldRaceView } from "./FieldRaceView";
import { RaceComparisonView } from "./RaceComparisonView";
import { fieldResults, hasFieldProgression } from "./field-results";
import { RunsView } from "./RunsView";
import { SectionsView, type SectionsOpenRequest } from "./SectionsView";
import { PhaseSelector, type PhaseOption } from "./PhaseSelector";
import type { LayoutCommandRequest } from "./TraceToolbox";
import { TracesView, type TraceFocus } from "./TracesView";

/** A saved-layout change for the shell to persist (`trace-layouts` PUT and DELETE, `active_trace_layout_id`). */
export type TraceLayoutCommand =
  | { type: "select"; id: string }
  | { type: "create"; layout: NamedLayout }
  | { type: "edit"; layout: NamedLayout; created: boolean }
  | { type: "delete"; id: string };

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
  /** The shell's single session picker, shown in the bar when no `phases` are given. */
  sessionPicker?: ComponentChildren;
  /** The event's phases for the P · Q · R selector (replaces `sessionPicker`). */
  phases?: PhaseOption[];
  currentSessionId?: number;
  onPhase?(sessionId: number): void;
  /** The driver's saved layouts, the built-in Default included; when absent the workspace keeps them for the visit. */
  traceLayouts?: NamedLayout[];
  activeTraceLayoutId?: string;
  onTraceLayoutCommand?(command: TraceLayoutCommand): void;
  /** Called with the live cursor (diagnostics, tests). */
  onCursor?(cursor: TelemetryCursor | null): void;
  /** The session's official start and retirement for the Race chart (`SessionOverview`, #96 #97); else the race view's own. */
  raceResult?: RaceResult | null;
}

const SHELL_LAYOUT_ID = "shell-active";

function initialSet(body: TraceLayoutBody | undefined, name: string | undefined): LayoutSet {
  const base = defaultLayout(criticalLayoutBody());
  if (!body) return { layouts: [base], activeId: base.id };
  return { layouts: [base, { id: SHELL_LAYOUT_ID, name: name ?? "My traces", builtin: false, body }], activeId: SHELL_LAYOUT_ID };
}

/** The recording's session type from the race view's identity (`event_type`), for Offline Testing wording (audit R7). */
function sessionTypeOf(race: { identity?: unknown }): string | null {
  const identity = race.identity as Record<string, unknown> | undefined;
  return typeof identity?.event_type === "string" ? identity.event_type : null;
}

const VIEW_LABELS: Record<InvestigationView, string> = { race: "Race", runs: "Runs", sections: "Sections", traces: "Traces", results: "Results Summary" };

function describe(error: Error): { title: string; detail: string; pending: boolean } {
  if (error instanceof ProjectionPendingError) return { title: "Preparing", detail: "", pending: true };
  if (error instanceof HttpError && error.status === 404) return { title: "Analysis not found", detail: error.message, pending: false };
  return { title: "Analysis unreadable", detail: error.message, pending: false };
}

function Notice(props: { title: string; detail: string; retry?: () => void }) {
  return (
    <div class="ri-panel ri-empty" role="status">
      <strong>{props.title}</strong>
      {props.detail && <span>{props.detail}</span>}
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
  const [compare, setCompare] = useState(false);
  const [selectedCar, setSelectedCar] = useState<number | null>(null);
  // Saved layouts: the shell's list when it gives one; otherwise Default plus the shell's active layout, kept for the visit.
  const [localSet, setLocalSet] = useState<LayoutSet>(() => initialSet(props.traceLayout, props.traceLayoutName));
  useEffect(() => {
    if (!props.traceLayout) return;
    setLocalSet((current) => current.layouts.some((layout) => layout.id === SHELL_LAYOUT_ID)
      ? { ...current, layouts: current.layouts.map((layout) => (layout.id === SHELL_LAYOUT_ID ? { ...layout, name: props.traceLayoutName ?? layout.name, body: props.traceLayout! } : layout)) }
      : initialSet(props.traceLayout, props.traceLayoutName));
  }, [props.traceLayout, props.traceLayoutName]);
  const shellSet: LayoutSet | null = props.traceLayouts && props.traceLayouts.length
    ? { layouts: props.traceLayouts, activeId: props.activeTraceLayoutId ?? props.traceLayouts.find((layout) => layout.builtin)?.id ?? props.traceLayouts[0]!.id }
    : null;
  const layoutSet = shellSet ?? localSet;
  const layoutSetRef = useRef(layoutSet);
  layoutSetRef.current = layoutSet;
  const workspaceRef = useRef<HTMLElement>(null);
  useViewportFit(workspaceRef);

  useEffect(() => { if (props.view) setView(props.view); }, [props.view]);
  useEffect(() => { if (props.comparisonId !== undefined) setComparisonId(props.comparisonId); }, [props.comparisonId]);
  useEffect(() => { if (props.sectionId !== undefined) setSectionId(props.sectionId); }, [props.sectionId]);
  useEffect(() => { setFocus(null); }, [props.analysisId]);
  useEffect(() => { setSelectedCar(null); }, [props.analysisId]);

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
  const field = useMemo(() => fieldResults(asRecord(overviewValue?.race.field_pace).results), [overviewValue]);
  const fieldCar = field.cars.find((car) => car.index === selectedCar) ?? field.cars.find((car) => car.player);
  const playerCar = field.cars.find((car) => car.player);
  const competitors = field.cars.filter((car) => !car.player && hasFieldProgression(car));
  const opponent = fieldCar && !fieldCar.player ? fieldCar : competitors[0];
  const canCompare = !!playerCar && hasFieldProgression(playerCar) && competitors.length > 0;
  const carPicker = <label class="ri-field-picker">Car
    {field.unavailableReason && <span role="status">{field.unavailableReason}</span>}
    <select aria-label="Race car" value={fieldCar?.index ?? "player"} onChange={(event) => setSelectedCar(event.currentTarget.value === "player" ? null : Number(event.currentTarget.value))}>
      {!field.cars.some((car) => car.player) && <option value="player">You</option>}
      {field.cars.filter((car) => car.player || hasFieldProgression(car)).map((car) => <option key={car.index} value={car.index}>{car.position === null ? "—" : `P${car.position}`} · #{car.number || "—"} {car.name}{car.player ? " · You" : ""}</option>)}
    </select>
  </label>;
  const comparison: Comparison | null = inv ? selectedComparison(inv, comparisonId) : null;
  const section = inv ? selectedSection(inv, comparison, sectionId) : null;
  const map = useMemo(() => buildMapGeometry(overviewValue?.track ?? null), [overviewValue?.track]);

  const commit = (next: LayoutSet, command: TraceLayoutCommand): void => {
    if (!shellSet) setLocalSet(next);
    props.onTraceLayoutCommand?.(command);
  };
  const editLayout = (body: TraceLayoutBody): void => {
    const set = layoutSetRef.current;
    const active = activeLayout(set);
    // The shell's own active layout keeps its save path (it makes a copy of a built-in itself).
    if (!shellSet && active.id === SHELL_LAYOUT_ID) {
      setLocalSet({ ...set, layouts: set.layouts.map((layout) => (layout.id === SHELL_LAYOUT_ID ? { ...layout, body } : layout)) });
      props.onTraceLayoutChange?.(body);
      return;
    }
    const result = editActiveLayout(set, body);
    commit(result.set, { type: "edit", layout: result.layout, created: result.created });
  };
  const layoutCommand = (request: LayoutCommandRequest): void => {
    const set = layoutSetRef.current;
    if (request.type === "select") commit(selectLayout(set, request.id), { type: "select", id: request.id });
    else if (request.type === "new") { const result = createLayout(set); commit(result.set, { type: "create", layout: result.layout }); }
    else if (request.type === "copy") { const result = duplicateLayout(set); commit(result.set, { type: "create", layout: result.layout }); }
    else {
      const result = deleteActiveLayout(set);
      if (result) commit(result.set, { type: "delete", id: result.deletedId });
    }
  };

  const openTraces = (request: TraceFocus): void => {
    setFocus(request);
    select({ view: "traces" });
  };
  // Sections › Open in Traces starts the Traces cursor at the section start (the focus region alone centres it).
  const pendingCursor = useRef<number | null>(null);
  const openSectionInTraces = (request: SectionsOpenRequest): void => {
    pendingCursor.current = request.cursorPct;
    openTraces({ laps: request.laps, startPct: request.startPct, endPct: request.endPct, signalKeys: request.signalKeys });
  };
  const traceCursor = (cursor: TelemetryCursor | null): void => {
    if (cursor && pendingCursor.current !== null) {
      cursor.setFraction(pendingCursor.current);
      pendingCursor.current = null;
    }
    props.onCursor?.(cursor);
  };

  let body: ComponentChildren;
  if (overview.state.status === "loading") {
    body = <Notice title="Loading" detail="" />;
  } else if (overview.state.status === "error") {
    const info = describe(overview.state.error);
    body = <Notice title={info.title} detail={info.detail} retry={() => { handle.reset(); overview.retry(); }} />;
  } else if (view === "traces") {
    if (traces.state.status === "loading") body = <Notice title="Loading traces" detail="" />;
    else if (traces.state.status === "error") {
      const info = describe(traces.state.error);
      body = <Notice title={info.title === "Analysis unreadable" ? "Traces unreadable" : info.title} detail={info.detail} retry={() => { handle.reset(); traces.retry(); }} />;
    } else if (traces.state.value) {
      const data = traces.state.value;
      const ov = overview.state.value;
      body = (
        <TracesView
          key={props.analysisId} data={data} map={map} race={ov.race} runs={ov.runs.runs as unknown[]}
          layoutSet={layoutSet} onLayoutChange={editLayout} onLayoutCommand={layoutCommand}
          ensureSignals={(ids) => handle.ensureSignals(ids)} focus={focus} onClearFocus={() => setFocus(null)}
          analysisKey={props.analysisId} reducedMotion={props.reducedMotion ?? false} onCursor={traceCursor} comparison={comparison}
        />
      );
    }
  } else if (view === "results") {
    body = <ResultsSummaryView results={field} onSelectCar={(index) => { setSelectedCar(index); select({ view: "race" }); }} />;
  } else if (view === "race" && compare && canCompare && playerCar && opponent) {
    body = <RaceComparisonView key={`${props.analysisId}-${opponent.index}`} player={playerCar} competitor={opponent} />;
  } else if (view === "race" && fieldCar && !fieldCar.player) {
    body = <div class="ri-field-host"><FieldRaceView car={fieldCar} /></div>;
  } else if (!inv) {
    body = (
      <Notice
        title={overview.state.value.investigationError ? "Investigation unreadable" : "No investigation"}
        detail={overview.state.value.investigationError ?? ""}
      />
    );
  } else if (view === "race") {
    body = (
      <div class="ri-field-host"><RaceView inv={inv} race={overview.state.value.race} runs={overview.state.value.runs.runs as unknown[]} sessionType={sessionTypeOf(overview.state.value.race)} result={props.raceResult} /></div>
    );
  } else if (view === "runs") {
    body = (
      <RunsView
        inv={inv} race={overview.state.value.race} sessionType={sessionTypeOf(overview.state.value.race)} runs={overview.state.value.runs.runs as unknown[]}
        analysisKey={props.analysisId}
      />
    );
  } else {
    body = (
      <SectionsView
        inv={inv} section={section} map={map} sectionLaps={overview.state.value.sectionLaps ?? null} onSelectSection={(id) => select({ view: "sections", sectionId: id })}
        onOpenTraces={openSectionInTraces} traces={(ids) => handle.ensureSignals(ids)} analysisKey={props.analysisId}
        race={overview.state.value.race} runs={overview.state.value.runs.runs as unknown[]}
      />
    );
  }

  return (
    <section ref={workspaceRef} class="ri-workspace" aria-label="Race investigation" data-view={view}>
      <header class="ri-bar">
        <div class="ri-tabs" role="tablist" aria-label="Investigation views">
          {INVESTIGATION_VIEWS.map((name) => (
            <button type="button" role="tab" key={name} class="ri-tab" aria-selected={view === name} data-view-tab={name} onClick={() => select({ view: name })}>{VIEW_LABELS[name]}</button>
          ))}
        </div>
        {(view === "race" || view === "results") && <div class="ri-race-controls">{carPicker}{view === "race" && canCompare && <button type="button" class="ri-button" aria-pressed={compare} onClick={() => setCompare(!compare)}>Compare</button>}</div>}
        {props.phases && props.phases.length > 1 && props.currentSessionId !== undefined && props.onPhase
          ? <PhaseSelector phases={props.phases} currentSessionId={props.currentSessionId} onSelect={props.onPhase} />
          : props.sessionPicker && <div class="ri-session-picker">{props.sessionPicker}</div>}
      </header>
      <div class="ri-body">{body}</div>
    </section>
  );
}

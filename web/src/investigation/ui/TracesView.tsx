import { useCallback, useEffect, useMemo, useRef, useState } from "preact/hooks";
import { createContextSplitter } from "../cursor/context-splitter";
import type { TelemetryCursor } from "../cursor/telemetry-cursor";
import { createTraceLayoutEditor, type TraceLayoutEditor } from "../cursor/trace-layout-editor";
import type { MapGeometry, MapMode } from "../geometry/map-model";
import { bestThree, fastestLap, hasUsableLapTime, isGreenLap, resolve, type TraceData } from "../geometry/trace-model";
import { applyLayoutAction, insertSignalRow, moveRow, placeSignal, removeRow, removeSignal, validateAndRepair, type LayoutAction } from "../layout-model";
import type { RaceViewData, TraceLayoutBody, TraceRowSpec } from "../types";
import { asArray, asRecord, num } from "../types";
import { LapRail, passesFilters } from "./LapRail";
import { TraceChart } from "./TraceChart";
import { TraceToolbox } from "./TraceToolbox";
import { TrackPanel, type TrackRefs } from "./TrackPanel";

export interface TraceFocus {
  laps: number[];
  startPct: number | null;
  endPct: number | null;
  signalKeys: string[];
}

export interface TracesViewProps {
  data: TraceData;
  map: MapGeometry | null;
  race: RaceViewData;
  runs: { run_number?: unknown; green_laps?: unknown; caution_laps?: unknown }[];
  layout: TraceLayoutBody;
  layoutName: string;
  onLayoutChange(body: TraceLayoutBody): void;
  ensureSignals(ids: readonly string[]): Promise<TraceData>;
  focus: TraceFocus | null;
  onClearFocus(): void;
  analysisKey: string;
  reducedMotion: boolean;
  onCursor?(cursor: TelemetryCursor | null): void;
}

/** Incident points per lap from the race view's damage and repair events (candidate_lap, points_added). */
export function incidentPointsByLap(race: RaceViewData): Map<number, number> {
  const out = new Map<number, number>();
  const events = asArray(asRecord(asRecord(race.damage_repair).incident_points).events);
  for (const event of events) {
    const record = asRecord(event);
    const lap = num(record.candidate_lap);
    const points = num(record.points_added);
    if (lap !== null && points !== null) out.set(lap, (out.get(lap) ?? 0) + points);
  }
  return out;
}

function focusRows(data: TraceData, focus: TraceFocus, maximum: number): TraceLayoutBody | null {
  const ids = [...new Set(focus.signalKeys)].filter((key) => resolve(data, key) !== null).slice(0, maximum);
  if (ids.length === 0) return null;
  return { rows: ids.map((id) => ({ row_id: `focus-${id}`, signals: [id] })) };
}

export function TracesView(props: TracesViewProps) {
  const { data, race } = props;
  const maximumRows = data.budgets.max_trace_rows;
  const incidents = useMemo(() => incidentPointsByLap(race), [race]);
  const [selected, setSelected] = useState<number[]>(() => (props.focus?.laps.length ? props.focus.laps.filter((lap) => data.lapIndex.has(lap)) : bestThree(data, { incidentPoints: incidents })));
  const [spotlight, setSpotlight] = useState<number | null>(null);
  const [greenOnly, setGreenOnly] = useState(false);
  const [cleanOnly, setCleanOnly] = useState(false);
  const [mode, setMode] = useState<MapMode>("traces");
  const [editing, setEditing] = useState(false);
  const [selectedRowId, setSelectedRowId] = useState<string | null>(null);
  const [, setTick] = useState(0);
  const trackRefs: TrackRefs = { svg: useRef<SVGSVGElement>(null), marker: useRef<HTMLDivElement>(null), percent: useRef<HTMLElement>(null), summary: useRef<HTMLElement>(null) };
  const context = useRef<HTMLDivElement>(null);
  const splitter = useRef<HTMLDivElement>(null);
  const studio = useRef<HTMLDivElement>(null);
  const editor = useRef<TraceLayoutEditor | null>(null);
  const layoutRef = useRef<TraceLayoutBody>(props.layout);
  const propsRef = useRef(props);
  propsRef.current = props;

  const userLayout = useMemo(() => validateAndRepair(props.layout).body, [props.layout]);
  layoutRef.current = userLayout;
  const focusLayout = useMemo(() => (props.focus ? focusRows(data, props.focus, maximumRows) : null), [props.focus, data, maximumRows]);
  const layout = focusLayout ?? userLayout;
  const rows: readonly TraceRowSpec[] = layout.rows;
  const region = props.focus && props.focus.startPct !== null && props.focus.endPct !== null ? { start: props.focus.startPct, end: props.focus.endPct } : null;

  // Selected laps outside the data (a new analysis) are dropped; an empty selection falls back to best three.
  useEffect(() => {
    setSelected((current) => {
      const valid = current.filter((lap) => data.lapIndex.has(lap));
      return valid.length === current.length ? current : valid.length ? valid : bestThree(data, { incidentPoints: incidents });
    });
  }, [data, incidents]);

  useEffect(() => {
    if (!context.current || !splitter.current) return undefined;
    const handle = createContextSplitter(context.current, splitter.current);
    return () => handle.dispose();
  }, []);

  const applyAction = useCallback(async (action: LayoutAction): Promise<boolean> => {
    const current = propsRef.current;
    if (action.type !== "move-row") await current.ensureSignals([action.signalId]);
    const next = applyLayoutAction(layoutRef.current, action);
    if (!next || next.rows.length > current.data.budgets.max_trace_rows) return false;
    setTick((value) => value + 1);
    current.onLayoutChange(next);
    return true;
  }, []);

  useEffect(() => {
    if (!studio.current) return undefined;
    editor.current = createTraceLayoutEditor(studio.current, applyAction, { editing: false, reducedMotion: props.reducedMotion, rowCount: rows.length, maximumRows });
    return () => {
      editor.current?.dispose();
      editor.current = null;
    };
    // The editor listens on one element for the life of the view; later state reaches it through configure.
  }, [applyAction]);

  useEffect(() => {
    editor.current?.configure({ editing: editing && !props.focus, reducedMotion: props.reducedMotion, rowCount: rows.length, maximumRows });
  }, [editing, props.focus, props.reducedMotion, rows.length, maximumRows]);

  const toggle = (lap: number): void => {
    setSelected((current) => (current.includes(lap) ? current.filter((value) => value !== lap) : [...current, lap].sort((a, b) => a - b)));
    setSpotlight((current) => (current === lap ? null : current));
  };
  const applyFilters = (green: boolean, clean: boolean): void => {
    setGreenOnly(green);
    setCleanOnly(clean);
    setSelected((current) => {
      const kept = current.filter((lap) => {
        const meta = data.lapMeta.get(lap);
        return meta && passesFilters(meta, green, clean, incidents.get(lap) ?? 0);
      });
      if (kept.length) return kept;
      const fastest = data.laps
        .map((lap) => data.lapMeta.get(lap))
        .filter((meta): meta is NonNullable<typeof meta> => hasUsableLapTime(meta) && passesFilters(meta!, green, clean, incidents.get(meta!.lap) ?? 0))
        .sort((a, b) => (a.lap_time_s as number) - (b.lap_time_s as number) || a.lap - b.lap)[0];
      return fastest ? [fastest.lap] : [];
    });
    setSpotlight((current) => (current !== null && data.lapMeta.get(current) && passesFilters(data.lapMeta.get(current)!, green, clean, incidents.get(current) ?? 0) ? current : null));
  };
  const runMetrics = useMemo(() => {
    const out = new Map<number, { green: number | null; caution: number | null }>();
    for (const run of props.runs) {
      const id = num(run.run_number);
      if (id === null) continue;
      const count = (value: unknown): number | null => (Array.isArray(value) ? value.length : num(value));
      out.set(id, { green: count(run.green_laps), caution: count(run.caution_laps) });
    }
    return out;
  }, [props.runs]);

  const spotlightCandidates = useMemo(() => data.laps.map((lap) => data.lapMeta.get(lap)).filter((meta): meta is NonNullable<typeof meta> => !!meta && isGreenLap(meta) && hasUsableLapTime(meta)).sort((a, b) => a.lap - b.lap), [data]);
  const edit = (next: TraceLayoutBody | null): void => {
    if (next) props.onLayoutChange(next);
  };
  const addSignal = async (signalId: string): Promise<void> => {
    await props.ensureSignals([signalId]);
    edit(insertSignalRow(layoutRef.current, signalId, layoutRef.current.rows.length));
    setTick((value) => value + 1);
  };
  const placeOnRow = async (rowId: string, signalId: string): Promise<void> => {
    await props.ensureSignals([signalId]);
    edit(placeSignal(layoutRef.current, rowId, signalId));
    setTick((value) => value + 1);
  };
  const trackPoints = props.map?.points ?? [];

  return (
    <div class="ri-traces" data-ri-view="traces">
      <div class="ri-context-column" ref={context}>
        <TrackPanel data={data} map={props.map} mode={mode} onModeChange={setMode} selectedLaps={selected} refs={trackRefs} analysisKey={props.analysisKey} sectors={data.catalog.sector_start_pcts ?? []} />
        <div class="ri-context-splitter" ref={splitter} role="separator" aria-label="Resize Track position and Laps and runs" aria-orientation="horizontal" aria-valuemin={33} aria-valuemax={67} aria-valuenow={43} tabIndex={0}><span aria-hidden="true" /></div>
        <LapRail
          data={data} selected={new Set(selected)} greenOnly={greenOnly} cleanOnly={cleanOnly} incidentPoints={incidents} runMetrics={runMetrics}
          onToggle={toggle} onGreenOnly={(value) => applyFilters(value, cleanOnly)} onCleanOnly={(value) => applyFilters(greenOnly, value)}
          onFastest={() => { const lap = fastestLap(data, { incidentPoints: incidents }); setSelected(lap === null ? [] : [lap]); setSpotlight(null); }}
          onBestThree={() => { setSelected(bestThree(data, { incidentPoints: incidents })); setSpotlight(null); }}
          onClear={() => { setSelected([]); setSpotlight(null); }}
          onShowAll={() => { setGreenOnly(false); setCleanOnly(false); }}
        />
      </div>
      <div class={`ri-studio${editing ? " editing" : ""}`} ref={studio}>
        <div class="ri-panel ri-studio-panel">
          <div class="ri-panel-title ri-trace-title">
            <div class="ri-heading"><strong>Aligned traces</strong><span>{selected.length} selected · hover to compare · scroll to see more laps</span></div>
            <div class="ri-panel-actions">
              {props.focus && (
                <>
                  <button type="button" class="ri-button active" onClick={props.onClearFocus}>Restore my traces</button>
                  {region && <span class="ri-fine">Inspect {(region.start * 100).toFixed(0)}–{(region.end * 100).toFixed(0)}% of lap</span>}
                </>
              )}
              <label class="ri-select"><span>Spotlight lap</span>
                <select value={spotlight ?? ""} onChange={(event) => { const value = (event.currentTarget as HTMLSelectElement).value; setSpotlight(value === "" ? null : Number(value)); if (value !== "" && !selected.includes(Number(value))) setSelected((current) => [...current, Number(value)].sort((a, b) => a - b)); }}>
                  <option value="">None</option>
                  {spotlightCandidates.map((meta) => <option key={meta.lap} value={meta.lap}>Lap {meta.lap}{meta.lap_time_s ? ` · ${meta.lap_time_s.toFixed(3)}` : ""}</option>)}
                </select>
              </label>
              <button type="button" class={`ri-button${editing ? " active" : ""}`} disabled={props.focus !== null} aria-expanded={editing} aria-controls="analysis-trace-toolbox" title={props.focus ? "Restore my traces to edit your saved layout" : "Customize trace charts"} onClick={() => setEditing((value) => !value)}>Customize</button>
            </div>
          </div>
          <div class="ri-studio-body">
            <TraceChart
              data={data} rows={rows} selected={selected} spotlight={spotlight} editing={editing && !props.focus} region={region} trackRefs={trackRefs}
              trackPoints={trackPoints} initialFraction={0.25} selectedRowId={selectedRowId} analysisKey={props.analysisKey}
              onSelectRow={setSelectedRowId} onRemoveRow={(id) => edit(removeRow(layoutRef.current, id))}
              onMoveRow={(id, direction) => edit(moveRow(layoutRef.current, id, direction))} onCursor={props.onCursor}
            />
            {editing && !props.focus && (
              <TraceToolbox
                data={data} rows={rows} maximumRows={maximumRows} layoutName={props.layoutName} selectedRowId={selectedRowId}
                onAddRow={(id) => void addSignal(id)} onPlace={(rowId, id) => void placeOnRow(rowId, id)}
                onRemoveSignal={(rowId, id) => edit(removeSignal(layoutRef.current, rowId, id))} onRemoveRow={(id) => edit(removeRow(layoutRef.current, id))}
                onClose={() => setEditing(false)}
              />
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

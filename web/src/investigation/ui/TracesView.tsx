import { useCallback, useEffect, useMemo, useRef, useState } from "preact/hooks";
import type { RefObject } from "preact";
import type { TelemetryCursor, TrackField } from "../cursor/telemetry-cursor";
import { createTraceLayoutEditor, type TraceLayoutEditor } from "../cursor/trace-layout-editor";
import type { MapGeometry, MapMode } from "../geometry/map-model";
import { incidentsByLap, inRaceStops, passesFilters, pitStops, railGroups, runWearInputs } from "../geometry/rail-model";
import { bestThree, fastestLap, hasUsableLapTime, isCompleteLap, lapColor, resolve, sectorStarts, setLapContext, zeroLapFor, type TraceData } from "../geometry/trace-model";
import type { TraceLapData } from "../types";
import { lapTime } from "../format";
import { applyLayoutAction, insertSignalRow, moveRow, placeSignal, removeRow, removeSignal, validateAndRepair, type LayoutAction } from "../layout-model";
import { activeLayout, type LayoutSet } from "../layout-sets";
import type { RaceViewData, TraceLayoutBody, TraceRowSpec } from "../types";
import type { Comparison } from "../investigation-model";
import { LapRail } from "./LapRail";
import { TraceChart } from "./TraceChart";
import { TraceToolbox, type LayoutCommandRequest } from "./TraceToolbox";
import { SingleSelect, type SelectOption } from "../../ui/single-select";
import { TrackPanel, type TrackRefs } from "./TrackPanel";

export { incidentPointsByLap } from "../geometry/rail-model";

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
  /** The runs view's runs (BackendRun), passed through: run grouping, pit service, tire observation. */
  runs: readonly unknown[];
  layoutSet: LayoutSet;
  /** An edit to the active layout's rows. */
  onLayoutChange(body: TraceLayoutBody): void;
  /** Select, New, Copy or Delete a saved layout. */
  onLayoutCommand(command: LayoutCommandRequest): void;
  ensureSignals(ids: readonly string[]): Promise<TraceData>;
  focus: TraceFocus | null;
  onClearFocus(): void;
  analysisKey: string;
  reducedMotion: boolean;
  onCursor?(cursor: TelemetryCursor | null): void;
  /** The workspace's selected comparison. Traces draws nothing of it (#66): the rail, the chart, the map and the cursor readouts. */
  comparison?: Comparison | null;
}

/** Focus rows after Race › Inspect or Sections › Open in Traces: the action's channels, at most six (the old workspace's limit). */
const FOCUS_ROWS = 6;
const SIGNAL_ALIASES: Record<string, string> = { "time-delta": "delta", time_delta: "delta", delta_s: "delta", speed_mph: "speed", steering_rad: "steering" };

export function focusRows(data: TraceData, focus: TraceFocus): TraceLayoutBody | null {
  const ids = [...new Set(focus.signalKeys.map((key) => SIGNAL_ALIASES[key] ?? key))].filter((key) => resolve(data, key) !== null).slice(0, FOCUS_ROWS);
  if (ids.length === 0) return null;
  return { rows: ids.map((id) => ({ row_id: `focus-${id}`, signals: [id] })) };
}

/** Channels a layout needs that the default set did not bring (fetched on mount and on layout change, audit T5). */
export function missingChannels(data: TraceData, rows: readonly TraceRowSpec[], mapMode: MapMode): string[] {
  const wanted = rows.flatMap((row) => row.signals).flatMap((id) => (id === "tire-wear" ? ["tire-stress"] : [id]));
  if (mapMode === "stress") wanted.push("tire-stress");
  return [...new Set(wanted)].filter((id) => data.signals.has(id) && !data.table.channels.has(id));
}

/**
 * The Spotlight list (#79): the laps the chart draws now, in the rail's order (run by run, lap by lap), each with its time. A lap
 * that is not on the chart is not offered, so spotlighting never adds a trace.
 */
export function spotlightLaps(data: TraceData, runs: readonly unknown[], selected: readonly number[]): TraceLapData[] {
  const drawn = new Set(selected);
  return railGroups(data, runs, (meta) => drawn.has(meta.lap)).flatMap((group) => group.laps);
}

export function TracesView(props: TracesViewProps) {
  const { data, race } = props;
  const maximumRows = data.budgets.max_trace_rows;
  const incidents = useMemo(() => incidentsByLap(race), [race]);
  const points = useMemo(() => new Map([...incidents].map(([lap, value]) => [lap, value.points] as const)), [incidents]);
  const wear = useMemo(() => runWearInputs({ runs: props.runs }), [props.runs]);
  // The lap rules read the race context from the data (fastest lap colour, sector colours, the zero lap).
  setLapContext(data, points, wear);
  const stops = useMemo(() => inRaceStops(pitStops(race, { runs: props.runs }, data)), [race, props.runs, data]);
  const [selected, setSelected] = useState<number[]>(() => (props.focus?.laps.length ? props.focus.laps.filter((lap) => data.lapIndex.has(lap)) : bestThree(data)));
  const [spotlight, setSpotlight] = useState<number | null>(null);
  const [greenOnly, setGreenOnly] = useState(false);
  const [cleanOnly, setCleanOnly] = useState(false);
  const [mode, setMode] = useState<MapMode>("traces");
  const [editing, setEditing] = useState(false);
  const [full, setFull] = useState(false);
  const [selectedRowId, setSelectedRowId] = useState<string | null>(null);
  const [loadTick, setLoadTick] = useState(0);
  /** Channels whose request failed; their rows say Failed and the header offers Retry (review finding 8). */
  const [failedChannels, setFailedChannels] = useState<string[]>([]);
  const [retryTick, setRetryTick] = useState(0);
  const fieldRefs: Record<TrackField, RefObject<HTMLElement>> = { speed: useRef<HTMLElement>(null), throttle: useRef<HTMLElement>(null), brake: useRef<HTMLElement>(null), delta: useRef<HTMLElement>(null) };
  const trackRefs: TrackRefs = { svg: useRef<SVGSVGElement>(null), marker: useRef<HTMLDivElement>(null), percent: useRef<HTMLElement>(null), summary: useRef<HTMLElement>(null), fields: fieldRefs };
  const studio = useRef<HTMLDivElement>(null);
  const card = useRef<HTMLDivElement>(null);
  const editor = useRef<TraceLayoutEditor | null>(null);
  const active = activeLayout(props.layoutSet);
  const layoutRef = useRef<TraceLayoutBody>(active.body);
  const propsRef = useRef(props);
  propsRef.current = props;

  const userLayout = useMemo(() => validateAndRepair(active.body).body, [active.body]);
  layoutRef.current = userLayout;
  const focusLayout = useMemo(() => (props.focus ? focusRows(data, props.focus) : null), [props.focus, data]);
  const layout = focusLayout ?? userLayout;
  const rows: readonly TraceRowSpec[] = layout.rows;
  const region = props.focus && props.focus.startPct !== null && props.focus.endPct !== null ? { start: props.focus.startPct, end: props.focus.endPct } : null;
  const zeroLap = useMemo(() => zeroLapFor(data, selected, points), [data, selected, points]);
  // The rail's Δ column reads against the same zero lap; with nothing selected, against the race's fastest lap under the same rule.
  const railZero = zeroLap ?? data.fastestLap;

  // Selected laps outside the data (a new analysis) are dropped; an empty selection falls back to best three.
  useEffect(() => {
    setSelected((current) => {
      const valid = current.filter((lap) => data.lapIndex.has(lap));
      return valid.length === current.length ? current : valid.length ? valid : bestThree(data);
    });
  }, [data]);

  const missing = missingChannels(data, rows, mode);
  const missingKey = missing.join(",");
  useEffect(() => {
    if (!missingKey) return;
    let live = true;
    const wanted = missingKey.split(",");
    setFailedChannels((current) => (current.some((id) => wanted.includes(id)) ? current.filter((id) => !wanted.includes(id)) : current));
    propsRef.current.ensureSignals(wanted).then(
      () => { if (live) setLoadTick((value) => value + 1); },
      () => { if (live) setFailedChannels((current) => [...new Set([...current, ...wanted])]); },
    );
    return () => { live = false; };
  }, [missingKey, data, retryTick]);
  const failedShown = failedChannels.filter((id) => missing.includes(id));

  const applyAction = useCallback(async (action: LayoutAction): Promise<boolean> => {
    const current = propsRef.current;
    if (action.type !== "move-row") await current.ensureSignals([action.signalId]);
    const next = applyLayoutAction(layoutRef.current, action);
    if (!next || next.rows.length > current.data.budgets.max_trace_rows) return false;
    setLoadTick((value) => value + 1);
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

  // Full screen: the traces card fills the window (and the screen where the browser allows it); Escape returns.
  useEffect(() => {
    if (!full) return undefined;
    const element = card.current;
    try { void element?.requestFullscreen?.().catch(() => undefined); } catch { /* not allowed here */ }
    const onKey = (event: KeyboardEvent): void => { if (event.key === "Escape") setFull(false); };
    const onChange = (): void => { if (!document.fullscreenElement) setFull(false); };
    window.addEventListener("keydown", onKey);
    document.addEventListener("fullscreenchange", onChange);
    return () => {
      window.removeEventListener("keydown", onKey);
      document.removeEventListener("fullscreenchange", onChange);
      if (document.fullscreenElement) void document.exitFullscreen?.().catch(() => undefined);
    };
  }, [full]);

  const toggle = (lap: number): void => {
    setSelected((current) => (current.includes(lap) ? current.filter((value) => value !== lap) : [...current, lap].sort((a, b) => a - b)));
    setSpotlight((current) => (current === lap ? null : current));
  };
  const allowed = (lap: number, green: boolean, clean: boolean): boolean => {
    const meta = data.lapMeta.get(lap);
    return meta !== undefined && passesFilters(meta, green, clean, points.get(lap) ?? 0);
  };
  const applyFilters = (green: boolean, clean: boolean): void => {
    setGreenOnly(green);
    setCleanOnly(clean);
    setSelected((current) => {
      const kept = current.filter((lap) => allowed(lap, green, clean));
      if (kept.length) return kept;
      const fastest = data.laps
        .map((lap) => data.lapMeta.get(lap))
        .filter((meta): meta is NonNullable<typeof meta> => hasUsableLapTime(meta) && isCompleteLap(meta!) && allowed(meta!.lap, green, clean))
        .sort((a, b) => (a.lap_time_s as number) - (b.lap_time_s as number) || a.lap - b.lap)[0];
      return fastest ? [fastest.lap] : [];
    });
    setSpotlight((current) => (current !== null && allowed(current, green, clean) ? current : null));
  };
  /** Show all (RA-033): every lap the active filters allow. */
  const showAll = (): void => {
    setSelected(data.laps.filter((lap) => allowed(lap, greenOnly, cleanOnly)).sort((a, b) => a - b));
  };

  const spotlightOptions = useMemo<SelectOption[]>(
    () => [
      { key: "", label: "None" },
      ...spotlightLaps(data, props.runs, selected).map((meta) => ({
        key: String(meta.lap),
        label: `L${meta.lap} · ${lapTime(meta.lap_time_s)}`,
        hint: meta.lap === data.fastestLap ? "fastest" : undefined,
        mark: <i class="swl" style={`--lap:${lapColor(data, meta.lap)}`} aria-hidden="true" />,
      })),
    ],
    [data, props.runs, selected],
  );
  const edit = (next: TraceLayoutBody | null): void => {
    if (next) props.onLayoutChange(next);
  };
  const addSignal = async (signalId: string): Promise<void> => {
    await props.ensureSignals([signalId]);
    edit(insertSignalRow(layoutRef.current, signalId, layoutRef.current.rows.length));
    setLoadTick((value) => value + 1);
  };
  const placeOnRow = async (rowId: string, signalId: string): Promise<void> => {
    await props.ensureSignals([signalId]);
    edit(placeSignal(layoutRef.current, rowId, signalId));
    setLoadTick((value) => value + 1);
  };
  const trackPoints = props.map?.points ?? [];
  const initialFraction = region ? (region.start + region.end) / 2 : 0.25;

  return (
    <div class={`ri-traces${full ? " full" : ""}`} data-ri-view="traces">
      <LapRail
        data={data} runs={props.runs} selected={new Set(selected)} spotlight={spotlight} zeroLap={railZero} greenOnly={greenOnly} cleanOnly={cleanOnly}
        incidents={incidents} pitStops={stops}
        onToggle={toggle} onGreenOnly={(value) => applyFilters(value, cleanOnly)} onCleanOnly={(value) => applyFilters(greenOnly, value)}
        onFastest={() => { const lap = fastestLap(data); setSelected(lap === null ? [] : [lap]); setSpotlight(null); }}
        onBestThree={() => { setSelected(bestThree(data)); setSpotlight(null); }}
        onClear={() => { setSelected([]); setSpotlight(null); }}
        onShowAll={showAll}
      />
      <div class="ri-traces-right">
        <TrackPanel data={data} map={props.map} mode={mode} onModeChange={setMode} selectedLaps={selected} zeroLap={zeroLap} refs={trackRefs} analysisKey={props.analysisKey} sectors={sectorStarts(data)} loadTick={loadTick} />
        <div class={`ri-studio${editing ? " editing" : ""}`} ref={studio}>
          <div class={`ri-card ri-studio-panel${full ? " ri-full" : ""}`} ref={card}>
            <div class="ri-trace-head">
              <span class="ri-card-title">Traces</span>
              <span class="ri-trace-count" data-zero-lap={zeroLap ?? ""}>{selected.length} lap{selected.length === 1 ? "" : "s"}{zeroLap !== null ? ` · Δ vs L${zeroLap}` : ""}</span>
              <div class="ri-trace-actions">
                {failedShown.length > 0 && (
                  <>
                    <span class="ri-fine" data-channel-error>{failedShown.map((id) => resolve(data, id)?.name ?? id).join(", ")} failed</span>
                    <button type="button" class="ri-pill-button" onClick={() => setRetryTick((value) => value + 1)}>Retry</button>
                  </>
                )}
                {props.focus && (
                  <>
                    {region && <span class="ri-fine">{(region.start * 100).toFixed(0)}–{(region.end * 100).toFixed(0)}%</span>}
                    <button type="button" class="ri-pill-button primary" onClick={props.onClearFocus}>My traces</button>
                  </>
                )}
                <SingleSelect
                  class="end" label="Spotlight" value={spotlight === null ? "" : String(spotlight)} options={spotlightOptions}
                  mark={spotlight !== null ? <i class="swl" style={`--lap:${lapColor(data, spotlight)}`} aria-hidden="true" /> : undefined}
                  onChange={(key) => { setSpotlight(key === "" ? null : Number(key)); if (key !== "" && !selected.includes(Number(key))) setSelected((current) => [...current, Number(key)].sort((x, y) => x - y)); }}
                />
                {!props.focus && props.layoutSet.layouts.length > 1 && (
                  <SingleSelect
                    class="end" label="Layout" value={active.id} options={props.layoutSet.layouts.map((entry) => ({ key: entry.id, label: entry.name }))}
                    onChange={(id) => props.onLayoutCommand({ type: "select", id })}
                  />
                )}
                <button type="button" class={`ri-pill-button${editing ? " on" : ""}`} disabled={props.focus !== null} aria-expanded={editing} aria-controls="analysis-trace-toolbox" aria-label="Customize" title="Customize" onClick={() => setEditing((value) => !value)}>
                  <svg class="ri-btn-icon" width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><path d="M2 4h7M13 4h1M2 12h1M7 12h7M11 2v4M5 10v4" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" /></svg>
                  <span class="ri-btn-text">Customize</span>
                </button>
                <button type="button" class={`ri-icon-pill${full ? " on" : ""}`} aria-pressed={full} aria-label={full ? "Exit full screen" : "Full screen"} title={full ? "Exit full screen" : "Full screen"} onClick={() => setFull((value) => !value)}>
                  <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">{full ? <path d="M6 1v5H1M10 1v5h5M6 15v-5H1M10 15v-5h5" fill="none" stroke="currentColor" stroke-width="1.8" /> : <path d="M1 6V1h5M15 6V1h-5M1 10v5h5M15 10v5h-5" fill="none" stroke="currentColor" stroke-width="1.8" />}</svg>
                </button>
              </div>
            </div>
            <div class="ri-studio-body">
              <TraceChart
                data={data} rows={rows} selected={selected} spotlight={spotlight} zeroLap={zeroLap} editing={editing && !props.focus} region={region} trackRefs={trackRefs}
                trackPoints={trackPoints} initialFraction={initialFraction} selectedRowId={selectedRowId} analysisKey={props.analysisKey} loadTick={loadTick} failedChannels={failedShown}
                onSelectRow={setSelectedRowId} onRemoveRow={(id) => edit(removeRow(layoutRef.current, id))}
                onMoveRow={(id, direction) => edit(moveRow(layoutRef.current, id, direction))} onCursor={props.onCursor}
              />
              {editing && !props.focus && (
                <TraceToolbox
                  data={data} rows={rows} maximumRows={maximumRows} layoutSet={props.layoutSet} selectedRowId={selectedRowId}
                  onAddRow={(id) => void addSignal(id)} onPlace={(rowId, id) => void placeOnRow(rowId, id)}
                  onRemoveSignal={(rowId, id) => edit(removeSignal(layoutRef.current, rowId, id))} onRemoveRow={(id) => edit(removeRow(layoutRef.current, id))}
                  onLayoutCommand={props.onLayoutCommand} onClose={() => setEditing(false)}
                />
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

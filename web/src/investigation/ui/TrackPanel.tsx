import { useEffect, useMemo, useRef } from "preact/hooks";
import type { RefObject } from "preact";
import { createTrackMap, type TrackMapController } from "../cursor/track-map";
import { MAP_MODES, lapMapPaths, metricLegend, metricRange, metricRibbon, type MapGeometry, type MapMode } from "../geometry/map-model";
import { signalColumn, type TraceData } from "../geometry/trace-model";
import { n } from "../format";

export interface TrackRefs {
  svg: RefObject<SVGSVGElement>;
  marker: RefObject<HTMLDivElement>;
  percent: RefObject<HTMLElement>;
  summary: RefObject<HTMLElement>;
}

export interface TrackPanelProps {
  data: TraceData;
  map: MapGeometry | null;
  mode: MapMode;
  onModeChange(mode: MapMode): void;
  selectedLaps: readonly number[];
  refs: TrackRefs;
  analysisKey: string;
  sectors: readonly number[];
}

/** Map modes that need a column the projection carries. */
function availableModes(data: TraceData): typeof MAP_MODES {
  return MAP_MODES.filter((mode) => mode.key === "traces" || signalColumn(data, mode.key === "stress" ? "tire-stress" : mode.key) !== null);
}

export function TrackPanel({ data, map, mode, onModeChange, selectedLaps, refs, analysisKey, sectors }: TrackPanelProps) {
  const controller = useRef<TrackMapController | null>(null);
  const viewBox = map?.viewBox ?? "0 0 420 280";
  useEffect(() => {
    const svg = refs.svg.current;
    if (!svg) return undefined;
    controller.current = createTrackMap(svg, viewBox, analysisKey);
    return () => {
      controller.current?.dispose();
      controller.current = null;
    };
    // The controller is created once per mounted map; later box changes go through setBase below.
  }, []);
  useEffect(() => {
    controller.current?.setBase(viewBox, analysisKey);
  }, [viewBox, analysisKey]);

  const modes = useMemo(() => availableModes(data), [data]);
  const lapPaths = useMemo(() => (map && mode === "traces" ? lapMapPaths(data, map, selectedLaps) : []), [data, map, mode, selectedLaps]);
  const ribbon = useMemo(() => (map && mode !== "traces" ? metricRibbon(data, map, mode, selectedLaps) : []), [data, map, mode, selectedLaps]);
  const legend = useMemo(() => {
    if (mode === "traces") return null;
    const indices = selectedLaps.map((lap) => data.lapIndex.get(lap)).filter((value): value is number => value !== undefined);
    return metricLegend(mode, metricRange(data, mode, indices));
  }, [data, mode, selectedLaps]);
  const strip = useMemo(() => {
    if (map) return null;
    const count = 80;
    const column = signalColumn(data, "speed");
    const indices = selectedLaps.map((lap) => data.lapIndex.get(lap)).filter((value): value is number => value !== undefined);
    const values: number[] = [];
    if (column) {
      for (let i = 0; i < count; i++) {
        const bin = Math.min(data.bins - 1, Math.floor(((i + 0.5) / count) * data.bins));
        const row = indices.map((lap) => column[lap * data.bins + bin]!).filter((v) => Number.isFinite(v)).sort((a, b) => a - b);
        values.push(row.length ? row[row.length >> 1]! : Number.NaN);
      }
    }
    const finite = values.filter(Number.isFinite);
    const min = finite.length ? Math.min(...finite) : 0;
    const max = finite.length ? Math.max(...finite) : 1;
    return values.map((value, i) => ({ x1: 28 + (i / count) * 364, x2: 28 + ((i + 1) / count) * 364, shade: Number.isFinite(value) ? (value - min) / Math.max(1, max - min) : null }));
  }, [data, map, selectedLaps]);

  return (
    <section class="ri-panel ri-track-panel" aria-label="Track position">
      <div class="ri-panel-title">
        <strong>{map ? "Track position" : "Lap distance"}</strong>
        <div class="ri-panel-actions">
          {map && (
            <label class="ri-select"><span>Type</span>
              <select value={mode} onChange={(event) => onModeChange((event.currentTarget as HTMLSelectElement).value as MapMode)}>
                {modes.map((option) => <option key={option.key} value={option.key}>{option.label}</option>)}
              </select>
            </label>
          )}
          {map && <button type="button" class="ri-button" title="Fit track" aria-label="Fit track" onClick={() => controller.current?.fit()}>Fit</button>}
        </div>
      </div>
      <div class="ri-track-stage">
      <svg ref={refs.svg} class="ri-track-map" data-analysis-track-map viewBox={viewBox} role="img" aria-label="Track telemetry map">
        {map ? (
          <>
            {mode === "traces" ? (
              <>
                <path d={map.ribbonPath} class="ri-main-halo" />
                <path d={map.ribbonPath} class="ri-main-ribbon" />
                {lapPaths.map((group) => <path key={group.color} d={group.d} stroke={group.color} class="ri-lap-trace" data-laps={group.laps.join(",")} />)}
              </>
            ) : (
              <>
                <path d={map.ribbonPath} class="ri-metric-route" />
                {ribbon.map((segment, index) => <path key={index} d={segment.d} fill={segment.color} stroke={segment.color} class="ri-metric-segment" />)}
              </>
            )}
            {map.pit?.road && <><path d={map.pit.road} class="ri-pit-halo" /><path d={map.pit.road} class="ri-pit-road" /></>}
            {map.pit?.entry && <path d={map.pit.entry} class="ri-pit-entry" />}
            {map.pit?.exit && <path d={map.pit.exit} class="ri-pit-exit" />}
            {map.pit?.commitment && <line x1={n(map.pit.commitment.x1)} y1={n(map.pit.commitment.y1)} x2={n(map.pit.commitment.x2)} y2={n(map.pit.commitment.y2)} class="ri-pit-commit" />}
            {map.pit?.merge && <line x1={n(map.pit.merge.x1)} y1={n(map.pit.merge.y1)} x2={n(map.pit.merge.x2)} y2={n(map.pit.merge.y2)} class="ri-pit-commit exit" />}
            {map.startFinish && <line x1={n(map.startFinish.x1)} y1={n(map.startFinish.y1)} x2={n(map.startFinish.x2)} y2={n(map.startFinish.y2)} class="ri-start-line" />}
          </>
        ) : (
          <>
            <line x1="28" y1="140" x2="392" y2="140" class="ri-distance-base" />
            {strip?.map((segment, index) => <line key={index} x1={n(segment.x1)} y1="140" x2={n(segment.x2)} y2="140" stroke={segment.shade === null ? "#56616D" : `rgb(${Math.round(232 - segment.shade * 198)} ${Math.round(66 + segment.shade * 154)} ${Math.round(78 + segment.shade * 38)})`} stroke-width="14" stroke-linecap="round" />)}
            {sectors.map((pct) => <line key={pct} x1={n(28 + pct * 364)} y1="113" x2={n(28 + pct * 364)} y2="167" class="ri-zone-marker" />)}
            <line x1="28" y1="124" x2="28" y2="156" class="ri-start-line" />
            <text x="28" y="205" class="ri-axis">Start / finish</text>
            <text x="392" y="205" text-anchor="end" class="ri-axis">100%</text>
          </>
        )}
      </svg>
      <div ref={refs.marker} class="ri-map-marker" aria-hidden="true"><i /></div>
      </div>
      {legend && <div class="ri-map-legend" data-mode={mode}><b>{legend.low}</b><i class={`ri-scale ${mode}`} /><b>{legend.high}</b></div>}
      <div class="ri-cursor-readout" title="Average of the selected laps"><strong ref={refs.percent as RefObject<HTMLElement>}>25.0%</strong><span ref={refs.summary as RefObject<HTMLElement>}>No sample</span></div>
    </section>
  );
}


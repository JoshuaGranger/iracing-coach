import { useEffect, useMemo, useRef } from "preact/hooks";
import type { RefObject } from "preact";
import { createTrackMap, type TrackMapController } from "../cursor/track-map";
import type { TrackField } from "../cursor/telemetry-cursor";
import { MAP_MODES, lapMapPaths, metricLegend, metricRange, metricRibbon, type MapGeometry, type MapMode } from "../geometry/map-model";
import { signalColumn, type TraceData } from "../geometry/trace-model";
import { n } from "../format";
import { SingleSelect } from "../../ui/single-select";

export interface TrackRefs {
  svg: RefObject<SVGSVGElement>;
  marker: RefObject<HTMLDivElement>;
  percent: RefObject<HTMLElement>;
  summary: RefObject<HTMLElement>;
  fields?: Record<TrackField, RefObject<HTMLElement>>;
}

export interface TrackPanelProps {
  data: TraceData;
  map: MapGeometry | null;
  mode: MapMode;
  onModeChange(mode: MapMode): void;
  selectedLaps: readonly number[];
  zeroLap: number | null;
  refs: TrackRefs;
  analysisKey: string;
  sectors: readonly number[];
  /** Bumped when channels arrive: the trace data is filled in place, so the ribbon and legend rebuild on it (review finding 7). */
  loadTick?: number;
}

/** Map modes whose channel the recording carries (the catalog decides; a channel outside the default set is fetched on choice). */
function availableModes(data: TraceData): typeof MAP_MODES {
  return MAP_MODES.filter((mode) => mode.key === "traces" || data.signals.has(mode.key === "stress" ? "tire-stress" : mode.key));
}

const FIELDS: { key: TrackField; label: string; unit: string }[] = [
  { key: "speed", label: "Speed", unit: "mph" },
  { key: "throttle", label: "Throttle", unit: "%" },
  { key: "brake", label: "Brake", unit: "%" },
  { key: "delta", label: "Δ", unit: "s" },
];

/**
 * The map strip: the track with the selected laps' lines, pit road in pit blue, the start/finish tick and the cursor marker in
 * the interaction colour, all turned with the outline so its long axis is horizontal (`MapGeometry`, backlog #81); beside it the cursor position and the selected laps' mean speed, throttle, brake and Δ in big numerals.
 */
export function TrackPanel({ data, map, mode, onModeChange, selectedLaps, zeroLap, refs, analysisKey, sectors, loadTick = 0 }: TrackPanelProps) {
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
  const lapPaths = useMemo(() => (map && mode === "traces" ? lapMapPaths(data, map, selectedLaps) : []), [data, map, mode, selectedLaps, data.colors]);
  const ribbon = useMemo(() => (map && mode !== "traces" ? metricRibbon(data, map, mode, selectedLaps) : []), [data, map, mode, selectedLaps, loadTick]);
  const legend = useMemo(() => {
    if (mode === "traces") return null;
    const indices = selectedLaps.map((lap) => data.lapIndex.get(lap)).filter((value): value is number => value !== undefined);
    return metricLegend(mode, metricRange(data, mode, indices));
  }, [data, mode, selectedLaps, loadTick]);
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
  }, [data, map, selectedLaps, loadTick]);

  return (
    <section class="ri-card ri-track-panel" aria-label="Track position">
      <div class="ri-track-stage">
        <svg ref={refs.svg} class="ri-track-map" data-analysis-track-map data-map-rotation={map?.rotationDeg} viewBox={viewBox} role="img" aria-label="Track map">
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
              <text x="28" y="205" class="ri-axis">S/F</text>
              <text x="392" y="205" text-anchor="end" class="ri-axis">100%</text>
            </>
          )}
        </svg>
        <div ref={refs.marker} class="ri-map-marker" aria-hidden="true"><i /></div>
      </div>
      <div class="ri-cursor-readout">
        <div class="ri-readout-head">
          <strong ref={refs.percent as RefObject<HTMLElement>} title="Lap distance at the cursor">25.0%</strong>
          <span>{selectedLaps.length === 0 ? "No laps" : selectedLaps.length === 1 ? `L${selectedLaps[0]}` : `Avg ${selectedLaps.length} laps`}</span>
          <div class="ri-map-tools">
            {/* What colours the map: the shared single-choice dropdown (U1), so it keeps the pills' focus and centring rules. */}
            {map && modes.length > 1 && (
              <SingleSelect class="end" label="Map" value={mode} options={modes.map((option) => ({ key: option.key, label: option.label }))} onChange={(key) => onModeChange(key as MapMode)} />
            )}
            {map && <button type="button" class="ri-pill-button small" title="Fit track" aria-label="Fit track" onClick={() => controller.current?.fit()}>Fit</button>}
          </div>
        </div>
        <div class="ri-kvs">
          {FIELDS.map((field) => (
            <div class={`ri-kv ${field.key}`} key={field.key}>
              <span>{field.key === "delta" && zeroLap !== null ? `Δ L${zeroLap}` : field.label}</span>
              <strong ref={refs.fields?.[field.key] as RefObject<HTMLElement> | undefined} data-field={field.key}>—</strong><small>{field.unit}</small>
            </div>
          ))}
        </div>
        <span ref={refs.summary as RefObject<HTMLElement>} class="ri-sr">No sample</span>
        {legend && <div class="ri-map-legend" data-mode={mode}><b>{legend.low}</b><i class={`ri-scale ${mode}`} /><b>{legend.high}</b></div>}
      </div>
    </section>
  );
}

import { useEffect, useMemo, useRef } from "preact/hooks";
import { buildCursorConfig, chartContentHeight, chartMetrics, type CursorConfig } from "../cursor/cursor-config";
import { createTelemetryCursor, type TelemetryCursor } from "../cursor/telemetry-cursor";
import type { MapPoint } from "../geometry/map-model";
import {
  chartPathGroups,
  hasSignalData,
  resolve,
  signalRange,
  type TraceData,
} from "../geometry/trace-model";
import { n } from "../format";
import type { TraceRowSpec } from "../types";
import { useElementSize } from "./hooks";
import type { TrackRefs } from "./TrackPanel";
import { scaleFor, useRemPx } from "./unit";

/** A row tall enough for two name lines and the unit (3.5 rem) wraps a long name (`Road-wheel / steering`) instead of cutting it. */
export function rowNameWraps(plotHeightPx: number, remPx: number): boolean {
  return plotHeightPx >= 3.5 * remPx;
}

export interface TraceChartProps {
  data: TraceData;
  rows: readonly TraceRowSpec[];
  selected: readonly number[];
  spotlight: number | null;
  /** The time-delta zero lap (the clean rule over the selection), named in the delta row's label. */
  zeroLap: number | null;
  editing: boolean;
  region: { start: number; end: number } | null;
  trackRefs: TrackRefs;
  trackPoints: MapPoint[];
  initialFraction: number;
  selectedRowId: string | null;
  analysisKey: string;
  onSelectRow(rowId: string): void;
  onRemoveRow(rowId: string): void;
  onMoveRow(rowId: string, direction: number): void;
  onCursor?(cursor: TelemetryCursor | null): void;
  /** Bumped when channels arrive (the trace data object is filled in place). */
  loadTick?: number;
  /** Channels whose request failed: their rows say Failed. */
  failedChannels?: readonly string[];
}

/** The map readouts the cursor writes, back to empty, and its marker hidden. */
function resetTrackReadouts(refs: TrackRefs): void {
  const fields = refs.fields;
  if (fields) for (const key of Object.keys(fields) as (keyof typeof fields)[]) { const element = fields[key].current; if (element) element.textContent = "—"; }
  if (refs.summary.current) refs.summary.current.textContent = "No sample";
  if (refs.marker.current) refs.marker.current.style.visibility = "hidden";
}

function rowName(data: TraceData, row: TraceRowSpec): string {
  return row.signals.map((id) => resolve(data, id)?.name ?? id).join(" / ");
}

function rowUnit(data: TraceData, row: TraceRowSpec, zeroLap: number | null): string {
  return [...new Set(row.signals.map((id) => (id === "delta" ? (zeroLap !== null ? `s · vs L${zeroLap}` : "s") : resolve(data, id)?.unit ?? "")))].filter(Boolean).join(" / ");
}

function rowHelp(data: TraceData, row: TraceRowSpec, available: boolean[]): string {
  const name = (id: string): string => resolve(data, id)?.name ?? id;
  const unit = (id: string): string => resolve(data, id)?.unit ?? "";
  const parts = row.signals.map((id, index) => `${row.signals.length > 1 ? (index === 0 ? "Solid · " : "Dashed · ") : ""}${name(id)} · ${unit(id)}${available[index] ? "" : " · not recorded"}`);
  if (row.signals.length > 1) parts.push("Own scales");
  return parts.join("\n");
}

/** What an empty row says: a failed channel request, a channel still on its way, a channel this recording lacks, or a delta without a complete lap. */
export function emptyRowText(data: TraceData, row: TraceRowSpec, zeroLap: number | null, failed: readonly string[] = []): string {
  if (row.signals.includes("delta") && zeroLap === null) return "No complete lap";
  if (row.signals.some((id) => failed.includes(id === "tire-wear" ? "tire-stress" : id))) return "Failed";
  if (row.signals.some((id) => id !== "delta" && id !== "tire-wear" && data.signals.has(id) && !data.table.channels.has(id))) return "Loading";
  if (row.signals.includes("tire-wear") && data.signals.has("tire-stress") && !data.table.channels.has("tire-stress")) return "Loading";
  return "Not recorded";
}

export function TraceChart(props: TraceChartProps) {
  const { data, rows, selected, spotlight } = props;
  const surface = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const cursor = useRef<TelemetryCursor | null>(null);
  const { width, height } = useElementSize(surface);
  const unit = useRemPx();
  const scale = scaleFor(unit);
  const ready = width >= 80 && height >= 80 && rows.length > 0 && selected.length > 0;
  const metrics = useMemo(() => chartMetrics(width, height, rows.length, unit), [width, height, rows.length, unit]);
  const lapIndices = useMemo(() => selected.map((lap) => data.lapIndex.get(lap)).filter((value): value is number => value !== undefined), [data, selected]);
  const reference = props.zeroLap;

  const drawn = useMemo(() => {
    if (!ready) return [];
    return rows.map((row, rowIndex) => {
      const frame = { plotLeft: metrics.plotLeft, plotWidth: metrics.plotWidth, top: metrics.chartTop + rowIndex * metrics.rowHeight, plotHeight: metrics.plotHeight };
      const available = row.signals.map((id) => hasSignalData(data, id, lapIndices, reference));
      const groups = row.signals.flatMap((id, signalIndex) => {
        if (!available[signalIndex]) return [];
        const range = signalRange(data, id, lapIndices, reference);
        return chartPathGroups(data, id, signalIndex, selected, spotlight, reference, frame, range).map((group) => ({ ...group, signalId: id, signalIndex }));
      });
      return { row, frame, available, groups };
    });
  }, [ready, rows, metrics, data, lapIndices, reference, selected, spotlight, props.loadTick, data.colors]);

  const config: CursorConfig | null = useMemo(
    () => (ready ? buildCursorConfig({ data, rows, selectedLaps: selected, spotlightLap: spotlight, metrics, trackPoints: props.trackPoints, initialFraction: props.initialFraction, zeroLap: reference, key: `${props.analysisKey}|${selected.join(",")}|${spotlight}|${reference}|${props.loadTick ?? 0}|${rows.map((row) => row.row_id + row.signals.join("+")).join(",")}|${Math.round(width)}x${Math.round(height)}@${unit}` }) : null),
    // initialFraction only seeds the first build; later changes belong to the cursor itself.
    [ready, data, rows, selected, spotlight, reference, metrics, props.trackPoints, props.analysisKey, width, height, unit, props.loadTick],
  );

  useEffect(() => {
    // Nothing selected (Clear) or no room: the cursor goes, with its markers and the map readouts of the old laps (review finding 4).
    if (!config) {
      if (cursor.current) {
        cursor.current.dispose();
        cursor.current = null;
        props.onCursor?.(null);
      }
      resetTrackReadouts(props.trackRefs);
      return;
    }
    if (!surface.current || !canvas.current) return;
    if (!cursor.current) {
      const svg = props.trackRefs.svg.current;
      cursor.current = createTelemetryCursor({
        surface: surface.current,
        canvas: canvas.current,
        track: svg,
        trackMarker: props.trackRefs.marker.current,
        trackPercent: props.trackRefs.percent.current,
        trackSummary: props.trackRefs.summary.current,
        trackFields: props.trackRefs.fields
          ? { speed: props.trackRefs.fields.speed.current, throttle: props.trackRefs.fields.throttle.current, brake: props.trackRefs.fields.brake.current, delta: props.trackRefs.fields.delta.current }
          : undefined,
        config,
      });
      if (props.trackRefs.marker.current) props.trackRefs.marker.current.style.visibility = "";
      props.onCursor?.(cursor.current);
    } else {
      cursor.current.update(config);
    }
  }, [config]);

  useEffect(() => () => {
    cursor.current?.dispose();
    cursor.current = null;
    props.onCursor?.(null);
  }, []);

  // Rows never shrink under their floor: when they do not fit, the chart frame scrolls instead of clipping the lower rows (review finding 3).
  const contentHeight = chartContentHeight(rows.length, unit);
  return (
    <div class="ri-chart-frame" data-chart-scroll style={`--ri-chart-min:${n(contentHeight)}px`}>
      <div class="ri-label-col" aria-label="Trace chart labels">
        {ready && drawn.map(({ row, frame, available }, index) => {
          const name = rowName(data, row);
          const helpId = `ri-row-help-${row.row_id}`;
          return (
            <div
              class="trace-row-label-shell" key={row.row_id} style={`top:${n(frame.top)}px;height:${n(frame.plotHeight)}px`} data-name-wraps={rowNameWraps(frame.plotHeight, unit) ? "" : undefined}
              data-analysis-row-target="label" data-row-id={row.row_id} data-row-index={index} data-row-name={name}
              data-primary-name={resolve(data, row.signals[0]!)?.name ?? ""} data-secondary-name={row.signals[1] ? resolve(data, row.signals[1])?.name ?? "" : ""}
            >
              <div
                class={`ri-row-label${props.selectedRowId === row.row_id ? " selected" : ""}`} tabIndex={0} role="group" aria-describedby={helpId} aria-label={`${name} chart`}
                data-analysis-drag-row={row.row_id} data-row-id={row.row_id} data-row-name={name}
                onFocus={() => props.onSelectRow(row.row_id)}
                onKeyDown={(event: KeyboardEvent) => {
                  if (event.altKey && (event.key === "ArrowUp" || event.key === "ArrowDown")) {
                    event.preventDefault();
                    props.onMoveRow(row.row_id, event.key === "ArrowUp" ? -1 : 1);
                  }
                }}
              >
                {props.editing && <span class="ri-grip" aria-hidden="true">⋮⋮</span>}
                <span class="ri-row-copy"><strong>{row.signals.map((id) => <span class="ri-row-signal" key={id}>{resolve(data, id)?.name ?? id}</span>)}</strong><span class="ri-row-unit" data-row-unit={row.signals.join("+")}>{rowUnit(data, row, reference)}</span></span>
                {props.editing && (
                  <button type="button" class="ri-row-remove" data-analysis-no-drag disabled={rows.length <= 1} title="Remove chart" aria-label={`Remove ${name} chart`} onClick={(event) => { event.stopPropagation(); props.onRemoveRow(row.row_id); }}>×</button>
                )}
              </div>
              <span id={helpId} class="ri-row-tooltip" role="tooltip">{rowHelp(data, row, available)}</span>
            </div>
          );
        })}
      </div>
      <div class="ri-chart-surface" ref={surface} tabIndex={0} role="application" aria-label="Traces · arrow keys move the cursor" data-analysis-trace-chart>
        {ready && (
          <svg class="ri-trace-svg" width={n(width)} height={n(height)} viewBox={`0 0 ${n(width)} ${n(height)}`} role="img" aria-label="Traces">
            {drawn.map(({ row, frame, available, groups }, index) => (
              <g key={row.row_id} data-analysis-render-row data-row-id={row.row_id}>
                <rect data-analysis-chart-row data-analysis-row-target="chart" data-row-id={row.row_id} data-row-index={index} data-row-name={rowName(data, row)}
                  data-primary-name={resolve(data, row.signals[0]!)?.name ?? ""} data-secondary-name={row.signals[1] ? resolve(data, row.signals[1])?.name ?? "" : ""}
                  x={n(frame.plotLeft)} y={n(frame.top)} width={n(frame.plotWidth)} height={n(frame.plotHeight)} class="ri-row-bg" />
                {props.region && props.region.end > props.region.start && (
                  <rect data-investigation-region x={n(frame.plotLeft + Math.min(1, Math.max(0, props.region.start)) * frame.plotWidth)} y={n(frame.top)}
                    width={n(Math.min(1, Math.max(0, props.region.end - props.region.start)) * frame.plotWidth)} height={n(frame.plotHeight)} class="ri-region" />
                )}
                <line x1={n(frame.plotLeft)} y1={n(frame.top + frame.plotHeight / 2)} x2={n(frame.plotLeft + frame.plotWidth)} y2={n(frame.top + frame.plotHeight / 2)} class="ri-grid" />
                {!available.some(Boolean) && <text x={n(frame.plotLeft + 12)} y={n(frame.top + frame.plotHeight / 2 + 5 * scale)} class="ri-unavailable" data-row-empty={row.row_id}>{emptyRowText(data, row, reference, props.failedChannels)}</text>}
                <g>
                  {groups.map((group) => (
                    <path key={`${group.signalId}|${group.key}`} data-analysis-trace-path data-row={index} data-signal={group.signalId} data-laps={group.laps.join(",")} data-spotlight={group.spotlight}
                      d={group.d} fill="none" stroke={group.color} stroke-width={group.strokeWidth} stroke-dasharray={group.signalIndex === 0 ? undefined : "4 3"} opacity={group.opacity} vector-effect="non-scaling-stroke" />
                  ))}
                </g>
              </g>
            ))}
            {[0, 1, 2, 3, 4].map((tick) => {
              const x = metrics.plotLeft + (tick * metrics.plotWidth) / 4;
              return (
                <g key={tick}>
                  <line x1={n(x)} y1={n(metrics.chartTop)} x2={n(x)} y2={n(metrics.chartBottom)} class="ri-grid-vertical" />
                  <text x={n(x)} y={n(metrics.height - 6 * scale)} text-anchor={tick === 0 ? "start" : tick === 4 ? "end" : "middle"} class="ri-axis">{tick * 25}%</text>
                </g>
              );
            })}
          </svg>
        )}
        <canvas ref={canvas} class="ri-cursor-canvas" aria-hidden="true" />
        {selected.length === 0 && <div class="ri-empty ri-over"><strong>No laps selected</strong></div>}
      </div>
    </div>
  );
}

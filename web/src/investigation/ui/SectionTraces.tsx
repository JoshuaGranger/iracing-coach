import { useEffect, useMemo, useRef } from "preact/hooks";
import type { ComponentChildren } from "preact";
import { chartMetrics } from "../cursor/cursor-config";
import { createTelemetryCursor, type TelemetryCursor } from "../cursor/telemetry-cursor";
import { resolve, type TraceData } from "../geometry/trace-model";
import { n } from "../format";
import { useElementSize } from "./hooks";
import { buildSectionCursorConfig, lapPctAt, SECTION_SIGNALS, sectionBins, sectionPath, sectionRange, sectionValues, type SectionSpan } from "./section-traces-model";
import { scaleFor, useRemPx } from "./unit";

/**
 * The chosen section's laps over its distance (backlog #64): speed, throttle, brake and steering, one row each, one line per lap in
 * its shade from the lap list, the section's median lap in grey. The synchronized cursor is the Traces one: pointer or keys over the
 * rows draw the per-lap value cards; `onFraction` moves the map marker; `cursorRef` lets the map move the cursor. Drawing only.
 */

export interface SectionTraceLap {
  lap: number;
  /** A CSS colour token for the SVG line (`var(--sl-g1)`); the median lap's is the chart reference grey. */
  stroke: string;
  median: boolean;
}

export type SectionTraceStatus = "loading" | "ready" | "failed";

export interface SectionTracesProps {
  data: TraceData | null;
  status: SectionTraceStatus;
  onRetry(): void;
  section: SectionSpan & { id: string };
  /** Drawn laps: the six (or the selected subset) in list order, then the median lap. */
  laps: readonly SectionTraceLap[];
  hot: number | null;
  /** Where the cursor starts across the section, and where it is kept between sections. */
  fractionRef: { current: number };
  /** The live cursor, for the map to drive. */
  cursorRef: { current: TelemetryCursor | null };
  /** Called with the position across the section after a cursor frame that moved it (map marker). */
  onFraction(local: number): void;
  analysisKey: string;
  /** The section header, first in the row that carries the median key and the cursor's lap distance. */
  head?: ComponentChildren;
}

/** Canvas colours for the lap tokens: read once from the page (the canvas cannot read CSS variables). */
function resolveColours(host: HTMLElement, strokes: readonly string[]): Map<string, string> {
  const probe = document.createElement("span");
  probe.style.display = "none";
  host.appendChild(probe);
  const out = new Map<string, string>();
  for (const stroke of strokes) {
    probe.style.color = stroke;
    out.set(stroke, getComputedStyle(probe).color || "#93A3AD");
  }
  probe.remove();
  return out;
}

export function SectionTraces(props: SectionTracesProps) {
  const { data, section, laps } = props;
  const surface = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const distance = useRef<HTMLElement>(null);
  const colours = useRef<Map<string, string>>(new Map());
  const { width, height } = useElementSize(surface);
  const unit = useRemPx();
  const scale = scaleFor(unit);
  const onFractionRef = useRef(props.onFraction);
  onFractionRef.current = props.onFraction;
  const sectionRef = useRef(section);
  sectionRef.current = section;

  const present = useMemo(() => (data ? laps.filter((lap) => data.lapIndex.has(lap.lap)) : []), [data, laps]);
  const ready = data !== null && width >= 80 && height >= 80 && present.length > 0;
  const metrics = useMemo(() => chartMetrics(width, height, SECTION_SIGNALS.length, unit), [width, height, unit]);
  const bins = useMemo(() => (data ? sectionBins(data.x, section) : null), [data, section.startPct, section.endPct]);
  const lapIndices = useMemo(() => (data ? present.map((lap) => data.lapIndex.get(lap.lap)!) : []), [data, present]);
  const ranges = useMemo(() => (data && bins ? SECTION_SIGNALS.map((id) => sectionRange(data, id, lapIndices, bins.bins)) : []), [data, bins, lapIndices]);

  const drawn = useMemo(() => {
    if (!ready || !data || !bins) return [];
    return SECTION_SIGNALS.map((id, rowIndex) => {
      const frame = { plotLeft: metrics.plotLeft, plotWidth: metrics.plotWidth, top: metrics.chartTop + rowIndex * metrics.rowHeight, plotHeight: metrics.plotHeight };
      const signal = resolve(data, id);
      const paths = present.map((lap) => ({ lap, d: sectionPath(sectionValues(data, id, data.lapIndex.get(lap.lap)!, bins.bins), bins.x, ranges[rowIndex]!, frame) })).filter((path) => path.d);
      return { id, frame, name: signal?.name ?? id, unit: signal?.unit ?? "", paths };
    });
  }, [ready, data, bins, metrics, present, ranges]);

  // The cursor: created once the rows have room, rebuilt in place when the section, the laps or the size change. Nothing here requests.
  useEffect(() => {
    if (!ready || !data || !bins || !surface.current || !canvas.current) {
      if (props.cursorRef.current) {
        props.cursorRef.current.dispose();
        props.cursorRef.current = null;
      }
      return;
    }
    const missing = present.map((lap) => lap.stroke).filter((stroke) => !colours.current.has(stroke));
    if (missing.length) for (const [stroke, colour] of resolveColours(surface.current, missing)) colours.current.set(stroke, colour);
    const config = buildSectionCursorConfig({
      data, bins, ranges, metrics, initialFraction: props.fractionRef.current,
      laps: present.map((lap) => ({ lap: lap.lap, color: colours.current.get(lap.stroke) ?? "#93A3AD" })),
      key: `${props.analysisKey}|${section.id}|${present.map((lap) => lap.lap).join(",")}|${Math.round(width)}x${Math.round(height)}@${unit}`,
    });
    if (!props.cursorRef.current) {
      props.cursorRef.current = createTelemetryCursor({
        surface: surface.current,
        canvas: canvas.current,
        config,
        onFraction: (fraction) => {
          props.fractionRef.current = fraction;
          if (distance.current) distance.current.textContent = `${(lapPctAt(fraction, sectionRef.current) * 100).toFixed(1)}%`;
          onFractionRef.current(fraction);
        },
      });
    } else {
      props.cursorRef.current.update(config);
    }
    onFractionRef.current(props.fractionRef.current);
  }, [ready, data, bins, ranges, metrics, present, section.id]);

  // The distance readout follows the section when it changes without a cursor move.
  useEffect(() => {
    if (distance.current) distance.current.textContent = `${(lapPctAt(props.fractionRef.current, section) * 100).toFixed(1)}%`;
  }, [section.id, section.startPct, section.endPct]);

  useEffect(() => () => {
    props.cursorRef.current?.dispose();
    props.cursorRef.current = null;
  }, []);

  const median = present.find((lap) => lap.median) ?? null;
  const span = section.endPct > section.startPct ? section.endPct - section.startPct : section.endPct + 1 - section.startPct;
  return (
    <div class="ri-st" data-sl-traces data-status={props.status}>
      <div class="ri-st-head">
        {props.head}
        {median &&<span class="ri-st-key" data-sl-median-key><i aria-hidden="true" />Median L{median.lap}</span>}
        <strong ref={distance} class="ri-st-pct" title="Lap distance at the cursor" data-sl-cursor-pct>{(lapPctAt(props.fractionRef.current, section) * 100).toFixed(1)}%</strong>
      </div>
      <div class="ri-st-surface" ref={surface} tabIndex={0} role="application" aria-label="Section telemetry · arrow keys move the cursor" data-sl-chart>
        {ready && (
          <svg class="ri-trace-svg" width={n(width)} height={n(height)} viewBox={`0 0 ${n(width)} ${n(height)}`} role="img" aria-label={`Speed, throttle, brake and steering through the section · ${present.length} laps`}>
            {drawn.map(({ id, frame, name, unit: rowUnit, paths }) => (
              <g key={id} data-sl-row={id}>
                <rect x={n(frame.plotLeft)} y={n(frame.top)} width={n(frame.plotWidth)} height={n(frame.plotHeight)} class="ri-row-bg" />
                <line x1={n(frame.plotLeft)} y1={n(frame.top + frame.plotHeight / 2)} x2={n(frame.plotLeft + frame.plotWidth)} y2={n(frame.top + frame.plotHeight / 2)} class="ri-grid" />
                {paths.map(({ lap, d }) => (
                  <path key={lap.lap} d={d} class={`ri-st-line${lap.median ? " median" : ""}${props.hot === lap.lap ? " hot" : ""}`} style={`--lap:${lap.stroke}`}
                    data-sl-trace={lap.lap} data-signal={id} data-median={lap.median ? "" : undefined} />
                ))}
                <text x={n(frame.plotLeft + 6 * scale)} y={n(frame.top + 15 * scale)} class="ri-axis ri-st-name">{name}<tspan class="ri-st-unit"> · {rowUnit}</tspan></text>
              </g>
            ))}
            {[0, 1, 2, 3, 4].map((tick) => {
              const x = metrics.plotLeft + (tick * metrics.plotWidth) / 4;
              const pct = section.startPct + (tick / 4) * span;
              return (
                <g key={tick}>
                  <line x1={n(x)} y1={n(metrics.chartTop)} x2={n(x)} y2={n(metrics.chartBottom)} class="ri-grid-vertical" />
                  <text x={n(x)} y={n(metrics.height - 6 * scale)} text-anchor={tick === 0 ? "start" : tick === 4 ? "end" : "middle"} class="ri-axis" data-sl-tick>{((pct >= 1 ? pct - 1 : pct) * 100).toFixed(1)}%</text>
                </g>
              );
            })}
          </svg>
        )}
        <canvas ref={canvas} class="ri-cursor-canvas" aria-hidden="true" />
        {props.status === "loading" && <div class="ri-st-over">Loading</div>}
        {props.status === "failed" && (
          <div class="ri-st-over">
            <span>Failed</span>
            <button type="button" class="ri-pill-button" onClick={props.onRetry}>Retry</button>
          </div>
        )}
      </div>
    </div>
  );
}

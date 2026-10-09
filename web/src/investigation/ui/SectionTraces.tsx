import { useEffect, useMemo, useRef } from "preact/hooks";
import type { ComponentChildren } from "preact";
import { theme } from "../../gen/theme";
import { chartMetrics } from "../cursor/cursor-config";
import { createTelemetryCursor, type TelemetryCursor } from "../cursor/telemetry-cursor";
import type { TraceData } from "../geometry/trace-model";
import { n } from "../format";
import { useElementSize } from "./hooks";
import {
  buildSectionCursorConfig,
  gapReference,
  passPair,
  sectionBins,
  sectionLocal,
  sectionRows,
  sectionWindow,
  splitSectionPath,
  windowPctAt,
  windowPosition,
  windowSpan,
  type SectionSpan,
  type SectionWindow,
} from "./section-traces-model";
import { scaleFor, useRemPx } from "./unit";

/**
 * The chosen section's laps over its approach and its distance (backlog #64, gap ledger G6): speed, time gap to the median lap,
 * throttle, brake, steering and body slip, one row each, one line per lap in its shade from the lap list, the section's median lap
 * in grey. The approach (the backend's lead-in before the section start) is drawn dashed left of one boundary line, so entry speed
 * reads before the section begins; the fastest and slowest drawn laps (the pass pair) carry the thick stroke. The gap row's
 * reference is the section's median lap, whether or not it is also one of the listed laps. The synchronized cursor is the Traces one: pointer or keys over the rows draw the
 * per-lap value cards; `onFraction` moves the map marker (with the cursor's lap distance, so the marker follows the approach too);
 * `cursorRef` lets the map move the cursor. Both speak the position across the section (negative in the approach); the cursor
 * itself runs across the window. Drawing only.
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
  /** The section; `previousStartPct`, when given, bounds the approach at the previous section's start. */
  section: SectionSpan & { id: string };
  /** Drawn laps: the six (or the selected subset) in list order, then the median lap when it is not among them. */
  laps: readonly SectionTraceLap[];
  /** The section's median lap (`section_laps[].median_lap`): the gap row's reference, also when it is one of the listed laps. */
  medianLap?: number | null;
  hot: number | null;
  /** Where the cursor starts across the section, and where it is kept between sections. */
  fractionRef: { current: number };
  /** The live cursor, for the map to drive (positions across the section). */
  cursorRef: { current: TelemetryCursor | null };
  /**
   * Called after a cursor frame that moved it, with the position across the section (negative in the approach) and the lap distance at
   * the cursor (the map marker: on the section segment inside the section, on the track before it).
   */
  onFraction(local: number, lapPct?: number): void;
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

/** The cursor as the view sees it: positions across the section in and out, the window position inside. */
function sectionCursor(inner: TelemetryCursor, windowRef: { current: SectionWindow }): TelemetryCursor {
  return {
    update: (config) => inner.update(config),
    setFraction: (local) => inner.setFraction(windowPosition(local, windowRef.current)),
    get fraction() {
      return sectionLocal(inner.fraction, windowRef.current);
    },
    diagnostics: () => inner.diagnostics(),
    resetDiagnostics: () => inner.resetDiagnostics(),
    dispose: () => inner.dispose(),
  };
}

const pctText = (pct: number): string => `${(pct * 100).toFixed(1)}%`;

export function SectionTraces(props: SectionTracesProps) {
  const { data, section, laps } = props;
  const surface = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const distance = useRef<HTMLElement>(null);
  const colours = useRef<Map<string, string>>(new Map());
  const inner = useRef<TelemetryCursor | null>(null);
  const { width, height } = useElementSize(surface);
  const unit = useRemPx();
  const scale = scaleFor(unit);
  const onFractionRef = useRef(props.onFraction);
  onFractionRef.current = props.onFraction;

  const win = useMemo(() => sectionWindow(section), [section.startPct, section.endPct, section.previousStartPct]);
  const windowRef = useRef(win);
  windowRef.current = win;
  const present = useMemo(() => (data ? laps.filter((lap) => data.lapIndex.has(lap.lap)) : []), [data, laps]);
  // The reference: the reported median lap when the store has it (listed or not), else the drawn median line.
  const medianLap = gapReference(props.medianLap, present, (lap) => data?.lapIndex.has(lap) ?? false);
  const pass = useMemo(() => passPair(present), [present]);
  const ready = data !== null && width >= 80 && height >= 80 && present.length > 0;
  const bins = useMemo(() => (data ? sectionBins(data.x, windowSpan(win)) : null), [data, win]);
  const rows = useMemo(() => (data && bins ? sectionRows(data, bins, present.map((lap) => lap.lap), medianLap) : []), [data, bins, present, medianLap]);
  const metrics = useMemo(() => chartMetrics(width, height, rows.length || 6, unit), [width, height, rows.length, unit]);

  const boundaryX = metrics.plotLeft + win.boundary * metrics.plotWidth;
  const drawn = useMemo(() => {
    if (!ready || !bins) return [];
    return rows.map((row, rowIndex) => {
      const frame = { plotLeft: metrics.plotLeft, plotWidth: metrics.plotWidth, top: metrics.chartTop + rowIndex * metrics.rowHeight, plotHeight: metrics.plotHeight };
      const paths = present.map((lap, lapIndex) => ({ lap, ...splitSectionPath(row.values[lapIndex]!, bins.x, win.boundary, row.range, frame) })).filter((path) => path.approach || path.section);
      return { row, frame, paths };
    });
  }, [ready, bins, rows, metrics, present, win]);

  // The cursor: created once the rows have room, rebuilt in place when the section, the laps or the size change. Nothing here requests.
  useEffect(() => {
    if (!ready || !data || !bins || !surface.current || !canvas.current) {
      if (props.cursorRef.current) {
        props.cursorRef.current.dispose();
        props.cursorRef.current = null;
        inner.current = null;
      }
      return;
    }
    const missing = present.map((lap) => lap.stroke).filter((stroke) => !colours.current.has(stroke));
    if (missing.length) for (const [stroke, colour] of resolveColours(surface.current, missing)) colours.current.set(stroke, colour);
    const config = buildSectionCursorConfig({
      bins, rows, metrics, initialFraction: windowPosition(props.fractionRef.current, win),
      laps: present.map((lap) => ({ lap: lap.lap, color: colours.current.get(lap.stroke) ?? "#93A3AD" })),
      key: `${props.analysisKey}|${section.id}|${win.approachPct}|${present.map((lap) => lap.lap).join(",")}|${Math.round(width)}x${Math.round(height)}@${unit}`,
    });
    if (!inner.current) {
      inner.current = createTelemetryCursor({
        surface: surface.current,
        canvas: canvas.current,
        config,
        onFraction: (position) => {
          const local = sectionLocal(position, windowRef.current);
          props.fractionRef.current = local;
          if (distance.current) distance.current.textContent = pctText(windowPctAt(position, windowRef.current));
          onFractionRef.current(local, windowPctAt(position, windowRef.current));
        },
      });
      props.cursorRef.current = sectionCursor(inner.current, windowRef);
    } else {
      inner.current.update(config);
    }
    const local = props.fractionRef.current;
    onFractionRef.current(local, windowPctAt(windowPosition(local, win), win));
  }, [ready, data, bins, rows, metrics, present, section.id, win]);

  // The distance readout follows the section when it changes without a cursor move.
  useEffect(() => {
    if (distance.current) distance.current.textContent = pctText(windowPctAt(windowPosition(props.fractionRef.current, win), win));
  }, [section.id, win]);

  useEffect(() => () => {
    props.cursorRef.current?.dispose();
    props.cursorRef.current = null;
    inner.current = null;
  }, []);

  // The key: the grey line when the median lap is drawn as one; a listed median lap keeps its shade, so its key has no grey swatch.
  const medianLine = present.find((lap) => lap.median) ?? null;
  const median = medianLine ?? (medianLap !== null ? { lap: medianLap } : null);
  const axisY = metrics.height - 6 * scale;
  const charPx = theme.typography.compactPx * scale * 0.56;
  // Ticks: the approach start, the section start (the boundary), the section's middle and its end; a label that would collide is left out.
  const ticks = [
    { at: 0, pct: win.approachPct, anchor: "start" },
    { at: win.boundary, pct: win.startPct, anchor: "middle" },
    { at: win.boundary + (1 - win.boundary) / 2, pct: windowPctAt(win.boundary + (1 - win.boundary) / 2, win), anchor: "middle" },
    { at: 1, pct: win.endPct, anchor: "end" },
  ].filter((tick, index, all) => index === 0 || (tick.at - all[index - 1]!.at) * metrics.plotWidth > charPx * 7);
  const approachWidth = win.boundary * metrics.plotWidth;
  const showApproachLabel = approachWidth > charPx * (8 + 6 + 6);
  return (
    <div class="ri-st" data-sl-traces data-status={props.status}>
      <div class="ri-st-head">
        {props.head}
        {median && <span class="ri-st-key" data-sl-median-key>{medianLine && <i aria-hidden="true" />}Median L{median.lap}</span>}
        <span class="ri-st-key" data-sl-approach-key><i class="approach" aria-hidden="true" />Approach</span>
        <strong ref={distance} class="ri-st-pct" title="Lap distance at the cursor" data-sl-cursor-pct>{pctText(windowPctAt(windowPosition(props.fractionRef.current, win), win))}</strong>
      </div>
      <div class="ri-st-surface" ref={surface} tabIndex={0} role="application" aria-label="Section telemetry with its approach · arrow keys move the cursor" data-sl-chart>
        {ready && (
          <svg class="ri-trace-svg" width={n(width)} height={n(height)} viewBox={`0 0 ${n(width)} ${n(height)}`} role="img" aria-label={`Speed, gap to the median lap, throttle, brake, steering and body slip through the approach and the section · ${present.length} laps`}>
            {drawn.map(({ row, frame, paths }) => (
              <g key={row.id} data-sl-row={row.id}>
                <rect x={n(frame.plotLeft)} y={n(frame.top)} width={n(frame.plotWidth)} height={n(frame.plotHeight)} class="ri-row-bg" />
                <line x1={n(frame.plotLeft)} y1={n(frame.top + frame.plotHeight / 2)} x2={n(frame.plotLeft + frame.plotWidth)} y2={n(frame.top + frame.plotHeight / 2)} class="ri-grid" />
                {paths.map(({ lap, approach, section: inside }) => {
                  const cls = `ri-st-line${lap.median ? " median" : ""}${pass.has(lap.lap) ? " pass" : ""}${props.hot === lap.lap ? " hot" : ""}`;
                  return (
                    <g key={lap.lap}>
                      {approach && <path d={approach} class={`${cls} approach`} style={`--lap:${lap.stroke}`} data-sl-approach={lap.lap} />}
                      {inside && <path d={inside} class={cls} style={`--lap:${lap.stroke}`} data-sl-trace={lap.lap} data-signal={row.id} data-median={lap.median ? "" : undefined} data-pass={pass.has(lap.lap) ? "" : undefined} />}
                    </g>
                  );
                })}
                <text x={n(frame.plotLeft + 6 * scale)} y={n(frame.top + 15 * scale)} class="ri-axis ri-st-name">{row.name}<tspan class="ri-st-unit"> · {row.unit}</tspan></text>
              </g>
            ))}
            {win.boundary > 0 && <line x1={n(boundaryX)} y1={n(metrics.chartTop)} x2={n(boundaryX)} y2={n(metrics.chartBottom)} class="ri-st-boundary" data-sl-boundary />}
            {ticks.map((tick) => {
              const x = metrics.plotLeft + tick.at * metrics.plotWidth;
              return (
                <g key={tick.at}>
                  {tick.at > 0 && tick.at < 1 && tick.at !== win.boundary && <line x1={n(x)} y1={n(metrics.chartTop)} x2={n(x)} y2={n(metrics.chartBottom)} class="ri-grid-vertical" />}
                  <text x={n(x)} y={n(axisY)} text-anchor={tick.anchor} class={`ri-axis${tick.at === win.boundary ? " ri-st-start" : ""}`} data-sl-tick>{pctText(tick.pct)}</text>
                </g>
              );
            })}
            {showApproachLabel && <text x={n(metrics.plotLeft + approachWidth / 2)} y={n(axisY)} text-anchor="middle" class="ri-axis ri-st-zone" data-sl-zone-label>Approach</text>}
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

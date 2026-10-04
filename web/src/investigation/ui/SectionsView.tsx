import { useEffect, useMemo, useRef } from "preact/hooks";
import { theme } from "../../gen/theme";
import { createRaceInspection, type InspectionMapPoint } from "../cursor/race-inspection";
import { n, numberText, passRole, percentText, PlotScale, reasonsText, seconds, signedText } from "../format";
import type { MapGeometry, MapPoint } from "../geometry/map-model";
import type { Comparison, Investigation, PairSteering, PassPair, Pass, PassRow, Section } from "../investigation-model";
import { ROAD_WHEEL_LABEL, STEERING_WHEEL_LABEL } from "../signals";
import { useElementSize } from "./hooks";
import { scaleFor, useRemPx } from "./unit";

const VIEW_WIDTH = 1000;
const VIEW_HEIGHT = 100;

interface Channel {
  key: "speed" | "gap" | "throttle" | "brake" | "steering" | "slip";
  label: string;
  unit: string;
  digits: number;
  axisDigits: number;
  title: string;
  fixedMin?: number;
  fixedMax?: number;
}

const PASS_CHANNELS: Channel[] = [
  { key: "speed", label: "Speed", unit: "mph", digits: 1, axisDigits: 0, title: "Speed" },
  { key: "gap", label: "Time gap", unit: "s", digits: 3, axisDigits: 2, title: "Second pass − first · 0 at approach start · + behind" },
  { key: "throttle", label: "Throttle", unit: "%", digits: 0, axisDigits: 0, title: "Throttle", fixedMin: 0, fixedMax: 100 },
  { key: "brake", label: "Brake", unit: "%", digits: 0, axisDigits: 0, title: "Brake · bin mean", fixedMin: 0, fixedMax: 100 },
];

function steeringChannel(steering: PairSteering): Channel {
  if (!steering.comparable) return { key: "steering", label: "Steering", unit: "°", digits: 1, axisDigits: 0, title: steering.reason ?? "Not compared" };
  const road = steering.basis === "road_wheel";
  return road
    ? { key: "steering", label: ROAD_WHEEL_LABEL, unit: "°", digits: steering.degreeDigits ?? 2, axisDigits: 1, title: "Wheel angle ÷ setup ratio · signed peak per bin · left +" }
    : { key: "steering", label: STEERING_WHEEL_LABEL, unit: "°, ratio not recorded", digits: steering.degreeDigits ?? 1, axisDigits: 0, title: `${steering.label ?? STEERING_WHEEL_LABEL} · signed peak per bin · left +` };
}

const SLIP: Channel = { key: "slip", label: "Body slip", unit: "°", digits: 2, axisDigits: 1, title: "Chassis sideslip · signed peak per bin" };

function valueOf(row: PassRow, side: "a" | "b" | "gap", key: Channel["key"]): number | null {
  if (key === "gap") return row.gap;
  const value = (side === "b" ? row.b : row.a)[key];
  return value === undefined ? null : value;
}

function channelScale(pair: PassPair, channel: Channel): PlotScale {
  const values = pair.rows.flatMap((row) => (channel.key === "gap" ? [row.gap] : [valueOf(row, "a", channel.key), valueOf(row, "b", channel.key)])).filter((value): value is number => value !== null && Number.isFinite(value));
  let min: number;
  let max: number;
  if (channel.fixedMin !== undefined && channel.fixedMax !== undefined) {
    min = channel.fixedMin;
    max = channel.fixedMax;
  } else if (values.length === 0) {
    min = 0;
    max = 1;
  } else {
    min = Math.min(...values);
    max = Math.max(...values);
    const pad = Math.max((max - min) * 0.12, channel.key === "gap" ? 0.005 : channel.key === "slip" ? 0.2 : channel.key === "steering" && pair.steering.basis === "road_wheel" ? 0.1 : 1);
    min -= pad;
    max += pad;
  }
  return new PlotScale(0, 4, VIEW_WIDTH, VIEW_HEIGHT - 8, pair.contextStartPct, pair.endPct, min, max);
}

/** Polyline through recorded rows; a missing value or a wide spacing breaks the line. */
function line(pair: PassPair, plot: PlotScale, read: (row: PassRow) => number | null): string {
  const parts: string[] = [];
  let previous: number | null = null;
  for (const row of pair.rows) {
    const value = read(row);
    if (value === null || !Number.isFinite(value)) {
      previous = null;
      continue;
    }
    parts.push(`${previous === null || row.x - previous > 0.0301 ? "M" : "L"}${n(plot.x(row.x))},${n(plot.y(Math.min(plot.yMax, Math.max(plot.yMin, value))))}`);
    previous = row.x;
  }
  return parts.join(" ");
}

function PassKey({ pass, side }: { pass: Pass; side: "a" | "b" }) {
  return (
    <span class={`ri-key ${side}`} data-pass={pass.lap}>
      <i aria-hidden="true" />
      <span>
        <b>{pass.runNumber !== null ? `R${pass.runNumber} · ` : ""}L{pass.lap} · {pass.greenExposure !== null ? `${pass.greenExposure.toFixed(1)} green` : "—"}</b>
        {pass.fuelStartGal !== null && <span> · {pass.fuelStartGal.toFixed(1)} gal</span>}
        <small>{passRole(pass.role)} · {seconds(pass.sectionS)} s{!pass.eligible && pass.exclusionReasons.length > 0 && <em> · {reasonsText(pass.exclusionReasons)}</em>}</small>
      </span>
    </span>
  );
}

function mapPath(points: readonly { x: number; y: number }[]): string {
  return points.map((point, index) => `${index === 0 ? "M" : "L"}${n(point.x)},${n(point.y)}`).join(" ");
}

/** Map points that may place a section label or the inspection cursor: all of them when every point has its lap fraction, else none. */
export function sectionMapPoints(map: MapGeometry | null): readonly MapPoint[] {
  return map?.sectionsReady === true ? map.points : [];
}

export interface SectionsViewProps {
  inv: Investigation;
  comparison: Comparison | null;
  section: Section | null;
  map: MapGeometry | null;
  onSelectSection(id: string): void;
  onOpenTraces(request: { laps: number[]; startPct: number; endPct: number; signalKeys: string[] }): void;
}

export function SectionsView({ inv, comparison, section, map, onSelectSection, onOpenTraces }: SectionsViewProps) {
  const root = useRef<HTMLDivElement>(null);
  const mapRef = useRef<SVGSVGElement>(null);
  const mapSize = useElementSize(mapRef);
  const k = scaleFor(useRemPx());
  const pair = section && comparison?.inspection ? comparison.inspection.pairs.get(section.id) ?? null : null;
  // The highlight sits where the geometry says only when every map point carries its lap fraction (audit R8).
  const highlight = map?.sectionsReady === true;
  const sections = inv.sections.sections;
  const mapPoints: readonly MapPoint[] = map?.points ?? [];
  // Labels and the synchronized map cursor are placed by lap fraction too: without every fraction they are not drawn (R8, review finding 6).
  const placedPoints = useMemo(() => sectionMapPoints(map), [map]);
  const inspectionPoints = useMemo<InspectionMapPoint[]>(() => placedPoints.map((point) => [point.x, point.y, point.percent] as const), [placedPoints]);
  const span = map ? Math.max(map.bounds[2], map.bounds[3]) : 1;
  // Section labels are at least the theme's data size (15 px at the default root) on screen whatever the map's scale, in a circle that holds them.
  const pxPerUnit = map && mapSize.width > 0 && mapSize.height > 0 ? Math.min(mapSize.width / map.bounds[2], mapSize.height / map.bounds[3]) : 0;
  const labelFont = pxPerUnit > 0 ? Math.max(span * 0.05, (theme.typography.dataPx * k) / pxPerUnit) : span * 0.05;
  const labelRadius = labelFont * 0.9;
  const range = (start: number, end: number): MapPoint[] => mapPoints.filter((point) => point.percent >= start - 1e-9 && point.percent <= end + 1e-9);
  const meterScale = Math.max(0, ...sections.map((s) => Math.abs(comparison?.corners.find((c) => c.id === s.id)?.deltaS ?? 0)));
  const labels = useMemo(() => {
    if (!map || placedPoints.length < 3) return [] as { id: string; text: string; x: number; y: number; ax: number; ay: number; leader: boolean }[];
    const placed: { id: string; text: string; x: number; y: number; ax: number; ay: number; leader: boolean }[] = [];
    const offsets: [number, number][] = [[0, 0], [0.09, 0], [-0.09, 0], [0, 0.1], [0, -0.1], [0.1, 0.09], [-0.1, -0.09], [0.1, -0.1], [-0.1, 0.1]];
    for (const s of sections) {
      const middle = (s.startPct + s.endPct) / 2;
      const anchor = placedPoints.reduce((best, point) => (Math.abs(point.percent - middle) < Math.abs(best.percent - middle) ? point : best), placedPoints[0]!);
      const candidates = offsets.map(([dx, dy]) => ({ x: anchor.x + dx * span, y: anchor.y + dy * span }));
      const chosen = candidates.find((c) => placed.every((o) => Math.hypot(o.x - c.x, o.y - c.y) >= labelRadius * 2.1)) ?? { x: anchor.x, y: anchor.y };
      placed.push({ id: s.id, text: s.label, x: chosen.x, y: chosen.y, ax: anchor.x, ay: anchor.y, leader: Math.abs(chosen.x - anchor.x) + Math.abs(chosen.y - anchor.y) > 1e-9 });
    }
    return placed;
  }, [map, placedPoints, sections, span, labelRadius]);

  useEffect(() => {
    if (!root.current || !pair) return undefined;
    const initial = Math.max(0, pair.rows.findIndex((row) => Math.abs(row.x - pair.startPct) < 1e-9));
    const inspection = createRaceInspection(root.current, { rows: pair.rows, start: pair.contextStartPct, end: pair.endPct, initialIndex: initial, mapPoints: inspectionPoints });
    return () => inspection.dispose();
  }, [pair, inspectionPoints]);

  if (sections.length === 0 || !comparison?.inspection) {
    return (
      <div class="ri-card ri-empty" data-ri-view="sections">
        <strong>No sections</strong>
        {inv.sections.unavailableReason && <span>{inv.sections.unavailableReason}</span>}
      </div>
    );
  }
  const channels: Channel[] = pair ? [...PASS_CHANNELS, steeringChannel(pair.steering), SLIP] : [];
  const fraction = (pct: number): number => (pair ? (pct - pair.contextStartPct) / Math.max(1e-9, pair.endPct - pair.contextStartPct) : 0);
  const lead = comparison.actions.find((action) => action.sectionId === section?.id) ?? null;
  return (
    <div class="ri-sections" ref={root} data-ri-view="sections" data-ri-root>
      <aside class="ri-side">
        <div class="ri-map-panel">
          <div class="ri-card-label"><span class="ri-card-title">Track</span>{!map && <span>not recorded</span>}</div>
          {map && mapPoints.length > 2 ? (
            <svg ref={mapRef} class="ri-map" data-ri-map tabIndex={0} role="group" viewBox={map.viewBox} aria-label="Track map · section and approach">
              <path d={mapPath(mapPoints)} class="ri-map-track" />
              {!highlight ? null : pair ? (
                <>
                  <path d={mapPath(range(pair.contextStartPct, pair.startPct))} class="ri-map-approach" />
                  <path d={mapPath(range(pair.startPct, pair.endPct))} class="ri-map-section" />
                </>
              ) : section ? (
                <path d={mapPath(range(section.startPct, section.endPct))} class="ri-map-section" />
              ) : null}
              {map.startFinish && <line x1={n(map.startFinish.x1)} y1={n(map.startFinish.y1)} x2={n(map.startFinish.x2)} y2={n(map.startFinish.y2)} class="ri-map-start" />}
              {labels.map((label) => {
                const selected = label.id === section?.id;
                return (
                  <g key={label.id} class={`ri-map-label${selected ? " selected" : ""}`} role="button" tabIndex={0} aria-label={`Section ${label.text}`} aria-pressed={selected}
                    onClick={() => onSelectSection(label.id)} onKeyDown={(event: KeyboardEvent) => { if (event.key === "Enter" || event.key === " ") onSelectSection(label.id); }}>
                    {label.leader && <line x1={n(label.ax)} y1={n(label.ay)} x2={n(label.x)} y2={n(label.y)} class="ri-map-leader" />}
                    <circle cx={n(label.x)} cy={n(label.y)} r={n(labelRadius)} />
                    <text x={n(label.x)} y={n(label.y + labelFont * 0.33)} text-anchor="middle" font-size={n(labelFont)}>{label.text}</text>
                  </g>
                );
              })}
              <circle data-ri-map-cursor class="ri-map-cursor" r={n(span * 0.025)} style="display:none" />
            </svg>
          ) : (
            <div class="ri-unavailable">Not recorded</div>
          )}
          {highlight && <div class="ri-map-legend"><span><i class="approach" />Approach</span><span><i class="section" />Section</span></div>}
        </div>
        <div class="ri-section-list" role="group" aria-label="Track sections">
          <div class="ri-section-head"><span>Section</span><span title="Later median − earlier median">Later − earlier · s</span></div>
          {sections.map((s) => {
            const corner = comparison.corners.find((c) => c.id === s.id);
            const delta = corner?.deltaS ?? null;
            const selected = s.id === section?.id;
            const width = meterScale <= 0 || delta === null ? 0 : (Math.abs(delta) / meterScale) * 48;
            return (
              <button type="button" key={s.id} class={`ri-section${selected ? " selected" : ""}`} aria-pressed={selected} data-section={s.id}
                title={`${s.provenance}${corner ? ` · ${corner.baselineCount} earlier · ${corner.comparedCount} later` : " · too few laps"}`}
                onClick={() => onSelectSection(s.id)}>
                <span class="ri-section-id">{s.label}</span>
                <span class="ri-section-range">{percentText(s.startPct)}–{percentText(s.endPct)}%{s.boundaryStatus === "driver_confirmed_grouping" && <em>grouped</em>}
                  <span class="ri-meter" aria-hidden="true">{delta !== null && <i class={delta < 0 ? "gain" : undefined} style={delta < 0 ? `width:${n(width)}%;left:${n(50 - width)}%` : `width:${n(width)}%;left:50%`} />}</span>
                </span>
                <strong class={delta !== null && delta < 0 ? "gain" : undefined}>{signedText(delta)}</strong>
              </button>
            );
          })}
        </div>
      </aside>
      <section class="ri-telemetry" aria-label="Section telemetry">
        {!pair ? (
          <div class="ri-empty"><strong>No comparable passes</strong></div>
        ) : (
          <>
            <header class="ri-pass-head">
              <h2>Section {section?.label} <small>{percentText(pair.startPct)}–{percentText(pair.endPct)}%</small></h2>
              <div class="ri-pass-keys"><PassKey pass={pair.a} side="a" /><PassKey pass={pair.b} side="b" /></div>
            </header>
            <div class="ri-timing">
              {pair.timingComplete ? (
                <>
                  <span>Entry<b>{signedText(pair.entryGapS)}</b></span>
                  <span>Inside<b>{signedText(pair.sectionDeltaS)}</b></span>
                  <span>Exit<b>{signedText(pair.exitGapS)}</b></span>
                </>
              ) : (
                <>
                  <span>Inside<b>{signedText(pair.sectionDeltaS)}</b></span>
                  <span class="ri-muted">Gap withheld · approach incomplete</span>
                </>
              )}
              <span class="ri-entry-speed">Entry speed<b>{signedText(pair.entrySpeedDifferenceMph, 1)} mph</b></span>
            </div>
            <div class="ri-channels">
              {channels.map((channel, index) => {
                const last = index === channels.length - 1;
                const plot = channelScale(pair, channel);
                const has = pair.rows.some((row) => (channel.key === "gap" ? row.gap !== null : valueOf(row, "a", channel.key) !== null || valueOf(row, "b", channel.key) !== null));
                return (
                  <div class={`ri-channel${last ? " last" : ""}`} key={channel.key} data-channel={channel.key}>
                    <div class="ri-channel-label" title={channel.title}>
                      {channel.label}<small>{channel.unit}</small>
                      <span class="ri-readings">
                        {channel.key === "gap" ? <output data-ri-read="gap" data-digits="3">—</output> : (
                          <>
                            <output class="a" data-ri-read={channel.key} data-side="a" data-digits={channel.digits}>—</output>
                            <output class="b" data-ri-read={channel.key} data-side="b" data-digits={channel.digits}>—</output>
                          </>
                        )}
                      </span>
                    </div>
                    <div class="ri-chart-wrap">
                      <div class="ri-y-axis" aria-hidden="true"><span class="top">{numberText(plot.yMax, channel.axisDigits)}</span><span class="bottom">{numberText(plot.yMin, channel.axisDigits)}</span></div>
                      <div class="ri-plot-area">
                        {index === 0 && (
                          <>
                            <span class="ri-zone" style="left:0">Approach</span>
                            <span class="ri-zone" style={`left:${n(fraction(pair.startPct) * 100)}%`}>Section {section?.label}</span>
                          </>
                        )}
                        {!has && <span class="ri-zone missing" title={channel.title}>{channel.key === "steering" && !pair.steering.comparable ? "Not compared" : "Not recorded"}</span>}
                        <svg class="ri-chart" data-ri-chart tabIndex={0} role="group" viewBox={`0 0 ${VIEW_WIDTH} ${VIEW_HEIGHT}`} preserveAspectRatio="none"
                          data-view-width={VIEW_WIDTH} data-left="0" data-width={VIEW_WIDTH} aria-label={`${channel.label} · arrow keys move the cursor`}>
                          {[plot.yMin, (plot.yMin + plot.yMax) / 2, plot.yMax].map((tick) => <line key={tick} x1="0" x2={VIEW_WIDTH} y1={n(plot.y(tick))} y2={n(plot.y(tick))} class="ri-grid" />)}
                          <line x1={n(plot.x(pair.startPct))} x2={n(plot.x(pair.startPct))} y1="0" y2={VIEW_HEIGHT} class="ri-boundary" />
                          {channel.key === "gap" ? (
                            <path d={line(pair, plot, (row) => row.gap)} class="ri-line gap" />
                          ) : (
                            <>
                              <path d={line(pair, plot, (row) => valueOf(row, "a", channel.key))} class="ri-line a" />
                              <path d={line(pair, plot, (row) => valueOf(row, "b", channel.key))} class="ri-line b" />
                            </>
                          )}
                          <line data-ri-cursor x1={n(plot.x(pair.startPct))} x2={n(plot.x(pair.startPct))} y1="0" y2={VIEW_HEIGHT} class="ri-cursor" />
                        </svg>
                      </div>
                    </div>
                    {last && (
                      <div class="ri-x-axis">
                        {[[pair.contextStartPct, "start"], [pair.startPct, "middle"], [pair.endPct, "end"]].map(([x, anchor]) => (
                          <span class={String(anchor)} key={String(anchor)} style={`left:${n(fraction(x as number) * 100)}%`}>{percentText(x as number)}%</span>
                        ))}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
            <footer class="ri-foot">
              <span>Gap 0 at {percentText(pair.contextStartPct)}% · {pair.contextLabel}</span>
              {comparison.inspection && <span>{comparison.inspection.mode === "matched_pool_extremes" ? `Fastest · slowest of ${comparison.inspection.poolLaps.length} laps` : "Group medians"}</span>}
              <output data-ri-position>{percentText(pair.startPct)}% lap</output>
            </footer>
            <div class="ri-action-slot">
              {lead ? <span class="ri-muted"><b>{lead.title}.</b> {lead.instruction}</span> : <span />}
              <button type="button" class="ri-pill-button primary" onClick={() => onOpenTraces({ laps: [pair.a.lap, pair.b.lap], startPct: pair.contextStartPct, endPct: pair.endPct, signalKeys: ["speed", "throttle", "brake", "steering"] })}>Open in Traces</button>
            </div>
          </>
        )}
      </section>
    </div>
  );
}

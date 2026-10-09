import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { useElementSize } from "../../core/element-size";
import { theme } from "../../gen/theme";
import type { TelemetryCursor } from "../cursor/telemetry-cursor";
import { n, percentText, seconds, signedText } from "../format";
import { mapPointAt, type MapGeometry, type MapPoint } from "../geometry/map-model";
import type { TraceData } from "../geometry/trace-model";
import type { Investigation, PacePriority, Section } from "../investigation-model";
import type { RaceViewData } from "../types";
import { contextTitle, greenText, lapContexts, tiresText, trafficCell, type LapContext } from "./section-context";
import { POSITION_SIGNALS, sectionRacingLines } from "./section-lines";
import { deltaBar, lapsToOpen, shadedLaps, type SectionLapEntry, type SectionLaps, type ShadedLap } from "./section-laps";
import { projectOnSection, SECTION_SIGNALS, sectionPointAt, sectionTrackPoints, tracedLaps } from "./section-traces-model";
import { SectionTraces, type SectionTraceLap, type SectionTraceStatus } from "./SectionTraces";
import { scaleFor, useRemPx } from "./unit";
import "./sections.css";

/**
 * Sections (backlog #48, #49, #64, #75, #76): per detected section, the three fastest and three slowest clean laps by that section's
 * time, the section's median as zero, green shades faster and red shades slower. The map draws each lap's own racing line through
 * the section from its recorded positions (no line where it recorded none); the tab strip picks the section; the lap rows, with the
 * laps on the tires and the lap's traffic beside the time, select the laps the telemetry draws and `Open in Traces` opens with the
 * cursor at the section start. The right panel draws those laps' speed, throttle, brake and steering over the section with the
 * median lap in grey, cursor-synced with a marker on the section segment. Every figure is the projector's (`section_laps`, the CCOL
 * traces, the race and runs views). Section tabs follow the producer's recoverable time, greatest first.
 */

/** The median lap's line: the chart reference grey. */
const MEDIAN_STROKE = "var(--color-chart-reference)";

function mapPath(points: readonly { x: number; y: number }[]): string {
  return points.map((point, index) => `${index === 0 ? "M" : "L"}${n(point.x)},${n(point.y)}`).join(" ");
}

/** Map points that may place a section label or segment: all of them when every point has its lap fraction, else none. */
export function sectionMapPoints(map: MapGeometry | null): readonly MapPoint[] {
  return map?.sectionsReady === true ? map.points : [];
}

export interface SectionsOpenRequest {
  laps: number[];
  startPct: number;
  endPct: number;
  signalKeys: string[];
  /** Where the Traces cursor starts: the section's start. */
  cursorPct: number;
}

export interface SectionsViewProps {
  inv: Investigation;
  section: Section | null;
  map: MapGeometry | null;
  /** The investigation view's `section_laps`; null when the view has none. */
  sectionLaps: SectionLaps | null;
  onSelectSection(id: string): void;
  onOpenTraces(request: SectionsOpenRequest): void;
  /**
   * The analysis's trace store with these channels present (the loader's `ensureSignals`: the Traces tab's data, fetched once per
   * analysis, missing channels only). Called when the view first needs telemetry and on Retry, never on pointer movement.
   */
  traces?(ids: readonly string[]): Promise<TraceData>;
  /** Identifies the analysis (the cursor key and the trace load). */
  analysisKey?: string;
  /** The race view (each lap's traffic, #75); none draws `—`. */
  race?: RaceViewData | null;
  /** The runs view's runs (the tire set's laps, #75); none draws `—`. */
  runs?: readonly unknown[] | null;
}

const OPEN_SIGNALS = ["speed", "throttle", "brake", "steering"];
/** What the view loads: the four telemetry channels and the positions the racing lines are drawn from. */
const LOAD_SIGNALS = [...SECTION_SIGNALS, ...POSITION_SIGNALS];

function countText(count: number): string {
  return `${count} clean ${count === 1 ? "lap" : "laps"} timed`;
}

function LapRow(props: { lap: ShadedLap; listed: readonly ShadedLap[]; context: LapContext | null; selected: boolean; hot: boolean; onToggle(): void; onHot(lap: number | null): void }) {
  const { lap, context } = props;
  const bar = deltaBar(lap.deltaS, props.listed);
  const tag = contextTitle(context);
  const traffic = context?.traffic?.fraction ?? null;
  const cell = trafficCell(context?.traffic);
  return (
    <button
      type="button" class={`ri-lap-row ri-sgrid${props.selected ? " selected" : ""}${props.hot ? " hot" : ""}`} data-lap={lap.lap} data-shade={lap.shade}
      aria-pressed={props.selected} style={`--lap:var(--sl-${lap.shade})`} title={`L${lap.lap} · ${seconds(lap.sectionS)} s · ${signedText(lap.deltaS)} s to the median${tag ? ` · ${tag}` : ""}`}
      onClick={props.onToggle} onPointerEnter={() => props.onHot(lap.lap)} onPointerLeave={() => props.onHot(null)} onFocus={() => props.onHot(lap.lap)} onBlur={() => props.onHot(null)}
    >
      <span class="ri-sl-sw" aria-hidden="true" />
      <span class="ri-n">{lap.lap}</span>
      <span class="ri-tm">{seconds(lap.sectionS)}</span>
      <span class={`ri-sl-dl ${bar.side}`}>{signedText(lap.deltaS)}</span>
      <span class="ri-sl-bar" aria-hidden="true">{bar.widthPct > 0 && <i class={bar.side} style={bar.side === "gain" ? `right:50%;width:${n(bar.widthPct)}%` : `left:50%;width:${n(bar.widthPct)}%`} />}</span>
      <span class="ri-sl-tires" data-sl-tires>{tiresText(context?.tires)}</span>
      {/* Gap ledger G6: green laps into the run (the backend's run_age_laps). Entry speed reads from the dashed approach on the chart;
          a per-lap entry figure waits for the backend (race_inspection.comparison_inspection's entry_speed_difference). */}
      <span class="ri-sl-green" data-sl-green>{greenText(context?.green)}</span>
      {/* One line: the share's bar, the share, and the side in words, which sections.css draws as the Race chart's mark where the
          column is narrow (the words stay for the screen reader and the cell's text). */}
      <span class="ri-sl-traffic" data-sl-traffic>
        <span class="ri-sl-tbar" aria-hidden="true">{traffic !== null && traffic > 0 && <i style={`width:${n(Math.min(1, traffic) * 100)}%`} />}</span>
        <span class="ri-sl-tq">{cell.share}</span>
        {cell.side !== null && <span class="ri-sl-side" data-side={context?.traffic?.side ?? undefined}><span class="ri-sl-sword">{` · ${cell.side}`}</span></span>}
      </span>
    </button>
  );
}

/** The viewBox numbers of the map (`x y w h`). */
function viewBoxOf(map: MapGeometry): [number, number, number, number] {
  const parts = map.viewBox.split(/[\s,]+/).map(Number);
  return parts.length === 4 && parts.every(Number.isFinite) ? (parts as [number, number, number, number]) : map.bounds;
}

/** Element pixels of a map point in an element of `width x height` showing `box` with `preserveAspectRatio` meet (centred). */
export function mapPixel(box: readonly [number, number, number, number], width: number, height: number, x: number, y: number): { px: number; py: number } {
  const scale = Math.min(width / box[2], height / box[3]);
  return { px: (width - box[2] * scale) / 2 + (x - box[0]) * scale, py: (height - box[3] * scale) / 2 + (y - box[1]) * scale };
}

/** The map point under element pixels (the inverse of `mapPixel`). */
export function mapUnits(box: readonly [number, number, number, number], width: number, height: number, px: number, py: number): { x: number; y: number } {
  const scale = Math.min(width / box[2], height / box[3]) || 1;
  return { x: box[0] + (px - (width - box[2] * scale) / 2) / scale, y: box[1] + (py - (height - box[3] * scale) / 2) / scale };
}

/** First priority for a section is its greatest recoverable time; unattached priorities never display. */
export function sectionPriorities(inv: Investigation): Map<string, PacePriority> {
  const detected = new Set(inv.sections.sections.map((s) => s.id));
  const priorities = new Map<string, PacePriority>();
  for (const p of [...(inv.paceAttribution?.priorities ?? [])].sort((a, b) => b.recoverableS - a.recoverableS)) {
    if (p.sectionId !== null && detected.has(p.sectionId) && !priorities.has(p.sectionId)) priorities.set(p.sectionId, p);
  }
  return priorities;
}

export function orderedSections(inv: Investigation): Section[] {
  const priorities = sectionPriorities(inv);
  return [...inv.sections.sections].sort((a, b) => (priorities.get(b.id)?.recoverableS ?? -1) - (priorities.get(a.id)?.recoverableS ?? -1));
}

export function paceFigures(p: PacePriority): { time: string; energy: string | null; energyEvidence: "derived" | "proxy" } {
  return {
    time: `${p.recoverableS > 0 ? "-" : ""}${p.recoverableS.toFixed(2)} s · best L${p.bestLap}`,
    energy: p.tireEnergyShare !== null && p.tireEnergyGrade !== null ? `${(p.tireEnergyShare * 100).toFixed(0)} % tire energy${p.tireEnergyGrade === "load_only" ? " proxy" : ""}` : null,
    energyEvidence: p.tireEnergyGrade === "load_only" ? "proxy" : "derived",
  };
}

function PaceFigures({ priority, tab = false }: { priority: PacePriority | undefined; tab?: boolean }) {
  if (!priority) return null;
  const figures = paceFigures(priority);
  return <span data-pace-figures style={tab ? "display:flex;flex-direction:column;gap:0.125rem;font-size:var(--text-compact);letter-spacing:0;text-transform:none" : "display:flex;flex-wrap:wrap;gap:var(--space-4);text-transform:none;letter-spacing:0"}>
    <span data-evidence="derived" style="color:var(--evidence-derived)">{figures.time}</span>
    {figures.energy !== null && <span data-evidence={figures.energyEvidence} style={`color:var(--evidence-${figures.energyEvidence})`}>{figures.energy}</span>}
  </span>;
}

export function SectionsView({ inv, section, map, sectionLaps, onSelectSection, onOpenTraces, traces, analysisKey = "", race = null, runs = null }: SectionsViewProps) {
  const sections = useMemo(() => orderedSections(inv), [inv]);
  const priorities = useMemo(() => sectionPriorities(inv), [inv]);
  const entry: SectionLapEntry | null = section ? sectionLaps?.get(section.id) ?? null : null;
  const { fastest, slowest } = useMemo(() => shadedLaps(entry), [entry]);
  const listed = useMemo(() => [...fastest, ...slowest], [fastest, slowest]);
  const [picked, setPicked] = useState<{ sectionId: string | null; laps: ReadonlySet<number> }>({ sectionId: null, laps: new Set() });
  const selected: ReadonlySet<number> = picked.sectionId === section?.id ? picked.laps : new Set();
  const [hot, setHot] = useState<number | null>(null);
  const hotLap = listed.find((lap) => lap.lap === hot) ?? null;
  const opens = lapsToOpen(listed.map((lap) => lap.lap), selected);
  const contexts = useMemo(() => lapContexts(race, runs), [race, runs]);
  // The approach is bounded by the previous section's start on the lap (race_inspection.approach_start); `sections` is in priority order, so
  // the previous one is the nearest earlier start, not the previous tab.
  const previousStartPct = useMemo(() => {
    if (!section) return null;
    const earlier = inv.sections.sections.map((s) => s.startPct).filter((start) => start < section.startPct);
    return earlier.length ? Math.max(...earlier) : null;
  }, [inv, section]);

  const svgRef = useRef<SVGSVGElement>(null);
  const overlayRef = useRef<SVGSVGElement>(null);
  const markerRef = useRef<HTMLDivElement>(null);
  const mapSize = useElementSize(svgRef);
  const k = scaleFor(useRemPx());
  const placed = useMemo(() => sectionMapPoints(map), [map]);
  const span = map ? Math.max(map.bounds[2], map.bounds[3]) : 1;
  const pxPerUnit = map && mapSize.width > 0 && mapSize.height > 0 ? Math.min(mapSize.width / map.bounds[2], mapSize.height / map.bounds[3]) : 0;
  const unitsPerPx = pxPerUnit > 0 ? 1 / pxPerUnit : span / 400;
  // Section labels keep one size on screen (1.25x the data size) whatever the map's scale.
  const labelFont = pxPerUnit > 0 ? theme.typography.dataPx * 1.25 * k * unitsPerPx : span * 0.04;
  const labelRadius = labelFont * 0.9;
  /** Half the segment band (sections.css `.ri-sl-segment`, 1.625 rem) plus a pixel, in map units. */
  const band = 14 * k * unitsPerPx;
  /** A label's tap target: 44 px across on screen. */
  const hitRadius = 22 * k * unitsPerPx;
  const labels = useMemo(() => {
    const out: { id: string; text: string; x: number; y: number; ax: number; ay: number }[] = [];
    if (!map || placed.length < 3) return out;
    // Labels sit beside the track so the racing lines stay readable: two rings of places around the section's middle (in label radii),
    // nearest first, kept inside the map's box so none is clipped at its edge.
    const ring = (d: number): [number, number][] => [[d, 0], [-d, 0], [0, d], [0, -d], [d * 0.8, d * 0.8], [-d * 0.8, -d * 0.8], [d * 0.8, -d * 0.8], [-d * 0.8, d * 0.8]];
    const offsets: [number, number][] = [...ring(2.4), ...ring(4), [0, 0]];
    const [bx, by, bw, bh] = map.bounds;
    const inside = (c: { x: number; y: number }) => ({ x: Math.min(bx + bw - labelRadius, Math.max(bx + labelRadius, c.x)), y: Math.min(by + bh - labelRadius, Math.max(by + labelRadius, c.y)) });
    for (const s of sections) {
      const middle = (s.startPct + s.endPct) / 2;
      const anchor = placed.reduce((best, point) => (Math.abs(point.percent - middle) < Math.abs(best.percent - middle) ? point : best), placed[0]!);
      const candidates = offsets.map(([dx, dy]) => inside({ x: anchor.x + dx * labelRadius, y: anchor.y + dy * labelRadius }));
      // The first place clear of the track's segment band and the other labels; failing that, the one farthest from both.
      const clearance = (c: { x: number; y: number }) => Math.min(
        ...placed.map((p) => Math.hypot(p.x - c.x, p.y - c.y) - band),
        ...out.map((o) => Math.hypot(o.x - c.x, o.y - c.y) / 2.1),
      );
      const chosen = candidates.find((c) => clearance(c) >= labelRadius) ?? candidates.reduce((best, c) => (clearance(c) > clearance(best) ? c : best), candidates[0]!);
      out.push({ id: s.id, text: s.label, x: chosen.x, y: chosen.y, ax: anchor.x, ay: anchor.y });
    }
    return out;
  }, [map, placed, sections, labelRadius, band]);
  const segment = useMemo(() => (section ? placed.filter((point) => point.percent >= section.startPct - 1e-9 && point.percent <= section.endPct + 1e-9) : []), [placed, section]);

  // Telemetry: the trace store, loaded once when the section first has laps to draw (and on Retry); section and lap changes only redraw.
  const [loaded, setLoaded] = useState<{ key: string; status: SectionTraceStatus; data: TraceData | null }>({ key: analysisKey, status: "loading", data: null });
  const traceState = loaded.key === analysisKey ? loaded : { key: analysisKey, status: "loading" as const, data: null };
  const [attempt, setAttempt] = useState(0);
  const wantsTraces = traces !== undefined && listed.length > 0;
  const tracesRef = useRef(traces);
  tracesRef.current = traces;
  useEffect(() => {
    if (!wantsTraces || !tracesRef.current) return undefined;
    let live = true;
    setLoaded((current) => (current.key === analysisKey && current.status === "ready" ? current : { key: analysisKey, status: "loading", data: null }));
    tracesRef.current(LOAD_SIGNALS).then(
      (data) => { if (live) setLoaded({ key: analysisKey, status: "ready", data }); },
      () => { if (live) setLoaded({ key: analysisKey, status: "failed", data: null }); },
    );
    return () => { live = false; };
  }, [analysisKey, wantsTraces, attempt]);

  // Racing lines (#76): each listed lap's recorded path through the section; the hovered lap is drawn last, on top.
  const recorded = useMemo(() => {
    const byLap = new Map(sectionRacingLines(traceState.data, map, section, listed.map((lap) => lap.lap)).map((line) => [line.lap, line] as const));
    return listed.flatMap((lap) => {
      const line = byLap.get(lap.lap);
      return line ? [{ lap, line }] : [];
    });
  }, [traceState.data, map, section?.id, section?.startPct, section?.endPct, listed]);
  const lines = hot === null ? recorded : [...recorded.filter((item) => item.lap.lap !== hot), ...recorded.filter((item) => item.lap.lap === hot)];

  const selectedKey = [...selected].sort((a, b) => a - b).join(",");
  const drawnLaps: SectionTraceLap[] = useMemo(() => {
    const { laps, median } = tracedLaps(listed.map((lap) => lap.lap), selected, entry?.medianLap ?? null);
    const shade = new Map(listed.map((lap) => [lap.lap, lap.shade] as const));
    return [
      ...laps.map((lap) => ({ lap, stroke: `var(--sl-${shade.get(lap)})`, median: false })),
      ...(median !== null ? [{ lap: median, stroke: MEDIAN_STROKE, median: true }] : []),
    ];
  }, [listed, selectedKey, entry?.medianLap]);

  // The cursor across the section and its marker on the section segment. The map moves the cursor through `setFraction`; the cursor
  // moves the marker through `onFraction`. Pointer movement writes a transform and a canvas, never a request.
  const cursorRef = useRef<TelemetryCursor | null>(null);
  const fractionRef = useRef(0.5);
  const sectionPoints = useMemo(() => (section && placed.length > 1 ? sectionTrackPoints(placed, section) : []), [placed, section?.id, section?.startPct, section?.endPct]);
  const pointsRef = useRef(sectionPoints);
  pointsRef.current = sectionPoints;
  const mapRef = useRef(map);
  mapRef.current = map;
  const sizeRef = useRef(mapSize);
  sizeRef.current = mapSize;
  const placedRef = useRef(placed);
  placedRef.current = placed;
  /** The cursor's lap distance, when the chart reported one (the approach lies before the section segment). */
  const lapPctRef = useRef<number | null>(null);
  // Inside the section the marker rides the section segment; in the approach (local below 0) it sits at the chart's lap distance on
  // the track, so the chart and the map name the same place.
  const placeMarker = (local: number, lapPct?: number): void => {
    lapPctRef.current = lapPct ?? null;
    const marker = markerRef.current;
    const current = mapRef.current;
    if (!marker || !current) return;
    const point = local < 0 && lapPct != null && placedRef.current.length > 1 ? mapPointAt(placedRef.current, lapPct) : sectionPointAt(pointsRef.current, local);
    if (!point || sizeRef.current.width <= 0) {
      marker.style.visibility = "hidden";
      return;
    }
    const { px, py } = mapPixel(viewBoxOf(current), sizeRef.current.width, sizeRef.current.height, point.x, point.y);
    marker.style.transform = `translate3d(${px.toFixed(1)}px,${py.toFixed(1)}px,0)`;
    marker.style.visibility = "";
  };
  useEffect(() => { placeMarker(fractionRef.current, lapPctRef.current ?? undefined); }, [sectionPoints, mapSize.width, mapSize.height]);
  const zoneRect = useRef<DOMRect | null>(null);
  useEffect(() => {
    const drop = (): void => { zoneRect.current = null; };
    window.addEventListener("scroll", drop, { capture: true, passive: true });
    window.addEventListener("resize", drop, { passive: true });
    return () => {
      window.removeEventListener("scroll", drop, true);
      window.removeEventListener("resize", drop);
    };
  }, []);
  const drive = (event: PointerEvent): void => {
    const overlay = overlayRef.current;
    const current = mapRef.current;
    if (!overlay || !current || pointsRef.current.length < 2) return;
    zoneRect.current ??= overlay.getBoundingClientRect();
    const rect = zoneRect.current;
    const { x, y } = mapUnits(viewBoxOf(current), rect.width, rect.height, event.clientX - rect.left, event.clientY - rect.top);
    const local = projectOnSection(pointsRef.current, x, y);
    fractionRef.current = local;
    if (cursorRef.current) cursorRef.current.setFraction(local);
    else placeMarker(local);
  };

  if (sections.length === 0) {
    return (
      <div class="ri-card ri-empty" data-ri-view="sections">
        <strong>No sections</strong>
        {inv.sections.unavailableReason && <span>{inv.sections.unavailableReason}</span>}
      </div>
    );
  }

  const toggle = (lap: number): void => {
    const next = new Set(selected);
    if (next.has(lap)) next.delete(lap);
    else next.add(lap);
    setPicked({ sectionId: section?.id ?? null, laps: next });
  };
  const onTabKey = (event: KeyboardEvent): void => {
    const step = event.key === "ArrowRight" || event.key === "ArrowDown" ? 1 : event.key === "ArrowLeft" || event.key === "ArrowUp" ? -1 : 0;
    if (!step) return;
    event.preventDefault();
    const index = sections.findIndex((s) => s.id === section?.id);
    const next = sections[(index + step + sections.length) % sections.length]!;
    onSelectSection(next.id);
    (event.currentTarget as HTMLElement).parentElement?.querySelector<HTMLElement>(`[data-section="${next.id}"]`)?.focus();
  };
  const row = (lap: ShadedLap) => (
    <LapRow key={lap.lap} lap={lap} listed={listed} context={contexts.get(lap.lap) ?? null} selected={selected.has(lap.lap)} hot={hot === lap.lap} onToggle={() => toggle(lap.lap)} onHot={setHot} />
  );
  const range = section ? `${percentText(section.startPct)}–${percentText(section.endPct)}` : "";
  const head = (
    <>
      <h2>Section {section?.label} <small>{range} % lap</small></h2>
      <div class="ri-sl-facts">
        <PaceFigures priority={section ? priorities.get(section.id) : undefined} />
        <span>Median<b>{entry?.medianS != null ? `${seconds(entry.medianS)} s` : "—"}</b></span>
        <span>Clean laps<b>{entry ? entry.cleanLapCount : "—"}</b></span>
      </div>
    </>
  );

  return (
    <div class="ri-sl" data-ri-view="sections">
      <div class="ri-sl-map">
        <div class="ri-card-label">
          <span class="ri-card-title">Track</span>
          {listed.length > 0 && segment.length > 1 && (
            <span class="ri-sl-key"><span><i class="g" />Fastest {fastest.length}</span><span><i class="r" />Slowest {slowest.length}</span><span>clean laps · section {section?.label}</span></span>
          )}
          {!map && <span>not recorded</span>}
        </div>
        {map && map.points.length > 2 ? (
          <div class="ri-sl-stage" data-sl-map>
            <svg ref={svgRef} class="ri-sl-svg" viewBox={map.viewBox} aria-hidden="true" data-map-rotation={map.rotationDeg}>
              <path d={mapPath(map.points)} class="ri-sl-track" />
              {segment.length > 1 && <path d={mapPath(segment)} class="ri-sl-segment" />}
              {map.startFinish && <line x1={n(map.startFinish.x1)} y1={n(map.startFinish.y1)} x2={n(map.startFinish.x2)} y2={n(map.startFinish.y2)} class="ri-sl-start" />}
            </svg>
            {/* Over the map with the same box: the racing lines and the scrub band along the section (pointer there moves the cursor), then the labels. */}
            <svg ref={overlayRef} class="ri-sl-over" viewBox={map.viewBox} role="group" aria-label={`Track map · section ${section?.label ?? ""}`}>
              {segment.length > 1 && (
                <g class="ri-sl-zone" data-sl-zone aria-hidden="true" onPointerEnter={drive} onPointerMove={drive} onPointerDown={drive} onPointerLeave={() => { zoneRect.current = null; }}>
                  <path d={mapPath(segment)} class="ri-sl-scrub" />
                  {lines.map(({ lap, line }) => (
                    <path key={lap.lap} d={line.d} class={`ri-sl-line${hot === lap.lap ? " hot" : ""}${selected.has(lap.lap) ? " selected" : ""}`}
                      style={`--lap:var(--sl-${lap.shade})`} data-sl-line={lap.lap} data-shade={lap.shade}
                      onPointerEnter={() => setHot(lap.lap)} onPointerLeave={() => setHot(null)}>
                      <title>{`L${lap.lap} · ${seconds(lap.sectionS)} s · ${signedText(lap.deltaS)} s`}</title>
                    </path>
                  ))}
                </g>
              )}
              {labels.map((label) => {
                const isSelected = label.id === section?.id;
                return (
                  <g key={label.id} class={`ri-sl-label${isSelected ? " selected" : ""}`} role="button" tabIndex={0} aria-label={`Section ${label.text}`} aria-pressed={isSelected} data-map-section={label.id}
                    onClick={() => onSelectSection(label.id)} onKeyDown={(event: KeyboardEvent) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onSelectSection(label.id); } }}>
                    <line x1={n(label.ax)} y1={n(label.ay)} x2={n(label.x)} y2={n(label.y)} class="ri-sl-leader" />
                    <circle cx={n(label.x)} cy={n(label.y)} r={n(Math.max(labelRadius, hitRadius))} class="ri-sl-hit" />
                    <circle cx={n(label.x)} cy={n(label.y)} r={n(labelRadius)} />
                    <text x={n(label.x)} y={n(label.y + labelFont * 0.33)} text-anchor="middle" font-size={n(labelFont)}>{label.text}</text>
                  </g>
                );
              })}
            </svg>
            <div ref={markerRef} class="ri-map-marker ri-sl-marker" data-sl-marker aria-hidden="true" style="visibility:hidden"><i /></div>
          </div>
        ) : (
          <div class="ri-unavailable">Not recorded</div>
        )}
      </div>

      <div class="ri-sl-laps">
        <div class="ri-sl-tabs" role="tablist" aria-label="Sections">
          {sections.map((s) => (
            <button type="button" role="tab" key={s.id} class="ri-sl-tab" style={priorities.has(s.id) ? "height:auto;min-height:var(--touch-target);padding:0.5rem 0.875rem;background:var(--color-surface2);color:var(--ri-t1)" : undefined} data-section={s.id} aria-selected={s.id === section?.id} tabIndex={s.id === section?.id ? 0 : -1}
              title={`${percentText(s.startPct)}–${percentText(s.endPct)} % lap · ${s.provenance}`} onClick={() => onSelectSection(s.id)} onKeyDown={onTabKey}>
              {s.label}
              <PaceFigures priority={priorities.get(s.id)} tab />
            </button>
          ))}
        </div>
        <div class="ri-sl-list" role="tabpanel" aria-label={`Section ${section?.label ?? ""} laps`}>
          {listed.length > 0 ? (
            <>
              <div class="ri-sgrid ri-sl-cols" aria-hidden="true">
                <span /><span>Lap</span><span class="r">Time<span class="ri-sl-long"> · s</span></span><span class="r">Δ<span class="ri-sl-long"> median</span></span><span />
                <span class="r">Tires</span><span class="r">Green</span><span class="ri-sl-traffic">Traffic</span>
              </div>
              {fastest.map(row)}
              <div class="ri-sgrid ri-sl-median" data-sl-median><span /><span>Med</span><span class="r">{seconds(entry?.medianS)}</span><span class="r">0</span><span class="ri-sl-zero" aria-hidden="true" /><span /><span /><span /></div>
              {slowest.map(row)}
            </>
          ) : (
            <div class="ri-sl-none">{sectionLaps === null ? "No section times" : countText(entry?.cleanLapCount ?? 0)}</div>
          )}
        </div>
      </div>

      <div class="ri-sl-open">
        {section && listed.length > 0 && traces ? (
          <SectionTraces
            data={traceState.data} status={traceState.status} onRetry={() => setAttempt((value) => value + 1)}
            section={{ id: section.id, startPct: section.startPct, endPct: section.endPct, previousStartPct }} laps={drawnLaps} medianLap={entry?.medianLap ?? null} hot={hot}
            fractionRef={fractionRef} cursorRef={cursorRef} onFraction={placeMarker} analysisKey={analysisKey} head={head}
          />
        ) : (
          <div class="ri-st-head">{head}</div>
        )}
        <div class="ri-sl-action">
          <span>{opens.length ? `${opens.length} ${opens.length === 1 ? "lap" : "laps"}` : ""}</span>
          <output class="ri-sl-read" data-sl-read>{hotLap ? `L${hotLap.lap} · ${seconds(hotLap.sectionS)} s · ${signedText(hotLap.deltaS)} s` : ""}</output>
          <button type="button" class="ri-pill-button primary" disabled={!section || opens.length === 0}
            onClick={() => section && onOpenTraces({ laps: opens, startPct: section.startPct, endPct: section.endPct, signalKeys: OPEN_SIGNALS, cursorPct: section.startPct })}>
            Open in Traces
          </button>
        </div>
      </div>
    </div>
  );
}

import { useMemo, useRef, useState } from "preact/hooks";
import { useElementSize } from "../../core/element-size";
import { theme } from "../../gen/theme";
import { n, percentText, seconds, signedText } from "../format";
import type { MapGeometry, MapPoint } from "../geometry/map-model";
import type { Investigation, Section } from "../investigation-model";
import { deltaBar, lapsToOpen, shadedLaps, type SectionLapEntry, type SectionLaps, type ShadedLap } from "./section-laps";
import { scaleFor, useRemPx } from "./unit";
import "./sections.css";

/**
 * Sections (backlog #48, #49): per detected section, the three fastest and three slowest clean laps by that section's time, the
 * section's median as zero, green shades faster and red shades slower. The map draws them as six strands along the section; the
 * tab strip picks the section; the lap rows select the laps `Open in Traces` opens with the cursor at the section start. Every
 * figure is the projector's (`section_laps`); this view ranks nothing.
 */

function mapPath(points: readonly { x: number; y: number }[]): string {
  return points.map((point, index) => `${index === 0 ? "M" : "L"}${n(point.x)},${n(point.y)}`).join(" ");
}

/** A polyline moved sideways by `offset` (map units) along each point's normal; drawing only. */
export function offsetPath(points: readonly { x: number; y: number }[], offset: number): string {
  if (points.length < 2) return "";
  return mapPath(points.map((point, i) => {
    const a = points[Math.max(0, i - 1)]!;
    const b = points[Math.min(points.length - 1, i + 1)]!;
    const length = Math.hypot(b.x - a.x, b.y - a.y) || 1;
    return { x: point.x - ((b.y - a.y) / length) * offset, y: point.y + ((b.x - a.x) / length) * offset };
  }));
}

/** Map points that may place a section label or strand: all of them when every point has its lap fraction, else none. */
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
}

const OPEN_SIGNALS = ["speed", "throttle", "brake", "steering"];
/** Strand spacing and the section label size on screen, in px at the default root size. */
const STRAND_PX = 3.25;

function countText(count: number): string {
  return `${count} clean ${count === 1 ? "lap" : "laps"} timed`;
}

function LapRow(props: { lap: ShadedLap; listed: readonly ShadedLap[]; selected: boolean; hot: boolean; onToggle(): void; onHot(lap: number | null): void }) {
  const { lap } = props;
  const bar = deltaBar(lap.deltaS, props.listed);
  return (
    <button
      type="button" class={`ri-lap-row ri-sgrid${props.selected ? " selected" : ""}${props.hot ? " hot" : ""}`} data-lap={lap.lap} data-shade={lap.shade}
      aria-pressed={props.selected} style={`--lap:var(--sl-${lap.shade})`} title={`L${lap.lap} · ${seconds(lap.sectionS)} s · ${signedText(lap.deltaS)} s to the median`}
      onClick={props.onToggle} onPointerEnter={() => props.onHot(lap.lap)} onPointerLeave={() => props.onHot(null)} onFocus={() => props.onHot(lap.lap)} onBlur={() => props.onHot(null)}
    >
      <span class="ri-sl-sw" aria-hidden="true" />
      <span class="ri-n">{lap.lap}</span>
      <span class="ri-tm">{seconds(lap.sectionS)}</span>
      <span class={`ri-sl-dl ${bar.side}`}>{signedText(lap.deltaS)}</span>
      <span class="ri-sl-bar" aria-hidden="true">{bar.widthPct > 0 && <i class={bar.side} style={bar.side === "gain" ? `right:50%;width:${n(bar.widthPct)}%` : `left:50%;width:${n(bar.widthPct)}%`} />}</span>
    </button>
  );
}

export function SectionsView({ inv, section, map, sectionLaps, onSelectSection, onOpenTraces }: SectionsViewProps) {
  const sections = inv.sections.sections;
  const entry: SectionLapEntry | null = section ? sectionLaps?.get(section.id) ?? null : null;
  const { fastest, slowest } = useMemo(() => shadedLaps(entry), [entry]);
  const listed = useMemo(() => [...fastest, ...slowest], [fastest, slowest]);
  const [picked, setPicked] = useState<{ sectionId: string | null; laps: ReadonlySet<number> }>({ sectionId: null, laps: new Set() });
  const selected: ReadonlySet<number> = picked.sectionId === section?.id ? picked.laps : new Set();
  const [hot, setHot] = useState<number | null>(null);
  const hotLap = listed.find((lap) => lap.lap === hot) ?? null;
  const opens = lapsToOpen(listed.map((lap) => lap.lap), selected);

  const svgRef = useRef<SVGSVGElement>(null);
  const mapSize = useElementSize(svgRef);
  const k = scaleFor(useRemPx());
  const placed = useMemo(() => sectionMapPoints(map), [map]);
  const span = map ? Math.max(map.bounds[2], map.bounds[3]) : 1;
  const pxPerUnit = map && mapSize.width > 0 && mapSize.height > 0 ? Math.min(mapSize.width / map.bounds[2], mapSize.height / map.bounds[3]) : 0;
  const unitsPerPx = pxPerUnit > 0 ? 1 / pxPerUnit : span / 400;
  // Section labels keep one size on screen (1.25x the data size) whatever the map's scale.
  const labelFont = pxPerUnit > 0 ? theme.typography.dataPx * 1.25 * k * unitsPerPx : span * 0.04;
  const labelRadius = labelFont * 0.9;
  /** Half the strand band (sections.css `.ri-sl-segment`, 1.625 rem) plus a pixel, in map units. */
  const band = 14 * k * unitsPerPx;
  const labels = useMemo(() => {
    const out: { id: string; text: string; x: number; y: number; ax: number; ay: number }[] = [];
    if (!map || placed.length < 3) return out;
    // Labels sit beside the track so the strands stay readable: two rings of places around the section's middle (in label radii),
    // nearest first, kept inside the map's box so none is clipped at its edge.
    const ring = (d: number): [number, number][] => [[d, 0], [-d, 0], [0, d], [0, -d], [d * 0.8, d * 0.8], [-d * 0.8, -d * 0.8], [d * 0.8, -d * 0.8], [-d * 0.8, d * 0.8]];
    const offsets: [number, number][] = [...ring(2.4), ...ring(4), [0, 0]];
    const [bx, by, bw, bh] = map.bounds;
    const inside = (c: { x: number; y: number }) => ({ x: Math.min(bx + bw - labelRadius, Math.max(bx + labelRadius, c.x)), y: Math.min(by + bh - labelRadius, Math.max(by + labelRadius, c.y)) });
    for (const s of sections) {
      const middle = (s.startPct + s.endPct) / 2;
      const anchor = placed.reduce((best, point) => (Math.abs(point.percent - middle) < Math.abs(best.percent - middle) ? point : best), placed[0]!);
      const candidates = offsets.map(([dx, dy]) => inside({ x: anchor.x + dx * labelRadius, y: anchor.y + dy * labelRadius }));
      // The first place clear of the track's strand band and the other labels; failing that, the one farthest from both.
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
  const strandGap = STRAND_PX * k * unitsPerPx;

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
    <LapRow key={lap.lap} lap={lap} listed={listed} selected={selected.has(lap.lap)} hot={hot === lap.lap} onToggle={() => toggle(lap.lap)} onHot={setHot} />
  );
  const range = section ? `${percentText(section.startPct)}–${percentText(section.endPct)}` : "";
  const strands = segment.length > 1 ? listed : [];

  return (
    <div class="ri-sl" data-ri-view="sections">
      <div class="ri-sl-map">
        <div class="ri-card-label">
          <span class="ri-card-title">Track</span>
          {strands.length > 0 && (
            <span class="ri-sl-key"><span><i class="g" />Fastest {fastest.length}</span><span><i class="r" />Slowest {slowest.length}</span><span>clean laps · section {section?.label}</span></span>
          )}
          {!map && <span>not recorded</span>}
        </div>
        {map && map.points.length > 2 ? (
          <svg ref={svgRef} class="ri-sl-svg" data-sl-map viewBox={map.viewBox} role="group" aria-label={`Track map · section ${section?.label ?? ""}`}>
            <path d={mapPath(map.points)} class="ri-sl-track" />
            {segment.length > 1 && <path d={mapPath(segment)} class="ri-sl-segment" />}
            {strands.map((lap, i) => (
              <path key={lap.lap} d={offsetPath(segment, (i - (strands.length - 1) / 2) * strandGap)} class={`ri-sl-strand${hot === lap.lap ? " hot" : ""}${selected.has(lap.lap) ? " selected" : ""}`}
                style={`--lap:var(--sl-${lap.shade})`} data-strand={lap.lap} data-shade={lap.shade}
                onPointerEnter={() => setHot(lap.lap)} onPointerLeave={() => setHot(null)}>
                <title>{`L${lap.lap} · ${seconds(lap.sectionS)} s · ${signedText(lap.deltaS)} s`}</title>
              </path>
            ))}
            {map.startFinish && <line x1={n(map.startFinish.x1)} y1={n(map.startFinish.y1)} x2={n(map.startFinish.x2)} y2={n(map.startFinish.y2)} class="ri-sl-start" />}
            {labels.map((label) => {
              const isSelected = label.id === section?.id;
              return (
                <g key={label.id} class={`ri-sl-label${isSelected ? " selected" : ""}`} role="button" tabIndex={0} aria-label={`Section ${label.text}`} aria-pressed={isSelected} data-map-section={label.id}
                  onClick={() => onSelectSection(label.id)} onKeyDown={(event: KeyboardEvent) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onSelectSection(label.id); } }}>
                  <line x1={n(label.ax)} y1={n(label.ay)} x2={n(label.x)} y2={n(label.y)} class="ri-sl-leader" />
                  <circle cx={n(label.x)} cy={n(label.y)} r={n(labelRadius)} />
                  <text x={n(label.x)} y={n(label.y + labelFont * 0.33)} text-anchor="middle" font-size={n(labelFont)}>{label.text}</text>
                </g>
              );
            })}
          </svg>
        ) : (
          <div class="ri-unavailable">Not recorded</div>
        )}
      </div>

      <div class="ri-sl-laps">
        <div class="ri-sl-tabs" role="tablist" aria-label="Sections">
          {sections.map((s) => (
            <button type="button" role="tab" key={s.id} class="ri-sl-tab" data-section={s.id} aria-selected={s.id === section?.id} tabIndex={s.id === section?.id ? 0 : -1}
              title={`${percentText(s.startPct)}–${percentText(s.endPct)} % lap · ${s.provenance}`} onClick={() => onSelectSection(s.id)} onKeyDown={onTabKey}>
              {s.label}
            </button>
          ))}
        </div>
        <div class="ri-sl-list" role="tabpanel" aria-label={`Section ${section?.label ?? ""} laps`}>
          {listed.length > 0 ? (
            <>
              <div class="ri-sgrid ri-sl-cols" aria-hidden="true"><span /><span>Lap</span><span class="r">Time · s</span><span class="r">Δ median</span><span /></div>
              {fastest.map(row)}
              <div class="ri-sgrid ri-sl-median" data-sl-median><span /><span>Med</span><span class="r">{seconds(entry?.medianS)}</span><span class="r">0</span><span class="ri-sl-zero" aria-hidden="true" /></div>
              {slowest.map(row)}
            </>
          ) : (
            <div class="ri-sl-none">{sectionLaps === null ? "No section times" : countText(entry?.cleanLapCount ?? 0)}</div>
          )}
        </div>
      </div>

      <div class="ri-sl-open">
        <h2>Section {section?.label} <small>{range} % lap</small></h2>
        <div class="ri-sl-facts">
          <span>Median<b>{entry?.medianS != null ? `${seconds(entry.medianS)} s` : "—"}</b></span>
          <span>Clean laps<b>{entry ? entry.cleanLapCount : "—"}</b></span>
        </div>
        <output class="ri-sl-read" data-sl-read>{hotLap ? `L${hotLap.lap} · ${seconds(hotLap.sectionS)} s · ${signedText(hotLap.deltaS)} s` : ""}</output>
        <div class="ri-sl-action">
          <span>{opens.length ? `${opens.length} ${opens.length === 1 ? "lap" : "laps"}` : ""}</span>
          <button type="button" class="ri-pill-button primary" disabled={!section || opens.length === 0}
            onClick={() => section && onOpenTraces({ laps: opens, startPct: section.startPct, endPct: section.endPct, signalKeys: OPEN_SIGNALS, cursorPct: section.startPct })}>
            Open in Traces
          </button>
        </div>
      </div>
    </div>
  );
}

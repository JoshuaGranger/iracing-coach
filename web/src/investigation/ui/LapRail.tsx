import { useMemo, useRef, useState } from "preact/hooks";
import { lapTime, reasonsText, runColor, runDash } from "../format";
import type { Comparison } from "../investigation-model";
import type { GroupRole } from "./comparison-model";
import {
  conditionsText,
  passesFilters,
  pitRoadSeconds,
  railDelta,
  railFlag,
  railGroups,
  statusChip,
  type LapFlag,
  type LapIncidents,
  type PitStop,
} from "../geometry/rail-model";
import { isCleanLap, lapColor, lapProgressEnds, sectorCells, sectorStarts, type SectorCell, type TraceData } from "../geometry/trace-model";
import type { TraceLapData } from "../types";
import { durationText, PitCard, PitGlyph, pitLabel } from "./PitPictogram";

export { passesFilters } from "../geometry/rail-model";

/**
 * The lap rail: a strict grid with one column track per field (swatch, lap chip, flag, time, Δ, sectors, fuel, incidents, pit);
 * the header row uses the same template and rows never wrap, so nothing can misalign. The lap chip carries the backend's screening
 * (hollow: not used in the run's pace fit; dashed: partial) with its reasons; the fuel cell holds the figure or `—`, nothing else.
 * Lap colour is race progress over the full phase, its endpoints in the rail's toolbar. One state model: selected (lap-colour edge
 * and tint, lit swatch, `aria-pressed`), spotlight (selected plus a lap-colour outline and number plate), hover (surface colour
 * only) and keyboard focus (`:focus-visible` only, cleared by any pointer use, so a click never leaves a box behind).
 */

export interface LapRailProps {
  data: TraceData;
  runs: readonly unknown[];
  selected: ReadonlySet<number>;
  spotlight: number | null;
  zeroLap: number | null;
  greenOnly: boolean;
  cleanOnly: boolean;
  incidents: ReadonlyMap<number, LapIncidents>;
  pitStops: readonly PitStop[];
  onToggle(lap: number): void;
  onGreenOnly(value: boolean): void;
  onCleanOnly(value: boolean): void;
  onFastest(): void;
  onBestThree(): void;
  onClear(): void;
  onShowAll(): void;
  /** The selected comparison's groups by lap: the rail marks them. */
  groups?: ReadonlyMap<number, GroupRole>;
  comparison?: Comparison | null;
}

const SECTOR_TITLE: Record<SectorCell["state"], string> = { sb: "session best", pb: "personal best", n: "", nc: "lap not clean", na: "not recorded" };

function Flag({ flag }: { flag: LapFlag }) {
  if (flag === "green") return <i class="ri-dot-mark green" title="Green" />;
  if (flag === "caution") return <i class="ri-dot-mark caution" title="Caution" />;
  if (flag === "white" || flag === "checkered") {
    return (
      <svg class={`ri-flag-mark ${flag}`} width="17" height="15" viewBox="0 0 16 14" role="img" aria-label={flag === "white" ? "White flag" : "Checkered flag"}>
        <line x1="2" y1="1" x2="2" y2="13.5" class="ri-flag-pole" />
        <rect x="3" y="1.2" width="11" height="7.2" class="ri-flag-cloth" />
        {flag === "checkered" && [0, 1, 2, 3, 4, 5, 6, 7].filter((cell) => ((cell % 4) + Math.floor(cell / 4)) % 2 === 1).map((cell) => (
          <rect key={cell} class="ri-flag-check" x={3 + (cell % 4) * 2.75} y={1.2 + Math.floor(cell / 4) * 3.6} width="2.75" height="3.6" />
        ))}
      </svg>
    );
  }
  return null;
}

function countText(value: number | null): string {
  if (value === null) return "—";
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

export function LapRail(props: LapRailProps) {
  const { data, selected, incidents, spotlight } = props;
  const rail = useRef<HTMLElement>(null);
  const [pitHover, setPitHover] = useState<{ stop: PitStop; left: number; top: number } | null>(null);
  const points = (lap: number): number => incidents.get(lap)?.points ?? 0;
  const groups = useMemo(
    () => railGroups(data, props.runs, (meta) => passesFilters(meta, props.greenOnly, props.cleanOnly, points(meta.lap))),
    [data, props.runs, props.greenOnly, props.cleanOnly, incidents],
  );
  const sectors = useMemo(() => sectorCells(data), [data, data.sectors, data.incidentPoints]);
  const sectorCount = Math.max(1, sectorStarts(data).length);
  const stops = useMemo(() => new Map(props.pitStops.map((stop) => [stop.lap, stop] as const)), [props.pitStops]);
  const zeroMeta = props.zeroLap !== null ? data.lapMeta.get(props.zeroLap) : undefined;
  const ends = lapProgressEnds(data);
  const groupColor = (role: GroupRole): string => runColor(role === "earlier" ? props.comparison?.baseline.runNumber : props.comparison?.compared.runNumber);

  // Arrow keys move between rows; any pointer use hides the focus ring until the keyboard is used again.
  const onKeyDown = (event: KeyboardEvent): void => {
    rail.current?.removeAttribute("data-pointer");
    const target = event.target as HTMLElement;
    if (!target.classList.contains("ri-lap-row")) return;
    const rows = [...(rail.current?.querySelectorAll<HTMLButtonElement>(".ri-lap-row") ?? [])];
    const index = rows.indexOf(target as HTMLButtonElement);
    const next = event.key === "ArrowDown" ? index + 1 : event.key === "ArrowUp" ? index - 1 : event.key === "Home" ? 0 : event.key === "End" ? rows.length - 1 : null;
    if (next === null || index < 0) return;
    event.preventDefault();
    rows[Math.max(0, Math.min(rows.length - 1, next))]?.focus();
  };

  const showPit = (stop: PitStop, element: HTMLElement): void => {
    const box = element.getBoundingClientRect();
    setPitHover({ stop, left: box.right + 10, top: Math.max(8, Math.min(window.innerHeight - 180, box.top + box.height / 2 - 70)) });
  };

  return (
    <aside
      ref={rail} class="ri-card ri-lap-rail" aria-label="Laps" style={`--ri-sector-count:${sectorCount}`}
      onPointerDown={() => rail.current?.setAttribute("data-pointer", "")} onKeyDown={onKeyDown}
    >
      <div class="ri-rail-head">
        <span class="ri-rail-title">Laps</span>
        <span class="ri-count-chip" data-selected-count={selected.size}>{selected.size} selected</span>
        <div class="ri-toggles" role="group" aria-label="Lap filters">
          <button type="button" class="ri-pill-toggle" aria-pressed={props.greenOnly} onClick={() => props.onGreenOnly(!props.greenOnly)}><i class="ri-dot-mark green" aria-hidden="true" />Green</button>
          <button type="button" class="ri-pill-toggle" aria-pressed={props.cleanOnly} title="Complete · green · no pit · no incident · on track" onClick={() => props.onCleanOnly(!props.cleanOnly)}>Clean</button>
        </div>
      </div>
      <div class="ri-lap-scroll" onScroll={() => pitHover && setPitHover(null)}>
        {/* The rail's toolbar: sticky at the top of the scroll parent, exactly --toolbar-height tall; the run rows stick under it. */}
        <div class="ri-rail-bar">
          {ends && (
            <div class="ri-progress-key" data-progress-ends={`${ends.first.lap}-${ends.last.lap}`} title="Lap colour: race progress">
              <span style={`--lap:${ends.first.color}`}><i class="ri-swl" />L{ends.first.lap}</span>
              <i class="ri-progress-bar" aria-hidden="true" />
              <span style={`--lap:${ends.last.color}`}>L{ends.last.lap}<i class="ri-swl" /></span>
            </div>
          )}
          <div class="ri-rail-cols ri-lgrid" aria-hidden="true">
            <span /><span class="c">Lap</span><span /><span class="r">Time</span>
            <span class="r" data-zero-header>{props.zeroLap !== null ? `Δ L${props.zeroLap}` : "Δ"}</span>
            <span class="c">{sectorCount > 1 ? Array.from({ length: sectorCount }, (_, index) => `S${index + 1}`).join(" ") : "Sectors"}</span>
            <span class="r">Fuel</span><span>Inc</span><span>Pit</span>
          </div>
        </div>
        {groups.map((group) => {
          const total = (group.green ?? 0) + (group.caution ?? 0);
          return (
            <section class="ri-lap-group" key={String(group.runNumber)} data-run={group.runNumber ?? ""}>
              <header class="ri-run-head" style={group.runNumber !== null ? `--run:${runColor(group.runNumber)}` : undefined}>
                {group.runNumber !== null && <svg class="ri-run-swatch" width="18" height="8" aria-hidden="true"><line x1="0" y1="4" x2="18" y2="4" stroke={runColor(group.runNumber)} stroke-width="3" stroke-dasharray={runDash(group.runNumber)} /></svg>}
                <b>{group.runNumber !== null ? `Run ${group.runNumber}` : "Laps"}</b>
                <span class="ri-run-range">L{group.firstLap}–{group.lastLap}</span>
                {total > 0 && (
                  <span class="ri-run-comp" aria-hidden="true">
                    <i class="green" style={`width:${((group.green ?? 0) / total) * 100}%`} />
                    <i class="caution" style={`width:${((group.caution ?? 0) / total) * 100}%`} />
                  </span>
                )}
                {group.green !== null && (
                  <span class="ri-run-counts">
                    <i class={`ri-dot-mark green${group.green === 0 ? " zero" : ""}`} aria-hidden="true" /><span title="Green laps">{countText(group.green)}</span>
                    <i class={`ri-dot-mark caution${group.caution === 0 ? " zero" : ""}`} aria-hidden="true" /><span title="Caution laps">{countText(group.caution)}</span>
                  </span>
                )}
              </header>
              {group.laps.map((meta) => (
                <LapRow
                  key={meta.lap} data={data} meta={meta} selected={selected.has(meta.lap)} spotlight={spotlight === meta.lap}
                  zero={zeroMeta} incidents={incidents.get(meta.lap) ?? null} cells={sectors.get(meta.lap) ?? null} sectorCount={sectorCount}
                  stop={stops.get(meta.lap) ?? null} onToggle={props.onToggle}
                  group={props.groups?.get(meta.lap) ?? null} groupColor={groupColor}
                  onPitEnter={showPit} onPitLeave={() => setPitHover(null)}
                />
              ))}
            </section>
          );
        })}
        {groups.length === 0 && <p class="ri-muted ri-pad">No laps</p>}
      </div>
      <div class="ri-rail-foot">
        <button type="button" class="ri-pill-button" onClick={props.onFastest}>Fastest</button>
        <button type="button" class="ri-pill-button" onClick={props.onBestThree}>Best three</button>
        <button type="button" class="ri-pill-button" onClick={props.onClear}>Clear</button>
        <button type="button" class="ri-pill-button" title="Every lap the filters allow" onClick={props.onShowAll}>All</button>
      </div>
      {pitHover && <div class="ri-pit-pop" style={`left:${pitHover.left}px;top:${pitHover.top}px`}><PitCard stop={pitHover.stop} /></div>}
    </aside>
  );
}

interface LapRowProps {
  data: TraceData;
  meta: TraceLapData;
  selected: boolean;
  spotlight: boolean;
  zero: TraceLapData | undefined;
  incidents: LapIncidents | null;
  cells: SectorCell[] | null;
  sectorCount: number;
  stop: PitStop | null;
  onToggle(lap: number): void;
  onPitEnter(stop: PitStop, element: HTMLElement): void;
  onPitLeave(): void;
  group: GroupRole | null;
  groupColor(role: GroupRole): string;
}

/** The lap chip's status: screened out of the pace fit (hollow) or partial (dashed), with the backend's reasons; null when used. */
export function lapChipStatus(meta: TraceLapData): { kind: "partial" | "screened"; text: string } | null {
  const chip = statusChip(meta);
  if (!chip) return null;
  const why = chip.reasons.length ? ` · ${reasonsText(chip.reasons)}` : "";
  const run = typeof meta.run_number === "number" ? `Run ${meta.run_number} pace fit` : "pace fit";
  return chip.kind === "partial" ? { kind: "partial", text: `Partial lap${why}` } : { kind: "screened", text: `Not used in ${run}${why}` };
}

function LapRow({ data, meta, selected, spotlight, zero, incidents, cells, sectorCount, stop, onToggle, onPitEnter, onPitLeave, group, groupColor }: LapRowProps) {
  const points = incidents?.points ?? 0;
  const clean = isCleanLap(meta, points);
  const fastest = data.fastestLap === meta.lap;
  const chip = lapChipStatus(meta);
  const delta = railDelta(meta, zero);
  const flag = railFlag(meta);
  const pitRoad = stop ? null : pitRoadSeconds(meta);
  const conditions = conditionsText(meta);
  const fuel = typeof meta.fuel_used_gal === "number" ? meta.fuel_used_gal.toFixed(3) : "—";
  const severity = points >= 4 ? "p4" : points >= 2 ? "p2" : "p1";
  const label = [`Lap ${meta.lap}`, lapTime(meta.lap_time_s), delta !== "—" && zero ? `Δ ${delta}` : null, chip ? chip.text : null, group ? (group === "earlier" ? "Earlier group" : "Later group") : null, incidents ? `x${points}` : null, stop ? pitLabel(stop) : null]
    .filter(Boolean)
    .join(", ");
  return (
    <button
      type="button" class={`ri-lap-row ri-lgrid${selected ? " selected" : ""}${spotlight ? " spotlight" : ""}${clean ? "" : " unclean"}`}
      style={`--lap:${lapColor(data, meta.lap)}${group ? `;--grp:${groupColor(group)}` : ""}`} aria-pressed={selected} aria-label={label} data-lap={meta.lap} data-group={group ?? undefined}
      onClick={() => onToggle(meta.lap)}
    >
      <span class="ri-sw" aria-hidden="true" />
      <span class={`ri-n${chip ? ` ${chip.kind}` : ""}`} title={chip?.text} data-lap-chip={chip?.kind ?? "used"}>{meta.lap}</span>
      <span class="ri-fl"><Flag flag={flag} /></span>
      <span class={`ri-tm${fastest ? " best" : ""}`} title={conditions || undefined}>{lapTime(meta.lap_time_s)}</span>
      <span class="ri-dl">{delta}</span>
      <span class="ri-secs" aria-hidden="true">
        {Array.from({ length: sectorCount }, (_, index) => {
          const cell = cells?.[index];
          if (!cell) return <i key={index} class="ri-blk na" />;
          const words = [`S${index + 1}`, cell.seconds !== null ? `${cell.seconds.toFixed(3)} s` : "—", SECTOR_TITLE[cell.state]].filter(Boolean).join(" · ");
          return <i key={index} class={`ri-blk ${cell.state}`} title={words} />;
        })}
      </span>
      <span class="ri-fu" title="Fuel used, gal">{fuel}</span>
      <span class="ri-inc">{incidents && <em class={`ri-inc-chip ${severity}`} title={`L${meta.lap} · x${points}`}>x{points}</em>}</span>
      <span class="ri-pt">
        {stop && (
          <span class="ri-pit-cell" data-pit-stop={stop.lap} onPointerEnter={(event) => onPitEnter(stop, event.currentTarget as HTMLElement)} onPointerLeave={onPitLeave}>
            <PitGlyph stop={stop} height={24} />{durationText(stop)}
          </span>
        )}
        {pitRoad !== null && <span class="ri-pit-road-mark" title={`Pit road · ${pitRoad.toFixed(1)} s`}>{pitRoad > 0 ? pitRoad.toFixed(1) : "Pit"}</span>}
      </span>
      {group && <i class={`ri-grp-mark ${group}`} aria-hidden="true" />}
    </button>
  );
}

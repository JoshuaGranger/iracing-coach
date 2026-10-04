import { useMemo, useRef, useState } from "preact/hooks";
import { exposureText, lapsText, lapTime, n, numberText, PlotScale, reasonsText, runColor, runDash, signedText, ticks } from "../format";
import { hasPaceResult, paceTone, type Comparison, type Investigation, type InvestigationLap, type TrendPoint } from "../investigation-model";
import { hypothesisStatus } from "../format";
import { incidentPointsByLap, inRaceStops, pitStops, type PitStop } from "../geometry/rail-model";
import { fastestCleanLap } from "../geometry/trace-model";
import { asArray, asRecord, type RaceViewData, type TraceLapData } from "../types";
import { useElementSize } from "./hooks";
import { PitCard, PitGlyph, pitLabel } from "./PitPictogram";
import { scaleFor, useRemPx } from "./unit";

function paceResultText(c: Comparison): string {
  return hasPaceResult(c) ? `${signedText(c.deltaS)} s median lap` : "No pace result";
}

/** `green` everywhere except Offline Testing, where laps are counted as clean (audit R7). */
export function exposureWord(sessionType: string | null | undefined): string {
  return sessionType && /offline|test/i.test(sessionType) ? "clean" : "green";
}

export function lapLabel(lap: InvestigationLap, word = "green"): string {
  const parts = [`Lap ${lap.lap}`, `${lapTime(lap.lapTimeS)} s`];
  if (lap.position !== null) parts.push(`P${lap.position}`);
  if (lap.runNumber !== null) parts.push(`Run ${lap.runNumber}`);
  if (lap.greenExposure !== null) parts.push(exposureText(lap.greenExposure, word));
  if (!lap.eligible) parts.push(reasonsText(lap.exclusionReasons) || "excluded");
  return parts.join(" · ");
}

export function trendPath(plot: PlotScale, segment: TrendPoint[], xOf: (point: TrendPoint) => number): string {
  return segment
    .map((point, index) => `${index === 0 ? "M" : "L"}${n(plot.x(xOf(point)))},${n(plot.y(Math.min(plot.yMax, Math.max(plot.yMin, point.y))))}`)
    .join(" ");
}

export function ComparisonChips({ cohorts, word }: { cohorts: { cohort: Comparison["baseline"]; role: string }[]; word: string }) {
  return (
    <div class="ri-cohorts">
      {cohorts.map(({ cohort, role }) => (
        <div class="ri-cohort" key={role}>
          <span class="ri-role">
            <svg width="20" height="8" aria-hidden="true"><line x1="0" y1="4" x2="20" y2="4" stroke={runColor(cohort.runNumber)} stroke-width="2.5" stroke-dasharray={runDash(cohort.runNumber)} /></svg>
            {role}
          </span>
          <span>{cohort.runNumber !== null ? `Run ${cohort.runNumber}` : "Laps"}</span>
          <span>L{lapsText(cohort.lapNumbers)}</span>
          <span>{cohort.greenAgeStart !== null && cohort.greenAgeEnd !== null ? `${cohort.greenAgeStart.toFixed(1)}–${cohort.greenAgeEnd.toFixed(1)} ${word}` : "—"}</span>
          <span class="ri-fuel-text">{cohort.fuelStartGal !== null ? `${cohort.fuelStartGal.toFixed(1)} gal` : "— gal"}</span>
          <span>{cohort.trackTempF !== null ? `${cohort.trackTempF.toFixed(0)} °F` : "— °F"}</span>
          <b class="ri-median">{lapTime(cohort.medianLapS)}</b>
          {cohort.excludedLaps.length > 0 && (
            <small class="ri-excluded" title={cohort.excludedLaps.map((lap) => `L${lap.lap} · ${reasonsText(lap.reasons)}`).join("\n")}>
              Excl {cohort.excludedLaps.length} · {reasonsText(cohort.excludedLaps.flatMap((lap) => lap.reasons))}
            </small>
          )}
        </div>
      ))}
    </div>
  );
}

/** Position axis ticks: 1, 2, 5 or 10 apart so at most six labels sit on the band, P1 side at the top (audit #1n). */
export function positionTicks(min: number, max: number): number[] {
  const span = Math.max(1, max - min);
  const step = [1, 2, 5, 10, 20].find((candidate) => span / candidate <= 5) ?? 50;
  const inner: number[] = [];
  // Round multiples between the ends, none closer than half a step to an end so no two labels collide.
  for (let value = Math.ceil(min / step) * step; value <= max; value += step) if (value - min > step / 2 && max - value > step / 2) inner.push(value);
  return [...new Set([min, ...inner, max])];
}

/** Position line segments coloured by what the lap did: gained (green), lost (loss red), held (neutral). */
export function positionSegments(laps: readonly InvestigationLap[]): { from: InvestigationLap; to: InvestigationLap; tone: "gain" | "loss" | "hold" }[] {
  const placed = laps.filter((lap) => lap.position !== null && lap.position > 0);
  const out: { from: InvestigationLap; to: InvestigationLap; tone: "gain" | "loss" | "hold" }[] = [];
  for (let index = 1; index < placed.length; index++) {
    const from = placed[index - 1]!;
    const to = placed[index]!;
    if (to.lap !== from.lap + 1) continue;
    out.push({ from, to, tone: to.position! < from.position! ? "gain" : to.position! > from.position! ? "loss" : "hold" });
  }
  return out;
}

type Hover = { kind: "lap"; lap: InvestigationLap } | { kind: "position"; lap: InvestigationLap } | { kind: "pit"; stop: PitStop; x: number; y: number };

/**
 * The race's fastest lap under the one clean rule Traces uses (complete, green, no pit, no incident, on track; else the fastest
 * complete lap), read from the race view's laps, so Race and Traces mark the same lap. Without the race view, the investigation's
 * laps stand in (flag state and exclusion reasons). Review finding 9.
 */
export function raceFastestLap(laps: readonly InvestigationLap[], race?: RaceViewData | null): number | null {
  const raceLaps = race ? asArray(race.laps).map((lap) => asRecord(lap) as unknown as TraceLapData).filter((lap) => typeof lap.lap === "number") : [];
  const source: TraceLapData[] = raceLaps.length
    ? raceLaps
    : laps.map((lap) => ({
        lap: lap.lap,
        lap_time_s: lap.lapTimeS,
        flag_state: lap.flagState,
        complete: !lap.exclusionReasons.includes("partial"),
        pit_time_s: lap.exclusionReasons.includes("pit") ? 1 : 0,
        comparison_eligible: lap.eligible,
        exclusion_reasons: lap.exclusionReasons,
      }));
  return fastestCleanLap(source, race ? incidentPointsByLap(race) : new Map());
}

function ProgressChart({ inv, selected, stops, knownPitLaps, fastestLap, word }: { inv: Investigation; selected: Comparison | null; stops: readonly PitStop[]; knownPitLaps: ReadonlySet<number>; fastestLap: number | null; word: string }) {
  const host = useRef<HTMLDivElement>(null);
  const { width, height } = useElementSize(host);
  const k = scaleFor(useRemPx());
  const [hover, setHover] = useState<Hover | null>(null);
  const plotted = useMemo(() => inv.laps.filter((lap) => lap.plotPace && lap.lapTimeS !== null && lap.lapTimeS > 0), [inv]);
  const incidents = useMemo(() => inv.timeline.filter((item) => item.kind === "incident"), [inv]);
  const timelinePits = useMemo(() => inv.timeline.filter((item) => item.kind === "pit_service" && !knownPitLaps.has(item.lap)), [inv, knownPitLaps]);
  const chosen = useMemo(() => new Set(selected ? [...selected.baseline.lapNumbers, ...selected.compared.lapNumbers] : []), [selected]);
  const placed = inv.laps.filter((lap) => lap.position !== null && lap.position > 0);
  const hasPositions = placed.length >= 2;
  const body = (() => {
    if (inv.laps.length === 0) return <div class="ri-empty"><strong>No recorded laps</strong></div>;
    if (width < 40 || height < 40) return null;
    const minLap = Math.min(...inv.laps.map((lap) => lap.lap));
    const maxLap = Math.max(...inv.laps.map((lap) => lap.lap));
    const times = plotted.map((lap) => lap.lapTimeS as number);
    const min = times.length ? Math.min(...times) : 0;
    const max = times.length ? Math.max(...times) : 1;
    const pad = Math.max(0.08, (max - min) * 0.06);
    // Margins and label offsets are the default-size geometry times the root font size's factor; the axis text is --text-compact.
    const top = Math.round(34 * k); // room for the pit and incident tags
    const left = Math.round(52 * k);
    const right = Math.round(12 * k);
    const below = Math.round(40 * k); // under the pace plot: caution marks, run brackets and their labels
    const axisBand = Math.round(26 * k);
    const drop = Math.round(5 * k);
    const bandHeight = hasPositions ? Math.min(150 * k, Math.max(60 * k, height * 0.26)) : 0;
    const positionGap = hasPositions ? Math.round(20 * k) : 0;
    const bottomSpace = below + (hasPositions ? positionGap + bandHeight : 0) + axisBand;
    const pace = new PlotScale(left, top, Math.max(60, width - left - right), Math.max(60, height - top - bottomSpace), minLap - 0.5, Math.max(minLap + 1, maxLap) + 0.5, min - pad, max + pad);
    const positions = placed.map((lap) => lap.position as number);
    const posMin = hasPositions ? Math.min(...positions) : 1;
    const posMax = hasPositions ? Math.max(posMin + 1, ...positions) : 2;
    // Position band: P-best at the top.
    const posScale = hasPositions ? new PlotScale(pace.left, pace.bottom + below + positionGap, pace.width, bandHeight, pace.xMin, pace.xMax, -posMax, -posMin) : null;
    const py = (position: number): number => posScale!.y(-position);
    const baseline = height - Math.round(5 * k);
    const lapWidth = pace.width / Math.max(1, pace.xMax - pace.xMin);
    const cautionLaps = inv.laps.filter((lap) => lap.flagState === "caution");
    const bandBottom = posScale ? posScale.bottom : pace.bottom;
    return (
      <svg class="ri-plot-svg" viewBox={`0 0 ${n(width)} ${n(height)}`} role="group" aria-label="Race progression: lap time, run trend, cautions, pit stops, incidents and position by lap" onPointerLeave={() => setHover(null)}>
        {cautionLaps.map((lap) => (
          <g key={`c${lap.lap}`} data-caution-lap={lap.lap}>
            <rect x={n(pace.x(lap.lap) - lapWidth / 2)} y={n(pace.top)} width={n(Math.max(1.5, lapWidth))} height={n(bandBottom - pace.top)} class="ri-caution-shade" />
            <rect x={n(pace.x(lap.lap) - lapWidth / 2 + 0.5)} y={n(pace.bottom + 3)} width={n(Math.max(1.5, lapWidth - 1))} height={n(6 * k)} rx="2" class="ri-caution"><title>{`L${lap.lap} · caution`}</title></rect>
          </g>
        ))}
        {ticks(pace.yMin, pace.yMax, 4).map((tick) => (
          <g key={tick}>
            <line x1={n(pace.left)} x2={n(pace.right)} y1={n(pace.y(tick))} y2={n(pace.y(tick))} class="ri-grid" />
            <text x={n(pace.left - 6)} y={n(pace.y(tick) + drop)} text-anchor="end" class="ri-axis">{tick.toFixed(1)}</text>
          </g>
        ))}
        <text x={n(pace.left)} y={n(Math.round(15 * k))} class="ri-axis-title">Lap time · s</text>
        {stops.map((stop) => {
          const x = pace.x(stop.lap);
          return (
            <g key={`p${stop.lap}`} class="ri-pit-marker" data-pit-marker={stop.lap} tabIndex={0} role="img" aria-label={pitLabel(stop)}
              onPointerEnter={() => setHover({ kind: "pit", stop, x, y: pace.top })} onFocus={() => setHover({ kind: "pit", stop, x, y: pace.top })}>
              <line x1={n(x)} x2={n(x)} y1={n(pace.top - 4)} y2={n(bandBottom)} class="ri-event service" />
              <rect x={n(x - 15 * k)} y={n(pace.top - 30 * k)} width={n(30 * k)} height={n(24 * k)} rx={n(12 * k)} class="ri-pit-tag" />
              <g transform={`translate(${n(x - 5 * k)} ${n(pace.top - 27 * k)})`}><PitGlyph stop={null} height={18 * k} gauge={false} /></g>
            </g>
          );
        })}
        {timelinePits.map((item) => {
          const x = pace.x(item.lap);
          return <line key={`t${item.lap}`} x1={n(x)} x2={n(x)} y1={n(pace.top)} y2={n(bandBottom)} class="ri-event service"><title>{`Pit L${item.lap}`}</title></line>;
        })}
        {incidents.map((item) => {
          const x = pace.x(item.lap);
          const points = item.value ?? 0;
          return (
            <g key={`i${item.lap}`} data-incident-lap={item.lap}>
              <line x1={n(x)} x2={n(x)} y1={n(pace.top - 4)} y2={n(bandBottom)} class="ri-event incident" />
              <rect x={n(x - 14 * k)} y={n(pace.top - 28 * k)} width={n(28 * k)} height={n(21 * k)} rx={n(10.5 * k)} class={`ri-inc-tag${points >= 2 ? " solid" : ""}`} />
              <text x={n(x)} y={n(pace.top - 13 * k)} text-anchor="middle" class={`ri-inc-tag-text${points >= 2 ? " solid" : ""}`}>{`x${points.toFixed(0)}`}<title>{`L${item.lap} · x${points.toFixed(0)}`}</title></text>
            </g>
          );
        })}
        {selected && [selected.baseline, selected.compared].map((cohort) => {
          if (cohort.lapNumbers.length === 0) return null;
          const start = pace.x(Math.min(...cohort.lapNumbers)) - 4;
          const end = pace.x(Math.max(...cohort.lapNumbers)) + 4;
          return (
            <g key={cohort.label + cohort.lapNumbers[0]}>
              <path d={`M${n(start)},${n(pace.bottom + 14 * k)}V${n(pace.bottom + 18 * k)}H${n(end)}V${n(pace.bottom + 14 * k)}`} class="ri-bracket" stroke={runColor(cohort.runNumber)} stroke-dasharray={runDash(cohort.runNumber)} />
              <text x={n((start + end) / 2)} y={n(pace.bottom + 35 * k)} text-anchor="middle" class="ri-bracket-label">L{lapsText(cohort.lapNumbers)}</text>
            </g>
          );
        })}
        {inv.runs.map((run) => run.trend.segments.map((segment, index) => (
          <path key={`t${run.runNumber}-${index}`} d={trendPath(pace, segment, (point) => point.lap)} class="ri-trend" stroke={runColor(run.runNumber)} stroke-dasharray={runDash(run.runNumber)} />
        )))}
        {plotted.map((lap) => {
          const isFastest = fastestLap === lap.lap;
          const color = isFastest ? "var(--ri-best)" : runColor(lap.runNumber);
          const isChosen = chosen.has(lap.lap);
          const x = pace.x(lap.lap);
          const y = pace.y(Math.min(pace.yMax, Math.max(pace.yMin, lap.lapTimeS as number)));
          return (
            <g key={lap.lap}>
              {isChosen && <circle cx={n(x)} cy={n(y)} r="6.5" class="ri-ring" stroke={color} />}
              <circle
                cx={n(x)} cy={n(y)} r={isChosen || isFastest ? "3.8" : "2.8"}
                fill={lap.eligible ? color : "var(--ri-card)"} stroke={color}
                class={`ri-dot${lap.eligible ? "" : " excluded"}${isFastest ? " fastest" : ""}`} tabIndex={0} data-lap={lap.lap}
                onPointerEnter={() => setHover({ kind: "lap", lap })} onFocus={() => setHover({ kind: "lap", lap })} aria-label={lapLabel(lap, word)}
              ><title>{lapLabel(lap, word)}</title></circle>
            </g>
          );
        })}
        {posScale && (
          <g class="ri-position-band" data-position-band>
            <text x={n(posScale.left)} y={n(posScale.top - Math.round(8 * k))} class="ri-axis-title">Position</text>
            {positionTicks(posMin, posMax).map((position) => (
              <g key={`pt${position}`}>
                <line x1={n(posScale.left)} x2={n(posScale.right)} y1={n(py(position))} y2={n(py(position))} class="ri-grid soft" />
                <text x={n(posScale.left - 6)} y={n(py(position) + drop)} text-anchor="end" class="ri-axis" data-position-tick={position}>{`P${position}`}</text>
              </g>
            ))}
            {positionSegments(inv.laps).map((segment) => (
              <line key={`s${segment.to.lap}`} x1={n(posScale.x(segment.from.lap))} y1={n(py(segment.from.position!))} x2={n(posScale.x(segment.to.lap))} y2={n(py(segment.to.position!))} class={`ri-position-seg ${segment.tone}`} data-tone={segment.tone} />
            ))}
            {placed.map((lap) => (
              <circle key={`pd${lap.lap}`} cx={n(posScale.x(lap.lap))} cy={n(py(lap.position!))} r={n(Math.max(2.2, Math.min(3.4, lapWidth * 0.32)))} class={`ri-pos-dot${hover?.kind === "position" && hover.lap.lap === lap.lap ? " on" : ""}`}
                data-position-lap={lap.lap} tabIndex={-1} onPointerEnter={() => setHover({ kind: "position", lap })}>
                <title>{`Lap ${lap.lap} · P${lap.position}`}</title>
              </circle>
            ))}
            {/* Wide invisible hit columns so a lap's position reads without hitting a 3 px dot. */}
            {placed.map((lap) => (
              <rect key={`ph${lap.lap}`} x={n(posScale.x(lap.lap) - lapWidth / 2)} y={n(posScale.top - 6)} width={n(Math.max(2, lapWidth))} height={n(posScale.height + 12)} class="ri-hit" onPointerEnter={() => setHover({ kind: "position", lap })} />
            ))}
          </g>
        )}
        {ticks(minLap, maxLap, 8).map((tick) => (
          <text key={`x${tick}`} x={n(pace.x(Math.round(tick)))} y={n(baseline)} text-anchor="middle" class="ri-axis">{Math.round(tick)}</text>
        ))}
        <text x="4" y={n(baseline)} class="ri-axis-title">Lap</text>
      </svg>
    );
  })();
  const readout = hover?.kind === "position"
    ? `Lap ${hover.lap.lap} · P${hover.lap.position}`
    : hover?.kind === "lap"
      ? lapLabel(hover.lap, word)
      : hover?.kind === "pit"
        ? pitLabel(hover.stop)
        : selected ? `${selected.title} · ${paceResultText(selected)}` : "";
  return (
    <div class="ri-progress" data-ri-progress>
      <div class="ri-chart-head">
        <span class="ri-card-title">Race progression</span>
        <div class="ri-legend">
          <span><i class="ld" />Lap</span><span><i class="ld hollow" />Excl</span><span><i class="ld best" />Fastest</span>
          <span><i class="lb" />Caution</span><span class="pit-word">Pit</span><span><em class="ri-inc-chip p2 small">x2</em>Incident</span>
          {inv.runs.slice(0, 3).map((run) => <span key={run.runNumber}><svg width="20" height="8" aria-hidden="true"><line x1="0" y1="4" x2="20" y2="4" stroke={runColor(run.runNumber)} stroke-width="2.5" stroke-dasharray={runDash(run.runNumber)} /></svg>Run {run.runNumber}</span>)}
          {hasPositions && <span><i class="lg gain" /><i class="lg loss" />Position</span>}
        </div>
        <output class="ri-readout" aria-live="polite" data-race-readout>{readout}</output>
      </div>
      <div class="ri-plot" ref={host}>
        {body}
        {hover?.kind === "pit" && (
          <div class="ri-pit-pop chart" style={`left:${n(Math.min(Math.max(0, width - 250), hover.x + 18))}px;top:${n(hover.y)}px`}><PitCard stop={hover.stop} compact /></div>
        )}
      </div>
    </div>
  );
}

const CHANGE_THRESHOLDS: Record<string, number> = { brake: 1, throttle: 2, steering: 2, corrections: 1, fuel: 0.5, track_temperature: 1, track_usage: 0.1, brake_bias: 0.1, traffic: 1 };
/** Display decimals per change unit (RaceInvestigationView.Digits, audit R3). */
const CHANGE_DIGITS: Record<string, number> = { "%": 0, "°F": 0, "° F": 0, gal: 1, deg: 2, "°": 2, count: 0, "": 1 };

export function changeDigits(metric: { key: string; unit: string }, roadWheel: boolean): number {
  if (metric.key === "steering") return roadWheel ? 2 : 1;
  if (metric.key === "brake_bias") return 1;
  return CHANGE_DIGITS[metric.unit.trim()] ?? 1;
}

/** The backend's steering threshold reference: wheel degrees over `threshold_reference_ratio` (12 by default), audit R1. */
export function steeringLimit(threshold: number, basis: Investigation["steeringBasis"]): number {
  const record = (basis ?? {}) as Record<string, unknown>;
  if (record.basis !== "road_wheel") return threshold;
  const reference = typeof record.threshold_reference_ratio === "number" && record.threshold_reference_ratio > 0 ? record.threshold_reference_ratio : 12;
  return threshold / reference;
}

export function changes(c: Comparison, basis: Investigation["steeringBasis"]): Comparison["metrics"] {
  return c.metrics
    .filter((metric) => {
      const threshold = CHANGE_THRESHOLDS[metric.key];
      if (metric.delta === null || threshold === undefined) return false;
      const limit = metric.key === "steering" ? steeringLimit(threshold, basis) : threshold;
      return Math.abs(metric.delta) >= limit;
    })
    .slice(0, 5);
}

export interface RaceViewProps {
  inv: Investigation;
  selected: Comparison | null;
  race?: RaceViewData;
  runs?: readonly unknown[];
  sessionType?: string | null;
  onSelectComparison(id: string): void;
  onInspect(action: Comparison["actions"][number]): void;
}

export function RaceView({ inv, selected, race, runs, sessionType, onSelectComparison, onInspect }: RaceViewProps) {
  const lead = selected?.actions.slice().sort((a, b) => a.priority - b.priority)[0] ?? null;
  const changed = selected ? changes(selected, inv.steeringBasis) : [];
  const roadWheel = (inv.steeringBasis as Record<string, unknown> | null)?.basis === "road_wheel";
  const allStops = useMemo(() => (race ? pitStops(race, { runs: runs ?? [] }, null) : []), [race, runs]);
  const stops = useMemo(() => inRaceStops(allStops), [allStops]);
  const knownPitLaps = useMemo(() => new Set(allStops.map((stop) => stop.lap)), [allStops]);
  const fastestLap = useMemo(() => raceFastestLap(inv.laps, race), [inv, race]);
  const word = exposureWord(sessionType);
  return (
    <div class="ri-race-view" data-ri-view="race">
      <div class="ri-card ri-fill"><ProgressChart inv={inv} selected={selected} stops={stops} knownPitLaps={knownPitLaps} fastestLap={fastestLap} word={word} /></div>
      <div class="ri-race-bottom">
        <nav class="ri-card ri-comparisons" aria-label="Pace comparisons">
          <div class="ri-card-label"><span class="ri-card-title">Comparisons</span><span>Later − earlier · median</span></div>
          {inv.comparisons.map((choice) => {
            const isSelected = choice.id === selected?.id;
            const excluded = [...choice.baseline.excludedLaps, ...choice.compared.excludedLaps];
            return (
              <button
                type="button" key={choice.id} class={`ri-comparison${isSelected ? " selected" : ""}`} aria-pressed={isSelected}
                data-comparison={choice.id} title={choice.paceComparable ? undefined : choice.summary} onClick={() => onSelectComparison(choice.id)}
              >
                <span class="ri-comparison-title">{choice.title}</span>
                <span class="ri-comparison-laps">
                  L{lapsText(choice.baseline.lapNumbers)} · L{lapsText(choice.compared.lapNumbers)}
                  {!choice.paceComparable && <em> · no pace result</em>}
                  {choice.paceComparable && choice.eligibility !== "eligible" && excluded.length > 0 && (
                    <em title={excluded.map((lap) => `L${lap.lap} · ${reasonsText(lap.reasons)}`).join("\n")}> · Excl {excluded.length}/{choice.baseline.lapCount + choice.compared.lapCount} · {reasonsText(excluded.flatMap((lap) => lap.reasons))}</em>
                  )}
                </span>
                <strong class={paceTone(choice)}>{hasPaceResult(choice) ? signedText(choice.deltaS) : "—"}</strong>
              </button>
            );
          })}
          {inv.comparisons.length === 0 && <p class="ri-muted ri-pad">No comparisons</p>}
        </nav>
        {selected && (
          <section class="ri-card ri-detail" data-selected-comparison={selected.id}>
            <header><h2>{selected.title}</h2><strong class={paceTone(selected)}>{hasPaceResult(selected) ? signedText(selected.deltaS) : "—"}<small>{hasPaceResult(selected) ? "s median lap" : "no pace result"}</small></strong></header>
            {!selected.paceComparable && <p class="ri-unusable" data-pace-unusable>{selected.paceUnusableReasons.length ? reasonsText(selected.paceUnusableReasons) : selected.summary}</p>}
            <ComparisonChips cohorts={[{ cohort: selected.baseline, role: "Earlier" }, { cohort: selected.compared, role: "Later" }]} word={word} />
            {changed.length > 0 && (
              <div class="ri-changes">{changed.map((metric) => {
                const digits = changeDigits(metric, roadWheel);
                return <span key={metric.key} title={metric.detail ?? undefined}>{metric.label} <b>{numberText(metric.baseline, digits)} → {numberText(metric.compared, digits)}{metric.unit ? ` ${metric.unit}` : ""}</b></span>;
              })}</div>
            )}
            {selected.paceComparable && selected.hypotheses.length > 0 && (
              <div class="ri-explanations">
                {selected.hypotheses.map((h) => (
                  <span key={h.key} class={`ri-hypothesis ${h.status}`} title={[...h.supporting.map((t) => `+ ${t}`), ...h.contradicting.map((t) => `− ${t}`), ...h.unknown.map((t) => `? ${t}`)].join("\n")}>
                    {h.label}<small>{hypothesisStatus(h.status)}</small>
                    {(h.supporting.length > 0 || h.contradicting.length > 0) && <small class="ri-evidence">+{h.supporting.length} −{h.contradicting.length}</small>}
                  </span>
                ))}
              </div>
            )}
            {inv.trackEvolution?.summary && inv.trackEvolution.status !== "unavailable" && <p class="ri-muted ri-evolution" data-track-evolution>{inv.trackEvolution.summary}</p>}
            {lead && (
              <div class="ri-action">
                <div><strong title={lead.instruction || undefined}>{lead.title}</strong></div>
                <button type="button" class="ri-pill-button primary" onClick={() => onInspect(lead)}>{lead.sectionId ? "Inspect section" : "Open in Traces"}</button>
              </div>
            )}
          </section>
        )}
      </div>
    </div>
  );
}

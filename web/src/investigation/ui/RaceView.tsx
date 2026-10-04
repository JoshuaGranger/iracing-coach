import { useMemo, useRef, useState } from "preact/hooks";
import { exposureText, lapTime, n, PlotScale, runColor, runDash, ticks } from "../format";
import { paceTone, type Comparison, type Investigation, type InvestigationLap, type TrendPoint } from "../investigation-model";
import { incidentPointsByLap, inRaceStops, pitStops, type PitStop } from "../geometry/rail-model";
import { fastestCleanLap } from "../geometry/trace-model";
import { asArray, asRecord, type RaceViewData, type TraceLapData } from "../types";
import { brackets, comparisonLaps, comparisonName, deltaText, noResultLine, placeLabels, screenedText } from "./comparison-model";
import { ComparisonPanel } from "./ComparisonPicture";
import { useElementSize } from "./hooks";
import { PitCard, PitGlyph, pitLabel } from "./PitPictogram";
import { scaleFor, useRemPx } from "./unit";

/** `green` everywhere except Offline Testing, where laps are counted as clean (audit R7). */
export function exposureWord(sessionType: string | null | undefined): string {
  return sessionType && /offline|test/i.test(sessionType) ? "clean" : "green";
}

/** A lap's readout: `Lap 36 · 28.559 s · P7 · 36.0 green · Not used in Run 1 pace fit · close traffic`. */
export function lapLabel(lap: InvestigationLap, word = "green"): string {
  const parts = [`Lap ${lap.lap}`, `${lapTime(lap.lapTimeS)} s`];
  if (lap.position !== null) parts.push(`P${lap.position}`);
  if (lap.flagState === "caution") parts.push("Caution");
  const screened = screenedText(lap);
  if (lap.runNumber !== null && !screened) parts.push(`Run ${lap.runNumber}`);
  if (lap.greenExposure !== null) parts.push(exposureText(lap.greenExposure, word));
  if (screened && lap.flagState !== "caution") parts.push(screened);
  return parts.join(" · ");
}

export function trendPath(plot: PlotScale, segment: TrendPoint[], xOf: (point: TrendPoint) => number): string {
  return segment
    .map((point, index) => `${index === 0 ? "M" : "L"}${n(plot.x(xOf(point)))},${n(plot.y(Math.min(plot.yMax, Math.max(plot.yMin, point.y))))}`)
    .join(" ");
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

/** Laps drawn as pace dots (green, timed, the backend's `plot_pace`) and as subdued dots in the caution band (actual times). */
export function chartLaps(laps: readonly InvestigationLap[]): { pace: InvestigationLap[]; caution: InvestigationLap[] } {
  const timed = (lap: InvestigationLap): boolean => lap.lapTimeS !== null && lap.lapTimeS > 0;
  return {
    pace: laps.filter((lap) => lap.plotPace && timed(lap) && lap.flagState !== "caution"),
    caution: laps.filter((lap) => lap.flagState === "caution" && timed(lap)),
  };
}

/**
 * The pace plot's top margin: room for the pit and incident tags (30 px at the default size), and a further line when a tag would
 * sit under the `Lap time · s` title at the plot's top left (a narrow chart with an early stop or incident), so the title clears it.
 */
export function paceTopMargin(tagXs: readonly number[], left: number, k: number): number {
  const titleRight = left + 12 * 7.3 * k + 6 * k;
  const crowded = tagXs.some((x) => x - 14 * k < titleRight);
  return Math.round((crowded ? 48 : 30) * k);
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
  const { pace: plotted, caution: cautionDots } = useMemo(() => chartLaps(inv.laps), [inv]);
  const incidents = useMemo(() => inv.timeline.filter((item) => item.kind === "incident"), [inv]);
  const timelinePits = useMemo(() => inv.timeline.filter((item) => item.kind === "pit_service" && !knownPitLaps.has(item.lap)), [inv, knownPitLaps]);
  const groupBrackets = useMemo(() => brackets(selected), [selected]);
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
    const left = Math.round(52 * k);
    const right = Math.round(12 * k);
    // Under the pace plot: the comparison's brackets and their labels, on a second line when a narrow chart would overlap them.
    const charPx = 7.3 * k;
    const labelWidths = groupBrackets.map((bracket) => bracket.label.length * charPx);
    const draft = new PlotScale(left, 0, Math.max(60, width - left - right), 1, minLap - 0.5, Math.max(minLap + 1, maxLap) + 0.5, 0, 1);
    const top = paceTopMargin([...stops.map((stop) => draft.x(stop.lap)), ...incidents.map((item) => draft.x(item.lap))], left, k);
    const labelPlaces = placeLabels(groupBrackets.map((bracket) => (draft.x(bracket.pieces[0]![0]) + draft.x(bracket.pieces[bracket.pieces.length - 1]![1])) / 2), labelWidths, draft.left, draft.right);
    const labelLines = labelPlaces.some((place) => place.line === 1) ? 2 : 1;
    const below = Math.round(groupBrackets.length ? (labelLines === 2 ? 56 : 40) * k : 12 * k);
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
    const clampY = (value: number): number => pace.y(Math.min(pace.yMax, Math.max(pace.yMin, value)));
    const dotRadius = Math.max(2.4, Math.min(3.4, lapWidth * 0.34));
    return (
      <svg class="ri-plot-svg" viewBox={`0 0 ${n(width)} ${n(height)}`} role="group" aria-label="Race progression: lap time, run trend, cautions, pit stops, incidents and position by lap" onPointerLeave={(event: PointerEvent) => { if (event.pointerType !== "touch") setHover(null); }}>
        {cautionLaps.map((lap) => (
          <rect key={`c${lap.lap}`} data-caution-lap={lap.lap} x={n(pace.x(lap.lap) - lapWidth / 2)} y={n(pace.top)} width={n(Math.max(1.5, lapWidth))} height={n(bandBottom - pace.top)} class="ri-caution-shade" />
        ))}
        {ticks(pace.yMin, pace.yMax, 4).map((tick, index) => (
          <g key={`y${index}`}>
            <line x1={n(pace.left)} x2={n(pace.right)} y1={n(pace.y(tick))} y2={n(pace.y(tick))} class="ri-grid" />
            <text x={n(pace.left - 6)} y={n(pace.y(tick) + drop)} text-anchor="end" class="ri-axis">{tick.toFixed(1)}</text>
          </g>
        ))}
        <text x={n(pace.left)} y={n(Math.round(13 * k))} class="ri-axis-title">Lap time · s</text>
        {stops.map((stop) => {
          const x = pace.x(stop.lap);
          return (
            <g key={`p${stop.lap}`} class="ri-pit-marker" data-pit-marker={stop.lap} tabIndex={0} role="img" aria-label={pitLabel(stop)}
              onPointerEnter={() => setHover({ kind: "pit", stop, x, y: pace.top })} onFocus={() => setHover({ kind: "pit", stop, x, y: pace.top })} onClick={() => setHover({ kind: "pit", stop, x, y: pace.top })}>
              <line x1={n(x)} x2={n(x)} y1={n(pace.top - 4)} y2={n(bandBottom)} class="ri-event service" />
              <rect x={n(x - 11 * k)} y={n(pace.top - 25 * k)} width={n(22 * k)} height={n(20 * k)} rx={n(10 * k)} class="ri-pit-tag" />
              <g transform={`translate(${n(x - 4 * k)} ${n(pace.top - 22.5 * k)})`}><PitGlyph stop={null} height={15 * k} gauge={false} /></g>
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
              <rect x={n(x - 14 * k)} y={n(pace.top - 26 * k)} width={n(28 * k)} height={n(21 * k)} rx={n(10.5 * k)} class={`ri-inc-tag${points >= 2 ? " solid" : ""}`} />
              <text x={n(x)} y={n(pace.top - 11 * k)} text-anchor="middle" class={`ri-inc-tag-text${points >= 2 ? " solid" : ""}`}>{`x${points.toFixed(0)}`}<title>{`L${item.lap} · x${points.toFixed(0)}`}</title></text>
            </g>
          );
        })}
        {/* The selected comparison's two groups: labelled brackets under the laps, split around omitted laps. */}
        {groupBrackets.map((bracket, index) => {
          const y0 = pace.bottom + 6 * k;
          const y1 = pace.bottom + 11 * k;
          const place = labelPlaces[index]!;
          return (
            <g key={bracket.role} class="ri-bracket-group" data-bracket={bracket.role} data-laps={bracket.pieces.map(([a, b]) => `${a}-${b}`).join(",")} style={`--grp:${runColor(bracket.run)}`}>
              {bracket.pieces.map(([a, b]) => {
                const start = pace.x(a) - Math.min(4, lapWidth * 0.45);
                const end = pace.x(b) + Math.min(4, lapWidth * 0.45);
                return <path key={a} d={`M${n(start)},${n(y0)}V${n(y1)}H${n(end)}V${n(y0)}`} class="ri-bracket" data-piece={`${a}-${b}`} />;
              })}
              <text x={n(place.x)} y={n(pace.bottom + (30 + place.line * 16) * k)} text-anchor="middle" class="ri-bracket-label">{bracket.label}</text>
            </g>
          );
        })}
        {inv.runs.map((run) => run.trend.segments.map((segment, index) => (
          <path key={`t${run.runNumber}-${index}`} d={trendPath(pace, segment, (point) => point.lap)} class="ri-trend" stroke={runColor(run.runNumber)} stroke-dasharray={runDash(run.runNumber)} data-trend-run={run.runNumber} />
        )))}
        {cautionDots.map((lap) => {
          const off = (lap.lapTimeS as number) > pace.yMax;
          return (
            <circle key={`cd${lap.lap}`} cx={n(pace.x(lap.lap))} cy={n(clampY(lap.lapTimeS as number))} r={n(dotRadius * 0.85)} class={`ri-dot caution${off ? " off" : ""}`} tabIndex={0} data-lap={lap.lap} data-caution-dot={lap.lap}
              onPointerEnter={() => setHover({ kind: "lap", lap })} onFocus={() => setHover({ kind: "lap", lap })} onClick={() => setHover({ kind: "lap", lap })} aria-label={lapLabel(lap, word)}>
              <title>{lapLabel(lap, word)}</title>
            </circle>
          );
        })}
        {plotted.map((lap) => {
          const isFastest = fastestLap === lap.lap;
          const color = isFastest ? "var(--ri-best)" : runColor(lap.runNumber);
          return (
            <circle
              key={lap.lap} cx={n(pace.x(lap.lap))} cy={n(clampY(lap.lapTimeS as number))} r={n(isFastest ? dotRadius + 1 : dotRadius)}
              fill={lap.eligible ? color : "var(--ri-card)"} stroke={color}
              class={`ri-dot${lap.eligible ? "" : " excluded"}${isFastest ? " fastest" : ""}`} tabIndex={0} data-lap={lap.lap}
              onPointerEnter={() => setHover({ kind: "lap", lap })} onFocus={() => setHover({ kind: "lap", lap })} onClick={() => setHover({ kind: "lap", lap })} aria-label={lapLabel(lap, word)}
            ><title>{lapLabel(lap, word)}</title></circle>
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
        {ticks(minLap, maxLap, 8).map((tick, index) => (
          <text key={`x${index}`} x={n(pace.x(Math.round(tick)))} y={n(baseline)} text-anchor="middle" class="ri-axis">{Math.round(tick)}</text>
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
        : selected ? `${comparisonName(selected)} · ${deltaText(selected) === "—" ? "No pace result" : `${deltaText(selected)} s`}` : "";
  return (
    <div class="ri-progress" data-ri-progress>
      <div class="ri-chart-head">
        <span class="ri-card-title">Race progression</span>
        <div class="ri-legend">
          <span><i class="ld" />Lap</span><span data-legend="screened"><i class="ld hollow" />Not used in pace fit</span><span><i class="ld best" />Fastest</span>
          <span><i class="lb" />Caution</span><span class="pit-word">Pit</span><span><em class="ri-inc-chip p2 small">x2</em>Incident</span>
          {inv.runs.slice(0, 8).map((run) => <span key={run.runNumber}><svg width="20" height="8" aria-hidden="true"><line x1="0" y1="4" x2="20" y2="4" stroke={runColor(run.runNumber)} stroke-width="2.5" stroke-dasharray={runDash(run.runNumber)} /></svg>Run {run.runNumber}</span>)}
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

/** The Comparisons list: the one comparison selector, rows named by their groups, the backend's signed difference at the right. */
export function ComparisonList({ inv, selected, onSelect }: { inv: Investigation; selected: Comparison | null; onSelect(id: string): void }) {
  return (
    <nav class="ri-card ri-comparisons" aria-label="Comparisons">
      <div class="ri-card-label"><span class="ri-card-title">Comparisons</span><span>Later − earlier · s</span></div>
      {inv.comparisons.map((choice) => {
        const isSelected = choice.id === selected?.id;
        const laps = comparisonLaps(choice);
        return (
          <button type="button" key={choice.id} class={`ri-comparison${isSelected ? " selected" : ""}`} aria-pressed={isSelected} data-comparison={choice.id}
            title={deltaText(choice) === "—" ? noResultLine(choice) : undefined} onClick={() => onSelect(choice.id)}>
            <span class="ri-comparison-title">{comparisonName(choice)}</span>
            {laps && <span class="ri-comparison-laps">{laps}</span>}
            <strong class={paceTone(choice)}>{deltaText(choice)}</strong>
          </button>
        );
      })}
      {inv.comparisons.length === 0 && <p class="ri-muted ri-pad">No comparisons</p>}
    </nav>
  );
}

export function RaceView({ inv, selected, race, runs, sessionType, onSelectComparison, onInspect }: RaceViewProps) {
  const lead = selected?.actions.slice().sort((a, b) => a.priority - b.priority)[0] ?? null;
  const allStops = useMemo(() => (race ? pitStops(race, { runs: runs ?? [] }, null) : []), [race, runs]);
  const stops = useMemo(() => inRaceStops(allStops), [allStops]);
  const knownPitLaps = useMemo(() => new Set(allStops.map((stop) => stop.lap)), [allStops]);
  const fastestLap = useMemo(() => raceFastestLap(inv.laps, race), [inv, race]);
  const word = exposureWord(sessionType);
  return (
    <div class="ri-race-view" data-ri-view="race">
      <div class="ri-card ri-fill"><ProgressChart inv={inv} selected={selected} stops={stops} knownPitLaps={knownPitLaps} fastestLap={fastestLap} word={word} /></div>
      <div class="ri-race-bottom">
        <ComparisonList inv={inv} selected={selected} onSelect={onSelectComparison} />
        {selected && (
          <ComparisonPanel inv={inv} comparison={selected}
            action={lead ? { label: lead.sectionId ? `Inspect section ${lead.sectionId}` : "Open in Traces", run: () => onInspect(lead) } : null} />
        )}
      </div>
    </div>
  );
}

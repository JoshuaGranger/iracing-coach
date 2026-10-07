import { Fragment, type ComponentChildren } from "preact";
import { useMemo, useRef, useState } from "preact/hooks";
import { exposureText, lapTime, n, PlotScale, reasonsText, ticks } from "../format";
import type { Comparison, Investigation, InvestigationLap } from "../investigation-model";
import { incidentPointsByLap, inRaceStops, pitStops, type PitStop } from "../geometry/rail-model";
import { fastestCleanLap } from "../geometry/trace-model";
import { asArray, asRecord, type RaceViewData, type TraceLapData } from "../types";
import { useElementSize } from "./hooks";
import { LapDot } from "./LapDot";
import { PitCard, PitGlyph, pitLabel } from "./PitPictogram";
import { availableLayers, fuelPath, lapContexts, layersReadout, readLayers, refills, seriesSegments, writeLayers, type LapContext, type LayerKey } from "./race-layers";
import { identityColour, observedTrend, runIdentities, runLineParts, runLineReadout, trendAt, trendHover, trendText, type ObservedTrend, type RunIdentity } from "./runs-model";
import { lapKind, lapTraffic, trafficText, type LapTraffic } from "./traffic";
import { scaleFor, useRemPx } from "./unit";

/** `green` everywhere except Offline Testing, where laps are counted as clean (audit R7). */
export function exposureWord(sessionType: string | null | undefined): string {
  return sessionType && /offline|test/i.test(sessionType) ? "clean" : "green";
}

/** Why a lap is not in its run's pace fit, from the backend's reasons: `Not in pace fit · close traffic`. Null for a lap the fit used. */
export function fitStatus(lap: Pick<InvestigationLap, "eligible" | "exclusionReasons">): string | null {
  if (lap.eligible) return null;
  const why = reasonsText(lap.exclusionReasons.filter((code) => code !== "partial"));
  return `Not in pace fit${why ? ` · ${why}` : ""}`;
}

/**
 * A lap's readout: `Lap 36 · 28.559 s · P7 · Run 2 · 36.0 green · Not in pace fit · caution`. A lap screened for close traffic alone
 * ends with its traffic instead (#67): `Lap 20 · 29.466 s · P4 · Run 1 · 20.0 green · traffic 75 % of lap`.
 */
export function lapLabel(lap: InvestigationLap, word = "green", traffic?: LapTraffic | null): string {
  const parts = [`Lap ${lap.lap}`, `${lapTime(lap.lapTimeS)} s`];
  if (lap.position !== null) parts.push(`P${lap.position}`);
  if (lap.flagState === "caution") parts.push("Caution");
  if (lap.runNumber !== null) parts.push(`Run ${lap.runNumber}`);
  if (lap.greenExposure !== null) parts.push(exposureText(lap.greenExposure, word));
  if (lapKind(lap) === "traffic") {
    parts.push(trafficText(traffic));
  } else {
    const status = lap.flagState === "caution" ? null : fitStatus(lap);
    if (status) parts.push(status);
  }
  return parts.join(" · ");
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
export function positionSegments<T extends { lap: number; position: number | null }>(laps: readonly T[]): { from: T; to: T; tone: "gain" | "loss" | "hold" }[] {
  const placed = laps.filter((lap) => lap.position !== null && lap.position > 0);
  const out: { from: T; to: T; tone: "gain" | "loss" | "hold" }[] = [];
  for (let index = 1; index < placed.length; index++) {
    const from = placed[index - 1]!;
    const to = placed[index]!;
    if (to.lap !== from.lap + 1) continue;
    out.push({ from, to, tone: to.position! < from.position! ? "gain" : to.position! > from.position! ? "loss" : "hold" });
  }
  return out;
}

/** One point of the position line: a lap's recorded place, or the start at lap 0 (backlog #96). */
export interface PositionPoint {
  lap: number;
  position: number;
  start: boolean;
}

/**
 * The position line: the start at lap 0, then every recorded lap's place from lap 1, so a car that lost places on the opening lap
 * starts the line where it lined up, joined to lap 1 by that lap's loss (#96: "Lap 0 should show my starting position"). Without
 * a start, the recorded laps alone.
 */
export function positionTrack(laps: readonly InvestigationLap[], start: number | null): PositionPoint[] {
  const recorded = laps
    .filter((lap) => lap.position !== null && lap.position > 0 && (start === null || lap.lap > 0))
    .map((lap) => ({ lap: lap.lap, position: lap.position as number, start: false }));
  return start !== null ? [{ lap: 0, position: start, start: true }, ...recorded] : recorded;
}

/** A race left before the flag (#97): the lap he was on and where he was running when the recording ended. */
export interface RaceRetirement {
  lap: number | null;
  position: number | null;
}

/** What the shell knows of the race's result from the session (`SessionOverview`): the official start and a retirement. */
export interface RaceResult {
  /** The official start (`start_position`: the grid, else the backend's position at the green flag); null when unknown. */
  start: number | null;
  /** Set when the recording stopped before the race did and no official result stands (`finished` false). */
  retired: RaceRetirement | null;
}

const positive = (value: unknown): number | null => (typeof value === "number" && Number.isFinite(value) && value > 0 ? value : null);

/**
 * The race's result for the Race chart. The shell's, from the session by the Sessions row's rule, when it passes one (null: not a
 * race). Without the shell (the harness), the race view's own, for a race only (the backend writes `race_summary.finish` for a Race
 * session alone; the identity's event type names the weekend, not the phase): `race_summary.starting_position` and, when
 * `finish.finished` is false, `finish.last_lap` and `finish.position_at_end`.
 */
export function raceResult(race: RaceViewData | null | undefined, given?: RaceResult | null): RaceResult | null {
  if (given !== undefined) return given && { start: positive(given.start), retired: given.retired };
  const summary = asRecord(race?.race_summary);
  if (summary.finish === null || typeof summary.finish !== "object") return null;
  const finish = asRecord(summary.finish);
  const lap = finish.last_lap;
  return {
    start: positive(summary.starting_position),
    retired: finish.finished === false ? { lap: typeof lap === "number" && Number.isFinite(lap) && lap >= 0 ? lap : null, position: positive(finish.position_at_end) } : null,
  };
}

/** `DNF · L14`, or `DNF` when the lap is unknown. */
export function retiredText(retired: RaceRetirement): string {
  return retired.lap === null ? "DNF" : `DNF · L${retired.lap}`;
}

/** The hover of a retirement: `Running P6 when the recording ended`. */
export function retiredTitle(retired: RaceRetirement): string {
  return retired.position !== null ? `Running P${retired.position} when the recording ended` : "The recording ended before the race did";
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
 * The pace plot's top margin: room for the pit and incident tags and their glow (38 px at the default size, so the pit tag's halo
 * clears the chart's top edge, #41), and a further line when a tag would sit under the `Lap time · s` title at the plot's top left
 * (a narrow chart with an early stop or incident), so the title clears it.
 */
export function paceTopMargin(tagXs: readonly number[], left: number, k: number): number {
  const titleRight = left + 12 * 7.3 * k + 6 * k;
  const crowded = tagXs.some((x) => x - 14 * k < titleRight);
  return Math.round((crowded ? 56 : 38) * k);
}

type Hover = { kind: "lap"; lap: InvestigationLap } | { kind: "position"; point: PositionPoint } | { kind: "pit"; stop: PitStop; x: number; y: number };

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

interface ChartProps {
  inv: Investigation;
  stops: readonly PitStop[];
  knownPitLaps: ReadonlySet<number>;
  fastestLap: number | null;
  word: string;
  identities: ReadonlyMap<number, RunIdentity>;
  contexts: ReadonlyMap<number, LapContext>;
  /** The backend's traffic share per lap (and the side, when it names one), for the traffic laps' readout. */
  traffic: ReadonlyMap<number, LapTraffic>;
  /** The runs view's runs: each run's observed trend (`observed_trend`, #90). */
  runs: readonly unknown[];
  layers: readonly LayerKey[];
  header: ComponentChildren;
  /** The readout while no chart mark is under the pointer (a run line's hover or tap). */
  note?: string;
  /** A race's start (lap 0 of the position line) and retirement; null outside a race. */
  result: RaceResult | null;
}

function ProgressChart({ inv, stops, knownPitLaps, fastestLap, word, identities, contexts, traffic, runs, layers, header, note, result }: ChartProps) {
  const host = useRef<HTMLDivElement>(null);
  const { width, height } = useElementSize(host);
  const k = scaleFor(useRemPx());
  const [hover, setHover] = useState<Hover | null>(null);
  const { pace: plotted, caution: cautionDots } = useMemo(() => chartLaps(inv.laps), [inv]);
  const incidents = useMemo(() => inv.timeline.filter((item) => item.kind === "incident"), [inv]);
  const timelinePits = useMemo(() => inv.timeline.filter((item) => item.kind === "pit_service" && !knownPitLaps.has(item.lap)), [inv, knownPitLaps]);
  // Each run's observed trend from the server (#90): a straight line over its counted laps, never extended.
  const trends = useMemo(() => inv.runs.map((run) => ({ run: run.runNumber, trend: observedTrend(runs, run.runNumber) })).filter((item): item is { run: number; trend: ObservedTrend } => item.trend !== null), [inv, runs]);
  const on = (key: LayerKey): boolean => layers.includes(key);
  const track = useMemo(() => positionTrack(inv.laps, result?.start ?? null), [inv, result]);
  const retired = result?.retired ?? null;
  const lastPoint = track.length ? track[track.length - 1]! : null;
  const hasPositions = track.length >= 2;
  // The start reads as the start (#96); the last place of a race left before the flag is a running place (#97).
  const positionLabel = (point: PositionPoint): string =>
    point.start
      ? `Lap 0 · Start · P${point.position}`
      : retired && point === lastPoint
        ? `Lap ${point.lap} · P${point.position} · DNF, running when the recording ended`
        : `Lap ${point.lap} · P${point.position}`;
  const colour = (run: number | null): string => identityColour(identities, run);
  const body = (() => {
    if (inv.laps.length === 0) return <div class="ri-empty"><strong>No recorded laps</strong></div>;
    if (width < 40 || height < 40) return null;
    // The start sits at lap 0 (#96), so the lap axis begins there when the line has one.
    const minLap = Math.min(...inv.laps.map((lap) => lap.lap), ...(track[0]?.start ? [0] : []));
    const maxLap = Math.max(...inv.laps.map((lap) => lap.lap));
    const times = [...plotted.map((lap) => lap.lapTimeS as number), ...trends.flatMap(({ trend }) => [trendAt(trend, trend.firstLap), trendAt(trend, trend.lastLap)])];
    const min = times.length ? Math.min(...times) : 0;
    const max = times.length ? Math.max(...times) : 1;
    const pad = Math.max(0.08, (max - min) * 0.06);
    // Margins and label offsets are the default-size geometry times the root font size's factor; the axis text is --text-compact.
    const temps = on("temp") ? inv.laps.map((lap) => ({ lap: lap.lap, value: contexts.get(lap.lap)?.trackTempF ?? null })).filter((p): p is { lap: number; value: number } => p.value !== null) : [];
    const left = Math.round(52 * k);
    const right = Math.round((temps.length ? 48 : 12) * k);
    const draft = new PlotScale(left, 0, Math.max(60, width - left - right), 1, minLap - 0.5, Math.max(minLap + 1, maxLap) + 0.5, 0, 1);
    const top = paceTopMargin([...stops.map((stop) => draft.x(stop.lap)), ...incidents.map((item) => draft.x(item.lap))], left, k);
    const usageStrip = on("usage") ? Math.round(7 * k) : 0;
    const below = Math.round(12 * k) + (usageStrip ? usageStrip + Math.round(4 * k) : 0);
    const axisBand = Math.round(26 * k);
    const drop = Math.round(5 * k);
    const bandHeight = hasPositions ? Math.min(150 * k, Math.max(60 * k, height * 0.26)) : 0;
    const positionGap = hasPositions ? Math.round(20 * k) : 0;
    const bottomSpace = below + (hasPositions ? positionGap + bandHeight : 0) + axisBand;
    const pace = new PlotScale(left, top, Math.max(60, width - left - right), Math.max(60, height - top - bottomSpace), minLap - 0.5, Math.max(minLap + 1, maxLap) + 0.5, min - pad, max + pad);
    const positions = track.map((point) => point.position);
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
    // Context layers, each on its own scale inside the pace plot: track temperature on the right axis, fuel load from zero.
    const tempLo = temps.length ? Math.min(...temps.map((p) => p.value)) : 0;
    const tempHi = temps.length ? Math.max(...temps.map((p) => p.value)) : 1;
    const tempPad = Math.max(0.5, (tempHi - tempLo) * 0.1);
    const tempScale = new PlotScale(pace.left, pace.top, pace.width, pace.height, pace.xMin, pace.xMax, tempLo - tempPad, tempHi + tempPad);
    const fuels = on("fuel") ? inv.laps.map((lap) => ({ lap: lap.lap, value: contexts.get(lap.lap)?.fuelGal ?? null })).filter((p): p is { lap: number; value: number } => p.value !== null) : [];
    const fuelScale = new PlotScale(pace.left, pace.top, pace.width, pace.height, pace.xMin, pace.xMax, 0, Math.max(1, ...fuels.map((p) => p.value)) * 1.05);
    const line = (scale: PlotScale, segment: { lap: number; value: number }[]): string => segment.map((p, i) => `${i ? "L" : "M"}${n(scale.x(p.lap))},${n(scale.y(p.value))}`).join("");
    const lapAt = new Map([...cautionDots, ...plotted].map((lap) => [lap.lap, lap] as const));
    const dotY = (lap: InvestigationLap): number => clampY(lap.lapTimeS as number);
    const lapHover = (lap: InvestigationLap) => () => setHover({ kind: "lap", lap });
    return (
      <svg class="ri-plot-svg" viewBox={`0 0 ${n(width)} ${n(height)}`} role="group" aria-label="Race progression: lap time, run pace trend, cautions, pit stops, incidents and position by lap" onPointerLeave={(event: PointerEvent) => { if (event.pointerType !== "touch") setHover(null); }}>
        {cautionLaps.map((lap) => (
          <rect key={`c${lap.lap}`} data-caution-lap={lap.lap} x={n(pace.x(lap.lap) - lapWidth / 2)} y={n(pace.top)} width={n(Math.max(1.5, lapWidth))} height={n(bandBottom - pace.top)} class="ri-caution-shade" />
        ))}
        {/* With a layer on, a lap's whole column answers the pointer, so a lap without a dot (a pit lap) still reads. */}
        {layers.length > 0 && inv.laps.map((lap) => (
          <rect key={`h${lap.lap}`} x={n(pace.x(lap.lap) - lapWidth / 2)} y={n(pace.top)} width={n(Math.max(2, lapWidth))} height={n(pace.height + below)} class="ri-hit" data-lap-column={lap.lap} onPointerEnter={lapHover(lap)} />
        ))}
        {ticks(pace.yMin, pace.yMax, 4).map((tick, index) => (
          <g key={`y${index}`}>
            <line x1={n(pace.left)} x2={n(pace.right)} y1={n(pace.y(tick))} y2={n(pace.y(tick))} class="ri-grid" />
            <text x={n(pace.left - 6)} y={n(pace.y(tick) + drop)} text-anchor="end" class="ri-axis">{tick.toFixed(1)}</text>
          </g>
        ))}
        <text x={n(pace.left)} y={n(Math.round(13 * k))} class="ri-axis-title">Lap time · s</text>
        {temps.length > 0 && (
          <g class="ri-layer-temp" data-layer="temp">
            <text x={n(pace.right + right - 6)} y={n(Math.round(13 * k))} text-anchor="end" class="ri-axis-title">Track °F</text>
            {ticks(tempScale.yMin, tempScale.yMax, 3).map((tick) => (
              <text key={`tt${tick}`} x={n(pace.right + 6)} y={n(tempScale.y(tick) + drop)} class="ri-axis temp">{tick.toFixed(tempHi - tempLo < 3 ? 1 : 0)}</text>
            ))}
            {seriesSegments(temps).map((segment) => <path key={`tl${segment[0]!.lap}`} d={line(tempScale, segment)} class="ri-temp-line" />)}
          </g>
        )}
        {fuels.length > 0 && (
          <g class="ri-layer-fuel" data-layer="fuel">
            {/* One line through every lap with a reading; a refill is a step up; only a lap with no reading breaks it (#63). */}
            {seriesSegments(fuels).map((segment) => (
              <g key={`fl${segment[0]!.lap}`}>
                <path d={fuelPath(segment, (lap) => fuelScale.x(lap), (value) => fuelScale.y(value), n)} class="ri-fuel-line" data-fuel-from={segment[0]!.lap} data-fuel-to={segment[segment.length - 1]!.lap} />
                {[segment[0]!, ...refills(segment)].map((point) => (
                  <text key={`fv${point.lap}`} x={n(fuelScale.x(point.lap) + 4)} y={n(fuelScale.y(point.value) - 5)} class="ri-fuel-label" data-fuel-label={point.lap}>{`${point.value.toFixed(1)} gal`}</text>
                ))}
              </g>
            ))}
          </g>
        )}
        {usageStrip > 0 && (
          <g class="ri-layer-usage" data-layer="usage">
            {inv.laps.map((lap) => {
              const usage = contexts.get(lap.lap)?.usagePct ?? null;
              if (usage === null) return null;
              return <rect key={`u${lap.lap}`} x={n(pace.x(lap.lap) - lapWidth / 2)} y={n(pace.bottom + Math.round(5 * k))} width={n(Math.max(1.5, lapWidth))} height={n(usageStrip)} class="ri-usage-cell" style={`opacity:${n(0.15 + 0.85 * (usage / 100))}`} data-usage-lap={lap.lap} />;
            })}
          </g>
        )}
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
        {/* The observed trend per run (#90): a straight line from its first to its last counted lap, the slope at its end. */}
        {(() => {
          const placed: number[] = [];
          return trends.map(({ run, trend }) => {
            const [x1, x2] = [pace.x(trend.firstLap), pace.x(trend.lastLap)];
            const [y1, y2] = [clampY(trendAt(trend, trend.firstLap)), clampY(trendAt(trend, trend.lastLap))];
            // The label sits above the line's end; one that would print over another moves up a line.
            let labelY = Math.max(pace.top + 10 * k, y2 - 9 * k);
            while (placed.some((other) => Math.abs(other - labelY) < 14 * k)) labelY -= 14 * k;
            placed.push(labelY);
            return (
              <g key={`t${run}`} class="ri-fit-group" data-trend-run={run} data-trend-span={`${trend.firstLap}-${trend.lastLap}`} style={`--run:${colour(run)}`}>
                <title>{`${identities.get(run)?.name ?? `Run ${run}`} · ${trendText(trend)} · ${trendHover(trend)}`}</title>
                <line x1={n(x1)} y1={n(y1)} x2={n(x2)} y2={n(y2)} class="ri-fit-halo" />
                <line x1={n(x1)} y1={n(y1)} x2={n(x2)} y2={n(y2)} class="ri-fit ri-trend" stroke-dasharray={identities.get(run)?.dash || undefined} />
                <text x={n(x2)} y={n(labelY)} text-anchor="end" class="ri-fit-label" data-trend-label={run}>{trendText(trend)}</text>
              </g>
            );
          });
        })()}
        {cautionDots.map((lap) => {
          const off = (lap.lapTimeS as number) > pace.yMax;
          return (
            <circle key={`cd${lap.lap}`} cx={n(pace.x(lap.lap))} cy={n(dotY(lap))} r={n(dotRadius * 0.85)} class={`ri-dot caution${off ? " off" : ""}`} tabIndex={0} data-lap={lap.lap} data-caution-dot={lap.lap}
              onPointerEnter={lapHover(lap)} onFocus={lapHover(lap)} onClick={lapHover(lap)} aria-label={lapLabel(lap, word)}>
              <title>{lapLabel(lap, word)}</title>
            </circle>
          );
        })}
        {/* Open air: the solid dot. Screened for close traffic alone: the same dot, fainter, outlined (#92). Anything else: the hollow dot. */}
        {plotted.map((lap) => (
          <LapDot
            key={lap.lap} lap={lap.lap} kind={lapKind(lap)} side={traffic.get(lap.lap)?.side ?? null} cx={pace.x(lap.lap)} cy={dotY(lap)} r={dotRadius} colour={colour(lap.runNumber)}
            label={lapLabel(lap, word, traffic.get(lap.lap))} onSelect={lapHover(lap)}
          />
        ))}
        {/* The fastest lap: a magenta ring around its mark. */}
        {fastestLap !== null && lapAt.has(fastestLap) && (
          <circle cx={n(pace.x(fastestLap))} cy={n(dotY(lapAt.get(fastestLap)!))} r={n(dotRadius + 2.6)} class="ri-best-ring" data-fastest={fastestLap} />
        )}
        {posScale && (
          <g class="ri-position-band" data-position-band>
            <text x={n(posScale.left)} y={n(posScale.top - Math.round(8 * k))} class="ri-axis-title">Position</text>
            {positionTicks(posMin, posMax).map((position) => (
              <g key={`pt${position}`}>
                <line x1={n(posScale.left)} x2={n(posScale.right)} y1={n(py(position))} y2={n(py(position))} class="ri-grid soft" />
                <text x={n(posScale.left - 6)} y={n(py(position) + drop)} text-anchor="end" class="ri-axis" data-position-tick={position}>{`P${position}`}</text>
              </g>
            ))}
            {/* A race left before the flag (#97): no finish, the band's corner says so and where he was running is its hover. */}
            {retired && (
              <g class="ri-dnf" data-retired={retired.lap ?? ""}>
                <title>{retiredTitle(retired)}</title>
                <text x={n(posScale.right)} y={n(posScale.top - Math.round(8 * k))} text-anchor="end" class="ri-axis-title ri-dnf-text">{retiredText(retired)}</text>
              </g>
            )}
            {positionSegments(track).map((segment) => (
              <line key={`s${segment.to.lap}`} x1={n(posScale.x(segment.from.lap))} y1={n(py(segment.from.position))} x2={n(posScale.x(segment.to.lap))} y2={n(py(segment.to.position))} class={`ri-position-seg ${segment.tone}`} data-tone={segment.tone} />
            ))}
            {track.map((point) => (
              <circle key={`pd${point.lap}`} cx={n(posScale.x(point.lap))} cy={n(py(point.position))} r={n(Math.max(2.2, Math.min(3.4, lapWidth * 0.32)))} class={`ri-pos-dot${point.start ? " start" : ""}${hover?.kind === "position" && hover.point.lap === point.lap ? " on" : ""}`}
                data-position-lap={point.lap} data-position={point.position} data-position-start={point.start ? "" : undefined} tabIndex={-1} onPointerEnter={() => setHover({ kind: "position", point })}>
                <title>{positionLabel(point)}</title>
              </circle>
            ))}
            {/* Wide invisible hit columns so a lap's position reads without hitting a 3 px dot. */}
            {track.map((point) => (
              <rect key={`ph${point.lap}`} x={n(posScale.x(point.lap) - lapWidth / 2)} y={n(posScale.top - 6)} width={n(Math.max(2, lapWidth))} height={n(posScale.height + 12)} class="ri-hit" onPointerEnter={() => setHover({ kind: "position", point })} />
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
  const lapReadout = (lap: InvestigationLap): string => {
    const parts = [lapLabel(lap, word, traffic.get(lap.lap))];
    if (lap.lap === fastestLap) parts.push("Fastest");
    parts.push(...layersReadout(layers, contexts.get(lap.lap)));
    return parts.join(" · ");
  };
  const readout = hover?.kind === "position"
    ? positionLabel(hover.point)
    : hover?.kind === "lap"
      ? lapReadout(hover.lap)
      : hover?.kind === "pit"
        ? pitLabel(hover.stop)
        : note ?? "";
  return (
    <div class="ri-progress" data-ri-progress>
      <div class="ri-chart-head">
        {header}
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

/** The run's swatch: its identity colour and line style. */
export function RunSwatch({ identity, width = 20 }: { identity: RunIdentity | undefined; width?: number }) {
  return (
    <svg width={width} height="8" aria-hidden="true">
      <line x1="0" y1="4" x2={width} y2="4" stroke={identity?.color ?? "var(--color-text-muted)"} stroke-width="2.5" stroke-dasharray={identity?.dash || undefined} />
    </svg>
  );
}

/** The device's context layers: off by default, remembered in this browser. */
function useLayers(): [LayerKey[], (key: LayerKey) => void] {
  const storage = (): Storage | null => {
    try {
      return typeof localStorage === "undefined" ? null : localStorage;
    } catch {
      return null;
    }
  };
  const [layers, setLayers] = useState<LayerKey[]>(() => readLayers(storage()));
  const toggle = (key: LayerKey): void => {
    setLayers((current) => {
      const next = current.includes(key) ? current.filter((item) => item !== key) : [...current, key];
      writeLayers(storage(), next);
      return next;
    });
  };
  return [layers, toggle];
}

const NO_RUNS: readonly unknown[] = [];

export interface RaceViewProps {
  inv: Investigation;
  race?: RaceViewData;
  runs?: readonly unknown[];
  sessionType?: string | null;
  /** The session's start and retirement from the shell (`SessionOverview`; null: not a race); without it the race view's own. */
  result?: RaceResult | null;
}

export function RaceView({ inv, race, runs, sessionType, result: given }: RaceViewProps) {
  const result = useMemo(() => raceResult(race, given), [race, given]);
  const allStops = useMemo(() => (race ? pitStops(race, { runs: runs ?? [] }, null) : []), [race, runs]);
  const stops = useMemo(() => inRaceStops(allStops), [allStops]);
  const knownPitLaps = useMemo(() => new Set(allStops.map((stop) => stop.lap)), [allStops]);
  const fastestLap = useMemo(() => raceFastestLap(inv.laps, race), [inv, race]);
  const identities = useMemo(() => runIdentities(inv.runs.map((run) => run.runNumber), allStops), [inv, allStops]);
  const contexts = useMemo(() => lapContexts(race), [race]);
  const traffic = useMemo(() => lapTraffic(race), [race]);
  const offered = useMemo(() => availableLayers(contexts), [contexts]);
  const [stored, toggleLayer] = useLayers();
  const layers = stored.filter((key) => offered.some((layer) => layer.key === key));
  const word = exposureWord(sessionType);
  const [note, setNote] = useState<string | null>(null);
  const lines = useMemo(() => inv.runs.map((run) => ({ run, parts: runLineParts(run, identities.get(run.runNumber), runs ?? [], inv.laps) })), [inv, identities, runs]);
  const header = (
    <>
      <span class="ri-card-title">Race progression</span>
      <div class="ri-legend" data-legend="runs">
        {inv.runs.map((run) => {
          const identity = identities.get(run.runNumber);
          return <span key={run.runNumber} class="ri-run-key" data-legend-run={run.runNumber}><RunSwatch identity={identity} />{identity?.name ?? `Run ${run.runNumber}`}</span>;
        })}
      </div>
      {offered.length > 0 && (
        <div class="ri-layer-toggles" role="group" aria-label="Context layers">
          {offered.map((layer) => (
            <button type="button" key={layer.key} class="ri-pill-toggle ri-layer-toggle" aria-pressed={layers.includes(layer.key)} data-layer-toggle={layer.key} onClick={() => toggleLayer(layer.key)}>{layer.label}</button>
          ))}
        </div>
      )}
    </>
  );
  return (
    <div class="ri-race-view" data-ri-view="race">
      <div class="ri-card ri-fill">
        <ProgressChart inv={inv} stops={stops} knownPitLaps={knownPitLaps} fastestLap={fastestLap} word={word} identities={identities} contexts={contexts} traffic={traffic} runs={runs ?? NO_RUNS} layers={layers} header={header} note={note ?? undefined} result={result} />
        {lines.length > 0 && (
          <ul class="ri-run-lines" aria-label="Runs">
            {lines.map(({ run, parts }) => {
              const show = (): void => setNote(runLineReadout(parts));
              return (
                <li key={run.runNumber} data-run-line={run.runNumber} style={`--run:${identityColour(identities, run.runNumber)}`} tabIndex={0}
                  onPointerEnter={show} onFocus={show} onClick={show} onPointerLeave={(event: PointerEvent) => { if (event.pointerType !== "touch") setNote(null); }} onBlur={() => setNote(null)}>
                  {parts.map((part, index) => (
                    <Fragment key={part.key}>{index > 0 && " · "}<span data-part={part.key} title={part.hover}>{part.text}</span></Fragment>
                  ))}
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}

import { Fragment, type ComponentChildren } from "preact";
import { useMemo, useRef, useState } from "preact/hooks";
import { exposureText, lapTime, n, PlotScale, reasonsText, ticks } from "../format";
import type { Comparison, Investigation, InvestigationLap } from "../investigation-model";
import { incidentPointsByLap, inRaceStops, pitStops, type PitStop } from "../geometry/rail-model";
import { fastestCleanLap } from "../geometry/trace-model";
import { asArray, asRecord, type RaceViewData, type TraceLapData } from "../types";
import { useElementSize } from "./hooks";
import { PitCard, PitGlyph, pitLabel } from "./PitPictogram";
import { availableLayers, fuelPath, lapContexts, layersReadout, readLayers, refills, seriesSegments, writeLayers, type LapContext, type LayerKey } from "./race-layers";
import { fitDrawn, fitOnLaps, identityColour, runIdentities, runLineParts, runLineReadout, smoothPath, type RunIdentity } from "./runs-model";
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

/** A lap's readout: `Lap 36 · 28.559 s · P7 · Run 2 · 36.0 green · Not in pace fit · close traffic`. */
export function lapLabel(lap: InvestigationLap, word = "green"): string {
  const parts = [`Lap ${lap.lap}`, `${lapTime(lap.lapTimeS)} s`];
  if (lap.position !== null) parts.push(`P${lap.position}`);
  if (lap.flagState === "caution") parts.push("Caution");
  if (lap.runNumber !== null) parts.push(`Run ${lap.runNumber}`);
  if (lap.greenExposure !== null) parts.push(exposureText(lap.greenExposure, word));
  const status = lap.flagState === "caution" ? null : fitStatus(lap);
  if (status) parts.push(status);
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
 * The pace plot's top margin: room for the pit and incident tags and their glow (38 px at the default size, so the pit tag's halo
 * clears the chart's top edge, #41), and a further line when a tag would sit under the `Lap time · s` title at the plot's top left
 * (a narrow chart with an early stop or incident), so the title clears it.
 */
export function paceTopMargin(tagXs: readonly number[], left: number, k: number): number {
  const titleRight = left + 12 * 7.3 * k + 6 * k;
  const crowded = tagXs.some((x) => x - 14 * k < titleRight);
  return Math.round((crowded ? 56 : 38) * k);
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

interface ChartProps {
  inv: Investigation;
  stops: readonly PitStop[];
  knownPitLaps: ReadonlySet<number>;
  fastestLap: number | null;
  word: string;
  identities: ReadonlyMap<number, RunIdentity>;
  contexts: ReadonlyMap<number, LapContext>;
  layers: readonly LayerKey[];
  header: ComponentChildren;
  /** The readout while no chart mark is under the pointer (a run line's hover or tap). */
  note?: string;
}

function ProgressChart({ inv, stops, knownPitLaps, fastestLap, word, identities, contexts, layers, header, note }: ChartProps) {
  const host = useRef<HTMLDivElement>(null);
  const { width, height } = useElementSize(host);
  const k = scaleFor(useRemPx());
  const [hover, setHover] = useState<Hover | null>(null);
  const { pace: plotted, caution: cautionDots } = useMemo(() => chartLaps(inv.laps), [inv]);
  const incidents = useMemo(() => inv.timeline.filter((item) => item.kind === "incident"), [inv]);
  const timelinePits = useMemo(() => inv.timeline.filter((item) => item.kind === "pit_service" && !knownPitLaps.has(item.lap)), [inv, knownPitLaps]);
  // The screened fit only where it covers at least half the run's green laps (#59a).
  const fits = useMemo(() => inv.runs.filter((run) => fitDrawn(run, inv.laps)).map((run) => ({ run, points: fitOnLaps(run, inv.laps) })).filter((fit) => fit.points.length >= 2), [inv]);
  const on = (key: LayerKey): boolean => layers.includes(key);
  const placed = inv.laps.filter((lap) => lap.position !== null && lap.position > 0);
  const hasPositions = placed.length >= 2;
  const colour = (run: number | null): string => identityColour(identities, run);
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
      <svg class="ri-plot-svg" viewBox={`0 0 ${n(width)} ${n(height)}`} role="group" aria-label="Race progression: lap time, run pace fit, cautions, pit stops, incidents and position by lap" onPointerLeave={(event: PointerEvent) => { if (event.pointerType !== "touch") setHover(null); }}>
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
        {/* The backend's screened fit per run: a smooth curve over the laps of its supported span; the dots are the observations. */}
        {fits.map(({ run, points }) => {
          const d = smoothPath(points.map((point) => [pace.x(point.lap), clampY(point.y)] as const));
          return (
            <g key={`f${run.runNumber}`} class="ri-fit-group" data-fit-run={run.runNumber} data-fit-span={`${points[0]!.lap}-${points[points.length - 1]!.lap}`} style={`--run:${colour(run.runNumber)}`}>
              <path d={d} class="ri-fit-halo" />
              <path d={d} class="ri-fit" stroke-dasharray={identities.get(run.runNumber)?.dash || undefined} />
            </g>
          );
        })}
        {cautionDots.map((lap) => {
          const off = (lap.lapTimeS as number) > pace.yMax;
          return (
            <circle key={`cd${lap.lap}`} cx={n(pace.x(lap.lap))} cy={n(dotY(lap))} r={n(dotRadius * 0.85)} class={`ri-dot caution${off ? " off" : ""}`} tabIndex={0} data-lap={lap.lap} data-caution-dot={lap.lap}
              onPointerEnter={lapHover(lap)} onFocus={lapHover(lap)} onClick={lapHover(lap)} aria-label={lapLabel(lap, word)}>
              <title>{lapLabel(lap, word)}</title>
            </circle>
          );
        })}
        {plotted.map((lap) => {
          const color = colour(lap.runNumber);
          return (
            <circle
              key={lap.lap} cx={n(pace.x(lap.lap))} cy={n(dotY(lap))} r={n(dotRadius)}
              fill={lap.eligible ? color : "var(--ri-card)"} stroke={color}
              class={`ri-dot${lap.eligible ? "" : " excluded"}`} tabIndex={0} data-lap={lap.lap}
              onPointerEnter={lapHover(lap)} onFocus={lapHover(lap)} onClick={lapHover(lap)} aria-label={lapLabel(lap, word)}
            ><title>{lapLabel(lap, word)}</title></circle>
          );
        })}
        {/* The fastest lap: a magenta ring around its dot. */}
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
  const lapReadout = (lap: InvestigationLap): string => {
    const parts = [lapLabel(lap, word)];
    if (lap.lap === fastestLap) parts.push("Fastest");
    parts.push(...layersReadout(layers, contexts.get(lap.lap)));
    return parts.join(" · ");
  };
  const readout = hover?.kind === "position"
    ? `Lap ${hover.lap.lap} · P${hover.lap.position}`
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

export interface RaceViewProps {
  inv: Investigation;
  race?: RaceViewData;
  runs?: readonly unknown[];
  sessionType?: string | null;
}

export function RaceView({ inv, race, runs, sessionType }: RaceViewProps) {
  const allStops = useMemo(() => (race ? pitStops(race, { runs: runs ?? [] }, null) : []), [race, runs]);
  const stops = useMemo(() => inRaceStops(allStops), [allStops]);
  const knownPitLaps = useMemo(() => new Set(allStops.map((stop) => stop.lap)), [allStops]);
  const fastestLap = useMemo(() => raceFastestLap(inv.laps, race), [inv, race]);
  const identities = useMemo(() => runIdentities(inv.runs.map((run) => run.runNumber), allStops), [inv, allStops]);
  const contexts = useMemo(() => lapContexts(race), [race]);
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
        <ProgressChart inv={inv} stops={stops} knownPitLaps={knownPitLaps} fastestLap={fastestLap} word={word} identities={identities} contexts={contexts} layers={layers} header={header} note={note ?? undefined} />
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

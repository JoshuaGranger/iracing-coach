import { useMemo, useRef, useState } from "preact/hooks";
import { n, PlotScale, ticks } from "../format";
import { pitStops } from "../geometry/rail-model";
import type { Investigation, InvestigationLap } from "../investigation-model";
import type { RaceViewData } from "../types";
import { exposureWord, lapLabel, RunSwatch } from "./RaceView";
import { useElementSize, useSessionNumbers } from "./hooks";
import { fitDrawn, fitLabel, fitSegment, identityColour, runCards, runIdentities, runsChartLaps, sparkGeometry, toggleRun, type RunCard, type RunIdentity } from "./runs-model";
import { scaleFor, useRemPx } from "./unit";

export interface RunsViewProps {
  inv: Investigation;
  sessionType?: string | null;
  /** The runs view's runs (BackendRun): fuel at start and end, tire laps. */
  runs?: readonly unknown[];
  /** The race view: its stops say which runs share a tire set. */
  race?: RaceViewData;
  /** Names the session-scoped memory of hidden runs (the analysis id). */
  analysisKey?: string;
}

/** A run card is the run's toggle on the chart: pressed shows the run, released hides its dots and fit and dims the card. */
function RunCardView({ card, identity, shown, locked, onToggle }: { card: RunCard; identity: RunIdentity | undefined; shown: boolean; locked: boolean; onToggle(): void }) {
  const color = identity?.color ?? "var(--color-text-muted)";
  const spark = sparkGeometry(card, 96, 24);
  return (
    <button type="button" class={`ri-run-card${shown ? "" : " hidden"}`} aria-pressed={shown} aria-disabled={locked || undefined} data-run-card={card.runNumber} style={`--run:${color}`} onClick={onToggle}
      title={locked ? "One run stays on the chart" : undefined}>
      <span class="ri-run-card-head">
        <RunSwatch identity={identity} width={22} />
        <b>{identity?.name ?? `Run ${card.runNumber}`}</b><small>{card.lapsText}</small>
      </span>
      <svg class="ri-spark" viewBox="0 0 96 24" aria-hidden="true">
        {spark.fit && <line x1={spark.fit[0]} y1={spark.fit[1]} x2={spark.fit[2]} y2={spark.fit[3]} class="ri-spark-fit" stroke-dasharray={identity?.dash || undefined} />}
        {spark.dots.map((dot, index) => <circle key={index} cx={dot.cx} cy={dot.cy} r="1.6" class={`ri-spark-dot${dot.used ? "" : " hollow"}`} />)}
      </svg>
      <span class="ri-facts">
        {/* Wide figures in the first column: the fit and the fuel band; the observed fall-off beside the fit (#59a). */}
        <span class="ri-fact"><span class="ri-fact-label">Fit</span><b class="ri-fact-value" data-run-slope>{card.slope ?? "—"}</b></span>
        <span class="ri-fact" title={card.fallOffHover ?? undefined}><span class="ri-fact-label">Fall-off</span><b class="ri-fact-value" data-run-falloff>{card.fallOff ?? "—"}</b></span>
        <span class="ri-fact"><span class="ri-fact-label">Used</span><b class="ri-fact-value" data-run-used>{card.used}</b></span>
        <span class="ri-fact"><span class="ri-fact-label">Fuel</span><b class="ri-fact-value">{card.fuelBand ?? "—"}</b></span>
        <span class="ri-fact"><span class="ri-fact-label">Tire laps</span><b class="ri-fact-value">{card.tireLaps ?? "—"}</b></span>
        <span class="ri-fact"><span class="ri-fact-label">Screened</span><b class="ri-fact-value" data-run-screened>{card.screened}</b></span>
      </span>
    </button>
  );
}

export function RunsView({ inv, sessionType, runs, race, analysisKey }: RunsViewProps) {
  const word = exposureWord(sessionType);
  const host = useRef<HTMLDivElement>(null);
  const { width, height } = useElementSize(host);
  const k = scaleFor(useRemPx());
  const [hover, setHover] = useState<InvestigationLap | null>(null);
  const runNumbers = useMemo(() => inv.runs.map((run) => run.runNumber), [inv]);
  const [storedHidden, setHidden] = useSessionNumbers(`coach.runs-hidden.${analysisKey ?? "analysis"}`);
  const hidden = useMemo(() => {
    const valid = new Set([...storedHidden].filter((run) => runNumbers.includes(run)));
    return runNumbers.length > 0 && runNumbers.every((run) => valid.has(run)) ? new Set<number>() : valid;
  }, [storedHidden, runNumbers]);
  const identities = useMemo(() => runIdentities(runNumbers, race ? pitStops(race, { runs: runs ?? [] }, null) : []), [runNumbers, race, runs]);
  const allPoints = useMemo(() => runsChartLaps(inv.laps), [inv]);
  const points = useMemo(() => allPoints.filter((lap) => lap.runNumber === null || !hidden.has(lap.runNumber)), [allPoints, hidden]);
  // The screened fit only where it covers at least half the run's green laps (#59a), as on Race.
  const fits = useMemo(() => inv.runs.filter((run) => !hidden.has(run.runNumber) && fitDrawn(run, inv.laps)).map((run) => ({ run, segment: fitSegment(run), label: fitLabel(run) })).filter((fit) => fit.segment !== null), [inv, hidden]);
  const cards = useMemo(() => runCards(inv, runs ?? []), [inv, runs]);
  // The chart's scale follows the visible runs: their laps and their fits.
  const plot = useMemo(() => {
    if (points.length === 0 || width < 80 || height < 80) return null;
    const times = [...points.map((point) => point.lapTimeS as number), ...fits.flatMap((fit) => [fit.segment!.y1, fit.segment!.y2])];
    const min = Math.min(...times);
    const max = Math.max(...times);
    const pad = Math.max(0.08, (max - min) * 0.06);
    const exposure = Math.max(...points.map((point) => point.greenExposure as number), ...fits.map((fit) => fit.segment!.x2));
    // Margins are the default-size geometry times the root font size's factor; the axis text is --text-compact.
    const top = Math.round(22 * k);
    const left = Math.round(52 * k);
    return new PlotScale(left, top, Math.max(60, width - left - Math.round(12 * k)), Math.max(60, height - top - Math.round(50 * k)), 0, Math.max(1, exposure * 1.02), min - pad, max + pad);
  }, [points, fits, width, height, k]);
  const drop = Math.round(5 * k); // baseline drop that centres a tick label on its line
  // Fit labels sit above their line's end; one that would print over another moves up a line.
  const labelY = new Map<number, number>();
  if (plot) {
    const placed: number[] = [];
    for (const { run, segment } of fits) {
      let y = plot.y(Math.min(plot.yMax, Math.max(plot.yMin, segment!.y2))) - 9 * k;
      while (placed.some((other) => Math.abs(other - y) < 15 * k)) y -= 15 * k;
      placed.push(y);
      labelY.set(run.runNumber, y);
    }
  }
  const readout = hover ? lapLabel(hover, word) : "";
  const visibleCount = runNumbers.filter((run) => !hidden.has(run)).length;
  return (
    <div class="ri-runs-view" data-ri-view="runs">
      <div class="ri-card ri-fill ri-runs" data-ri-runs>
        {inv.runs.length === 0 && allPoints.length === 0 ? (
          <div class="ri-empty"><strong>No laps with run exposure</strong></div>
        ) : (
          <>
            <div class="ri-chart-head">
              <div class="ri-legend" data-legend="runs">
                {inv.runs.filter((run) => !hidden.has(run.runNumber)).map((run) => {
                  const identity = identities.get(run.runNumber);
                  return <span class="ri-run-key" key={run.runNumber} data-run={run.runNumber}><RunSwatch identity={identity} />{identity?.name ?? `Run ${run.runNumber}`}</span>;
                })}
              </div>
              <output class="ri-readout" aria-live="polite" data-runs-readout>{readout}</output>
            </div>
            <div class="ri-plot" ref={host}>
              {plot && (
                <svg class="ri-plot-svg" viewBox={`0 0 ${n(width)} ${n(height)}`} role="group" aria-label={`Lap time against ${word} laps into run, runs overlaid`} data-domain={`${plot.xMax.toFixed(2)} ${plot.yMin.toFixed(3)}-${plot.yMax.toFixed(3)}`}>
                  {ticks(plot.yMin, plot.yMax, 4).map((tick, index) => (
                    <g key={`y${index}`}>
                      <line x1={n(plot.left)} x2={n(plot.right)} y1={n(plot.y(tick))} y2={n(plot.y(tick))} class="ri-grid" />
                      <text x={n(plot.left - 6)} y={n(plot.y(tick) + drop)} text-anchor="end" class="ri-axis">{tick.toFixed(1)}</text>
                    </g>
                  ))}
                  {ticks(0, plot.xMax, 6).map((tick, index) => (
                    <text key={`x${index}`} x={n(plot.x(tick))} y={n(plot.bottom + 22 * k)} text-anchor="middle" class="ri-axis">{tick.toFixed(0)}</text>
                  ))}
                  <text x={n(plot.left)} y={n(Math.round(15 * k))} class="ri-axis-title">Lap time · s</text>
                  <text x={n(plot.right)} y={n(plot.bottom + 42 * k)} text-anchor="end" class="ri-axis-title">{word === "clean" ? "Clean laps into run" : "Green laps into run"}</text>
                  {/* The backend's screened fit, only over the span it fitted, labelled with its slope and eligible count. The dots are the observations. */}
                  {fits.map(({ run, segment, label }) => {
                    const s = segment!;
                    const x1 = plot.x(s.x1);
                    const x2 = plot.x(s.x2);
                    const y1 = plot.y(Math.min(plot.yMax, Math.max(plot.yMin, s.y1)));
                    const y2 = plot.y(Math.min(plot.yMax, Math.max(plot.yMin, s.y2)));
                    const dash = identities.get(run.runNumber)?.dash;
                    return (
                      <g key={`f${run.runNumber}`} class="ri-fit-group" data-fit-run={run.runNumber} data-fit-span={`${s.x1}-${s.x2}`} style={`--run:${identityColour(identities, run.runNumber)}`}>
                        <line x1={n(x1)} y1={n(y1)} x2={n(x2)} y2={n(y2)} class="ri-fit-halo" />
                        <line x1={n(x1)} y1={n(y1)} x2={n(x2)} y2={n(y2)} class="ri-fit" stroke-dasharray={dash || undefined} />
                        {label && <text x={n(Math.min(x2, plot.right) - 2)} y={n(labelY.get(run.runNumber) ?? y2 - 9 * k)} text-anchor="end" class="ri-fit-label" data-fit-label>{label}</text>}
                      </g>
                    );
                  })}
                  {points.map((point) => {
                    const color = identityColour(identities, point.runNumber);
                    return (
                      <circle key={point.lap} cx={n(plot.x(point.greenExposure as number))} cy={n(plot.y(Math.min(plot.yMax, Math.max(plot.yMin, point.lapTimeS as number))))} r="2.8"
                        fill={point.eligible ? color : "var(--ri-card)"} stroke={color} class={`ri-dot${point.eligible ? "" : " excluded"}`} tabIndex={0} data-lap={point.lap} data-run={point.runNumber ?? ""}
                        onPointerEnter={() => setHover(point)} onFocus={() => setHover(point)} onClick={() => setHover(point)} aria-label={lapLabel(point, word)}><title>{lapLabel(point, word)}</title></circle>
                    );
                  })}
                </svg>
              )}
            </div>
          </>
        )}
      </div>
      {cards.length > 0 && (
        <div class="ri-run-cards" role="group" aria-label="Runs on the chart">
          {cards.map((card) => {
            const shown = !hidden.has(card.runNumber);
            return (
              <RunCardView key={card.runNumber} card={card} identity={identities.get(card.runNumber)} shown={shown} locked={shown && visibleCount <= 1}
                onToggle={() => setHidden(toggleRun(hidden, card.runNumber, runNumbers))} />
            );
          })}
        </div>
      )}
    </div>
  );
}

import { useMemo, useRef, useState } from "preact/hooks";
import { n, PlotScale, reason, runColor, runDash, ticks } from "../format";
import type { Comparison, Investigation, InvestigationLap } from "../investigation-model";
import { exposureWord, lapLabel, trendPath } from "./RaceView";
import { useElementSize } from "./hooks";
import { fitLabel, fitSegment, runCards, sparkPath, type RunCard } from "./runs-model";
import { scaleFor, useRemPx } from "./unit";

export interface RunsViewProps {
  inv: Investigation;
  selected: Comparison | null;
  sessionType?: string | null;
  /** The runs view's runs (BackendRun): fuel at start and end, tire laps. */
  runs?: readonly unknown[];
}

function RunCardView({ card }: { card: RunCard }) {
  const color = runColor(card.runNumber);
  const dash = runDash(card.runNumber);
  return (
    <article class="ri-run-card" role="listitem" data-run-card={card.runNumber} style={`--run:${color}`}>
      <header>
        <svg width="22" height="8" aria-hidden="true"><line x1="0" y1="4" x2="22" y2="4" stroke={color} stroke-width="3" stroke-dasharray={dash} /></svg>
        <b>Run {card.runNumber}</b><small>{card.lapsText}</small>
      </header>
      <svg class="ri-spark" viewBox="0 0 96 24" preserveAspectRatio="none" aria-hidden="true"><path d={sparkPath(card.trend, 96, 24)} stroke={color} stroke-dasharray={dash} /></svg>
      <div class="ri-facts">
        <span class="ri-fact"><span class="ri-fact-label">Fit</span><b class="ri-fact-value" data-run-slope>{card.slope ?? <span title={card.noFitReason ? reason(card.noFitReason) : undefined}>No fit</span>}</b></span>
        <span class="ri-fact"><span class="ri-fact-label">Used</span><b class="ri-fact-value" data-run-used>{card.used}</b></span>
        <span class="ri-fact"><span class="ri-fact-label">Screened</span><b class="ri-fact-value" data-run-screened>{card.screened}</b></span>
        <span class="ri-fact"><span class="ri-fact-label">Fuel</span><b class="ri-fact-value">{card.fuelBand ?? "—"}</b></span>
        <span class="ri-fact"><span class="ri-fact-label">Tire laps</span><b class="ri-fact-value">{card.tireLaps ?? "—"}</b></span>
      </div>
    </article>
  );
}

export function RunsView({ inv, sessionType, runs }: RunsViewProps) {
  const word = exposureWord(sessionType);
  const host = useRef<HTMLDivElement>(null);
  const { width, height } = useElementSize(host);
  const k = scaleFor(useRemPx());
  const [hover, setHover] = useState<InvestigationLap | null>(null);
  const points = useMemo(() => inv.laps.filter((lap) => lap.plotPace && lap.greenExposure !== null && lap.lapTimeS !== null && lap.lapTimeS > 0), [inv]);
  const cards = useMemo(() => runCards(inv, runs ?? []), [inv, runs]);
  const plot = useMemo(() => {
    if (points.length === 0 || width < 80 || height < 80) return null;
    const times = points.map((point) => point.lapTimeS as number);
    const min = Math.min(...times);
    const max = Math.max(...times);
    const pad = Math.max(0.08, (max - min) * 0.06);
    const exposure = Math.max(...points.map((point) => point.greenExposure as number));
    // Margins are the default-size geometry times the root font size's factor; the axis text is --text-compact.
    const top = Math.round(22 * k);
    const left = Math.round(52 * k);
    return new PlotScale(left, top, Math.max(60, width - left - Math.round(12 * k)), Math.max(60, height - top - Math.round(50 * k)), 0, Math.max(1, exposure * 1.02), min - pad, max + pad);
  }, [points, width, height, k]);
  const drop = Math.round(5 * k); // baseline drop that centres a tick label on its line
  const readout = hover ? lapLabel(hover, word) : "";
  return (
    <div class="ri-runs-view" data-ri-view="runs">
      <div class="ri-card ri-fill ri-runs" data-ri-runs>
        {inv.runs.length === 0 && points.length === 0 ? (
          <div class="ri-empty"><strong>No laps with run exposure</strong></div>
        ) : (
          <>
            <div class="ri-chart-head">
              <div class="ri-legend">
                {inv.runs.map((run) => (
                  <span class="ri-run-key" key={run.runNumber} data-run={run.runNumber}>
                    <svg width="20" height="8" aria-hidden="true"><line x1="0" y1="4" x2="20" y2="4" stroke={runColor(run.runNumber)} stroke-width="2.5" stroke-dasharray={runDash(run.runNumber)} /></svg>Run {run.runNumber}
                  </span>
                ))}
                <span><i class="lt" />Observed · 5-lap median</span>
                <span><i class="lf" />Screened fit</span>
                <span><i class="ld hollow" />Not used in pace fit</span>
              </div>
              <output class="ri-readout" aria-live="polite">{readout}</output>
            </div>
            <div class="ri-plot" ref={host}>
              {plot && (
                <svg class="ri-plot-svg" viewBox={`0 0 ${n(width)} ${n(height)}`} role="group" aria-label={`Lap time against ${word} laps into run, runs overlaid`}>
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
                  {/* Observed trend: the backend's 5-observation median, the run's colour and line style. */}
                  {inv.runs.map((run) => run.trend.segments.map((segment, index) => (
                    <path key={`t${run.runNumber}-${index}`} d={trendPath(plot, segment, (point) => point.x)} class="ri-trend" stroke={runColor(run.runNumber)} stroke-dasharray={runDash(run.runNumber)} data-trend-run={run.runNumber} />
                  )))}
                  {points.map((point) => {
                    const color = runColor(point.runNumber);
                    return (
                      <circle key={point.lap} cx={n(plot.x(point.greenExposure as number))} cy={n(plot.y(Math.min(plot.yMax, Math.max(plot.yMin, point.lapTimeS as number))))} r="2.8"
                        fill={point.eligible ? color : "var(--ri-card)"} stroke={color} class={`ri-dot${point.eligible ? "" : " excluded"}`} tabIndex={0} data-lap={point.lap}
                        onPointerEnter={() => setHover(point)} onFocus={() => setHover(point)} onClick={() => setHover(point)} aria-label={lapLabel(point, word)}><title>{lapLabel(point, word)}</title></circle>
                    );
                  })}
                  {/* Screened fit: drawn only over the span the backend fitted, labelled with its slope and eligible count. */}
                  {inv.runs.map((run) => {
                    const segment = fitSegment(run);
                    const label = fitLabel(run);
                    if (!segment) return null;
                    const x1 = plot.x(segment.x1);
                    const x2 = plot.x(segment.x2);
                    const y1 = plot.y(Math.min(plot.yMax, Math.max(plot.yMin, segment.y1)));
                    const y2 = plot.y(Math.min(plot.yMax, Math.max(plot.yMin, segment.y2)));
                    const cap = 5 * k;
                    return (
                      <g key={`f${run.runNumber}`} class="ri-fit-group" data-fit-run={run.runNumber} data-fit-span={`${segment.x1}-${segment.x2}`} style={`--run:${runColor(run.runNumber)}`}>
                        <line x1={n(x1)} y1={n(y1)} x2={n(x2)} y2={n(y2)} class="ri-fit-halo" />
                        <line x1={n(x1)} y1={n(y1)} x2={n(x2)} y2={n(y2)} class="ri-fit" />
                        <line x1={n(x1)} x2={n(x1)} y1={n(y1 - cap)} y2={n(y1 + cap)} class="ri-fit" />
                        <line x1={n(x2)} x2={n(x2)} y1={n(y2 - cap)} y2={n(y2 + cap)} class="ri-fit" />
                        {label && <text x={n(Math.min(x2, plot.right) - 2)} y={n(y2 - cap - 4)} text-anchor="end" class="ri-fit-label" data-fit-label>{label}</text>}
                      </g>
                    );
                  })}
                </svg>
              )}
            </div>
          </>
        )}
      </div>
      {cards.length > 0 && <div class="ri-run-cards" role="list" aria-label="Runs">{cards.map((card) => <RunCardView key={card.runNumber} card={card} />)}</div>}
    </div>
  );
}

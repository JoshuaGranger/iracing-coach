import { useMemo, useRef, useState } from "preact/hooks";
import { cohortRibbon } from "./runs-model";
import { n, numberText, PlotScale, reason, runColor, runDash, signedText, ticks } from "../format";
import { hasPaceResult, paceTone, type Comparison, type Investigation, type InvestigationLap } from "../investigation-model";
import { ComparisonChips, exposureWord, lapLabel, trendPath } from "./RaceView";
import { useElementSize } from "./hooks";
import { scaleFor, useRemPx } from "./unit";

export interface RunsViewProps {
  inv: Investigation;
  selected: Comparison | null;
  sessionType?: string | null;
}

export function RunsView({ inv, selected, sessionType }: RunsViewProps) {
  const word = exposureWord(sessionType);
  const host = useRef<HTMLDivElement>(null);
  const { width, height } = useElementSize(host);
  const k = scaleFor(useRemPx());
  const [showFit, setShowFit] = useState(false);
  const [hover, setHover] = useState<InvestigationLap | null>(null);
  const points = useMemo(() => inv.laps.filter((lap) => lap.plotPace && lap.greenExposure !== null && lap.lapTimeS !== null && lap.lapTimeS > 0), [inv]);
  const chosen = useMemo(() => (selected ? [selected.baseline, selected.compared] : []), [selected]);
  const isChosen = (point: InvestigationLap): boolean => chosen.some((cohort) => cohort.lapNumbers.includes(point.lap) && (cohort.runNumber === null || cohort.runNumber === point.runNumber));
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
    <div class="ri-card ri-fill ri-runs" data-ri-view="runs" data-ri-runs>
      {inv.runs.length === 0 && points.length === 0 ? (
        <div class="ri-empty"><strong>No laps with run exposure</strong></div>
      ) : (
        <>
          <div class="ri-chart-head">
            <div class="ri-run-legend">
              {inv.runs.map((run) => (
                <span class="ri-run-key" key={run.runNumber} data-run={run.runNumber}
                  title={`Trend ${run.trend.coveredLapCount}/${run.plottedLapCount} laps${run.trend.gaps.length ? ` · breaks ${run.trend.gaps.map((gap) => `L${gap.afterLap}–${gap.beforeLap} ${gap.reason.replace(/_/g, " ")}`).join(", ")}` : ""}`}>
                  <svg width="20" height="8" aria-hidden="true"><line x1="0" y1="4" x2="20" y2="4" stroke={runColor(run.runNumber)} stroke-width="2.5" stroke-dasharray={runDash(run.runNumber)} /></svg>
                  <b>Run {run.runNumber}</b><small>L{run.firstLap}–{run.lastLap} · {run.plottedLapCount} laps</small>
                  {showFit && run.fit && run.fit.slopeSPerGreenLap !== null && <small class="ri-fit-text">fit {signedText(run.fit.slopeSPerGreenLap * 10, 2)} s / 10 {word} · {run.fit.eligibleCount} laps · {numberText(run.fit.domain?.[0], 1)}–{numberText(run.fit.domain?.[1], 1)}</small>}
                  {showFit && (!run.fit || run.fit.points.length !== 2) && <small class="ri-fit-text" data-fit-missing={run.runNumber}>no fit{run.fitUnavailableReason ? ` · ${reason(run.fitUnavailableReason)}` : ""}</small>}
                </span>
              ))}
            </div>
            <button type="button" class="ri-pill-toggle" aria-pressed={showFit} title="Least squares · screened laps · their exposure range" onClick={() => setShowFit((value) => !value)}>Screened fit</button>
            <output class="ri-readout" aria-live="polite">{readout}</output>
          </div>
          <div class="ri-plot" ref={host}>
            {plot && (
              <svg class="ri-plot-svg" viewBox={`0 0 ${n(width)} ${n(height)}`} role="group" aria-label="Lap time against green laps into run, runs overlaid">
                {ticks(plot.yMin, plot.yMax, 4).map((tick) => (
                  <g key={`y${tick}`}>
                    <line x1={n(plot.left)} x2={n(plot.right)} y1={n(plot.y(tick))} y2={n(plot.y(tick))} class="ri-grid" />
                    <text x={n(plot.left - 6)} y={n(plot.y(tick) + drop)} text-anchor="end" class="ri-axis">{tick.toFixed(1)}</text>
                  </g>
                ))}
                {ticks(0, plot.xMax, 6).map((tick) => (
                  <text key={`x${tick}`} x={n(plot.x(tick))} y={n(plot.bottom + 22 * k)} text-anchor="middle" class="ri-axis">{tick.toFixed(0)}</text>
                ))}
                <text x={n(plot.left)} y={n(Math.round(15 * k))} class="ri-axis-title">Lap time · s</text>
                <text x={n(plot.right)} y={n(plot.bottom + 42 * k)} text-anchor="end" class="ri-axis-title">{word === "clean" ? "Clean laps into run" : "Green laps into run"}</text>
                {selected && cohortRibbon(selected).map((ribbon) => (
                  <path key={ribbon.key} d={`M${n(plot.x(ribbon.start))},${n(plot.bottom + 3)}V${n(plot.bottom + 7)}H${n(plot.x(ribbon.end))}V${n(plot.bottom + 3)}`} class="ri-bracket" stroke={runColor(ribbon.run)} />
                ))}
                {inv.runs.map((run) => (
                  <g key={`r${run.runNumber}`}>
                    {run.trend.segments.map((segment, index) => (
                      <path key={index} d={trendPath(plot, segment, (point) => point.x)} class="ri-trend" stroke={runColor(run.runNumber)} stroke-dasharray={runDash(run.runNumber)} />
                    ))}
                    {showFit && run.fit && run.fit.points.length === 2 && (
                      <line x1={n(plot.x(run.fit.points[0]!.x))} y1={n(plot.y(run.fit.points[0]!.y))} x2={n(plot.x(run.fit.points[1]!.x))} y2={n(plot.y(run.fit.points[1]!.y))} class="ri-fit" stroke={runColor(run.runNumber)} />
                    )}
                  </g>
                ))}
                {points.map((point) => {
                  const color = runColor(point.runNumber);
                  const chosenPoint = isChosen(point);
                  const x = plot.x(point.greenExposure as number);
                  const y = plot.y(Math.min(plot.yMax, Math.max(plot.yMin, point.lapTimeS as number)));
                  return (
                    <g key={point.lap}>
                      {chosenPoint && <circle cx={n(x)} cy={n(y)} r="7" class="ri-ring" stroke={color} />}
                      <circle cx={n(x)} cy={n(y)} r={chosenPoint ? "4" : "2.8"} fill={point.eligible ? color : "var(--ri-card)"} stroke={color}
                        class={`ri-dot${point.eligible ? "" : " excluded"}`} tabIndex={0} data-lap={point.lap}
                        onPointerEnter={() => setHover(point)} onFocus={() => setHover(point)} aria-label={lapLabel(point, word)}><title>{lapLabel(point, word)}</title></circle>
                    </g>
                  );
                })}
              </svg>
            )}
          </div>
          {selected && (
            <div class="ri-runs-foot">
              <strong>{selected.title} <b class={paceTone(selected)}>{hasPaceResult(selected) ? `${signedText(selected.deltaS)} s median lap` : "No pace result"}</b></strong>
              <ComparisonChips cohorts={[{ cohort: selected.baseline, role: "Earlier" }, { cohort: selected.compared, role: "Later" }]} word={word} />
              <div class="ri-legend"><span><i class="ring" />Compared</span><span><i class="ld hollow" />Excl</span><span><svg width="20" height="8" aria-hidden="true"><line x1="0" y1="4" x2="20" y2="4" stroke="var(--ri-run1)" stroke-width="2.5" /></svg>5-lap median · traffic in</span></div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

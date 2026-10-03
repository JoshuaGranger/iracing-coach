import { useMemo, useRef, useState } from "preact/hooks";
import { exposureText, lapsText, lapTime, n, numberText, PlotScale, reasonsText, runColor, runDash, signedText, ticks } from "../format";
import { hasPaceResult, paceTone, type Comparison, type Investigation, type InvestigationLap, type TrendPoint } from "../investigation-model";
import { hypothesisStatus } from "../format";
import { steeringDeclaration } from "../signals";
import { useElementSize } from "./hooks";

function paceResultText(c: Comparison): string {
  return hasPaceResult(c) ? `${signedText(c.deltaS)} s median lap` : "No pace result";
}

export function lapLabel(lap: InvestigationLap, word = "green"): string {
  const parts = [`Lap ${lap.lap}`, `${lapTime(lap.lapTimeS)} s`];
  if (lap.position !== null) parts.push(`P${lap.position}`);
  if (lap.runNumber !== null) parts.push(`Run ${lap.runNumber}`);
  if (lap.greenExposure !== null) parts.push(exposureText(lap.greenExposure, word));
  parts.push(lap.eligible ? "screened" : reasonsText(lap.exclusionReasons) || "excluded");
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
            <svg width="14" height="8" aria-hidden="true"><line x1="0" y1="4" x2="14" y2="4" stroke={runColor(cohort.runNumber)} stroke-width="2.5" stroke-dasharray={runDash(cohort.runNumber)} /></svg>
            {role}
          </span>
          <b>{cohort.runNumber !== null ? `Run ${cohort.runNumber}` : "Laps"} · {cohort.lapCount} lap{cohort.lapCount === 1 ? "" : "s"} · {lapsText(cohort.lapNumbers)}</b>
          <span>{cohort.greenAgeStart !== null && cohort.greenAgeEnd !== null ? `${cohort.greenAgeStart.toFixed(1)}–${cohort.greenAgeEnd.toFixed(1)} ${word}` : "exposure unknown"}</span>
          <span>{cohort.fuelStartGal !== null ? `${cohort.fuelStartGal.toFixed(1)} gal` : "fuel —"}</span>
          <span>{cohort.trackTempF !== null ? `${cohort.trackTempF.toFixed(0)} °F track` : "track °F —"}</span>
          <span class="ri-median">{lapTime(cohort.medianLapS)} s</span>
          {cohort.excludedLaps.length > 0 && (
            <small class="ri-excluded" title={cohort.excludedLaps.map((lap) => `Lap ${lap.lap}: ${reasonsText(lap.reasons)}`).join("; ")}>
              {cohort.excludedLaps.length} excluded: {reasonsText(cohort.excludedLaps.flatMap((lap) => lap.reasons))}
            </small>
          )}
        </div>
      ))}
    </div>
  );
}

function ProgressChart({ inv, selected }: { inv: Investigation; selected: Comparison | null }) {
  const host = useRef<HTMLDivElement>(null);
  const { width, height } = useElementSize(host);
  const [hover, setHover] = useState<InvestigationLap | null>(null);
  const plotted = useMemo(() => inv.laps.filter((lap) => lap.plotPace && lap.lapTimeS !== null && lap.lapTimeS > 0), [inv]);
  const events = useMemo(() => inv.timeline.filter((item) => item.kind === "pit_service" || item.kind === "incident"), [inv]);
  const chosen = useMemo(() => new Set(selected ? [...selected.baseline.lapNumbers, ...selected.compared.lapNumbers] : []), [selected]);
  const hasPositions = inv.laps.filter((lap) => lap.position !== null && lap.position > 0).length >= 2;
  const body = (() => {
    if (inv.laps.length === 0) return <div class="ri-empty"><strong>No recorded laps</strong><span>{inv.summary}</span></div>;
    if (width < 40 || height < 40) return null;
    const minLap = Math.min(...inv.laps.map((lap) => lap.lap));
    const maxLap = Math.max(...inv.laps.map((lap) => lap.lap));
    const times = plotted.map((lap) => lap.lapTimeS as number);
    const min = times.length ? Math.min(...times) : 0;
    const max = times.length ? Math.max(...times) : 1;
    const pad = Math.max(0.08, (max - min) * 0.06);
    const bandHeight = hasPositions ? Math.min(70, Math.max(34, height * 0.18)) : 0;
    const bottomSpace = 32 + (hasPositions ? bandHeight + 18 : 0) + 18;
    const pace = new PlotScale(44, 18, Math.max(60, width - 56), Math.max(60, height - 18 - bottomSpace), minLap, Math.max(minLap + 1, maxLap), min - pad, max + pad);
    const positions = inv.laps.filter((lap) => lap.position !== null && lap.position > 0).map((lap) => lap.position as number);
    const posScale = hasPositions ? new PlotScale(pace.left, pace.bottom + 44, pace.width, bandHeight, pace.xMin, pace.xMax, 1, Math.max(2, ...positions)) : null;
    const lapWidth = pace.width / Math.max(1, pace.xMax - pace.xMin);
    const positionPath = posScale
      ? inv.laps
          .filter((lap) => lap.position !== null && lap.position > 0)
          .map((lap, index, all) => {
            const y = posScale.top + ((lap.position as number) - posScale.yMin) / Math.max(1, posScale.yMax - posScale.yMin) * posScale.height;
            const move = index === 0 || lap.lap !== all[index - 1]!.lap + 1;
            return `${move ? "M" : "L"}${n(posScale.x(lap.lap))},${n(y)}`;
          })
          .join(" ")
      : "";
    return (
      <svg class="ri-plot-svg" viewBox={`0 0 ${n(width)} ${n(height)}`} role="group" aria-label="Race progression: lap time, run trend, cautions, service, incidents and position by lap">
        {ticks(pace.yMin, pace.yMax, 4).map((tick) => (
          <g key={tick}>
            <line x1={n(pace.left)} x2={n(pace.right)} y1={n(pace.y(tick))} y2={n(pace.y(tick))} class="ri-grid" />
            <text x={n(pace.left - 6)} y={n(pace.y(tick) + 4)} text-anchor="end" class="ri-axis">{tick.toFixed(1)}</text>
          </g>
        ))}
        <text x={n(pace.left)} y="12" class="ri-axis-title">Lap time · s</text>
        {inv.laps.filter((lap) => lap.flagState === "caution").map((lap) => (
          <rect key={`c${lap.lap}`} x={n(pace.x(lap.lap) - lapWidth / 2)} y={n(pace.bottom + 2)} width={n(Math.max(1.5, lapWidth))} height="5" class="ri-caution"><title>{`Lap ${lap.lap} · caution`}</title></rect>
        ))}
        {events.map((item) => {
          const x = pace.x(item.lap);
          const cls = item.kind === "incident" ? "incident" : "service";
          return (
            <g key={`${item.kind}${item.lap}`}>
              <line x1={n(x)} x2={n(x)} y1={n(pace.top)} y2={n(pace.bottom)} class={`ri-event ${cls}`} />
              <text x={n(x + 3)} y={n(pace.top + 10)} class={`ri-event-label ${cls}`}>{item.kind === "incident" ? (item.value !== null ? `+${item.value.toFixed(0)}x` : "Inc") : "Pit"}<title>{`Lap ${item.lap} · ${item.label} · ${item.detail}`}</title></text>
            </g>
          );
        })}
        {selected && [selected.baseline, selected.compared].map((cohort) => {
          if (cohort.lapNumbers.length === 0) return null;
          const start = pace.x(Math.min(...cohort.lapNumbers)) - 4;
          const end = pace.x(Math.max(...cohort.lapNumbers)) + 4;
          return (
            <g key={cohort.label + cohort.lapNumbers[0]}>
              <path d={`M${n(start)},${n(pace.bottom + 12)}V${n(pace.bottom + 16)}H${n(end)}V${n(pace.bottom + 12)}`} class="ri-bracket" stroke={runColor(cohort.runNumber)} />
              <text x={n((start + end) / 2)} y={n(pace.bottom + 28)} text-anchor="middle" class="ri-bracket-label">{lapsText(cohort.lapNumbers)}</text>
            </g>
          );
        })}
        {inv.runs.map((run) => run.trend.segments.map((segment, index) => (
          <path key={`t${run.runNumber}-${index}`} d={trendPath(pace, segment, (point) => point.lap)} class="ri-trend" stroke={runColor(run.runNumber)} />
        )))}
        {plotted.map((lap) => {
          const color = runColor(lap.runNumber);
          const isChosen = chosen.has(lap.lap);
          const x = pace.x(lap.lap);
          const y = pace.y(Math.min(pace.yMax, Math.max(pace.yMin, lap.lapTimeS as number)));
          return (
            <g key={lap.lap}>
              {isChosen && <circle cx={n(x)} cy={n(y)} r="6.5" class="ri-ring" stroke={color} />}
              <circle
                cx={n(x)} cy={n(y)} r={isChosen ? "3.6" : "2.5"}
                fill={lap.eligible ? color : "var(--color-surface1)"} stroke={color}
                class={`ri-dot${lap.eligible ? "" : " excluded"}`} tabIndex={0} data-lap={lap.lap}
                onPointerEnter={() => setHover(lap)} onFocus={() => setHover(lap)} aria-label={lapLabel(lap)}
              ><title>{lapLabel(lap)}</title></circle>
            </g>
          );
        })}
        {posScale && (
          <g>
            <text x={n(posScale.left)} y={n(posScale.top - 5)} class="ri-axis-title">Position</text>
            <line x1={n(posScale.left)} x2={n(posScale.right)} y1={n(posScale.top)} y2={n(posScale.top)} class="ri-grid" />
            <line x1={n(posScale.left)} x2={n(posScale.right)} y1={n(posScale.bottom)} y2={n(posScale.bottom)} class="ri-grid" />
            <text x={n(posScale.left - 6)} y={n(posScale.top + 4)} text-anchor="end" class="ri-axis">P1</text>
            <text x={n(posScale.left - 6)} y={n(posScale.bottom + 4)} text-anchor="end" class="ri-axis">{`P${posScale.yMax.toFixed(0)}`}</text>
            <path d={positionPath} class="ri-position" />
          </g>
        )}
        {ticks(minLap, maxLap, 8).map((tick) => (
          <text key={`x${tick}`} x={n(pace.x(Math.round(tick)))} y={n(height - 4)} text-anchor="middle" class="ri-axis">{Math.round(tick)}</text>
        ))}
        <text x="4" y={n(height - 4)} class="ri-axis-title">Lap</text>
      </svg>
    );
  })();
  const readout = hover ? lapLabel(hover) : selected ? `${selected.title}: ${paceResultText(selected)}` : "Point to a lap for its time, position and screening";
  return (
    <div class="ri-progress" data-ri-progress>
      <div class="ri-chart-head">
        <div class="ri-legend">
          <span><i class="dot" />Screened lap</span><span><i class="dot hollow" />Excluded lap</span>
          <span><i class="line" />Run trend</span><span><i class="caution" />Caution</span>
          <span><i class="service" />Service</span><span><i class="incident" />Incident</span>
        </div>
        <output class="ri-readout" aria-live="polite">{readout}</output>
      </div>
      <div class="ri-plot" ref={host}>{body}</div>
    </div>
  );
}

const CHANGE_THRESHOLDS: Record<string, number> = { brake: 1, throttle: 2, steering: 2, corrections: 1, fuel: 0.5, track_temperature: 1, track_usage: 0.1, brake_bias: 0.1, traffic: 1 };

function changes(c: Comparison, steeringRatio: number | null, roadWheel: boolean): Comparison["metrics"] {
  return c.metrics
    .filter((metric) => {
      const threshold = CHANGE_THRESHOLDS[metric.key];
      if (metric.delta === null || threshold === undefined) return false;
      // The steering threshold is steering-wheel degrees; on road-wheel steering it converts as the backend does.
      const limit = metric.key === "steering" && roadWheel && steeringRatio ? threshold / steeringRatio : threshold;
      return Math.abs(metric.delta) >= limit;
    })
    .slice(0, 5);
}

export interface RaceViewProps {
  inv: Investigation;
  selected: Comparison | null;
  onSelectComparison(id: string): void;
  onInspect(action: Comparison["actions"][number]): void;
}

export function RaceView({ inv, selected, onSelectComparison, onInspect }: RaceViewProps) {
  const steering = useMemo(() => steeringDeclaration(inv.steeringBasis), [inv]);
  const lead = selected?.actions.slice().sort((a, b) => a.priority - b.priority)[0] ?? null;
  const changed = selected ? changes(selected, steering.ratio, steering.roadWheel) : [];
  return (
    <div class="ri-race-view" data-ri-view="race">
      <div class="ri-panel ri-fill"><ProgressChart inv={inv} selected={selected} /></div>
      <div class="ri-race-bottom">
        <nav class="ri-panel ri-comparisons" aria-label="Pace comparisons">
          <div class="ri-panel-label"><strong>Comparisons</strong><span>Later − earlier, median</span></div>
          {inv.comparisons.map((choice) => {
            const isSelected = choice.id === selected?.id;
            return (
              <button
                type="button" key={choice.id} class={`ri-comparison${isSelected ? " selected" : ""}`} aria-pressed={isSelected}
                data-comparison={choice.id} title={choice.paceComparable ? undefined : choice.summary} onClick={() => onSelectComparison(choice.id)}
              >
                <span class="ri-comparison-title">{choice.title}</span>
                <span class="ri-comparison-laps">
                  {lapsText(choice.baseline.lapNumbers)} → {lapsText(choice.compared.lapNumbers)}
                  {!choice.paceComparable && <em> · no pace result</em>}
                  {choice.paceComparable && choice.eligibility !== "eligible" && <em> · {choice.baseline.excludedLaps.length + choice.compared.excludedLaps.length} of {choice.baseline.lapCount + choice.compared.lapCount} laps excluded</em>}
                </span>
                <strong class={paceTone(choice)}>{hasPaceResult(choice) ? signedText(choice.deltaS) : "—"}</strong>
              </button>
            );
          })}
          {inv.comparisons.length === 0 && <p class="ri-muted">{inv.summary}</p>}
        </nav>
        {selected && (
          <section class="ri-panel ri-card" data-selected-comparison={selected.id}>
            <header><h2>{selected.title}</h2><strong class={paceTone(selected)}>{paceResultText(selected)}</strong></header>
            {!selected.paceComparable && <p class="ri-unusable" data-pace-unusable>{selected.summary}</p>}
            <ComparisonChips cohorts={[{ cohort: selected.baseline, role: "Earlier" }, { cohort: selected.compared, role: "Later" }]} word="green" />
            {changed.length > 0 && (
              <div class="ri-changes"><span>Changed</span>{changed.map((metric) => <b key={metric.key} title={metric.detail ?? undefined}>{metric.label} {numberText(metric.baseline, 1)} → {numberText(metric.compared, 1)} {metric.unit}</b>)}</div>
            )}
            {selected.paceComparable && selected.hypotheses.length > 0 && (
              <div class="ri-explanations">
                <span>Explanations</span>
                {selected.hypotheses.map((h) => (
                  <b key={h.key} class={`ri-hypothesis ${h.status}`} title={[...h.supporting.map((t) => `+ ${t}`), ...h.contradicting.map((t) => `− ${t}`), ...h.unknown.map((t) => `? ${t}`)].join("\n")}>
                    {h.label}<small>{hypothesisStatus(h.status)}</small>
                  </b>
                ))}
              </div>
            )}
            {lead && (
              <div class="ri-action">
                <div><strong>{lead.title}</strong><p>{lead.instruction}</p>{lead.limitation && <small class="ri-muted">{lead.limitation}</small>}</div>
                <button type="button" class="ri-button" onClick={() => onInspect(lead)}>Inspect</button>
              </div>
            )}
          </section>
        )}
      </div>
    </div>
  );
}

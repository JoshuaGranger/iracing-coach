import { lapTime, n, numberText, reasonsText, runColor } from "../format";
import { hasPaceResult, paceTone, type Comparison, type Investigation } from "../investigation-model";
import { comparisonLaps, comparisonName, deltaText, direction, dotDomain, groupRows, noResultLine, pairedMetrics, rangesText, type GroupRow, type PairedMetric } from "./comparison-model";

/**
 * The selected comparison drawn (web round 2): Earlier and Later as two rows of lap dots on one lap-time scale with the backend's
 * median marks, the backend's signed difference large with its direction, green/total counts, and paired bars for starting fuel,
 * average throttle and close-traffic exposure, each on its own scale from zero. No sentence; with no pace result, one line.
 */

const pct = (value: number, lo: number, hi: number): number => ((value - lo) / Math.max(1e-9, hi - lo)) * 100;

function DotRows({ rows }: { rows: GroupRow[] }) {
  const domain = dotDomain(rows);
  if (!domain) return null;
  const [lo, hi] = domain;
  const mid = (lo + hi) / 2;
  return (
    <div class="ri-dotrows" role="group" aria-label="Lap times by group">
      {rows.map((row) => {
        const median = row.cohort.medianLapS;
        const color = runColor(row.cohort.runNumber);
        return (
          <div class="ri-dotrow" key={row.role} data-group={row.role} style={`--grp:${color}`}>
            <span class="ri-dotrow-name"><b>{row.role === "earlier" ? "Earlier" : "Later"}</b><small>{rangesText(row.cohort.lapNumbers)}</small></span>
            <svg class="ri-dotrow-track" aria-hidden="true">
              <line x1="0" x2="100%" y1="50%" y2="50%" class="ri-dotrow-rule" />
              {row.dots.map((dot) => (
                <circle key={dot.lap} cx={`${n(pct(dot.lapTimeS, lo, hi))}%`} cy="50%" r="5" class={`ri-gdot${dot.used ? "" : " hollow"}`} data-lap={dot.lap}>
                  <title>{`L${dot.lap} · ${lapTime(dot.lapTimeS)} s${dot.used ? "" : ` · Not used${dot.reasons.length ? ` · ${reasonsText(dot.reasons)}` : ""}`}`}</title>
                </circle>
              ))}
              {median !== null && <line class="ri-median-mark" data-median={median} x1={`${n(pct(median, lo, hi))}%`} x2={`${n(pct(median, lo, hi))}%`} y1="8%" y2="92%" />}
            </svg>
            <span class="ri-dotrow-fig">
              <b data-median-text>{lapTime(median)}</b>
              <small data-counts>{row.green}/{row.total} green</small>
            </span>
          </div>
        );
      })}
      <div class="ri-dotrow-axis" aria-hidden="true">
        <span style="left:0">{lo.toFixed(2)}</span>
        <span style="left:50%" class="mid">{mid.toFixed(2)} s</span>
        <span style="left:100%" class="end">{hi.toFixed(2)}</span>
      </div>
    </div>
  );
}

export function PairedBars({ metric, compact = false }: { metric: PairedMetric; compact?: boolean }) {
  const width = (value: number | null): string => (value === null ? "0" : n(Math.max(0, Math.min(100, (value / metric.max) * 100))));
  const value = (v: number | null): string => numberText(v, metric.digits);
  return (
    <div class={`ri-pbar ${metric.key}${compact ? " compact" : ""}`} data-metric={metric.key} role="img"
      aria-label={`${metric.label} · Earlier ${value(metric.earlier)} · Later ${value(metric.later)} ${metric.unit}`}>
      <span class="ri-pbar-title">{compact ? metric.short : metric.label}<small>{metric.unit}</small></span>
      <span class="ri-pbar-row earlier"><i class="ri-pbar-role">E</i><span class="ri-pbar-track"><i style={`width:${width(metric.earlier)}%`} /></span><b data-side="earlier">{value(metric.earlier)}</b></span>
      <span class="ri-pbar-row later"><i class="ri-pbar-role">L</i><span class="ri-pbar-track"><i style={`width:${width(metric.later)}%`} /></span><b data-side="later">{value(metric.later)}</b></span>
      {!compact && <span class="ri-pbar-scale" aria-hidden="true"><span>0</span><span>{numberText(metric.max, 0)}</span></span>}
    </div>
  );
}

function Delta({ comparison, compact = false }: { comparison: Comparison; compact?: boolean }) {
  const dir = direction(comparison);
  return (
    <span class={`ri-delta ${paceTone(comparison)}${compact ? " compact" : ""}`} data-delta={comparison.deltaS ?? ""} data-direction={dir ?? ""}>
      <b>{deltaText(comparison)}</b><small>s</small>
      {dir && <span class="ri-direction">{dir === "slower" ? "▲ Later slower" : dir === "faster" ? "▼ Later faster" : "Equal"}</span>}
    </span>
  );
}

export interface ComparisonPanelProps {
  inv: Investigation;
  comparison: Comparison;
  action?: { label: string; run(): void } | null;
}

export function ComparisonPanel({ inv, comparison, action }: ComparisonPanelProps) {
  const laps = comparisonLaps(comparison);
  if (!hasPaceResult(comparison)) {
    return (
      <section class="ri-card ri-detail" data-selected-comparison={comparison.id}>
        <header><h2>{comparisonName(comparison)}</h2></header>
        <p class="ri-noresult" data-pace-unusable>{noResultLine(comparison)}</p>
      </section>
    );
  }
  const rows = groupRows(comparison, inv.laps);
  const metrics = pairedMetrics(comparison);
  return (
    <section class="ri-card ri-detail" data-selected-comparison={comparison.id}>
      <header>
        <h2>{comparisonName(comparison)}{laps && <small>{laps}</small>}</h2>
        <Delta comparison={comparison} />
      </header>
      <div class="ri-picture">
        <DotRows rows={rows} />
        {metrics.length > 0 && <div class="ri-pbars">{metrics.map((metric) => <PairedBars key={metric.key} metric={metric} />)}</div>}
      </div>
      {action && (
        <div class="ri-action">
          <button type="button" class="ri-pill-button primary" onClick={action.run}>{action.label}</button>
        </div>
      )}
    </section>
  );
}

/** The comparison context in Traces: the name, the difference with its direction, the paired bars, and the rail-mark key. */
export function ComparisonStrip({ comparison }: { comparison: Comparison }) {
  const color = (run: number | null) => runColor(run);
  return (
    <div class="ri-card ri-cmp-strip" data-comparison-strip={comparison.id} role="group" aria-label="Comparison">
      <span class="ri-cmp-name">
        <b>{comparisonName(comparison)}</b>
        <span class="ri-cmp-key">
          <i class="earlier" style={`--grp:${color(comparison.baseline.runNumber)}`} />Earlier
          <i class="later" style={`--grp:${color(comparison.compared.runNumber)}`} />Later
        </span>
      </span>
      {hasPaceResult(comparison) ? (
        <>
          <Delta comparison={comparison} compact />
          <span class="ri-cmp-bars">{pairedMetrics(comparison).map((metric) => <PairedBars key={metric.key} metric={metric} compact />)}</span>
        </>
      ) : (
        <span class="ri-noresult" data-pace-unusable>{noResultLine(comparison)}</span>
      )}
    </div>
  );
}

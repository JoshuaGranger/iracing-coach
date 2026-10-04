import { n, numberText, runColor } from "../format";
import { hasPaceResult, paceTone, type Comparison } from "../investigation-model";
import { comparisonName, deltaText, direction, noResultLine, pairedMetrics, type PairedMetric } from "./comparison-model";

/**
 * The selected comparison in Traces (round 3: it left the Race tab, #43 #45): the name, the backend's signed difference with its
 * direction, and paired bars for starting fuel, average throttle and close-traffic exposure, each on its own scale from zero. No
 * sentence; with no pace result, one line.
 */

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

import { useMemo } from "preact/hooks";
import { lapTime, signedText } from "../format";
import { isCleanLap, isGreenLap, lapColor, type TraceData } from "../geometry/trace-model";
import type { TraceLapData } from "../types";

export interface LapRailProps {
  data: TraceData;
  selected: ReadonlySet<number>;
  greenOnly: boolean;
  cleanOnly: boolean;
  incidentPoints: ReadonlyMap<number, number>;
  runMetrics: ReadonlyMap<number, { green: number | null; caution: number | null }>;
  onToggle(lap: number): void;
  onGreenOnly(value: boolean): void;
  onCleanOnly(value: boolean): void;
  onFastest(): void;
  onBestThree(): void;
  onClear(): void;
  onShowAll(): void;
}

export function passesFilters(meta: TraceLapData, greenOnly: boolean, cleanOnly: boolean, incidents: number): boolean {
  return (!cleanOnly || isCleanLap(meta, incidents)) && (!greenOnly || isGreenLap(meta));
}

function conditionsTitle(meta: TraceLapData): string {
  const c = meta.conditions as Record<string, unknown> | null | undefined;
  if (!c) return "Lap time";
  const parts: string[] = [];
  if (typeof c.track_temperature_f === "number") parts.push(`${c.track_temperature_f.toFixed(0)} °F track`);
  if (typeof c.air_temperature_f === "number") parts.push(`${c.air_temperature_f.toFixed(0)} °F air`);
  if (typeof c.wind_speed_mph === "number") parts.push(`${c.wind_speed_mph.toFixed(0)} mph wind`);
  if (typeof c.relative_humidity_percent === "number") parts.push(`${c.relative_humidity_percent.toFixed(0)}% humidity`);
  return parts.length ? parts.join(" · ") : "Lap time";
}

export function LapRail(props: LapRailProps) {
  const { data, selected, incidentPoints } = props;
  const groups = useMemo(() => {
    const byRun = new Map<number | null, TraceLapData[]>();
    for (const lap of data.laps.slice().sort((a, b) => a - b)) {
      const meta = data.lapMeta.get(lap);
      if (!meta || !passesFilters(meta, props.greenOnly, props.cleanOnly, incidentPoints.get(lap) ?? 0)) continue;
      const run = typeof meta.run_number === "number" ? meta.run_number : null;
      const list = byRun.get(run) ?? [];
      list.push(meta);
      byRun.set(run, list);
    }
    return [...byRun.entries()].sort((a, b) => (a[1][0]!.lap - b[1][0]!.lap));
  }, [data, props.greenOnly, props.cleanOnly, incidentPoints]);
  const fastest = data.fastestLapTime;
  return (
    <aside class="ri-panel ri-lap-rail" aria-label="Laps and runs">
      <div class="ri-lap-head">
        <div class="ri-heading"><strong>Laps &amp; runs</strong><span>{selected.size} selected</span></div>
        <div class="ri-filters" aria-label="Lap filters">
          <label><input type="checkbox" checked={props.greenOnly} onChange={(event) => props.onGreenOnly((event.currentTarget as HTMLInputElement).checked)} /> Green</label>
          <label><input type="checkbox" checked={props.cleanOnly} onChange={(event) => props.onCleanOnly((event.currentTarget as HTMLInputElement).checked)} /> Clean</label>
        </div>
      </div>
      <div class="ri-lap-scroll">
        {groups.map(([run, laps]) => {
          const metrics = run !== null ? props.runMetrics.get(run) : undefined;
          return (
            <section class="ri-lap-group" key={String(run)}>
              <header>
                <span class="ri-run-name">{run !== null ? `Run ${run}` : "Recorded laps"}</span>
                {metrics ? <span class="ri-run-metrics">{metrics.green ?? "—"} green · {metrics.caution ?? "—"} caution</span> : <small>{laps.length} laps</small>}
              </header>
              {laps.map((meta) => {
                const isSelected = selected.has(meta.lap);
                const isFastest = typeof meta.lap_time_s === "number" && Math.abs(meta.lap_time_s - fastest) < 0.0001;
                const incidents = incidentPoints.get(meta.lap) ?? 0;
                const pit = typeof meta.pit_time_s === "number" && meta.pit_time_s > 0;
                const caution = !isGreenLap(meta);
                const fuel = typeof meta.fuel_used_gal === "number" ? meta.fuel_used_gal.toFixed(2) : "—";
                return (
                  <button
                    type="button" key={meta.lap} class={`ri-lap-row${isSelected ? " selected" : ""}${pit ? " pit-lap" : ""}`} style={`--lap-color:${lapColor(data, meta.lap)}`}
                    aria-pressed={isSelected} aria-label={`Lap ${meta.lap}, ${lapTime(meta.lap_time_s)}`} data-lap={meta.lap} onClick={() => props.onToggle(meta.lap)}
                  >
                    <span class="ri-lap-id"><b>{meta.lap}</b><i class="ri-lap-swatch" aria-hidden="true" /></span>
                    <span class={`ri-lap-time${isFastest ? " fastest" : ""}`} title={conditionsTitle(meta)}>
                      {lapTime(meta.lap_time_s)}
                      {!isFastest && typeof meta.lap_time_s === "number" && Number.isFinite(fastest) && <small>{signedText(meta.lap_time_s - fastest)}</small>}
                    </span>
                    <span class="ri-lap-fuel" title="Fuel used (gal)">{fuel}</span>
                    <span class="ri-lap-flags">
                      {caution && <em class="flag caution" title="Caution or yellow flag">C</em>}
                      {pit && <em class="flag pit" title="Pit lap">Pit</em>}
                      {incidents > 0 && <em class="flag incident" title={`${incidents} incident point${incidents === 1 ? "" : "s"}`}>x{incidents}</em>}
                    </span>
                  </button>
                );
              })}
            </section>
          );
        })}
        {groups.length === 0 && <p class="ri-muted ri-pad">No laps match the filters.</p>}
      </div>
      <div class="ri-lap-foot">
        <button type="button" class="ri-text-button" onClick={props.onFastest}>Fastest</button>
        <button type="button" class="ri-text-button" onClick={props.onBestThree}>Best three</button>
        <button type="button" class="ri-text-button" onClick={props.onClear}>Clear</button>
        <button type="button" class="ri-text-button" onClick={props.onShowAll}>Show all</button>
      </div>
    </aside>
  );
}

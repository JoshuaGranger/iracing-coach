import { fieldTime, sampledTime, timeSourceLabel, type FieldCar } from "./field-results";
import "./field-results.css";

/** Competitor progression uses only recorded lap times, positions, and pit-road entries. */
export interface FieldDomain { firstLap: number; lastLap: number; minTime: number; maxTime: number; maxPosition: number }
export function FieldRaceView({ car, domain, cursorLap, onCursor, chartWidth = 1000 }: { car: FieldCar; domain?: FieldDomain; chartWidth?: number; cursorLap?: number | null; onCursor?(lap: number | null): void }) {
  const timed = car.laps.filter((lap) => lap.time !== null && lap.time > 0);
  const positioned = car.laps.filter((lap) => lap.position !== null && lap.position > 0);
  const maxLap = domain?.lastLap ?? Math.max(1, ...car.laps.map((lap) => lap.lap));
  const firstLap = domain?.firstLap ?? 0;
  const minTime = domain?.minTime ?? Math.min(...timed.map((lap) => lap.time!));
  const maxTime = domain?.maxTime ?? Math.max(...timed.map((lap) => lap.time!));
  const maxPosition = domain?.maxPosition ?? Math.max(2, ...positioned.map((lap) => lap.position!));
  const paceHeight = domain ? 120 : 220;
  const positionTop = domain ? 175 : 305;
  const positionHeight = domain ? 50 : 100;
  const chartBottom = positionTop + positionHeight;
  const chartRight = chartWidth - 50;
  const x = (lap: number) => 60 + (lap - firstLap) / Math.max(1, maxLap - firstLap) * (chartWidth - 110);
  const y = (time: number) => 32 + (time - minTime) / Math.max(.1, maxTime - minTime) * paceHeight;
  const positionY = (position: number) => positionTop + (position - 1) / (maxPosition - 1) * positionHeight;
  return <div class="ri-field-race" data-ri-view="field-race">
    <div class="ri-chart-head"><strong>{car.name} · #{car.number || "—"}</strong><span title="Solid dots: green pace laps. Hollow dots: screened laps. Tire service and fuel are unavailable for competitors.">Recorded P{car.position ?? "—"} · {car.completed ?? "—"} laps · {car.pits ?? "—"} pit visits</span></div>
    {car.laps.length === 0 ? <div class="ri-empty">{car.progressionUnavailableReason === "payload_budget" ? "Lap progression unavailable for this oversized recording" : "No competitor lap progression recorded"}</div> : <div class="ri-field-chart-scroll"><svg onPointerMove={(event) => { const rect = event.currentTarget.getBoundingClientRect(); const fraction = Math.max(0, Math.min(1, (event.clientX - rect.left - rect.width * (60 / chartWidth)) / (rect.width * ((chartWidth - 110) / chartWidth)))); onCursor?.(Math.round(firstLap + fraction * (maxLap - firstLap))); }} onPointerLeave={() => onCursor?.(null)} viewBox={`0 0 ${chartWidth} ${domain ? 260 : 450}`} preserveAspectRatio={domain ? "none" : "xMidYMid meet"} class="ri-field-chart" role="img" aria-label={`Recorded lap times and positions for ${car.name}`}>
      <text x="60" y="20" class="ri-axis-title">Lap time · s</text>
      {timed.length > 0 && [minTime, maxTime].map((value, i) => <g key={i}><line x1="60" x2={chartRight} y1={y(value)} y2={y(value)} class="ri-grid" /><text x="52" y={y(value) + 5} text-anchor="end" class="ri-axis">{value.toFixed(1)}</text></g>)}
      {car.pitEvents.filter((pit) => pit.lap !== null && pit.lap >= firstLap && pit.lap <= maxLap).map((pit, i) => <g key={i} data-field-pit={pit.lap}><line x1={x(pit.lap!)} x2={x(pit.lap!)} y1="32" y2={chartBottom} class="ri-field-pit-line" /><text x={x(pit.lap!)} y={positionTop - 15} text-anchor="middle" class="ri-axis">Pit L{pit.lap}</text></g>)}
      {timed.filter((lap) => lap.lap >= firstLap && lap.lap <= maxLap).map((lap) => <circle key={lap.lap} cx={x(lap.lap)} cy={y(lap.time!)} r="4" class={`ri-field-lap ${lap.eligible ? "green" : "screened"}`} data-field-lap={lap.lap}><title>L{lap.lap} · {fieldTime(lap.time, sampledTime(lap.timeSource, car.player))} s · {timeSourceLabel(lap.timeSource, car.player)} · {lap.flag}{lap.pit ? " · pit road" : ""}</title></circle>)}
      <text x="60" y={positionTop - 5} class="ri-axis-title">Position · P1 at top</text>
      <line x1="60" x2={chartRight} y1={positionTop} y2={positionTop} class="ri-grid" />
      <text x="52" y={positionTop + 5} text-anchor="end" class="ri-axis">1</text><text x="52" y={chartBottom + 5} text-anchor="end" class="ri-axis">{maxPosition}</text>
      {positioned.filter((lap) => lap.lap >= firstLap && lap.lap <= maxLap).map((lap) => <circle key={lap.lap} cx={x(lap.lap)} cy={positionY(lap.position!)} r="3" class="ri-field-position"><title>L{lap.lap} · P{lap.position}</title></circle>)}
      {cursorLap !== null && cursorLap !== undefined && <line x1={x(cursorLap)} x2={x(cursorLap)} y1="32" y2={chartBottom} class="ri-field-cursor" />}
      {[firstLap, Math.round((firstLap + maxLap) / 2), maxLap].map((lap, i) => <text key={i} x={x(lap)} y={chartBottom + 20} text-anchor="middle" class="ri-axis">{lap}</text>)}
      <text x={chartRight} y={chartBottom + 34} text-anchor="end" class="ri-axis-title">Lap</text>
    </svg></div>}
  </div>;
}

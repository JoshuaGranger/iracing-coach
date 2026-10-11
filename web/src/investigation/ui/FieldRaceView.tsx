import { fieldTime, sampledTime, timeSourceLabel, type FieldCar } from "./field-results";
import "./field-results.css";

/** Competitor progression uses only recorded lap times, positions, and pit-road entries. */
export function FieldRaceView({ car }: { car: FieldCar }) {
  const timed = car.laps.filter((lap) => lap.time !== null && lap.time > 0);
  const positioned = car.laps.filter((lap) => lap.position !== null && lap.position > 0);
  const maxLap = Math.max(1, ...car.laps.map((lap) => lap.lap));
  const minTime = Math.min(...timed.map((lap) => lap.time!));
  const maxTime = Math.max(...timed.map((lap) => lap.time!));
  const maxPosition = Math.max(2, ...positioned.map((lap) => lap.position!));
  const x = (lap: number) => 60 + lap / maxLap * 890;
  const y = (time: number) => 32 + (maxTime - time) / Math.max(.1, maxTime - minTime) * 220;
  const positionY = (position: number) => 305 + (position - 1) / (maxPosition - 1) * 100;
  return <div class="ri-field-race" data-ri-view="field-race">
    <div class="ri-chart-head"><strong>{car.name} · #{car.number || "—"}</strong><span>Recorded P{car.position ?? "—"} · {car.completed ?? "—"} laps · {car.pits ?? "—"} pit visits</span></div>
    {car.laps.length === 0 ? <div class="ri-empty">{car.progressionUnavailableReason === "payload_budget" ? "Lap progression unavailable for this oversized recording" : "No competitor lap progression recorded"}</div> : <div class="ri-field-chart-scroll"><svg viewBox="0 0 1000 450" class="ri-field-chart" role="img" aria-label={`Recorded lap times and positions for ${car.name}`}>
      <text x="60" y="20" class="ri-axis-title">Lap time · s</text>
      {timed.length > 0 && [minTime, maxTime].map((value, i) => <g key={i}><line x1="60" x2="950" y1={y(value)} y2={y(value)} class="ri-grid" /><text x="52" y={y(value) + 5} text-anchor="end" class="ri-axis">{value.toFixed(1)}</text></g>)}
      {car.pitEvents.filter((pit) => pit.lap !== null).map((pit, i) => <g key={i} data-field-pit={pit.lap}><line x1={x(pit.lap!)} x2={x(pit.lap!)} y1="32" y2="405" class="ri-field-pit-line" /><text x={x(pit.lap!)} y="280" text-anchor="middle" class="ri-axis">Pit L{pit.lap}</text></g>)}
      {timed.map((lap) => <circle key={lap.lap} cx={x(lap.lap)} cy={y(lap.time!)} r="4" class={`ri-field-lap ${lap.eligible ? "green" : "screened"}`} data-field-lap={lap.lap}><title>L{lap.lap} · {fieldTime(lap.time, sampledTime(lap.timeSource, car.player))} s · {timeSourceLabel(lap.timeSource, car.player)} · {lap.flag}{lap.pit ? " · pit road" : ""}</title></circle>)}
      <text x="60" y="300" class="ri-axis-title">Position · P1 at top</text>
      <line x1="60" x2="950" y1="305" y2="305" class="ri-grid" />
      <text x="52" y="310" text-anchor="end" class="ri-axis">1</text><text x="52" y="410" text-anchor="end" class="ri-axis">{maxPosition}</text>
      {positioned.map((lap) => <circle key={lap.lap} cx={x(lap.lap)} cy={positionY(lap.position!)} r="3" class="ri-field-position"><title>L{lap.lap} · P{lap.position}</title></circle>)}
      {[0, Math.round(maxLap / 2), maxLap].map((lap, i) => <text key={i} x={x(lap)} y="435" text-anchor="middle" class="ri-axis">{lap}</text>)}
      <text x="950" y="449" text-anchor="end" class="ri-axis-title">Lap</text>
    </svg></div>}
    <p class="ri-field-note">Solid dots: recorded green pace laps. Hollow dots: other recorded laps. ≈ sampled time, not scored lap time. Pit marks: observed pit-road entries. Tire service and fuel are unavailable for competitors.</p>
  </div>;
}

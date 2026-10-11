import { useMemo, useRef, useState } from "preact/hooks";
import { FieldRaceView, type FieldDomain } from "./FieldRaceView";
import { useElementSize } from "./hooks";
import { fieldTime, sampledTime, type FieldCar } from "./field-results";

export function comparisonDomain(cars: readonly FieldCar[]): FieldDomain {
  const laps = cars.flatMap((car) => car.laps);
  const times = laps.flatMap((lap) => lap.time !== null && lap.time > 0 ? [lap.time] : []);
  const min = times.length ? Math.min(...times) : 0;
  const max = times.length ? Math.max(...times) : 1;
  return { firstLap: 0, lastLap: Math.max(1, ...laps.map((lap) => lap.lap)), minTime: min - .1, maxTime: max + .1, maxPosition: Math.max(2, ...laps.map((lap) => lap.position ?? 1)) };
}

/** One shared lap window, time scale and cursor for the two recorded cars. */
export function RaceComparisonView({ player, competitor }: { player: FieldCar; competitor: FieldCar }) {
  const host = useRef<HTMLDivElement>(null);
  const { width } = useElementSize(host);
  const full = useMemo(() => comparisonDomain([player, competitor]), [player, competitor]);
  const [window, setWindow] = useState<{ first: number; last: number } | null>(null);
  const [cursor, setCursor] = useState<number | null>(null);
  const domain = { ...full, firstLap: window?.first ?? full.firstLap, lastLap: window?.last ?? full.lastLap };
  const zoom = (factor: number) => {
    const span = domain.lastLap - domain.firstLap;
    const next = Math.min(full.lastLap, Math.max(Math.min(4, full.lastLap), span * factor));
    const center = cursor ?? (domain.firstLap + domain.lastLap) / 2;
    const first = Math.max(0, Math.min(full.lastLap - next, center - next / 2));
    setWindow({ first, last: first + next });
  };
  return <div ref={host} class="ri-race-comparison" data-ri-view="race-comparison" onWheel={(event) => { event.preventDefault(); zoom(event.deltaY > 0 ? 1.25 : .8); }}>
    <div class="ri-comparison-readout"><output aria-live="polite">{cursor === null ? "Lap comparison" : `L${cursor} · ${[player, competitor].map((car) => { const lap = car.laps.find((item) => item.lap === cursor); return `${car.player ? "You" : car.name}: ${fieldTime(lap?.time ?? null, sampledTime(lap?.timeSource ?? null, car.player))} s${lap?.position ? ` · P${lap.position}` : ""}`; }).join(" / ")}`}</output><div class="ri-comparison-zoom"><button type="button" class="ri-button" aria-label="Zoom in both charts" onClick={() => zoom(.8)}>+</button><button type="button" class="ri-button" aria-label="Zoom out both charts" onClick={() => zoom(1.25)}>−</button><button type="button" class="ri-button" onClick={() => setWindow(null)} title="Restore all laps">Reset zoom</button></div></div>
    <FieldRaceView car={player} domain={domain} chartWidth={Math.max(320, width - 2)} cursorLap={cursor} onCursor={setCursor} />
    <FieldRaceView car={competitor} domain={domain} chartWidth={Math.max(320, width - 2)} cursorLap={cursor} onCursor={setCursor} />
  </div>;
}

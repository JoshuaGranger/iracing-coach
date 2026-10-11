import { hasFieldSummary, hasFieldProgression, fieldTime, sampledTime, timeSourceLabel, type FieldResults } from "./field-results";
import "./field-results.css";

export function ResultsSummaryView({ results, onSelectCar }: { results: FieldResults; onSelectCar?(index: number): void }) {
  const cars = results.cars.filter((car) => car.player || hasFieldSummary(car));
  const hasCompetitors = cars.some((car) => !car.player);
  return <div class="ri-results-summary" data-ri-view="results">
    <div class="ri-chart-head"><strong title="Recorded order; final classification may be unavailable">Results Summary</strong></div>
    {cars.length === 0 ? <div class="ri-empty">{results.unavailableReason ?? "No field results recorded"}</div> : <div class="ri-results-scroll">
      <table class="ri-results-table"><thead><tr>
        <th>Pos</th><th>Driver / car</th><th>Laps</th>
        <th title="Recorded pit-road entries, including drive-throughs; entries before recording are unknown">Pit visits</th>
        <th title="Observed tire changes on the recorded player car; competitor tire service is unavailable">Tires taken</th>
        <th title="Fuel used over recorded complete player laps; competitor fuel is unavailable">Fuel · L</th>
        <th>Fastest · s</th><th title={results.definition}>Green median · s</th><th>Green laps</th>
      </tr></thead><tbody>{cars.map((car) => <tr key={car.index} class={car.player ? "player" : ""} data-results-car={car.index}>
        <td>{car.position ?? "—"}</td><th scope="row">{onSelectCar && (car.player || hasFieldProgression(car)) ? <button type="button" onClick={() => onSelectCar(car.index)}>{car.name}{car.player ? " · You" : ""}</button> : <span>{car.name}{car.player ? " · You" : ""}</span>}<small>#{car.number || "—"}</small></th>
        <td>{car.completed ?? "—"}</td><td>{car.pits ?? "—"}</td><td>{car.tires === null ? "—" : car.tires.length === 0 ? "None" : `${car.tires.length} · ${car.tires.join(" ")}`}</td>
        <td>{car.fuel === null ? "—" : car.fuel.toFixed(1)}</td><td title={timeSourceLabel(car.fastestSource, car.player)}>{fieldTime(car.fastest, sampledTime(car.fastestSource, car.player))}</td><td title={car.player ? "Recorded player green-lap median" : "Approximate sampled competitor green-lap median"}>{fieldTime(car.pace, !car.player)}</td><td>{hasFieldProgression(car) || car.player ? car.paceCount ?? "—" : "—"}</td>
      </tr>)}</tbody></table>
    </div>}
    {cars.length > 0 && !hasCompetitors && <p class="ri-field-empty" role="status">Competitor history was not recorded for this session.</p>}
  </div>;
}

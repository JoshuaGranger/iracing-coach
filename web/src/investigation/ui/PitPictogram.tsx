import { TIRE_CORNERS, tiresText, type PitStop, type TireCorner } from "../geometry/rail-model";
import { n } from "../format";

/**
 * One pit-stop picture in three places (rail cell, chart-marker hover, details): the car from above, nose up, changed tires lit
 * green and unchanged grey, and a fuel gauge filled by the gallons added over the largest recorded fuel load (omitted when no
 * fuel was added). No sentence: the lap, the stop time, the tires and the gallons are the words.
 */

const CORNER_POSITION: Record<TireCorner, [number, number]> = { LF: [0, 0], RF: [1, 0], LR: [0, 1], RR: [1, 1] };

export function PitGlyph({ stop, height, gauge = true }: { stop: PitStop | null; height: number; gauge?: boolean }) {
  const scale = height / 78;
  const bodyX = 10 * scale;
  const bodyW = 26.8 * scale;
  const tireW = 12.2 * scale;
  const tireH = 18.7 * scale;
  const carW = bodyX * 2 + bodyW;
  const fuel = gauge && stop && stop.fuelAddedGal !== null && stop.fuelAddedGal > 0.05 ? stop.fuelAddedGal : null;
  const gaugeW = 13.3 * scale;
  const gaugeX = carW + 9 * scale;
  const width = fuel !== null ? gaugeX + gaugeW + 1 : carW;
  const fill = fuel !== null ? Math.min(1, fuel / Math.max(fuel, stop!.capacityGal ?? fuel)) : 0;
  const inner = height - 2 - 2 * scale;
  return (
    <svg class="ri-pit-glyph" width={n(width)} height={n(height)} viewBox={`0 0 ${n(width)} ${n(height)}`} aria-hidden="true">
      <rect class="ri-pit-body" x={n(bodyX)} y={n(0.8 * scale)} width={n(bodyW)} height={n(height - 1.6 * scale)} rx={n(11.3 * scale)} stroke-width={n(Math.max(1, 2.8 * scale))} />
      <path class="ri-pit-shield" d={`M${n(15.3 * scale)} ${n(26.5 * scale)}h${n(16.1 * scale)}l${n(-2.1 * scale)} ${n(9.4 * scale)}h${n(-11.8 * scale)}z`} />
      {TIRE_CORNERS.map((corner) => {
        const [column, row] = CORNER_POSITION[corner];
        const lit = stop?.tires?.has(corner) ?? false;
        return (
          <rect key={corner} class={`ri-pit-tire${lit ? " lit" : ""}`} data-tire={corner} data-changed={lit}
            x={n(column === 0 ? 0 : carW - tireW)} y={n((row === 0 ? 10.1 : 49.1) * scale)} width={n(tireW)} height={n(tireH)} rx={n(3.7 * scale)} />
        );
      })}
      {fuel !== null && (
        <g transform={`translate(${n(gaugeX)} 0)`} data-fuel-fill={fill.toFixed(3)}>
          <rect class="ri-pit-gauge" x="0.5" y="0.5" width={n(gaugeW)} height={n(height - 1)} rx={n(4.3 * scale)} />
          <rect class="ri-pit-fuel" x={n(1.5 * scale + 0.5)} y={n(1 + 1 * scale + inner * (1 - fill))} width={n(Math.max(1, gaugeW - 3 * scale))} height={n(Math.max(1, inner * fill))} rx={n(2.9 * scale)} />
        </g>
      )}
    </svg>
  );
}

export function durationText(stop: PitStop): string {
  return stop.durationS !== null ? stop.durationS.toFixed(1) : "—";
}

/** The stop detail's one line about the fields the backend does not extract yet. */
export const PIT_UNEXTRACTED = "Repairs and penalties: not extracted";

/** The hover card: band `PIT · L41`, the picture, the stop time, the tires and `+12.0 gal`. */
export function PitCard({ stop, compact = false }: { stop: PitStop; compact?: boolean }) {
  return (
    <div class={`ri-pit-card${compact ? " compact" : ""}`} role="tooltip" data-pit-card={stop.lap}>
      <div class="ri-pit-band"><span>Pit</span><b>L{stop.lap}</b></div>
      <div class="ri-pit-card-body">
        <PitGlyph stop={stop} height={compact ? 60 : 78} />
        <div class="ri-pit-figs">
          <span class="ri-pit-duration">{durationText(stop)}<small>s</small></span>
          <span class="ri-pit-tires">{tiresText(stop)}</span>
          {stop.fuelAddedGal !== null && stop.fuelAddedGal > 0.05 && <span class="ri-pit-gal">+{stop.fuelAddedGal.toFixed(1)}<small>gal</small></span>}
        </div>
      </div>
      {/* Repair and penalty fields are not extracted yet: nothing is drawn for them, and the stop says so once. */}
      <p class="ri-pit-note" data-pit-unextracted>{PIT_UNEXTRACTED}</p>
    </div>
  );
}

/** Accessible name of a stop: `Pit L41 · 13.0 s · 4 tires · +12.0 gal`. */
export function pitLabel(stop: PitStop): string {
  const parts = [`Pit L${stop.lap}`];
  if (stop.durationS !== null) parts.push(`${stop.durationS.toFixed(1)} s`);
  if (stop.tires) parts.push(tiresText(stop));
  if (stop.fuelAddedGal !== null && stop.fuelAddedGal > 0.05) parts.push(`+${stop.fuelAddedGal.toFixed(1)} gal`);
  return parts.join(" · ");
}

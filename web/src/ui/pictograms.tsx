/**
 * Pit stop pictograms (backlog 1m): one picture, three places (lap rail cell, chart marker hover, details).
 * A car from above, nose up; the tires changed are lit race green, the others grey. A vertical fuel gauge fills in proportion
 * to the gallons added over the car's fuel capacity and is left out when nothing was added. No sentence.
 */
export type TireId = "LF" | "RF" | "LR" | "RR";
export const TIRE_IDS: readonly TireId[] = ["LF", "RF", "LR", "RR"];

/** Gauge scale when the car's fuel capacity is not known. */
export const DEFAULT_FUEL_CAPACITY_GAL = 20;

/** `RF RR`, `4 tires`, or an empty string when none was changed. */
export function tireSummary(changed: readonly TireId[] | null | undefined): string {
  if (!changed || changed.length === 0) return "";
  const lit = TIRE_IDS.filter((t) => changed.includes(t));
  return lit.length === TIRE_IDS.length ? "4 tires" : lit.join(" ");
}

/** Fill of the fuel gauge, 0 to 1; null when no fuel was added. */
export function fuelFill(gallons: number | null | undefined, capacityGal: number | null | undefined): number | null {
  if (gallons === null || gallons === undefined || !Number.isFinite(gallons) || gallons <= 0) return null;
  const capacity = capacityGal && capacityGal > 0 ? capacityGal : DEFAULT_FUEL_CAPACITY_GAL;
  return Math.min(1, gallons / capacity);
}

/** Stop duration to one decimal; null when not known. */
export function formatStopSeconds(seconds: number | null | undefined): string | null {
  return seconds === null || seconds === undefined || !Number.isFinite(seconds) || seconds <= 0 ? null : seconds.toFixed(1);
}

const TIRE_RECT: Record<TireId, { x: number; y: number }> = {
  LF: { x: 1, y: 8 },
  RF: { x: 30, y: 8 },
  LR: { x: 1, y: 40 },
  RR: { x: 30, y: 40 },
};

/** Surface temperatures the band hue spans, °F. Fixed, so two run cards compare; outside it the hue holds its end. */
export const SURFACE_TEMP_RANGE_F: readonly [number, number] = [120, 220];

/**
 * The hue of a surface temperature: the app's progress ramp (cool `--progress-2`, mid `--progress-4`, warm `--progress-6`) mixed
 * linearly over `SURFACE_TEMP_RANGE_F`. A hue only, no good or bad. Null (not read) is the muted text colour.
 */
export function surfaceTempColour(fahrenheit: number | null | undefined): string {
  if (fahrenheit === null || fahrenheit === undefined || !Number.isFinite(fahrenheit)) return "var(--color-text-muted)";
  const [lo, hi] = SURFACE_TEMP_RANGE_F;
  const t = Math.min(1, Math.max(0, (fahrenheit - lo) / (hi - lo)));
  const pct = (value: number): number => Math.round(value * 100);
  return t <= 0.5 ? `color-mix(in srgb, var(--progress-4) ${pct(t * 2)}%, var(--progress-2))` : `color-mix(in srgb, var(--progress-6) ${pct((t - 0.5) * 2)}%, var(--progress-4))`;
}

/** The CSS gradient of the hue over its range, for the key under a wear picture. */
export const SURFACE_TEMP_GRADIENT = "linear-gradient(90deg, var(--progress-2), var(--progress-4), var(--progress-6))";

/** One tire's tread and surface temperature in three bands, left to right as the picture draws them. */
export interface TireWear {
  id: TireId;
  bands: readonly { remaining: number | null; surfaceF: number | null }[];
}

// The wear picture: wider tires (three bars each) on a taller car, in its own 60 × 80 box.
const WEAR_RECT: Record<TireId, { x: number; y: number }> = { LF: { x: 0, y: 10 }, RF: { x: 46, y: 10 }, LR: { x: 0, y: 46 }, RR: { x: 46, y: 46 } };
const WEAR_W = 14;
const WEAR_H = 26;
const BAR_W = 3;
const BAR_GAP = 1;
const BAR_H = 22;

/** Fill height in the wear picture's bar for a tread-left percent. */
export function wearBarHeight(remaining: number | null): number {
  return remaining === null ? 0 : (BAR_H * Math.min(100, Math.max(0, remaining))) / 100;
}

function WearTires({ wear }: { wear: readonly TireWear[] }) {
  return (
    <>
      <g transform="translate(10 2) scale(1 1.25)">
        <path d="M13 4h14c3 0 5 2 5.5 6L34 22l1 6v18l-1 10c-.5 3-2.5 4-5 4H11c-2.5 0-4.5-1-5-4l-1-10V28l1-6-.5-12C6 6 8 4 13 4z" fill="#2a3a46" stroke="#425663" stroke-width="1" />
        <path d="M12 20h16l2 8H10z" fill="#15202a" />
      </g>
      {TIRE_IDS.map((id) => {
        const { x, y } = WEAR_RECT[id];
        const tire = wear.find((item) => item.id === id);
        return (
          <g key={id} data-tire={id}>
            <rect x={x} y={y} width={WEAR_W} height={WEAR_H} rx="3" fill="#2f3d47" stroke="#52626d" stroke-width="1" />
            {[0, 1, 2].map((index) => {
              const band = tire?.bands[index];
              const bx = x + 1.5 + index * (BAR_W + BAR_GAP);
              const top = y + 2;
              const h = wearBarHeight(band?.remaining ?? null);
              return (
                <g key={index} data-band={index}>
                  <rect x={bx} y={top} width={BAR_W} height={BAR_H} rx="0.8" fill="#15202a" />
                  {h > 0 ? <rect x={bx} y={top + BAR_H - h} width={BAR_W} height={h} rx="0.8" style={`fill:${surfaceTempColour(band?.surfaceF)}`} data-remaining={band?.remaining ?? undefined} /> : null}
                </g>
              );
            })}
          </g>
        );
      })}
    </>
  );
}

/**
 * The car from above with its tires. `changed` null means the tires are not known: all grey. With `wear` the tires draw three bars
 * each (tread left as the fill, surface temperature as its hue) and `changed` is not drawn: a run card's end-of-run picture.
 */
export function TireDiagram({ changed, wear, label, width = 24, height = 38 }: { changed?: readonly TireId[] | null | undefined; wear?: readonly TireWear[] | null; label?: string; width?: number; height?: number }) {
  if (wear) {
    return (
      <svg class="tire-diagram tire-wear" width={width} height={height} viewBox="0 0 60 80" role="img" aria-label={label ?? "Tire tread and temperature"} data-tire-wear>
        <WearTires wear={wear} />
      </svg>
    );
  }
  return (
    <svg class="tire-diagram" width={width} height={height} viewBox="0 0 40 64" role="img" aria-label={changed ? tireSummary(changed) || "No tires changed" : "Tires unknown"}>
      <path d="M13 4h14c3 0 5 2 5.5 6L34 22l1 6v18l-1 10c-.5 3-2.5 4-5 4H11c-2.5 0-4.5-1-5-4l-1-10V28l1-6-.5-12C6 6 8 4 13 4z" fill="#2a3a46" stroke="#425663" stroke-width="1.2" />
      <path d="M12 20h16l2 8H10z" fill="#15202a" />
      {TIRE_IDS.map((t) => {
        const lit = changed?.includes(t) === true;
        const { x, y } = TIRE_RECT[t];
        return (
          <rect
            key={t}
            x={x}
            y={y}
            width="9"
            height="16"
            rx="3"
            fill={lit ? "var(--race-green)" : "#3a4954"}
            stroke={lit ? "var(--race-green)" : "#52626d"}
            stroke-width="1"
            data-lit={lit}
          />
        );
      })}
    </svg>
  );
}

/** Vertical fuel gauge; draws nothing when no fuel was added. */
export function FuelGauge({ gallons, capacityGal, height = 38 }: { gallons: number | null | undefined; capacityGal?: number | null; height?: number }) {
  const fill = fuelFill(gallons, capacityGal);
  if (fill === null) return null;
  const h = Math.max(2, 28 * fill);
  return (
    <svg class="fuel-gauge" width="10" height={height} viewBox="0 0 10 38" role="img" aria-label={`${gallons!.toFixed(1)} gal added`} data-fill={fill.toFixed(3)}>
      <rect x="0.75" y="2.75" width="8.5" height="32.5" rx="3" fill="none" stroke="var(--race-fuel)" stroke-width="1.5" opacity="0.7" />
      <rect x="2.5" y={32.5 - h} width="5" height={h} rx="1.5" fill="var(--race-fuel)" />
    </svg>
  );
}

export interface PitStop {
  /** Lap the stop was made on. */
  lap?: number | null;
  /** The tires changed; null when not known. */
  tires?: readonly TireId[] | null;
  /** US gallons added; 0 or null leaves the gauge out. */
  gallons?: number | null;
  /** The car's fuel capacity in US gallons (session info DriverCarFuelMaxLtr, converted). */
  capacityGal?: number | null;
  /** Stop duration in seconds when known. */
  durationS?: number | null;
}

/** The pictogram in a row or a marker: lap number, car, gauge, duration. */
export function PitStopPictogram({ stop, showLap = true }: { stop: PitStop; showLap?: boolean }) {
  const duration = formatStopSeconds(stop.durationS);
  return (
    <span class="pit-pic" data-testid="pit-pictogram">
      {showLap && stop.lap != null ? <span class="pit-pic-lap">L{stop.lap}</span> : null}
      <TireDiagram changed={stop.tires} />
      <FuelGauge gallons={stop.gallons} capacityGal={stop.capacityGal} />
      {duration ? <span class="pit-pic-dur">{duration} s</span> : null}
    </span>
  );
}

/** The marker hover and the details view: a pit-blue band with the lap, the picture, the duration large, the tire list and the gallons. */
export function PitStopCard({ stop }: { stop: PitStop }) {
  const duration = formatStopSeconds(stop.durationS);
  const tires = stop.tires ? tireSummary(stop.tires) : "";
  const gal = fuelFill(stop.gallons, stop.capacityGal) !== null ? stop.gallons! : null;
  return (
    <div class="pit-card" data-testid="pit-card">
      <div class="pit-card-band">
        {stop.lap != null ? `L${stop.lap}` : "Pit"}
        <span>Pit</span>
      </div>
      <div class="pit-card-body">
        <span class="pit-pic">
          <TireDiagram changed={stop.tires} width={34} height={54} />
          <FuelGauge gallons={stop.gallons} capacityGal={stop.capacityGal} height={54} />
        </span>
        <div class="pit-card-figs">
          {duration ? (
            <div class="pit-card-dur">
              {duration}
              <small>s</small>
            </div>
          ) : null}
          {tires ? <div class="pit-card-tires">{tires}</div> : null}
          {gal !== null ? (
            <div class="pit-card-gal">
              +{gal.toFixed(1)}
              <small>gal</small>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}

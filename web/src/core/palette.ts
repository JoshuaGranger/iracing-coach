import { theme } from "../gen/theme";

/**
 * Colour that carries data in the investigation and on Sessions (web round 2, "Colour budget"):
 * run identity on run-level marks, race progress on lap-level marks. Both come from `config/theme.dark.json`
 * (`identity.runs`, `identity.progress`), never from a literal in a page.
 */

/** Number of distinct run colours (`--run-1` to `--run-8`); a ninth run cycles back to the first. */
export const RUN_COLORS: number = theme.identity.runs.length;

/**
 * The identity colour of run `n` (1-based, in time order) as a CSS value, `var(--run-1)` to `var(--run-8)`, so it follows the
 * token. A run beyond the palette cycles; line style (`runDash`) is the secondary cue that tells a cycled run apart.
 * A missing or invalid number answers the muted text colour.
 */
export function runColour(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isInteger(n) || n < 1) return "var(--color-text-muted)";
  return `var(--run-${((n - 1) % RUN_COLORS) + 1})`;
}

/** The token hex of run `n` for canvas and SVG attributes that cannot read a CSS variable; same cycling rule as `runColour`. */
export function runHex(n: number): string {
  const list = theme.identity.runs;
  const i = Number.isInteger(n) && n >= 1 ? (n - 1) % list.length : 0;
  return list[i]!;
}

/** SVG `stroke-dasharray` per run, the second cue after colour: solid, dashed, dotted, then alternations. Empty string is solid. */
const DASHES = ["", "7 4", "1.5 3.5", "10 3 2 3", "4 4"] as const;
export function runDash(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isInteger(n) || n < 1) return "";
  return DASHES[(n - 1) % DASHES.length]!;
}

type Rgb = readonly [number, number, number];
const hex = (h: string): Rgb => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16)) as unknown as Rgb;
const STOPS: readonly Rgb[] = theme.identity.progress.map(hex);

function mix(t: number): Rgb {
  const x = t * (STOPS.length - 1);
  const k = Math.min(STOPS.length - 2, Math.floor(x));
  const f = x - k;
  const a = STOPS[k]!;
  const b = STOPS[k + 1]!;
  return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f];
}

const toHex = ([r, g, b]: Rgb): string =>
  `#${[r, g, b].map((v) => Math.round(Math.max(0, Math.min(255, v))).toString(16).padStart(2, "0")).join("")}`;

/**
 * Race progress colour of lap `i` of `n` (both 0-based count: `i` in 0..n-1): a start-to-finish gradient through the
 * `identity.progress` stops. Compute it on the full phase (`n` is the phase's lap count, not the filtered count), so a
 * filter never recolours a lap. One lap (`n` of 1) is the start colour; `i` outside the range is clamped. Returns `#rrggbb`.
 */
export function raceProgressColour(i: number, n: number): string {
  if (!Number.isFinite(i) || !Number.isFinite(n) || n <= 1) return toHex(STOPS[0]!);
  const t = Math.max(0, Math.min(1, i / (n - 1)));
  return toHex(mix(t));
}

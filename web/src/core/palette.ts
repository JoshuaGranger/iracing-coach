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

/* ---- car accent ---------------------------------------------------------------------------------------------------------- */

/** Hue slots around the circle and the two tones of each: 60 accents, 12 degrees apart within a tone. */
const ACCENT_SLOTS = 30;
/** `[L, C]` in OKLCH: a body tone and a paler one, both well under the chroma of the race hues so no accent reads as a flag or a loss. */
const ACCENT_TONES = [
  [0.66, 0.12],
  [0.8, 0.1],
] as const;
/**
 * Chosen once against the iRacing car folders and the mock and fixture cars so neighbouring names (`ferrari296gt3`, `ferrari499p`) and
 * the cars the tests use land on separate accents. Changing it recolours every car on every device.
 */
const ACCENT_SALT = 5561;

/** A 32-bit hash of the car identity (FNV-1a with a murmur finaliser): close names land far apart. */
function accentHash(key: string): number {
  let h = (0x811c9dc5 ^ ACCENT_SALT) >>> 0;
  for (let i = 0; i < key.length; i++) {
    h ^= key.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  h ^= h >>> 16;
  h = Math.imul(h, 0x85ebca6b) >>> 0;
  h ^= h >>> 13;
  h = Math.imul(h, 0xc2b2ae35) >>> 0;
  h ^= h >>> 16;
  return h >>> 0;
}

const encode = (v: number): number => {
  const c = Math.max(0, Math.min(1, v));
  return c <= 0.0031308 ? 12.92 * c : 1.055 * c ** (1 / 2.4) - 0.055;
};

/** OKLCH to `#rrggbb` (out-of-gamut channels clamp). */
function oklchHex(L: number, C: number, hueDeg: number): string {
  const r = (hueDeg * Math.PI) / 180;
  const a = C * Math.cos(r);
  const b = C * Math.sin(r);
  const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3;
  const rgb = [4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s, -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s, -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s];
  return toHex(rgb.map((v) => encode(v) * 255) as unknown as Rgb);
}

/**
 * The accent of a car, a function of its identity alone, so it is the same on every page, device and visit and never depends on
 * which cars happen to be listed (backlog #73). The key is the car path without case or punctuation (`mx5-cup` and `MX5 Cup`
 * are one car). The hash picks one of 30 hues and one of two tones of equal lightness steps, so a car beside another of the same
 * make still differs. Chroma stays under the race hues'. A missing car answers the muted text colour. Returns `#rrggbb`.
 */
export function carAccent(carPath: string | null | undefined): string {
  const key = (carPath ?? "").toLowerCase().replace(/[^a-z0-9]+/g, "");
  if (!key) return "var(--color-text-muted)";
  const h = accentHash(key);
  const [L, C] = ACCENT_TONES[(h >>> 4) & 1]!;
  return oklchHex(L, C, ((h >>> 8) % ACCENT_SLOTS) * (360 / ACCENT_SLOTS) + 360 / ACCENT_SLOTS / 2);
}

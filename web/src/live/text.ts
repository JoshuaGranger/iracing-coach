/**
 * The .NET (en-US, invariant culture) number and time renderings the overlay shows
 * (crate-api.md 4.7 text rules). `crates/coach-live/src/format.rs` is the reference;
 * `tiles.test.ts` checks this module against the tile readings of `tests/vectors/engine.json`.
 */

const F64 = new DataView(new ArrayBuffer(8));

/** `abs` as `mantissa * 2^exponent`, exactly. */
function decompose(abs: number): [bigint, number] {
  F64.setFloat64(0, abs);
  const bits = F64.getBigUint64(0);
  const biased = Number((bits >> 52n) & 0x7ffn);
  const fraction = bits & ((1n << 52n) - 1n);
  return biased === 0 ? [fraction, -1074] : [fraction | (1n << 52n), biased - 1075];
}

/** .NET `F{n}`: the exact binary value rounded to n decimals, ties to even (JS `toFixed` rounds ties up). */
export function fixed(value: number, decimals: number): string {
  if (!Number.isFinite(value)) return String(value);
  const negative = value < 0 || Object.is(value, -0);
  const [mantissa, exponent] = decompose(Math.abs(value));
  const scaled = mantissa * 10n ** BigInt(decimals);
  let q: bigint;
  if (exponent >= 0) q = scaled << BigInt(exponent);
  else {
    const shift = BigInt(-exponent);
    q = scaled >> shift;
    const rest = scaled - (q << shift);
    const half = 1n << (shift - 1n);
    if (rest > half || (rest === half && (q & 1n) === 1n)) q += 1n;
  }
  const digits = q.toString().padStart(decimals + 1, "0");
  const body = decimals === 0 ? digits : `${digits.slice(0, -decimals)}.${digits.slice(-decimals)}`;
  return negative ? `-${body}` : body;
}

/** 15 significant digits of a non-negative finite value, and the number of digits before the decimal point. */
function significantDigits(abs: number): [number[], number] {
  if (abs === 0) return [[0], 1];
  const [mantissa, exponent] = abs.toExponential(14).split("e");
  return [[...mantissa!.replace(".", "")].map(Number), Number(exponent) + 1];
}

function roundHalfUp(digits: number[], point: number, decimals: number): string {
  const intLen = Math.max(point, 1);
  const total = intLen + decimals;
  const grid: number[] = new Array(total + 1).fill(0);
  digits.forEach((digit, index) => {
    const position = index + (intLen - point);
    if (position >= 0 && position <= total) grid[position] = digit;
  });
  const roundUp = grid[total]! >= 5;
  grid.length = total;
  if (roundUp) {
    let index = total;
    for (;;) {
      if (index === 0) {
        grid.unshift(1);
        break;
      }
      index -= 1;
      if (grid[index] === 9) grid[index] = 0;
      else {
        grid[index]! += 1;
        break;
      }
    }
  }
  const intDigits = grid.length - decimals;
  return decimals === 0 ? grid.join("") : `${grid.slice(0, intDigits).join("")}.${grid.slice(intDigits).join("")}`;
}

/** .NET custom format `0.00...`: reduced to 15 significant digits, then rounded half away from zero. A nonzero result keeps the sign. */
export function custom(value: number, decimals: number): string {
  if (!Number.isFinite(value)) return String(value);
  const negative = value < 0;
  const [digits, point] = significantDigits(Math.abs(value));
  const body = roundHalfUp(digits, point, decimals);
  return negative && /[1-9]/.test(body) ? `-${body}` : body;
}

/** .NET custom format `0.##`: up to `maxDecimals` places, trailing zeros dropped. */
export function customOptional(value: number, maxDecimals: number): string {
  const text = custom(value, maxDecimals);
  return text.includes(".") ? text.replace(/0+$/, "").replace(/\.$/, "") : text;
}

/** .NET custom format `+0.000;-0.000;0.000`: a value that rounds to zero uses the zero section. */
export function signed(value: number, decimals: number): string {
  const body = custom(Math.abs(value), decimals);
  if (!/[1-9]/.test(body)) return body;
  return value < 0 ? `-${body}` : `+${body}`;
}

/** .NET en-US `P0`: the exact value times 100 rounded ties to even, thousands separators, `%`. */
export function percent0(value: number): string {
  const hundredths = fixed(Math.abs(value), 2).replace(".", "").replace(/^0+/, "") || "0";
  return `${value < 0 ? "-" : ""}${hundredths.replace(/\B(?=(\d{3})+$)/g, ",")}%`;
}

/** A whole number carried as f32: the nearest integer, halves away from zero. */
export function whole(value: number): string {
  if (!Number.isFinite(value)) return "";
  return String((value < 0 ? -1 : 1) * Math.round(Math.abs(value)));
}

/** `Math.Ceiling` interpolated into text. */
export function ceiling(value: number): string {
  return Number.isFinite(value) ? String(Math.ceil(value)) : "";
}

/**
 * `TimeSpan.FromSeconds(s).ToString(s >= 60 ? "m\:ss\.fff" : "s\.fff")`: whole ticks, the fraction truncated
 * (never rounded) to milliseconds, hours dropped.
 */
export function lapTime(seconds: number): string {
  const ticks = Math.trunc(seconds * 10_000_000);
  const sign = ticks < 0 ? "-" : "";
  const abs = Math.abs(ticks);
  const millis = Math.floor((abs % 10_000_000) / 10_000);
  const secs = Math.floor(abs / 10_000_000) % 60;
  if (seconds >= 60) return `${sign}${Math.floor(abs / 600_000_000) % 60}:${String(secs).padStart(2, "0")}.${String(millis).padStart(3, "0")}`;
  return `${sign}${secs}.${String(millis).padStart(3, "0")}`;
}

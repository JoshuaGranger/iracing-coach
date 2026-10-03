import { CHANNELS, CHANNEL_INDEX } from "../gen/live-channels";
import type { LiveFrame } from "./frame";

/** Ten minutes at the 60 frames/s wire rate, the server's backlog horizon (live-frame.md, architecture 2.2). */
export const HISTORY_CAPACITY = 36_000;

export interface TrendWindow {
  seconds?: number;
  laps?: number;
}

export const TREND_WINDOW_SPECS: Record<string, TrendWindow> = {
  s15: { seconds: 15 },
  s30: { seconds: 30 },
  s60: { seconds: 60 },
  one_lap: { laps: 1 },
  three_laps: { laps: 3 },
};

export interface Series {
  t: Float64Array;
  v: Float32Array;
}

/**
 * Ring buffer of the last HISTORY_CAPACITY frames, one Float32 column per channel (allocated when the channel
 * first has a value). A session epoch change clears it: values from before a session boundary never mix with after.
 */
export class LiveHistory {
  private times = new Float64Array(HISTORY_CAPACITY);
  private laps = new Uint16Array(HISTORY_CAPACITY);
  private columns = new Map<number, Float32Array>();
  private start = 0;
  private size = 0;
  private epoch = -1;
  private lapIndex = CHANNEL_INDEX.lap!;

  get length(): number {
    return this.size;
  }

  clear(): void {
    this.start = 0;
    this.size = 0;
    for (const c of this.columns.values()) c.fill(NaN);
  }

  push(frame: LiveFrame): void {
    if (frame.sessionEpoch !== this.epoch) {
      this.clear();
      this.epoch = frame.sessionEpoch;
    }
    const last = this.size > 0 ? this.times[this.slot(this.size - 1)]! : -Infinity;
    if (frame.sessionTime < last) this.clear();
    let at: number;
    if (this.size < HISTORY_CAPACITY) at = this.slot(this.size++);
    else {
      at = this.start;
      this.start = (this.start + 1) % HISTORY_CAPACITY;
    }
    this.times[at] = frame.sessionTime;
    this.laps[at] = frame.values[this.lapIndex] ?? 0;
    for (let i = 0; i < CHANNELS.length; i++) {
      const v = frame.values[i];
      let col = this.columns.get(i);
      if (!col) {
        if (v === null || v === undefined) continue;
        col = new Float32Array(HISTORY_CAPACITY).fill(NaN);
        this.columns.set(i, col);
      }
      col[at] = v ?? NaN;
    }
  }

  private slot(i: number): number {
    return (this.start + i) % HISTORY_CAPACITY;
  }

  /** The newest session time held, or null. */
  latestTime(): number | null {
    return this.size ? this.times[this.slot(this.size - 1)]! : null;
  }

  /** Points of `channel` inside the window ending at the newest frame. Missing values are NaN. */
  series(channel: string, window: TrendWindow): Series {
    const idx = CHANNEL_INDEX[channel];
    const col = idx === undefined ? undefined : this.columns.get(idx);
    if (!col || this.size === 0) return { t: new Float64Array(0), v: new Float32Array(0) };
    const newest = this.slot(this.size - 1);
    let first = 0;
    if (window.laps !== undefined && this.laps[newest]! > 0) {
      const minLap = this.laps[newest]! - (window.laps - 1);
      first = this.size;
      while (first > 0 && this.laps[this.slot(first - 1)]! >= minLap) first--;
    } else {
      const t0 = this.times[newest]! - (window.seconds ?? (window.laps ?? 1) * 120);
      let lo = 0;
      let hi = this.size;
      while (lo < hi) {
        const mid = (lo + hi) >> 1;
        if (this.times[this.slot(mid)]! < t0) lo = mid + 1;
        else hi = mid;
      }
      first = lo;
    }
    const n = this.size - first;
    const t = new Float64Array(n);
    const v = new Float32Array(n);
    for (let i = 0; i < n; i++) {
      const s = this.slot(first + i);
      t[i] = this.times[s]!;
      v[i] = col[s]!;
    }
    return { t, v };
  }
}

export interface Column {
  x: number;
  min: number;
  max: number;
}

/**
 * Reduce a series to one min/max pair per pixel column so spikes survive at any zoom (a plain stride would lose them).
 * NaN values break the line: columns with no finite value are omitted.
 */
export function downsample(series: Series, t0: number, t1: number, columns: number): Column[] {
  const out: Column[] = [];
  if (columns < 1 || t1 <= t0) return out;
  const span = t1 - t0;
  let cur: Column | null = null;
  for (let i = 0; i < series.t.length; i++) {
    const v = series.v[i]!;
    if (Number.isNaN(v)) continue;
    const x = Math.min(columns - 1, Math.max(0, Math.floor(((series.t[i]! - t0) / span) * columns)));
    if (cur && cur.x === x) {
      if (v < cur.min) cur.min = v;
      if (v > cur.max) cur.max = v;
    } else {
      cur = { x, min: v, max: v };
      out.push(cur);
    }
  }
  return out;
}

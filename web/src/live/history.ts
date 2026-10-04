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
  /** Session time of each sample, seconds. */
  t: Float64Array;
  v: Float32Array;
  /** Plot position: session time for a time window, lap number plus lap fraction for a lap window. */
  x: Float64Array;
  axis: "time" | "distance";
}

/** Name of the history column that holds the coach cue's priority (the cue is not a table channel). */
export const CUE_PRIORITY = "cue_priority";

/**
 * Ring buffer of the last HISTORY_CAPACITY frames, one Float32 column per channel (allocated when the channel
 * first has a value). A session epoch change clears it: values from before a session boundary never mix with after.
 */
export class LiveHistory {
  private times = new Float64Array(HISTORY_CAPACITY);
  private laps = new Uint16Array(HISTORY_CAPACITY);
  /** Lap number plus lap fraction, NaN when either is missing: the axis of the lap windows. */
  private positions = new Float64Array(HISTORY_CAPACITY).fill(NaN);
  private columns = new Map<number, Float32Array>();
  private cue: Float32Array | null = null;
  private start = 0;
  private size = 0;
  private epoch = -1;
  private lapIndex = CHANNEL_INDEX.lap!;
  private distIndex = CHANNEL_INDEX.lap_dist_pct!;

  get length(): number {
    return this.size;
  }

  clear(): void {
    this.start = 0;
    this.size = 0;
    for (const c of this.columns.values()) c.fill(NaN);
    this.cue?.fill(NaN);
    this.positions.fill(NaN);
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
    const lap = frame.values[this.lapIndex];
    const dist = frame.values[this.distIndex];
    this.laps[at] = lap ?? 0;
    this.positions[at] = lap != null && lap > 0 && dist != null ? lap + dist : NaN;
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
    if (frame.cue) {
      this.cue ??= new Float32Array(HISTORY_CAPACITY).fill(NaN);
      this.cue[at] = frame.cue.priority;
    } else if (this.cue) this.cue[at] = NaN;
  }

  private slot(i: number): number {
    return (this.start + i) % HISTORY_CAPACITY;
  }

  /** The newest session time held, or null. */
  latestTime(): number | null {
    return this.size ? this.times[this.slot(this.size - 1)]! : null;
  }

  /** Whether the channel has had a value in this session (false for a channel the collector does not report). */
  hasChannel(channel: string): boolean {
    return this.column(channel) !== undefined;
  }

  private column(channel: string): Float32Array | undefined {
    if (channel === CUE_PRIORITY) return this.cue ?? undefined;
    const idx = CHANNEL_INDEX[channel];
    return idx === undefined ? undefined : this.columns.get(idx);
  }

  /** Index of the first sample inside the window ending at the newest frame, and the axis it was cut on. */
  private windowStart(window: TrendWindow): { first: number; axis: "time" | "distance" } {
    const newest = this.slot(this.size - 1);
    if (window.laps !== undefined) {
      const head = this.positions[newest]!;
      if (Number.isFinite(head)) {
        // Rolling window: the newest lap position minus N laps. A sample with no position stays inside it.
        const minPos = head - window.laps;
        let first = this.size;
        while (first > 0) {
          const p = this.positions[this.slot(first - 1)]!;
          if (Number.isFinite(p) && p < minPos) break;
          first--;
        }
        return { first, axis: "distance" };
      }
    }
    const t0 = this.times[newest]! - (window.seconds ?? (window.laps ?? 1) * 120);
    let lo = 0;
    let hi = this.size;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (this.times[this.slot(mid)]! < t0) lo = mid + 1;
      else hi = mid;
    }
    return { first: lo, axis: "time" };
  }

  /**
   * One value per sample of the window, computed from `channels` of the same row by `read` (NaN where a channel is missing or
   * `read` says so). Lets a tile trend show the tile's own display value, not the raw stream value.
   */
  map(channels: readonly string[], window: TrendWindow, read: (values: readonly number[]) => number): Series {
    const cols = channels.map((c) => this.column(c));
    if (this.size === 0 || cols.some((c) => !c)) return { t: new Float64Array(0), v: new Float32Array(0), x: new Float64Array(0), axis: "time" };
    const { first, axis } = this.windowStart(window);
    const n = this.size - first;
    const t = new Float64Array(n);
    const v = new Float32Array(n);
    const x = new Float64Array(n);
    const row = new Array<number>(cols.length);
    for (let i = 0; i < n; i++) {
      const s = this.slot(first + i);
      t[i] = this.times[s]!;
      x[i] = axis === "distance" ? this.positions[s]! : this.times[s]!;
      let missing = false;
      for (let c = 0; c < cols.length; c++) {
        const value = cols[c]![s]!;
        if (Number.isNaN(value)) missing = true;
        row[c] = value;
      }
      v[i] = missing ? NaN : read(row);
    }
    // A distance window keeps the samples that have a position; the others have no place on that axis.
    if (axis === "distance") return compactPlaced({ t, v, x, axis });
    return { t, v, x, axis };
  }

  /** Points of `channel` inside the window ending at the newest frame. Missing values are NaN. */
  series(channel: string, window: TrendWindow): Series {
    return this.map([channel], window, (r) => r[0]!);
  }
}

function compactPlaced(s: Series): Series {
  let keep = 0;
  for (let i = 0; i < s.x.length; i++) if (Number.isFinite(s.x[i]!)) keep++;
  if (keep === s.x.length) return s;
  const t = new Float64Array(keep);
  const v = new Float32Array(keep);
  const x = new Float64Array(keep);
  let at = 0;
  for (let i = 0; i < s.x.length; i++) {
    if (!Number.isFinite(s.x[i]!)) continue;
    t[at] = s.t[i]!;
    v[at] = s.v[i]!;
    x[at] = s.x[i]!;
    at++;
  }
  return { t, v, x, axis: s.axis };
}

export interface Column {
  x: number;
  min: number;
  max: number;
  /** The line breaks before this column: a gap in time, or samples with no value, lie between it and the previous one. */
  gap: boolean;
}

/**
 * A gap in a trend: a jump in time of more than 3.5 sample intervals of the slowest stream the page ever asks for (10 Hz, the
 * lowest decimation step). A threshold from the data itself would misread a window that mixes the 60 Hz backlog and decimated
 * live frames. Samples with no value break the line on their own (NaN).
 */
export const TREND_GAP_S = 3.5 / 10;

/**
 * Reduce a series to one min/max pair per pixel column so spikes survive at any zoom (a plain stride would lose them).
 * NaN values and time gaps break the line: columns with no finite value are omitted, and a column after a gap says so.
 */
export function downsample(series: Series, x0: number, x1: number, columns: number, gap = TREND_GAP_S): Column[] {
  const out: Column[] = [];
  if (columns < 1 || x1 <= x0) return out;
  const span = x1 - x0;
  let cur: Column | null = null;
  let prevT: number | null = null;
  let sawMissing = false;
  for (let i = 0; i < series.t.length; i++) {
    const v = series.v[i]!;
    if (Number.isNaN(v)) {
      sawMissing = true;
      continue;
    }
    const t = series.t[i]!;
    const broken = prevT !== null && (sawMissing || t - prevT > gap);
    prevT = t;
    sawMissing = false;
    const x = Math.min(columns - 1, Math.max(0, Math.floor(((series.x[i]! - x0) / span) * columns)));
    if (cur && cur.x === x && !broken) {
      if (v < cur.min) cur.min = v;
      if (v > cur.max) cur.max = v;
    } else {
      cur = { x, min: v, max: v, gap: broken };
      out.push(cur);
    }
  }
  return out;
}

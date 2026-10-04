import type { LiveRate } from "./connection";

/**
 * Automatic decimation (backlog 1e): the Live page always asks for 60 Hz and there is no rate setting. A device that cannot keep
 * up shows it in its frame time, the time between two render passes. Over a window of passes, a mean above the budget steps the
 * stream down (60, 30, 10 Hz); after a calm minute it tries the next step up again, and each failed try doubles the wait.
 */
export const RATE_STEPS: readonly LiveRate[] = [60, 30, 10];

/** Mean frame time that counts as keeping up: 40 frames a second. Above it the page is dropping frames. */
export const FRAME_BUDGET_MS = 25;
const WINDOW = 90;
/** A pass longer than this was a hidden tab or a suspended page, not a slow device. */
const IGNORE_ABOVE_MS = 250;
const FIRST_RETRY_MS = 60_000;
const MAX_RETRY_MS = 15 * 60_000;

export class RateGovernor {
  private step = 0;
  private samples: number[] = [];
  private loweredAt = -Infinity;
  private retryAfter = FIRST_RETRY_MS;
  private triedUpAt = -Infinity;

  constructor(private readonly budgetMs = FRAME_BUDGET_MS) {}

  get rate(): LiveRate {
    return RATE_STEPS[this.step]!;
  }

  /**
   * Feed one render pass: its duration since the previous pass and the clock. Returns the new stream rate when it changes,
   * else null.
   */
  observe(frameMs: number, nowMs: number): LiveRate | null {
    if (!(frameMs > 0) || frameMs > IGNORE_ABOVE_MS) {
      this.samples = [];
      return null;
    }
    this.samples.push(frameMs);
    if (this.samples.length < WINDOW) return null;
    const mean = this.samples.reduce((a, b) => a + b, 0) / this.samples.length;
    this.samples = [];
    if (mean > this.budgetMs && this.step < RATE_STEPS.length - 1) {
      // A step up that fails soon after waits twice as long before the next try.
      if (nowMs - this.triedUpAt < 10_000) this.retryAfter = Math.min(MAX_RETRY_MS, this.retryAfter * 2);
      this.step += 1;
      this.loweredAt = nowMs;
      return this.rate;
    }
    if (mean <= this.budgetMs && this.step > 0 && nowMs - this.loweredAt >= this.retryAfter) {
      this.step -= 1;
      this.loweredAt = nowMs;
      this.triedUpAt = nowMs;
      return this.rate;
    }
    return null;
  }
}

import { describe, expect, it } from "vitest";
import type { Job } from "./jobs";
import { isFinalJobState, jobFailure } from "./jobs";

const job = (state: string, extra: Partial<Job> = {}) => ({ job_id: 1, state, ...extra }) as unknown as Job;

describe("jobs", () => {
  it("treats every final state as final, interrupted included, and the live states as not", () => {
    for (const state of ["succeeded", "failed", "cancelled", "deferred", "interrupted"]) expect(isFinalJobState(state), state).toBe(true);
    for (const state of ["queued", "running"]) expect(isFinalJobState(state), state).toBe(false);
  });

  it("reports an interrupted job instead of waiting for it", () => {
    expect(jobFailure(job("interrupted"))).toBe("Interrupted. Run it again.");
    expect(jobFailure(job("interrupted", { error_message: "Server restarted" }))).toBe("Server restarted");
    expect(jobFailure(job("cancelled"))).toBe("Cancelled.");
  });
});

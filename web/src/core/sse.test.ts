import { describe, expect, it } from "vitest";
import { EventsClient, backoffMs } from "./sse";

describe("events client", () => {
  it("routes parsed events by type and to wildcard listeners, ignoring junk", () => {
    const c = new EventsClient();
    const seen: string[] = [];
    c.on("job_state", (e) => seen.push(`job:${e.job.job_id}`));
    c.on("*", (e) => seen.push(`any:${e.type}`));
    c.dispatch(JSON.stringify({ type: "job_state", job: { job_id: 3, kind: "analyze", state: "running" } }));
    c.dispatch(JSON.stringify({ type: "library_changed", customer_id: 1, scope: "sessions" }));
    c.dispatch("not json");
    c.dispatch(JSON.stringify({ nope: 1 }));
    expect(seen).toEqual(["job:3", "any:job_state", "any:library_changed"]);
  });

  it("stops delivering after unsubscribe", () => {
    const c = new EventsClient();
    let n = 0;
    const off = c.on("presence", () => n++);
    c.dispatch(JSON.stringify({ type: "presence", collector: {} }));
    off();
    c.dispatch(JSON.stringify({ type: "presence", collector: {} }));
    expect(n).toBe(1);
  });

  it("backs off 1 s to 30 s with jitter", () => {
    expect(backoffMs(1, () => 0.5)).toBe(1000);
    expect(backoffMs(5, () => 0.5)).toBe(16000);
    expect(backoffMs(20, () => 0.5)).toBe(30000);
  });
});

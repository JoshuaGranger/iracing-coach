import { useEffect, useRef, useState } from "preact/hooks";
import { ApiError, type Schemas, api, isJobRef } from "./api";
import { humanize } from "./format";
import { events } from "./sse";

export type Job = Schemas["Job"];
const TERMINAL = new Set<string>(["succeeded", "failed", "cancelled", "deferred"]);

/**
 * Resolves with the finished job. Uses `job_state` events and polls every `pollMs` as a fallback (the stream may be
 * down; polling also covers a missed event). Rejects only when aborted.
 */
export function waitForJob(id: number, opts: { signal?: AbortSignal; pollMs?: number } = {}): Promise<Job> {
  return new Promise<Job>((resolve, reject) => {
    let finished = false;
    let checking = false;
    const end = () => {
      finished = true;
      off();
      clearInterval(timer);
      opts.signal?.removeEventListener("abort", onAbort);
    };
    const check = async () => {
      if (finished || checking) return;
      checking = true;
      try {
        const job = await api.job(id);
        if (!finished && TERMINAL.has(job.state)) {
          end();
          resolve(job);
        }
      } catch {
        /* unreachable or not yet visible: the next tick tries again */
      } finally {
        checking = false;
      }
    };
    const onAbort = () => {
      end();
      reject(new DOMException("Aborted", "AbortError"));
    };
    const off = events.on("job_state", (e) => {
      if (e.job.job_id === id) void check();
    });
    const timer = setInterval(check, opts.pollMs ?? 1500);
    if (opts.signal?.aborted) onAbort();
    else opts.signal?.addEventListener("abort", onAbort);
    void check();
  });
}

/** The job's own words for why it did not produce a result. */
export function jobFailure(job: Job): string {
  if (job.state === "cancelled") return "Cancelled.";
  if (job.state === "deferred") return job.error_message ?? `Deferred: ${humanize(job.error_code ?? "exceeds_memory_budget").toLowerCase()}.`;
  return job.error_message ?? (job.error_code ? humanize(job.error_code) : "The job failed.");
}

export type RunState<T> =
  | { phase: "idle" }
  | { phase: "running"; label?: string }
  | { phase: "done"; result: T; job: Job | null }
  | { phase: "error"; message: string };

/**
 * Run a request that answers with a result or a `202 JobRef`, and wait for the result. One run at a time;
 * starting another aborts the wait for the first.
 */
export function useJobRun<T>(): { state: RunState<T>; run(start: () => Promise<{ status: number; body: unknown }>, label?: string): Promise<void>; reset(): void } {
  const [state, setState] = useState<RunState<T>>({ phase: "idle" });
  const ctl = useRef<AbortController | null>(null);
  useEffect(() => () => ctl.current?.abort(), []);
  return {
    state,
    reset: () => {
      ctl.current?.abort();
      setState({ phase: "idle" });
    },
    async run(start, label) {
      ctl.current?.abort();
      const mine = new AbortController();
      ctl.current = mine;
      setState({ phase: "running", label });
      try {
        const res = await start();
        if (res.status === 202 || isJobRef(res.body)) {
          const job = await waitForJob((res.body as Schemas["JobRef"]).job_id, { signal: mine.signal });
          if (job.state === "succeeded") setState({ phase: "done", result: (job.result ?? {}) as T, job });
          else setState({ phase: "error", message: jobFailure(job) });
        } else {
          setState({ phase: "done", result: res.body as T, job: null });
        }
      } catch (e) {
        if (e instanceof DOMException && e.name === "AbortError") return;
        setState({ phase: "error", message: e instanceof ApiError ? e.message : e instanceof Error ? e.message : "Request failed" });
      }
    },
  };
}

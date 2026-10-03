import { ApiError, NetworkError, VersionConflictError, request } from "./api";
import { createStore, safeStorage } from "./store";
import { uuid4 } from "./uuid";

/**
 * Pending-write outbox (architecture 2.5). User-authored writes (notes, drafts, layouts, prefs, annotations) are
 * queued with a persisted `Idempotency-Key`, sent in order, and survive reload and server outages. Each stays
 * visible until the server accepts it. A stale `If-Match` (412) becomes an explicit choice: keep mine or use theirs.
 */
export type OutboxState = "pending" | "sending" | "conflict" | "failed";

export interface OutboxEntry {
  /** Idempotency-Key; stable across retries of this exact write. */
  id: string;
  method: "POST" | "PUT" | "DELETE";
  path: string;
  body?: unknown;
  /** Explicit `If-Match`; otherwise the last version seen for `path` (`observe`). */
  ifMatch?: string | null;
  label: string;
  createdAt: number;
  attempts: number;
  state: OutboxState;
  error?: string;
  /** Server copy after a 412. */
  current?: Record<string, unknown>;
  /** Later writes with the same key replace this one while it has not been sent (autosave). */
  coalesceKey?: string;
}

export interface Applied {
  entry: OutboxEntry;
  status: number;
  body: unknown;
}

export interface EnqueueInput {
  method: OutboxEntry["method"];
  path: string;
  body?: unknown;
  ifMatch?: string | null;
  label: string;
  coalesceKey?: string;
}

export interface OutboxDeps {
  storage: Pick<Storage, "getItem" | "setItem"> | null;
  send(entry: OutboxEntry, ifMatch: string | null | undefined): Promise<{ status: number; body: unknown }>;
  now(): number;
  uuid(): string;
  /** Returns a cancel function. */
  schedule(fn: () => void, ms: number): () => void;
  random(): number;
}

const KEY = "coach.outbox.v1";

export const isTransient = (e: unknown): boolean =>
  e instanceof NetworkError || (e instanceof ApiError && (e.status >= 500 || e.status === 408 || e.status === 429));

export function retryDelay(attempts: number, random: () => number): number {
  const base = Math.min(30_000, 1000 * 2 ** Math.max(0, attempts - 1));
  return Math.round(base * (0.8 + random() * 0.4));
}

interface Waiter {
  resolve(a: Applied): void;
  reject(e: unknown): void;
}

export class Outbox {
  readonly entries = createStore<readonly OutboxEntry[]>([]);
  private versions = new Map<string, string>();
  private waiters = new Map<string, Waiter[]>();
  private appliedListeners = new Set<(a: Applied) => void>();
  private draining = false;
  private cancelRetry: (() => void) | null = null;

  constructor(private readonly deps: OutboxDeps) {
    this.entries.set(this.load());
  }

  /** Record the version of a resource just read, so later writes to `path` send the right `If-Match`. */
  observe(path: string, version: number | string | null | undefined): void {
    if (version === null || version === undefined) return;
    this.versions.set(path, String(version));
  }

  versionOf(path: string): string | undefined {
    return this.versions.get(path);
  }

  onApplied(listener: (a: Applied) => void): () => void {
    this.appliedListeners.add(listener);
    return () => this.appliedListeners.delete(listener);
  }

  pendingFor(pathPrefix: string): readonly OutboxEntry[] {
    return this.entries.get().filter((e) => e.path.startsWith(pathPrefix));
  }

  enqueue(input: EnqueueInput): Promise<Applied> {
    const entry: OutboxEntry = {
      id: this.deps.uuid(),
      method: input.method,
      path: input.path,
      body: input.body,
      ifMatch: input.ifMatch,
      label: input.label,
      createdAt: this.deps.now(),
      attempts: 0,
      state: "pending",
      coalesceKey: input.coalesceKey,
    };
    const promise = new Promise<Applied>((resolve, reject) => {
      this.waiters.set(entry.id, [{ resolve, reject }]);
    });
    promise.catch(() => undefined);
    const list = [...this.entries.get()];
    const at = input.coalesceKey ? list.findIndex((e) => e.coalesceKey === input.coalesceKey && e.state === "pending") : -1;
    if (at >= 0) {
      const old = list[at]!;
      // The replaced write never reaches the server; whoever awaited it gets the replacement's result.
      this.waiters.set(entry.id, [...(this.waiters.get(old.id) ?? []), ...(this.waiters.get(entry.id) ?? [])]);
      this.waiters.delete(old.id);
      list[at] = { ...entry, createdAt: old.createdAt };
    } else {
      list.push(entry);
    }
    this.commit(list);
    void this.drain();
    return promise;
  }

  async drain(): Promise<void> {
    if (this.draining) return;
    this.draining = true;
    this.cancelRetry?.();
    this.cancelRetry = null;
    try {
      for (;;) {
        const entry = this.next();
        if (!entry) break;
        this.patch(entry.id, { state: "sending", attempts: entry.attempts + 1 });
        const sending = this.entries.get().find((e) => e.id === entry.id)!;
        const ifMatch = sending.ifMatch !== undefined ? sending.ifMatch : sending.method === "POST" ? undefined : this.versions.get(sending.path);
        try {
          const res = await this.deps.send(sending, ifMatch);
          this.finish(sending, res);
        } catch (e) {
          if (e instanceof VersionConflictError) {
            this.patch(sending.id, { state: "conflict", current: e.current, error: "The server has a newer version." });
            continue;
          }
          if (isTransient(e)) {
            this.patch(sending.id, { state: "pending", error: e instanceof Error ? e.message : "Unreachable" });
            this.cancelRetry = this.deps.schedule(() => void this.drain(), retryDelay(sending.attempts, this.deps.random));
            break;
          }
          const message = e instanceof ApiError ? `${e.message} (${e.status})` : e instanceof Error ? e.message : "Failed";
          this.patch(sending.id, { state: "failed", error: message });
        }
      }
    } finally {
      this.draining = false;
    }
  }

  /** Re-send a conflicted write over the server's current version. */
  keepMine(id: string): void {
    const e = this.entries.get().find((x) => x.id === id);
    if (!e || e.state !== "conflict") return;
    const version = e.current && "version" in e.current ? String(e.current.version) : undefined;
    if (version !== undefined) this.versions.set(e.path, version);
    // A fresh key: the first attempt was refused, so it must not replay as a stored 412.
    this.patch(id, { id: this.deps.uuid(), state: "pending", ifMatch: undefined, current: undefined, error: undefined, attempts: 0 });
    void this.drain();
  }

  /** Drop a conflicted or failed write and keep what the server has. Returns the server copy, if any. */
  useTheirs(id: string): Record<string, unknown> | undefined {
    const e = this.entries.get().find((x) => x.id === id);
    if (!e) return undefined;
    if (e.current && "version" in e.current) this.versions.set(e.path, String(e.current.version));
    this.remove(id);
    this.rejectWaiters(id, new Error("discarded"));
    return e.current;
  }

  retry(id: string): void {
    this.patch(id, { state: "pending", error: undefined });
    void this.drain();
  }

  // ---- internals

  private next(): OutboxEntry | undefined {
    const blocked = new Set<string>();
    for (const e of this.entries.get()) {
      if (e.state === "conflict" || e.state === "failed") blocked.add(e.path);
      else if (e.state === "pending" && !blocked.has(e.path)) return e;
    }
    return undefined;
  }

  private finish(entry: OutboxEntry, res: { status: number; body: unknown }): void {
    this.remove(entry.id);
    const body = res.body as { version?: unknown } | undefined;
    if (body && typeof body === "object" && "version" in body && body.version !== undefined) {
      this.versions.set(entry.path, String(body.version));
    } else if (entry.method === "DELETE") {
      this.versions.delete(entry.path);
    }
    const applied: Applied = { entry, status: res.status, body: res.body };
    for (const w of this.waiters.get(entry.id) ?? []) w.resolve(applied);
    this.waiters.delete(entry.id);
    for (const l of [...this.appliedListeners]) l(applied);
  }

  private rejectWaiters(id: string, error: unknown): void {
    for (const w of this.waiters.get(id) ?? []) w.reject(error);
    this.waiters.delete(id);
  }

  private patch(id: string, change: Partial<OutboxEntry>): void {
    const list = this.entries.get().map((e) => (e.id === id ? { ...e, ...change } : e));
    if (change.id && change.id !== id) {
      const w = this.waiters.get(id);
      if (w) {
        this.waiters.set(change.id, w);
        this.waiters.delete(id);
      }
    }
    this.commit(list);
  }

  private remove(id: string): void {
    this.commit(this.entries.get().filter((e) => e.id !== id));
  }

  private commit(list: OutboxEntry[]): void {
    this.entries.set(list);
    try {
      this.deps.storage?.setItem(KEY, JSON.stringify(list));
    } catch {
      /* the queue stays in memory */
    }
  }

  private load(): OutboxEntry[] {
    try {
      const raw = this.deps.storage?.getItem(KEY);
      if (!raw) return [];
      const list = JSON.parse(raw) as OutboxEntry[];
      if (!Array.isArray(list)) return [];
      // A write that was in flight when the page closed is retried under the same key; the server dedups.
      return list.filter((e) => e && typeof e.id === "string").map((e) => (e.state === "sending" ? { ...e, state: "pending" as const } : e));
    } catch {
      return [];
    }
  }
}

function browserDeps(): OutboxDeps {
  return {
    storage: safeStorage,
    send: (entry, ifMatch) =>
      request(entry.method, entry.path, entry.body, { idempotencyKey: entry.id, ifMatch }).then((r) => ({ status: r.status, body: r.body })),
    now: () => Date.now(),
    uuid: uuid4,
    schedule: (fn, ms) => {
      const t = setTimeout(fn, ms);
      return () => clearTimeout(t);
    },
    random: Math.random,
  };
}

/** The tab's outbox. */
export const outbox = new Outbox(browserDeps());

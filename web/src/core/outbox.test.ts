import { describe, expect, it } from "vitest";
import { ApiError, NetworkError, VersionConflictError } from "./api";
import { type OutboxDeps, type OutboxEntry, Outbox, retryDelay } from "./outbox";

type Reply = { status: number; body: unknown } | Error;

function harness(script: (entry: OutboxEntry, ifMatch: string | null | undefined, n: number) => Reply) {
  const store = new Map<string, string>();
  const sent: { id: string; method: string; path: string; ifMatch: string | null | undefined; body: unknown }[] = [];
  const timers: { fn: () => void; ms: number }[] = [];
  let n = 0;
  let u = 0;
  const deps: OutboxDeps = {
    storage: { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => void store.set(k, v) },
    send: async (entry, ifMatch) => {
      sent.push({ id: entry.id, method: entry.method, path: entry.path, ifMatch, body: entry.body });
      const r = script(entry, ifMatch, ++n);
      if (r instanceof Error) throw r;
      return r;
    },
    now: () => 1000,
    uuid: () => `key-${++u}`,
    schedule: (fn, ms) => {
      timers.push({ fn, ms });
      return () => undefined;
    },
    random: () => 0.5,
  };
  return { deps, store, sent, timers, make: () => new Outbox(deps) };
}

const flush = () => new Promise((r) => setTimeout(r, 0));

describe("outbox", () => {
  it("sends writes in order under their own idempotency keys and removes them when accepted", async () => {
    const h = harness(() => ({ status: 200, body: { version: 2 } }));
    const box = h.make();
    const a = box.enqueue({ method: "PUT", path: "/a", body: { x: 1 }, label: "A" });
    const b = box.enqueue({ method: "POST", path: "/b", body: { y: 1 }, label: "B" });
    await Promise.all([a, b]);
    expect(h.sent.map((s) => [s.id, s.path])).toEqual([
      ["key-1", "/a"],
      ["key-2", "/b"],
    ]);
    expect(box.entries.get()).toHaveLength(0);
    expect(box.versionOf("/a")).toBe("2");
  });

  it("uses the last observed version as If-Match for PUT and DELETE, never for POST", async () => {
    const h = harness(() => ({ status: 200, body: {} }));
    const box = h.make();
    box.observe("/r", 7);
    await box.enqueue({ method: "PUT", path: "/r", body: {}, label: "r" });
    await box.enqueue({ method: "POST", path: "/r", body: {}, label: "r" });
    await box.enqueue({ method: "DELETE", path: "/r", ifMatch: "9", label: "r" });
    expect(h.sent.map((s) => s.ifMatch)).toEqual(["7", undefined, "9"]);
  });

  it("keeps a write through an outage, retries under the same key with backoff, and reports it pending", async () => {
    let up = false;
    const h = harness(() => (up ? { status: 201, body: {} } : new NetworkError()));
    const box = h.make();
    const done = box.enqueue({ method: "POST", path: "/notes", body: { body: "hi" }, label: "Note" });
    await flush();
    expect(box.entries.get()[0]).toMatchObject({ state: "pending", attempts: 1 });
    expect(h.timers).toHaveLength(1);
    expect(h.timers[0]!.ms).toBe(retryDelay(1, () => 0.5));
    up = true;
    h.timers[0]!.fn();
    await done;
    expect(h.sent.map((s) => s.id)).toEqual(["key-1", "key-1"]);
    expect(box.entries.get()).toHaveLength(0);
  });

  it("survives a reload: persisted entries resume, an in-flight one is retried under its key", async () => {
    const h = harness(() => new NetworkError());
    const first = h.make();
    void first.enqueue({ method: "POST", path: "/notes", body: { body: "x" }, label: "Note" });
    await flush();
    const raw = JSON.parse(h.store.get("coach.outbox.v1")!) as OutboxEntry[];
    raw[0]!.state = "sending";
    h.store.set("coach.outbox.v1", JSON.stringify(raw));
    const second = new Outbox({ ...h.deps, send: async () => ({ status: 201, body: {} }) });
    expect(second.entries.get()[0]).toMatchObject({ id: "key-1", state: "pending" });
    await second.drain();
    expect(second.entries.get()).toHaveLength(0);
  });

  it("coalesces autosaves that have not been sent into the newest body", async () => {
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    const h = harness(() => ({ status: 200, body: { version: 3 } }));
    const slow: OutboxDeps = {
      ...h.deps,
      send: async (e, m) => {
        if (e.path === "/first") await gate;
        return h.deps.send(e, m);
      },
    };
    const box = new Outbox(slow);
    void box.enqueue({ method: "PUT", path: "/first", body: {}, label: "first" });
    const a = box.enqueue({ method: "PUT", path: "/draft", body: { v: 1 }, label: "draft", coalesceKey: "d" });
    const b = box.enqueue({ method: "PUT", path: "/draft", body: { v: 2 }, label: "draft", coalesceKey: "d" });
    release();
    await Promise.all([a, b]);
    expect(h.sent.filter((s) => s.path === "/draft").map((s) => s.body)).toEqual([{ v: 2 }]);
  });

  it("turns a 412 into an explicit choice and holds later writes to the same resource", async () => {
    const h = harness((e) => (e.path === "/r" && e.attempts === 1 && h.sent.filter((s) => s.path === "/r").length === 1 ? new VersionConflictError({ version: 5, body: "theirs" }) : { status: 200, body: { version: 6 } }));
    const box = h.make();
    void box.enqueue({ method: "PUT", path: "/r", body: { v: "mine" }, label: "r" });
    void box.enqueue({ method: "PUT", path: "/r", body: { v: "later" }, label: "r2" });
    void box.enqueue({ method: "PUT", path: "/other", body: {}, label: "o" });
    await flush();
    expect(box.entries.get().map((e) => [e.path, e.state])).toEqual([
      ["/r", "conflict"],
      ["/r", "pending"],
    ]);
    box.keepMine(box.entries.get()[0]!.id);
    await flush();
    await flush();
    const rSends = h.sent.filter((s) => s.path === "/r");
    expect(rSends[1]!.ifMatch).toBe("5");
    expect(rSends[1]!.id).not.toBe(rSends[0]!.id);
    expect(box.entries.get()).toHaveLength(0);
  });

  it("use-theirs drops the write and returns the server copy", async () => {
    const h = harness(() => new VersionConflictError({ version: 5 }));
    const box = h.make();
    void box.enqueue({ method: "PUT", path: "/r", body: {}, label: "r" });
    await flush();
    const copy = box.useTheirs(box.entries.get()[0]!.id);
    expect(copy).toEqual({ version: 5 });
    expect(box.entries.get()).toHaveLength(0);
    expect(box.versionOf("/r")).toBe("5");
  });

  it("marks a rejected write failed instead of retrying it forever, and lets the user retry", async () => {
    let reject = true;
    const h = harness(() => (reject ? new ApiError(422, "invalid_request", "Too long") : { status: 200, body: {} }));
    const box = h.make();
    void box.enqueue({ method: "POST", path: "/n", body: {}, label: "n" });
    await flush();
    expect(box.entries.get()[0]).toMatchObject({ state: "failed" });
    expect(h.timers).toHaveLength(0);
    reject = false;
    box.retry(box.entries.get()[0]!.id);
    await flush();
    expect(box.entries.get()).toHaveLength(0);
  });

  it("treats 5xx as an outage", async () => {
    const h = harness(() => new ApiError(503, "unavailable", "down"));
    const box = h.make();
    void box.enqueue({ method: "POST", path: "/n", body: {}, label: "n" });
    await flush();
    expect(box.entries.get()[0]).toMatchObject({ state: "pending" });
  });

  it("notifies listeners when a write is applied", async () => {
    const h = harness(() => ({ status: 201, body: { note_id: 4, version: 1 } }));
    const box = h.make();
    const seen: unknown[] = [];
    box.onApplied((a) => seen.push(a.body));
    await box.enqueue({ method: "POST", path: "/n", body: {}, label: "n" });
    expect(seen).toEqual([{ note_id: 4, version: 1 }]);
  });
});

describe("retryDelay", () => {
  it("doubles from 1 s to 30 s with 20 percent jitter", () => {
    expect(retryDelay(1, () => 0.5)).toBe(1000);
    expect(retryDelay(4, () => 0.5)).toBe(8000);
    expect(retryDelay(9, () => 0.5)).toBe(30000);
    expect(retryDelay(1, () => 0)).toBe(800);
    expect(retryDelay(1, () => 1)).toBe(1200);
  });
});

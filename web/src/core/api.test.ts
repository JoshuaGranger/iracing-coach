import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, NetworkError, VersionConflictError, paths, reachable, request } from "./api";

const reply = (status: number, body: unknown, headers: Record<string, string> = {}) =>
  new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...headers } });

afterEach(() => vi.unstubAllGlobals());

describe("api client", () => {
  it("sends idempotency and if-match headers and returns the parsed body", async () => {
    const fetchMock = vi.fn(async (_url: string, _init: RequestInit) => reply(200, { ok: true }));
    vi.stubGlobal("fetch", fetchMock);
    const r = await request("PUT", "/x", { a: 1 }, { idempotencyKey: "k1", ifMatch: "3" });
    expect(r.body).toEqual({ ok: true });
    const init = fetchMock.mock.calls[0]![1];
    expect(init.headers).toMatchObject({ "Idempotency-Key": "k1", "If-Match": "3", "Content-Type": "application/json" });
    expect(init.body).toBe('{"a":1}');
  });

  it("maps 412 to a version conflict carrying the server copy", async () => {
    vi.stubGlobal("fetch", async () => reply(412, { error: "version_conflict", current: { version: 4 } }));
    const e = await request("PUT", "/x", {}).catch((x: unknown) => x);
    expect(e).toBeInstanceOf(VersionConflictError);
    expect((e as VersionConflictError).current).toEqual({ version: 4 });
  });

  it("maps error bodies to ApiError with the stable code", async () => {
    vi.stubGlobal("fetch", async () => reply(409, { error: "projection_pending", message: "Building" }, { "Retry-After": "3" }));
    const e = await request("GET", "/x").catch((x: unknown) => x);
    expect(e).toBeInstanceOf(ApiError);
    expect(e).toMatchObject({ status: 409, code: "projection_pending", retryAfter: 3 });
  });

  it("reports an unreachable server and recovers", async () => {
    vi.stubGlobal("fetch", async () => {
      throw new TypeError("fetch failed");
    });
    await expect(request("GET", "/x")).rejects.toBeInstanceOf(NetworkError);
    expect(reachable.get()).toBe(false);
    vi.stubGlobal("fetch", async () => reply(200, {}));
    await request("GET", "/x");
    expect(reachable.get()).toBe(true);
  });

  it("handles 204", async () => {
    vi.stubGlobal("fetch", async () => new Response(null, { status: 204 }));
    expect((await request("DELETE", "/x")).status).toBe(204);
  });

  it("builds paths with encoded ids and only the filters that are set", () => {
    expect(paths.sessions(1, { type: ["race"], q: "", limit: 50 })).toBe("/api/v1/drivers/1/sessions?type=race&limit=50");
    expect(paths.sessions(1, { car: ["a", "b"], series: [], setup: "fixed" })).toBe("/api/v1/drivers/1/sessions?car=a&car=b&setup=fixed");
    expect(paths.setups(1, { raced: false })).toBe("/api/v1/drivers/1/setups?raced=false");
    expect(paths.tuning("a/b")).toBe("/api/v1/analyses/a%2Fb/tuning");
    expect(paths.notes(1, "setup", "9")).toBe("/api/v1/drivers/1/notes?subject_kind=setup&subject_id=9");
  });
});

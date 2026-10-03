import { describe, expect, it } from "vitest";
import { clearCache, invalidate, invalidateKey, isFresh, loadShared, onInvalidate, setCached } from "./resource";

describe("shared loads", () => {
  it("serves readers that mount together from one request, and loads again once it has finished", async () => {
    let calls = 0;
    const load = async () => ++calls;
    const [a, b, c] = await Promise.all([loadShared("/api/v1/drivers", load), loadShared("/api/v1/drivers", load), loadShared("/api/v1/drivers", load)]);
    expect([a, b, c]).toEqual([1, 1, 1]);
    expect(await loadShared("/api/v1/drivers", load)).toBe(2);
  });

  it("keeps different keys apart and lets a failure be retried", async () => {
    const fail = loadShared("/x", () => Promise.reject(new Error("down")));
    await expect(fail).rejects.toThrow("down");
    expect(await loadShared("/x", async () => "ok")).toBe("ok");
    expect(await Promise.all([loadShared("/y", async () => 1), loadShared("/z", async () => 2)])).toEqual([1, 2]);
  });
});

describe("freshness", () => {
  it("treats a value as fresh for a few seconds after it was stored", () => {
    clearCache();
    setCached("/k", 1);
    expect(isFresh("/k")).toBe(true);
    expect(isFresh("/k", Date.now() + 10_000)).toBe(false);
    expect(isFresh("/other")).toBe(false);
  });
});

describe("invalidation", () => {
  const keys = ["/api/v1/drivers", "/api/v1/drivers/7/prefs", "/api/v1/drivers/7/sessions", "/api/v1/sessions/3"];
  const reached = (fire: () => void) => {
    let matches: (k: string) => boolean = () => false;
    const off = onInvalidate((m) => (matches = m));
    fire();
    off();
    return keys.filter(matches);
  };

  it("reaches every path below a prefix", () => {
    expect(reached(() => invalidate("/api/v1/drivers"))).toEqual(keys.slice(0, 3));
  });

  it("reaches only the named key, not the resources below the list path", () => {
    expect(reached(() => invalidateKey("/api/v1/drivers"))).toEqual(["/api/v1/drivers"]);
  });
});

import { describe, expect, it } from "vitest";
import { CACHE_MAX_BYTES, CACHE_MAX_ENTRIES, createAnalysisCache } from "../cache";

describe("analysis cache", () => {
  it("defaults to 3 analyses and 96 MB", () => {
    expect(CACHE_MAX_ENTRIES).toBe(3);
    expect(CACHE_MAX_BYTES).toBe(96 * 1024 * 1024);
  });

  it("evicts the least recently used entry beyond three analyses", () => {
    const cache = createAnalysisCache<string>();
    cache.set("a", "A", 10);
    cache.set("b", "B", 10);
    cache.set("c", "C", 10);
    expect(cache.get("a")).toBe("A");
    cache.set("d", "D", 10);
    expect(cache.keys()).toEqual(["c", "a", "d"]);
    expect(cache.has("b")).toBe(false);
  });

  it("evicts by bytes and refuses an entry larger than the whole budget", () => {
    const cache = createAnalysisCache<string>();
    cache.set("a", "A", 40 * 1024 * 1024);
    cache.set("b", "B", 40 * 1024 * 1024);
    cache.set("c", "C", 40 * 1024 * 1024);
    expect(cache.keys()).toEqual(["b", "c"]);
    expect(cache.totalBytes).toBe(80 * 1024 * 1024);
    expect(cache.set("huge", "H", CACHE_MAX_BYTES + 1)).toBe(false);
    expect(cache.has("huge")).toBe(false);
    expect(cache.size).toBe(2);
  });

  it("re-measures an entry that grew and drops others, then itself, to stay within budget", () => {
    const cache = createAnalysisCache<string>({ maxBytes: 100 });
    cache.set("a", "A", 30);
    cache.set("b", "B", 30);
    cache.resize("b", 90);
    expect(cache.keys()).toEqual(["b"]);
    cache.resize("b", 150);
    expect(cache.size).toBe(0);
    expect(cache.totalBytes).toBe(0);
  });

  it("replacing a key does not double-count its bytes", () => {
    const cache = createAnalysisCache<string>({ maxBytes: 100 });
    cache.set("a", "A", 60);
    cache.set("a", "A2", 70);
    expect(cache.totalBytes).toBe(70);
    expect(cache.get("a")).toBe("A2");
  });
});

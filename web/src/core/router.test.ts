import { describe, expect, it } from "vitest";
import { formatRoute, parseHash } from "./router";

describe("router", () => {
  it("parses every page", () => {
    expect(parseHash("#/")).toMatchObject({ page: "drivers" });
    expect(parseHash("")).toMatchObject({ page: "drivers" });
    expect(parseHash("#/d/42/sessions")).toMatchObject({ page: "sessions", customerId: 42 });
    expect(parseHash("#/d/42/s/7")).toMatchObject({ page: "investigation", customerId: 42, sessionId: 7, view: "race" });
    expect(parseHash("#/d/42/s/7/traces?comparison=c1&section=s3")).toMatchObject({ view: "traces", query: { comparison: "c1", section: "s3" } });
    expect(parseHash("#/d/42/setups")).toMatchObject({ page: "setups" });
    expect(parseHash("#/d/42/setups/baseline")).toMatchObject({ page: "baseline" });
    expect(parseHash("#/d/42/setups/9")).toMatchObject({ page: "setup", setupId: 9 });
    expect(parseHash("#/d/42/tuning")).toMatchObject({ page: "tuning", analysisId: null });
    expect(parseHash("#/d/42/tuning/an%2F1")).toMatchObject({ page: "tuning", analysisId: "an/1" });
    expect(parseHash("#/d/42/live")).toMatchObject({ page: "live" });
  });

  it("rejects malformed routes", () => {
    for (const h of ["#/x", "#/d/abc/sessions", "#/d/1/s/2/nope", "#/d/1/s/x", "#/d/1/live/extra", "#/d/1/setups/1/2"]) {
      expect(parseHash(h).page).toBe("notfound");
    }
  });

  it("round-trips routes with queries", () => {
    const r = { page: "investigation", customerId: 1, sessionId: 2, view: "sections" } as const;
    const h = formatRoute(r, { section: "s 1", comparison: null });
    expect(h).toBe("#/d/1/s/2/sections?section=s+1");
    expect(parseHash(h)).toMatchObject({ ...r, query: { section: "s 1" } });
    expect(parseHash(formatRoute({ page: "tuning", customerId: 1, analysisId: "a/b c" }))).toMatchObject({ analysisId: "a/b c" });
  });
});

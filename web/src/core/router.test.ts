import { afterEach, describe, expect, it, vi } from "vitest";
import { formatRoute, onHashChange, parseHash, setConfirmLeave, setLeaveGuard, subscribeRoute } from "./router";

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
    expect(parseHash("#/d/42/setups/files/source%3Aab12")).toMatchObject({ page: "setupFile", customerId: 42, fileId: "source:ab12" });
    expect(parseHash("#/d/42/setups/files/coach%3A3?version=2")).toMatchObject({ page: "setupFile", fileId: "coach:3", query: { version: "2" } });
    expect(parseHash("#/d/42/tuning")).toMatchObject({ page: "tuning", analysisId: null });
    expect(parseHash("#/d/42/tuning/an%2F1")).toMatchObject({ page: "tuning", analysisId: "an/1" });
    expect(parseHash("#/d/42/live")).toMatchObject({ page: "live" });
    expect(parseHash("#/__kit")).toMatchObject({ page: "kit" });
    expect(formatRoute({ page: "kit" })).toBe("#/__kit");
  });

  it("rejects malformed routes", () => {
    for (const h of ["#/x", "#/d/abc/sessions", "#/d/1/s/2/nope", "#/d/1/s/x", "#/d/1/live/extra", "#/d/1/setups/1/2", "#/d/1/setups/files", "#/d/1/setups/files/", "#/d/1/setups/files/a/b"]) {
      expect(parseHash(h).page).toBe("notfound");
    }
  });

  it("round-trips routes with queries", () => {
    const r = { page: "investigation", customerId: 1, sessionId: 2, view: "sections" } as const;
    const h = formatRoute(r, { section: "s 1", comparison: null });
    expect(h).toBe("#/d/1/s/2/sections?section=s+1");
    expect(parseHash(h)).toMatchObject({ ...r, query: { section: "s 1" } });
    expect(parseHash(formatRoute({ page: "tuning", customerId: 1, analysisId: "a/b c" }))).toMatchObject({ analysisId: "a/b c" });
    const file = formatRoute({ page: "setupFile", customerId: 7, fileId: "source:1f/ é" }, { version: "3" });
    expect(parseHash(file)).toMatchObject({ page: "setupFile", customerId: 7, fileId: "source:1f/ é", query: { version: "3" } });
  });
});

describe("leave guard", () => {
  // A minimal browser: the hash, history.replaceState and one hashchange listener.
  function browser(hash: string) {
    const listeners = new Set<() => void>();
    const loc = { hash, pathname: "/", search: "" };
    const replaced: string[] = [];
    vi.stubGlobal("location", loc);
    vi.stubGlobal("history", { state: null, replaceState: (_s: unknown, _t: string, url: string) => { replaced.push(url); loc.hash = url.slice(url.indexOf("#")); } });
    vi.stubGlobal("window", { addEventListener: (_: string, fn: () => void) => listeners.add(fn), removeEventListener: (_: string, fn: () => void) => listeners.delete(fn) });
    const go = (next: string) => {
      loc.hash = next;
      for (const l of [...listeners]) l();
    };
    return { loc, replaced, go, listeners };
  }
  let cleanup: (() => void)[] = [];
  afterEach(() => {
    for (const c of cleanup.reverse()) c();
    cleanup = [];
    vi.unstubAllGlobals();
  });

  it("lets ordinary navigation through without asking", () => {
    const b = browser("#/d/1/setups");
    const seen: string[] = [];
    const ask = vi.fn(() => true);
    setConfirmLeave(ask);
    cleanup.push(subscribeRoute(() => seen.push(b.loc.hash)));
    b.go("#/d/1/sessions");
    b.go("#/d/1/setups/files/coach%3A1");
    expect(seen).toEqual(["#/d/1/sessions", "#/d/1/setups/files/coach%3A1"]);
    expect(ask).not.toHaveBeenCalled();
    expect(b.replaced).toEqual([]);
  });

  it("keeps the page when leaving is declined and goes when it is accepted", () => {
    const b = browser("#/d/1/setups/files/coach%3A1");
    const seen: string[] = [];
    const answers = [false, true];
    const ask = vi.fn(() => answers.shift()!);
    setConfirmLeave(ask);
    cleanup.push(subscribeRoute(() => seen.push(b.loc.hash)));
    cleanup.push(setLeaveGuard((next) => (next.page === "setupFile" ? null : "Discard unsaved changes?")));
    b.go("#/d/1/setups");
    expect(ask).toHaveBeenCalledWith("Discard unsaved changes?");
    expect(b.replaced).toEqual(["/#/d/1/setups/files/coach%3A1"]);
    expect(seen).toEqual([]);
    // Another revision of the same file is not leaving.
    b.go("#/d/1/setups/files/coach%3A1?version=1");
    expect(ask).toHaveBeenCalledTimes(1);
    b.go("#/d/1/sessions");
    expect(ask).toHaveBeenCalledTimes(2);
    expect(seen).toEqual(["#/d/1/setups/files/coach%3A1?version=1", "#/d/1/sessions"]);
  });

  it("a remover only removes its own guard, and a removed guard never asks", () => {
    const b = browser("#/d/1/setups");
    const ask = vi.fn(() => false);
    setConfirmLeave(ask);
    cleanup.push(subscribeRoute(() => undefined));
    const first = setLeaveGuard(() => "first");
    const second = setLeaveGuard(() => "second");
    first();
    b.go("#/d/1/live");
    expect(ask).toHaveBeenCalledWith("second");
    second();
    b.go("#/d/1/sessions");
    expect(ask).toHaveBeenCalledTimes(1);
    expect(b.loc.hash).toBe("#/d/1/sessions");
  });

  it("stops listening when the last reader leaves", () => {
    const b = browser("#/d/1/setups");
    const off = subscribeRoute(() => undefined);
    expect(b.listeners.size).toBe(1);
    off();
    expect(b.listeners.size).toBe(0);
    expect(onHashChange).toBeTypeOf("function");
  });
});

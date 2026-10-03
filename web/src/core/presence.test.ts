import { describe, expect, it } from "vitest";
import type { Schemas } from "./api";
import { createPresenceWatch } from "./presence";

type Collector = Schemas["CollectorPresence"];

function collector(over: { online?: boolean; sim?: "not_running" | "menus" | "in_session"; driver?: number | null } = {}): Collector {
  const { online = true, sim = "in_session", driver = 7 } = over;
  return {
    collector_id: "c1",
    machine: "SIM",
    collector_version: "0.19.0",
    protocol: 1,
    compatible: true,
    online,
    last_seen_at: "2026-10-03T05:00:00Z",
    viewers: 0,
    status: { sim, connected: true, on_track: false, in_garage: false, on_pit_road: false, streaming: false, queue: { uploads_pending: 0, bytes_pending: 0 }, driver: driver === null ? null : { customer_id: driver, name: "D" } },
  } as Collector;
}

const listed = (live: boolean) => [{ customer_id: 7, name: "D", session_count: 1, live }];

describe("presence watch", () => {
  it("leaves the drivers list alone while a collector's status repeats", () => {
    const watch = createPresenceWatch();
    expect(watch(collector(), listed(true))).toBe(false);
    for (let i = 0; i < 5; i++) expect(watch({ ...collector(), last_seen_at: `2026-10-03T05:00:0${i}Z` }, listed(true))).toBe(false);
  });

  it("refetches the list when online, sim state or driver changes", () => {
    const watch = createPresenceWatch();
    watch(collector(), listed(true));
    expect(watch(collector({ sim: "menus" }), listed(true))).toBe(true);
    expect(watch(collector({ sim: "menus" }), listed(false))).toBe(false);
    expect(watch(collector({ online: false }), listed(false))).toBe(true);
    expect(watch(collector({ driver: 8 }), listed(false))).toBe(true);
  });

  it("compares a collector seen first with the list already loaded", () => {
    expect(createPresenceWatch()(collector(), listed(true))).toBe(false);
    expect(createPresenceWatch()(collector(), listed(false))).toBe(true);
    expect(createPresenceWatch()(collector({ sim: "menus" }), listed(false))).toBe(false);
    expect(createPresenceWatch()(collector({ driver: null }), listed(false))).toBe(false);
    expect(createPresenceWatch()(collector(), undefined)).toBe(false);
  });
});

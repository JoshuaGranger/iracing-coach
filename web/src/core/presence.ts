import type { Schemas } from "./api";

type Collector = Schemas["CollectorPresence"];
type Driver = Schemas["Driver"];

/**
 * The server sends one `presence` event per collector status, every 5 s, most of them unchanged. Only what the
 * driver list shows (`Driver.live`: an online collector reports the driver in a session) can make it stale, so the
 * list is refetched when a collector's online flag, sim state or driver changes. A collector seen for the first
 * time is compared with the list already loaded.
 */
export function createPresenceWatch(): (collector: Collector, listed: readonly Driver[] | undefined) => boolean {
  const seen = new Map<string, string>();
  return (collector, listed) => {
    const driver = collector.status?.driver?.customer_id ?? null;
    const live = collector.online && collector.status?.sim === "in_session";
    const digest = `${collector.online}|${collector.status?.sim ?? ""}|${driver ?? ""}`;
    const before = seen.get(collector.collector_id);
    seen.set(collector.collector_id, digest);
    if (before !== undefined) return before !== digest;
    if (!listed || driver === null) return false;
    const entry = listed.find((d) => d.customer_id === driver);
    return !entry || entry.live !== live;
  };
}

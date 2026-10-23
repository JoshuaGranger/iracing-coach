import type { ComponentChildren } from "preact";
import { useEffect } from "preact/hooks";
import { type Schemas, api, paths, reachable } from "./core/api";
import { outbox } from "./core/outbox";
import { usePrefs, useReducedMotion } from "./core/prefs";
import { createPresenceWatch } from "./core/presence";
import { cachedValue, invalidate, invalidateKey, useResource } from "./core/resource";
import { type ParsedRoute, href, navigate, useRoute } from "./core/router";
import { events } from "./core/sse";
import { readJson, useStore, writeJson } from "./core/store";
import { DriversPage } from "./pages/drivers";
import { SessionsPage } from "./pages/sessions";
import { InvestigationSlot } from "./ui/investigation-slot";
import { Notice } from "./ui/kit";
import { Lazy } from "./ui/lazy";
import { OutboxBar } from "./ui/outbox-bar";

const loadSetups = () => import("./pages/setups");
const loadSetupDetail = () => import("./pages/setup-detail");
const loadSetupFile = () => import("./pages/setup-file");
const loadBaseline = () => import("./pages/baseline");
const loadTuning = () => import("./pages/tuning");
const loadLive = () => import("./pages/live");
const loadKit = () => import("./pages/kit");

const LAST_DRIVER = "coach.last-driver";

/** Server events make mounted readers refetch; reconnecting refetches everything and flushes the outbox. */
function useShellEvents(): void {
  useEffect(() => {
    events.start();
    const timers = new Map<string, ReturnType<typeof setTimeout>>();
    const soon = (prefix: string, exact = false) => {
      if (timers.has(prefix)) return;
      timers.set(
        prefix,
        setTimeout(() => {
          timers.delete(prefix);
          if (exact) invalidateKey(prefix);
          else invalidate(prefix);
        }, 400),
      );
    };
    const presenceChanged = createPresenceWatch();
    const offs = [
      events.on("library_changed", (e) => {
        const base = `/api/v1/drivers/${e.customer_id}`;
        if (e.scope === "sessions") {
          soon(`${base}/sessions`);
          soon("/api/v1/sessions/");
        } else if (e.scope === "setups") {
          soon(`${base}/setups`);
          soon(`${base}/sto-files`);
          soon("/api/v1/setups/");
        } else if (e.scope === "layouts") {
          soon(`${base}/live-layouts`);
          soon(`${base}/trace-layouts`);
        } else if (e.scope === "notes") soon(`${base}/notes`);
        else if (e.scope === "prefs") soon(`${base}/prefs`);
      }),
      events.on("analysis_ready", (e) => {
        soon(`/api/v1/drivers/${e.customer_id}/sessions`);
        soon("/api/v1/sessions/");
      }),
      events.on("recording_state", (e) => {
        if (e.customer_id != null) soon(`/api/v1/drivers/${e.customer_id}/sessions`);
      }),
      // The drivers path is a prefix of every driver resource; only the list itself depends on presence.
      events.on("presence", (e) => {
        if (presenceChanged(e.collector, cachedValue<Schemas["DriverList"]>(paths.drivers)?.drivers)) soon(paths.drivers, true);
      }),
      events.onReopen(() => {
        invalidate("/api/v1/");
        void outbox.drain();
      }),
    ];
    const online = () => void outbox.drain();
    const visible = () => document.visibilityState === "visible" && void outbox.drain();
    window.addEventListener("online", online);
    document.addEventListener("visibilitychange", visible);
    return () => {
      offs.forEach((f) => f());
      window.removeEventListener("online", online);
      document.removeEventListener("visibilitychange", visible);
      events.stop();
    };
  }, []);
}

function driverIdFor(route: ParsedRoute): number | null {
  return "customerId" in route ? route.customerId : null;
}

type CurrentDriver = ReturnType<typeof useCurrentDriver>;

/** The driver the app is showing: the route's, else the only one, else the last one used. One reader of the drivers list for the whole shell. */
function useCurrentDriver(route: ParsedRoute) {
  const drivers = useResource(paths.drivers, () => api.drivers());
  const list = drivers.data?.drivers ?? [];
  const fromRoute = driverIdFor(route);
  const stored = readJson<number | null>(LAST_DRIVER, null);
  const current = fromRoute ?? (list.length === 1 ? list[0]!.customer_id : stored && list.some((d) => d.customer_id === stored) ? stored : null);
  useEffect(() => {
    if (fromRoute !== null) writeJson(LAST_DRIVER, fromRoute);
  }, [fromRoute]);
  return { list, current, driver: list.find((d) => d.customer_id === current) };
}

function TopBar({ route, who }: { route: ParsedRoute; who: CurrentDriver }) {
  const { list, current, driver } = who;
  const page = route.page === "investigation" ? "sessions" : route.page === "setup" || route.page === "setupFile" || route.page === "baseline" || route.page === "tuning" ? "setups" : route.page;
  const link = (name: "sessions" | "setups" | "live", label: string) =>
    current === null ? null : (
      <a class={page === name ? "nav-link active" : "nav-link"} href={href({ page: name, customerId: current })} aria-current={page === name ? "page" : undefined}>
        {label}
        {name === "live" && driver?.live ? <span class="live-dot" role="img" aria-label="Live now" title="Live now" /> : null}
      </a>
    );
  return (
    <header class="topbar">
      <a class="brand" href={href({ page: "drivers" })}>
        <svg width="28" height="17" viewBox="0 0 28 17" aria-hidden="true">
          <path d="M5 13V6.5C5 4 7 3 9 3h9c2.6 0 4.5 1 5.3 2.7.8 1.800.2 3.600-1.500 4.600L16 13.600c-1 .6-2 .4-3-.2L5 13z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" />
        </svg>
        iRacing Coach
      </a>
      <nav aria-label="Main">
        {link("sessions", "Sessions")}
        {link("setups", "Setups")}
        {link("live", "Live")}
      </nav>
      {list.length > 1 ? (
        <label class="driver-switch">
          <span class="visually-hidden">Driver</span>
          <select
            value={current ?? ""}
            onChange={(e) => navigate({ page: "sessions", customerId: Number((e.currentTarget as HTMLSelectElement).value) })}
          >
            {current === null ? <option value="">Driver</option> : null}
            {list.map((d) => (
              <option key={d.customer_id} value={d.customer_id}>
                {d.name}
              </option>
            ))}
          </select>
        </label>
      ) : driver ? (
        <span class="driver-name">{driver.name}</span>
      ) : null}
    </header>
  );
}

function Banner() {
  const ok = useStore(reachable);
  const entries = useStore(outbox.entries);
  if (ok) return null;
  const n = entries.length;
  return (
    <p class="banner" role="status" data-testid="offline-banner">
      Server unreachable{n ? ` · ${n} ${n === 1 ? "change" : "changes"} waiting` : ""}
    </p>
  );
}

function CompanionDownload() {
  const description = "Install iRacing Coach Companion on your Windows sim PC to collect telemetry and garage setups, show the live monitor, and start at Windows sign-in.";
  return (
    <footer class="companion-footer">
      <a class="companion-download" href="/downloads/iRacingCoachCompanionSetup.exe" download="iRacingCoachCompanionSetup.exe" title={description} aria-describedby="companion-description">
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M8 2v8m-3-3 3 3 3-3M3 11v3h10v-3" />
        </svg>
        Download companion
      </a>
      <span class="companion-platform">Windows sim PC</span>
      <span id="companion-description" class="visually-hidden">{description}</span>
    </footer>
  );
}

function ThemeBinding({ route }: { route: ParsedRoute }) {
  const id = driverIdFor(route);
  const { prefs } = usePrefs(id);
  const reduced = useReducedMotion(id ? prefs : null);
  useEffect(() => {
    document.documentElement.dataset.motion = reduced ? "reduced" : "full";
  }, [reduced]);
  return null;
}

/** `?version=n` of a setup file; anything else reads the current revision. */
function versionOf(query: Record<string, string>): number | null {
  const v = query.version;
  return v && /^[1-9]\d{0,8}$/.test(v) ? Number(v) : null;
}

function renderPage(route: ParsedRoute) {
    switch (route.page) {
      case "drivers":
        return <DriversPage />;
      case "sessions":
        return <SessionsPage customerId={route.customerId} />;
      case "investigation":
        return <InvestigationSlot customerId={route.customerId} sessionId={route.sessionId} view={route.view} query={route.query} />;
      case "setups":
        return <Lazy load={loadSetups} props={{ customerId: route.customerId }} what="setups" />;
      case "setup":
        return <Lazy load={loadSetupDetail} props={{ customerId: route.customerId, setupId: route.setupId }} what="setup" />;
      case "setupFile":
        return <Lazy load={loadSetupFile} props={{ customerId: route.customerId, fileId: route.fileId, version: versionOf(route.query) }} what="setup file" />;
      case "baseline":
        return <Lazy load={loadBaseline} props={{ customerId: route.customerId }} what="baseline finder" />;
      case "tuning":
        return <Lazy load={loadTuning} props={{ customerId: route.customerId, analysisId: route.analysisId }} what="tuning" />;
      case "live":
        return <Lazy load={loadLive} props={{ customerId: route.customerId }} what="live" />;
      case "kit":
        return <Lazy load={loadKit} props={{}} what="components" />;
      default:
        return <Notice>Page not found.</Notice>;
    }
}

/**
 * The top bar and the bindings that follow the current driver. They hold the one reader of the drivers list, so a presence change
 * re-renders them and not the page, which arrives as `children` made by `App`.
 */
function Chrome({ route, children }: { route: ParsedRoute; children: ComponentChildren }) {
  const who = useCurrentDriver(route);
  return (
    <>
      <ThemeBinding route={route} />
      <TopBar route={route} who={who} />
      {children}
    </>
  );
}

export function App() {
  useShellEvents();
  const route = useRoute();
  const page = renderPage(route);
  return (
    <div class="shell">
      <Chrome route={route}>
        <Banner />
        <OutboxBar />
        <main id="main" class={route.page === "investigation" ? "main main-wide" : "main"}>
          <div key={route.page} class="page">
            {page}
          </div>
        </main>
        {route.page !== "investigation" ? <CompanionDownload /> : null}
      </Chrome>
    </div>
  );
}

import { useEffect } from "preact/hooks";
import { api, paths, reachable } from "./core/api";
import { outbox } from "./core/outbox";
import { usePrefs, useReducedMotion } from "./core/prefs";
import { invalidate, useResource } from "./core/resource";
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
const loadBaseline = () => import("./pages/baseline");
const loadTuning = () => import("./pages/tuning");
const loadLive = () => import("./pages/live");

const LAST_DRIVER = "coach.last-driver";

/** Server events make mounted readers refetch; reconnecting refetches everything and flushes the outbox. */
function useShellEvents(): void {
  useEffect(() => {
    events.start();
    const timers = new Map<string, ReturnType<typeof setTimeout>>();
    const soon = (prefix: string) => {
      if (timers.has(prefix)) return;
      timers.set(
        prefix,
        setTimeout(() => {
          timers.delete(prefix);
          invalidate(prefix);
        }, 400),
      );
    };
    const offs = [
      events.on("library_changed", (e) => {
        const base = `/api/v1/drivers/${e.customer_id}`;
        if (e.scope === "sessions") {
          soon(`${base}/sessions`);
          soon("/api/v1/sessions/");
        } else if (e.scope === "setups") {
          soon(`${base}/setups`);
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
      events.on("presence", () => soon(paths.drivers)),
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

function TopBar({ route }: { route: ParsedRoute }) {
  const drivers = useResource(paths.drivers, () => api.drivers());
  const list = drivers.data?.drivers ?? [];
  const fromRoute = driverIdFor(route);
  const stored = readJson<number | null>(LAST_DRIVER, null);
  const current = fromRoute ?? (list.length === 1 ? list[0]!.customer_id : stored && list.some((d) => d.customer_id === stored) ? stored : null);
  useEffect(() => {
    if (fromRoute !== null) writeJson(LAST_DRIVER, fromRoute);
  }, [fromRoute]);
  const driver = list.find((d) => d.customer_id === current);
  const page = route.page === "investigation" ? "sessions" : route.page === "setup" || route.page === "baseline" || route.page === "tuning" ? "setups" : route.page;
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
      Server unreachable. Loaded data stays open{n ? `; ${n} ${n === 1 ? "change is" : "changes are"} waiting to save` : ""}.
    </p>
  );
}

function ThemeBinding({ route }: { route: ParsedRoute }) {
  const id = driverIdFor(route);
  const { prefs } = usePrefs(id ?? 0);
  const reduced = useReducedMotion(id ? prefs : null);
  useEffect(() => {
    document.documentElement.dataset.motion = reduced ? "reduced" : "full";
  }, [reduced]);
  return null;
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
      case "baseline":
        return <Lazy load={loadBaseline} props={{ customerId: route.customerId }} what="baseline finder" />;
      case "tuning":
        return <Lazy load={loadTuning} props={{ customerId: route.customerId, analysisId: route.analysisId }} what="tuning" />;
      case "live":
        return <Lazy load={loadLive} props={{ customerId: route.customerId }} what="live" />;
      default:
        return <Notice>Page not found.</Notice>;
    }
}

export function App() {
  useShellEvents();
  const route = useRoute();
  const page = renderPage(route);
  return (
    <div class="shell">
      <ThemeBinding route={route} />
      <TopBar route={route} />
      <Banner />
      <OutboxBar />
      <main id="main" class={route.page === "investigation" ? "main main-wide" : "main"}>
        <div key={route.page} class="page">
          {page}
        </div>
      </main>
    </div>
  );
}

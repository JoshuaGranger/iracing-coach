import { useEffect, useMemo, useState } from "preact/hooks";
import { type Schemas, api, paths } from "../core/api";
import { DASH, formatDate, formatLapTime, humanize, plural, sessionTypeLabel, trackLabel } from "../core/format";
import { waitForJob } from "../core/jobs";
import { getLastInvestigation, usePrefs } from "../core/prefs";
import { invalidate, useDebounced, useResource } from "../core/resource";
import { href } from "../core/router";
import { readJson, writeJson } from "../core/store";
import { Chip, Empty, ErrorLine, Loading, Notice, PageHead, type Tone } from "../ui/kit";

type Session = Schemas["SessionSummary"];
type TypeFilter = "all" | "race" | "qualify" | "practice" | "test";
const TYPES: readonly [TypeFilter, string][] = [
  ["all", "All"],
  ["race", "Races"],
  ["qualify", "Qualifying"],
  ["practice", "Practice"],
  ["test", "Tests"],
];
const FILTER_KEY = "coach.sessions.type";
const PAGE = 50;

export function groupEvents(sessions: readonly Session[]): { key: string; head: Session; sessions: Session[] }[] {
  const order: string[] = [];
  const byKey = new Map<string, Session[]>();
  for (const s of sessions) {
    let list = byKey.get(s.event_key);
    if (!list) {
      byKey.set(s.event_key, (list = []));
      order.push(s.event_key);
    }
    list.push(s);
  }
  return order.map((key) => {
    const list = byKey.get(key)!.slice().sort((a, b) => (a.sim_session_num ?? 0) - (b.sim_session_num ?? 0));
    return { key, head: list[list.length - 1]!, sessions: list };
  });
}

export function analysisChip(s: Session): { tone: Tone; text: string } {
  switch (s.analysis_state) {
    case "ready":
      return { tone: "good", text: "Analyzed" };
    case "running":
      return { tone: "info", text: "Analyzing" };
    case "queued":
      return { tone: "info", text: "Queued" };
    case "failed":
      return { tone: "bad", text: s.analysis_reason ? `Failed: ${humanize(s.analysis_reason).toLowerCase()}` : "Failed" };
    case "deferred":
      return { tone: "warn", text: s.analysis_reason ? `Deferred: ${humanize(s.analysis_reason).toLowerCase()}` : "Deferred" };
    default:
      return { tone: "neutral", text: "Not analyzed" };
  }
}

function figures(s: Session): string {
  const o = s.overview;
  const parts: string[] = [];
  if (o.starting_position != null || o.final_position != null) parts.push(`${o.starting_position != null ? `P${o.starting_position}` : DASH} to ${o.final_position != null ? `P${o.final_position}` : DASH}`);
  if (o.best_clean_lap_s != null) parts.push(`best ${formatLapTime(o.best_clean_lap_s)}`);
  if (o.recorded_laps != null) parts.push(plural(o.recorded_laps, "lap"));
  if (o.pit_stops_detected != null) parts.push(plural(o.pit_stops_detected, "stop"));
  if (s.recording_count > 1) parts.push(`${s.recording_count} recordings`);
  return parts.join(" · ");
}

function SessionRow({ s, customerId }: { s: Session; customerId: number }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const chip = analysisChip(s);
  const open = s.analysis_state === "ready";
  const body = (
    <>
      <span class="row-main">
        <Chip>{sessionTypeLabel(s.session_type)}</Chip>
        <span class="row-title">{s.car_name ?? s.car_path ?? "Car unknown"}</span>
      </span>
      <span class="row-meta">{figures(s)}</span>
    </>
  );
  return (
    <li class="session-row" data-state={s.analysis_state}>
      {open ? (
        <a class="row-link" href={href({ page: "investigation", customerId, sessionId: s.session_id, view: "race" })}>
          {body}
        </a>
      ) : (
        <div class="row-link row-static">{body}</div>
      )}
      <span class="row-state">
        <Chip tone={chip.tone}>{chip.text}</Chip>
        {s.analysis_state === "none" || s.analysis_state === "failed" ? (
          <button
            type="button"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              setError(null);
              try {
                const queued = await api.queueAnalysis(s.session_id);
                invalidate(`/api/v1/drivers/${customerId}/sessions`);
                // The stream says when it is ready; polling the job covers an event missed while it was down.
                void waitForJob(queued.body.job_id).then(() => invalidate(`/api/v1/drivers/${customerId}/sessions`));
              } catch (e) {
                setError(e instanceof Error ? e.message : "Could not queue");
              } finally {
                setBusy(false);
              }
            }}
          >
            {s.analysis_state === "failed" ? "Retry" : "Analyze"}
          </button>
        ) : null}
        {error ? <span class="inline-error">{error}</span> : null}
      </span>
    </li>
  );
}

function Resume({ customerId }: { customerId: number }) {
  const { prefs } = usePrefs(customerId);
  const last = getLastInvestigation(customerId, prefs?.last_investigation);
  const detail = useResource(last ? paths.session(last.session_id) : null, () => api.session(last!.session_id));
  if (!last) return null;
  const s = detail.data?.session;
  if (detail.data === undefined && !detail.error) return null;
  if (!s) return null;
  const query: Record<string, string> = {};
  if (last.comparison_id) query.comparison = last.comparison_id;
  if (last.section_id) query.section = last.section_id;
  return (
    <a class="resume" href={href({ page: "investigation", customerId, sessionId: last.session_id, view: last.view }, query)}>
      <span class="resume-label">Resume</span>
      <span>
        {sessionTypeLabel(s.session_type)} · {trackLabel(s.track, s.track_config)} · {formatDate(s.start_time_utc)} · {humanize(last.view)}
      </span>
    </a>
  );
}

export function SessionsPage({ customerId }: { customerId: number }) {
  const [type, setType] = useState<TypeFilter>(() => readJson<TypeFilter>(FILTER_KEY, "all"));
  const [text, setText] = useState("");
  const q = useDebounced(text.trim(), 250);
  useEffect(() => writeJson(FILTER_KEY, type), [type]);

  const key = paths.sessions(customerId, { type: type === "all" ? undefined : type, q, limit: PAGE });
  const first = useResource(key, (signal) => api.sessions(customerId, { type: type === "all" ? undefined : type, q, limit: PAGE }, { signal }));
  const [more, setMore] = useState<{ sessions: Session[]; cursor: string | null } | null>(null);
  const [moreBusy, setMoreBusy] = useState(false);
  useEffect(() => setMore(null), [first.data, key]);

  const sessions = useMemo(() => [...(first.data?.sessions ?? []), ...(more?.sessions ?? [])], [first.data, more]);
  const cursor = more ? more.cursor : first.data?.next_cursor ?? null;
  const events = useMemo(() => groupEvents(sessions), [sessions]);

  const drivers = useResource(paths.drivers, () => api.drivers());
  const live = drivers.data?.drivers.find((d) => d.customer_id === customerId)?.live;

  async function loadMore() {
    if (!cursor) return;
    setMoreBusy(true);
    try {
      const page = await api.sessions(customerId, { type: type === "all" ? undefined : type, q, limit: PAGE, cursor });
      setMore({ sessions: [...(more?.sessions ?? []), ...page.sessions], cursor: page.next_cursor ?? null });
    } finally {
      setMoreBusy(false);
    }
  }

  return (
    <>
      <PageHead title="Sessions">
        {live ? (
          <a class="btn btn-accent" href={href({ page: "live", customerId })}>
            Live now
          </a>
        ) : null}
      </PageHead>
      <Resume customerId={customerId} />
      <div class="filters" role="search">
        <div class="seg" role="group" aria-label="Session type">
          {TYPES.map(([id, label]) => (
            <button key={id} type="button" aria-pressed={type === id} class={type === id ? "on" : ""} onClick={() => setType(id)}>
              {label}
            </button>
          ))}
        </div>
        <label class="search">
          <span class="visually-hidden">Search track, car or series</span>
          <input type="search" placeholder="Track, car, series" value={text} onInput={(e) => setText((e.currentTarget as HTMLInputElement).value)} />
        </label>
      </div>
      {first.error && !first.data ? <ErrorLine error={first.error} /> : null}
      {first.error && first.data ? <Notice tone="warn">Could not refresh: {first.error.message}</Notice> : null}
      {!first.data && !first.error ? <Loading what="sessions" /> : null}
      {first.data && sessions.length === 0 ? (
        <Empty title={q || type !== "all" ? "No matching sessions" : "No sessions yet"}>
          {q || type !== "all" ? "Clear the filters to see everything." : "Drive with the collector running; recordings appear here when iRacing finishes them."}
        </Empty>
      ) : null}
      <div class="events">
        {events.map((ev) => (
          <section key={ev.key} class="event" aria-label={trackLabel(ev.head.track, ev.head.track_config)}>
            <header class="event-head">
              <h2>{trackLabel(ev.head.track, ev.head.track_config)}</h2>
              <span class="event-meta">
                {[ev.head.series_name, formatDate(ev.head.start_time_utc)].filter(Boolean).join(" · ")}
              </span>
            </header>
            <ul>
              {ev.sessions.map((s) => (
                <SessionRow key={s.session_id} s={s} customerId={customerId} />
              ))}
            </ul>
          </section>
        ))}
      </div>
      {cursor ? (
        <button type="button" class="btn load-more" disabled={moreBusy} onClick={loadMore}>
          {moreBusy ? "Loading" : "Older sessions"}
        </button>
      ) : null}
    </>
  );
}

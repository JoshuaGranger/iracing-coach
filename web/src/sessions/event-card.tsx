import type { ComponentChildren } from "preact";
import { useState } from "preact/hooks";
import { api } from "../core/api";
import { formatLapTime, plural, trackLabel } from "../core/format";
import { waitForJob } from "../core/jobs";
import { invalidate } from "../core/resource";
import { href } from "../core/router";
import { localDay, localDayTime, localTime } from "./dates";
import {
  type EventGroup,
  NONE,
  type Session,
  type SetupType,
  analysisException,
  bestLap,
  carKey,
  carName,
  formatPace,
  formatPosition,
  hasFigures,
  isOpenable,
  isWin,
  paceTone,
  phaseOf,
  positionChange,
  resultBand,
  resultText,
  seriesLabel,
  setupTypeOf,
} from "./model";
import { openingTab } from "./tab-memory";
import { TrackSilhouette } from "./track-silhouette";

export function SetupBadge({ type }: { type: SetupType }) {
  return type === "Fixed" ? (
    <span class="sx-badge fixed" title="Fixed setup">
      <svg width="13" height="13" viewBox="0 0 14 14" aria-hidden="true">
        <rect x="2.5" y="6" width="9" height="6.5" rx="1.4" fill="currentColor" />
        <path d="M4.5 6V4.3a2.5 2.5 0 015 0V6" fill="none" stroke="currentColor" stroke-width="1.5" />
      </svg>
      Fixed
    </span>
  ) : (
    <span class="sx-badge open" title="Open setup">
      <svg width="13" height="13" viewBox="0 0 14 14" aria-hidden="true">
        <path d="M9.6 1.4a3.2 3.2 0 00-3.9 4.1L1.6 9.6a1.3 1.3 0 001.8 1.8l4.1-4.1a3.2 3.2 0 004.1-3.9L9.8 5.2 8.3 4.9 8 3.4z" fill="currentColor" />
      </svg>
      Open
    </span>
  );
}

function Fig({ label, tone, children }: { label: string; tone?: string; children: ComponentChildren }) {
  return (
    <div class="sx-fig">
      <span class="sx-fig-l">{label}</span>
      <b class={tone ? `sx-fig-v ${tone}` : "sx-fig-v"}>{children}</b>
    </div>
  );
}

const count = (n: number | null | undefined) => (n == null ? null : n);

function Figures({ s, eventBest }: { s: Session; eventBest: number | null }) {
  const o = s.overview;
  const green = count(o.green_laps_estimated);
  const caution = count(o.caution_laps_estimated);
  const best = bestLap(s);
  const pace = paceTone(o.pace_slope_s_per_lap);
  const stops = count(o.pit_stops_detected);
  const run = count(o.longest_green_run);
  const fuel = o.fuel_used_gal;
  const laps = count(o.recorded_laps);
  return (
    <div class="sx-stats">
      <Fig label="Green" tone={green == null || green === 0 ? "none" : undefined}>
        <i class={green ? "sx-dot g" : "sx-dot zero"} aria-hidden="true" />
        <span>{green ?? NONE}</span>
      </Fig>
      <Fig label="Caution" tone={caution == null || caution === 0 ? "none" : undefined}>
        <i class={caution ? "sx-dot y" : "sx-dot zero"} aria-hidden="true" />
        <span>{caution ?? NONE}</span>
      </Fig>
      <Fig label="Best" tone={best === null ? "none" : eventBest !== null && best === eventBest ? "best" : undefined}>
        {best === null ? NONE : formatLapTime(best)}
      </Fig>
      <Fig label="Pace" tone={pace === null ? "none" : `pace-${pace}`}>
        {formatPace(o.pace_slope_s_per_lap)}
      </Fig>
      <Fig label="Stops" tone={stops == null || stops === 0 ? "none" : "pit"}>
        {stops ?? NONE}
      </Fig>
      <Fig label="Long run" tone={run == null ? "none" : undefined}>
        {run ?? NONE}
      </Fig>
      <Fig label="Fuel" tone={fuel == null ? "none" : undefined}>
        {fuel == null ? (
          NONE
        ) : (
          <>
            <span class="fuel">{fuel.toFixed(1)}</span>
            <small>gal</small>
          </>
        )}
      </Fig>
      <Fig label="Laps" tone={laps == null ? "none" : undefined}>
        {laps ?? NONE}
      </Fig>
    </div>
  );
}

function Hero({ s }: { s: Session }) {
  const figures = hasFigures(s);
  const race = phaseOf(s.session_type).code === "R";
  if (!figures) {
    const laps = s.overview.recorded_laps;
    return (
      <div class="sx-hero">
        <span class="sx-big dim sx-laps">{laps != null ? plural(laps, "lap") : NONE}</span>
      </div>
    );
  }
  const change = positionChange(s);
  return (
    <div class="sx-hero">
      <span class={race ? "sx-big" : "sx-big dim"}>{formatPosition(s.overview.final_position)}</span>
      {change && change.gained !== 0 ? (
        <div class="sx-sub">
          <span class={change.gained > 0 ? "sx-chg gain" : "sx-chg loss"}>
            <span class="sx-tri" aria-hidden="true">
              {change.gained > 0 ? "▲" : "▼"}
            </span>
            {Math.abs(change.gained)}
          </span>
          <span class="sx-from">from P{change.from}</span>
        </div>
      ) : null}
      {isWin(s) ? (
        <span class="sx-win">
          <i class="sx-flag" aria-hidden="true" />
          Win
        </span>
      ) : null}
    </div>
  );
}

function Action({ s, customerId, label }: { s: Session; customerId: number; label: string }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <>
      <button
        type="button"
        class="sx-act"
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
        {label}
      </button>
      {error ? <span class="sx-error">{error}</span> : null}
    </>
  );
}

export function SessionTile({ s, ev, customerId, now }: { s: Session; ev: EventGroup; customerId: number; now: Date }) {
  const phase = phaseOf(s.session_type);
  const ex = analysisException(s);
  const band = resultBand(s);
  const type = setupTypeOf(s);
  const time = localTime(s.start_time_utc);
  const open = isOpenable(s);
  const name = `${phase.label}, ${trackLabel(s.track, s.track_config)}${localDayTime(s.start_time_utc, now) ? `, ${localDayTime(s.start_time_utc, now)}` : ""}`;
  const title = phase.code === "R" ? resultText(s) : undefined;
  const body = (
    <>
      <div class="sx-band">
        <b>{phase.label}</b>
        <span>
          {type !== null && type !== ev.setupType ? <SetupBadge type={type} /> : null}
          {[time, s.recording_count > 1 ? `${s.recording_count} rec` : null].filter(Boolean).join(" · ")}
        </span>
      </div>
      {band ? <i class={`sx-rb ${band}`} aria-hidden="true" /> : null}
      <Hero s={s} />
      {ex ? (
        <div class="sx-state-row">
          <span class={`sx-state ${ex.kind}`}>
            {ex.kind === "analyzing" ? (
              <svg class="sx-spin" width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">
                <circle cx="7" cy="7" r="5" fill="none" stroke="currentColor" stroke-opacity=".35" stroke-width="2" />
                <path d="M7 2a5 5 0 015 5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
              </svg>
            ) : null}
            {ex.text}
          </span>
          {ex.action ? <Action s={s} customerId={customerId} label={ex.action} /> : null}
        </div>
      ) : null}
      {hasFigures(s) ? <Figures s={s} eventBest={ev.bestLapS} /> : null}
    </>
  );
  return (
    <li class="session-row sx-tile" data-state={s.analysis_state} data-phase={phase.code} data-session={s.session_id}>
      {open ? (
        <a class="sx-tile-body sx-open" href={tileHref(customerId, s)} aria-label={name} title={title}>
          {body}
        </a>
      ) : (
        <div class="sx-tile-body" role="group" aria-label={name}>
          {body}
        </div>
      )}
    </li>
  );
}

function tileHref(customerId: number, s: Session): string {
  const { view, query } = openingTab(customerId, s.session_id);
  return href({ page: "investigation", customerId, sessionId: s.session_id, view }, query);
}

export function EventCard({ ev, customerId, colors, now }: { ev: EventGroup; customerId: number; colors: ReadonlyMap<string, number>; now: Date }) {
  const head = ev.head;
  const car = colors.get(carKey(head)) ?? 0;
  const series = seriesLabel(head);
  const day = localDay(ev.start, now);
  const trackKey = trackLabel(head.track, head.track_config);
  const analysisId = ev.sessions.find((s) => isOpenable(s) && s.analysis_id)?.analysis_id ?? null;
  return (
    <section class="sx-event" data-car={car} aria-label={trackKey}>
      <header class="sx-id">
        <div class="sx-id-top">
          <div class="sx-id-title">
            <h2>{head.track ?? "Track unknown"}</h2>
            <div class="sx-meta">{[head.track_config, day].filter(Boolean).join(" · ")}</div>
          </div>
          {head.track ? <TrackSilhouette trackKey={trackKey} analysisId={analysisId} /> : null}
        </div>
        <div class="sx-id-bot">
          <span class="sx-car">
            <i class="sx-stripe" aria-hidden="true" />
            {carName(head)}
          </span>
          {series || ev.setupType ? (
            <span class="sx-ser">
              {series}
              {ev.setupType ? <SetupBadge type={ev.setupType} /> : null}
            </span>
          ) : null}
        </div>
      </header>
      <ul class="sx-tiles">
        {ev.sessions.map((s) => (
          <SessionTile key={s.session_id} s={s} ev={ev} customerId={customerId} now={now} />
        ))}
      </ul>
    </section>
  );
}

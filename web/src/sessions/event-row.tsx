import { useState } from "preact/hooks";
import { api } from "../core/api";
import { trackLabel } from "../core/format";
import { waitForJob } from "../core/jobs";
import { invalidate } from "../core/resource";
import { href } from "../core/router";
import { Chip } from "../ui/kit";
import { localDay, localDayTime, localTime } from "./dates";
import {
  type ColumnCode,
  type EventGroup,
  NONE,
  type Session,
  type SetupType,
  type Slot,
  analysisException,
  carKey,
  carName,
  cellKind,
  cellSlots,
  gridTemplate,
  hasFigures,
  isOpenable,
  phaseColumns,
  phaseOf,
  seriesLabel,
  setupTypeOf,
} from "./model";
import { openingTab } from "./tab-memory";
import { TrackSilhouette } from "./track-silhouette";

/** Fixed or Open: the one constraint chip of an event (the chip rule). A phase that differs from its event carries its own. */
export function SetupBadge({ type }: { type: SetupType }) {
  return (
    <Chip variant="constraint" title={type === "Fixed" ? "Fixed setup" : "Open setup"}>
      {type === "Fixed" ? (
        <svg width="12" height="12" viewBox="0 0 14 14" aria-hidden="true">
          <rect x="2.5" y="6" width="9" height="6.5" rx="1.4" fill="currentColor" />
          <path d="M4.5 6V4.3a2.5 2.5 0 015 0V6" fill="none" stroke="currentColor" stroke-width="1.5" />
        </svg>
      ) : (
        <svg width="12" height="12" viewBox="0 0 14 14" aria-hidden="true">
          <path d="M9.6 1.4a3.2 3.2 0 00-3.9 4.1L1.6 9.6a1.3 1.3 0 001.8 1.8l4.1-4.1a3.2 3.2 0 004.1-3.9L9.8 5.2 8.3 4.9 8 3.4z" fill="currentColor" />
        </svg>
      )}
      {type}
    </Chip>
  );
}

/**
 * One labelled figure. `quiet` is the practice and qualifying type (backlog #33): secondary size and colour, its own classes so the
 * type audit holds it to the 14 px secondary minimum and the race's figures to the 15 px one.
 */
function SlotView({ slot, quiet }: { slot: Slot; quiet: boolean }) {
  const v = quiet ? "sx-qv" : "sx-v";
  const p = quiet ? "sx-qp" : "sx-p";
  return (
    <div class={slot.missing ? "sx-slot none" : "sx-slot"} data-slot={slot.key} title={slot.title}>
      <span class="sx-l">{slot.label}</span>
      <b class={slot.eventBest ? `${v} event-best` : v} data-event-best={slot.eventBest ? "true" : undefined}>
        {slot.parts.map((part, i) => (
          <span key={i} class={part.tone ? `${p} ${part.tone}` : p}>
            {part.text}
          </span>
        ))}
      </b>
    </div>
  );
}

/** Queued, Deferred, Failed and Not analyzed: named only while they explain missing results. Failed and Not analyzed carry their verb. */
function ExceptionView({ s, customerId }: { s: Session; customerId: number }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ex = analysisException(s);
  if (!ex) return null;
  const level = ex.kind === "failed" ? "fail" : "wait";
  const action = ex.action
    ? {
        label: ex.action,
        disabled: busy,
        onClick: async () => {
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
        },
      }
    : undefined;
  return (
    <div class="sx-state-row">
      <Chip variant="exception" level={level} action={action}>
        {ex.text}
      </Chip>
      {error ? <span class="sx-error">{error}</span> : null}
    </div>
  );
}

function tileHref(customerId: number, s: Session): string {
  const { view, query } = openingTab(customerId, s.session_id);
  return href({ page: "investigation", customerId, sessionId: s.session_id, view }, query);
}

/**
 * One phase of an event, its own panel: a header slot and the labelled slots of its kind (race seven in a 4 x 2 grid; practice four
 * and qualifying three, in quieter type). The whole cell opens the investigation when it can. A session with no figures shows its
 * lap count and the one exception that explains it.
 */
export function PhaseCell({ s, ev, customerId, now, column }: { s: Session; ev: EventGroup; customerId: number; now: Date; column: number }) {
  const phase = phaseOf(s.session_type);
  const type = setupTypeOf(s);
  const time = localTime(s.start_time_utc);
  const open = isOpenable(s);
  const figures = hasFigures(s);
  const when = localDayTime(s.start_time_utc, now);
  const name = `${phase.label}, ${trackLabel(s.track, s.track_config)}${when ? `, ${when}` : ""}`;
  const laps = s.overview.recorded_laps;
  const kind = cellKind(s);
  const quiet = kind !== "race";
  const body = (
    <>
      <div class="sx-slots" data-figures={figures ? "true" : "false"}>
        <div class="sx-slot sx-head">
          <span class="sx-l">{[time, s.recording_count > 1 ? `${s.recording_count} rec` : null].filter(Boolean).join(" · ") || NONE}</span>
          <b class={quiet ? "sx-qv sx-phase" : "sx-v sx-phase"}>
            {phase.label}
            {type !== null && type !== ev.setupType ? <SetupBadge type={type} /> : null}
          </b>
        </div>
        {figures ? (
          cellSlots(s, ev.bestLapS).map((slot) => <SlotView key={slot.key} slot={slot} quiet={quiet} />)
        ) : (
          <div class="sx-slot" data-slot="recorded">
            <span class="sx-l">Laps</span>
            <b class={quiet ? "sx-qv" : "sx-v"}>
              <span class={quiet ? "sx-qp" : "sx-p"}>{laps != null ? String(laps) : NONE}</span>
            </b>
          </div>
        )}
      </div>
      <ExceptionView s={s} customerId={customerId} />
    </>
  );
  return (
    <li class="session-row sx-cell" data-state={s.analysis_state} data-phase={phase.code} data-kind={kind} data-session={s.session_id} style={{ "--col": column + 1 }}>
      {open ? (
        <a class="sx-cell-body sx-open" href={tileHref(customerId, s)} aria-label={name}>
          {body}
        </a>
      ) : (
        <div class="sx-cell-body" role="group" aria-label={name}>
          {body}
        </div>
      )}
    </li>
  );
}

/**
 * One compact row per event, three blocks: identity at the left behind a divider, then each phase its own panel across a gutter, the
 * race widest (stacked rows on a phone).
 */
export function EventRow({ ev, customerId, colors, now, layout }: { ev: EventGroup; customerId: number; colors: ReadonlyMap<string, number>; now: Date; layout: readonly ColumnCode[] }) {
  const head = ev.head;
  const car = colors.get(carKey(head)) ?? 0;
  const series = seriesLabel(head);
  const day = localDay(ev.start, now);
  const trackKey = trackLabel(head.track, head.track_config);
  const analysisId = ev.sessions.find((s) => isOpenable(s) && s.analysis_id)?.analysis_id ?? null;
  const { columns, tracks } = phaseColumns(ev.sessions, layout);
  return (
    <section class="sx-event" data-car={car} aria-label={trackKey}>
      <div class="sx-row">
        <header class="sx-id">
          <div class="sx-id-title">
            {head.track ? <TrackSilhouette trackKey={trackKey} analysisId={analysisId} /> : null}
            <h2>{head.track ?? "Track unknown"}</h2>
          </div>
          <div class="sx-meta">
            <span>{[head.track_config, day].filter(Boolean).join(" · ")}</span>
            {ev.setupType ? <SetupBadge type={ev.setupType} /> : null}
          </div>
          <div class="sx-car">
            <i class="sx-stripe" aria-hidden="true" />
            <span title={series ? `${carName(head)} · ${series}` : undefined}>{carName(head)}</span>
          </div>
        </header>
        <ul class="sx-cells" style={{ "--tracks": gridTemplate(tracks) }}>
          {ev.sessions.map((s, i) => (
            <PhaseCell key={s.session_id} s={s} ev={ev} customerId={customerId} now={now} column={columns[i] ?? i} />
          ))}
        </ul>
      </div>
    </section>
  );
}

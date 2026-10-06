import { useState } from "preact/hooks";
import { api } from "../core/api";
import { trackLabel } from "../core/format";
import { carAccent } from "../core/palette";
import { waitForJob } from "../core/jobs";
import { invalidate } from "../core/resource";
import { href } from "../core/router";
import { Chip } from "../ui/kit";
import { localDay, localDayTime, localTime } from "./dates";
import {
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
  hasFigures,
  isOpenable,
  phaseOf,
  seriesLabel,
  setupTypeOf,
} from "./model";
import { openingTab } from "./tab-memory";
import { TrackSilhouette } from "./track-silhouette";

export const SETUP_WORDS: Record<SetupType, string> = { Fixed: "Fixed setup", Open: "Open setup" };

/** The lock alone (backlog #51): closed for a fixed setup, open for an open one. Hidden from assistive technology: its holder names it. */
export function LockIcon({ type }: { type: SetupType }) {
  return (
    <svg class="sx-lock-icon" width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">
      <rect x="2.5" y="6" width="9" height="6.5" rx="1.4" fill="currentColor" />
      {type === "Fixed" ? (
        <path d="M4.5 6V4.3a2.5 2.5 0 015 0V6" fill="none" stroke="currentColor" stroke-width="1.5" />
      ) : (
        <path d="M4.5 6V4.3a2.5 2.5 0 014.9-.7" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
      )}
    </svg>
  );
}

/** Fixed or Open as a glyph with its meaning on hover and for assistive technology. */
export function SetupLock({ type, class: cls }: { type: SetupType; class?: string }) {
  return (
    <span class={cls ? `sx-lock ${cls}` : "sx-lock"} role="img" aria-label={SETUP_WORDS[type]} title={SETUP_WORDS[type]} data-setup={type}>
      <LockIcon type={type} />
    </span>
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
          <span key={i} class={part.tone ? `${p} ${part.tone}` : p} title={part.title} role={part.title ? "img" : undefined} aria-label={part.title}>
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
 * One phase of an event as a full-width line (backlog #72): the badge, the phase name and its time on one baseline at the left, then
 * the labelled figures of its kind evenly spaced across the line (race seven, practice four, qualifying three; practice and
 * qualifying in quieter type). Below a line's own width of 56 rem the head takes its own row and the figures wrap under it. The
 * whole line opens the investigation when it can. A session with no figures shows its lap count and the one exception that
 * explains it.
 */
export function PhaseLine({ s, ev, customerId, now }: { s: Session; ev: EventGroup; customerId: number; now: Date }) {
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
  const badge = phase.code === "W" || phase.code === "T" ? "P" : phase.code;
  const body = (
    <>
      <div class="sx-slots" data-figures={figures ? "true" : "false"}>
        <div class="sx-slot sx-head">
          <b class={quiet ? "sx-qv sx-phase" : "sx-v sx-phase"}>
            <span class="sx-badge" aria-hidden="true">
              {badge}
            </span>
            {phase.label}
            {type !== null && type !== ev.setupType ? <SetupLock type={type} /> : null}
          </b>
          <span class="sx-l">{[time, s.recording_count > 1 ? `${s.recording_count} rec` : null].filter(Boolean).join(" · ") || NONE}</span>
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
    <li class="session-row sx-line" data-state={s.analysis_state} data-phase={phase.code} data-kind={kind} data-session={s.session_id}>
      {open ? (
        <a class="sx-line-body sx-open" href={tileHref(customerId, s)} aria-label={name}>
          {body}
        </a>
      ) : (
        <div class="sx-line-body" role="group" aria-label={name}>
          {body}
        </div>
      )}
    </li>
  );
}

/**
 * One card per event (backlog #50), a gap between cards: the identity block at the left behind a divider, vertically centred against
 * the phase lines (#71, #72) it sits beside (above them when the card is under 62 rem wide); the phases are full-width lines stacked
 * in session order, a missing phase has no line (#72). The identity is the silhouette beside three lines on one left edge: the track,
 * `config · date`, the car behind its colour bar; the setup lock sits in the block's top-right corner (#69).
 */
export function EventRow({ ev, customerId, now }: { ev: EventGroup; customerId: number; now: Date }) {
  const head = ev.head;
  const series = seriesLabel(head);
  const day = localDay(ev.start, now);
  const trackKey = trackLabel(head.track, head.track_config);
  const analysisId = ev.sessions.find((s) => isOpenable(s) && s.analysis_id)?.analysis_id ?? null;
  return (
    <section class="sx-event" style={`--car:${carAccent(carKey(head))}`} data-setup={ev.setupType ?? undefined} aria-label={trackKey}>
      <div class="sx-row">
        <header class="sx-id">
          {ev.setupType ? <SetupLock type={ev.setupType} class="sx-card-lock" /> : null}
          <div class="sx-id-body">
            {head.track ? <TrackSilhouette trackKey={trackKey} analysisId={analysisId} /> : null}
            <div class="sx-id-text">
              <h2>{head.track ?? "Track unknown"}</h2>
              <div class="sx-meta">
                <span>{[head.track_config, day].filter(Boolean).join(" · ")}</span>
              </div>
              <div class="sx-car">
                <i class="sx-stripe" aria-hidden="true" />
                <span title={series ? `${carName(head)} · ${series}` : undefined}>{carName(head)}</span>
              </div>
            </div>
          </div>
        </header>
        <ul class="sx-lines">
          {ev.sessions.map((s) => (
            <PhaseLine key={s.session_id} s={s} ev={ev} customerId={customerId} now={now} />
          ))}
        </ul>
      </div>
    </section>
  );
}

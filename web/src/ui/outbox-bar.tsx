import { useEffect, useState } from "preact/hooks";
import { outbox, type OutboxEntry } from "../core/outbox";
import { invalidate } from "../core/resource";
import { useStore } from "../core/store";

export function pendingSummary(entries: readonly OutboxEntry[]): string {
  const waiting = entries.filter((entry) => entry.state === "pending" || entry.state === "sending");
  const conflicts = entries.filter((entry) => entry.state === "conflict");
  const failed = entries.filter((entry) => entry.state === "failed");
  const detail = (items: readonly OutboxEntry[], suffix: string) => items.length === 1 ? `${items[0]!.label} ${suffix}` : items.length ? `${items.length} changes ${suffix}` : "";
  return [detail(waiting, "waiting to save"), detail(conflicts, "conflict"), detail(failed, "not saved")].filter(Boolean).join(" · ");
}

/** Pending writes stay visible until the server accepts them; a conflict is an explicit choice. */
export function OutboxBar() {
  const entries = useStore(outbox.entries);
  const [open, setOpen] = useState(false);
  const [waitingVisible, setWaitingVisible] = useState(false);
  useEffect(() => {
    if (entries.length === 0) { setWaitingVisible(false); return; }
    const timer = setTimeout(() => setWaitingVisible(true), 600);
    return () => clearTimeout(timer);
  }, [entries.length === 0]);
  if (entries.length === 0 || (!waitingVisible && !entries.some((entry) => entry.state === "conflict" || entry.state === "failed"))) return null;
  const conflicts = entries.filter((e) => e.state === "conflict");
  const failed = entries.filter((e) => e.state === "failed");
  const summary = pendingSummary(entries);
  const show = open || conflicts.length > 0 || failed.length > 0;
  return (
    <section class="outbox" aria-label="Pending changes" data-testid="outbox">
      <div class="outbox-head">
        <span role="status">{summary}</span>
        {!conflicts.length && !failed.length ? (
          <button type="button" class="link-btn" aria-expanded={show} onClick={() => setOpen(!open)}>
            {show ? "Hide" : "Show"}
          </button>
        ) : null}
      </div>
      {show ? (
        <ul>
          {entries.map((e) => (
            <li key={e.id} data-state={e.state}>
              <span class="outbox-label">
                {e.label}
                <small>
                  {e.state === "pending" ? (e.attempts ? ` retrying: ${e.error ?? "server unreachable"}` : " queued") : null}
                  {e.state === "sending" ? " sending" : null}
                  {e.state === "conflict" ? " changed on the server" : null}
                  {e.state === "failed" ? ` not saved: ${e.error ?? "rejected"}` : null}
                </small>
              </span>
              {e.state === "conflict" ? (
                <span class="outbox-actions">
                  <button type="button" onClick={() => outbox.keepMine(e.id)}>
                    Keep mine
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      outbox.useTheirs(e.id);
                      invalidate(e.path);
                    }}
                  >
                    Use theirs
                  </button>
                </span>
              ) : null}
              {e.state === "failed" ? (
                <span class="outbox-actions">
                  <button type="button" onClick={() => outbox.retry(e.id)}>
                    Retry
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      outbox.useTheirs(e.id);
                      invalidate(e.path);
                    }}
                  >
                    Discard
                  </button>
                </span>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

import { useState } from "preact/hooks";
import { outbox } from "../core/outbox";
import { invalidate } from "../core/resource";
import { useStore } from "../core/store";

/** Pending writes stay visible until the server accepts them; a conflict is an explicit choice. */
export function OutboxBar() {
  const entries = useStore(outbox.entries);
  const [open, setOpen] = useState(false);
  if (entries.length === 0) return null;
  const conflicts = entries.filter((e) => e.state === "conflict");
  const failed = entries.filter((e) => e.state === "failed");
  const waiting = entries.length - conflicts.length - failed.length;
  const summary = [
    waiting ? `${waiting} ${waiting === 1 ? "change" : "changes"} waiting to save` : "",
    conflicts.length ? `${conflicts.length} ${conflicts.length === 1 ? "conflict" : "conflicts"}` : "",
    failed.length ? `${failed.length} not saved` : "",
  ]
    .filter(Boolean)
    .join(" · ");
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
                  {e.state === "pending" ? (e.attempts ? " waiting for the server" : " queued") : null}
                  {e.state === "sending" ? " sending" : null}
                  {e.state === "conflict" ? " changed on the server meanwhile" : null}
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

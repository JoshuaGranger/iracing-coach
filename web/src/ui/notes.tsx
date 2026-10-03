import { useState } from "preact/hooks";
import { type Schemas, api, paths } from "../core/api";
import { formatDateTime } from "../core/format";
import { outbox } from "../core/outbox";
import { invalidate, useResource } from "../core/resource";
import { useStore } from "../core/store";
import { ErrorLine } from "./kit";

type Note = Schemas["Note"];

/**
 * Notes on one subject (a session, setup or analysis). Writes are queued in the outbox with an idempotency key, so a
 * retry after a dropped connection never makes a second note. Shared with the investigation workspace.
 */
export function NotesPanel({ customerId, kind, subjectId, title = "Notes" }: { customerId: number; kind: Schemas["NoteSubjectKind"]; subjectId: string; title?: string }) {
  const key = paths.notes(customerId, kind, subjectId);
  const res = useResource(key, (signal) => api.notes(customerId, kind, subjectId, { signal }));
  const queued = useStore(outbox.entries).filter(
    (e) => e.method === "POST" && e.path === paths.notesCreate(customerId) && (e.body as { subject_id?: string; subject_kind?: string })?.subject_id === subjectId && (e.body as { subject_kind?: string }).subject_kind === kind,
  );
  const [draft, setDraft] = useState("");
  const [editing, setEditing] = useState<number | null>(null);
  const [edit, setEdit] = useState("");
  const [confirming, setConfirming] = useState<number | null>(null);

  const refresh = () => invalidate(key);

  function add() {
    const body = draft.trim();
    if (!body) return;
    setDraft("");
    void outbox
      .enqueue({ method: "POST", path: paths.notesCreate(customerId), body: { subject_kind: kind, subject_id: subjectId, body }, label: "New note" })
      .then(refresh, () => undefined);
  }

  function save(n: Note) {
    const body = edit.trim();
    if (!body) return;
    setEditing(null);
    void outbox
      .enqueue({ method: "PUT", path: paths.note(n.note_id), body: { body }, ifMatch: String(n.version), label: "Edit note", coalesceKey: `note:${n.note_id}` })
      .then(refresh, () => undefined);
  }

  function remove(n: Note) {
    setConfirming(null);
    void outbox.enqueue({ method: "DELETE", path: paths.note(n.note_id), ifMatch: String(n.version), label: "Delete note" }).then(refresh, () => undefined);
  }

  return (
    <section class="notes" aria-label={title}>
      <h2>{title}</h2>
      <ErrorLine error={res.data ? null : res.error} />
      <ul>
        {(res.data?.notes ?? []).map((n) => (
          <li key={n.note_id}>
            {editing === n.note_id ? (
              <>
                <textarea rows={3} maxLength={16384} value={edit} onInput={(e) => setEdit((e.currentTarget as HTMLTextAreaElement).value)} aria-label="Edit note" />
                <div class="row-actions">
                  <button type="button" class="btn btn-accent" onClick={() => save(n)}>
                    Save
                  </button>
                  <button type="button" class="btn" onClick={() => setEditing(null)}>
                    Cancel
                  </button>
                </div>
              </>
            ) : (
              <>
                <p class="note-body">{n.body}</p>
                <div class="note-meta">
                  <span>{formatDateTime(n.updated_at)}</span>
                  <span class="row-actions">
                    <button
                      type="button"
                      class="link-btn"
                      onClick={() => {
                        setEditing(n.note_id);
                        setEdit(n.body);
                      }}
                    >
                      Edit
                    </button>
                    {confirming === n.note_id ? (
                      <button type="button" class="link-btn danger" onClick={() => remove(n)}>
                        Confirm delete
                      </button>
                    ) : (
                      <button type="button" class="link-btn" onClick={() => setConfirming(n.note_id)}>
                        Delete
                      </button>
                    )}
                  </span>
                </div>
              </>
            )}
          </li>
        ))}
        {queued.map((e) => (
          <li key={e.id} class="note-queued">
            <p class="note-body">{(e.body as { body: string }).body}</p>
            <div class="note-meta">
              <span>Waiting to save</span>
            </div>
          </li>
        ))}
      </ul>
      <label class="note-new">
        <span class="visually-hidden">New note</span>
        <textarea rows={2} maxLength={16384} placeholder="Add a note" value={draft} onInput={(e) => setDraft((e.currentTarget as HTMLTextAreaElement).value)} />
      </label>
      <button type="button" class="btn" disabled={!draft.trim()} onClick={add}>
        Add note
      </button>
    </section>
  );
}

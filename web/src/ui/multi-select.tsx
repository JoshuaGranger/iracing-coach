import type { ComponentChildren } from "preact";
import { useEffect, useMemo, useRef, useState } from "preact/hooks";

/**
 * The filter of every library page (design README, Components: Filters): a searchable multi-select dropdown. Sessions (Car,
 * Track, Series, Session, Setup) and Setups (Car) use the same component and look. The dropdown button carries its own
 * selection and clears it from inside its list; there are no chips and no Clear beside the heading (round 3 #35/#36).
 */
export interface Option {
  key: string;
  label: string;
  count?: number;
  /** Identity mark drawn before the label: a car stripe, a phase letter, a Fixed/Open badge. */
  mark?: ComponentChildren;
}

/** The label with the typed text underlined where it matches. */
export function Highlight({ text, query }: { text: string; query: string }) {
  const q = query.trim().toLowerCase();
  const at = q ? text.toLowerCase().indexOf(q) : -1;
  if (at < 0) return <>{text}</>;
  return (
    <>
      {text.slice(0, at)}
      <u>{text.slice(at, at + q.length)}</u>
      {text.slice(at + q.length)}
    </>
  );
}

/**
 * What the dropdown button reads: the label alone while nothing is chosen, `Car · Supra` for one value, `Car · 2` for several.
 * Only values the options still offer count.
 */
export function pillText(label: string, options: readonly Option[], selected: readonly string[]): string {
  const chosen = options.filter((o) => selected.includes(o.key));
  if (chosen.length === 0) return label;
  return chosen.length === 1 ? `${label} · ${chosen[0]!.label}` : `${label} · ${chosen.length}`;
}

export function matches(options: readonly Option[], query: string): Option[] {
  const q = query.trim().toLowerCase();
  return q ? options.filter((o) => o.label.toLowerCase().includes(q)) : options.slice();
}

let uid = 0;

/**
 * A searchable multi-select. The trigger shows what is chosen (`pillText`) at a fixed width; the panel opens on a focused search
 * field, then a Clear row, then checkbox options with their identity mark and count; each choice applies at once and the footer
 * reads `n of m`. The Clear row is always there (disabled while nothing is chosen), so the options never shift when the first
 * one is chosen. Up/Down move, Enter chooses, Escape closes and returns focus to the trigger.
 */
export function MultiSelect({ label, noun, options, selected, onChange }: { label: string; noun: string; options: readonly Option[]; selected: readonly string[]; onChange(next: string[]): void }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const [keyboard, setKeyboard] = useState(false);
  const wrap = useRef<HTMLSpanElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const search = useRef<HTMLInputElement>(null);
  const id = useRef(`ms-${++uid}`).current;
  const shown = useMemo(() => matches(options, query), [options, query]);
  const known = new Set(options.map((o) => o.key));
  const chosen = selected.filter((k) => known.has(k)).length;

  useEffect(() => {
    if (!open) return;
    search.current?.focus();
    const away = (e: Event) => {
      if (wrap.current && !wrap.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", away);
    return () => document.removeEventListener("pointerdown", away);
  }, [open]);
  useEffect(() => setActive(0), [query, open]);

  function close(refocus: boolean) {
    setOpen(false);
    setQuery("");
    if (refocus) trigger.current?.focus();
  }
  function toggle(key: string) {
    onChange(selected.includes(key) ? selected.filter((k) => k !== key) : [...selected, key]);
  }
  function onKey(e: KeyboardEvent) {
    setKeyboard(true);
    const last = shown.length - 1;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((a) => Math.min(last, a + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => Math.max(0, a - 1));
    } else if (e.key === "Home" && !query) {
      e.preventDefault();
      setActive(0);
    } else if (e.key === "End" && !query) {
      e.preventDefault();
      setActive(Math.max(0, last));
    } else if (e.key === "Enter") {
      e.preventDefault();
      const o = shown[active];
      if (o) toggle(o.key);
    } else if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      close(true);
    } else if (e.key === "Tab" && (e.shiftKey || chosen === 0)) {
      // Forward Tab goes on to the Clear row while there is something to clear; focus leaving the control closes the panel.
      close(false);
    }
  }

  return (
    <span
      class="ms"
      ref={wrap}
      onFocusOut={(e) => {
        const next = (e as FocusEvent).relatedTarget as Node | null;
        if (open && next && wrap.current && !wrap.current.contains(next)) close(false);
      }}
    >
      <button
        type="button"
        ref={trigger}
        class={(open ? "ms-pill open" : "ms-pill") + (chosen > 0 ? " has" : "")}
        title={chosen > 1 ? pillText(label, options, selected) : undefined}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        onClick={() => (open ? close(false) : setOpen(true))}
      >
        <span class="ms-pill-text">{pillText(label, options, selected)}</span>
        <svg class="ms-chev" width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">
          <path d="M4 6l4 4 4-4" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
      </button>
      {open ? (
        <div
          class="ms-panel"
          id={id}
          role="group"
          aria-label={`${label} filter`}
          // Pressing on an option, the Clear row or the footer keeps focus on the search field: focus moving to a surrounding
          // dialog would count as leaving the control and close it before the click lands.
          onMouseDown={(e) => {
            if ((e.target as HTMLElement).tagName !== "INPUT") e.preventDefault();
          }}
        >
          <div class="ms-search">
            <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
              <circle cx="7" cy="7" r="4.5" fill="none" stroke="currentColor" stroke-width="1.6" />
              <path d="M10.5 10.5L14 14" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" />
            </svg>
            <input
              ref={search}
              type="text"
              role="combobox"
              aria-label={`Search ${label.toLowerCase()}`}
              aria-expanded="true"
              aria-controls={`${id}-list`}
              aria-activedescendant={shown[active] ? `${id}-o${active}` : undefined}
              autocomplete="off"
              spellcheck={false}
              placeholder={`Find ${label.toLowerCase()}`}
              value={query}
              onInput={(e) => setQuery((e.currentTarget as HTMLInputElement).value)}
              onKeyDown={onKey}
            />
          </div>
          <button
            type="button"
            class="ms-clear-row"
            disabled={chosen === 0}
            onClick={() => {
              onChange([]);
              search.current?.focus();
            }}
          >
            <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
              <path d="M2.5 2.5l7 7M9.5 2.5l-7 7" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" />
            </svg>
            Clear
          </button>
          <div class="ms-opts" id={`${id}-list`} role="listbox" aria-label={label} aria-multiselectable="true">
            {shown.map((o, i) => {
              const on = selected.includes(o.key);
              return (
                <div
                  key={o.key}
                  id={`${id}-o${i}`}
                  role="option"
                  aria-selected={on}
                  class={i === active ? (keyboard ? "ms-opt hl kb" : "ms-opt hl") : "ms-opt"}
                  onPointerMove={() => {
                    setKeyboard(false);
                    if (i !== active) setActive(i);
                  }}
                  onClick={() => {
                    toggle(o.key);
                    search.current?.focus();
                  }}
                >
                  <span class={on ? "ms-box on" : "ms-box"} aria-hidden="true">
                    {on ? (
                      <svg width="12" height="12" viewBox="0 0 12 12">
                        <path d="M2.2 6.3l2.4 2.4 5.2-5.4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
                      </svg>
                    ) : null}
                  </span>
                  <span class="ms-opt-mark">{o.mark}</span>
                  <span class="ms-opt-label">
                    <Highlight text={o.label} query={query} />
                  </span>
                  {o.count !== undefined ? <span class="ms-opt-n">{o.count}</span> : null}
                </div>
              );
            })}
            {shown.length === 0 ? <div class="ms-opt-none">No match</div> : null}
          </div>
          <div class="ms-foot">
            <span>
              {chosen} of {options.length} {noun}
            </span>
          </div>
        </div>
      ) : null}
    </span>
  );
}

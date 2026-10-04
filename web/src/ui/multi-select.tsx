import type { ComponentChildren } from "preact";
import { useEffect, useMemo, useRef, useState } from "preact/hooks";

/**
 * The filter of every library page (design README, Components: Filters): a searchable multi-select dropdown and the removable
 * chips of what is chosen. Sessions (Car, Track, Series, Session, Setup) and Setups (Car) use the same component and look.
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

export function matches(options: readonly Option[], query: string): Option[] {
  const q = query.trim().toLowerCase();
  return q ? options.filter((o) => o.label.toLowerCase().includes(q)) : options.slice();
}

let uid = 0;

/**
 * A searchable multi-select. The trigger shows how many are chosen; the panel opens on a focused search field, lists checkbox
 * options with their identity mark and count, and applies each choice at once; the footer reads `n of m` with Clear.
 * Up/Down move, Enter chooses, Escape closes and returns focus to the trigger.
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
    } else if (e.key === "Tab") {
      close(false);
    }
  }

  return (
    <span class="ms" ref={wrap}>
      <button
        type="button"
        ref={trigger}
        class={open ? "ms-pill open" : "ms-pill"}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        onClick={() => (open ? close(false) : setOpen(true))}
      >
        {label}
        {chosen > 0 ? <span class="ms-count">{chosen}</span> : null}
        <svg class="ms-chev" width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">
          <path d="M4 6l4 4 4-4" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
      </button>
      {open ? (
        <div class="ms-panel" id={id} role="group" aria-label={`${label} filter`}>
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
            <button type="button" class="ms-clear" disabled={chosen === 0} onClick={() => onChange([])}>
              Clear
            </button>
          </div>
        </div>
      ) : null}
    </span>
  );
}

/** A chosen filter value: the whole pill removes it. `color` is the car colour of a car value. */
export function FilterChip({ label, color, mark, onRemove }: { label: string; color?: string; mark?: ComponentChildren; onRemove(): void }) {
  return (
    <button type="button" class="filter-chip" style={color ? { "--car": color } : undefined} aria-label={`Remove ${label}`} onClick={onRemove}>
      {mark}
      {label}
      <svg class="filter-x" width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
        <path d="M2.5 2.5l7 7M9.5 2.5l-7 7" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" />
      </svg>
    </button>
  );
}

/** The chips after a divider, then Clear (the whole selection). Nothing when nothing is chosen. */
export function FilterChips({ children, count, onClear }: { children: ComponentChildren; count: number; onClear(): void }) {
  if (count === 0) return null;
  return (
    <>
      <span class="filter-sep" aria-hidden="true" />
      {children}
      <button type="button" class="filter-clear" onClick={onClear}>
        Clear
      </button>
    </>
  );
}

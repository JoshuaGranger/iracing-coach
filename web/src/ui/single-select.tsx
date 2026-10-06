import type { ComponentChildren } from "preact";
import { useEffect, useLayoutEffect, useRef, useState } from "preact/hooks";

/**
 * The single-choice dropdown (round 5, U1; backlog #79 and #80): the same pill and panel as the filters' MultiSelect, for a control
 * that holds one value, such as the Traces toolbar's Spotlight lap and chart Layout. It replaces a native `<select>`, whose list,
 * focus ring and value text the page cannot style or centre. The pill reads `LABEL value` on one centre line with a centred
 * chevron; the list opens under it, the current option marked, and choosing closes it. Up and Down move, Home and End jump, Enter
 * and Space choose, Escape closes and returns focus to the pill. Focus shows for the keyboard only (`control-behaviour.ts`).
 */
export interface SelectOption {
  key: string;
  label: string;
  /** Small text after the label (a tag such as `fastest`). */
  hint?: string;
  /** Identity mark drawn before the label: a lap swatch. */
  mark?: ComponentChildren;
}

/** The index to move to from `current` by `delta`, stopping at the ends (a list does not wrap). */
export function stepIndex(current: number, delta: number, count: number): number {
  if (count <= 0) return -1;
  return Math.max(0, Math.min(count - 1, current + delta));
}

/** The pill's value text: the chosen option's label, or the first option's (the empty choice, `None`) when the key is unknown. */
export function selectedOption(options: readonly SelectOption[], value: string): SelectOption | undefined {
  return options.find((option) => option.key === value) ?? options[0];
}

let uid = 0;

export function SingleSelect({
  label,
  options,
  value,
  onChange,
  mark,
  class: className,
}: {
  label: string;
  options: readonly SelectOption[];
  value: string;
  onChange(key: string): void;
  /** A mark drawn in the pill before the value (the spotlight lap's swatch). */
  mark?: ComponentChildren;
  class?: string;
}) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  /** The active option is lit only once the keyboard or the pointer has moved over the list. */
  const [moved, setMoved] = useState(false);
  const wrap = useRef<HTMLSpanElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const id = useRef(`ss-${++uid}`).current;
  const current = selectedOption(options, value);
  const selectedIndex = Math.max(0, options.findIndex((option) => option.key === current?.key));

  // Focus moves into the list as it opens, before the next key can go to the pill.
  useLayoutEffect(() => {
    if (!open) return;
    setActive(selectedIndex);
    setMoved(false);
    list.current?.focus({ preventScroll: true });
    list.current?.querySelector<HTMLElement>('[aria-selected="true"]')?.scrollIntoView({ block: "nearest" });
  }, [open]);

  useEffect(() => {
    if (!open) return undefined;
    const away = (event: Event): void => {
      if (wrap.current && !wrap.current.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", away);
    return () => document.removeEventListener("pointerdown", away);
  }, [open]);

  useEffect(() => {
    if (open) list.current?.querySelector<HTMLElement>(`#${id}-o${active}`)?.scrollIntoView({ block: "nearest" });
  }, [active, open]);

  const close = (refocus: boolean): void => {
    setOpen(false);
    if (refocus) trigger.current?.focus();
  };
  const choose = (key: string, refocus: boolean): void => {
    onChange(key);
    close(refocus);
  };
  const onKey = (event: KeyboardEvent): void => {
    const last = options.length - 1;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      setMoved(true);
      setActive((index) => stepIndex(index, event.key === "ArrowDown" ? 1 : -1, options.length));
    } else if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      setMoved(true);
      setActive(event.key === "Home" ? 0 : Math.max(0, last));
    } else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      const option = options[active];
      if (option) choose(option.key, true);
    } else if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      close(true);
    } else if (event.key === "Tab") {
      close(false);
    }
  };

  return (
    <span
      class={`ss${className ? ` ${className}` : ""}`}
      ref={wrap}
      onFocusOut={(event) => {
        const next = (event as FocusEvent).relatedTarget as Node | null;
        if (open && next && wrap.current && !wrap.current.contains(next)) setOpen(false);
      }}
    >
      <button
        type="button"
        ref={trigger}
        class={open ? "ss-pill open" : "ss-pill"}
        data-empty={current?.key === "" ? "" : undefined}
        title={label}
        aria-label={`${label} ${current?.label ?? ""}`.trim()}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        onClick={() => setOpen((was) => !was)}
        onKeyDown={(event) => {
          if (open || (event.key !== "ArrowDown" && event.key !== "ArrowUp")) return;
          event.preventDefault();
          setOpen(true);
        }}
      >
        <span class="ss-cap">{label}</span>
        {mark}
        <span class="ss-val">{current?.label ?? ""}</span>
        <svg class="ss-chev" width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">
          <path d="M4 6l4 4 4-4" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
      </button>
      {open ? (
        <div
          class="ss-panel"
          id={id}
          ref={list}
          role="listbox"
          aria-label={label}
          tabIndex={-1}
          aria-activedescendant={`${id}-o${active}`}
          onKeyDown={onKey}
          // A press on the list keeps focus on it: focus moving to a surrounding region would count as leaving and close it first.
          onMouseDown={(event) => event.preventDefault()}
        >
          {options.map((option, index) => (
            <div
              key={option.key}
              id={`${id}-o${index}`}
              role="option"
              aria-selected={option.key === current?.key}
              data-key={option.key}
              class={index === active && moved ? "ss-opt hl" : "ss-opt"}
              onPointerMove={() => {
                setMoved(true);
                if (index !== active) setActive(index);
              }}
              onClick={() => choose(option.key, false)}
            >
              <span class="ss-opt-mark">{option.mark}</span>
              <span class="ss-opt-label">{option.label}</span>
              {option.hint ? <span class="ss-opt-hint">{option.hint}</span> : null}
            </div>
          ))}
        </div>
      ) : null}
    </span>
  );
}

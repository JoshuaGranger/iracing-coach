import type { ComponentChildren } from "preact";
import { useEffect, useLayoutEffect, useRef, useState } from "preact/hooks";

/**
 * The page heading with its filters (web round 2, "Filters by width"; round 3 #35/#36). Same rule on every page.
 *
 * The filter controls sit at the heading's right when the title and the complete set of controls fit at their fixed
 * widths. When they do not, the heading shows one `Filters · N` button; it opens a popover on pointer devices and a bottom
 * sheet on touch screens. The decision is a measurement of the row (ResizeObserver), never a device-class test: a wide
 * tablet shows the controls inline, a narrow desktop window collapses.
 *
 * Nothing in the heading appears, disappears or changes width when a filter is set or cleared: there is no Clear button and
 * no chip row; each dropdown button carries its own selection (`Car · Supra`, `Car · 2`) at a fixed width and clears from
 * inside its list, and the collapsed button has a fixed width too.
 *
 * The bar is stateless about selections. The page owns them and saves them per page through the driver's prefs
 * (`session_filters`, `setup_filters`), so they survive navigation and Back; the popover only shows them.
 */
export interface FilterControl {
  /** Stable key. */
  id: string;
  /** Name of the control, shown above it in the popover or sheet (the inline control carries its own label). */
  label: string;
  /** The control: a `MultiSelect`, a `Segmented`, a text field. */
  node: ComponentChildren;
  /** Inline width in rem. Defaults to `DEFAULT_CONTROL_REM`; set it when the control is wider (a search field). */
  minRem?: number;
}

/** A multi-select pill: a fixed width, whatever it carries (`.ms-pill`). */
export const DEFAULT_CONTROL_REM = 10;
/** Space between the title and the controls, and between controls (`--space-4`, `--space-2`). */
export const TITLE_GAP_REM = 1;
export const CONTROL_GAP_REM = 0.5;

export interface FitInput {
  /** Width of the heading row in px. */
  rowWidth: number;
  /** Natural width of the title node in px. */
  titleWidth: number;
  /** Inline width of each control, in rem. */
  controlRems: readonly number[];
  /** Pixels per rem (the root font size). */
  rootPx: number;
}

/** True when the title and every control, at their widths with their gaps, fit in the row. An unmeasured row never fits. */
export function fitsInline({ rowWidth, titleWidth, controlRems, rootPx }: FitInput): boolean {
  if (controlRems.length === 0) return true;
  if (!(rowWidth > 0)) return false;
  const controls = controlRems.reduce((sum, r) => sum + r, 0) + (controlRems.length - 1) * CONTROL_GAP_REM;
  return titleWidth + (TITLE_GAP_REM + controls) * rootPx <= rowWidth;
}

/** The label of the collapsed button: `Filters`, or `Filters · N` while N are chosen. */
export function filtersLabel(activeCount: number): string {
  return activeCount > 0 ? `Filters · ${activeCount}` : "Filters";
}

const rootPx = () => (typeof document === "undefined" ? 16 : Number.parseFloat(getComputedStyle(document.documentElement).fontSize) || 16);

/** Live match of a media query. */
function useMedia(query: string): boolean {
  const [matches, setMatches] = useState(() => (typeof matchMedia === "function" ? matchMedia(query).matches : false));
  useEffect(() => {
    if (typeof matchMedia !== "function") return undefined;
    const m = matchMedia(query);
    const on = () => setMatches(m.matches);
    on();
    m.addEventListener("change", on);
    return () => m.removeEventListener("change", on);
  }, [query]);
  return matches;
}

let uid = 0;

export function FilterBar({
  title,
  controls,
  activeCount,
}: {
  /** The page title. A string becomes the `h1`; pass a node (an `h1` with a Live now link beside it) for more. */
  title: ComponentChildren;
  controls: readonly FilterControl[];
  /** How many choices are in force; the collapsed button reads `Filters · N`. */
  activeCount: number;
}) {
  const row = useRef<HTMLDivElement>(null);
  const titleRef = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const surface = useRef<HTMLDivElement>(null);
  const id = useRef(`fb-${++uid}`).current;
  const [size, setSize] = useState({ row: 0, title: 0, root: 16 });
  const [open, setOpen] = useState(false);
  const touch = useMedia("(pointer: coarse)");

  useLayoutEffect(() => {
    const measure = () => {
      const r = row.current?.getBoundingClientRect().width ?? 0;
      const t = titleRef.current?.getBoundingClientRect().width ?? 0;
      const root = rootPx();
      setSize((p) => (Math.abs(p.row - r) < 0.5 && Math.abs(p.title - t) < 0.5 && p.root === root ? p : { row: r, title: t, root }));
    };
    measure();
    if (typeof ResizeObserver !== "function") return undefined;
    const observer = new ResizeObserver(measure);
    if (row.current) observer.observe(row.current);
    if (titleRef.current) observer.observe(titleRef.current);
    return () => observer.disconnect();
  }, []);

  const inline = fitsInline({
    rowWidth: size.row,
    titleWidth: size.title,
    controlRems: controls.map((c) => c.minRem ?? DEFAULT_CONTROL_REM),
    rootPx: size.root,
  });

  // The surface is only open while collapsed; widening the window closes it.
  useEffect(() => {
    if (inline) setOpen(false);
  }, [inline]);

  // A layout effect, so the listener is in place the moment the surface is on screen.
  useLayoutEffect(() => {
    if (!open) return undefined;
    surface.current?.focus();
    const away = (e: Event) => {
      const t = e.target as Node;
      if (touch) return; // the sheet closes on its backdrop and Done
      if (surface.current?.contains(t) || trigger.current?.contains(t)) return;
      setOpen(false);
    };
    document.addEventListener("pointerdown", away);
    return () => document.removeEventListener("pointerdown", away);
  }, [open, touch]);

  function close() {
    setOpen(false);
    trigger.current?.focus();
  }
  const onKey = (e: KeyboardEvent) => {
    if (e.key === "Escape" && open) {
      e.preventDefault();
      close();
    }
  };

  const heading = typeof title === "string" ? <h1>{title}</h1> : title;
  const body = (
    <>
      {controls.map((c) => (
        <section key={c.id} class="fb-field" aria-label={c.label}>
          <h3 class="fb-field-label">{c.label}</h3>
          {c.node}
        </section>
      ))}
      <div class="fb-foot">
        <button type="button" class="btn fb-done" onClick={close}>
          Done
        </button>
      </div>
    </>
  );

  return (
    <div class="fb" data-layout={inline ? "inline" : "collapsed"} onKeyDown={onKey}>
      <header class="page-head fb-row" ref={row}>
        <div class="fb-title" ref={titleRef}>
          {heading}
        </div>
        {inline ? (
          <div class="fb-controls" role="search" aria-label="Filters">
            {controls.map((c) => (
              <span key={c.id} class="fb-ctl">
                {c.node}
              </span>
            ))}
          </div>
        ) : (
          <div class="fb-anchor">
            <button
              type="button"
              ref={trigger}
              class={open ? "ms-pill fb-trigger open" : "ms-pill fb-trigger"}
              aria-haspopup="dialog"
              aria-expanded={open}
              aria-controls={open ? id : undefined}
              onClick={() => setOpen((o) => !o)}
            >
              <span class="ms-pill-text">{filtersLabel(activeCount)}</span>
              <svg class="ms-chev" width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">
                <path d="M4 6l4 4 4-4" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" />
              </svg>
            </button>
            {open && !touch ? (
              <div class="fb-surface fb-pop" id={id} role="dialog" aria-label="Filters" tabIndex={-1} ref={surface}>
                {body}
              </div>
            ) : null}
          </div>
        )}
      </header>
      {open && touch ? (
        <>
          <div class="fb-backdrop" onClick={close} aria-hidden="true" />
          <div class="fb-surface fb-sheet" id={id} role="dialog" aria-modal="true" aria-label="Filters" tabIndex={-1} ref={surface}>
            <span class="fb-grip" aria-hidden="true" />
            {body}
          </div>
        </>
      ) : null}
    </div>
  );
}

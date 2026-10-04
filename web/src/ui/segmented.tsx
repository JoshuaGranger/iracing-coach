import type { ComponentChildren } from "preact";
import { selectableClass } from "./selectable";

/** The class list of one segment: `on` when it is the value, its band colour for the phase variant. Hover adds nothing. */
export function segmentClass(selected: boolean, band?: "p" | "q" | "r"): string | undefined {
  return selectableClass(selected, band ? `seg-${band}` : null);
}

export interface SegmentOption<T extends string | number = string> {
  value: T;
  label: ComponentChildren;
  /** Hover text; a disabled segment names its state here. */
  title?: string;
  disabled?: boolean;
  /** Phase band colour (variant `phase`): P, Q or R. */
  band?: "p" | "q" | "r";
  ariaLabel?: string;
}

/**
 * The segmented selector: one segment per choice, the current one lit in the interaction colour (`aria-pressed`).
 * The selected segment has one state and ignores hover (`selectable.ts`).
 * Tab reaches the group; Left, Right, Home and End move between the enabled segments, Enter and Space choose.
 * Variant `phase` is the P . Q . R selector: segments of 50 px, each carrying its band colour, the current one filled with it.
 */
export function Segmented<T extends string | number = string>({
  label,
  options,
  value,
  onChange,
  variant = "plain",
  class: className,
}: {
  label: string;
  options: readonly SegmentOption<T>[];
  value: T | null;
  onChange: (value: T) => void;
  variant?: "plain" | "phase";
  class?: string;
}) {
  const onKey = (e: KeyboardEvent) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return;
    const group = (e.currentTarget as HTMLElement).closest(".seg");
    const items = [...(group?.querySelectorAll<HTMLButtonElement>("button:not(:disabled)") ?? [])];
    if (items.length === 0) return;
    const at = items.indexOf(e.target as HTMLButtonElement);
    const next = e.key === "Home" ? 0 : e.key === "End" ? items.length - 1 : (at + (e.key === "ArrowRight" ? 1 : -1) + items.length) % items.length;
    e.preventDefault();
    items[next]?.focus();
  };
  return (
    <div class={`seg${variant === "phase" ? " phase" : ""}${className ? ` ${className}` : ""}`} role="group" aria-label={label} onKeyDown={onKey}>
      {options.map((o) => (
        <button
          key={String(o.value)}
          type="button"
          class={segmentClass(o.value === value, o.band)}
          aria-pressed={o.value === value}
          aria-label={o.ariaLabel}
          disabled={o.disabled}
          title={o.title}
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

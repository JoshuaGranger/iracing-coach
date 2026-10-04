/**
 * Selection has one state (web round 2). The selected item of any control (tab, segment, list row, toggle) looks one way and
 * ignores hover; hover changes only unselected items. A shared selectable marks itself with any of these, and the global
 * hover rules in styles.css skip them, so a page needs no `:not(.on)` of its own.
 */
export const SELECTED_MARKERS = [".on", ".selected", '[aria-pressed="true"]', '[aria-selected="true"]', "[aria-current]"] as const;

/** The CSS selector list for "is selected", for pages that write their own hover rule: `.row:hover:not(:where(...))`. */
export const SELECTED_SELECTOR: string = SELECTED_MARKERS.join(", ");

/**
 * The class list of a selectable item: the base classes, plus `on` only when selected. Hover never adds a class here, so the
 * selected look cannot depend on the pointer. `undefined` when there is nothing to add (no empty class attribute).
 */
export function selectableClass(selected: boolean, ...base: (string | false | null | undefined)[]): string | undefined {
  const parts = base.filter((x): x is string => typeof x === "string" && x.length > 0);
  if (selected) parts.unshift("on");
  return parts.length > 0 ? parts.join(" ") : undefined;
}

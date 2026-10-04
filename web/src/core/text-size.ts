import { useEffect } from "preact/hooks";
import { theme } from "../gen/theme";
import { savePrefs, usePrefs } from "./prefs";
import { type Store, createStore, safeStorage } from "./store";

/**
 * The driver's text size: three steps (`theme.typography.textSizes`, from config/theme.dark.json) that set the root font size as
 * a percentage of the browser default. Every type size, control and space is rem, so one step scales the whole app.
 *
 * Where it lives: the driver's prefs on the server (`text_size`, so it follows the driver to every device) is the truth;
 * localStorage keeps only a hint so the first paint on this device is already the right size. An unknown value reads as Default.
 */
export type TextSize = (typeof theme.typography.textSizes)[number]["id"];
export const TEXT_SIZES = theme.typography.textSizes;
export const DEFAULT_TEXT_SIZE: TextSize = "default";
export const TEXT_SIZE_HINT_KEY = "coach.text-size";

export function parseTextSize(value: unknown): TextSize | null {
  return TEXT_SIZES.find((step) => step.id === value)?.id ?? null;
}

/** The step this device last showed (the pre-load hint); Default when there is none. */
export function textSizeHint(): TextSize {
  try {
    const raw = safeStorage?.getItem(TEXT_SIZE_HINT_KEY);
    return parseTextSize(raw?.replace(/^"|"$/g, "")) ?? DEFAULT_TEXT_SIZE;
  } catch {
    return DEFAULT_TEXT_SIZE;
  }
}

function applyTextSize(size: TextSize): void {
  if (typeof document !== "undefined") document.documentElement.dataset.textSize = size;
  try {
    safeStorage?.setItem(TEXT_SIZE_HINT_KEY, size);
  } catch {
    /* storage blocked: the saved preference still applies once it loads */
  }
}

/** The step in effect. Setting it applies it to `<html data-text-size>` and refreshes the hint. */
export const textSize: Store<TextSize> = createStore<TextSize>(textSizeHint());
textSize.subscribe(() => applyTextSize(textSize.get()));

/** Applies the hint before the first render, so nothing paints at the wrong size. */
export function initTextSize(): void {
  const size = textSize.get();
  if (typeof document !== "undefined") document.documentElement.dataset.textSize = size;
}

/** The driver picked a step: apply it now and save it with the driver's prefs. Without a driver only this device remembers it. */
export function chooseTextSize(size: TextSize, customerId: number | null): void {
  textSize.set(size);
  if (customerId !== null) void savePrefs(customerId, { text_size: size });
}

/** The saved step of a prefs record, or null when it holds none (or one this app does not know). */
export function savedTextSize(prefs: object | null | undefined): TextSize | null {
  return parseTextSize((prefs as { text_size?: unknown } | null | undefined)?.text_size);
}

/** Follows the driver's saved step: when the prefs load (or change on another device) and name a step, it takes effect here. */
export function useSavedTextSize(customerId: number | null): void {
  const { prefs } = usePrefs(customerId);
  const saved = savedTextSize(prefs);
  useEffect(() => {
    if (saved !== null) textSize.set(saved);
  }, [saved]);
}

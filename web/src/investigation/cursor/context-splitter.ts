/**
 * Splitter between the track panel and the laps rail (port of analysis-context-splitter.js). The ratio
 * is the track panel's share of the column, shared by every splitter, 1/3 to 2/3, default 0.43.
 */

export const MINIMUM_RATIO = 1 / 3;
export const MAXIMUM_RATIO = 2 / 3;
export const DEFAULT_RATIO = 0.43;

export function clampRatio(value: number): number {
  return Math.max(MINIMUM_RATIO, Math.min(MAXIMUM_RATIO, value));
}

/** Track share for a pointer at `clientY` within `container`, with the splitter's own height excluded. */
export function ratioAtPointer(containerTop: number, containerHeight: number, splitterHeight: number, clientY: number): number {
  const available = Math.max(1, containerHeight - splitterHeight);
  return (clientY - containerTop - splitterHeight / 2) / available;
}

/** Keyboard steps: arrows move 2 %, Home and End jump to the limits; other keys return null. */
export function ratioForKey(current: number, key: string): number | null {
  switch (key) {
    case "ArrowUp": return clampRatio(current - 0.02);
    case "ArrowDown": return clampRatio(current + 0.02);
    case "Home": return MINIMUM_RATIO;
    case "End": return MAXIMUM_RATIO;
    default: return null;
  }
}

let sharedRatio = DEFAULT_RATIO;
const active = new Set<() => void>();

export function currentRatio(): number {
  return sharedRatio;
}

export function setSharedRatio(ratio: number): void {
  sharedRatio = clampRatio(ratio);
  for (const apply of active) apply();
}

export interface ContextSplitter {
  dispose(): void;
}

export function createContextSplitter(container: HTMLElement, splitter: HTMLElement): ContextSplitter {
  let dragging = false;
  let pointerId: number | null = null;
  const apply = (): void => {
    container.style.setProperty("--analysis-context-track-share", `${sharedRatio}fr`);
    container.style.setProperty("--analysis-context-laps-share", `${1 - sharedRatio}fr`);
    splitter.setAttribute("aria-valuenow", String(Math.round(sharedRatio * 100)));
  };
  const at = (clientY: number): number => {
    const bounds = container.getBoundingClientRect();
    return ratioAtPointer(bounds.top, bounds.height, splitter.getBoundingClientRect().height, clientY);
  };
  const onDown = (event: PointerEvent): void => {
    if (event.button !== 0 || !event.isPrimary) return;
    dragging = true;
    pointerId = event.pointerId;
    splitter.setPointerCapture?.(event.pointerId);
    container.classList.add("context-resizing");
    setSharedRatio(at(event.clientY));
    event.preventDefault();
  };
  const onMove = (event: PointerEvent): void => {
    if (dragging && event.pointerId === pointerId) setSharedRatio(at(event.clientY));
  };
  const onUp = (event: PointerEvent): void => {
    if (!dragging || event.pointerId !== pointerId) return;
    dragging = false;
    pointerId = null;
    splitter.releasePointerCapture?.(event.pointerId);
    container.classList.remove("context-resizing");
  };
  const onKey = (event: KeyboardEvent): void => {
    const next = ratioForKey(sharedRatio, event.key);
    if (next === null) return;
    setSharedRatio(next);
    event.preventDefault();
  };
  splitter.addEventListener("pointerdown", onDown);
  splitter.addEventListener("pointermove", onMove);
  splitter.addEventListener("pointerup", onUp);
  splitter.addEventListener("pointercancel", onUp);
  splitter.addEventListener("keydown", onKey);
  active.add(apply);
  apply();
  return {
    dispose() {
      splitter.removeEventListener("pointerdown", onDown);
      splitter.removeEventListener("pointermove", onMove);
      splitter.removeEventListener("pointerup", onUp);
      splitter.removeEventListener("pointercancel", onUp);
      splitter.removeEventListener("keydown", onKey);
      container.classList.remove("context-resizing");
      active.delete(apply);
    },
  };
}

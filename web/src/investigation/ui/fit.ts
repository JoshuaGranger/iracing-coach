import { useEffect } from "preact/hooks";
import type { RefObject } from "preact";

/** The workspace never shrinks below this; a shorter window scrolls the page instead of crushing the rows. */
export const MIN_FIT_HEIGHT = 360;

/**
 * Height that makes the workspace end at the bottom of the window: the window height less the space above the
 * workspace and the padding and margins below it. The shell does not bound its content, so the workspace does it.
 */
export function fitHeight(viewportHeight: number, top: number, below: number): number {
  return Math.max(MIN_FIT_HEIGHT, Math.floor(viewportHeight - top - below));
}

/** Padding, border and margin under the element, up to the root: the part of the window it cannot use. */
function spaceBelow(element: HTMLElement): number {
  let total = 0;
  for (let node: HTMLElement | null = element; node; node = node.parentElement) {
    const style = getComputedStyle(node);
    total += parseFloat(style.marginBottom) || 0;
    if (node !== element) total += (parseFloat(style.paddingBottom) || 0) + (parseFloat(style.borderBottomWidth) || 0);
  }
  return total;
}

/**
 * Sets `--ri-fit` (px) on the element so a stylesheet can bound it to the window (`height: var(--ri-fit, 100%)`).
 * Re-measured on window resize and when the page above it changes height (a banner appearing).
 */
export function useViewportFit(ref: RefObject<HTMLElement>): void {
  useEffect(() => {
    const element = ref.current;
    if (!element) return undefined;
    let frame = 0;
    const apply = (): void => {
      frame = 0;
      const top = element.getBoundingClientRect().top + window.scrollY;
      const next = `${fitHeight(document.documentElement.clientHeight, top, spaceBelow(element))}px`;
      if (element.style.getPropertyValue("--ri-fit") !== next) element.style.setProperty("--ri-fit", next);
    };
    const schedule = (): void => {
      if (!frame) frame = requestAnimationFrame(apply);
    };
    apply();
    window.addEventListener("resize", schedule);
    const observer = typeof ResizeObserver === "function" ? new ResizeObserver(schedule) : null;
    observer?.observe(document.body);
    return () => {
      window.removeEventListener("resize", schedule);
      observer?.disconnect();
      if (frame) cancelAnimationFrame(frame);
    };
  }, [ref]);
}

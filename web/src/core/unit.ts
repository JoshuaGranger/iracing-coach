import { useEffect, useState } from "preact/hooks";

/**
 * The charts and maps draw in pixels (an SVG whose view box is its own size, a canvas), so their margins, label offsets and card
 * sizes follow the root font size the way the CSS does: one rem is the root font size, the browser's default font size. The
 * geometry constants in their callers were set for the default 16 px root; `scaleFor` is their factor.
 */
export const DEFAULT_ROOT_PX = 16;

/** Pixels in one rem now. 16 where the document cannot say (tests, server rendering). */
export function remPx(): number {
  if (typeof document === "undefined" || typeof getComputedStyle !== "function") return DEFAULT_ROOT_PX;
  const size = parseFloat(getComputedStyle(document.documentElement).fontSize);
  return Number.isFinite(size) && size > 0 ? size : DEFAULT_ROOT_PX;
}

/** Factor to apply to geometry written for the default root: 1 at 16 px, more where the browser's default font size is larger. */
export function scaleFor(root: number): number {
  return root / DEFAULT_ROOT_PX;
}

/** The root font size, current when the window resizes or the root element's style or class changes. */
export function useRemPx(): number {
  const [px, setPx] = useState(remPx);
  useEffect(() => {
    const update = (): void => setPx(remPx());
    update();
    window.addEventListener("resize", update);
    const observer = typeof MutationObserver === "function" ? new MutationObserver(update) : null;
    observer?.observe(document.documentElement, { attributes: true, attributeFilter: ["style", "class"] });
    return () => {
      window.removeEventListener("resize", update);
      observer?.disconnect();
    };
  }, []);
  return px;
}

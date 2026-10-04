import { useEffect, useState } from "preact/hooks";
import type { RefObject } from "preact";

export interface Size {
  width: number;
  height: number;
}

/** Content-box size of an element, updated at most once per frame while it resizes. */
export function useElementSize(ref: RefObject<HTMLElement | SVGElement>): Size {
  const [size, setSize] = useState<Size>({ width: 0, height: 0 });
  useEffect(() => {
    const element = ref.current;
    if (!element) return undefined;
    let frame = 0;
    const measure = (): void => {
      frame = 0;
      const rect = element.getBoundingClientRect();
      setSize((previous) => (Math.abs(previous.width - rect.width) < 0.5 && Math.abs(previous.height - rect.height) < 0.5 ? previous : { width: rect.width, height: rect.height }));
    };
    measure();
    if (typeof ResizeObserver !== "function") return undefined;
    const observer = new ResizeObserver(() => {
      if (!frame) frame = requestAnimationFrame(measure);
    });
    observer.observe(element);
    return () => {
      observer.disconnect();
      if (frame) cancelAnimationFrame(frame);
    };
  }, [ref]);
  return size;
}

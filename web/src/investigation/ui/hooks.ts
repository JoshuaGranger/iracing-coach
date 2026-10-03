import { useEffect, useRef, useState } from "preact/hooks";
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

export type AsyncState<T> = { status: "loading" } | { status: "ready"; value: T } | { status: "error"; error: Error };

/** Runs `load` when `key` changes; a stale result is dropped. `retry` re-runs it. */
export function useAsync<T>(load: () => Promise<T>, key: unknown): { state: AsyncState<T>; retry: () => void } {
  const [state, setState] = useState<AsyncState<T>>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  const loadRef = useRef(load);
  loadRef.current = load;
  useEffect(() => {
    let current = true;
    setState({ status: "loading" });
    loadRef.current().then(
      (value) => { if (current) setState({ status: "ready", value }); },
      (error: unknown) => { if (current) setState({ status: "error", error: error instanceof Error ? error : new Error(String(error)) }); },
    );
    return () => {
      current = false;
    };
  }, [key, attempt]);
  return { state, retry: () => setAttempt((value) => value + 1) };
}

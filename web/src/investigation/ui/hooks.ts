import { useEffect, useRef, useState } from "preact/hooks";

export { useElementSize, type Size } from "../../core/element-size";

/** Numbers kept in session storage under `key` (the tab's session): read once per key, written on every change. */
export function useSessionNumbers(key: string): [Set<number>, (next: Set<number>) => void] {
  const read = (): Set<number> => {
    try {
      const value: unknown = JSON.parse(sessionStorage.getItem(key) ?? "[]");
      return new Set(Array.isArray(value) ? value.filter((item): item is number => typeof item === "number" && Number.isFinite(item)) : []);
    } catch {
      return new Set();
    }
  };
  const [state, setState] = useState<{ key: string; value: Set<number> }>(() => ({ key, value: read() }));
  const current = state.key === key ? state.value : read();
  if (state.key !== key) setState({ key, value: current });
  const write = (next: Set<number>): void => {
    setState({ key, value: next });
    try {
      sessionStorage.setItem(key, JSON.stringify([...next].sort((a, b) => a - b)));
    } catch {
      // Storage unavailable: the choice lasts while the view is open.
    }
  };
  return [current, write];
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

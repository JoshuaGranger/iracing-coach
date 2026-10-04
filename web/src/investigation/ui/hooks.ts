import { useEffect, useRef, useState } from "preact/hooks";

export { useElementSize, type Size } from "../../core/element-size";

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

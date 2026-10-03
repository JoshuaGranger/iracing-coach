import { useCallback, useEffect, useRef, useState } from "preact/hooks";
import { getJson } from "./api";

/**
 * Read cache with stale-while-revalidate. Loaded data stays inspectable when the server drops: a failed refresh
 * keeps the last value and reports the error beside it. Keys are request paths. `invalidate(prefix)` makes mounted
 * readers refetch (the shell calls it on server events). At most MAX_ENTRIES keys are kept, least recently used first out.
 */
const MAX_ENTRIES = 96;

interface Entry {
  data: unknown;
  at: number;
}

const cache = new Map<string, Entry>();
const subscribers = new Set<(prefix: string) => void>();

export function cachedValue<T>(key: string): T | undefined {
  const e = cache.get(key);
  if (!e) return undefined;
  cache.delete(key);
  cache.set(key, e);
  return e.data as T;
}

export function setCached(key: string, data: unknown): void {
  cache.delete(key);
  cache.set(key, { data, at: Date.now() });
  while (cache.size > MAX_ENTRIES) cache.delete(cache.keys().next().value as string);
}

export function invalidate(prefix: string): void {
  for (const fn of [...subscribers]) fn(prefix);
}

export function clearCache(): void {
  cache.clear();
}

export interface Resource<T> {
  data: T | undefined;
  error: Error | null;
  /** True while the first value is loading, or a refresh is running. */
  loading: boolean;
  reload(): void;
  /** Replace the cached value (after a write). */
  mutate(next: T): void;
}

export function useResource<T>(key: string | null, load?: (signal: AbortSignal) => Promise<T>): Resource<T> {
  const [data, setData] = useState<T | undefined>(() => (key ? cachedValue<T>(key) : undefined));
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(key !== null);
  const token = useRef(0);
  const loader = useRef(load);
  loader.current = load;

  const run = useCallback(
    (k: string | null) => {
      const mine = ++token.current;
      if (k === null) {
        setLoading(false);
        return () => undefined;
      }
      const ctl = new AbortController();
      setLoading(true);
      (loader.current ?? ((signal: AbortSignal) => getJson<T>(k, { signal })))(ctl.signal).then(
        (value) => {
          if (mine !== token.current) return;
          setCached(k, value);
          setData(value);
          setError(null);
          setLoading(false);
        },
        (e: unknown) => {
          if (mine !== token.current || (e instanceof DOMException && e.name === "AbortError")) return;
          setError(e instanceof Error ? e : new Error(String(e)));
          setLoading(false);
        },
      );
      return () => ctl.abort();
    },
    [],
  );

  useEffect(() => {
    setData(key ? cachedValue<T>(key) : undefined);
    setError(null);
    return run(key);
  }, [key, run]);

  useEffect(() => {
    if (key === null) return;
    const fn = (prefix: string) => {
      if (key.startsWith(prefix)) run(key);
    };
    subscribers.add(fn);
    return () => void subscribers.delete(fn);
  }, [key, run]);

  return {
    data,
    error,
    loading,
    reload: () => void run(key),
    mutate: (next) => {
      if (key) setCached(key, next);
      setData(next);
    },
  };
}

export function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

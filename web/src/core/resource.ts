import { useCallback, useEffect, useRef, useState } from "preact/hooks";
import { getJson } from "./api";

/**
 * Read cache with stale-while-revalidate. Loaded data stays inspectable when the server drops: a failed refresh
 * keeps the last value and reports the error beside it. A value fetched under 3 s ago is not fetched again on mount. Keys are request paths. `invalidate(prefix)` makes mounted
 * readers refetch (the shell calls it on server events). At most MAX_ENTRIES keys are kept, least recently used first out.
 */
const MAX_ENTRIES = 96;
/** A value this young is shown without asking again; pages that mount one after another (shell, then page) share it. */
const FRESH_MS = 3000;

interface Entry {
  data: unknown;
  at: number;
}

const cache = new Map<string, Entry>();
const subscribers = new Set<(matches: (key: string) => boolean) => void>();
/** Loads under way for mounting readers; readers of one key that mount together share one request. */
const inflight = new Map<string, Promise<unknown>>();

/** One request per key at a time for readers that mount while it runs. The load is not tied to any reader's lifetime. */
export function loadShared<T>(key: string, load: (signal: AbortSignal) => Promise<T>): Promise<T> {
  const running = inflight.get(key);
  if (running) return running as Promise<T>;
  const p = load(new AbortController().signal).finally(() => {
    if (inflight.get(key) === p) inflight.delete(key);
  });
  inflight.set(key, p);
  return p;
}

export function cachedValue<T>(key: string): T | undefined {
  const e = cache.get(key);
  if (!e) return undefined;
  cache.delete(key);
  cache.set(key, e);
  return e.data as T;
}

export function isFresh(key: string, now = Date.now()): boolean {
  const e = cache.get(key);
  return e !== undefined && now - e.at < FRESH_MS;
}

export function setCached(key: string, data: unknown): void {
  cache.delete(key);
  cache.set(key, { data, at: Date.now() });
  while (cache.size > MAX_ENTRIES) cache.delete(cache.keys().next().value as string);
}

/** Called with a key matcher whenever something is invalidated. Returns the unsubscribe function. */
export function onInvalidate(fn: (matches: (key: string) => boolean) => void): () => void {
  subscribers.add(fn);
  return () => void subscribers.delete(fn);
}

export function invalidate(prefix: string): void {
  for (const fn of [...subscribers]) fn((key) => key.startsWith(prefix));
}

/** Refetch readers of exactly this key (a list path is a prefix of every path below it, so `invalidate` would reach them all). */
export function invalidateKey(key: string): void {
  for (const fn of [...subscribers]) fn((k) => k === key);
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
  const [loading, setLoading] = useState(key !== null && !isFresh(key));
  const token = useRef(0);
  const loader = useRef(load);
  loader.current = load;

  const run = useCallback(
    (k: string | null, fresh = false) => {
      const mine = ++token.current;
      if (k === null) {
        setLoading(false);
        return () => undefined;
      }
      const ctl = new AbortController();
      setLoading(true);
      const load = loader.current ?? ((signal: AbortSignal) => getJson<T>(k, { signal }));
      (fresh ? load(ctl.signal) : loadShared(k, load)).then(
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
      return () => {
        if (fresh) ctl.abort();
        else if (token.current === mine) token.current++;
      };
    },
    [],
  );

  useEffect(() => {
    setData(key ? cachedValue<T>(key) : undefined);
    setError(null);
    if (key !== null && isFresh(key)) {
      setLoading(false);
      return;
    }
    return run(key);
  }, [key, run]);

  useEffect(() => {
    if (key === null) return;
    const fn = (matches: (k: string) => boolean) => {
      if (matches(key)) run(key, true);
    };
    return onInvalidate(fn);
  }, [key, run]);

  return {
    data,
    error,
    loading,
    reload: () => void run(key, true),
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

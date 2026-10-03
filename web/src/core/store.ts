import { useEffect, useState } from "preact/hooks";

/** Minimal observable value. Shared modules expose stores; components read them with `useStore`. */
export interface Store<T> {
  get(): T;
  set(value: T): void;
  update(fn: (value: T) => T): void;
  subscribe(listener: () => void): () => void;
}

export function createStore<T>(initial: T): Store<T> {
  let value = initial;
  const listeners = new Set<() => void>();
  return {
    get: () => value,
    set(next) {
      if (Object.is(next, value)) return;
      value = next;
      for (const l of [...listeners]) l();
    },
    update(fn) {
      this.set(fn(value));
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
}

export function useStore<T>(store: Store<T>): T {
  const [value, setValue] = useState(store.get());
  useEffect(() => {
    setValue(store.get());
    return store.subscribe(() => setValue(store.get()));
  }, [store]);
  return value;
}

/** Safe `localStorage` access: the accessor can throw (private windows, blocked site data). */
export const safeStorage: Pick<Storage, "getItem" | "setItem" | "removeItem"> | null = (() => {
  try {
    const s = globalThis.localStorage;
    const probe = "coach.probe";
    s.setItem(probe, "1");
    s.removeItem(probe);
    return s;
  } catch {
    return null;
  }
})();

export function readJson<T>(key: string, fallback: T): T {
  try {
    const raw = safeStorage?.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

export function writeJson(key: string, value: unknown): void {
  try {
    safeStorage?.setItem(key, JSON.stringify(value));
  } catch {
    /* storage full or blocked: the value stays in memory only */
  }
}

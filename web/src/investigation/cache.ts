/**
 * In-memory analysis cache: at most 3 analyses and 96 MB (architecture 2.5). Loaded analyses stay
 * inspectable when the server drops; eviction is least-recently-used by entry count and by bytes.
 */

export const CACHE_MAX_ENTRIES = 3;
export const CACHE_MAX_BYTES = 96 * 1024 * 1024;

export interface CacheOptions {
  maxEntries?: number;
  maxBytes?: number;
}

interface Slot<T> {
  value: T;
  bytes: number;
}

export interface AnalysisCache<T> {
  get(key: string): T | undefined;
  /** Stores a value; returns false (and stores nothing) when it alone exceeds the byte budget. */
  set(key: string, value: T, bytes: number): boolean;
  /** Re-measures an entry whose size grew after it was stored (views load in stages). */
  resize(key: string, bytes: number): void;
  has(key: string): boolean;
  delete(key: string): void;
  keys(): string[];
  readonly totalBytes: number;
  readonly size: number;
}

export function createAnalysisCache<T>(options: CacheOptions = {}): AnalysisCache<T> {
  const maxEntries = options.maxEntries ?? CACHE_MAX_ENTRIES;
  const maxBytes = options.maxBytes ?? CACHE_MAX_BYTES;
  // Map iteration order is insertion order: the first key is the least recently used.
  const slots = new Map<string, Slot<T>>();
  let total = 0;

  function evict(keep: string): void {
    for (const key of slots.keys()) {
      if (slots.size <= maxEntries && total <= maxBytes) return;
      if (key === keep) continue;
      total -= slots.get(key)!.bytes;
      slots.delete(key);
    }
  }

  return {
    get(key) {
      const slot = slots.get(key);
      if (!slot) return undefined;
      slots.delete(key);
      slots.set(key, slot);
      return slot.value;
    },
    set(key, value, bytes) {
      if (bytes > maxBytes) {
        this.delete(key);
        return false;
      }
      const existing = slots.get(key);
      if (existing) total -= existing.bytes;
      slots.delete(key);
      slots.set(key, { value, bytes });
      total += bytes;
      evict(key);
      return true;
    },
    resize(key, bytes) {
      const slot = slots.get(key);
      if (!slot) return;
      total += bytes - slot.bytes;
      slot.bytes = bytes;
      if (total > maxBytes) {
        // The grown entry alone can exceed the budget: drop the others first, then the entry itself.
        evict(key);
        if (total > maxBytes) {
          total -= slot.bytes;
          slots.delete(key);
        }
      }
    },
    has: (key) => slots.has(key),
    delete(key) {
      const slot = slots.get(key);
      if (!slot) return;
      total -= slot.bytes;
      slots.delete(key);
    },
    keys: () => [...slots.keys()],
    get totalBytes() {
      return total;
    },
    get size() {
      return slots.size;
    },
  };
}

import type { Library } from "./api";
import { createStore, useStore } from "./store";

/**
 * The library the visit is looking at: Joshua's own (`main`, the default) or the `lab` library of bot-driven recordings.
 * The choice is per visit: it survives page changes while the app stays open and is never written to prefs or storage, so a
 * reload is back on `main`. Pages put `LibraryControl` inside their filter surface and pass `library` to the API calls.
 */
const current = createStore<Library>("main");

/** The current library and its setter. Every page reads the same store, so Sessions, Setups and the Driver page agree. */
export function useLibraryChoice(): [Library, (next: Library) => void] {
  const value = useStore(current);
  return [value, (next) => current.set(next)];
}

/** Non-hook read, for code outside a component. */
export const libraryChoice = (): Library => current.get();

/** Test hook: back to the default, as a fresh visit. */
export function resetLibraryChoice(): void {
  current.set("main");
}

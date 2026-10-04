import type { Library } from "../core/api";
import { Segmented } from "./segmented";

/** Options of the library switch, in order. `Joshua` is the driver's own library (`main`); `Lab` holds bot-driven recordings. */
export function libraryOptions(mainLabel = "Joshua"): { value: Library; label: string }[] {
  return [
    { value: "main", label: mainLabel },
    { value: "lab", label: "Lab" },
  ];
}

/** Minimum inline width of the control in rem, for `FilterControl.minRem`. */
export const LIBRARY_MIN_REM = 15;

/**
 * `Library: Joshua | Lab`, a two-segment control. Place it inside the page's filter surface (a `FilterControl` of `FilterBar`).
 * The choice is per visit and never saved: wire it to `useLibraryChoice()` from `core/library.ts`, which defaults to `main` and
 * resets on reload, and pass the value to the sessions, setups and statistics calls.
 */
export function LibraryControl({
  value,
  onChange,
  mainLabel = "Joshua",
}: {
  value: Library;
  onChange(next: Library): void;
  /** The driver's first name for the main library. */
  mainLabel?: string;
}) {
  return (
    <span class="lib-ctl">
      <span class="lib-label" aria-hidden="true">
        Library
      </span>
      <Segmented<Library> label="Library" options={libraryOptions(mainLabel)} value={value} onChange={onChange} />
    </span>
  );
}

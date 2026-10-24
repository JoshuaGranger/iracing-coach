import { useEffect } from "preact/hooks";
import { useStore } from "../core/store";
import { LOOKS, applyLook, chooseLook, closeLookBar, currentLook, lookBarOpen, stepLook } from "./looks";
import "./look-bar.css";

/**
 * The look chooser: a slim strip above the app, outside the header and the page, styled the same in every look so it never takes part
 * in the comparison. Alt+L steps to the next look, Alt+Shift+L back; the close button returns to the shipped look and hides the strip.
 * Exploration tooling: it goes once a look is chosen.
 */
export function LookBar() {
  const id = useStore(currentLook);
  const open = useStore(lookBarOpen);
  const look = LOOKS.find((entry) => entry.id === id)!;
  useEffect(() => {
    void applyLook(currentLook.get());
    const onKey = (event: KeyboardEvent) => {
      if (!event.altKey || event.ctrlKey || event.metaKey || event.code !== "KeyL") return;
      event.preventDefault();
      chooseLook(stepLook(currentLook.get(), event.shiftKey ? -1 : 1));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  if (!open) return null;
  return (
    <div id="look-bar" class="look-bar" role="group" aria-label="Look">
      <span class="look-bar-label">Look</span>
      <span class="look-bar-choices">
        {LOOKS.map((entry) => (
          <button key={entry.id} type="button" class="look-bar-choice" aria-pressed={entry.id === id} title={entry.idea} onClick={() => chooseLook(entry.id)}>
            {entry.name}
          </button>
        ))}
      </span>
      <span class="look-bar-idea">{look.idea}</span>
      <span class="look-bar-keys">Alt+L</span>
      <button type="button" class="look-bar-close" aria-label="Close the look chooser and return to the shipped look" title="Back to the shipped look" onClick={closeLookBar}>
        ×
      </button>
    </div>
  );
}

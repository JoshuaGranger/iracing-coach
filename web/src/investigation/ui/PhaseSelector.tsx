import { Fragment } from "preact";

/**
 * P · Q · R: one segment per phase the event has, in running order, the current one lit in its phase band colour. A phase that
 * cannot open (not analyzed, analyzing, failed, deferred) is disabled and names its state on hover (audit S2). Arrow keys move
 * between the open segments.
 */

export interface PhaseOption {
  sessionId: number;
  /** The session type as recorded (`Race`, `Lone Qualify`, `Open Practice`, ...). */
  sessionType: string | null;
  /** Analysis state when the shell knows it; a phase without one is treated as openable. */
  state?: string | null;
  reason?: string | null;
}

export type PhaseLetter = "P" | "Q" | "R" | "W" | "T";

export function phaseLetter(type: string | null | undefined): PhaseLetter {
  const text = (type ?? "").toLowerCase();
  if (text.includes("race")) return "R";
  if (text.includes("qual")) return "Q";
  if (text.includes("warm")) return "W";
  if (text.includes("practice")) return "P";
  return "T";
}

const ORDER: Record<PhaseLetter, number> = { P: 0, W: 1, Q: 2, R: 3, T: 4 };
const WORD: Record<PhaseLetter, string> = { P: "Practice", Q: "Qualifying", R: "Race", W: "Warmup", T: "Test" };

/** The phase hue tokens the Sessions cells use (backlog #52): a warmup or test reads as practice. */
export const PHASE_HUE: Record<PhaseLetter, string> = {
  P: "var(--phase-practice-hue)",
  Q: "var(--phase-qualify-hue)",
  R: "var(--phase-race-hue)",
  W: "var(--phase-practice-hue)",
  T: "var(--phase-practice-hue)",
};

function stateText(option: PhaseOption): string | null {
  const state = option.state ?? "ready";
  if (state === "ready") return null;
  const words: Record<string, string> = { none: "Not analyzed", queued: "Queued", running: "Analyzing", analyzing: "Analyzing", failed: "Failed", deferred: "Deferred", pending: "Analyzing" };
  const word = words[state] ?? state;
  return option.reason ? `${word} · ${option.reason.replace(/_/g, " ")}` : word;
}

/**
 * Phases in running order (P, Q, R), one per letter. Of two sessions of one kind the segment takes the current session, else the
 * first that can open, else the first, so a failed duplicate never hides an analyzed one (review finding 14).
 */
export function orderedPhases(options: readonly PhaseOption[], currentSessionId: number | null = null): PhaseOption[] {
  const rank = (option: PhaseOption): number => (option.sessionId === currentSessionId ? 0 : stateText(option) === null ? 1 : 2);
  const chosen = new Map<PhaseLetter, { option: PhaseOption; index: number }>();
  options.forEach((option, index) => {
    const letter = phaseLetter(option.sessionType);
    const held = chosen.get(letter);
    if (!held || rank(option) < rank(held.option)) chosen.set(letter, { option, index });
  });
  return [...chosen.entries()]
    .sort(([a, x], [b, y]) => ORDER[a] - ORDER[b] || x.index - y.index)
    .map(([, { option }]) => option);
}

export function PhaseSelector({ phases, currentSessionId, onSelect }: { phases: readonly PhaseOption[]; currentSessionId: number; onSelect(sessionId: number): void }) {
  const ordered = orderedPhases(phases, currentSessionId);
  if (ordered.length < 2) return null;
  const move = (event: KeyboardEvent): void => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    const buttons = [...((event.currentTarget as HTMLElement).querySelectorAll<HTMLButtonElement>("button:not(:disabled)"))];
    const index = buttons.indexOf(event.target as HTMLButtonElement);
    if (index < 0) return;
    event.preventDefault();
    buttons[(index + (event.key === "ArrowRight" ? 1 : buttons.length - 1)) % buttons.length]?.focus();
  };
  return (
    <div class="ri-pqr" role="group" aria-label="Session" onKeyDown={move}>
      {ordered.map((option, index) => {
        const letter = phaseLetter(option.sessionType);
        const current = option.sessionId === currentSessionId;
        const blocked = stateText(option);
        return (
          <Fragment key={option.sessionId}>
            {index > 0 && <span class="ri-pqr-sep" aria-hidden="true">·</span>}
            <button
              type="button" class={`ri-pqr-seg ${letter.toLowerCase()}${current ? " on" : ""}`} aria-pressed={current}
              aria-label={blocked ? `${WORD[letter]} · ${blocked}` : WORD[letter]} title={blocked ? `${WORD[letter]} · ${blocked}` : WORD[letter]}
              disabled={!current && blocked !== null} data-phase={letter} data-session={option.sessionId} style={{ "--b": PHASE_HUE[letter] }}
              onClick={() => { if (!current) onSelect(option.sessionId); }}
            >{letter}</button>
          </Fragment>
        );
      })}
    </div>
  );
}

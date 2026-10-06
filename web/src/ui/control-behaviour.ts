/**
 * Focus shows for the keyboard only (round 5, U1; backlog #78). `html[data-input]` says which device was used last; in `pointer`
 * mode styles.css draws no ring or highlight on a control, and a control the pointer used gives up focus as soon as the click is
 * over, so nothing is left lit after a dropdown, button, segment, lap row or card is pressed. A text field keeps its focus: it is
 * being typed in. The control recipe's text centring is `control-text.ts` and the `.ctl-t` rule in styles.css.
 */

/**
 * The elements a pointer press can leave focused and lit: controls and cards. A text field is not one of them: it keeps focus while it is
 * typed in. Neither is a chart mark (a lap dot, a pit tag, a run line): its focus shows its readout, which a tap on a touch screen
 * depends on, and it draws no ring for the pointer.
 */
export const POINTER_BLUR_SELECTOR = 'button, a[href], summary, [role="button"], [role="tab"], [role="switch"], [role="option"], input[type="checkbox"], input[type="radio"], input[type="button"], input[type="range"], [role="listitem"][tabindex="0"]';

export type InputMode = "pointer" | "keyboard";

/** Keys that are not use of the keyboard as a way of operating the page: modifiers alone. */
const MODIFIER_KEYS = new Set(["Shift", "Control", "Alt", "Meta", "CapsLock", "NumLock", "ScrollLock", "OS", "AltGraph"]);

export function inputModeForKey(key: string): InputMode | null {
  return MODIFIER_KEYS.has(key) ? null : "keyboard";
}

/**
 * The element to blur once a pointer click is over: whichever control holds focus then, the pressed one or one the app handed focus to
 * (a dialog giving focus back to its opener). Left alone: a text field (being typed in), a native select (blurring would close its open
 * list; it blurs on change), a programmatic focus target (`tabindex="-1"`: a dialog surface or an open list, which keyboard handling
 * relies on), a keyboard-driven widget (`role="application"`: a chart surface whose arrow keys move the cursor), a scrolling region
 * (it takes the arrow keys to scroll) and anything inside an open dialog.
 */
export function blurAfterPointer(active: Element | null): Element | null {
  if (!active || active === (active.ownerDocument?.body ?? null)) return null;
  if (!active.matches(POINTER_BLUR_SELECTOR) || active.tagName === "SELECT") return null;
  if (active.getAttribute("tabindex") === "-1" || active.getAttribute("role") === "application") return null;
  if ((active as HTMLElement).scrollHeight > (active as HTMLElement).clientHeight + 1) return null;
  if (active.closest('[role="dialog"], [aria-modal="true"]')) return null;
  return active;
}

/** Install the focus rule. Returns the function that removes the listeners (tests). */
export function installControlBehaviour(doc: Document = document): () => void {
  const root = doc.documentElement;
  const set = (mode: InputMode): void => {
    if (root.dataset.input !== mode) root.dataset.input = mode;
  };
  const onPointerDown = (): void => set("pointer");
  const onKeyDown = (event: KeyboardEvent): void => {
    const mode = inputModeForKey(event.key);
    if (mode) set(mode);
  };
  const onClick = (event: MouseEvent): void => {
    // A click with no pointer behind it (Enter or Space on a button, an assistive tool) is keyboard use.
    if (event.detail === 0) return;
    // After the app's own handlers: a handler that moves focus on purpose has the last word.
    requestAnimationFrame(() => {
      if (root.dataset.input !== "pointer") return;
      const target = blurAfterPointer(doc.activeElement);
      (target as Partial<HTMLOrSVGElement> | null)?.blur?.();
    });
  };
  const onChange = (event: Event): void => {
    const target = event.target;
    if (root.dataset.input === "pointer" && target instanceof HTMLSelectElement) requestAnimationFrame(() => target.blur());
  };
  doc.addEventListener("pointerdown", onPointerDown, true);
  doc.addEventListener("keydown", onKeyDown, true);
  doc.addEventListener("click", onClick);
  doc.addEventListener("change", onChange, true);
  return () => {
    doc.removeEventListener("pointerdown", onPointerDown, true);
    doc.removeEventListener("keydown", onKeyDown, true);
    doc.removeEventListener("click", onClick);
    doc.removeEventListener("change", onChange, true);
  };
}

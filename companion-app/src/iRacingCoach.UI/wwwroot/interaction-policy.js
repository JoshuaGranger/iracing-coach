(function () {
  "use strict";

  if (window.iracingCoachInteractionPolicy) return;

  const pointerWindowMs = 1600;
  const pointerFocusReleases = new WeakSet();
  const pointerSelectOrigins = new WeakSet();
  const pointerSelectAt = new WeakMap();

  const controlSelector = "button,[role='button'],[role='tab'],input[type='button'],input[type='submit'],input[type='reset'],input[type='checkbox'],input[type='radio'],input[type='range'],input[type='color']";

  const rememberPointer = (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    const pointerSelect = target.closest("select");
    if (pointerSelect instanceof HTMLSelectElement) {
      pointerSelectOrigins.add(pointerSelect);
      pointerSelectAt.set(pointerSelect, performance.now());
    }
    const control = target.closest("[data-pointer-no-focus]");
    if (!(control instanceof HTMLElement)) return;

    // Custom popover triggers must open without ever acquiring transient pointer
    // focus. Preventing that default before the click also avoids a Blazor
    // rerender replacing the focused node and emitting a misleading focusout.
    event.preventDefault();
    if (document.activeElement === control) control.blur();
  };

  const releaseFocus = (control) => {
    if (document.activeElement === control) {
      pointerFocusReleases.add(control);
      control.blur();
    }
  };

  const forgetPointerFocusRelease = (event) => {
    const control = event.target;
    if (control instanceof HTMLElement) pointerFocusReleases.delete(control);
  };

  const releasePointerSelect = (event) => {
    const control = event.target;
    if (!(control instanceof HTMLSelectElement)) return;
    const pointerAt = pointerSelectAt.get(control);
    const fromThisControl = pointerSelectOrigins.has(control)
      && pointerAt !== undefined
      && (performance.now() - pointerAt) <= pointerWindowMs;
    pointerSelectOrigins.delete(control);
    pointerSelectAt.delete(control);
    if (!fromThisControl) return;

    requestAnimationFrame(() => releaseFocus(control));
  };

  const releasePointerControl = (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;

    const control = target.closest(controlSelector);
    if (!(control instanceof HTMLElement)) return;
    if (control.hasAttribute("data-pointer-no-focus")) return;

    requestAnimationFrame(() => releaseFocus(control));
  };

  document.addEventListener("pointerdown", rememberPointer, true);
  document.addEventListener("pointerup", releasePointerControl, true);
  document.addEventListener("change", releasePointerSelect, true);
  document.addEventListener("focusin", forgetPointerFocusRelease, true);

  window.iracingCoachInteractionPolicy = {
    consumePointerFocusRelease: function (control) {
      if (!(control instanceof HTMLElement) || !pointerFocusReleases.has(control)) return false;
      pointerFocusReleases.delete(control);
      return true;
    },
    dispose: function () {
      document.removeEventListener("pointerdown", rememberPointer, true);
      document.removeEventListener("pointerup", releasePointerControl, true);
      document.removeEventListener("change", releasePointerSelect, true);
      document.removeEventListener("focusin", forgetPointerFocusRelease, true);
      delete window.iracingCoachInteractionPolicy;
    }
  };
})();

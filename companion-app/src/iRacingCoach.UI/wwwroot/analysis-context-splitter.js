(function () {
  "use strict";

  const minimumRatio = 1 / 3;
  const maximumRatio = 2 / 3;
  let sharedRatio = 0.43;
  const sessions = new WeakMap();
  const activeSessions = new Set();
  const clamp = (value) => Math.max(minimumRatio, Math.min(maximumRatio, value));

  function apply(state) {
    const trackShare = sharedRatio;
    state.container.style.setProperty("--analysis-context-track-share", `${trackShare}fr`);
    state.container.style.setProperty("--analysis-context-laps-share", `${1 - trackShare}fr`);
    state.splitter.setAttribute("aria-valuenow", String(Math.round(trackShare * 100)));
  }

  function applyToEverySession() {
    activeSessions.forEach(apply);
  }

  function setRatio(ratio) {
    sharedRatio = clamp(ratio);
    applyToEverySession();
  }

  function ratioAtPointer(state, clientY) {
    const bounds = state.container.getBoundingClientRect();
    const splitterHeight = state.splitter.getBoundingClientRect().height;
    const availableHeight = Math.max(1, bounds.height - splitterHeight);
    return (clientY - bounds.top - splitterHeight / 2) / availableHeight;
  }

  function initialize(container, splitter) {
    if (!container || !splitter) return;
    let state = sessions.get(splitter);
    if (state) {
      state.container = container;
      apply(state);
      return;
    }

    state = {
      container,
      splitter,
      dragging: false,
      pointerId: null
    };

    state.pointerDown = (event) => {
      if (event.button !== 0 || !event.isPrimary) return;
      state.dragging = true;
      state.pointerId = event.pointerId;
      splitter.setPointerCapture?.(event.pointerId);
      container.classList.add("context-resizing");
      setRatio(ratioAtPointer(state, event.clientY));
      event.preventDefault();
    };
    state.pointerMove = (event) => {
      if (!state.dragging || event.pointerId !== state.pointerId) return;
      setRatio(ratioAtPointer(state, event.clientY));
    };
    state.pointerUp = (event) => {
      if (!state.dragging || event.pointerId !== state.pointerId) return;
      state.dragging = false;
      state.pointerId = null;
      splitter.releasePointerCapture?.(event.pointerId);
      container.classList.remove("context-resizing");
    };
    state.keyDown = (event) => {
      let next = sharedRatio;
      if (event.key === "ArrowUp") next -= 0.02;
      else if (event.key === "ArrowDown") next += 0.02;
      else if (event.key === "Home") next = minimumRatio;
      else if (event.key === "End") next = maximumRatio;
      else return;
      setRatio(next);
      event.preventDefault();
    };

    splitter.addEventListener("pointerdown", state.pointerDown);
    splitter.addEventListener("pointermove", state.pointerMove);
    splitter.addEventListener("pointerup", state.pointerUp);
    splitter.addEventListener("pointercancel", state.pointerUp);
    splitter.addEventListener("keydown", state.keyDown);
    sessions.set(splitter, state);
    activeSessions.add(state);
    apply(state);
  }

  function dispose(splitter) {
    const state = sessions.get(splitter);
    if (!state) return;
    splitter.removeEventListener("pointerdown", state.pointerDown);
    splitter.removeEventListener("pointermove", state.pointerMove);
    splitter.removeEventListener("pointerup", state.pointerUp);
    splitter.removeEventListener("pointercancel", state.pointerUp);
    splitter.removeEventListener("keydown", state.keyDown);
    state.container.classList.remove("context-resizing");
    activeSessions.delete(state);
    sessions.delete(splitter);
  }

  window.iracingCoachAnalysisContextSplitter = { initialize, dispose };
})();

(function () {
  "use strict";

  const sessions = new WeakMap();
  const minimumZoomWindow = 1 / 8;
  const clamp = (value, minimum, maximum) => Math.max(minimum, Math.min(maximum, value));

  function parseViewBox(value) {
    const parts = String(value || "").trim().split(/[\s,]+/).map(Number);
    if (parts.length !== 4 || parts.some((part) => !Number.isFinite(part)) || parts[2] <= 0 || parts[3] <= 0) return null;
    return { x: parts[0], y: parts[1], width: parts[2], height: parts[3] };
  }

  function copyViewBox(value) {
    return { x: value.x, y: value.y, width: value.width, height: value.height };
  }

  function apply(state) {
    const view = state.view;
    state.element.setAttribute("viewBox", `${view.x.toFixed(4)} ${view.y.toFixed(4)} ${view.width.toFixed(4)} ${view.height.toFixed(4)}`);
    const zoom = state.base.width / view.width;
    state.element.dataset.mapZoom = zoom.toFixed(3);
    for (const cursor of state.element.querySelectorAll("[data-map-cursor-radius]")) {
      const baseRadius = Number(cursor.dataset.mapCursorRadius);
      if (Number.isFinite(baseRadius) && baseRadius > 0)
        cursor.setAttribute("r", (baseRadius / zoom).toFixed(4));
    }
  }

  function constrain(state, view) {
    const base = state.base;
    view.width = clamp(view.width, base.width * minimumZoomWindow, base.width);
    view.height = view.width * base.height / base.width;
    if (view.height > base.height) {
      view.height = base.height;
      view.width = view.height * base.width / base.height;
    }
    view.x = clamp(view.x, base.x, base.x + base.width - view.width);
    view.y = clamp(view.y, base.y, base.y + base.height - view.height);
    return view;
  }

  function svgPoint(element, clientX, clientY) {
    const matrix = element.getScreenCTM?.();
    if (matrix && typeof element.createSVGPoint === "function") {
      const point = element.createSVGPoint();
      point.x = clientX;
      point.y = clientY;
      return point.matrixTransform(matrix.inverse());
    }
    const rect = element.getBoundingClientRect();
    const view = element.viewBox?.baseVal;
    if (!rect || rect.width < 1 || rect.height < 1 || !view) return null;
    return {
      x: view.x + (clientX - rect.left) / rect.width * view.width,
      y: view.y + (clientY - rect.top) / rect.height * view.height
    };
  }

  function fitState(state) {
    state.view = copyViewBox(state.base);
    apply(state);
  }

  function initialize(element, raceKey, baseViewBox) {
    if (!element) return;
    const base = parseViewBox(baseViewBox);
    if (!base) return;
    let state = sessions.get(element);
    if (state) {
      const sameRace = state.raceKey === raceKey;
      state.raceKey = raceKey;
      state.base = base;
      if (!sameRace) state.view = copyViewBox(base);
      else constrain(state, state.view);
      apply(state);
      return;
    }

    state = {
      element,
      raceKey,
      base,
      view: copyViewBox(base),
      dragging: false,
      pointerId: null,
      lastClientX: 0,
      lastClientY: 0
    };

    state.wheel = (event) => {
      if (Math.abs(event.deltaY) < 0.01) return;
      const anchor = svgPoint(element, event.clientX, event.clientY);
      if (!anchor) return;
      event.preventDefault();
      const factor = Math.exp(clamp(event.deltaY, -120, 120) * 0.0018);
      const previous = state.view;
      const next = copyViewBox(previous);
      next.width = previous.width * factor;
      next.height = previous.height * factor;
      const ratio = next.width / previous.width;
      next.x = anchor.x - (anchor.x - previous.x) * ratio;
      next.y = anchor.y - (anchor.y - previous.y) * ratio;
      state.view = constrain(state, next);
      apply(state);
    };
    state.pointerDown = (event) => {
      if (event.button !== 0 || !event.isPrimary) return;
      state.dragging = true;
      state.pointerId = event.pointerId;
      state.lastClientX = event.clientX;
      state.lastClientY = event.clientY;
      element.setPointerCapture?.(event.pointerId);
      element.classList.add("map-panning");
      event.preventDefault();
    };
    state.pointerMove = (event) => {
      if (!state.dragging || event.pointerId !== state.pointerId) return;
      const previous = svgPoint(element, state.lastClientX, state.lastClientY);
      const current = svgPoint(element, event.clientX, event.clientY);
      state.lastClientX = event.clientX;
      state.lastClientY = event.clientY;
      if (!previous || !current) return;
      const next = copyViewBox(state.view);
      next.x += previous.x - current.x;
      next.y += previous.y - current.y;
      state.view = constrain(state, next);
      apply(state);
    };
    state.pointerUp = (event) => {
      if (!state.dragging || event.pointerId !== state.pointerId) return;
      state.dragging = false;
      state.pointerId = null;
      element.releasePointerCapture?.(event.pointerId);
      element.classList.remove("map-panning");
    };

    element.addEventListener("wheel", state.wheel, { passive: false });
    element.addEventListener("pointerdown", state.pointerDown);
    element.addEventListener("pointermove", state.pointerMove);
    element.addEventListener("pointerup", state.pointerUp);
    element.addEventListener("pointercancel", state.pointerUp);
    sessions.set(element, state);
    apply(state);
  }

  function fit(element) {
    const state = sessions.get(element);
    if (state) fitState(state);
  }

  function blur(element) {
    if (element && typeof element.blur === "function") requestAnimationFrame(() => element.blur());
  }

  function dispose(element) {
    const state = sessions.get(element);
    if (!state) return;
    element.removeEventListener("wheel", state.wheel);
    element.removeEventListener("pointerdown", state.pointerDown);
    element.removeEventListener("pointermove", state.pointerMove);
    element.removeEventListener("pointerup", state.pointerUp);
    element.removeEventListener("pointercancel", state.pointerUp);
    element.classList.remove("map-panning");
    sessions.delete(element);
  }

  window.iracingCoachAnalysisTrackMap = { initialize, fit, blur, dispose };
})();

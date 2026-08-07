(() => {
  const editors = new WeakMap();
  const dragThreshold = 5;

  function clamp(value, minimum, maximum) {
    return Math.max(minimum, Math.min(maximum, value));
  }

  function rowIndex(element) {
    const value = Number.parseInt(element?.dataset?.rowIndex || "", 10);
    return Number.isFinite(value) ? value : -1;
  }

  function rowTargets(root, kind = null) {
    return Array.from(root.querySelectorAll("[data-analysis-row-target]"))
      .filter(element => !kind || element.dataset.analysisRowTarget === kind)
      .sort((left, right) => rowIndex(left) - rowIndex(right));
  }

  function chartTarget(root, rowId) {
    return rowTargets(root, "chart").find(element => element.dataset.rowId === rowId) || null;
  }

  function visualRect(root, target) {
    const chart = chartTarget(root, target?.dataset?.rowId);
    return (chart || target)?.getBoundingClientRect() || null;
  }

  function elementAtPointer(root, event) {
    for (const element of document.elementsFromPoint(event.clientX, event.clientY)) {
      const target = element.closest && element.closest("[data-analysis-row-target]");
      if (target && root.contains(target)) return target;
    }
    return null;
  }

  function chartBounds(root) {
    return root.querySelector("[data-analysis-trace-chart]")?.getBoundingClientRect() || null;
  }

  function chartTargetAtPointer(root, event) {
    const chart = chartBounds(root);
    if (!chart || event.clientX < chart.left || event.clientX > chart.right) return null;
    return rowTargets(root, "chart").find(target => {
      const rect = target.getBoundingClientRect();
      return event.clientY >= rect.top && event.clientY <= rect.bottom;
    }) || null;
  }

  function insertionAtPointer(root, event) {
    const chart = chartBounds(root);
    const targets = rowTargets(root, "chart");
    if (!chart || targets.length === 0 || event.clientX < chart.left || event.clientX > chart.right || event.clientY < chart.top || event.clientY > chart.bottom) return null;
    for (const target of targets) {
      const rect = target.getBoundingClientRect();
      if (event.clientY < rect.top) return { index: rowIndex(target), top: rect.top, left: chart.left, width: chart.width };
      if (event.clientY <= rect.bottom) return null;
    }
    const last = targets[targets.length - 1].getBoundingClientRect();
    return { index: targets.length, top: last.bottom, left: chart.left, width: chart.width };
  }

  function createGhost(session, event) {
    const ghost = document.createElement("div");
    ghost.className = "live-layout-drag-ghost analysis-trace-drag-ghost";
    const grip = document.createElement("span");
    grip.textContent = "⋮⋮";
    grip.setAttribute("aria-hidden", "true");
    const label = document.createElement("strong");
    label.textContent = session.name;
    ghost.append(grip, label);
    document.body.appendChild(ghost);
    session.ghost = ghost;
    moveGhost(session, event);
  }

  function moveGhost(session, event) {
    if (!session.ghost) return;
    const width = session.ghost.offsetWidth;
    const height = session.ghost.offsetHeight;
    session.ghost.style.left = `${Math.round(clamp(event.clientX + 14, 8, Math.max(8, window.innerWidth - width - 8)))}px`;
    session.ghost.style.top = `${Math.round(clamp(event.clientY + 14, 8, Math.max(8, window.innerHeight - height - 8)))}px`;
  }

  function ensurePreview(session) {
    if (session.preview) return session.preview;
    const preview = document.createElement("div");
    preview.className = "live-grid-drop-preview analysis-trace-drop-preview";
    preview.appendChild(document.createElement("span"));
    document.body.appendChild(preview);
    session.preview = preview;
    return preview;
  }

  function showPreview(session, rect, message, valid, replacement = false, insertion = false) {
    const preview = ensurePreview(session);
    preview.classList.toggle("invalid", !valid);
    preview.classList.toggle("replacement", valid && replacement);
    preview.classList.toggle("insertion", insertion);
    preview.style.left = `${Math.round(rect.left)}px`;
    preview.style.top = `${Math.round(rect.top)}px`;
    preview.style.width = `${Math.round(Math.max(4, rect.width))}px`;
    preview.style.height = `${Math.round(insertion ? 4 : Math.max(20, rect.height))}px`;
    preview.querySelector("span").textContent = message;
    session.valid = valid;
  }

  function invalidate(session, message = "Move over a trace chart") {
    const sourceRect = session.source.getBoundingClientRect();
    showPreview(session, sourceRect, message, false);
    session.action = null;
  }

  function insertionPreview(state, session, insertion, targetIndex) {
    const maximum = state.options.rowCount;
    const clamped = clamp(targetIndex, 0, maximum);
    if (session.kind === "row") {
      const adjusted = clamped > session.originalIndex ? clamped - 1 : clamped;
      const finalIndex = clamp(adjusted, 0, Math.max(0, state.options.rowCount - 1));
      const valid = finalIndex !== session.originalIndex;
      showPreview(session, { left: insertion.left, top: insertion.top - 2, width: insertion.width, height: 4 }, valid ? `Move ${session.name} to row ${finalIndex + 1}` : "Already here", valid, false, true);
      session.action = valid ? { type: "move-row", rowId: session.rowId, targetIndex: finalIndex } : null;
      return;
    }
    const valid = state.options.rowCount < state.options.maximumRows;
    showPreview(session, { left: insertion.left, top: insertion.top - 2, width: insertion.width, height: 4 }, valid ? `Add ${session.name} as row ${clamped + 1}` : `${state.options.maximumRows} chart limit`, valid, false, true);
    session.action = valid ? { type: "insert-signal", signalId: session.signalId, targetIndex: clamped } : null;
  }

  function targetPreview(state, session, target, event) {
    const rect = visualRect(state.root, target);
    if (!rect) { invalidate(session); return; }
    const targetIndex = rowIndex(target);
    const edge = Math.min(11, Math.max(5, rect.height * .12));
    if (event.clientY <= rect.top + edge || event.clientY >= rect.bottom - edge) {
      insertionPreview(state, session, {
        index: targetIndex + (event.clientY >= rect.bottom - edge ? 1 : 0),
        top: event.clientY >= rect.bottom - edge ? rect.bottom : rect.top,
        left: rect.left,
        width: rect.width
      }, targetIndex + (event.clientY >= rect.bottom - edge ? 1 : 0));
      return;
    }
    if (session.kind === "row") {
      const boundary = targetIndex + (event.clientY >= rect.top + rect.height / 2 ? 1 : 0);
      insertionPreview(state, session, { index: boundary, top: event.clientY >= rect.top + rect.height / 2 ? rect.bottom : rect.top, left: rect.left, width: rect.width }, boundary);
      return;
    }

    const rowId = target.dataset.rowId;
    const primary = target.dataset.primaryName || "this trace";
    const secondary = target.dataset.secondaryName || "";
    if (session.name === primary || session.name === secondary) {
      showPreview(session, rect, `${session.name} is already in this chart`, false);
      session.action = null;
      return;
    }
    const replacement = secondary.length > 0;
    const message = replacement ? `Replace ${secondary} with ${session.name}` : `Pair ${primary} with ${session.name}`;
    showPreview(session, rect, message, true, replacement);
    session.action = { type: "place-signal", signalId: session.signalId, rowId };
  }

  function updateTarget(state, session, event) {
    const target = elementAtPointer(state.root, event) || chartTargetAtPointer(state.root, event);
    if (target) { targetPreview(state, session, target, event); return; }
    const insertion = insertionAtPointer(state.root, event);
    if (insertion) { insertionPreview(state, session, insertion, insertion.index); return; }
    invalidate(session);
  }

  function autoScroll(state, event) {
    const host = state.root.closest(".workspace");
    if (!host || host.scrollHeight <= host.clientHeight) return;
    const rect = host.getBoundingClientRect();
    const threshold = 54;
    const maximumStep = 22;
    let delta = 0;
    if (event.clientY < rect.top + threshold) delta = -maximumStep * (1 - clamp((event.clientY - rect.top) / threshold, 0, 1));
    else if (event.clientY > rect.bottom - threshold) delta = maximumStep * (1 - clamp((rect.bottom - event.clientY) / threshold, 0, 1));
    if (Math.abs(delta) >= 1) host.scrollTop += delta;
  }

  function beginVisibleGesture(state, session, event) {
    if (session.active) return;
    session.active = true;
    state.root.classList.add("analysis-trace-gesture-active");
    session.source.classList.add("is-gesture-source");
    createGhost(session, event);
  }

  function onPointerMove(state, event) {
    const session = state.session;
    if (!session || event.pointerId !== session.pointerId) return;
    if (!session.active && Math.hypot(event.clientX - session.startX, event.clientY - session.startY) < dragThreshold) return;
    beginVisibleGesture(state, session, event);
    event.preventDefault();
    autoScroll(state, event);
    moveGhost(session, event);
    updateTarget(state, session, event);
  }

  function captureRects(root) {
    const captured = new Map();
    for (const element of root.querySelectorAll("[data-analysis-render-row], .trace-row-label-shell")) {
      const prefix = element.hasAttribute("data-analysis-render-row") ? "chart" : "label";
      captured.set(`${prefix}:${element.dataset.rowId}`, element.getBoundingClientRect());
    }
    return captured;
  }

  function animateReflow(state, before) {
    if (state.options.reducedMotion || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    for (const element of state.root.querySelectorAll("[data-analysis-render-row], .trace-row-label-shell")) {
      const prefix = element.hasAttribute("data-analysis-render-row") ? "chart" : "label";
      const prior = before.get(`${prefix}:${element.dataset.rowId}`);
      if (!prior) continue;
      const next = element.getBoundingClientRect();
      const deltaY = prior.top - next.top;
      if (Math.abs(deltaY) < .5) continue;
      element.animate([{ transform: `translateY(${deltaY}px)` }, { transform: "none" }], { duration: 200, easing: "cubic-bezier(.2,0,0,1)" });
    }
  }

  function removeGestureVisuals(state, session) {
    state.root.classList.remove("analysis-trace-gesture-active");
    session.source.classList.remove("is-gesture-source");
    if (session.ghost) session.ghost.remove();
    if (session.preview) session.preview.remove();
    try { session.capture.releasePointerCapture(session.pointerId); } catch (_) { }
  }

  async function completeGesture(state, event, cancelled) {
    const session = state.session;
    if (!session || event.pointerId !== undefined && event.pointerId !== session.pointerId) return;
    state.session = null;
    if (cancelled || !session.active || !session.valid || !session.action) {
      removeGestureVisuals(state, session);
      return;
    }

    state.committing = true;
    const before = captureRects(state.root);
    if (session.preview) session.preview.classList.add("committing");
    let succeeded = false;
    try {
      const action = session.action;
      if (action.type === "move-row") succeeded = await state.dotnet.invokeMethodAsync("MoveTraceRowToIndex", action.rowId, action.targetIndex);
      else if (action.type === "insert-signal") succeeded = await state.dotnet.invokeMethodAsync("InsertTraceSignalRow", action.signalId, action.targetIndex);
      else if (action.type === "place-signal") succeeded = await state.dotnet.invokeMethodAsync("PlaceTraceSignal", action.rowId, action.signalId);
    } catch (_) {
      succeeded = false;
    }
    state.committing = false;
    removeGestureVisuals(state, session);
    requestAnimationFrame(() => requestAnimationFrame(() => {
      if (succeeded) animateReflow(state, before);
      else {
        const chart = state.root.querySelector("[data-analysis-trace-chart]");
        if (chart && !state.options.reducedMotion && !window.matchMedia("(prefers-reduced-motion: reduce)").matches)
          chart.animate([{ transform: "translateX(0)" }, { transform: "translateX(-3px)" }, { transform: "translateX(3px)" }, { transform: "translateX(0)" }], { duration: 160 });
      }
    }));
  }

  function cancelGesture(state) {
    const session = state.session;
    if (!session) return;
    state.session = null;
    removeGestureVisuals(state, session);
  }

  function onPointerDown(state, event) {
    if (!state.options.editing || event.button !== 0 || state.session || state.committing) return;
    const noDrag = event.target.closest && event.target.closest("[data-analysis-no-drag]");
    if (noDrag) return;
    const rowSource = event.target.closest && event.target.closest("[data-analysis-drag-row]");
    const signalSource = event.target.closest && event.target.closest("[data-analysis-drag-signal]");
    const source = rowSource || signalSource;
    if (!source || !state.root.contains(source)) return;
    const kind = rowSource ? "row" : "signal";
    const capture = source;
    event.stopPropagation();
    try { capture.setPointerCapture(event.pointerId); } catch (_) { }
    state.session = {
      kind,
      source,
      capture,
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      rowId: rowSource?.dataset.rowId || rowSource?.dataset.analysisDragRow || null,
      signalId: signalSource?.dataset.signalId || signalSource?.dataset.analysisDragSignal || null,
      name: rowSource?.dataset.rowName || signalSource?.dataset.signalName || "trace",
      originalIndex: rowSource ? rowIndex(state.root.querySelector(`[data-analysis-row-target="label"][data-row-id="${rowSource.dataset.rowId}"]`)) : -1,
      active: false,
      valid: false,
      action: null,
      ghost: null,
      preview: null
    };
  }

  function configure(root, options) {
    const state = editors.get(root);
    if (!state) return;
    state.options = { editing: false, reducedMotion: false, rowCount: 0, maximumRows: 10, ...(options || {}) };
    if (!state.options.editing) cancelGesture(state);
  }

  function initialize(root, dotnet, options) {
    if (!root) return;
    const existing = editors.get(root);
    if (existing) {
      existing.dotnet = dotnet;
      configure(root, options);
      return;
    }
    const state = { root, dotnet, options: {}, session: null, committing: false };
    state.pointerDown = event => onPointerDown(state, event);
    state.pointerMove = event => onPointerMove(state, event);
    state.pointerUp = event => void completeGesture(state, event, false);
    state.pointerCancel = event => void completeGesture(state, event, true);
    state.lostPointerCapture = event => { if (state.session && event.pointerId === state.session.pointerId) void completeGesture(state, event, true); };
    state.windowBlur = () => cancelGesture(state);
    state.keyDown = event => {
      if (event.key === "Escape" && state.session) {
        event.preventDefault();
        event.stopPropagation();
        cancelGesture(state);
      }
    };
    root.addEventListener("pointerdown", state.pointerDown, true);
    root.addEventListener("pointermove", state.pointerMove, true);
    root.addEventListener("pointerup", state.pointerUp, true);
    root.addEventListener("pointercancel", state.pointerCancel, true);
    root.addEventListener("lostpointercapture", state.lostPointerCapture, true);
    root.addEventListener("keydown", state.keyDown, true);
    window.addEventListener("blur", state.windowBlur);
    editors.set(root, state);
    configure(root, options);
  }

  function dispose(root) {
    const state = editors.get(root);
    if (!state) return;
    cancelGesture(state);
    root.removeEventListener("pointerdown", state.pointerDown, true);
    root.removeEventListener("pointermove", state.pointerMove, true);
    root.removeEventListener("pointerup", state.pointerUp, true);
    root.removeEventListener("pointercancel", state.pointerCancel, true);
    root.removeEventListener("lostpointercapture", state.lostPointerCapture, true);
    root.removeEventListener("keydown", state.keyDown, true);
    window.removeEventListener("blur", state.windowBlur);
    editors.delete(root);
  }

  window.iracingCoachAnalysisTraceLayout = { initialize, configure, dispose };
})();

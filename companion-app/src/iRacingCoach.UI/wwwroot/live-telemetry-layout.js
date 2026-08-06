(function () {
  "use strict";

  const layouts = new WeakMap();
  const dragThreshold = 5;
  const snapHysteresis = 0.56;

  function clamp(value, minimum, maximum) {
    return Math.max(minimum, Math.min(maximum, value));
  }

  function number(value, fallback) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  }

  function configure(root, options) {
    const state = layouts.get(root);
    if (!state) return;
    state.options = {
      editing: !!(options && options.editing),
      reducedMotion: !!(options && options.reducedMotion),
      rows: Math.max(1, number(options && options.rows, 1)),
      columns: Math.max(1, number(options && options.columns, 1))
    };
    if (!state.options.editing && state.session) cancelGesture(state);
    scheduleGridFit(state);
  }

  function fitGrid(state) {
    const viewport = state.root.querySelector("[data-live-grid-viewport]");
    const grid = state.root.querySelector("[data-live-grid]");
    if (!viewport || !grid) return;

    const viewportStyle = getComputedStyle(viewport);
    const gridStyle = getComputedStyle(grid);
    const columns = Math.max(1, number(grid.dataset.columns, state.options.columns));
    const rows = Math.max(1, number(grid.dataset.rows, state.options.rows));
    const horizontalPadding = (parseFloat(viewportStyle.paddingLeft) || 0) + (parseFloat(viewportStyle.paddingRight) || 0);
    const verticalPadding = (parseFloat(viewportStyle.paddingTop) || 0) + (parseFloat(viewportStyle.paddingBottom) || 0);
    const columnGap = parseFloat(gridStyle.columnGap) || 0;
    const rowGap = parseFloat(gridStyle.rowGap) || 0;
    const availableWidth = Math.max(1, viewport.clientWidth - horizontalPadding - columnGap * (columns - 1));
    const availableHeight = Math.max(1, viewport.clientHeight - verticalPadding - rowGap * (rows - 1));
    const cellSize = Math.max(1, Math.min(availableWidth / columns, availableHeight / rows));
    const width = cellSize * columns + columnGap * (columns - 1);
    const height = cellSize * rows + rowGap * (rows - 1);

    grid.style.setProperty("--live-cell-size", `${cellSize}px`);
    grid.style.width = `${width}px`;
    grid.style.height = `${height}px`;
  }

  function scheduleGridFit(state) {
    if (state.fitFrame) cancelAnimationFrame(state.fitFrame);
    state.fitFrame = requestAnimationFrame(() => {
      state.fitFrame = 0;
      fitGrid(state);
    });
  }

  function gridMetrics(state) {
    fitGrid(state);
    const grid = state.root.querySelector("[data-live-grid]");
    if (!grid) return null;
    const rect = grid.getBoundingClientRect();
    const style = getComputedStyle(grid);
    const columns = Math.max(1, number(grid.dataset.columns, state.options.columns));
    const rows = Math.max(1, number(grid.dataset.rows, state.options.rows));
    const leftPadding = parseFloat(style.paddingLeft) || 0;
    const rightPadding = parseFloat(style.paddingRight) || 0;
    const topPadding = parseFloat(style.paddingTop) || 0;
    const bottomPadding = parseFloat(style.paddingBottom) || 0;
    const columnGap = parseFloat(style.columnGap) || 0;
    const rowGap = parseFloat(style.rowGap) || 0;
    const contentWidth = Math.max(1, rect.width - leftPadding - rightPadding);
    const contentHeight = Math.max(1, rect.height - topPadding - bottomPadding);
    const cellWidth = Math.max(1, (contentWidth - columnGap * (columns - 1)) / columns);
    const cellHeight = Math.max(1, (contentHeight - rowGap * (rows - 1)) / rows);
    return {
      grid,
      rect,
      columns,
      rows,
      leftPadding,
      topPadding,
      columnGap,
      rowGap,
      cellWidth,
      cellHeight,
      columnStep: cellWidth + columnGap,
      rowStep: cellHeight + rowGap,
      contentLeft: rect.left + leftPadding,
      contentTop: rect.top + topPadding
    };
  }

  function tileData(tile) {
    return {
      id: tile.dataset.tileId,
      name: tile.dataset.metricName || "widget",
      row: number(tile.dataset.row, 0),
      column: number(tile.dataset.column, 0),
      rowSpan: Math.max(1, number(tile.dataset.rowSpan, 1)),
      columnSpan: Math.max(1, number(tile.dataset.columnSpan, 1))
    };
  }

  function tileAtCell(state, row, column) {
    return Array.from(state.root.querySelectorAll("[data-live-tile]"), tileData)
      .find(tile => row >= tile.row && row < tile.row + tile.rowSpan && column >= tile.column && column < tile.column + tile.columnSpan) || null;
  }

  function tileAtPointer(state, event, row, column) {
    for (const tileElement of state.root.querySelectorAll("[data-live-tile]")) {
      const rect = tileElement.getBoundingClientRect();
      if (event.clientX >= rect.left && event.clientX <= rect.right && event.clientY >= rect.top && event.clientY <= rect.bottom) return tileData(tileElement);
    }
    const hoveredElement = document.elementFromPoint(event.clientX, event.clientY);
    const hoveredTile = hoveredElement?.closest?.("[data-live-tile]");
    if (hoveredTile) return tileData(hoveredTile);
    return tileAtCell(state, row, column);
  }

  function placementCanPack(state, session, placement, metrics) {
    const pendingMetricId = "__pending_metric__";
    const tiles = Array.from(state.root.querySelectorAll("[data-live-tile]"), tileData);
    const firstId = session.kind === "metric" ? pendingMetricId : session.original.id;
    if (session.kind === "metric") {
      tiles.push({ id: pendingMetricId, row: placement.row, column: placement.column, rowSpan: 1, columnSpan: 1 });
    } else {
      const active = tiles.find(tile => tile.id === firstId);
      if (!active) return false;
      Object.assign(active, placement);
    }

    tiles.sort((left, right) => {
      const leftPriority = left.id === firstId ? 0 : 1;
      const rightPriority = right.id === firstId ? 0 : 1;
      return leftPriority - rightPriority || left.row - right.row || left.column - right.column || (left.id < right.id ? -1 : left.id > right.id ? 1 : 0);
    });

    const occupied = new Set();
    const fits = (row, column, rowSpan, columnSpan) => {
      if (row < 0 || column < 0 || row + rowSpan > metrics.rows || column + columnSpan > metrics.columns) return false;
      for (let r = row; r < row + rowSpan; r++) for (let c = column; c < column + columnSpan; c++) if (occupied.has(`${r}:${c}`)) return false;
      return true;
    };
    const occupy = (row, column, rowSpan, columnSpan) => {
      for (let r = row; r < row + rowSpan; r++) for (let c = column; c < column + columnSpan; c++) occupied.add(`${r}:${c}`);
    };

    for (const tile of tiles) {
      const rowSpan = clamp(tile.rowSpan, 1, metrics.rows);
      const columnSpan = clamp(tile.columnSpan, 1, metrics.columns);
      let row = tile.row;
      let column = tile.column;
      if (!fits(row, column, rowSpan, columnSpan)) {
        let found = false;
        for (let candidateRow = 0; candidateRow <= metrics.rows - rowSpan && !found; candidateRow++) {
          for (let candidateColumn = 0; candidateColumn <= metrics.columns - columnSpan; candidateColumn++) {
            if (!fits(candidateRow, candidateColumn, rowSpan, columnSpan)) continue;
            row = candidateRow;
            column = candidateColumn;
            found = true;
            break;
          }
        }
        if (!found) return false;
      }
      occupy(row, column, rowSpan, columnSpan);
    }
    return true;
  }

  function snapIndex(rawIndex, current, maximum) {
    let next = current;
    while (rawIndex > next + snapHysteresis && next < maximum) next++;
    while (rawIndex < next - snapHysteresis && next > 0) next--;
    return clamp(next, 0, maximum);
  }

  function snapDelta(rawDelta, current, minimum, maximum) {
    let next = current;
    while (rawDelta > next + snapHysteresis && next < maximum) next++;
    while (rawDelta < next - snapHysteresis && next > minimum) next--;
    return clamp(next, minimum, maximum);
  }

  function pointerInsideGrid(event, metrics) {
    return event.clientX >= metrics.rect.left && event.clientX <= metrics.rect.right &&
      event.clientY >= metrics.rect.top && event.clientY <= metrics.rect.bottom;
  }

  function placementRect(metrics, placement) {
    return {
      left: metrics.contentLeft + placement.column * metrics.columnStep,
      top: metrics.contentTop + placement.row * metrics.rowStep,
      width: placement.columnSpan * metrics.cellWidth + (placement.columnSpan - 1) * metrics.columnGap,
      height: placement.rowSpan * metrics.cellHeight + (placement.rowSpan - 1) * metrics.rowGap
    };
  }

  function updatePreview(session, metrics, placement, valid, invalidMessage = "No room at this size", replacementMessage = null) {
    if (!session.preview) {
      session.preview = document.createElement("div");
      session.preview.className = "live-grid-drop-preview";
      session.preview.innerHTML = "<span></span>";
      document.body.appendChild(session.preview);
    }
    const rect = placementRect(metrics, placement);
    session.preview.style.left = `${Math.round(rect.left)}px`;
    session.preview.style.top = `${Math.round(rect.top)}px`;
    session.preview.style.width = `${Math.round(rect.width)}px`;
    session.preview.style.height = `${Math.round(rect.height)}px`;
    session.preview.classList.toggle("invalid", !valid);
    session.preview.classList.toggle("replacement", valid && replacementMessage !== null);
    session.preview.querySelector("span").textContent = valid
      ? replacementMessage || `${placement.columnSpan} x ${placement.rowSpan}`
      : invalidMessage;
    session.placement = placement;
    session.valid = valid;
  }

  function createGhost(session, event) {
    const source = session.source;
    const rect = source.getBoundingClientRect();
    const ghost = source.cloneNode(true);
    ghost.removeAttribute("id");
    ghost.className = "live-layout-drag-ghost";
    ghost.style.width = `${Math.min(320, Math.max(190, rect.width))}px`;
    ghost.style.height = `${Math.min(160, Math.max(54, rect.height))}px`;
    document.body.appendChild(ghost);
    session.ghost = ghost;
    moveGhost(session, event);
  }

  function moveGhost(session, event) {
    if (!session.ghost) return;
    const left = clamp(event.clientX + 14, 8, Math.max(8, window.innerWidth - session.ghost.offsetWidth - 8));
    const top = clamp(event.clientY + 14, 8, Math.max(8, window.innerHeight - session.ghost.offsetHeight - 8));
    session.ghost.style.left = `${Math.round(left)}px`;
    session.ghost.style.top = `${Math.round(top)}px`;
  }

  function beginVisibleGesture(state, event) {
    const session = state.session;
    if (!session || session.active) return;
    session.active = true;
    state.root.classList.add("live-gesture-active");
    session.source.classList.add("is-gesture-source");
    if (session.kind !== "resize") createGhost(session, event);
  }

  function moveTileGesture(state, event, metrics) {
    const session = state.session;
    const sourceRect = session.sourceRect;
    const rawColumn = (sourceRect.left + (event.clientX - session.startX) - metrics.contentLeft) / metrics.columnStep;
    const rawRow = (sourceRect.top + (event.clientY - session.startY) - metrics.contentTop) / metrics.rowStep;
    const placement = { ...session.original };
    placement.column = snapIndex(rawColumn, session.targetColumn, Math.max(0, metrics.columns - placement.columnSpan));
    placement.row = snapIndex(rawRow, session.targetRow, Math.max(0, metrics.rows - placement.rowSpan));
    session.targetColumn = placement.column;
    session.targetRow = placement.row;
    const inside = pointerInsideGrid(event, metrics);
    const target = inside ? tileAtPointer(state, event, placement.row, placement.column) : null;
    session.replacementTileId = target?.id || null;
    if (target) {
      const replacementPlacement = { row: target.row, column: target.column, rowSpan: target.rowSpan, columnSpan: target.columnSpan };
      updatePreview(session, metrics, replacementPlacement, true, "", `Replace ${target.name} with ${session.metricName}`);
      return;
    }
    updatePreview(session, metrics, placement, inside && placementCanPack(state, session, placement, metrics), inside ? "No room at this size" : "Move over dashboard");
  }

  function moveMetricGesture(state, event, metrics) {
    const session = state.session;
    const rawColumn = (event.clientX - metrics.contentLeft - metrics.cellWidth / 2) / metrics.columnStep;
    const rawRow = (event.clientY - metrics.contentTop - metrics.cellHeight / 2) / metrics.rowStep;
    const placement = {
      row: snapIndex(rawRow, session.targetRow, metrics.rows - 1),
      column: snapIndex(rawColumn, session.targetColumn, metrics.columns - 1),
      rowSpan: 1,
      columnSpan: 1
    };
    session.targetColumn = placement.column;
    session.targetRow = placement.row;
    const inside = pointerInsideGrid(event, metrics);
    updatePreview(session, metrics, placement, inside && placementCanPack(state, session, placement, metrics), inside ? "No room at this size" : "Move over dashboard");
  }

  function moveResizeGesture(state, event, metrics) {
    const session = state.session;
    const original = session.original;
    const edge = session.edge;
    const rawColumns = (event.clientX - session.startX) / metrics.columnStep;
    const rawRows = (event.clientY - session.startY) / metrics.rowStep;
    let horizontal = session.horizontalDelta;
    let vertical = session.verticalDelta;

    if (edge.includes("e")) horizontal = snapDelta(rawColumns, horizontal, -original.columnSpan + 1, metrics.columns - original.column - original.columnSpan);
    if (edge.includes("w")) horizontal = snapDelta(rawColumns, horizontal, -original.column, original.columnSpan - 1);
    if (edge.includes("s")) vertical = snapDelta(rawRows, vertical, -original.rowSpan + 1, metrics.rows - original.row - original.rowSpan);
    if (edge.includes("n")) vertical = snapDelta(rawRows, vertical, -original.row, original.rowSpan - 1);
    session.horizontalDelta = horizontal;
    session.verticalDelta = vertical;

    const placement = { ...original };
    if (edge.includes("e")) placement.columnSpan = original.columnSpan + horizontal;
    if (edge.includes("w")) { placement.column = original.column + horizontal; placement.columnSpan = original.columnSpan - horizontal; }
    if (edge.includes("s")) placement.rowSpan = original.rowSpan + vertical;
    if (edge.includes("n")) { placement.row = original.row + vertical; placement.rowSpan = original.rowSpan - vertical; }
    updatePreview(session, metrics, placement, placementCanPack(state, session, placement, metrics));
  }

  function autoScroll(state, event) {
    const scrollHost = state.root.closest(".workspace");
    if (!scrollHost || scrollHost.scrollHeight <= scrollHost.clientHeight) return;
    const rect = scrollHost.getBoundingClientRect();
    const threshold = 54;
    const maximumStep = 22;
    let delta = 0;
    if (event.clientY < rect.top + threshold) delta = -maximumStep * (1 - clamp((event.clientY - rect.top) / threshold, 0, 1));
    else if (event.clientY > rect.bottom - threshold) delta = maximumStep * (1 - clamp((rect.bottom - event.clientY) / threshold, 0, 1));
    if (Math.abs(delta) >= 1) scrollHost.scrollTop += delta;
  }

  function onPointerMove(state, event) {
    const session = state.session;
    if (!session || event.pointerId !== session.pointerId) return;
    const distance = Math.hypot(event.clientX - session.startX, event.clientY - session.startY);
    if (!session.active && distance < dragThreshold) return;
    beginVisibleGesture(state, event);
    event.preventDefault();
    autoScroll(state, event);
    moveGhost(session, event);
    const metrics = gridMetrics(state);
    if (!metrics) return;
    if (session.kind === "tile") moveTileGesture(state, event, metrics);
    else if (session.kind === "metric") moveMetricGesture(state, event, metrics);
    else moveResizeGesture(state, event, metrics);
  }

  function captureRects(root) {
    const captured = new Map();
    for (const tile of root.querySelectorAll("[data-live-tile]")) captured.set(tile.dataset.tileId, tile.getBoundingClientRect());
    return captured;
  }

  function animateReflow(state, before) {
    if (state.options.reducedMotion || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    for (const tile of state.root.querySelectorAll("[data-live-tile]")) {
      const prior = before.get(tile.dataset.tileId);
      if (!prior) {
        tile.animate([{ opacity: 0, transform: "scale(.96)" }, { opacity: 1, transform: "none" }], { duration: 180, easing: "cubic-bezier(.2,0,0,1)" });
        continue;
      }
      const next = tile.getBoundingClientRect();
      const deltaX = prior.left - next.left;
      const deltaY = prior.top - next.top;
      const scaleX = next.width > 0 ? prior.width / next.width : 1;
      const scaleY = next.height > 0 ? prior.height / next.height : 1;
      if (Math.abs(deltaX) < 0.5 && Math.abs(deltaY) < 0.5 && Math.abs(scaleX - 1) < 0.01 && Math.abs(scaleY - 1) < 0.01) continue;
      tile.animate([
        { transformOrigin: "top left", transform: `translate(${deltaX}px,${deltaY}px) scale(${scaleX},${scaleY})` },
        { transformOrigin: "top left", transform: "none" }
      ], { duration: 200, easing: "cubic-bezier(.2,0,0,1)" });
    }
  }

  function removeGestureVisuals(state, session) {
    state.root.classList.remove("live-gesture-active");
    session.source.classList.remove("is-gesture-source");
    if (session.ghost) session.ghost.remove();
    if (session.preview) session.preview.remove();
    try { session.capture.releasePointerCapture(session.pointerId); } catch (_) { }
  }

  async function completeGesture(state, event, cancelled) {
    const session = state.session;
    if (!session || event.pointerId !== undefined && event.pointerId !== session.pointerId) return;
    state.session = null;
    if (!cancelled && session.active && session.kind === "metric") {
      const metrics = gridMetrics(state);
      if (metrics && pointerInsideGrid(event, metrics)) {
        const finalColumn = clamp(Math.floor((event.clientX - metrics.contentLeft) / metrics.columnStep), 0, metrics.columns - 1);
        const finalRow = clamp(Math.floor((event.clientY - metrics.contentTop) / metrics.rowStep), 0, metrics.rows - 1);
        const finalTarget = tileAtPointer(state, event, finalRow, finalColumn);
        if (finalTarget) {
          session.replacementTileId = finalTarget.id;
          session.placement = { row: finalTarget.row, column: finalTarget.column, rowSpan: finalTarget.rowSpan, columnSpan: finalTarget.columnSpan };
          session.valid = true;
        }
      }
    }
    if (cancelled || !session.active || !session.valid || !session.placement) {
      removeGestureVisuals(state, session);
      return;
    }

    state.committing = true;
    const before = captureRects(state.root);
    const placement = session.placement;
    if (session.preview) session.preview.classList.add("committing");
    let succeeded = false;
    try {
      succeeded = session.kind === "metric"
        ? session.replacementTileId
          ? await state.dotnet.invokeMethodAsync("ReplaceMetric", session.replacementTileId, session.metricId)
          : await state.dotnet.invokeMethodAsync("DropMetric", session.metricId, placement.row, placement.column)
        : await state.dotnet.invokeMethodAsync("CommitTilePlacement", session.original.id, placement.row, placement.column, placement.rowSpan, placement.columnSpan);
    } catch (_) {
      succeeded = false;
    }
    state.committing = false;
    removeGestureVisuals(state, session);
    requestAnimationFrame(() => requestAnimationFrame(() => {
      if (succeeded) animateReflow(state, before);
      else {
        const grid = state.root.querySelector("[data-live-grid]");
        if (grid && !state.options.reducedMotion && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) grid.animate([{ transform: "translateX(0)" }, { transform: "translateX(-3px)" }, { transform: "translateX(3px)" }, { transform: "translateX(0)" }], { duration: 160 });
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
    const resizeHandle = event.target.closest("[data-live-resize]");
    const tileHandle = event.target.closest("[data-live-drag-tile]");
    const noDragTarget = event.target.closest("[data-live-no-drag]");
    const metricHandle = noDragTarget ? null : event.target.closest("[data-live-drag-metric]");
    if (!resizeHandle && !tileHandle && !metricHandle) return;
    const capture = resizeHandle || tileHandle || metricHandle;
    const tile = capture.closest("[data-live-tile]");
    const kind = resizeHandle ? "resize" : tileHandle ? "tile" : "metric";
    if (kind !== "metric" && !tile) return;
    event.preventDefault();
    event.stopPropagation();
    try { capture.setPointerCapture(event.pointerId); } catch (_) { }
    const original = tile ? tileData(tile) : null;
    state.session = {
      kind,
      source: tile || capture.closest("[data-live-catalog-item]") || capture,
      capture,
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      sourceRect: tile ? tile.getBoundingClientRect() : capture.getBoundingClientRect(),
      original,
      metricId: metricHandle ? metricHandle.dataset.liveDragMetric : null,
      metricName: metricHandle ? metricHandle.dataset.metricName || metricHandle.closest("[data-live-catalog-item]")?.dataset.metricName || "widget" : null,
      edge: resizeHandle ? resizeHandle.dataset.liveResize : null,
      targetColumn: original ? original.column : 0,
      targetRow: original ? original.row : 0,
      horizontalDelta: 0,
      verticalDelta: 0,
      active: false,
      valid: false,
      placement: null,
      ghost: null,
      preview: null,
      replacementTileId: null
    };
  }

  function initialize(root, dotnet, options) {
    if (!root) return;
    const existing = layouts.get(root);
    if (existing) {
      existing.dotnet = dotnet;
      configure(root, options);
      return;
    }
    const state = { root, dotnet, options: {}, session: null, committing: false, fitFrame: 0, resizeObserver: null };
    state.pointerDown = event => onPointerDown(state, event);
    state.pointerMove = event => onPointerMove(state, event);
    state.pointerUp = event => void completeGesture(state, event, false);
    state.pointerCancel = event => void completeGesture(state, event, true);
    state.lostPointerCapture = event => { if (state.session && event.pointerId === state.session.pointerId) void completeGesture(state, event, true); };
    state.windowBlur = () => cancelGesture(state);
    state.windowResize = () => scheduleGridFit(state);
    state.keyDown = event => {
      if (event.key === "Escape" && state.session) {
        event.preventDefault();
        event.stopPropagation();
        cancelGesture(state);
        return;
      }
      const tileHandle = event.target.closest && event.target.closest("[data-live-drag-tile]");
      const isArrow = event.key === "ArrowLeft" || event.key === "ArrowRight" || event.key === "ArrowUp" || event.key === "ArrowDown";
      if (tileHandle && (event.key === "Delete" || isArrow && (event.altKey || event.shiftKey))) event.preventDefault();
    };
    root.addEventListener("pointerdown", state.pointerDown, true);
    root.addEventListener("pointermove", state.pointerMove, true);
    root.addEventListener("pointerup", state.pointerUp, true);
    root.addEventListener("pointercancel", state.pointerCancel, true);
    root.addEventListener("lostpointercapture", state.lostPointerCapture, true);
    root.addEventListener("keydown", state.keyDown, true);
    window.addEventListener("blur", state.windowBlur);
    window.addEventListener("resize", state.windowResize);
    if (typeof ResizeObserver !== "undefined") {
      state.resizeObserver = new ResizeObserver(() => scheduleGridFit(state));
      const viewport = root.querySelector("[data-live-grid-viewport]");
      if (viewport) state.resizeObserver.observe(viewport);
    }
    layouts.set(root, state);
    configure(root, options);
  }

  function dispose(root) {
    const state = layouts.get(root);
    if (!state) return;
    cancelGesture(state);
    root.removeEventListener("pointerdown", state.pointerDown, true);
    root.removeEventListener("pointermove", state.pointerMove, true);
    root.removeEventListener("pointerup", state.pointerUp, true);
    root.removeEventListener("pointercancel", state.pointerCancel, true);
    root.removeEventListener("lostpointercapture", state.lostPointerCapture, true);
    root.removeEventListener("keydown", state.keyDown, true);
    window.removeEventListener("blur", state.windowBlur);
    window.removeEventListener("resize", state.windowResize);
    if (state.fitFrame) cancelAnimationFrame(state.fitFrame);
    if (state.resizeObserver) state.resizeObserver.disconnect();
    layouts.delete(root);
  }

  window.iracingCoachLiveTelemetryLayout = { initialize, configure, dispose };
})();

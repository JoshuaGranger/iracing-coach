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

  function rectSnapshot(rect) {
    return { left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom, width: rect.width, height: rect.height };
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

    // The dedicated live page is a viewport-sized flex/grid composition. Let
    // CSS give the dashboard the exact remaining track after the heading,
    // connection state, toolbar, and collapsed trace disclosure. Measuring and
    // writing a second height here would count only the content above the
    // disclosure and recreate a page scrollbar.
    const livePage = state.root.closest("[data-live-telemetry-page]");
    if (livePage) {
      viewport.style.removeProperty("height");
      viewport.style.removeProperty("min-height");
      grid.style.removeProperty("--live-cell-size");
      grid.style.width = "100%";
      grid.style.height = "100%";
      return;
    }

    // Measure from the viewport's real top edge instead of subtracting a fixed
    // header estimate. This keeps the grid inside narrow, short and disconnected
    // layouts where the toolbar or connection message wraps to another line.
    const scrollHost = state.root.closest(".workspace");
    const hostRect = scrollHost ? scrollHost.getBoundingClientRect() : null;
    const viewportTop = viewport.getBoundingClientRect().top;
    const hostTop = hostRect ? hostRect.top : 0;
    const hostBottom = hostRect ? hostRect.bottom : window.innerHeight;
    const visibleTop = Math.max(0, viewportTop, hostTop);
    const visibleBottom = Math.min(window.innerHeight, hostBottom);
    const availableHeight = Math.max(180, visibleBottom - visibleTop - 14);
    viewport.style.height = `${availableHeight}px`;
    viewport.style.minHeight = `${Math.min(320, availableHeight)}px`;

    // CSS fractional tracks remain the sole grid authority. Every row and
    // column receives an equal share of the measured container on both axes.
    grid.style.removeProperty("--live-cell-size");
    grid.style.width = "100%";
    grid.style.height = "100%";
  }

  function scheduleGridFit(state) {
    if (state.fitFrame) cancelAnimationFrame(state.fitFrame);
    state.fitFrame = requestAnimationFrame(() => {
      state.fitFrame = 0;
      fitGrid(state);
    });
  }

  function gridMetrics(state) {
    const grid = state.root.querySelector("[data-live-grid]");
    if (!grid) return null;
    const rect = rectSnapshot(grid.getBoundingClientRect());
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

  function captureTileGeometry(state) {
    return Array.from(state.root.querySelectorAll("[data-live-tile]"), element => ({
      data: tileData(element),
      rect: rectSnapshot(element.getBoundingClientRect())
    }));
  }

  function tileAtCell(session, row, column, excludedTileId = null) {
    return session.tiles.map(item => item.data)
      .find(tile => tile.id !== excludedTileId && row >= tile.row && row < tile.row + tile.rowSpan && column >= tile.column && column < tile.column + tile.columnSpan) || null;
  }

  function tileAtPointer(session, event, row, column, excludedTileId = null) {
    for (const tile of session.tiles) {
      if (tile.data.id === excludedTileId) continue;
      const rect = tile.rect;
      if (event.clientX >= rect.left && event.clientX <= rect.right && event.clientY >= rect.top && event.clientY <= rect.bottom) return tile.data;
    }
    return tileAtCell(session, row, column, excludedTileId);
  }

  function placementCanPack(state, session, placement, metrics) {
    const occupied = session.occupied;
    occupied.fill(0);
    const fits = (row, column, rowSpan, columnSpan) => {
      if (row < 0 || column < 0 || row + rowSpan > metrics.rows || column + columnSpan > metrics.columns) return false;
      for (let r = row; r < row + rowSpan; r++)
        for (let c = column; c < column + columnSpan; c++)
          if (occupied[r * metrics.columns + c]) return false;
      return true;
    };
    const occupy = (row, column, rowSpan, columnSpan) => {
      for (let r = row; r < row + rowSpan; r++)
        for (let c = column; c < column + columnSpan; c++)
          occupied[r * metrics.columns + c] = 1;
    };

    const place = (initialRow, initialColumn, initialRowSpan, initialColumnSpan) => {
      const rowSpan = clamp(initialRowSpan, 1, metrics.rows);
      const columnSpan = clamp(initialColumnSpan, 1, metrics.columns);
      let row = initialRow;
      let column = initialColumn;
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
      return true;
    };

    if (!place(placement.row, placement.column, placement.rowSpan, placement.columnSpan)) return false;
    for (const tile of session.packTiles) {
      if (session.kind !== "metric" && tile.id === session.original.id) continue;
      if (!place(tile.row, tile.column, tile.rowSpan, tile.columnSpan)) return false;
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
      session.previewLabel = session.preview.firstElementChild;
    }
    const rect = placementRect(metrics, placement);
    session.preview.style.transform = `translate3d(${Math.round(rect.left)}px,${Math.round(rect.top)}px,0)`;
    session.preview.style.width = `${Math.round(rect.width)}px`;
    session.preview.style.height = `${Math.round(rect.height)}px`;
    session.preview.classList.toggle("invalid", !valid);
    session.preview.classList.toggle("replacement", valid && replacementMessage !== null);
    session.previewLabel.textContent = valid
      ? replacementMessage || `${placement.columnSpan} x ${placement.rowSpan}`
      : invalidMessage;
    session.placement = placement;
    session.valid = valid;
  }

  function createGhost(session, event) {
    const source = session.source;
    const rect = session.sourceRect;
    const ghost = source.cloneNode(true);
    ghost.removeAttribute("id");
    ghost.className = "live-layout-drag-ghost";
    ghost.style.width = `${Math.min(320, Math.max(190, rect.width))}px`;
    ghost.style.height = `${Math.min(160, Math.max(54, rect.height))}px`;
    document.body.appendChild(ghost);
    session.ghost = ghost;
    session.ghostWidth = Math.min(320, Math.max(190, rect.width));
    session.ghostHeight = Math.min(160, Math.max(54, rect.height));
    moveGhost(session, event);
  }

  function moveGhost(session, event) {
    if (!session.ghost) return;
    const left = clamp(event.clientX + 14, 8, Math.max(8, window.innerWidth - session.ghostWidth - 8));
    const top = clamp(event.clientY + 14, 8, Math.max(8, window.innerHeight - session.ghostHeight - 8));
    session.ghost.style.transform = `translate3d(${Math.round(left)}px,${Math.round(top)}px,0) rotate(.35deg)`;
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
    const target = inside ? tileAtPointer(session, event, placement.row, placement.column, session.original.id) : null;
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
    const target = inside ? tileAtPointer(session, event, placement.row, placement.column) : null;
    session.replacementTileId = target?.id || null;
    if (target) {
      const replacementPlacement = { row: target.row, column: target.column, rowSpan: target.rowSpan, columnSpan: target.columnSpan };
      updatePreview(session, metrics, replacementPlacement, true, "", `Replace ${target.name} with ${session.metricName}`);
      return;
    }
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

  function shiftGestureGeometry(session, scrollDelta) {
    if (!scrollDelta) return;
    session.metrics.rect.top -= scrollDelta;
    session.metrics.rect.bottom -= scrollDelta;
    session.metrics.contentTop -= scrollDelta;
    session.sourceRect.top -= scrollDelta;
    session.sourceRect.bottom -= scrollDelta;
    for (const tile of session.tiles) {
      tile.rect.top -= scrollDelta;
      tile.rect.bottom -= scrollDelta;
    }
  }

  function autoScroll(state, event) {
    const session = state.session;
    const scrollHost = session.scrollHost;
    if (!scrollHost || session.maximumScroll <= 0) return 0;
    const rect = session.scrollHostRect;
    const threshold = 54;
    const maximumStep = 22;
    let delta = 0;
    if (event.clientY < rect.top + threshold) delta = -maximumStep * (1 - clamp((event.clientY - rect.top) / threshold, 0, 1));
    else if (event.clientY > rect.bottom - threshold) delta = maximumStep * (1 - clamp((rect.bottom - event.clientY) / threshold, 0, 1));
    if (Math.abs(delta) < 1) return 0;
    const before = scrollHost.scrollTop;
    scrollHost.scrollTop = clamp(before + delta, 0, session.maximumScroll);
    return scrollHost.scrollTop - before;
  }

  function pointerSample(event) {
    return { pointerId: event.pointerId, clientX: event.clientX, clientY: event.clientY };
  }

  function processPointerMove(state, event, allowAutoScroll = true) {
    const session = state.session;
    if (!session || event.pointerId !== session.pointerId) return;
    const distance = Math.hypot(event.clientX - session.startX, event.clientY - session.startY);
    if (!session.active && distance < dragThreshold) return;
    beginVisibleGesture(state, event);
    if (allowAutoScroll) shiftGestureGeometry(session, autoScroll(state, event));
    moveGhost(session, event);
    const metrics = session.metrics;
    if (!metrics) return;
    if (session.kind === "tile") moveTileGesture(state, event, metrics);
    else if (session.kind === "metric") moveMetricGesture(state, event, metrics);
    else moveResizeGesture(state, event, metrics);
  }

  function onPointerMove(state, event) {
    const session = state.session;
    if (!session || event.pointerId !== session.pointerId) return;
    const distance = Math.hypot(event.clientX - session.startX, event.clientY - session.startY);
    if (!session.active && distance < dragThreshold) return;
    event.preventDefault();
    state.latestPointer = pointerSample(event);
    if (state.pointerMoveFrame) return;
    state.pointerMoveFrame = requestAnimationFrame(() => {
      state.pointerMoveFrame = 0;
      const latest = state.latestPointer;
      state.latestPointer = null;
      if (latest) processPointerMove(state, latest);
    });
  }

  function captureRects(root) {
    const captured = new Map();
    for (const tile of root.querySelectorAll("[data-live-tile]")) captured.set(tile.dataset.tileId, tile.getBoundingClientRect());
    return captured;
  }

  function motionMilliseconds(root) {
    const raw = getComputedStyle(root).getPropertyValue("--motion-structure").trim();
    const parsed = Number.parseFloat(raw);
    if (!Number.isFinite(parsed)) return 500;
    return raw.endsWith("s") && !raw.endsWith("ms") ? parsed * 1000 : parsed;
  }

  function animateReflow(state, before) {
    const duration = motionMilliseconds(state.root);
    if (duration <= 0 || state.options.reducedMotion || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    for (const tile of state.root.querySelectorAll("[data-live-tile]")) {
      const prior = before.get(tile.dataset.tileId);
      if (!prior) {
        tile.animate([{ opacity: 0, transform: "scale(.96)" }, { opacity: 1, transform: "none" }], { duration, easing: "cubic-bezier(.2,0,0,1)" });
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
      ], { duration, easing: "cubic-bezier(.2,0,0,1)" });
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
    if (state.pointerMoveFrame) {
      cancelAnimationFrame(state.pointerMoveFrame);
      state.pointerMoveFrame = 0;
    }
    state.latestPointer = null;
    if (!cancelled) processPointerMove(state, pointerSample(event), false);
    // Pointer capture can deliver an up event at a newer position than the
    // last display frame. The direct call above commits that exact position so
    // the visible preview and stored placement cannot disagree.
    state.session = null;
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
        : session.kind === "tile" && session.replacementTileId
          ? await state.dotnet.invokeMethodAsync("ReplaceTile", session.original.id, session.replacementTileId)
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
    if (state.pointerMoveFrame) {
      cancelAnimationFrame(state.pointerMoveFrame);
      state.pointerMoveFrame = 0;
    }
    state.latestPointer = null;
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
    const original = tile ? tileData(tile) : null;
    const source = tile || capture.closest("[data-live-catalog-item]") || capture;
    const metrics = gridMetrics(state);
    if (!metrics) return;
    const sourceRect = rectSnapshot(source.getBoundingClientRect());
    const scrollHost = state.root.closest(".workspace");
    const scrollHostRect = scrollHost ? rectSnapshot(scrollHost.getBoundingClientRect()) : null;
    event.preventDefault();
    event.stopPropagation();
    try { capture.setPointerCapture(event.pointerId); } catch (_) { }
    const tiles = captureTileGeometry(state);
    const packTiles = tiles.map(item => item.data).sort((left, right) =>
      left.row - right.row || left.column - right.column || (left.id < right.id ? -1 : left.id > right.id ? 1 : 0));
    state.session = {
      kind,
      source,
      capture,
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      sourceRect,
      metrics,
      tiles,
      packTiles,
      occupied: new Uint8Array(metrics.rows * metrics.columns),
      scrollHost,
      scrollHostRect,
      maximumScroll: scrollHost ? Math.max(0, scrollHost.scrollHeight - scrollHost.clientHeight) : 0,
      original,
      metricId: metricHandle ? metricHandle.dataset.liveDragMetric : null,
      metricName: metricHandle
        ? metricHandle.dataset.metricName || metricHandle.closest("[data-live-catalog-item]")?.dataset.metricName || "widget"
        : original?.name || "widget",
      edge: resizeHandle ? resizeHandle.dataset.liveResize : null,
      targetColumn: original ? original.column : 0,
      targetRow: original ? original.row : 0,
      horizontalDelta: 0,
      verticalDelta: 0,
      active: false,
      valid: false,
      placement: null,
      ghost: null,
      ghostWidth: 0,
      ghostHeight: 0,
      preview: null,
      previewLabel: null,
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
    const state = { root, dotnet, options: {}, session: null, committing: false, fitFrame: 0, pointerMoveFrame: 0, latestPointer: null, resizeObserver: null };
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
    if (state.pointerMoveFrame) cancelAnimationFrame(state.pointerMoveFrame);
    if (state.resizeObserver) state.resizeObserver.disconnect();
    layouts.delete(root);
  }

  window.iracingCoachLiveTelemetryLayout = { initialize, configure, dispose };
})();

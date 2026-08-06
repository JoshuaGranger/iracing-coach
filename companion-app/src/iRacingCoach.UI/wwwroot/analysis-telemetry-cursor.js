(function () {
  "use strict";

  const svgNamespace = "http://www.w3.org/2000/svg";
  const sessions = new WeakMap();
  const clamp = (value, minimum, maximum) => Math.max(minimum, Math.min(maximum, value));
  const finite = (value) => typeof value === "number" && Number.isFinite(value);

  function createSvg(name, attributes) {
    const node = document.createElementNS(svgNamespace, name);
    for (const [key, value] of Object.entries(attributes || {})) node.setAttribute(key, String(value));
    return node;
  }

  function nearestIndex(points, fraction) {
    if (!points || !points.length) return -1;
    let low = 0;
    let high = points.length - 1;
    while (low <= high) {
      const middle = low + ((high - low) >> 1);
      if (points[middle] < fraction) low = middle + 1;
      else high = middle - 1;
    }
    if (low <= 0) return 0;
    if (low >= points.length) return points.length - 1;
    return Math.abs(points[low] - fraction) < Math.abs(points[low - 1] - fraction) ? low : low - 1;
  }

  function signed(value, digits) {
    if (Math.abs(value) < Math.pow(10, -digits) / 2) return (0).toFixed(digits);
    return `${value > 0 ? "+" : ""}${value.toFixed(digits)}`;
  }

  function directionalDegrees(value, compact) {
    if (Math.abs(value) < 0.05) return compact ? "C 0°" : "Center 0°";
    const direction = value > 0 ? (compact ? "L" : "Left") : (compact ? "R" : "Right");
    return `${direction} ${Math.abs(value).toFixed(1)}°`;
  }

  function formatSignal(signal, value, includeLabel) {
    const prefix = includeLabel ? `${signal.shortLabel} ` : "";
    switch (signal.id) {
      case "delta": return `${prefix}${signed(value, 3)} s`;
      case "gear": return `${prefix}${value.toFixed(0)}`;
      case "rpm": return `${prefix}${value.toFixed(0)} rpm`;
      case "speed": return `${prefix}${value.toFixed(1)} mph`;
      case "throttle":
      case "brake": return `${prefix}${(value * 100).toFixed(0)}%`;
      case "tire-wear": return `${prefix}${value.toFixed(3)}% est.`;
      case "steering": return `${prefix}${directionalDegrees(value, includeLabel)}`;
      case "slip": return `${prefix}${signed(value, 1)}°`;
      case "yaw": return `${prefix}${signed(value, 1)}°/s`;
      case "lateral-g":
      case "longitudinal-g": return `${prefix}${signed(value, 2)} g`;
      default: return `${prefix}${value.toFixed(3)}`;
    }
  }

  function traceIndex(trace, fraction) {
    if (trace.cursorFraction === fraction && Number.isInteger(trace.cursorIndex)) return trace.cursorIndex;
    trace.cursorFraction = fraction;
    trace.cursorIndex = nearestIndex(trace.percents, fraction);
    return trace.cursorIndex;
  }

  function rowValue(trace, rowIndex, signalIndex, pointIndex) {
    const values = trace.rowValues?.[rowIndex]?.[signalIndex];
    const value = pointIndex >= 0 ? values?.[pointIndex] : null;
    return finite(value) ? value : null;
  }

  function formattedRowValue(row, trace, rowIndex, fraction) {
    const pointIndex = traceIndex(trace, fraction);
    const includeLabel = row.signals.length > 1;
    const values = [];
    for (let signalIndex = 0; signalIndex < row.signals.length; signalIndex++) {
      const value = rowValue(trace, rowIndex, signalIndex, pointIndex);
      if (value !== null) values.push(formatSignal(row.signals[signalIndex], value, includeLabel));
    }
    return values.length ? values.join(" · ") : "—";
  }

  function resizedTracePath(state, trace, row, rowIndex, signalIndex) {
    const values = trace.rowValues?.[rowIndex]?.[signalIndex];
    if (!values?.length) return "";
    const signal = row.signals[signalIndex];
    const span = Math.max(0.000001, signal.maximum - signal.minimum);
    let started = false;
    const commands = [];
    for (let pointIndex = 0; pointIndex < trace.percents.length; pointIndex++) {
      const value = values[pointIndex];
      if (!finite(value)) {
        started = false;
        continue;
      }
      const x = state.plotLeft + trace.percents[pointIndex] * state.plotWidth;
      const y = row.top + row.plotHeight - 4 - (value - signal.minimum) / span * (row.plotHeight - 8);
      commands.push(`${started ? "L" : "M"}${x.toFixed(3)},${y.toFixed(3)}`);
      started = true;
    }
    return commands.join(" ");
  }

  function resizeChartDom(state, elementWidth, force) {
    if (!finite(elementWidth) || elementWidth <= 0) return;
    if (!force && Math.abs(elementWidth - state.renderWidth) < 0.5) return;
    state.renderWidth = elementWidth;
    state.plotLeft = state.config.plotLeft;
    state.plotWidth = Math.max(40, elementWidth - state.plotLeft - 20);
    state.element.setAttribute("viewBox", `0 0 ${elementWidth.toFixed(3)} ${state.config.chartHeight}`);

    for (const rowNode of state.element.querySelectorAll("[data-analysis-chart-row]"))
      rowNode.setAttribute("width", state.plotWidth.toFixed(3));
    for (const line of state.element.querySelectorAll("[data-analysis-horizontal-grid]"))
      line.setAttribute("x2", (state.plotLeft + state.plotWidth).toFixed(3));
    for (const line of state.element.querySelectorAll("[data-analysis-vertical-grid]")) {
      const tick = Number(line.dataset.analysisVerticalGrid);
      const x = state.plotLeft + clamp(tick, 0, 4) * state.plotWidth / 4;
      line.setAttribute("x1", x.toFixed(3));
      line.setAttribute("x2", x.toFixed(3));
    }
    for (const label of state.element.querySelectorAll("[data-analysis-x-tick]")) {
      const tick = Number(label.dataset.analysisXTick);
      label.setAttribute("x", (state.plotLeft + clamp(tick, 0, 4) * state.plotWidth / 4).toFixed(3));
    }
    for (const path of state.element.querySelectorAll("[data-analysis-trace-path]")) {
      const rowIndex = Number(path.dataset.row);
      const trace = state.config.traces.find(candidate => candidate.lap === Number(path.dataset.lap));
      const row = state.config.rows[rowIndex];
      const signalIndex = row?.signals.findIndex(signal => signal.id === path.dataset.signal) ?? -1;
      path.setAttribute("d", trace && row && signalIndex >= 0 ? resizedTracePath(state, trace, row, rowIndex, signalIndex) : "");
    }
  }

  function buildOverlay(state) {
    const layer = state.element.querySelector("[data-analysis-cursor-layer]");
    if (!layer) return false;
    layer.replaceChildren();
    state.layer = layer;
    state.domRows = [];

    state.sharedLine = createSvg("line", {
      x1: state.plotLeft,
      y1: state.config.chartTop,
      x2: state.plotLeft,
      y2: state.config.chartBottom,
      class: "shared-cursor"
    });
    layer.appendChild(state.sharedLine);

    state.config.rows.forEach((row, rowIndex) => {
      const markers = Array.from({ length: state.config.tooltipCapacity }, () => row.signals.map((_, signalIndex) => {
        const marker = createSvg("circle", {
          cx: state.plotLeft,
          cy: row.top + row.plotHeight / 2,
          r: 3.4,
          fill: "transparent",
          class: `trace-cursor-marker ${signalIndex === 0 ? "primary" : "secondary"}`
        });
        marker.style.display = "none";
        layer.appendChild(marker);
        return marker;
      }));

      const tooltip = createSvg("g", { class: "trace-cursor-tooltip" });
      const background = createSvg("rect", { x: 0, y: row.top + 4, width: 102, height: 14, rx: 4 });
      tooltip.appendChild(background);
      const slots = [];
      for (let slotIndex = 0; slotIndex < state.config.tooltipCapacity; slotIndex++) {
        const lap = createSvg("text", { x: 0, y: 0, class: "trace-tooltip-lap" });
        const swatch = createSvg("rect", { x: 0, y: 0, width: 10, height: 10, rx: 1 });
        const value = createSvg("text", { x: 0, y: 0, class: "trace-tooltip-value" });
        tooltip.append(lap, swatch, value);
        slots.push({ lap, swatch, value });
      }
      layer.appendChild(tooltip);
      state.domRows.push({ markers, tooltip, background, slots });
    });
    return true;
  }

  function updateDomReferences(state, trackElement) {
    state.trackElement = trackElement;
    const panel = trackElement?.closest(".track-panel");
    state.trackPoints = trackElement ? Array.from(trackElement.querySelectorAll("[data-analysis-track-cursor-point]")) : [];
    state.trackLine = trackElement?.querySelector("[data-analysis-track-cursor-line]") || null;
    state.trackPercent = panel?.querySelector("[data-analysis-track-percent]") || null;
    state.trackSummary = panel?.querySelector("[data-analysis-track-summary]") || null;
  }

  function mapPointAt(points, fraction) {
    if (!points?.length) return null;
    const target = ((fraction % 1) + 1) % 1;
    for (let index = 0; index < points.length; index++) {
      const first = points[index];
      const second = points[(index + 1) % points.length];
      const start = first.percent;
      const end = second.percent <= start ? second.percent + 1 : second.percent;
      const adjustedTarget = target < start ? target + 1 : target;
      if (adjustedTarget > end + 0.000001) continue;
      const amount = Math.abs(end - start) < 0.000001 ? 0 : (adjustedTarget - start) / (end - start);
      return { x: first.x + (second.x - first.x) * amount, y: first.y + (second.y - first.y) * amount };
    }
    return points[0];
  }

  function averageAt(state, property, fraction) {
    const values = [];
    for (const trace of state.config.traces) {
      const pointIndex = traceIndex(trace, fraction);
      const value = pointIndex >= 0 ? trace.summary?.[property]?.[pointIndex] : null;
      if (finite(value)) values.push(value);
    }
    return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null;
  }

  function updateTrack(state, fraction) {
    const point = mapPointAt(state.config.trackPoints, fraction);
    if (point) {
      for (const cursor of state.trackPoints) {
        cursor.setAttribute("cx", point.x.toFixed(3));
        cursor.setAttribute("cy", point.y.toFixed(3));
      }
    } else if (state.trackLine) {
      const x = 28 + fraction * 364;
      state.trackLine.setAttribute("x1", x.toFixed(3));
      state.trackLine.setAttribute("x2", x.toFixed(3));
    }
    if (state.trackPercent) state.trackPercent.textContent = `${(fraction * 100).toFixed(1)}%`;
    if (!state.trackSummary) return;
    const speed = averageAt(state, "speed", fraction);
    const throttle = averageAt(state, "throttle", fraction);
    const brake = averageAt(state, "brake", fraction);
    const delta = averageAt(state, "delta", fraction);
    if (speed === null && throttle === null && brake === null) {
      state.trackSummary.textContent = "No sample";
      return;
    }
    const parts = [
      `${(speed ?? 0).toFixed(0)} mph`,
      `${((throttle ?? 0) * 100).toFixed(0)}% throttle`,
      `${((brake ?? 0) * 100).toFixed(0)}% brake`
    ];
    if (delta !== null) parts.push(`${signed(delta, 3)} s`);
    state.trackSummary.textContent = parts.join(" · ");
  }

  function renderCursor(state) {
    state.frame = 0;
    if (!state.inside || !state.layer) return;
    const fraction = state.fraction;
    const cursorX = state.plotLeft + fraction * state.plotWidth;
    state.layer.style.display = "";
    state.sharedLine.setAttribute("x1", cursorX.toFixed(3));
    state.sharedLine.setAttribute("x2", cursorX.toFixed(3));

    const maximumOffset = Math.max(0, state.config.traces.length - state.config.tooltipCapacity);
    state.lapOffset = clamp(state.lapOffset, 0, maximumOffset);
    const visibleTraces = state.config.traces.slice(state.lapOffset, state.lapOffset + state.config.tooltipCapacity);

    state.config.rows.forEach((row, rowIndex) => {
      const dom = state.domRows[rowIndex];
      dom.markers.forEach((slotMarkers, slotIndex) => {
        const trace = visibleTraces[slotIndex];
        const pointIndex = trace ? traceIndex(trace, fraction) : -1;
        row.signals.forEach((signal, signalIndex) => {
          const marker = slotMarkers[signalIndex];
          const value = trace ? rowValue(trace, rowIndex, signalIndex, pointIndex) : null;
          if (!trace || value === null) {
            marker.style.display = "none";
            return;
          }
          const span = Math.max(0.000001, signal.maximum - signal.minimum);
          const y = row.top + row.plotHeight - 4 - (value - signal.minimum) / span * (row.plotHeight - 8);
          marker.setAttribute("cx", cursorX.toFixed(3));
          marker.setAttribute("cy", y.toFixed(3));
          marker.setAttribute("fill", trace.color);
          marker.style.display = "";
        });
      });

      const formatted = visibleTraces.map((trace) => formattedRowValue(row, trace, rowIndex, fraction));
      const widest = formatted.reduce((width, value) => Math.max(width, value.length), 1);
      dom.tooltip.style.display = visibleTraces.length ? "" : "none";
      let widestRenderedValue = 0;
      dom.slots.forEach((slot, slotIndex) => {
        const trace = visibleTraces[slotIndex];
        const display = trace ? "" : "none";
        slot.lap.style.display = display;
        slot.swatch.style.display = display;
        slot.value.style.display = display;
        if (!trace) return;
        const baseline = row.top + 22 + slotIndex * state.config.tooltipRowHeight;
        slot.lap.setAttribute("y", baseline.toFixed(3));
        slot.lap.textContent = String(trace.lap);
        slot.swatch.setAttribute("y", (baseline - 9).toFixed(3));
        slot.swatch.setAttribute("fill", trace.color);
        slot.value.setAttribute("y", baseline.toFixed(3));
        slot.value.textContent = formatted[slotIndex];
        try {
          const measured = slot.value.getComputedTextLength();
          if (Number.isFinite(measured)) widestRenderedValue = Math.max(widestRenderedValue, measured);
        } catch {
          // SVG measurement can be unavailable during an initial/test layout;
          // the configured character estimate remains a safe fallback.
        }
      });

      const estimatedValueWidth = widest * state.config.tooltipCharacterWidth;
      const valueContentWidth = widestRenderedValue > 0 ? widestRenderedValue : estimatedValueWidth;
      const desiredTooltipWidth = Math.ceil(55 + valueContentWidth);
      const plotInset = 4;
      const plotStart = state.plotLeft + plotInset;
      const plotEnd = state.plotLeft + state.plotWidth - plotInset;
      const availableTooltipWidth = Math.max(40, plotEnd - plotStart);
      const tooltipWidth = Math.min(Math.max(102, desiredTooltipWidth), availableTooltipWidth);
      const leftCandidate = cursorX - tooltipWidth - 12;
      const rightCandidate = cursorX + 12;
      let tooltipX;
      if (leftCandidate >= plotStart) tooltipX = leftCandidate;
      else if (rightCandidate + tooltipWidth <= plotEnd) tooltipX = rightCandidate;
      else tooltipX = clamp(leftCandidate, plotStart, Math.max(plotStart, plotEnd - tooltipWidth));

      dom.background.setAttribute("x", tooltipX.toFixed(3));
      dom.background.setAttribute("width", tooltipWidth.toFixed(3));
      dom.background.setAttribute("height", String(14 + visibleTraces.length * state.config.tooltipRowHeight));
      dom.slots.forEach((slot, slotIndex) => {
        if (!visibleTraces[slotIndex]) return;
        slot.lap.setAttribute("x", (tooltipX + 8).toFixed(3));
        slot.swatch.setAttribute("x", (tooltipX + 30).toFixed(3));
        slot.value.setAttribute("x", (tooltipX + 47).toFixed(3));
      });
    });
    updateTrack(state, fraction);
  }

  function schedule(state) {
    if (!state.frame) state.frame = requestAnimationFrame(() => renderCursor(state));
  }

  function updateFractionFromPointer(state) {
    const rect = state.rect;
    const viewBox = state.element.viewBox?.baseVal;
    if (!rect || rect.width < 1 || !viewBox || viewBox.width < 1) return;
    const svgX = viewBox.x + (state.clientX - rect.left) / rect.width * viewBox.width;
    state.fraction = clamp((svgX - state.plotLeft) / Math.max(1, state.plotWidth), 0, 1);
  }

  function initialize(element, trackElement, config) {
    if (!element || !config) return;
    const existing = sessions.get(element);
    if (existing) {
      existing.config = config;
      existing.plotLeft = config.plotLeft;
      existing.lapOffset = clamp(existing.lapOffset, 0, Math.max(0, config.traces.length - config.tooltipCapacity));
      updateDomReferences(existing, trackElement);
      resizeChartDom(existing, element.getBoundingClientRect().width, true);
      buildOverlay(existing);
      if (existing.inside) schedule(existing);
      else updateTrack(existing, existing.fraction);
      return;
    }

    const state = {
      element,
      trackElement,
      config,
      frame: 0,
      inside: false,
      clientX: 0,
      fraction: clamp(config.initialFraction ?? 0.25, 0, 1),
      lapOffset: 0,
      rect: element.getBoundingClientRect(),
      renderWidth: config.elementWidth,
      plotLeft: config.plotLeft,
      plotWidth: config.plotWidth
    };
    updateDomReferences(state, trackElement);
    resizeChartDom(state, state.rect.width, true);
    if (!buildOverlay(state)) return;

    state.enter = (event) => {
      state.inside = true;
      state.rect = element.getBoundingClientRect();
      state.clientX = event.clientX;
      updateFractionFromPointer(state);
      schedule(state);
    };
    state.move = (event) => {
      state.clientX = event.clientX;
      updateFractionFromPointer(state);
      schedule(state);
    };
    state.leave = () => {
      state.inside = false;
      state.lapOffset = 0;
      if (state.frame) cancelAnimationFrame(state.frame);
      state.frame = 0;
      if (state.layer) state.layer.style.display = "none";
    };
    state.wheel = (event) => {
      if (Math.abs(event.deltaY) < 0.01) return;
      const maximumOffset = Math.max(0, state.config.traces.length - state.config.tooltipCapacity);
      if (maximumOffset === 0) return;
      event.preventDefault();
      state.lapOffset = clamp(state.lapOffset + (event.deltaY > 0 ? 1 : -1), 0, maximumOffset);
      schedule(state);
    };
    state.resizeObserver = typeof ResizeObserver === "function" ? new ResizeObserver(() => {
      state.rect = element.getBoundingClientRect();
      resizeChartDom(state, state.rect.width, false);
      if (state.inside) {
        updateFractionFromPointer(state);
        schedule(state);
      }
    }) : null;
    state.scrolled = () => {
      state.rect = element.getBoundingClientRect();
      if (state.inside) {
        updateFractionFromPointer(state);
        schedule(state);
      }
    };

    element.addEventListener("pointerenter", state.enter);
    element.addEventListener("pointermove", state.move);
    element.addEventListener("pointerleave", state.leave);
    element.addEventListener("wheel", state.wheel, { passive: false });
    window.addEventListener("scroll", state.scrolled, true);
    state.resizeObserver?.observe(element);
    sessions.set(element, state);
    updateTrack(state, state.fraction);
  }

  function sync(element, trackElement) {
    const state = sessions.get(element);
    if (!state) return;
    updateDomReferences(state, trackElement);
    state.rect = element.getBoundingClientRect();
    resizeChartDom(state, state.rect.width, true);
    if (!state.element.querySelector("[data-analysis-cursor-layer] > *")) buildOverlay(state);
    if (state.inside) schedule(state);
    else updateTrack(state, state.fraction);
  }

  function dispose(element) {
    const state = sessions.get(element);
    if (!state) return;
    if (state.frame) cancelAnimationFrame(state.frame);
    state.resizeObserver?.disconnect();
    element.removeEventListener("pointerenter", state.enter);
    element.removeEventListener("pointermove", state.move);
    element.removeEventListener("pointerleave", state.leave);
    element.removeEventListener("wheel", state.wheel);
    window.removeEventListener("scroll", state.scrolled, true);
    sessions.delete(element);
  }

  window.iracingCoachAnalysisCursor = { initialize, sync, dispose };
})();

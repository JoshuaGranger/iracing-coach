(function () {
  "use strict";

  const svgNamespace = "http://www.w3.org/2000/svg";
  const sessions = new WeakMap();
  const clamp = (value, minimum, maximum) => Math.max(minimum, Math.min(maximum, value));
  const finite = (value) => typeof value === "number" && Number.isFinite(value);

  function setAttributeIfChanged(node, name, value) {
    const rendered = String(value);
    if (node.getAttribute(name) !== rendered) node.setAttribute(name, rendered);
  }

  function setTextIfChanged(node, value) {
    const rendered = String(value);
    if (node.textContent !== rendered) node.textContent = rendered;
  }

  function setVisible(node, visible) {
    const display = visible ? "" : "none";
    if (node.style.display !== display) node.style.display = display;
  }

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
      default: {
        const unit = signal.unit || "";
        if (unit === "on / off") return `${prefix}${value >= 0.5 ? "On" : "Off"}`;
        if (unit === "state" || unit === "position") return `${prefix}${value.toFixed(0)}`;
        const digits = unit === "%" || unit === "mph" || unit === "deg F" || unit === "psi" ? 1 : unit === "g" ? 2 : 3;
        const renderedUnit = unit === "deg F" ? "°F" : unit;
        return `${prefix}${value.toFixed(digits)}${renderedUnit ? ` ${renderedUnit}` : ""}`;
      }
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

  function resizeChartDom(state, elementWidth, force) {
    if (!finite(elementWidth) || elementWidth <= 0) return;
    if (!force && Math.abs(elementWidth - state.renderWidth) < 0.5) return;
    state.renderWidth = elementWidth;
    state.plotLeft = state.config.plotLeft;
    state.plotWidth = Math.max(40, elementWidth - state.plotLeft - 20);
    setAttributeIfChanged(state.element, "viewBox", `0 0 ${elementWidth.toFixed(3)} ${state.config.chartHeight}`);

    for (const rowNode of state.element.querySelectorAll("[data-analysis-chart-row]"))
      setAttributeIfChanged(rowNode, "width", state.plotWidth.toFixed(3));
    for (const line of state.element.querySelectorAll("[data-analysis-horizontal-grid]"))
      setAttributeIfChanged(line, "x2", (state.plotLeft + state.plotWidth).toFixed(3));
    for (const line of state.element.querySelectorAll("[data-analysis-vertical-grid]")) {
      const tick = Number(line.dataset.analysisVerticalGrid);
      const x = state.plotLeft + clamp(tick, 0, 4) * state.plotWidth / 4;
      setAttributeIfChanged(line, "x1", x.toFixed(3));
      setAttributeIfChanged(line, "x2", x.toFixed(3));
    }
    for (const label of state.element.querySelectorAll("[data-analysis-x-tick]")) {
      const tick = Number(label.dataset.analysisXTick);
      setAttributeIfChanged(label, "x", (state.plotLeft + clamp(tick, 0, 4) * state.plotWidth / 4).toFixed(3));
    }

    // Every selected lap remains in the compound trace paths, but the DOM has
    // one bounded render layer. During structural motion only that compositor
    // layer scales; no per-lap path is mutated in ResizeObserver callbacks.
    if (force || !finite(state.pathBasePlotWidth) || state.pathBasePlotWidth <= 0)
      state.pathBasePlotWidth = Math.max(1, state.config.plotWidth || state.plotWidth);
    const pathScale = state.plotWidth / state.pathBasePlotWidth;
    const pathTransform = Math.abs(pathScale - 1) < 0.000001
      ? ""
      : `translate(${state.plotLeft.toFixed(3)} 0) scale(${pathScale.toFixed(6)} 1) translate(${(-state.plotLeft).toFixed(3)} 0)`;
    for (const layer of state.element.querySelectorAll("[data-analysis-trace-render-layer]"))
      setAttributeIfChanged(layer, "transform", pathTransform);
  }

  function buildOverlay(state) {
    const layer = state.element.querySelector("[data-analysis-cursor-layer]");
    const tooltipLayer = state.element.parentElement?.querySelector("[data-analysis-cursor-tooltips]");
    if (!layer || !tooltipLayer) return false;
    layer.replaceChildren();
    tooltipLayer.replaceChildren();
    state.tooltipLayer = tooltipLayer;
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

      const tooltip = document.createElement("div");
      tooltip.className = "analysis-cursor-tooltip-card";
      const slots = [];
      for (let slotIndex = 0; slotIndex < state.config.tooltipCapacity; slotIndex++) {
        const slot = document.createElement("div");
        slot.className = "analysis-cursor-tooltip-slot";
        const lap = document.createElement("span");
        lap.className = "trace-tooltip-lap";
        const swatch = document.createElement("i");
        swatch.className = "trace-tooltip-swatch";
        const value = document.createElement("span");
        value.className = "trace-tooltip-value";
        slot.append(lap, swatch, value);
        tooltip.appendChild(slot);
        slots.push({ slot, lap, swatch, value });
      }
      tooltipLayer.appendChild(tooltip);
      state.domRows.push({ markers, tooltip, slots });
    });
    setVisible(tooltipLayer, false);
    return true;
  }

  function updateDomReferences(state, trackElement) {
    if (state.trackElement !== trackElement) unbindTrackElement(state);
    state.trackElement = trackElement;
    const panel = trackElement?.closest(".track-panel");
    state.trackPoints = trackElement ? Array.from(trackElement.querySelectorAll("[data-analysis-track-cursor-point]")) : [];
    state.trackLine = trackElement?.querySelector("[data-analysis-track-cursor-line]") || null;
    state.trackPercent = panel?.querySelector("[data-analysis-track-percent]") || null;
    state.trackSummary = panel?.querySelector("[data-analysis-track-summary]") || null;
    bindTrackElement(state);
  }

  function bindTrackElement(state) {
    if (!state.trackElement || !state.trackEnter || state.boundTrackElement === state.trackElement) return;
    unbindTrackElement(state);
    state.trackElement.addEventListener("pointerenter", state.trackEnter);
    state.trackElement.addEventListener("pointermove", state.trackMove);
    state.trackElement.addEventListener("pointerleave", state.trackLeave);
    state.boundTrackElement = state.trackElement;
  }

  function unbindTrackElement(state) {
    if (!state.boundTrackElement || !state.trackEnter) return;
    state.boundTrackElement.removeEventListener("pointerenter", state.trackEnter);
    state.boundTrackElement.removeEventListener("pointermove", state.trackMove);
    state.boundTrackElement.removeEventListener("pointerleave", state.trackLeave);
    state.boundTrackElement = null;
  }

  function cursorActive(state) {
    return state.chartInside || state.trackInside;
  }

  function hideCursorIfInactive(state) {
    if (cursorActive(state)) return;
    state.lapOffset = 0;
    state.inputSource = null;
    if (state.frame) cancelAnimationFrame(state.frame);
    state.frame = 0;
    if (state.layer) setVisible(state.layer, false);
    if (state.tooltipLayer) setVisible(state.tooltipLayer, false);
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

  function svgPointFromClient(element, clientX, clientY) {
    if (!element) return null;
    try {
      const matrix = element.getScreenCTM?.();
      if (matrix && typeof element.createSVGPoint === "function") {
        const point = element.createSVGPoint();
        point.x = clientX;
        point.y = clientY;
        const transformed = point.matrixTransform(matrix.inverse());
        if (finite(transformed.x) && finite(transformed.y)) return transformed;
      }
    } catch {
      // Fall through to the viewBox mapping used by headless/test hosts.
    }
    const rect = element.getBoundingClientRect?.();
    const viewBox = element.viewBox?.baseVal;
    if (!rect || rect.width < 1 || rect.height < 1 || !viewBox || viewBox.width < 1 || viewBox.height < 1) return null;
    return {
      x: viewBox.x + (clientX - rect.left) / rect.width * viewBox.width,
      y: viewBox.y + (clientY - rect.top) / rect.height * viewBox.height
    };
  }

  function segmentFraction(start, end, amount) {
    const adjustedEnd = end <= start ? end + 1 : end;
    const value = start + (adjustedEnd - start) * amount;
    return ((value % 1) + 1) % 1;
  }

  function projectedTrackFraction(points, pointerX, pointerY, fallback) {
    if (!points?.length) return fallback;
    let bestDistance = Number.POSITIVE_INFINITY;
    let bestFraction = fallback;
    for (let index = 0; index < points.length; index++) {
      const first = points[index];
      const second = points[(index + 1) % points.length];
      const dx = second.x - first.x;
      const dy = second.y - first.y;
      const lengthSquared = dx * dx + dy * dy;
      const amount = lengthSquared <= 0.000001
        ? 0
        : clamp(((pointerX - first.x) * dx + (pointerY - first.y) * dy) / lengthSquared, 0, 1);
      const projectedX = first.x + dx * amount;
      const projectedY = first.y + dy * amount;
      const distance = (pointerX - projectedX) ** 2 + (pointerY - projectedY) ** 2;
      if (distance >= bestDistance) continue;
      bestDistance = distance;
      bestFraction = segmentFraction(first.percent, second.percent, amount);
    }
    return bestFraction;
  }

  function averageAt(state, property, fraction) {
    const aggregate = state.config.aggregate;
    if (aggregate?.percents?.length) {
      const pointIndex = nearestIndex(aggregate.percents, fraction);
      const value = pointIndex >= 0 ? aggregate.summary?.[property]?.[pointIndex] : null;
      return finite(value) ? value : null;
    }
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
        setAttributeIfChanged(cursor, "cx", point.x.toFixed(3));
        setAttributeIfChanged(cursor, "cy", point.y.toFixed(3));
      }
    } else if (state.config.distanceStrip && state.trackPoints.length) {
      const x = 28 + fraction * 364;
      for (const cursor of state.trackPoints) {
        setAttributeIfChanged(cursor, "cx", x.toFixed(3));
        setAttributeIfChanged(cursor, "cy", "140");
      }
    } else if (state.trackLine) {
      const x = 28 + fraction * 364;
      setAttributeIfChanged(state.trackLine, "x1", x.toFixed(3));
      setAttributeIfChanged(state.trackLine, "x2", x.toFixed(3));
    }
    if (state.trackPercent) setTextIfChanged(state.trackPercent, `${(fraction * 100).toFixed(1)}%`);
    if (!state.trackSummary) return;
    const speed = averageAt(state, "speed", fraction);
    const throttle = averageAt(state, "throttle", fraction);
    const brake = averageAt(state, "brake", fraction);
    const delta = averageAt(state, "delta", fraction);
    if (speed === null && throttle === null && brake === null && delta === null) {
      setTextIfChanged(state.trackSummary, "No sample");
      return;
    }
    const parts = [];
    if (speed !== null) parts.push(`${speed.toFixed(0)} mph`);
    if (throttle !== null) parts.push(`${(throttle * 100).toFixed(0)}% throttle`);
    if (brake !== null) parts.push(`${(brake * 100).toFixed(0)}% brake`);
    if (delta !== null) parts.push(`${signed(delta, 3)} s`);
    setTextIfChanged(state.trackSummary, parts.join(" · "));
  }

  function renderCursor(state, timestamp) {
    state.frame = 0;
    if (finite(timestamp)) {
      const metrics = state.metrics;
      if (finite(metrics.lastFrame)) {
        const gap = timestamp - metrics.lastFrame;
        metrics.maximumFrameGap = Math.max(metrics.maximumFrameGap, gap);
        if (gap > 25) metrics.framesOver25ms++;
      }
      metrics.lastFrame = timestamp;
      metrics.renderedFrames++;
    }
    if (state.resizePending) {
      state.resizePending = false;
      state.rect = state.element.getBoundingClientRect();
      state.frameRect = state.tooltipLayer?.parentElement?.getBoundingClientRect?.() || state.rect;
      resizeChartDom(state, state.rect.width, false);
    }
    if (!cursorActive(state)) return;
    if (state.inputSource === "track" && state.trackInside) updateFractionFromTrackPointer(state);
    else if (state.chartInside) updateFractionFromPointer(state);
    const fraction = state.fraction;
    if (!state.chartInside || !state.layer) {
      if (state.layer) setVisible(state.layer, false);
      if (state.tooltipLayer) setVisible(state.tooltipLayer, false);
      updateTrack(state, fraction);
      return;
    }
    const cursorX = state.plotLeft + fraction * state.plotWidth;
    const frameRect = state.frameRect || state.rect;
    const chartRect = state.rect || state.element.getBoundingClientRect();
    const viewBox = state.element.viewBox?.baseVal;
    const logicalWidth = viewBox?.width || state.renderWidth || chartRect.width;
    const logicalHeight = viewBox?.height || state.config.chartHeight;
    const scaleX = chartRect.width / Math.max(1, logicalWidth);
    const scaleY = chartRect.height / Math.max(1, logicalHeight);
    const chartOffsetX = chartRect.left - frameRect.left;
    const chartOffsetY = chartRect.top - frameRect.top;
    const cursorPixelX = chartOffsetX + (cursorX - (viewBox?.x || 0)) * scaleX;
    setVisible(state.layer, true);
    if (state.tooltipLayer) setVisible(state.tooltipLayer, true);
    setAttributeIfChanged(state.sharedLine, "x1", cursorX.toFixed(3));
    setAttributeIfChanged(state.sharedLine, "x2", cursorX.toFixed(3));

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
            setVisible(marker, false);
            return;
          }
          const span = Math.max(0.000001, signal.maximum - signal.minimum);
          const y = row.top + row.plotHeight - 4 - (value - signal.minimum) / span * (row.plotHeight - 8);
          setAttributeIfChanged(marker, "cx", cursorX.toFixed(3));
          setAttributeIfChanged(marker, "cy", y.toFixed(3));
          setAttributeIfChanged(marker, "fill", trace.color);
          setVisible(marker, true);
        });
      });

      const formatted = visibleTraces.map((trace) => formattedRowValue(row, trace, rowIndex, fraction));
      const widest = formatted.reduce((width, value) => Math.max(width, value.length), 1);
      setVisible(dom.tooltip, visibleTraces.length > 0);
      dom.slots.forEach((slot, slotIndex) => {
        const trace = visibleTraces[slotIndex];
        setVisible(slot.slot, Boolean(trace));
        if (!trace) return;
        setTextIfChanged(slot.lap, trace.lap);
        if (slot.swatch.style.backgroundColor !== trace.color) slot.swatch.style.backgroundColor = trace.color;
        setTextIfChanged(slot.value, formatted[slotIndex]);
      });

      // Tooltip typography lives in an HTML overlay measured in CSS pixels.
      // It never inherits the chart SVG's non-uniform responsive transform.
      const desiredTooltipWidth = Math.ceil(55 + widest * state.config.tooltipCharacterWidth);
      const plotInset = 4;
      const plotStart = chartOffsetX + (state.plotLeft + plotInset - (viewBox?.x || 0)) * scaleX;
      const plotEnd = chartOffsetX + (state.plotLeft + state.plotWidth - plotInset - (viewBox?.x || 0)) * scaleX;
      const availableTooltipWidth = Math.max(40, plotEnd - plotStart);
      const tooltipWidth = Math.min(Math.max(102, desiredTooltipWidth), availableTooltipWidth);
      const leftCandidate = cursorPixelX - tooltipWidth - 12;
      const rightCandidate = cursorPixelX + 12;
      let tooltipX;
      if (leftCandidate >= plotStart) tooltipX = leftCandidate;
      else if (rightCandidate + tooltipWidth <= plotEnd) tooltipX = rightCandidate;
      else tooltipX = clamp(leftCandidate, plotStart, Math.max(plotStart, plotEnd - tooltipWidth));
      dom.tooltip.style.left = `${tooltipX.toFixed(1)}px`;
      dom.tooltip.style.top = `${(chartOffsetY + (row.top + 4) * scaleY).toFixed(1)}px`;
      dom.tooltip.style.width = `${tooltipWidth.toFixed(1)}px`;
    });
    updateTrack(state, fraction);
  }

  function schedule(state) {
    if (!state.frame) state.frame = requestAnimationFrame(timestamp => renderCursor(state, timestamp));
  }

  function updateFractionFromPointer(state) {
    const rect = state.rect;
    const viewBox = state.element.viewBox?.baseVal;
    if (!rect || rect.width < 1 || !viewBox || viewBox.width < 1) return;
    const svgX = viewBox.x + (state.clientX - rect.left) / rect.width * viewBox.width;
    state.fraction = clamp((svgX - state.plotLeft) / Math.max(1, state.plotWidth), 0, 1);
  }

  function updateFractionFromTrackPointer(state) {
    const point = svgPointFromClient(state.trackElement, state.trackClientX, state.trackClientY);
    if (!point) return;
    if (state.config.trackPoints?.length) {
      state.fraction = projectedTrackFraction(state.config.trackPoints, point.x, point.y, state.fraction);
      return;
    }
    state.fraction = clamp((point.x - 28) / 364, 0, 1);
  }

  function initialize(element, trackElement, config) {
    if (!element || !config) return;
    const existing = sessions.get(element);
    if (existing) {
      existing.config = config;
      existing.plotLeft = config.plotLeft;
      existing.lapOffset = clamp(existing.lapOffset, 0, Math.max(0, config.traces.length - config.tooltipCapacity));
      updateDomReferences(existing, trackElement);
      existing.rect = element.getBoundingClientRect();
      existing.frameRect = existing.tooltipLayer?.parentElement?.getBoundingClientRect?.() || existing.rect;
      resizeChartDom(existing, existing.rect.width, true);
      buildOverlay(existing);
      if (cursorActive(existing)) schedule(existing);
      else updateTrack(existing, existing.fraction);
      return;
    }

    const state = {
      element,
      trackElement,
      config,
      frame: 0,
      resizePending: false,
      pathBasePlotWidth: null,
      chartInside: false,
      trackInside: false,
      inputSource: null,
      clientX: 0,
      trackClientX: 0,
      trackClientY: 0,
      boundTrackElement: null,
      fraction: clamp(config.initialFraction ?? 0.25, 0, 1),
      lapOffset: 0,
      rect: element.getBoundingClientRect(),
      frameRect: element.parentElement?.getBoundingClientRect?.() || element.getBoundingClientRect(),
      renderWidth: config.elementWidth,
      plotLeft: config.plotLeft,
      plotWidth: config.plotWidth,
      metrics: {
        renderedFrames: 0,
        framesOver25ms: 0,
        maximumFrameGap: 0,
        lastFrame: null,
        resizeCallbacks: 0
      }
    };
    updateDomReferences(state, trackElement);
    resizeChartDom(state, state.rect.width, true);
    if (!buildOverlay(state)) return;

    state.enter = (event) => {
      state.chartInside = true;
      state.inputSource = "chart";
      state.rect = element.getBoundingClientRect();
      state.frameRect = state.tooltipLayer?.parentElement?.getBoundingClientRect?.() || state.rect;
      state.clientX = event.clientX;
      schedule(state);
    };
    state.move = (event) => {
      state.chartInside = true;
      state.inputSource = "chart";
      state.clientX = event.clientX;
      schedule(state);
    };
    state.leave = () => {
      state.chartInside = false;
      hideCursorIfInactive(state);
    };
    state.trackEnter = (event) => {
      state.trackInside = true;
      state.inputSource = "track";
      state.trackClientX = event.clientX;
      state.trackClientY = event.clientY;
      schedule(state);
    };
    state.trackMove = (event) => {
      state.trackInside = true;
      state.inputSource = "track";
      state.trackClientX = event.clientX;
      state.trackClientY = event.clientY;
      schedule(state);
    };
    state.trackLeave = () => {
      state.trackInside = false;
      hideCursorIfInactive(state);
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
      state.metrics.resizeCallbacks++;
      state.resizePending = true;
      schedule(state);
    }) : null;
    state.scrolled = () => {
      state.rect = element.getBoundingClientRect();
      state.frameRect = state.tooltipLayer?.parentElement?.getBoundingClientRect?.() || state.rect;
      if (state.chartInside) schedule(state);
    };

    element.addEventListener("pointerenter", state.enter);
    element.addEventListener("pointermove", state.move);
    element.addEventListener("pointerleave", state.leave);
    element.addEventListener("wheel", state.wheel, { passive: false });
    window.addEventListener("scroll", state.scrolled, true);
    bindTrackElement(state);
    state.resizeObserver?.observe(element);
    sessions.set(element, state);
    updateTrack(state, state.fraction);
  }

  function sync(element, trackElement) {
    const state = sessions.get(element);
    if (!state) return;
    updateDomReferences(state, trackElement);
    state.rect = element.getBoundingClientRect();
    state.frameRect = state.tooltipLayer?.parentElement?.getBoundingClientRect?.() || state.rect;
    state.resizePending = true;
    if (!state.element.querySelector("[data-analysis-cursor-layer] > *")) buildOverlay(state);
    schedule(state);
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
    unbindTrackElement(state);
    window.removeEventListener("scroll", state.scrolled, true);
    sessions.delete(element);
  }

  function diagnostics(element) {
    const state = sessions.get(element);
    if (!state) return null;
    return {
      ...state.metrics,
      logicalTraceCount: state.config.logicalTraceCount || state.config.traces.length,
      tooltipTraceCount: state.config.traces.length,
      renderedPathNodes: state.element.querySelectorAll("[data-analysis-trace-path]").length,
      renderedPathLayers: state.element.querySelectorAll("[data-analysis-trace-render-layer]").length,
      configuredRows: state.config.rows.length,
      aggregateBins: state.config.aggregate?.percents?.length || 0
    };
  }

  function resetDiagnostics(element) {
    const state = sessions.get(element);
    if (!state) return;
    state.metrics = { renderedFrames: 0, framesOver25ms: 0, maximumFrameGap: 0, lastFrame: null, resizeCallbacks: 0 };
  }

  window.iracingCoachAnalysisCursor = { initialize, sync, diagnostics, resetDiagnostics, dispose };
})();

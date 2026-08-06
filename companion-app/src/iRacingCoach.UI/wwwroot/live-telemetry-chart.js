(function () {
  const charts = new WeakMap();
  const maximumAgeMs = 10 * 60 * 1000;
  const maximumRetainedPoints = 144000;
  const panels = Object.freeze([
    { key: "speed", label: "Speed", unit: "mph", color: "speed" },
    { key: "throttle", label: "Throttle", unit: "%", color: "throttle" },
    { key: "brake", label: "Brake", unit: "%", color: "brake" },
    { key: "steering", label: "Steering", unit: "deg", color: "steering" }
  ]);

  function number(value) {
    return typeof value === "number" && Number.isFinite(value) ? value : null;
  }

  function normalize(point) {
    return {
      at: number(point.atUnixMilliseconds),
      speed: number(point.speedMph),
      throttle: number(point.throttlePercent),
      brake: number(point.brakePercent),
      steering: number(point.steeringDegrees),
      lastLap: number(point.lastLapSeconds)
    };
  }

  function clearPoints(state) {
    state.points = [];
    state.pointStart = 0;
    state.lapSeconds = 60;
    state.cacheEndTime = Date.now();
    state.lastAppendAt = performance.now();
    state.dataDirty = true;
  }

  function updateOptions(state, options) {
    const nextSessionEpoch = Math.max(0, Number(options && options.sessionEpoch) || 0);
    if (state.sessionEpoch !== null && nextSessionEpoch < state.sessionEpoch) return false;
    if (state.sessionEpoch !== null && nextSessionEpoch !== state.sessionEpoch) clearPoints(state);
    state.sessionEpoch = nextSessionEpoch;
    const historyLaps = Math.max(1, Math.min(10, Number(options && options.historyLaps) || 3));
    const sourceRate = Math.max(1, Math.min(240, Number(options && options.sourceRate) || 60));
    const reducedMotion = !!(options && options.reducedMotion);
    const suppliedLap = number(options && options.lastLapSeconds);
    const lapSeconds = suppliedLap && suppliedLap >= 10 && suppliedLap <= 600 ? suppliedLap : state.lapSeconds;
    if (historyLaps !== state.historyLaps || sourceRate !== state.sourceRate || lapSeconds !== state.lapSeconds) {
      state.dataDirty = true;
    }
    state.historyLaps = historyLaps;
    state.sourceRate = sourceRate;
    state.reducedMotion = reducedMotion;
    state.lapSeconds = lapSeconds;
    return true;
  }

  function palette(canvas) {
    const style = getComputedStyle(canvas);
    const color = (name, fallback) => style.getPropertyValue(name).trim() || fallback;
    return {
      background: color("--chart-background", "#17191c"),
      row: color("--surface-1", "#1b1d20"),
      border: color("--border-subtle", "#30353b"),
      grid: color("--chart-grid", "#30353b"),
      text: color("--text-secondary", "#b9bdc2"),
      muted: color("--text-muted", "#90969e"),
      speed: color("--telemetry-speed", "#8db4f6"),
      throttle: color("--telemetry-throttle", "#71c29a"),
      brake: color("--telemetry-brake", "#e08a80"),
      steering: color("--telemetry-steering", "#b99ce3")
    };
  }

  function canDraw(state) {
    if (state.disposed || !state.canvas.isConnected || document.hidden || state.intersecting === false) return false;
    if (state.disclosure && !state.disclosure.open) return false;
    if (state.canvas.offsetParent === null) return false;
    const rect = state.canvas.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  }

  function stopRendering(state) {
    if (!state.animationFrame) return;
    cancelAnimationFrame(state.animationFrame);
    state.animationFrame = 0;
  }

  function requestRender(state) {
    if (state.disposed || state.animationFrame || !canDraw(state)) return;
    state.animationFrame = requestAnimationFrame(now => {
      state.animationFrame = 0;
      if (draw(state, now)) requestRender(state);
    });
  }

  function visibilityChanged(state) {
    if (!canDraw(state)) {
      stopRendering(state);
      return;
    }
    state.layoutDirty = true;
    requestRender(state);
  }

  function updateLayout(state) {
    const labelWidth = 74;
    const right = 14;
    const rowGap = 8;
    const outerTop = 6;
    const rowHeight = (state.height - outerTop * 2 - rowGap * 3) / 4;
    state.plots = panels.map((_, index) => ({
      left: labelWidth,
      top: outerTop + index * (rowHeight + rowGap),
      width: Math.max(1, state.width - labelWidth - right),
      height: Math.max(1, rowHeight)
    }));
    state.bucketCount = Math.max(1, Math.floor(state.plots[0].width));
  }

  function resize(state) {
    const rect = state.canvas.getBoundingClientRect();
    const width = Math.max(1, Math.round(rect.width));
    const height = Math.max(1, Math.round(rect.height));
    const scale = Math.max(1, Math.min(2, window.devicePixelRatio || 1));
    const pixelWidth = Math.round(width * scale);
    const pixelHeight = Math.round(height * scale);
    const changed = state.canvas.width !== pixelWidth || state.canvas.height !== pixelHeight ||
      state.width !== width || state.height !== height;
    if (state.canvas.width !== pixelWidth || state.canvas.height !== pixelHeight) {
      state.canvas.width = pixelWidth;
      state.canvas.height = pixelHeight;
    }
    state.context.setTransform(scale, 0, 0, scale, 0, 0);
    state.width = width;
    state.height = height;
    if (changed || state.layoutDirty) {
      updateLayout(state);
      state.layoutDirty = false;
      state.dataDirty = true;
    }
  }

  function lowerBound(points, start, target) {
    let low = start;
    let high = points.length;
    while (low < high) {
      const middle = (low + high) >>> 1;
      if (points[middle].at < target) low = middle + 1;
      else high = middle;
    }
    return low;
  }

  function upperBound(points, start, target) {
    let low = start;
    let high = points.length;
    while (low < high) {
      const middle = (low + high) >>> 1;
      if (points[middle].at <= target) low = middle + 1;
      else high = middle;
    }
    return low;
  }

  function newPanelCache(bucketCount) {
    return {
      minima: new Float64Array(bucketCount),
      maxima: new Float64Array(bucketCount),
      latest: new Float64Array(bucketCount),
      times: new Float64Array(bucketCount),
      segments: new Int32Array(bucketCount),
      minimum: -1,
      maximum: 1
    };
  }

  function resetPanelCache(cache) {
    cache.minima.fill(Infinity);
    cache.maxima.fill(-Infinity);
    cache.latest.fill(NaN);
    cache.times.fill(NaN);
    cache.segments.fill(-1);
    cache.minimum = -1;
    cache.maximum = 1;
  }

  function ensurePanelCaches(state) {
    if (state.panelCaches && state.panelCaches[0].latest.length === state.bucketCount) return;
    state.panelCaches = panels.map(() => newPanelCache(state.bucketCount));
  }

  function setRange(panel, cache, observedMinimum, observedMaximum) {
    if (panel.key === "throttle" || panel.key === "brake") {
      cache.minimum = 0;
      cache.maximum = 100;
      return;
    }
    if (!Number.isFinite(observedMinimum) || !Number.isFinite(observedMaximum)) {
      cache.minimum = -1;
      cache.maximum = 1;
      return;
    }
    if (panel.key === "speed") {
      cache.minimum = 0;
      cache.maximum = Math.max(50, Math.ceil(observedMaximum / 25) * 25);
      return;
    }
    const magnitude = Math.max(30, Math.ceil(Math.max(Math.abs(observedMinimum), Math.abs(observedMaximum)) / 30) * 30);
    cache.minimum = -magnitude;
    cache.maximum = magnitude;
  }

  function rebuildCache(state) {
    ensurePanelCaches(state);
    for (const cache of state.panelCaches) resetPanelCache(cache);

    const latestPoint = state.points[state.points.length - 1];
    state.cacheDuration = Math.max(15000, Math.min(maximumAgeMs, state.historyLaps * state.lapSeconds * 1000));
    state.cacheEndTime = latestPoint ? latestPoint.at : Date.now();
    const startTime = state.cacheEndTime - state.cacheDuration;
    const first = lowerBound(state.points, state.pointStart, startTime);
    const end = upperBound(state.points, first, state.cacheEndTime);
    const observedMinimum = state.observedMinimum;
    const observedMaximum = state.observedMaximum;
    const segment = state.segment;
    observedMinimum.fill(Infinity);
    observedMaximum.fill(-Infinity);
    segment.fill(0);
    const gapThreshold = Math.max(50, 3500 / state.sourceRate);
    let previousAt = null;

    for (let pointIndex = first; pointIndex < end; pointIndex++) {
      const point = state.points[pointIndex];
      if (previousAt !== null && point.at - previousAt > gapThreshold) {
        for (let panelIndex = 0; panelIndex < panels.length; panelIndex++) segment[panelIndex]++;
      }
      previousAt = point.at;
      const fraction = (point.at - startTime) / state.cacheDuration;
      if (fraction < 0 || fraction > 1) continue;
      const bucket = Math.max(0, Math.min(state.bucketCount - 1, Math.floor(fraction * (state.bucketCount - 1))));

      for (let panelIndex = 0; panelIndex < panels.length; panelIndex++) {
        const panel = panels[panelIndex];
        const value = point[panel.key];
        if (value === null) {
          segment[panelIndex]++;
          continue;
        }
        const cache = state.panelCaches[panelIndex];
        if (cache.segments[bucket] !== segment[panelIndex]) {
          cache.minima[bucket] = value;
          cache.maxima[bucket] = value;
          cache.segments[bucket] = segment[panelIndex];
        } else {
          cache.minima[bucket] = Math.min(cache.minima[bucket], value);
          cache.maxima[bucket] = Math.max(cache.maxima[bucket], value);
        }
        cache.latest[bucket] = value;
        cache.times[bucket] = point.at;
        observedMinimum[panelIndex] = Math.min(observedMinimum[panelIndex], value);
        observedMaximum[panelIndex] = Math.max(observedMaximum[panelIndex], value);
      }
    }

    for (let panelIndex = 0; panelIndex < panels.length; panelIndex++) {
      setRange(panels[panelIndex], state.panelCaches[panelIndex], observedMinimum[panelIndex], observedMaximum[panelIndex]);
    }
    state.dataDirty = false;
  }

  function drawTrace(context, cache, panel, plot, startTime, duration, colors) {
    const valueSpan = Math.max(0.0001, cache.maximum - cache.minimum);
    const y = value => plot.top + plot.height - (value - cache.minimum) / valueSpan * plot.height;
    const x = time => plot.left + (time - startTime) / duration * plot.width;

    context.save();
    context.beginPath();
    context.rect(plot.left, plot.top, plot.width, plot.height);
    context.clip();
    context.strokeStyle = colors[panel.color];
    context.lineWidth = 1.75;
    context.lineJoin = "round";
    context.beginPath();
    let started = false;
    let previousSegment = -1;
    for (let bucket = 0; bucket < cache.latest.length; bucket++) {
      if (Number.isNaN(cache.latest[bucket])) continue;
      const px = x(cache.times[bucket]);
      if (px < plot.left - 1) continue;
      if (px > plot.left + plot.width + 1) break;
      const py = y(cache.latest[bucket]);
      if (!started || cache.segments[bucket] !== previousSegment) context.moveTo(px, py);
      else context.lineTo(px, py);
      started = true;
      previousSegment = cache.segments[bucket];
    }
    context.stroke();

    context.globalAlpha = 0.58;
    context.lineWidth = 1;
    context.beginPath();
    for (let bucket = 0; bucket < cache.latest.length; bucket++) {
      if (!Number.isFinite(cache.minima[bucket]) || cache.maxima[bucket] <= cache.minima[bucket]) continue;
      const px = x(cache.times[bucket]);
      if (px < plot.left - 1) continue;
      if (px > plot.left + plot.width + 1) break;
      context.moveTo(px, y(cache.minima[bucket]));
      context.lineTo(px, y(cache.maxima[bucket]));
    }
    context.stroke();
    context.restore();
  }

  function draw(state, now) {
    if (!canDraw(state)) return false;
    resize(state);
    if (state.dataDirty) rebuildCache(state);

    const framePeriod = 1000 / state.sourceRate;
    const coastLimit = framePeriod * 1.25;
    const elapsed = Math.max(0, now - state.lastAppendAt);
    const coast = state.reducedMotion ? 0 : Math.min(coastLimit, elapsed);
    const endTime = state.cacheEndTime + coast;
    const startTime = endTime - state.cacheDuration;
    const context = state.context;
    const colors = state.colors;
    context.clearRect(0, 0, state.width, state.height);
    context.fillStyle = colors.background;
    context.fillRect(0, 0, state.width, state.height);
    context.font = "600 11px 'Segoe UI', sans-serif";
    context.textBaseline = "top";

    for (let index = 0; index < panels.length; index++) {
      const panel = panels[index];
      const plot = state.plots[index];
      context.fillStyle = colors.row;
      context.strokeStyle = colors.border;
      context.lineWidth = 1;
      context.fillRect(plot.left, plot.top, plot.width, plot.height);
      context.strokeRect(plot.left + 0.5, plot.top + 0.5, plot.width - 1, plot.height - 1);
      context.strokeStyle = colors.grid;
      context.beginPath();
      context.moveTo(plot.left, plot.top + plot.height / 2 + 0.5);
      context.lineTo(plot.left + plot.width, plot.top + plot.height / 2 + 0.5);
      context.stroke();
      context.fillStyle = colors.text;
      context.fillText(panel.label, 8, plot.top + 8);
      context.fillStyle = colors.muted;
      context.font = "10px 'Segoe UI', sans-serif";
      context.fillText(panel.unit, 8, plot.top + 25);
      context.font = "600 11px 'Segoe UI', sans-serif";
      drawTrace(context, state.panelCaches[index], panel, plot, startTime, state.cacheDuration, colors);
    }

    return !state.reducedMotion && elapsed < coastLimit;
  }

  function prune(state) {
    const newest = state.points[state.points.length - 1];
    const cutoff = newest ? newest.at - maximumAgeMs : 0;
    while (state.pointStart < state.points.length &&
           (state.points[state.pointStart].at < cutoff || state.points.length - state.pointStart > maximumRetainedPoints)) {
      state.pointStart++;
    }
    if (state.pointStart > 4096 && state.pointStart * 3 > state.points.length) {
      state.points = state.points.slice(state.pointStart);
      state.pointStart = 0;
    }
  }

  function initialize(canvas, points, options) {
    if (!canvas) return false;
    const existing = charts.get(canvas);
    if (existing) {
      updateOptions(existing, options);
      requestRender(existing);
      return canDraw(existing);
    }
    const context = canvas.getContext("2d", { alpha: false });
    if (!context) return false;
    const normalizedPoints = [];
    for (const raw of points || []) {
      const point = normalize(raw);
      if (point.at !== null) normalizedPoints.push(point);
    }
    normalizedPoints.sort((left, right) => left.at - right.at);
    const state = {
      canvas,
      context,
      points: normalizedPoints,
      pointStart: 0,
      colors: palette(canvas),
      historyLaps: 3,
      sourceRate: 60,
      lapSeconds: 60,
      reducedMotion: false,
      dataDirty: true,
      layoutDirty: true,
      disposed: false,
      intersecting: true,
      lastAppendAt: performance.now(),
      animationFrame: 0,
      width: 0,
      height: 0,
      bucketCount: 1,
      panelCaches: null,
      observedMinimum: new Float64Array(panels.length),
      observedMaximum: new Float64Array(panels.length),
      segment: new Int32Array(panels.length),
      plots: [],
      cacheDuration: 15000,
      cacheEndTime: Date.now(),
      disclosure: canvas.closest("details"),
      sessionEpoch: null
    };
    updateOptions(state, options);
    for (const point of state.points) {
      if (point.lastLap && point.lastLap >= 10 && point.lastLap <= 600) state.lapSeconds = point.lastLap;
    }
    state.resizeObserver = new ResizeObserver(() => {
      state.layoutDirty = true;
      requestRender(state);
    });
    state.resizeObserver.observe(canvas);
    if (typeof IntersectionObserver === "function") {
      state.intersectionObserver = new IntersectionObserver(entries => {
        const entry = entries[entries.length - 1];
        state.intersecting = !!entry && entry.isIntersecting && entry.intersectionRect.width > 0 && entry.intersectionRect.height > 0;
        visibilityChanged(state);
      });
      state.intersectionObserver.observe(canvas);
    }
    state.documentVisibilityHandler = () => visibilityChanged(state);
    document.addEventListener("visibilitychange", state.documentVisibilityHandler);
    if (state.disclosure) {
      state.disclosureToggleHandler = () => visibilityChanged(state);
      state.disclosure.addEventListener("toggle", state.disclosureToggleHandler);
    }
    charts.set(canvas, state);
    requestRender(state);
    return canDraw(state);
  }

  function append(canvas, points, options) {
    const state = charts.get(canvas);
    if (!state) return false;
    if (!updateOptions(state, options)) return canDraw(state);
    let appended = false;
    for (const raw of points || []) {
      const point = normalize(raw);
      if (point.at === null) continue;
      state.points.push(point);
      appended = true;
      if (point.lastLap && point.lastLap >= 10 && point.lastLap <= 600 && point.lastLap !== state.lapSeconds) {
        state.lapSeconds = point.lastLap;
      }
    }
    if (appended) {
      prune(state);
      state.lastAppendAt = performance.now();
      state.dataDirty = true;
    }
    requestRender(state);
    return canDraw(state);
  }

  function configure(canvas, options) {
    const state = charts.get(canvas);
    if (!state) return false;
    if (!updateOptions(state, options)) return canDraw(state);
    state.colors = palette(canvas);
    requestRender(state);
    return canDraw(state);
  }

  function dispose(canvas) {
    const state = charts.get(canvas);
    if (!state) return;
    state.disposed = true;
    stopRendering(state);
    state.resizeObserver.disconnect();
    if (state.intersectionObserver) state.intersectionObserver.disconnect();
    document.removeEventListener("visibilitychange", state.documentVisibilityHandler);
    if (state.disclosure && state.disclosureToggleHandler) {
      state.disclosure.removeEventListener("toggle", state.disclosureToggleHandler);
    }
    charts.delete(canvas);
  }

  window.iracingCoachLiveTelemetryChart = { initialize, append, configure, dispose };
})();

(function () {
  const charts = new WeakMap();
  const maximumAgeMs = 10 * 60 * 1000;

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

  function updateOptions(state, options) {
    state.historyLaps = Math.max(1, Math.min(10, Number(options && options.historyLaps) || 3));
    state.reducedMotion = !!(options && options.reducedMotion);
    const suppliedLap = number(options && options.lastLapSeconds);
    if (suppliedLap && suppliedLap >= 10 && suppliedLap <= 600) state.lapSeconds = suppliedLap;
  }

  function resize(state) {
    const rect = state.canvas.getBoundingClientRect();
    const width = Math.max(320, Math.round(rect.width));
    const height = Math.max(220, Math.round(rect.height));
    const scale = Math.max(1, Math.min(2, window.devicePixelRatio || 1));
    const pixelWidth = Math.round(width * scale);
    const pixelHeight = Math.round(height * scale);
    if (state.canvas.width !== pixelWidth || state.canvas.height !== pixelHeight) {
      state.canvas.width = pixelWidth;
      state.canvas.height = pixelHeight;
    }
    state.context.setTransform(scale, 0, 0, scale, 0, 0);
    state.width = width;
    state.height = height;
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

  function range(panel, points) {
    if (panel.key === "throttle" || panel.key === "brake") return { minimum: 0, maximum: 100 };
    let minimum = Infinity;
    let maximum = -Infinity;
    for (const point of points) {
      const value = point[panel.key];
      if (value === null) continue;
      minimum = Math.min(minimum, value);
      maximum = Math.max(maximum, value);
    }
    if (!Number.isFinite(minimum) || !Number.isFinite(maximum)) return { minimum: -1, maximum: 1 };
    if (panel.key === "speed") return { minimum: 0, maximum: Math.max(50, Math.ceil(maximum / 25) * 25) };
    const magnitude = Math.max(30, Math.ceil(Math.max(Math.abs(minimum), Math.abs(maximum)) / 30) * 30);
    return { minimum: -magnitude, maximum: magnitude };
  }

  function drawTrace(context, points, panel, plot, startTime, duration, colors) {
    const buckets = Math.max(1, Math.floor(plot.width));
    const minima = new Float64Array(buckets);
    const maxima = new Float64Array(buckets);
    const latest = new Float64Array(buckets);
    minima.fill(Infinity);
    maxima.fill(-Infinity);
    latest.fill(NaN);
    const limits = range(panel, points);
    const valueSpan = Math.max(0.0001, limits.maximum - limits.minimum);

    for (const point of points) {
      const value = point[panel.key];
      if (value === null) continue;
      const fraction = (point.at - startTime) / duration;
      if (fraction < 0 || fraction > 1) continue;
      const bucket = Math.max(0, Math.min(buckets - 1, Math.floor(fraction * (buckets - 1))));
      minima[bucket] = Math.min(minima[bucket], value);
      maxima[bucket] = Math.max(maxima[bucket], value);
      latest[bucket] = value;
    }

    const y = value => plot.top + plot.height - (value - limits.minimum) / valueSpan * plot.height;
    context.strokeStyle = colors[panel.color];
    context.lineWidth = 1.75;
    context.lineJoin = "round";
    context.beginPath();
    let started = false;
    for (let bucket = 0; bucket < buckets; bucket++) {
      if (Number.isNaN(latest[bucket])) continue;
      const x = plot.left + bucket + 0.5;
      const py = y(latest[bucket]);
      if (!started) { context.moveTo(x, py); started = true; }
      else context.lineTo(x, py);
    }
    context.stroke();

    context.globalAlpha = 0.58;
    context.lineWidth = 1;
    context.beginPath();
    for (let bucket = 0; bucket < buckets; bucket++) {
      if (!Number.isFinite(minima[bucket]) || maxima[bucket] <= minima[bucket]) continue;
      const x = plot.left + bucket + 0.5;
      context.moveTo(x, y(minima[bucket]));
      context.lineTo(x, y(maxima[bucket]));
    }
    context.stroke();
    context.globalAlpha = 1;
  }

  function draw(state, now) {
    if (state.disposed) return;
    state.animationFrame = requestAnimationFrame(next => draw(state, next));
    if (!state.canvas.isConnected || state.canvas.offsetParent === null) return;
    if (state.reducedMotion && !state.dirty) return;
    resize(state);
    state.dirty = false;

    const context = state.context;
    const colors = state.colors;
    const width = state.width;
    const height = state.height;
    context.clearRect(0, 0, width, height);
    context.fillStyle = colors.background;
    context.fillRect(0, 0, width, height);

    const latestPoint = state.points[state.points.length - 1];
    const latestTime = latestPoint ? latestPoint.at : Date.now();
    const coast = state.reducedMotion ? 0 : Math.min(50, Math.max(0, now - state.lastAppendAt));
    const endTime = latestTime + coast;
    const duration = Math.max(15_000, Math.min(maximumAgeMs, state.historyLaps * state.lapSeconds * 1000));
    const startTime = endTime - duration;
    const visible = state.points.filter(point => point.at !== null && point.at >= startTime && point.at <= endTime);
    const panels = [
      { key: "speed", label: "Speed", unit: "mph", color: "speed" },
      { key: "throttle", label: "Throttle", unit: "%", color: "throttle" },
      { key: "brake", label: "Brake", unit: "%", color: "brake" },
      { key: "steering", label: "Steering", unit: "deg", color: "steering" }
    ];
    const labelWidth = 74;
    const right = 14;
    const rowGap = 8;
    const outerTop = 6;
    const rowHeight = (height - outerTop * 2 - rowGap * 3) / 4;

    context.font = "600 11px 'Segoe UI', sans-serif";
    context.textBaseline = "top";
    for (let index = 0; index < panels.length; index++) {
      const panel = panels[index];
      const top = outerTop + index * (rowHeight + rowGap);
      const plot = { left: labelWidth, top, width: width - labelWidth - right, height: rowHeight };
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
      context.fillText(panel.label, 8, top + 8);
      context.fillStyle = colors.muted;
      context.font = "10px 'Segoe UI', sans-serif";
      context.fillText(panel.unit, 8, top + 25);
      context.font = "600 11px 'Segoe UI', sans-serif";
      drawTrace(context, visible, panel, plot, startTime, duration, colors);
    }
  }

  function initialize(canvas, points, options) {
    if (!canvas) return;
    const existing = charts.get(canvas);
    if (existing) {
      updateOptions(existing, options);
      existing.dirty = true;
      return;
    }
    const state = {
      canvas,
      context: canvas.getContext("2d", { alpha: false }),
      points: (points || []).map(normalize).filter(point => point.at !== null),
      colors: palette(canvas),
      historyLaps: 3,
      lapSeconds: 60,
      reducedMotion: false,
      dirty: true,
      disposed: false,
      lastAppendAt: performance.now(),
      animationFrame: 0,
      width: 0,
      height: 0
    };
    updateOptions(state, options);
    for (const point of state.points) {
      if (point.lastLap && point.lastLap >= 10 && point.lastLap <= 600) state.lapSeconds = point.lastLap;
    }
    state.resizeObserver = new ResizeObserver(() => { state.dirty = true; });
    state.resizeObserver.observe(canvas);
    charts.set(canvas, state);
    state.animationFrame = requestAnimationFrame(now => draw(state, now));
  }

  function append(canvas, points, options) {
    const state = charts.get(canvas);
    if (!state) return;
    updateOptions(state, options);
    for (const raw of points || []) {
      const point = normalize(raw);
      if (point.at === null) continue;
      state.points.push(point);
      if (point.lastLap && point.lastLap >= 10 && point.lastLap <= 600) state.lapSeconds = point.lastLap;
    }
    const newest = state.points[state.points.length - 1];
    const cutoff = newest ? newest.at - maximumAgeMs : 0;
    let remove = 0;
    while (remove < state.points.length && state.points[remove].at < cutoff) remove++;
    if (remove > 0) state.points.splice(0, remove);
    state.lastAppendAt = performance.now();
    state.dirty = true;
  }

  function configure(canvas, options) {
    const state = charts.get(canvas);
    if (!state) return;
    updateOptions(state, options);
    state.dirty = true;
  }

  function dispose(canvas) {
    const state = charts.get(canvas);
    if (!state) return;
    state.disposed = true;
    cancelAnimationFrame(state.animationFrame);
    state.resizeObserver.disconnect();
    charts.delete(canvas);
  }

  window.iracingCoachLiveTelemetryChart = { initialize, append, configure, dispose };
})();

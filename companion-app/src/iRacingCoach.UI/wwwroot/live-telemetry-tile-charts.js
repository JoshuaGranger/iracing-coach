(function () {
  "use strict";

  const studios = new WeakMap();
  const maximumPoints = 36_000;
  const verticesPerPixel = 4;
  const resizeSettleMilliseconds = 72;
  const domainShrinkDelayMilliseconds = 6_000;
  const domainDecayIntervalMilliseconds = 1_500;
  const domainDecayFactor = 0.22;

  function finite(value) {
    if (value === null || value === undefined || value === "") return null;
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function clamp(value, minimum, maximum) {
    return Math.max(minimum, Math.min(maximum, value));
  }

  function updateOptions(studio, options) {
    const nextSessionEpoch = Math.max(0, finite(options && options.sessionEpoch) || 0);
    if (studio.sessionEpoch !== null && nextSessionEpoch < studio.sessionEpoch) return false;
    if (studio.sessionEpoch !== null && nextSessionEpoch !== studio.sessionEpoch) resetCharts(studio);
    studio.sessionEpoch = nextSessionEpoch;
    studio.sourceRate = clamp(finite(options && options.sourceRate) || 60, 1, 240);
    studio.reducedMotion = !!(options && options.reducedMotion);
    return true;
  }

  function secondsWindow(configuration) {
    switch (configuration.duration) {
      case "Seconds15": return 15_000;
      case "Seconds30": return 30_000;
      case "Seconds60": return 60_000;
      default: return null;
    }
  }

  function lapWindow(configuration) {
    return configuration.duration === "ThreeLaps" ? 3 : 1;
  }

  function lapProgress(point) {
    if (point.lap === null) return null;
    return point.lap + (point.lapDistance === null ? 0 : clamp(point.lapDistance, 0, 1));
  }

  function normalizePoint(point) {
    const structured = point !== null && typeof point === "object";
    const value = finite(structured ? point.value : point);
    const at = finite(structured ? point.atUnixMilliseconds : null);
    const lap = finite(structured ? point.lap : null);
    const lapDistance = finite(structured ? point.lapDistancePercent : null);
    const rawAbsActive = structured ? point.brakeAbsActive : null;
    // BrakeABSactive is the SDK's explicit intervention state. A cut percentage
    // can remain non-zero while that state is false, so it is never promoted
    // into an activation event.
    const absActive = rawAbsActive === true;
    // A structured missing sample is retained as a gap marker. Raw nulls do
    // not carry a position and therefore cannot truthfully be plotted.
    if (value === null && (!structured || at === null && lap === null)) return null;
    return {
      value,
      at,
      lap,
      lapDistance,
      absActive
    };
  }

  function isLapRegression(previous, next) {
    return previous !== null && next !== null && next < previous - 0.5;
  }

  function observeClock(studio, point, arrivedAt) {
    if (point.at !== null && (studio.latestSourceAt === null || point.at >= studio.latestSourceAt)) {
      studio.latestSourceAt = point.at;
      studio.latestArrivalAt = arrivedAt;
    }
    const progress = lapProgress(point);
    if (progress === null || point.at === null) return false;
    if (studio.latestLapAt !== null && point.at < studio.latestLapAt) return false;
    if (isLapRegression(studio.latestLapProgress, progress)) {
      studio.latestLapProgress = progress;
      studio.latestLapAt = point.at;
      studio.lapRatePerMillisecond = 0;
      return true;
    }
    if (studio.latestLapProgress !== null && studio.latestLapAt !== null && point.at > studio.latestLapAt) {
      const delta = progress - studio.latestLapProgress;
      if (delta >= 0 && delta < 0.25) studio.lapRatePerMillisecond = clamp(delta / (point.at - studio.latestLapAt), 0, 0.01);
    }
    if (studio.latestLapAt === null || point.at >= studio.latestLapAt) {
      studio.latestLapProgress = progress;
      studio.latestLapAt = point.at;
    }
    return false;
  }

  function predictedSourceAt(studio, now) {
    if (studio.latestSourceAt === null || studio.latestArrivalAt === null) return null;
    if (studio.reducedMotion) return studio.latestSourceAt;
    const coast = clamp(now - studio.latestArrivalAt, 0, 1500 / studio.sourceRate);
    return studio.latestSourceAt + coast;
  }

  function predictedLapProgress(studio, now) {
    if (studio.latestLapProgress === null || studio.latestArrivalAt === null) return null;
    if (studio.reducedMotion) return studio.latestLapProgress;
    const coast = clamp(now - studio.latestArrivalAt, 0, 1500 / studio.sourceRate);
    return studio.latestLapProgress + studio.lapRatePerMillisecond * coast;
  }

  function trim(chart, studio) {
    if (chart.points.length === chart.pointStart) return;
    let nextStart = chart.pointStart;
    const windowMilliseconds = secondsWindow(chart.configuration);
    if (windowMilliseconds !== null) {
      const newest = studio.latestSourceAt ?? latestTimestamp(chart);
      if (newest !== undefined && newest !== null) {
        const cutoff = newest - windowMilliseconds - 2000 / studio.sourceRate;
        while (nextStart < chart.points.length) {
          const at = chart.points[nextStart].at;
          if (at !== null && at >= cutoff) break;
          nextStart++;
        }
      }
    } else {
      const newestProgress = studio.latestLapProgress ?? lapProgress(chart.points[chart.points.length - 1]);
      if (newestProgress !== null) {
        const cutoff = newestProgress - lapWindow(chart.configuration) - 0.02;
        while (nextStart < chart.points.length) {
          const progress = lapProgress(chart.points[nextStart]);
          if (progress !== null && progress >= cutoff) break;
          nextStart++;
        }
      }
    }
    chart.pointStart = Math.max(nextStart, chart.points.length - maximumPoints);
    // Removing one expired sample with splice shifts the whole history on every
    // SDK tick. A moving start index makes steady-state trimming O(1); compact
    // only after the unreachable prefix is both large and dominant.
    if (chart.pointStart > 4096 && chart.pointStart * 2 > chart.points.length) {
      chart.points = chart.points.slice(chart.pointStart);
      chart.pointStart = 0;
    }
  }

  function latestTimestamp(chart) {
    for (let index = chart.points.length - 1; index >= chart.pointStart; index--)
      if (chart.points[index].at !== null) return chart.points[index].at;
    return null;
  }

  function resetDomain(chart) {
    chart.domain = null;
    chart.domainLastExpansionAt = 0;
    chart.domainLastAdjustmentAt = 0;
  }

  function resetLapCharts(studio) {
    for (const chart of studio.charts.values()) {
      if (secondsWindow(chart.configuration) !== null) continue;
      chart.points.length = 0;
      chart.pointStart = 0;
      chart.path = null;
      chart.absPath = null;
      chart.pathAnchorAt = null;
      chart.pathAnchorLap = null;
      chart.pathDirty = true;
      chart.dirty = true;
      resetDomain(chart);
    }
  }

  function resetCharts(studio) {
    studio.latestSourceAt = null;
    studio.latestArrivalAt = null;
    studio.latestLapProgress = null;
    studio.latestLapAt = null;
    studio.lapRatePerMillisecond = 0;
    for (const chart of studio.charts.values()) {
      chart.points.length = 0;
      chart.pointStart = 0;
      chart.path = null;
      chart.absPath = null;
      chart.pathAnchorAt = null;
      chart.pathAnchorLap = null;
      chart.pathDirty = true;
      chart.dirty = true;
      resetDomain(chart);
    }
  }

  function palette(canvas) {
    const style = getComputedStyle(canvas.closest("[data-live-tile]") || canvas);
    return {
      line: style.getPropertyValue("--tile-accent").trim() || style.getPropertyValue("--accent").trim() || "#65d0b6",
      grid: style.getPropertyValue("--chart-grid").trim() || "#30383a"
    };
  }

  function createChart(canvas, configuration, studio) {
    const initial = canvas.getBoundingClientRect();
    const chart = {
      canvas,
      context: canvas.getContext("2d", { alpha: true, desynchronized: true }),
      configuration,
      points: [],
      pointStart: 0,
      path: null,
      absPath: null,
      pathAnchorAt: null,
      pathAnchorLap: null,
      width: 0,
      height: 0,
      scale: 1,
      pendingWidth: initial.width,
      pendingHeight: initial.height,
      resizeObservedAt: 0,
      resizeDirty: true,
      pathDirty: true,
      dirty: true,
      domain: null,
      domainLastExpansionAt: 0,
      domainLastAdjustmentAt: 0,
      palette: palette(canvas),
      resizeObserver: null
    };
    chart.resizeObserver = new ResizeObserver(entries => {
      const size = entries[0] && entries[0].contentRect;
      chart.pendingWidth = size ? size.width : canvas.clientWidth;
      chart.pendingHeight = size ? size.height : canvas.clientHeight;
      chart.resizeObservedAt = performance.now();
      chart.resizeDirty = true;
      requestStudioDraw(studio);
    });
    chart.resizeObserver.observe(canvas);
    return chart;
  }

  function resize(chart, now) {
    if (!chart.resizeDirty && chart.width > 0 && chart.height > 0) {
      chart.context.setTransform(chart.scale, 0, 0, chart.scale, 0, 0);
      return true;
    }
    // During the 500 ms toolbox/reflow animation the canvas CSS box changes on
    // every frame. Keep the existing bitmap and let the compositor scale it;
    // resizing the backing store each step clears the canvas and forces every
    // history path to be rebuilt. Commit once the box has settled.
    if (chart.width > 0 && now - chart.resizeObservedAt < resizeSettleMilliseconds) return false;
    const width = Math.max(80, Math.round(chart.pendingWidth || chart.width || 1));
    const height = Math.max(44, Math.round(chart.pendingHeight || chart.height || 1));
    const scale = clamp(window.devicePixelRatio || 1, 1, 2.5);
    const pixelWidth = Math.round(width * scale);
    const pixelHeight = Math.round(height * scale);
    if (chart.canvas.width !== pixelWidth || chart.canvas.height !== pixelHeight) {
      chart.canvas.width = pixelWidth;
      chart.canvas.height = pixelHeight;
      chart.width = width;
      chart.height = height;
      chart.scale = scale;
      chart.pathDirty = true;
    }
    chart.resizeDirty = false;
    chart.context.setTransform(scale, 0, 0, scale, 0, 0);
    return true;
  }

  function seed(chart, values, studio) {
    chart.points = [];
    chart.pointStart = 0;
    resetDomain(chart);
    const arrivedAt = performance.now();
    let localLapProgress = null;
    let localLapAt = null;
    for (const rawPoint of values || []) {
      const point = normalizePoint(rawPoint);
      if (!point) continue;
      const progress = lapProgress(point);
      if (secondsWindow(chart.configuration) === null && progress !== null &&
          (localLapAt === null || point.at === null || point.at >= localLapAt) &&
          isLapRegression(localLapProgress, progress)) {
        // The durable history can span a qualifying/race transition without
        // an SDK disconnect. A lap chart must contain only the newest session.
        chart.points.length = 0;
        chart.pointStart = 0;
        resetDomain(chart);
      }
      chart.points.push(point);
      if (progress !== null && (localLapAt === null || point.at === null || point.at >= localLapAt)) {
        localLapProgress = progress;
        localLapAt = point.at;
      }
      observeClock(studio, point, arrivedAt);
    }
    trim(chart, studio);
    chart.pathDirty = true;
    chart.dirty = true;
  }

  function observedRange(chart) {
    let minimum = Infinity;
    let maximum = -Infinity;
    let count = 0;
    for (let index = chart.pointStart; index < chart.points.length; index++) {
      const point = chart.points[index];
      if (point.value === null) continue;
      minimum = Math.min(minimum, point.value);
      maximum = Math.max(maximum, point.value);
      count++;
    }
    return count === 0 ? null : { minimum, maximum };
  }

  function desiredDomain(chart, observed) {
    const configuredMinimum = finite(chart.configuration.minimum);
    const configuredMaximum = finite(chart.configuration.maximum);
    const observedSpan = Math.max(
      1,
      observed.maximum - observed.minimum,
      Math.abs(observed.minimum) * 0.16,
      Math.abs(observed.maximum) * 0.16);
    const padding = observedSpan * 0.35;
    let minimum = configuredMinimum === null ? observed.minimum - padding : Math.min(configuredMinimum, observed.minimum);
    let maximum = configuredMaximum === null ? observed.maximum + padding : Math.max(configuredMaximum, observed.maximum);
    if (maximum <= minimum) {
      minimum -= Math.max(0.0001, observedSpan / 2);
      maximum += Math.max(0.0001, observedSpan / 2);
    }
    return { minimum, maximum };
  }

  function stableRange(chart, observed, now) {
    const desired = desiredDomain(chart, observed);
    if (!chart.domain) {
      chart.domain = desired;
      chart.domainLastExpansionAt = now;
      chart.domainLastAdjustmentAt = now;
      return chart.domain;
    }

    let { minimum, maximum } = chart.domain;
    const currentSpan = Math.max(0.0001, maximum - minimum);
    let expanded = false;
    if (desired.minimum < minimum) {
      minimum = Math.min(desired.minimum, observed.minimum - currentSpan * 0.5);
      expanded = true;
    }
    if (desired.maximum > maximum) {
      maximum = Math.max(desired.maximum, observed.maximum + currentSpan * 0.5);
      expanded = true;
    }
    if (expanded) {
      chart.domain = { minimum, maximum };
      chart.domainLastExpansionAt = now;
      chart.domainLastAdjustmentAt = now;
      return chart.domain;
    }

    const desiredSpan = Math.max(0.0001, desired.maximum - desired.minimum);
    const hasMaterialHeadroom = desiredSpan < currentSpan * 0.72;
    if (hasMaterialHeadroom && now - chart.domainLastExpansionAt >= domainShrinkDelayMilliseconds &&
        now - chart.domainLastAdjustmentAt >= domainDecayIntervalMilliseconds) {
      minimum += (desired.minimum - minimum) * domainDecayFactor;
      maximum += (desired.maximum - maximum) * domainDecayFactor;
      // Never decay through a currently visible sample.
      minimum = Math.min(minimum, observed.minimum);
      maximum = Math.max(maximum, observed.maximum);
      chart.domain = { minimum, maximum };
      chart.domainLastAdjustmentAt = now;
    }
    return chart.domain;
  }

  function pointX(chart, point, windowMilliseconds) {
    if (windowMilliseconds !== null)
      return point.at !== null && chart.pathAnchorAt !== null
        ? chart.width - (chart.pathAnchorAt - point.at) / windowMilliseconds * chart.width
        : null;
    const progress = lapProgress(point);
    return progress !== null && chart.pathAnchorLap !== null
      ? chart.width - (chart.pathAnchorLap - progress) / lapWindow(chart.configuration) * chart.width
      : null;
  }

  function buildPixelEnvelope(chart, studio, valueRange) {
    const windowMilliseconds = secondsWindow(chart.configuration);
    const top = 4;
    const bottom = chart.height - 4;
    const span = Math.max(0.0001, valueRange.maximum - valueRange.minimum);
    const y = value => bottom - (value - valueRange.minimum) / span * (bottom - top);
    const gapThreshold = Math.max(40, 3500 / studio.sourceRate);
    const bucketCount = Math.max(1, Math.ceil(chart.width));
    const maximumVertices = Math.max(8, bucketCount * verticesPerPixel);
    let path = new Path2D();
    let vertexCount = 0;
    let segmentOpen = false;
    let previousY = 0;
    let bucketIndex = -1;
    let firstOrder = -1, firstX = 0, firstY = 0;
    let minimumOrder = -1, minimumX = 0, minimumY = 0, minimumValue = 0;
    let maximumOrder = -1, maximumX = 0, maximumY = 0, maximumValue = 0;
    let lastOrder = -1, lastX = 0, lastY = 0;
    let previousAt = null;
    let order = 0;

    const emit = (x, py) => {
      if (vertexCount >= maximumVertices) {
        // Path2D cannot discard its prefix. Starting a fresh path here keeps
        // the newest visible envelope bounded under pathological gap data.
        path = new Path2D();
        vertexCount = 0;
        segmentOpen = false;
      }
      if (!segmentOpen) path.moveTo(x, py);
      else {
        if (chart.configuration.shape === "step") path.lineTo(x, previousY);
        path.lineTo(x, py);
      }
      previousY = py;
      segmentOpen = true;
      vertexCount += chart.configuration.shape === "step" && vertexCount > 0 ? 2 : 1;
    };

    const flushBucket = () => {
      if (bucketIndex < 0) return;
      let emittedOrder = -1;
      // first/minimum/maximum/last preserves brief extrema and
      // source order. Primitive slots avoid a candidate object plus a Map and
      // sorted array for every horizontal pixel on every SDK sample.
      for (let slot = 0; slot < verticesPerPixel; slot++) {
        let bestOrder = Number.MAX_SAFE_INTEGER;
        let bestX = 0;
        let bestY = 0;
        if (firstOrder > emittedOrder && firstOrder < bestOrder) { bestOrder = firstOrder; bestX = firstX; bestY = firstY; }
        if (minimumOrder > emittedOrder && minimumOrder < bestOrder) { bestOrder = minimumOrder; bestX = minimumX; bestY = minimumY; }
        if (maximumOrder > emittedOrder && maximumOrder < bestOrder) { bestOrder = maximumOrder; bestX = maximumX; bestY = maximumY; }
        if (lastOrder > emittedOrder && lastOrder < bestOrder) { bestOrder = lastOrder; bestX = lastX; bestY = lastY; }
        if (bestOrder === Number.MAX_SAFE_INTEGER) break;
        emit(bestX, bestY);
        emittedOrder = bestOrder;
      }
      bucketIndex = -1;
    };

    const finishSegment = () => {
      flushBucket();
      segmentOpen = false;
    };

    for (let index = chart.pointStart; index < chart.points.length; index++) {
      const point = chart.points[index];
      order++;
      if (point.value === null) {
        finishSegment();
        previousAt = point.at;
        continue;
      }
      if (previousAt !== null && point.at !== null && point.at - previousAt > gapThreshold) finishSegment();
      previousAt = point.at;
      const x = pointX(chart, point, windowMilliseconds);
      if (x === null || x < -2 || x > chart.width + 2) continue;
      const py = y(point.value);
      const nextBucket = clamp(Math.floor(x), 0, bucketCount - 1);
      if (bucketIndex !== nextBucket) {
        flushBucket();
        bucketIndex = nextBucket;
        firstOrder = minimumOrder = maximumOrder = lastOrder = order;
        firstX = minimumX = maximumX = lastX = x;
        firstY = minimumY = maximumY = lastY = py;
        minimumValue = maximumValue = point.value;
      } else {
        lastOrder = order; lastX = x; lastY = py;
        if (point.value < minimumValue) { minimumOrder = order; minimumX = x; minimumY = py; minimumValue = point.value; }
        if (point.value > maximumValue) { maximumOrder = order; maximumX = x; maximumY = py; maximumValue = point.value; }
      }
    }
    finishSegment();
    return { path, vertexCount, y, windowMilliseconds, maximumVertices, gapThreshold };
  }

  function buildAbsPath(chart, envelope) {
    if (!chart.configuration.highlightAbs) return { path: null, vertexCount: 0 };
    let path = new Path2D();
    let vertexCount = 0;
    let runOpen = false;
    let previousValidX = null;
    let previousValidY = null;
    let previousY = 0;
    let previousAt = null;
    for (let index = chart.pointStart; index < chart.points.length; index++) {
      const point = chart.points[index];
      if (point.value === null) {
        runOpen = false;
        previousValidX = previousValidY = null;
        previousAt = point.at;
        continue;
      }
      if (previousAt !== null && point.at !== null && point.at - previousAt > envelope.gapThreshold) {
        runOpen = false;
        previousValidX = previousValidY = null;
      }
      previousAt = point.at;
      const x = pointX(chart, point, envelope.windowMilliseconds);
      if (x === null || x < -2 || x > chart.width + 2) continue;
      const py = envelope.y(point.value);
      if (point.absActive === true) {
        if (vertexCount >= envelope.maximumVertices) {
          path = new Path2D();
          vertexCount = 0;
          runOpen = false;
        }
        if (!runOpen) {
          path.moveTo(previousValidX ?? x, previousValidY ?? py);
          runOpen = true;
          vertexCount++;
        }
        if (chart.configuration.shape === "step") path.lineTo(x, previousY);
        path.lineTo(x, py);
        vertexCount += chart.configuration.shape === "step" ? 2 : 1;
      } else {
        runOpen = false;
      }
      previousValidX = x;
      previousValidY = py;
      previousY = py;
    }
    return { path, vertexCount };
  }

  function buildPath(chart, studio, now) {
    trim(chart, studio);
    const observed = observedRange(chart);
    if (!observed) {
      chart.path = null;
      chart.absPath = null;
      chart.pathAnchorAt = null;
      chart.pathAnchorLap = null;
      chart.pathDirty = false;
      return;
    }
    chart.pathAnchorAt = studio.latestSourceAt ?? latestTimestamp(chart);
    chart.pathAnchorLap = studio.latestLapProgress ?? lapProgress(chart.points[chart.points.length - 1]);
    const envelope = buildPixelEnvelope(chart, studio, stableRange(chart, observed, now));
    const abs = buildAbsPath(chart, envelope);
    chart.path = envelope.vertexCount > 1 ? envelope.path : null;
    chart.absPath = abs.vertexCount > 1 ? abs.path : null;
    chart.pathDirty = false;
  }

  function canDrawStudio(studio) {
    return !studio.disposed && studio.root.isConnected && !document.hidden && studio.intersecting !== false;
  }

  function stopStudioDraw(studio) {
    if (!studio.animationFrame) return;
    cancelAnimationFrame(studio.animationFrame);
    studio.animationFrame = 0;
  }

  function requestStudioDraw(studio) {
    if (studio.animationFrame || !canDrawStudio(studio)) return;
    studio.animationFrame = requestAnimationFrame(now => {
      studio.animationFrame = 0;
      if (drawStudio(studio, now)) requestStudioDraw(studio);
    });
  }

  function studioVisibilityChanged(studio) {
    if (!canDrawStudio(studio)) {
      stopStudioDraw(studio);
      return;
    }
    for (const chart of studio.charts.values()) chart.dirty = true;
    requestStudioDraw(studio);
  }

  function configure(root, configurations, options) {
    if (!root) return;
    let studio = studios.get(root);
    if (!studio) {
      studio = {
        root,
        charts: new Map(),
        sourceRate: 60,
        reducedMotion: false,
        disposed: false,
        animationFrame: 0,
        intersecting: true,
        latestSourceAt: null,
        latestArrivalAt: null,
        latestLapProgress: null,
        latestLapAt: null,
        lapRatePerMillisecond: 0,
        sessionEpoch: null
      };
      studios.set(root, studio);
      studio.documentVisibilityHandler = () => studioVisibilityChanged(studio);
      document.addEventListener("visibilitychange", studio.documentVisibilityHandler);
      if (typeof IntersectionObserver === "function") {
        studio.intersectionObserver = new IntersectionObserver(entries => {
          const entry = entries[entries.length - 1];
          studio.intersecting = !!entry && entry.isIntersecting && entry.intersectionRect.width > 0 && entry.intersectionRect.height > 0;
          studioVisibilityChanged(studio);
        });
        studio.intersectionObserver.observe(root);
      }
    }
    const previousSessionEpoch = studio.sessionEpoch;
    if (!updateOptions(studio, options)) return;
    const sessionChanged = previousSessionEpoch !== null && previousSessionEpoch !== studio.sessionEpoch;
    const canvases = new Map(Array.from(root.querySelectorAll("[data-live-trend-chart]"), canvas => [canvas.dataset.liveTrendId, canvas]));
    const retained = new Set();
    for (const configuration of configurations || []) {
      const canvas = canvases.get(configuration.id);
      if (!canvas) continue;
      retained.add(configuration.id);
      let chart = studio.charts.get(configuration.id);
      const identity = `${configuration.metricId}|${configuration.unit}|${configuration.duration}|${configuration.shape}|${!!configuration.highlightAbs}`;
      if (!chart || chart.canvas !== canvas) {
        if (chart) chart.resizeObserver.disconnect();
        chart = createChart(canvas, configuration, studio);
        chart.identity = identity;
        studio.charts.set(configuration.id, chart);
        seed(chart, configuration.seed, studio);
      } else {
        const changed = chart.identity !== identity;
        chart.configuration = configuration;
        chart.identity = identity;
        chart.palette = palette(canvas);
        if (changed || sessionChanged) seed(chart, configuration.seed, studio);
        else { chart.pathDirty = true; chart.dirty = true; }
      }
    }
    for (const [id, chart] of studio.charts) {
      if (retained.has(id)) continue;
      chart.resizeObserver.disconnect();
      studio.charts.delete(id);
    }
    requestStudioDraw(studio);
  }

  function append(root, frames, options) {
    const studio = studios.get(root);
    if (!studio || studio.disposed) return;
    if (!updateOptions(studio, options)) return;
    const arrivedAt = performance.now();
    const touched = new Set();
    for (const frame of frames || []) {
      const clockPoint = normalizePoint({
        value: 0,
        atUnixMilliseconds: frame.atUnixMilliseconds,
        lap: frame.lap,
        lapDistancePercent: frame.lapDistancePercent,
        brakeAbsActive: frame.brakeAbsActive
      });
      if (clockPoint && observeClock(studio, clockPoint, arrivedAt)) resetLapCharts(studio);
      for (const [id, chart] of studio.charts) {
        const point = normalizePoint({
          value: frame.values && frame.values[id],
          atUnixMilliseconds: frame.atUnixMilliseconds,
          lap: frame.lap,
          lapDistancePercent: frame.lapDistancePercent,
          brakeAbsActive: frame.brakeAbsActive
        });
        if (!point) continue;
        chart.points.push(point);
        touched.add(chart);
      }
    }
    for (const chart of touched) {
      trim(chart, studio);
      chart.pathDirty = true;
      chart.dirty = true;
    }
    requestStudioDraw(studio);
  }

  function drawChart(chart, studio, now) {
    if (!chart.canvas.isConnected) return false;
    const scrolling = !studio.reducedMotion && chart.path !== null && studio.latestArrivalAt !== null && now - studio.latestArrivalAt <= Math.max(40, 2500 / studio.sourceRate);
    if (!chart.dirty && !scrolling && !chart.resizeDirty) return false;
    const resizeReady = resize(chart, now);
    if (!resizeReady && !chart.dirty && !scrolling) return true;
    if (chart.pathDirty) buildPath(chart, studio, now);
    const context = chart.context;
    const width = chart.width;
    const height = chart.height;
    context.clearRect(0, 0, width, height);
    context.strokeStyle = chart.palette.grid;
    context.globalAlpha = 0.74;
    context.lineWidth = 1;
    context.beginPath();
    context.moveTo(0, Math.round(height / 2) + 0.5);
    context.lineTo(width, Math.round(height / 2) + 0.5);
    context.stroke();
    context.globalAlpha = 1;
    if (chart.path) {
      let shift = 0;
      const windowMilliseconds = secondsWindow(chart.configuration);
      if (windowMilliseconds !== null && chart.pathAnchorAt !== null) {
        const renderAt = predictedSourceAt(studio, now);
        if (renderAt !== null) shift = Math.max(0, (renderAt - chart.pathAnchorAt) / windowMilliseconds * width);
      } else if (chart.pathAnchorLap !== null) {
        const renderLap = predictedLapProgress(studio, now);
        if (renderLap !== null) shift = Math.max(0, (renderLap - chart.pathAnchorLap) / lapWindow(chart.configuration) * width);
      }
      context.save();
      context.translate(-shift, 0);
      context.strokeStyle = chart.palette.line;
      context.lineWidth = 1.65;
      context.lineJoin = "round";
      context.lineCap = "round";
      context.stroke(chart.path);
      if (chart.absPath) {
        context.strokeStyle = "#f4c24f";
        context.lineWidth = 2.2;
        context.stroke(chart.absPath);
      }
      context.restore();
    }
    chart.dirty = false;
    return scrolling || chart.resizeDirty;
  }

  function drawStudio(studio, now) {
    if (!canDrawStudio(studio)) return false;
    let continueDrawing = false;
    for (const chart of studio.charts.values()) continueDrawing = drawChart(chart, studio, now) || continueDrawing;
    return continueDrawing;
  }

  function dispose(root) {
    const studio = studios.get(root);
    if (!studio) return;
    studio.disposed = true;
    stopStudioDraw(studio);
    document.removeEventListener("visibilitychange", studio.documentVisibilityHandler);
    if (studio.intersectionObserver) studio.intersectionObserver.disconnect();
    for (const chart of studio.charts.values()) chart.resizeObserver.disconnect();
    studio.charts.clear();
    studios.delete(root);
  }

  window.iracingCoachLiveTelemetryTileCharts = { configure, append, dispose };
})();

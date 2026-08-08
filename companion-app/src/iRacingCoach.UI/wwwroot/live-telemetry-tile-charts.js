(function () {
  "use strict";

  const studios = new WeakMap();
  const maximumPoints = 36_000;
  const verticesPerPixel = 4;
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
    if (chart.points.length === 0) return;
    let removeCount = 0;
    const windowMilliseconds = secondsWindow(chart.configuration);
    if (windowMilliseconds !== null) {
      const newest = studio.latestSourceAt ?? [...chart.points].reverse().find(point => point.at !== null)?.at;
      if (newest !== undefined && newest !== null) {
        const cutoff = newest - windowMilliseconds - 2000 / studio.sourceRate;
        while (removeCount < chart.points.length) {
          const at = chart.points[removeCount].at;
          if (at !== null && at >= cutoff) break;
          removeCount++;
        }
      }
    } else {
      const newestProgress = studio.latestLapProgress ?? lapProgress(chart.points[chart.points.length - 1]);
      if (newestProgress !== null) {
        const cutoff = newestProgress - lapWindow(chart.configuration) - 0.02;
        while (removeCount < chart.points.length) {
          const progress = lapProgress(chart.points[removeCount]);
          if (progress !== null && progress >= cutoff) break;
          removeCount++;
        }
      }
    }
    if (removeCount > 0) chart.points.splice(0, removeCount);
    if (chart.points.length > maximumPoints) chart.points.splice(0, chart.points.length - maximumPoints);
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

  function createChart(canvas, configuration) {
    const chart = {
      canvas,
      context: canvas.getContext("2d", { alpha: true, desynchronized: true }),
      configuration,
      points: [],
      path: null,
      absPath: null,
      pathAnchorAt: null,
      pathAnchorLap: null,
      width: 0,
      height: 0,
      scale: 1,
      pendingWidth: 0,
      pendingHeight: 0,
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
      chart.resizeDirty = true;
      chart.pathDirty = true;
      chart.dirty = true;
    });
    chart.resizeObserver.observe(canvas);
    return chart;
  }

  function resize(chart) {
    if (!chart.resizeDirty && chart.width > 0 && chart.height > 0) {
      chart.context.setTransform(chart.scale, 0, 0, chart.scale, 0, 0);
      return;
    }
    const width = Math.max(80, Math.round(chart.pendingWidth || chart.canvas.clientWidth || 1));
    const height = Math.max(44, Math.round(chart.pendingHeight || chart.canvas.clientHeight || 1));
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
  }

  function seed(chart, values, studio) {
    chart.points = [];
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
    for (const point of chart.points) {
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

  function decimatedSegments(chart, studio, valueRange) {
    const windowMilliseconds = secondsWindow(chart.configuration);
    const top = 4;
    const bottom = chart.height - 4;
    const span = Math.max(0.0001, valueRange.maximum - valueRange.minimum);
    const y = value => bottom - (value - valueRange.minimum) / span * (bottom - top);
    const gapThreshold = Math.max(40, 3500 / studio.sourceRate);
    const bucketCount = Math.max(1, Math.ceil(chart.width));
    const segments = [];
    let segment = [];
    let bucket = null;
    let previousAt = null;
    let order = 0;

    const flushBucket = () => {
      if (!bucket) return;
      const unique = new Map();
      for (const candidate of [bucket.first, bucket.minimum, bucket.maximum, bucket.absFirst, bucket.absLast, bucket.last])
        if (candidate) unique.set(candidate.order, candidate);
      const ordered = Array.from(unique.values()).sort((left, right) => left.order - right.order);
      for (const candidate of ordered) segment.push(candidate);
      bucket = null;
    };
    const finishSegment = () => {
      flushBucket();
      if (segment.length > 0) segments.push(segment);
      segment = [];
    };

    for (const point of chart.points) {
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
      const candidate = {
        x,
        y: y(point.value),
        value: point.value,
        order,
        absActive: point.absActive === true
      };
      const bucketIndex = clamp(Math.floor(x), 0, bucketCount - 1);
      if (!bucket || bucket.index !== bucketIndex) {
        flushBucket();
        bucket = { index: bucketIndex, first: candidate, minimum: candidate, maximum: candidate, last: candidate, absFirst: candidate.absActive ? candidate : null, absLast: candidate.absActive ? candidate : null };
      } else {
        bucket.last = candidate;
        if (candidate.value < bucket.minimum.value) bucket.minimum = candidate;
        if (candidate.value > bucket.maximum.value) bucket.maximum = candidate;
        if (candidate.absActive) {
          if (!bucket.absFirst) bucket.absFirst = candidate;
          bucket.absLast = candidate;
        }
      }
    }
    finishSegment();

    // Each horizontal pixel contributes at most first/min/max/last. Keeping
    // candidates in source order preserves brief extrema without drawing tens
    // of thousands of redundant vertices at every display refresh.
    const maximumVertices = Math.max(8, bucketCount * verticesPerPixel);
    let remaining = maximumVertices;
    const bounded = [];
    for (let index = segments.length - 1; index >= 0 && remaining > 0; index--) {
      const source = segments[index];
      if (source.length > remaining) bounded.unshift(source.slice(source.length - remaining));
      else bounded.unshift(source);
      remaining -= Math.min(source.length, remaining);
    }
    return bounded;
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
    chart.pathAnchorAt = studio.latestSourceAt ?? [...chart.points].reverse().find(point => point.at !== null)?.at ?? null;
    chart.pathAnchorLap = studio.latestLapProgress ?? lapProgress(chart.points[chart.points.length - 1]);
    const segments = decimatedSegments(chart, studio, stableRange(chart, observed, now));
    const path = new Path2D();
    const absPath = new Path2D();
    let vertexCount = 0;
    let absVertexCount = 0;
    for (const segment of segments) {
      if (segment.length === 0) continue;
      path.moveTo(segment[0].x, segment[0].y);
      vertexCount++;
      let previous = segment[0];
      let absRunOpen = false;
      if (segment[0].absActive) {
        absPath.moveTo(segment[0].x, segment[0].y);
        absPath.lineTo(segment[0].x + 0.01, segment[0].y);
        absRunOpen = true;
        absVertexCount += 2;
      }
      for (let index = 1; index < segment.length; index++) {
        const next = segment[index];
        if (chart.configuration.shape === "step") path.lineTo(next.x, previous.y);
        path.lineTo(next.x, next.y);
        vertexCount += chart.configuration.shape === "step" ? 2 : 1;
        if (next.absActive) {
          if (!absRunOpen) {
            absPath.moveTo(previous.x, previous.y);
            absRunOpen = true;
            absVertexCount++;
          }
          if (chart.configuration.shape === "step") absPath.lineTo(next.x, previous.y);
          absPath.lineTo(next.x, next.y);
          absVertexCount += chart.configuration.shape === "step" ? 2 : 1;
        } else {
          absRunOpen = false;
        }
        previous = next;
      }
    }
    chart.path = vertexCount > 1 ? path : null;
    chart.absPath = chart.configuration.highlightAbs && absVertexCount > 1 ? absPath : null;
    chart.pathDirty = false;
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
        latestSourceAt: null,
        latestArrivalAt: null,
        latestLapProgress: null,
        latestLapAt: null,
        lapRatePerMillisecond: 0,
        sessionEpoch: null
      };
      studios.set(root, studio);
      studio.animationFrame = requestAnimationFrame(now => drawStudio(studio, now));
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
        chart = createChart(canvas, configuration);
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
  }

  function drawChart(chart, studio, now) {
    if (!chart.canvas.isConnected || chart.canvas.offsetParent === null) return;
    const scrolling = !studio.reducedMotion && chart.path !== null && studio.latestArrivalAt !== null && now - studio.latestArrivalAt <= Math.max(40, 2500 / studio.sourceRate);
    if (!chart.dirty && !scrolling) return;
    resize(chart);
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
  }

  function drawStudio(studio, now) {
    if (studio.disposed) return;
    studio.animationFrame = requestAnimationFrame(next => drawStudio(studio, next));
    if (!studio.root.isConnected) return;
    for (const chart of studio.charts.values()) drawChart(chart, studio, now);
  }

  function dispose(root) {
    const studio = studios.get(root);
    if (!studio) return;
    studio.disposed = true;
    cancelAnimationFrame(studio.animationFrame);
    for (const chart of studio.charts.values()) chart.resizeObserver.disconnect();
    studio.charts.clear();
    studios.delete(root);
  }

  window.iracingCoachLiveTelemetryTileCharts = { configure, append, dispose };
})();

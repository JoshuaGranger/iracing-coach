import { clamp, finite, formatSignalValue, nearestIndex, projectedTrackFraction, signed, tooltipX } from "./cursor-core";
import { mapPointAt } from "../geometry/map-model";
import { TOOLTIP_ROW_HEIGHT, type CursorConfig } from "./cursor-config";

/**
 * Synchronized chart/map cursor (port of analysis-telemetry-cursor.js, reworked for the G2 gate).
 *
 * Per pointer frame the work is bounded by what is visible, never by lap count: one bin lookup on the
 * shared bin grid and one canvas dirty-rect redraw holding the cursor line, one marker per visible lap
 * and signal, and the per-row value cards. The cards are drawn on the canvas rather than built from DOM
 * text, so a frame has no style recalculation, text layout or paint of page content; the map marker is a
 * compositor-only transform. The chart rect is cached (resize and scroll refresh it), so a pointer frame
 * reads no layout. The percent and summary readouts under the map update at most every 100 ms while the
 * pointer moves and once more when it rests. Nothing here touches the network.
 */

export interface TelemetryCursorOptions {
  /** Element that receives the pointer, sized exactly like the chart (CSS pixels). */
  surface: HTMLElement;
  /** Marker and value-card layer over the surface; sized and scaled by the cursor. */
  canvas: HTMLCanvasElement;
  /** Map `<svg>` that also drives the cursor (optional). */
  track?: SVGSVGElement | null;
  /** Marker that follows the cursor over the map: an absolutely positioned element moved with a compositor-only transform. */
  trackMarker?: HTMLElement | null;
  /** Readouts under the map. */
  trackPercent?: HTMLElement | null;
  trackSummary?: HTMLElement | null;
  config: CursorConfig;
  /** Called after a frame in which the fraction changed (never per pointer event). */
  onFraction?: (fraction: number) => void;
}

export interface CursorDiagnostics {
  renderedFrames: number;
  framesOver25ms: number;
  maximumFrameGap: number;
  resizeCallbacks: number;
  /** Markers drawn in the last frame. */
  markersDrawn: number;
  /** Value cards drawn in the last frame. */
  cardsDrawn: number;
  /** The last card's text, for tests and the harness. */
  lastCardText: string;
  logicalTraceCount: number;
  detailedTraceCount: number;
}

export interface TelemetryCursor {
  update(config: CursorConfig): void;
  setFraction(fraction: number): void;
  readonly fraction: number;
  diagnostics(): CursorDiagnostics;
  resetDiagnostics(): void;
  dispose(): void;
}

interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

const MARKER_RADIUS = 3.4;
const READOUT_INTERVAL_MS = 100;
const CARD_PAD_X = 6;
const CARD_PAD_Y = 3;
const LAP_COLUMN = 22;
const SWATCH_WIDTH = 10;
const GAP = 4;
const FIRST_DRAW_MARK = "ri-cursor-first-draw";

export function createTelemetryCursor(options: TelemetryCursorOptions): TelemetryCursor {
  const { surface, canvas } = options;
  let config = options.config;
  let fraction = clamp(config.initialFraction, 0, 1);
  let chartInside = false;
  let trackInside = false;
  let source: "chart" | "track" | null = null;
  let clientX = 0;
  let trackClientX = 0;
  let trackClientY = 0;
  let rect = surface.getBoundingClientRect();
  let trackRect: DOMRect | null = options.track ? options.track.getBoundingClientRect() : null;
  let trackBox = readViewBox(options.track);
  let frame = 0;
  let lapOffset = 0;
  let visibleCapacity = config.tooltipCapacity;
  let rectDirty = false;
  let disposed = false;
  let dirty: Rect[] = [];
  let lastReportedFraction = Number.NaN;
  let lastPercentText = "";
  let lastSummaryText = "";
  let lastMarkerTransform = "";
  let readoutTimer = 0;
  let lastReadoutAt = 0;
  let pendingBin = 0;
  let firstDrawMarked = false;
  let characterWidth = 6.6;
  let metrics = { renderedFrames: 0, framesOver25ms: 0, maximumFrameGap: 0, resizeCallbacks: 0, markersDrawn: 0, cardsDrawn: 0, lastCardText: "" };
  let lastFrameTime: number | null = null;
  const context = canvas.getContext("2d");
  const styles = getComputedStyle(surface);
  const token = (name: string, fallback: string): string => styles.getPropertyValue(name).trim() || fallback;
  const lineColor = token("--color-text-secondary", "#D0D8DC");
  const markerOutline = token("--color-app", "#0B1015");
  const cardFill = token("--color-surface3", "#25333D");
  const cardStroke = token("--color-border-strong", "#4C6572");
  const textColor = token("--color-text-primary", "#F6F8FA");
  const mutedColor = token("--color-text-muted", "#96A6AE");
  const fontFamily = token("--font-metric", "Consolas, monospace");
  const font = `11px ${fontFamily}`;

  function readViewBox(svg: SVGSVGElement | null | undefined): { x: number; y: number; w: number; h: number } | null {
    if (!svg) return null;
    const box = svg.viewBox?.baseVal;
    return box && box.width > 0 && box.height > 0 ? { x: box.x, y: box.y, w: box.width, h: box.height } : null;
  }

  function sizeCanvas(): void {
    const { width, height } = config.metrics;
    const ratio = Math.min(2, Math.max(1, window.devicePixelRatio || 1));
    const pixelWidth = Math.round(width * ratio);
    const pixelHeight = Math.round(height * ratio);
    if (canvas.width !== pixelWidth || canvas.height !== pixelHeight) {
      canvas.width = pixelWidth;
      canvas.height = pixelHeight;
    }
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    context?.setTransform(ratio, 0, 0, ratio, 0, 0);
    if (context) {
      context.font = font;
      context.textBaseline = "middle";
      characterWidth = context.measureText("0").width || 6.6;
    }
    dirty = [];
  }

  function clearDirty(): void {
    if (!context) return;
    for (const r of dirty) context.clearRect(r.x - 1, r.y - 1, r.w + 2, r.h + 2);
    dirty = [];
  }

  function hideAll(): void {
    if (context) context.clearRect(0, 0, config.metrics.width, config.metrics.height);
    dirty = [];
  }

  function cursorActive(): boolean {
    return chartInside || trackInside;
  }

  function schedule(): void {
    if (!frame && !disposed) frame = requestAnimationFrame(render);
  }

  function summaryAt(bin: number): string {
    const a = config.aggregate;
    const speed = a.speed[bin]!;
    const throttle = a.throttle[bin]!;
    const brake = a.brake[bin]!;
    const delta = a.delta[bin]!;
    const parts: string[] = [];
    if (finite(speed)) parts.push(`${speed.toFixed(0)} mph`);
    if (finite(throttle)) parts.push(`${(throttle * 100).toFixed(0)}% throttle`);
    if (finite(brake)) parts.push(`${(brake * 100).toFixed(0)}% brake`);
    if (finite(delta)) parts.push(`${signed(delta, 3)} s`);
    return parts.length ? parts.join(" · ") : "No sample";
  }

  function writeReadouts(): void {
    readoutTimer = 0;
    lastReadoutAt = performance.now();
    if (options.trackPercent) {
      const text = `${(fraction * 100).toFixed(1)}%`;
      if (text !== lastPercentText) {
        lastPercentText = text;
        options.trackPercent.textContent = text;
      }
    }
    if (options.trackSummary) {
      const text = summaryAt(pendingBin);
      if (text !== lastSummaryText) {
        lastSummaryText = text;
        options.trackSummary.textContent = text;
      }
    }
  }

  function updateReadouts(bin: number, immediate: boolean): void {
    pendingBin = bin;
    if (immediate) {
      writeReadouts();
      return;
    }
    const elapsed = performance.now() - lastReadoutAt;
    if (elapsed >= READOUT_INTERVAL_MS) writeReadouts();
    else if (!readoutTimer) readoutTimer = window.setTimeout(writeReadouts, READOUT_INTERVAL_MS - elapsed + 4);
  }

  function updateMarker(): void {
    const marker = options.trackMarker;
    if (!marker || !options.track || !trackRect || trackRect.width < 1 || trackRect.height < 1) return;
    let x: number;
    let y: number;
    const point = mapPointAt(config.trackPoints, fraction);
    if (point) {
      x = point.x;
      y = point.y;
    } else {
      x = 28 + fraction * 364;
      y = 140;
    }
    // The viewBox is read live (wheel zoom and pan change it); the element size is cached.
    const box = readViewBox(options.track);
    if (!box) return;
    const scale = Math.min(trackRect.width / box.w, trackRect.height / box.h);
    const px = (trackRect.width - box.w * scale) / 2 + (x - box.x) * scale;
    const py = (trackRect.height - box.h * scale) / 2 + (y - box.y) * scale;
    const transform = `translate3d(${px.toFixed(1)}px,${py.toFixed(1)}px,0)`;
    if (transform !== lastMarkerTransform) {
      lastMarkerTransform = transform;
      marker.style.transform = transform;
    }
  }

  function render(timestamp: number): void {
    frame = 0;
    if (disposed) return;
    if (lastFrameTime !== null) {
      const gap = timestamp - lastFrameTime;
      if (gap > metrics.maximumFrameGap) metrics.maximumFrameGap = gap;
      if (gap > 25) metrics.framesOver25ms += 1;
    }
    lastFrameTime = timestamp;
    metrics.renderedFrames += 1;
    if (rectDirty) {
      rectDirty = false;
      rect = surface.getBoundingClientRect();
      if (options.track) {
        trackRect = options.track.getBoundingClientRect();
        trackBox = readViewBox(options.track);
      }
    }
    if (!cursorActive()) {
      hideAll();
      updateReadouts(Math.max(0, nearestIndex(config.x, fraction)), true);
      return;
    }
    const m = config.metrics;
    if (source === "track" && trackInside && options.track && trackRect && trackRect.width >= 1 && trackRect.height >= 1) {
      trackBox = readViewBox(options.track) ?? trackBox;
      if (!trackBox) return;
      // preserveAspectRatio meet: the viewBox is centred in the element with uniform scale.
      const scale = Math.min(trackRect.width / trackBox.w, trackRect.height / trackBox.h);
      const offsetX = (trackRect.width - trackBox.w * scale) / 2;
      const offsetY = (trackRect.height - trackBox.h * scale) / 2;
      const px = trackBox.x + (trackClientX - trackRect.left - offsetX) / scale;
      const py = trackBox.y + (trackClientY - trackRect.top - offsetY) / scale;
      fraction = config.trackPoints.length
        ? projectedTrackFraction(config.trackPoints, px, py, fraction)
        : clamp((px - 28) / 364, 0, 1);
    } else if (chartInside && rect.width >= 1) {
      fraction = clamp((clientX - rect.left) / rect.width * m.width - m.plotLeft, 0, m.plotWidth) / m.plotWidth;
    }
    const bin = Math.max(0, nearestIndex(config.x, fraction));
    drawChart(bin);
    updateMarker();
    updateReadouts(bin, false);
    if (options.onFraction && fraction !== lastReportedFraction) {
      lastReportedFraction = fraction;
      options.onFraction(fraction);
    }
    if (!firstDrawMarked && metrics.markersDrawn > 0) {
      firstDrawMarked = true;
      performance.mark(FIRST_DRAW_MARK);
    }
  }

  function drawChart(bin: number): void {
    if (!context) return;
    const m = config.metrics;
    const cursorX = m.plotLeft + config.x[bin]! * m.plotWidth;
    clearDirty();
    const drawn: Rect[] = [{ x: cursorX - MARKER_RADIUS - 2, y: 0, w: MARKER_RADIUS * 2 + 4, h: m.height }];
    context.strokeStyle = lineColor;
    context.globalAlpha = 0.9;
    context.lineWidth = 1;
    context.beginPath();
    context.moveTo(Math.round(cursorX) + 0.5, m.chartTop);
    context.lineTo(Math.round(cursorX) + 0.5, m.chartBottom);
    context.stroke();
    context.globalAlpha = 1;

    visibleCapacity = clamp(Math.min(config.tooltipCapacity, Math.max(1, Math.floor((m.rowHeight - 18) / TOOLTIP_ROW_HEIGHT))), 1, config.tooltipCapacity);
    const maximumOffset = Math.max(0, config.traces.length - visibleCapacity);
    lapOffset = clamp(lapOffset, 0, maximumOffset);
    const visibleCount = Math.min(visibleCapacity, config.traces.length - lapOffset);
    let markers = 0;
    let cards = 0;
    let lastText = "";
    context.lineWidth = 1.2;
    const slotTexts: string[] = [];
    for (let rowIndex = 0; rowIndex < config.rows.length; rowIndex++) {
      const row = config.rows[rowIndex]!;
      const single = row.signals.length <= 1;
      slotTexts.length = 0;
      for (let slotIndex = 0; slotIndex < visibleCount; slotIndex++) {
        const trace = config.traces[lapOffset + slotIndex]!;
        const values = trace.rowValues[rowIndex]!;
        let text = "";
        for (let s = 0; s < row.signals.length; s++) {
          const signal = row.signals[s]!;
          const value = values[s]![bin]!;
          if (!Number.isFinite(value)) continue;
          const span = Math.max(0.000001, signal.max - signal.min);
          const y = row.top + row.plotHeight - 4 - ((value - signal.min) / span) * (row.plotHeight - 8);
          context.fillStyle = trace.color;
          context.strokeStyle = markerOutline;
          context.beginPath();
          context.arc(cursorX, y, s === 0 ? MARKER_RADIUS : MARKER_RADIUS - 0.8, 0, Math.PI * 2);
          context.fill();
          if (s === 0) context.stroke();
          markers += 1;
          const piece = formatSignalValue(signal, value, !single);
          text = text ? `${text} · ${piece}` : piece;
        }
        slotTexts.push(text || "—");
      }
      if (visibleCount === 0) continue;
      const textWidth = Math.ceil(row.widestText * characterWidth);
      const width = CARD_PAD_X * 2 + LAP_COLUMN + SWATCH_WIDTH + GAP * 2 + textWidth;
      const height = CARD_PAD_Y * 2 + visibleCount * TOOLTIP_ROW_HEIGHT;
      const x = tooltipX({ cursorX, plotLeft: m.plotLeft, plotWidth: m.plotWidth, tooltipWidth: width });
      const y = row.top + 4;
      context.globalAlpha = 0.93;
      context.fillStyle = cardFill;
      context.strokeStyle = cardStroke;
      context.beginPath();
      context.roundRect(x, y, width, height, 4);
      context.fill();
      context.globalAlpha = 1;
      context.lineWidth = 1;
      context.stroke();
      context.lineWidth = 1.2;
      for (let slotIndex = 0; slotIndex < visibleCount; slotIndex++) {
        const trace = config.traces[lapOffset + slotIndex]!;
        const cy = y + CARD_PAD_Y + slotIndex * TOOLTIP_ROW_HEIGHT + TOOLTIP_ROW_HEIGHT / 2;
        context.textAlign = "right";
        context.fillStyle = mutedColor;
        context.fillText(String(trace.lap), x + CARD_PAD_X + LAP_COLUMN, cy);
        context.fillStyle = trace.color;
        context.fillRect(x + CARD_PAD_X + LAP_COLUMN + GAP, cy - 1.5, SWATCH_WIDTH, 3);
        context.textAlign = "left";
        context.fillStyle = textColor;
        context.fillText(slotTexts[slotIndex]!, x + CARD_PAD_X + LAP_COLUMN + GAP * 2 + SWATCH_WIDTH, cy);
      }
      lastText = slotTexts.join(" | ");
      drawn.push({ x, y, w: width, h: height });
      cards += 1;
    }
    dirty = drawn;
    metrics.markersDrawn = markers;
    metrics.cardsDrawn = cards;
    metrics.lastCardText = lastText;
  }

  const onEnter = (event: PointerEvent): void => {
    chartInside = true;
    source = "chart";
    rect = surface.getBoundingClientRect();
    clientX = event.clientX;
    schedule();
  };
  const onMove = (event: PointerEvent): void => {
    chartInside = true;
    source = "chart";
    clientX = event.clientX;
    schedule();
  };
  const onLeave = (): void => {
    chartInside = false;
    if (!trackInside) {
      lapOffset = 0;
      source = null;
      schedule();
    }
  };
  const onTrackEnter = (event: PointerEvent): void => {
    trackInside = true;
    source = "track";
    trackRect = options.track!.getBoundingClientRect();
    trackBox = readViewBox(options.track);
    trackClientX = event.clientX;
    trackClientY = event.clientY;
    schedule();
  };
  const onTrackMove = (event: PointerEvent): void => {
    trackInside = true;
    source = "track";
    trackClientX = event.clientX;
    trackClientY = event.clientY;
    schedule();
  };
  const onTrackLeave = (): void => {
    trackInside = false;
    if (!chartInside) {
      source = null;
      schedule();
    }
  };
  const onWheel = (event: WheelEvent): void => {
    if (Math.abs(event.deltaY) < 0.01) return;
    const maximumOffset = Math.max(0, config.traces.length - visibleCapacity);
    if (maximumOffset === 0) return;
    event.preventDefault();
    lapOffset = clamp(lapOffset + (event.deltaY > 0 ? 1 : -1), 0, maximumOffset);
    schedule();
  };
  const onKey = (event: KeyboardEvent): void => {
    const step = 1 / Math.max(1, config.bins - 1);
    let next = fraction;
    if (event.key === "ArrowRight" || event.key === "ArrowUp") next += step;
    else if (event.key === "ArrowLeft" || event.key === "ArrowDown") next -= step;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = 1;
    else return;
    event.preventDefault();
    api.setFraction(next);
  };
  const resizeObserver = typeof ResizeObserver === "function"
    ? new ResizeObserver(() => {
        metrics.resizeCallbacks += 1;
        rectDirty = true;
        if (cursorActive()) schedule();
      })
    : null;
  const onScroll = (): void => {
    rectDirty = true;
  };

  surface.addEventListener("pointerenter", onEnter);
  surface.addEventListener("pointermove", onMove, { passive: true });
  surface.addEventListener("pointerleave", onLeave);
  surface.addEventListener("wheel", onWheel, { passive: false });
  surface.addEventListener("keydown", onKey);
  options.track?.addEventListener("pointerenter", onTrackEnter);
  options.track?.addEventListener("pointermove", onTrackMove, { passive: true });
  options.track?.addEventListener("pointerleave", onTrackLeave);
  window.addEventListener("scroll", onScroll, { capture: true, passive: true });
  window.addEventListener("resize", onScroll, { passive: true });
  resizeObserver?.observe(surface);
  if (options.track) resizeObserver?.observe(options.track);

  sizeCanvas();
  updateMarker();
  updateReadouts(Math.max(0, nearestIndex(config.x, fraction)), true);

  const api: TelemetryCursor = {
    update(next) {
      config = next;
      lapOffset = clamp(lapOffset, 0, Math.max(0, config.traces.length - config.tooltipCapacity));
      rect = surface.getBoundingClientRect();
      if (options.track) {
        trackRect = options.track.getBoundingClientRect();
        trackBox = readViewBox(options.track);
      }
      sizeCanvas();
      lastMarkerTransform = "";
      lastPercentText = "";
      lastSummaryText = "";
      updateMarker();
      updateReadouts(Math.max(0, nearestIndex(config.x, fraction)), true);
      if (cursorActive()) schedule();
    },
    setFraction(value) {
      fraction = clamp(value, 0, 1);
      // Keyboard and programmatic moves behave as if the pointer were over the chart.
      chartInside = true;
      source = "chart";
      clientX = rect.left + ((config.metrics.plotLeft + fraction * config.metrics.plotWidth) / config.metrics.width) * rect.width;
      schedule();
    },
    get fraction() {
      return fraction;
    },
    diagnostics() {
      return {
        renderedFrames: metrics.renderedFrames,
        framesOver25ms: metrics.framesOver25ms,
        maximumFrameGap: metrics.maximumFrameGap,
        resizeCallbacks: metrics.resizeCallbacks,
        markersDrawn: metrics.markersDrawn,
        cardsDrawn: metrics.cardsDrawn,
        lastCardText: metrics.lastCardText,
        logicalTraceCount: config.logicalTraceCount,
        detailedTraceCount: config.traces.length,
      };
    },
    resetDiagnostics() {
      metrics = { renderedFrames: 0, framesOver25ms: 0, maximumFrameGap: 0, resizeCallbacks: 0, markersDrawn: 0, cardsDrawn: 0, lastCardText: "" };
      lastFrameTime = null;
    },
    dispose() {
      disposed = true;
      if (frame) cancelAnimationFrame(frame);
      if (readoutTimer) window.clearTimeout(readoutTimer);
      resizeObserver?.disconnect();
      surface.removeEventListener("pointerenter", onEnter);
      surface.removeEventListener("pointermove", onMove);
      surface.removeEventListener("pointerleave", onLeave);
      surface.removeEventListener("wheel", onWheel);
      surface.removeEventListener("keydown", onKey);
      options.track?.removeEventListener("pointerenter", onTrackEnter);
      options.track?.removeEventListener("pointermove", onTrackMove);
      options.track?.removeEventListener("pointerleave", onTrackLeave);
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", onScroll);
    },
  };
  return api;
}

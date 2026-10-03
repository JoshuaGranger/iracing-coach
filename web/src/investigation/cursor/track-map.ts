/**
 * Track map zoom and pan (port of analysis-track-map.js). Wheel zooms around the pointer, dragging
 * pans, the view is constrained to the base box and never closer than 1/8 of it. Cursor circles
 * marked `data-map-cursor-radius` keep their on-screen size.
 */

const MINIMUM_ZOOM_WINDOW = 1 / 8;
const clamp = (value: number, minimum: number, maximum: number): number => Math.max(minimum, Math.min(maximum, value));

export interface ViewBox {
  x: number;
  y: number;
  width: number;
  height: number;
}

export function parseViewBox(value: string | null | undefined): ViewBox | null {
  const parts = String(value ?? "").trim().split(/[\s,]+/).map(Number);
  if (parts.length !== 4 || parts.some((part) => !Number.isFinite(part)) || parts[2]! <= 0 || parts[3]! <= 0) return null;
  return { x: parts[0]!, y: parts[1]!, width: parts[2]!, height: parts[3]! };
}

/** Pure constraint step, exposed for tests. */
export function constrainView(base: ViewBox, view: ViewBox): ViewBox {
  const next = { ...view };
  next.width = clamp(next.width, base.width * MINIMUM_ZOOM_WINDOW, base.width);
  next.height = (next.width * base.height) / base.width;
  if (next.height > base.height) {
    next.height = base.height;
    next.width = (next.height * base.width) / base.height;
  }
  next.x = clamp(next.x, base.x, base.x + base.width - next.width);
  next.y = clamp(next.y, base.y, base.y + base.height - next.height);
  return next;
}

/** Zooms `view` by `factor` keeping the anchor point fixed on screen. */
export function zoomView(base: ViewBox, view: ViewBox, anchor: { x: number; y: number }, factor: number): ViewBox {
  const next = { ...view, width: view.width * factor, height: view.height * factor };
  const ratio = next.width / view.width;
  next.x = anchor.x - (anchor.x - view.x) * ratio;
  next.y = anchor.y - (anchor.y - view.y) * ratio;
  return constrainView(base, next);
}

export interface TrackMapController {
  /** Replaces the base box; keeps the zoom when the key is unchanged. */
  setBase(viewBox: string, key: string): void;
  fit(): void;
  readonly zoom: number;
  dispose(): void;
}

function svgPoint(element: SVGSVGElement, clientX: number, clientY: number): { x: number; y: number } | null {
  const matrix = element.getScreenCTM?.();
  if (matrix && typeof element.createSVGPoint === "function") {
    const point = element.createSVGPoint();
    point.x = clientX;
    point.y = clientY;
    const mapped = point.matrixTransform(matrix.inverse());
    if (Number.isFinite(mapped.x) && Number.isFinite(mapped.y)) return { x: mapped.x, y: mapped.y };
  }
  const rect = element.getBoundingClientRect();
  const box = element.viewBox?.baseVal;
  if (!rect || rect.width < 1 || rect.height < 1 || !box) return null;
  return { x: box.x + ((clientX - rect.left) / rect.width) * box.width, y: box.y + ((clientY - rect.top) / rect.height) * box.height };
}

export function createTrackMap(element: SVGSVGElement, viewBox: string, key: string): TrackMapController {
  let base = parseViewBox(viewBox) ?? { x: 0, y: 0, width: 420, height: 280 };
  let raceKey = key;
  let view: ViewBox = { ...base };
  let dragging = false;
  let pointerId: number | null = null;
  let lastX = 0;
  let lastY = 0;

  function apply(): void {
    element.setAttribute("viewBox", `${view.x.toFixed(4)} ${view.y.toFixed(4)} ${view.width.toFixed(4)} ${view.height.toFixed(4)}`);
    const zoom = base.width / view.width;
    element.dataset.mapZoom = zoom.toFixed(3);
    for (const cursor of element.querySelectorAll<SVGElement>("[data-map-cursor-radius]")) {
      const baseRadius = Number(cursor.dataset.mapCursorRadius);
      if (Number.isFinite(baseRadius) && baseRadius > 0) cursor.setAttribute("r", (baseRadius / zoom).toFixed(4));
    }
  }

  const onWheel = (event: WheelEvent): void => {
    if (Math.abs(event.deltaY) < 0.01) return;
    const anchor = svgPoint(element, event.clientX, event.clientY);
    if (!anchor) return;
    event.preventDefault();
    view = zoomView(base, view, anchor, Math.exp(clamp(event.deltaY, -120, 120) * 0.0018));
    apply();
  };
  const onDown = (event: PointerEvent): void => {
    if (event.button !== 0 || !event.isPrimary) return;
    dragging = true;
    pointerId = event.pointerId;
    lastX = event.clientX;
    lastY = event.clientY;
    element.setPointerCapture?.(event.pointerId);
    element.classList.add("map-panning");
  };
  const onMove = (event: PointerEvent): void => {
    if (!dragging || event.pointerId !== pointerId) return;
    const previous = svgPoint(element, lastX, lastY);
    const current = svgPoint(element, event.clientX, event.clientY);
    lastX = event.clientX;
    lastY = event.clientY;
    if (!previous || !current) return;
    view = constrainView(base, { ...view, x: view.x + previous.x - current.x, y: view.y + previous.y - current.y });
    apply();
  };
  const onUp = (event: PointerEvent): void => {
    if (!dragging || event.pointerId !== pointerId) return;
    dragging = false;
    pointerId = null;
    element.releasePointerCapture?.(event.pointerId);
    element.classList.remove("map-panning");
  };
  element.addEventListener("wheel", onWheel, { passive: false });
  element.addEventListener("pointerdown", onDown);
  element.addEventListener("pointermove", onMove);
  element.addEventListener("pointerup", onUp);
  element.addEventListener("pointercancel", onUp);
  apply();

  return {
    setBase(nextBox, nextKey) {
      const parsed = parseViewBox(nextBox);
      if (!parsed) return;
      const same = nextKey === raceKey;
      raceKey = nextKey;
      base = parsed;
      view = same ? constrainView(base, view) : { ...base };
      apply();
    },
    fit() {
      view = { ...base };
      apply();
    },
    get zoom() {
      return base.width / view.width;
    },
    dispose() {
      element.removeEventListener("wheel", onWheel);
      element.removeEventListener("pointerdown", onDown);
      element.removeEventListener("pointermove", onMove);
      element.removeEventListener("pointerup", onUp);
      element.removeEventListener("pointercancel", onUp);
      element.classList.remove("map-panning");
    },
  };
}

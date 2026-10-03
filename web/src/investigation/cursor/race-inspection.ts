/**
 * Synchronized inspection for the Sections view (port of race-inspection.js with object input instead
 * of `data-rows` / `data-points` attributes). One cursor moves across every channel chart, the readouts
 * and the map marker. Rows and positions come from the backend projection; nothing is calculated here.
 */

export type ChannelValues = Record<string, number | null | undefined>;

export interface InspectionRow {
  /** Lap fraction of the row. */
  x: number;
  gap?: number | null;
  a?: ChannelValues | null;
  b?: ChannelValues | null;
}

/** `[x, y, lapFraction]` of a recorded layout point. */
export type InspectionMapPoint = readonly [number, number, number];

export interface InspectionInput {
  rows: readonly InspectionRow[];
  /** Fraction at the left edge of the charts (the approach start) and at the right edge (the section end). */
  start: number;
  end: number;
  initialIndex?: number;
  mapPoints?: readonly InspectionMapPoint[];
}

export function formatReading(value: number | null | undefined, digits: number, signedFormat: boolean): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  const text = Math.abs(value).toFixed(digits);
  if (!signedFormat) return (value < 0 ? "−" : "") + text;
  return (value < 0 ? "−" : value > 0 ? "+" : "") + text;
}

/** Index of the row whose x is nearest `x`; rows ascend in x. */
export function nearestRow(rows: readonly InspectionRow[], x: number): number {
  if (rows.length === 0) return -1;
  let low = 0;
  let high = rows.length - 1;
  while (low < high) {
    const middle = (low + high) >> 1;
    if (rows[middle]!.x < x) low = middle + 1;
    else high = middle;
  }
  if (low > 0 && Math.abs(rows[low - 1]!.x - x) <= Math.abs(rows[low]!.x - x)) return low - 1;
  return low;
}

/** The recorded map point nearest to a lap fraction. */
export function nearestMapPoint(points: readonly InspectionMapPoint[], fraction: number): InspectionMapPoint | null {
  let best: InspectionMapPoint | null = null;
  let distance = Infinity;
  for (const point of points) {
    const d = Math.abs(point[2] - fraction);
    if (d < distance) { distance = d; best = point; }
  }
  return best;
}

/** The lap fraction of the recorded point nearest to a map position, or null outside the inspected span. */
export function fractionAtMapPosition(points: readonly InspectionMapPoint[], x: number, y: number, start: number, end: number): number | null {
  let best: InspectionMapPoint | null = null;
  let distance = Infinity;
  for (const point of points) {
    const d = (point[0] - x) ** 2 + (point[1] - y) ** 2;
    if (d < distance) { distance = d; best = point; }
  }
  // Only the highlighted approach and section inspect; the rest of the lap has no pair data.
  return best && best[2] >= start && best[2] <= end ? best[2] : null;
}

export interface RaceInspection {
  readonly index: number;
  setIndex(index: number): void;
  dispose(): void;
}

export function createRaceInspection(root: HTMLElement, input: InspectionInput): RaceInspection {
  const rows = input.rows;
  const span = Math.max(1e-9, input.end - input.start);
  let index = clampIndex(input.initialIndex ?? 0);
  let frame = 0;
  let pending = 0;
  const charts = Array.from(root.querySelectorAll<SVGSVGElement>("[data-ri-chart]"));
  const map = root.querySelector<SVGSVGElement>("[data-ri-map]");
  const marker = map?.querySelector<SVGElement>("[data-ri-map-cursor]") ?? null;
  const position = root.querySelector<HTMLElement>("[data-ri-position]");
  const outputs = Array.from(root.querySelectorAll<HTMLElement>("[data-ri-read]"));
  const mapPoints = input.mapPoints ?? [];
  const cursors = charts.map((chart) => ({
    left: Number(chart.getAttribute("data-left")),
    width: Number(chart.getAttribute("data-width")),
    line: chart.querySelector<SVGLineElement>("[data-ri-cursor]"),
  }));

  function clampIndex(value: number): number {
    return Math.max(0, Math.min(Math.max(0, rows.length - 1), value));
  }

  function render(): void {
    const row = rows[index];
    if (!row) return;
    const fraction = (row.x - input.start) / span;
    for (const cursor of cursors) {
      if (!cursor.line) continue;
      const x = String(cursor.left + fraction * cursor.width);
      cursor.line.setAttribute("x1", x);
      cursor.line.setAttribute("x2", x);
    }
    for (const output of outputs) {
      const key = output.getAttribute("data-ri-read") ?? "";
      const digits = Number(output.getAttribute("data-digits") ?? 1);
      const text = key === "gap"
        ? formatReading(row.gap, 3, true)
        : formatReading(((output.getAttribute("data-side") === "b" ? row.b : row.a) ?? {})[key], digits, false);
      if (output.textContent !== text) output.textContent = text;
    }
    if (position) position.textContent = `${(row.x * 100).toFixed(1)}% lap`;
    if (marker && mapPoints.length) {
      const point = nearestMapPoint(mapPoints, row.x);
      if (point) {
        marker.setAttribute("cx", String(point[0]));
        marker.setAttribute("cy", String(point[1]));
        (marker as SVGElement).style.display = "";
      }
    }
  }

  function schedule(x: number): void {
    pending = x;
    if (frame) return;
    frame = requestAnimationFrame(() => {
      frame = 0;
      index = clampIndex(nearestRow(rows, pending));
      render();
    });
  }

  function fromChart(chart: SVGSVGElement, event: PointerEvent): void {
    const box = chart.getBoundingClientRect();
    const scale = box.width / Math.max(1, Number(chart.getAttribute("data-view-width")) || box.width);
    const left = Number(chart.getAttribute("data-left")) * scale;
    const width = Number(chart.getAttribute("data-width")) * scale;
    const fraction = Math.max(0, Math.min(1, (event.clientX - box.left - left) / Math.max(1, width)));
    schedule(input.start + fraction * (input.end - input.start));
  }

  function onKey(event: KeyboardEvent): void {
    let next = index;
    if (event.key === "ArrowRight" || event.key === "ArrowUp") next += 1;
    else if (event.key === "ArrowLeft" || event.key === "ArrowDown") next -= 1;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = rows.length - 1;
    else return;
    event.preventDefault();
    index = clampIndex(next);
    render();
  }

  const removers: (() => void)[] = [];
  const listen = <K extends keyof HTMLElementEventMap>(element: Element, name: K | string, handler: (event: never) => void): void => {
    element.addEventListener(name, handler as EventListener);
    removers.push(() => element.removeEventListener(name, handler as EventListener));
  };
  for (const chart of charts) {
    listen(chart, "pointermove", (event: PointerEvent) => fromChart(chart, event));
    listen(chart, "pointerdown", (event: PointerEvent) => {
      chart.focus({ preventScroll: true });
      if (event.pointerType !== "mouse") chart.setPointerCapture(event.pointerId);
      fromChart(chart, event);
    });
    listen(chart, "keydown", onKey);
  }
  if (map && mapPoints.length) {
    listen(map, "pointermove", (event: PointerEvent) => {
      const box = map.getBoundingClientRect();
      const view = map.viewBox?.baseVal;
      if (!view || !view.width) return;
      const scale = Math.min(box.width / view.width, box.height / view.height);
      const offsetX = (box.width - view.width * scale) / 2;
      const offsetY = (box.height - view.height * scale) / 2;
      const x = (event.clientX - box.left - offsetX) / scale + view.x;
      const y = (event.clientY - box.top - offsetY) / scale + view.y;
      const fraction = fractionAtMapPosition(mapPoints, x, y, input.start, input.end);
      if (fraction !== null) schedule(fraction);
    });
    listen(map, "keydown", onKey);
  }
  render();
  return {
    get index() {
      return index;
    },
    setIndex(value) {
      index = clampIndex(value);
      render();
    },
    dispose() {
      if (frame) cancelAnimationFrame(frame);
      for (const remove of removers) remove();
    },
  };
}

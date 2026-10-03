import { render } from "preact";
import "../../gen/theme.css";
import { createAnalysisLoader } from "../load";
import type { TelemetryCursor } from "../cursor/telemetry-cursor";
import { InvestigationWorkspace } from "../ui/InvestigationWorkspace";
import type { InvestigationView } from "../types";

/**
 * G2 harness page: mounts the investigation workspace against the harness server and exposes timing
 * hooks on `window.__g2`. `perf/g2.mjs` drives it under CDP CPU throttling.
 */

interface SweepResult {
  frames: number;
  seconds: number;
  fps: number;
  framesOver25ms: number;
  maximumFrameGap: number;
  events: number;
}

interface G2Api {
  analysisId: string;
  /** performance.now() when the first trace path appeared. */
  tracesPaintedMs: number | null;
  /** performance.now() when a tooltip first became visible after a pointer move (first interactive). */
  firstInteractiveMs: number | null;
  cursor: TelemetryCursor | null;
  /** Counts animation frames for `ms` while dispatching one pointermove per frame across the chart. */
  drivenSweep(ms: number): Promise<SweepResult>;
  /** Counts animation frames for `ms` with no input of its own (a real pointer is driving). */
  probe(ms: number): Promise<SweepResult>;
  diagnostics(): unknown;
}

declare global {
  interface Window {
    __g2: G2Api;
  }
}

const params = new URLSearchParams(location.search);
const analysisId = params.get("analysis") ?? "synthetic-82-lap";
const view = (params.get("view") ?? "traces") as InvestigationView;

const api: G2Api = {
  analysisId,
  tracesPaintedMs: null,
  firstInteractiveMs: null,
  cursor: null,
  async drivenSweep(ms) {
    const surface = document.querySelector<HTMLElement>(".ri-chart-surface");
    if (!surface) throw new Error("chart surface not mounted");
    const rect = surface.getBoundingClientRect();
    const y = rect.top + rect.height / 2;
    surface.dispatchEvent(new PointerEvent("pointerenter", { clientX: rect.left + 10, clientY: y, pointerType: "mouse", isPrimary: true }));
    api.cursor?.resetDiagnostics();
    return new Promise<SweepResult>((resolve) => {
      const started = performance.now();
      let previous = started;
      let frames = 0;
      let over = 0;
      let maximum = 0;
      let events = 0;
      const step = (now: number): void => {
        frames += 1;
        const gap = now - previous;
        if (frames > 1) {
          if (gap > 25) over += 1;
          if (gap > maximum) maximum = gap;
        }
        previous = now;
        // One pointer move per frame, bouncing across the plot so every frame has new work.
        const phase = ((now - started) / 1800) % 2;
        const t = phase < 1 ? phase : 2 - phase;
        surface.dispatchEvent(new PointerEvent("pointermove", { clientX: rect.left + 8 + t * (rect.width - 16), clientY: y, pointerType: "mouse", isPrimary: true }));
        events += 1;
        if (now - started < ms) requestAnimationFrame(step);
        else {
          surface.dispatchEvent(new PointerEvent("pointerleave", { clientX: rect.left, clientY: y, pointerType: "mouse", isPrimary: true }));
          const seconds = (now - started) / 1000;
          resolve({ frames, seconds, fps: frames / seconds, framesOver25ms: over, maximumFrameGap: maximum, events });
        }
      };
      requestAnimationFrame(step);
    });
  },
  probe(ms) {
    return new Promise<SweepResult>((resolve) => {
      const started = performance.now();
      let previous = started;
      let frames = 0;
      let over = 0;
      let maximum = 0;
      const step = (now: number): void => {
        frames += 1;
        const gap = now - previous;
        if (frames > 1) {
          if (gap > 25) over += 1;
          if (gap > maximum) maximum = gap;
        }
        previous = now;
        if (now - started < ms) requestAnimationFrame(step);
        else {
          const seconds = (now - started) / 1000;
          resolve({ frames, seconds, fps: frames / seconds, framesOver25ms: over, maximumFrameGap: maximum, events: 0 });
        }
      };
      requestAnimationFrame(step);
    });
  },
  diagnostics() {
    return {
      cursor: api.cursor?.diagnostics() ?? null,
      pathNodes: document.querySelectorAll("[data-analysis-trace-path]").length,
      domNodes: document.getElementsByTagName("*").length,
    };
  },
};
window.__g2 = api;

const loader = createAnalysisLoader();
const root = document.getElementById("root")!;
render(
  <InvestigationWorkspace
    loader={loader}
    analysisId={analysisId}
    view={view}
    onCursor={(cursor) => { api.cursor = cursor; }}
  />,
  root,
);

const observer = new MutationObserver(() => {
  if (api.tracesPaintedMs === null && root.querySelector("[data-analysis-trace-path]")) api.tracesPaintedMs = performance.now();
});
observer.observe(root, { childList: true, subtree: true });
// First interactive: the first frame in which the cursor drew markers and value cards for a pointer (the cursor marks it).
new PerformanceObserver((list) => {
  for (const entry of list.getEntries()) if (entry.name === "ri-cursor-first-draw" && api.firstInteractiveMs === null && api.tracesPaintedMs !== null) api.firstInteractiveMs = entry.startTime;
}).observe({ type: "mark", buffered: true });

import type { LayoutAction } from "../layout-model";

/**
 * Drag editing for the trace layout (port of analysis-trace-layout.js). Dragging a chart title reorders
 * rows, dragging a signal from the library adds a row (between charts) or pairs it with a chart (on a
 * chart). The `dotnetRef` callbacks are one function: `onAction`, which applies the action to the layout
 * and returns whether it was accepted.
 */

const DRAG_THRESHOLD = 5;
const clamp = (value: number, minimum: number, maximum: number): number => Math.max(minimum, Math.min(maximum, value));

export interface TraceLayoutEditorOptions {
  editing: boolean;
  reducedMotion: boolean;
  rowCount: number;
  maximumRows: number;
}

export interface TraceLayoutEditor {
  configure(options: Partial<TraceLayoutEditorOptions>): void;
  dispose(): void;
}

interface Session {
  kind: "row" | "signal";
  source: HTMLElement;
  pointerId: number;
  startX: number;
  startY: number;
  rowId: string | null;
  signalId: string | null;
  name: string;
  originalIndex: number;
  active: boolean;
  valid: boolean;
  action: LayoutAction | null;
  ghost: HTMLElement | null;
  preview: HTMLElement | null;
}

interface Insertion {
  index: number;
  top: number;
  left: number;
  width: number;
}

function rowIndex(element: Element | null | undefined): number {
  const value = Number.parseInt((element as HTMLElement | null)?.dataset?.rowIndex ?? "", 10);
  return Number.isFinite(value) ? value : -1;
}

export function createTraceLayoutEditor(
  root: HTMLElement,
  onAction: (action: LayoutAction) => boolean | Promise<boolean>,
  initial: Partial<TraceLayoutEditorOptions> = {},
): TraceLayoutEditor {
  let options: TraceLayoutEditorOptions = { editing: false, reducedMotion: false, rowCount: 0, maximumRows: 10, ...initial };
  let session: Session | null = null;
  let committing = false;

  const rowTargets = (kind: string | null = null): HTMLElement[] =>
    Array.from(root.querySelectorAll<HTMLElement>("[data-analysis-row-target]"))
      .filter((element) => !kind || element.dataset.analysisRowTarget === kind)
      .sort((left, right) => rowIndex(left) - rowIndex(right));
  const chartTarget = (rowId: string | undefined): HTMLElement | null => rowTargets("chart").find((element) => element.dataset.rowId === rowId) ?? null;
  const visualRect = (target: HTMLElement): DOMRect => (chartTarget(target.dataset.rowId) ?? target).getBoundingClientRect();
  const chartBounds = (): DOMRect | null => root.querySelector("[data-analysis-trace-chart]")?.getBoundingClientRect() ?? null;

  function elementAtPointer(event: PointerEvent): HTMLElement | null {
    for (const element of document.elementsFromPoint(event.clientX, event.clientY)) {
      const target = element.closest<HTMLElement>("[data-analysis-row-target]");
      if (target && root.contains(target)) return target;
    }
    return null;
  }

  function pointerIsOverToolbox(event: PointerEvent): boolean {
    const toolbox = root.querySelector("#analysis-trace-toolbox");
    const topmost = document.elementFromPoint(event.clientX, event.clientY);
    return Boolean(toolbox && topmost && toolbox.contains(topmost));
  }

  function chartTargetAtPointer(event: PointerEvent): HTMLElement | null {
    const chart = chartBounds();
    if (!chart || event.clientX < chart.left || event.clientX > chart.right) return null;
    return rowTargets("chart").find((target) => {
      const rect = target.getBoundingClientRect();
      return event.clientY >= rect.top && event.clientY <= rect.bottom;
    }) ?? null;
  }

  function insertionAtPointer(event: PointerEvent): Insertion | null {
    const chart = chartBounds();
    const targets = rowTargets("chart");
    if (!chart || targets.length === 0 || event.clientX < chart.left || event.clientX > chart.right || event.clientY < chart.top || event.clientY > chart.bottom) return null;
    for (const target of targets) {
      const rect = target.getBoundingClientRect();
      if (event.clientY < rect.top) return { index: rowIndex(target), top: rect.top, left: chart.left, width: chart.width };
      if (event.clientY <= rect.bottom) return null;
    }
    const last = targets[targets.length - 1]!.getBoundingClientRect();
    return { index: targets.length, top: last.bottom, left: chart.left, width: chart.width };
  }

  function moveGhost(s: Session, event: PointerEvent): void {
    if (!s.ghost) return;
    s.ghost.style.left = `${Math.round(clamp(event.clientX + 14, 8, Math.max(8, window.innerWidth - s.ghost.offsetWidth - 8)))}px`;
    s.ghost.style.top = `${Math.round(clamp(event.clientY + 14, 8, Math.max(8, window.innerHeight - s.ghost.offsetHeight - 8)))}px`;
  }

  function createGhost(s: Session, event: PointerEvent): void {
    const ghost = document.createElement("div");
    ghost.className = "ri-drag-ghost";
    const grip = document.createElement("span");
    grip.textContent = "⋮⋮";
    grip.setAttribute("aria-hidden", "true");
    const label = document.createElement("strong");
    label.textContent = s.name;
    ghost.append(grip, label);
    document.body.appendChild(ghost);
    s.ghost = ghost;
    moveGhost(s, event);
  }

  function showPreview(s: Session, rect: { left: number; top: number; width: number; height: number }, message: string, valid: boolean, replacement = false, insertion = false): void {
    if (!s.preview) {
      const preview = document.createElement("div");
      preview.className = "ri-drop-preview";
      preview.appendChild(document.createElement("span"));
      document.body.appendChild(preview);
      s.preview = preview;
    }
    const preview = s.preview;
    preview.classList.toggle("invalid", !valid);
    preview.classList.toggle("replacement", valid && replacement);
    preview.classList.toggle("insertion", insertion);
    preview.style.left = `${Math.round(rect.left)}px`;
    preview.style.top = `${Math.round(rect.top)}px`;
    preview.style.width = `${Math.round(Math.max(4, rect.width))}px`;
    preview.style.height = `${Math.round(insertion ? 4 : Math.max(20, rect.height))}px`;
    preview.querySelector("span")!.textContent = message;
    s.valid = valid;
  }

  function invalidate(s: Session, message = "Move over a trace chart"): void {
    showPreview(s, s.source.getBoundingClientRect(), message, false);
    s.action = null;
  }

  function insertionPreview(s: Session, insertion: Insertion, targetIndex: number): void {
    const clamped = clamp(targetIndex, 0, options.rowCount);
    const rect = { left: insertion.left, top: insertion.top - 2, width: insertion.width, height: 4 };
    if (s.kind === "row") {
      const adjusted = clamped > s.originalIndex ? clamped - 1 : clamped;
      const finalIndex = clamp(adjusted, 0, Math.max(0, options.rowCount - 1));
      const valid = finalIndex !== s.originalIndex;
      showPreview(s, rect, valid ? `Move ${s.name} to row ${finalIndex + 1}` : "Already here", valid, false, true);
      s.action = valid ? { type: "move-row", rowId: s.rowId!, targetIndex: finalIndex } : null;
      return;
    }
    const valid = options.rowCount < options.maximumRows;
    showPreview(s, rect, valid ? `Add ${s.name} as row ${clamped + 1}` : `${options.maximumRows} chart limit`, valid, false, true);
    s.action = valid ? { type: "insert-signal", signalId: s.signalId!, targetIndex: clamped } : null;
  }

  function targetPreview(s: Session, target: HTMLElement, event: PointerEvent): void {
    const rect = visualRect(target);
    const targetIndex = rowIndex(target);
    const edge = Math.min(11, Math.max(5, rect.height * 0.12));
    const nearBottom = event.clientY >= rect.bottom - edge;
    if (event.clientY <= rect.top + edge || nearBottom) {
      insertionPreview(s, { index: targetIndex + (nearBottom ? 1 : 0), top: nearBottom ? rect.bottom : rect.top, left: rect.left, width: rect.width }, targetIndex + (nearBottom ? 1 : 0));
      return;
    }
    if (s.kind === "row") {
      const below = event.clientY >= rect.top + rect.height / 2;
      const boundary = targetIndex + (below ? 1 : 0);
      insertionPreview(s, { index: boundary, top: below ? rect.bottom : rect.top, left: rect.left, width: rect.width }, boundary);
      return;
    }
    const rowId = target.dataset.rowId!;
    const primary = target.dataset.primaryName || "this trace";
    const secondary = target.dataset.secondaryName || "";
    if (s.name === primary || s.name === secondary) {
      showPreview(s, rect, `${s.name} is already in this chart`, false);
      s.action = null;
      return;
    }
    const replacement = secondary.length > 0;
    showPreview(s, rect, replacement ? `Replace ${secondary} with ${s.name}` : `Pair ${primary} with ${s.name}`, true, replacement);
    s.action = { type: "place-signal", signalId: s.signalId!, rowId };
  }

  function updateTarget(s: Session, event: PointerEvent): void {
    if (pointerIsOverToolbox(event)) {
      invalidate(s, "Move over a visible trace chart");
      return;
    }
    const target = elementAtPointer(event) ?? chartTargetAtPointer(event);
    if (target) {
      targetPreview(s, target, event);
      return;
    }
    const insertion = insertionAtPointer(event);
    if (insertion) {
      insertionPreview(s, insertion, insertion.index);
      return;
    }
    invalidate(s);
  }

  function autoScroll(event: PointerEvent): void {
    const host = root.closest<HTMLElement>(".workspace");
    if (!host || host.scrollHeight <= host.clientHeight) return;
    const rect = host.getBoundingClientRect();
    const threshold = 54;
    const step = 22;
    let delta = 0;
    if (event.clientY < rect.top + threshold) delta = -step * (1 - clamp((event.clientY - rect.top) / threshold, 0, 1));
    else if (event.clientY > rect.bottom - threshold) delta = step * (1 - clamp((rect.bottom - event.clientY) / threshold, 0, 1));
    if (Math.abs(delta) >= 1) host.scrollTop += delta;
  }

  function removeVisuals(s: Session): void {
    root.classList.remove("ri-gesture-active");
    s.source.classList.remove("is-gesture-source");
    s.ghost?.remove();
    s.preview?.remove();
    try { s.source.releasePointerCapture(s.pointerId); } catch { /* the capture may already be gone */ }
  }

  function structuralMotionMs(): number {
    const configured = Number.parseFloat(getComputedStyle(root).getPropertyValue("--motion-structure"));
    return Number.isFinite(configured) ? Math.max(0, configured) : 500;
  }

  function captureRects(): Map<string, DOMRect> {
    const captured = new Map<string, DOMRect>();
    for (const element of root.querySelectorAll<HTMLElement>("[data-analysis-render-row], .trace-row-label-shell")) {
      captured.set(`${element.hasAttribute("data-analysis-render-row") ? "chart" : "label"}:${element.dataset.rowId}`, element.getBoundingClientRect());
    }
    return captured;
  }

  const motionReduced = (): boolean => options.reducedMotion || window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function animateReflow(before: Map<string, DOMRect>): void {
    if (motionReduced()) return;
    const duration = structuralMotionMs();
    for (const element of root.querySelectorAll<HTMLElement>("[data-analysis-render-row], .trace-row-label-shell")) {
      const prior = before.get(`${element.hasAttribute("data-analysis-render-row") ? "chart" : "label"}:${element.dataset.rowId}`);
      if (!prior) continue;
      const deltaY = prior.top - element.getBoundingClientRect().top;
      if (Math.abs(deltaY) < 0.5) continue;
      element.animate([{ transform: `translateY(${deltaY}px)` }, { transform: "none" }], { duration, easing: "cubic-bezier(.2,0,0,1)" });
    }
  }

  async function complete(event: PointerEvent, cancelled: boolean): Promise<void> {
    const s = session;
    if (!s || (event.pointerId !== undefined && event.pointerId !== s.pointerId)) return;
    if (!cancelled && s.active && Number.isFinite(event.clientX) && Number.isFinite(event.clientY)) updateTarget(s, event);
    session = null;
    if (cancelled || !s.active || !s.valid || !s.action) {
      removeVisuals(s);
      return;
    }
    committing = true;
    const before = captureRects();
    s.preview?.classList.add("committing");
    let succeeded = false;
    try {
      succeeded = await onAction(s.action);
    } catch {
      succeeded = false;
    }
    committing = false;
    removeVisuals(s);
    requestAnimationFrame(() => requestAnimationFrame(() => {
      if (succeeded) animateReflow(before);
      else {
        const chart = root.querySelector<HTMLElement>("[data-analysis-trace-chart]");
        if (chart && !motionReduced()) chart.animate([{ transform: "translateX(0)" }, { transform: "translateX(-3px)" }, { transform: "translateX(3px)" }, { transform: "translateX(0)" }], { duration: 160 });
      }
    }));
  }

  function cancel(): void {
    const s = session;
    if (!s) return;
    session = null;
    removeVisuals(s);
  }

  const onDown = (event: PointerEvent): void => {
    if (!options.editing || event.button !== 0 || session || committing) return;
    const target = event.target as Element | null;
    if (target?.closest("[data-analysis-no-drag]")) return;
    const rowSource = target?.closest<HTMLElement>("[data-analysis-drag-row]") ?? null;
    const signalSource = target?.closest<HTMLElement>("[data-analysis-drag-signal]") ?? null;
    const source = rowSource ?? signalSource;
    if (!source || !root.contains(source)) return;
    event.stopPropagation();
    try { source.setPointerCapture(event.pointerId); } catch { /* a synthetic pointer has nothing to capture */ }
    session = {
      kind: rowSource ? "row" : "signal",
      source,
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      rowId: rowSource?.dataset.rowId ?? rowSource?.dataset.analysisDragRow ?? null,
      signalId: signalSource?.dataset.signalId ?? signalSource?.dataset.analysisDragSignal ?? null,
      name: rowSource?.dataset.rowName ?? signalSource?.dataset.signalName ?? "trace",
      originalIndex: rowSource ? rowIndex(root.querySelector(`[data-analysis-row-target="label"][data-row-id="${rowSource.dataset.rowId}"]`)) : -1,
      active: false,
      valid: false,
      action: null,
      ghost: null,
      preview: null,
    };
  };
  const onMove = (event: PointerEvent): void => {
    const s = session;
    if (!s || event.pointerId !== s.pointerId) return;
    if (!s.active && Math.hypot(event.clientX - s.startX, event.clientY - s.startY) < DRAG_THRESHOLD) return;
    if (!s.active) {
      s.active = true;
      root.classList.add("ri-gesture-active");
      s.source.classList.add("is-gesture-source");
      createGhost(s, event);
    }
    event.preventDefault();
    autoScroll(event);
    moveGhost(s, event);
    updateTarget(s, event);
  };
  const onUp = (event: PointerEvent): void => void complete(event, false);
  const onCancel = (event: PointerEvent): void => void complete(event, true);
  const onLost = (event: PointerEvent): void => {
    if (session && event.pointerId === session.pointerId) void complete(event, true);
  };
  const onBlur = (): void => cancel();
  const onKey = (event: KeyboardEvent): void => {
    if (event.key === "Escape" && session) {
      event.preventDefault();
      event.stopPropagation();
      cancel();
    }
  };

  root.addEventListener("pointerdown", onDown, true);
  root.addEventListener("pointermove", onMove, true);
  root.addEventListener("pointerup", onUp, true);
  root.addEventListener("pointercancel", onCancel, true);
  root.addEventListener("lostpointercapture", onLost, true);
  root.addEventListener("keydown", onKey, true);
  window.addEventListener("blur", onBlur);

  return {
    configure(next) {
      options = { ...options, ...next };
      if (!options.editing) cancel();
    },
    dispose() {
      cancel();
      root.removeEventListener("pointerdown", onDown, true);
      root.removeEventListener("pointermove", onMove, true);
      root.removeEventListener("pointerup", onUp, true);
      root.removeEventListener("pointercancel", onCancel, true);
      root.removeEventListener("lostpointercapture", onLost, true);
      root.removeEventListener("keydown", onKey, true);
      window.removeEventListener("blur", onBlur);
    },
  };
}

/** Frame-gap probe for structural motion (benchmarkStructuralMotion): toggles `open` classes and samples rAF gaps. */
export interface StructuralMotionResult {
  cycles: number;
  transitions: number;
  duration: number;
  sampledFrames: number;
  framesOver25ms: number;
  maximumFrameGap: number;
  p95FrameGap: number;
  minimumWidth: number;
  maximumWidth: number;
  layoutShiftScore: number;
}

export async function benchmarkStructuralMotion(root: HTMLElement, toggle: (open: boolean) => void, cycles = 50): Promise<StructuralMotionResult> {
  const count = clamp(Math.round(Number(cycles) || 50), 1, 50);
  const duration = Number.parseFloat(getComputedStyle(root).getPropertyValue("--motion-structure"));
  const motion = Number.isFinite(duration) ? Math.max(0, duration) : 500;
  const gaps: number[] = [];
  const widths: number[] = [];
  let shift = 0;
  const observer = typeof PerformanceObserver === "function"
    ? new PerformanceObserver((list) => {
        for (const entry of list.getEntries() as unknown as { hadRecentInput: boolean; value: number }[]) if (!entry.hadRecentInput) shift += entry.value || 0;
      })
    : null;
  try { observer?.observe({ type: "layout-shift", buffered: false }); } catch { /* unsupported in this browser */ }
  const sample = (length: number): Promise<void> => new Promise((resolve) => {
    const started = performance.now();
    let prior = started;
    const step = (timestamp: number): void => {
      gaps.push(timestamp - prior);
      prior = timestamp;
      widths.push(root.getBoundingClientRect().width);
      if (timestamp - started < length) requestAnimationFrame(step);
      else resolve();
    };
    requestAnimationFrame(step);
  });
  try {
    for (let cycle = 0; cycle < count; cycle++) {
      for (const open of [true, false]) {
        toggle(open);
        await sample(motion + 34);
      }
    }
  } finally {
    observer?.disconnect();
  }
  const sorted = gaps.slice().sort((a, b) => a - b);
  return {
    cycles: count,
    transitions: count * 2,
    duration: motion,
    sampledFrames: gaps.length,
    framesOver25ms: gaps.filter((gap) => gap > 25).length,
    maximumFrameGap: sorted[sorted.length - 1] ?? 0,
    p95FrameGap: sorted.length ? sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * 0.95))]! : 0,
    minimumWidth: widths.length ? Math.min(...widths) : 0,
    maximumWidth: widths.length ? Math.max(...widths) : 0,
    layoutShiftScore: shift,
  };
}

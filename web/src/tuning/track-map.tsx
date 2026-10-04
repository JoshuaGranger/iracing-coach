import { useMemo, useRef } from "preact/hooks";
import type { Schemas } from "../core/api";
import { useElementSize } from "../core/element-size";
import { scaleFor, useRemPx } from "../core/unit";
import { theme } from "../gen/theme";
import type { FeedbackItem, MapTurn, RunPhase } from "./model";
import { type MapPoint, nearestPct, pathData, pointAt, segment, shortLabel, viewBox } from "./map";

type Map = Schemas["TuningMapView"];

function feedbackClass(items: readonly FeedbackItem[]): string {
  if (items.length === 0) return "";
  const primary = items.filter((i) => i.symptom_id === "tight" || i.symptom_id === "good" || i.symptom_id === "loose");
  const tight = primary.some((i) => i.symptom_id === "tight");
  const loose = primary.some((i) => i.symptom_id === "loose");
  const good = primary.some((i) => i.symptom_id === "good");
  const kind = tight && loose ? "mixed" : tight ? "tight" : loose ? "loose" : good ? "comfortable" : "detail";
  return `fb-${kind} fb-level-${Math.min(5, Math.max(1, ...items.map((i) => i.severity)))}`;
}

export function turnDetail(turn: MapTurn, items: readonly FeedbackItem[]): string {
  if (items.length === 0) return turn.user_verified ? "Verified" : turn.is_official ? "Official · unverified" : "Unverified";
  const names: Record<string, string> = { good: "comfortable", tight: "tight", loose: "loose" };
  const stages: Record<string, string> = { entry: "Entry", center: "Center", exit: "Exit", whole: "Whole" };
  const parts = [
    ...new Set(
      items
        .filter((i) => names[i.symptom_id])
        .flatMap((i) => (i.corner_phases.length ? i.corner_phases : ["whole"]).map((p) => `${stages[p] ?? p} ${names[i.symptom_id]}`)),
    ),
  ].slice(0, 2);
  return parts.length ? parts.join(" · ") : `${items.length} ${items.length === 1 ? "detail" : "details"}`;
}

export interface TrackMapProps {
  map: Map;
  turns: readonly MapTurn[];
  selectedId: string | null;
  feedback: readonly FeedbackItem[];
  phase: RunPhase;
  priorityId: string | null;
  /** Correction mode: handle positions (lap fractions) and the click handler that sets the active one. */
  handles?: { entry: number; apex: number; exit: number; active: "entry" | "apex" | "exit" } | null;
  onPick(turnId: string): void;
  onSetPct?(pct: number): void;
}

export function TrackMap(p: TrackMapProps) {
  const points = p.map.path as MapPoint[];
  const vb = useMemo(() => viewBox(points), [points]);
  const svg = useRef<SVGSVGElement>(null);
  const size = useElementSize(svg);
  const k = scaleFor(useRemPx());
  // Turn numbers are at least the theme's data size on screen whatever the map's scale (a phone shows the map small); the circle grows with them.
  const pxPerUnit = size.width > 0 && size.height > 0 ? Math.min(size.width / vb.w, size.height / vb.h) : 0;
  const r = Math.max(Math.max(vb.w, vb.h) * 0.032, pxPerUnit > 0 ? (theme.typography.dataPx * k) / (1.05 * pxPerUnit) : 0);
  const main = useMemo(() => pathData(points, true), [points]);
  if (points.length < 2) {
    return (
      <div class="map-empty">
        <strong>No track shape</strong>
      </div>
    );
  }
  const onSvgClick = (e: MouseEvent) => {
    if (!p.handles || !p.onSetPct || !svg.current) return;
    const m = svg.current.getScreenCTM();
    if (!m) return;
    const pt = svg.current.createSVGPoint();
    pt.x = e.clientX;
    pt.y = e.clientY;
    const local = pt.matrixTransform(m.inverse());
    const pct = nearestPct(points, local.x, local.y);
    if (pct !== null) p.onSetPct(pct);
  };
  return (
    <svg
      ref={svg}
      class={`track-map${p.handles ? " correcting" : ""}`}
      viewBox={`${vb.x} ${vb.y} ${vb.w} ${vb.h}`}
      preserveAspectRatio="xMidYMid meet"
      role="img"
      aria-label="Track map with selectable turns"
      onClick={onSvgClick}
    >
      <path d={main} class="map-halo" />
      <path d={main} class="map-track" />
      {p.turns.map((t) => {
        const seg = pathData(segment(points, t.start_pct, t.end_pct));
        const mk = pointAt(points, t.apex_pct);
        if (!seg || !mk) return null;
        const items = p.feedback.filter((f) => f.corner_id === t.corner_id && f.run_phase === p.phase);
        const cls = [
          t.corner_id === p.selectedId ? "selected" : "",
          !p.map.is_verified && !t.user_verified ? "unverified" : "",
          t.corner_id === p.priorityId ? "priority" : "",
          feedbackClass(items),
        ]
          .filter(Boolean)
          .join(" ");
        return (
          <g key={t.corner_id} class={`map-turn ${cls}`}>
            <path d={seg} class="turn-seg" />
            <path
              d={seg}
              class="turn-hit"
              role="button"
              tabIndex={0}
              aria-label={`${t.label}, ${Math.round(t.start_pct * 100)} to ${Math.round(t.end_pct * 100)} percent, ${turnDetail(t, items).toLowerCase()}`}
              aria-pressed={t.corner_id === p.selectedId}
              onClick={(e) => {
                if (p.handles) return;
                e.stopPropagation();
                p.onPick(t.corner_id);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  p.onPick(t.corner_id);
                }
              }}
            />
            <circle cx={mk.x} cy={mk.y} r={r} class="turn-dot" />
            <text x={mk.x} y={mk.y + r * 0.36} font-size={r * 1.05} text-anchor="middle" class="turn-text">
              {shortLabel(t.label, t.is_official)}
            </text>
          </g>
        );
      })}
      {p.handles
        ? (["entry", "apex", "exit"] as const).map((k) => {
            const at = pointAt(points, p.handles![k]);
            return at ? (
              <g key={k} class={`map-handle${p.handles!.active === k ? " active" : ""}`}>
                <circle cx={at.x} cy={at.y} r={r * 0.9} />
                <text x={at.x} y={at.y + r * 0.32} font-size={r} text-anchor="middle">
                  {k === "entry" ? "E" : k === "apex" ? "A" : "X"}
                </text>
              </g>
            ) : null;
          })
        : null}
    </svg>
  );
}

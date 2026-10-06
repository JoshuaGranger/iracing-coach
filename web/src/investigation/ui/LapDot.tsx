import { n } from "../format";
import type { LapKind, TrafficSide } from "./traffic";

/**
 * One lap's mark on the Race and Runs charts, in the run's colour (backlog #67):
 * - open air: the plain dot;
 * - screened for something other than traffic: the hollow dot;
 * - screened for close traffic alone: a double ring, the dot's own ring standing off a smaller one inside it; when the backend names
 *   the side the car was on, the plain dot with a chevron instead, on the leading (right, ahead) or trailing (left, behind) side.
 * The focusable, hoverable element is always the one `.ri-dot` carrying `data-lap`; rings and the chevron are decoration.
 */

/** How far the traffic ring's outer circle stands off the plain dot's radius, and the inner one's inset, in chart px. */
export const TRAFFIC_OUT = 1.7;
export const TRAFFIC_IN = 0.9;

/** The mark's outer radius: the fastest-lap ring clears it. */
export function markRadius(kind: LapKind, side: TrafficSide | null, r: number): number {
  return kind === "traffic" && (side === null || side === "both") ? r + TRAFFIC_OUT : r;
}

export interface LapDotProps {
  lap: number;
  /** The run, for the Runs chart's `data-run` (an empty string outside every run). */
  run?: number | null;
  kind: LapKind;
  side?: TrafficSide | null;
  cx: number;
  cy: number;
  /** The plain dot's radius. */
  r: number;
  colour: string;
  /** The lap's readout: the tooltip and the accessible name. */
  label: string;
  onSelect(): void;
}

export function LapDot({ lap, run, kind, side = null, cx, cy, r, colour, label, onSelect }: LapDotProps) {
  const x = n(cx);
  const y = n(cy);
  const runAttr = run === undefined ? undefined : run ?? "";
  if (kind === "traffic" && (side === null || side === "both")) {
    return (
      <>
        <circle cx={x} cy={y} r={n(r + TRAFFIC_OUT)} fill="var(--ri-card)" stroke={colour} class="ri-dot traffic" tabIndex={0} data-lap={lap} data-run={runAttr} data-mark="traffic"
          onPointerEnter={onSelect} onFocus={onSelect} onClick={onSelect} aria-label={label}><title>{label}</title></circle>
        <circle cx={x} cy={y} r={n(Math.max(1.1, r - TRAFFIC_IN))} fill="none" stroke={colour} class="ri-dot-ring" data-traffic-ring={lap} aria-hidden="true" />
      </>
    );
  }
  const dot = (
    <circle cx={x} cy={y} r={n(r)} fill={kind === "screened" ? "var(--ri-card)" : colour} stroke={colour} class={`ri-dot${kind === "screened" ? " excluded" : ""}`} tabIndex={0} data-lap={lap} data-run={runAttr} data-mark={kind}
      data-traffic-side={kind === "traffic" ? side ?? undefined : undefined}
      onPointerEnter={onSelect} onFocus={onSelect} onClick={onSelect} aria-label={label}><title>{label}</title></circle>
  );
  if (kind !== "traffic" || side === null || side === "both") return dot;
  const lead = side === "ahead" ? 1 : -1;
  const near = cx + lead * (r + 1.2);
  const far = cx + lead * (r + 3.4);
  return (
    <>
      {dot}
      <path d={`M${n(near)},${n(cy - 2.1)}L${n(far)},${n(cy)}L${n(near)},${n(cy + 2.1)}`} stroke={colour} class="ri-dot-chevron" data-traffic-chevron={lap} aria-hidden="true" />
    </>
  );
}

import { n } from "../format";
import type { LapKind, TrafficSide } from "./traffic";

/**
 * One lap's mark on the Race and Runs charts, in the run's colour (backlog #67, quieter in #92):
 * - open air: the solid dot;
 * - screened for close traffic alone: the same solid dot at reduced opacity (`TRAFFIC_OPACITY`) with a thin outline in the run's
 *   colour; the side and the share stay in the readout, never on the mark;
 * - screened for anything else: the hollow dot.
 * Every kind is the one `.ri-dot` circle carrying `data-lap`, at the same radius, so it is the focusable, hoverable element.
 */

/** The fill opacity of a traffic lap's dot. */
export const TRAFFIC_OPACITY = 0.55;

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
  const runAttr = run === undefined ? undefined : run ?? "";
  const cls = kind === "screened" ? "ri-dot excluded" : kind === "traffic" ? "ri-dot traffic" : "ri-dot";
  return (
    <circle cx={n(cx)} cy={n(cy)} r={n(r)} fill={kind === "screened" ? "var(--ri-card)" : colour} fill-opacity={kind === "traffic" ? TRAFFIC_OPACITY : undefined} stroke={colour} class={cls}
      tabIndex={0} data-lap={lap} data-run={runAttr} data-mark={kind} data-traffic-side={kind === "traffic" ? side ?? undefined : undefined}
      onPointerEnter={onSelect} onFocus={onSelect} onClick={onSelect} aria-label={label}><title>{label}</title></circle>
  );
}

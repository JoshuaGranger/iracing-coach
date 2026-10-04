import { useEffect, useRef } from "preact/hooks";
import { CHANNEL_INDEX, type TileSpec } from "../gen/live-channels";
import { FrameFlag, type LiveFrame } from "./frame";
import { LiveHistory, TREND_WINDOW_SPECS, downsample } from "./history";
import type { LiveTileCfg } from "./layout";
import { type TileText, displayRange, formatTile, secondaryLine, tileRange, tileTrend } from "./tiles";

const ACCENT_VAR: Record<string, string> = {
  default: "var(--color-interaction)",
  blue: "var(--race-pit)",
  green: "var(--race-green)",
  amber: "var(--race-fuel)",
  coral: "var(--race-loss)",
  violet: "var(--telemetry-steering)",
};

const METRIC_COLOR: Record<string, string> = {
  speed: "var(--telemetry-speed)",
  throttle: "var(--telemetry-throttle)",
  brake: "var(--telemetry-brake)",
  steering: "var(--telemetry-steering)",
  "steering-wheel-angle": "var(--telemetry-steering)",
  fuel: "var(--telemetry-fuel)",
};

export function tileColor(cfg: LiveTileCfg): string {
  if (cfg.accent && cfg.accent !== "default") return ACCENT_VAR[cfg.accent]!;
  return METRIC_COLOR[cfg.metric] ?? ACCENT_VAR.default!;
}

function resolveColor(el: HTMLElement, value: string): string {
  const m = /^var\((--[\w-]+)\)$/.exec(value);
  return m ? getComputedStyle(el).getPropertyValue(m[1]!).trim() || "#EAF2F6" : value;
}

/**
 * Trend: min/max per pixel column over the tile's window, drawn on a canvas; redrawn only when a frame arrived. The line is the
 * tile's own value (display unit, flag step, phase, pace midpoint, cue priority). It breaks where samples are missing or time
 * jumps. A "1 lap" or "3 laps" window is a rolling lap distance drawn on a lap-distance axis. Brake with Mark ABS draws the
 * stretches where ABS was active over the line.
 */
export function TrendCanvas({ cfg, spec, history, frame, tick }: { cfg: LiveTileCfg; spec: TileSpec; history: LiveHistory; frame: LiveFrame | null; tick: number }) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const dpr = Math.min(2, globalThis.devicePixelRatio || 1);
    const w = Math.max(1, Math.round(canvas.clientWidth * dpr));
    const h = Math.max(1, Math.round(canvas.clientHeight * dpr));
    if (canvas.width !== w || canvas.height !== h) {
      canvas.width = w;
      canvas.height = h;
    }
    const g = canvas.getContext("2d");
    if (!g) return;
    g.clearRect(0, 0, w, h);
    const newest = history.latestTime();
    // No fresh frame: the tile reads Waiting and the old line goes with it.
    if (spec.channels.length === 0 || newest === null || !frame || (frame.flags & FrameFlag.SimConnected) === 0) return;
    const win = TREND_WINDOW_SPECS[cfg.trend_window ?? "s30"] ?? { seconds: 30 };
    const series = tileTrend(history, spec, cfg, win);
    if (series.t.length === 0) return;
    const x1 = series.x[series.x.length - 1]!;
    const span = series.axis === "distance" ? (win.laps ?? 1) : (win.seconds ?? (win.laps ?? 1) * 120);
    const x0 = x1 - span;
    const cols = downsample(series, x0, Math.max(x1, x0 + 1e-6), w);
    if (cols.length === 0) return;
    const text = formatTile(spec, cfg, frame);
    const range = displayRange(spec, text.value ?? series.v[series.v.length - 1]!, tileRange(spec, frame, cfg), series.v);
    let lo = range?.min ?? Math.min(...cols.map((c) => c.min));
    let hi = range?.max ?? Math.max(...cols.map((c) => c.max));
    if (hi <= lo) hi = lo + 1;
    const y = (v: number) => h - 2 - ((v - lo) / (hi - lo)) * (h - 4);
    g.strokeStyle = resolveColor(canvas, tileColor(cfg));
    g.lineWidth = Math.max(1, dpr);
    g.beginPath();
    let started = false;
    let prevY = 0;
    for (const c of cols) {
      const x = c.x + 0.5;
      if (!started || c.gap) {
        g.moveTo(x, y(c.max));
        started = true;
      } else if (spec.step) {
        g.lineTo(x, prevY);
      }
      g.lineTo(x, y(c.max));
      if (c.min !== c.max) g.lineTo(x, y(c.min));
      prevY = y(c.min !== c.max ? c.min : c.max);
    }
    g.stroke();
    if (cfg.highlight_abs && spec.id === "brake" && history.hasChannel("abs_active")) {
      const abs = history.series("abs_active", win);
      g.strokeStyle = resolveColor(canvas, "var(--race-loss)");
      g.lineWidth = Math.max(2, 2 * dpr);
      g.beginPath();
      let open = false;
      for (let i = 0; i < series.v.length && i < abs.v.length; i++) {
        const on = abs.v[i] === 1 && !Number.isNaN(series.v[i]!);
        const px = ((series.x[i]! - x0) / (x1 - x0 || 1)) * w;
        if (on) {
          if (!open) g.moveTo(px, y(series.v[i]!));
          else g.lineTo(px, y(series.v[i]!));
          open = true;
        } else open = false;
      }
      g.stroke();
    }
  }, [tick, cfg, spec, history, frame]);
  return <canvas ref={ref} class="trend-canvas" aria-hidden="true" />;
}

function Gauge({ value, min, max }: { value: number | null; min: number; max: number }) {
  const frac = value === null || max <= min ? 0 : Math.min(0.9999, Math.max(0, (value - min) / (max - min)));
  const r = 40;
  const circ = 2 * Math.PI * r;
  const arc = circ * 0.75;
  return (
    <svg class="gauge" viewBox="0 0 100 100" aria-hidden="true">
      <circle cx="50" cy="50" r={r} fill="none" stroke="var(--color-chart-grid)" stroke-width="7" stroke-dasharray={`${arc} ${circ}`} transform="rotate(135 50 50)" stroke-linecap="round" />
      <circle cx="50" cy="50" r={r} fill="none" stroke="currentColor" stroke-width="7" stroke-dasharray={`${arc * frac} ${circ}`} transform="rotate(135 50 50)" stroke-linecap="round" />
    </svg>
  );
}

export interface TileViewProps {
  cfg: LiveTileCfg;
  spec: TileSpec;
  frame: LiveFrame | null;
  history: LiveHistory;
  tick: number;
}

export function TileView({ cfg, spec, frame, history, tick }: TileViewProps) {
  const text: TileText = formatTile(spec, { unit: cfg.unit, precision: cfg.precision }, frame);
  const style = cfg.style;
  const line = secondaryLine(spec, text);
  // Gauges and bars take their range from the tile's trend window, so it follows the data and shrinks after a spike.
  let range: { min: number; max: number } | null = null;
  if ((style === "gauge" || style === "bar") && text.value !== null) {
    const win = TREND_WINDOW_SPECS[cfg.trend_window ?? "s30"] ?? { seconds: 30 };
    range = displayRange(spec, text.value, tileRange(spec, frame, cfg), tileTrend(history, spec, cfg, win).v);
  }
  const absHot = cfg.highlight_abs && frame && frame.values[CHANNEL_INDEX.abs_active!] === 1;
  return (
    <div class={`tile style-${style} tone-${text.tone} state-${text.state}${absHot ? " abs" : ""}`} style={{ color: style === "gauge" || style === "bar" ? tileColor(cfg) : undefined }} data-metric={cfg.metric}>
      <div class="tile-label">{spec.label}</div>
      {style === "status" ? (
        <div class="tile-status">{text.primary}</div>
      ) : (
        <div class="tile-body">
          {style === "gauge" ? <Gauge value={text.value} min={range?.min ?? 0} max={range?.max ?? 1} /> : null}
          <div class="tile-value">
            <span class="tile-primary">{text.amount}</span>
            {text.unit ? <span class="tile-unit">{text.unit}</span> : null}
          </div>
          {style === "bar" ? (
            <div class="tile-bar" aria-hidden="true">
              <i style={{ width: `${text.value === null || range === null || range.max <= range.min ? 0 : Math.min(100, Math.max(0, ((text.value - range.min) / (range.max - range.min)) * 100))}%` }} />
            </div>
          ) : null}
          {style === "trend" ? <TrendCanvas cfg={cfg} spec={spec} history={history} frame={frame} tick={tick} /> : null}
        </div>
      )}
      {line ? <div class="tile-secondary">{line}</div> : null}
    </div>
  );
}

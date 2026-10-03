import { useEffect, useRef } from "preact/hooks";
import { CHANNEL_INDEX, type TileSpec } from "../gen/live-channels";
import type { LiveFrame } from "./frame";
import { LiveHistory, TREND_WINDOW_SPECS, downsample } from "./history";
import type { LiveTileCfg } from "./layout";
import { type TileText, formatTile, tileRange, unitConverter } from "./tiles";

const ACCENT_VAR: Record<string, string> = {
  default: "var(--color-accent)",
  blue: "var(--color-info)",
  green: "var(--color-success)",
  amber: "var(--color-warning)",
  coral: "var(--color-danger)",
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
  return m ? getComputedStyle(el).getPropertyValue(m[1]!).trim() || "#5CE8C3" : value;
}

/** Trend: min/max per pixel column over the tile's window, drawn on a canvas; redrawn only when a frame arrived. */
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
    const channel = spec.channels[0];
    const newest = history.latestTime();
    if (!channel || newest === null) return;
    const win = TREND_WINDOW_SPECS[cfg.trend_window ?? "s30"] ?? { seconds: 30 };
    const series = history.series(channel, win);
    if (series.t.length === 0) return;
    const conv = unitConverter(channel, cfg.unit ?? spec.units[0]);
    for (let i = 0; i < series.v.length; i++) series.v[i] = conv.fn(series.v[i]!);
    const t1 = series.t[series.t.length - 1]!;
    const t0 = win.seconds !== undefined ? t1 - win.seconds : series.t[0]!;
    const cols = downsample(series, t0, Math.max(t1, t0 + 1e-6), w);
    if (cols.length === 0) return;
    const range = tileRange(spec, frame, { unit: cfg.unit });
    let lo = range.min ?? Math.min(...cols.map((c) => c.min));
    let hi = range.max ?? Math.max(...cols.map((c) => c.max));
    if (range.min === null || range.max === null) {
      const pad = (hi - lo || 1) * 0.08;
      if (range.min === null) lo -= pad;
      if (range.max === null) hi += pad;
    }
    if (hi <= lo) hi = lo + 1;
    const y = (v: number) => h - 2 - ((v - lo) / (hi - lo)) * (h - 4);
    g.strokeStyle = resolveColor(canvas, tileColor(cfg));
    g.lineWidth = Math.max(1, dpr);
    g.beginPath();
    let started = false;
    let prevY = 0;
    for (const c of cols) {
      const x = c.x + 0.5;
      if (!started) {
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
  /** Observed range kept by the page so bars and gauges only widen between resets. */
  observed: Map<string, { min: number; max: number }>;
}

export function TileView({ cfg, spec, frame, history, tick, observed }: TileViewProps) {
  const text: TileText = formatTile(spec, { unit: cfg.unit, precision: cfg.precision }, frame);
  const semantic = tileRange(spec, frame, { unit: cfg.unit });
  let { min, max } = semantic;
  if (text.value !== null && (min === null || max === null)) {
    const o = observed.get(cfg.tile_id) ?? { min: text.value, max: text.value };
    o.min = Math.min(o.min, text.value);
    o.max = Math.max(o.max, text.value);
    observed.set(cfg.tile_id, o);
    const pad = (o.max - o.min || 1) * 0.08;
    if (min === null) min = o.min - pad;
    if (max === null) max = o.max + pad;
  }
  const style = cfg.style;
  const absHot = cfg.highlight_abs && frame && frame.values[CHANNEL_INDEX.abs_active!] === 1;
  return (
    <div class={`tile style-${style} tone-${text.tone} state-${text.state}${absHot ? " abs" : ""}`} style={{ color: style === "gauge" || style === "bar" ? tileColor(cfg) : undefined }} data-metric={cfg.metric}>
      <div class="tile-label">{spec.label}</div>
      {style === "status" ? (
        <div class="tile-status">{text.primary}</div>
      ) : (
        <div class="tile-body">
          {style === "gauge" ? <Gauge value={text.value} min={min ?? 0} max={max ?? 1} /> : null}
          <div class="tile-value">
            <span class="tile-primary">{text.primary}</span>
            {text.unit && text.state === "ok" ? <span class="tile-unit">{text.unit}</span> : null}
          </div>
          {style === "bar" ? (
            <div class="tile-bar" aria-hidden="true">
              <i style={{ width: `${text.value === null || max === null || min === null || max <= min ? 0 : Math.min(100, Math.max(0, ((text.value - min) / (max - min)) * 100))}%` }} />
            </div>
          ) : null}
          {style === "trend" ? <TrendCanvas cfg={cfg} spec={spec} history={history} frame={frame} tick={tick} /> : null}
        </div>
      )}
      {text.secondary ? <div class="tile-secondary">{text.secondary}</div> : null}
    </div>
  );
}

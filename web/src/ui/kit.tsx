import type { ComponentChildren } from "preact";

export type Tone = "neutral" | "good" | "warn" | "bad" | "info";

export function Chip({ tone = "neutral", title, children }: { tone?: Tone; title?: string; children: ComponentChildren }) {
  return (
    <span class={`chip chip-${tone}`} title={title}>
      {children}
    </span>
  );
}

export function Notice({ tone = "neutral", children, role }: { tone?: Tone; children: ComponentChildren; role?: "status" | "alert" }) {
  return (
    <p class={`notice notice-${tone}`} role={role ?? (tone === "bad" ? "alert" : "status")}>
      {children}
    </p>
  );
}

export function Empty({ title, children }: { title: string; children?: ComponentChildren }) {
  return (
    <div class="empty">
      <h2>{title}</h2>
      {children ? <p>{children}</p> : null}
    </div>
  );
}

export function Loading({ what }: { what?: string }) {
  return (
    <p class="loading" role="status">
      {what ? `Loading ${what}` : "Loading"}
    </p>
  );
}

export function PageHead({ title, sub, children }: { title: string; sub?: string; children?: ComponentChildren }) {
  return (
    <header class="page-head">
      <div>
        <h1>{title}</h1>
        {sub ? <p class="sub">{sub}</p> : null}
      </div>
      {children ? <div class="page-actions">{children}</div> : null}
    </header>
  );
}

export function ErrorLine({ error }: { error: Error | null }) {
  return error ? <Notice tone="bad">{error.message}</Notice> : null;
}

/* ---- racing marks -------------------------------------------------------------------------------------------------------
 * One component per meaning in docs/08-rewrite/design/README.md. Colour is the race token, never a decoration. */

export type DotKind = "green" | "caution" | "pit" | "zero";

/** Lap flag state: green flag, caution, pit. `zero` is the hollow dot for a count of none. */
export function Dot({ kind, label }: { kind: DotKind; label?: string }) {
  return <span class={`dot-${kind}`} role={label ? "img" : undefined} aria-label={label} aria-hidden={label ? undefined : true} title={label} />;
}

/** White flag (last lap) or checkered flag (finish, win). */
export function FlagGlyph({ kind, size = 18, label }: { kind: "white" | "checkered"; size?: number; label?: string }) {
  const title = label ?? (kind === "white" ? "Last lap" : "Finish");
  return (
    <svg class="flag-glyph" width={size} height={size} viewBox="0 0 18 18" role="img" aria-label={title}>
      <title>{title}</title>
      <path d="M3 2v14" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" fill="none" />
      {kind === "white" ? (
        <path d="M4 3h11l-2.2 3.2L15 9.4H4z" fill="#f4f6f8" />
      ) : (
        <>
          <rect x="4" y="3" width="11" height="7" fill="#0b1218" stroke="#f4f6f8" stroke-width="0.8" />
          <path d="M4 3h2.75v3.5H4zM9.5 3h2.75v3.5H9.5zM6.75 6.5h2.75V10H6.75zM12.25 6.5H15V10h-2.75z" fill="#f4f6f8" />
        </>
      )}
    </svg>
  );
}

/** Car identity stripe; `color` is a CSS value from `carColor`. */
export function Stripe({ color }: { color: string }) {
  return <span class="stripe" style={{ "--car": color }} aria-hidden="true" />;
}

/** Setup type of a session: Fixed (solid, lock) or Open (dashed, wrench). Absent when the type is unknown. */
export function SetupBadge({ type }: { type: "Fixed" | "Open" | string | null | undefined }) {
  if (type !== "Fixed" && type !== "Open") return null;
  return (
    <span class={`badge ${type === "Fixed" ? "badge-fixed" : "badge-open"}`} title={type === "Fixed" ? "Fixed setup" : "Open setup"}>
      <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
        {type === "Fixed" ? (
          <path d="M3 5V4a3 3 0 0 1 6 0v1h.5v6h-7V5zm1.5 0h3V4a1.5 1.5 0 0 0-3 0z" fill="currentColor" />
        ) : (
          <path d="M8.6 1a2.8 2.8 0 0 0-2.3 3.9L1.5 9.7 2.3 10.5l4.8-4.8A2.8 2.8 0 0 0 11 3.4L9.4 5 8 4 7 2.6z" fill="currentColor" />
        )}
      </svg>
      {type}
    </span>
  );
}

/** Positions gained or lost: ▲8 in race green, ▼2 in loss red. `delta` is start minus finish, so positive is a gain. Null when equal or unknown. */
export function PositionChange({ delta }: { delta: number | null | undefined }) {
  if (delta === null || delta === undefined || !Number.isFinite(delta) || delta === 0) return null;
  const gain = delta > 0;
  return (
    <span class={`change ${gain ? "change-gain" : "change-loss"}`} title={gain ? `${delta} positions gained` : `${-delta} positions lost`}>
      <span aria-hidden="true">{gain ? "▲" : "▼"}</span>
      {Math.abs(delta)}
    </span>
  );
}

/** Incident points of a lap: x1 outline, x2 and x3 filled, x4 and more solid; shown whenever an event exists, x0 included. */
export function IncidentChip({ points, title }: { points: number; title?: string }) {
  const level = points >= 4 ? 4 : points >= 2 ? 2 : points === 1 ? 1 : 0;
  return (
    <span class={`inc-chip inc-${level}`} title={title}>
      x{points}
    </span>
  );
}

/** Exception on a lap: `Excl` (excluded from comparisons, reasons in the title) or `Partial` (dashed). */
export function StatusChip({ kind, title }: { kind: "excl" | "partial"; title?: string }) {
  return (
    <span class={`state-chip state-chip-${kind}`} title={title}>
      {kind === "excl" ? "Excl" : "Partial"}
    </span>
  );
}

/** One figure of an event tile: a label over a value; a missing value is a dash. */
export function Stat({ label, tone, children }: { label: string; tone?: "best" | "gain" | "loss" | "fuel" | "pit"; children: ComponentChildren }) {
  const empty = children === null || children === undefined || children === false || children === "";
  return (
    <div class="bc-stat">
      <span>{label}</span>
      <b class={`${empty ? "none" : ""}${tone && !empty ? ` fig-${tone}` : ""}`}>{empty ? "-" : children}</b>
    </div>
  );
}

/**
 * The track silhouette from the analysis geometry: `points` are the main path in any coordinate system, scaled to fit with the
 * aspect kept; a checkered tick marks the line. No points, no silhouette.
 */
export function TrackSilhouette({ points, width = 118, height = 78 }: { points: ReadonlyArray<readonly [number, number]> | null | undefined; width?: number; height?: number }) {
  if (!points || points.length < 3) return null;
  let x0 = Infinity;
  let x1 = -Infinity;
  let y0 = Infinity;
  let y1 = -Infinity;
  for (const [x, y] of points) {
    if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
    x0 = Math.min(x0, x);
    x1 = Math.max(x1, x);
    y0 = Math.min(y0, y);
    y1 = Math.max(y1, y);
  }
  const pad = 6;
  const scale = Math.min((width - 2 * pad) / Math.max(x1 - x0, 1e-9), (height - 2 * pad) / Math.max(y1 - y0, 1e-9));
  const ox = (width - (x1 - x0) * scale) / 2;
  const oy = (height - (y1 - y0) * scale) / 2;
  const at = ([x, y]: readonly [number, number]) => [ox + (x - x0) * scale, height - (oy + (y - y0) * scale)] as const;
  const mapped = points.map(at);
  const d = mapped.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join("");
  const [sx, sy] = mapped[0]!;
  const [nx, ny] = mapped[Math.min(2, mapped.length - 1)]!;
  const angle = Math.atan2(ny - sy, nx - sx) + Math.PI / 2;
  return (
    <svg class="bc-silhouette" width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-hidden="true">
      <path d={`${d}Z`} fill="none" stroke="currentColor" stroke-width="3" stroke-linejoin="round" stroke-linecap="round" />
      <rect x={sx - 1.5} y={sy - 5} width="3" height="10" fill="#f4f6f8" transform={`rotate(${((angle * 180) / Math.PI).toFixed(1)} ${sx} ${sy})`} />
    </svg>
  );
}

/**
 * The identity block of an event card (Broadcast direction): car-colour edge and wash, the track in display type, a line of
 * meta (configuration, date), the silhouette, the car with its stripe, the series, and the Fixed or Open badge.
 */
export function EventHeader({
  title,
  meta,
  carName,
  carColorValue,
  series,
  setupType,
  silhouette,
  children,
}: {
  title: string;
  meta?: ComponentChildren;
  carName?: string | null;
  carColorValue: string;
  series?: string | null;
  setupType?: string | null;
  silhouette?: ComponentChildren;
  children?: ComponentChildren;
}) {
  return (
    <div class="bc-id" style={{ "--car": carColorValue }}>
      <div class="bc-id-top">
        <div>
          <h2>{title}</h2>
          {meta ? <div class="bc-id-meta">{meta}</div> : null}
        </div>
        {silhouette}
      </div>
      {children}
      <div class="bc-id-foot">
        {carName ? (
          <div class="bc-car">
            <Stripe color={carColorValue} />
            {carName}
          </div>
        ) : null}
        {series || setupType ? (
          <div class="bc-series">
            {setupType ? <SetupBadge type={setupType} /> : null}
            {series ? <span>{series}</span> : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}

/** A session tile of an event card: phase band on top (letter and time), then the hero and the figures. */
export function PhaseTile({
  phase,
  label,
  time,
  result,
  children,
}: {
  phase: "p" | "q" | "r";
  label: string;
  time?: string | null;
  /** Result band on the left edge of the race tile. */
  result?: "win" | "gain" | "loss" | null;
  children: ComponentChildren;
}) {
  return (
    <div class={`bc-tile bc-tile-${phase}`}>
      <div class="bc-band">
        {label}
        {time ? <span>{time}</span> : null}
      </div>
      {result ? <i class={`bc-result bc-result-${result}`} aria-hidden="true" /> : null}
      {children}
    </div>
  );
}

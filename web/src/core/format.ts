/** Display formatting shared by the pages. Missing values are "-", never zero. */
export const DASH = "-";

export function formatLapTime(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds) || seconds <= 0) return DASH;
  if (seconds >= 60) {
    const m = Math.floor(seconds / 60);
    return `${m}:${(seconds - m * 60).toFixed(3).padStart(6, "0")}`;
  }
  return seconds.toFixed(3);
}

export function formatSigned(value: number | null | undefined, digits = 3, unit = ""): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return DASH;
  const s = value.toFixed(digits);
  const signed = value > 0 ? `+${s}` : s;
  return unit ? `${signed} ${unit}` : signed;
}

export function formatNumber(value: number | null | undefined, digits = 1, unit = ""): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return DASH;
  return unit ? `${value.toFixed(digits)} ${unit}` : value.toFixed(digits);
}

export function formatPercent(fraction: number | null | undefined, digits = 0): string {
  if (fraction === null || fraction === undefined || !Number.isFinite(fraction)) return DASH;
  return `${(fraction * 100).toFixed(digits)}%`;
}

export function formatPosition(p: number | null | undefined): string {
  return p === null || p === undefined ? DASH : `P${p}`;
}

/** Local date, e.g. "Sep 20, 2026" (audit S1: recordings carry UTC times, the driver reads the clock on the wall). `timeZone` is for tests. */
export function formatDate(iso: string | null | undefined, timeZone?: string): string {
  if (!iso) return DASH;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? DASH : new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", timeZone }).format(d);
}

/** Local date and time, e.g. "Sep 20, 2026 7:30 pm". */
export function formatDateTime(iso: string | null | undefined, timeZone?: string): string {
  if (!iso) return DASH;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return DASH;
  const time = new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit", hour12: true, timeZone })
    .format(d)
    .replace(/\s?([AP])M$/i, (_m, c: string) => ` ${c.toLowerCase()}m`);
  return `${formatDate(iso, timeZone)} ${time}`;
}

export function formatBytes(n: number | null | undefined): string {
  if (n === null || n === undefined) return DASH;
  const units = ["B", "KB", "MB", "GB"];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

/** `snake_case` or `kebab-case` code to a sentence-case label. */
export function humanize(code: string | null | undefined): string {
  if (!code) return DASH;
  const s = code.replace(/[_-]+/g, " ").trim();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

export function sessionTypeLabel(t: string | null | undefined): string {
  if (!t) return "Session";
  const s = t.toLowerCase();
  if (s.includes("race")) return "Race";
  if (s.includes("qual")) return "Qualifying";
  if (s.includes("practice")) return "Practice";
  if (s.includes("test") || s.includes("offline")) return "Test";
  return humanize(t);
}

export type EventPhase = { session_id: number; session_type?: string | null };

const PHASE_ORDER = ["Practice", "Qualifying", "Race"];

/** The event's phases in running order. `event_sessions` holds only the other phases, so the open session joins them. */
export function eventPhases(current: EventPhase, others: readonly EventPhase[]): EventPhase[] {
  const rank = (p: EventPhase) => {
    const i = PHASE_ORDER.indexOf(sessionTypeLabel(p.session_type));
    return i < 0 ? PHASE_ORDER.length : i;
  };
  return [...others.filter((o) => o.session_id !== current.session_id), current]
    .map((p, i) => ({ p, i }))
    .sort((a, b) => rank(a.p) - rank(b.p) || a.i - b.i)
    .map(({ p }) => p);
}

export function trackLabel(track: string | null | undefined, config: string | null | undefined): string {
  if (!track) return "Track unknown";
  return config && config.trim() ? `${track} - ${config}` : track;
}

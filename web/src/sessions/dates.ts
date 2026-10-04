/**
 * Local date and time for the library (audit S1): recordings carry UTC start times, the driver reads the clock on their wall.
 * `timeZone` is for tests; the page uses the device's zone.
 */
function valid(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** `Sat Oct 3`, with the year once it is not the current one (`Sun Sep 27 2025`). */
export function localDay(iso: string | null | undefined, now: Date = new Date(), timeZone?: string): string | null {
  const d = valid(iso);
  if (!d) return null;
  const parts = new Intl.DateTimeFormat("en-US", { weekday: "short", month: "short", day: "numeric", year: "numeric", timeZone }).formatToParts(d);
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? "";
  const year = new Intl.DateTimeFormat("en-US", { year: "numeric", timeZone }).format(now);
  const base = `${get("weekday")} ${get("month")} ${get("day")}`;
  return get("year") === year ? base : `${base} ${get("year")}`;
}

/** `7:45 pm`. */
export function localTime(iso: string | null | undefined, timeZone?: string): string | null {
  const d = valid(iso);
  if (!d) return null;
  return new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit", hour12: true, timeZone }).format(d).replace(/\s?([AP])M$/i, (_m, c: string) => ` ${c.toLowerCase()}m`);
}

/** `Mon Sep 28, 1:00 pm`: one phrase for a tooltip or a link name. */
export function localDayTime(iso: string | null | undefined, now: Date = new Date(), timeZone?: string): string | null {
  const day = localDay(iso, now, timeZone);
  const time = localTime(iso, timeZone);
  return day && time ? `${day}, ${time}` : (day ?? time);
}

import { useEffect, useRef } from "preact/hooks";
import type { Schemas } from "./api";
import { createStore } from "./store";

export type ServerEvent = Schemas["ServerEvent"];
export type ServerEventType = ServerEvent["type"];
export type ServerEventOf<T extends ServerEventType> = Extract<ServerEvent, { type: T }>;
export type ConnectionState = "idle" | "connecting" | "open" | "retrying";

export const EVENT_TYPES: readonly ServerEventType[] = ["presence", "recording_state", "job_state", "analysis_ready", "library_changed"];

type Handler = (event: ServerEvent) => void;

/** Backoff after a failed connection: 1, 2, 4 ... 30 s with 20 % jitter (collector-protocol section 3). */
export function backoffMs(attempt: number, random: () => number = Math.random): number {
  const base = Math.min(30_000, 1000 * 2 ** Math.max(0, attempt - 1));
  return Math.round(base * (0.8 + random() * 0.4));
}

/**
 * One `/api/v1/events` stream per tab. Handlers get parsed `ServerEvent`s; `connection` shows whether the
 * stream is up. Opening again after a drop makes consumers refetch (`onReopen`), because events missed while
 * disconnected are not replayed.
 */
export class EventsClient {
  readonly connection = createStore<ConnectionState>("idle");
  private source: EventSource | null = null;
  private handlers = new Map<string, Set<Handler>>();
  private reopen = new Set<() => void>();
  private attempt = 0;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private wasOpen = false;

  constructor(private readonly url = "/api/v1/events") {}

  start(): void {
    if (this.source || this.timer || typeof EventSource === "undefined") return;
    this.open();
  }

  stop(): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    this.source?.close();
    this.source = null;
    this.connection.set("idle");
  }

  on<T extends ServerEventType>(type: T, handler: (e: ServerEventOf<T>) => void): () => void;
  on(type: "*", handler: Handler): () => void;
  on(type: string, handler: Handler): () => void {
    let set = this.handlers.get(type);
    if (!set) this.handlers.set(type, (set = new Set()));
    set.add(handler as Handler);
    return () => set!.delete(handler as Handler);
  }

  onReopen(fn: () => void): () => void {
    this.reopen.add(fn);
    return () => this.reopen.delete(fn);
  }

  /** Test hook and message entry point. */
  dispatch(raw: string): void {
    let event: ServerEvent;
    try {
      event = JSON.parse(raw) as ServerEvent;
    } catch {
      return;
    }
    if (!event || typeof event !== "object" || typeof event.type !== "string") return;
    for (const h of [...(this.handlers.get(event.type) ?? [])]) h(event);
    for (const h of [...(this.handlers.get("*") ?? [])]) h(event);
  }

  private open(): void {
    this.connection.set(this.attempt === 0 ? "connecting" : "retrying");
    const source = new EventSource(this.url);
    this.source = source;
    source.onopen = () => {
      this.attempt = 0;
      this.connection.set("open");
      if (this.wasOpen) for (const fn of [...this.reopen]) fn();
      this.wasOpen = true;
    };
    for (const type of EVENT_TYPES) source.addEventListener(type, (m) => this.dispatch((m as MessageEvent<string>).data));
    source.onerror = () => {
      // EventSource retries by itself while CONNECTING; a CLOSED source needs a new one.
      if (source.readyState !== EventSource.CLOSED) {
        this.connection.set("retrying");
        return;
      }
      source.close();
      this.source = null;
      this.attempt += 1;
      this.connection.set("retrying");
      this.timer = setTimeout(() => {
        this.timer = null;
        this.open();
      }, backoffMs(this.attempt));
    };
  }
}

export const events = new EventsClient();

export function useServerEvent<T extends ServerEventType>(type: T, handler: (e: ServerEventOf<T>) => void): void {
  const ref = useRef(handler);
  ref.current = handler;
  useEffect(() => events.on(type, (e) => ref.current(e)), [type]);
}

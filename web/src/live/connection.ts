import { useEffect, useRef, useState } from "preact/hooks";
import type { Schemas } from "../core/api";
import { backoffMs } from "../core/sse";
import { createStore } from "../core/store";
import { TABLE_VERSION } from "../gen/live-channels";
import { FrameError, FrameFlag, type LiveFrame, decodeFrame } from "./frame";
import { LiveHistory } from "./history";

export type LiveRate = 1 | 10 | 30 | 60;
export const LIVE_RATES: readonly LiveRate[] = [1, 10, 30, 60];
export type LiveHello = Schemas["LiveHello"];

export type LinkState = "connecting" | "open" | "retrying" | "closed";

export interface LiveStatus {
  link: LinkState;
  hello: LiveHello | null;
  /** Live frames received on this socket (updated every 120 frames). */
  frames: number;
  rejected: number;
  lastError: string | null;
}

/** Rate by device: phones 10 Hz, tablets 30 Hz, desktops and rig screens 60 Hz (D6: chosen per device). */
export function defaultRate(width: number): LiveRate {
  return width < 700 ? 10 : width < 1100 ? 30 : 60;
}

export interface SocketLike {
  binaryType: string;
  readyState: number;
  onopen: ((e: unknown) => void) | null;
  onmessage: ((e: { data: unknown }) => void) | null;
  onclose: ((e: { code: number }) => void) | null;
  onerror: ((e: unknown) => void) | null;
  send(data: string): void;
  close(): void;
}

export interface LiveDeps {
  open(url: string): SocketLike;
  schedule(fn: () => void, ms: number): () => void;
}

const browserDeps = (): LiveDeps => ({
  open: (url) => new WebSocket(url) as unknown as SocketLike,
  schedule: (fn, ms) => {
    const t = setTimeout(fn, ms);
    return () => clearTimeout(t);
  },
});

export function liveUrl(customerId: number, rate: LiveRate, loc: Pick<Location, "protocol" | "host"> = location): string {
  return `${loc.protocol === "https:" ? "wss" : "ws"}://${loc.host}/ws/live/${customerId}?rate=${rate}`;
}

/**
 * One browser socket to `/ws/live/{driverId}`: LiveHello, backlog frames (bit6, fill the trend history only), then live
 * frames. Reconnects with backoff. Opening the socket is what makes this tab a viewer, so close() releases the stream.
 * `onFrame` fires per live frame without touching component state: the page samples `latest` at its own rate.
 */
export class LiveConnection {
  readonly status = createStore<LiveStatus>({ link: "connecting", hello: null, frames: 0, rejected: 0, lastError: null });
  readonly history = new LiveHistory();
  latest: LiveFrame | null = null;
  /** Increments on every live frame; lets a renderer skip work when nothing arrived. */
  version = 0;
  private socket: SocketLike | null = null;
  private attempt = 0;
  private cancel: (() => void) | null = null;
  private closed = false;
  private rejected = 0;
  private frames = 0;

  constructor(
    readonly customerId: number,
    private rate: LiveRate,
    private readonly deps: LiveDeps = browserDeps(),
    private readonly urlFor: (customerId: number, rate: LiveRate) => string = liveUrl,
  ) {}

  connect(): void {
    this.closed = false;
    this.open();
  }

  close(): void {
    this.closed = true;
    this.cancel?.();
    this.cancel = null;
    const s = this.socket;
    this.socket = null;
    if (s) {
      s.onclose = s.onmessage = s.onerror = s.onopen = null;
      s.close();
    }
    this.status.update((st) => ({ ...st, link: "closed" }));
  }

  setRate(rate: LiveRate): void {
    if (rate === this.rate) return;
    this.rate = rate;
    if (this.socket && this.socket.readyState === 1) this.socket.send(JSON.stringify({ type: "rate", rate }));
  }

  get currentRate(): LiveRate {
    return this.rate;
  }

  /** Message entry point (also used by tests). Text is LiveHello; binary is a frame. */
  handle(data: unknown): void {
    if (typeof data === "string") {
      try {
        const msg = JSON.parse(data) as LiveHello;
        if (msg.type === "live.hello") {
          if (msg.table_version > TABLE_VERSION) this.status.update((s) => ({ ...s, lastError: "The server's live table is newer than this page; reload." }));
          this.status.update((s) => ({ ...s, hello: msg }));
        }
      } catch {
        /* not JSON: ignore */
      }
      return;
    }
    if (!(data instanceof ArrayBuffer) && !ArrayBuffer.isView(data)) return;
    let frame: LiveFrame;
    try {
      frame = decodeFrame(data as ArrayBuffer | Uint8Array);
    } catch (e) {
      this.rejected += 1;
      this.status.update((s) => ({ ...s, rejected: this.rejected, lastError: e instanceof FrameError ? e.message : "Bad frame" }));
      return;
    }
    this.history.push(frame);
    if ((frame.flags & FrameFlag.Backlog) === 0) {
      this.latest = frame;
      this.version += 1;
      this.frames += 1;
      if (this.frames === 1 || this.frames % 120 === 0) this.status.update((s) => ({ ...s, frames: this.frames }));
    }
  }

  private open(): void {
    if (this.closed) return;
    this.status.update((s) => ({ ...s, link: this.attempt === 0 ? "connecting" : "retrying" }));
    const socket = this.deps.open(this.urlFor(this.customerId, this.rate));
    socket.binaryType = "arraybuffer";
    this.socket = socket;
    socket.onopen = () => {
      this.attempt = 0;
      this.status.update((s) => ({ ...s, link: "open", lastError: null }));
    };
    socket.onmessage = (e) => this.handle(e.data);
    socket.onclose = () => {
      this.socket = null;
      if (this.closed) return;
      this.attempt += 1;
      this.status.update((s) => ({ ...s, link: "retrying", hello: null }));
      this.cancel = this.deps.schedule(() => this.open(), backoffMs(this.attempt));
    };
    socket.onerror = () => {
      /* onclose follows */
    };
  }
}

/** Connection for a mounted live page; the socket is closed (viewer released) on unmount. */
export function useLiveConnection(customerId: number, rate: LiveRate): LiveConnection {
  const ref = useRef<LiveConnection | null>(null);
  if (!ref.current || ref.current.customerId !== customerId) ref.current = new LiveConnection(customerId, rate);
  const conn = ref.current;
  useEffect(() => {
    conn.connect();
    return () => conn.close();
  }, [conn]);
  useEffect(() => conn.setRate(rate), [conn, rate]);
  return conn;
}

/** Re-render at most `hz` times a second while frames arrive; returns the newest frame. */
export function useFrameSample(conn: LiveConnection, hz = 15): { frame: LiveFrame | null; tick: number } {
  const [, setN] = useState(0);
  useEffect(() => {
    let last = -1;
    const id = setInterval(() => {
      if (conn.version !== last) {
        last = conn.version;
        setN((n) => n + 1);
      }
    }, Math.round(1000 / hz));
    return () => clearInterval(id);
  }, [conn, hz]);
  return { frame: conn.latest, tick: conn.version };
}

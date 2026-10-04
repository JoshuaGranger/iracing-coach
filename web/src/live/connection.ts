import { useEffect, useRef, useState } from "preact/hooks";
import type { Schemas } from "../core/api";
import { backoffMs } from "../core/sse";
import { createStore } from "../core/store";
import { TABLE_VERSION } from "../gen/live-channels";
import { FrameError, FrameFlag, type LiveFrame, decodeFrame } from "./frame";
import { LiveHistory } from "./history";
import { RateGovernor } from "./rate";

export type LiveRate = 1 | 10 | 30 | 60;
/** Every device asks for the full stream rate. A device that cannot keep up is decimated by `live/rate.ts` from its measured frame time, never by a setting. */
export const LIVE_RATE: LiveRate = 60;
/** A live frame older than this (at least three frame intervals) is stale: the tiles read Waiting. */
export const STALE_MIN_MS = 2000;

export function staleAfterMs(rate: LiveRate): number {
  return Math.max(STALE_MIN_MS, (3 * 1000) / rate);
}
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
  /** Monotonic milliseconds; `performance.now()` in the browser. */
  now?(): number;
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
  /** The newest live frame of this socket; cleared when the socket closes or the hello says the sim is not live. Use `current()` to read it. */
  latest: LiveFrame | null = null;
  /** Increments on every live frame; lets a renderer skip work when nothing arrived. */
  version = 0;
  private latestAt = -Infinity;
  private urlRate: LiveRate;
  private socket: SocketLike | null = null;
  private attempt = 0;
  private cancel: (() => void) | null = null;
  private closed = false;
  private rejected = 0;
  private frames = 0;

  constructor(
    readonly customerId: number,
    private rate: LiveRate = LIVE_RATE,
    private readonly deps: LiveDeps = browserDeps(),
    private readonly urlFor: (customerId: number, rate: LiveRate) => string = liveUrl,
  ) {
    this.urlRate = rate;
  }

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
    this.latest = null;
    this.status.update((st) => ({ ...st, link: "closed" }));
  }

  /** Changes the stream rate: the rate governor's decimation. Sent at once on an open socket, and on open for a socket still connecting. */
  setRate(rate: LiveRate): void {
    if (rate === this.rate) return;
    this.rate = rate;
    if (this.socket && this.socket.readyState === 1) this.socket.send(JSON.stringify({ type: "rate", rate }));
  }

  get currentRate(): LiveRate {
    return this.rate;
  }

  private clock(): number {
    return this.deps.now ? this.deps.now() : performance.now();
  }

  /** The frame the tiles show: the newest live frame while it is fresh, else null (every tile reads Waiting). */
  current(): LiveFrame | null {
    if (!this.latest) return null;
    return this.clock() - this.latestAt > staleAfterMs(this.rate) ? null : this.latest;
  }

  /** True once a live frame has arrived on this socket, fresh or not. */
  get hadFrames(): boolean {
    return this.frames > 0;
  }

  /** Message entry point (also used by tests). Text is LiveHello; binary is a frame. */
  handle(data: unknown): void {
    if (typeof data === "string") {
      try {
        const msg = JSON.parse(data) as LiveHello;
        if (msg.type === "live.hello") {
          if (msg.table_version > TABLE_VERSION) this.status.update((s) => ({ ...s, lastError: "The server's live table is newer than this page; reload." }));
          // A hello that says the sim is not live ends what the last frame showed.
          if (!msg.live) this.latest = null;
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
      this.latestAt = this.clock();
      this.version += 1;
      this.frames += 1;
      if (this.frames === 1 || this.frames % 120 === 0) this.status.update((s) => ({ ...s, frames: this.frames }));
    }
  }

  private open(): void {
    if (this.closed) return;
    this.status.update((s) => ({ ...s, link: this.attempt === 0 ? "connecting" : "retrying" }));
    this.urlRate = this.rate;
    const socket = this.deps.open(this.urlFor(this.customerId, this.rate));
    socket.binaryType = "arraybuffer";
    this.socket = socket;
    socket.onopen = () => {
      this.attempt = 0;
      this.status.update((s) => ({ ...s, link: "open", lastError: null }));
      // The URL carries the rate the socket was created with; a change made while it connected goes out now.
      if (this.rate !== this.urlRate) socket.send(JSON.stringify({ type: "rate", rate: this.rate }));
    };
    socket.onmessage = (e) => this.handle(e.data);
    socket.onclose = () => {
      this.socket = null;
      this.latest = null;
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

/** Connection for a mounted live page; the socket is closed (viewer released) on unmount. It always asks for the full rate. */
export function useLiveConnection(customerId: number): LiveConnection {
  const ref = useRef<LiveConnection | null>(null);
  if (!ref.current || ref.current.customerId !== customerId) ref.current = new LiveConnection(customerId, LIVE_RATE);
  const conn = ref.current;
  useEffect(() => {
    conn.connect();
    return () => conn.close();
  }, [conn]);
  return conn;
}

export interface LiveSample {
  /** The frame to show: the newest while it is fresh, else null (every tile reads Waiting). */
  frame: LiveFrame | null;
  /** Counts live frames; changes whenever there is something new to draw. */
  tick: number;
}

/**
 * The page's render loop: one pass per display frame, drawing only when a frame arrived or the freshness of the last one changed
 * (stale after max(2 s, three frame intervals), on a closed socket and on a hello that says the sim is not live). The measured time
 * between passes feeds the rate governor, which lowers the stream rate on a device that cannot keep up.
 */
export function useLiveSample(conn: LiveConnection, governor: RateGovernor = new RateGovernor()): LiveSample {
  const [, setN] = useState(0);
  const gov = useRef(governor);
  useEffect(() => {
    let raf = 0;
    let lastVersion = -1;
    let lastFrame: LiveFrame | null | undefined;
    let lastPass = performance.now();
    const pass = (now: number) => {
      const next = gov.current.observe(now - lastPass, now);
      lastPass = now;
      if (next !== null) conn.setRate(next);
      const frame = conn.current();
      if (conn.version !== lastVersion || frame !== lastFrame) {
        lastVersion = conn.version;
        lastFrame = frame;
        setN((n) => n + 1);
      }
      raf = requestAnimationFrame(pass);
    };
    raf = requestAnimationFrame(pass);
    return () => cancelAnimationFrame(raf);
  }, [conn]);
  return { frame: conn.current(), tick: conn.version };
}

export interface LiveHealth {
  /** A live frame with the sim connected arrived within the stale limit. */
  fresh: boolean;
  /** At least one live frame has arrived on this socket. */
  hadFrames: boolean;
  /** The sim reports ABS activation (a value has arrived for it). */
  absRecorded: boolean;
}

/** What the headline and the layout editor need to know about the stream, checked four times a second. */
export function useLiveHealth(conn: LiveConnection): LiveHealth {
  const read = (): LiveHealth => {
    const frame = conn.current();
    return { fresh: frame !== null && (frame.flags & FrameFlag.SimConnected) !== 0, hadFrames: conn.hadFrames, absRecorded: conn.history.hasChannel("abs_active") };
  };
  const [health, setHealth] = useState<LiveHealth>(read);
  useEffect(() => {
    const check = () => setHealth((prev) => {
      const next = read();
      return prev.fresh === next.fresh && prev.hadFrames === next.hadFrames && prev.absRecorded === next.absRecorded ? prev : next;
    });
    check();
    const id = setInterval(check, 250);
    return () => clearInterval(id);
  }, [conn]);
  return health;
}

/** The headline of the Live page: the link while it is not open, then Live only while fresh frames arrive. The hello speaks only before the first frame. */
export function liveHeadline(link: LinkState, hello: LiveHello | null, health: Pick<LiveHealth, "fresh" | "hadFrames">): { text: string; live: boolean } {
  if (link === "connecting") return { text: "Connecting", live: false };
  if (link === "retrying") return { text: "Reconnecting", live: false };
  if (link === "closed") return { text: "Closed", live: false };
  if (health.fresh) return { text: "Live", live: true };
  if (health.hadFrames || hello?.live === false) return { text: "Waiting for iRacing", live: false };
  return { text: hello ? "Waiting for data" : "Connecting", live: false };
}

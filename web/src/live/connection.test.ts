import { describe, expect, it } from "vitest";
import { CHANNELS, CHANNEL_INDEX } from "../gen/live-channels";
import { LiveConnection, type SocketLike, defaultRate, liveUrl } from "./connection";
import { encodeFrame } from "./encode";
import { FrameFlag } from "./frame";

class FakeSocket implements SocketLike {
  binaryType = "blob";
  readyState = 0;
  onopen: SocketLike["onopen"] = null;
  onmessage: SocketLike["onmessage"] = null;
  onclose: SocketLike["onclose"] = null;
  onerror: SocketLike["onerror"] = null;
  sent: string[] = [];
  closed = false;
  constructor(readonly url: string) {}
  send(d: string) {
    this.sent.push(d);
  }
  close() {
    this.closed = true;
  }
  open() {
    this.readyState = 1;
    this.onopen?.({});
  }
  drop() {
    this.readyState = 3;
    this.onclose?.({ code: 1006 });
  }
}

function setup() {
  const sockets: FakeSocket[] = [];
  const timers: { fn: () => void; ms: number }[] = [];
  const conn = new LiveConnection(
    7,
    30,
    {
      open: (url) => {
        const s = new FakeSocket(url);
        sockets.push(s);
        return s;
      },
      schedule: (fn, ms) => {
        timers.push({ fn, ms });
        return () => undefined;
      },
    },
    (c, r) => liveUrl(c, r, { protocol: "http:", host: "h:1" }),
  );
  return { conn, sockets, timers };
}

const frame = (time: number, flags: number, speed = 50) => {
  const v: (number | null)[] = Array(CHANNELS.length).fill(null);
  v[CHANNEL_INDEX.speed!] = speed;
  return encodeFrame({ flags, sessionTime: time, values: v }).buffer as ArrayBuffer;
};

describe("live connection", () => {
  it("builds the socket url and picks a rate per device", () => {
    expect(liveUrl(7, 30, { protocol: "http:", host: "h:1" })).toBe("ws://h:1/ws/live/7?rate=30");
    expect(liveUrl(7, 60, { protocol: "https:", host: "h" })).toBe("wss://h/ws/live/7?rate=60");
    expect([defaultRate(390), defaultRate(1024), defaultRate(1280)]).toEqual([10, 30, 60]);
  });

  it("reads hello, keeps backlog frames out of the live display, and shows live frames", () => {
    const { conn, sockets } = setup();
    conn.connect();
    const s = sockets[0]!;
    expect(s.url).toBe("ws://h:1/ws/live/7?rate=30");
    expect(s.binaryType).toBe("arraybuffer");
    s.open();
    s.onmessage!({ data: JSON.stringify({ type: "live.hello", table_version: 1, live: true, backlog_frames: 2, rate: 30 }) });
    s.onmessage!({ data: frame(1, FrameFlag.SimConnected | FrameFlag.Backlog) });
    s.onmessage!({ data: frame(2, FrameFlag.SimConnected | FrameFlag.Backlog) });
    expect(conn.latest).toBeNull();
    expect(conn.history.length).toBe(2);
    s.onmessage!({ data: frame(3, FrameFlag.SimConnected, 61) });
    expect(conn.latest?.sessionTime).toBe(3);
    expect(conn.version).toBe(1);
    expect(conn.status.get()).toMatchObject({ link: "open", hello: { live: true } });
  });

  it("counts and survives bad frames", () => {
    const { conn, sockets } = setup();
    conn.connect();
    sockets[0]!.onmessage!({ data: new Uint8Array([1, 2, 3]).buffer });
    sockets[0]!.onmessage!({ data: frame(1, 1) });
    expect(conn.status.get().rejected).toBe(1);
    expect(conn.latest).not.toBeNull();
  });

  it("reconnects with backoff after a drop, and not after close()", () => {
    const { conn, sockets, timers } = setup();
    conn.connect();
    sockets[0]!.open();
    sockets[0]!.drop();
    expect(conn.status.get().link).toBe("retrying");
    expect(timers).toHaveLength(1);
    timers[0]!.fn();
    expect(sockets).toHaveLength(2);
    sockets[1]!.open();
    sockets[1]!.drop();
    expect(timers).toHaveLength(2);
    conn.close();
    timers[1]!.fn();
    expect(sockets).toHaveLength(2);
    expect(conn.status.get().link).toBe("closed");
  });

  it("sends a rate change over the open socket and releases the viewer on close", () => {
    const { conn, sockets } = setup();
    conn.connect();
    const s = sockets[0]!;
    s.open();
    conn.setRate(10);
    expect(JSON.parse(s.sent[0]!)).toEqual({ type: "rate", rate: 10 });
    conn.setRate(10);
    expect(s.sent).toHaveLength(1);
    conn.close();
    expect(s.closed).toBe(true);
  });
});

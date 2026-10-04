import { describe, expect, it } from "vitest";
import { CHANNELS, CHANNEL_INDEX } from "../gen/live-channels";
import { LIVE_RATE, LiveConnection, type SocketLike, liveHeadline, liveUrl, staleAfterMs } from "./connection";
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
  const clock = { ms: 0 };
  const conn = new LiveConnection(
    7,
    30,
    {
      now: () => clock.ms,
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
  return { conn, sockets, timers, clock };
}

const frame = (time: number, flags: number, speed = 50) => {
  const v: (number | null)[] = Array(CHANNELS.length).fill(null);
  v[CHANNEL_INDEX.speed!] = speed;
  return encodeFrame({ flags, sessionTime: time, values: v }).buffer as ArrayBuffer;
};

describe("live connection", () => {
  it("builds the socket url; every device asks for the full 60 Hz", () => {
    expect(liveUrl(7, 30, { protocol: "http:", host: "h:1" })).toBe("ws://h:1/ws/live/7?rate=30");
    expect(liveUrl(7, 60, { protocol: "https:", host: "h" })).toBe("wss://h/ws/live/7?rate=60");
    expect(LIVE_RATE).toBe(60);
    expect(new LiveConnection(7).currentRate).toBe(60);
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

  it("sends a rate chosen while the socket was still connecting as soon as it opens", () => {
    const { conn, sockets } = setup();
    conn.connect();
    conn.setRate(10);
    const s = sockets[0]!;
    expect(s.sent).toHaveLength(0);
    s.open();
    expect(s.sent.map((m) => JSON.parse(m))).toEqual([{ type: "rate", rate: 10 }]);
  });

  describe("freshness", () => {
    const live = FrameFlag.SimConnected;

    it("treats the last frame as stale after max(2 s, three frame intervals), and clears it", () => {
      const { conn, sockets, clock } = setup();
      conn.connect();
      const s = sockets[0]!;
      s.open();
      s.onmessage!({ data: JSON.stringify({ type: "live.hello", table_version: 1, live: true, backlog_frames: 0, rate: 30 }) });
      clock.ms = 1000;
      s.onmessage!({ data: frame(5, live, 61) });
      expect(conn.current()?.sessionTime).toBe(5);
      clock.ms = 1000 + staleAfterMs(30) - 1;
      expect(conn.current()).not.toBeNull();
      clock.ms = 1000 + staleAfterMs(30) + 1;
      expect(conn.current()).toBeNull();
      expect(staleAfterMs(60)).toBe(2000);
      expect(staleAfterMs(1)).toBe(3000);
    });

    it("a closed socket and a hello that says the sim is not live both end what the last frame showed", () => {
      const { conn, sockets, clock } = setup();
      conn.connect();
      const s = sockets[0]!;
      s.open();
      s.onmessage!({ data: frame(5, live) });
      expect(conn.current()).not.toBeNull();
      s.onmessage!({ data: JSON.stringify({ type: "live.hello", table_version: 1, live: false, backlog_frames: 0, rate: 30 }) });
      expect(conn.current()).toBeNull();
      s.onmessage!({ data: frame(6, live) });
      clock.ms += 10;
      expect(conn.current()).not.toBeNull();
      s.drop();
      expect(conn.current()).toBeNull();
    });
  });

  describe("headline", () => {
    const hello = (liveNow: boolean) => ({ type: "live.hello" as const, table_version: 1, live: liveNow, backlog_frames: 0, rate: 60 });
    it("says Live only while fresh frames arrive; the hello speaks before the first frame only", () => {
      const none = { fresh: false, hadFrames: false };
      expect(liveHeadline("connecting", null, none).text).toBe("Connecting");
      expect(liveHeadline("retrying", null, none).text).toBe("Reconnecting");
      expect(liveHeadline("closed", null, none).text).toBe("Closed");
      expect(liveHeadline("open", hello(true), none).text).toBe("Waiting for data");
      expect(liveHeadline("open", hello(false), none).text).toBe("Waiting for iRacing");
      expect(liveHeadline("open", hello(false), { fresh: true, hadFrames: true })).toEqual({ text: "Live", live: true });
      // A session that starts after the hello still reads Live, and one that stops reads Waiting although the socket is open.
      expect(liveHeadline("open", hello(true), { fresh: true, hadFrames: true }).text).toBe("Live");
      expect(liveHeadline("open", hello(true), { fresh: false, hadFrames: true }).text).toBe("Waiting for iRacing");
    });
  });
});

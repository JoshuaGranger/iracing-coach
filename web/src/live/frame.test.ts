import { describe, expect, it } from "vitest";
import { CHANNELS, CHANNEL_INDEX, CUES, Evidence, FlagState, Priority } from "../gen/live-channels";
import { encodeFrame } from "./encode";
import { FrameError, FrameFlag, decodeFrame } from "./frame";

const full = (): (number | null)[] => {
  const v: (number | null)[] = Array(CHANNELS.length).fill(null);
  v[CHANNEL_INDEX.speed!] = 62.5;
  v[CHANNEL_INDEX.throttle!] = 0.75;
  v[CHANNEL_INDEX.gear!] = -1;
  v[CHANNEL_INDEX.latitude!] = 36.5151234567;
  v[CHANNEL_INDEX.lap!] = 12;
  v[CHANNEL_INDEX.flag_state!] = FlagState.Green;
  v[CHANNEL_INDEX.session_flags!] = 0x80000004;
  v[CHANNEL_INDEX.abs_active!] = 1;
  return v;
};

describe("live frame decoder", () => {
  it("round-trips header, channel values at their own widths, and absent channels", () => {
    const bytes = encodeFrame({ flags: FrameFlag.SimConnected | FrameFlag.OnTrack, seq: 77, sessionEpoch: 3, capturedUnixMs: 1_790_000_000_123, sessionTime: 123.456, tick: 7407, tickRate: 60, values: full() });
    const f = decodeFrame(bytes);
    expect(f).toMatchObject({ flags: 3, seq: 77, sessionEpoch: 3, capturedUnixMs: 1_790_000_000_123, sessionTime: 123.456, tick: 7407, tickRate: 60, tableVersion: 1, channelCount: 45 });
    expect(f.values[CHANNEL_INDEX.speed!]).toBeCloseTo(62.5, 4);
    expect(f.values[CHANNEL_INDEX.throttle!]).toBeCloseTo(0.75, 6);
    expect(f.values[CHANNEL_INDEX.gear!]).toBe(-1);
    expect(f.values[CHANNEL_INDEX.latitude!]).toBe(36.5151234567);
    expect(f.values[CHANNEL_INDEX.lap!]).toBe(12);
    expect(f.values[CHANNEL_INDEX.session_flags!]).toBe(0x80000004);
    expect(f.values[CHANNEL_INDEX.abs_active!]).toBe(1);
    expect(f.values[CHANNEL_INDEX.brake!]).toBeNull();
    expect(f.cue).toBeNull();
  });

  it("decodes the cue block", () => {
    const f = decodeFrame(encodeFrame({ values: full(), cue: { id: 11, priority: Priority.Coaching, evidence: Evidence.Derived, args: [25, 0.43, 4] } }));
    expect(f.cue?.id).toBe(11);
    expect(f.cue?.args[0]).toBe(25);
    expect(f.cue?.args[1]).toBeCloseTo(0.43, 5);
    expect(CUES.some((c) => c.id === f.cue!.id)).toBe(true);
  });

  it("treats a non-finite float as not measured", () => {
    const v = full();
    v[CHANNEL_INDEX.speed!] = Number.NaN;
    expect(decodeFrame(encodeFrame({ values: v })).values[CHANNEL_INDEX.speed!]).toBeNull();
  });

  it("rejects bad magic, version, and short input", () => {
    const ok = encodeFrame({ values: full() });
    const code = (b: Uint8Array) => {
      try {
        decodeFrame(b);
        return "ok";
      } catch (e) {
        return e instanceof FrameError ? e.code : "other";
      }
    };
    expect(code(ok.slice(0, 20))).toBe("too_short");
    expect(code(Uint8Array.from(ok, (b, i) => (i === 0 ? 0 : b)))).toBe("bad_magic");
    expect(code(Uint8Array.from(ok, (b, i) => (i === 2 ? 2 : b)))).toBe("bad_version");
    expect(code(ok.slice(0, ok.length - 3))).toBe("truncated");
  });

  it("rejects cue blocks the contract forbids", () => {
    const base = encodeFrame({ values: full(), cue: { id: 13, priority: 0, evidence: 1, args: [] } });
    const cueAt = base.length - 4;
    const patch = (i: number, v: number) => Uint8Array.from(base, (b, j) => (j === cueAt + i ? v : b));
    const code = (b: Uint8Array) => {
      try {
        decodeFrame(b);
        return "ok";
      } catch (e) {
        return (e as FrameError).code;
      }
    };
    expect(code(patch(3, 5))).toBe("bad_cue"); // k > 4
    expect(code(patch(3, 2))).toBe("bad_cue"); // arguments cut off
    expect(code(patch(0, 99))).toBe("bad_cue"); // unknown id
    expect(code(patch(1, 7))).toBe("bad_cue"); // unknown priority
    expect(code(patch(2, 9))).toBe("bad_cue"); // unknown evidence
  });

  it("rejects more channels than the table has unless the sender's table is newer; then reads the known channels and no cue", () => {
    const bytes = encodeFrame({ values: full(), extraChannels: 3, tableVersion: 1 });
    expect(() => decodeFrame(bytes)).toThrowError(FrameError);
    const newer = decodeFrame(encodeFrame({ values: full(), extraChannels: 3 }));
    expect(newer.tableVersion).toBe(2);
    expect(newer.channelCount).toBe(48);
    expect(newer.values[CHANNEL_INDEX.speed!]).toBeCloseTo(62.5, 4);
    expect(newer.cue).toBeNull();
  });

  it("accepts a sender with a shorter table: the missing tail is absent", () => {
    const presence = 4; // 30 channels
    const bytes = new Uint8Array(38 + presence + 4 + 4); // header, presence, one f32, empty cue
    bytes.set([0x4c, 0x46, 1, 1]);
    const view = new DataView(bytes.buffer);
    view.setUint16(34, 1, true);
    view.setUint16(36, 30, true);
    bytes[38 + (CHANNEL_INDEX.speed! >> 3)]! |= 1 << (CHANNEL_INDEX.speed! & 7);
    view.setFloat32(38 + presence, 10, true);
    const f = decodeFrame(bytes);
    expect(f.channelCount).toBe(30);
    expect(f.values[CHANNEL_INDEX.speed!]).toBe(10);
    expect(f.values[CHANNEL_INDEX.incidents!]).toBeNull();
  });
});

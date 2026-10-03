import { describe, expect, it } from "vitest";
import { CcolError, channelAsFloat, channelValue, decodeCcol, encodeCcol } from "../ccol";

function sample(): ArrayBuffer {
  const bins = 5;
  const laps = [3, 4];
  const f = Float64Array.from([1.5, 2.5, Number.NaN, 4.25, 5, 6, 7, 8, 9, Number.NaN]);
  const i = Int16Array.from([0, 1, -32768, 3, 4, 5, 6, 7, 8, 9]);
  const u = Uint8Array.from([1, 2, 3, 255, 5, 6, 7, 8, 9, 10]);
  return encodeCcol("a-1", 1, bins, laps, [
    { id: "speed", dtype: "f64", values: f },
    { id: "rpm", dtype: "i16", values: i, missing: -32768 },
    { id: "gear", dtype: "u8", values: u, missing: 255 },
  ]);
}

describe("CCOL decoder", () => {
  it("round-trips f64, i16 and u8 channels with their missing sentinels", () => {
    const table = decodeCcol(sample());
    expect(table.analysisId).toBe("a-1");
    expect(table.projectionVersion).toBe(1);
    expect(table.bins).toBe(5);
    expect(table.laps).toEqual([3, 4]);
    const speed = table.channels.get("speed")!;
    expect(channelValue(speed, 0, 0, 5)).toBe(1.5);
    expect(Number.isNaN(channelValue(speed, 0, 2, 5))).toBe(true);
    expect(channelValue(speed, 1, 4, 5)).toBeNaN();
    const rpm = table.channels.get("rpm")!;
    expect(channelValue(rpm, 0, 1, 5)).toBe(1);
    expect(channelValue(rpm, 0, 2, 5)).toBeNaN();
    const gear = table.channels.get("gear")!;
    expect(channelValue(gear, 0, 3, 5)).toBeNaN();
    expect(channelValue(gear, 1, 0, 5)).toBe(6);
    const asFloat = channelAsFloat(rpm);
    expect(asFloat[1]).toBe(1);
    expect(asFloat[2]).toBeNaN();
  });

  it("aligns every channel to 8 bytes and keeps views zero-copy", () => {
    const buffer = sample();
    const table = decodeCcol(buffer);
    for (const channel of table.channels.values()) {
      expect(channel.raw.byteOffset % 8).toBe(0);
      expect(channel.raw.buffer).toBe(buffer);
    }
  });

  it("rejects a bad magic, a truncated container and an unsupported version", () => {
    const good = sample();
    const bad = good.slice(0);
    new Uint8Array(bad)[0] = 0x58;
    expect(() => decodeCcol(bad)).toThrow(CcolError);
    expect(() => decodeCcol(good.slice(0, 8))).toThrow(/truncated/);
    const wrongVersion = good.slice(0);
    new DataView(wrongVersion).setUint16(4, 2, true);
    expect(() => decodeCcol(wrongVersion)).toThrow(/version/);
  });

  it("rejects data that exceeds the container and byte lengths that do not match the grid", () => {
    const good = sample();
    expect(() => decodeCcol(good.slice(0, good.byteLength - 16))).toThrow(/exceeds/);
    const copy = good.slice(0);
    const view = new DataView(copy);
    const headerLength = view.getUint32(8, true);
    const text = new TextDecoder().decode(new Uint8Array(copy, 12, headerLength));
    const patched = new TextEncoder().encode(text.replace('"byte_length":80', '"byte_length":72'));
    expect(patched.length).toBe(headerLength);
    new Uint8Array(copy, 12, headerLength).set(patched);
    expect(() => decodeCcol(copy)).toThrow(/byte_length/);
  });

  it("rejects a header that is not JSON", () => {
    const buffer = sample().slice(0);
    new Uint8Array(buffer, 12, 4).set([0xff, 0xfe, 0xfd, 0xfc]);
    expect(() => decodeCcol(buffer)).toThrow(/header/);
  });
});
